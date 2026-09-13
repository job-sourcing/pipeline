"""Findwork client — lifted from resume-wing api/findwork.py.

Findwork specialises in tech/developer roles with strong US coverage. The
API is clean, well-documented, and returns high-quality listings from
companies actively recruiting engineers. Supports remote_ok, date_posted
cutoff, and location — all server-side.

Get a free key: findwork.dev → Register → copy API Token.
Set FINDWORK_API_KEY in env (Token auth: `Authorization: Token <key>`).

Adapted: cfg carries the token; fetch_json is the mockable seam. Auth lives
in the headers dict, which fetch_json already forwards.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from requests import HTTPError, ConnectionError

from ..config import Config
from ..models import Job
from .base import (
    fetch_json, detect_h1b, extract_email, normalize_date, is_within_days,
)

_URL = "https://findwork.dev/api/jobs/"


def is_configured(cfg: Config) -> bool:
    return bool(cfg.findwork_api_key)


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg=None) -> list[Job]:
    """Search Findwork for tech jobs matching keywords + location."""
    cfg = cfg or Config()
    if not is_configured(cfg):
        raise RuntimeError(
            "Findwork API key not configured. "
            "Set FINDWORK_API_KEY (free at findwork.dev)."
        )

    headers = {"Authorization": f"Token {cfg.findwork_api_key}"}
    params: dict = {
        "search": keywords,
        "page_size": min(num_results, 50),
    }

    location_lower = (location or "").lower()
    is_remote_search = location_lower in ("remote", "") or not location_lower
    if not is_remote_search:
        params["location"] = location
    if (job_type and job_type.lower() == "remote") or is_remote_search:
        params["remote_ok"] = "true"

    # Findwork's date_posted param is a YYYY-MM-DD cutoff.
    if date_filter:
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=date_filter)
                  ).strftime("%Y-%m-%d")
        params["date_posted"] = cutoff

    try:
        data = fetch_json(_URL, params=params, headers=headers, cfg=cfg)
    except HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 0
        if code == 403:
            raise RuntimeError(
                "Findwork: Invalid API key. Check FINDWORK_API_KEY."
            )
        raise RuntimeError(f"Findwork API error (HTTP {code})")
    except ConnectionError:
        raise RuntimeError("Findwork: No internet connection.")

    postings = (data or {}).get("results", []) if isinstance(data, dict) else []
    jobs: list[Job] = []
    for item in postings:
        if len(jobs) >= num_results:
            break

        date_str = normalize_date(item.get("date"))
        if date_filter and not is_within_days(date_str, date_filter):
            continue

        emp_type = (item.get("employment_type") or "").lower()
        is_remote = bool(item.get("remote", False))
        loc = (item.get("location") or "") or ("Remote" if is_remote else "")

        # Client-side job-type filter for contract/full-time.
        if job_type and job_type.lower() not in ("any", "remote"):
            if emp_type:
                jt = job_type.lower()
                if jt == "full-time" and "full" not in emp_type:
                    continue
                if jt == "contract" and "contract" not in emp_type:
                    continue

        desc = item.get("text") or ""
        jobs.append(Job(
            title=(item.get("role") or "").strip(),
            company=(item.get("company_name") or "Unknown").strip(),
            description=desc,
            link=item.get("url") or "",
            contact_email=extract_email(desc),
            source="Findwork",
            search_query=keywords,
            location=loc,
            date_posted=date_str,
            remote=is_remote,
            h1b_mention=detect_h1b(desc),
        ))
    return jobs
