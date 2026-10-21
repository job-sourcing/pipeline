#!/usr/bin/env python3
"""industry_sweep — the industry×region sweep engine (S41 stress test #2).

DESIGN-S41 v2 §3: the second STANDARDIZED engine — reuses the role_sweep
fetcher roster (all sources, via import — the adhoc-proven pattern),
the shared geo_levels module (region_tag + industrial_level_tag — one
module, no copies), and the same evidence-first SOP. Parameterized by
an INDUSTRIES registry: new industries = new data entries, not code.

The user cut (home services / construction × SF Bay Area):
  "local home services / construction related, any roles that doesn't
   require license (like not a licensed contractor / professional, but
   corp / office jobs all okay), local or remote, anything that is not
   pure labor but can help access the business, likely lower end pay
   but okay, SMB fine or bigger. be exhaustive... try exhaustively all
   the sources as they may not be all on linkedin... indeed.com etc."

Post-fetch gates (client-side, every drop labeled):
  1. region gate      region_tag ∈ {bay_area, remote} (CA-context-gated,
                     VDC-deny; the adhoc classifier shared)
  2. industry-affirm company ∨ title hits an industry token (boundary-
                     matched — the F12 door/window/paint class), OR the
                     desc's first 500ch hits a QUALIFIED phrase; else
                     bucket industry_no_affirm (counted, kept as
                     evidence, never head-lined)
  3. level gate      industrial_level_tag ∈ {corp_office (head-lined),
                     field_mgmt (secondary)}; licensed_professional /
                     pure_labor / retail_service / unclassified counted
                     and excluded per the user's cut

Outputs:
  ingest/data/industry/<slug>.json         rows + envelope (all gates
                                           labeled — a measuring
                                           instrument, not a filter)
  ingest/data/industry/<slug>.report.md    the head-lined answer +
                                           per-source×gate counters

Never crashes: every source failure recorded (D3). Exit 0 with a
summary; JD_REFETCH-style env vars for the lane.
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
    industrial_level_tag,
)

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "ingest/data/industry"

# ── the INDUSTRIES registry (data, not code) ────────────────────────────────
# role×industry cross-queries: role-first with industry qualifier —
# relevance beats exhaustiveness-per-query on capped APIs; the broad
# industry terms sweep the rest. Each entry: queries, the geo string
# for the location-aware fetchers, and the affirm-token set.
INDUSTRIES: dict[str, dict] = {
    "home_services_ba": {
        "display": "Home services / construction × SF Bay Area",
        "location": "San Francisco Bay Area, CA",
        # adzuna's where= geocoder is city-shaped (the 2-pivot probe
        # answered: region strings return 0) — city + 50mi radius
        # covers the Peninsula/East Bay core; jooble/careerjet/indeed
        # take the wider region forms
        "adzuna_location": "San Francisco, CA",
        "queries": [
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
        ],
        "affirm_tokens": (
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
        ),
    },
}


# ── the affirm gate (F12) ───────────────────────────────────────────────────

def _compile_affirms(tokens) -> re.Pattern:
    parts = []
    for t in tokens:
        if t.startswith("\\b") or t.startswith("("):
            parts.append(t)             # already regex-shaped
        elif t.endswith(("*" ) ) or "*" in t:
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


# ── the sweep ───────────────────────────────────────────────────────────────

def run(industry_slug: str, skip: str = "") -> int:
    ind = INDUSTRIES.get(industry_slug)
    if not ind:
        print(f"unknown industry: {industry_slug} "
              f"(known: {sorted(INDUSTRIES)})")
        return 2
    ind["_affirm_pat"] = _compile_affirms(ind["affirm_tokens"])
    skip_set = {s.strip() for s in skip.split(",") if s.strip()}
    started = datetime.now(timezone.utc)
    loc = ind["location"]
    queries = ind["queries"]

    sources: list[tuple[str, object, list, str]] = []
    # location-aware fetchers (S41 geo params; None = US-wide default).
    # adzuna takes the city-shaped override (the 2-pivot probe measured
    # region strings → 0 rows); jooble/careerjet/indeed take the region
    # form — each API's geo dialect is its own pin.
    sources.append(("adzuna", role_sweep.fetch_adzuna,
                    [queries, ind.get("adzuna_location") or loc],
                    "geo"))
    sources.append(("jooble", role_sweep.fetch_jooble,
                    [queries, loc], "geo"))
    sources.append(("indeed", role_sweep.fetch_jobspy_indeed,
                    [queries, loc], "geo"))
    # careerjet: 403-on-Azure documented — still attempted (evidence,
    # not assumption); the HK local leg is the working transport
    sources.append(("careerjet", role_sweep.fetch_careerjet,
                    [queries, loc], "geo"))
    # keyless/whole-index sources: US-wide, client-side region filter
    sources.append(("findwork", role_sweep.fetch_findwork,
                    [queries], "wide"))
    sources.append(("muse", role_sweep.fetch_muse, [], "wide"))
    sources.append(("talent", role_sweep.fetch_talent, [], "wide"))
    sources.append(("himalayas", role_sweep.fetch_himalayas, [], "wide"))
    sources.append(("wwr", role_sweep.fetch_wwr, [], "wide"))
    sources.append(("trio", role_sweep.fetch_free_trio, [], "wide"))
    sources.append(("yc", role_sweep.fetch_yc, [], "wide"))
    sources.append(("wellfound", role_sweep.fetch_wellfound, [], "wide"))
    sources.append(("hn", role_sweep.fetch_hn, [], "wide"))
    # firecrawl-dependent (credits exhausted until Oct-24 renewal):
    # expected-zero declared, attempted anyway — a 0 here must never be
    # misread as "the industry isn't on this source"
    sources.append(("gj", role_sweep.fetch_gj_role, [queries], "wide"))
    sources.append(("builtin", role_sweep.fetch_builtin,
                    [queries], "wide"))

    env: dict = {"sources": {}, "ran_at": started.strftime(
        "%Y-%m-%dT%H:%M:%SZ"), "industry": industry_slug,
        "location": loc}
    rows: list[dict] = []
    for name, fn, args, mode in sources:
        if name in skip_set:
            env["sources"][name] = "skipped"
            continue
        try:
            out, v = fn(*args)
        except Exception as exc:               # D3: record, never raise
            out, v = [], f"error:{type(exc).__name__}"
        env["sources"][name] = v
        for r in out:
            r["_src_mode"] = mode
        rows += out
        print(f"[{industry_slug}:{name}] {len(out)} rows | {v}")

    # ── the gates (every drop labeled) ─────────────────────────────
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
        reg = region_tag(loc_s, title)
        if reg == "other":
            gate_counts["drop:region_other"] = \
                gate_counts.get("drop:region_other", 0) + 1
            continue
        aff = industry_affirm(ind, company, title, desc)
        if not aff:
            gate_counts["drop:industry_no_affirm"] = \
                gate_counts.get("drop:industry_no_affirm", 0) + 1
            continue
        lvl = industrial_level_tag(title, desc)
        if lvl in ("licensed_professional", "pure_labor",
                   "retail_service"):
            gate_counts[f"drop:level_{lvl}"] = \
                gate_counts.get(f"drop:level_{lvl}", 0) + 1
            continue
        kept.append({
            "title": title, "company": company, "loc": loc_s,
            "url": r.get("url") or "", "posted": (r.get("posted")
                                                  or "")[:10],
            "desc": desc[:2000], "source": src,
            "region": reg, "affirmTier": aff, "level": lvl,
            "titleMatchedQuery": bool(r.get("query")),
        })
        gate_counts[f"kept:{lvl}"] = gate_counts.get(f"kept:{lvl}", 0) + 1
        by_source_kept[src] = by_source_kept.get(src, 0) + 1

    # head-lining is TIER-GATED (the lane-1 FP lesson): desc-tier
    # affirms ("SaaS serving construction verticals" / the metaphorical
    # "isn't just plumbing") are KEPT as review evidence but never
    # head-lined — the user's cut is roles AT the businesses, and only
    # the company∨title tiers carry that evidence.
    head = [k for k in kept if k["level"] == "corp_office"
            and k["region"] in ("bay_area", "remote")
            and k["affirmTier"] in ("company", "title")]
    head.sort(key=lambda x: (x["region"] != "bay_area",
                             x["level"], x["company"]))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = OUT_DIR / f"{industry_slug}.json"
    out_json.write_text(json.dumps({
        "ran_at": env["ran_at"], "industry": industry_slug,
        "envelope": env, "gates": gate_counts,
        "kept": len(kept), "headlined": len(head),
        "jobs": kept,
    }, default=str, indent=1))

    lines = [
        f"# {ind['display']} — industry sweep report",
        "",
        f"- ran: {env['ran_at']} | raw rows: {len(rows)} | kept: "
        f"{len(kept)} | **head-lined (corp_office ∧ (bay_area ∨ "
        f"remote) ∧ company∨title affirm): {len(head)}**",
        f"- region: {loc} (adzuna geo: "
        f"{ind.get('adzuna_location') or loc})",
        "",
        "## Sources (the exhaustion measurement)",
        "",
    ]
    for name, v in env["sources"].items():
        lines.append(f"- {name}: {v}")
    lines += ["", "## Gate counters", ""]
    for k in sorted(gate_counts):
        lines.append(f"- {k}: {gate_counts[k]}")
    lines += ["", "## Head-lined roles", ""]
    for r in head[:60]:
        lines.append(
            f"- **{r['title']}** — {r['company']} | {r['loc']} | "
            f"{r['region']} | {r['source']} | posted {r['posted']}")
    (OUT_DIR / f"{industry_slug}.report.md").write_text("\n".join(lines))

    print(f"[industry_sweep] raw={len(rows)} kept={len(kept)} "
          f"head-lined={len(head)}")
    genv = os.environ.get("GITHUB_ENV")
    if genv:
        with open(genv, "a") as f:
            f.write(f"INDUSTRY_KEPT={len(kept)}\n"
                    f"INDUSTRY_HEAD={len(head)}\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--industry", default="home_services_ba")
    ap.add_argument("--skip", default="")
    args = ap.parse_args()
    return run(args.industry, args.skip)


if __name__ == "__main__":
    raise SystemExit(main())
