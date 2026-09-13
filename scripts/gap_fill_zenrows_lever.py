#!/usr/bin/env python3
"""
Gap-fill 2: ZenRows at scale + better Lever slugs + Workday tenant discovery.

1. ZenRows: Send 10 sequential ZipRecruiter requests with different keywords.
   - Does rate stay stable?
   - Does cost stay linear?
   - Does Cloudflare ever trigger?
2. Lever: Try known-good slugs (from prior research: coursera was 404, but plaid returned []).
   Test more companies.
3. Workday: Discover Job_Posting_Site_ID via Playwright rendering of the SPA.
"""
import os
import json
import time
import re
from pathlib import Path
import requests

OUT = Path('/home/z/my-project/research_data/gap_fill')
OUT.mkdir(parents=True, exist_ok=True)

ZENROWS_API_KEY = os.environ.get("ZENROWS_API_KEY", "")
ZENROWS_URL = "https://api.zenrows.com/v1/"


def test_zenrows_scale(n_requests=10):
    """Send N ZenRows requests to ZipRecruiter with varying keywords. Track cost, time, success."""
    print(f"\n{'='*70}")
    print(f"ZenRows Scale Test: {n_requests} sequential requests to ZipRecruiter")
    print(f"{'='*70}")
    
    queries = [
        ('software engineer', 'San Francisco'),
        ('frontend engineer', 'Remote'),
        ('backend engineer', 'Remote'),
        ('data scientist', 'New York'),
        ('product manager', 'Remote'),
        ('devops engineer', 'Remote'),
        ('machine learning engineer', 'San Francisco'),
        ('staff engineer', 'Remote'),
        ('engineering manager', 'Remote'),
        ('full stack engineer', 'Austin'),
    ][:n_requests]
    
    results = []
    for i, (kw, loc) in enumerate(queries):
        print(f"\n[{i+1}/{n_requests}] {kw} @ {loc}")
        url = f'https://www.ziprecruiter.com/jobs-search?search={kw.replace(" ", "+")}&location={loc.replace(" ", "+")}'
        params = {
            'apikey': ZENROWS_API_KEY,
            'url': url,
            'js_render': 'true',
            'premium_proxy': 'true',
            'proxy_country': 'us',
            'original_status': 'true',
            'wait': '4000',
        }
        start = time.time()
        try:
            r = requests.get(ZENROWS_URL, params=params, timeout=120)
            duration = round(time.time() - start, 2)
            articles = re.findall(r'<article[^>]*>(.*?)</article>', r.text, re.DOTALL)
            # Count unique job titles (dedupe within page)
            titles = set()
            for art in articles:
                h2s = re.findall(r'<h2[^>]*>([^<]+)</h2>', art)
                for h in h2s:
                    h = h.strip()
                    if h and 'share' not in h.lower() and 'be seen' not in h.lower():
                        titles.add(h)
                        break
            result = {
                'i': i + 1, 'query': f'{kw} @ {loc}',
                'status': r.status_code, 'size': len(r.text),
                'duration_s': duration, 'articles': len(articles),
                'unique_titles': len(titles),
            }
            results.append(result)
            print(f"  status={r.status_code}, size={len(r.text):,}b, dur={duration}s, articles={len(articles)}, unique_titles={len(titles)}")
            # Check for Cloudflare
            if 'just a moment' in r.text.lower() or 'forbidden cf-waf' in r.text.lower():
                print(f"  ⚠️ Cloudflare challenge detected!")
                result['cloudflare_challenge'] = True
            else:
                result['cloudflare_challenge'] = False
            # Check for ZenRows error responses
            if r.status_code != 200:
                try:
                    err = r.json()
                    result['zenrows_error'] = err
                    print(f"  ZenRows error: {err}")
                except Exception:
                    pass
        except Exception as e:
            duration = round(time.time() - start, 2)
            print(f"  ERROR after {duration}s: {e}")
            results.append({'i': i + 1, 'query': f'{kw} @ {loc}', 'error': str(e)[:200], 'duration_s': duration})
    
    # Summary
    print(f"\n{'='*70}")
    print(f"ZenRows Scale Summary")
    print(f"{'='*70}")
    successes = [r for r in results if r.get('status') == 200]
    failures = [r for r in results if r.get('status') != 200 or 'error' in r]
    total_duration = sum(r.get('duration_s', 0) for r in results)
    avg_duration = total_duration / len(results) if results else 0
    total_jobs = sum(r.get('unique_titles', 0) for r in successes)
    
    print(f"  Successes: {len(successes)}/{len(results)}")
    print(f"  Failures: {len(failures)}")
    print(f"  Total time: {total_duration:.1f}s")
    print(f"  Avg per request: {avg_duration:.2f}s")
    print(f"  Total jobs extracted: {total_jobs}")
    print(f"  Cloudflare challenges: {sum(1 for r in successes if r.get('cloudflare_challenge'))}")
    
    # ZenRows credit cost calculation
    # Per docs: js_render=true + premium_proxy=true = 25 credits per successful request
    # (this varies — need to confirm)
    est_credits = len(successes) * 25
    print(f"  Est. ZenRows credits used: ~{est_credits} (at 25 credits per successful request)")
    
    out_file = OUT / 'zenrows_scale_results.json'
    out_file.write_text(json.dumps({
        'test': 'zenrows_scale_ziprecruiter',
        'n_requests': n_requests,
        'successes': len(successes),
        'failures': len(failures),
        'total_duration_s': total_duration,
        'avg_duration_s': avg_duration,
        'total_jobs_extracted': total_jobs,
        'est_credits_used': est_credits,
        'results': results,
    }, indent=2, default=str), encoding='utf-8')
    print(f"\n✓ Saved to {out_file}")


