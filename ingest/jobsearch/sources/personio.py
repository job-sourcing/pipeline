"""Personio ATS-direct client — XML feed primary + HTML fallback
(SRC-PERSONIO, built 2026-08-27).

Personio is the dominant ATS in the DACH region (~2,000 customers). Each
customer has a subdomain: ``{slug}.jobs.personio.de`` with:

  (a) PRIMARY: ``/xml`` — a no-auth XML feed (``<workzag-jobs>`` /
      ``<position>`` schema). Verified live 2026-08-26: personio=1,
      kiwigrid=4, demo=4 positions.
  (b) FALLBACK: ``/?language=en`` — server-rendered React page with the
      full job list embedded; used when /xml is 404 (some tenants disable
      the feed) or 429 (rate-limited).

⚠ DOMAIN-WIDE RATE LIMIT (verified live 2026-08-26): ``*.jobs.personio.de``
throttles GLOBALLY across all subdomains — the 5th+ request inside 60s
returns HTTP 429 with a 33 KB HTML challenge page (not XML). Even 11s
spacing triggered 429s on probes 3-8; the working cadence is ≥25s between
requests. This module enforces that pacing with a module-level lock +
timestamp — serial requests only, regardless of caller parallelism.

Job URL: ``https://{slug}.jobs.personio.de/job/{id}?language=en`` (verified).
"""
from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from typing import Optional

from requests import HTTPError, ConnectionError, Timeout

from ..config import Config
from ..models import Job
from .base import fetch_text, detect_h1b, extract_email, clean_html

# Global pacing across ALL Personio tenants (module state — one per process).
_PERSONIO_LOCK = threading.Lock()
_last_request_ts = 0.0
_MIN_SPACING_S = 25.0          # ≥25s spacing (11s verified insufficient)

# Curated default slugs — all verified live 2026-08-26/27.
_DEFAULT_SLUGS = ["personio", "kiwigrid"]

# Known-good job-description section names to keep (others are legal boilerplate).
_DESC_SECTIONS = re.compile(r"^(deine aufgaben|your mission|your tasks|"
                            r"deine qualifikationen|your profile|"
                            r"what you.{0,20}bring|was wir dir bieten|"
                            r"what we offer|about us|über uns)", re.I)


def is_configured(cfg: Config) -> bool:
    """Personio needs no key — always configured."""
    return True


class _Pacer:
    """Context manager enforcing the global ≥25s spacing.

    The lock is held across the sleep + request, serializing all Personio
    traffic process-wide. If the sleep is interrupted (KeyboardInterrupt /
    SystemExit), the lock is released so later calls don't deadlock.
    """

    def __enter__(self):
        global _last_request_ts
        _PERSONIO_LOCK.acquire()
        try:
            wait = _MIN_SPACING_S - (time.monotonic() - _last_request_ts)
            if wait > 0:
                time.sleep(wait)
        except BaseException:
            _PERSONIO_LOCK.release()
            raise
        return self

    def __exit__(self, *exc):
        global _last_request_ts
        _last_request_ts = time.monotonic()
        _PERSONIO_LOCK.release()
        return False


def _xml_to_jobs(raw_xml: str, slug: str, keywords: str) -> list[Job]:
    """Parse the <workzag-jobs> XML feed into Jobs."""
    jobs: list[Job] = []
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        return jobs
    if root.tag != "workzag-jobs":
        return jobs

    for pos in root.findall("position"):
        title = (pos.findtext("name") or "").strip()
        if not title:
            continue
        pos_id = (pos.findtext("id") or "").strip()
        office = (pos.findtext("office") or "").strip()
        department = (pos.findtext("department") or "").strip()
        schedule = (pos.findtext("schedule") or "").strip()      # full-time
        employment = (pos.findtext("employmentType") or "").strip()  # permanent

        created = (pos.findtext("createdAt") or "").strip()[:10]

        # Description: concatenate jobDescription sections (CDATA HTML),
        # cleaned; cap at 500 chars per the stack's storage convention.
        parts: list[str] = []
        for jd in pos.findall(".//jobDescription"):
            section = (jd.findtext("name") or "").strip()
            body = clean_html(jd.findtext("value") or "")
            if not body:
                continue
            if section and _DESC_SECTIONS.match(section):
                parts.append(f"{section}: {body}")
            elif not section:
                parts.append(body)
        description = "\n\n".join(parts)[:500]

        keywords_field = (pos.findtext("keywords") or "")
        is_remote = "remote" in f"{title} {keywords_field}".lower()

        link = (f"https://{slug}.jobs.personio.de/job/{pos_id}?language=en"
                if pos_id else "")

        jt = {"full-time": "full-time", "part-time": "part-time",
              "internship": "internship"}.get(schedule)
        desc_bits = [b for b in (description,
                                 f"Schedule: {jt}" if jt else "") if b]

        jobs.append(Job(
            title=title,
            company=slug.replace("-", " ").title(),
            location=office or "Remote",
            link=link,
            salary_text=None,
            date_posted=created,
            description=" ".join(desc_bits),
            source=f"Personio.{slug}",
            search_query=keywords,
            remote=is_remote,
            h1b_mention=detect_h1b(f"{title} {description}"),
        ))
    return jobs


