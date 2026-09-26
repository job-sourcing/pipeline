#!/usr/bin/env python3
"""s19_refresh.py — the CONFIG-DRIVEN churn-refresh driver (S19).

Reads ingest/data/board_watch/config.json (the SAME registry the watch
uses: board / company / li_variants / slice_locations / time_type /
country_client) and runs the FULL board_dump phase chain for any label
— no per-company hard-coding, unlike the s16/s17/s18 chain scripts
(their case-blocks were the bootstrapping era; this is the generalized
instrument for every future refresh wave).

Usage:
  python3 scripts/s19_refresh.py --labels byd,jd            # subset
  python3 scripts/s19_refresh.py --churned                  # state vs CSV drift set
  python3 scripts/s19_refresh.py --labels nvidia --from corroborate
  python3 scripts/s19_refresh.py --labels nvidia --phase finish   # single phase

Phase order (netflix-class boards get countryfilter after corroborate):
  list -> details -> tagfacets -> questionnaires -> index(+li-reindex)
  -> titlesearch -> corroborate -> [countryfilter] -> finish -> invariants
"""
from __future__ import annotations

import argparse
import csv as _csv
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CFG = REPO / "ingest" / "data" / "board_watch" / "config.json"
DATA = REPO / "ingest" / "data" / "workday"

ORDER = ["list", "details", "tagfacets", "questionnaires", "index",
         "titlesearch", "corroborate", "countryfilter", "finish"]


def load_watches() -> dict:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    return {w["label"].removesuffix("_us_fulltime"): w
            for w in cfg.get("watches", [])}


def churned_labels() -> list[str]:
    """Labels whose watch-state active-row set != the shipped CSV set."""
    watch = REPO / "ingest" / "data" / "board_watch"
    out = []
    for lbl in load_watches():
        state = watch / f"{lbl}_us_fulltime.state.jsonl"
        cf = DATA / f"{lbl}_us_fulltime.csv"
        if not state.exists() or not cf.exists():
            continue
        ids = set()
        for line in state.read_text(encoding="utf-8").split("\n"):
            line = line.strip()
            if not line or line.startswith("***"):
                continue
            try:
                ids.add(json.loads(line)["reqId"])
            except Exception:
                continue
        with open(cf, encoding="utf-8-sig") as f:
            csv_ids = {r.get("reqId") for r in _csv.DictReader(f)}
        if ids != csv_ids:
            out.append(lbl)
    return sorted(out)


def run_phase(lbl: str, w: dict, phase: str) -> int:
    """phase is a chain step; 'index' is the corroborate-index pseudo-phase."""
    board = w["board"]
    company = w.get("company", "")
    variants = ",".join(w.get("li_variants") or [])
    slices = ";".join(w.get("slice_locations") or [])
    tt = w.get("time_type")
    cmd = [sys.executable, str(REPO / "scripts" / "board_dump.py"),
           "--board", board, "--company", company,
           "--label", f"{lbl}_us_fulltime",
           "--country", w.get("country", "United States"),
           "--time-type", tt if tt else ""]
    if variants:
        cmd += ["--li-variants", variants]
    if slices:
        cmd += ["--slice-locations", slices]
    if phase == "details":
        cmd += ["--details-batch", "200", "--phase", "details"]
    elif phase == "index":
        cmd += ["--phase", "corroborate", "--corroborate-index",
                "--index-mode", "partitioned", "--li-reindex"]
    else:
        cmd += ["--phase", phase]
    print(f"== [{lbl}] {phase} ==", flush=True)
    r = subprocess.run(cmd, cwd=REPO)
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="")
    ap.add_argument("--churned", action="store_true")
    ap.add_argument("--from", dest="from_phase", default="list")
    ap.add_argument("--phase", default="")  # single-phase mode
    args = ap.parse_args()

    watches = load_watches()
    if args.churned:
        labels = churned_labels()
    elif args.labels:
        labels = [l.strip() for l in args.labels.split(",") if l.strip()]
    else:
        labels = sorted(watches)
    print("labels:", labels, flush=True)

    failed = []
    for lbl in labels:
        w = watches[lbl]
        if args.phase:
            order = [args.phase]
        else:
            order = [p for p in ORDER if p != "countryfilter"]
            if args.from_phase != "list":
                keep = False
                trimmed = []
                for p in order:
                    if p == args.from_phase:
                        keep = True
                    if keep:
                        trimmed.append(p)
                order = trimmed
        for p in order:
            rc = run_phase(lbl, w, p)
            if rc != 0:
                print(f"!! [{lbl}] phase {p} rc={rc}", flush=True)
                failed.append((lbl, p))
                break
            if p == "corroborate" and "finish" in order:
                # netflix-class gate, read FRESH (a re-list re-marks the
                # country_filter_pending flag — the pre-chain status lies).
                # details must be complete before countryfilter runs.
                try:
                    ls = json.loads(
                        (DATA / f"{lbl}_us_fulltime.list.status")
                        .read_text(encoding="utf-8"))
                except FileNotFoundError:
                    ls = {}
                if ls.get("country_filter_pending"):
                    print(f"== [{lbl}] countryfilter (netflix-class)",
                          flush=True)
                    rc = run_phase(lbl, w, "countryfilter")
                    if rc != 0:
                        print(f"!! [{lbl}] countryfilter rc={rc}",
                              flush=True)
                        failed.append((lbl, "countryfilter"))
                        break
        r = subprocess.run([sys.executable, str(REPO / "scripts" /
                                                "s10_invariants.py"),
                            f"{lbl}_us_fulltime"], cwd=REPO,
                           capture_output=True, text=True)
        green = "ALL INVARIANTS GREEN" in (r.stdout + r.stderr)
        tail = [l for l in r.stdout.splitlines() if l][-1:] or [""]
        print(f"== [{lbl}] invariants: {'GREEN' if green else 'RED'}"
              f" ({tail[0]})", flush=True)
        if not green:
            failed.append((lbl, "invariants"))
    if failed:
        print("FAILED:", failed)
        return 1
    print("ALL CHAINS DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
