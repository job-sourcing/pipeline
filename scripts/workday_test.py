#!/usr/bin/env python3
"""
Test 3: Find correct Workday CXS pattern by parsing careers page HTML.
Also: try headful SeleniumBase UC (with xvfb) for Cloudflare sites.
"""
import re
import json
import time
from pathlib import Path
import httpx

OUT_DIR = Path('/home/z/my-project/research_data/stealth_tests')

def find_workday_site_id(tenant_sub):
    """Visit Workday careers page and extract actual Job_Posting_Site_ID."""
    result = {'tenant': tenant_sub, 'strategy': 'workday_site_id_hunt'}
    careers_url = f'https://{tenant_sub}/en-US/{tenant_sub.split(".")[0]}'
    start = time.time()
    try:
        with httpx.Client(
            timeout=20.0,
            http2=True,
            headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
            },
            follow_redirects=True,
        ) as client:
            r = client.get(careers_url)
            result['careers_status'] = r.status_code
            result['careers_size'] = len(r.text)
            result['final_url'] = str(r.url)
            result['cookies'] = list(client.cookies.keys())
            
            # Look for site ID patterns in the HTML
            html = r.text
            patterns = [
                r'"jobPostingSiteId"\s*:\s*"([^"]+)"',
                r'Job_Posting_Site_ID\\u003d([A-Za-z0-9_-]+)',
                r'Job_Posting_Site_ID=([A-Za-z0-9_-]+)',
                r'/wday/cxs/[^/]+/([^/]+)/jobs',
                r'data-automation-id="job-title"[^>]+href="([^"]+)"',
                r'/en-US/[^/]+/job/',
                r'/([^/]+)/job/[^"]+',
                r'window\.__INITIAL_STATE__\s*=\s*({[^;]+})',
            ]
            matches = {}
            for pat in patterns:
                m = re.findall(pat, html)
                if m:
                    matches[pat[:60]] = list(set(m))[:5]
            result['pattern_matches'] = matches
            
            # Look for any URL path that contains /job/ — that's the site ID
            job_paths = re.findall(r'/en-US/([^/]+)/job/', html)
            result['job_paths_found'] = list(set(job_paths))[:5]
            
            # Look for any mention of CXS or wday
            cxs_paths = re.findall(r'/wday/cxs/[^"\']+', html)
            result['cxs_paths_found'] = list(set(cxs_paths))[:5]
            
            # Save the HTML for inspection
            html_path = OUT_DIR / f'workday_{tenant_sub.split(".")[0]}_careers.html'
            html_path.write_text(html, encoding='utf-8')
            
            result['duration_s'] = round(time.time() - start, 2)
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:300]}'
        result['duration_s'] = round(time.time() - start, 2)
    return result


def test_workday_with_extracted_site_id(tenant_sub, site_id, name):
    """Try CXS with the extracted site ID."""
    if not site_id:
        return {'name': name, 'error': 'no site_id extracted'}
    
    tenant_lower = tenant_sub.split('.')[0]
    api_url = f'https://{tenant_sub}/wday/cxs/{tenant_lower}/{site_id}/jobs'
    careers_url = f'https://{tenant_sub}/en-US/{site_id}'
    
    result = {'name': name, 'strategy': 'workday_cxs_extracted', 'tenant': tenant_sub, 'site_id': site_id, 'api_url': api_url}
    start = time.time()
    try:
        with httpx.Client(
            timeout=20.0,
            http2=True,
            headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Content-Type': 'application/json',
                'Origin': f'https://{tenant_sub}',
                'Referer': careers_url,
            }
        ) as client:
            # GET careers page first
            r1 = client.get(careers_url)
            result['careers_status'] = r1.status_code
            
            # POST to CXS
            payload = {'appliedFacets': {}, 'limit': 20, 'offset': 0, 'searchText': ''}
            r2 = client.post(api_url, json=payload)
            result['cxs_status'] = r2.status_code
            result['cxs_size'] = len(r2.text)
            result['cxs_preview'] = r2.text[:500]
            
            if 'json' in r2.headers.get('content-type', '').lower():
                try:
                    data = r2.json()
                    if isinstance(data, dict):
                        result['cxs_json_keys'] = list(data.keys())
                        if 'jobPostings' in data:
                            result['job_count'] = len(data['jobPostings'])
                            if data['jobPostings']:
                                j = data['jobPostings'][0]
                                result['sample_job'] = {
                                    'title': j.get('title', ''),
                                    'externalPath': j.get('externalPath', ''),
                                    'locations': j.get('locations', []),
                                    'postedOn': j.get('postedOn', ''),
                                }
                        if 'total' in data:
                            result['total_jobs'] = data.get('total')
                except Exception as e:
                    result['json_parse_error'] = str(e)
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:300]}'
    result['duration_s'] = round(time.time() - start, 2)
    return result


