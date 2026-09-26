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


def _details_remaining(lbl: str) -> int:
    """Rows the details phase still has to fetch (settled-view math:
    list rows minus settled minus 3-strike-capped) — mirrors
    phase_details' own todo computation."""
    list_path = DATA / f"{lbl}_us_fulltime.list.jsonl"
    det_path = DATA / f"{lbl}_us_fulltime.details.jsonl"
    if not list_path.exists() or not det_path.exists():
        return 0
    list_ids = set()
    for line in list_path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            list_ids.add(json.loads(line).get("reqId"))
        except Exception:
            continue
    settled: set = set()
    attempts: dict = {}
    for line in det_path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        rid = d.get("reqId")
        if d.get("info"):
            settled.add(rid)
        try:
            attempts[rid] = max(attempts.get(rid, 0),
                                int(d.get("attempts") or 0))
        except (TypeError, ValueError):
            pass
    todo = [r for r in list_ids
            if r not in settled and attempts.get(r, 0) < 3]
    return len(todo)


def _titlesearch_running(lbl: str) -> bool:
    """True while the titlesearch phase reports an unfinished run
    (done=false in its latest meta record — the phase's own contract)."""
    p = DATA / f"{lbl}_us_fulltime.title_search.jsonl"
    if not p.exists():
        return False
    last = ""
    for line in p.read_text(encoding="utf-8").split("\n")[::-1]:
        line = line.strip()
        if line and not line.startswith("***"):
            last = line
            break
    if not last:
        return False
    try:
        d = json.loads(last)
    except Exception:
        return False
    meta = d if "done" in d else {}
    return meta.get("done") is False


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
                "--index-mode", "partitioned", "--li-reindex",
                "--reprobe-no-card-days", "7"]
    elif phase == "finish":
        # SEV-1 review fix: finish FAILS (rc 1) when details are missing —
        # a partially-detailed board must never ship silently
        cmd += ["--require-details", "--phase", "finish"]
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
    # validate labels up front (a KeyError mid-loop after 3 companies
    # wastes the whole run — P3 review finding)
    unknown = [l for l in labels if l not in watches]
    if unknown:
        print(f"ERROR: unknown labels {unknown} "
              f"(known: {sorted(watches)})")
        return 2
    for lbl in labels:
        w = watches[lbl]
        if args.phase:
            order = [args.phase]
        else:
            order = [p for p in ORDER if p != "countryfilter"]
            if args.from_phase != "list":
                if args.from_phase == "countryfilter":
                    # the natural resume point after a failed netflix-class
                    # countryfilter: finish still needs to run (the fresh
                    # gate inside the loop re-runs countryfilter first)
                    order = ["corroborate", "countryfilter", "finish"]
                else:
                    keep = False
                    trimmed = []
                    for p in order:
                        if p == args.from_phase:
                            keep = True
                        if keep:
                            trimmed.append(p)
                    order = trimmed
                    if not order:
                        print(f"ERROR: --from {args.from_phase} matches "
                              f"no phase in the chain order")
                        return 2
        phase_failed = False
        for p in order:
            rc = run_phase(lbl, w, p)
            if rc != 0:
                print(f"!! [{lbl}] phase {p} rc={rc}", flush=True)
                failed.append((lbl, p))
                phase_failed = True
                break
            if p == "details":
                # SEV-1 review fix: phase_details returns rc 0 with rows
                # still remaining (batch-capped) — drain until the board
                # is fully detailed or the loop cap trips (a >200-row
                # churn wave must never silently ship detail-less rows)
                drains = 0
                while _details_remaining(lbl) > 0 and drains < 10:
                    rc = run_phase(lbl, w, "details")
                    drains += 1
                    if rc != 0:
                        print(f"!! [{lbl}] details drain rc={rc}",
                              flush=True)
                        failed.append((lbl, "details"))
                        phase_failed = True
                        break
                if _details_remaining(lbl) > 0:
                    print(f"!! [{lbl}] details did not drain after "
                          f"{drains} batches — board churn loop?",
                          flush=True)
                    failed.append((lbl, "details-drain"))
                    phase_failed = True
                if phase_failed:
                    break
            if p == "titlesearch":
                # the phase's own contract: repeat until done=true
                # (a single pass on a large no_match wave ships honest
                # not_checked rows but leaves discovery half-done)
                for _ in range(6):
                    rc = run_phase(lbl, w, "titlesearch")
                    if rc != 0:
                        print(f"!! [{lbl}] titlesearch drain rc={rc}",
                              flush=True)
                        failed.append((lbl, "titlesearch"))
                        phase_failed = True
                        break
                    if not _titlesearch_running(lbl):
                        break
                if phase_failed:
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
        if phase_failed:
            print(f"== [{lbl}] invariants SKIPPED (phase failed — "
                  f"the stale CSV would validate green)", flush=True)
            continue
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
