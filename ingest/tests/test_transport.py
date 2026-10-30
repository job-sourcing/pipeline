"""ZenRows premium-proxy transport tests (jobsearch/transport.py).

Offline: requests.request is monkeypatched so no credits are burned.
"""
from __future__ import annotations

import pytest
import requests

from jobsearch.config import Config
from jobsearch import transport


class _FakeResp:
    def __init__(self, payload, status=200, text=""):
        self._payload = payload
        self.status_code = status
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class TestZenrowsFetchJson:
    @pytest.fixture(autouse=True)
    def _no_relay_pool(self, monkeypatch):
        """[S51] hermetic: the tracked ingest/.env carries the live
        ZENROWS_KEYS pool + NETLIFY_RELAY config — these pins test the
        DIRECT request shape, so neutralize both (the relay path has
        its own pins in test_s51_vendor_relay.py)."""
        from jobsearch import vendor_relay as _vr
        monkeypatch.setattr(_vr, "relay_configured", lambda: False)
        monkeypatch.setattr(_vr, "zenrows_keys", lambda: [])
        monkeypatch.setattr(_vr, "_envfile_vals", lambda: {})

    def test_not_configured_raises(self):
        cfg = Config()
        with pytest.raises(RuntimeError, match="ZENROWS_API_KEY"):
            transport.zenrows_fetch_json("https://example.com/api", cfg=cfg)

    def test_request_shape(self, monkeypatch):
        """premium_proxy + proxy_country + custom_headers + URL-encoded target
        with our original headers forwarded."""
        seen: dict = {}

        def fake_request(method, url, *, params=None, headers=None,
                         json=None, timeout=None):
            seen.update(method=method, url=url, params=params,
                        headers=headers, json=json, timeout=timeout)
            return _FakeResp({"ok": True})

        monkeypatch.setattr(transport.requests, "request", fake_request)
        cfg = Config()
        cfg.zenrows_api_key = "zr-key"
        cfg.http_timeout_s = 30
        out = transport.zenrows_fetch_json(
            "https://api.example.com/search",
            params={"Keyword": "python dev", "ResultsPerPage": 3},
            headers={"Authorization-Key": "k", "User-Agent": "me@x.com"},
            cfg=cfg, proxy_country="us")

        assert out == {"ok": True}
        assert seen["method"] == "GET"
        assert seen["url"] == transport.ZENROWS_API
        assert seen["params"]["apikey"] == "zr-key"
        assert seen["params"]["premium_proxy"] == "true"
        assert seen["params"]["proxy_country"] == "us"
        assert seen["params"]["custom_headers"] == "true"
        assert "Keyword=python+dev" in seen["params"]["url"]
        assert seen["params"]["url"].startswith("https://api.example.com/search?")
        assert seen["headers"]["Authorization-Key"] == "k"
        # timeout floor: proxied calls are slower than plain fetches
        assert seen["timeout"] >= 60

    def test_js_render_flag(self, monkeypatch):
        seen: dict = {}

        def fake_request(method, url, *, params=None, headers=None,
                         json=None, timeout=None):
            seen["params"] = params
            return _FakeResp({"ok": 1})

        monkeypatch.setattr(transport.requests, "request", fake_request)
        cfg = Config()
        cfg.zenrows_api_key = "zr-key"
        transport.zenrows_fetch_json("https://example.com", cfg=cfg,
                                     js_render=True)
        assert seen["params"]["js_render"] == "true"

    def test_non_json_raises_runtimeerror(self, monkeypatch):
        monkeypatch.setattr(
            transport.requests, "request",
            lambda *a, **k: _FakeResp(None, status=200, text="<html>challenge</html>"))
        cfg = Config()
        cfg.zenrows_api_key = "zr-key"
        with pytest.raises(RuntimeError, match="non-JSON"):
            transport.zenrows_fetch_json("https://example.com", cfg=cfg)


