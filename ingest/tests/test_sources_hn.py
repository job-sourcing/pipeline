"""HN "Ask HN: Who is hiring?" monthly thread client tests (SRC-HN).

Monkeypatch pattern: hn_whos_hiring does `from .base import fetch_json` at
import time, so we patch `jobsearch.sources.hn_whos_hiring.fetch_json`, NOT
`jobsearch.sources.base.fetch_json` (same gotcha as test_sources.py /
test_sources_freekey.py / test_sources_ats_as.py / test_sources_ats_gl.py).

Fixture shape (tests/fixtures/sources/hn_thread.json):
  • search_response — replays the Algolia /search_by_date endpoint. The
    fixture carries 3 hits: an unrelated "Ask HN: Who wants to fund DB
    research?" story (which must be filtered out), the August 2026 thread
    (objectID 49156683 — the fixture's primary thread), and the July 2026
    thread (filtered out because we take the FIRST match = newest).
  • thread — replays the Algolia /items/{thread_id} endpoint. Carries 8
    top-level children: 7 real-format job postings + 1 empty text (tests
    the malformed-skip path). The 7 postings cover: standard pipe format,
    swapped-order 5-segment format, salary+URL segment, hybrid parenthetical
    that should NOT promote to location, newline-separated body, no-pipe
    narrative, and the REMOTELY variant.

All unit tests are NO-NETWORK — fetch_json is monkeypatched. The single
@pytest.mark.live test hits the real Algolia API; deselected by default,
opt in with `pytest -m live`.
"""
from __future__ import annotations

import json
import re

import pytest
import requests

from jobsearch.config import Config, load_config
from jobsearch.sources import hn_whos_hiring

from conftest import SOURCE_FIXTURES


@pytest.fixture
def cfg(tmp_path) -> Config:
    """Config with a temp DB; hn_thread_id is left empty by default (auto-
    discover path). Pin via `cfg.hn_thread_id = '49156683'` to test the
    override path."""
    return Config(db_path=tmp_path / "t.db")


def load_fixture(name: str):
    return json.loads((SOURCE_FIXTURES / name).read_text(encoding="utf-8"))


def _resp(status: int) -> requests.Response:
    """Build a real-enough Response for HTTPError(response=...)."""
    r = requests.Response()
    r.status_code = status
    return r


# ── Parser — pure-function tests on parse_comment ────────────────────────

