"""Additional coverage for the free-key source adapters.

Targeted at lines uncovered by tests/test_sources_freekey.py:
  - Jooble: HTTP error translation, date_filter (SearchPeriod), job_type
    filter, num_results early-break, date_filter exclusion
  - Careerjet: date filter exclusion, contract_type parameter passing,
    HTTP error translation, locations handling, salary formatters
  - USAJobs: date filter, date_posted parsing, salary parsing, HTTP errors
  - JSearch: HTTP error translation, date_filter mapping, job_type mapping

Run: pytest tests/test_sources_freekey_ext.py -v
"""
from __future__ import annotations

import json
import pytest
import requests
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jobsearch.config import Config
from jobsearch.sources import adzuna, careerjet, findwork, jooble, jsearch, usajobs

from conftest import SOURCE_FIXTURES


def load_fixture(name: str):
    return json.loads((SOURCE_FIXTURES / name).read_text(encoding="utf-8"))


def _resp(status: int) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    return r


@pytest.fixture
def cfg():
    return Config()


# ── Jooble additional coverage ─────────────────────────────────────────────

class TestJoobleExtra:
    def test_http_401_translates_to_invalid_key(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("401", response=_resp(401))
        monkeypatch.setattr(jooble, "fetch_json", fake)
        cfg.jooble_api_key = "k"
        with pytest.raises(RuntimeError, match="Invalid API key"):
            jooble.fetch("python", cfg=cfg)

    def test_http_403_translates_to_invalid_key(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("403", response=_resp(403))
        monkeypatch.setattr(jooble, "fetch_json", fake)
        cfg.jooble_api_key = "k"
        with pytest.raises(RuntimeError, match="Invalid API key"):
            jooble.fetch("python", cfg=cfg)

    def test_http_500_translates_to_generic_error(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("500", response=_resp(500))
        monkeypatch.setattr(jooble, "fetch_json", fake)
        cfg.jooble_api_key = "k"
        with pytest.raises(RuntimeError, match="HTTP 500"):
            jooble.fetch("python", cfg=cfg)

    def test_connection_error_translates_to_runtime(self, monkeypatch, cfg):
        from requests import ConnectionError
        def fake(*a, **k):
            raise ConnectionError("no internet")
        monkeypatch.setattr(jooble, "fetch_json", fake)
        cfg.jooble_api_key = "k"
        with pytest.raises(RuntimeError, match="No internet"):
            jooble.fetch("python", cfg=cfg)

    def test_date_filter_sets_search_period_in_body(self, monkeypatch, cfg):
        fx = load_fixture("jooble.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(jooble, "fetch_json", fake)
        cfg.jooble_api_key = "k"
        jooble.fetch("python", cfg=cfg, date_filter=7)
        assert seen["json"]["SearchPeriod"] == 7

    def test_job_type_full_time_filter_skips_non_full(self, monkeypatch, cfg):
        # fixture row 1 has no `type` field (empty), row 2 has "Contract"
        # Looking for full-time -> row with "Contract" should be excluded.
        # We need a fixture row that has `type` to test the filter.
        # Use a synthesized response to ensure deterministic behavior.
        # (`updated` is RELATIVE — jooble filters on it via is_within_days
        # when date_filter is set; a hardcoded date is the time-bomb
        # class that rotted 2026-09-08, audit S7-B3 A7.)
        recent = (date.today() - timedelta(days=2)).isoformat()
        fx = {
            "jobs": [
                {"title": "FT Engineer", "company": "Acme", "link": "l1",
                 "type": "Full-Time", "snippet": "python", "location": "NY",
                 "updated": recent},
                {"title": "Contract Engineer", "company": "Beta", "link": "l2",
                 "type": "Contract", "snippet": "python", "location": "NY",
                 "updated": recent},
            ]
        }
        monkeypatch.setattr(jooble, "fetch_json", lambda *a, **k: fx)
        cfg.jooble_api_key = "k"
        jobs = jooble.fetch("python", num_results=5, cfg=cfg, job_type="Full-time")
        assert len(jobs) == 1
        assert jobs[0].title == "FT Engineer"

    def test_job_type_contract_filter_skips_full(self, monkeypatch, cfg):
        recent = (date.today() - timedelta(days=2)).isoformat()
        fx = {
            "jobs": [
                {"title": "FT Engineer", "company": "Acme", "link": "l1",
                 "type": "Full-Time", "snippet": "python", "location": "NY",
                 "updated": recent},
                {"title": "Contract Engineer", "company": "Beta", "link": "l2",
                 "type": "Contract", "snippet": "python", "location": "NY",
                 "updated": recent},
            ]
        }
        monkeypatch.setattr(jooble, "fetch_json", lambda *a, **k: fx)
        cfg.jooble_api_key = "k"
        jobs = jooble.fetch("python", num_results=5, cfg=cfg, job_type="Contract")
        assert len(jobs) == 1
        assert jobs[0].title == "Contract Engineer"

    def test_num_results_limits_output(self, monkeypatch, cfg):
        recent = (date.today() - timedelta(days=2)).isoformat()
        fx = {
            "jobs": [
                {"title": f"Job {i}", "company": "C", "link": f"l{i}",
                 "snippet": "python", "location": "NY",
                 "updated": recent} for i in range(10)
            ]
        }
        monkeypatch.setattr(jooble, "fetch_json", lambda *a, **k: fx)
        cfg.jooble_api_key = "k"
        jobs = jooble.fetch("python", num_results=3, cfg=cfg)
        assert len(jobs) == 3

    def test_date_filter_excludes_old_postings(self, monkeypatch, cfg):
        # NOTE: "recent" must be computed relative to today — a hardcoded
        # date rots as the calendar advances (time-bomb test, caught
        # 2026-09-08 when 2026-08-25 slipped past the 7-day window).
        recent = (date.today() - timedelta(days=2)).isoformat()
        fx = {
            "jobs": [
                {"title": "Recent", "company": "Acme", "link": "l1",
                 "snippet": "python", "location": "NY",
                 "updated": recent},
                {"title": "Old", "company": "Beta", "link": "l2",
                 "snippet": "python", "location": "NY",
                 "updated": "2024-01-01"},
            ]
        }
        monkeypatch.setattr(jooble, "fetch_json", lambda *a, **k: fx)
        cfg.jooble_api_key = "k"
        jobs = jooble.fetch("python", num_results=5, cfg=cfg, date_filter=7)
        # Only the recent posting should survive the date filter
        assert len(jobs) == 1
        assert jobs[0].title == "Recent"

    def test_is_configured_reflects_key(self, cfg):
        assert jooble.is_configured(cfg) is False
        cfg.jooble_api_key = "k"
        assert jooble.is_configured(cfg) is True

    def test_paginates_until_num_results(self, monkeypatch, cfg):
        """audit P2-3: single-POST truncation fixed — page 1..N while
        len(jobs) < num_results and totalCount/jobs signal more (cap 3
        pages, count=20 each); the side-effect fake asserts the body's
        `page` actually advances."""
        pages: list[int] = []

        def fake(url, *, params=None, cfg=None, headers=None,
                 method="GET", json=None, auth=None):
            body = json or {}
            page = body.get("page")
            pages.append(page)
            if page == 1:
                rows = [self._jooble_row(i) for i in range(20)]
                return {"jobs": rows, "totalCount": 30}
            rows = [self._jooble_row(100 + i) for i in range(10)]
            return {"jobs": rows, "totalCount": 30}

        monkeypatch.setattr(jooble, "fetch_json", fake)
        monkeypatch.setattr(jooble.time, "sleep", lambda s: None)
        cfg.jooble_api_key = "k"
        jobs = jooble.fetch("python", num_results=25, cfg=cfg)

        assert len(jobs) == 25                # num_results satisfied (> 20)
        assert pages == [1, 2]                # page param actually advanced
        assert len({j.link for j in jobs}) == 25   # no duplicates

    def test_truncation_safe_returns_what_exists(self, monkeypatch, cfg):
        """audit P2-3: fewer available than requested (totalCount=5, short
        page) → return what exists without error, no extra requests."""
        calls = {"n": 0}

        def fake(url, *, params=None, cfg=None, headers=None,
                 method="GET", json=None, auth=None):
            calls["n"] += 1
            return {"jobs": [self._jooble_row(i) for i in range(5)],
                    "totalCount": 5}

        monkeypatch.setattr(jooble, "fetch_json", fake)
        cfg.jooble_api_key = "k"
        jobs = jooble.fetch("python", num_results=40, cfg=cfg)
        assert len(jobs) == 5
        assert calls["n"] == 1                 # exhausted → no second request

    @staticmethod
    def _jooble_row(i: int) -> dict:
        """Minimal valid Jooble posting row for pagination fixtures.

        `updated` is RELATIVE (today−2d): jooble's fetch() runs it through
        is_within_days whenever date_filter is set — the hardcoded
        "2026-08-20" here was an inert trap that detonates the moment a
        date_filter test reuses this helper (audit S7-B3 A7, the exact
        2026-09-08 rot class)."""
        return {
            "title": f"Python Developer {i}", "company": f"Co {i}",
            "link": f"https://jooble.org/JobRedirect/pg{i}",
            "snippet": "python backend",
            "location": "Remote",
            "updated": (date.today() - timedelta(days=2)).isoformat(),
            "type": "Full-time",
        }


# ── Careerjet additional coverage ───────────────────────────────────────────

class TestCareerjetExtra:
    def test_http_401_translates_to_invalid_key(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("401", response=_resp(401))
        monkeypatch.setattr(careerjet, "fetch_json", fake)
        cfg.careerjet_api_key = "k"
        with pytest.raises(RuntimeError, match="Invalid API key"):
            careerjet.fetch("python", cfg=cfg)

    def test_http_500_translates_to_generic_error(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("500", response=_resp(500))
        monkeypatch.setattr(careerjet, "fetch_json", fake)
        cfg.careerjet_api_key = "k"
        with pytest.raises(RuntimeError, match="HTTP 500"):
            careerjet.fetch("python", cfg=cfg)

    def test_connection_error_translates_to_runtime(self, monkeypatch, cfg):
        from requests import ConnectionError
        def fake(*a, **k):
            raise ConnectionError("no internet")
        monkeypatch.setattr(careerjet, "fetch_json", fake)
        cfg.careerjet_api_key = "k"
        with pytest.raises(RuntimeError, match="No internet"):
            careerjet.fetch("python", cfg=cfg)

    def test_contract_type_full_time_passes_p(self, monkeypatch, cfg):
        fx = load_fixture("careerjet.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(careerjet, "fetch_json", fake)
        cfg.careerjet_api_key = "k"
        careerjet.fetch("python", cfg=cfg, job_type="Full-time")
        assert seen["params"]["contract_type"] == "p"

    def test_contract_type_contract_passes_c(self, monkeypatch, cfg):
        fx = load_fixture("careerjet.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(careerjet, "fetch_json", fake)
        cfg.careerjet_api_key = "k"
        careerjet.fetch("python", cfg=cfg, job_type="Contract")
        assert seen["params"]["contract_type"] == "c"

    def test_contract_type_part_time_passes_t(self, monkeypatch, cfg):
        fx = load_fixture("careerjet.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(careerjet, "fetch_json", fake)
        cfg.careerjet_api_key = "k"
        careerjet.fetch("python", cfg=cfg, job_type="Part-time")
        assert seen["params"]["contract_type"] == "t"

    def test_contract_type_internship_passes_i(self, monkeypatch, cfg):
        fx = load_fixture("careerjet.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(careerjet, "fetch_json", fake)
        cfg.careerjet_api_key = "k"
        careerjet.fetch("python", cfg=cfg, job_type="Internship")
        assert seen["params"]["contract_type"] == "i"

    def test_remote_location_search_passes_remote(self, monkeypatch, cfg):
        fx = load_fixture("careerjet.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(careerjet, "fetch_json", fake)
        cfg.careerjet_api_key = "k"
        careerjet.fetch("python", location="Remote", cfg=cfg)
        assert seen["params"]["location"] == "remote"

    def test_date_filter_excludes_old_postings(self, monkeypatch, cfg):
        # "recent" computed relative to today (hardcoded dates rot — see the
        # Jooble twin of this test for the 2026-09-08 incident).
        recent_dt = datetime.now(timezone.utc) - timedelta(days=2)
        recent_rfc822 = recent_dt.strftime("%a, %d %b %Y 12:00:00 GMT")
        fx = {
            "type": "JOBS",
            "jobs": [
                {"title": "Recent Job", "company": "Acme", "url": "l1",
                 "description": "python", "locations": "Remote",
                 "date": recent_rfc822},
                {"title": "Old Job", "company": "Beta", "url": "l2",
                 "description": "python", "locations": "Remote",
                 "date": "Mon, 01 Jan 2024 12:00:00 GMT"},
            ],
        }
        monkeypatch.setattr(careerjet, "fetch_json", lambda *a, **k: fx)
        cfg.careerjet_api_key = "k"
        jobs = careerjet.fetch("python", num_results=5, cfg=cfg, date_filter=7)
        assert len(jobs) == 1
        assert jobs[0].title == "Recent Job"

    def test_non_jobs_response_raises(self, monkeypatch, cfg):
        # LOCATIONS disambiguation response
        fx = {"type": "LOCATIONS", "message": "Multiple locations match"}
        monkeypatch.setattr(careerjet, "fetch_json", lambda *a, **k: fx)
        cfg.careerjet_api_key = "k"
        with pytest.raises(RuntimeError, match="Unexpected response"):
            careerjet.fetch("python", cfg=cfg)

    def test_num_results_limits_output(self, monkeypatch, cfg):
        fx = {
            "type": "JOBS",
            "jobs": [
                {"title": f"Job {i}", "company": "C", "url": f"l{i}",
                 "description": "python", "locations": "Remote",
                 "date": "Wed, 25 Aug 2026 12:00:00 GMT"} for i in range(10)
            ],
        }
        monkeypatch.setattr(careerjet, "fetch_json", lambda *a, **k: fx)
        cfg.careerjet_api_key = "k"
        jobs = careerjet.fetch("python", num_results=3, cfg=cfg)
        assert len(jobs) == 3

    def test_paginates_until_num_results(self, monkeypatch, cfg):
        """audit P2-3: page-1-only truncation fixed — loop `page` while
        len(jobs) < num_results and page*page_size < totalFound (cap 3
        pages, 0.4s pacing); the side-effect fake asserts the params' `page`
        actually advances. Page 1 is a full page (50 rows = page_size) of
        OLD rows so the client-side date filter drops them all."""
        pages: list[int] = []
        # "recent" computed relative to today (hardcoded dates rot — see
        # test_date_filter_excludes_old_postings for the 2026-09-08 incident).
        recent_rfc822 = (datetime.now(timezone.utc) - timedelta(days=2)
                         ).strftime("%a, %d %b %Y 12:00:00 GMT")
        old_rfc822 = "Mon, 01 Jan 2024 12:00:00 GMT"

        def fake(url, *, params=None, cfg=None, headers=None,
                 method="GET", json=None, auth=None):
            page = (params or {}).get("page")
            pages.append(page)
            if page == 1:
                rows = [self._cj_row(i, old_rfc822) for i in range(50)]
            else:
                rows = [self._cj_row(100 + i, recent_rfc822) for i in range(30)]
            return {"type": "JOBS", "jobs": rows, "totalFound": 80}

        monkeypatch.setattr(careerjet, "fetch_json", fake)
        monkeypatch.setattr(careerjet.time, "sleep", lambda s: None)
        cfg.careerjet_api_key = "k"
        jobs = careerjet.fetch("python", location="Remote", num_results=25,
                               date_filter=7, cfg=cfg)

        assert len(jobs) == 25                # num_results satisfied (> 20)
        assert pages == [1, 2]                # page param actually advanced
        assert all(j.date_posted for j in jobs)

    def test_truncation_safe_returns_what_exists(self, monkeypatch, cfg):
        """audit P2-3: fewer available than requested (totalFound=5, short
        page) → return what exists without error, no extra requests."""
        calls = {"n": 0}

        def fake(url, *, params=None, cfg=None, headers=None,
                 method="GET", json=None, auth=None):
            calls["n"] += 1
            rows = [self._cj_row(i) for i in range(5)]
            return {"type": "JOBS", "jobs": rows, "totalFound": 5}

        monkeypatch.setattr(careerjet, "fetch_json", fake)
        cfg.careerjet_api_key = "k"
        jobs = careerjet.fetch("python", num_results=25, cfg=cfg)
        assert len(jobs) == 5
        assert calls["n"] == 1                 # exhausted → no second request

    @staticmethod
    def _cj_row(i: int, date_rfc822: str = "Wed, 25 Aug 2026 12:00:00 GMT") -> dict:
        """Minimal valid Careerjet posting row for pagination fixtures."""
        return {
            "title": f"Python Developer {i}", "company": f"Co {i}",
            "url": f"https://www.careerjet.com/jobad/pg{i}",
            "description": "python backend",
            "locations": "Remote",
            "date": date_rfc822,
        }

    def test_is_configured_reflects_key(self, cfg):
        assert careerjet.is_configured(cfg) is False
        cfg.careerjet_api_key = "k"
        assert careerjet.is_configured(cfg) is True

    def test_format_salary_with_min_max(self):
        # _format_salary builds "$USD 100,000 – 200,000 / yr" when both present
        item = {
            "salary_min": 100000, "salary_max": 200000,
            "salary_currency_code": "usd", "salary_type": "Y",
        }
        text, mn, mx = careerjet._format_salary(item)
        assert mn == 100000.0
        assert mx == 200000.0
        assert "USD" in text
        assert "/ yr" in text

    def test_format_salary_with_min_only(self):
        item = {"salary_min": 80000, "salary_currency_code": "usd",
                "salary_type": "Y"}
        text, mn, mx = careerjet._format_salary(item)
        assert mn == 80000.0
        assert mx is None
        assert "+" in text  # "USD 80,000+"

    def test_format_salary_with_max_only(self):
        item = {"salary_max": 95000, "salary_currency_code": "usd",
                "salary_type": "Y"}
        text, mn, mx = careerjet._format_salary(item)
        assert mn is None
        assert mx == 95000.0
        assert "Up to" in text

    def test_format_salary_with_no_data(self):
        item = {}
        text, mn, mx = careerjet._format_salary(item)
        assert text == ""
        assert mn is None
        assert mx is None

    def test_format_salary_prefers_raw_text_when_present(self):
        # If the API returns a formatted `salary` string, we should use it
        # directly rather than synthesizing one from min/max.
        item = {
            "salary": "$80/hr – $120/hr",
            "salary_min": 80, "salary_max": 120,
            "salary_currency_code": "usd", "salary_type": "H",
        }
        text, mn, mx = careerjet._format_salary(item)
        assert text == "$80/hr – $120/hr"
        assert mn == 80.0 and mx == 120.0

    def test_parse_careerjet_date_uses_base_normalize_date(self):
        """P1-5 DRY fix: careerjet no longer ships a local _parse_careerjet_date;
        it reuses base.normalize_date which handles RFC 2822 + ISO 8601 + epoch.
        Verify that Careerjet's RFC 2822 date format is still parsed correctly
        via the shared helper."""
        from jobsearch.sources.base import normalize_date
        # RFC 2822 format as returned by Careerjet
        assert normalize_date("Wed, 15 Nov 2023 19:13:43 GMT") == "2023-11-15"
        assert normalize_date("") is None
        assert normalize_date(None) is None
        assert normalize_date("not a date") is None


# ── USAJobs additional coverage ──────────────────────────────────────────────

class TestUSAJobsExtra:
    def test_http_401_translates_to_invalid_credentials(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("401", response=_resp(401))
        monkeypatch.setattr(usajobs, "fetch_json", fake)
        cfg.usajobs_api_key = "k"
        cfg.usajobs_user_agent = "test@example.com"
        with pytest.raises(RuntimeError, match="credentials rejected"):
            usajobs.fetch("python", cfg=cfg)

    def test_http_500_translates_to_generic_error(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("500", response=_resp(500))
        monkeypatch.setattr(usajobs, "fetch_json", fake)
        cfg.usajobs_api_key = "k"
        cfg.usajobs_user_agent = "test@example.com"
        with pytest.raises(RuntimeError, match="HTTP 500"):
            usajobs.fetch("python", cfg=cfg)

    def test_connection_error_translates_to_runtime(self, monkeypatch, cfg):
        from requests import ConnectionError
        def fake(*a, **k):
            raise ConnectionError("no internet")
        monkeypatch.setattr(usajobs, "fetch_json", fake)
        cfg.usajobs_api_key = "k"
        cfg.usajobs_user_agent = "test@example.com"
        with pytest.raises(RuntimeError, match="No internet"):
            usajobs.fetch("python", cfg=cfg)

    def test_date_filter_passes_days_param(self, monkeypatch, cfg):
        fx = load_fixture("usajobs.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(usajobs, "fetch_json", fake)
        cfg.usajobs_api_key = "k"
        cfg.usajobs_user_agent = "test@example.com"
        usajobs.fetch("python", cfg=cfg, date_filter=7)
        # USAJobs uses DatePosted param for days-ago cutoff.
        assert seen["params"]["DatePosted"] == 7

    def test_job_type_full_time_passes_one(self, monkeypatch, cfg):
        fx = load_fixture("usajobs.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(usajobs, "fetch_json", fake)
        cfg.usajobs_api_key = "k"
        cfg.usajobs_user_agent = "test@example.com"
        usajobs.fetch("python", cfg=cfg, job_type="Full-time")
        assert seen["params"]["PositionSchedule"] == "1"

    def test_job_type_part_time_passes_two(self, monkeypatch, cfg):
        fx = load_fixture("usajobs.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(usajobs, "fetch_json", fake)
        cfg.usajobs_api_key = "k"
        cfg.usajobs_user_agent = "test@example.com"
        usajobs.fetch("python", cfg=cfg, job_type="Part-time")
        assert seen["params"]["PositionSchedule"] == "2"

    def test_job_type_internship_passes_five(self, monkeypatch, cfg):
        fx = load_fixture("usajobs.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(usajobs, "fetch_json", fake)
        cfg.usajobs_api_key = "k"
        cfg.usajobs_user_agent = "test@example.com"
        usajobs.fetch("python", cfg=cfg, job_type="Internship")
        assert seen["params"]["PositionSchedule"] == "5"

    def test_job_type_contract_not_supported_no_param(self, monkeypatch, cfg):
        # USAJobs does not have a Contract mapping; the param is omitted.
        fx = load_fixture("usajobs.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(usajobs, "fetch_json", fake)
        cfg.usajobs_api_key = "k"
        cfg.usajobs_user_agent = "test@example.com"
        usajobs.fetch("python", cfg=cfg, job_type="Contract")
        assert "PositionSchedule" not in seen["params"]

    def test_is_configured_requires_both_key_and_user_agent(self, cfg):
        assert usajobs.is_configured(cfg) is False
        cfg.usajobs_api_key = "k"
        assert usajobs.is_configured(cfg) is False  # UA still missing
        cfg.usajobs_user_agent = "test@example.com"
        assert usajobs.is_configured(cfg) is True


# ── JSearch additional coverage ──────────────────────────────────────────────

JS_RESPONSE = {
    "data": [
        {
            "job_id": "js1",
            "job_title": "Senior Python Developer",
            "employer_name": "Acme Cloud",
            "job_apply_link": "https://acme.com/careers/js1",
            "apply_options": [],
            "job_description": "We need a Python dev. Visa sponsorship available. Contact jobs@acme.cloud",
            "job_city": "Austin", "job_state": "TX",
            "job_min_salary": 130000, "job_max_salary": 170000,
            "job_posted_at_datetime_utc": "2026-08-20T10:00:00Z",
            "job_is_remote": False,
        },
    ]
}


class TestJSearchExtra:
    def test_http_401_translates_to_invalid_key(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("401", response=_resp(401))
        monkeypatch.setattr(jsearch, "fetch_json", fake)
        cfg.jsearch_api_key = "k"
        with pytest.raises(RuntimeError, match="Invalid API key"):
            jsearch.fetch("python", cfg=cfg)

    def test_http_429_translates_to_rate_limit(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("429", response=_resp(429))
        monkeypatch.setattr(jsearch, "fetch_json", fake)
        cfg.jsearch_api_key = "k"
        with pytest.raises(RuntimeError, match="Rate limit"):
            jsearch.fetch("python", cfg=cfg)

    def test_http_500_translates_to_generic_error(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("500", response=_resp(500))
        monkeypatch.setattr(jsearch, "fetch_json", fake)
        cfg.jsearch_api_key = "k"
        with pytest.raises(RuntimeError, match="HTTP 500"):
            jsearch.fetch("python", cfg=cfg)

    def test_connection_error_translates_to_runtime(self, monkeypatch, cfg):
        from requests import ConnectionError
        def fake(*a, **k):
            raise ConnectionError("no internet")
        monkeypatch.setattr(jsearch, "fetch_json", fake)
        cfg.jsearch_api_key = "k"
        with pytest.raises(RuntimeError, match="No internet"):
            jsearch.fetch("python", cfg=cfg)

    def test_date_filter_1_day_maps_to_today(self, monkeypatch, cfg):
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return JS_RESPONSE
        monkeypatch.setattr(jsearch, "fetch_json", fake)
        cfg.jsearch_api_key = "k"
        jsearch.fetch("python", cfg=cfg, date_filter=1)
        assert seen["params"]["date_posted"] == "today"

    def test_date_filter_7_days_maps_to_week(self, monkeypatch, cfg):
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return JS_RESPONSE
        monkeypatch.setattr(jsearch, "fetch_json", fake)
        cfg.jsearch_api_key = "k"
        jsearch.fetch("python", cfg=cfg, date_filter=7)
        assert seen["params"]["date_posted"] == "week"

    def test_date_filter_30_days_maps_to_month(self, monkeypatch, cfg):
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return JS_RESPONSE
        monkeypatch.setattr(jsearch, "fetch_json", fake)
        cfg.jsearch_api_key = "k"
        jsearch.fetch("python", cfg=cfg, date_filter=30)
        assert seen["params"]["date_posted"] == "month"

    def test_job_type_full_time_maps_to_fulltime(self, monkeypatch, cfg):
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return JS_RESPONSE
        monkeypatch.setattr(jsearch, "fetch_json", fake)
        cfg.jsearch_api_key = "k"
        jsearch.fetch("python", cfg=cfg, job_type="Full-time")
        assert seen["params"]["employment_types"] == "FULLTIME"

    def test_job_type_contract_maps_to_contractor(self, monkeypatch, cfg):
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return JS_RESPONSE
        monkeypatch.setattr(jsearch, "fetch_json", fake)
        cfg.jsearch_api_key = "k"
        jsearch.fetch("python", cfg=cfg, job_type="Contract")
        assert seen["params"]["employment_types"] == "CONTRACTOR"

    def test_job_type_internship_maps_to_intern(self, monkeypatch, cfg):
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return JS_RESPONSE
        monkeypatch.setattr(jsearch, "fetch_json", fake)
        cfg.jsearch_api_key = "k"
        jsearch.fetch("python", cfg=cfg, job_type="Internship")
        assert seen["params"]["employment_types"] == "INTERN"

    def test_is_configured_reflects_key(self, cfg):
        assert jsearch.is_configured(cfg) is False
        cfg.jsearch_api_key = "k"
        assert jsearch.is_configured(cfg) is True


# ── Adzuna additional coverage (some missed branches) ──────────────────────

class TestAdzunaExtra:
    def test_http_403_translates_to_generic_error(self, monkeypatch, cfg):
        # Adzuna only special-cases 401 as invalid creds; 403 falls through to
        # the generic HTTP error message.
        def fake(*a, **k):
            raise requests.HTTPError("403", response=_resp(403))
        monkeypatch.setattr(adzuna, "fetch_json", fake)
        cfg.adzuna_app_id, cfg.adzuna_api_key = "id", "key"
        with pytest.raises(RuntimeError, match="HTTP 403"):
            adzuna.fetch("python", cfg=cfg)

    def test_http_500_translates_to_generic_error(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("500", response=_resp(500))
        monkeypatch.setattr(adzuna, "fetch_json", fake)
        cfg.adzuna_app_id, cfg.adzuna_api_key = "id", "key"
        with pytest.raises(RuntimeError, match="HTTP 500"):
            adzuna.fetch("python", cfg=cfg)

    def test_connection_error_translates_to_runtime(self, monkeypatch, cfg):
        from requests import ConnectionError
        def fake(*a, **k):
            raise ConnectionError("no internet")
        monkeypatch.setattr(adzuna, "fetch_json", fake)
        cfg.adzuna_app_id, cfg.adzuna_api_key = "id", "key"
        with pytest.raises(RuntimeError, match="No internet"):
            adzuna.fetch("python", cfg=cfg)

    def test_date_filter_passes_max_days_old(self, monkeypatch, cfg):
        fx = load_fixture("adzuna.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(adzuna, "fetch_json", fake)
        cfg.adzuna_app_id, cfg.adzuna_api_key = "id", "key"
        adzuna.fetch("python", cfg=cfg, date_filter=7)
        assert seen["params"]["max_days_old"] == 7

    def test_job_type_full_time_param(self, monkeypatch, cfg):
        fx = load_fixture("adzuna.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(adzuna, "fetch_json", fake)
        cfg.adzuna_app_id, cfg.adzuna_api_key = "id", "key"
        adzuna.fetch("python", cfg=cfg, job_type="Full-time")
        assert seen["params"]["full_time"] == 1

    def test_job_type_contract_param(self, monkeypatch, cfg):
        fx = load_fixture("adzuna.json")
        seen: dict = {}
        def fake(*a, **k):
            seen.update(k)
            return fx
        monkeypatch.setattr(adzuna, "fetch_json", fake)
        cfg.adzuna_app_id, cfg.adzuna_api_key = "id", "key"
        adzuna.fetch("python", cfg=cfg, job_type="Contract")
        assert seen["params"]["contract"] == 1


# ── Findwork additional coverage ──────────────────────────────────────────

class TestFindworkExtra:
    def test_http_401_translates_to_generic_error(self, monkeypatch, cfg):
        # Findwork only special-cases 403 as invalid key; 401 falls through to
        # the generic HTTP error message.
        def fake(*a, **k):
            raise requests.HTTPError("401", response=_resp(401))
        monkeypatch.setattr(findwork, "fetch_json", fake)
        cfg.findwork_api_key = "k"
        with pytest.raises(RuntimeError, match="HTTP 401"):
            findwork.fetch("python", cfg=cfg)

    def test_http_403_translates_to_invalid_key(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("403", response=_resp(403))
        monkeypatch.setattr(findwork, "fetch_json", fake)
        cfg.findwork_api_key = "k"
        with pytest.raises(RuntimeError, match="Invalid API key"):
            findwork.fetch("python", cfg=cfg)

    def test_http_500_translates_to_generic_error(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("500", response=_resp(500))
        monkeypatch.setattr(findwork, "fetch_json", fake)
        cfg.findwork_api_key = "k"
        with pytest.raises(RuntimeError, match="HTTP 500"):
            findwork.fetch("python", cfg=cfg)

    def test_connection_error_translates_to_runtime(self, monkeypatch, cfg):
        from requests import ConnectionError
        def fake(*a, **k):
            raise ConnectionError("no internet")
        monkeypatch.setattr(findwork, "fetch_json", fake)
        cfg.findwork_api_key = "k"
        with pytest.raises(RuntimeError, match="No internet"):
            findwork.fetch("python", cfg=cfg)

    def test_is_configured_reflects_key(self, cfg):
        assert findwork.is_configured(cfg) is False
        cfg.findwork_api_key = "k"
        assert findwork.is_configured(cfg) is True


# ── Dynamic config regression (PR_REVIEW_ARCHITECTURE_v2 P1-1) ──────────────

class TestDynamicConfig:
    """P1-1 fix: env mutations after import must propagate to the registry's
    `is_configured` flag (previously frozen at first import via `_C = load_config()`
    at the top of sources/__init__.py).

    Uses Config(db_path=tmp_path / 't.db') to avoid the test-isolation
    regression flagged in PR_REVIEW_TESTS_v2 P1-2 (Config() with default
    db_path creates an empty data/ dir in the working tree).
    """

    def test_registry_uses_dynamic_is_configured_fn(self):
        """Each free-key source's REGISTRY entry has `is_configured_fn` set
        (not the legacy frozen `configured` bool)."""
        from jobsearch.sources import REGISTRY
        for name in ("Adzuna", "JSearch", "USAJobs", "Findwork",
                     "Jooble", "Careerjet", "JobSpy"):
            src = REGISTRY[name]
            assert src.is_configured_fn is not None, (
                f"{name} registry entry must set is_configured_fn for dynamic "
                f"config evaluation (PR_REVIEW_ARCHITECTURE_v2 P1-1)"
            )

    def test_is_configured_evaluated_at_call_time(self):
        """If we change cfg AFTER the registry was built, the new value must
        be reflected when search_all_sources() is called."""
        from jobsearch.config import Config
        from jobsearch.sources import REGISTRY
        cfg = Config(db_path=Path("/tmp/test_dynamic_config.db"))
        # Adzuna starts unconfigured
        adzuna = REGISTRY["Adzuna"]
        assert adzuna.is_configured(cfg) is False
        # Set the keys on the cfg object — the next is_configured() call must
        # reflect this WITHOUT rebuilding the registry.
        cfg.adzuna_app_id = "test-id"
        cfg.adzuna_api_key = "test-key"
        assert adzuna.is_configured(cfg) is True

    def test_search_all_sources_skips_unconfigured_via_dynamic_check(self):
        """search_all_sources() must skip unconfigured sources at call time,
        not at import time. This means a source whose env was unset at import
        but set later DOES get queried."""
        from jobsearch.config import Config
        from jobsearch.sources import search_all_sources, REGISTRY
        cfg = Config(db_path=Path("/tmp/test_dynamic_skip.db"))  # no env keys set
        # Adzuna is unconfigured in this cfg; the aggregator must skip it.
        results = search_all_sources("python", "Remote",
                                      sources=["Adzuna"], cfg=cfg)
        # Adzuna was skipped (not configured); results is empty list (no task
        # was queued for it).
        assert results == []
