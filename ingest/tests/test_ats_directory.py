"""Tests for scripts/build_ats_directory.py (Step E tooling).

Covers the CodeRabbit round-2/round-3 hardening:
  - persisted attempts counter with a cap (an 'error' row must stop
    being re-claimed after _MAX_ATTEMPTS probes)
  - --retry-errors honors the --batch limit (--batch 1 claims 1 row)
  - pre-attempts databases are migrated in place (guarded ALTER)
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_INGEST = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "build_ats_directory", _INGEST / "scripts" / "build_ats_directory.py")
batd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(batd)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(batd, "DB_PATH", tmp_path / "ats.db")
    # isolate from a LIVE prober daemon's heartbeat (the race guard
    # would otherwise refuse every batch while the daemon drains)
    monkeypatch.setattr(batd, "HEARTBEAT", tmp_path / "no-heartbeat.txt")
    monkeypatch.setattr(batd, "install_dns_cache", lambda: None)
    conn = batd._connect()
    yield conn
    conn.close()


def _row(conn, ats, slug):
    return conn.execute(
        "SELECT status, attempts FROM directory WHERE ats=? AND slug=?",
        (ats, slug)).fetchone()


class TestAttemptsCounter:
    def test_probe_increments_attempts(self, db, monkeypatch):
        db.execute("INSERT INTO directory (ats, slug) VALUES ('lever', 'acme')")
        db.commit()
        monkeypatch.setitem(batd.PROBES, "lever",
                            lambda slug: ("live", 3, "Acme"))
        batd.run_batch(db, None, batch=5, sleep_s=0)
        assert _row(db, "lever", "acme") == ("live", 1)

    def test_unknown_ats_row_also_counts_and_goes_dead(self, db):
        # no probe fn for this ATS name → dead + attempts incremented
        db.execute("INSERT INTO directory (ats, slug) "
                   "VALUES ('notarealats', 'acme')")
        db.commit()
        batd.run_batch(db, None, batch=5, sleep_s=0)
        assert _row(db, "notarealats", "acme") == ("dead", 1)

    def test_error_rows_stop_being_retried_after_cap(self, db, monkeypatch):
        db.execute("INSERT INTO directory (ats, slug) VALUES ('lever', 'acme')")
        db.commit()
        monkeypatch.setitem(batd.PROBES, "lever", lambda slug: ("error", 0, ""))
        batd.run_batch(db, None, batch=5, sleep_s=0)          # attempt 1
        assert _row(db, "lever", "acme") == ("error", 1)
        # age the row past the 1h retry window, then retry → attempt 2
        db.execute("UPDATE directory SET last_probed_at = '2000-01-01T00:00:00'")
        db.commit()
        batd.run_batch(db, None, batch=5, sleep_s=0, retry_errors=True)
        assert _row(db, "lever", "acme") == ("error", 2)
        # aged again but attempts exhausted → never re-claimed
        db.execute("UPDATE directory SET last_probed_at = '2000-01-01T00:00:00'")
        db.commit()
        batd.run_batch(db, None, batch=5, sleep_s=0, retry_errors=True)
        assert _row(db, "lever", "acme") == ("error", 2)

    def test_fresh_error_rows_are_not_retried_immediately(self, db, monkeypatch):
        """The 1h age guard stands: a just-failed row is not re-claimed in
        the same drain loop (that would be a hot retry loop)."""
        db.execute("INSERT INTO directory (ats, slug) VALUES ('lever', 'acme')")
        db.commit()
        monkeypatch.setitem(batd.PROBES, "lever", lambda slug: ("error", 0, ""))
        batd.run_batch(db, None, batch=5, sleep_s=0)              # attempt 1
        batd.run_batch(db, None, batch=5, sleep_s=0, retry_errors=True)
        assert _row(db, "lever", "acme") == ("error", 1)

    def test_pending_rows_over_cap_are_not_claimed(self, db, monkeypatch):
        """Pending rows that already burned their attempts (e.g. status
        manually reset to pending) don't get claimed either — the cap
        applies to the pending query too."""
        db.execute("INSERT INTO directory (ats, slug, status, attempts) "
                   "VALUES ('lever', 'acme', 'pending', 2)")
        db.commit()
        monkeypatch.setitem(batd.PROBES, "lever", lambda slug: ("live", 1, ""))
        batd.run_batch(db, None, batch=5, sleep_s=0)
        assert _row(db, "lever", "acme") == ("pending", 2)


class TestRetryBatchLimit:
    def test_retry_honors_batch_limit(self, db, monkeypatch):
        """CodeRabbit round-3: '--batch 1 --retry-errors' must claim at
        most 1 error row (it used to claim 10 regardless)."""
        db.executemany(
            "INSERT INTO directory (ats, slug, status, attempts, "
            "last_probed_at) VALUES ('lever', ?, 'error', 1, "
            "'2000-01-01T00:00:00')",
            [(f"slug{i}",) for i in range(5)])
        db.commit()
        claimed: list[str] = []

        def fake_probe(slug):
            claimed.append(slug)
            return ("live", 1, "")

        monkeypatch.setitem(batd.PROBES, "lever", fake_probe)
        batd.run_batch(db, None, batch=1, sleep_s=0, retry_errors=True)
        assert len(claimed) == 1

    def test_retry_limit_never_negative(self, db, monkeypatch):
        """--batch 0 must claim nothing (not 10)."""
        db.executemany(
            "INSERT INTO directory (ats, slug, status, attempts, "
            "last_probed_at) VALUES ('lever', ?, 'error', 1, "
            "'2000-01-01T00:00:00')",
            [(f"slug{i}",) for i in range(5)])
        db.commit()
        monkeypatch.setitem(batd.PROBES, "lever", lambda slug: ("live", 1, ""))
        batd.run_batch(db, None, batch=0, sleep_s=0, retry_errors=True)
        assert _row(db, "lever", "slug0") == ("error", 1)


class TestLegacyMigration:
    def test_connect_migrates_pre_attempts_db(self, tmp_path, monkeypatch):
        """A database created by the pre-attempts schema is migrated in
        place (guarded ALTER), preserving rows with attempts=0."""
        monkeypatch.setattr(batd, "DB_PATH", tmp_path / "legacy.db")
        conn = sqlite3.connect(tmp_path / "legacy.db")
        conn.execute("""
            CREATE TABLE directory (
                ats TEXT NOT NULL,
                slug TEXT NOT NULL,
                company TEXT DEFAULT '',
                job_count INTEGER,
                status TEXT DEFAULT 'pending',
                last_probed_at TEXT,
                PRIMARY KEY (ats, slug)
            )""")
        conn.execute(
            "INSERT INTO directory (ats, slug, status) "
            "VALUES ('lever', 'acme', 'error')")
        conn.commit()
        conn.close()

        conn2 = batd._connect()          # runs the guarded ALTER
        try:
            cols = {r[1] for r in conn2.execute(
                "PRAGMA table_info(directory)")}
            assert "attempts" in cols
            assert conn2.execute(
                "SELECT attempts FROM directory "
                "WHERE ats='lever' AND slug='acme'").fetchone()[0] == 0
        finally:
            conn2.close()

        # idempotent: a second _connect() must not fail or duplicate
        conn3 = batd._connect()
        cols = {r[1] for r in conn3.execute("PRAGMA table_info(directory)")}
        assert "attempts" in cols
        conn3.close()


class TestDaemonRaceGuard:
    def test_manual_batch_refuses_while_daemon_fresh(self, db, monkeypatch,
                                                     tmp_path):
        """R4-opus-review P2: a manual --batch racing the prober daemon
        double-probes rows — run_batch must refuse on a fresh heartbeat
        (unless invoked by the daemon via the env marker)."""
        hb = tmp_path / "heartbeat.txt"
        hb.write_text("2026-08-29T00:00:00 batch=1")
        monkeypatch.setattr(batd, "HEARTBEAT", hb)
        monkeypatch.delenv("ATS_PROBER_DAEMON", raising=False)
        db.execute("INSERT INTO directory (ats, slug) VALUES ('lever', 'x')")
        db.commit()
        monkeypatch.setitem(batd.PROBES, "lever", lambda slug: ("live", 1, ""))
        batd.run_batch(db, None, batch=5, sleep_s=0)
        # refused → row untouched
        assert _row(db, "lever", "x") == ("pending", 0)

    def test_daemon_child_runs_despite_fresh_heartbeat(self, db, monkeypatch,
                                                       tmp_path):
        hb = tmp_path / "heartbeat.txt"
        hb.write_text("2026-08-29T00:00:00 batch=1")
        monkeypatch.setattr(batd, "HEARTBEAT", hb)
        monkeypatch.setenv("ATS_PROBER_DAEMON", "1")
        db.execute("INSERT INTO directory (ats, slug) VALUES ('lever', 'x')")
        db.commit()
        monkeypatch.setitem(batd.PROBES, "lever", lambda slug: ("live", 1, ""))
        batd.run_batch(db, None, batch=5, sleep_s=0)
        assert _row(db, "lever", "x") == ("live", 1)

    def test_stale_heartbeat_allows_manual_run(self, db, monkeypatch,
                                               tmp_path):
        hb = tmp_path / "heartbeat.txt"
        hb.write_text("2026-08-29T00:00:00 batch=1")
        import os
        old = hb.stat().st_mtime - 7200      # 2h old
        os.utime(hb, (old, old))
        monkeypatch.setattr(batd, "HEARTBEAT", hb)
        monkeypatch.delenv("ATS_PROBER_DAEMON", raising=False)
        db.execute("INSERT INTO directory (ats, slug) VALUES ('lever', 'x')")
        db.commit()
        monkeypatch.setitem(batd.PROBES, "lever", lambda slug: ("live", 1, ""))
        batd.run_batch(db, None, batch=5, sleep_s=0)
        assert _row(db, "lever", "x") == ("live", 1)


# ── S37-W3: the universe-refresh mode (D-S37-1 R4/A5) ─────────────────

class TestRefreshMode:
    """The refresh selection: live boards stale > refresh_days re-probe;
    dead boards re-probe at the (longer) dead cadence; pending keeps
    PRIORITY; refresh claims reset attempts (the per-cycle budget)."""

    @staticmethod
    def _stale(conn, ats, slug, status, days, attempts=0):
        conn.execute(
            "INSERT INTO directory (ats, slug, status, attempts, "
            "last_probed_at) VALUES (?, ?, ?, ?, "
            "datetime('now', ?))",
            (ats, slug, status, attempts, f"-{days} days"))
        conn.commit()

    def _claim(self, db, refresh_days=30, dead_refresh_days=95, batch=50):
        # capture what run_batch SELECTED via the probe stub: every
        # claimed slug gets probed (status overwritten) — the claimed
        # set is observable through the probe's side effect
        probed = []

        def fake_probe(slug):
            probed.append(slug)
            return "live", 3, "Probed Co"

        batd.PROBES["lever"] = fake_probe
        batd.run_batch(db, "lever", batch, 0.0,
                       refresh_days=refresh_days,
                       dead_refresh_days=dead_refresh_days)
        return probed

    def test_stale_live_board_is_reprobed(self, db):
        self._stale(db, "lever", "old-acme", "live", 45)
        probed = self._claim(db)
        assert "old-acme" in probed

    def test_fresh_live_board_is_not_reprobed(self, db):
        self._stale(db, "lever", "fresh-acme", "live", 3)
        probed = self._claim(db)
        assert "fresh-acme" not in probed

    def test_dead_board_reprobes_only_past_dead_cutoff(self, db):
        self._stale(db, "lever", "old-dead", "dead", 100)
        self._stale(db, "lever", "newer-dead", "dead", 40)
        probed = self._claim(db)
        assert "old-dead" in probed          # 100d > 95d cutoff
        assert "newer-dead" not in probed    # 40d < 95d cutoff

    def test_pending_keeps_priority_over_refresh(self, db):
        self._stale(db, "lever", "stale-live", "live", 60)
        db.execute(
            "INSERT INTO directory (ats, slug, status) "
            "VALUES ('lever', 'brand-new', 'pending')")
        db.commit()
        probed = self._claim(db)
        # the pending row is claimed (priority); the stale live row is
        # NOT in this batch (pending drains first)
        assert "brand-new" in probed
        assert "stale-live" not in probed

    def test_refresh_claim_resets_attempts(self, db):
        self._stale(db, "lever", "capped-acme", "live", 45, attempts=3)
        probed = self._claim(db)
        assert "capped-acme" in probed        # claims despite attempts=3
        row = _row(db, "lever", "capped-acme")
        # probed once this cycle: attempts went 0 (reset) + 1 = 1 — NOT 4
        assert row[1] == 1, (
            f"refresh claim must reset the attempts budget per cycle, "
             f"got {row[1]}")

    def test_refresh_disabled_by_default(self, db):
        self._stale(db, "lever", "old-acme", "live", 400)
        probed = []
        batd.PROBES["lever"] = lambda slug: (probed.append(slug), "live", 1, "x")[1:]
        batd.run_batch(db, "lever", 50, 0.0)   # no refresh_days
        assert "old-acme" not in probed

    def test_stalest_first_ordering(self, db):
        self._stale(db, "lever", "ancient", "live", 90)
        self._stale(db, "lever", "merely-old", "live", 40)
        probed = self._claim(db, batch=1)
        assert probed == ["ancient"]           # ORDER BY last_probed_at ASC


# ── S38/W6c: the P3-1/P3-2 fixes from the S37-W5a runtime-lanes audit ────

class TestS38RefreshClaimScope:
    """P3-1: the refresh attempts-reset is keyed to EXACTLY the claimed
    rows. The old predicate-shaped UPDATE dropped the SELECT's --ats and
    LIMIT scope — every matching row, not just the probed batch, had its
    retry budget zeroed ("reset but not selected"). Inert while only
    live/dead rows matched; the W5a P2-3 error clause made it live (an
    unselected error row's attempts silently restarted)."""

    @staticmethod
    def _stale(conn, ats, slug, status, days, attempts=0):
        conn.execute(
            "INSERT INTO directory (ats, slug, status, attempts, "
            "last_probed_at) VALUES (?, ?, ?, ?, datetime('now', ?))",
            (ats, slug, status, attempts, f"-{days} days"))
        conn.commit()

    def test_reset_does_not_leak_across_ats(self, db, monkeypatch):
        # an --ats-scoped refresh must not zero a stale OTHER-ATS row's
        # attempts (the docstring says "attempts reset at claim" — the
        # UPDATE's set must equal the SELECT's)
        self._stale(db, "lever", "lever-stale", "live", 45, attempts=3)
        self._stale(db, "greenhouse", "gh-stale", "live", 45, attempts=2)
        monkeypatch.setitem(batd.PROBES, "lever",
                            lambda slug: ("live", 1, ""))
        batd.run_batch(db, "lever", 50, 0.0,
                       refresh_days=30, dead_refresh_days=95)
        assert _row(db, "lever", "lever-stale")[1] == 1     # reset + probed
        assert _row(db, "greenhouse", "gh-stale")[1] == 2   # UNTOUCHED

    def test_reset_does_not_leak_past_the_batch_limit(self, db, monkeypatch):
        # batch=1 claims the STALEST row only; the other stale rows keep
        # their attempts (pre-fix: ALL stale rows were zeroed)
        self._stale(db, "lever", "ancient", "live", 90, attempts=3)
        self._stale(db, "lever", "merely-old", "live", 40, attempts=4)
        # an error row with attempts left, newer than both — also
        # unselected at batch=1, and (P2-3) also in the predicate: the
        # pre-fix UPDATE would have restarted its retry budget unseen
        db.execute(
            "INSERT INTO directory (ats, slug, status, attempts, "
            "last_probed_at) VALUES ('lever', 'err-row', 'error', 1, "
            "datetime('now', '-2 hours'))")
        db.commit()
        monkeypatch.setitem(batd.PROBES, "lever",
                            lambda slug: ("live", 1, ""))
        batd.run_batch(db, "lever", 1, 0.0,
                       refresh_days=30, dead_refresh_days=95)
        assert _row(db, "lever", "ancient")[1] == 1         # claimed+probed
        assert _row(db, "lever", "merely-old")[1] == 4      # UNTOUCHED
        assert _row(db, "lever", "err-row")[1] == 1         # budget intact


class TestS38TimestampShape:
    """P3-2: the builder's timestamps must be `datetime('now')`-shaped
    UTC (space separator) — every staleness/error cutoff in the drain,
    the builder, and the workflow gate compares last_probed_at against
    SQLite's space-format UTC strings, and at a shared date
    'T'(0x54) > ' '(0x20): the old ISO-T LOCAL writes made a row probed
    hours past the cutoff day read FRESH (<=24h one-directional
    false-fresh, all three predicate copies) and put the builder's 1h
    error cutoff on a different clock AND format than the drain's."""

    def test_now_is_space_separated(self):
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$",
                        batd._now())
        assert "T" not in batd._now()

    def test_now_minus_hours_same_shape(self):
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$",
                        batd._now_minus_hours(1))
        assert "T" not in batd._now_minus_hours(1)

    def test_now_shares_sqlites_utc_clock(self, db):
        # the written stamp and SQLite's datetime('now') must agree to
        # the second — same format AND same clock (local-time writes
        # skewed dev boxes by the TZ offset)
        import datetime as _dt
        sql_now = db.execute("SELECT datetime('now')").fetchone()[0]
        a = _dt.datetime.strptime(batd._now(), "%Y-%m-%d %H:%M:%S")
        b = _dt.datetime.strptime(sql_now, "%Y-%m-%d %H:%M:%S")
        assert abs((a - b).total_seconds()) <= 2

    def test_builder_stamp_is_cutoff_comparable(self, db):
        # a timestamp 2h old MUST compare older than datetime('now',
        # '-1 hour') inside SQLite — the pre-fix ISO-T shape read as
        # FRESH at any shared date ('T' > ' ' in string compare)
        older = batd._now_minus_hours(2)
        verdict = db.execute(
            "SELECT ? < datetime('now', '-1 hour')", (older,)
        ).fetchone()[0]
        assert verdict == 1

    def test_boundary_stale_row_is_claimed(self, db, monkeypatch):
        # the regression core: a live row truly 30d2h stale (stamped by
        # the builder's own writer) must be refresh-claimed — pre-fix it
        # read FRESH whenever the stamp shared the cutoff's date
        db.execute(
            "INSERT INTO directory (ats, slug, status, attempts, "
            "last_probed_at) VALUES ('lever', 'edge', 'live', 0, ?)",
            (batd._now_minus_hours(24 * 30 + 2),))
        db.commit()
        probed: list[str] = []

        def fake_probe(slug):
            probed.append(slug)
            return ("live", 1, "")
        monkeypatch.setitem(batd.PROBES, "lever", fake_probe)
        batd.run_batch(db, "lever", 50, 0.0,
                       refresh_days=30, dead_refresh_days=95)
        assert "edge" in probed