class TestParser:
    def test_parse_standard_format(self):
        """Senzing: `Co | Title | Remote (USA) | Full-Time<p>...` — the
        canonical HN format. Company = first segment, title = segment
        with 'Engineer', work_mode = REMOTE, parenthetical 'USA' promoted
        to location (geo match)."""
        out = hn_whos_hiring.parse_comment(
            "Senzing | Platform Engineer | Remote (USA) | Full-Time"
            "<p>Senzing builds entity-resolution SDK technology. "
            "H1B sponsorship available. Apply to jobs@senzing.com"
        )
        assert out is not None
        assert out["company"] == "Senzing"
        assert out["title"] == "Platform Engineer"
        assert out["work_mode"] == "REMOTE"
        assert out["remote"] is True
        assert out["location"] == "USA"   # parenthetical promoted (geo match)
        assert out["salary"] == ""

    def test_parse_swapped_order_with_remote_in_middle(self):
        """PostHog: `Co | Full-Time | Title | REMOTE (all remote) | GMT | ...`
        — 5 segments, swapped order. The 'REMOTE (all remote)' parenthetical
        is 'all remote' which CONTAINS a work-mode word → must NOT be
        promoted to location (it's a work-mode qualifier, not a place)."""
        out = hn_whos_hiring.parse_comment(
            "PostHog | Full-Time | Technical CSMs, AI Research Engineer | "
            "REMOTE (all remote) | Hiring GMT-8 to GMT+2 | "
            "PostHog makes your product self-driving.<p>Stack: Python, Django."
        )
        assert out["company"] == "PostHog"
        assert "AI Research Engineer" in out["title"]
        assert out["work_mode"] == "REMOTE"
        assert out["remote"] is True
        # The 'all remote' parenthetical was rejected → no location from it.
        assert out["location"] == ""

    def test_parse_salary_segment(self):
        """SmarterDx: `Co | $150-250k+ + equity + benefits | Remote (US only)
        | Multiple roles | <a>URL</a><p>...`. Salary is segment 2, URL is
        segment 5 (only the URL survives `<a>` tag stripping). 'Multiple
        roles' isn't a title-keyword segment → fallback title."""
        out = hn_whos_hiring.parse_comment(
            'SmarterDx | 150-250k+ + equity + benefits | Remote (US only) | '
            'Multiple roles | <a href="https://smarterdx.com/careers" '
            'rel="nofollow">https://smarterdx.com/careers</a><p>'
            'Clinical AI for healthcare billing. Stack: Python, PyTorch.'
        )
        assert out["company"] == "SmarterDx"
        assert out["title"] == "Multiple Roles"   # no title kw in segments
        assert out["work_mode"] == "REMOTE"
        assert out["location"] == "US only"        # parenthetical, geo match
        assert out["salary"] == "150-250k+ + equity + benefits"
        assert out["url"] == "https://smarterdx.com/careers"

    def test_parse_hybrid_with_parenthetical_not_promoted_to_location(self):
        """Marple: `Co | Title | Antwerp, Belgium | Full-time | Hybrid (3
        days/week onsite)<p>...`. The 'Hybrid (3 days/week onsite)' segment
        is work_mode=HYBRID; the parenthetical '3 days/week onsite' contains
        'onsite' (a work-mode word) → must NOT be promoted to location
        (the 'Antwerp, Belgium' segment from position 3 wins instead)."""
        out = hn_whos_hiring.parse_comment(
            "Marple | Software Engineer | Antwerp, Belgium | Full-time | "
            "Hybrid (3 days/week onsite)<p>We build cloud software for "
            "time series data. Stack: Python, TypeScript, PostgreSQL."
        )
        assert out["company"] == "Marple"
        assert out["title"] == "Software Engineer"
        assert out["work_mode"] == "HYBRID"
        assert out["remote"] is False
        assert out["location"] == "Antwerp, Belgium"   # not "3 days/week onsite"

    def test_parse_newline_separator_with_salary(self):
        """Ashby: `Co | YC 19 | REMOTE | Hiring Engineering Leaders | $200k–
        $275k\\nAshby is the all-in-one...` — body separated by a literal
        newline (no <p>). Salary is the LAST segment."""
        out = hn_whos_hiring.parse_comment(
            "Ashby | YC 19 | REMOTE | Hiring Engineering Leaders | "
            "$200k\u2013$275k\nAshby is the all-in-one recruiting platform. "
            "We're scaling our engineering leadership team. Stack: TypeScript."
        )
        assert out["company"] == "Ashby"
        # "Hiring Engineering Leaders" matches the title-keyword regex via
        # "engineering" (engineer + ing suffix). So title segment is captured.
        assert "Engineering" in out["title"] or "Leaders" in out["title"]
        assert out["work_mode"] == "REMOTE"
        assert out["remote"] is True
        assert out["salary"] == "$200k\u2013$275k"

    def test_parse_no_pipe_narrative(self):
        """Sumble: no `|` delimiters anywhere in the header — the parser
        treats the whole first paragraph as one segment. Company is
        extracted from the first 1-2 capitalized words ('Sumble'), the
        rest of the narrative becomes the description. The keyword filter
        still finds matches in the description body."""
        out = hn_whos_hiring.parse_comment(
            "Sumble is the newco from the founders of Kaggle. We are hiring "
            "full stack engineers, ai/ml engineers and growth/marketing. "
            "Can view our roles and apply here: "
            '<a href="https://sumble-inc.workable.com/" rel="nofollow">'
            'https://sumble-inc.workable.com/</a>\n'
            "We are building a knowledge graph from web data. Stack: Python."
        )
        assert out is not None
        # Company extracted from the narrative prefix.
        assert out["company"] == "Sumble"
        # Title fallback: no Engineer/Developer in header segments; body
        # scan finds 'engineers' (matches engineer + s suffix).
        assert out["title"] != ""  # either a body-scanned title or "Multiple Roles"
        # The narrative body (with the residual after 'Sumble') carries the
        # actual job info — so keyword matching still works downstream.
        assert "Kaggle" in out["description"]
        assert "Python" in out["description"]
        # URL extracted from the inline <a> tag.
        assert out["url"] == "https://sumble-inc.workable.com/"

    def test_parse_remotely_token_normalized(self):
        """REMOTELY → REMOTE normalization. Acme Cloud uses the variant
        spelling 'REMOTELY' — the parser must normalize it to 'REMOTE'
        so the `remote` bool is True."""
        out = hn_whos_hiring.parse_comment(
            "Acme Cloud | Senior Backend Engineer | REMOTELY (worldwide) | "
            "Python, Go, Kubernetes<p>Cloud-native event streaming infra. "
            "We offer H1B visa sponsorship. $180k-$220k. careers@acme.cloud"
        )
        assert out["company"] == "Acme Cloud"
        assert out["title"] == "Senior Backend Engineer"
        assert out["work_mode"] == "REMOTE"   # REMOTELY → REMOTE
        assert out["remote"] is True
        assert out["location"] == "worldwide"  # parenthetical, geo match

    def test_parse_empty_comment_returns_none(self):
        """An empty text or whitespace-only text → None (skipped at fetch
        level, no crash)."""
        assert hn_whos_hiring.parse_comment("") is None
        assert hn_whos_hiring.parse_comment("   \n\n  ") is None

    def test_parse_malformed_html_does_not_crash(self):
        """Random text with broken tags / no structure — the parser must
        return SOMETHING (not raise). Company extracted from the prefix."""
        out = hn_whos_hiring.parse_comment(
            "Random Startup <<<<broken | tags | everywhere | >>><p>still works"
        )
        assert out is not None
        # No crash is the assertion; field values are best-effort.
        assert "company" in out

    def test_parse_url_only_segment_does_not_become_company(self):
        """If the first segment is ONLY a URL (rare but possible), the parser
        should not promote it to company — it should fall through to find
        a real company segment."""
        out = hn_whos_hiring.parse_comment(
            '<a href="https://example.com">https://example.com</a> | '
            "Acme Corp | Senior Engineer | REMOTE | Full-time<p>Body."
        )
        assert out is not None
        assert out["company"] == "Acme Corp"
        assert out["url"] == "https://example.com"

    def test_parse_onsite_work_mode(self):
        """ONSITE token → work_mode=ONSITE, remote=False (mirror of REMOTE)."""
        out = hn_whos_hiring.parse_comment(
            "Shepherd | ONSITE | San Francisco, CA | Senior Underwriter<p>"
            "Commercial insurance. Stack: Python."
        )
        assert out["work_mode"] == "ONSITE"
        assert out["remote"] is False
        assert "San Francisco" in out["location"]

    def test_has_title_keyword_matches_plurals(self):
        """'Engineering' (gerund) and 'engineers' (plural) must both match
        the title-keyword regex — without the `(?:s|ing)?` suffix, segments
        like 'Hiring Engineering Leaders' wouldn't classify as title."""
        assert hn_whos_hiring._has_title_keyword("Hiring Engineering Leaders")
        assert hn_whos_hiring._has_title_keyword("Senior Engineers")
        assert hn_whos_hiring._has_title_keyword("Designers")
        assert hn_whos_hiring._has_title_keyword("Manager")  # bare singular
        # Negative: words that merely CONTAIN the substring 'engineer' but
        # aren't whole-word matches.
        assert not hn_whos_hiring._has_title_keyword("reengineering project")
        assert not hn_whos_hiring._has_title_keyword("full stack")

    def test_scan_body_for_title_finds_capitalized_phrase(self):
        """When no title segment is in the header, the body scan finds the
        first standalone title keyword and expands to include surrounding
        capitalized words."""
        body = ("We are a small team building distributed systems. "
                "Looking for a Senior Backend Engineer to lead the team. "
                "Stack: Python, Go, Kubernetes.")
        out = hn_whos_hiring._scan_body_for_title(body)
        assert "Engineer" in out
        assert "Senior Backend" in out

    def test_scan_body_for_title_returns_empty_when_no_match(self):
        assert hn_whos_hiring._scan_body_for_title(
            "We sell pet food. No tech roles here."
        ) == ""

    def test_normalize_work_mode(self):
        assert hn_whos_hiring._normalize_work_mode("remote") == "REMOTE"
        assert hn_whos_hiring._normalize_work_mode("REMOTE") == "REMOTE"
        assert hn_whos_hiring._normalize_work_mode("Remotely") == "REMOTE"
        assert hn_whos_hiring._normalize_work_mode("onsite") == "ONSITE"
        assert hn_whos_hiring._normalize_work_mode("on-site") == "ONSITE"
        assert hn_whos_hiring._normalize_work_mode("hybrid") == "HYBRID"

    def test_month_from_created_at_pins_to_first_of_month(self):
        """The thread's created_at is normalized to YYYY-MM-01 — every
        parsed job carries the thread's month (HN comments don't have
        per-comment timestamps in the Algolia payload)."""
        assert hn_whos_hiring._month_from_created_at(
            "2026-08-03T15:00:54.000Z") == "2026-08-01"
        assert hn_whos_hiring._month_from_created_at(None) is None
        assert hn_whos_hiring._month_from_created_at("") is None


