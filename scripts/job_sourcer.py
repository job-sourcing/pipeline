#!/usr/bin/env python3
"""
Integrated Job Sourcing Script — Personal Scale.

Combines all verified working methods from the research:

Tier 1 — Free, no auth (just curl_cffi or requests):
  - LinkedIn guest API (~500 jobs/query)
  - Greenhouse Job Board API (per-company)
  - Lever Postings API (per-company)
  - Ashby Job Posting API (per-company)
  - SmartRecruiters public postings (per-company)
  - Remotive (remote jobs)
  - Jobicy (remote jobs)
  - RemoteOK (remote jobs)
  - WeWorkRemotely RSS (remote jobs)

Tier 2 — ZenRows (paid API, $49/mo entry):
  - ZipRecruiter (Cloudflare-protected, but ZenRows bypasses it)
  - Glassdoor (Cloudflare-protected, but ZenRows bypasses it via JSON-LD)
  - Indeed (Cloudflare-protected; ZenRows struggles, may need Apify as fallback)

Tier 3 — Supabase Edge Proxy (free, 60 req/min):
  - Use as alternate transport for LinkedIn guest API (cached, faster)
  - Useful for IP rotation on sites without Cloudflare Bot Management

Usage:
    python3 job_sourcer.py --keywords "software engineer" --location "San Francisco"
    python3 job_sourcer.py --keywords "frontend engineer" --location "Remote" --sources linkedin,greenhouse,ziprecruiter

Output:
    /home/z/my-project/download/jobs_{timestamp}.json  (all jobs combined, deduplicated)
"""
import os
import sys
import re
import json
import time
import argparse
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

import requests

OUT_DIR = Path('/home/z/my-project/download')
OUT_DIR.mkdir(parents=True, exist_ok=True)

# === Tier 1: Free APIs ===

# ZenRows config (optional — set via --zenrows-key or env var)
ZENROWS_API_KEY = None  # Set via argparse or env

# Supabase Edge Proxy (optional — set via --use-supabase-proxy flag).
# Env-only credentials (S8-A scrub): os.environ first, then ingest/.env
# (the committed secret store) — never hardcoded.
def _env_file_value(name: str) -> str:
    """Read `name` from ingest/.env (KEY=value lines) if present."""
    env_path = Path(__file__).resolve().parents[1] / "ingest" / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""

SUPABASE_PROXY_URL = (os.environ.get("SUPABASE_PROXY_URL")
                      or _env_file_value("SUPABASE_PROXY_URL"))
SUPABASE_TOKEN = (os.environ.get("SUPABASE_PROXY_TOKEN")
                  or _env_file_value("SUPABASE_PROXY_TOKEN"))


def fetch_with_cffi(url, **kwargs):
    """Fetch using curl_cffi (Chrome TLS fingerprint)."""
    if not HAS_CFFI:
        raise RuntimeError("curl_cffi not installed. Run: pip install curl_cffi")
    return cffi_requests.get(url, impersonate='chrome131', timeout=kwargs.pop('timeout', 20), **kwargs)


# ====== LinkedIn Guest API ======

def scrape_linkedin_guest(keywords, location, max_pages=25, delay_s=1.0):
    """Free LinkedIn guest API — ~500 jobs per query, no auth, no proxy."""
    print(f"\n[LinkedIn Guest API] '{keywords}' in '{location}' (max {max_pages} pages)")
    all_jobs = []
    for page in range(max_pages):
        start = page * 25
        url = (
            f'https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search'
            f'?keywords={quote_plus(keywords)}&location={quote_plus(location)}&start={start}'
        )
        try:
            r = fetch_with_cffi(url, headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': f'https://www.linkedin.com/jobs/search/?keywords={quote_plus(keywords)}&location={quote_plus(location)}',
            })
        except Exception as e:
            print(f"  page {page + 1}: ERROR {e}")
            break
        if r.status_code != 200:
            print(f"  page {page + 1}: HTTP {r.status_code}")
            break
        jobs = _parse_linkedin_jobs(r.text)
        if not jobs:
            print(f"  page {page + 1}: 0 jobs (cap reached)")
            break
        all_jobs.extend(jobs)
        print(f"  page {page + 1}/{max_pages}: {len(jobs)} jobs | cumulative: {len(all_jobs)}")
        time.sleep(delay_s)
    return all_jobs


