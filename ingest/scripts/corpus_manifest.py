#!/usr/bin/env python3
"""Build data/corpus/manifest.json — the durable-corpus run manifest.

Reads the committed JSONL files + the tracker DB run log and writes a
compact manifest: queries, run timing, per-query row counts, per-source
contribution, degraded sources (names from the run log — the standing
WHY lives in data/corpus/README.md).

Usage (from ingest/):  python3 scripts/corpus_manifest.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

INGEST = Path(__file__).resolve().parent.parent
CORPUS = INGEST / "data" / "corpus"

# The canonical corpus queries (Wave-R R2). Keep in sync with the README.
QUERIES = {
    "software_engineer": "software engineer",
    "platform_engineer": "platform engineer",
    "product_manager": "product manager",
}


def main() -> int:
    db = sqlite3.connect(INGEST / "data" / "tracker.db")
    db.row_factory = sqlite3.Row

    manifest: dict = {
        "contract": "facet-01 (docs/jsonl-export-spec.md)",
        "generated_at": datetime.now(timezone.utc)
                          .isoformat(timespec="seconds"),
        "queries": {},
        "notes": [
            "Descriptions truncated to 4000 chars in the EXPORT only "
            "(--jsonl-desc-max 4000); tracker.db keeps full text.",
            "Runs used --no-score: no LLM calls were burned; llm_score/"
            "sector are absent by design and can be re-run later.",
            "degraded = source names from the run log; the standing "
            "reasons are in data/corpus/README.md.",
        ],
    }

    total_rows = 0
    source_totals: Counter[str] = Counter()
    id_kinds: Counter[str] = Counter()
    for slug, query in QUERIES.items():
        path = CORPUS / f"{slug}.jsonl"
        if not path.exists():
            print(f"missing {path}", file=sys.stderr)
            return 1
        # split on "\n" ONLY: descriptions may contain U+2028/U+2029
        # (legal inside JSON strings, invisible to \n-based JSONL readers
        # like the facet-01 loader — but str.splitlines() would split them)
        rows = [json.loads(x) for x in
                path.read_text(encoding="utf-8").split("\n") if x.strip()]
        sources = Counter(r["source"].split(".")[0].split(" +")[0]
                          for r in rows)
        source_totals.update(sources)
        id_kinds.update(r["job_id"].split("-")[0] for r in rows)
        run = db.execute(
            "SELECT * FROM runs WHERE query = ? AND finished_at IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (query,)).fetchone()
        entry: dict = {
            "query": query,
            "location": "Remote",
            "num_per_source": 50,
            "rows": len(rows),
            "unique_job_ids": len({r["job_id"] for r in rows}),
            "bytes": path.stat().st_size,
            "sources": dict(sources.most_common()),
        }
        if run is not None:
            entry["run"] = {
                "started_at": run["started_at"],
                "finished_at": run["finished_at"],
                "sources_ok": json.loads(run["sources_ok"] or "[]"),
                "sources_degraded": json.loads(
                    run["sources_degraded"] or "[]"),
                "jobs_found": run["jobs_found"],
                "jobs_new": run["jobs_new"],
                "jobs_merged": run["jobs_merged"],
            }
        manifest["queries"][slug] = entry
        total_rows += len(rows)

    manifest["total_rows"] = total_rows
    manifest["source_totals"] = dict(source_totals.most_common())
    manifest["job_id_kinds"] = dict(id_kinds.most_common())

    out = CORPUS / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"wrote {out} — {total_rows} rows across {len(QUERIES)} queries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
