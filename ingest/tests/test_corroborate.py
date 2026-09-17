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

    def test_malformed_card_skipped_not_crash(self, monkeypatch, capsys):
        """S9-audit D3 P3: a card dict missing a required key (or not a
        dict at all) must SKIP with a stderr note — never crash the
        batch (the no-raise contract). Well-formed cards keep the
        one-in-one-out record contract."""
        p = LinkedInSignalProvider(cfg=None)
        monkeypatch.setattr(
            corroborate.linkedin_guest, "fetch_detail",
            _FlakyFetch(fail_ids=set()))
        cards = [
            {"id": "1", "title": "T", "url": "u", "location": "x"},
            "not a dict",
            _card(2),
        ]
        recs = p.fetch_signals(cards, detail_pause_s=0)
        assert len(recs) == 1                 # only the well-formed card
        assert recs[0]["linkedin_job_id"] == "2"
        assert recs[0]["status"] == STATUS_MATCHED
        err = capsys.readouterr().err
        assert "malformed index card" in err
        assert "company" in err               # names the missing key(s)

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
        # offsets advance 0 → 10 → 20 (cards RECEIVED), never 25-steps.
        # S9-audit D3 fix: the trailing 4-card page is SHORT — genuine
        # exhaustion, no empty-body probe past it (the old probe read a
        # soft wall at offset 24 as done=true; empty 200 pages are now
        # blocked_empty, see the retryable pins below).
        assert offsets == [0, 10, 20]
        assert len(cards) == 24
        assert exhausted
        assert next_offset == 24

    def test_empty_page_after_full_page_is_retryable_not_done(self,
                                                             monkeypatch):
        """S9-audit D3 P2 pin: an HTTP-200 page with ZERO cards after ≥1
        served page is soft-wall AMBIGUOUS — the index must report it as
        retryable (exhausted=False so the phase resumes at the offset,
        or blocked), NEVER as terminal done=true."""
        pages = [[_card(i) for i in range(10)], []]
        offsets = self._patch_pages(monkeypatch, pages)
        monkeypatch.setattr(corroborate, "_INDEX_PAUSE_S", 0)
        p = LinkedInSignalProvider(cfg=None)
        cards, next_offset, exhausted = p.index_cards(
            "NVIDIA", max_pages=10, max_cards=100)
        assert offsets == [0, 10]             # page 0 full → page 1 empty
        assert len(cards) == 10               # partial cards KEPT
        assert exhausted is False             # ← the inversion fix
        assert next_offset == 10              # resume point for retry

    def test_empty_first_page_raises_blocked_empty(self, monkeypatch):
        """Mirrors search_title's blocked_empty doctrine: a 200-empty
        page 0 with nothing gained raises CorroborationBlocked (caller
        marks the phase blocked, B5) instead of done=true with 0 cards."""
        self._patch_pages(monkeypatch, [[]])
        p = LinkedInSignalProvider(cfg=None)
        with pytest.raises(corroborate.CorroborationBlocked,
                           match="blocked_empty"):
            p.index_cards("NVIDIA", max_pages=5)

    def test_short_page_is_genuine_exhaustion_even_at_card_cap(
            self, monkeypatch):
        """A short tail page sets done=true even when the card cap is
        simultaneously reached — the serving window IS complete, so the
        meta must not re-open the index next run."""
        pages = [[_card(i) for i in range(10)],
                 [_card(i + 10) for i in range(4)]]
        self._patch_pages(monkeypatch, pages)
        monkeypatch.setattr(corroborate, "_INDEX_PAUSE_S", 0)
        p = LinkedInSignalProvider(cfg=None)
        cards, _, exhausted = p.index_cards(
            "NVIDIA", max_pages=10, max_cards=12)
        assert len(cards) == 12               # trimmed to the cap
        assert exhausted is True              # short tail page = done

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
        # S9-audit (B1): job_key strips dash/paren suffixes, which used
        # to equate specializations — the verbatim guard now rejects
        # 'Senior Engineer' vs 'Senior Engineer - DGX Cloud' (different
        # requisitions per GT). Punctuation/case variants still join.
        signals = [{
            "title": "Senior Engineer, DGX Cloud",
            "company": "NVIDIA",
        }]
        postings = {"JR1": "Senior Engineer - DGX Cloud",
                    "JR2": "Totally Different"}
        joined = join_by_title(signals, postings, "NVIDIA")
        assert "JR1" in joined
        assert "JR2" not in joined

    def test_join_by_title_rejects_specialization_variants(self):
        """S9-audit B1 P1 pin: a key-equal pair that is NOT verbatim
        (the req is the base title, the card a specialization — 108/115
        live F1-failures were exactly this class) must NOT join."""
        signals = [{
            "title": "Senior Engineer - DGX Cloud",
            "company": "NVIDIA",
        }]
        postings = {"JR1": "Senior Engineer"}
        joined = join_by_title(signals, postings, "NVIDIA")
        assert joined == {}

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
        blocked = {"job_req_id": "JR2026100", "title": "Walled Engineer",
                   "company": "NVIDIA", "linkedin_job_id": "9",
                   "num_applicants": None, "status": STATUS_BLOCKED}
        joined = join_by_title([blocked], {"JR2026100": "Walled Engineer"},
                               "NVIDIA")
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
        # S9-audit: the 4 reqs share ONE verbatim title (the realistic
        # title family); the card serves exactly one of them. (The old
        # fixture used suffix variants — now correctly rejected as
        # different specializations by the verbatim guard.)
        sigs = [self._sig("1", "Senior System Software Engineer",
                          "2026-09-08")]
        posts = {f"JR{i}": "Senior System Software Engineer"
                 for i in range(1, 5)}
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


