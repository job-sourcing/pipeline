"""Tests for scripts/gha_name_backfill.py — the backfill burn-proofing
(audit findings-stack-quality.md B1: the 2026-09-09 incident where the
chain self-retriggered 31 legs x 40 min and blew the 2,000 min/mo
allowance).

Covers the three structural fixes:
  - attempts migration: idempotent guarded ALTER (pre-migration DBs)
  - 3-strike pending exclusion: failed lookups bump name_attempts; rows
    at the cap leave the pending set (nameless but no longer blocking)
  - leg-cap gating: date-keyed counter in the DB; 'pending' is emitted
    only while (reclaimable rows remain AND legs remain today)

No network: fetch_name is always monkeypatched.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # repo root
SCRIPT = REPO_ROOT / "scripts" / "gha_name_backfill.py"

_spec = importlib.util.spec_from_file_location("gha_name_backfill", SCRIPT)
bf = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("gha_name_backfill", bf)
_spec.loader.exec_module(bf)


# The pre-migration schema (exactly what build_ats_directory.py creates
# and what the committed DB looked like before the B1 fix).
OLD_SCHEMA = """
CREATE TABLE directory (
            ats TEXT NOT NULL,
            slug TEXT NOT NULL,
            company TEXT DEFAULT '',
            job_count INTEGER,
            status TEXT DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_probed_at TEXT,
            PRIMARY KEY (ats, slug)
        )
"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A committed-DB stand-in at a tmp path, pre-migration schema."""
    path = tmp_path / "ats.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO directory (ats, slug, status) "
                 "VALUES ('greenhouse', 'acme', 'live')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(bf, "DB", path)
    monkeypatch.setattr(bf, "BATCH", 200)
    monkeypatch.setattr(bf, "SLEEP", 0.0)
    return path


@pytest.fixture
def genv(tmp_path, monkeypatch):
    """Capture emit_result via a GITHUB_ENV stand-in file."""
    env_file = tmp_path / "github_env"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    return env_file


def _result(genv) -> str:
    lines = [l for l in genv.read_text().splitlines() if l.strip()]
    assert lines, "no BACKFILL_RESULT emitted"
    key, _, value = lines[-1].partition("=")
    assert key == "BACKFILL_RESULT"
    return value


def _attempts(path: Path, slug: str) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT name_attempts FROM directory "
            "WHERE ats='greenhouse' AND slug=?", (slug,)).fetchone()[0]
    finally:
        conn.close()


def _no_network(*a, **k):
    raise AssertionError("fetch_name must be monkeypatched in tests")


# ── attempts migration ───────────────────────────────────────────────────
class TestSchemaMigration:
    def test_pre_migration_db_gets_the_column(self, db):
        bf.ensure_schema()
        cols = {r[1] for r in sqlite3.connect(db).execute(
            "PRAGMA table_info(directory)")}
        assert "name_attempts" in cols

    def test_existing_rows_default_to_zero(self, db):
        bf.ensure_schema()
        assert _attempts(db, "acme") == 0

    def test_migration_is_idempotent(self, db):
        bf.ensure_schema()
        bf.ensure_schema()   # second run must be a no-op, not an error
        cols = [r[1] for r in sqlite3.connect(db).execute(
            "PRAGMA table_info(directory)")]
        assert cols.count("name_attempts") == 1

    def test_migrated_schema_already_present(self, db):
        conn = sqlite3.connect(db)
        conn.execute("ALTER TABLE directory "
                     "ADD COLUMN name_attempts INTEGER DEFAULT 0")
        conn.commit()
        conn.close()
        bf.ensure_schema()   # column exists → no-op
        assert _attempts(db, "acme") == 0


# ── 3-strike pending exclusion ──────────────────────────────────────────
class TestThreeStrike:
    def test_failed_lookup_bumps_attempts(self, db, monkeypatch):
        bf.ensure_schema()
        monkeypatch.setattr(bf, "fetch_name", lambda slug: None)
        attempted, named = bf.run_batch()
        assert (attempted, named) == (1, 0)
        assert _attempts(db, "acme") == 1   # failure makes progress now

    def test_three_strikes_leave_the_pending_set(self, db, monkeypatch):
        bf.ensure_schema()
        monkeypatch.setattr(bf, "fetch_name", lambda slug: None)
        for _ in range(3):
            bf.run_batch()
        # the row is still nameless+live …
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT company, status FROM directory WHERE slug='acme'"
        ).fetchone()
        conn.close()
        assert row == ("", "live")
        assert _attempts(db, "acme") == 3
        # … but it no longer blocks: the gate count drops to 0
        assert bf.nameless_count() == 0

    def test_three_strikes_are_not_selected(self, db, monkeypatch):
        bf.ensure_schema()
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO directory (ats, slug, status) "
                     "VALUES ('greenhouse', 'fresh', 'live')")
        conn.execute("UPDATE directory SET name_attempts=3 "
                     "WHERE slug='acme'")
        conn.commit()
        conn.close()
        seen: list[str] = []
        monkeypatch.setattr(
            bf, "fetch_name",
            lambda slug: (seen.append(slug) or "Named"))
        attempted, named = bf.run_batch()
        assert (attempted, named) == (1, 1)
        assert seen == ["fresh"]          # dead slug never re-selected

    def test_success_names_the_row_and_resets_attempts(self, db, monkeypatch):
        bf.ensure_schema()
        conn = sqlite3.connect(db)
        conn.execute("UPDATE directory SET name_attempts=2 "
                     "WHERE slug='acme'")
        conn.commit()
        conn.close()
        monkeypatch.setattr(bf, "fetch_name", lambda slug: "Acme Inc")
        attempted, named = bf.run_batch()
        assert (attempted, named) == (1, 1)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT company, name_attempts FROM directory "
            "WHERE slug='acme'").fetchone()
        conn.close()
        assert row == ("Acme Inc", 0)
        assert bf.nameless_count() == 0

    def test_non_greenhouse_nameless_rows_ignored(self, db, monkeypatch):
        bf.ensure_schema()
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO directory (ats, slug, status) "
                     "VALUES ('lever', 'namelessco', 'live')")
        conn.commit()
        conn.close()
        monkeypatch.setattr(bf, "fetch_name", _no_network)
        assert bf.nameless_count() == 1   # lever row never counts


