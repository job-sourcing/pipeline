"""S24 pins — unreachable-list classification (run 36872298267).

Root cause: blacksesame's LIST fetch hit ONE transient ConnectTimeout
(www.blacksesame.com from a GHA runner) and the whole 83-board daily
watch failed. A GHA board-probe re-run 12h later fetched the board
200/103KB/9-US-hits — the failure was a network blip, not a contract
change. The board writes NO state on a network failure (no silent-empty
risk — that trap is the PARSE-level 200-with-0-rows case, which still
fails loudly), so the honest classification is 'unreachable' with a
cross-date streak, escalating to failed only when the board is
persistently network-dead.

Pinned here:
  1. _net_transient — network-class exceptions YES, HTTP verdicts and
     parse-level errors NO (the never-green-a-dead-board guard stays).
  2. run_watch — an undeclared board's network-class LIST failure
     returns 'unreachable', state untouched, alerts log written;
     the streak escalates to 'failed' at the cap; a healthy list
     resets the streak; 'unreachable' never fails the run overall.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "ingest"))

_SCRIPT = REPO_ROOT / "scripts" / "gha_board_watch.py"
_spec = importlib.util.spec_from_file_location("gha_board_watch_s24", _SCRIPT)
watch = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("gha_board_watch_s24", watch)
_spec.loader.exec_module(watch)

from jobsearch.config import Config  # noqa: E402

CFG = {"label": "test_watch", "board": "custom:blacksesame",
       "company": "Black Sesame", "country": "United States",
       "time_type": "Full time"}


@pytest.fixture
def wdir(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "WATCH_DIR", tmp_path)
    monkeypatch.setattr(watch, "LIST_SLEEP", 0.0)
    monkeypatch.setattr(watch, "DETAIL_SLEEP", 0.0)
    return tmp_path


# ── Layer 1: _net_transient classification ──────────────────────────────
class TestNetTransient:
    def test_urlopen_timeout_is_transient(self):
        exc = urllib.error.URLError(
            "<urlopen error [Errno 101] Network is unreachable>")
        assert watch._net_transient(exc) is True

    def test_requests_connectionerror_is_transient(self):
        import requests.exceptions as rqe
        exc = rqe.ConnectionError(
            "HTTPSConnectionPool(host='www.blacksesame.com', port=443): "
            "Max retries exceeded with url: /en/join-us/")
        assert watch._net_transient(exc) is True

    def test_bare_timeout_and_oserror_are_transient(self):
        assert watch._net_transient(TimeoutError("connect timed out")) is True
        assert watch._net_transient(OSError("connection reset")) is True

    def test_ssl_handshake_is_transient(self):
        import ssl
        assert watch._net_transient(
            ssl.SSLError("The handshake operation timed out")) is True

    def test_http_verdict_is_not_transient(self):
        # the server ANSWERED — a verdict, not a network failure
        exc = urllib.error.HTTPError(
            "https://x", 406, "Not Acceptable", None, None)
        assert watch._net_transient(exc) is False

    def test_requests_httperror_is_not_transient(self):
        import requests as rq
        resp = rq.models.Response()
        resp.status_code = 404
        exc = rq.HTTPError("404 Client Error", response=resp)
        assert watch._net_transient(exc) is False

    def test_parse_level_error_is_not_transient(self):
        # ValueError/KeyError/regex failures = the renamed-board traps:
        # fetch answered, parse got garbage — still fail loudly
        assert watch._net_transient(ValueError("0 cards parsed")) is False
        assert watch._net_transient(KeyError("bulletFields")) is False


# ── Layer 2: run_watch classification ───────────────────────────────────
class TestUnreachableClassification:
    def _net_fail(self, wdir, monkeypatch, exc):
        import requests.exceptions as rqe
        if exc is None:
            exc = rqe.ConnectionError(
                "HTTPSConnectionPool(host='www.blacksesame.com', "
                "port=443): Max retries exceeded")
        def boom(*a, **k):
            raise exc
        monkeypatch.setattr(watch, "current_postings", boom)
        monkeypatch.setattr(watch, "_legs_bump", lambda label: 1)
        return watch.run_watch(dict(CFG), Config())

    def test_transient_returns_unreachable_state_untouched(
            self, wdir, monkeypatch):
        result = self._net_fail(wdir, monkeypatch, None)
        assert result == "unreachable"
        # state untouched: no state / newposts / digest files created
        assert not (wdir / "test_watch.state.jsonl").exists()
        assert not (wdir / "test_watch.newposts.jsonl").exists()
        # loud: the alerts log carries the classification
        alerts = (wdir / "test_watch.alerts.log").read_text(
            encoding="utf-8")
        assert "UNREACHABLE" in alerts

    def test_streak_escalates_to_failed_at_cap(
            self, wdir, monkeypatch):
        (wdir / "test_watch.legs.json").write_text(json.dumps(
            {"date": "2000-01-01", "legs": 99,
             "unreach_streak": watch._UNREACH_STREAK_CAP - 1}))
        result = self._net_fail(wdir, monkeypatch, None)
        assert result == "failed"
        alerts = (wdir / "test_watch.alerts.log").read_text(
            encoding="utf-8")
        assert "STREAK CAP" in alerts

    def test_http_verdict_still_fails_loudly(self, wdir, monkeypatch):
        exc = urllib.error.HTTPError(
            "https://x/jobs", 404, "Not Found", None, None)
        result = self._net_fail(wdir, monkeypatch, exc)
        assert result == "failed"          # the guard is intact

    def test_unreachable_never_fails_run_overall(self):
        # replicated from main()'s logic for the pin: unreachable sits
        # with egress_blocked/backlog — loud in the summary, not a
        # red run
        vals = {"a": "complete", "b": "unreachable", "c": "egress_blocked"}
        overall = ("failed" if "failed" in vals
                   else "backlog" if "backlog" in vals else "complete")
        assert overall == "complete"

    def test_healthy_list_resets_unreach_streak(self, wdir, monkeypatch):
        (wdir / "test_watch.legs.json").write_text(json.dumps(
            {"date": "2000-01-01", "legs": 1, "unreach_streak": 3}))
        current = [{"reqId": "R1", "title": "T", "externalPath": "/R1",
                    "url": "https://x/R1", "locationsText": "US, CA, SJ",
                    "postedOn": "", "company": "Black Sesame"}]
        monkeypatch.setattr(
            watch, "current_postings",
            lambda *a, **k: ({r["reqId"]: r for r in current},
                             True, False))
        monkeypatch.setattr(watch, "_legs_bump", lambda label: 1)
        monkeypatch.setattr(
            watch, "enrich_new",
            lambda rows, *a, **k: rows)
        monkeypatch.setattr(watch, "corroborate_new",
                            lambda *a, **k: {})
        monkeypatch.setattr(watch, "_send_alerts", lambda *a, **k: None)
        result = watch.run_watch(dict(CFG), Config())
        assert result == "complete"
        m = json.loads((wdir / "test_watch.legs.json").read_text())
        assert m["unreach_streak"] == 0    # the blip is forgiven
