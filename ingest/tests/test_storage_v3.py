"""Storage v3 (Sprint 3): categorization columns migration, ops-signal
persistence, JSONL export, and the aggregator's categorization enrichment."""
from __future__ import annotations

import json
import sqlite3

import pytest

from jobsearch.models import Job
from jobsearch.storage import SCHEMA_VERSION, Store, connect, init_db

from conftest import make_job


# ── migration ──────────────────────────────────────────────────────────────

def test_schema_version_bumped():
    assert SCHEMA_VERSION == 5


def test_fresh_db_has_v3_and_v4_columns(store):
    cols = {r["name"] for r in store.conn.execute("PRAGMA table_info(jobs)")}
    assert {"tier", "skills", "work_mode", "ats_platform",
            "ghost_candidate", "repost_count", "sector"} <= cols


def test_v2_db_migrates_to_v3(db_path):
    """A DB created at v2 (trust columns, no categorization) migrates
    forward in place — existing rows survive with NULL categorization."""
    conn = connect(db_path)
    init_db(conn)                       # creates at current version
    # regress it to v2: drop the v3+v4 columns + version rows. Indexes that
    # reference the dropped columns must go first (SQLite refuses
    # DROP COLUMN while an index depends on it).
    for idx in ("idx_jobs_tier", "idx_jobs_ghost", "idx_jobs_sector"):
        conn.execute(f"DROP INDEX IF EXISTS {idx}")
    for col in ("tier", "skills", "work_mode", "ats_platform",
                "ghost_candidate", "repost_count", "sector"):
        conn.execute(f"ALTER TABLE jobs DROP COLUMN {col}")
    conn.execute("DELETE FROM schema_version WHERE version >= 3")
    conn.commit()
    conn.close()

    store = Store(db_path)              # open → applies migrations 3+4
    cols = {r["name"] for r in store.conn.execute("PRAGMA table_info(jobs)")}
    assert {"tier", "skills", "work_mode", "ats_platform",
            "ghost_candidate", "repost_count", "sector"} <= cols
    version = store.conn.execute(
        "SELECT MAX(version) v FROM schema_version").fetchone()["v"]
    assert version == 5
    store.close()


# ── upsert round-trip with categorization fields ───────────────────────────

def test_upsert_persists_categorization(store):
    job = make_job(
        title="Senior Kubernetes Platform Engineer",
        link="https://boards.greenhouse.io/acme/jobs/1",
        tier="senior",
        skills=["Kubernetes", "Terraform"],
        work_mode="remote",
        ats_platform="greenhouse",
    )
    new, merged = store.upsert_jobs([job])
    assert new == 1 and merged == 0
    stored = store.jobs_by_query(job.search_query)
    # make_job has search_query="" — query by link instead
    row = store.conn.execute("SELECT * FROM jobs WHERE link = ?",
                             (job.link,)).fetchone()
    assert row["tier"] == "senior"
    assert json.loads(row["skills"]) == ["Kubernetes", "Terraform"]
    assert row["work_mode"] == "remote"
    assert row["ats_platform"] == "greenhouse"


def test_upsert_merge_fills_null_categorization_only(store):
    """A v2-era row (categorization NULL) gains its tier on re-fetch; an
    already-categorized row is never overwritten (fill-only policy)."""
    v2_row = make_job(title="Backend Engineer",
                      link="https://example.com/j/9")
    store.upsert_jobs([v2_row])
    # simulate v2-era storage: clear the categorization columns
    store.conn.execute(
        "UPDATE jobs SET tier = NULL, skills = NULL, work_mode = NULL, "
        "ats_platform = NULL WHERE link = ?", (v2_row.link,))
    store.conn.commit()

    refetch = make_job(title="Backend Engineer",
                       link="https://example.com/j/9",
                       tier="mid", skills=["Python"], work_mode="hybrid",
                       ats_platform=None)
    new, merged = store.upsert_jobs([refetch])
    assert merged == 1
    row = store.conn.execute("SELECT * FROM jobs WHERE link = ?",
                             (v2_row.link,)).fetchone()
    assert row["tier"] == "mid"
    assert json.loads(row["skills"]) == ["Python"]
    assert row["work_mode"] == "hybrid"

    # now a DIFFERENT categorization must NOT overwrite
    refetch2 = make_job(title="Backend Engineer",
                        link="https://example.com/j/9",
                        tier="senior", skills=["Go"], work_mode="remote")
    store.upsert_jobs([refetch2])
    row = store.conn.execute("SELECT * FROM jobs WHERE link = ?",
                             (v2_row.link,)).fetchone()
    assert row["tier"] == "mid"                      # first value wins
    assert json.loads(row["skills"]) == ["Python"]


