#!/usr/bin/env python3
"""
Gap-fill probe #2: correct endpoints after inspecting T2 provider source code.

T2 source code revealed:
1. SmartRecruiters correct path is /v1/companies/{slug}/postings?status=PUBLIC (NOT /jobs)
2. WTTJ does NOT have a /api/public/jobs/{slug} endpoint — it uses ALGOLIA SEARCH.
   Flow: fetch welcometothejungle.com/api/env → extract PUBLIC_ALGOLIA_APPLICATION_ID
   + PUBLIC_ALGOLIA_API_KEY_CLIENT → call {appId}-dsn.algolia.net/1/indexes/wttj_jobs_production_en/query
3. BambooHR correct path is /careers/list (NOT /jobs/?in_iframe=1)
4. Personio HTML fallback needs 60+s between probes (domain-wide 429)
"""
import json, time, os, re, sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
OUT_DIR = "/home/z/my-project/job-sourcing/track3-js/validation_results/gap_fill_post_p0"
os.makedirs(OUT_DIR, exist_ok=True)

def probe(label, url, headers=None, method="GET", body=None, content_type=None):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    if content_type:
        h["Content-Type"] = content_type
    req = Request(url, headers=h, method=method, data=body)
    t0 = time.time()
    try:
        with urlopen(req, timeout=15) as r:
            raw = r.read()
            size = len(raw)
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                text = ""
            job_markers = 0
            for m in ('"title":', '"jobOpeningName"', '"position"', '"id":', "<position>", 'class="job', '"hits":'):
                job_markers += text.count(m)
            anti_bot = []
            for sig in ("cloudflare", "captcha", "turnstile", "challenge", "blocked", "human"):
                if sig in text.lower():
                    anti_bot.append(sig)
            return {
                "label": label, "url": url, "status": r.status, "size": size,
                "content_type": r.headers.get("Content-Type", ""),
                "final_url": r.url, "job_markers": job_markers,
                "anti_bot_signals": sorted(set(anti_bot)),
                "duration_s": round(time.time() - t0, 2),
                "preview": text[:600],
            }
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        return {
            "label": label, "url": url, "status": e.code,
            "size": len(body), "content_type": e.headers.get("Content-Type", ""),
            "final_url": e.url, "job_markers": 0, "anti_bot_signals": [],
            "duration_s": round(time.time() - t0, 2),
            "preview": body, "error": str(e),
        }
    except URLError as e:
        return {
            "label": label, "url": url, "status": "URLError",
            "size": 0, "content_type": "", "final_url": url,
            "job_markers": 0, "anti_bot_signals": [],
            "duration_s": round(time.time() - t0, 2),
            "preview": "", "error": str(e),
        }

results = {}

# === 1. SmartRecruiters (correct endpoint: /postings?status=PUBLIC) ===
print("=== SmartRecruiters (corrected /postings endpoint) ===", flush=True)
results["smartrecruiters_postings"] = []
sr_slugs = ["visa", "square", "block", "atlassian", "nike", "cargill", "genentech", "samsung", "ericsson", "bosch"]
for slug in sr_slugs:
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0&status=PUBLIC"
    r = probe(f"sr_postings_{slug}", url)
    results["smartrecruiters_postings"].append(r)
    print(f"  {slug}: {r['status']} size={r['size']} markers={r['job_markers']}", flush=True)
    time.sleep(0.5)

# === 2. WTTJ via Algolia (the real flow) ===
print("\n=== WTTJ Algolia flow ===", flush=True)
results["wttj_algolia"] = []

# Step A: fetch /api/env to extract PUBLIC_ALGOLIA_APPLICATION_ID + PUBLIC_ALGOLIA_API_KEY_CLIENT
env_url = "https://www.welcometothejungle.com/api/env"
env_probe = probe("wttj_env", env_url)
results["wttj_algolia"].append(env_probe)
print(f"  env: status={env_probe['status']} size={env_probe['size']}", flush=True)

# Parse the env payload — T2's wttj.mjs extracts JSON from a script-like structure
env_text = env_probe.get("preview", "")
# Look for PUBLIC_ALGOLIA_APPLICATION_ID and PUBLIC_ALGOLIA_API_KEY_CLIENT
app_id_match = re.search(r'PUBLIC_ALGOLIA_APPLICATION_ID["\']?\s*[:=]\s*["\']([A-Z0-9]{6,16})', env_text, re.I)
api_key_match = re.search(r'PUBLIC_ALGOLIA_API_KEY_CLIENT["\']?\s*[:=]\s*["\']([a-f0-9]{16,500})', env_text, re.I)
print(f"  app_id match: {app_id_match.group(1) if app_id_match else 'NONE'}", flush=True)
print(f"  api_key match: {api_key_match.group(1)[:32] + '...' if api_key_match else 'NONE'}", flush=True)

