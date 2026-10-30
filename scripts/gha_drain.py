#!/usr/bin/env python3
"""Time-budgeted ATS-directory drain runner for GitHub Actions.

Replaces the local double-forked daemon (scripts/ats_prober_daemon.py):
local daemons die silently with the sandbox and take their progress with
them — nothing local survives a recycle, so the drain now runs on GitHub
hosted runners where every batch checkpoints into the COMMITTED DB
(ingest/data/ats_directory.db) and the workflow pushes state after the
budget expires. Progress is therefore durable by construction.

Loops `build_ats_directory.py --batch N` until the time budget expires,
until nothing is pending, or after too many consecutive failures. The
runner itself checkpoints the WAL at the end so the committed .db file
is self-contained (never commit a hot -wal).

[S50-T1] THE GATE: `--counts` is the workflow's read-only no-op gate
(ats-drain.yml runs `gha_drain.py --counts` BEFORE setup-python).
It computes needs-work and emits `DRAIN_PENDING=<n>` (always — 0
included) so the workflow's `if: env.DRAIN_PENDING &&
env.DRAIN_PENDING != '0'` conditions skip deterministically on a
fresh universe. Missing OR unreadable DB → DRAIN_PENDING=0 + exit 1
(RED — the promised SystemExit behavior [RA1]); it NEVER bumps legs,
NEVER probes, and NEVER emits DRAIN_RESULT. The refresh cadence knobs
(DRAIN_REFRESH_DAYS / DRAIN_DEAD_REFRESH_DAYS, defaults 30/95) live
HERE as the single source [the S38/W6c P3-4 doctrine done right] and
ride both the gate's count and the driver's builder pass-through.

Env knobs (all optional):
  DRAIN_BUDGET_SECONDS   wall-clock probing budget   (default 4800 = 80 min)
  DRAIN_BATCH            slugs per batch             (default 60)
  DRAIN_SLEEP            per-request courtesy sleep  (default 0.3)
  DRAIN_REFRESH_DAYS     live-board re-probe staleness (default 30)
  DRAIN_DEAD_REFRESH_DAYS dead-board resurrection check  (default 95)
  DRAIN_LEGS_MAX_PER_DAY daily leg/re-trigger cap    (default 8; date-keyed
                         counter in the committed DB — cheap insurance
                         from the 2026-09-09 GHA-burn lesson: a
                         pathological re-seed must not self-retrigger
                         unbounded 100-min legs all day. The drain is
                         normally a 0.2-min no-op gate; the cap only
                         matters when it self-retriggers)

Machine-readable result (for the workflow):
  - `--counts`: DRAIN_PENDING=<needs-work> (always; RED rc 1 when the
    DB is missing/unreadable, and DRAIN_PENDING=0 in that case)
  - the driver: appends DRAIN_RESULT=pending|complete|failed to
    $GITHUB_ENV when set
  - 'pending' ONLY while (work remains AND legs remain today) — a capped
    day reports complete-not-pending so the workflow does not re-trigger;
    tomorrow's cron resets the date-keyed counter
  - always exits 0 in the non-gate modes (the workflow branches on
    DRAIN_RESULT, not on rc)

Exit code 0 in all non-gate modes; "failed" still checkpoints whatever
probed. [RA1] --counts is exempt: it runs OUTSIDE the rc-0 catch-all
and exits 1 on DB-unavailable.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
INGEST = REPO / "ingest"
BUILD = INGEST / "scripts" / "build_ats_directory.py"
DB = INGEST / "data" / "ats_directory.db"


def _int_env(name: str, default: int) -> int:
    """Env knob parsing that never raises (audit F5): malformed overrides
    fall back to the default instead of crashing before DRAIN_RESULT is
    emitted."""
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        print(f"ignoring malformed {name}={os.environ.get(name)!r} — "
              f"using default {default}", flush=True)
        return default


BUDGET = _int_env("DRAIN_BUDGET_SECONDS", 4800)
BATCH = os.environ.get("DRAIN_BATCH", "60")
SLEEP = os.environ.get("DRAIN_SLEEP", "0.3")
# [S50-T1 / RA11] the hung-batch bound scales with the ACTUAL sleep
# (the fixed 15.3 voided the bound at DRAIN_SLEEP > 0.3)
_REQUEST_S = 15.0 + float(SLEEP)
# [S50-T1] the refresh cadence knobs — THE single source (the workflow
# comment's S37-W3 promise, finally real). Gate + driver + builder
# pass-through all read these.
REFRESH_DAYS = _int_env("DRAIN_REFRESH_DAYS", 30)
DEAD_REFRESH_DAYS = _int_env("DRAIN_DEAD_REFRESH_DAYS", 95)
MAX_CONSECUTIVE_FAILURES = 5
LEGS_MAX_PER_DAY = _int_env("DRAIN_LEGS_MAX_PER_DAY", 8)


def _subproc_timeout(size: int) -> int:
    return int(size * _REQUEST_S) + 30


def _legs_bump() -> int:
    """Date-keyed leg counter in the committed DB — the daily re-trigger
    cap (2026-09-09 GHA-burn lesson; mirrors the watch's *.legs.json and
    the backfill's twin helper, but in the DB so the race-proof
    checkpoint — which rescues the .db — carries it). Resets with the
    date; tomorrow's cron resumes."""
    today = date.today().isoformat()
    db = sqlite3.connect(DB)
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS runner_legs ("
            "runner TEXT NOT NULL, date TEXT NOT NULL, "
            "legs INTEGER NOT NULL DEFAULT 0, "
            "PRIMARY KEY (runner, date))")
        row = db.execute(
            "SELECT legs FROM runner_legs WHERE runner=? AND date=?",
            ("drain", today)).fetchone()
        legs = (row[0] if row else 0) + 1
        db.execute(
            "INSERT INTO runner_legs (runner, date, legs) VALUES (?, ?, ?) "
            "ON CONFLICT(runner, date) DO UPDATE SET legs=excluded.legs",
            ("drain", today, legs))
        db.commit()
        return legs
    finally:
        db.close()


