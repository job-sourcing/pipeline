#!/usr/bin/env python3
"""
Gap-fill probe: validate 4 unverified fallback paths referenced in the methodology doc.
Run BEFORE final commit so the methodology's "Verified 2026-08-26" claims are all real.

Probes:
1. Personio HTML fallback: {slug}.jobs.personio.de/?language=en  (XML 429 → fall back here)
2. WTTJ public jobs API: wttj.com/api/public/jobs/{slug}  (Otta replacement)
3. Workable markdown feed fallback: apply.workable.com/{slug}/jobs.md  (widget 429 → fall back)
4. SmartRecruiters publicposting API: api.smartrecruiters.com/v1/companies/{slug}/jobs
5. bonus: a few more WTTJ company slugs (welcometothejungle's customers)
6. bonus: Ashby partner feed api (ashbyhq.com/api/public/job-board/{slug}/feed)
"""
import json, time, os, sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
OUT_DIR = "/home/z/my-project/job-sourcing/track3-js/validation_results/gap_fill_post_p0"
os.makedirs(OUT_DIR, exist_ok=True)

def probe(label, url, headers=None, method="GET", body=None):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
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
            for m in ('"title":', "<title>", '"job"', '"position"', '"id":', "<position>", 'class="job'):
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
                "preview": text[:400],
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

# --- 1. Personio HTML fallback ---
results["personio_html_fallback"] = []
personio_slugs = ["personio", "kiwigrid", "demo", "neuralink", "deepgram", "vetster", "forto", "home24"]
for slug in personio_slugs:
    url = f"https://{slug}.jobs.personio.de/?language=en"
    results["personio_html_fallback"].append(probe(f"personio_html_{slug}", url))
    time.sleep(11)  # domain-wide throttle

# --- 2. WTTJ public jobs API (Otta replacement) ---
results["wttj_public_api"] = []
wttj_slugs = ["welcometothejungle", "doctolib", "alan", "ledger", "qonto", "swile", "phaidon", "mecane"]
for slug in wttj_slugs:
    url = f"https://www.welcometothejungle.com/api/public/jobs/{slug}"
    results["wttj_public_api"].append(probe(f"wttj_api_{slug}", url))
    time.sleep(1)

for slug in ["doctolib", "alan", "qonto"]:
    url = f"https://api.welcometothejungle.com/v2/jobs?organization_slug={slug}"
    results["wttj_public_api"].append(probe(f"wttj_v2_{slug}", url))
    time.sleep(1)

# --- 3. Workable markdown feed fallback ---
results["workable_md_fallback"] = []
workable_slugs = ["semios", "huggingface", "wise", "klarna", "sumup", "typeform", "hotjar", "veed"]
for slug in workable_slugs:
    url = f"https://apply.workable.com/{slug}/jobs.md"
    results["workable_md_fallback"].append(probe(f"workable_md_{slug}", url))
    time.sleep(0.5)

# --- 4. SmartRecruiters publicposting API (corrected endpoint) ---
results["smartrecruiters_publicposting"] = []
sr_slugs = ["visa", "square", "block", "atlassian", "nike", "cargill", "genentech", "samsung"]
for slug in sr_slugs:
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/jobs"
    results["smartrecruiters_publicposting"].append(probe(f"sr_jobs_{slug}", url))
    time.sleep(0.5)

for slug in ["visa", "atlassian"]:
    url = f"https://api.smartrecruiters.com/public-posting-api/api-v1/companies/{slug}/jobs?limit=100&offset=0"
    results["smartrecruiters_publicposting"].append(probe(f"sr_publicposting_{slug}", url))
    time.sleep(0.5)

# --- 5. Bonus: Ashby partner feed (mentioned in methodology as hourly push) ---
results["ashby_partner_feed"] = []
for slug in ["openai", "ramp", "notion"]:
    url = f"https://api.ashbyhq.com/public/v1/sites/{slug}/jobs"
    results["ashby_partner_feed"].append(probe(f"ashby_partner_{slug}", url))
    time.sleep(0.5)

# --- 6. Bonus: BambooHR public JSON endpoint (mentioned in T2 providers) ---
results["bamboohr_public"] = []
for slug in ["reddit", "kickstarter"]:
    url = f"https://{slug}.bamboohr.com/jobs/?in_iframe=1"
    results["bamboohr_public"].append(probe(f"bamboohr_{slug}", url))
    time.sleep(0.5)

# Save results
out_path = os.path.join(OUT_DIR, "results.json")
with open(out_path, "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

# Summary
print("\n=== GAP-FILL SUMMARY ===")
for section, items in results.items():
    print(f"\n{section}: {len(items)} probes")
    for r in items:
        s = r.get("status")
        size = r.get("size")
        jm = r.get("job_markers", 0)
        ab = r.get("anti_bot_signals", [])
        print(f"  {r['label']}: status={s} size={size} job_markers={jm} anti_bot={ab}")
print(f"\nResults saved: {out_path}")
