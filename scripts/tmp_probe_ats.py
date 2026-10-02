#!/usr/bin/env python3
"""Quick multi-ATS probe for census agents. Usage: tmp_probe_ats.py <ats> <slug> [...]"""
import json, subprocess, sys, re

US_RE = re.compile(r'\b(US|USA|United States|U\.S\.)\b|,\s*(A[LKZR]|C[AOT]|DE|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|N[EVHJMYC]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])\b|Remote.*(US|US-Americas)|City, ST')

def curl(url, ua="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"):
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "15", "-A", ua, url], capture_output=True, text=True, timeout=20)
        return r.stdout
    except Exception:
        return ""

def probe(ats, slug):
    if ats == "ashby":
        body = curl(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        try:
            d = json.loads(body); jobs = d.get("jobs", [])
        except Exception:
            print(f"ashby {slug}: MISS"); return
        locs = [j.get("location") or "" for j in jobs]
        us = [l for l in locs if US_RE.search(str(l))]
        print(f"ashby {slug}: 200 jobs={len(jobs)} us~{len(us)}")
        from collections import Counter
        print("   top:", Counter(locs).most_common(8))
    elif ats == "greenhouse":
        body = curl(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        try:
            d = json.loads(body)
            if d.get("status") in (404, 400) or "error" in d:
                print(f"greenhouse {slug}: MISS {d.get('status', d.get('error'))}"); return
            jobs = d.get("jobs", [])
        except Exception:
            print(f"greenhouse {slug}: MISS"); return
        locs = [j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else str(j.get("location", "")) for j in jobs]
        us = [l for l in locs if US_RE.search(str(l))]
        print(f"greenhouse {slug}: 200 jobs={len(jobs)} us~{len(us)}")
        print("   top:", sorted(set(locs))[:10])
    elif ats == "lever":
        body = curl(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        try:
            jobs = json.loads(body)
            if isinstance(jobs, dict) or "error" in (jobs if isinstance(jobs, list) else []):
                print(f"lever {slug}: MISS"); return
            if isinstance(jobs, dict): jobs = []
        except Exception:
            print(f"lever {slug}: MISS"); return
        locs = [j.get("categories", {}).get("location", "") or j.get("workplaceType", "") or "" for j in jobs]
        us = [l for l in locs if US_RE.search(str(l))]
        print(f"lever {slug}: 200 jobs={len(jobs)} us~{len(us)}")
        print("   top:", sorted(set(locs))[:10])
    elif ats == "workable":
        body = curl(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
        try:
            d = json.loads(body); jobs = d.get("jobs", [])
        except Exception:
            print(f"workable {slug}: MISS"); return
        locs = [j.get("location", {}).get("city", "") + ", " + j.get("location", {}).get("country", "") for j in jobs]
        us = [l for l in locs if US_RE.search(str(l))]
        print(f"workable {slug}: 200 name={d.get('name')} jobs={len(jobs)} us~{len(us)}")
        print("   top:", sorted(set(locs))[:10])
    elif ats == "smartrecruiters":
        body = curl(f"https://api.smartrecruiters.com/v1/companies/{slug}/jobs?limit=5")
        try:
            d = json.loads(body)
        except Exception:
            print(f"smartrecruiters {slug}: MISS"); return
        total = d.get("total")
        if total is None:
            print(f"smartrecruiters {slug}: MISS (no total)"); return
        locs = [c.get("location") and (c["location"].get("city", "") + ", " + c["location"].get("country", "")) or "" for c in d.get("content", [])]
        print(f"smartrecruiters {slug}: 200 total={total}")
        print("   top:", locs)

if __name__ == "__main__":
    ats = sys.argv[1]
    for slug in sys.argv[2:]:
        probe(ats, slug)