# ── S8-E1 (audit/findings-engagement-coverage.md): partitioned index,
# applicantCensored, location-aware title tiebreak ─────────────────────────
class TestDefaultIndexSlices:
    def test_nvidia_matrix_shape(self):
        slices = corroborate.default_index_slices("NVIDIA")
        assert len(slices) == 30                     # 6 keywords × 5 locs
        assert {sl["keywords"] for sl in slices} == {
            "NVIDIA", "NVIDIA software", "NVIDIA engineer",
            "NVIDIA hardware", "NVIDIA marketing", "NVIDIA sales"}
        assert {sl["location"] for sl in slices} == {
            "United States",
            "Santa Clara, California, United States",
            "Austin, Texas, United States",
            "Seattle, Washington, United States",
            "Remote, United States"}
        assert all(set(sl) == {"keywords", "location"} for sl in slices)
        # request bound: 30 queries × 3 pages ≈ 90 per run
        assert corroborate.PARTITIONED_PAGES_PER_SLICE == 3
        assert len(slices) * corroborate.PARTITIONED_PAGES_PER_SLICE == 90

    def test_generic_company_derives_matrix_from_name(self):
        slices = corroborate.default_index_slices("Acme")
        assert {sl["keywords"] for sl in slices} == {
            "Acme", "Acme software", "Acme engineer", "Acme hardware",
            "Acme marketing", "Acme sales"}
        assert len(slices) == 30

    def test_empty_company_falls_back_to_nvidia(self):
        slices = corroborate.default_index_slices("")
        assert slices[0]["keywords"] == "NVIDIA"


