#!/usr/bin/env python3
"""s35_aggregator_probe.py — the free remote-API aggregator probe (S35 D2).

Probes the sibling's remote-job API layer (remotive / remoteok / jobicy /
arbeitnow — all free, no firecrawl credits) against OUR watch roster.
Output: audit/s35_aggregator/probe_results.json + per-source yield table.

Doctrine: census-first (measure yield BEFORE wiring any adapter).
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

MIRROR = Path("/home/z/repos/pipeline-mirror")
OUT = Path("/home/z/repos/job-sourcing-research/audit/s35_aggregator")
OUT.mkdir(parents=True, exist_ok=True)

SOURCES = {
    "remotive": "https://remotive.com/api/remote-jobs?limit=1000",
    "remoteok": "https://remoteok.com/api",
    "jobicy": "https://jobicy.com/api/v2/remote-jobs?count=200",
    "arbeitnow": "https://www.arbeitnow.com/api/job-board-api",
}


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def main():
    cfg = json.load(open(MIRROR / "ingest/data/board_watch/config.json"))
    companies = {}
    for w in cfg["watches"]:
        # variant strings incl. li_variants + company name
        variants = set()
        variants.add(w.get("company", ""))
        variants.update(w.get("li_variants", []))
        # strip parenthetical suffixes ("VAVA (Sunvalley Group)" -> "VAVA")
        for v in list(variants):
            base = re.sub(r"\s*\(.*?\)\s*$", "", v).strip()
            if base and base != v:
                variants.add(base)
        keys = {norm(v) for v in variants if v and len(norm(v)) >= 3}
        companies.setdefault(w["company"], set()).update(keys)

    results = {}
    for name, url in SOURCES.items():
        try:
            data = fetch(url)
        except Exception as e:
            results[name] = {"ok": False, "error": str(e)[:200], "hits": []}
            print(f"{name}: FETCH FAILED — {e}")
            continue
        # normalize each source's row shape
        rows = []
        if name == "remotive":
            rows = data.get("jobs", [])
            rows = [{"company": r.get("company", ""),
                     "title": r.get("title", ""),
                     "loc": r.get("candidate_required_location", ""),
                     "url": r.get("url", ""),
                     "type": r.get("job_type", ""),
                     "posted": (r.get("publication_date") or "")[:10]}
                    for r in rows]
        elif name == "remoteok":
            rows = [r for r in data if isinstance(r, dict) and r.get("company")]
            rows = [{"company": r.get("company", ""),
                     "title": r.get("position", ""),
                     "loc": r.get("location", ""),
                     "url": r.get("url", ""),
                     "type": "", "posted": (r.get("date") or "")[:10]}
                    for r in rows]
        elif name == "jobicy":
            rows = data.get("jobs", [])
            rows = [{"company": r.get("companyName", ""),
                     "title": r.get("jobTitle", ""),
                     "loc": r.get("jobGeo", ""),
                     "url": r.get("url", ""),
                     "type": r.get("jobType", ""),
                     "posted": (r.get("pubDate") or "")[:10]}
                    for r in rows]
        elif name == "arbeitnow":
            rows = data.get("data", [])
            rows = [{"company": r.get("company", ""),
                     "title": r.get("title", ""),
                     "loc": r.get("location", ""),
                     "url": r.get("url", ""),
                     "type": r.get("job_type", ""),
                     "posted": str(r.get("created_at") or "")[:10]}
                    for r in rows]
        # match: EXACT variant equality (the gjobs doctrine — never
        # substring; the S35 review's P0-5: "engine" in "tdengine"
        # was a false positive)
        hits = []
        for row in rows:
            rc = norm(row["company"])
            if not rc:
                continue
            for comp, keys in companies.items():
                if rc in keys:
                    hits.append({"company": comp, "row": row,
                                 "matched_key": rc})
                    break
        results[name] = {"ok": True, "total_rows": len(rows), "hits": hits}
        uniq = {h["company"] for h in hits}
        print(f"{name}: {len(rows)} rows, {len(hits)} matches "
              f"at {len(uniq)} companies: {sorted(uniq)[:12]}")

    OUT.joinpath("probe_results.json").write_text(json.dumps(results, indent=1))
    print(f"\nwritten: {OUT / 'probe_results.json'}")


if __name__ == "__main__":
    sys.exit(main())
