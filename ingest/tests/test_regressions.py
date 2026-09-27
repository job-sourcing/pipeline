"""Regression tests for the CR-1 code-review fixes (build/week1).

Each test pins one reviewed behavior so the fix cannot silently regress:
  1. HTML-entity decoding in RemoteOK/Jobicy titles+companies — live evidence:
     /tmp/live_test.db row 14 shipped company "JACK &amp; JONES" (CR-1-TESTS).
  2. clean_html() stripping of Remotive/Arbeitnow descriptions — the real APIs
     return raw HTML descriptions (live DB rows 1-10), fixtures were plain text.
  3. _fill() single-pass placeholder fill (CR-1-CODE F-01): the Python cache
     key must hash the same string score_jobs.mjs fillTemplate produces.
  4. node_unavailable SeamError (F-02): a missing node binary must not escape
     the SeamError taxonomy nor retry.
  5. Secondary retryable classification in LLMSeam.run (F-04): a transient
     message the runner missed ("API request failed with status 503") still
     gets the bounded retry even when the envelope says retryable:false.
  6. CLI SeamError -> TF-IDF-only degrade (D3): the run never aborts.
  7. Per-source duration_ms measured INSIDE the worker thread (F-06) —
     previously structurally ~0 because as_completed() yields post-completion.
"""
from __future__ import annotations

import json
import re
import time

import pytest
from click.testing import CliRunner

import jobsearch.cli as cli_module
from jobsearch.cli import search_jobs
from jobsearch.config import Config
from jobsearch.llm import (
    DESCRIPTION_TRUNC,
    RESUME_TRUNC,
    LLMSeam,
    SeamError,
    _fill,
)
from jobsearch.models import Job, SourceResult
from jobsearch.scoring import rank_by_tfidf
from jobsearch.sources import REGISTRY, search_all_sources
from jobsearch.sources import arbeitnow, jobicy, remotive, remoteok
from jobsearch.storage import Store

from conftest import make_job


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(db_path=tmp_path / "t.db")


# ── 1. HTML-entity decoding (RemoteOK / Jobicy) ───────────────────────────

class TestEntityDecode:
    """Real boards send HTML entities; _clean must decode them (live evidence:
    RemoteOK returned company_name "JACK &amp; JONES" in the Week 1 live run)."""

    def test_remoteok_decodes_company_and_title(self, monkeypatch, cfg):
        payload = [
            {"legal": "We legally have to put this here", "count": 2},  # [0]
            {
                "slug": "senior-developer-jones",
                "position": "Senior Developer &#8212; Remote",
                "company_name": "JACK &amp; JONES",
                "description": "Senior python developer role.",
                "url": "https://remoteok.com/remote-jobs/senior-developer-jones",
                "location": "Remote",
                "date": 1787000000,
                "salary_range": None,
                "tags": ["python"],
            },
        ]
        monkeypatch.setattr(remoteok, "fetch_json", lambda *a, **k: payload)
        jobs = remoteok.fetch("python", cfg=cfg)
        assert len(jobs) == 1
        assert jobs[0].company == "JACK & JONES"          # &amp; decoded
        assert jobs[0].title == "Senior Developer \u2014 Remote"  # &#8212; decoded
        assert "amp;" not in jobs[0].company
        assert "#8212;" not in jobs[0].title

    def test_jobicy_decodes_company(self, monkeypatch, cfg):
        payload = {"jobs": [{
            "id": 211,
            "url": "https://jobicy.com/jobs/python-engineer-jones",
            "jobTitle": "Python Engineer",
            "companyName": "JACK &amp; JONES",
            "jobGeo": "Worldwide",
            "jobLevel": "Senior",
            "jobType": "Full-time",
            "jobExcerpt": "Remote python engineering role.",
            "pubDate": "2026-08-20",
            "salary": "",
        }]}
        monkeypatch.setattr(jobicy, "fetch_json", lambda *a, **k: payload)
        jobs = jobicy.fetch("python", cfg=cfg)
        assert len(jobs) == 1
        # desired post-fix behavior (what RemoteOK already does)
        assert jobs[0].company == "JACK & JONES"
        assert "amp;" not in jobs[0].company


