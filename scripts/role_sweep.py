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
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
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
)

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

# ── env ─────────────────────────────────────────────────────────────────────


def _env(name: str) -> str:
    v = os.environ.get(name, "")
    if not v and (REPO / "ingest/.env").exists():
        for line in (REPO / "ingest/.env").read_text().splitlines():
            if line.startswith(f"{name}="):
                v = line.split("=", 1)[1].strip().strip('"')
    return v


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


def fetch_adzuna(queries: list[str]) -> tuple[list[dict], str]:
    app_id, key = _env("ADZUNA_APP_ID"), _env("ADZUNA_API_KEY")
    if not (app_id and key):
        return [], "no-key"
    rows: list[dict] = []
    # S38 §2a: relevance sort (measured 100% PM-family density p1-50)
    # + max_days_old=45 (the li/indeed freshness doctrine) + 10 pages
    # (weekly cap; density verified far beyond — the cap is politeness).
    max_page = 10
    for q in queries:
        for page in range(1, max_page + 1):
            url = (f"https://api.adzuna.com/v1/api/jobs/us/search/{page}"
                   f"?app_id={app_id}&app_key={key}"
                   f"&what={urllib.parse.quote_plus(q)}"
                   f"&results_per_page=50&max_days_old=45"
                   f"&content-type=application/json")
            try:
                d = _http_json(url)
            except Exception as exc:
                if page == 1:
                    return rows, f"error:page{page}:{q[:12]}:{type(exc).__name__}"
                break                      # partial page-run: keep what we have
            for j in d.get("results", []):
                created = (j.get("created") or "")[:10]
                rows.append({
                    "title": j.get("title") or "",
                    "company": (j.get("company") or {}).get("display_name") or "",
                    "loc": ((j.get("location") or {}).get("display_name") or ""),
                    "url": j.get("redirect_url") or "",
                    "posted": created,
                    "desc": (j.get("description") or "")[:12000],
                    "source": "adzuna", "layer": "l1.5", "query": q,
                    "rid": f"adz:{j.get('id') or ''}",
                })
            if len(d.get("results", [])) < 50:
                break
            time.sleep(1.0)
    return rows, "ok"


def fetch_jooble(queries: list[str]) -> tuple[list[dict], str]:
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
                                     "United States", "page": page}).encode(),
                    headers={"Content-Type": "application/json"})
            except Exception as exc:
                if page == 1:
                    return rows, f"error:page{page}:{q[:12]}:{type(exc).__name__}"
                break
            for j in d.get("jobs", []):
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


def fetch_jobspy_indeed(queries: list[str]) -> tuple[list[dict], str]:
    try:
        from jobspy import scrape_jobs  # noqa: PLC0415
    except ImportError:
        return [], "not-installed"
    rows: list[dict] = []
    for q in queries:                    # S38 §2a: all queries (was [:5])
        try:
            jobs = scrape_jobs(site_name=["indeed"], search_term=q,
                               location="United States",
                               results_wanted=100, hours_old=24 * 30)
            if not hasattr(jobs, "iterrows"):
                continue
            for _, j in jobs.iterrows():
                loc = j.get("location")
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


