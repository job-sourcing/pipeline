#!/usr/bin/env python3
"""Supabase edge proxy probes.
- 3 hostile sites x 3 modes (default + raw + region-pin) = 9 probes
- IP rotation: 10 unpinned + 5 regions x 5 calls = 35 calls
Total: 44 calls. Sleep 1.5s between each to be polite.
"""
import os, json, time, urllib.parse
from pathlib import Path
import requests

OUT = Path('/home/z/my-project/job-sourcing/track3-js/validation_results/supabase_proxy')
OUT.mkdir(parents=True, exist_ok=True)

# Env-only credentials (S8-A scrub): os.environ first, then ingest/.env
# (the committed secret store) — never hardcoded.
def _env_file_value(name: str) -> str:
    """Read `name` from ingest/.env (KEY=value lines) if present."""
    env_path = Path(__file__).resolve().parents[2] / "ingest" / ".env"
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

CHALLENGE = ['just a moment', 'cloudflare', 'humans only', 'verify you are human',
             'performing security verification', 'request blocked', 'forbidden cf-waf',
             'security check', 'additional verification', 'captcha',
             'sign in to view more jobs', 'authwall', 'unusual traffic']

JOB_MARKERS = {
    'indeed': ['data-jk=', 'jobTitle', 'jobsearch-ResultsList'],
    'linkedin': ['base-search-card__title', 'urn:li:jobPosting', 'job-search-card'],
    'glassdoor': ['data-test="job-link"', 'react-job-listing', 'JobList'],
}

SITES = [
    ('indeed', 'https://www.indeed.com/jobs?q=software+engineer&l=Remote'),
    ('linkedin', 'https://www.linkedin.com/jobs/search/?keywords=software%20engineer'),
    ('glassdoor', 'https://www.glassdoor.com/Job/jobs.htm'),
]

REGIONS = ['us-east-1', 'eu-west-1', 'ap-southeast-1', 'ap-northeast-1', 'sa-east-1']

def probe_target(url, name, mode='default', region=None):
    """Make a single proxy call. mode is 'default'|'raw'. region pins via x-region header."""
    full_url = f"{PROXY_URL}?url={urllib.parse.quote(url, safe='')}"
    if mode == 'raw':
        full_url += '&mode=raw'
    headers = dict(HEADERS)
    if region:
        headers['x-region'] = region
    start = time.time()
    try:
        r = requests.get(full_url, headers=headers, timeout=45)
        dur = round(time.time() - start, 2)
        text = r.text
        markers = JOB_MARKERS.get(name, [])
        found = [m for m in markers if m in text]
        tl = text.lower()
        challenges = [c for c in CHALLENGE if c in tl]
        return {
            'name': name, 'mode': mode, 'region': region,
            'status': r.status_code, 'size': len(text), 'duration_s': dur,
            'x_proxy_status': r.headers.get('x-proxy-status'),
            'x_proxy_mode': r.headers.get('x-proxy-mode'),
            'x_sb_edge_region': r.headers.get('x-sb-edge-region'),
            'x_ratelimit_remaining': r.headers.get('x-ratelimit-remaining'),
            'job_markers_found': found, 'has_job_content': bool(found),
            'challenges': challenges, 'blocked': bool(challenges),
            'preview': text[:200],
        }
    except Exception as e:
        return {'name': name, 'mode': mode, 'region': region,
                'error': f'{type(e).__name__}: {str(e)[:200]}',
                'duration_s': round(time.time()-start, 2)}

def run_hostile_sites():
    print('='*70); print('Hostile site probes via Supabase proxy'); print('='*70)
    results = []
    for name, url in SITES:
        # default mode
        r = probe_target(url, name, mode='default')
        print(f"\n[{name}/default] status={r.get('status')} size={r.get('size')} dur={r.get('duration_s')}s edge={r.get('x_sb_edge_region')} blocked={r.get('blocked')} job_content={r.get('has_job_content')}")
        results.append(r); time.sleep(1.5)
        # raw mode
        r = probe_target(url, name, mode='raw')
        print(f"[{name}/raw]     status={r.get('status')} size={r.get('size')} dur={r.get('duration_s')}s edge={r.get('x_sb_edge_region')} blocked={r.get('blocked')} job_content={r.get('has_job_content')}")
        results.append(r); time.sleep(1.5)
        # region-pin us-east-1
        r = probe_target(url, name, mode='raw', region='us-east-1')
        print(f"[{name}/raw/us-east-1] status={r.get('status')} size={r.get('size')} dur={r.get('duration_s')}s edge={r.get('x_sb_edge_region')}")
        results.append(r); time.sleep(1.5)
    return results

