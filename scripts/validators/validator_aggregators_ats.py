#!/usr/bin/env python3
"""Probe free aggregator APIs, ATS-direct endpoints, no-key aggregators.
- 6 free aggregators (adzuna, jsearch, usajobs, findwork, jooble, careerjet)
- 7 ATS-direct endpoints (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Personio)
- 5 no-key aggregators (HN, RemoteOK, WWR, Remotive, arbeitnow)
Total ~18 calls, sleep 1-2s between each.
"""
import json, time, urllib.parse
from pathlib import Path
import requests

OUT = Path('/home/z/my-project/job-sourcing/track3-js/validation_results/aggregators_ats')
OUT.mkdir(parents=True, exist_ok=True)

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
      'Accept': 'application/json, text/html, */*',
      'Accept-Language': 'en-US,en;q=0.9'}

def probe(name, url, expect_json=True, **kw):
    print(f"\n[{name}] {url[:90]}")
    start = time.time()
    try:
        r = requests.get(url, headers=UA, timeout=25, **kw)
        dur = round(time.time()-start, 2)
        text = r.text
        size = len(text)
        ctype = r.headers.get('content-type','')[:80]
        # try parse JSON
        parsed = None
        if expect_json and 'json' in ctype.lower():
            try: parsed = r.json()
            except Exception: pass
        # detect job content
        job_keys = []
        if isinstance(parsed, dict):
            job_keys = [k for k in parsed.keys()][:10]
        elif isinstance(parsed, list):
            job_keys = ['list_len=' + str(len(parsed))]
        sample = None
        # try to count jobs
        job_count = None
        if isinstance(parsed, dict):
            for key in ('jobs', 'results', 'items', 'data', 'job_postings', 'postings', 'jobPostings'):
                v = parsed.get(key)
                if isinstance(v, list):
                    job_count = len(v)
                    if v and isinstance(v[0], dict):
                        sample = {k: str(v[0].get(k,''))[:80] for k in list(v[0].keys())[:5]}
                    break
            if 'total' in parsed: job_count = parsed.get('total') if not job_count else job_count
        elif isinstance(parsed, list):
            job_count = len(parsed)
            if parsed and isinstance(parsed[0], dict):
                sample = {k: str(parsed[0].get(k,''))[:80] for k in list(parsed[0].keys())[:5]}
        # RSS / HTML
        if not expect_json or 'xml' in ctype or 'html' in ctype:
            # count item tags
            import re
            job_count = len(re.findall(r'<item', text)) or len(re.findall(r'<entry', text))
            if not job_count:
                # try job-posting schema
                job_count = len(re.findall(r'"@type"\s*:\s*"JobPosting"', text))
        result = {
            'name': name, 'url': url, 'status': r.status_code, 'size': size,
            'content_type': ctype, 'duration_s': dur,
            'job_count': job_count, 'json_keys': job_keys, 'sample': sample,
            'preview': text[:200],
        }
        print(f"  status={r.status_code} size={size:,}b dur={dur}s ctype={ctype}")
        print(f"  job_count={job_count} keys={job_keys}")
        if sample:
            print(f"  sample={sample}")
        return result
    except Exception as e:
        result = {'name': name, 'url': url, 'error': f'{type(e).__name__}: {str(e)[:200]}'}
        print(f"  ERROR: {result['error']}")
        return result

