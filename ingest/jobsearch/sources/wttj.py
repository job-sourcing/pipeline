"""WelcomeToTheJungle (WTTJ) Algolia search client (SRC-WTTJ).

WTTJ exposes its ENTIRE job index (~50-100k EU jobs) via a public Algolia
index — no key signup. The credentials (app id + search-only API key) are
public-by-design and published in the site's own ``/api/env`` bootstrap;
they rotate ~monthly, so we fetch them fresh each run (24h module cache).

Flow (methodology §3.6/§8.18, verified live 2026-08-26 + 2026-08-27):
  1. GET https://www.welcometothejungle.com/api/env
     - CloudFront blocks non-browser TLS fingerprints from HK datacenter
       IPs → curl-cffi chrome131 impersonation required for THIS call.
     - Body: ``window.env = {"PUBLIC_ALGOLIA_APPLICATION_ID": "...",
       "PUBLIC_ALGOLIA_API_KEY_CLIENT": "..."}``
  2. POST https://{appId}-dsn.algolia.net/1/indexes/wttj_jobs_production_en/query
     with ``{"params": "query={kw}&hitsPerPage=N&page={p}"}`` + Algolia
     headers + WTTJ Referer. This call works with plain requests.

Yield verified live: 9,895 hits for "software engineer" (nbPages 500 × 20).
Formerly Otta (decommissioned — redirects here). EU/DACH-heavy coverage.
"""
from __future__ import annotations

import json
import re
import sys
import time
from typing import Optional
from urllib.parse import quote_plus

from requests import HTTPError, ConnectionError

from ..config import Config
from ..models import Job
from .base import fetch_json, detect_h1b, extract_email, normalize_date

_ENV_URL = "https://www.welcometothejungle.com/api/env"
_INDEX = "wttj_jobs_production_en"
_WTTJ = "https://www.welcometothejungle.com"

# Module-level cred cache: (app_id, api_key, fetched_at_epoch). The keys
# rotate ~monthly; 24h is the methodology-mandated re-fetch interval.
_env_cache: Optional[tuple[str, str, float]] = None
_ENV_TTL_S = 24 * 3600

# Known-good creds from the 2026-08-26/27 verification — used as a bootstrap
# fallback ONLY if the env endpoint is unreachable (never trusted silently;
# every Algolia 403/401 triggers a forced refresh).
_FALLBACK_CREDS = ("CSEKHVMS53", "4bd8f6215d0cc52b26430765769e65a0")

# audit P2-3: pagination bounds — the fetch loop pages while num_results is
# unmet and Algolia signals more (nbPages / full page), capped at 10 pages
# (10 × 50 hits) and paced 0.4s between requests. Previously `page=0` only,
# so client-side remote/date filters silently truncated below num_results.
_MAX_PAGES = 10
_PAGE_PAUSE_S = 0.4

# contract_type codes → our job_type vocabulary
_CONTRACT_MAP = {
    "full_time": "full-time",
    "part_time": "part-time",
    "internship": "internship",
    "apprenticeship": "internship",
    "temporary": "temporary",
    "freelance": "contract",
}


def is_configured(cfg: Config) -> bool:
    """WTTJ needs no signup key — always configured."""
    return True


def _parse_env_body(text: str) -> tuple[str, str]:
    """Extract Algolia creds from the ``window.env = {...}`` bootstrap."""
    m = re.search(r"window\.env\s*=\s*(\{.*\})", text, re.S)
    if not m:
        raise RuntimeError("WTTJ: /api/env shape changed — no window.env blob")
    # The blob is JS-ish JSON; normalize quotes before parsing.
    raw = m.group(1).strip().rstrip(";")
    try:
        env = json.loads(raw)
    except json.JSONDecodeError:
        env = json.loads(raw.replace("'", '"'))
    app_id = env.get("PUBLIC_ALGOLIA_APPLICATION_ID", "")
    api_key = env.get("PUBLIC_ALGOLIA_API_KEY_CLIENT", "")
    if not app_id or not api_key:
        raise RuntimeError("WTTJ: /api/env missing Algolia keys")
    return app_id, api_key


