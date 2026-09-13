"""Free-key board clients against replayed fixtures — no network.

Covers the 6 ResumeWing lifts (adzuna, jsearch, usajobs, findwork, jooble,
careerjet). Each source's `fetch_json` is monkeypatched (same pattern as
test_sources.py: patch the name as imported into the source module, not
jobsearch.sources.base.fetch_json). Live tests are marked `live` and skip
with a clear message when the relevant env key isn't set.
"""
from __future__ import annotations

import json
import re

import pytest
import requests

from jobsearch.config import Config
from jobsearch.sources import (
    adzuna, careerjet, findwork, jooble, jsearch, usajobs,
)

from conftest import SOURCE_FIXTURES


# ── helpers ─────────────────────────────────────────────────────────────────

def load_fixture(name: str):
    return json.loads((SOURCE_FIXTURES / name).read_text(encoding="utf-8"))


def _resp(status: int) -> requests.Response:
    """Build a real-enough Response for HTTPError(response=...)."""
    r = requests.Response()
    r.status_code = status
    return r


# JSearch (RapidAPI) has no committed fixture file — its response shape is
# documented inline below, reconstructed from jsearch.py's parse logic
# (job_title / employer_name / job_apply_link / apply_options / job_description
# / job_city / job_state / job_min_salary / job_posted_at_datetime_utc /
# job_is_remote), since a RapidAPI capture needs a live key.
JS_RESPONSE = {
    "data": [
        {
            "job_id": "js1",
            "job_title": "Senior Python Developer",
            "employer_name": "Acme Cloud",
            "job_apply_link": "https://acme.com/careers/js1",
            "apply_options": [
                {"publisher": "LinkedIn", "apply_link": "https://linkedin.com/js1"},
                {"publisher": "Indeed",   "apply_link": "https://indeed.com/js1"},
            ],
            "job_description": (
                "Senior Python developer. FastAPI, PostgreSQL, AWS. "
                "H1B visa sponsorship available. Apply to jobs@acme.cloud"
            ),
            "job_city": "Austin",
            "job_state": "TX",
            "job_min_salary": 130000,
            "job_max_salary": 170000,
            "job_salary_currency": "USD",
            "job_posted_at_datetime_utc": "2026-08-20T10:00:00.000Z",
            "job_is_remote": False,
        },
        {
            "job_id": "js2",
            "job_title": "Backend Engineer",
            "employer_name": "BetaData",
            "job_apply_link": "",          # forces apply_options fallback
            "apply_options": [
                {"publisher": "Workday", "apply_link": "https://wd.com/js2"}
            ],
            "job_description": "Backend engineer. Python, Django, Redis.",
            "job_city": "",
            "job_state": "",
            "job_min_salary": None,
            "job_max_salary": None,
            "job_posted_at_datetime_utc": "2026-08-19T08:00:00.000Z",
            "job_is_remote": True,
        },
    ]
}


# ── Adzuna ──────────────────────────────────────────────────────────────────

