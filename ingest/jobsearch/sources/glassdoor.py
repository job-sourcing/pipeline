"""Glassdoor via ZenRows (SRC-GLASSDOOR, folded in from
scripts/job_sourcer.py 2026-08-27 — Step C).

Glassdoor sits behind Cloudflare ("Humans Only" wall after 3-5 requests).
ZenRows' js_render + US premium proxy gets 200s; the job list is embedded
as a JSON-LD ``ItemList`` script (validated 2026-08-26: 200 OK, 977 KB,
~30 jobs via ItemList). PARTIAL: some renders return a challenge page with
no JSON-LD — the adapter returns [] then (flag, don't drop: a
zero-response is surfaced as a normal empty page, and the caller can
retry).

Cost note: same as ZipRecruiter — premium proxy burns ~25+ credits per
request; registered but NOT in DEFAULT_SOURCES (opt in explicitly).

Note: JSON-LD items carry name + url but NOT per-job location/company —
company sometimes sits in the URL slug (glassdoor.com/job-listing/...-JR
codes). We extract what's there and leave the rest empty rather than
guessing.
"""
from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import quote_plus

from requests import HTTPError, ConnectionError

from ..config import Config
from ..models import Job
from ..transport import zenrows_fetch_text
from .base import detect_h1b

_SEARCH_URL = "https://www.glassdoor.com/Job/jobs.htm"


def is_configured(cfg: Config) -> bool:
    """ZenRows premium-proxy transport required (ZENROWS_API_KEY)."""
    return bool(cfg.zenrows_api_key)


def _extract_jsonld_itemlist(html: str) -> Optional[dict]:
    """Find the JSON-LD ItemList script in the rendered page."""
    for pattern in (
            r'<script[^>]*type="application/ld\+json"[^>]*>(\{.*?"ItemList".*?\})</script>',
            r'<script[^>]*type="application/ld\+json"[^>]*>(\{[^<]+\})</script>'):
        m = re.search(pattern, html, re.DOTALL)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if data.get("@type") == "ItemList":
            return data
    return None


def _items_to_jobs(data: dict, keywords: str, location: str) -> list[Job]:
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    for item in data.get("itemListElement") or []:
        li = item if isinstance(item, dict) else {}
        name = (li.get("name") or "").strip()
        url = (li.get("url") or "").strip()
        if not name:
            continue
        # Dedup on non-empty URLs only — linkless entries would otherwise
        # all collapse into the first "" key.
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)

        # Company often appears in the listing slug:
        # /job-listing/{title}-at-{Company}-Jobs-...  — best-effort extract.
        company = ""
        m = re.search(r"-at-([A-Za-z0-9&.'\- ]+?)-?(?:JR\d|Jobs|SVG)", url)
        if m:
            company = m.group(1).strip().replace("-", " ")

        jobs.append(Job(
            title=name,
            company=company or "Unknown",
            # Audit P2-4: the JSON-LD items carry NO location — copying the
            # search string into every job's location ("Austin" searches got
            # location="Austin" on jobs that may be anywhere; "Remote"
            # searches flagged every job remote=True) fabricated data and
            # poisoned downstream location/work-mode filters. Store nothing
            # rather than guess (module docstring policy).
            location="",
            link=url,
            date_posted="",
            description="",
            source="Glassdoor",
            search_query=keywords,
            remote=False,
            h1b_mention=detect_h1b(name),
        ))
    return jobs


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg=None) -> list[Job]:
    """Search Glassdoor through the ZenRows US premium proxy."""
    cfg = cfg or Config()
    if not is_configured(cfg):
        raise RuntimeError(
            "Glassdoor requires the ZenRows transport — set ZENROWS_API_KEY "
            "(Cloudflare blocks direct datacenter access)."
        )

    # The locT/locId params hurt more than they help (job_sourcer finding);
    # keyword-only search works best.
    url = f"{_SEARCH_URL}?sc.keyword={quote_plus(keywords)}"
    try:
        html = zenrows_fetch_text(url, cfg=cfg, proxy_country="us")
    except HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 0
        raise RuntimeError(f"Glassdoor: ZenRows transport error (HTTP {code})")
    except ConnectionError:
        raise RuntimeError("Glassdoor: No internet connection.")

    data = _extract_jsonld_itemlist(html)
    if not data:
        # Challenge page or shape change — empty result, not an error
        # (verified partial: some renders challenge even via proxy).
        return []
    return _items_to_jobs(data, keywords, location)[:num_results]
