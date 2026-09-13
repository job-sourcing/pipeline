#!/usr/bin/env python3
"""Export the drained slug→ATS directory as a committed snapshot.

The directory DB (ingest/data/ats_directory.db) is gitignored data that
dies with the container; this script exports the LIVE rows (the resolver's
working set) to a JSON snapshot that IS committed — so a fresh sandbox can
seed the resolver instantly and the drain progress survives recycles.

Run any time (partial drains export what's live so far); re-run after the
daemon finishes for the complete set.

  python3 scripts/export_ats_directory.py            # ingest/data/ats_directory_snapshot.json
  python3 scripts/export_ats_directory.py --out FILE --pretty

Snapshot shape: {"exported_at": ISO, "source_db": path, "counts": {...},
                 "boards": [{platform, slug, company, job_count, board_url}]}
sorted by (platform, slug) for stable diffs.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ingest"))

from jobsearch.ats_resolve import board_url  # noqa: E402

DB_PATH = HERE.parent / "ingest" / "data" / "ats_directory.db"
DEFAULT_OUT = HERE.parent / "ingest" / "data" / "ats_directory_snapshot.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"[export] no directory DB at {db} — nothing to export")
        return 1

    conn = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ats, slug, company, job_count FROM directory "
        "WHERE status = 'live' ORDER BY ats, slug").fetchall()
    counts = dict(conn.execute(
        "SELECT status, COUNT(*) FROM directory GROUP BY status").fetchall())
    conn.close()

    boards = [{
        "platform": r["ats"],
        "slug": r["slug"],
        "company": r["company"] or "",
        "job_count": r["job_count"],
        "board_url": board_url(r["ats"], r["slug"]) or "",
    } for r in rows]

    snapshot = {
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_db": str(db),
        "counts": {**counts, "live_exported": len(boards),
                   "total_jobs_on_live_boards": sum(
                       b["job_count"] or 0 for b in boards)},
        "boards": boards,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        snapshot, ensure_ascii=False,
        indent=2 if args.pretty else None) + "\n", encoding="utf-8")
    print(f"[export] {len(boards)} live boards "
          f"({snapshot['counts']['total_jobs_on_live_boards']} jobs) → {out}")
    by_platform: dict[str, int] = {}
    for b in boards:
        by_platform[b["platform"]] = by_platform.get(b["platform"], 0) + 1
    for p, n in sorted(by_platform.items()):
        print(f"  {p}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
