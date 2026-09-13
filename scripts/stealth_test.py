#!/usr/bin/env python3
"""
Stealth browser automation tests for hardened job boards.
Tests Playwright with stealth plugin against:
- Indeed (Cloudflare Turnstile + JS challenge)
- LinkedIn (authwall, guest API already works)
- Glassdoor (Cloudflare Turnstile + "Humans only")
- ZipRecruiter (Cloudflare "Performing security verification")
- Workday per-tenant CXS JSON API

Strategy:
1. Test plain Playwright (baseline)
2. Test Playwright with playwright-stealth
3. Test curl_cffi with browser TLS fingerprint (Workday CXS)
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Add scripts dir to path
sys.path.insert(0, '/home/z/my-project/scripts')

OUT_DIR = Path('/home/z/my-project/research_data/stealth_tests')
OUT_DIR.mkdir(parents=True, exist_ok=True)

CHROME_PATH = '/home/z/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome'

async def test_plain_playwright(url, name, wait_ms=4000):
    """Baseline: plain Playwright with no stealth."""
    from playwright.async_api import async_playwright
    result = {'name': name, 'strategy': 'plain_playwright', 'url': url}
    start = time.time()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=CHROME_PATH,
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
            )
            page = await context.new_page()
            response = await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(wait_ms)
            
            title = await page.title()
            content = await page.content()
            
            # Get text content (strip scripts/styles)
            text = await page.evaluate('() => document.body.innerText')
            
            result['status'] = response.status if response else None
            result['title'] = title
            result['html_size'] = len(content)
            result['text_size'] = len(text)
            result['text_preview'] = text[:1500] if text else ''
            result['duration_s'] = round(time.time() - start, 2)
            
            # Check for known challenge indicators
            challenge_indicators = [
                'just a moment', 'cloudflare', 'humans only', 'verify you are human',
                'performing security verification', 'checking your browser',
                'request blocked', 'bot-detection', 'cf-challenge',
                'sign in to view', 'authenticating'
            ]
            text_lower = (title + ' ' + text).lower()
            result['challenges_detected'] = [c for c in challenge_indicators if c in text_lower]
            result['blocked'] = bool(result['challenges_detected'])
            
            # Save screenshot
            screenshot_path = OUT_DIR / f'{name}_plain.png'
            await page.screenshot(path=str(screenshot_path), full_page=False)
            result['screenshot'] = str(screenshot_path)
            
            # Save HTML
            html_path = OUT_DIR / f'{name}_plain.html'
            html_path.write_text(content, encoding='utf-8')
            
            await browser.close()
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {e}'
        result['duration_s'] = round(time.time() - start, 2)
    return result


async def test_stealth_playwright(url, name, wait_ms=6000):
    """Playwright with playwright-stealth."""
    from playwright.async_api import async_playwright
    # Lazy import — playwright_stealth has both sync and async APIs
    try:
        from playwright_stealth import stealth_async
    except ImportError:
        try:
            from playwright_stealth import Stealth
            stealth = Stealth()
        except ImportError:
            return {'name': name, 'strategy': 'stealth_playwright', 'error': 'playwright_stealth not installed'}
    
    result = {'name': name, 'strategy': 'stealth_playwright', 'url': url}
    start = time.time()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=CHROME_PATH,
                headless=True,
                args=[
                    '--no-sandbox', '--disable-setuid-sandbox',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-features=IsolateOrigins,site-per-process',
                ]
            )
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
                extra_http_headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Sec-Ch-Ua': '"Chromium";v="131", "Not_A Brand";v="24"',
                    'Sec-Ch-Ua-Mobile': '?0',
                    'Sec-Ch-Ua-Platform': '"Linux"',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                    'Upgrade-Insecure-Requests': '1',
                }
            )
            page = await context.new_page()
            
            # Apply stealth
            try:
                await stealth_async(page)
            except NameError:
                # Newer playwright_stealth API
                await stealth.apply_stealth_async(page)
            except Exception as e:
                result['stealth_apply_error'] = str(e)
            
            response = await page.goto(url, wait_until='domcontentloaded', timeout=45000)
            await page.wait_for_timeout(wait_ms)
            
            title = await page.title()
            content = await page.content()
            text = await page.evaluate('() => document.body.innerText')
            
            result['status'] = response.status if response else None
            result['title'] = title
            result['html_size'] = len(content)
            result['text_size'] = len(text)
            result['text_preview'] = text[:1500] if text else ''
            result['duration_s'] = round(time.time() - start, 2)
            
            challenge_indicators = [
                'just a moment', 'cloudflare', 'humans only', 'verify you are human',
                'performing security verification', 'checking your browser',
                'request blocked', 'bot-detection', 'cf-challenge',
                'sign in to view', 'authenticating'
            ]
            text_lower = (title + ' ' + text).lower()
            result['challenges_detected'] = [c for c in challenge_indicators if c in text_lower]
            result['blocked'] = bool(result['challenges_detected'])
            
            screenshot_path = OUT_DIR / f'{name}_stealth.png'
            await page.screenshot(path=str(screenshot_path), full_page=False)
            result['screenshot'] = str(screenshot_path)
            
            html_path = OUT_DIR / f'{name}_stealth.html'
            html_path.write_text(content, encoding='utf-8')
            
            await browser.close()
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {e}'
        result['duration_s'] = round(time.time() - start, 2)
    return result


async def test_workday_cxs(tenant_subdomain, tenant_slug, name):
    """Test Workday CXS JSON API directly with browser-like headers."""
    import httpx
    result = {'name': name, 'strategy': 'workday_cxs', 'tenant': tenant_subdomain}
    
    # Try multiple Workday customer tenants
    api_url = f'https://{tenant_subdomain}/wday/cxs/en-US/{tenant_slug}/{tenant_slug}/jobs'
    start = time.time()
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            http2=True,
            headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Origin': f'https://{tenant_subdomain}',
                'Referer': f'https://{tenant_subdomain}/en-US/{tenant_slug}',
                'Sec-Ch-Ua': '"Chromium";v="131", "Not_A Brand";v="24"',
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Ch-Ua-Platform': '"Linux"',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
            }
        ) as client:
            # First GET the careers page to grab a session cookie
            careers_url = f'https://{tenant_subdomain}/en-US/{tenant_slug}'
            r1 = await client.get(careers_url)
            result['careers_page_status'] = r1.status_code
            result['careers_page_size'] = len(r1.text)
            
            # Then POST to the CXS endpoint
            r2 = await client.post(api_url, json={'appliedFacets': {}, 'limit': 20, 'offset': 0, 'searchText': ''})
            result['cxs_status'] = r2.status_code
            result['cxs_size'] = len(r2.text)
            result['cxs_content_type'] = r2.headers.get('content-type', '')
            result['cxs_preview'] = r2.text[:2000]
            result['duration_s'] = round(time.time() - start, 2)
            
            # Try to parse JSON
            if 'json' in r2.headers.get('content-type', '').lower():
                try:
                    data = r2.json()
                    result['cxs_json_keys'] = list(data.keys()) if isinstance(data, dict) else 'list'
                    if isinstance(data, dict) and 'jobPostings' in data:
                        result['job_count'] = len(data['jobPostings'])
                        if data['jobPostings']:
                            result['sample_job'] = {
                                'title': data['jobPostings'][0].get('title', ''),
                                'externalPath': data['jobPostings'][0].get('externalPath', ''),
                                'locations': data['jobPostings'][0].get('locations', []),
                            }
                except Exception as e:
                    result['json_parse_error'] = str(e)
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {e}'
        result['duration_s'] = round(time.time() - start, 2)
    return result


async def test_curl_cffi(url, name, impersonate='chrome131'):
    """Test curl_cffi with Chrome TLS fingerprint."""
    from curl_cffi import requests as cffi_requests
    result = {'name': name, 'strategy': 'curl_cffi', 'impersonate': impersonate, 'url': url}
    start = time.time()
    try:
        r = cffi_requests.get(
            url,
            impersonate=impersonate,
            timeout=20,
            allow_redirects=True,
        )
        result['status'] = r.status_code
        result['size'] = len(r.text)
        result['duration_s'] = round(time.time() - start, 2)
        result['text_preview'] = r.text[:2000]
        
        challenge_indicators = [
            'just a moment', 'cloudflare', 'humans only', 'verify you are human',
            'performing security verification', 'checking your browser',
            'request blocked', 'bot-detection', 'cf-challenge',
        ]
        text_lower = r.text.lower()
        result['challenges_detected'] = [c for c in challenge_indicators if c in text_lower]
        result['blocked'] = bool(result['challenges_detected'])
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {e}'
        result['duration_s'] = round(time.time() - start, 2)
    return result


async def main():
    results = []
    
    # Test sites
    sites = [
        ('indeed', 'https://www.indeed.com/jobs?q=software+engineer&l=San+Francisco%2C+CA'),
        ('linkedin', 'https://www.linkedin.com/jobs/search/?keywords=software+engineer&location=San+Francisco'),
        ('glassdoor', 'https://www.glassdoor.com/Job/jobs.htm?sc.keyword=software+engineer'),
        ('ziprecruiter', 'https://www.ziprecruiter.com/jobs-search?search=software+engineer&location=San+Francisco'),
    ]
    
    # Workday tenants to test (varied)
    workday_tenants = [
        (' bf.wd5.myworkdayjobs.com', 'BF', 'brown_forman'),  # Brown-Forman
        ('netflix.wd5.myworkdayjobs.com', 'Netflix', 'netflix'),  # Netflix
        ('paypal.wd1.myworkdayjobs.com', 'PayPal', 'paypal'),  # PayPal
        ('nike.wd1.myworkdayjobs.com', 'Nike', 'nike'),  # Nike
        ('ucsf.wd1.myworkdayjobs.com', 'UCSF', 'ucsf'),  # UC San Francisco
    ]
    
    print("=" * 60)
    print("PHASE 1: Plain Playwright (baseline)")
    print("=" * 60)
    for name, url in sites:
        print(f"Testing {name}...")
        result = await test_plain_playwright(url, name, wait_ms=4000)
        results.append(result)
        print(f"  → status={result.get('status')} title={result.get('title', '')[:80]!r} blocked={result.get('blocked')}")
    
    print()
    print("=" * 60)
    print("PHASE 2: Stealth Playwright (with playwright-stealth)")
    print("=" * 60)
    for name, url in sites:
        print(f"Testing {name}...")
        result = await test_stealth_playwright(url, name, wait_ms=8000)
        results.append(result)
        print(f"  → status={result.get('status')} title={result.get('title', '')[:80]!r} blocked={result.get('blocked')}")
    
    print()
    print("=" * 60)
    print("PHASE 3: curl_cffi (Chrome TLS fingerprint)")
    print("=" * 60)
    for name, url in sites:
        print(f"Testing {name}...")
        result = await test_curl_cffi(url, name, impersonate='chrome131')
        results.append(result)
        print(f"  → status={result.get('status')} size={result.get('size')} blocked={result.get('blocked')}")
    
    print()
    print("=" * 60)
    print("PHASE 4: Workday per-tenant CXS JSON API")
    print("=" * 60)
    for tenant_sub, tenant_slug, name in workday_tenants:
        print(f"Testing {name} ({tenant_sub})...")
        result = await test_workday_cxs(tenant_sub.strip(), tenant_slug, name)
        results.append(result)
        print(f"  → careers={result.get('careers_page_status')} cxs={result.get('cxs_status')} jobs={result.get('job_count', 'N/A')}")
    
    # Save all results
    out_file = OUT_DIR / 'all_results.json'
    out_file.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print(f"\n✓ All results saved to {out_file}")
    
    # Summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for r in results:
        name = r['name']
        strategy = r['strategy']
        if 'error' in r:
            print(f"  {name:20s} | {strategy:25s} | ERROR: {r['error'][:60]}")
        elif strategy == 'workday_cxs':
            cxs_status = r.get('cxs_status', 'N/A')
            jobs = r.get('job_count', 'N/A')
            print(f"  {name:20s} | {strategy:25s} | CXS={cxs_status} jobs={jobs}")
        else:
            status = r.get('status', 'N/A')
            blocked = r.get('blocked', False)
            challenges = r.get('challenges_detected', [])
            challenge_str = ','.join(challenges[:2]) if challenges else 'none'
            print(f"  {name:20s} | {strategy:25s} | status={status} blocked={blocked} challenges={challenge_str}")


if __name__ == '__main__':
    asyncio.run(main())
