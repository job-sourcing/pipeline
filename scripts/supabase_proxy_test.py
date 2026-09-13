#!/usr/bin/env python3
"""
Test Supabase Edge Proxy features:
1. Basic proxy with IP rotation
2. Region pinning (15 AWS edge regions)
3. Mode switching (fetch / http2 / raw)
4. Test against LinkedIn guest API (to see if it works there)
5. Test against Cloudflare-blocked sites (probably won't work, but worth confirming)
"""
import os
import json
import time
from pathlib import Path
import requests

OUT_DIR = Path('/home/z/my-project/research_data/supabase_proxy_tests')
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Env-only credentials (S8-A scrub): os.environ first, then ingest/.env
# (the committed secret store) — never hardcoded.
def _env_file_value(name: str) -> str:
    """Read `name` from ingest/.env (KEY=value lines) if present."""
    env_path = Path(__file__).resolve().parents[1] / "ingest" / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""

PROXY_URL = (os.environ.get("SUPABASE_PROXY_URL")
             or _env_file_value("SUPABASE_PROXY_URL"))
TOKEN = (os.environ.get("SUPABASE_PROXY_TOKEN")
         or _env_file_value("SUPABASE_PROXY_TOKEN"))
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def test_health():
    """Check the proxy is alive."""
    print("--- Health check ---")
    try:
        r = requests.get(f"{PROXY_URL.rsplit('/', 1)[0]}/health", timeout=15)
        print(f"  status: {r.status_code}")
        print(f"  body: {r.text}")
        return r.json()
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def test_basic(url, name):
    """Basic proxy GET."""
    print(f"\n--- Basic proxy: {name} ---")
    print(f"  URL: {url[:80]}")
    result = {'name': name, 'strategy': 'supabase_basic', 'url': url}
    start = time.time()
    try:
        r = requests.get(f"{PROXY_URL}?url={url}", headers=HEADERS, timeout=30)
        result['status'] = r.status_code
        result['size'] = len(r.text)
        result['duration_s'] = round(time.time() - start, 2)
        result['x_proxy_status'] = r.headers.get('x-proxy-status')
        result['x_proxy_mode'] = r.headers.get('x-proxy-mode')
        result['x_proxy_target_host'] = r.headers.get('x-proxy-target-host')
        result['x_proxy_timing_ms'] = r.headers.get('x-proxy-timing-ms')
        result['x_proxy_cached'] = r.headers.get('x-proxy-cached')
        result['x_sb_edge_region'] = r.headers.get('x-sb-edge-region')
        result['x_ratelimit_remaining'] = r.headers.get('x-ratelimit-remaining')
        result['preview'] = r.text[:1000]
        
        # Check for challenges
        challenge_indicators = ['just a moment', 'cloudflare', 'humans only', 'verify you are human',
                                'performing security verification', 'request blocked', 'forbidden cf-waf',
                                'security check', 'additional verification']
        text_lower = r.text.lower()
        result['challenges'] = [c for c in challenge_indicators if c in text_lower]
        result['blocked'] = bool(result['challenges'])
        
        print(f"  status: {result['status']}, size: {result['size']:,}b, duration: {result['duration_s']}s")
        print(f"  x-proxy-status: {result['x_proxy_status']}, mode: {result['x_proxy_mode']}")
        print(f"  x-sb-edge-region: {result['x_sb_edge_region']}, remaining: {result['x_ratelimit_remaining']}")
        print(f"  challenges: {result['challenges']}")
        print(f"  preview: {result['preview'][:200]!r}")
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:200]}'
        print(f"  ERROR: {result['error']}")
    return result


def test_region_pin(url, region, name):
    """Test with region pinned to specific AWS edge region."""
    print(f"\n--- Region pin ({region}): {name} ---")
    result = {'name': name, 'strategy': f'supabase_region_{region}', 'url': url, 'region': region}
    start = time.time()
    try:
        r = requests.get(
            f"{PROXY_URL}?url={url}",
            headers={**HEADERS, 'x-region': region},
            timeout=30
        )
        result['status'] = r.status_code
        result['size'] = len(r.text)
        result['duration_s'] = round(time.time() - start, 2)
        result['x_sb_edge_region'] = r.headers.get('x-sb-edge-region')
        result['x_proxy_status'] = r.headers.get('x-proxy-status')
        result['preview'] = r.text[:500]
        print(f"  status: {result['status']}, size: {result['size']:,}b, edge: {result['x_sb_edge_region']}")
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:200]}'
        print(f"  ERROR: {result['error']}")
    return result


