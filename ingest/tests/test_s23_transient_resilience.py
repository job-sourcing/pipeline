"""S23 pins — transient-429 resilience (the 2026-09-30 haidilao failure).

Root cause: a single workstream.us 429 on ONE detail page escaped the
documented best-effort contracts (enrich_new "unreachable detail still
yields an error-status record"; refetch_repost_details "an unreachable
detail leaves the event at 'label' confidence") and failed an entire
62-board watch leg.

Two-layer fix pinned here:
  1. base.fetch_text/fetch_json — bounded 429/503 retry with backoff
     (one round, 2 retries). Non-transient errors unchanged.
  2. gha_board_watch.detail_payload call sites — an exception surfaces
     as an error-status record / 'label'-confidence event, never as a
     crashed leg.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "ingest"))

from jobsearch.config import Config  # noqa: E402
from jobsearch.sources import base  # noqa: E402

# ── Layer 1: bounded transient retry in the fetch contract ─────────────────


class TestTransientRetry:
    def _cfg(self) -> Config:
        return Config()

    def _resp(self, status: int, text: str = "ok") -> Mock:
        m = Mock()
        m.status_code = status
        m.text = text
        m.json.return_value = {"ok": True}
        if status >= 400:
            import requests as _rq
            m.raise_for_status.side_effect = _rq.HTTPError(
                f"{status} Client Error", response=m)
        return m

    def test_429_then_200_succeeds_after_retry(self, monkeypatch):
        seq = [self._resp(429), self._resp(200, "recovered")]
        monkeypatch.setattr(base.requests, "request",
                            lambda *a, **k: seq.pop(0))
        monkeypatch.setattr(base.time, "sleep", lambda s: None)
        out = base.fetch_text("https://x/j/1", cfg=self._cfg())
        assert out == "recovered"
        assert len(seq) == 0

    def test_503_then_200_json_succeeds_after_retry(self, monkeypatch):
        seq = [self._resp(503), self._resp(200)]
        monkeypatch.setattr(base.requests, "request",
                            lambda *a, **k: seq.pop(0))
        monkeypatch.setattr(base.time, "sleep", lambda s: None)
        assert base.fetch_json("https://x/api", cfg=self._cfg()) == {"ok": True}

    def test_persistent_429_still_raises(self, monkeypatch):
        monkeypatch.setattr(base.requests, "request",
                            lambda *a, **k: self._resp(429))
        monkeypatch.setattr(base.time, "sleep", lambda s: None)
        with pytest.raises(Exception):
            base.fetch_text("https://x/j/1", cfg=self._cfg())

    def test_non_transient_404_raises_immediately(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            base.requests, "request",
            lambda *a, **k: calls.append(1) or self._resp(404))
        with pytest.raises(Exception):
            base.fetch_text("https://x/j/1", cfg=self._cfg())
        assert len(calls) == 1          # no retry round for non-transient

    def test_retry_budget_is_bounded(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            base.requests, "request",
            lambda *a, **k: calls.append(1) or self._resp(429))
        monkeypatch.setattr(base.time, "sleep", lambda s: None)
        with pytest.raises(Exception):
            base.fetch_text("https://x/j/1", cfg=self._cfg())
        assert len(calls) == 3          # initial + 2 retries, then stop

    def test_success_first_try_no_sleep(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(base.requests, "request",
                            lambda *a, **k: self._resp(200, "fine"))
        monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))
        assert base.fetch_text("https://x/j/1", cfg=self._cfg()) == "fine"
        assert sleeps == []             # fast path unchanged


# ── Layer 2: the watch driver's best-effort guards ─────────────────────────

_SCRIPT = REPO_ROOT / "scripts" / "gha_board_watch.py"
_spec = importlib.util.spec_from_file_location("gha_board_watch_s23", _SCRIPT)
watch = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("gha_board_watch_s23", watch)
_spec.loader.exec_module(watch)

from jobsearch.sources import site_boards  # noqa: E402


class TestEnrichNewDetailFetchGuard:
    def test_raising_detail_payload_yields_error_record(self, monkeypatch):
        """One 429 (or ANY exception) on a detail page must produce an
        error-status record with attempts+1 — not escape enrich_new."""
        def boom(*a, **k):
            raise RuntimeError("429 Client Error")
        monkeypatch.setattr(site_boards, "detail_payload", boom)
        cfg = Config()
        rows = [{"reqId": "R1", "title": "Host", "externalPath": "/R1",
                 "url": "https://x/R1", "locationsText": "Irvine, CA, USA",
                 "postedOn": "", "company": "Haidilao"}]
        out = watch.enrich_new(rows, "ats:workstream:haidilao",
                               "Haidilao", cfg, deadline=float("inf"))
        assert len(out) == 1
        rec = out[0]
        assert rec.get("error") == "detail_unreachable"
        assert rec.get("attempts") == 1
        assert rec.get("title") == "Host"      # the record survives intact


class TestRepostRefetchGuard:
    def test_raising_detail_payload_keeps_label_confidence(self, monkeypatch):
        """The documented contract: an unreachable detail leaves the event
        at 'label' confidence — a transient 429 must not crash the leg."""
        def boom(*a, **k):
            raise RuntimeError("429 Client Error")
        monkeypatch.setattr(site_boards, "detail_payload", boom)
        monkeypatch.setattr(watch.time, "sleep", lambda s: None)
        cfg = Config()
        flags = [{"reqId": "R9", "label": "haidilao_us_fulltime",
                  "externalPath": "/j/R9", "confidence": "label",
                  "prev_startDate": "2026-08-01",
                  "detected": "2026-09-30T00:00:00Z"}]
        events, updates = watch.refetch_repost_details(
            flags, "ats:workstream:haidilao", cfg, deadline=float("inf"))
        assert len(events) == 1
        assert events[0].get("confidence") == "label"   # unchanged
        assert "new_startDate" not in events[0]
        assert updates == {}