def _leg_guard() -> tuple[bool, int]:
    """Bump the daily counter once per invocation that intends to work.
    Returns (capped, leg): a capped day does NO work and the caller
    reports complete (NOT pending) so the workflow does not re-trigger —
    the backfill's 31×40-min burn class can never happen here."""
    leg = _legs_bump()
    if leg > LEGS_MAX_PER_DAY:
        print(f"leg {leg} > DRAIN_LEGS_MAX_PER_DAY={LEGS_MAX_PER_DAY} — "
              f"no work today; tomorrow's cron resumes", flush=True)
        return True, leg
    print(f"leg {leg}/{LEGS_MAX_PER_DAY} today", flush=True)
    return False, leg


def _cap_aware(result: str, leg: int) -> str:
    """'pending' only while legs remain today (the workflow's re-trigger
    keys on exactly that). 'failed' and 'complete' are never touched."""
    if result == "pending" and leg >= LEGS_MAX_PER_DAY:
        print(f"leg {leg}/{LEGS_MAX_PER_DAY} — daily cap reached, not "
              f"re-triggering; tomorrow's cron resumes", flush=True)
        return "complete"
    return result


class GateUnavailable(RuntimeError):
    """[RA1] the DB is missing OR unreadable — the gate's RED sentinel.
    Raised (not swallowed) so --counts can exit 1 OUTSIDE the rc-0
    catch-all; the driver catches it and reports 'failed' honestly."""