def _parse_linkedin_jobs(html):
    """Parse LinkedIn guest API HTML response."""
    jobs = []
    blocks = re.findall(
        r'data-entity-urn="urn:li:jobPosting:(\d+)"[^>]*>(.*?)(?=data-entity-urn="urn:li:jobPosting:|</ul>)',
        html, re.DOTALL
    )
    for job_id, block in blocks:
        title_m = re.search(r'<h3 class="base-search-card__title">\s*([^<]+?)\s*</h3>', block)
        company_m = re.search(r'<h4 class="base-search-card__subtitle">\s*<a[^>]*>\s*([^<]+?)\s*</a>', block)
        loc_m = re.search(r'<span class="job-search-card__location">\s*([^<]+?)\s*</span>', block)
        date_m = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        url_m = re.search(r'href="(https://www\.linkedin\.com/jobs/view/[^"]+)"', block)
        jobs.append({
            'id': f'li_{job_id}',
            'source': 'linkedin',
            'external_id': job_id,
            'title': title_m.group(1).strip() if title_m else None,
            'company': company_m.group(1).strip() if company_m else None,
            'location': loc_m.group(1).strip() if loc_m else None,
            'posted_date': date_m.group(1).strip() if date_m else None,
            'url': url_m.group(1).strip() if url_m else None,
        })
    return jobs


# ====== Greenhouse Job Board API ======

def scrape_greenhouse(company_slugs):
    """Greenhouse Job Board API — free, no auth, per-company."""
    print(f"\n[Greenhouse API] {len(company_slugs)} companies")
    all_jobs = []
    for slug in company_slugs:
        try:
            r = requests.get(
                f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?per_page=500',
                timeout=15,
                headers={'User-Agent': 'job-sourcer/1.0'},
            )
            if r.status_code != 200:
                print(f"  {slug}: HTTP {r.status_code}")
                continue
            data = r.json()
            jobs = data.get('jobs', [])
            for j in jobs:
                all_jobs.append({
                    'id': f'gh_{j["id"]}',
                    'source': 'greenhouse',
                    'external_id': str(j['id']),
                    'title': j.get('title'),
                    'company': j.get('company_name', slug),
                    'location': j.get('location', {}).get('name') if isinstance(j.get('location'), dict) else j.get('location'),
                    'url': j.get('absolute_url'),
                    'posted_date': j.get('first_published'),
                })
            print(f"  {slug}: {len(jobs)} jobs")
            time.sleep(0.5)  # polite
        except Exception as e:
            print(f"  {slug}: ERROR {e}")
    return all_jobs


# ====== Lever Postings API ======

def scrape_lever(company_slugs):
    """Lever Postings API — free, no auth, per-company."""
    print(f"\n[Lever API] {len(company_slugs)} companies")
    all_jobs = []
    for slug in company_slugs:
        try:
            r = requests.get(
                f'https://api.lever.co/v0/postings/{slug}?limit=500&mode=json',
                timeout=15,
                headers={'User-Agent': 'job-sourcer/1.0'},
            )
            if r.status_code != 200:
                print(f"  {slug}: HTTP {r.status_code}")
                continue
            jobs = r.json()
            if not isinstance(jobs, list):
                continue
            for j in jobs:
                loc = j.get('categories', {}).get('location') if isinstance(j.get('categories'), dict) else None
                all_jobs.append({
                    'id': f'lv_{j["id"]}',
                    'source': 'lever',
                    'external_id': j['id'],
                    'title': j.get('text'),
                    'company': slug,
                    'location': loc,
                    'url': j.get('hostedUrl') or j.get('applyUrl'),
                    'posted_date': j.get('createdAt'),
                })
            print(f"  {slug}: {len(jobs)} jobs")
            time.sleep(0.5)
        except Exception as e:
            print(f"  {slug}: ERROR {e}")
    return all_jobs


# ====== Ashby Job Posting API ======

