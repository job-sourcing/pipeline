#!/usr/bin/env python3
"""Netlify edge scraper probe.
- Probe Indeed and LinkedIn via Netlify /api/scrape (POST endpoint per llms.txt)
- Try engines: fetch, chrome_impersonate, puppeteer
"""
import json, time, urllib.parse
import os
from pathlib import Path
import requests

OUT = Path('/home/z/my-project/job-sourcing/track3-js/validation_results/netlify_scraper')
OUT.mkdir(parents=True, exist_ok=True)

# Env-only credentials (S8-A scrub): os.environ first, then ingest/.env
# (the committed secret store) — never hardcoded.
def _env_file_value(name: str) -> str:
    """Read `name` from ingest/.env (KEY=value lines) if present."""
    env_path = Path(__file__).resolve().parents[2] / "ingest" / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""

NETLIFY_BASE = (os.environ.get("NETLIFY_SCRAPER_URL")
                or _env_file_value("NETLIFY_SCRAPER_URL"))
NETLIFY_URL = NETLIFY_BASE.rstrip('/') + '/api/scrape'
TOKEN = (os.environ.get("NETLIFY_SCRAPER_TOKEN")
         or _env_file_value("NETLIFY_SCRAPER_TOKEN"))
HEADERS = {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'}

SITES = [
    ('indeed', 'https://www.indeed.com/jobs?q=software+engineer&l=Remote'),
    ('linkedin', 'https://www.linkedin.com/jobs/search/?keywords=software%20engineer'),
]
ENGINES = ['fetch', 'chrome_impersonate', 'puppeteer']

CHALLENGE = ['just a moment', 'cloudflare', 'humans only', 'verify you are human',
             'captcha', 'sign in to view more jobs', 'unusual traffic']

def probe(name, url, engine):
    print(f"\n[{name}/{engine}] {url[:80]}")
    body = {'jobs': [{'url': url, 'engine': engine}], 'result_mode': 'inline'}
    start = time.time()
    try:
        r = requests.post(NETLIFY_URL, json=body, headers=HEADERS, timeout=180)
        dur = round(time.time()-start, 2)
        text = r.text
        size = len(text)
        ctype = r.headers.get('content-type','')[:80]
        tl = text.lower()
        challenges = [c for c in CHALLENGE if c in tl]
        # extract job markers
        markers = {
            'indeed': ['data-jk=', 'jobTitle', 'jobsearch-ResultsList'],
            'linkedin': ['base-search-card__title', 'urn:li:jobPosting', 'job-search-card'],
        }.get(name, [])
        found = [m for m in markers if m in text]
        try:
            parsed = r.json()
            keys = list(parsed.keys()) if isinstance(parsed, dict) else None
        except Exception:
            parsed = None; keys = None
        print(f"  status={r.status_code} size={size:,}b dur={dur}s ctype={ctype}")
        print(f"  challenges={challenges} job_markers_found={found}")
        print(f"  preview: {text[:200]!r}")
        return {
            'name': name, 'engine': engine, 'url': url,
            'status': r.status_code, 'size': size, 'content_type': ctype,
            'duration_s': dur, 'challenges': challenges,
            'job_markers_found': found, 'has_job_content': bool(found),
            'json_keys': keys, 'preview': text[:300],
        }
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {str(e)[:200]}")
        return {'name': name, 'engine': engine, 'url': url,
                'error': f'{type(e).__name__}: {str(e)[:200]}'}

def main():
    print('='*70); print('Netlify edge scraper probes'); print('='*70)
    # First check what the POST endpoint expects
    results = []
    for name, url in SITES:
        for engine in ENGINES:
            r = probe(name, url, engine)
            results.append(r)
            time.sleep(2.0)
        time.sleep(2.0)
    (OUT / 'results.json').write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print('\n✓ Saved')

if __name__ == '__main__':
    main()