# ── Discovery — _discover_thread_id ──────────────────────────────────────

class TestDiscovery:
    def test_discover_returns_first_monthly_thread(self, monkeypatch, cfg):
        """The fixture's search_response has 3 hits — the first is an
        unrelated "Ask HN: Who wants to fund DB research?" story (must
        be filtered out); the SECOND is the August 2026 monthly thread.
        Discovery returns (objectID, created_at) for the first MATCHING
        title, not the first hit."""
        fx = load_fixture("hn_thread.json")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx["search_response"])
        thread_id, created_at = hn_whos_hiring._discover_thread_id(cfg)
        assert thread_id == "49156683"
        assert "2026-08" in created_at

    def test_discover_filters_unrelated_titles(self, monkeypatch, cfg):
        """A search_response where NO hit matches the monthly-thread title
        regex → RuntimeError (D3: surface, don't silently return None)."""
        monkeypatch.setattr(hn_whos_hiring, "fetch_json", lambda *a, **k: {
            "hits": [
                {"objectID": "1", "title": "Ask HN: Who wants to fund DB research?"},
                {"objectID": "2", "title": "Ask HN: Are there any startups?"},
                {"objectID": "3", "title": "Ask HN: Who is dating? (July 2026)"},
            ]
        })
        with pytest.raises(RuntimeError, match="no monthly thread found"):
            hn_whos_hiring._discover_thread_id(cfg)

    def test_discover_skips_non_story_payload(self, monkeypatch, cfg):
        """Defensive: a malformed Algolia response (no 'hits' key) doesn't
        crash — discovery raises a clean RuntimeError instead."""
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: {"unexpected": "payload"})
        with pytest.raises(RuntimeError, match="no monthly thread found"):
            hn_whos_hiring._discover_thread_id(cfg)


