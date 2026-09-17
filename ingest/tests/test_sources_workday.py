"""Workday CXS adapter tests (S5-2 pilot — sources/workday.py).

Pins the live-verified CXS contract quirks (2026-09-08, nvidia.wd5):
- limit is always 20 (the API hard-caps at 20; 25+ → HTTP 400)
- pagination is bounded by the FIRST page's total; offsets past total
  wrap around to page 0 (the duplicate trap)
- country/location + timeType filters via appliedFacets (IDs discovered
  from the facets payload)
- detail GET enriches the matched top-N (title/date/location/description)
- dump_board() is exhaustive: every page, dedup by reqId

Monkeypatch pattern: patch jobsearch.sources.workday.fetch_json (the
module-local import), NOT base.fetch_json — same gotcha as the other
adapter suites.
"""
from __future__ import annotations

import json

import pytest

from jobsearch.config import Config
from jobsearch.sources import workday


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(db_path=tmp_path / "t.db")


def _facet(param: str, values: list[tuple[str, str, int]]) -> dict:
    return {"facetParameter": param,
            "values": [{"descriptor": d, "id": i, "count": c}
                       for d, i, c in values]}


def _facets_payload(with_loc_type: bool = True) -> list[dict]:
    loc_group_values = [
        {"facetParameter": "locationHierarchy1",
         "values": [
             {"descriptor": "United States", "id": "USID", "count": 1428},
             {"descriptor": "India", "id": "INID", "count": 243},
         ]},
        {"facetParameter": "locations", "values": [
            {"descriptor": "US, CA, Santa Clara", "id": "SCID", "count": 400},
        ]},
    ]
    if with_loc_type:
        loc_group_values.insert(0, {
            "facetParameter": "locationHierarchy2",
            "values": [
                {"descriptor": "Office", "id": "OFFID", "count": 2551},
                {"descriptor": "Remote", "id": "REMID", "count": 578},
            ]})
    return [
        _facet("timeType", [("Full time", "TTFULL", 2689),
                            ("Part time", "TTPART", 2)]),
        {"facetParameter": "locationMainGroup", "values": loc_group_values},
    ]


def _page_response(total: int, postings: list[dict],
                   with_loc_type: bool = True) -> dict:
    return {"total": total, "jobPostings": postings,
            "facets": _facets_payload(with_loc_type),
            "userAuthenticated": False}


def _posting(n: int, title: str = "Software Engineer") -> dict:
    return {"title": f"{title} {n}",
            "externalPath": f"/job/US-CA-Santa-Clara/Role-{n}_JR{n}",
            "locationsText": "US, CA, Santa Clara",
            "postedOn": "Posted Today",
            "bulletFields": [f"JR{n}"]}


class _Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, *, cfg, method="GET", json=None, headers=None,
                 params=None, auth=None):
        self.calls.append({"url": url, "method": method, "json": json})
        if not self.responses:
            raise AssertionError("unexpected extra fetch_json call")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ── parse / helpers ────────────────────────────────────────────────────────


def test_parse_board_accepts_both_separators():
    assert workday.parse_board("Nvidia|wd5|Site") == ("nvidia", "wd5", "Site")
    assert workday.parse_board("nvidia/wd5/site") == ("nvidia", "wd5", "site")
    with pytest.raises(ValueError):
        workday.parse_board("nvidia-only")


def test_company_display_known_map():
    assert workday.company_display("nvidia") == "NVIDIA"
    assert workday.company_display("acme-labs") == "Acme Labs"


def test_flatten_facets_handles_nested_location_tree():
    flat = workday._flatten_facets({"facets": _facets_payload()})
    params = {f.get("facetParameter") for f in flat}
    assert {"timeType", "locationHierarchy1", "locations"} <= params


