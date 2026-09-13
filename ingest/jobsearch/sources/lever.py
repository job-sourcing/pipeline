"""Lever ATS-direct client (SRC-ATS-GL).

Hits `api.lever.co/v0/postings/{slug}?mode=json&limit=N` for each configured
company slug — no auth, JSON, returns postings many startups / scale-ups
publish directly to Lever and nowhere else.

Caveat (SRC-ATS-GL discovery): the original main-agent candidate list (npm,
segment, cashapp, plank, blink, miro, figma, webflow, discord-1, hellofresh-
canada, circle, notion, brex, plaidbrex, dashtoon, ramp-1linear, vercel-
blueprint, wealthsimplebrex) ALL 404'd on Lever — Notion moved off, Figma /
Vercel / Plaid / Brex / Datadog / Atlassian / Anthropic / OpenAI / Replit /
Substack / Mercury / Stripe / Mozilla all migrated to Greenhouse/Ashby/
Workday. The 8 verified-live slugs that DID return real postings during
SRC-ATS-GL discovery are the Config.lever_slugs default — `spotify`,
`ro`, `capital`, `qonto`, `wealthfront`, `moonpay`, `tri`, `newton`.
Override via LEVER_SLUGS env.

Pattern (mirrors jobsearch/sources/smartrecruiters.py so failure handling
stays consistent across the ATS-direct family):
  • Iterate cfg.lever_slugs in parallel via ThreadPoolExecutor.
  • Per slug, GET /v0/postings/{slug}?mode=json&limit=20.
  • Map each posting to a Job (title, company, description, link, source,
    location, date_posted, h1b).
  • HTTP 404 / "Document not found" on a slug → silent skip (return []).
    Other 4xx/5xx → RuntimeError, isolated per-slug. If ALL slugs fail,
    raise one aggregated RuntimeError so the parent aggregator's
    SourceResult.error surfaces (D3 policy).
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from requests import HTTPError, ConnectionError

from ..config import Config
from ..models import Job
from .base import fetch_json, detect_h1b, clean_html
from .arbeitnow import keyword_match

_BASE_URL = "https://api.lever.co/v0/postings"

# Lever posts up to ~100 listings on the public board per slug; cap at 20 to
# stay polite (Spotify = 95, qonto = 41, others have <25). Override by
# passing num_results to fetch(); we fetch min(num_results, 20) per slug.
_PER_SLUG_LIMIT = 20


def fetch(keywords: str, location: str = "", num_results: int = 20,
          date_filter: Optional[int] = None, job_type: Optional[str] = None,
          cfg: Config | None = None) -> list[Job]:
    """Iterate all configured Lever slugs in parallel; return matching jobs
    across slugs, sorted newest-first, capped at num_results.

    Failure isolation: a single slug 404'ing is skipped + logged; the
    pipeline keeps going with the slugs that did respond. If ALL slugs fail,
    raises one aggregated RuntimeError (D3 — surface, don't swallow).
    """
    cfg = cfg or Config()
    slugs = list(cfg.lever_slugs) or []
    if not slugs:
        return []

    limit = min(max(num_results, 1), _PER_SLUG_LIMIT)
    jobs: list[Job] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, len(slugs))) as pool:
        futures = {
            pool.submit(_fetch_one_slug, slug, keywords, location, limit, cfg):
                slug
            for slug in slugs
        }
        for fut in as_completed(futures):
            slug = futures[fut]
            try:
                jobs.extend(fut.result())
            except Exception as exc:  # noqa: BLE001 — per-slug isolation
                errors.append(f"{slug}: {type(exc).__name__}: {exc}")
                print(f"[lever] {slug}: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)

    if not jobs and errors:
        # All slugs failed — surface to the aggregator's SourceResult.error.
        msg = (f"Lever: all {len(errors)} slug(s) failed: "
               + "; ".join(errors))[:300]
        raise RuntimeError(msg)

    # Sort newest-first (createdAt is epoch ms → normalized to YYYY-MM-DD).
    jobs.sort(key=lambda j: j.date_posted or "", reverse=True)
    return jobs[:num_results]


def _fetch_one_slug(slug: str, keywords: str, location: str,
                    limit: int, cfg: Config) -> list[Job]:
    """Fetch + filter one Lever slug; raises on hard HTTP failure (5xx/4xx
    other than 404), returns [] for 404 or "Document not found".

    GET /v0/postings/{slug}?mode=json&limit=N → keyword filter against
    text (title) + categories.location + categories.team + categories.department.
    """
    url = f"{_BASE_URL}/{slug}"
    try:
        data = fetch_json(url, params={"mode": "json", "limit": limit}, cfg=cfg)
    except HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 0
        if code == 404:
            return []
        raise RuntimeError(
            f"Lever.{slug} API error (HTTP {code})"
        ) from exc
    except ConnectionError as exc:
        raise RuntimeError(
            f"Lever.{slug}: no internet connection"
        ) from exc

    # Lever returns a JSON list of postings on success; a dict with `error`
    # means the slug doesn't exist (e.g. "Document not found"). Treat both
    # as empty (silent skip — the slug migrated off Lever).
    if not isinstance(data, list):
        return []

    loc_filter = (location or "").strip().lower()
    matches: list[Job] = []

    for item in data:
        title = (item.get("text") or "").strip()
        if not title:
            continue
        cats = item.get("categories") or {}
        loc_name = (cats.get("location") or "").strip()
        team = (cats.get("team") or "").strip()
        dept = (cats.get("department") or "").strip()

        match_text = f"{title} {loc_name} {team} {dept}"
        if keywords and not keyword_match(match_text, keywords):
            continue
        if loc_filter and loc_filter not in loc_name.lower() and \
                loc_filter not in team.lower():
            continue

        # Lever gives us both descriptionPlain (clean) and description (HTML).
        # Prefer descriptionPlain; fall back to clean_html(description) when
        # missing (some postings only have the HTML form).
        desc_plain = (item.get("descriptionPlain") or "").strip()
        if desc_plain:
            description = desc_plain
        else:
            description = clean_html(item.get("description") or "")

        # additionalPlain holds comp/equity + EEO statements — fold it in so
        # h1b / salary detection can scan it too. Strip if huge (>4k chars).
        additional = (item.get("additionalPlain") or "").strip()
        if additional and len(additional) < 4000:
            description = f"{description}\n\n{additional}".strip()

        link = item.get("hostedUrl") or item.get("applyUrl") or ""
        matches.append(Job(
            title=title,
            company=slug,                # slug IS the company identifier
            description=description,
            link=link,
            contact_email=None,          # ATS postings rarely surface one
            source=f"Lever.{slug}",
            location=loc_name,
            date_posted=_lever_date(item.get("createdAt")),
            remote=_is_remote(loc_name, item.get("workplaceType") or ""),
            h1b_mention=detect_h1b(f"{title} {description}"),
        ))

    return matches


def _lever_date(value) -> Optional[str]:
    """Lever returns `createdAt` as epoch milliseconds (int).
    Normalize to YYYY-MM-DD."""
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            # Lever timestamps are ms since epoch — convert to seconds.
            ts = value / 1000 if abs(value) > 10**11 else value
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                "%Y-%m-%d")
        s = str(value).strip()
        # ISO 8601 fallback (some legacy postings).
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d")
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _is_remote(loc_name: str, workplace_type: str) -> bool:
    """Lever's workplaceType is 'remote' / 'hybrid' / 'on-site'. Location
    string may also contain 'Remote' / 'Anywhere'."""
    text = f"{loc_name} {workplace_type}".lower()
    return "remote" in text or "anywhere" in text
