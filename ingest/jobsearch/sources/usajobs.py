"""USAJobs client — lifted from resume-wing api/usajobs.py.

USAJobs is the official US federal-government job board. Links are always
valid (hosted on usajobs.gov), never 404. Completely unique in the stack —
no other source covers federal roles; ideal for users near DC/VA/MD or any
federal agency/military installation.

Get a key (free, instant): developer.usajobs.gov/apirequest → Register →
copy Authorization-Key. Your registered email MUST be the User-Agent header
value — set both USAJOBS_API_KEY and USAJOBS_USER_AGENT.

Adapted: cfg carries the credentials; fetch_json is the mockable seam.
Our Job model has no expires_at field, so ApplicationCloseDate (an explicit
federal close date) is dropped — documented adaptation (could be appended to
description in a future iteration).
"""
from __future__ import annotations

import time
from typing import Optional

from requests import HTTPError, ConnectionError

from ..config import Config
from ..models import Job
from ..transport import netlify_fetch_json, zenrows_fetch_json
from .base import (
    fetch_json, detect_h1b, normalize_date, salary_text,
)

_BASE_URL = "https://data.usajobs.gov/api/search"

# Pagination cap: bounds the worst case for heavily-filtered remote searches
# (each page is one request; pages are paced 1s apart).
_MAX_PAGES = 5


def is_configured(cfg: Config) -> bool:
    return bool(cfg.usajobs_api_key and cfg.usajobs_user_agent)


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg=None) -> list[Job]:
    """Search USAJobs for federal-government positions."""
    cfg = cfg or Config()
    if not is_configured(cfg):
        raise RuntimeError(
            "USAJobs credentials not configured. "
            "Set USAJOBS_API_KEY and USAJOBS_USER_AGENT (your email) — "
            "free at developer.usajobs.gov/apirequest."
        )

    headers = {
        "Authorization-Key": cfg.usajobs_api_key,
        "User-Agent": cfg.usajobs_user_agent,
        "Host": "data.usajobs.gov",
    }
    # LocationName must be a real place name — "Remote" matches nothing on
    # USAJobs (verified live 2026-08-27: LocationName=Remote → 0 hits). The
    # API's RemoteIndicator param is ALSO dead as of 2026-08-29 (verified
    # live: RemoteIndicator=true → 0 results on EVERY query, including
    # ones with remote listings). Remote searches therefore run nationwide
    # and rely on the client-side remote filter + pagination to surface
    # remote-eligible listings (~1-2 per 25; the pager collects them).
    params: dict = {
        "Keyword": keywords,
        "SortField": "OpenDate",
        "SortDirection": "Desc",
    }
    loc_norm = (location or "").strip().lower()
    if loc_norm and loc_norm not in ("remote", ""):
        params["LocationName"] = location
    if date_filter:
        # USAJobs DatePosted accepts a number-of-days integer.
        params["DatePosted"] = date_filter
    if job_type:
        sched = _map_job_type(job_type)
        if sched:
            params["PositionSchedule"] = sched

    # Over-fetch + paginate (CodeRabbit round-3 finding): client-side
    # filtering (remote belt-and-braces + shape validation in _normalize)
    # drops rows, so a single num_results-sized page can return fewer jobs
    # than requested. Page until num_results is satisfied or the API is
    # exhausted; per-page over-fetch (2x, floor 25, cap 100 — API max is
    # 500 but we stay polite) makes one request enough in the common case,
    # and a hard page cap bounds the worst case for heavily-filtered
    # remote searches.
    per_page = min(max(num_results * 2, 25), 100)
    remote_only = (location or "").strip().lower() in ("remote", "")
    jobs: list[Job] = []
    page = 1
    while page <= _MAX_PAGES and len(jobs) < num_results:
        params["ResultsPerPage"] = per_page
        params["Page"] = page
        data = _fetch_page(params, headers, cfg)
        search_result = ((data or {}).get("SearchResult", {})
                         if isinstance(data, dict) else {})
        items = search_result.get("SearchResultItems", [])
        if not items:
            break
        for item in items:
            if len(jobs) >= num_results:
                break
            job = _normalize(item, keywords, location)
            if job is None:
                continue
            if remote_only and not job.remote:
                continue
            jobs.append(job)
        if len(items) < per_page:
            break               # last page — API exhausted
        page += 1
        if page <= _MAX_PAGES and len(jobs) < num_results:
            time.sleep(1.0)     # pacing between pages (federal API politeness)
    return jobs


