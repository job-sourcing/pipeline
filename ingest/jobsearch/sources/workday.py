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
- ``total`` is CAPPED at exactly 2,000 (S8-D quirk 1g, live-verified
  2026-09-13: no-facet reports 2,000 while three independent facet-count
  sums agree the true NVIDIA board = 2,657) — any board/facet-set with
  ≥2,000 postings would SILENTLY TRUNCATE under plain pagination.
  ``iter_board_postings`` guards this: a page-0 total of exactly 2,000
  triggers a facet-partitioned fallback (union-dedupe by reqId).
- server-side filters via ``appliedFacets`` keyed by facet parameter with
  facet-value IDs discovered from the first page's ``facets`` payload:
  ``locationHierarchy1`` (countries, e.g. "United States" id …32d8),
  ``timeType`` ("Full time" / "Part time" ids).
- the page-0 ``facets`` payload carries per-value COUNTS for every facet
  (countries, sites, Office/Remote, timeType, workerSubType,
  jobFamilyGroup) — the board-composition census, exposed via
  ``facet_census``/``facet_values`` and ``iter_board_postings`` meta.
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
import sys
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

# S8-D quirk 1g: the CXS `total` field is CAPPED at exactly 2,000 — a
# board/facet-set with more postings reports 2,000 and plain pagination
# silently truncates (NVIDIA no-facet: reported 2,000, true 2,657).
_CAPPED_TOTAL = 2000

# Facet-parameter names on the CXS response (verified nvidia.wd5).
_FACET_COUNTRY = "locationHierarchy1"
_FACET_LOC_TYPE = "locationHierarchy2"   # Office / Remote — the remote facet
_FACET_TIME = "timeType"

# Facet parameters usable to partition a capped listing back to
# exhaustiveness (preference order — countries first: verified live that
# every single-country filter stays below the cap on nvidia.wd5).
_PARTITION_FACETS = (_FACET_COUNTRY, "workerSubType", "jobFamilyGroup",
                     "locations", _FACET_LOC_TYPE, _FACET_TIME)

# tenant → display company name (title() is wrong for these)
_COMPANY_DISPLAY = {"nvidia": "NVIDIA", "netflix": "Netflix",
                    "tencent": "Tencent", "jd": "JD.com"}

# Client-side country filtering (S12 multi-company): boards differ in
# WHICH facets they expose — nvidia.wd5 serves a locationHierarchy1
# (country) facet, but netflix.wd108 / tencent.wd1 / jd.wd103 only
# offer locationMainGroup (city-level, nested). For those boards the
# country filter CANNOT be server-side; list rows are filtered by a
# location-token predicate instead (locationsText carries the country
# in every board's dialect: "US-CA-Santa Clara", "USA - Remote",
# "US-California-Palo Alto", "USA-California-Fontana").
# Token aliases for the requested country name — US only for now (the
# stress-test scope); other countries fall back to exact name-token
# matching, extensible here.
_COUNTRY_ALIASES: dict[str, frozenset[str]] = {
    "united states": frozenset(("usa", "us", "u.s.", "u.s.a",
                                 "u.s.a.", "united states of america")),
}


def _country_matchers(country: str) -> tuple[frozenset[str], list[list[str]]]:
    """(single tokens, phrase token-lists) that mean `country`.

    Single tokens match by membership ("usa"); multi-word names match
    only when EVERY phrase token is present ("united"+"states" — so
    "United Kingdom" / "United Arab Emirates" never hit)."""
    base = (country or "").strip().lower()
    if not base:
        return frozenset(), []
    singles: set[str] = set()
    phrases: list[list[str]] = []
    for alias in _COUNTRY_ALIASES.get(base, ()):
        parts = alias.split()
        if len(parts) == 1:
            singles.add(alias)
        else:
            phrases.append(parts)
    parts = base.split()
    if len(parts) == 1:
        singles.add(base)
    else:
        phrases.append(parts)
    return frozenset(singles), phrases


