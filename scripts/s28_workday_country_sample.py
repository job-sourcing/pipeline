#!/usr/bin/env python3
"""S28: sample unfiltered workday postings → country distribution.

(Estimate: first 100 postings per CN-company tenant.)
"""
import json
import collections
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Content-Type": "application/json", "Accept": "application/json"}

BOARDS = [
    ("jd", 103, "Careers_at_JD"),
    ("tencent", 1, "Tencent_Careers"),
    ("popmart", 102, "popmart"),
    ("haier", 3, "GE_Appliances"),
    ("chagee", 102, "External"),
]

def loc_country(posting):
    parts = []
    for loc in posting.get("locations") or []:
        d = loc.get("descriptor") or ""
        parts.append(d)
    return " / ".join(p for p in parts if p)[:60]

for tenant, wd, site in BOARDS:
    cc = collections.Counter()
    try:
        for off in (0, 20, 40, 60, 80):
            url = (f"https://{tenant}.wd{wd}.myworkdayjobs.com/wday/cxs/"
                   f"{tenant}/{site}/jobs")
            body = json.dumps({"appliedFacets": {}, "limit": 20,
                               "offset": off, "searchText": ""}).encode()
            req = urllib.request.Request(url, data=body, headers=UA,
                                         method="POST")
            with urllib.request.urlopen(req, timeout=25) as r:
                d = json.loads(r.read().decode())
            for p in d.get("jobPostings") or []:
                cc[loc_country(p)] += 1
        print(f"== {tenant} (first {sum(cc.values())} unfiltered)")
        for k, v in cc.most_common(10):
            print(f"   {v:3} {k}")
    except Exception as e:
        print(f"== {tenant} FAIL {type(e).__name__}: {e}")
    print()