class TestNetlifyFetchJson:
    """Netlify edge-scraper transport (US egress; ZenRows-free fallback).

    Offline: requests.post/get are monkeypatched — no site quota is used.
    """

    def test_not_configured_raises(self):
        cfg = Config()
        with pytest.raises(RuntimeError, match="NETLIFY_SCRAPER_TOKEN"):
            transport.netlify_fetch_json("https://example.com/api", cfg=cfg)

    def test_request_shape_and_inline_json(self, monkeypatch):
        """The scrape job carries the full target URL (params encoded),
        forwarded headers, fetch engine + inline mode."""
        seen: dict = {}

        def fake_post(url, *, json=None, headers=None, timeout=None):
            seen.update(url=url, json=json, headers=headers)
            return _FakeResp({
                "results": [{
                    "index": 0, "ok": True, "status": 200,
                    "url": "https://api.example.com/search",
                    "blob_key": None,
                    "inline_body": '{"SearchResult": {"items": []}}',
                }]})

        monkeypatch.setattr(transport.requests, "post", fake_post)
        cfg = Config()
        cfg.netlify_scraper_token = "nfl-token"
        cfg.http_timeout_s = 30
        out = transport.netlify_fetch_json(
            "https://api.example.com/search",
            params={"Keyword": "python dev", "ResultsPerPage": 3},
            headers={"Authorization-Key": "k", "User-Agent": "me@x.com"},
            cfg=cfg)

        assert out == {"SearchResult": {"items": []}}
        assert seen["url"].endswith("/api/scrape")
        assert seen["headers"]["Authorization"] == "Bearer nfl-token"
        job = seen["json"]["jobs"][0]
        assert job["url"] == ("https://api.example.com/search?"
                              "Keyword=python+dev&ResultsPerPage=3")
        assert job["engine"] == "fetch"
        assert job["method"] == "GET"
        assert job["headers"] == {"Authorization-Key": "k",
                                  "User-Agent": "me@x.com"}

    def test_blob_fallback_for_non_html(self, monkeypatch):
        """JSON bodies land in the blob store — the transport follows the
        blob_key transparently."""
        def fake_post(url, *, json=None, headers=None, timeout=None):
            return _FakeResp({
                "results": [{
                    "index": 0, "ok": True, "status": 200,
                    "url": "https://api.example.com/search",
                    "blob_key": "result/batch-1-0",
                    "inline_body": None,
                }]})

        def fake_get(url, *, headers=None, timeout=None):
            assert url.endswith("/site:scraper-results/result/batch-1-0")
            return _FakeResp(None, text='{"ok": true}')

        monkeypatch.setattr(transport.requests, "post", fake_post)
        monkeypatch.setattr(transport.requests, "get", fake_get)
        cfg = Config()
        cfg.netlify_scraper_token = "nfl-token"
        out = transport.netlify_fetch_json("https://api.example.com/x",
                                           cfg=cfg)
        assert out == {"ok": True}

    def test_target_4xx_raises_httperror(self, monkeypatch):
        """Upstream API errors surface as requests.HTTPError so caller
        except-clauses can key off the status code (mirrors fetch_json)."""
        def fake_post(url, *, json=None, headers=None, timeout=None):
            return _FakeResp({
                "results": [{
                    "index": 0, "ok": False, "status": 404,
                    "url": "https://api.example.com/missing",
                    "blob_key": None, "inline_body": None,
                }]})

        monkeypatch.setattr(transport.requests, "post", fake_post)
        cfg = Config()
        cfg.netlify_scraper_token = "nfl-token"
        with pytest.raises(requests.HTTPError) as ei:
            transport.netlify_fetch_json("https://api.example.com/missing",
                                         cfg=cfg)
        assert ei.value.response is not None
        assert ei.value.response.status_code == 404

    def test_scraper_error_raises_runtimeerror(self, monkeypatch):
        def fake_post(url, *, json=None, headers=None, timeout=None):
            return _FakeResp({"error": "puppeteer engine is only available "
                                       "in build mode"})

        monkeypatch.setattr(transport.requests, "post", fake_post)
        cfg = Config()
        cfg.netlify_scraper_token = "nfl-token"
        with pytest.raises(RuntimeError, match="puppeteer engine"):
            transport.netlify_fetch_json("https://api.example.com/x",
                                         cfg=cfg)

    def test_non_json_raises_runtimeerror(self, monkeypatch):
        def fake_post(url, *, json=None, headers=None, timeout=None):
            return _FakeResp({
                "results": [{
                    "index": 0, "ok": True, "status": 200,
                    "url": "https://api.example.com/x",
                    "blob_key": None,
                    "inline_body": "<html>challenge page</html>",
                }]})

        monkeypatch.setattr(transport.requests, "post", fake_post)
        cfg = Config()
        cfg.netlify_scraper_token = "nfl-token"
        with pytest.raises(RuntimeError, match="non-JSON"):
            transport.netlify_fetch_json("https://api.example.com/x",
                                         cfg=cfg)