class TestIndexCardsPartitioned:
    """S8-E1 (research §c): the single guest query hits a SERVING
    ceiling; the slice matrix unions keyword×location queries, deduped
    by linkedin_job_id, with per-slice B2 stepping and the page-0
    blocked contract preserved."""

    _S0 = {"keywords": "NVIDIA", "location": "United States"}
    _S1 = {"keywords": "NVIDIA software", "location": "United States"}
    _S2 = {"keywords": "NVIDIA",
           "location": "Santa Clara, California, United States"}

    def _patch(self, monkeypatch, pages_by_query: dict,
               fail_queries=frozenset()):
        """fetch_text serves canned card pages keyed by (keywords,
        location) (list of pages, each a list of cards); raises for
        fail_queries; records every (keywords, location, offset) served."""
        from urllib.parse import urlparse, parse_qs

        def _card_html(c):
            return (f'<li data-entity-urn="urn:li:jobPosting:{c["id"]}">'
                    f'<h3 class="base-search-card__title">{c["title"]}</h3>'
                    f'<h4 class="base-search-card__subtitle">'
                    f'<a>{c["company"]}</a></h4>'
                    f'<span class="job-search-card__location">'
                    f'{c["location"]}</span>'
                    f'<time datetime="{c["date"]}">now</time></li>')

        page_i: dict = {}
        served: list[tuple] = []

        def fake_fetch_text(url, *, params=None, cfg=None):
            q = parse_qs(urlparse(url).query)
            kw, loc = q["keywords"][0], q["location"][0]
            served.append((kw, loc, int(q["start"][0])))
            if (kw, loc) in fail_queries:
                raise RuntimeError(f"blocked {kw}/{loc}")
            i = page_i.get((kw, loc), 0)
            page_i[(kw, loc)] = i + 1
            pages = pages_by_query.get((kw, loc), [])
            if i >= len(pages):
                return ""                    # exhausted (empty body)
            return "".join(_card_html(c) for c in pages[i])

        monkeypatch.setattr(corroborate, "fetch_text", fake_fetch_text)
        monkeypatch.setattr(corroborate, "_INDEX_PAUSE_S", 0)
        monkeypatch.setattr(corroborate, "_SLICE_PAUSE_S", 0)
        return served

    def test_union_dedupes_across_slices(self, monkeypatch):
        # S0 serves cards 1,2 then 2,3 (intra-slice dupe); S1 serves
        # 3,4 (card 3 = cross-slice dupe) then re-serves both (intra-
        # slice dupe) → union [1,2,3,4]
        monkeypatch.setattr(corroborate, "_CARDS_PER_PAGE", 2)  # 2-wide
        served = self._patch(monkeypatch, {
            ("NVIDIA", "United States"): [
                [_card(1), _card(2)], [_card(2), _card(3)]],
            ("NVIDIA software", "United States"): [
                [_card(3), _card(4)], [_card(3), _card(4)]],
        })
        p = LinkedInSignalProvider(cfg=None)
        cards, next_offset, exhausted = p.index_cards_partitioned(
            "NVIDIA", slices=[self._S0, self._S1], max_pages_per_slice=2)
        assert [c["id"] for c in cards] == ["1", "2", "3", "4"]
        assert next_offset == 0            # slices restart at 0 on re-run
        assert exhausted is True           # page-cap counts complete
        # each slice issued its own query from offset 0, stepping B2-style
        # by cards RECEIVED (page 0 of 2 cards → offset 2)
        assert served == [("NVIDIA", "United States", 0),
                          ("NVIDIA", "United States", 2),
                          ("NVIDIA software", "United States", 0),
                          ("NVIDIA software", "United States", 2)]

    def test_company_variant_filter_applies_per_slice(self, monkeypatch):
        self._patch(monkeypatch, {
            ("NVIDIA", "United States"): [
                [_card(1, company="NVIDIA"),
                 _card(2, company="Some Other Co")]]})
        p = LinkedInSignalProvider(cfg=None)
        cards, _, _ = p.index_cards_partitioned(
            "NVIDIA", slices=[self._S0], max_pages_per_slice=1)
        assert [c["id"] for c in cards] == ["1"]   # noise card filtered

    def test_slices_none_uses_default_matrix(self, monkeypatch):
        # every default-matrix query serves a SHORT (1-card) page — a
        # well-formed board where every slice genuinely ends its serving
        matrix = corroborate.default_index_slices("NVIDIA")
        served = self._patch(monkeypatch, {
            (sl["keywords"], sl["location"]): [[_card(1)]]
            for sl in matrix})
        p = LinkedInSignalProvider(cfg=None)
        cards, next_offset, exhausted = p.index_cards_partitioned(
            "NVIDIA", max_pages_per_slice=1)
        assert [c["id"] for c in cards] == ["1"]   # union dedup
        assert next_offset == 0 and exhausted is True
        assert len(served) == 30                   # the full 6×5 matrix
        assert served[0][:2] == ("NVIDIA", "United States")
        assert any(kw == "NVIDIA sales" and
                   loc == "Remote, United States"
                   for kw, loc, _ in served)

    def test_all_empty_board_raises_blocked_empty(self, monkeypatch):
        """S9-audit D3 P2 pin (the inversion, partitioned flavor): an
        all-empty board is soft-wall AMBIGUOUS, not a clean done=true
        with 0 cards — the first slice's empty page-0 raises
        CorroborationBlocked so the phase records blocked (B5) and
        retries, exactly like search_title's blocked_empty."""
        served = self._patch(monkeypatch, {})      # every query empty
        p = LinkedInSignalProvider(cfg=None)
        with pytest.raises(corroborate.CorroborationBlocked,
                           match="blocked_empty"):
            p.index_cards_partitioned(
                "NVIDIA", max_pages_per_slice=1)
        assert len(served) == 1                     # slice 0 only

    def test_first_slice_page0_blocked_raises(self, monkeypatch):
        self._patch(monkeypatch, {},
                    fail_queries={("NVIDIA", "United States")})
        p = LinkedInSignalProvider(cfg=None)
        with pytest.raises(corroborate.CorroborationBlocked):
            p.index_cards_partitioned(
                "NVIDIA", slices=[self._S0, self._S1],
                max_pages_per_slice=2)

    def test_later_slice_blocked_degrades_partial(self, monkeypatch):
        """A non-first slice blocking is NOT fatal: keep the union, report
        exhausted=False so the caller re-runs (idempotent union)."""
        self._patch(monkeypatch,
                    {("NVIDIA", "United States"): [[_card(1)]]},
                    fail_queries={("NVIDIA software", "United States")})
        p = LinkedInSignalProvider(cfg=None)
        cards, next_offset, exhausted = p.index_cards_partitioned(
            "NVIDIA", slices=[self._S0, self._S1], max_pages_per_slice=1)
        assert [c["id"] for c in cards] == ["1"]
        assert exhausted is False
        assert next_offset == 0

    def test_exhausted_when_all_slices_run_clean(self, monkeypatch):
        # both slices end on SHORT tail pages — genuine completion
        self._patch(monkeypatch, {
            ("NVIDIA", "United States"): [[_card(1)]],
            ("NVIDIA software", "United States"): [[_card(2)]]})
        p = LinkedInSignalProvider(cfg=None)
        cards, _, exhausted = p.index_cards_partitioned(
            "NVIDIA", slices=[self._S0, self._S1], max_pages_per_slice=2)
        assert [c["id"] for c in cards] == ["1", "2"]
        assert exhausted is True

    def test_zero_result_slice_is_retryable_not_complete(self, monkeypatch):
        """S9-audit D3 P2 pin: a slice serving an EMPTY 200 page is
        wall-ambiguous — exhausted_all must go False (re-run later,
        idempotent union) instead of silently certifying the whole
        partitioned index done."""
        self._patch(monkeypatch, {
            ("NVIDIA", "United States"): [[_card(1)]],
            ("NVIDIA software", "United States"): [[]]})   # 0-result slice
        p = LinkedInSignalProvider(cfg=None)
        cards, next_offset, exhausted = p.index_cards_partitioned(
            "NVIDIA", slices=[self._S0, self._S1], max_pages_per_slice=2)
        assert [c["id"] for c in cards] == ["1"]   # partial union kept
        assert next_offset == 0
        assert exhausted is False                    # ← the inversion fix

    def test_sleeps_between_slices_not_after_last(self, monkeypatch):
        """Rate discipline: _SLICE_PAUSE_S between slices (page pauses
        still apply inside each slice) — N slices → N-1 slice pauses.
        Sleeps are RECORDED, never taken (sentinel pause values).
        S9-audit D3: pages are declared 1-wide (_CARDS_PER_PAGE=1) so
        the 1-card fixture pages count as FULL — a SHORT tail page now
        breaks BEFORE its page pause (no trailing sleep, D3 P3)."""
        sleeps: list[float] = []
        monkeypatch.setattr(
            corroborate, "time",
            type("T", (), {"sleep": staticmethod(sleeps.append)})())
        self._patch(monkeypatch, {
            ("NVIDIA", "United States"): [[_card(1)]],
            ("NVIDIA software", "United States"): [[_card(2)]]})
        monkeypatch.setattr(corroborate, "_CARDS_PER_PAGE", 1)
        monkeypatch.setattr(corroborate, "_INDEX_PAUSE_S", 0.25)
        monkeypatch.setattr(corroborate, "_SLICE_PAUSE_S", 3.0)
        p = LinkedInSignalProvider(cfg=None)
        p.index_cards_partitioned(
            "NVIDIA", slices=[self._S0, self._S1], max_pages_per_slice=1)
        assert sleeps.count(3.0) == 1       # 2 slices → ONE inter-slice pause
        assert sleeps.count(0.25) == 2      # one page pause inside each slice
        assert sleeps == [0.25, 3.0, 0.25]  # page, slice, page — no trailing