def test_modes(url, name):
    """Test all three modes (fetch / http2 / raw) on a URL."""
    print(f"\n--- Mode comparison: {name} ---")
    results = []
    for mode in ['fetch', 'http2', 'raw']:
        print(f"\n  Mode: {mode}")
        result = {'name': name, 'strategy': f'supabase_mode_{mode}', 'url': url, 'mode': mode}
        start = time.time()
        try:
            r = requests.get(
                f"{PROXY_URL}?url={url}&mode={mode}",
                headers=HEADERS,
                timeout=30
            )
            result['status'] = r.status_code
            result['size'] = len(r.text)
            result['duration_s'] = round(time.time() - start, 2)
            result['x_proxy_mode'] = r.headers.get('x-proxy-mode')
            result['x_proxy_status'] = r.headers.get('x-proxy-status')
            result['x_sb_edge_region'] = r.headers.get('x-sb-edge-region')
            result['preview'] = r.text[:300]
            print(f"    status: {result['status']}, size: {result['size']:,}b, duration: {result['duration_s']}s")
            print(f"    edge region: {result['x_sb_edge_region']}")
        except Exception as e:
            result['error'] = f'{type(e).__name__}: {str(e)[:200]}'
            print(f"    ERROR: {result['error']}")
        results.append(result)
    return results


def test_ip_rotation(url, n=5):
    """Call the same URL N times to confirm IP rotation."""
    print(f"\n--- IP rotation test: {n} calls to {url[:60]} ---")
    seen_ips = []
    for i in range(n):
        try:
            r = requests.get(f"{PROXY_URL}?url={url}", headers=HEADERS, timeout=20)
            # Try to parse IP from response
            try:
                data = r.json()
                ip = data.get('ip', '?')
                city = data.get('city', '?')
                country = data.get('country', '?')
                region = r.headers.get('x-sb-edge-region', '?')
                print(f"  call {i+1}: IP={ip}, city={city}, country={country}, edge={region}")
                seen_ips.append({'ip': ip, 'city': city, 'country': country, 'edge': region})
            except Exception:
                print(f"  call {i+1}: status={r.status_code}, size={len(r.text)}b, edge={r.headers.get('x-sb-edge-region')}")
                seen_ips.append({'edge': r.headers.get('x-sb-edge-region')})
        except Exception as e:
            print(f"  call {i+1}: ERROR {e}")
    return seen_ips


def main():
    print("=" * 70)
    print("Supabase Edge Proxy — Validation Tests")
    print("=" * 70)
    
    results = []
    
    # 1. Health check
    health = test_health()
    if not health or not health.get('ok'):
        print("Proxy not healthy — aborting.")
        return
    
    # 2. Test IP rotation (multiple calls to ipinfo.io)
    seen_ips = test_ip_rotation("https://ipinfo.io/json", n=5)
    results.append({'test': 'ip_rotation', 'results': seen_ips})
    
    # 3. Region pinning test
    regions_to_test = ['us-east-1', 'eu-west-1', 'ap-northeast-1']
    for region in regions_to_test:
        result = test_region_pin("https://ipinfo.io/json", region, f"ipinfo_{region}")
        results.append(result)
    
    # 4. Test against LinkedIn guest API (should work — no Cloudflare wall on that endpoint)
    li_url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=software+engineer&location=San+Francisco&start=0"
    li_result = test_basic(li_url, "linkedin_guest_api")
    results.append(li_result)
    
    # 5. Test against Cloudflare-blocked sites (probably won't work, but confirm)
    cf_sites = [
        ('indeed', 'https://www.indeed.com/jobs?q=software+engineer'),
        ('glassdoor', 'https://www.glassdoor.com/Job/jobs.htm?sc.keyword=software+engineer'),
        ('ziprecruiter', 'https://www.ziprecruiter.com/jobs-search?search=software+engineer'),
    ]
    for name, url in cf_sites:
        result = test_basic(url, name)
        results.append(result)
    
    # 6. Test modes on LinkedIn guest API
    mode_results = test_modes(li_url, "linkedin_guest_api_modes")
    results.extend(mode_results)
    
    # Save all results
    out_file = OUT_DIR / 'supabase_proxy_results.json'
    out_file.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print(f"\n✓ All results saved to {out_file}")
    
    # Summary
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for r in results:
        if 'test' in r:
            print(f"  {r['test']}: {len(r.get('results', []))} calls")
        else:
            name = r.get('name', '?')
            strat = r.get('strategy', '?')
            status = r.get('status', r.get('x_proxy_status', '?'))
            size = r.get('size', '?')
            blocked = r.get('blocked', '?')
            print(f"  {name:30s} | {strat:30s} | status={status} | size={size} | blocked={blocked}")


if __name__ == '__main__':
    main()
