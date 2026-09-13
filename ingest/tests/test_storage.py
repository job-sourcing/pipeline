"""Storage layer: WAL, migrations, upsert semantics, cache, quarantine,
telemetry, runs. All DBs live in tmp_path."""
from __future__ import annotations

import sqlite3

import pytest

from jobsearch.storage import SCHEMA_VERSION, Store, cache_key, connect, init_db

from conftest import make_job


# ── connection & schema ───────────────────────────────────────────────────

def test_wal_mode_enabled(db_path):
    conn = connect(db_path)
    init_db(conn)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    conn.close()


def test_busy_timeout_pragma(db_path):
    conn = connect(db_path)
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    conn.close()


def test_init_db_creates_all_tables(db_path):
    conn = connect(db_path)
    init_db(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "llm_cache", "quarantine", "telemetry", "runs",
            "schema_version"} <= tables
    conn.close()


def test_init_db_idempotent(db_path):
    conn = connect(db_path)
    init_db(conn)
    before = sorted(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"))
    init_db(conn)  # second run must be a no-op, not an error
    init_db(conn)  # ... and a third
    after = sorted(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"))
    assert before == after
    # one row per applied migration version, never re-stamped
    n_versions = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert n_versions == SCHEMA_VERSION
    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] \
        == SCHEMA_VERSION
    conn.close()


def test_store_constructor_is_idempotent(db_path):
    Store(db_path).close()
    Store(db_path).close()  # re-opening an initialized DB must not fail


def test_readonly_connection_can_read(db_path):
    s = Store(db_path)
    s.upsert_jobs([make_job()])
    s.close()
    ro = connect(db_path, readonly=True)
    assert ro.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    ro.close()


# ── upsert ────────────────────────────────────────────────────────────────

def test_upsert_new_jobs(store):
    new, merged = store.upsert_jobs([
        make_job(link="https://ex.com/1"),
        make_job(title="Other", company="Beta", link="https://ex.com/2"),
    ])
    assert (new, merged) == (2, 0)
    assert store.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_upsert_requires_title_and_company(store):
    new, merged = store.upsert_jobs([
        make_job(title="", company="C"),        # no title -> skipped
        make_job(title="T", company=""),        # no company -> skipped
    ])
    assert (new, merged) == (0, 0)


def test_upsert_same_link_merges_not_duplicates(store):
    store.upsert_jobs([make_job(link="https://ex.com/1", description="")])
    new, merged = store.upsert_jobs([
        make_job(link="https://ex.com/1", description="full description",
                 location="Remote", contact_email="jobs@acme.io"),
    ])
    assert (new, merged) == (0, 1)
    row = store.conn.execute("SELECT * FROM jobs WHERE link='https://ex.com/1'").fetchone()
    assert row["description"] == "full description"  # filled (was empty)
    assert row["location"] == "Remote"
    assert row["contact_email"] == "jobs@acme.io"


def test_upsert_never_overwrites_existing_data(store):
    store.upsert_jobs([make_job(link="https://ex.com/1", description="original",
                                location="Berlin")])
    store.upsert_jobs([make_job(link="https://ex.com/1", description="DIFFERENT",
                                location="NOWHERE")])
    row = store.conn.execute("SELECT * FROM jobs WHERE link='https://ex.com/1'").fetchone()
    assert row["description"] == "original"   # never overwritten
    assert row["location"] == "Berlin"


def test_upsert_merge_fills_only_empty_fields(store):
    store.upsert_jobs([make_job(link="https://ex.com/1", location="Berlin",
                                description="")])
    store.upsert_jobs([make_job(link="https://ex.com/1", location="Remote",
                                description="desc")])
    row = store.conn.execute("SELECT * FROM jobs WHERE link='https://ex.com/1'").fetchone()
    assert row["location"] == "Berlin"   # kept
    assert row["description"] == "desc"  # filled


def test_upsert_merges_boolean_flags_upward_only(store):
    store.upsert_jobs([make_job(link="https://ex.com/1", h1b_mention=False)])
    store.upsert_jobs([make_job(link="https://ex.com/1", h1b_mention=True)])
    row = store.conn.execute("SELECT h1b_mention FROM jobs WHERE link='https://ex.com/1'").fetchone()
    assert row["h1b_mention"] == 1  # False -> True is a fill (0 -> 1)


def test_unique_link_index_rejects_duplicate_insert(db_path):
    conn = connect(db_path)
    init_db(conn)
    conn.execute("INSERT INTO jobs (title, company, source, link) "
                 "VALUES ('T', 'C', 'S', 'dup-link')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO jobs (title, company, source, link) "
                     "VALUES ('T2', 'C2', 'S', 'dup-link')")
    conn.close()


def test_empty_links_are_not_indexed(db_path):
    conn = connect(db_path)
    init_db(conn)
    for _ in range(2):  # empty link is allowed multiple times
        conn.execute("INSERT INTO jobs (title, company, source, link) "
                     "VALUES ('T', 'C', 'S', '')")
    for _ in range(2):  # NULL link too
        conn.execute("INSERT INTO jobs (title, company, source, link) "
                     "VALUES ('T', 'C', 'S', NULL)")
    conn.close()


def test_linkless_job_re_run_does_not_duplicate(store):
    """Audit P2-1 regression: upserting the same linkless job twice (the
    scheduler re-runs the same query every cycle) must merge into ONE row,
    not accumulate one row per cycle."""
    job = make_job(title="Ghost Link", company="NoLink Co", source="Glassdoor",
                   link="")
    new1, merged1 = store.upsert_jobs([job])
    assert (new1, merged1) == (1, 0)
    new2, merged2 = store.upsert_jobs([job])
    assert (new2, merged2) == (0, 1)
    count = store.conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE link = '' AND company = 'NoLink Co'"
    ).fetchone()[0]
    assert count == 1