def _row_in_country(row: dict, country: str) -> bool:
    """Client-side country predicate for a CXS list row.

    Matches the country's single tokens + phrase tokens against the
    tokens of locationsText AND primaryLocation.country when present.
    Hyphen/whitespace tokenization keeps every observed dialect honest
    ("US-CA-Santa Clara" → us/ca/santa/clara; "AUS-Sydney" → aus/sydney
    — no false "us" hit because the token is "aus"; multi-word names
    are PHRASE-matched so "United Kingdom" never hits "united").
    """
    singles, phrases = _country_matchers(country)
    if not singles and not phrases:
        return True
    loc_toks: set[str] = set()
    lt = row.get("locationsText")
    if isinstance(lt, str):
        loc_toks.update(t.strip(",()").lower()
                        for t in re.split(r"[\s\-]+", lt))
    pl = row.get("primaryLocation") or {}
    if isinstance(pl, dict):
        li = pl.get("location") or {}
        if isinstance(li, dict):
            c = (li.get("country") or "").strip().lower()
            if c:
                loc_toks.update(t.strip(",()").lower()
                                for t in re.split(r"[\s\-]+", c))
    if loc_toks & singles:
        return True
    return any(all(t in loc_toks for t in ph) for ph in phrases)


def client_country_filter(country: str,
                           first: dict) -> Optional[callable]:
    """The client-side row filter for `country`, or None.

    Returns a predicate ONLY when the board exposes no country facet at
    all (then server-side filtering is impossible and the caller must
    filter rows itself). None when the board HAS the facet — then the
    normal resolve_facets path applies (including its unknown-country
    ValueError, which stays the honest failure for a mis-typed name).

    S13: the token predicate UNDERCOUNTS (city-only dialects — Netflix
    writes locationsText='Los Gatos' with no US token, no server country;
    ~200 real US postings silently dropped). The honest classifier is the
    DETAIL payload's jobPostingInfo.country — see detail_country(). The
    token filter stays as the 3-strike fallback + the fast path where
    boards carry explicit country codes (tencent/jd ISO dialects).
    """
    has_country_facet = any(
        f.get("facetParameter") == _FACET_COUNTRY
        for f in _flatten_facets(first))
    if has_country_facet or not (country or "").strip():
        return None
    return lambda row: _row_in_country(row, country)


# S13 detail-based country classification — the authoritative signal.
# Workday details carry jobPostingInfo.country = {descriptor, id} (and
# jobRequisitionLocation.country adds alpha2Code) on EVERY board,
# including the city-only-dialect ones that defeat the list-level token
# predicate (netflix wd108: 'Los Gatos' / '2 Locations' rows classify
# cleanly as 'United States of America' / 'Canada').
_US_COUNTRY_NAMES = frozenset((
    "united states", "united states of america", "usa", "us",
    "u.s.", "u.s.a", "u.s.a.", "america",
))

# S15 greenhouse dialect ladder — rung-3 state tokens. US state
# abbreviations + full state names (lowercased), the last-resort US
# evidence for office/location strings that carry no country phrase
# (byd '…, CA 95337' offices, baidu 'Los Angels, CA' locations).
# Deliberately NOT including Canadian provinces (ON/BC/QC are not US
# state abbrevs) and NOT city names — that is the guessing line.
_US_STATE_TOKENS = frozenset(
    ["al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga",
     "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md",
     "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
     "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
     "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
     "dc", "district of columbia",
     "alabama", "alaska", "arizona", "arkansas", "california",
     "colorado", "connecticut", "delaware", "florida", "georgia",
     "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
     "kentucky", "louisiana", "maine", "maryland", "massachusetts",
     "michigan", "minnesota", "mississippi", "missouri", "montana",
     "nebraska", "nevada", "new hampshire", "new jersey",
     "new mexico", "new york", "north carolina", "north dakota",
     "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
     "south carolina", "south dakota", "tennessee", "texas", "utah",
     "vermont", "virginia", "washington", "west virginia",
     "wisconsin", "wyoming"])


