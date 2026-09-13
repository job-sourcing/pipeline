#!/usr/bin/env python3
"""
Test ZenRows API against Cloudflare-protected job boards.
ZenRows provides: residential IPs + real browser rendering + anti-bot bypass.
"""
import json
import time
import os
from pathlib import Path
import requests

OUT_DIR = Path('/home/z/my-project/research_data/zenrows_tests')
OUT_DIR.mkdir(parents=True, exist_ok=True)

ZENROWS_API_KEY = os.environ.get("ZENROWS_API_KEY", "")
ZENROWS_BASE = "https://api.zenrows.com/v1/"

SITES = [
    ('indeed_search', 'https://www.indeed.com/jobs?q=software+engineer&l=San+Francisco%2C+CA'),
    ('glassdoor_search', 'https://www.glassdoor.com/Job/jobs.htm?sc.keyword=software+engineer'),
    ('ziprecruiter_search', 'https://www.ziprecruiter.com/jobs-search?search=software+engineer&location=San+Francisco'),
    ('linkedin_search', 'https://www.linkedin.com/jobs/search/?keywords=software+engineer&location=San+Francisco'),
]


def test_zenrows_basic(url, name):
    """Basic ZenRows GET with all the anti-bot features enabled."""
    result = {'name': name, 'strategy': 'zenrows_basic', 'url': url}
    start = time.time()
    try:
        params = {
            'url': url,
            'apikey': ZENROWS_API_KEY,
            # Anti-bot features
            'js_render': 'true',           # real headless browser
            'premium_proxy': 'true',       # residential proxies
            'proxy_country': 'us',         # US residential IPs
            'original_status': 'true',     # return original HTTP status
            'block_resources': 'false',    # don't block images/css (sometimes needed for CF)
        }
        r = requests.get(ZENROWS_BASE, params=params, timeout=90)
        result['status'] = r.status_code
        result['size'] = len(r.text)
        result['content_type'] = r.headers.get('content-type', '')
        result['duration_s'] = round(time.time() - start, 2)
        result['preview'] = r.text[:3000]
        
        # Check for challenge indicators
        challenge_indicators = [
            'just a moment', 'cloudflare', 'humans only', 'verify you are human',
            'performing security verification', 'request blocked', 'forbidden cf-waf',
            'security check', 'additional verification', 'cf-challenge',
            'sign in to view more jobs', 'authenticating'
        ]
        text_lower = r.text.lower()
        result['challenges_detected'] = [c for c in challenge_indicators if c in text_lower]
        result['blocked'] = bool(result['challenges_detected'])
        
        # Look for actual job content
        job_indicators = {
            'indeed': ['jobsearch-ResultsList', 'data-jk=', 'h2.jobTitle', 'class="result"'],
            'glassdoor': ['jobSearchResult', 'react-job-listing', 'data-test="job-link"'],
            'ziprecruiter': ['job_content', 'job-card', 'data-job-id'],
            'linkedin': ['job-search-card', 'base-search-card__title', 'urn:li:jobPosting'],
        }
        site_key = name.split('_')[0]
        if site_key in job_indicators:
            found = [i for i in job_indicators[site_key] if i in r.text]
            result['job_content_indicators'] = found
            result['has_job_content'] = bool(found)
        
        # Save HTML
        html_path = OUT_DIR / f'{name}_zenrows.html'
        html_path.write_text(r.text, encoding='utf-8')
        result['html_saved'] = str(html_path)
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:300]}'
        result['duration_s'] = round(time.time() - start, 2)
    return result


def test_zenrows_with_wait(url, name, wait_ms=5000):
    """ZenRows with custom wait for JS to render."""
    result = {'name': name, 'strategy': 'zenrows_wait', 'url': url, 'wait_ms': wait_ms}
    start = time.time()
    try:
        params = {
            'url': url,
            'apikey': ZENROWS_API_KEY,
            'js_render': 'true',
            'premium_proxy': 'true',
            'proxy_country': 'us',
            'original_status': 'true',
            'wait': str(wait_ms),  # wait for JS to render
            'block_resources': 'false',
        }
        r = requests.get(ZENROWS_BASE, params=params, timeout=120)
        result['status'] = r.status_code
        result['size'] = len(r.text)
        result['duration_s'] = round(time.time() - start, 2)
        result['preview'] = r.text[:3000]
        
        challenge_indicators = [
            'just a moment', 'cloudflare', 'humans only', 'verify you are human',
            'performing security verification', 'request blocked', 'forbidden cf-waf',
            'security check', 'additional verification'
        ]
        text_lower = r.text.lower()
        result['challenges_detected'] = [c for c in challenge_indicators if c in text_lower]
        result['blocked'] = bool(result['challenges_detected'])
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:300]}'
        result['duration_s'] = round(time.time() - start, 2)
    return result


def main():
    print("=" * 70)
    print("ZenRows API Test — Cloudflare-Protected Job Boards")
    print("=" * 70)
    
    results = []
    
    # Phase 1: Basic test with all features
    print("\n--- Phase 1: ZenRows basic (js_render + premium_proxy + us residential) ---")
    for name, url in SITES:
        print(f"\n  Testing {name}...")
        result = test_zenrows_basic(url, name)
        results.append(result)
        if 'error' in result:
            print(f"    ERROR: {result['error']}")
        else:
            print(f"    status: {result['status']}, size: {result['size']}, duration: {result['duration_s']}s")
            print(f"    challenges: {result.get('challenges_detected', [])}")
            print(f"    has_job_content: {result.get('has_job_content', 'N/A')}")
            print(f"    job_indicators_found: {result.get('job_content_indicators', [])}")
            preview = result.get('preview', '')[:200].replace('\n', ' ')
            print(f"    preview: {preview!r}")
    
    # Phase 2: With explicit wait for JS rendering
    print("\n--- Phase 2: ZenRows with 5s wait for JS rendering ---")
    # Only retry the ones that failed Phase 1
    failed_sites = [r for r in results if r.get('blocked') or 'error' in r]
    for r in failed_sites:
        name = r['name']
        url = r['url']
        print(f"\n  Retrying {name} with 5s wait...")
        result = test_zenrows_with_wait(url, name, wait_ms=5000)
        results.append(result)
        if 'error' in result:
            print(f"    ERROR: {result['error']}")
        else:
            print(f"    status: {result['status']}, size: {result['size']}, duration: {result['duration_s']}s")
            print(f"    challenges: {result.get('challenges_detected', [])}")
            print(f"    blocked: {result.get('blocked')}")
    
    # Save all results
    out_file = OUT_DIR / 'zenrows_results.json'
    out_file.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print(f"\n✓ All results saved to {out_file}")
    
    # Final summary
    print()
    print("=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    print(f"{'Site':25s} | {'Strategy':20s} | {'Status':>6s} | {'Size':>8s} | Blocked | Job Content")
    print("-" * 90)
    for r in results:
        name = r['name'][:25]
        strat = r['strategy'][:20]
        status = str(r.get('status', 'ERR'))[:6]
        size = str(r.get('size', 'N/A'))[:8]
        blocked = 'YES' if r.get('blocked') else 'NO'
        jc = 'YES' if r.get('has_job_content') else ('N/A' if 'has_job_content' not in r else 'NO')
        print(f"{name:25s} | {strat:20s} | {status:>6s} | {size:>8s} | {blocked:7s} | {jc}")


if __name__ == '__main__':
    main()
