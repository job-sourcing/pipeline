"""Tests for jobsearch.corroborate — the corroboration signals module.

Covers (per design-board-v2.md + peer review B2/B5):
- fetch_signals' no-raise contract: exactly one record per card, circuit
  breaker marks the REMAINder blocked (error='circuit_open')
- index_cards B2 stepping: `start` advances by cards received
- company-variant filtering (NVIDIA / NVIDIA AI)
- join_by_req_id (exact) and join_by_title (fallback) semantics
- blocked record shape (B5: blocked ≠ no_match)
"""
from __future__ import annotations

import time as time_mod

import pytest

from jobsearch import corroborate
from jobsearch.corroborate import (
    LinkedInSignalProvider, STATUS_BLOCKED, STATUS_MATCHED,
    STATUS_NO_MATCH, STATUS_NOT_CHECKED, join_by_req_id, join_by_title,
)


def _card(i: int, company: str = "NVIDIA", title: str = "Engineer",
          date: str = "2026-09-08") -> dict:
    i = str(i)
    return {
        "id": i,
        "title": title,
        "company": company,
        "location": "Santa Clara, CA",
        "date": date,
        "url": f"https://www.linkedin.com/jobs/view/{i}",
    }


class _FlakyFetch:
    """Stand-in for linkedin_guest.fetch_detail: raises for given ids."""

    def __init__(self, fail_ids: set[str], results: dict | None = None):
        self.fail_ids = fail_ids
        self.results = results or {}
        self.calls: list[str] = []

    def __call__(self, job_id, cfg=None):
        self.calls.append(job_id)
        if job_id in self.fail_ids:
            raise RuntimeError(f"blocked {job_id}")
        return self.results.get(job_id, {
            "work_mode": "", "description": "desc", "closed": False,
            "num_applicants": 5, "applicants_label": "5 applicants",
            "job_req_id": "JR2026100", "posted_time_ago": "2 days ago",
        })


class TestFetchSignals:
    def test_one_record_per_card_never_raises(self, monkeypatch):
        p = LinkedInSignalProvider(cfg=None)
        flaky = _FlakyFetch(fail_ids={"1"})
        monkeypatch.setattr(corroborate.linkedin_guest, "fetch_detail",
                            flaky)
        cards = [_card(1), _card(2), _card(3)]
        recs = p.fetch_signals(cards, detail_pause_s=0)
        assert len(recs) == 3          # exactly one per card
        assert recs[0]["status"] == STATUS_BLOCKED
        assert recs[0]["error"] == "RuntimeError"
        assert recs[1]["status"] == STATUS_MATCHED
        assert recs[2]["num_applicants"] == 5

    def test_circuit_breaker_marks_rest_blocked(self, monkeypatch):
        p = LinkedInSignalProvider(cfg=None)
        flaky = _FlakyFetch(fail_ids={"1", "2", "3"})   # 3 consecutive
        monkeypatch.setattr(corroborate.linkedin_guest, "fetch_detail",
                            flaky)
        cards = [_card(i) for i in range(1, 7)]
        recs = p.fetch_signals(cards, detail_pause_s=0)
        assert len(recs) == 6
        # first 3 blocked by exception; the breaker trips at 3, the rest
        # get circuit_open records without a network call
        assert flaky.calls == ["1", "2", "3"]            # only 3 attempts
        assert recs[3]["error"] == "circuit_open"
        assert recs[5]["status"] == STATUS_BLOCKED

    def test_blocked_record_has_all_fields(self, monkeypatch):
        p = LinkedInSignalProvider(cfg=None)
        flaky = _FlakyFetch(fail_ids={"9"})
        monkeypatch.setattr(corroborate.linkedin_guest, "fetch_detail",
                            flaky)
        recs = p.fetch_signals([_card(9)], detail_pause_s=0)
        assert set(recs[0]) == {
            "linkedin_job_id", "linkedin_url", "title", "company",
            "location", "linkedin_posted_date", "num_applicants",
            "applicants_label", "job_req_id", "posted_time_ago", "closed",
            "status", "error", "fetched_at"}

    def test_fetched_at_iso_valid_and_callback_fires(self, monkeypatch):
        """fetched_at on EVERY record (design D1 col 29) + per-record
        callback (per-card checkpointing for callers)."""
        p = LinkedInSignalProvider(cfg=object())   # cfg unused under mock
        seen: list[dict] = []
        monkeypatch.setattr(
            "jobsearch.sources.linkedin_guest.fetch_detail",
            lambda cid, cfg=None: {"num_applicants": 5,
                                   "applicants_label": "5 applicants",
                                   "job_req_id": "JR1",
                                   "posted_time_ago": "2 days ago"})
        monkeypatch.setattr(time_mod, "sleep", lambda s: None)
        recs = p.fetch_signals([_card(1)], detail_pause_s=0,
                               on_record=seen.append)
        import datetime as dt
        dt.datetime.fromisoformat(recs[0]["fetched_at"])   # raises if bad
        assert seen == recs                               # fired per record

    def test_success_record_extracts_all_signals(self, monkeypatch):
        p = LinkedInSignalProvider(cfg=None)
        detail = {
            "work_mode": "Remote", "description": "d", "closed": False,
            "num_applicants": 122, "applicants_label": "122 applicants",
            "job_req_id": "JR2023808", "posted_time_ago": "2 days ago"}
        flaky = _FlakyFetch(set(), {"1": detail})
        monkeypatch.setattr(corroborate.linkedin_guest, "fetch_detail",
                            flaky)
        rec = p.fetch_signals([_card(1)], detail_pause_s=0)[0]
        assert rec["status"] == STATUS_MATCHED
        assert rec["num_applicants"] == 122
        assert rec["job_req_id"] == "JR2023808"
        assert rec["linkedin_posted_date"] == "2026-09-08"