def detail_country(payload: Optional[dict]) -> str:
    """Normalized country descriptor from a detail payload ('' when
    unknown). Prefers jobPostingInfo.country.descriptor, falls back to
    jobRequisitionLocation.country (descriptor or alpha2Code)."""
    if not isinstance(payload, dict):
        return ""
    jpi = payload.get("jobPostingInfo") or {}
    c = jpi.get("country")
    if isinstance(c, dict) and isinstance(c.get("descriptor"), str) \
            and c["descriptor"].strip():
        return c["descriptor"].strip().lower()
    jrl = jpi.get("jobRequisitionLocation")
    if isinstance(jrl, dict):
        c2 = jrl.get("country")
        if isinstance(c2, dict):
            d = c2.get("descriptor") or c2.get("alpha2Code") or ""
            if isinstance(d, str) and d.strip():
                return d.strip().lower()
    return ""


def country_str_matches(got: str, country: str) -> bool:
    """String-level normalized country match (for feed/state records that
    carry a plain country descriptor, e.g. the watch's enriched
    records). 'united states' ↔ 'USA' / 'US' / 'United States of
    America' / 'America' all agree; multi-word names phrase-match."""
    got = (got or "").strip().lower()
    base = (country or "").strip().lower()
    if not got or not base:
        return False
    if got == base:
        return True
    if base == "united states" and got in _US_COUNTRY_NAMES:
        return True
    if got == "united states" and base in _US_COUNTRY_NAMES:
        return True
    singles, phrases = _country_matchers(base)
    if got in singles:
        return True
    got_toks = set(re.split(r"[\s\-]+", got))
    return any(all(t in got_toks for t in ph) for ph in phrases)


def detail_in_country(payload: Optional[dict], country: str) -> bool:
    """Is this detail's country == `country` (normalized)? Uses the
    alias table so 'united states' matches 'USA' / 'US' / 'United
    States of America' descriptors."""
    return country_str_matches(detail_country(payload), country)

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


def facet_census(payload: dict) -> dict[str, list[dict]]:
    """Flatten a page-0 payload's facet blocks into
    ``{facetParameter: [{"descriptor", "id", "count"}, …]}`` — the
    board-composition census (S8-D gap #4: per-value counts for countries /
    sites / Office-Remote / timeType / workerSubType / jobFamilyGroup,
    previously discarded by every consumer). Counts reflect whatever
    filters the given page-0 payload carried."""
    census: dict[str, list[dict]] = {}
    for f in _flatten_facets(payload):
        param = f.get("facetParameter")
        if not param:
            continue
        for v in f.get("values") or []:
            if not (isinstance(v, dict) and v.get("descriptor") is not None):
                continue      # nested facet-group placeholders
            census.setdefault(param, []).append({
                "descriptor": v.get("descriptor"),
                "id": v.get("id"),
                "count": int(v.get("count") or 0),
            })
    return census


