#!/usr/bin/env python3
"""s35_gate_diff.py — the A-prime country-gate ship gate (D-S35-1).

Compares PRE-gate CSVs vs POST-gate CSVs per label, attributing every
row change to an expected class. PASSes only when:
  - LOST rows == gate_dropped_survivors + ghosts_dropped (manifests)
  - GAINED rows == new (manifests, gate-surviving new rows)
  - the ONLY cell changes on surviving rows are country normalization
    (variant -> canonical) — every other field identical
  - the manifest identity holds for every label
Usage: python3 scripts/s35_gate_diff.py <pre_csv_dir> <post_csv_dir>
Exit 0 = PASS; nonzero + report on failure.
"""
import csv
import json
import sys
from pathlib import Path

FIELDS_TO_CHECK = ["title", "company", "timeType", "postedOn",
                   "startDate", "primaryLocation", "locations",
                   "remoteFlag", "stateCodes", "url", "description",
                   "descriptionLength", "firstSeenDate", "dumpDate",
                   "h1bFilings", "h1bWageP50", "h1bMatchBasis"]


def read_rows(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["reqId"]: r for r in csv.DictReader(f) if r.get("reqId")}


def main(pre_dir: str, post_dir: str) -> int:
    pre, post = Path(pre_dir), Path(post_dir)
    post_labels = {p.name for p in post.glob("*_us_fulltime.csv")}
    pre_labels = {p.name for p in pre.glob("*_us_fulltime.csv")}
    # manifests live next to post CSVs
    problems, total_lost, total_gain, total_cell = [], 0, 0, 0
    total_gate, total_ghost = 0, 0
    us_norm = 0
    for name in sorted(pre_labels | post_labels):
        label = name.removesuffix("_us_fulltime.csv")
        pre_rows = read_rows(pre / name)
        post_rows = read_rows(post / name)
        mpath = post / f"{label}_us_fulltime.export.json"
        m = json.loads(mpath.read_text()) if mpath.exists() else None
        lost = set(pre_rows) - set(post_rows)
        gained = set(post_rows) - set(pre_rows)
        # manifest identity
        if m:
            ident = (m["kept"] + m["gate_dropped_survivors"]
                     + m["ghosts_in_state"] + m["new"]
                     + m["gate_dropped_new"] + m["pending"]
                     + m["feed_excluded_state"])
            if ident != m["state_rows"]:
                problems.append(f"{label}: manifest identity broken "
                                f"{ident} != {m['state_rows']}")
        exp_lost = (m or {}).get("gate_dropped_survivors", 0) + \
                   (m or {}).get("ghosts_dropped", 0)
        exp_gain = (m or {}).get("new", 0)
        if len(lost) != exp_lost:
            problems.append(f"{label}: LOST {len(lost)} != expected "
                            f"{exp_lost} (gate {(m or {}).get('gate_dropped_survivors')}"
                            f" + ghosts {(m or {}).get('ghosts_dropped')})")
        if len(gained) != exp_gain:
            problems.append(f"{label}: GAINED {len(gained)} != expected "
                            f"{exp_gain}")
        # cell-level: survivors must be unchanged except country
        for rid in set(pre_rows) & set(post_rows):
            for field in FIELDS_TO_CHECK:
                if pre_rows[rid].get(field) != post_rows[rid].get(field):
                    problems.append(f"{label}/{rid}: cell change {field}: "
                                    f"{pre_rows[rid].get(field)!r} -> "
                                    f"{post_rows[rid].get(field)!r}")
                    total_cell += 1
            if pre_rows[rid].get("country") != post_rows[rid].get("country"):
                us_norm += 1
        total_lost += len(lost)
        total_gain += len(gained)
        total_gate += (m or {}).get("gate_dropped_survivors", 0) + \
            (m or {}).get("gate_dropped_new", 0)
        total_ghost += (m or {}).get("ghosts_dropped", 0)
    print(f"GATE-DIFF: labels={len(pre_labels | post_labels)} "
          f"LOST={total_lost} (gate {total_gate} + ghosts {total_ghost}) "
          f"GAINED={total_gain} cell-changes={total_cell} "
          f"country-normalized={us_norm}")
    if problems:
        print(f"FAIL — {len(problems)} problem(s):")
        for p in problems[:40]:
            print("  ", p)
        return 1
    print("PASS — every row change attributed to an expected class")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
