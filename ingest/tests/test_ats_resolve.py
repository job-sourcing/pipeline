"""ATS resolution (Step E finishing move): link→platform detection,
company slug candidates, and the directory-backed company→ATS resolver
against a fixture DB (the real drained directory is gitignored data)."""
from __future__ import annotations

import json
import sqlite3

import pytest

from jobsearch.ats_resolve import (
    AtsDirectory, Board, board_url, company_slug_candidates, detect_platform,
)


# ── detect_platform ────────────────────────────────────────────────────────

@pytest.mark.parametrize("link,expected", [
    # greenhouse: three URL shapes
    ("https://boards.greenhouse.io/embed/job_app?for=linear&job_id=1",
     ("greenhouse", "linear")),
    ("https://job-boards.greenhouse.io/openai/jobs/123",
     ("greenhouse", "openai")),
    ("https://boards-api.greenhouse.io/v1/boards/ramp/jobs",
     ("greenhouse", "ramp")),
    # lever: incl. the eu subdomain
    ("https://jobs.lever.co/ramp/abc-123", ("lever", "ramp")),
    ("https://jobs.eu.lever.co/someco/x", ("lever", "someco")),
    # ashby / smartrecruiters
    ("https://jobs.ashbyhq.com/notion/abc", ("ashby", "notion")),
    ("https://careers.smartrecruiters.com/Visa/abc", ("smartrecruiters", "visa")),
    # workable: apply-path shape AND subdomain shape
    ("https://apply.workable.com/acme/j/1", ("workable", "acme")),
    ("https://acme.workable.com/jobs/1", ("workable", "acme")),
    # personio / workday / other hosted boards
    ("https://acme.jobs.personio.de/list", ("personio", "acme")),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/x", ("workday", "nvidia")),
    ("https://acme.teamtailor.com/jobs/1", ("teamtailor", "acme")),
    ("https://acme.recruitee.com/jobs/1", ("recruitee", "acme")),
    ("https://acme.breezy.hr/p/xyz", ("breezy", "acme")),
    ("https://acme.bamboohr.com/careers/1", ("bamboohr", "acme")),
    ("https://acme.recruitcrm.io/jobs/1", ("recruitcrm", "acme")),
    ("https://acme.applytojob.com/apply/xyz", ("jazzhr", "acme")),
    # NOT ATS links
    ("https://www.somecompany.com/careers", (None, None)),
    ("https://remotive.com/remote-jobs/software-engineer", (None, None)),
    ("https://www.linkedin.com/jobs/view/123", (None, None)),
    ("", (None, None)),
    (None, (None, None)),
    # greenhouse embed WITHOUT for= param → not a slug match (falls to the
    # generic path shape, whose first segment 'embed' is not a slug — the
    # embed widget always carries for=, so this shape is unrecognized)
    ("https://boards.greenhouse.io/embed/job_app?job_id=1", ("greenhouse", "embed")),
])
def test_detect_platform(link, expected):
    got = detect_platform(link)
    if expected == (None, None):
        assert got == (None, None)
    else:
        assert got == expected


def test_board_url_templates():
    assert board_url("greenhouse", "linear") == "https://boards.greenhouse.io/linear"
    assert board_url("lever", "ramp") == "https://jobs.lever.co/ramp"
    assert board_url("ashby", "notion") == "https://jobs.ashbyhq.com/notion"
    assert board_url("workable", "acme") == "https://acme.workable.com"
    assert board_url("unknown-platform", "x") is None


# ── company_slug_candidates ────────────────────────────────────────────────

def test_slug_candidates_two_stripping_levels():
    # legal-only: "Linear Labs, Inc." → 'linear-labs' (+fused); then
    # legal+descriptor: → 'linear' (the actual ashby slug for Linear)
    cands = company_slug_candidates("Linear Labs, Inc.")
    assert "linear-labs" in cands
    assert "linearlabs" in cands
    assert "linear" in cands


def test_slug_candidates_descriptor_drop():
    # "Palantir Technologies" registers as 'palantir'
    cands = company_slug_candidates("Palantir Technologies")
    assert "palantir" in cands
    assert "palantir-technologies" in cands


def test_slug_candidates_dashed_and_fused():
    cands = company_slug_candidates("Some Company Corp")
    assert "some-company" in cands
    assert "somecompany" in cands
    assert "some" in cands
    # 'corp' (legal) never survives into any candidate
    assert all("corp" not in c for c in cands)


def test_slug_candidates_empty():
    assert company_slug_candidates("") == []
    assert company_slug_candidates("   ") == []
    assert company_slug_candidates(None) == []


# ── AtsDirectory (fixture DB) ──────────────────────────────────────────────