def test_facet_id_matches_case_insensitive():
    payload = {"facets": _facets_payload()}
    assert workday._facet_id(payload, "locationHierarchy1", "united states") == "USID"
    assert workday._facet_id(payload, "timeType", "Full time") == "TTFULL"
    assert workday._facet_id(payload, "timeType", "Contract") is None


# ── fetch() ────────────────────────────────────────────────────────────────


def test_fetch_applies_country_and_timetype_facets(cfg, monkeypatch):
    """location='United States' + job_type='full-time' → the second page-0
    call carries BOTH facet filters; the filtered total bounds pagination."""
    p0_unfiltered = _page_response(2691, [_posting(i) for i in range(20)])
    p0_filtered = _page_response(1428, [_posting(i) for i in range(20)])
    rec = _Recorder([p0_unfiltered, p0_filtered])
    monkeypatch.setattr(workday, "fetch_json", rec)
    # list-level only for this test: no detail GETs, no extra fetch_json calls
    monkeypatch.setattr(workday, "_detail", lambda *a, **k: None)
    monkeypatch.setattr(workday, "_DETAIL_SLEEP_S", 0)

    jobs = workday.fetch("engineer", location="United States",
                         num_results=5, job_type="full-time", cfg=cfg)
    assert len(jobs) == 5
    assert all(j.source == "Workday.nvidia" for j in jobs)
    assert all(j.company == "NVIDIA" for j in jobs)
    # call 1: unfiltered page 0 (facet discovery); call 2: filtered page 0
    assert rec.calls[0]["json"]["appliedFacets"] == {}
    assert rec.calls[1]["json"]["appliedFacets"] == {
        "locationHierarchy1": ["USID"], "timeType": ["TTFULL"]}
    # every request honors the CXS limit cap
    assert all(c["json"]["limit"] == 20 for c in rec.calls)


def test_fetch_pagination_bounded_by_first_page_total(cfg, monkeypatch):
    """40 postings, total=40 → exactly 2 page calls; NO offset past the
    total (the CXS wrap-around would silently re-serve page 0)."""
    pages = [_page_response(40, [_posting(i) for i in range(20)]),
             _page_response(0, [_posting(i) for i in range(20, 40)])]
    rec = _Recorder(pages)
    monkeypatch.setattr(workday, "fetch_json", rec)
    monkeypatch.setattr(workday, "_detail", lambda *a, **k: None)

    jobs = workday.fetch("", location="", num_results=100, cfg=cfg)
    assert len(jobs) == 40
    assert [c["json"]["offset"] for c in rec.calls] == [0, 20]
    # near-end pages report total=0 — the loop must not stop early
    assert jobs[39].title.endswith("39")


def test_fetch_pagination_steps_by_cards_received(cfg, monkeypatch):
    """S9-audit F3 (P2, the B2 anti-pattern): a SHORT page mid-list must
    advance the offset by the cards it actually served — the old fixed
    _PAGE_LIMIT step skipped postings 35-39 after a 15-card page."""
    pages = [_page_response(40, [_posting(i) for i in range(20)]),
             _page_response(0, [_posting(i) for i in range(20, 35)]),
             _page_response(0, [_posting(i) for i in range(35, 40)])]
    rec = _Recorder(pages)
    monkeypatch.setattr(workday, "fetch_json", rec)
    monkeypatch.setattr(workday, "_detail", lambda *a, **k: None)

    jobs = workday.fetch("", location="", num_results=100, cfg=cfg)
    assert len(jobs) == 40                     # nothing skipped
    assert [c["json"]["offset"] for c in rec.calls] == [0, 20, 35]
    assert jobs[34].title.endswith("34") and jobs[35].title.endswith("35")


