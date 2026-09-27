#!/usr/bin/env python3
"""S20-C — the master cross-reference matcher (v2).

Joins every discovery axis into ONE master census:
  axis A: Chinese-company master lists (Wikipedia category walk,
          Wikipedia notable list, EDGAR 20-F filers CN/HK/Cayman/BVI,
          the live roster's Chinese-origin companies + CN AI startups)
  axis B: LCA employer universe (FY2026 Q3 — US-hiring EVIDENCE)
  axis C: ATS directory boards (9,705 — careers-surface EVIDENCE)

Match rule v2 (the v1 lesson: single-token matching drowned in
false positives — "Pop Mart"→WAL-MART, "…Development"→AMAZON
DEVELOPMENT CENTER):
  1. IDF token rarity: a master token is matchable only if it appears
     in <= RARITY_CAP distinct LCA employers AND <= RARITY_CAP distinct
     ATS board companies (corpus-frequency rarity, no hand lists).
  2. ALL-rare-tokens rule: a candidate employer/board must contain
     EVERY rare token of the company name (1-token names = that token).
  3. MNC-China-arm demotion: names that are foreign-MNC China pages
     ("Amazon China") get suspect flags, not deletion (auditable).

Output: ingest/data/ats_seed/s20_census/master_census.json
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = REPO / "ingest/data/ats_seed/s20_census"

RARITY_CAP = 8

GENERIC = {
    "GROUP", "HOLDINGS", "HOLDING", "TECHNOLOGY", "TECHNOLOGIES", "CO",
    "LTD", "LLC", "INC", "CORP", "CORPORATION", "COMPANY", "LIMITED",
    "INTERNATIONAL", "AMERICA", "AMERICAN", "USA", "US", "THE", "AND",
    "OF", "ENTERPRISES", "ENTERPRISE", "INDUSTRY", "INDUSTRIAL",
    "INDUSTRIES", "GLOBAL", "CHINA", "CHINESE", "NORTH", "SOUTH",
    "EAST", "WEST", "NEW", "DEVELOPMENT", "CENTER", "CENTRE", "BANK",
    "HOSPITAL", "UNIVERSITY", "SCHOOL", "COLLEGE", "INSTITUTE",
    "BANKING", "RAILWAY", "RAILROAD", "RAILWAYS", "SPORTS", "BUS",
    "SHENZHEN", "BEIJING", "SHANGHAI", "HANGZHOU", "GUANGZHOU",
    "PHARMA", "PHARMACEUTICAL", "MEDICAL", "BIO", "Biosciences".upper(),
    "ELECTRONICS", "ELECTRIC", "ENERGY", "AEROSPACE", "AVIATION",
    "SERVICES", "SERVICE", "SOLUTIONS", "SYSTEMS", "COMMUNICATIONS",
    "INSURANCE", "SECURITIES", "INVESTMENTS", "INVESTMENT", "CAPITAL",
    "MANAGEMENT", "CONSULTING", "TRADING", "SUPPLY", "CHAIN", "STORES",
    "RETAIL", "BRANDS", "FASHION", "FOODS", "TRAVEL", "TOURISM",
    "REALTY", "PROPERTIES", "PROPERTY", "CONSTRUCTION", "ENGINEERING",
    "LOGISTICS", "TRANSPORT", "TRANSPORTATION", "MOTORS", "AUTOMOTIVE",
    "AUTO", "MART", "MEDIA", "NETWORK", "NETWORKS", "SOFTWARE",
    "DIGITAL", "DATA", "CLOUD", "HEALTH", "HEALTHCARE", "LIFE",
    "SCIENCE", "SCIENCES", "MATERIALS", "CHEMICAL", "MACHINERY",
    "EQUIPMENT", "TECH", "LABS", "LABORATORIES", "STUDIO", "STUDIOS",
    "GAMES", "GAMING", "ENTERTAINMENT", "CULTURE", "PUBLISHING",
    "EDUCATION", "FINANCE", "FINANCIAL", "FUND", "TRUST", "GENERAL",
    "NATIONAL", "STATE", "OCEAN", "SUN", "STAR", "GREAT", "FIRST",
    "SUPER", "SMART", "FUTURE", "POWER", "LIGHT", "TOWN", "CITY",
    "LAND", "HARBOR", "HARBOUR", "PORT", "RIVER", "PACIFIC", "ASIA",
    "HONG", "KONG", "MACAU", "TAIWAN", "SINGAPORE", "SUC", "SHARE",
    "PUBLIC", "JOINT", "STOCK", "PLC", "AG", "NV", "SA", "SE",
}
SHORT_BRANDS = {
    "BYD", "NIO", "PDD", "TCL", "OPPO", "VIVO", "ZTE", "BOE", "CATL",
    "SHEIN", "TEMU", "MEITU", "WECHAT", "ZTO", "TME", "IQIYI", "VIPS",
    "BILIBILI", "CTRIP", "QIHOO", "UCAR", "SAIC", "CRRC", "COMAC",
    "FREYR", "NIO", "XPENG", "LI", "AI", "JD", "QQ", "UC", "VIP",
    "KDS", "OKI", "NEC", "SU",
}

# Chinese-origin companies on the live roster + CN startups from the
# S16-S18 censuses (our own prior evidence — US reference companies
# like nvidia/openai/anthropic/netflix are deliberately absent).
CN_ROSTER = [
    "Alibaba Group", "Baidu", "BYD", "ByteDance", "DiDi", "GE Appliances",
    "Gotion", "Horizon Robotics", "JD.com", "MiniMax", "Moonshot AI",
    "NetEase Games", "Pony.ai", "Shengshu Technology", "SHEIN",
    "Tencent", "TP-Link", "Trip.com Group", "United Imaging",
    "WeRide", "Xiaohongshu", "HoYoverse", "PlusAI", "TCL",
    "Faraday Future", "Zhipu AI", "Baichuan AI", "01.AI", "StepFun",
    "Zhipu", "MiniMax AI", "Kuaishou", "Kwai", "Manus", "PixVerse",
    "Shanghai AI Lab", "SenseTime", "Megvii", "CloudWalk", "Yitu",
    "DeepSeek", "Westlake", "Shanghai Innovation Institute",
]


def tokens(name: str) -> list[str]:
    s = re.sub(r"[^A-Za-z0-9&\.\- ]", " ", str(name).upper())
    parts = re.split(r"[\s\.\-]+", s)
    return [p for p in parts if p]


def name_tokens(name: str) -> set[str]:
    """All tokens (for containment checks)."""
    return set(tokens(name))


def rare_candidates(name: str) -> set[str]:
    """Brand-distinctive tokens of a company name (pre-rarity).
    Any non-generic token >= 2 chars (acronyms included — FAW, SAIC —
    rarity does the real filtering)."""
    out = set()
    for t in tokens(name):
        if t in GENERIC:
            continue
        if len(t) >= 2 and not t.isdigit() and t != "COM" and t != "NET":
            out.add(t)
    return out


def normalize(name: str) -> str:
    return " ".join(tokens(name))


def main():
    # ---------- load ----------
    wiki_cat = json.loads((OUT / "wiki_category_companies.json").read_text())
    wiki_notable = json.loads((OUT / "wiki_companies_of_china.json").read_text())
    edgar = json.loads((OUT / "edgar_20f_classified.json").read_text())
    lca = json.loads((OUT / "lca_employers_fy2026q3.json").read_text())
    ats_dir = json.loads(
        (REPO / "ingest/data/ats_directory_snapshot.json").read_text())
    lca_rows = lca["employers"]
    boards = ats_dir["boards"]

    # ---------- axis A ----------
    master: dict[str, dict] = {}

    def add(name: str, source: str, extra: dict | None = None):
        key = normalize(name)
        if not key:
            return
        ent = master.setdefault(key, {
            "name": name, "sources": [], "lca": None, "ats": [],
            "flags": []})
        if source not in ent["sources"]:
            ent["sources"].append(source)
        if extra:
            for k, v in extra.items():
                if k not in ent or ent[k] in (None, "", []):
                    ent[k] = v

    for c in wiki_cat["companies"]:
        catpath = (c.get("category_path") or "") + " " + (c.get("category") or "")
        if re.search(r"businesspeople|\bpeople\b|executives|founders|"
                     r"chief executive|chairmen|billionaires|alumni|employees",
                     catpath, re.I):
            continue  # person articles, not companies
        add(c["company"], "wiki_category", {"category": c.get("category")})
    for c in wiki_notable["companies"]:
        add(c["company"], "wiki_notable",
            {"industry": c.get("industry"), "subsector": c.get("subsector")})
    for e in edgar["entities"]:
        desc = (e.get("incorporationDesc") or "")
        if desc in ("China", "Hong Kong", "Cayman Islands",
                    "British Virgin Islands", "Macao") or e.get("chinaFlag"):
            add(e.get("name") or "?", "edgar_20f",
                {"incorporation": desc, "tickers": e.get("tickers")})
    for nm in CN_ROSTER:
        add(nm, "cn_roster_census")
    print(f"master companies (union): {len(master)}")

    # ---------- corpus token frequency (IDF) ----------
    lca_tok_freq: Counter = Counter()
    for e in lca_rows:
        for t in set(tokens(e["employer"])):
            if t and t not in GENERIC:
                lca_tok_freq[t] += 1
    ats_tok_freq: Counter = Counter()
    for b in boards:
        for t in set(tokens(b["company"])):
            if t and t not in GENERIC:
                ats_tok_freq[t] += 1

    lca_tok: dict[str, list[int]] = {}
    for i, e in enumerate(lca_rows):
        for t in set(tokens(e["employer"])):
            lca_tok.setdefault(t, []).append(i)
    ats_tok: dict[str, list[int]] = {}
    for i, b in enumerate(boards):
        for t in set(tokens(b["company"])):
            ats_tok.setdefault(t, []).append(i)

    def rare_in(corpus_freq: Counter, t: str) -> bool:
        return corpus_freq.get(t, 0) <= RARITY_CAP

    # top-150 US filers (the MNC-China-arm demotion evidence)
    top150 = {e["employer"] for e in lca_rows[:150]}

    # ---------- join ----------
    stats = Counter()
    for key, ent in master.items():
        toks = rare_candidates(ent["name"])
        # a token is matchable only if rare in BOTH corpora
        match_toks = {t for t in toks
                      if rare_in(lca_tok_freq, t) and rare_in(ats_tok_freq, t)}
        # lca side
        if match_toks:
            best = None
            for t in match_toks:
                for i in lca_tok.get(t, []):
                    e = lca_rows[i]
                    etoks = name_tokens(e["employer"])
                    if match_toks <= etoks:
                        if best is None or e["filings"] > lca_rows[best]["filings"]:
                            best = i
            if best is not None:
                e = lca_rows[best]
                ent["lca"] = {"employer": e["employer"],
                              "filings": e["filings"],
                              "states": list(e["states"].keys())[:6],
                              "median_wage": e.get("median_wage"),
                              "naics": e.get("naics")}
                stats["lca"] += 1
                if e["employer"] in top150 and "CHINA" in name_tokens(ent["name"]):
                    ent["flags"].append("suspect_mnc_arm")
                    stats["mnc_arm"] += 1
        # ats side
        if match_toks:
            hits = {}
            for t in match_toks:
                for i in ats_tok.get(t, []):
                    b = boards[i]
                    if match_toks <= name_tokens(b["company"]):
                        hits[i] = b
            for i, b in sorted(hits.items(),
                               key=lambda kv: -kv[1]["job_count"])[:4]:
                ent["ats"].append({"platform": b["platform"],
                                   "slug": b["slug"],
                                   "company": b["company"],
                                   "job_count": b["job_count"],
                                   "board_url": b["board_url"]})
            if hits:
                stats["ats"] += 1
    print("join stats:", dict(stats))

    # ---------- write ----------
    rows = sorted(
        master.values(),
        key=lambda x: -(x["lca"]["filings"] if x["lca"] else 0))
    payload = {"built": time.time(),
               "counts": {"master": len(rows),
                          "lca_evidence": stats["lca"],
                          "ats_board": stats["ats"],
                          "mnc_arm_suspect": stats["mnc_arm"]},
               "axes": {"wiki_category": wiki_cat["count"],
                        "wiki_notable": wiki_notable["count"],
                        "edgar_20f_fil": edgar["filers"],
                        "lca_employers": lca["unique_employers"],
                        "ats_boards": len(boards)},
               "companies": rows}
    (OUT / "master_census.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False))
    print("wrote master_census.json:",
          (OUT / "master_census.json").stat().st_size, "bytes")

    clean = [r for r in rows if "suspect_mnc_arm" not in r["flags"]]
    print("\nTOP 40 by LCA filings (mnc-arm suspects removed):")
    for r in clean[:40]:
        l = r["lca"]
        a = r["ats"][0] if r["ats"] else None
        print(f"  {l['filings'] if l else 0:>4} | {r['name'][:32]:32s} | "
              f"{(l['employer'][:36] if l else ''):36s} | "
              f"{(a['platform'] + ':' + (a['slug'] or '')[:20]) if a else ''}")


if __name__ == "__main__":
    main()