class TestAdzuna:
    def test_fetch_parses_fixture(self, monkeypatch, cfg):
        fx = load_fixture("adzuna.json")
        seen: dict = {}

        def fake(url, *, params=None, cfg=None, headers=None,
                method="GET", json=None, auth=None):
            seen["url"], seen["params"] = url, params
            return fx

        monkeypatch.setattr(adzuna, "fetch_json", fake)
        cfg.adzuna_app_id, cfg.adzuna_api_key = "id", "key"
        jobs = adzuna.fetch("python", location="New York, NY",
                            num_results=2, cfg=cfg)

        # 3 rows in fixture, 3rd lacks redirect_url -> skipped by _normalize
        assert len(jobs) == 2
        assert "/us/search/1" in seen["url"]          # US country detected
        assert seen["params"]["app_id"] == "id"
        assert seen["params"]["what"] == "python"
        assert seen["params"]["sort_by"] == "date"

        j = jobs[0]
        assert j.title == "Python Backend Engineer"
        assert j.company == "Acme Cloud"
        assert j.source == "Adzuna"
        assert j.link == "https://www.adzuna.com/details/adz/a1"
        assert j.location == "New York, NY"
        assert j.date_posted == "2026-08-20"
        assert j.salary_text == "$120,000 – $160,000/yr"
        assert j.salary_min == 120000.0
        assert j.salary_max == 160000.0
        assert j.h1b_mention is True          # "Visa sponsorship available"
        assert j.contact_email == "jobs@acme.cloud"
        assert j.remote is False              # "remote" not in row-1 text

    def test_country_detection_gb_changes_currency(self, monkeypatch, cfg):
        fx = load_fixture("adzuna.json")
        seen: dict = {}
        monkeypatch.setattr(adzuna, "fetch_json",
                            lambda url, **k: (seen.__setitem__("url", url), fx)[1])
        cfg.adzuna_app_id, cfg.adzuna_api_key = "id", "key"
        jobs = adzuna.fetch("python", location="London", num_results=2, cfg=cfg)
        assert "/gb/search/" in seen["url"]
        assert jobs[0].salary_text.startswith("£")   # GBP currency

    def test_fetch_not_configured_raises(self, cfg):
        with pytest.raises(RuntimeError, match="Adzuna credentials"):
            adzuna.fetch("python", cfg=cfg)

    def test_fetch_http_401_translates_to_runtime(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("401", response=_resp(401))
        monkeypatch.setattr(adzuna, "fetch_json", fake)
        cfg.adzuna_app_id, cfg.adzuna_api_key = "id", "key"
        with pytest.raises(RuntimeError, match="Invalid credentials"):
            adzuna.fetch("python", cfg=cfg)

    def test_is_configured_reflects_keys(self, cfg):
        assert adzuna.is_configured(cfg) is False
        cfg.adzuna_app_id, cfg.adzuna_api_key = "i", "k"
        assert adzuna.is_configured(cfg) is True


# ── JSearch ─────────────────────────────────────────────────────────────────

class TestJSearch:
    def test_fetch_parses_response(self, monkeypatch, cfg):
        seen: dict = {}

        def fake(url, *, params=None, cfg=None, headers=None,
                method="GET", json=None, auth=None):
            seen["url"], seen["params"], seen["headers"] = url, params, headers
            return JS_RESPONSE

        monkeypatch.setattr(jsearch, "fetch_json", fake)
        cfg.jsearch_api_key = "rapid-key"
        # num_results=2 fits one JSearch page (10/page) so the fixture replays once.
        jobs = jsearch.fetch("python", location="Austin, TX", num_results=2, cfg=cfg)

        assert len(jobs) == 2
        assert seen["url"] == "https://jsearch.p.rapidapi.com/search"
        assert seen["headers"]["X-RapidAPI-Key"] == "rapid-key"
        assert seen["headers"]["X-RapidAPI-Host"] == "jsearch.p.rapidapi.com"
        assert seen["params"]["query"] == "python in Austin, TX"
        assert seen["params"]["country"] == "us"

        j = jobs[0]
        assert j.title == "Senior Python Developer"
        assert j.company == "Acme Cloud"
        assert j.source == "JSearch"
        assert j.link == "https://acme.com/careers/js1"     # primary apply link
        assert j.location == "Austin, TX"
        assert j.date_posted == "2026-08-20"
        assert j.salary_text == "$130,000 – $170,000/yr"
        assert j.salary_min == 130000.0 and j.salary_max == 170000.0
        assert j.h1b_mention is True
        assert j.contact_email == "jobs@acme.cloud"
        assert j.remote is False

    def test_apply_options_fallback_when_no_apply_link(self, monkeypatch, cfg):
        monkeypatch.setattr(jsearch, "fetch_json", lambda *a, **k: JS_RESPONSE)
        cfg.jsearch_api_key = "k"
        jobs = jsearch.fetch("python", num_results=2, cfg=cfg)
        # row 2 has job_apply_link="" -> falls back to apply_options[0]
        assert jobs[1].link == "https://wd.com/js2"
        assert jobs[1].remote is True       # job_is_remote=True

    def test_remote_search_omits_location_in_query(self, monkeypatch, cfg):
        seen: dict = {}
        monkeypatch.setattr(jsearch, "fetch_json",
                            lambda *a, **k: (seen.__setitem__("p", k.get("params")), JS_RESPONSE)[1])
        cfg.jsearch_api_key = "k"
        jsearch.fetch("python", location="Remote", num_results=1, cfg=cfg)
        assert seen["p"]["query"] == "python"
        assert seen["p"].get("remote_jobs_only") == "true"

    def test_fetch_not_configured_raises(self, cfg):
        with pytest.raises(RuntimeError, match="JSearch API key"):
            jsearch.fetch("python", cfg=cfg)


# ── USAJobs ──────────────────────────────────────────────────────────────────

class TestUSAJobs:
    def test_fetch_parses_fixture(self, monkeypatch, cfg):
        fx = load_fixture("usajobs.json")
        seen: dict = {}

        def fake(url, *, params=None, cfg=None, headers=None,
                method="GET", json=None, auth=None):
            seen["url"], seen["params"], seen["headers"] = url, params, headers
            return fx

        monkeypatch.setattr(usajobs, "fetch_json", fake)
        cfg.usajobs_api_key, cfg.usajobs_user_agent = "key", "me@example.com"
        jobs = usajobs.fetch("python", location="Washington, DC", cfg=cfg)

        assert len(jobs) == 2
        assert seen["url"] == "https://data.usajobs.gov/api/search"
        assert seen["headers"]["Authorization-Key"] == "key"
        assert seen["headers"]["User-Agent"] == "me@example.com"
        assert seen["headers"]["Host"] == "data.usajobs.gov"
        assert seen["params"]["Keyword"] == "python"
        assert seen["params"]["SortField"] == "OpenDate"

        j = jobs[0]
        assert j.title == "IT Specialist (Python Developer)"
        assert j.company == "Department of Veterans Affairs"
        assert j.source == "USAJobs"
        assert j.link == "https://www.usajobs.gov/job/771234001"
        assert j.location == "Washington, DC"
        assert j.date_posted == "2026-08-15"
        assert j.salary_text == "$60,000 – $90,000/yr"
        assert j.salary_min == 60000.0 and j.salary_max == 90000.0
        # Federal jobs never sponsor — explicit False (not detect_h1b).
        assert j.h1b_mention is False
        assert j.contact_email is None       # structured apply flow, no email

    def test_row_without_remuneration_has_no_salary(self, monkeypatch, cfg):
        fx = load_fixture("usajobs.json")
        monkeypatch.setattr(usajobs, "fetch_json", lambda *a, **k: fx)
        cfg.usajobs_api_key, cfg.usajobs_user_agent = "k", "me@x.com"
        # concrete location: remote searches filter to remote-eligible jobs
        # only, and the fixture's IT Specialist is onsite.
        jobs = usajobs.fetch("python", location="Washington, DC", cfg=cfg)
        assert jobs[1].salary_text is None
        assert jobs[1].salary_min is None and jobs[1].salary_max is None

    def test_fetch_not_configured_raises(self, cfg):
        with pytest.raises(RuntimeError, match="USAJobs credentials"):
            usajobs.fetch("python", cfg=cfg)

    def test_is_configured_requires_both_key_and_user_agent(self, cfg):
        assert usajobs.is_configured(cfg) is False
        cfg.usajobs_api_key = "k"
        assert usajobs.is_configured(cfg) is False      # user_agent missing too
        cfg.usajobs_user_agent = "me@x.com"
        assert usajobs.is_configured(cfg) is True

    def test_remote_location_is_omitted(self, monkeypatch, cfg):
        """LocationName=Remote matches nothing on USAJobs (verified live
        2026-08-27) — the adapter must drop it and search nationwide. The
        API's RemoteIndicator param is ALSO dead (verified live
        2026-08-29: 0 results on every query) — the adapter must NOT set
        it; remote filtering happens client-side."""
        fx = load_fixture("usajobs.json")
        seen: dict = {}

        def fake(url, *, params=None, cfg=None, headers=None,
                method="GET", json=None, auth=None):
            seen["params"] = params
            return fx

        monkeypatch.setattr(usajobs, "fetch_json", fake)
        cfg.usajobs_api_key, cfg.usajobs_user_agent = "k", "me@x.com"
        usajobs.fetch("python", location="Remote", cfg=cfg)
        assert "LocationName" not in seen["params"]
        assert "RemoteIndicator" not in seen["params"]
        # Real locations still pass through.
        usajobs.fetch("python", location="Washington, DC", cfg=cfg)
        assert seen["params"]["LocationName"] == "Washington, DC"

    def test_pagination_satisfies_num_results_after_filtering(
            self, monkeypatch, cfg):
        """CodeRabbit round-3: client-side filtering must not silently
        shrink the result set — the adapter pages (paced) until
        num_results is satisfied or the API is exhausted."""
        fx = load_fixture("usajobs.json")
        items = fx["SearchResult"]["SearchResultItems"]
        onsite = items[0]                      # "Washington, DC" → filtered
        remote = items[1]                      # "Remote eligible" → kept
        pages: list[dict] = []

        def fake(url, *, params=None, cfg=None, headers=None,
                 method="GET", json=None, auth=None):
            params = dict(params or {})
            pages.append(params)
            if params.get("Page", 1) == 1:
                # a FULL page (25 rows = per_page) of filterable rows
                return {"SearchResult": {"SearchResultItems": [onsite] * 25}}
            # page 2: enough remote rows to satisfy num_results
            return {"SearchResult": {"SearchResultItems":
                    [remote, dict(remote)]}}

        monkeypatch.setattr(usajobs, "fetch_json", fake)
        monkeypatch.setattr(usajobs.time, "sleep", lambda s: None)
        cfg.usajobs_api_key, cfg.usajobs_user_agent = "k", "me@x.com"
        jobs = usajobs.fetch("python", location="Remote",
                             num_results=2, cfg=cfg)
        assert len(jobs) == 2                  # satisfied despite filtering
        assert len(pages) == 2                 # paged exactly once more
        assert pages[0]["Page"] == 1 and pages[1]["Page"] == 2
        # single-page short result: no second request when the API is
        # already exhausted (fewer items than requested per page)
        pages.clear()
        usajobs.fetch("python", location="Washington, DC",
                      num_results=5, cfg=cfg)
        assert len(pages) == 1

    def test_403_falls_back_to_zenrows(self, monkeypatch, cfg):
        """Direct 403 (Akamai geo-block) retries through the ZenRows US
        proxy transport (verified live 2026-08-27: direct 403 → proxied 200
        with the same stored key)."""
        fx = load_fixture("usajobs.json")
        calls: list[str] = []

        def direct_403(url, *, params=None, cfg=None, headers=None,
                       method="GET", json=None, auth=None):
            calls.append("direct")
            raise requests.HTTPError(response=_resp(403))

        def fake_zenrows(url, *, params=None, headers=None, cfg=None,
                         proxy_country="us", js_render=False, method="GET",
                         json_body=None, timeout=None):
            calls.append(f"zenrows:{proxy_country}")
            return fx

        monkeypatch.setattr(usajobs, "fetch_json", direct_403)
        monkeypatch.setattr(usajobs, "zenrows_fetch_json", fake_zenrows)
        cfg.usajobs_api_key, cfg.usajobs_user_agent = "k", "me@x.com"
        cfg.zenrows_api_key = "zr-key"
        jobs = usajobs.fetch("python", location="Washington, DC", cfg=cfg)

        assert calls == ["direct", "zenrows:us"]
        assert len(jobs) == 2
        assert jobs[0].source == "USAJobs"

    def test_403_without_zenrows_key_raises(self, monkeypatch, cfg):
        def direct_403(url, **kwargs):
            raise requests.HTTPError(response=_resp(403))

        monkeypatch.setattr(usajobs, "fetch_json", direct_403)
        cfg.usajobs_api_key, cfg.usajobs_user_agent = "k", "me@x.com"
        with pytest.raises(RuntimeError, match="geo-blocked|ZENROWS"):
            usajobs.fetch("python", cfg=cfg)

    def test_403_prefers_netlify_over_zenrows(self, monkeypatch, cfg):
        """Both US-proxy transports configured → the free Netlify edge
        scraper is tried FIRST (ZenRows credits are finite); ZenRows is
        the last resort only."""
        fx = load_fixture("usajobs.json")
        calls: list[str] = []

        def direct_403(url, **kwargs):
            calls.append("direct")
            raise requests.HTTPError(response=_resp(403))

        def fake_netlify(url, *, params=None, headers=None, cfg=None,
                         method="GET", timeout=None):
            calls.append("netlify")
            return fx

        def fake_zenrows(url, **kwargs):
            calls.append("zenrows")
            return fx

        monkeypatch.setattr(usajobs, "fetch_json", direct_403)
        monkeypatch.setattr(usajobs, "netlify_fetch_json", fake_netlify)
        monkeypatch.setattr(usajobs, "zenrows_fetch_json", fake_zenrows)
        cfg.usajobs_api_key, cfg.usajobs_user_agent = "k", "me@x.com"
        cfg.netlify_scraper_token = "nfl-token"
        cfg.zenrows_api_key = "zr-key"
        jobs = usajobs.fetch("python", location="Washington, DC", cfg=cfg)

        assert calls == ["direct", "netlify"]
        assert len(jobs) == 2

    def test_403_netlify_failure_falls_through_to_zenrows(
            self, monkeypatch, cfg):
        """Netlify transport error → the chain continues to ZenRows."""
        fx = load_fixture("usajobs.json")
        calls: list[str] = []

        def direct_403(url, **kwargs):
            calls.append("direct")
            raise requests.HTTPError(response=_resp(403))

        def netlify_down(url, **kwargs):
            calls.append("netlify:failed")
            raise RuntimeError("Netlify scraper error HTTP 502")

        def fake_zenrows(url, **kwargs):
            calls.append("zenrows")
            return fx

        monkeypatch.setattr(usajobs, "fetch_json", direct_403)
        monkeypatch.setattr(usajobs, "netlify_fetch_json", netlify_down)
        monkeypatch.setattr(usajobs, "zenrows_fetch_json", fake_zenrows)
        cfg.usajobs_api_key, cfg.usajobs_user_agent = "k", "me@x.com"
        cfg.netlify_scraper_token = "nfl-token"
        cfg.zenrows_api_key = "zr-key"
        jobs = usajobs.fetch("python", location="Washington, DC", cfg=cfg)

        assert calls == ["direct", "netlify:failed", "zenrows"]
        assert len(jobs) == 2


# ── Findwork ─────────────────────────────────────────────────────────────────

class TestFindwork:
    def test_fetch_parses_fixture(self, monkeypatch, cfg):
        fx = load_fixture("findwork.json")
        seen: dict = {}

        def fake(url, *, params=None, cfg=None, headers=None,
                method="GET", json=None, auth=None):
            seen["url"], seen["params"], seen["headers"] = url, params, headers
            return fx

        monkeypatch.setattr(findwork, "fetch_json", fake)
        cfg.findwork_api_key = "fw-token"
        jobs = findwork.fetch("python", location="Remote", cfg=cfg)

        assert len(jobs) == 2
        assert seen["url"] == "https://findwork.dev/api/jobs/"
        assert seen["headers"]["Authorization"] == "Token fw-token"
        assert seen["params"]["search"] == "python"
        assert seen["params"].get("remote_ok") == "true"     # Remote search

        j = jobs[0]
        assert j.title == "Senior Python Engineer"
        assert j.company == "Acme Cloud"
        assert j.source == "Findwork"
        assert j.link == "https://findwork.dev/job/12345-senior-python-engineer"
        assert j.location == "Remote"
        assert j.date_posted == "2026-08-20"
        assert j.remote is True
        assert j.h1b_mention is True
        assert j.contact_email == "jobs@acme.dev"

    def test_fetch_http_403_translates_to_runtime(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise requests.HTTPError("403", response=_resp(403))
        monkeypatch.setattr(findwork, "fetch_json", fake)
        cfg.findwork_api_key = "k"
        with pytest.raises(RuntimeError, match="Invalid API key"):
            findwork.fetch("python", cfg=cfg)

    def test_fetch_not_configured_raises(self, cfg):
        with pytest.raises(RuntimeError, match="Findwork API key"):
            findwork.fetch("python", cfg=cfg)


# ── Jooble ───────────────────────────────────────────────────────────────────

class TestJooble:
    def test_fetch_parses_fixture(self, monkeypatch, cfg):
        fx = load_fixture("jooble.json")
        seen: dict = {}

        def fake(url, *, params=None, cfg=None, headers=None,
                method="GET", json=None, auth=None):
            seen["url"], seen["method"], seen["json"], seen["auth"] = (
                url, method, json, auth)
            return fx

        monkeypatch.setattr(jooble, "fetch_json", fake)
        cfg.jooble_api_key = "jb-key"
        jobs = jooble.fetch("python", location="Remote", cfg=cfg)

        assert len(jobs) == 2
        # Jooble is POST with the key in the URL path and a JSON body.
        assert seen["url"] == "https://jooble.org/api/jb-key"
        assert seen["method"] == "POST"
        assert seen["json"]["keywords"] == "python"
        assert seen["json"]["location"] == "Remote"

        j = jobs[0]
        assert j.title == "Python Developer"
        assert j.company == "Acme Cloud"
        assert j.source == "Jooble"
        assert j.link == "https://jooble.org/JobRedirect/abc123"
        assert j.date_posted == "2026-08-18"
        assert j.salary_text == "$90k - $130k"      # raw salary string
        assert j.remote is True
        assert j.h1b_mention is True
        assert j.contact_email == "jobs@acme.cloud"

    def test_fetch_not_configured_raises(self, cfg):
        with pytest.raises(RuntimeError, match="Jooble API key"):
            jooble.fetch("python", cfg=cfg)


# ── Careerjet ────────────────────────────────────────────────────────────────

class TestCareerjet:
    def test_fetch_parses_fixture(self, monkeypatch, cfg):
        fx = load_fixture("careerjet.json")
        seen: dict = {}

        def fake(url, *, params=None, cfg=None, headers=None,
                method="GET", json=None, auth=None):
            seen["url"], seen["params"], seen["auth"] = url, params, auth
            return fx

        monkeypatch.setattr(careerjet, "fetch_json", fake)
        cfg.careerjet_api_key = "cj-key"
        jobs = careerjet.fetch("python", location="Austin, TX", cfg=cfg)

        assert len(jobs) == 2
        assert seen["url"] == "https://search.api.careerjet.net/v4/query"
        # HTTP Basic auth: API key as username, empty password.
        assert seen["auth"] == ("cj-key", "")
        assert seen["params"]["keywords"] == "python"
        assert seen["params"]["location"] == "Austin, TX"
        assert seen["params"]["locale_code"] == "en_US"

        j = jobs[0]
        assert j.title == "Python Developer"
        assert j.company == "Acme Cloud"
        assert j.source == "Careerjet"
        assert j.link == "https://www.careerjet.com/jobad/abc123"
        # RFC 2822 date -> YYYY-MM-DD
        assert j.date_posted == "2026-08-18"
        # Salary built from structured min/max + currency + type
        assert j.salary_text == "USD 90,000 – 130,000 / yr"
        assert j.salary_min == 90000.0 and j.salary_max == 130000.0
        assert j.h1b_mention is True
        assert j.contact_email == "jobs@acme.cloud"
        assert j.remote is False         # locations="Austin, TX" not remote

    def test_row_without_salary(self, monkeypatch, cfg):
        fx = load_fixture("careerjet.json")
        monkeypatch.setattr(careerjet, "fetch_json", lambda *a, **k: fx)
        cfg.careerjet_api_key = "k"
        jobs = careerjet.fetch("python", cfg=cfg)
        # row 2 has no salary fields -> _format_salary returns ("", None, None)
        assert not jobs[1].salary_text
        assert jobs[1].salary_min is None and jobs[1].salary_max is None
        assert jobs[1].remote is True       # locations="Remote"

    def test_non_jobs_response_raises(self, monkeypatch, cfg):
        # Careerjet returns type=LOCATIONS for disambiguation, not job listings.
        monkeypatch.setattr(careerjet, "fetch_json",
                            lambda *a, **k: {"type": "LOCATIONS",
                                             "message": "ambiguous"})
        cfg.careerjet_api_key = "k"
        with pytest.raises(RuntimeError, match="Unexpected response"):
            careerjet.fetch("python", cfg=cfg)

    def test_fetch_not_configured_raises(self, cfg):
        with pytest.raises(RuntimeError, match="Careerjet API key"):
            careerjet.fetch("python", cfg=cfg)

    def test_referer_header_sent_when_configured(self, monkeypatch, cfg):
        """The registered-site Referer is what authorizes the call (IP
        allowlist is only consulted without it — verified live 2026-08-27)."""
        fx = load_fixture("careerjet.json")
        seen: dict = {}

        def fake(url, *, params=None, cfg=None, headers=None,
                method="GET", json=None, auth=None):
            seen["headers"] = headers
            return fx

        monkeypatch.setattr(careerjet, "fetch_json", fake)
        cfg.careerjet_api_key = "k"
        cfg.careerjet_referer = "https://mysite.example/find-jobs/"
        careerjet.fetch("python", cfg=cfg)
        assert seen["headers"]["Referer"] == "https://mysite.example/find-jobs/"

        # Without the knob: no Referer header, still a UA.
        cfg.careerjet_referer = ""
        careerjet.fetch("python", cfg=cfg)
        assert "Referer" not in seen["headers"]
        assert "User-Agent" in seen["headers"]

    def test_403_error_mentions_referer(self, monkeypatch, cfg):
        def fake_403(url, **kwargs):
            raise requests.HTTPError(response=_resp(403))

        monkeypatch.setattr(careerjet, "fetch_json", fake_403)
        cfg.careerjet_api_key = "k"
        with pytest.raises(RuntimeError, match="Referer"):
            careerjet.fetch("python", cfg=cfg)


# ── Config env resolution (cross-source) ────────────────────────────────────

class TestConfigEnvResolution:
    def test_config_resolves_keys_from_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JSEARCH_API_KEY", "env-jsk")
        monkeypatch.setenv("FINDWORK_API_KEY", "env-fw")
        monkeypatch.setenv("ADZUNA_APP_ID", "env-id")
        monkeypatch.setenv("ADZUNA_API_KEY", "env-key")
        monkeypatch.setenv("USAJOBS_API_KEY", "env-usa")
        monkeypatch.setenv("USAJOBS_USER_AGENT", "env@x.com")
        monkeypatch.setenv("JOOBLE_API_KEY", "env-jb")
        monkeypatch.setenv("CAREERJET_API_KEY", "env-cj")
        cfg = Config(db_path=tmp_path / "t.db")
        assert cfg.jsearch_api_key == "env-jsk"
        assert cfg.findwork_api_key == "env-fw"
        assert cfg.adzuna_app_id == "env-id" and cfg.adzuna_api_key == "env-key"
        assert cfg.usajobs_api_key == "env-usa" and cfg.usajobs_user_agent == "env@x.com"
        assert cfg.jooble_api_key == "env-jb"
        assert cfg.careerjet_api_key == "env-cj"
        # All six sources see themselves as configured.
        for m in (adzuna, jsearch, usajobs, findwork, jooble, careerjet):
            assert m.is_configured(cfg) is True

    def test_db_path_not_clobbered_by_env(self, monkeypatch, tmp_path):
        # An explicit db_path passed to Config() survives even if JOBSEARCH_DB
        # were set in the env (we don't touch path fields in __post_init__).
        monkeypatch.setenv("JOBSEARCH_DB", "/should/not/overwrite")
        cfg = Config(db_path=tmp_path / "t.db")
        assert cfg.db_path == tmp_path / "t.db"


# ── Live tests (deselected by default; -m live to run) ─────────────────────

def _live_skip_message(env_key: str, source: str) -> str:
    return (f"Set {env_key} (and see worklog SRC-FREEKEY) to run the "
            f"live {source} test")


class TestLiveFreeKey:
    """Live tests for the 6 free-key sources.

    Policy (PR_REVIEW_TESTS_v2 P0-1 fix): a live test that returns 0 jobs
    is a FAILURE — the fetch succeeded but produced nothing useful.
    RuntimeError (network/HTTP errors) skips cleanly so environmental
    issues don't mask real code bugs.
    """

    @pytest.mark.live
    def test_live_adzuna(self):
        from jobsearch.config import load_config
        cfg = load_config()
        if not adzuna.is_configured(cfg):
            pytest.skip(_live_skip_message(
                "ADZUNA_APP_ID + ADZUNA_API_KEY", "Adzuna"))
        try:
            jobs = adzuna.fetch("python", location="New York, NY",
                                num_results=5, cfg=cfg)
        except RuntimeError as exc:
            pytest.skip(f"Adzuna live API unreachable: {exc}")
        assert isinstance(jobs, list)
        assert len(jobs) > 0, (
            "Adzuna returned 0 jobs — transient outage or shape drift. "
            "Investigate before merging changes to adzuna.py."
        )
        for j in jobs:
            assert j.source == "Adzuna"
            assert j.title and j.company

    @pytest.mark.live
    def test_live_jsearch(self):
        from jobsearch.config import load_config
        cfg = load_config()
        if not jsearch.is_configured(cfg):
            pytest.skip(_live_skip_message("JSEARCH_API_KEY", "JSearch"))
        try:
            jobs = jsearch.fetch("python", location="Austin, TX",
                                 num_results=5, cfg=cfg)
        except RuntimeError as exc:
            pytest.skip(f"JSearch live API unreachable: {exc}")
        assert isinstance(jobs, list)
        assert len(jobs) > 0, (
            "JSearch returned 0 jobs — transient outage or shape drift. "
            "Investigate before merging changes to jsearch.py."
        )
        for j in jobs:
            assert j.source == "JSearch"
            assert j.title and j.company

    @pytest.mark.live
    def test_live_usajobs(self):
        from jobsearch.config import load_config
        cfg = load_config()
        if not usajobs.is_configured(cfg):
            pytest.skip(_live_skip_message(
                "USAJOBS_API_KEY + USAJOBS_USER_AGENT", "USAJobs"))
        try:
            jobs = usajobs.fetch("python", location="Washington, DC",
                                 num_results=5, cfg=cfg)
        except RuntimeError as exc:
            pytest.skip(f"USAJobs live API unreachable: {exc}")
        assert isinstance(jobs, list)
        assert len(jobs) > 0, (
            "USAJobs returned 0 jobs — transient outage or shape drift. "
            "Investigate before merging changes to usajobs.py."
        )
        for j in jobs:
            assert j.source == "USAJobs"
            assert j.title and j.company

    @pytest.mark.live
    def test_live_findwork(self):
        from jobsearch.config import load_config
        cfg = load_config()
        if not findwork.is_configured(cfg):
            pytest.skip(_live_skip_message("FINDWORK_API_KEY", "Findwork"))
        try:
            jobs = findwork.fetch("python", location="Remote",
                                  num_results=5, cfg=cfg)
        except RuntimeError as exc:
            pytest.skip(f"Findwork live API unreachable: {exc}")
        assert isinstance(jobs, list)
        assert len(jobs) > 0, (
            "Findwork returned 0 jobs — transient outage or shape drift. "
            "Investigate before merging changes to findwork.py."
        )
        for j in jobs:
            assert j.source == "Findwork"
            assert j.title and j.company

    @pytest.mark.live
    def test_live_jooble(self):
        from jobsearch.config import load_config
        cfg = load_config()
        if not jooble.is_configured(cfg):
            pytest.skip(_live_skip_message("JOOBLE_API_KEY", "Jooble"))
        try:
            jobs = jooble.fetch("python", location="Remote",
                                num_results=5, cfg=cfg)
        except RuntimeError as exc:
            pytest.skip(f"Jooble live API unreachable: {exc}")
        assert isinstance(jobs, list)
        assert len(jobs) > 0, (
            "Jooble returned 0 jobs — transient outage or shape drift. "
            "Investigate before merging changes to jooble.py."
        )
        for j in jobs:
            assert j.source == "Jooble"
            assert j.title and j.company

    @pytest.mark.live
    def test_live_careerjet(self):
        from jobsearch.config import load_config
        cfg = load_config()
        if not careerjet.is_configured(cfg):
            pytest.skip(_live_skip_message("CAREERJET_API_KEY", "Careerjet"))
        try:
            jobs = careerjet.fetch("python", location="Austin, TX",
                                   num_results=5, cfg=cfg)
        except RuntimeError as exc:
            pytest.skip(f"Careerjet live API unreachable: {exc}")
        assert isinstance(jobs, list)
        assert len(jobs) > 0, (
            "Careerjet returned 0 jobs — transient outage or shape drift. "
            "Investigate before merging changes to careerjet.py."
        )
        for j in jobs:
            assert j.source == "Careerjet"
            assert j.title and j.company