# ── 2. HTML description stripping (Remotive / Arbeitnow) ──────────────────

# clean_html removes tags WITHOUT whitespace collapse: adjacent text runs
# concatenate directly (documented behavior, see W1-TESTS notes).
HTML_DESC = "<p>We need <b>Python</b></p><a href='x'>link</a>"
STRIPPED_DESC = "We need Pythonlink"


class TestHtmlDescriptionStripping:
    """Real Remotive/Arbeitnow payloads carry HTML descriptions; the clients
    must strip tags so raw markup never reaches TF-IDF or LLM prompts."""

    def test_remotive_strips_description_html(self, monkeypatch, cfg):
        payload = {"jobs": [{
            "id": "r1",
            "url": "https://remotive.com/remote-jobs/software-dev/python-eng",
            "title": "Python Backend Engineer",
            "company_name": "Acme Cloud",
            "publication_date": "2026-08-20T10:00:00",
            "salary": "",
            "description": HTML_DESC,
        }]}
        monkeypatch.setattr(remotive, "fetch_json", lambda *a, **k: payload)
        jobs = remotive.fetch("python", cfg=cfg)
        assert len(jobs) == 1
        assert jobs[0].description == STRIPPED_DESC
        assert "<" not in jobs[0].description
        assert ">" not in jobs[0].description
        assert "href" not in jobs[0].description   # attributes gone, not leaked

    def test_arbeitnow_strips_description_html(self, monkeypatch, cfg):
        payload = {"data": [{
            "slug": "python-developer-acme",
            "company_name": "Acme Cloud",
            "title": "Python Developer (f/m/d)",
            "description": HTML_DESC,
            "remote": True,
            "url": "https://www.arbeitnow.com/jobs/python-developer-acme",
            "tags": ["python"],
            "location": "Berlin",
            "created_at": 1787000000.0,
        }]}
        monkeypatch.setattr(arbeitnow, "fetch_json", lambda *a, **k: payload)
        jobs = arbeitnow.fetch("python", cfg=cfg)
        assert len(jobs) == 1
        assert jobs[0].description == STRIPPED_DESC
        assert "<" not in jobs[0].description
        assert ">" not in jobs[0].description


# ── 3. _fill parity with score_jobs.mjs fillTemplate (F-01) ───────────────

class TestFillParity:
    """Sequential .replace() double-injects {description}/{title} embedded in
    the resume text while Node's fillTemplate keeps them literal — the Python
    cache key then hashes a string that is NOT the prompt Node sends."""

    def test_literal_placeholders_in_resume_are_preserved(self):
        job = make_job(title="X", company="Y", description="Z")
        template = "R:{resume} T:{title} C:{company} D:{description}"
        resume = "Skills: {description} extraction, {title} matching"
        out = _fill(template, resume, job)
        # the resume section keeps its literal braces; template slots filled
        assert out == ("R:Skills: {description} extraction, {title} matching "
                       "T:X C:Y D:Z")

    def test_resume_truncated_at_4000(self):
        job = make_job(title="X", company="Y", description="Z")
        out = _fill("{resume}", "a" * 5000, job)
        assert RESUME_TRUNC == 4000            # pinned equal to score_jobs.mjs
        assert len(out) == 4000
        assert out == "a" * 4000

    def test_description_truncated_at_3000(self):
        job = make_job(title="X", company="Y", description="d" * 5000)
        out = _fill("{description}", "resume", job)
        assert DESCRIPTION_TRUNC == 3000       # pinned equal to score_jobs.mjs
        assert len(out) == 3000
        assert out == "d" * 3000

    def test_unknown_placeholder_left_verbatim(self):
        # fillTemplate only substitutes known keys; {salary} must survive.
        job = make_job(title="X", company="Y", description="Z")
        out = _fill("{resume}|{salary}|{title}", "R", job)
        assert out == "R|{salary}|X"


# ── 4. node_unavailable (F-02) ─────────────────────────────────────────────

