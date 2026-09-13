"""Jooble client — lifted from resume-wing api/jooble.py.

Jooble is a high-volume global aggregator pulling from thousands of US job
sites, regional boards, company career pages, and niche portals that
JSearch/Adzuna don't always cover. The dedup pass in the aggregator catches
same-job duplicates by title+company fingerprint, so Jooble's multi-hop
redirect chains are the only real risk.

API docs: jooble.org/api/about. Get a free key: fill the form, receive by
email. Set JOOBLE_API_KEY in env.

Adapted: cfg carries the key (it's part of the URL path in Jooble's
contract); fetch_json is the mockable seam, extended to support POST +
JSON body (Jooble is POST-only). Reference handled 401/403 before
raise_for_status — here fetch_json raises HTTPError on non-2xx, which we
translate to the same friendly RuntimeErrors the reference used.
"""
from __future__ import annotations

import sys
import time
from typing import Optional

from requests import HTTPError, ConnectionError

from ..config import Config
from ..models import Job
from .base import (
    fetch_json, detect_h1b, extract_email, normalize_date, is_within_days,
)

_BASE_URL = "https://jooble.org/api"

# audit P2-3: pagination bounds — page through (page=1..N, count=20 per
# page, 0.5s pacing) while len(jobs) < num_results and the response's
# totalCount/jobs signal more. Capped at 3 pages: Jooble is a key-capped
# free API, so we deliberately do not overdraw. Previously a single POST
# with count=min(num, 20) silently truncated num_results > 20.
_MAX_PAGES = 3
_PAGE_SIZE = 20      # Jooble default (and max) page size
_PAGE_PAUSE_S = 0.5


def is_configured(cfg: Config) -> bool:
    return bool(cfg.jooble_api_key)


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg=None) -> list[Job]:
    """Search Jooble for jobs matching keywords + location.

    Jooble uses POST with a JSON body; the API key is part of the URL path.
    audit P2-3: pages 1..N (count=20 each) until num_results is met or the
    response signals exhaustion (empty/short page or totalCount consumed).
    """
    cfg = cfg or Config()
    if not is_configured(cfg):
        raise RuntimeError(
            "Jooble API key not configured. "
            "Set JOOBLE_API_KEY (free at jooble.org/api/about)."
        )

    base_body: dict = {
        "keywords": keywords,
        "location": location or "United States",
        # audit P2-3: fixed page size — depth comes from pagination, not a
        # bigger count (Jooble caps count at 20 per page anyway).
        "count": _PAGE_SIZE,
    }
    if date_filter:
        # Jooble's SearchPeriod accepts number-of-days: 1, 3, 7, 30.
        base_body["SearchPeriod"] = date_filter

    jobs: list[Job] = []
    for page in range(1, _MAX_PAGES + 1):
        body = {**base_body, "page": page}
        try:
            data = fetch_json(f"{_BASE_URL}/{cfg.jooble_api_key}",
                              method="POST", json=body, cfg=cfg)
        except HTTPError as exc:
            if page == 1:
                code = exc.response.status_code if exc.response is not None else 0
                if code in (401, 403):
                    raise RuntimeError("Jooble: Invalid API key. Check JOOBLE_API_KEY.")
                raise RuntimeError(f"Jooble API error (HTTP {code})")
            # audit P2-3: later-page failure degrades — keep what's collected.
            print(f"[jooble] page {page} failed after {len(jobs)} jobs: {exc}",
                  file=sys.stderr)
            break
        except ConnectionError:
            if page == 1:
                raise RuntimeError("Jooble: No internet connection.")
            print(f"[jooble] page {page} failed after {len(jobs)} jobs: "
                  f"no internet connection", file=sys.stderr)
            break

        postings = (data or {}).get("jobs", []) if isinstance(data, dict) else []
        total_count = 0
        if isinstance(data, dict):
            try:
                total_count = int(data.get("totalCount") or 0)
            except (TypeError, ValueError):
                total_count = 0

        for item in postings:
            if len(jobs) >= num_results:
                break

            date_str = normalize_date(item.get("updated"))
            if date_filter and not is_within_days(date_str, date_filter):
                continue

            # Client-side job-type filter using Jooble's `type` field.
            type_raw = (item.get("type") or "").lower()
            if job_type and job_type.lower() not in ("any", "remote"):
                if type_raw:
                    jt = job_type.lower()
                    if jt == "full-time" and "full" not in type_raw:
                        continue
                    if jt == "contract" and "contract" not in type_raw:
                        continue

            desc = item.get("snippet") or ""
            loc = item.get("location") or location or ""
            sal_str = item.get("salary") or None

            jobs.append(Job(
                title=(item.get("title") or "").strip(),
                company=(item.get("company") or "Unknown").strip(),
                description=desc,
                link=item.get("link") or "",
                contact_email=extract_email(desc),
                source="Jooble",
                search_query=keywords,
                location=loc,
                date_posted=date_str,
                salary_text=sal_str,
                remote="remote" in (loc + " " + desc).lower(),
                h1b_mention=detect_h1b(desc),
            ))

        if len(jobs) >= num_results:
            break
        # audit P2-3: stop when the API signals there is nothing more.
        if not postings or len(postings) < _PAGE_SIZE:
            break               # empty/short page — no more results
        if total_count and page * _PAGE_SIZE >= total_count:
            break               # totalCount consumed
        time.sleep(_PAGE_PAUSE_S)   # polite pacing between pages
    return jobs
