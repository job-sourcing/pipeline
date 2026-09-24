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
    ats:lever:{org}         e.g. ats:lever:weride      (S16)
    ats:workable:{org}      e.g. ats:workable:tp-link-usa-corp  (S16)
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

import json
import re
import sys
import time
import http.cookiejar
import urllib.request
from datetime import date, datetime, timezone
from html import unescape
from typing import Optional

from ..config import Config
from . import workday
from .base import fetch_json, fetch_text

_SPEC_RE = re.compile(
    r"^ats:(greenhouse|ashby|lever|workable|feishuhire|paylocity):"
    r"([A-Za-z0-9_.\-]+)$")
# S14 registry-driven custom grammar: 'custom:{kind}' where kind ∈ _ADAPTERS
# (own-platform boards — the platform IS the company; adding one = a class
# + a registry entry, no grammar edit).
_CUSTOM_RE = re.compile(r"^custom:([a-z0-9_]+)$")

# per-process one-fetch cache: {spec: (fetched_at, jobs_payload)}
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 60.0


def is_site_spec(spec: str) -> bool:
    """True when the board spec routes to a custom-site adapter."""
    if _SPEC_RE.match((spec or "").strip()):
        return True
    m = _CUSTOM_RE.match((spec or "").strip())
    return bool(m and m.group(1) in _ADAPTERS)


