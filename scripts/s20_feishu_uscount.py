#!/usr/bin/env python3
"""S20-D feishu US-count probe — for each candidate feishuhire portal,
count jobs with a US-mapped city (the wire-worthiness signal).

Uses the exact S17-reverse-engineered API contract (csrf token -> search
POST with BODY offset/limit; envelope code==0 guard).
"""
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "ingest"))
from jobsearch.sources.site_boards import _FEISHU_CITY_COUNTRY  # noqa: E402

PORTALS = [
    "camel", "chagee", "cheetah-mobile", "haidilao", "hailiang",
    "hisense", "jac-group", "momenta", "poizon", "robosense", "xtalpi",
]
OUT = REPO / "ingest/data/ats_seed/s20_census/feishu_us_counts.json"


def post(url: str, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        **(headers or {}),
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def main():
    out = {}
    for portal in PORTALS:
        base = f"https://{portal}.jobs.feishu.cn"
        try:
            tok = post(f"{base}/api/v1/csrf/token", {})
            token = tok.get("data", {}).get("token")
            rows, total, offset = [], None, 0
            while True:
                r = post(
                    f"{base}/api/v1/search/job/posts?portal_type=6&portal_entrance=1",
                    {"offset": offset, "limit": 200},
                    {"X-Csrf-Token": token},
                )
                if r.get("code") != 0:
                    out[portal] = {"error": f"code={r.get('code')}"}
                    break
                d = r.get("data", {})
                total = d.get("count")
                page = d.get("job_post_list") or []
                rows.extend(page)
                if not page or (total is not None and len(rows) >= total) or offset > 5000:
                    break
                offset += 200
            if "error" not in (out.get(portal) or {}):
                us, unmapped = [], set()
                for row in rows:
                    cities = [(c or {}).get("en_name") or (c or {}).get("name")
                              for c in (row.get("city_list") or [])]
                    for c in cities:
                        if not c:
                            continue
                        if _FEISHU_CITY_COUNTRY.get(c) == "United States":
                            us.append({"title": (row.get("title") or "")[:60],
                                       "cities": cities})
                            break
                        if c not in _FEISHU_CITY_COUNTRY:
                            unmapped.add(c)
                out[portal] = {
                    "total": total, "fetched": len(rows),
                    "us_count": len(us), "us_rows": us[:8],
                    "unmapped_cities": sorted(unmapped)[:12],
                }
                print(f"{portal:15} total={total or 0:4} us={len(us):3} "
                      f"unmapped={len(unmapped)}")
        except Exception as e:
            out[portal] = {"error": f"{type(e).__name__}: {e}"}
            print(f"{portal:15} ERROR {type(e).__name__}: {str(e)[:80]}")
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print("\n->", OUT)


if __name__ == "__main__":
    main()