if app_id_match and api_key_match:
    app_id = app_id_match.group(1)
    api_key = api_key_match.group(1)
    algolia_host = f"{app_id}-dsn.algolia.net"
    index = "wttj_jobs_production_en"
    # Algolia query API: POST /1/indexes/{index}/query with body {"params": "..."}
    algolia_url = f"https://{algolia_host}/1/indexes/{index}/query"
    # Try a search for "software engineer"
    body = json.dumps({"params": "query=software%20engineer&hitsPerPage=5"}).encode("utf-8")
    algolia_probe = probe(
        "wttj_algolia_search",
        algolia_url,
        method="POST",
        body=body,
        content_type="application/json",
        headers={
            "x-algolia-application-id": app_id,
            "x-algolia-api-key": api_key,
            "Referer": "https://www.welcometothejungle.com/",
            "Origin": "https://www.welcometothejungle.com",
        },
    )
    results["wttj_algolia"].append(algolia_probe)
    print(f"  algolia search: status={algolia_probe['status']} size={algolia_probe['size']} markers={algolia_probe['job_markers']}", flush=True)
    
    # Also try a no-query "list all" — Algolia accepts empty query
    body2 = json.dumps({"params": "hitsPerPage=10"}).encode("utf-8")
    algolia_probe2 = probe(
        "wttj_algolia_list",
        algolia_url,
        method="POST",
        body=body2,
        content_type="application/json",
        headers={
            "x-algolia-application-id": app_id,
            "x-algolia-api-key": api_key,
            "Referer": "https://www.welcometothejungle.com/",
        },
    )
    results["wttj_algolia"].append(algolia_probe2)
    print(f"  algolia list: status={algolia_probe2['status']} size={algolia_probe2['size']} markers={algolia_probe2['job_markers']}", flush=True)
else:
    # If env endpoint didn't expose creds inline, the T2 provider extracts JSON from a script tag
    # Look for { ... PUBLIC_ALGOLIA_APPLICATION_ID ... }
    json_match = re.search(r'\{[^{}]*PUBLIC_ALGOLIA_APPLICATION_ID[^{}]*\}', env_text)
    print(f"  json-pattern match: {'YES' if json_match else 'NO'}", flush=True)
    # Print bigger preview to inspect
    print(f"  env preview (first 800 chars): {env_text[:800]}", flush=True)

# === 3. BambooHR (correct endpoint: /careers/list) ===
print("\n=== BambooHR (corrected /careers/list endpoint) ===", flush=True)
results["bamboohr_careers_list"] = []
# BambooHR tenants are typically the company's name; try common ones
bamboohr_slugs = ["reddit", "kickstarter", "cloudbeds", "envoy", "mercury", "ramp", "lever", "tidemark", "mural", "superside"]
for slug in bamboohr_slugs:
    url = f"https://{slug}.bamboohr.com/careers/list"
    r = probe(f"bamboohr_{slug}", url)
    results["bamboohr_careers_list"].append(r)
    print(f"  {slug}: {r['status']} size={r['size']} markers={r['job_markers']}", flush=True)
    time.sleep(0.3)

# === 4. Personio HTML fallback (with 90s wait first to clear the domain-wide rate limit) ===
print("\n=== Personio HTML fallback (after 90s cool-down) ===", flush=True)
print("  sleeping 90s to let domain-wide 429 clear...", flush=True)
time.sleep(90)
results["personio_html_fallback_v2"] = []
# Use only 2 slugs that DIDN'T get 429 earlier (kiwigrid + demo already returned 200)
# plus 2 fresh slugs to confirm HTML fallback generality
personio_slugs_v2 = ["kiwigrid", "demo", "twa", "forto"]
for slug in personio_slugs_v2:
    url = f"https://{slug}.jobs.personio.de/?language=en"
    r = probe(f"personio_html_v2_{slug}", url)
    results["personio_html_fallback_v2"].append(r)
    print(f"  {slug}: {r['status']} size={r['size']} markers={r['job_markers']}", flush=True)
    time.sleep(15)  # 15s spacing to avoid re-triggering 429

# Save results
out_path = os.path.join(OUT_DIR, "results.json")
# Merge with existing results if present
existing = {}
if os.path.exists(out_path):
    try:
        with open(out_path) as f:
            existing = json.load(f)
    except Exception:
        pass
# Merge: existing + new (new keys overwrite if same name)
merged = {**existing, **results}
with open(out_path, "w") as f:
    json.dump(merged, f, indent=2, ensure_ascii=False)

print(f"\nResults saved: {out_path}")
print(f"Total sections: {len(merged)}")
for k in merged:
    print(f"  - {k}")
