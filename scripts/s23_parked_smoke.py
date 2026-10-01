#!/usr/bin/env python3
"""S23 smoke test: live list_board() of every parked-wire adapter
(re_D.md contracts). Validates row counts + detail payloads for one row
each. Usage: python3 scripts/s23_parked_smoke.py [--detail]"""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ingest"))

from jobsearch.config import Config  # noqa: E402
from jobsearch.sources import site_boards as sb  # noqa: E402

BOARDS = [
    ("custom:greenland", 8, "Greenland"),
    ("custom:jereh", 6, "Jereh"),
    ("custom:autelenergy", 3, "AutelEnergy"),
    ("custom:aden", 2, "Aden"),
    ("custom:mandarinoriental", 30, "MandarinOriental", True),
    ("custom:wuxiapptec", 35, "WuXiAppTec"),
    ("custom:blacksesame", 6, "BlackSesame"),
    ("custom:ecovacsus", 1, "EcovacsUS"),
    ("custom:accutar", 10, "Accutar"),
    ("custom:hitgen", 1, "HitGen"),
    ("custom:insilico", 1, "Insilico"),
]


def main() -> int:
    detail = "--detail" in sys.argv
    cfg = Config()
    bad = 0
    for entry in BOARDS:
        spec, expect, name = entry[0], entry[1], entry[2]
        soft = len(entry) > 3 and entry[3]
        try:
            rows, meta = sb.list_board(spec, country="United States",
                                       progress_label="smoke")
        except Exception as exc:
            if soft and "refusing to report 0 rows" in str(exc):
                print(f"[SOFT-FAIL] {name}: throttled (202 anti-bot) — "
                      "fail-loud guard working; chain retries later")
                continue
            print(f"[SMOKE-FAIL] {name} ({spec}): {exc!r}")
            bad += 1
            continue
        ok = "OK" if len(rows) == expect else f"DRIFT(exp {expect})"
        if len(rows) != expect:
            bad += 1
        print(f"[{name}] {len(rows)} rows {ok} — "
              f"{next(iter(rows.values()))['title'][:60]!r}")
        if detail and rows:
            rid = next(iter(rows))
            try:
                p = sb.detail_payload(spec, rows[rid]["externalPath"],
                                      cfg, country="United States")
                info = (p or {}).get("jobPostingInfo") or {}
                desc_len = len(info.get("jobDescription") or "")
                print(f"    detail[{rid[:40]}]: title="
                      f"{info.get('title','')[:40]!r} loc="
                      f"{info.get('location','')[:30]!r} "
                      f"desc={desc_len}ch tt={info.get('timeType','')!r} "
                      f"sd={info.get('startDate','')!r}")
                if p is None:
                    bad += 1
            except Exception as exc:
                print(f"    [DETAIL-FAIL] {name} {rid}: {exc!r}")
                bad += 1
    print(f"\nsmoke result: {'ALL GREEN' if bad == 0 else f'{bad} FAILURES'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
