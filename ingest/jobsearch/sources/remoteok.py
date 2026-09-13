"""RemoteOK client — lifted from resume-wing api/remoteok.py + exp 01 v2 fix
(correct endpoint is /api, not /api/remote-jobs; first item is metadata).

Note (exp 01): API quality is degraded — many fields are None. Documented,
kept because it still yields matches.
"""
from __future__ import annotations

import html as html_mod
import re
from typing import Optional

from ..models import Job
from .base import fetch_json, detect_h1b, extract_email, normalize_date
from .arbeitnow import keyword_match

_URL = "https://remoteok.com/api"


def _clean(value: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", value or "")).strip()


def fetch(keywords: str, location: str = "Remote", num_results: int = 20,
          date_filter: Optional[int] = None, job_type: Optional[str] = None,
          cfg=None) -> list[Job]:
    data = fetch_json(_URL, cfg=cfg)
    jobs: list[Job] = []
    for item in (data[1:] if isinstance(data, list) else []):
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("position") or item.get("title") or "")
        company = _clean(item.get("company_name") or item.get("company") or "")
        if not title:
            continue
        desc = _clean(item.get("description") or "")
        tags = item.get("tags") or []
        if not keyword_match(f"{title} {desc} {' '.join(tags)} {company}", keywords):
            continue
        jobs.append(Job(
            title=title,
            company=company or "Unknown",
            description=desc,
            link=item.get("url", ""),
            contact_email=extract_email(desc),
            source="RemoteOK",
            location=item.get("location", "Remote"),
            date_posted=normalize_date(item.get("date")),
            salary_text=item.get("salary_range") or None,
            remote=True,
            h1b_mention=detect_h1b(desc),
        ))
        if len(jobs) >= num_results:
            break
    return jobs
