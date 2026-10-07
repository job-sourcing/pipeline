#!/usr/bin/env python3
"""S28: live country-facet probe of the CN-company workday tenants.

Question: do jd/tencent/gea/beone/popmart/chagee tenants carry non-US
countries in locationHierarchy1 (i.e., would extending geo_scope to
workday actually yield foreign rows), or are they US-only tenants?
"""
import json
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Content-Type": "application/json",
      "Accept": "application/json"}

BOARDS = {
    "jd": ("jd", 103, "Careers_at_JD"),
    "tencent": ("tencent", 1, "Tencent_Careers"),
    "gea": ("haier", 3, "GE_Appliances"),
    "beone": ("beigene", 5, "BeiGene"),
    "popmart": ("popmart", 102, "popmart"),
    "chagee": ("chagee", 102, "External"),
}

def probe(tenant, wd, site):
    url = f"https://{tenant}.wd{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    body = json.dumps({"appliedFacets": {}, "limit": 20, "offset": 0,
                       "searchText": ""}).encode()
    req = urllib.request.Request(url, data=body, headers=UA, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return f"FAIL {type(e).__name__}: {e}"
    out = {"total": d.get("total")}
    facets = d.get("facetCounts") or []
    for f in facets:
        param = f.get("facetParameter")
        if param == "locationHierarchy1":
            out["countries"] = [
                (v.get("descriptor"), v.get("count"))
                for v in (f.get("values") or [])][:20]
    return out

for name, (tenant, wd, site) in BOARDS.items():
    r = probe(tenant, wd, site)
    print(f"== {name} ({tenant}|wd{wd}|{site})")
    print("  ", r if isinstance(r, str) else json.dumps(
        r, ensure_ascii=False)[:400])
