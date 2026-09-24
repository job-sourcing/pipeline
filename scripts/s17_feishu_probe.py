#!/usr/bin/env python3
"""S17 census probe: Feishu-Hire jobs boards (jobs.feishu.cn).

Flow discovered live 2026-09-25 via browser network capture:
  1. POST https://{portal}.jobs.feishu.cn/api/v1/csrf/token  -> {"data":{"token":...}}
  2. POST https://{portal}.jobs.feishu.cn/api/v1/search/job/posts?...&portal_type=6
     with header X-Csrf-Token: {token}, body {}
  -> throne-platform job_post_list (same family as ByteDance jobs API).

Usage: python3 s17_feishu_probe.py portal1 portal2 ...
"""
import json
import sys
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")

SEARCH_URL = ("https://{p}.jobs.feishu.cn/api/v1/search/job/posts"
              "?keyword=&limit=10&offset=0&job_category_id_list="
              "&tag_id_list=&location_code_list=&subject_id_list="
              "&recruitment_id_list=&portal_type=6"
              "&job_function_id_list=&storefront_id_list=&portal_entrance=1")

# US city tokens for the census's US-job detection
US_TOKENS = (
    "san francisco", "new york", "seattle", "los angeles", "mountain view",
    "palo alto", "boston", "austin", "santa clara", "san jose", "redmond",
    "remote", "usa", "united states", "menlo park", "cupertino", "berkeley",
    "chicago", "san diego", "washington", "cambridge",
)


def probe(portal: str) -> dict:
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def post(url, headers=None, body=None):
        data = json.dumps(body if body is not None else {}).encode()
        h = {"User-Agent": UA, "Content-Type": "application/json",
             "Referer": f"https://{portal}.jobs.feishu.cn/",
             "Origin": f"https://{portal}.jobs.feishu.cn"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=data, headers=h, method="POST")
        with opener.open(req, timeout=25) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    try:
        td = post(f"https://{portal}.jobs.feishu.cn/api/v1/csrf/token")
        token = ((td.get("data") or {}).get("token")) or ""
        if not token:
            return {"portal": portal, "err": f"no token: {str(td)[:120]}"}
        d = post(SEARCH_URL.format(p=portal),
                 headers={"X-Csrf-Token": token}, body={})
        jobs = ((d.get("data") or {}).get("job_post_list")) or []
        locs = {}
        us_jobs = []
        for j in jobs:
            cities = [str(c.get("en_name") or c.get("name") or "")
                      for c in (j.get("city_list") or [])]
            key = "/".join(cities) or "?"
            locs[key] = locs.get(key, 0) + 1
            if any(any(t in c.lower() for t in US_TOKENS) for c in cities):
                us_jobs.append({
                    "title": j.get("title"),
                    "cities": cities,
                    "tt": ((j.get("recruit_type") or {}).get("en_name")),
                    "code": j.get("code"),
                })
        return {"portal": portal, "n": len(jobs), "locations": locs,
                "us_jobs": us_jobs}
    except Exception as e:
        return {"portal": portal, "err": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    portals = sys.argv[1:] or [
        "vrfi1sk8a0", "zhipu-ai", "01ai", "dcar", "cq6qe6bvfr6",
        "nio", "sensetime", "agirobot",
    ]
    for p in portals:
        r = probe(p)
        print(json.dumps(r, ensure_ascii=False))
