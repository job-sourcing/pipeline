"""Workday CXS job-board adapter (Layer-1 ATS-direct).

Workday is the largest ATS surface missing from the directory drain: the
seed (`ingest/data/ats_seed/workday_companies.json`) carries 12,884
`tenant|instance|site` board triples, and the public Candidate Experience
API ("CXS") serves each board's postings with no auth and no anti-bot.

Endpoint shape (verified live 2026-09-08 from an HK egress):

    POST https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    body: {"appliedFacets": {...}, "limit": 20, "offset": N, "searchText": ""}

CXS quirks — ALL verified live on nvidia.wd5 (S5-2 pilot):
- ``limit`` MUST be <= 20 (25+ → HTTP 400).
- ``offset`` past the result count WRAPS AROUND to page 0 (silent
  duplicates!) — bound the loop by the FIRST page's ``total``.
- near the end of the result set the per-page ``total`` reports 0 — never
  trust it after page 1.
- server-side filters via ``appliedFacets`` keyed by facet parameter with
  facet-value IDs discovered from the first page's ``facets`` payload:
  ``locationHierarchy1`` (countries, e.g. "United States" id …32d8),
  ``timeType`` ("Full time" / "Part time" ids).
- detail: ``GET {cxs_base}{externalPath}`` → ``jobPostingInfo`` with
  title / location / additionalLocations / startDate (ISO) / timeType /
  jobDescription (HTML) / externalUrl (canonical apply link) / jobReqId.
- the board UI page ``https://{host}/{site}`` embeds
  ``window.workday = {tenant, siteId, …}`` — free site-ID discovery for
  future tenants (see ``discover_board_config``).

NVIDIA pilot result: 1,428 US full-time roles, exhaustively paginated
(72 pages, unique jobReqIds == total — scripts/workday_dump.py).
"""
from __future__ import annotations

import html as html_mod
import re
import time
from typing import Optional

from ..config import Config
from ..models import Job
from .base import clean_html, fetch_json, fetch_text, detect_h1b, is_within_days

# Board spec: "tenant|instance|site" (the seed's format). Default = the
# NVIDIA pilot board (user-requested exhaustive test case, 2026-09-08).
# Override via WORKDAY_BOARDS="tenant|instance|site,..." (csv).
DEFAULT_BOARDS = ["nvidia|wd5|nvidiaexternalcareersite"]

_PAGE_LIMIT = 20          # CXS hard cap (25+ → HTTP 400)
_MAX_PAGES = 60           # 60 × 20 = 1,200 list rows scanned per board —
                          # enough for any single-company keyword search
_DETAIL_SLEEP_S = 0.35    # courtesy pacing for detail GETs

# Facet-parameter names on the CXS response (verified nvidia.wd5).
_FACET_COUNTRY = "locationHierarchy1"
_FACET_LOC_TYPE = "locationHierarchy2"   # Office / Remote — the remote facet
_FACET_TIME = "timeType"

# tenant → display company name (title() is wrong for these)
_COMPANY_DISPLAY = {"nvidia": "NVIDIA"}

_WORKDAY_CFG_RE = re.compile(
    r"window\.workday\s*=\s*(?:window\.workday\s*\|\|\s*)?\{")


def parse_board(spec: str) -> tuple[str, str, str]:
    """'tenant|instance|site' → (tenant, instance, site). Also accepts
    'tenant/instance/site' (URL-ish). Case of `site` is preserved — it is
    case-sensitive in board URLs (NVIDIAExternalCareerSite)."""
    for sep in ("|", "/"):
        parts = [p.strip() for p in spec.split(sep) if p.strip()]
        if len(parts) == 3:
            return parts[0].lower(), parts[1].lower(), parts[2]
    raise ValueError(
        f"workday board spec must be 'tenant|instance|site', got {spec!r}")


def _cxs_base(tenant: str, instance: str) -> str:
    return f"https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/"


def _board_base_url(tenant: str, instance: str, site: str) -> str:
    return f"https://{tenant}.{instance}.myworkdayjobs.com/{site}"


def company_display(tenant: str) -> str:
    return _COMPANY_DISPLAY.get(tenant, tenant.replace("-", " ").title())


