#!/usr/bin/env python3
"""s27_origin_recalibrate.py — D-S27-1 origin-criteria roster recalibration.

The user's S27 criteria (verbatim):
  "founder of chinese descent means nothing i don't care, it should be a
   china-based company or one that will be biased towards hiring chinese
   because the operation heavily gravitate towards china / chinese owned /
   chinese operated."

Qualifying origin classes: china_hq | cn_owned | china_ops |
china_remote_workforce. us_only (Chinese-descent founders alone) = OUT.

Supersedes D-S25-1 (the loosened bar that wired on founder evidence).

What this script does (idempotent, --apply to execute, default = dry-run):
  1. Removes the us_only verdict boards from the watch config.
  2. Deletes their CSVs + linked h1b_lca.csv views (bundle loses the
     company; state files + .h1b_lca.jsonl extracts stay archived).
  3. Strips their needles from h1b-extract.yml.
  4. Writes the verdict ledger ingest/data/ats_seed/s27_recalibration.jsonl
     (company, verdict, class, citation) and surgically updates the
     s25_census companies.jsonl records (origin_verdict fields).

Evidence base: origin_research_s27_{a,b1,b2}.md (live-researched
2026-10-07, sub-agents S27-R1/R3/R4) + the S24 origin research
(origin_research_b1/b2.md — the 2026-10-02 wave covering the S23
origin_review class).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCH_DIR = ROOT / "ingest/data/board_watch"
WORKDAY = ROOT / "ingest/data/workday"
CENSUS = ROOT / "ingest/data/ats_seed/s25_census/companies.jsonl"
LEDGER = ROOT / "ingest/data/ats_seed/s27_recalibration.jsonl"
H1B_YML = ROOT / ".github/workflows/h1b-extract.yml"

# ── verdict table (label, company, class, citation) ───────────────────────
# KEEP verdicts are recorded too (the ledger IS the finalized roster
# adjudication). Reference anchors = the S15 pilot/comp-comparison boards
# (origin criteria do not apply; kept deliberately).
REMOVALS: list[tuple[str, str, str]] = [
    # (label, company, evidence citation)
    ("heygen_us_fulltime", "HeyGen",
     "s27_a: CN entity 诗云科技（深圳） deregistered 2023-12; CN VCs forced out"),
    ("tigergraph_us_fulltime", "TigerGraph",
     "s27_a: CN WFOE 维加星信息科技（上海） deregistered; Cuadrilla US PE acquisition 2025-07"),
    ("plusai_us_fulltime", "PlusAI",
     "s27_a: China ops sold to 满帮 Manbang 2025-07; US-only SPAC path"),
    ("wyze_us_fulltime", "Wyze",
     "s27_b1: US company Kirkland WA, no CN entity; ODM mfg migrating to Vietnam; founder descent only (gray)"),
    ("grubmarket_us_fulltime", "GrubMarket",
     "s27_b2: all roles SF, no operating CN entity (qcc matches = deregistered shells)"),
    ("uniuni_us_fulltime", "UniUni",
     "s27_b2: NA-only ops; founder naturalized-Canadian; zero mainland footprint (gray)"),
    ("creatify_us_fulltime", "Creatify",
     "s27_b2: V2EX RMB rounds 2024-historic (last_touched 2024-06-12); 5/5 roles Mountain View (gray)"),
    ("fireworks_us_fulltime", "Fireworks AI",
     "S24 b1: no China ops found; LCA filings all CA/NY/WA; founder-origin only"),
    ("togetherai_us_fulltime", "Together AI",
     "S24 b1: 1 office (SF); no mainland entity/office/R&D"),
    ("cognition_us_fulltime", "Cognition",
     "S24 b1: American company SF HQ; founders US-based; no mainland entity"),
    ("genspark_us_fulltime", "Genspark",
     "S24 b1: Palo Alto + Singapore + Tokyo; no mainland entity (mainfunc.cn = Xi'an homonym)"),
    ("hyperbolic_us_fulltime", "Hyperbolic",
     "S24 b1: SF-inc; CEO/CTO US-based; zero China-team results"),
    ("sundayrobotics_us_fulltime", "Sunday Robotics",
     "S24 b1: Redwood City CA; Stanford founders; no mainland entity"),
    ("pika_us_fulltime", "Pika",
     "D-S25 loose bar only: US-inc Palo Alto; no China-ops evidence on file"),
    ("worldlabs_us_fulltime", "World Labs",
     "D-S25 loose bar only: US-inc SF; no China-ops evidence on file"),
    ("gptzero_us_fulltime", "GPTZero",
     "D-S25 loose bar only: NYC; no China-ops evidence on file"),
    ("llamaindex_us_fulltime", "LlamaIndex",
     "D-S25 loose bar only: SF remote-first; no China-ops evidence on file"),
    ("onyx_us_fulltime", "Onyx (Danswer)",
     "D-S25 loose bar only: SF; no China-ops evidence on file"),
    ("opusclip_us_fulltime", "OpusClip",
     "D-S25 loose bar only: Mountain View; no China-ops evidence on file"),
    ("retellai_us_fulltime", "Retell AI",
     "D-S25 loose bar only: SF; no China-ops evidence on file"),
    ("otterai_us_fulltime", "Otter.ai",
     "D-S25 loose bar only: Mountain View; US operations; founder descent only"),
]

KEEPS: list[tuple[str, str, str, str]] = [
    # (label, company, class, citation) — the gray-zone boards that PASSED
    ("alluxio_us_fulltime", "Alluxio", "china_ops",
     "s27_a: current active China operation (Beijing entity/team)"),
    ("risingwave_us_fulltime", "RisingWave", "china_ops",
     "s27_a: China entity/team current; CN hiring rounds"),
    ("streamnative_us_fulltime", "StreamNative", "china_ops",
     "s27_a: China entity/team current"),
    ("pingcap_us_fulltime", "PingCAP", "china_ops",
     "s27_a: Beijing entity 平凯科技 + CN team current"),
    ("airwallex_us_fulltime", "Airwallex", "china_ops",
     "s27_a: current active China operation (Shanghai hub)"),
    ("lightelligence_us_fulltime", "Lightelligence", "china_hq",
     "s27_a: 曦智科技 Shanghai parent, HKEX-listed 2026-04"),
    ("okx_us_fulltime", "OKX", "cn_owned",
     "s27_b1: PRC-national founder Star Xu controls OKX Group (ICE stake minority)"),
    ("binance_us_fulltime", "Binance", "cn_owned",
     "s27_b1: CZ majority owner; PRC-national co-founder Yi He co-CEO; 48/302 roles require Mandarin"),
    ("webull_us_fulltime", "Webull", "cn_owned",
     "s27_b1: 20-F — Wang Anquan 79.2% voting power; Hunan Weibu 863 employees = 62% of workforce"),
    ("ambergroup_us_fulltime", "Amber Group", "cn_owned",
     "s27_b1: founder Michael Wu 91.9% voting power; HK-founded 2017 (HK = China-based)"),
    ("weee_us_fulltime", "Weee!", "china_ops",
     "s27_b2: Shanghai WFOE 赛潍 100% Weee-owned, 251 insured CN employees (2025年报)"),
    ("dify_us_fulltime", "Dify (LangGenius)", "china_ops",
     "s27_b2 re-verified: 苏州语灵 在业, 72 参保; 4/4 live roles Suzhou"),
    ("zilliz_us_fulltime", "Zilliz", "china_ops",
     "S24 b1/b2 pass verdict (major China ops)"),
    ("qcraft_us_fulltime", "QCraft", "china_ops",
     "S24 b2 pass verdict"),
    ("riotgames_us_fulltime", "Riot Games", "cn_owned",
     "Tencent 100% subsidiary"),
    ("gea_us_fulltime", "GE Appliances", "cn_owned",
     "Haier-owned"),
    ("amersports_us_fulltime", "Amer Sports", "cn_owned",
     "ANTA-controlled consortium"),
    ("mandarinoriental_us_fulltime", "Mandarin Oriental", "china_hq",
     "HK-based (China territory); Asia-centric ops"),
    ("canadiansolar_us_fulltime", "Canadian Solar", "china_ops",
     "阿特斯: massive CN manufacturing base"),
    ("webull_note", "webull", "cn_owned", ""),  # marker removed below if unused
]

REFERENCE_ANCHORS = [
    ("nvidia_us_fulltime", "NVIDIA (S15 pilot / H-1B comp anchor)"),
    ("netflix_us_fulltime", "Netflix (H-1B comp anchor)"),
    ("openai_us_fulltime", "OpenAI (H-1B comp anchor)"),
    ("anthropic_us_fulltime", "Anthropic (H-1B comp anchor)"),
]


def load_config() -> dict:
    return json.loads((WATCH_DIR / "config.json").read_text(encoding="utf-8"))


def dry_run_report(cfg: dict) -> None:
    labels = {w["label"] for w in cfg["watches"]}
    print(f"config watches: {len(labels)}")
    missing = [lbl for lbl, _, _ in REMOVALS if lbl not in labels]
    print(f"removals: {len(REMOVALS)} (missing from config: {missing})")
    for lbl, company, cite in REMOVALS:
        csv = WORKDAY / f"{lbl}.csv"
        print(f"  - {lbl:32s} csv={'Y' if csv.exists() else 'n'}  {company}: {cite[:70]}")
    print(f"keeps recorded: {sum(1 for k in KEEPS if k[0] != 'webull_note')}")
    print(f"reference anchors: {len(REFERENCE_ANCHORS)}")
    print("DRY RUN — pass --apply to execute")


def apply(cfg: dict) -> int:
    labels = {w["label"] for w in cfg["watches"]}
    removed = []
    for lbl, _, _ in REMOVALS:
        if lbl in labels:
            labels.discard(lbl)
            removed.append(lbl)
    cfg["watches"] = [w for w in cfg["watches"]
                      if w["label"] not in {r for r, _, _ in REMOVALS}]
    (WATCH_DIR / "config.json").write_text(
        json.dumps(cfg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[config] removed {len(removed)} watches -> "
          f"{len(cfg['watches'])} remain")

    # 2. delete CSVs + linked h1b views (state + raw extracts stay)
    n_csv = 0
    for lbl in removed:
        for suffix in (".csv", ".h1b_lca.csv"):
            p = WORKDAY / f"{lbl}{suffix}"
            if p.exists():
                p.unlink()
                n_csv += 1
    print(f"[workday] deleted {n_csv} CSV artifacts (state + extracts archived)")

    # 3. strip needles from h1b-extract.yml
    yml = H1B_YML.read_text(encoding="utf-8")
    n_needle = 0
    for lbl in removed:
        # needles appear as "label:NEEDLES;" or "label:NEEDLES" at the tail
        pat = re.compile(rf"{lbl}:[^;\"']+;?")
        new = pat.sub("", yml, count=1)
        if new != yml:
            yml = new
            n_needle += 1
    H1B_YML.write_text(yml, encoding="utf-8")
    print(f"[h1b-extract.yml] stripped {n_needle} needle specs")

    # 4. ledger + census surgical update
    ledger = []
    for lbl, company, cite in REMOVALS:
        ledger.append({"label": lbl, "company": company,
                       "verdict": "us_only", "action": "removed",
                       "citation": cite,
                       "ts": "2026-10-07"})
    for lbl, company, klass, cite in KEEPS:
        if lbl == "webull_note":
            continue
        ledger.append({"label": lbl, "company": company,
                       "verdict": klass, "action": "kept",
                       "citation": cite, "ts": "2026-10-07"})
    for lbl, note in REFERENCE_ANCHORS:
        ledger.append({"label": lbl, "verdict": "reference_anchor",
                       "action": "kept", "citation": note,
                       "ts": "2026-10-07"})
    LEDGER.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in ledger) + "\n",
        encoding="utf-8")
    print(f"[ledger] wrote {len(ledger)} verdicts -> {LEDGER.name}")

    # census surgical update (only records present in s25_census)
    if CENSUS.exists():
        recs = []
        by_label_company = {(lbl, comp) for lbl, comp, _ in REMOVALS}
        removed_companies = {comp for _, comp, _ in REMOVALS}
        n_up = 0
        for line in CENSUS.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.strip().startswith("***"):
                recs.append(line)
                continue
            r = json.loads(line)
            if r.get("name") in removed_companies:
                r["origin_verdict_s27"] = "us_only"
                r["origin_verdict_s27_citation"] = next(
                    (c for _, comp, c in REMOVALS
                     if comp == r["name"]), "")
                n_up += 1
            recs.append(json.dumps(r, ensure_ascii=False))
        CENSUS.write_text("\n".join(recs) + "\n", encoding="utf-8")
        print(f"[census] updated {n_up} s25_census records")
    return 0


def main() -> int:
    apply_mode = "--apply" in sys.argv
    cfg = load_config()
    if not apply_mode:
        dry_run_report(cfg)
        return 0
    return apply(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
