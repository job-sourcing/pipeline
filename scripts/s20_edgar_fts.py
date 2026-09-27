#!/usr/bin/env python3
"""S20-B instrument #4b — EDGAR full-text-search 20-F filer walk.

Pages efts.sec.gov/LATEST/search-index over forms=20-F (the foreign
private issuer annual report — the systematic US-listed foreign-company
set), collecting {name, tickers, CIK} from display_names, then
classifies each entity by stateOfIncorporationDescription via the
submissions API (10 req/s).

Output: ingest/data/ats_seed/s20_census/edgar_20f_foreign_issuers.json
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = REPO / "ingest/data/ats_seed/s20_census"
OUT.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "job-sourcing-research census census@job-sourcing.invalid",
      "Accept": "application/json"}

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI",
    "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
    "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR", "GU", "VI", "MP",
    "AS",
}
CHINA_HK = {"China", "Hong Kong"}


def fts_page(params: dict) -> dict:
    r = requests.get("https://efts.sec.gov/LATEST/search-index",
                     params=params, headers=UA, timeout=60)
    r.raise_for_status()
    return r.json()


def main():
    import os
    # --- stage 1: page the 20-F filers (2024-01-01 .. now) ---
    part = OUT / "edgar_20f_entities_partial.json"
    entities: dict[str, dict] = {}
    start_from = 0
    if part.exists():  # resume
        prev = json.loads(part.read_text())
        entities = prev["entities"]
        start_from = prev["next_from"]
        print(f"resuming at from={start_from} with {len(entities)} entities")
    for start in range(start_from, 20000, 100):
        params = {"q": "\"the\"", "forms": "20-F",
                  "dateRange": "custom", "startdt": "2024-01-01",
                  "enddt": "2026-09-26", "from": str(start)}
        try:
            d = fts_page(params)
        except Exception as e:
            print("fts page error:", e, "— stopping pagination")
            break
        hits = d.get("hits", {}).get("hits", [])
        if not hits:
            break
        for h in hits:
            src = h.get("_source", {})
            names = src.get("display_names") or []
            for nm in names:
                m = re.match(r"(.+?)\s*\(([^()]*)\)\s*\(?CIK\s?(\d+)\)?", nm)
                if m:
                    name, tickers, cik = m.group(1).strip(), m.group(2), m.group(3)
                    entities[cik] = {"name": name, "tickers": tickers,
                                     "cik": cik}
                    break
            else:
                # no-ticker form: "Name  CIK 1234567"
                m2 = re.match(r"(.+?)\s+CIK\s?(\d+)$", names[0] if names else "")
                if m2:
                    entities[m2.group(2)] = {"name": m2.group(1).strip(),
                                             "tickers": "", "cik": m2.group(2)}
        print(f"  fts page from={start}: total seen {len(entities)}")
        part.write_text(json.dumps({"entities": entities,
                                    "next_from": start + 100}))
        if start >= 9900 and start % 1000 == 0:
            print("  NOTE: beyond the FTS 10k window — relying on 2024+ "
                  "date slice to stay under it")
        time.sleep(0.3)
    print(f"20-F filers (2024+): {len(entities)}")
    if os.environ.get("SKIP_CLASSIFY"):
        return

    # --- stage 2: classify by incorporation description ---
    rows = []
    for i, (cik, ent) in enumerate(sorted(entities.items())):
        rec = dict(ent)
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
                rec["incorporationDesc"] = d.get("stateOfIncorporationDescription")
                rec["incorporation"] = d.get("stateOfIncorporation")
                rec["sicDesc"] = d.get("sicDescription")
                rec["businessCountry"] = (d.get("businessAddress") or {}).get("country")
                break
            except Exception:
                time.sleep(1 + attempt)
        time.sleep(0.11)
        if i % 200 == 0:
            print(f"  classified {i}/{len(entities)}")
        rows.append(rec)

    for r_ in rows:
        desc = (r_.get("incorporationDesc") or "").strip()
        r_["chinaFlag"] = desc in CHINA_HK or r_.get("businessCountry") in ("CH", "CN", "HK")
        r_["foreignFlag"] = desc.upper() not in US_STATES and desc != ""

    china = [r_ for r_ in rows if r_.get("chinaFlag")]
    (OUT / "edgar_20f_foreign_issuers.json").write_text(json.dumps(
        {"source": "EDGAR FTS forms=20-F 2025-2026 + submissions walk",
         "fetched": time.time(), "filers_20f": len(rows),
         "china_hk": len(china), "entities": rows}, indent=1))
    print(f"20-F filers: {len(rows)} | China/HK: {len(china)}")
    for r_ in china[:25]:
        print("  ", r_.get("name"), "|", r_.get("incorporationDesc"),
              "|", r_.get("tickers"))


if __name__ == "__main__":
    main()