def _flatten_facets(payload: dict) -> list[dict]:
    """Flatten the (possibly nested) `facets` list into a single list of
    facet dicts, each with `facetParameter` + `values`."""
    flat: list[dict] = []
    stack = list(payload.get("facets") or [])
    while stack:
        f = stack.pop(0)
        if not isinstance(f, dict):
            continue
        flat.append(f)
        for v in f.get("values") or []:
            if isinstance(v, dict) and v.get("facetParameter"):
                stack.append(v)
    return flat


def _facet_id(payload: dict, param: str, label: str) -> Optional[str]:
    """Find the facet value ID whose descriptor matches `label`
    (case-insensitive) under facet parameter `param`."""
    want = label.strip().lower()
    for f in _flatten_facets(payload):
        if f.get("facetParameter") != param:
            continue
        for v in f.get("values") or []:
            if str(v.get("descriptor", "")).strip().lower() == want:
                return v.get("id")
    return None


def _country_names(payload: dict) -> list[str]:
    """All country descriptors under locationHierarchy1 (for diagnostics)."""
    names: list[str] = []
    for f in _flatten_facets(payload):
        if f.get("facetParameter") == _FACET_COUNTRY:
            names = [str(v.get("descriptor", "")) for v in f.get("values") or []]
    return names


def _time_type_label(job_type: Optional[str]) -> Optional[str]:
    if not job_type:
        return None
    jt = job_type.strip().lower().replace("_", " ")
    if "full" in jt:
        return "Full time"
    if "part" in jt:
        return "Part time"
    return None


def _page(board: tuple[str, str, str], facets: dict, offset: int,
          cfg: Config) -> dict:
    tenant, instance, site = board
    return fetch_json(
        f"{_cxs_base(tenant, instance)}{site}/jobs",
        cfg=cfg,
        method="POST",
        headers={"Accept": "application/json",
                 "Content-Type": "application/json"},
        json={"appliedFacets": facets, "limit": _PAGE_LIMIT,
              "offset": offset, "searchText": ""},
    )


def detail_payload(board: tuple[str, str, str], external_path: str,
                   cfg: Config) -> Optional[dict]:
    """GET the FULL detail payload (top-level dict: jobPostingInfo +
    hiringOrganization + similarJobs + userAuthenticated) or None.

    Added for the v2 dump (design-board-v2.md D1) — raw preservation of
    every field the API returns, future-proofing the enrichment."""
    tenant, instance, site = board
    try:
        payload = fetch_json(
            f"{_cxs_base(tenant, instance)}{site}{external_path}",
            cfg=cfg, headers={"Accept": "application/json"})
    except Exception:  # noqa: BLE001 — detail is enrichment, never fatal
        return None
    return payload if isinstance(payload, dict) else None


def _detail(board: tuple[str, str, str], external_path: str,
            cfg: Config) -> Optional[dict]:
    """GET the posting detail (jobPostingInfo) or None on failure."""
    payload = detail_payload(board, external_path, cfg)
    info = payload.get("jobPostingInfo") if payload else None
    return info if isinstance(info, dict) else None


def discover_board_config(board_url_or_host: str, cfg: Optional[Config] = None
                          ) -> dict:
    """Scrape the board UI page for its `window.workday` config —
    tenant + siteId discovery without a browser (S5-2 enabler).

    Accepts 'https://nvidia.wd5.myworkdayjobs.com/Anything' (path ignored)
    or a bare host. Returns the parsed config dict (tenant, siteId,
    locale, …) — raises RuntimeError when no config is found (dead board /
    outage redirect).
    """
    cfg = cfg or Config()
    host = board_url_or_host.strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]
    html = fetch_text(f"https://{host}/", cfg=cfg)
    m = _WORKDAY_CFG_RE.search(html)
    if not m:
        raise RuntimeError(f"no window.workday config on {host!r} "
                           f"(outage redirect or not a Workday board)")
    # The config object is a JS object literal, not JSON — extract the
    # fields we need with targeted regexes (robust to quoting styles).
    def _field(name: str) -> Optional[str]:
        fm = re.search(rf'\b{name}\s*:\s*"([^"]*)"', html)
        return fm.group(1) if fm else None
    return {
        "tenant": _field("tenant"),
        "siteId": _field("siteId"),
        "locale": _field("locale"),
        "requestLocale": _field("requestLocale"),
    }


