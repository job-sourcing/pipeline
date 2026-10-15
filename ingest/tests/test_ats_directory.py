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