class TestSupabaseFetchJson:
    """Tier-1 edge-proxy transport (agent-fetch-kit integration, 2026-09-08)."""

    def test_not_configured_raises(self):
        cfg = Config()
        with pytest.raises(RuntimeError, match="SUPABASE_PROXY_URL"):
            transport.supabase_fetch_json("https://example.com/api", cfg=cfg)

    def test_request_shape(self, monkeypatch):
        """Bearer auth, x-region pinning, url+mode=raw query encoding."""
        seen: dict = {}

        def fake_get(url, *, headers=None, timeout=None):
            seen.update(url=url, headers=headers, timeout=timeout)
            return _FakeResp({"jobs": [1]})

        monkeypatch.setattr(transport.requests, "get", fake_get)
        cfg = Config()
        cfg.supabase_proxy_url = "https://px.example.supabase.co/functions/v1/proxy"
        cfg.supabase_proxy_token = "tok-1"
        cfg.http_timeout_s = 30
        out = transport.supabase_fetch_json(
            "https://api.example.com/v1/jobs",
            params={"limit": 10, "q": "py thon"}, cfg=cfg, region="us-east-1")
        assert out == {"jobs": [1]}
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(seen["url"]).query)
        assert q["mode"] == ["raw"]
        assert q["url"] == ["https://api.example.com/v1/jobs?limit=10&q=py+thon"]
        assert seen["headers"]["Authorization"] == "Bearer tok-1"
        assert seen["headers"]["x-region"] == "us-east-1"
        assert seen["timeout"] == 60  # max(http_timeout_s, 60)

    def test_transport_error_raises_runtimeerror(self, monkeypatch):
        monkeypatch.setattr(
            transport.requests, "get",
            lambda *a, **k: _FakeResp(None, status=401, text="Unauthorized"))
        cfg = Config()
        cfg.supabase_proxy_url = "https://px.example.supabase.co/x"
        cfg.supabase_proxy_token = "tok"
        with pytest.raises(RuntimeError, match="HTTP 401"):
            transport.supabase_fetch_json("https://api.example.com/x", cfg=cfg)

    def test_non_json_raises_runtimeerror(self, monkeypatch):
        monkeypatch.setattr(
            transport.requests, "get",
            lambda *a, **k: _FakeResp(None, status=200, text="<html>hi</html>"))
        cfg = Config()
        cfg.supabase_proxy_url = "https://px.example.supabase.co/x"
        cfg.supabase_proxy_token = "tok"
        with pytest.raises(RuntimeError, match="non-JSON"):
            transport.supabase_fetch_json("https://api.example.com/x", cfg=cfg)


