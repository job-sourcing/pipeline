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
    ap.add_argument("--spec", default="",
                    help='board spec: "tenant|instance|site" (site "?" = '
                         "auto-discover), ats:kind:org, or custom:kind")
    ap.add_argument("--url", default="",
                    help="S22 raw-URL probe: fetch from GHA egress, "
                         "report bytes/status/title/platform hints (the "
                         "CF/geo-walled board triage — careers pages "
                         "that need a US egress read)")
    ap.add_argument("--country", default="United States")
    ap.add_argument("--time-type", default=None)
    args = ap.parse_args()

    spec = args.spec.strip()
    out = {"spec": spec, "country": args.country}
    if args.url or spec.startswith("http"):
        # S22: an http(s) spec routes to the raw-URL probe (the GHA
        # workflow input only carries --spec — URL-shape specs are the
        # CF/geo-walled board triage class)
        args.url = args.url or spec
        import re as _re
        u = args.url.strip()
        out = {"url": u}
        try:
            req = urllib.request.Request(u, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/131.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,"
                          "application/json;q=0.9,*/*;q=0.8"})
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read().decode("utf-8", "replace")
                out.update({"status": "ok", "http": r.status,
                            "final_url": r.geturl(), "bytes": len(body)})
            # S23: WordPress route-index extraction — a wp-json/ root
            # body IS the route table (205KB JSON listing every REST
            # route); surface job/career-named routes directly
            if "wp-json" in u and body.lstrip()[:1] == "{":
                try:
                    j = json.loads(body)
                    job_routes = [r_ for r_ in (j.get("routes") or {})
                                  if _re.search(
                                      r"job|career|position|vacancy|"
                                      r"recruit|hiring|opening", r_,
                                      _re.I)]
                    out["wp_routes_total"] = len(j.get("routes") or {})
                    out["wp_job_routes"] = job_routes[:40]
                except json.JSONDecodeError:
                    pass
            t = _re.search(r"<title[^>]*>(.*?)</title>", body,
                           _re.S | _re.I)
            out["title"] = (t.group(1).strip()[:120] if t else "")
            hints = []
            for pat, name in [
                    (r"wd\d+\.myworkdayjobs", "workday"),
                    (r"boards\.greenhouse\.io|job-boards\.greenhouse",
                     "greenhouse"), (r"jobs\.ashbyhq\.com", "ashby"),
                    (r"jobs\.lever\.co", "lever"),
                    (r"apply\.workable\.com|jobs\.workable", "workable"),
                    ("smartrecruiters", "smartrecruiters"),
                    ("myworkdaysite", "workday-site"),
                    ("__NEXT_DATA__", "nextjs"),
                    (r"breezy\.hr", "breezy"), ("icims", "icims"),
                    ("successfactors|sapsf", "successfactors"),
                    ("taleo", "taleo"), ("jobvite", "jobvite"),
                    ("brassring", "brassring"),
                    (r"application/ld\+json", "jsonld"),
                    ("wp-json", "wordpress")]:
                if _re.search(pat, body, _re.I):
                    hints.append(name)
            out["platform_hints"] = hints
            us = _re.findall(
                r"[^<>]{0,50}(?:United States|, [A-Z]{2}\b|Remote - US)"
                r"[^<>]{0,40}", body)
            out["us_text_hits"] = len(us)
            out["us_samples"] = [x.strip()[:70] for x in us[:5]]
            # S22b: SPA triage — collect script srcs + api/fetch hints
            # from the first N bundles (the Trina class: real HTML from
            # GHA but data lives in XHRs the shell references)
            srcs = _re.findall(
                r'<script[^>]+src="([^"]+\.js[^"]*)"', body)
            out["script_srcs"] = [x[:120] for x in srcs[:8]]
            api_hints = []
            for src in srcs[:6]:
                if src.startswith("/"):
                    src = u.rstrip("/") + src
                elif not src.startswith("http"):
                    continue
                try:
                    req2 = urllib.request.Request(src, headers={
                        "User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req2, timeout=25) as r2:
                        js = r2.read(400000).decode("utf-8", "replace")
                    for m2 in _re.findall(
                            r'https?://[A-Za-z0-9._\-]+(?:/api/|/v[0-9]/)'
                            r"[A-Za-z0-9/._\-]{2,50}", js)[:6]:
                        if m2 not in api_hints:
                            api_hints.append(m2)
                    for m3 in _re.findall(
                            r'["\']/(api/[A-Za-z0-9/._\-]{2,60})'
                            r'["\']', js)[:8]:
                        cand = u.rstrip("/") + "/" + m3
                        if cand not in api_hints:
                            api_hints.append(cand)
                except Exception:
                    pass
            out["api_hints"] = api_hints[:12]
        except Exception as e:
            out.update({"status": "error",
                        "error": f"{type(e).__name__}: {e}"[:300]})
        print(json.dumps(out, ensure_ascii=False))
        return 0
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