class TestApplicantCensored:
    """S8-E1 (research §a): numApplicants is a censored bucket — floor 25
    ("among first 25") / cap 200 ("Over 200 applicants"). The flag rides
    fetch_signals' MATCHED records; blocked records omit it."""

    def _rec(self, monkeypatch, num, label) -> dict:
        p = LinkedInSignalProvider(cfg=None)
        monkeypatch.setattr(
            corroborate.linkedin_guest, "fetch_detail",
            lambda cid, cfg=None: {
                "work_mode": "", "description": "d", "closed": False,
                "num_applicants": num, "applicants_label": label,
                "job_req_id": "", "posted_time_ago": ""})
        return p.fetch_signals([_card(1)], detail_pause_s=0)[0]

    def test_bucket_constants(self):
        assert corroborate.APPLICANT_BUCKET_FLOOR == 25
        assert corroborate.APPLICANT_BUCKET_CAP == 200

    def test_floor_25_flagged(self, monkeypatch):
        assert self._rec(monkeypatch, 25, "25 applicants")[
            "applicantCensored"] is True

    def test_cap_200_flagged(self, monkeypatch):
        assert self._rec(monkeypatch, 200, "Over 200 applicants")[
            "applicantCensored"] is True

    def test_among_first_floor_label_flagged(self, monkeypatch):
        assert self._rec(monkeypatch, 25, "among first 25")[
            "applicantCensored"] is True

    def test_mid_count_not_flagged(self, monkeypatch):
        assert self._rec(monkeypatch, 31, "31 applicants")[
            "applicantCensored"] is False

    def test_sub_floor_censoring_label_flagged(self, monkeypatch):
        # "Be among the first 10" — true count at-or-below 10
        assert self._rec(monkeypatch, 10, "among first 10")[
            "applicantCensored"] is True

    def test_blocked_records_omit_the_flag(self, monkeypatch):
        p = LinkedInSignalProvider(cfg=None)

        def wall(cid, cfg=None):
            raise RuntimeError("429 wall")

        monkeypatch.setattr(corroborate.linkedin_guest, "fetch_detail", wall)
        rec = p.fetch_signals([_card(1)], detail_pause_s=0)[0]
        assert rec["status"] == STATUS_BLOCKED
        assert "applicantCensored" not in rec

    def test_one_in_one_out_contract_holds(self, monkeypatch):
        """Contract regression (S8-E1): mixed batch — one blocked, one
        floor, one cap, one uncensored — exactly one record per card,
        never raises, flags exactly the censored ones."""
        p = LinkedInSignalProvider(cfg=None)
        details = {
            "1": {"num_applicants": 25, "applicants_label": "25 applicants",
                  "job_req_id": "", "posted_time_ago": ""},
            "3": {"num_applicants": 200,
                  "applicants_label": "Over 200 applicants",
                  "job_req_id": "", "posted_time_ago": ""},
            "4": {"num_applicants": 54, "applicants_label": "54 applicants",
                  "job_req_id": "", "posted_time_ago": ""},
        }

        def fake(cid, cfg=None):
            if cid == "2":
                raise RuntimeError("blocked")
            return details[cid]

        monkeypatch.setattr(corroborate.linkedin_guest, "fetch_detail", fake)
        recs = p.fetch_signals([_card(i) for i in range(1, 5)],
                               detail_pause_s=0)
        assert len(recs) == 4                       # one per card, no raise
        assert recs[1]["status"] == STATUS_BLOCKED  # card 2 walled, not fatal
        assert [r["applicantCensored"] for r in recs
                if r["status"] == STATUS_MATCHED] == [True, True, False]


