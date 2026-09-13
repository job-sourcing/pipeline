#!/usr/bin/env python3
"""
Test 2: SeleniumBase UC Mode (Undetected-Chromedriver) and correct Workday CXS pattern.
SeleniumBase UC Mode is documented as 'most reliable Cloudflare Turnstile clicker'.
"""

import json
import time
from pathlib import Path
from seleniumbase import SB

OUT_DIR = Path('/home/z/my-project/research_data/stealth_tests')
OUT_DIR.mkdir(parents=True, exist_ok=True)


def test_seleniumbase_uc(url, name, wait_seconds=8):
    """Test SeleniumBase UC Mode against a Cloudflare-protected site."""
    result = {'name': name, 'strategy': 'seleniumbase_uc', 'url': url}
    start = time.time()
    try:
        with SB(
            browser='chrome',
            headless=True,  # try headless first
            uc=True,  # undetected-chromedriver mode
            xvfb=False,  # no X virtual framebuffer
            incognito=False,
            ad_block=False,
        ) as sb:
            sb.driver.set_page_load_timeout(40)
            sb.uc_open_with_reconnect(url, reconnect_time=4)
            time.sleep(wait_seconds)
            
            title = sb.driver.title
            html = sb.driver.page_source
            try:
                text = sb.driver.find_element('tag name', 'body').text
            except Exception:
                text = ''
            
            result['status'] = 'unknown'  # UC mode doesn't expose easily
            result['title'] = title
            result['html_size'] = len(html)
            result['text_size'] = len(text)
            result['text_preview'] = text[:1500] if text else ''
            result['duration_s'] = round(time.time() - start, 2)
            
            challenge_indicators = [
                'just a moment', 'cloudflare', 'humans only', 'verify you are human',
                'performing security verification', 'checking your browser',
                'request blocked', 'bot-detection', 'cf-challenge',
                'forbidden cf-waf', 'security check',
            ]
            text_lower = (title + ' ' + text).lower()
            result['challenges_detected'] = [c for c in challenge_indicators if c in text_lower]
            result['blocked'] = bool(result['challenges_detected'])
            
            # Save screenshot
            screenshot_path = OUT_DIR / f'{name}_uc.png'
            try:
                sb.driver.save_screenshot(str(screenshot_path))
                result['screenshot'] = str(screenshot_path)
            except Exception as e:
                result['screenshot_error'] = str(e)
            
            # Save HTML
            html_path = OUT_DIR / f'{name}_uc.html'
            html_path.write_text(html, encoding='utf-8')
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:300]}'
        result['duration_s'] = round(time.time() - start, 2)
    return result