def _iso_cutoff(days: int) -> str:
    """[RA10] Python-computed ISO-'T' cutoff, matching _now()'s format —
    the SQL datetime('now','-N day') SPACE-separated form never compares
    correctly against last_probed_at's 'T' format (the latent -1h lie)."""
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def needs_work_parts() -> dict[str, int]:
    """[S50-T1 / RA6] the FIVE-term needs-work predicate — the single
    source for the --counts gate AND the driver's every predicate site
    AND (as a golden cross-check pin) the builder's claim classes:
      pending        true pending rows (attempts < cap, as claimed)
      err_reclaim    error rows reclaimable THIS cycle (aged 1h, attempts < 2)
      err_rollover   error rows aged past DEAD_REFRESH_DAYS — cycle
                     rollover, claimed with attempts reset (the iCIMS
                     parking-page round-1 pool must not accrete terminal)
      live_stale     live boards whose last probe is older than
                     REFRESH_DAYS (the universe refresh)
      dead_stale     dead boards older than DEAD_REFRESH_DAYS (the
                     quarterly resurrection check)
    Raises GateUnavailable when the DB is missing or unreadable."""
    if not DB.exists():
        raise GateUnavailable(f"no DB at {DB}")
    try:
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
        try:
            row = db.execute("SELECT COUNT(*) FROM directory").fetchone()
            if not row:
                raise sqlite3.DatabaseError("directory table empty/missing")
            parts = {
                "pending": db.execute(
                    "SELECT COUNT(*) FROM directory"
                    " WHERE status='pending' AND attempts < 2"
                ).fetchone()[0],
                # reclaimable THIS cycle: attempts left + aged ≥1h (the
                # NULL case = never actually probed → claimable)
                "err_reclaim": db.execute(
                    "SELECT COUNT(*) FROM directory"
                    " WHERE status='error' AND attempts < 2"
                    " AND (last_probed_at IS NULL"
                    "      OR last_probed_at < ?)",
                    (_iso_cutoff_hours(1),)
                ).fetchone()[0],
                # cycle-rollover: attempts-CAPPED error rows aged past the
                # dead-refresh window — disjoint from err_reclaim (the
                # ≥2 attempts class ONLY the rollover can rescue)
                "err_rollover": db.execute(
                    "SELECT COUNT(*) FROM directory"
                    " WHERE status='error' AND attempts >= 2"
                    " AND (last_probed_at IS NULL"
                    "      OR last_probed_at < ?)",
                    (_iso_cutoff(DEAD_REFRESH_DAYS),)
                ).fetchone()[0],
                "live_stale": db.execute(
                    "SELECT COUNT(*) FROM directory"
                    " WHERE status='live'"
                    " AND (last_probed_at IS NULL"
                    "      OR last_probed_at < ?)",
                    (_iso_cutoff(REFRESH_DAYS),)
                ).fetchone()[0],
                "dead_stale": db.execute(
                    "SELECT COUNT(*) FROM directory"
                    " WHERE status='dead'"
                    " AND (last_probed_at IS NULL"
                    "      OR last_probed_at < ?)",
                    (_iso_cutoff(DEAD_REFRESH_DAYS),)
                ).fetchone()[0],
            }
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise GateUnavailable(f"unreadable DB at {DB}: {exc}") from exc
    return parts