# ── Fetch — fetch() with mocked fetch_json ───────────────────────────────

class TestFetch:
    def test_fetch_parses_fixture_no_filter(self, monkeypatch, cfg):
        """No keyword filter → returns all 7 parseable comments (the 8th
        is empty text → skipped)."""
        fx = load_fixture("hn_thread.json")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx["thread"])
        cfg.hn_thread_id = "49156683"   # skip discovery
        jobs = hn_whos_hiring.fetch("", num_results=20, cfg=cfg)
        # 7 postings; the empty-text comment is skipped silently.
        assert len(jobs) == 7
        # Companies are extracted from each comment (parsing spec).
        companies = {j.company for j in jobs}
        assert "Senzing" in companies
        assert "PostHog" in companies
        assert "SmarterDx" in companies
        assert "Marple" in companies
        assert "Ashby" in companies
        assert "Sumble" in companies
        assert "Acme Cloud" in companies

    def test_fetch_filters_by_keyword(self, monkeypatch, cfg):
        """keywords='python' → only comments whose title+description+tags
        contain 'python' survive. The fixture's Senzing comment doesn't
        mention 'python' in its body (only 'AWS', 'infra', 'platform') so
        it's filtered out; the other 6 mention Python in their stack."""
        fx = load_fixture("hn_thread.json")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx["thread"])
        cfg.hn_thread_id = "49156683"
        jobs = hn_whos_hiring.fetch("python", num_results=20, cfg=cfg)
        companies = {j.company for j in jobs}
        assert "Senzing" not in companies   # body doesn't mention python
        assert "PostHog" in companies       # body has 'Stack: Python, Django'
        assert "Marple" in companies        # body has 'Stack: Python, TypeScript'
        assert "Acme Cloud" in companies    # body has 'Stack: Python, Go'

    def test_fetch_keyword_no_match_returns_empty(self, monkeypatch, cfg):
        fx = load_fixture("hn_thread.json")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx["thread"])
        cfg.hn_thread_id = "49156683"
        assert hn_whos_hiring.fetch("cobol", cfg=cfg) == []

    def test_fetch_respects_num_results(self, monkeypatch, cfg):
        fx = load_fixture("hn_thread.json")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx["thread"])
        cfg.hn_thread_id = "49156683"
        jobs = hn_whos_hiring.fetch("", num_results=3, cfg=cfg)
        assert len(jobs) == 3

    def test_fetch_pinned_thread_id_skips_discovery(self, monkeypatch, cfg):
        """When cfg.hn_thread_id is set, fetch() must NOT call the Algolia
        search endpoint — only the /items/{id} call is made. This is the
        'replayable run against a known-good thread' path."""
        fx = load_fixture("hn_thread.json")
        calls = []

        def fake(url, *, params=None, cfg=None, headers=None):
            calls.append(url)
            if "search_by_date" in url:
                return fx["search_response"]
            if "/items/" in url:
                return fx["thread"]
            raise AssertionError(f"unexpected url: {url}")

        monkeypatch.setattr(hn_whos_hiring, "fetch_json", fake)
        cfg.hn_thread_id = "49156683"
        hn_whos_hiring.fetch("", num_results=3, cfg=cfg)
        assert len(calls) == 1
        assert calls[0] == "https://hn.algolia.com/api/v1/items/49156683"

    def test_fetch_auto_discover_makes_two_calls(self, monkeypatch, cfg):
        """Empty cfg.hn_thread_id → fetch() calls Algolia search first
        (discovery), then /items/{thread_id} (parse). Exactly 2 calls."""
        fx = load_fixture("hn_thread.json")
        calls = []

        def fake(url, *, params=None, cfg=None, headers=None):
            calls.append((url, params))
            if "search_by_date" in url:
                return fx["search_response"]
            if "/items/" in url:
                return fx["thread"]
            raise AssertionError(f"unexpected url: {url}")

        monkeypatch.setattr(hn_whos_hiring, "fetch_json", fake)
        cfg.hn_thread_id = ""
        jobs = hn_whos_hiring.fetch("", num_results=3, cfg=cfg)
        assert len(jobs) == 3
        assert len(calls) == 2
        # First call: discovery search (with the configured params).
        assert "search_by_date" in calls[0][0]
        assert calls[0][1]["tags"] == "story"
        assert calls[0][1]["hitsPerPage"] == 30
        # Second call: thread fetch.
        assert calls[1][0] == "https://hn.algolia.com/api/v1/items/49156683"

    def test_fetch_link_is_comment_url(self, monkeypatch, cfg):
        """Per spec: link = the comment URL on HN (not the company URL
        scraped from the text). Comment URL pattern: news.ycombinator.com/
        item?id={comment_id}."""
        fx = load_fixture("hn_thread.json")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx["thread"])
        cfg.hn_thread_id = "49156683"
        jobs = hn_whos_hiring.fetch("", num_results=20, cfg=cfg)
        for j in jobs:
            assert j.link.startswith("https://news.ycombinator.com/item?id=")
            # The comment ID is part of the URL (not the thread ID).
            cid = j.link.rsplit("=", 1)[1]
            assert cid != "49156683"  # not the thread ID
            assert cid.isdigit()

    def test_fetch_source_attribution(self, monkeypatch, cfg):
        """Per spec: source field = 'HN Who's Hiring' (literal apostrophe)."""
        fx = load_fixture("hn_thread.json")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx["thread"])
        cfg.hn_thread_id = "49156683"
        jobs = hn_whos_hiring.fetch("", num_results=3, cfg=cfg)
        assert all(j.source == "HN Who's Hiring" for j in jobs)

    def test_fetch_date_posted_is_thread_month(self, monkeypatch, cfg):
        """Every parsed job carries the thread's month (Aug 2026 → 2026-08-01)
        because HN comments don't have per-comment timestamps in the Algolia
        payload."""
        fx = load_fixture("hn_thread.json")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx["thread"])
        cfg.hn_thread_id = "49156683"
        jobs = hn_whos_hiring.fetch("", num_results=20, cfg=cfg)
        for j in jobs:
            assert j.date_posted == "2026-08-01"

    def test_fetch_h1b_detected(self, monkeypatch, cfg):
        """detect_h1b scans title + tags + description. Senzing / SmarterDx /
        Acme Cloud all mention H1B sponsorship in their bodies."""
        fx = load_fixture("hn_thread.json")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx["thread"])
        cfg.hn_thread_id = "49156683"
        jobs = hn_whos_hiring.fetch("", num_results=20, cfg=cfg)
        by_company = {j.company: j for j in jobs}
        assert by_company["Senzing"].h1b_mention is True        # "H1B sponsorship available"
        assert by_company["SmarterDx"].h1b_mention is True      # "We offer H1B visa sponsorship"
        assert by_company["Acme Cloud"].h1b_mention is True     # "We offer H1B visa sponsorship"
        assert by_company["PostHog"].h1b_mention is False

    def test_fetch_contact_email_extracted(self, monkeypatch, cfg):
        """extract_email scans the description. Senzing, PostHog, Ashby,
        Acme Cloud all include a contact email; SmarterDx + Marple + Sumble
        don't."""
        fx = load_fixture("hn_thread.json")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx["thread"])
        cfg.hn_thread_id = "49156683"
        jobs = hn_whos_hiring.fetch("", num_results=20, cfg=cfg)
        by_company = {j.company: j for j in jobs}
        assert by_company["Senzing"].contact_email == "jobs@senzing.com"
        assert by_company["PostHog"].contact_email == "careers@posthog.com"
        assert by_company["Ashby"].contact_email == "careers@ashbyhq.com"
        assert by_company["Acme Cloud"].contact_email == "careers@acme.cloud"
        assert by_company["SmarterDx"].contact_email is None

    def test_fetch_remote_inferred_from_work_mode(self, monkeypatch, cfg):
        fx = load_fixture("hn_thread.json")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx["thread"])
        cfg.hn_thread_id = "49156683"
        jobs = hn_whos_hiring.fetch("", num_results=20, cfg=cfg)
        by_company = {j.company: j for j in jobs}
        # REMOTE / REMOTELY → remote=True
        assert by_company["Senzing"].remote is True      # Remote (USA)
        assert by_company["PostHog"].remote is True      # REMOTE (all remote)
        assert by_company["SmarterDx"].remote is True    # Remote (US only)
        assert by_company["Ashby"].remote is True        # REMOTE
        assert by_company["Acme Cloud"].remote is True   # REMOTELY → REMOTE
        # HYBRID → remote=False (Marple)
        assert by_company["Marple"].remote is False
        # No work-mode token in header or body → remote=False (Sumble)
        assert by_company["Sumble"].remote is False

    def test_fetch_http_error_on_search_raises_runtime(self, monkeypatch, cfg):
        """When auto-discover is on and the Algolia search endpoint fails
        (any HTTP error), fetch() raises RuntimeError so the parent
        aggregator's SourceResult.error surfaces it (D3 policy)."""
        def fake(url, *, params=None, cfg=None, headers=None):
            if "search_by_date" in url:
                raise requests.HTTPError("503", response=_resp(503))
            raise AssertionError("should not reach /items/ when discovery fails")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json", fake)
        cfg.hn_thread_id = ""  # force discovery
        with pytest.raises(RuntimeError, match="thread discovery failed"):
            hn_whos_hiring.fetch("python", cfg=cfg)

    def test_fetch_http_error_on_thread_raises_runtime(self, monkeypatch, cfg):
        """When the /items/{thread_id} call fails, fetch() raises RuntimeError
        (D3). This path also covers the pinned-thread-id case where discovery
        is skipped — only the /items call is made and must fail loudly."""
        def fake(url, *, params=None, cfg=None, headers=None):
            if "/items/" in url:
                raise requests.HTTPError("404", response=_resp(404))
            raise AssertionError("unexpected search call")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json", fake)
        cfg.hn_thread_id = "99999999999"  # nonexistent
        with pytest.raises(RuntimeError, match="thread fetch failed"):
            hn_whos_hiring.fetch("python", cfg=cfg)

    def test_fetch_malformed_comment_skipped_not_crashed(self, monkeypatch, cfg):
        """A single malformed comment (parse_comment raises) is caught and
        skipped — the other comments still parse and return. parse_comment
        itself never raises on real-world inputs, but the fetch() wrapper
        has a try/except so even a hypothetical parser regression doesn't
        crash the source (D3 isolation)."""
        fx = load_fixture("hn_thread.json")
        # Inject a comment whose `text` is a non-string to force a parser
        # exception in the wrapper. Real HN comments are always strings, but
        # this guards against future payload-shape regressions.
        fx_thread = json.loads(json.dumps(fx["thread"]))
        fx_thread["children"].append({
            "id": 99999999,
            "text": None,   # parse_comment returns None for falsy text
            "children": [],
        })
        # And a child that's not even a dict (defensive — API contract says
        # dict, but be resilient).
        fx_thread["children"].append("not-a-dict")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx_thread)
        cfg.hn_thread_id = "49156683"
        jobs = hn_whos_hiring.fetch("", num_results=20, cfg=cfg)
        # The 7 parseable comments still came back; the malformed ones skipped.
        assert len(jobs) == 7

    def test_fetch_location_filter_for_remote(self, monkeypatch, cfg):
        """When location='Remote', only remote-flagged jobs survive
        (mirrors the Source.remote_only flag set in REGISTRY)."""
        fx = load_fixture("hn_thread.json")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx["thread"])
        cfg.hn_thread_id = "49156683"
        jobs = hn_whos_hiring.fetch("", location="Remote", num_results=20,
                                    cfg=cfg)
        companies = {j.company for j in jobs}
        # All returned jobs are remote.
        assert all(j.remote for j in jobs)
        # Marple (HYBRID) and Sumble (no work mode) are filtered out.
        assert "Marple" not in companies
        assert "Sumble" not in companies

    def test_fetch_location_filter_substring(self, monkeypatch, cfg):
        """When location is a city/state/country substring (e.g. 'Belgium'),
        only jobs whose parsed location OR description contains it survive."""
        fx = load_fixture("hn_thread.json")
        monkeypatch.setattr(hn_whos_hiring, "fetch_json",
                            lambda *a, **k: fx["thread"])
        cfg.hn_thread_id = "49156683"
        jobs = hn_whos_hiring.fetch("", location="Belgium", num_results=20,
                                    cfg=cfg)
        # Marple is in Antwerp, Belgium → its location segment is
        # "Antwerp, Belgium" → substring match.
        assert any(j.company == "Marple" for j in jobs)


