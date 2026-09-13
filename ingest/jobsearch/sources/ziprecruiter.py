"""ZipRecruiter via ZenRows (SRC-ZIPRECRUITER, folded in from
scripts/job_sourcer.py 2026-08-27 — Step C).

ZipRecruiter sits behind Cloudflare Bot Management: ~50 requests per
datacenter-IP/hour before a hard block, and our HK egress is flagged from
the start. ZenRows' js_render + US premium proxy bypasses it (validated
2026-08-26: 5/5 successes @ 9.48s avg, 0 CF challenges across 5 sequential
requests).

Cost note: premium_proxy + js_render ≈ 25+ ZenRows credits per request —
this source is registered but NOT in DEFAULT_SOURCES; opt in explicitly
(``--sources ZipRecruiter``) and keep it out of daily rotation unless the
coverage is worth the credits.

Parse path (from the validated job_sourcer.py implementation): rendered
HTML → ``<article>`` blocks → h2 title (skip "share"/"be seen" promo
blocks) + company <a> + location data-testid + salary regex + posted-ago +
/jobs/ href. ZipRecruiter often shows the same job twice → in-source dedup
by (title, company).
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote_plus

from requests import HTTPError, ConnectionError

from ..config import Config
from ..models import Job
from ..transport import zenrows_fetch_text
from .base import detect_h1b, normalize_date

_SEARCH_URL = "https://www.ziprecruiter.com/jobs-search"

_PROMO_TITLE_MARKERS = ("share", "be seen")


def is_configured(cfg: Config) -> bool:
    """ZenRows premium-proxy transport required (ZENROWS_API_KEY)."""
    return bool(cfg.zenrows_api_key)


def _posted_to_date(posted: str) -> str:
    """'today'/'yesterday'/'N days ago' → YYYY-MM-DD (best effort)."""
    from datetime import date, timedelta
    p = (posted or "").lower()
    if "today" in p:
        return date.today().isoformat()
    if "yesterday" in p:
        return (date.today() - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s*days?\s*ago", p)
    if m:
        return (date.today() - timedelta(days=int(m.group(1)))).isoformat()
    return ""


def _parse_articles(html: str, keywords: str) -> list[Job]:
    jobs: list[Job] = []
    seen: set[tuple[str, str]] = set()
    for art in re.findall(r"<article[^>]*>(.*?)</article>", html, re.DOTALL):
        title = None
        for h in re.findall(r"<h2[^>]*>([^<]+)</h2>", art):
            h = h.strip()
            if h and not any(x in h.lower() for x in _PROMO_TITLE_MARKERS):
                title = h
                break
        if not title:
            continue
        company_m = re.search(r"<p[^>]*>\s*<a[^>]*>([^<]+)</a>\s*</p>", art)
        company = company_m.group(1).strip() if company_m else ""

        key = (title, company)
        if key in seen:                       # ZipRecruiter double-lists
            continue
        seen.add(key)

        loc_m = (re.search(r'data-testid="job-card-location"[^>]*>([^<]+)<', art)
                 or re.search(r">\s*([A-Z][a-zA-Z\s]+,\s+[A-Z]{2})\s*<", art))
        sal_m = re.search(r"\$[\d,]+K?\s*-\s*\$[\d,]+K?\s*/\s*yr", art)
        posted_m = re.search(r"Posted\s+(today|yesterday|\d+\s*days?\s*ago)",
                             art, re.I)
        url_m = re.search(r'href="(/jobs/[^"]+)"', art)

        salary = sal_m.group(0) if sal_m else None
        sal_min = sal_max = None
        if sal_m:
            nums = []
            for tok in re.findall(r"\$([\d,]+)(K?)", sal_m.group(0)):
                val = float(tok[0].replace(",", ""))
                if tok[1] == "K":
                    val *= 1000
                nums.append(val)
            if len(nums) == 2:
                sal_min, sal_max = nums

        loc = (loc_m.group(1).strip() if loc_m else "") or "Remote"
        is_remote = "remote" in loc.lower()

        jobs.append(Job(
            title=title,
            company=company or "Unknown",
            location=loc,
            link=(f"https://www.ziprecruiter.com{url_m.group(1)}"
                  if url_m else ""),
            salary_text=salary,
            salary_min=sal_min,
            salary_max=sal_max,
            date_posted=_posted_to_date(posted_m.group(1) if posted_m else ""),
            description="",
            source="ZipRecruiter",
            search_query=keywords,
            remote=is_remote,
            h1b_mention=detect_h1b(title),
        ))
    return jobs


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg=None) -> list[Job]:
    """Search ZipRecruiter through the ZenRows US premium proxy."""
    cfg = cfg or Config()
    if not is_configured(cfg):
        raise RuntimeError(
            "ZipRecruiter requires the ZenRows transport — set "
            "ZENROWS_API_KEY (Cloudflare blocks direct datacenter access)."
        )

    url = (f"{_SEARCH_URL}?search={quote_plus(keywords)}"
           f"&location={quote_plus(location)}")
    try:
        html = zenrows_fetch_text(url, cfg=cfg, proxy_country="us")
    except HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 0
        raise RuntimeError(f"ZipRecruiter: ZenRows transport error (HTTP {code})")
    except ConnectionError:
        raise RuntimeError("ZipRecruiter: No internet connection.")

    jobs = _parse_articles(html, keywords)
    if date_filter:
        from datetime import date, datetime, timedelta
        cutoff = date.today() - timedelta(days=date_filter)
        kept = []
        for j in jobs:
            if not j.date_posted:
                kept.append(j)            # unknown dates pass through
                continue
            try:
                d = datetime.strptime(j.date_posted, "%Y-%m-%d").date()
                if d >= cutoff:
                    kept.append(j)
            except ValueError:
                kept.append(j)
        jobs = kept
    return jobs[:num_results]
