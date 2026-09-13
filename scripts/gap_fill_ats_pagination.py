#!/usr/bin/env python3
"""
Gap-fill validation 1: ATS pagination depth.

Question: Do Greenhouse/Lever/Ashby/SmartRecruiters public APIs truly paginate
infinitely (or to a high cap), or do they hard-limit results?

This matters because: if Greenhouse caps at 500 jobs per company, scraping at
scale means we hit that ceiling for big companies (Stripe had 583 in our test).
"""
import json
import time
import requests
from pathlib import Path

OUT = Path('/home/z/my-project/research_data/gap_fill')
OUT.mkdir(parents=True, exist_ok=True)

# Pick companies likely to have LOTS of jobs
GREENHOUSE_BIG = ['stripe', 'airbnb', 'shopify', 'github', 'voxmedia']
LEVER_BIG = ['plaid', 'square', 'dropbox', 'gitlab', 'notion', 'vercel']
ASHBY_BIG = ['ashby', 'openai', 'anthropic', 'shopify', 'notion']
SR_BIG = ['smartrecruiters', 'visa', 'nike']


def test_greenhouse_pagination(slug):
    """Greenhouse Job Board API: per_page max is 500. Test total job count."""
    print(f"\n[Greenhouse] {slug}")
    # First request: get meta with per_page=1 to see total
    try:
        r = requests.get(
            f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?per_page=1',
            timeout=15, headers={'User-Agent': 'gap-fill-test/1.0'}
        )
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}")
            return None
        data = r.json()
        # Greenhouse returns "meta": {"total": N}
        meta = data.get('meta', {})
        total = meta.get('total', 0)
        jobs_returned = len(data.get('jobs', []))
        print(f"  meta.total = {total}, jobs in this page = {jobs_returned}")
        # Now try per_page=500 (the documented max)
        r2 = requests.get(
            f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?per_page=500',
            timeout=30, headers={'User-Agent': 'gap-fill-test/1.0'}
        )
        if r2.status_code == 200:
            data2 = r2.json()
            jobs2 = data2.get('jobs', [])
            print(f"  per_page=500 → returned {len(jobs2)} jobs")
            # Try page=2
            r3 = requests.get(
                f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?per_page=500&page=2',
                timeout=30, headers={'User-Agent': 'gap-fill-test/1.0'}
            )
            if r3.status_code == 200:
                data3 = r3.json()
                jobs3 = data3.get('jobs', [])
                print(f"  page=2 → returned {len(jobs3)} jobs")
                return {'slug': slug, 'meta_total': total, 'page1': len(jobs2), 'page2': len(jobs3)}
            else:
                print(f"  page=2 → HTTP {r3.status_code}")
                return {'slug': slug, 'meta_total': total, 'page1': len(jobs2), 'page2_status': r3.status_code}
        else:
            print(f"  per_page=500 → HTTP {r2.status_code}")
            return {'slug': slug, 'meta_total': total, 'per_page_500_status': r2.status_code}
    except Exception as e:
        print(f"  ERROR: {e}")
        return {'slug': slug, 'error': str(e)}


def test_lever_pagination(slug):
    """Lever: limit parameter. Test with limit=1000 (very high)."""
    print(f"\n[Lever] {slug}")
    try:
        # Try limit=1000 — see if it actually returns up to 1000
        r = requests.get(
            f'https://api.lever.co/v0/postings/{slug}?limit=1000',
            timeout=15, headers={'User-Agent': 'gap-fill-test/1.0'}
        )
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}")
            return {'slug': slug, 'status': r.status_code}
        data = r.json()
        if not isinstance(data, list):
            print(f"  Not a list: {str(data)[:200]}")
            return {'slug': slug, 'unexpected_response': str(data)[:200]}
        print(f"  limit=1000 → returned {len(data)} jobs")
        # Lever has no pagination cursor — limit is the only way
        # Check if there's an offset param
        r2 = requests.get(
            f'https://api.lever.co/v0/postings/{slug}?limit=1000&offset=1000',
            timeout=15, headers={'User-Agent': 'gap-fill-test/1.0'}
        )
        if r2.status_code == 200:
            data2 = r2.json()
            print(f"  offset=1000 → returned {len(data2) if isinstance(data2, list) else 'non-list'} jobs")
            return {'slug': slug, 'limit_1000': len(data), 'offset_1000': len(data2) if isinstance(data2, list) else 0}
        return {'slug': slug, 'limit_1000': len(data)}
    except Exception as e:
        print(f"  ERROR: {e}")
        return {'slug': slug, 'error': str(e)}


