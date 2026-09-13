#!/usr/bin/env python3
"""Targeted Indeed test via ZenRows with longer wait and retry logic."""
import os
import json, time
from pathlib import Path
import requests

OUT_DIR = Path('/home/z/my-project/research_data/zenrows_tests')
ZENROWS_API_KEY = os.environ.get("ZENROWS_API_KEY", "")
ZENROWS_URL = "https://api.zenrows.com/v1/"

def test_indeed(url, wait_ms, label):
    print(f"\n--- Indeed test: {label} (wait={wait_ms}ms) ---")
    params = {
        'apikey': ZENROWS_API_KEY,
        'url': url,
        'js_render': 'true',
        'premium_proxy': 'true',
        'proxy_country': 'us',
        'original_status': 'true',
        'wait': str(wait_ms),
    }
    start = time.time()
    try:
        r = requests.get(ZENROWS_URL, params=params, timeout=180)
        duration = round(time.time() - start, 2)
        print(f"  status: {r.status_code}, size: {len(r.text):,} bytes, duration: {duration}s")
        # Check for job content
        import re
        title_m = re.search(r'<title[^>]*>([^<]+)</title>', r.text)
        print(f"  title: {title_m.group(1) if title_m else 'NONE'}")
        # Indeed job cards
        job_cards = re.findall(r'data-jk="([^"]+)"', r.text)
        print(f"  data-jk job IDs: {len(job_cards)}")
        if job_cards:
            print(f"    first 5: {job_cards[:5]}")
        # Job titles
        titles = re.findall(r'<h2[^>]*class="[^"]*jobTitle[^"]*"[^>]*>([^<]+)<', r.text)
        print(f"  jobTitle h2 matches: {len(titles)}")
        for t in titles[:5]:
            print(f"    - {t.strip()}")
        # Check for challenge
        challenges = ['just a moment', 'additional verification', 'request blocked', 'cloudflare']
        text_lower = r.text.lower()
        for c in challenges:
            if c in text_lower:
                # Find context
                idx = text_lower.find(c)
                print(f"  challenge '{c}' found at pos {idx}")
                print(f"    context: {r.text[max(0,idx-100):idx+300]!r}")
        # Save HTML
        html_path = OUT_DIR / f'indeed_{label}.html'
        html_path.write_text(r.text, encoding='utf-8')
        print(f"  saved to: {html_path}")
        return {
            'status': r.status_code, 'size': len(r.text), 'duration_s': duration,
            'title': title_m.group(1) if title_m else None,
            'job_count': len(job_cards), 'sample_job_ids': job_cards[:5],
        }
    except Exception as e:
        duration = round(time.time() - start, 2)
        print(f"  ERROR after {duration}s: {type(e).__name__}: {str(e)[:200]}")
        return {'error': str(e), 'duration_s': duration}

# Test multiple Indeed URLs and waits
tests = [
    ('https://www.indeed.com/jobs?q=software+engineer&l=San+Francisco%2C+CA', 8000, 'wait8s'),
    ('https://www.indeed.com/jobs?q=software+engineer&l=San+Francisco%2C+CA', 15000, 'wait15s'),
    ('https://www.indeed.com/m/jobs?q=software+engineer&l=San+Francisco%2C+CA', 8000, 'mobile_wait8s'),  # mobile URL sometimes less protected
]

results = []
for url, wait, label in tests:
    r = test_indeed(url, wait, label)
    r['label'] = label
    r['url'] = url
    results.append(r)

# Save
out_file = OUT_DIR / 'indeed_zenrows_results.json'
out_file.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
print(f"\n✓ Results saved to {out_file}")