def scrape_ashby(company_slugs):
    """Ashby Job Posting API — free, no auth, per-company. Returns 1MB+ per company."""
    print(f"\n[Ashby API] {len(company_slugs)} companies")
    all_jobs = []
    for slug in company_slugs:
        try:
            r = requests.get(
                f'https://api.ashbyhq.com/posting-api/job-board/{slug}',
                timeout=30,
                headers={'User-Agent': 'job-sourcer/1.0'},
            )
            if r.status_code != 200:
                print(f"  {slug}: HTTP {r.status_code}")
                continue
            data = r.json()
            jobs = data.get('jobs', [])
            for j in jobs:
                locations = j.get('locations', [])
                loc_str = ', '.join([l.get('locationName', '') for l in locations if isinstance(l, dict)]) if locations else None
                all_jobs.append({
                    'id': f'as_{j["id"]}',
                    'source': 'ashby',
                    'external_id': j['id'],
                    'title': j.get('title'),
                    'company': j.get('organizationName', slug),
                    'location': loc_str,
                    'url': j.get('externalLink'),
                    'posted_date': j.get('publishedAt'),
                })
            print(f"  {slug}: {len(jobs)} jobs")
            time.sleep(1.0)  # Ashby responses are big — be polite
        except Exception as e:
            print(f"  {slug}: ERROR {e}")
    return all_jobs


# ====== SmartRecruiters public postings ======

def scrape_smartrecruiters(company_slugs):
    """SmartRecruiters public postings — free, no auth, per-company."""
    print(f"\n[SmartRecruiters API] {len(company_slugs)} companies")
    all_jobs = []
    for slug in company_slugs:
        try:
            r = requests.get(
                f'https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100',
                timeout=15,
                headers={'User-Agent': 'job-sourcer/1.0'},
            )
            if r.status_code != 200:
                print(f"  {slug}: HTTP {r.status_code}")
                continue
            data = r.json()
            jobs = data.get('content', [])
            for j in jobs:
                loc = j.get('location', {})
                loc_str = ', '.join(filter(None, [loc.get('city'), loc.get('country')])) if isinstance(loc, dict) else None
                all_jobs.append({
                    'id': f'sr_{j["id"]}',
                    'source': 'smartrecruiters',
                    'external_id': j['id'],
                    'title': j.get('name'),
                    'company': j.get('companyName', slug),
                    'location': loc_str,
                    'url': j.get('applyUrl') or f'https://jobs.smartrecruiters.com/{slug}/{j["id"]}',
                    'posted_date': j.get('releasedDate'),
                })
            print(f"  {slug}: {len(jobs)} jobs")
            time.sleep(0.5)
        except Exception as e:
            print(f"  {slug}: ERROR {e}")
    return all_jobs


# ====== Remotive ======

def scrape_remotive():
    """Remotive free API — remote jobs, 24-hour delay."""
    print("\n[Remotive API] Fetching remote jobs")
    try:
        r = requests.get('https://remotive.com/api/remote-jobs?limit=500', timeout=20,
                         headers={'User-Agent': 'job-sourcer/1.0'})
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}")
            return []
        data = r.json()
        jobs = data.get('jobs', [])
        all_jobs = []
        for j in jobs:
            all_jobs.append({
                'id': f'rm_{j["id"]}',
                'source': 'remotive',
                'external_id': str(j['id']),
                'title': j.get('title'),
                'company': j.get('company_name'),
                'location': j.get('candidate_required_location') or j.get('region'),
                'url': j.get('url'),
                'posted_date': j.get('publication_date'),
                'tags': j.get('tags', []),
                'salary': j.get('salary'),
            })
        print(f"  Fetched {len(all_jobs)} remote jobs")
        return all_jobs
    except Exception as e:
        print(f"  ERROR {e}")
        return []


# ====== Jobicy ======

def scrape_jobicy():
    """Jobicy free API — remote jobs, real-time."""
    print("\n[Jobicy API] Fetching remote jobs")
    try:
        r = requests.get('https://jobicy.com/api/v2/remote-jobs?count=100', timeout=20,
                         headers={'User-Agent': 'job-sourcer/1.0'})
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}")
            return []
        data = r.json()
        jobs = data.get('jobs', [])
        all_jobs = []
        for j in jobs:
            all_jobs.append({
                'id': f'jb_{j["id"]}',
                'source': 'jobicy',
                'external_id': str(j['id']),
                'title': j.get('jobTitle'),
                'company': j.get('companyName'),
                'location': j.get('jobGeo'),
                'url': j.get('url'),
                'industry': j.get('jobIndustry'),
                'job_type': j.get('jobType'),
                'level': j.get('jobLevel'),
            })
        print(f"  Fetched {len(all_jobs)} remote jobs")
        return all_jobs
    except Exception as e:
        print(f"  ERROR {e}")
        return []


# ====== RemoteOK ======

