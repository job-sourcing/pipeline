"""SmartRecruiters ATS-direct client.

Public endpoint (no API key required):
    GET https://api.smartrecruiters.com/v1/companies/{slug}/postings
        ?limit=N&offset=0&q={keywords}

Discovery (SRC-ATS-AS): the main-agent's slug `smartrecruiters` was correct,
but the path `/v1/companies/{slug}/jobs` is dead — the load balancer rewrites
it to `/public-posting-api/api-v1/companies/{slug}/jobs`, which returns HTTP
404 with body "Cannot GET /public-posting-api/api-v1/companies/{slug}/jobs".
The current public endpoint is `/v1/companies/{slug}/postings` (plural
"postings" not "jobs") — verified 200 OK with content for slugs:
  smartrecruiters (~8), averydennison (~400+), geico, bigcommerce,
  wework, yardi.

Response shape (verified against the live API):
    {
      "offset": int, "limit": int, "totalFound": int,
      "content": [
        {
          "id": "744000143115219",
          "name": "Senior Information Security Engineer",   # job title
          "company": {"identifier": "smartrecruiters",
                      "name": "SmartRecruiters Inc"},
          "releasedDate": "2026-08-12T14:04:56.128Z",       # ISO 8601
          "location": {
            "city": "Poland", "region": "REMOTE", "country": "pl",
            "remote": true, "hybrid": false,
            "fullLocation": "Poland, REMOTE, Poland"
          },
          "function":        {"id": "engineering",  "label": "Engineering"},
          "department":      {"id": "5408693",      "label": "Engineering"},
          "industry":        {"id": "computer_software",
                              "label": "Computer Software"},
          "typeOfEmployment":{"id": "permanent",    "label": "Full-time"},
          "experienceLevel": {"id": "mid_senior_level",
                              "label": "Mid-Senior Level"},
          "customField": [{"fieldLabel": "...", "valueLabel": "..."}, ...],
          "visibility": "PUBLIC",
          "ref": "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{id}"
        }
      ]
    }

A 200-OK response with `totalFound=0` is NOT an error (the slug exists on SR
but the company currently has zero public postings) — `_fetch_one` returns
[] and the per-slug thread completes normally. 4xx/5xx → RuntimeError,
isolated per-slug inside `fetch()` and again at the aggregator level.

Per the Greenhouse/Lever pattern: one slug = one company ATS board, the
source string is `f"SmartRecruiters.{slug}"` so downstream dedup can
attribute jobs back to the originating company board.
"""
from __future__ import annotations

import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from requests import HTTPError, ConnectionError

from ..config import Config
from ..models import Job
from .base import (
    fetch_json, detect_h1b, extract_email, normalize_date,
)

_BASE_URL = "https://api.smartrecruiters.com/v1/companies"

# audit P2-3: pagination bounds — per slug, advance offset by limit while
# the slug's collected count is below its per_slug share and offset is
# still below totalFound, capped at 3 pages per slug, paced 0.3s.
# Previously one request per slug with totalFound ignored (averydennison
# ~400 postings → only per_slug newest-ish rows ever seen).
_MAX_PAGES_PER_SLUG = 3
_PAGE_PAUSE_S = 0.3


def fetch(keywords: str, location: str = "Remote", num_results: int = 20,
          date_filter: Optional[int] = None, job_type: Optional[str] = None,
          cfg=None) -> list[Job]:
    """Iterate cfg.smartrecruiters_slugs in parallel; per-slug failure isolation.

    Each slug is one company's ATS board on SmartRecruiters. We fan out N
    slug-queries in parallel (one ThreadPoolExecutor with min(8, n) workers),
    collect results, and truncate to `num_results`. A failing slug degrades
    (logged to stderr) but never crashes the whole fetch (D3 isolation).
    """
    cfg = cfg or Config()
    slugs = cfg.smartrecruiters_slugs
    if not slugs:
        return []

    # Spread num_results across the configured slugs (ceiling division so we
    # never under-fetch — extra rows are truncated at the end). At least 1
    # per slug so a thin board can still contribute.
    per_slug = max(1, math.ceil(num_results / len(slugs)))

    jobs: list[Job] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, len(slugs))) as pool:
        futures = {pool.submit(_fetch_one, slug, keywords, per_slug, cfg): slug
                   for slug in slugs}
        for fut in as_completed(futures):
            slug = futures[fut]
            try:
                jobs.extend(fut.result())
            except Exception as exc:  # noqa: BLE001 — defensive
                # Per-slug hard failure (5xx, network) — collect for the
                # all-slugs-failed aggregated error below (D3 policy: a fully
                # failed source must NOT silently return []).
                msg = f"{slug}: {type(exc).__name__}: {exc}"
                errors.append(msg)
                print(f"[smartrecruiters] {msg}", file=sys.stderr)
                continue

    # D3 policy: if every slug failed, surface the failure to the parent
    # aggregator's SourceResult.error — do NOT silently return [].
    if not jobs and errors:
        agg = (f"SmartRecruiters: all {len(errors)} slug(s) failed: "
               + "; ".join(errors))[:300]
        raise RuntimeError(agg)

    # Sort newest-first (releasedDate normalized to YYYY-MM-DD by _normalize).
    # Mirrors the Greenhouse/Lever convention so cross-source ordering stays
    # consistent for downstream dedup + scoring.
    jobs.sort(key=lambda j: j.date_posted or "", reverse=True)
    return jobs[:num_results]


