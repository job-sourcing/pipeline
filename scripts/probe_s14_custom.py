#!/usr/bin/env python3
"""S14 probe harness — custom own-platform job boards (ByteDance/Alibaba/Trip/Huawei).

curl_cffi chrome-impersonated transport (Akamai rejects plain curl from HK egress).
Usage: python3 scripts/probe_s14_custom.py {bytedance|alibaba|trip|huawei|all}
"""
import json
import sys

from curl_cffi import requests as cffi_requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"


def imp():
    return cffi_requests.Session(impersonate="chrome")


def probe_bytedance():
    s = imp()
    url = "https://joinbytedance.com/api/v1/search/job/posts"
    body = {
        "keyword": "",
        "limit": 10,
        "offset": 0,
        "job_category_id_list": [],
        "tag_id_list": [],
        "location_code_list": [],
        "subject_id_list": [],
        "recruitment_id": 0,
        "portal_type": 0,
        "portal_entrance": 1,
    }
    r = s.post(url, json=body, headers={"Referer": "https://joinbytedance.com/search"}, timeout=20)
    print("bytedance POST status:", r.status_code)
    if r.status_code == 200:
        d = r.json()
        print("code:", d.get("code"), "total:", d.get("data", {}).get("total"))
        posts = d.get("data", {}).get("job_post_list") or []
        if posts:
            p = posts[0]
            print("first post keys:", sorted(p.keys()))
            print("sample:", json.dumps({k: p.get(k) for k in ("title", "city_info", "recruitment_type", "job_category", "code", "id", "title_suffix")}, ensure_ascii=False)[:500])
    return r.status_code


def probe_alibaba():
    s = imp()
    # Global talent portal SPA — find its API from the page first
    r = s.get("https://talent.alibaba.com/en/home?lang=en", timeout=20)
    print("alibaba SPA status:", r.status_code, "len:", len(r.text))
    # common API guesses for the talent portal
    for path in [
        "/api/position/search",
        "/position/search",
        "/off-campus/position/list",
    ]:
        u = "https://talent.alibaba.com" + path
        r2 = s.get(u, params={"pageIndex": 1, "pageSize": 10, "keyword": ""}, timeout=15)
        print(f"  GET {path} -> {r2.status_code} {r2.headers.get('content-type','')[:40]}")
        if r2.status_code == 200 and "json" in r2.headers.get("content-type", ""):
            print("  body head:", r2.text[:300])
            return
    # fallback: scan the SPA html for api hints
    import re
    hits = re.findall(r'["\'](/[a-zA-Z0-9_/\-]*api[a-zA-Z0-9_/\-]*)["\']', r.text)[:20]
    print("api-ish strings in SPA:", sorted(set(hits))[:20])
    scripts = re.findall(r'src="([^"]+\.js[^"]*)"', r.text)[:10]
    print("scripts:", scripts[:10])


def probe_trip():
    s = imp()
    r = s.get("https://careers.trip.com/", timeout=20)
    print("trip SPA status:", r.status_code, "len:", len(r.text))
    import re
    hits = re.findall(r'["\'](/api/[a-zA-Z0-9_/\-\.]+)["\']', r.text)[:20]
    print("api strings in SPA:", sorted(set(hits))[:20])
    for path in ["/api/job/list", "/api/position/list"]:
        r2 = s.get("https://careers.trip.com" + path, timeout=15)
        print(f"  GET {path} -> {r2.status_code} {r2.headers.get('content-type','')[:40]} :: {r2.text[:200]!r}")


def probe_huawei():
    s = imp()
    r = s.get("https://career.huawei.com/cn", timeout=20)
    print("huawei SPA status:", r.status_code, "len:", len(r.text))
    r2 = s.get("https://career.huawei.com/reccampportal/services/portal/portaljob/getJobListForEn?currentPage=1&pageSize=10", timeout=15)
    print("huawei EN joblist ->", r2.status_code, r2.text[:200])


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    fns = {"bytedance": probe_bytedance, "alibaba": probe_alibaba, "trip": probe_trip, "huawei": probe_huawei}
    for name, fn in fns.items():
        if which in (name, "all"):
            print(f"===== {name} =====")
            try:
                fn()
            except Exception as e:
                print(f"  ERROR: {type(e).__name__}: {e}")
