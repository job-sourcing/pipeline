#!/usr/bin/env python3
"""role_sweep.py — the S36 role-keyed PM sweep (D-S36-1, DESIGN-S36 v2.1).

The role campaign inverts the company keying: queries are ROLE
phrases, the universe is every company that surfaces. Layers (§3):

  L0   bundle PM projection (ingest/data/ui/jobs.json — the manifest-
       derived canonical bundle; companyKey + jobFamilyGroup reuse)
  L1.5 Adzuna US + Jooble US + jobspy→Indeed  (keyed/keyless volume)
  L1   LinkedIn-guest keyword feeds            (depth, JD-less, the
       429 ladder; page-0-per-variant budget)
  L2   WWR RSS + remoteok/jobicy/arbeitnow     (keyless closure; HN
       v1.5 — adapter exists, parse cost deferred)
  L3   Google-Jobs role queries                (firecrawl, behind the
       credit preflight; outage-tolerant)

Outputs (§5 — the new-artifact triple rule):
  ingest/data/roles/pm_inventory.csv + pm_dropped.csv +
    pm_coverage.json + pm_envelope.json
  ingest/data/board_watch/roles:pm.runs.jsonl (drift ledger)
  ingest/data/board_watch/drift_baseline.json  (roles:pm series fold)
  ingest/data/ui/roles-pm.json                 (UI bundle)

Failure doctrine: per-source fail-soft (dead engine skipped,
reported in the envelope); all sources dead in a LAYER with zero rows
across ALL layers (minus L0) = fail-loudly (exit 1, outputs from the
last good run left untouched — write-atomic via tmp+rename).

Usage:
  python3 scripts/role_sweep.py [--full]   # --full = core + tail variants
  python3 scripts/role_sweep.py --gj-only  # the firecrawl recovery probe
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# ── S41: the jd_cache consult (DESIGN-S41 §1.2/§1.5) ──────────────────────
# Keyed (source, url) — NOT rid: 89% of refetch targets carry row-hash
# ids whose basis embeds the mutable 'posted' (S41-R1 F2); every
# addressable pending row has a url. The cache is written by the
# jd-refetch lane (or any local leg — transport-agnostic, committed).
_JD_CACHE_PATH = REPO / "ingest/data/roles/jd_cache.jsonl"
_JD_CACHE: dict[str, dict] = {}
_STOP = frozenset(
    ("the and for with your you our that this from are have will "
     "about their they what when where who all can into not but job "
     "jobs work working company team role").split()
    + ["about", "please", "apply", "now", "more", "join", "us", "we",
       "to"])


def _jd_cache_key(source: str, url: str) -> str:
    return f"{source}|{url}"


def _load_jd_cache() -> dict[str, dict]:
    """Idempotent load (the sweep calls once; tests reset via reload)."""
    global _JD_CACHE
    if _JD_CACHE:
        return _JD_CACHE
    if _JD_CACHE_PATH.exists():
        try:
            for line in _JD_CACHE_PATH.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                k = _jd_cache_key(d.get("source", ""), d.get("url", ""))
                prev = _JD_CACHE.get(k)
                # union-merge: newest fetched_at wins per key
                if not prev or (d.get("fetched_at", "")
                                >= prev.get("fetched_at", "")):
                    _JD_CACHE[k] = d
        except OSError:
            pass
    return _JD_CACHE


def _jd_overlap_ok(old_desc: str, new_desc: str, title: str) -> bool:
    """Consult-time sanity (S41-R1 F3): a garbage-longer capture must
    never replace a clean snippet. The API snippet is normally a
    substring/paraphrase of the full JD — require meaningful token
    overlap; when the row has no snippet, fall back to title/company
    tokens (≥2 of them in the fetched text)."""
    toks = lambda s: {w for w in re.findall(r"[a-z]{4,}", s.lower())
                      if w not in _STOP}
    new_t = toks(new_desc)
    if old_desc and len(old_desc) >= 30:
        old_t = toks(old_desc)
        return len(old_t) > 0 and len(old_t & new_t) / len(old_t) >= 0.25
    t_t = toks(title)
    return len(t_t) >= 2 and len(t_t & new_t) >= 2
sys.path.insert(0, str(REPO / "ingest"))

from jobsearch.rolefamilies import (  # noqa: E402
    DIVISION_ALLOWLIST,
    PM_QUERY_CORE,
    PM_QUERY_TAIL,
    classify_family,
    g1_fires,
    g1b_fires,
    is_anonymized_employer,
    is_board_employer,
    is_screening_post,
    is_staffing_company,
    pm_seniority,
    sector_verdict,
    title_core,
    title_core_family,
    family_spec,
)
from jobsearch import company_seeds as _seeds  # noqa: E402  # S40 A3

ROLES_DIR = REPO / "ingest/data/roles"
WATCH_DIR = REPO / "ingest/data/board_watch"
UI_DIR = REPO / "ingest/data/ui"
BASELINE = WATCH_DIR / "drift_baseline.json"
LEDGER = WATCH_DIR / "roles:pm.runs.jsonl"
BUNDLE = UI_DIR / "jobs.json"

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

# Layer ranks for the merge priority (design §2.4: boards canonical,
# JD-carrying layers outrank JD-less): L0 > L3 > L1.5 > L1 > L2
LAYER_RANK = {"l0": 0, "l3": 1, "l1.5": 2, "l1": 3, "l2": 4}
CREDIT_PREFLIGHT_MIN = 200

# ── S46-B4 §3.3: the geo ladder (the catch-all lane's query-time scopes) ────
# Tiers are QUERY-time scopes, not post-filters (§3.0 law 3): t1a/t1b =
# the CA metro runs (adzuna where={City}, CA + distance=30; the other
# location-aware fetchers location={City}, CA), t2 = California
# statewide (adzuna where=California with NO distance — state-level
# geocode, 5,103 measured; region strings return 0), t3 = US-wide (the
# pre-B4 default, bit-identical when --geo-ladder is not passed), t4 =
# remote (adzuna what_and=remote — where=Remote 400s, the adzuna.py
# 76-81 doctrine; careerjet location=remote; jooble/indeed client-side
# remote flag). Every row carries its tier stamp at fetch time; tiers
# union through the existing merge ladder.
GEO_TIER_RANK = {"t1a": 0, "t1b": 1, "t2": 2, "t3": 3, "t4": 4}
_GEO_WIRED = ("adzuna", "careerjet", "jooble", "indeed")
_T1A_METROS = ("Sacramento", "San Francisco", "Los Angeles",
               "San Diego")            # §3.3: the 4 weekly CA metros
_T1B_METROS = ("Irvine", "Riverside", "Fresno",
               "Bakersfield")          # §3.3: the 4 monthly CA metros
# the B4 lane (§3.4/§3.7): the adzuna category param + the per-row tag
# stamp apply to gen/dmm ONLY — pm keeps the S41 identity doctrine
# (bit-identical default behavior, no param, no stamp)
_B4_FAMILIES = ("gen", "dmm")


def _geo_tier_config(tier: str, family: str) -> dict:
    """[S46-B4 §3.3/§3.4] the tier → fetcher-argument mapping.

    Query-time scopes, not post-filters (§3.0 law 3). Returns:
      scopes  — [(location, distance)] pairs the location-aware
                fetchers iterate; None location = US-wide (t3/t4 — the
                t3 default is bit-identical with the pre-B4 behavior)
      remote  — the t4 flag (adzuna what_and=remote; careerjet
                location="remote" via the scope string; jooble/indeed
                the client-side filter the fetchers grew)
      category — the adzuna sector filter (§3.4): gen admin-jobs on
                 the METRO tiers only (the §5-gate-4 recommendation:
                 filtered where noise is worst, unfiltered at t2/t3);
                 dmm pr-advertising-marketing-jobs at every tier (the
                 5.1x measured cut)
    """
    if tier == "t3":
        return {"scopes": [(None, None)], "remote": False,
                "category": "pr-advertising-marketing-jobs"
                            if family == "dmm" else None}
    if tier == "t4":
        return {"scopes": [("remote", None)], "remote": True,
                "category": "pr-advertising-marketing-jobs"
                            if family == "dmm" else None}
    if tier == "t2":
        return {"scopes": [("California", 0)], "remote": False,
                "category": "pr-advertising-marketing-jobs"
                            if family == "dmm" else None}
    metros = _T1A_METROS if tier == "t1a" else _T1B_METROS
    return {"scopes": [(f"{m}, CA", 30) for m in metros], "remote": False,
            "category": "admin-jobs" if family == "gen"
                        else "pr-advertising-marketing-jobs"}

# ── env ─────────────────────────────────────────────────────────────────────


def _env(name: str) -> str:
    v = os.environ.get(name, "")
    if not v and (REPO / "ingest/.env").exists():
        for line in (REPO / "ingest/.env").read_text().splitlines():
            if line.startswith(f"{name}="):
                v = line.split("=", 1)[1].strip().strip('"')
    return v


def _firecrawl_keys() -> list[str]:
    """[S47 fleet] ALL firecrawl keys in rotation order — mirrors
    site_boards._firecrawl_keys (S46): env FIRECRAWL_KEYS (comma/space
    separated) → env FIRECRAWL_API_KEY → ingest/.env lines
    (FIRECRAWL_KEYS= then FIRECRAWL_API_KEY=, in file order; multiple
    lines accumulate). Deduped, order-preserving. The org lanes get the
    fleet via the INGEST_ENV-materialized ingest/.env (the vault blob
    carries FIRECRAWL_KEYS since 2026-10-24)."""
    keys: list[str] = []

    def _add(val: str) -> None:
        for k in re.split(r"[,\s]+", val or ""):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)

    _add(os.environ.get("FIRECRAWL_KEYS", ""))
    _add(os.environ.get("FIRECRAWL_API_KEY", ""))
    if (REPO / "ingest/.env").exists():
        for line in (REPO / "ingest/.env").read_text().splitlines():
            line = line.strip()
            if line.startswith("FIRECRAWL_KEYS="):
                _add(line.partition("=")[2].strip())
            elif line.startswith("FIRECRAWL_API_KEY="):
                _add(line.partition("=")[2].strip())
    return keys


def _fleet_credits() -> tuple[int, str]:
    """[S47 fleet] Sum remaining credits across the WHOLE fleet (the
    single-key preflight dies at key[0] exhaustion while 3 funded keys
    idle — the S47-A1/A2-1 audit finding). Returns (total, status);
    per-key failures are tolerated (one dead key ≠ fleet-dead)."""
    keys = _firecrawl_keys()
    if not keys:
        return 0, "no-key"
    total = 0
    dead = 0
    for k in keys:
        try:
            d = _http_json("https://api.firecrawl.dev/v1/team/credit-usage",
                           headers={"Authorization": f"Bearer {k}"})
            total += int((d.get("data") or {}).get("remaining_credits", 0)
                         or 0)
        except Exception:                       # noqa: BLE001
            dead += 1                           # best-effort per key
    status = "ok" if total > 0 else f"fleet-exhausted"
    if dead and total:
        status += f" ({dead}/{len(keys)} key(s) unreachable)"
    return total, status


def _fc_scrape(payload: dict, timeout: int = 120) -> tuple[dict | None, str]:
    """[S47 fleet] firecrawl /scrape with KEY ROTATION — tries each
    fleet key in order; on per-key auth/quota failure (401/402/403 or
    the API's payment_required error) moves to the next key. Returns
    (parsed_response | None, status). A fleet-wide failure returns
    (None, 'fleet:<reason>') so callers record the honest verdict."""
    keys = _firecrawl_keys()
    if not keys:
        return None, "no-key"
    last = ""
    for k in keys:
        try:
            d = _http_json(
                "https://api.firecrawl.dev/v2/scrape",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {k}"}, timeout=timeout)
        except urllib.error.HTTPError as h:
            last = f"http{h.code}"
            if h.code in (401, 402, 403):       # key-level: rotate
                continue
            return None, f"http{h.code}"        # e.g. 500: caller retries
        except Exception as exc:                # noqa: BLE001
            last = f"error:{type(exc).__name__}"
            continue                             # network-ish: next key
        if not d.get("success"):
            err = str(d.get("error") or "")
            if ("payment" in err.lower()
                    or "credit" in err.lower()
                    or "quota" in err.lower()):
                last = "key-exhausted"
                continue                         # key-level: rotate
            return None, f"api-fail:{err[:40]}"
        return d, "ok"
    return None, f"fleet:{last or 'exhausted'}"


# short aliases (cross-module use — jd_refetch imports these)
_fc_keys = _firecrawl_keys


# ── usgate (design §4 — per-source priors, no silent defaults) ──────────────

_US_STATE_TOKENS = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY DC".split())
_US_STATE_LC = {t.lower() for t in _US_STATE_TOKENS}   # S39 F0: the case fix
_US_WORDS = {"us", "usa", "u.s.", "united", "states", "america", "u.s",
             "american"}
_REMOTE_WORDS = ("remote", "work from home", "wfh", "anywhere",
                 "distributed", "telecommut")

# S39 §A1/F4: the US-city allowlist (bare city names — "San Francisco" has
# no state token; careerjet US rows carry bare state names, WaaS carries
# bare cities). Multi-word cities match as 2-grams; single-token cities
# require len>2 (la/sf excluded — collision guard). Deliberately absent:
# vienna/newark/sterling (foreign collisions, F4).
_US_CITIES = {
    "new york", "new york city", "nyc", "los angeles", "chicago",
    "houston", "phoenix", "philadelphia", "san antonio", "san diego",
    "dallas", "san jose", "austin", "jacksonville", "san francisco",
    "columbus", "fort worth", "charlotte", "indianapolis", "seattle",
    "denver", "washington dc", "boston", "el paso", "nashville",
    "detroit", "oklahoma city", "las vegas", "portland", "louisville",
    "memphis", "baltimore", "milwaukee", "albuquerque", "tucson",
    "fresno", "sacramento", "kansas city", "mesa", "atlanta", "omaha",
    "raleigh", "long beach", "colorado springs", "virginia beach",
    "miami", "oakland", "minneapolis", "tulsa", "bakersfield", "wichita",
    "arlington", "aurora", "tampa", "new orleans", "cleveland",
    "honolulu", "anaheim", "lexington", "orlando", "pittsburgh",
    "cincinnati", "st louis", "saint louis", "st paul", "corpus christi",
    "salt lake city", "slc", "provo", "boise", "santa monica",
    "mountain view", "palo alto", "san mateo", "redwood city", "berkeley",
    "cupertino", "sunnyvale", "santa clara", "san bruno", "burlingame",
    "menlo park", "belmont", "foster city", "campbell", "milpitas",
    "bay area", "sf bay area", "silicon valley", "cambridge ma",
    "somerville ma", "brookline ma", "durham nc", "cary nc",
    "bellevue wa", "kirkland wa", "redmond wa", "round rock tx",
    "plano tx", "frisco tx", "irving tx", "allen tx", "framingham ma",
    "waltham ma", "burlington ma", "needham ma", "woburn ma",
    "billerica ma", "westford ma", "newton ma", "shrewsbury ma",
    "marlborough ma", "hudson ma", "westborough ma", "sterling va",
    "herndon va", "reston va", "arlington va",
}
# foreign-city guard (F4): these as the loc's LEADING comma-segment →
# non_us even when a US state token follows ('Chennai, TN' / 'Perth, WA'
# / 'Delhi, IN' — foreign states sharing US state codes)
_FOREIGN_CITY_HEADS = {
    "chennai", "perth", "delhi", "new delhi", "mumbai", "bangalore",
    "bengaluru", "hyderabad", "kolkata", "calcutta", "coimbatore",
    "madurai", "toronto", "vancouver", "montreal", "calgary", "ottawa",
    "edmonton", "quebec", "sydney", "melbourne", "brisbane", "adelaide",
    "auckland", "wellington", "dublin", "manchester uk", "london",
    "berlin", "munich", "amsterdam", "paris", "singapore", "tokyo",
    "hong kong", "shanghai", "beijing", "shenzhen",
}
# country markers (F4): single-word markers are WORD-BOUNDARY token
# matches (substring 'india' would eat 'Indianapolis'); multi-word
# markers match as substrings
_FOREIGN_MARKERS_1W = {
    "england", "scotland", "wales", "india", "australia", "canada",
    "germany", "netherlands", "france", "spain", "italy", "portugal",
    "brazil", "japan", "singapore", "philippines", "vietnam", "thailand",
    "malaysia", "indonesia", "london", "berlin", "munich", "amsterdam",
    "paris", "tokyo", "shanghai", "beijing", "shenzhen", "toronto",
    "vancouver", "montreal", "calgary", "sydney", "melbourne", "dublin",
}
_FOREIGN_MARKERS_MW = (
    "united kingdom", "new zealand", "hong kong", "republic of ireland",
)
# S39: bare US state FULL NAMES (careerjet's US rows carry bare state
# names — the 1,884-row measurement depends on this)
_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
}


def _classify_loc(loc: str) -> str:
    """'us' | 'remote' | 'non_us' | 'ambiguous' — the client-side
    classify for sources without a US-scoped endpoint.

    S39 §A1: the state-token CASE FIX (the set holds uppercase, the
    compare lowercased — never matched, shipped S36, silently dropped
    ~2,555 pm-family rows/sweep; audit/s39/usgate-bug-measurement.txt)
    + the bare-city allowlist + the F4 foreign-collision guards.
    'N Locations' (BuiltIn multi-loc cards) → ambiguous, never non_us.
    """
    l = (loc or "").lower().strip()
    if not l:
        return "ambiguous"
    if re.match(r"^\d+\s+locations?$", l):
        return "ambiguous"                  # F11: BuiltIn multi-loc cards
    if any(w in l for w in _REMOTE_WORDS):
        return "remote"
    toks = set(re.split(r"[^a-z.]+", l))
    # foreign markers first (F4): word-boundary single-word + substring
    # multi-word
    if (toks & _FOREIGN_MARKERS_1W or
            any(m in l for m in _FOREIGN_MARKERS_MW)):
        return "non_us"
    head = re.split(r",", l)[0].strip()     # the leading city segment
    if head in _FOREIGN_CITY_HEADS:
        return "non_us"
    if _US_WORDS & toks:
        return "us"
    for t in re.split(r"[^a-z]+", l):
        if t in _US_STATE_LC:
            return "us"
    if toks & _US_STATE_NAMES:
        return "us"
    for name in _US_STATE_NAMES:            # multi-word state names
        if " " in name and name in l:
            return "us"
    # bare/leading US city names
    for city in ("washington dc", "new york", "san francisco",
                 "salt lake", "kansas city", "st louis", "saint louis",
                 "st paul", "bay area", "silicon valley"):
        if city in l:
            return "us"
    for t in toks:
        if t in _US_CITIES and len(t) > 2:
            return "us"
    words = re.sub(r"[^a-z ]", " ", l).split()
    for i in range(len(words) - 1):
        if f"{words[i]} {words[i+1]}" in _US_CITIES:
            return "us"
    return "non_us"


# ── fetchers (each: -> (rows, verdict); row = the normalized shape) ────────
# row keys: title, company, loc, url, posted (ISO or ''), desc, source,
#           layer, query, rid

def _http_json(url: str, data=None, headers=None, timeout=30):
    req = urllib.request.Request(
        url, headers=headers or UA,
        data=data, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


_ADZUNA_FLEET: dict = {"keys": None, "idx": 0, "dead": set()}


def _adzuna_keys() -> list[tuple[str, str]]:
    """[S49-D4] the adzuna key FLEET — ADZUNA_KEYS="id:key,id:key,..."
    (rotation-ready; each key = +250 req/day). Falls back to the
    legacy single pair (bit-identical when only it exists). Keys hit
    with 410/429 (cap-exceeded / revoked — adzuna's documented quota
    contract) are quarantined dead-for-run so the sweep degrades to
    the remaining keys instead of dying mid-query."""
    if _ADZUNA_FLEET["keys"] is None:
        fleet: list[tuple[str, str]] = []
        raw = _env("ADZUNA_KEYS")
        if raw:
            for chunk in raw.split(","):
                chunk = chunk.strip()
                if ":" in chunk:
                    app_id, key = chunk.split(":", 1)
                    if app_id.strip() and key.strip():
                        pair = (app_id.strip(), key.strip())
                        if pair not in fleet:
                            fleet.append(pair)
        legacy = (_env("ADZUNA_APP_ID"), _env("ADZUNA_API_KEY"))
        if legacy[0] and legacy[1] and legacy not in fleet:
            fleet.append(legacy)
        _ADZUNA_FLEET["keys"] = fleet
    return [k for k in _ADZUNA_FLEET["keys"]
            if k not in _ADZUNA_FLEET["dead"]]


def _adzuna_next_key() -> tuple[tuple[str, str] | None, str]:
    """round-robin pick over the live keys (None + reason when the
    whole fleet is quarantined)."""
    live = _adzuna_keys()
    if not live:
        if _ADZUNA_FLEET["keys"]:
            # the fleet EXISTED but every key is quarantined for this
            # run — the honest cap verdict (the caller degrades to the
            # rows it already holds).
            return None, "quota:all-keys"
        return None, "no-key"
    pair = live[_ADZUNA_FLEET["idx"] % len(live)]
    _ADZUNA_FLEET["idx"] += 1
    return pair, "ok"


def fetch_adzuna(queries: list[str],
                   location: str | None = None,
                   distance: int | None = None,
                   category: str | None = None,
                   what_and: str | None = None) -> tuple[list[dict], str]:
    app_id, key = _env("ADZUNA_APP_ID"), _env("ADZUNA_API_KEY")
    if not (app_id and key) and not _env("ADZUNA_KEYS"):
        return [], "no-key"
    if not (app_id and key):
        # the fleet-only configuration (ADZUNA_KEYS without the legacy
        # pair): seed the fleet and take the first key
        pair, st = _adzuna_next_key()
        if pair is None:
            return [], st
        app_id, key = pair
    rows: list[dict] = []
    # S38 §2a: relevance sort (measured 100% PM-family density p1-50)
    # + max_days_old=45 (the li/indeed freshness doctrine) + 10 pages
    # (weekly cap; density verified far beyond — the cap is politeness).
    # S41 (DESIGN-S41 §3.2): optional geo filter — None = US-wide
    # (every PM-lane call is bit-identical); the industry sweeps pass a
    # region string (adzuna's where= geocoder: city/state shaped).
    # S46-B4 §3.3/§3.4 additions: `distance` (None = the S41/S42
    # industry default 50 — bit-identity for industry_sweep; 0 = the
    # t2 state-level geocode, NO distance param; >0 = the ladder's
    # metro radius), `category` (the only real sector filter, 5.1× cut
    # measured), `what_and` (the t4 remote filter — where=Remote 400s,
    # the adzuna.py:76-81 doctrine). The per-row category tag returns
    # even UNFILTERED (probe §A.1) — captured on every row for the
    # sectorEvidence stamp.
    max_page = 10
    quota_hard = False
    for q in queries:
        for page in range(1, max_page + 1):
            # [S49-D4] per-page key rotation: the round-robin spreads
            # the 250/day cap across the fleet; a 410/429 quarantines
            # THAT key (dead-for-run) and this page retries on the next
            # live key — the sweep degrades gracefully instead of dying.
            pair, pick_st = _adzuna_next_key()
            if pair is None:
                quota_hard = True
                break
            app_id, key = pair
            url = (f"https://api.adzuna.com/v1/api/jobs/us/search/{page}"
                   f"?app_id={app_id}&app_key={key}"
                   f"&what={urllib.parse.quote_plus(q)}"
                   f"&results_per_page=50&max_days_old=45"
                   f"&content-type=application/json")
            if location:
                url += f"&where={urllib.parse.quote_plus(location)}"
                dist = 50 if distance is None else distance
                if dist:
                    url += f"&distance={dist}"
            if category:
                url += f"&category={urllib.parse.quote_plus(category)}"
            if what_and:
                url += f"&what_and={urllib.parse.quote_plus(what_and)}"
            try:
                d = _http_json(url)
            except urllib.error.HTTPError as http_exc:
                if http_exc.code in (410, 429):
                    _ADZUNA_FLEET["dead"].add(pair)
                    continue           # same page, next live key
                if page == 1:
                    return rows, (f"error:page{page}:{q[:12]}:"
                                  f"HTTP{http_exc.code}")
                break                # partial page-run: keep what we have
            except Exception as exc:
                if page == 1:
                    return rows, f"error:page{page}:{q[:12]}:{type(exc).__name__}"
                break                      # partial page-run: keep what we have
            for j in d.get("results", []):
                created = (j.get("created") or "")[:10]
                cat = j.get("category")
                if isinstance(cat, dict):        # {"tag": ..., "label": ...}
                    cat = cat.get("tag") or ""
                elif not isinstance(cat, str):
                    cat = ""
                row = {
                    "title": j.get("title") or "",
                    "company": (j.get("company") or {}).get("display_name") or "",
                    "loc": ((j.get("location") or {}).get("display_name") or ""),
                    "url": j.get("redirect_url") or "",
                    "posted": created,
                    "desc": (j.get("description") or "")[:12000],
                    "source": "adzuna", "layer": "l1.5", "query": q,
                    "rid": f"adz:{j.get('id') or ''}",
                    "adzuna_category": cat,
                }
                # [S49-D4] salary preservation — adzuna's unique data
                # (annualized salary_min/salary_max on the row) was
                # DISCARDED at construction before S49; now it rides
                # the row (emitted gen/dmm-side; the pm artifact stays
                # byte-identical via the writer-level family gate).
                sal_min = j.get("salary_min")
                sal_max = j.get("salary_max")
                if sal_min is not None:
                    row["salary_min"] = sal_min
                if sal_max is not None:
                    row["salary_max"] = sal_max
                rows.append(row)
            if len(d.get("results", [])) < 50:
                break
            time.sleep(1.0)
        if quota_hard:
            break
    if quota_hard:
        return rows, f"quota:all-keys:{len(_ADZUNA_FLEET['dead'])}"
    return rows, "ok"


def fetch_jooble(queries: list[str],
                   location: str | None = None,
                   remote: bool = False) -> tuple[list[dict], str]:
    key = _env("JOOBLE_API_KEY")
    if not key:
        return [], "no-key"
    rows: list[dict] = []
    max_page = 5                        # S38 §2a: depth 2→5
    for q in queries:
        for page in range(1, max_page + 1):
            try:
                d = _http_json(
                    f"https://jooble.org/api/{key}",
                    data=json.dumps({"keywords": q, "location":
                                     location or "United States",
                                     "page": page}).encode(),
                    headers={"Content-Type": "application/json"})
            except Exception as exc:
                if page == 1:
                    return rows, f"error:page{page}:{q[:12]}:{type(exc).__name__}"
                break
            for j in d.get("jobs", []):
                # S46-B4 §3.3 t4: jooble has no remote param — the
                # client-side doctrine (jooble.py:139): "remote" in
                # loc+snippet. US-wide query + the client filter.
                if remote and "remote" not in (
                        (j.get("location") or "") + " "
                        + (j.get("snippet") or "")).lower():
                    continue
                rows.append({
                    "title": j.get("title") or "",
                    "company": j.get("company") or "",
                    "loc": j.get("location") or "",
                    "url": j.get("link") or "",
                    "posted": "",       # jooble carries relative dates only
                    "desc": (j.get("snippet") or "")[:12000],
                    "source": "jooble", "layer": "l1.5", "query": q,
                    "rid": "",
                })
            if not d.get("jobs"):
                break
            time.sleep(1.0)
    return rows, "ok"


def fetch_jobspy_indeed(queries: list[str],
                           location: str | None = None,
                           remote: bool = False) -> tuple[list[dict], str]:
    try:
        from jobspy import scrape_jobs  # noqa: PLC0415
    except ImportError:
        return [], "not-installed"
    rows: list[dict] = []
    for q in queries:                    # S38 §2a: all queries (was [:5])
        try:
            jobs = scrape_jobs(site_name=["indeed"], search_term=q,
                               location=location or "United States",
                               results_wanted=100, hours_old=24 * 30)
            if not hasattr(jobs, "iterrows"):
                continue
            for _, j in jobs.iterrows():
                # S46-B4 §3.3 t4: indeed has no remote query param —
                # the structured is_remote flag comes back per row
                # (jobspy_source.py:197-201: bool, with the loc-text
                # fallback); the t4 tier filters client-side on it.
                loc = j.get("location")
                if remote:
                    flag = j.get("is_remote")
                    if not (flag is True or str(flag).strip().lower()
                            in ("true", "1", "1.0")) and not (
                            isinstance(loc, str)
                            and "remote" in loc.lower()):
                        continue
                rows.append({
                    "title": str(j.get("title") or ""),
                    "company": str(j.get("company") or ""),
                    "loc": "" if not isinstance(loc, str) else loc,
                    "url": str(j.get("job_url") or ""),
                    "posted": str(j.get("date_posted") or "")[:10],
                    "desc": str(j.get("description") or "")[:12000],
                    "source": "indeed", "layer": "l1.5", "query": q,
                    "rid": "",
                })
        except Exception as exc:
            return rows, f"error:{q[:12]}:{type(exc).__name__}"
        time.sleep(2.0)
    return rows, "ok"


def fetch_li_keyword(queries: list[str]) -> tuple[list[dict], str]:
    """L1 depth: page-0 per variant (the politeness budget); the 429
    ladder from S33 (45/60/90 page-0, single rung deeper)."""
    try:
        from curl_cffi import requests as cr  # noqa: PLC0415
    except ImportError:
        return [], "not-installed"
    search = ("https://www.linkedin.com/jobs-guest/jobs/api/"
              "seeMoreJobPostings/search")
    rows: list[dict] = []
    status = "ok"
    for q in queries:
        params = {"keywords": q, "location": "United States",
                  "f_TPR": "r2592000", "sortBy": "DD", "start": "0"}
        code, body = 0, ""
        for sleep_s in (0, 45, 60, 90):
            if sleep_s:
                time.sleep(sleep_s)
            try:
                r = cr.get(search, params=params, impersonate="chrome131",
                           timeout=30, allow_redirects=False,
                           headers={"User-Agent": UA["User-Agent"]})
                code, body = r.status_code, r.text
            except Exception as exc:
                return rows, f"error:{q[:12]}:{type(exc).__name__}"
            if code != 429:
                break
        if code != 200:
            status = f"blocked:{code}:{q[:12]}"
            continue
        for m in re.finditer(
                r'data-entity-urn="urn:li:jobPosting:(\d+)"(.*?)</li>',
                body, re.DOTALL):
            jid, card = m.groups()
            def _txt(pat):
                mm = re.search(pat, card, re.DOTALL)
                return re.sub(r"<[^>]+>", "", mm.group(1)).strip() \
                    if mm else ""
            t = _txt(r"base-search-card__title[^>]*>\s*(.*?)\s*</h3>")
            c = _txt(r"hidden-nested-link[^>]*>\s*(.*?)\s*</a>")
            l = _txt(r"job-search-card__location[^>]*>\s*(.*?)\s*</span>")
            d = re.search(r'datetime="([^"]+)"', card)
            if t:
                rows.append({
                    "title": t, "company": c, "loc": l, "url":
                        f"https://www.linkedin.com/jobs/view/{jid}",
                    "posted": (d.group(1) or "")[:10] if d else "",
                    "desc": "", "source": "li", "layer": "l1",
                    "query": q, "rid": jid,
                })
        time.sleep(3.0)
    return rows, status


def fetch_wwr(family: str = "pm") -> tuple[list[dict], str]:
    """S38 L2 'wwr_rss'. [S49-B / audit F12] the feed IS family-shaped:
    pm = the pinned /categories/remote-product-jobs.rss category feed;
    gen = the all-roles main feed https://weworkremotely.com/remote-jobs.rss
    (live-probed 2026-10-29: 200, 95 items; /categories/remote-jobs.rss
    301s) — the WWR shape carries no client-side filter to drop. Gen
    rows pre-stamp family (the gate-loop bypass)."""
    feed = ("https://weworkremotely.com/categories/"
            "remote-product-jobs.rss" if family == "pm"
            else "https://weworkremotely.com/remote-jobs.rss")
    try:
        req = urllib.request.Request(feed, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            xml = r.read().decode("utf-8", "replace")
    except Exception as exc:
        return [], f"error:{type(exc).__name__}"
    rows = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
        def _tag(t):
            m = re.search(rf"<{t}><!\[CDATA\[(.*?)\]\]></{t}>", item, re.DOTALL)
            if not m:
                m = re.search(rf"<{t}>(.*?)</{t}>", item, re.DOTALL)
            return (m.group(1) if m else "").strip()
        title = _tag("title")
        # WWR shape: "Company: Role (Extra)"
        m = re.match(r"^(.*?):\s*(.*?)(?:\s*\((.*)\))?$", title)
        company, role = (m.group(1), m.group(2)) if m else ("", title)
        row = {
            "title": role, "company": company,
            "loc": "Remote", "url": _tag("link"),
            "posted": (re.search(r"<pubDate>(.*?)</pubDate>", item)
                       and _parse_rss_date(_tag("pubDate"))) or "",
            "desc": _tag("description")[:12000],
            "source": "wwr", "layer": "l2",
            "query": "wwr:product" if family == "pm" else "wwr:all",
            "rid": "",
        }
        if family == "gen":
            row["family"] = "gen"
            row["family_evidence"] = "wwr_remote_jobs"
        rows.append(row)
    return rows, "ok"


def _parse_rss_date(s: str) -> str:
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def fetch_free_trio() -> tuple[list[dict], str]:
    """remoteok + jobicy + arbeitnow (remotive DEAD — retired D-S36-1)."""
    rows, verdicts = [], []
    try:
        d = _http_json("https://remoteok.com/api")
        for j in d:
            if isinstance(j, dict) and j.get("position"):
                rows.append({
                    "title": j["position"],
                    "company": j.get("company") or "",
                    "loc": j.get("location") or "",
                    "url": j.get("url") or "", "posted":
                        (j.get("date") or "")[:10],
                    "desc": (j.get("description") or "")[:12000],
                    "source": "remoteok", "layer": "l2",
                    "query": "remoteok:feed", "rid": j.get("slug") or "",
                })
    except Exception as exc:
        verdicts.append(f"remoteok:{type(exc).__name__}")
    try:
        d = _http_json("https://jobicy.com/api/v2/remote-jobs?count=200")
        for j in d.get("jobs", []):
            rows.append({
                "title": j.get("jobTitle") or "",
                "company": j.get("companyName") or "",
                "loc": j.get("jobGeo") or "",
                "url": j.get("jobUrl") or "", "posted": "",
                "desc": (j.get("jobExcerpt") or "")[:12000],
                "source": "jobicy", "layer": "l2",
                "query": "jobicy:feed", "rid": j.get("id") or "",
            })
    except Exception as exc:
        verdicts.append(f"jobicy:{type(exc).__name__}")
    try:
        d = _http_json("https://www.arbeitnow.com/api/job-board-api")
        for j in d.get("data", []):
            rows.append({
                "title": j.get("title") or "",
                "company": j.get("company_name") or "",
                "loc": j.get("location") or "",
                "url": j.get("url") or "",
                "posted": (j.get("creation_date") or "")[:10],
                "desc": "", "source": "arbeitnow", "layer": "l2",
                "query": "arbeitnow:feed", "rid": "",
            })
    except Exception as exc:
        verdicts.append(f"arbeitnow:{type(exc).__name__}")
    verdict = "ok" if not verdicts else ("partial:" + ",".join(verdicts))
    return rows, verdict


def fetch_gj_role(queries: list[str]) -> tuple[list[dict], str]:
    """L3: Google-Jobs vertical per query — 1 firecrawl credit each
    (behind the credit preflight; outage-tolerant per-source).
    S38 §2a/F17: transport errors retry ×3 (30/45s backoff — proven:
    6/6 queries recovered under backoff this round); cardless shells
    do NOT retry (a shell pages shells again)."""
    if not _firecrawl_keys():
        return [], "no-key"
    # S47 fleet: the preflight sums the WHOLE fleet (a dead key[0]
    # no longer darkens gj while 3 funded keys idle — the S47-A1/A2-1
    # audit finding). CREDIT_PREFLIGHT_MIN gates the fleet total.
    fleet_total, fleet_status = _fleet_credits()
    try:
        if fleet_total < CREDIT_PREFLIGHT_MIN:
            return [], (f"preflight-fail:credits={fleet_total}"
                        f"[{fleet_status}]")
    except Exception:
        pass                       # preflight is best-effort; scrape decides
    rows: list[dict] = []
    status = "ok"
    for q in queries:
        url = ("https://www.google.com/search?q="
               + urllib.parse.quote_plus(q) + "&ibp=htl;jobs&hl=en&gl=us")
        md = ""
        for attempt in range(3):
            d, fc_status = _fc_scrape({"url": url, "formats": ["markdown"],
                                       "onlyMainContent": False,
                                       "waitFor": 5000})
            if d is None:
                status = f"error:{q[:12]}:{fc_status}"
                if attempt < 2:
                    time.sleep(30 + 15 * attempt)
                    continue
                break                        # transport-error ladder exhausted
            if not d.get("success"):
                status = f"api-fail:{q[:12]}"
                if attempt < 2:
                    time.sleep(30 + 15 * attempt)
                    continue
                break
            md = str((d.get("data") or {}).get("markdown") or "")
            if len(md) >= 3000:
                break                        # a real cards page — no retry
            # cardless shell (S33 stub class) — one gentle retry only
            if attempt < 1:
                time.sleep(20)
                continue
            break
        if len(md) < 3000:
            continue                       # shell after retries: skip query
        for m in re.finditer(
                r"^(?P<title>[^\n•·]{3,140})\n+"
                r"(?P<company>[^\n•·]{2,80})\n+"
                r"(?P<loc>[^\n•]{2,80}) • via (?P<via>[^\n]{2,60})\n",
                md, re.MULTILINE):
            g = m.groupdict()
            rows.append({
                "title": g["title"], "company": g["company"],
                "loc": g["loc"], "url": "", "posted": "",
                "desc": md[:12000], "source": "gj", "layer": "l3",
                "query": q, "rid": f"gj:{q}:{g['title'][:40]}",
            })
        time.sleep(2.0)
    return rows, status


# ── S38 fetchers (DESIGN-S38 §1a — posted-contract table honored) ──────────

def _rfc2822_to_iso(s: str) -> str:
    if not s:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s.strip()).strftime("%Y-%m-%d")
    except (TypeError, ValueError, ImportError):
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s.strip()[:32], fmt).strftime(
                "%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _epoch_to_iso(v) -> str:
    try:
        n = int(float(v))
        if n > 10 ** 12:                    # epoch millis
            n //= 1000
        return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def fetch_dice(queries: list[str]) -> tuple[list[dict], str]:
    """S38 L1.5: Dice SERP RSC flight JSON (keyless; the old Dice API is
    dead — engine-census-r2 §1.1). 30 rows/page; extraction walks the
    ESCAPED jobList.data array out of the script payload.

    S39 §A6: html-unescape title/company/loc — the flight JSON carries
    HTML entities ('Procter &amp; Gamble'), which previously leaked
    into company strings and DEFEATED the company_seed_key exact-key
    seeds (T2a's guard finding; now normalized both sides anyway)."""
    import html as _html
    import urllib.error
    ua = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
          "Accept-Language": "en-US,en;q=0.9"}
    rows: list[dict] = []
    max_page = 8
    for q in queries:
        for page in range(1, max_page + 1):
            url = ("https://www.dice.com/jobs?q="
                   + urllib.parse.quote_plus(q)
                   + "&location=United%20States"
                   + (f"&page={page}" if page > 1 else ""))
            try:
                req = urllib.request.Request(url, headers=ua)
                with urllib.request.urlopen(req, timeout=30) as r:
                    html = r.read().decode("utf-8", "replace")
            except Exception as exc:
                if page == 1:
                    return rows, f"error:{q[:12]}:{type(exc).__name__}"
                break
            jobs = _dice_extract_jobs(html)
            if jobs is None:
                if page == 1:
                    return rows, f"no-joblist:{q[:12]}"
                break
            for j in jobs:
                loc = j.get("jobLocation")
                if isinstance(loc, dict):
                    loc = loc.get("displayName") or ", ".join(
                        filter(None, [loc.get("city"),
                                      loc.get("state")])[:2])
                elif not isinstance(loc, str):
                    loc = ""
                rows.append({
                    "title": _html.unescape(j.get("title") or ""),
                    "company": _html.unescape(j.get("companyName") or ""),
                    "loc": _html.unescape(loc)[:80],
                    "url": j.get("detailsPageUrl") or "",
                    "posted": _rfc2822_to_iso(j.get("postedDate") or "")
                    or (j.get("postedDate") or "")[:10],
                    "desc": (j.get("summary") or "")[:12000],
                    "source": "dice", "layer": "l1.5", "query": q,
                    "rid": f"dice:{j.get('id') or j.get('guid') or ''}",
                })
            if len(jobs) < 30:
                break
            time.sleep(1.2)
    return rows, "ok"


def _dice_extract_jobs(html: str):
    """The jobList.data array out of the RSC payload (JSON is escaped
    inside script strings — locate 'jobList', then walk to the matching
    bracket in the ESCAPED text; census probe s38a_probe_dice4)."""
    m = re.search(r'jobList.*?\\"data\\":\[(?=[^\]]*\\"companyName\\")', html)
    if not m:
        return None
    start = m.end() - 1
    depth, i = 0, start
    while i < len(html):
        c = html[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                break
        i += 1
    raw = html[start:i + 1]
    try:
        return json.loads(raw.encode().decode("unicode_escape"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def fetch_careerjet(queries: list[str],
                       location: str | None = None) -> tuple[list[dict], str]:
    """S38 L1.5: Careerjet v4 query API — Basic auth (key:), Referer,
    QUOTED-phrase keywords (unquoted = noise: Floor Manager/Optician),
    location=United States (census s38a_probe_careerjet3)."""
    import base64
    key, ref = _env("CAREERJET_API_KEY"), _env("CAREERJET_REFERER")
    if not (key and ref):
        return [], "no-key"
    ua = "jobsearch/1.0 (job aggregator; server-side)"
    rows: list[dict] = []
    max_page = 5
    for q in queries:
        for page in range(1, max_page + 1):
            params = {"keywords": f'"{q}"',
                      "location": location or "United States",
                      "sort": "relevance", "pagesize": 99, "page": page,
                      "locale_code": "en_US", "user_ip": "127.0.0.1",
                      "user_agent": ua}
            qs = "&".join(f"{k}={urllib.parse.quote(str(v))}"
                          for k, v in params.items())
            try:
                req = urllib.request.Request(
                    f"https://search.api.careerjet.net/v4/query?{qs}",
                    headers={"User-Agent": ua, "Referer": ref,
                             "Authorization": "Basic " + base64.b64encode(
                                 f"{key}:".encode()).decode()})
                with urllib.request.urlopen(req, timeout=30) as r:
                    d = json.loads(r.read().decode())
            except Exception as exc:
                if page == 1:
                    return rows, f"error:{q[:12]}:{type(exc).__name__}"
                break
            jobs = d.get("jobs", [])
            for j in jobs:
                loc = j.get("locations")
                if isinstance(loc, list):           # list-of-dicts or strings
                    loc = (loc[0].get("name", "") if isinstance(
                        loc[0], dict) else str(loc[0] or "")) \
                        if loc else ""
                elif not isinstance(loc, str):
                    loc = ""
                rows.append({
                    "title": j.get("title") or "",
                    "company": j.get("company") or "",
                    "loc": loc[:80],
                    "url": j.get("url") or "",
                    "posted": _rfc2822_to_iso(j.get("date") or ""),
                    "desc": (j.get("description") or "")[:12000],
                    "source": "careerjet", "layer": "l1.5", "query": q,
                    "rid": "",
                })
            if not jobs:
                break
            time.sleep(1.2)
    return rows, "ok"


def fetch_findwork(queries: list[str],
                   all_queries: bool = False) -> tuple[list[dict], str]:
    """S38 L1.5: findwork.dev — search= full-text (count 1,667), title
    field = `role`, JD = `text` (FULL — the highest merge-value tier);
    location param is exact-match-only → usgate runs client-side.
    [S47 F4b/A6] all_queries=True (the B4 families): iterate EVERY
    query (3-page cap each — 19/20 widened core queries previously
    never hit the full-JD source); the pinned single-query mode keeps
    the 17-page behavior bit-identical for pm."""
    key = _env("FINDWORK_API_KEY")
    if not key:
        return [], "no-key"
    rows: list[dict] = []
    q_list = queries if (all_queries and queries) else queries[:1]
    max_page = 3 if all_queries else 17
    for q in q_list:
        seen = 0
        for page in range(1, max_page + 1):
            try:
                req = urllib.request.Request(
                    "https://findwork.dev/api/jobs/?search="
                    + urllib.parse.quote_plus(q if queries
                                              else "product manager")
                    + f"&page={page}",
                    headers={"Authorization": f"Token {key}",
                             "User-Agent": "jobsearch-sweep"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    d = json.loads(r.read().decode())
            except Exception as exc:
                if page == 1:
                    break                        # this query's loss —
                break                            # next query continues
            res = d.get("results", [])
            if not res:
                break
            for j in res:
                rows.append({
                    "title": j.get("role") or "",
                    "company": j.get("company_name") or "",
                    "loc": j.get("location") or "",
                    "url": j.get("url") or j.get("id") or "",
                    "posted": (j.get("date_posted") or "")[:10],
                    "desc": (j.get("text") or "")[:12000],
                    "source": "findwork", "layer": "l1.5",
                    "query": f"findwork:{q[:40]}", "rid": f"fw:{j.get('id') or ''}",
                })
            seen += len(res)
            if seen >= (d.get("count") or 0):
                break
            time.sleep(1.2)
        if all_queries:
            time.sleep(1.2)
    return rows, "ok" if rows else "no-yield"


def fetch_muse(family: str = "pm") -> tuple[list[dict], str]:
    """S38 L2 (GHA-only): The Muse public API — keyless, full JD.
    403 from HK (CloudFront geo) — fail-soft locally, live from the
    Azure lane. First successful page is captured to audit/s38_
    rolecampaign/ as the field-map evidence (F8 — the row contract was
    endpoint-verified, not row-verified).
    [S49-B / audit F12] the PM shape is the category QUERY PARAM:
    pm = category=Product Management (pinned); gen drops the category
    entirely — the all-jobs firehose with location=United States kept
    (the US lens; the API documents category as optional). Same 24-page
    cap (the S47 hard page bound). Gen rows pre-stamp family (the
    gate-loop bypass — classify_gen would kill the firehose's non-office
    titles)."""
    rows: list[dict] = []
    for page in range(1, 25):
        url = ("https://www.themuse.com/api/public/jobs?"
               + ("" if family == "gen"
                  else "category=Product%20Management&")
               + f"location=United%20States&page={page}")
        try:
            d = _http_json(url, timeout=30)
        except Exception as exc:
            return rows, f"error:{type(exc).__name__}"
        if page == 1 and rows == [] and d:
            _evidence_dump("muse_gen_page1.json" if family == "gen"
                           else "muse_page1.json", d)
        jobs = d.get("results", [])
        for j in jobs:
            comp = ((j.get("company") or {}) or {})
            row = {
                "title": j.get("name") or "",
                "company": comp.get("name") or "",
                "loc": ", ".join(
                    l.get("name", "") for l in (j.get("locations") or [])
                    if isinstance(l, dict))[:80],
                "url": ((j.get("refs") or {}).get("landing_page")
                        or (comp.get("refs") or {}).get("landing_page")
                        or ""),
                "posted": _rfc2822_to_iso(
                    (j.get("publication_datetime") or ""))[:10]
                if j.get("publication_datetime") else "",
                "desc": (j.get("contents") or "")[:12000],
                "source": "muse", "layer": "l2",
                "query": ("muse:all" if family == "gen"
                          else "muse:product-management"),
                "rid": f"muse:{j.get('id') or ''}",
            }
            if family == "gen":
                row["family"] = "gen"
                row["family_evidence"] = "muse_api"
            rows.append(row)
        if not jobs:
            break
        time.sleep(1.2)
    return rows, "ok"


def _fc_relay_json(url: str, headers: dict) -> tuple[object, str]:
    """S39 §A2 (probe-relay.md): relay a JSON API GET through firecrawl's
    US egress. data.usajobs.gov works through firecrawl (api.usajobs.gov
    returns IIS 'Invalid Hostname' 400 via relay); the JSON arrives in
    data.markdown markdown-escaped — unescape then parse. Returns
    (parsed | None, status). [S47 fleet] rotates keys via _fc_scrape."""
    if not _firecrawl_keys():
        return None, "no-fc-key"
    d, fc_status = _fc_scrape({"url": url, "formats": ["markdown"],
                               "headers": headers})
    if d is None:
        return None, f"relay-error:{fc_status}"
    if not d.get("success"):
        return None, f"relay-api-fail"
    md = str((d.get("data") or {}).get("markdown") or "")
    try:
        payload = json.loads(re.sub(r"\\(.)", r"\1", md))
    except json.JSONDecodeError:
        return None, "relay-parse-fail"
    return payload, "relay-ok"


def fetch_usajobs(queries: list[str]) -> tuple[list[dict], str]:
    """S38 L2: USAJOBS search API — owned key + UA; 403 from HK direct,
    key VERIFIED valid from US egress. Full JD (JobSummary).

    S39 §A2/F9: direct leg stays on api.usajobs.gov (Azure tries it
    first); on HTTPError the firecrawl relay leg runs against
    data.usajobs.gov with header forwarding (probe-relay.md — the
    relay-verified host; ResultsPerPage pinned at 25 on the data host
    per F9 until verified)."""
    key, ua = _env("USAJOBS_API_KEY"), _env("USAJOBS_USER_AGENT")
    if not key:
        return [], "no-key"
    rows: list[dict] = []
    transport = "direct"
    for q in queries[:4]:
        for page in range(0, 3):
            d = None
            if transport == "direct":
                try:
                    d = _http_json(
                        "https://api.usajobs.gov/api/search?Keyword="
                        + urllib.parse.quote_plus(q)
                        + "&LocationName=United%20States"
                        + f"&Page={page + 1}&ResultsPerPage=50",
                        headers={"User-Agent": ua,
                                 "Authorization-Key": key}, timeout=30)
                except Exception:
                    d, v = _fc_relay_json(
                        "https://data.usajobs.gov/api/search?Keyword="
                        + urllib.parse.quote_plus(q)
                        + "&LocationName=United%20States"
                        + f"&Page={page + 1}&ResultsPerPage=25",
                        {"User-Agent": ua, "Authorization-Key": key})
                    if d is None:
                        return rows, f"error:{v}"
                    transport = "relay"
                    _evidence_dump("usajobs_relay_page1.json", d)
            else:
                d, v = _fc_relay_json(
                    "https://data.usajobs.gov/api/search?Keyword="
                    + urllib.parse.quote_plus(q)
                    + "&LocationName=United%20States"
                    + f"&Page={page + 1}&ResultsPerPage=25",
                    {"User-Agent": ua, "Authorization-Key": key})
                if d is None:
                    break                        # relay leg fail-soft
            if page == 0 and q == queries[0] and d:
                _evidence_dump("usajobs_page1.json", d)
            items = (d.get("SearchResult") or {}).get("SearchResultItems",
                                                      [])
            for it in items:
                mo = it.get("MatchedObjectDescriptor") or {}
                det = (mo.get("UserArea") or {}).get("Details") or {}
                locs = mo.get("PositionLocation") or [{}]
                rows.append({
                    "title": mo.get("PositionTitle") or "",
                    "company": mo.get("OrganizationName") or "",
                    "loc": ((locs[0] or {}).get("DisplayLocation")
                            or "")[:80],
                    "url": mo.get("PositionURI") or "",
                    "posted": (mo.get("PublicationStartDate")
                               or "")[:10],
                    "desc": (det.get("JobSummary") or "")[:12000],
                    "source": "usajobs", "layer": "l2", "query": q,
                    "rid": f"usaj:{mo.get('PositionID') or ''}",
                })
            if not items:
                break
            time.sleep(1.2)
    return rows, "ok" if transport == "direct" else "ok:relay"


_EVIDENCE_DIR = REPO / "audit/s38_rolecampaign"


def _evidence_dump(name: str, payload) -> None:
    try:
        _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        p = _EVIDENCE_DIR / name
        if not p.exists():
            p.write_text(json.dumps(payload, indent=1,
                                    default=str)[:400000])
    except OSError:
        pass


YC_CITY_PAGES = ["new-york", "san-francisco", "los-angeles", "seattle",
                 "boston", "austin"]

# [S49-B / audit F12] the gen-mode yc.com role-slug set — ALL-ROLES
# (the owner's ask). Live-probed 2026-10-29: all 19 return 200 with
# 5-40 postings each via the WaasJobListingsPage data-page island (the
# S38 JSON-island extractor is DEAD on yc.com — the pages moved to
# <div id="…" data-page="…">; pm keeps the dead extractor bit-identical,
# gen extracts through the new shape).
GEN_YC_ROLE_SLUGS = (
    "software-engineer", "designer", "data-scientist", "data-engineer",
    "product-designer", "frontend-engineer", "backend-engineer",
    "mobile-engineer", "devops", "sales", "account-executive",
    "marketing", "operations", "customer-support", "finance", "hr",
    "recruiter", "business-operations", "growth",
)


def fetch_yc(family: str = "pm") -> tuple[list[dict], str]:
    """S38 L2 'yc' — THE startup layer (the directive's scrape-the-source
    branch): Work at a Startup Inertia JSON (/jobs/l/product-manager) +
    ycombinator.com/jobs SSR (/role/product-manager + /remote) + the 6
    US city pages (F18). Rows carry companySlug/companyBatch (rich);
    posted only from yc.com rows (WaaS list rows carry no date — the
    dateless-donor merge rule). YC-backed = board-grade sector evidence
    (yc_startup), applied post-merge (F2).

    [S49-B / audit F12] family-parameterized: pm keeps the pinned legs
    bit-identical (the WaaS PM list leg + the two PM yc.com slugs + the
    6 city pages); gen = the ALL-ROLES startup sweep — the 19-role
    yc.com slug set (GEN_YC_ROLE_SLUGS, live-probed) + the SAME 6
    city pages (all-roles already); the WaaS /jobs/l/product-manager
    list leg is PM-ONLY (gen skips it). The yc.com pages moved to the
    WaasJobListingsPage data-page shape (escaped JSON on a <div> with
    a leading id= attribute) — gen extracts through it; posted stays
    "" (the island's createdAt is a relative '6 months' string, not
    ISO — the dateless-donor rule covers it, same as the WaaS rows).
    Gen rows PRE-STAMP family="gen"/family_evidence="yc_waas" (the
    gate-loop pre-stamp bypass — classify_gen would kill them)."""
    import html as html_mod
    try:
        from curl_cffi import requests as cr  # noqa: PLC0415
    except ImportError:
        return [], "not-installed"
    headers = {"Accept": "text/html,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.9"}

    def _get_json(url: str):
        try:
            r = cr.get(url, impersonate="chrome131", timeout=30,
                       headers=headers)
            if r.status_code != 200:
                return None
            m = re.search(r'<div data-page="([^"]+)"', r.text)
            if not m:
                return {}
            return (json.loads(html_mod.unescape(m.group(1))
                               ).get("props") or {})
        except Exception:
            return None

    rows: list[dict] = []
    verdicts: list[str] = []

    def _waas_rows(props: dict, query_tag: str) -> list[dict]:
        out = []
        for j in (props.get("jobs") or []):
            out.append({
                "title": j.get("title") or "",
                "company": j.get("companyName") or "",
                "loc": (j.get("location") or "")[:80],
                "url": f"https://www.workatastartup.com/jobs/{j.get('id')}",
                "posted": "",
                "desc": ((j.get("companyOneLiner") or "") +
                         (f" | {j.get('salary')}" if j.get("salary")
                          else ""))[:2000],
                "source": "yc", "layer": "l2", "query": query_tag,
                "rid": f"yc:{j.get('id') or ''}",
                "yc_slug": j.get("companySlug") or "",
            })
            if family == "gen":
                # [S49-B] the city pages are all-roles — pre-stamp so
                # the gate-loop bypass keeps them (classify_gen would
                # kill most); pm rows stay unstamped (bit-identical)
                out[-1]["family"] = "gen"
                out[-1]["family_evidence"] = "yc_waas"
        return out

    if family == "pm":
        # the pinned PM leg — WaaS /jobs/l/product-manager (F9: the
        # /jobs/l/ list leg is PM-shaped; gen skips it per the task)
        props = _get_json(
            "https://www.workatastartup.com/jobs/l/product-manager")
        if props is None:
            verdicts.append("waas:dead")
        else:
            rows += _waas_rows(props, "waas:product-manager")

    # ycombinator.com/jobs — [S49-B] per-family slug sets. pm keeps the
    # pinned S38 pair (Next.js-SSR JSON-island extraction — the island
    # is dead on today's yc.com shape, kept bit-identical anyway); gen
    # walks the 19-role all-roles slug set through the live
    # WaasJobListingsPage data-page shape.
    _pm_legs = [("product-manager", "yc:role"),
                ("product-manager/remote", "yc:remote")]
    _gen_legs = [(s, f"yc:gen:{s}") for s in GEN_YC_ROLE_SLUGS]
    for i, (slug, tag) in enumerate(_pm_legs if family == "pm"
                                     else _gen_legs):
        if family == "gen" and i:
            time.sleep(1.0)            # 19 live slug pages — politeness
        try:
            r = cr.get(f"https://www.ycombinator.com/jobs/role/{slug}",
                       impersonate="chrome131", timeout=30, headers=headers)
            if r.status_code == 200:
                if family == "pm":
                    found = _yc_com_extract(r.text)
                    for j in found:
                        rows.append({
                            "title": j.get("title") or
                            j.get("jobTitle") or "",
                            "company": j.get("companyName") or
                            (j.get("company") or {}).get("name", ""),
                            "loc": (j.get("location") or
                                    j.get("primaryLocation") or "")[:80],
                            "url": (j.get("url") or
                                    j.get("applyUrl") or
                                    f"https://www.ycombinator.com/jobs/"
                                    f"{j.get('id')}"),
                            "posted": _epoch_to_iso(j.get("createdAt")),
                            "desc": (j.get("description") or "")[:2000],
                            "source": "yc", "layer": "l2",
                            "query": tag,
                            "rid": f"yc:{j.get('id') or ''}",
                            "yc_slug": (j.get("companySlug") or
                                        (j.get("company") or {})
                                        .get("slug", "")),
                        })
                else:
                    # gen: the live WaasJobListingsPage shape — props.
                    # jobPostings[], fields id/title/url/companyName/
                    # companyOneLiner/companyUrl/location/salaryRange
                    m = re.search(r'<div[^>]*\bdata-page="([^"]+)"',
                                  r.text)
                    found = []
                    if m:
                        try:
                            props_ = (json.loads(html_mod.unescape(
                                m.group(1))).get("props") or {})
                            found = props_.get("jobPostings") or []
                        except (json.JSONDecodeError, ValueError):
                            found = []
                    for j in found:
                        rows.append({
                            "title": j.get("title") or "",
                            "company": j.get("companyName") or "",
                            "loc": (j.get("location") or "")[:80],
                            "url": ("https://www.ycombinator.com"
                                    + (j.get("url") or "")),
                            "posted": "",   # island createdAt is
                            # relative ('6 months') — not ISO; the
                            # dateless-donor rule covers it
                            "desc": ((j.get("companyOneLiner") or "")
                                     + (f" | {j.get('salaryRange')}"
                                        if j.get("salaryRange") else "")
                                     + (f" | {j.get('type')}"
                                        if j.get("type") else ""))[:2000],
                            "source": "yc", "layer": "l2",
                            "query": tag,
                            "rid": f"yc:{j.get('id') or ''}",
                            "yc_slug": (j.get("companyUrl") or "").rsplit(
                                "/companies/", 1)[-1],
                            "family": "gen",
                            "family_evidence": "yc_waas",
                        })
            else:
                verdicts.append(f"ycc:{r.status_code}")
        except Exception as exc:
            verdicts.append(f"ycc:{type(exc).__name__}")

    # F18: the 6 US city pages (fixed windows, not pagination)
    for city in YC_CITY_PAGES:
        props = _get_json(
            f"https://www.workatastartup.com/jobs/location/{city}")
        if props:
            rows += _waas_rows(props, f"waas:city:{city}")
        time.sleep(1.0)

    verdict = "ok" if not verdicts else ("partial:" + ",".join(verdicts))
    return rows, verdict


def _yc_com_extract(html_text: str) -> list[dict]:
    """yc.com job pages embed job JSON (script/JSON islands); grab the
    largest {...} blobs carrying jobTitle/title + company keys."""
    found: list[dict] = []
    for m in re.finditer(r'<script[^>]*>\s*(\{.*?\})\s*</script>',
                         html_text, re.DOTALL):
        blob = m.group(1)
        if '"jobTitle"' not in blob and '"title"' not in blob:
            continue
        try:
            d = json.loads(blob)
        except json.JSONDecodeError:
            continue
        stack = [d]
        while stack:
            obj = stack.pop()
            if isinstance(obj, dict):
                if ("jobTitle" in obj or "title" in obj) and (
                        "companyName" in obj or "company" in obj):
                    found.append(obj)
                stack.extend(v for v in obj.values()
                             if isinstance(v, (dict, list)))
            elif isinstance(obj, list):
                stack.extend(x for x in obj if isinstance(x, dict))
    return found


def fetch_wellfound() -> tuple[list[dict], str]:
    """S38 L2: Wellfound /jobs feed (keyless via curl_cffi; the GraphQL
    search host is CF-walled; the OLD registry adapter's role URLs are
    dead-404 — this is a NEW fetcher per F21). __NEXT_DATA__ apolloState
    JobListing entries; liveStartAt epoch → ISO (F1)."""
    try:
        from curl_cffi import requests as cr  # noqa: PLC0415
    except ImportError:
        return [], "not-installed"
    try:
        r = cr.get("https://wellfound.com/jobs", impersonate="chrome131",
                   timeout=30,
                   headers={"Accept": "text/html,application/xhtml+xml",
                            "Accept-Language": "en-US,en;q=0.9"})
        if r.status_code != 200:
            return [], f"error:status{r.status_code}"
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                      r.text, re.DOTALL)
        if not m:
            return [], "no-next-data"
        data = json.loads(m.group(1))
    except Exception as exc:
        return [], f"error:{type(exc).__name__}"
    rows: list[dict] = []
    state = ((data.get("props") or {}).get("pageProps") or {}).get(
        "apolloState") or {}
    if "data" in state and isinstance(state.get("data"), dict):
        state = state["data"]                 # the nested apollo cache
    for key, obj in state.items():
        if not isinstance(obj, dict) or "JobListing" not in str(
                obj.get("__typename", "")):
            continue
        # S39 §A6/F15: skip title-less entries (the /jobs feed carries
        # non-job cache objects — they inflate raw counts and die at
        # the family gate anyway; measured 60+ empty-title rows)
        if not (obj.get("title") or "").strip():
            continue
        comp_ref = (obj.get("startup") or {}).get("__ref", "")
        comp = state.get(comp_ref, {}) if comp_ref else {}
        loc = ", ".join(obj.get("locationNames") or [])[:80]
        rows.append({
            "title": obj.get("title") or "",
            "company": (comp.get("name") or "")[:80] if isinstance(
                comp, dict) else "",
            "loc": loc,
            "url": ("https://wellfound.com/l/"
                    + str(obj.get("slug") or "")),
            "posted": _epoch_to_iso(obj.get("liveStartAt")),
            "desc": ((comp.get("highConcept") or "") + " " +
                     (comp.get("productDescription") or ""))[:12000],
            "source": "wellfound", "layer": "l2",
            "query": "wellfound:feed",
            "rid": f"wf:{obj.get('id') or obj.get('slug') or ''}",
        })
    return rows, "ok"


def fetch_talent(family: str = "pm") -> tuple[list[dict], str]:
    """S38 L2: talent.com SERP — server-rendered JobCard articles
    (closure-tier, 20 rows keyless, no pagination — the census verdict).
    CSS-module class prefixes (JobCard_title/…) with hashed suffixes;
    the JSON-LD ItemList is URL-only (no job fields).
    [S49-B / audit F12] the PM shape is the k= QUERY: pm = the pinned
    k=product+manager single fetch; gen sweeps the 6-query all-roles
    set (live-probed 2026-10-29: k=engineer/designer/data/sales/
    marketing/operations all 200 with 19-20 cards each). Gen rows mint
    rid=tal:{view-id} (the 'tal' prefix rides the seed rid whitelist)
    and pre-stamp family (the gate-loop bypass)."""
    kws = ["product manager"] if family == "pm" else [
        "engineer", "designer", "data", "sales", "marketing",
        "operations"]
    try:
        from curl_cffi import requests as cr  # noqa: PLC0415
    except ImportError:
        return [], "not-installed"
    rows: list[dict] = []
    status = "ok"
    for qi, kw in enumerate(kws):
        if qi:
            time.sleep(1.0)               # the gen sweep's politeness
        try:
            r = cr.get("https://www.talent.com/jobs"
                       f"?k={urllib.parse.quote_plus(kw)}"
                       "&l=United+States", impersonate="chrome131",
                       timeout=30)
            if r.status_code != 200:
                status = f"error:status{r.status_code}"
                continue
        except Exception as exc:
            return rows, f"error:{type(exc).__name__}"
        for m in re.finditer(
                r'<article class="JobCard_card[^"]*"[^>]*>(.*?)</article>',
                r.text, re.DOTALL):
            card = m.group(1)

            def _cls(prefix: str) -> str:
                mm = re.search(
                    rf'class="{prefix}[^"]*"[^>]*>(.*?)</',
                    card, re.DOTALL)
                return (re.sub(r"<[^>]+>", "", mm.group(1)).strip()
                        if mm else "")

            title = _cls("JobCard_title")
            company = _cls("JobCard_company")
            loc = _cls("JobCard_location")
            snippet = _cls("JobCard_snippet")
            dm = re.search(r'datetime="([^"]+)"', card, re.IGNORECASE)
            href = re.search(r'href="/view\?id=(\d+)"', card)
            if not title:
                continue
            row = {
                "title": title, "company": company,
                "loc": loc[:80],
                "url": (f"https://www.talent.com/view?id={href.group(1)}"
                        if href else ""),
                "posted": (dm.group(1) or "")[:10] if dm else "",
                "desc": snippet[:12000],
                "source": "talent", "layer": "l2",
                "query": ("talent:pm" if family == "pm"
                          else f"talent:{kw.split()[0]}"),
                "rid": ("" if family == "pm"
                        else (f"tal:{href.group(1)}" if href else "")),
            }
            if family == "gen":
                row["family"] = "gen"
                row["family_evidence"] = "talent_serp"
            rows.append(row)
    return rows, status


def fetch_hn(family: str = "pm") -> tuple[list[dict], str]:
    """S38 L2: HN Who's Hiring via the in-repo adapter (the deferred
    v1.5) — the adapter returns list[Job] dataclasses (title/company/
    description/link/location/date_posted); tech-community by norm →
    board-grade evidence hn_whihiring, applied post-merge (F2).
    [S49-B] family-parameterized: pm keeps the keyword legs
    ("Product Manager", "PM") bit-identical; gen = the UNFILTERED
    thread-walk (keywords="" — the adapter skips the keyword_match
    filter, live-probed 261 top-level postings) — the owner's ALL
    startup roles ask. Gen rows pre-stamp family="gen"/
    family_evidence="hn_whihiring" so the gate-loop pre-stamp bypass
    keeps them (classify_gen would kill the engineering titles)."""
    try:
        from jobsearch.sources import hn_whos_hiring as hn  # noqa: PLC0415
    except ImportError:
        return [], "not-installed"
    rows: list[dict] = []
    verdicts: list[str] = []
    legs = (("Product Manager", "PM") if family == "pm" else ("",))
    for kw in legs:
        try:
            got = hn.fetch(kw, num_results=400 if family == "pm" else 1000)
        except Exception:
            verdicts.append(f"{kw[:2]}:{type(hn) and 'exc'}")
            continue
        for j in (got or []):
            if not getattr(j, "title", ""):
                continue
            row = {
                "title": j.title or "",
                "company": j.company or "",
                "loc": (j.location or "")[:80],
                "url": j.link or "",
                "posted": (j.date_posted or "")[:10],
                "desc": (j.description or "")[:12000],
                "source": "hn", "layer": "l2",
                "query": f"hn:{kw.lower()}" if kw else "hn:all",
                "rid": f"hn:{j.id or j.link or j.title[:40]}",
            }
            if family == "gen":
                row["family"] = "gen"
                row["family_evidence"] = "hn_whihiring"
            rows.append(row)
    verdict = ("ok" if rows or not verdicts
               else "partial:" + ",".join(verdicts))
    return rows, verdict


def fetch_builtin(queries: list[str]) -> tuple[list[dict], str]:
    """S39 §A3 (probe-relay.md): BuiltIn via firecrawl relay — CF-walled
    from HK direct (403 'Just a moment'); firecrawl passes CF. ONLY the
    `?search=` SSR form filters server-side (path URLs SSR a generic
    feed); 25 cards/page. Card markdown: '## [Title](url)' +
    '[Company](company-url)' + posted/location lines. 3 queries × 4
    pages ≈ 12 firecrawl credits (F13 fleet budget). [S47 fleet]
    rotates keys via _fc_scrape."""
    if not _firecrawl_keys():
        return [], "no-key"
    rows: list[dict] = []
    status = "ok"
    for q in queries[:3]:
        for page in range(1, 5):
            url = ("https://www.builtin.com/jobs?search="
                   + urllib.parse.quote_plus(q) + f"&page={page}")
            d, fc_status = _fc_scrape({"url": url, "formats": ["markdown"],
                                        "waitFor": 3000})
            if d is None:
                status = f"error:{q[:12]}:{fc_status}"
                break
            if not d.get("success"):
                status = f"api-fail:{q[:12]}"
                break
            md = str((d.get("data") or {}).get("markdown") or "")
            if page == 1 and q == queries[0]:
                _evidence_dump("builtin_page1.md", {"markdown": md[:8000]})
            # cards: ## [Title](https://builtin.com/job/slug/id)
            n_new = 0
            # builtin card layout: [Company](company-url) BEFORE the
            # title '## [Title](job-url)' — pair BACKWARD (title-first
            # pairing steals the NEXT card's company; probe evidence)
            for m in re.finditer(
                    r"\[(?P<company>[^\]!][^\]]{1,80})\]"
                    r"\(https://builtin\.com/company/[^)\s]+\)\s*\n+"
                    r"##\s+\[(?P<title>[^\]]{3,140})\]\("
                    r"(?P<url>https://builtin\.com/job/[^)\s]+)\)",
                    md):
                g = m.groupdict()
                # card meta after the company link: 'time-ago Saved' →
                # [Easy Apply] → work-type → LOCATION → salary/level.
                # Take the first loc-looking line (probe-relay.md form:
                # 'Chicago, IL, USA' | '2 Locations' | 'Remote').
                block = md[m.end():m.end() + 500]
                loc = ""
                for line in block.split("\n"):
                    s = line.strip()
                    if not s or "Ago" in s or s in (
                            "Easy Apply", "Hybrid", "On Site",
                            "Fully Remote"):
                        continue
                    if s == "Remote":
                        loc = "Remote"
                        break
                    if re.match(r"^\d+\s+Locations?$", s):
                        loc = s
                        break
                    if re.match(r"^[A-Z][a-zA-Z .,'-]{2,40},\s*[A-Z]{2}"
                                r"(?:,\s*USA)?$", s) or s.endswith(", USA"):
                        loc = s
                        break
                rows.append({
                    "title": g["title"].strip(),
                    "company": g["company"].strip(),
                    "loc": loc[:80],
                    "url": g["url"],
                    "posted": "", "desc": block[:12000],
                    "source": "builtin", "layer": "l2", "query": q,
                    "rid": "",          # slug-derived ids vary per card
                })
                n_new += 1
            if n_new == 0:
                break                        # no more cards for this query
            time.sleep(2.0)
    return rows, status


def fetch_himalayas(family: str = "pm") -> tuple[list[dict], str]:
    """S39 §A4 (probe-relay.md): Himalayas open JSON firehose —
    `himalayas.app/jobs/api?limit=20&cursor=<nextCursor>` keyless with
    full-JD rows; no server-side search → client-side filter over the
    recent window (30 pages × 20, politeness sleep 1s; the full-JD
    evidence value inverts the trio's thin-density precedent — F11).
    rid = him:{guid}; loc = locationRestrictions joined.
    [S49-B / audit F12] the ONLY filter is the client-side PM list —
    pm keeps it bit-identical; gen keeps ALL rows (the feed is the
    all-roles remote-jobs firehose) and pre-stamps family (the
    gate-loop bypass)."""
    rows: list[dict] = []
    cursor = ""
    verdicts: list[str] = []
    seen = set()
    for page_i in range(30):
        url = ("https://himalayas.app/jobs/api?limit=20"
               + (f"&cursor={cursor}" if cursor else ""))
        try:
            d = _http_json(url, timeout=30)
        except Exception as exc:
            verdicts.append(f"p{page_i}:{type(exc).__name__}")
            break
        jobs = d.get("jobs", [])
        if page_i == 0:
            _evidence_dump("himalayas_page1.json", d)
        for j in jobs:
            title = j.get("title") or ""
            if not title:
                continue
            # client-side PM-family filter (the board is all-roles) —
            # [S49-B] pm-only; gen keeps every titled row
            if family == "pm":
                tl = title.lower()
                if not any(k in tl for k in (
                        "product manager", "product owner", "product lead",
                        "head of product", "product marketing",
                        "director of product", "principal product",
                        "group product", "technical product")):
                    continue
            guid = j.get("guid") or j.get("id") or ""
            # S41-R1 F8: some guids ARE full URLs (the RSS guid field)
            # — the blind prefix built double-prefixed URLs
            # (himalayas.app/jobs/https://himalayas.app/...) that 404
            # the detail fetch. URL-guids are used verbatim.
            guid_is_url = guid.startswith("http")
            if guid in seen:
                continue
            seen.add(guid)
            locs = " / ".join(
                l for l in (j.get("locationRestrictions") or [])
                if isinstance(l, str))[:80]
            pub = j.get("pubDate") or ""
            if isinstance(pub, (int, float)):
                pub = _epoch_to_iso(pub)
            row = {
                "title": title,
                "company": j.get("companyName") or "",
                "loc": locs,
                "url": (guid if guid_is_url else
                        (guid and f"https://himalayas.app/jobs/{guid}")) or "",
                "posted": str(pub)[:10],
                "desc": (j.get("description") or "")[:12000],
                "source": "himalayas", "layer": "l2",
                "query": "himalayas:recent",
                "rid": f"him:{(guid.rsplit('/', 1)[-1]
                          if guid_is_url else guid)}",
            }
            if family == "gen":
                row["family"] = "gen"
                row["family_evidence"] = "himalayas_firehose"
            rows.append(row)
        cursor = d.get("nextCursor") or ""
        if not jobs or not cursor:
            break
        time.sleep(1.0)
    verdict = "ok" if not verdicts else "partial:" + ",".join(verdicts)
    return rows, verdict


def project_l0() -> tuple[list[dict], str]:
    """L0: the bundle PM projection (manifest-derived canonical).
    S38/F15: the board_family demotion is REMOVED — the R2-hardened
    grammar (88 adversarial pins) is the family authority; the
    jobFamilyGroup stays as a note field only."""
    if not BUNDLE.exists():
        return [], "no-bundle"
    d = json.load(open(BUNDLE))
    rows = []
    for j in d.get("jobs", []):
        title = j.get("title") or ""
        if "product" not in title.lower():
            continue                      # cheap pre-filter for the corpus
        fam, ev = classify_family(title)
        if fam == "other":
            continue
        remote = bool(j.get("remoteFlag"))
        us = _classify_loc(j.get("country") or
                           j.get("primaryLocation") or "")
        us_status = "remote" if remote else us
        rows.append({
            "title": title,
            "company": j.get("company") or "",
            "loc": j.get("primaryLocation") or "",
            "url": j.get("url") or "",
            "posted": (j.get("firstPublishedIso") or
                       (j.get("postedOn") or "")[-10:] or ""),
            "desc": "", "source": "board", "layer": "l0",
            "query": "l0:bundle", "rid": j.get("id") or "",
            "company_key": j.get("companyKey") or "",
            "family": fam, "family_evidence": ev,
            "seniority": pm_seniority(title),
            "us_status": us_status, "us_evidence": "board_country",
            "description_source": "none",
            "job_family_group": j.get("jobFamilyGroup") or "",
        })
    return rows, "ok"


def project_l0_gen() -> tuple[list[dict], str]:
    """[S49-B / DESIGN-S49 D2] L0: the ALL-ROLES board-corpus projection
    for the gen family — the owner's ask: every row of the China-based +
    AI-company watch corpus (the 30 curated ATS boards — openai,
    anthropic, nvidia, bytedance, xiaohongshu, miniso, fuyao…) in the
    all-roles bundle, WITH full JDs. Doctrine vs project_l0 (the pm
    twin stays bit-identical):

    - NO 'product' prefilter, NO classify_gen gate — the gate keeps
      1.9% of this corpus (audit F6: GEN_CORE is the SMB office
      grammar; nvidia/openai/anthropic are engineering/research
      corpora it rejects wholesale). l0 rows bypass the family gate
      in the sweep loop exactly like pm's l0 rows do.
    - posted from earliestEvidenceDate (audit F8: firstPublishedIso
      exists on 0/8,161 rows; postedOn is a '+ Days Ago' garbage
      string — the live pm-bundle bug). 2,398 rows carry no date and
      stay honestly dateless. NO 60d staleness bound (F10: the board
      still listing a posting is the board's own liveness claim).
    - desc = the FULL JD from the corpus desc store
      (ui/desc/{companyId}.json → {rows: {full-row-id: jd_text}},
      99.7% coverage), truncated at 12,000 like every other fetcher;
      rows without one ship descLen=0/description_source="none".
    - CJK titles (38 rows — 销售经理 etc.) KEPT: they are China-company
      rows, the whole point of the strategy; pm_seniority is title-
      based and degrades to "mid" on them. Rows whose title AND desc
      are BOTH empty are skipped (nothing to show, nothing to grade).

    The shared machinery (staffing/anonymized flags, sector_verdict,
    the merge ladder) still runs on every projected row — the sweep
    loop applies it to l0 rows too; the gen-l0 doctrine amendments
    (g1/g1b/screening + bucket→pending) live there, family-gated.
    """
    if not BUNDLE.exists():
        return [], "no-bundle"
    d = json.load(open(BUNDLE))
    rows = []
    desc_cache: dict[str, object] = {}
    n_joined = 0
    for j in d.get("jobs", []):
        title = j.get("title") or ""
        rid = j.get("id") or ""
        # the desc store: one file per companyId, rows keyed by the
        # FULL row id (companyId:reqId-slug) — same id as jobs.json's
        # 'id' field (verified: 8,134/8,161 joined, 27 blank)
        desc = ""
        cid = j.get("companyId") or ""
        if cid and rid:
            if cid not in desc_cache:
                p = UI_DIR / "desc" / f"{cid}.json"
                try:
                    desc_cache[cid] = ((json.load(open(p)).get("rows")
                                        if p.exists() else None))
                except (json.JSONDecodeError, OSError):
                    desc_cache[cid] = None
            drows = desc_cache[cid]
            if isinstance(drows, dict):
                desc = str(drows.get(rid) or "")[:12000]
        if not title.strip() and not desc.strip():
            continue
        remote = bool(j.get("remoteFlag"))
        us = _classify_loc(j.get("country") or
                           j.get("primaryLocation") or "")
        us_status = "remote" if remote else us
        if desc.strip():
            n_joined += 1
        rows.append({
            "title": title,
            "company": j.get("company") or "",
            "loc": j.get("primaryLocation") or "",
            "url": j.get("url") or "",
            # F8: earliestEvidenceDate is the only ISO date field the
            # corpus carries (5,763 rows); the pm projection's
            # firstPublishedIso/postedOn reads are dead/garbage fields
            "posted": (j.get("earliestEvidenceDate") or "")[:10],
            "desc": desc, "source": "board", "layer": "l0",
            "query": "l0:bundle", "rid": rid,
            "company_key": j.get("companyKey") or "",
            "family": "gen", "family_evidence": "l0_corpus",
            "seniority": pm_seniority(title),
            "us_status": us_status, "us_evidence": "board_country",
            # "corpus": the FULL JD joined from the board-watch desc
            # store (a NEW description_source value — the writers are
            # passthrough, verified; "none" for the 27 JD-less rows)
            "description_source": "corpus" if desc.strip() else "none",
            "job_family_group": j.get("jobFamilyGroup") or "",
        })
    v = (f"ok:jd={n_joined}/{len(rows)}" if rows else "ok")
    return rows, v


# ── the pipeline: gates → usgate → dedup/merge → outputs ───────────────────

# S38-C4: the measured variant map (49 TRUE same-employer key variants
# from the 2,616-row corpus — dup-variant-map.md; evidence: collision
# groups + measured non-colliding mates; AWS≠Amazon and the Amazon
# entities stay DISTINCT per the sector doctrine).
COMPANY_KEY_VARIANTS = {
    "allstate-insurance-company": "allstate-insurance",
    "amazon-com": "amazon",
    "amazon-web-services-inc": "amazon-web-services-aws",
    "apn-consulting-inc": "apn-consulting",
    "bny-mellon": "bny", "bold-limited": "bold",
    "bosch-group": "bosch", "calm-com": "calm",
    "caterpillar-inc": "caterpillar", "circle-k-stores-inc": "circle-k-stores",
    "clindcast-llc": "clindcast", "compsych-corporation": "compsych",
    "construction-specialties-inc": "construction-specialties",
    "cummins-inc": "cummins", "eagleview-technologies": "eagleview",
    "fiserv-inc": "fiserv", "ford-motor": "ford-motor-company",
    "general-motors-gm": "general-motors",
    "guardian-life-insurance-company": "guardian-life",
    "harbourvest-partners-llc": "harbourvest-partners", "hiscox-ltd": "hiscox",
    "icf-international-inc": "icf-international",
    "johnson-and-johnson": "johnson-johnson",
    "jpmorgan-chase-co": "jpmorgan-chase", "meta-platforms": "meta",
    "metasense-inc": "metasense", "microsoft-corporation": "microsoft",
    "mmd-services-inc": "mmd-services", "motive-technologies": "motive",
    "mutual-of-enumclaw-insurance-company": "mutual-of-enumclaw",
    "noblesoft-technologies-inc": "noblesoft-technologies",
    "omg-technology": "omg-technologies",
    "procter-amp-gamble": "procter-gamble",
    "prysmian-group": "prysmian", "servicenow-inc": "servicenow",
    "simpro-software": "simpro",
    "software-guidance-assistance-inc": "software-guidance-assistance",
    "steps-talent-llc": "steps-talent",
    "t-mobile-usa": "t-mobile", "t-mobile-usa-inc": "t-mobile",
    "talent-software-services-inc": "talent-software-services",
    "the-boeing-company": "boeing", "the-cigna-group": "cigna",
    "the-standard-insurance": "standard-insurance-company",
    "the-vanguard-group": "vanguard", "tp-link-usa-corp": "tp-link",
    "true-anomaly-inc": "true-anomaly", "truist-bank": "truist",
    "uline-inc": "uline",
}


def _company_key(company: str, existing: dict | None = None) -> str:
    """build_ui_bundle.company_key-compatible slug + the variant map
    (design §6 — Anker-class unification via triage-grown variants;
    S38-C4: the 49-entry measured map is the seed)."""
    s = re.sub(r"[^a-z0-9]+", "-", (company or "").lower()).strip("-")
    s = COMPANY_KEY_VARIANTS.get(s, s)
    if existing and s in existing:
        return existing[s]
    return s or "unknown"


# ── [S48-B] the smbSignal join (B4 §4 — the catch-all's remaining half) ────
# Classify every gen/dmm bundle row's company against the LOCAL-LICENSED
# census pools (the vendored sha256 lookup sets built by
# audit/s48/census/build_smb_lookup_sets.py) + the repo's own
# dictionaries, one emit-time pass. The enum follows the canon B4 §4 +
# B3-synthesis §4.3 exactly: cslb_match / dca_match / dre_match /
# fbn_new (the four pools, first-hit precedence) → harvest_dict (the
# industry harvest's known local companies) → national_known (the
# curated national/brand seeds) → unknown_local (a CA/metro-coded row
# hitting NO known pool — the freshest SMB leads). The spec is SILENT
# on the last two classes; they are this repo's honest fills (a
# non-CA/non-pool row is `unknown`; the sets themselves being absent
# — a fresh clone, the builder not run — degrades the whole pass to
# `no_lookup`, fail-soft: the lane runs BEFORE the builder ever does
# and must NEVER crash on it).
_SMB_SETS_DIR = REPO / "ingest/data/census"
_SMB_POOLS = (          # pool classes in precedence order (first hit wins)
    ("cslb_match", "smb_cslb_names.sha256.gz"),
    ("dca_match", "smb_dca_names.sha256.gz"),
    ("dre_match", "smb_dre_names.sha256.gz"),
    ("fbn_new", "smb_fbn_names.sha256.gz"),
)
_SMB_CA_TIERS = ("t1a", "t1b", "t2")   # the CA-scoped geo-ladder tiers
_SMB_CA_LOC = re.compile(r"(?:^|,\s*|\s+)ca(?:lifornia)?\s*$", re.I)
_SMB_LOOKUP: dict | None = None       # None=unloaded, {}=missing (cached)
_SMB_HARVEST: frozenset | None = None
_SMB_NATIONAL_KEYS: frozenset | None = None
_SMB_NORM = None                      # lazy industry_sweep._company_norm


def _smb_norm(company: str) -> str:
    """[S48-B] THE shared normalizer — ONE function on both sides of
    the join (the builder hashes pools with it; this lookup digests
    row companies with it — a mismatch would silently match nothing).
    Decision: industry_sweep._company_norm — the dba-splitting,
    punctuation+corporate-suffix-stripping NAME normalizer the census
    cross-match already measured (S47-E: 4 of the 200 vendored CSLB
    companies appear in the harvest by exact match — the big mechanical
    GCs only; the S44-R2 ceiling rides the README, not the code).
    NOT this module's _company_key: that is the bundle identity SLUG
    space (COMPANY_KEY_VARIANTS has no registry-side equivalent).
    Imported LAZILY — industry_sweep imports THIS module at module
    level, so a module-level import here would be circular."""
    global _SMB_NORM
    if _SMB_NORM is None:
        _sp = str(REPO / "scripts")
        if _sp not in sys.path:
            sys.path.insert(0, _sp)
        import industry_sweep as _isw            # noqa: PLC0415
        _SMB_NORM = _isw._company_norm
    return _SMB_NORM(company)


def _smb_digest(company: str) -> str:
    """sha256(_company_norm(company)) hex — the pool-membership key."""
    return hashlib.sha256(
        _smb_norm(company).encode("utf-8")).hexdigest()


def _smb_load_lookup() -> dict:
    """Lazy, cached digest-set loader → {class: frozenset}. Missing
    files are SKIPPED per-pool (partial lookup still classifies the
    pools it has); ALL absent → {} (fail-soft: `no_lookup` + the
    one-line warning at the call site, never a crash)."""
    global _SMB_LOOKUP
    if _SMB_LOOKUP is not None:
        return _SMB_LOOKUP
    sets: dict = {}
    for cls, fname in _SMB_POOLS:
        p = _SMB_SETS_DIR / fname
        if not p.exists():
            continue
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                sets[cls] = frozenset(
                    ln.strip() for ln in f if ln.strip())
        except OSError:
            continue                    # unreadable pool = absent pool
    _SMB_LOOKUP = sets
    return sets


def _smb_harvest_keys() -> frozenset:
    """The existing harvest company dictionary (the `harvest_dict`
    class): the industry artifacts' companies — home_services_us/ba +
    craigslist_home_services — normalized by the SAME shared
    normalizer, loaded lazily once per sweep. Missing artifacts = the
    empty set (fail-soft: the class just can't fire)."""
    global _SMB_HARVEST
    if _SMB_HARVEST is not None:
        return _SMB_HARVEST
    names: set = set()
    for slug in ("home_services_us", "home_services_ba",
                 "craigslist_home_services"):
        p = REPO / f"ingest/data/industry/{slug}.json"
        try:
            d = json.load(open(p))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        for r in (d.get("jobs") or []):
            nm = _smb_norm(r.get("company") or "")
            if nm:
                names.add(nm)
    _SMB_HARVEST = frozenset(names)
    return _SMB_HARVEST


def _smb_national_keys() -> frozenset:
    """The `national_known` class: the EXISTING curated brand-name
    seeds (company_seeds) — TECH ∪ NONTECH ∪ BANK ∪ FINTECH_GUARD ∪
    PAYER (the B4 §4 corpus-177/national-chain successors; matched
    via the existing seed_hit both-forms machinery — reused, not
    invented). STAFF_SEED is deliberately NOT here: a staffing brand
    is an agency, not the employer — staffing rows fall through to the
    loc classes (staffingEvidence already rides every row)."""
    global _SMB_NATIONAL_KEYS
    if _SMB_NATIONAL_KEYS is None:
        _SMB_NATIONAL_KEYS = frozenset(
            _seeds.TECH_KEYS | _seeds.NONTECH_KEYS | _seeds.BANK_KEYS
            | _seeds.FINTECH_KEYS | _seeds.PAYER_KEYS)
    return _SMB_NATIONAL_KEYS


def _smb_classify(r: dict) -> str:
    """One emit-time pass per row. Precedence: cslb_match → dca_match
    → dre_match → fbn_new (pools, first hit) → harvest_dict →
    national_known → unknown_local (CA/metro-coded row) → unknown.
    NO lookup sets at all → `no_lookup` for EVERY row (the fail-soft
    degrade — the class describes the lookup state; a PARTIAL lookup
    still classifies the pools it has). An anonymized/board-employer
    company has NO real employer name to classify — honest `unknown`,
    never a pool guess."""
    lookup = _smb_load_lookup()         # lazy + cached (idempotent)
    if not lookup:
        return "no_lookup"               # the sets are absent entirely
    comp = (r.get("company") or "").strip()
    nm = _smb_norm(comp)
    if not nm or is_anonymized_employer(comp):
        return "unknown"
    d = _smb_digest(comp)
    for cls, _fname in _SMB_POOLS:
        if d in lookup.get(cls, ()):          # the pool precedence ladder
            return cls
    if nm in _smb_harvest_keys():
        return "harvest_dict"
    if _seeds.seed_hit(r.get("company_key") or r.get("companyKey")
                       or "", comp, _smb_national_keys()):
        return "national_known"
    if (r.get("geoTier") in _SMB_CA_TIERS
            or _SMB_CA_LOC.search((r.get("loc") or ""))):
        return "unknown_local"
    return "unknown"


def _smb_classify_rows(rows: list[dict], counters: dict) -> dict:
    """The sweep-emit wrapper: classify, stamp `smb_signal` per row,
    ride the envelope's counters (smb_<class>), return the histogram.
    Missing sets → every row still gets stamped — with `no_lookup`
    (the honest degrade; the sweep NEVER crashes on it)."""
    if not _smb_load_lookup():
        print(f"[sweep] WARNING: smb lookup sets missing under "
              f"{_SMB_SETS_DIR} — run "
              f"audit/s48/census/build_smb_lookup_sets.py; "
              f"smb_signal=no_lookup")
    hist: dict[str, int] = {}
    for r in rows:
        sig = _smb_classify(r)
        r["smb_signal"] = sig
        counters["smb_" + sig] = counters.get("smb_" + sig, 0) + 1
        hist[sig] = hist.get(sig, 0) + 1
    return hist


def _norm_day(posted: str):
    if not posted:
        return None
    try:
        return datetime.strptime(posted[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def sweep(queries: list[str], skip_gj: bool = False,
          skip_li: bool = False, skip: str = "",
          seed_envelope: str = "", family: str = "pm",
          geo_ladder: str = "t3") -> int:
    # S41: the family registry routes every artifact path (DESIGN-S41
    # v2 §2.3). 'pm' = bit-identical with the pre-S41 behavior (pinned
    # by identity tests); 'dmm' = the ads demand-side stress family.
    # [S46-B4 §3.3] geo_ladder: the catch-all lane's query-time geo
    # scope (t1a/t1b/t2/t3/t4). pm ALWAYS runs t3 (the identity
    # doctrine — the CLI rejects the combination); gen/dmm stamp
    # geoTier on every row and union tiers through the merge ladder.
    spec = family_spec(family)
    prefix = spec["quartet_prefix"]
    skip_set = {s.strip() for s in skip.split(",") if s.strip()}
    skip_set.discard("")
    started = datetime.now(timezone.utc)
    env = {"sources": {}, "layers": {}}
    # F9: endpoints hardcoded to PM-shaped URLs are SKIPPED for other
    # families and recorded as locked (honest 0-row measurement).
    # [S49-B] gen's lock set is now EMPTY (the startup unlock — every
    # one of the six sources is family-parameterized); the record says
    # so honestly instead of trailing "skipped:" with no list.
    if family != "pm":
        for locked in spec["locked_endpoints"]:
            if locked == "l0_bundle":
                skip_set.add("l0")
            else:
                skip_set.add(locked)
        env["sources"]["locked_endpoints"] = (
            f"skipped:{','.join(spec['locked_endpoints'])}"
            if spec["locked_endpoints"] else "none")
    all_rows: list[dict] = []

    # S41 family generalization → [S49-B / audit F13] the l0 dispatch is
    # family-aware: pm keeps the PM projection (bit-identical, pinned);
    # gen runs the ALL-ROLES projection (every corpus row + full JDs —
    # D2); dmm stays locked (its locked_endpoints keeps l0_bundle, so
    # skip_set carries "l0" and the lock fires here). --skip l0 still
    # wins for any family (the local-run routing).
    if "l0" in skip_set:
        l0, v = [], "locked_endpoint"
    elif family == "gen":
        l0, v = project_l0_gen()
    else:
        l0, v = project_l0()
    env["sources"]["l0_bundle"] = v
    env["layers"]["l0"] = len(l0)
    all_rows += l0

    # S38 §4a/F9: registration order matters for intra-tier ties —
    # findwork (full JD, cleanest) first in l1.5; the snippet tier
    # follows; muse/usajobs = l2 closure-tier (deliberate, documented).
    # [S46-B4 §3.3] the geo-ladder dispatch. At t3 the fetcher list is
    # UNCHANGED (the pre-B4 behavior — pm's bit-identity + the US-wide
    # lane). At the scoped tiers (t1a/t1b/t2/t4) ONLY the four
    # location-aware fetchers run per scope — the location-blind boards
    # return the same rows at every tier (the lane's t3 run carries
    # them; re-fetching per tier burns politeness budget for duplicate
    # rows). Each entry carries its scope label for the geoTier stamp
    # and the per-scope source status (the honest 4x adzuna verdict).
    geo_cfg = _geo_tier_config(geo_ladder, family)
    fetchers: list[tuple[str, object, list, str | None]] = []
    if geo_ladder == "t3":
        # [S47 F17] findwork goes multi-query for the B4 catch-all
        # families (A6: queries[0]-only meant 19/20 new core queries
        # never hit the full-JD source); pm keeps the pinned single-
        # search behavior bit-identical
        fetchers = [
            ("findwork", fetch_findwork,
             [queries, family in _B4_FAMILIES], None),
            ("adzuna", fetch_adzuna, [queries], None),
            ("dice", fetch_dice, [queries], None),
            ("careerjet", fetch_careerjet, [queries], None),
            ("jooble", fetch_jooble, [queries], None),
            ("indeed", fetch_jobspy_indeed, [queries], None),
        ]
        # [S47 F17 → S49 seam] zip/gd registration lives ONCE, in the
        # shared t3 block after gj_role (the parallel session's landing
        # of the same fix — the rebase would have doubled the calls:
        # 2x firecrawl credits per query; deduped here)
    else:
        remote = geo_cfg["remote"]
        for scope_loc, scope_dist in geo_cfg["scopes"]:
            # adzuna: t4 carries NO where (where=Remote → HTTP 400) —
            # the what_and=remote filter instead (adzuna.py:76-81)
            fetchers.append(("adzuna", fetch_adzuna, [
                queries,
                None if remote else scope_loc,
                None if remote else scope_dist,
                geo_cfg["category"],
                "remote" if remote else None], scope_loc))
            # careerjet: location="remote" maps to the remote search
            fetchers.append(("careerjet", fetch_careerjet,
                             [queries, scope_loc], scope_loc))
            # jooble/indeed: no remote param — the client-side filter
            fetchers.append(("jooble", fetch_jooble,
                             [queries, None if remote else scope_loc,
                              remote], scope_loc))
            fetchers.append(("indeed", fetch_jobspy_indeed,
                             [queries, None if remote else scope_loc,
                              remote], scope_loc))
        fetchers = [(n, f, a, s) for n, f, a, s in fetchers
                    if n not in skip_set]
    if geo_ladder == "t3":
        if not skip_li and "li" not in skip_set:
            # [S47 F6] the li 429 ladder (45/60/90s) kills full-mode
            # runs: 100 queries × 195s worst = 5.4h vs the 90m lane
            # timeout. Cap li at 25 queries when the list is full-ish
            # (>30 — pm's 7/14 stay bit-identical).
            fetchers.append(("li_keyword", fetch_li_keyword,
                             [queries if len(queries) <= 30
                              else queries[:25]], None))
        fetchers += [
            # [S49-B] the startup parameterization: family rides the
            # call args (pm defaults keep the pinned behavior
            # bit-identical — fetch_x(family="pm") ≡ fetch_x())
            ("yc", fetch_yc, [family], None),
            ("wellfound", fetch_wellfound, [], None),
            ("hn", fetch_hn, [family], None),
            ("wwr_rss", fetch_wwr, [family], None),
            ("free_trio", fetch_free_trio, [], None),
            ("talent", fetch_talent, [family], None),
        ]
        if "muse" not in skip_set:
            fetchers.append(("muse", fetch_muse, [family], None))
        if "usajobs" not in skip_set:
            fetchers.append(("usajobs", fetch_usajobs, [queries], None))
        if "builtin" not in skip_set:
            fetchers.append(("builtin", fetch_builtin, [queries], None))
        if "himalayas" not in skip_set:
            fetchers.append(("himalayas", fetch_himalayas,
                             [family], None))
        if not skip_gj and "gj" not in skip_set:
            fetchers.append(("gj_role", fetch_gj_role, [queries], None))
        # [S49, the S48 queue item 4] the firecrawl re-openings join
        # the t3 roster for the B4 families ONLY (pm keeps the
        # identity doctrine — its t3 stays bit-identical, pinned by
        # the identity tests): ziprecruiter (591 rows/query measured,
        # the S47-R1 fleet probe 2026-10-24) + glassdoor (4,557
        # rows/query) — 1 credit/query each, fleet-rotated via
        # _fc_scrape, fail-soft per query, --skip-gated like the rest.
        if family in _B4_FAMILIES:
            fetchers.append(("ziprecruiter", fetch_ziprecruiter,
                             [queries], None))
            fetchers.append(("glassdoor", fetch_glassdoor_zenrows,
                             [queries], None))
        fetchers = [(n, f, a, s) for n, f, a, s in fetchers
                    if n not in skip_set]

    for name, fn, args, fscope in fetchers:
        try:
            rows, v = fn(*args)
        except Exception as exc:               # fail-soft per source
            rows, v = [], f"crash:{type(exc).__name__}"
        env["sources"][f"{name}:{fscope}" if fscope else name] = v
        for r in rows:
            r.setdefault("company_key", _company_key(r["company"], {}))
            if family in _B4_FAMILIES:
                # [S46-B4 §3.3/§3.4] the tier stamp rides every
                # gen/dmm row (pm keeps the identity doctrine); the
                # adzuna category tag becomes the sector evidence.
                # SETDEFAULT: fresh fetcher rows carry no tier (stamped
                # with THIS run's); seed rows keep their OWN tier (the
                # cross-tier union correctness — a t1a seed unioned
                # into a t2 run stays t1a)
                r.setdefault("geoTier", geo_ladder)
                if r.get("adzuna_category"):
                    r.setdefault("sector_evidence",
                                 f"adzuna_category:{r['adzuna_category']}")
        all_rows += rows
        env["layers"][r["layer"] if rows else "?"] = \
            env["layers"].get(rows[0]["layer"] if rows else "?", 0) + len(rows)
        tag = f"{name}:{fscope}" if fscope else name
        print(f"[sweep:{tag:<20}] {len(rows):>4} rows | {v}")

    # per-source usgate priors (design §4 + S38 §4a/F21 + S39 F11 — the
    # new sources default client-side: dice/careerjet leak 1-2/30
    # non-US rows (measured, caught); yc is US-scoped by board filter;
    # builtin 'N Locations' cards → ambiguous; himalayas
    # locationRestrictions are client-side)
    SRC_US = {"adzuna": "us", "jooble": None, "indeed": "us",
              "li": "us", "wwr": "remote", "remoteok": None,
              "jobicy": None, "arbeitnow": None, "gj": None,
              "board": "skip", "dice": None, "careerjet": None,
              "ziprecruiter": None, "glassdoor": None,
              "findwork": None, "muse": None, "usajobs": "us",
              "yc": None, "wellfound": None, "hn": None,
              "talent": None, "builtin": None, "himalayas": None}
    counters = {"family_other": 0, "nontech_gate": 0, "dedup_merge": 0,
                "usgate_non_us": 0, "pending_sector": 0,
                "staffing_repost": 0, "employer_anonymized": 0,
                "dedup_dateless": 0, "residual_dup_est": 0,
                "g1b_title": 0, "screening_post": 0,
                "seed_rows_in": 0, "seed_dup_absorbed": 0,
                "seed_dup_residual": 0, "roster_kept": 0,
                "agency_pending": 0, "res7_merge": 0, "cross_src_merge": 0,
                "jd_cache_hits": 0, "jd_seed_admitted": 0}
    dropped: list[dict] = []
    kept: list[dict] = []
    seniority_conflicts = 0
    title_conflicts = 0
    day_delta_hist: dict[str, int] = {}

    # S39 §A5 (F1/F2): the seed-envelope union — rows from a prior
    # envelope re-enter as inputs AFTER all fresh rows (dateless-donor
    # ordering), staleness-bounded (posted ≥ now-60d; dateless rows are
    # NOT seed-eligible). Field map: postedOn→posted, description→desc
    # (4,000-char cap accepted), layer reconstructed from source,
    # rid parsed from src:-prefixed ids, everything else passes the
    # SAME gates + merge rules as fresh rows. Careerjet is HK-only and
    # 403s on the Azure lane — the seed is how its rows reach production.
    if seed_envelope:
        seed_path = REPO / seed_envelope
        n_seed = 0
        try:
            sd = json.load(open(seed_path))
            cutoff = (datetime.now(timezone.utc).date()
                      - timedelta(days=60))
            _SRC_LAYER = {"adzuna": "l1.5", "dice": "l1.5",
                          "careerjet": "l1.5", "jooble": "l1.5",
                          "indeed": "l1.5", "findwork": "l1.5",
                          "ziprecruiter": "l1.5", "glassdoor": "l1.5",
                          "li": "l1", "gj": "l3", "yc": "l2",
                          "wellfound": "l2", "hn": "l2", "wwr": "l2",
                          "remoteok": "l2", "jobicy": "l2",
                          "arbeitnow": "l2", "muse": "l2",
                          "usajobs": "l2", "talent": "l2",
                          "builtin": "l2", "himalayas": "l2",
                          "board": "l0"}
            for row in (sd.get("jobs") or
                        sd.get("rows") or []):
                posted = (row.get("postedOn") or "")[:10]
                day = _norm_day(posted)
                # S41-R1 F1: dateless rows are seed-INeligible EXCEPT
                # when a cached full JD exists for (source, url) — the
                # refetch evidence rides them back through grading via
                # the cache consult below. Bounded to exactly the rows
                # with new evidence (214 dateless jooble pending rows
                # otherwise ride only fresh re-crawl and vanish from
                # the bundle by attrition, never by verdict).
                if day is None:
                    if not _load_jd_cache().get(_jd_cache_key(
                            row.get("source") or "",
                            row.get("url") or "")):
                        continue
                    counters["jd_seed_admitted"] += 1
                elif day < cutoff:
                    continue           # F2: staleness bound (45-60d)
                src = row.get("source") or ""
                rid = row.get("id") or ""
                # [W2a-8] the rid whitelist follows the prefixes the
                # fetchers themselves mint: ziprecruiter rows carry
                # `ziprecruiter:{coslug}:{titleslug}`, glassdoor rows
                # `glassdoor:jl{jid}` — both were missing, so their
                # seed rids were stripped (rid-dedup disabled; the
                # finding's zp/gd shorthand does NOT match either
                # shape). The bound widens 10→16 so the 12-char
                # `ziprecruiter` prefix parses. The zenrows-fallback
                # `gd_{n}_{hash}` ids stay STRIPPED by design — a
                # per-process salted hash is a row-hash, not a rid
                # (the F1 doctrine).
                m = re.match(r"^([a-z]{2,16}):", rid)
                if not (m and m.group(1) in
                        ("adz", "cj", "dice", "fw", "jbl", "ex",
                         "usaj", "muse", "yc", "wf", "hn", "him",
                         "tal", "gj", "ziprecruiter", "glassdoor")):
                    rid = ""          # row-hash ids are NOT rids (F1)
                # [S49-B → S50-A] seed-union semantics: (a) l0/board
                # rows NEVER re-enter via the seed for ANY family —
                # they are re-projected fresh every run (the corpus
                # is local; rid-stripped seed copies would double the
                # merge input for zero information). S49-B scoped the
                # skip gen-only; the pm lane then lived one corpus
                # churn away from a tail crash (PM run 16, 2026-10-29:
                # the S49-C corpus→cache join made the 11 DATELESS
                # board seed copies admissible for the first time;
                # they enter at layer l0 where the classify loop
                # never stamps family, and one donor-miss put a
                # family-less row in merged → KeyError: 'family' at
                # the histogram — 20 min of fetch work lost at the
                # tail). All 68 fresh + 11 dateless pm board seeds
                # measured WITH fresh corpus twins: the skip is a
                # pure no-op for green runs, doctrine-aligned (l0
                # attrition is by corpus verdict, not seed attrition)
                # and kills the class at the source. (b) the STARTUP
                # rows re-enter WITH their family stamp (the pre-stamp
                # bypass) — without it the geo-tier runs (t1a/t1b/
                # t2/t4 don't fetch the startup sources) would attrit
                # every non-office startup row at classify_gen. dmm
                # has no l0 rows and no pre-stamped startup rows.
                if src == "board":
                    continue
                all_rows.append({
                    "title": row.get("title") or "",
                    "company": row.get("company") or "",
                    "loc": row.get("loc") or "",
                    "url": row.get("url") or "",
                    "posted": posted,
                    # S40 fix: the bundle field is 'desc' — reading
                    # 'description' returned None and STRIPPED the JD
                    # from every seed row (1,282 careerjet rows hit
                    # descLen=0 on lane #5; the jd_* sector graders
                    # could never fire on them)
                    "desc": (row.get("desc")
                             or row.get("description") or "")[:4000],
                    "source": src, "layer": _SRC_LAYER.get(src, "l2"),
                    "query": "seed", "rid": rid,
                    "company_key": _company_key(
                        row.get("company") or "", {}),
                    "_seed": True,
                })
                if family == "gen" and (row.get("family") or "") == "gen":
                    all_rows[-1]["family"] = "gen"
                    all_rows[-1]["family_evidence"] = (
                        row.get("familyEvidence") or "")
                # [S49-D4] salary round-trip: adzuna rows re-entering
                # via the seed keep their bands (the ui bundle carries
                # salaryMin/salaryMax gen/dmm-side; without this map the
                # weekly seed round-trip strips salary from every
                # 60d-fresh adzuna row — audit F18's seed-map seam).
                if row.get("salaryMin") is not None:
                    all_rows[-1]["salary_min"] = row["salaryMin"]
                if row.get("salaryMax") is not None:
                    all_rows[-1]["salary_max"] = row["salaryMax"]
                # [S46-B4 §3.6 join-i] the seed row keeps its OWN
                # geoTier (the cross-tier union: the bundle job carries
                # it; the dispatch stamp is a setdefault so it survives)
                if row.get("geoTier"):
                    all_rows[-1]["geoTier"] = row["geoTier"]
                elif family in _B4_FAMILIES:
                    # [W2a-17] the one-time legacy stamp: pre-B4 seed
                    # rows (914 in the shipped dmm bundle) re-entered
                    # with geoTier blank — ~24% of the bundle's tier
                    # field stayed "" forever. They were all t3 US-wide
                    # harvests (pre-B4 there was no ladder), so the
                    # honest provenance tier is t3_legacy — NOT the
                    # current run's ladder. Naturally one-time: on the
                    # next round-trip the row carries a tier and rides
                    # the branch above. Fresh rows never see this
                    # (they take the setdefault at the fetch loop).
                    # Merge-unsafe? NO — unknown tiers rank 9
                    # (least-specific) in GEO_TIER_RANK, exactly like
                    # the old "" — a fresh t3/t2/t1a double of the
                    # same key still wins the survivor role.
                    all_rows[-1]["geoTier"] = "t3_legacy"
                n_seed += 1
            env["sources"]["seed_envelope"] = (
                f"ok:{n_seed}/{len(sd.get('jobs') or [])}")
        except FileNotFoundError:
            # S41-R1 F17: a family's FIRST sweep has no prior envelope
            # yet (roles-dmm.json run 1) — an absent seed is a skip,
            # not a dead source. The PM lane stays FAIL-LOUD (the
            # careerjet doctrine rides its committed seed — a typo'd
            # path must surface as an error, never a silent skip).
            env["sources"]["seed_envelope"] = (
                "ok:skipped" if family != "pm"
                else "error:FileNotFoundError")
        except Exception as exc:
            env["sources"]["seed_envelope"] = (
                f"error:{type(exc).__name__}")
        counters["seed_rows_in"] = n_seed
        print(f"[sweep:seed_envelope] {n_seed:>4} rows (staleness-bounded)")

    for r in all_rows:
        title, company = r.get("title", ""), r.get("company", "")
        # [S49-B] the family pre-stamp bypass: the gen-mode startup
        # fetchers emit rows with family/family_evidence already stamped
        # (the all-roles doctrine — hn walks ALL hiring comments, wwr
        # sweeps the all-jobs feed; classify_gen would kill 93%+ of
        # them, audit F6). Pre-stamped rows skip ONLY the family gate;
        # seniority/us_status/description_source still stamp below. pm
        # fetchers never pre-stamp family → the added `not r.get
        # ("family")` clause is pm-inert (pinned: test_gen_family_
        # prestamp_bypasses_gate).
        if r.get("layer") != "l0":
            if not r.get("family"):
                fam, ev = spec["gate"](title, query_is_pm=True)
                if fam == "other":
                    counters["family_other"] += 1
                    continue
                r["family"], r["family_evidence"] = fam, ev
            r["seniority"] = pm_seniority(title)
            prior = SRC_US.get(r.get("source", ""), None)
            if prior == "us":
                r["us_status"], r["us_evidence"] = "us", "endpoint"
            elif prior == "remote":
                r["us_status"], r["us_evidence"] = "remote", "source-remote"
            else:
                us = _classify_loc(r.get("loc", ""))
                r["us_status"] = us
                r["us_evidence"] = "client-side"
            r["description_source"] = ("gj" if r.get("source") == "gj"
                                       else "none")
            if r.get("desc"):
                r["description_source"] = r["source"]
        # S41 jd_cache consult (DESIGN-S41 §1.5): a cached FULL JD
        # (jd-refetch lane / local leg, keyed source+url) upgrades a
        # thin API snippet BEFORE sector_verdict — the desc-informed
        # tiers fire on real text and pending rows flip out of the
        # queue at the natural bundle regeneration. Strictly-longer +
        # overlap-gated: a garbage-longer capture never replaces a
        # clean snippet. Seed rows reach the same hook (they flow
        # through this loop with url carried).
        _cinfo = _load_jd_cache().get(
            _jd_cache_key(r.get("source", ""), r.get("url", "")))
        if (_cinfo
                and _cinfo.get("status") == "ok"
                and _cinfo.get("desc_len", 0) > len(r.get("desc") or "")):
            if _jd_overlap_ok(r.get("desc") or "", _cinfo["desc"],
                              title):
                r["desc"] = _cinfo["desc"][:4000]
                r["description_source"] = "jd_cache"
                counters["jd_cache_hits"] += 1
        # usgate drop: non-US non-remote (the A-prime rule at sweep time)
        if r.get("us_status") == "non_us":
            counters["usgate_non_us"] += 1
            continue
        # gates (G1 + G1B + screening-post + sector verdict) — apply to
        # pm AND adjacent rows. [S49-B / audit F7] the gen-l0 all-roles
        # doctrine: for gen l0 rows the three title gates and the
        # sector 'bucket' drop are OFF — they encode the pm office/tech
        # scope, not corpus quality, and would amputate real postings
        # from the curated watch boards (miniso store-ops, fuyao
        # production, faraday-future…); 'bucket' demotes to pending
        # (ships flagged ▲ — the corpus membership is itself the
        # sector evidence). Family-gated so pm/dmm (and gen non-l0
        # rows) keep the pinned behavior.
        _l0_allroles = (family == "gen" and r.get("layer") == "l0")
        if not _l0_allroles and (g1_fires(title)
                                 or g1_fires(r.get("loc", ""))):
            counters["nontech_gate"] += 1
            dropped.append({**r, "reason": "g1_title",
                            "recoverable": False})
            continue
        if not _l0_allroles and g1b_fires(title):
            # S38: title-only, recoverable
            counters["g1b_title"] += 1
            dropped.append({**r, "reason": "g1b_title",
                            "recoverable": True})
            continue
        if not _l0_allroles and is_screening_post(title):
            # S39 N7: recoverable bucket
            counters["screening_post"] += 1
            dropped.append({**r, "reason": "screening_post",
                            "recoverable": True})
            continue
        # S38 flags (never drops): staffing repost + anonymized/board
        # employer (N9: Jobgether-class — company field is not the
        # real employer; flagged + merge-excluded, kept)
        # S40 A3/F3: the agency resolution also consults the slug-keyed
        # STAFF_SEED fold (the display-name regex cannot see companyKeys)
        r["staffing_evidence"] = (
            "agency" if (is_staffing_company(company)
                        or _seeds.staff_seed_hit(r.get("company_key"),
                                                 company)) else "")
        if r["staffing_evidence"]:
            counters["staffing_repost"] += 1
        r["employer_anonymized"] = bool(
            is_anonymized_employer(company) or is_board_employer(company))
        if r["employer_anonymized"]:
            counters["employer_anonymized"] += 1
        act, sev, reason = sector_verdict(
            title, company, r.get("desc", ""),
            r.get("company_key"))
        if act == "bucket" and _l0_allroles:
            # [S49-B / audit F7] gen-l0: the watch-board membership is
            # itself the sector evidence — bucket demotes to pending
            # (265 measured corpus rows: faraday-future, omnivision,
            # united-imaging…), sectorReason preserved, ships flagged ▲
            act = "pending"
        if act == "bucket":
            counters["nontech_gate"] += 1
            dropped.append({**r, "reason": reason, "recoverable": True})
            continue
        # [W2a-4] preserve the fetch-time adzuna_category stamp when
        # the verdict has no evidence of its own (sev empty) — the
        # unconditional write wiped EVERY adzuna_category stamp (0
        # across 4,590 adzuna rows in both bundles)
        if sev:
            r["sector_evidence"] = sev
        r["sector_reason"] = reason
        if act == "pending":
            # S39 §B1: the agency evidence-grade — staffing-flagged
            # pending rows resolve as 'agency' (repost attribution),
            # ranking division > jd > roster > agency > pending
            if r["staffing_evidence"]:
                r["sector_evidence"] = "agency"
                r["sector_reason"] = "agency:repost"
                counters["agency_pending"] += 1
            else:
                counters["pending_sector"] += 1
        if sev == "roster":
            counters["roster_kept"] += 1
        kept.append(r)

    # dedup: rid-dedup (S38 F1) → intra-layer exact → cross-layer
    # title_core ±1 day → dateless-donor (S38 F1: exact raw-title +
    # company_key, day=None donors)
    _ANON_KEYS = {"confidential", "company-confidential", "stealth",
                  "stealth-startup", "unknown", "hidden", "undisclosed",
                  "anonymous", "private-company",
                  "a-vinyl-bar-in-shibuya",
                  # N9 board-employers (shared key across unrelated jobs)
                  "jobgether", "us-government-jobs", "ziprecruiter",
                  "adzuna", "jooble", "careerjet", "indeed", "linkedin"}
    by_key: dict[tuple, dict] = {}
    by_rid: dict[str, dict] = {}
    # [S49-B / audit F9] the by_key dict-indexing: the seven per-row
    # linear scans over by_key made the ladder O(7n²) — the gen
    # expansion's 23.9k-row input (seed 15,717 + l0 8,161) blew the
    # 120s budget (>300s measured, merge_ladder_timing.py). Every
    # equality-keyed rule now hits a bucket dict keyed by its own
    # match fields; each bucket list preserves the by_key INSERTION
    # ORDER (append on insert, remove on the survivor swap), so the
    # first bucket member is bit-identically the first by_key member
    # the linear scan returned. Day-range rules (re7 / xs3 / core±1)
    # scan only their small (title,ck,…) bucket with the SAME
    # predicate. pm-inert by construction: same rules, same order,
    # same merges — only the candidate enumeration changed (pinned
    # by tests/test_s40_role_sweep.py).
    ix_ukey: dict[tuple, list] = {}   # S40 url-identity
    ix_exact: dict[tuple, list] = {}   # exact (t_norm, ck, layer, day)
    ix_tcs: dict[tuple, list] = {}    # (t_norm, ck, src) — re7 ±7d
    ix_tck: dict[tuple, list] = {}    # (t_norm, ck) — xs3/dateless
    ix_adz: dict[tuple, list] = {}    # (t_norm, ck, loc12) — B6 suffix
    ix_core: dict[tuple, list] = {}   # title_core cross-layer ±1d

    def _ix_add(row: dict, kid) -> None:
        ix_ukey.setdefault(row["_ukey"], []).append(kid)
        ix_exact.setdefault(row["_exact"], []).append(kid)
        ix_tcs.setdefault((row["_title_norm"], row["_ck"],
                           row["_src"]), []).append(kid)
        ix_tck.setdefault((row["_title_norm"], row["_ck"]),
                          []).append(kid)
        ix_adz.setdefault((row["_title_norm"], row["_ck"],
                           row["_loc_prefix"]), []).append(kid)
        ix_core.setdefault(row["_core"], []).append(kid)

    def _ix_del(row: dict, kid) -> None:
        # the donor's OWN _-fields name every bucket it lives in
        for ix, bkey in (
                (ix_ukey, row["_ukey"]), (ix_exact, row["_exact"]),
                (ix_tcs, (row["_title_norm"], row["_ck"], row["_src"])),
                (ix_tck, (row["_title_norm"], row["_ck"])),
                (ix_adz, (row["_title_norm"], row["_ck"],
                          row["_loc_prefix"])),
                (ix_core, row["_core"])):
            bucket = ix.get(bkey)
            if bucket and kid in bucket:
                bucket.remove(kid)

    for r in kept:
        day = _norm_day(r.get("posted", ""))
        # F14: anonymized employer keys never merge (shared key across
        # unrelated employers — false-merge guard)
        ck = r["company_key"]
        if ck in _ANON_KEYS:
            ck = f"{ck}#{r.get('rid') or id(r)}"      # salted
        t_norm = re.sub(r"\W", "", (r["title"] or "").lower())
        exact = (t_norm, ck, r.get("layer"), day)
        core = (title_core_family(r["title"] or "", family), ck)
        rid = r.get("rid") or ""
        merge_into = None
        if rid and rid in by_rid:                    # F1: rid-dedup first
            merge_into = id(by_rid[rid])
        if merge_into is None and (r.get("url") or "").strip():
            # S40 (url-identity): same source + same non-empty url +
            # same title_norm = the SAME posting, regardless of the
            # anonymized/board-employer ck salt. Kills the compounding
            # dup class: board-employer rows (company=Indeed/LinkedIn/
            # adzuna/...) are F14-salted per-row so the company-key
            # merge rules can NEVER fire on them — every seed-union
            # run re-inserted the prior envelope copy (the
            # indeed:4e33... triple, +1 per run). url equality is the
            # identity signal the salt cannot defeat (same url = same
            # posting page; different titles never match).
            ukey = (t_norm,
                    (r.get("url") or "").strip(),
                    r.get("source") or "")
            bucket = ix_ukey.get(ukey)
            if bucket:
                merge_into = bucket[0]
                counters["url_identity_merge"] = counters.get(
                    "url_identity_merge", 0) + 1
        if merge_into is None:
            bucket = ix_exact.get(exact)
            if bucket:
                merge_into = bucket[0]
        if merge_into is None and day is not None:
            # S38-C4: the intra-source re-crawl class (Seismic ×3 on
            # consecutive careerjet crawl days) — same source, same
            # title, same company, ≤7 days apart = the same posting
            # re-crawled, merge (earliest evidence wins)
            for k in ix_tcs.get((t_norm, ck, r.get("source")), ()):
                cand = by_key[k]
                if cand.get("_day") is not None and \
                        abs((day - cand["_day"]).days) <= 7:
                    merge_into = k
                    day_delta_hist["re7"] = \
                        day_delta_hist.get("re7", 0) + 1
                    counters["res7_merge"] = counters.get("res7_merge", 0) + 1
                    break
        if merge_into is None and day is not None and \
                r.get("layer") == "l1.5":
            # S39 F1(d): intra-L1.5 CROSS-SOURCE exact title_norm +
            # same company_key + day Δ≤3 — the cross-engine date-skew
            # class (careerjet/adzuna re-host the same ATS feed with
            # 0-3d posting-date skew; the seed union's main dedup
            # path). title_core is deliberately NOT used (the bjak
            # 12-variant multi-req class stays un-merged).
            for k in ix_tck.get((t_norm, ck), ()):
                cand = by_key[k]
                if cand.get("_layer") == "l1.5" and \
                        cand.get("_src") != r.get("source") and \
                        cand.get("_day") is not None and \
                        abs((day - cand["_day"]).days) <= 3:
                    merge_into = k
                    counters["cross_src_merge"] += 1
                    day_delta_hist[f"xs{abs((day - cand['_day']).days)}"] = \
                        day_delta_hist.get(
                            f"xs{abs((day - cand['_day']).days)}", 0) + 1
                    break
        if merge_into is None:
            # S39 B6/F15 (N8): the multi-agency same-req pair class —
            # adzuna staffing rows whose trailing '(City)' suffix
            # duplicates an unsuffixed sibling at the same loc. The
            # city-suffix strip applies to the MERGE KEY ONLY (the
            # shipped title is never mutated). Staffing-flagged,
            # adzuna-only, loc-prefix-matched — conservative by design.
            if r.get("source") == "adzuna" and r.get("staffing_evidence"):
                bare = re.sub(
                    r"\s*\((?:[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\)\s*$", "",
                    (r["title"] or "")).strip()
                bare_norm = re.sub(r"\W", "", bare.lower())
                if bare_norm and bare_norm != t_norm:
                    for k in ix_adz.get((bare_norm, ck,
                                         (r.get("loc") or "")[:12]), ()):
                        cand = by_key[k]
                        if cand.get("_src") == "adzuna":
                            merge_into = k
                            counters["res7_merge"] = counters.get(
                                "res7_merge", 0) + 1
                            break
        if merge_into is None and day is not None:
            for k in ix_core.get(core, ()):
                cand = by_key[k]
                if cand.get("_layer") != r.get("layer") and \
                        cand.get("_day") is not None and \
                        abs((day - cand["_day"]).days) <= 1:
                    merge_into = k
                    d = abs((day - cand["_day"]).days)
                    day_delta_hist[str(d)] = \
                        day_delta_hist.get(str(d), 0) + 1
                    break
        if merge_into is None and day is None:
            # F1 dateless-donor: exact raw title + same company key
            bucket = ix_tck.get((t_norm, ck))
            if bucket:
                merge_into = bucket[0]
                counters["dedup_dateless"] += 1
        if merge_into is not None and merge_into not in by_key:
            # S39: stale by_rid pointer (the donor was swapped out of
            # by_key by a later higher-rank survivor) — treat as fresh
            merge_into = None
        if merge_into is None:
            r["_exact"], r["_core"], r["_day"], r["_layer"] = \
                exact, core, day, r.get("layer")
            r["_ck"], r["_title_norm"], r["_src"] = ck, t_norm, \
                r.get("source")
            r["_loc_prefix"] = (r.get("loc") or "")[:12]
            r["_ukey"] = (t_norm,
                          (r.get("url") or "").strip(),
                          r.get("source") or "")
            by_key[id(r)] = r
            _ix_add(r, id(r))
            if rid:
                by_rid[rid] = r
            if r.get("_seed"):
                counters["seed_dup_residual"] += 1
            continue
        # MERGE, never drop (the 8-rule table; priority = LAYER_RANK:
        # L0 board > L3 gj > L1.5 keyed > L1 li > L2 closure)
        tgt = by_key[merge_into]
        if LAYER_RANK.get(r.get("layer"), 9) < \
                LAYER_RANK.get(tgt.get("layer"), 9):
            survivor, donor = r, tgt
        elif (LAYER_RANK.get(r.get("layer"), 9) ==
              LAYER_RANK.get(tgt.get("layer"), 9)
              and GEO_TIER_RANK.get(r.get("geoTier"), 9) <
              GEO_TIER_RANK.get(tgt.get("geoTier"), 9)):
            # [S46-B4 join-i] equal transport layer -> the MORE
            # SPECIFIC geo tier survives (t1a > t1b > t2 > t3 > t4) --
            # a metro row beats its own US-wide double
            survivor, donor = r, tgt
        else:
            survivor, donor = tgt, r
        counters["dedup_merge"] += 1
        if r.get("_seed") or tgt.get("_seed"):
            counters["seed_dup_absorbed"] += 1
        if survivor.get("seniority") != donor.get("seniority"):
            seniority_conflicts += 1
        if title_core_family(survivor.get("title") or "", family) != \
                title_core_family(donor.get("title") or "", family):
            title_conflicts += 1
        # postedOn: earliest evidence across members
        sday = _norm_day(survivor.get("posted", ""))
        dday = _norm_day(donor.get("posted", ""))
        if dday is not None and (sday is None or dday < sday):
            survivor["posted"], survivor["postedOnSource"] = \
                donor.get("posted"), donor.get("source")
        # description: longest wins (gj/adzuna carry JD; li cards none)
        if len(donor.get("desc", "")) > len(survivor.get("desc", "")):
            survivor["desc"], survivor["description_source"] = \
                donor["desc"], donor.get("description_source")
        # [S49-D4] salary: the survivor inherits the donor's bands
        # when its own are blank — an adzuna donor's UNIQUE data (the
        # only salary-bearing source) must survive the merge that
        # precedes it (audit F18's survivor-inheritance seam). Row-
        # internal only: the emit-level family gate keeps the pm
        # bundle/CSV byte-identical.
        for _sal in ("salary_min", "salary_max"):
            if survivor.get(_sal) is None and donor.get(_sal) is not None:
                survivor[_sal] = donor[_sal]
        # location: priority-layer first; donor fills blanks (R2's
        # L4-detail > gj-card > li-card ladder = the rank order)
        if not survivor.get("loc") and donor.get("loc"):
            survivor["loc"] = donor["loc"]
        # S38 F2: the survivor INHERITS the donor's keep-side sector
        # evidence when its own is blank (yc/hn/jd evidence must
        # survive the merge that precedes it)
        if not survivor.get("sector_evidence") and \
                donor.get("sector_evidence"):
            survivor["sector_evidence"] = donor["sector_evidence"]
            survivor["sector_reason"] = donor.get("sector_reason", "")
        if not survivor.get("url") and donor.get("url"):
            survivor["url"] = donor["url"]
        if donor.get("staffing_evidence") and \
                not survivor.get("staffing_evidence"):
            survivor["staffing_evidence"] = donor["staffing_evidence"]
        survivor.setdefault("provenance", []).append({
            "layer": donor.get("layer"), "source": donor.get("source"),
            "title": donor.get("title"), "posted": donor.get("posted")})
        if survivor is r:
            # the NEW row outranks the stored one — swap membership
            r["_exact"], r["_core"], r["_day"], r["_layer"] = \
                exact, core, day, r.get("layer")
            r["_ck"], r["_title_norm"], r["_src"] = ck, t_norm, \
                r.get("source")
            r["_loc_prefix"] = (r.get("loc") or "")[:12]
            r["_ukey"] = (t_norm,
                          (r.get("url") or "").strip(),
                          r.get("source") or "")
            del by_key[merge_into]
            _ix_del(tgt, merge_into)   # the donor leaves every bucket
            # S39: purge stale by_rid pointers to the swapped-out donor
            # (a later same-rid row would resolve to a dead key)
            for rid_k, rid_v in list(by_rid.items()):
                if rid_v is tgt:
                    del by_rid[rid_k]
            by_key[id(r)] = r
            _ix_add(r, id(r))          # the survivor re-enters at the end
            if rid:
                by_rid[rid] = r

    merged = list(by_key.values())

    # S38 F2: post-merge board-grade evidence — every YC-backed company
    # (the yc layer's companySlug set, slug-equal to company_key) and
    # every hn-source row get keep-side board evidence
    yc_slugs = {r.get("yc_slug") for r in all_rows
                if r.get("source") == "yc" and r.get("yc_slug")}
    for r in merged:
        ck = re.sub(r"[^a-z0-9]+", "-", (r.get("company") or "")
                    .lower()).strip("-")
        if r.get("sector_evidence") in ("", None):
            if ck and ck in yc_slugs:
                r["sector_evidence"], r["sector_reason"] = \
                    "board", "yc_startup"
            elif any(p.get("source") == "hn"
                     for p in r.get("provenance", [])) or \
                    r.get("source") == "hn":
                r["sector_evidence"], r["sector_reason"] = \
                    "board", "hn_whihiring"

    # S38 F10: the cross-source residual-dup estimate (title_core +
    # loc-prefix collisions across sources — the measured gap bucket
    # that feeds the companyKey variant map's next growth)
    seen_pairs: set[tuple] = set()
    for r in merged:
        pair = (title_core(r["title"] or ""),
                (r.get("loc") or "").split(",")[0].strip().lower())
        if pair in seen_pairs:
            counters["residual_dup_est"] += 1
        else:
            seen_pairs.add(pair)

    for r in merged:
        r.pop("_exact", None), r.pop("_core", None)
        r.pop("_day", None), r.pop("_layer", None)
        r.pop("_ck", None), r.pop("_title_norm", None)
        r.pop("_src", None), r.pop("_ukey", None)

    # [S48-B] the smbSignal join at emit time (B4 §4): classify every
    # merged row's company against the census pools + the repo
    # dictionaries — gen/dmm ONLY (pm stays bit-identical: no field,
    # no counters, no print). The CSV writer ignores the extra key
    # (extrasaction="ignore" — the inventory schema stays pre-S48).
    smb_hist: dict[str, int] = {}
    if family in _B4_FAMILIES:
        smb_hist = _smb_classify_rows(merged, counters)
        env["smb_signal_histogram"] = smb_hist

    # outputs (write-atomic)
    ROLES_DIR.mkdir(parents=True, exist_ok=True)
    _write_atomic(ROLES_DIR / f"{prefix}_inventory.csv",
                  _csv_bytes(merged, family))
    _write_atomic(ROLES_DIR / f"{prefix}_dropped.csv",
                  _csv_bytes(dropped, family))

    env.update({
        "ran_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "queries": queries,
        "rows_kept": len(merged),
        "rows_dropped": len(dropped),
        "counters": counters,
        "seniority_conflicts": seniority_conflicts,
        "title_conflicts": title_conflicts,
        "day_delta_histogram": day_delta_hist,
        "dedup_merges": counters["dedup_merge"],
    })
    # [S50-A] the .get hardening: the histogram is post-merge METADATA
    # — a missing stamp must surface as an honest "?" bucket, never a
    # KeyError that kills the run AFTER the fetch work is done (the
    # PM-16 crash shape; defense-in-depth behind the seed-union fix).
    fam_hist: dict[str, int] = {}
    sen_hist: dict[str, int] = {}
    for r in merged:
        _f = r.get("family") or "?"
        _s = r.get("seniority") or "?"
        fam_hist[_f] = fam_hist.get(_f, 0) + 1
        sen_hist[_s] = sen_hist.get(_s, 0) + 1
    env["family_histogram"] = fam_hist
    env["seniority_histogram"] = sen_hist
    _write_atomic(ROLES_DIR / f"{prefix}_envelope.json",
                  json.dumps(env, indent=1, default=str).encode())

    # cumulative closure ledger (design S36 §6, family-generalized)
    # [W2a-16] the fields are family-neutral and MAINTAINED now: the
    # old `cumulative_pm_rows` name lied for gen/dmm (it counted THAT
    # family's rows), and `max_consecutive` never updated (always 0
    # in the shipped gen_coverage.json). Semantics: consecutive SWEEP
    # RUN-DAYS — a company keeps its streak iff it appeared in the
    # immediately-previous run too (tracked via the _meta.last_run_day
    # sentinel this update stamps); any run-day it misses breaks it.
    cov = {}
    cov_path = ROLES_DIR / f"{prefix}_coverage.json"
    if cov_path.exists():
        try:
            cov = json.load(open(cov_path))
        except json.JSONDecodeError:
            cov = {}
    prev_run_day = (cov.get("_meta") or {}).get("last_run_day", "")
    today = started.strftime("%Y-%m-%d")
    comp_counts: dict[str, int] = {}
    for r in merged:
        comp_counts[r["company_key"]] = \
            comp_counts.get(r["company_key"], 0) + 1
    roster_keys: set[str] = set()
    idx = UI_DIR / "index.json"
    if idx.exists():
        try:
            roster_keys = {c.get("companyKey") for c in
                           json.load(open(idx)).get("companies", [])}
        except json.JSONDecodeError:
            roster_keys = set()
    board_keys = {r["company_key"] for r in merged
                  if r.get("layer") == "l0"}
    for ck, n in comp_counts.items():
        e = cov.setdefault(ck, {"first_seen": today, "last_seen": today,
                                "weeks_present": 0,
                                "max_consecutive": 0,
                                "cumulative_rows": 0, "class":
                                "aggregator_only"})
        # [W2a-16] one-time rename: legacy files carry the pm-named
        # key — carry its value forward, then drop it
        if "cumulative_pm_rows" in e:
            e["cumulative_rows"] = e.get(
                "cumulative_rows", e.pop("cumulative_pm_rows"))
        if e["last_seen"] != today:
            e["weeks_present"] += 1
            # [W2a-16] the streak: unbroken iff the company ALSO
            # appeared in the immediately-previous run (its last_seen
            # IS the previous run-day); else it starts fresh at 1
            e["current_streak"] = (
                e.get("current_streak", 0) + 1
                if prev_run_day and e["last_seen"] == prev_run_day
                else 1)
            e["max_consecutive"] = max(e["max_consecutive"],
                                       e["current_streak"])
        elif not e.get("current_streak"):
            # first appearance (a new entry, or the first touch after
            # the rename on a same-day re-run) — the streak starts at 1
            e["current_streak"] = 1
            e["max_consecutive"] = max(e["max_consecutive"], 1)
        e["last_seen"] = today
        e["cumulative_rows"] += n
        if ck in board_keys or ck in roster_keys:
            e["class"] = "onboarded_board"
        elif e.get("class") not in ("onboarded_board", "deferred_queue"):
            e["class"] = "aggregator_only"
    # [W2a-16] the streak sentinel the NEXT update compares against;
    # _meta is OUR key — consumers iterate companies, never _meta
    cov["_meta"] = {"last_run_day": today}
    _write_atomic(cov_path, json.dumps(cov, indent=1, default=str).encode())

    # drift ledger + baseline fold (design §5)
    verdict = "complete" if len(env["sources"]) else "failed"
    # S38: dead = error verdict AND zero rows delivered (an engine that
    # hit its quota wall mid-run but delivered 1,800 rows is PARTIAL,
    # not dead — the drift source-death alert must not false-fire)
    rows_by_source: dict[str, int] = {}
    for r in all_rows:
        rows_by_source[r.get("source", "?")] = \
            rows_by_source.get(r.get("source", "?"), 0) + 1
    ledger_name = {"board": "l0_bundle", "li": "li_keyword",
                   "gj": "gj_role", "remoteok": "free_trio",
                   "jobicy": "free_trio", "arbeitnow": "free_trio"}
    dead = []
    for k, v in env["sources"].items():
        if not v.startswith(("error", "crash", "preflight-fail")):
            continue
        delivered = 0
        for src_key, ledger_key in ledger_name.items():
            if ledger_key == k:
                delivered += rows_by_source.get(src_key, 0)
        delivered += rows_by_source.get(k, 0)
        if delivered == 0:
            dead.append(k)
    if len(merged) == 0:
        verdict = "failed"
    # S41 family: the drift ledger is PER-FAMILY (roles:pm / roles:dmm
    # — drift_alert globs roles:*.runs.jsonl). The lane-1 DMM run
    # appended into the PM ledger before this fix (the family spec's
    # drift_label was written but the append still hit the module-level
    # PM constant — found via the checkpoint's pathspec-128).
    ledger_path = WATCH_DIR / f"{spec['drift_label']}.runs.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "family": family,             # S41: self-identifying entries
            "verdict": verdict, "rows": len(merged),
            "sources_dead": dead,
        }) + "\n")
    baselines: dict = {}
    if BASELINE.exists():
        try:
            baselines = json.load(open(BASELINE))
        except json.JSONDecodeError:
            baselines = {}
    series = baselines.get(spec["drift_label"], [])
    if not series or series[-1]["d"] != today:
        series.append({"d": today, "rows": len(merged)})
    baselines[spec["drift_label"]] = series[-120:]
    _write_atomic(BASELINE, json.dumps(baselines, indent=1).encode())

    # UI bundle (roles-pm.json — design §5, rides the ui/ mirror sync).
    # S38-C4: ids are row-hashes (company_key + title + loc + source)
    # — the companyId:title_core fallback produced 62 duplicate ids on
    # the 2,616-row corpus (rows without reqIds share title cores).
    import hashlib

    def _row_id(r) -> str:
        if r.get("rid"):
            return r["rid"]
        # S40: posted + url joined the basis — (ck,title,loc,source)
        # alone collided on 16 production rows (careerjet >7d
        # repost pairs kept by design + same-day different-url
        # pairs). posted distinguishes the repost windows; url-hash
        # distinguishes url-variants. Rid rows are unaffected.
        basis = "|".join((r.get("company_key") or "",
                          r.get("title") or "",
                          (r.get("loc") or "")[:40],
                          r.get("source") or "",
                          (r.get("posted") or "")[:10],
                          hashlib.sha1((r.get("url") or "")
                                       .encode()).hexdigest()[:8]))
        return (r.get("company_key") or "unknown") + ":" + \
            hashlib.sha1(basis.encode()).hexdigest()[:12]

    ui_rows = [{
        "id": _row_id(r),
        "title": r["title"], "company": r["company"],
        "companyKey": r["company_key"], "loc": r.get("loc", ""),
        "geoTier": r.get("geoTier", ""),
        "url": r.get("url", ""), "postedOn": r.get("posted", ""),
        # [S50-A] .get hardening — same doctrine as the histogram: a
        # missing stamp ships as null (the UI renders uncategorized),
        # never a tail crash. Green runs are byte-identical.
        "family": r.get("family"), "familyEvidence": r.get("family_evidence"),
        "seniority": r.get("seniority"), "usStatus": r.get("us_status"),
        "sectorEvidence": r.get("sector_evidence", ""),
        "sectorReason": r.get("sector_reason", ""),
        "staffingEvidence": r.get("staffing_evidence", ""),
        "employerAnonymized": bool(r.get("employer_anonymized")),
        "source": r.get("source"), "layer": r.get("layer"),
        "descLen": len(r.get("desc", "")),
        "desc": (r.get("desc", "") or "")[:4000],
        # S41: audit tag — which transport supplied the graded desc
        # (jd_cache = the refetch lane / local leg; source = the API
        # snippet; none = graded on title/company only)
        "descriptionSource": r.get("description_source", "none"),
    } for r in merged]
    # [S48-B] the smbSignal join rides the bundle rows as a plain
    # string field — gen/dmm ONLY: pm rows carry NO smb_signal key
    # (the identity doctrine; the emitted pm bundle stays
    # bit-identical — pinned).
    if family in _B4_FAMILIES:
        for row, r in zip(ui_rows, merged):
            row["smb_signal"] = r.get("smb_signal", "")
            # [S49-D4] the adzuna salary bands ride the gen/dmm bundle
            # rows (pm's emitted bundle stays byte-identical — the
            # writer-level family gate; audit F18).
            if r.get("salary_min") is not None:
                row["salaryMin"] = r["salary_min"]
            if r.get("salary_max") is not None:
                row["salaryMax"] = r["salary_max"]
    _sen_order = {"senior": 0, "director+": 1, "mid": 2, "junior": 3}
    ui_rows.sort(key=lambda x: (_sen_order.get(x["seniority"], 4),
                                x["postedOn"] or ""))
    _write_atomic(UI_DIR / spec["ui_file"], json.dumps({
        "snapshotDate": today, "rows": len(ui_rows),
        "envelope": {k: v for k, v in env.items()
                     if k not in ("queries",)},
        "jobs": ui_rows,
    }, default=str).encode())

    print(f"[sweep] kept {len(merged)} (dropped {len(dropped)}, "
          f"merges {counters['dedup_merge']}, family_other "
          f"{counters['family_other']}, non_us {counters['usgate_non_us']})")
    print(f"[sweep] families {fam_hist} | seniority {sen_hist}")
    if family in _B4_FAMILIES and smb_hist:
        print(f"[sweep] smb_signal {smb_hist}")
    print(f"[sweep] verdict {verdict}; dead: {dead or 'none'}")
    return 0 if verdict == "complete" else 1


def _csv_bytes(rows: list[dict], family: str = "pm") -> bytes:
    if not rows:
        return b""
    cols = ["title", "company", "company_key", "loc", "geoTier",
            "url", "posted",
            "family", "family_evidence", "seniority", "us_status",
            "us_evidence", "sector_evidence", "sector_reason",
            "staffing_evidence", "employer_anonymized",
            "source", "layer",
            "query", "rid", "description_source", "desc_len", "reason",
            "recoverable"]
    # [S49-D4] the salary columns exist ONLY on the gen/dmm exports —
    # the pm CSV byte-identity is pinned and salary never reaches a
    # pm row's emit anyway (writer-level family gate).
    if family in _B4_FAMILIES:
        cols += ["salary_min", "salary_max"]
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        out = dict(r)
        out["desc_len"] = len(r.get("desc", ""))
        out.pop("desc", None) if "desc" in out else None
        w.writerow(out)
    return buf.getvalue().encode()


def _write_atomic(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="core + tail variants (the monthly rotation)")
    ap.add_argument("--tail-only", action="store_true",
                    help="[S47 §7] with --full --query-window: run ONLY "
                         "the windowed tail (the local completion leg)")
    ap.add_argument("--query-window", default="",
                    help="[S47 F6] N/M — run window N of M over the "
                         "tail queries (needs --full); N='auto' rotates "
                         "from the ISO week calendar ((week-1) % M)+1 — "
                         "the weekly tail-bucket rotation that replaced "
                         "the blind monthly 100-query run. [S47 §7] "
                         "WITHOUT --full it slices the CORE (the local "
                         "9-min-chunk rule: t3 US-wide at 30 queries = "
                         "10+ min fetch alone)")
    ap.add_argument("--family", default="pm",
                    help="role family slug (pm, dmm, gen — the S41 "
                         "registry; gen/dmm are the B4 catch-all lanes)")
    ap.add_argument("--gj-only", action="store_true",
                    help="the firecrawl recovery probe (L3 only)")
    ap.add_argument("--skip", default="",
                    help="comma-separated source names to skip "
                         "(li,gj,muse,usajobs... — local-run routing)")
    ap.add_argument("--seed-envelope", default="",
                    help="S39 §A5: union a prior sweep envelope (repo-relative "
                         "path, e.g. ingest/data/ui/roles-pm.json) — rows "
                         "≥60d fresh re-enter as inputs (the careerjet "
                         "HK-only lane transport doctrine)")
    ap.add_argument("--geo-ladder", default="t3",
                    choices=["t1a", "t1b", "t2", "t3", "t4"],
                    help="[S46-B4 sec3.3] the geo scope: t1a/t1b = the CA "
                         "metro tiers, t2 = California, t3 = US-wide "
                         "(default), t4 = remote -- gen/dmm only")
    args = ap.parse_args()
    if args.geo_ladder != "t3" and args.family == "pm":
        print("role_sweep: --geo-ladder is a catch-all-lane feature "
              "(gen/dmm) -- pm keeps the bit-identity doctrine",
              file=sys.stderr)
        return 2
    spec = family_spec(args.family)
    queries = spec["queries_core"] + (
        spec["queries_tail"] if args.full else [])
    # [S47 F6] the weekly tail-bucket rotation: slice the tail into M
    # windows, run window N (auto = the calendar's). The monthly single
    # 100-query run is DEAD (timeout + adzuna arithmetic + blind
    # scheduling — R1-F6); every weekly run carries core + one bucket.
    # [S47 §7 local-chunk rule] WITHOUT --full the window slices the
    # CORE (t3 US-wide adzuna at 30 queries = 10+ min fetch alone —
    # the box's 9-min foreground rule needs 10-query windows); with
    # --full + --tail-only it slices the tail ALONE (the local tail
    # completion leg — the core already ran).
    if args.query_window:
        n_s, _, m_s = args.query_window.partition("/")
        m = max(2, int(m_s or 4))
        if n_s == "auto":
            n = (datetime.now(timezone.utc).isocalendar().week - 1) \
                % m + 1
        else:
            n = int(n_s)
        if args.full and args.tail_only:
            tail = spec["queries_tail"]
            w = -(-len(tail) // m)
            lo, hi = (n - 1) * w, n * w
            queries = tail[lo:hi]
            print(f"[query-window {n}/{m}] tail-only[{lo}:{hi}] "
                  f"→ {min(hi, len(tail)) - lo} of {len(tail)} tail")
        elif args.full:
            tail = spec["queries_tail"]
            w = -(-len(tail) // m)              # ceil(len/m)
            lo, hi = (n - 1) * w, n * w
            queries = spec["queries_core"] + tail[lo:hi]
            print(f"[query-window {n}/{m}] tail[{lo}:{hi}] "
                  f"→ {min(hi, len(tail)) - lo} of {len(tail)} tail queries "
                  f"(+{len(spec['queries_core'])} core)")
        else:
            core = spec["queries_core"]
            w = -(-len(core) // m)
            lo, hi = (n - 1) * w, n * w
            queries = core[lo:hi]
            print(f"[query-window {n}/{m}] core[{lo}:{hi}] "
                  f"→ {min(hi, len(core)) - lo} of {len(core)} core queries")
    if args.gj_only:
        # [S47 F4b/A7] the probe family-aware: under --family dmm/gen
        # the probe must exercise THAT family's core (a gen probe on
        # PM core validates nothing about the catch-all lane)
        rows, v = fetch_gj_role(spec["queries_core"][:2])
        print(f"[gj-probe:{args.family}] {len(rows)} rows | {v}")
        return 0
    return sweep(queries, skip=args.skip, family=args.family,
                 seed_envelope=args.seed_envelope,
                 geo_ladder=args.geo_ladder)


_ZP_CARD = re.compile(
    r"\[(?P<company>[^\]!][^\]]{1,90})\]"
    r"\(https://www\.ziprecruiter\.com/co/(?P<coslug>[^)\s]+)"
    r"[^)]*\)\s*\n+(?:\s*\n)*"
    r"##\s+(?P<title>[^\n]{3,140})\s*\n+"
    r"(?:\s*\n)*"
    r"\[(?P<loc>[^\]]{2,80})\]\("
    r"https://www\.ziprecruiter\.com/jobs-search[^)]*\)"
    r"(?P<rest>(?:(?!https://www\.ziprecruiter\.com/co/).)*?)(?="
    r"\[https?://|\[|\Z|https://www\.ziprecruiter\.com/co/)",
    re.DOTALL)


def _zp_posted(text: str) -> str:
    """Best-effort freshness from a ziprecruiter card block: 'Posted
    today' → today; 'Nd ago'/'N days ago' → the relative date."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if re.search(r"Posted today", text):
        return today
    m = re.search(r"(?P<n>\d+)\s*(d|day|days)\s*ago", text)
    if m:
        try:
            d = (datetime.now(timezone.utc)
                 - timedelta(days=int(m.group("n")))).strftime("%Y-%m-%d")
            return d
        except Exception:                        # noqa: BLE001
            pass
    if re.search(r"\bNew\b", text):
        # ziprecruiter's 'New' badge = <~3 days old; stamp 2d as the
        # honest midpoint (never inflate to today on the badge alone)
        return ((datetime.now(timezone.utc) - timedelta(days=2))
                .strftime("%Y-%m-%d"))
    return ""


def fetch_ziprecruiter(queries: list[str],
                       location: str | None = None) -> tuple[list[dict], str]:
    """S42 industry v2 (DESIGN-S42 v2 B6) → **S47 firecrawl transport**:
    ZipRecruiter was 403-jobspy/402-ZenRows for a year of probes; the
    S47 fleet probe (2026-10-24) measured the firecrawl scrape OPEN —
    '591 hvac office coordinator jobs in United States', cards carry
    company/title/loc/salary/freshness (SERP cards expose NO per-job
    hrefs — client-side handlers only, verified at waitFor=9s; url =
    the company co/ link, the stable per-company board page).

    Ladder: (1) firecrawl markdown scrape, 1 credit/query, fleet-rotated
    via _fc_scrape; (2) jobspy attempt-and-record fallback (the S42
    verdict semantics preserved: blocked:no-yield(403) when walled).
    Politeness: 1 page/query, 1.5s pacing, fail-soft per query."""
    rows: list[dict] = []
    errors = 0
    loc = location or "United States"
    if _firecrawl_keys():
        base = ("https://www.ziprecruiter.com/jobs-search?search="
                + urllib.parse.quote_plus(queries[0] if queries else "")
                + "&location=" + urllib.parse.quote_plus(loc))
        d, fc_status = _fc_scrape({"url": base, "formats": ["markdown"],
                                    "waitFor": 4000})
        if d is not None:
            md = str((d.get("data") or {}).get("markdown") or "")
            if len(md) >= 1000:
                for qi, q in enumerate(queries):
                    url = ("https://www.ziprecruiter.com/jobs-search?search="
                           + urllib.parse.quote_plus(q)
                           + "&location=" + urllib.parse.quote_plus(loc))
                    if qi == 0:
                        d2, st2 = d, "ok"          # the probe IS q[0]
                    else:
                        d2, st2 = _fc_scrape(
                            {"url": url, "formats": ["markdown"],
                             "waitFor": 4000})
                    if d2 is None:
                        errors += 1
                        continue
                    md2 = str((d2.get("data") or {}).get("markdown") or "")
                    for m in _ZP_CARD.finditer(md2):
                        g = m.groupdict()
                        coslug = (g["coslug"] or "").split("/Jobs")[0]
                        co_url = ("https://www.ziprecruiter.com/co/"
                                  + coslug)
                        rows.append({
                            "title": g["title"].strip(),
                            "company": g["company"].strip(),
                            "loc": re.sub(r"\s*·.*", "", g["loc"]).strip(),
                            "url": co_url,
                            "posted": _zp_posted(g["rest"] or ""),
                            "desc": (g["title"] + "\n" + g["loc"]
                                     + "\n" + (g["rest"] or ""))[:600],
                            "source": "ziprecruiter", "layer": "l1.5",
                            "query": q,
                            "rid": (f"ziprecruiter:{coslug}:"
                                    + re.sub(r"[^a-z0-9]", "",
                                             g["title"].lower())[:60]),
                        })
                    time.sleep(1.5)
                if rows:
                    return rows, "ok:firecrawl"
                if errors == 0 and md:
                    return [], "blocked:firecrawl-empty"  # shell/no cards
            else:
                errors += 1                        # shell page — fall through
        else:
            errors += 1                            # fleet failure — fall
    # (2) jobspy fallback — attempt-and-record (the S42 doctrine)
    try:
        from jobspy import scrape_jobs  # noqa: PLC0415
    except ImportError:
        if rows:
            return rows, "ok:firecrawl"
        return [], "not-installed" if errors else "no-key"
    for q in queries:
        try:
            jobs = scrape_jobs(site_name=["zip_recruiter"],
                               search_term=q,
                               location=loc,
                               results_wanted=100, hours_old=24 * 30)
            if not hasattr(jobs, "iterrows"):
                continue
            for _, j in jobs.iterrows():
                jl = j.get("location")
                rows.append({
                    "title": str(j.get("title") or ""),
                    "company": str(j.get("company") or ""),
                    "loc": "" if not isinstance(jl, str) else jl,
                    "url": str(j.get("job_url") or ""),
                    "posted": str(j.get("date_posted") or "")[:10],
                    "desc": str(j.get("description") or "")[:12000],
                    "source": "ziprecruiter", "layer": "l1.5",
                    "query": q, "rid": "",
                })
        except Exception as exc:                # one bad query ≠ a dead
            errors += 1                          # source; keep fetching
            if errors >= 3:
                return rows, f"error:{q[:12]}:{type(exc).__name__}"
        time.sleep(2.0)
    if rows:
        return rows, "ok"
    # jobspy swallows the 403 and returns an EMPTY frame — a zero-yield
    # on ziprecruiter is the measured block (403 'use a proxy'), never
    # "no jobs exist". The verdict must say so (F12: honest channels).
    return [], "blocked:no-yield(403)" if errors == 0 else \
        f"blocked:x{errors}"






# ── craigslist T0 (S47 §3, DESIGN-S47; live-probed 2026-10-26) ──────────
CRAIGSLIST_CITIES = (
    "sfbay", "losangeles", "newyork", "chicago", "houston", "phoenix",
    "philadelphia", "sanantonio", "sandiego", "dallas", "austin",
    "atlanta", "seattle", "denver", "boston", "washingtondc", "miami",
    "orlando", "tampa", "minneapolis", "sacramento", "portland",
    "raleigh", "charlotte", "nashville", "lasvegas", "detroit",
    "columbus", "kansascity", "stlouis")

# the T0 census queries: the F9 verticals that yield <=2 head rows on
# the aggregators (pool/appliance/junk/moving live HERE) + the trade
# heads + the SMB office side. 12 queries x 30 metros, 1 page each.
CRAIGSLIST_QUERIES = (
    "hvac", "plumbing", "roofing", "electrician", "landscaping",
    "appliance repair", "pool cleaning", "handyman", "junk removal",
    "moving", "office manager", "dispatcher")

_CL_ROW = re.compile(
    r'<li class="cl-static-search-result"[^>]*title="(?P<rowtitle>[^"]*)"'
    r'.*?<a href="(?P<url>https://www\.craigslist\.org/view/[^"]+)">'
    r'.*?<div class="title">(?P<title>[^<]*)</div>'
    r'.*?<div class="location">\s*(?P<loc>[^<]*?)\s*</div>',
    re.DOTALL)

# [W2B-3] phone redaction at T0 ingest: poster-published personal
# numbers are the PII class riding the wholesale industry mirror
# (build_public_mirror.sh rsyncs ingest/data/industry/ verbatim).
# Digit-runs >=7 in the PHONE shapes: contiguous runs (10-digit and
# partial) + the separated US groupings 3-3-4 / (3)-3-4 / +1-3-3-4
# with [-. ] joins ("Tina 713-493-2288" -> "Tina [redacted]").
# Two-group ranges (2500-3000 miles, 900-1000 pay, 2021-2026 trucks)
# are NOT phone-shaped and stay — they are title-token signal, not
# PII (the W2B-2/W2B-8 relevance analysis depends on them).
_CL_PHONE = re.compile(
    r"(?<!\d)(?:"
    r"\d{7,}"
    r"|(?:\+?1[\s.\-])?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}"
    r")(?!\d)")

_CL_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/130.0.0.0 Safari/537.36"}

# [W2b-10] the anchored block-page markers: <title> content (the block
# page's status line) + the second-marker pair for the body case
_CL_TITLE_PAT = re.compile(r"<title[^>]*>(.*?)</title>",
                           re.DOTALL | re.IGNORECASE)
_CL_BLOCK2 = re.compile(
    r"\b(?:blocked|access\s+denied|temporarily|maintenance|"
    r"too\s+many\s+requests|try\s+again)\b")


def fetch_craigslist(city: str, queries: list[str]) -> tuple[list[dict], str]:
    """S47 T0 (DESIGN-S47 §3): craigslist is the smallest-employer tier
    (sole props, 1-5 staff, word-of-mouth) — the F9 verticals with ZERO
    aggregator yield live here. Canonical URL www.craigslist.org/search/
    area/{city}?cat=jjj (the city-subdomain form 301s — live-verified
    2026-10-26; 49 server-rendered rows on the sfbay/hvac probe). Rows
    are DATELESS (no datetime attr in the static HTML — R1/F15: no
    per-post detail fetches to recover dates, the derived-data doctrine:
    title/loc/url/counts, never republished bodies). Company: the
    title text IS the signal (craigslist titles carry the employer
    descriptor when they carry anything). Pacing 3s/fetch (B1's
    measured safe rate); captcha on a city = that city pauses (no
    bypass — ever). Box-side only (IP reputation; NEVER a GHA lane)."""
    # [W2B-8] aliased: the loop body's `html` is the fetched PAGE —
    # a bare `import html` would be shadowed by it
    import html as _html_mod                     # noqa: PLC0415
    rows: list[dict] = []
    verdicts: list[str] = []
    for q in queries:
        url = ("https://www.craigslist.org/search/area/"
               + urllib.parse.quote(city)
               + "?cat=jjj&query=" + urllib.parse.quote_plus(q))
        try:
            req = urllib.request.Request(url, headers=_CL_UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            verdicts.append(f"{q}:http{exc.code}")
            time.sleep(3.0)
            continue
        except Exception as exc:                    # noqa: BLE001
            verdicts.append(f"{q}:error:{type(exc).__name__}")
            time.sleep(3.0)
            continue
        # [W2b-10] ANCHORED captcha heuristic: the old bare substring
        # check ("unavailable" anywhere in the first 2000 chars)
        # false-positived on legit pages whose early JS/ARIA text
        # carries the word (the city would pause needlessly). A block
        # is now declared only on a STRONG marker: 'recaptcha'
        # anywhere, 'unavailable' inside <title> (the block page's
        # status line), or 'unavailable' early PAIRED with a second
        # block-page marker. The honest-blocked doctrine holds either
        # way: a blocked city pauses — run_window's W2B-1 gate keeps
        # its prior rows (pinned: test_blocked_rerun_preserves_prior_rows).
        low = html.lower()
        _mt = _CL_TITLE_PAT.search(low)
        if ("recaptcha" in low
                or bool(_mt and "unavailable" in _mt.group(1))
                or ("unavailable" in low[:2000]
                    and _CL_BLOCK2.search(low))):
            verdicts.append(f"{q}:blocked:captcha")
            return rows, "blocked:captcha"          # the city pauses
        n = 0
        # [W2b-9] per-row fetch stamp: the PAGE fetch time — one stamp
        # per (city,query) page. Dateless rows otherwise carry no
        # generation timestamp; the city ledger (cities_run[city]
        # ["ran_at"]) stays the window-level authority.
        fetched_at = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        for m in _CL_ROW.finditer(html):
            g = m.groupdict()
            title = (g["title"] or g["rowtitle"] or "").strip()
            if not title:
                continue
            # [W2B-8] entity decode at ingest — 945 titles (9.5%) in
            # the 10-26 census carried raw &amp;/&#39;/&nbsp; entities,
            # degrading the title-token signal (the only T0 signal).
            # Unescape BEFORE redaction (a &nbsp;-joined number must
            # still match the phone shape; \s covers \xa0).
            title = _html_mod.unescape(title)
            # [W2B-3] phone redaction at ingest (see _CL_PHONE)
            title = _CL_PHONE.sub("[redacted]", title)
            rid_tail = (g["url"].rstrip("/").rsplit("/", 1)[-1]
                        if g["url"] else "")
            rows.append({
                "title": title,
                "company": "",          # title-text IS the T0 signal
                "loc": (f"{g['loc'].strip()} ({city})"
                        if g["loc"].strip() else city),
                "url": g["url"],
                "posted": "",            # dateless (F15)
                "dateless": True,
                "desc": "",
                "source": "craigslist", "layer": "l2",
                "query": f"cl:{city}:{q}",
                "rid": f"cl:{rid_tail}",
                "fetched_at": fetched_at,
                # [W2b-9] geo tag, doctrine-minimal: the deterministic
                # metro slug already rides BOTH loc ("({city})" suffix)
                # and query ("cl:{city}:") — the census aggregates by
                # those. The geo field carries the geo-SOURCE class;
                # NO 30-metro city->state map (poster-authored loc
                # text is not state-parseable — a region tagger over it
                # would be noise, and geo_levels.region_tag/
                # us_region_tag gate Bay Area/US membership, not
                # metro identity).
                "geo": "craigslist",
            })
            n += 1
        verdicts.append(f"{q}:{n}")
        time.sleep(3.0)
    # honest verdict: a clean page with 0 hits is ok:0 (the query
    # genuinely has no posts); "blocked" only when every query errored
    n_err = sum(1 for x in verdicts if ":http" in x or ":error:" in x)
    v = ("ok" if rows or not n_err else "blocked") if verdicts \
        else "no-yield"
    return rows, v + ("|" + ",".join(verdicts) if verdicts else "")


_GD_CARD = re.compile(
    r"\[(?P<title>[^\]]{3,140})\]"
    r"\(https://www\.glassdoor\.com/job-listing/(?P<slug>[^)\s]+)"
    r"\?jl=(?P<jid>\d+)\)")


def _gd_company(md: str, start: int, slug: str, title: str) -> str:
    """Company for a glassdoor card: the nearest preceding `- Company`
    list item (within ~400 chars), else decoded from the URL slug
    (`title-slug-company-slug-JV_IC...` — logo cards carry no text)."""
    look = md[max(0, start - 400):start]
    for line in reversed(look.splitlines()):
        line = line.strip()
        m = re.match(r"^-(?P<co>(?!\[)[^\\n]{2,90})$", line)
        if m:
            return m.group("co").strip()
    # slug fallback: strip the title slug + the JV/IC tail
    tslug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    co = slug
    if tslug and tslug in co:
        co = co.split(tslug, 1)[-1]
    co = re.sub(r"-jv.*|-[ij]c_?\d.*", "", co, flags=re.I)
    co = re.sub(r"-ko\d.*|_ko\d.*", "", co, flags=re.I)
    return co.strip("- ").replace("-", " ").strip()[:90]


def _gd_loc_salary(md: str, start: int) -> tuple[str, str]:
    """The first short loc line + the first $-line after the card title."""
    rest = md[start:start + 600]
    loc = ""
    sal = ""
    for line in rest.splitlines():
        line = line.strip()
        if not line:
            continue
        if not loc and 2 <= len(line) <= 80 and "$" not in line \
                and "Easy Apply" not in line and not line.startswith(
                    ("!", "[", "-")):
            loc = line
        if not sal and "$" in line:
            sal = line.replace("(Employer provided)", "").strip()
        if loc and sal:
            break
    return loc, sal


def fetch_glassdoor_zenrows(queries: list[str],
                            location: str | None = None) -> tuple[
        list[dict], str]:
    """S42 industry v2 → **S47 firecrawl transport**: Glassdoor via the
    job_sourcer ZenRows adapter was 402 (credits exhausted) since
    2026-10-22; the S47 fleet probe (2026-10-24) measured the firecrawl
    scrape OPEN — '4,557 Construction coordinator jobs in United
    States', cards carry title/job-listing-URL(jl=ID)/company/loc/
    salary; NO dates in the cards (dateless rows — the merge ladder's
    dateless-donor + the seed's 2-generation bound handle them).

    Ladder: (1) firecrawl markdown scrape, 1 credit/query, fleet-rotated
    via _fc_scrape; (2) ZenRows attempt-and-record fallback. Politeness:
    1 page/query, 2s pacing, fail-soft per query. The name keeps the
    S42 roster contract (industry_sweep + pins reference it)."""
    rows: list[dict] = []
    errors = 0
    if _firecrawl_keys():
        for q in queries:
            url = ("https://www.glassdoor.com/Job/jobs.htm?sc.keyword="
                   + urllib.parse.quote_plus(q))
            d, st = _fc_scrape({"url": url, "formats": ["markdown"],
                                "waitFor": 4000})
            if d is None:
                errors += 1
                if errors >= 2 and not rows:
                    break                # transport dead — ZenRows leg
                continue
            md = str((d.get("data") or {}).get("markdown") or "")
            for m in _GD_CARD.finditer(md):
                g = m.groupdict()
                co = _gd_company(md, m.start(), g["slug"], g["title"])
                loc, sal = _gd_loc_salary(md, m.end())
                rows.append({
                    "title": g["title"].strip(),
                    "company": co,
                    "loc": loc,
                    "url": ("https://www.glassdoor.com/job-listing/"
                            + g["slug"] + "?jl=" + g["jid"]),
                    "posted": "",          # glassdoor cards carry no dates
                    "desc": (g["title"] + "\n" + co + "\n"
                             + loc + "\n" + sal)[:600],
                    "source": "glassdoor", "layer": "l1.5",
                    "query": q,
                    "rid": f"glassdoor:jl{g['jid']}",
                })
            time.sleep(2.0)
        if rows:
            return rows, "ok:firecrawl"
        if errors and errors < len(queries):
            pass                            # mixed — fall to zenrows leg
    # (2) ZenRows fallback — attempt-and-record (the S42 doctrine)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import job_sourcer as _js  # noqa: PLC0415
    except Exception as exc:               # noqa: BLE001
        return rows or [], f"error:{type(exc).__name__}"
    key = _env("ZENROWS_API_KEY")
    if not key:
        return rows or [], ("blocked:fc-only" if errors
                            else "no-key")
    for q in queries:
        try:
            got = _js.scrape_glassdoor_zenrows(
                q, location or "United States", key)
        except Exception as exc:            # noqa: BLE001
            return rows or [], f"error:{type(exc).__name__}"
        if not got:                        # 402/transport-dead: stop
            return rows or [], "blocked:zenrows" if not rows else "ok"
        for g in got:
            g.setdefault("source", "glassdoor")
            g.setdefault("layer", "l1.5")
            g.setdefault("company", "")   # JSON-LD rows carry no company
            g.setdefault("loc", g.pop("location", "") or "")
            g.setdefault("url", "")
            g.setdefault("posted", "")
            g.setdefault("desc", "")
            g.setdefault("rid", g.get("id", ""))
            g["query"] = q
            rows.append(g)
        time.sleep(2.0)
    return rows or [], "ok"


if __name__ == "__main__":
    sys.exit(main())
