#!/usr/bin/env python3
"""board_probe.py — the GENERIC board-egress probe (S18: the Kwai ask,
built as a reusable instrument, not a one-off).

Runs from wherever you dispatch it (local HK egress OR a GHA US-egress
runner — that is the point) and reports, for ANY board spec the pipeline
knows:

  workday  tenant|instance|site    — CXS list (site '?' = auto-discover
                                     the site id from the board page's
                                     embedded config first)
  ats:kind:org / custom:kind       — the site-boards dispatch seam

Output: one JSON line summary {spec, status, rows, total, meta} — a
census/probe record, never pipeline state.

Usage:
  python3 scripts/board_probe.py --spec "kwai|wd3|?" --country us
  python3 scripts/board_probe.py --spec "ats:paylocity:{guid}" --country us
  GHA: dispatch board-probe.yml with inputs {spec, country} — the runner's
  US egress is the WAF-blocked-board rescue path (Kwai's workday board
  406s from HK but may serve from GitHub's Azure ranges).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ingest"))

from jobsearch.config import Config                 # noqa: E402
from jobsearch.sources import site_boards           # noqa: E402
from jobsearch.sources import workday               # noqa: E402


def discover_workday_site(tenant: str, instance: str) -> str:
    """Fetch the board page and extract the career-site id from the
    embedded wday config (the page HTML carries the site in its JSON
    bootstrap / canonical paths). Raises on capture-free pages."""
    url = f"https://{tenant}.{instance}.myworkdayjobs.com/"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    # pattern 1: embedded JSON "site":"xyz" (the wday bootstrap)
    m = re.search(r'"site"\s*:\s*"([A-Za-z0-9_-]{3,60})"', html)
    if m:
        return m.group(1)
    # pattern 2: canonical/careers path segments
    m = re.search(r'(?:canonical|href)="?/([A-Za-z0-9_-]{3,60})(?:/'
                  r'|\?[^"]*)?"', html)
    if m and not m.group(1).lower().startswith(("en", "en-us", "jobs")):
        return m.group(1)
    # pattern 3: jsonPath site hints
    m = re.search(r'site\s*=\s*"([A-Za-z0-9_-]{3,60})"', html)
    if m:
        return m.group(1)
    raise RuntimeError(
        f"{url}: no site id discoverable in the board page "
        f"({len(html)} bytes fetched)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True,
                    help='board spec: "tenant|instance|site" (site "?" = '
                         "auto-discover), ats:kind:org, or custom:kind")
    ap.add_argument("--country", default="United States")
    ap.add_argument("--time-type", default=None)
    args = ap.parse_args()

    spec = args.spec.strip()
    out = {"spec": spec, "country": args.country}
    try:
        if "|" in spec:
            parts = spec.split("|")
            tenant, instance = parts[0], parts[1]
            site = parts[2] if len(parts) > 2 else "?"
            if site in ("?", ""):
                site = discover_workday_site(tenant, instance)
                out["site_discovered"] = site
                spec = f"{tenant}|{instance}|{site}"
                out["spec"] = spec
            rows, meta = workday.list_board(
                spec, country=args.country, time_type=args.time_type,
                cfg=Config(), client_filter=False, progress_every=0)
        else:
            tt = args.time_type if args.time_type != "" else None
            rows, meta = site_boards.list_board(
                spec, country=args.country, time_type=tt, cfg=Config(),
                progress_label="probe")
        out.update({"status": "ok", "rows": len(rows),
                    "total": meta.get("total"),
                    "complete": meta.get("complete"),
                    "meta": {k: v for k, v in meta.items()
                             if isinstance(v, (str, int, bool, float,
                                               list))}})
    except Exception as e:
        out.update({"status": "error",
                    "error": f"{type(e).__name__}: {e}"[:400]})
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
