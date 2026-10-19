#!/usr/bin/env python3
"""S40 offline regrade — apply the fold (new seeds/rules) to the
5,026-row production bundle WITHOUT a fresh sweep.

DESIGN-S40 v2 §4 + review A4 (read-modify-write, never regenerate):
  - roles-pm.json (bundle)   : pending rows re-graded; bucketed rows
                               leave; staffing recompute + agency
                               overlay (A3); id/sort semantics intact
  - pm_dropped.csv           : READ + APPEND the newly-bucketed rows
  - pm_inventory.csv         : RMW by (company_key,title,url) — drop
                               bucketed, refresh grade columns
  - pm_coverage.json         : carried VERBATIM (the cumulative ledger
                               is historical; the regrade adds no
                               first-seens/weeks)
  - pm_envelope.json         : RMW — process counters carried, only
                               grade-dependent fields updated +
                               regenerated_at/regenerated_by provenance
  - COVERAGE.md              : regenerated from the new bundle

Desc note (A4): the bundle desc is 4,000-truncated; the regrade
grades on that. Lane #6 grades on full desc — 338 long rows may
differ; the lane is the authority.
"""
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/z/jsr")
sys.path.insert(0, str(REPO / "ingest"))
sys.path.insert(0, str(REPO / "ingest/scripts"))

from jobsearch.rolefamilies import (  # noqa: E402
    is_staffing_company, sector_verdict)
from jobsearch import company_seeds as _seeds  # noqa: E402

UI = REPO / "ingest/data/ui/roles-pm.json"
ROLES = REPO / "ingest/data/roles"
DROPPED = ROLES / "pm_dropped.csv"
INVENTORY = ROLES / "pm_inventory.csv"
ENVELOPE = ROLES / "pm_envelope.json"
COVERAGE_LEDGER = ROLES / "pm_coverage.json"
COVERAGE_MD = REPO / "audit/s40/COVERAGE.md"

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

bundle = json.load(open(UI))
jobs = bundle["jobs"]
old_dropped = list(csv.DictReader(open(DROPPED)))
inv = list(csv.DictReader(open(INVENTORY)))
env = json.load(open(ENVELOPE))
inv_by_key = {(r.get("company_key"), r.get("title"), r.get("url")): r
              for r in inv}

# ── re-grade the pending rows ────────────────────────────────────────
kept, bucketed = [], []
regrade_stats = Counter()
for j in jobs:
    if j.get("sectorEvidence"):
        kept.append(j)                       # already evidence-graded
        continue
    ck = j.get("companyKey") or ""
    comp = j.get("company") or ""
    staffing = bool(is_staffing_company(comp)
                    or _seeds.staff_seed_hit(ck, comp))
    j["staffingEvidence"] = "agency" if staffing else ""
    act, sev, reason = sector_verdict(
        j.get("title") or "", comp, j.get("desc") or "", ck)
    regrade_stats[f"act:{act}"] += 1
    regrade_stats[f"why:{reason}"] += 1
    if act == "bucket":
        j["sectorEvidence"] = ""
        j["sectorReason"] = reason
        bucketed.append(j)
        continue
    if act == "pending" and staffing:
        j["sectorEvidence"] = "agency"
        j["sectorReason"] = "agency:repost"
        regrade_stats["agency_flips"] += 1
    else:
        j["sectorEvidence"] = sev
        j["sectorReason"] = reason
    kept.append(j)

print(f"regraded {sum(regrade_stats[k] for k in regrade_stats if k.startswith('act:'))} pending rows")
print(f"  keep={regrade_stats['act:keep']} pending={regrade_stats['act:pending']} bucket={regrade_stats['act:bucket']}")
print(f"  agency_flips={regrade_stats['agency_flips']}")

# ── rmw: pm_dropped.csv (append) ─────────────────────────────────────
drop_cols = list(old_dropped[0].keys()) if old_dropped else \
    ["title", "company", "company_key", "loc", "url", "posted",
     "family", "family_evidence", "seniority", "us_status",
     "us_evidence", "sector_evidence", "sector_reason",
     "staffing_evidence", "employer_anonymized", "source", "layer",
     "query", "rid", "description_source", "desc_len", "reason",
     "recoverable"]
new_drop_rows = []
for j in bucketed:
    irow = inv_by_key.get((j.get("companyKey"), j.get("title"),
                           j.get("url")), {})
    new_drop_rows.append({
        "title": j.get("title"), "company": j.get("company"),
        "company_key": j.get("companyKey"), "loc": j.get("loc"),
        "url": j.get("url"), "posted": j.get("postedOn"),
        "family": j.get("family"), "family_evidence": j.get("familyEvidence"),
        "seniority": j.get("seniority"), "us_status": j.get("usStatus"),
        "us_evidence": irow.get("us_evidence", ""),
        "sector_evidence": "", "sector_reason": j.get("sectorReason"),
        "staffing_evidence": j.get("staffingEvidence", ""),
        "employer_anonymized": j.get("employerAnonymized", False),
        "source": j.get("source"), "layer": j.get("layer"),
        "query": irow.get("query", "regrade"),
        "rid": j.get("id"),
        "description_source": irow.get("description_source", "regrade"),
        "desc_len": j.get("descLen", 0),
        "reason": j.get("sectorReason"), "recoverable": True,
    })