def _fetch_one(slug: str, keywords: str, limit: int,
              cfg: Config) -> list[Job]:
    """Fetch one company's postings (audit P2-3: paginated); raises on hard
    failure (5xx, network), returns [] only for a first-page 404.

    Mirrors the Greenhouse/Lever contract: 404 (slug doesn't exist on SR) is
    a soft skip — return []; 5xx and ConnectionError propagate so the parent
    fetch() can collect them into the all-slugs-failed aggregated RuntimeError
    (D3 policy: a fully failed source must NOT silently return []). A
    later-page failure degrades instead — the rows already collected for
    this slug are kept.
    """
    jobs: list[Job] = []
    page_limit = min(limit, 100)
    offset = 0
    for page in range(_MAX_PAGES_PER_SLUG):
        params: dict = {"limit": page_limit, "offset": offset}
        if keywords:
            params["q"] = keywords
        try:
            data = fetch_json(f"{_BASE_URL}/{slug}/postings",
                             params=params, cfg=cfg)
        except HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else 0
            if code == 404:
                # Slug not registered on SmartRecruiters — soft skip (matches
                # the Greenhouse/Lever 404 convention).
                if page == 0:
                    return []
                break              # later-page 404 — keep what's collected
            if page == 0:
                raise RuntimeError(
                    f"SmartRecruiters.{slug} API error (HTTP {code})"
                ) from exc
            # audit P2-3: later-page failure degrades — keep this slug's rows.
            print(f"[smartrecruiters] {slug} page {page + 1} failed after "
                  f"{len(jobs)} jobs: {exc}", file=sys.stderr)
            break
        except ConnectionError as exc:
            if page == 0:
                raise RuntimeError(
                    f"SmartRecruiters.{slug}: no internet connection"
                ) from exc
            print(f"[smartrecruiters] {slug} page {page + 1} failed after "
                  f"{len(jobs)} jobs: {exc}", file=sys.stderr)
            break

        content = data.get("content", []) or []
        total_found = 0
        try:
            total_found = int(data.get("totalFound") or 0)
        except (TypeError, ValueError):
            total_found = 0

        for item in content:
            job = _normalize(item, slug, keywords)
            if job:
                jobs.append(job)
                if len(jobs) >= limit:
                    break

        if len(jobs) >= limit:
            break
        # audit P2-3: stop when the board signals there is nothing more.
        if not content or len(content) < page_limit:
            break               # empty/short page — board exhausted
        if total_found and offset + page_limit >= total_found:
            break               # totalFound consumed
        offset += page_limit
        time.sleep(_PAGE_PAUSE_S)   # polite pacing between pages
    return jobs


def _normalize(item: dict, slug: str, keywords: str) -> Optional[Job]:
    """Map one SR posting row → Job. Returns None if title/company missing."""
    title = (item.get("name") or "").strip()
    company = ((item.get("company") or {}).get("name") or "").strip()
    if not title or not company:
        return None

    loc = item.get("location") or {}
    full_location = (loc.get("fullLocation") or "").strip()
    is_remote = bool(loc.get("remote")) or "remote" in full_location.lower()

    # The SR listing endpoint exposes no free-text job description; the
    # posting body lives on the per-id detail endpoint. We synthesize a
    # short description from the structured fields so the row carries enough
    # signal for h1b_mention detection and TF-IDF scoring downstream.
    function = ((item.get("function") or {}).get("label") or "").strip()
    department = ((item.get("department") or {}).get("label") or "").strip()
    industry = ((item.get("industry") or {}).get("label") or "").strip()
    type_emp = ((item.get("typeOfEmployment") or {}).get("label") or "").strip()
    experience = ((item.get("experienceLevel") or {}).get("label") or "").strip()
    custom_fields = item.get("customField") or []
    custom_text = " ".join(
        (cf.get("valueLabel") or "")
        for cf in custom_fields
        if cf.get("valueLabel")
    ).strip()
    desc_parts = [p for p in (department, function, industry, type_emp,
                              experience, custom_text) if p]
    description = " · ".join(desc_parts)[:500]   # SRC-ATS-AS: trim to 500 chars

    # Synthesize the candidate-facing careers URL (the SR API's `ref` field
    # is the API endpoint, not the human-readable apply page).
    job_id = item.get("id")
    apply_url = (f"https://careers.smartrecruiters.com/{slug}/{job_id}"
                 if job_id else (item.get("ref") or ""))

    return Job(
        title=title,
        company=company,
        description=description,
        link=apply_url,
        contact_email=extract_email(description),   # rarely present; harmless
        source=f"SmartRecruiters.{slug}",
        search_query=keywords,
        location=full_location,
        date_posted=normalize_date(item.get("releasedDate")),
        remote=is_remote,
        h1b_mention=detect_h1b(f"{title} {description}"),
        salary_text=None,   # SR doesn't expose comp on the listing endpoint
    )
