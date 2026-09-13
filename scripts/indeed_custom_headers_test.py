#!/usr/bin/env python3
"""Indeed test with custom headers + Google referer."""
import os
import json, time
from pathlib import Path
import requests

OUT_DIR = Path('/home/z/my-project/research_data/zenrows_tests')
ZENROWS_API_KEY = os.environ.get("ZENROWS_API_KEY", "")
ZENROWS_URL = "https://api.zenrows.com/v1/"

# Try Indeed with custom headers + referer
url = 'https://www.indeed.com/jobs?q=software+engineer&l=San+Francisco%2C+CA'
params = {
    'apikey': ZENROWS_API_KEY,
    'url': url,
    'js_render': 'true',
    'premium_proxy': 'true',
    'proxy_country': 'us',
    'original_status': 'true',
    'wait': '10000',
    'custom_headers': 'true',
}
headers = {
    'Referer': 'https://www.google.com/',
    'Accept-Language': 'en-US,en;q=0.9',
}
print("Testing Indeed with custom_headers=true + Google referer...")
start = time.time()
try:
    r = requests.get(ZENROWS_URL, params=params, headers=headers, timeout=180)
    duration = round(time.time() - start, 2)
    print(f"  status: {r.status_code}, size: {len(r.text):,} bytes, duration: {duration}s")
    import re
    title_m = re.search(r'<title[^>]*>([^<]+)</title>', r.text)
    print(f"  title: {title_m.group(1) if title_m else 'NONE'}")
    job_cards = re.findall(r'data-jk="([^"]+)"', r.text)
    print(f"  data-jk job IDs: {len(job_cards)}")
    if job_cards:
        print(f"    first 5: {job_cards[:5]}")
    titles = re.findall(r'<span[^>]*class="[^"]*jobTitle[^"]*"[^>]*>([^<]+)<', r.text)
    print(f"  jobTitle span matches: {len(titles)}")
    for t in titles[:5]:
        print(f"    - {t.strip()}")
    # Check for challenge
    challenges = ['just a moment', 'additional verification', 'request blocked']
    for c in challenges:
        if c in r.text.lower():
            print(f"  challenge found: {c}")
    # Save
    html_path = OUT_DIR / 'indeed_custom_headers.html'
    html_path.write_text(r.text, encoding='utf-8')
    print(f"  saved to: {html_path}")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {str(e)[:200]}")
