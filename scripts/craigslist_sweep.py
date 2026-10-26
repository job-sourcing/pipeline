#!/usr/bin/env python3
"""craigslist_sweep.py — the T0 home-services census driver (S47 §3).

The craigslist lane is BOX-SIDE ONLY (IP reputation — the S44/R1
doctrine; never a GHA lane, no yml exists on purpose). Foreground
chunks (the sandbox reaping rule): --city-window N/M runs ~10 cities
(10 × 12 queries × 3s ≈ 6 min) — commit between windows.

Artifact: ingest/data/industry/craigslist_home_services.json —
ACCUMULATES across windows (cities_run maps city → per-query counts +
verdicts; a re-run of a city REPLACES its rows on an OK verdict only —
[W2B-1] a blocked/captcha re-run keeps the prior window's rows, the
city pauses). Rows are dateless metadata (title/loc/url/rid/counts
— the derived-data doctrine; no post bodies, no per-post fetches).
The .report.md twin carries the per-city × query table + the honest
verdict ledger (any 403/captcha city is recorded, never retried
blindly — the city pauses until the next window).

Usage:
  python3 scripts/craigslist_sweep.py --city-window 1/3   # cities 1-10
  python3 scripts/craigslist_sweep.py --city sfbay        # one city
  python3 scripts/craigslist_sweep.py --status             # read-only
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import role_sweep as rs  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "ingest/data/industry/craigslist_home_services.json"
REPORT = REPO / "ingest/data/industry/craigslist_home_services.report.md"


def _load() -> dict:
    if OUT.exists():
        try:
            return json.load(open(OUT))
        except Exception:
            # [W2B-5] corrupt-artifact guard: back the file up, then
            # FAIL LOUD. The old catch-all silently reset to the
            # empty skeleton — run_window would then _save over it,
            # silently losing every other city's rows (the artifact
            # is the only store; the rows are dateless and not
            # re-derivable without a re-fetch).
            bak = OUT.with_name(OUT.name + ".corrupt")
            bak.write_bytes(OUT.read_bytes())
            raise
    return {"ran_at": "", "industry": "home_services_craigslist",
            "cities_run": {}, "rows": [], "gates": {}}


def _save(d: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_bytes(json.dumps(d, indent=1).encode())
    tmp.replace(OUT)


def _write_report(d: dict) -> None:
    lines = ["# craigslist home-services census (T0)",
             "",
             f"- ran_at (last window): {d.get('ran_at') or 'never'}",
             f"- cities run: {len(d.get('cities_run', {}))}"
             f" / {len(rs.CRAIGSLIST_CITIES)}",
             f"- rows: {len(d.get('rows', []))} (dateless; title/loc/"
             "url/counts only — the derived-data doctrine)",
             "",
             "| city | queries | rows | verdict |",
             "|---|---|---|---|"]
    for city, info in sorted(d.get("cities_run", {}).items()):
        lines.append(f"| {city} | {info.get('queries', 0)} "
                     f"| {info.get('rows', 0)} "
                     f"| {(info.get('verdict') or '')[:80]} |")
    lines += ["", "## per-query totals", "",
             "| query | rows |", "|---|---|"]
    q_totals: dict[str, int] = {}
    for r in d.get("rows", []):
        q = (r.get("query") or "").rsplit(":", 1)[-1]
        q_totals[q] = q_totals.get(q, 0) + 1
    for q, n in sorted(q_totals.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {q} | {n} |")
    REPORT.write_text("\n".join(lines) + "\n")


def run_window(cities: list[str]) -> int:
    d = _load()
    d["ran_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for city in cities:
        rows, v = rs.fetch_craigslist(city, list(rs.CRAIGSLIST_QUERIES))
        # [W2B-1] replace this city's rows ONLY on an ok verdict — a
        # blocked (captcha mid-city / all-http-error) sweep keeps the
        # prior window's rows: the city PAUSES, it must not lose the
        # only store of its data. The old unconditional replace fired
        # on blocked/partial verdicts too (silent cross-window data
        # regression). The verdict is recorded honestly either way.
        if v.startswith("ok"):
            # drop old rows of THIS city, keep every other city's;
            # the defensive default KEEPS query-less rows (the old
            # `or f"cl:{city}:"` fallback dropped them)
            d["rows"] = [r for r in d.get("rows", [])
                         if not (r.get("query") or
                                 "").startswith(f"cl:{city}:")]
            d["rows"] += rows
        # the ledger row-count = rows PRESENT for this city (fresh on
        # ok, prior-kept on blocked — stays exact vs the artifact)
        n_city = sum(1 for r in d.get("rows", [])
                     if (r.get("query") or "").startswith(f"cl:{city}:"))
        d["cities_run"][city] = {
            "queries": len(rs.CRAIGSLIST_QUERIES), "rows": n_city,
            "verdict": v,
            "ran_at": datetime.now(timezone.utc)
                              .strftime("%Y-%m-%dT%H:%M:%SZ")}
        print(f"[cl:{city:<14}] {len(rows):>4} rows"
              + ("" if v.startswith("ok")
                 else f" (kept {n_city} prior)")
              + f" | {v[:70]}")
        _save(d)                      # incremental per-city checkpoint
    uniq = len({r.get("rid") for r in d["rows"]})
    d["gates"] = {"rows_total": len(d["rows"]),
                  "unique_posts": uniq,
                  "cross_query_dupes": len(d["rows"]) - uniq,
                  "cities": len(d["cities_run"]),
                  "queries_per_city": len(rs.CRAIGSLIST_QUERIES)}
    _save(d)
    _write_report(d)
    print(f"[craigslist] total {len(d['rows'])} rows across "
          f"{len(d['cities_run'])} cities")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city-window", default="",
                    help="N/M — cities window N of M (10-city chunks)")
    ap.add_argument("--city", default="",
                    help="a single city slug")
    ap.add_argument("--status", action="store_true",
                    help="read-only status of the artifact")
    args = ap.parse_args()
    if args.status:
        d = _load()
        print(json.dumps({"ran_at": d.get("ran_at"),
                          "cities": sorted(d.get("cities_run", {})),
                          "rows": len(d.get("rows", []))}, indent=1))
        return 0
    cities = list(rs.CRAIGSLIST_CITIES)
    if args.city:
        return run_window([args.city])
    if args.city_window:
        n_s, _, m_s = args.city_window.partition("/")
        n, m = int(n_s), max(1, int(m_s or 1))
        w = -(-len(cities) // m)
        lo, hi = (n - 1) * w, n * w
        return run_window(cities[lo:hi])
    ap.error("pass --city-window N/M or --city <slug>")
    return 2


if __name__ == "__main__":
    sys.exit(main())


def main_with(argv: list[str]) -> int:
    """test hook: argv-driven main (the __main__ guard stays clean)."""
    import argparse as _ap
    global argparse
    _old_argv, sys.argv = sys.argv, ["craigslist_sweep"] + argv
    try:
        return main()
    finally:
        sys.argv = _old_argv