with open(DROPPED, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=drop_cols)
    w.writeheader()
    w.writerows(old_dropped)
    w.writerows(new_drop_rows)
print(f"pm_dropped.csv: {len(old_dropped)} + {len(new_drop_rows)} = {len(old_dropped) + len(new_drop_rows)}")

# ── rmw: pm_inventory.csv ────────────────────────────────────────────
bucket_ids = {j["id"] for j in bucketed}
kept_by_ktu = {(j.get("companyKey"), j.get("title"), j.get("url")): j
               for j in kept}
inv_out = []
for r in inv:
    k = (r.get("company_key"), r.get("title"), r.get("url"))
    j = kept_by_ktu.get(k)
    if j is None:      # bucketed in this regrade (or bundle-missing)
        continue
    r = dict(r)
    r["sector_evidence"] = j.get("sectorEvidence", "")
    r["sector_reason"] = j.get("sectorReason", "")
    r["staffing_evidence"] = j.get("staffingEvidence", "")
    inv_out.append(r)
with open(INVENTORY, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(inv[0].keys()))
    w.writeheader()
    w.writerows(inv_out)
print(f"pm_inventory.csv: {len(inv)} → {len(inv_out)}")

# ── rmw: envelope ────────────────────────────────────────────────────
env["rows_kept"] = len(kept)
env["rows_dropped"] = env.get("rows_dropped", 0) + len(new_drop_rows)
env.setdefault("counters", {})
env["counters"]["s40_regrade"] = {
    "at": NOW,
    "regraded_pending": int(sum(regrade_stats[k] for k in regrade_stats
                                if k.startswith("act:"))),
    "kept": int(regrade_stats["act:keep"]),
    "still_pending": int(regrade_stats["act:pending"]),
    "bucketed_nontech": int(regrade_stats["act:bucket"]),
    "agency_flips": int(regrade_stats["agency_flips"]),
    "jdblind": sum(v for k, v in regrade_stats.items()
                   if k.startswith("why:g3_deny:") and k.endswith("|jdblind")),
}
env["regenerated_at"] = NOW
env["regenerated_by"] = ("s40-fold-regrade (DESIGN-S40 v2 §4; offline — "
                         "lane #6 is the live authority)")
ENVELOPE.write_text(json.dumps(env, indent=1, default=str))

# ── bundle rewrite (sort semantics preserved) ────────────────────────
_sen_order = {"senior": 0, "director+": 1, "mid": 2, "junior": 3}
kept.sort(key=lambda x: (_sen_order.get(x.get("seniority"), 4),
                         x.get("postedOn") or ""))
bundle["jobs"] = kept
bundle["rows"] = len(kept)
bundle["snapshotDate"] = bundle.get("snapshotDate")
bundle["envelope"] = {k: v for k, v in env.items()
                      if k not in ("queries",)}
UI.write_text(json.dumps(bundle, default=str))

# ── COVERAGE.md ─────────────────────────────────────────────────────
src_count = Counter(j.get("source") for j in kept)
reason_count = Counter(j.get("sectorReason") for j in kept)
pending_after = sum(1 for j in kept if not j.get("sectorEvidence"))
lines = [
    "# S40 COVERAGE — the fold-regraded envelope",
    "",
    f"> Regenerated {NOW} by scripts/s40_regrade.py from the lane #5 "
    "bundle (ran_at "
    f"{env.get('ran_at')}, original rows_kept 5026). Offline fold — "
    "lane #6 validates the same grading on live fetches.",
    "",
    "## Headline",
    "",
    f"- rows_kept: 5,026 → **{len(kept)}** (nontech-bucketed "
    f"{len(new_drop_rows)} — every drop recorded in pm_dropped.csv, "
    "recoverable, and re-evaluated by lane #6 with restored JDs)",
    f"- ▲ pending: 2,556 → **{pending_after}** "
    f"({pending_after * 100 // max(len(kept), 1)}% of the envelope)",
    f"- grades now live: " + ", ".join(
        f"`{k}` {v}" for k, v in reason_count.most_common(14)),
    "",
    "## Per-source kept rows",
    "",
    "| source | kept |",
    "|---|---|",
]
for s, n in src_count.most_common():
    lines.append(f"| {s} | {n} |")
lines += ["", "## The ▲ residual queue", "",
          f"{pending_after} rows stay pending by design: 104 UNKNOWN "
          "(opaque keys), low-confidence triage verdicts (291 "
          "companies gated), and bank/payer review re-pends. The next "
          "iteration surface: JD re-fetch (Fix A restores seed JDs) + "
          "the 338 long-desc rows the lane grades on full text.", ""]
COVERAGE_MD.write_text("\n".join(lines))
print(f"COVERAGE.md → {COVERAGE_MD}")
print(f"\nRESULT: 5,026 → {len(kept)} kept | pending {pending_after} | "
      f"dropped +{len(new_drop_rows)}")
