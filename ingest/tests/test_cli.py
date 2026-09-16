"""CLI end-to-end via CliRunner with the search + scoring seams patched.

cli.py imports search_all_sources / score_pipeline at module top level, so we
patch them in the jobsearch.cli namespace.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

import jobsearch.cli as cli_module
from jobsearch.cli import search_jobs
from jobsearch.models import Job, SourceResult


def fake_search_ok(keywords, location, sources, num, cfg):
    return [
        SourceResult(source="Remotive", jobs=[
            Job(title="Senior Python Developer", company="Acme Cloud",
                description="Python FastAPI backend services.",
                link="https://ex.com/1", source="Remotive", remote=True),
            Job(title="Python Backend Engineer", company="BetaData",
                description="Django, Redis, PostgreSQL.",
                link="https://ex.com/2", source="Remotive"),
        ]),
    ]


def make_fake_score_pipeline():
    calls = []

    def fake(jobs, resume_text, cfg, store, seam=None, top_n=None):
        calls.append([j.id for j in jobs])
        for j in jobs[: (top_n or len(jobs))]:
            store.update_scores(j.id, llm={
                "score": 82.0,
                "reasoning": "[Strong Fit] fake",
                "top_matches": ["Python"],
                "gaps": [],
            })
        return jobs

    return fake, calls


@pytest.fixture
def cli_env(monkeypatch):
    monkeypatch.setattr(cli_module, "search_all_sources", fake_search_ok)
    fake, calls = make_fake_score_pipeline()
    monkeypatch.setattr(cli_module, "score_pipeline", fake)
    return calls


class TestCliHappyPath:
    def test_run_produces_report_table(self, tmp_path, cli_env):
        db = tmp_path / "cli" / "tracker.db"
        result = CliRunner().invoke(search_jobs, [
            "python developer", "--db", str(db), "--sources", "Remotive",
        ])
        assert result.exit_code == 0, result.output
        assert "RESULTS for 'python developer'" in result.output
        assert "2 tracked jobs" in result.output
        # table header
        assert "score" in result.output
        assert "title" in result.output
        # job rows
        assert "Senior Python Developer" in result.output
        assert "Acme Cloud" in result.output
        # LLM-scored column (fake score_pipeline persisted 82.0)
        assert "82.0L" in result.output
        assert "[Strong Fit] fake" in result.output

    def test_run_persists_jobs_and_run_row(self, tmp_path, cli_env):
        db = tmp_path / "cli" / "tracker.db"
        result = CliRunner().invoke(search_jobs, [
            "python developer", "--db", str(db), "--sources", "Remotive",
        ])
        assert result.exit_code == 0, result.output

        from jobsearch.storage import Store
        store = Store(db)
        jobs = store.jobs_by_query("python developer")
        assert len(jobs) == 2
        assert all(j.search_query == "python developer" for j in jobs)
        assert {j.source for j in jobs} == {"Remotive"}

        run = store.conn.execute("SELECT * FROM runs").fetchone()
        assert run["command"] == "search-jobs"
        assert run["finished_at"]
        assert run["jobs_found"] == 2
        assert run["jobs_new"] == 2
        assert run["jobs_merged"] == 0
        assert json.loads(run["sources_ok"]) == ["Remotive"]
        assert json.loads(run["sources_degraded"]) == []
        store.close()

    def test_score_pipeline_called_with_top_n(self, tmp_path, cli_env):
        db = tmp_path / "cli" / "tracker.db"
        result = CliRunner().invoke(search_jobs, [
            "python developer", "--db", str(db), "--sources", "Remotive",
            "--top-n", "1",
        ])
        assert result.exit_code == 0, result.output
        assert len(cli_env) == 1        # scoring ran exactly once
        assert len(cli_env[0]) == 2     # saw both stored jobs
        # only the top-1 got the LLM score persisted
        from jobsearch.storage import Store
        store = Store(db)
        scored = store.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE llm_score IS NOT NULL"
        ).fetchone()[0]
        assert scored == 1
        store.close()


class TestCliOptions:
    def test_unknown_source_exits_2(self, tmp_path, cli_env):
        db = tmp_path / "cli" / "tracker.db"
        result = CliRunner().invoke(search_jobs, [
            "python developer", "--db", str(db),
            "--sources", "Remotive,NoSuchBoard",
        ])
        assert result.exit_code == 2
        assert "Unknown sources" in result.output
        assert "NoSuchBoard" in result.output

    def test_no_score_skips_scoring(self, tmp_path, cli_env):
        db = tmp_path / "cli" / "tracker.db"
        result = CliRunner().invoke(search_jobs, [
            "python developer", "--db", str(db), "--sources", "Remotive",
            "--no-score",
        ])
        assert result.exit_code == 0, result.output
        assert cli_env == []            # score_pipeline never called
        assert "TF-IDF ranked" in result.output
        assert "Senior Python Developer" in result.output

    def test_degraded_source_warns_but_completes(self, tmp_path, monkeypatch):
        def search_with_failure(keywords, location, sources, num, cfg):
            return [
                SourceResult(source="Remotive", jobs=[
                    Job(title="Senior Python Developer", company="Acme Cloud",
                        link="https://ex.com/1", source="Remotive"),
                ]),
                SourceResult(source="RemoteOK", error="RuntimeError: boom"),
            ]
        monkeypatch.setattr(cli_module, "search_all_sources", search_with_failure)
        fake, _calls = make_fake_score_pipeline()
        monkeypatch.setattr(cli_module, "score_pipeline", fake)

        db = tmp_path / "cli" / "tracker.db"
        result = CliRunner().invoke(search_jobs, [
            "python developer", "--db", str(db),
            "--sources", "Remotive,RemoteOK",
        ])
        assert result.exit_code == 0, result.output
        assert "degraded" in result.output
        assert "RemoteOK" in result.output

        from jobsearch.storage import Store
        store = Store(db)
        run = store.conn.execute("SELECT * FROM runs").fetchone()
        assert json.loads(run["sources_ok"]) == ["Remotive"]
        assert json.loads(run["sources_degraded"]) == ["RemoteOK"]
        store.close()

    def test_resume_file_option(self, tmp_path, cli_env):
        resume = tmp_path / "resume.txt"
        resume.write_text("Python developer with Go and Rust experience.",
                          encoding="utf-8")
        db = tmp_path / "cli" / "tracker.db"
        result = CliRunner().invoke(search_jobs, [
            "python developer", "--db", str(db), "--sources", "Remotive",
            "--resume-file", str(resume),
        ])
        assert result.exit_code == 0, result.output


class TestCliOpsAndJsonl:
    """Sprint 3: --ops pass (repost + ghost detection) and --jsonl export."""

    def _fake_search_ops(self, keywords, location, sources, num, cfg):
        from datetime import date, timedelta
        old = (date.today() - timedelta(days=45)).isoformat()
        recent = (date.today() - timedelta(days=10)).isoformat()
        return [
            # repost cluster: same role, two links, 40 days apart
            SourceResult(source="Remotive", jobs=[
                Job(title="Senior Data Engineer", company="Ghostly",
                    description="dbt, Airflow.", link="https://g.com/1",
                    source="Remotive", date_posted=old),
            ]),
            SourceResult(source="Adzuna", jobs=[
                Job(title="Senior Data Engineer", company="Ghostly",
                    description="dbt, Airflow.", link="https://g.com/2",
                    source="Adzuna", date_posted=recent),
                # a healthy job: single sighting, recent
                Job(title="Backend Engineer", company="FreshCo",
                    description="Python.", link="https://f.com/1",
                    source="Adzuna", date_posted=recent),
            ]),
        ]

    def test_ops_pass_flags_repost_and_ghost(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli_module, "search_all_sources",
                            self._fake_search_ops)
        monkeypatch.setattr(cli_module, "score_pipeline",
                            make_fake_score_pipeline()[0])
        runner = CliRunner()
        result = runner.invoke(search_jobs, [
            "data engineer", "--db", str(tmp_path / "t.db"),
            "--no-score",
        ])
        assert result.exit_code == 0, result.output
        assert "Ghost-job candidates" in result.output
        assert "Repost clusters" in result.output

        # persisted on the surviving stored rows (dedup merged the two
        # Ghostly sightings into ONE row — its link is one of the cluster's)
        import sqlite3
        conn = sqlite3.connect(tmp_path / "t.db")
        ghosts = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE ghost_candidate = 1").fetchone()[0]
        reposts = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE repost_count >= 2").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        conn.close()
        assert total == 2            # Ghostly pair merged + FreshCo
        assert ghosts == 1           # the surviving Ghostly row is flagged
        assert reposts == 1          # …and carries repost_count=2

    def test_jsonl_export_option(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli_module, "search_all_sources",
                            self._fake_search_ops)
        monkeypatch.setattr(cli_module, "score_pipeline",
                            make_fake_score_pipeline()[0])
        out = tmp_path / "out" / "jobs.jsonl"
        runner = CliRunner()
        result = runner.invoke(search_jobs, [
            "data engineer", "--db", str(tmp_path / "t.db"),
            "--no-score", "--jsonl", str(out),
        ])
        assert result.exit_code == 0, result.output
        assert "Exported 2 jobs" in result.output   # Ghostly pair dedup-merged
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["title"]
        assert "ghost_candidate" in first and "repost_count" in first
        assert "tier" in first and "skills" in first and "work_mode" in first
        # Wave-R R2: --jsonl emits the facet-01 contract by default
        assert first["job_id"] and first["remote_allowed"] is False
        assert "experience_level" in first and "work_type" in first
        assert "listed_time" in first and "link" in first

    def test_no_ops_skips_detection(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli_module, "search_all_sources",
                            self._fake_search_ops)
        monkeypatch.setattr(cli_module, "score_pipeline",
                            make_fake_score_pipeline()[0])
        runner = CliRunner()
        result = runner.invoke(search_jobs, [
            "data engineer", "--db", str(tmp_path / "t.db"),
            "--no-score", "--no-ops",
        ])
        assert result.exit_code == 0, result.output
        assert "Ghost-job candidates" not in result.output
        import sqlite3
        conn = sqlite3.connect(tmp_path / "t.db")
        assert conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE ghost_candidate = 1"
        ).fetchone()[0] == 0
        conn.close()
