"""LLMSeam: the single Python->Node->SDK boundary (D1.1/D1.2/D8/D9).

ZAI_MOCK is inherited by the node subprocess; the real repo llm/ dir is used
(Config default llm_dir) so the actual score_jobs.mjs contract is exercised.
"""
from __future__ import annotations

import pytest

from jobsearch.config import Config
from jobsearch.llm import BANDS, WEIGHTS, LLMSeam, SeamError, compute_overall
from jobsearch.storage import Store, cache_key

from conftest import LLM_FIXTURES, make_job

RESUME = "Senior Python engineer: FastAPI, PostgreSQL, Docker, AWS."


def make_seam(tmp_path, **cfg_overrides) -> tuple[LLMSeam, Store, Config]:
    defaults = dict(db_path=tmp_path / "tracker.db",
                    llm_batch_delay_s=0.01,
                    llm_retry_backoff_s=0.01,
                    llm_max_retries=2)
    defaults.update(cfg_overrides)
    cfg = Config(**defaults)
    store = Store(cfg.db_path)
    return LLMSeam(cfg, store), store, cfg


def three_jobs():
    return [
        make_job(id=1, title="Senior Python Developer", company="Acme Cloud",
                 description="Python, FastAPI, PostgreSQL.", link="https://ex/1"),
        make_job(id=2, title="Data Engineer", company="BetaData",
                 description="Python, SQL, Airflow.", link="https://ex/2"),
        make_job(id=3, title="DevOps Engineer", company="GammaOps",
                 description="Kubernetes, Terraform.", link="https://ex/3"),
    ]


def telemetry_events(store, event=None):
    if event:
        rows = store.conn.execute("SELECT event FROM telemetry WHERE event=?",
                                  (event,)).fetchall()
    else:
        rows = store.conn.execute("SELECT event FROM telemetry").fetchall()
    return [r["event"] for r in rows]


# ── compute_overall (deterministic Python-side arithmetic) ────────────────

class TestComputeOverall:
    def test_weights_sum_to_one(self):
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_exact_weighted_math(self):
        dims = {"technical": 85, "experience": 90, "behavioral": 75, "career": 80}
        overall, band = compute_overall(dims)
        expected = 0.30 * 85 + 0.25 * 90 + 0.15 * 75 + 0.30 * 80
        assert overall == pytest.approx(expected, abs=0.06)
        assert band == "Strong Fit"

    @pytest.mark.parametrize("score,band", [
        (100, "Strong Fit"),
        (75, "Strong Fit"),      # boundary is inclusive
        (74.9, "Good"),
        (60, "Good"),
        (59.9, "Moderate"),
        (45, "Moderate"),
        (44.9, "Weak"),
        (30, "Weak"),
        (29.9, "Poor"),
        (0, "Poor"),
    ])
    def test_band_boundaries(self, score, band):
        dims = {k: score for k in WEIGHTS}
        overall, label = compute_overall(dims)
        assert overall == pytest.approx(score, abs=0.01)
        assert label == band

    def test_each_dimension_weight(self):
        base = {k: 0.0 for k in WEIGHTS}
        for dim, weight in WEIGHTS.items():
            dims = dict(base, **{dim: 100.0})
            overall, _ = compute_overall(dims)
            assert overall == pytest.approx(weight * 100)

    def test_bands_are_ordered(self):
        floors = [floor for floor, _ in BANDS]
        assert floors == sorted(floors, reverse=True)


# ── score_jobs end-to-end through the real node script ────────────────────

