#!/usr/bin/env python3
"""Concurrent ATS board prober: ashby / greenhouse / lever / workable / smartrecruiters / rippling."""
import json, sys, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0 (research census probe)"}

def fetch(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return None, str(e)[:60]

def probe(provider, slug):
    try:
        if provider == "ashby":
            st, body = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
            if st == 200 and body:
                d = json.loads(body)
                jobs = d.get("jobs", [])
                locs = [x.get("location") for x in jobs[:5]]
                return f"ashby:{slug} LIVE jobs={len(jobs)} locs={locs}"
        elif provider == "greenhouse":
            st, body = fetch(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
            if st == 200 and body:
                d = json.loads(body)
                jobs = d.get("jobs", [])
                return f"greenhouse:{slug} LIVE jobs={len(jobs)} first={jobs[0]['title'] if jobs else None}"
        elif provider == "lever":
            st, body = fetch(f"https://api.lever.co/v0/postings/{slug}?mode=json")
            if st == 200 and body:
                d = json.loads(body)
                return f"lever:{slug} LIVE jobs={len(d)} first={d[0]['text'] if d else None}"
        elif provider == "workable":
            st, body = fetch(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
            if st == 200 and body:
                d = json.loads(body)
                name = d.get("name")
                jobs = d.get("jobs", [])
                if name:
                    return f"workable:{slug} LIVE account={name} jobs={len(jobs)}"
        elif provider == "smartrecruiters":
            st, body = fetch(f"https://api.smartrecruiters.com/v1/companies/{slug}/jobs?limit=5")
            if st == 200 and body:
                d = json.loads(body)
                total = d.get("total")
                if total:
                    return f"smartrecruiters:{slug} LIVE total={total}"
        elif provider == "rippling":
            st, body = fetch(f"https://ats.rippling.com/{slug}/jobs")
            if st == 200 and body and "No jobs" not in body[:2000]:
                import re
                m = re.search(r'"openPositionCount":(\d+)', body)
                n = m.group(1) if m else "?"
                return f"rippling:{slug} HTTP200 (openPositionCount={n}, len={len(body)})"
    except Exception as e:
        return None
    return None

def run(pairs):
    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(lambda p: probe(*p), pairs))
    for r in results:
        if r:
            print(r)

if __name__ == "__main__":
    # pairs from argv: "provider:slug" tokens
    pairs = []
    for tok in sys.argv[1:]:
        provider, slug = tok.split(":", 1)
        pairs.append((provider, slug))
    run(pairs)
