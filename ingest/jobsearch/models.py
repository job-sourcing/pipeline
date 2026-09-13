"""Data models — plain dataclasses, no ORM (lifted/adapted from ResumeWing
database/models.py, provenance: vageesh-kudutini-ramesh/resume-wing).

Extended per HANDOFF Week 1 step 5: id/created_at/updated_at,
apply_intent_at/apply_intent_acknowledged_at, source (H14), plus scoring
fields for the two-stage scoring pipeline (TF-IDF rank + LLM score).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    """A single job listing discovered by a source, tracked through the pipeline."""

    # ── Identity ────────────────────────────────────────────────────────
    id: Optional[int] = None
    title: str = ""
    company: str = ""
    description: str = ""
    link: str = ""                     # primary apply URL (unique when set)
    contact_email: Optional[str] = None
    source: str = ""                   # "Remotive", "LinkedIn Guest", "JobSpy", ...

    # ── Metadata ────────────────────────────────────────────────────────
    location: str = ""
    date_posted: Optional[str] = None  # YYYY-MM-DD
    remote: bool = False
    h1b_mention: bool = False
    salary_text: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None

    # ── Workflow state ──────────────────────────────────────────────────
    status: str = "shortlisted"        # shortlisted | applied | skipped | ...
    pipeline_stage: str = "saved"      # saved | applied | following_up | interview | offer

    # ── Scoring (stage 1: TF-IDF rank; stage 2: LLM) ─────────────────────
    tfidf_score: Optional[float] = None
    llm_score: Optional[float] = None
    llm_reasoning: Optional[str] = None
    llm_matches: Optional[list[str]] = None
    llm_gaps: Optional[list[str]] = None
    scored_at: Optional[str] = None

    # ── Trust (Step D — per-job scoring, ported from T2 _trust-validator) ─
    trust_score: Optional[int] = None      # 0-100
    trust_flags: Optional[list[str]] = None  # e.g. ["suspicious_domain"]
    trust_level: Optional[str] = None      # high | medium | low

    # ── Categorization (Sprint 3 — T2 classify-tier/skill-extract port) ────
    tier: Optional[str] = None             # intern | entry | mid | senior
    skills: Optional[list[str]] = None     # canonical names, sorted
    work_mode: Optional[str] = None        # remote | hybrid | onsite | unknown
    ats_platform: Optional[str] = None     # greenhouse | lever | ashby | ... from link
    sector: Optional[str] = None           # LLM-pass taxonomy (prompt v3, top-N only)

    # ── Ops signals (Sprint 3 — T2 detect-reposts + §708 ghost heuristic) ──
    ghost_candidate: Optional[int] = None  # 0/1 (§708: aggregators-only, >30d)
    repost_count: Optional[int] = None     # distinct-URL sightings in window

    # ── Provenance & timestamps ─────────────────────────────────────────
    search_query: str = ""
    scraped_at: str = field(default_factory=_utcnow)
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    apply_intent_at: Optional[str] = None
    apply_intent_acknowledged_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # JSON-encode list fields for SQLite storage
        for k in ("llm_matches", "llm_gaps", "trust_flags", "skills"):
            if d.get(k) is not None:
                d[k] = __import__("json").dumps(d[k], ensure_ascii=False)
        return d

    def job_text(self) -> str:
        """Text used for matching/scoring."""
        return f"{self.title} {self.company} {self.description}".strip()


@dataclass
class SourceResult:
    """Outcome of one source fetch — success or degraded, never a crash (D3)."""
    source: str
    jobs: list[Job] = field(default_factory=list)
    error: Optional[str] = None        # set when the source degraded/failed
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None
