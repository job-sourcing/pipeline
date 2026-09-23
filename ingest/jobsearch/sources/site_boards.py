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
        f"greenhouse|ashby, or 'custom:kind' with kind in "
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
            d = _imp_post_json(self._API, body, headers=self._HDRS)
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

    def detail_payload(self, external_path: str) -> Optional[dict]:
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

    Host registry (live-pinned 2026-09-19):
      aidc      aidc-jobs.alibaba.com            153 rows,   6 US
      cloud     careers-alibabacloud.com         224 rows, ~37 US — DNS-
                VOLATILE (gTLD delegation flaps; fail-soft: 3 attempts,
                then LOUD skip + complete=False — never mass-false-gone)
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
        ("cloud", "careers-alibabacloud.com"),
        ("holding", "talent-holding.alibaba.com"),
        ("tongyi", "careers-tongyi.alibaba.com"),
    ]
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
                r0 = _imp_get(base + "/en/off-campus/position-list?lang=en")
                if r0.status_code != 200:
                    raise RuntimeError(f"{host}: page HTTP {r0.status_code}")
                xsrf = None
                for c in _imp_session().cookies.jar:
                    if getattr(c, "name", "") == "XSRF-TOKEN":
                        xsrf = getattr(c, "value", None)
                if not xsrf:
                    raise RuntimeError(f"{host}: no XSRF-TOKEN cookie served")
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
                        raise RuntimeError(f"{host}: datas not a list")
                    out.extend(datas)
                    total = content.get("totalCount")
                    if (len(datas) < self._PAGE
                            or (isinstance(total, int) and total and
                                len(out) >= total)):
                        break
                    page += 1
                    if page > 60:  # circuit breaker
                        raise RuntimeError(f"{host}: pagination runaway")
                return out, True
            except Exception as e:            # DNS/WAF/shape — fail-soft
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        print(f"[alibaba] WARNING: host {hostkey} ({host}) unreachable "
              f"after {self._ATTEMPTS} attempts ({last_err}) — SKIPPED, "
              f"sweep marked incomplete (complete=False so the watch "
              f"never computes false gones)", file=sys.stderr, flush=True)
        return [], False

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

    def detail_payload(self, external_path: str) -> Optional[dict]:
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
        d = _imp_post_json(self._LOC, {"countryCode": "",
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
            d = _imp_post_json(self._API, body, headers=self._HDRS)
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

    def detail_payload(self, external_path: str) -> Optional[dict]:
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


_ADAPTERS = {"greenhouse": GreenhouseAdapter, "ashby": AshbyAdapter,
             "bytedance": ByteDanceAdapter, "alibaba": AlibabaAdapter,
             "tripcom": TripComAdapter}
