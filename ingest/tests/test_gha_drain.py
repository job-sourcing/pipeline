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
        # [S37-W3 amended] the sweep+refresh path now LOOPS while work
        # remains within the budget (the pre-S37 one-batch-then-settle
        # shape is superseded). The fake CLEARS the work per batch —
        # the loop runs, the work empties, the result settles complete.
        conn = sqlite3.connect(db)
        conn.execute("UPDATE directory SET status='error', attempts=0, "
                     "last_probed_at='2020-01-01 00:00:00' "
                     "WHERE slug='acme'")
        conn.commit()
        conn.close()
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 5)
        monkeypatch.setattr(dr, "BUDGET", 5)
        calls: list[tuple] = []

        def fake_run_batch(size, refresh=False):
            calls.append((size, refresh))
            # the batch genuinely clears the reclaimable work (the
            # aged error row heals) — the loop's work_left then empties
            c = sqlite3.connect(db)
            c.execute("UPDATE directory SET status='live', attempts=1, "
                      "last_probed_at=datetime('now') WHERE slug='acme'")
            c.commit()
            c.close()
            return True, "swept 1 error row"
        monkeypatch.setattr(dr, "run_batch", fake_run_batch)
        assert dr.main() == 0
        assert calls and calls[0][0] == dr.BATCH   # the sweep batch ran
        assert all(c[1] for c in calls)            # refresh-armed batches
        assert _result(genv) == "complete"         # work emptied honestly

    def test_no_work_day_burns_no_leg(self, db, genv, monkeypatch):
        conn = sqlite3.connect(db)
        conn.execute("UPDATE directory SET status='live', "
                     "last_probed_at=datetime('now') "
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


# ── S38/W6c P3-3: the fake-batch spin bound ───────────────────────────────
class TestS38NoProgressSpin:
    """A builder batch returning rc=0 having probed NOTHING (the
    daemon-refusal tail — local-only, GHA sets ATS_PROBER_DAEMON=1 — or
    "nothing pending" on a count/select divergence) used to read
    ok=True / failures=0: a ~1 s/iteration subprocess spin for the full
    80-min budget ending 'pending', up to 8 legs/day of churn. The
    no-progress tails now count as failures, so the existing
    5-consecutive-failure cutoff bounds the spin in seconds."""

    def _fake_subproc(self, monkeypatch, stdout, rc=0):
        import types

        def fake_run(cmd, **k):
            return types.SimpleNamespace(
                returncode=rc, stdout=stdout, stderr="")
        monkeypatch.setattr(dr.subprocess, "run", fake_run)

    def test_refusal_tail_is_a_failure(self, db, monkeypatch):
        self._fake_subproc(
            monkeypatch,
            "[batch] refusing to start: the prober daemon is draining "
            "this directory (fresh heartbeat). Stop the daemon first.")
        ok, tail = dr.run_batch("60")
        assert ok is False
        assert "no progress" in tail

    def test_nothing_pending_tail_is_a_failure(self, db, monkeypatch):
        self._fake_subproc(monkeypatch, "[batch] nothing pending")
        ok, tail = dr.run_batch("60", refresh=True)
        assert ok is False
        assert "no progress" in tail

    def test_a_real_done_tail_is_still_success(self, db, monkeypatch):
        self._fake_subproc(
            monkeypatch,
            "[batch] refresh: re-probing 3 stale boards\n"
            "[batch] done: 3 slugs in 12s")
        ok, tail = dr.run_batch("60", refresh=True)
        assert ok is True
        assert tail.startswith("[batch] done")

    def test_nonzero_rc_still_a_failure(self, db, monkeypatch):
        self._fake_subproc(monkeypatch, "[batch] probing 5 slugs", rc=1)
        ok, tail = dr.run_batch("60")
        assert ok is False and tail.startswith("rc=1")

    def test_refresh_loop_spin_ends_failed(self, db, genv, monkeypatch):
        # loop-level pin: a no-progress builder must NOT spin for the
        # budget — exactly MAX_CONSECUTIVE_FAILURES batches, then
        # DRAIN_RESULT=failed (seconds, not 80 minutes). Refresh-path
        # entry: pending==0 + an aged error row = work_left true.
        conn = sqlite3.connect(db)
        conn.execute("UPDATE directory SET status='error', attempts=0, "
                     "last_probed_at='2020-01-01 00:00:00' "
                     "WHERE slug='acme'")
        conn.commit()
        conn.close()
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 5)
        monkeypatch.setattr(dr, "BUDGET", 4800)
        calls: list[tuple] = []

        def fake_run_batch(size, refresh=False):
            calls.append((size, refresh))
            return False, "no progress — [batch] refusing to start"
        monkeypatch.setattr(dr, "run_batch", fake_run_batch)
        assert dr.main() == 0
        assert _result(genv) == "failed"
        assert len(calls) == dr.MAX_CONSECUTIVE_FAILURES

    def test_main_loop_spin_ends_failed_too(self, db, genv, monkeypatch):
        # the pending>0 main loop spun on the same refusal tail — the
        # fix is in run_batch, so it is bounded here as well (the
        # time.sleep back-off is stubbed to keep the test instant)
        monkeypatch.setattr(dr.time, "sleep", lambda s: None)
        monkeypatch.setattr(dr, "LEGS_MAX_PER_DAY", 5)
        monkeypatch.setattr(dr, "BUDGET", 4800)
        calls: list[tuple] = []

        def fake_run_batch(size, refresh=False):
            calls.append((size, refresh))
            return False, "no progress — [batch] nothing pending"
        monkeypatch.setattr(dr, "run_batch", fake_run_batch)
        assert dr.main() == 0            # 'acme' is pending → main loop
        assert _result(genv) == "failed"
        assert len(calls) == dr.MAX_CONSECUTIVE_FAILURES