def fetch_wwr() -> tuple[list[dict], str]:
    try:
        req = urllib.request.Request(
            "https://weworkremotely.com/categories/remote-product-jobs.rss",
            headers=UA)
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
        rows.append({
            "title": role, "company": company,
            "loc": "Remote", "url": _tag("link"),
            "posted": (re.search(r"<pubDate>(.*?)</pubDate>", item)
                       and _parse_rss_date(_tag("pubDate"))) or "",
            "desc": _tag("description")[:12000],
            "source": "wwr", "layer": "l2", "query": "wwr:product", "rid": "",
        })
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
    key = _env("FIRECRAWL_API_KEY")
    if not key:
        return [], "no-key"
    try:
        d = _http_json("https://api.firecrawl.dev/v2/team/credit-usage",
                       headers={"Authorization": f"Bearer {key}"})
        remaining = (d.get("data") or {}).get("remainingCredits", 0)
        if remaining < CREDIT_PREFLIGHT_MIN:
            return [], (f"preflight-fail:credits={remaining}")
    except Exception:
        pass                       # preflight is best-effort; scrape decides
    rows: list[dict] = []
    status = "ok"
    for q in queries:
        url = ("https://www.google.com/search?q="
               + urllib.parse.quote_plus(q) + "&ibp=htl;jobs&hl=en&gl=us")
        md = ""
        for attempt in range(3):
            try:
                d = _http_json(
                    "https://api.firecrawl.dev/v2/scrape",
                    data=json.dumps({"url": url, "formats": ["markdown"],
                                     "onlyMainContent": False,
                                     "waitFor": 5000}).encode(),
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {key}"}, timeout=120)
            except Exception as exc:
                status = f"error:{q[:12]}:{type(exc).__name__}"
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


def fetch_careerjet(queries: list[str]) -> tuple[list[dict], str]:
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
            params = {"keywords": f'"{q}"', "location": "United States",
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


def fetch_findwork(queries: list[str]) -> tuple[list[dict], str]:
    """S38 L1.5: findwork.dev — search= full-text (count 1,667), title
    field = `role`, JD = `text` (FULL — the highest merge-value tier);
    location param is exact-match-only → usgate runs client-side."""
    key = _env("FINDWORK_API_KEY")
    if not key:
        return [], "no-key"
    rows: list[dict] = []
    max_page = 17                       # count=1,667 / 100 per page
    seen = 0
    for page in range(1, max_page + 1):
        try:
            req = urllib.request.Request(
                "https://findwork.dev/api/jobs/?search="
                + urllib.parse.quote_plus(queries[0] if queries
                                          else "product manager")
                + f"&page={page}",
                headers={"Authorization": f"Token {key}",
                         "User-Agent": "jobsearch-sweep"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode())
        except Exception as exc:
            if page == 1:
                return rows, f"error:{type(exc).__name__}"
            break
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
                "query": "findwork:search", "rid": f"fw:{j.get('id') or ''}",
            })
        seen += len(res)
        if seen >= (d.get("count") or 0):
            break
        time.sleep(1.2)
    return rows, "ok"


def fetch_muse() -> tuple[list[dict], str]:
    """S38 L2 (GHA-only): The Muse public API — category=Product
    Management, keyless, full JD. 403 from HK (CloudFront geo) —
    fail-soft locally, live from the Azure lane. First successful page
    is captured to audit/s38_rolecampaign/ as the field-map evidence
    (F8 — the row contract was endpoint-verified, not row-verified)."""
    rows: list[dict] = []
    for page in range(1, 25):
        try:
            d = _http_json(
                "https://www.themuse.com/api/public/jobs?category="
                "Product%20Management&location=United%20States"
                f"&page={page}", timeout=30)
        except Exception as exc:
            return rows, f"error:{type(exc).__name__}"
        if page == 1 and rows == [] and d:
            _evidence_dump("muse_page1.json", d)
        jobs = d.get("results", [])
        for j in jobs:
            comp = ((j.get("company") or {}) or {})
            rows.append({
                "title": j.get("name") or "",
                "company": comp.get("name") or "",
                "loc": ", ".join(
                    l.get("name", "") for l in (j.get("locations") or [])
                    if isinstance(l, dict))[:80],
                "url": ((j.get("refs") or {}).get("landing_page")
                        or (comp.get("refs") or {}).get("landing_page") or ""),
                "posted": _rfc2822_to_iso(
                    (j.get("publication_datetime") or ""))[:10]
                if j.get("publication_datetime") else "",
                "desc": (j.get("contents") or "")[:12000],
                "source": "muse", "layer": "l2",
                "query": "muse:product-management",
                "rid": f"muse:{j.get('id') or ''}",
            })
        if not jobs:
            break
        time.sleep(1.2)
    return rows, "ok"


def _fc_relay_json(url: str, headers: dict) -> tuple[object, str]:
    """S39 §A2 (probe-relay.md): relay a JSON API GET through firecrawl's
    US egress. data.usajobs.gov works through firecrawl (api.usajobs.gov
    returns IIS 'Invalid Hostname' 400 via relay); the JSON arrives in
    data.markdown markdown-escaped — unescape then parse. Returns
    (parsed | None, status)."""
    key = _env("FIRECRAWL_API_KEY")
    if not key:
        return None, "no-fc-key"
    try:
        d = _http_json(
            "https://api.firecrawl.dev/v2/scrape",
            data=json.dumps({"url": url, "formats": ["markdown"],
                             "headers": headers}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"}, timeout=120)
    except Exception as exc:
        return None, f"relay-error:{type(exc).__name__}"
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


def fetch_yc() -> tuple[list[dict], str]:
    """S38 L2 'yc' — THE startup layer (the directive's scrape-the-source
    branch): Work at a Startup Inertia JSON (/jobs/l/product-manager) +
    ycombinator.com/jobs SSR (/role/product-manager + /remote) + the 6
    US city pages (F18). Rows carry companySlug/companyBatch (rich);
    posted only from yc.com rows (WaaS list rows carry no date — the
    dateless-donor merge rule). YC-backed = board-grade sector evidence
    (yc_startup), applied post-merge (F2)."""
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
        return out

    props = _get_json("https://www.workatastartup.com/jobs/l/product-manager")
    if props is None:
        verdicts.append("waas:dead")
    else:
        rows += _waas_rows(props, "waas:product-manager")

    # ycombinator.com/jobs — Next.js SSR with embedded job JSON
    for slug, tag in [("product-manager", "yc:role"),
                      ("product-manager/remote", "yc:remote")]:
        try:
            r = cr.get(f"https://www.ycombinator.com/jobs/role/{slug}",
                       impersonate="chrome131", timeout=30, headers=headers)
            if r.status_code == 200:
                found = _yc_com_extract(r.text)
                for j in found:
                    rows.append({
                        "title": j.get("title") or j.get("jobTitle") or "",
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
                        "source": "yc", "layer": "l2", "query": tag,
                        "rid": f"yc:{j.get('id') or ''}",
                        "yc_slug": (j.get("companySlug") or
                                    (j.get("company") or {}).get("slug", "")),
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


def fetch_talent() -> tuple[list[dict], str]:
    """S38 L2: talent.com SERP — server-rendered JobCard articles
    (closure-tier, 20 rows keyless, no pagination — the census verdict).
    CSS-module class prefixes (JobCard_title/…) with hashed suffixes;
    the JSON-LD ItemList is URL-only (no job fields)."""
    try:
        from curl_cffi import requests as cr  # noqa: PLC0415
    except ImportError:
        return [], "not-installed"
    try:
        r = cr.get("https://www.talent.com/jobs?k=product+manager"
                   "&l=United+States", impersonate="chrome131", timeout=30)
        if r.status_code != 200:
            return [], f"error:status{r.status_code}"
    except Exception as exc:
        return [], f"error:{type(exc).__name__}"
    rows: list[dict] = []
    for m in re.finditer(
            r'<article class="JobCard_card[^"]*"[^>]*>(.*?)</article>',
            r.text, re.DOTALL):
        card = m.group(1)

        def _cls(prefix: str) -> str:
            mm = re.search(
                rf'class="{prefix}[^"]*"[^>]*>(.*?)</',
                card, re.DOTALL)
            return re.sub(r"<[^>]+>", "", mm.group(1)).strip() if mm else ""

        title = _cls("JobCard_title")
        company = _cls("JobCard_company")
        loc = _cls("JobCard_location")
        snippet = _cls("JobCard_snippet")
        dm = re.search(r'datetime="([^"]+)"', card, re.IGNORECASE)
        href = re.search(r'href="/view\?id=(\d+)"', card)
        if not title:
            continue
        rows.append({
            "title": title, "company": company,
            "loc": loc[:80],
            "url": (f"https://www.talent.com/view?id={href.group(1)}"
                    if href else ""),
            "posted": (dm.group(1) or "")[:10] if dm else "",
            "desc": snippet[:12000],
            "source": "talent", "layer": "l2",
            "query": "talent:pm", "rid": "",
        })
    return rows, "ok"


def fetch_hn() -> tuple[list[dict], str]:
    """S38 L2: HN Who's Hiring via the in-repo adapter (the deferred
    v1.5) — the adapter returns list[Job] dataclasses (title/company/
    description/link/location/date_posted); tech-community by norm →
    board-grade evidence hn_whihiring, applied post-merge (F2)."""
    try:
        from jobsearch.sources import hn_whos_hiring as hn  # noqa: PLC0415
    except ImportError:
        return [], "not-installed"
    rows: list[dict] = []
    verdicts: list[str] = []
    for kw in ("Product Manager", "PM"):
        try:
            got = hn.fetch(kw, num_results=400)
        except Exception:
            verdicts.append(f"{kw[:2]}:{type(hn) and 'exc'}")
            continue
        for j in (got or []):
            if not getattr(j, "title", ""):
                continue
            rows.append({
                "title": j.title or "",
                "company": j.company or "",
                "loc": (j.location or "")[:80],
                "url": j.link or "",
                "posted": (j.date_posted or "")[:10],
                "desc": (j.description or "")[:12000],
                "source": "hn", "layer": "l2",
                "query": f"hn:{kw.lower()}",
                "rid": f"hn:{j.id or j.link or j.title[:40]}",
            })
    verdict = ("ok" if rows or not verdicts
               else "partial:" + ",".join(verdicts))
    return rows, verdict


def fetch_builtin(queries: list[str]) -> tuple[list[dict], str]:
    """S39 §A3 (probe-relay.md): BuiltIn via firecrawl relay — CF-walled
    from HK direct (403 'Just a moment'); firecrawl passes CF. ONLY the
    `?search=` SSR form filters server-side (path URLs SSR a generic
    feed); 25 cards/page. Card markdown: '## [Title](url)' +
    '[Company](company-url)' + posted/location lines. 3 queries × 4
    pages ≈ 12 firecrawl credits (F13 fleet budget)."""
    key = _env("FIRECRAWL_API_KEY")
    if not key:
        return [], "no-key"
    rows: list[dict] = []
    status = "ok"
    for q in queries[:3]:
        for page in range(1, 5):
            url = ("https://www.builtin.com/jobs?search="
                   + urllib.parse.quote_plus(q) + f"&page={page}")
            try:
                d = _http_json(
                    "https://api.firecrawl.dev/v2/scrape",
                    data=json.dumps({"url": url, "formats": ["markdown"],
                                     "waitFor": 3000}).encode(),
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {key}"}, timeout=120)
            except Exception as exc:
                status = f"error:{q[:12]}:{type(exc).__name__}"
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


def fetch_himalayas() -> tuple[list[dict], str]:
    """S39 §A4 (probe-relay.md): Himalayas open JSON firehose —
    `himalayas.app/jobs/api?limit=20&cursor=<nextCursor>` keyless with
    full-JD rows; no server-side search → client-side filter over the
    recent window (30 pages × 20, politeness sleep 1s; the full-JD
    evidence value inverts the trio's thin-density precedent — F11).
    rid = him:{guid}; loc = locationRestrictions joined."""
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
            # client-side PM-family filter (the board is all-roles)
            tl = title.lower()
            if not any(k in tl for k in (
                    "product manager", "product owner", "product lead",
                    "head of product", "product marketing",
                    "director of product", "principal product",
                    "group product", "technical product")):
                continue
            guid = j.get("guid") or j.get("id") or ""
            if guid in seen:
                continue
            seen.add(guid)
            locs = " / ".join(
                l for l in (j.get("locationRestrictions") or [])
                if isinstance(l, str))[:80]
            pub = j.get("pubDate") or ""
            if isinstance(pub, (int, float)):
                pub = _epoch_to_iso(pub)
            rows.append({
                "title": title,
                "company": j.get("companyName") or "",
                "loc": locs,
                "url": j.get("guid") and
                f"https://himalayas.app/jobs/{guid}" or "",
                "posted": str(pub)[:10],
                "desc": (j.get("description") or "")[:12000],
                "source": "himalayas", "layer": "l2",
                "query": "himalayas:recent", "rid": f"him:{guid}",
            })
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


def _norm_day(posted: str):
    if not posted:
        return None
    try:
        return datetime.strptime(posted[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def sweep(queries: list[str], skip_gj: bool = False,
          skip_li: bool = False, skip: str = "",
          seed_envelope: str = "") -> int:
    skip_set = {s.strip() for s in skip.split(",") if s.strip()}
    skip_set.discard("")
    started = datetime.now(timezone.utc)
    env = {"sources": {}, "layers": {}}
    all_rows: list[dict] = []

    l0, v = project_l0()
    env["sources"]["l0_bundle"] = v
    env["layers"]["l0"] = len(l0)
    all_rows += l0

    # S38 §4a/F9: registration order matters for intra-tier ties —
    # findwork (full JD, cleanest) first in l1.5; the snippet tier
    # follows; muse/usajobs = l2 closure-tier (deliberate, documented).
    fetchers: list[tuple[str, object, list]] = [
        ("findwork", fetch_findwork, [queries]),
        ("adzuna", fetch_adzuna, [queries]),
        ("dice", fetch_dice, [queries]),
        ("careerjet", fetch_careerjet, [queries]),
        ("jooble", fetch_jooble, [queries]),
        ("indeed", fetch_jobspy_indeed, [queries]),
    ]
    if not skip_li and "li" not in skip_set:
        fetchers.append(("li_keyword", fetch_li_keyword, [queries]))
    fetchers += [
        ("yc", fetch_yc, []),
        ("wellfound", fetch_wellfound, []),
        ("hn", fetch_hn, []),
        ("wwr_rss", fetch_wwr, []),
        ("free_trio", fetch_free_trio, []),
        ("talent", fetch_talent, []),
    ]
    if "muse" not in skip_set:
        fetchers.append(("muse", fetch_muse, []))
    if "usajobs" not in skip_set:
        fetchers.append(("usajobs", fetch_usajobs, [queries]))
    if "builtin" not in skip_set:
        fetchers.append(("builtin", fetch_builtin, [queries]))
    if "himalayas" not in skip_set:
        fetchers.append(("himalayas", fetch_himalayas, []))
    if not skip_gj and "gj" not in skip_set:
        fetchers.append(("gj_role", fetch_gj_role, [queries]))
    fetchers = [(n, f, a) for n, f, a in fetchers
                if n not in skip_set]

    for name, fn, args in fetchers:
        try:
            rows, v = fn(*args)
        except Exception as exc:               # fail-soft per source
            rows, v = [], f"crash:{type(exc).__name__}"
        env["sources"][name] = v
        for r in rows:
            r.setdefault("company_key", _company_key(r["company"], {}))
        all_rows += rows
        env["layers"][r["layer"] if rows else "?"] = \
            env["layers"].get(rows[0]["layer"] if rows else "?", 0) + len(rows)
        print(f"[sweep:{name:<12}] {len(rows):>4} rows | {v}")

    # per-source usgate priors (design §4 + S38 §4a/F21 + S39 F11 — the
    # new sources default client-side: dice/careerjet leak 1-2/30
    # non-US rows (measured, caught); yc is US-scoped by board filter;
    # builtin 'N Locations' cards → ambiguous; himalayas
    # locationRestrictions are client-side)
    SRC_US = {"adzuna": "us", "jooble": None, "indeed": "us",
              "li": "us", "wwr": "remote", "remoteok": None,
              "jobicy": None, "arbeitnow": None, "gj": None,
              "board": "skip", "dice": None, "careerjet": None,
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
                "agency_pending": 0, "res7_merge": 0, "cross_src_merge": 0}
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
                if day is None or day < cutoff:
                    continue           # F2: staleness bound (45-60d)
                src = row.get("source") or ""
                rid = row.get("id") or ""
                m = re.match(r"^([a-z]{2,10}):", rid)
                if not (m and m.group(1) in
                        ("adz", "cj", "dice", "fw", "jbl", "ex",
                         "usaj", "muse", "yc", "wf", "hn", "him",
                         "tal", "gj")):
                    rid = ""          # row-hash ids are NOT rids (F1)
                all_rows.append({
                    "title": row.get("title") or "",
                    "company": row.get("company") or "",
                    "loc": row.get("loc") or "",
                    "url": row.get("url") or "",
                    "posted": posted,
                    "desc": (row.get("description") or "")[:4000],
                    "source": src, "layer": _SRC_LAYER.get(src, "l2"),
                    "query": "seed", "rid": rid,
                    "company_key": _company_key(
                        row.get("company") or "", {}),
                    "_seed": True,
                })
                n_seed += 1
            env["sources"]["seed_envelope"] = (
                f"ok:{n_seed}/{len(sd.get('jobs') or [])}")
        except Exception as exc:
            env["sources"]["seed_envelope"] = (
                f"error:{type(exc).__name__}")
        counters["seed_rows_in"] = n_seed
        print(f"[sweep:seed_envelope] {n_seed:>4} rows (staleness-bounded)")

    for r in all_rows:
        title, company = r.get("title", ""), r.get("company", "")
        if r.get("layer") != "l0":
            fam, ev = classify_family(title, query_is_pm=True)
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
        # usgate drop: non-US non-remote (the A-prime rule at sweep time)
        if r.get("us_status") == "non_us":
            counters["usgate_non_us"] += 1
            continue
        # gates (G1 + G1B + screening-post + sector verdict) — apply to
        # pm AND adjacent rows
        if g1_fires(title) or g1_fires(r.get("loc", "")):
            counters["nontech_gate"] += 1
            dropped.append({**r, "reason": "g1_title",
                            "recoverable": False})
            continue
        if g1b_fires(title):                 # S38: title-only, recoverable
            counters["g1b_title"] += 1
            dropped.append({**r, "reason": "g1b_title",
                            "recoverable": True})
            continue
        if is_screening_post(title):         # S39 N7: recoverable bucket
            counters["screening_post"] += 1
            dropped.append({**r, "reason": "screening_post",
                            "recoverable": True})
            continue
        # S38 flags (never drops): staffing repost + anonymized/board
        # employer (N9: Jobgether-class — company field is not the
        # real employer; flagged + merge-excluded, kept)
        r["staffing_evidence"] = ("agency" if is_staffing_company(company)
                                   else "")
        if r["staffing_evidence"]:
            counters["staffing_repost"] += 1
        r["employer_anonymized"] = bool(
            is_anonymized_employer(company) or is_board_employer(company))
        if r["employer_anonymized"]:
            counters["employer_anonymized"] += 1
        act, sev, reason = sector_verdict(
            title, company, r.get("desc", ""),
            r.get("company_key"))
        if act == "bucket":
            counters["nontech_gate"] += 1
            dropped.append({**r, "reason": reason, "recoverable": True})
            continue
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
    for r in kept:
        day = _norm_day(r.get("posted", ""))
        # F14: anonymized employer keys never merge (shared key across
        # unrelated employers — false-merge guard)
        ck = r["company_key"]
        if ck in _ANON_KEYS:
            ck = f"{ck}#{r.get('rid') or id(r)}"      # salted
        exact = (re.sub(r"\W", "", (r["title"] or "").lower()),
                 ck, r.get("layer"), day)
        core = (title_core(r["title"] or ""), ck)
        rid = r.get("rid") or ""
        merge_into = None
        if rid and rid in by_rid:                    # F1: rid-dedup first
            merge_into = id(by_rid[rid])
        if merge_into is None:
            for k, cand in by_key.items():
                if cand.get("_exact") == exact:
                    merge_into = k
                    break
        if merge_into is None:
            # S38-C4: the intra-source re-crawl class (Seismic ×3 on
            # consecutive careerjet crawl days) — same source, same
            # title, same company, ≤7 days apart = the same posting
            # re-crawled, merge (earliest evidence wins)
            for k, cand in by_key.items():
                if cand.get("_title_norm") == re.sub(
                        r"\W", "", (r["title"] or "").lower()) and \
                        cand.get("_ck") == ck and \
                        cand.get("_src") == r.get("source") and \
                        day is not None and cand.get("_day") is not None and \
                        abs((day - cand["_day"]).days) <= 7:
                    merge_into = k
                    day_delta_hist["re7"] = \
                        day_delta_hist.get("re7", 0) + 1
                    counters["res7_merge"] = counters.get("res7_merge", 0) + 1
                    break
        if merge_into is None:
            # S39 F1(d): intra-L1.5 CROSS-SOURCE exact title_norm +
            # same company_key + day Δ≤3 — the cross-engine date-skew
            # class (careerjet/adzuna re-host the same ATS feed with
            # 0-3d posting-date skew; the seed union's main dedup
            # path). title_core is deliberately NOT used (the bjak
            # 12-variant multi-req class stays un-merged).
            if r.get("layer") == "l1.5":
                for k, cand in by_key.items():
                    if cand.get("_layer") == "l1.5" and \
                            cand.get("_src") != r.get("source") and \
                            cand.get("_title_norm") == re.sub(
                                r"\W", "", (r["title"] or "").lower()) and \
                            cand.get("_ck") == ck and \
                            day is not None and \
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
                if bare_norm and bare_norm != re.sub(
                        r"\W", "", (r["title"] or "").lower()):
                    for k, cand in by_key.items():
                        if cand.get("_src") == "adzuna" and \
                                cand.get("_ck") == ck and \
                                cand.get("_title_norm") == bare_norm and \
                                (cand.get("_loc_prefix") or "") == \
                                (r.get("loc") or "")[:12]:
                            merge_into = k
                            counters["res7_merge"] = counters.get(
                                "res7_merge", 0) + 1
                            break
        if merge_into is None:
            for k, cand in by_key.items():
                if cand.get("_core") == core and \
                        cand.get("_layer") != r.get("layer") and \
                        day is not None and cand.get("_day") is not None and \
                        abs((day - cand["_day"]).days) <= 1:
                    merge_into = k
                    d = abs((day - cand["_day"]).days)
                    day_delta_hist[str(d)] = \
                        day_delta_hist.get(str(d), 0) + 1
                    break
        if merge_into is None and day is None:
            # F1 dateless-donor: exact raw title + same company key
            for k, cand in by_key.items():
                if cand.get("_title_norm") == re.sub(
                        r"\W", "", (r["title"] or "").lower()) and \
                        cand.get("_ck") == ck:
                    merge_into = k
                    counters["dedup_dateless"] += 1
                    break
        if merge_into is not None and merge_into not in by_key:
            # S39: stale by_rid pointer (the donor was swapped out of
            # by_key by a later higher-rank survivor) — treat as fresh
            merge_into = None
        if merge_into is None:
            r["_exact"], r["_core"], r["_day"], r["_layer"] = \
                exact, core, day, r.get("layer")
            r["_ck"], r["_title_norm"], r["_src"] = ck, re.sub(
                r"\W", "", (r["title"] or "").lower()), r.get("source")
            r["_loc_prefix"] = (r.get("loc") or "")[:12]
            by_key[id(r)] = r
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
        else:
            survivor, donor = tgt, r
        counters["dedup_merge"] += 1
        if r.get("_seed") or tgt.get("_seed"):
            counters["seed_dup_absorbed"] += 1
        if survivor.get("seniority") != donor.get("seniority"):
            seniority_conflicts += 1
        if title_core(survivor.get("title") or "") != \
                title_core(donor.get("title") or ""):
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
            r["_ck"], r["_title_norm"], r["_src"] = ck, re.sub(
                r"\W", "", (r["title"] or "").lower()), r.get("source")
            r["_loc_prefix"] = (r.get("loc") or "")[:12]
            del by_key[merge_into]
            # S39: purge stale by_rid pointers to the swapped-out donor
            # (a later same-rid row would resolve to a dead key)
            for rid_k, rid_v in list(by_rid.items()):
                if rid_v is tgt:
                    del by_rid[rid_k]
            by_key[id(r)] = r
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
        r.pop("_src", None)

    # outputs (write-atomic)
    ROLES_DIR.mkdir(parents=True, exist_ok=True)
    _write_atomic(ROLES_DIR / "pm_inventory.csv", _csv_bytes(merged))
    _write_atomic(ROLES_DIR / "pm_dropped.csv", _csv_bytes(dropped))

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
    fam_hist: dict[str, int] = {}
    sen_hist: dict[str, int] = {}
    for r in merged:
        fam_hist[r["family"]] = fam_hist.get(r["family"], 0) + 1
        sen_hist[r["seniority"]] = sen_hist.get(r["seniority"], 0) + 1
    env["family_histogram"] = fam_hist
    env["seniority_histogram"] = sen_hist
    _write_atomic(ROLES_DIR / "pm_envelope.json",
                  json.dumps(env, indent=1, default=str).encode())

    # cumulative closure ledger (design §6)
    cov = {}
    cov_path = ROLES_DIR / "pm_coverage.json"
    if cov_path.exists():
        try:
            cov = json.load(open(cov_path))
        except json.JSONDecodeError:
            cov = {}
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
                                "cumulative_pm_rows": 0, "class":
                                "aggregator_only"})
        if e["last_seen"] != today:
            e["weeks_present"] += 1
        e["last_seen"] = today
        e["cumulative_pm_rows"] += n
        if ck in board_keys or ck in roster_keys:
            e["class"] = "onboarded_board"
        elif e.get("class") not in ("onboarded_board", "deferred_queue"):
            e["class"] = "aggregator_only"
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
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "verdict": verdict, "rows": len(merged),
            "sources_dead": dead,
        }) + "\n")
    baselines: dict = {}
    if BASELINE.exists():
        try:
            baselines = json.load(open(BASELINE))
        except json.JSONDecodeError:
            baselines = {}
    series = baselines.get("roles:pm", [])
    if not series or series[-1]["d"] != today:
        series.append({"d": today, "rows": len(merged)})
    baselines["roles:pm"] = series[-120:]
    _write_atomic(BASELINE, json.dumps(baselines, indent=1).encode())

    # UI bundle (roles-pm.json — design §5, rides the ui/ mirror sync).
    # S38-C4: ids are row-hashes (company_key + title + loc + source)
    # — the companyId:title_core fallback produced 62 duplicate ids on
    # the 2,616-row corpus (rows without reqIds share title cores).
    import hashlib

    def _row_id(r) -> str:
        if r.get("rid"):
            return r["rid"]
        basis = "|".join((r.get("company_key") or "",
                          r.get("title") or "",
                          (r.get("loc") or "")[:40],
                          r.get("source") or ""))
        return (r.get("company_key") or "unknown") + ":" + \
            hashlib.sha1(basis.encode()).hexdigest()[:12]

    ui_rows = [{
        "id": _row_id(r),
        "title": r["title"], "company": r["company"],
        "companyKey": r["company_key"], "loc": r.get("loc", ""),
        "url": r.get("url", ""), "postedOn": r.get("posted", ""),
        "family": r["family"], "familyEvidence": r.get("family_evidence"),
        "seniority": r["seniority"], "usStatus": r.get("us_status"),
        "sectorEvidence": r.get("sector_evidence", ""),
        "sectorReason": r.get("sector_reason", ""),
        "staffingEvidence": r.get("staffing_evidence", ""),
        "employerAnonymized": bool(r.get("employer_anonymized")),
        "source": r.get("source"), "layer": r.get("layer"),
        "descLen": len(r.get("desc", "")),
        "desc": (r.get("desc", "") or "")[:4000],
    } for r in merged]
    _sen_order = {"senior": 0, "director+": 1, "mid": 2, "junior": 3}
    ui_rows.sort(key=lambda x: (_sen_order.get(x["seniority"], 4),
                                x["postedOn"] or ""))
    _write_atomic(UI_DIR / "roles-pm.json", json.dumps({
        "snapshotDate": today, "rows": len(ui_rows),
        "envelope": {k: v for k, v in env.items()
                     if k not in ("queries",)},
        "jobs": ui_rows,
    }, default=str).encode())

    print(f"[sweep] kept {len(merged)} (dropped {len(dropped)}, "
          f"merges {counters['dedup_merge']}, family_other "
          f"{counters['family_other']}, non_us {counters['usgate_non_us']})")
    print(f"[sweep] families {fam_hist} | seniority {sen_hist}")
    print(f"[sweep] verdict {verdict}; dead: {dead or 'none'}")
    return 0 if verdict == "complete" else 1


def _csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    cols = ["title", "company", "company_key", "loc", "url", "posted",
            "family", "family_evidence", "seniority", "us_status",
            "us_evidence", "sector_evidence", "sector_reason",
            "staffing_evidence", "employer_anonymized",
            "source", "layer",
            "query", "rid", "description_source", "desc_len", "reason",
            "recoverable"]
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
    args = ap.parse_args()
    queries = PM_QUERY_CORE + (PM_QUERY_TAIL if args.full else [])
    if args.gj_only:
        rows, v = fetch_gj_role(PM_QUERY_CORE[:2])
        print(f"[gj-probe] {len(rows)} rows | {v}")
        return 0
    return sweep(queries, skip=args.skip,
                 seed_envelope=args.seed_envelope)


if __name__ == "__main__":
    sys.exit(main())
