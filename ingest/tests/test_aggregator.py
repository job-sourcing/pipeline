"""Aggregator: parallel fan-out with failure isolation (D3)."""
from __future__ import annotations

import pytest

from jobsearch.config import Config
from jobsearch.models import Job
from jobsearch.sources import (
    DEFAULT_SOURCES,
    REGISTRY,
    all_source_names,
    search_all_sources,
)

from conftest import make_job


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(db_path=tmp_path / "t.db")


def fake_fetch(jobs):
    def fetch(keywords, location, num_results, cfg=None):
        return jobs
    return fetch


def raiser(*args, **kwargs):
    raise RuntimeError("boom")


class TestSearchAllSources:
    def test_two_mocked_sources_return_results(self, monkeypatch, cfg):
        monkeypatch.setattr(REGISTRY["Remotive"], "fetch",
                            fake_fetch([make_job(title="A")]))
        monkeypatch.setattr(REGISTRY["Arbeitnow"], "fetch",
                            fake_fetch([make_job(title="B"),
                                        make_job(title="C")]))
        results = search_all_sources("python", "Remote",
                                     ["Remotive", "Arbeitnow"], 5, cfg)
        assert len(results) == 2
        by_source = {r.source: r for r in results}
        assert set(by_source) == {"Remotive", "Arbeitnow"}
        assert all(r.ok for r in results)
        assert len(by_source["Arbeitnow"].jobs) == 2
        assert all(isinstance(j, Job) for r in results for j in r.jobs)

    def test_failing_source_degrades_never_crashes(self, monkeypatch, cfg):
        monkeypatch.setattr(REGISTRY["Remotive"], "fetch", raiser)
        monkeypatch.setattr(REGISTRY["Arbeitnow"], "fetch",
                            fake_fetch([make_job(title="B")]))
        results = search_all_sources("python", "Remote",
                                     ["Remotive", "Arbeitnow"], 5, cfg)
        assert len(results) == 2
        failed = next(r for r in results if r.source == "Remotive")
        ok = next(r for r in results if r.source == "Arbeitnow")
        assert failed.ok is False
        assert "RuntimeError" in failed.error
        assert "boom" in failed.error
        assert failed.jobs == []
        assert ok.ok is True          # other sources unaffected
        assert len(ok.jobs) == 1

    def test_remote_only_sources_skipped_for_city_search(self, monkeypatch, cfg):
        # Use an explicit source list so the contract test stays robust to
        # future DEFAULT_SOURCES additions (CR-1-DOCS / SRC-* gap-fill).
        sources = ["Remotive", "RemoteOK", "Jobicy", "Arbeitnow",
                   "LinkedIn Guest", "The Muse"]
        monkeypatch.setattr(REGISTRY["Remotive"], "fetch", raiser)      # remote_only
        monkeypatch.setattr(REGISTRY["RemoteOK"], "fetch", raiser)      # remote_only
        monkeypatch.setattr(REGISTRY["Jobicy"], "fetch", raiser)        # remote_only
        monkeypatch.setattr(REGISTRY["Arbeitnow"], "fetch",
                            fake_fetch([make_job(title="B")]))
        monkeypatch.setattr(REGISTRY["LinkedIn Guest"], "fetch",
                            fake_fetch([make_job(title="L")]))
        monkeypatch.setattr(REGISTRY["The Muse"], "fetch",
                            fake_fetch([make_job(title="M")]))
        results = search_all_sources("python", "New York", sources, 5, cfg)
        sources_returned = {r.source for r in results}
        # remote-only boards are skipped, not even attempted (no error rows)
        assert sources_returned == {"Arbeitnow", "LinkedIn Guest", "The Muse"}

    def test_remote_only_sources_run_for_remote_location(self, monkeypatch, cfg):
        monkeypatch.setattr(REGISTRY["Remotive"], "fetch",
                            fake_fetch([make_job(title="R")]))
        results = search_all_sources("python", "Remote", ["Remotive"], 5, cfg)
        assert len(results) == 1
        assert results[0].ok and results[0].source == "Remotive"

    def test_unknown_source_names_ignored(self, monkeypatch, cfg):
        monkeypatch.setattr(REGISTRY["Remotive"], "fetch",
                            fake_fetch([make_job()]))
        results = search_all_sources("python", "Remote",
                                     ["Remotive", "NoSuchBoard", "Also Fake"],
                                     5, cfg)
        assert len(results) == 1
        assert results[0].source == "Remotive"

    def test_default_sources_are_all_registered(self):
        assert set(DEFAULT_SOURCES) <= set(all_source_names())
        assert set(all_source_names()) == set(REGISTRY.keys())

    def test_duration_ms_recorded(self, monkeypatch, cfg):
        monkeypatch.setattr(REGISTRY["Remotive"], "fetch",
                            fake_fetch([make_job()]))
        results = search_all_sources("python", "Remote", ["Remotive"], 5, cfg)
        assert all(r.duration_ms >= 0 for r in results)

    def test_source_registry_fetch_contract(self, cfg):
        # every registered fetch must accept the aggregator's call shape:
        # fetch(keywords, location, num_per_source, cfg=cfg)
        for name, src in REGISTRY.items():
            import inspect
            sig = inspect.signature(src.fetch)
            params = list(sig.parameters)
            assert len(params) >= 3, f"{name} fetch arity too low: {params}"
            assert "cfg" in sig.parameters, f"{name} fetch lacks cfg kwarg"


class TestCategorizationEnrichment:
    """Sprint 3: the aggregator's per-job pass now categorizes (tier, skills,
    work_mode, ats_platform) alongside trust enrichment."""

    def test_jobs_categorized_on_fetch(self, monkeypatch, cfg):
        monkeypatch.setattr(REGISTRY["Remotive"], "fetch", fake_fetch([
            make_job(
                title="Senior Kubernetes Platform Engineer",
                location="Remote",
                description="Terraform, AWS, Go. Hybrid possible.",
                link="https://boards.greenhouse.io/acme/jobs/7",
            ),
        ]))
        results = search_all_sources("python", "Remote", ["Remotive"], 5, cfg)
        job = results[0].jobs[0]
        assert job.tier == "senior"
        assert "Kubernetes" in (job.skills or [])
        assert "Terraform" in (job.skills or [])
        assert job.work_mode == "remote"          # location marker wins
        assert job.ats_platform == "greenhouse"   # from the link

    def test_trust_still_enriched(self, monkeypatch, cfg):
        monkeypatch.setattr(REGISTRY["Remotive"], "fetch",
                            fake_fetch([make_job()]))
        results = search_all_sources("python", "Remote", ["Remotive"], 5, cfg)
        job = results[0].jobs[0]
        assert job.trust_score is not None
        assert job.trust_level is not None

    def test_non_ats_link_gets_no_platform(self, monkeypatch, cfg):
        monkeypatch.setattr(REGISTRY["Remotive"], "fetch", fake_fetch([
            make_job(link="https://remotive.com/remote-jobs/123")]))
        results = search_all_sources("python", "Remote", ["Remotive"], 5, cfg)
        assert results[0].jobs[0].ats_platform is None
