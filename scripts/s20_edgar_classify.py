#!/usr/bin/env python3
"""S20-B instrument #4c — classify the EDGAR 20-F filers (from the
full-index master files) by incorporation description via the
submissions API. Completes the US-listed foreign-issuer axis.

Input:  ingest/data/ats_seed/s20_census/edgar_20f_filers_index.json
Output: ingest/data/ats_seed/s20_census/edgar_20f_classified.json
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = REPO / "ingest/data/ats_seed/s20_census"
UA = {"User-Agent": "job-sourcing-research census census@job-sourcing.invalid"}
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI",
    "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
    "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR", "GU", "VI", "MP",
    "AS",
}
CHINA_HK = {"China", "Hong Kong"}
CACHE = OUT / "edgar_20f_class_cache.json"


def main():
    filers = json.loads((OUT / "edgar_20f_filers_index.json").read_text())
    cache: dict = {}
    if CACHE.exists():
        cache = json.loads(CACHE.read_text())
    rows = []
    t0 = time.time()
    for i, (cik, name) in enumerate(sorted(filers.items())):
        if cik in cache:
            rec = cache[cik]
        else:
            rec = {}
            for attempt in range(3):
                try:
                    r = requests.get(
                        f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                        headers=UA, timeout=30)
                    if r.status_code == 429:
                        time.sleep(2 + attempt)
                        continue
                    r.raise_for_status()
                    d = r.json()
                    rec = {
                        "name": d.get("name") or name,
                        "tickers": d.get("tickers"),
                        "exchanges": d.get("exchanges"),
                        "incorporationDesc": d.get("stateOfIncorporationDescription"),
                        "sicDesc": d.get("sicDescription"),
                        "businessCountry": (d.get("businessAddress") or {}).get("country"),
                    }
                    cache[cik] = rec
                    break
                except Exception:
                    time.sleep(1 + attempt)
            time.sleep(0.11)
        rec = dict(rec)
        rec["cik"] = cik
        desc = (rec.get("incorporationDesc") or "").strip()
        rec["chinaFlag"] = desc in CHINA_HK or rec.get("businessCountry") in ("CH", "CN", "HK")
        rec["foreignFlag"] = desc.upper() not in US_STATES and bool(desc)
        rows.append(rec)
        if i % 200 == 0:
            CACHE.write_text(json.dumps(cache))
            print(f"  {i}/{len(filers)} ({time.time()-t0:.0f}s)")
    CACHE.write_text(json.dumps(cache))
    china = [r for r in rows if r.get("chinaFlag")]
    cayman = [r for r in rows if (r.get("incorporationDesc") or "") == "Cayman Islands"]
    (OUT / "edgar_20f_classified.json").write_text(json.dumps(
        {"source": "EDGAR full-index 20-F filers (4 quarters) + submissions",
         "fetched": time.time(), "filers": len(rows),
         "china_hk": len(china), "cayman": len(cayman),
         "entities": rows}, indent=1))
    print(f"20-F filers: {len(rows)} | China/HK: {len(china)} | "
          f"Cayman (needs verify): {len(cayman)}")
    for r in china:
        print("  CN/HK:", r.get("name"), "|", r.get("incorporationDesc"),
              "|", (r.get("tickers") or [""])[0])


if __name__ == "__main__":
    main()
