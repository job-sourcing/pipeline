"""Cross-runtime seam contract, tested against the REAL node scripts.

Runs `node llm/score_jobs.mjs --payload <file>` with ZAI_MOCK pointing at
fixtures — no network, no real LLM. Exit code 0 + one JSON envelope on stdout
is the contract (D1.1); per-item failures must never crash the script (D9).
"""
from __future__ import annotations

import json
import os
import subprocess

from conftest import LLM_FIXTURES, REPO_ROOT

SCORE_SCRIPT = REPO_ROOT / "llm" / "score_jobs.mjs"
NODE = "node"


def make_payload(tmp_path, jobs=None) -> str:
    payload = {
        "resume": "Senior Python backend engineer: FastAPI, PostgreSQL, Docker.",
        "prompt_template": (
            "Resume: {resume}\nTitle: {title}\nCompany: {company}\n"
            "Description: {description}\nRespond with one JSON object."
        ),
        "prompt_version": "2",
        "jobs": jobs if jobs is not None else [
            {"id": 1, "title": "Python Developer", "company": "Acme Cloud",
             "description": "Python, FastAPI, PostgreSQL backend work."},
        ],
    }
    p = tmp_path / "payload.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def run_script(payload_path=None, mock=None, stdin="") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if mock:
        env["ZAI_MOCK"] = str(LLM_FIXTURES / mock)
    else:
        env.pop("ZAI_MOCK", None)
    cmd = [NODE, str(SCORE_SCRIPT)]
    if payload_path:
        cmd += ["--payload", payload_path]
    return subprocess.run(
        cmd, capture_output=True, text=True, env=env, timeout=120,
        cwd=str(REPO_ROOT), input=stdin,
    )


def envelope(proc) -> dict:
    return json.loads(proc.stdout)


class TestEnvelopeShape:
    def test_valid_fixture_ok_envelope(self, tmp_path):
        payload = make_payload(tmp_path, jobs=[
            {"id": 1, "title": "T1", "company": "C1", "description": "D1"},
            {"id": 2, "title": "T2", "company": "C2", "description": "D2"},
            {"id": 3, "title": "T3", "company": "C3", "description": "D3"},
        ])
        proc = run_script(payload, mock="valid.json")
        assert proc.returncode == 0
        env = envelope(proc)
        assert env["ok"] is True
        assert env["schema_version"] == "1"
        results = env["data"]["results"]
        assert len(results) == 3
        assert all(r["ok"] for r in results)
        # fixture items replay in order -> job 1 gets the first response
        assert results[0]["id"] == 1
        assert results[0]["dims"]["technical"] == 85
        assert results[0]["dims"]["experience"] == 90
        assert results[0]["reasoning"].startswith("Strong Python")
        assert results[0]["top_matches"] == ["Python", "FastAPI"]
        assert results[0]["gaps"] == ["Go"]

    def test_stdout_is_exactly_one_json_object(self, tmp_path):
        payload = make_payload(tmp_path)
        proc = run_script(payload, mock="valid.json")
        # the whole stdout must parse as a single JSON document
        json.loads(proc.stdout)
        assert proc.stdout.count("{") >= 1


class TestSalvageParsing:
    def test_fenced_json_is_salvaged(self, tmp_path):
        proc = run_script(make_payload(tmp_path), mock="fenced.json")
        env = envelope(proc)
        assert proc.returncode == 0
        assert env["ok"] is True
        r = env["data"]["results"][0]
        assert r["ok"] is True
        assert r["dims"]["technical"] == 85

    def test_prose_wrapped_json_is_salvaged(self, tmp_path):
        proc = run_script(make_payload(tmp_path), mock="prose.json")
        env = envelope(proc)
        assert env["ok"] is True
        r = env["data"]["results"][0]
        assert r["ok"] is True
        assert r["dims"]["technical"] == 85
        assert r["top_matches"] == ["Python", "Docker"]

    def test_double_object_returns_first(self, tmp_path):
        proc = run_script(make_payload(tmp_path), mock="double_object.json")
        env = envelope(proc)
        assert env["ok"] is True
        r = env["data"]["results"][0]
        assert r["ok"] is True
        assert r["dims"]["technical"] == 85       # FIRST object wins
        assert r["reasoning"] == "first"

    def test_truncated_json_fails_cleanly(self, tmp_path):
        proc = run_script(make_payload(tmp_path), mock="truncated.json")
        env = envelope(proc)
        assert proc.returncode == 0               # still an envelope, not a crash
        assert env["ok"] is True                  # script-level ok; item failed
        r = env["data"]["results"][0]
        assert r["ok"] is False
        assert r["error"] == "unparseable_output"
        assert r["raw"]                            # raw output preserved

    def test_garbage_output_is_per_item_failure_not_crash(self, tmp_path):
        payload = make_payload(tmp_path, jobs=[
            {"id": 1, "title": "T1", "company": "C1", "description": "D1"},
            {"id": 2, "title": "T2", "company": "C2", "description": "D2"},
        ])
        proc = run_script(payload, mock="garbage.json")
        assert proc.returncode == 0
        env = envelope(proc)
        assert env["ok"] is True
        results = env["data"]["results"]
        assert len(results) == 2
        assert all(r["ok"] is False for r in results)
        assert {r["error"] for r in results} == {"unparseable_output"}

    def test_invalid_dims_quarantined_as_item_failure(self, tmp_path):
        proc = run_script(make_payload(tmp_path), mock="invalid_dims.json")
        env = envelope(proc)
        assert env["ok"] is True
        r = env["data"]["results"][0]
        assert r["ok"] is False
        assert r["error"] == "bad_dim_technical"  # 200 is out of 0-100

    def test_retry_then_valid_recovers_on_second_attempt(self, tmp_path):
        proc = run_script(make_payload(tmp_path), mock="retry_then_valid.json")
        env = envelope(proc)
        assert env["ok"] is True
        r = env["data"]["results"][0]
        assert r["ok"] is True                     # recovered after retry
        assert r["dims"]["technical"] == 70
        attempts = {a["id"]: a["attempts"] for a in env["data"]["attempts"]}
        assert attempts[1] == 2                    # genuinely used both attempts

    def test_attempts_is_one_on_first_try(self, tmp_path):
        proc = run_script(make_payload(tmp_path), mock="valid.json")
        env = envelope(proc)
        attempts = {a["id"]: a["attempts"] for a in env["data"]["attempts"]}
        assert attempts[1] == 1


class TestErrorPropagation:
    def test_retryable_sdk_error_becomes_llm_retryable_envelope(self, tmp_path):
        proc = run_script(make_payload(tmp_path), mock="error_retryable.json")
        assert proc.returncode == 0
        env = envelope(proc)
        assert env["ok"] is False
        assert env["error"]["code"] == "llm_retryable"
        assert env["error"]["retryable"] is True
        assert "429" in env["error"]["message"]

    def test_bad_payload_envelope_exit_zero(self):
        # empty stdin, no --payload -> bad_payload envelope, NOT a crash
        proc = run_script(stdin="")
        assert proc.returncode == 0
        env = envelope(proc)
        assert env["ok"] is False
        assert env["error"]["code"] == "bad_payload"

    def test_malformed_payload_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        proc = run_script(str(p), mock="valid.json")
        assert proc.returncode == 0
        env = envelope(proc)
        assert env["ok"] is False
        assert env["error"]["code"] == "bad_payload"