def free_aggregators():
    print('='*70); print('FREE AGGREGATOR APIs'); print('='*70)
    results = []
    # Adzuna - test endpoint without key
    results.append(probe('adzuna_test',
        'https://api.adzuna.com/v1/api/what-works?app_id=test&app_key=test'))
    time.sleep(1.5)
    # Adzuna jobs endpoint attempt (will fail without auth)
    results.append(probe('adzuna_jobs_us',
        'https://api.adzuna.com/v1/api/jobs/us/search/1?app_id=test&app_key=test&what=software%20engineer'))
    time.sleep(1.5)
    # JSearch RapidAPI - confirm endpoint exists
    results.append(probe('jsearch_rapidapi_root',
        'https://jsearch.p.rapidapi.com/search?query=software+engineer+in+us&page=1&num_pages=1'))
    time.sleep(1.5)
    # USAJobs - actually doesn't need auth for basic search; uses Email header for higher limits
    results.append(probe('usajobs_search',
        'https://data.usajobs.gov/api/search?Keyword=software+engineer&ResultsPerPage=5',
        headers={**UA, 'Host': 'data.usajovbs.gov'}))
    time.sleep(1.5)
    # USAJobs without any auth at all
    results.append(probe('usajobs_search_noauth',
        'https://data.usajobs.gov/api/search?Keyword=software+engineer&ResultsPerPage=5'))
    time.sleep(1.5)
    # findwork.dev - will 403 without key
    results.append(probe('findwork_jobs',
        'https://findwork.dev/api/jobs/?search=software'))
    time.sleep(1.5)
    # Jooble - public API about page
    results.append(probe('jooble_about',
        'https://jooble.org/api/about'))
    time.sleep(1.5)
    # Careerjet partner page
    results.append(probe('careerjet_partners',
        'https://www.careerjet.com/partners/'))
    time.sleep(1.5)
    return results

def ats_direct():
    print('\n' + '='*70); print('ATS-DIRECT ENDPOINTS'); print('='*70)
    results = []
    # Greenhouse - OpenAI board
    results.append(probe('greenhouse_openai',
        'https://boards-api.greenhouse.io/v1/boards/openai/jobs?count=true'))
    time.sleep(1.5)
    # Lever - OpenAI postings (real slug)
    results.append(probe('lever_openai',
        'https://api.lever.co/v0/postings/openai?mode=json&limit=50'))
    time.sleep(1.5)
    # Ashby - OpenAI board
    results.append(probe('ashby_openai',
        'https://api.ashbyhq.com/posting-api/job-board/openai'))
    time.sleep(1.5)
    # SmartRecruiters - OpenAI
    results.append(probe('smartrecruiters_openai',
        'https://api.smartrecruiters.com/v1/companies/openai/jobs?limit=10'))
    time.sleep(1.5)
    # Workable - OpenAI (sp = "sp/v1/jobs" endpoint pattern, account query)
    results.append(probe('workable_openai',
        'https://www.workable.com/sp/v1/jobs/?account=openai'))
    time.sleep(1.5)
    # Personio - OpenAI (XML)
    results.append(probe('personio_openai_xml',
        'https://openai.jobs.personio.com/xml'))
    time.sleep(1.5)
    # Personio - jobs.html
    results.append(probe('personio_openai_html',
        'https://openai.jobs.personio.com/'))
    time.sleep(1.5)
    return results

def nokey_aggregators():
    print('\n' + '='*70); print('NO-KEY AGGREGATORS'); print('='*70)
    results = []
    # HN Algolia "who is hiring"
    results.append(probe('hn_whoshiring',
        'https://hn.algolia.com/api/v1/search_by_date?tags=story&query=who%20is%20hiring'))
    time.sleep(1.5)
    # RemoteOK
    results.append(probe('remoteok',
        'https://remoteok.com/api'))
    time.sleep(2.5)
    # WWR RSS
    results.append(probe('wwr_rss',
        'https://weworkremotely.com/remote-jobs.rss'))
    time.sleep(1.5)
    # Remotive
    results.append(probe('remotive',
        'https://remotive.com/api/remote-jobs?limit=10'))
    time.sleep(1.5)
    # arbeitnow
    results.append(probe('arbeitnow',
        'https://www.arbeitnow.com/api/job-board-api'))
    time.sleep(1.5)
    return results

def main():
    free = free_aggregators()
    ats = ats_direct()
    nokey = nokey_aggregators()
    all_results = {'free_aggregators': free, 'ats_direct': ats, 'nokey_aggregators': nokey}
    (OUT / 'results.json').write_text(json.dumps(all_results, indent=2, default=str), encoding='utf-8')
    print('\n✓ All saved')

if __name__ == '__main__':
    main()
