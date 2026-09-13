"""Workable global job-board client (SRC-WORKABLE).

Workable is a popular ATS for startups and SMBs. Unlike Greenhouse/Lever
which require per-company board tokens, Workable exposes a SINGLE global
search endpoint that returns jobs across ALL Workable-using companies:

  GET https://jobs.workable.com/api/v1/jobs?query={keywords}&limit={N}

The `companyId` URL parameter exists but is silently ignored — the endpoint
always returns a global search. Each job comes with full company info
inline (no separate detail fetch needed), so we cap list fetches at the
caller's num_results and never hammer the API.

Workplace: jobs have a `workplace` field — values seen: "remote", "hybrid",
"office". `locations` is an array of strings like ["TELECOMMUTE", "Poland"]
where TELECOMMUTE indicates fully-remote-eligible jobs.

Adapted from jobsearch/sources/greenhouse.py — same parallel-fetch +
keyword-filter + location-filter pattern, just simpler (one endpoint,
no per-company iteration).
"""
from __future__ import annotations

import re
import sys
import time
from typing import Optional

from requests import HTTPError, ConnectionError

from ..config import Config
from ..models import Job
from .base import (
    fetch_json, detect_h1b, extract_email, normalize_date, clean_html,
)

_BASE_URL = "https://jobs.workable.com/api/v1/jobs"

# audit P2-3: pagination bounds — the fetch loop follows nextPageToken
# (capped at 5 pages, paced 0.3s) while the post-filter result count is
# below num_results. Previously first-page-only with limit=min(num, 20),
# so a default Remote search returned only the remote fraction of 20 rows.
_MAX_PAGES = 5
_PAGE_PAUSE_S = 0.3


def is_configured(cfg: Config) -> bool:
    """Workable needs no API key — always configured."""
    return True


def _parse_location(job: dict) -> tuple[str, str, bool]:
    """Return (city_str, country_str, is_remote) from a Workable job."""
    loc = job.get("location") or {}
    city = (loc.get("city") or "").strip()
    subregion = (loc.get("subregion") or "").strip()
    country = (loc.get("countryName") or "").strip()
    locations_arr = job.get("locations") or []
    workplace = (job.get("workplace") or "").lower()
    # TELECOMMUTE in locations[] OR workplace == "remote" → remote
    is_remote = (
        "TELECOMMUTE" in locations_arr
        or workplace == "remote"
        or workplace == "fully-remote"
    )
    # Build a city string preferring explicit city, then subregion, then country
    city_part = city or subregion or ""
    return city_part, country, is_remote


def _location_matches(search_loc: str, city: str, country: str,
                      is_remote: bool) -> bool:
    """True if the job's location matches the search location string.

    Mirrors the permissive location matching in greenhouse.py — 'Remote'
    search matches remote jobs + jobs with no location set. Specific city
    searches match city/country substring.
    """
    if not search_loc or search_loc.lower().strip() in ("remote", ""):
        # 'Remote' search: return remote jobs only (we don't return onsite
        # jobs for a remote search — matches the rest of the stack).
        return is_remote
    needle = search_loc.lower().strip()
    haystack = f"{city} {country}".lower()
    return needle in haystack


def _build_job(job: dict) -> Job:
    """Convert a Workable API job dict to our Job model."""
    company_obj = job.get("company") or {}
    company_name = (company_obj.get("title") or "").strip() or "Unknown"
    # If company name still empty, parse it from the URL slug
    if not company_name or company_name == "Unknown":
        url = job.get("url") or ""
        m = re.search(r"-at-([a-z0-9-]+)$", url, re.IGNORECASE)
        if m:
            company_name = m.group(1).replace("-", " ").title()

    city, country, is_remote = _parse_location(job)
    location_str = "Remote" if is_remote else (f"{city}, {country}" if city else country)
    if not location_str:
        location_str = "Unspecified"

    description_html = job.get("description") or ""
    requirements_html = job.get("requirementsSection") or ""
    benefits_html = job.get("benefitsSection") or ""
    full_text = clean_html(description_html + " " + requirements_html + " " + benefits_html)

    apply_url = job.get("url") or ""
    # Workable job URLs redirect to the application form on the company's
    # Workable board — apply_url is canonical.
    posted_at = normalize_date(job.get("created"))
    salary = _extract_salary(description_html + " " + benefits_html)

    return Job(
        source="Workable",
        title=(job.get("title") or "").strip(),
        company=company_name,
        location=location_str,
        link=apply_url,
        description=full_text[:8000] if full_text else "",
        date_posted=posted_at,
        remote=is_remote,
        salary_text=salary,
        h1b_mention=detect_h1b(full_text),
        contact_email=extract_email(full_text),
    )


