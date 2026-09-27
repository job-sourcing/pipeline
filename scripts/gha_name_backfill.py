#!/usr/bin/env python3
"""Time-budgeted greenhouse company-name backfill runner for GitHub Actions.

Second instance of the durable-collection pattern (SKILL §A3 / S12 —
reference implementation: scripts/gha_drain.py). The greenhouse probe
reads `meta.company_name`, but the jobs endpoint's `meta` only carries
`total` — so 5,285 live greenhouse rows shipped with empty company names
(audit findings-data.md, DATA-BUG 1). The fix source, verified live
2026-09-08: GET https://boards-api.greenhouse.io/v1/boards/{slug} →
{"name": "Anthropic"} — a tiny JSON on the SAME API host the drain
already probes (public, no-auth, polite pacing).

Env knobs (all optional):
  BACKFILL_BUDGET_SECONDS      wall-clock budget    (default 2400 = 40 min)
  BACKFILL_BATCH               boards per batch     (default 200)
  BACKFILL_SLEEP               per-request sleep    (default 0.3)
  BACKFILL_LEGS_MAX_PER_DAY    daily leg/re-trigger cap (default 6;
                               date-keyed counter in the committed DB —
                               the 2026-09-09 budget-burn lesson: 31 legs
                               x 40 min blew the 2,000 min/mo allowance)

Machine-readable result (for the workflow):
  - appends BACKFILL_RESULT=pending|complete|failed to $GITHUB_ENV
  - 'pending' ONLY while (reclaimable nameless rows remain AND legs
    remain today) — a slug with name_attempts >= 3 (3 failed lookups)
    is permanently skipped: nameless, but no longer blocking (audit B1)
  - always exits 0 (the workflow branches on BACKFILL_RESULT, not rc)
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DB = REPO / "ingest" / "data" / "ats_directory.db"

_BOARDS_API = "https://boards-api.greenhouse.io/v1/boards/{slug}"
UA = "jobsearch-ats-directory/1.0 (company-name backfill)"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


BUDGET = _int_env("BACKFILL_BUDGET_SECONDS", 2400)
BATCH = _int_env("BACKFILL_BATCH", 200)
SLEEP = float(os.environ.get("BACKFILL_SLEEP", "0.3"))
# Audit B1: a failed lookup used to leave the row untouched forever —
# the row was re-selected every batch, so the gate stayed > 0 forever
# and the chain self-retriggered all day (the 2026-09-09 burn). 3
# strikes = permanently skipped (nameless but no longer blocking).
MAX_NAME_ATTEMPTS = 3
LEGS_MAX_PER_DAY = _int_env("BACKFILL_LEGS_MAX_PER_DAY", 6)


def ensure_schema() -> None:
    """Idempotent B1 migration: the name_attempts strike counter.

    Guarded by PRAGMA table_info so pre-migration DBs, re-runs and
    fresh checkouts are all safe (the same guard the workflow gate
    uses before it can see the column)."""
    db = sqlite3.connect(DB)
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(directory)")}
        if "name_attempts" not in cols:
            db.execute("ALTER TABLE directory "
                       "ADD COLUMN name_attempts INTEGER DEFAULT 0")
            db.commit()
            print("schema: added directory.name_attempts (B1 migration)",
                  flush=True)
    finally:
        db.close()


def nameless_count() -> int:
    """RECLAIMABLE nameless rows only (audit B1): rows at the 3-strike
    cap no longer count — permanently skipped, never blocking."""
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return db.execute(
            "SELECT COUNT(*) FROM directory WHERE ats='greenhouse' "
            "AND status='live' AND (company IS NULL OR company = '') "
            "AND (name_attempts IS NULL OR name_attempts < ?)",
            (MAX_NAME_ATTEMPTS,)).fetchone()[0]
    finally:
        db.close()


def stats_line() -> str:
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        named = db.execute(
            "SELECT COUNT(*) FROM directory WHERE ats='greenhouse' "
            "AND status='live' AND company IS NOT NULL AND company != ''"
        ).fetchone()[0]
        dead = db.execute(
            "SELECT COUNT(*) FROM directory WHERE ats='greenhouse' "
            "AND status='live' AND (company IS NULL OR company = '') "
            "AND name_attempts >= ?", (MAX_NAME_ATTEMPTS,)).fetchone()[0]
        total_live = db.execute(
            "SELECT COUNT(*) FROM directory WHERE status='live'").fetchone()[0]
        return (f"greenhouse_named={named} nameless_dead(3-strike)={dead} "
                f"live_total={total_live}")
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
    print(f"BACKFILL_RESULT={result}", flush=True)
    genv = os.environ.get("GITHUB_ENV")
    if genv:
        with open(genv, "a") as f:
            f.write(f"BACKFILL_RESULT={result}\n")


def fetch_name(slug: str) -> str | None:
    """GET /v1/boards/{slug} → {'name': …}. None on failure (row stays
    nameless; failures never abort the batch — flag, never drop)."""
    req = urllib.request.Request(
        _BOARDS_API.format(slug=slug), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            import json
            payload = json.loads(r.read())
            name = str(payload.get("name") or "").strip()
            return name or None
    except Exception:  # noqa: BLE001
        return None


def _legs_bump() -> int:
    """Date-keyed leg counter in the committed DB — the daily re-trigger
    cap (2026-09-09 GHA-burn lesson; mirrors the watch's *.legs.json,
    but in the DB so the race-proof checkpoint — which rescues the .db —
    carries it). Resets with the date; tomorrow's cron resumes."""
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
            ("backfill", today)).fetchone()
        legs = (row[0] if row else 0) + 1
        db.execute(
            "INSERT INTO runner_legs (runner, date, legs) VALUES (?, ?, ?) "
            "ON CONFLICT(runner, date) DO UPDATE SET legs=excluded.legs",
            ("backfill", today, legs))
        db.commit()
        return legs
    finally:
        db.close()


