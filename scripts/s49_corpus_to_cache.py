#!/usr/bin/env python3
"""s49_corpus_to_cache — the corpus→cache join (S49-C #1).

The complaint #3 root cause (b)+(d-board): the pm projection
(project_l0) hardcodes desc "" for every board row while the FULL JDs
sit in the corpus desc store (ingest/data/ui/desc/{companyId}.json →
{rows: {full-row-id: jd_text}}, 99.7% coverage); the board transport
in jd_refetch's SELECT_RULES is "skip" (the v2 detail transport was
never built). The measured gap: pm 184 + gen 22 empty-desc board rows
(gen's 22 are the honest store-less floor — project_l0_gen already
joins the store directly).

The fix routes through the EXISTING consult mechanism — zero family
surgery, zero fetching: every board corpus row that has a desc in the
store becomes a jd_cache.jsonl entry keyed (source="board", url=<the
corpus row url — verified 100% identical to the bundle board rows'
urls, pm 196/196 + gen 6,956/6,956>). The sweep's consult hook
(role_sweep.py ~L2626) then upgrades the empty board rows before the
gates: a cached ok desc with desc_len > 0 is strictly-longer than ""
and _jd_overlap_ok's title-token fallback passes (empty rows ARE
upgradeable — measured 182/184 pass; the 2 misses are the CJK-title
xiaohongshu row + a boilerplate-only netflix capture, honest floor).

Entry format = the jd_refetch store contract EXACTLY (source, url,
id, status="ok", desc, desc_len, transport, fetched_at, http) —
transport "corpus_join" is the honest provenance marker (no http
fetch happened; http:200 records the successful extraction). Union-
merge semantics identical to the refetch lane: never delete, newest
fetched_at wins, a live-fetched ok never gets clobbered by an
older-stamped join.

Usage:
  python3 scripts/s49_corpus_to_cache.py            # join + write
  python3 scripts/s49_corpus_to_cache.py --dry-run  # stats only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jd_refetch as jr  # noqa: E402  # the cache IO + store gate contract

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "ingest/data/ui/jobs.json"
DESC_DIR = REPO / "ingest/data/ui/desc"
BUNDLES = {f: REPO / f"ingest/data/ui/roles-{f}.json"
           for f in ("pm", "gen", "dmm")}
MAX_LEN = jr.MAX_FETCH_LEN          # 12,000 — the refetch store gate


def join_corpus_to_cache(corpus_rows: list[dict], desc_dir: Path,
                          now: str, existing: dict[str, dict],
                          max_len: int = MAX_LEN
                          ) -> tuple[dict[str, dict], dict[str, int]]:
    """Pure join: corpus rows + desc store → jd_cache entries keyed
    (board, url). Returns (entries-to-merge, stats). Union-merge rule:
    an existing entry stays when it is ok AND its fetched_at is ≥ the
    join stamp (a live fetch never loses to the join); any failure
    verdict (403/q:/…) is overridden — the corpus JD is strictly more
    useful than a wall."""
    store: dict[str, dict | None] = {}

    def _drows(cid: str):
        if cid not in store:
            p = desc_dir / f"{cid}.json"
            try:
                store[cid] = (json.load(open(p)).get("rows")
                              if p.exists() else None)
            except (json.JSONDecodeError, OSError):
                store[cid] = None
        return store[cid]

    entries: dict[str, dict] = {}
    stats = {"corpus_rows": len(corpus_rows), "store_joined": 0,
             "no_store_desc": 0, "no_url": 0, "clobbered_verdicts": 0,
             "kept_existing_ok": 0, "entries_added": 0}
    for j in corpus_rows:
        url = (j.get("url") or "").strip()
        if not url:
            stats["no_url"] += 1
            continue
        cid = j.get("companyId") or ""
        rid = j.get("id") or ""
        text = ""
        drows = _drows(cid)
        if isinstance(drows, dict) and rid:
            text = str(drows.get(rid) or "")[:max_len]
        if not text.strip():
            stats["no_store_desc"] += 1
            continue
        stats["store_joined"] += 1
        key = f"board|{url}"
        prev = existing.get(key)
        if prev is not None and prev.get("status") == "ok" \
                and prev.get("fetched_at", "") >= now:
            stats["kept_existing_ok"] += 1
            continue
        if prev is not None:
            stats["clobbered_verdicts"] += 1
        entries[key] = {
            "source": "board", "url": url, "id": rid,
            "status": "ok", "desc": text, "desc_len": len(text),
            "transport": "corpus_join", "fetched_at": now, "http": 200,
        }
        stats["entries_added"] += 1
    return entries, stats


def verify_bundle_url_join(corpus_urls: set[str]) -> dict[str, int]:
    """The url-join verification (the id join was verified 8,134/8,161;
    the url field needed its own pass): every bundle board row must
    carry a url that exists in the corpus — else the cache key would
    never consult."""
    out: dict[str, int] = {}
    for fam, p in BUNDLES.items():
        if not p.exists():
            continue
        board = [j for j in (json.load(open(p)).get("jobs") or [])
                 if j.get("source") == "board"]
        hit = sum(1 for j in board if (j.get("url") or "").strip()
                  in corpus_urls)
        out[fam] = hit
        print(f"[verify] {fam}: board rows={len(board)} "
              f"url-in-corpus={hit} ({100 * hit / max(1, len(board)):.1f}%)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--desc-dir", default=str(DESC_DIR))
    args = ap.parse_args()

    corpus = json.load(open(args.corpus)).get("jobs") or []
    corpus_urls = {(j.get("url") or "").strip() for j in corpus
                   if (j.get("url") or "").strip()}
    verify_bundle_url_join(corpus_urls)

    existing = jr._load_cache_map()
    now = jr._now()
    entries, stats = join_corpus_to_cache(
        corpus, Path(args.desc_dir), now, existing)
    print(f"[join] {stats}")
    if args.dry_run:
        print("[join] dry-run — cache untouched")
        return 0
    merged = {**existing, **entries}
    jr._write_cache(merged)
    print(f"[join] cache: {len(existing)} → {len(merged)} entries "
          f"(+{len(entries)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