def test_linkless_fingerprint_matches_on_source_title_company(store):
    """The fallback identity is (source, title, company) — a DIFFERENT linkless
    posting (different title) from the same source must still insert."""
    store.upsert_jobs([make_job(title="A", company="Co", source="S", link="")])
    new, _ = store.upsert_jobs(
        [make_job(title="B", company="Co", source="S", link="")])
    assert new == 1


def test_jobs_by_query_orders_llm_first(store):
    store.upsert_jobs([
        make_job(title="A", link="https://ex.com/1", search_query="q"),
        make_job(title="B", link="https://ex.com/2", search_query="q"),
        make_job(title="C", link="https://ex.com/3", search_query="q"),
    ])
    a_id = store.conn.execute(
        "SELECT id FROM jobs WHERE title='A'").fetchone()["id"]
    b_id = store.conn.execute(
        "SELECT id FROM jobs WHERE title='B'").fetchone()["id"]
    c_id = store.conn.execute(
        "SELECT id FROM jobs WHERE title='C'").fetchone()["id"]
    store.update_scores(a_id, tfidf=5.0)
    store.update_scores(b_id, tfidf=50.0)
    store.update_scores(c_id, tfidf=1.0, llm={"score": 10.0, "reasoning": "r",
                                              "top_matches": [], "gaps": []})
    jobs = store.jobs_by_query("q")
    # any LLM score outranks TF-IDF-only jobs; then TF-IDF desc; NULLs last
    assert [j.title for j in jobs] == ["C", "B", "A"]


def test_unscored_llm_returns_null_llm_ordered_by_tfidf(store):
    store.upsert_jobs([
        make_job(title="Low", link="https://ex.com/1", search_query="q"),
        make_job(title="High", link="https://ex.com/2", search_query="q"),
        make_job(title="Scored", link="https://ex.com/3", search_query="q"),
    ])
    ids = {t: store.conn.execute(
        "SELECT id FROM jobs WHERE title=?", (t,)).fetchone()["id"]
        for t in ("Low", "High", "Scored")}
    store.update_scores(ids["Low"], tfidf=1.0)
    store.update_scores(ids["High"], tfidf=99.0)
    store.update_scores(ids["Scored"], tfidf=100.0,
                        llm={"score": 1.0, "reasoning": "r",
                             "top_matches": [], "gaps": []})
    unscored = store.unscored_llm("q", 10)
    assert [j.title for j in unscored] == ["High", "Low"]
    assert all(j.llm_score is None for j in unscored)