def test_ashby_pagination(slug):
    """Ashby: 1MB+ responses. Check if there's a pagination mechanism."""
    print(f"\n[Ashby] {slug}")
    try:
        r = requests.get(
            f'https://api.ashbyhq.com/posting-api/job-board/{slug}',
            timeout=60, headers={'User-Agent': 'gap-fill-test/1.0'}
        )
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}")
            return {'slug': slug, 'status': r.status_code}
        data = r.json()
        jobs = data.get('jobs', [])
        print(f"  returned {len(jobs)} jobs, response size = {len(r.text):,} bytes")
        # Ashby API doesn't appear to support pagination — returns all jobs in one shot
        return {'slug': slug, 'job_count': len(jobs), 'response_size_bytes': len(r.text)}
    except Exception as e:
        print(f"  ERROR: {e}")
        return {'slug': slug, 'error': str(e)}


def test_smartrecruiters_pagination(slug):
    """SmartRecruiters: limit + offset."""
    print(f"\n[SmartRecruiters] {slug}")
    try:
        # Get first page with limit=100 (max?)
        r = requests.get(
            f'https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0',
            timeout=15, headers={'User-Agent': 'gap-fill-test/1.0'}
        )
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}")
            return {'slug': slug, 'status': r.status_code}
        data = r.json()
        content = data.get('content', [])
        total_found = data.get('totalFound', 0)
        print(f"  totalFound = {total_found}, returned = {len(content)}")
        # Try offset=100
        if total_found > 100:
            r2 = requests.get(
                f'https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=100',
                timeout=15, headers={'User-Agent': 'gap-fill-test/1.0'}
            )
            if r2.status_code == 200:
                data2 = r2.json()
                content2 = data2.get('content', [])
                print(f"  offset=100 → returned {len(content2)} jobs")
                return {'slug': slug, 'totalFound': total_found, 'page1': len(content), 'page2': len(content2)}
        return {'slug': slug, 'totalFound': total_found, 'page1': len(content)}
    except Exception as e:
        print(f"  ERROR: {e}")
        return {'slug': slug, 'error': str(e)}


def main():
    print("=" * 70)
    print("ATS Pagination Depth Validation")
    print("=" * 70)
    
    results = {'greenhouse': [], 'lever': [], 'ashby': [], 'smartrecruiters': []}
    
    for slug in GREENHOUSE_BIG:
        r = test_greenhouse_pagination(slug)
        if r: results['greenhouse'].append(r)
        time.sleep(0.5)
    
    for slug in LEVER_BIG:
        r = test_lever_pagination(slug)
        if r: results['lever'].append(r)
        time.sleep(0.5)
    
    for slug in ASHBY_BIG:
        r = test_ashby_pagination(slug)
        if r: results['ashby'].append(r)
        time.sleep(1.0)
    
    for slug in SR_BIG:
        r = test_smartrecruiters_pagination(slug)
        if r: results['smartrecruiters'].append(r)
        time.sleep(0.5)
    
    out_file = OUT / 'ats_pagination_results.json'
    out_file.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print(f"\n✓ Saved to {out_file}")
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("\nGreenhouse:")
    for r in results['greenhouse']:
        if 'meta_total' in r:
            print(f"  {r['slug']:15s}: total={r['meta_total']}, page1(500)={r.get('page1', '?')}, page2(500)={r.get('page2', '?')}")
    print("\nLever:")
    for r in results['lever']:
        if 'limit_1000' in r:
            print(f"  {r['slug']:15s}: limit=1000 returned {r['limit_1000']}, offset=1000 returned {r.get('offset_1000', 'N/A')}")
    print("\nAshby:")
    for r in results['ashby']:
        if 'job_count' in r:
            print(f"  {r['slug']:15s}: {r['job_count']} jobs ({r['response_size_bytes']:,} bytes)")
    print("\nSmartRecruiters:")
    for r in results['smartrecruiters']:
        if 'totalFound' in r:
            print(f"  {r['slug']:15s}: totalFound={r['totalFound']}, page1={r.get('page1', '?')}, page2={r.get('page2', 'N/A')}")


if __name__ == '__main__':
    main()