def test_fetch_remote_uses_server_side_remote_facet(cfg, monkeypatch):
    """location='Remote' on a board WITH a locationHierarchy2 facet →
    server-side Remote filter (the exhaustive path), no client filter."""
    postings = [_posting(1), _posting(2)]
    rec = _Recorder([_page_response(2691, postings),
                     _page_response(578, postings)])
    monkeypatch.setattr(workday, "fetch_json", rec)
    monkeypatch.setattr(workday, "_detail", lambda *a, **k: None)
    jobs = workday.fetch("engineer", location="Remote", num_results=2, cfg=cfg)
    assert len(jobs) == 2
    assert rec.calls[1]["json"]["appliedFacets"] == {
        "locationHierarchy2": ["REMID"]}


def test_fetch_keyword_filter_and_detail_enrichment(cfg, monkeypatch):
    """Only title matches survive; matched jobs get detail enrichment
    (exact location, ISO date, cleaned description, canonical link)."""
    postings = [_posting(1, "Senior Software Engineer"),
                _posting(2, "Marketing Manager"),
                _posting(3, "Software Engineer")]
    postings[0]["locationsText"] = "US, CA, Remote"     # passes the remote gate
    postings[2]["locationsText"] = "US, CA, Remote"
    rec = _Recorder([_page_response(3, postings, with_loc_type=False)])

    def fake_detail(board, path, cfg):
        assert path.endswith("_JR1") or path.endswith("_JR3")
        return {"title": "X", "location": "US, CA, Remote",
                "additionalLocations": ["US, TX, Remote"],
                "startDate": "2026-09-07", "timeType": "Full time",
                "jobDescription": "<p>Build <b>GPU</b> systems.</p>",
                "externalUrl": "https://nvidia.wd5.myworkdayjobs.com/"
                               "NVIDIAExternalCareerSite/job/X_JR1",
                "jobReqId": "JR1"}
    monkeypatch.setattr(workday, "fetch_json", rec)
    monkeypatch.setattr(workday, "_detail", fake_detail)
    monkeypatch.setattr(workday, "_DETAIL_SLEEP_S", 0)  # no test latency

    jobs = workday.fetch("engineer", location="Remote", num_results=10, cfg=cfg)
    assert [j.title for j in jobs] == ["X", "X"]
    assert all(j.location == "US, CA, Remote" for j in jobs)
    assert all(j.date_posted == "2026-09-07" for j in jobs)
    assert all(j.remote is True for j in jobs)
    assert "GPU" in jobs[0].description and "<b>" not in jobs[0].description


def test_fetch_date_filter_uses_detail_start_date(cfg, monkeypatch):
    rec = _Recorder([_page_response(2, [_posting(1), _posting(2)])])
    monkeypatch.setattr(workday, "fetch_json", rec)
    monkeypatch.setattr(
        workday, "_detail",
        lambda *a, **k: {"title": "T", "location": "US",
                         "additionalLocations": [],
                         "startDate": "2020-01-01", "timeType": "Full time",
                         "jobDescription": "", "externalUrl": "u", "jobReqId": "J"})
    monkeypatch.setattr(workday, "_DETAIL_SLEEP_S", 0)
    jobs = workday.fetch("", location="", num_results=10, date_filter=7, cfg=cfg)
    assert jobs == []          # 2020 detail date is outside the 7-day window


def test_fetch_board_failure_isolated_and_bad_spec_skipped(cfg, monkeypatch,
                                                            capsys):
    rec = _Recorder([RuntimeError("board exploded")])
    monkeypatch.setattr(workday, "fetch_json", rec)
    cfg.workday_boards = ["nvidia|wd5|nvidiaexternalcareersite", "garbage"]
    jobs = workday.fetch("", location="", num_results=5, cfg=cfg)
    assert jobs == []
    err = capsys.readouterr().out
    assert "board exploded" in err and "malformed board spec" in err


