"""Facet-01 JSONL export contract — the durable corpus shape (Wave-R R2).

Source of truth for the consumer side: the MAIN repo's facet-01 loader
(`facets/01_profile_foundation/build/profile_foundation/corpus.py`,
`load_postings_jsonl` — pinned 2026-09-16). One JSON object per line:

  REQUIRED    title          (a line without it fails the consumer's load)
  RECOMMENDED job_id         stable id — see `stable_job_id` below
  OPTIONAL    description, company, location, experience_level, work_type,
              remote_allowed, salary_min, salary_med, salary_max, currency,
              pay_period, skills, listed_time, source

Field mapping from the ingest `Job` model:

  title             ← title
  job_id            ← `stable_job_id(job)` (see below)
  description       ← description (an EXPORT-ONLY truncation knob,
                      `max_description_chars`, exists for committed-corpus
                      size control — the DB row is never modified)
  company           ← company
  location          ← location
  experience_level  ← tier ('intern' | 'entry' | 'mid' | 'senior' — the
                      ingest enum, NOT LinkedIn-style labels; note
                      classify_tier defaults plain titles to 'mid')
  work_type         ← work_mode ('remote' | 'hybrid' | 'onsite'; 'unknown'
                      exports as null — the model has no employment-type
                      field, so work_type carries the work MODE)
  remote_allowed    ← remote (always a real JSON bool)
  salary_min/max    ← salary_min/salary_max (raw, source-native figures)
  currency          ← 'USD' when the board is known-USD (USAJobs); 'EUR'
                      when known-EUR (WTTJ — the facet-01 loader EXCLUDES
                      non-USD bands); otherwise the band is DROPPED
                      (salary_min/max null, salary_text kept) so no number
                      ever rides the consumer's default-USD assumption
                      (S15-R4 P1-1)
  pay_period        ← never emitted (the model does not carry it)
  skills            ← skills (real JSON array; [] when absent)
  listed_time       ← date_posted ('YYYY-MM-DD' or null)
  source            ← source

Provenance extras (unknown keys are dropped by the facet-01 loader; other
readers keep the traceability): link, tier, work_mode, date_posted,
salary_text, ats_platform, remote, row_id (the tracker.db rowid — NOT
stable across DB rebuilds; use job_id for identity), scraped_at,
search_query, ghost_candidate, repost_count, description_truncated.
Run-local scoring artifacts (llm_*, trust_*, tfidf_score, scored_at) are
deliberately NOT exported — they are per-resume opinions, not corpus data.

`stable_job_id` derivation — the Job model carries no separate native-id
column; the native id, when one exists, lives embedded in the apply URL.
Precedence:

  1. URL-pattern extraction (`_NATIVE_ID_PATTERNS`): the id the board
     itself uses — LinkedIn numeric id, ATS reqId/UUID (Greenhouse —
     including the gh_jid param on company careers domains, Lever, Ashby,
     Personio, SmartRecruiters, Workable view key), board posting id
     (Wellfound, Remotive, RemoteOK, Arbeitnow, Jobicy, Careerjet,
     Adzuna, USAJobs, HN, WTTJ slug, Workday reqId) — emitted as
     '<board>-<native id>' so numeric ids from different boards can never
     collide in a merged corpus.
  2. URL hash: sources whose apply URLs carry no stable id (Jooble
     redirects, Findwork/TheMuse referrer links, …) get
     'u-<sha1(link)[:16]>'. Stable across runs, NOT board-native.
  3. Content fingerprint: linkless rows (the same rows upsert keys on the
     (source, title, company) fingerprint) get
     'c-<sha1(source|company|title)[:16]>'. Stable across runs for the
     same posting content, NOT board-native.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

from .models import Job

# board key → first capture group = the board's own id. Order matters only
# for readability; patterns are mutually exclusive by hostname.
_NATIVE_ID_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("linkedin", re.compile(
        r"linkedin\.com/(?:[^/?#]+/)?jobs/view/(\d+)")),
    ("linkedin", re.compile(
        r"linkedin\.com/jobs-guest/jobs/api/jobPosting/(\d+)")),
    ("greenhouse", re.compile(
        r"(?:boards|job-boards)\.greenhouse\.io/[^/]+/jobs/(\d+)")),
    # Greenhouse postings served from company careers domains carry the
    # native id as the gh_jid query param (datadog/coinbase/asana/…).
    ("greenhouse", re.compile(r"[?&]gh_jid=(\d+)")),
    ("lever", re.compile(
        r"jobs\.lever\.co/[^/]+/([0-9a-fA-F-]{8,})")),
    ("ashby", re.compile(
        r"jobs\.ashbyhq\.com/[^/]+/([0-9a-fA-F-]{8,})")),
    ("personio", re.compile(
        r"\.jobs\.personio\.[a-z.]+/job/(\d+)")),
    ("smartrecruiters", re.compile(
        r"smartrecruiters\.com/[^/]+/(\d+)")),
    ("workable", re.compile(
        r"jobs\.workable\.com/view/([A-Za-z0-9_-]+)")),
    ("wellfound", re.compile(
        r"(?:wellfound|angel)\.(?:co|com)/jobs/(\d+)")),
    ("remotive", re.compile(
        r"remotive\.com/(?:remote-)?jobs/(\d+)")),
    ("remotive", re.compile(
        r"remotive\.com/(?:remote-)?jobs/(?:[^/?#]+/)?[^/?#]*-(\d+)(?:[?/#]|$)")),
    ("remoteok", re.compile(
        r"remoteok\.com/(?:remote-)?jobs?/(\d+)", re.I)),
    ("remoteok", re.compile(
        r"remoteok\.com/(?:remote-)?jobs?/[^/?#]*-(\d+)(?:[?/#]|$)", re.I)),
    ("arbeitnow", re.compile(
        r"arbeitnow\.com/jobs/(?:companies/[^/]+/)?[^/?#]*-(\d+)(?:[?/#]|$)")),
    ("jobicy", re.compile(
        r"jobicy\.com/jobs2?/(\d+)")),
    ("findwork", re.compile(
        r"findwork\.dev/([A-Za-z0-9]+)(?:[/?#]|$)")),
    ("careerjet", re.compile(
        r"careerjet\.[a-z.]+/jobad/(\d+)")),
    ("adzuna", re.compile(
        r"adzuna\.[a-z.]+/details/(\d+)")),
    ("usajobs", re.compile(
        r"usajobs\.gov(?::\d+)?/(?:job/|GetJob/ViewDetails/)(\d+)")),
    ("hn", re.compile(
        r"news\.ycombinator\.com/item\?id=(\d+)")),
    ("workday", re.compile(
        r"myworkdayjobs\.com/.*?/candidate/job/(\d+)")),
    # site-path format: /job/[location/]title-slug_reqId — the reqId is
    # the token after the FINAL underscore of the last path segment
    # (e.g. 'Senior-Software-Engineer--AI-Agent-Compute_JR2021516-1').
    ("workday", re.compile(
        r"myworkdayjobs\.com/.*?/job/(?:[^/?#]+/)?[^/?#]*_([A-Za-z0-9]+(?:-[0-9]+)?)")),
    ("wttj", re.compile(
        r"welcometothejungle\.com/[^/]+/companies/[^/]+/jobs/([^/?#]+)")),
    # Jooble path id — the URL also carries volatile rank/page/query params
    # (pos/p/ckey) that made the URL-hash fallback unstable across runs
    ("jooble", re.compile(r"jooble\.org/(?:desc|away)/(-?\d+)")),
)

# Boards whose numeric bands are known-USD / known-EUR at the source.
_KNOWN_USD = (re.compile(r"usajobs\.gov/", re.I),)
_KNOWN_EUR = (re.compile(r"welcometothejungle\.com/", re.I),)


def _sha1_16(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def native_job_id(link: str) -> Optional[str]:
    """Board-native id from an apply URL, as '<board>-<id>' — else None."""
    if not link:
        return None
    for board, pattern in _NATIVE_ID_PATTERNS:
        m = pattern.search(link)
        if m:
            return f"{board}-{m.group(1)}"
    return None


def stable_job_id(job: Job) -> str:
    """Stable job_id for one posting (see module docstring for precedence).

    Never empty — the facet-01 loader synthesizes 'line-N' for missing
    ids, which defeats cross-run dedup, so every row gets one of:
    native '<board>-<id>' / URL hash 'u-<hex16>' / fingerprint 'c-<hex16>'.
    """
    native = native_job_id(job.link or "")
    if native:
        return native
    if job.link:
        return f"u-{_sha1_16(job.link)}"
    return ("c-" +
            _sha1_16(f"{job.source}|{job.company}|{job.title}"))


def facet01_row(job: Job,
                max_description_chars: Optional[int] = None) -> dict:
    """Map one Job onto the facet-01 JSONL contract row (see docstring).

    `max_description_chars` truncates the EXPORTED description only —
    `job.description` (and the DB row behind it) is never modified.
    """
    description = job.description or ""
    truncated = False
    if (max_description_chars is not None and max_description_chars > 0
            and len(description) > max_description_chars):
        description = description[:max_description_chars]
        truncated = True

    currency = None
    drop_bands = False
    if job.salary_min is not None or job.salary_max is not None:
        link = job.link or ""
        if any(p.search(link) for p in _KNOWN_USD):
            currency = "USD"
        elif any(p.search(link) for p in _KNOWN_EUR):
            # marked non-USD: the facet-01 loader EXCLUDES such bands
            currency = "EUR"
        else:
            # Unknown currency: raw numbers would ride the consumer's
            # default-USD assumption and mis-aggregate (S15-R4 P1-1) —
            # drop the band, keep salary_text for humans.
            drop_bands = True

    # work_mode 'unknown' means "no signal" — export null, not noise.
    work_mode = job.work_mode if job.work_mode != "unknown" else None

    return {
        # ── the contract ────────────────────────────────────────────
        "title": job.title,
        "job_id": stable_job_id(job),
        "description": description,
        "company": job.company or None,
        "location": job.location or None,
        "experience_level": job.tier,
        "work_type": work_mode,
        "remote_allowed": bool(job.remote),
        "salary_min": None if drop_bands else job.salary_min,
        "salary_max": None if drop_bands else job.salary_max,
        "currency": currency,
        "skills": list(job.skills) if job.skills else [],
        "listed_time": job.date_posted,
        "source": job.source,
        # ── provenance extras (dropped by the facet-01 loader) ───────
        "link": job.link or None,
        "tier": job.tier,
        "work_mode": job.work_mode,
        "date_posted": job.date_posted,
        "salary_text": job.salary_text,
        "ats_platform": job.ats_platform,
        "remote": bool(job.remote),
        "row_id": job.id,
        "scraped_at": job.scraped_at,
        "search_query": job.search_query,
        "ghost_candidate": job.ghost_candidate,
        "repost_count": job.repost_count,
        "description_truncated": truncated,
    }


__all__ = [
    "facet01_row",
    "native_job_id",
    "stable_job_id",
]
