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
