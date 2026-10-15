"""Tests for scripts/drift_alert.py — the durable drift auditor
(D-S35-2 + the S37-W1 amendments).

Covers:
  - CADENCE_DAYS (A12): the ra / gj_quarterly / gj_monthly entries —
    66 boards were wrong-by-accident at DEFAULT_CADENCE=1 (masked only
    by the no-CSV exemption; a future CSV emission would storm issues)
  - the corroboration-blocked-streak class (A1 / review P0-1): >=3
    consecutive runs.jsonl lines with corroboration_blocked=true fire
    the finding; 2 do not; a clean line breaks the streak; the class
    is verdict-independent (a green 'complete' line still counts when
    flagged — the lane runs green while the signals layer freezes)
  - the board-fail-streak mirror: a source_blocked verdict BREAKS a
    fail-streak (it is not a failure) and never fires the class itself

Hermetic: WATCH_DIR / WORKDAY / BASELINE monkeypatched to tmp_path —
no network, no issue writes (sink_issues is never exercised here).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # repo root
SCRIPT = REPO_ROOT / "scripts" / "drift_alert.py"

_spec = importlib.util.spec_from_file_location("drift_alert", SCRIPT)
drift = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("drift_alert", drift)
_spec.loader.exec_module(drift)


@pytest.fixture
def wdir(tmp_path, monkeypatch):
    monkeypatch.setattr(drift, "WATCH_DIR", tmp_path)
    monkeypatch.setattr(drift, "WORKDAY", tmp_path / "workday")
    monkeypatch.setattr(drift, "BASELINE", tmp_path / "drift_baseline.json")
    (tmp_path / "workday").mkdir()
    return tmp_path


def _runs(wdir, label: str, lines: list[dict]) -> None:
    p = wdir / f"{label}.runs.jsonl"
    p.write_text(
        "".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")


def _line(verdict: str = "complete", blocked: bool = False) -> dict:
    r = {"ts": "2026-10-14T06:45:00", "verdict": verdict}
    if blocked:
        r["corroboration_blocked"] = True
    return r


# ── A12: the cadence map ─────────────────────────────────────────────────
class TestCadenceMap:
    def test_the_three_missing_lanes_are_mapped(self):
        # A12 (review P2-5): ra (59 boards), gj_quarterly (5),
        # gj_monthly (2) fell to DEFAULT_CADENCE=1 — wrong-by-accident
        assert drift.CADENCE_DAYS["ra"] == 7
        assert drift.CADENCE_DAYS["gj_quarterly"] == 95
        assert drift.CADENCE_DAYS["gj_monthly"] == 35

    def test_untagged_daily_default_unchanged(self):
        assert drift.DEFAULT_CADENCE == 1

    def test_no_lane_tag_falls_back_to_daily(self):
        # the pre-existing lanes keep their pins (a regression on the
        # map would silently re-classify whole lanes)
        for tag, days in (("li", 1), ("weekly", 7), ("li_dormant", 7),
                          ("gj", 7), ("gj_dormant", 30), ("cf", 7),
                          ("monthly", 30)):
            assert drift.CADENCE_DAYS[tag] == days


# ── A1: the corroboration-blocked-streak class ───────────────────────────
class TestCorroborationBlockedStreak:
    """The enrich-phase outage marker: >=3 consecutive flagged ledger
    lines -> the issue sink. N=3 is the documented threshold (one
    blocked run can be a transient wall that the in-run retry heals;
    three daily runs with a frozen signals layer is a real outage)."""

    W = {"label": "acme_us_fulltime", "tags": [], "company": "Acme"}

    def test_three_consecutive_flagged_lines_fire(self, wdir):
        _runs(wdir, "acme_us_fulltime",
              [_line(), _line(blocked=True), _line(blocked=True),
               _line(blocked=True)])
        assert drift._corroboration_blocked_streak("acme_us_fulltime") == 3
        findings = drift.detect([self.W], {})
        assert findings and findings[0]["class"] == \
            "corroboration-blocked-streak"
        assert findings[0]["label"] == "acme_us_fulltime"
        assert "signals" in findings[0]["evidence"]

    def test_two_flagged_lines_do_not_fire(self, wdir):
        _runs(wdir, "acme_us_fulltime",
              [_line(), _line(blocked=True), _line(blocked=True)])
        assert drift._corroboration_blocked_streak("acme_us_fulltime") == 2
        findings = drift.detect([self.W], {})
        assert findings == []

    def test_clean_line_breaks_the_streak(self, wdir):
        _runs(wdir, "acme_us_fulltime",
              [_line(blocked=True), _line(blocked=True),
               _line(blocked=True), _line()])
        assert drift._corroboration_blocked_streak("acme_us_fulltime") == 0
        findings = drift.detect([self.W], {})
        assert findings == []

    def test_flag_survives_on_a_green_verdict(self, wdir):
        # the whole point (P0-1): the lane runs GREEN (complete) while
        # the signals layer freezes — the flag is verdict-independent
        _runs(wdir, "acme_us_fulltime",
              [_line("complete", blocked=True)] * 3)
        findings = drift.detect([self.W], {})
        assert findings and findings[0]["class"] == \
            "corroboration-blocked-streak"

    def test_absent_field_is_false(self, wdir):
        # old ledger lines have no corroboration_blocked key — they
        # read as clean and break streaks (schema-compatible)
        _runs(wdir, "acme_us_fulltime",
              [_line(blocked=True), _line(blocked=True),
               _line(blocked=True),
               {"ts": "2026-10-01T06:45:00", "verdict": "complete"}])
        assert drift._corroboration_blocked_streak("acme_us_fulltime") == 0

    def test_missing_runs_file_is_zero(self, wdir):
        assert drift._corroboration_blocked_streak("never_ran") == 0


class TestFailStreakInterplay:
    """A source_blocked verdict is NOT a failure (A3: green-but-
    degraded) — it must break a fail-streak, not extend one."""

    W = {"label": "acme_us_fulltime", "tags": [], "company": "Acme"}

    def test_source_blocked_breaks_fail_streak(self, wdir):
        _runs(wdir, "acme_us_fulltime",
              [{"ts": "t1", "verdict": "failed"},
               {"ts": "t2", "verdict": "failed"},
               {"ts": "t3", "verdict": "source_blocked"}])
        assert drift._fail_streak("acme_us_fulltime") == 0
        findings = drift.detect([self.W], {})
        assert findings == []       # neither class fires

    def test_failed_then_blocked_then_failed_is_one(self, wdir):
        _runs(wdir, "acme_us_fulltime",
              [{"ts": "t1", "verdict": "failed"},
               {"ts": "t2", "verdict": "source_blocked"},
               {"ts": "t3", "verdict": "failed"}])
        assert drift._fail_streak("acme_us_fulltime") == 1
        findings = drift.detect([self.W], {})
        assert findings == []       # 1 < 2: no issue
