"""Greenhouse ATS-direct client (SRC-ATS-GL).

Hits `boards-api.greenhouse.io/v1/boards/{token}/jobs` for each configured
company board token — no auth, JSON, covers ~15 well-known tech companies
(Stripe, Datadog, Anthropic, Databricks, Cloudflare, …) whose job postings
NEVER appear on aggregator boards because Greenhouse's public API is the
canonical source.

Pattern (mirrors jobsearch/sources/smartrecruiters.py so failure handling
stays consistent across the ATS-direct family):
  • Iterate cfg.greenhouse_boards in parallel via ThreadPoolExecutor.
  • Per board, GET the lightweight list endpoint
    `/v1/boards/{token}/jobs`, filter by keywords against title +
    location.name + department names + office names.
  • For the top N matches per board, GET the detail endpoint
    `/v1/boards/{token}/jobs/{id}` for the full description. Cap at 2
    detail fetches per board so we never hammer one company.
  • HTTP 404 on a board (renamed / migrated / typo) → silent skip (return
    []). Other 4xx/5xx → RuntimeError, isolated per-board. If ALL boards
    fail, raise one aggregated RuntimeError so the parent aggregator's
    SourceResult.error surfaces (D3 policy: a fully-failed source must
    not silently return []).
  • Small inter-request delay 0.05s per board to be polite.
"""
from __future__ import annotations

import sys
import time
import html as html_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import urlparse, parse_qs

from requests import HTTPError, ConnectionError, Timeout

from ..config import Config
from ..models import Job
from .base import fetch_json, detect_h1b, normalize_date, clean_html
from .arbeitnow import keyword_match

_BASE_URL = "https://boards-api.greenhouse.io/v1/boards"

# Hard cap on detail fetches per board — keeps us polite and bounded even
# for boards with hundreds of matches (Stripe = 580 jobs).
_DETAIL_CAP_PER_BOARD = 2

# Small inter-request delay per board — set so the per-board worker thread
# sleeps between list and each detail request. Total added latency per board
# is at most _DETAIL_CAP_PER_BOARD * _INTER_REQUEST_DELAY_S (0.1s), which is
# well inside the http_timeout_s budget and never bothers Greenhouse.
_INTER_REQUEST_DELAY_S = 0.05


