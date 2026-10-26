#!/usr/bin/env python3
"""industry_sweep — the industry×region sweep engine (S41 stress test #2,
v2 = S42-R1-amended: local + remote, ALL-US-WIDE).

DESIGN-S42 v2 §2. Reuses the role_sweep fetcher roster (via import —
the adhoc-proven pattern), the shared geo_levels module (region_tag +
us_region_tag + industrial_level_tag — one module, no copies), and the
same evidence-first SOP. Parameterized by an INDUSTRIES registry: new
industries/regions = new data entries, not code.

The user cut (home services / construction, S42 directive):
  "local + remote (all US wide) home services & construction, any
   roles that don't require a license (not a licensed contractor /
   professional, but corp / office jobs all okay), anything not pure
   labor but that helps access the business... the roles seem quite
   small and that's where the stress test should push — may indicate
   coverage is low, need more robust ways."

Post-fetch gates (client-side, every drop labeled):
  1. region gate    entry.region_gate: "bay_area" → region_tag ∈
                   {bay_area, remote}; "us" → us_region_tag ∈
                   {us, remote} (origin-aware: wide-source remotes
                   need a US marker — S42-R1 F8)
  2. industry-affirm company ∨ title hits an industry token
                   (boundary-matched), OR desc first 500ch hits a
                   qualified phrase; else industry_no_affirm (kept as
                   evidence, never head-lined)
  3. level gate     industrial_level_tag ∈ {corp_office (head-lined),
                   field_mgmt (secondary)}; licensed/labor/retail/
                   unclassified counted and excluded per the user cut

v2 engine guarantees (the S42-R1 findings):
  * --budget-seconds (default 2400): BEFORE each source; past budget →
    "skipped:budget" and the gates still run — outputs ALWAYS written
    (F4: the 45-min yml timeout would otherwise kill a us run
    mid-fetch with zero artifacts, green, every week).
  * honest channels only (F10): PM-hardcoded feeds are declared
    "skipped:pm-hardcoded", never silently run; the measured-block
    channels (ziprecruiter/glassdoor) run attempt-and-record — the
    verdict IS the measurement and auto-reopens when a transport
    clears (firecrawl re-arm / ZenRows credits).
  * per_source_raw + by_source_kept live in the envelope (F12); the
    report prints ALL head-lined rows + the WHERE histogram.
  * staffing-brand split + multi-branch rollup (F11): headlined_unique
    on (company_norm, title_norm) — the dba part wins the company key;
    "×N locations" printed per role.

v2.2 (S47-B — DESIGN-S47 §6, findings-a2-industry §1, the P2 round):
  1. rollup n_locations = DISTINCT loc strings + n_rows (A2-4/F19 —
     740/3,964 groups were inflated by row counts).
  2. rollup KEYS strip trailing city/state + salary suffixes
     (A2-5/F19 — key-only re-derivation, raw titles unchanged; the
     report says so).
  3. per-source kept prints via the roster↔row-source alias map + the
     FULL head list (A2-6 — 'kept None' for 5 wide sources; the
     [:300] cap hid every wide-source group, li kept 227 invisible).
  4. per_query {source: {query: {raw, kept}}} persisted in the
     artifact + the query string per kept row (A2-10/F20 — only the
     titleMatchedQuery boolean survived before).
  5. the jd_cache consult wired before the industry-affirm gate
     (A2-11 — the role_sweep hook: strictly-longer + overlap-gated;
     908 ok JDs sat idle for industry rows). Fail-soft.
  6. dice freshness cap: >60d posted rows drop with a labeled
     counter (A2-9 — the only >60d kept rows, max 177d).
  7. adzuna category STAMP (sectorEvidence=adzuna_category:<tag>),
     never a filter (§6.7/A2-B7 — the 1.8× cut amputates the
     admin/sales-classified office slice).

Outputs:
  ingest/data/industry/<slug>.json         rows + envelope (all gates
                                           labeled — a measuring
                                           instrument, not a filter)
  ingest/data/industry/<slug>.report.md    the head-lined answer +
                                           per-source×gate counters
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # role_sweep
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))

import role_sweep  # noqa: E402
from jobsearch.geo_levels import (  # noqa: E402
    BAY_AREA,
    US_STATES,
    region_tag,
    us_region_tag,
    industrial_level_tag,
)
from jobsearch.rolefamilies import is_staffing_company  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "ingest/data/industry"

# ── the INDUSTRIES registry (data, not code) ────────────────────────────────
# role×industry cross-queries: role-first with industry qualifier —
# relevance beats exhaustiveness-per-query on capped APIs; the broad
# industry terms sweep the rest. Entries may declare region_gate
# ("bay_area" default); the us entries set location=None (US-wide
# defaults — the F14 pins: None = bit-identical US-wide).
_BA_QUERIES = [
    # office / admin
    "construction office manager",
    "construction administrative assistant",
    "construction dispatcher",
    "plumbing office",
    "construction receptionist",
    # sales / customer
    "home services sales",
    "remodeling sales representative",
    "construction sales",
    "plumbing customer service",
    "construction customer service representative",
    "hvac sales",
    # ops / coordination
    "construction project coordinator",
    "construction estimator",
    "construction assistant project manager",
    "remodeling project coordinator",
    "landscaping account manager",
    "construction scheduler",
    "construction purchasing",
    # marketing / GA
    "construction marketing coordinator",
    "home services marketing",
    "construction bookkeeper",
    "construction accounting",
    "home services operations manager",
    # broad sweeps
    "home services jobs",
    "construction company office",
    "home improvement careers",
    "remodeling company jobs",
]
# S42 v2 B3: the verticals the ba set doesn't cover (role-first form)
_US_EXTRA_QUERIES = [
    "cleaning service office manager",
    "cleaning company dispatcher",
    "moving company dispatcher",
    "handyman company office",
    "appliance repair dispatcher",
    "garage door sales representative",
    "gutter sales representative",
    "insulation estimator",
    "septic services office",
    "pool service coordinator",
    "painting company office",
    "tree service estimator",
    "junk removal dispatcher",
    "paving estimator",
    "countertop sales representative",
    "kitchen remodel coordinator",
    "fence sales representative",
    "water restoration coordinator",
]
_BA_AFFIRM = (
    # boundary-safe tokens (F12): door/window/paint/floor are
    # matched with \b boundaries; "contractor" only via
    # qualified phrases (1099 boilerplate never affirms)
    "construction", "remodel", "remodeling", "plumbing",
    "plumb", "electric", "hvac", "heating", "cooling",
    "roofing", "landscap", "pest control", "flooring",
    r"\bdoor\b", r"\bwindow\b", r"\bpaint(ing|er)?s?\b",
    "solar", "home improvement", "handyman", "restoration",
    "building trades", "general contractor",
    "licensed contractor", "building materials",
    "excavat", "concrete", "drywall", "siding", "fence",
    "property management", "home builder", "homebuilder",
)
# S42-R1 F9: the new verticals' affirm tokens — WITHOUT these the
# 18 new queries' rows die at industry_no_affirm (queries their own
# token set cannot match). Qualified forms only: bare \bpool\b hits
# "candidate pool", bare \bcleaning\b hits dry-cleaning, bare
# \bmoving\b hits "fast-moving", bare \bsecurity\b hits clearances.
_US_EXTRA_AFFIRM = (
    "gutter", "insulation", "septic", "pool service", "pool repair",
    "tree service", "junk removal", "paving", "countertop",
    "kitchen remodel", "bath remodel", "security system",
    "cleaning service", "moving company", "appliance repair",
    "demolition", "water damage", "mitigation", "maid service",
    "house cleaning", "chimney", "sewer", "drain cleaning",
)

INDUSTRIES: dict[str, dict] = {
    "home_services_ba": {
        "display": "Home services / construction × SF Bay Area",
        "location": "San Francisco Bay Area, CA",
        # adzuna's where= geocoder is city-shaped (the 2-pivot probe
        # answered: region strings return 0) — city + radius covers
        # the Peninsula/East Bay core; jooble/careerjet/indeed take
        # the wider region forms
        "adzuna_location": "San Francisco, CA",
        "region_gate": "bay_area",
        "queries": _BA_QUERIES,
        "affirm_tokens": _BA_AFFIRM,
    },
    "home_services_us": {
        "display": "Home services / construction × US-wide (local + remote)",
        "location": None,             # US-wide defaults on every
        "adzuna_location": None,      # location-aware fetcher (F14)
        "region_gate": "us",
        "queries": _BA_QUERIES + _US_EXTRA_QUERIES,
        "affirm_tokens": _BA_AFFIRM + _US_EXTRA_AFFIRM,
    },
}

# ── the affirm gate (F12) ───────────────────────────────────────────────────

def _compile_affirms(tokens) -> re.Pattern:
    parts = []
    for t in tokens:
        if t.startswith("\\b") or t.startswith("("):
            parts.append(t)             # already regex-shaped
        elif "*" in t:
            parts.append(re.escape(t).replace(r"\*", ".*"))
        else:
            parts.append(re.escape(t))
    return re.compile("|".join(parts), re.I)


def industry_affirm(industry: dict, company: str, title: str,
                    desc: str) -> str:
    """Returns 'company' | 'title' | 'desc' | '' — the evidence tier
    that affirmed the industry (company∨title primary; desc only via
    the qualified token set, first 500ch)."""
    pat = industry["_affirm_pat"]
    if pat.search(company or ""):
        return "company"
    if pat.search(title or ""):
        return "title"
    if pat.search((desc or "")[:500]):
        return "desc"
    return ""


# ── the multi-branch rollup (S42-R1 F11) ────────────────────────────────────

_DBA_PAT = re.compile(r"\bdba\b|\bd\.b\.a\.\b", re.I)
_CORP_SUFFIX = re.compile(
    r"\b(?:inc|llc|l\.l\.c\.?|co\b|corp|corporation|ltd|company|"
    r"plc|gp|lp)\b\.?$", re.I)

# S47-B §6.2 (A2-5, F19): title-embedded city/salary suffixes split
# rollup keys — "Project Coordinator - Construction | Becker, MN",
# "… - Base Salary to 100k/year - Warren, MI" (Michael Page style)
# and "Estimator in Los Angeles" are ONE role posted from many
# branches (the audit measured 41 keys carrying embedded
# city/state fragments). KEY-only: the row's raw title stays
# untouched (a labeled metric re-derivation per F19 — the report
# carries the note). Cities match an ENUMERABLE set (top US metros ∪
# the geo_levels BAY_AREA belt — the conservative choice: a bare
# trailing "- Senior" never strips); "City, ST" tails use the
# us_region_tag state set (the audit's mirror); salary tails need a
# $ amount, a k/rate unit or a salary word (a bare trailing digit
# NEVER strips — "Estimator 2" is a level, not a wage).
_METRO_CITIES = (
    "new york", "new york city", "nyc", "los angeles", "chicago",
    "houston", "phoenix", "philadelphia", "san antonio", "san diego",
    "dallas", "austin", "jacksonville", "fort worth", "columbus",
    "charlotte", "indianapolis", "seattle", "denver", "washington dc",
    "washington", "boston", "las vegas", "portland", "louisville",
    "memphis", "detroit", "baltimore", "milwaukee", "albuquerque",
    "fresno", "tucson", "mesa", "kansas city", "atlanta", "omaha",
    "colorado springs", "raleigh", "virginia beach", "long beach",
    "miami", "minneapolis", "tulsa", "bakersfield", "wichita",
    "arlington", "anaheim", "honolulu", "tampa", "st louis",
    "st. louis", "saint louis", "pittsburgh", "cincinnati",
    "cleveland", "orlando", "new orleans", "salt lake city",
    "providence", "richmond", "hartford", "buffalo", "rochester",
)
_CITY_ALT = "|".join(sorted(set(_METRO_CITIES) | set(BAY_AREA),
                            key=len, reverse=True))
_ST_ALT = "|".join(US_STATES)
_AREA_WORDS = (r"(?:\s+(?:area|metro(?:politan)?|county|region"
               r"|and\s+(?:surrounding\s+)?areas?))?")
# a salary amount: $-prefixed, a k/m unit, or a per-rate form —
# never a bare trailing number
_AMT = (r"\$\s?\d[\d,\.]*(?:\s?[km])?"
        r"|\d[\d,\.]*\s?[km](?![a-z])"
        r"|\d[\d,\.]*(?:\s?(?:/\s*|per\s+)(?:hr|hour|yr|year|annum"
        r"|week|wk|month))")
_SAL_WORDS = (r"(?:(?:base\s+)?salary(?:\s+up)?\s+to\s+"
              r"|(?:base\s+)?salary\s+|pay:?\s+(?:up\s+to\s+)?|"
              r"wages?\s+|earn(?:ings)?\s+(?:up\s+to\s+)?|making\s+"
              r"|up\s+to\s+)?")
_SALARY_TAIL = re.compile(
    rf"\s*[-–—|,;:]?\s*{_SAL_WORDS}"
    rf"(?:{_AMT})"
    rf"(?:\s*(?:[-–—]|\bto\b)\s*(?:{_AMT}))?"
    rf"\s*\+?"
    rf"(?:\s*(?:/\s*|per\s+)(?:hr|hour|hrs|yr|year|years|annum"
    rf"|week|wk|weeks|month|months)|\s+annually|\s+per\s+hour)?"
    rf"\s*$")
# trailing "City, ST" (after -, –, —, | or a comma)
_CITY_ST_TAIL = re.compile(
    rf"\s*[-–—|,]\s*[a-z][a-z .']*\s*,\s*(?:{_ST_ALT})\s*$")
# trailing "- <known city>" (+ optional area words / state code)
_CITY_SEP_TAIL = re.compile(
    rf"\s*[-–—|]\s*(?:{_CITY_ALT}){_AREA_WORDS}\s*"
    rf"(?:,\s*(?:{_ST_ALT}))?\s*$")
# trailing "in <known city>" — the enumerable set is the guard: "Manager
# in Training" (the MIT program) is a DIFFERENT role from "Manager"
_IN_CITY_TAIL = re.compile(
    rf"(?:\s*[-–—|]\s*)?\s*\bin\s+(?:{_CITY_ALT}){_AREA_WORDS}"
    rf"[a-z .',]*\s*$")
# paren-location endings on the LOWERCASED key ('… (san jose)', '…
# (walnut creek, ca)') — supersedes the S42 _LOC_PAREN, which
# required [A-Z]{2} post-lower and could never fire (dead code the
# A2-5 audit caught in passing). Known residual, documented: a
# non-city parenthetical ('(Experience Required)') never strips.
_LOC_PAREN = re.compile(
    rf"\s*\(\s*(?:{_CITY_ALT}){_AREA_WORDS}[a-z .',]*\s*\)\s*$")


def _company_norm(company: str) -> str:
    """The dba part wins ('Able General Engineering dba Able Plumbing'
    → 'able plumbing' — the BRAND is the business the role belongs
    to); strip punctuation + corporate suffixes."""
    s = (company or "").lower().strip()
    if _DBA_PAT.search(s):
        s = _DBA_PAT.split(s)[-1]
    s = re.sub(r"[^\w\s]", " ", s)
    s = _CORP_SUFFIX.sub("", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def _title_norm(title: str) -> str:
    """The ROLLUP-KEY derivation (never shown as a title): lower +
    strip trailing city/state + salary suffixes innermost-out — the
    same role posted from 15 branches is ONE role in the rollup.
    S47-B §6.2 (A2-5/F19): key-only — the row's raw title stays
    untouched (the report carries the note)."""
    s = (title or "").lower().strip()
    prev = None
    while prev != s:
        prev = s
        for pat in (_SALARY_TAIL, _CITY_ST_TAIL, _CITY_SEP_TAIL,
                    _IN_CITY_TAIL, _LOC_PAREN):
            s = pat.sub("", s).strip()
        s = s.rstrip("-–—|,;: ").strip()
    return re.sub(r"\s+", " ", s)


def _rollup(head: list[dict]) -> list[dict]:
    """Collapse multi-branch head-lined rows: one entry per
    (company_norm, title_norm), locations collected + counted.
    S47-B §6.1 (A2-4/F19): n_locations = DISTINCT loc strings per
    key (was rows — 740/3,964 groups were inflated by multi-source
    duplicates of one posting); n_rows carries the posting count
    (the branch-count sort stays bit-identical to the old
    row-count order)."""
    groups: dict[tuple[str, str], dict] = {}
    for r in head:
        k = (_company_norm(r["company"]), _title_norm(r["title"]))
        g = groups.get(k)
        if not g:
            g = {**r, "locations": [], "n_locations": 0, "n_rows": 0}
            groups[k] = g
        if r["loc"] and r["loc"] not in g["locations"]:
            g["locations"].append(r["loc"])
            g["n_locations"] += 1
        g["n_rows"] += 1
    return sorted(groups.values(),
                  key=lambda g: (-g["n_rows"], g["company"]))


# S47-B §6.3 (A2-6): roster-name ↔ row-source alias map — rows tag
# their fetcher's source ('li'), the report/envelope lookups key on
# the roster name ('li_keyword'); five wide sources printed 'kept
# None' while li actually kept 227 (invisible — the undercount hid a
# live source). by_source_kept stays keyed by ROW source (the
# granular truth: jobicy vs remoteok); the report aggregates.
_SRC_ALIAS: dict[str, tuple[str, ...]] = {
    "li_keyword": ("li",),
    "trio": ("arbeitnow", "jobicy", "remoteok"),
}
_SRC_TO_ROSTER = {s: r for r, ss in _SRC_ALIAS.items() for s in ss}


# ── the sweep ───────────────────────────────────────────────────────────────

def _seed_prior(slug: str, ran_at: str) -> list[dict]:
    """v2.1 (post-review, the F1 seed doctrine): union the prior
    artifact's kept rows as inputs — the careerjet lesson. The us leg
    measured careerjet at 2,781 raw / 2,160 kept rows (the single
    biggest industry source) with an HK-ONLY transport; every Azure
    re-run would WIPE those rows from the artifact without a seed
    union (the families' seed-envelope class, applied to the industry
    engine). Staleness-bounded: carried rows must carry a posted date
    within 60d of the run (a stale carry is a museum, not a corpus)."""
    p = OUT_DIR / f"{slug}.json"
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text())
    except json.JSONDecodeError:
        return []
    jobs = d.get("jobs") or []
    if not jobs:
        return []
    try:
        today = datetime.strptime(ran_at[:10], "%Y-%m-%d").date()
    except ValueError:
        return jobs[:0]
    out = []
    for r in jobs:
        pd = (r.get("posted") or "")[:10]
        gen = int(r.get("seedGen", 0))
        try:
            age = (today - datetime.strptime(pd, "%Y-%m-%d").date()).days
        except ValueError:
            # dateless: the families' F1 doctrine (dateless-with-
            # evidence) with a GENERATION bound — a dateless row
            # carries at most 2 generations, then must be re-fetched
            # to re-enter (a museum row needs a fresh sighting).
            if gen < 2:
                out.append(r)
            continue
        if 0 <= age <= 60:
            out.append(r)
    return out


def _posted_age_days(posted: str, ran_at: str) -> int | None:
    """Row age in days vs the run date — None = dateless/unparseable
    (NEVER a staleness drop: the S42-R1 F1 dateless doctrine). The
    S47-B §6.6 dice gate and the seed union share this 60d bound."""
    try:
        return ((datetime.strptime(ran_at[:10], "%Y-%m-%d").date()
                 - datetime.strptime((posted or "")[:10],
                                     "%Y-%m-%d").date()).days)
    except ValueError:
        return None


def run(industry_slug: str, skip: str = "", budget_seconds: int = 2400,
        no_seed: bool = False) -> int:
    ind = INDUSTRIES.get(industry_slug)
    if not ind:
        print(f"unknown industry: {industry_slug} "
              f"(known: {sorted(INDUSTRIES)})")
        return 2
    ind["_affirm_pat"] = _compile_affirms(ind["affirm_tokens"])
    gate = ind.get("region_gate", "bay_area")
    skip_set = {s.strip() for s in skip.split(",") if s.strip()}
    started = datetime.now(timezone.utc)
    loc = ind["location"]
    queries = ind["queries"]

    # S42-R1 F10: PM-hardcoded feeds — declared, never silently run
    # (the F9 doctrine). v1 counted their "ok" verdicts as industry
    # measurements; they measured Product-Management feeds.
    PM_HARDCODED = ("muse", "talent", "himalayas", "wwr")

    sources: list[tuple[str, object, list, str]] = []
    # location-aware fetchers (S41 geo params; None = US-wide default).
    # adzuna takes the city-shaped override (the 2-pivot probe);
    # "geo" mode = US-locale API ⇒ US-remote by construction (F8).
    sources.append(("adzuna", role_sweep.fetch_adzuna,
                    [queries, ind.get("adzuna_location") or loc], "geo"))
    sources.append(("jooble", role_sweep.fetch_jooble,
                    [queries, loc], "geo"))
    sources.append(("indeed", role_sweep.fetch_jobspy_indeed,
                    [queries, loc], "geo"))
    sources.append(("careerjet", role_sweep.fetch_careerjet,
                    [queries, loc], "geo"))
    # ziprecruiter: the home-services-heavy board — measured 403
    # (jobspy, 'use a proxy') + 402 (ZenRows credits); attempt-and-
    # record; auto-reopens when a transport clears.
    sources.append(("ziprecruiter", role_sweep.fetch_ziprecruiter,
                    [queries, loc], "geo"))
    sources.append(("glassdoor", role_sweep.fetch_glassdoor_zenrows,
                    [queries, loc], "geo"))
    # query-keyed US-centric boards
    sources.append(("li_keyword", role_sweep.fetch_li_keyword,
                    [queries], "wide"))
    sources.append(("dice", role_sweep.fetch_dice, [queries], "wide"))
    sources.append(("usajobs", role_sweep.fetch_usajobs,
                    [queries], "geo"))        # federal = US-only
    # keyless/whole-index sources: US-wide, client-side region filter
    sources.append(("findwork", role_sweep.fetch_findwork,
                    [queries], "wide"))
    sources.append(("trio", role_sweep.fetch_free_trio, [], "wide"))
    sources.append(("yc", role_sweep.fetch_yc, [], "wide"))
    sources.append(("wellfound", role_sweep.fetch_wellfound, [], "wide"))
    sources.append(("hn", role_sweep.fetch_hn, [], "wide"))
    # firecrawl-dependent (credits exhausted until the Oct-24 renewal):
    # expected-zero declared, attempted anyway — a 0 here must never
    # be misread as "the industry isn't on this source"
    sources.append(("gj", role_sweep.fetch_gj_role, [queries], "wide"))
    sources.append(("builtin", role_sweep.fetch_builtin,
                    [queries], "wide"))

    env: dict = {"sources": {}, "ran_at": started.strftime(
        "%Y-%m-%dT%H:%M:%SZ"), "industry": industry_slug,
        "location": loc, "region_gate": gate}
    for name in PM_HARDCODED:
        env["sources"][name] = "skipped:pm-hardcoded"
    rows: list[dict] = []
    per_source_raw: dict[str, int] = {}
    # S47-B §6.4 (A2-10/F20): per-query raw/kept yield — {source:
    # {query: {"raw": N, "kept": M}}} in the artifact (only the
    # titleMatchedQuery boolean survived before; raw rows were
    # discarded and the §4 vertical-yield analysis had to run by
    # token proxy)
    per_q_raw: dict[str, dict[str, int]] = {}
    per_q_kept: dict[str, dict[str, int]] = {}
    t0 = time.time()
    for name, fn, args, mode in sources:
        if name in skip_set:
            env["sources"][name] = "skipped"
            continue
        # F4: the budget guard — outputs are ALWAYS written; a source
        # past the budget is honestly labeled, never silently dropped
        if time.time() - t0 > budget_seconds:
            env["sources"][name] = "skipped:budget"
            continue
        try:
            out, v = fn(*args)
        except Exception as exc:               # D3: record, never raise
            out, v = [], f"error:{type(exc).__name__}"
        env["sources"][name] = v
        per_source_raw[name] = len(out)
        qd = per_q_raw.setdefault(name, {})
        for r in out:
            r["_src_mode"] = mode
            # S47-B §6.3: the roster identity rides the row (rows tag
            # their fetcher's source — 'li' — while the report keys on
            # the roster name — 'li_keyword'; seeds lack _roster and
            # fall back to the alias map at count time)
            r["_roster"] = name
            q = r.get("query") or ""
            if q:
                qd[q] = qd.get(q, 0) + 1
        rows += out
        print(f"[{industry_slug}:{name}] {len(out)} rows | {v} "
              f"({int(time.time() - t0)}s elapsed)")

    # ── the gates (every drop labeled) ─────────────────────────────
    # v2.1: the seed union — prior-artifact rows re-enter as inputs
    # BEFORE the url-dedupe, so a careerjet row carried from the HK
    # leg merges with (or stands in for) the same posting re-fetched
    # by the Azure lane. Recorded like every other source.
    seed_rows = [] if no_seed else _seed_prior(industry_slug, env["ran_at"])
    env["sources"]["seed_envelope"] = (
        f"ok:{len(seed_rows)}" if seed_rows else "ok:skipped")
    for r in seed_rows:
        r["_seed"] = True
        # srcMode is persisted in the kept schema for EXACTLY this
        # re-entry: a careerjet seed row re-classifying as 'wide'
        # would lose its US-remote rows at the us gate (origin-aware
        # remote — S42-R1 F8). The origin the row was FETCHED under
        # is part of its identity.
        if not r.get("_src_mode") and r.get("srcMode"):
            r["_src_mode"] = r["srcMode"]
        rows.append(r)

    kept: list[dict] = []
    gate_counts: dict[str, int] = {}
    by_source_kept: dict[str, int] = {}
    # S47-B §6.5 (A2-11): the jd_cache consult setup — the SAME
    # hook the role_sweep family run() uses (strictly-longer desc +
    # overlap-gated): a cached FULL JD (jd-refetch lane / local leg,
    # keyed (source, url)) upgrades a thin API snippet BEFORE the
    # industry-affirm gate, so the desc tier fires on real text. The
    # audit measured 908 ok JDs idle for industry rows (the consult
    # lived only inside the families' run()). Fail-soft: an
    # absent/unreadable cache is a no-op, recorded in the envelope.
    try:
        jd_cache = role_sweep._load_jd_cache()
        env["jd_cache"] = (f"ok:{len(jd_cache)}entries"
                           if jd_cache else "absent")
    except Exception as exc:
        jd_cache, env["jd_cache"] = {}, f"error:{type(exc).__name__}"
    jd_cache_hits = 0
    adzuna_stamped = 0
    seen_urls: set[str] = set()
    for r in rows:
        # light url-identity dedupe (same posting twice via one source)
        u = r.get("url") or ""
        if u and u in seen_urls:
            gate_counts["drop:dup_url"] = gate_counts.get(
                "drop:dup_url", 0) + 1
            continue
        if u:
            seen_urls.add(u)
        title = r.get("title") or ""
        company = r.get("company") or ""
        loc_s = r.get("loc") or ""
        desc = r.get("desc") or ""
        src = r.get("source") or r.get("_src") or "?"
        # S47-B §6.6 (A2-9): the dice freshness cap — fetch_dice takes
        # no date param (adzuna 45d / indeed 30d / li 30d all cap;
        # dice rode 177d rows into a fresh weekly artifact — the only
        # >60d kept rows the audit found, 13 of them). The SAME 60d
        # bound the seed union uses (_seed_prior); the drop is
        # labeled like every gate.
        if src == "dice":
            _age = _posted_age_days(r.get("posted") or "", env["ran_at"])
            if _age is not None and _age > 60:
                gate_counts["drop:stale_dice"] = gate_counts.get(
                    "drop:stale_dice", 0) + 1
                continue
        if gate == "us":
            reg = us_region_tag(loc_s, title,
                                origin=r.get("_src_mode") or "wide")
        else:
            reg = region_tag(loc_s, title)
        if reg == "other":
            gate_counts["drop:region_other"] = gate_counts.get(
                "drop:region_other", 0) + 1
            continue
        # S47-B §6.5 (A2-11): the consult itself — strictly-longer +
        # overlap-gated (a garbage-longer capture never replaces a
        # clean snippet; role_sweep's S41-R1 F3 hook, applied to the
        # industry keep/grade path). Seeds flow the same loop, so a
        # cache hit rides a careerjet carry too.
        jd_cached = False
        if jd_cache:
            _cinfo = jd_cache.get(role_sweep._jd_cache_key(src, u))
            if (_cinfo and _cinfo.get("status") == "ok"
                    and _cinfo.get("desc_len", 0) > len(desc)
                    and role_sweep._jd_overlap_ok(desc, _cinfo["desc"],
                                                    title)):
                desc = _cinfo["desc"][:2000]
                jd_cached = True
                jd_cache_hits += 1
        aff = industry_affirm(ind, company, title, desc)
        if not aff:
            gate_counts["drop:industry_no_affirm"] = gate_counts.get(
                "drop:industry_no_affirm", 0) + 1
            continue
        lvl = industrial_level_tag(title, desc)
        if lvl in ("licensed_professional", "pure_labor",
                   "retail_service"):
            gate_counts[f"drop:level_{lvl}"] = gate_counts.get(
                f"drop:level_{lvl}", 0) + 1
            continue
        staffing = is_staffing_company(company)
        q = r.get("query") or ""
        kept.append({
            "title": title, "company": company, "loc": loc_s,
            "url": r.get("url") or "", "posted": (r.get("posted")
                                                  or "")[:10],
            "desc": desc[:2000], "source": src,
            "region": reg, "affirmTier": aff, "level": lvl,
            "staffingBrand": staffing,
            "srcMode": r.get("_src_mode") or "wide",
            "seed": bool(r.get("_seed")),
            "seedGen": int(r.get("seedGen", 0)) + (
                1 if r.get("_seed") else 0),
            # S47-B §6.4 (A2-10/F20): the query string persists per
            # row — only the boolean survived before, so per-query
            # yield was untraceable from the artifact.
            "query": q,
            "titleMatchedQuery": bool(r.get("query")),
        })
        # S47-B §6.7 (A2-B7): STAMP, never filter — the live probe
        # measured a 1.8× category cut that would amputate the
        # admin/sales-classified office slice (75 admin-jobs rows on
        # ONE query); the tag rides the fetcher's adzuna_category
        # field (stamped on every adzuna row, filtered or not) and
        # lands as its own row field + sectorEvidence.
        _cat = (r.get("adzuna_category") or "").strip()
        if _cat:
            kept[-1]["adzuna_category"] = _cat
            kept[-1]["sectorEvidence"] = f"adzuna_category:{_cat}"
            adzuna_stamped += 1
        if jd_cached:
            kept[-1]["jd_cached"] = True
        if q:
            _roster = r.get("_roster") or _SRC_TO_ROSTER.get(src, src)
            qd = per_q_kept.setdefault(_roster, {})
            qd[q] = qd.get(q, 0) + 1
        gate_counts[f"kept:{lvl}"] = gate_counts.get(f"kept:{lvl}", 0) + 1
        gate_counts[f"kept:region_{reg}"] = gate_counts.get(
            f"kept:region_{reg}", 0) + 1
        by_source_kept[src] = by_source_kept.get(src, 0) + 1

    # S47-B §6.5/§6.7: the non-gate counters ride the ENVELOPE (the
    # gates dict must stay a pure drop:/kept: ledger — its counters
    # are the verified sums-to-raw invariant; stamps and consult
    # hits would double-count there).
    env["jd_cache_hits"] = jd_cache_hits
    env["adzuna_category_stamped"] = adzuna_stamped
    env["source_aliases"] = dict(_SRC_ALIAS)

    # S47-B §6.4 (A2-10/F20): the per-query yield table, bounded at
    # the registry size per source (the query list is enumerable;
    # feed pseudo-queries like 'remoteok:feed' count inside the same
    # bound — largest yields survive a cap). Lands BEFORE §7.5's
    # completeness gate (F20).
    per_query: dict[str, dict[str, dict[str, int]]] = {}
    _cap = max(len(queries), 8)
    for _roster in set(per_q_raw) | set(per_q_kept):
        _t: dict[str, dict[str, int]] = {}
        for _q in (set(per_q_raw.get(_roster, {}))
                   | set(per_q_kept.get(_roster, {}))):
            _t[_q] = {"raw": per_q_raw.get(_roster, {}).get(_q, 0),
                      "kept": per_q_kept.get(_roster, {}).get(_q, 0)}
        if len(_t) > _cap:
            _t = dict(sorted(_t.items(),
                            key=lambda kv: (-(kv[1]["raw"]
                                             + kv[1]["kept"]),
                                            kv[0]))[:_cap])
        per_query[_roster] = _t

    # head-lining is TIER-GATED (the lane-1 FP lesson): desc-tier
    # affirms are KEPT as review evidence but never head-lined.
    head = [k for k in kept if k["level"] == "corp_office"
            and k["region"] in (
                ("bay_area", "remote") if gate == "bay_area"
                else ("us", "remote"))
            and k["affirmTier"] in ("company", "title")]
    head.sort(key=lambda x: (x["region"] not in (
        ("bay_area",) if gate == "bay_area" else ("us",)),
        x["level"], x["company"]))
    rolled = _rollup(head)
    staffing_head = sum(1 for h in head if h["staffingBrand"])

    # F12: the WHERE histogram — the us mode's "and where?" answer
    where_hist: dict[str, int] = {}
    if gate == "us":
        for h in head:
            m = re.search(r",\s*([A-Z]{2})\s*$", h["loc"])
            where_hist[m.group(1) if m else h["region"]] = \
                where_hist.get(
                    m.group(1) if m else h["region"], 0) + 1
        where_hist = dict(sorted(where_hist.items(),
                                 key=lambda kv: -kv[1]))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = OUT_DIR / f"{industry_slug}.json"
    out_json.write_text(json.dumps({
        "ran_at": env["ran_at"], "industry": industry_slug,
        "envelope": env,
        "gates": gate_counts,
        "per_source_raw": per_source_raw,
        "by_source_kept": by_source_kept,
        "kept": len(kept), "headlined_raw": len(head),
        "headlined_unique": len(rolled),
        "headlined_staffing": staffing_head,
        "where_histogram": where_hist,
        # S47-B §6.4 (A2-10/F20): the per-query yield table
        "per_query": per_query,
        "jobs": kept, "headlined_rolled": rolled,
    }, default=str, indent=1))

    lines = [
        f"# {ind['display']} — industry sweep report",
        "",
        f"- ran: {env['ran_at']} | raw rows: {len(rows)} | kept: "
        f"{len(kept)} | **head-lined raw: {len(head)} | unique roles "
        f"(company×title rollup): {len(rolled)} | staffing-brand: "
        f"{staffing_head}**",
        f"- region: {loc or 'US-wide'} (gate: {gate} | adzuna geo: "
        f"{ind.get('adzuna_location') or 'US-wide'})",
        "",
        "## Sources (the exhaustion measurement)",
        "",
    ]
    for name, v in env["sources"].items():
        raw = per_source_raw.get(name)
        if raw is None:
            lines.append(f"- {name}: {v}")
            continue
        # S47-B §6.3 (A2-6): the kept lookup aggregates the roster's
        # ROW-source names (rows tag 'li', the roster is
        # 'li_keyword' — five wide sources printed 'kept None' while
        # li actually kept 227, invisible). A source that RAN always
        # prints a number: 'None' reads as unmeasured.
        kept_n = sum(by_source_kept.get(s, 0)
                     for s in (name,) + _SRC_ALIAS.get(name, ()))
        lines.append(f"- {name}: {v} (raw {raw}, kept {kept_n})")
    # S47-B §6.5/§6.7/§6.4: the consult + stamp + yield lines
    lines.append(f"- jd_cache consult: {env['jd_cache']} "
                 f"({jd_cache_hits} desc upgrades — S47-B §6.5)")
    lines.append(f"- adzuna category: {adzuna_stamped} stamped, "
                 f"0 filtered (stamp-don't-filter — DESIGN-S47 §6.7)")
    lines.append(f"- per-query yield: "
                 f"{sum(len(v) for v in per_query.values())} "
                 f"source×query cells persisted (artifact per_query)")
    lines += ["", "## Gate counters", ""]
    for k in sorted(gate_counts):
        lines.append(f"- {k}: {gate_counts[k]}")
    if where_hist:
        lines += ["", "## WHERE histogram (head-lined, per state)",
                  ""]
        lines.append(f"- {json.dumps(where_hist)}")
    lines += ["", f"## Head-lined roles ({len(rolled)} unique of "
              f"{len(head)} raw — n_rows × n_locations per role) — "
              f"rollup keys suffix-stripped (trailing city/state + "
              f"salary tails — DESIGN-S47 §6.2/F19; the rows' raw "
              f"titles are unchanged)", ""]
    # S47-B §6.3 (A2-6): the FULL list — the [:300] cap (branch-count
    # order) hid every wide-source group: li's 140 groups all ranked
    # ≥335, so a 227-kept source was invisible in the report.
    for g in rolled:
        locs = ""
        if g["n_rows"] > 1:
            locs = f" · {g['n_rows']} rows"
            if g["n_locations"] > 1:
                locs += f" ×{g['n_locations']} locations"
        st = " [staffing]" if g.get("staffingBrand") else ""
        lines.append(
            f"- **{g['title']}** — {g['company']}{st} | "
            f"{g['locations'][0] if g['locations'] else g['loc']}"
            f"{locs} | {g['region']} | {g['source']} | "
            f"posted {g['posted']}")
    (OUT_DIR / f"{industry_slug}.report.md").write_text("\n".join(lines))

    print(f"[industry_sweep] raw={len(rows)} kept={len(kept)} "
          f"head-raw={len(head)} head-unique={len(rolled)}")
    genv = os.environ.get("GITHUB_ENV")
    if genv:
        with open(genv, "a") as f:
            f.write(f"INDUSTRY_KEPT={len(kept)}\n"
                    f"INDUSTRY_HEAD={len(head)}\n"
                    f"INDUSTRY_HEAD_UNIQUE={len(rolled)}\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--industry", default="home_services_ba")
    ap.add_argument("--skip", default="")
    ap.add_argument("--budget-seconds", type=int, default=2400,
                    help="fetch budget; past it, remaining sources "
                         "are skipped:budget and gates still run "
                         "(outputs ALWAYS written — S42-R1 F4)")
    ap.add_argument("--no-seed", action="store_true",
                    help="disable the prior-artifact seed union (the "
                         "HK-transport careerjet rows' carrier — "
                         "v2.1)")
    args = ap.parse_args()
    return run(args.industry, args.skip, args.budget_seconds,
               no_seed=args.no_seed)


if __name__ == "__main__":
    raise SystemExit(main())
