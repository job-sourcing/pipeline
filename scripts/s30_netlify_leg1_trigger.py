#!/usr/bin/env python3
"""s30 Task 3 — Netlify compute-as-build LEG-1 driver (trigger → guard → poll → harvest).

Usage:
  python3 scripts/s30_netlify_leg1_trigger.py --key-line 5 --site 43b83851-... --zip /tmp/leg1.zip

Reads the fleet PAT from ingest/.netlify_fleet_keys line N (1-based; line 4 = key 0).
NEVER prints the token. Line-by-line progress log (stdout → captured to log file).

Guard: if the new deploy's context == "production" → POST /deploys/{id}/cancel
immediately and exit(2) (HARD RULE: no production deploys / no credit spend).
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

API = "https://api.netlify.com/api/v1"
KEYS_FILE = "/home/z/research/ingest/.netlify_fleet_keys"
UA = "s30-leg1-driver/1.0"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_key(line_no):
    with open(KEYS_FILE) as f:
        lines = [ln.rstrip("\n") for ln in f if ln.strip() and not ln.startswith("#")]
    return lines[line_no - 4].split()[0]  # line 4 == index 0


def req(method, path, key, body=None, content_type=None, timeout=60):
    url = path if path.startswith("http") else API + path
    r = urllib.request.Request(url, method=method, data=body)
    r.add_header("Authorization", f"Bearer {key}")
    r.add_header("User-Agent", UA)
    if content_type:
        r.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            code = resp.status
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        raw = e.read()
        code = e.code
        hdrs = {k.lower(): v for k, v in e.headers.items()}
    except Exception as e:  # network
        return None, None, {"error": repr(e)}
    try:
        data = json.loads(raw)
    except Exception:
        data = raw[:500].decode("utf-8", "replace")
    return code, data, hdrs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-line", type=int, default=5, help="1-based file line of fleet key (5 = key idx 1)")
    ap.add_argument("--site", default="43b83851-4eed-46b9-8596-16b0cc70f592", help="site_id (default js-fleet-01)")
    ap.add_argument("--zip", default="/tmp/leg1.zip")
    ap.add_argument("--branch", default="leg1")
    ap.add_argument("--title", default="s30-netlify-leg1-task3")
    ap.add_argument("--max-minutes", type=float, default=20.0)
    ap.add_argument("--out", default="/home/z/research/audit/s30_netlify_leg1_results.json")
    ap.add_argument("--workdir", default="/tmp/leg1-work")
    args = ap.parse_args()

    key = load_key(args.key_line)
    log(f"driver start: site={args.site} branch={args.branch} zip={args.zip} key_line={args.key_line} (token redacted)")

    # ── pre-flight: site sanity (read-only) ───────────────────────────────
    code, site_rec, _ = req("GET", f"/sites/{args.site}", key)
    if code != 200:
        log(f"FATAL: GET /sites -> HTTP {code}: {json.dumps(site_rec)[:300]}")
        sys.exit(1)
    log(f"site ok: name={site_rec.get('name')} state={site_rec.get('state')} account={site_rec.get('account_slug')} default_branch={site_rec.get('default_branch')}")

    code, builds_before, _ = req("GET", f"/sites/{args.site}/builds", key)
    log(f"lane check: existing builds={len(builds_before or [])}")

    # ── TRIGGER: POST /builds?branch=…&title=… raw zip body ───────────────
    with open(args.zip, "rb") as f:
        zip_bytes = f.read()
    log(f"trigger: POST /sites/{args.site}/builds?branch={args.branch}&title={args.title} body={len(zip_bytes)}B application/zip")
    code, build_rec, hdrs = req(
        "POST",
        f"/sites/{args.site}/builds?branch={args.branch}&title={args.title}",
        key,
        body=zip_bytes,
        content_type="application/zip",
        timeout=120,
    )
    log(f"trigger -> HTTP {code} ratelimit_remaining={hdrs.get('x-ratelimit-remaining') if isinstance(hdrs, dict) else '?'}")
    log(f"trigger response: {json.dumps(build_rec)[:400] if not isinstance(build_rec, str) else build_rec[:400]}")
    if code not in (200, 201):
        log("FATAL: zip-build trigger rejected — see plan §3 fallback (build_hooks); report RESTRICTED")
        with open(f"{args.workdir}/trigger_response.json", "w") as f:
            json.dump({"http": code, "body": build_rec}, f, indent=1)
        sys.exit(1)

    build_id = build_rec.get("id")
    deploy_id = build_rec.get("deploy_id")
    log(f"build_id={build_id} deploy_id={deploy_id}")

    if not deploy_id:
        code, deploys, _ = req("GET", f"/sites/{args.site}/deploys", key)
        for d in deploys or []:
            if d.get("build_id") == build_id:
                deploy_id = d.get("id")
                break
    if not deploy_id:
        log("FATAL: no deploy_id found for build — cannot guard/poll")
        sys.exit(1)

    # ── GUARD: context must be branch-deploy / deploy-preview, NEVER production ──
    ctx = None
    for attempt in range(12):  # up to ~60 s for the record to appear
        code, dep, _ = req("GET", f"/deploys/{deploy_id}", key)
        if code == 200 and dep:
            ctx = dep.get("context")
            if ctx:
                break
        time.sleep(5)
    log(f"GUARD: deploy {deploy_id} context={ctx!r} (required: branch-deploy|deploy-preview)")
    if ctx == "production":
        log("!!! PRODUCTION CONTEXT DETECTED — cancelling deploy NOW (hard rule: never prod) !!!")
        cc, resp, _ = req("POST", f"/deploys/{deploy_id}/cancel", key)
        log(f"cancel -> HTTP {cc}: {json.dumps(resp)[:200] if not isinstance(resp, str) else resp[:200]}")
        sys.exit(2)
    if ctx not in ("branch-deploy", "deploy-preview"):
        log(f"WARN: unexpected context {ctx!r} — continuing to poll but flagging")

    # ── POLL: build done + deploy terminal state ──────────────────────────
    deadline = time.time() + args.max_minutes * 60
    last_state = None
    deploy_final = None
    while time.time() < deadline:
        code, dep, _ = req("GET", f"/deploys/{deploy_id}", key)
        state = (dep or {}).get("state")
        ctx2 = (dep or {}).get("context")
        if state != last_state:
            log(f"deploy state: {state} (context={ctx2}, deploy_source={(dep or {}).get('deploy_source')})")
            last_state = state
        deploy_final = dep
        if state in ("ready", "error", "canceled"):
            break
        # context re-assert each poll (cheap, same call)
        if ctx2 == "production":
            log("!!! PRODUCTION APPEARED MID-FLIGHT — cancelling NOW !!!")
            req("POST", f"/deploys/{deploy_id}/cancel", key)
            sys.exit(2)
        time.sleep(6)

    if not deploy_final or deploy_final.get("state") != "ready":
        log(f"END: deploy did not reach ready: {deploy_final and deploy_final.get('state')}")
        with open(f"{args.workdir}/final_deploy.json", "w") as f:
            json.dump(deploy_final, f, indent=1)
        sys.exit(3)

    with open(f"{args.workdir}/final_deploy.json", "w") as f:
        json.dump(deploy_final, f, indent=1)
    log("deploy READY: " + json.dumps({k: deploy_final.get(k) for k in
        ("id", "state", "context", "branch", "title", "deploy_source", "build_id",
         "deploy_time", "created_at", "ssl_url", "deploy_ssl_url", "deploy_url", "url", "has_source_zip")}))

    # ── HARVEST: results.json from the branch deploy URL ─────────────────
    base = deploy_final.get("deploy_ssl_url") or deploy_final.get("ssl_url")
    harvest_url = base.rstrip("/") + "/results.json"
    log(f"harvest: GET {harvest_url}")
    got = None
    for attempt in range(10):
        r = urllib.request.Request(harvest_url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                if resp.status == 200:
                    got = resp.read()
                    break
        except urllib.error.HTTPError as e:
            log(f"harvest attempt {attempt + 1}: HTTP {e.code} — retrying")
        except Exception as e:
            log(f"harvest attempt {attempt + 1}: {e!r} — retrying")
        time.sleep(6)
    if not got:
        log("HARVEST FAILED from deploy URL — trying Blobs REST list as alternative")
        code, blobs, _ = req("GET", f"/blobs/{args.site}/site:compute-results", key)
        log(f"blobs list -> HTTP {code}: {json.dumps(blobs)[:200] if not isinstance(blobs, str) else blobs[:200]}")
        sys.exit(4)

    with open(args.out, "wb") as f:
        f.write(got)
    try:
        parsed = json.loads(got)
        counts = parsed.get("counts", {})
        env = parsed.get("env", {})
        witness = next((x for x in parsed.get("results", []) if "ipify" in x.get("url", "")), {})
        log(f"HARVEST OK: {len(got)}B -> {args.out}")
        log(f"counts: {json.dumps(counts)}")
        log(f"build env: site={env.get('site_name')} deploy_id={env.get('deploy_id')} branch={env.get('branch')} context={env.get('context')}")
        log(f"egress witness: {witness.get('snippet')}")
    except Exception as e:
        log(f"harvest saved raw ({len(got)}B) but parse failed: {e!r}")
    log("driver done")
    sys.exit(0)


if __name__ == "__main__":
    main()
