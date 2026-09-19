"""S13 custom job-site adapter pins — the generalized non-Workday infra.

Contract under test: site_boards maps public one-call board APIs
(greenhouse/ashby) onto the workday list-row + detail-payload shapes so
the WHOLE dump/watch phase chain runs unchanged. All network is
monkeypatched (fetch_json) — these pins hold the MAPPING, the country
classification, the label computation, and the dispatch routing."""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobsearch.config import Config  # noqa: E402
from jobsearch.sources import site_boards  # noqa: E402
from jobsearch.sources import workday  # noqa: E402


# ── fixtures: API payload shapes (live-pinned 2026-09-19) ─────────────────

def _gh_job(jid, req_id, title, offices, departments=None, published=None,
            content="<p>desc</p>"):
    return {
        "id": jid, "title": title, "requisition_id": req_id,
        "absolute_url": f"https://job-boards.greenhouse.io/x/jobs/{jid}",
        "location": {"name": " | ".join(o["name"] for o in offices)},
        "offices": offices,
        "departments": [{"id": i, "name": n}
                        for i, n in enumerate(departments or [])],
        "content": content, "first_published": published,
        "company_name": "Acme Corp", "metadata": None,
    }


def _ashby_job(jid, title, country="United States", loc="San Francisco",
               department="Engineering", employment="FullTime",
               published=None, listed=True):
    return {
        "id": jid, "title": title, "location": loc,
        "secondaryLocations": [], "department": department,
        "employmentType": employment, "isListed": listed,
        "jobUrl": f"https://jobs.ashbyhq.com/x/{jid}",
        "descriptionHtml": "<p>desc</p>", "publishedAt": published,
        "address": {"postalAddress": {
            "addressCountry": country, "addressRegion": "CA",
            "addressLocality": "SF"}},
        "workplaceType": "remote",
    }


def _patch_fetch(monkeypatch, jobs):
    calls = []

    def fake(url, *, params=None, cfg=None, **kw):
        calls.append(url)
        return {"jobs": jobs}
    monkeypatch.setattr(site_boards, "fetch_json", fake)
    monkeypatch.setattr(site_boards, "_CACHE", {})   # no cross-test leak
    return calls


@pytest.fixture(autouse=True)
def _clear_cache():
    site_boards._CACHE.clear()
    yield
    site_boards._CACHE.clear()


class TestSpecParsing:
    def test_site_specs(self):
        assert site_boards.is_site_spec("ats:ashby:openai")
        assert site_boards.is_site_spec("ats:greenhouse:anthropic")
        assert not site_boards.is_site_spec("nvidia|wd5|x")
        assert not site_boards.is_site_spec("")
        assert not site_boards.is_site_spec("ats:lever:x")  # no adapter yet

    def test_parse_site(self):
        assert site_boards.parse_site("ats:ashby:openai") == ("ashby",
                                                              "openai")
        with pytest.raises(ValueError):
            site_boards.parse_site("nvidia|wd5|x")