def test_update_scores(store):
    store.upsert_jobs([make_job(link="https://ex.com/1")])
    job_id = store.conn.execute("SELECT id FROM jobs").fetchone()["id"]

    store.update_scores(job_id, tfidf=42.5)
    row = store.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["tfidf_score"] == 42.5
    assert row["llm_score"] is None

    store.update_scores(job_id, llm={"score": 88.0, "reasoning": "why",
                                     "top_matches": ["Python", "Go"],
                                     "gaps": ["AWS"]})
    row = store.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["tfidf_score"] == 42.5          # untouched
    assert row["llm_score"] == 88.0
    assert row["llm_reasoning"] == "why"
    import json
    assert json.loads(row["llm_matches"]) == ["Python", "Go"]
    assert json.loads(row["llm_gaps"]) == ["AWS"]
    assert row["scored_at"]


# ── cache ─────────────────────────────────────────────────────────────────

def test_cache_key_deterministic_and_version_sensitive():
    k1 = cache_key("prompt text", "v1")
    assert k1 == cache_key("prompt text", "v1")
    assert k1 != cache_key("prompt text", "v2")   # version changes key
    assert k1 != cache_key("other prompt", "v1")  # prompt changes key


def test_cache_put_get_roundtrip(store):
    key = cache_key("prompt", "v1")
    assert store.cache_get(key) is None
    store.cache_put(key, "v1", {"results": [1, 2, 3]})
    assert store.cache_get(key) == {"results": [1, 2, 3]}


def test_cache_version_different_key_is_a_miss(store):
    store.cache_put(cache_key("prompt", "v1"), "v1", {"a": 1})
    assert store.cache_get(cache_key("prompt", "v2")) is None


def test_cache_lookup_logs_telemetry(store):
    key = cache_key("prompt", "v1")
    store.cache_get(key)
    store.cache_put(key, "v1", {"a": 1})
    store.cache_get(key)
    events = [r["event"] for r in store.conn.execute(
        "SELECT event FROM telemetry ORDER BY id")]
    assert events == ["cache_miss", "cache_hit"]


# ── quarantine / telemetry / runs ─────────────────────────────────────────

def test_quarantine_row(store):
    store.quarantine("score_jobs", "2", '{"technical": 999}', "bad_dim_technical")
    row = store.conn.execute("SELECT * FROM quarantine").fetchone()
    assert row["script"] == "score_jobs"
    assert row["prompt_version"] == "2"
    assert row["raw_output"] == '{"technical": 999}'
    assert row["reason"] == "bad_dim_technical"
    assert row["created_at"]


def test_telemetry_row(store):
    store.log_telemetry("llm_ok", script="score_jobs", duration_ms=123,
                        cache_hit=False, detail="all good")
    row = store.conn.execute("SELECT * FROM telemetry").fetchone()
    assert row["event"] == "llm_ok"
    assert row["script"] == "score_jobs"
    assert row["duration_ms"] == 123
    assert row["cache_hit"] == 0
    assert row["error_class"] == ""
    assert row["detail"] == "all good"


def test_runs_lifecycle(store):
    run_id = store.start_run("search-jobs", "python developer")
    assert isinstance(run_id, int)
    row = store.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    assert row["started_at"]
    assert row["finished_at"] is None
    assert row["command"] == "search-jobs"
    assert row["query"] == "python developer"

    store.finish_run(run_id, sources_ok=["Remotive"],
                     sources_degraded=["RemoteOK"], jobs_found=10,
                     jobs_new=7, jobs_merged=3)
    row = store.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    assert row["finished_at"]
    assert row["jobs_found"] == 10
    assert row["jobs_new"] == 7
    assert row["jobs_merged"] == 3
    import json
    assert json.loads(row["sources_ok"]) == ["Remotive"]
    assert json.loads(row["sources_degraded"]) == ["RemoteOK"]