def run_batch() -> tuple[int, int]:
    """One batch: claim BATCH reclaimable nameless rows, GET names,
    UPDATE. Failed lookups bump name_attempts (audit B1: a failure must
    make progress toward leaving the pending set, 3 strikes = skipped
    forever). Returns (attempted, named)."""
    db = sqlite3.connect(DB)
    try:
        rows = db.execute(
            "SELECT slug FROM directory WHERE ats='greenhouse' "
            "AND status='live' AND (company IS NULL OR company = '') "
            "AND (name_attempts IS NULL OR name_attempts < ?) "
            "ORDER BY RANDOM() LIMIT ?", (MAX_NAME_ATTEMPTS, BATCH)).fetchall()
        attempted = named = 0
        for (slug,) in rows:
            attempted += 1
            name = fetch_name(slug)
            if name:
                db.execute(
                    "UPDATE directory SET company = ?, name_attempts = 0 "
                    "WHERE ats='greenhouse' AND slug = ?", (name, slug))
                named += 1
            else:
                db.execute(
                    "UPDATE directory SET "
                    "name_attempts = COALESCE(name_attempts, 0) + 1 "
                    "WHERE ats='greenhouse' AND slug = ?", (slug,))
            db.commit()
            time.sleep(SLEEP)
        return attempted, named
    finally:
        db.close()


def main() -> int:
    if not DB.exists():
        emit_result("failed")
        return 0
    ensure_schema()
    remaining = nameless_count()
    if remaining == 0:
        print(f"nothing to backfill — all reclaimable greenhouse names "
              f"present. {stats_line()}", flush=True)
        emit_result("complete")
        return 0

    leg = _legs_bump()
    if leg > LEGS_MAX_PER_DAY:
        # Cap already exceeded (extra dispatch, manual run): NO work, and
        # 'complete' — NOT 'pending' — so the workflow does not re-trigger
        # (audit B1). Tomorrow's cron resets the date-keyed counter.
        print(f"leg {leg} > BACKFILL_LEGS_MAX_PER_DAY={LEGS_MAX_PER_DAY} — "
              f"no work today; tomorrow's cron resumes", flush=True)
        checkpoint_wal()
        emit_result("complete")
        return 0

    print(f"backfill start: {stats_line()} (reclaimable nameless="
          f"{remaining}; leg {leg}/{LEGS_MAX_PER_DAY})", flush=True)
    start = time.monotonic()
    result = "pending"
    while time.monotonic() - start < BUDGET:
        if nameless_count() == 0:
            result = "complete"
            break
        attempted, named = run_batch()
        elapsed = int(time.monotonic() - start)
        print(f"[{elapsed:>5}s] batch: {named}/{attempted} named "
              f"({stats_line()})", flush=True)
        if attempted == 0:
            # Zero claimable rows while the gate still counts some —
            # statuses flipped under us mid-batch. Never re-trigger on a
            # zero-progress leg (burn guard): the daily cron re-evaluates.
            result = "complete"
            break

    # 'pending' ONLY while (reclaimable rows remain AND legs remain
    # today) — exactly what the workflow's re-trigger step keys on.
    if result == "pending" and (nameless_count() == 0
                                or leg >= LEGS_MAX_PER_DAY):
        if nameless_count() > 0:
            print(f"leg {leg}/{LEGS_MAX_PER_DAY} — daily cap reached, not "
                  f"re-triggering; tomorrow's cron resumes", flush=True)
        result = "complete"

    checkpoint_wal()
    print(f"backfill end ({result}): {stats_line()}", flush=True)
    emit_result(result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 — keep the rc-0 contract
        import traceback
        traceback.print_exc()
        print(f"unexpected {type(exc).__name__} — emitting "
              f"BACKFILL_RESULT=failed", flush=True)
        emit_result("failed")
        checkpoint_wal()
        sys.exit(0)
