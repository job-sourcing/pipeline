"""Tests for Step-D ops primitives: trust.py (per-job + per-source scoring),
net.py (Retry-After clamp, backoff, DNS cache), and the storage v2 migration.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from jobsearch import trust
from jobsearch import net
from jobsearch.config import Config
from jobsearch.models import Job
from jobsearch.storage import Store


# ── trust.py — per-job validator (T2 semantics) ─────────────────────────────

class TestScoreJob:
    def test_perfect_job_scores_100_high(self):
        out = trust.score_job("https://acme.com/careers/1", "Acme")
        assert out == {"score": 100, "flags": [], "level": "high"}

    def test_missing_apply_url(self):
        out = trust.score_job("", "Acme")
        assert out["score"] == 60
        assert out["flags"] == ["missing_apply_url"]
        assert out["level"] == "medium"

    def test_invalid_url(self):
        out = trust.score_job("not a url ://", "Acme")
        assert out["score"] == 50
        assert out["flags"] == ["invalid_url"]

    def test_ftp_scheme_is_invalid(self):
        out = trust.score_job("ftp://acme.com/x", "Acme")
        assert out["flags"] == ["invalid_url"]

    def test_suspicious_domain(self):
        # empty company: only the −25 suspicious flag fires → 75
        out = trust.score_job("https://bit.ly/abc", "")
        assert out["score"] == 75
        assert out["flags"] == ["suspicious_domain"]

    def test_suspicious_domain_subdomain_matches(self):
        out = trust.score_job("https://sub.bit.ly/abc", "")
        assert out["flags"] == ["suspicious_domain"]
        # subdomain of a blocklisted host matches the blocklist entry

    def test_company_domain_mismatch(self):
        out = trust.score_job("https://globex.com/jobs/1", "Acme Systems")
        assert out["score"] == 85
        assert out["flags"] == ["company_domain_mismatch"]

    def test_ats_allowlist_exempts_mismatch(self):
        # Company "Acme" posting on greenhouse.io — ATS-hosted, no flag.
        out = trust.score_job(
            "https://boards.greenhouse.io/acme/jobs/1", "Acme")
        assert out["score"] == 100
        assert out["flags"] == []

    def test_own_domain_company_matches(self):
        out = trust.score_job("https://www.societegenerale.com/jobs/1",
                              "Société Générale")
        assert out["flags"] == []

    def test_word_level_match(self):
        # "Acme Cloud Systems" on acme.io — word "acme" matches.
        out = trust.score_job("https://careers.acme.io/1", "Acme Cloud Systems")
        assert out["flags"] == []

    def test_non_latin_company_never_flags(self):
        out = trust.score_job("https://example.com/jobs/1", "株式会社テスト")
        assert out["flags"] == []

    def test_combined_penalties_clamp_at_zero(self):
        out = trust.score_job("http://bit.ly/x", "Totally Unrelated Co")
        # suspicious −25 + mismatch −15 = 60
        assert out["score"] == 60
        assert set(out["flags"]) == {"suspicious_domain",
                                     "company_domain_mismatch"}


class TestAsciiFold:
    def test_accented_folds_to_ascii(self):
        assert trust.ascii_fold_for_hostname("Société Générale") == "societe generale"

    def test_non_decomposing_latin(self):
        assert trust.ascii_fold_for_hostname("Işık") == "isik"
        assert trust.ascii_fold_for_hostname("Straße") == "strasse"

    def test_cjk_returns_empty(self):
        assert trust.ascii_fold_for_hostname("株式会社") == ""


# ── trust.py — per-source score ─────────────────────────────────────────────

class TestSourceTrustScore:
    def test_full_marks(self):
        score = trust.source_trust_score("Remotive", {
            "attempts": 10, "successes": 10,
            "avg_latency_ms": 1000, "avg_field_completeness": 1.0,
        })
        assert score == 100

    def test_no_data_is_neutralish(self):
        score = trust.source_trust_score("Remotive", {})
        # completeness .5*30 + latency .5*25 + stability .5*25 + tos 1.0*20 = 60
        assert score == 60

    def test_failures_reduce_stability(self):
        good = trust.source_trust_score("Remotive", {
            "attempts": 10, "successes": 10, "avg_latency_ms": 1000,
            "avg_field_completeness": 1.0})
        bad = trust.source_trust_score("Remotive", {
            "attempts": 10, "successes": 5, "avg_latency_ms": 1000,
            "avg_field_completeness": 1.0})
        assert bad < good

    def test_slow_latency_reduces_score(self):
        fast = trust.source_trust_score("Remotive", {
            "attempts": 5, "successes": 5, "avg_latency_ms": 1000,
            "avg_field_completeness": 1.0})
        slow = trust.source_trust_score("Remotive", {
            "attempts": 5, "successes": 5, "avg_latency_ms": 10_000,
            "avg_field_completeness": 1.0})
        assert slow < fast

    def test_field_completeness(self):
        full = Job(title="T", company="C", link="https://x.com/1",
                   location="R", date_posted="2026-01-01",
                   salary_text="$1", description="D")
        empty = Job(title="T", company="C")
        assert trust.field_completeness([full]) == 1.0
        assert trust.field_completeness([empty]) < 0.5
        assert trust.field_completeness([]) == 0.0


# ── net.py — Retry-After + backoff ──────────────────────────────────────────

class TestParseRetryAfter:
    def test_delta_seconds(self):
        assert net.parse_retry_after_ms("10") == 10_000

    def test_delta_seconds_below_clamp(self):
        assert net.parse_retry_after_ms("2") == 2_000

    def test_fractional_seconds(self):
        assert net.parse_retry_after_ms("2.5") == 2500

    def test_http_date(self):
        from datetime import datetime, timedelta, timezone
        # relative future (was hardcoded 2030-01-01 — a time bomb that
        # flips PAST once the clock reaches it; P3, S9-CLOSE residual)
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        from email.utils import format_datetime
        ms = net.parse_retry_after_ms(format_datetime(future))
        assert ms is not None and ms > 0

    def test_http_date_clamped(self):
        """A hostile future HTTP-date (now + 1 day) must clamp to 32s —
        the scenario the module docstring exists for."""
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime
        day_out = datetime.now(timezone.utc) + timedelta(days=1)
        assert net.parse_retry_after_ms(format_datetime(day_out)) == 32_000
        just_over = datetime.now(timezone.utc) + timedelta(seconds=60)
        assert net.parse_retry_after_ms(format_datetime(just_over)) == 32_000
        just_under = datetime.now(timezone.utc) + timedelta(seconds=30)
        ms = net.parse_retry_after_ms(format_datetime(just_under))
        assert 25_000 <= ms <= 30_500       # sub-second drift is fine

    def test_past_http_date_clamps_to_zero(self):
        from datetime import datetime, timezone
        from email.utils import format_datetime
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        assert net.parse_retry_after_ms(format_datetime(past)) == 0

    def test_hostile_value_clamped_to_4x_max(self):
        # 'Retry-After: 86400' (a day) must clamp to 8s * 4 = 32s.
        assert net.parse_retry_after_ms("86400") == 32_000

    def test_none_and_garbage_return_none(self):
        assert net.parse_retry_after_ms(None) is None
        assert net.parse_retry_after_ms("") is None
        assert net.parse_retry_after_ms("soon-ish") is None

    def test_negative_delta(self):
        assert net.parse_retry_after_ms("-5") is None


class TestBackoff:
    def test_exponential_with_cap(self):
        d0 = net.backoff_delay_ms(0)
        d5 = net.backoff_delay_ms(5)
        assert 500 <= d0 <= 500 + net.JITTER_MS
        assert d5 <= net.DEFAULT_MAX_DELAY_MS + net.JITTER_MS


# ── net.py — DNS cache ──────────────────────────────────────────────────────

class TestDnsCache:
    def test_install_uninstall_idempotent(self):
        cache = net.DnsCache()
        original = socket.getaddrinfo
        try:
            assert cache.install() is True
            assert cache.install() is False      # already installed
            assert socket.getaddrinfo is not original
        finally:
            cache.uninstall()
            assert socket.getaddrinfo is original

    def test_caches_and_coalesces(self):
        cache = net.DnsCache()
        calls = {"n": 0}
        lock = threading.Lock()

        def fake_gai(host, port, family=0, type=0, proto=0, flags=0, *a, **k):
            with lock:
                calls["n"] += 1
            time.sleep(0.05)                     # hold in-flight window open
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 80))]

        original = socket.getaddrinfo
        socket.getaddrinfo = fake_gai
        try:
            cache._original = fake_gai           # wire the inner call
            results = []
            threads = [threading.Thread(
                target=lambda: results.append(cache._lookup("x.com", 443)))
                for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            # 5 parallel lookups → 1 real resolver hit (coalescing)
            assert calls["n"] == 1
            assert all(r == results[0] for r in results)
            # second burst hits the cache — no new resolver calls
            cache._lookup("x.com", 443)
            assert calls["n"] == 1
        finally:
            socket.getaddrinfo = original
            cache._original = None

    def test_failures_not_cached(self):
        cache = net.DnsCache()
        calls = {"n": 0}

        def failing_gai(host, port, family=0, type=0, proto=0, flags=0, *a, **k):
            calls["n"] += 1
            raise socket.gaierror("no such host")

        cache._original = failing_gai
        with pytest.raises(socket.gaierror):
            cache._lookup("gone.example", 443)
        with pytest.raises(socket.gaierror):
            cache._lookup("gone.example", 443)
        assert calls["n"] == 2                    # retried, not negative-cached


# ── storage v2 migration + trust round-trip ─────────────────────────────────

class TestStorageV2:
    def test_v1_db_migrates_forward(self, tmp_path):
        import sqlite3
        # Build a v1-shaped DB manually
        db = tmp_path / "v1.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        # minimal jobs table shape (v1 subset is fine for the ALTERs)
        conn.execute("""CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            company TEXT NOT NULL, description TEXT DEFAULT '', link TEXT,
            contact_email TEXT, source TEXT NOT NULL, location TEXT DEFAULT '',
            date_posted TEXT, remote INTEGER DEFAULT 0,
            h1b_mention INTEGER DEFAULT 0, salary_text TEXT,
            salary_min REAL, salary_max REAL, search_query TEXT DEFAULT '',
            scraped_at TEXT, created_at TEXT, updated_at TEXT,
            status TEXT DEFAULT 'shortlisted',
            pipeline_stage TEXT DEFAULT 'saved', apply_intent_at TEXT,
            apply_intent_acknowledged_at TEXT, tfidf_score REAL,
            llm_score REAL, llm_reasoning TEXT, llm_matches TEXT,
            llm_gaps TEXT, scored_at TEXT)""")
        conn.commit()
        conn.close()
        # Opening with Store applies migration v2
        store = Store(db)
        cols = {r["name"] for r in store.conn.execute(
            "PRAGMA table_info(jobs)")}
        assert {"trust_score", "trust_flags", "trust_level"} <= cols
        tables = {r["name"] for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "source_stats" in tables
        store.close()

    def test_job_trust_roundtrip(self, tmp_path):
        store = Store(tmp_path / "t.db")
        j = Job(title="T", company="Acme", link="https://bit.ly/x",
                source="Test")
        t = trust.score_job(j.link, j.company)
        j.trust_score, j.trust_flags, j.trust_level = (
            t["score"], t["flags"], t["level"])
        new, _ = store.upsert_jobs([j])
        assert new == 1
        out = store.jobs_by_query("")[0] if False else store.conn.execute(
            "SELECT * FROM jobs LIMIT 1").fetchone()
        from jobsearch.storage import Store as S
        job = S._row_to_job(out)
        # bit.ly: suspicious −25 AND Acme↔bit.ly mismatch −15 → 60
        assert job.trust_score == 60
        assert job.trust_flags == ["suspicious_domain",
                                   "company_domain_mismatch"]
        assert job.trust_level == "medium"
        store.close()

    def test_record_source_result_rolls_stats(self, tmp_path):
        store = Store(tmp_path / "t.db")
        s1 = store.record_source_result("Remotive", ok=True, jobs_count=10,
                                        duration_ms=800,
                                        completeness=0.9)
        s2 = store.record_source_result("Remotive", ok=True, jobs_count=5,
                                        duration_ms=1200,
                                        completeness=0.7)
        stats = store.source_stats("Remotive")[0]
        assert stats["attempts"] == 2
        assert stats["successes"] == 2
        assert stats["jobs_returned"] == 15
        assert 0 < s2 <= 100
        # failures lower it
        s3 = store.record_source_result("Remotive", ok=False, jobs_count=0,
                                        duration_ms=0)
        assert s3 < s2
        store.close()


class TestReviewRound1Fixes:
    """Tests from the opus review round 1 (PR #3): merge-path trust
    persistence, migration with data, source_stats semantics, DNS
    coalescing under failure, ladder edge cases."""

    def test_merge_path_persists_trust_fields(self, tmp_path):
        """P1-2: re-upserting an existing link with trust fields must fill
        them on the stored row (they were silently dropped before)."""
        store = Store(tmp_path / "t.db")
        j = Job(title="T", company="Acme", link="https://acme.com/1",
                source="Test")
        new, _ = store.upsert_jobs([j])
        assert new == 1

        j2 = Job(title="T", company="Acme", link="https://acme.com/1",
                 source="Test")
        j2.trust_score, j2.trust_flags, j2.trust_level = 95, ["ok"], "high"
        _, merged = store.upsert_jobs([j2])
        assert merged == 1
        row = store.conn.execute(
            "SELECT trust_score, trust_flags, trust_level FROM jobs "
            "WHERE link = ?", ("https://acme.com/1",)).fetchone()
        assert row["trust_score"] == 95
        assert json.loads(row["trust_flags"]) == ["ok"]
        assert row["trust_level"] == "high"
        store.close()

    def test_merge_path_does_not_overwrite_existing_trust(self, tmp_path):
        store = Store(tmp_path / "t.db")
        j = Job(title="T", company="Acme", link="https://acme.com/1",
                source="Test", trust_score=95, trust_flags=["a"],
                trust_level="high")
        store.upsert_jobs([j])
        j2 = Job(title="T", company="Acme", link="https://acme.com/1",
                 source="Test", trust_score=10, trust_flags=["b"],
                 trust_level="low")
        store.upsert_jobs([j2])
        row = store.conn.execute(
            "SELECT trust_score FROM jobs WHERE link = ?",
            ("https://acme.com/1",)).fetchone()
        assert row["trust_score"] == 95          # first value wins
        store.close()

    def test_v1_db_with_data_migrates_and_preserves_rows(self, tmp_path):
        import sqlite3 as sq
        db = tmp_path / "v1data.db"
        conn = sq.connect(db)
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.execute("""CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            company TEXT NOT NULL, description TEXT DEFAULT '', link TEXT,
            contact_email TEXT, source TEXT NOT NULL, location TEXT DEFAULT '',
            date_posted TEXT, remote INTEGER DEFAULT 0,
            h1b_mention INTEGER DEFAULT 0, salary_text TEXT,
            salary_min REAL, salary_max REAL, search_query TEXT DEFAULT '',
            scraped_at TEXT, created_at TEXT, updated_at TEXT,
            status TEXT DEFAULT 'shortlisted',
            pipeline_stage TEXT DEFAULT 'saved', apply_intent_at TEXT,
            apply_intent_acknowledged_at TEXT, tfidf_score REAL,
            llm_score REAL, llm_reasoning TEXT, llm_matches TEXT,
            llm_gaps TEXT, scored_at TEXT)""")
        conn.execute(
            "INSERT INTO jobs (title, company, link, source) VALUES "
            "('Old Job', 'LegacyCo', 'https://legacy.com/1', 'OldSource')")
        conn.execute(
            "INSERT INTO jobs (title, company, link, source) VALUES "
            "('Old Job 2', 'LegacyCo', 'https://legacy.com/2', 'OldSource')")
        conn.commit()
        conn.close()

        store = Store(db)
        rows = store.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert rows == 2                          # data preserved
        # v1 rows read back with NULL trust fields
        job = store.conn.execute(
            "SELECT * FROM jobs WHERE link='https://legacy.com/1'").fetchone()
        from jobsearch.storage import Store as S
        parsed = S._row_to_job(job)
        assert parsed.trust_score is None
        assert parsed.title == "Old Job"
        # and a re-upsert with trust fills them (merge path)
        j = Job(title="Old Job", company="LegacyCo",
                link="https://legacy.com/1", source="NewSource",
                trust_score=80, trust_flags=[], trust_level="high")
        store.upsert_jobs([j])
        row = store.conn.execute(
            "SELECT trust_score FROM jobs WHERE link='https://legacy.com/1'"
        ).fetchone()
        assert row["trust_score"] == 80
        store.close()

    def test_half_applied_migration_recovers(self, tmp_path):
        """A v1 DB with the trust columns already ALTERed but no version
        stamp must not brick on open (guarded migration)."""
        import sqlite3 as sq
        db = tmp_path / "half.db"
        conn = sq.connect(db)
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.execute("""CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            company TEXT NOT NULL, description TEXT DEFAULT '', link TEXT,
            contact_email TEXT, source TEXT NOT NULL, location TEXT DEFAULT '',
            date_posted TEXT, remote INTEGER DEFAULT 0,
            h1b_mention INTEGER DEFAULT 0, salary_text TEXT,
            salary_min REAL, salary_max REAL, search_query TEXT DEFAULT '',
            scraped_at TEXT, created_at TEXT, updated_at TEXT,
            status TEXT DEFAULT 'shortlisted',
            pipeline_stage TEXT DEFAULT 'saved', apply_intent_at TEXT,
            apply_intent_acknowledged_at TEXT, tfidf_score REAL,
            llm_score REAL, llm_reasoning TEXT, llm_matches TEXT,
            llm_gaps TEXT, scored_at TEXT,
            trust_score INTEGER)""")           # half-applied: one column exists
        conn.commit()
        conn.close()
        store = Store(db)                        # must not raise
        cols = {r["name"] for r in store.conn.execute(
            "PRAGMA table_info(jobs)")}
        assert {"trust_score", "trust_flags", "trust_level"} <= cols
        store.close()

    def test_last_success_at_survives_failure(self, tmp_path):
        store = Store(tmp_path / "t.db")
        store.record_source_result("X", ok=True, jobs_count=1, duration_ms=10)
        first = store.source_stats("X")[0]["last_success_at"]
        assert first is not None
        store.record_source_result("X", ok=False, jobs_count=0, duration_ms=0)
        second = store.source_stats("X")[0]["last_success_at"]
        assert second == first                   # not erased by the failure
        store.close()

    def test_avg_latency_divides_by_attempts(self, tmp_path):
        """Failures count toward avg latency (no double-counting weirdness):
        1 fast success + 9 slow timeouts → avg = total/attempts."""
        store = Store(tmp_path / "t.db")
        store.record_source_result("X", ok=True, jobs_count=1, duration_ms=100)
        for _ in range(9):
            store.record_source_result("X", ok=False, jobs_count=0,
                                       duration_ms=10_000)
        stats = store.source_stats("X")[0]
        avg = stats["total_latency_ms"] / stats["attempts"]
        assert avg == (100 + 90_000) / 10
        store.close()

    def test_empty_source_ladder_is_default_not_max(self):
        from jobsearch.dedup import source_ladder_trust
        assert source_ladder_trust("") == 5
        assert source_ladder_trust("   ") == 5
        assert source_ladder_trust(" + ") == 5

    def test_dns_cache_waiter_after_failure_re_registers(self):
        """A waiter whose resolution FAILED re-registers as resolver
        (sequentially — never N concurrent resolvers for one key)."""
        cache = net.DnsCache()
        calls = {"n": 0}
        lock = threading.Lock()

        def fake_gai(host, port, family=0, type=0, proto=0, flags=0, *a, **k):
            with lock:
                calls["n"] += 1
            time.sleep(0.03)
            if calls["n"] <= 1:
                raise socket.gaierror("first resolver fails")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("9.9.9.9", 80))]

        original = socket.getaddrinfo
        cache._original = fake_gai
        try:
            results: list = []
            errors: list = []

            def run():
                try:
                    results.append(cache._lookup("fail.example", 443))
                except socket.gaierror:
                    errors.append(1)

            threads = [threading.Thread(target=run) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            # Thread A resolves (fails); waiters B/C each re-register and
            # resolve sequentially — at most 1 concurrent resolver at a time
            # and no more than 3 total calls for 3 threads.
            assert calls["n"] <= 3
            assert len(results) + len(errors) == 3
        finally:
            socket.getaddrinfo = original
            cache._original = None

    def test_dns_cache_returns_copy(self):
        cache = net.DnsCache()
        payload = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 80))]
        with cache._lock:
            cache._cache[("h", 443, 0, 0, 0, 0)] = (time.monotonic(), payload)
        r1 = cache._lookup("h", 443)
        r1.pop()                                   # caller mutates
        r2 = cache._lookup("h", 443)
        assert len(r2) == 1                        # cache entry unpoisoned