class TestIndexCards:
    def _patch_pages(self, monkeypatch, pages: list[list[dict]]):
        """fetch_text returns canned search HTML pages in order."""
        from jobsearch.sources.linkedin_guest import _parse_search_results

        def _card_html(c):
            return (f'<li data-entity-urn="urn:li:jobPosting:{c["id"]}">'
                    f'<h3 class="base-search-card__title">{c["title"]}</h3>'
                    f'<h4 class="base-search-card__subtitle">'
                    f'<a>{c["company"]}</a></h4>'
                    f'<span class="job-search-card__location">'
                    f'{c["location"]}</span>'
                    f'<time datetime="{c["date"]}">now</time></li>')

        page_htmls = ["".join(_card_html(c) for c in pg) for pg in pages]
        seen_offsets: list[int] = []
        state = {"i": 0}

        def fake_fetch_text(url, *, params=None, cfg=None):
            assert "start=" in url
            offset = int(url.split("start=")[1].split("&")[0])
            seen_offsets.append(offset)
            if state["i"] >= len(page_htmls):
                return ""                     # exhausted (empty body)
            html = page_htmls[state["i"]]
            state["i"] += 1
            return html

        monkeypatch.setattr(corroborate, "fetch_text", fake_fetch_text)
        return seen_offsets

    def test_b2_step_by_cards_received(self, monkeypatch):
        pages = [[_card(i) for i in range(10)],
                 [_card(i + 10) for i in range(10)],
                 [_card(i + 20) for i in range(4)]]
        offsets = self._patch_pages(monkeypatch, pages)
        monkeypatch.setattr(corroborate, "_INDEX_PAUSE_S", 0)
        p = LinkedInSignalProvider(cfg=None)
        cards, next_offset, exhausted = p.index_cards(
            "NVIDIA", max_pages=10, max_cards=100)
        # offsets advance 0 → 10 → 20 (cards RECEIVED), never 25-steps;
        # the trailing 24 probe is the empty-body exhaustion check
        assert offsets == [0, 10, 20, 24]
        assert len(cards) == 24
        assert exhausted
        assert next_offset == 24

    def test_company_variant_filter(self, monkeypatch):
        pages = [[_card(1, company="NVIDIA"),
                  _card(2, company="NVIDIA AI"),
                  _card(3, company="Some Other Co")]]
        self._patch_pages(monkeypatch, pages)
        monkeypatch.setattr(corroborate, "_INDEX_PAUSE_S", 0)
        p = LinkedInSignalProvider(cfg=None)
        cards, _, _ = p.index_cards("NVIDIA", max_pages=1)
        assert {c["company"] for c in cards} == {"NVIDIA", "NVIDIA AI"}

    def test_max_cards_bound(self, monkeypatch):
        pages = [[_card(i + pg * 10) for i in range(10)]
                 for pg in range(5)]
        self._patch_pages(monkeypatch, pages)
        monkeypatch.setattr(corroborate, "_INDEX_PAUSE_S", 0)
        p = LinkedInSignalProvider(cfg=None)
        cards, _, _ = p.index_cards("NVIDIA", max_pages=10, max_cards=15)
        assert len(cards) == 15

    def test_first_page_blocked_raises(self, monkeypatch):
        def fake(url, *, params=None, cfg=None):
            raise RuntimeError("403 wall")
        monkeypatch.setattr(corroborate, "fetch_text", fake)
        p = LinkedInSignalProvider(cfg=None)
        with pytest.raises(corroborate.CorroborationBlocked):
            p.index_cards("NVIDIA", max_pages=5)