def facet_values(payload: dict, param: str) -> list[tuple[str, str, int]]:
    """``[(descriptor, facet_id, count)]`` for one facet parameter from a
    page-0 payload — the enumeration input for facet-partitioned listing
    (the 2,000-cap recovery) and facet tagging."""
    return [(v["descriptor"], v["id"], v["count"])
            for v in facet_census(payload).get(param, [])]


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
    every field the API returns, future-proofing the enrichment.

    Failure convention (S9-audit F3/C1, the info:{} zombie): ANY
    not-usable response returns None — a transport error, a non-dict
    body, or an HTTP-200 dict whose jobPostingInfo is absent/empty (a
    req taken down between list and detail GET serves exactly that
    shape). Callers translate None into their error path (board_dump's
    detail_unreachable + attempts/3-strike; the watch's error record)
    instead of writing a never-settled {"reqId", "info": {}} record
    that re-fetches forever and ships detailError=""."""
    tenant, instance, site = board
    try:
        payload = fetch_json(
            f"{_cxs_base(tenant, instance)}{site}{external_path}",
            cfg=cfg, headers={"Accept": "application/json"})
    except Exception:  # noqa: BLE001 — detail is enrichment, never fatal
        return None
    if not isinstance(payload, dict):
        return None
    info = payload.get("jobPostingInfo")
    if not isinstance(info, dict) or not info:
        return None    # 200-without-jobPostingInfo — error, not success
    return payload


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
        # ADAPTIVE step (B2 — S9-audit F3): advance by cards RECEIVED,
        # never a fixed _PAGE_LIMIT step — a short page mid-list followed
        # by a fixed step silently SKIPS postings (the dump primitive
        # _paginate does exactly this; iter_board_postings' contract
        # warns about it).
        offset += len(postings)
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

    Raises ValueError (message lists available countries) when a
    requested facet label doesn't exist ON A BOARD THAT HAS THE FACET —
    callers translate to their own contract. S12 multi-company: when
    the board exposes NO country facet at all, the country request is
    legitimate but un-appliable server-side — returns
    (facets-without-country, country_client=True) and the caller is
    expected to consult client_country_filter() for the row predicate.
    """
    facets: dict[str, list[str]] = {}
    country_client = False
    if country:
        cid = _facet_id(first, _FACET_COUNTRY, country)
        if cid:
            facets[_FACET_COUNTRY] = [cid]
        elif any(f.get("facetParameter") == _FACET_COUNTRY
                 for f in _flatten_facets(first)):
            raise ValueError(
                f"country {country!r} not a facet "
                f"(available: {', '.join(_country_names(first))})")
        else:
            country_client = True
    if time_type:
        tid = _facet_id(first, _FACET_TIME, time_type)
        if not tid:
            raise ValueError(f"timeType {time_type!r} not a facet")
        facets[_FACET_TIME] = [tid]
    return facets, country_client


def _row_of(board: tuple[str, str, str], p: dict) -> tuple[str, dict]:
    """One CXS list posting → (reqId, row) with company + canonical url."""
    tenant, instance, site = board
    rid = (p.get("bulletFields") or [None])[0] \
        or p.get("externalPath", "")
    row = dict(p)
    row["reqId"] = rid
    row["company"] = company_display(tenant)
    row["url"] = (f"{_board_base_url(tenant, instance, site)}"
                  f"{p.get('externalPath', '')}")
    return rid, row


def _paginate(board: tuple[str, str, str], facets: dict, first: dict,
              cfg: Config, sleep_s: float, progress_every: int,
              progress_label: str,
              row_filter=None) -> tuple[dict[str, dict], dict]:
    """The plain total-bounded pagination loop (page-0 total trusted,
    adaptive offset, reqId dedup, B1 partial-never-raise). No 2,000-cap
    guard — callers needing it go through ``iter_board_postings``.
    ``row_filter`` (S12): optional client-side predicate — a row that
    fails it is dropped from the accumulation (the page is still fully
    consumed, so offsets stay honest). meta["client_filtered"] counts
    the drops."""
    total = int(first.get("total") or 0)
    rows: dict[str, dict] = {}
    dropped = 0
    offset, pages = 0, 0
    postings = first.get("jobPostings") or []
    while True:
        pages += 1
        for p in postings:
            rid, row = _row_of(board, p)
            if rid in rows:
                continue                      # wrap-past-total duplicate
            if row_filter is not None and not row_filter(row):
                dropped += 1
                continue
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
                          "pages": pages, "client_filtered": dropped}
        postings = nxt.get("jobPostings") or []
        time.sleep(sleep_s)
    return rows, {"complete": total <= len(rows) + dropped or not total,
                  "total": total, "pages": pages,
                  "client_filtered": dropped}


