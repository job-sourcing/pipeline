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
from .base import USER_AGENT, fetch_json, fetch_text

_SPEC_RE = re.compile(
    r"^ats:(greenhouse|ashby|lever|workable|feishuhire|paylocity|adp|jazzhr|"
    r"teamtailor|radancy|rippling|breezy|jobvite|oraclehcm|bamboohr|j2w|"
    r"ultipro|talentadore|workstream|ttiproxy|sanity|wpjobboard|wuxibio|"
    r"antintl):([A-Za-z0-9_.\-|/]+)$")
# S14 registry-driven custom grammar: 'custom:{kind}' where kind ∈ _ADAPTERS
# (own-platform boards — the platform IS the company; adding one = a class
# + a registry entry, no grammar edit).
_CUSTOM_RE = re.compile(r"^custom:([a-z0-9_]+)$")

# per-process one-fetch cache: {spec: (fetched_at, jobs_payload)}
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 60.0
_TEXT_CACHE: dict[str, tuple[float, str]] = {}     # S21: html/portal pages
_TEXT_CACHE_TTL = 900.0


def _fetch_text_cached(url: str, spec: str, cfg: "Config") -> str:
    """One text fetch per process per spec (15-min TTL) — the S21
    multi-page adapters (radancy 26-page walk, jazzhr board html) must
    not re-walk their list on every detail call."""
    now = time.monotonic()
    hit = _TEXT_CACHE.get(url)
    if hit and now - hit[0] < _TEXT_CACHE_TTL:
        return hit[1]
    text = fetch_text(url, cfg=cfg)
    _TEXT_CACHE[url] = (time.monotonic(), text)
    return text


