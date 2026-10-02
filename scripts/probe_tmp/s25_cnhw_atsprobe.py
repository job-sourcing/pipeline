#!/usr/bin/env python3
import json, subprocess, sys

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'

def fetch(url):
    try:
        r = subprocess.run(['curl','-s','--max-time','12','-A',UA,url],
                           capture_output=True, text=True, timeout=15)
        return r.stdout[:300000]
    except Exception:
        return ''

def probe(ats, slug):
    urls = {
        'ashby': f'https://api.ashbyhq.com/posting-api/job-board/{slug}',
        'greenhouse': f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs',
        'lever': f'https://api.lever.co/v0/postings/{slug}?mode=json',
        'workable': f'https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true',
        'smartrecruiters': f'https://api.smartrecruiters.com/v1/companies/{slug}/jobs?limit=5',
    }
    body = fetch(urls[ats])
    if not body.strip():
        return 'EMPTY/NO-DATA'
    try:
        d = json.loads(body)
    except Exception:
        return 'NOT-JSON'
    try:
        if ats == 'ashby':
            jobs = d.get('jobs')
            if jobs is None: return 'MISS'
            locs = [str((j.get('location') or ''))[:30] for j in jobs[:8]]
            return f'{len(jobs)} jobs | {locs}'
        if ats == 'greenhouse':
            jobs = d.get('jobs')
            if jobs is None: return 'MISS'
            locs = [str(((j.get('location') or {}).get('name')) or '')[:30] for j in jobs[:8]]
            return f'{len(jobs)} jobs | {locs}'
        if ats == 'lever':
            if isinstance(d, list):
                locs = [str(((j.get('categories') or {}).get('location')) or '')[:30] for j in d[:8]]
                return f'{len(d)} jobs | {locs}'
            return 'MISS'
        if ats == 'workable':
            if d.get('jobs') is None: return 'MISS'
            jobs = d.get('jobs') or []
            locs = [str(((j.get('location') or {}).get('city')) or '')[:25] for j in jobs[:8]]
            return f"{d.get('name')} | {len(jobs)} jobs | {locs}"
        if ats == 'smartrecruiters':
            total = d.get('total')
            if total is None: return 'MISS'
            locs = [str(((j.get('location') or {}).get('city')) or '')[:25] for j in (d.get('content') or [])[:8]]
            return f'{total} total | {locs}'
    except Exception as e:
        return f'PARSE-ERR {e}'
    return '??'

if __name__ == '__main__':
    for spec in sys.argv[1:]:
        ats, slug = spec.split(':', 1)
        print(f'{spec}\t=> {probe(ats, slug)}', flush=True)
