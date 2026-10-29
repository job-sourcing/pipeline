#!/usr/bin/env python3
"""s49_jd_cache_patch.py — [S49-C] the post-hoc jd_cache consult (the
bundle-side completion of the JD-fill pass).

WHY this exists: the zero-fetch seed-envelope re-emit PROVED the consult
works end-to-end (board-empty 184→2, 671 cache-filled) but a zero-fetch
pass is NOT a lane run — the seed's 60-day staleness bound drops rows
(7,220→6,645 measured) that only a FRESH fetch re-adds. Shipping that
would trade 575 real rows for the fill. This applier runs the EXACT
sweep-time consult transformation (role_sweep's own loader, key, and
overlap gate — strictly-longer + _jd_overlap_ok, title-token fallback
for empty rows) DIRECTLY on the committed bundle rows, in place:
idempotent by construction (the next lane fire's in-process consult sees
desc already >= cached and skips), and it never touches row membership
(no staleness logic, no drops, no merges — desc/descLen/
descriptionSource only).

Usage:
  python3 scripts/s49_jd_cache_patch.py            # all three families
  python3 scripts/s49_jd_cache_patch.py pm gen     # selected families
  python3 scripts/s49_jd_cache_patch.py --dry-run   # report only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "ingest"))

import role_sweep as _rs  # noqa: E402  (the SAME consult machinery)

UI = REPO / "ingest/data/ui"


def patch_family(fam: str, dry_run: bool) -> dict:
    path = UI / f"roles-{fam}.json"
    d = json.loads(path.read_text())
    rows = d.get("jobs") or []
    cache = _rs._load_jd_cache()
    n_filled = n_skipped_longer = n_gated = 0
    for r in rows:
        key = _rs._jd_cache_key(r.get("source", ""), r.get("url", ""))
        info = cache.get(key)
        if not info or info.get("status") != "ok":
            continue
        cur = r.get("desc") or ""
        new = (info.get("desc") or "")[:4000]   # the sweep's consult cap —
        # [S49-C fix] compare POST-CAP: a 12k-char cached entry whose
        # first 4,000 chars equal the row's existing 4,000 (the gen l0
        # corpus rows — cache and bundle share the source text) is a
        # NO-OP, and stamping it jd_cache would flip provenance on
        # ~5k rows with zero content gain (measured: 4,681 flips).
        if not new.strip() or len(new) <= len(cur):
            n_skipped_longer += 1
            continue
        if not _rs._jd_overlap_ok(cur, new, r.get("title", "")):
            n_gated += 1
            continue
        if not dry_run:
            r["desc"] = new
            r["descLen"] = len(new)
            r["descriptionSource"] = "jd_cache"
        n_filled += 1
    empty = sum(1 for r in rows if not (r.get("desc") or "").strip())
    if not dry_run and n_filled:
        d["rows"] = len(rows)
        path.write_text(json.dumps(d, ensure_ascii=False))
    return {"family": fam, "rows": len(rows), "filled": n_filled,
            "skipped_not_longer": n_skipped_longer, "overlap_gated": n_gated,
            "empty_after": empty,
            "empty_pct": round(100 * empty / max(1, len(rows)), 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("families", nargs="*",
                    help="pm / dmm / gen (default: all three)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    fams = a.families or ["pm", "gen", "dmm"]
    for fam in fams:
        if fam not in ("pm", "gen", "dmm"):
            print(f"unknown family {fam!r}")
            return 2
    out = [patch_family(f, a.dry_run) for f in fams]
    for r in out:
        tag = "DRY-RUN " if a.dry_run else ""
        print(f"[{tag}{r['family']}] rows={r['rows']} filled={r['filled']} "
              f"(not-longer {r['skipped_not_longer']}, gated "
              f"{r['overlap_gated']}) → empty {r['empty_after']} "
              f"({r['empty_pct']}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
