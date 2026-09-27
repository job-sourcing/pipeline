#!/usr/bin/env python3
"""S20-B instrument #1 — Wikipedia "List of companies of China" parser.

The page (~371KB) organizes Chinese companies by industry sector in
wikitables. This extracts {company, sector} rows via the MediaWiki API
wikitext (more parseable than rendered HTML), dedupes, and writes
ingest/data/ats_seed/s20_census/wiki_companies_of_china.json.

Transport: supabase proxy (Wikipedia 403s the HK egress directly).
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

OUT = REPO / "ingest/data/ats_seed/s20_census"
OUT.mkdir(parents=True, exist_ok=True)

PAGE = "List of companies of China"


def proxy(url: str, cfg, timeout: int = 120) -> requests.Response:
    q = urlencode({"url": url, "mode": "raw"})
    return requests.get(f"{cfg.supabase_proxy_url}?{q}",
                        headers={"Authorization": f"Bearer {cfg.supabase_proxy_token}",
                                 "x-region": "us-east-1"}, timeout=timeout)


def get_wikitext(cfg) -> str:
    url = ("https://en.wikipedia.org/w/api.php?action=parse&page="
           + PAGE.replace(" ", "_") + "&prop=wikitext&format=json")
    r = proxy(url, cfg)
    d = r.json()
    return d["parse"]["wikitext"]["*"]


def parse_entries(wikitext: str) -> list[dict]:
    """The real rows are {{Company-list table entry}} templates:
    | name | industry | sector | HQ | founded | (notes/defunct...).
    Walk them as multi-line template blocks."""
    rows: list[dict] = []
    lines = wikitext.split("\n")
    i = 0
    sector = ""
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"^(=+)\s*(.*?)\s*=+\s*$", ln)
        if m and len(m.group(1)) <= 2:
            sector = m.group(2).strip()
            i += 1
            continue
        if ln.strip().startswith("{{Company-list table entry"):
            block: list[str] = [ln.strip()]
            depth = ln.count("{{") - ln.count("}}")
            i += 1
            while i < len(lines) and depth > 0:
                block.append(lines[i].strip())
                depth += lines[i].count("{{") - lines[i].count("}}")
                i += 1
            rows.append(_parse_entry(block, sector))
            continue
        i += 1
    return [r for r in rows if r]


def _parse_entry(block: list[str], sector: str) -> dict | None:
    # join and split on top-level pipes
    joined = " ".join(block)
    joined = re.sub(r"\{\{Company-list table entry\s*", "", joined)
    joined = re.sub(r"\}\}\s*$", "", joined)
    # split on '|' not inside nested {{...}} or [[...]]
    parts: list[str] = []
    depth = 0
    cur = ""
    for ch in joined:
        if ch == "[" or ch == "{":
            depth += 1
        elif ch == "]" or ch == "}":
            depth -= 1
        if ch == "|" and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return None
    def clean(p: str) -> str:
        links = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", p)
        name = links[0].strip() if links else p
        name = re.sub(r"<[^>]+>", "", name)
        return name.strip()
    company = clean(parts[0])
    industry = clean(parts[1]) if len(parts) > 1 else ""
    subsector = clean(parts[2]) if len(parts) > 2 else ""
    hq = clean(parts[3]) if len(parts) > 3 else ""
    tail = " ".join(parts[5:]) if len(parts) > 5 else ""
    return {"company": company, "industry": industry, "subsector": subsector,
            "hq": hq, "section": sector,
            "defunct": bool(re.search(r"defunct", tail, re.I))}


def main():
    cfg = load_config()
    wt = get_wikitext(cfg)
    (OUT / "wiki_list_companies_of_china.wikitext").write_text(wt)
    rows = parse_entries(wt)
    # dedupe by company name
    seen: dict[str, dict] = {}
    for r in rows:
        key = r["company"].lower()
        if key not in seen:
            seen[key] = r
    companies = sorted(seen.values(), key=lambda x: x["company"].lower())
    (OUT / "wiki_companies_of_china.json").write_text(
        json.dumps({"source": f"wikipedia:{PAGE}", "fetched": time.time(),
                    "count": len(companies), "companies": companies},
                   indent=1, ensure_ascii=False))
    sectors: dict[str, int] = {}
    for c in companies:
        sectors[c["section"]] = sectors.get(c["section"], 0) + 1
    print(f"companies: {len(companies)} (raw rows {len(rows)})")
    for s, n in sorted(sectors.items(), key=lambda x: -x[1]):
        print(f"  {s}: {n}")


if __name__ == "__main__":
    main()