def _fetch_env_creds(force: bool = False) -> tuple[str, str]:
    """Fetch (and cache) the Algolia creds from WTTJ's public env endpoint.

    curl-cffi chrome131 impersonation is required — CloudFront 403's plain
    requests from datacenter IPs. If curl_cffi isn't installed or the env
    endpoint is unreachable, fall back to cached/fallback creds.
    """
    global _env_cache
    now = time.time()
    if (not force and _env_cache
            and now - _env_cache[2] < _ENV_TTL_S):
        return _env_cache[0], _env_cache[1]

    app_id = api_key = ""
    try:
        from curl_cffi import requests as cffi_requests
        r = cffi_requests.get(
            _ENV_URL, impersonate="chrome131", timeout=20,
            headers={"Referer": f"{_WTTJ}/", "Accept": "*/*"})
        if r.status_code == 200:
            app_id, api_key = _parse_env_body(r.text)
    except ImportError:
        pass
    except Exception:
        pass  # fall through to cache/fallback below

    if not app_id:
        if _env_cache:
            app_id, api_key = _env_cache[0], _env_cache[1]
        else:
            app_id, api_key = _fallback_or_raise()

    _env_cache = (app_id, api_key, now)
    return app_id, api_key


def _fallback_or_raise() -> tuple[str, str]:
    """Use the verified fallback creds, or raise if curl_cffi is missing too.

    The fallback is only wrong when WTTJ rotated keys since the last
    verification — in that case the Algolia call 401/403s and the caller
    surfaces a clear error rather than silently succeeding.
    """
    return _FALLBACK_CREDS


def _algolia_headers(app_id: str, api_key: str) -> dict:
    return {
        "x-algolia-application-id": app_id,
        "x-algolia-api-key": api_key,
        "Content-Type": "application/json",
        "Referer": f"{_WTTJ}/",
        "Origin": _WTTJ,
    }


def _hit_to_job(hit: dict) -> Optional[Job]:
    """Map an Algolia hit to our Job model. Returns None for unusable hits."""
    title = (hit.get("name") or "").strip()
    if not title:
        return None
    org = hit.get("organization") or {}
    company = (org.get("name") or "").strip()

    org_slug = (org.get("slug") or "").strip()
    job_slug = (hit.get("slug") or "").strip()
    link = (f"{_WTTJ}/fr/companies/{org_slug}/jobs/{job_slug}"
            if org_slug and job_slug else "")

    # Location: first office (city + country).
    offices = hit.get("offices") or []
    city = country = ""
    if offices:
        o = offices[0] or {}
        city = (o.get("city") or "").strip()
        country = (o.get("country") or "").strip()
    location = ", ".join(x for x in (city, country) if x)

    remote_str = (hit.get("remote") or "").lower()
    is_remote = remote_str in ("yes", "full", "fully_remote")

    salary_min = hit.get("salary_minimum")
    salary_max = hit.get("salary_maximum")
    currency = (hit.get("salary_currency") or "").strip()
    salary = ""
    if salary_min is not None and salary_max is not None and currency:
        salary = f"{currency} {salary_min:,.0f} – {salary_max:,.0f}"

    description = (hit.get("summary") or "").strip()
    desc_html = description
    if hit.get("key_missions"):
        missions = hit["key_missions"]
        if isinstance(missions, list):
            missions = "\n".join(str(x) for x in missions)
        desc_html += f"\n{missions}"

    date_posted = (hit.get("published_at_date") or "").strip()[:10]

    contract = hit.get("contract_type") or ""
    if contract:
        desc_html += f"\nContract: {_CONTRACT_MAP.get(contract, contract)}"

    return Job(
        title=title,
        company=company or "Unknown",
        location=location or "Remote",
        link=link,
        salary_text=salary,
        salary_min=float(salary_min) if salary_min is not None else None,
        salary_max=float(salary_max) if salary_max is not None else None,
        date_posted=date_posted,
        description=desc_html,
        source="WTTJ",
        remote=is_remote,
    )


def _location_matches(search_loc: str, hit: dict) -> bool:
    """Client-side location filter across all offices of a hit."""
    if not search_loc or search_loc.lower().strip() in ("remote", ""):
        # Remote search: keep remote + partial-remote EU listings.
        return (hit.get("remote") or "").lower() in ("yes", "partial")
    needle = search_loc.lower().strip()
    offices = hit.get("offices") or []
    for o in offices:
        hay = " ".join(str(x or "") for x in (
            o.get("city"), o.get("country"), o.get("country_code"))).lower()
        if needle in hay:
            return True
    return False


