"""Board clients against replayed fixtures — no network.

Monkeypatch gotcha: each client module does `from .base import fetch_json`
at import time, so we patch e.g. jobsearch.sources.remotive.fetch_json,
NOT jobsearch.sources.base.fetch_json.
"""
from __future__ import annotations

import json
import re

import pytest

from jobsearch.config import Config
from jobsearch.sources import arbeitnow, jobicy, linkedin_guest, remotive, remoteok, themuse
from jobsearch.sources.base import clean_html, detect_h1b, extract_email

from conftest import SOURCE_FIXTURES


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(db_path=tmp_path / "t.db")


def load_fixture(name: str):
    return json.loads((SOURCE_FIXTURES / name).read_text(encoding="utf-8"))


# ── base helpers ──────────────────────────────────────────────────────────

class TestBaseHelpers:
    def test_detect_h1b_positive(self):
        assert detect_h1b("We offer H1B sponsorship") is True
        assert detect_h1b("visa sponsor available for this role") is True
        assert detect_h1b("Sponsorship available") is True
        assert detect_h1b("will sponsor work authorization") is True

    def test_detect_h1b_negative(self):
        assert detect_h1b("requires 5 years of experience") is False
        assert detect_h1b("competitive salary and benefits") is False

    def test_extract_email(self):
        assert extract_email("apply at jobs@acme.io today") == "jobs@acme.io"
        assert extract_email("hr+recruiting@corp.de") == "hr+recruiting@corp.de"

    def test_extract_email_filters_noise(self):
        assert extract_email("noreply@boards.com") is None
        assert extract_email("no-reply@boards.com") is None
        assert extract_email("someone@example.com") is None
        assert extract_email("test@company.io") is None
        assert extract_email("donotreply@mailer.xyz") is None
        assert extract_email("notifications@svc.com") is None

    def test_extract_email_prefers_non_noise(self):
        text = "questions to noreply@board.com or jobs@acme.io"
        assert extract_email(text) == "jobs@acme.io"

    def test_extract_email_no_email(self):
        assert extract_email("no contact info here") is None

    def test_clean_html(self):
        assert clean_html("<p>Hello <b>world</b></p>") == "Hello world"
        assert clean_html("plain") == "plain"


# ── Remotive ──────────────────────────────────────────────────────────────

class TestRemotive:
    def test_fetch_parses_fixture(self, monkeypatch, cfg):
        fx = load_fixture("remotive.json")
        seen = {}

        def fake_fetch_json(url, *, params=None, cfg=None, headers=None):
            seen["url"], seen["params"] = url, params
            return fx

        monkeypatch.setattr(remotive, "fetch_json", fake_fetch_json)
        jobs = remotive.fetch("python", cfg=cfg)

        # Remotive filters server-side via the `search` param (exp 01 v2):
        # the fixture replays the API response verbatim, so all 3 rows pass.
        assert len(jobs) == 3
        assert seen["url"] == "https://remotive.com/api/remote-jobs"
        assert seen["params"]["search"] == "python"

        j = jobs[0]
        assert j.title == "Python Backend Engineer"
        assert j.company == "Acme Cloud"
        # description arrives as raw HTML (mirrors the real API, live DB rows
        # 1-10) — clean_html strips tags with no whitespace collapse
        assert j.description == ("We need a Python backend engineer with "
                                 "FastAPI and PostgreSQL experience. Kubernetes "
                                 "a plus. Visa sponsorship available for "
                                 "exceptional candidates. Apply at "
                                 "jobs@acme.cloud")
        assert "<" not in j.description
        assert j.link == ("https://remotive.com/remote-jobs/software-dev/"
                          "python-backend-engineer-1")
        assert j.source == "Remotive"
        assert j.remote is True            # remote-only board
        assert j.location == "Remote"
        assert j.h1b_mention is True       # "Visa sponsorship available"
        assert j.contact_email == "jobs@acme.cloud"
        assert j.salary_text == "40k-70k USD/year"
        assert j.date_posted == "2026-08-20"

    def test_fetch_respects_num_results(self, monkeypatch, cfg):
        fx = load_fixture("remotive.json")
        monkeypatch.setattr(remotive, "fetch_json",
                            lambda *a, **k: fx)
        jobs = remotive.fetch("python", num_results=2, cfg=cfg)
        assert len(jobs) == 2

    def test_fixture_relevance_split(self):
        # fixture sanity: 2 of 3 jobs are python-relevant, 1 is a designer
        fx = load_fixture("remotive.json")
        titles = [j["title"].lower() for j in fx["jobs"]]
        assert sum("python" in t for t in titles) == 2
        assert any("designer" in t for t in titles)