def test_fetch_plain_remote_location_filters_client_side(cfg, monkeypatch):
    """Remote fallback when NO locationHierarchy2 facet exists (older
    boards): jobs are filtered client-side on the location text."""
    postings = [_posting(1), _posting(2)]
    postings[0]["locationsText"] = "US, CA, Remote"
    postings[1]["locationsText"] = "US, CA, Santa Clara"
    rec = _Recorder([_page_response(2, postings, with_loc_type=False)])
    monkeypatch.setattr(workday, "fetch_json", rec)
    monkeypatch.setattr(workday, "_detail", lambda *a, **k: None)
    jobs = workday.fetch("engineer", location="Remote", num_results=10, cfg=cfg)
    assert len(jobs) == 1 and "Remote" in jobs[0].location
    # no facet was requested (no second page-0 call)
    assert len(rec.calls) == 1


# ── detail_payload — the info:{} zombie contract (S9-audit F3/C1) ─────


def test_detail_payload_requires_job_posting_info(cfg, monkeypatch):
    """A 200 whose jobPostingInfo is absent/empty is an ERROR (None —
    the module's failure convention), not a success dict: board_dump
    translates None into error='detail_unreachable' with attempts (the
    3-strike cap), instead of writing a never-settled {"reqId",
    "info": {}} zombie that re-fetches forever and ships
    detailError=""."""
    good = {"jobPostingInfo": {"title": "T", "startDate": "2026-09-08"},
            "hiringOrganization": {"name": "2100 NVIDIA USA"},
            "similarJobs": []}
    board = ("nvidia", "wd5", "site")
    monkeypatch.setattr(workday, "fetch_json", lambda *a, **k: good)
    assert workday.detail_payload(board, "/job/X_JR1", cfg) is good

    # 200 dict WITHOUT jobPostingInfo (req taken down between list+detail)
    monkeypatch.setattr(workday, "fetch_json",
                        lambda *a, **k: {"someOtherKey": 1})
    assert workday.detail_payload(board, "/job/X_JR1", cfg) is None

    # 200 dict with an EMPTY jobPostingInfo — same zombie shape
    monkeypatch.setattr(workday, "fetch_json",
                        lambda *a, **k: {"jobPostingInfo": {}})
    assert workday.detail_payload(board, "/job/X_JR1", cfg) is None

    # non-dict 200 body
    monkeypatch.setattr(workday, "fetch_json", lambda *a, **k: "nope")
    assert workday.detail_payload(board, "/job/X_JR1", cfg) is None

    # transport failure (the pre-existing convention, pinned)
    def boom(*a, **k):
        raise RuntimeError("429")
    monkeypatch.setattr(workday, "fetch_json", boom)
    assert workday.detail_payload(board, "/job/X_JR1", cfg) is None


# ── dump_board() — the exhaustive mission-side API ─────────────────────────


def test_dump_board_is_exhaustive_and_dedups(cfg, monkeypatch):
    """3 pages, 20+20+8 postings, total=48 → 48 unique rows, dedup by
    reqId even if the API re-serves rows (wrap-around guard)."""
    pages = [
        _page_response(48, [_posting(i) for i in range(20)]),
        _page_response(48, [_posting(i) for i in range(20, 40)]),
        _page_response(0, [_posting(i) for i in range(40, 48)]),
    ]
    rec = _Recorder(pages)
    monkeypatch.setattr(workday, "fetch_json", rec)
    monkeypatch.setattr(workday, "time", type("T", (), {"sleep": staticmethod(
        lambda *_: None)}))
    rows = workday.dump_board("nvidia|wd5|nvidiaexternalcareersite", cfg=cfg)
    assert len(rows) == 48
    assert len({r["reqId"] for r in rows}) == 48
    assert all(r["company"] == "NVIDIA" for r in rows)
    assert all(r["url"].startswith(
        "https://nvidia.wd5.myworkdayjobs.com/"
        "nvidiaexternalcareersite/job/") for r in rows)
    assert [c["json"]["offset"] for c in rec.calls] == [0, 20, 40]