class TestGreenhouseAdapter:
    def _board(self, monkeypatch, jobs):
        _patch_fetch(monkeypatch, jobs)
        return "ats:greenhouse:acme"

    def test_rows_shape_and_country_filter(self, monkeypatch):
        us = _gh_job(1, "REQ-1", "Engineer",
                     [{"id": 1, "name": "SF",
                       "location": "San Francisco, California, "
                                   "United States"}],
                     departments=["Engineering"],
                     published=(date.today() - timedelta(days=3)).isoformat()
                     + "T10:00:00-04:00")
        uk = _gh_job(2, "REQ-2", "London Engineer",
                     [{"id": 2, "name": "London",
                       "location": "London, Greater London, "
                                   "United Kingdom"}])
        multi = _gh_job(3, "REQ-3", "Multi",
                        [{"id": 3, "name": "SF",
                          "location": "San Francisco, California, "
                                      "United States"},
                         {"id": 4, "name": "London",
                          "location": "London, Greater London, "
                                      "United Kingdom"}])
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [us, uk, multi]),
            country="United States", cfg=Config(), progress_label="t")
        # ANY-US-office semantics: multi (SF+London) is US-available
        assert set(rows) == {"REQ-1", "REQ-3"}
        r = rows["REQ-1"]
        assert r["title"] == "Engineer"
        assert r["url"].endswith("/jobs/1")
        assert r["locationsText"] == "SF"
        assert r["postedOn"] == "Posted 3 Days Ago"
        assert r["timeType"] == ""            # greenhouse serves none
        assert r["departments"] == ["Engineering"]
        assert meta["country_client"] is False   # classified at list time
        assert meta["ats"] == "greenhouse"
        assert meta["client_filtered"] == 1
        assert meta["complete"] is True

    def test_detail_payload_shape(self, monkeypatch):
        us = _gh_job(7, "REQ-7", "Engineer",
                     [{"id": 1, "name": "SF",
                       "location": "San Francisco, California, "
                                   "United States"}],
                     departments=["Engineering"], content="<p>Body</p>")
        spec = self._board(monkeypatch, [us])
        p = site_boards.detail_payload(spec, "/jobs/7", Config())
        info = p["jobPostingInfo"]
        assert info["title"] == "Engineer"
        assert info["jobDescription"] == "<p>Body</p>"
        assert info["jobReqId"] == "REQ-7"
        assert info["country"] == {"descriptor": "United States"}
        assert p["hiringOrganization"]["name"] == "Acme Corp"
        assert p["similarJobs"] == []
        # workday-shape compatibility: the country classifier works
        assert workday.detail_in_country(p, "united states")
        # unknown path → None (the detail-unreachable convention)
        assert site_boards.detail_payload(spec, "/jobs/999",
                                          Config()) is None

    def test_no_country_no_filter(self, monkeypatch):
        us = _gh_job(1, "REQ-1", "A",
                     [{"id": 1, "name": "SF",
                       "location": "San Francisco, California, "
                                   "United States"}])
        uk = _gh_job(2, "REQ-2", "B",
                     [{"id": 2, "name": "London",
                       "location": "London, United Kingdom"}])
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [us, uk]), cfg=Config(),
            progress_label="t")
        assert set(rows) == {"REQ-1", "REQ-2"}    # global board
        assert meta["client_filtered"] == 0

    def test_reqid_fallback_to_numeric_id(self, monkeypatch):
        job = _gh_job(42, None, "NoReq",
                      [{"id": 1, "name": "SF",
                        "location": "SF, California, United States"}])
        rows, _ = site_boards.list_board(
            self._board(monkeypatch, [job]), cfg=Config(),
            progress_label="t")
        assert set(rows) == {"42"}


