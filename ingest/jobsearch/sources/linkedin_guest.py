"""LinkedIn Guest API client — lifted from hendrixfreire/linkedin-job-scraper
src/candidatura_agent/linkedin.py, adapted per HANDOFF Week 1 step 3:
BR-focused defaults replaced with configurable location (US/EU).

Uses the public jobs-guest endpoint — no auth (validated in exp 09:
8 real jobs in 6s). Politeness: max 1 run/day recommended (Risk 1).
"""
from __future__ import annotations

import re
import sys
import time
from html import unescape
from typing import Optional
from urllib.parse import urlencode

from ..models import Job
from .base import fetch_text, clean_html, detect_h1b, extract_email
from requests import HTTPError, ConnectionError

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

# audit P2-3: pagination bounds — guest pages return ~10 cards (verified
# live 2026-09-09); the pager advances by the number of cards ACTUALLY
# received (a fixed 25-step silently skipped ~60% of cards — audit B2),
# capped at _MAX_PAGES pages / _MAX_CARDS unique cards.
_CARDS_PER_PAGE = 10
_MAX_PAGES = 10          # 10 × ~10 = 100 cards — the politeness cap
_MAX_CARDS = 100
_PAGE_PAUSE_S = 1.0

# detail-page signal extractors (added 2026-09-09 — corroboration module,
# design-board-v2.md D2): applicant counts + requisition-id join key.
_APPLICANTS_RE = re.compile(r"([\d,]+)\s+applicants?\b", re.IGNORECASE)
_APPLICANTS_OVER_RE = re.compile(
    r"over\s*([\d,]+)\s*applicant", re.IGNORECASE)
_REQ_ID_RE = re.compile(
    r"Job Requisition ID[\s:]*([A-Z0-9-]{4,15})(?![A-Z0-9-])",
    re.IGNORECASE)
# S8-E1 (audit/findings-engagement-coverage.md §f R3a): second-pass BARE
# req token — only 65/752 indexed NVIDIA cards (8.6%) embed the anchored
# "Job Requisition ID" label, but many more carry a bare "JR#######"
# token in the description. 7-8 digits is NVIDIA's req shape and is
# high-precision: 6-digit (JR123456) and 9+-digit forms deliberately do
# NOT match, and a glued "AJR2023999" has no \b boundary before JR.
_REQ_ID_BARE_RE = re.compile(r"\bJR\d{7,8}\b")
_POSTED_TIME_RE = re.compile(
    r"posted-time-ago__text[^>]*>\s*([^<]+?)\s*</span>", re.DOTALL)

# (from original) — LinkedIn HTML cards are regex-parsed; entity urn holds the id.
_CARD_RE = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"(.*?)</li>', re.DOTALL)
_DATE_RE = re.compile(r'<time[^>]*datetime="([^"]*)"[^>]*>(.*?)</time>',
                      re.DOTALL | re.IGNORECASE)
_TITLE_RE = re.compile(r"base-search-card__title[^>]*>(.*?)</h3>")
_COMPANY_RE = re.compile(
    r"(?:hidden-nested-link[^>]*>|base-search-card__subtitle[^>]*>)(.*?)</(?:a|h4)>")
_LOCATION_RE = re.compile(r"job-search-card__location[^>]*>(.*?)</span>")


def _extract(pattern: str, value: str) -> str:
    m = re.search(pattern, value, re.DOTALL | re.IGNORECASE)
    return clean_html(m.group(1)) if m else ""


def _parse_search_results(html: str) -> list[dict]:
    # lifted verbatim from hendrixfreire linkedin.py
    jobs = []
    for m in _CARD_RE.finditer(html):
        job_id, card = m.groups()
        date_m = _DATE_RE.search(card)
        jobs.append({
            "id": job_id,
            "title": _extract(r"base-search-card__title[^>]*>(.*?)</h3>", card),
            "company": _extract(
                r"(?:hidden-nested-link[^>]*>|base-search-card__subtitle[^>]*>)"
                r"(.*?)</(?:a|h4)>", card),
            "location": _extract(r"job-search-card__location[^>]*>(.*?)</span>", card),
            "date": date_m.group(1).strip() if date_m else "",
            "url": f"https://www.linkedin.com/jobs/view/{job_id}",
        })
    return jobs


def job_key(title: str, company: str) -> str:
    """Normalize title+company to catch reposts with different IDs (original)."""
    values = []
    for value in (title, company):
        normalized = unescape(clean_html(value)).strip().lower()
        for pattern in (r"\s*[-–—]\s*.*$", r"\s*\(.*?\)\s*$", r"\s*\|.*$"):
            normalized = re.sub(pattern, "", normalized).strip()
        values.append(normalized)
    return "||".join(values)


def _parse_num_applicants(html: str) -> tuple[Optional[int], str]:
    """Extract the applicant-count signal from a detail page.

    Handles "122 applicants" / "1 applicant" / "Over 200 applicants" /
    "Be among the first 10 applicants". "Be the first to apply" has no
    digit+applicant pattern → (None, ""). Returns (int_or_None, raw_label)."""
    m = _APPLICANTS_OVER_RE.search(html)
    if m:
        return int(m.group(1).replace(",", "")), \
            f"Over {m.group(1)} applicants"
    # "Be among the first 10 applicants" — checked BEFORE the generic
    # pattern so the label names the semantics (early-stage posting)
    m = re.search(r"first\s*([\d,]+)\s*applicant", html, re.IGNORECASE)
    if m:
        return int(m.group(1).replace(",", "")), \
            f"among first {m.group(1)}"
    m = _APPLICANTS_RE.search(html)
    if m:
        raw = m.group(1)
        n = int(raw.replace(",", ""))
        label = f"{raw} applicant{'s' if n != 1 else ''}"
        return n, label
    return None, ""


