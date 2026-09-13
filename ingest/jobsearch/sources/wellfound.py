"""Wellfound (ex-AngelList Talent) job-board client (SRC-WELLFOUND).

Wellfound is a startup-focused job board with strong US tech coverage.
It's behind Cloudflare bot detection — plain HTTP requests get 404 even
with TLS impersonation. The only reliable way to fetch is via a real
headless browser (Playwright + stealth).

The page at https://wellfound.com/jobs (and per-role pages like
/role/j/remote-software-engineer) renders jobs server-side via Next.js +
Apollo. The Apollo state is embedded in __NEXT_DATA__ as JobListing
entities, each pointing to a Startup entity for company info.

Per-role pages with filters are also supported:
  /role/j/remote-software-engineer    (role filter)
  /role/j/remote-product-manager      (other roles)
  /jobs?...                           (search with query params)

Pagination is via cursor — currently only the first ~50 jobs per page
are scraped, which is plenty for a typical search.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from requests import HTTPError, ConnectionError

from ..config import Config
from ..models import Job
from .base import detect_h1b, extract_email, normalize_date

# Use Playwright-stealth + headless Chromium — Cloudflare blocks plain
# HTTP fetches. The stealth_browser helper lives in scripts/signup/lib
# (it's a tool we use both for sign-ups and for scraping).
_WELLFOUND_JOBS_URL = "https://wellfound.com/jobs"
_WELLFOUND_ROLE_URL = "https://wellfound.com/role/j/remote-{role}"

# Map our keywords to Wellfound's role slugs. If keywords contains a known
# role name, we hit the role-filtered page (more focused results); else
# we fall back to the generic /jobs page (broader but less filtered).
_ROLE_MAP = {
    "software engineer": "software-engineer",
    "developer": "developer",
    "product manager": "product-manager",
    "data scientist": "data-scientist",
    "data engineer": "data-engineer",
    "designer": "designer",
    "frontend": "frontend-engineer",
    "backend": "backend-engineer",
    "full stack": "full-stack-engineer",
    "fullstack": "full-stack-engineer",
    "devops": "devops-engineer",
    "sre": "site-reliability-engineer",
    "machine learning": "machine-learning-engineer",
    "ml engineer": "machine-learning-engineer",
    "ai engineer": "ai-engineer",
    "engineering manager": "engineering-manager",
    "tech lead": "tech-lead",
}


def is_configured(cfg: Config) -> bool:
    """Wellfound needs no API key — always configured."""
    return True


def _build_url_for_keywords(keywords: str) -> str:
    """Pick the best Wellfound URL based on keywords."""
    kw_lower = keywords.lower().strip()
    for needle, role_slug in _ROLE_MAP.items():
        if needle in kw_lower:
            return _WELLFOUND_ROLE_URL.format(role=role_slug)
    return _WELLFOUND_JOBS_URL


def _extract_next_data(html: str) -> Optional[dict]:
    """Pull the __NEXT_DATA__ JSON out of a Wellfound SSR HTML page."""
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html, re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None


def _build_job(job_data: dict, startup_data: dict) -> Optional[Job]:
    """Build a Job from a Wellfound Apollo JobListing + its Startup ref."""
    job_id = str(job_data.get("id") or "")
    slug = job_data.get("slug") or ""
    title = (job_data.get("title") or "").strip()
    if not title or not job_id:
        return None

    company_name = (startup_data or {}).get("name") or "Unknown"
    company_slug = (startup_data or {}).get("slug") or ""
    # Job URL
    job_url = f"https://wellfound.com/jobs/{job_id}-{slug}" if slug else f"https://wellfound.com/jobs/{job_id}"

    # Location: prefer locationNames, fall back to acceptedRemoteLocationNames + remote flag
    locations_arr = job_data.get("locationNames") or []
    remote_locations = job_data.get("acceptedRemoteLocationNames") or []
    is_remote = bool(job_data.get("remote"))
    if locations_arr:
        location_str = ", ".join(locations_arr)
    elif is_remote and remote_locations:
        location_str = f"Remote ({', '.join(remote_locations)})"
    elif is_remote:
        location_str = "Remote"
    else:
        location_str = "Unspecified"

    compensation = job_data.get("compensation") or ""

    # liveStartAt is a Unix timestamp (seconds)
    posted_at = normalize_date(job_data.get("liveStartAt"))

    return Job(
        source="Wellfound",
        title=title,
        company=company_name,
        location=location_str,
        link=job_url,
        description=compensation,  # compensation goes in description for now; real desc needs detail fetch
        date_posted=posted_at,
        remote=is_remote,
        salary_text=compensation or None,
        h1b_mention=False,  # would need detail-fetch to detect
        contact_email=None,
    )


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg: Config | None = None
          ) -> list[Job]:
    """Scrape Wellfound's /jobs page (or a role-filtered page) via Playwright.

    The page renders jobs server-side via Next.js + Apollo, so we don't need
    to execute JS — just fetch the HTML via a real browser (Cloudflare
    blocks plain HTTP). Then parse the __NEXT_DATA__ JSON.

    Cloudflare bot detection forces us to use Playwright; this source is
    therefore SLOW (~5-10s per fetch). Caller should expect higher latency
    than the no-key sources.
    """
    cfg = cfg or Config()
    # Lazy import — Playwright is heavy; only import when this source is actually called
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import stealth_sync
    except ImportError as e:
        raise RuntimeError(
            "Wellfound source requires `playwright` + `playwright-stealth` "
            f"installed: {e}"
        ) from None

    target_url = _build_url_for_keywords(keywords)

    jobs: list[Job] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        ctx.set_extra_http_headers({
            "Accept-Language": "en-US,en;q=0.9",
        })
        page = ctx.new_page()
        stealth_sync(page)
        try:
            page.goto(target_url, timeout=30000, wait_until="domcontentloaded")
            # Brief sleep to let Next.js write __NEXT_DATA__ to the DOM
            time.sleep(2)
            html = page.content()
        except Exception as e:
            browser.close()
            raise RuntimeError(f"Wellfound page load failed: {e}") from None
        browser.close()

    next_data = _extract_next_data(html)
    if not next_data:
        raise RuntimeError(
            "Wellfound SSR didn't include __NEXT_DATA__ — Cloudflare may "
            "have served an interstitial or the URL pattern changed."
        )

    page_props = next_data.get("props", {}).get("pageProps", {})
    apollo_state = (page_props.get("apolloState") or {}).get("data") or {}

    # Build a startup lookup table keyed by reference string ("Startup:NNN")
    startups_by_ref = {
        k: v for k, v in apollo_state.items()
        if k.startswith("Startup:")
    }

    # Iterate JobListing entities
    job_listings = {
        k: v for k, v in apollo_state.items()
        if k.startswith("JobListing:")
    }

    for key, job_data in job_listings.items():
        # Resolve the startup reference
        startup_ref_obj = job_data.get("startup") or {}
        startup_ref = startup_ref_obj.get("__ref") if isinstance(startup_ref_obj, dict) else None
        startup_data = startups_by_ref.get(startup_ref, {}) if startup_ref else {}

        # Filter by keywords (client-side — Wellfound's role-filtered page
        # matches role, not arbitrary keywords)
        title_lc = (job_data.get("title") or "").lower()
        # Strip simple connector words from keywords for matching
        kw_parts = [w for w in keywords.lower().split() if w not in ("a", "an", "the", "and", "or")]
        if not all(w in title_lc for w in kw_parts):
            # Also try matching the title against individual keyword tokens —
            # if at least 50% of tokens match, keep it. This avoids dropping
            # "Senior Software Engineer" for a "Python Developer" search.
            matched = sum(1 for w in kw_parts if w in title_lc)
            if matched < max(1, len(kw_parts) // 2):
                continue

        built = _build_job(job_data, startup_data)
        if not built:
            continue

        # Location filter
        if location and location.lower().strip() not in ("remote", ""):
            loc_lc = built.location.lower()
            if location.lower() not in loc_lc:
                continue
        else:
            # 'Remote' search: only keep jobs where Wellfound flagged remote=true
            if not built.remote:
                continue

        # Date filter
        if date_filter and built.date_posted:
            from .base import is_within_days
            if not is_within_days(built.date_posted, date_filter):
                continue

        jobs.append(built)
        if len(jobs) >= num_results:
            break

    return jobs