def fetch(keywords: str, location: str = "", num_results: int = 20,
          date_filter: Optional[int] = None, job_type: Optional[str] = None,
          cfg: Config | None = None) -> list[Job]:
    """Iterate all configured Greenhouse board tokens in parallel; return
    matching jobs across boards, sorted newest-first, capped at num_results.

    Failure isolation: a single board 404'ing is skipped + logged; the
    pipeline keeps going with the boards that did respond. If ALL boards
    fail, raises one aggregated RuntimeError (D3 — surface, don't swallow).
    """
    cfg = cfg or Config()
    boards = list(cfg.greenhouse_boards) or []
    if not boards:
        return []

    jobs: list[Job] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, len(boards))) as pool:
        futures = {
            pool.submit(_fetch_one_board, board, keywords, location,
                        num_results, cfg): board
            for board in boards
        }
        for fut in as_completed(futures):
            board = futures[fut]
            try:
                jobs.extend(fut.result())
            except Exception as exc:  # noqa: BLE001 — isolation per board
                errors.append(f"{board}: {type(exc).__name__}: {exc}")
                print(f"[greenhouse] {board}: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)

    if not jobs and errors:
        # All boards failed — surface to the aggregator's SourceResult.error.
        msg = (f"Greenhouse: all {len(errors)} board(s) failed: "
               + "; ".join(errors))[:300]
        raise RuntimeError(msg)

    # Sort newest-first (updated_at normalized to YYYY-MM-DD; ISO string sort
    # works for date_posted after normalization).
    jobs.sort(key=lambda j: j.date_posted or "", reverse=True)
    return jobs[:num_results]


def _fetch_one_board(board: str, keywords: str, location: str,
                     num_results: int, cfg: Config) -> list[Job]:
    """Fetch + filter one Greenhouse board; raises on hard HTTP failure
    (5xx/4xx other than 404), returns [] for 404.

    GET /v1/boards/{board}/jobs (list, lightweight) → keyword filter against
    title + location.name + department names → top N matches → up to
    _DETAIL_CAP_PER_BOARD detail GETs for full description.
    """
    list_url = f"{_BASE_URL}/{board}/jobs"
    try:
        data = fetch_json(list_url, cfg=cfg)
    except HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 0
        if code == 404:
            # Board token not registered on Greenhouse — skip silently per
            # the smartrecruiters.py pattern.
            return []
        raise RuntimeError(
            f"Greenhouse.{board} API error (HTTP {code})"
        ) from exc
    except ConnectionError as exc:
        raise RuntimeError(
            f"Greenhouse.{board}: no internet connection"
        ) from exc

    raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []
    if not raw_jobs:
        return []

    loc_filter = (location or "").strip().lower()
    matches: list[Job] = []

    for item in raw_jobs:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        loc_name = ((item.get("location") or {}).get("name") or "").strip()
        # Departments / offices on the list payload may be present or empty;
        # the detail endpoint reliably has them, but we don't want every
        # match to trigger a detail fetch, so match against list fields only.
        dept_names = " ".join(
            (d.get("name") or "") for d in (item.get("departments") or [])
        )
        office_names = " ".join(
            (o.get("name") or "") for o in (item.get("offices") or [])
        )
        match_text = f"{title} {loc_name} {dept_names} {office_names}"

        if keywords and not keyword_match(match_text, keywords):
            continue
        if loc_filter and loc_filter not in loc_name.lower() and \
                loc_filter not in office_names.lower():
            continue

        job = Job(
            title=title,
            company=board,                 # board token is the company slug
            description="",                # filled from detail endpoint below
            link=item.get("absolute_url", "") or "",
            contact_email=None,             # ATS postings rarely surface one
            source=f"Greenhouse.{board}",
            location=loc_name,
            date_posted=_coerce_date(item.get("updated_at")),
            h1b_mention=detect_h1b(f"{title} {dept_names}"),
        )
        matches.append(job)
        # Cap per-board matches at num_results — extra matches won't get
        # detail fetches; we still want to truncate so a board with 580 jobs
        # where 200 match doesn't return 200 thinly-described rows.
        if len(matches) >= num_results:
            break

    # Detail fetches: cap at _DETAIL_CAP_PER_BOARD. The first N matches get
    # full descriptions; the rest keep the empty placeholder (still useful
    # for dedup + title-only scoring).
    for j in matches[:_DETAIL_CAP_PER_BOARD]:
        gh_jid = _parse_gh_jid(j.link)
        if not gh_jid:
            continue
        # Polite delay before each detail GET — keeps us under any rate
        # limit even when a board returns many matches.
        time.sleep(_INTER_REQUEST_DELAY_S)
        try:
            detail_url = f"{_BASE_URL}/{board}/jobs/{gh_jid}"
            detail = fetch_json(detail_url, cfg=cfg)
            content = (detail.get("content") or "").strip()
            if content:
                # Greenhouse's `content` arrives HTML-entity-escaped
                # (`&lt;h2&gt;...&lt;/h2&gt;`) — the API pre-encodes the
                # tags. Unescape first, then strip the now-real tags, so
                # the description field is plain text for scoring + LLM.
                # (Same pattern as jobicy.py / remoteok.py _clean.)
                j.description = clean_html(html_mod.unescape(content))
                # Re-run h1b detection on the richer description.
                j.h1b_mention = detect_h1b(f"{j.title} {j.description}")
                # Refresh date_posted if the detail payload has updated_at
                # (it's the same value as the list, but defensive).
                d_updated = _coerce_date(detail.get("updated_at"))
                if d_updated:
                    j.date_posted = d_updated
        except HTTPError:
            # Detail fetch failed (404, board gone, posting pulled, etc.)
            # — keep the title-only Job we already have; description stays "".
            continue
        except ConnectionError:
            continue
        except Timeout:
            # requests.Timeout is NOT a ConnectionError subclass (audit
            # P2-5) — without this arm a slow detail GET would discard
            # every match this board already collected.
            continue

    return matches


def _coerce_date(value) -> Optional[str]:
    """Greenhouse returns `updated_at` as ISO 8601 with offset
    (e.g. "2026-08-18T17:59:41-04:00") — normalize_date handles it."""
    if not value:
        return None
    return normalize_date(str(value))


def _parse_gh_jid(url: str) -> Optional[str]:
    """Pull the gh_jid query param out of a Greenhouse absolute_url.

    `https://acme.com/jobs/search?gh_jid=7737237` → "7737237".
    Returns None if the URL doesn't carry the param (older postings / custom
    career-site routing). In that case the detail fetch is skipped.
    """
    if not url or "gh_jid=" not in url:
        return None
    try:
        q = parse_qs(urlparse(url).query)
        v = (q.get("gh_jid") or [None])[0]
        return str(v) if v else None
    except Exception:
        return None
