#!/usr/bin/env python3
"""s34_shadow_quant.py — quantify the candidate-shadowing bug.

The bug: _derive_h1b_columns picked `best` by domain-conditioning rank
BEFORE the >=3-filings gate. A hyper-specific 1-filing pool (e.g.
{research, scientist, tech}) shadows a well-populated general pool
(e.g. {research, scientist} x84), then dies at the gate → the row
ships honest-empty even though a banded population exists.

PRE-FIX measurement (2026-10-13, commit f4246945^): 285 shadowed rows
across 11 companies (top: bytedance 132, nvidia 99, netflix 29); with
the watch-config company passed (S34-a review finding 3): ~287/12
(+OpenAI 2, nvidia +1 drift).

POST-FIX INVARIANT: measured against CSV state — after a re-band
export with the S34 passable gate, this must report 0 (any non-zero
= the gate regressed or the CSVs are stale). Run after any
board_dump.py h1b change + export.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

ORG = Path("/home/z/pipeline-mirror")
ARC = Path("/home/z/research")

sys.path.insert(0, str(ARC / "scripts"))
from board_dump import (  # noqa: E402
    _title_tokens, _load_h1b_bands, _derive_h1b_columns,
    _H1B_LEVEL_TOKENS, _H1B_ROLE_BLOCK,
)

csvs = {p.name.removesuffix(".csv"): p for p in
        (ORG / "ingest/data/workday").glob("*_us_fulltime.csv")}
lca_files = {p.name.removesuffix(".h1b_lca.jsonl") for p in
             (ORG / "ingest/data/workday").glob("*.h1b_lca.jsonl")}

# S34-a review finding 3: pass the WATCH-CONFIG company so house-title
# transforms (e.g. OpenAI's MTS wrapper) apply — company="" disabled
# them and undercounted by OpenAI's 2 shadowed rows.
wcfg = json.loads((ARC / "ingest/data/board_watch/config.json")
                   .read_text())["watches"]
co_by_label = {w.get("label"): w.get("company", "") for w in wcfg}

total_shadowed = 0
per_company: Counter = Counter()
examples = []
gate_pass_present = 0  # rows where SOME candidate passes the gate

for cid in sorted(set(csvs) & lca_files):
    pools, _ = _load_h1b_bands(csvs[cid].with_suffix(""),
                               company=co_by_label.get(cid, ""))
    with open(csvs[cid], newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    us = [r for r in rows if (r.get("country") or "").strip()
          .lower().startswith("united states")]
    for r in us:
        if (r.get("h1bFilings") or "").strip() not in ("", "0"):
            continue
        title = r.get("title") or ""
        p_toks = _title_tokens(title)
        if not p_toks or not pools:
            continue
        cands = [s for s in pools
                 if len(s) >= 2 and s <= p_toks
                 and not ((p_toks - s) & _H1B_ROLE_BLOCK)]
        if not cands:
            continue
        # which candidates could actually band (state >=3 or all >=3)?
        loc = r.get("primaryLocation") or ""
        passable = [s for s in cands if len(pools[s]["all"]) >= 3]
        if passable:
            gate_pass_present += 1
        # current best-by-rank (the buggy order)
        best = max(cands, key=lambda s: (
            len(s - _H1B_LEVEL_TOKENS), len(s),
            len(pools[s]["all"]), " ".join(sorted(s))))
        best_passes = len(pools[best]["all"]) >= 3
        if passable and not best_passes:
            total_shadowed += 1
            per_company[cid] += 1
            if len(examples) < 30:
                fixed = _derive_h1b_columns(
                    title, loc,
                    {s: pools[s] for s in passable} | {
                        k: v for k, v in pools.items()
                        if len(v["all"]) >= 3})
                examples.append(
                    (cid, title, sorted(best),
                     len(pools[best]["all"]),
                     sorted(max(passable, key=lambda s: (
                         len(s - _H1B_LEVEL_TOKENS), len(s),
                         len(pools[s]["all"]), " ".join(sorted(s))))),
                     len(pools[max(passable, key=lambda s: (
                         len(s - _H1B_LEVEL_TOKENS), len(s),
                         len(pools[s]["all"]), " ".join(sorted(s))))]["all"])))

print(f"rows with a gate-passing candidate but empty band: "
      f"{gate_pass_present}")
print(f"  of which shadowed by a gate-failing 'best': "
      f"{total_shadowed}")
print(f"  companies: {dict(per_company.most_common(20))}")
print()
for cid, t, bad, badn, good, goodn in examples[:20]:
    print(f"  {cid}: {t[:64]!r}")
    print(f"     buggy best {bad} ({badn} filings) "
          f"vs passable {good} ({goodn} filings)")
