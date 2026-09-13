"""Careerjet Partner API client (v4) — lifted from resume-wing api/careerjet.py.

Careerjet is a large global aggregator with strong US onsite/hybrid/remote
coverage across engineering, data, product, finance, healthcare. Structured
salary data (min/max/currency/type) and RFC 2822 dates.

Get a free publisher key: careerjet.com/partners/register/as-publisher
(<2 min, no approval wait). Set CAREERJET_API_KEY in env.
Endpoint: https://search.api.careerjet.net/v4/query
Auth: HTTP Basic — API key as username, empty password.

Adapted: cfg carries the key; fetch_json is the mockable seam, extended to
forward the `auth=(key, "")` tuple (Careerjet's HTTP-Basic contract). The
reference's per-source _format_salary / _parse_careerjet_date helpers are
preserved verbatim — they encode real response-shape knowledge.
"""
from __future__ import annotations

import sys
import time
from email.utils import parsedate_to_datetime
from typing import Optional

from requests import HTTPError, ConnectionError

from ..config import Config
from ..models import Job
from .base import (
    fetch_json, detect_h1b, extract_email, is_within_days, normalize_date,
)

_URL = "https://search.api.careerjet.net/v4/query"

# audit P2-3: pagination bounds — loop `page` (paced 0.4s) while
# len(jobs) < num_results and page * page_size < totalFound, capped at 3
# pages (Careerjet's documented API page cap). Previously page was
# hardcoded to 1 and totalFound was ignored, silently truncating deep
# requests after the client-side date filter dropped rows.
_MAX_PAGES = 3
_PAGE_PAUSE_S = 0.4

# Salary type codes returned by Careerjet → human-readable label.
_SALARY_TYPE = {
    "Y": "yr",
    "M": "mo",
    "W": "wk",
    "D": "day",
    "H": "hr",
}

# Careerjet requires user_ip and user_agent on every request. For a
# server-side aggregator there is no real browser session, so we send a
# neutral placeholder that satisfies the API's validation without spoofing.
_PLACEHOLDER_IP = "127.0.0.1"
_PLACEHOLDER_AGENT = "jobsearch/1.0 (job aggregator; server-side)"


def _api_headers(cfg: Config) -> dict:
    """Headers for the v4 API. The Referer (our registered publisher site)
    is what actually authorizes the call: with it, Careerjet ties the request
    to the registered-website integration model; without it, the request is
    IP-allowlist-checked and datacenter egress IPs get 403 (verified live
    2026-08-27: same key+params, Referer → 113 hits, no Referer → 403)."""
    headers = {"User-Agent": _PLACEHOLDER_AGENT}
    if getattr(cfg, "careerjet_referer", ""):
        headers["Referer"] = cfg.careerjet_referer
    return headers


