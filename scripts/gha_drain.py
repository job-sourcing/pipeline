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

Env knobs (all optional):
  DRAIN_BUDGET_SECONDS   wall-clock probing budget   (default 4800 = 80 min)
  DRAIN_BATCH            slugs per batch             (default 60)
  DRAIN_SLEEP            per-request courtesy sleep  (default 0.3)
  DRAIN_LEGS_MAX_PER_DAY daily leg/re-trigger cap    (default 8; date-keyed
                         counter in the committed DB — cheap insurance
                         from the 2026-09-09 GHA-burn lesson: a
                         pathological re-seed must not self-retrigger
                         unbounded 100-min legs all day. The drain is
                         normally a 0.2-min no-op gate; the cap only
                         matters when it self-retriggers)

Machine-readable result (for the workflow):
  - appends DRAIN_RESULT=pending|complete|failed to $GITHUB_ENV when set
  - 'pending' ONLY while (work remains AND legs remain today) — a capped
    day reports complete-not-pending so the workflow does not re-trigger;
    tomorrow's cron resets the date-keyed counter
  - always exits 0 (the workflow branches on DRAIN_RESULT, not on rc)

Exit code 0 in all cases; "failed" still checkpoints whatever probed.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from datetime import date
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
MAX_CONSECUTIVE_FAILURES = 5
LEGS_MAX_PER_DAY = _int_env("DRAIN_LEGS_MAX_PER_DAY", 8)
# [S37-W3 / D-S37-1 R4+A5] the universe-refresh knobs: live boards go
# stale after DRAIN_REFRESH_DAYS (default 30 — the weekly cadence's
# steady state ≈ 9,705×7/30 ≈ 2,265 re-probes/week ≈ 45-115 min on the
# free public runner); dead boards re-probe after DRAIN_DEAD_REFRESH_DAYS
# (default 95 ≈ quarterly — the resurrection check). 0 disables the
# refresh lane entirely (the pre-S37 behavior).
REFRESH_DAYS = _int_env("DRAIN_REFRESH_DAYS", 30)
DEAD_REFRESH_DAYS = _int_env("DRAIN_DEAD_REFRESH_DAYS", 95)
# Per-request worst case in build_ats_directory: 15 s HTTP timeout + sleep.
# Scale the subprocess timeout to the batch size (audit F6: a fixed 300 s
# still times out the shrunk 25-batch when every probe hangs: 25×15.3≈383 s).
_REQUEST_S = 15.3


def _subproc_timeout(size: int) -> int:
    # [S37/W5a P2-2] the sleep was HARDCODED at 0.3s while DRAIN_SLEEP
    # is env-configurable — a DRAIN_SLEEP > ~0.8 silently voided the
    # F6 hung-batch bound (the timeout no longer covered the worst
    # case). Scale with the ACTUAL sleep; the +30 covers startup.
    return int(size * (_REQUEST_S + float(SLEEP))) + 30


# [S38/W6c P3-3] the builder's rc=0 "did nothing" tails. The drain used
# to read these as ok=True / failures=0 — a ~1 s/iteration subprocess
# spin for the whole 80-min budget ending 'pending' (up to 8 legs/day of
# churn). The refusal is local-only (GHA sets ATS_PROBER_DAEMON=1, so
# _daemon_is_draining() never fires there); "nothing pending" fires
# when the drain's count and the builder's selection disagree (e.g. a
# pending row already over the attempts cap). Treat both as a FAILURE
# so the existing 5-consecutive-failure cutoff bounds the spin in
# seconds — and says why.
_NO_PROGRESS_TAILS = (
    "[batch] refusing to start",   # fresh prober-daemon heartbeat
    "[batch] nothing pending",     # count/select divergence
)


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


def pending_count() -> int:
    if not DB.exists():
        return 0
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return db.execute(
            "SELECT COUNT(*) FROM directory WHERE status='pending'"
        ).fetchone()[0]
    finally:
        db.close()


def refresh_eligible_count() -> int:
    """[S37-W3] live boards stale beyond REFRESH_DAYS + dead boards
    stale beyond DEAD_REFRESH_DAYS (the resurrection check) + [S37/W5a
    P2-3] error rows with attempts remaining (the refresh cycle's own
    retry: a board erroring in the campaign's final batch used to
    strand for the WEEK — the <1h-age gate on the reclaimable class
    left it uncounted and the chain exited complete). The attempts cap
    bounds the retries naturally. The gate's refresh half — a stale
    universe is WORK, not a no-op (the pre-S37 gate exited at
    pending==0 which was PERMANENT since Sep 8). Disabled → 0."""
    if not DB.exists() or REFRESH_DAYS <= 0:
        return 0
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return db.execute(
            "SELECT COUNT(*) FROM directory WHERE "
            "(status='live' AND (last_probed_at IS NULL OR "
            " last_probed_at < datetime('now', ?))) OR "
            "(status='dead' AND (last_probed_at IS NULL OR "
            " last_probed_at < datetime('now', ?))) OR "
            "(status='error' AND attempts < 2)",
            (f"-{REFRESH_DAYS} days", f"-{DEAD_REFRESH_DAYS} days"),
        ).fetchone()[0]
    finally:
        db.close()


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


