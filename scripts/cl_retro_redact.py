#!/usr/bin/env python3
"""one-off [W2B-3 + W2B-8] — the retroactive craigslist title hygiene
pass (2026-10-26).

The 10-26 T0 census (9,945 rows) was minted BEFORE the ingest-time
fixes landed in role_sweep.fetch_craigslist: 22 rows carry
poster-published personal phone numbers (the PII class riding the
wholesale industry mirror) and 945 titles carry raw HTML entities
(&amp;/&#39;/&nbsp;) that degrade the title-token signal. This pass
applies the SAME two transforms to the artifact in place:

  1. html.unescape(title)                       [W2B-8]
  2. _CL_PHONE.sub("[redacted]", title)         [W2B-3]

Titles only — every other field (and the ledger/gates/cities_run)
preserved verbatim; the write is atomic (tmp+replace, the _save
contract) and idempotent (a [redacted] token carries no digits).

Run once; kept for the audit trail:
  python3 scripts/cl_retro_redact.py [--dry-run]
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import role_sweep as rs  # noqa: E402

OUT = (Path(__file__).resolve().parent.parent
       / "ingest/data/industry/craigslist_home_services.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    d = json.loads(OUT.read_text())
    n_redacted = n_unescaped = 0
    for r in d.get("rows", []):
        t = r.get("title") or ""
        t2 = html.unescape(t)
        if t2 != t:
            n_unescaped += 1
        t3 = rs._CL_PHONE.sub("[redacted]", t2)
        if t3 != t2:
            n_redacted += 1
        r["title"] = t3
    print(f"[cl_retro_redact] rows={len(d.get('rows', []))} "
          f"entities_decoded={n_unescaped} phones_redacted={n_redacted}")
    if args.dry_run:
        print("[cl_retro_redact] dry-run — artifact untouched")
        return 0
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(OUT)                 # atomic, the _save contract
    print(f"[cl_retro_redact] wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
