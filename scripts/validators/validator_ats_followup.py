#!/usr/bin/env python3
"""Follow-up ATS-direct probes with real customer slugs."""
import json, time
from pathlib import Path
import requests

OUT = Path('/home/z/my-project/job-sourcing/track3-js/validation_results/aggregators_ats')
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'}

def probe(name, url):
    print(f"\n[{name}] {url[:90]}")
    start = time.time()
    try:
        r = requests.get(url, headers=UA, timeout=25)
        dur = round(time.time()-start, 2)
        size = len(r.text)
        ctype = r.headers.get('content-type','')[:80]
        parsed = None
        if 'json' in ctype.lower():
            try: parsed = r.json()
            except Exception: pass
        job_count = None
        sample = None
        if isinstance(parsed, dict):
            for key in ('jobs', 'postings', 'jobPostings', 'data', 'items', 'results'):
                v = parsed.get(key)
                if isinstance(v, list):
                    job_count = len(v)
                    if v and isinstance(v[0], dict):
                        sample = {k: str(v[0].get(k,''))[:80] for k in list(v[0].keys())[:6]}
                    break
            if 'total' in parsed and not job_count:
                job_count = parsed.get('total')
        elif isinstance(parsed, list):
            job_count = len(parsed)
            if parsed and isinstance(parsed[0], dict):
                sample = {k: str(parsed[0].get(k,''))[:80] for k in list(parsed[0].keys())[:6]}
        print(f"  status={r.status_code} size={size:,}b dur={dur}s job_count={job_count}")
        if sample: print(f"  sample={sample}")
        return {'name': name, 'url': url, 'status': r.status_code, 'size': size,
                'content_type': ctype, 'duration_s': dur,
                'job_count': job_count, 'sample': sample,
                'preview': r.text[:200]}
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {str(e)[:200]}")
        return {'name': name, 'url': url, 'error': f'{type(e).__name__}: {str(e)[:200]}'}

def main():
    print('='*70); print('Follow-up ATS probes with correct customer slugs'); print('='*70)
    results = []
    # Greenhouse: real customers (stripe is greenhouse customer? actually stripe uses greenhouse)
    results.append(probe('greenhouse_stripe', 'https://boards-api.greenhouse.io/v1/boards/stripe/jobs?count=true'))
    time.sleep(1.5)
    results.append(probe('greenhouse_airbnb', 'https://boards-api.greenhouse.io/v1/boards/airbnb/jobs?count=true'))
    time.sleep(1.5)
    # Lever real customers
    results.append(probe('lever_plaid', 'https://api.lever.co/v0/postings/plaid?mode=json&limit=50'))
    time.sleep(1.5)
    results.append(probe('lever_dropbox', 'https://api.lever.co/v0/postings/dropbox?mode=json&limit=50'))
    time.sleep(1.5)
    results.append(probe('lever_notion', 'https://api.lever.co/v0/postings/notion?mode=json&limit=50'))
    time.sleep(1.5)
    # SmartRecruiters real customer (smartrecruiters customers: visa, ibm)
    results.append(probe('smartrecruiters_ibm', 'https://api.smartrecruiters.com/v1/companies/ibm/jobs?limit=10'))
    time.sleep(1.5)
    results.append(probe('smartrecruiters_visa', 'https://api.smartrecruiters.com/v1/companies/visa/jobs?limit=10'))
    time.sleep(1.5)
    # Workable - the endpoint pattern is actually /api/accounts/{account}/jobs?... let's check
    # Actually Workable uses https://www.workable.com/sp/v1/jobs/?account=company_slug
    # Real workable customers include "graphite", "frame", etc. but I don't know slugs.
    # The known endpoint: https://apply.workable.com/api/v1/{account}/jobs?state=published
    results.append(probe('workable_apply_endpoint', 'https://apply.workable.com/api/v1/smartjobs/jobs?state=published'))
    time.sleep(1.5)
    # Personio retry - small test with another slug
    results.append(probe('personio_personio_xml', 'https://personio.jobs.personio.de/xml'))
    time.sleep(1.5)
    # Jobvite - another ATS
    results.append(probe('jobvite_lever', 'https://www.jobvite.com/api/v1/jobs?company=lever'))
    time.sleep(1.5)
    (OUT / 'ats_followup.json').write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print('\n✓ Saved')

if __name__ == '__main__':
    main()