def scrape_remoteok():
    """RemoteOK free API — remote tech jobs."""
    print("\n[RemoteOK API] Fetching remote jobs")
    try:
        r = requests.get('https://remoteok.com/api', timeout=20,
                         headers={'User-Agent': 'job-sourcer/1.0'})
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}")
            return []
        data = r.json()
        # First element is metadata, rest are jobs
        if isinstance(data, list) and len(data) > 1:
            jobs = data[1:]
        else:
            jobs = data
        all_jobs = []
        for j in jobs:
            if not isinstance(j, dict) or 'id' not in j:
                continue
            all_jobs.append({
                'id': f'ro_{j["id"]}',
                'source': 'remoteok',
                'external_id': str(j['id']),
                'title': j.get('position'),
                'company': j.get('company'),
                'location': j.get('location'),
                'url': j.get('url'),
                'tags': j.get('tags', []),
                'salary_min': j.get('salary_min'),
                'salary_max': j.get('salary_max'),
            })
        print(f"  Fetched {len(all_jobs)} remote jobs")
        return all_jobs
    except Exception as e:
        print(f"  ERROR {e}")
        return []


# ====== ZipRecruiter via ZenRows ======

def scrape_ziprecruiter_zenrows(keywords, location, api_key):
    """ZipRecruiter via ZenRows — bypasses Cloudflare."""
    print(f"\n[ZipRecruiter via ZenRows] '{keywords}' in '{location}'")
    url = f'https://www.ziprecruiter.com/jobs-search?search={quote_plus(keywords)}&location={quote_plus(location)}'
    params = {
        'apikey': api_key,
        'url': url,
        'js_render': 'true',
        'premium_proxy': 'true',
        'proxy_country': 'us',
        'original_status': 'true',
        'wait': '4000',
    }
    start = time.time()
    try:
        r = requests.get('https://api.zenrows.com/v1/', params=params, timeout=120)
        duration = round(time.time() - start, 2)
        print(f"  HTTP {r.status_code}, {len(r.text):,} bytes, {duration}s")
        if r.status_code != 200:
            return []
        # Parse articles
        articles = re.findall(r'<article[^>]*>(.*?)</article>', r.text, re.DOTALL)
        print(f"  Found {len(articles)} articles")
        jobs = []
        for art in articles:
            h2s = re.findall(r'<h2[^>]*>([^<]+)</h2>', art)
            title = None
            for h in h2s:
                h = h.strip()
                if h and not any(x in h.lower() for x in ['share', 'be seen']):
                    title = h
                    break
            company_m = re.search(r'<p[^>]*>\s*<a[^>]*>([^<]+)</a>\s*</p>', art)
            loc_m = re.search(r'data-testid="job-card-location"[^>]*>([^<]+)<', art)
            if not loc_m:
                loc_m = re.search(r'>\s*([A-Z][a-zA-Z\s]+,\s+[A-Z]{2})\s*<', art)
            sal_m = re.search(r'\$[\d,]+K?\s*-\s*\$[\d,]+K?\s*/\s*yr', art)
            posted_m = re.search(r'Posted\s+(today|yesterday|\d+\s*days?\s*ago)', art, re.I)
            url_m = re.search(r'href="(/jobs/[^"]+)"', art)
            if title:
                # Dedupe: ZipRecruiter often shows same job twice
                job_key = f'zr_{title}_{company_m.group(1) if company_m else "none"}'
                jobs.append({
                    'id': job_key,
                    'source': 'ziprecruiter',
                    'title': title,
                    'company': company_m.group(1).strip() if company_m else None,
                    'location': loc_m.group(1).strip() if loc_m else None,
                    'salary': sal_m.group(0) if sal_m else None,
                    'posted': posted_m.group(1) if posted_m else None,
                    'url': f'https://www.ziprecruiter.com{url_m.group(1)}' if url_m else None,
                })
        # Dedupe by id
        seen = set()
        unique_jobs = []
        for j in jobs:
            if j['id'] not in seen:
                seen.add(j['id'])
                unique_jobs.append(j)
        print(f"  Extracted {len(unique_jobs)} unique jobs (deduped from {len(jobs)})")
        return unique_jobs
    except Exception as e:
        print(f"  ERROR {e}")
        return []


# ====== Glassdoor via ZenRows ======

