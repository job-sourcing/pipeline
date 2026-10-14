#!/usr/bin/env python3
"""dispatch_all_lanes.py — full-fleet refresh dispatcher (S35 directive #3).

Dispatches ALL watch lanes via workflow_dispatch (no waiting for crons),
then polls until all complete or timeout. Alerts on any lane that ends
non-success. Usage:
  python3 scripts/dispatch_all_lanes.py [--poll] [--timeout 3600]
"""
import json
import os
import sys
import time
import urllib.request

PAT = os.environ.get("GITHUB_PAT") or open(
    "/home/z/repos/job-sourcing-research/.git/config").read().split(
    "https://")[1].split("@github.com")[0]
REPO = "job-sourcing/pipeline"

# lane id -> (workflow_id, inputs)
LANES = {
    "board_main": (357057424, {"watch_details_max": "120",
                                "watch_budget_seconds": "300"}),
    "li_daily": (381704432, {}),
    "li_dormant": (381704433, {}),
    "cf_weekly": (381235104, {}),
    "gj_weekly": (382718595, {}),
    "gj_dormant": (382718596, {}),
}


def api(method, path, body=None):
    url = f"https://api.github.com/repos/{REPO}/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers={
        "Authorization": f"token {PAT}",
        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def dispatch():
    run_ids = {}
    for name, (wid, inputs) in LANES.items():
        r = api("POST", f"actions/workflows/{wid}/dispatches",
                {"ref": "main", "inputs": inputs})
        print(f"dispatched {name} (wf {wid}) -> HTTP {r.get('status', '204')}")
        time.sleep(2)
    return run_ids


def poll(timeout=3600):
    t0 = time.time()
    seen = {}
    while time.time() - t0 < timeout:
        runs = api("GET", "actions/runs?per_page=25")["workflow_runs"]
        for r in runs:
            if r["event"] == "workflow_dispatch" and r["created_at"] > "2026-10-14T11:3":
                name = r["name"]
                if name not in seen:
                    seen[name] = (r["id"], r["status"], r["conclusion"])
                else:
                    seen[name] = (r["id"], r["status"], r["conclusion"])
        done = {n: v for n, v in seen.items()
                if v[1] == "completed"}
        print(f"poll: {len(done)} completed / {len(seen)} seen")
        for n, v in sorted(seen.items()):
            print(f"  {n}: {v[1]} {v[2] or ''}")
        if len(done) == len(LANES):
            failures = {n: v for n, v in done.items() if v[2] != "success"}
            if failures:
                print("FAILED LANES:", json.dumps(failures))
                return 1
            print("ALL LANES GREEN")
            return 0
        time.sleep(120)
    print("TIMEOUT waiting for lanes")
    return 2


if __name__ == "__main__":
    dispatch()
    if "--poll" in sys.argv:
        sys.exit(poll(int(os.environ.get("POLL_TIMEOUT", "3600"))))
