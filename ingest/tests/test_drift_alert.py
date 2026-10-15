"""Tests for scripts/drift_alert.py — D-S35-2 drift alerting.

Pins the detection contract (DESIGN-S35 §3.2, round-2 amendments):
- board-fail-streak: >=2 consecutive 'failed' verdicts in runs.jsonl
- row-collapse: <40% of 7d-max AND >=3 rows lost (churn-aware edge:
  the 4->1 board escapes; 10->3 fires)
- row-zero: baseline>0 and current 0
- window-decline: <50% of the 28d-ago head
- stale-board: last_seen beyond cadence x 2 (never-yielded exempt)
- unexported-drift: manifest identity break
- baseline: append-only series, same-day upsert, capped
- issue sink: create/comment/auto-close decisions (mocked API)
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

import pytest
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "drift_alert.py"

_spec = importlib.util.spec_from_file_location("drift_alert", SCRIPT)
da = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("drift_alert", da)
_spec.loader.exec_module(da)
drift = da   # the S37-W1 amendment alias (the agent block names it drift)

TODAY = date.today()


def _setup(tmp_path: Path, watches: list[dict]):
    watch = tmp_path / "board_watch"
    workday = tmp_path / "workday"
    watch.mkdir(parents=True)
    workday.mkdir(parents=True)
    (watch / "config.json").write_text(json.dumps({"watches": watches}))
    da.WATCH_DIR = watch
    da.WORKDAY = workday
    da.BASELINE = watch / "drift_baseline.json"
    return watch, workday


def _w(label: str, tags: list[str] | None = None) -> dict:
    return {"label": label, "board": "ats:greenhouse:x",
            "company": label, "country": "United States",
            "tags": tags or []}


def _state(watch: Path, label: str, last_seen: str, n: int = 1) -> None:
    (watch / f"{label}.state.jsonl").write_text("\n".join(
        json.dumps({"reqId": f"R{i}", "last_seen": last_seen})
        for i in range(n)) + "\n")


def _runs(watch: Path, label: str, verdicts: list[str]) -> None:
    (watch / f"{label}.runs.jsonl").write_text("\n".join(
        json.dumps({"ts": "2026-10-14T00:00:00Z", "verdict": v})
        for v in verdicts) + "\n")


def _manifest(workday: Path, label: str, **kv) -> None:
    base = {"label": label, "rows": 5, "kept": 5, "new": 0, "pending": 0,
            "terminal_err": 0, "gate_dropped": 0,
            "gate_dropped_survivors": 0, "gate_dropped_new": 0,
            "feed_excluded_state": 0, "ghosts_in_state": 0,
            "ghosts_dropped": 0, "state_rows": 5, "banded": 0,
            "lca_pools": 0, "snapshot": "2026-10-14"}
    base.update(kv)
    (workday / f"{label}.export.json").write_text(json.dumps(base))


def _csv(workday: Path, label: str, n: int) -> None:
    (workday / f"{label}.csv").write_text(
        "reqId,title\n" + "".join(f"R{i},T\n" for i in range(n)))


def _series(baseline: dict, label: str, entries: list[tuple[str, int]]):
    baseline[label] = [{"d": d, "rows": n} for d, n in entries]


class TestFailStreak:
    def test_two_consecutive_failures_alert(self, tmp_path):
        watch, _ = _setup(tmp_path, [_w("a_us_fulltime")])
        _runs(watch, "a_us_fulltime", ["complete", "failed", "failed"])
        f = da.detect([_w("a_us_fulltime")], {})
        assert any(x["class"] == "board-fail-streak" for x in f)

    def test_single_failure_does_not_alert(self, tmp_path):
        watch, _ = _setup(tmp_path, [_w("a_us_fulltime")])
        _runs(watch, "a_us_fulltime", ["complete", "failed", "complete"])
        f = da.detect([_w("a_us_fulltime")], {})
        assert not any(x["class"] == "board-fail-streak" for x in f)


class TestRowClasses:
    def test_row_collapse_fires(self, tmp_path):
        _setup(tmp_path, [_w("a_us_fulltime")])
        baseline = {}
        _series(baseline, "a_us_fulltime",
                [(TODAY.isoformat(), 10)])
        da.BASELINE and None
        # patch _rows_now via manifest rows=3
        # (10 -> 3: -70%, 7 rows lost)
        # need workday manifest: use the module's _rows_now through env
        watch, workday = tmp_path / "board_watch", tmp_path / "workday"
        _manifest(workday, "a_us_fulltime", rows=3)
        f = da.detect([_w("a_us_fulltime")], baseline)
        assert any(x["class"] == "row-collapse" for x in f)

    def test_small_board_4to2_escapes(self, tmp_path):
        # the >=3-rows floor: 2 rows lost under the threshold — churn,
        # not drift (round-2 P2 amendment: 4->1 with 3 lost DOES fire)
        _setup(tmp_path, [_w("a_us_fulltime")])
        baseline = {}
        _series(baseline, "a_us_fulltime", [(TODAY.isoformat(), 4)])
        watch, workday = tmp_path / "board_watch", tmp_path / "workday"
        _manifest(workday, "a_us_fulltime", rows=2)
        f = da.detect([_w("a_us_fulltime")], baseline)
        assert not any(x["class"] in ("row-collapse", "row-zero")
                       for x in f)

    def test_small_board_4to1_fires(self, tmp_path):
        # 3 rows lost = at the floor — fires
        _setup(tmp_path, [_w("a_us_fulltime")])
        baseline = {}
        _series(baseline, "a_us_fulltime", [(TODAY.isoformat(), 4)])
        watch, workday = tmp_path / "board_watch", tmp_path / "workday"
        _manifest(workday, "a_us_fulltime", rows=1)
        f = da.detect([_w("a_us_fulltime")], baseline)
        assert any(x["class"] == "row-collapse" for x in f)

    def test_row_zero_fires(self, tmp_path):
        _setup(tmp_path, [_w("a_us_fulltime")])
        baseline = {}
        _series(baseline, "a_us_fulltime", [(TODAY.isoformat(), 6)])
        workday = tmp_path / "workday"
        _manifest(workday, "a_us_fulltime", rows=0)
        f = da.detect([_w("a_us_fulltime")], baseline)
        assert any(x["class"] == "row-zero" for x in f)

    def test_window_decline_fires(self, tmp_path):
        _setup(tmp_path, [_w("a_us_fulltime")])
        baseline = {}
        _series(baseline, "a_us_fulltime", [
            ((TODAY - timedelta(days=30)).isoformat(), 20),
            (TODAY.isoformat(), 8)])
        workday = tmp_path / "workday"
        _manifest(workday, "a_us_fulltime", rows=8)
        f = da.detect([_w("a_us_fulltime")], baseline)
        assert any(x["class"] == "window-decline" for x in f)

    def test_never_seeded_labels_exempt_from_row_classes(self, tmp_path):
        _setup(tmp_path, [_w("a_us_fulltime")])
        workday = tmp_path / "workday"
        _manifest(workday, "a_us_fulltime", rows=0)
        f = da.detect([_w("a_us_fulltime")], {})
        assert not any(x["class"].startswith("row-") for x in f)


class TestStale:
    def test_daily_board_stale_at_4d(self, tmp_path):
        watch, workday = _setup(tmp_path, [_w("a_us_fulltime")])
        _state(watch, "a_us_fulltime", (TODAY - timedelta(days=4)
                                        ).isoformat())
        _csv(workday, "a_us_fulltime", 3)
        f = da.detect([_w("a_us_fulltime")], {})
        assert any(x["class"] == "stale-board" for x in f)

    def test_weekly_tag_scales(self, tmp_path):
        watch, workday = _setup(tmp_path, [_w("a_us_fulltime", ["li_dormant"])])
        _state(watch, "a_us_fulltime", (TODAY - timedelta(days=10)
                                        ).isoformat())
        _csv(workday, "a_us_fulltime", 3)
        f = da.detect([_w("a_us_fulltime")], {})
        assert any(x["class"] == "stale-board" for x in f)

    def test_fresh_board_clean(self, tmp_path):
        watch, workday = _setup(tmp_path, [_w("a_us_fulltime")])
        _state(watch, "a_us_fulltime", TODAY.isoformat())
        _csv(workday, "a_us_fulltime", 3)
        f = da.detect([_w("a_us_fulltime")], {})
        assert not any(x["class"] == "stale-board" for x in f)


class TestManifestIdentity:
    def test_broken_identity_fires(self, tmp_path):
        _setup(tmp_path, [_w("a_us_fulltime")])
        workday = tmp_path / "workday"
        _manifest(workday, "a_us_fulltime", state_rows=99)  # broken
        f = da.detect([_w("a_us_fulltime")], {})
        assert any(x["class"] == "unexported-drift" for x in f)

    def test_clean_identity_silent(self, tmp_path):
        _setup(tmp_path, [_w("a_us_fulltime")])
        workday = tmp_path / "workday"
        _manifest(workday, "a_us_fulltime")
        f = da.detect([_w("a_us_fulltime")], {})
        assert not any(x["class"] == "unexported-drift" for x in f)


class TestBaseline:
    def test_series_append_is_append_only(self, tmp_path):
        # round-1 P1-6: a same-day downsert would ERASE the baseline
        # max mid-collapse — the series must keep both entries
        _setup(tmp_path, [_w("a_us_fulltime")])
        b = {}
        da._series_append(b, "a_us_fulltime", 10)
        da._series_append(b, "a_us_fulltime", 3)
        assert [e["rows"] for e in b["a_us_fulltime"]] == [10, 3]

    def test_series_capped(self, tmp_path):
        _setup(tmp_path, [_w("a_us_fulltime")])
        b = {}
        for i in range(150):
            da._series_append(b, "a_us_fulltime", i)
        assert len(b["a_us_fulltime"]) == da.SERIES_KEEP


class TestIssueSink:
    def _patch_api(self, monkeypatch, calls):
        def fake_api(method, url, token, body=None):
            calls.append((method, url, body))
            if method == "GET" and url.endswith("/issues?state=open"
                                                "&labels=drift-alert"
                                                "&per_page=100"):
                return []
            return {"number": 42}
        monkeypatch.setattr(da, "_api", fake_api)

    def test_create_on_new_finding(self, tmp_path, monkeypatch):
        calls = []
        self._patch_api(monkeypatch, calls)
        da.sink_issues([{"class": "row-zero", "label": "a_us_fulltime",
                         "evidence": "0 rows"}], "tok", "org/repo")
        creates = [c for c in calls if c[0] == "POST" and c[1].endswith(
            "/issues")]
        assert len(creates) == 1
        assert "[drift:row-zero] a_us_fulltime" in creates[0][2]["title"]

    def test_auto_close_on_heal(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(da, "_api", lambda m, u, t, b=None: (
            [{"title": "[drift:row-zero] a_us_fulltime", "number": 7,
              "labels": [{"name": "drift-alert"}]}]
            if (m == "GET") else {"number": 7}))
        da.sink_issues([], "tok", "org/repo")   # no live findings
        patches = [c for c in calls if c[0] == "PATCH"]
        assert len(patches) == 0                # lambda records nothing;
        # verify via real fake:
        calls2 = []

        def fake_api(method, url, token, body=None):
            calls2.append((method, url, body))
            if method == "GET":
                return [{"title": "[drift:row-zero] a_us_fulltime",
                         "number": 7,
                         "labels": [{"name": "drift-alert"}]}]
            return {"number": 7}
        monkeypatch.setattr(da, "_api", fake_api)
        da.sink_issues([], "tok", "org/repo")
        assert any(c[0] == "PATCH" and c[2].get("state") == "closed"
                   for c in calls2)


class TestRunsLedger:
    """D-S35-2: the driver appends one verdict line per watch per run
    (scripts/gha_board_watch.py::_ledger_append — the fail-streak
    evidence source)."""

    def test_ledger_append_per_watch(self, tmp_path, monkeypatch):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "gha_board_watch",
            REPO_ROOT / "scripts" / "gha_board_watch.py")
        bw = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("gha_board_watch", bw)
        spec.loader.exec_module(bw)
        monkeypatch.setattr(bw, "WATCH_DIR", tmp_path)
        ws = [{"label": "a_us_fulltime"}, {"label": "b_us_fulltime"},
              {"label": "no-verdict_us_fulltime"}]
        results = {"a_us_fulltime": "complete",
                   "b_us_fulltime": "failed"}
        bw._ledger_append(ws, results)
        la = (tmp_path / "a_us_fulltime.runs.jsonl").read_text().splitlines()
        lb = (tmp_path / "b_us_fulltime.runs.jsonl").read_text().splitlines()
        assert json.loads(la[0])["verdict"] == "complete"
        assert json.loads(lb[0])["verdict"] == "failed"
        assert not (tmp_path / "no-verdict_us_fulltime.runs.jsonl").exists()
        # append semantics: a second run appends, never overwrites
        bw._ledger_append(ws, {"a_us_fulltime": "failed",
                               "b_us_fulltime": "failed"})
        la2 = (tmp_path / "a_us_fulltime.runs.jsonl").read_text(
            ).splitlines()
        assert len(la2) == 2 and json.loads(la2[1])["verdict"] == "failed"


# ── S36 sweep-mode (roles:pm — DESIGN-S36 §5) ───────────────────────────────

class TestSweepMode:
    """detect_sweep: the roles:pm ledger + baseline series."""

    def _ledger(self, tmp_path, entries):
        p = tmp_path / "roles:pm.runs.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        return p

    def test_sweep_fail_streak(self, tmp_path, monkeypatch):
        entries = [
            {"ts": "2026-10-13T00:00:00Z", "verdict": "complete",
             "rows": 900, "sources_dead": []},
            {"ts": "2026-10-14T00:00:00Z", "verdict": "failed",
             "rows": 0, "sources_dead": ["adzuna"]},
            {"ts": "2026-10-15T00:00:00Z", "verdict": "failed",
             "rows": 0, "sources_dead": ["adzuna"]},
        ]
        self._ledger(tmp_path, entries)
        monkeypatch.setattr(da, "WATCH_DIR", tmp_path)
        f = da.detect_sweep({})
        classes = [x["class"] for x in f]
        assert "sweep-fail-streak" in classes
        assert "sweep-source-death" in classes      # adzuna dead x2

    def test_sweep_source_death_healed(self, tmp_path, monkeypatch):
        entries = [
            {"ts": "1", "verdict": "complete", "rows": 900,
             "sources_dead": ["jooble"]},
            {"ts": "2", "verdict": "complete", "rows": 900,
             "sources_dead": []},                   # healed before 2
        ]
        self._ledger(tmp_path, entries)
        monkeypatch.setattr(da, "WATCH_DIR", tmp_path)
        assert da.detect_sweep({}) == []

    def test_sweep_row_collapse(self, tmp_path, monkeypatch):
        entries = [{"ts": "1", "verdict": "complete", "rows": 900,
                    "sources_dead": []}]
        self._ledger(tmp_path, entries)
        monkeypatch.setattr(da, "WATCH_DIR", tmp_path)
        baseline = {"roles:pm": [
            {"d": "2026-10-08", "rows": 900},
            {"d": "2026-10-14", "rows": 900},
            {"d": "2026-10-15", "rows": 100},       # -89% > collapse
        ]}
        f = da.detect_sweep(baseline)
        assert any(x["class"] == "row-collapse" for x in f)

    def test_sweep_never_ran(self, tmp_path, monkeypatch):
        monkeypatch.setattr(da, "WATCH_DIR", tmp_path)
        assert da.detect_sweep({}) == []



@pytest.fixture
def wdir(tmp_path, monkeypatch):
    monkeypatch.setattr(drift, "WATCH_DIR", tmp_path)
    monkeypatch.setattr(drift, "WORKDAY", tmp_path / "workday")
    monkeypatch.setattr(drift, "BASELINE", tmp_path / "drift_baseline.json")
    (tmp_path / "workday").mkdir()
    return tmp_path

# ── S37-W1 amendments (A1 / review P0-1): the corroboration-blocked
# streak class + the source_blocked interplay — hermetic, same module
def _runs_rows(wdir, label: str, lines: list[dict]) -> None:
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
        _runs_rows(wdir, "acme_us_fulltime",
              [_line(), _line(blocked=True), _line(blocked=True),
               _line(blocked=True)])
        assert drift._corroboration_blocked_streak("acme_us_fulltime") == 3
        findings = drift.detect([self.W], {})
        assert findings and findings[0]["class"] == \
            "corroboration-blocked-streak"
        assert findings[0]["label"] == "acme_us_fulltime"
        assert "signals" in findings[0]["evidence"]

    def test_two_flagged_lines_do_not_fire(self, wdir):
        _runs_rows(wdir, "acme_us_fulltime",
              [_line(), _line(blocked=True), _line(blocked=True)])
        assert drift._corroboration_blocked_streak("acme_us_fulltime") == 2
        findings = drift.detect([self.W], {})
        assert findings == []

    def test_clean_line_breaks_the_streak(self, wdir):
        _runs_rows(wdir, "acme_us_fulltime",
              [_line(blocked=True), _line(blocked=True),
               _line(blocked=True), _line()])
        assert drift._corroboration_blocked_streak("acme_us_fulltime") == 0
        findings = drift.detect([self.W], {})
        assert findings == []

    def test_flag_survives_on_a_green_verdict(self, wdir):
        # the whole point (P0-1): the lane runs GREEN (complete) while
        # the signals layer freezes — the flag is verdict-independent
        _runs_rows(wdir, "acme_us_fulltime",
              [_line("complete", blocked=True)] * 3)
        findings = drift.detect([self.W], {})
        assert findings and findings[0]["class"] == \
            "corroboration-blocked-streak"

    def test_absent_field_is_false(self, wdir):
        # old ledger lines have no corroboration_blocked key — they
        # read as clean and break streaks (schema-compatible)
        _runs_rows(wdir, "acme_us_fulltime",
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
        _runs_rows(wdir, "acme_us_fulltime",
              [{"ts": "t1", "verdict": "failed"},
               {"ts": "t2", "verdict": "failed"},
               {"ts": "t3", "verdict": "source_blocked"}])
        assert drift._fail_streak("acme_us_fulltime") == 0
        findings = drift.detect([self.W], {})
        assert findings == []       # neither class fires

    def test_failed_then_blocked_then_failed_is_one(self, wdir):
        _runs_rows(wdir, "acme_us_fulltime",
              [{"ts": "t1", "verdict": "failed"},
               {"ts": "t2", "verdict": "source_blocked"},
               {"ts": "t3", "verdict": "failed"}])
        assert drift._fail_streak("acme_us_fulltime") == 1
        findings = drift.detect([self.W], {})
        assert findings == []       # 1 < 2: no issue