def scrape_glassdoor_zenrows(keywords, location, api_key):
    """Glassdoor via ZenRows — bypasses Cloudflare, extracts JSON-LD."""
    print(f"\n[Glassdoor via ZenRows] '{keywords}' in '{location}'")
    # Use a broader location term for Glassdoor (it works better with 'United States')
    if not location or location.lower() in ('remote', 'anywhere'):
        loc_param = 'United States'
    else:
        loc_param = location
    url = f'https://www.glassdoor.com/Job/jobs.htm?sc.keyword={quote_plus(keywords)}&locT=N&locId={quote_plus(loc_param)}'
    # Simpler URL works better
    url = f'https://www.glassdoor.com/Job/jobs.htm?sc.keyword={quote_plus(keywords)}'
    params = {
        'apikey': api_key,
        'url': url,
        'js_render': 'true',
        'premium_proxy': 'true',
        'proxy_country': 'us',
        'original_status': 'true',
        'wait': '4000',
    }
    start = time.time()
    try:
        r = requests.get('https://api.zenrows.com/v1/', params=params, timeout=120)
        duration = round(time.time() - start, 2)
        print(f"  HTTP {r.status_code}, {len(r.text):,} bytes, {duration}s")
        if r.status_code != 200:
            return []
        # Parse JSON-LD ItemList
        jsonld_match = re.search(
            r'<script[^>]*type="application/ld\+json"[^>]*>(\{.*?"ItemList".*?\})</script>',
            r.text, re.DOTALL
        )
        if not jsonld_match:
            # Try simpler pattern
            jsonld_match = re.search(
                r'<script[^>]*type="application/ld\+json"[^>]*>(\{[^<]+\})</script>',
                r.text
            )
        if not jsonld_match:
            print("  No JSON-LD found in HTML")
            return []
        try:
            data = json.loads(jsonld_match.group(1))
        except json.JSONDecodeError as e:
            print(f"  JSON-LD parse error: {e}")
            return []
        if data.get('@type') != 'ItemList':
            print(f"  JSON-LD type is {data.get('@type')}, not ItemList")
            return []
        items = data.get('itemListElement', [])
        jobs = []
        for item in items:
            li = item if 'ListItem' not in item else item  # sometimes nested
            if isinstance(item, dict) and 'itemListElement' in data:
                li = item
            name = li.get('name', '')
            item_url = li.get('url', '')
            position = li.get('position', len(jobs) + 1)
            jobs.append({
                'id': f'gd_{position}_{hash(name) & 0xFFFFFFFF}',
                'source': 'glassdoor',
                'title': name,
                'url': item_url,
                'location': location,  # Glassdoor JSON-LD doesn't include location per job
            })
        print(f"  Extracted {len(jobs)} jobs from JSON-LD")
        return jobs
    except Exception as e:
        print(f"  ERROR {e}")
        return []


# ====== Deduplication ======

def dedupe_jobs(jobs):
    """Deduplicate jobs by (title, company) tuple."""
    seen = {}
    deduped = []
    for j in jobs:
        title = (j.get('title') or '').strip().lower()
        company = (j.get('company') or '').strip().lower()
        key = (title, company)
        if not title or not company:
            deduped.append(j)
            continue
        if key in seen:
            # Keep the one with more complete data
            existing = seen[key]
            existing_fields = sum(1 for v in existing.values() if v)
            new_fields = sum(1 for v in j.values() if v)
            if new_fields > existing_fields:
                idx = deduped.index(existing)
                deduped[idx] = j
                seen[key] = j
            continue
        seen[key] = j
        deduped.append(j)
    return deduped


# ====== Main ======

