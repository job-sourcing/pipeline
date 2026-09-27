"""The Muse client — lifted from resume-wing api/themuse.py + exp 01 v2 fix
(match on title + description, not title only; iterate up to 3 pages)."""
from __future__ import annotations

import html as html_mod
import re
from typing import Optional

from ..models import Job
from .base import fetch_json, detect_h1b, extract_email, normalize_date
from .arbeitnow import keyword_match

_URL = "https://www.themuse.com/api/public/jobs"


def fetch(keywords: str, location: str = "", num_results: int = 20,
          date_filter: Optional[int] = None, job_type: Optional[str] = None,
          cfg=None) -> list[Job]:
    jobs: list[Job] = []
    for page in range(3):  # ~50 per page, up to 3 pages (exp 01 v2)
        data = fetch_json(_URL, params={"page": page, "take": 50}, cfg=cfg)
        for item in data.get("results", []):
            title = (item.get("name") or "").strip()
            company = ((item.get("company") or {}).get("name") or "").strip()
            if not title or not company:
                continue
            # Build text from levels + locations + categories for matching
            levels = " ".join((l.get("name") or "") for l in item.get("levels", []))
            locs = " ".join((l.get("name") or "") for l in item.get("locations", []))
            cats = " ".join((c.get("name") or "") for c in item.get("categories", []))
            if not keyword_match(f"{title} {locs} {cats} {levels}", keywords):
                continue
            # v2 fix: description available via separate endpoint is skipped;
            # match against title+locations+categories (fields the listing has).
            jobs.append(Job(
                title=title,
                company=company,
                description=f"{cats} {levels}".strip(),
                link=item.get("referrer") or (item.get("apply_url") or ""),
                source="The Muse",
                location=locs,
                date_posted=normalize_date(item.get("publication_date")),
                remote="remote" in locs.lower() or "anywhere" in locs.lower(),
                h1b_mention=detect_h1b(f"{title} {cats}"),
            ))
            if len(jobs) >= num_results:
                return jobs
    return jobs
