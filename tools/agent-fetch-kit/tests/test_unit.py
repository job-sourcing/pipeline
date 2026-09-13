"""Unit tests for agent-fetch-kit — no network calls (backends mocked)."""
import json, os, sys, tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure lib/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from fetchkit.detect import is_challenged, CHALLENGE_MARKERS
from fetchkit.config import Config
from fetchkit.history import History
from fetchkit.backends import FetchResult
from fetchkit.core import fetch, _PLAIN_LADDER, _RENDER_LADDER, _ANTIBOT_LADDER, _backend_kwargs


# ---------- Challenge detection ----------

class TestChallengeDetection:
    def test_clean_200(self):
        chal, reason = is_challenged(200, b"<html>hello world</html>")
        assert not chal
        assert reason is None

    def test_status_403(self):
        chal, reason = is_challenged(403, b"forbidden")
        assert chal and "403" in reason

    def test_status_429(self):
        chal, _ = is_challenged(429, b"")
        assert chal

    def test_status_503(self):
        chal, _ = is_challenged(503, b"")
        assert chal

    def test_cf_marker_just_a_moment(self):
        chal, reason = is_challenged(200, b"<html><title>Just a moment...</title>...</html>")
        assert chal
        assert "title" in reason or "marker" in reason

    def test_cf_marker_body(self):
        chal, reason = is_challenged(200, b"<html>please wait, checking your browser...</html>")
        assert chal
        assert "marker" in reason

    def test_datadome_marker(self):
        chal, _ = is_challenged(200, b"<html>dd-function-name datadome</html>")
        assert chal

    def test_perimeterx_marker(self):
        chal, _ = is_challenged(200, b"<html>px-captcha perimeterx</html>")
        assert chal

    def test_incapsula_marker(self):
        chal, _ = is_challenged(200, b"<html>incap_ses incapsula</html>")
        assert chal

    def test_clean_json(self):
        chal, _ = is_challenged(200, b'{"ip":"1.2.3.4","city":"HK"}')
        assert not chal

    def test_none_body(self):
        chal, _ = is_challenged(200, None)
        assert not chal

    def test_str_body(self):
        chal, _ = is_challenged(200, "Hello world, just a moment please")
        assert chal

    def test_attention_required(self):
        chal, _ = is_challenged(200, b"<title>Attention Required! | Cloudflare</title>")
        assert chal


# ---------- Config ----------