# ── Arbeitnow ─────────────────────────────────────────────────────────────

class TestArbeitnow:
    def test_keyword_match_rules(self):
        assert arbeitnow.keyword_match("Senior Python Developer", "python") is True
        assert arbeitnow.keyword_match("Frontend React TypeScript", "python") is False
        # tokens shorter than 3 chars are ignored entirely
        assert arbeitnow.keyword_match("Golang developer", "go") is False
        assert arbeitnow.keyword_match("Rust engineer", "go rust") is True

    def test_fetch_filters_non_matching(self, monkeypatch, cfg):
        fx = load_fixture("arbeitnow.json")
        monkeypatch.setattr(arbeitnow, "fetch_json", lambda *a, **k: fx)
        jobs = arbeitnow.fetch("python", cfg=cfg)
        # fixture: 1 python+docker+h1b job, 1 react job -> only the first survives
        assert len(jobs) == 1
        j = jobs[0]
        assert j.title == "Python Developer (f/m/d)"
        assert j.company == "Acme Cloud"
        # real API descriptions are HTML — tags stripped, text preserved
        # (clean_html does NOT collapse whitespace: "work.</p><p>H1B" -> "work.H1B")
        assert j.description == ("Backend developer with Python, Docker and "
                                 "PostgreSQL. We offer remote work.H1B "
                                 "sponsorship available.")
        assert "<" not in j.description
        assert j.source == "Arbeitnow"
        assert j.link == "https://www.arbeitnow.com/jobs/python-developer-acme"
        assert j.remote is True
        assert j.h1b_mention is True       # "H1B sponsorship available"
        assert j.location == "Berlin"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", j.date_posted)

    def test_fetch_no_match_returns_empty(self, monkeypatch, cfg):
        fx = load_fixture("arbeitnow.json")
        monkeypatch.setattr(arbeitnow, "fetch_json", lambda *a, **k: fx)
        assert arbeitnow.fetch("cobol", cfg=cfg) == []


# ── The Muse ──────────────────────────────────────────────────────────────

class TestTheMuse:
    def test_fetch_filters_and_parses(self, monkeypatch, cfg):
        fx = load_fixture("themuse.json")

        def fake_fetch_json(url, *, params=None, cfg=None, headers=None):
            # page 1+ of the real API returns further/different results;
            # replaying the same fixture would duplicate jobs.
            if (params or {}).get("page", 0) == 0:
                return fx
            return {"results": []}

        monkeypatch.setattr(themuse, "fetch_json", fake_fetch_json)
        jobs = themuse.fetch("python", cfg=cfg)
        assert len(jobs) == 1   # backend matches, marketing does not
        j = jobs[0]
        assert j.title == "Backend Engineer, Python"
        assert j.company == "Acme Cloud"
        assert j.source == "The Muse"
        assert j.link == "https://www.themuse.com/jobs/acme/backend-engineer-python"
        assert j.location == "Remote - US"
        assert j.remote is True
        assert j.date_posted == "2026-08-18"

    def test_fetch_paginates_until_num_results(self, monkeypatch, cfg):
        pages = [load_fixture("themuse.json"), {"results": []}]
        calls = []

        def fake_fetch_json(url, *, params=None, cfg=None, headers=None):
            calls.append((params or {}).get("page", 0))
            return pages[min((params or {}).get("page", 0), len(pages) - 1)]

        monkeypatch.setattr(themuse, "fetch_json", fake_fetch_json)
        jobs = themuse.fetch("python", num_results=1, cfg=cfg)
        assert len(jobs) == 1
        assert calls == [0]    # stops as soon as num_results is reached


# ── RemoteOK ──────────────────────────────────────────────────────────────

