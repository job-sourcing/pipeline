"""Workflow YAML contract (audit S7-B3 finding A5).

The broken board-watch.yml that shipped 2026-09-10 was caught only by a
manual pyyaml run — nothing in the suite or in automation validated the
workflows. This pins: every .yml under .github/workflows parses as a
mapping and declares a non-empty jobs map (a duplicate key or bad indent
otherwise ships a silently dead cron — the workflow never runs at all).

Mirrors the YAML-lint step in .github/workflows/ci.yml (which runs the
same parse over the same glob in automation). GHA budget is exhausted
until Oct 1 2026, so THIS test is the gate that actually fires locally
until then.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")   # dev extra (pyproject [dev])

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml"))


def test_workflows_exist():
    assert WORKFLOWS, f"no workflow YAMLs under {WORKFLOW_DIR}"


def test_every_workflow_parses_and_declares_jobs():
    for p in WORKFLOWS:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert isinstance(doc, dict), f"{p.name}: not a YAML mapping"
        assert doc.get("jobs"), f"{p.name}: no jobs map declared"


class TestCiWorkflow:
    """Pin the CI gate's own contract (the backstop's backstop): the
    budget-lean trigger choice and the job cap that make it safe to run
    once the GHA spending limit resets."""

    def _ci(self) -> dict:
        path = WORKFLOW_DIR / "ci.yml"
        assert path.exists(), "ci.yml must exist (audit A4: no CI at all)"
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_no_push_trigger_pr_and_dispatch_only(self):
        """Solo-dev flow pushes many small commits to main (checkpoint
        bots push on every leg); a push trigger would burn the 2,000
        min/mo private-repo allowance on runs the pusher just verified
        locally. PR + manual dispatch only."""
        doc = self._ci()
        # NB: pyyaml is YAML 1.1 — a bare `on:` key parses as boolean
        # True; GitHub's parser treats it as the string "on".
        on = doc.get("on") or doc.get(True)
        assert on, "ci.yml has no trigger (on:) map"
        assert "pull_request" in on
        assert "workflow_dispatch" in on
        assert "push" not in on, (
            "push trigger is a budget hazard for this repo — see the "
            "comment block in ci.yml")

    def test_job_capped_at_15_minutes(self):
        job = self._ci()["jobs"]["test"]
        assert job["timeout-minutes"] == 15
        assert job["runs-on"] == "ubuntu-latest"
