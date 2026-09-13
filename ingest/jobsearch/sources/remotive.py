"""Remotive client — lifted from vageesh-kudutini-ramesh/resume-wing api/remotive.py,
with the exp 01 v2 fixes applied (search param works; remote-only board)."""
from __future__ import annotations

from typing import Optional

from ..models import Job
from .base import fetch_json, detect_h1b, extract_email, clean_html, normalize_date

_BASE_URL = "https://remotive.com/api/remote-jobs"


def fetch(keywords: str, location: str = "Remote", num_results: int = 20,
          date_filter: Optional[int] = None, job_type: Optional[str] = None,
          cfg=None) -> list[Job]:
    data = fetch_json(_BASE_URL, params={"search": keywords, "limit": min(num_results * 2, 100)},
                      cfg=cfg)
    jobs: list[Job] = []
    for item in data.get("jobs", []):
        title = item.get("title", "").strip()
        company = (item.get("company_name") or "").strip()
        if not title or not company:
            continue
        desc = clean_html(item.get("description", "") or "")
        salary = item.get("salary") or ""
        jobs.append(Job(
            title=title,
            company=company,
            description=desc,
            link=item.get("url", ""),
            contact_email=extract_email(desc),
            source="Remotive",
            location="Remote",
            date_posted=normalize_date(item.get("publication_date")),
            remote=True,
            h1b_mention=detect_h1b(f"{title} {desc}"),
            salary_text=str(salary) if salary else None,
        ))
        if len(jobs) >= num_results:
            break
    return jobs
