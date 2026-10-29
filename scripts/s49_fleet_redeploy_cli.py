#!/usr/bin/env python3
"""s49_fleet_redeploy_cli.py — the S49 CLI *draft-deploy* redeploy of
the js-fleet workers (the ORIGINAL s21 path, revived after the REST
file-PUT stalled in `prepared` and the zip-build queue proved durably
stuck at deploy_state=new). The CLI bundles the function LOCALLY
(esbuild via netlify-cli's own toolchain — no Netlify build minutes)
and a --draft deploy costs 0 credits.

Usage:
  python3 scripts/s49_fleet_redeploy_cli.py 1            # rebuild worker 1
  python3 scripts/s49_fleet_redeploy_cli.py 0 1 2 3 4 5  # the whole fleet
  python3 scripts/s49_fleet_redeploy_cli.py 1 --test     # then live-test
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KEYS_FILE = REPO / "ingest/.netlify_fleet_keys"
REG = REPO / "ingest/data/ats_seed/s21_fleet/sites.json"
API = "https://api.netlify.com/api/v1"
CLI = "/home/z/node_modules/.bin/netlify"

S21 = (REPO / "scripts/s21_netlify_fleet.mjs").read_text()
M = re.search(r"const WORKER_SRC = `([\s\S]*?)`\n", S21)
WORKER_SRC = M.group(1) if M else ""
# JS template-literal escape processing (s21's writeFileSync gets this
# free from the JS engine; raw Python extraction does NOT): the only
# escape class this source uses is \\  → unescape it or the regex
# literals in fleet.mjs terminate early (esbuild "Syntax error" at the
# first \\/ — the S49 lesson: replicate the writer's escape semantics).
WORKER_SRC = WORKER_SRC.replace("\\\\", "\\")
assert WORKER_SRC, "WORKER_SRC not found in s21_netlify_fleet.mjs"

TOML = ('[build]\n  command = "echo fleet-worker"\n'
        '  publish = "src"\n  functions = "netlify/functions"\n\n'
        '[functions]\n  node_bundler = "esbuild"\n')
INDEX = '<!doctype html><title>js-fleet</title>ok\n'


def load_keys() -> list[str]:
    keys = []
    for line in KEYS_FILE.read_text().split("\n"):
        m = re.match(r"^(nfp_\S+)", line)
        if m:
            keys.append(m.group(1))
    return keys


def api(token: str, path: str):
    req = urllib.request.Request(API + path, headers={
        "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def deploy_one(n: int, token: str, do_test: bool) -> bool:
    site = api(token, "/sites?filter=all")
    name = next((s for s in site if s.get("name") == f"js-fleet-{n:02d}"), None)
    if not name:
        print(f"[js-fleet-{n:02d}] site not found on this key")
        return False
    site_id = name["id"]
    print(f"[js-fleet-{n:02d}] siteId={site_id}")

    with tempfile.TemporaryDirectory() as td:
        tp = Path(td)
        (tp / "netlify/functions").mkdir(parents=True, exist_ok=True)
        (tp / "src").mkdir(parents=True, exist_ok=True)
        (tp / "netlify/functions/fleet.mjs").write_text(WORKER_SRC)
        (tp / "netlify.toml").write_text(TOML)
        (tp / "src/index.html").write_text(INDEX)
        r = subprocess.run(
            [CLI, "deploy", "--draft", "--site", site_id, "--dir", "src",
             "--functions", "netlify/functions",
             "--skip-functions-cache"],   # the S49 lesson: a cached
             # function bundle ships the OLD worker (bytes=0/body=""
             # — exactly the stale shape the fleet already serves)
            cwd=td, env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                         "HOME": "/tmp",
                         "NETLIFY_AUTH_TOKEN": token,
                         "NETLIFY_SITE_ID": site_id},
            capture_output=True, text=True, timeout=420)
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"https?://([0-9a-f]{8,})--[^ ]*netlify\.app", out)
    dep_id = m.group(1) if m else None
    interesting = [ln for ln in out.splitlines()
                   if re.search(r"draft|deploy|error|fail|https", ln, re.I)]
    print("\n".join(interesting[-6:]))
    if r.returncode != 0 and not dep_id:
        print(f"[js-fleet-{n:02d}] CLI deploy failed "
              f"(exit {r.returncode})")
        return False
    url = f"https://{dep_id}--js-fleet-{n:02d}.netlify.app/fleet"
    print(f"[js-fleet-{n:02d}] deploy {dep_id} -> {url}")

    reg = json.loads(REG.read_text()) if REG.exists() else {}
    reg[str(n)] = {"n": n, "site": f"js-fleet-{n:02d}", "siteId": site_id,
                   "deployId": dep_id, "url": url,
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                       time.gmtime())}
    REG.write_text(json.dumps(reg, indent=1))

    if do_test:
        time.sleep(20)                     # function warm-up margin
        post = urllib.request.Request(
            url, data=json.dumps({"op": "ip"}).encode(),
            headers={"Content-Type": "application/json"})
        ip = json.loads(urllib.request.urlopen(post, timeout=60).read())
        post2 = urllib.request.Request(
            url, data=json.dumps({"op": "fetch",
                                  "url": "https://example.com",
                                  "timeout_s": 15,
                                  "max_bytes": 200000}).encode(),
            headers={"Content-Type": "application/json"})
        f = json.loads(urllib.request.urlopen(post2, timeout=60).read())
        body_len = len(f.get("body") or "")
        print(f"[js-fleet-{n:02d}] ip={ip.get('ip')} "
              f"fetch={f.get('status')} bytes={f.get('bytes')} "
              f"body_len={body_len}")
        return f.get("status") == 200 and body_len > 0
    return True


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_test = "--test" in sys.argv
    ns = [int(a) for a in args]
    keys = load_keys()
    fails = 0
    for n in ns:
        if n >= len(keys):
            print(f"[js-fleet-{n:02d}] no key for index {n}")
            fails += 1
            continue
        try:
            if not deploy_one(n, keys[n], do_test):
                fails += 1
        except Exception as e:              # noqa: BLE001
            print(f"[js-fleet-{n:02d}] ERROR {str(e)[:160]}")
            fails += 1
    print(f"done: {len(ns) - fails}/{len(ns)} ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
