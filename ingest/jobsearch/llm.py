"""THE seam — every LLM call in the system goes through here (D1.1).

Python → Node subprocess → z-ai SDK. Contract:
  - payload via temp file (never argv)
  - ONE JSON envelope on stdout, logs on stderr
  - subprocess timeout with process-group kill
  - retryable errors → bounded retry with backoff (D8)
  - responses cached in SQLite keyed by hash(prompt + prompt_version) (D8)
  - persistent failures → quarantine, sentinel scores NEVER enter rankings (D1.2)

The overall score is computed DETERMINISTICALLY in Python from the four
dimension scores (0.30*technical + 0.25*experience + 0.15*behavioral +
0.30*career, ai-job-search /rank weights) — LLM arithmetic is never trusted.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from .config import Config
from .prompts import load_prompt, PromptAsset
from .storage import Store, cache_key

# ai-job-search /rank weights (exp 12, verbatim)
WEIGHTS = {"technical": 0.30, "experience": 0.25, "behavioral": 0.15, "career": 0.30}
BANDS = [(75, "Strong Fit"), (60, "Good"), (45, "Moderate"), (30, "Weak"), (0, "Poor")]

# Truncation limits — MUST match llm/score_jobs.mjs (single source of truth
# would be better; pinned equal by tests/test_llm_seam.py parity test).
RESUME_TRUNC = 4000
DESCRIPTION_TRUNC = 3000

# Retryable classifier — MUST match llm/lib/runner.mjs RETRYABLE_RE. Includes
# the SDK's actual format ("API request failed with status 503") verified in
# vendor dist/index.js.
_RETRYABLE_RE = re.compile(
    r"429|rate|timeout|timed out|temporar|ECONNRESET|ETIMEDOUT|ECONNREFUSED|"
    r"EPIPE|fetch failed|status 5\d\d", re.IGNORECASE)


class SeamError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.retryable = retryable


def compute_overall(dims: dict) -> tuple[float, str]:
    overall = sum(WEIGHTS[k] * dims[k] for k in WEIGHTS)
    band = next((label for floor, label in BANDS if overall >= floor), "Poor")
    return round(overall, 1), band


class LLMSeam:
    def __init__(self, cfg: Config, store: Optional[Store] = None):
        self.cfg = cfg
        self.store = store

    # ── low-level run ───────────────────────────────────────────────────

    def _run_script(self, script: str, payload: dict) -> dict:
        """Run llm/<script>.mjs with the envelope contract + timeout + group kill."""
        script_path = self.cfg.llm_dir / f"{script}.mjs"
        if not script_path.exists():
            raise SeamError("missing_script", f"{script_path} not found", False)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as tf:
            json.dump(payload, tf, ensure_ascii=False)
            payload_path = tf.name
        try:
            proc = subprocess.Popen(
                [self.cfg.node_bin, str(script_path), "--payload", payload_path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True,  # own process group
                cwd=str(self.cfg.llm_dir),
            )
        except OSError as e:  # node binary missing / not executable
            os.unlink(payload_path)
            raise SeamError("node_unavailable",
                            f"cannot launch {self.cfg.node_bin}: {e}", False)
        try:
            try:
                stdout, stderr = proc.communicate(timeout=self.cfg.subprocess_timeout_s)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                raise SeamError("timeout",
                                f"{script} exceeded {self.cfg.subprocess_timeout_s}s",
                                True)
            except KeyboardInterrupt:
                # D1.1: reap the child before propagating — otherwise the
                # detached process group survives and may hold DB locks.
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                raise
            if proc.returncode != 0:
                raise SeamError("crash",
                                f"exit {proc.returncode}: {stderr[-400:]}", False)
            try:
                envelope = json.loads(stdout)
            except json.JSONDecodeError:
                raise SeamError("bad_envelope",
                                f"stdout not JSON: {stdout[:300]}", False)
            if not envelope.get("ok"):
                err = envelope.get("error", {})
                raise SeamError(err.get("code", "unknown"),
                                err.get("message", ""), bool(err.get("retryable")))
            return envelope.get("data", {})
        finally:
            try:
                os.unlink(payload_path)
            except OSError:
                pass

    def run(self, script: str, payload: dict, *, prompt: str,
            prompt_version: str, use_cache: bool = True) -> dict:
        """Run with cache + bounded retry (D8). Raises SeamError on final failure."""
        key = cache_key(prompt, prompt_version)
        if use_cache and self.store:
            cached = self.store.cache_get(key)
            if cached is not None:
                return cached
        last_err: Optional[SeamError] = None
        for attempt in range(self.cfg.llm_max_retries + 1):
            try:
                t0 = time.monotonic()
                data = self._run_script(script, payload)
                duration = int((time.monotonic() - t0) * 1000)
                if self.store:
                    self.store.log_telemetry("llm_ok", script=script,
                                             duration_ms=duration, cache_hit=False)
                if use_cache and self.store:
                    self.store.cache_put(key, prompt_version, data)
                return data
            except SeamError as e:
                last_err = e
                if self.store:
                    self.store.log_telemetry("llm_error", script=script,
                                             error_class=e.code, detail=str(e))
                # Secondary classification (defense in depth): runner.mjs is
                # the primary classifier, but a transient message it misses
                # still deserves the bounded retry (F-04).
                retryable = e.retryable or bool(_RETRYABLE_RE.search(str(e)))
                if not retryable or attempt >= self.cfg.llm_max_retries:
                    raise
                time.sleep(self.cfg.llm_retry_backoff_s * (attempt + 1))
        raise last_err or SeamError("unknown", "unreachable", False)

    # ── scoring API (Week 1) ─────────────────────────────────────────────

    def score_jobs(self, jobs: list, resume_text: str,
                   prompt: Optional[PromptAsset] = None) -> list[dict]:
        """Score jobs (list of Job-like with id/title/company/description).

        Returns per-job dicts: {id, ok, dims, overall, band, reasoning,
        top_matches, gaps} — failures come back ok=False with error+raw
        and are quarantined (D1.2), never silently scored.

        Cache is PER JOB (key = hash(filled prompt + version)), so a re-run
        after a partial failure only re-scores the missing ones.
        """
        prompt = prompt or load_prompt("score_jobs")
        results: list[dict] = []
        pending: list[tuple] = []  # (job, per_job_key)

        for j in jobs:
            per_prompt = _fill(prompt.template, resume_text, j)
            k = cache_key(per_prompt, prompt.version)
            cached = self.store.cache_get(k) if self.store else None
            if cached is not None and "ok" in cached:
                results.append(_finalize(j, cached))
            else:
                pending.append((j, k))

        batch_size = self.cfg.llm_batch_size
        for i in range(0, len(pending), batch_size):
            if i > 0:
                time.sleep(self.cfg.llm_batch_delay_s)  # pacing (D8)
            batch = pending[i:i + batch_size]
            payload = {
                "resume": resume_text,
                "prompt_template": prompt.template,
                "prompt_version": prompt.version,
                "jobs": [{"id": j.id, "title": j.title, "company": j.company,
                          "description": j.description} for j, _ in batch],
            }
            batch_prompt = _batch_prompt(prompt.template, resume_text,
                                         [j for j, _ in batch])
            data = self.run("score_jobs", payload, prompt=batch_prompt,
                            prompt_version=prompt.version, use_cache=False)
            by_id = {r["id"]: r for r in data.get("results", [])}
            for j, k in batch:
                r = by_id.get(j.id)
                if r is None:
                    r = {"id": j.id, "ok": False, "error": "missing_from_batch"}
                if r.get("ok") and self.store:
                    self.store.cache_put(k, prompt.version, r)
                elif not r.get("ok") and self.store:
                    self.store.quarantine("score_jobs", prompt.version,
                                          r.get("raw", ""), r.get("error", ""))
                results.append(_finalize(j, r))
        return results


def _fill(template: str, resume: str, job) -> str:
    """Single-pass placeholder fill — parity with llm/score_jobs.mjs fillTemplate.

    Sequential .replace() is WRONG here: a resume containing a literal
    "{description}" would get re-substituted inside the already-filled resume
    section (CR-1-CODE F-01), and the cache key would hash a string that is
    not the prompt Node actually sends. re.sub processes the template once.
    """
    values = {
        "resume": (resume or "")[:RESUME_TRUNC],
        "title": job.title,
        "company": job.company,
        "description": (job.description or "")[:DESCRIPTION_TRUNC],
    }
    return re.sub(r"\{(resume|title|company|description)\}",
                  lambda m: str(values[m.group(1)]), template)


def _batch_prompt(template: str, resume: str, jobs: list) -> str:
    # A stable string covering the batch — used only as the cache key basis.
    return template + "||" + (resume or "")[:400] + "||" + "|".join(
        f"{j.id}:{j.title}:{j.company}" for j in jobs)


def _find_result(results: list[dict], job_id) -> Optional[dict]:
    for r in results:
        if r.get("id") == job_id:
            return r
    return None


def _finalize(job, r: dict) -> dict:
    out = {"id": job.id, "ok": bool(r.get("ok"))}
    if out["ok"]:
        dims = r.get("dims", {})
        overall, band = compute_overall(dims)
        out.update({
            "dims": dims, "overall": overall, "band": band,
            "reasoning": r.get("reasoning", ""),
            "top_matches": r.get("top_matches", []),
            "gaps": r.get("gaps", []),
        })
        # Sector (prompt v3, Sprint 3): absent on older cached results and
        # when the model omitted it — carried through as None, never "other".
        if r.get("sector"):
            out["sector"] = r["sector"]
    else:
        out["error"] = r.get("error", "unknown")
    return out