def run_ip_rotation():
    print('\n' + '='*70); print('IP rotation: 10 unpinned calls to ipinfo.io/json'); print('='*70)
    ipinfo_url = 'https://ipinfo.io/json'
    unpinned = []
    for i in range(10):
        r = probe_target(ipinfo_url, 'ipinfo', mode='default')
        if 'error' in r:
            print(f"  call {i+1}: ERROR {r['error'][:100]}")
            unpinned.append(r); time.sleep(1.5); continue
        # extract IP from preview
        try:
            data = json.loads(r['preview'] + (r.get('preview','')*0)) if False else None
        except Exception:
            data = None
        # need full text; re-do: read r.text via probe... we only kept preview. Refetch:
        # Actually probe_target stored preview only. Let's grab IP from preview if JSON-able
        # preview is only first 200 chars; not enough. We need to make raw request again.
        # Simpler: directly call requests here.
        unpinned.append(r); time.sleep(1.2)
    # Redo properly: store the full text in a new function
    return unpinned

def run_ip_rotation_v2():
    """Direct fetch of ipinfo.io/json via proxy, capture full IP."""
    print('\n' + '='*70); print('IP rotation: 10 unpinned calls to ipinfo.io/json'); print('='*70)
    ipinfo_url = 'https://ipinfo.io/json'
    unpinned = []
    for i in range(10):
        full_url = f"{PROXY_URL}?url={urllib.parse.quote(ipinfo_url, safe='')}"
        start = time.time()
        try:
            r = requests.get(full_url, headers=HEADERS, timeout=20)
            dur = round(time.time()-start, 2)
            try:
                data = r.json()
            except Exception:
                data = {}
            entry = {
                'call': i+1, 'status': r.status_code, 'duration_s': dur,
                'x_sb_edge_region': r.headers.get('x-sb-edge-region'),
                'ip': data.get('ip'), 'city': data.get('city'),
                'country': data.get('country'), 'org': data.get('org'),
            }
            print(f"  call {i+1}: ip={entry['ip']} city={entry['city']} country={entry['country']} edge={entry['x_sb_edge_region']}")
            unpinned.append(entry)
        except Exception as e:
            unpinned.append({'call': i+1, 'error': str(e)[:100]})
            print(f"  call {i+1}: ERROR {e}")
        time.sleep(1.2)

    print('\n' + '='*70); print('IP rotation: 5 regions x 5 calls'); print('='*70)
    pinned = {}
    for region in REGIONS:
        pinned[region] = []
        for i in range(5):
            full_url = f"{PROXY_URL}?url={urllib.parse.quote(ipinfo_url, safe='')}"
            start = time.time()
            try:
                r = requests.get(full_url, headers={**HEADERS, 'x-region': region}, timeout=20)
                dur = round(time.time()-start, 2)
                try:
                    data = r.json()
                except Exception:
                    data = {}
                entry = {
                    'region': region, 'call': i+1, 'status': r.status_code, 'duration_s': dur,
                    'x_sb_edge_region': r.headers.get('x-sb-edge-region'),
                    'ip': data.get('ip'), 'city': data.get('city'),
                    'country': data.get('country'), 'org': data.get('org'),
                }
                print(f"  [{region}] call {i+1}: ip={entry['ip']} city={entry['city']} country={entry['country']}")
                pinned[region].append(entry)
            except Exception as e:
                pinned[region].append({'region': region, 'call': i+1, 'error': str(e)[:100]})
                print(f"  [{region}] call {i+1}: ERROR {e}")
            time.sleep(1.0)
        time.sleep(1.0)
    return unpinned, pinned

def summarize_rotation(unpinned, pinned):
    print('\n' + '='*70); print('IP rotation summary'); print('='*70)
    uniq_unpinned = set([r.get('ip') for r in unpinned if r.get('ip')])
    print(f"Unpinned: {len(uniq_unpinned)} unique IPs from {len(unpinned)} calls")
    print(f"  IPs: {sorted(uniq_unpinned)}")
    edges_unpinned = set([r.get('x_sb_edge_region') for r in unpinned if r.get('x_sb_edge_region')])
    print(f"  Edges seen: {edges_unpinned}")
    print('\nPinned per region unique IP counts:')
    for region, calls in pinned.items():
        ips = set([c.get('ip') for c in calls if c.get('ip')])
        cities = set([c.get('city') for c in calls if c.get('city')])
        countries = set([c.get('country') for c in calls if c.get('country')])
        print(f"  {region}: {len(ips)} unique IPs from {len(calls)} calls | cities={cities} countries={countries}")
        print(f"    IPs: {sorted(ips)}")

def main():
    hostile_results = run_hostile_sites()
    (OUT / 'hostile_results.json').write_text(json.dumps(hostile_results, indent=2, default=str), encoding='utf-8')
    unpinned, pinned = run_ip_rotation_v2()
    (OUT / 'ip_rotation.json').write_text(json.dumps({'unpinned': unpinned, 'pinned': pinned}, indent=2, default=str), encoding='utf-8')
    summarize_rotation(unpinned, pinned)

if __name__ == '__main__':
    main()