def test_dump_board_applies_country_and_time_type(cfg, monkeypatch):
    p0 = _page_response(1428, [_posting(i) for i in range(20)])
    p1 = _page_response(5, [_posting(i) for i in range(5)])
    rec = _Recorder([p0, p1])
    monkeypatch.setattr(workday, "fetch_json", rec)
    monkeypatch.setattr(workday, "time", type("T", (), {"sleep": staticmethod(
        lambda *_: None)}))
    rows = workday.dump_board("nvidia|wd5|nvidiaexternalcareersite",
                              country="United States",
                              time_type="Full time", cfg=cfg)
    assert len(rows) == 5
    assert rec.calls[1]["json"]["appliedFacets"] == {
        "locationHierarchy1": ["USID"], "timeType": ["TTFULL"]}


def test_dump_board_unknown_country_raises(cfg, monkeypatch):
    rec = _Recorder([_page_response(10, [_posting(1)])])
    monkeypatch.setattr(workday, "fetch_json", rec)
    # ValueError since the resolve_facets refactor (was RuntimeError —
    # same message contract, more specific type)
    with pytest.raises(ValueError, match="not a facet"):
        workday.dump_board("nvidia|wd5|nvidiaexternalcareersite",
                           country="Atlantis", cfg=cfg)


# ── discover_board_config() — S5-2 site-ID discovery ───────────────────────


def test_discover_board_config_parses_window_workday(monkeypatch, cfg):
    html = ('<script>window.workday = window.workday || {\n'
            '            tenant: "nvidia",\n'
            '            siteId: "nvidiaexternalcareersite",\n'
            '            locale: "",\n'
            '            requestLocale: "en-US",\n'
            '        };</script>')
    monkeypatch.setattr(workday, "fetch_text", lambda url, **k: html)
    conf = workday.discover_board_config(
        "https://nvidia.wd5.myworkdayjobs.com/whatever", cfg=cfg)
    assert conf == {"tenant": "nvidia", "siteId": "nvidiaexternalcareersite",
                    "locale": "", "requestLocale": "en-US"}


def test_discover_board_config_no_config_raises(monkeypatch, cfg):
    monkeypatch.setattr(workday, "fetch_text", lambda url, **k: "<html>outage</html>")
    with pytest.raises(RuntimeError, match="no window.workday"):
        workday.discover_board_config("dead.wd5.myworkdayjobs.com", cfg=cfg)


# ── source tag shape (dedup attribution contract) ──────────────────────────


def test_job_source_tag_is_dotted_like_other_ats_adapters(cfg, monkeypatch):
    """The ghost heuristic (reposts.py) now matches dotted ATS sources —
    'Workday.nvidia' must keep that exact shape."""
    rec = _Recorder([_page_response(1, [_posting(1)])])
    monkeypatch.setattr(workday, "fetch_json", rec)
    monkeypatch.setattr(workday, "_detail", lambda *a, **k: None)
    jobs = workday.fetch("", location="", num_results=1, cfg=cfg)
    assert jobs[0].source == "Workday.nvidia"


# ── 2,000-cap guard + facet census (S8-E3 / S8-D 1g + gap #4) ─────────────

_BOARD = ("nvidia", "wd5", "nvidiaexternalcareersite")


def test_facet_census_and_values_shape():
    payload = {"facets": _facets_payload()}
    census = workday.facet_census(payload)
    # the nested location tree flattens to real facet params only
    assert set(census) >= {"timeType", "locationHierarchy1", "locations",
                           "locationHierarchy2"}
    assert set(census["timeType"][0]) == {"descriptor", "id", "count"}
    assert workday.facet_values(payload, "locationHierarchy1") == [
        ("United States", "USID", 1428), ("India", "INID", 243)]
    # zero-count values stay in the census (callers filter when needed)
    assert workday.facet_values(payload, "timeType") == [
        ("Full time", "TTFULL", 2689), ("Part time", "TTPART", 2)]


