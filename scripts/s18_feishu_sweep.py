#!/usr/bin/env python3
"""s18_feishu_sweep.py — generalized Feishu-Hire portal census sweep.

Reusable census instrument (the S17 lesson: the census instrument of
record is the ADAPTER — never a one-off probe). For any list of candidate
portal slugs, this script:

  1. probes https://{slug}.jobs.feishu.cn/ for portal existence (404/NXDOMAIN
     => no portal),
  2. runs the REAL FeishuHireAdapter.list_board(country="US") — the same
     envelope-code guard, body-offset pagination, ANY-city rule and curated
     city map the production pipeline uses,
  3. reports per-slug: board total, US rows, US titles, and the failure
     class (no-portal / error / ok).

Usage:
  python3 scripts/s18_feishu_sweep.py slug1 slug2 ...        # ad-hoc
  python3 scripts/s18_feishu_sweep.py --list-file slugs.txt  # file of slugs
  python3 scripts/s18_feishu_sweep.py --batch s18            # built-in batch

Output: JSON to stdout (rows + summary) — census evidence payload class.

Deliberately network-dependent (census mode): no caching, one page per
slug (limit=200), never writes pipeline state.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ingest"))

from jobsearch.config import Config           # noqa: E402
from jobsearch.sources.site_boards import (   # noqa: E402
    FeishuHireAdapter,
)


# The S18 go-broader candidate list (CN AI landscape, slug guesses from
# brand/pinyin/known ids — KEEP SORTED, append new guesses at the end so
# future rounds can diff coverage).
S18_CANDIDATES = [
    # model startups (LM/application)
    "deepseek", "stepfun", "jieyue", "moonshot", "kimi", "hunyuan",
    "qwen", "tongyi", "baichuan-intl", "linyiwanwu", "lingyiwanwu",
    "monica", "genspark", "skywork", "kunlun-tech", "manus", "manusai",
    "monicaai", "hiart", "z.ai",
    # AI infra / compute
    "siliconflow", "ppio", "baai", "moonbit", "sensecore", "lepton",
    "bitdeer", "volcanoengine", "functionpuls",
    # robotics / embodied
    "ubtech", "rokid", "limx", "engineai", "robotera", "pudu", "galbot",
    "astribot", "unitree", "keonz", "dyna",
    # AV / mobility
    "qcraft", "deeproute", "autox", "plusai", "plus-ai", "imot",
    "li-auto", "liauto", "zhito", "haomo",
    # vision / CV / voice
    "cloudwalk", "megvii", "yitu", "iflytek", "intsig", "aispeech",
    "iot-excellent", "rekognition",
    # data / platforms / apps
    "thinkingdata", "shence", "growingio", "kuaishou", "kwaixsh",
    "meituan", "dianping", "xhs", "rednote",
    # S17-verified live portals (control group — expect same numbers)
    "zhipu-ai", "01ai", "modelbest", "agirobot", "sensetime", "shengshu",
    "nio", "dcar", "momenta", "infinigence",
]


def probe_portal(slug: str) -> int:
    """HEAD-ish existence probe of the portal host. Returns HTTP code."""
    url = f"https://{slug}.jobs.feishu.cn/"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html",
    })
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        name = type(e).__name__
        if name == "HTTPError":          # pragma: no cover
            return 599
        return 0                          # DNS/timeout/conn class


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--list-file")
    ap.add_argument("--batch", action="store_true",
                    help="use the built-in S18 candidate list")
    args = ap.parse_args()

    slugs: list[str] = list(args.slugs)
    if args.list_file:
        slugs += [s.strip() for s in
                  Path(args.list_file).read_text().splitlines()
                  if s.strip() and not s.startswith("#")]
    if args.batch or not slugs:
        slugs = S18_CANDIDATES

    cfg = Config()
    rows = []
    for slug in slugs:
        code = probe_portal(slug)
        row = {"slug": slug, "portal_http": code}
        if code == 200:
            try:
                adapter = FeishuHireAdapter(slug, cfg)
                us_rows, meta = adapter.list_board(
                    country="US", time_type=None,
                    progress_label=f"sweep:{slug}",
                )
                # unfiltered total (no country filter) for the census table
                all_rows, _ = adapter.list_board(
                    country=None, time_type=None,
                    progress_label=f"sweep:{slug}:all",
                )
                row.update({
                    "status": "ok",
                    "total": len(all_rows),
                    "us_rows": len(us_rows),
                    "complete": meta.get("complete"),
                    "us_titles": [r.get("title", "") for r in
                                  list(us_rows.values())[:20]],
                })
            except Exception as e:
                row.update({"status": "error", "error":
                            f"{type(e).__name__}: {e}"})
        else:
            row["status"] = "no-portal" if code in (404, 0) else f"http-{code}"
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        time.sleep(0.6)

    live = [r for r in rows if r["status"] == "ok"]
    us_hits = [r for r in live if r.get("us_rows")]
    print("\n=== SUMMARY ===", file=sys.stderr)
    print(f"probed {len(rows)} slugs; live portals {len(live)}; "
          f"portals-with-US {len(us_hits)}", file=sys.stderr)
    for r in us_hits:
        print(f"  US HIT: {r['slug']} — {r['us_rows']} US / "
              f"{r['total']} total", file=sys.stderr)
    print(json.dumps({"rows": rows,
                      "summary": {"probed": len(rows),
                                  "live": len(live),
                                  "us_hits": [r["slug"] for r in us_hits]}},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
