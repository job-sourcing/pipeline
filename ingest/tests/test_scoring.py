"""Two-stage scoring pipeline (D8: RELATIVE top-N, never absolute cutoff)."""
from __future__ import annotations

import json

import pytest

from jobsearch.config import Config
from jobsearch.scoring import rank_by_tfidf, score_pipeline, tfidf_score, tokenize

from conftest import make_job

PYTHON_RESUME = (
    "Senior backend engineer with Python, FastAPI, PostgreSQL, Redis, "
    "Docker and AWS experience."
)


class FakeSeam:
    """Deterministic stand-in for LLMSeam.score_jobs."""

    def __init__(self):
        self.calls: list[list[int]] = []

    def score_jobs(self, jobs, resume_text, prompt=None):
        self.calls.append([j.id for j in jobs])
        return [{
            "id": j.id, "ok": True,
            "dims": {"technical": 80, "experience": 80, "behavioral": 80,
                     "career": 80},
            "overall": 80.0, "band": "Strong Fit",
            "reasoning": "fake reasoning",
            "top_matches": ["Python"], "gaps": ["Kubernetes"],
        } for j in jobs]


# ── tokenize / tfidf ──────────────────────────────────────────────────────

class TestTokenize:
    def test_stopwords_removed(self):
        tokens = tokenize("The python and the docker with experience")
        assert "the" not in tokens
        assert "and" not in tokens
        assert "with" not in tokens
        assert "python" in tokens and "docker" in tokens

    def test_lowercase_and_min_length(self):
        assert tokenize("Go") == ["go"]          # 2 chars: kept
        assert tokenize("a I x") == []           # 1 char: dropped


class TestTfidfScore:
    def test_identical_texts_score_high(self):
        text = "python backend engineer fastapi postgresql"
        assert tfidf_score(text, text) == pytest.approx(100.0)

    def test_disjoint_texts_score_zero(self):
        assert tfidf_score("python docker kubernetes",
                           "gardening floral basketweaving") == 0.0

    def test_empty_text_scores_zero(self):
        assert tfidf_score("", "python") == 0.0
        assert tfidf_score("python", "") == 0.0

    def test_score_bounded_0_100(self):
        score = tfidf_score(PYTHON_RESUME,
                            "python engineer with docker and aws")
        assert 0.0 <= score <= 100.0


class TestRankByTfidf:
    def test_python_job_ranks_above_designer_job(self, sample_jobs):
        ranked = rank_by_tfidf(sample_jobs, PYTHON_RESUME)
        titles = [j.title for j, _ in ranked]
        assert titles.index("Senior Python Developer") < \
            titles.index("Graphic Designer")
        # scores sorted descending
        scores = [s for _, s in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_ranks_all_jobs(self, sample_jobs):
        ranked = rank_by_tfidf(sample_jobs, PYTHON_RESUME)
        assert len(ranked) == len(sample_jobs)


# ── score_pipeline ────────────────────────────────────────────────────────

@pytest.fixture
def stored_jobs(store):
    """Three jobs persisted in the DB (real ids) under query 'q'."""
    jobs = [
        make_job(title="Senior Python Developer", company="Acme Cloud",
                 description="Python, FastAPI, PostgreSQL, Docker, AWS.",
                 link="https://ex.com/1", source="Remotive", search_query="q"),
        make_job(title="Data Engineer", company="BetaData",
                 description="Python, SQL, Airflow, Spark pipelines.",
                 link="https://ex.com/2", source="Arbeitnow", search_query="q"),
        make_job(title="Graphic Designer", company="Gamma Creative",
                 description="Figma, Photoshop, branding, illustration.",
                 link="https://ex.com/3", source="The Muse", search_query="q"),
    ]
    new, _ = store.upsert_jobs(jobs)
    assert new == 3
    return store.jobs_by_query("q")


class TestScorePipeline:
    def test_tfidf_persisted_for_all_llm_for_top_n(self, store, stored_jobs,
                                                   tmp_path):
        cfg = Config(db_path=tmp_path / "unused.db")
        seam = FakeSeam()
        out = score_pipeline(stored_jobs, PYTHON_RESUME, cfg, store,
                             seam=seam, top_n=2)

        # ALL jobs got a TF-IDF score (objects + DB)
        assert all(j.tfidf_score is not None for j in out)
        db_rows = store.conn.execute(
            "SELECT title, tfidf_score, llm_score FROM jobs").fetchall()
        assert len(db_rows) == 3
        assert all(r["tfidf_score"] is not None for r in db_rows)

        # EXACTLY the top-2 by TF-IDF got LLM scores
        llm_scored = [r for r in db_rows if r["llm_score"] is not None]
        assert len(llm_scored) == 2
        ranked_titles = [j.title for j, _ in
                         rank_by_tfidf(stored_jobs, PYTHON_RESUME)]
        assert {r["title"] for r in llm_scored} == set(ranked_titles[:2])

        # the seam saw exactly those two jobs (and nothing else)
        assert len(seam.calls) == 1
        assert set(seam.calls[0]) == {j.id for j in stored_jobs
                                      if j.title in ranked_titles[:2]}

        # LLM fields persisted with band-prefixed reasoning
        row = store.conn.execute(
            "SELECT * FROM jobs WHERE llm_score IS NOT NULL").fetchone()
        assert row["llm_score"] == 80.0
        assert row["llm_reasoning"].startswith("[Strong Fit]")
        assert json.loads(row["llm_matches"]) == ["Python"]
        assert json.loads(row["llm_gaps"]) == ["Kubernetes"]

    def test_relative_top_n_scored_even_when_all_tfidf_low(self, store,
                                                           stored_jobs,
                                                           tmp_path):
        """D8 regression: no absolute cutoff — top-N is RELATIVE to the pool.

        A resume that barely matches anything drives every TF-IDF score
        near zero; the pipeline must still LLM-score the top-N.
        """
        cfg = Config(db_path=tmp_path / "unused.db", llm_top_n=2)
        irrelevant_resume = "gardening permaculture botanical illustration"
        seam = FakeSeam()
        score_pipeline(stored_jobs, irrelevant_resume, cfg, store,
                       seam=seam, top_n=2)

        rows = store.conn.execute(
            "SELECT tfidf_score, llm_score FROM jobs").fetchall()
        assert all(r["tfidf_score"] < 30 for r in rows)   # all "low"
        llm_scored = [r for r in rows if r["llm_score"] is not None]
        assert len(llm_scored) == 2                        # still exactly top-N
        assert len(seam.calls) == 1 and len(seam.calls[0]) == 2

    def test_returns_same_mutated_jobs(self, store, stored_jobs, tmp_path):
        cfg = Config(db_path=tmp_path / "unused.db")
        out = score_pipeline(stored_jobs, PYTHON_RESUME, cfg, store,
                             seam=FakeSeam(), top_n=3)
        assert out is stored_jobs
        assert all(j.llm_score == 80.0 for j in out)

    def test_default_top_n_from_config(self, store, stored_jobs, tmp_path):
        cfg = Config(db_path=tmp_path / "unused.db", llm_top_n=3)
        seam = FakeSeam()
        score_pipeline(stored_jobs, PYTHON_RESUME, cfg, store, seam=seam)
        assert len(seam.calls[0]) == 3
