#!/usr/bin/env python3
"""Firecrawl v2 evaluation. Economical (~6 scrapes) covering basic/JS/stealth/WAF.
Writes docs/results/firecrawl.md.
"""
from __future__ import annotations
import json, os, time, pathlib, urllib.request, urllib.error, re

REPO = pathlib.Path("/home/z/agent-kit")
OUT  = REPO / "docs" / "results" / "firecrawl"; OUT.mkdir(parents=True, exist_ok=True)


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


KEY = _env("FIRECRAWL_API_KEY")
API = "https://api.firecrawl.dev/v2/scrape"

def scrape(url, formats=("markdown",), proxy=None, wait_for=None, only_main=True, timeout_s=60):
    body = {"url": url, "formats": list(formats), "onlyMainContent": only_main, "timeout": timeout_s*1000}
    if proxy: body["proxy"] = proxy
    if wait_for: body["waitFor"] = wait_for
    req = urllib.request.Request(API,
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {KEY}"})
    started = time.time()
    res = {"url":url, "proxy":proxy or "basic", "formats":formats, "wait_for":wait_for,
           "elapsed_ms":None, "status":None, "headers":{}, "body":None, "error":None}
    try:
        with urllib.request.urlopen(req, timeout=timeout_s+20) as r:
            res["status"] = r.status
            res["headers"] = dict(r.headers)
            res["body"] = r.read().decode("utf-8","ignore")
    except urllib.error.HTTPError as e:
        res["status"] = e.code
        res["headers"] = dict(e.headers)
        res["body"] = e.read().decode("utf-8","ignore")
    except Exception as e:
        res["error"] = repr(e)
    res["elapsed_ms"] = int((time.time()-started)*1000)
    return res

def extract_md(res):
    """Pull markdown + metadata from a scrape response."""
    try:
        j = json.loads(res["body"])
        d = j.get("data", {}) or {}
        return {
            "success": j.get("success"),
            "markdown": (d.get("markdown") or "")[:1500],
            "rawHtml_present": "rawHtml" in d,
            "metadata": d.get("metadata", {}),
            "error": j.get("error") or d.get("error"),
        }
    except Exception as e:
        return {"parse_err": repr(e), "body_head": (res["body"] or "")[:300]}

md = ["# Firecrawl v2 evaluation", "", f"_captured: {time.strftime('%H:%M:%S UTC')}_", ""]

# 0. Test connection + credit headers
md += ["## 0. Probe (example.com markdown)", ""]
r = scrape("https://example.com")
m = extract_md(r)
md.append(f"- HTTP {r['status']} elapsed={r['elapsed_ms']}ms proxy={r['proxy']}")
md.append(f"- response headers of interest:")
for h in ["x-firecrawl-credits","x-firecrawl-credits-used","x-firecrawl-plan","x-ratelimit-remaining","x-ratelimit-reset"]:
    v = r["headers"].get(h) or r["headers"].get(h.title()) or r["headers"].get(h.replace("x-","X-"))
    if v: md.append(f"  - `{h}`: `{v}`")
md.append(f"- success={m.get('success')} error={m.get('error')}")
md.append(f"- markdown head: `{m.get('markdown','')[:200]}`")
md.append(f"- metadata: `{json.dumps(m.get('metadata',{}))[:300]}`")
md += [""]

# 1. Egress IP (ipinfo markdown)
md += ["## 1. Egress IP (ipinfo.io/json as markdown)", ""]
r = scrape("https://ipinfo.io/json")
m = extract_md(r)
md.append(f"- HTTP {r['status']} elapsed={r['elapsed_ms']}ms")
# ipinfo returns JSON; Firecrawl markdown may quote it
ip_match = re.search(r'("ip"\s*:\s*"([\d.]+))', m.get("markdown","") + (r.get("body") or ""))
ip = ip_match.group(2) if ip_match else None
md.append(f"- Firecrawl egress IP (parsed from markdown): `{ip}`")
md.append(f"- markdown head: `{m.get('markdown','')[:300]}`")
md += [""]

# 2. JS rendering (quotes.toscrape.com/js/)
md += ["## 2. JS rendering — quotes.toscrape.com/js/ (waitFor=3000)", ""]
r = scrape("https://quotes.toscrape.com/js/", wait_for=3000)
m = extract_md(r)
qb = (m.get("markdown","") or "").count('"quote') + (m.get("markdown","") or "").count("text-")
# count quote blocks more reliably from rawHtml if present
md.append(f"- HTTP {r['status']} elapsed={r['elapsed_ms']}ms")
md.append(f"- markdown length={len(m.get('markdown','') or '')} quote-markers={qb}")
md.append(f"- markdown head: `{(m.get('markdown','') or '')[:300]}`")
md += [""]

# 3. CF challenge — basic vs stealth
md += ["## 3. Cloudflare challenge — basic vs stealth proxy", ""]
md.append("| proxy | http_status | success | title/markers | markdown_head | elapsed_ms |")
md.append("|---|---|---|---|---|---|")
for proxy in [None, "stealth"]:
    r = scrape("https://www.scrapingcourse.com/cloudflare-challenge", proxy=proxy, wait_for=4000, timeout_s=90)
    m = extract_md(r)
    bl = (m.get("markdown","") or "").lower()
    markers = [x for x in ["just a moment","cf-chl","captcha","cloudflare","challenge"] if x in bl]
    md.append(f"| {proxy or 'basic'} | {r['status']} | {m.get('success')} | markers={','.join(markers)} | `{(m.get('markdown','') or '')[:80]}` | {r['elapsed_ms']} |")
    time.sleep(1)
md += [""]

# 4. nowsecure.nl (hard CF) — stealth
md += ["## 4. nowsecure.nl (hard CF) — stealth proxy", ""]
r = scrape("https://nowsecure.nl", proxy="stealth", wait_for=5000, timeout_s=90)
m = extract_md(r)
bl = (m.get("markdown","") or "").lower()
markers = [x for x in ["just a moment","cf-chl","captcha","cloudflare","challenge","nowsecure"] if x in bl]
md.append(f"- HTTP {r['status']} success={m.get('success')} elapsed={r['elapsed_ms']}ms")
md.append(f"- markers: {markers}")
md.append(f"- markdown head: `{(m.get('markdown','') or '')[:300]}`")
md += [""]

# 5. Reddit JSON — stealth
md += ["## 5. Reddit /r/technology.json — stealth proxy", ""]
r = scrape("https://www.reddit.com/r/technology.json", formats=("markdown",), proxy="stealth", timeout_s=60)
m = extract_md(r)
md.append(f"- HTTP {r['status']} success={m.get('success')} elapsed={r['elapsed_ms']}ms")
md.append(f"- markdown head: `{(m.get('markdown','') or '')[:200]}`")
md += [""]

# Save raw responses
(OUT/"raw-responses.json").write_text(json.dumps({"probe":extract_md(scrape("https://example.com"))}, indent=2)[:2000])
(REPO/"docs"/"results"/"firecrawl.md").write_text("\n".join(md))
print("wrote docs/results/firecrawl.md")
print("\n=== SUMMARY (sections captured) ===")
for line in md:
    if line.startswith("##"): print(line)