def parse_site(spec: str) -> tuple[str, str]:
    """'ats:ashby:openai' → ('ashby', 'openai'); 'custom:bytedance' →
    ('bytedance', ''). Raises ValueError with the registered kinds."""
    s = (spec or "").strip()
    m = _SPEC_RE.match(s)
    if m:
        return m.group(1), m.group(2)
    m = _CUSTOM_RE.match(s)
    if m and m.group(1) in _ADAPTERS:
        return m.group(1), ""
    raise ValueError(
        f"not a site spec: {spec!r} (expected 'ats:kind:org' with kind in "
        f"greenhouse|ashby|lever|workable|feishuhire|paylocity, or "
        f"'custom:kind' with kind in "
        f"{sorted(k for k in _ADAPTERS)})")


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
                   cfg: Optional[Config] = None,
                   country: Optional[str] = None,
                   time_type: Optional[str] = None) -> Optional[dict]:
    """workday.detail_payload's contract for ANY board spec: the raw
    payload for one posting, identified by its externalPath (adapters
    accept either the path form '/jobs/{id}' or the bare reqId).

    S15 seam threading (design §6): `country`/`time_type` are OPTIONAL
    and only the greenhouse adapter consults them — its list-time
    ladder verdict must also produce the detail payload's country
    descriptor so the two agree by construction. Adapters whose
    payloads carry authoritative structured fields (ashby
    addressCountry, custom boards' server-side filters) ignore the
    params: their detail payloads are already the source of truth."""
    if is_site_spec(spec):
        kind, org = parse_site(spec)
        adapter = _ADAPTERS[kind](org, cfg or Config())
        return adapter.detail_payload(external_path, country=country,
                                      time_type=time_type)
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
    fail-safe contract handles them exactly as workday's would.
    S16: lever's API serves a TOP-LEVEL LIST (not {jobs: []}) — both
    payload shapes accepted here."""
    now = time.monotonic()
    hit = _CACHE.get(spec)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    payload = fetch_json(url, cfg=cfg)
    if isinstance(payload, list):
        jobs = payload
    else:
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

# S15: hoisted module-level (was an AshbyAdapter class attr) so the
# greenhouse metadata timeType extractor shares ONE table — no drift
# between the two dialect normalizers.
_TT = {"fulltime": "Full time", "full-time": "Full time",
       "parttime": "Part time", "part-time": "Part time",
       "contract": "Contract", "internship": "Internship",
       "temporary": "Temporary",
       # feishu recruit_type dialect (live-observed on minimax 2026-09)
       "outsourced": "Contract"}


class GreenhouseAdapter:
    """boards-api.greenhouse.io/v1/boards/{org}/jobs?content=true —
    the whole board (descriptions included) in one public call.

    Field map (live-pinned 2026-09-19 on anthropic, 609 jobs):
      reqId            requisition_id (falls back to the numeric id —
                       baidu serves NO requisition_id at all, the ashby
                       GUID precedent)
      url              absolute_url (job-boards.greenhouse.io/…)
      locationsText    location.name ("San Francisco, CA | Seattle, WA")
      postedOn         label from first_published (EXACT, never censored)
      departments      departments[].name → jobFamilyGroup tags
      questions        NOT in the public payload — the questionnaires
                       phase skips for adapters (questionnaireId blank)

    Country classification — S15 dialect ladder (`_job_in_country`,
    design docs/s15_greenhouse_dialects_design.md §4). The 2026-09-19
    office-last-segment classifier was pinned on anthropic and breaks
    on three of the four S15 boards (baidu default-office, byd
    malformed CA-zip offices, neteasegames empty offices + semicolon
    country tokens in location.name). The ladder, first hit wins:
      1. explicit target-country phrase in a location.name SEGMENT
         (per-segment country_str_matches; 'United States-Remote'
         matches — phrase tokens hyphen-split inside the segment)
      2. office-location segments — ONLY when the board's offices
         DISCRIMINATE (per-job office-id SETS vary; a board-wide
         single signature = company-HQ default office, DISQUALIFIED
         from every rung — baidu's id-1570-on-every-job-including-
         the-Toronto-rows). Null-E2 fallback: office channel
         disqualified AND location.name empty → offices still consulted
      3. US state-token fallback (United States only): any whitespace
         token of a location.name segment (+ office segments when
         discriminating) that is a US state abbreviation/name
         ('CA 95337' → 'CA'; 'Sunnyvale,CA'; 'Los Angels, CA')
      4. no hit → not in country (client_filtered count, loud)
    timeType: greenhouse serves no top-level field — honest blank —
    EXCEPT metadata[{name: 'Employment Type'}] when the board carries
    it (shein: Full-time/Part-time → _TT-normalized 'Full time'), per
    row, pass-through when the value is unmapped.
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

    # ── S15 dialect ladder helpers ────────────────────────────────

    @staticmethod
    def _segments(text: Optional[str]) -> list[str]:
        """Location dialect string → non-empty segments. Splits on
        ';', '|' and ',' — covers anthropic's ';'-lists, the '|'
        groupings, neteasegames's 'Canada-Remote; …; United
        States-Remote', and comma'd office addresses."""
        if not isinstance(text, str) or not text.strip():
            return []
        return [s.strip() for s in re.split(r"[;|,]", text) if s.strip()]

    @staticmethod
    def _office_countries(job: dict) -> list[str]:
        """Pre-S15 helper, KEPT for the unthreaded detail path: office
        location last-segments (the 2026-09-19 classifier)."""
        out: list[str] = []
        for office in job.get("offices") or []:
            loc = (office or {}).get("location") or ""
            if not isinstance(loc, str) or not loc.strip():
                continue
            seg = loc.split(",")[-1].strip()
            if seg:
                out.append(seg)
        return _dedup_keep_order(out)

    @staticmethod
    def _offices_discriminate(jobs: list[dict]) -> bool:
        """Board-level: do offices vary per job? Each job's office-id
        SET is its signature; the channel discriminates iff more than
        one distinct NON-EMPTY signature exists. baidu: every
        office-bearing job is {1570} → one signature → default office
        (recruiter sloppiness — it stamps the Sunnyvale HQ on the
        Toronto rows too) → disqualified. anthropic (21 ids, varying
        sets) / byd (5) / shein (3) / neteasegames (15): vary."""
        signatures = set()
        for job in jobs:
            ids = frozenset(
                str((o or {}).get("id")) for o in (job.get("offices")
                                                   or [])
                if (o or {}).get("id") is not None)
            if ids:
                signatures.add(ids)
        return len(signatures) > 1

    def _job_in_country(self, job: dict, country: Optional[str],
                        offices_discriminate: bool) -> bool:
        """The ladder (§4 of the S15 design). country=None → True
        (no filter requested — caller never drops)."""
        if not country:
            return True
        loc_name = (job.get("location") or {}).get("name") or ""
        e2 = self._segments(loc_name)
        # rung 1 — explicit target-country phrase in an E2 segment
        for seg in e2:
            if workday.country_str_matches(seg, country):
                return True
        # rung 2 — office evidence, only when the channel discriminates
        e1: list[str] = []
        for office in job.get("offices") or []:
            e1.extend(self._segments((office or {}).get("location")))
        if offices_discriminate:
            for seg in e1:
                if workday.country_str_matches(seg, country):
                    return True
        # null-E2 fallback: disqualified offices AND empty free-text —
        # consulting the (uniform) office channel beats dropping every
        # row of a board that serves zero location free-text.
        if not offices_discriminate and not e2 and e1:
            for seg in e1:
                if workday.country_str_matches(seg, country):
                    return True
        # rung 3 — US state-token fallback (United States only):
        # E2 always; E1 ONLY when the channel discriminates (a
        # disqualified default office must contribute no state token —
        # baidu's Sunnyvale office would otherwise rescue the Toronto
        # rows via its 'CA' token). Tokens are whitespace-split
        # INSIDE segments ('CA 95337' → 'CA').
        if (country or "").strip().lower() in workday._US_COUNTRY_NAMES:
            segs = (e2 + e1) if offices_discriminate else e2
            for seg in segs:
                for tok in seg.split():
                    if tok.strip(",()").lower() in workday._US_STATE_TOKENS:
                        return True
        return False

    @staticmethod
    def _time_type_from_metadata(job: dict) -> str:
        """metadata[{name: 'Employment Type', value}] → _TT-normalized
        ('Full-time' → 'Full time'). '' when absent; unmapped values
        pass through as-served (the ashby precedent — honest)."""
        for m in (job.get("metadata") or []):
            if isinstance(m, dict) and m.get("name") == "Employment Type":
                v = str(m.get("value") or "").strip()
                if not v:
                    return ""
                return _TT.get(v.lower(), v)
        return ""

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        jobs = self._jobs()
        serves_tt = any(self._time_type_from_metadata(j) for j in jobs)
        if time_type and not serves_tt:
            print(f"[{progress_label}] NOTE: greenhouse serves no "
                  f"employment-type field — time filter {time_type!r} "
                  f"NOT applied (honest: no guessing)",
                  file=sys.stderr, flush=True)
        offices_discriminate = self._offices_discriminate(jobs)
        rows: dict[str, dict] = {}
        dropped_country = dropped_tt = 0
        for job in jobs:
            rid = str(job.get("requisition_id") or job.get("id") or "")
            if not rid:
                continue
            if rid in rows:
                continue                      # requisition_id duplicates
            tt = self._time_type_from_metadata(job)
            if time_type and serves_tt and tt \
                    and tt.lower() != time_type.lower():
                dropped_tt += 1
                continue
            if country and not self._job_in_country(
                    job, country, offices_discriminate):
                dropped_country += 1
                continue
            label, iso = _posted_label(job.get("first_published"))
            row = {
                "reqId": rid,
                "title": job.get("title") or "",
                "company": self.company,
                "url": job.get("absolute_url") or "",
                "externalPath": f"/jobs/{job.get('id')}",
                "locationsText": (job.get("location") or {}).get("name")
                                 or "",
                "postedOn": label,
                "timeType": tt,
                "bulletFields": [rid],
                "ats": "greenhouse",
                "firstPublishedIso": iso,
                "departments": _dedup_keep_order(
                    [str(d.get("name") or "") for d in
                     job.get("departments") or []]),
                "countries": [country] if country else [],
                "applicationDeadline": job.get("application_deadline")
                                        or "",
            }
            rows[rid] = row
        meta = {
            "complete": True, "total": len(jobs), "pages": 1,
            "country_client": False,   # classified at list time
            "client_filtered": dropped_country + dropped_tt,
            "client_filtered_country": dropped_country,
            "client_filtered_time": dropped_tt,
            "offices_discriminate": offices_discriminate,
            "ats": "greenhouse",
        }
        drop_note = ""
        if country or time_type:
            parts = []
            if country:
                parts.append(f"{dropped_country} non-{country}")
            if time_type:
                parts.append(f"{dropped_tt} non-{time_type}")
            drop_note = f" ({' + '.join(parts)} dropped client-side)"
        print(f"[{progress_label}] greenhouse:{self.org}: {len(rows)} rows"
              + drop_note, file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        key = str(external_path or "").rsplit("/", 1)[-1].strip()
        for job in self._jobs():
            jid = str(job.get("id"))
            if jid == key or str(job.get("requisition_id")) == key:
                offices = job.get("offices") or []
                # S15 seam threading: when the caller threads the
                # country (dump details phase / watch enrich), the
                # SAME ladder verdict that admitted the list row
                # produces the detail's country descriptor — list and
                # detail agree by construction. Unthreaded (None):
                # the pre-S15 office-derived descriptor (back-compat
                # with the existing pin).
                if country:
                    verdict = self._job_in_country(
                        job, country, self._offices_discriminate(
                            self._jobs()))
                    countries = [country] if verdict else []
                else:
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
                        "timeType": self._time_type_from_metadata(job),
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

    # S15: _TT hoisted module-level (shared with the greenhouse
    # metadata extractor) — this class attribute removed to prevent
    # drift between the two normalizers.

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
        return _TT.get(tt, job.get("employmentType") or "")

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

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
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


_ADAPTERS_PLACEHOLDER = None  # real registry at module bottom (S14)


# ── S14: impersonated transport (Akamai/WAF boards) ─────────────────────
#
# ByteDance (Akamai 508/Access-Denied on plain requests — measured), and
# the Alibaba/Trip.com boards all need a chrome-impersonated POST. The
# helpers are module-level so tests monkeypatch them exactly like
# fetch_json. curl_cffi is a package dependency (pyproject, S14).

_IMP_SESSION = None


def _imp_session():
    """Lazily-built shared chrome-impersonated session (cookie jar kept —
    the alibaba XSRF-TOKEN flow relies on it)."""
    global _IMP_SESSION
    if _IMP_SESSION is None:
        from curl_cffi import requests as _cffi_requests  # lazy: py-level dep
        _IMP_SESSION = _cffi_requests.Session(impersonate="chrome")
    return _IMP_SESSION


def _imp_post_json(url: str, body: dict, headers: Optional[dict] = None,
                   timeout: float = 30.0) -> dict:
    """POST → parsed JSON. Non-200 raises RuntimeError (the B1 fail-safe
    contract: never partial-complete). Non-JSON bodies raise too."""
    r = _imp_session().post(url, json=body, headers=headers or {},
                            timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"{url}: HTTP {r.status_code} "
                           f"(impersonated POST)")
    try:
        return r.json()
    except Exception as e:  # XML/challenge page instead of JSON
        raise RuntimeError(f"{url}: non-JSON response ({e})") from e


def _imp_get(url: str, headers: Optional[dict] = None,
             timeout: float = 25.0):
    """GET via the impersonated session (cookie collection)."""
    return _imp_session().get(url, headers=headers or {}, timeout=timeout)


def _imp_post_json_retry(url: str, body: dict,
                         headers: Optional[dict] = None,
                         timeout: float = 30.0, attempts: int = 2,
                         backoff_s: float = 2.0) -> dict:
    """POST → parsed JSON with one retry. S14 run-#42 lesson: a single
    Akamai/CDN hiccup (curl-28 timeout, 0 bytes received — observed on
    GHA after 4 consecutive watch legs) must not fail a whole watch run.
    The page request is a pure idempotent query, so retrying it is
    safe; the session is RESET between attempts (a fresh TLS
    fingerprint — the dead connection does not poison the retry). A
    persistent outage still raises loudly after the last attempt (the
    watch's fail-safe contract stays intact)."""
    last: Exception = RuntimeError(f"{url}: no attempts made")
    for attempt in range(attempts):
        try:
            return _imp_post_json(url, body, headers=headers,
                                  timeout=timeout)
        except Exception as e:
            last = e
            if attempt + 1 < attempts:
                _imp_reset_session()
                time.sleep(backoff_s * (attempt + 1))
    raise last


def _imp_reset_session():
    """Test hook: drop the shared session (cross-test isolation)."""
    global _IMP_SESSION
    _IMP_SESSION = None


# ── bytedance (own platform: Feishu atsx-throne supplier API) ────────────

class ByteDanceAdapter:
    """jobs.bytedance.com/api/v1/public/supplier/search/job/posts — the
    INTERNATIONAL portal (header website-path: 'en'; the joinbytedance.com
    SPA fronts this API cross-origin; the zh portal serves China-only).

    Live-pinned 2026-09-19: full en board 1,413 rows / 594 US (443 Regular,
    147 Intern, 4 Third-party Associate). Pagination: limit=200, offset,
    NO total field → loop until a short page. Rows carry the COMPLETE
    posting (description + requirement + 2-level job_category + a 3-level
    city_info hierarchy) — details serve from the cached pages, zero extra
    network. NO dates anywhere (postedOn blank — honest; corroborated rows
    gain daysOnMarket from LI card dates, verified).

    Country classification (S13 rule, by design): each row's
    city_info.parent.parent.en_name is the AUTHORITATIVE country from the
    site's own structured field — classify AT LIST TIME. Unified unknown
    rule: blank/missing city_info → dropped + loudly counted
    (unresolved_dropped) — never claim US without evidence.

    reqId: the site's own `code` (e.g. 'A91359') — RAW, not namespaced
    (the corroborate join requires exact card↔row equality; namespacing
    kills every tier — review SEV-1).
    """

    KIND = "bytedance"
    _API = "https://jobs.bytedance.com/api/v1/public/supplier/search/job/posts"
    _HDRS = {"Content-Type": "application/json",
             "Referer": "https://joinbytedance.com/",
             "Origin": "https://joinbytedance.com",
             "website-path": "en"}
    _PAGE = 200
    _TT = {"regular": "Full time", "intern": "Internship",
           "third-party associate": "Contract",
           "full-time": "Full time", "part-time": "Part time"}

    def __init__(self, org: str, cfg: Config):
        self.org = org  # unused (custom: grammar) — kept for dispatch shape
        self.cfg = cfg
        self.company = "bytedance"

    def _fetch_pages(self) -> list[dict]:
        """Full en-portal board (all pages), cached per process."""
        spec = f"custom:{self.KIND}"
        now = time.monotonic()
        hit = _CACHE.get(spec)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
        posts: list[dict] = []
        offset = 0
        first_empty = True
        while True:
            body = {"recruitment_id_list": [], "job_category_id_list": [],
                    "subject_id_list": [], "location_code_list": [],
                    "keyword": "", "limit": self._PAGE, "offset": offset}
            d = _imp_post_json_retry(self._API, body,
                                     headers=self._HDRS)
            data = d.get("data") or {}
            page = data.get("job_post_list") or []
            if not isinstance(page, list):
                raise RuntimeError(f"{self._API}: job_post_list not a list")
            if not page and offset == 0:
                if first_empty:
                    # SEV-5: one retry — an Akamai soft-block can serve an
                    # empty 200; a real empty board stays empty after retry.
                    first_empty = False
                    time.sleep(1.0)
                    continue
                print(f"[bytedance] WARNING: page 1 empty after retry — "
                      f"board reported empty (watch zero-row guard is the "
                      f"second defense)", file=sys.stderr, flush=True)
            posts.extend(page)
            if len(page) < self._PAGE:
                break
            offset += self._PAGE
            if offset > 20000:  # circuit breaker
                raise RuntimeError(f"{self._API}: pagination runaway")
        _CACHE[spec] = (time.monotonic(), posts)
        return posts

    def _row_country(self, post: dict) -> str:
        """Authoritative country from the row's own 3-level hierarchy."""
        ci = post.get("city_info") or {}
        parent = (ci or {}).get("parent") or {}
        country = ((parent or {}).get("parent") or {}).get("en_name") or ""
        if not country:
            country = ""
        return str(country).strip()

    def _time_type(self, post: dict) -> str:
        rt = ((post.get("recruit_type") or {}).get("en_name")) or ""
        tt = self._TT.get(str(rt).strip().lower(), str(rt).strip())
        return "" if tt.lower() in ("", "none") else tt

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        posts = self._fetch_pages()
        rows: dict[str, dict] = {}
        dropped_country = dropped_tt = unresolved = dupes = 0
        for post in posts:
            rid = str(post.get("code") or post.get("id") or "")
            if not rid or rid in rows:
                if rid:
                    dupes += 1
                continue
            c = self._row_country(post)
            if country:
                if not c:
                    unresolved += 1          # unified rule: drop + count
                    continue
                if not workday.country_str_matches(c, country):
                    dropped_country += 1
                    continue
            tt = self._time_type(post)
            if time_type and tt and tt.lower() != time_type.lower():
                dropped_tt += 1
                continue
            cat = post.get("job_category") or {}
            parent = (cat.get("parent") or {}).get("en_name") or ""
            label, _iso = _posted_label("")     # no dates on this API
            row = {
                "reqId": rid,
                "title": post.get("title") or "",
                "company": self.company,
                "url": f"https://joinbytedance.com/search/{post.get('id')}",
                "externalPath": f"/bytedance/{rid}",
                "locationsText": (post.get("city_info") or {}).get("en_name")
                                 or "",
                "postedOn": label,               # "" (honest)
                "timeType": tt,
                "bulletFields": [rid],
                "ats": "bytedance",
                "departments": _dedup_keep_order([parent,
                                                  cat.get("en_name") or ""]),
                "countries": [c] if c else [],
            }
            rows[rid] = row
        meta = {
            "complete": True, "total": len(posts),
            "pages": (len(posts) + self._PAGE - 1) // self._PAGE,
            "country_client": False,   # classified at list time
            "client_filtered": dropped_country + dropped_tt,
            "unresolved_dropped": unresolved,
            "duplicate_codes": dupes,
            "ats": "bytedance",
        }
        print(f"[{progress_label}] bytedance: {len(rows)} rows of "
              f"{len(posts)} en-portal posts"
              + (f" ({dropped_country} non-{country}, {dropped_tt} "
                 f"non-{time_type}, {unresolved} unresolved-location, "
                 f"{dupes} dupes dropped)" if country or time_type else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        key = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        for post in self._fetch_pages():
            if key in (str(post.get("code") or ""),
                       str(post.get("id") or "")):
                c = self._row_country(post)
                cat = post.get("job_category") or {}
                desc = post.get("description") or ""
                req = post.get("requirement") or ""
                return {
                    "jobPostingInfo": {
                        "title": post.get("title") or "",
                        "location": (post.get("city_info") or {})
                                    .get("en_name") or "",
                        "additionalLocations": [],
                        "jobDescription": (desc + "\n\n" + req).strip(),
                        "timeType": self._time_type(post),
                        "startDate": "",
                        "externalUrl": f"https://joinbytedance.com/search/{post.get('id')}",
                        "jobReqId": str(post.get("code")
                                        or post.get("id") or ""),
                        "postedOn": "",
                        "country": {"descriptor": c} if c else None,
                    },
                    "hiringOrganization": {"name": "ByteDance"},
                    "similarJobs": [],
                }
        return None


# ── alibaba (own platform: multi-host Lumos sweep) ───────────────────────

class AlibabaAdapter:
    """Alibaba's 'Lumos' careers platform — SAME channel API on per-BU
    hosts: POST {host}/position/search?_csrf={XSRF-TOKEN cookie} with
    channel 'group_overseas_official_site', language 'en' (cookie flow:
    GET /en/off-campus/position-list first). Multi-host sweep, one label:
    the US postings live on different BUs' boards.

    Host registry (live-pinned 2026-09-19; cloud host CORRECTED 2026-09-24:
    careers-alibabacloud.com went globally NXDOMAIN — Google+Cloudflare DNS
    both Status 3, not egress-local; the live host is careers.alibabacloud.com,
    SAME Lumos API + cookie flow, S16-census-measured 246 rows / 25 US.
    fail-soft retained: 3 attempts, then LOUD skip + complete=False —
    never mass-false-gone):
      aidc      aidc-jobs.alibaba.com            153 rows,   6 US
      cloud     careers.alibabacloud.com         246 rows,  25 US (S16 fix)
      holding   talent-holding.alibaba.com       AGH board,  0 US today
      tongyi    careers-tongyi.alibaba.com       Token Foundry, EMPTY

    Rows carry the complete posting (description, requirement, degree,
    experience, publishTime epoch-ms, workLocations city-level English).
    NO region filter vocabulary exists on the API → client-side country
    classification via a CURATED city→country map (the observed board
    vocabulary, ~45 cities; pinned by tests). Unified unknown rule:
    unknown city → dropped + loudly counted.

    reqId: the RAW site code (e.g. 'GP7000022511') — host provenance lives
    in externalPath '/{hostkey}/{code}'; cross-host duplicate codes dedup
    keep-first + loud count (one requisition must not ship two rows).
    """

    KIND = "alibaba"
    _CHANNEL = "group_overseas_official_site"
    _HOSTS = [
        ("aidc", "aidc-jobs.alibaba.com"),
        ("cloud", "careers.alibabacloud.com"),
        ("holding", "talent-holding.alibaba.com"),
        ("tongyi", "careers-tongyi.alibaba.com"),
    ]
    # S16: the NEW cloud host (careers.alibabacloud.com, after the old
    # hyphenated host went NXDOMAIN) REJECTS chrome-impersonated POSTs
    # (HTTP 405 whitelabel — live-measured 2026-09-24) but serves PLAIN
    # requests (200). Inverse of the other Lumos hosts (Akamai-gated,
    # impersonation required). Transport is per-hostkey.
    _PLAIN_HOSTS = {"cloud"}
    _PAGE = 50          # pageSize cap (measured)
    _ATTEMPTS = 3       # per-host fail-soft (DNS-volatile cloud host)
    # curated city→country map — the observed overseas-board vocabulary
    # (live-measured 2026-09-19 across all four hosts); unknown cities
    # fall to the loud unresolved_dropped path, never guessed.
    _CITY_COUNTRY = {
        # US
        "sunnyvale": "United States", "bellevue": "United States",
        "seattle": "United States", "pasadena": "United States",
        "washington d.c.": "United States", "atlanta": "United States",
        "san mateo": "United States", "santa clara": "United States",
        "san francisco": "United States", "los angeles": "United States",
        "new york": "United States", "san jose": "United States",
        "austin": "United States", "fremont": "United States",
        "palo alto": "United States", "boston": "United States",
        # non-US (observed vocabulary)
        "hangzhou": "China", "beijing": "China", "shanghai": "China",
        "shenzhen": "China", "guangzhou": "China", "chengdu": "China",
        "hong kong, china": "Hong Kong SAR", "macau, china": "Macau SAR",
        "kuala lumpur": "Malaysia", "johor bahru": "Malaysia",
        "singapore": "Singapore", "tokyo": "Japan", "seoul": "South Korea",
        "sydney": "Australia", "melbourne": "Australia",
        "london": "United Kingdom", "edinburgh": "United Kingdom",
        "dublin": "Ireland", "amsterdam": "Netherlands",
        "paris": "France", "munich": "Germany", "frankfurt": "Germany",
        "berlin": "Germany", "zurich": "Switzerland", "milano": "Italy",
        "madrid": "Spain", "warszawa": "Poland", "stockholm": "Sweden",
        "helsinki": "Finland", "dubai": "United Arab Emirates",
        "riyadh": "Saudi Arabia", "riyad": "Saudi Arabia",
        "johannesburg": "South Africa", "bangkok": "Thailand",
        "ho chi minh city": "Vietnam", "hanoi": "Vietnam",
        "jakarta": "Indonesia", "jakarta raya": "Indonesia",
        "daerah khusus ibukota jakarta": "Indonesia",
        "jawa barat": "Indonesia", "manila": "Philippines",
        "karachi": "Pakistan", "lahore": "Pakistan", "islamabad": "Pakistan",
        "multan": "Pakistan", "sargodha": "Pakistan", "dhaka": "Bangladesh",
        "delhi": "India", "mumbai": "India", "kathmandu": "Nepal",
        "colombo": "Sri Lanka", "bagmati": "Nepal",
        "mexico city": "Mexico", "queretaro": "Mexico",
        "sao paulo": "Brazil", "istanbul": "Turkey",
    }

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg
        self.company = "alibaba"

    # ── host sweep ───────────────────────────────────────────────────────

    def _host_rows(self, hostkey: str, host: str) -> tuple[list[dict], bool]:
        """All rows from one host. Returns (rows, ok); ok=False means the
        host was unreachable after retries (fail-soft, complete=False)."""
        base = f"https://{host}"
        last_err: Optional[Exception] = None
        for attempt in range(self._ATTEMPTS):
            try:
                if hostkey in self._PLAIN_HOSTS:
                    rows = self._host_rows_plain(base)
                else:
                    rows = self._host_rows_imp(base)
                return rows, True
            except Exception as e:            # DNS/WAF/shape — fail-soft
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        print(f"[alibaba] WARNING: host {hostkey} ({host}) unreachable "
              f"after {self._ATTEMPTS} attempts ({last_err}) — SKIPPED, "
              f"sweep marked incomplete (complete=False so the watch "
              f"never computes false gones)", file=sys.stderr, flush=True)
        return [], False

    def _host_rows_plain(self, base: str) -> list[dict]:
        """The NEW cloud host's transport (S16): plain requests session;
        chrome-impersonation is 405-rejected here (live-measured)."""
        import requests as _plain
        s = _plain.Session()
        s.headers.update({"User-Agent":
                         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36"})
        r0 = s.get(base + "/en/off-campus/position-list?lang=en",
                   timeout=20)
        r0.raise_for_status()
        xsrf = next((c.value for c in s.cookies
                     if getattr(c, "name", "") == "XSRF-TOKEN"), None)
        if not xsrf:
            raise RuntimeError("no XSRF-TOKEN cookie served")
        out: list[dict] = []
        page = 1
        while True:
            body = {"channel": self._CHANNEL, "language": "en",
                    "pageIndex": page, "pageSize": self._PAGE}
            d = s.post(f"{base}/position/search?_csrf={xsrf}",
                       json=body, timeout=20).json()
            content = d.get("content") or {}
            datas = content.get("datas") or []
            if not isinstance(datas, list):
                raise RuntimeError("datas not a list")
            out.extend(datas)
            total = content.get("totalCount")
            if (len(datas) < self._PAGE
                    or (isinstance(total, int) and total
                        and len(out) >= total)):
                break
            page += 1
            if page > 60:  # circuit breaker
                raise RuntimeError("pagination runaway")
        return out

    def _host_rows_imp(self, base: str) -> list[dict]:
        """The Akamai-gated hosts' transport (S14): chrome-impersonated."""
        r0 = _imp_get(base + "/en/off-campus/position-list?lang=en")
        if r0.status_code != 200:
            raise RuntimeError(f"page HTTP {r0.status_code}")
        xsrf = None
        for c in _imp_session().cookies.jar:
            if getattr(c, "name", "") == "XSRF-TOKEN":
                xsrf = getattr(c, "value", None)
        if not xsrf:
            raise RuntimeError("no XSRF-TOKEN cookie served")
        out: list[dict] = []
        page = 1
        while True:
            body = {"channel": self._CHANNEL, "language": "en",
                    "batchId": "", "categories": "",
                    "deptCodes": [], "key": "",
                    "pageIndex": page, "pageSize": self._PAGE,
                    "regions": "", "subCategories": ""}
            d = _imp_post_json(
                f"{base}/position/search?_csrf={xsrf}", body,
                headers={"Content-Type": "application/json",
                         "Referer": base
                         + "/en/off-campus/position-list?lang=en"})
            content = d.get("content") or {}
            datas = content.get("datas") or []
            if not isinstance(datas, list):
                raise RuntimeError(f"datas not a list")
            out.extend(datas)
            total = content.get("totalCount")
            if (len(datas) < self._PAGE
                    or (isinstance(total, int) and total and
                        len(out) >= total)):
                break
            page += 1
            if page > 60:  # circuit breaker
                raise RuntimeError("pagination runaway")
        return out

    def _sweep(self) -> list[dict]:
        """All hosts' rows (cached); skipped hostkeys go to the
        _ALIBABA_SKIPPED side channel (set at cache-fill time)."""
        spec = f"custom:{self.KIND}"
        now = time.monotonic()
        hit = _CACHE.get(spec)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
        allrows: list[dict] = []
        skipped: list[str] = []
        for hostkey, host in self._HOSTS:
            rows, ok = self._host_rows(hostkey, host)
            for r in rows:
                r["_hostkey"] = hostkey      # provenance (externalPath)
            allrows.extend(rows)
            if not ok:
                skipped.append(hostkey)
        _CACHE[spec] = (time.monotonic(), allrows)
        _ALIBABA_SKIPPED["v"] = skipped
        return allrows

    def _row_country(self, row: dict) -> str:
        """Client-side classification from workLocations (any-US
        semantics: a row with a US location among several is US)."""
        locs = row.get("workLocations") or []
        if not locs:
            return ""
        countries = []
        for loc in locs:
            c = self._CITY_COUNTRY.get(str(loc or "").strip().lower(), "")
            if c:
                countries.append(c)
        if not countries:
            return ""                        # unknown city vocabulary
        us = [c for c in countries
              if workday.country_str_matches(c, "United States")]
        return "United States" if us else countries[0]

    def _posted(self, row: dict) -> tuple[str, str]:
        pt = row.get("publishTime")
        if isinstance(pt, (int, float)) and pt > 0:
            dt = datetime.fromtimestamp(pt / 1000.0, tz=timezone.utc)
            return _posted_label(dt.date().isoformat())
        return "", ""

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        if time_type:
            print(f"[{progress_label}] NOTE: the alibaba overseas channel "
                  f"serves no employment-type field — time filter "
                  f"{time_type!r} NOT applied (honest: no guessing)",
                  file=sys.stderr, flush=True)
        allrows = self._sweep()
        skipped = list(_ALIBABA_SKIPPED["v"])
        rows: dict[str, dict] = {}
        dropped_country = unresolved = dupes = 0
        for row in allrows:
            rid = str(row.get("code") or row.get("id") or "")
            if not rid or rid in rows:
                if rid:
                    dupes += 1
                continue
            c = self._row_country(row)
            if country:
                if not c:
                    unresolved += 1
                    continue
                if not workday.country_str_matches(c, country):
                    dropped_country += 1
                    continue
            label, iso = self._posted(row)
            hostkey = row.get("_hostkey") or "alibaba"
            posurl = str(row.get("positionUrl") or "")
            host = dict(self._HOSTS).get(hostkey, "")
            row_out = {
                "reqId": rid,                  # RAW site code (SEV-1)
                "title": row.get("name") or "",
                "company": self.company,
                "url": (f"https://{host}{posurl}" if host and posurl
                        else f"https://{host}/en/off-campus/position-list"),
                "externalPath": f"/{hostkey}/{rid}",
                "locationsText": ", ".join(
                    str(x) for x in row.get("workLocations") or []),
                "postedOn": label,
                "timeType": "",
                "bulletFields": [rid],
                "ats": "alibaba",
                "firstPublishedIso": iso,
                "departments": _dedup_keep_order(
                    [str(x) for x in row.get("categories") or []]),
                "countries": [c] if c else [],
            }
            rows[rid] = row_out
        meta = {
            "complete": not skipped,          # SEV-2: partial ≠ complete
            "total": len(allrows),
            "pages": len(self._HOSTS),
            # False = rows in list.jsonl are ALREADY country-classified
            # (the greenhouse/ashby meaning; the curated map runs at list
            # time — NOT the netflix full-global-board + countryfilter
            # flow, which country_client=True would trigger).
            "country_client": False,
            "client_filtered": dropped_country,
            "unresolved_dropped": unresolved,
            "duplicate_codes": dupes,
            "hosts_skipped": skipped,
            "ats": "alibaba",
        }
        print(f"[{progress_label}] alibaba: {len(rows)} rows of "
              f"{len(allrows)} across {len(self._HOSTS)} hosts"
              + (f" ({dropped_country} non-{country} dropped, "
                 f"{unresolved} unresolved-location, {dupes} dupes)"
                 if country else "")
              + (f" — HOSTS SKIPPED: {skipped} (incomplete sweep)"
                 if skipped else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        key = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        for row in self._sweep():
            if key in (str(row.get("code") or ""), str(row.get("id") or "")):
                c = self._row_country(row)
                label, iso = self._posted(row)
                desc = row.get("description") or ""
                req = row.get("requirement") or ""
                return {
                    "jobPostingInfo": {
                        "title": row.get("name") or "",
                        "location": ", ".join(
                            str(x) for x in row.get("workLocations") or []),
                        "additionalLocations": [],
                        "jobDescription": (desc + "\n\n" + req).strip(),
                        "timeType": "",
                        "startDate": iso,   # exact publish date (UTC)
                        "externalUrl": row.get("positionUrl") or "",
                        "jobReqId": str(row.get("code")
                                        or row.get("id") or ""),
                        "postedOn": label,
                        "country": {"descriptor": c} if c else None,
                    },
                    "hiringOrganization": {"name": "Alibaba Group"},
                    "similarJobs": [],
                    "firstPublishedIso": iso,
                }
        return None


_ALIBABA_SKIPPED: dict = {"v": []}   # cache side-channel (host skips)


# ── tripcom (own platform: careers.trip.com oversea API) ────────────────

class TripComAdapter:
    """careers.trip.com/api/oversea/getOverseaJobAd — Trip.com Group's own
    board. SERVER-side country filter: condition.country takes ISO-alpha-3
    codes (taxonomy from /api/oversea/getLocation, type
    'OverseasCareersCountry'). Live-pinned 2026-09-19: 10 US postings.

    Transport trap (measured): without Accept: application/json the API
    serves XML — the guard is structural (the POST helper raises on
    non-JSON). Pager index/size are STRINGS. publishDate is an exact ISO
    date. fromId is the reqId ('MJ003945'); jobTitle embeds a trailing
    '(MJ…)' code artifact — stripped from the title (deterministic
    normalization; the code survives as reqId — the h1b house-title
    translation class), else the token-F1 verbatim join would fail on
    every row.
    """

    KIND = "tripcom"
    _API = "https://careers.trip.com/api/oversea/getOverseaJobAd"
    _LOC = "https://careers.trip.com/api/oversea/getLocation"
    _HDRS = {"Content-Type": "application/json",
             "Accept": "application/json",        # else XML comes back
             "Referer": "https://careers.trip.com/",
             "Origin": "https://careers.trip.com"}
    _PAGE = 50
    _MJ = re.compile(r"\s*\(MJ[0-9A-Za-z]+\)\s*$")
    _ISO3 = {"united states": "USA", "usa": "USA", "china": "CHN",
             "singapore": "SGP", "united kingdom": "GBR", "japan": "JPN"}
    # kindName dialect → workday timeType dialect (deterministic map,
    # the ashby FullTime→'Full time' class; unknown kinds pass through
    # raw — never guessed)
    _TT = {"regular": "Full time", "full-time": "Full time",
           "full time": "Full time", "intern": "Internship",
           "internship": "Internship", "part-time": "Part time",
           "part time": "Part time", "contract": "Contract",
           "temporary": "Temporary"}

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg
        self.company = "tripcom"

    def _country_codes(self) -> list[dict]:
        """Location taxonomy (name/code pairs) — cached per process."""
        spec = f"custom:{self.KIND}:locations"
        now = time.monotonic()
        hit = _CACHE.get(spec)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
        d = _imp_post_json_retry(self._LOC, {"countryCode": "",
                                       "type": "OverseasCareersCountry",
                                       "head": {"language": "en-US"}},
                           headers=self._HDRS)
        entries = d.get("retValue") or []
        if not isinstance(entries, list):
            raise RuntimeError(f"{self._LOC}: retValue not a list")
        _CACHE[spec] = (time.monotonic(), entries)
        return entries

    def _iso3_for(self, country: Optional[str]) -> str:
        if not country:
            return ""
        want = country.strip().lower()
        for e in self._country_codes():
            if str(e.get("name") or "").strip().lower() == want:
                return str(e.get("code") or "")
        return self._ISO3.get(want, "")

    def _fetch_us(self, iso3: str) -> list[dict]:
        """All rows for the ISO-3 country (server-side filter), cached."""
        spec = f"custom:{self.KIND}:{iso3}"
        now = time.monotonic()
        hit = _CACHE.get(spec)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
        out: list[dict] = []
        page = 1
        while True:
            body = {"condition": {"keyword": "", "jobId": [], "kind": [],
                                  "country": [iso3] if iso3 else [],
                                  "city": [], "bucode": [],
                                  "jobFamilyGroupCode": [],
                                  "jobFamilyCode": []},
                    "pager": {"index": str(page), "size": str(self._PAGE)},
                    "head": {"language": "en-US"}}
            d = _imp_post_json_retry(self._API, body,
                                     headers=self._HDRS)
            v = d.get("retValue") or {}
            jobs = v.get("recruitJobAdList") or []
            if not isinstance(jobs, list):
                raise RuntimeError(f"{self._API}: recruitJobAdList not a "
                                   f"list")
            out.extend(jobs)
            total = v.get("total")
            if (len(jobs) < self._PAGE
                    or (isinstance(total, int) and total
                        and len(out) >= total)):
                break
            page += 1
            if page > 40:
                raise RuntimeError(f"{self._API}: pagination runaway")
        _CACHE[spec] = (time.monotonic(), out)
        return out

    def _time_type(self, job: dict) -> str:
        k = str(job.get("kindName") or "").strip()
        tt = self._TT.get(k.lower(), k)
        return tt if tt else ""  # blank when absent — never guessed

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        iso3 = self._iso3_for(country)
        if country and not iso3:
            raise RuntimeError(f"tripcom: no ISO-3 code for country "
                               f"{country!r} in the site taxonomy")
        jobs = self._fetch_us(iso3)
        rows: dict[str, dict] = {}
        dropped_tt = dupes = 0
        for job in jobs:
            rid = str(job.get("fromId") or job.get("id") or "")
            if not rid or rid in rows:
                if rid:
                    dupes += 1
                continue
            tt = self._time_type(job)
            if time_type and tt and tt.lower() != time_type.lower():
                dropped_tt += 1
                continue
            title = self._MJ.sub("", str(job.get("jobTitle") or ""))
            label, iso = _posted_label(job.get("publishDate"))
            rows[rid] = {
                "reqId": rid,
                "title": title,
                "company": self.company,
                "url": (f"https://careers.trip.com/#/jobDetail?"
                        f"jobId={job.get('jobId')}"),
                "externalPath": f"/tripcom/{rid}",
                "locationsText": str(job.get("cityName") or ""),
                "postedOn": label,
                "timeType": tt,
                "bulletFields": [rid],
                "ats": "tripcom",
                "firstPublishedIso": iso,
                "departments": _dedup_keep_order(
                    [str(job.get("jobFamilyGroupName") or ""),
                     str(job.get("buName") or "")]),
                "countries": [country] if country else [],
            }
        meta = {
            "complete": True, "total": len(jobs), "pages": 1,
            "country_client": False,   # server-side filter (taxonomy ISO-3)
            "client_filtered": dropped_tt,
            "duplicate_codes": dupes,
            "ats": "tripcom",
        }
        print(f"[{progress_label}] tripcom: {len(rows)} rows "
              f"(server-side country={iso3!r}, {len(jobs)} served)"
              + (f" ({dropped_tt} non-{time_type} dropped)"
                 if time_type else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        key = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        for job in self._fetch_us(self._iso3_for(None) or "USA"):
            if key in (str(job.get("fromId") or ""), str(job.get("id") or "")):
                title = self._MJ.sub("", str(job.get("jobTitle") or ""))
                label, iso = _posted_label(job.get("publishDate"))
                desc = job.get("duty") or ""
                req = job.get("requirements") or ""
                return {
                    "jobPostingInfo": {
                        "title": title,
                        "location": str(job.get("cityName") or ""),
                        "additionalLocations": [],
                        "jobDescription": (desc + "\n\n" + req).strip(),
                        "timeType": self._time_type(job),
                        "startDate": iso,
                        "externalUrl": (f"https://careers.trip.com/#/"
                                        f"jobDetail?jobId={job.get('jobId')}"),
                        "jobReqId": str(job.get("fromId")
                                        or job.get("id") or ""),
                        "postedOn": label,
                        "country": {"descriptor": "United States"},
                    },
                    "hiringOrganization": {"name": "Trip.com Group"},
                    "similarJobs": [],
                    "firstPublishedIso": iso,
                }
        return None


# ── lever (S16: jobs.lever.co's own public listing API) ──────────────────

# ISO alpha-2 → the full names workday.country_str_matches compares
# against (live-observed on the weride board: US/AE/SG/CN). Unmapped
# codes pass through and fail the country match LOUDLY (never guessed).
_LEVER_CC = {
    "US": "United States", "CA": "Canada", "GB": "United Kingdom",
    "CN": "China", "SG": "Singapore", "AE": "United Arab Emirates",
    "DE": "Germany", "FR": "France", "NL": "Netherlands",
    "AU": "Australia", "JP": "Japan", "KR": "Korea, Republic of",
    "IN": "India", "BR": "Brazil", "MX": "Mexico", "TW": "Taiwan",
    "HK": "Hong Kong", "ES": "Spain", "IT": "Italy", "SE": "Sweden",
    "PL": "Poland", "IE": "Ireland", "CH": "Switzerland",
    "NZ": "New Zealand", "ZA": "South Africa", "ID": "Indonesia",
    "TH": "Thailand", "VN": "Vietnam", "MY": "Malaysia",
    "PH": "Philippines", "TR": "Turkey", "IL": "Israel",
    "SA": "Saudi Arabia", "AR": "Argentina", "CL": "Chile",
    "CO": "Colombia", "PE": "Peru", "PT": "Portugal", "DK": "Denmark",
    "FI": "Finland", "NO": "Norway", "AT": "Austria", "BE": "Belgium",
    "CZ": "Czechia", "RO": "Romania", "HU": "Hungary", "UA": "Ukraine",
}


class LeverAdapter:
    """api.lever.co/v0/postings/{org}?mode=json — the whole board in one
    public call (Lever's own job-boards API; descriptions included; the
    1,964 live lever boards in the ATS directory are this class).

    Field map (live-pinned 2026-09-24 on weride, 17 postings):
      reqId            id (a GUID — the ashby precedent: lever posts
                       carry no requisition id)
      url              hostedUrl (jobs.lever.co/{org}/{id})
      locationsText    categories.location ('San Jose, CA'), extras
                       appended from categories.allLocations
      country          the top-level 'country' field — STRUCTURED ISO
                       alpha-2 codes ('US','AE','SG','CN') — AUTHORITATIVE
                       (the S13 netflix lesson by design: never classify
                       from free text when a structured field exists).
                       Mapped through _LEVER_CC; unmapped passes through.
      timeType         categories.commitment ('Full-time' → the workday
                       dialect 'Full time' via the shared _TT table)
      departments      categories.team → jobFamilyGroup tags
      postedOn         label from createdAt (epoch-ms; EXACT, never
                       censored — the greenhouse/ashby contract)
      remoteType       workplaceType ('remote'/'hybrid'/'onsite')
    """

    KIND = "lever"

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    @property
    def _url(self) -> str:
        return f"https://api.lever.co/v0/postings/{self.org}?mode=json"

    def _jobs(self) -> list[dict]:
        return _fetch_cached(self._url, f"ats:lever:{self.org}", self.cfg)

    def _country(self, job: dict) -> str:
        c = str(job.get("country") or "").strip().upper()
        return _LEVER_CC.get(c, c)

    def _time_type(self, job: dict) -> str:
        tt = str((job.get("categories") or {}).get("commitment")
                 or "").strip().lower()
        return _TT.get(tt, (job.get("categories") or {}).get("commitment")
                       or "")

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
            tt = self._time_type(job)
            if time_type and tt and tt.lower() != time_type.lower():
                dropped_tt += 1
                continue
            c = self._country(job)
            if country and not workday.country_str_matches(c, country):
                # location fallback ONLY when the structured code is
                # absent (never override an authoritative mismatch)
                if c:
                    dropped_country += 1
                    continue
                loc = str((job.get("categories") or {}).get("location")
                          or "")
                last = loc.split(",")[-1].strip() if loc else ""
                if not workday.country_str_matches(last, country):
                    dropped_country += 1
                    continue
            cats = job.get("categories") or {}
            label, iso = _posted_label(_epoch_ms_to_iso(
                job.get("createdAt")))
            locs = _dedup_keep_order(
                [str(x) for x in (cats.get("allLocations") or [])]
                or [str(cats.get("location") or "")])
            row = {
                "reqId": rid,
                "title": job.get("text") or "",
                "company": self.org,
                "url": job.get("hostedUrl") or "",
                "externalPath": f"/{rid}",
                "locationsText": " | ".join(locs),
                "postedOn": label,
                "timeType": tt,
                "bulletFields": [rid],
                "ats": "lever",
                "firstPublishedIso": iso,
                "departments": [str(cats.get("team") or "")],
                "countries": [c] if c else [],
                "remoteType": str(job.get("workplaceType") or ""),
            }
            rows[rid] = row
        meta = {
            "complete": True, "total": len(jobs), "pages": 1,
            "country_client": False,
            "client_filtered": dropped_country + dropped_tt,
            "ats": "lever",
        }
        print(f"[{progress_label}] lever:{self.org}: {len(rows)} rows"
              + (f" ({dropped_country} non-{country} + {dropped_tt} "
                 f"non-{time_type} dropped client-side)" if country
                 or time_type else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        key = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        for job in self._jobs():
            if str(job.get("id")) == key:
                c = self._country(job)
                cats = job.get("categories") or {}
                label, iso = _posted_label(_epoch_ms_to_iso(
                    job.get("createdAt")))
                return {
                    "jobPostingInfo": {
                        "title": job.get("text") or "",
                        "location": str(cats.get("location") or ""),
                        "additionalLocations": _dedup_keep_order(
                            [str(x) for x in
                             (cats.get("allLocations") or [])
                             if x != cats.get("location")]),
                        "jobDescription": job.get("descriptionBody")
                        or job.get("description") or "",
                        "timeType": self._time_type(job),
                        "startDate": "",
                        "externalUrl": job.get("hostedUrl") or "",
                        "jobReqId": str(job.get("id") or ""),
                        "postedOn": label,
                        "country": {"descriptor": c} if c else None,
                    },
                    "hiringOrganization": {"name": self.org},
                    "similarJobs": [],
                    "firstPublishedIso": iso,
                    "compensation": job.get("salaryRange") or None,
                }
        return None


def _epoch_ms_to_iso(epoch_ms) -> Optional[str]:
    """Lever's createdAt (epoch-ms) → ISO timestamp (None on garbage)."""
    try:
        val = int(epoch_ms)
        if val <= 0:
            return None
        return (datetime.fromtimestamp(val / 1000.0, tz=timezone.utc)
                .isoformat())
    except (TypeError, ValueError):
        return None


# ── workable (S16: apply.workable.com widget API) ─────────────────────────

class WorkableAdapter:
    """apply.workable.com/api/v1/widget/accounts/{domain}?details=true —
    the whole board in one public call (the widget companies embed in
    their careers pages; descriptions included in details mode).

    Field map (live-pinned 2026-09-24 on tp-link-usa-corp, 86 jobs):
      reqId            shortcode (the stable URL component: /j/{code})
      url              url (apply.workable.com/j/{shortcode})
      locationsText    'city, state' (+ telecommuting flag → Remote)
      country          top-level 'country' — STRUCTURED full names
                       ('United States') — AUTHORITATIVE, ashby-style
      timeType         employment_type ('Full-time' → _TT 'Full time')
      departments      department → jobFamilyGroup tags
      postedOn         label from published_on (ISO date; EXACT)
      remoteType       'Remote' when telecommuting is true
      experience/education/function: available, mapped to departments
      extra locations  locations[] (multi-site postings)
    """

    KIND = "workable"

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    @property
    def _url(self) -> str:
        return (f"https://apply.workable.com/api/v1/widget/accounts/"
                f"{self.org}?details=true")

    def _jobs(self) -> list[dict]:
        return _fetch_cached(self._url, f"ats:workable:{self.org}",
                             self.cfg)

    def _time_type(self, job: dict) -> str:
        tt = str(job.get("employment_type") or "").strip().lower()
        return _TT.get(tt, job.get("employment_type") or "")

    @staticmethod
    def _loc_text(job: dict) -> str:
        parts = [str(job.get("city") or ""), str(job.get("state") or "")]
        txt = ", ".join(p for p in parts if p)
        if job.get("telecommuting"):
            txt = (txt + " (Remote)").strip(", ")
        return txt

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        jobs = self._jobs()
        rows: dict[str, dict] = {}
        dropped_country = dropped_tt = 0
        for job in jobs:
            rid = str(job.get("shortcode") or "")
            if not rid or rid in rows:
                continue
            tt = self._time_type(job)
            if time_type and tt and tt.lower() != time_type.lower():
                dropped_tt += 1
                continue
            c = str(job.get("country") or "").strip()
            if country and not workday.country_str_matches(c, country):
                dropped_country += 1
                continue
            label, iso = _posted_label(job.get("published_on"))
            row = {
                "reqId": rid,
                "title": job.get("title") or "",
                "company": self.org,
                "url": job.get("url") or "",
                "externalPath": f"/j/{rid}",
                "locationsText": self._loc_text(job),
                "postedOn": label,
                "timeType": tt,
                "bulletFields": [rid],
                "ats": "workable",
                "firstPublishedIso": iso,
                "departments": _dedup_keep_order(
                    [str(job.get("department") or ""),
                     str(job.get("function") or ""),
                     str(job.get("experience") or "")]),
                "countries": [c] if c else [],
                "remoteType": "Remote" if job.get("telecommuting") else "",
            }
            # multi-site postings: extra locations[]
            extra = [str(x.get("city") or "") + (", " + str(x.get("state"))
                     if x.get("state") else "")
                     for x in (job.get("locations") or [])
                     if isinstance(x, dict) and x.get("city")]
            if extra:
                row["locationsText"] = " | ".join(
                    _dedup_keep_order([row["locationsText"]] + extra))
            rows[rid] = row
        meta = {
            "complete": True, "total": len(jobs), "pages": 1,
            "country_client": False,
            "client_filtered": dropped_country + dropped_tt,
            "ats": "workable",
        }
        print(f"[{progress_label}] workable:{self.org}: {len(rows)} rows"
              + (f" ({dropped_country} non-{country} + {dropped_tt} "
                 f"non-{time_type} dropped client-side)" if country
                 or time_type else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        key = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        for job in self._jobs():
            if str(job.get("shortcode")) == key:
                c = str(job.get("country") or "").strip()
                label, iso = _posted_label(job.get("published_on"))
                return {
                    "jobPostingInfo": {
                        "title": job.get("title") or "",
                        "location": self._loc_text(job),
                        "additionalLocations": _dedup_keep_order(
                            [f"{x.get('city', '')}, {x.get('state', '')}"
                             .strip(", ")
                             for x in (job.get("locations") or [])
                             if isinstance(x, dict) and x.get("city")]),
                        "jobDescription": job.get("description") or "",
                        "timeType": self._time_type(job),
                        "startDate": "",
                        "externalUrl": job.get("url") or "",
                        "jobReqId": str(job.get("shortcode") or ""),
                        "postedOn": label,
                        "country": {"descriptor": c} if c else None,
                    },
                    "hiringOrganization": {"name": self.org},
                    "similarJobs": [],
                    "firstPublishedIso": iso,
                }
        return None


# ── feishuhire (S17: {portal}.jobs.feishu.cn throne portals) ─────────────

# The feishu city taxonomy is FLAT (city names, no country hierarchy — the
# detail's city_info_list_for_delivery is the same flat list). Country is
# classified from this curated map (alibaba precedent). Unknown city OR
# empty city_list → row unresolved (dropped + loudly counted,
# complete=False — the never-false-gone B1 contract: a US row may hide
# behind an unmapped name). 'lle-de-France': the API strips the Î
# diacritic (live-observed; same for 'Sao Paulo').
# AMBIGUOUS names stay UNMAPPED BY DESIGN (unresolved = loud, never
# guessed): 'Cambridge' (MA vs UK). 'San Jose' is a latent US/Costa Rica
# collision (diacritics stripped) — not on any wired board; if a portal
# ever serves LatAm 'San Jose' rows the fix is the row's mdm_code, not
# the map (MDCY codes are the disambiguation key, future work).
_FEISHU_CITY_COUNTRY = {
    # China
    "Beijing": "China", "Shanghai": "China", "Shenzhen": "China",
    "Hangzhou": "China", "Guangzhou": "China", "Chengdu": "China",
    "Chongqing": "China", "Wuhan": "China", "Xi'an": "China",
    "Nanjing": "China", "Tianjin": "China", "Hefei": "China",
    "Suzhou": "China", "Changsha": "China", "Zhengzhou": "China",
    "Qingdao": "China", "Xiamen": "China", "Jinan": "China",
    "Dalian": "China", "Shenyang": "China", "Harbin": "China",
    "Changchun": "China", "Kunming": "China", "Guiyang": "China",
    "Nanning": "China", "Fuzhou": "China", "Shijiazhuang": "China",
    "Taiyuan": "China", "Lanzhou": "China", "Urumqi": "China",
    "Hohhot": "China", "Yinchuan": "China", "Xining": "China",
    "Haikou": "China", "Sanya": "China", "Dongguan": "China",
    "Foshan": "China", "Ningbo": "China", "Wuxi": "China",
    # Hong Kong / Macau / Taiwan
    "Hong Kong (China)": "Hong Kong", "Hong Kong": "Hong Kong",
    "New Territories": "Hong Kong", "Kowloon": "Hong Kong",
    "Macau": "Macau", "Taipei": "Taiwan",
    # United States (pre-seeded with CN-tech US geos — finding 11:
    # high-likelihood satellites before future portals need them)
    "San Francisco": "United States", "New York": "United States",
    "Seattle": "United States", "Los Angeles": "United States",
    "San Jose": "United States", "Mountain View": "United States",
    "Palo Alto": "United States", "Boston": "United States",
    "Austin": "United States", "Chicago": "United States",
    "San Diego": "United States", "Irvine": "United States",
    "Santa Clara": "United States", "Cupertino": "United States",
    "Sunnyvale": "United States", "Redmond": "United States",
    "Bellevue": "United States", "Atlanta": "United States",
    "Dallas": "United States", "Houston": "United States",
    "Denver": "United States", "Miami": "United States",
    "Philadelphia": "United States", "Phoenix": "United States",
    "Portland": "United States", "Washington": "United States",
    "San Mateo": "United States", "Menlo Park": "United States",
    "Redwood City": "United States", "Fremont": "United States",
    "Milpitas": "United States", "Santa Monica": "United States",
    "El Segundo": "United States", "Pasadena": "United States",
    "Waltham": "United States", "Burlington": "United States",
    "Arlington": "United States", "Reston": "United States",
    "Tysons": "United States", "Pittsburgh": "United States",
    "Ann Arbor": "United States", "Princeton": "United States",
    "Jersey City": "United States", "San Ramon": "United States",
    # Other international (observed in live boards)
    "Singapore": "Singapore", "London": "United Kingdom",
    "Manchester": "United Kingdom", "Edinburgh": "United Kingdom",
    "Berlin": "Germany", "Munich": "Germany", "Madrid": "Spain",
    "Barcelona": "Spain", "Seoul": "Korea, Republic of",
    "Tokyo": "Japan", "Sapporo": "Japan", "Osaka": "Japan",
    "Dubai": "United Arab Emirates", "Abu Dhabi": "United Arab Emirates",
    "Mexico City": "Mexico", "Kuala Lumpur": "Malaysia",
    "Ile-de-France": "France", "lle-de-France": "France",
    "Paris": "France", "Sydney": "Australia", "Melbourne": "Australia",
    "Toronto": "Canada", "Vancouver": "Canada", "Montreal": "Canada",
    "Ottawa": "Canada", "Bangkok": "Thailand", "Jakarta": "Indonesia",
    "Ho Chi Minh City": "Vietnam", "Hanoi": "Vietnam",
    "Manila": "Philippines", "Mumbai": "India", "Bangalore": "India",
    "New Delhi": "India", "Hyderabad": "India", "Pune": "India",
    "Sao Paulo": "Brazil", "São Paulo": "Brazil",
    "Amsterdam": "Netherlands", "Milan": "Italy", "Rome": "Italy",
    "Stockholm": "Sweden", "Zurich": "Switzerland",
    "Warsaw": "Poland", "Istanbul": "Turkey", "Riyadh": "Saudi Arabia",
    "Doha": "Qatar", "Tel Aviv": "Israel", "Cairo": "Egypt",
    "Lagos": "Nigeria", "Nairobi": "Kenya",
}

# portal subdomain → display company for the row field (the CSV company
# comes from the CLI --company flag; this is the adapter-side fallback
# + the watch's alert text). Unlisted portals pass the code through.
# momenta + infinigence: live feishu portals confirmed by peer review
# (248/59 posts, 0 US today) — wire-ready when US roles appear.
_PORTAL_COMPANY = {
    "vrfi1sk8a0": "MiniMax", "shengshu": "Shengshu",
    "zhipu-ai": "Zhipu AI", "01ai": "01.AI", "sensetime": "SenseTime",
    "agirobot": "AgiBot", "nio": "NIO", "moonshot": "Moonshot AI",
    "momenta": "Momenta", "infinigence": "Infinigence",
}


class FeishuHireAdapter:
    """{portal}.jobs.feishu.cn/api/v1/search/job/posts — Feishu-Hire
    (Lark ATS, ByteDance's throne family) PUBLIC job-board API, the
    standard ATS for CN AI startups (S17 census: MiniMax, Zhipu, 01.AI,
    Baichuan, AgiBot, SenseTime, Shengshu, NIO, Momenta, Infinigence
    + 1000s more).

    Reverse-engineered live 2026-09-25 (browser network capture;
    peer-review-verified 2026-09-26):
      1. POST {base}/api/v1/csrf/token  → {"data":{"token":...}}
      2. POST {base}/api/v1/search/job/posts?...&portal_type=6&portal_entrance=1
         headers X-Csrf-Token + BODY {"offset": N, "limit": 200}
         → {"code": 0, "data": {"job_post_list": [...], "count": total}}
         ⚠ offset and limit BOTH work ONLY in the BODY (URL params are
         silently ignored → the naive probe sees 10 of N jobs — census
         undercount trap); body limit serves up to 200/call (live-proven:
         185/185 minimax in ONE request, 200/834 agirobot pages).
      3. GET {base}/api/v1/job/posts/{id}?portal_type=6&with_recommend=false
         → {"code": 0, "data": {"job_post_detail": {...description...}}}
         (search rows carry null description/requirement — details are
         per-id fetches; the workday details-phase contract unchanged).
      Plain urllib works — no impersonation, no signature (the URL
      _signature param is optional). The envelope's `code` MUST be 0
      (peer-review SEV-1: a 200-with-code≠0 mid-pagination is an ERROR,
      never an empty page — a silent short board is the false-gone hole).
      Detail GETs need NO cookie/token (cross-instance cache-hit safe).
      Job-page URLs 404 unless the request sends Accept: text/html —
      browsers always do (the CSV url is user-fine); plain-urllib
      validators must add the header (peer-review finding 9).

    Field map (live-pinned 2026-09-25, minimax 185 rows / shengshu 106):
      reqId            id (raw — the S14 join-safety decision)
      url              {base}/index/position/{id}/detail (universal path
                       — serves both /index/ and custom portal paths
                       like MiniMax's /379481/; see the Accept note above)
      locationsText    city_list[].en_name joined ' | '
      country          ANY city in the curated map mapping to the target
                       (MiniMax rows are 'Beijing/Shanghai/San Francisco'
                       multi-city — a US opening inside a CN-hybrid role
                       IS the user's target; netflix-class precedent).
                       A KNOWN US city + unmapped siblings → row KEPT
                       (provably US; siblings surface in unmapped_cities
                       — round-2 F2, the alibaba precedent). Empty
                       city_list or NO known target city → row
                       unresolved: dropped loudly + complete=False
                       (never false-gone). Ambiguous names (Cambridge)
                       stay UNMAPPED by design (loud, not guessed).
      timeType         recruit_type.en_name ('Full-time' → _TT 'Full
                       time'; 'Internship' stays; 'Consultant' and
                       'Outsourced' → Contract — the live-observed
                       vocabulary is {Full-time, Internship, Consultant,
                       Outsourced})
      departments      job_category.en_name + job_function.en_name
                       (job_function is null on minimax's 185 — single-
                       source in practice; job_category carries a real
                       depth-2 parent chain we do not walk)
      postedOn         publish_time epoch-ms → EXACT ISO date (the
                       alibaba publishTime contract)
      description      detail fetch (description + requirement joined)
    """

    KIND = "feishuhire"
    _SEARCH = ("/api/v1/search/job/posts?keyword=&limit=10&offset=0"
               "&job_category_id_list=&tag_id_list=&location_code_list="
               "&subject_id_list=&recruitment_id_list=&portal_type=6"
               "&job_function_id_list=&storefront_id_list="
               "&portal_entrance=1")
    _PAGE = 200         # body limit cap (live-verified 2026-09-26)
    _MAX_OFFSET = 5000  # circuit breaker (offset cap)

    def __init__(self, org: str, cfg: Config):
        self.org = org  # the portal subdomain
        self.cfg = cfg
        self.company = _PORTAL_COMPANY.get(org, org)
        self._jar = None
        self._opener = None
        self._token = ""

    # ── transport (plain urllib + cookie jar; token per process) ────────

    def _session(self):
        if self._opener is None:
            self._jar = http.cookiejar.CookieJar()
            self._opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(self._jar))
        return self._opener

    def _post_json(self, path: str, body: dict,
                   token: bool = True) -> dict:
        base = f"https://{self.org}.jobs.feishu.cn"
        h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
             "Content-Type": "application/json",
             "Referer": f"{base}/", "Origin": base}
        if token and self._token:
            h["X-Csrf-Token"] = self._token
        req = urllib.request.Request(base + path,
                                     data=json.dumps(body).encode(),
                                     headers=h, method="POST")
        with self._session().open(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def _get_json(self, path: str) -> dict:
        base = f"https://{self.org}.jobs.feishu.cn"
        req = urllib.request.Request(
            base + path, headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": f"{base}/"})
        with self._session().open(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def _token_refresh(self) -> str:
        d = self._post_json("/api/v1/csrf/token", {}, token=False)
        tok = ((d.get("data") or {}).get("token")) or ""
        if not tok:
            raise RuntimeError(f"feishuhire:{self.org}: no csrf token "
                               f"({str(d)[:120]})")
        self._token = tok
        return tok

    def _fetch_rows(self) -> list[dict]:
        """Full board (all pages), cached per process. The envelope's
        `code` MUST be 0 (peer-review SEV-1: a 200-with-code≠0 is an
        ERROR — treating it as an empty page silently truncates the
        board with complete=True = the false-gone hole). count is the
        authoritative total; pages end on a short page OR at count,
        and the distinct-id count is asserted against count before
        caching (a short/mid-pagination anomaly raises — never a
        silent partial). Cached posts are SHARED state: treat as
        immutable."""
        spec = f"ats:feishuhire:{self.org}"
        now = time.monotonic()
        hit = _CACHE.get(spec)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
        self._token_refresh()
        posts: list[dict] = []
        offset = 0
        pages = 0
        while True:
            d = self._search_page(offset)
            code = d.get("code")
            if code not in (0, None):
                raise RuntimeError(
                    f"feishuhire:{self.org}: envelope code={code} at "
                    f"offset {offset} (message={str(d.get('message'))
                        [:120]}) — refusing to cache a short board")
            data = d.get("data") or {}
            page = data.get("job_post_list") or []
            if not isinstance(page, list):
                raise RuntimeError(f"feishuhire:{self.org}: "
                                   f"job_post_list not a list")
            total = data.get("count")
            if not page:
                if offset == 0:
                    # count==0 = genuine empty board (live-verified on a
                    # moonshot portal); count missing/not-int = shape
                    # anomaly — raise, never cache an ambiguous empty
                    if isinstance(total, int) and total == 0:
                        break
                    raise RuntimeError(
                        f"feishuhire:{self.org}: empty page 0 with "
                        f"count={total!r} — shape anomaly, not an empty "
                        f"board (nothing cached)")
                # short/empty page mid-board: trust count or raise
                if isinstance(total, int) and total == len(posts):
                    break
                raise RuntimeError(
                    f"feishuhire:{self.org}: empty page at offset "
                    f"{offset} with {len(posts)}/{total} collected — "
                    f"mid-pagination anomaly, refusing short board")
            posts.extend(page)
            pages += 1
            if (len(page) < self._PAGE
                    or (isinstance(total, int) and total
                        and len(posts) >= total)):
                break
            offset += self._PAGE
            if offset > self._MAX_OFFSET:
                raise RuntimeError(f"feishuhire:{self.org}: "
                                   f"pagination runaway")
        # SEV-1 (b): distinct ids vs count — catches body-offset drift
        # (the API ignoring our offset would dedup pages to ~1 page)
        ids = {str(p.get("id")) for p in posts}
        if isinstance(total, int) and total and len(ids) != total:
            raise RuntimeError(
                f"feishuhire:{self.org}: collected {len(ids)} distinct "
                f"ids but the board says count={total} — pagination "
                f"drift (short board), refusing to cache")
        self._pages_fetched = pages
        _CACHE[spec] = (time.monotonic(), posts)
        return posts

    def _search_page(self, offset: int) -> dict:
        """One search POST with one transport retry (peer-review SEV-2
        #5 — the S14 _imp_post_json_retry class: a single hiccup must
        not fail the run). Retry refreshes the CSRF token + cookie jar."""
        last: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                return self._post_json(
                    self._SEARCH,
                    {"offset": offset, "limit": self._PAGE})
            except Exception as exc:  # transport errors only — HTTP
                # errors, timeouts, resets (envelope-code errors raise
                # in the caller)
                last = exc
                if attempt == 1:
                    # fresh token + fresh cookies, then retry once
                    self._opener = None
                    self._token = ""
                    try:
                        self._token_refresh()
                    except Exception:
                        pass
                    time.sleep(1.0)
        raise RuntimeError(f"feishuhire:{self.org}: search page at "
                           f"offset {offset} failed after retry: "
                           f"{type(last).__name__}: {last}")

    # ── classification helpers ──────────────────────────────────────────

    @staticmethod
    def _cities(job: dict) -> list[str]:
        out = []
        for c in (job.get("city_list") or []):
            name = str(c.get("en_name") or c.get("name") or "").strip()
            if name:
                out.append(name)
        return out

    def _time_type(self, job: dict) -> str:
        rt = str(((job.get("recruit_type") or {}).get("en_name"))
                 or "").strip()
        return _TT.get(rt.lower(), rt)

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        posts = self._fetch_rows()
        rows: dict[str, dict] = {}
        dropped_country = dropped_tt = unresolved = dupes = 0
        unmapped: set[str] = set()
        for post in posts:
            rid = str(post.get("id") or "")
            if not rid or rid in rows:
                if rid:
                    dupes += 1
                continue
            tt = self._time_type(post)
            if time_type and tt and tt.lower() != time_type.lower():
                dropped_tt += 1
                continue
            cities = self._cities(post)
            mapped = [_FEISHU_CITY_COUNTRY.get(c) for c in cities]
            if not cities:
                # empty city_list → unresolved (the unified blank-country
                # rule — bytedance/alibaba precedent: never guess, never
                # false-gone)
                unresolved += 1
                continue
            # round-2 F2 (SEV-2): a row with a KNOWN US city + unmapped
            # sibling cities is PROVABLY US — keep it (the ANY-city rule
            # the docstring already promises; the alibaba precedent —
            # unknown cities ignored, row kept when any known city hits).
            # Unmapped siblings still surface in unmapped_cities (map
            # growth); unresolved is reserved for rows with NO known
            # target city (their US-ness is the thing in doubt).
            any_target = any(m and workday.country_str_matches(m, country)
                             for m in mapped) if country else bool(mapped)
            if not any_target:
                if any(m is None for m in mapped) or not mapped:
                    unresolved += 1
                    unmapped.update(c for c, m in zip(cities, mapped)
                                    if m is None)
                    continue
                dropped_country += 1
                continue
            if any(m is None for m in mapped):
                unmapped.update(c for c, m in zip(cities, mapped)
                                if m is None)
            c = next((m for m in mapped
                      if m and workday.country_str_matches(m, "United States")), mapped[0] if mapped else "")
            label, iso = _posted_label(_epoch_ms_to_iso(
                post.get("publish_time")))
            jc = ((post.get("job_category") or {}).get("en_name")) or ""
            jf = ((post.get("job_function") or {}).get("en_name")) or ""
            rows[rid] = {
                "reqId": rid,
                "title": str(post.get("title") or ""),
                "company": self.company,
                "url": (f"https://{self.org}.jobs.feishu.cn"
                        f"/index/position/{rid}/detail"),
                # externalPath ends with the id (the detail_payload
                # last-segment contract — greenhouse '/jobs/{id}' form;
                # the real apply URL with the trailing /detail lives in
                # row['url'])
                "externalPath": f"/index/position/{rid}",
                "locationsText": " | ".join(cities),
                "postedOn": label,
                "timeType": tt,
                "bulletFields": [rid],
                "ats": "feishuhire",
                "firstPublishedIso": iso,
                "departments": _dedup_keep_order([jc, jf]),
                "countries": [c] if c else [],
            }
        meta = {
            # unknown/blank cities = possible hidden US rows → never
            # claim done (B1)
            "complete": unresolved == 0,
            "total": len(posts),
            "pages": getattr(self, "_pages_fetched", 1),
            "country_client": False,
            "client_filtered": dropped_country + dropped_tt + unresolved,
            "client_filtered_country": dropped_country,
            "client_filtered_time": dropped_tt,
            "duplicate_codes": dupes,
            "unresolved_dropped": unresolved,
            "unmapped_cities": sorted(unmapped),
            "ats": "feishuhire",
        }
        print(f"[{progress_label}] feishuhire:{self.org}: {len(rows)} "
              f"rows of {len(posts)} (count meta)"
              + (f" ({dropped_country} non-{country} + {dropped_tt} "
                 f"non-{time_type} + {unresolved} unresolved dropped"
                 f" client-side)" if country or time_type or unresolved
                 else "")
              + (f" UNMAPPED: {sorted(unmapped)}" if unmapped else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        for post in self._fetch_rows():
            if str(post.get("id")) != rid:
                continue
            cities = self._cities(post)
            mapped = [_FEISHU_CITY_COUNTRY.get(c) for c in cities]
            c = next((m for m in mapped
                      if m and workday.country_str_matches(
                          m, "United States")), mapped[0] if mapped else "")
            label, iso = _posted_label(_epoch_ms_to_iso(
                post.get("publish_time")))
            url = (f"https://{self.org}.jobs.feishu.cn"
                   f"/index/position/{rid}/detail")
            # search rows carry null description — fetch the real detail
            desc, req = "", ""
            try:
                d = self._get_json(f"/api/v1/job/posts/{rid}"
                                   f"?portal_type=6&with_recommend=false")
                if d.get("code") not in (0, None):
                    # 200-with-code≠0: error body, NOT an empty JD —
                    # warn loudly (peer-review finding 6); the row still
                    # serves with an empty description (B1: data loss ≠
                    # a degraded description)
                    print(f"[feishuhire:{self.org}] detail envelope "
                          f"code={d.get('code')} for {rid} — description "
                          f"served EMPTY (error body, not an empty JD)",
                          file=sys.stderr, flush=True)
                j = ((d.get("data") or {}).get("job_post_detail")) or {}
                desc = str(j.get("description") or "")
                req = str(j.get("requirement") or "")
                if self._cities(j):
                    # detail cities win; recompute the country from the
                    # SAME list the payload serves (consistency by
                    # construction — peer-review finding 6)
                    cities = self._cities(j)
                    mapped = [_FEISHU_CITY_COUNTRY.get(x) for x in cities]
                    c = next((m for m in mapped
                              if m and workday.country_str_matches(
                                  m, "United States")),
                             mapped[0] if mapped else "")
            except Exception as exc:  # transport failure ≠ data loss (B1)
                print(f"[feishuhire:{self.org}] detail fetch failed for "
                      f"{rid}: {exc}", file=sys.stderr, flush=True)
            jc = ((post.get("job_category") or {}).get("en_name")) or ""
            return {
                "jobPostingInfo": {
                    "title": str(post.get("title") or ""),
                    "location": cities[0] if cities else "",
                    "additionalLocations": cities[1:],
                    "jobDescription": (desc + "\n\n" + req).strip(),
                    "timeType": self._time_type(post),
                    "startDate": iso or "",
                    "externalUrl": url,
                    "jobReqId": rid,
                    "postedOn": label,
                    "country": {"descriptor": c} if c else None,
                },
                "hiringOrganization": {"name": self.company},
                "similarJobs": [],
                "firstPublishedIso": iso,
            }
        return None


def _post_json_urllib(url: str, body: dict,
                      headers: Optional[dict] = None) -> dict:
    """Plain-urllib JSON POST (no impersonation, no session) — for
    endpoints that serve plain requests and REJECT chrome-impersonation
    (the alibaba-cloud inversion class; also xiaohongshu, live-verified
    2026-09-26). Raises on non-JSON bodies (structural guard — the
    tripcom XML-fallback trap)."""
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
         "Content-Type": "application/json",
         "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    if not raw.lstrip().startswith(("{", "[")):
        raise RuntimeError(f"{url}: non-JSON response (first 60: "
                           f"{raw[:60]!r})")
    return json.loads(raw)


# ── xiaohongshu (own platform: job.xiaohongshu.com recruit API) ──────────

class XiaohongshuAdapter:
    """job.xiaohongshu.com/websiterecruit/position/pageQueryPosition —
    Xiaohongshu/RedNote's own board. SERVER-side workplace filter:
    `workplaces: ["840"]` = 美国 (United States) — the tripcom pattern
    (ANY-match: a '美国，新加坡，上海市，北京市' multi-site row IS in the
    US-filtered result — the netflix-class semantics).

    Reverse-engineered live 2026-09-26 (browser XHR capture + plain-urllib
    verification — NO anti-bot on this endpoint):
      POST body {positionName:"", pageNum:N, pageSize:30,
                 recruitType:"social", workplaces:["840"]}
      → {success:true, data:{total, totalPage, list:[...]}}
    Rows carry the COMPLETE posting (duty + qualification IN the list
    rows — one-call board, zero detail fetches), exact ISO publishTime,
    workplace names as a comma string ('美国，新加坡，上海市，北京市').

    Field map (live-pinned 2026-09-26, 20 US rows of the 863-position
    social board):
      reqId            positionId (int → str; RAW — S14 join-safety)
      title            positionName
      url              job.xiaohongshu.com/social/position/{id}
      locationsText    workplace (the site's own display string)
      postedOn         publishTime (exact ISO date; never censored)
      timeType         "" — the API carries no employment type (honest
                       blank; config sets time_type "")
      departments      directionName / subDirectionName / jobType
      description      duty + qualification (from the list row itself)
    country: server-side filter — country_client=False; all rows are
    US-workplace rows by construction.
    """

    KIND = "xiaohongshu"
    _API = ("https://job.xiaohongshu.com/websiterecruit/position/"
            "pageQueryPosition")
    _HDRS = {"Content-Type": "application/json",
             "Accept": "application/json",
             "Referer": "https://job.xiaohongshu.com/social/position",
             "Origin": "https://job.xiaohongshu.com"}
    _US_WORKPLACE = "840"     # 美国 — live-observed filter value
    _PAGE = 30
    _MAX_PAGES = 60           # circuit breaker

    def __init__(self, org: str, cfg: Config):
        self.org = org  # unused (custom: grammar)
        self.cfg = cfg
        self.company = "Xiaohongshu"

    def _fetch_pages(self, workplaces: list[str]) -> list[dict]:
        """Full filtered board (all pages), cached per process."""
        key = f"custom:xiaohongshu:{'+'.join(workplaces) or 'all'}"
        now = time.monotonic()
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
        posts: list[dict] = []
        page_num = 1
        total: Optional[int] = None
        while True:
            body = {"positionName": "", "pageNum": page_num,
                    "pageSize": self._PAGE, "recruitType": "social",
                    "workplaces": workplaces}
            d = _post_json_urllib(self._API, body, self._HDRS)
            if d.get("success") is not True:
                raise RuntimeError(
                    f"{self._API}: success={d.get('success')} "
                    f"(errorMsg={str(d.get('errorMsg'))[:120]}) — refusing "
                    f"to serve an error body as a board")
            data = d.get("data") or {}
            page = data.get("list") or []
            if not isinstance(page, list):
                raise RuntimeError(f"{self._API}: list not a list")
            if total is None:
                total = data.get("total")
                if not isinstance(total, int):
                    raise RuntimeError(
                        f"{self._API}: count missing (total="
                        f"{total!r}) — refusing ambiguous board state")
            if not page and page_num == 1:
                if total == 0:
                    break      # genuine empty (server-filtered 0)
                raise RuntimeError(
                    f"{self._API}: empty page 1 with total={total} — "
                    f"shape anomaly, nothing cached")
            posts.extend(page)
            if len(posts) >= total or len(page) < self._PAGE:
                break
            page_num += 1
            if page_num > self._MAX_PAGES:
                raise RuntimeError(f"{self._API}: pagination runaway")
        if len({str(p.get("positionId")) for p in posts}) != len(posts) \
                or len(posts) != total:
            raise RuntimeError(
                f"{self._API}: collected {len(posts)} rows but "
                f"total={total} — pagination drift, refusing short board")
        _CACHE[key] = (time.monotonic(), posts)
        return posts

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        # SERVER-side US filter (the tripcom class): workplaces=["840"].
        # A non-US country param has no taxonomy code — refuse loudly
        # (never serve US rows under a foreign filter).
        workplaces = []
        if country:
            if not workday.country_str_matches("United States", country):
                raise RuntimeError(
                    "xiaohongshu: only the United States workplace filter "
                    "is mapped (taxonomy code 840); refusing to guess a "
                    f"code for {country!r}")
            workplaces = [self._US_WORKPLACE]
        # round-2 F1 (SEV-2): the API carries NO employment-type field
        # — a time filter is unanswerable, and silently dropping every
        # row with complete=True is the mass-false-gone trap the
        # board_dump --time-type 'Full time' DEFAULT would spring.
        # Refuse loudly BEFORE the fetch (round-3 nit: no wasted call).
        if time_type:
            raise RuntimeError(
                "xiaohongshu: the API serves no employment type — a "
                f"time_type filter ({time_type!r}) is unanswerable; use "
                "--time-type '' (the s17 dump-chain driver does)")
        jobs = self._fetch_pages(workplaces)
        rows: dict[str, dict] = {}
        dupes = 0
        for job in jobs:
            rid = str(job.get("positionId") or "")
            if not rid or rid in rows:
                if rid:
                    dupes += 1
                continue
            label, iso = _posted_label(str(job.get("publishTime") or ""))
            desc = str(job.get("duty") or "")
            qual = str(job.get("qualification") or "")
            rows[rid] = {
                "reqId": rid,
                "title": str(job.get("positionName") or ""),
                "company": self.company,
                "url": (f"https://job.xiaohongshu.com/social"
                        f"/position/{rid}"),
                "externalPath": f"/social/position/{rid}",
                "locationsText": str(job.get("workplace") or ""),
                "postedOn": label,
                "timeType": "",   # no field — honest blank
                "bulletFields": [rid],
                "ats": "xiaohongshu",
                "firstPublishedIso": iso,
                "departments": _dedup_keep_order(
                    [str(job.get("directionName") or ""),
                     str(job.get("subDirectionName") or ""),
                     str(job.get("jobType") or "")]),
                "countries": ["United States"] if country else [],
                "description": (desc + "\n\n" + qual).strip(),
            }
        meta = {
            "complete": True, "total": len(jobs), "pages": 1,
            "country_client": False,   # server-side workplace filter
            "client_filtered": 0,
            "duplicate_codes": dupes,
            "ats": "xiaohongshu",
        }
        print(f"[{progress_label}] xiaohongshu: {len(rows)} rows "
              f"(server-side workplaces={workplaces}, total={len(jobs)})",
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        # round-2 F4: the same refusal as list_board — never stamp US
        # under a foreign filter (defense in depth)
        if country and not workday.country_str_matches(
                "United States", country):
            raise RuntimeError(
                "xiaohongshu: only the United States workplace filter is "
                "mapped; refusing to serve a US payload under "
                f"{country!r}")
        for job in self._fetch_pages([self._US_WORKPLACE]
                                     if country else []):
            if str(job.get("positionId")) != rid:
                continue
            label, iso = _posted_label(str(job.get("publishTime") or ""))
            desc = str(job.get("duty") or "")
            qual = str(job.get("qualification") or "")
            workplace = str(job.get("workplace") or "")
            return {
                "jobPostingInfo": {
                    "title": str(job.get("positionName") or ""),
                    "location": workplace.split("，")[0] if workplace else "",
                    "additionalLocations": workplace.split("，")[1:],
                    "jobDescription": (desc + "\n\n" + qual).strip(),
                    "timeType": "",
                    "startDate": iso or "",
                    "externalUrl": (f"https://job.xiaohongshu.com"
                                    f"/social/position/{rid}"),
                    "jobReqId": rid,
                    "postedOn": label,
                    "country": {"descriptor": "United States"}
                    if country else None,
                },
                "hiringOrganization": {"name": self.company},
                "similarJobs": [],
                "firstPublishedIso": iso,
            }
        return None


# ── paylocity (recruiting.paylocity.com public boards) ────────────────────

class PaylocityAdapter:
    """ats:paylocity:{CompanyId} — the public board hosted at
    recruiting.paylocity.com/recruiting/jobs/All/{CompanyId}.

    S18 discovery (United Imaging North America, live-pinned
    2026-09-25, 41 jobs): the whole board is SERVER-RENDERED into the
    HTML as a single JS literal:

        window.pageData = {"ModuleTitle": ..., "ModuleId": ...,
                           "Jobs": [{"JobId": 4463447,
                                     "JobTitle": "...",
                                     "LocationName": "West Coast region",
                                     "ShouldDisplayLocation": true,
                                     "PublishedDate": "2026-09-22T15:54:27-05:00",
                                     "Description": "<110-char teaser>",
                                     "IsRemote": true,
                                     "IndeedRemoteType": "2",
                                     "HiringDepartment": null,
                                     "JobLocation": {"Country": "USA", ...}}],
                           "Departments": [...], "Locations": [...]}

    Field map (the workday dialect):
      reqId            JobId
      url              https://recruiting.paylocity.com/Recruiting/Jobs/Details/{JobId}
      locationsText    LocationName (fallback JobLocation.Name)
      country          JobLocation.Country — STRUCTURED, authoritative
                       ('USA' → 'United States'); the board is
                       entity-scoped (the US subsidiary's own module),
                       so every row carries the entity's country
      timeType         NONE — paylocity list rows serve no employment
                       type → a time_type filter is REFUSED (the XHS
                       class: refuse the unanswerable, never drop-all)
      postedOn         label from PublishedDate (EXACT, ISO with TZ)
      remoteType       IsRemote/IndeedRemoteType

    The list row's Description is a 110-char TEASER — full JD (+
    Requirements) is per-id detail HTML:
    /Recruiting/Jobs/Details/{JobId} with <div class="job-listing-
    header">Description</div>/<div>... and a Requirements sibling.

    One-call complete board: the page's "N of N Job Opportunities"
    count is computed client-side FROM this array (React + Immutable
    filter over the embedded list) — no pagination endpoint observed.
    B1 guards: pageData unparseable / Jobs not a list = refuse; a
    missing Jobs key on a page that says 'Job Opportunities' = shape
    anomaly, refuse (never an empty board).
    """

    KIND = "paylocity"
    _BASE = "https://recruiting.paylocity.com"
    _TT = {}  # no employment-type field — honest blanks

    def __init__(self, org: str, cfg: Config):
        # org = the CompanyId GUID (display-name suffix optional in URL)
        self.org = org
        self.cfg = cfg

    # -- board fetch ------------------------------------------------------

    def _board_html(self) -> str:
        key = f"ats:paylocity:{self.org}:html"
        now = time.monotonic()
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
        url = f"{self._BASE}/recruiting/jobs/All/{self.org}"
        html = fetch_text(url, cfg=self.cfg)
        if not html or "window.pageData" not in html:
            raise RuntimeError(
                f"{url}: no window.pageData — not a paylocity jobs board "
                f"(company id wrong or board dead); refusing")
        _CACHE[key] = (time.monotonic(), html)
        return html

    @staticmethod
    def _extract_page_data(html: str) -> dict:
        """window.pageData = {…}; → dict. Brace-matched, JSON-first,
        tolerant fallback for single-quoted / Python-bool literals."""
        marker = "window.pageData = "
        i = html.find(marker)
        if i < 0:
            raise RuntimeError("pageData marker missing")
        seg = html[i + len(marker):]
        depth = 0
        end = -1
        in_str = False
        esc = False
        quote = ""
        for idx, ch in enumerate(seg):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == quote:
                    in_str = False
                continue
            if ch in "\"'":
                in_str = True
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = idx + 1
                    break
        if end < 0:
            raise RuntimeError("pageData braces unmatched")
        raw = seg[:end]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            fixed = (raw.replace("'", '"').replace(": True", ": true")
                        .replace(": False", ": false")
                        .replace(": None", ": null"))
            return json.loads(fixed)

    def _jobs(self) -> list[dict]:
        html = self._board_html()
        d = self._extract_page_data(html)
        jobs = d.get("Jobs")
        if not isinstance(jobs, list):
            if "Job Opportunities" in html:
                raise RuntimeError(
                    "pageData.Jobs missing/not-a-list on a live board page "
                    "— shape anomaly, refusing (never an empty board)")
            jobs = []          # board page without a jobs region: empty
        self._module_title = str(d.get("ModuleTitle") or self.org)
        return jobs

    # -- classification ---------------------------------------------------

    def _country_of(self, job: dict) -> str:
        jl = job.get("JobLocation") or {}
        if isinstance(jl, str):       # pragma: no cover — defensive
            jl = {}
        c = str(jl.get("Country") or "").strip()
        if c.upper() in ("USA", "US", "UNITED STATES"):
            return "United States"
        return c

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        if time_type:
            raise RuntimeError(
                "paylocity: the board serves no employment type — a "
                f"time_type filter ({time_type!r}) is unanswerable; use "
                "--time-type '' (the s18 dump-chain driver does)")
        jobs = self._jobs()
        rows: dict[str, dict] = {}
        dropped_country = 0
        for job in jobs:
            rid = str(job.get("JobId") or "")
            if not rid or rid in rows:
                continue
            c = self._country_of(job)
            if country and not workday.country_str_matches(c, country):
                dropped_country += 1
                continue
            label, iso = _posted_label(str(job.get("PublishedDate") or ""))
            loc = str(job.get("LocationName") or "").strip()
            if not loc:
                jl = job.get("JobLocation") or {}
                if isinstance(jl, dict):
                    loc = str(jl.get("Name") or "").strip()
            remote = bool(job.get("IsRemote"))
            rows[rid] = {
                "reqId": rid,
                "title": str(job.get("JobTitle") or ""),
                "company": self._module_title,
                "url": f"{self._BASE}/Recruiting/Jobs/Details/{rid}",
                "externalPath": f"/Recruiting/Jobs/Details/{rid}",
                "locationsText": loc,
                "postedOn": label,
                "timeType": "",   # no field — honest blank
                "bulletFields": [rid],
                "ats": "paylocity",
                "firstPublishedIso": iso,
                "departments": _dedup_keep_order(
                    [str(job.get("HiringDepartment") or "")]),
                "countries": [c] if c else [],
                "remoteType": "Remote" if remote else "",
                "description": str(job.get("Description") or "").strip(),
            }
        meta = {
            "complete": True, "total": len(jobs), "pages": 1,
            # country classification happens IN list_board from the
            # STRUCTURED JobLocation.Country (ashby-class: the listing
            # IS the {country} population — no detail-based
            # countryfilter phase needed; board_dump's country_client
            # flag means 'unfiltered global board', which this is NOT)
            "country_client": False,
            "client_filtered": dropped_country,
            "ats": "paylocity",
        }
        print(f"[{progress_label}] paylocity:{self.org}: {len(rows)} rows"
              + (f" ({dropped_country} non-{country} dropped client-side)"
                 if country else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    # -- detail -----------------------------------------------------------

    @staticmethod
    def _section_html(html: str, header: str) -> str:
        """Inner HTML of the <div> following the
        <div class="job-listing-header">{header}</div> marker."""
        m = re.search(
            r'job-listing-header">\s*' + re.escape(header)
            + r'\s*</div>\s*<div([^>]*)>(.*?)</div>',
            html, re.S)
        if not m:
            return ""
        return m.group(2).strip()

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        if time_type:
            raise RuntimeError(
                "paylocity: no employment type on the board — a "
                f"time_type filter ({time_type!r}) is unanswerable")
        rid = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        job = None
        for j in self._jobs():
            if str(j.get("JobId")) == rid:
                job = j
                break
        if job is None:
            return None
        url = f"{self._BASE}/Recruiting/Jobs/Details/{rid}"
        html = fetch_text(url, cfg=self.cfg)
        desc_html = self._section_html(html, "Description")
        req_html = self._section_html(html, "Requirements")
        c = self._country_of(job)
        label, iso = _posted_label(str(job.get("PublishedDate") or ""))
        title = str(job.get("JobTitle") or "")
        tmatch = re.search(
            r'job-preview-title[^>]*>\s*<span>(.*?)</span>', html, re.S)
        if tmatch:
            title = unescape(tmatch.group(1)).strip() or title
        loc_line = ""
        lmatch = re.search(
            r'preview-location">\s*(.*?)</div>', html, re.S)
        if lmatch:
            loc_line = unescape(
                re.sub(r"<[^>]+>", " ", lmatch.group(1)))
            loc_line = re.sub(r"\s+", " ", loc_line).strip(" •")
        return {
            "jobPostingInfo": {
                "title": title,
                "location": loc_line or (job.get("LocationName") or ""),
                "additionalLocations": [],
                "jobDescription": (desc_html + "\n\n" + req_html).strip(),
                "timeType": "",
                "startDate": iso or "",
                "externalUrl": url,
                "jobReqId": rid,
                "postedOn": label,
                "country": ({"descriptor": c} if c and country else None),
            },
            "hiringOrganization": {"name": self._module_title},
            "similarJobs": [],
            "firstPublishedIso": iso,
        }


_ADAPTERS = {"greenhouse": GreenhouseAdapter, "ashby": AshbyAdapter,
             "lever": LeverAdapter, "workable": WorkableAdapter,
             "feishuhire": FeishuHireAdapter,
             "bytedance": ByteDanceAdapter, "alibaba": AlibabaAdapter,
             "tripcom": TripComAdapter, "xiaohongshu": XiaohongshuAdapter,
             "paylocity": PaylocityAdapter}
