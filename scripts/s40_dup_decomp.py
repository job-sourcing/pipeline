#!/usr/bin/env python3
"""S40 residual-dup re-decomposition (F12(c)).

The S38 decomposition (1,144 residual on 2,937 rows): 2.4% variant +
30% same-employer + 67% different-employer. The corpus doubled
(5,026 rows, residual_dup_est=2,172) — mostly via careerjet recovery +
the seed union. Re-decompose to decide whether a NEW merge rule is
warranted or the residual is (still) mostly legitimate
different-employer same-title jobs.

Buckets within (title_core, city) collisions:
  A. same-employer (companyKey equal or variant-mapped):
     A1. exact-dup signal (same rid-family / same url) — SHOULD have
         merged; if >0, a merge-rule hole (P0-class).
     A2. same-employer multi-req (different dates/urls) — the S38-30%
         class: legit (multiple requisitions / reposts >7d) unless
         ±7d same-source (which the res7 merge should have caught).
     A3. cross-source same-employer ±3d — should have merged under
         the F1(d) rule; if >0 at same granularity, a hole.
  B. different-employer (legit same-title-same-city jobs).
  C. companyKey-variant collisions the 49-entry map misses.
"""
import json
import re
import sys
from collections import defaultdict

sys.path.insert(0, "ingest")
from jobsearch.rolefamilies import title_core  # noqa: E402
from jobsearch import company_seeds  # noqa: F401,E402  (variant map lives in sweep)

# companyKey variant map — mirrors scripts/role_sweep.py's _company_key
# behavior by importing the module it lives in.
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("role_sweep_mod", "scripts/role_sweep.py")
_rs = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_rs) if hasattr(_spec.loader, "exec_module") else None

VARIANT_MAP = getattr(_rs, "COMPANY_VARIANT_MAP", None) or {}
_company_key = _rs._company_key


def main() -> None:
    d = json.load(open("ingest/data/ui/roles-pm.json"))
    jobs = d["jobs"]

    # collision groups by the SAME pair heuristic the sweep's estimator uses
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for j in jobs:
        pair = (title_core(j["title"] or ""),
                (j.get("loc") or "").split(",")[0].strip().lower())
        groups[pair].append(j)

    residual_rows = 0
    bucket = defaultdict(int)
    examples = defaultdict(list)

    for pair, rows in groups.items():
        if len(rows) < 2:
            continue
        residual_rows += len(rows) - 1
        # normalize employer identity
        by_key: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            ck = _company_key(r.get("company") or "", {})
            # fold known variants
            ck = VARIANT_MAP.get(ck, ck)
            by_key[ck].append(r)
        if len(by_key) == 1:
            # same employer, same title_core, same city
            empl = list(by_key.values())[0]
            srcs = {r["source"] for r in empl}
            urls = {r.get("url") for r in empl}
            if len(srcs) == 1 and len(urls) < len(empl):
                bucket["A1_same_src_url_overlap"] += 1
                examples["A1"].append(pair)
            elif len(srcs) == 1:
                bucket["A2_same_employer_multi_req"] += 1
                examples["A2"].append((pair, len(empl)))
            else:
                bucket["A3_cross_source_same_employer"] += 1
                examples["A3"].append((pair, sorted(srcs), len(empl)))
        else:
            bucket["B_diff_employer"] += 1
            examples["B"].append((pair, list(by_key)[:4]))

    print(f"rows: {len(jobs)}  residual_dup_est (sweep counter): "
          f"{d['envelope']['counters'].get('residual_dup_est')}")
    print(f"collision pairs: {sum(1 for g in groups.values() if len(g)>1)}"
          f"  residual rows (sum len-1): {residual_rows}")
    print("\n--- buckets (per collision pair) ---")
    for k in sorted(bucket):
        print(f"{bucket[k]:5d}  {k}")

    print("\n--- A1 url-overlap examples (P0-class holes if any) ---")
    for e in examples["A1"][:10]:
        print("   ", e)
    print("\n--- A3 cross-source same-employer examples ---")
    for e in examples["A3"][:12]:
        print("   ", e)
    print("\n--- A2 same-employer multi-req examples (top sizes) ---")
    for e in sorted(examples["A2"], key=lambda x: -x[1])[:10]:
        print("   ", e)
    print("\n--- B different-employer sample ---")
    for e in examples["B"][:8]:
        print("   ", e)


if __name__ == "__main__":
    main()