def _iter_partitioned(board: tuple[str, str, str], facets: dict,
                      first: dict, cfg: Config, sleep_s: float,
                      progress_every: int, progress_label: str,
                      row_filter=None
                      ) -> tuple[dict[str, dict], dict]:
    """Capped-total recovery (S8-D 1g): enumerate the board as a UNION of
    per-facet-value sub-lists, each below the 2,000 cap, deduped by reqId.

    Partition facet = the first candidate parameter the caller has NOT
    already filtered (countries first — every country filter verified
    < 2,000 on nvidia.wd5). Sub-list facets = caller's facets + one value.
    Bounded: a sub-list that ALSO reports 2,000 raises RuntimeError —
    never recurses silently. A partition page-0 network failure follows
    B1 (partial rows, complete=False), never raises mid-list.
    """
    total = int(first.get("total") or 0)
    values_by_param = {
        p: [v for v in facet_values(first, p) if v[2]]
        for p in _PARTITION_FACETS}
    param = next((p for p in _PARTITION_FACETS
                  if p not in facets and values_by_param[p]), None)
    if param is None:
        raise RuntimeError(
            f"CXS total is capped at {_CAPPED_TOTAL} and no unfiltered "
            f"facet with values is available to partition by — cannot "
            f"enumerate this board exhaustively; narrow the caller's "
            f"facets (e.g. per-country) and retry")
    rows: dict[str, dict] = {}
    pages = 0
    complete = True
    partitions: list[dict] = []
    # seed with the capped page-0's own postings (definitely on the board;
    # partitions re-serve them — setdefault keeps the first sighting, and
    # they survive even if a later partition page-0 fails per B1)
    for p in first.get("jobPostings") or []:
        rid, row = _row_of(board, p)
        if row_filter is not None and not row_filter(row):
            continue
        rows.setdefault(rid, row)
    for descriptor, vid, _count in values_by_param[param]:
        sub_facets = dict(facets)
        sub_facets[param] = [vid]
        try:
            sub_first = _page(board, sub_facets, 0, cfg)
        except Exception:                   # B1: partial, never raise
            complete = False
            break
        sub_total = int(sub_first.get("total") or 0)
        if sub_total == _CAPPED_TOTAL:
            raise RuntimeError(
                f"CXS total is capped at {_CAPPED_TOTAL} even for "
                f"partition {param}={descriptor!r} — per-value "
                f"partitioning cannot enumerate this board; narrow the "
                f"caller's facets (e.g. per-country dumps) and retry")
        sub_rows, sub_meta = _paginate(
            board, sub_facets, sub_first, cfg, sleep_s, progress_every,
            f"{progress_label}[{param}={descriptor}]",
            row_filter=row_filter)
        pages += sub_meta["pages"]
        complete = complete and sub_meta["complete"]
        before = len(rows)
        for rid, row in sub_rows.items():
            rows.setdefault(rid, row)       # union-dedupe by reqId
        partitions.append({"facet": param, "value": descriptor,
                           "total": sub_total, "rows": len(sub_rows),
                           "new": len(rows) - before})
        print(f"[{progress_label}] partition {param}={descriptor}: "
              f"{sub_total} total, +{len(rows) - before} new "
              f"({len(rows)} accumulated)", flush=True)
        time.sleep(sleep_s)
    print(f"[{progress_label}] capped-total recovery: {len(rows)} unique "
          f"rows via {len(partitions)} {param} partitions (sub-total sum "
          f"{sum(p['total'] for p in partitions)})", flush=True)
    return rows, {"complete": complete, "total": total, "pages": pages,
                  "total_capped": True, "partition_facet": param,
                  "partitions": partitions}


