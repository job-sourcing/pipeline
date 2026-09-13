#!/usr/bin/env python3
"""
Test 6: Validate LinkedIn guest API at personal scale.
- Pagination depth: how many pages before rate-limit / empty response?
- Multiple parallel keyword queries: total jobs extractable per day?
- Sample job extraction with structured fields.
"""
import json
import re
import time
from curl_cffi import requests as cffi_requests
from pathlib import Path

OUT_DIR = Path('/home/z/my-project/research_data/stealth_tests')
OUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_linkedin_guest_page(keywords, location, start):
    """Fetch one page (25 jobs) from LinkedIn guest API."""
    url = 'https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search'
    params = {'keywords': keywords, 'location': location, 'start': start}
    headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': f'https://www.linkedin.com/jobs/search/?keywords={keywords.replace(" ", "+")}&location={location.replace(" ", "+")}',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
    }
    try:
        r = cffi_requests.get(url, params=params, headers=headers, impersonate='chrome131', timeout=20)
        return {
            'status': r.status_code,
            'size': len(r.text),
            'text': r.text,
            'duration_s': round(r.elapsed.total_seconds(), 2) if hasattr(r, 'elapsed') else None,
        }
    except Exception as e:
        return {'error': f'{type(e).__name__}: {str(e)[:200]}'}


def parse_jobs_from_html(html):
    """Extract structured jobs from LinkedIn guest API HTML response."""
    jobs = []
    # Each job is in <li> ... </li> with data-entity-urn="urn:li:jobPosting:NUMBER"
    job_blocks = re.findall(
        r'<li[^>]*data-entity-urn="urn:li:jobPosting:(\d+)"[^>]*>(.*?)</li>',
        html, re.DOTALL
    )
    for job_id, block in job_blocks:
        # Extract title
        title_m = re.search(r'<h3 class="base-search-card__title">\s*([^<]+?)\s*</h3>', block)
        # Extract company
        company_m = re.search(r'<h4[^>]*>\s*<a[^>]*>\s*([^<]+?)\s*</a>', block)
        # Extract location
        loc_m = re.search(r'<span class="job-search-card__location">\s*([^<]+?)\s*</span>', block)
        # Extract posted date
        date_m = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        # Extract URL
        url_m = re.search(r'<a[^>]*href="([^"]+)"[^>]*data-tracking-control-name="public_jobs_jserp-result_search-card"', block)
        # Extract benefits
        benefit_m = re.search(r'<span class="job-posting-benefits__text">\s*([^<]+?)\s*</span>', block)
        
        jobs.append({
            'id': job_id,
            'title': title_m.group(1).strip() if title_m else None,
            'company': company_m.group(1).strip() if company_m else None,
            'location': loc_m.group(1).strip() if loc_m else None,
            'posted_date': date_m.group(1).strip() if date_m else None,
            'url': url_m.group(1).strip() if url_m else None,
            'benefits': benefit_m.group(1).strip() if benefit_m else None,
        })
    return jobs


def test_pagination_depth():
    """How many pages (25 jobs each) can we fetch before being rate-limited?"""
    keywords = 'software engineer'
    location = 'San Francisco'
    
    print(f"Testing pagination depth for '{keywords}' in '{location}'...")
    results = []
    total_jobs = 0
    
    for start in range(0, 1100, 25):  # 0, 25, 50, ..., 1075
        r = fetch_linkedin_guest_page(keywords, location, start)
        if 'error' in r:
            print(f"  start={start:4d}: ERROR {r['error'][:80]}")
            results.append({'start': start, **r})
            break
        
        jobs = parse_jobs_from_html(r['text'])
        total_jobs += len(jobs)
        status = r['status']
        size = r['size']
        print(f"  start={start:4d}: HTTP {status} | {size}b | {len(jobs)} jobs | total={total_jobs}")
        
        results.append({
            'start': start,
            'status': status,
            'size': size,
            'jobs_count': len(jobs),
            'sample_titles': [j['title'] for j in jobs[:2]],
        })
        
        if len(jobs) == 0:
            print(f"  → empty response at start={start}, stopping")
            break
        
        if status == 429:
            print(f"  → rate limited at start={start}, stopping")
            break
        
        # Be polite — 1 second between requests
        time.sleep(1.0)
    
    return {
        'test': 'pagination_depth',
        'keywords': keywords,
        'location': location,
        'total_jobs_extracted': total_jobs,
        'pages_fetched': len(results),
        'results': results,
    }


def test_multiple_keyword_queries():
    """How many jobs can we extract with multiple parallel keyword queries?"""
    queries = [
        ('software engineer', 'San Francisco'),
        ('frontend engineer', 'Remote'),
        ('backend engineer', 'Remote'),
        ('data scientist', 'New York'),
        ('product manager', 'Remote'),
        ('devops engineer', 'Remote'),
        ('machine learning engineer', 'San Francisco'),
    ]
    
    print(f"\nTesting {len(queries)} parallel keyword queries (first page each)...")
    results = []
    total_jobs = 0
    
    for keywords, location in queries:
        r = fetch_linkedin_guest_page(keywords, location, 0)
        if 'error' in r:
            print(f"  '{keywords}' @ '{location}': ERROR {r['error'][:80]}")
            continue
        
        jobs = parse_jobs_from_html(r['text'])
        total_jobs += len(jobs)
        print(f"  '{keywords}' @ '{location}': {len(jobs)} jobs (page 1)")
        results.append({
            'keywords': keywords,
            'location': location,
            'jobs_count_page1': len(jobs),
            'sample': jobs[:3],
        })
        time.sleep(1.5)  # polite rate limit
    
    return {
        'test': 'multiple_queries',
        'queries': len(queries),
        'total_jobs_first_pages': total_jobs,
        'results': results,
    }


def main():
    print("=" * 70)
    print("LinkedIn Guest API — Personal Scale Validation")
    print("=" * 70)
    
    # Test 1: pagination depth
    pagination_result = test_pagination_depth()
    
    # Test 2: multiple keyword queries
    queries_result = test_multiple_keyword_queries()
    
    # Save
    summary = {
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'pagination_test': pagination_result,
        'multiple_queries_test': queries_result,
    }
    out_file = OUT_DIR / 'linkedin_guest_validation.json'
    out_file.write_text(json.dumps(summary, indent=2, default=str), encoding='utf-8')
    print(f"\n✓ Results saved to {out_file}")
    
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Pagination depth test:")
    print(f"  Pages fetched: {pagination_result['pages_fetched']}")
    print(f"  Total jobs extracted (single query): {pagination_result['total_jobs_extracted']}")
    print()
    print(f"Multiple queries test ({queries_result['queries']} queries, first page only):")
    print(f"  Total jobs across queries: {queries_result['total_jobs_first_pages']}")
    print()
    print(f"Extrapolation for daily personal use:")
    avg_jobs_per_query = queries_result['total_jobs_first_pages'] / max(queries_result['queries'], 1)
    print(f"  Average jobs/page: {avg_jobs_per_query:.1f}")
    print(f"  If 10 keyword queries × 1000 jobs/query ceiling = ~10,000 jobs/day from LinkedIn alone")
    print(f"  Cost: $0 (free, no auth, no proxy)")
    print(f"  Time: ~10-15 minutes for 10 queries × 40 pages each (with 1s delay)")


if __name__ == '__main__':
    main()
