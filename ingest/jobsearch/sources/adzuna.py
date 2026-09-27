"""Adzuna client — lifted from resume-wing api/adzuna.py.

Adzuna is a broad US market aggregator; redirect_url is Adzuna-hosted and
validated (doesn't 404), with structured salary_min/salary_max and a
reliable created date on every row. Free tier: 250 requests/day. Get both
keys (app_id + app_key) at developer.adzuna.com.

Adapted to our conventions: cfg carries the credentials (no module-level
imports from config), fetch_json is the mockable HTTP seam, and the
per-source is_configured(cfg) helper feeds the registry's configured-flag.
"""
from __future__ import annotations

from typing import Optional

from requests import HTTPError, ConnectionError

from ..config import Config
from ..models import Job
from .base import (
    fetch_json, detect_h1b, extract_email, normalize_date, salary_text,
)

_BASE_URL = "https://api.adzuna.com/v1/api/jobs"

# Maps our internal job_type to Adzuna's per-type flag params.
_JOB_TYPE_PARAMS = {
    "Full-time":  {"full_time": 1},
    "Part-time":  {"part_time": 1},
    "Contract":   {"contract": 1},
    "Internship": {"contract": 1},
    "Remote":     {"what_and": "remote"},
}


def is_configured(cfg: Config) -> bool:
    return bool(cfg.adzuna_app_id and cfg.adzuna_api_key)


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg=None) -> list[Job]:
    """Search Adzuna for jobs matching keywords + location.

    Raises RuntimeError on missing credentials or HTTP failure so the
    aggregator (search_all_sources) isolates the failure into SourceResult.
    """
    cfg = cfg or Config()
    if not is_configured(cfg):
        raise RuntimeError(
            "Adzuna credentials not configured. "
            "Set ADZUNA_APP_ID and ADZUNA_API_KEY (free at developer.adzuna.com)."
        )

    country = _detect_country(location)
    per_page = min(num_results, 50)              # Adzuna max per page = 50
    jobs: list[Job] = []

    # Always enforce a freshness window. When the caller doesn't specify one,
    # cap at 45 days — the single most effective way to avoid Adzuna
    # "redirect_url exists but job closed on the employer's ATS" dead links.
    effective_date_filter = date_filter if date_filter else 45

    for page in range(1, 4):                    # cap at 3 pages (rate-limit safety)
        if len(jobs) >= num_results:
            break
        params: dict = {
            "app_id": cfg.adzuna_app_id,
            "app_key": cfg.adzuna_api_key,
            "results_per_page": per_page,
            "what": keywords,
            "sort_by": "date",                  # always sort by freshness
            "content-type": "application/json",
            "max_days_old": effective_date_filter,
        }
        # Adzuna's `where` parameter expects a city/region — passing "Remote"
        # returns 0 results (it's not a location keyword). For 'Remote'
        # searches we skip `where` entirely and filter client-side via the
        # "remote" keyword appearing in description + title.
        if location and location.lower().strip() not in ("remote", ""):
            params["where"] = location
        if job_type and job_type in _JOB_TYPE_PARAMS:
            params.update(_JOB_TYPE_PARAMS[job_type])

        try:
            data = fetch_json(f"{_BASE_URL}/{country}/search/{page}",
                              params=params, cfg=cfg)
        except HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else 0
            if code == 401:
                raise RuntimeError(
                    "Adzuna: Invalid credentials. Check ADZUNA_APP_ID/ADZUNA_API_KEY."
                )
            raise RuntimeError(f"Adzuna API error (HTTP {code})")
        except ConnectionError:
            raise RuntimeError("Adzuna: No internet connection.")

        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            break
        for item in results:
            if len(jobs) >= num_results:
                break
            job = _normalize(item, keywords, location)
            # Client-side remote filter: when searching 'Remote', skip jobs
            # whose description/title don't mention "remote"
            if (not job or (location and location.lower().strip() == "remote"
                            and not job.remote)):
                continue
            if job:
                jobs.append(job)
    return jobs


def _normalize(item: dict, keywords: str, location: str) -> Optional[Job]:
    title = (item.get("title") or "").strip()
    apply_link = item.get("redirect_url", "")
    if not title or not apply_link:
        return None

    description = item.get("description", "") or ""
    company = (item.get("company") or {}).get("display_name", "Unknown")
    loc_data = item.get("location") or {}
    job_location = loc_data.get("display_name", location)

    sal_min = item.get("salary_min")
    sal_max = item.get("salary_max")
    currency = "GBP" if _detect_country(location) == "gb" else "USD"

    return Job(
        title=title,
        company=company,
        description=description,
        link=apply_link,
        contact_email=extract_email(description),
        source="Adzuna",
        search_query=keywords,
        location=job_location,
        date_posted=normalize_date(item.get("created")),
        salary_text=salary_text(sal_min, sal_max, currency),
        salary_min=float(sal_min) if sal_min else None,
        salary_max=float(sal_max) if sal_max else None,
        remote="remote" in (description + title).lower(),
        h1b_mention=detect_h1b(description),
    )


def _detect_country(location: str) -> str:
    """Infer Adzuna country code from location string. Defaults to 'us'."""
    loc = (location or "").lower()
    if any(x in loc for x in ("uk", "england", "london", "manchester",
                              "birmingham", "glasgow")):
        return "gb"
    if any(x in loc for x in ("canada", "toronto", "vancouver", "montreal", "calgary")):
        return "ca"
    if any(x in loc for x in ("australia", "sydney", "melbourne", "brisbane", "perth")):
        return "au"
    if any(x in loc for x in ("india", "bangalore", "bengaluru", "mumbai",
                              "delhi", "hyderabad")):
        return "in"
    if any(x in loc for x in ("germany", "berlin", "munich", "hamburg", "frankfurt")):
        return "de"
    if any(x in loc for x in ("france", "paris", "lyon", "marseille")):
        return "fr"
    if any(x in loc for x in ("netherlands", "amsterdam", "rotterdam")):
        return "nl"
    if "singapore" in loc:
        return "sg"
    return "us"