def test_more_lever_slugs():
    """Try many more Lever slugs to find ones with actual jobs."""
    print(f"\n{'='*70}")
    print(f"Lever slug discovery")
    print(f"{'='*70}")
    
    # Try many possible slugs
    slugs = [
        # Tech companies
        'plaid', 'square', 'dropbox', 'gitlab', 'notion', 'vercel',
        # More tech
        'figma', 'loom', 'linear', 'monzo', 'revolut', 'brex', 'mercury',
        'segment', 'twilio', 'sendgrid', 'mailchimp', 'hubspot',
        # Stripe, Airbnb, etc are NOT on Lever
        'atlassian', 'digitalocean', 'cloudflare', 'fastly',
        # Startups that might be on Lever
        'doordash', 'instacart', 'roblox', 'unity-technologies',
        # Other guesses
        'nerdwallet', 'creditkarma', 'plaid', 'datadog',
    ]
    
    results = []
    for slug in slugs:
        try:
            r = requests.get(
                f'https://api.lever.co/v0/postings/{slug}?limit=500',
                timeout=10, headers={'User-Agent': 'gap-fill-test/1.0'}
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    n = len(data)
                    if n > 0:
                        sample = data[0].get('text', '') if data else ''
                        print(f"  ✅ {slug}: {n} jobs (sample: {sample[:50]})")
                        results.append({'slug': slug, 'job_count': n, 'sample_title': sample})
                    else:
                        print(f"  ⚠️ {slug}: 0 jobs (valid slug, no openings)")
                else:
                    print(f"  ⚠️ {slug}: non-list response")
            else:
                pass  # 404 - not a Lever slug
        except Exception as e:
            pass
        time.sleep(0.3)
    
    out_file = OUT / 'lever_slug_discovery.json'
    out_file.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(f"\n✓ Saved to {out_file}")
    print(f"\nSummary: {len(results)} Lever companies with > 0 jobs")


if __name__ == '__main__':
    test_more_lever_slugs()
    test_zenrows_scale(n_requests=5)  # 5 to keep within timeout
