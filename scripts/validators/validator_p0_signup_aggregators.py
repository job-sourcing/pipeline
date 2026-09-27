#!/usr/bin/env python3
"""
Validator: P0 ATS-direct (Workable, Personio) + Wellfound/Otta scraping
+ sign-up-required aggregator endpoints (Adzuna / JSearch / USAJobs /
Findwork / Jooble / Careerjet).

Goal: produce a CONCRETE validation-status verdict for each source, NOT
just "works/404" — every row needs (status, jobs_yield, auth_required,
signup_url, projected_daily_yield_if_activated, fraud_signals).

Writes results to:
  /home/z/my-project/job-sourcing/track3-js/validation_results/p0_signup_aggregators/results.json
"""

import json
import re
import sys
import time
from pathlib import Path

import requests
from curl_cffi import requests as cffi

OUT_DIR = Path("/home/z/my-project/job-sourcing/track3-js/validation_results/p0_signup_aggregators")
OUT_DIR.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HEADERS_BROWSER = {
    "User-Agent": UA,
    "Accept": "text/html,application/json,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

results = {}

# ----------------------------------------------------------------------
# 1. WORKABLE  — widget API (the primary, no-auth path from T2 provider)
# ----------------------------------------------------------------------
def probe_workable():
    print("\n=== WORKABLE (widget API) ===", flush=True)
    slug_set = [
        # From track2 tests/providers/workable.test.mjs fixtures:
        "acme-widgets", "exampleco", "semios", "huggingface",
        # Try common customers from public knowledge:
        "wise", "wise-payments", "wise-transfer", "klarna", "sumup",
        "typeform", "hotjar", "wrike", "veed", "deepgram", "n26",
        "revolut", "monzo", "klaviyo", "laterpay", "neuralink",
        # Smaller — common patterns
        "form3", "pleo", "lilium", "jobandtalent",
    ]
    out = []
    for slug in slug_set:
        url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
        t0 = time.time()
        try:
            r = cffi.get(url, headers={**HEADERS_BROWSER, "Origin": "https://apply.workable.com", "Referer": f"https://apply.workable.com/{slug}/"}, impersonate="chrome131", timeout=15)
            status = r.status_code
            size = len(r.content)
            preview = r.text[:300].replace("\n", " ")
            job_count = None
            if r.headers.get("content-type", "").startswith("application/json"):
                try:
                    payload = r.json()
                    if isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
                        job_count = len(payload["jobs"])
                except Exception:
                    pass
            ok = status == 200 and isinstance(job_count, int) and job_count > 0
            print(f"  {slug:30s} HTTP {status}  size={size:>8}  jobs={job_count}  dt={time.time()-t0:.2f}s  {'OK' if ok else '-'}", flush=True)
            out.append({
                "slug": slug,
                "url": url,
                "status": status,
                "size": size,
                "job_count": job_count,
                "duration_s": round(time.time()-t0, 2),
                "preview": preview if status != 200 else "(200, payload parse attempted)",
            })
        except Exception as e:
            print(f"  {slug:30s} ERROR: {e}", flush=True)
            out.append({"slug": slug, "url": url, "error": str(e)[:200]})
        time.sleep(0.2)
    results["workable"] = out

# ----------------------------------------------------------------------
# 2. PERSONIO — XML feed (the primary, no-auth path from T2 provider)
# ----------------------------------------------------------------------
def probe_personio():
    print("\n=== PERSONIO (XML feed) ===", flush=True)
    # Known Personio customers (DACH-heavy) from public research:
    slug_set = [
        "personio",             # Personio itself — already probed = 815 bytes (confirmed works)
        "sennder", "lilium", "carmiq", "tamos", "cumminz",
        "kiwigrid", "h2".ljust(2,"_"), "wefox", "tier",
        "stylight", "lemonbird", "sumup", "personio-dachser",
        "fundingcircle", "checkmobile", "autopay", "instaflie",
        "apple-carplay", "fleethub", "klarna", "neuralink-de",
        "jokr", "data-artisans", "kustomer", "aleph", "sennder-de",
        # Generic patterns
        "jobs", "demo",
    ]
    out = []
    for slug in slug_set:
        url = f"https://{slug}.jobs.personio.de/xml"
        t0 = time.time()
        try:
            r = cffi.get(url, headers=HEADERS_BROWSER, impersonate="chrome131", timeout=15)
            status = r.status_code
            size = len(r.content)
            # Count <position> tags in the body
            position_count = r.text.count("<position") if "position" in r.text else 0
            ok = status == 200 and position_count > 0
            preview = r.text[:200].replace("\n", " ")
            print(f"  {slug:30s} HTTP {status}  size={size:>8}  positions={position_count}  dt={time.time()-t0:.2f}s  {'OK' if ok else '-'}", flush=True)
            out.append({
                "slug": slug,
                "url": url,
                "status": status,
                "size": size,
                "position_count": position_count,
                "duration_s": round(time.time()-t0, 2),
                "preview": preview,
            })
        except Exception as e:
            print(f"  {slug:30s} ERROR: {e}", flush=True)
            out.append({"slug": slug, "url": url, "error": str(e)[:200]})
        time.sleep(0.2)
    results["personio"] = out

# ----------------------------------------------------------------------
# 3. WELLFOUND — direct HTML scrape (formerly AngelList Talent)
# ----------------------------------------------------------------------
def probe_wellfound():
    print("\n=== WELLFOUND (direct scrape, no API) ===", flush=True)
    urls = [
        ("wellfound_jobs_search_remote_engineer", "https://wellfound.com/jobs?q=software+engineer&remote=true"),
        ("wellfound_jobs_root", "https://wellfound.com/jobs"),
        ("wellfound_role_search_engineer", "https://wellfound.com/role/r/software-engineer"),
        ("wellfound_api_v1_jobs_legacy", "https://angel.co/api/v1/jobs"),
        ("wellfound_jobs_api_search_legacy", "https://angel.co/api/v1/jobs/search"),
    ]
    out = []
    for label, url in urls:
        t0 = time.time()
        try:
            r = cffi.get(url, headers=HEADERS_BROWSER, impersonate="chrome131", timeout=20, allow_redirects=True)
            status = r.status_code
            size = len(r.content)
            content_type = r.headers.get("content-type", "")
            final_url = str(r.url)
            # Heuristic for job cards in HTML
            job_markers = sum([
                r.text.count("job-post"),
                r.text.count("JobPosting"),
                r.text.count('"title":"'),
                r.text.count('data-job-id='),
            ])
            # Anti-bot signals
            signals = []
            for sig in ["captcha", "cloudflare", "Verify you are human", "Press & Hold", "Access denied", "blocked", "Ray ID"]:
                if sig.lower() in r.text.lower():
                    signals.append(sig)
            ok = status == 200 and size > 10000 and not signals
            print(f"  {label:45s} HTTP {status}  size={size:>8}  markers={job_markers}  signals={signals}  final={final_url[:80]}", flush=True)
            out.append({
                "label": label,
                "url": url,
                "status": status,
                "size": size,
                "content_type": content_type,
                "final_url": final_url,
                "job_markers": job_markers,
                "anti_bot_signals": signals,
                "duration_s": round(time.time()-t0, 2),
                "preview": r.text[:300].replace("\n", " "),
            })
        except Exception as e:
            print(f"  {label:45s} ERROR: {e}", flush=True)
            out.append({"label": label, "url": url, "error": str(e)[:200]})
        time.sleep(0.5)
    results["wellfound"] = out

# ----------------------------------------------------------------------
# 4. OTTA — direct scrape
# ----------------------------------------------------------------------
def probe_otta():
    print("\n=== OTTA (direct scrape) ===", flush=True)
    urls = [
        ("otta_root", "https://otta.com/"),
        ("otta_jobs_search", "https://otta.com/jobs?engineer"),
        ("otta_search_remote_engineer", "https://otta.com/search?keywords=software+engineer&remote=remote"),
        ("otta_api_jobs_legacy", "https://otta.com/api/jobs"),
        ("otta_api_v2_jobs_legacy", "https://api.otta.com/v2/jobs"),
        ("otta_jobs_role_engineer", "https://otta.com/jobs/role/engineer"),
    ]
    out = []
    for label, url in urls:
        t0 = time.time()
        try:
            r = cffi.get(url, headers=HEADERS_BROWSER, impersonate="chrome131", timeout=20, allow_redirects=True)
            status = r.status_code
            size = len(r.content)
            final_url = str(r.url)
            signals = []
            for sig in ["captcha", "cloudflare", "Verify you are human", "Access denied", "blocked", "Ray ID", "shut down", "deprecated", "no longer available", "moved to"]:
                if sig.lower() in r.text.lower():
                    signals.append(sig)
            job_markers = sum([
                r.text.count("JobPosting"),
                r.text.count('"title":"'),
                r.text.count("data-job-id="),
                r.text.count('class="job-card'),
            ])
            print(f"  {label:40s} HTTP {status}  size={size:>8}  markers={job_markers}  signals={signals}  final={final_url[:80]}", flush=True)
            out.append({
                "label": label,
                "url": url,
                "status": status,
                "size": size,
                "final_url": final_url,
                "job_markers": job_markers,
                "anti_bot_signals": signals,
                "duration_s": round(time.time()-t0, 2),
                "preview": r.text[:300].replace("\n", " "),
            })
        except Exception as e:
            print(f"  {label:40s} ERROR: {e}", flush=True)
            out.append({"label": label, "url": url, "error": str(e)[:200]})
        time.sleep(0.5)
    results["otta"] = out

# ----------------------------------------------------------------------
# 5. SIGN-UP-REQUIRED AGGREGATORS — probe endpoints WITHOUT keys first
# (to see whether they reveal anything; and to confirm the auth wall)
# Plus probe alternate no-auth endpoints if they exist.
# ----------------------------------------------------------------------
def probe_signup_aggregators():
    print("\n=== SIGN-UP-REQUIRED AGGREGATORS (probe without keys, confirm wall) ===", flush=True)
    out = {}

    # ----- 5.1 Adzuna -----
    adzuna_urls = [
        ("adzuna_spec_doc", "https://api.adzuna.com/v1/doc"),
        ("adzuna_countries", "https://api.adzuna.com/v1/api/countries?app_id=test&app_key=test"),
        ("adzuna_jobs_us_test", "https://api.adzuna.com/v1/api/jobs/us/search/1?app_id=test&app_key=test&what=software+engineer&results_per_page=5"),
        ("adzuna_jobs_gb_test", "https://api.adzuna.com/v1/api/jobs/gb/search/1?app_id=test&app_key=test&results_per_page=5"),
        ("adzuna_root", "https://api.adzuna.com/"),
    ]
    out["adzuna"] = []
    for label, url in adzuna_urls:
        t0 = time.time()
        try:
            r = cffi.get(url, headers=HEADERS_BROWSER, impersonate="chrome131", timeout=15, allow_redirects=True)
            preview = r.text[:200].replace("\n", " ")
            print(f"  {label:30s} HTTP {r.status_code}  size={len(r.content):>6}  dt={time.time()-t0:.2f}s  preview={preview[:120]}", flush=True)
            out["adzuna"].append({
                "label": label, "url": url,
                "status": r.status_code, "size": len(r.content),
                "content_type": r.headers.get("content-type", ""),
                "final_url": str(r.url),
                "preview": preview,
                "duration_s": round(time.time()-t0, 2),
            })
        except Exception as e:
            print(f"  {label:30s} ERROR: {e}", flush=True)
            out["adzuna"].append({"label": label, "url": url, "error": str(e)[:200]})
        time.sleep(0.3)

    # ----- 5.2 JSearch / RapidAPI -----
    jsearch_urls = [
        ("jsearch_root", "https://jsearch.p.rapidapi.com/"),
        ("jsearch_search", "https://jsearch.p.rapidapi.com/search?query=software+engineer&page=1&num_pages=1"),
        ("jsearch_search_no_key", "https://jsearch.p.rapidapi.com/search?query=software+engineer"),
    ]
    out["jsearch"] = []
    for label, url in jsearch_urls:
        t0 = time.time()
        try:
            r = cffi.get(url, headers=HEADERS_BROWSER, impersonate="chrome131", timeout=15, allow_redirects=True)
            preview = r.text[:200].replace("\n", " ")
            print(f"  {label:30s} HTTP {r.status_code}  size={len(r.content):>6}  dt={time.time()-t0:.2f}s  preview={preview[:120]}", flush=True)
            out["jsearch"].append({
                "label": label, "url": url,
                "status": r.status_code, "size": len(r.content),
                "content_type": r.headers.get("content-type", ""),
                "preview": preview,
                "duration_s": round(time.time()-t0, 2),
            })
        except Exception as e:
            print(f"  {label:30s} ERROR: {e}", flush=True)
            out["jsearch"].append({"label": label, "url": url, "error": str(e)[:200]})
        time.sleep(0.3)

    # ----- 5.3 USAJobs -----
    usajobs_urls = [
        # The required header is "Authorization-Key: <email>" — see if any non-Akamai endpoint exists:
        ("usajobs_search_no_auth", "https://data.usajobs.gov/api/search?Keyword=engineer&ResultsPerPage=5"),
        ("usajobs_search_with_dummy_email", "https://data.usajobs.gov/api/search?Keyword=engineer&ResultsPerPage=5"),
        ("usajobs_search_hiring_path", "https://data.usajobs.gov/api/hiringpath"),
        ("usajobs_search_series", "https://data.usajobs.gov/api/series"),
        ("usajobs_root", "https://data.usajobs.gov/"),
        ("usajobs_root_search_page", "https://www.usajobs.gov/Search/Results?a=DE&k=software"),
    ]
    out["usajobs"] = []
    for label, url in usajobs_urls:
        t0 = time.time()
        try:
            # For label 'usajobs_search_with_dummy_email' — send the documented header pattern:
            headers = dict(HEADERS_BROWSER)
            if label == "usajobs_search_with_dummy_email":
                headers["Host"] = "data.usajobs.gov"
                headers["User-Agent"] = "validator@example.com"
                headers["Authorization-Key"] = "validator@example.com"
                headers["Accept"] = "application/json"
            elif "data.usajobs.gov/api" in url:
                headers["Host"] = "data.usajobs.gov"
                headers["User-Agent"] = "validator@example.com"
                headers["Accept"] = "application/json"
            r = cffi.get(url, headers=headers, impersonate="chrome131", timeout=15, allow_redirects=True)
            preview = r.text[:200].replace("\n", " ")
            # Check for job markers in the public search page
            markers = r.text.count("jobResult") + r.text.count("usajobs.gov/job/")
            print(f"  {label:40s} HTTP {r.status_code}  size={len(r.content):>6}  markers={markers}  dt={time.time()-t0:.2f}s  preview={preview[:120]}", flush=True)
            out["usajobs"].append({
                "label": label, "url": url,
                "status": r.status_code, "size": len(r.content),
                "content_type": r.headers.get("content-type", ""),
                "final_url": str(r.url),
                "job_markers": markers,
                "preview": preview,
                "duration_s": round(time.time()-t0, 2),
            })
        except Exception as e:
            print(f"  {label:40s} ERROR: {e}", flush=True)
            out["usajobs"].append({"label": label, "url": url, "error": str(e)[:200]})
        time.sleep(0.3)

    # ----- 5.4 Findwork -----
    findwork_urls = [
        ("findwork_jobs_no_auth", "https://findwork.dev/api/jobs/?search=software"),
        ("findwork_root", "https://findwork.dev/"),
        ("findwork_api_root", "https://findwork.dev/api/"),
        ("findwork_jobs_with_dummy_token", "https://findwork.dev/api/jobs/?search=software"),
    ]
    out["findwork"] = []
    for label, url in findwork_urls:
        t0 = time.time()
        try:
            headers = dict(HEADERS_BROWSER)
            if label == "findwork_jobs_with_dummy_token":
                headers["Authorization"] = "Token dummy_key_for_validation"
                headers["Accept"] = "application/json"
            r = cffi.get(url, headers=headers, impersonate="chrome131", timeout=15, allow_redirects=True)
            preview = r.text[:200].replace("\n", " ")
            print(f"  {label:40s} HTTP {r.status_code}  size={len(r.content):>6}  dt={time.time()-t0:.2f}s  preview={preview[:120]}", flush=True)
            out["findwork"].append({
                "label": label, "url": url,
                "status": r.status_code, "size": len(r.content),
                "content_type": r.headers.get("content-type", ""),
                "preview": preview,
                "duration_s": round(time.time()-t0, 2),
            })
        except Exception as e:
            print(f"  {label:40s} ERROR: {e}", flush=True)
            out["findwork"].append({"label": label, "url": url, "error": str(e)[:200]})
        time.sleep(0.3)

    # ----- 5.5 Jooble -----
    jooble_urls = [
        ("jooble_about_page", "https://jooble.org/api/about"),
        ("jooble_root", "https://jooble.org/"),
        ("jooble_search_engineer_us", "https://us.jooble.org/SearchResult?p=1&rgn=ac&q=software+engineer&urg=us"),
        ("jooble_search_engineer_us_html", "https://us.jooble.org/SearchResult?p=1&q=software+engineer"),
    ]
    out["jooble"] = []
    for label, url in jooble_urls:
        t0 = time.time()
        try:
            r = cffi.get(url, headers=HEADERS_BROWSER, impersonate="chrome131", timeout=15, allow_redirects=True)
            preview = r.text[:200].replace("\n", " ")
            # Job-card markers in HTML
            markers = r.text.count("job-card") + r.text.count("vacancy") + r.text.count("JobPosting")
            signals = [s for s in ["captcha", "cloudflare", "Verify you are human", "Access denied", "blocked", "Ray ID"] if s.lower() in r.text.lower()]
            print(f"  {label:40s} HTTP {r.status_code}  size={len(r.content):>6}  markers={markers}  signals={signals}  dt={time.time()-t0:.2f}s", flush=True)
            out["jooble"].append({
                "label": label, "url": url,
                "status": r.status_code, "size": len(r.content),
                "content_type": r.headers.get("content-type", ""),
                "final_url": str(r.url),
                "job_markers": markers,
                "anti_bot_signals": signals,
                "preview": preview,
                "duration_s": round(time.time()-t0, 2),
            })
        except Exception as e:
            print(f"  {label:40s} ERROR: {e}", flush=True)
            out["jooble"].append({"label": label, "url": url, "error": str(e)[:200]})
        time.sleep(0.3)

    # ----- 5.6 Careerjet -----
    careerjet_urls = [
        ("careerjet_partners", "https://www.careerjet.com/partners/"),
        ("careerjet_search_results", "https://www.careerjet.com/search/results.html?sort=relevance&s=software+engineer&l=us"),
        ("careerjet_root", "https://www.careerjet.com/"),
        ("careerjet_search_us", "https://www.careerjet.com/search/jobs?s=software+engineer&l=United+States"),
        ("careerjet_api_v2_jobs", "https://www.careerjet.com/partners/api/v2/search"),
        ("careerjet_xml_feed", "https://www.careerjet.com/rss/rss.html?s=software+engineer&l=United+States"),
    ]
    out["careerjet"] = []
    for label, url in careerjet_urls:
        t0 = time.time()
        try:
            r = cffi.get(url, headers=HEADERS_BROWSER, impersonate="chrome131", timeout=15, allow_redirects=True)
            preview = r.text[:200].replace("\n", " ")
            markers = r.text.count("job-item") + r.text.count("JobPosting") + r.text.count("class=\"job ")
            # RSS feed markers
            rss_markers = r.text.count("<item") + r.text.count("<title>")
            print(f"  {label:40s} HTTP {r.status_code}  size={len(r.content):>6}  html_markers={markers}  rss_markers={rss_markers}  dt={time.time()-t0:.2f}s", flush=True)
            out["careerjet"].append({
                "label": label, "url": url,
                "status": r.status_code, "size": len(r.content),
                "content_type": r.headers.get("content-type", ""),
                "final_url": str(r.url),
                "html_job_markers": markers,
                "rss_markers": rss_markers,
                "preview": preview,
                "duration_s": round(time.time()-t0, 2),
            })
        except Exception as e:
            print(f"  {label:40s} ERROR: {e}", flush=True)
            out["careerjet"].append({"label": label, "url": url, "error": str(e)[:200]})
        time.sleep(0.3)

    results["signup_aggregators"] = out

# ----------------------------------------------------------------------
# RUN ALL
# ----------------------------------------------------------------------
if __name__ == "__main__":
    probe_workable()
    probe_personio()
    probe_wellfound()
    probe_otta()
    probe_signup_aggregators()
    out_path = OUT_DIR / "results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n✓ Wrote {out_path}")
    print(f"  workable rows:        {len(results.get('workable', []))}")
    print(f"  personio rows:       {len(results.get('personio', []))}")
    print(f"  wellfound rows:      {len(results.get('wellfound', []))}")
    print(f"  otta rows:           {len(results.get('otta', []))}")
    print(f"  signup aggregators:  adzuna={len(results.get('signup_aggregators',{}).get('adzuna',[]))} jsearch={len(results.get('signup_aggregators',{}).get('jsearch',[]))} usajobs={len(results.get('signup_aggregators',{}).get('usajobs',[]))} findwork={len(results.get('signup_aggregators',{}).get('findwork',[]))} jooble={len(results.get('signup_aggregators',{}).get('jooble',[]))} careerjet={len(results.get('signup_aggregators',{}).get('careerjet',[]))}")