def _job_from_detail(board: tuple[str, str, str], info: dict) -> Job:
    tenant, instance, _site = board
    loc = str(info.get("location") or "")
    addls = [str(a) for a in (info.get("additionalLocations") or [])]
    all_locs = ", ".join([loc] + addls)
    link = str(info.get("externalUrl") or "").strip()
    if not link:
        # fallback: board URL + externalPath
        link = _board_base_url(tenant, instance, _site)
    title = str(info.get("title") or "")
    desc = clean_html(html_mod.unescape(str(info.get("jobDescription") or "")))
    date = str(info.get("startDate") or "")[:10] or None
    return Job(
        title=title,
        company=company_display(tenant),
        description=desc,
        link=link,
        source=f"Workday.{tenant}",
        location=loc,
        date_posted=date,
        remote="remote" in all_locs.lower(),
        h1b_mention=detect_h1b(f"{title} {desc}"),
    )


def _job_from_list(board: tuple[str, str, str], posting: dict) -> Job:
    """List-level Job (no detail yet) — link built from the board URL +
    externalPath; date/description filled later by the detail pass."""
    tenant, instance, site = board
    title = str(posting.get("title") or "")
    path = str(posting.get("externalPath") or "")
    return Job(
        title=title,
        company=company_display(tenant),
        description="",
        link=f"{_board_base_url(tenant, instance, site)}{path}" if path else "",
        source=f"Workday.{tenant}",
        location=str(posting.get("locationsText") or ""),
        date_posted=None,
        remote=False,
        h1b_mention=detect_h1b(title),
    )


def _matches_keywords(title: str, keywords: str) -> bool:
    terms = [t for t in re.split(r"\W+", (keywords or "").lower()) if len(t) > 2]
    if not terms:
        return True
    t = title.lower()
    return any(term in t for term in terms)


def _is_multi_location(locations_text: str) -> bool:
    """locationsText like '4 Locations' — the real sites are only in the
    detail payload; treat as location-unknown at list level."""
    lt = (locations_text or "").strip().lower()
    return lt.endswith("locations") or lt.endswith("location")


def _loc_matches(needle: str, *location_strings: str) -> bool:
    blob = " ".join(s or "" for s in location_strings).lower()
    return needle in blob


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, date_filter: Optional[int] = None,
          job_type: Optional[str] = None, cfg: Optional[Config] = None
          ) -> list[Job]:
    """Fetch jobs from the configured Workday boards (default: NVIDIA).

    Thin-adapter contract: keyword filter on title (client-side, same
    policy as the Ashby/Greenhouse family), country-level location and
    time-type via server-side CXS facets, detail GETs only for the matched
    top-N (paced). Board list: cfg.workday_boards (WORKDAY_BOARDS env).
    """
    cfg = cfg or Config()
    boards = getattr(cfg, "workday_boards", None) or list(DEFAULT_BOARDS)
    out: list[Job] = []

    for spec in boards:
        try:
            board = parse_board(spec)
        except ValueError as e:
            print(f"[workday] skipping malformed board spec: {e}")
            continue
        try:
            out.extend(_fetch_board(board, keywords, location, num_results,
                                    date_filter, job_type, cfg))
        except Exception as e:  # noqa: BLE001 — per-board isolation
            print(f"[workday] board {spec} failed: {type(e).__name__}: {e}")
    return out