def _fetch_text_resilient(url: str, spec: str, cfg: "Config") -> str:
    """_fetch_text_cached + curl_cffi chrome131 fallback (S23).

    The static-page adapter class (greenland/autelenergy/aden…) hits
    TLS-fingerprint WAFs: requests→403 (adengroup.com) or persistent
    Shopify 429 (autelenergy.us) while an impersonated chrome131
    client serves the full page from the SAME egress IP. The fallback
    result enters the shared per-process cache so the details phase
    (a fresh subprocess) re-walks identically. curl_cffi is optional
    (fresh runners) — without it the ORIGINAL error propagates and the
    chain's existing failure contract reports it."""
    try:
        return _fetch_text_cached(url, spec, cfg)
    except Exception as exc:
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            raise exc from None
        try:
            r = cffi_requests.get(
                url, impersonate="chrome131",
                timeout=cfg.http_timeout_s,
                headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            text = r.text
        except Exception:
            raise exc from None       # original error = the contract
        _TEXT_CACHE[url] = (time.monotonic(), text)
        return text


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
        f"greenhouse|ashby|lever|workable|feishuhire|paylocity|adp|"
        f"breezy|jobvite|oraclehcm|bamboohr|j2w|ultipro|talentadore|"
        f"workstream|ttiproxy|sanity|wpjobboard|wuxibio|antintl|"
        f"greenland|jereh|autelenergy|aden|mandarinoriental|wuxiapptec|"
        f"blacksesame|ecovacsus|accutar|hitgen|insilico|orbbec|"
        f"visionnav|verisilicon|uniview, "
        f"or 'custom:kind' with kind in "
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
                    # S20 (genscript "Remote in Europe" lesson): a 2-letter
                    # state code must match CASE-SENSITIVELY — the lowercase
                    # English words 'in' (Indiana) / 'or' (Oregon) /
                    # 'me' (Maine) are not state tokens. Full state names
                    # ('california') stay case-insensitive.
                    t = tok.strip(",()")
                    if len(t) > 2:
                        if t.lower() in workday._US_STATE_TOKENS:
                            return True
                    elif t.isupper() and t.lower() in \
                            workday._US_STATE_TOKENS:
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
    "Langfang": "China", "Yulin": "China",  # S20 poizon
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
    # S23 (orionstar): "Washington D.C." is its own token, not "Washington"
    "Washington D.C.": "United States", "Washington DC": "United States",
    "Washington, D.C.": "United States",
    "San Mateo": "United States", "Menlo Park": "United States",
    "Redwood City": "United States", "Fremont": "United States",
    "Milpitas": "United States", "Santa Monica": "United States",
    "El Segundo": "United States", "Pasadena": "United States",
    "Waltham": "United States", "Burlington": "United States",
    "Arlington": "United States", "Reston": "United States",
    "Tysons": "United States", "Pittsburgh": "United States",
    "Ann Arbor": "United States", "Princeton": "United States",
    "Jersey City": "United States", "San Ramon": "United States",
    # S20 (poizon portal): US satellites beyond the pre-seed
    "Brooklyn": "United States", "Essex County": "United States",
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
                      headers: Optional[dict] = None,
                      attempts: int = 2, backoff_s: float = 2.0) -> dict:
    """Plain-urllib JSON POST (no impersonation, no session) — for
    endpoints that serve plain requests and REJECT chrome-impersonation
    (the alibaba-cloud inversion class; also xiaohongshu, live-verified
    2026-09-26). Raises on non-JSON bodies (structural guard — the
    tripcom XML-fallback trap).

    S18 (watch run #59): one transport retry with backoff — the GHA
    runner's transient Errno-101 (Network is unreachable) failed a
    whole xiaohongshu watch leg (and, by the loud-failure contract,
    the whole run). A single-blip retry is the feishuhire-transport
    precedent; a NON-JSON 200 body is still refused immediately (the
    structural guard is not retried — it is a verdict, not a blip)."""
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
         "Content-Type": "application/json",
         "Accept": "application/json"}
    if headers:
        h.update(headers)
    last_exc: Exception | None = None
    for attempt in range(max(1, attempts)):
        if attempt:
            time.sleep(backoff_s)
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            # HTTP verdicts are server answers, not blips — surface now
            raise
        except Exception as e:            # URLError/timeout/conn reset
            last_exc = e
            continue
        if not raw.lstrip().startswith(("{", "[")):
            raise RuntimeError(f"{url}: non-JSON response (first 60: "
                               f"{raw[:60]!r})")
        return json.loads(raw)
    raise last_exc


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
        # S18 review P3 4: the Jobs key is the BOARD CONTRACT — absent
        # means the payload shape changed (template rename, new page
        # type). Refuse UNCONDITIONALLY: a key-less page trusted as
        # empty would cache a complete=True 0-row board (mass-gone in
        # the next diff — the B1 contract's worst case).
        if "Jobs" not in d:
            raise RuntimeError(
                "pageData.Jobs key MISSING on the board page — payload "
                "shape change (template rename / page type), refusing "
                "(never an empty board)")
        jobs = d.get("Jobs")
        if not isinstance(jobs, list):
            raise RuntimeError(
                "pageData.Jobs is not a list — shape anomaly, refusing")
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
        <div class="job-listing-header">{header}</div> marker.
        S18 review SEV-3 3: DIV-DEPTH BALANCED — the paylocity section
        body may nest <div>s (a JD with a nested layout div would be
        silently truncated by a naive non-greedy (.*?)</div>); walk to
        the closing tag at depth 0."""
        m = re.search(
            r'job-listing-header">\s*' + re.escape(header)
            + r'\s*</div>\s*<div([^>]*)>',
            html, re.S)
        if not m:
            return ""
        body_start = m.end()
        depth = 1
        i = body_start
        token = re.compile(r'<(/?)div\b[^>]*>', re.I)
        for t in token.finditer(html, body_start):
            depth += -1 if t.group(1) else 1
            if depth == 0:
                return html[body_start:t.start()].strip()
        # no balanced close found — fall back to the next </div>
        # (better a truncated body than an unbounded one)
        nxt = html.find("</div>", body_start)
        return html[body_start:nxt].strip() if nxt >= 0 else ""

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

def _plain_loc_in_country(loc: str, country: Optional[str]) -> bool:
    """A bare location string ('Auburn Hills, MI' / 'Ramos Arizpe,
    Coahuila de Zaragoza, Mexico') → in-country? Rungs mirror the S15
    greenhouse ladder minus the office channel (JazzHR serves no
    offices): (1) an explicit country phrase in any comma-segment,
    (2) the US state-token fallback — CASE-SENSITIVE 2-letter codes
    (the S20 'Remote in Europe'/'in'=Indiana lesson, pinned)."""
    if not country:
        return True
    if not isinstance(loc, str) or not loc.strip():
        return False
    segs = [s.strip() for s in loc.split(",") if s.strip()]
    for seg in segs:
        if workday.country_str_matches(seg, country):
            return True
    if (country or "").strip().lower() in workday._US_COUNTRY_NAMES:
        for seg in segs:
            for tok in seg.split():
                t = tok.strip(",()")
                if len(t) > 2:
                    if t.lower() in workday._US_STATE_TOKENS:
                        return True
                elif t.isupper() and t.lower() in \
                        workday._US_STATE_TOKENS:
                    return True
    return False


# ── adp workforcenow (mascsr public career-center REST) ────────────────────

class ADPWorkforceNowAdapter:
    """ats:adp:{cid} — the PUBLIC REST surface behind every
    workforcenow.adp.com/mascsr recruitment.html board.

    S21 discovery (Fuyao Glass America, live-pinned 2026-09-27, 180
    openings). The SPA (mdfLoader → recruitment.{hash}.js) resolves its
    API through a RAAS url-map: public access point
    /mascsr/default/careercenter/public + /events/staffing + the
    mapped path — so the list endpoint is

      GET .../events/staffing/v1/job-requisitions
              ?cid={cid}&lang=en_US&locale=en_US
              &$skip={s}&$top=20&userQuery=

    and the detail is the same path + /{itemID}. VERIFIED minimal: the
    cid ALONE authenticates (ccId/client/timeStamp all optional); $top
    is server-capped at 20. PAGINATION QUIRK (pinned live): page 0
    returns 19 rows while meta.startSequence advances 20-per-page —
    the walk MUST advance skip = startSequence + len(rows), and one
    itemID DUPLICATES across the page boundary (dedup, flagged).

    Row map (workday dialect):
      reqId            itemID
      title            requisitionTitle
      timeType         workLevelCode.shortName — STRUCTURED
                       ('Full Time'→Full time, 'Part Time'→Part time,
                       'Intern'→Internship; None → honest blank)
      postedOn         _posted_label(postDate) — ISO w/ TZ, exact
      locationsText    requisitionLocations[].nameCode.shortName
                       ('Moraine, OH, US' — city, state, ISO country)
      countries        trailing ISO segment ('US'→United States)
      url              getShareUrl shape (pinned from the bundle):
                       .../recruitment.html?cid=..&ccId=19000101_000001
                       &jobId={itemID}&lang=en_US

    Detail: requisitionDescription = the FULL HTML job description;
    B1 guards (not JSON / missing jobRequisitions / missing meta /
    > 40 pages) refuse — never an empty board.
    """

    KIND = "adp"
    _API = ("https://workforcenow.adp.com/mascsr/default/"
            "careercenter/public/events/staffing/v1/job-requisitions")
    _BOARD = ("https://workforcenow.adp.com/mascsr/default/mdf/"
              "recruitment/recruitment.html")
    _TT = {"full time": "Full time", "full-time": "Full time",
           "part time": "Part time", "part-time": "Part time",
           "intern": "Internship", "internship": "Internship",
           "temporary": "Temporary", "contract": "Contract"}
    _COUNTRY = {"US": "United States", "MX": "Mexico", "CN": "China",
                "CA": "Canada", "GB": "United Kingdom", "DE": "Germany",
                "JP": "Japan", "SG": "Singapore", "IN": "India"}

    def __init__(self, org: str, cfg: Config):
        # org = the board's cid GUID (the ?cid= query param)
        self.org = org
        self.cfg = cfg

    # -- list ------------------------------------------------------------

    def _pages(self):
        """Generate every list page. Advance rule is the pinned quirk:
        skip = startSequence + len(rows)."""
        seen: set[str] = set()
        dup = 0
        skip = 0
        pages = 0
        total = None
        while pages < 40:
            url = (f"{self._API}?cid={self.org}&lang=en_US"
                   f"&locale=en_US&$skip={skip}&$top=20&userQuery=")
            d = fetch_json(url, cfg=self.cfg)
            if not isinstance(d, dict) or "jobRequisitions" not in d \
                    or "meta" not in d:
                raise RuntimeError(
                    f"adp:{self.org}: unexpected list payload "
                    f"(no jobRequisitions/meta) — board dead or shape "
                    f"changed; refusing")
            m = d["meta"]
            total = int(m.get("totalNumber") or 0)
            rows = d["jobRequisitions"] or []
            if not rows:
                break
            for r in rows:
                rid = str(r.get("itemID") or "")
                if not rid:
                    continue
                if rid in seen:
                    dup += 1        # the pinned page-boundary duplicate
                    continue
                seen.add(rid)
                yield r
            pages += 1
            seq = int(m.get("startSequence") or skip)
            skip = seq + len(rows)
            if total and skip >= total:
                break
        self._dup_count = dup
        self._total = total

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped_country = dropped_tt = 0
        for job in self._pages():
            rid = str(job.get("itemID") or "")
            tt = self._time_type(job)
            if time_type and tt and tt.lower() != time_type.lower():
                dropped_tt += 1
                continue
            c = self._country_of(job)
            if country and not workday.country_str_matches(c, country):
                dropped_country += 1
                continue
            locs = [str((L.get("nameCode") or {}).get("shortName") or
                        "").strip()
                    for L in job.get("requisitionLocations") or []]
            locs = [x for x in locs if x]
            label, iso = _posted_label(str(job.get("postDate") or ""))
            rows[rid] = {
                "reqId": rid,
                "title": str(job.get("requisitionTitle") or ""),
                "company": "",   # config company is the authority
                "url": (f"{self._BOARD}?cid={self.org}"
                        f"&ccId=19000101_000001&jobId={rid}&lang=en_US"),
                "externalPath": f"/{rid}",
                "locationsText": "; ".join(locs),
                "postedOn": label,
                "timeType": tt,
                "bulletFields": [rid, str(job.get("clientRequisitionID")
                                          or "")],
                "ats": "adp",
                "firstPublishedIso": iso,
                "departments": [],
                "countries": [c] if c else [],
                "remoteType": "",
            }
        meta = {
            "complete": True,
            "total": self._total if hasattr(self, "_total") else len(rows),
            "pages": getattr(self, "_pages_walked", 0) or None,
            "country_client": False,
            "client_filtered": dropped_country + dropped_tt,
            "ats": "adp",
            "duplicate_itemids": getattr(self, "_dup_count", 0),
        }
        print(f"[{progress_label}] adp:{self.org}: {len(rows)} rows"
              + (f" of {meta['total']} listed"
                 + (f" (+{meta['duplicate_itemids']} page-boundary dup)"
                    if meta["duplicate_itemids"] else ""))
              + (f" ({dropped_country} non-{country} + {dropped_tt} "
                 f"non-{time_type} dropped client-side)"
                 if country or time_type else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    # -- helpers ----------------------------------------------------------

    def _time_type(self, job: dict) -> str:
        raw = str(((job.get("workLevelCode") or {}).get("shortName"))
                  or "").strip()
        return self._TT.get(raw.lower(), raw) if raw else ""

    def _country_of(self, job: dict) -> str:
        locs = job.get("requisitionLocations") or []
        for L in locs:
            name = str((L.get("nameCode") or {}).get("shortName")
                       or "").strip()
            if not name:
                continue
            seg = [x.strip() for x in name.split(",")]
            code = seg[-1] if seg else ""
            if len(code) == 2 and code.isalpha():
                return self._COUNTRY.get(code.upper(), code)
        return ""

    # -- detail -----------------------------------------------------------

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        url = (f"{self._API}/{rid}?cid={self.org}&lang=en_US"
               f"&locale=en_US")
        d = fetch_json(url, cfg=self.cfg)
        if not isinstance(d, dict) or not d.get("itemID"):
            return None
        job = d
        tt = self._time_type(job)
        c = self._country_of(job)
        label, iso = _posted_label(str(job.get("postDate") or ""))
        locs = [str((L.get("nameCode") or {}).get("shortName") or
                    "").strip()
                for L in job.get("requisitionLocations") or []]
        locs = [x for x in locs if x]
        return {
            "jobPostingInfo": {
                "title": str(job.get("requisitionTitle") or ""),
                "location": locs[0] if locs else "",
                "additionalLocations": locs[1:],
                "jobDescription": str(job.get("requisitionDescription")
                                      or ""),
                "timeType": tt,
                "startDate": iso or "",
                "externalUrl": (f"{self._BOARD}?cid={self.org}"
                                f"&ccId=19000101_000001&jobId={rid}"
                                f"&lang=en_US"),
                "jobReqId": rid,
                "postedOn": label,
                "country": ({"descriptor": c} if c else None),
            },
            "hiringOrganization": {"name": ""},
            "similarJobs": [],
            "firstPublishedIso": iso,
        }

# ── jazzhr (applytojob.com public boards) ─────────────────────────────────

class JazzHRAdapter:
    """ats:jazzhr:{slug} — the server-rendered public board at
    {slug}.applytojob.com (one-call complete — all jobs on the first
    page; no pagination endpoint observed on 172-job foxconnassemblyllc).

    S21 discovery (Sanhua International 34 jobs + Foxconn Industrial
    Internet / FII 172 jobs, live-pinned 2026-09-27). List rows:

        <li class="list-group-item">
          <h3 class='list-group-item-heading'>
            <a href="https://{slug}.applytojob.com/apply/{jobId}/{slug}">
              {title}</a></h3>
          <ul class='list-inline list-group-item-text'>
            <li><i class='fa fa-map-marker'></i>{location}</li>
            <li><i class='fa fa-sitemap'></i>{department}</li>

    Field map (workday dialect):
      reqId            {jobId} from the apply-URL path (stable)
      title            the anchor text
      locationsText    fa-map-marker li
      departments      fa-sitemap li
      timeType         NONE in the list — honest blank; the DETAIL's
                       JSON-LD employmentType fills it at detail time
      postedOn         detail JSON-LD datePosted (list serves none)

    Country: the S15 ladder minus the office channel — rung 1 explicit
    country phrase in a comma-segment ('…, Mexico'), rung 3 US
    state-token ('Auburn Hills, MI', bare 'NC' — CASE-sensitive codes).

    Detail: the /apply/{jobId}/ page carries a FULL JSON-LD
    JobPosting (title / datePosted / employmentType / description /
    jobLocation.address[locality|region] / hiringOrganization.name =
    the board's SELF-NAME / validThrough). B1: a 200 page whose HTML
    has neither list-group items nor a 'no jobs' marker = shape
    anomaly → refuse (never a silently-empty complete board).
    """

    KIND = "jazzhr"
    _TT_JSONLD = {"full_time": "Full time", "full-time": "Full time",
                  "part_time": "Part time", "part-time": "Part time",
                  "contract": "Contract", "internship": "Internship",
                  "temporary": "Temporary", "contractor": "Contract"}

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    # -- board fetch ------------------------------------------------------

    def _board_html(self) -> str:
        url = f"https://{self.org}.applytojob.com"
        html = _fetch_text_cached(url, f"ats:jazzhr:{self.org}", self.cfg)
        if not html or len(html) < 500:
            raise RuntimeError(
                f"{url}: empty/short body — board dead; refusing")
        return html

    @staticmethod
    def _parse_jobs(html: str) -> list[dict]:
        """One job per <li class="list-group-item"> — but the job li
        NESTS attr <li>s (map-marker/sitemap), so a non-greedy …</li>
        block ends at the FIRST nested close and silently loses the
        department. Split on the OPENERS instead: block = opener →
        next opener (or </ul>)."""
        jobs = []
        seen = set()
        opener = re.compile(
            r"""<li\s+class=['"]list-group-item['"]>""")
        hits = list(opener.finditer(html))
        for i, m in enumerate(hits):
            end = hits[i + 1].start() if i + 1 < len(hits) else \
                html.find("</ul>", m.end())
            if end < 0:
                end = len(html)
            block = html[m.end():end]
            a = re.search(
                r"""href=['"](https?://[^'"]+/apply/([A-Za-z0-9]+)/[^'"]*)['"][^>]*>\s*(.*?)\s*</a>""",
                block, re.S)
            if not a:
                continue
            url, jid, title = a.group(1), a.group(2), \
                re.sub(r"<[^>]+>", "", a.group(3)).strip()
            if jid in seen or not title:
                continue
            seen.add(jid)
            loc = ""
            dept = ""
            lm = re.search(
                r"""fa-map-marker['"]?\s*></i>([^<]+)""", block)
            if lm:
                loc = lm.group(1).strip()
            dm = re.search(r"""fa-sitemap['"]?\s*></i>([^<]+)""", block)
            if dm:
                dept = dm.group(1).strip()
            jobs.append({"reqId": jid, "title": title, "url": url,
                         "location": loc, "department": dept})
        return jobs

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        if time_type:
            raise RuntimeError(
                "jazzhr: the board LIST serves no employment type (it "
                "lives in the detail JSON-LD) — a time_type filter "
                f"({time_type!r}) is unanswerable at list time; use "
                "--time-type '' (the s19 driver does; the detail phase "
                "fills timeType)")
        html = self._board_html()
        jobs = self._parse_jobs(html)
        has_openings_heading = "Current Openings" in html
        if not jobs and not has_openings_heading:
            raise RuntimeError(
                f"jazzhr:{self.org}: 200 but no list-group items and no "
                f"openings heading — shape anomaly; refusing")
        rows: dict[str, dict] = {}
        dropped_country = 0
        for job in jobs:
            rid = job["reqId"]
            loc = job["location"]
            if country and not _plain_loc_in_country(loc, country):
                dropped_country += 1
                continue
            rows[rid] = {
                "reqId": rid,
                "title": job["title"],
                "company": "",
                "url": job["url"],
                "externalPath": f"/{rid}",
                "locationsText": loc,
                "postedOn": "",       # list serves none — detail fills
                "timeType": "",       # honest blank (detail JSON-LD)
                "bulletFields": [rid],
                "ats": "jazzhr",
                "firstPublishedIso": "",
                "departments": [job["department"]] if job["department"] else [],
                "countries": [],
                "remoteType": "",
            }
        meta = {
            "complete": True, "total": len(jobs), "pages": 1,
            "country_client": False,
            "client_filtered": dropped_country,
            "ats": "jazzhr",
        }
        print(f"[{progress_label}] jazzhr:{self.org}: {len(rows)} rows"
              + (f" of {len(jobs)} listed"
                 if len(rows) != len(jobs) else "")
              + (f" ({dropped_country} non-{country} dropped "
                 f"client-side)" if dropped_country else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    # -- detail -----------------------------------------------------------

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        # resolve URL from the cached list (the apply-URL's title slug
        # is not derivable from the id alone)
        html = self._board_html()
        url = None
        for job in self._parse_jobs(html):
            if job["reqId"] == rid:
                url = job["url"]
                break
        if not url:
            return None
        page = _fetch_text_cached(url, f"ats:jazzhr:{self.org}", self.cfg)
        ld = None
        for m in re.finditer(
                r'<script type="application/ld\+json">\s*(.*?)'
                r"\s*</script>", page, re.S):
            try:
                d = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                ld = d
                break
        if ld is None:
            # FALLBACK (pinned live: 4/9 sanhua rows — older postings
            # serve only the Organization ld+json): the page title is
            # '{job} - {org} - Career Page' and the FULL JD sits in
            # the #job-description div (balanced-depth walk — the body
            # nests <div>s; a naive first-</div> match truncates).
            return self._detail_from_html(page, url, rid)
        tt_raw = str(ld.get("employmentType") or "").strip()
        tt = self._TT_JSONLD.get(tt_raw.lower(), tt_raw) if tt_raw else ""
        loc = ld.get("jobLocation") or {}
        addr = (loc or {}).get("address") or {}
        loc_text = ", ".join(
            x for x in [str(addr.get("addressLocality") or ""),
                        str(addr.get("addressRegion") or "")] if x)
        org = (ld.get("hiringOrganization") or {}).get("name") or ""
        posted_iso = str(ld.get("datePosted") or "")
        label, iso = _posted_label(posted_iso)
        return {
            "jobPostingInfo": {
                "title": str(ld.get("title") or ""),
                "location": loc_text,
                "additionalLocations": [],
                "jobDescription": str(ld.get("description") or ""),
                "timeType": tt,
                "startDate": iso or "",
                "externalUrl": str(ld.get("url") or url),
                "jobReqId": str(ld.get("uniqueJobCode") or rid),
                "postedOn": label,
                "country": ({"descriptor": "United States"}
                            if _plain_loc_in_country(loc_text,
                                                     "United States")
                            else None),
                "validThrough": str(ld.get("validThrough") or ""),
            },
            "hiringOrganization": {"name": org},
            "similarJobs": [],
            "firstPublishedIso": iso,
        }

    @staticmethod
    def _div_inner(html: str, marker_re: str) -> str:
        """Inner HTML of the <div> whose opening tag matches marker_re —
        DIV-DEPTH balanced (a nested layout div must not truncate the
        body — the paylocity _section_html lesson)."""
        m = re.search(marker_re, html)
        if not m:
            return ""
        start = m.end()
        depth = 1
        i = start
        token = re.compile(r"<(/?)div\b[^>]*>", re.I)
        for t in token.finditer(html, start):
            depth += -1 if t.group(1) else 1
            if depth == 0:
                return html[start:t.start()].strip()
        nxt = html.find("</div>", start)
        return html[start:nxt].strip() if nxt >= 0 else ""

    def _detail_from_html(self, page: str, url: str,
                          rid: str) -> Optional[dict]:
        """No-JobPosting-JSON-LD fallback: page <title> carries
        '{job} - {org} - Career Page'; the JD = #job-description div."""
        tm = re.search(r"<title>([^<]{0,200})</title>", page, re.I)
        title = org = ""
        if tm:
            parts = [" ".join(p.split()) for p in tm.group(1).split(" - ")]
            parts = [p for p in parts if p]
            if parts:
                title = parts[0]
                org = parts[1] if len(parts) > 2 else ""
        desc = self._div_inner(
            page, r'<div[^>]*id="job-description"[^>]*>')
        if not title and not desc:
            return None
        # location from the job-header attributes (fa-map-marker)
        loc = ""
        hm = re.search(
            r"""fa-map-marker['"]?\s*></i>([^<]+)""", page)
        if hm:
            loc = hm.group(1).strip()
        return {
            "jobPostingInfo": {
                "title": title,
                "location": loc,
                "additionalLocations": [],
                "jobDescription": desc,
                "timeType": "",          # honest — page serves none
                "startDate": "",
                "externalUrl": url,
                "jobReqId": rid,
                "postedOn": "",          # honest — page serves none
                "country": ({"descriptor": "United States"}
                            if _plain_loc_in_country(loc,
                                                     "United States")
                            else None),
                "validThrough": "",
            },
            "hiringOrganization": {"name": org},
            "similarJobs": [],
            "firstPublishedIso": "",
        }


# ── teamtailor ({slug}.teamtailor.com JSON feed) ───────────────────────────

class TeamtailorAdapter:
    """ats:teamtailor:{slug} — the JSON-Feed endpoint every Teamtailor
    careersite serves at {slug}.teamtailor.com/jobs.json.

    S21 discovery (Cainiao, live-pinned 2026-09-27: 48 jobs, 5 US).
    ONE CALL carries the complete board INCLUDING full JDs:

      {version: jsonfeed-1.1, items: [{
         title, url, date_published (ISO w/ TZ),
         content_html (the FULL JD),
         _jobposting: {          # schema.org JobPosting
           title, description, datePosted,
           hiringOrganization: {name: 'Cainiao'},   # the SELF-NAME
           jobLocation: [{address: {addressLocality,
                                    addressRegion,
                                    addressCountry: 'US'}}],
           identifier: {value: 8326489}}}]}

    Field map (workday dialect):
      reqId            identifier.value (fallback: the /jobs/{id}- URL)
      timeType         NONE served — honest blank
      postedOn         date_published (EXACT, ISO with TZ)
      countries        addressCountry ISO codes ('US' → United States;
                       unmapped 2-letter codes pass through, never
                       blank-claimed) — multi-location rows take the SET
      locationsText    locality + region, '; '-joined

    Detail: served from the SAME feed (description == content_html) —
    detail_payload re-reads the cached feed, zero extra network. B1:
    non-dict / items not a list / missing url → refuse.
    """

    KIND = "teamtailor"
    _TT = {}  # no employment-type field — honest blanks

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    def _feed(self) -> dict:
        url = f"https://{self.org}.teamtailor.com/jobs.json"
        d = fetch_json(url, cfg=self.cfg)
        if not isinstance(d, dict) or not isinstance(d.get("items"),
                                                     list):
            raise RuntimeError(
                f"teamtailor:{self.org}: {url} is not a JSON feed "
                f"(no items list) — board dead or shape changed; "
                f"refusing")
        return d

    @staticmethod
    def _rid(item: dict) -> str:
        jp = item.get("_jobposting") or {}
        ident = jp.get("identifier") or {}
        if ident.get("value") is not None:
            return str(ident["value"])
        u = str(item.get("url") or "")
        m = re.search(r"/jobs/(\d+)", u)
        return m.group(1) if m else ""

    @staticmethod
    def _locations(item: dict) -> list[dict]:
        jp = item.get("_jobposting") or {}
        locs = jp.get("jobLocation") or []
        if isinstance(locs, dict):
            locs = [locs]
        return [x for x in locs if isinstance(x, dict)]

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        if time_type:
            raise RuntimeError(
                "teamtailor: the feed serves no employment type — a "
                f"time_type filter ({time_type!r}) is unanswerable; "
                "use --time-type '' (the s19 driver does)")
        feed = self._feed()
        rows: dict[str, dict] = {}
        dropped_country = 0
        for item in feed["items"]:
            if not item.get("url"):
                continue          # B1-adjacent: an item with no URL
            rid = self._rid(item)
            if not rid or rid in rows:
                continue
            locs = self._locations(item)
            countries = []
            loc_texts = []
            for L in locs:
                a = L.get("address") or {}
                code = str(a.get("addressCountry") or "").strip()
                if code:
                    countries.append(
                        ADPWorkforceNowAdapter._COUNTRY.get(
                            code.upper(), code.upper()))
                seg = [str(a.get("addressLocality") or ""),
                       str(a.get("addressRegion") or "")]
                seg = [s for s in seg if s and "United States" not in s]
                if seg:
                    loc_texts.append(", ".join(seg))
            countries = _dedup_keep_order(countries)
            if country:
                if not any(workday.country_str_matches(c, country)
                           for c in countries):
                    dropped_country += 1
                    continue
            label, iso = _posted_label(str(item.get("date_published")
                                           or ""))
            rows[rid] = {
                "reqId": rid,
                "title": str(item.get("title") or ""),
                "company": "",
                "url": str(item.get("url")),
                "externalPath": f"/{rid}",
                "locationsText": "; ".join(_dedup_keep_order(
                    loc_texts)),
                "postedOn": label,
                "timeType": "",   # honest — feed serves none
                "bulletFields": [rid],
                "ats": "teamtailor",
                "firstPublishedIso": iso,
                "departments": [],
                "countries": countries,
                "remoteType": "",
            }
        meta = {
            "complete": True, "total": len(feed["items"]), "pages": 1,
            "country_client": False,
            "client_filtered": dropped_country,
            "ats": "teamtailor",
        }
        print(f"[{progress_label}] teamtailor:{self.org}: "
              f"{len(rows)} rows of {meta['total']} listed"
              + (f" ({dropped_country} non-{country} dropped "
                 f"client-side)" if dropped_country else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        feed = self._feed()
        for item in feed["items"]:
            if self._rid(item) != rid:
                continue
            jp = item.get("_jobposting") or {}
            locs = self._locations(item)
            loc_texts = []
            countries = []
            for L in locs:
                a = L.get("address") or {}
                code = str(a.get("addressCountry") or "").strip()
                if code:
                    countries.append(ADPWorkforceNowAdapter._COUNTRY.get(
                        code.upper(), code.upper()))
                seg = [str(a.get("addressLocality") or ""),
                       str(a.get("addressRegion") or "")]
                seg = [s for s in seg if s and "United States" not in s]
                if seg:
                    loc_texts.append(", ".join(seg))
            label, iso = _posted_label(str(item.get("date_published")
                                           or ""))
            org = (jp.get("hiringOrganization") or {}).get("name") or ""
            desc = str(item.get("content_html")
                       or jp.get("description") or "")
            return {
                "jobPostingInfo": {
                    "title": str(item.get("title") or ""),
                    "location": loc_texts[0] if loc_texts else "",
                    "additionalLocations": loc_texts[1:],
                    "jobDescription": desc,
                    "timeType": "",     # honest — feed serves none
                    "startDate": iso or "",
                    "externalUrl": str(item.get("url") or ""),
                    "jobReqId": rid,
                    "postedOn": label,
                    "country": ({"descriptor": countries[0]}
                                if countries else None),
                },
                "hiringOrganization": {"name": org},
                "similarJobs": [],
                "firstPublishedIso": iso,
            }
        return None

# ── radancy (tpt portal: SearchJobs pagination + JobDetail articles) ───────

class RadancyAdapter:
    """ats:radancy:{host} — the Radancy/TPT portal (jobs.{company}.com).

    S21 discovery (Lenovo jobs.lenovo.com, live-pinned 2026-09-27:
    1,010 global jobs, 255 matching 'United States'). List pages are
    SERVER-RENDERED at

      /en_US/careers/SearchJobs/?jobRecordsPerPage=10&jobOffset={n}
                                    [&search={term}]

    (jobRecordsPerPage is server-capped at 10; jobOffset advances
    cleanly). The free-text search param hits the LOCATION index —
    'United States' is a server-side prefilter (verified: all rows US;
    the client ladder still re-verifies each row loudly).

    Card map: the JobDetail/{Title-Slug}/{id} anchor (reqId = the URL
    id), span.paragraph = department, the article__header__text__subtitle
    spans = location then 'Req #: WD…' (Lenovo's own requisition id →
    bulletFields).

    Detail (JobDetail/{slug}/{id}): STRUCTURED field labels —
    Country/Region, State, City, Date ('Saturday, September 26, 2026'),
    Working time ('Full-time' — _TT-normalized) — plus the 'Description
    and Requirements' article's field__value HTML as the full JD. B1:
    a 200 page with no JobDetail cards and no empty-marker → refuse.
    """

    KIND = "radancy"
    _TT = {"full-time": "Full time", "full time": "Full time",
           "part-time": "Part time", "part time": "Part time",
           "contract": "Contract", "internship": "Internship",
           "temporary": "Temporary"}

    def __init__(self, org: str, cfg: Config):
        # org = the portal HOST (e.g. 'jobs.lenovo.com')
        self.org = org
        self.cfg = cfg

    @property
    def _base(self) -> str:
        return f"https://{self.org}"

    # -- list ------------------------------------------------------------

    def _search_page(self, offset: int, search: Optional[str]) -> str:
        q = f"{self._base}/en_US/careers/SearchJobs/"
        q += f"?jobRecordsPerPage=10&jobOffset={offset}"
        if search:
            from urllib.parse import quote
            q += f"&search={quote(search)}"
        return _fetch_text_cached(q, f"ats:radancy:{self.org}", self.cfg)

    @staticmethod
    def _parse_cards(html: str) -> list[dict]:
        cards = []
        seen = set()
        for m in re.finditer(
                r'href="([^"]*JobDetail/([A-Za-z0-9-]+)/(\d+))"',
                html):
            url, slug, jid = m.group(1), m.group(2), m.group(3)
            if jid in seen:
                continue
            seen.add(jid)
            # the card block around this link: title anchor text,
            # department, location, Req #
            i = m.start()
            block = html[i:html.find("</article>", i)]
            block = block[:6000] if len(block) > 6000 else block
            tm = re.search(
                r'JobDetail/[A-Za-z0-9-]+/\d+"[^>]*>\s*(.*?)\s*</a>',
                block, re.S)
            title = re.sub(r"<[^>]+>", "", tm.group(1)).strip() \
                if tm else ""
            dm = re.search(
                r'<span class="paragraph">\s*(.*?)\s*</span>', block, re.S)
            dept = re.sub(r"<[^>]+>", "", dm.group(1)).strip() \
                if dm else ""
            lm = re.search(
                r'article__header__text__subtitle">\s*<span>\s*(.*?)'
                r"\s*</span>", block, re.S)
            loc = re.sub(r"<[^>]+>", "", lm.group(1)).strip() \
                if lm else ""
            rm = re.search(r"Req #:?\s*([A-Z0-9]+)", block)
            req = rm.group(1) if rm else ""
            cards.append({"reqId": jid, "title": title, "url": url,
                          "department": dept, "location": loc,
                          "reqNo": req})
        return cards

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        if time_type:
            raise RuntimeError(
                "radancy: the list serves no employment type (it lives "
                "in the detail's 'Working time' field) — a time_type "
                f"filter ({time_type!r}) is unanswerable at list time; "
                "use --time-type '' (the s19 driver does)")
        # server-side location prefilter for the US case (the search
        # param hits the location index); other countries walk unfiltered
        search = None
        if (country or "").strip().lower() in workday._US_COUNTRY_NAMES:
            search = "United States"
        rows: dict[str, dict] = {}
        dropped_country = 0
        offset = 0
        pages = 0
        total_cards = 0
        while pages < 150:
            html = self._search_page(offset, search)
            if len(html) < 500:
                raise RuntimeError(
                    f"radancy:{self.org}: short/empty search page at "
                    f"offset {offset} — refusing")
            cards = self._parse_cards(html)
            if not cards:
                break
            for c in cards:
                if country and not _plain_loc_in_country(
                        c["location"], country):
                    dropped_country += 1
                    continue
                rows[c["reqId"]] = {
                    "reqId": c["reqId"],
                    "title": c["title"],
                    "company": "",
                    "url": c["url"] if c["url"].startswith("http")
                           else f"{self._base}{c['url']}",
                    "externalPath": f"/{c['reqId']}",
                    "locationsText": c["location"],
                    "postedOn": "",      # honest — detail fills
                    "timeType": "",      # honest — detail fills
                    "bulletFields": [x for x in (c["reqId"], c["reqNo"])
                                     if x],
                    "ats": "radancy",
                    "firstPublishedIso": "",
                    "departments": [c["department"]] if c["department"] else [],
                    "countries": [],
                    "remoteType": "",
                }
            total_cards += len(cards)
            pages += 1
            offset += len(cards)
            if pages % 5 == 0:
                print(f"[{progress_label}] radancy:{self.org} "
                      f"page {pages}: {len(rows)} rows kept "
                      f"({dropped_country} dropped)", file=sys.stderr,
                      flush=True)
        meta = {
            "complete": True, "total": total_cards, "pages": pages,
            "country_client": False,
            "client_filtered": dropped_country,
            "ats": "radancy",
            "server_prefilter": search,
        }
        print(f"[{progress_label}] radancy:{self.org}: {len(rows)} rows"
              + (f" of {total_cards} cards"
                 + (f" (server search={search!r})"
                    if search else ""))
              + (f" ({dropped_country} non-{country} dropped "
                 f"client-side)" if dropped_country else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    # -- detail -----------------------------------------------------------

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        # resolve the URL from the list (the title slug is not derivable)
        rows, _ = self.list_board(country=country)
        row = rows.get(rid)
        if not row:
            return None
        url = row["url"]
        page = _fetch_text_cached(url, f"ats:radancy:{self.org}", self.cfg)
        if len(page) < 500 or "JobDetail" not in url:
            return None
        # structured fields: label → value pairs
        fields: dict[str, str] = {}
        for m in re.finditer(
                r'article__content__view__field__label">\s*([^<]{2,40})'
                r'\s*</div>\s*'
                r'<div class="article__content__view__field__value">\s*'
                r'(.*?)\s*</div>', page, re.S):
            label = m.group(1).strip().rstrip(":")
            value = re.sub(r"<[^>]+>", " ", m.group(2))
            value = " ".join(value.split())
            fields[label] = value
        country_field = fields.get("Country/Region", "")
        state = fields.get("State", "")
        city = fields.get("City", "")
        loc_parts = [p for p in (city, state, country_field) if p]
        loc_text = ", ".join(loc_parts)
        tt_raw = fields.get("Working time", "")
        tt = self._TT.get(tt_raw.lower(), tt_raw) if tt_raw else ""
        date_raw = fields.get("Date", "")
        iso = ""
        if date_raw:
            try:
                dt = datetime.strptime(date_raw.replace(",", ""),
                                       "%A %B %d %Y")
                iso = dt.date().isoformat()
            except ValueError:
                pass
        label, iso = _posted_label(iso or "")
        # the JD: the 'Description and Requirements' article's values
        desc = ""
        i = page.find("Description and Requirements")
        if i > 0:
            seg = page[i:page.find("</article>", i)]
            vals = re.findall(
                r'article__content__view__field__value">\s*(.*?)'
                r"\s*</div>", seg, re.S)
            desc = "\n".join(v.strip() for v in vals if v.strip())
        # the portal page carries NO org self-name (og:title is the job
        # title) — the config company is the authority (honest blank)
        # normalize the country descriptor to the workday vocabulary
        cdesc = country_field
        if cdesc and cdesc.strip().lower() in workday._US_COUNTRY_NAMES:
            cdesc = "United States"
        return {
            "jobPostingInfo": {
                "title": row["title"],
                "location": loc_text or row["locationsText"],
                "additionalLocations": [],
                "jobDescription": desc,
                "timeType": tt,
                "startDate": iso or "",
                "externalUrl": url,
                "jobReqId": fields.get("Req #", "") or rid,
                "postedOn": label,
                "country": ({"descriptor": cdesc}
                            if cdesc else None),
                "validThrough": "",
            },
            "hiringOrganization": {"name": ""},
            "similarJobs": [],
            "firstPublishedIso": iso,
        }

# ── rippling (ats.rippling.com Next.js boards) ─────────────────────────────

class RipplingAdapter:
    """ats:rippling:{slug} — the public board at
    ats.rippling.com/{slug}/jobs (a Next.js SSR app).

    S21 discovery (Webull, live-pinned 2026-09-27: 30 jobs / 2 pages).
    The jobs are NOT behind an API from plain fetch (the XHR layer sits
    behind Cloudflare's challenge platform) — but every page is
    SERVER-RENDERED with the full __NEXT_DATA__ payload:

      pageProps.dehydratedState.queries → queryKey
      ['board', {slug}, 'job-posts', …] → state.data
        {items, page, pageSize, totalItems, totalPages}
      item = {id (uuid), name, url, department: {name},
              locations: [{name, country: 'United States',
                           countryCode: 'US', state, stateCode, city,
                           workplaceType: ON_SITE|REMOTE|HYBRID}]}

    Pagination: ?page={n} (0-based, totalPages from the payload).

    Detail (/{slug}/jobs/{uuid}): apiData.jobPost = {name,
    companyName (the SELF-NAME), createdOn (ISO w/ TZ), employmentType
    {label: SALARIED_FT, id: 'Salaried, full-time'} → _TT, description
    {company, role} (both HTML), url}. timeType/postedOn are
    DETAIL-only fields — the list serves neither (honest blanks; a
    list-time_type filter is REFUSED, the paylocity class). B1: no
    __NEXT_DATA__ / no job-posts query / items not a list → refuse.
    """

    KIND = "rippling"
    _TT = {"salaried_ft": "Full time", "salaried, full-time": "Full time",
           "hourly_ft": "Full time", "hourly, full-time": "Full time",
           "salaried_pt": "Part time", "salaried, part-time": "Part time",
           "hourly_pt": "Part time", "hourly, part-time": "Part time",
           "contract": "Contract", "internship": "Internship",
           "temporary": "Temporary"}

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    @staticmethod
    def _next_data(html: str) -> dict:
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">'
            r"(.*?)</script>", html, re.S)
        if not m:
            raise RuntimeError(
                "rippling: no __NEXT_DATA__ — not a rippling board page")
        return json.loads(m.group(1))

    @classmethod
    def _job_posts_query(cls, html: str) -> dict:
        d = cls._next_data(html)
        dh = (d.get("props", {}).get("pageProps", {})
                .get("dehydratedState", {}))
        for q in dh.get("queries", []) or []:
            key = str(q.get("queryKey", ""))
            if "job-posts" in key:
                data = (q.get("state") or {}).get("data") or {}
                if isinstance(data, dict) and \
                        isinstance(data.get("items"), list):
                    return data
        raise RuntimeError(
            "rippling: no job-posts query in __NEXT_DATA__ — board dead "
            "or shape changed; refusing")

    def _page(self, n: int) -> dict:
        url = f"https://ats.rippling.com/{self.org}/jobs?page={n}"
        html = _fetch_text_cached(url, f"ats:rippling:{self.org}",
                                  self.cfg)
        if len(html) < 500:
            raise RuntimeError(
                f"rippling:{self.org}: short/empty board page {n} — "
                f"refusing")
        return self._job_posts_query(html)

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        if time_type:
            raise RuntimeError(
                "rippling: the board LIST serves no employment type (it "
                "lives in the detail's employmentType) — a time_type "
                f"filter ({time_type!r}) is unanswerable at list time; "
                "use --time-type '' (the s19 driver does)")
        rows: dict[str, dict] = {}
        dropped_country = 0
        total = None
        page = 0
        pages = 0
        while pages < 60:
            data = self._page(page)
            total = int(data.get("totalItems") or 0)
            totalPages = int(data.get("totalPages") or 1)
            items = data.get("items") or []
            if not items:
                break
            for it in items:
                rid = str(it.get("id") or "")
                if not rid or rid in rows:
                    continue
                locs = [L for L in (it.get("locations") or [])
                        if isinstance(L, dict)]
                countries = _dedup_keep_order(
                    [str(L.get("country") or "") for L in locs
                     if L.get("country")])
                if country and not any(
                        workday.country_str_matches(c, country)
                        for c in countries):
                    dropped_country += 1
                    continue
                loc_text = "; ".join(_dedup_keep_order(
                    [str(L.get("name") or "") for L in locs
                     if L.get("name")]))
                remote = any(str(L.get("workplaceType")).upper()
                             == "REMOTE" for L in locs)
                rows[rid] = {
                    "reqId": rid,
                    "title": str(it.get("name") or ""),
                    "company": "",
                    "url": str(it.get("url") or ""),
                    "externalPath": f"/{rid}",
                    "locationsText": loc_text,
                    "postedOn": "",      # honest — detail fills
                    "timeType": "",      # honest — detail fills
                    "bulletFields": [rid],
                    "ats": "rippling",
                    "firstPublishedIso": "",
                    "departments": [str((it.get("department") or {})
                                        .get("name") or "")]
                    if (it.get("department") or {}).get("name") else [],
                    "countries": countries,
                    "remoteType": "Remote" if remote else "",
                }
            pages += 1
            page += 1
            if page >= totalPages:
                break
        meta = {
            "complete": True, "total": total if total is not None
            else len(rows), "pages": pages,
            "country_client": False,
            "client_filtered": dropped_country,
            "ats": "rippling",
        }
        print(f"[{progress_label}] rippling:{self.org}: {len(rows)} rows"
              + (f" of {meta['total']} listed" if meta['total'] else "")
              + (f" ({dropped_country} non-{country} dropped "
                 f"client-side)" if dropped_country else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").rsplit("/", 1)[-1]
        # list rows carry the canonical url; fallback constructs it
        url = None
        rows, _ = self.list_board(country=country)
        row = rows.get(rid)
        if row:
            url = row["url"]
        if not url:
            url = f"https://ats.rippling.com/{self.org}/jobs/{rid}"
        page = _fetch_text_cached(url, f"ats:rippling:{self.org}",
                                  self.cfg)
        if len(page) < 500:
            return None
        d = self._next_data(page)
        api = d.get("props", {}).get("pageProps", {}).get("apiData") or {}
        jp = api.get("jobPost") or {}
        if not jp or str(jp.get("uuid") or rid) != rid and \
                not jp.get("name"):
            return None
        et = jp.get("employmentType") or {}
        tt_raw = str(et.get("id") or et.get("label") or "")
        tt = self._TT.get(tt_raw.lower(), tt_raw) if tt_raw else ""
        created = str(jp.get("createdOn") or "")
        label, iso = _posted_label(created)
        desc = jp.get("description") or {}
        full_desc = "\n".join(
            x for x in (str(desc.get("company") or ""),
                        str(desc.get("role") or "")) if x)
        wls = [str(x) for x in (jp.get("workLocations") or []) if x]
        return {
            "jobPostingInfo": {
                "title": str(jp.get("name") or ""),
                "location": wls[0] if wls else
                (rows.get(rid, {}).get("locationsText", "") if rows
                 else ""),
                "additionalLocations": wls[1:],
                "jobDescription": full_desc,
                "timeType": tt,
                "startDate": iso or "",
                "externalUrl": str(jp.get("url") or url),
                "jobReqId": rid,
                "postedOn": label,
                "country": ({"descriptor": "United States"}
                            if country and
                            workday.country_str_matches(
                                "United States", country) else None),
                "validThrough": "",
            },
            "hiringOrganization": {
                "name": str(jp.get("companyName") or "")},
            "similarJobs": [],
            "firstPublishedIso": iso,
        }


# ══ S22 wave-3 platform classes ═══════════════════════════════════════════
# Contracts: ingest/data/ats_seed/s22_wave3/re_A.md / re_B.md (live-verified
# 2026-09-30). Each class follows the ADP/Rippling conventions: list_board
# returns (rows, meta) in the workday dialect; detail_payload(external_path)
# returns the workday-dialect jobPostingInfo envelope.


# ── breezy (S22: <org>.breezy.hr flat /json feed; detail = 2nd ld+json) ───
class BreezyAdapter:
    """bitdeer.breezy.hr/json — one call, whole board, no pagination, no
    auth (re_A.md §1). Rows: reqId=id, timeType=type.name map, country
    via locations[].country.id ISO-2. Detail = /p/<friendly_id> page →
    the JobPosting ld+json (2nd block; 1st is WebSite)."""

    KIND = "breezy"
    _TT = {"fulltime": "Full time", "full-time": "Full time",
           "full time": "Full time", "parttime": "Part time",
           "part-time": "Part time", "part time": "Part time",
           "contract": "Contract", "internship": "Internship",
           "temporary": "Temporary"}
    _COUNTRY = {"US": "United States", "CA": "Canada", "SG": "Singapore",
                "GB": "United Kingdom", "DE": "Germany", "JP": "Japan",
                "AU": "Australia", "MY": "Malaysia", "NO": "Norway"}

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    def _feed(self) -> list:
        url = f"https://{self.org}.breezy.hr/json"
        payload = _CACHE.get(url)
        if payload and time.monotonic() - payload[0] < _CACHE_TTL:
            return payload[1]
        d = fetch_json(url, cfg=self.cfg)
        if not isinstance(d, list) or not d:
            raise RuntimeError(
                f"breezy:{self.org}: /json payload is not a non-empty "
                f"list — board dead or shape changed; refusing")
        _CACHE[url] = (time.monotonic(), d)
        return d

    @staticmethod
    def _row_country(job: dict) -> str:
        """ANY-location rule (re_A.md §1.6): a row is US if ANY entry
        in locations[] carries country.id == 'US' (multi-loc rows list
        the non-US primary first — a first-only check under-counted
        bitdeer 46 → 41). Returns the first US country when one exists
        (else the first non-empty country)."""
        locs = ([job.get("location")] if job.get("location") else []) \
            + (job.get("locations") or [])
        first_any = ""
        for loc in locs:
            code = ((loc or {}).get("country") or {}).get("id") or ""
            if not code:
                continue
            name = BreezyAdapter._COUNTRY.get(code, code)
            if code == "US":
                return name
            if not first_any:
                first_any = name
        return first_any

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped_country = dropped_tt = 0
        feed = self._feed()
        for job in feed:
            rid = str(job.get("id") or "")
            if not rid:
                continue
            c = self._row_country(job)
            if country and not workday.country_str_matches(c, country):
                dropped_country += 1
                continue
            tt = self._TT.get(str(((job.get("type") or {}).get("name")
                                   or "")).strip().lower(), "")
            if time_type and tt and tt.lower() != time_type.lower():
                dropped_tt += 1
                continue
            locs = []
            for loc in (job.get("locations") or []) or []:
                name = str((loc or {}).get("name") or "").strip()
                if name and name not in locs:
                    locs.append(name)
            primary = str((job.get("location") or {}).get("name") or "")
            if primary and primary not in locs:
                locs.insert(0, primary)
            label, iso = _posted_label(str(job.get("published_date") or ""))
            rows[rid] = {
                "reqId": rid,
                "title": str(job.get("name") or ""),
                "company": str(((job.get("company") or {}).get("name"))
                               or ""),
                "url": str(job.get("url") or ""),
                "externalPath": f"/{job.get('friendly_id') or rid}",
                "locationsText": "; ".join(locs[:4]),
                "postedOn": label,
                "postedOnIso": iso,
                "timeType": tt,
                "bulletFields": [rid],
                "ats": "breezy",
            }
        meta = {"rows": len(rows), "total": len(feed), "complete": True,
                "pages": 1,
                "client_filtered": dropped_country + dropped_tt,
                "client_filtered_country": dropped_country,
                "client_filtered_time": dropped_tt}
        print(f"[{progress_label}] breezy:{self.org}: {len(rows)} rows of "
              f"{len(feed)} listed ({dropped_country} non-{country or '-'},"
              f" {dropped_tt} non-FT dropped client-side)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        fid = str(external_path or "").strip("/").split("/")[-1] or None
        if not fid:
            return None
        # find the feed row first (authoritative fields incl. locations)
        row = None
        for job in self._feed():
            if str(job.get("friendly_id") or "") == fid \
                    or str(job.get("id") or "") == fid:
                row = job
                break
        if row is None:
            return None
        url = str(row.get("url") or
                  f"https://{self.org}.breezy.hr/p/{fid}")
        html = _fetch_text_cached(url, f"ats:breezy:{self.org}:{fid}",
                                  self.cfg)
        desc = ""
        for block in re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>'
                r'(.*?)</script>', html or "", re.S):
            try:
                d = json.loads(block)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                desc = str(d.get("description") or "")
                break
        if not desc:
            # re_A.md §1.4 fallback: <div class="description"> holds the
            # same markup when the ld+json is absent (GTT pages)
            m = re.search(r'class="description">(.*?)</div>',
                          html or "", re.S)
            if m:
                desc = m.group(1).strip()
        locs = [str((l or {}).get("name") or "").strip()
                for l in (row.get("locations") or [])]
        tt = self._TT.get(str(((row.get("type") or {}).get("name") or "")
                              ).strip().lower(), "")
        return {
            "jobPostingInfo": {
                "title": str(row.get("name") or ""),
                "location": str((row.get("location") or {}).get("name")
                                or ""),
                "additionalLocations": [l for l in locs if l][1:],
                "jobDescription": desc,
                "timeType": tt,
                "startDate": str(row.get("published_date") or ""),
                "externalUrl": url,
                "jobReqId": str(row.get("id") or ""),
                "postedOn": "",
                "country": {"descriptor": self._row_country(row)},
            },
            "hiringOrganization": {
                "name": str(((row.get("company") or {}).get("name"))
                            or "")},
            "similarJobs": [],
        }


# ── jobvite (S22: jobs.jobvite.com SSR search; r=USA server filter) ────────
class JobviteAdapter:
    """jobs.jobvite.com/{org}/search?p=N — 50 rows/page SSR table; the
    region facet `r=USA` is server-side WHEN paired with p (re_A.md §3).
    reqId = /{org}/job/<EId> path segment. Detail = job page ld+json
    (jobLocation ARRAY — any US address makes the row US). Board home
    /{org} is INCOMPLETE (77/108) — always walk /search."""

    KIND = "jobvite"

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    def _page(self, p: int, region: str) -> tuple[list, int, str]:
        url = (f"https://jobs.jobvite.com/{self.org}/search"
               f"?p={p}" + (f"&r={region}" if region else ""))
        html = fetch_text(url, cfg=self.cfg)
        if not html or "jv-search-list" not in html:
            return [], 0, html or ""
        total = 0
        m = re.search(r'jv-pagination-text">\s*(\d+)\s*-\s*(\d+) of (\d+)',
                      html)
        if m:
            total = int(m.group(3))
        jobs = []
        for tr in re.findall(r'<tr>(.*?)</tr>', html, re.S):
            a = re.search(
                r'<a[^>]+href="(/' + re.escape(self.org) +
                r'/job/([A-Za-z0-9]+))"[^>]*>\s*(.*?)\s*</a>', tr, re.S)
            if not a:
                continue
            loc = re.search(
                r'jv-job-list-location">\s*(.*?)\s*</td>', tr, re.S)
            jobs.append({
                "href": a.group(1),
                "rid": a.group(2),
                "title": re.sub(r"<[^>]+>", "", a.group(3)).strip(),
                "loc": re.sub(r"<[^>]+>+", " ",
                              re.sub(r"<[^>]+>", "", loc.group(1)
                                     if loc else "")).strip(),
            })
        return jobs, total, html

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        region = "USA" if country else ""
        rows: dict[str, dict] = {}
        total = 0
        pages = 0
        p = 0
        while True:
            jobs, t, _html = self._page(p, region)
            if not jobs:
                break
            if t:
                total = t
            pages += 1
            for j in jobs:
                if j["rid"] in rows:
                    continue
                rows[j["rid"]] = {
                    "reqId": j["rid"],
                    "title": j["title"],
                    "company": "",
                    "url": f"https://jobs.jobvite.com{j['href']}",
                    "externalPath": f"/{j['rid']}",
                    "locationsText": j["loc"],
                    "postedOn": "",
                    "timeType": "",
                    "bulletFields": [j["rid"]],
                    "ats": "jobvite",
                }
            first, last = (p * 50) + 1, (p * 50) + len(jobs)
            if total and last >= total:
                break
            if len(jobs) < 50:
                break
            p += 1
            if pages > 30:
                break
        # guard: an ignored r-filter can silently 0-result — if the
        # region filter produced nothing, retry unfiltered (detail-side
        # classification will trim; the countryfilter phase handles it)
        if region and not rows:
            print(f"[{progress_label}] jobvite:{self.org}: r={region} "
                  f"produced 0 rows — retrying unfiltered")
            return self.list_board(country=None, time_type=time_type,
                                   progress_label=progress_label)
        meta = {"rows": len(rows), "total": total, "complete": True,
                "pages": pages, "client_filtered": 0,
                "client_filtered_country": 0, "client_filtered_time": 0}
        print(f"[{progress_label}] jobvite:{self.org}: {len(rows)} rows "
              f"(total {total}, {pages} pages"
              f"{' , r=USA server filter' if region else ''})")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").split("/")[-1]
        if not rid:
            return None
        html = _fetch_text_cached(
            f"https://jobs.jobvite.com/{self.org}/job/{rid}",
            f"ats:jobvite:{self.org}:{rid}", self.cfg)
        info = {}
        for block in re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>'
                r'(.*?)</script>', html or "", re.S):
            try:
                d = json.loads(block)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                info = d
                break
        desc = str(info.get("description") or "")
        # meta line: CATEGORY<sep>LOC1<sep>LOC2 (all locations as text)
        meta_m = re.search(
            r'jv-job-detail-meta">\s*(.*?)\s*</p>', html or "", re.S)
        locs = []
        is_us = False
        jl = info.get("jobLocation") or []
        if isinstance(jl, dict):
            jl = [jl]
        for place in jl:
            addr = (place or {}).get("address") or {}
            city = str(addr.get("addressLocality") or "")
            region_name = str(addr.get("addressRegion") or "")
            cc = str(addr.get("addressCountry") or "")
            if cc and "united states" in cc.lower():
                is_us = True
            locs.append(", ".join(x for x in (city, region_name) if x))
        if meta_m and not locs:
            locs = [re.sub(r"<[^>]+>", "", x).strip() for x in
                    re.split(r"jv-inline-separator", meta_m.group(1))][1:]
        tt = ""
        et = info.get("employmentType")
        if isinstance(et, list) and et:
            tt = {"FULL_TIME": "Full time", "PART_TIME": "Part time",
                  "CONTRACTOR": "Contract"}.get(str(et[0]).upper(), "")
        elif isinstance(et, str):
            tt = {"FULL_TIME": "Full time", "PART_TIME": "Part time",
                  "CONTRACTOR": "Contract"}.get(et.upper(), "")
        return {
            "jobPostingInfo": {
                "title": str(info.get("title") or ""),
                "location": locs[0] if locs else "",
                "additionalLocations": locs[1:],
                "jobDescription": desc,
                "timeType": tt,
                "startDate": str(info.get("datePosted") or ""),
                "externalUrl":
                    f"https://jobs.jobvite.com/{self.org}/job/{rid}",
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor":
                            "United States" if is_us else None},
            },
            "hiringOrganization": {"name": ""},
            "similarJobs": [],
        }


# ── oracle hcm CE (S22: hcmRestApi recruitingCEJobRequisitions) ────────────
class OracleHCMAdapter:
    """spec org = '<host>|<siteNumber>' (e.g.
    eidg.fa.us6.oraclecloud.com|CX_1). List = the findReqs finder with
    limit/offset; rows carry Id/Title/PrimaryLocation/
    PrimaryLocationCountry + secondaryLocations. Detail =
    recruitingCEJobRequisitionDetails finder=ById;Id="<reqId>" (the
    quotes are REQUIRED, URL-encoded %22). re_B.md §1."""

    KIND = "oraclehcm"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        parts = org.split("|")
        self.host = parts[0]
        self.site = parts[1] if len(parts) > 1 else "CX_1"
        self.cfg = cfg

    def _list_url(self, offset: int, limit: int = 100) -> str:
        return (f"https://{self.host}/hcmRestApi/resources/latest/"
                f"recruitingCEJobRequisitions?onlyData=true"
                f"&expand=requisitionList.secondaryLocations"
                f"&finder=findReqs;siteNumber={self.site},"
                f"limit={limit},offset={offset}")

    def _rows(self) -> list:
        key = f"oraclehcm:{self.host}:{self.site}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        out = []
        total = None
        offset = 0
        while True:
            d = fetch_json(self._list_url(offset), cfg=self.cfg)
            items = (d or {}).get("items") or []
            if not items or "requisitionList" not in items[0]:
                raise RuntimeError(
                    f"oraclehcm:{self.host}: unexpected list payload "
                    f"(no items[0].requisitionList) — refusing")
            reqs = items[0].get("requisitionList") or []
            total = int(items[0].get("TotalJobsCount") or 0)
            out.extend(reqs)
            offset += 100
            if not reqs or (total and offset >= total) or len(reqs) < 100:
                break
            if offset > 3000:
                break
        _CACHE[key] = (time.monotonic(), out)
        return out

    @staticmethod
    def _row_country(req: dict) -> str:
        if str(req.get("PrimaryLocationCountry") or "").upper() == "US":
            return OracleHCMAdapter._US
        for sec in req.get("secondaryLocations") or []:
            if str((sec or {}).get("CountryCode") or "").upper() == "US":
                return OracleHCMAdapter._US
        c = str(req.get("PrimaryLocationCountry") or "")
        return c or ""

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        if time_type:
            # S13 refuse-the-unanswerable (pinned S22): both live boards
            # serve WorkerType/JobSchedule/ContractType = null — a time
            # filter would be a silent lie
            raise RuntimeError(
                f"oraclehcm:{self.host}: the CE API serves no reliable "
                f"timeType (WorkerType null on both live boards) — a "
                f"time filter is unanswerable; refusing")
        rows: dict[str, dict] = {}
        dropped_country = 0
        reqs = self._rows()
        for req in reqs:
            rid = str(req.get("Id") or "")
            if not rid:
                continue
            c = self._row_country(req)
            if country and not workday.country_str_matches(c, country):
                dropped_country += 1
                continue
            label, iso = _posted_label(str(req.get("PostedDate") or ""))
            rows[rid] = {
                "reqId": rid,
                "title": str(req.get("Title") or ""),
                "company": "",
                "url": (f"https://{self.host}/hcmUI/CandidateExperience/"
                        f"en/sites/{self.site}/job/{rid}"),
                "externalPath": f"/{rid}",
                "locationsText": str(req.get("PrimaryLocation") or ""),
                "postedOn": label,
                "postedOnIso": iso,
                "timeType": "",
                "bulletFields": [rid],
                "ats": "oraclehcm",
            }
        meta = {"rows": len(rows), "total": len(reqs), "complete": True,
                "pages": 1, "client_filtered": dropped_country,
                "client_filtered_country": dropped_country,
                "client_filtered_time": 0}
        print(f"[{progress_label}] oraclehcm:{self.host}: {len(rows)} "
              f"rows of {len(reqs)} listed ({dropped_country} "
              f"non-{country or '-'} dropped client-side)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").split("/")[-1]
        if not rid:
            return None
        url = (f"https://{self.host}/hcmRestApi/resources/latest/"
               f"recruitingCEJobRequisitionDetails?expand=all"
               f"&onlyData=true&finder=ById;Id=%22{rid}%22,"
               f"siteNumber={self.site}")
        d = fetch_json(url, cfg=self.cfg)
        items = (d or {}).get("items") or []
        if not items:
            return None
        info = items[0]
        req = next((r for r in self._rows() if str(r.get("Id")) == rid),
                   {})
        desc = str(info.get("ExternalDescriptionStr") or "")
        qual = str(info.get("ExternalQualificationsStr") or "")
        resp = str(info.get("ExternalResponsibilitiesStr") or "")
        body = "\n".join(x for x in (desc, resp, qual) if x and x != "None")
        return {
            "jobPostingInfo": {
                "title": str(info.get("Title") or req.get("Title") or ""),
                "location": str(info.get("PrimaryLocation")
                                or req.get("PrimaryLocation") or ""),
                "additionalLocations": [
                    str((s or {}).get("Name") or "")
                    for s in (info.get("secondaryLocations")
                              or req.get("secondaryLocations") or [])],
                "jobDescription": body,
                "timeType": str(info.get("WorkerType") or ""),
                "startDate": str(info.get("ExternalPostedStartDate")
                                 or req.get("PostedDate") or ""),
                "externalUrl": (f"https://{self.host}/hcmUI/"
                                f"CandidateExperience/en/sites/"
                                f"{self.site}/job/{rid}"),
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor": self._row_country(req)},
            },
            "hiringOrganization": {"name": ""},
            "similarJobs": [],
        }


# ── talentadore (S22: ats.talentadore.com feed via the careers page) ───────
class TalentAdoreAdapter:
    """spec org = careers-page host (e.g. amersports.careers.talentadore
    .com). The page carries #ta-json-careers[data-url] = space-separated
    feed urls (one per business unit); each feed is a FULL unpaginated
    dump with descriptions inline (re_B.md §4)."""

    KIND = "talentadore"
    _TT = {"full time": "Full time", "full-time": "Full time",
           "part time": "Part time", "part-time": "Part time"}

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    def _feed_urls(self) -> list:
        html = _fetch_text_cached(f"https://{self.org}/",
                                  f"ats:talentadore:{self.org}",
                                  self.cfg)
        m = re.search(
            r'id=["\']ta-json-careers["\'][^>]*data-url=["\']([^"\']+)["\']',
            html or "")
        if not m:
            raise RuntimeError(
                f"talentadore:{self.org}: no #ta-json-careers[data-url] "
                f"on the careers page — engine changed; refusing")
        return [u for u in m.group(1).split() if u.startswith("http")]

    def _jobs(self) -> list:
        key = f"talentadore:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        jobs = []
        seen = set()
        for u in self._feed_urls():
            d = fetch_json(u, cfg=self.cfg)
            for j in (d or {}).get("jobs") or []:
                tok = str(j.get("job_token") or "")
                if tok and tok in seen:
                    continue
                if tok:
                    seen.add(tok)
                jobs.append(j)
        _CACHE[key] = (time.monotonic(), jobs)
        return jobs

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped_country = dropped_tt = 0
        jobs = self._jobs()
        for j in jobs:
            tok = str(j.get("job_token") or j.get("id") or "")
            if not tok:
                continue
            c = str(j.get("country") or "")
            if country and not workday.country_str_matches(c, country):
                dropped_country += 1
                continue
            tt = self._TT.get(str(j.get("employment_type") or
                                  "").strip().lower(), "")
            if time_type and tt and tt.lower() != time_type.lower():
                dropped_tt += 1
                continue
            label, iso = _posted_label(str(j.get("start_date") or ""))
            loc_bits = [str(j.get("city") or ""), str(j.get("location")
                                                      or "")]
            loc = " — ".join(x for x in loc_bits if x)
            rows[tok] = {
                "reqId": tok,
                "title": str(j.get("name") or ""),
                "company": str(j.get("business_unit_name") or ""),
                "url": str(j.get("link") or ""),
                "externalPath": f"/{tok}",
                "locationsText": loc,
                "postedOn": label,
                "postedOnIso": iso,
                "timeType": tt,
                "bulletFields": [tok],
                "ats": "talentadore",
            }
        meta = {"rows": len(rows), "total": len(jobs), "complete": True,
                "pages": len(self._feed_urls()),
                "client_filtered": dropped_country + dropped_tt,
                "client_filtered_country": dropped_country,
                "client_filtered_time": dropped_tt}
        print(f"[{progress_label}] talentadore:{self.org}: {len(rows)} "
              f"rows of {len(jobs)} listed ({dropped_country} "
              f"non-{country or '-'} dropped client-side)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        tok = str(external_path or "").strip("/").split("/")[-1]
        j = next((x for x in self._jobs()
                  if str(x.get("job_token")) == tok
                  or str(x.get("id")) == tok), None)
        if j is None:
            return None
        tt = self._TT.get(str(j.get("employment_type") or
                              "").strip().lower(), "")
        return {
            "jobPostingInfo": {
                "title": str(j.get("name") or ""),
                "location": str(j.get("city") or ""),
                "additionalLocations": [],
                "jobDescription": str(j.get("description_html") or
                                      j.get("description_text") or ""),
                "timeType": tt,
                "startDate": str(j.get("start_date") or ""),
                "externalUrl": str(j.get("link") or ""),
                "jobReqId": tok,
                "postedOn": "",
                "country": {"descriptor": str(j.get("country") or "")},
            },
            "hiringOrganization": {
                "name": str(j.get("business_unit_name") or "")},
            "similarJobs": [],
        }


# ── workstream (S22: workstream.us SSR HTML board; JSON-LD detail) ─────────
class WorkstreamAdapter:
    """spec org = the board short-id (fcc54c54 — the slug 301s to it).
    List = /j/{org}/positions?page=N (10/page; currentPage/totalPages
    inline JS; page>totalPages = 200-with-0-cards stop). reqId =
    trailing 8-hex of the detail-URL slug. Detail = the job page's
    single JobPosting ld+json (re_A.md §2). Employment-type tags are
    on the card only ~40% of the time — detail is authoritative."""

    KIND = "workstream"
    _TT = {"full-time": "Full time", "full time": "Full time",
           "part-time": "Part time", "part time": "Part time"}

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    def _page(self, p: int) -> tuple[list, int]:
        url = f"https://www.workstream.us/j/{self.org}/positions?page={p}"
        html = fetch_text(url, cfg=self.cfg)
        if not html or "position-card" not in (html or ""):
            return [], 0
        tp = 1
        m = re.search(r"totalPages\s*=\s*(\d+)", html)
        if m:
            tp = int(m.group(1))
        cards = re.findall(
            r'<div class="position-card[^"]*"[^>]*>(.*?)(?=<div class="position-card|<div class="pagination|$)',
            html, re.S)
        jobs = []
        for card in cards:
            href = re.search(
                r"""onclick="location\.href='([^']+)'""", card)
            a = re.search(
                r"""<a[^>]+href="([^"]+)"[^>]*>\s*(.*?)\s*</a>""", card,
                re.S)
            loc = re.search(
                r'position-address[^>]*>\s*(.*?)\s*</div>', card, re.S)
            tag = re.search(r'<span class="tag[^"]*">\s*(.*?)\s*</span>',
                            card, re.S)
            if not href and not a:
                continue
            link = (href.group(1) if href
                    else (a.group(1) if a else ""))
            m2 = re.search(r"-([0-9a-f]{8})(?:\?|$)", link or "")
            rid = m2.group(1) if m2 else link.rsplit("/", 1)[-1][:40]
            jobs.append({
                "rid": rid,
                "url": link,
                "title": re.sub(r"<[^>]+>", "",
                                (a.group(2) if a else "")).strip(),
                "loc": re.sub(r"<[^>]+>", "",
                              loc.group(1) if loc else "").strip(),
                "tt": re.sub(r"<[^>]+>", "",
                             tag.group(1) if tag else "").strip(),
            })
        return jobs, tp

    def _all_jobs(self) -> list:
        """The full page walk, in-process cached. BOTH list_board and
        detail_payload call this — the details phase runs in a FRESH
        process (board_dump subprocess), so a list-populated _CACHE is
        EMPTY there; the walk must be re-runnable on demand."""
        key = f"workstream:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        all_jobs = []
        total_pages = 1
        p = 1
        while p <= total_pages and p <= 40:
            jobs, tp = self._page(p)
            total_pages = max(total_pages, tp)
            if not jobs:
                break
            all_jobs.extend(jobs)
            p += 1
        _CACHE[key] = (time.monotonic(), all_jobs)
        return all_jobs

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped_country = 0
        all_jobs = self._all_jobs()
        for j in all_jobs:
            if j["rid"] in rows:
                continue
            # every workstream address ends ', USA' (re_A.md) —
            # country marker from the address suffix
            is_us = bool(re.search(
                r",\s*(?:USA|U\.S\.A\.?)\s*$", j["loc"])) or bool(
                re.search(r",\s*[A-Z]{2}\s+\d{5}(?:,|$)", j["loc"]))
            if country and not is_us:
                dropped_country += 1
                continue
            rows[j["rid"]] = {
                "reqId": j["rid"],
                "title": j["title"],
                "company": "",
                "url": j["url"],
                "externalPath": f"/{j['rid']}",
                "locationsText": j["loc"],
                "postedOn": "",
                "timeType": self._TT.get(j["tt"].lower(), ""),
                "bulletFields": [j["rid"]],
                "ats": "workstream",
            }
        meta = {"rows": len(rows), "total": len(rows), "complete": True,
                "pages": 1, "client_filtered": dropped_country,
                "client_filtered_country": dropped_country,
                "client_filtered_time": 0}
        print(f"[{progress_label}] workstream:{self.org}: {len(rows)} "
              f"rows of {len(all_jobs)} ({dropped_country} "
              f"non-{country or '-'} dropped client-side)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").split("/")[-1]
        if not rid:
            return None
        # resolve the detail URL via the on-demand page walk (the
        # details phase is a FRESH process — no list cache exists)
        row_url = next((r["url"] for r in self._all_jobs()
                        if r.get("rid") == rid
                        or (r.get("url") or "").endswith(rid)), "")
        if not row_url:
            return None
        html = _fetch_text_cached(
            row_url, f"ats:workstream:{self.org}:{rid}", self.cfg)
        info = {}
        for block in re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>'
                r'(.*?)</script>', html or "", re.S):
            try:
                d = json.loads(block)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                info = d
                break
        et = info.get("employmentType")
        tt = ""
        if isinstance(et, list) and et:
            tt = {"FULL_TIME": "Full time", "PART_TIME": "Part time",
                  "CONTRACTOR": "Contract"}.get(str(et[0]).upper(), "")
        elif isinstance(et, str):
            tt = {"FULL_TIME": "Full time", "PART_TIME": "Part time",
                  "CONTRACTOR": "Contract"}.get(et.upper(), "")
        addr = ((info.get("jobLocation") or {}).get("address") or {})
        loc = ", ".join(x for x in (str(addr.get("streetAddress") or ""),
                                    str(addr.get("addressLocality") or ""),
                                    str(addr.get("addressRegion") or ""))
                        if x)
        return {
            "jobPostingInfo": {
                "title": str(info.get("title") or ""),
                "location": loc,
                "additionalLocations": [],
                "jobDescription": str(info.get("description") or ""),
                "timeType": tt,
                "startDate": str(info.get("datePosted") or ""),
                "externalUrl": row_url,
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor":
                            str(addr.get("addressCountry") or "")},
            },
            "hiringOrganization": {
                "name": str(((info.get("hiringOrganization") or {})
                             .get("name")) or "")},
            "similarJobs": [],
        }

# ── bamboohr (S22: /jobs/embed2.php feed + /careers/<id>/detail JSON) ──────
class BambooHRAdapter:
    """spec org = the company subdomain (hisenseusacorporation). Listing
    = the embed2.php widget feed (departments[].positions[] — NO
    country field, dates only at detail); detail = /careers/<id>/detail
    with atsLocation.country + employmentStatusLabel + description
    (re_C.md §2). The country filter is therefore DETAIL-CLASSIFIED —
    boards on this platform are typically single-country, so the list
    phase ships all rows and the countryfilter phase trims."""

    KIND = "bamboohr"

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    def _feed(self) -> list:
        key = f"bamboohr:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        url = (f"https://{self.org}.bamboohr.com/jobs/embed2.php"
               f"?version=1.0.0&format=json")
        d = fetch_json(url, cfg=self.cfg)
        positions = []
        for dept in (d or {}).get("departments") or []:
            for pos in dept.get("positions") or []:
                if pos.get("id") is not None:
                    pos = dict(pos)
                    pos["_department"] = str(dept.get("label") or "")
                    positions.append(pos)
        if not positions:
            raise RuntimeError(
                f"bamboohr:{self.org}: embed2.php returned no positions "
                f"— board dead or shape changed; refusing")
        _CACHE[key] = (time.monotonic(), positions)
        return positions

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        positions = self._feed()
        for pos in positions:
            rid = str(pos.get("id") or "")
            if not rid:
                continue
            rows[rid] = {
                "reqId": rid,
                "title": str(pos.get("name") or ""),
                "company": "",
                "url": str(pos.get("url") or
                           f"https://{self.org}.bamboohr.com/careers/{rid}"),
                "externalPath": f"/{rid}",
                "locationsText": str(pos.get("location") or ""),
                "postedOn": "",
                "timeType": "",
                "bulletFields": [rid, pos.get("_department") or ""],
                "ats": "bamboohr",
                "department": pos.get("_department") or "",
            }
        # NOTE (S12/S13 honest contract): country cannot be filtered at
        # list time (no field) — the netflix-class countryfilter phase
        # classifies from details. single-country boards ship as-is.
        meta = {"rows": len(rows), "total": len(rows), "complete": True,
                "pages": 1, "client_filtered": 0,
                "client_filtered_country": 0, "client_filtered_time": 0,
                "country_filter_pending": bool(country)}
        print(f"[{progress_label}] bamboohr:{self.org}: {len(rows)} rows "
              f"listed (country {country!r} is detail-classified "
              f"client-side — S12/S13)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").split("/")[-1]
        if not rid:
            return None
        url = (f"https://{self.org}.bamboohr.com/careers/{rid}/detail")
        # the detail endpoint requires the XHR headers (re_C.md §2)
        d = _fetch_json_xhr(url, f"ats:bamboohr:{self.org}:{rid}",
                            self.cfg)
        if not d:
            return None
        opening = ((d or {}).get("result") or {}).get("jobOpening") or {}
        if not opening:
            return None
        ats_loc = opening.get("atsLocation") or {}
        tt = str(opening.get("employmentStatusLabel") or "")
        tt = {"full-time": "Full time", "full time": "Full time",
              "part-time": "Part time", "part time": "Part time",
              }.get(tt.strip().lower(), tt)
        city = str(ats_loc.get("city") or "")
        state = str(ats_loc.get("state") or "")
        pos = next((p for p in self._feed()
                    if str(p.get("id")) == rid), {})
        return {
            "jobPostingInfo": {
                "title": str(opening.get("jobOpeningName")
                             or pos.get("name") or ""),
                "location": ", ".join(x for x in (city, state) if x),
                "additionalLocations": [],
                "jobDescription": str(opening.get("description") or ""),
                "timeType": tt,
                "startDate": str(opening.get("datePosted") or ""),
                "externalUrl": str(opening.get("jobOpeningShareUrl")
                                   or pos.get("url") or ""),
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor":
                            str(ats_loc.get("country") or "")},
            },
            "hiringOrganization": {"name": ""},
            "similarJobs": [],
        }


# ── ultipro / UKG (S22: LoadSearchResults POST; silent-empty trap) ─────────
class UltiProAdapter:
    """spec org = '<tenant>|<boardHash>' (MEY1000MEYER|7a970c3d-…).
    List = POST JobBoardView/LoadSearchResults with a PASCALCASE body —
    ProximitySearchType:0 is REQUIRED (null → silent empty, the trap
    pinned in re_B.md §3). Detail = OpportunityDetail page; the model
    is inline JS `new US.Opportunity.CandidateOpportunityDetail({...})`
    — string-aware brace scan."""

    KIND = "ultipro"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        parts = org.split("|")
        self.tenant = parts[0]
        self.hash = parts[1] if len(parts) > 1 else ""
        self.cfg = cfg

    def _board_url(self) -> str:
        return (f"https://recruiting.ultipro.com/{self.tenant}/JobBoard/"
                f"{self.hash}")

    def _search(self, skip: int, top: int = 50) -> dict:
        import urllib.request
        url = (self._board_url() + "/JobBoardView/LoadSearchResults")
        body = json.dumps({"opportunitySearch": {
            "Top": top, "Skip": skip, "QueryString": "",
            "OrderBy": None, "OrderByKey": None, "Filters": [],
            "Coordinates": None, "Extent": None,
            "ProximitySearchType": 0}}).encode()
        req = urllib.request.Request(
            url, data=body, method="POST", headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X "
                              "10_15_7) AppleWebKit/537.36"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped_country = 0
        total = None
        skip = 0
        while True:
            d = self._search(skip)
            opps = d.get("opportunities") or []
            total = int(d.get("totalCount") or 0)
            if total == 0:
                raise RuntimeError(
                    f"ultipro:{self.tenant}: totalCount=0 — silent-empty "
                    f"trap (wrong body?) or dead board; refusing")
            for o in opps:
                rid = str(o.get("RequisitionNumber") or o.get("Id") or "")
                if not rid:
                    continue
                locs = []
                is_us = False
                for loc in o.get("Locations") or []:
                    addr = (loc or {}).get("Address") or {}
                    cc = str(((addr.get("Country") or {}).get("Code"))
                             or "")
                    if cc.upper() in ("USA", "US"):
                        is_us = True
                    piece = ", ".join(x for x in (
                        str(loc.get("LocalizedName") or ""),
                        str(((addr.get("State") or {}).get("Code")) or ""))
                        if x)
                    if piece and piece not in locs:
                        locs.append(piece)
                if country and not is_us:
                    dropped_country += 1
                    continue
                ft = o.get("FullTime")
                tt = "Full time" if ft else ("Part time" if ft is False
                                             else "")
                label, iso = _posted_label(str(o.get("PostedDate") or ""))
                rows[rid] = {
                    "reqId": rid,
                    "title": str(o.get("Title") or ""),
                    "company": "",
                    "url": (self._board_url()
                            + f"/OpportunityDetail?opportunityId="
                            f"{o.get('Id')}"),
                    "externalPath": f"/{rid}",
                    "locationsText": "; ".join(locs[:3]),
                    "postedOn": label,
                    "postedOnIso": iso,
                    "timeType": tt,
                    "bulletFields": [rid],
                    "ats": "ultipro",
                }
            skip += 50
            if skip >= total or not opps:
                break
            if skip > 5000:
                break
        meta = {"rows": len(rows), "total": total, "complete": True,
                "pages": (skip // 50), "client_filtered": dropped_country,
                "client_filtered_country": dropped_country,
                "client_filtered_time": 0}
        print(f"[{progress_label}] ultipro:{self.tenant}: {len(rows)} "
              f"rows of {total} listed ({dropped_country} "
              f"non-{country or '-'} dropped client-side)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").split("/")[-1]
        if not rid:
            return None
        row = None
        cached = _CACHE.get(f"ultipro:{self.tenant}")
        if cached:
            pass  # list cache stores rows dict; find via full re-search
        # resolve the GUID via one search call (rows carry RequisitionNumber)
        d = self._search(0, top=500)
        opp = next((o for o in (d.get("opportunities") or [])
                    if str(o.get("RequisitionNumber")) == rid
                    or str(o.get("Id")) == rid), None)
        if opp is None:
            return None
        url = (self._board_url()
               + f"/OpportunityDetail?opportunityId={opp.get('Id')}")
        html = _fetch_text_cached(url, f"ats:ultipro:{self.tenant}:{rid}",
                                  self.cfg)
        desc = ""
        m = re.search(r"new\s+US\.Opportunity\.CandidateOpportunity"
                      r"Detail\(", html or "")
        if m:
            model = _brace_scan(html, m.end())
            try:
                obj = json.loads(model)
                desc = str(obj.get("Description") or "")
            except (json.JSONDecodeError, ValueError):
                desc = ""
        if not desc:
            desc = str(opp.get("BriefDescription") or "")
        locs = []
        for loc in opp.get("Locations") or []:
            addr = (loc or {}).get("Address") or {}
            piece = ", ".join(x for x in (
                str(addr.get("City") or ""),
                str(((addr.get("State") or {}).get("Code")) or ""))
                if x)
            if piece:
                locs.append(piece)
        ft = opp.get("FullTime")
        tt = "Full time" if ft else ("Part time" if ft is False else "")
        return {
            "jobPostingInfo": {
                "title": str(opp.get("Title") or ""),
                "location": locs[0] if locs else "",
                "additionalLocations": locs[1:],
                "jobDescription": desc,
                "timeType": tt,
                "startDate": str(opp.get("PostedDate") or ""),
                "externalUrl": url,
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor": self._US},
            },
            "hiringOrganization": {"name": ""},
            "similarJobs": [],
        }


def _brace_scan(s: str, start: int) -> str:
    """Extract a balanced {…} JSON object starting AT `start` (string-
    aware: braces inside quoted strings don't count). For the UltiPro
    inline-JS model extraction (re_B.md §3.5)."""
    if start >= len(s) or s[start] != "{":
        return ""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
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
                return s[start:i + 1]
    return ""


def _fetch_json_xhr(url: str, spec: str, cfg: "Config") -> Optional[dict]:
    """GET with X-Requested-With + Accept json (the BambooHR detail
    contract, re_C.md §2). Returns None on failure (callers decide)."""
    import urllib.request
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None


# ── j2w / SAP SuccessFactors RMK (S22: SSR 25-row pages + microdata) ───────
class J2WAdapter:
    """spec org = '<host>/<path>' (careers.joysonsafety.com/search;
    careers.cofcointernational.com/search-jobs). Server-rendered
    HTML: 25 rows/page via startrow=N; total from .paginationLabel.
    Rows: tr.data-row + a.jobTitle-link + span.jobLocation/jobDate.
    Detail = /job/<slug>/<reqId>/ with itemprop microdata (re_B.md
    §2). Country token = last comma-token (US/USA); multi-loc rows
    ('+1 more…') are detail-classified (netflix-class)."""

    KIND = "j2w"
    _US_TOKENS = {"us", "usa", "united states", "united states of america"}
    _DATE_FORMATS = ("%b %d, %Y", "%d %b %Y")

    def __init__(self, org: str, cfg: Config):
        self.org = org.rstrip("/")
        self.cfg = cfg

    def _page(self, startrow: int) -> tuple[list, int, str]:
        url = f"https://{self.org}?q=&sortColumn=referencedate&" \
              f"sortDirection=desc&startrow={startrow}"
        html = fetch_text(url, cfg=self.cfg)
        if not html or "searchresults" not in html:
            return [], 0, html or ""
        total = 0
        m = re.search(
            r'paginationLabel[^>]*>.*?of\s*<b>\s*(\d+)\s*</b>', html, re.S)
        if not m:
            m = re.search(r'Results\s*.*?of\s*<b>\s*(\d+)', html, re.S)
        if m:
            total = int(m.group(1))
        rows = []
        for tr in re.findall(r'<tr class="data-row">(.*?)</tr>',
                             html, re.S):
            a = re.search(
                r'<a[^>]+class="jobTitle-link"[^>]+href="([^"]+)"'
                r'[^>]*>\s*(.*?)\s*</a>', tr, re.S)
            if not a:
                continue
            loc = re.search(r'jobLocation[^>]*>\s*(.*?)\s*</span>',
                            tr, re.S)
            date = re.search(r'jobDate[^>]*>\s*(.*?)\s*</span>', tr, re.S)
            href = a.group(1)
            rid = href.strip("/").rsplit("/", 1)[-1]
            rows.append({
                "rid": rid,
                "href": href if href.startswith("http")
                        else f"https://{self.org.split('/')[0]}{href}",
                "title": re.sub(r"<[^>]+>", "", a.group(2)).strip(),
                "loc": re.sub(r"\s+", " ", re.sub(
                    r"<[^>]+>", " ", loc.group(1) if loc else "")).strip(),
                "date": re.sub(r"<[^>]+>", "", date.group(1)
                               if date else "").strip(),
            })
        return rows, total, html

    def _all_rows(self) -> list:
        """The full startrow walk, in-process cached. BOTH list_board and
        detail_payload call this — the details phase is a FRESH process
        (the list-populated _CACHE is empty there), so the walk must be
        re-runnable on demand."""
        key = f"j2w:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        all_rows: list = []
        total = 0
        startrow = 0
        pages = 0
        while True:
            page_rows, t, _html = self._page(startrow)
            if not page_rows:
                break
            if t:
                total = t
            pages += 1
            all_rows.extend(page_rows)
            startrow += 25
            if total and startrow >= total:
                break
            if len(page_rows) < 25:
                break
            if pages > 60:
                break
        _CACHE[key] = (time.monotonic(), all_rows)
        return all_rows

    @staticmethod
    def _row_is_us(loc: str) -> bool:
        tokens = [t.strip().lower() for t in loc.split(",")]
        return bool(tokens) and tokens[-1] in J2WAdapter._US_TOKENS

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped_country = 0
        pending_detail = 0
        all_rows = self._all_rows()
        total = len(all_rows)
        for j in all_rows:
            if j["rid"] in rows:
                continue
            loc_clean = re.sub(r"\(\+?\s*\d*\s*more[^)]*\)", "",
                               j["loc"]).strip()
            multi = "more" in j["loc"]
            if country:
                if self._row_is_us(loc_clean) or multi:
                    if multi and not self._row_is_us(loc_clean):
                        pending_detail += 1
                else:
                    dropped_country += 1
                    continue
            # dates: 'Sep 24, 2026' (en_US) or '21 Sept 2026' (en_GB)
            iso = ""
            for fmt in self._DATE_FORMATS:
                try:
                    iso = datetime.strptime(j["date"], fmt).date() \
                        .isoformat()
                    break
                except ValueError:
                    continue
            label, iso = _posted_label(iso)
            rows[j["rid"]] = {
                "reqId": j["rid"],
                "title": j["title"],
                "company": "",
                "url": j["href"],
                "externalPath": f"/{j['rid']}",
                "locationsText": j["loc"],
                "postedOn": label,
                "postedOnIso": iso,
                "timeType": "",
                "bulletFields": [j["rid"]],
                "ats": "j2w",
            }
        meta = {"rows": len(rows), "total": total, "complete": True,
                "pages": 1, "client_filtered": dropped_country,
                "client_filtered_country": dropped_country,
                "client_filtered_time": 0,
                "detail_pending_country": pending_detail}
        print(f"[{progress_label}] j2w:{self.org}: {len(rows)} rows of "
              f"{total} ({dropped_country} non-{country or '-'} dropped,"
              f" {pending_detail} multi-loc → detail)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").split("/")[-1]
        if not rid:
            return None
        # resolve the detail URL via the on-demand page walk (the
        # details phase is a FRESH process — no list cache exists)
        row_url = next((r["href"] for r in self._all_rows()
                        if r.get("rid") == rid), "")
        if not row_url:
            return None
        html = _fetch_text_cached(row_url, f"ats:j2w:{self.org}:{rid}",
                                  self.cfg)
        if not html:
            return None
        title_m = re.search(
            r'itemprop="title"[^>]*>\s*(.*?)\s*</span>', html, re.S)
        desc = ""
        # the description span NESTS another span (19KB payload): a
        # naive non-greedy match stops at the inner </span> — require
        # the double close (re_B.md §2.4 + live-verified 2026-09-30)
        desc_m = re.search(
            r'itemprop="description".*?<span class="jobdescription">'
            r'(.*?)</span>\s*</span>', html, re.S)
        if desc_m:
            desc = desc_m.group(1)
        else:
            desc_m = re.search(
                r'<span[^>]*class="jobdescription">(.*?)</span>',
                html or "", re.S)
            if desc_m:
                desc = desc_m.group(1)
        locs = re.findall(r'jobGeoLocation[^>]*>\s*(.*?)\s*</span>',
                          html, re.S)
        date_m = re.search(
            r'itemprop="datePosted"[^>]+content="([^"]+)"', html)
        date_iso = ""
        if date_m:
            raw = date_m.group(1)
            try:
                date_iso = datetime.strptime(
                    raw, "%a %b %d %H:%M:%S %Z %Y").date().isoformat()
            except ValueError:
                date_iso = ""
        label, iso = _posted_label(date_iso)
        is_us = any(self._row_is_us(l) for l in locs) if locs else False
        return {
            "jobPostingInfo": {
                "title": re.sub(r"<[^>]+>", "",
                                title_m.group(1) if title_m else "").strip(),
                "location": locs[0] if locs else "",
                "additionalLocations": locs[1:],
                "jobDescription": desc,
                "timeType": "",
                "startDate": iso or date_iso,
                "externalUrl": row_url,
                "jobReqId": rid,
                "postedOn": label,
                "country": {"descriptor": "United States" if is_us
                            else None},
            },
            "hiringOrganization": {"name": ""},
            "similarJobs": [],
        }


# ── ttiproxy (S22: ttigroup.com Drupal Workday-proxy module) ───────────────
class TTIDrupalAdapter:
    """spec org = region ('US'). ttigroup.com/ttigroup/api/v1/workday/
    jobPostings?region={org}&page=1&take=1000 — unauthenticated JSON,
    full Workday-shaped rows under job_json_data INCLUDING the HTML
    description (single call for the whole region board; re_C.md §1).
    7/645 rows have primaryLocation=null (all-US by region — honest
    fallback to the jobSite descriptor)."""

    KIND = "ttiproxy"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        self.org = org or "US"
        self.cfg = cfg

    def _rows(self) -> list:
        key = f"ttiproxy:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        url = (f"https://www.ttigroup.com/ttigroup/api/v1/workday/"
               f"jobPostings?region={self.org}&page=1&take=1000"
               f"&ajax_wrapper=html")
        d = fetch_json(url, cfg=self.cfg)
        data = (d or {}).get("data")
        if not isinstance(data, list) or not data:
            raise RuntimeError(
                f"ttiproxy:{self.org}: jobPostings returned no data[] — "
                f"board dead or shape changed; refusing")
        _CACHE[key] = (time.monotonic(), data)
        return data

    @staticmethod
    def _row_country(row: dict) -> str:
        jd = (row.get("job_json_data") or {})
        loc = jd.get("primaryLocation") or {}
        a3 = str(((loc.get("country") or {}).get("alpha3Code")) or "")
        if a3.upper() == "USA":
            return TTIDrupalAdapter._US
        if a3:
            return a3
        # region=US boards: null primaryLocation rows are US jobs
        return TTIDrupalAdapter._US

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped_tt = 0
        data = self._rows()
        for row in data:
            jd = (row.get("job_json_data") or {})
            rid = str(jd.get("id") or row.get("workday_id") or "")
            if not rid:
                continue
            loc = jd.get("primaryLocation") or {}
            # null-primaryLocation fallback (7/645 rows): the jobSite
            # DESCRIPTOR (not the raw job_site id — pinned S22)
            loc_text = str(loc.get("descriptor") or
                           ((jd.get("jobSite") or {}).get("descriptor"))
                           or "")
            tt = str(((jd.get("timeType") or {}).get("descriptor"))
                     or "")
            if time_type and tt and tt.lower() != time_type.lower():
                dropped_tt += 1
                continue
            label, iso = _posted_label(str(jd.get("startDate") or ""))
            rows[rid] = {
                "reqId": rid,
                "title": str(jd.get("title") or row.get("title") or ""),
                "company": str(((jd.get("company") or {}).get("descriptor"))
                               or "Techtronic Industries"),
                "url": str(jd.get("url") or
                           (f"https://www.ttigroup.com/careers/"
                            f"career-opportunities/job-details"
                            f"?data_source=wd&job_id={rid}")),
                "externalPath": f"/{rid}",
                "locationsText": loc_text,
                "postedOn": label,
                "postedOnIso": iso,
                "timeType": tt,
                "bulletFields": [rid],
                "ats": "ttiproxy",
                "jobSite": str(((jd.get("jobSite") or {}).get("descriptor"))
                               or ""),
            }
        meta = {"rows": len(rows), "total": len(data), "complete": True,
                "pages": 1, "client_filtered": dropped_tt,
                "client_filtered_country": 0,
                "client_filtered_time": dropped_tt}
        print(f"[{progress_label}] ttiproxy:{self.org}: {len(rows)} rows "
              f"of {len(data)} listed ({dropped_tt} non-FT dropped "
              f"client-side)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").split("/")[-1]
        row = next((r for r in self._rows()
                    if str((r.get("job_json_data") or {}).get("id"))
                    == rid or str(r.get("workday_id")) == rid), None)
        if row is None:
            return None
        jd = row.get("job_json_data") or {}
        loc = jd.get("primaryLocation") or {}
        loc_text = str(loc.get("descriptor") or
                       ((jd.get("jobSite") or {}).get("descriptor"))
                       or "")
        sec = str(jd.get("jobDescription") or "")
        return {
            "jobPostingInfo": {
                "title": str(jd.get("title") or ""),
                "location": loc_text,
                "additionalLocations": [],
                "jobDescription": sec,
                "timeType": str(((jd.get("timeType") or {})
                                 .get("descriptor")) or ""),
                "startDate": str(jd.get("startDate") or ""),
                "externalUrl": str(jd.get("url") or ""),
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor": self._row_country(row)},
            },
            "hiringOrganization": {
                "name": str(((jd.get("company") or {}).get("descriptor"))
                            or "Techtronic Industries")},
            "similarJobs": [],
        }


# ── wuxibio (S22: WP static + /server.php AJAX mirror of SAP SF) ───────────
class WuXiBioAdapter:
    """spec org = host (www.wuxibiologics.com). POST /server.php with
    type=join_us_filterBy_sapsf (first 50 + postCount) then
    join_us_more_sapsf with cumulative postId until drained
    (re_C.md §3). country[] MUST be an array key. Detail = the WP page
    (div.applyD_right > div.text = full description)."""

    KIND = "wuxibio"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    def _post_form(self, params: list) -> Optional[dict]:
        import urllib.request
        import urllib.parse
        body = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(
            f"https://{self.org}/server.php", data=body, method="POST",
            headers={"Content-Type":
                     "application/x-www-form-urlencoded",
                     "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac "
                                   "OS X 10_15_7) AppleWebKit/537.36",
                     "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            return None

    def _rows(self, country: str) -> list:
        key = f"wuxibio:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        d = self._post_form([
            ("catId", "30"), ("country[]", country),
            ("type", "join_us_filterBy_sapsf")])
        if not d or "result" not in d:
            raise RuntimeError(
                f"wuxibio:{self.org}: server.php filterBy failed — "
                f"board dead or shape changed; refusing")
        rows = list(d.get("result") or [])
        ids = [str(r.get("id")) for r in rows]
        total = int(d.get("postCount") or len(rows))
        guard = 0
        while len(rows) < total and guard < 40:
            guard += 1
            d2 = self._post_form([
                ("postId", ",".join(ids)), ("type", "join_us_more_sapsf"),
                ("catId", "30"), ("country[]", country),
                ("total", str(total))])
            if not d2:
                break
            more = list(d2.get("result") or [])
            if not more:
                break
            for r in more:
                if str(r.get("id")) not in ids:
                    ids.append(str(r.get("id")))
                    rows.append(r)
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        want = country or "all"
        rows: dict[str, dict] = {}
        data = self._rows(want)
        for r in data:
            rid = str(r.get("id") or "")
            if not rid:
                continue
            c = str(r.get("country") or "")
            if country and not workday.country_str_matches(c, country):
                continue
            label, iso = _posted_label(str(r.get("date") or ""))
            rows[rid] = {
                "reqId": rid,
                "title": str(r.get("title") or ""),
                "company": "WuXi Biologics",
                "url": str(r.get("href") or ""),
                "externalPath": f"/{rid}",
                "locationsText": c,
                "postedOn": label,
                "postedOnIso": iso,
                "timeType": "",
                "bulletFields": [rid],
                "ats": "wuxibio",
            }
        meta = {"rows": len(rows), "total": len(data), "complete": True,
                "pages": 1, "client_filtered": 0,
                "client_filtered_country": 0, "client_filtered_time": 0}
        print(f"[{progress_label}] wuxibio:{self.org}: {len(rows)} rows "
              f"of {len(data)} (country[] filter {want!r})")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").split("/")[-1]
        r = next((x for x in self._rows(country or "all")
                  if str(x.get("id")) == rid), None)
        if r is None:
            return None
        url = str(r.get("href") or "")
        html = _fetch_text_cached(url, f"ats:wuxibio:{self.org}:{rid}",
                                  self.cfg)
        desc = ""
        title = str(r.get("title") or "")
        m = re.search(
            r'<div class="title">([^<]*)</div>', html or "")
        if m:
            title = m.group(1).strip() or title
        m2 = re.search(r'applyD_right.*?<div class="text">(.*?)'
                       r'</div>\s*<', html or "", re.S)
        if m2:
            desc = m2.group(1)
        else:
            # broader: the .text div inside applyD_right
            m3 = re.search(
                r'<div class="applyD_right">(.*?)<div class="apply',
                html or "", re.S)
            if m3:
                seg = m3.group(1)
                m4 = re.search(r'<div class="text">(.*?)</div>\s*(?:<|$)',
                               seg, re.S)
                if m4:
                    desc = m4.group(1)
        return {
            "jobPostingInfo": {
                "title": title,
                "location": str(r.get("country") or ""),
                "additionalLocations": [],
                "jobDescription": desc,
                "timeType": "",
                "startDate": str(r.get("date") or ""),
                "externalUrl": url,
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor": str(r.get("country") or "")},
            },
            "hiringOrganization": {"name": "WuXi Biologics"},
            "similarJobs": [],
        }


# ── antintl (S22: hrcareersweb.antgroup.com social/position API) ───────────
class AntIntlAdapter:
    """spec org = bgCode ('M7892' = Ant International). POST
    hrcareersweb.antgroup.com/api/social/position/search — the US
    region filter is a HARDCODED city-code list (SUNNYVALE,USANYNYQEE,
    USADCDCWAS; country codes alone return 0), pageSize caps at 49
    (re_C.md §4). description+requirement are INLINE in rows."""

    KIND = "antintl"
    _US_REGIONS = "SUNNYVALE,USANYNYQEE,USADCDCWAS"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        self.org = org or "M7892"
        self.cfg = cfg

    def _search(self, page: int) -> dict:
        import urllib.request
        body = json.dumps({
            "language": "en", "channel": "group_official_site",
            "categories": "", "key": "",
            "regions": self._US_REGIONS, "subCategories": "",
            "pageIndex": page, "pageSize": 49,
            "bgCode": self.org}).encode()
        req = urllib.request.Request(
            "https://hrcareersweb.antgroup.com/api/social/position/search",
            data=body, method="POST", headers={
                "Content-Type": "application/json",
                "Origin": "https://www.ant-intl.com",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X "
                              "10_15_7) AppleWebKit/537.36"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")

    def _rows(self) -> list:
        key = f"antintl:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        out = []
        total = None
        page = 1
        while True:
            d = self._search(page)
            if not d.get("success"):
                raise RuntimeError(
                    f"antintl:{self.org}: position/search success=false "
                    f"({d.get('errorCode')}) — refusing")
            rows = d.get("content") or []
            total = int(d.get("totalCount") or 0)
            out.extend(rows)
            if not rows or len(out) >= total or page > 20:
                break
            page += 1
        _CACHE[key] = (time.monotonic(), out)
        return out

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        data = self._rows()
        for j in data:
            rid = str(j.get("id") or "")
            if not rid:
                continue
            locs = ", ".join(str(x) for x in (j.get("workLocations")
                                              or []))
            label, iso = _posted_label(str(j.get("publishTime") or ""))
            rows[rid] = {
                "reqId": rid,
                "title": str(j.get("name") or ""),
                "company": "Ant International",
                "url": (f"https://talent.antgroup.com/off-campus-position"
                        f"?positionId={rid}&from=intl"),
                "externalPath": f"/{rid}",
                "locationsText": locs,
                "postedOn": label,
                "postedOnIso": iso,
                "timeType": "",
                "bulletFields": [rid],
                "ats": "antintl",
            }
        meta = {"rows": len(rows), "total": len(data), "complete": True,
                "pages": 1, "client_filtered": 0,
                "client_filtered_country": 0, "client_filtered_time": 0}
        print(f"[{progress_label}] antintl:{self.org}: {len(rows)} US "
              f"rows (regions filter server-side)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").split("/")[-1]
        j = next((x for x in self._rows() if str(x.get("id")) == rid),
                 None)
        if j is None:
            return None
        body = "\n\n".join(x for x in (
            str(j.get("description") or ""),
            str(j.get("requirement") or "")) if x)
        locs = ", ".join(str(x) for x in (j.get("workLocations") or []))
        return {
            "jobPostingInfo": {
                "title": str(j.get("name") or ""),
                "location": locs,
                "additionalLocations": [],
                "jobDescription": body,
                "timeType": "",
                "startDate": str(j.get("publishTime") or ""),
                "externalUrl": (f"https://talent.antgroup.com/"
                                f"off-campus-position?positionId={rid}"
                                f"&from=intl"),
                "jobReqId": str(j.get("code") or rid),
                "postedOn": "",
                "country": {"descriptor": self._US},
            },
            "hiringOrganization": {"name": "Ant International"},
            "similarJobs": [],
        }


# ── sanity (S22: public Content-Lake GROQ over the site's dataset) ─────────
class SanityAdapter:
    """spec org = '<projectId>/<dataset>|<slug>|<language>' (NIU:
    cd9iwvgl/production|Jobs|en-us). One GROQ fetch returns the whole
    Jobs page doc; jobs = pageBuilder textArea sections (h4 title,
    first normal block = region, rest = description, ctas[0] = PDF
    link — re_D.md §2). No reqIds exist — the PDF sha1 in the cta url
    is the stable row key."""

    KIND = "sanity"

    def __init__(self, org: str, cfg: Config):
        parts = org.split("|")
        pid_ds = parts[0].split("/")
        self.project = pid_ds[0]
        self.dataset = pid_ds[1] if len(pid_ds) > 1 else "production"
        self.slug = parts[1] if len(parts) > 1 else "Jobs"
        self.language = parts[2] if len(parts) > 2 else "en-us"
        self.cfg = cfg

    def _groq(self) -> Optional[dict]:
        key = f"sanity:{self.project}:{self.dataset}:{self.slug}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        q = (f'*[_type=="page" && slug.current=="{self.slug}" && '
             f'language=="{self.language}"][0]'
             f'{{pageBuilder, _updatedAt}}')
        import urllib.parse
        url = (f"https://{self.project}.api.sanity.io/v2024-07-15/data/"
               f"query/{self.dataset}?query="
               f"{urllib.parse.quote(q)}")
        d = fetch_json(url, cfg=self.cfg)
        result = ((d or {}).get("result") if isinstance(d, dict)
                  else None)
        if result is None:
            raise RuntimeError(
                f"sanity:{self.project}: query returned no result doc — "
                f"dataset/slug/language wrong or engine changed; "
                f"refusing")
        _CACHE[key] = (time.monotonic(), result)
        return result

    @staticmethod
    def _blocks_text(blocks: list) -> str:
        out = []
        for b in blocks or []:
            if not isinstance(b, dict):
                continue
            text = "".join(str(c.get("text") or "")
                           for c in b.get("children") or []
                           if isinstance(c, dict))
            if text:
                out.append(text)
        return "\n".join(out)

    def _sections(self) -> list:
        doc = self._groq() or {}
        out = []
        region = ""
        for section in doc.get("pageBuilder") or []:
            if not isinstance(section, dict):
                continue
            # NIU shape (re_D.md §2): the SECTION ITSELF is _type
            # 'textArea' with text[]/ctas at the section level (NOT
            # nested under a .textArea key). Region headers are the
            # textArea sections whose blocks carry an h2 but no h4.
            _type = str(section.get("_type") or "")
            ta = section if _type == "textArea" else (section.get(
                "textArea") or {})
            if not ta or not ta.get("text"):
                continue
            blocks = [b for b in (ta.get("text") or [])
                      if isinstance(b, dict)]
            h2s = [self._blocks_text([b]) for b in blocks
                   if b.get("style") == "h2"]
            h4s = [self._blocks_text([b]) for b in blocks
                   if b.get("style") == "h4"]
            normals = [self._blocks_text([b]) for b in blocks
                       if b.get("style") in (None, "normal")]
            if h2s and not h4s:
                region = h2s[0]
                continue
            if not h4s:
                continue
            title = h4s[0]
            subtitle = normals[0] if normals else ""
            desc = "\n".join(normals[1:])
            url = ""
            ctas = (ta.get("ctas") or [])
            if isinstance(ctas, dict):
                ctas = ctas.get("ctas") or []
            if ctas and isinstance(ctas[0], dict):
                url = str((((ctas[0].get("link") or {})
                            .get("url"))) or "")
            out.append({"title": title, "subtitle": subtitle,
                        "desc": desc, "url": url, "region": region,
                        "updatedAt": str(doc.get("_updatedAt") or "")})
        return out

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        sections = self._sections()
        for s in sections:
            # US rows: the section's region header or the row subtitle
            # carries the country (NIU: h2 'United States:' headers)
            blob = (s["region"] + " " + s["subtitle"]).lower()
            is_us = ("united states" in blob or "usa" in blob
                     or bool(re.search(r"\bUS\b", s["region"])))
            if country and not is_us:
                continue
            m = re.search(r"/([0-9a-f]{40})\.(?:pdf|PDF)", s["url"])
            rid = m.group(1) if m else (
                re.sub(r"\W+", "-", s["title"]).strip("-").lower())
            if rid in rows:
                continue
            rows[rid] = {
                "reqId": rid,
                "title": s["title"],
                "company": "",
                "url": s["url"],
                "externalPath": f"/{rid}",
                "locationsText": s["region"],
                "postedOn": "",
                "timeType": "",
                "bulletFields": [rid],
                "ats": "sanity",
                "region": s["region"],
            }
        meta = {"rows": len(rows), "total": len(sections),
                "complete": True, "pages": 1,
                "client_filtered": 0, "client_filtered_country": 0,
                "client_filtered_time": 0}
        print(f"[{progress_label}] sanity:{self.project}/{self.dataset}:"
              f" {len(rows)} rows of {len(sections)} sections")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").split("/")[-1]
        s = None
        for cand in self._sections():
            m = re.search(r"/([0-9a-f]{40})\.(?:pdf|PDF)", cand["url"])
            key = m.group(1) if m else (
                re.sub(r"\W+", "-", cand["title"]).strip("-").lower())
            if key == rid:
                s = cand
                break
        if s is None:
            return None
        return {
            "jobPostingInfo": {
                "title": s["title"],
                "location": s["region"],
                "additionalLocations": [],
                "jobDescription": (s["subtitle"] + "\n" + s["desc"]).strip(),
                "timeType": "",
                "startDate": s["updatedAt"],
                "externalUrl": s["url"],
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor": "United States"},
            },
            "hiringOrganization": {"name": ""},
            "similarJobs": [],
        }


# ── wpjobboard (S22: WordPress Simple Job Board plugin REST) ───────────────
class WPJobBoardAdapter:
    """spec org = host (www.ascentage.com). /wp-json/wp/v2/jobpost
    (public CPT) + jobpost_location taxonomy; US = the 'United States'
    term (ascentage term id 38 — resolved by NAME at runtime, not the
    id). content.rendered = full JD inline (re_D.md §4)."""

    KIND = "wpjobboard"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    def _taxonomy(self, tax: str) -> dict:
        key = f"wpjobboard:{self.org}:{tax}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        d = fetch_json(
            f"https://{self.org}/wp-json/wp/v2/{tax}?per_page=100",
            cfg=self.cfg)
        mapping = {}
        for t in d or []:
            mapping[int(t.get("id") or 0)] = str(t.get("name") or "")
        _CACHE[key] = (time.monotonic(), mapping)
        return mapping

    def _posts(self) -> list:
        key = f"wpjobboard:{self.org}:posts"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        d = fetch_json(
            f"https://{self.org}/wp-json/wp/v2/jobpost?per_page=100",
            cfg=self.cfg)
        if not isinstance(d, list):
            raise RuntimeError(
                f"wpjobboard:{self.org}: jobpost CPT not exposed — "
                f"engine changed; refusing")
        _CACHE[key] = (time.monotonic(), d)
        return d

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        locs = self._taxonomy("jobpost_location")
        cats = self._taxonomy("jobpost_category")
        posts = self._posts()
        rows: dict[str, dict] = {}
        dropped_country = 0
        for p in posts:
            rid = str(p.get("id") or "")
            if not rid:
                continue
            loc_names = [locs.get(int(t), "") for t in
                         p.get("jobpost_location") or []]
            cat_names = [cats.get(int(t), "") for t in
                         p.get("jobpost_category") or []]
            c = "; ".join(x for x in loc_names if x)
            is_us = any("united states" in x.lower()
                        for x in loc_names if x)
            if country and not is_us:
                dropped_country += 1
                continue
            title = re.sub(r"<[^>]+>", "",
                           str((p.get("title") or {}).get("rendered")
                               or "")).strip()
            label, iso = _posted_label(str(p.get("date") or ""))
            rows[rid] = {
                "reqId": rid,
                "title": title,
                "company": "",
                "url": str(p.get("link") or ""),
                "externalPath": f"/{rid}",
                "locationsText": c,
                "postedOn": label,
                "postedOnIso": iso,
                "timeType": "",
                "bulletFields": [rid],
                "ats": "wpjobboard",
                "department": "; ".join(x for x in cat_names if x),
            }
        meta = {"rows": len(rows), "total": len(posts), "complete": True,
                "pages": 1, "client_filtered": dropped_country,
                "client_filtered_country": dropped_country,
                "client_filtered_time": 0}
        print(f"[{progress_label}] wpjobboard:{self.org}: {len(rows)} "
              f"rows of {len(posts)} listed ({dropped_country} "
              f"non-{country or '-'} dropped client-side)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").split("/")[-1]
        p = next((x for x in self._posts()
                  if str(x.get("id")) == rid), None)
        if p is None:
            return None
        locs = self._taxonomy("jobpost_location")
        loc_names = [locs.get(int(t), "") for t in
                     p.get("jobpost_location") or []]
        c = "; ".join(x for x in loc_names if x)
        is_us = any("united states" in x.lower()
                    for x in loc_names if x)
        return {
            "jobPostingInfo": {
                "title": re.sub(r"<[^>]+>", "",
                                str((p.get("title") or {})
                                    .get("rendered") or "")).strip(),
                "location": c,
                "additionalLocations": [],
                "jobDescription": str((p.get("content") or {})
                                      .get("rendered") or ""),
                "timeType": "",
                "startDate": str(p.get("date") or ""),
                "externalUrl": str(p.get("link") or ""),
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor": self._US if is_us else None},
            },
            "hiringOrganization": {"name": ""},
            "similarJobs": [],
        }


def _slugify(text: str) -> str:
    """Stable rid for static boards that have NO server-side ids —
    the dedupe key is the (location, title) string itself (re_D.md
    Greenland/Aden/Autel class: 'dedupe by title')."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:80] or "row"


def _ld_jobposting(html: str) -> dict:
    """First schema.org JobPosting JSON-LD block in a page (the re_D
    Mandarin-Oriental / WuXi-AppTec detail contract). Returns {} when
    absent — callers treat as detail_unreachable."""
    for block in re.findall(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>'
            r'(.*?)</script>', html or "", re.S):
        try:
            d = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(d, dict) and d.get("@type") == "JobPosting":
            return d
    return {}


# ── greenland (S23: Drupal 7 static careers page — re_D.md §1) ─────────────
class GreenlandAdapter:
    """spec org = host (greenlandusa.com). ONE static page; jobs are a
    field-collection (per-location title lists) with NO ids, dates,
    links or descriptions. rid = slug(location|title). The dead ATS
    iframe (hrdepartment.com NXDOMAIN) is never touched."""

    KIND = "greenland"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        if not org:            # custom: grammar — host baked
            org = "greenlandusa.com"
        self.org = org
        self.cfg = cfg

    def _page(self) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/careers", f"ats:greenland:{self.org}",
            self.cfg)

    def _rows(self) -> list:
        key = f"greenland:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        html = self._page()
        rows: list[dict] = []
        blocks = re.split(
            r'class="[^"]*field-collection-item-field-current-openings',
            html)
        for block in blocks[1:]:
            lm = re.search(
                r'field-name-field-career-location.*?field-item'
                r'[^>]*>([^<]+)<', block, re.S)
            loc = (lm.group(1) if lm else "").strip()
            # ALL titles live in ONE field-items container (re_D §1:
            # 4 field-item divs per block) — scope to it, then finditer
            cm = re.search(
                r'field-name-field-position-title(.*)', block, re.S)
            titles_area = cm.group(1) if cm else ""
            for tm in re.finditer(
                    r'field-item[^>]*>([^<]+)<', titles_area):
                title = unescape(tm.group(1)).strip()
                if not (loc and title):
                    continue
                rid = _slugify(f"{loc}|{title}")
                rows.append({"rid": rid, "title": title, "loc": loc})
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        for j in self._rows():
            # greenlandusa.com = the US arm; every opening is US-side
            if country and not workday.country_str_matches(
                    "United States", country):
                continue
            rows[j["rid"]] = {
                "reqId": j["rid"],
                "title": j["title"],
                "company": "Greenland USA",
                "url": f"https://{self.org}/careers",
                "externalPath": f"/{j['rid']}",
                "locationsText": f"{j['loc']}, US",
                "postedOn": "",
                "timeType": "",
                "bulletFields": [j["rid"]],
                "ats": "greenland",
            }
        meta = {"rows": len(rows), "total": len(rows), "complete": True,
                "pages": 1, "client_filtered": 0,
                "client_filtered_country": 0,
                "client_filtered_time": 0}
        print(f"[{progress_label}] greenland:{self.org}: {len(rows)} rows "
              "(static field-collection)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        # Honest static-page contract: no per-job pages exist — the
        # description field is empty by DESIGN (titles-only board).
        return {
            "jobPostingInfo": {
                "title": r["title"],
                "location": f"{r['loc']}, US",
                "additionalLocations": [],
                "jobDescription": "",
                "timeType": "",
                "startDate": "",
                "externalUrl": f"https://{self.org}/careers",
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor": "US"},
            },
            "hiringOrganization": {"name": "Greenland USA"},
            "similarJobs": [],
        }


# ── jereh (S23: JSP board + AJAX detail fragments — re_D.md §3) ────────────
class JerehAdapter:
    """spec org = host (www.jereh-nag.com). Listing = careers.jsp static
    rows (li[data-id]); detail = GET /ext/ajax_job.jsp?flag=jobList&
    jobId=<id> HTML fragment. ALSO scrapes the sibling surface
    americanjereh.com (1 static row) for full coverage."""

    KIND = "jereh"
    _US = "United States"

    _SECONDARY = "https://www.americanjereh.com/en/service/Careers.htm"

    def __init__(self, org: str, cfg: Config):
        if not org:            # custom: grammar — host baked
            org = "www.jereh-nag.com"
        self.org = org
        self.cfg = cfg

    def _list_page(self) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/careers/careers.jsp",
            f"ats:jereh:{self.org}", self.cfg)

    def _rows(self) -> list:
        key = f"jereh:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        html = self._list_page()
        rows: list[dict] = []
        # scope to the jobs ul (nav menu <li>s ALSO carry data-id!)
        sec = re.search(
            r'<ul class="full-row"[^>]*>(.*?)</ul>', html, re.S)
        job_html = sec.group(1) if sec else html
        for m in re.finditer(
                r'<li[^>]*data-id="(\d+)"[^>]*>(.*?)</li>', job_html,
                re.S):
            rid, body = m.group(1), m.group(2)
            tm = re.search(
                r'<div class="title"[^>]*>(.*?)</div>', body, re.S)
            title = unescape(
                re.sub(r"<[^>]+>", "", tm.group(1) if tm else "")
            ).strip()
            ps = re.findall(r'<p[^>]*>(.*?)</p>', body, re.S)
            ps = [unescape(re.sub(r"<[^>]+>", "", p)).strip()
                  for p in ps]
            loc = next((p[10:].strip() for p in ps
                        if p.startswith("Location:")), "")
            loc = re.sub(r"^Jereh [^,]*( and Technologies)?,?\s*", "",
                         loc).strip() or loc
            posted = next((p[len("Release time:"):].strip() for p in ps
                           if p.startswith("Release time:")), "")
            # country from the location tail (re_D: Houston→US,
            # Remote→US-entity remote, Canada→Canada)
            tail = loc.split(",")[-1].strip().lower()
            if "canada" in tail:
                cc = "Canada"
            elif "remote" in tail or "usa" in tail:
                cc = "United States"
            else:
                cc = "United States"   # Houston, Texas default (US site)
            if "remote" in tail and "canada" not in tail:
                loc = "Remote (US)"
            rows.append({"rid": rid, "title": title, "loc": loc,
                         "posted": posted, "cc": cc})
        # secondary surface: americanjereh.com single static row
        try:
            sec = _fetch_text_resilient(
                self._SECONDARY, f"ats:jereh:americanjereh", self.cfg)
            sec_text = unescape(
                re.sub(r"<[^>]+>", " ", sec)).strip()
            if "Marketing Specialist" in sec_text:
                rows.append({"rid": "amj-cyl001",
                             "title": "Marketing Specialist",
                             "loc": "Houston, Texas",
                             "posted": "", "cc": "United States"})
        except Exception:
            pass                     # secondary surface is best-effort
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped = 0
        for j in self._rows():
            if country and not workday.country_str_matches(j["cc"], country):
                dropped += 1
                continue
            label, iso = _posted_label(
                _month_year_iso(j["posted"]))
            rows[j["rid"]] = {
                "reqId": j["rid"],
                "title": j["title"],
                "company": "Jereh Group",
                "url": (f"https://{self.org}/ext/ajax_job.jsp?"
                        f"flag=jobList&jobId={j['rid']}"
                        if j["rid"].isdigit() else self._SECONDARY),
                "externalPath": f"/{j['rid']}",
                "locationsText": j["loc"],
                "postedOn": label,
                "postedOnIso": iso,
                "timeType": "",
                "bulletFields": [j["rid"]],
                "ats": "jereh",
            }
        meta = {"rows": len(rows), "total": len(rows) + dropped,
                "complete": True, "pages": 1,
                "client_filtered": dropped,
                "client_filtered_country": dropped,
                "client_filtered_time": 0}
        print(f"[{progress_label}] jereh:{self.org}: {len(rows)} rows "
              f"({dropped} non-{country or '-'} dropped client-side)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        desc = ""
        if rid.isdigit():
            html = _fetch_text_resilient(
                f"https://{self.org}/ext/ajax_job.jsp?flag=jobList"
                f"&jobId={rid}", f"ats:jereh:{self.org}:{rid}", self.cfg)
            text = unescape(
                re.sub(r"<[^>]+>", "\n", html or ""))
            text = re.sub(r"\n{2,}", "\n", text).strip()
            m = re.search(r"Job Description[：:]\s*(.+)$", text, re.S)
            if m:
                desc = m.group(1).strip()
        else:                       # the americanjereh static row
            try:
                sec = _fetch_text_resilient(
                    self._SECONDARY, f"ats:jereh:americanjereh",
                    self.cfg)
                body = re.search(
                    r'(Marketing Specialist.*?)(?:</div>|$)',
                    sec or "", re.S)
                if body:
                    desc = unescape(re.sub(
                        r"<[^>]+>", " ", body.group(1)))
                    desc = re.sub(r"\s{2,}", " ", desc).strip()
            except Exception:
                pass
        return {
            "jobPostingInfo": {
                "title": r["title"],
                "location": r["loc"],
                "additionalLocations": [],
                "jobDescription": desc,
                "timeType": "",
                "startDate": _month_year_iso(r["posted"]),
                "externalUrl": (f"https://{self.org}/careers/careers.jsp"),
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor": "US"},
            },
            "hiringOrganization": {"name": "Jereh Group"},
            "similarJobs": [],
        }


def _month_year_iso(text: str) -> str:
    """"July, 2025" / "July 2025" → "2025-07-01" (month-year granularity,
    re_D Jereh class). Empty for unparseable."""
    m = re.search(r"([A-Za-z]+)\s*,?\s*(\d{4})", text or "")
    if not m:
        return ""
    months = {m_[:3].lower(): i for i, m_ in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], 1)}
    mm = months.get(m.group(1)[:3].lower())
    if not mm:
        return ""
    return f"{int(m.group(2)):04d}-{mm:02d}-01"


# ── autelenergy (S23: Shopify PageFly accordion — re_D.md §5) ──────────────
class AutelEnergyAdapter:
    """spec org = host (autelenergy.us). Pure static PageFly accordion;
    jobs expand inline (title in Accordion.Header, full JD in
    Accordion.Content). No ids/dates/apply links — rid = slug(title)."""

    KIND = "autelenergy"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        if not org:            # custom: grammar — host baked
            org = "autelenergy.us"
        self.org = org
        self.cfg = cfg

    def _page(self) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/pages/careers",
            f"ats:autelenergy:{self.org}", self.cfg)

    def _rows(self) -> list:
        key = f"autelenergy:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        html = self._page()
        rows: list[dict] = []
        # accordion pairs in DOM order: header then content
        units = re.findall(
            r'data-pf-type="Accordion\.(?:Header|Content)"[^>]*>'
            r'(.*?)(?=data-pf-type="Accordion\.(?:Header|Content)"|'
            r'<script|\Z)', html, re.S)
        pend_title: Optional[str] = None
        for u in units:
            if pend_title is None:
                tm = re.search(r'<span[^>]*>(.*?)</span>', u, re.S)
                if tm:
                    pend_title = unescape(
                        re.sub(r"<[^>]+>", "", tm.group(1))).strip()
            else:
                body = unescape(
                    re.sub(r"<br\s*/?>", "\n", u))
                body = re.sub(r"<[^>]+>", " ", body)
                body = re.sub(r"[ \t]{2,}", " ", body).strip()
                lines = [x.strip() for x in body.split("\n")
                         if x.strip()]
                loc = next((ln for ln in lines
                            if re.search(r"–\s*United States", ln)), "")
                text = "\n".join(lines)
                rid = _slugify(pend_title)
                rows.append({"rid": rid, "title": pend_title,
                             "loc": loc or "North Carolina – United States",
                             "desc": text})
                pend_title = None
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        for j in self._rows():
            if country and not workday.country_str_matches(
                    "United States", country):
                continue
            rows[j["rid"]] = {
                "reqId": j["rid"],
                "title": j["title"],
                "company": "Autel Energy",
                "url": f"https://{self.org}/pages/careers",
                "externalPath": f"/{j['rid']}",
                "locationsText": j["loc"],
                "postedOn": "",
                "timeType": "",
                "bulletFields": [j["rid"]],
                "ats": "autelenergy",
            }
        meta = {"rows": len(rows), "total": len(rows), "complete": True,
                "pages": 1, "client_filtered": 0,
                "client_filtered_country": 0,
                "client_filtered_time": 0}
        print(f"[{progress_label}] autelenergy:{self.org}: {len(rows)} "
              "rows (static PageFly accordion)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        return {
            "jobPostingInfo": {
                "title": r["title"],
                "location": r["loc"],
                "additionalLocations": [],
                "jobDescription": r["desc"],
                "timeType": "",
                "startDate": "",
                "externalUrl": f"https://{self.org}/pages/careers",
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor": "US"},
            },
            "hiringOrganization": {"name": "Autel Energy"},
            "similarJobs": [],
        }


# ── aden (S23: WP hand-coded accordion — re_D.md §6) ───────────────────────
class AdenAdapter:
    """spec org = host (adengroup.com). FAQ-style accordion; the question
    string carries title+company+location ("Project Manager (PMO) -
    Akila, Remote, USA"); the answer div holds the full JD. rid =
    slug(question). US rule: USA / 'North American' → US."""

    KIND = "aden"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        if not org:            # custom: grammar — host baked
            org = "adengroup.com"
        self.org = org
        self.cfg = cfg

    def _page(self) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/career/", f"ats:aden:{self.org}",
            self.cfg)

    def _rows(self) -> list:
        key = f"aden:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        html = self._page()
        rows: list[dict] = []
        heads = re.findall(
            r'<div class="question-position"[^>]*>.*?'
            r'<p class=["\']?question-faq["\']?[^>]*>(.*?)</p>',
            html, re.S)
        answers = re.findall(
            r'<div[^>]*id="answer-(\d+)"[^>]*>(.*?)'
            r'(?:</div>\s*(?=<div class="question-position")|\Z)',
            html, re.S)
        # align by DOM order (answer N ↔ Nth question, re_D §6)
        for i, h in enumerate(heads):
            q = unescape(re.sub(r"<[^>]+>", "", h)).strip()
            body = next((a[1] for a in answers
                         if int(a[0]) == i), "")
            desc = unescape(re.sub(r"<[^>]+>", "\n", body))
            desc = re.sub(r"\n{2,}", "\n", desc).strip()
            cc = "United States" if re.search(
                r"\b(USA|U\.S\.A|North American)\b", q) else ""
            if re.search(r"\b(China|Vietnam|Shanghai|Wuxi|Hanoi)\b", q):
                cc = ""
            rows.append({"rid": _slugify(q), "title": q, "cc": cc,
                         "desc": desc})
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped = 0
        for j in self._rows():
            if country and not (j["cc"] and workday.country_str_matches(
                    j["cc"], country)):
                dropped += 1
                continue
            rows[j["rid"]] = {
                "reqId": j["rid"],
                "title": j["title"],
                "company": "Aden Group",
                "url": f"https://{self.org}/career/",
                "externalPath": f"/{j['rid']}",
                "locationsText": _aden_loc(j["title"]),
                "postedOn": "",
                "timeType": "",
                "bulletFields": [j["rid"]],
                "ats": "aden",
            }
        meta = {"rows": len(rows), "total": len(rows) + dropped,
                "complete": True, "pages": 1,
                "client_filtered": dropped,
                "client_filtered_country": dropped,
                "client_filtered_time": 0}
        print(f"[{progress_label}] aden:{self.org}: {len(rows)} rows "
              f"({dropped} non-{country or '-'} dropped client-side)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        return {
            "jobPostingInfo": {
                "title": r["title"],
                "location": _aden_loc(r["title"]),
                "additionalLocations": [],
                "jobDescription": r["desc"],
                "timeType": "",
                "startDate": "",
                "externalUrl": f"https://{self.org}/career/",
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor":
                            "US" if r["cc"] else None},
            },
            "hiringOrganization": {"name": "Aden Group"},
            "similarJobs": [],
        }


def _aden_loc(title_q: str) -> str:
    """Best-effort location from the Aden question string (the whole
    string IS the title — keep it, and surface the tail as location)."""
    m = re.search(r",\s*([A-Za-z .]+)$", title_q)
    return m.group(1).strip() if m else ""


# ── mandarinoriental (S23: Rails careers platform — re_D.md §7) ────────────
class MandarinOrientalAdapter:
    """spec org = host (careers.mandarinoriental.com). NO JSON list API;
    listing = GET /jobs/search?cities[]=<US city> ×4 (the dropdown's US
    set is exactly {Beverly Hills, Boston, Miami, New York} — 4 requests
    cover the whole US board); detail = /jobs/<slug> JSON-LD JobPosting.
    Two req-id formats (JR-xxxxx + plain integers). robots asks
    Crawl-delay 5 — the shared fetch path sleeps politely already; the
    4+29 requests per full chain stay well under budget."""

    KIND = "mandarinoriental"
    _US = "United States"
    _US_CITIES = ["New York", "Boston", "Miami", "Beverly Hills"]

    def __init__(self, org: str, cfg: Config):
        if not org:            # custom: grammar — host baked
            org = "careers.mandarinoriental.com"
        self.org = org
        self.cfg = cfg

    def _search_page(self, city: str) -> str:
        from urllib.parse import quote
        return _fetch_text_resilient(
            f"https://{self.org}/jobs/search?cities%5B%5D="
            f"{quote(city)}", f"ats:mo:{self.org}:{city}", self.cfg)

    def _rows(self) -> list:
        key = f"mandarinoriental:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        rows: list[dict] = []
        seen: set[str] = set()
        for i, city in enumerate(self._US_CITIES):
            if i:                      # robots Crawl-delay 5 — be polite
                time.sleep(1.2)        # (4 pages ≈ 4s, well under budget)
            html = self._search_page(city)
            # SILENT-EMPTY guard (re_D §7: the platform serves an
            # empty 202 anti-bot shell when throttled — raise_for_status
            # PASSES on 202). A real results page always carries the
            # "Displaying" marker; refuse to claim complete=0 rows.
            if "Displaying" not in (html or ""):
                raise RuntimeError(
                    f"mandarinoriental:{self.org}: {city} filter served "
                    "a challenge/empty shell (no 'Displaying' marker) — "
                    "board throttled or shape changed; refusing to "
                    "report 0 rows as complete")
            for m in re.finditer(
                    r'<article[^>]*job-search-results-card-col[^>]*>'
                    r'(.*?)</article>', html or "", re.S):
                card = m.group(1)
                rm = re.search(
                    r'job-component-requisition-identifier[^>]*>'
                    r'(?:\s*<[^>]+>)*\s*([^<]+)<', card)
                req = (rm.group(1) if rm else "").strip()
                tm = re.search(
                    r'<h3[^>]*job-search-results-card-title[^>]*>\s*'
                    r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', card, re.S)
                if not tm:
                    continue
                href, title = tm.group(1), unescape(
                    re.sub(r"<[^>]+>", "", tm.group(2))).strip()
                hm = re.search(
                    r'job-component-dropdown-field-2[^>]*>'
                    r'(?:\s*<[^>]+>)*\s*([^<]+)<', card)
                hotel = (hm.group(1) if hm else "").strip()
                em = re.search(
                    r'job-component-employment-type[^>]*>'
                    r'(?:\s*<[^>]+>)*\s*([^<]+)<', card)
                tt = (em.group(1) if em else "").strip()
                if not req or req in seen:
                    continue
                seen.add(req)
                url = href if href.startswith("http") \
                    else f"https://{self.org}{href}"
                rows.append({"rid": req, "title": title, "city": city,
                             "hotel": hotel, "tt": tt, "url": url})
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        n_filtered = 0
        for j in self._rows():
            if time_type and j["tt"] and j["tt"].lower() != \
                    time_type.lower():
                n_filtered += 1
                continue
            rows[j["rid"]] = {
                "reqId": j["rid"],
                "title": j["title"],
                "company": "Mandarin Oriental",
                "url": j["url"],
                "externalPath": f"/{j['rid']}",
                "locationsText": j["city"],
                "postedOn": "",
                "timeType": j["tt"],
                "bulletFields": [j["rid"]],
                "ats": "mandarinoriental",
            }
        meta = {"rows": len(rows), "total": len(rows) + n_filtered,
                "complete": True, "pages": len(self._US_CITIES),
                "client_filtered": n_filtered,
                "client_filtered_country": 0,
                "client_filtered_time": n_filtered}
        print(f"[{progress_label}] mandarinoriental:{self.org}: "
              f"{len(rows)} US rows via 4 city filters "
              f"({n_filtered} time-filtered)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        # per-row pacing: the platform's anti-bot escalates on bursts
        # (202 shells); the details phase's default sleep is too fast
        # for this board. ~0.9s/row ≈ 27s for the full 30-row board.
        time.sleep(0.9)
        html = _fetch_text_resilient(r["url"],
                                  f"ats:mo:{self.org}:{rid}", self.cfg)
        info = _ld_jobposting(html)
        if not info:
            return None
        et = info.get("employmentType")
        tt = {"FULL_TIME": "Full time", "PART_TIME": "Part time",
              "CONTRACTOR": "Contract"}.get(str(et).upper(), r["tt"])
        locs = []
        for jl in ([info.get("jobLocation")]
                   if isinstance(info.get("jobLocation"), dict)
                   else (info.get("jobLocation") or [])):
            a = ((jl or {}).get("address") or {})
            locs.append(", ".join(x for x in (
                str(a.get("addressLocality") or ""),
                str(a.get("addressRegion") or "")) if x))
        addr = (((info.get("jobLocation") or {})
                 .get("address") or {})
                if isinstance(info.get("jobLocation"), dict) else {})
        return {
            "jobPostingInfo": {
                "title": str(info.get("title") or r["title"]),
                "location": locs[0] if locs else r["city"],
                "additionalLocations": locs[1:],
                "jobDescription": str(info.get("description") or ""),
                "timeType": tt,
                "startDate": str(info.get("datePosted") or ""),
                "externalUrl": r["url"],
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor":
                            str(addr.get("addressCountry") or "US")},
            },
            "hiringOrganization": {
                "name": str(((info.get("hiringOrganization") or {})
                             .get("name")) or "Mandarin Oriental")},
            "similarJobs": [],
        }


# ── wuxiapptec (S23: Django mirror of the iCIMS board — re_D.md §8) ────────
class WuXiAppTecAdapter:
    """spec org = host (www.wuxiappteccareers.com). The mirror is the
    ONLY scrape-friendly surface (iCIMS itself serves a JS shell).
    Listing = GET /jobs/us/?page_jobs=N (20/page, 'Found N' total);
    rid = the iCIMS job id (dd after 'Job ID:'); detail = the mirror
    job page's JSON-LD. employmentType is always 'OTHER' — the Job Type
    dd (Remote/On-site/Hybrid) is the workplace type, mapped into
    locationsText."""

    KIND = "wuxiapptec"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        if not org:            # custom: grammar — host baked
            org = "www.wuxiappteccareers.com"
        self.org = org
        self.cfg = cfg

    def _page(self, n: int) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/jobs/us/?page_jobs={n}",
            f"ats:wuxiapptec:{self.org}:p{n}", self.cfg)

    def _rows(self) -> list:
        key = f"wuxiapptec:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        rows: list[dict] = []
        seen: set[str] = set()
        for n in range(1, 6):          # guard: 5 pages max (100 rows)
            html = self._page(n)
            if not html:
                break
            fm = re.search(r'Found\s*<strong>(\d+)</strong>', html)
            total = int(fm.group(1)) if fm else 0
            # the list = ul#job-list-section > li blocks (anchor + h3 +
            # a <dl> of Job ID/Category/Location/Job Type divs — the dl
            # sits BETWEEN </a> and </li>, so split li blocks, not tags)
            sec = re.search(
                r'id="job-list-section"[^>]*>(.*?)</ul>', html, re.S)
            blocks = re.split(r"<li>", sec.group(1))[1:] if sec else []
            got = 0
            for b in blocks:
                hm = re.search(r'<a href="(/job/[^"]+)"', b)
                if not hm:
                    continue
                href = hm.group(1)
                cm = re.search(r'/job/(\d+)/([^/]+)/?', href)
                site_id = cm.group(1) if cm else ""
                tm = re.search(r'<h3[^>]*>(.*?)</h3>', b, re.S)
                title = unescape(re.sub(
                    r"<[^>]+>", "", tm.group(1) if tm else "")).strip()
                jm = re.search(
                    r'<dt>Job ID:</dt>\s*<dd>([^<]+)<', b)
                job_id = (jm.group(1) if jm else "").strip()
                lm = re.search(
                    r'<dt>Location:</dt>\s*<dd>([^<]+)<', b)
                loc = (lm.group(1) if lm else "").strip()
                wm = re.search(
                    r'<dt>Job Type:</dt>\s*<dd>([^<]+)<', b)
                wtype = (wm.group(1) if wm else "").strip()
                rid = job_id or (f"site{site_id}" if site_id else "")
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                rows.append({
                    "rid": rid, "site_id": site_id, "title": title,
                    "loc": loc or "United States", "wtype": wtype,
                    "url": f"https://{self.org}{href}"})
                got += 1
            if not got or (total and len(rows) >= total) or not fm:
                break
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        for j in self._rows():
            # /jobs/us/ is the US-filtered surface — every row is US
            rows[j["rid"]] = {
                "reqId": j["rid"],
                "title": j["title"],
                "company": "WuXi AppTec",
                "url": j["url"],
                "externalPath": f"/{j['rid']}",
                "locationsText":
                    f"{j['loc']} ({j['wtype']})" if j["wtype"] else j["loc"],
                "postedOn": "",
                "timeType": "",
                "bulletFields": [j["rid"]],
                "ats": "wuxiapptec",
            }
        meta = {"rows": len(rows), "total": len(rows), "complete": True,
                "pages": 1, "client_filtered": 0,
                "client_filtered_country": 0,
                "client_filtered_time": 0}
        print(f"[{progress_label}] wuxiapptec:{self.org}: {len(rows)} "
              "US rows (mirror of the iCIMS board)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        html = _fetch_text_resilient(r["url"],
                                  f"ats:wuxiapptec:{self.org}:{rid}",
                                  self.cfg)
        info = _ld_jobposting(html)
        if not info:
            return None
        jl = info.get("jobLocation")
        addr = ((jl or {}).get("address") or {}) \
            if isinstance(jl, dict) \
            else (((jl or [{}])[0] or {}).get("address") or {})
        loc = ", ".join(x for x in (
            str(addr.get("addressLocality") or ""),
            str(addr.get("addressRegion") or "")) if x) or r["loc"]
        return {
            "jobPostingInfo": {
                "title": str(info.get("title") or r["title"]),
                "location": loc,
                "additionalLocations": [],
                "jobDescription": str(info.get("description") or ""),
                "timeType": "",     # always 'OTHER' on this board
                "startDate": str(info.get("datePosted") or ""),
                "externalUrl": r["url"],
                "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor":
                            str(addr.get("addressCountry") or "US")},
            },
            "hiringOrganization": {
                "name": str(((info.get("hiringOrganization") or {})
                             .get("name")) or "WuXi AppTec")},
            "similarJobs": [],
        }


# ── blacksesame (S23: PbootCMS SSR cards — s23_ai_census/re_E.md §1) ───────
class BlackSesameAdapter:
    """spec org = host (www.blacksesame.com). One SSR page; job-card
    divs with data-department; detail = /en/join-us/<id>.html (numeric
    PbootCMS ids, unordered). div.job-type holds a DATE (misnamed).
    The Tencent WAF serves plain fetches; the resilient fallback
    covers any future escalation."""

    KIND = "blacksesame"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        if not org:
            org = "www.blacksesame.com"   # custom: grammar — host baked
        self.org = org
        self.cfg = cfg

    def _page(self) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/en/join-us/", f"ats:bs:{self.org}",
            self.cfg)

    def _rows(self) -> list:
        key = f"blacksesame:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        html = self._page()
        rows: list[dict] = []
        for m in re.finditer(
                r'<div class="job-card"[^>]*data-department="([^"]*)"'
                r'(.*?)</div>\s*(?=<div class="job-card|</div>\s*</div>'
                r'\s*</div>)', html, re.S):
            dept, body = m.group(1), m.group(2)
            tm = re.search(r'<h3 class="job-title"[^>]*>(.*?)</h3>',
                           body, re.S)
            lm = re.search(r'<div class="job-location"[^>]*>(.*?)</div>',
                           body, re.S)
            dm = re.search(
                r'<a[^>]*class="apply-btn"[^>]*href="([^"]+)"', body)
            dym = re.search(r'<div class="job-type"[^>]*>(.*?)</div>',
                            body, re.S)
            title = unescape(re.sub(r"<[^>]+>", "",
                                    tm.group(1) if tm else "")).strip()
            loc = unescape(re.sub(r"<[^>]+>", "",
                                  lm.group(1) if lm else "")).strip()
            href = dm.group(1) if dm else ""
            im = re.search(r"/(\d+)\.html", href)
            rid = im.group(1) if im else _slugify(title)
            posted = unescape(re.sub(r"<[^>]+>", "", dym.group(1)
                                     if dym else "")).strip()
            if not title:
                continue
            rows.append({
                "rid": rid, "title": title, "loc": loc, "dept": dept,
                "posted": posted if re.match(r"\d{4}-\d", posted) else "",
                "url": (f"https://{self.org}{href}" if href.startswith("/")
                        else href) or f"https://{self.org}/en/join-us/",
            })
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped = 0
        for j in self._rows():
            is_us = bool(re.search(r",\s*US\s*$", j["loc"]))
            if country and not is_us:
                dropped += 1
                continue
            label, iso = _posted_label(j["posted"] or "")
            rows[j["rid"]] = {
                "reqId": j["rid"], "title": j["title"],
                "company": "Black Sesame Technologies",
                "url": j["url"], "externalPath": f"/{j['rid']}",
                "locationsText": j["loc"], "postedOn": label,
                "postedOnIso": iso, "timeType": "",
                "bulletFields": [j["rid"]], "ats": "blacksesame",
            }
        meta = {"rows": len(rows), "total": len(rows) + dropped,
                "complete": True, "pages": 1,
                "client_filtered": dropped,
                "client_filtered_country": dropped,
                "client_filtered_time": 0}
        print(f"[{progress_label}] blacksesame:{self.org}: {len(rows)} "
              f"rows ({dropped} non-{country or '-'} dropped)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        url = (f"https://{self.org}/en/join-us/{rid}.html"
               if rid.isdigit() else r["url"])
        html = _fetch_text_resilient(url, f"ats:bs:{self.org}:{rid}",
                                     self.cfg)
        desc = ""
        secs = re.findall(
            r'<div class="job-section"[^>]*>(.*?)</div>\s*</div>\s*'
            r'(?:<div class="job-section"|</div>)', html or "", re.S)
        for s_ in secs:
            dmm = re.search(r'<div class="job-description"[^>]*>'
                            r'(.*?)</div>', s_, re.S)
            if dmm:
                txt = unescape(re.sub(r"<[^>]+>", "\n", dmm.group(1)))
                desc += re.sub(r"\n{2,}", "\n", txt).strip() + "\n\n"
        if not desc:                     # fallback: the list snippet
            desc = ""
        return {
            "jobPostingInfo": {
                "title": r["title"], "location": r["loc"],
                "additionalLocations": [],
                "jobDescription": desc.strip(),
                "timeType": "", "startDate": r["posted"],
                "externalUrl": url, "jobReqId": rid, "postedOn": "",
                "country": {"descriptor":
                            "US" if re.search(r",\s*US\s*$", r["loc"])
                            else None},
            },
            "hiringOrganization": {
                "name": "Black Sesame Technologies"},
            "similarJobs": [],
        }


# ── ecovacsus (S23: Astro shell + Next.js data island — re_E.md §2) ────────
class EcovacsUSAdapter:
    """spec org = host (www.ecovacs.com). The /us/careers board is a
    Next SSR island inside an Astro shell: buildId is re-read from the
    HTML each run (drifts on redeploy), then
    /_next/data/<bid>/us/careers/job-list.json (+ ?page=N) lists and
    job-detail.json?id=<id> details. Detail JSON has a literal "type;"
    key (backend typo)."""

    KIND = "ecovacsus"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        if not org:
            org = "www.ecovacs.com"      # custom: grammar — host baked
        self.org = org
        self.cfg = cfg

    def _build_id(self) -> str:
        html = _fetch_text_resilient(
            f"https://{self.org}/us/careers/job-list",
            f"ats:ecovacs:{self.org}:html", self.cfg)
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)'
                      r'</script>', html or "", re.S)
        if not m:
            raise RuntimeError(
                f"ecovacsus:{self.org}: no __NEXT_DATA__ island — board "
                "shape changed; refusing")
        return str(json.loads(m.group(1)).get("buildId") or "")

    def _rows(self) -> list:
        key = f"ecovacsus:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        bid = self._build_id()
        rows: list[dict] = []
        page = 1
        while page < 10:
            url = (f"https://{self.org}/_next/data/{bid}/us/careers/"
                   f"job-list.json" + (f"?page={page}" if page > 1 else ""))
            d = fetch_json(url, cfg=self.cfg)
            # _next/data JSON root: pageProps DIRECTLY (no "props"
            # wrapper — re_E.md's path had a subtle error; live-verified)
            pd_ = ((d or {}).get("pageProps")
                   or (d or {}).get("props", {}).get("pageProps")
                   or {}).get("pageData", {})
            lst = pd_.get("list") or []
            for r in lst or []:
                rid = str(r.get("id") or "")
                if not rid:
                    continue
                rows.append({
                    "rid": rid,
                    "title": str(r.get("title") or "").strip(),
                    "tt": str(r.get("type") or "").strip(),
                    "loc": str(r.get("work_place") or "").strip(),
                    "bid": bid,
                })
            total_page = int(pd_.get("page_data", {}).get(
                "total_page", 1) or 1)
            if page >= total_page or not lst:
                break
            page += 1
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped = 0
        for j in self._rows():
            if country and not _plain_loc_in_country(
                    j["loc"], country):
                dropped += 1
                continue
            rows[j["rid"]] = {
                "reqId": j["rid"], "title": j["title"],
                "company": "Ecovacs Robotics",
                "url": (f"https://{self.org}/us/careers/job-detail"
                        f"?id={j['rid']}"),
                "externalPath": f"/{j['rid']}",
                "locationsText": j["loc"], "postedOn": "",
                "timeType": j["tt"], "bulletFields": [j["rid"]],
                "ats": "ecovacsus",
            }
        meta = {"rows": len(rows), "total": len(rows) + dropped,
                "complete": True, "pages": 1,
                "client_filtered": dropped,
                "client_filtered_country": dropped,
                "client_filtered_time": 0}
        print(f"[{progress_label}] ecovacsus:{self.org}: {len(rows)} "
              f"rows ({dropped} non-{country or '-'} dropped)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        bid = r["bid"]
        d = fetch_json(
            f"https://{self.org}/_next/data/{bid}/us/careers/"
            f"job-detail.json?id={rid}", cfg=self.cfg)
        career = (((d or {}).get("pageProps")
                   or (d or {}).get("props", {}).get("pageProps")
                   or {}).get("pageData", {})).get("career") or {}
        # NB: detail JSON uses "type;" (trailing semicolon) — backend
        # typo documented in re_E.md §2
        tt = str(career.get("type;") or career.get("type")
                 or r["tt"]).strip()
        parts = []
        for f in ("description", "responsibility",
                  "mini_qualification", "preferred_qualification"):
            v = career.get(f)
            if v:
                txt = unescape(re.sub(r"<[^>]+>", "\n", str(v)))
                txt = txt.replace("\xa0", " ")
                parts.append(re.sub(r"\n{2,}", "\n", txt).strip())
        desc = "\n\n".join(x for x in parts if x)
        loc = str(career.get("work_place") or r["loc"]).strip()
        return {
            "jobPostingInfo": {
                "title": str(career.get("title") or r["title"]).strip(),
                "location": loc, "additionalLocations": [],
                "jobDescription": desc, "timeType": tt,
                "startDate": "",
                "externalUrl": (f"https://{self.org}/us/careers/"
                                f"job-detail?id={rid}"),
                "jobReqId": rid, "postedOn": "",
                "country": {"descriptor":
                            "US" if _plain_loc_in_country(
                                loc, "United States") else None},
            },
            "hiringOrganization": {"name": "Ecovacs Robotics"},
            "similarJobs": [],
        }


# ── accutar (S23: WP page accordion, no REST CPT — re_E.md §3) ─────────────
class AccutarAdapter:
    """spec org = host (www.accutarbio.com). Jobs are PAGE CONTENT (WP
    blocks), not a CPT — no REST feed. Parse the mobile list for
    ids+links and the desktop accordion for full descriptions
    (index-aligned by data-category+data-index); locations derived
    from 3-address regexes (Cranbury/Mountain View/Bellevue)."""

    KIND = "accutar"
    _US = "United States"
    _LOCS = [
        (r"Cranbury[, ]+(?:New Jersey|NJ)", "Cranbury, NJ"),
        (r"Mountain View,? CA", "Mountain View, CA"),
        (r"Bellevue,? WA", "Bellevue, WA"),
    ]

    def __init__(self, org: str, cfg: Config):
        if not org:
            org = "www.accutarbio.com"   # custom: grammar — host baked
        self.org = org
        self.cfg = cfg

    def _page(self) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/careers/", f"ats:accutar:{self.org}",
            self.cfg)

    def _rows(self) -> list:
        key = f"accutar:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        html = self._page()
        rows: list[dict] = []
        # 1) mobile list: title -> career-detail id (mixed-quote attrs!)
        rid_by_title: dict[str, str] = {}
        for m in re.finditer(
                r"<div class=\"position-title\"[^>]*data-category=['\"]"
                r"([^'\"]*)['\"][^>]*data-index=['\"](\d+)['\"][^>]*>"
                r"\s*<a[^>]*href=\"[^\"]*career-detail\??id=(\d+)\"[^>]*>"
                r"(.*?)</a>", html, re.S):
            _cat, _idx, rid, title = m.groups()
            t = unescape(re.sub(r"<[^>]+>", "", title)).strip()
            if t:
                rid_by_title[t] = rid
        # 2) desktop cards: title + description TOGETHER in one card
        # (button.position-title + #collapse-<cat>-<n> body)
        for seg in re.split(r'<div class="card position ', html)[1:]:
            tm = re.search(
                r'<button[^>]*position-title[^>]*>(.*?)</button>',
                seg, re.S)
            title = unescape(re.sub(r"<[^>]+>", "",
                                    tm.group(1) if tm else "")).strip()
            if not title:
                continue
            dm = re.search(
                r'<div class="card-body position-requirement"[^>]*>'
                r'(.*?)</div>\s*</div>\s*</div>', seg, re.S)
            desc = ""
            if dm:
                d_ = unescape(re.sub(r"<!--.*?-->", "", dm.group(1)))
                desc = re.sub(r"<[^>]+>", "\n", d_)
                desc = re.sub(r"\n{2,}", "\n", desc).strip()
                desc = desc.replace("\xa0", " ")
            loc = ""
            # addresses line-wrap ("Bellevue,\nWA") — flatten first
            flat = re.sub(r"\s+", " ", desc or "")
            for pat, lbl in self._LOCS:
                if re.search(pat, flat):
                    loc = lbl
                    break
            rid = rid_by_title.get(title) or _slugify(title)
            rows.append({"rid": rid, "title": title,
                         "cat": "bio" if " bio" in seg[:60] else "com",
                         "desc": desc, "loc": loc})
        # 3) mobile-only rows (ids with no desktop twin) stay honest:
        # skipped — desktop carries the full text
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped = 0
        for j in self._rows():
            if country and not (j["loc"] and _plain_loc_in_country(
                    j["loc"], country)):
                dropped += 1
                continue
            rows[j["rid"]] = {
                "reqId": j["rid"], "title": j["title"],
                "company": "Accutar Biotech",
                "url": f"https://{self.org}/career-detail/?id={j['rid']}",
                "externalPath": f"/{j['rid']}",
                "locationsText": j["loc"] or "US (see description)",
                "postedOn": "", "timeType": "",
                "bulletFields": [j["rid"]], "ats": "accutar",
            }
        meta = {"rows": len(rows), "total": len(rows) + dropped,
                "complete": True, "pages": 1,
                "client_filtered": dropped,
                "client_filtered_country": dropped,
                "client_filtered_time": 0}
        print(f"[{progress_label}] accutar:{self.org}: {len(rows)} "
              f"rows ({dropped} non-{country or '-'} dropped)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        return {
            "jobPostingInfo": {
                "title": r["title"], "location": r["loc"],
                "additionalLocations": [],
                "jobDescription": r["desc"], "timeType": "",
                "startDate": "",
                "externalUrl": (f"https://{self.org}/career-detail/"
                                f"?id={rid}"),
                "jobReqId": rid, "postedOn": "",
                "country": {"descriptor":
                            "US" if r["loc"] else None},
            },
            "hiringOrganization": {"name": "Accutar Biotech"},
            "similarJobs": [],
        }


# ── hitgen (S23: ThinkPHP SSR + ?location=US filter — re_E.md §4) ──────────
class HitGenAdapter:
    """spec org = host (www.hitgen.com). No JSON; li[data-id] rows;
    detail popups at /en/careers-position-popup-<id>.html. US rule:
    rows under ?location=US (NEVER ?location=USA — dirty DB tagging,
    maps to a China row!) cross-checked against "Base in China"
    full-width-paren titles."""

    KIND = "hitgen"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        if not org:
            org = "www.hitgen.com"       # custom: grammar — host baked
        self.org = org
        self.cfg = cfg

    def _us_page(self) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/en/careers-position.html?location=US",
            f"ats:hitgen:{self.org}:us", self.cfg)

    def _all_page(self) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/en/careers-position.html",
            f"ats:hitgen:{self.org}:all", self.cfg)

    def _rows(self) -> list:
        key = f"hitgen:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        us_html = self._us_page()
        all_html = self._all_page()

        def _ids(html: str) -> set:
            return set(re.findall(
                r'<li[^>]*data-id="(\d+)"[^>]*>', html or ""))
        us_ids = _ids(us_html)
        rows: list[dict] = []
        for m in re.finditer(
                r'<li[^>]*data-id="(\d+)"[^>]*>(.*?)</li>', all_html,
                re.S):
            rid, body = m.group(1), m.group(2)
            tm = re.search(r'<div class="results-txt"[^>]*>(.*?)'
                           r'</div>', body, re.S)
            title = unescape(re.sub(r"<[^>]+>", "",
                                    tm.group(1) if tm else "")).strip()
            if not title:
                continue
            # the US filter result + the Base-in-China cross-check
            is_us = rid in us_ids and not re.search(
                r"Base in China", title)
            rows.append({"rid": rid, "title": title, "us": is_us})
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped = 0
        for j in self._rows():
            if country and not j["us"]:
                dropped += 1
                continue
            rows[j["rid"]] = {
                "reqId": j["rid"], "title": j["title"],
                "company": "HitGen",
                "url": (f"https://{self.org}/en/careers-position-"
                        f"popup-{j['rid']}.html"),
                "externalPath": f"/{j['rid']}",
                "locationsText": "US" if j["us"] else "China",
                "postedOn": "", "timeType": "",
                "bulletFields": [j["rid"]], "ats": "hitgen",
            }
        meta = {"rows": len(rows), "total": len(rows) + dropped,
                "complete": True, "pages": 1,
                "client_filtered": dropped,
                "client_filtered_country": dropped,
                "client_filtered_time": 0}
        print(f"[{progress_label}] hitgen:{self.org}: {len(rows)} rows "
              f"({dropped} non-{country or '-'} dropped; "
              "location=US filter)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        html = _fetch_text_resilient(
            f"https://{self.org}/en/careers-position-popup-{rid}.html",
            f"ats:hitgen:{self.org}:{rid}", self.cfg)
        desc = ""
        dm = re.search(r'<div class="popup-dt"[^>]*>(.*?)</div>',
                       html or "", re.S)
        if dm:
            d_ = unescape(re.sub(r"<[^>]+>", "\n", dm.group(1)))
            desc = re.sub(r"\n{2,}", "\n", d_).strip()
        return {
            "jobPostingInfo": {
                "title": r["title"],
                "location": "US" if r["us"] else "China",
                "additionalLocations": [],
                "jobDescription": desc, "timeType": "",
                "startDate": "",
                "externalUrl": (f"https://{self.org}/en/"
                                f"careers-position.html?location=US"),
                "jobReqId": rid, "postedOn": "",
                "country": {"descriptor": "US" if r["us"] else None},
            },
            "hiringOrganization": {"name": "HitGen"},
            "similarJobs": [],
        }


# ── insilico (S23: Tilda SSR accordion — re_E.md §5) ───────────────────────
class InsilicoAdapter:
    """spec org = host (insilico.com). Tilda t849 accordion, fully
    server-rendered; scoped to the rec block (marketing noise
    elsewhere). US eligibility = the remote-global row (the mission's
    remote-friendly class); UAE rows drop under a US country filter."""

    KIND = "insilico"
    _US = "United States"
    _REC = "rec620788308"

    def __init__(self, org: str, cfg: Config):
        if not org:
            org = "insilico.com"         # custom: grammar — host baked
        self.org = org
        self.cfg = cfg

    def _page(self) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/careers", f"ats:insilico:{self.org}",
            self.cfg)

    def _rows(self) -> list:
        key = f"insilico:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        html = self._page()
        m = re.search(r'id="' + self._REC + r'"(.*?)<!--/ record -->'
                      r'|id="' + self._REC + r'"(.*)', html or "", re.S)
        scope = (m.group(1) or m.group(2)) if m else (html or "")
        rows: list[dict] = []
        for hm in re.finditer(
                r'<div class="t849__header\s*"[^>]*>(.*?)</div>\s*'
                r'<div class="t849__content"[^>]*>(.*?)(?=<div '
                r'class="t849__wrapper"|$)', scope, re.S):
            head, body = hm.group(1), hm.group(2)
            tm2 = re.search(
                r'<span class="t849__title[^"]*"[^>]*>(.*?)</span>',
                head, re.S)
            title = unescape(re.sub(
                r"<[^>]+>", "", tm2.group(1) if tm2 else head)).strip()
            title = re.sub(r"\s+", " ", title)
            dm = re.search(
                r'<div class="t849__text [^"]*"[^>]*>(.*?)'
                r'(?=</div>\s*</div>\s*<div|</div>\s*</div>\s*$)'
                r'|<div class="t849__text"[^>]*>(.*?)'
                r'(?=</div>\s*</div>\s*<div|</div>\s*$)',
                body, re.S)
            desc_raw = (dm.group(1) or dm.group(2)) if dm else ""
            desc = ""
            if dm:
                d_ = unescape(re.sub(r"<[^>]+>", "\n", desc_raw))
                desc = re.sub(r"\n{2,}", "\n", d_
                              ).strip().replace("\xa0", " ")
            pm = re.search(r"Place of work[:\s]*(.*?)(?:\n|\.|$)",
                           desc or "")
            place = (pm.group(1).strip() if pm else "")
            rid = _slugify(title)
            rows.append({"rid": rid, "title": title, "desc": desc,
                         "place": place})
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped = 0
        for j in self._rows():
            # US eligibility: remote-global rows (remote-friendly
            # mission class); explicit non-US places drop
            remote_global = re.search(
                r"remote,? open globally", j["place"], re.I)
            if country and not remote_global:
                dropped += 1
                continue
            rows[j["rid"]] = {
                "reqId": j["rid"], "title": j["title"],
                "company": "Insilico Medicine",
                "url": f"https://{self.org}/careers",
                "externalPath": f"/{j['rid']}",
                "locationsText": j["place"] or "Remote (global)",
                "postedOn": "", "timeType": "",
                "bulletFields": [j["rid"]], "ats": "insilico",
            }
        meta = {"rows": len(rows), "total": len(rows) + dropped,
                "complete": True, "pages": 1,
                "client_filtered": dropped,
                "client_filtered_country": dropped,
                "client_filtered_time": 0}
        print(f"[{progress_label}] insilico:{self.org}: {len(rows)} "
              f"rows ({dropped} non-remote-global dropped)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        return {
            "jobPostingInfo": {
                "title": r["title"],
                "location": r["place"] or "Remote (global)",
                "additionalLocations": [],
                "jobDescription": r["desc"], "timeType": "",
                "startDate": "",
                "externalUrl": f"https://{self.org}/careers",
                "jobReqId": rid, "postedOn": "",
                "country": {"descriptor":
                            "Remote" if re.search(
                                r"remote,? open globally", r["place"],
                                re.I) else None},
            },
            "hiringOrganization": {"name": "Insilico Medicine"},
            "similarJobs": [],
        }


# ── orbbec (S23: Elementor inline-JD static page — the S21 catalog
#    class, upgraded to wire per the adj-5 evidence) ───────────────────
class OrbbecAdapter:
    """spec org = host (www.orbbec.com). Jobs are inline text-editor
    widgets: '<p><strong>TITLE</strong></p>' followed by Job
    Responsibilities/Requirements + a resume address. US evidence =
    the resume address (Troy MI — the LCA employer's address). No
    ids/dates; rid = slug(title)."""

    KIND = "orbbec"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        if not org:
            org = "www.orbbec.com"       # custom: grammar — host baked
        self.org = org
        self.cfg = cfg

    def _page(self) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/careers/", f"ats:orbbec:{self.org}",
            self.cfg)

    def _rows(self) -> list:
        key = f"orbbec:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        html = self._page()
        # strip scripts/styles FIRST — the last block would otherwise
        # absorb the page tail (CSS/JS noise, false US matches)
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.S)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.S)
        rows: list[dict] = []
        # job blocks: <p><strong>Title</strong></p> then the body until
        # the next <p><strong> (Elementor wraps each job in its own
        # text-editor widget; titles are the bold-lead paragraphs)
        blocks = re.split(r'<p>\s*<strong>', html)
        cur: Optional[dict] = None
        for b in blocks[1:]:
            tm = re.match(r'([^<]{3,120})</strong>\s*</p>', b)
            head = unescape(tm.group(1)).strip() if tm else ""
            is_job_title = bool(re.search(
                r'(engineer|developer|manager|scientist|designer'
                r'|specialist|director|analyst|architect|intern|sales'
                r'|marketing|technician|consultant|lead)', head, re.I))
            txt = unescape(re.sub(r"<[^>]+>", "\n", b))
            txt = re.sub(r"\n{2,}", "\n", txt).strip()
            txt = txt.replace("\xa0", " ")
            if is_job_title and head:
                if cur:
                    rows.append(cur)
                cur = {"rid": _slugify(head), "title": head,
                       "desc": txt}
            elif cur is not None:
                # section blocks (Job Responsibilities:, requirements,
                # address) MERGE into the current job
                cur["desc"] += "\n" + txt
        if cur:
            rows.append(cur)
        # classify AFTER merging — trim the FOOTER first (the last
        # job's desc absorbs page-tail marketing/footer text where the
        # resume address repeats and would misclassify it)
        for r in rows:
            d = re.split(
                r"Terms and Conditions|Stay updated|粤ICP备|"
                r"Facebook-f\s*Linkedin|North America 2800",
                r["desc"])[0]
            r["desc"] = d.strip()
            r["us"] = bool(re.search(
                r"(?:Troy\s*,?\s*MI|Livernois|,\s*[A-Z]{2}\s+\d{5}"
                r"|United States)", d))
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped = 0
        for j in self._rows():
            if country and not j["us"]:
                dropped += 1
                continue
            rows[j["rid"]] = {
                "reqId": j["rid"], "title": j["title"],
                "company": "Orbbec",
                "url": f"https://{self.org}/careers/",
                "externalPath": f"/{j['rid']}",
                "locationsText": "US" if j["us"] else "See description",
                "postedOn": "", "timeType": "",
                "bulletFields": [j["rid"]], "ats": "orbbec",
            }
        meta = {"rows": len(rows), "total": len(rows) + dropped,
                "complete": True, "pages": 1,
                "client_filtered": dropped,
                "client_filtered_country": dropped,
                "client_filtered_time": 0}
        print(f"[{progress_label}] orbbec:{self.org}: {len(rows)} rows "
              f"({dropped} non-{country or '-'} dropped; inline-JD "
              "address classification)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        return {
            "jobPostingInfo": {
                "title": r["title"],
                "location": "US" if r["us"] else "See description",
                "additionalLocations": [],
                "jobDescription": r["desc"], "timeType": "",
                "startDate": "",
                "externalUrl": f"https://{self.org}/careers/",
                "jobReqId": rid, "postedOn": "",
                "country": {"descriptor": "US" if r["us"] else None},
            },
            "hiringOrganization": {"name": "Orbbec"},
            "similarJobs": [],
        }


# ── visionnav (S23: SSR data.php list + detail pages — adj-6) ──────────────
class VisionNavAdapter:
    """spec org = host (www.visionnav.com). data.php = the SSR jobs
    list (19 recruiting-item cards: title h4 + 'Product | <loc> |
    Social Recruitment | <date>' meta + detail<N>.html links). US rule
    from the meta location token (North America / USA / Remote,US)."""

    KIND = "visionnav"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        if not org:
            org = "www.visionnav.com"     # custom: grammar — host baked
        self.org = org
        self.cfg = cfg

    def _page(self) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/about/data.php",
            f"ats:visionnav:{self.org}", self.cfg)

    def _rows(self) -> list:
        key = f"visionnav:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        html = self._page()
        rows: list[dict] = []
        for m in re.finditer(
                r'<div class="recruiting-item"[^>]*>\s*<a href="'
                r'(detail\d+\.html)"[^>]*>(.*?)</a>', html, re.S):
            href, body = m.groups()
            tm = re.search(r'<h4[^>]*>(.*?)</h4>', body, re.S)
            mm = re.search(r'<div class="text-2[^"]*"[^>]*>(.*?)'
                           r'</div>', body, re.S)
            title = unescape(re.sub(r"<[^>]+>", "",
                                    tm.group(1) if tm else "")).strip()
            meta = unescape(re.sub(r"<[^>]+>", " ",
                                   mm.group(1) if mm else "")).strip()
            parts = [p.strip() for p in meta.split("|")]
            loc = parts[1] if len(parts) >= 2 else ""
            posted = parts[-1] if parts and re.match(
                r"\d{4}\.", parts[-1]) else ""
            is_us = bool(re.search(
                r"North America|USA|Remote,\s*US\b|United States", loc))
            if not title:
                continue
            rows.append({"rid": href.replace(".html", ""),
                         "title": title, "loc": loc or "See meta",
                         "posted": posted, "us": is_us,
                         "url": f"https://{self.org}/about/{href}"})
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped = 0
        for j in self._rows():
            if country and not j["us"]:
                dropped += 1
                continue
            label, iso = _posted_label(_dotdate_iso(j["posted"]))
            rows[j["rid"]] = {
                "reqId": j["rid"], "title": j["title"],
                "company": "VisionNav Robotics",
                "url": j["url"], "externalPath": f"/{j['rid']}",
                "locationsText": j["loc"], "postedOn": label,
                "postedOnIso": iso, "timeType": "",
                "bulletFields": [j["rid"]], "ats": "visionnav",
            }
        meta = {"rows": len(rows), "total": len(rows) + dropped,
                "complete": True, "pages": 1,
                "client_filtered": dropped,
                "client_filtered_country": dropped,
                "client_filtered_time": 0}
        print(f"[{progress_label}] visionnav:{self.org}: {len(rows)} "
              f"rows ({dropped} non-{country or '-'} dropped)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        html = _fetch_text_resilient(r["url"],
                                     f"ats:visionnav:{self.org}:{rid}",
                                     self.cfg)
        desc = ""
        dm = re.search(r'<div class="recruiting-detail[^"]*"'
                       r'[^>]*>(.*?)</div>\s*</div>', html or "", re.S)
        if not dm:
            dm = re.search(
                r'Position</[^>]+>(.*?)</div>\s*</div>', html or "",
                re.S)
        if dm:
            d_ = unescape(re.sub(r"<[^>]+>", "\n", dm.group(1)))
            desc = re.sub(r"\n{2,}", "\n", d_).strip()
        if not desc:               # whole-body fallback (scoped page)
            body = re.sub(r"<script[^>]*>.*?</script>", "",
                          html or "", flags=re.S)
            d_ = unescape(re.sub(r"<[^>]+>", "\n", body))
            desc = re.sub(r"\n{2,}", "\n", d_).strip()[:6000]
        return {
            "jobPostingInfo": {
                "title": r["title"], "location": r["loc"],
                "additionalLocations": [],
                "jobDescription": desc, "timeType": "",
                "startDate": _dotdate_iso(r["posted"]),
                "externalUrl": r["url"], "jobReqId": rid,
                "postedOn": "",
                "country": {"descriptor": "US" if r["us"] else None},
            },
            "hiringOrganization": {"name": "VisionNav Robotics"},
            "similarJobs": [],
        }


def _dotdate_iso(text: str) -> str:
    """"2026.03.31" → "2026-03-31" (the VisionNav dot-date format)."""
    m = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", text or "")
    if not m:
        return ""
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-" \
           f"{int(m.group(3)):02d}"


# ── verisilicon (S23: Careers page divUSTabsItem h3 sections — adj-6) ─────
class VeriSiliconAdapter:
    """spec org = host (www.verisilicon.com). The Careers page's US tab
    (divUSTabsItem) renders jobs as careertabcont blocks: h3 title +
    opencareercont description; email apply (US.HR@). 2 live US roles
    (the adj note's 4 counted description bullets as roles)."""

    KIND = "verisilicon"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        if not org:
            org = "www.verisilicon.com"   # custom: grammar — host baked
        self.org = org
        self.cfg = cfg

    def _page(self) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/en/Careers", f"ats:vs:{self.org}",
            self.cfg)

    def _rows(self) -> list:
        key = f"verisilicon:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        html = self._page()
        i = html.find("divUSTabsItem")
        seg = html[i:] if i >= 0 else ""
        rows: list[dict] = []
        for m in re.finditer(
                r'<div class="careertabcont">(.*?)(?=<div class='
                r'"careertabcont">|</div>\s*</div>\s*</div>\s*</div>)',
                seg, re.S):
            blk = m.group(1)
            tm = re.search(r'<h3>([^<]+)</h3>', blk)
            title = unescape(tm.group(1)).strip() if tm else ""
            if not title:
                continue
            d_ = unescape(re.sub(r"<[^>]+>", "\n", blk))
            desc = re.sub(r"\n{2,}", "\n", d_).strip()
            desc = desc.replace("\xa0", " ")
            # drop the leading title echo in the description
            desc = re.sub(r"^\s*" + re.escape(title) + r"\s*", "", desc)
            rows.append({"rid": _slugify(title), "title": title,
                         "desc": desc})
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        for j in self._rows():      # the /en US tab IS the US board
            rows[j["rid"]] = {
                "reqId": j["rid"], "title": j["title"],
                "company": "VeriSilicon",
                "url": f"https://{self.org}/en/Careers",
                "externalPath": f"/{j['rid']}",
                "locationsText": "San Jose, CA",
                "postedOn": "", "timeType": "",
                "bulletFields": [j["rid"]], "ats": "verisilicon",
            }
        meta = {"rows": len(rows), "total": len(rows), "complete": True,
                "pages": 1, "client_filtered": 0,
                "client_filtered_country": 0,
                "client_filtered_time": 0}
        print(f"[{progress_label}] verisilicon:{self.org}: {len(rows)} "
              "US rows (en/Careers divUS tab)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        return {
            "jobPostingInfo": {
                "title": r["title"], "location": "San Jose, CA",
                "additionalLocations": [],
                "jobDescription": r["desc"], "timeType": "",
                "startDate": "",
                "externalUrl": f"https://{self.org}/en/Careers",
                "jobReqId": rid, "postedOn": "",
                "country": {"descriptor": "US"},
            },
            "hiringOrganization": {"name": "VeriSilicon"},
            "similarJobs": [],
        }


# ── uniview (S23: US careers page div.li blocks — adj-6) ───────────────────
class UniviewAdapter:
    """spec org = host (www.uniview.com). /us/About_Us/Career/ renders
    div.li blocks: div.tit-20 title ('Sales Engineer(Based in
    USA/Canada)') + body with description and 'Base in: USA/Canada'.
    US rule: 'Based in USA' / 'Base in: USA' in title or body."""

    KIND = "uniview"
    _US = "United States"

    def __init__(self, org: str, cfg: Config):
        if not org:
            org = "www.uniview.com"       # custom: grammar — host baked
        self.org = org
        self.cfg = cfg

    def _page(self) -> str:
        return _fetch_text_resilient(
            f"https://{self.org}/us/About_Us/Career/",
            f"ats:uniview:{self.org}", self.cfg)

    def _rows(self) -> list:
        key = f"uniview:{self.org}"
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        html = self._page()
        rows: list[dict] = []
        # split on the li openers (nested divs make close-matching
        # unreliable; each segment runs to the next li or page end)
        for blk in re.split(r'<div class="li\w*">', html)[1:]:
            blk = blk.split('<div class="page')[0]
            tm = re.search(r'<div class="tit-20"[^>]*>(.*?)</div>',
                           blk, re.S)
            title = unescape(re.sub(r"<[^>]+>", "",
                                    tm.group(1) if tm else "")).strip()
            if not title:
                continue
            d_ = unescape(re.sub(r"<[^>]+>", "\n", blk))
            desc = re.sub(r"\n{2,}", "\n", d_).strip()
            desc = desc.replace("\xa0", " ")
            is_us = bool(re.search(r"Bas(?:e|ed) in[:\s]*USA",
                                   title + " " + desc))
            rows.append({"rid": _slugify(title), "title": title,
                         "desc": desc, "us": is_us})
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list"
                   ) -> tuple[dict[str, dict], dict]:
        rows: dict[str, dict] = {}
        dropped = 0
        for j in self._rows():
            if country and not j["us"]:
                dropped += 1
                continue
            rows[j["rid"]] = {
                "reqId": j["rid"], "title": j["title"],
                "company": "Uniview",
                "url": f"https://{self.org}/us/About_Us/Career/",
                "externalPath": f"/{j['rid']}",
                "locationsText": "USA/Canada" if j["us"]
                else "See description",
                "postedOn": "", "timeType": "",
                "bulletFields": [j["rid"]], "ats": "uniview",
            }
        meta = {"rows": len(rows), "total": len(rows) + dropped,
                "complete": True, "pages": 1,
                "client_filtered": dropped,
                "client_filtered_country": dropped,
                "client_filtered_time": 0}
        print(f"[{progress_label}] uniview:{self.org}: {len(rows)} rows "
              f"({dropped} non-{country or '-'} dropped)")
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        r = next((x for x in self._rows() if x["rid"] == rid), None)
        if r is None:
            return None
        return {
            "jobPostingInfo": {
                "title": r["title"],
                "location": "USA/Canada" if r["us"] else "See description",
                "additionalLocations": [],
                "jobDescription": r["desc"], "timeType": "",
                "startDate": "",
                "externalUrl": (f"https://{self.org}/us/About_Us/"
                                f"Career/"),
                "jobReqId": rid, "postedOn": "",
                "country": {"descriptor": "US" if r["us"] else None},
            },
            "hiringOrganization": {"name": "Uniview"},
            "similarJobs": [],
        }


_ADAPTERS = {"greenhouse": GreenhouseAdapter, "ashby": AshbyAdapter,
             "lever": LeverAdapter, "workable": WorkableAdapter,
             "feishuhire": FeishuHireAdapter,
             "bytedance": ByteDanceAdapter, "alibaba": AlibabaAdapter,
             "tripcom": TripComAdapter, "xiaohongshu": XiaohongshuAdapter,
             "paylocity": PaylocityAdapter, "adp": ADPWorkforceNowAdapter,
             "jazzhr": JazzHRAdapter, "teamtailor": TeamtailorAdapter,
             "radancy": RadancyAdapter, "rippling": RipplingAdapter,
             # S22 wave-3 (13 new classes; contracts in
             # ingest/data/ats_seed/s22_wave3/re_*.md)
             "breezy": BreezyAdapter, "jobvite": JobviteAdapter,
             "oraclehcm": OracleHCMAdapter, "bamboohr": BambooHRAdapter,
             "j2w": J2WAdapter, "ultipro": UltiProAdapter,
             "talentadore": TalentAdoreAdapter,
             "workstream": WorkstreamAdapter,
             "ttiproxy": TTIDrupalAdapter, "sanity": SanityAdapter,
             "wpjobboard": WPJobBoardAdapter, "wuxibio": WuXiBioAdapter,
             "antintl": AntIntlAdapter,
             # S23 parked-wire customs (contracts in
             # ingest/data/ats_seed/s22_wave3/re_D.md)
             "greenland": GreenlandAdapter, "jereh": JerehAdapter,
             "autelenergy": AutelEnergyAdapter, "aden": AdenAdapter,
             "mandarinoriental": MandarinOrientalAdapter,
             "wuxiapptec": WuXiAppTecAdapter,
             # S23 AI-census customs (contracts in
             # ingest/data/ats_seed/s23_ai_census/re_E.md)
             "blacksesame": BlackSesameAdapter,
             "ecovacsus": EcovacsUSAdapter, "accutar": AccutarAdapter,
             "hitgen": HitGenAdapter, "insilico": InsilicoAdapter,
             "orbbec": OrbbecAdapter, "visionnav": VisionNavAdapter,
             "verisilicon": VeriSiliconAdapter, "uniview": UniviewAdapter}