class TestNetlifyFetchJsonRetry:
    """S32 (watch #122 root cause): transient 5xx/429 from the target /
    edge function / blob store must be retried with backoff; permanent
    4xx / engine errors fail fast. The watch failed a whole 153-board
    run on ONE Netlify 503 — these pins keep the retry honest."""

    def test_target_503_retries_then_succeeds(self, monkeypatch):
        """The watch #122 failure shape: first attempt 503, retry 200."""
        calls = {"n": 0}
        sleeps: list[float] = []
        monkeypatch.setattr(transport.time, "sleep", sleeps.append)

        def fake_post(url, *, json=None, headers=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResp({
                    "results": [{"index": 0, "ok": False, "status": 503,
                                 "url": json["jobs"][0]["url"],
                                 "blob_key": None, "inline_body": None}]})
            return _FakeResp({
                "results": [{"index": 0, "ok": True, "status": 200,
                             "url": json["jobs"][0]["url"],
                             "blob_key": None,
                             "inline_body": '{"jobs": [1]}'}]})

        monkeypatch.setattr(transport.requests, "post", fake_post)
        cfg = Config()
        cfg.netlify_scraper_token = "nfl-token"
        out = transport.netlify_fetch_json("https://api.example.com/x",
                                           cfg=cfg)
        assert out == {"jobs": [1]}
        assert calls["n"] == 2, "503 must trigger exactly one retry"
        assert sleeps == [6], "linear backoff 6s on the first retry"

    def test_target_503_exhausts_retries_and_raises(self, monkeypatch):
        calls = {"n": 0}
        monkeypatch.setattr(transport.time, "sleep", lambda *_: None)

        def fake_post(url, *, json=None, headers=None, timeout=None):
            calls["n"] += 1
            return _FakeResp({
                "results": [{"index": 0, "ok": False, "status": 503,
                             "url": "x", "blob_key": None,
                             "inline_body": None}]})

        monkeypatch.setattr(transport.requests, "post", fake_post)
        cfg = Config()
        cfg.netlify_scraper_token = "nfl-token"
        with pytest.raises(requests.HTTPError) as ei:
            transport.netlify_fetch_json("https://api.example.com/x",
                                         cfg=cfg)
        assert ei.value.response.status_code == 503
        assert calls["n"] == 3, "default = 2 retries (3 total attempts)"
        # retries=0 → single attempt, no sleep
        calls["n"] = 0
        with pytest.raises(requests.HTTPError):
            transport.netlify_fetch_json("https://api.example.com/x",
                                         cfg=cfg, retries=0)
        assert calls["n"] == 1

    def test_function_502_retries(self, monkeypatch):
        """Edge-function gateway 5xx (cold start / deploy) is transient."""
        calls = {"n": 0}
        monkeypatch.setattr(transport.time, "sleep", lambda *_: None)

        def fake_post(url, *, json=None, headers=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResp(None, status=502, text="bad gateway")
            return _FakeResp({
                "results": [{"index": 0, "ok": True, "status": 200,
                             "url": "x", "blob_key": None,
                             "inline_body": '{"ok": 1}'}]})

        monkeypatch.setattr(transport.requests, "post", fake_post)
        cfg = Config()
        cfg.netlify_scraper_token = "nfl-token"
        out = transport.netlify_fetch_json("https://api.example.com/x",
                                           cfg=cfg)
        assert out == {"ok": 1} and calls["n"] == 2

    def test_4xx_and_engine_error_fail_fast(self, monkeypatch):
        """4xx (auth/config) and engine misuse retry identically forever
        — they must fail on the FIRST attempt."""
        calls = {"n": 0}
        monkeypatch.setattr(transport.time, "sleep", lambda *_: None)

        def fake_403(url, *, json=None, headers=None, timeout=None):
            calls["n"] += 1
            return _FakeResp({
                "results": [{"index": 0, "ok": False, "status": 403,
                             "url": "x", "blob_key": None,
                             "inline_body": None}]})

        monkeypatch.setattr(transport.requests, "post", fake_403)
        cfg = Config()
        cfg.netlify_scraper_token = "nfl-token"
        with pytest.raises(requests.HTTPError):
            transport.netlify_fetch_json("https://api.example.com/x",
                                         cfg=cfg)
        assert calls["n"] == 1, "403 is permanent — no retry"

        calls["n"] = 0

        def fake_engine(url, *, json=None, headers=None, timeout=None):
            calls["n"] += 1
            return _FakeResp({"error": "puppeteer engine is only "
                                       "available in build mode"})

        monkeypatch.setattr(transport.requests, "post", fake_engine)
        with pytest.raises(RuntimeError, match="puppeteer engine"):
            transport.netlify_fetch_json("https://api.example.com/x",
                                         cfg=cfg)
        assert calls["n"] == 1, "engine error is permanent — no retry"

    def test_red_check_retry_pin_fails_without_retry(self, monkeypatch):
        """RED-CHECK (SKILL A8-5): mutate the scenario — if the retry
        were removed (all attempts raise), the first pin must FAIL."""
        def fake_post(url, *, json=None, headers=None, timeout=None):
            return _FakeResp({
                "results": [{"index": 0, "ok": False, "status": 503,
                             "url": "x", "blob_key": None,
                             "inline_body": None}]})

        monkeypatch.setattr(transport.requests, "post", fake_post)
        monkeypatch.setattr(transport.time, "sleep", lambda *_: None)
        cfg = Config()
        cfg.netlify_scraper_token = "nfl-token"
        with pytest.raises(requests.HTTPError):
            transport.netlify_fetch_json(
                "https://api.example.com/x", cfg=cfg)
