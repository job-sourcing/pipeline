"""Generalized CUSTOM JOB SITE adapters (S13) — non-Workday ATS boards.

The multi-company dump/watch chain speaks ONE internal contract:

    list rows  {reqId, title, company, url, externalPath, locationsText,
                postedOn, timeType, ...}          (workday list-row shape)
    detail     {jobPostingInfo: {title, location, additionalLocations,
                jobDescription, timeType, startDate, externalUrl,
                jobReqId, country, ...},
                hiringOrganization: {name}, similarJobs: []}

This module maps public one-call board APIs onto that contract so the
WHOLE phase chain (list → details → countryfilter → tagfacets →
corroborate → titlesearch → finish → 49-col CSV) and the GHA watch run
unchanged for non-Workday sites. Adding a future site = one adapter
class here (~80 lines of mapping) — no phase changes anywhere.

Spec grammar (the dispatch seam):

    ats:greenhouse:{org}    e.g. ats:greenhouse:anthropic
    ats:ashby:{org}         e.g. ats:ashby:openai
    {tenant}|{instance}|{site}           workday (unchanged legacy form)

Adapter economics (vs workday): both APIs return the ENTIRE board with
full descriptions in ONE request (workday needs 1 page-request per 20
rows + 1 detail request per row). The adapter caches that single fetch
per process (60s TTL) — list, per-row details, and the watch's
enrichment all serve from it: ZERO extra network.

Country classification (the S13 lesson, applied by design): the
authoritative country comes from the payload itself — greenhouse
`offices[].location` ("San Francisco, California, United States"),
ashby `address.postalAddress.addressCountry` ("United States") — so
adapters classify AT LIST TIME (any-US-office semantics) and report
meta["country_client"]=False: no detail-fetch classification, no token
undercounts (the netflix lesson), no UK-poison (the tencent lesson).

postedOn: workday serves censored labels ("Posted 30+ Days Ago");
these APIs serve EXACT ISO timestamps (first_published / publishedAt).
The adapter emits BOTH: the workday-compatible label ("Posted N Days
 Ago", never artificially censored — the censor was a workday API
limitation) for the repost-detector/implied-date machinery, and the
raw ISO on the row (firstPublishedIso) for future absolute-basis work.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import date, datetime, timezone
from typing import Optional

from ..config import Config
from . import workday
from .base import fetch_json

_SPEC_RE = re.compile(r"^ats:(greenhouse|ashby):([A-Za-z0-9_.\-]+)$")

# per-process one-fetch cache: {spec: (fetched_at, jobs_payload)}
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 60.0


def is_site_spec(spec: str) -> bool:
    """True when the board spec routes to a custom-site adapter."""
    return bool(_SPEC_RE.match((spec or "").strip()))


def parse_site(spec: str) -> tuple[str, str]:
    """'ats:ashby:openai' → ('ashby', 'openai'). Raises ValueError."""
    m = _SPEC_RE.match((spec or "").strip())
    if not m:
        raise ValueError(
            f"not a site spec: {spec!r} (expected "
            f"'ats:kind:org' with kind in greenhouse|ashby)")
    return m.group(1), m.group(2)


# ── dispatch seam (callers import THIS, not workday) ─────────────────────

def list_board(spec: str, *, country: Optional[str] = None,
               time_type: Optional[str] = None,
               cfg: Optional[Config] = None, sleep_s: float = 0.2,
               progress_every: int = 0,
               progress_label: str = "list",
               client_filter: bool = True,
               ) -> tuple[dict[str, dict], dict]:
    """workday.list_board's contract for ANY board spec. Routes to the
    matching adapter; a workday spec (no 'ats:' prefix) goes to the
    workday path unchanged (byte-identical behavior)."""
    if is_site_spec(spec):
        kind, org = parse_site(spec)
        adapter = _ADAPTERS[kind](org, cfg or Config())
        return adapter.list_board(country=country, time_type=time_type,
                                  progress_label=progress_label)
    return workday.list_board(spec, country=country, time_type=time_type,
                              cfg=cfg, sleep_s=sleep_s,
                              progress_every=progress_every,
                              progress_label=progress_label,
                              client_filter=client_filter)


def detail_payload(spec: str, external_path: str,
                   cfg: Optional[Config] = None) -> Optional[dict]:
    """workday.detail_payload's contract for ANY board spec: the raw
    payload for one posting, identified by its externalPath (adapters
    accept either the path form '/jobs/{id}' or the bare reqId)."""
    if is_site_spec(spec):
        kind, org = parse_site(spec)
        adapter = _ADAPTERS[kind](org, cfg or Config())
        return adapter.detail_payload(external_path)
    return workday.detail_payload(workday.parse_board(spec), external_path,
                                  cfg or Config())


# ── shared helpers ───────────────────────────────────────────────────────

def _posted_label(iso: Optional[str]) -> tuple[str, str]:
    """ISO timestamp → (workday-style label, iso-normalized). The label
    drives the existing repost-detector / implied-post-date machinery;
    numeric labels are never artificially censored (the '30+' censor
    was a workday API limitation, not a property of these APIs)."""
    if not iso:
        return "", ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "", ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (date.today() - dt.astimezone(timezone.utc).date()).days
    if days < 0:
        days = 0
    label = "Posted Today" if days == 0 else f"Posted {days} Days Ago"
    return label, dt.date().isoformat()


def _fetch_cached(url: str, spec: str, cfg: Config) -> list[dict]:
    """One fetch per process per spec (TTL-bounded); returns the jobs
    list. Transport failures raise — the phase chain's existing B1
    fail-safe contract handles them exactly as workday's would."""
    now = time.monotonic()
    hit = _CACHE.get(spec)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    payload = fetch_json(url, cfg=cfg)
    jobs = payload.get("jobs") or []
    if not isinstance(jobs, list):
        raise RuntimeError(f"{url}: unexpected payload (jobs not a list)")
    _CACHE[spec] = (now, jobs)
    return jobs


