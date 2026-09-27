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


class TestMultiMode:
    """S14 runs #7/#8 post-mortem: the single-pass multi-employer mode —
    ONE parse per quarter, every employer filter in the same stream."""

    def test_parse_multi_spec(self):
        pairs = h1b._parse_multi_spec(
            "a_label:NVIDIA;other:TRIP.COM,CTRIP;;  ")
        assert pairs == [("a_label", "NVIDIA"),
                         ("other", "TRIP.COM,CTRIP")]

    def test_parse_multi_spec_rejects_bad(self):
        with pytest.raises(ValueError, match="label:employer"):
            h1b._parse_multi_spec("nolabel")
        with pytest.raises(ValueError, match="needs"):
            h1b._parse_multi_spec("")

    def test_extract_rows_multi_one_parse_many_filters(self):
        # one body containing a 2-row sheet: each row lands in exactly
        # its label's bucket; both labels get the SAME total row count
        body = _xlsx([["CASE_NUMBER", "EMPLOYER_NAME", "WAGE_FROM"],
                      ["C1", "TikTok Inc.", 100],
                      ["C2", "ALIBABA GROUP", 200],
                      ["C3", "Netflix", 300]])
        pairs = [("tiktok_x", "TIKTOK"), ("ali_x", "ALIBABA")]
        out = h1b._extract_rows_multi(body, pairs, "FY2026_Q3")
        assert out["tiktok_x"][1] == 3 and out["ali_x"][1] == 3
        assert [r["caseNumber"] for r in out["tiktok_x"][0]] == ["C1"]
        assert [r["caseNumber"] for r in out["ali_x"][0]] == ["C2"]
        assert out["tiktok_x"][0][0]["sourceFile"] == "FY2026_Q3"

    def test_run_multi_writes_per_label_dedup(self, tmp_path,
                                              monkeypatch,
                                              cache_dir):
        monkeypatch.setattr(h1b, "_list_quarters",
                            lambda cfg: ["FY2025_Q1"])
        calls = []

        def fake_dl(url, transport, cfg, timeout=600):
            calls.append(url)
            return _xlsx([["CASE_NUMBER", "EMPLOYER_NAME", "WAGE_FROM"],
                          ["C1", "NVIDIA", 1],
                          ["C2", "CTRIP", 2]]), "impersonate"
        monkeypatch.setattr(h1b, "_download_cached", fake_dl)

        class A:
            multi = "nv:NVIDIA;tc:TRIP.COM,CTRIP"
            out_dir = str(tmp_path)
            transport = "auto"
            sleep = 0.0

        rc = h1b._run_multi(A(), None, ["FY2025_Q1"])
        assert rc == 0
        nv = (tmp_path / "nv.h1b_lca.jsonl").read_text(
            encoding="utf-8").strip().split("\n")
        tc = (tmp_path / "tc.h1b_lca.jsonl").read_text(
            encoding="utf-8").strip().split("\n")
        assert json.loads(nv[0])["caseNumber"] == "C1"
        assert json.loads(tc[0])["caseNumber"] == "C2"
        assert len(nv) == 1 and len(tc) == 1

    def test_run_multi_dedups_existing_case_numbers(self, tmp_path,
                                                    monkeypatch,
                                                    cache_dir):
        monkeypatch.setattr(h1b, "_list_quarters", lambda cfg: [])
        out = tmp_path / "nv.h1b_lca.jsonl"
        out.write_text(json.dumps({"caseNumber": "C1"}) + "\n",
                       encoding="utf-8")
        monkeypatch.setattr(
            h1b, "_download_cached",
            lambda url, transport, cfg, timeout=600:
            (_xlsx([["CASE_NUMBER", "EMPLOYER_NAME", "WAGE_FROM"],
                    ["C1", "NVIDIA", 1],
                    ["C9", "NVIDIA", 2]]), "cache"))

        class A:
            multi = "nv:NVIDIA"
            out_dir = str(tmp_path)
            transport = "auto"
            sleep = 0.0

        rc = h1b._run_multi(A(), None, ["FY2025_Q1"])
        assert rc == 0
        lines = [json.loads(x) for x in out.read_text(
            encoding="utf-8").strip().split("\n") if x.strip()]
        assert {r["caseNumber"] for r in lines} == {"C1", "C9"}


def _xlsx(rows: list[list]) -> bytes:
    """A minimal real xlsx (openpyxl-written) for the streaming reader."""
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