# ── S38/W6c P3-4: the --counts gate (single source of truth) ──────────────
class TestS38CountsGate:
    """The workflow gate's needs-work count comes from gha_drain itself
    (same env knobs, same SQL the driver gates on) — the yml's inline
    third copy of the predicate SQL (hardcoded 30/95/attempts/-1h) is
    gone, and the driver-vs-builder equivalence is pinned so the two
    remaining copies cannot drift apart silently."""

    def test_counts_mode_emits_pending(self, db, genv):
        # 'acme' is pending → exactly 1 row needing work
        assert dr._print_counts() == 0
        assert "DRAIN_PENDING=1" in genv.read_text()

    def test_counts_mode_zero_on_a_done_day(self, db, genv):
        conn = sqlite3.connect(db)
        conn.execute("UPDATE directory SET status='live', "
                     "last_probed_at=datetime('now') WHERE slug='acme'")
        conn.commit()
        conn.close()
        assert dr._print_counts() == 0
        assert "DRAIN_PENDING=0" in genv.read_text()

    def test_counts_mode_counts_the_stale_universe(self, db, genv):
        conn = sqlite3.connect(db)
        conn.execute("UPDATE directory SET status='live', "
                     "last_probed_at=datetime('now', '-45 days') "
                     "WHERE slug='acme'")
        conn.commit()
        conn.close()
        assert dr._print_counts() == 0
        assert "DRAIN_PENDING=1" in genv.read_text()

    def test_counts_mode_red_and_silent_when_db_missing(
            self, tmp_path, genv, monkeypatch):
        # the old inline gate's SystemExit contract, kept: a missing DB
        # is a RED step and writes NO DRAIN_PENDING (genv is created
        # lazily by the writer — absent means nothing was written)
        monkeypatch.setattr(dr, "DB", tmp_path / "nope.db")
        monkeypatch.setattr(dr, "checkpoint_wal", lambda: None)
        assert dr._print_counts() == 1
        assert (not genv.exists()
                or "DRAIN_PENDING" not in genv.read_text())

    def test_counts_mode_dispatches_from_argv(self, db, genv, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["gha_drain.py", "--counts"])
        assert dr.main() == 0
        assert "DRAIN_PENDING=" in genv.read_text()

    def test_workflow_gate_uses_the_driver_not_a_sql_copy(self):
        # the yml must call the driver's --counts and must NOT re-grow
        # a hardcoded copy of the predicate (30/95/-1h SQL)
        yml = (REPO_ROOT / ".github" / "workflows" /
               "ats-drain.yml").read_text(encoding="utf-8")
        assert "gha_drain.py --counts" in yml
        assert "datetime('now'" not in yml
        assert "'-30 days'" not in yml
        assert "'-95 days'" not in yml

    def test_gate_counts_match_the_builders_selection(
            self, tmp_path, monkeypatch, genv):
        # driver-vs-builder equivalence pin (the two LIVE predicate
        # copies): every row the builder's refresh selection claims is
        # counted by the driver's refresh_eligible_count() — and only
        # those. Rows: stale-live(40d) + stale-dead(100d) + error with
        # attempts left (the P2-3 class) are WORK; fresh-live,
        # fresh-dead, capped-error are not; pending drains first and is
        # counted by pending_count().
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location(
            "build_ats_directory_gatepin",
            REPO_ROOT / "ingest" / "scripts" / "build_ats_directory.py")
        batd = _ilu.module_from_spec(spec)
        spec.loader.exec_module(batd)

        dbp = tmp_path / "gate.db"
        monkeypatch.setattr(dr, "DB", dbp)
        monkeypatch.setattr(batd, "DB_PATH", dbp)
        monkeypatch.setattr(batd, "HEARTBEAT", tmp_path / "no-hb.txt")
        monkeypatch.setattr(batd, "install_dns_cache", lambda: None)
        conn = batd._connect()
        rows = [
            ("lever", "stale-live", "live", 0, "-40 days"),
            ("lever", "fresh-live", "live", 0, "-1 days"),
            ("lever", "stale-dead", "dead", 0, "-100 days"),
            ("lever", "fresh-dead", "dead", 0, "-10 days"),
            ("lever", "err-retry", "error", 1, "-2 hours"),
            ("lever", "err-capped", "error", 2, "-2 hours"),
            ("lever", "pending-row", "pending", 0, None),
        ]
        conn.executemany(
            "INSERT INTO directory (ats, slug, status, attempts, "
            "last_probed_at) VALUES (?, ?, ?, ?, "
            "COALESCE(datetime('now', ?), '2000-01-01 00:00:00'))",
            rows)
        conn.commit()

        # the gate's view BEFORE any work: 1 pending + 3 refresh rows
        assert dr.pending_count() == 1
        assert dr.refresh_eligible_count() == 3
        assert dr._print_counts() == 0
        assert "DRAIN_PENDING=4" in genv.read_text()

        # the builder claims EXACTLY that set: batch 1 drains pending
        # (priority), batch 2 takes the refresh selection
        probed: list[str] = []
        monkeypatch.setitem(batd.PROBES, "lever",
                            lambda slug: (probed.append(slug),
                                          ("live", 1, ""))[1])
        batd.run_batch(conn, "lever", 100, 0.0,
                       refresh_days=dr.REFRESH_DAYS,
                       dead_refresh_days=dr.DEAD_REFRESH_DAYS)
        batd.run_batch(conn, "lever", 100, 0.0,
                       refresh_days=dr.REFRESH_DAYS,
                       dead_refresh_days=dr.DEAD_REFRESH_DAYS)
        conn.close()

        assert sorted(probed) == sorted(
            ["pending-row",                    # priority
             "stale-live", "stale-dead", "err-retry"])   # refresh set
        # and after the work the gate (and the driver) see zero
        assert dr.pending_count() == 0
        assert dr.refresh_eligible_count() == 0
        assert dr._print_counts() == 0
        assert genv.read_text().splitlines()[-1] == "DRAIN_PENDING=0"
