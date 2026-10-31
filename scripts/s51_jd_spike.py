#!/usr/bin/env python3
"""s51_jd_spike.py — the S51 JD-completion spike (the owner directive:
catch-up runs complete NOW, never scheduled days ahead; spike as if
there's no tomorrow — refills are coming).

What it does (per --family pm|dmm|gen, one PROCESS per shard):
  1. Reuses jd_refetch's selection (fc_relay superset transport) —
     pending rows with desc below the per-source bar.
  2. EXCLUDES the careerjet class (measured 2026-10-30: fc basic =
     junk_signature CF page 1.4k chars, zenrows = http500 upstream —
     the class needs a stealth pass, its own budget decision; burning
     1 basic credit per row for junk is the no-op spend class).
  3. Per-CALL key rotation across the firecrawl pool (round-robin, not
     failure-only): N shards × M keys distribute load evenly — each
     account sees ~1 call per (keys/shards × row-period). All calls
     ride the vendor RELAY (never one direct IP).
  4. Appends every result IMMEDIATELY to its shard file (append-only —
     a sandbox death loses nothing); the supervisor merges shards into
     jd_cache.jsonl and commits (git-is-disk checkpoints).

Usage (the launcher spawns one process per family+shard):
  python3 scripts/s51_jd_spike.py --family pm --shard 0 --shards 6
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))

import jd_refetch as jr  # noqa: E402
from jobsearch import vendor_relay  # noqa: E402
from role_sweep import _firecrawl_keys  # noqa: E402

SHARD_DIR = jr.REPO / "ingest/data/roles/jd_spike"
EXCLUDE_SOURCES = {"careerjet"}     # the junk class — see docstring


def _fc_fetch_one_rotating(url: str, keys: list[str],
                           state: dict) -> tuple[str, int]:
    """jd_refetch._fetch_one_fc with PER-CALL round-robin key rotation
    (load spread across the pool, not first-key-preference). Same
    (text, status) contract; every call rides the relay."""
    m = jr.re.match(r"^(https?://himalayas\.app/jobs/)(https?://.+)$",
                    url)
    if m:
        url = m.group(2)
    if not keys:
        return "error:no-fc-key", 0
    key = keys[state["i"] % len(keys)]
    state["i"] += 1
    d, st = vendor_relay.fc_api(
        "/v2/scrape",
        {"url": url, "formats": ["markdown"], "onlyMainContent": True,
         "waitFor": 2500}, key, timeout=120)
    if d is None and (st.startswith(("relay:", "error:", "http401",
                                     "http402", "http403", "http429"))
                      or st.startswith("http5")):
        # key-level (401/402/403/429), transport, or a transient target
        # 5xx → ONE retry on the next key; a definitive target answer
        # (404/410) is returned as-is (no double credit)
        key2 = keys[(state["i"]) % len(keys)]
        state["i"] += 1
        d, st = vendor_relay.fc_api(
            "/v2/scrape",
            {"url": url, "formats": ["markdown"],
             "onlyMainContent": True, "waitFor": 2500}, key2, timeout=120)
    if d is None:
        # normalize to jd_refetch's failure contract: "http-error:NNN"
        # for target statuses (an ANSWER), "error:<st>" otherwise —
        # a raw "http500" would fall through _quality_reject as
        # too_short and wrongly RESOLVE the row for this transport
        if st.startswith("http") and st[4:].isdigit():
            return f"http-error:{st[4:]}", int(st[4:])
        return f"error:{st[:40]}", 0
    md = str((d.get("data") or {}).get("markdown") or "")
    return jr.re.sub(r"[ \t]+", " ", md).strip()[: jr.MAX_FETCH_LEN], 200


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", choices=["pm", "dmm", "gen"],
                    required=True)
    ap.add_argument("--shard", type=int, required=True)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--max-rows", type=int, default=10 ** 9)
    ap.add_argument("--budget-seconds", type=float, default=10 ** 9)
    ap.add_argument("--todo-file", default="",
                    help="precomputed todo list (one JSON row per line — "
                         "the launcher's --prepare mode; avoids the "
                         "68MB-bundle parse per shard process)")
    args = ap.parse_args()

    jr._set_family(args.family)
    keys = _firecrawl_keys()
    if not keys:
        print("no firecrawl keys", file=sys.stderr)
        return 2

    if args.todo_file and Path(args.todo_file).exists():
        mine = [json.loads(l) for l in
                Path(args.todo_file).read_text().splitlines() if l.strip()]
        todo_total = -1
    else:
        jobs = jr._load_bundle()
        targets = jr._select_targets(jobs, "fc_relay")
        cache = jr._load_cache_map()

        def _resolved(key: str) -> bool:    # jd_refetch's semantics
            ent = cache.get(key, {})
            st = ent.get("status", "")
            if st == "ok":
                return True
            same = ent.get("transport", "direct") == "fc_relay"
            if st.startswith("q:"):
                return same or ent.get("transport", "") == ""
            if st.startswith("http-error:4"):
                return same
            return False

        todo = [t for t in targets
                if t["source"] not in EXCLUDE_SOURCES
                and not _resolved(f"{t['source']}|{t['url']}")]
        todo_total = len(todo)
        # shard by index stride
        mine = todo[args.shard::args.shards]
    print(f"[spike {args.family}/{args.shard}] todo-total={todo_total} "
          f"mine={len(mine)} keys={len(keys)}", flush=True)

    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SHARD_DIR / f"{args.family}_{args.shard}.jsonl"
    log_path = SHARD_DIR / f"{args.family}_{args.shard}.log"
    state: dict = {"i": args.shard}         # decorrelate shard starts
    stats: dict[str, int] = {}
    fetched = 0
    t0 = time.time()
    with open(out_path, "a") as out:
        for r in mine:
            if (fetched >= args.max_rows
                    or time.time() - t0 > args.budget_seconds):
                break
            if (SHARD_DIR / "STOP").exists():
                print("[spike] STOP flag — exiting at row boundary",
                      flush=True)
                break
            url, src = r["url"], r["source"]
            text, status = _fc_fetch_one_rotating(url, keys, state)
            rej = jr._quality_reject(text)
            if rej and not text.startswith(("http-error:", "error:",
                                            "relay:", "http")):
                entry = {"source": src, "url": url,
                         "id": r.get("id") or "",
                         "status": f"q:{rej}", "desc": "",
                         "desc_len": 0, "transport": "fc_relay",
                         "fetched_at": jr._now(), "http": status}
                stats[f"{src}:q:{rej}"] = \
                    stats.get(f"{src}:q:{rej}", 0) + 1
            elif not text or text.startswith(("http-error:", "error:",
                                              "relay:", "http4",
                                              "http5")):
                # fleet/transport errors stay UNRESOLVED (retry later);
                # target 4xx are answers (resolve for this transport)
                st_str = text.split(":")[0] if ":" in text else text[:20]
                is_answer = text.startswith("http-error:4")
                entry = {"source": src, "url": url,
                         "id": r.get("id") or "",
                         "status": text[:60] if is_answer
                         else f"error:{st_str[:30]}",
                         "desc": "", "desc_len": 0,
                         "transport": "fc_relay" if is_answer else "",
                         "fetched_at": jr._now(), "http": status}
                stats[f"{src}:{st_str[:20]}"] = \
                    stats.get(f"{src}:{st_str[:20]}", 0) + 1
            else:
                entry = {"source": src, "url": url,
                         "id": r.get("id") or "",
                         "status": "ok", "desc": text,
                         "desc_len": len(text),
                         "transport": "fc_relay",
                         "fetched_at": jr._now(), "http": status}
                stats[f"{src}:ok"] = stats.get(f"{src}:ok", 0) + 1
            out.write(json.dumps(entry, ensure_ascii=False) + "\n")
            out.flush()
            fetched += 1
            if fetched % 25 == 0:
                msg = (f"[spike {args.family}/{args.shard}] "
                       f"{fetched}/{len(mine)} "
                       f"({time.time() - t0:.0f}s) " +
                       " ".join(f"{k}={v}" for k, v in
                                sorted(stats.items())[:6]))
                print(msg, flush=True)
                with open(log_path, "a") as lg:
                    lg.write(msg + "\n")
            time.sleep(1.2)
    done = (fetched >= len(mine))
    print(f"[spike {args.family}/{args.shard}] DONE fetched={fetched} "
          f"of {len(mine)} in {time.time() - t0:.0f}s", flush=True)
    with open(log_path, "a") as lg:
        lg.write(f"FINAL fetched={fetched}/{len(mine)} done={done}\n")
        for k, v in sorted(stats.items()):
            lg.write(f"  {v:5} {k}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
