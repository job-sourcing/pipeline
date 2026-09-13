#!/usr/bin/env python3
"""Zenrows + HK baseline probes on Indeed/LinkedIn/Glassdoor.
- 3 sites x 2 modes = 6 probes
- Plus also run plain curl-cffi impersonation
"""
import os, json, time, re
from pathlib import Path
import requests
from curl_cffi import requests as cffi_requests

OUT = Path('/home/z/my-project/job-sourcing/track3-js/validation_results/zenrows_hk')
OUT.mkdir(parents=True, exist_ok=True)

ZENROWS_API_KEY = os.environ.get('ZENROWS_API_KEY', '')
ZENROWS_URL = 'https://api.zenrows.com/v1/'

SITES = [
    ('indeed', 'https://www.indeed.com/jobs?q=software+engineer&l=Remote'),
    ('linkedin', 'https://www.linkedin.com/jobs/search/?keywords=software%20engineer'),
    ('glassdoor', 'https://www.glassdoor.com/Job/jobs.htm'),
]

CHALLENGE = ['just a moment', 'cloudflare', 'humans only', 'verify you are human',
             'performing security verification', 'request blocked', 'forbidden cf-waf',
             'security check', 'additional verification', 'captcha', 'unusual traffic',
             'sign in to view more jobs', 'authwall']

JOB_MARKERS = {
    'indeed': ['data-jk=', 'jobTitle', 'jobsearch-ResultsList'],
    'linkedin': ['base-search-card__title', 'urn:li:jobPosting', 'job-search-card'],
    'glassdoor': ['data-test="job-link"', 'react-job-listing', 'JobList'],
}

def analyze(name, text, status, duration):
    text_lower = text.lower()
    challenges = [c for c in CHALLENGE if c in text_lower]
    markers = JOB_MARKERS.get(name, [])
    found = [m for m in markers if m in text]
    title_m = re.search(r'<title[^>]*>([^<]+)</title>', text)
    return {
        'status': status, 'size': len(text), 'duration_s': round(duration, 2),
        'challenges': challenges, 'blocked': bool(challenges),
        'job_markers_found': found, 'has_job_content': bool(found),
        'title': (title_m.group(1).strip()[:120] if title_m else None),
        'preview': text[:300],
    }

def zenrows_probe(name, url):
    print(f"\n[ZENROWS] {name}: {url[:80]}")
    params = {
        'url': url, 'apikey': ZENROWS_API_KEY,
        'js_render': 'true', 'premium_proxy': 'true', 'proxy_country': 'us',
        'original_status': 'true', 'wait': '5000',
    }
    start = time.time()
    try:
        r = requests.get(ZENROWS_URL, params=params, timeout=180)
        result = analyze(name, r.text, r.status_code, time.time() - start)
        result.update({'name': name, 'mode': 'zenrows', 'url': url})
        print(f"  status={result['status']} size={result['size']} dur={result['duration_s']}s blocked={result['blocked']} job_content={result['has_job_content']}")
        if result['challenges']:
            print(f"  challenges: {result['challenges']}")
        (OUT / f'zenrows_{name}.html').write_text(r.text, encoding='utf-8')
    except Exception as e:
        result = {'name': name, 'mode': 'zenrows', 'url': url, 'error': f'{type(e).__name__}: {str(e)[:200]}', 'duration_s': round(time.time()-start,2)}
        print(f"  ERROR: {result['error']}")
    return result

def plain_probe(name, url):
    print(f"\n[PLAIN-CURL-CFFI] {name}: {url[:80]}")
    start = time.time()
    try:
        r = cffi_requests.get(url, impersonate='chrome131', timeout=30, headers={
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        result = analyze(name, r.text, r.status_code, time.time() - start)
        result.update({'name': name, 'mode': 'plain_cffi', 'url': url})
        print(f"  status={result['status']} size={result['size']} dur={result['duration_s']}s blocked={result['blocked']} job_content={result['has_job_content']}")
        if result['challenges']:
            print(f"  challenges: {result['challenges']}")
        (OUT / f'plain_{name}.html').write_text(r.text, encoding='utf-8')
    except Exception as e:
        result = {'name': name, 'mode': 'plain_cffi', 'url': url, 'error': f'{type(e).__name__}: {str(e)[:200]}', 'duration_s': round(time.time()-start,2)}
        print(f"  ERROR: {result['error']}")
    return result

def main():
    print('='*70)
    print('Zenrows + HK Baseline Probes')
    print('='*70)
    results = []
    # Run plain first (lower risk), then zenrows
    for name, url in SITES:
        results.append(plain_probe(name, url))
        time.sleep(3)
    for name, url in SITES:
        results.append(zenrows_probe(name, url))
        time.sleep(3)
    (OUT / 'results.json').write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print(f"\n✓ Saved {len(results)} results to {OUT/'results.json'}")

if __name__ == '__main__':
    main()