class TestNodeUnavailable:
    """A missing node binary must surface as a non-retryable SeamError —
    previously a raw FileNotFoundError escaped the taxonomy and aborted runs
    despite the CLI's D3 degrade handler (which only catches SeamError)."""

    def test_missing_node_binary_is_node_unavailable_not_retried(self, tmp_path):
        cfg = Config(db_path=tmp_path / "t.db",
                     node_bin="/nonexistent/node-xyz",
                     llm_retry_backoff_s=0.01, llm_max_retries=2)
        store = Store(cfg.db_path)
        seam = LLMSeam(cfg, store)
        t0 = time.monotonic()
        with pytest.raises(SeamError) as excinfo:
            seam.run("score_jobs", {"resume": "r"}, prompt="p",
                     prompt_version="v")
        elapsed = time.monotonic() - t0
        assert excinfo.value.code == "node_unavailable"
        assert excinfo.value.retryable is False
        # not retried: exactly ONE llm_error row...
        errors = store.conn.execute(
            "SELECT error_class FROM telemetry WHERE event='llm_error'"
        ).fetchall()
        assert len(errors) == 1
        assert errors[0]["error_class"] == "node_unavailable"
        # ...and no backoff sleeps happened
        assert elapsed < 1.0
        store.close()


# ── 5. Secondary retryable classification in run() (F-04) ─────────────────

def _envelope_script(code: str, message: str, retryable: bool) -> str:
    """A standalone .mjs that emits ONE hand-crafted error envelope, bypassing
    runner.mjs's classifier — this isolates the PYTHON-side secondary check."""
    return (f"console.log(JSON.stringify({{ok: false, schema_version: 1, "
            f"error: {{code: {json.dumps(code)}, "
            f"message: {json.dumps(message)}, "
            f"retryable: {'true' if retryable else 'false'}}}}}));\n")


def _seam_with_envelope_error(tmp_path, code: str, message: str,
                              retryable: bool) -> tuple[LLMSeam, Store]:
    llm_dir = tmp_path / "llm"
    llm_dir.mkdir(exist_ok=True)
    (llm_dir / "fail.mjs").write_text(
        _envelope_script(code, message, retryable), encoding="utf-8")
    cfg = Config(db_path=tmp_path / "t.db", llm_dir=llm_dir, node_bin="node",
                 llm_retry_backoff_s=0.01, llm_max_retries=2)
    store = Store(cfg.db_path)
    return LLMSeam(cfg, store), store


class TestSecondaryRetryableClassification:
    """The runner's envelope is the primary classifier, but the SDK's actual
    transient format ("API request failed with status 503" — vendor
    dist/index.js) used to classify as fatal with ZERO retries."""

    def test_status_503_envelope_retried_via_secondary_regex(self, tmp_path):
        seam, store = _seam_with_envelope_error(
            tmp_path, "llm_fatal", "API request failed with status 503", False)
        with pytest.raises(SeamError) as excinfo:
            seam.run("fail", {}, prompt="p", prompt_version="v")
        # the original envelope code/message still propagate after the retries
        assert excinfo.value.code == "llm_fatal"
        # envelope said retryable:false; the secondary regex ("status 5xx")
        # overrides -> full bounded retry: 1 initial + 2 retries = 3 errors
        errors = store.conn.execute(
            "SELECT error_class FROM telemetry WHERE event='llm_error'"
        ).fetchall()
        assert len(errors) == 3
        assert all(r["error_class"] == "llm_fatal" for r in errors)
        store.close()

    def test_non_transient_status_400_not_retried(self, tmp_path):
        # negative control: 4xx is genuinely fatal -> exactly one attempt
        seam, store = _seam_with_envelope_error(
            tmp_path, "llm_fatal", "API request failed with status 400", False)
        with pytest.raises(SeamError) as excinfo:
            seam.run("fail", {}, prompt="p", prompt_version="v")
        assert excinfo.value.code == "llm_fatal"
        errors = store.conn.execute(
            "SELECT error_class FROM telemetry WHERE event='llm_error'"
        ).fetchall()
        assert len(errors) == 1
        store.close()


