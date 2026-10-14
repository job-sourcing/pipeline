#!/usr/bin/env python3
"""s34_banding_factbase.py — the S34 title-hop design-round fact base.

Decomposes the 54 "LCA data exists, 0 banded rows" audit anomalies into
CAUSAL classes with concrete title evidence, and measures what each
candidate relaxation would unlock. Evidence-first: no design choices
here, only measured facts.

Class taxonomy (per unbanded CSV row):
  A no-candidates     — no pool S ⊆ posting tokens P (the lexical
                        disconnect = the TRUE title-hop class)
  B below-gate        — candidate pools exist, all < 3 filings
  C role-blocked      — candidates exist but (P − S) ∩ ROLE_BLOCK ≠ ∅
  D posting-side      — title too generic (< 2 tokens after norm)

For class A we also emit, per company, the closest pools by
Jaccard/token-overlap with raw LCA titles — the join the current
subset rule cannot see. That is the design surface for role-family
bucketing.

Output: audit/s34_banding/factbase.txt (+ factbase.json for the
design round's numeric tables).
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ORG = Path("/home/z/pipeline-mirror")
ARC = Path("/home/z/research")
OUT_DIR = ARC / "audit" / "s34_banding"
OUT_TXT = OUT_DIR / "factbase.txt"
OUT_JSON = OUT_DIR / "factbase.json"

sys.path.insert(0, str(ARC / "scripts"))
from board_dump import (  # noqa: E402
    _annualize_wage, _title_tokens, _h1b_house_title,
    _H1B_LEVEL_TOKENS, _H1B_ROLE_BLOCK, _load_h1b_bands,
    _derive_h1b_columns, _norm_join_title,
)

rows_out: list[str] = []
facts: dict = {"companies": {}, "totals": {}, "relaxations": {}}


def emit(s: str = "") -> None:
    rows_out.append(s)
    print(s)


# ── 1. company-level anomaly scan (the audit's §7 basis, extended) ──────
csvs = {p.name.removesuffix(".csv"): p for p in
        (ORG / "ingest/data/workday").glob("*_us_fulltime.csv")}
lca_files = {p.name.removesuffix(".h1b_lca.jsonl"): p for p in
             (ORG / "ingest/data/workday").glob("*.h1b_lca.jsonl")}

emit("=" * 72)
emit("S34 BANDING FACT BASE (title-hop decomposition)")
emit("=" * 72)
emit(f"CSVs: {len(csvs)}; LCA extracts: {len(lca_files)}; "
     f"companies with both: {len(set(csvs) & set(lca_files))}")

anomalies = []
partial = []
healthy = []
for cid in sorted(set(csvs) & set(lca_files)):
    with open(csvs[cid], newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    us_rows = [r for r in rows if (r.get("country") or "").strip()
               .lower().startswith("united states")]
    banded = [r for r in us_rows
              if (r.get("h1bFilings") or "").strip() not in ("", "0")]
    if not us_rows:
        continue
    if lca_files and not banded:
        anomalies.append(cid)
    elif banded and len(banded) < len(us_rows):
        partial.append((cid, len(banded), len(us_rows)))
    elif banded:
        healthy.append(cid)

emit(f"0-banded companies w/ US rows + LCA: {len(anomalies)}")
emit(f"partially banded: {len(partial)}")
emit(f"fully banded: {len(healthy)}")

# ── 2. per-row causal decomposition on the anomaly set ──────────────────
emit("")
emit("=" * 72)
emit("PER-ROW CAUSAL DECOMPOSITION (anomaly companies)")
emit("=" * 72)

class_counts: Counter[str] = Counter()
company_detail: dict[str, dict] = {}

for cid in anomalies:
    pools, _recs = _load_h1b_bands(
        csvs[cid].with_suffix(""), company="")
    # company name for house-title transforms
    with open(csvs[cid], newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    us_rows = [r for r in rows if (r.get("country") or "").strip()
               .lower().startswith("united states")]
    det = {"rows": len(us_rows), "pools": len(pools),
           "pool_sizes": sorted(
               (len(p["all"]) for p in pools.values()), reverse=True)[:8],
           "A": [], "B": [], "C": [], "D": []}
    for r in us_rows:
        title = r.get("title") or ""
        p_toks = _title_tokens(title)
        cur = _derive_h1b_columns(
            title, r.get("primaryLocation") or "", pools)
        if cur.get("h1bFilings"):
            continue  # actually banded (shouldn't happen; sanity)
        if len(p_toks - _H1B_LEVEL_TOKENS) < 2:
            det["D"].append(title)
            class_counts["D_posting_generic"] += 1
            continue
        cands = [s for s in pools
                 if len(s) >= 2 and s <= p_toks]
        blocked = [s for s in cands
                   if (p_toks - s) & _H1B_ROLE_BLOCK]
        unblocked = [s for s in cands
                     if not (p_toks - s) & _H1B_ROLE_BLOCK]
        if unblocked:
            if all(len(pools[s]["all"]) < 3 for s in unblocked):
                det["B"].append(
                    (title, [( " ".join(sorted(s)), len(pools[s]["all"]))
                             for s in unblocked[:3]]))
                class_counts["B_below_gate"] += 1
            else:
                # gate-passing candidates exist but state-hop missed?
                # (state pool <3 AND all-pool <3 for that best) — recheck
                best = max(unblocked, key=lambda s: (
                    len(s - _H1B_LEVEL_TOKENS), len(s),
                    len(pools[s]["all"]), " ".join(sorted(s))))
                det["B"].append(
                    (title, [(" ".join(sorted(best)),
                              len(pools[best]["all"]))]))
                class_counts["B_below_gate"] += 1
        elif blocked:
            det["C"].append(
                (title, [" ".join(sorted(s)) for s in blocked[:3]]))
            class_counts["C_role_blocked"] += 1
        else:
            det["A"].append(title)
            class_counts["A_no_candidates"] += 1
    company_detail[cid] = det

emit(f"per-row classes across {len(anomalies)} anomaly companies:")
for k in sorted(class_counts):
    emit(f"  {k:22} {class_counts[k]:5}")
facts["totals"]["class_counts"] = dict(class_counts)
facts["totals"]["anomaly_companies"] = anomalies

# ── 3. class-A evidence: closest pools by overlap ───────────────────────
emit("")
emit("=" * 72)
emit("CLASS A DETAIL — the true title-hop surface (top examples)")
emit("=" * 72)

def overlap(a: frozenset, b: frozenset) -> tuple[int, int, int]:
    inter = len(a & b)
    return inter, len(a - b), len(b - a)


for cid in anomalies:
    det = company_detail[cid]
    if not det["A"]:
        continue
    pools, _ = _load_h1b_bands(csvs[cid].with_suffix(""), company="")
    emit("")
    emit(f"  {cid} — pools={det['pools']} "
         f"(sizes {det['pool_sizes']}), "
         f"A-rows={len(det['A'])}")
    for title in det["A"][:6]:
        p_toks = _title_tokens(title)
        scored = []
        for s, p in pools.items():
            inter, a_only, b_only = overlap(p_toks, s)
            if inter == 0:
                continue
            # rank: shared domain tokens, then fewer posting-side
            # leftovers (the subset rule's blind spot is b_only>0)
            scored.append((len((s & p_toks) - _H1B_LEVEL_TOKENS),
                           -b_only, len(p["all"]),
                           " ".join(sorted(s)), p["title"]))
        scored.sort(reverse=True)
        emit(f"    P: {title!r}  tokens={sorted(p_toks)}")
        for sc in scored[:3]:
            emit(f"      pool {sc[3]!r} ({sc[2]} filings, "
                 f"raw={sc[4]!r}) shared={sc[0]} extra={-sc[1]}")
    facts["companies"][cid] = {
        "A": det["A"], "pool_count": det["pools"]}

# ── 4. relaxation measurements on class A ───────────────────────────────
emit("")
emit("=" * 72)
emit("RELAXATION MEASUREMENTS (what each rule change would band)")
emit("=" * 72)

# R1: superset/subset swap — pool S SUPERSET of P (posting is MORE
# generic than pool; e.g. P={engineer} excluded by <2 but P={ml,
# engineer} vs S={engineer, machine, learning})
emit("R1 subset-swap: pools S ⊇ P (|P|>=2):")
r1_count = 0
for cid in anomalies:
    pools, _ = _load_h1b_bands(csvs[cid].with_suffix(""), company="")
    det = company_detail.get(cid, {})
    for title in det.get("A", []):
        p_toks = _title_tokens(title)
        if len(p_toks) < 2:
            continue
        cands = [s for s in pools if s >= p_toks]
        ok = [s for s in cands
              if len(pools[s]["all"]) >= 3
              and not ((s - p_toks) & _H1B_ROLE_BLOCK)]
        if ok:
            r1_count += 1
emit(f"  rows banded by R1: {r1_count}")

# R2: single-domain-token pools (drop the |S|>=2 requirement when the
# one token is a STRONG domain word — measured via a fixed lexicon)
STRONG = {"software", "engineer", "engineering", "data", "scientist",
          "machine", "learning", "algorithm", "hardware", "electrical",
          "mechanical", "salesforce", "embedded", "firmware"}
emit("R2 one-token pools with strong domain word (|S|==1, S ⊆ P):")
r2_count = 0
for cid in anomalies:
    pools, _ = _load_h1b_bands(csvs[cid].with_suffix(""), company="")
    det = company_detail.get(cid, {})
    for title in det.get("A", []):
        p_toks = _title_tokens(title)
        cands = [s for s in pools
                 if len(s) == 1 and s & p_toks
                 and (s & STRONG)
                 and len(pools[s]["all"]) >= 3]
        if cands:
            r2_count += 1
emit(f"  rows banded by R2: {r2_count}")

# R3: role-family bucketing preview — map BOTH sides into coarse
# families via keyword rules, band from the family pool when the
# families agree. THIS ONLY MEASURES JOIN RATE, not correctness.
FAMILY_RULES = {
    "swe": {"software", "engineer", "development", "developer",
            "swe", "embedded", "firmware", "fullstack", "backend",
            "frontend"},
    "data": {"data", "analyst", "scientist", "analytics",
             "business", "intelligence", "bi"},
    "ml": {"machine", "learning", "ml", "ai", "algorithm",
           "deep"},
    "hw": {"hardware", "electrical", "mechanical", "test",
           "equipment", "device", "optical", "automation"},
    "sales": {"sales", "account", "business", "development",
              "regional", "channel"},
    "ops": {"operations", "supply", "logistics", "manufacturing",
            "production", "quality"},
}
def families(toks: frozenset) -> set[str]:
    out = set()
    for fam, words in FAMILY_RULES.items():
        if toks & words:
            out.add(fam)
    return out

emit("R3 role-family join preview (keyword families, both sides):")
r3 = {"rows": 0, "companies": Counter(),
      "examples": []}
for cid in anomalies:
    pools, _ = _load_h1b_bands(csvs[cid].with_suffix(""), company="")
    # family pools: {family: [wages]} over pool-eligible filings
    fpools: dict[str, list] = defaultdict(list)
    for s, p in pools.items():
        for fam in families(s):
            fpools[fam].extend(p["all"])
    det = company_detail.get(cid, {})
    for title in det.get("A", []):
        p_toks = _title_tokens(title)
        pf = families(p_toks)
        ok = [fam for fam in pf
              if len(fpools.get(fam, [])) >= 3]
        if ok:
            r3["rows"] += 1
            r3["companies"][cid] += 1
            if len(r3["examples"]) < 25:
                r3["examples"].append(
                    (cid, title, sorted(pf),
                     {f: len(fpools[f]) for f in ok}))
emit(f"  rows banded by R3: {r3['rows']} "
     f"(companies: {len(r3['companies'])})")
for cid, title, pf, ok in r3["examples"][:15]:
    emit(f"    {cid}: {title!r} fam={pf} ok={ok}")

# R4: pool-side leftover cap — allow S ⊆ P ∪ X where X ⊆ pool-only
# tokens with |X| <= 1 (the comma-phrase case: ONE extra domain word)
emit("R4 near-subset (pool has <=1 token not in posting):")
r4_count = 0
r4_examples = []
for cid in anomalies:
    pools, _ = _load_h1b_bands(csvs[cid].with_suffix(""), company="")
    det = company_detail.get(cid, {})
    for title in det.get("A", []):
        p_toks = _title_tokens(title)
        cands = []
        for s in pools:
            extra = s - p_toks
            if 0 < len(extra) <= 1 and len(pools[s]["all"]) >= 3 \
                    and not (extra & _H1B_ROLE_BLOCK) \
                    and (s & p_toks) \
                    and len(s - _H1B_LEVEL_TOKENS) >= 2:
                cands.append((s, extra))
        if cands:
            r4_count += 1
            if len(r4_examples) < 15:
                r4_examples.append(
                    (cid, title, [" ".join(sorted(s)) + f" +{sorted(extra)}"
                                  for s, extra in cands[:2]]))
emit(f"  rows banded by R4: {r4_count}")
for cid, title, cands in r4_examples[:10]:
    emit(f"    {cid}: {title!r} → {cands}")

facts["relaxations"] = {
    "R1_subset_swap": r1_count,
    "R2_one_token_strong": r2_count,
    "R3_role_family": {"rows": r3["rows"],
                       "companies": dict(r3["companies"])},
    "R4_near_subset_1": r4_count,
}

# R5: posting-side title scrub — strip company-name tokens and
# US/geo junk from POSTING titles, then re-run the EXACT current
# matcher unchanged. This is input hygiene, not a join relaxation
# (the antintl "Ant International-U.S. Software Engineer-Antom"
# class; company names from the watch config).
emit("R5 posting-title scrub (strip company/geo junk, matcher "
     "UNCHANGED):")
wcfg = json.loads((ARC / "ingest/data/board_watch/config.json")
                   .read_text())["watches"]
co_by_label = {w.get("label"): w.get("company", "") for w in wcfg}
GEO_JUNK = {"us", "usa", "u", "s", "united", "states", "america",
            "americas", "emea", "apac", "eu", "na", "tech",
            "international", "inc", "llc", "corp", "corporation",
            "company", "group", "ltd", "limited", "co"}
r5 = {"count": 0, "examples": [], "companies": Counter()}
for cid in anomalies + [c for c, _b, _u in partial]:
    pools, _ = _load_h1b_bands(csvs[cid].with_suffix(""), company="")
    co = (co_by_label.get(cid) or "")
    co_toks = {t for t in _norm_join_title(co).split()
               if len(t) > 2} if co else set()
    with open(csvs[cid], newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    us_rows = [r for r in rows if (r.get("country") or "").strip()
               .lower().startswith("united states")]
    for r in us_rows:
        if (r.get("h1bFilings") or "").strip() not in ("", "0"):
            continue
        title = r.get("title") or ""
        scrubbed = " ".join(
            t for t in _norm_join_title(title).split()
            if t not in co_toks and t not in GEO_JUNK)
        if scrubbed == _norm_join_title(title) or not scrubbed:
            continue
        p_toks = _title_tokens(scrubbed)
        if len(p_toks - _H1B_LEVEL_TOKENS) < 2:
            continue
        cur = _derive_h1b_columns(
            scrubbed, r.get("primaryLocation") or "", pools)
        if cur.get("h1bFilings"):
            r5["count"] += 1
            r5["companies"][cid] += 1
            if len(r5["examples"]) < 20:
                r5["examples"].append(
                    (cid, title, scrubbed, cur["h1bMatchBasis"],
                     cur["h1bMatchTitle"]))
emit(f"  rows banded by R5: {r5['count']} "
     f"(companies: {dict(r5['companies'])})")
for cid, t, s, basis, mt in r5["examples"][:12]:
    emit(f"    {cid}: {t!r}")
    emit(f"        → {s!r} basis={basis} pool={mt!r}")

facts["relaxations"]["R5_title_scrub"] = {
    "rows": r5["count"], "companies": dict(r5["companies"])}

# ── 6. partial-band residual (companies with SOME banded rows) ─────────
emit("")
emit("=" * 72)
emit("PARTIAL-BAND RESIDUAL (companies with some but not all banded)")
emit("=" * 72)
partial_resid = 0
for cid, nb, nu in partial[:40]:
    partial_resid += nu - nb
emit(f"companies: {len(partial)}; unbanded residual rows: "
     f"{partial_resid}")
for cid, nb, nu in partial[:25]:
    emit(f"  {cid}: {nb}/{nu} banded")
facts["totals"]["partial"] = [(c, b, u) for c, b, u in partial]

OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_TXT.write_text("\n".join(rows_out) + "\n", encoding="utf-8")
OUT_JSON.write_text(json.dumps(facts, indent=2, sort_keys=True,
                                default=str), encoding="utf-8")
emit("")
emit(f"written: {OUT_TXT} + {OUT_JSON}")