def _fetch_board(board: tuple[str, str, str], keywords: str, location: str,
                 num_results: int, date_filter: Optional[int],
                 job_type: Optional[str], cfg: Config) -> list[Job]:
    tenant, instance, site = board
    loc = (location or "").strip()

    # 1) first page (no facets) → discover facet ids for the filters
    first = _page(board, {}, 0, cfg)
    total = int(first.get("total") or 0)
    if total <= 0:
        return []

    facets: dict[str, list[str]] = {}
    server_loc = False          # location handled server-side → skip client filter
    country_id = _facet_id(first, _FACET_COUNTRY, loc)
    if country_id:
        facets[_FACET_COUNTRY] = [country_id]
        server_loc = True
    elif loc.lower() == "remote":
        # server-side remote facet (578/2551 split on nvidia.wd5) — exact,
        # exhaustive; falls back to client-side substring when absent.
        # (location="" means NO location filter — don't apply it.)
        remote_id = _facet_id(first, _FACET_LOC_TYPE, "Remote")
        if remote_id:
            facets[_FACET_LOC_TYPE] = [remote_id]
            server_loc = True
    tt_label = _time_type_label(job_type)
    if tt_label:
        tt_id = _facet_id(first, _FACET_TIME, tt_label)
        if tt_id:
            facets[_FACET_TIME] = [tt_id]

    # 2) if a facet was applied, refetch page 0 — the filtered total is
    #    what bounds the pagination loop (server-side count).
    if facets:
        first = _page(board, facets, 0, cfg)
        total = int(first.get("total") or 0)
        if total <= 0:
            return []

    # 3) paginate (bounded by the FIRST page's total; offsets past total
    #    wrap around to page 0 — the CXS duplicate trap). City-level
    #    searches keep multi-location rows as candidates ('4 Locations' —
    #    the real sites are only in the detail payload); confirmed searches
    #    only need num_results candidates.
    needle = loc.lower()
    confirmed_cap = num_results if (server_loc or not needle) else \
        max(num_results * 3, num_results + 10)
    collected: list[tuple[Job, bool]] = []   # (job, location_confirmed)
    offset = 0
    pages = 0
    postings = first.get("jobPostings") or []
    while True:
        pages += 1
        for p in postings:
            j = _job_from_list(board, p)
            if not _matches_keywords(j.title, keywords):
                continue
            if server_loc:
                confirmed = True
            elif not needle:
                confirmed = True
            elif _loc_matches(needle, j.location):
                confirmed = True
            elif _is_multi_location(j.location):
                confirmed = False          # decide after the detail fetch
            else:
                continue
            collected.append((j, confirmed))
            if len(collected) >= confirmed_cap:
                break
        if len(collected) >= confirmed_cap:
            break
        offset += _PAGE_LIMIT
        if offset >= total or pages >= _MAX_PAGES:
            break
        nxt = _page(board, facets, offset, cfg)
        # near the end, per-page total reports 0 — trust only page 1's
        postings = nxt.get("jobPostings") or []
        if not postings:
            break
        time.sleep(0.25)

    # 4) detail pass — enriches (description, exact date/location) AND is
    #    the location arbiter for unconfirmed multi-location candidates.
    #    Paced; detail failures keep the list-level Job (flag, never drop).
    detailed: list[Job] = []
    for j, confirmed in collected:
        if len(detailed) >= num_results:
            break
        path = j.link.rsplit(site, 1)[-1] if site in j.link else ""
        info = _detail(board, path, cfg) if path else None
        dj = _job_from_detail(board, info) if info else j
        if not confirmed and needle:
            addls = ", ".join(str(a) for a in
                              ((info or {}).get("additionalLocations") or []))
            if not _loc_matches(needle, dj.location, addls):
                continue
        if date_filter and not is_within_days(dj.date_posted, date_filter):
            continue
        detailed.append(dj)
        time.sleep(_DETAIL_SLEEP_S)
    return detailed


def resolve_facets(board: tuple[str, str, str], first: dict,
                    country: Optional[str],
                    time_type: Optional[str]) -> dict[str, list[str]]:
    """Resolve country/timeType facet ids from a page-0 payload.

    Raises ValueError (message lists available countries) when a requested
    facet label doesn't exist — callers translate to their own contract.
    """
    facets: dict[str, list[str]] = {}
    if country:
        cid = _facet_id(first, _FACET_COUNTRY, country)
        if not cid:
            raise ValueError(
                f"country {country!r} not a facet "
                f"(available: {', '.join(_country_names(first))})")
        facets[_FACET_COUNTRY] = [cid]
    if time_type:
        tid = _facet_id(first, _FACET_TIME, time_type)
        if not tid:
            raise ValueError(f"timeType {time_type!r} not a facet")
        facets[_FACET_TIME] = [tid]
    return facets