def main():
    results = []
    
    workday_tenants = [
        ('bf.wd5.myworkdayjobs.com', 'Brown-Forman'),
        ('netflix.wd5.myworkdayjobs.com', 'Netflix'),
        ('paypal.wd1.myworkdayjobs.com', 'PayPal'),
        ('nike.wd1.myworkdayjobs.com', 'Nike'),
        ('ucsf.wd1.myworkdayjobs.com', 'UCSF'),
        ('liveramp.wd1.myworkdayjobs.com', 'LiveRamp'),
        ('atlassian.wd1.myworkdayjobs.com', 'Atlassian'),
        ('sparthealth.wd1.myworkdayjobs.com', 'Sparthealth'),
        ('cargill.wd1.myworkdayjobs.com', 'Cargill'),
        ('aig.wd5.myworkdayjobs.com', 'AIG'),
    ]
    
    print("=" * 70)
    print("PHASE 1: Discover Workday Job_Posting_Site_ID from careers HTML")
    print("=" * 70)
    
    discovered = {}
    for tenant_sub, name in workday_tenants:
        print(f"\n--- {name} ({tenant_sub}) ---")
        result = find_workday_site_id(tenant_sub)
        results.append({**result, 'name': name})
        
        if 'error' in result:
            print(f"  ERROR: {result['error']}")
            continue
        
        print(f"  careers_status: {result.get('careers_status')}")
        print(f"  careers_size: {result.get('careers_size')} bytes")
        print(f"  final_url: {result.get('final_url', '')[:100]}")
        print(f"  cookies: {result.get('cookies')}")
        print(f"  job_paths: {result.get('job_paths_found')}")
        print(f"  cxs_paths: {result.get('cxs_paths_found')}")
        
        # Try to extract a likely site_id from job_paths_found
        if result.get('job_paths_found'):
            discovered[tenant_sub] = result['job_paths_found'][0]
            print(f"  → extracted site_id: {discovered[tenant_sub]}")
        elif result.get('cxs_paths_found'):
            # parse from /wday/cxs/{tenant}/{site_id}/jobs
            m = re.search(r'/wday/cxs/[^/]+/([^/]+)/jobs', result['cxs_paths_found'][0])
            if m:
                discovered[tenant_sub] = m.group(1)
                print(f"  → extracted site_id from CXS: {discovered[tenant_sub]}")
    
    print()
    print("=" * 70)
    print("PHASE 2: Test Workday CXS with extracted site IDs")
    print("=" * 70)
    
    for tenant_sub, site_id in discovered.items():
        # Find name
        name = next((n for t, n in workday_tenants if t == tenant_sub), tenant_sub.split('.')[0])
        print(f"\n--- {name} ({tenant_sub}, site_id={site_id}) ---")
        result = test_workday_with_extracted_site_id(tenant_sub, site_id, name)
        results.append(result)
        
        if 'error' in result:
            print(f"  ERROR: {result['error']}")
            continue
        
        print(f"  careers_status: {result.get('careers_status')}")
        print(f"  cxs_status: {result.get('cxs_status')} ({result.get('cxs_size')} bytes)")
        if 'job_count' in result:
            print(f"  job_count: {result.get('job_count')}")
        if 'total_jobs' in result:
            print(f"  total_jobs: {result.get('total_jobs')}")
        if 'sample_job' in result:
            print(f"  sample_job: {result.get('sample_job')}")
        preview = result.get('cxs_preview', '')[:200]
        print(f"  preview: {preview!r}")
    
    # Save all results
    out_file = OUT_DIR / 'workday_site_id_results.json'
    out_file.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print(f"\n✓ Results saved to {out_file}")


if __name__ == '__main__':
    main()
