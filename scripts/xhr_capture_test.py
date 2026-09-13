#!/usr/bin/env python3
"""
Test 4: Use Playwright to render Workday SPA, capture XHR requests to /wday/cxs/,
then replay the captured request with curl_cffi using stored cookies.
Also: try the LinkedIn guest API with proper headers via curl_cffi.
"""
import asyncio
import json
import time
from pathlib import Path
from playwright.async_api import async_playwright
from curl_cffi import requests as cffi_requests

OUT_DIR = Path('/home/z/my-project/research_data/stealth_tests')
CHROME_PATH = '/home/z/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome'


async def render_and_capture_xhr(url, name, wait_ms=8000):
    """Load a page in Playwright, capture all XHR requests, return them."""
    result = {'name': name, 'strategy': 'render_capture_xhr', 'url': url}
    captured_xhrs = []
    start = time.time()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=CHROME_PATH,
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-blink-features=AutomationControlled']
            )
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
                extra_http_headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                }
            )
            page = await context.new_page()
            
            # Capture XHR requests
            async def on_request(request):
                if '/wday/cxs/' in request.url or '/jobs' in request.url:
                    captured_xhrs.append({
                        'url': request.url,
                        'method': request.method,
                        'headers': dict(request.headers),
                        'post_data': request.post_data,
                    })
            page.on('request', on_request)
            
            response = await page.goto(url, wait_until='networkidle', timeout=30000)
            await page.wait_for_timeout(wait_ms)
            
            # Get all cookies for replay
            cookies = await context.cookies()
            
            result['status'] = response.status if response else None
            result['final_url'] = page.url
            result['title'] = await page.title()
            result['text'] = (await page.evaluate('() => document.body.innerText'))[:500]
            result['cookies'] = [{'name': c['name'], 'value': c['value'][:80]} for c in cookies]
            result['captured_xhr_count'] = len(captured_xhrs)
            result['captured_xhrs'] = captured_xhrs[:5]
            result['duration_s'] = round(time.time() - start, 2)
            
            await browser.close()
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:300]}'
        result['duration_s'] = round(time.time() - start, 2)
    return result


def replay_xhr_with_curl_cffi(xhr, cookies_dict, name):
    """Replay the captured XHR request using curl_cffi."""
    result = {'name': name, 'strategy': 'replay_curl_cffi', 'url': xhr['url']}
    start = time.time()
    try:
        if xhr['method'] == 'POST':
            r = cffi_requests.post(
                xhr['url'],
                headers=xhr['headers'],
                cookies=cookies_dict,
                data=xhr['post_data'] or '',
                impersonate='chrome131',
                timeout=15,
            )
        else:
            r = cffi_requests.get(
                xhr['url'],
                headers=xhr['headers'],
                cookies=cookies_dict,
                impersonate='chrome131',
                timeout=15,
            )
        result['status'] = r.status_code
        result['size'] = len(r.text)
        result['content_type'] = r.headers.get('content-type', '')
        result['preview'] = r.text[:500]
        result['duration_s'] = round(time.time() - start, 2)
        
        if 'json' in r.headers.get('content-type', '').lower():
            try:
                data = r.json()
                if isinstance(data, dict):
                    result['json_keys'] = list(data.keys())
                    if 'jobPostings' in data:
                        result['job_count'] = len(data['jobPostings'])
                        if data['jobPostings']:
                            j = data['jobPostings'][0]
                            result['sample_job'] = {
                                'title': j.get('title'),
                                'externalPath': j.get('externalPath'),
                                'locations': j.get('locations'),
                                'postedOn': j.get('postedOn'),
                            }
                    if 'total' in data:
                        result['total_jobs'] = data.get('total')
            except Exception as e:
                result['json_parse_error'] = str(e)
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:300]}'
    return result


async def test_linkedin_guest_api_cffi():
    """Test LinkedIn guest API with curl_cffi and proper headers."""
    url = 'https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=software+engineer&location=San+Francisco&start=0'
    result = {'name': 'linkedin_guest_api', 'strategy': 'curl_cffi_guest_api', 'url': url}
    start = time.time()
    try:
        r = cffi_requests.get(
            url,
            impersonate='chrome131',
            timeout=20,
            headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://www.linkedin.com/jobs/search/?keywords=software+engineer&location=San+Francisco',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
            }
        )
        result['status'] = r.status_code
        result['size'] = len(r.text)
        result['duration_s'] = round(time.time() - start, 2)
        result['preview'] = r.text[:1500]
        
        # Count job cards in the response
        import re
        # LinkedIn job cards have data-entity-urn="urn:li:jobPosting:NUMBER"
        job_ids = re.findall(r'data-entity-urn="urn:li:jobPosting:(\d+)"', r.text)
        result['job_ids_extracted'] = len(job_ids)
        result['sample_job_ids'] = job_ids[:5]
        
        # Extract job titles
        titles = re.findall(r'<h3 class="base-search-card__title">\s*([^<]+?)\s*</h3>', r.text)
        result['titles_extracted'] = len(titles)
        result['sample_titles'] = [t.strip() for t in titles[:5]]
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:300]}'
    return result


