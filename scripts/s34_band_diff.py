#!/usr/bin/env python3
"""s34_band_diff.py — pre/post cell-diff gate for the S34 banding fix.

The S34-a review P1 finding 2: the rollout needs a SHIP-TIME artifact
proving strict additivity on the real CSVs, not just the analytic
argument. This script diffs the six h1b columns (#44-#49) of every
US row between two CSV states:

  OLD = the pre-fix CSV snapshot (git revision or directory)
  NEW = the post-export CSV directory

Invariants (exit non-zero on violation):
  1. CHANGED == 0: no row that had a band in OLD loses or mutates it
     in NEW (strict additivity — the shadowing fix can only ADD).
  2. LOST == 0: no banded row became empty.
  3. GAINED >= expected floor (default 280): the shadowed cohort
     (~287 at measurement) actually gained bands.
  4. Row identity: every (cid, reqId) in OLD exists in NEW with the
     same title. GONE rows fail ONLY when they were previously
     BANDED (ghost-dropped empty rows are the exporter's legitimate
     `ghosts_dropped` class — the §2.3 order assumes the export is
     dispatched immediately after mirror-sync, before any watch;
     S34-a2 P2-2). Title drift is reported (info only) — but a
     drifted title whose h1b cells re-band still counts as CHANGED
     (conservative FAIL; S34-a2 P2-4).

Compares ALL rows, not just US rows (stricter than the spec scope —
the 32 non-US banded rows are covered too; S34-a2 P2-1).

Usage:
  python3 scripts/s34_band_diff.py OLD_DIR NEW_DIR [--floor 280]
  OLD_DIR may be a git revision spec: 'git:HEAD~1' — then the old CSVs
  are materialized from that revision of the runtime repo.

Writes audit/s34_banding/band_diff.txt (the ship-time artifact).
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ORG = Path("/home/z/pipeline-mirror")
ARC = Path("/home/z/research")
OUT = ARC / "audit" / "s34_banding" / "band_diff.txt"

H1B_COLS = ["h1bFilings", "h1bWageP25", "h1bWageP50", "h1bWageP75",
            "h1bMatchBasis", "h1bMatchTitle"]


def load_csvs(root: Path) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for p in sorted(root.glob("*_us_fulltime.csv")):
        cid = p.name.removesuffix(".csv")
        with open(p, newline="", encoding="utf-8-sig") as f:
            out[cid] = {r["reqId"]: r for r in csv.DictReader(f)}
    return out


def materialize_git(spec: str) -> Path:
    """git:REV → a temp checkout of ingest/data/workday CSVs only."""
    rev = spec.removeprefix("git:")
    tmp = Path(tempfile.mkdtemp(prefix="s34old_"))
    res = subprocess.run(
        ["git", "-C", str(ORG), "archive", rev,
         "ingest/data/workday"],
        capture_output=True)
    if res.returncode != 0:
        sys.exit(f"bad git revision {rev!r}: "
                 f"{res.stderr.decode()[:200]}")
    subprocess.run(["tar", "-x", "-C", str(tmp)],
                   input=res.stdout, check=True)
    return tmp / "ingest" / "data" / "workday"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("old", help="old CSV dir or git:REV spec")
    ap.add_argument("new", help="new CSV dir")
    ap.add_argument("--floor", type=int, default=280,
                    help="minimum expected GAINED rows")
    args = ap.parse_args()

    old_root = (materialize_git(args.old) if args.old.startswith("git:")
                else Path(args.old))
    new_root = Path(args.new)

    old = load_csvs(old_root)
    new = load_csvs(new_root)

    lines: list[str] = []
    changed = lost = gained = 0
    gained_by: Counter = Counter()
    drift: list[str] = []
    gone: list[str] = []
    gone_banded = 0        # only BANDED gone rows fail the gate

    for cid in sorted(old):
        if cid not in new:
            gone.append(cid)
            gone_banded += sum(
                1 for r in old[cid].values()
                if (r.get("h1bFilings") or "").strip() not in ("", "0"))
            continue
        for rid, orow in old[cid].items():
            nrow = new[cid].get(rid)
            if nrow is None:
                gone.append(f"{cid}/{rid}")
                if (orow.get("h1bFilings") or "").strip() \
                        not in ("", "0"):
                    gone_banded += 1
                continue
            if (orow.get("title") or "") != (nrow.get("title") or ""):
                drift.append(f"{cid}/{rid}: "
                             f"{orow.get('title')!r} → "
                             f"{nrow.get('title')!r}")
            o_band = (orow.get("h1bFilings") or "").strip() not in ("", "0")
            n_band = (nrow.get("h1bFilings") or "").strip() not in ("", "0")
            if o_band:
                if not n_band:
                    lost += 1
                    lines.append(f"LOST   {cid}/{rid}")
                elif any((orow.get(c) or "") != (nrow.get(c) or "")
                         for c in H1B_COLS):
                    changed += 1
                    lines.append(
                        f"CHANGED {cid}/{rid}: "
                        + ", ".join(
                            f"{c}={orow.get(c)!r}→{nrow.get(c)!r}"
                            for c in H1B_COLS
                            if (orow.get(c) or "")
                            != (nrow.get(c) or "")))
            elif n_band:
                gained += 1
                gained_by[cid] += 1

    lines.append(f"banded rows in OLD: "
                 f"{sum(1 for c in old.values() for r in c.values() if (r.get('h1bFilings') or '').strip() not in ('', '0'))}")
    lines.append(f"CHANGED (band mutated): {changed}  [must be 0]")
    lines.append(f"LOST   (band removed):  {lost}   [must be 0]")
    lines.append(f"GAINED (empty→band):    {gained}  "
                 f"[>= {args.floor}]")
    lines.append(f"gained by company: {dict(gained_by.most_common())}")
    lines.append(f"title drift rows (info only): {len(drift)}")
    lines.extend(f"  {d}" for d in drift[:10])
    if gone:
        lines.append(f"rows gone in NEW: {len(gone)} "
                     f"({gone_banded} previously-banded — "
                     f"empty-row ghosts are the exporter's legitimate "
                     f"ghosts_dropped class)")
        lines.extend(f"  {g}" for g in gone[:10])

    verdict = (changed == 0 and lost == 0 and gained >= args.floor
               and gone_banded == 0)
    lines.append("")
    lines.append(f"VERDICT: {'PASS' if verdict else 'FAIL'}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[-12:]))
    print(f"artifact: {OUT}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
