#!/usr/bin/env python3
"""S20-B instrument #4 — SEC EDGAR US-listed foreign-issuer classifier.

Walks company_tickers.json (~10k US-listed entities), fetching each
entity's submissions metadata (data.sec.gov, 10 req/s with UA), and
records stateOfIncorporationDescription — the nationality classifier
for US-listed companies ("China", "Hong Kong", "Cayman Islands", ...).

Output: ingest/data/ats_seed/s20_census/edgar_foreign_issuers.json
(only non-US-description entities are kept; resume-safe cache).

Usage: python3 scripts/s20_edgar_companies.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = REPO / "ingest/data/ats_seed/s20_census"
OUT.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "job-sourcing-research census (contact: "
                    "census@job-sourcing.invalid)"}
CACHE = OUT / "edgar_submissions_cache.json"

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI",
    "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
    "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR", "GU", "VI", "MP",
    "AS",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max entities (0=all)")
    args = ap.parse_args()

    r = requests.get("https://www.sec.gov/files/company_tickers.json",
                     headers=UA, timeout=60)
    tickers = list(r.json().values())  # [{ticker, cik_str, title}]
    print(f"entities in company_tickers.json: {len(tickers)}")

    cache: dict = {}
    if CACHE.exists():
        cache = json.loads(CACHE.read_text())
        print(f"cache: {len(cache)} entities already classified")

    results: dict = {}
    t0 = time.time()
    n_done = 0
    for ent in tickers:
        cik = ent["cik_str"]
        if args.limit and n_done >= args.limit:
            break
        if str(cik) in cache:
            rec = cache[str(cik)]
        else:
            url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
            for attempt in range(4):
                try:
                    rr = requests.get(url, headers=UA, timeout=30)
                    if rr.status_code == 429:
                        time.sleep(2 + attempt * 2)
                        continue
                    rr.raise_for_status()
                    d = rr.json()
                    rec = {
                        "name": d.get("name"),
                        "tickers": d.get("tickers"),
                        "exchanges": d.get("exchanges"),
                        "incorporation": d.get("stateOfIncorporation"),
                        "incorporationDesc": d.get("stateOfIncorporationDescription"),
                        "sic": d.get("sic"),
                        "sicDesc": d.get("sicDescription"),
                        "businessCountry": (d.get("businessAddress") or {}).get("country"),
                        "filingsRecent": (d.get("filings", {}).get("recent") or {}).get("form", [])[:5],
                    }
                    cache[str(cik)] = rec
                    break
                except Exception:
                    time.sleep(1 + attempt)
            else:
                rec = None
            time.sleep(0.12)
            # persist cache periodically
        n_done += 1
        if n_done % 200 == 0:
            CACHE.write_text(json.dumps(cache))
            print(f"  {n_done} walked ({time.time()-t0:.0f}s), "
                  f"cache {len(cache)}")
        if not rec:
            continue
        desc = (rec.get("incorporationDesc") or "").strip()
        # keep every entity whose incorporation is not a US state code
        if desc and desc.upper() not in US_STATES:
            results[str(cik)] = rec
        elif rec.get("incorporation") and rec["incorporation"].upper() not in US_STATES:
            results[str(cik)] = rec
    CACHE.write_text(json.dumps(cache))

    # finalize: keep only entities with actual recent filings (drops dead
    # registrations) + annotate China/HK flag
    out_rows = []
    for cik, rec in results.items():
        desc = (rec.get("incorporationDesc") or "")
        china = desc in ("China", "Hong Kong") or \
            (rec.get("businessCountry") in ("CH", "CN", "HK"))
        out_rows.append({**rec, "cik": cik,
                         "chinaFlag": bool(china),
                         "chinaFlagDesc": desc})
    out_rows.sort(key=lambda x: (0 if x["chinaFlag"] else 1,
                                 x.get("name") or ""))
    (OUT / "edgar_foreign_issuers.json").write_text(json.dumps(
        {"source": "SEC EDGAR submissions walk over company_tickers.json",
         "fetched": time.time(), "walked": n_done,
         "foreign_issuers": len(out_rows),
         "china_hk_flagged": sum(1 for x in out_rows if x["chinaFlag"]),
         "entities": out_rows}, indent=1))
    print(f"foreign issuers: {len(out_rows)} "
          f"(China/HK flagged: {sum(1 for x in out_rows if x['chinaFlag'])})")
    for x in [r for r in out_rows if r["chinaFlag"]][:20]:
        print(" ", x.get("name"), "|", x.get("incorporationDesc"),
              "|", x.get("tickers"))


if __name__ == "__main__":
    main()
