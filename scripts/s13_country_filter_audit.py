#!/usr/bin/env python3
"""S13 audit: is the client-side US filter dropping real US postings?

Re-fetches each S12 board (netflix/tencent/jd) WITHOUT the country filter,
then classifies every row: kept / dropped-by-filter, cross-tabbed against
primaryLocation.country (the server's own country field) and the raw
locationsText. A dropped row whose primaryLocation.country says US (or whose
location string is US-shaped but tokenless) = false negative.

Usage: python3 s13_country_filter_audit.py [netflix] [tencent] [jd]
"""
import sys
import json
from collections import Counter

sys.path.insert(0, "/home/z/my-project/job-sourcing-research/ingest")

from jobsearch.sources.workday import list_board, _row_in_country

BOARDS = {
    "netflix": "netflix|wd108|Netflix",
    "tencent": "tencent|wd1|Tencent_Careers",
    "jd": "jd|wd103|Careers_at_JD",
}


def main() -> None:
    labels = sys.argv[1:] or list(BOARDS)
    for label in labels:
        spec = BOARDS[label]
        print(f"\n===== {label} ({spec}) =====")
        rows_by_id, meta = list_board(spec, sleep_s=0.15)
        rows = list(rows_by_id.values())
        print(f"total rows fetched: {len(rows)} "
              f"(complete={meta.get('complete')} total={meta.get('total')})")

        kept, dropped = [], []
        for r in rows:
            (kept if _row_in_country(r, "united states") else dropped).append(r)

        print(f"kept (US predicate): {len(kept)}   dropped: {len(dropped)}")

        # Server-side country truth for every row (primaryLocation.country).
        srv = Counter(
            ((r.get("primaryLocation") or {}).get("country") or "<none>")
            for r in rows
        )
        print("server primaryLocation.country census (all rows):")
        for c, n in srv.most_common():
            print(f"  {c!r}: {n}")

        # The money question: rows the predicate dropped that the server
        # says are US. Also rows the predicate KEPT that the server says
        # are non-US (false positives).
        fn = [r for r in dropped
              if (r.get("primaryLocation") or {}).get("country", "").lower()
              .startswith(("united states", "us", "usa"))]
        fp = [r for r in kept
              if (r.get("primaryLocation") or {}).get("country")
              and not (r.get("primaryLocation") or {}).get("country", "")
              .lower().startswith(("united states", "us", "usa"))]
        print(f"FALSE NEGATIVES (dropped but server says US): {len(fn)}")
        for r in fn[:15]:
            print(f"  DROP {r.get('reqId')}: loc={r.get('locationsText')!r} "
                  f"country={(r.get('primaryLocation') or {}).get('country')!r}")
        print(f"FALSE POSITIVES (kept but server says non-US): {len(fp)}")
        for r in fp[:15]:
            print(f"  KEEP {r.get('reqId')}: loc={r.get('locationsText')!r} "
                  f"country={(r.get('primaryLocation') or {}).get('country')!r}")

        # Where do dropped-US-suspects live? census of dropped locations that
        # look US-shaped (US city/state without a US token: state abbrev etc.)
        us_state_like = [
            r for r in dropped
            if (r.get("primaryLocation") or {}).get("country") in
            (None, "", "<none>", "US", "USA", "United States")
        ]
        locs = Counter((r.get("locationsText") or "<none>") for r in us_state_like)
        if locs:
            print("dropped rows with US/absent server country, by locationsText:")
            for loc, n in locs.most_common(25):
                print(f"  {loc!r}: {n}")


if __name__ == "__main__":
    main()