class TestAshbyAdapter:
    def _board(self, monkeypatch, jobs):
        _patch_fetch(monkeypatch, jobs)
        return "ats:ashby:acme"

    def test_rows_country_and_time_filter(self, monkeypatch):
        us = _ashby_job("a1", "Engineer")
        uk = _ashby_job("a2", "London Engineer", country="United Kingdom",
                        loc="London, UK")
        pt = _ashby_job("a3", "Part timer", employment="PartTime")
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [us, uk, pt]),
            country="United States", time_type="Full time",
            cfg=Config(), progress_label="t")
        assert set(rows) == {"a1"}
        r = rows["a1"]
        assert r["timeType"] == "Full time"       # normalized dialect
        assert r["locationsText"] == "San Francisco"
        assert meta["ats"] == "ashby"
        assert meta["country_client"] is False
        assert meta["client_filtered"] == 2

    def test_address_missing_falls_back_to_location_tail(self, monkeypatch):
        job = _ashby_job("a9", "Engineer", loc="Seattle, US")
        job["address"] = None                     # no structured country
        rows, _ = site_boards.list_board(
            self._board(monkeypatch, [job]), country="United States",
            cfg=Config(), progress_label="t")
        assert set(rows) == {"a9"}

    def test_unlisted_jobs_excluded(self, monkeypatch):
        listed = _ashby_job("a1", "A")
        unlisted = _ashby_job("a2", "B", listed=False)
        rows, _ = site_boards.list_board(
            self._board(monkeypatch, [listed, unlisted]), cfg=Config(),
            progress_label="t")
        assert set(rows) == {"a1"}

    def test_detail_payload_shape(self, monkeypatch):
        job = _ashby_job("a1", "Engineer", published="2026-09-01T00:00:00Z")
        spec = self._board(monkeypatch, [job])
        p = site_boards.detail_payload(spec, "/a1", Config())
        info = p["jobPostingInfo"]
        assert info["title"] == "Engineer"
        assert info["timeType"] == "Full time"
        assert info["country"] == {"descriptor": "United States"}
        assert info["jobReqId"] == "a1"
        assert workday.detail_in_country(p, "united states")
        assert site_boards.detail_payload(spec, "/zzz",
                                          Config()) is None


class TestPostedLabel:
    def test_label_and_iso(self):
        iso = (date.today() - timedelta(days=5)).isoformat() \
            + "T12:00:00+00:00"
        label, norm = site_boards._posted_label(iso)
        assert label == "Posted 5 Days Ago"
        assert norm == (date.today() - timedelta(days=5)).isoformat()

    def test_today(self):
        label, _ = site_boards._posted_label(
            date.today().isoformat() + "T00:00:00Z")
        assert label == "Posted Today"

    def test_long_tenure_never_censored(self):
        # workday censors at 30+; adapters know the exact date — the
        # label stays numeric (parse_posted_on handles any N)
        iso = (date.today() - timedelta(days=87)).isoformat() \
            + "T00:00:00Z"
        label, _ = site_boards._posted_label(iso)
        assert label == "Posted 87 Days Ago"

    def test_bad_iso(self):
        assert site_boards._posted_label("") == ("", "")
        assert site_boards._posted_label("not-a-date") == ("", "")


class TestCache:
    def test_one_fetch_per_process(self, monkeypatch):
        jobs = [_ashby_job("a1", "A")]
        calls = _patch_fetch(monkeypatch, jobs)
        spec = "ats:ashby:acme"
        site_boards.list_board(spec, cfg=Config(), progress_label="t")
        site_boards.list_board(spec, cfg=Config(), progress_label="t")
        site_boards.detail_payload(spec, "/a1", Config())
        site_boards.detail_payload(spec, "/a1", Config())
        assert len(calls) == 1          # list + 2 details → ONE network hit


class TestDispatchRouting:
    def test_workday_spec_routes_to_workday(self, monkeypatch):
        seen = {}

        def fake_list_board(spec, **kw):
            seen["spec"] = spec
            seen["kw"] = kw
            return {"JR1": {"reqId": "JR1"}}, {"complete": True}
        monkeypatch.setattr(site_boards.workday, "list_board",
                            fake_list_board)
        rows, meta = site_boards.list_board(
            "nvidia|wd5|site", country="United States", client_filter=False)
        assert seen["spec"] == "nvidia|wd5|site"
        assert seen["kw"]["country"] == "United States"
        assert seen["kw"]["client_filter"] is False
        assert rows == {"JR1": {"reqId": "JR1"}}

    def test_workday_detail_routing(self, monkeypatch):
        def fake_detail(board, path, cfg):
            assert board == ("nvidia", "wd5", "site")
            return {"jobPostingInfo": {"title": "T"}}
        monkeypatch.setattr(site_boards.workday, "detail_payload",
                            fake_detail)
        p = site_boards.detail_payload("nvidia|wd5|site", "/job/X",
                                       Config())
        assert p["jobPostingInfo"]["title"] == "T"
