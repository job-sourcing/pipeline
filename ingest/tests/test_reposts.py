"""Repost + ghost detection (Sprint 3): role-matcher port semantics,
detect-reposts clustering (T2 self-test scenario), the §708 ghost
heuristic. Rows are Job-shaped (conftest.make_job)."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace as NS

import pytest

from jobsearch.reposts import (
    ATS_DIRECT_SOURCES, detect_reposts, ghost_candidates, role_fuzzy_match,
    role_tokens,
)


def row(link, posted, title, company="Acme", source="Careerjet"):
    return NS(link=link, date_posted=posted, title=title,
              company=company, source=source)


# ── role_fuzzy_match (role-matcher.mjs semantics) ──────────────────────────

@pytest.mark.parametrize("a,b,expected", [
    # exact text (case/whitespace-insensitive)
    ("Senior SRE", "senior sre ", True),
    # seniority agreement: both state, must overlap
    ("Senior Software Engineer", "Principal Software Engineer", False),
    # NOTE: "Senior SE" vs "Senior Staff SE" share ONLY baseline tokens
    # (staff/senior are stopwords) → no discriminating overlap → NOT a
    # fuzzy match (T2 rule: baseline-only overlap = same role altitude,
    # not the same opening). A shared non-baseline word re-enables matching.
    ("Senior Software Engineer", "Senior Staff Software Engineer", False),
    ("Senior Payments Platform Engineer", "Senior Staff Payments Platform Engineer", True),
    # one side seniority, other bare → Jaccard decides
    ("Software Engineer Platform", "Senior Software Engineer Platform", False),
    # lone sub-baseline qualifier = level disagreement (#2009)
    ("Associate Product Manager, Team", "Product Manager, Team", False),
    ("Product Manager, Team", "Junior Product Manager, Team", False),
    # sibling roles at the same altitude stay separate
    ("Full Stack Engineer, Foundation", "Full Stack Engineer, Guarded Releases", False),
    ("Senior Analytics Engineer", "Senior Analytics Engineer, People Analytics", False),
    # baseline-only overlap is not a match
    ("Software Engineer", "Software Developer", False),
    # genuine repost variants
    ("Senior Site Reliability Engineer", "Site Reliability Engineer III", True),
    ("Member of Technical Staff, Connector Platform",
     "Member of Technical Staff, Backend Platform", False),
])
def test_role_fuzzy_match(a, b, expected):
    assert role_fuzzy_match(a, b) == expected is not None  # sanity: bool
    assert role_fuzzy_match(a, b) is expected


def test_role_tokens_stopwords_and_specialty():
    tokens = role_tokens("Senior Remote Backend Engineer (Paris, France)")
    assert "senior" not in tokens        # seniority stopword
    assert "remote" not in tokens        # work-mode stopword
    assert "paris" not in tokens         # location stopword
    assert "backend" in tokens           # baseline but KEPT in tokens
    assert "engineer" in tokens


def test_role_tokens_short_specialty_acronyms():
    tokens = role_tokens("SRE with API and UX skills")
    assert "sre" in tokens               # SHORT_SPECIALTY survives length filter
    assert "api" in tokens
    assert "ux" in tokens
    assert "with" not in tokens          # preposition stopword


def test_role_tokens_slash_acronyms_fused():
    # "(CI/CD)" fuses to 'cicd' BEFORE the length filter drops both halves
    tokens = role_tokens("Senior SWE, Infrastructure (CI/CD)")
    assert "cicd" in tokens


# ── detect_reposts (detect-reposts.mjs self-test scenario) ─────────────────

def _t2_rows():
    return [
        # genuine repost: same role, different URL, within 90 days
        row("https://acme.com/jobs/sre-1", "2024-01-10",
            "Senior Site Reliability Engineer"),
        row("https://acme.com/jobs/sre-2", "2024-03-01",
            "Senior Site Reliability Engineer", source="Adzuna"),
        # distinct role at the same company — must NOT be flagged
        row("https://acme.com/jobs/eng-mgr", "2024-02-15",
            "Engineering Manager Platform"),
        # same role + same URL — dedup hit, NOT a repost
        row("https://acme.com/jobs/sre-1", "2024-03-20",
            "Senior Site Reliability Engineer", source="Adzuna"),
        # same role + different URL but outside the 90-day window
        row("https://acme.com/jobs/sre-3", "2024-12-01",
            "Senior Site Reliability Engineer"),
    ]


def test_detect_reposts_genuine_cluster():
    clusters = detect_reposts(_t2_rows())
    sre = [c for c in clusters
           if "Site Reliability" in c.role
           and any(a["url"] == "https://acme.com/jobs/sre-1" for a in c.appearances)]
    assert len(sre) == 1
    c = sre[0]
    assert c.repost_count == 2                     # sre-1 + sre-2
    assert c.days_span == 51
    assert c.first_seen == "2024-01-10" and c.last_seen == "2024-03-01"
    # the same-URL sighting collapsed onto the earliest appearance
    urls = [a["url"] for a in c.appearances]
    assert len(urls) == len(set(urls))


def test_detect_reposts_distinct_role_not_flagged():
    clusters = detect_reposts(_t2_rows())
    assert not any("Engineering Manager" in c.role for c in clusters)


def test_detect_reposts_outside_window_not_flagged():
    clusters = detect_reposts(_t2_rows())
    assert not any(any(a["url"] == "https://acme.com/jobs/sre-3"
                       for a in c.appearances) for c in clusters)


def test_detect_reposts_company_suffix_clustering():
    # "Acme Inc." and "Acme" are one company key (normalize_company_name)
    rows = _t2_rows() + [
        row("https://acme.com/jobs/sre-5", "2024-02-20",
            "Senior Site Reliability Engineer", company="Acme Inc.",
            source="Findwork"),
    ]
    clusters = detect_reposts(rows)
    sre = [c for c in clusters if "Site Reliability" in c.role]
    assert len(sre) == 1
    assert sre[0].repost_count == 3


def test_detect_reposts_sliding_window_overlap():
    # Jan1 + Mar15 sealed as one cluster; Mar15 + Jun10 also valid — the
    # sliding window must not drop the second pair (T2's documented case)
    rows = [
        row("https://a.com/1", "2024-01-01", "Backend Engineer"),
        row("https://a.com/2", "2024-03-15", "Backend Engineer"),
        row("https://a.com/3", "2024-06-10", "Backend Engineer"),
    ]
    clusters = detect_reposts(rows)
    # 2024-01-01 → 2024-06-10 spans 161 days: the outer span exceeds the
    # window, so we expect (at least) the Mar15+Jun10 pair to be reported
    assert any(c.days_span <= 90 and c.repost_count >= 2 for c in clusters)


def test_detect_reposts_invalid_rows_ignored():
    rows = [
        row("", "2024-01-10", "Engineer"),                    # no link
        row("https://a.com/x", None, "Engineer"),             # no date
        row("https://a.com/y", "not-a-date", "Engineer"),     # bad date
        row("https://a.com/z", "2024-01-10", ""),             # no title
    ]
    assert detect_reposts(rows) == []
    assert detect_reposts([]) == []


def test_detect_reposts_empty_input():
    assert detect_reposts([]) == []
    assert detect_reposts([row("https://a.com/1", "2024-01-01", "E")]) == []


def test_detect_reposts_custom_window():
    rows = [
        row("https://a.com/1", "2024-01-01", "Backend Engineer"),
        row("https://a.com/2", "2024-02-20", "Backend Engineer"),
    ]
    assert len(detect_reposts(rows, window_days=60)) == 1   # 50d span fits
    assert detect_reposts(rows, window_days=45) == []       # span exceeds


# ── ghost_candidates (§708) ────────────────────────────────────────────────

def test_ghost_candidate_aggregator_only_and_old():
    old = (date.today() - timedelta(days=45)).isoformat()
    recent = (date.today() - timedelta(days=5)).isoformat()
    rows = [
        row("https://g.com/1", old, "Senior Data Engineer",
            company="Ghostly", source="Careerjet"),
        row("https://g.com/2", old, "Senior Data Engineer",
            company="Ghostly", source="Adzuna"),
        # recent multi-aggregator cluster: too young → not a ghost
        row("https://r.com/1", recent, "Platform Engineer",
            company="Fresh", source="Careerjet"),
        row("https://r.com/2", recent, "Platform Engineer",
            company="Fresh", source="Adzuna"),
    ]
    ghosts = ghost_candidates(rows, days=30)
    assert len(ghosts) == 1
    g = ghosts[0]
    assert g.company == "Ghostly"
    assert g.sources == ["Adzuna", "Careerjet"]
    assert g.days_old == 45
    assert g.links == ["https://g.com/1", "https://g.com/2"]


def test_ghost_not_flagged_when_own_ats_sighting():
    old = (date.today() - timedelta(days=60)).isoformat()
    rows = [
        row("https://g.com/1", old, "Senior Data Engineer",
            company="Real", source="Careerjet"),
        row("https://g.com/2", old, "Senior Data Engineer",
            company="Real", source="Adzuna"),
        # the company's own ATS board also lists it → not a ghost.
        # NOTE: production adapters emit DOTTED sources ("Greenhouse.real",
        # "Ashby.openai") — audit P1-1: exact set-match made this branch
        # unreachable, so a live-on-own-board job could still be flagged
        # ghost (and lose its alert). Keep both forms pinned here.
        row("https://boards.greenhouse.io/real/jobs/1", old,
            "Senior Data Engineer", company="Real", source="Greenhouse.real"),
        row("https://jobs.ashbyhq.com/real-openai/senior-data-engineer",
            old, "Senior Data Engineer", company="Real", source="Ashby.openai"),
    ]
    assert ghost_candidates(rows, days=30) == []


def test_ghost_plain_workable_source_still_recognized():
    """Workable is the one non-dotted ATS-direct source — keep exact matching."""
    old = (date.today() - timedelta(days=60)).isoformat()
    rows = [
        row("https://g.com/1", old, "Engineer", company="W", source="Careerjet"),
        row("https://g.com/2", old, "Engineer", company="W", source="Adzuna"),
        row("https://apply.workable.com/w/jobs/1", old,
            "Engineer", company="W", source="Workable"),
    ]
    assert ghost_candidates(rows, days=30) == []


def test_ghost_needs_two_distinct_aggregators():
    old = (date.today() - timedelta(days=60)).isoformat()
    rows = [row("https://g.com/1", old, "Senior Data Engineer",
                company="Solo", source="Careerjet"),
            row("https://g.com/2", old, "Senior Data Engineer",
                company="Solo", source="Careerjet")]
    assert ghost_candidates(rows, days=30) == []


def test_ghost_ats_direct_set_contains_expected_sources():
    assert {"Greenhouse", "Lever", "SmartRecruiters", "Ashby",
            "Personio", "Workable"} <= ATS_DIRECT_SOURCES


def test_ghost_custom_ats_sources():
    old = (date.today() - timedelta(days=60)).isoformat()
    rows = [
        row("https://g.com/1", old, "Engineer", source="Careerjet"),
        row("https://g.com/2", old, "Engineer", source="Adzuna"),
        row("https://g.com/3", old, "Engineer", source="WTTJ"),
    ]
    # default: WTTJ is an aggregator → ghost
    assert len(ghost_candidates(rows, days=30)) == 1
    # treating WTTJ as own-ATS kills the ghost signal
    assert ghost_candidates(rows, days=30,
                            ats_sources=frozenset({"WTTJ"})) == []