def _dedup_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ── greenhouse ───────────────────────────────────────────────────────────

class GreenhouseAdapter:
    """boards-api.greenhouse.io/v1/boards/{org}/jobs?content=true —
    the whole board (descriptions included) in one public call.

    Field map (live-pinned 2026-09-19 on anthropic, 609 jobs):
      reqId            requisition_id (falls back to the numeric id)
      url              absolute_url (job-boards.greenhouse.io/…)
      locationsText    location.name ("San Francisco, CA | Seattle, WA")
      postedOn         label from first_published (EXACT, never censored)
      country          ANY office's location last-segment (authoritative
                       at list time — office strings end with the full
                       country name, e.g. '…, California, United States')
      departments      departments[].name → jobFamilyGroup tags
      timeType         NOT served by this API — blank (honest; no
                       guessing from titles), noted in the report
      questions        NOT in the public payload — the questionnaires
                       phase skips for adapters (questionnaireId blank)
    """

    KIND = "greenhouse"

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg
        self.company = org  # display name override happens at CLI level

    @property
    def _url(self) -> str:
        return (f"https://boards-api.greenhouse.io/v1/boards/"
                f"{self.org}/jobs?content=true")

    def _jobs(self) -> list[dict]:
        return _fetch_cached(self._url, f"ats:greenhouse:{self.org}",
                             self.cfg)

    def _office_countries(self, job: dict) -> list[str]:
        """Authoritative countries from office locations (last segment)."""
        out: list[str] = []
        for office in job.get("offices") or []:
            loc = (office or {}).get("location") or ""
            if not isinstance(loc, str) or not loc.strip():
                continue
            seg = loc.split(",")[-1].strip()
            if seg:
                out.append(seg)
        return _dedup_keep_order(out)

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        jobs = self._jobs()
        if time_type:
            print(f"[{progress_label}] NOTE: greenhouse serves no "
                  f"employment-type field — time filter {time_type!r} "
                  f"NOT applied (honest: no guessing)",
                  file=sys.stderr, flush=True)
        rows: dict[str, dict] = {}
        dropped_country = 0
        for job in jobs:
            rid = str(job.get("requisition_id") or job.get("id") or "")
            if not rid:
                continue
            if rid in rows:
                continue                      # requisition_id duplicates
            label, iso = _posted_label(job.get("first_published"))
            offices = job.get("offices") or []
            countries = self._office_countries(job)
            if country:
                if not any(workday.country_str_matches(c, country)
                           for c in countries):
                    dropped_country += 1
                    continue
            row = {
                "reqId": rid,
                "title": job.get("title") or "",
                "company": self.company,
                "url": job.get("absolute_url") or "",
                "externalPath": f"/jobs/{job.get('id')}",
                "locationsText": (job.get("location") or {}).get("name")
                                 or "",
                "postedOn": label,
                "timeType": "",
                "bulletFields": [rid],
                "ats": "greenhouse",
                "firstPublishedIso": iso,
                "departments": _dedup_keep_order(
                    [str(d.get("name") or "") for d in
                     job.get("departments") or []]),
                "countries": countries,
                "applicationDeadline": job.get("application_deadline")
                                        or "",
            }
            rows[rid] = row
        meta = {
            "complete": True, "total": len(jobs), "pages": 1,
            "country_client": False,   # classified at list time
            "client_filtered": dropped_country,
            "ats": "greenhouse",
        }
        print(f"[{progress_label}] greenhouse:{self.org}: {len(rows)} rows"
              + (f" ({dropped_country} non-{country} dropped client-side "
                 f"from office countries)" if country else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str) -> Optional[dict]:
        key = str(external_path or "").rsplit("/", 1)[-1].strip()
        for job in self._jobs():
            jid = str(job.get("id"))
            if jid == key or str(job.get("requisition_id")) == key:
                offices = job.get("offices") or []
                countries = self._office_countries(job)
                label, iso = _posted_label(job.get("first_published"))
                return {
                    "jobPostingInfo": {
                        "title": job.get("title") or "",
                        "location": (job.get("location") or {}).get("name")
                                    or "",
                        "additionalLocations": _dedup_keep_order(
                            [(o or {}).get("name") or ""
                             for o in offices[1:]]),
                        "jobDescription": job.get("content") or "",
                        "timeType": "",
                        "startDate": "",
                        "externalUrl": job.get("absolute_url") or "",
                        "jobReqId": str(job.get("requisition_id")
                                        or job.get("id") or ""),
                        "postedOn": label,
                        "country": ({"descriptor": countries[0]}
                                    if countries else None),
                    },
                    "hiringOrganization": {
                        "name": job.get("company_name") or self.company},
                    "similarJobs": [],
                    "firstPublishedIso": iso,
                    "applicationDeadline": job.get("application_deadline")
                                            or "",
                }
        return None


# ── ashby ────────────────────────────────────────────────────────────────

class AshbyAdapter:
    """api.ashbyhq.com/posting-api/job-board/{org} — the whole board
    (descriptions, compensation, employment type) in one public call.

    Field map (live-pinned 2026-09-19 on openai, 817 jobs):
      reqId            id (a GUID; ashby posts carry no requisition id)
      url              jobUrl (jobs.ashbyhq.com/{org}/{id})
      locationsText    location (city-only strings like 'San Francisco')
      country          address.postalAddress.addressCountry — STRUCTURED
                       and authoritative (the netflix lesson by design)
      timeType         employmentType ('FullTime' — normalized to the
                       workday dialect 'Full time')
      departments      department → jobFamilyGroup tags
      postedOn         label from publishedAt (EXACT, never censored)
      compensation     available on the payload (compensationTierSummary)
    """

    KIND = "ashby"

    _TT = {"fulltime": "Full time", "full-time": "Full time",
           "parttime": "Part time", "part-time": "Part time",
           "contract": "Contract", "internship": "Internship",
           "temporary": "Temporary"}

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    @property
    def _url(self) -> str:
        return (f"https://api.ashbyhq.com/posting-api/job-board/"
                f"{self.org}")

    def _jobs(self) -> list[dict]:
        return _fetch_cached(self._url, f"ats:ashby:{self.org}", self.cfg)

    def _country(self, job: dict) -> str:
        pa = (job.get("address") or {}).get("postalAddress") or {}
        c = pa.get("addressCountry")
        return str(c).strip() if isinstance(c, str) else ""

    def _time_type(self, job: dict) -> str:
        tt = str(job.get("employmentType") or "").strip().lower()
        return self._TT.get(tt, job.get("employmentType") or "")

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        jobs = self._jobs()
        rows: dict[str, dict] = {}
        dropped_country = dropped_tt = 0
        for job in jobs:
            rid = str(job.get("id") or "")
            if not rid or rid in rows:
                continue
            if not job.get("isListed", True):
                continue                    # ashby serves unlisted too
            tt = self._time_type(job)
            if time_type and tt and tt.lower() != time_type.lower():
                dropped_tt += 1
                continue
            c = self._country(job)
            if country:
                if not workday.country_str_matches(c, country):
                    # location fallback only when the address is absent
                    loc = str(job.get("location") or "")
                    last = loc.split(",")[-1].strip() if loc else ""
                    if not workday.country_str_matches(last, country):
                        dropped_country += 1
                        continue
            label, iso = _posted_label(job.get("publishedAt"))
            row = {
                "reqId": rid,
                "title": job.get("title") or "",
                "company": self.org,
                "url": job.get("jobUrl") or "",
                "externalPath": f"/{rid}",
                "locationsText": job.get("location") or "",
                "postedOn": label,
                "timeType": tt,
                "bulletFields": [rid],
                "ats": "ashby",
                "firstPublishedIso": iso,
                "departments": [job.get("department") or ""],
                "countries": [c] if c else [],
                "remoteType": ("Remote" if job.get("workplaceType")
                               in ("remote", "Remote") else ""),
            }
            rows[rid] = row
        meta = {
            "complete": True, "total": len(jobs), "pages": 1,
            "country_client": False,
            "client_filtered": dropped_country + dropped_tt,
            "ats": "ashby",
        }
        print(f"[{progress_label}] ashby:{self.org}: {len(rows)} rows"
              + (f" ({dropped_country} non-{country} + {dropped_tt} "
                 f"non-{time_type} dropped client-side)" if country
                 or time_type else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str) -> Optional[dict]:
        key = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        for job in self._jobs():
            if str(job.get("id")) == key:
                c = self._country(job)
                label, iso = _posted_label(job.get("publishedAt"))
                return {
                    "jobPostingInfo": {
                        "title": job.get("title") or "",
                        "location": job.get("location") or "",
                        "additionalLocations": _dedup_keep_order(
                            [str(x) for x in
                             (job.get("secondaryLocations") or [])]),
                        "jobDescription": job.get("descriptionHtml") or "",
                        "timeType": self._time_type(job),
                        "startDate": "",
                        "externalUrl": job.get("jobUrl") or "",
                        "jobReqId": str(job.get("id") or ""),
                        "postedOn": label,
                        "country": {"descriptor": c} if c else None,
                    },
                    "hiringOrganization": {"name": self.org},
                    "similarJobs": [],
                    "firstPublishedIso": iso,
                    "compensation": job.get("compensation") or None,
                }
        return None


_ADAPTERS = {"greenhouse": GreenhouseAdapter, "ashby": AshbyAdapter}