class TestRemoteOK:
    def test_fetch_skips_metadata_and_filters(self, monkeypatch, cfg):
        fx = load_fixture("remoteok.json")
        monkeypatch.setattr(remoteok, "fetch_json", lambda *a, **k: fx)
        jobs = remoteok.fetch("python", cfg=cfg)
        # fixture: [0] is the legal/metadata object, then a python job and
        # a devops job -> only the python job survives the keyword filter.
        assert len(jobs) == 1
        j = jobs[0]
        assert j.title == "Python Engineer"
        # live evidence (CR-1-TESTS): RemoteOK sent company_name
        # "JACK &amp; JONES" — _clean must decode HTML entities (case kept)
        assert j.company == "JACK & JONES"
        assert j.source == "RemoteOK"
        assert j.link == "https://remoteok.com/remote-jobs/python-engineer-acme"
        # _clean strips tags with a space replacement (no whitespace
        # collapse) — mid-text tags leave double spaces. Documented behavior.
        assert j.description == ("Build  Python  services with PostgreSQL "
                                 "and Redis. Remote forever.")
        assert "<" not in j.description and ">" not in j.description
        assert j.salary_text == "90k-130k USD"
        assert j.remote is True
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", j.date_posted)


# ── Jobicy ────────────────────────────────────────────────────────────────

class TestJobicy:
    def test_fetch_filters_and_parses(self, monkeypatch, cfg):
        fx = load_fixture("jobicy.json")
        monkeypatch.setattr(jobicy, "fetch_json", lambda *a, **k: fx)
        jobs = jobicy.fetch("python", cfg=cfg)
        assert len(jobs) == 1   # content writer excluded
        j = jobs[0]
        assert j.title == "Python Backend Engineer"
        assert j.company == "Acme Cloud"
        assert j.source == "Jobicy"
        assert j.link == "https://jobicy.com/jobs/python-backend-engineer-remote"
        assert j.description == ("Remote Python backend role: FastAPI, "
                                 "PostgreSQL, AWS. Sponsorship considered.")
        assert j.salary_text == "120k-160k USD/year"
        assert j.remote is True
        assert j.location == "Remote"
        assert j.date_posted == "2026-08-20"


# ── LinkedIn Guest ────────────────────────────────────────────────────────

DETAIL_OPEN = """
<div class="show-more-less-html__markup">
  <p>We need a Senior Python Developer with FastAPI, PostgreSQL and AWS
  experience. This role is Remote. H1B visa sponsorship is available for
  strong candidates. Apply to jobs@acme.cloud</p>
</div>
"""

DETAIL_CLOSED = """
<div class="job-closed-message">This job is no longer accepting applications.</div>
"""


@pytest.fixture
def li_search_html() -> str:
    return (SOURCE_FIXTURES / "linkedin_search.html").read_text(encoding="utf-8")


def patch_linkedin(monkeypatch, li_search_html):
    def fake_fetch_text(url, *, params=None, cfg=None, headers=None):
        if "seeMoreJobPostings/search" in url:
            return li_search_html
        if "3900000001" in url:
            return DETAIL_OPEN
        if "3900000002" in url:
            return DETAIL_CLOSED
        raise AssertionError(f"unexpected fetch_text url: {url}")

    monkeypatch.setattr(linkedin_guest, "fetch_text", fake_fetch_text)


def _li_page_html(start_id: int, n: int) -> str:
    """Synthesize a guest search-results page with n cards (audit P2-3
    pagination fixtures — one page = up to 25 cards)."""
    cards = []
    for i in range(n):
        jid = start_id + i
        cards.append(
            f'''  <li>
    <div data-entity-urn="urn:li:jobPosting:{jid}">
      <div class="base-card">
        <h3 class="base-search-card__title">Python Developer {jid}</h3>
        <h4 class="base-search-card__subtitle">
          <a class="hidden-nested-link">Company {jid}</a>
        </h4>
        <span class="job-search-card__location">Remote, United States</span>
        <time class="job-search-card__listdate" datetime="2026-08-20">2 days ago</time>
      </div>
    </div>
  </li>''')
    return ('<ul class="jobs-search__results-list">\n' + "\n".join(cards)
            + "\n</ul>")


