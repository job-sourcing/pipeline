"""Job / SourceResult dataclass contracts."""
from __future__ import annotations

import json

from jobsearch.models import Job, SourceResult


class TestJobDefaults:
    def test_identity_defaults(self):
        j = Job()
        assert j.id is None
        assert j.title == ""
        assert j.company == ""
        assert j.description == ""
        assert j.link == ""
        assert j.contact_email is None
        assert j.source == ""

    def test_metadata_defaults(self):
        j = Job()
        assert j.location == ""
        assert j.date_posted is None
        assert j.remote is False
        assert j.h1b_mention is False
        assert j.salary_text is None
        assert j.salary_min is None
        assert j.salary_max is None

    def test_workflow_state_defaults(self):
        j = Job()
        assert j.status == "shortlisted"
        assert j.pipeline_stage == "saved"

    def test_scoring_defaults(self):
        j = Job()
        assert j.tfidf_score is None
        assert j.llm_score is None
        assert j.llm_reasoning is None
        assert j.llm_matches is None
        assert j.llm_gaps is None
        assert j.scored_at is None

    def test_provenance_defaults(self):
        j = Job()
        assert j.search_query == ""
        assert j.scraped_at  # auto-stamped ISO timestamp
        assert j.created_at
        assert j.updated_at
        assert j.apply_intent_at is None
        assert j.apply_intent_acknowledged_at is None


class TestToDict:
    def test_lists_are_json_encoded_strings(self):
        j = Job(title="T", company="C", llm_matches=["Python", "Go"],
                llm_gaps=["AWS"])
        d = j.to_dict()
        assert isinstance(d["llm_matches"], str)
        assert isinstance(d["llm_gaps"], str)
        assert json.loads(d["llm_matches"]) == ["Python", "Go"]
        assert json.loads(d["llm_gaps"]) == ["AWS"]

    def test_none_lists_stay_none(self):
        d = Job().to_dict()
        assert d["llm_matches"] is None
        assert d["llm_gaps"] is None

    def test_roundtrip_through_json(self):
        j = Job(title="T", company="C", llm_matches=["a"], llm_gaps=["b"])
        d = json.loads(json.dumps(j.to_dict()))
        assert d["title"] == "T"
        assert json.loads(d["llm_matches"]) == ["a"]

    def test_all_columns_present(self):
        d = Job().to_dict()
        for key in ("id", "title", "company", "description", "link",
                    "contact_email", "source", "location", "date_posted",
                    "remote", "h1b_mention", "salary_text", "salary_min",
                    "salary_max", "status", "pipeline_stage", "tfidf_score",
                    "llm_score", "llm_reasoning", "llm_matches", "llm_gaps",
                    "scored_at", "search_query", "scraped_at", "created_at",
                    "updated_at", "apply_intent_at",
                    "apply_intent_acknowledged_at"):
            assert key in d, f"missing column {key}"


class TestJobText:
    def test_concatenates_title_company_description(self):
        j = Job(title="Python Dev", company="Acme", description="FastAPI")
        assert j.job_text() == "Python Dev Acme FastAPI"

    def test_strips_when_description_empty(self):
        j = Job(title="Dev", company="Acme")
        assert j.job_text() == "Dev Acme"


class TestSourceResult:
    def test_ok_when_no_error(self):
        r = SourceResult(source="Remotive", jobs=[Job()])
        assert r.ok is True
        assert r.error is None

    def test_not_ok_when_error_set(self):
        r = SourceResult(source="Remotive", error="RuntimeError: boom")
        assert r.ok is False

    def test_defaults(self):
        r = SourceResult(source="X")
        assert r.jobs == []
        assert r.duration_ms == 0
