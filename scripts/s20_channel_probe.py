#!/usr/bin/env python3
"""S20-A channel probe — live-validate the at-scale discovery channels
for "Chinese companies hiring US roles".

Each probe: can we REACH it, what does it yield, at what cost.
Evidence -> ingest/data/ats_seed/s20_census/research/probe_*.json
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "ingest"))
from jobsearch.config import load_config  # noqa: E402

OUT = REPO / "ingest/data/ats_seed/s20_census/research"
OUT.mkdir(parents=True, exist_ok=True)


def proxy(url: str, cfg, mode: str = "raw", timeout: int = 90) -> requests.Response:
    q = urlencode({"url": url, "mode": mode})
    r = requests.get(f"{cfg.supabase_proxy_url}?{q}",
                     headers={"Authorization": f"Bearer {cfg.supabase_proxy_token}",
                              "x-region": "us-east-1"}, timeout=timeout)
    r.raise_for_status()
    return r


def rec(name: str, ok: bool, evidence: str, extra: dict | None = None):
    d = {"channel": name, "ok": ok, "evidence": evidence, "ts": time.time()}
    if extra:
        d.update(extra)
    (OUT / f"probe_{name}.json").write_text(json.dumps(d, indent=2))
    print(f"[{'OK ' if ok else 'FAIL'}] {name}: {evidence[:200]}")


def main():
    cfg = load_config()

    # --- CH-2: SEC EDGAR — Chinese issuers (country=CN, 20-F filers) ---
    try:
        r = proxy("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                  "&company=&country=CN&type=20-F&dateb=&owner=include&count=400"
                  "&search_text=", cfg)
        names = re.findall(r'companyName="([^"]+)"', r.text)
        ciks = re.findall(r"CIK=(\d{10})", r.text)
        rec("edgar_cn_20f", True,
            f"{len(set(ciks))} distinct CIKs, {len(set(names))} names "
            f"(country=CN, type=20-F)", {"sample": sorted(set(names))[:20]})
    except Exception as e:
        rec("edgar_cn_20f", False, f"{type(e).__name__}: {e}")

    # --- CH-3: Wikipedia — Chinese companies listed on US exchanges ---
    try:
        r = proxy("https://en.wikipedia.org/wiki/List_of_Chinese_companies"
                  "_listed_on_major_U.S._stock_exchanges", cfg)
        title = re.search(r"<title>([^<]+)</title>", r.text)
        wikitable_rows = len(re.findall(r"<tr[^>]*>\s*<td", r.text))
        rec("wikipedia_us_listed", "does not exist" not in (title.group(1) if title else ""),
            f"title='{title.group(1) if title else '?'}', ~{wikitable_rows} table rows, "
            f"{len(r.text)} bytes")
    except Exception as e:
        rec("wikipedia_us_listed", False, f"{type(e).__name__}: {e}")

    # --- CH-1: DOL performance page — LCA file inventory ---
    try:
        r = proxy("https://www.dol.gov/agencies/eta/foreign-labor/performance", cfg)
        quarters = sorted(set(re.findall(
            r"LCA_Disclosure_Data_FY(\d{4})_Q(\d)\.xlsx", r.text)))
        rec("dol_lca_files", True,
            f"{len(quarters)} LCA quarters visible; latest: "
            f"{['FY'+q[0]+'_Q'+q[1] for q in quarters[-4:]]}")
    except Exception as e:
        rec("dol_lca_files", False, f"{type(e).__name__}: {e}")

    # --- CH-6: h1bdata.info — employer browse surface ---
    try:
        r = proxy("https://h1bdata.info/topjobs.php", cfg)
        rec("h1bdata_info", r.status_code == 200,
            f"h1bdata.info {r.status_code}, {len(r.text)} bytes")
    except Exception as e:
        rec("h1bdata_info", False, f"{type(e).__name__}: {e}")

    # --- CH-6b: myvisajobs ---
    try:
        r = proxy("https://www.myvisajobs.com/", cfg)
        rec("myvisajobs", r.status_code == 200,
            f"myvisajobs {r.status_code}, {len(r.text)} bytes")
    except Exception as e:
        rec("myvisajobs", False, f"{type(e).__name__}: {e}")

    print("\nprobe artifacts ->", OUT)


if __name__ == "__main__":
    main()
