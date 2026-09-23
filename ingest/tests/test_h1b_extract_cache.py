"""S14 run-#7 post-mortem: the per-employer xlsx re-download defect.

The workflow invokes h1b_extract.py once PER EMPLOYER (9 python
processes); each re-downloaded every quarterly xlsx (83-251MB) —
~54 downloads ≈ 54 min, past the 45-min timeout at employer #8
(run #7 cancelled mid-alibaba). _download_cached shares ONE download
per quarter across the invocations via /tmp on the runner."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "h1b_extract.py"

_spec = importlib.util.spec_from_file_location("h1b_extract", SCRIPT)
h1b = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("h1b_extract", h1b)
_spec.loader.exec_module(h1b)


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("H1B_CACHE_DIR", str(tmp_path / "c"))
    return tmp_path / "c"


def _fake_downloads(monkeypatch, calls, bodies):
    def fake(url, transport, cfg, timeout=600):
        calls.append(url)
        if url in bodies:
            return bodies[url], "impersonate"
        raise RuntimeError(f"unexpected url {url}")
    monkeypatch.setattr(h1b, "_download", fake)


class TestQuarterCache:
    def test_second_invocation_served_from_cache(self, cache_dir,
                                                 monkeypatch):
        calls = []
        _fake_downloads(monkeypatch, calls,
                        {h1b._file_url("FY2025_Q2"): b"XLSX-BYTES"})
        b1, v1 = h1b._download_cached(h1b._file_url("FY2025_Q2"),
                                      "auto", None)
        b2, v2 = h1b._download_cached(h1b._file_url("FY2025_Q2"),
                                      "auto", None)
        assert b1 == b2 == b"XLSX-BYTES"
        assert v1 == "impersonate" and v2 == "cache"
        assert len(calls) == 1          # ONE download, two consumers

    def test_alt_path_shares_the_cache_key(self, cache_dir, monkeypatch):
        # /sites/ and /media/ share the basename: a quarter cached via
        # the alt path answers the primary path with no 404 roundtrip
        calls = []
        _fake_downloads(monkeypatch, calls,
                        {h1b._file_url("FY2026_Q3", alt=True): b"ALT"})
        h1b._download_cached(h1b._file_url("FY2026_Q3", alt=True),
                             "auto", None)
        b, v = h1b._download_cached(h1b._file_url("FY2026_Q3"),
                                    "auto", None)
        assert b == b"ALT" and v == "cache"
        assert len(calls) == 1

    def test_stale_cache_bypassed(self, cache_dir, monkeypatch):
        calls = []
        _fake_downloads(monkeypatch, calls,
                        {h1b._file_url("FY2025_Q1"): b"FRESH"})
        h1b._download_cached(h1b._file_url("FY2025_Q1"), "auto", None)
        # age the cached file past the 6h bound (DOL appends in-quarter)
        body_p = cache_dir / "LCA_Disclosure_Data_FY2025_Q1.xlsx"
        old = time.time() - h1b._CACHE_MAX_AGE_S - 60
        import os
        os.utime(body_p, (old, old))
        b, v = h1b._download_cached(h1b._file_url("FY2025_Q1"),
                                    "auto", None)
        assert b == b"FRESH" and v == "impersonate"
        assert len(calls) == 2          # re-downloaded

    def test_corrupt_meta_refetches(self, cache_dir, monkeypatch):
        calls = []
        _fake_downloads(monkeypatch, calls,
                        {h1b._file_url("FY2025_Q1"): b"OK"})
        h1b._download_cached(h1b._file_url("FY2025_Q1"), "auto", None)
        (cache_dir / "LCA_Disclosure_Data_FY2025_Q1.xlsx.meta"
         ).write_text("{not json", encoding="utf-8")
        b, v = h1b._download_cached(h1b._file_url("FY2025_Q1"),
                                    "auto", None)
        assert b == b"OK" and v == "impersonate"
        assert len(calls) == 2

    def test_size_mismatch_refetches(self, cache_dir, monkeypatch):
        # a truncated body (the supabase trap class) must not poison
        # the cache: size vs meta verification
        calls = []
        _fake_downloads(monkeypatch, calls,
                        {h1b._file_url("FY2025_Q1"): b"FULL-BODY"})
        h1b._download_cached(h1b._file_url("FY2025_Q1"), "auto", None)
        body_p = cache_dir / "LCA_Disclosure_Data_FY2025_Q1.xlsx"
        body_p.write_bytes(b"FULL")    # corrupt/truncate the cache
        b, v = h1b._download_cached(h1b._file_url("FY2025_Q1"),
                                    "auto", None)
        assert b == b"FULL-BODY" and v == "impersonate"
        assert len(calls) == 2

    def test_download_error_leaves_no_cache_entry(self, cache_dir,
                                                  monkeypatch):
        calls = []

        def fake(url, transport, cfg, timeout=600):
            calls.append(url)
            raise RuntimeError("all transports failed")

        monkeypatch.setattr(h1b, "_download", fake)
        with pytest.raises(RuntimeError, match="transports"):
            h1b._download_cached(h1b._file_url("FY2025_Q1"), "auto",
                                 None)
        # failed download → no body/meta → nothing half-cached
        assert not list(cache_dir.glob("*.xlsx"))
        assert list(cache_dir.glob("*.part")) == []