def _html_to_jobs(raw_html: str, slug: str, keywords: str) -> list[Job]:
    """Fallback: parse the server-rendered HTML board's /job/{id} links."""
    jobs: list[Job] = []
    seen: set[str] = set()
    # Links look like /job/36748?language=en — title text sits in the <a>.
    for m in re.finditer(
            r'<a[^>]*href="/job/(\d+)[^"]*"[^>]*>(.*?)</a>',
            raw_html, re.S | re.I):
        pos_id, inner = m.group(1), m.group(2)
        if pos_id in seen:
            continue
        seen.add(pos_id)
        title = clean_html(inner).strip()
        if not title:
            continue
        jobs.append(Job(
            title=title,
            company=slug.replace("-", " ").title(),
            location="",
            link=f"https://{slug}.jobs.personio.de/job/{pos_id}?language=en",
            date_posted="",
            description="",
            source=f"Personio.{slug}",
            search_query=keywords,
        ))
    return jobs


def _fetch_slug(slug: str, keywords: str, cfg: Config) -> tuple[list[Job], str]:
    """Fetch one tenant: XML primary, HTML fallback. Returns (jobs, mode)."""
    xml_url = f"https://{slug}.jobs.personio.de/xml"
    html_url = f"https://{slug}.jobs.personio.de/?language=en"

    try:
        with _Pacer():
            raw = fetch_text(xml_url, cfg=cfg)
    except HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 0
        if code == 404:
            with _Pacer():
                try:
                    raw = fetch_text(html_url, cfg=cfg)
                except (HTTPError, ConnectionError):
                    return [], "html_unreachable"
            return _html_to_jobs(raw, slug, keywords), "html_fallback"
        if code == 429:
            return [], "rate_limited"
        return [], f"http_{code}"
    except ConnectionError:
        return [], "no_connection"

    if "<workzag-jobs" not in raw:
        # 429 challenge pages arrive as 200-with-HTML sometimes.
        return [], "not_xml"
    return _xml_to_jobs(raw, slug, keywords), "xml"


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg=None) -> list[Job]:
    """Fetch jobs across configured Personio tenants (throttled, serial)."""
    cfg = cfg or Config()
    slugs = getattr(cfg, "personio_slugs", None) or list(_DEFAULT_SLUGS)

    all_jobs: list[Job] = []
    notes: list[str] = []
    for slug in slugs:
        try:
            jobs, mode = _fetch_slug(slug, keywords, cfg)
        except Exception as e:  # noqa: BLE001
            # Serial-loop isolation (audit P2-5): a Timeout escaping
            # _fetch_slug must not discard tenants already fetched.
            jobs, mode = [], f"error:{type(e).__name__}"
        if mode not in ("xml", "html_fallback"):
            notes.append(f"{slug}:{mode}")
        all_jobs.extend(jobs)
        if len(all_jobs) >= num_results:
            break

    # Client-side location filter (DACH offices; remote is keyword-based).
    if location and location.strip().lower() not in ("remote", ""):
        needle = location.lower().strip()
        all_jobs = [j for j in all_jobs if needle in (j.location or "").lower()]

    # Keyword relevance filter (same policy as ashby.py).
    terms = [t for t in re.split(r"\W+", (keywords or "").lower()) if len(t) > 2]
    if terms:
        kept = [j for j in all_jobs
                if any(t in f"{j.title} {j.description}".lower() for t in terms)]
        all_jobs = kept or all_jobs

    if not all_jobs and notes:
        raise RuntimeError(
            f"Personio: no jobs (tenant notes: {'; '.join(notes)}) — "
            "domain-wide 429 likely; retry after 60s+ with ≥25s spacing"
        )
    return all_jobs[:num_results]
