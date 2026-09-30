#!/usr/bin/env python3
"""S22 smoke test: live list_board() of every new wave-3 adapter.
Validates row counts vs the RE-contract expectations + detail payloads
for one row each. Usage: python3 scripts/s22_adapter_smoke.py [--detail]"""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ingest"))

from jobsearch.sources import site_boards as sb  # noqa: E402

BOARDS = [
    ("ats:breezy:bitdeer", 46, "Bitdeer"),
    ("ats:jobvite:ovt", 61, "OmniVision"),
    ("ats:oraclehcm:eidg.fa.us6.oraclecloud.com|CX_1", 24, "Mattson"),
    ("ats:oraclehcm:fa-exhj-saasfaprod1.fa.ocs.oraclecloud.com|CX_1", 6,
     "Virtuos"),
    ("ats:bamboohr:hisenseusacorporation", 19, "Hisense"),
    ("ats:j2w:careers.joysonsafety.com/search", 7, "Joyson"),
    ("ats:j2w:careers.cofcointernational.com/search-jobs", 3, "COFCO"),
    ("ats:ultipro:MEY1000MEYER|7a970c3d-b076-4042-8735-673b5e5508ac", 8,
     "Meyer"),
    ("ats:talentadore:amersports.careers.talentadore.com", 12, "Amer"),
    ("ats:workstream:fcc54c54", 60, "Haidilao"),
    ("ats:ttiproxy:US", 645, "TTI"),
    ("ats:sanity:cd9iwvgl/production|Jobs|en-us", 7, "NIU"),
    ("ats:wpjobboard:www.ascentage.com", 4, "Ascentage"),
    ("ats:wuxibio:www.wuxibiologics.com", 61, "WuXiBio"),
    ("ats:antintl:M7892", 7, "AntIntl"),
]

WANT_DETAIL = "--detail" in sys.argv


def main() -> int:
    fails = []
    for spec, expected, name in BOARDS:
        try:
            rows, meta = sb.list_board(spec, country="United States",
                                       progress_label="smoke")
            n = len(rows)
            # boards can drift a few rows day-to-day
            drift_ok = abs(n - expected) <= max(3, expected // 10)
            status = "OK " if drift_ok else "DRIFT"
            print(f"[{status}] {name:10} {n:4} rows (expected ~{expected},"
                  f" meta total={meta.get('total')},"
                  f" pages={meta.get('pages')})")
            if not drift_ok:
                fails.append((name, n, expected))
            if WANT_DETAIL and rows:
                rid, row = next(iter(rows.items()))
                det = sb.detail_payload(spec, row.get("externalPath"),
                                        country="United States")
                if det:
                    info = det.get("jobPostingInfo", {})
                    desc_len = len(str(info.get("jobDescription") or ""))
                    print(f"        detail {str(rid)[:24]}: title="
                          f"{str(info.get('title'))[:40]!r} desc="
                          f"{desc_len}ch loc={str(info.get('location'))[:30]!r}"
                          f" tt={info.get('timeType')!r}")
                    if desc_len < 50:
                        fails.append((name + "-detail-empty", desc_len, 50))
                else:
                    print(f"        detail {rid}: NONE")
                    fails.append((name + "-detail-none", 0, 1))
        except Exception as e:
            print(f"[ERR] {name:10} {str(e)[:120]}")
            fails.append((name, "exception", str(e)[:60]))
    print()
    if fails:
        print("FAILURES:", fails)
        return 1
    print("ALL SMOKE TESTS GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