# ── Config defaults + env resolution ─────────────────────────────────────

class TestConfigHnField:
    def test_defaults_empty(self, tmp_path):
        """Default is empty string (auto-discover at fetch time)."""
        cfg = Config(db_path=tmp_path / "t.db")
        assert cfg.hn_thread_id == ""

    def test_env_overrides(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HN_THREAD_ID", "49156683")
        cfg = Config(db_path=tmp_path / "t.db")
        assert cfg.hn_thread_id == "49156683"

    def test_load_config_merges(self, monkeypatch, tmp_path):
        """load_config (used by the registry) resolves HN_THREAD_ID too —
        so a user with the env var set doesn't need an explicit Config(...)."""
        monkeypatch.setenv("HN_THREAD_ID", "48747976")
        cfg = load_config()
        assert cfg.hn_thread_id == "48747976"


# ── Live test (deselected by default; -m live to run) ────────────────────

class TestLiveHn:
    @pytest.mark.live
    def test_live_auto_discover_and_parse(self):
        """Live: hit the real Algolia API to auto-discover the current
        month's thread, then parse 3 jobs. Asserts the API is reachable
        + the parser handles real-world HN comment text."""
        from pathlib import Path
        import os
        # Use a temp DB so we never touch the real tracker.db.
        db_path = Path("/tmp/hn_live_test.db")
        cfg = Config(db_path=db_path)
        # Force auto-discovery (don't pin the thread ID; the test should
        # work for ANY future month, not just August 2026).
        cfg.hn_thread_id = ""
        jobs = hn_whos_hiring.fetch("engineer", num_results=3, cfg=cfg)
        assert isinstance(jobs, list)
        assert len(jobs) >= 1, "expected at least 1 real job from the live thread"
        for j in jobs:
            assert j.source == "HN Who's Hiring"
            assert j.company and j.title
            assert j.link.startswith("https://news.ycombinator.com/item?id=")
            # date_posted is YYYY-MM-01 (the thread's month).
            assert re.fullmatch(r"\d{4}-\d{2}-01", j.date_posted or "")
        # Cleanup
        try:
            os.unlink(db_path)
        except OSError:
            pass
