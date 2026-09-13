#!/usr/bin/env python3
"""
ZenRows API test — proper parameter format.
ZenRows provides: residential IPs + real headless Chrome rendering + anti-bot bypass.
"""
import os
import json
import time
from pathlib import Path
import requests

OUT_DIR = Path('/home/z/my-project/research_data/zenrows_tests')
OUT_DIR.mkdir(parents=True, exist_ok=True)

ZENROWS_API_KEY = os.environ.get("ZENROWS_API_KEY", "")
ZENROWS_URL = "https://api.zenrows.com/v1/"

SITES = [
    ('indeed_search', 'https://www.indeed.com/jobs?q=software+engineer&l=San+Francisco%2C+CA'),
    ('glassdoor_search', 'https://www.glassdoor.com/Job/jobs.htm?sc.keyword=software+engineer'),
    ('ziprecruiter_search', 'https://www.ziprecruiter.com/jobs-search?search=software+engineer&location=San+Francisco'),
    ('linkedin_search', 'https://www.linkedin.com/jobs/search/?keywords=software+engineer&location=San+Francisco'),
]

# Site-specific job content indicators (HTML/CSS markers we expect to find on real job pages)
JOB_INDICATORS = {
    'indeed_search': ['jobsearch-ResultsList', 'data-jk=', 'class="result"', 'jobTitle', 'mosaic-zone'],
    'glassdoor_search': ['jobSearchResult', 'react-job-listing', 'data-test="job-link"', 'JobList'],
    'ziprecruiter_search': ['job_content', 'job-card', 'data-job-id', 'JobCard'],
    'linkedin_search': ['job-search-card', 'base-search-card__title', 'urn:li:jobPosting'],
}


def test_zenrows(url, name, wait_ms=4000, mode='basic'):
    """ZenRows GET with all anti-bot features."""
    result = {'name': name, 'strategy': f'zenrows_{mode}', 'url': url, 'wait_ms': wait_ms}
    start = time.time()
    try:
        params = {
            'apikey': ZENROWS_API_KEY,
            'url': url,
            'js_render': 'true',
            'premium_proxy': 'true',
            'proxy_country': 'us',
            'original_status': 'true',
            'wait': str(wait_ms),  # wait for JS rendering
        }
        r = requests.get(ZENROWS_URL, params=params, timeout=120)
        result['status'] = r.status_code
        result['size'] = len(r.text)
        result['content_type'] = r.headers.get('content-type', '')
        result['duration_s'] = round(time.time() - start, 2)
        result['preview'] = r.text[:3000]
        
        # Check for Cloudflare challenge indicators
        challenge_indicators = [
            'just a moment', 'cloudflare', 'humans only', 'verify you are human',
            'performing security verification', 'request blocked', 'forbidden cf-waf',
            'security check', 'additional verification required', 'cf-challenge',
            'sign in to view more jobs', 'authenticating', 'additional verification'
        ]
        text_lower = r.text.lower()
        result['challenges_detected'] = [c for c in challenge_indicators if c in text_lower]
        result['blocked'] = bool(result['challenges_detected'])
        
        # Check for actual job content
        expected_indicators = JOB_INDICATORS.get(name, [])
        found = [i for i in expected_indicators if i in r.text]
        result['job_content_indicators_found'] = found
        result['job_content_indicators_count'] = len(found)
        result['has_job_content'] = len(found) >= 1
        
        # Save HTML for inspection
        html_path = OUT_DIR / f'{name}_zenrows_{mode}.html'
        html_path.write_text(r.text, encoding='utf-8')
        result['html_saved'] = str(html_path)
        
        # If there's an error in the response (ZenRows returns JSON errors with status 400+)
        if r.status_code >= 400 and 'json' in r.headers.get('content-type', '').lower():
            try:
                err = r.json()
                result['zenrows_error'] = err
            except Exception:
                pass
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:300]}'
        result['duration_s'] = round(time.time() - start, 2)
    return result


def main():
    print("=" * 80)
    print("ZenRows API Test — Cloudflare-Protected Job Boards")
    print("Strategy: js_render=true + premium_proxy=true + proxy_country=us + wait=4s")
    print("=" * 80)
    
    results = []
    
    # Phase 1: Basic test with all features (4s wait for JS rendering)
    print("\n--- Phase 1: ZenRows with js_render + premium_proxy + 4s wait ---")
    for name, url in SITES:
        print(f"\n  >>> Testing {name}...")
        result = test_zenrows(url, name, wait_ms=4000, mode='basic')
        results.append(result)
        if 'error' in result:
            print(f"    ERROR: {result['error']}")
            continue
        print(f"    status: {result['status']}, size: {result['size']:,} bytes, duration: {result['duration_s']}s")
        print(f"    blocked: {result['blocked']}, challenges: {result.get('challenges_detected', [])}")
        print(f"    job_content_indicators_found: {result.get('job_content_indicators_found', [])}")
        preview = result.get('preview', '')[:300].replace('\n', ' ').replace('\r', '')
        print(f"    preview: {preview!r}")
        if 'zenrows_error' in result:
            print(f"    zenrows_error: {result['zenrows_error']}")
    
    # Phase 2: Retry failed with longer wait
    failed = [r for r in results if r.get('blocked') or 'error' in r or not r.get('has_job_content')]
    if failed:
        print("\n--- Phase 2: Retry with 8s wait for JS rendering ---")
        for r in failed:
            name = r['name']
            url = r['url']
            print(f"\n  >>> Retrying {name} with 8s wait...")
            result = test_zenrows(url, name, wait_ms=8000, mode='wait8s')
            results.append(result)
            if 'error' in result:
                print(f"    ERROR: {result['error']}")
                continue
            print(f"    status: {result['status']}, size: {result['size']:,} bytes, duration: {result['duration_s']}s")
            print(f"    blocked: {result['blocked']}, challenges: {result.get('challenges_detected', [])}")
            print(f"    job_content_indicators_found: {result.get('job_content_indicators_found', [])}")
            preview = result.get('preview', '')[:300].replace('\n', ' ').replace('\r', '')
            print(f"    preview: {preview!r}")
    
    # Save all results
    out_file = OUT_DIR / 'zenrows_results.json'
    out_file.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print(f"\n✓ All results saved to {out_file}")
    
    # Final summary
    print()
    print("=" * 100)
    print("FINAL SUMMARY")
    print("=" * 100)
    print(f"{'Site':25s} | {'Strategy':22s} | {'Status':>6s} | {'Size':>10s} | Blocked | Job Content")
    print("-" * 100)
    for r in results:
        name = r['name'][:25]
        strat = r['strategy'][:22]
        status = str(r.get('status', 'ERR'))[:6]
        size = f"{r.get('size', 0):,}"[:10]
        blocked = 'YES' if r.get('blocked') else 'NO'
        jc = 'YES' if r.get('has_job_content') else 'NO'
        print(f"{name:25s} | {strat:22s} | {status:>6s} | {size:>10s} | {blocked:7s} | {jc}")


if __name__ == '__main__':
    main()