class TestJoinLocationTiebreak:
    """S8-E1 (research §f R3b): location-aware tiebreak in the greedy
    title join — card/req location overlap (city tokens, state codes)
    ranks BEFORE date proximity; conservative by construction."""

    def _sig(self, cid, title, date, location) -> dict:
        return {"linkedin_job_id": cid, "linkedin_url": f"u/{cid}",
                "title": title, "company": "NVIDIA", "location": location,
                "linkedin_posted_date": date, "num_applicants": 32,
                "status": "matched"}

    def test_overlap_beats_date_proximity(self):
        # card 2 is 1 day from the req's date but wrong city; card 1 is
        # 39 days off but shares the req's location → card 1 wins
        sigs = [self._sig("1", "SRE CUDA", "2026-08-01", "Santa Clara, CA"),
                self._sig("2", "SRE CUDA", "2026-09-08", "Austin, TX")]
        joined = join_by_title(sigs, {"JR1": "SRE CUDA"}, "NVIDIA",
                               req_dates={"JR1": "2026-09-09"},
                               req_locations={"JR1": "US, CA, Santa Clara"})
        assert joined["JR1"]["linkedin_job_id"] == "1"

    def test_neutral_without_req_locations(self):
        # regression guard: no req_locations → date proximity decides
        # (the pre-S8-E1 behavior, unchanged)
        sigs = [self._sig("1", "SRE CUDA", "2026-08-01", "Santa Clara, CA"),
                self._sig("2", "SRE CUDA", "2026-09-08", "Austin, TX")]
        joined = join_by_title(sigs, {"JR1": "SRE CUDA"}, "NVIDIA",
                               req_dates={"JR1": "2026-09-09"})
        assert joined["JR1"]["linkedin_job_id"] == "2"

    def test_generic_country_location_is_neutral(self):
        # "United States" carries no usable token — must NOT be preferred
        # over a specific card; proximity keeps deciding
        sigs = [self._sig("1", "SRE CUDA", "2026-08-01", "United States"),
                self._sig("2", "SRE CUDA", "2026-09-08", "Austin, TX")]
        joined = join_by_title(sigs, {"JR1": "SRE CUDA"}, "NVIDIA",
                               req_dates={"JR1": "2026-09-09"},
                               req_locations={"JR1": "US, CA, Santa Clara"})
        assert joined["JR1"]["linkedin_job_id"] == "2"

    def test_both_overlap_falls_back_to_proximity(self):
        sigs = [self._sig("1", "SRE CUDA", "2026-08-01", "Santa Clara, CA"),
                self._sig("2", "SRE CUDA", "2026-09-08", "Santa Clara, CA")]
        joined = join_by_title(sigs, {"JR1": "SRE CUDA"}, "NVIDIA",
                               req_dates={"JR1": "2026-09-09"},
                               req_locations={"JR1": "US, CA, Santa Clara"})
        assert joined["JR1"]["linkedin_job_id"] == "2"

    def test_tiebreak_creates_no_new_fanout(self):
        # one card, two same-title reqs (one location-overlapping) → the
        # overlapping req is preferred and the OTHER req gets nothing
        sigs = [self._sig("1", "SRE CUDA", "2026-09-08", "Austin, TX")]
        joined = join_by_title(
            sigs, {"JRA": "SRE CUDA", "JRB": "SRE CUDA"}, "NVIDIA",
            req_locations={"JRA": "US, TX, Austin",
                           "JRB": "US, WA, Seattle"})
        assert set(joined) == {"JRA"}
        assert joined["JRA"]["linkedin_job_id"] == "1"

    def test_foreign_req_id_serves_nobody(self):
        """S8-E1 guard (existing behavior, pinned): a card whose — now
        possibly bare-regex-extracted — reqId points OUTSIDE the input
        batch joins nothing by reqId."""
        signals = [
            {"job_req_id": "JR9999999", "title": "A",
             "status": STATUS_MATCHED},          # foreign (bare-JR shape)
            {"job_req_id": "JR2026100", "title": "B",
             "status": STATUS_MATCHED},
        ]
        joined = join_by_req_id(signals, {"JR2026100"})
        assert set(joined) == {"JR2026100"}
        assert joined["JR2026100"]["title"] == "B"