class TestScoreJobs:
    def test_valid_fixture_all_ok_with_exact_math(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZAI_MOCK", str(LLM_FIXTURES / "valid.json"))
        seam, store, _ = make_seam(tmp_path)
        results = seam.score_jobs(three_jobs(), RESUME)
        assert len(results) == 3
        assert all(r["ok"] for r in results)

        r0 = results[0]
        assert r0["id"] == 1
        expected = 0.30 * 85 + 0.25 * 90 + 0.15 * 75 + 0.30 * 80
        assert r0["overall"] == pytest.approx(expected, abs=0.06)
        assert r0["band"] == "Strong Fit"
        assert r0["dims"] == {"technical": 85, "experience": 90,
                              "behavioral": 75, "career": 80}
        assert r0["top_matches"] == ["Python", "FastAPI"]
        assert r0["gaps"] == ["Go"]

        # second fixture response -> 60/50/40/55
        r1 = results[1]
        assert r1["overall"] == pytest.approx(53.0, abs=0.06)
        assert r1["band"] == "Moderate"

        # third fixture response -> all 100
        r2 = results[2]
        assert r2["overall"] == pytest.approx(100.0)
        assert r2["band"] == "Strong Fit"

    def test_garbage_fixture_quarantines(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZAI_MOCK", str(LLM_FIXTURES / "garbage.json"))
        seam, store, _ = make_seam(tmp_path)
        results = seam.score_jobs(three_jobs(), RESUME)
        assert all(r["ok"] is False for r in results)
        assert all(r["error"] == "unparseable_output" for r in results)

        rows = store.conn.execute("SELECT * FROM quarantine").fetchall()
        assert len(rows) == 3   # one row per failed job
        assert all(r["script"] == "score_jobs" for r in rows)
        assert all(r["reason"] == "unparseable_output" for r in rows)
        from jobsearch.prompts import load_prompt as _lp
        assert all(r["prompt_version"] == _lp("score_jobs").version
                   for r in rows)
        # sentinel scores never enter the jobs table
        assert store.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE llm_score IS NOT NULL"
        ).fetchone()[0] == 0

    def test_second_call_is_served_from_cache(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZAI_MOCK", str(LLM_FIXTURES / "valid.json"))
        seam, store, _ = make_seam(tmp_path)
        jobs = three_jobs()

        first = seam.score_jobs(jobs, RESUME)
        llm_ok_after_first = telemetry_events(store, "llm_ok").count("llm_ok")
        assert llm_ok_after_first == 1   # one batch -> one subprocess run

        second = seam.score_jobs(jobs, RESUME)
        assert telemetry_events(store, "llm_ok").count("llm_ok") == \
            llm_ok_after_first          # cache hits, no new subprocess
        assert [r["overall"] for r in second] == [r["overall"] for r in first]
        assert [r["band"] for r in second] == [r["band"] for r in first]
        assert telemetry_events(store).count("cache_hit") >= 3

    def test_invalid_dims_fixture_quarantines(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZAI_MOCK", str(LLM_FIXTURES / "invalid_dims.json"))
        seam, store, _ = make_seam(tmp_path)
        results = seam.score_jobs(three_jobs()[:1], RESUME)
        assert results[0]["ok"] is False
        assert results[0]["error"] == "bad_dim_technical"
        assert store.conn.execute(
            "SELECT COUNT(*) FROM quarantine").fetchone()[0] == 1


# ── run(): retry / timeout / cache ────────────────────────────────────────

class TestRunRetry:
    def test_retryable_error_retries_then_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZAI_MOCK", str(LLM_FIXTURES / "error_retryable.json"))
        seam, store, _ = make_seam(tmp_path)  # max_retries=2, backoff=0.01
        payload = {"resume": RESUME, "prompt_template": "t", "prompt_version": "2",
                   "jobs": [{"id": 1, "title": "T", "company": "C",
                             "description": "D"}]}
        with pytest.raises(SeamError) as excinfo:
            seam.run("score_jobs", payload, prompt="batch prompt",
                     prompt_version="2")
        assert excinfo.value.code == "llm_retryable"
        assert excinfo.value.retryable is True
        # 1 initial attempt + 2 retries -> 3 subprocess runs, 3 error rows
        assert telemetry_events(store, "llm_error").count("llm_error") == 3

    def test_non_retryable_error_does_not_retry(self, tmp_path):
        # missing script fails fast with a non-retryable SeamError
        cfg = Config(db_path=tmp_path / "t.db", llm_retry_backoff_s=0.01)
        store = Store(cfg.db_path)
        seam = LLMSeam(cfg, store)
        with pytest.raises(SeamError) as excinfo:
            seam.run("no_such_script", {}, prompt="p", prompt_version="v")
        assert excinfo.value.code == "missing_script"
        assert excinfo.value.retryable is False
        assert telemetry_events(store, "llm_error").count("llm_error") == 1


class TestRunTimeout:
    def test_timeout_kills_process_group(self, tmp_path):
        llm_dir = tmp_path / "llm"
        llm_dir.mkdir()
        (llm_dir / "slow.mjs").write_text(
            "setTimeout(() => {}, 60000);\n", encoding="utf-8")
        cfg = Config(db_path=tmp_path / "t.db", llm_dir=llm_dir,
                     subprocess_timeout_s=2)
        seam = LLMSeam(cfg)
        with pytest.raises(SeamError) as excinfo:
            seam._run_script("slow", {})
        assert excinfo.value.code == "timeout"
        assert excinfo.value.retryable is True
        # the process group is dead: no lingering node processes for our script
        import subprocess as sp
        out = sp.run(["pgrep", "-f", "slow.mjs"], capture_output=True, text=True)
        assert out.stdout.strip() == ""


class TestRunCache:
    def test_batch_cache_hit_skips_subprocess(self, tmp_path, monkeypatch):
        # if the subprocess ran, it would fail with llm_retryable (mock);
        # a cache hit must return without touching node at all
        monkeypatch.setenv("ZAI_MOCK", str(LLM_FIXTURES / "error_retryable.json"))
        seam, store, _ = make_seam(tmp_path)
        key = cache_key("cached prompt", "v9")
        store.cache_put(key, "v9", {"results": [{"id": 1, "ok": True}]})

        data = seam.run("score_jobs", {}, prompt="cached prompt",
                        prompt_version="v9")
        assert data == {"results": [{"id": 1, "ok": True}]}
        assert telemetry_events(store, "llm_ok").count("llm_ok") == 0
        assert telemetry_events(store, "llm_error").count("llm_error") == 0

    def test_cache_version_invalidation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZAI_MOCK", str(LLM_FIXTURES / "error_retryable.json"))
        seam, store, _ = make_seam(tmp_path)
        key = cache_key("prompt text", "v1")
        store.cache_put(key, "v1", {"old": True})
        # same prompt, different version -> different key -> miss -> subprocess
        # (the mock's 429 error makes the run fail loudly if it is reached)
        payload = {"resume": RESUME, "prompt_template": "t", "prompt_version": "2",
                   "jobs": [{"id": 1, "title": "T", "company": "C",
                             "description": "D"}]}
        with pytest.raises(SeamError) as excinfo:
            seam.run("score_jobs", payload, prompt="prompt text",
                     prompt_version="v2")
        assert excinfo.value.code == "llm_retryable"


class TestSectorFinalize:
    """Sprint 3 (prompt v3): the sector axis flows through _finalize."""

    def test_finalize_carries_sector(self):
        from jobsearch.llm import _finalize
        job = make_job()
        r = {"ok": True, "dims": {"technical": 80, "experience": 70,
                                  "behavioral": 75, "career": 85},
             "reasoning": "fit", "top_matches": ["Python"], "gaps": [],
             "sector": "fintech"}
        out = _finalize(job, r)
        assert out["ok"] is True
        assert out["sector"] == "fintech"

    def test_finalize_sector_absent_ok(self):
        # older cached results / model omitted the field → no sector key
        from jobsearch.llm import _finalize
        job = make_job()
        r = {"ok": True, "dims": {"technical": 80, "experience": 70,
                                  "behavioral": 75, "career": 85},
             "reasoning": "fit", "top_matches": ["Python"], "gaps": []}
        out = _finalize(job, r)
        assert out["ok"] is True
        assert "sector" not in out

    def test_finalize_failed_item_has_no_sector(self):
        from jobsearch.llm import _finalize
        job = make_job()
        out = _finalize(job, {"ok": False, "error": "bad_json"})
        assert out["ok"] is False
        assert "sector" not in out
