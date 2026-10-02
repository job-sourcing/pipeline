#!/usr/bin/env python3
"""S25 cn_hardware census — feishu portal probe (reuses S17 API contract)."""
import json, sys, urllib.request
sys.path.insert(0, "/home/z/research/ingest")
from jobsearch.sources.site_boards import _FEISHU_CITY_COUNTRY  # noqa: E402

def post(url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        **(headers or {}),
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())

def probe_portal(portal):
    base = f"https://{portal}.jobs.feishu.cn"
    try:
        tok = post(f"{base}/api/v1/csrf/token", {})
        token = tok.get("data", {}).get("token")
        r = post(f"{base}/api/v1/search/job/posts?portal_type=6&portal_entrance=1",
                 {"offset": 0, "limit": 200}, {"X-Csrf-Token": token})
        if r.get("code") != 0:
            return {"error": f"code={r.get('code')}"}
        d = r.get("data", {})
        rows = d.get("job_post_list") or []
        total = d.get("count")
        us, unmapped, cities_all = [], set(), {}
        for row in rows:
            cities = [(c or {}).get("en_name") or (c or {}).get("name")
                      for c in (row.get("city_list") or [])]
            for c in cities:
                if not c:
                    continue
                cities_all[c] = cities_all.get(c, 0) + 1
                if _FEISHU_CITY_COUNTRY.get(c) == "United States":
                    us.append({"title": (row.get("title") or "")[:60], "cities": cities})
                    break
                if c not in _FEISHU_CITY_COUNTRY:
                    unmapped.add(c)
        return {"total": total, "fetched": len(rows), "us_count": len(us),
                "us_rows": us[:6], "top_cities": sorted(cities_all.items(), key=lambda x:-x[1])[:12],
                "unmapped": sorted(unmapped)[:10]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

if __name__ == "__main__":
    for portal in sys.argv[1:]:
        res = probe_portal(portal)
        print(f"== {portal} => {json.dumps(res, ensure_ascii=False)[:900]}", flush=True)