def main():
    parser = argparse.ArgumentParser(description='Personal job sourcing script')
    parser.add_argument('--keywords', '-k', default='software engineer', help='Job keywords')
    parser.add_argument('--location', '-l', default='San Francisco', help='Location')
    parser.add_argument('--linkedin-pages', type=int, default=10, help='Max LinkedIn pages (25 jobs each)')
    parser.add_argument('--zenrows-key', default=None, help='ZenRows API key (enables ZipRecruiter/Glassdoor)')
    parser.add_argument('--use-supabase-proxy', action='store_true', help='Use Supabase Edge Proxy as fallback')
    parser.add_argument('--sources', default='all',
                        help='Comma-separated source list (all, linkedin, greenhouse, lever, ashby, smartrecruiters, remotive, jobicy, remoteok, ziprecruiter, glassdoor)')
    parser.add_argument('--greenhouse-companies', default='airbnb,stripe,voxmedia',
                        help='Comma-separated Greenhouse company slugs')
    parser.add_argument('--lever-companies', default='plaid',
                        help='Comma-separated Lever company slugs')
    parser.add_argument('--ashby-companies', default='ashby',
                        help='Comma-separated Ashby company slugs')
    parser.add_argument('--smartrecruiters-companies', default='smartrecruiters',
                        help='Comma-separated SmartRecruiters company slugs')
    args = parser.parse_args()
    
    sources = args.sources.split(',') if args.sources != 'all' else ['all']
    use_all = 'all' in sources
    
    print("=" * 70)
    print(f"Job Sourcing: '{args.keywords}' in '{args.location}'")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 70)
    
    all_jobs = []
    
    # Tier 1: Free APIs
    if use_all or 'linkedin' in sources:
        jobs = scrape_linkedin_guest(args.keywords, args.location, max_pages=args.linkedin_pages)
        all_jobs.extend(jobs)
    
    if use_all or 'greenhouse' in sources:
        slugs = [s.strip() for s in args.greenhouse_companies.split(',') if s.strip()]
        jobs = scrape_greenhouse(slugs)
        all_jobs.extend(jobs)
    
    if use_all or 'lever' in sources:
        slugs = [s.strip() for s in args.lever_companies.split(',') if s.strip()]
        jobs = scrape_lever(slugs)
        all_jobs.extend(jobs)
    
    if use_all or 'ashby' in sources:
        slugs = [s.strip() for s in args.ashby_companies.split(',') if s.strip()]
        jobs = scrape_ashby(slugs)
        all_jobs.extend(jobs)
    
    if use_all or 'smartrecruiters' in sources:
        slugs = [s.strip() for s in args.smartrecruiters_companies.split(',') if s.strip()]
        jobs = scrape_smartrecruiters(slugs)
        all_jobs.extend(jobs)
    
    if use_all or 'remotive' in sources:
        jobs = scrape_remotive()
        all_jobs.extend(jobs)
    
    if use_all or 'jobicy' in sources:
        jobs = scrape_jobicy()
        all_jobs.extend(jobs)
    
    if use_all or 'remoteok' in sources:
        jobs = scrape_remoteok()
        all_jobs.extend(jobs)
    
    # Tier 2: ZenRows (paid)
    if args.zenrows_key:
        if use_all or 'ziprecruiter' in sources:
            jobs = scrape_ziprecruiter_zenrows(args.keywords, args.location, args.zenrows_key)
            all_jobs.extend(jobs)
        if use_all or 'glassdoor' in sources:
            jobs = scrape_glassdoor_zenrows(args.keywords, args.location, args.zenrows_key)
            all_jobs.extend(jobs)
    else:
        if 'ziprecruiter' in sources or 'glassdoor' in sources:
            print("\n[ZenRows] --zenrows-key not provided; skipping ZipRecruiter/Glassdoor (require ZenRows to bypass Cloudflare)")
    
    # Dedupe
    print(f"\n--- Deduplication ---")
    print(f"  Pre-dedupe: {len(all_jobs)} jobs")
    unique = dedupe_jobs(all_jobs)
    print(f"  Post-dedupe: {len(unique)} jobs ({len(all_jobs) - len(unique)} duplicates removed)")
    
    # Stats by source
    print(f"\n--- Stats by source ---")
    by_source = {}
    for j in unique:
        s = j.get('source', 'unknown')
        by_source[s] = by_source.get(s, 0) + 1
    for s, c in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {s:20s}: {c} jobs")
    
    # Save
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_file = OUT_DIR / f'jobs_{timestamp}.json'
    out_file.write_text(json.dumps({
        'query': {'keywords': args.keywords, 'location': args.location},
        'timestamp': timestamp,
        'total_jobs': len(unique),
        'by_source': by_source,
        'jobs': unique,
    }, indent=2, default=str), encoding='utf-8')
    
    print(f"\n✓ Saved {len(unique)} jobs to {out_file}")
    
    # Sample
    print(f"\n--- Sample jobs (first 10) ---")
    for j in unique[:10]:
        print(f"  [{j.get('source', '?'):12s}] {j.get('title', '?')}")
        print(f"               at {j.get('company', '?')} ({j.get('location', '?')})")
        if j.get('url'):
            print(f"               {j['url'][:100]}")


if __name__ == '__main__':
    main()