def _fetch_page(params: dict, headers: dict, cfg: Config) -> dict:
    """One search request, with the US-proxy 403 fallback chain.

    Fallback order (2026-08-29): Netlify edge scraper FIRST (free, US
    egress — ZenRows credits are exhausted), ZenRows second (premium
    residential proxy, costs credits). Both forward our headers verbatim.
    """
    try:
        return fetch_json(_BASE_URL, params=params, headers=headers, cfg=cfg)
    except HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 0
        if code == 403:
            # Akamai geo-block (HK datacenter egress). Strip hop-by-hop/host
            # headers: the proxied request now targets a different host and
            # a mismatched Host would be rejected.
            proxied_headers = {k: v for k, v in headers.items()
                               if k.lower() not in ("host",)}
            last_error: Exception | None = None
            # 1) Netlify edge scraper (US egress, free) — verified live
            #    2026-08-29: direct 403 → Netlify fetch engine 200 with the
            #    re-registered base64 key.
            if cfg.netlify_scraper_token:
                try:
                    return netlify_fetch_json(
                        _BASE_URL, params=params, headers=proxied_headers,
                        cfg=cfg)
                except (HTTPError, RuntimeError) as proxied:
                    last_error = proxied
            # 2) ZenRows premium US proxy — verified live 2026-08-27
            #    (direct 403 → proxied 200 with the then-current key).
            if cfg.zenrows_api_key:
                try:
                    return zenrows_fetch_json(
                        _BASE_URL, params=params, headers=proxied_headers,
                        cfg=cfg, proxy_country="us")
                except (HTTPError, RuntimeError) as proxied:
                    last_error = proxied
            # Chain the proxied failure so auth errors (401/402 from the
            # target or the proxy) surface with their real status — the
            # live-test skip policy keys off these markers (auth/credit
            # failures must FAIL, not skip). Only the direct 403 is
            # claimed as geo-blocking.
            if last_error is not None:
                raise RuntimeError(
                    "USAJobs: direct request geo-blocked (HTTP 403 from HK "
                    "egress) and the US-proxy transports also failed: "
                    f"{last_error}"
                ) from last_error
            raise RuntimeError(
                "USAJobs: direct request geo-blocked (HTTP 403 from HK "
                "egress) and no US-proxy transport is configured — set "
                "NETLIFY_SCRAPER_TOKEN (free) or ZENROWS_API_KEY."
            )
        elif code == 401:
            raise RuntimeError(
                "USAJobs: credentials rejected (HTTP 401) — the API key is "
                "invalid or deactivated. Re-register via "
                "scripts/usajobs_reregister.py (USAJOBS_USER_AGENT must be "
                "the key's registered email)."
            )
        else:
            raise RuntimeError(f"USAJobs API error (HTTP {code})")
    except ConnectionError:
        raise RuntimeError("USAJobs: No internet connection.")


def _normalize(item: dict, keywords: str, location: str) -> Optional[Job]:
    descriptor = item.get("MatchedObjectDescriptor", {}) or {}

    title = (descriptor.get("PositionTitle") or "").strip()
    uris = descriptor.get("ApplyURI") or []
    apply_link = uris[0] if uris else ""

    if not title or not apply_link:
        return None

    # USAJobs salary comes from the PositionRemuneration list.
    remuneration = (descriptor.get("PositionRemuneration") or [{}])[0] or {}
    sal_min = remuneration.get("MinimumRange")
    sal_max = remuneration.get("MaximumRange")

    # UserArea > Details > JobSummary is the cleanest description field.
    user_area = descriptor.get("UserArea", {}) or {}
    details = user_area.get("Details", {}) or {}
    description = details.get("JobSummary", "") or descriptor.get("PositionTitle", "")

    org_name = descriptor.get("OrganizationName", "US Government")
    job_location = descriptor.get("PositionLocationDisplay", location)

    return Job(
        title=title,
        company=org_name,
        description=description,
        link=apply_link,
        contact_email=None,        # Federal jobs use the structured USAJobs apply flow
        source="USAJobs",
        search_query=keywords,
        location=job_location,
        date_posted=normalize_date(descriptor.get("PublicationStartDate")),
        salary_text=salary_text(sal_min, sal_max, "USD"),
        salary_min=float(sal_min) if sal_min else None,
        salary_max=float(sal_max) if sal_max else None,
        remote="remote" in (description + job_location).lower(),
        # Federal jobs don't sponsor visas — explicit False (not detect_h1b).
        h1b_mention=False,
    )


def _map_job_type(job_type: str) -> Optional[str]:
    """Map our internal job_type to USAJobs PositionSchedule codes."""
    return {
        "Full-time": "1",     # Full-Time
        "Part-time": "2",     # Part-Time
        "Internship": "5",    # Student/Internship
    }.get(job_type)