def iter_board_postings(board: tuple[str, str, str], facets: dict,
                        first: dict, cfg: Optional[Config] = None,
                        sleep_s: float = 0.2,
                        progress_every: int = 0,
                        progress_label: str = "list"
                        ) -> tuple[dict[str, dict], dict]:
    """THE exhaustive CXS listing primitive (single source of truth — was
    copy-drifted 4x across board_dump/watch/dump_board/workday_dump;
    the timeType-facet bug shipped from that drift, S7-B1 SA-1).

    Contract:
    - adaptive offset (advances by len(page postings), never a fixed step
      — pages can return <limit; a fixed step SKIPS cards)
    - dedup by reqId (the offset-past-total WRAP returns duplicates)
    - total-driven stop, trusting ONLY page-0's total (near-end pages
      report total=0)
    - rows carry reqId + canonical url + company display name
    - B1 semantics: complete=True only when pagination ended naturally;
      a mid-list network failure returns (partial rows, complete=False) —
      NEVER raises mid-list (a partial listing must never be read as
      exhaustive)
    Returns (rows: {reqId: row}, meta: {"complete", "total", "pages"}).
    """
    cfg = cfg or Config()
    tenant, instance, site = board
    total = int(first.get("total") or 0)
    rows: dict[str, dict] = {}
    offset, pages = 0, 0
    postings = first.get("jobPostings") or []
    while True:
        pages += 1
        for p in postings:
            rid = (p.get("bulletFields") or [None])[0] \
                or p.get("externalPath", "")
            if rid in rows:
                continue                      # wrap-past-total duplicate
            row = dict(p)
            row["reqId"] = rid
            row["company"] = company_display(tenant)
            row["url"] = (f"{_board_base_url(tenant, instance, site)}"
                          f"{p.get('externalPath', '')}")
            rows[rid] = row
        if progress_every and pages % progress_every == 0:
            print(f"[{progress_label}] {len(rows)}/{total} rows "
                  f"({pages} pages)", flush=True)
        if total and offset + len(postings) >= total:
            break
        if not postings:
            break
        offset += len(postings)               # ADAPTIVE (never fixed-step)
        try:
            nxt = _page(board, facets, offset, cfg)
        except Exception:
            return rows, {"complete": False, "total": total,
                          "pages": pages}     # B1: partial, never raise
        postings = nxt.get("jobPostings") or []
        time.sleep(sleep_s)
    return rows, {"complete": total <= len(rows) or not total,
                  "total": total, "pages": pages}


def list_board(spec: str, *, country: Optional[str] = None,
               time_type: Optional[str] = None,
               cfg: Optional[Config] = None, sleep_s: float = 0.2,
               progress_every: int = 0,
               progress_label: str = "list"
               ) -> tuple[dict[str, dict], dict]:
    """Convenience: parse spec → page-0 → resolve facets → refetch →
    iter. ValueError on unknown facet; (partial, complete=False) on
    mid-list failure; page-0 fetch errors propagate to the caller.
    Returns (rows, meta: {"complete", "total", "pages"})."""
    cfg = cfg or Config()
    board = parse_board(spec)
    first = _page(board, {}, 0, cfg)
    facets = resolve_facets(board, first, country, time_type)
    if facets:
        first = _page(board, facets, 0, cfg)
    return iter_board_postings(board, facets, first, cfg=cfg,
                               sleep_s=sleep_s,
                               progress_every=progress_every)


def dump_board(spec: str, *, country: Optional[str] = None,
               time_type: Optional[str] = None, cfg: Optional[Config] = None,
               with_details: bool = False,
               progress_every: int = 10) -> list[dict]:
    """EXHAUSTIVE list-level dump of one Workday board (back-compat list
    return; raises on unknown facet, unlike list_board's B1 partials).

    Used by tests + legacy callers; the dump/watch scripts use the phased
    board_dump flow (raw JSONL, resumable) on top of the same
    iter_board_postings primitive. `with_details=True` additionally GETs
    every detail (slow) and merges a trimmed jobPostingInfo view.
    """
    cfg = cfg or Config()
    board = parse_board(spec)
    rows, _meta = list_board(spec, country=country, time_type=time_type,
                                 cfg=cfg, progress_every=progress_every)
    out: list[dict] = list(rows.values())
    if with_details:
        for row in out:
            info = _detail(board, row.get("externalPath", ""), cfg)
            if info:
                row["detail"] = {
                    k: info.get(k) for k in (
                        "title", "location", "additionalLocations",
                        "startDate", "timeType", "jobReqId", "externalUrl")
                }
            time.sleep(_DETAIL_SLEEP_S)
    return out