class TestConfig:
    def test_loads_from_env(self, monkeypatch):
        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-123")
        monkeypatch.setenv("ZENROWS_API_KEY", "zr-test-456")
        c = Config()
        assert c.firecrawl_api_key == "fc-test-123"
        assert c.zenrows_api_key == "zr-test-456"

    def test_has_local_always(self):
        c = Config()
        assert c.has("local") is True
        assert c.has("browser") is True

    def test_has_supabase_when_configured(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_PROXY_URL", "https://example.com/proxy")
        monkeypatch.setenv("SUPABASE_PROXY_TOKEN", "tok")
        c = Config()
        assert c.has("supabase") is True

    def test_has_supabase_false_when_missing(self, monkeypatch):
        for k in ("SUPABASE_PROXY_URL","SUPABASE_PROXY_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        c = Config()
        assert c.has("supabase") is False

    def test_has_gha_when_configured(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "ghp_test")
        monkeypatch.setenv("GH_REPO", "user/repo")
        c = Config()
        assert c.has("gha") is True


# ---------- History ----------

class TestHistory:
    def test_remember_and_preferred(self, tmp_path):
        h = History(str(tmp_path / "h.json"))
        h.remember("https://example.com/page", "supabase", True, 1200)
        assert h.preferred_backend("https://example.com/other") == "supabase"  # same domain

    def test_preferred_none_on_failure(self, tmp_path):
        h = History(str(tmp_path / "h.json"))
        h.remember("https://example.com", "firecrawl", False, 5000)
        assert h.preferred_backend("https://example.com") is None

    def test_preferred_none_for_unknown_domain(self, tmp_path):
        h = History(str(tmp_path / "h.json"))
        assert h.preferred_backend("https://never-seen.com") is None

    def test_persists_across_instances(self, tmp_path):
        p = str(tmp_path / "h.json")
        h1 = History(p)
        h1.remember("https://a.com", "local", True, 100)
        h2 = History(p)
        assert h2.preferred_backend("https://a.com") == "local"

    def test_attempt_counts(self, tmp_path):
        h = History(str(tmp_path / "h.json"))
        h.remember("https://a.com", "local", True, 100)
        h.remember("https://a.com", "local", False, 200)
        h.remember("https://a.com", "local", True, 150)
        atts = h.data["a.com"]["attempts"]["local"]
        assert atts["ok"] == 2 and atts["fail"] == 1


# ---------- FetchResult ----------

class TestFetchResult:
    def test_ok_for_200_no_challenge(self):
        r = FetchResult(url="x", backend="local", status=200, body=b"ok")
        assert r.ok is True

    def test_not_ok_for_403(self):
        r = FetchResult(url="x", backend="local", status=403, body=b"forbidden")
        assert r.ok is False

    def test_not_ok_when_challenged(self):
        r = FetchResult(url="x", backend="local", status=200, body=b"just a moment", challenged=True)
        assert r.ok is False

    def test_not_ok_when_error(self):
        r = FetchResult(url="x", backend="local", status=None, error="timeout")
        assert r.ok is False

    def test_text_decoder(self):
        r = FetchResult(url="x", backend="local", body=b"hello")
        assert r.text == "hello"


# ---------- Router ladder selection ----------

class TestLadderSelection:
    def test_plain_ladder_order(self):
        assert _PLAIN_LADDER[0] == "local"
        assert "supabase" in _PLAIN_LADDER
        assert _PLAIN_LADDER[-1] == "gha"

    def test_render_ladder_starts_with_browser(self):
        assert _RENDER_LADDER[0] == "browser"

    def test_antibot_ladder_starts_with_zenrows(self):
        assert _ANTIBOT_LADDER[0] == "zenrows"

    def test_backend_kwargs_supabase_region(self):
        c = Config()
        kwargs = _backend_kwargs("supabase", "https://x.com", c, {"region":"eu-west-1","timeout":30})
        assert kwargs["region"] == "eu-west-1"
        assert kwargs["mode"] == "raw"

    def test_backend_kwargs_firecrawl_antibot_stealth(self):
        c = Config()
        kwargs = _backend_kwargs("firecrawl", "https://x.com", c, {"antibot":True,"render":True,"wait_ms":5000,"markdown":True,"timeout":60})
        assert kwargs["proxy"] == "stealth"
        assert kwargs["wait_for"] == 5000

    def test_backend_kwargs_zenrows_antibot_mode_auto(self):
        c = Config()
        kwargs = _backend_kwargs("zenrows", "https://x.com", c, {"antibot":True,"region":"US","markdown":True,"timeout":90})
        assert kwargs["mode"] == "auto"
        assert kwargs["proxy_country"] == "US"

    def test_backend_kwargs_gha_browser_mode_for_render(self):
        c = Config()
        kwargs = _backend_kwargs("gha", "https://x.com", c, {"render":True,"wait_ms":5000,"timeout":30})
        assert kwargs["mode"] == "browser"
        assert kwargs["wait_ms"] == 5000
        assert kwargs["timeout"] >= 60  # bumped for gha


# ---------- Router with mocked backends ----------

class TestRouterMocked:
    def _mock_backend(self, name, status, body, challenged=False):
        """Create a mock backend function returning a fixed FetchResult."""
        def fn(url, config, **kw):
            return FetchResult(url=url, backend=name, status=status, body=body.encode() if isinstance(body,str) else body,
                              elapsed_ms=100, challenged=challenged,
                              challenge_reason=("status "+str(status)) if challenged else None)
        return fn

    def test_local_succeeds_no_escalation(self, tmp_path, monkeypatch):
        # configure all backends
        for k,v in [("SUPABASE_PROXY_URL","x"),("SUPABASE_PROXY_TOKEN","x"),
                    ("NETLIFY_SCRAPER_URL","x"),("NETLIFY_TOKEN","x"),
                    ("FIRECRAWL_API_KEY","x"),("ZENROWS_API_KEY","x"),
                    ("GH_TOKEN","x"),("GH_REPO","x/x")]:
            monkeypatch.setenv(k, v)
        c = Config()
        c.history_path = str(tmp_path / "h.json")
        with patch("fetchkit.core.BACKENDS", {"local": self._mock_backend("local", 200, "ok")}):
            r = fetch("https://x.com", config=c)
        assert r.backend == "local"
        assert r.ok
        assert len(r.attempts) == 1

    def test_escalates_on_403(self, tmp_path, monkeypatch):
        for k,v in [("SUPABASE_PROXY_URL","x"),("SUPABASE_PROXY_TOKEN","x"),
                    ("NETLIFY_SCRAPER_URL","x"),("NETLIFY_TOKEN","x"),
                    ("FIRECRAWL_API_KEY","x"),("ZENROWS_API_KEY","x"),
                    ("GH_TOKEN","x"),("GH_REPO","x/x")]:
            monkeypatch.setenv(k, v)
        c = Config()
        c.history_path = str(tmp_path / "h.json")
        backends = {
            "local": self._mock_backend("local", 403, "forbidden", challenged=True),
            "supabase": self._mock_backend("supabase", 200, "ok"),
        }
        with patch("fetchkit.core.BACKENDS", backends):
            r = fetch("https://x.com", config=c)
        assert r.backend == "supabase"
        assert r.ok
        assert len(r.attempts) == 2
        assert r.attempts[0]["backend"] == "local"
        assert r.attempts[0]["challenged"] is True
        assert r.attempts[1]["backend"] == "supabase"

    def test_history_preferred_backend_tried_first(self, tmp_path, monkeypatch):
        for k,v in [("SUPABASE_PROXY_URL","x"),("SUPABASE_PROXY_TOKEN","x"),
                    ("NETLIFY_SCRAPER_URL","x"),("NETLIFY_TOKEN","x"),
                    ("FIRECRAWL_API_KEY","x"),("ZENROWS_API_KEY","x"),
                    ("GH_TOKEN","x"),("GH_REPO","x/x")]:
            monkeypatch.setenv(k, v)
        c = Config()
        c.history_path = str(tmp_path / "h.json")
        # pre-seed history: supabase succeeded for x.com
        h = History(c.history_path)
        h.remember("https://x.com", "supabase", True, 1000)
        calls = []
        def make(name, status, body):
            def fn(url, config, **kw):
                calls.append(name)
                return FetchResult(url=url, backend=name, status=status, body=body.encode(), elapsed_ms=100,
                                  challenged=(status in (403,429,503)))
            return fn
        backends = {"local": make("local", 200, "ok"),
                    "supabase": make("supabase", 200, "ok-supa"),
                    "netlify": make("netlify", 200, "ok-net")}
        with patch("fetchkit.core.BACKENDS", backends):
            r = fetch("https://x.com", config=c, history=h)
        # supabase should be tried first (from history)
        assert calls[0] == "supabase"
        assert r.backend == "supabase"

    def test_antibot_skips_local(self, tmp_path, monkeypatch):
        """--antibot should NOT try local/supabase/netlify (they can't pass WAF)."""
        for k,v in [("ZENROWS_API_KEY","x"),("FIRECRAWL_API_KEY","x"),("GH_TOKEN","x"),("GH_REPO","x/x")]:
            monkeypatch.setenv(k, v)
        # explicitly DON'T configure supabase/netlify to keep ladder short
        monkeypatch.delenv("SUPABASE_PROXY_URL", raising=False)
        monkeypatch.delenv("NETLIFY_SCRAPER_URL", raising=False)
        c = Config()
        c.history_path = str(tmp_path / "h.json")
        calls = []
        def make(name, status, body, challenged=False):
            def fn(url, config, **kw):
                calls.append(name)
                return FetchResult(url=url, backend=name, status=status, body=body.encode(), elapsed_ms=100, challenged=challenged)
            return fn
        backends = {"zenrows": make("zenrows", 200, "ok-zr"),
                    "firecrawl": make("firecrawl", 200, "ok-fc"),
                    "gha": make("gha", 200, "ok-gha")}
        with patch("fetchkit.core.BACKENDS", backends):
            r = fetch("https://x.com", antibot=True, config=c)
        assert r.backend == "zenrows"
        assert "local" not in calls
        assert "supabase" not in calls
