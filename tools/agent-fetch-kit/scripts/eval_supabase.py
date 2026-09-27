#!/usr/bin/env python3
"""Supabase edge proxy evaluation.
Writes docs/results/supabase.md + raw JSON to docs/results/supabase/.
"""
from __future__ import annotations
import os, json, time, pathlib, urllib.request, urllib.error, urllib.parse

REPO = pathlib.Path("/home/z/agent-kit")
OUT  = REPO / "docs" / "results" / "supabase"; OUT.mkdir(parents=True, exist_ok=True)


def _env(*names):
    """os.environ first, then kit .env, then the repo secret store ingest/.env
    (S8-A scrub — URL/token are never hardcoded)."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    here = pathlib.Path(__file__).resolve()
    for p in (here.parents[1] / ".env", here.parents[3] / "ingest" / ".env"):
        try:
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() in names and v.strip():
                    return v.strip().strip("'\"")
        except OSError:
            pass
    return ""


PROXY = _env("SUPABASE_PROXY_URL")
TOKEN = _env("SUPABASE_PROXY_TOKEN")

def call(url, mode=None, region=None, extract=None, timeout_s=30, method="GET", body=None):
    q = {"url": url}
    if mode: q["mode"] = mode
    if extract: q["extract"] = extract
    qs = urllib.parse.urlencode(q)
    full = f"{PROXY}?{qs}"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if region: headers["x-region"] = region
    data = None
    if method == "POST" and body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps({"url": url, "mode": mode or "fetch", "body": body}).encode()
        full = PROXY
    req = urllib.request.Request(full, headers=headers, method=method, data=data)
    started = time.time()
    res = {"elapsed_ms": None, "status": None, "headers": {}, "body": None, "error": None,
           "url": url, "mode": mode, "region": region, "extract": extract}
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            res["status"] = r.status
            res["headers"] = dict(r.headers)
            res["body"] = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        res["status"] = e.code
        res["headers"] = dict(e.headers)
        res["body"] = e.read().decode("utf-8", "ignore")
    except Exception as e:
        res["error"] = repr(e)
    res["elapsed_ms"] = int((time.time() - started) * 1000)
    return res

def diag(res):
    h = res["headers"]
    return {
        "x-proxy-mode": h.get("x-proxy-mode") or h.get("X-Proxy-Mode"),
        "x-proxy-status": h.get("x-proxy-status") or h.get("X-Proxy-Status"),
        "x-proxy-timing-ms": h.get("x-proxy-timing-ms") or h.get("X-Proxy-Timing-ms"),
        "x-sb-edge-region": h.get("x-sb-edge-region") or h.get("X-Sb-Edge-Region"),
        "x-ratelimit-remaining": h.get("x-ratelimit-remaining") or h.get("X-Ratelimit-Remaining"),
        "content-type": h.get("content-type") or h.get("Content-Type"),
    }

md = ["# Supabase edge proxy evaluation", "", f"_captured: {time.strftime('%H:%M:%S UTC')}_", ""]

md += ["## 1. Health endpoint", ""]
try:
    with urllib.request.urlopen(f"{PROXY.replace('/proxy','/health')}", timeout=15) as r:
        health = json.loads(r.read().decode())
        md.append(f"- HTTP {r.status}, body: `{json.dumps(health)}`")
except Exception as e:
    md.append(f"- health err: `{e!r}`")
md += [""]

md += ["## 2. IP rotation (default fetch mode, 5 sequential ipinfo calls)", ""]
ips = []
for i in range(5):
    r = call("https://ipinfo.io/json")
    d = diag(r)
    try:
        body = json.loads(r["body"]) if r["body"] else {}
    except Exception:
        body = {}
    ip = body.get("ip")
    if ip: ips.append(ip)
    md.append(f"- call {i+1}: status={r['status']} ip={ip} city={body.get('city')} country={body.get('country')} "
              f"elapsed={r['elapsed_ms']}ms proxy_timing={d.get('x-proxy-timing-ms')} edge={d.get('x-sb-edge-region')}")
    time.sleep(0.4)
uniq = set(ips)
md += [f"", "**Distinct IPs observed:** {} of {} calls (rotation = {})".format(
    len(uniq), len(ips), "ON" if len(uniq) > 1 else "OFF"), ""]
md += [""]

md += ["## 3. Region pinning (x-region header → IP city change)", ""]
md.append("| region | ip | city | country | edge_region | proxy_timing_ms |")
md.append("|---|---|---|---|---|---|")
for reg in ["us-east-1","us-west-2","eu-west-1","eu-central-1","ap-southeast-1","ap-northeast-1","sa-east-1","ca-central-1"]:
    r = call("https://ipinfo.io/json", mode="raw", region=reg)
    d = diag(r)
    try:
        b = json.loads(r["body"]) if r["body"] else {}
    except Exception:
        b = {}
    md.append(f"| {reg} | {b.get('ip')} | {b.get('city')} | {b.get('country')} | {d.get('x-sb-edge-region')} | {d.get('x-proxy-timing-ms')} |")
    time.sleep(0.4)
md += [""]

md += ["## 4. JA3 randomization + mode fingerprint difference (tls.peet.ws/api/all)", ""]
md.append("| call | mode | ja3_hash | ja4 | akamai_h2_fp | ip_seen | elapsed_ms | proxy_timing_ms |")
md.append("|---|---|---|---|---|---|---|---|")
ja3_modes = []
for i in range(3):
    r = call("https://tls.peet.ws/api/all", mode="fetch")
    d = diag(r)
    try:
        t = json.loads(r["body"]) if r["body"] else {}
        tls = t.get("tls",{}); h2 = t.get("http2",{})
        ja3_modes.append(("fetch",tls.get("ja3_hash"),tls.get("ja4"),h2.get("akamai_fingerprint"),t.get("ip")))
        md.append(f"| fetch#{i+1} | fetch | {tls.get('ja3_hash')} | {tls.get('ja4')} | `{h2.get('akamai_fingerprint')}` | {t.get('ip')} | {r['elapsed_ms']} | {d.get('x-proxy-timing-ms')} |")
    except Exception as e:
        md.append(f"| fetch#{i+1} err | fetch | err:{e!r} |  |  |  | {r['elapsed_ms']} |  |")
    time.sleep(0.4)
for m in ("http2","raw"):
    r = call("https://tls.peet.ws/api/all", mode=m)
    d = diag(r)
    try:
        t = json.loads(r["body"]) if r["body"] else {}
        tls = t.get("tls",{}); h2 = t.get("http2",{})
        ja3_modes.append((m,tls.get("ja3_hash"),tls.get("ja4"),h2.get("akamai_fingerprint"),t.get("ip")))
        md.append(f"| {m} | {m} | {tls.get('ja3_hash')} | {tls.get('ja4')} | `{h2.get('akamai_fingerprint')}` | {t.get('ip')} | {r['elapsed_ms']} | {d.get('x-proxy-timing-ms')} |")
    except Exception as e:
        md.append(f"| {m} err | {m} | err:{e!r} |  |  |  | {r['elapsed_ms']} |  |")
    time.sleep(0.4)
distinct_ja3 = len({x[1] for x in ja3_modes if x[1]})
md += ["", f"**Distinct JA3 hashes across {len(ja3_modes)} mode calls:** {distinct_ja3} "
       f"({'randomization ON' if distinct_ja3 >= 3 else 'limited'})"]
md += [""]

md += ["## 5. Header leakage (httpbin.org/headers) — fetch vs raw", ""]
md.append("| mode | traceparent seen? | x-forwarded-for / x-amzn-trace-id | host | user-agent |")
md.append("|---|---|---|---|---|")
for m in ("fetch","raw"):
    r = call("https://httpbin.org/headers", mode=m)
    try:
        b = json.loads(r["body"]) if r["body"] else {}
        hh = b.get("headers", {})
        tp = any(k.lower() == "traceparent" for k in hh)
        xff = hh.get("X-Forwarded-For") or hh.get("X-Amzn-Trace-Id") or ""
        md.append(f"| {m} | {tp} | {xff[:60]} | {hh.get('Host')} | {hh.get('User-Agent','')[:50]} |")
    except Exception as e:
        md.append(f"| {m} | parse err {e!r} |  |  |  |")
    time.sleep(0.4)
md += [""]

md += ["## 6. Distillation (extract=title on example.com)", ""]
r = call("https://example.com", extract="title")
d = diag(r)
md.append(f"- HTTP {r['status']}, edge={d.get('x-sb-edge-region')}, proxy_timing={d.get('x-proxy-timing-ms')}ms, total={r['elapsed_ms']}ms")
try:
    j = json.loads(r["body"])
    md.append(f"- distilled: `{json.dumps(j.get('distilled',{}))}`")
except Exception:
    md.append(f"- body first 200: `{r['body'][:200]}`")
md += [""]

md += ["## 7. POST handling (httpbin.org/post)", ""]
r = call("https://httpbin.org/post", mode="fetch", method="POST", body={"probe":"agent-fetch-kit", "ts": int(time.time())})
d = diag(r)
md.append(f"- HTTP {r['status']}, edge={d.get('x-sb-edge-region')}, proxy_timing={d.get('x-proxy-timing-ms')}ms")
try:
    j = json.loads(r["body"])
    md.append(f"- echoed json: `{json.dumps(j.get('json',{}))}`")
except Exception:
    md.append(f"- body first 200: `{r['body'][:200]}`")
md += [""]

md += ["## 8. Error / edge-case handling", ""]
md.append("| target | mode | status | body snippet | elapsed_ms |")
md.append("|---|---|---|---|---|")
for tgt in ["https://httpbin.org/status/500","https://httpbin.org/status/403","https://nonexistent.invalid./"]:
    r = call(tgt, mode="raw", timeout_s=40)
    md.append(f"| {tgt} | raw | {r['status']} | `{(r['body'] or '')[:120]}` | {r['elapsed_ms']} |")
    time.sleep(0.3)
md += [""]

(OUT / "raw.json").write_text(json.dumps({
    "rotation_ips": list(uniq),
    "ja3_modes": [{"mode":m,"ja3_hash":j,"ja4":ja4,"akamai_h2":h2,"ip":ip} for (m,j,ja4,h2,ip) in ja3_modes],
}, indent=2))
(REPO / "docs" / "results" / "supabase.md").write_text("\n".join(md))
print("wrote docs/results/supabase.md")
print(f"\n=== SUMMARY ===\nrotation: {len(uniq)} unique IPs in 5 calls\ndistinct JA3 hashes: {distinct_ja3}")
