"""Source registry + parallel aggregator.

Adapted from ResumeWing api/aggregator.py: parallel thread-pool fetch,
failure isolation (one board failing never blocks others), remote-only
board skipping for city searches, freshness sort.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from ..config import Config, load_config
from ..models import Job, SourceResult
from .base import Source
from . import (adzuna, arbeitnow, ashby, careerjet, findwork, glassdoor, greenhouse,
               hn_whos_hiring, jobicy, jooble, jsearch, jobspy_source, lever,
               linkedin_guest, personio, remoteok, remotive, smartrecruiters,
               themuse, usajobs, workable, wellfound, workday, wttj, ziprecruiter)

# Import-time `_C` is kept only as a fallback for sources whose legacy
# `configured: bool` field is consulted (no-key tier). All free-key sources
# now supply `is_configured_fn` which is evaluated at search time so
# runtime env mutations propagate (PR_REVIEW_ARCHITECTURE_v2 P1-1 fix).
_C = load_config()

REGISTRY: dict[str, Source] = {
    # Tier 2 — no key required (built Week 1)
    "Remotive": Source("Remotive", remotive.fetch, remote_only=True),
    "Arbeitnow": Source("Arbeitnow", arbeitnow.fetch),
    "The Muse": Source("The Muse", themuse.fetch),
    "RemoteOK": Source("RemoteOK", remoteok.fetch, remote_only=True),
    "Jobicy": Source("Jobicy", jobicy.fetch, remote_only=True),
    "LinkedIn Guest": Source("LinkedIn Guest", linkedin_guest.fetch),
    "HN Who's Hiring": Source("HN Who's Hiring", hn_whos_hiring.fetch),
    # Tier 1/3 — free key required (lifted from ResumeWing + SRC-FREEKEY).
    # is_configured_fn is evaluated fresh per search_all_sources() call so
    # runtime env mutations propagate (no import-time freeze).
    "Adzuna":    Source("Adzuna",    adzuna.fetch,    is_configured_fn=adzuna.is_configured,
                         hint="ADZUNA_APP_ID + ADZUNA_API_KEY (developer.adzuna.com)"),
    "JSearch":   Source("JSearch",   jsearch.fetch,   is_configured_fn=jsearch.is_configured,
                         hint="JSEARCH_API_KEY (RapidAPI 'JSearch' by OpenWeb Ninja)"),
    "USAJobs":   Source("USAJobs",   usajobs.fetch,   is_configured_fn=usajobs.is_configured,
                         hint="USAJOBS_API_KEY + USAJOBS_USER_AGENT (developer.usajobs.gov)"),
    "Findwork":  Source("Findwork",  findwork.fetch,  is_configured_fn=findwork.is_configured,
                         hint="FINDWORK_API_KEY (findwork.dev)"),
    "Jooble":    Source("Jooble",    jooble.fetch,    is_configured_fn=jooble.is_configured,
                         hint="JOOBLE_API_KEY (jooble.org/api/about)"),
    "Careerjet": Source("Careerjet", careerjet.fetch, is_configured_fn=careerjet.is_configured,
                         hint="CAREERJET_API_KEY (careerjet.com/partners/register/as-publisher)"),
    # ATS-direct — public no-auth JSON endpoints (built SRC-ATS-GL + SRC-ATS-AS)
    "Greenhouse":       Source("Greenhouse",       greenhouse.fetch),
    "Lever":            Source("Lever",            lever.fetch),
    "SmartRecruiters":  Source("SmartRecruiters",  smartrecruiters.fetch),
    # P0 sources — public job boards (SRC-WORKABLE + SRC-WELLFOUND)
    # Workable: global search endpoint, no key, ~170k jobs across all Workable customers
    "Workable":         Source("Workable",         workable.fetch),
    # WTTJ (WelcometotheJungle) — entire index via public Algolia search
    # (~9.9k SE jobs/query, EU-heavy). Creds auto-fetched from /api/env.
    "WTTJ":             Source("WTTJ",             wttj.fetch),
    # Ashby HTML board — server-rendered appData blob, no auth
    # (openai=748 postings, notion=135, ramp, linear verified 2026-08-27)
    "Ashby":            Source("Ashby",            ashby.fetch),
    # Personio XML feed + HTML fallback — DACH ATS (domain-throttled: ≥25s spacing)
    "Personio":         Source("Personio",         personio.fetch),
    # Workday CXS — the biggest missing ATS surface (12,884 seeded tenant
    # boards; S5-2 pilot: NVIDIA board, no-auth POST JSON, 1,428 US
    # full-time roles exhaustively paginated 2026-09-08)
    "Workday":          Source("Workday",          workday.fetch),
    # Wellfound (ex-AngelList Talent) — startup jobs, scraping via Playwright + stealth
    "Wellfound":        Source("Wellfound",        wellfound.fetch,
                                 hint="Playwright + stealth required (Cloudflare blocks plain HTTP)"),
    # ZenRows-transport Cloudflare-walled boards (SRC Step C). NOT in
    # DEFAULT_SOURCES — each request burns ~25+ premium credits; opt in
    # explicitly via --sources ZipRecruiter,Glassdoor.
    "ZipRecruiter":      Source("ZipRecruiter",      ziprecruiter.fetch,
                              is_configured_fn=ziprecruiter.is_configured,
                              hint="ZENROWS_API_KEY (premium proxy + js_render)"),
    "Glassdoor":         Source("Glassdoor",         glassdoor.fetch,
                              is_configured_fn=glassdoor.is_configured,
                              hint="ZENROWS_API_KEY (premium proxy + js_render)"),
    # Aggregator wrapper (SRC-JOBSPY-2) — pip install python-jobspy (NOT bare jobspy)
    "JobSpy":           Source("JobSpy", jobspy_source.fetch,
                              is_configured_fn=jobspy_source.is_configured,
                              hint="pip install python-jobspy (NOT 'jobspy' which is a different Redis package)"),
}

DEFAULT_SOURCES = ["Remotive","Arbeitnow","The Muse","RemoteOK","Jobicy",
                   "LinkedIn Guest","HN Who's Hiring","Adzuna","JSearch","USAJobs",
                   "Findwork","Jooble","Careerjet",
                   "Greenhouse","Lever","SmartRecruiters","Workable","Wellfound",
                   "WTTJ","Ashby","Personio","Workday",
                   "JobSpy"]


def search_all_sources(keywords: str, location: str = "Remote",
                       sources: list[str] | None = None,
                       num_per_source: int = 20,
                       cfg: Config | None = None) -> list[SourceResult]:
    """Fetch all requested boards in parallel. Failures degrade, never crash (D3)."""
    cfg = cfg or Config()
    requested = sources or DEFAULT_SOURCES
    is_remote_search = location.lower().strip() in ("remote", "")

    tasks: list[tuple[str, Callable[[], list[Job]]]] = []
    for name in requested:
        src = REGISTRY.get(name)
        if src is None:
            continue
        if src.remote_only and not is_remote_search:
            continue
        if not src.is_configured(cfg):
            continue
        tasks.append((name, lambda s=src: s.fetch(
            keywords, location, num_per_source, cfg=cfg)))

    results: list[SourceResult] = []

    def _timed_fetch(name: str, fn: Callable[[], list[Job]]) -> SourceResult:
        """Fetch inside the worker thread so duration reflects real latency
        (as_completed yields only AFTER completion — timing there measures ~0)."""
        import time
        from .. import trust as trust_mod
        result = SourceResult(source=name)
        t0 = time.monotonic()
        try:
            result.jobs = fn()
        except Exception as exc:  # noqa: BLE001 — deliberate isolation
            result.error = f"{type(exc).__name__}: {exc}"[:300]
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        # Step D: per-job trust enrichment (flag, never drop — T2 semantics).
        # Sprint 3: categorization enrichment in the same pass (tier from
        # title, skills from title+description, work_mode, ats_platform from
        # the link) — pure computation, no network, failure-isolated per job.
        from .. import trust as trust_mod
        from .. import categorize as categorize_mod
        from .. import ats_resolve
        for j in result.jobs:
            t = trust_mod.score_job(j.link, j.company)
            j.trust_score = t["score"]
            j.trust_flags = t["flags"]
            j.trust_level = t["level"]
            cat = categorize_mod.categorize(
                j.title, j.location, j.description, remote_hint=j.remote)
            j.tier = cat["tier"]
            j.skills = cat["skills"] or None
            j.work_mode = cat["work_mode"]
            j.ats_platform = ats_resolve.detect_platform(j.link)[0]
        return result

    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        futures = {pool.submit(_timed_fetch, name, fn): name for name, fn in tasks}
        for fut in as_completed(futures):
            results.append(fut.result())
    return results


def all_source_names() -> list[str]:
    return list(REGISTRY.keys())
