#!/usr/bin/env python3
"""s49_fleet_redeploy_zip.py — the S49 REST *zip-build* redeploy of the
js-fleet workers (fallback after the file-PUT draft path stalled in
`prepared`: API deploys skip the build step, so the esbuild function
bundling never runs. The zip-build path (audit/s30_netlify_compute_plan.md
P6) hands Netlify's build infra a source zip — the build command +
function processing run on THEIR machines (minutes unmetered) and the
deploy lands as a branch deploy = 0 credits).

Usage:
  python3 scripts/s49_fleet_redeploy_zip.py 1            # rebuild worker 1
  python3 scripts/s49_fleet_redeploy_zip.py 0 1 2 3 4 5   # the whole fleet
  python3 scripts/s49_fleet_redeploy_zip.py 1 --test      # then live-test
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KEYS_FILE = REPO / "ingest/.netlify_fleet_keys"
REG = REPO / "ingest/data/ats_seed/s21_fleet/sites.json"
API = "https://api.netlify.com/api/v1"

S21 = (REPO / "scripts/s21_netlify_fleet.mjs").read_text()
M = re.search(r"const WORKER_SRC = `([\s\S]*?)`\n", S21)
WORKER_SRC = M.group(1) if M else ""
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


def api(token: str, path: str, *, data: bytes | None = None,
        headers: dict | None = None, method: str | None = None):
    req = urllib.request.Request(
        API + path, data=data,
        headers={"Authorization": f"Bearer {token}",
                 **(headers or {})}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            body = r.read()
    except urllib.error.HTTPError as h:
        try:
            return json.loads(h.read())
        except Exception:                    # noqa: BLE001
            return {"_http_error": h.code}
    try:
        return json.loads(body)
    except ValueError:
        return {"_raw": body[:200].decode("utf8", "replace")}


def make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("netlify.toml", TOML)
        z.writestr("src/index.html", INDEX)
        z.writestr("netlify/functions/fleet.mjs", WORKER_SRC)
    return buf.getvalue()


def deploy_one(n: int, keys: list[str], do_test: bool) -> bool:
    token = keys[n]
    site_name = f"js-fleet-{n:02d}"
    sites = api(token, "/sites?filter=all")
    site = next((s for s in sites if s["name"] == site_name), None)
    if site is None:
        print(f"[{site_name}] site NOT FOUND on key {n}'s account — skipping")
        return False
    site_id = site["id"]
    print(f"[{site_name}] site {site_id}")

    zbytes = make_zip()
    build = api(token, f"/sites/{site_id}/builds?branch=worker&title=s49-redeploy",
                data=zbytes, headers={"Content-Type": "application/zip"},
                method="POST")
    build_id = build.get("id") if isinstance(build, dict) else None
    if not build_id:
        print(f"[{site_name}] build create FAILED: {str(build)[:200]}")
        return False
    print(f"[{site_name}] build {build_id} submitted (zip {len(zbytes)//1024}KB)")

    # poll the build → done; the build object carries deploy_id directly
    build2: dict = {}
    for _ in range(60):                      # builds ~1-5 min; cap 15 min
        time.sleep(15)
        build2 = api(token, f"/builds/{build_id}")
        done = build2.get("done")
        dstate = build2.get("deploy_state", "?")
        print(f"[{site_name}] done={done} deploy_state={dstate}")
        if done is True or done is False and dstate in (
                "ready", "error", "rejected", "canceled"):
            break
    if build2.get("error") or build2.get("deploy_state") not in (
            "ready", "uploaded", "prepared"):
        print(f"[{site_name}] build did not finish ready "
              f"(done={build2.get('done')} state={build2.get('deploy_state')} "
              f"error={str(build2.get('error'))[:120]})")
        return False
    dep_id = build2.get("deploy_id")
    if not dep_id:
        print(f"[{site_name}] no deploy_id on the build")
        return False
    url = f"https://{dep_id}--{site_name}.netlify.app/fleet"
    print(f"[{site_name}] deploy {dep_id} → {url}")

    reg = json.loads(REG.read_text()) if REG.exists() else {}
    reg[str(n)] = {"n": n, "site": site_name, "siteId": site_id,
                   "deployId": dep_id, "url": url,
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    REG.write_text(json.dumps(reg, indent=1))

    if do_test:
        time.sleep(20)                       # function warm-up margin
        post = urllib.request.Request(
            url, data=json.dumps({"op": "ip"}).encode(),
            headers={"Content-Type": "application/json"})
        ip = json.loads(urllib.request.urlopen(post, timeout=60).read())
        post2 = urllib.request.Request(
            url, data=json.dumps({"op": "fetch", "url": "https://example.com",
                                  "timeout_s": 15,
                                  "max_bytes": 200000}).encode(),
            headers={"Content-Type": "application/json"})
        f = json.loads(urllib.request.urlopen(post2, timeout=60).read())
        body_len = len(f.get("body") or "")
        print(f"[{site_name}] ip={ip.get('ip')} fetch={f.get('status')} "
              f"bytes={f.get('bytes')} body_len={body_len}")
        return f.get("status") == 200 and body_len > 0
    return True


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_test = "--test" in sys.argv
    keys = load_keys()
    if args and args[0] == "all":
        args = ["0", "1", "2", "3", "4", "5"]
    ok = True
    for a in args:
        n = int(a)
        if n >= len(keys):
            print(f"bad key index {n}")
            return 2
        try:
            ok = deploy_one(n, keys, do_test) and ok
        except Exception as exc:              # noqa: BLE001
            print(f"FAIL [{a}]: {type(exc).__name__}: {exc}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