def _print_counts() -> int:
    """[S38/W6c P3-4] the workflow gate's single source of truth.

    The ats-drain.yml gate used to inline its own copy of the
    needs-work SQL (the THIRD hand-copy: 30/95/attempts/-1h hardcoded
    against the driver's env knobs — semantically identical today, a
    4-place 2-file edit on any cadence change). The count now comes
    from THIS module — the same env knobs and the same SQL the driver
    itself gates on, so the gate cannot drift from the drain.

    Union semantics: pending + refresh-eligible (+ reclaimable errors
    only when the refresh lane is off — with it on,
    refresh_eligible_count() already counts every attempts<2 error row
    regardless of age, so adding _reclaimable_errors() would
    double-count). Exit 1 (RED, no DRAIN_PENDING written) when the DB
    is missing or unreadable — the old inline gate's SystemExit
    behavior, kept: a bad DB must never limp into the drain/export/
    checkpoint steps.
    """
    if not DB.exists():
        print("::error::ats_directory.db is missing from the repo — "
              "re-seed (ingest/scripts/build_ats_directory.py --seed) "
              "and commit it before running the drain")
        return 1
    try:
        pending = pending_count()
        refresh = refresh_eligible_count()
        reclaim = _reclaimable_errors() if REFRESH_DAYS <= 0 else 0
    except Exception as exc:  # noqa: BLE001 — the gate must stay RED, not crash
        print(f"::error::gate could not read {DB}: {exc}")
        return 1
    n = pending + refresh + reclaim
    print(f"pending={pending} refresh_eligible={refresh} "
          f"reclaimable_errors={reclaim} — rows needing work: {n}")
    genv = os.environ.get("GITHUB_ENV")
    if genv:
        with open(genv, "a") as f:
            f.write(f"DRAIN_PENDING={n}\n")
    return 0


def run_batch(size: str, refresh: bool = False) -> tuple[bool, str]:
    """Run one drain batch. Returns (ok, last_output_line).

    ok=False on a nonzero rc, a timeout, OR [S38/W6c P3-3] an rc=0
    no-progress tail (the builder refused / found nothing pending) —
    the failures counter treats all three alike, so a can't-progress
    builder is bounded by the consecutive-failure cutoff instead of
    spinning the budget away."""
    env = dict(os.environ)
    # Authorized-child marker: we ARE the daemon-equivalent HERE (GHA), and
    # this makes us immune to any stale heartbeat file committed by accident.
    # Set it ONLY under CI (audit F2): a local gha_drain.py started while a
    # real ats_prober_daemon is live must still be refused by the heartbeat
    # check instead of double-probing alongside it.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        env["ATS_PROBER_DAEMON"] = "1"
    timeout = _subproc_timeout(int(size))
    cmd = [sys.executable, str(BUILD),
           "--batch", size, "--sleep", SLEEP, "--retry-errors"]
    if refresh:
        # [S37-W3] the universe refresh: pending keeps priority in the
        # builder; when pending is drained, the stalest live/dead boards
        # are re-probed (attempts reset per cycle — the cap can't march
        # across refresh cycles).
        cmd += ["--refresh-days", str(REFRESH_DAYS),
                "--dead-refresh-days", str(DEAD_REFRESH_DAYS)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=str(INGEST), env=env)
    except subprocess.TimeoutExpired:
        return False, f"batch of {size} timed out after {timeout}s"
    out = (r.stdout or "").strip().splitlines()
    if out:
        tail = out[-1]
    else:
        err = (r.stderr or "").strip().splitlines()
        tail = err[-1] if err else ""
    if r.returncode != 0:
        return False, f"rc={r.returncode}: {tail}"
    if tail.startswith(_NO_PROGRESS_TAILS):
        # [S38/W6c P3-3] rc=0 but the builder did NOTHING — see
        # _NO_PROGRESS_TAILS. Count it as a failure (bounded spin).
        return False, f"no progress — {tail}"
    return True, tail


def _reclaimable_errors() -> int:
    """Error rows that a --retry-errors sweep could still reclaim: below
    the attempts cap and not probed in the last hour (build_ats_directory's
    sweep criteria)."""
    if not DB.exists():
        return 0
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return db.execute(
            "SELECT COUNT(*) FROM directory WHERE status='error'"
            " AND attempts < 2"
            " AND (last_probed_at IS NULL OR last_probed_at <"
            "      datetime('now', '-1 hour'))"
        ).fetchone()[0]
    finally:
        db.close()