class TestLinkedInGuest:
    def test_parse_search_results(self, li_search_html):
        parsed = linkedin_guest._parse_search_results(li_search_html)
        assert len(parsed) == 2
        first = parsed[0]
        assert first["id"] == "3900000001"
        assert first["title"] == "Senior Python Developer"
        assert first["company"] == "Acme Cloud"
        assert first["location"] == "Remote, United States"
        assert first["url"] == "https://www.linkedin.com/jobs/view/3900000001"
        assert parsed[1]["company"] == "BetaData"

    def test_job_key_normalizes_reposts(self):
        assert linkedin_guest.job_key("Senior Python Developer", "Acme Cloud") == \
            linkedin_guest.job_key("Senior Python Developer - Remote", "Acme Cloud")
        assert linkedin_guest.job_key("Data Engineer (Contract)", "Acme") == \
            linkedin_guest.job_key("Data Engineer", "Acme")
        assert linkedin_guest.job_key("Data Engineer", "Acme") != \
            linkedin_guest.job_key("Data Engineer", "BetaData")

    def test_fetch_skips_closed_postings(self, monkeypatch, cfg, li_search_html):
        patch_linkedin(monkeypatch, li_search_html)
        jobs = linkedin_guest.fetch("python developer", location="United States",
                                    cfg=cfg)
        # card 2 says "no longer accepting applications" -> skipped
        assert len(jobs) == 1
        j = jobs[0]
        assert j.title == "Senior Python Developer"
        assert j.company == "Acme Cloud"
        assert j.source == "LinkedIn Guest"
        assert j.link == "https://www.linkedin.com/jobs/view/3900000001"
        assert j.remote is True                 # work_mode == "Remote"
        assert j.h1b_mention is True            # from the detail description
        assert j.contact_email == "jobs@acme.cloud"
        assert "FastAPI" in j.description
        assert j.date_posted == "2026-08-20"

    def test_fetch_without_details_keeps_all_cards(self, monkeypatch, cfg,
                                                   li_search_html):
        patch_linkedin(monkeypatch, li_search_html)
        jobs = linkedin_guest.fetch("python developer", fetch_details=False,
                                    cfg=cfg)
        assert len(jobs) == 2   # closed-check only happens on detail fetch
        assert jobs[0].description == ""   # no detail -> no description

    def test_fetch_detail(self, monkeypatch, cfg):
        monkeypatch.setattr(linkedin_guest, "fetch_text",
                            lambda url, **k: DETAIL_OPEN)
        detail = linkedin_guest.fetch_detail("3900000001", cfg=cfg)
        assert detail["work_mode"] == "Remote"
        assert detail["closed"] is False
        assert "FastAPI" in detail["description"]

        monkeypatch.setattr(linkedin_guest, "fetch_text",
                            lambda url, **k: DETAIL_CLOSED)
        detail = linkedin_guest.fetch_detail("3900000002", cfg=cfg)
        assert detail["closed"] is True

    def test_fetch_pages_beyond_25_cards(self, monkeypatch, cfg):
        """audit P2-3: num_results > 25 no longer silently truncated — the
        pager advances `start` by 25 per page (capped 4 pages / 100 cards);
        the side-effect fake asserts the start param actually advances."""
        starts: list[int] = []

        def fake_fetch_text(url, *, params=None, cfg=None, headers=None):
            if "seeMoreJobPostings/search" not in url:
                raise AssertionError(f"unexpected fetch_text url: {url}")
            start = int(re.search(r"start=(\d+)", url).group(1))
            starts.append(start)
            if start == 0:
                return _li_page_html(3910000000, 25)   # full first page
            return _li_page_html(3920000000, 10)

        monkeypatch.setattr(linkedin_guest, "fetch_text", fake_fetch_text)
        monkeypatch.setattr(linkedin_guest.time, "sleep", lambda s: None)
        jobs = linkedin_guest.fetch("python developer", num_results=30,
                                    fetch_details=False, cfg=cfg)

        assert len(jobs) == 30                # num_results satisfied (> 20)
        assert starts == [0, 25]              # fixed-25-step when pages are 25-wide
        assert len({j.link for j in jobs}) == 30   # all cards distinct


    # ── corroboration-signal extraction (2026-09-09, board-v2 design D2) ──

    DETAIL_WITH_SIGNALS = """
<div class="topcard__flavor-row">
  <span class="posted-time-ago__text topcard__flavor--metadata">
    2 days ago
  </span>
  <span class="num-applicants__caption topcard__flavor--metadata topcard__flavor--bullet">
    122 applicants
  </span>
</div>
<div class="show-more-less-html__markup">
  <p>Job Requisition ID JR2023808<br><br>Job Category Sales<br><br>
  Time Type Full time<br><br>NVIDIA has been transforming computer
  graphics.</p>
</div>
"""

    def test_parse_num_applicants_variants(self):
        from jobsearch.sources.linkedin_guest import _parse_num_applicants
        assert _parse_num_applicants("122 applicants") == (122, "122 applicants")
        assert _parse_num_applicants("1 applicant") == (1, "1 applicant")
        assert _parse_num_applicants("Over 200 applicants") == (200, "Over 200 applicants")
        assert _parse_num_applicants("Be the first to apply") == (None, "")
        assert _parse_num_applicants("Be among the first 10 applicants") == (10, "among first 10")
        assert _parse_num_applicants("") == (None, "")
        assert _parse_num_applicants("1,234 applicants") == (1234, "1,234 applicants")

    def test_parse_req_id(self):
        from jobsearch.sources.linkedin_guest import _parse_req_id
        assert _parse_req_id("Job Requisition ID JR2023808 blah") == "JR2023808"
        assert _parse_req_id("job requisition id: R12345 other") == "R12345"
        assert _parse_req_id("no req here") == ""

    def test_parse_req_id_bare_jr_second_pass(self):
        """S8-E1 (research §f R3a): only 8.6% of indexed NVIDIA cards
        embed the anchored label — descriptions carrying a bare JR#######
        token (7-8 digits, NVIDIA's req shape) must yield it too."""
        from jobsearch.sources.linkedin_guest import _parse_req_id
        assert _parse_req_id(
            "NVIDIA is hiring. Apply with requisition JR2023999 today.") \
            == "JR2023999"
        assert _parse_req_id("see code JR20244123 in the ad") == "JR20244123"
        # false positives must NOT match: 6-digit, 9-digit, glued
        assert _parse_req_id("code JR123456 is only six digits") == ""
        assert _parse_req_id("id JR123456789 has nine digits") == ""
        assert _parse_req_id("glued AJR2023999 has no boundary") == ""
        assert _parse_req_id("") == ""

    def test_parse_req_id_anchored_wins_on_conflict(self):
        """S8-E1: when BOTH forms appear, the anchored 'Job Requisition
        ID' match wins over the bare token found elsewhere in the text."""
        from jobsearch.sources.linkedin_guest import _parse_req_id
        text = ("Job Requisition ID R12345 … the footer mentions "
                "JR2023999 as well")
        assert _parse_req_id(text) == "R12345"

    def test_fetch_detail_bare_jr_without_anchor(self, monkeypatch, cfg):
        html = """
<div class="show-more-less-html__markup">
  <p>Join our team. Requisition code JR2024123 applies.<br>Remote role.</p>
</div>
"""
        monkeypatch.setattr(linkedin_guest, "fetch_text",
                            lambda url, **k: html)
        detail = linkedin_guest.fetch_detail("4461860999", cfg=cfg)
        assert detail["job_req_id"] == "JR2024123"

    def test_fetch_detail_extracts_signals(self, monkeypatch, cfg):
        monkeypatch.setattr(linkedin_guest, "fetch_text",
                            lambda url, **k: self.DETAIL_WITH_SIGNALS)
        detail = linkedin_guest.fetch_detail("4461860982", cfg=cfg)
        assert detail["num_applicants"] == 122
        assert detail["applicants_label"] == "122 applicants"
        assert detail["job_req_id"] == "JR2023808"
        assert "2 days ago" in detail["posted_time_ago"]
        assert detail["closed"] is False

    def test_fetch_detail_no_signal_fields(self, monkeypatch, cfg):
        monkeypatch.setattr(linkedin_guest, "fetch_text",
                            lambda url, **k: DETAIL_OPEN)
        detail = linkedin_guest.fetch_detail("3900000001", cfg=cfg)
        assert detail["num_applicants"] is None
        assert detail["applicants_label"] == ""
        assert detail["job_req_id"] == ""

    def test_b2_pagination_steps_by_cards_received(self, monkeypatch, cfg):
        """B2 regression (2026-09-09): guest pages return ~10 cards and
        `start` is a TRUE offset — the pager must advance by cards
        RECEIVED (10), never a fixed 25-step which would skip ~60%."""
        starts: list[int] = []

        def fake_fetch_text(url, *, params=None, cfg=None, headers=None):
            if "seeMoreJobPostings/search" not in url:
                raise AssertionError(f"unexpected url: {url}")
            start = int(re.search(r"start=(\d+)", url).group(1))
            starts.append(start)
            if start == 0:
                return _li_page_html(3910000000, 10)   # 10-card page
            if start == 10:
                return _li_page_html(3910000010, 10)
            return ""

        monkeypatch.setattr(linkedin_guest, "fetch_text", fake_fetch_text)
        monkeypatch.setattr(linkedin_guest.time, "sleep", lambda s: None)
        jobs = linkedin_guest.fetch("python developer", num_results=20,
                                    fetch_details=False, cfg=cfg)
        assert len(jobs) == 20
        assert starts == [0, 10]              # stepped by cards received
        assert len({j.link for j in jobs}) == 20

    def test_fetch_truncation_safe_when_board_exhausted(self, monkeypatch, cfg):
        """audit P2-3: fewer cards available than requested → return what
        exists without error (page 2 comes back empty → stop paging)."""
        starts: list[int] = []

        def fake_fetch_text(url, *, params=None, cfg=None, headers=None):
            if "seeMoreJobPostings/search" not in url:
                raise AssertionError(f"unexpected fetch_text url: {url}")
            start = int(re.search(r"start=(\d+)", url).group(1))
            starts.append(start)
            if start == 0:
                return _li_page_html(3910000000, 3)
            return ""    # guest endpoint returns an empty body when exhausted

        monkeypatch.setattr(linkedin_guest, "fetch_text", fake_fetch_text)
        monkeypatch.setattr(linkedin_guest.time, "sleep", lambda s: None)
        jobs = linkedin_guest.fetch("python developer", num_results=30,
                                    fetch_details=False, cfg=cfg)
        assert len(jobs) == 3
        # B2 (2026-09-09): steps by cards RECEIVED (3) — then one more
        # probe at start=3 comes back empty → stop. Never a blind 25-step.
        assert starts == [0, 3]


