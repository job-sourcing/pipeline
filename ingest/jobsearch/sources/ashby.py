"""Ashby ATS-direct client — HTML board path (SRC-ATS-AS, rebuilt 2026-08-27).

Discovery history: the per-customer Job Posting API at
``api.ashbyhq.com/v1/job-board-api/v1/job-postings`` requires a
per-customer ``Ashby-API-Key`` (HTTP 401 without it — no unauthenticated
path exists). BUT the hosted HTML board at ``https://jobs.ashbyhq.com/{slug}``
is server-rendered with the FULL job list embedded as a
``window.__appData = {...}`` JSON blob — no auth, one request per org.

Verified live 2026-08-27 (from the HK container, plain requests):
  openai=748 postings (309 KB) · notion=135 (62 KB) · ramp=81 KB ·
  linear=21 KB · ashby=65 (74 KB) · NOT-on-Ashby slugs return ~7.3 KB
  (size threshold = reliable detection).

appData shape (brace-matched JSON after the ``window.__appData =`` marker):
  organization: {organizationId, name, publicWebsite, hostedJobsPageSlug}
  jobBoard: {
    teams: [{id, name, ...}],
    jobPostings: [{id (UUID), title, teamId, locationId, locationName,
                   workplaceType ("Remote"|"Hybrid"|"Onsite"),
                   employmentType ("FullTime"|...),
                   secondaryLocations: [{locationId, locationName}],
                   compensationTierSummary ("$342K – $555K • Offers Equity")}]
  }
Job URL: ``https://jobs.ashbyhq.com/{slug}/{posting.id}`` (verified 200).

The keyed-API path (ASHBY_API_KEY) remains documented in the module
docstring for when a per-customer key is available; the HTML path needs
only ASHBY_ORGS (defaults to a curated verified list).
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from requests import HTTPError, ConnectionError, Timeout

from ..config import Config
from ..models import Job
from .base import fetch_text, detect_h1b, extract_email

# Curated default orgs — every slug verified live 2026-08-27 with real
# postings. Users extend/override via ASHBY_ORGS env (comma-separated).
_DEFAULT_ORGS = ["openai", "notion", "ramp", "linear", "ashby"]

# HTML size threshold: boards ON Ashby ship the full appData blob (≥20 KB);
# boards NOT on Ashby (or empty) return the ~7.3 KB shell. Note: an org with
# a valid account but ZERO open jobs also returns the small shell — we treat
# both as "no postings" (correct outcome, wrong-board and empty-board are
# indistinguishable without the key — acceptable for a listing source).
_MIN_BOARD_BYTES = 10_000

_APPDATA_MARKER = "window.__appData"


def is_configured(cfg: Config) -> bool:
    """The HTML path needs no key — always configured."""
    return True


def _extract_app_data(html: str) -> Optional[dict]:
    """Pull the window.__appData JSON blob out of the board HTML.

    The blob is the full app bootstrap (can be 100s of KB) — brace-match
    from the marker rather than regexing to the end.
    """
    m = re.search(r"window\.__appData\s*=\s*", html)
    if not m:
        return None
    start = m.end()
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _workplace_to_remote(workplace_type: str) -> bool:
    return (workplace_type or "").strip().lower() == "remote"


def _posting_to_job(posting: dict, org: str, org_name: str,
                    teams_by_id: dict, keywords: str,
                    location: str) -> Optional[Job]:
    title = (posting.get("title") or "").strip()
    if not title:
        return None

    loc_name = (posting.get("locationName") or "").strip()
    secondary = posting.get("secondaryLocations") or []
    all_locs = [loc_name] + [
        (s or {}).get("locationName") or "" for s in secondary
    ]
    is_remote = _workplace_to_remote(posting.get("workplaceType"))
    if not loc_name:
        loc_name = "Remote" if is_remote else ""

    # Client-side location filter (mirrors greenhouse.py semantics).
    if location and location.strip().lower() not in ("remote", ""):
        needle = location.lower().strip()
        hay = " ".join(all_locs).lower()
        if needle not in hay:
            return None
    else:
        # Remote search keeps remote listings only.
        if not is_remote:
            return None

    team = teams_by_id.get(posting.get("teamId")) or ""
    company = org_name or org

    comp = (posting.get("compensationTierSummary") or "").strip()
    salary_txt = comp or None      # local (don't shadow the base helper)
    # Parse "$342K – $555K • Offers Equity" and "$150,000 - $200,000"
    # → (min, max) in absolute dollars (per-group K multiplier).
    # Only the FIRST TWO $-amounts form the range — a 3rd token
    # ("$100K - $150K plus $20K bonus") is bonus/equity detail and must
    # not corrupt min/max (CodeRabbit round-3 finding); it stays in
    # salary_text. A reversed pair is normalized so min ≤ max always.
    vals: list[float] = []
    for m in re.finditer(r"\$([\d.,]+)(K?)", comp or ""):
        val = float(m.group(1).replace(",", ""))
        if m.group(2) == "K":
            val *= 1000
        vals.append(val)
        if len(vals) == 2:
            break                      # range complete — ignore extras
    salary_min = min(vals) if vals else None
    salary_max = max(vals) if vals else None

    job_id = posting.get("id") or ""
    link = f"https://jobs.ashbyhq.com/{org}/{job_id}" if job_id else ""

    employment = (posting.get("employmentType") or "").lower()
    jt = {"fulltime": "full-time", "parttime": "part-time",
          "contract": "contract", "internship": "internship",
          "temporary": "temporary"}.get(employment)

    desc_bits = [b for b in (f"Team: {team}" if team else "",
                             f"Employment: {jt}" if jt else "") if b]

    return Job(
        title=title,
        company=company,
        description=" ".join(desc_bits),
        link=link,
        contact_email=None,
        source=f"Ashby.{org}",
        search_query=keywords,
        location=loc_name or "Remote",
        date_posted="",                    # HTML board omits per-job dates
        remote=is_remote,
        h1b_mention=detect_h1b(f"{title} {team}"),
        salary_text=salary_txt,
        salary_min=salary_min,
        salary_max=salary_max,
    )


def _fetch_org(org: str, keywords: str, location: str,
               num_results: int, cfg: Config) -> list[Job]:
    """Fetch one org's HTML board and map its postings. Per-org failure
    isolation — one dead board never kills the batch."""
    url = f"https://jobs.ashbyhq.com/{org}"
    try:
        html = fetch_text(url, cfg=cfg)
    except HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 0
        if code == 404:
            return []
        raise RuntimeError(f"Ashby.{org}: HTTP {code}")
    except ConnectionError:
        raise RuntimeError(f"Ashby.{org}: No internet connection.")

    if len(html) < _MIN_BOARD_BYTES:
        return []          # not-on-Ashby shell page (~7.3 KB)

    app = _extract_app_data(html)
    if not app:
        return []          # board exists but shape changed / empty

    jb = app.get("jobBoard") or {}
    postings = jb.get("jobPostings") or []
    if not postings:
        return []
    teams_by_id = {t.get("id"): t.get("name")
                   for t in (jb.get("teams") or [])}
    org_name = ((app.get("organization") or {}).get("name") or org)

    jobs: list[Job] = []
    for p in postings:
        if len(jobs) >= num_results:
            break
        j = _posting_to_job(p, org, org_name, teams_by_id,
                            keywords, location)
        if j:
            jobs.append(j)
    return jobs


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg=None) -> list[Job]:
    """Fetch jobs across all configured Ashby orgs (HTML board path)."""
    cfg = cfg or Config()
    orgs = cfg.ashby_orgs or list(_DEFAULT_ORGS)

    all_jobs: list[Job] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(len(orgs), 5)) as ex:
        futures = {
            ex.submit(_fetch_org, org, keywords, location, num_results, cfg): org
            for org in orgs
        }
        for fut in as_completed(futures):
            org = futures[fut]
            try:
                all_jobs.extend(fut.result())
            except Exception as e:  # noqa: BLE001
                # Per-org isolation, mirroring the greenhouse family: one
                # failing org (incl. requests.Timeout, which is NOT a
                # ConnectionError — audit P2-5) must not discard the other
                # orgs' results or degrade the whole source.
                errors.append(str(e))

    if not all_jobs and errors:
        raise RuntimeError("; ".join(errors[:3]))

    # Keyword relevance filter — boards return ALL postings; keep only
    # postings whose title/description mentions a query term.
    terms = [t for t in re.split(r"\W+", (keywords or "").lower()) if len(t) > 2]
    if terms:
        kept = []
        for j in all_jobs:
            hay = f"{j.title} {j.description}".lower()
            if any(t in hay for t in terms):
                kept.append(j)
        # If the filter is too aggressive (0 hits on a populated board),
        # fall back to unfiltered — Ashby boards are small enough that
        # the caller's ranking handles noise.
        all_jobs = kept or all_jobs

    return all_jobs[:num_results]


# ── Keyed-API path (documented for when a per-customer key IS available) ────
# POST https://api.ashbyhq.com/v1/job-board-api/v1/job-postings
#   Headers: {"Ashby-API-Key": cfg.ashby_api_key,
#             "Content-Type": "application/json"}
#   Body: {"organization": org, "limit": N}
# (ASHBY_API_KEY / ASHBY_ORGS env knobs already exist in config.py.)
