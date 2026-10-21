#!/usr/bin/env python3
"""Lane status checker — list the org fleet's recent workflow runs.

Reads GITHUB_PAT from the environment (or ingest/.env GITHUB_PAT/inline
token) — never hardcodes credentials (the org push secret-scanner
rejects any inline PAT).

Usage:
  python3 scripts/lane_check.py                 # all lanes, 4 runs each
  python3 scripts/lane_check.py "jd refetch"    # substring filter (any)
"""
import json
import os
import sys
import urllib.request
from pathlib import Path


def _pat() -> str:
    pat = os.environ.get("GITHUB_PAT", "")
    if pat:
        return pat
    env = Path(__file__).resolve().parent.parent / "ingest/.env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("GITHUB_PAT="):
                return line.split("=", 1)[1].strip()
    # last resort: the archive remote's token (local disk only)
    cfg = Path(__file__).resolve().parent.parent / ".git/config"
    import re
    if cfg.exists():
        m = re.search(r"github\.com[/:]\w+/.*?https://(\S+)@github", cfg.read_text())
        if m:
            return m.group(1)
    raise SystemExit("GITHUB_PAT not set (env or ingest/.env)")


def api(url: str, pat: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {pat}",
        "Accept": "application/vnd.github+json"})
    return json.load(urllib.request.urlopen(req))


def main() -> int:
    pat = _pat()
    names = sys.argv[1:]
    for wf in api("https://api.github.com/repos/job-sourcing/pipeline"
                  "/actions/workflows?per_page=100",
                  pat)["workflows"]:
        n = wf["name"]
        if names and not any(x.lower() in n.lower() for x in names):
            continue
        try:
            runs = api(
                f'https://api.github.com/repos/job-sourcing/pipeline'
                f'/actions/workflows/{wf["id"]}/runs?per_page=4',
                pat)["workflow_runs"]
        except Exception as e:  # noqa: BLE001
            print(f"== {n}\n   ERR {e}")
            continue
        print(f"== {n} ({wf['path'].split('/')[-1]})")
        for r in runs:
            print(f"   {r['run_number']:>4} {r['event'][:18]:<18} "
                  f"{r['status']:<12} {str(r['conclusion']):<10} "
                  f"{r['created_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