def test_workday_cxs_correct(tenant_sub, tenant_lower, name):
    """Test Workday CXS API with correct lowercase tenant slug and POST body."""
    import httpx
    result = {'name': name, 'strategy': 'workday_cxs_correct', 'tenant': tenant_sub}
    
    api_url = f'https://{tenant_sub}/wday/cxs/{tenant_lower}/{tenant_lower}/jobs'
    careers_url = f'https://{tenant_sub}/en-US/{tenant_lower}'
    start = time.time()
    
    try:
        # Use a synchronous client with proper session cookie handling
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
                'Sec-Ch-Ua': '"Chromium";v="131", "Not_A Brand";v="24"',
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Ch-Ua-Platform': '"Linux"',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
            }
        ) as client:
            # First load the careers page to get session cookies
            r1 = client.get(careers_url)
            result['careers_page_status'] = r1.status_code
            result['careers_page_size'] = len(r1.text)
            result['cookies_after_get'] = list(client.cookies.keys())
            
            # POST to CXS endpoint with proper body
            payload = {
                'appliedFacets': {},
                'limit': 20,
                'offset': 0,
                'searchText': '',
            }
            r2 = client.post(api_url, json=payload)
            result['cxs_status'] = r2.status_code
            result['cxs_size'] = len(r2.text)
            result['cxs_content_type'] = r2.headers.get('content-type', '')
            result['cxs_preview'] = r2.text[:2000]
            result['duration_s'] = round(time.time() - start, 2)
            
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
    
    # Test 1: SeleniumBase UC Mode on Cloudflare-protected sites
    print("=" * 70)
    print("TEST 1: SeleniumBase UC Mode (Chrome with anti-detection)")
    print("=" * 70)
    
    test_sites = [
        ('indeed', 'https://www.indeed.com/jobs?q=software+engineer&l=San+Francisco%2C+CA'),
        ('glassdoor', 'https://www.glassdoor.com/Job/jobs.htm?sc.keyword=software+engineer'),
        ('ziprecruiter', 'https://www.ziprecruiter.com/jobs-search?search=software+engineer&location=San+Francisco'),
    ]
    
    for name, url in test_sites:
        print(f"\n--- Testing {name} with SeleniumBase UC ---")
        result = test_seleniumbase_uc(url, name, wait_seconds=10)
        results.append(result)
        if 'error' in result:
            print(f"  ERROR: {result['error'][:120]}")
        else:
            print(f"  title: {result.get('title', '')[:80]!r}")
            print(f"  text_size: {result.get('text_size', 0)}")
            print(f"  blocked: {result.get('blocked')}")
            print(f"  challenges: {result.get('challenges_detected')}")
            print(f"  preview: {result.get('text_preview', '')[:200]!r}")
    
    # Test 2: Workday CXS with correct URL pattern
    print()
    print("=" * 70)
    print("TEST 2: Workday CXS API (correct lowercase tenant slug + POST body)")
    print("=" * 70)
    
    workday_tenants = [
        ('bf.wd5.myworkdayjobs.com', 'bf', 'Brown-Forman'),
        ('netflix.wd5.myworkdayjobs.com', 'netflix', 'Netflix'),
        ('paypal.wd1.myworkdayjobs.com', 'paypal', 'PayPal'),
        ('nike.wd1.myworkdayjobs.com', 'nike', 'Nike'),
        ('ucsf.wd1.myworkdayjobs.com', 'ucsf', 'UCSF'),
        ('liveramp.wd1.myworkdayjobs.com', 'liveramp', 'LiveRamp'),
        ('atlassian.wd1.myworkdayjobs.com', 'atlassian', 'Atlassian'),
    ]
    
    for tenant_sub, tenant_lower, name in workday_tenants:
        print(f"\n--- Testing {name} ({tenant_sub}) ---")
        result = test_workday_cxs_correct(tenant_sub, tenant_lower, name)
        results.append(result)
        if 'error' in result:
            print(f"  ERROR: {result['error'][:120]}")
        else:
            print(f"  careers_page: {result.get('careers_page_status')} ({result.get('careers_page_size')} bytes)")
            print(f"  cxs_status: {result.get('cxs_status')} ({result.get('cxs_size')} bytes)")
            print(f"  cxs_content_type: {result.get('cxs_content_type')}")
            if 'job_count' in result:
                print(f"  job_count: {result.get('job_count')}")
            if 'total_jobs' in result:
                print(f"  total_jobs: {result.get('total_jobs')}")
            if 'sample_job' in result:
                print(f"  sample_job: {result.get('sample_job')}")
            preview = result.get('cxs_preview', '')[:300]
            print(f"  preview: {preview!r}")
    
    # Save results
    out_file = OUT_DIR / 'seleniumbase_workday_results.json'
    out_file.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print(f"\n✓ Results saved to {out_file}")
    
    # Summary
    print()
    print("=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    for r in results:
        name = r['name']
        strat = r['strategy']
        if 'error' in r:
            print(f"  {name:18s} | {strat:25s} | ERROR: {r['error'][:80]}")
        elif strat == 'workday_cxs_correct':
            cxs = r.get('cxs_status', 'N/A')
            jobs = r.get('job_count', r.get('total_jobs', 'N/A'))
            print(f"  {name:18s} | {strat:25s} | CXS={cxs} jobs={jobs}")
        else:
            blocked = r.get('blocked', '?')
            title = r.get('title', '')[:50]
            print(f"  {name:18s} | {strat:25s} | blocked={blocked} title={title!r}")


if __name__ == '__main__':
    main()