def main() -> int:
    if "--counts" in sys.argv[1:]:
        # [S38/W6c P3-4] the workflow's cheap no-op gate (see
        # _print_counts) — no drain, no leg burned, no DRAIN_RESULT.
        return _print_counts()
    if not DB.exists():
        print(f"no DB at {DB} — seed first (build_ats_directory.py --seed)",
              flush=True)
        emit_result("failed")
        checkpoint_wal()
        return 0
    if pending_count() == 0:
        # Audit F4: the daily-cron early-exit path must also sweep aged,
        # reclaimable error rows (the workflow gate counts them, so we can
        # land here with work left). One sweep batch retries them; rows that
        # fail again hit the attempts cap → terminal → the gate returns to 0
        # and the chain self-terminates.
        # [S37-W3] the refresh lane: a STALE UNIVERSE is also work — run
        # refresh batches while budget remains (the workflow's gate counts
        # refresh-eligible rows too, so the chain re-triggers until the
        # universe is fresh; the leg cap bounds the day).
        refresh_left = refresh_eligible_count()
        if _reclaimable_errors() > 0 or refresh_left > 0:
            capped, leg = _leg_guard()
            if capped:
                checkpoint_wal()
                emit_result("complete")
                return 0
            start = time.monotonic()
            batches = 0
            failures = 0
            result = "pending"
            while time.monotonic() - start < BUDGET:
                work_left = (_reclaimable_errors() > 0
                             or refresh_eligible_count() > 0)
                if not work_left:
                    result = "complete"
                    break
                ok, tail = run_batch(BATCH, refresh=True)
                if not ok and "timed out" in tail:
                    ok, tail = run_batch("25", refresh=True)
                batches += 1
                print(f"[refresh] batch {batches}: "
                      f"{'ok' if ok else 'FAIL'} — {tail}", flush=True)
                if not ok:
                    failures += 1
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        print(f"{MAX_CONSECUTIVE_FAILURES} consecutive "
                              f"failures — stopping (DRAIN_RESULT=failed)",
                              flush=True)
                        result = "failed"
                        break
                else:
                    failures = 0
            checkpoint_wal()
            result = _cap_aware(result, leg)
            print(f"drain end ({result}): {stats_line()}", flush=True)
            emit_result(result)
            return 0
        print(f"nothing pending — drain already complete. {stats_line()}",
              flush=True)
        emit_result("complete")
        return 0

    capped, leg = _leg_guard()
    if capped:
        checkpoint_wal()
        emit_result("complete")
        return 0

    print(f"drain start: {stats_line()}", flush=True)
    print(f"budget={BUDGET}s batch={BATCH} sleep={SLEEP}", flush=True)
    start = time.monotonic()
    failures = 0
    batches = 0
    result = "pending"

    while time.monotonic() - start < BUDGET:
        if pending_count() == 0:
            # Audit F4: with pending==0 the loop used to break BEFORE any
            # run_batch, so --retry-errors (which only sweeps when a batch
            # finds nothing pending) was dead code in the drain — 'complete'
            # shipped reclaimable error rows. Run one final sweep batch
            # while budget remains.
            # [S37-W3] the refresh lane: pending exhausted mid-run — flow
            # straight into the universe refresh within the SAME leg's
            # budget (the stalest boards first; the chain continues via
            # DRAIN_RESULT=pending until the universe is fresh).
            while (time.monotonic() - start < BUDGET
                   and (_reclaimable_errors() > 0
                        or refresh_eligible_count() > 0)):
                ok, tail = run_batch(BATCH, refresh=True)
                if not ok and "timed out" in tail:
                    ok, tail = run_batch("25", refresh=True)
                batches += 1
                print(f"[refresh] batch {batches}: "
                      f"{'ok' if ok else 'FAIL'} — {tail}", flush=True)
                if not ok:
                    failures += 1
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        print(f"{MAX_CONSECUTIVE_FAILURES} consecutive "
                              f"failures — stopping (DRAIN_RESULT=failed)",
                              flush=True)
                        result = "failed"
                        break
                else:
                    failures = 0
            if result != "failed":
                result = ("pending" if pending_count() > 0
                          or _reclaimable_errors() > 0
                          or refresh_eligible_count() > 0
                          else "complete")
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
    result = _cap_aware(result, leg)
    checkpoint_wal()
    print(f"drain end ({result}): {stats_line()} after {batches} batches",
          flush=True)
    emit_result(result)
    return 0


if __name__ == "__main__":
    # Audit F5: the docstring promises exit 0 with a DRAIN_RESULT marker, but
    # malformed env knobs or a corrupt DB could raise BEFORE emission — the
    # re-trigger step would then never fire and the chain stalls ≤24 h. Keep
    # the promise: any surprise becomes 'failed' + rc 0.
    try:
        sys.exit(main())
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