def iter_board_postings(board: tuple[str, str, str], facets: dict,
                        first: dict, cfg: Optional[Config] = None,
                        sleep_s: float = 0.2,
                        progress_every: int = 0,
                        progress_label: str = "list",
                        board_facets: Optional[dict] = None,
                        row_filter=None,
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
    - 2,000-cap guard (S8-D 1g): a page-0 total of EXACTLY 2,000 means
      the server capped the count — plain pagination would silently
      truncate. Logs a LOUD warning, sets meta["total_capped"]=True and
      falls back to facet-partitioned enumeration (per value of the first
      facet parameter the caller has NOT filtered — countries first),
      union-deduped by reqId. A partition that ALSO reports 2,000 raises
      RuntimeError (never recurses silently).
    - meta["facets"]: the page-0 facet census ({param: [{descriptor, id,
      count}]}). Board-wide when invoked via ``list_board`` (which passes
      the discovery page-0's census); the caller-filtered census is
      additionally exposed as meta["facets_filtered"] by ``list_board``.
      Additive keys — old consumers see only complete/total/pages.
    Returns (rows: {reqId: row}, meta: {"complete", "total", "pages"}).
    """
    cfg = cfg or Config()
    total = int(first.get("total") or 0)
    if total == _CAPPED_TOTAL:
        print(f"[{progress_label}] WARNING: page-0 total is exactly "
              f"{_CAPPED_TOTAL} — the CXS server CAPS `total` at 2,000 "
              f"(S8-D quirk 1g): plain pagination would SILENTLY TRUNCATE "
              f"this listing. Falling back to facet-partitioned "
              f"enumeration …", file=sys.stderr, flush=True)
        rows, meta = _iter_partitioned(board, facets, first, cfg, sleep_s,
                                       progress_every, progress_label,
                                       row_filter=row_filter)
    else:
        rows, meta = _paginate(board, facets, first, cfg, sleep_s,
                               progress_every, progress_label,
                               row_filter=row_filter)
    meta["facets"] = (board_facets if board_facets is not None
                      else facet_census(first))
    return rows, meta


def list_board(spec: str, *, country: Optional[str] = None,
               time_type: Optional[str] = None,
               cfg: Optional[Config] = None, sleep_s: float = 0.2,
               progress_every: int = 0,
               progress_label: str = "list",
               client_filter: bool = True,
               ) -> tuple[dict[str, dict], dict]:
    """Convenience: parse spec → page-0 → resolve facets → refetch →
    iter. ValueError on unknown facet; (partial, complete=False) on
    mid-list failure; page-0 fetch errors propagate to the caller.
    Returns (rows, meta: {"complete", "total", "pages", "facets"
    [, "facets_filtered"[, "total_capped", …]]}) — meta["facets"] is the
    BOARD-WIDE census from the discovery page-0 (even when filters are
    applied); meta["facets_filtered"] carries the caller-filtered census
    when facets were applied (both additive, S8-D gap #4).

    S13: meta["country_client"] reports whether this board has no
    country facet (client-classification mode). In that mode
    client_filter=False DISABLES the list-level token predicate — the
    caller intends to classify by detail payload instead (the honest
    path: city-only dialects like 'Los Gatos' carry no US token but the
    detail's jobPostingInfo.country is authoritative)."""
    cfg = cfg or Config()
    board = parse_board(spec)
    first = _page(board, {}, 0, cfg)
    facets, country_client = resolve_facets(board, first, country,
                                            time_type)
    row_filter = client_country_filter(country, first) \
        if (country_client and client_filter) else None
    if country_client:
        print(f"[{progress_label}] NOTE: board {spec!r} exposes no "
              f"country facet — country {country!r} is "
              f"{'token-filtered' if row_filter else 'detail-classified'}"
              f" client-side (S12/S13)", file=sys.stderr, flush=True)
    board_facets = facet_census(first)
    iter_first = first
    if facets:
        iter_first = _page(board, facets, 0, cfg)
    rows, meta = iter_board_postings(
        board, facets, iter_first, cfg=cfg, sleep_s=sleep_s,
        progress_every=progress_every, progress_label=progress_label,
        board_facets=board_facets, row_filter=row_filter)
    if facets:
        meta["facets_filtered"] = facet_census(iter_first)
    meta["country_client"] = bool(country_client)
    return rows, meta


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