def test_row_to_job_parses_skills_json(store):
    store.upsert_jobs([make_job(link="https://example.com/j/10",
                                skills=["AWS", "Go"])])
    row = store.conn.execute(
        "SELECT * FROM jobs WHERE link = 'https://example.com/j/10'").fetchone()
    job = store._row_to_job(row)
    assert job.skills == ["AWS", "Go"]
    assert isinstance(job.skills, list)


# ── ops signals ────────────────────────────────────────────────────────────

def test_update_ops_signals(store):
    store.upsert_jobs([make_job(link="https://example.com/j/20")])
    job_id = store.conn.execute(
        "SELECT id FROM jobs WHERE link = 'https://example.com/j/20'"
    ).fetchone()["id"]
    store.update_ops_signals(job_id, ghost_candidate=1, repost_count=3)
    row = store.conn.execute("SELECT * FROM jobs WHERE id = ?",
                             (job_id,)).fetchone()
    assert row["ghost_candidate"] == 1
    assert row["repost_count"] == 3

    # flag-only update: passing None leaves the other column untouched
    store.update_ops_signals(job_id, ghost_candidate=0, repost_count=None)
    row = store.conn.execute("SELECT * FROM jobs WHERE id = ?",
                             (job_id,)).fetchone()
    assert row["ghost_candidate"] == 0
    assert row["repost_count"] == 3


# ── sector (v4 — LLM pass, prompt v3) ────────────────────────────────────────

def test_update_scores_persists_sector(store):
    store.upsert_jobs([make_job(link="https://example.com/j/30")])
    job_id = store.conn.execute(
        "SELECT id FROM jobs WHERE link = 'https://example.com/j/30'"
    ).fetchone()["id"]
    store.update_scores(job_id, llm={"score": 80.0, "reasoning": "ok",
                                     "top_matches": [], "gaps": [],
                                     "sector": "fintech"})
    row = store.conn.execute("SELECT * FROM jobs WHERE id = ?",
                             (job_id,)).fetchone()
    assert row["sector"] == "fintech"


def test_update_scores_sector_never_blanked(store):
    """A later scoring pass WITHOUT a sector (older cached result, or the
    model omitted it) must not blank an already-inferred sector."""
    store.upsert_jobs([make_job(link="https://example.com/j/31")])
    job_id = store.conn.execute(
        "SELECT id FROM jobs WHERE link = 'https://example.com/j/31'"
    ).fetchone()["id"]
    store.update_scores(job_id, llm={"score": 80.0, "reasoning": "ok",
                                     "top_matches": [], "gaps": [],
                                     "sector": "ai-ml"})
    store.update_scores(job_id, llm={"score": 82.0, "reasoning": "rescored",
                                     "top_matches": [], "gaps": []})
    row = store.conn.execute("SELECT * FROM jobs WHERE id = ?",
                             (job_id,)).fetchone()
    assert row["sector"] == "ai-ml"    # survived the sector-less rescore
    assert row["llm_score"] == 82.0     # the rescore itself landed


# ── JSONL export ───────────────────────────────────────────────────────────

def test_export_jsonl_by_query(store, tmp_path):
    jobs = [
        make_job(title=f"Engineer {i}", link=f"https://example.com/j/{i}",
                 search_query="python dev", skills=["Python"])
        for i in range(3)
    ]
    store.upsert_jobs(jobs)
    out = tmp_path / "export.jsonl"
    n = store.export_jsonl(out, query="python dev")
    assert n == 3
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["title"] == "Engineer 0"
    assert first["skills"] == ["Python"]      # real JSON array, not a string
    assert first["source"] == "Remotive"
    # Wave-R R2: the default export IS the facet-01 contract —
    # stable job_id + the consumer's field names are pinned here too.
    assert first["job_id"]                    # never empty → no 'line-N' ids
    assert len({json.loads(x)["job_id"] for x in lines}) == 3
    assert "experience_level" in first and "work_type" in first
    assert "remote_allowed" in first and "listed_time" in first


def test_export_jsonl_whole_db(store, tmp_path):
    store.upsert_jobs([make_job(link=f"https://example.com/x/{i}",
                                search_query=f"q{i}") for i in range(4)])
    out = tmp_path / "all.jsonl"
    n = store.export_jsonl(out)
    assert n == 4
    assert out.exists()


def test_export_jsonl_creates_parent_dirs(store, tmp_path):
    store.upsert_jobs([make_job()])
    out = tmp_path / "nested" / "dir" / "e.jsonl"
    assert store.export_jsonl(out) == 1
    assert out.exists()
