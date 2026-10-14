"""Tests for the RemoteAggSweepAdapter (D-S35-3, custom:rasweep:{key}).

Pins the S35 aggregator-layer contract:
- contracts DERIVED from LICOMPANY_CONTRACTS (same keys — the
  same-entity owner rule)
- EXACT normalized-variant matching (the round-1 P0-5 doctrine:
  "Engine" must NEVER match "TDengine")
- US/remote geo filter at list time (non-US-geo = noise for the
  US-remote mission — the A-prime rule at ingest)
- per-source fail-soft: a dead API is skipped; ALL dead raises
  (fail-loudly, state untouched)
- rid = sha1(title|company|loc|source) — stable across runs
- shared fetch cache: one fetch per RUN for all companies
- detail payload from the API row's own description (HTML stripped)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobsearch.sources import site_boards as sb  # noqa: E402


def _clear_cache():
    sb._RA_CACHE.clear()


def _patch_fetch(monkeypatch, responses: dict[str, str | Exception]):
    """responses: source name -> JSON text or exception to raise."""
    def fake_urlopen(req, timeout=45):
        class R:
            def __init__(self, body):
                self._body = body.encode() if isinstance(body, str) else b"{}"

            def read(self):
                if isinstance(self._body, Exception):
                    raise self._body
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        from urllib.parse import urlparse, parse_qs
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for name, u in sb._RA_SOURCES.items():
            if u.split("?")[0] == url.split("?")[0]:
                v = responses.get(name)
                if isinstance(v, Exception):
                    raise v
                return R(v if v is not None else "{}")
        return R("{}")
    monkeypatch.setattr(sb.urllib.request, "urlopen", fake_urlopen)


ROK_ROWS = [
    {"company": "Legalist", "position": "Analyst", "location": "Remote",
     "url": "https://x/1", "job_type": "full_time", "date": "2026-10-01",
     "description": "<b>Do things</b>"},
    {"company": "TDengine", "position": "Marketing Ops",
     "location": "Remote - US", "url": "https://x/2",
     "job_type": "full_time", "date": "2026-08-18",
     "description": "<p>Marketing</p>"},
    {"company": "Engine", "position": "Fake match", "location": "Remote",
     "url": "https://x/3", "job_type": "full_time",
     "date": "2026-10-01", "description": ""},
]
ROK_JSON = json.dumps(ROK_ROWS)


class TestContracts:
    def test_rasweep_key_mirrors_licompany(self):
        a = sb.RemoteAggSweepAdapter("vesync", None)
        assert a.company == "VeSync"
        assert "vesync" in a.variants

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match="rasweep: unknown contract"):
            sb.RemoteAggSweepAdapter("nope-not-real", None)


class TestMatching:
    def test_exact_variant_match_only(self, monkeypatch):
        # the P0-5 doctrine: "Engine" must NOT match TDengine
        _clear_cache()
        cfg = type("C", (), {"http_timeout_s": 30})()
        a = sb.RemoteAggSweepAdapter("tdengine", cfg)
        assert "tdengine" in a.variants
        _patch_fetch(monkeypatch, {"remoteok": ROK_JSON})
        rows, meta = a.list_board()
        titles = [r["title"] for r in rows.values()]
        assert "Marketing Ops" in titles
        assert "Fake match" not in titles
        assert meta["complete"] is True

    def test_non_us_geo_dropped(self, monkeypatch):
        _clear_cache()
        cfg = type("C", (), {"http_timeout_s": 30})()
        jobicy = {"jobs": [
            {"companyName": "OKX", "jobTitle": "Affiliate Mgr",
             "jobGeo": "APAC", "url": "https://x/9",
             "jobType": "Full-time", "pubDate": "2026-10-12",
             "jobDescription": "x"},
            {"companyName": "OKX", "jobTitle": "US Ops",
             "jobGeo": "Remote - US", "url": "https://x/10",
             "jobType": "Full-time", "pubDate": "2026-10-12",
             "jobDescription": "x"}]}
        _patch_fetch(monkeypatch, {"jobicy": json.dumps(jobicy)})
        a = sb.RemoteAggSweepAdapter("tdengine", cfg)
        # tdengine has no jobicy rows; use a fake licompany key with OKX?
        # OKX is not li-class — emulate via variants: monkeypatch
        monkeypatch.setattr(a, "variants", {"okx"})
        rows, meta = a.list_board()
        assert list(rows.values())[0]["title"] == "US Ops"
        assert meta["client_filtered"] == 1


class TestFailSoft:
    def test_one_dead_source_skipped(self, monkeypatch):
        _clear_cache()
        cfg = type("C", (), {"http_timeout_s": 30})()
        _patch_fetch(monkeypatch, {
            "remoteok": ROK_JSON,
            "jobicy": RuntimeError("dead"),
            "remotive": RuntimeError("dead"),
            "arbeitnow": RuntimeError("dead")})
        a = sb.RemoteAggSweepAdapter("tdengine", cfg)
        rows, _ = a.list_board()
        assert any(r["title"] == "Marketing Ops" for r in rows.values())

    def test_all_dead_raises(self, monkeypatch):
        _clear_cache()
        cfg = type("C", (), {"http_timeout_s": 30})()
        _patch_fetch(monkeypatch, {
            "remoteok": RuntimeError("dead"),
            "jobicy": RuntimeError("dead"),
            "remotive": RuntimeError("dead"),
            "arbeitnow": RuntimeError("dead")})
        a = sb.RemoteAggSweepAdapter("tdengine", cfg)
        with pytest.raises(RuntimeError, match="ALL aggregator sources"):
            a.list_board()


class TestRows:
    def test_rid_stable_and_detail(self, monkeypatch):
        _clear_cache()
        cfg = type("C", (), {"http_timeout_s": 30})()
        _patch_fetch(monkeypatch, {"remoteok": ROK_JSON})
        a = sb.RemoteAggSweepAdapter("tdengine", cfg)
        rows, _ = a.list_board()
        rid = next(k for k, r in rows.items()
                   if r["title"] == "Marketing Ops")
        det = a.detail_payload(f"/{rid}")
        assert det["jobPostingInfo"]["title"] == "Marketing Ops"
        assert "<" not in det["jobPostingInfo"]["jobDescription"]
        assert det["workMode"] == "Remote"

    def test_shared_cache_one_fetch_per_run(self, monkeypatch):
        _clear_cache()
        cfg = type("C", (), {"http_timeout_s": 30})()
        calls = []

        def fake_urlopen(req, timeout=45):
            class R:
                def read(self_inner):
                    return ROK_JSON.encode()

                def __enter__(self_inner):
                    calls.append(1)
                    return self_inner

                def __exit__(self_inner, *a):
                    return False
            return R()
        monkeypatch.setattr(sb.urllib.request, "urlopen", fake_urlopen)
        a1 = sb.RemoteAggSweepAdapter("tdengine", cfg)
        a2 = sb.RemoteAggSweepAdapter("vesync", cfg)
        a1.list_board()
        a2.list_board()
        a1.list_board()
        assert len(calls) == 4       # 4 sources, fetched ONCE total


class TestRegistration:
    def test_spec_parses(self):
        assert sb.is_site_spec("custom:rasweep:tdengine")
        kind, key = sb.parse_site("custom:rasweep:tdengine")
        assert kind == "rasweep" and key == "tdengine"

    def test_adapter_registered(self):
        assert sb._ADAPTERS.get("rasweep") is sb.RemoteAggSweepAdapter