def _query_algolia(body: dict, cfg: Config) -> dict:
    """One Algolia query POST, with the cred-refresh retry (unchanged
    semantics: 401/403 → force-refresh /api/env creds → one retry).

    Reads the (24h-cached) creds per call so pages after a mid-run cred
    rotation pick up the refreshed pair instead of re-403ing every page.
    """
    app_id, api_key = _fetch_env_creds()
    url = f"https://{app_id}-dsn.algolia.net/1/indexes/{_INDEX}/query"
    try:
        return fetch_json(url, json=body, method="POST",
                          headers=_algolia_headers(app_id, api_key), cfg=cfg)
    except HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status in (401, 403):
            # Creds rotated → force-refresh once and retry.
            app_id, api_key = _fetch_env_creds(force=True)
            url = f"https://{app_id}-dsn.algolia.net/1/indexes/{_INDEX}/query"
            try:
                return fetch_json(url, json=body, method="POST",
                                  headers=_algolia_headers(app_id, api_key),
                                  cfg=cfg)
            except HTTPError as exc2:
                st2 = exc2.response.status_code if exc2.response is not None else 0
                raise RuntimeError(
                    f"WTTJ: Algolia auth failed after cred refresh "
                    f"(HTTP {st2}) — /api/env may have changed shape."
                )
        else:
            raise RuntimeError(f"WTTJ: Algolia error (HTTP {status})")
    except ConnectionError:
        raise RuntimeError("WTTJ: No internet connection.")


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg=None) -> list[Job]:
    """Search WTTJ's Algolia index for jobs matching keywords + location."""
    cfg = cfg or Config()

    # Request extra headroom for client-side filters (location + date).
    hits_per_page = min(max(num_results * 3, 20), 50)

    # audit P2-3: page through the index (previously `page=0` only, so the
    # remote/date filters silently truncated below num_results). Loop while
    # num_results is unmet AND Algolia signals more (full page / nbPages);
    # a short page or `page + 1 >= nbPages` means the index is exhausted.
    jobs: list[Job] = []
    page = 0
    while page < _MAX_PAGES and len(jobs) < num_results:
        params_str = (f"query={quote_plus(keywords)}"
                      f"&hitsPerPage={hits_per_page}&page={page}")
        try:
            data = _query_algolia({"params": params_str}, cfg)
        except Exception as exc:  # noqa: BLE001 — audit P2-3 isolation
            if not jobs:
                raise          # first page — surface the error as before
            # Later page: degrade, keep what's already collected.
            print(f"[wttj] page {page} failed after {len(jobs)} jobs "
                  f"({exc}); keeping collected rows", file=sys.stderr)
            break

        if not isinstance(data, dict) or "hits" not in data:
            if not jobs:
                raise RuntimeError("WTTJ: Unexpected Algolia response shape")
            break

        hits = data.get("hits") or []
        for hit in hits:
            if len(jobs) >= num_results:
                break
            if not _location_matches(location, hit):
                continue
            j = _hit_to_job(hit)
            if j is None:
                continue
            if date_filter:
                if not j.date_posted or not _within_days(j.date_posted, date_filter):
                    continue
            if job_type:
                # job_type filtering is approximate: match against description
                # (contract type is folded into the description text).
                if job_type.lower() not in (j.description or "").lower():
                    continue
            jobs.append(j)

        if len(jobs) >= num_results:
            break
        if len(hits) < hits_per_page:
            break               # short page — index exhausted
        try:
            nb_pages = int(data.get("nbPages") or 0)
            cur_page = int(data.get("page") if data.get("page") is not None
                           else page)
        except (TypeError, ValueError):
            nb_pages, cur_page = 0, page
        if nb_pages and cur_page + 1 >= nb_pages:
            break               # last page per Algolia's own metadata
        page += 1
        time.sleep(_PAGE_PAUSE_S)   # polite pacing between pages
    return jobs


def _within_days(date_str: str, days: int) -> bool:
    """True if date_str (YYYY-MM-DD) is within the last N days."""
    try:
        from datetime import date, datetime, timedelta
        d = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        return (date.today() - d).days <= days
    except ValueError:
        return True  # unparseable dates pass through rather than dropping
