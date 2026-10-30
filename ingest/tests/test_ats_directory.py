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


# ── S50-T2: the four new probes + the refresh mode ─────────────────────
class _FakeResp:
    def __init__(self, status_code=200, text="", json_data=None,
                 exc=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data
        self._exc = exc

    def json(self):
        if self._exc:
            raise self._exc
        return self._json


def _patch_requests(monkeypatch, resp_by_call):
    """Feed probe_x's requests.get/post a scripted response sequence."""
    import types
    calls = []

    class _FakeReq:
        RequestException = batd.requests.RequestException

        def get(self, url, **k):
            calls.append(("get", url, k))
            r = resp_by_call[len(calls) - 1] if len(calls) <= \
                len(resp_by_call) else resp_by_call[-1]
            if isinstance(r, Exception):
                raise r
            return r

        def post(self, url, **k):
            calls.append(("post", url, k))
            r = resp_by_call[len(calls) - 1] if len(calls) <= \
                len(resp_by_call) else resp_by_call[-1]
            if isinstance(r, Exception):
                raise r
            return r

    fake = _FakeReq()
    monkeypatch.setattr(batd, "requests", fake)
    return calls


class TestNewProbes:
    """[S50-T2] each probe's live/dead/error contract, pinned to the
    live-measured shapes (DESIGN-S50 §0, measured 2026-10-30)."""

    def test_workday_live_and_company_is_tenant(self, monkeypatch):
        _patch_requests(monkeypatch, [_FakeResp(
            json_data={"total": 1135})])
        assert batd.probe_workday("santander|wd3|santandercareers") == \
            ("live", 1135, "santander")

    def test_workday_404_and_422_are_dead(self, monkeypatch):
        _patch_requests(monkeypatch, [_FakeResp(status_code=422)])
        assert batd.probe_workday("zz|wd1|zz")[0] == "dead"
        _patch_requests(monkeypatch, [_FakeResp(status_code=404)])
        assert batd.probe_workday("zz|wd1|zz")[0] == "dead"

    def test_workday_connection_error_is_error_never_dead(self,
                                                          monkeypatch):
        # [RA5] a DNS blip must not freeze a live board terminal-dead
        _patch_requests(monkeypatch, [
            batd.requests.RequestException("dns blip")])
        assert batd.probe_workday("nvidia|wd5|ext")[0] == "error"

    def test_bamboohr_live_counts_positions(self, monkeypatch):
        _patch_requests(monkeypatch, [_FakeResp(json_data={
            "departments": [
                {"positions": [{"id": 1}, {"id": 2}]},
                {"positions": [{"id": 3}]},
                {"no": "positions key"},
            ]})])
        assert batd.probe_bamboohr("100percentgroup") == \
            ("live", 3, "")

    def test_bamboohr_blankstate_is_live_zero(self, monkeypatch):
        # the live-with-0-jobs semantics (risk #1): never dead
        _patch_requests(monkeypatch, [_FakeResp(json_data={
            "departments": []})])
        assert batd.probe_bamboohr("somelive") == ("live", 0, "")

    def test_bamboohr_200_empty_is_dead(self, monkeypatch):
        # the wildcard-DNS unknown-tenant answer (live-measured)
        _patch_requests(monkeypatch, [_FakeResp(
            json_data=None,
            exc=ValueError("no body") if None else None)])
        # ValueError from r.json() = the 200-empty shape
        _patch_requests(monkeypatch, [_FakeResp(
            text="", json_data=None)])
        class _JErr(_FakeResp):
            def json(self):
                raise ValueError("Expecting value")
        _patch_requests(monkeypatch, [_JErr()])
        assert batd.probe_bamboohr("zznosuchtenant") == ("dead", 0, "")

    def test_icims_live_with_markers_and_pager(self, monkeypatch):
        html = ('<li class="iCIMS_JobCardItem">x</li>' * 33
                + "<span>Page 1 of 3</span>")
        _patch_requests(monkeypatch, [_FakeResp(text=html)])
        st, count, company = batd.probe_icims("careers-gbrx")
        assert st == "live"
        assert count == 33 + 2 * 50          # the cap-bounded estimate
        assert company == "gbrx"              # careers- prefix stripped

    def test_icims_200_without_markers_is_error(self, monkeypatch):
        # the parking-page guard (a redirect to a marketing page)
        _patch_requests(monkeypatch, [_FakeResp(
            text="<html><body>we moved!</body></html>")])
        assert batd.probe_icims("zz") == ("error", 0, "")

    def test_icims_dead_on_404(self, monkeypatch):
        _patch_requests(monkeypatch, [_FakeResp(status_code=404)])
        assert batd.probe_icims("zz")[0] == "dead"

    def test_paylocity_live_via_pagedata(self, monkeypatch):
        html = ('<script>window.pageData = {"ModuleTitle": "x", '
                '"Jobs": [{"JobId": 1}, {"JobId": 2}]}</script>')
        _patch_requests(monkeypatch, [_FakeResp(text=html)])
        assert batd.probe_paylocity("some-guid") == ("live", 2, "")

    def test_paylocity_200_without_pagedata_is_error(self, monkeypatch):
        # [RA9] the adapter's refuse-case — never live, never dead
        _patch_requests(monkeypatch, [_FakeResp(
            text="<html><body>careers portal moved</body></html>")])
        assert batd.probe_paylocity("wrong-guid") == ("error", 0, "")

    def test_paylocity_pagedata_without_jobs_is_error(self, monkeypatch):
        html = '<script>window.pageData = {"ModuleTitle": "x"}</script>'
        _patch_requests(monkeypatch, [_FakeResp(text=html)])
        assert batd.probe_paylocity("g") == ("error", 0, "")

    def test_paylocity_dead_on_404(self, monkeypatch):
        _patch_requests(monkeypatch, [_FakeResp(status_code=404)])
        assert batd.probe_paylocity("g")[0] == "dead"

    def test_pagedata_extractor_handles_nested_braces(self):
        html = ('window.pageData = {"a": {"b": "}"}, '
                '"Jobs": [1, 2, 3]}')
        assert batd._page_data_jobs(html) == 3


class TestRefreshMode:
    """[S50-T2/RA3] the claim order + the attempts reset + the
    resurrection + the company-preserving UPDATE."""

    def _stamp(self, conn, days_ago=None):
        import datetime as _dt
        if days_ago is None:
            return None
        return (_dt.datetime.now()
                - _dt.timedelta(days=days_ago)).strftime(
                    "%Y-%m-%dT%H:%M:%S")

    def test_pending_outranks_refresh(self, db, monkeypatch):
        db.execute("INSERT INTO directory (ats, slug) "
                   "VALUES ('lever', 'fresh_pending')")
        db.execute(
            "INSERT INTO directory (ats, slug, status, last_probed_at) "
            "VALUES ('greenhouse', 'stale_live', 'live', ?)",
            (self._stamp(db, 50),))
        db.commit()
        got: list = []
        monkeypatch.setattr(batd, "PROBES", {
            "lever": lambda s: (got.append(s), ("live", 1, ""))[1],
            "greenhouse": lambda s: (
                got.append(s), ("live", 1, ""))[1]})
        batd.run_batch(db, None, 10, 0, refresh_days=30,
                       dead_refresh_days=95)
        assert got == ["fresh_pending"]      # pending first, batch=10

    def test_stale_live_claimed_when_no_pending(self, db, monkeypatch):
        db.execute(
            "INSERT INTO directory (ats, slug, status, last_probed_at) "
            "VALUES ('greenhouse', 'stale_live', 'live', ?)",
            (self._stamp(db, 50),))
        db.execute(
            "INSERT INTO directory (ats, slug, status, last_probed_at) "
            "VALUES ('greenhouse', 'fresh_live', 'live', ?)",
            (self._stamp(db, 1),))
        db.commit()
        got: list = []
        monkeypatch.setattr(batd, "PROBES", {"greenhouse": lambda s: (
            got.append(s), ("live", 5, ""))[1]})
        batd.run_batch(db, None, 10, 0, refresh_days=30,
                       dead_refresh_days=95)
        assert got == ["stale_live"]          # only the stale row claims

    def test_dead_resurrection_flips_live(self, db, monkeypatch):
        db.execute(
            "INSERT INTO directory (ats, slug, status, last_probed_at, "
            "attempts) VALUES ('lever', 'backfromdead', 'dead', ?, 3)",
            (self._stamp(db, 100),))
        db.commit()
        monkeypatch.setattr(batd, "PROBES", {"lever": lambda s: ("live", 7, "")})
        batd.run_batch(db, None, 10, 0, refresh_days=30,
                       dead_refresh_days=95)
        row = db.execute(
            "SELECT status, job_count, attempts FROM directory "
            "WHERE slug='backfromdead'").fetchone()
        assert row[0] == "live" and row[1] == 7
        assert row[2] == 1                    # reset at claim, +1 probe

    def test_error_rollover_reclaims_capped_errors(self, db,
                                                   monkeypatch):
        # [RA6] the iCIMS parking-page class: attempts=2 error rows
        # aged past the window re-enter via the rollover
        db.execute(
            "INSERT INTO directory (ats, slug, status, last_probed_at, "
            "attempts) VALUES ('icims', 'parked', 'error', ?, 2)",
            (self._stamp(db, 100),))
        db.commit()
        got: list = []
        monkeypatch.setattr(batd, "PROBES", {"icims": lambda s: (
            got.append(s), ("dead", 0, ""))[1]})
        batd.run_batch(db, None, 10, 0, refresh_days=30,
                       dead_refresh_days=95)
        assert got == ["parked"]

    def test_company_never_clobbered_by_empty_probe(self, db,
                                                     monkeypatch):
        # [RA4] the probe returns '' — the learned name survives
        db.execute(
            "INSERT INTO directory (ats, slug, company, status, "
            "last_probed_at) VALUES ('paylocity', 'guid1', "
            "'Mesa County Public Library District', 'live', ?)",
            (self._stamp(db, 50),))
        db.commit()
        monkeypatch.setattr(batd, "PROBES", {"paylocity": lambda s: ("live", 4, "")})
        batd.run_batch(db, None, 10, 0, refresh_days=30,
                       dead_refresh_days=95)
        row = db.execute(
            "SELECT company FROM directory WHERE slug='guid1'").fetchone()
        assert row[0] == "Mesa County Public Library District"

    def test_company_updated_when_probe_returns_a_name(self, db,
                                                       monkeypatch):
        db.execute(
            "INSERT INTO directory (ats, slug, company, status, "
            "last_probed_at) VALUES ('greenhouse', 'gh1', '', "
            "'live', ?)", (self._stamp(db, 50),))
        db.commit()
        monkeypatch.setattr(batd, "PROBES", {"greenhouse": lambda s: ("live", 4, "Acme Inc")})
        batd.run_batch(db, None, 10, 0, refresh_days=30,
                       dead_refresh_days=95)
        row = db.execute(
            "SELECT company FROM directory WHERE slug='gh1'").fetchone()
        assert row[0] == "Acme Inc"

    def test_seeded_ats_subset_of_probes_pin(self):
        # [RA16] a deploy skew would mass-kill a platform's rows
        for ats, fname, _ in [
                ("ashby", "ashby_companies.json", None),
                ("greenhouse", "greenhouse_companies.json", None),
                ("lever", "lever_companies.json", None),
                ("bamboohr", "bamboohr_companies.json", None),
                ("icims", "icims_companies.json", None),
                ("paylocity", "paylocity_companies_clean.json", "name"),
                ("workday", "workday_companies.json", None)]:
            assert ats in batd.PROBES, \
                f"{ats} is seeded but has no probe (mass-kill risk)"
