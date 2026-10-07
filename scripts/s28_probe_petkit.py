#!/usr/bin/env python3
"""S28: live-probe petkit's feishuhire board (run #98 LIST FAILED diagnosis).

Also cross-checks laifen + rokid (completed with 0 US rows) to confirm
the boards themselves are alive — distinguishes petkit-dead vs
feishu-egress-flaky.
"""
import sys, json, traceback
sys.path.insert(0, "/home/z/pipeline-mirror/ingest")

# minimal Config stub if needed
try:
    from jobsearch.config import Config
    cfg = Config()
except Exception:
    cfg = None

from jobsearch.sources.site_boards import FeishuHireAdapter

for org in ("jijiapets", "f1l5e2ythy", "rokid-jungle"):
    print(f"=== {org} ===")
    try:
        a = FeishuHireAdapter(org, cfg)
        # hit the raw search endpoint once — the same path board_dump list uses
        try:
            tok = a._token_refresh()
            print("  csrf token OK:", bool(tok))
        except Exception as e:
            print(f"  csrf FAIL: {type(e).__name__}: {e}")
            continue
        try:
            body = {"keyword": "", "limit": 10, "offset": 0}
            env = a._post_json(a._SEARCH % {} if "%" in a._SEARCH else a._SEARCH, body)
            code = env.get("code")
            data = env.get("data") or {}
            posts = data.get("job_post_list") or []
            total = data.get("total") or env.get("msg")
            print(f"  search OK: code={code} page_rows={len(posts)} total={total}")
            for p in posts[:3]:
                title = p.get("title") or (p.get("job_post") or {}).get("title")
                loc = p.get("city_list") or p.get("location")
                print("   -", str(title)[:60], "|", str(loc)[:80])
        except Exception as e:
            print(f"  search FAIL: {type(e).__name__}: {e}")
    except Exception:
        traceback.print_exc()