def is_configured(cfg: Config) -> bool:
    return bool(cfg.careerjet_api_key)


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg=None) -> list[Job]:
    """Search Careerjet (v4) for jobs matching keywords + location."""
    cfg = cfg or Config()
    if not is_configured(cfg):
        raise RuntimeError(
            "Careerjet API key not configured. Set CAREERJET_API_KEY "
            "(free at careerjet.com/partners/register/as-publisher)."
        )

    location_lower = (location or "").lower().strip()
    is_remote = location_lower in ("remote", "") or not location_lower
    search_location = "remote" if is_remote else location

    # Map our job_type string to Careerjet's contract_type parameter.
    # Careerjet accepts: p=permanent, c=contract, t=temporary, i=internship, v=volunteer
    contract_type = None
    if job_type:
        jt = job_type.lower()
        if jt in ("full-time", "fulltime", "permanent"):
            contract_type = "p"
        elif jt in ("contract", "contractor"):
            contract_type = "c"
        elif jt in ("part-time", "parttime", "temporary"):
            contract_type = "t"
        elif jt == "internship":
            contract_type = "i"

    # Request 2× what we need for headroom after client-side date filtering.
    page_size = min(num_results * 2, 100)
    params: dict = {
        "keywords": keywords,
        "location": search_location,
        "locale_code": "en_US",
        "page_size": page_size,
        "page": 1,
        "sort": "date",
        "user_ip": _PLACEHOLDER_IP,
        "user_agent": _PLACEHOLDER_AGENT,
    }
    if contract_type:
        params["contract_type"] = contract_type

    jobs: list[Job] = []
    page = 1
    while page <= _MAX_PAGES and len(jobs) < num_results:
        params["page"] = page
        try:
            # Careerjet v4 uses HTTP Basic auth: API key as username, empty password.
            data = fetch_json(_URL, params=params, auth=(cfg.careerjet_api_key, ""),
                              headers=_api_headers(cfg), cfg=cfg)
        except HTTPError as exc:
            if page == 1:
                status = exc.response.status_code if exc.response is not None else 0
                if status == 401:
                    raise RuntimeError(
                        "Careerjet: Invalid API key. Check CAREERJET_API_KEY."
                    )
                if status == 403:
                    raise RuntimeError(
                        "Careerjet: 403 — the request carried no Referer (or the IP "
                        "isn't allowlisted). Set CAREERJET_REFERER to the registered "
                        "publisher site URL (see ingest/.env provenance notes)."
                    )
                raise RuntimeError(f"Careerjet API error (HTTP {status})")
            # audit P2-3: later-page failure degrades — keep what's collected.
            print(f"[careerjet] page {page} failed after {len(jobs)} jobs: {exc}",
                  file=sys.stderr)
            break
        except ConnectionError:
            if page == 1:
                raise RuntimeError("Careerjet: No internet connection.")
            print(f"[careerjet] page {page} failed after {len(jobs)} jobs: "
                  f"no internet connection", file=sys.stderr)
            break

        # Handle non-JOBS response types (LOCATIONS disambiguation, etc.).
        response_type = (data or {}).get("type", "") if isinstance(data, dict) else ""
        if response_type != "JOBS":
            if page == 1:
                msg = (data or {}).get("message", "unknown response")
                raise RuntimeError(f"Careerjet: Unexpected response — {msg}")
            break

        postings = (data or {}).get("jobs") or []
        total_found = 0
        if isinstance(data, dict):
            try:
                total_found = int(data.get("totalFound") or 0)
            except (TypeError, ValueError):
                total_found = 0

        for item in postings:
            if len(jobs) >= num_results:
                break

            date_str = normalize_date(item.get("date", ""))
            if date_filter and date_str and not is_within_days(date_str, date_filter):
                continue

            sal_text, sal_min, sal_max = _format_salary(item)

            loc_raw = (item.get("locations") or "").strip()
            is_remote_listing = is_remote or "remote" in loc_raw.lower()

            desc = (item.get("description") or "").strip()
            jobs.append(Job(
                title=(item.get("title") or "").strip(),
                company=(item.get("company") or "Unknown").strip(),
                description=desc,
                link=(item.get("url") or "").strip(),
                contact_email=extract_email(desc),
                source="Careerjet",
                search_query=keywords,
                location=loc_raw or ("Remote" if is_remote_listing else ""),
                date_posted=date_str,
                salary_text=sal_text,
                salary_min=sal_min,
                salary_max=sal_max,
                remote=is_remote_listing,
                h1b_mention=detect_h1b(desc),
            ))

        if len(jobs) >= num_results:
            break
        # audit P2-3: stop when the API signals there is nothing more.
        if not postings or len(postings) < page_size:
            break               # empty/short page — no more results
        if total_found and page * page_size >= total_found:
            break               # totalFound consumed
        page += 1
        time.sleep(_PAGE_PAUSE_S)   # polite pacing between pages
    return jobs


# ── Date parser ────────────────────────────────────────────────────────────────
# NOTE: Careerjet returns RFC 2822 dates ("Wed, 15 Nov 2023 19:13:43 GMT").
# The shared base.normalize_date already handles RFC 2822 (via
# email.utils.parsedate_to_datetime), so we no longer need a local
# _parse_careerjet_date helper (DRY fix per PR_REVIEW_CODE_v2 P1-5).


# ── Salary formatter ───────────────────────────────────────────────────────────
def _format_salary(item: dict) -> tuple:
    """Build a human-readable salary_text + extract numeric min/max.

    Returns (salary_text, salary_min, salary_max).
    """
    sal_min = item.get("salary_min")
    sal_max = item.get("salary_max")
    currency = (item.get("salary_currency_code") or "").upper() or "USD"
    sal_type = _SALARY_TYPE.get(item.get("salary_type", ""), "")

    # If the API already returned a formatted salary string, prefer it.
    raw_text = (item.get("salary") or "").strip()
    if raw_text:
        return raw_text, _to_float(sal_min), _to_float(sal_max)

    # Build from structured fields when salary text is absent.
    if sal_min and sal_max:
        text = f"{currency} {sal_min:,.0f} – {sal_max:,.0f}"
    elif sal_min:
        text = f"{currency} {sal_min:,.0f}+"
    elif sal_max:
        text = f"Up to {currency} {sal_max:,.0f}"
    else:
        text = ""

    if text and sal_type:
        text += f" / {sal_type}"
    return text, _to_float(sal_min), _to_float(sal_max)


def _to_float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None
