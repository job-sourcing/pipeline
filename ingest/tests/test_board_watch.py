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
- budget guards: legs counter date-keyed reset; backlog verdict only
  while unenriched remain AND legs remain
- digest shape: NEW lines w/ applicants, GONE lines, header counts
- seed_state: B3 seeding + refuses to clobber an existing state
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
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
        rows, complete = watch.current_postings(
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
        rows, complete = watch.current_postings(
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
        rows, complete = watch.current_postings(
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
    the other thing mocked-away tests can't see)."""

    name = "linkedin"

    def __init__(self, cards, sigs, index_exc=None):
        self.cards, self.sigs, self.index_exc = cards, sigs, index_exc
        self.index_calls: list[dict] = []
        self.fetch_calls: list[list] = []

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


class TestCorroborateNew:
    """corroborate_new had ZERO direct tests (always stubbed). Real code
    under test: get_provider seam + kwargs, REAL linkedin_guest.job_key
    normalization, reqId-exact override, blocked-index no-raise, matched
    -only signal use."""

    def _run(self, monkeypatch, provider, rows, cfg=None,
             deadline=None):
        got: dict = {}

        def fake_get(name, cfg=None):
            got["name"], got["cfg"] = name, cfg
            return provider

        monkeypatch.setattr(watch.corroborate, "get_provider",
                            fake_get)
        import time as _t
        cfg = cfg or Config()
        return (watch.corroborate_new(
            rows, "NVIDIA", cfg,
            deadline if deadline is not None else _t.monotonic() + 60),
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
            lambda *a, **k: ({p["reqId"]: p for p in current}, complete))
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
                                {"JR9": _post("JR9", "Fresh")}, True))
        monkeypatch.setattr(watch, "_legs_bump", lambda l: 1)
        monkeypatch.setattr(watch, "enrich_new", lambda *a, **k: [])
        # new-corroboration returns nothing; lag pass must hit JR9
        monkeypatch.setattr(
            watch, "corroborate_new",
            lambda rows, company, cfg, deadline: (
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
            return dict(board), True

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
        rows, complete = watch.current_postings(
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
                              for p, t in current}, True))
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
        feed_prior = [{"reqId": "JR1", "title": "T", "first_seen":
                       "2026-09-01", "error": "detail_unreachable",
                       "attempts": 3}]
        got_input: dict = {}

        def fake_enrich(rows, *a, **k):
            got_input["rows"] = list(rows)
            return []

        self._run_once(wdir, monkeypatch, [("JR1", "T")], feed_prior,
                       fake_enrich)
        assert got_input["rows"] == []          # 3 strikes → skipped


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