def _parse_req_id(description_text: str) -> str:
    """Extract the requisition-id join key if the posting's description
    embeds one (NVIDIA does; format is company-specific).

    Two passes (S8-E1): (1) the anchored "Job Requisition ID <token>"
    pattern — company-specific, WINS on conflict; (2) a bare JR#######
    token (7-8 digits, NVIDIA's req shape) for descriptions that carry
    the id without the label.

    Run against the RAW html — clean_html strips <br> to nothing, which
    concatenates the req token with the following word (JR2023808Job).
    """
    m = _REQ_ID_RE.search(description_text)
    if m:
        return m.group(1)
    m = _REQ_ID_BARE_RE.search(description_text)
    return m.group(0) if m else ""


def fetch_detail(job_id: str, cfg=None) -> dict:
    """Fetch one posting's detail page: description + work mode + closed flag
    + corroboration signals (applicants / req-id / posted-time-ago)."""
    html = fetch_text(DETAIL_URL.format(job_id=job_id), cfg=cfg)
    text = " ".join(clean_html(html).split())
    mode_m = re.search(r"\b(Remote|Hybrid|On-site)\b", text, re.I)
    num_applicants, applicants_label = _parse_num_applicants(html)
    time_m = _POSTED_TIME_RE.search(html)
    return {
        "work_mode": mode_m.group(1) if mode_m else "",
        "description": text[:4000],
        "closed": "no longer accepting applications" in text.lower(),
        "num_applicants": num_applicants,
        "applicants_label": applicants_label,
        "job_req_id": _parse_req_id(html),
        "posted_time_ago": time_m.group(1).strip() if time_m else "",
    }


def fetch(keywords: str, location: str = "United States", num_results: int = 10,
          work_type: str = "2", max_pages: int = 1, fetch_details: bool = True,
          cfg=None) -> list[Job]:
    """Search the guest endpoint. One keyword term per request (original pattern).

    work_type: 1=onsite 2=remote 3=hybrid (LinkedIn f_WT codes).

    audit P2-3 + B2 (2026-09-09): pages return ~10 cards and `start` is a
    TRUE offset — the pager advances by the number of cards actually
    received (a fixed 25-step skipped ~60% of cards), bounded by
    _MAX_PAGES pages / _MAX_CARDS unique cards.
    """
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    cards: list[dict] = []

    search_params = {
        "keywords": keywords,
        "location": location,
        "f_WT": work_type,
        "f_TPR": "r2592000",       # last 30 days (original)
        "sortBy": "DD",
    }
    pages = min(
        _MAX_PAGES,
        max(max_pages, -(-num_results // _CARDS_PER_PAGE)))
    offset = 0
    for page in range(pages):
        url = f"{SEARCH_URL}?{urlencode({**search_params, 'start': offset})}"
        try:
            html = fetch_text(url, cfg=cfg)
        except HTTPError as exc:
            if page > 0:
                # audit P2-3: later-page failure degrades — keep the cards
                # already collected instead of discarding the whole search.
                print(f"[linkedin_guest] page {page + 1} failed after "
                      f"{len(cards)} cards: {exc}", file=sys.stderr)
                break
            code = exc.response.status_code if exc.response is not None else 0
            if code in (401, 403):
                raise RuntimeError(
                    "LinkedIn Guest: blocked (auth/forbidden). "
                    "Guest endpoint may be bot-detected — try later."
                ) from exc
            if code == 429:
                raise RuntimeError(
                    "LinkedIn Guest: rate-limited, try later."
                ) from exc
            raise RuntimeError(f"LinkedIn Guest: HTTP {code}") from exc
        except ConnectionError as exc:
            if page > 0:
                print(f"[linkedin_guest] page {page + 1} failed after "
                      f"{len(cards)} cards: {exc}", file=sys.stderr)
                break
            raise RuntimeError(
                "LinkedIn Guest: no internet connection."
            ) from exc
        if not html or not html.strip():
            break               # guest endpoint returns an empty body when exhausted
        page_cards = _parse_search_results(html)
        for job in page_cards:
            if job["id"] in seen_ids:
                continue
            key = job_key(job["title"], job["company"])
            if key in seen_keys:
                continue
            seen_ids.add(job["id"])
            seen_keys.add(key)
            cards.append(job)
        # advance by cards RECEIVED (dedup-aware): `start` is a true offset
        # into the result list; a fixed step skips cards on 10-card pages.
        offset += max(len(page_cards), 1)
        if len(cards) >= min(num_results, _MAX_CARDS):
            break
        if page + 1 < pages:
            time.sleep(_PAGE_PAUSE_S)   # 1s page delay (guest-endpoint politeness)

    jobs: list[Job] = []
    for card in cards[:num_results]:
        desc = ""
        work_mode = ""
        if fetch_details:
            try:
                detail = fetch_detail(card["id"], cfg=cfg)
                if detail["closed"]:
                    continue
                desc, work_mode = detail["description"], detail["work_mode"]
            except Exception:
                pass  # keep the card even if detail fetch fails (original tolerates)
        jobs.append(Job(
            title=card["title"],
            company=card["company"],
            description=desc,
            link=card["url"],
            contact_email=extract_email(desc) if desc else None,
            source="LinkedIn Guest",
            location=card["location"],
            date_posted=card["date"][:10] if card.get("date") else None,
            remote=work_mode.lower() == "remote" or "remote" in card["location"].lower(),
            h1b_mention=detect_h1b(f"{card['title']} {desc}"),
        ))
    return jobs