def test_iter_meta_exposes_facet_census(cfg):
    first = _page_response(1, [_posting(1)])
    _rows, meta = workday.iter_board_postings(_BOARD, {}, first, cfg=cfg,
                                              sleep_s=0)
    assert set(meta) >= {"complete", "total", "pages", "facets"}
    assert meta["facets"]["timeType"][0]["id"] == "TTFULL"
    assert meta["facets"]["locationHierarchy1"][0]["count"] == 1428


def test_iter_capped_total_partitions_by_country(cfg, monkeypatch, capsys):
    """S8-D 1g: total==2000 (the server's cap) → LOUD warning + per-country
    partition fallback + union-dedupe by reqId + meta total_capped flags.
    The capped page-0's own postings are seeded into the union."""
    first = _page_response(2000, [_posting(i) for i in range(3)])
    us = _page_response(5, [_posting(i) for i in range(5)])
    india = _page_response(3, [_posting(i) for i in (3, 5, 6)])
    rec = _Recorder([us, india])            # partition page-0s only
    monkeypatch.setattr(workday, "fetch_json", rec)
    rows, meta = workday.iter_board_postings(_BOARD, {}, first, cfg=cfg,
                                             sleep_s=0)
    # 3 seeded + US(JR0-4) + India(JR3,JR5,JR6) − dups = 7 unique reqIds
    assert len(rows) == 7
    assert sorted(rows) == [f"JR{i}" for i in range(7)]
    assert meta["total_capped"] is True
    assert meta["partition_facet"] == "locationHierarchy1"
    assert meta["total"] == 2000            # the (capped) server claim
    assert meta["complete"] is True
    assert [(p["value"], p["total"]) for p in meta["partitions"]] == [
        ("United States", 5), ("India", 3)]
    # sub-list requests applied the partition facet id (nothing else)
    assert rec.calls[0]["json"]["appliedFacets"] == {
        "locationHierarchy1": ["USID"]}
    assert rec.calls[1]["json"]["appliedFacets"] == {
        "locationHierarchy1": ["INID"]}
    captured = capsys.readouterr()
    assert "CAPS" in captured.err and "2,000" in captured.err \
        and "SILENTLY TRUNCATE" in captured.err
    assert "capped-total recovery: 7 unique rows" in captured.out


def test_iter_capped_sublist_raises_clear_error(cfg, monkeypatch):
    """A partition that ALSO reports 2,000 → RuntimeError, never silent
    recursion."""
    first = _page_response(2000, [_posting(1)])
    us = _page_response(2000, [_posting(1)])      # still capped!
    rec = _Recorder([us])
    monkeypatch.setattr(workday, "fetch_json", rec)
    with pytest.raises(RuntimeError, match="capped at 2000 even for"):
        workday.iter_board_postings(_BOARD, {}, first, cfg=cfg, sleep_s=0)


def test_iter_capped_without_facet_census_raises(cfg, monkeypatch):
    """Capped total but the page-0 carries no facet values at all → clear
    error (cannot partition), not silent truncation."""
    first = {"total": 2000, "jobPostings": [_posting(1)]}
    with pytest.raises(RuntimeError, match="no unfiltered facet"):
        workday.iter_board_postings(_BOARD, {}, first, cfg=cfg, sleep_s=0)


def test_iter_capped_partition_respects_caller_facets(cfg, monkeypatch):
    """Caller already filtered timeType → partition by country and the
    sub-list facets = timeType + locationHierarchy1 (the caller's filters
    are preserved, never re-partitioned on the same facet)."""
    first = _page_response(2000, [_posting(1)])
    us = _page_response(1, [_posting(1)])
    rec = _Recorder([us])
    monkeypatch.setattr(workday, "fetch_json", rec)
    rows, meta = workday.iter_board_postings(
        _BOARD, {"timeType": ["TTFULL"]}, first, cfg=cfg, sleep_s=0)
    assert meta["partition_facet"] == "locationHierarchy1"
    assert rec.calls[0]["json"]["appliedFacets"] == {
        "timeType": ["TTFULL"], "locationHierarchy1": ["USID"]}


