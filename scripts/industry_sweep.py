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
_LOC_PAREN = re.compile(r"\s*[-–—(]\s*[^)]*[A-Z]{2}[^)]*\)?\s*$")


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
    """lower + strip trailing location parentheticals ('… (San Jose)'
    and '… - Walnut Creek') — the same role posted from 15 branches
    is ONE role in the rollup."""
    s = (title or "").lower().strip()
    s = _LOC_PAREN.sub("", s).strip()
    return re.sub(r"\s+", " ", s)


def _rollup(head: list[dict]) -> list[dict]:
    """Collapse multi-branch head-lined rows: one entry per
    (company_norm, title_norm), locations collected + counted."""
    groups: dict[tuple[str, str], dict] = {}
    for r in head:
        k = (_company_norm(r["company"]), _title_norm(r["title"]))
        g = groups.get(k)
        if not g:
            g = {**r, "locations": [], "n_locations": 0}
            groups[k] = g
        if r["loc"] and r["loc"] not in g["locations"]:
            g["locations"].append(r["loc"])
        g["n_locations"] += 1
    return sorted(groups.values(),
                  key=lambda g: (-g["n_locations"], g["company"]))


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
        for r in out:
            r["_src_mode"] = mode
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
        if gate == "us":
            reg = us_region_tag(loc_s, title,
                                origin=r.get("_src_mode") or "wide")
        else:
            reg = region_tag(loc_s, title)
        if reg == "other":
            gate_counts["drop:region_other"] = gate_counts.get(
                "drop:region_other", 0) + 1
            continue
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
            "titleMatchedQuery": bool(r.get("query")),
        })
        gate_counts[f"kept:{lvl}"] = gate_counts.get(f"kept:{lvl}", 0) + 1
        gate_counts[f"kept:region_{reg}"] = gate_counts.get(
            f"kept:region_{reg}", 0) + 1
        by_source_kept[src] = by_source_kept.get(src, 0) + 1

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
        kept_n = by_source_kept.get(name)
        extra = (f" (raw {raw}, kept {kept_n})"
                 if raw is not None else "")
        lines.append(f"- {name}: {v}{extra}")
    lines += ["", "## Gate counters", ""]
    for k in sorted(gate_counts):
        lines.append(f"- {k}: {gate_counts[k]}")
    if where_hist:
        lines += ["", "## WHERE histogram (head-lined, per state)",
                  ""]
        lines.append(f"- {json.dumps(where_hist)}")
    lines += ["", f"## Head-lined roles ({len(rolled)} unique of "
              f"{len(head)} raw — ×N locations)", ""]
    for g in rolled[:300]:
        locs = (f" ×{g['n_locations']} locations"
                if g["n_locations"] > 1 else "")
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
