#!/usr/bin/env python3
"""Zenrows v1 evaluation. Economical (~6 calls) focused on WAF bypass capability.
Writes docs/results/zenrows.md + raw responses.
"""
from __future__ import annotations
import json, os, time, pathlib, urllib.parse, re
import requests

REPO = pathlib.Path("/home/z/agent-kit")
OUT  = REPO / "docs" / "results" / "zenrows"; OUT.mkdir(parents=True, exist_ok=True)


def _env(*names):
    """os.environ first, then kit .env, then the repo secret store ingest/.env
    (S8-A scrub — the key is never hardcoded)."""
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


KEY = _env("ZENROWS_API_KEY")
API = "https://api.zenrows.com/v1/"

def fetch(url, response_type="markdown", js_render=False, wait_for=None, wait_ms=None,
          premium_proxy=False, proxy_country=None, mode=None, timeout_s=90):
    q = {"apikey": KEY, "url": url}
    if response_type: q["response_type"] = response_type
    if js_render: q["js_render"] = "true"
    if wait_for: q["wait_for"] = str(wait_for)
    if wait_ms: q["wait"] = str(wait_ms)
    if premium_proxy: q["premium_proxy"] = "true"
    if proxy_country: q["proxy_country"] = proxy_country
    if mode: q["mode"] = mode
    started = time.time()
    res = {"url":url, "params":{k:v for k,v in q.items() if k!="apikey"},
           "elapsed_ms":None, "status":None, "headers":{}, "body":None, "error":None}
    try:
        r = requests.get(API, params=q, timeout=timeout_s+20,
                         headers={"User-Agent":"agent-kit-eval/1.0","Accept-Encoding":"gzip, deflate"})
        res["status"] = r.status_code
        res["headers"] = dict(r.headers)
        res["body"] = r.text
    except Exception as e:
        res["error"] = repr(e)
    res["elapsed_ms"] = int((time.time()-started)*1000)
    return res

def credit_info(res):
    h = res["headers"]
    return {
        "X-Request-Cost": h.get("X-Request-Cost") or h.get("x-request-cost"),
        "X-Request-Id": h.get("X-Request-Id") or h.get("x-request-id"),
        "Zr-Final-Url": h.get("Zr-Final-Url") or h.get("zr-final-url"),
        "Concurrency-Remaining": h.get("Concurrency-Remaining") or h.get("concurrency-remaining"),
    }

md = ["# Zenrows v1 evaluation", "", f"_captured: {time.strftime('%H:%M:%S UTC')}_", ""]

# 1. Egress IP + credit baseline (plain proxy, no js_render)
md += ["## 1. Egress IP + credit baseline (ipinfo.io/json, plain proxy)", ""]
r = fetch("https://ipinfo.io/json", response_type=None)
ci = credit_info(r)
md.append(f"- HTTP {r['status']} elapsed={r['elapsed_ms']}ms error={r['error']}")
md.append(f"- request cost: `{json.dumps(ci)}`")
try:
    j = json.loads(r["body"] or "")
    md.append(f"- egress IP: `{j.get('ip')}` city={j.get('city')} country={j.get('country')} org={j.get('org')}")
except Exception:
    md.append(f"- body[:200]=`{(r['body'] or '')[:200]}`")
md += [""]

# 2. Basic markdown (example.com)
md += ["## 2. Basic markdown (example.com)", ""]
r = fetch("https://example.com", response_type="markdown")
ci = credit_info(r)
md.append(f"- HTTP {r['status']} elapsed={r['elapsed_ms']}ms X-Request-Cost={ci.get('X-Request-Cost')} error={r['error']}")
md.append(f"- markdown head: `{(r['body'] or '')[:200]}`")
md += [""]

# 3. JS rendering (quotes.toscrape.com/js/)
md += ["## 3. JS rendering (quotes.toscrape.com/js/, js_render + wait)", ""]
r = fetch("https://quotes.toscrape.com/js/", response_type="markdown", js_render=True, wait_ms=2000)
ci = credit_info(r)
qb = (r["body"] or "").count('"quote') + (r["body"] or "").count("text-")
md.append(f"- HTTP {r['status']} elapsed={r['elapsed_ms']}ms X-Request-Cost={ci.get('X-Request-Cost')} error={r['error']}")
md.append(f"- markdown length={len(r['body'] or '')} quote-markers={qb}")
md.append(f"- markdown head: `{(r['body'] or '')[:300]}`")
md += [""]

# 4. CF challenge — Adaptive Stealth Mode (mode=auto) + premium proxy US
md += ["## 4. Cloudflare challenge — mode=auto (Adaptive Stealth) + premium proxy (US)", ""]
md.append("| target | http_status | request_cost | title/markers | elapsed_ms | error |")
md.append("|---|---|---|---|---|---|")
for tgt in ["https://www.scrapingcourse.com/cloudflare-challenge",
            "https://nowsecure.nl",
            "https://www.reddit.com/r/technology.json"]:
    r = fetch(tgt, response_type="markdown", mode="auto", proxy_country="us", timeout_s=120)
    ci = credit_info(r)
    bl = (r["body"] or "").lower()
    markers = [x for x in ["just a moment","cf-chl","captcha","cloudflare","forbidden","denied","reddit","attention"] if x in bl]
    m = re.search(r"^#\s+(.+)$", r["body"] or "", re.M)
    title = (m.group(1).strip()[:60] if m else "")
    md.append(f"| {tgt} | {r['status']} | {ci.get('X-Request-Cost')} | title=`{title}` markers={','.join(markers)} | {r['elapsed_ms']} | {(r['error'] or '')[:60]} |")
    md.append(f"  - final_url={ci.get('Zr-Final-Url')}")
    time.sleep(2)
md += [""]

# Save raw
(OUT/"raw-summary.json").write_text(json.dumps({
    "ipinfo_egress": (r["body"] or "")[:200] if r else None,
    "credit_header_examples": ci,
}, indent=2))
(REPO/"docs"/"results"/"zenrows.md").write_text("\n".join(md))
print("wrote docs/results/zenrows.md")
print("\n=== SUMMARY (sections) ===")
for line in md:
    if line.startswith("##"): print(line)