class TestLinkedInGuestHTTPErrors:
    """P1-3 fix: LinkedIn Guest search call now translates HTTPError /
    ConnectionError to friendly RuntimeError messages (mirrors the
    free-key tier pattern)."""
    def _make(self, status):
        import requests
        r = requests.Response()
        r.status_code = status
        return requests.HTTPError(f"{status}", response=r)

    def test_http_403_translates_to_blocked(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise self._make(403)
        monkeypatch.setattr(linkedin_guest, "fetch_text", fake)
        with pytest.raises(RuntimeError, match="blocked"):
            linkedin_guest.fetch("python", cfg=cfg, fetch_details=False)

    def test_http_429_translates_to_rate_limited(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise self._make(429)
        monkeypatch.setattr(linkedin_guest, "fetch_text", fake)
        with pytest.raises(RuntimeError, match="rate-limited"):
            linkedin_guest.fetch("python", cfg=cfg, fetch_details=False)

    def test_http_500_translates_to_generic(self, monkeypatch, cfg):
        def fake(*a, **k):
            raise self._make(500)
        monkeypatch.setattr(linkedin_guest, "fetch_text", fake)
        with pytest.raises(RuntimeError, match="HTTP 500"):
            linkedin_guest.fetch("python", cfg=cfg, fetch_details=False)

    def test_connection_error_translates_to_no_internet(self, monkeypatch, cfg):
        from requests import ConnectionError
        def fake(*a, **k):
            raise ConnectionError("no internet")
        monkeypatch.setattr(linkedin_guest, "fetch_text", fake)
        with pytest.raises(RuntimeError, match="no internet"):
            linkedin_guest.fetch("python", cfg=cfg, fetch_details=False)
