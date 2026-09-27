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
# Per-request worst case in build_ats_directory: 15 s HTTP timeout + sleep.
# Scale the subprocess timeout to the batch size (audit F6: a fixed 300 s
# still times out the shrunk 25-batch when every probe hangs: 25×15.3≈383 s).
_REQUEST_S = 15.3


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
    """Run one drain batch. Returns (ok, last_output_line)."""
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
             "--batch", size, "--sleep", SLEEP, "--retry-errors"],
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
        if _reclaimable_errors() > 0:
            capped, leg = _leg_guard()
            if capped:
                checkpoint_wal()
                emit_result("complete")
                return 0
            print(f"pending exhausted — sweeping "
                  f"{_reclaimable_errors()} aged error rows (--retry-errors)",
                  flush=True)
            ok, tail = run_batch(BATCH)
            print(f"[sweep] {'ok' if ok else 'FAIL'} — {tail}", flush=True)
            checkpoint_wal()
            result = _cap_aware(
                "pending" if pending_count() > 0
                or _reclaimable_errors() > 0 else "complete", leg)
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
            if _reclaimable_errors() > 0 and batches > 0:
                print("pending exhausted — sweeping aged error rows "
                      "(--retry-errors)", flush=True)
                ok, tail = run_batch(BATCH)
                batches += 1
                print(f"[sweep] batch {batches}: "
                      f"{'ok' if ok else 'FAIL'} — {tail}", flush=True)
            result = ("pending" if pending_count() > 0 or _reclaimable_errors() > 0
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
