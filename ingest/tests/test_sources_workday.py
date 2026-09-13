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