# ── leg cap (date-keyed counter in the DB) ──────────────────────────────
class TestLegCap:
    def test_legs_counter_bumps_and_is_date_keyed(self, db):
        bf.ensure_schema()
        assert bf._legs_bump() == 1
        assert bf._legs_bump() == 2
        # yesterday's row → the counter resets for today
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM runner_legs")
        conn.execute("INSERT INTO runner_legs VALUES ('backfill', ?, 99)",
                     (yesterday,))
        conn.commit()
        conn.close()
        assert bf._legs_bump() == 1

    def test_cap_exceeded_no_work_reports_complete(self, db, genv, monkeypatch):
        bf.ensure_schema()
        monkeypatch.setattr(bf, "LEGS_MAX_PER_DAY", 1)
        assert bf._legs_bump() == 1          # today's only allowed leg
        monkeypatch.setattr(bf, "fetch_name", _no_network)  # must NOT run
        monkeypatch.setattr(bf, "checkpoint_wal", lambda: None)
        assert bf.main() == 0
        assert _result(genv) == "complete"   # NOT pending → no re-trigger

    def test_pending_only_while_legs_remain(self, db, genv, monkeypatch):
        # budget already exhausted → the loop never runs → work remains
        # and legs remain → 'pending' (the workflow re-triggers)
        bf.ensure_schema()
        monkeypatch.setattr(bf, "LEGS_MAX_PER_DAY", 5)
        monkeypatch.setattr(bf, "BUDGET", -1)
        monkeypatch.setattr(bf, "checkpoint_wal", lambda: None)
        assert bf.main() == 0
        assert _result(genv) == "pending"
        # the leg was counted for today
        conn = sqlite3.connect(db)
        legs = conn.execute(
            "SELECT legs FROM runner_legs WHERE runner='backfill'").fetchone()
        conn.close()
        assert legs[0] == 1

    def test_last_leg_demotes_pending_to_complete(self, db, genv, monkeypatch):
        # work remains, budget exhausted, but leg == cap → no re-trigger
        bf.ensure_schema()
        monkeypatch.setattr(bf, "LEGS_MAX_PER_DAY", 1)
        monkeypatch.setattr(bf, "BUDGET", -1)
        monkeypatch.setattr(bf, "checkpoint_wal", lambda: None)
        assert bf.main() == 0
        assert _result(genv) == "complete"
        assert bf.nameless_count() == 1     # work still remains, honestly

    def test_zero_progress_leg_does_not_retrigger(self, db, genv, monkeypatch):
        # statuses flip under us: gate counts a row the selection no
        # longer claims → attempted==0 → complete, never pending
        bf.ensure_schema()
        monkeypatch.setattr(bf, "BUDGET", 10)
        monkeypatch.setattr(bf, "checkpoint_wal", lambda: None)

        real_nameless = bf.nameless_count
        monkeypatch.setattr(bf, "nameless_count", lambda: 1)
        monkeypatch.setattr(bf, "run_batch", lambda: (0, 0))
        monkeypatch.setattr(bf, "fetch_name", _no_network)
        try:
            assert bf.main() == 0
        finally:
            monkeypatch.setattr(bf, "nameless_count", real_nameless)
        assert _result(genv) == "complete"


# ── runner result contract ───────────────────────────────────────────────
class TestResultContract:
    def test_missing_db_emits_failed(self, tmp_path, genv, monkeypatch):
        monkeypatch.setattr(bf, "DB", tmp_path / "nope.db")
        assert bf.main() == 0
        assert _result(genv) == "failed"

    def test_nothing_reclaimable_emits_complete_without_leg(
            self, db, genv, monkeypatch):
        bf.ensure_schema()
        conn = sqlite3.connect(db)
        conn.execute("UPDATE directory SET company='Acme' "
                     "WHERE slug='acme'")   # nothing left to reclaim
        conn.commit()
        conn.close()
        monkeypatch.setattr(bf, "fetch_name", _no_network)  # must NOT run
        assert bf.main() == 0
        assert _result(genv) == "complete"
        # a no-op day burns no leg (the counter table isn't even touched)
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "runner_legs" not in tables

    def test_all_work_done_mid_loop_emits_complete(
            self, db, genv, monkeypatch):
        bf.ensure_schema()
        monkeypatch.setattr(bf, "BUDGET", 30)
        monkeypatch.setattr(bf, "checkpoint_wal", lambda: None)
        monkeypatch.setattr(bf, "fetch_name", lambda slug: "Acme Inc")
        assert bf.main() == 0
        assert _result(genv) == "complete"
