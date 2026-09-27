#!/usr/bin/env python3
"""S20-B instrument #2 — Wikipedia Category:Companies of China walker.

Recursively walks the category tree under top Chinese-company categories
(depth-limited), collecting article titles (company names) tagged with
their category path. Output:
ingest/data/ats_seed/s20_census/wiki_category_companies.json

Transport: supabase proxy (Wikipedia 403s direct HK egress).
API: list=categorymembers, cmtype=subcat|page, cmlimit=500, continuation.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, quote

import requests

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "ingest"))
from jobsearch.config import load_config  # noqa: E402

OUT = REPO / "ingest/data/ats_seed/s20_census"
OUT.mkdir(parents=True, exist_ok=True)

TOP_CATS = [
    "Category:Companies of China",
    "Category:Chinese companies",
]
# categories that are noise for company discovery
SKIP_CAT_RE = [
    r"companies disestablished", r"companies established in \d",
    r"defunct", r"Lists of companies", r"company stubs",
    r":Articles", r" templates", r" navigational boxes",
    r"former companies", r"mergers and acquisitions",
]
DEPTH = 2
CACHE = OUT / "wiki_category_cache.json"


def proxy_json(url: str, cfg, retries: int = 5) -> dict:
    q = urlencode({"url": url, "mode": "raw"})
    for attempt in range(retries):
        r = requests.get(f"{cfg.supabase_proxy_url}?{q}",
                         headers={"Authorization": f"Bearer {cfg.supabase_proxy_token}",
                                  "x-region": "us-east-1"}, timeout=90)
        if r.status_code == 429:
            wait = min(2 ** attempt * 5, 60)
            print(f"    429 — backoff {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


def api_members(cfg, cat: str, cmtype: str, cont: dict | None) -> tuple[list, dict | None]:
    params = {"action": "query", "list": "categorymembers",
              "cmtitle": cat, "cmtype": cmtype, "cmlimit": "500",
              "format": "json"}
    if cont:
        params.update(cont)
    url = ("https://en.wikipedia.org/w/api.php?" + urlencode(params))
    d = proxy_json(url, cfg)
    return d["query"]["categorymembers"], d.get("continue")


def walk(cfg):
    cache: dict = {}
    if CACHE.exists():
        cache = json.loads(CACHE.read_text())
    companies: dict[str, dict] = {}
    stats = {"api_calls": 0, "subcats_seen": 0}

    def should_skip(cat: str) -> bool:
        return any(re.search(p, cat, re.I) for p in SKIP_CAT_RE)

    import re  # noqa: F811 — used above lazily

    def recurse(cat: str, depth: int, path: list[str]):
        if depth > DEPTH or should_skip(cat) or cat in path:
            return
        # pages (companies) — with continuation
        cont = None
        while True:
            key = f"{cat}|page|{cont}"
            if key in cache:
                members, new_cont = cache[key]
            else:
                members, new_cont = api_members(cfg, cat, "page", cont)
                cache[key] = [members, new_cont]
                stats["api_calls"] += 1
                CACHE.write_text(json.dumps(cache))
                time.sleep(0.4)
            for m in members:
                title = m["title"]
                if ":" in title and not title.startswith("Category:"):
                    continue  # portal/talk/etc
                if title not in companies:
                    companies[title] = {"company": title, "category": cat,
                                        "category_path": " > ".join(path + [cat])}
            if not new_cont or "cmcontinue" not in new_cont:
                break
            cont = {"cmcontinue": new_cont["cmcontinue"]}
        # subcategories
        cont = None
        while True:
            key = f"{cat}|subcat|{cont}"
            if key in cache:
                members, new_cont = cache[key]
            else:
                members, new_cont = api_members(cfg, cat, "subcat", cont)
                cache[key] = [members, new_cont]
                stats["api_calls"] += 1
                CACHE.write_text(json.dumps(cache))
                time.sleep(0.4)
            stats["subcats_seen"] += len(members)
            for m in members:
                recurse(m["title"], depth + 1, path + [cat])
            if not new_cont or "cmcontinue" not in new_cont:
                break
            cont = {"cmcontinue": new_cont["cmcontinue"]}

    for top in TOP_CATS:
        print(f"walking {top} ...")
        recurse(top, 0, [])
        print(f"  companies so far: {len(companies)}")
    return companies, stats


def main():
    cfg = load_config()
    companies, stats = walk(cfg)
    out = sorted(companies.values(), key=lambda x: x["company"].lower())
    (OUT / "wiki_category_companies.json").write_text(json.dumps(
        {"source": "wikipedia category walk (Companies of China, Chinese companies, depth 2)",
         "fetched": time.time(), "count": len(out), "api_calls": stats["api_calls"],
         "companies": out}, indent=1, ensure_ascii=False))
    print(f"companies: {len(out)}  api_calls: {stats['api_calls']}  "
          f"subcats_seen: {stats['subcats_seen']}")


if __name__ == "__main__":
    main()