# ── 6. CLI SeamError -> TF-IDF-only degrade (D3, mutation-proof) ──────────

class TestCliDegradeOnSeamError:
    """The CLI tests previously faked score_pipeline to succeed, so removing
    the SeamError handler changed nothing observable. This test makes the
    degrade path load-bearing: exit 0, warning on stderr, report on stdout,
    finished runs row, zero LLM scores."""

    def test_seam_error_degrades_to_tfidf_report(self, tmp_path, monkeypatch):
        jobs = [
            Job(title="Senior Python Developer", company="Acme Cloud",
                description="Python FastAPI backend services.",
                link="https://ex.com/1", source="Remotive", remote=True),
            Job(title="Python Backend Engineer", company="BetaData",
                description="Django, Redis, PostgreSQL.",
                link="https://ex.com/2", source="Remotive"),
        ]

        def fake_search(keywords, location, sources, num, cfg):
            return [SourceResult(source="Remotive", jobs=jobs)]

        def failing_score_pipeline(jobs_arg, resume_text, cfg, store,
                                   seam=None, top_n=None):
            # realistic order: the TF-IDF stage persists first, THEN the LLM
            # seam raises (that is exactly where the real 429 storm hits).
            for job, score in rank_by_tfidf(jobs_arg, resume_text):
                job.tfidf_score = score
                if job.id is not None:
                    store.update_scores(job.id, tfidf=score)
            raise SeamError("llm_retryable", "rate limited 429 too many requests",
                            True)

        monkeypatch.setattr(cli_module, "search_all_sources", fake_search)
        monkeypatch.setattr(cli_module, "score_pipeline",
                            failing_score_pipeline)

        db = tmp_path / "cli" / "tracker.db"
        # Click < 8.2 needs mix_stderr=False to expose result.stderr;
        # Click >= 8.2 removed the kwarg (streams are always separate).
        try:
            runner = CliRunner(mix_stderr=False)
        except TypeError:
            runner = CliRunner()
        result = runner.invoke(search_jobs, [
            "python developer", "--db", str(db), "--sources", "Remotive",
        ])
        assert result.exit_code == 0, result.output

        # degrade warning on stderr, carrying the seam error code
        assert "degraded" in result.stderr
        assert "llm_retryable" in result.stderr

        # the report still ships on stdout: table, both jobs, T-suffix scores
        assert "RESULTS for 'python developer'" in result.stdout
        assert "TF-IDF ranked" in result.stdout
        assert "Senior Python Developer" in result.stdout
        assert "Python Backend Engineer" in result.stdout
        assert re.search(r"\d+\.\dT", result.stdout)
        assert "Legend:" in result.stdout

        # the runs row is finished and both jobs are persisted
        store = Store(db)
        run = store.conn.execute("SELECT * FROM runs").fetchone()
        assert run is not None
        assert run["finished_at"] is not None
        assert run["jobs_new"] == 2
        assert json.loads(run["sources_ok"]) == ["Remotive"]
        assert len(store.jobs_by_query("python developer")) == 2
        # ranking is TF-IDF only — no LLM scores were persisted
        assert store.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE llm_score IS NOT NULL"
        ).fetchone()[0] == 0
        store.close()


# ── 7. duration_ms measured inside the worker thread (F-06) ───────────────

class TestDurationMs:
    """duration_ms used to be timed around as_completed() — structurally ~0.
    It must now reflect the real fetch latency, recorded inside the worker."""

    def test_slow_fetches_record_real_duration(self, monkeypatch, cfg):
        def slow_fetch(keywords, location, num_results, cfg=None):
            time.sleep(0.05)
            return [make_job()]

        monkeypatch.setattr(REGISTRY["Remotive"], "fetch", slow_fetch)
        monkeypatch.setattr(REGISTRY["Arbeitnow"], "fetch", slow_fetch)
        results = search_all_sources("python", "Remote",
                                     ["Remotive", "Arbeitnow"], 5, cfg)
        assert len(results) == 2
        for r in results:
            assert r.ok
            assert r.duration_ms >= 40    # real ~50ms, not a ~0ms stub