async def main():
    results = []
    
    print("=" * 70)
    print("PHASE 1: Render Workday SPA + capture XHR calls")
    print("=" * 70)
    
    workday_pages = [
        ('https://www.myworkdayjobs.com/paypal', 'PayPal'),
        ('https://www.myworkdayjobs.com/nike', 'Nike'),
        ('https://careers.netflix.com', 'Netflix'),
        ('https://www.paypal.com/careers', 'PayPalCareers'),
    ]
    
    # Try the more direct approach — visit well-known company careers pages that use Workday
    # These usually have a path like /en-US/{SiteID}
    
    discovered_tenants = [
        # Real Workday career sites known to use the pattern /en-US/{tenant}/{SiteID}
        ('https://paypal.wd1.myworkdayjobs.com/en-US/PayPal/job/Software-Engineer_R123456', 'paypal', 'PayPal'),
        # Let me also try careers.netflix.com which is a CNAMEd Workday site
        ('https://careers.netflix.com/jobs', 'netflix', 'Netflix'),
    ]
    
    # Simpler: just visit these URLs and see what happens
    test_urls = [
        ('https://careers.netflix.com', 'NetflixCareers'),
        ('https://paypal.wd1.myworkdayjobs.com/en-US/PayPal', 'PayPalWorkday'),
        ('https://www.myworkdayjobs.com/paypal', 'PayPalAggregate'),
    ]
    
    for url, name in test_urls:
        print(f"\n--- {name}: {url} ---")
        result = await render_and_capture_xhr(url, name, wait_ms=10000)
        results.append(result)
        if 'error' in result:
            print(f"  ERROR: {result['error']}")
            continue
        print(f"  status: {result.get('status')}")
        print(f"  final_url: {result.get('final_url', '')[:100]}")
        print(f"  title: {result.get('title', '')[:80]!r}")
        print(f"  captured_xhr_count: {result.get('captured_xhr_count')}")
        if result.get('captured_xhrs'):
            for xhr in result['captured_xhrs'][:3]:
                print(f"    XHR: {xhr['method']} {xhr['url'][:120]}")
        print(f"  cookies: {[c['name'] for c in result.get('cookies', [])]}")
        print(f"  text preview: {result.get('text', '')[:200]!r}")
        
        # If we captured a CXS XHR, replay it with curl_cffi
        cxs_xhrs = [x for x in result.get('captured_xhrs', []) if '/wday/cxs/' in x['url']]
        if cxs_xhrs:
            print(f"\n  >>> Replaying CXS XHR with curl_cffi:")
            cookies_dict = {c['name']: c['value'] for c in result.get('cookies', [])}
            replay_result = replay_xhr_with_curl_cffi(cxs_xhrs[0], cookies_dict, f'{name}_replay')
            results.append(replay_result)
            if 'error' in replay_result:
                print(f"    ERROR: {replay_result['error']}")
            else:
                print(f"    status: {replay_result.get('status')}")
                print(f"    size: {replay_result.get('size')}")
                print(f"    content_type: {replay_result.get('content_type')}")
                if 'job_count' in replay_result:
                    print(f"    job_count: {replay_result.get('job_count')}")
                if 'total_jobs' in replay_result:
                    print(f"    total_jobs: {replay_result.get('total_jobs')}")
                if 'sample_job' in replay_result:
                    print(f"    sample_job: {replay_result.get('sample_job')}")
    
    print()
    print("=" * 70)
    print("PHASE 2: LinkedIn guest API via curl_cffi (validate pagination)")
    print("=" * 70)
    
    result = await test_linkedin_guest_api_cffi()
    results.append(result)
    if 'error' in result:
        print(f"  ERROR: {result['error']}")
    else:
        print(f"  status: {result.get('status')}")
        print(f"  size: {result.get('size')} bytes")
        print(f"  job_ids_extracted: {result.get('job_ids_extracted')}")
        print(f"  titles_extracted: {result.get('titles_extracted')}")
        print(f"  sample_titles: {result.get('sample_titles')}")
        print(f"  duration: {result.get('duration_s')}s")
    
    out_file = OUT_DIR / 'xhr_capture_results.json'
    out_file.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print(f"\n✓ Results saved to {out_file}")


if __name__ == '__main__':
    asyncio.run(main())