def _iso_cutoff_hours(hours: float) -> str:
    return (datetime.now() - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%S")


def needs_work() -> int:
    """[RA2] the single driver predicate — every completion/exit check
    reads THIS, never a bare pending_count."""
    p = needs_work_parts()
    return sum(p.values())


def needs_work_line() -> str:
    p = needs_work_parts()
    return (f"pending={p['pending']} err_reclaim={p['err_reclaim']} "
            f"err_rollover={p['err_rollover']} "
            f"live_stale={p['live_stale']} dead_stale={p['dead_stale']}")


def emit_env(name: str, value: str) -> None:
    genv = os.environ.get("GITHUB_ENV")
    if genv:
        with open(genv, "a") as f:
            f.write(f"{name}={value}\n")


def run_counts_gate() -> int:
    """[S50-T1 / RA1] the --counts gate. RED (rc 1) on missing/unreadable
    DB; never bumps legs; never probes; never emits DRAIN_RESULT."""
    try:
        n = needs_work()
        print(f"needs-work {n} ({needs_work_line()})", flush=True)
        print(f"DRAIN_PENDING={n}", flush=True)
        emit_env("DRAIN_PENDING", str(n))
        return 0
    except GateUnavailable as exc:
        print(f"::error::{exc} — gate RED, DRAIN_PENDING=0", flush=True)
        emit_env("DRAIN_PENDING", "0")
        return 1


def pending_count() -> int:
    """kept for the batch-time inside-loop cheap check (the drain's
    own progress display); the COMPLETION predicate is needs_work()."""
    try:
        return needs_work_parts()["pending"]
    except GateUnavailable:
        return 0


def stats_line() -> str:
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = dict(db.execute(
            "SELECT status, COUNT(*) FROM directory GROUP BY status").fetchall())
        live_jobs = db.execute(
            "SELECT COALESCE(SUM(job_count),0) FROM directory"
            " WHERE status='live'").fetchone()[0]
        return (f"live={rows.get('live', 0)} dead={rows.get('dead', 0)} "
                f"error={rows.get('error', 0)} pending={rows.get('pending', 0)} "
                f"jobs_on_live_boards={live_jobs}")
    finally:
        db.close()


def checkpoint_wal() -> None:
    if not DB.exists():
        return
    db = sqlite3.connect(DB)
    try:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        db.close()


def emit_result(result: str) -> None:
    print(f"DRAIN_RESULT={result}", flush=True)
    genv = os.environ.get("GITHUB_ENV")
    if genv:
        with open(genv, "a") as f:
            f.write(f"DRAIN_RESULT={result}\n")


def run_batch(size: str) -> tuple[bool, str]:
    """Run one drain batch. Returns (ok, last_output_line).
    [S50-T1] the refresh knobs ride every builder invocation — the
    gate's count and the builder's claim classes share the same env
    knobs (REFRESH_DAYS/DEAD_REFRESH_DAYS defaults live in THIS file)."""
    env = dict(os.environ)
    # Authorized-child marker: we ARE the daemon-equivalent HERE (GHA), and
    # this makes us immune to any stale heartbeat file committed by accident.
    # Set it ONLY under CI (audit F2): a local gha_drain.py started while a
    # real ats_prober_daemon is live must still be refused by the heartbeat
    # check instead of double-probing alongside it.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        env["ATS_PROBER_DAEMON"] = "1"
    timeout = _subproc_timeout(int(size))
    try:
        r = subprocess.run(
            [sys.executable, str(BUILD),
             "--batch", size, "--sleep", SLEEP, "--retry-errors",
             "--refresh-days", str(REFRESH_DAYS),
             "--dead-refresh-days", str(DEAD_REFRESH_DAYS)],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(INGEST), env=env)
    except subprocess.TimeoutExpired:
        return False, f"batch of {size} timed out after {timeout}s"
    out = (r.stdout or "").strip().splitlines()
    tail = out[-1] if out else (r.stderr or "").strip().splitlines()[-1:] or ""
    if r.returncode != 0:
        return False, f"rc={r.returncode}: {tail}"
    return True, str(tail)


def _reclaimable_errors() -> int:
    """Error rows a --retry-errors sweep could still reclaim THIS cycle
    (attempts left + aged 1h) — kept for the display only; the driver's
    completion predicate is needs_work()."""
    try:
        return needs_work_parts()["err_reclaim"]
    except GateUnavailable:
        return 0


def _counts_cli(argv: list[str]):
    """[S50-T1] argparse shared by both modes."""
    ap = argparse.ArgumentParser(
        description="Time-budgeted ATS-directory drain runner")
    ap.add_argument("--counts", action="store_true",
                    help="read-only gate: emit DRAIN_PENDING=<needs-work> "
                         "(RED rc 1 when the DB is missing/unreadable); "
                         "never probes, never bumps legs")
    ap.add_argument("--refresh-days", type=int, default=REFRESH_DAYS,
                    help=f"live-board re-probe staleness (default {REFRESH_DAYS})")
    ap.add_argument("--dead-refresh-days", type=int,
                    default=DEAD_REFRESH_DAYS,
                    help=f"dead-board resurrection window "
                         f"(default {DEAD_REFRESH_DAYS})")
    return ap, ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # argv=None (a test/lib call) parses NO flags — the driver mode;
    # the __main__ block passes sys.argv[1:] explicitly
    ap, args = _counts_cli(argv or [])
    if args.counts:
        # [RA1] the gate runs OUTSIDE the rc-0 catch-all: DB-unavailable
        # must be RED (rc 1), and never touches legs or DRAIN_RESULT.
        return run_counts_gate()
    if not DB.exists():
        print(f"no DB at {DB} — seed first (build_ats_directory.py --seed)",
              flush=True)
        emit_result("failed")
        checkpoint_wal()
        return 0
    try:
        work = needs_work()
    except GateUnavailable as exc:
        print(f"::error::{exc} — reporting failed (state not drainable)",
              flush=True)
        emit_result("failed")
        checkpoint_wal()
        return 0
    if work == 0:
        # [RA2] the entry predicate is needs_work() — a day where pending==0
        # but live boards are stale must NOT exit here (the S37 no-op class:
        # this exact branch green-wink'd 9,705 aging boards for 7 weeks).
        print(f"nothing needs work — drain already complete. "
              f"{needs_work_line()}", flush=True)
        emit_result("complete")
        return 0

    capped, leg = _leg_guard()
    if capped:
        checkpoint_wal()
        emit_result("complete")
        return 0

    print(f"drain start: {stats_line()}", flush=True)
    print(f"needs-work {work} ({needs_work_line()})", flush=True)
    print(f"budget={BUDGET}s batch={BATCH} sleep={SLEEP} "
          f"refresh={REFRESH_DAYS}d dead-refresh={DEAD_REFRESH_DAYS}d",
          flush=True)
    start = time.monotonic()
    failures = 0
    batches = 0
    result = "pending"

    while time.monotonic() - start < BUDGET:
        if needs_work() == 0:
            result = "complete"
            break
        ok, tail = run_batch(BATCH)
        # Slow-batch shrink: mirror the daemon's fallback so one pathological
        # batch (a handful of very slow boards) can't stall the run.
        if not ok and "timed out" in tail:
            ok, tail = run_batch("25")
        batches += 1
        elapsed = int(time.monotonic() - start)
        print(f"[{elapsed:>5}s] batch {batches}: {'ok' if ok else 'FAIL'} — {tail}",
              flush=True)
        if not ok:
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"{MAX_CONSECUTIVE_FAILURES} consecutive failures — "
                      f"stopping early, checkpointing what we have", flush=True)
                result = "failed"
                break
            time.sleep(10)
        else:
            failures = 0

    # Budget exhausted → 'pending' (workflow re-triggers); 'complete' and
    # 'failed' both fall through to the snapshot+commit steps. The leg cap
    # demotes 'pending' at/past the daily cap (never re-trigger past it).
    # [RA2] the loop-exit verdict re-reads needs_work() — a batch that
    # drained true-pending but left stale-live rows stays 'pending'.
    try:
        if result == "pending" and needs_work() > 0:
            result = "pending"
        elif result == "pending":
            result = "complete"
    except GateUnavailable:
        pass                       # keep the loop's verdict
    result = _cap_aware(result, leg)
    checkpoint_wal()
    print(f"drain end ({result}): {stats_line()} after {batches} batches",
          flush=True)
    print(f"needs-work at exit: {needs_work_line()}", flush=True)
    emit_result(result)
    return 0


if __name__ == "__main__":
    # Audit F5: the docstring promises exit 0 with a DRAIN_RESULT marker, but
    # malformed env knobs or a corrupt DB could raise BEFORE emission — the
    # re-trigger step would then never fire and the chain stalls ≤24 h. Keep
    # the promise: any surprise becomes 'failed' + rc 0. [S50-T1/RA1] EXCEPT
    # --counts: the gate's RED contract (rc 1 on DB-unavailable) must survive
    # the catch-all, so SystemExit passes through untouched (the gate's own
    # run_counts_gate returns the rc; argparse errors exit 2 as GHA expects).
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"unexpected {type(exc).__name__} — emitting DRAIN_RESULT=failed",
              flush=True)
        emit_result("failed")
        checkpoint_wal()
        sys.exit(0)
