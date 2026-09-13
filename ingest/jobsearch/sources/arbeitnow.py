"""Arbeitnow client — lifted from resume-wing api/arbeitnow.py + exp 01 v2 fix
(iterate ALL returned jobs, not just first 40; match title+desc+tags)."""
from __future__ import annotations

import re
from typing import Optional

from ..models import Job
from .base import fetch_json, detect_h1b, extract_email, clean_html, normalize_date

_URL = "https://www.arbeitnow.com/api/job-board-api"


def keyword_match(text: str, keywords: str) -> bool:
    """Any token >= 3 chars present (exp 01 validated filter)."""
    tokens = [t.lower() for t in re.split(r"[\s,]+", keywords)
              if t.strip() and len(t.strip()) >= 3]
    lowered = text.lower()
    return any(tok in lowered for tok in tokens)


def fetch(keywords: str, location: str = "", num_results: int = 20,
          date_filter: Optional[int] = None, job_type: Optional[str] = None,
          cfg=None) -> list[Job]:
    data = fetch_json(_URL, params={"search": keywords}, cfg=cfg)
    jobs: list[Job] = []
    for item in data.get("data", []):
        title = (item.get("title") or "").strip()
        desc = clean_html(item.get("description") or "")
        tags = item.get("tags") or []
        if not title:
            continue
        if not keyword_match(f"{title} {desc} {' '.join(tags)}", keywords):
            continue
        jobs.append(Job(
            title=title,
            company=(item.get("company_name") or "Unknown").strip(),
            description=desc,
            link=item.get("url", ""),
            contact_email=extract_email(desc),
            source="Arbeitnow",
            location=item.get("location", ""),
            date_posted=normalize_date(item.get("created_at")),
            remote=bool(item.get("remote", False)),
            h1b_mention=detect_h1b(desc),
        ))
        if len(jobs) >= num_results:
            break
    return jobs