def _extract_salary(text: str) -> Optional[str]:
    """Heuristic salary extraction from the job description."""
    if not text:
        return None
    # Patterns: "$120k-$160k", "$120,000 - $160,000", "120k-160k USD"
    patterns = [
        r"\$\s*(\d{2,3}(?:[,.]?\d{3})*)\s*k?\s*[-–to]+\s*\$?\s*(\d{2,3}(?:[,.]?\d{3})*)\s*k?\s*/?\s*(?:yr|year|annual|annualy)?",
        r"\$\s*(\d{2,3}(?:[,.]?\d{3})*)\s*k\s*\+?\s*/?\s*(?:yr|year)?",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return None


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg: Config | None = None
          ) -> list[Job]:
    """Search Workable's global job board.

    The endpoint supports `query` (keyword search) + `limit` (page size,
    capped at 20 by the API). audit P2-3: we over-fetch the page limit 2x
    (still capped at 20) to compensate for the client-side filters that run
    AFTER the fetch (location/job_type/date), and follow `nextPageToken`
    (up to 5 pages, paced) until num_results is met or the board is
    exhausted.
    """
    cfg = cfg or Config()
    results: list[Job] = []
    seen_ids: set = set()      # token-loop guard: dedupe rows across pages
    next_token: Optional[str] = None
    for page in range(_MAX_PAGES):
        params: dict = {
            "query": keywords,
            "limit": min(num_results * 2, 20),
        }
        if next_token:
            params["nextPageToken"] = next_token
        try:
            data = fetch_json(_BASE_URL, params=params, cfg=cfg)
        except (HTTPError, ConnectionError) as e:
            if page == 0:
                raise RuntimeError(f"Workable API error: {e}") from None
            # audit P2-3: later-page failure degrades — keep what's collected.
            print(f"[workable] page {page + 1} failed after "
                  f"{len(results)} jobs: {e}", file=sys.stderr)
            break

        jobs_raw = data.get("jobs", []) or []
        new_raw = 0
        for j in jobs_raw:
            jid = str(j.get("id") or j.get("url") or "")
            if jid and jid in seen_ids:
                continue
            if jid:
                seen_ids.add(jid)
            new_raw += 1
            try:
                built = _build_job(j)
            except Exception:
                # Skip a single malformed job; don't crash the whole source
                continue

            # Location filter (client-side — the API's location param was unreliable
            # in probes)
            city, country, is_remote = _parse_location(j)
            if not _location_matches(location, city, country, is_remote):
                continue

            # Date filter
            if date_filter and built.date_posted:
                from .base import is_within_days
                if not is_within_days(built.date_posted, date_filter):
                    continue

            # Job-type filter (mapping is loose — Workable has employmentType field)
            if job_type:
                et = (j.get("employmentType") or "").lower()
                wanted = job_type.lower().replace("_", "-")
                if wanted in ("full-time", "full_time", "fulltime") and "full" not in et:
                    continue
                if wanted in ("part-time", "part_time") and "part" not in et:
                    continue
                if wanted == "contract" and "contract" not in et and "intern" not in et:
                    continue
                if wanted == "internship" and "intern" not in et:
                    continue

            results.append(built)
            if len(results) >= num_results:
                break

        if len(results) >= num_results:
            break
        nt = data.get("nextPageToken")
        next_token = str(nt).strip() if nt else ""
        if not next_token or new_raw == 0:
            break               # board exhausted (or token loop returning dupes)
        time.sleep(_PAGE_PAUSE_S)
    return results
