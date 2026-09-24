"""Tests for scripts/gha_board_watch.py — the durable board-watch layer.

Covers (per design-board-v2.md D4 + review addenda):
- B1: partial list never marks postings gone; empty-board anomaly guard
- state machine: new/gone diff, compact rewrite preserving first_seen,
  last_seen= today; gone rows dropped from state
- enrich_new bounds (DETAILS_MAX) + detail_unreachable recorded honestly
  (DIRECT tests: TestEnrichNew — audit S7-B3 A2 found these were only
  ever monkeypatched away, with the docstring claiming coverage)
- corroborate_new: title-key match, reqId-exact override, blocked → {}
  (DIRECT tests: TestCorroborateNew — same hole)
- S9-audit fix wave (D2/D1): backlog recovery feeds CURRENT list rows
  through the REAL enrich_new (P1), first_seen preserved on retries (P2),
  C5 fallback pins — hits iteration, foreign-reqId window parity,
  pause-on-every-search, provenance stamp, budget-cell wiring
- budget guards: legs counter date-keyed reset; backlog verdict only
  while unenriched remain AND legs remain
- digest shape: NEW lines w/ applicants, GONE lines, header counts
- seed_state: B3 seeding + refuses to clobber an existing state
- S8-E2 repost/days-on-market detector (audit findings-recency.md §7/§8):
  postedOn parser buckets, Pacific run-date rule, R1 label-regression
  FP guards (aging vs 30→30+ vs 30+→small, +1d tolerance, unparseable),
  startDate-move channel, R2 bounded re-fetch (cap 5/leg + budget stop),
  reposts.jsonl event-log record shape, state schema round-trip with
  old-format rows, REPOSTED digest section + alerts on repost-only days.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # repo root
SCRIPT = REPO_ROOT / "scripts" / "gha_board_watch.py"

_spec = importlib.util.spec_from_file_location("gha_board_watch", SCRIPT)
watch = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("gha_board_watch", watch)
_spec.loader.exec_module(watch)

sys.path.insert(0, str(REPO_ROOT / "ingest"))

from jobsearch.config import Config  # noqa: E402
from jobsearch.sources import site_boards  # noqa: E402


CFG = {"label": "test_watch", "board": "nvidia|wd5|nvidiaexternalcareersite",
       "company": "NVIDIA", "country": "United States",
       "time_type": "Full time"}


def _post(rid: str, title: str = "Engineer") -> dict:
    return {"reqId": rid, "bulletFields": [rid], "title": title,
            "externalPath": f"/job/{rid}", "url": f"https://x/{rid}",
            "locationsText": "US, CA, Santa Clara",
            "postedOn": "Posted Today"}


def _page_payload(posts: list[dict], total: int) -> dict:
    return {"total": total, "jobPostings": posts}


@pytest.fixture
def wdir(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "WATCH_DIR", tmp_path)
    monkeypatch.setattr(watch, "LIST_SLEEP", 0.0)
    monkeypatch.setattr(watch, "DETAIL_SLEEP", 0.0)
    return tmp_path


# ── current_postings ─────────────────────────────────────────────────────
class TestCurrentPostings:
    def test_full_pagination_dedup_wrap(self, monkeypatch):
        def fake_page(board, facets, offset, cfg):
            # offset-keyed (the facet-discovery call also passes 0 —
            # must return the same first page both times)
            if offset == 0:
                return _page_payload([_post(f"JR{i}") for i in range(10)],
                                     25)
            if offset == 10:
                return _page_payload(
                    [_post(f"JR{i}") for i in range(10, 20)], 25)
            if offset == 20:
                # wrap-past-total quirk: duplicates near the end
                return _page_payload(
                    [_post("JR0"), _post("JR20"), _post("JR21"),
                     _post("JR22"), _post("JR23"), _post("JR24")], 25)
            return _page_payload([], 25)

        monkeypatch.setattr(watch.workday, "_page", fake_page)
        monkeypatch.setattr(watch.workday, "_facet_id",
                            lambda p, param, label: "f1")
        rows, complete, _cc = watch.current_postings(
            "nvidia|wd5|site", "United States", "Full time", Config())
        assert len(rows) == 25          # 10 + 10 + 5 unique (wrap deduped)
        assert complete is True
        assert rows["JR0"]["reqId"] == "JR0"

    def test_partial_list_incomplete(self, monkeypatch):
        def fake_page(board, facets, offset, cfg):
            if offset == 0:
                return _page_payload([_post(f"JR{i}") for i in range(10)],
                                     30)
            raise RuntimeError("network died mid-list")

        monkeypatch.setattr(watch.workday, "_page", fake_page)
        monkeypatch.setattr(watch.workday, "_facet_id",
                            lambda p, param, label: "f1")
        rows, complete, _cc = watch.current_postings(
            "nvidia|wd5|site", "United States", "Full time", Config())
        assert len(rows) == 10
        assert complete is False        # B1: partial ≠ complete

    def test_first_page_failure_raises(self, monkeypatch):
        def fake_page(board, facets, offset, cfg):
            raise RuntimeError("total outage")
        monkeypatch.setattr(watch.workday, "_page", fake_page)
        monkeypatch.setattr(watch.workday, "_facet_id",
                            lambda p, param, label: "f1")
        with pytest.raises(RuntimeError):
            watch.current_postings(
                "nvidia|wd5|site", "United States", "Full time", Config())

    def test_empty_board_is_complete(self, monkeypatch):
        monkeypatch.setattr(watch.workday, "_page",
                            lambda b, f, o, c: _page_payload([], 0))
        monkeypatch.setattr(watch.workday, "_facet_id",
                            lambda p, param, label: "f1")
        rows, complete, _cc = watch.current_postings(
            "nvidia|wd5|site", "United States", "Full time", Config())
        assert rows == {} and complete is True


# ── enrich_new (DIRECT — audit S7-B3 A2) ─────────────────────────────────
class TestEnrichNew:
    """enrich_new had ZERO direct tests — every run_watch test
    monkeypatched it away, and the module docstring claimed coverage.
    That hole shipped the cfg=None crash class (mocked-away network seams
    hide signature regressions). Here the network is mocked at ONE seam
    (watch.workday.detail_payload — exactly what a real payload looks
    like); parse_board, html_to_text, record shape, budget, bounds all
    run for real."""

    @staticmethod
    def _payload(rid: str = "JR2023969") -> dict:
        """REAL jobPostingInfo shape, field-for-field from
        ingest/data/workday/nvidia_us_fulltime.details.jsonl line 1
        (Staff SRE — JR2023969), incl. the entity-encoded HTML
        description and additionalLocations (509/1360 real rows have
        them; the fixture family in this file never did)."""
        return {
            "jobPostingInfo": {
                "id": "72824c976bd0102f17d84d7c62800000",
                "title": ("Staff Site Reliability Engineer - AI "
                          "Platform Runtime"),
                "jobDescription": (
                    "<p><span>SRE&#39;s mission: keep production systems "
                    "up.</span></p><ul><li><p><span>Lead reliability "
                    "initiatives.</span></p></li><li><p><span>Own "
                    "observability.</span></p></li></ul>"),
                "location": "US, CA, Santa Clara",
                "additionalLocations": ["US, CA, Remote"],
                "postedOn": "Posted Yesterday",
                "startDate": "2026-09-08",
                "timeType": "Full time",
                "jobReqId": rid,
                "jobPostingId": f"Staff-SRE_{rid}",
                "jobPostingSiteId": "NVIDIAExternalCareerSite",
                "country": {"descriptor": "United States of America",
                            "id": "bc33aa3152ec42d4995f4791a106ed09"},
                "canApply": True, "posted": True,
                "includeResumeParsing": True,
                "jobRequisitionLocation": {
                    "descriptor": "US, CA, Santa Clara",
                    "country": {"descriptor":
                                "United States of America"}},
                "externalUrl": (
                    "https://nvidia.wd5.myworkdayjobs.com/"
                    "NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/"
                    f"Staff-SRE_{rid}"),
                "questionnaireId": "2f2764b3a82910064296831695150000",
            },
            "hiringOrganization": {"name": "2100 NVIDIA USA"},
            "similarJobs": [{"title": "a"}, {"title": "b"}],
        }

    def test_record_shape_and_cfg_threading(self, wdir, monkeypatch):
        cfg = Config()
        calls: list = []

        def fake_detail(board, path, cfg_arg):
            calls.append((board, path, cfg_arg))
            return self._payload()

        monkeypatch.setattr(watch.workday, "detail_payload", fake_detail)
        out = watch.enrich_new([_post("JR2023969")], "nvidia|wd5|x",
                               "NVIDIA", cfg, time.monotonic() + 60)
        assert len(out) == 1
        # cfg threaded verbatim to the network seam — the cfg=None crash
        # shipped green precisely because no test pinned this
        assert calls == [(("nvidia", "wd5", "x"), "/job/JR2023969", cfg)]
        rec = out[0]
        # locations = [location] + additionalLocations
        assert rec["locations"] == ["US, CA, Santa Clara", "US, CA, Remote"]
        assert rec["startDate"] == "2026-09-08"          # passthrough
        assert rec["timeType"] == "Full time"
        assert rec["hiringOrg"] == "2100 NVIDIA USA"
        assert rec["externalUrl"].endswith("Staff-SRE_JR2023969")
        # description = html_to_text(jobDescription): entities unescaped,
        # bullets rendered, no tags. NOTE the "•\n" bullet shape — real
        # Workday payloads nest <p> inside <li> (see the committed
        # nvidia_us_fulltime.csv descriptions; audit S7-A1 0B observed
        # this exact shape) — do NOT "fix" this to "• text".
        assert "SRE's mission" in rec["description"]
        assert "•\nLead reliability initiatives." in rec["description"]
        assert "•\nOwn observability." in rec["description"]
        assert "<" not in rec["description"]
        # durable feed fields always present, no error
        assert rec["reqId"] == "JR2023969"
        assert rec["company"] == "NVIDIA"
        assert "error" not in rec

    def test_detail_unreachable_is_an_honest_error_record(self, wdir,
                                                          monkeypatch):
        monkeypatch.setattr(watch.workday, "detail_payload",
                            lambda b, p, c: None)
        out = watch.enrich_new([_post("JR1")], "nvidia|wd5|x", "NVIDIA",
                               Config(), time.monotonic() + 60)
        assert len(out) == 1
        rec = out[0]
        assert rec["error"] == "detail_unreachable"
        assert "locations" not in rec and "description" not in rec
        assert rec["reqId"] == "JR1"   # the record itself stays durable

    def test_retry_preserves_first_seen_and_increments_attempts(
            self, wdir, monkeypatch):
        """S9-audit D2 P2: the retry record used to reset first_seen to
        TODAY — the durable feed's tenure field drifted later with every
        failed retry (25/25 live error records wrong; consumers read the
        LAST line per reqId). Keep the prior record's first sighting;
        today is only for genuinely-new reqIds."""
        monkeypatch.setattr(watch.workday, "detail_payload",
                            lambda b, p, c: None)   # still unreachable
        prior_feed = {"JR1": {"reqId": "JR1",
                              "first_seen": "2026-09-12",
                              "error": "detail_unreachable",
                              "attempts": 2}}
        out = watch.enrich_new([_post("JR1"), _post("JR2")],
                               "nvidia|wd5|x", "NVIDIA", Config(),
                               time.monotonic() + 60,
                               prior_feed=prior_feed)
        by_rid = {r["reqId"]: r for r in out}
        # the retried reqId keeps its original sighting date + 3rd strike
        assert by_rid["JR1"]["first_seen"] == "2026-09-12"
        assert by_rid["JR1"]["attempts"] == 3
        # a genuinely-new reqId still gets today, attempts restart at 1
        # (midnight-crossing safe: accept the pre- or post-run date)
        _t0 = date.today().isoformat()
        assert by_rid["JR2"]["first_seen"] in {
            _t0, (date.fromisoformat(_t0) + timedelta(days=1)).isoformat()}
        assert by_rid["JR2"]["attempts"] == 1

    def test_bounded_at_details_max(self, wdir, monkeypatch):
        monkeypatch.setattr(watch, "DETAILS_MAX", 2)
        calls: list = []

        def fake_detail(board, path, cfg):
            calls.append(path)
            return self._payload()

        monkeypatch.setattr(watch.workday, "detail_payload", fake_detail)
        out = watch.enrich_new([_post(f"JR{i}") for i in range(1, 6)],
                               "nvidia|wd5|x", "NVIDIA", Config(),
                               time.monotonic() + 60)
        assert [r["reqId"] for r in out] == ["JR1", "JR2"]
        assert calls == ["/job/JR1", "/job/JR2"]   # rows past the cap
        # are never fetched (not fetched-then-dropped)

    def test_budget_deadline_stops_after_current_record(self, wdir,
                                                        monkeypatch):
        """Deadline already past → the in-flight record is still appended
        (checkpoint semantics), then the loop breaks — partial, never
        silent loss."""
        monkeypatch.setattr(watch.workday, "detail_payload",
                            lambda b, p, c: self._payload())
        out = watch.enrich_new([_post("JR1"), _post("JR2")],
                               "nvidia|wd5|x", "NVIDIA", Config(),
                               time.monotonic() - 1)
        assert len(out) == 1
        assert out[0]["reqId"] == "JR1"


# ── corroborate_new (DIRECT — audit S7-B3 A2) ────────────────────────────
def _li_card(cid, title="Solutions Architect, Infrastructure",
             company="NVIDIA", date="2026-09-05") -> dict:
    """REAL index-card shape from nvidia_us_fulltime.li_index.jsonl
    line 1."""
    return {"id": str(cid), "title": title, "company": company,
            "location": "Santa Clara, CA", "date": date,
            "url": f"https://www.linkedin.com/jobs/view/{cid}"}


def _sig(cid, title="Solutions Architect, Infrastructure", req_id="",
         applicants=95, status="matched") -> dict:
    """REAL fetch_signals record shape from nvidia_us_fulltime
    .signals.jsonl line 1 (the provider's 13-key contract)."""
    return {"linkedin_job_id": str(cid),
            "linkedin_url": f"https://www.linkedin.com/jobs/view/{cid}",
            "title": title, "company": "NVIDIA",
            "location": "Santa Clara, CA",
            "linkedin_posted_date": "2026-09-05",
            "num_applicants": applicants,
            "applicants_label": f"{applicants} applicants",
            "job_req_id": req_id, "posted_time_ago": "4 days ago",
            "closed": False, "status": status,
            "fetched_at": "2026-09-10T01:51:44"}


class _FakeProvider:
    """Stands in at the corroborate.get_provider seam; records the
    kwargs corroborate_new actually sends (kwarg-shape regressions are
    the other thing mocked-away tests can't see). S9 C5: also fakes
    search_title (ts_hits for every search, or ts_map keyed by title →
    (hits, exc) for per-row scenarios) so the title-search fallback is
    exercisable from this file."""

    name = "linkedin"

    def __init__(self, cards, sigs, index_exc=None,
                 ts_hits=None, ts_exc=None, ts_map=None):
        self.cards, self.sigs, self.index_exc = cards, sigs, index_exc
        self.index_calls: list[dict] = []
        self.fetch_calls: list[list] = []
        self.ts_hits = ts_hits or []
        self.ts_exc = ts_exc
        self.ts_map = ts_map or {}
        self.ts_calls: list[tuple] = []

    def index_cards(self, *, company, location, max_pages, max_cards,
                    start_offset=0):
        self.index_calls.append({"company": company, "location": location,
                                 "max_pages": max_pages,
                                 "max_cards": max_cards,
                                 "start_offset": start_offset})
        if self.index_exc:
            raise self.index_exc
        return self.cards, 100, False

    def fetch_signals(self, cards, on_record=None):
        self.fetch_calls.append([c["id"] for c in cards])
        return [self.sigs[c["id"]] for c in cards if c["id"] in self.sigs]

    def search_title(self, title, location, known_ids, company="NVIDIA"):
        self.ts_calls.append((title, location, set(known_ids)))
        if title in self.ts_map:
            hits, exc = self.ts_map[title]
            return list(hits), exc
        if self.ts_exc is not None:
            return [], self.ts_exc
        return list(self.ts_hits), None


class TestCorroborateNew:
    """corroborate_new had ZERO direct tests (always stubbed). Real code
    under test: get_provider seam + kwargs, REAL linkedin_guest.job_key
    normalization, reqId-exact override, blocked-index no-raise, matched
    -only signal use."""

    def _run(self, monkeypatch, provider, rows, cfg=None,
             deadline=None, budget=None):
        got: dict = {}

        def fake_get(name, cfg=None):
            got["name"], got["cfg"] = name, cfg
            return provider

        monkeypatch.setattr(watch.corroborate, "get_provider",
                            fake_get)
        import time as _t
        cfg = cfg or Config()
        kw: dict = {}
        if budget is not None:            # S9 C5 fallback cell (opt-in)
            kw["title_search_budget"] = budget
        return (watch.corroborate_new(
            rows, "NVIDIA", cfg,
            deadline if deadline is not None else _t.monotonic() + 60,
            **kw),
            got, cfg)

    def test_cfg_passed_through_to_get_provider(self, monkeypatch):
        """S7-A2 N regression: cfg must reach the provider constructor —
        the watch used to pass None and only defaults saved it."""
        provider = _FakeProvider([], {})
        out, got, cfg = self._run(monkeypatch, provider, [_post("JR1")])
        assert got["name"] == "linkedin"
        assert got["cfg"] is cfg
        assert out == {}

    def test_title_key_match_via_real_job_key(self, monkeypatch):
        # card "Senior Engineer - DGX Cloud" vs posting "Senior Engineer":
        # job_key strips " - …" → one key → match. Totally Different: no.
        cards = [_li_card(1, title="Senior Engineer - DGX Cloud"),
                 _li_card(2, title="AI Hardware Architect")]
        provider = _FakeProvider(cards, {"1": _sig(1, req_id="JR1")})
        rows = [_post("JR1", title="Senior Engineer"),
                _post("JR9", title="Totally Different")]
        out, _, _ = self._run(monkeypatch, provider, rows)
        assert set(out) == {"JR1"}
        assert out["JR1"]["num_applicants"] == 95
        # index queried for the company at the US location, from offset 0
        assert provider.index_calls == [{
            "company": "NVIDIA", "location": "United States",
            "max_pages": watch.LI_INDEX_PAGES,
            "max_cards": watch.LI_INDEX_PAGES * 10,
            "start_offset": 0}]
        # fetch_signals received exactly the matched cards
        assert provider.fetch_calls == [["1"]]

    def test_foreign_reqid_serves_nobody(self, monkeypatch):
        """S7-V1 P3-2: a signal whose reqId is NOT in this batch is a
        DIFFERENT requisition that shares the title family — it must not
        misattribute its applicant count onto our rows."""
        cards = [_li_card(1, title="Solutions Architect, Infrastructure")]
        sig = _sig(1, title="Solutions Architect, Infrastructure",
                   req_id="JR2024999")     # foreign req
        provider = _FakeProvider(cards, {"1": sig})
        rows = [_post("JR2024001",
                      title="Solutions Architect, Infrastructure"),
                _post("JR2024002",
                      title="Solutions Architect, Infrastructure")]
        out, _, _ = self._run(monkeypatch, provider, rows)
        assert out == {}                 # nobody gets the foreign signal

    def test_inbatch_reqid_exact_overrides_title(self, monkeypatch):
        """reqId-exact when the extracted reqId IS one of ours: that req
        gets the signal, its title-family siblings do NOT (1:1)."""
        cards = [_li_card(1, title="Solutions Architect, Infrastructure")]
        sig = _sig(1, title="Solutions Architect, Infrastructure",
                   req_id="JR2024001")
        provider = _FakeProvider(cards, {"1": sig})
        rows = [_post("JR2024001",
                      title="Solutions Architect, Infrastructure"),
                _post("JR2024002",
                      title="Solutions Architect, Infrastructure")]
        out, _, _ = self._run(monkeypatch, provider, rows)
        assert set(out) == {"JR2024001"}   # exactly the reqId match
        assert out["JR2024001"] is sig

    def test_blocked_index_is_an_empty_no_op_not_a_crash(self,
                                                         monkeypatch):
        provider = _FakeProvider(
            [], {},
            index_exc=watch.corroborate.CorroborationBlocked("429 wall"))
        out, _, _ = self._run(monkeypatch, provider, [_post("JR1")])
        assert out == {}
        assert provider.fetch_calls == []   # signals never attempted

    def test_non_matched_signal_records_are_not_used(self, monkeypatch):
        """The card matched by title, but its fetch was blocked — no
        signal for the posting (blocked ≠ zero-applicants)."""
        cards = [_li_card(1, title="Senior Engineer - DGX Cloud")]
        provider = _FakeProvider(
            cards, {"1": _sig(1, title="Senior Engineer - DGX Cloud",
                              status="blocked")})
        out, _, _ = self._run(
            monkeypatch, provider,
            [_post("JR1", title="Senior Engineer")])
        assert out == {}

    def test_fetch_bounded_at_details_max(self, monkeypatch):
        monkeypatch.setattr(watch, "DETAILS_MAX", 2)
        cards = [_li_card(i, title="Senior Engineer - DGX Cloud")
                 for i in (1, 2, 3)]
        provider = _FakeProvider(
            cards, {str(i): _sig(i, title="Senior Engineer - DGX Cloud")
                    for i in (1, 2, 3)})
        rows = [_post("JR1", title="Senior Engineer")]
        out, _, _ = self._run(monkeypatch, provider, rows)
        assert provider.fetch_calls == [["1", "2"]]   # capped
        assert set(out) == {"JR1"}

    def test_empty_new_rows_short_circuits(self, monkeypatch):
        provider = _FakeProvider([], {})
        out, got, _ = self._run(monkeypatch, provider, [])
        assert out == {}
        assert "name" not in got      # get_provider never called

    def test_budget_exhausted_skips_corroboration(self, monkeypatch):
        provider = _FakeProvider([], {})
        out, got, _ = self._run(monkeypatch, provider, [_post("JR1")],
                                deadline=time.monotonic() - 1)
        assert out == {}
        assert "name" not in got      # provider never constructed


# ── run_watch state machine ──────────────────────────────────────────────
class TestRunWatch:
    def _run(self, wdir, monkeypatch, prior: list[dict], current: list[dict],
             complete: bool = True, legs: int = 1,
             enrich_out=None, corroborate_out=None):
        state = wdir / "test_watch.state.jsonl"
        if prior:
            state.write_text("\n".join(json.dumps(r) for r in prior) + "\n",
                             encoding="utf-8")
        monkeypatch.setattr(
            watch, "current_postings",
            lambda *a, **k: ({p["reqId"]: p for p in current}, complete, False))
        monkeypatch.setattr(
            watch, "_legs_bump", lambda label: legs)
        def _default_enrich(new_rows, *a, **k):
            return [{"reqId": r["reqId"], "title": r["title"],
                     "first_seen": "2026-09-10",
                     "locations": ["US, CA, Santa Clara"],
                     "startDate": "2026-09-09", "description": "d",
                     "url": r.get("url", "")} for r in new_rows]
        monkeypatch.setattr(
            watch, "enrich_new",
            _default_enrich if enrich_out is None
            else (lambda *a, **k: enrich_out))
        monkeypatch.setattr(
            watch, "corroborate_new",
            lambda *a, **k: corroborate_out or {})
        monkeypatch.setattr(watch, "_send_alerts", lambda *a, **k: None)
        return watch.run_watch(dict(CFG), Config())

    def test_new_and_gone_diff_and_state_rewrite(self, wdir, monkeypatch):
        prior = [
            {"reqId": "JR1", "title": "Old", "first_seen": "2026-09-01",
             "last_seen": "2026-09-09", "last_postedOn": "",
             "last_startDate": ""},
            {"reqId": "JR2", "title": "Keep", "first_seen": "2026-09-01",
             "last_seen": "2026-09-09", "last_postedOn": "",
             "last_startDate": ""}]
        result = self._run(wdir, monkeypatch, prior,
                           current=[_post("JR2"), _post("JR3")])
        assert result == "complete"
        lines = [json.loads(x) for x in
                 (wdir / "test_watch.state.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert {r["reqId"] for r in lines} == {"JR2", "JR3"}  # JR1 gone
        jr2 = next(r for r in lines if r["reqId"] == "JR2")
        assert jr2["first_seen"] == "2026-09-01"      # preserved
        assert jr2["last_seen"] != "2026-09-09"       # refreshed today
        # newposts feed has the enriched new posting
        newposts = [json.loads(x) for x in
                    (wdir / "test_watch.newposts.jsonl").read_text(
                        encoding="utf-8").splitlines()]
        assert [r["reqId"] for r in newposts] == ["JR3"]

    def test_partial_list_never_gone_b1(self, wdir, monkeypatch):
        # list broke after JR1 — JR2's fate is UNKNOWN, not gone
        prior = [{"reqId": "JR1", "title": "A",
                  "first_seen": "2026-09-01", "last_seen": "2026-09-09"},
                 {"reqId": "JR2", "title": "B",
                  "first_seen": "2026-09-01", "last_seen": "2026-09-09"}]
        result = self._run(wdir, monkeypatch, prior,
                           current=[_post("JR1")], complete=False)
        assert result == "complete"
        # state keeps BOTH — JR2 preserved verbatim (no false "new" next run)
        lines = [json.loads(x) for x in
                 (wdir / "test_watch.state.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert {r["reqId"] for r in lines} == {"JR1", "JR2"}
        jr2 = next(r for r in lines if r["reqId"] == "JR2")
        assert jr2["last_seen"] == "2026-09-09"   # untouched

    def test_total_list_failure_state_untouched(self, wdir, monkeypatch):
        prior = [{"reqId": "JR1", "title": "A",
                  "first_seen": "2026-09-01", "last_seen": "2026-09-09"}]
        result = self._run(wdir, monkeypatch, prior, current=[],
                           complete=False)
        assert result == "failed"
        lines = [json.loads(x) for x in
                 (wdir / "test_watch.state.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert [r["reqId"] for r in lines] == ["JR1"]

    def test_empty_board_anomaly_guard(self, wdir, monkeypatch):
        prior = [{"reqId": f"JR{i}", "title": "T",
                  "first_seen": "2026-09-01", "last_seen": "2026-09-09"}
                 for i in range(12)]
        result = self._run(wdir, monkeypatch, prior, current=[],
                           complete=True)
        assert result == "failed"    # suspicious empty ≠ mass-gone
        # state untouched
        assert (wdir / "test_watch.state.jsonl").read_text(
            encoding="utf-8").count("\n") == 12

    def test_backlog_verdict_only_with_new_and_legs(self, wdir, monkeypatch):
        result = self._run(wdir, monkeypatch, prior=[],
                           current=[_post("JR1")], legs=1,
                           enrich_out=[])   # nothing enriched → backlog
        assert result == "backlog"

    def test_no_alerts_when_quiet(self, wdir, monkeypatch):
        sent = []
        monkeypatch.setattr(
            watch, "_send_alerts",
            lambda label, digest, cfg: sent.append(digest))
        prior = [{"reqId": "JR1", "title": "Same",
                  "first_seen": "2026-09-01", "last_seen": "2026-09-09"}]
        self._run(wdir, monkeypatch, prior, current=[_post("JR1")])
        assert sent == []            # no new/gone → no alert spam


# ── legs counter (the retrigger cap) ─────────────────────────────────────
class TestLegs:
    def test_date_keyed_reset(self, wdir, monkeypatch):
        assert watch._legs_bump("test_watch") == 1
        assert watch._legs_bump("test_watch") == 2
        # yesterday's file → counter resets
        (wdir / "test_watch.legs.json").write_text(
            json.dumps({"date": "2000-01-01", "legs": 99}))
        assert watch._legs_bump("test_watch") == 1


# ── digest shape ─────────────────────────────────────────────────────────
class TestDigest:
    def test_new_line_with_applicants_and_gone_line(self):
        digest = watch.format_digest(
            "test_watch", "NVIDIA",
            new_rows=[_post("JR1", "SRE")],
            enriched=[{"reqId": "JR1", "locations": ["US, CA, Santa Clara"],
                       "startDate": "2026-09-09",
                       "externalUrl": "https://x/JR1"}],
            signals={"JR1": {"num_applicants": 25}},
            gone_rows=[{"reqId": "JR0", "title": "Old",
                        "first_seen": "2026-08-01"}],
            current_count=1360)
        assert "+1 new, -1 gone (1360 active" in digest
        assert "NEW SRE | US, CA, Santa Clara | 2026-09-09" in digest
        assert "25 applicants" in digest
        assert "GONE Old (first seen 2026-08-01)" in digest

    def test_gone_capped_at_20(self):
        gone = [{"reqId": f"JR{i}", "title": "T", "first_seen": "x"}
                for i in range(30)]
        digest = watch.format_digest("l", "NVIDIA", [], [], {}, gone, 5)
        assert digest.count("GONE T ") == 20
        assert "and 10 more" in digest


# ── seed_state (B3) ──────────────────────────────────────────────────────
class TestSeed:
    def test_seed_from_dump(self, wdir, tmp_path):
        src = tmp_path / "list.jsonl"
        src.write_text("\n".join(json.dumps(_post(f"JR{i}"))
                                  for i in range(5)) + "\n",
                       encoding="utf-8")
        watch.seed_state(CFG, src, "2026-09-09")
        lines = [json.loads(x) for x in
                 (wdir / "test_watch.state.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert len(lines) == 5
        assert all(r["first_seen"] == "2026-09-09" for r in lines)

    def test_seed_refuses_existing_state(self, wdir, tmp_path):
        (wdir / "test_watch.state.jsonl").write_text("x\n")
        src = tmp_path / "list.jsonl"
        src.write_text(json.dumps(_post("JR1")) + "\n")
        watch.seed_state(CFG, src, "2026-09-09")
        assert (wdir / "test_watch.state.jsonl").read_text() == "x\n"


# ── cross-post-lag re-corroboration pass ─────────────────────────────────
class TestRecorroborate:
    def test_recent_unsignal_ed_get_second_pass(self, wdir, monkeypatch):
        from datetime import date as _d
        today = _d.today().isoformat()
        feed = wdir / "test_watch.newposts.jsonl"
        feed.write_text("\n".join(json.dumps(r) for r in [
            # recent, no signals → candidate
            {"reqId": "JR9", "title": "Fresh", "first_seen": today,
             "locations": ["US, CA, Santa Clara"], "url": "u"},
            # recent BUT already has signals → skip
            {"reqId": "JR8", "title": "Done", "first_seen": today,
             "signals": {"num_applicants": 5}, "url": "u"},
            # too old → skip
            {"reqId": "JR7", "title": "Stale",
             "first_seen": "2020-01-01", "url": "u"},
        ]) + "\n", encoding="utf-8")

        state = wdir / "test_watch.state.jsonl"
        state.write_text(json.dumps(
            {"reqId": "JR9", "title": "Fresh", "first_seen": today,
             "last_seen": today}) + "\n", encoding="utf-8")

        monkeypatch.setattr(watch, "current_postings",
                            lambda *a, **k: (
                                {"JR9": _post("JR9", "Fresh")}, True,
                                False))
        monkeypatch.setattr(watch, "_legs_bump", lambda l: 1)
        monkeypatch.setattr(watch, "enrich_new", lambda *a, **k: [])
        # new-corroboration returns nothing; lag pass must hit JR9
        monkeypatch.setattr(
            watch, "corroborate_new",
            lambda rows, company, cfg, deadline, **k: (
                {"JR9": {"num_applicants": 42,
                         "status": "matched"}} if rows else {}))
        monkeypatch.setattr(watch, "_send_alerts", lambda *a, **k: None)
        # generous budget so the lag pass runs
        monkeypatch.setattr(watch, "BUDGET_SECONDS", 3600)
        result = watch.run_watch(dict(CFG), Config())
        assert result == "complete"
        lines = [json.loads(x) for x in feed.read_text(
            encoding="utf-8").splitlines()]
        jr9 = [r for r in lines if r["reqId"] == "JR9"]
        assert len(jr9) == 2                      # original + updated
        assert jr9[-1]["signals"]["num_applicants"] == 42


# ── P0-1 regression: the backlog must drain across legs ──────────────────
class TestBacklogDrainsAcrossLegs:
    """The pre-fix bug (S7-A2 C / S7-A3 P0-1): leg 1 admitted ALL current
    postings to state, so retriggered legs saw +0 new and the unenriched
    postings were never recovered — the self-retrigger was a no-op and
    newposts.jsonl permanently lost v2 enrichment for everything beyond
    DETAILS_MAX."""

    def test_two_legs_recover_the_leftovers(self, wdir, monkeypatch):
        monkeypatch.setattr(watch, "DETAILS_MAX", 2)
        from datetime import date as _d
        today = _d.today().isoformat()

        board = {f"JR{i}": _post(f"JR{i}") for i in range(1, 6)}  # 5 live
        state = wdir / "test_watch.state.jsonl"
        state.write_text(json.dumps(
            {"reqId": "JR0", "title": "Old", "first_seen": "2026-09-01",
             "last_seen": today}) + "\n", encoding="utf-8")

        def fake_current(*a, **k):
            return dict(board), True, False

        def fake_enrich(rows, *a, **k):
            return [{"reqId": r["reqId"], "title": r["title"],
                     "first_seen": today, "locations": ["US, CA, X"],
                     "startDate": today, "description": "d",
                     "url": r.get("url", "")} for r in rows[:2]]

        monkeypatch.setattr(watch, "current_postings", fake_current)
        monkeypatch.setattr(watch, "enrich_new", fake_enrich)
        monkeypatch.setattr(watch, "corroborate_new",
                            lambda *a, **k: {})
        monkeypatch.setattr(watch, "_send_alerts", lambda *a, **k: None)

        r1 = watch.run_watch(dict(CFG), Config())
        assert r1 == "backlog"          # 5 new, only 2 enriched
        feed = {json.loads(x)["reqId"] for x in
                (wdir / "test_watch.newposts.jsonl").read_text(
                    encoding="utf-8").splitlines()}
        assert len(feed) == 2

        r2 = watch.run_watch(dict(CFG), Config())
        assert r2 == "backlog"          # +0 new BUT recovery found the 3
        feed = {json.loads(x)["reqId"] for x in
                (wdir / "test_watch.newposts.jsonl").read_text(
                    encoding="utf-8").splitlines()}
        assert len(feed) == 4           # 2 + 2 recovered

        r3 = watch.run_watch(dict(CFG), Config())
        assert r3 == "complete"         # last one drained, 0 remaining
        feed = {json.loads(x)["reqId"] for x in
                (wdir / "test_watch.newposts.jsonl").read_text(
                    encoding="utf-8").splitlines()}
        assert len(feed) == 5

        # needs_enrich flag cleared for all enriched, none left flagged
        flagged = [json.loads(x) for x in state.read_text(
            encoding="utf-8").splitlines()
            if json.loads(x).get("needs_enrich")]
        assert flagged == []

    def test_facets_actually_applied_to_page_requests(self, monkeypatch):
        """S7-A2 P1-A regression: every fake _page used to IGNORE the
        facets argument — the timeType facet was never even assigned, so
        the watch silently listed ALL time types. This fake ASSERTS the
        facets contract."""
        seen_facets = []

        def fake_page(board, facets, offset, cfg):
            seen_facets.append((dict(facets), offset))
            # 1 page, 2 postings, total 2
            return _page_payload([_post("JR1"), _post("JR2")], 2)

        monkeypatch.setattr(watch.workday, "_page", fake_page)
        monkeypatch.setattr(watch.workday, "_facet_id",
                            lambda p, param, label: f"f_{param}")
        rows, complete, _cc = watch.current_postings(
            "nvidia|wd5|site", "United States", "Full time", Config())
        assert complete and len(rows) == 2
        # page-0 discovery call: no facets; refetch + all pages: BOTH facets
        assert seen_facets[0] == ({}, 0)
        facet_calls = [f for f, o in seen_facets[1:]]
        assert all(f == {"locationHierarchy1": ["f_locationHierarchy1"],
                         "timeType": ["f_timeType"]} for f in facet_calls), \
            seen_facets

    def test_country_only_watch_refetches_with_facet(self, monkeypatch):
        """S7-A2 P2-E: the facet refetch was nested under time_type — a
        country-only watch kept an UNFILTERED page 0."""
        seen = []

        def fake_page(board, facets, offset, cfg):
            seen.append((dict(facets), offset))
            return _page_payload([_post("JR1")], 1)

        monkeypatch.setattr(watch.workday, "_page", fake_page)
        monkeypatch.setattr(watch.workday, "_facet_id",
                            lambda p, param, label: f"f_{param}")
        watch.current_postings("nvidia|wd5|site", "United States", "",
                               Config())
        assert seen[0] == ({}, 0)                     # discovery
        assert seen[1] == ({"locationHierarchy1":
                            ["f_locationHierarchy1"]}, 0)   # refetched!


class TestB6ErrorRetry:
    """S7-V1 B6: a detail_unreachable record must retry (3-strike) via the
    recovery query — a transient detail outage must not permanently
    degrade the newposts record."""

    def _run_once(self, wdir, monkeypatch, current, feed_prior, enrich_out):
        state = wdir / "test_watch.state.jsonl"
        prior = [{"reqId": rid, "title": t, "first_seen": "2026-09-01",
                  "last_seen": "2026-09-09"} for rid, t in current]
        state.write_text("\n".join(json.dumps(r) for r in prior) + "\n",
                         encoding="utf-8")
        feed = wdir / "test_watch.newposts.jsonl"
        if feed_prior:
            feed.write_text("\n".join(
                json.dumps(r) for r in feed_prior) + "\n",
                encoding="utf-8")
        monkeypatch.setattr(
            watch, "current_postings",
            lambda *a, **k: ({p: {"reqId": p, "title": t, "url": "u",
                                   "locationsText": "US, CA, X"}
                              for p, t in current}, True, False))
        monkeypatch.setattr(watch, "_legs_bump", lambda l: 1)
        monkeypatch.setattr(watch, "enrich_new", enrich_out)
        monkeypatch.setattr(watch, "corroborate_new", lambda *a, **k: {})
        monkeypatch.setattr(watch, "_send_alerts", lambda *a, **k: None)
        return watch.run_watch(dict(CFG), Config())

    def test_error_record_retried_until_three_strikes(self, wdir,
                                                      monkeypatch):
        # feed carries an error record w/ attempts=1 → recoverable
        feed_prior = [{"reqId": "JR1", "title": "T", "first_seen":
                       "2026-09-01", "error": "detail_unreachable",
                       "url": "https://real/JR1",
                       "attempts": 1}]
        got_input: dict = {}

        def fake_enrich(rows, *a, **k):
            got_input["rows"] = list(rows)
            return [{"reqId": r["reqId"], "title": r.get("title", "T"),
                     "first_seen": "2026-09-01",
                     "locations": ["US, CA, X"], "url": "u",
                     "error": "detail_unreachable",
                     "attempts": 2} for r in rows]

        r = self._run_once(wdir, monkeypatch, [("JR1", "T")], feed_prior,
                           fake_enrich)
        assert r == "complete"
        assert [x["reqId"] for x in got_input["rows"]] == ["JR1"]  # retried

    def test_three_strike_error_not_retried(self, wdir, monkeypatch):
        # legit-failure shape (real url) at 3 strikes → skipped
        feed_prior = [{"reqId": "JR1", "title": "T", "first_seen":
                       "2026-09-01", "error": "detail_unreachable",
                       "url": "https://real/JR1",
                       "attempts": 3}]
        got_input: dict = {}

        def fake_enrich(rows, *a, **k):
            got_input["rows"] = list(rows)
            return []

        self._run_once(wdir, monkeypatch, [("JR1", "T")], feed_prior,
                       fake_enrich)
        assert got_input["rows"] == []          # 3 strikes → skipped


class TestBacklogRecoveryUsesCurrentRows:
    """S9-audit D2 P1 regression. The pre-fix backlog query fed STATE-
    shaped rows (no externalPath / url) to the REAL enrich_new →
    detail_payload('') → guaranteed detail_unreachable → every recovery
    retry was structurally doomed (25 live reqIds burned the 3-strike
    valve on failures that could never succeed). Both recovery branches
    (needs_enrich crash survivor / feed error retry) must enrich the
    CURRENT list row — the predicate already guarantees rid ∈ current.
    REAL enrich_new runs here; network mocked at ONE seam
    (watch.workday.detail_payload), exactly like TestEnrichNew."""

    def _mock_watch(self, wdir, monkeypatch, detail):
        monkeypatch.setattr(watch.workday, "detail_payload", detail)
        monkeypatch.setattr(
            watch, "current_postings",
            lambda *a, **k: ({"JR1": _post("JR1", "Staff SRE")}, True, False))
        monkeypatch.setattr(watch, "_legs_bump", lambda l: 1)
        monkeypatch.setattr(watch, "corroborate_new", lambda *a, **k: {})
        monkeypatch.setattr(watch, "_send_alerts", lambda *a, **k: None)

    def test_crash_survivor_enriches_the_current_list_row(self, wdir,
                                                          monkeypatch):
        # last_seen=today + Yesterday→Today aging keeps the R1 repost
        # detector quiet (a flag would add an R2 detail re-fetch call)
        state = wdir / "test_watch.state.jsonl"
        state.write_text(json.dumps({
            "reqId": "JR1", "title": "Staff SRE",
            "first_seen": "2026-09-12",
            "last_seen": date.today().isoformat(),
            "last_postedOn": "Posted Yesterday", "last_startDate": "",
            "needs_enrich": True}) + "\n", encoding="utf-8")
        calls: list = []

        def fake_detail(board, path, cfg):
            calls.append(path)
            return TestEnrichNew._payload("JR1")

        self._mock_watch(wdir, monkeypatch, fake_detail)
        result = watch.run_watch(dict(CFG), Config())
        assert result == "complete"
        # THE regression: the detail fetch used the CURRENT list row's
        # externalPath (pre-fix: '' — a structurally doomed fetch)
        assert calls == ["/job/JR1"]
        rec = _read_jsonl(wdir / "test_watch.newposts.jsonl")[0]
        assert rec["reqId"] == "JR1"
        assert rec["url"] == "https://x/JR1"     # real list-row url
        assert "error" not in rec                # enrichment SUCCEEDED
        assert rec["startDate"] == "2026-09-08"
        # D2 P2: recovery must not reset tenure — the STATE's first
        # sighting seeds the feed record (crash survivor: no feed line)
        assert rec["first_seen"] == "2026-09-12"
        # needs_enrich cleared by the good record
        st = _read_jsonl(state)
        assert not st[0].get("needs_enrich")

    def test_error_retry_uses_current_rows_and_keeps_first_seen(
            self, wdir, monkeypatch):
        """The B6 3-strike branch: a feed error record (attempts=1) is
        recoverable, and its retry must fetch the real detail page and
        keep the original first_seen (pre-fix drift: every retry stamped
        today — 25/25 live error records wrong)."""
        state = wdir / "test_watch.state.jsonl"
        state.write_text(json.dumps({
            "reqId": "JR1", "title": "Staff SRE",
            "first_seen": "2026-09-12",
            "last_seen": date.today().isoformat(),
            "last_postedOn": "Posted Yesterday",
            "last_startDate": ""}) + "\n", encoding="utf-8")
        feed = wdir / "test_watch.newposts.jsonl"
        feed.write_text(json.dumps({
            "reqId": "JR1", "title": "Staff SRE",
            "first_seen": "2026-09-12", "url": "",
            "error": "detail_unreachable", "attempts": 1}) + "\n",
            encoding="utf-8")
        calls: list = []

        def fake_detail(board, path, cfg):
            calls.append(path)
            return TestEnrichNew._payload("JR1")

        self._mock_watch(wdir, monkeypatch, fake_detail)
        result = watch.run_watch(dict(CFG), Config())
        assert result == "complete"
        assert calls == ["/job/JR1"]              # real externalPath
        rec = _read_jsonl(feed)[-1]              # last line wins
        assert "error" not in rec                # the retry SUCCEEDED
        assert rec["url"] == "https://x/JR1"
        assert rec["first_seen"] == "2026-09-12"  # prior record's date


class TestWatchJoinOneToOne:
    """S7-V1 P3-3 (elevated): one LinkedIn card's applicants must never
    fan out onto a whole title family in the WATCH path either."""

    def test_one_card_serves_exactly_one_req(self, monkeypatch):
        cards = [_li_card(1, title="SRE"), _li_card(2, title="SRE")]
        sigs = {"1": _sig(1, title="SRE", applicants=32),
                "2": _sig(2, title="SRE", applicants=45)}
        provider = _FakeProvider(cards, sigs)
        rows = [_post("JRA", title="SRE"), _post("JRB", title="SRE"),
                _post("JRC", title="SRE")]
        out, _, _ = TestCorroborateNew()._run(monkeypatch, provider, rows)
        # 2 cards, 3 same-family reqs → at most 2 matched, no sharing
        assert len(out) <= 2
        assert len({id(s) for s in out.values()}) == len(out)


# ── S9-audit fix wave: C5 title-search fallback policy ──────────────────
class TestTitleSearchFallbackFixes:
    """Pins for the S9-audit fixes (D1 P2/P3): hits iteration (a sibling
    card at hits[0] must not starve the req's own jr-exact card at
    hits[1]), window-parity foreign-reqId policy, pause-on-every-search,
    and the fallback provenance stamp. REAL corroborate_new; provider
    faked at the get_provider seam (search_title included)."""

    def _run(self, monkeypatch, provider, rows, budget=None):
        monkeypatch.setattr(watch.corroborate, "get_provider",
                            lambda name, cfg=None: provider)
        kw: dict = {}
        if budget is not None:
            kw["title_search_budget"] = budget
        return watch.corroborate_new(rows, "NVIDIA", Config(),
                                     time.monotonic() + 60, **kw)

    def test_jr_exact_hit_serves_the_searching_req(self, monkeypatch):
        """(D1 test-gap b) jr == rid: the hit's extracted job_req_id
        names THIS req — reqId-exact confirmation. _sig defaults
        req_id="", so no pre-existing test ever exercised the branch."""
        provider = _FakeProvider(
            [], {"7": _sig(7, title="SRE", req_id="JR2024001")},
            ts_hits=[_li_card(7, title="SRE")])
        rows = [_post("JR2024001", title="SRE")]
        out = self._run(monkeypatch, provider, rows, budget=[5])
        assert set(out) == {"JR2024001"}
        assert out["JR2024001"]["job_req_id"] == "JR2024001"
        assert provider.fetch_calls == [["7"]]       # one detail fetch

    def test_hits_iterated_sibling_served_then_own_card(self, monkeypatch):
        """(D1 test-gap c, probe P4) JR1's search serves card 7 (jr=JR1);
        JR2's search returns [card7 (served), card8 (jr=JR2 — JR2's OWN
        card)]. Pre-fix: the served check discarded hits[0] and the loop
        continued — card 8 was never fetched, JR2 unmatched. Also pins
        the served-check-BEFORE-fetch (card 7 fetched exactly once)."""
        provider = _FakeProvider(
            [], {"7": _sig(7, title="SRE", req_id="JR1"),
                 "8": _sig(8, title="SRE", req_id="JR2")},
            ts_hits=[_li_card(7, title="SRE"), _li_card(8, title="SRE")])
        rows = [_post("JR1", title="SRE"), _post("JR2", title="SRE")]
        out = self._run(monkeypatch, provider, rows, budget=[5])
        assert set(out) == {"JR1", "JR2"}           # BOTH siblings served
        assert out["JR1"]["linkedin_job_id"] == "7"
        assert out["JR2"]["linkedin_job_id"] == "8"   # hits[1] reached
        assert sorted(provider.fetch_calls) == [["7"], ["8"]]

    def test_jrless_hold_beats_nothing_foreign_hit_serves_named_req(
            self, monkeypatch):
        """Composite: a jr-less card at hits[0] is HELD while scanning
        for a jr-exact card; the jr-bearing card at hits[1] names the
        SEARCHER'S SIBLING (JR2, an input req) → window parity: it serves
        JR2; the held jr-less card then serves the searcher (JR1)."""
        provider = _FakeProvider(
            [], {"7": _sig(7, title="SRE", req_id=""),
                 "8": _sig(8, title="SRE", req_id="JR2")},
            ts_hits=[_li_card(7, title="SRE"), _li_card(8, title="SRE")])
        rows = [_post("JR1", title="SRE"), _post("JR2", title="SRE")]
        out = self._run(monkeypatch, provider, rows, budget=[5])
        assert out["JR1"]["linkedin_job_id"] == "7"   # held jr-less card
        assert out["JR2"]["linkedin_job_id"] == "8"   # named by card 8
        # JR2 was served mid-loop by JR1's search → its own search is
        # skipped (budget not burned on an already-served req)
        assert len(provider.ts_calls) == 1

    def test_foreign_hit_serves_the_req_it_names(self, monkeypatch):
        """(D1 P2 asymmetry) A card whose job_req_id names ANOTHER input
        new posting serves that req in the window path — the fallback
        used to serve nobody in this corner (docstring claimed "SAME
        policy")."""
        provider = _FakeProvider(
            [], {"9": _sig(9, title="SRE", req_id="JRB")},
            ts_hits=[_li_card(9, title="SRE")])
        rows = [_post("JRA", title="SRE"), _post("JRB", title="SRE")]
        out = self._run(monkeypatch, provider, rows, budget=[5])
        assert set(out) == {"JRB"}       # the card's OWN requisition
        assert out["JRB"]["linkedin_job_id"] == "9"
        assert len(provider.ts_calls) == 1   # JRB skipped (already served)

    def test_pause_after_every_executed_search(self, monkeypatch):
        """(D1 test-gap d, B3 P2) The pause used to be skipped by all six
        continue paths — no-hit searches ran back-to-back against the
        walled guest endpoint. EVERY executed search must sleep; the
        pre-loop skips (empty title) consume nothing and stay silent."""
        provider = _FakeProvider(
            [], {"9": _sig(9, title="Foreign", req_id="JR999")},
            ts_map={"No Hit": ([], None),
                    "Blocked": ([], RuntimeError("guest wall")),
                    "Foreign": ([_li_card(9, title="Foreign")], None)})
        rows = [_post("JR1", title="No Hit"),
                _post("JR2", title="Blocked"),
                _post("JR3", title="Foreign"),
                _post("JR4", title="")]           # pre-loop skip
        sleeps: list = []
        monkeypatch.setattr(watch.time, "sleep",
                            lambda s: sleeps.append(s))
        out = self._run(monkeypatch, provider, rows, budget=[10])
        assert out == {}                      # nobody served
        assert len(provider.ts_calls) == 3     # JR4 never searched
        # every executed search slept — no-hit, exception and foreign
        # paths included (pre-fix: zero of the three)
        assert len(sleeps) == 3
        assert all(s == watch.corroborate.TITLE_SEARCH_PAUSE_S
                   for s in sleeps)

    def test_fallback_provenance_stamp_window_unstamped(self, monkeypatch):
        """(D1 P3) Fallback-assigned signals carry
        match_method='titleSearchFallback' so newposts.jsonl is
        self-describing (the run #4 "+4 matches" console line is the
        only other evidence, and it expires with the GHA log); window
        matches are deliberately NOT stamped."""
        provider = _FakeProvider(
            [_li_card(1, title="Senior Engineer - DGX Cloud")],
            {"1": _sig(1, title="Senior Engineer - DGX Cloud",
                       req_id="JR1"),
             "50": _sig(50, title="Niche Guru")},
            ts_hits=[_li_card(50, title="Niche Guru")])
        rows = [_post("JR1", title="Senior Engineer"),   # window match
                _post("JR2", title="Niche Guru")]        # fallback match
        out = self._run(monkeypatch, provider, rows, budget=[5])
        assert set(out) == {"JR1", "JR2"}
        assert "match_method" not in out["JR1"]    # window: unstamped
        assert out["JR2"]["match_method"] == "titleSearchFallback"


class TestTitleSearchBudgetWiring:
    """(D1/E1 test-gap a) NOTHING used to assert that run_watch passes
    the budget cell to the MAIN corroborate call and NOT to the lag pass
    — TestRunWatch/TestRecorroborate stub corroborate_new with **k-
    swallowing lambdas, so deleting the title_search_budget kwarg at the
    main call site disabled C5 in production with the suite green."""

    def test_main_call_gets_cell_lag_call_does_not(self, wdir, monkeypatch):
        calls: list[dict] = []

        def recording(rows, company, cfg, deadline, **k):
            calls.append({"rows": [r["reqId"] for r in rows],
                          "budget": k.get("title_search_budget")})
            return {}

        today = date.today().isoformat()
        monkeypatch.setattr(
            watch, "current_postings",
            lambda *a, **k: ({"JR9": _post("JR9")}, True, False))
        monkeypatch.setattr(watch, "_legs_bump", lambda l: 1)
        # enrich writes a signal-less record dated today → the lag pass
        # must pick it up for its second corroborate call
        monkeypatch.setattr(
            watch, "enrich_new",
            lambda rows, *a, **k: [
                {"reqId": r["reqId"], "title": r["title"],
                 "first_seen": today, "url": r.get("url", "")}
                for r in rows])
        monkeypatch.setattr(watch, "corroborate_new", recording)
        monkeypatch.setattr(watch, "_send_alerts", lambda *a, **k: None)
        result = watch.run_watch(dict(CFG), Config())
        assert result == "complete"
        assert len(calls) == 2                     # main + lag pass
        # MAIN call: the run-level budget cell — C5 wired in production
        assert calls[0]["rows"] == ["JR9"]
        assert calls[0]["budget"] == [watch.TITLE_SEARCH_MAX]
        # LAG call: NO cell — the lag pass NEVER title-searches
        assert calls[1]["rows"] == ["JR9"]
        assert calls[1]["budget"] is None


# ═════════════════════════════════════════════════════════════════════════
# S8-E2: repost / days-on-market detector (audit findings-recency.md §7/§8)
# ═════════════════════════════════════════════════════════════════════════
RUN_DATE = date(2026, 9, 13)          # fixed PT run date for pure tests


def _prior(rid: str, label: str, last_seen: str = "2026-09-12",
           last_startDate: str = "", **extra) -> dict:
    """A state row in the CURRENT PRODUCTION schema (old-format for the
    S8-E2 fields: no implied_post_date / startDate_first — the exact
    shape of the 1,395 committed nvidia_us_fulltime.state.jsonl rows)."""
    row = {"reqId": rid, "title": f"Role {rid}", "first_seen": "2026-09-01",
           "last_seen": last_seen, "last_postedOn": label,
           "last_startDate": last_startDate}
    row.update(extra)
    return row


def _cur(rid: str, label: str, title: str = None) -> dict:
    """A current LIST row carrying a specific postedOn label (real labels
    from the committed state file: 'Posted Yesterday', 'Posted N Days
    Ago', 'Posted 30+ Days Ago')."""
    r = _post(rid, title or f"Role {rid}")
    r["postedOn"] = label
    return r


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in
            path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ── postedOn parser ──────────────────────────────────────────────────────
class TestPostedOnParser:
    def test_yesterday(self):
        assert watch.parse_posted_on("Posted Yesterday") == 1

    def test_n_days_ago(self):
        assert watch.parse_posted_on("Posted 3 Days Ago") == 3
        # singular form + case-insensitivity
        assert watch.parse_posted_on("posted 1 day ago") == 1
        # "Posted Today" exists in the list-card label set (bucket 0)
        assert watch.parse_posted_on("Posted Today") == 0

    def test_open_bucket_30_plus(self):
        v = watch.parse_posted_on("Posted 30+ Days Ago")
        assert v == watch.OPEN_BUCKET       # censored sentinel (< 0)
        assert v is not None                # …but PARSEABLE (≠ unparseable)
        # the CLOSED bucket 30 is a plain number — 30 → 30+ is normal aging
        assert watch.parse_posted_on("Posted 30 Days Ago") == 30

    def test_unparseable_returns_none(self):
        for bad in ("", "Posted Recently", "30+", "New", "Posted 30+ Days",
                    "Posted About A Month Ago"):
            assert watch.parse_posted_on(bad) is None, bad


# ── implied post date + the Pacific timezone rule (audit §7.4) ───────────
class TestImpliedPostDate:
    def test_run_date_minus_bucket(self):
        assert watch.implied_post_date(3, RUN_DATE) == date(2026, 9, 10)
        assert watch.implied_post_date(0, RUN_DATE) == RUN_DATE

    def test_open_and_unparseable_are_censored(self):
        assert watch.implied_post_date(watch.OPEN_BUCKET, RUN_DATE) is None
        assert watch.implied_post_date(None, RUN_DATE) is None


class TestPtRunDate:
    def test_cron_0645z_is_pre_pt_rollover(self):
        # THE production case (audit §7.4): 06:45Z is 23:45/22:45 PT the
        # PREVIOUS day — a UTC run date would skew every implied date by
        # exactly 1 day, every single day
        assert watch._to_pt_date(
            datetime(2026, 9, 13, 6, 45, tzinfo=timezone.utc)) \
            == date(2026, 9, 12)          # PDT (UTC−7)
        assert watch._to_pt_date(
            datetime(2026, 12, 13, 6, 45, tzinfo=timezone.utc)) \
            == date(2026, 12, 12)         # PST (UTC−8)

    def test_afternoon_utc_same_pt_date(self):
        assert watch._to_pt_date(
            datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)) \
            == date(2026, 9, 13)

    def test_run_date_smoke(self):
        assert isinstance(watch._pt_run_date(), date)


# ── R1: label-regression detector (pure) ─────────────────────────────────
class TestDetectReposts:
    def test_normal_aging_21_to_22_not_a_regression(self):
        prior = {"JR1": _prior("JR1", "Posted 21 Days Ago")}
        cur = {"JR1": _cur("JR1", "Posted 22 Days Ago")}
        assert watch.detect_reposts(cur, prior, RUN_DATE) == []

    def test_30_to_30plus_is_aging_never_a_regression(self):
        prior = {"JR1": _prior("JR1", "Posted 30 Days Ago")}
        cur = {"JR1": _cur("JR1", "Posted 30+ Days Ago")}
        assert watch.detect_reposts(cur, prior, RUN_DATE) == []

    def test_open_to_open_censored_no_flag(self):
        prior = {"JR1": _prior("JR1", "Posted 30+ Days Ago")}
        cur = {"JR1": _cur("JR1", "Posted 30+ Days Ago")}
        assert watch.detect_reposts(cur, prior, RUN_DATE) == []

    def test_dramatic_regression_30plus_to_2d(self):
        # audit §4's headline case (9 rows in 4 days): impossible without
        # a reset — the req exited the censored bucket downward
        prior = {"JR2008226": _prior("JR2008226", "Posted 30+ Days Ago")}
        cur = {"JR2008226": _cur("JR2008226", "Posted 2 Days Ago")}
        flags = watch.detect_reposts(cur, prior, RUN_DATE)
        assert len(flags) == 1
        ev = flags[0]
        assert ev["reqId"] == "JR2008226"
        assert ev["prev_implied"] is None        # OPEN is censored
        assert ev["new_implied"] == "2026-09-11"  # 09-13 − 2d
        assert ev["confidence"] == "label"
        assert ev["label_reset"] is True
        assert (ev["label_from"], ev["label_to"]) == ("30+d", "2d")
        assert ev["prev_startDate"] == ""

    def test_one_day_tolerance_absorbed(self):
        # implied date moved forward by exactly +1 (a 1-day label stall) —
        # boundary rounding, NOT a repost
        prior = {"JR1": _prior("JR1", "Posted 3 Days Ago")}   # 09-12−3
        cur = {"JR1": _cur("JR1", "Posted 3 Days Ago")}       # 09-13−3
        assert watch.detect_reposts(cur, prior, RUN_DATE) == []

    def test_two_day_jump_flags(self):
        # 3d→2d overnight: the posting got a day YOUNGER while a day
        # passed → startDate moved ~2 days forward → flag
        prior = {"JR1": _prior("JR1", "Posted 3 Days Ago")}
        cur = {"JR1": _cur("JR1", "Posted 2 Days Ago")}
        flags = watch.detect_reposts(cur, prior, RUN_DATE)
        assert len(flags) == 1
        assert flags[0]["prev_implied"] == "2026-09-09"
        assert flags[0]["new_implied"] == "2026-09-11"

    def test_real_case_23d_to_2d(self):
        # audit §4: "Posted 23 Days Ago" → "Posted 2 Days Ago" (JR2017846)
        prior = {"JR2017846": _prior("JR2017846", "Posted 23 Days Ago",
                                     last_startDate="2026-08-17")}
        cur = {"JR2017846": _cur("JR2017846", "Posted 2 Days Ago")}
        flags = watch.detect_reposts(cur, prior, RUN_DATE)
        assert len(flags) == 1
        assert flags[0]["label_from"] == "23d"
        assert flags[0]["label_to"] == "2d"
        assert flags[0]["prev_startDate"] == "2026-08-17"
        assert flags[0]["externalPath"] == "/job/JR2017846"  # R2 needs it

    def test_unparseable_labels_never_flag(self):
        for prev_l, new_l in [("Posted Recently", "Posted 2 Days Ago"),
                              ("Posted 23 Days Ago", "Posted Fresh"),
                              ("", "Posted 2 Days Ago")]:
            prior = {"JR1": _prior("JR1", prev_l)}
            cur = {"JR1": _cur("JR1", new_l)}
            assert watch.detect_reposts(cur, prior, RUN_DATE) == [], \
                (prev_l, new_l)

    def test_new_and_gone_rows_are_not_compared(self):
        prior = {"JRGONE": _prior("JRGONE", "Posted 30 Days Ago")}
        cur = {"JRNEW": _cur("JRNEW", "Posted 2 Days Ago")}
        assert watch.detect_reposts(cur, prior, RUN_DATE) == []

    def test_row_without_last_seen_or_implied_skipped(self):
        prior = {"JR1": {"reqId": "JR1", "title": "T",
                         "last_postedOn": "Posted 21 Days Ago"}}
        cur = {"JR1": _cur("JR1", "Posted 2 Days Ago")}
        assert watch.detect_reposts(cur, prior, RUN_DATE) == []

    def test_stored_implied_post_date_preferred_over_last_seen(self):
        # stored 09-08 → new 09-10 = +2 → flag; the last_seen fallback
        # (09-09, +1) would have stayed silent — pinned: stored wins
        prior = {"JR1": dict(_prior("JR1", "Posted 3 Days Ago"),
                             implied_post_date="2026-09-08")}
        cur = {"JR1": _cur("JR1", "Posted 3 Days Ago")}
        flags = watch.detect_reposts(cur, prior, RUN_DATE)
        assert len(flags) == 1
        assert flags[0]["prev_implied"] == "2026-09-08"


# ── startDate-move channel ───────────────────────────────────────────────
class TestDetectStartDateMoves:
    def test_moved_later_is_high_confidence_event(self):
        prior = {"JR2017846": _prior("JR2017846", "Posted 23 Days Ago",
                                     last_startDate="2026-08-17")}
        cur = {"JR2017846": _cur("JR2017846", "Posted 2 Days Ago")}
        evs = watch.detect_start_date_moves(
            cur, prior, {"JR2017846": "2026-09-11"}, RUN_DATE)
        assert len(evs) == 1
        ev = evs[0]
        assert ev["confidence"] == "high"
        assert ev["prev_startDate"] == "2026-08-17"
        assert ev["new_startDate"] == "2026-09-11"
        assert ev["start_delta_days"] == 25
        assert ev["label_reset"] is False    # startDate channel only
        # label context fields still carried (uniform record shape)
        assert ev["prev_postedOn"] == "Posted 23 Days Ago"

    def test_equal_start_date_is_not_an_event(self):
        prior = {"JR1": _prior("JR1", "Posted 4 Days Ago",
                               last_startDate="2026-09-08")}
        cur = {"JR1": _cur("JR1", "Posted 5 Days Ago")}
        assert watch.detect_start_date_moves(
            cur, prior, {"JR1": "2026-09-08"}, RUN_DATE) == []

    def test_backwards_move_is_a_data_bug_not_an_event(self, capsys):
        prior = {"JR1": _prior("JR1", "Posted 4 Days Ago",
                               last_startDate="2026-08-17")}
        cur = {"JR1": _cur("JR1", "Posted 5 Days Ago")}
        assert watch.detect_start_date_moves(
            cur, prior, {"JR1": "2026-01-01"}, RUN_DATE) == []
        assert "BACKWARD" in capsys.readouterr().err   # logged loudly

    def test_missing_prev_start_date_skipped(self):
        prior = {"JR1": _prior("JR1", "Posted 4 Days Ago")}   # no startDate
        cur = {"JR1": _cur("JR1", "Posted 5 Days Ago")}
        assert watch.detect_start_date_moves(
            cur, prior, {"JR1": "2026-09-11"}, RUN_DATE) == []


# ── R2: bounded detail re-fetch ──────────────────────────────────────────
class TestRefetchRepostDetails:
    @staticmethod
    def _flag(rid: str, prev_sd: str = "") -> dict:
        return {"reqId": rid, "title": f"Role {rid}",
                "detected_at": "2026-09-13T06:45:00",
                "prev_postedOn": "Posted 30+ Days Ago",
                "new_postedOn": "Posted 2 Days Ago",
                "prev_implied": None, "new_implied": "2026-09-11",
                "prev_startDate": prev_sd, "confidence": "label",
                "externalPath": f"/job/{rid}",
                "label_reset": True, "label_from": "30+d",
                "label_to": "2d", "start_delta_days": None}

    def test_six_flagged_only_five_fetched(self, monkeypatch):
        monkeypatch.setattr(watch, "REPOST_REFETCH_SLEEP", 0.0)
        calls: list = []

        def fake_detail(board, path, cfg):
            calls.append(path)
            return {"jobPostingInfo": {"startDate": "2026-09-12"}}

        monkeypatch.setattr(watch.workday, "detail_payload", fake_detail)
        flags = [self._flag(f"JR{i}") for i in range(1, 7)]
        events, updates = watch.refetch_repost_details(
            flags, "nvidia|wd5|site", Config(), time.monotonic() + 60)
        assert calls == [f"/job/JR{i}" for i in range(1, 6)]  # capped at 5
        assert len(events) == 6            # all 6 events still logged
        assert len(updates) == 5
        # the unfetched 6th stays label-confidence, no startDate
        sixth = next(e for e in events if e["reqId"] == "JR6")
        assert "new_startDate" not in sixth
        assert sixth["confidence"] == "label"

    def test_moved_start_date_upgrades_to_high(self, monkeypatch):
        monkeypatch.setattr(watch, "REPOST_REFETCH_SLEEP", 0.0)
        monkeypatch.setattr(watch.workday, "detail_payload",
                            lambda b, p, c: {
                                "jobPostingInfo": {
                                    "startDate": "2026-09-12"}})
        # the audit's live-verified case: JR2008226 2026-01-09 → 2026-09-12
        flags = [self._flag("JR2008226", prev_sd="2026-01-09")]
        events, updates = watch.refetch_repost_details(
            flags, "nvidia|wd5|x", Config(), time.monotonic() + 60)
        ev = events[0]
        assert ev["confidence"] == "high"
        assert ev["new_startDate"] == "2026-09-12"
        assert ev["start_delta_days"] == 246
        assert updates == {"JR2008226": "2026-09-12"}

    def test_unchanged_start_date_stays_label_confidence(self, monkeypatch):
        monkeypatch.setattr(watch, "REPOST_REFETCH_SLEEP", 0.0)
        monkeypatch.setattr(watch.workday, "detail_payload",
                            lambda b, p, c: {
                                "jobPostingInfo": {
                                    "startDate": "2026-08-17"}})
        flags = [self._flag("JR1", prev_sd="2026-08-17")]
        events, updates = watch.refetch_repost_details(
            flags, "nvidia|wd5|x", Config(), time.monotonic() + 60)
        assert events[0]["confidence"] == "label"   # no move, not confirmed
        assert events[0]["new_startDate"] == "2026-08-17"  # still recorded
        assert updates == {"JR1": "2026-08-17"}

    def test_unreachable_detail_stays_label(self, monkeypatch):
        monkeypatch.setattr(watch, "REPOST_REFETCH_SLEEP", 0.0)
        monkeypatch.setattr(watch.workday, "detail_payload",
                            lambda b, p, c: None)
        flags = [self._flag("JR1", prev_sd="2026-08-17")]
        events, updates = watch.refetch_repost_details(
            flags, "nvidia|wd5|x", Config(), time.monotonic() + 60)
        assert events[0]["confidence"] == "label"
        assert "new_startDate" not in events[0]
        assert updates == {}

    def test_budget_stop_before_any_fetch(self, monkeypatch):
        monkeypatch.setattr(watch, "REPOST_REFETCH_SLEEP", 0.0)
        calls: list = []

        def fake_detail(board, path, cfg):
            calls.append(path)
            return {"jobPostingInfo": {"startDate": "2026-09-12"}}

        monkeypatch.setattr(watch.workday, "detail_payload", fake_detail)
        flags = [self._flag(f"JR{i}") for i in range(1, 4)]
        events, updates = watch.refetch_repost_details(
            flags, "nvidia|wd5|x", Config(), time.monotonic() - 1)
        assert calls == []                  # budget exhausted → 0 fetches
        assert all(e["confidence"] == "label" for e in events)
        assert updates == {}

    def test_empty_flags_short_circuit(self):
        events, updates = watch.refetch_repost_details(
            [], "nvidia|wd5|x", Config(), time.monotonic() + 60)
        assert events == [] and updates == {}


# ── event merge + canonical log record ───────────────────────────────────
class TestMergeAndLogRecord:
    def test_start_date_evidence_upgrades_existing_flag(self):
        flag = TestRefetchRepostDetails._flag("JR1", prev_sd="2026-08-17")
        extra = {"reqId": "JR1", "confidence": "high",
                 "prev_startDate": "2026-08-17",
                 "new_startDate": "2026-09-11", "start_delta_days": 25}
        merged = watch._merge_repost_events([flag], [extra])
        assert len(merged) == 1
        assert merged[0]["confidence"] == "high"
        assert merged[0]["new_startDate"] == "2026-09-11"
        assert merged[0]["start_delta_days"] == 25
        assert merged[0]["label_reset"] is True   # label evidence kept

    def test_disjoint_events_append(self):
        flag = TestRefetchRepostDetails._flag("JR1")
        extra = {"reqId": "JR2", "confidence": "high",
                 "new_startDate": "2026-09-11", "start_delta_days": 4}
        assert len(watch._merge_repost_events([flag], [extra])) == 2

    def test_canonical_record_shape(self):
        ev = {"reqId": "JR1", "title": "T", "detected_at": "x",
              "prev_postedOn": "Posted 21 Days Ago",
              "new_postedOn": "Posted 2 Days Ago",
              "prev_implied": "2026-08-22", "new_implied": "2026-09-11",
              "prev_startDate": "2026-09-01", "confidence": "high",
              "new_startDate": "2026-09-12", "externalPath": "/job/JR1",
              "label_reset": True, "label_from": "21d", "label_to": "2d",
              "start_delta_days": 11}
        rec = watch._repost_log_record(ev)
        assert set(rec) == {"reqId", "title", "detected_at",
                            "prev_postedOn", "new_postedOn", "prev_implied",
                            "new_implied", "prev_startDate",
                            "new_startDate", "confidence"}
        assert rec["confidence"] == "high"

    def test_new_start_date_omitted_when_unknown(self):
        ev = TestRefetchRepostDetails._flag("JR1")   # no new_startDate
        rec = watch._repost_log_record(ev)
        assert "new_startDate" not in rec
        assert rec["confidence"] == "label"

    def test_magnitude_variants(self):
        ev = {"label_reset": True, "label_from": "21d", "label_to": "2d",
              "start_delta_days": 244}
        assert watch._repost_magnitude(ev) == \
            "label reset 21d→2d, startDate +244d"
        assert watch._repost_magnitude(
            dict(ev, start_delta_days=None)) == "label reset 21d→2d"
        assert watch._repost_magnitude(
            dict(ev, label_reset=False)) == "startDate +244d"
        assert watch._repost_magnitude(
            dict(ev, label_from="30+d", label_to="3d",
                 start_delta_days=None)) == "label reset 30+d→3d"


# ── digest REPOSTED section ──────────────────────────────────────────────
class TestRepostDigest:
    @staticmethod
    def _ev(**kw) -> dict:
        ev = {"reqId": "JR2008226", "title": "Senior DFT Engineer",
              "detected_at": "2026-09-13T10:35:00",
              "prev_postedOn": "Posted 21 Days Ago",
              "new_postedOn": "Posted 2 Days Ago",
              "prev_implied": "2026-08-23", "new_implied": "2026-09-11",
              "prev_startDate": "2026-01-09", "confidence": "high",
              "new_startDate": "2026-09-10", "label_reset": True,
              "label_from": "21d", "label_to": "2d",
              "start_delta_days": 244}
        ev.update(kw)
        return ev

    def test_reposted_section_line(self):
        digest = watch.format_digest("l", "NVIDIA", [], [], {}, [], 5,
                                     repost_events=[self._ev()])
        assert "REPOSTED (1)" in digest
        # task's exact example format: title + magnitude
        assert ("REPOSTED Senior DFT Engineer "
                "(label reset 21d→2d, startDate +244d)") in digest

    def test_no_reposted_section_when_empty(self):
        digest = watch.format_digest("l", "NVIDIA", [], [], {}, [], 5)
        assert "REPOSTED" not in digest
        digest2 = watch.format_digest("l", "NVIDIA", [], [], {}, [], 5,
                                      repost_events=[])
        assert "REPOSTED" not in digest2

    def test_section_capped_at_10(self):
        evs = [self._ev(reqId=f"JR{i}", title=f"T{i}",
                        label_reset=False, start_delta_days=None)
               for i in range(12)]
        digest = watch.format_digest("l", "NVIDIA", [], [], {}, [], 5,
                                     repost_events=evs)
        assert "REPOSTED (12)" in digest
        assert digest.count("REPOSTED T") == 10
        assert "and 2 more (reposts.jsonl)" in digest

    def test_section_after_new_and_gone(self):
        digest = watch.format_digest(
            "l", "NVIDIA",
            new_rows=[_post("JR1", "SRE")],
            enriched=[{"reqId": "JR1", "locations": ["US, CA, Santa Clara"],
                       "startDate": "2026-09-09",
                       "externalUrl": "https://x/JR1"}],
            signals={}, gone_rows=[{"reqId": "JR0", "title": "Old",
                                    "first_seen": "2026-08-01"}],
            current_count=1360, repost_events=[self._ev()])
        assert digest.index("NEW SRE") < digest.index("GONE Old") \
            < digest.index("REPOSTED (1)")


# ── run_watch integration (detector → event log → state → alerts) ────────
class TestRepostRunWatch:
    """End-to-end through run_watch: prior state (old-format rows) + a
    regressed list → reposts.jsonl event, state schema update, REPOSTED
    digest section, alert on a repost-only day. The network is mocked at
    ONE seam (watch.workday.detail_payload), exactly like TestEnrichNew."""

    def _run_watch(self, wdir, monkeypatch, prior_rows, current_rows,
                   detail_sd=None, enrich_out=None):
        state = wdir / "test_watch.state.jsonl"
        state.write_text("\n".join(json.dumps(r) for r in prior_rows) + "\n",
                         encoding="utf-8")
        monkeypatch.setattr(
            watch, "current_postings",
            lambda *a, **k: ({r["reqId"]: r for r in current_rows}, True, False))
        monkeypatch.setattr(watch, "_legs_bump", lambda label: 1)
        monkeypatch.setattr(
            watch, "enrich_new",
            enrich_out if enrich_out is not None else lambda *a, **k: [])
        monkeypatch.setattr(watch, "corroborate_new",
                            lambda *a, **k: {})
        sent: list = []
        monkeypatch.setattr(
            watch, "_send_alerts",
            lambda label, digest, cfg: sent.append(digest))
        monkeypatch.setattr(watch, "_pt_run_date", lambda: RUN_DATE)
        monkeypatch.setattr(watch, "REPOST_REFETCH_SLEEP", 0.0)
        calls: list = []

        def fake_detail(board, path, cfg):
            calls.append(path)
            rid = path.rsplit("/", 1)[-1]
            sd = (detail_sd or {}).get(rid)
            if sd is None:
                return None                     # unreachable
            return {"jobPostingInfo": {"startDate": sd}}

        monkeypatch.setattr(watch.workday, "detail_payload", fake_detail)
        result = watch.run_watch(dict(CFG), Config())
        return result, sent, calls

    def test_label_regression_end_to_end_event_log(self, wdir, monkeypatch):
        prior = [_prior("JR1", "Posted 21 Days Ago",
                        last_startDate="2026-09-01")]
        cur = [_cur("JR1", "Posted 2 Days Ago")]
        result, sent, calls = self._run_watch(
            wdir, monkeypatch, prior, cur, detail_sd={"JR1": "2026-09-12"})
        assert result == "complete"
        assert calls == ["/job/JR1"]           # R2 re-fetch happened
        log = _read_jsonl(wdir / "test_watch.reposts.jsonl")
        assert len(log) == 1
        rec = log[0]
        # canonical event record (task S8-E2 / audit §7.1)
        assert set(rec) == {"reqId", "title", "detected_at",
                            "prev_postedOn", "new_postedOn", "prev_implied",
                            "new_implied", "prev_startDate",
                            "new_startDate", "confidence"}
        assert rec["reqId"] == "JR1"
        assert rec["prev_postedOn"] == "Posted 21 Days Ago"
        assert rec["new_postedOn"] == "Posted 2 Days Ago"
        assert rec["prev_implied"] == "2026-08-22"   # last_seen 09-12 − 21d
        assert rec["new_implied"] == "2026-09-11"    # RUN_DATE − 2d
        assert rec["prev_startDate"] == "2026-09-01"
        assert rec["new_startDate"] == "2026-09-12"  # +11d move → confirmed
        assert rec["confidence"] == "high"
        # alert fires on a REPOST-ONLY day (no new/gone churn at all)
        assert len(sent) == 1
        assert "REPOSTED (1)" in sent[0]
        assert "REPOSTED Role JR1 (label reset 21d→2d, startDate +11d)" \
            in sent[0]
        # state: new fields written, last_startDate now the reset date
        st = {r["reqId"]: r for r in
              _read_jsonl(wdir / "test_watch.state.jsonl")}
        jr1 = st["JR1"]
        assert jr1["last_postedOn"] == "Posted 2 Days Ago"
        assert jr1["implied_post_date"] == "2026-09-11"
        assert jr1["last_startDate"] == "2026-09-12"
        assert jr1["startDate_first"] == "2026-09-01"  # earliest ever seen

    def test_old_format_state_round_trip(self, wdir, monkeypatch):
        """The committed state schema (no implied_post_date /
        startDate_first) must load, compare via the conservative
        last_seen fallback, and only gain new fields where evidence
        exists — dormant rows stay old-format."""
        prior = [
            _prior("JR1", "Posted 21 Days Ago", last_startDate="2026-09-01"),
            _prior("JR2", "Posted 30+ Days Ago"),       # stays OPEN
            _prior("JR3", "Posted 4 Days Ago"),         # ages normally
        ]
        cur = [
            _cur("JR1", "Posted 2 Days Ago"),           # regression → event
            _cur("JR2", "Posted 30+ Days Ago"),         # OPEN→OPEN: quiet
            _cur("JR3", "Posted 5 Days Ago"),           # normal aging
        ]
        result, sent, _ = self._run_watch(
            wdir, monkeypatch, prior, cur, detail_sd={"JR1": "2026-09-12"})
        assert result == "complete"
        log = _read_jsonl(wdir / "test_watch.reposts.jsonl")
        assert [r["reqId"] for r in log] == ["JR1"]     # only the regression
        st = {r["reqId"]: r for r in
              _read_jsonl(wdir / "test_watch.state.jsonl")}
        # active row gains the implied date (numeric bucket)
        assert st["JR3"]["implied_post_date"] == "2026-09-08"   # 09-13−5d
        # OPEN bucket: censored → no implied date; no detail → no first
        assert "implied_post_date" not in st["JR2"]
        assert "startDate_first" not in st["JR2"]
        assert "startDate_first" not in st["JR3"]
        # the re-fetched row gets both new fields
        assert st["JR1"]["startDate_first"] == "2026-09-01"
        assert st["JR1"]["implied_post_date"] == "2026-09-11"
        # round-trip: the rewritten state re-loads cleanly
        assert len(_read_jsonl(wdir / "test_watch.state.jsonl")) == 3

    def test_quiet_day_no_event_log_no_alerts(self, wdir, monkeypatch):
        prior = [_prior("JR1", "Posted 4 Days Ago")]
        cur = [_cur("JR1", "Posted 5 Days Ago")]        # plain aging
        result, sent, calls = self._run_watch(wdir, monkeypatch, prior, cur)
        assert result == "complete"
        assert sent == []                                # no alert spam
        assert calls == []                               # no refetch
        assert not (wdir / "test_watch.reposts.jsonl").exists()

    def test_start_date_move_via_enrichment_channel(self, wdir, monkeypatch):
        """A startDate that moved LATER is a direct reset even when the
        label aged normally: backlog-recovery enrichment re-fetches an
        existing reqId and supplies the new startDate."""
        prior = [_prior("JR1", "Posted 5 Days Ago",
                        last_startDate="2026-09-01", needs_enrich=True)]
        cur = [_cur("JR1", "Posted 5 Days Ago")]        # label is quiet
        enr = [{"reqId": "JR1", "title": "Role JR1",
                "first_seen": "2020-01-01", "locations": ["US, CA, X"],
                "startDate": "2026-09-05", "description": "d", "url": "u"}]
        result, sent, _ = self._run_watch(
            wdir, monkeypatch, prior, cur,
            enrich_out=lambda rows, *a, **k: enr)
        assert result == "complete"
        log = _read_jsonl(wdir / "test_watch.reposts.jsonl")
        assert len(log) == 1
        rec = log[0]
        assert rec["confidence"] == "high"
        assert rec["prev_startDate"] == "2026-09-01"
        assert rec["new_startDate"] == "2026-09-05"
        assert "startDate +4d" in sent[0]
        assert "label reset" not in sent[0]             # startDate-only
        st = {r["reqId"]: r for r in
              _read_jsonl(wdir / "test_watch.state.jsonl")}
        assert st["JR1"]["last_startDate"] == "2026-09-05"
        assert st["JR1"]["startDate_first"] == "2026-09-01"

    def test_refetch_cap_five_per_leg_run_watch(self, wdir, monkeypatch):
        prior = [_prior(f"JR{i}", "Posted 30+ Days Ago",
                        last_startDate="2026-08-01") for i in range(1, 7)]
        cur = [_cur(f"JR{i}", "Posted 2 Days Ago") for i in range(1, 7)]
        result, sent, calls = self._run_watch(
            wdir, monkeypatch, prior, cur,
            detail_sd={f"JR{i}": "2026-09-12" for i in range(1, 7)})
        assert result == "complete"
        assert len(calls) == 5                     # 6 flagged → only 5
        log = _read_jsonl(wdir / "test_watch.reposts.jsonl")
        assert len(log) == 6
        assert sum(1 for r in log if r["confidence"] == "high") == 5
        assert sum(1 for r in log if "new_startDate" in r) == 5
        assert "REPOSTED (6)" in sent[0]
        # digest section holds all 6 (≤10)
        assert sent[0].count("REPOSTED Role JR") == 6

    def test_new_posting_gains_start_date_first(self, wdir, monkeypatch):
        """The newposts flow already fetches details — its startDate lands
        in the state as startDate_first (first observation)."""
        prior = []                                   # JR9 is brand new
        cur = [_cur("JR9", "Posted Yesterday")]
        enr = [{"reqId": "JR9", "title": "Role JR9",
                "first_seen": date.today().isoformat(),
                "locations": ["US, CA, X"], "startDate": "2026-09-12",
                "description": "d", "url": "u"}]
        result, sent, _ = self._run_watch(
            wdir, monkeypatch, prior, cur,
            enrich_out=lambda rows, *a, **k: enr)
        assert result == "complete"
        st = {r["reqId"]: r for r in
              _read_jsonl(wdir / "test_watch.state.jsonl")}
        assert st["JR9"]["startDate_first"] == "2026-09-12"
        assert st["JR9"]["last_startDate"] == "2026-09-12"
        assert st["JR9"]["implied_post_date"] == "2026-09-12"  # 09-13−1d
        assert "REPOSTED" not in sent[0]            # new posting ≠ repost
        assert not (wdir / "test_watch.reposts.jsonl").exists()

    def test_detector_failure_never_kills_the_watch(self, wdir, monkeypatch):
        prior = [_prior("JR1", "Posted 21 Days Ago")]
        cur = [_cur("JR1", "Posted 2 Days Ago")]

        def boom(current, prior, run_date):
            raise RuntimeError("detector exploded")

        monkeypatch.setattr(watch, "detect_reposts", boom)
        result, sent, _ = self._run_watch(
            wdir, monkeypatch, prior, cur, detail_sd={"JR1": "2026-09-12"})
        assert result == "complete"                 # watch still completes
        assert sent == []                           # no repost alert
        assert not (wdir / "test_watch.reposts.jsonl").exists()
        # state still written (with the new implied field)
        st = {r["reqId"]: r for r in
              _read_jsonl(wdir / "test_watch.state.jsonl")}
        assert st["JR1"]["implied_post_date"] == "2026-09-11"


class TestShapeBurnedRowsHeal:
    """S9-audit H3v: the 25 pre-852bb5f rows burned their 3 strikes on
    STATE-shaped rows (no url — guaranteed failures). The strike cap is
    ignored for the url-less error signature; one successful enrichment
    heals the row permanently. BEHAVIORAL pin (H8: the first version
    was a tautology)."""

    def test_urlless_error_at_three_strikes_is_retried(self, wdir,
                                                       monkeypatch):
        feed_prior = [{"reqId": "JR1", "title": "T", "first_seen":
                       "2026-09-01", "error": "detail_unreachable",
                       "url": "", "attempts": 3}]   # shape-burn signature
        got_input: dict = {}

        def fake_enrich(rows, *a, **k):
            got_input["rows"] = list(rows)
            return []

        self._run_once(wdir, monkeypatch, [("JR1", "T")], feed_prior,
                       fake_enrich)
        # the 3-strike cap is BYPASSED for url-less error records —
        # the row re-enters the backlog with its CURRENT (real) row
        assert [r["reqId"] for r in got_input["rows"]] == ["JR1"]
        assert got_input["rows"][0]["url"] == "u"   # real url, not ""

    def _run_once(self, wdir, monkeypatch, board_rows, feed_prior,
                  fake_enrich):
        """Reuse the TestB6ErrorRetry harness (same wdir fixture)."""
        import test_board_watch as tbw
        b6 = tbw.TestB6ErrorRetry()
        b6._run_once(wdir, monkeypatch, board_rows, feed_prior,
                     fake_enrich)


class TestUrlHealRunOnce:
    """S10: bug-era feed records that enriched FINE (details present, no
    error) but carry url='' (pre-852bb5f state-shaped inputs) get their
    url re-stamped from the CURRENT list row — network-free, append-only,
    idempotent. Error records with url='' are NOT this pass's concern
    (shape-burned class → recovery query)."""

    def _run_once(self, wdir, monkeypatch, board_rows, feed_prior,
                  fake_enrich):
        import test_board_watch as tbw
        b6 = tbw.TestB6ErrorRetry()
        return b6._run_once(wdir, monkeypatch, board_rows, feed_prior,
                            fake_enrich)

    @staticmethod
    def _last_by_rid(wdir):
        feed = wdir / "test_watch.newposts.jsonl"
        last: dict = {}
        for line in feed.read_text(encoding="utf-8").splitlines():
            if line.strip():
                last[json.loads(line)["reqId"]] = json.loads(line)
        return last

    def test_buguera_record_healed_from_current_row(self, wdir,
                                                    monkeypatch):
        feed_prior = [
            {"reqId": "JR1", "title": "T1", "first_seen": "2026-09-10",
             "url": "", "description": "D", "locations": ["US, CA, X"],
             "externalUrl": "https://ext/JR1"},          # bug-era, ok
            {"reqId": "JR2", "title": "T2", "first_seen": "2026-09-13",
             "url": "", "error": "detail_unreachable", "attempts": 3},
        ]
        self._run_once(wdir, monkeypatch, [("JR1", "T1"), ("JR2", "T2")],
                       feed_prior, lambda *a, **k: [])
        last = self._last_by_rid(wdir)
        # JR1 healed: url re-stamped, everything else preserved
        assert last["JR1"]["url"] == "u"
        assert last["JR1"]["first_seen"] == "2026-09-10"
        assert last["JR1"]["description"] == "D"
        assert "error" not in last["JR1"]
        # JR2 (error shape-burn) NOT touched by the heal — owned by the
        # recovery query, which re-fed it through enrich_new
        assert last["JR2"]["url"] == ""
        assert last["JR2"].get("error") == "detail_unreachable"

    def test_heal_is_idempotent(self, wdir, monkeypatch):
        feed_prior = [{"reqId": "JR1", "title": "T1",
                       "first_seen": "2026-09-10", "url": "",
                       "description": "D"}]
        self._run_once(wdir, monkeypatch, [("JR1", "T1")], feed_prior,
                       lambda *a, **k: [])
        n_after_first = len((wdir / "test_watch.newposts.jsonl")
                            .read_text(encoding="utf-8").splitlines())
        assert n_after_first == 2                     # prior + healed
        # second pass over the ALREADY-HEALED feed appends nothing
        # (the harness REWRITES the feed from feed_prior — so "no new
        # lines" means: file still holds exactly the prior records)
        healed_prior = list(self._last_by_rid(wdir).values())
        assert len(healed_prior) == 1
        self._run_once(wdir, monkeypatch, [("JR1", "T1")], healed_prior,
                       lambda *a, **k: [])
        lines2 = [l for l in (wdir / "test_watch.newposts.jsonl")
                  .read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines2) == 1                    # nothing appended
        assert json.loads(lines2[0])["url"] == "u"  # healed state kept

    def test_departed_and_urlset_records_untouched(self, wdir,
                                                   monkeypatch):
        feed_prior = [
            {"reqId": "JRGONE", "title": "TG", "first_seen": "2026-09-10",
             "url": "", "description": "D"},           # departed req
            {"reqId": "JROK", "title": "TO", "first_seen": "2026-09-10",
             "url": "https://real/JROK", "description": "D"},  # already ok
        ]
        self._run_once(wdir, monkeypatch, [("JROK", "TO")], feed_prior,
                       lambda *a, **k: [])
        # only JROK is on the board; it already has a url → no heals at
        # all: the file still carries exactly the two prior lines
        lines = [l for l in (wdir / "test_watch.newposts.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 2


class TestShippedWatchConfig:
    """S12 multi-company: the SHIPPED config.json is the operational
    registry — pins its integrity so a bad hand-edit fails loudly here
    instead of at 06:45 UTC on the runner (per-watch try/except there
    makes a config typo a SILENT per-company skip)."""

    def test_config_parses_and_watches_are_well_formed(self):
        cfg = json.loads(
            (REPO_ROOT / "ingest/data/board_watch/config.json")
            .read_text(encoding="utf-8"))
        watches = cfg.get("watches")
        assert isinstance(watches, list) and len(watches) >= 4
        labels = set()
        for w in watches:
            required = ["label", "board", "company", "country"]
            # time_type: REQUIRED for workday boards (the facet exists);
            # OPTIONAL for site specs (greenhouse serves none — an
            # empty value there would be a lie, S13)
            if not site_boards.is_site_spec(w["board"]):
                required.append("time_type")
            for key in required:
                assert w.get(key), f"watch missing {key}: {w}"
            # board spec parses as workday tenant|instance|site, an
            # ATS site ats:kind:org (S13), or an own-platform custom
            # board custom:kind (S14 — org is EMPTY by design: the
            # platform IS the company)
            if site_boards.is_site_spec(w["board"]):
                kind, org = site_boards.parse_site(w["board"])
                if w["board"].startswith("ats:"):
                    assert kind and org, w["board"]
                else:  # custom: own-platform
                    assert kind and not org, w["board"]
                    assert w["board"].startswith("custom:"), w["board"]
            else:
                parts = w["board"].split("|")
                assert len(parts) == 3 and all(parts), w["board"]
            assert w["label"] not in labels, f"duplicate label {w['label']}"
            labels.add(w["label"])
            # optional S12 keys must be lists when present
            for opt in ("li_variants", "slice_locations"):
                if opt in w:
                    assert isinstance(w[opt], list) and w[opt], (
                        f"{w['label']}.{opt} must be a non-empty list")

    def test_stress_test_roster_is_configured(self):
        cfg = json.loads(
            (REPO_ROOT / "ingest/data/board_watch/config.json")
            .read_text(encoding="utf-8"))
        boards = {w["board"] for w in cfg["watches"]}
        # the S12 stress-test tenants (live-verified 2026-09-17)
        assert "nvidia|wd5|nvidiaexternalcareersite" in boards
        assert "netflix|wd108|Netflix" in boards
        assert "tencent|wd1|Tencent_Careers" in boards
        assert "jd|wd103|Careers_at_JD" in boards
        # the S13 ATS stress sites (live-verified 2026-09-19)
        assert "ats:ashby:openai" in boards
        assert "ats:greenhouse:anthropic" in boards
        # the S14 own-platform custom boards (live-verified 2026-09-19)
        assert "custom:bytedance" in boards
        assert "custom:alibaba" in boards
        assert "custom:tripcom" in boards
        # the S15 greenhouse dialect boards (live-verified 2026-09-24)
        assert "ats:greenhouse:baidu" in boards
        assert "ats:greenhouse:byd" in boards
        assert "ats:greenhouse:neteasegames" in boards
        assert "ats:greenhouse:shein" in boards

    def test_roster_is_exactly_twentythree(self):
        """S15 (review #7f) → S16: the EXACT roster count — a watch
        added or lost by a hand-edit fails HERE, not silently on the
        runner. S16 census wires: gea (workday) + xpeng/faradayfuture/
        didi/tcl/gotion (greenhouse) + moonshot (ashby) + weride
        (lever) + tplink/pony (workable)."""
        cfg = json.loads(
            (REPO_ROOT / "ingest/data/board_watch/config.json")
            .read_text(encoding="utf-8"))
        assert len(cfg["watches"]) == 27
        boards = {w["board"] for w in cfg["watches"]}
        for b in ("haier|wd3|GE_Appliances",
                  "ats:greenhouse:xpengmotors",
                  "ats:greenhouse:faradayfuture",
                  "ats:greenhouse:didi", "ats:greenhouse:tcl",
                  "ats:greenhouse:gotion", "ats:ashby:moonshot",
                  "ats:lever:weride", "ats:workable:tp-link-usa-corp",
                  "ats:workable:pony-dot-ai"):
            assert b in boards, b

    def test_she_in_time_type_gating(self):
        """time_type on a site-spec watch row is a CLAIM that the board
        serves a structured employment-type field. S15: only shein's
        greenhouse metadata does (Employment Type); the other three
        greenhouse dialect boards + anthropic must NOT carry the key
        (an empty/unbacked value would be a lie, the S13 rule)."""
        cfg = json.loads(
            (REPO_ROOT / "ingest/data/board_watch/config.json")
            .read_text(encoding="utf-8"))
        by_board = {w["board"]: w for w in cfg["watches"]}
        assert by_board["ats:greenhouse:shein"].get("time_type") == \
            "Full time"
        for board in ("ats:greenhouse:baidu", "ats:greenhouse:byd",
                      "ats:greenhouse:neteasegames",
                      "ats:greenhouse:anthropic",
                      # S16 census wires: no structured timeType on
                      # these greenhouse boards (no metadata key)
                      "ats:greenhouse:xpengmotors",
                      "ats:greenhouse:faradayfuture",
                      "ats:greenhouse:didi", "ats:greenhouse:tcl",
                      "ats:greenhouse:gotion",
                      "ats:ashby:moonshot"):
            assert "time_type" not in by_board[board], board
        # S16: lever/workable DO serve structured commitment/employment
        # types — the time_type claim is backed
        assert by_board["ats:lever:weride"].get("time_type") == "Full time"
        assert by_board["ats:workable:tp-link-usa-corp"].get("time_type") \
            == "Full time"
        assert by_board["ats:workable:pony-dot-ai"].get("time_type") == \
            "Full time"

    def test_run_watch_registers_overrides(self, tmp_path, monkeypatch):
        """li_variants/slice_locations from the watch dict reach the
        corroborate registries before any corroboration runs."""
        w = dict(CFG, li_variants=["NVIDIA", "NVIDIA AI"],
                 slice_locations=["United States"])
        monkeypatch.setattr(watch, "WATCH_DIR", tmp_path)
        # fail the list fetch immediately — we only need the registration
        # side effect before the early return
        def boom(*a, **k):
            raise RuntimeError("stop here")
        monkeypatch.setattr(watch, "current_postings", boom)
        import jobsearch.corroborate as corr
        saved_v, saved_l = (dict(corr.COMPANY_VARIANTS),
                            dict(corr.SLICE_LOCATIONS))
        try:
            assert watch.run_watch(w, Config()) == "failed"
            assert corr.COMPANY_VARIANTS.get("nvidia") == [
                "nvidia", "nvidia ai"]
            assert corr.SLICE_LOCATIONS.get("nvidia") == ["United States"]
        finally:
            corr.COMPANY_VARIANTS.clear()
            corr.COMPANY_VARIANTS.update(saved_v)
            corr.SLICE_LOCATIONS.clear()
            corr.SLICE_LOCATIONS.update(saved_l)


# ── S13: client-country classification (detail-based) ────────────────────
class TestRunWatchClientCountry:
    """Boards with no country facet list the FULL GLOBAL board; the
    classification piggybacks on enrich_new's detail fetch (the feed
    record's `country` field). Only US rows enter state/new/digest;
    foreign rows live in the feed for audit; pending rows retrigger."""

    CC_CFG = {"label": "test_watch", "board": "netflix|wd108|Netflix",
              "company": "Netflix", "country": "United States",
              "time_type": "Full time"}

    def _run(self, wdir, monkeypatch, prior, current, enrich_map,
             complete=True, legs=1, corroborate_seen=None,
             feed_prior=None, fetch_seen=None):
        state = wdir / "test_watch.state.jsonl"
        if prior:
            state.write_text("\n".join(json.dumps(r) for r in prior) + "\n",
                             encoding="utf-8")
        if feed_prior:
            (wdir / "test_watch.newposts.jsonl").write_text(
                "\n".join(json.dumps(r) for r in feed_prior) + "\n",
                encoding="utf-8")
        monkeypatch.setattr(
            watch, "current_postings",
            lambda *a, **k: ({p["reqId"]: p for p in current},
                             complete, True))
        monkeypatch.setattr(watch, "_legs_bump", lambda l: legs)
        # enrich_new returns feed records carrying the detail country;
        # fetch_seen records the rows HANDED to it (the fetch budget)
        fs = [] if fetch_seen is None else fetch_seen

        def _fake_enrich(rows, *a, **k):
            fs.extend(r["reqId"] for r in rows)
            return [dict(enrich_map[r["reqId"]],
                         reqId=r["reqId"],
                         title=r.get("title") or "",
                         first_seen="2026-09-10",
                         url=r.get("url", ""))
                    for r in rows
                    if r["reqId"] in enrich_map]
        monkeypatch.setattr(watch, "enrich_new", _fake_enrich)
        seen = []
        if corroborate_seen is None:
            corroborate_seen = seen.append
        monkeypatch.setattr(
            watch, "corroborate_new",
            lambda rows, *a, **k: (corroborate_seen(rows) or {}))
        monkeypatch.setattr(watch, "_send_alerts", lambda *a, **k: None)
        return watch.run_watch(dict(self.CC_CFG), Config())

    def test_us_kept_foreign_excluded_pending_retriggers(self, wdir,
                                                         monkeypatch):
        prior = [{"reqId": "JR0", "title": "Stay", "first_seen":
                  "2026-09-01", "last_seen": "2026-09-09"}]
        current = [_post("JR0", "Stay"),          # prior (US)
                   _post("JR1", "US city"),       # classified US
                   _post("JR2", "Seoul"),         # classified foreign
                   _post("JR3", "pending")]       # not enriched this leg
        enrich_map = {
            "JR1": {"country": "united states of america",
                    "locations": ["Los Gatos"], "startDate": "2026-09-09",
                    "description": "d"},
            "JR2": {"country": "south korea",
                    "locations": ["Seoul"], "startDate": "2026-09-09",
                    "description": "d"},
        }
        result = self._run(wdir, monkeypatch, prior, current, enrich_map)
        assert result == "backlog"     # JR3 pending → retrigger
        state = [json.loads(x) for x in
                 (wdir / "test_watch.state.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert {r["reqId"] for r in state} == {"JR0", "JR1"}  # US only
        # the feed carries the foreign record for audit
        feed = [json.loads(x) for x in
                (wdir / "test_watch.newposts.jsonl").read_text(
                    encoding="utf-8").splitlines()]
        assert {r["reqId"] for r in feed} == {"JR1", "JR2"}
        jr2 = next(r for r in feed if r["reqId"] == "JR2")
        assert jr2["country"] == "south korea"

    def test_corroborate_never_sees_foreign_rows(self, wdir, monkeypatch):
        current = [_post("JR1", "US city"), _post("JR2", "Seoul")]
        enrich_map = {"JR1": {"country": "united states of america"},
                      "JR2": {"country": "south korea"}}
        seen = []
        self._run(wdir, monkeypatch, [], current, enrich_map,
                  corroborate_seen=seen.append)
        seen_flat = [r["reqId"] for rows in seen for r in rows]
        assert "JR2" not in seen_flat
        assert "JR1" in seen_flat

    def test_three_strike_falls_back_to_tokens(self, wdir, monkeypatch):
        # detail failed 3x → token predicate decides (S12 behavior for
        # that tail only); 'US, CA' passes, 'Seoul' does not
        jr9 = _post("JR9", "US city")
        jr8 = _post("JR8", "Seoul")
        jr8["locationsText"] = "Seoul, Seoul, South Korea"  # token ≠ US
        current = [jr9, jr8]
        enrich_map = {
            "JR9": {"error": "detail_unreachable", "attempts": 3,
                    "country": ""},
            "JR8": {"error": "detail_unreachable", "attempts": 3,
                    "country": ""},
        }
        result = self._run(wdir, monkeypatch, [], current, enrich_map)
        assert result == "complete"
        state = [json.loads(x) for x in
                 (wdir / "test_watch.state.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert {r["reqId"] for r in state} == {"JR9"}

    def test_no_new_rows_completes(self, wdir, monkeypatch):
        prior = [{"reqId": "JR0", "title": "Stay",
                  "first_seen": "2026-09-01", "last_seen": "2026-09-09"}]
        result = self._run(wdir, monkeypatch, prior,
                           [_post("JR0", "Stay")], {})
        assert result == "complete"
        state = [json.loads(x) for x in
                 (wdir / "test_watch.state.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert {r["reqId"] for r in state} == {"JR0"}

    def test_lag_pass_skips_foreign_feed_rows(self, wdir, monkeypatch):
        # feed carries a fresh foreign record without signals — the
        # cross-post-lag pass must NOT corroborate it
        feed = wdir / "test_watch.newposts.jsonl"
        feed.write_text(json.dumps({
            "reqId": "JRF", "title": "Foreign", "first_seen":
            date.today().isoformat(), "url": "u", "country": "canada"
        }) + "\n", encoding="utf-8")
        current = [_post("JR1", "US city")]
        enrich_map = {"JR1": {"country": "united states of america"}}
        seen = []
        self._run(wdir, monkeypatch, [], current, enrich_map,
                  corroborate_seen=seen.append)
        # second corroborate call (lag pass) — only via feed recent rows;
        # JRF must never appear in any corroborate input
        for rows in seen:
            assert all(r["reqId"] != "JRF" for r in rows)

    # ── S14: the one-fetch contract (run #38 live-validation fix) ─────────

    def test_feed_classified_foreign_dequeued(self, wdir, monkeypatch):
        # the S13 comment said 'foreign rows cost exactly ONE detail
        # fetch each' — the code re-candidated them every run (run #38:
        # netflix 5 days, 214/393 classified, a stable foreign head
        # starved the queue). A feed-classified-foreign row must NOT be
        # a candidate: not fetched, not in state, run completes.
        feed_prior = [{"reqId": "JR2", "title": "Seoul",
                       "first_seen": "2026-09-01", "url": "u",
                       "country": "south korea"}]
        fetch = []
        result = self._run(wdir, monkeypatch, [],
                           [_post("JR1", "US city"), _post("JR2", "Seoul")],
                           {"JR1": {"country":
                                    "united states of america"}},
                           feed_prior=feed_prior, fetch_seen=fetch)
        assert result == "complete"
        assert "JR2" not in fetch          # never re-fetched (the fix)
        state = [json.loads(x) for x in
                 (wdir / "test_watch.state.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert {r["reqId"] for r in state} == {"JR1"}
        # the audit record survives in the feed untouched
        feed = [json.loads(x) for x in
                (wdir / "test_watch.newposts.jsonl").read_text(
                    encoding="utf-8").splitlines()]
        jr2 = [r for r in feed if r["reqId"] == "JR2"]
        assert jr2 and jr2[0]["country"] == "south korea"

    def test_no_country_usa_remote_classifies_via_tokens(self, wdir,
                                                         monkeypatch):
        # the netflix 'USA - Remote' class: detail payload carries NO
        # jobPostingInfo.country (108 rows pending-forever at S14
        # validation). The record's own location tokens decide — US
        # token → state, without any re-fetch.
        feed_prior = [{"reqId": "JRR", "title": "Remote",
                       "first_seen": "2026-09-19", "url": "u",
                       "locationsText": "USA - Remote",
                       "locations": ["USA - Remote"], "startDate":
                       "2026-09-19", "description": "d"}]
        fetch = []
        result = self._run(wdir, monkeypatch, [],
                           [_post("JRR", "Remote")], {},
                           feed_prior=feed_prior, fetch_seen=fetch)
        assert result == "complete"
        state = [json.loads(x) for x in
                 (wdir / "test_watch.state.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert {r["reqId"] for r in state} == {"JRR"}  # rescued

    def test_no_country_foreign_location_unresolved_not_refetched(
            self, wdir, monkeypatch):
        # no country + non-US locations: NEVER guessed-foreign and
        # NEVER guessed-US — the row stays out of state, auditable in
        # the feed, and (one-fetch contract) is not re-fetched: its
        # existing record already answered "unresolved from available
        # data". Live boards have ZERO such rows (all 185 no-country
        # records carry 'USA - …' locations); if one ever appears the
        # honest state is complete-with-unresolved, not an infinite
        # retrigger loop.
        feed_prior = [{"reqId": "JRP", "title": "Seoul",
                       "first_seen": "2026-09-19", "url": "u",
                       "locationsText": "Seoul, South Korea",
                       "locations": ["Seoul"], "startDate":
                       "2026-09-19", "description": "d"}]
        jr = _post("JRP", "Seoul")
        jr["locationsText"] = "Seoul, South Korea"
        fetch = []
        result = self._run(wdir, monkeypatch, [], [jr], {},
                           feed_prior=feed_prior, fetch_seen=fetch)
        assert result == "complete"         # unresolved ≠ retrigger
        assert fetch == []                  # one-fetch contract
        state = [json.loads(x) for x in
                 (wdir / "test_watch.state.jsonl").read_text(
                     encoding="utf-8").split("\n") if x.strip()]
        assert state == []                  # never entered state

    def test_prior_feed_us_verdict_rescues_starved_row(self, wdir,
                                                       monkeypatch):
        # a row classified US in a PRIOR leg but starved beyond every
        # fetch head since: today's classification must consult the
        # last feed record — verdict kept, state converges with ZERO
        # network cost (the state-160-vs-369 gap closes in one run)
        feed_prior = [{"reqId": "JRS", "title": "US city",
                       "first_seen": "2026-09-19", "url": "u",
                       "country": "united states of america",
                       "startDate": "2026-09-19",
                       "locationsText": "US, CA, Santa Clara"}]
        fetch = []
        result = self._run(wdir, monkeypatch, [],
                           [_post("JRS", "US city")], {},
                           feed_prior=feed_prior, fetch_seen=fetch)
        assert result == "complete"
        state = [json.loads(x) for x in
                 (wdir / "test_watch.state.jsonl").read_text(
                     encoding="utf-8").split("\n") if x.strip()]
        assert {r["reqId"] for r in state} == {"JRS"}
        rec = state[0]
        # review amendments: the feed record IS the enrichment evidence
        assert rec["first_seen"] == "2026-09-19"   # tenure preserved
        assert rec["last_startDate"] == "2026-09-19"
        assert rec["startDate_first"] == "2026-09-19"
        assert "needs_enrich" not in rec           # resolved, not pending
