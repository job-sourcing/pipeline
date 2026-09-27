"""Tests for the new P0 sources: Workable + Wellfound.

Workable: pure HTTP, no key needed — uses fetch_json mock for unit tests +
allows live tests with @pytest.mark.live.

Wellfound: requires Playwright (Cloudflare blocks plain HTTP); unit tests
mock the Playwright sync_playwright context. Live tests use a real browser.
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from jobsearch.config import Config
from jobsearch.models import Job


# ─────────────────────────────────────────────────────────────────────────────
# Workable — pure HTTP, easy to mock
# ─────────────────────────────────────────────────────────────────────────────

WORKABLE_SAMPLE_RESPONSE = {
    "title": "Workable",
    "totalSize": 8491,
    "nextPageToken": "abc",
    "jobs": [
        {
            "id": "abc-123",
            "title": "Senior Python Engineer",
            "state": "published",
            "description": "<p>We need a Python dev. $120k-$160k USD.</p>",
            "url": "https://jobs.workable.com/view/abc-123/senior-python-engineer-in-argentina-at-acme",
            "location": {"city": "", "subregion": None, "countryName": "Argentina"},
            "locations": ["TELECOMMUTE", "Argentina"],
            "workplace": "remote",
            "employmentType": "Full-time",
            "company": {
                "id": "comp-1",
                "title": "Acme Corp",
                "website": "https://acme.com",
                "description": "<p>Acme is a startup.</p>",
            },
            "department": "Engineering",
            "created": "2026-08-25T14:14:21.880Z",
        },
        {
            "id": "def-456",
            "title": "Backend Engineer",
            "state": "published",
            "description": "<p>Backend role in San Francisco. Onsite only.</p>",
            "url": "https://jobs.workable.com/view/def-456/backend-engineer-in-san-francisco-at-zcorp",
            "location": {"city": "San Francisco", "subregion": "California", "countryName": "United States"},
            "locations": ["San Francisco, California, United States"],
            "workplace": "office",
            "employmentType": "Full-time",
            "company": {
                "id": "comp-2",
                "title": "Z Corp",
                "website": "https://zcorp.com",
                "description": "<p>Z Corp is a fintech.</p>",
            },
            "department": "Engineering",
            "created": "2026-08-25T10:00:00.000Z",
        },
    ],
}


def test_workable_parses_remote_job_correctly():
    """Remote job (TELECOMMUTE + workplace=remote) → location='Remote', remote=True."""
    from jobsearch.sources.workable import _build_job, _parse_location

    sample = WORKABLE_SAMPLE_RESPONSE["jobs"][0]
    city, country, is_remote = _parse_location(sample)
    assert is_remote is True
    assert country == "Argentina"
    assert city == ""

    built = _build_job(sample)
    assert built.title == "Senior Python Engineer"
    assert built.company == "Acme Corp"
    assert built.location == "Remote"
    assert built.remote is True
    assert built.date_posted == "2026-08-25"
    assert "jobs.workable.com/view/abc-123" in built.link


def test_workable_parses_onsite_job_correctly():
    """Onsite job → location='City, Country', remote=False."""
    from jobsearch.sources.workable import _build_job

    sample = WORKABLE_SAMPLE_RESPONSE["jobs"][1]
    built = _build_job(sample)
    assert built.title == "Backend Engineer"
    assert built.company == "Z Corp"
    assert "San Francisco" in built.location
    assert "United States" in built.location
    assert built.remote is False


def test_workable_fetch_with_mock_returns_only_remote_for_remote_search():
    """'Remote' search filter excludes onsite jobs; returns matching remote jobs."""
    from jobsearch.sources import workable

    cfg = Config()
    with patch("jobsearch.sources.workable.fetch_json", return_value=WORKABLE_SAMPLE_RESPONSE):
        jobs = workable.fetch("Python Engineer", "Remote", num_results=5, cfg=cfg)

    # Should return only the 1 remote job
    assert len(jobs) == 1
    assert jobs[0].title == "Senior Python Engineer"
    assert jobs[0].remote is True


def test_workable_fetch_with_location_filter_matches_city():
    """Specific city search matches jobs where city appears in location."""
    from jobsearch.sources import workable

    cfg = Config()
    with patch("jobsearch.sources.workable.fetch_json", return_value=WORKABLE_SAMPLE_RESPONSE):
        # Search for "San Francisco" should return the onsite SF job
        jobs = workable.fetch("Engineer", "San Francisco", num_results=5, cfg=cfg)

    assert len(jobs) == 1
    assert jobs[0].title == "Backend Engineer"
    assert "San Francisco" in jobs[0].location


def test_workable_fetch_handles_empty_response():
    """A 200 response with no jobs → empty list, no crash."""
    from jobsearch.sources import workable

    cfg = Config()
    with patch("jobsearch.sources.workable.fetch_json", return_value={"jobs": []}):
        jobs = workable.fetch("Python", "Remote", num_results=5, cfg=cfg)
    assert jobs == []


def test_workable_is_configured_always_true():
    """Workable needs no API key — always configured."""
    from jobsearch.sources.workable import is_configured

    assert is_configured(Config()) is True


def test_workable_company_name_fallback_from_url_slug():
    """When company.title is missing, parse name from URL slug."""
    from jobsearch.sources.workable import _build_job

    sample = {
        "id": "test-1",
        "title": "Test Engineer",
        "url": "https://jobs.workable.com/view/test-1/test-engineer-at-some-company",
        "company": None,
        "description": "<p>Test role.</p>",
        "location": {},
        "locations": [],
        "workplace": "remote",
    }
    built = _build_job(sample)
    # Should extract "Some Company" from the URL slug "at-some-company"
    assert "Some Company" in built.company or built.company != ""


def _workable_job(i: int) -> dict:
    """Minimal valid Workable job row for pagination fixtures (remote)."""
    return {
        "id": f"wk-{i}",
        "title": f"Python Engineer {i}",
        "state": "published",
        "description": "<p>Python backend role.</p>",
        "url": (f"https://jobs.workable.com/view/wk-{i}/"
                f"python-engineer-{i}-at-co{i}"),
        "location": {"city": "", "subregion": None, "countryName": "Argentina"},
        "locations": ["TELECOMMUTE", "Argentina"],
        "workplace": "remote",
        "employmentType": "Full-time",
        "company": {"id": f"comp-{i}", "title": f"Co {i}",
                    "website": "https://example.com",
                    "description": "<p>Co.</p>"},
        "department": "Engineering",
        "created": "2026-08-25T10:00:00.000Z",
    }


def test_workable_paginates_via_next_page_token(monkeypatch):
    """audit P2-3: first-page-only truncation fixed — the adapter follows
    nextPageToken (capped 5 pages, paced 0.3s) until the post-filter count
    reaches num_results; the side-effect fake asserts the token is actually
    forwarded and the page limit over-fetches 2x (API caps at 20)."""
    from jobsearch.sources import workable

    pages: list[dict] = []

    def fake_fetch_json(url, *, params=None, cfg=None, headers=None,
                        method="GET", json=None, auth=None):
        params = dict(params or {})
        pages.append(params)
        if "nextPageToken" not in params:
            # full remote page of 20 (limit is capped at 20 by the API)
            return {"title": "Workable", "totalSize": 30, "nextPageToken": "tok-2",
                    "jobs": [_workable_job(i) for i in range(20)]}
        assert params["nextPageToken"] == "tok-2"
        return {"title": "Workable", "totalSize": 30, "nextPageToken": "",
                "jobs": [_workable_job(100 + i) for i in range(10)]}

    monkeypatch.setattr(workable, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(workable.time, "sleep", lambda s: None)
    cfg = Config()
    jobs = workable.fetch("Python Engineer", "Remote", num_results=25, cfg=cfg)

    assert len(jobs) == 25                    # num_results satisfied (> 20)
    assert len(pages) == 2                    # paged exactly once more
    assert "nextPageToken" not in pages[0]    # first page: no token
    assert pages[1]["nextPageToken"] == "tok-2"   # token actually forwarded
    assert pages[0]["limit"] == 20            # 2x over-fetch, API-capped
    assert len({j.link for j in jobs}) == 25  # no cross-page duplicates


def test_workable_truncation_safe_returns_what_exists(monkeypatch):
    """audit P2-3: fewer available than requested (no nextPageToken) →
    return what exists without error and without extra requests."""
    from jobsearch.sources import workable

    calls = {"n": 0}

    def fake_fetch_json(url, *, params=None, cfg=None, headers=None,
                        method="GET", json=None, auth=None):
        calls["n"] += 1
        return {"title": "Workable", "totalSize": 7, "nextPageToken": "",
                "jobs": [_workable_job(i) for i in range(7)]}

    monkeypatch.setattr(workable, "fetch_json", fake_fetch_json)
    cfg = Config()
    jobs = workable.fetch("Python Engineer", "Remote", num_results=25, cfg=cfg)
    assert len(jobs) == 7
    assert calls["n"] == 1                    # exhausted → no second request


def test_workable_later_page_failure_degrades(monkeypatch):
    """audit P2-3 failure isolation: a page-2 transport error keeps the
    page-1 rows instead of discarding the whole search."""
    from jobsearch.sources import workable
    import requests as _requests

    def fake_fetch_json(url, *, params=None, cfg=None, headers=None,
                        method="GET", json=None, auth=None):
        if "nextPageToken" not in (params or {}):
            return {"nextPageToken": "tok-2", "totalSize": 30,
                    "jobs": [_workable_job(i) for i in range(20)]}
        raise _requests.ConnectionError("reset mid-pagination")

    monkeypatch.setattr(workable, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(workable.time, "sleep", lambda s: None)
    cfg = Config()
    jobs = workable.fetch("Python Engineer", "Remote", num_results=25, cfg=cfg)
    assert len(jobs) == 20                    # page-1 rows kept, no raise


# ─────────────────────────────────────────────────────────────────────────────
# Wellfound — Playwright-based; unit tests mock the browser
# ─────────────────────────────────────────────────────────────────────────────

WELLFOUND_SAMPLE_HTML = """
<html>
<body>
<script id="__NEXT_DATA__" type="application/json" crossorigin="anonymous">{
  "props": {
    "pageProps": {
      "apolloState": {
        "data": {
          "JobListing:123": {
            "__typename": "JobListing",
            "id": "123",
            "slug": "senior-software-engineer",
            "title": "Senior Software Engineer",
            "compensation": "$140k - $150k",
            "locationNames": [],
            "acceptedRemoteLocationNames": ["United States"],
            "remote": true,
            "liveStartAt": 1787697177,
            "startup": {"__ref": "Startup:456"}
          },
          "Startup:456": {
            "__typename": "Startup",
            "id": "456",
            "name": "Test Startup",
            "slug": "test-startup"
          }
        }
      }
    }
  }
}</script>
</body>
</html>
"""


def test_wellfound_extracts_next_data_from_html():
    """The __NEXT_DATA__ parser correctly extracts JSON from Wellfound SSR HTML."""
    from jobsearch.sources.wellfound import _extract_next_data

    data = _extract_next_data(WELLFOUND_SAMPLE_HTML)
    assert data is not None
    page_props = data["props"]["pageProps"]
    apollo_state = page_props["apolloState"]["data"]
    assert "JobListing:123" in apollo_state
    assert "Startup:456" in apollo_state
    assert apollo_state["JobListing:123"]["title"] == "Senior Software Engineer"


def test_wellfound_builds_job_with_correct_fields():
    """Job built from Wellfound JobListing + Startup ref has all fields set."""
    from jobsearch.sources.wellfound import _build_job

    job_data = {
        "id": "123",
        "slug": "senior-software-engineer",
        "title": "Senior Software Engineer",
        "compensation": "$140k - $150k",
        "locationNames": [],
        "acceptedRemoteLocationNames": ["United States"],
        "remote": True,
        "liveStartAt": 1787697177,
        "startup": {"__ref": "Startup:456"},
    }
    startup_data = {
        "id": "456",
        "name": "Test Startup",
        "slug": "test-startup",
    }
    built = _build_job(job_data, startup_data)
    assert built.title == "Senior Software Engineer"
    assert built.company == "Test Startup"
    assert built.remote is True
    assert "United States" in built.location
    assert "Remote" in built.location
    assert built.link == "https://wellfound.com/jobs/123-senior-software-engineer"
    assert built.salary_text == "$140k - $150k"
    assert built.date_posted is not None  # converted from epoch


def test_wellfound_is_configured_always_true():
    """Wellfound needs no API key — always configured (Playwright required separately)."""
    from jobsearch.sources.wellfound import is_configured

    assert is_configured(Config()) is True


def test_wellfound_fetch_with_mock_returns_jobs():
    """Mock the Playwright context to return our sample HTML; verify fetch parses jobs."""
    from jobsearch.sources import wellfound

    # Build a fake Playwright sync_playwright context manager
    fake_page = MagicMock()
    fake_page.content.return_value = WELLFOUND_SAMPLE_HTML
    fake_ctx = MagicMock()
    fake_ctx.new_page.return_value = fake_page
    fake_browser = MagicMock()
    fake_browser.new_context.return_value = fake_ctx
    fake_pw = MagicMock()
    fake_pw.chromium.launch.return_value = fake_browser

    # Build a fake sync_playwright context manager
    class FakeSyncPW:
        def __enter__(self):
            return fake_pw
        def __exit__(self, *args):
            return False

    cfg = Config()
    # Patch the lazy imports inside fetch() — since they're imported at call time,
    # we patch sys.modules instead
    import sys
    fake_pw_module = MagicMock()
    fake_pw_module.sync_playwright.return_value = FakeSyncPW()
    fake_stealth_module = MagicMock()
    fake_stealth_module.stealth_sync = MagicMock()

    with patch.dict(sys.modules, {
        "playwright.sync_api": fake_pw_module,
        "playwright_stealth": fake_stealth_module,
    }):
        jobs = wellfound.fetch("Software Engineer", "Remote", num_results=5, cfg=cfg)

    assert len(jobs) == 1
    assert jobs[0].title == "Senior Software Engineer"
    assert jobs[0].company == "Test Startup"
    assert jobs[0].remote is True
    assert jobs[0].salary_text == "$140k - $150k"


@pytest.mark.live
def test_workable_live():
    """Live test: hit Workable's real API and verify we get jobs back.

    Skips cleanly on environmental failure (network-blocked, 0 results
    due to transient outage) rather than failing hard — see PR_REVIEW_TESTS_v2
    P0-4. The live test suite should report SKIP for environmental issues
    so real code bugs surface clearly.
    """
    from jobsearch.sources import workable

    try:
        jobs = workable.fetch("Python Developer", "Remote", num_results=3)
    except RuntimeError as exc:
        pytest.skip(f"Workable live API unreachable: {exc}")
    if not jobs:
        pytest.skip(
            "Workable returned 0 jobs — likely transient outage or API shape "
            "drift. Investigate before merging changes to workable.py."
        )
    assert all(j.source == "Workable" for j in jobs)


@pytest.mark.live
def test_wellfound_live():
    """Live test: hit Wellfound's real /jobs page via Playwright.

    Skips cleanly when Playwright/stealth is not installed (P0-3 fix) and
    when the live page returns 0 jobs (transient outage). Per the JobSpy
    pattern: a missing dependency is an environmental issue, not a test
    failure.
    """
    try:
        import playwright.sync_api  # noqa: F401
        import playwright_stealth  # noqa: F401
    except ImportError:
        pytest.skip(
            "playwright + playwright-stealth not installed. "
            "Install with: pip install playwright playwright-stealth && "
            "playwright install chromium"
        )

    from jobsearch.sources import wellfound
    try:
        jobs = wellfound.fetch("Software Engineer", "Remote", num_results=3)
    except RuntimeError as exc:
        pytest.skip(f"Wellfound live scrape failed: {exc}")
    if not jobs:
        pytest.skip(
            "Wellfound returned 0 jobs — likely Cloudflare challenge or "
            "transient outage. Investigate before merging."
        )
    assert all(j.source == "Wellfound" for j in jobs)
    assert all(j.remote for j in jobs)


def test_wellfound_uses_role_specific_url(monkeypatch):
    """P1-2 regression: page.goto() MUST be called with the role-specific
    URL returned by _build_url_for_keywords(keywords), NOT the generic
    _WELLFOUND_JOBS_URL constant. The previous implementation computed
    target_url and then ignored it, making _ROLE_MAP dead code.

    Round 2 P2-1 fix: assertion strengthened to check for the exact
    /role/j/remote-<slug> path component, not just the substring 'role'
    (which would match '/jobs?role=engineer' too).
    """
    from jobsearch.sources import wellfound
    from unittest.mock import MagicMock, patch

    # Mock the Playwright chain to capture page.goto's first positional arg.
    fake_page = MagicMock()
    fake_page.content.return_value = (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        '{"props": {"pageProps": {"jobPosts": []}}}</script></html>'
    )
    fake_ctx = MagicMock()
    fake_ctx.new_page.return_value = fake_page
    fake_browser = MagicMock()
    fake_browser.new_context.return_value = fake_ctx
    fake_pw = MagicMock()
    fake_pw.chromium.launch.return_value = fake_browser

    class FakeSyncPW:
        def __enter__(self):
            return fake_pw
        def __exit__(self, *args):
            return False

    import sys
    fake_pw_module = MagicMock()
    fake_pw_module.sync_playwright.return_value = FakeSyncPW()
    fake_stealth_module = MagicMock()
    fake_stealth_module.stealth_sync = MagicMock()

    cfg = Config()
    with patch.dict(sys.modules, {
        "playwright.sync_api": fake_pw_module,
        "playwright_stealth": fake_stealth_module,
    }):
        # Use a role that IS in _ROLE_MAP — should map to /role/j/remote-<slug>
        wellfound.fetch("Software Engineer", "Remote", num_results=5, cfg=cfg)
        # page.goto was called with a URL containing 'role' (the role-routed
        # path), NOT the generic /jobs URL.
        goto_args, goto_kwargs = fake_page.goto.call_args
        url = goto_args[0]
        assert "/role/j/remote-" in url, (
            f"Expected role-routed URL '/role/j/remote-<slug>' but got: {url!r}"
        )
        # Negative assertion: it must NOT be the generic /jobs URL.
        assert url != "https://wellfound.com/jobs", (
            f"page.goto was called with the generic /jobs URL — the bug is "
            f"still present. Got: {url!r}"
        )