@pytest.fixture
def directory_db(tmp_path):
    """A miniature ats_directory.db with the production schema."""
    db = tmp_path / "ats_directory.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE directory (
            ats TEXT NOT NULL,
            slug TEXT NOT NULL,
            company TEXT DEFAULT '',
            job_count INTEGER,
            status TEXT DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_probed_at TEXT,
            PRIMARY KEY (ats, slug)
        )""")
    rows = [
        # (ats, slug, company, job_count, status)
        ("ashby", "linear", "Linear", 135, "live"),
        ("ashby", "notion", "Notion", 100, "live"),
        ("greenhouse", "linear", "Linear", 40, "live"),      # same co, 2 ATS
        ("lever", "stripe", "Stripe", 999, "live"),
        ("greenhouse", "deadco", "", None, "dead"),          # dead → excluded
        ("ashby", "pendingco", "", None, "pending"),         # pending → excluded
    ]
    conn.executemany("INSERT INTO directory VALUES (?, ?, ?, ?, ?, 0, NULL)",
                     [(a, s, c, n, st) for a, s, c, n, st in rows])
    conn.commit()
    conn.close()
    return db


def test_directory_missing_db_returns_empty(tmp_path):
    # BOTH backends absent (explicit snapshot path so the repo's committed
    # snapshot never leaks into the test)
    d = AtsDirectory(tmp_path / "nope.db",
                     snapshot_path=tmp_path / "nope.json")
    assert not d.available
    assert d.mode == "none"
    assert d.resolve("Linear") == []
    assert d.resolve_slug("ashby", "linear") is None
    assert d.stats() == {}
    d.close()


def test_directory_resolve_by_slug(directory_db):
    with AtsDirectory(directory_db) as d:
        assert d.available
        boards = d.resolve("Linear Labs, Inc.")
        # slug 'linear' hits BOTH live boards; higher job_count first
        assert [(b.platform, b.slug) for b in boards] == [("ashby", "linear"),
                                                          ("greenhouse", "linear")]
        assert boards[0].job_count == 135
        assert boards[0].company == "Linear"
        assert boards[0].board_url == "https://jobs.ashbyhq.com/linear"


def test_directory_resolve_exact_slug_only_for_dead(directory_db):
    with AtsDirectory(directory_db) as d:
        # dead/pending rows never resolve, even on exact slug
        assert d.resolve("Deadco") == []
        assert d.resolve_slug("greenhouse", "deadco") is None
        assert d.resolve_slug("ashby", "pendingco") is None


def test_directory_resolve_by_learned_company_name(directory_db):
    with AtsDirectory(directory_db) as d:
        # 'Stripe' has no slug candidate collision, but the directory LEARNED
        # the company name while probing → name-corroboration pass finds it
        boards = d.resolve("Stripe")
        assert [(b.platform, b.slug) for b in boards] == [("lever", "stripe")]


def test_directory_resolve_slug_exact_lookup(directory_db):
    with AtsDirectory(directory_db) as d:
        b = d.resolve_slug("lever", "stripe")
        assert b is not None and b.platform == "lever" and b.slug == "stripe"
        assert d.resolve_slug("lever", "nope") is None


def test_directory_stats(directory_db):
    with AtsDirectory(directory_db) as d:
        st = d.stats()
        assert st["total"] == 6
        assert st["live"] == 4
        assert st["dead"] == 1
        assert st["pending"] == 1
        assert st["ashby:live"] == 2


def test_directory_close_is_idempotent(directory_db):
    d = AtsDirectory(directory_db, snapshot_path=directory_db.parent / "none.json")
    assert d.available and d.mode == "db"
    d.close()
    d.close()
    assert not d.available


# ── snapshot fallback backend (fresh-sandbox cold start) ────────────────────

@pytest.fixture
def snapshot_file(tmp_path, directory_db):
    """Export-style snapshot built from the fixture DB's live rows."""
    import sqlite3 as sq
    conn = sq.connect(directory_db)
    rows = conn.execute(
        "SELECT ats, slug, company, job_count FROM directory "
        "WHERE status = 'live'").fetchall()
    conn.close()
    snap = {"exported_at": "2026-08-29T00:00:00+00:00",
            "boards": [{"platform": a, "slug": s, "company": c or "",
                        "job_count": n,
                        "board_url": board_url(a, s) or ""}
                       for a, s, c, n in rows]}
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(snap))
    return p


def test_snapshot_backend_resolves_when_db_missing(snapshot_file, tmp_path):
    d = AtsDirectory(tmp_path / "nope.db", snapshot_path=snapshot_file)
    assert d.available and d.mode == "snapshot"
    boards = d.resolve("Linear Labs, Inc.")
    assert [(b.platform, b.slug) for b in boards] == [("ashby", "linear"),
                                                      ("greenhouse", "linear")]
    assert boards[0].job_count == 135
    # exact-slug + learned-name lookups work too
    b = d.resolve_slug("lever", "stripe")
    assert b is not None and b.slug == "stripe"
    assert [(b.platform, b.slug) for b in d.resolve("Stripe")] == [("lever", "stripe")]
    st = d.stats()
    assert st["live"] == 4
    d.close()


def test_db_backend_preferred_over_snapshot(snapshot_file, directory_db):
    d = AtsDirectory(directory_db, snapshot_path=snapshot_file)
    assert d.mode == "db"          # snapshot ignored when the DB connects
    d.close()


def test_snapshot_dead_rows_not_exported(snapshot_file, tmp_path):
    d = AtsDirectory(tmp_path / "nope.db", snapshot_path=snapshot_file)
    assert d.resolve("Deadco") == []       # dead/pending never in the snapshot
    d.close()
