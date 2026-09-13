"""Jobicy client — lifted from resume-wing api/jobicy.py + exp 01 v2 fix
(the `term` param 400s; fetch with count, filter client-side)."""
from __future__ import annotations

import html as html_mod
import re
from typing import Optional

from ..models import Job
from .base import fetch_json, detect_h1b, extract_email, normalize_date
from .arbeitnow import keyword_match

_URL = "https://jobicy.com/api/v2/remote-jobs"


def _clean(value: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", value or "")).strip()


def fetch(keywords: str, location: str = "Remote", num_results: int = 20,
          date_filter: Optional[int] = None, job_type: Optional[str] = None,
          cfg=None) -> list[Job]:
    data = fetch_json(_URL, params={"count": min(num_results * 5, 100)}, cfg=cfg)
    jobs: list[Job] = []
    for item in data.get("jobs", []):
        title = _clean(item.get("jobTitle") or "")
        company = _clean(item.get("companyName") or "") or "Unknown"
        if not title:
            continue
        desc = _clean(item.get("jobExcerpt") or item.get("jobDescription") or "")
        if not keyword_match(f"{title} {desc} {company}", keywords):
            continue
        jobs.append(Job(
            title=title,
            company=company,
            description=desc,
            link=item.get("url", ""),
            contact_email=extract_email(desc),
            source="Jobicy",
            location="Remote",
            date_posted=normalize_date(item.get("pubDate")),
            salary_text=item.get("salary") or None,
            remote=True,
            h1b_mention=detect_h1b(desc),
        ))
        if len(jobs) >= num_results:
            break
    return jobs