def test_iter_capped_partition_prefers_unfiltered_param(cfg, monkeypatch):
    """Caller filtered locationHierarchy1 → the partition walks the NEXT
    unfiltered facet (workerSubType/jobFamilyGroup absent here → the
    sites facet)."""
    first = _page_response(2000, [_posting(1)])
    site = _page_response(1, [_posting(1)])
    rec = _Recorder([site])
    monkeypatch.setattr(workday, "fetch_json", rec)
    rows, meta = workday.iter_board_postings(
        _BOARD, {"locationHierarchy1": ["USID"]}, first, cfg=cfg, sleep_s=0)
    assert meta["partition_facet"] == "locations"
    assert rec.calls[0]["json"]["appliedFacets"] == {
        "locationHierarchy1": ["USID"], "locations": ["SCID"]}


def test_iter_capped_partition_page0_failure_is_partial(cfg, monkeypatch):
    """B1 inside the partition path: a partition page-0 network failure
    returns (partial rows, complete=False) — never raises mid-list."""
    first = _page_response(2000, [_posting(0), _posting(1)])
    us = _page_response(2, [_posting(0), _posting(1)])
    rec = _Recorder([us, RuntimeError("network died between partitions")])
    monkeypatch.setattr(workday, "fetch_json", rec)
    rows, meta = workday.iter_board_postings(_BOARD, {}, first, cfg=cfg,
                                             sleep_s=0)
    assert len(rows) == 2                 # US partition survived
    assert meta["complete"] is False      # India partition never fetched
    assert meta["total_capped"] is True
    assert len(meta["partitions"]) == 1


def test_list_board_meta_carries_boardwide_and_filtered_census(cfg,
                                                               monkeypatch):
    """list_board: meta['facets'] = BOARD-WIDE census (from the discovery
    page-0, even when filters applied); meta['facets_filtered'] = the
    caller-filtered census. Additive keys — 'complete' still first-class."""
    p0 = _page_response(2691, [_posting(i) for i in range(20)])
    us_facets = [
        _facet("timeType", [("Full time", "TTFULL", 5)]),
        _facet("locationHierarchy1", [("United States", "USID", 5)]),
    ]
    p0_us = {"total": 5, "jobPostings": [_posting(i) for i in range(5)],
             "facets": us_facets, "userAuthenticated": False}
    rec = _Recorder([p0, p0_us])
    monkeypatch.setattr(workday, "fetch_json", rec)
    rows, meta = workday.list_board(
        "nvidia|wd5|nvidiaexternalcareersite", country="United States",
        cfg=cfg, sleep_s=0)
    assert len(rows) == 5
    assert meta["facets"]["timeType"][0]["count"] == 2689     # board-wide
    assert {v["descriptor"] for v in
            meta["facets"]["locationHierarchy1"]} == {"United States",
                                                      "India"}
    assert meta["facets_filtered"]["timeType"][0]["count"] == 5
    assert meta["facets_filtered"]["locationHierarchy1"] == [
        {"descriptor": "United States", "id": "USID", "count": 5}]
    assert meta["complete"] is True        # old keys untouched (back-compat)


def test_list_board_unfiltered_census_from_single_page0(cfg, monkeypatch):
    """No filters → the one page-0 IS the board-wide census; no
    facets_filtered key is invented."""
    rec = _Recorder([_page_response(3, [_posting(i) for i in range(3)])])
    monkeypatch.setattr(workday, "fetch_json", rec)
    rows, meta = workday.list_board(
        "nvidia|wd5|nvidiaexternalcareersite", cfg=cfg, sleep_s=0)
    assert len(rows) == 3
    assert "facets_filtered" not in meta
    assert meta["facets"]["locationHierarchy1"][0]["id"] == "USID"
