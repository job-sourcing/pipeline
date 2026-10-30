"""S51 vendor-relay pins — the transport contract that keeps the pooled
vendor accounts off one direct IP.

Pins (mock-level, no network):
  1. base rotation: consecutive calls cycle NETLIFY_RELAY_URLS
  2. fake-success guard: wrapper-200 + inner fetch-failed rotates bases
  3. wrapper-401 = dead endpoint → next base, never a key verdict
  4. target status is an ANSWER (403 returned, no base rotation)
  5. truncated body → blob retrieval path
  6. fc_api strict mode: relay-down NEVER falls back to direct
  7. fc_api direct mode + unconfigured relay = the legacy path
  8. zenrows key rotation on 402 + the dead-set parking
  9. _envfile_vals multi-line FIRECRAWL_KEYS accumulation
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch import vendor_relay as vr  # noqa: E402


def _env(monkeypatch, **kv):
    for k in ("NETLIFY_RELAY_URLS", "NETLIFY_RELAY_TOKEN",
              "NETLIFY_RELAY_BLOBS_API", "NETLIFY_RELAY_ADMIN_TOKEN",
              "FC_RELAY_MODE", "FIRECRAWL_KEYS", "FIRECRAWL_API_KEY",
              "ZENROWS_KEYS", "ZENROWS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    # neutralize the env-FILE fallback (the tracked ingest/.env carries
    # the live relay config — tests must not inherit it)
    monkeypatch.setattr(vr, "_envfile_vals", lambda: {})
    vr._ENV_CACHE.clear()
    vr._ENV_CACHE.update(mt=0.0, vals={})
    for k, v in kv.items():
        monkeypatch.setenv(k, v)


def _wrapper(inner: dict, http_status: int = 200) -> bytes:
    return json.dumps({"batch_id": "x", "results": [inner]}).encode()


def _relay_server(monkeypatch, responses):
    """responses: list of (http_status, body_bytes) per urlopen call."""
    calls: list = []
    state = {"i": 0}

    class _Resp:
        def __init__(self, status, body):
            self.status = status
            self._b = body

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        idx = state["i"]
        state["i"] += 1
        calls.append(req.full_url)
        st, body = responses[min(idx, len(responses) - 1)]
        if st >= 400:
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, st, "x", {}, None)  # type: ignore[arg-type]
        return _Resp(st, body)

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


B = ["https://a.example", "https://b.example", "https://c.example"]


class TestRotation:
    def test_bases_rotate_per_call(self, monkeypatch):
        _env(monkeypatch, NETLIFY_RELAY_URLS=",".join(B),
             NETLIFY_RELAY_TOKEN="tok")
        calls = _relay_server(monkeypatch, [
            (200, _wrapper({"status": 200, "inline_body": "ok1"})),
            (200, _wrapper({"status": 200, "inline_body": "ok2"})),
            (200, _wrapper({"status": 200, "inline_body": "ok3"})),
            (200, _wrapper({"status": 200, "inline_body": "ok4"})),
        ])
        vr._CUR["i"] = 0
        r1 = vr.relay_fetch("https://target.example")
        r2 = vr.relay_fetch("https://target.example")
        r3 = vr.relay_fetch("https://target.example")
        r4 = vr.relay_fetch("https://target.example")   # wraps to base[0]
        assert r1["base"] == B[0] and r2["base"] == B[1]
        assert r3["base"] == B[2] and r4["base"] == B[0]
        assert r1["text"] == "ok1" and r4["text"] == "ok4"
        assert len(calls) == 4

    def test_unconfigured_relay_is_an_error_not_a_crash(self, monkeypatch):
        _env(monkeypatch)
        r = vr.relay_fetch("https://target.example")
        assert r["status"] is None and r["err"] == "relay-unconfigured"


class TestGuards:
    def test_fake_success_rotates_bases(self, monkeypatch):
        """wrapper-200 + inner fetch-failed = TRANSPORT death → the next
        base must carry the call (the round-3 P0 class)."""
        _env(monkeypatch, NETLIFY_RELAY_URLS=",".join(B),
             NETLIFY_RELAY_TOKEN="tok")
        calls = _relay_server(monkeypatch, [
            (200, _wrapper({"status": 0, "error": "fetch failed",
                            "inline_body": ""})),
            (200, _wrapper({"status": 200, "inline_body": "recovered"})),
        ])
        vr._CUR["i"] = 0
        r = vr.relay_fetch("https://target.example")
        assert r["status"] == 200 and r["text"] == "recovered"
        assert r["base"] == B[1]
        assert len(calls) == 2

    def test_wrapper_401_is_endpoint_death_not_key_death(self, monkeypatch):
        """A dead deploy (wrapper 401/403/404) rotates BASES; it must
        never surface as a vendor-key verdict (poisoned-key class)."""
        _env(monkeypatch, NETLIFY_RELAY_URLS=",".join(B),
             NETLIFY_RELAY_TOKEN="tok")
        import urllib.error
        calls = _relay_server(monkeypatch, [
            (403, b"forbidden"),   # base 0 dead
            (200, _wrapper({"status": 200, "inline_body": "ok"})),
        ])
        vr._CUR["i"] = 0
        r = vr.relay_fetch("https://target.example")
        assert r["status"] == 200 and r["text"] == "ok"
        assert r["base"] == B[1]

    def test_target_status_is_an_answer(self, monkeypatch):
        """A real target 403 returns verbatim — NO base rotation (the
        caller's key logic decides what it means)."""
        _env(monkeypatch, NETLIFY_RELAY_URLS=",".join(B),
             NETLIFY_RELAY_TOKEN="tok")
        calls = _relay_server(monkeypatch, [
            (200, _wrapper({"status": 403, "inline_body": "blocked",
                            "error": None})),
        ])
        vr._CUR["i"] = 0
        r = vr.relay_fetch("https://target.example")
        assert r["status"] == 403 and r["text"] == "blocked"
        assert len(calls) == 1

    def test_truncated_body_fetches_blob(self, monkeypatch):
        _env(monkeypatch, NETLIFY_RELAY_URLS=",".join(B[:1]),
             NETLIFY_RELAY_TOKEN="tok",
             NETLIFY_RELAY_BLOBS_API="https://blobs.example/x",
             NETLIFY_RELAY_ADMIN_TOKEN="adm")
        blob_calls: list = []

        def fake_blob(blob_key, timeout):
            blob_calls.append(blob_key)
            return b'{"full": "body"}'

        monkeypatch.setattr(vr, "_blob_fetch", fake_blob)
        _relay_server(monkeypatch, [
            (200, _wrapper({"status": 200, "inline_body": "pre",
                            "inline_body_truncated": True,
                            "blob_key": "k1"})),
        ])
        r = vr.relay_fetch("https://target.example")
        assert r["text"] == '{"full": "body"}'
        assert blob_calls == ["k1"]


class TestFcApi:
    def test_relay_down_never_falls_back_to_direct(self, monkeypatch):
        """The owner IP-hygiene directive as a pin: with the relay
        configured, a dead relay = an honest failure — never a silent
        direct call exposing the caller's IP."""
        _env(monkeypatch, NETLIFY_RELAY_URLS=",".join(B),
             NETLIFY_RELAY_TOKEN="tok")
        _relay_server(monkeypatch, [
            (200, _wrapper({"status": 0, "error": "fetch failed"})),
        ] * 3)
        vr._CUR["i"] = 0
        d, st = vr.fc_api("/v2/scrape", {"url": "x"}, "fc-key")
        assert d is None and st.startswith("relay:")

    def test_direct_mode_uses_the_legacy_path(self, monkeypatch):
        _env(monkeypatch, FC_RELAY_MODE="direct")
        seen: list = []

        def fake_direct(url, payload, key, timeout):
            seen.append(url)
            return {"success": True, "data": {}}, "ok"

        monkeypatch.setattr(vr, "_direct_post_json", fake_direct)
        d, st = vr.fc_api("/v2/scrape", {"url": "x"}, "fc-key")
        assert st == "ok" and seen == ["https://api.firecrawl.dev/v2/scrape"]

    def test_unconfigured_relay_is_direct(self, monkeypatch):
        _env(monkeypatch)   # no NETLIFY_RELAY_*
        monkeypatch.setattr(
            vr, "_direct_post_json",
            lambda url, payload, key, timeout: ({"success": True}, "ok"))
        d, st = vr.fc_api("/v2/scrape", {"url": "x"}, "fc-key")
        assert st == "ok"


class TestZenrows:
    def test_key_rotation_on_402(self, monkeypatch):
        _env(monkeypatch, NETLIFY_RELAY_URLS=",".join(B[:1]),
             NETLIFY_RELAY_TOKEN="tok",
             ZENROWS_KEYS="zr1,zr2,zr3")
        vr._ZR_STATE.update(idx=0, dead=set())
        _relay_server(monkeypatch, [
            (200, _wrapper({"status": 402, "inline_body": '{"error": 402}'})),
            (200, _wrapper({"status": 200, "inline_body": '{"rows": 1}'})),
        ])
        parsed, st = vr.zenrows_fetch({"url": "https://t.example"})
        assert st == "ok" and parsed == {"rows": 1}
        assert "zr1" in vr._ZR_STATE["dead"]   # parked, not retried

    def test_no_keys_is_honest(self, monkeypatch):
        _env(monkeypatch)
        vr._ZR_STATE.update(idx=0, dead=set())
        parsed, st = vr.zenrows_fetch({"url": "https://t.example"})
        assert parsed is None and st == "no-key"


class TestEnvfile:
    def test_multiline_keys_accumulate(self, tmp_path, monkeypatch):
        (tmp_path / "ingest").mkdir()
        (tmp_path / "ingest" / ".env").write_text(
            "FIRECRAWL_KEYS=fc-a,fc-b\n"
            "# comment\n"
            "FIRECRAWL_KEYS=fc-c\n"
            "OTHER=x\n")
        monkeypatch.setattr(vr, "_REPO", tmp_path)
        vr._ENV_CACHE.clear()
        vr._ENV_CACHE.update(mt=0.0, vals={})
        vals = vr._envfile_vals()
        assert vals["FIRECRAWL_KEYS"] == "fc-a,fc-b,fc-c"
        assert vals["OTHER"] == "x"

    def test_zenrows_keys_union_single(self, monkeypatch):
        _env(monkeypatch, ZENROWS_KEYS="zr1,zr2", ZENROWS_API_KEY="zr3")
        assert vr.zenrows_keys() == ["zr1", "zr2", "zr3"]
