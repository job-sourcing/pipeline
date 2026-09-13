"""JSearch client — lifted from resume-wing api/jsearch.py.

JSearch (by OpenWeb Ninja on RapidAPI) pulls real-time data from Google for
Jobs, which aggregates 50+ boards (LinkedIn, Indeed, Glassdoor, ZipRecruiter)
at once. Every listing includes apply_options (multiple fallback apply
links) and structured US city/state fields. Free tier: 200 requests/month.

Get a key: rapidapi.com → search "JSearch" by OpenWeb Ninja → Subscribe Free
→ Your Apps → Default App → Authorization → copy X-RapidAPI-Key.

Adapted: cfg carries JSEARCH_API_KEY; fetch_json is the mockable seam. The
reference Job model's expires_at / apply_options fields are NOT in our Job
dataclass — apply_options fallback link selection is preserved (we still
prefer apply_options[0].apply_link when job_apply_link is missing), but the
serialized blob and expiry timestamp are dropped (documented adaptation).
"""
from __future__ import annotations

from typing import Optional

from requests import HTTPError, ConnectionError

from ..config import Config
from ..models import Job
from .base import (
    fetch_json, detect_h1b, extract_email, normalize_date, salary_text,
)

_BASE_URL = "https://jsearch.p.rapidapi.com/search"

# Maps our internal date_filter (days) to JSearch date_posted param values.
_DATE_FILTER_MAP = {
    1: "today",
    3: "3days",
    7: "week",
    30: "month",
}

# Maps our internal job_type to JSearch employment_types param values.
_JOB_TYPE_MAP = {
    "Full-time": "FULLTIME",
    "Part-time": "PARTTIME",
    "Contract": "CONTRACTOR",
    "Internship": "INTERN",
}


def is_configured(cfg: Config) -> bool:
    return bool(cfg.jsearch_api_key)


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg=None) -> list[Job]:
    """Search JSearch for jobs matching keywords + location."""
    cfg = cfg or Config()
    if not is_configured(cfg):
        raise RuntimeError(
            "JSearch API key not configured. "
            "Set JSEARCH_API_KEY (free at rapidapi.com — search 'JSearch')."
        )

    headers = {
        "X-RapidAPI-Key": cfg.jsearch_api_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }

    # JSearch query format: "keywords in location"
    query = (f"{keywords} in {location}"
             if location and location.lower() != "remote" else keywords)
    is_remote = (location or "").lower() in ("remote", "") or not location

    params: dict = {
        "query": query,
        "page": "1",
        "num_pages": "1",           # one page = 10 results per request
        "country": "us",
    }
    if is_remote:
        params["remote_jobs_only"] = "true"
    if date_filter and date_filter in _DATE_FILTER_MAP:
        params["date_posted"] = _DATE_FILTER_MAP[date_filter]
    if job_type and job_type in _JOB_TYPE_MAP:
        params["employment_types"] = _JOB_TYPE_MAP[job_type]

    jobs: list[Job] = []
    # Paginate up to ceil(num_results / 10) pages; each page = 10 results.
    pages_needed = max(1, (num_results + 9) // 10)
    for page in range(1, pages_needed + 1):
        params["page"] = str(page)
        try:
            data = fetch_json(_BASE_URL, params=params, headers=headers, cfg=cfg)
        except HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else 0
            if code in (401, 403):
                raise RuntimeError(
                    "JSearch: Invalid API key. Check JSEARCH_API_KEY."
                )
            if code == 429:
                raise RuntimeError(
                    "JSearch: Rate limit hit. Free tier = 200 req/mo. Try later."
                )
            raise RuntimeError(f"JSearch API error (HTTP {code})")
        except ConnectionError:
            raise RuntimeError("JSearch: No internet connection.")

        items = data.get("data", []) if isinstance(data, dict) else []
        if not items:
            break
        for item in items:
            if len(jobs) >= num_results:
                break
            job = _normalize(item, keywords, location)
            if job:
                jobs.append(job)
    return jobs


def _normalize(item: dict, keywords: str, location: str) -> Optional[Job]:
    title = (item.get("job_title") or "").strip()
    company = (item.get("employer_name") or "Unknown").strip()

    # Use primary apply link; fall back to first apply_option if missing
    # (the apply_options blob itself isn't stored — our Job has no such field).
    apply_link = item.get("job_apply_link", "")
    if not apply_link:
        options = item.get("apply_options") or []
        apply_link = options[0].get("apply_link", "") if options else ""

    if not title or not apply_link:
        return None

    description = item.get("job_description", "") or ""
    city = item.get("job_city", "") or ""
    state = item.get("job_state", "") or ""
    job_location = f"{city}, {state}".strip(", ") if (city or state) else location

    salary_min = item.get("job_min_salary")
    salary_max = item.get("job_max_salary")
    currency = item.get("job_salary_currency", "USD") or "USD"

    return Job(
        title=title,
        company=company,
        description=description,
        link=apply_link,
        contact_email=extract_email(description),
        source="JSearch",
        search_query=keywords,
        location=job_location,
        date_posted=normalize_date(item.get("job_posted_at_datetime_utc")),
        salary_text=salary_text(salary_min, salary_max, currency),
        salary_min=float(salary_min) if salary_min else None,
        salary_max=float(salary_max) if salary_max else None,
        remote=bool(item.get("job_is_remote", False)),
        h1b_mention=detect_h1b(description),
    )