class TestJoins:
    def test_join_by_req_id_exact_only(self):
        signals = [
            {"job_req_id": "JR2026100", "title": "A"},
            {"job_req_id": "JR9999999", "title": "B"},
            {"job_req_id": "", "title": "C"},
        ]
        joined = join_by_req_id(signals, {"JR2026100"})
        assert set(joined) == {"JR2026100"}

    def test_join_by_title_normalized(self):
        signals = [{
            "title": "Senior Engineer - DGX Cloud",
            "company": "NVIDIA",
        }]
        # job_key strips trailing " - …" segments (dash pattern)
        postings = {"JR1": "Senior Engineer", "JR2": "Totally Different"}
        joined = join_by_title(signals, postings, "NVIDIA")
        assert "JR1" in joined
        assert "JR2" not in joined

    def test_blocked_never_joins(self):
        """Blocked records carry NO signal (the fetch failed) — they must
        not corroborate a posting by reqId, even when a job_req_id is
        present. This test spent its life asserting NOTHING (audit
        S7-B3 A1: body was a compute + a comment); the honest contract
        is now implemented in join_by_req_id and pinned here."""
        signals = [{"job_req_id": "JR2026100", "title": "A",
                    "status": STATUS_BLOCKED},
                   {"job_req_id": "JR2026100", "title": "A",
                    "status": STATUS_MATCHED, "linkedin_job_id": "9"}]
        # blocked alone joins nothing — even with a reqId to match
        assert join_by_req_id([signals[0]], {"JR2026100"}) == {}
        # blocked never shadows a genuine match for the same reqId
        # (first-wins used to let the blocked record win)
        joined = join_by_req_id(signals, {"JR2026100"})
        assert joined == {"JR2026100": signals[1]}

    def test_blocked_joins_by_title_only_as_status_payload(self):
        """The deliberate asymmetry: join_by_title is the path
        phase_finish uses to surface `blocked` for postings whose card
        fetch was walled (B5) — the record joins carrying its status,
        never a fabricated signal (num_applicants is None on blocked)."""
        blocked = {"job_req_id": "JR2026100", "title": "A",
                   "company": "NVIDIA", "linkedin_job_id": "9",
                   "num_applicants": None, "status": STATUS_BLOCKED}
        joined = join_by_title([blocked], {"JR2026100": "A"}, "NVIDIA")
        assert joined["JR2026100"]["status"] == STATUS_BLOCKED
        assert joined["JR2026100"]["num_applicants"] is None


class TestRegistry:
    def test_get_provider(self):
        assert corroborate.get_provider("linkedin").name == "linkedin"

    def test_unknown_provider(self):
        with pytest.raises(ValueError, match="unknown corroboration"):
            corroborate.get_provider("nope")

    def test_provider_defaults_to_real_config(self, monkeypatch):
        """Regression (2026-09-10): board_dump calls get_provider() with no
        cfg; cfg=None crashed fetch_text (None.http_timeout_s) on the LIVE
        path — tests had fetch_text mocked so the suite stayed green."""
        p = corroborate.get_provider("linkedin")   # no cfg
        assert p.cfg is not None
        assert hasattr(p.cfg, "http_timeout_s")
        # the live path itself: index_cards must reach the HTTP layer,
        # not die on cfg dereference
        seen: dict = {}

        def fake_fetch(url, cfg=None, **kw):
            seen["cfg"] = cfg
            raise corroborate.CorroborationBlocked("probe stop")

        monkeypatch.setattr(corroborate, "fetch_text", fake_fetch)
        with pytest.raises(corroborate.CorroborationBlocked):
            p.index_cards(company="NVIDIA", max_pages=1)
        assert seen["cfg"] is p.cfg   # real Config passed through


class TestJoinOneToOne:
    """Regression (S7-A2 finding B): title-join must be GREEDY 1:1 — the
    shipped CSV had one LinkedIn card's applicant count stamped on 31
    different reqs (N:1 fanout)."""

    def _sig(self, cid: str, title: str, date: str) -> dict:
        return {"linkedin_job_id": cid, "linkedin_url": f"u/{cid}",
                "title": title, "company": "NVIDIA", "location": "x",
                "linkedin_posted_date": date, "num_applicants": 32,
                "status": "matched"}

    def test_one_card_never_fans_out(self):
        sigs = [self._sig("1", "Senior System Software Engineer - Sim", "2026-09-08")]
        posts = {f"JR{i}": f"Senior System Software Engineer - {n}"
                 for i, n in enumerate(["A", "B", "C", "D"], start=1)}
        joined = join_by_title(sigs, posts, "NVIDIA")
        assert len(joined) == 1                    # exactly one req gets it
        assert list(joined.values())[0]["num_applicants"] == 32

    def test_proximity_wins_two_cards(self):
        sigs = [self._sig("1", "SRE - X", "2026-01-01"),
                self._sig("2", "SRE - X", "2026-09-09")]
        posts = {"JR1": "SRE - X"}
        joined = join_by_title(sigs, posts, "NVIDIA",
                               req_dates={"JR1": "2026-09-08"})
        assert list(joined.values())[0]["linkedin_job_id"] == "2"

    def test_two_cards_two_reqs_full_assignment(self):
        # NB: titles must normalize to DIFFERENT keys — job_key strips
        # " - suffix" families to one key, so "SRE - X"/"SRE - Y" are the
        # SAME key (that prefix-collapse is what the 1:1 greedy guards).
        sigs = [self._sig("1", "SRE CUDA", "2026-09-01"),
                self._sig("2", "SRE Driver", "2026-09-02")]
        posts = {"JRA": "SRE CUDA", "JRB": "SRE Driver"}
        joined = join_by_title(sigs, posts, "NVIDIA")
        assert set(joined) == {"JRA", "JRB"}
        assert joined["JRA"]["linkedin_job_id"] == "1"
        assert joined["JRB"]["linkedin_job_id"] == "2"
