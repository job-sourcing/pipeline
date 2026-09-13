#!/usr/bin/env python3
"""
LinkedIn Guest API Scraper — Free, no auth, no proxy.

30-line script that hits the LinkedIn guest API and extracts structured job listings.
Validated working in this research: ~500 jobs per query, ~25 jobs per page, 1-second delay between pages.

Usage:
    python3 linkedin_guest_scraper.py [keywords] [location] [max_pages]

Examples:
    python3 linkedin_guest_scraper.py "software engineer" "San Francisco" 20
    python3 linkedin_guest_scraper.py "frontend engineer" "Remote" 10
    python3 linkedin_guest_scraper.py  # uses defaults

Output:
    - Saves jobs to /home/z/my-project/download/linkedin_jobs_{timestamp}.json
    - Prints sample jobs to console
"""
import sys
import re
import json
import time
from datetime import datetime
from pathlib import Path
from curl_cffi import requests as cffi_requests

OUT_DIR = Path('/home/z/my-project/download')
OUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_page(keywords, location, start):
    """Fetch one page (25 jobs) from LinkedIn guest API."""
    url = (
        f'https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search'
        f'?keywords={keywords.replace(" ", "+")}&location={location.replace(" ", "+")}&start={start}'
    )
    return cffi_requests.get(url, impersonate='chrome131', timeout=20, headers={
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': f'https://www.linkedin.com/jobs/search/?keywords={keywords.replace(" ", "+")}&location={location.replace(" ", "+")}',
    })


def parse_jobs(html):
    """Extract structured jobs from LinkedIn guest API HTML response."""
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
            'id': job_id,
            'title': title_m.group(1).strip() if title_m else None,
            'company': company_m.group(1).strip() if company_m else None,
            'location': loc_m.group(1).strip() if loc_m else None,
            'posted_date': date_m.group(1).strip() if date_m else None,
            'url': url_m.group(1).strip() if url_m else None,
            'source': 'linkedin_guest_api',
        })
    return jobs


def scrape(keywords, location, max_pages=25, delay_s=1.0):
    """Scrape up to max_pages * 25 jobs."""
    print(f"Searching: '{keywords}' in '{location}' (max {max_pages} pages = {max_pages * 25} jobs)")
    all_jobs = []
    for page in range(max_pages):
        start = page * 25
        try:
            r = fetch_page(keywords, location, start)
        except Exception as e:
            print(f"  page {page + 1}/{max_pages} (start={start}): ERROR {e}")
            break
        if r.status_code != 200:
            print(f"  page {page + 1}/{max_pages} (start={start}): HTTP {r.status_code} (stop)")
            break
        jobs = parse_jobs(r.text)
        if not jobs:
            print(f"  page {page + 1}/{max_pages} (start={start}): 0 jobs (cap reached)")
            break
        all_jobs.extend(jobs)
        print(f"  page {page + 1}/{max_pages} (start={start}): {len(jobs)} jobs | cumulative: {len(all_jobs)}")
        time.sleep(delay_s)
    return all_jobs


def main():
    keywords = sys.argv[1] if len(sys.argv) > 1 else "software engineer"
    location = sys.argv[2] if len(sys.argv) > 2 else "San Francisco"
    max_pages = int(sys.argv[3]) if len(sys.argv) > 3 else 25

    jobs = scrape(keywords, location, max_pages)

    # Save
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_file = OUT_DIR / f'linkedin_jobs_{timestamp}.json'
    out_file.write_text(json.dumps({
        'query': {'keywords': keywords, 'location': location, 'max_pages': max_pages},
        'timestamp': timestamp,
        'total_jobs': len(jobs),
        'jobs': jobs,
    }, indent=2), encoding='utf-8')

    print(f"\n✓ Saved {len(jobs)} jobs to {out_file}")
    print("\nSample jobs (first 5):")
    for j in jobs[:5]:
        print(f"  - {j['title']}")
        print(f"      at {j['company']} ({j['location']})")
        print(f"      posted {j['posted_date']}")
        if j['url']:
            print(f"      URL: {j['url'][:80]}")


if __name__ == '__main__':
    main()
