"""Tests for scripts/gha_drain.py — the drain leg cap (audit
findings-stack-quality.md B1/F5-adjacent: cheap insurance so a
pathological re-seed cannot self-retrigger unbounded ~100-min legs all
day, the exact class that burned 31 x 40-min backfill legs on
2026-09-09).

Covers:
  - the date-keyed leg counter (bumps, resets with the date)
  - a capped day does NO work and reports complete (NOT pending → the
    workflow's re-trigger step does not fire; tomorrow's cron resumes)
  - 'pending' only while legs remain: the LAST allowed leg demotes a
    pending verdict to complete
  - 'failed' and 'complete' verdicts are never demoted
  - both work-entry paths (main loop + the F4 error-sweep path) are
    guarded; a no-op day burns no leg

No network, no subprocess: run_batch / checkpoint_wal are monkeypatched.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # repo root
SCRIPT = REPO_ROOT / "scripts" / "gha_drain.py"

_spec = importlib.util.spec_from_file_location("gha_drain", SCRIPT)
dr = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("gha_drain", dr)
_spec.loader.exec_module(dr)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "ats.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE directory ("
        "            ats TEXT NOT NULL,"
        "            slug TEXT NOT NULL,"
        "            company TEXT DEFAULT '',"
        "            job_count INTEGER,"
        "            status TEXT DEFAULT 'pending',"
        "            attempts INTEGER NOT NULL DEFAULT 0,"
        "            last_probed_at TEXT,"
        "            PRIMARY KEY (ats, slug)"
        "        )")
    conn.execute("INSERT INTO directory (ats, slug, status) "
                 "VALUES ('lever', 'acme', 'pending')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(dr, "DB", path)
    monkeypatch.setattr(dr, "checkpoint_wal", lambda: None)
    return path


@pytest.fixture
def genv(tmp_path, monkeypatch):
    env_file = tmp_path / "github_env"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    return env_file


def _result(genv) -> str:
    lines = [l for l in genv.read_text().splitlines() if l.strip()]
    assert lines, "no DRAIN_RESULT emitted"
    key, _, value = lines[-1].partition("=")
    assert key == "DRAIN_RESULT"
    return value


def _no_subprocess(*a, **k):
    raise AssertionError("run_batch (a subprocess) must be mocked in tests")


# ── the leg counter itself ───────────────────────────────────────────────
class TestLegCounter:
    def test_bumps_and_is_date_keyed(self, db):
        assert dr._legs_bump() == 1
        assert dr._legs_bump() == 2
        # yesterday's row → the counter resets for today
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM runner_legs")
        conn.execute("INSERT INTO runner_legs VALUES ('drain', ?, 99)",
                     (yesterday,))
        conn.commit()
        conn.close()
        assert dr._legs_bump() == 1

    def test_leg_guard_uncapped(self, db, monkeypatch):
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 5)
        capped, leg = dr._leg_guard()
        assert (capped, leg) == (False, 1)

    def test_leg_guard_capped(self, db, monkeypatch):
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 1)
        assert dr._legs_bump() == 1
        capped, leg = dr._leg_guard()
        assert capped is True
        assert leg == 2


# ── cap_aware verdict demotion ───────────────────────────────────────────
class TestCapAware:
    def test_pending_demoted_at_cap(self, monkeypatch):
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 8)
        assert dr._cap_aware("pending", 8) == "complete"

    def test_pending_kept_below_cap(self, monkeypatch):
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 8)
        assert dr._cap_aware("pending", 7) == "pending"

    def test_failed_never_demoted(self, monkeypatch):
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 1)
        assert dr._cap_aware("failed", 99) == "failed"

    def test_complete_untouched(self, monkeypatch):
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 1)
        assert dr._cap_aware("complete", 99) == "complete"


# ── main() gating (no network, no subprocess) ────────────────────────────
class TestMainLegCap:
    def test_pending_only_while_legs_remain(self, db, genv, monkeypatch):
        # budget already exhausted → the loop body never runs; work
        # remains and legs remain → 'pending' (the workflow re-triggers)
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 5)
        monkeypatch.setattr(dr, "BUDGET", -1)
        monkeypatch.setattr(dr, "run_batch", _no_subprocess)
        assert dr.main() == 0
        assert _result(genv) == "pending"
        conn = sqlite3.connect(db)
        legs = conn.execute(
            "SELECT legs FROM runner_legs WHERE runner='drain'").fetchone()
        conn.close()
        assert legs[0] == 1                 # the leg was counted

    def test_last_leg_demotes_pending_to_complete(
            self, db, genv, monkeypatch):
        # work remains, budget exhausted, leg == cap → no re-trigger
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 1)
        monkeypatch.setattr(dr, "BUDGET", -1)
        monkeypatch.setattr(dr, "run_batch", _no_subprocess)
        assert dr.main() == 0
        assert _result(genv) == "complete"
        assert dr.pending_count() == 1      # work still remains, honestly

    def test_cap_exceeded_no_work_reports_complete(
            self, db, genv, monkeypatch):
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 1)
        assert dr._legs_bump() == 1         # today's only allowed leg
        monkeypatch.setattr(dr, "run_batch", _no_subprocess)
        assert dr.main() == 0
        assert _result(genv) == "complete"  # NOT pending → no re-trigger

    def test_sweep_path_is_capped_too(self, db, genv, monkeypatch):
        # pending==0 but an aged reclaimable error row exists (F4 sweep
        # path) — the cap must guard this entry point as well
        conn = sqlite3.connect(db)
        conn.execute("UPDATE directory SET status='error', attempts=0, "
                     "last_probed_at='2020-01-01 00:00:00' "
                     "WHERE slug='acme'")
        conn.commit()
        conn.close()
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 1)
        assert dr._legs_bump() == 1
        monkeypatch.setattr(dr, "run_batch", _no_subprocess)
        assert dr.main() == 0
        assert _result(genv) == "complete"

    def test_sweep_pending_while_legs_remain(self, db, genv, monkeypatch):
        conn = sqlite3.connect(db)
        conn.execute("UPDATE directory SET status='error', attempts=0, "
                     "last_probed_at='2020-01-01 00:00:00' "
                     "WHERE slug='acme'")
        conn.commit()
        conn.close()
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 5)
        calls: list[str] = []

        def fake_run_batch(size):
            calls.append(size)
            return True, "swept 1 error row"
        monkeypatch.setattr(dr, "run_batch", fake_run_batch)
        assert dr.main() == 0
        assert calls == [dr.BATCH]           # the sweep batch actually ran
        assert _result(genv) == "pending"    # reclaimable work remains

    def test_no_work_day_burns_no_leg(self, db, genv, monkeypatch):
        conn = sqlite3.connect(db)
        conn.execute("UPDATE directory SET status='live' "
                     "WHERE slug='acme'")
        conn.commit()
        conn.close()
        monkeypatch.setattr(dr, "run_batch", _no_subprocess)
        assert dr.main() == 0
        assert _result(genv) == "complete"
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "runner_legs" not in tables    # no-op day: no leg counted

    def test_missing_db_emits_failed(self, tmp_path, genv, monkeypatch):
        monkeypatch.setattr(dr, "DB", tmp_path / "nope.db")
        monkeypatch.setattr(dr, "checkpoint_wal", lambda: None)
        assert dr.main() == 0
        assert _result(genv) == "failed"
