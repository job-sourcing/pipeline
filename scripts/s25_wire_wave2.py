#!/usr/bin/env python3
"""S25 wave-2 — wire the 31 census wire_now boards that use EXISTING
adapter classes (ashby/greenhouse/lever/workable/rippling/feishuhire/
paylocity/workday). Notion/Scale AI/Databricks deliberately EXCLUDED
(one-Chinese-cofounder-of-many class → origin_review for the user).

Zai Lab / I-Mab (SmartRecruiters) + Recurrent (workday host unverified)
deferred: SmartRecruiters is a search source, not a board class — needs
the S26 RE wave. Canadian Solar wired with workday spec; the local chain
may fail on egress but the GHA watch (US egress) covers it.
"""
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent.parent
CFG = HERE / "ingest/data/board_watch/config.json"
WF = HERE / ".github/workflows/h1b-extract.yml"

# label, board, company, li_variants, slice_locations
ENTRIES = [
    ("airwallex_us_fulltime", "ats:ashby:airwallex", "Airwallex",
     ["Airwallex"],
     ["United States", "San Francisco, California, United States",
      "New York, New York, United States"]),
    ("gptzero_us_fulltime", "ats:ashby:gptzero", "GPTZero",
     ["GPTZero"],
     ["United States", "New York, New York, United States"]),
    ("llamaindex_us_fulltime", "ats:ashby:llamaindex", "LlamaIndex",
     ["LlamaIndex", "LlamaIndex AI"],
     ["United States", "San Francisco, California, United States"]),
    ("onyx_us_fulltime", "ats:ashby:onyx", "Onyx",
     ["Onyx", "Danswer"],
     ["United States", "San Francisco, California, United States"]),
    ("opusclip_us_fulltime", "ats:ashby:opusclip", "OpusClip",
     ["OpusClip", "Opus Clip"],
     ["United States", "Mountain View, California, United States"]),
    ("retellai_us_fulltime", "ats:ashby:retell-ai", "Retell AI",
     ["Retell AI", "Retell"],
     ["United States", "San Francisco, California, United States"]),
    ("zingage_us_fulltime", "ats:ashby:zingage", "Zingage",
     ["Zingage"],
     ["United States"]),
    ("heygen_us_fulltime", "ats:greenhouse:heygen", "HeyGen",
     ["HeyGen"],
     ["United States", "San Jose, California, United States",
      "Remote, United States"]),
    ("okx_us_fulltime", "ats:greenhouse:okx", "OKX",
     ["OKX", "OKCoin"],
     ["United States", "San Jose, California, United States",
      "New York, New York, United States"]),
    ("otterai_us_fulltime", "ats:greenhouse:otterai", "Otter.ai",
     ["Otter.ai", "Otter ai"],
     ["United States", "Mountain View, California, United States",
      "Remote, United States"]),
    ("pingcap_us_fulltime", "ats:greenhouse:pingcap", "PingCAP",
     ["PingCAP", "TiDB"],
     ["United States", "Remote, United States",
      "San Jose, California, United States"]),
    ("riotgames_us_fulltime", "ats:greenhouse:riotgames", "Riot Games",
     ["Riot Games"],
     ["United States", "Los Angeles, California, United States",
      "Seattle, Washington, United States"]),
    ("streamnative_us_fulltime", "ats:greenhouse:streamnative",
     "StreamNative",
     ["StreamNative"],
     ["United States", "Remote, United States"]),
    ("tigergraph_us_fulltime", "ats:greenhouse:tigergraph", "TigerGraph",
     ["TigerGraph"],
     ["United States", "Milpitas, California, United States",
      "Redwood City, California, United States"]),
    ("alluxio_us_fulltime", "ats:lever:alluxio", "Alluxio",
     ["Alluxio"],
     ["United States", "Foster City, California, United States",
      "San Mateo, California, United States"]),
    ("ambergroup_us_fulltime", "ats:lever:ambergroup", "Amber Group",
     ["Amber Group", "Amber"],
     ["United States"]),
    ("binance_us_fulltime", "ats:lever:binance", "Binance",
     ["Binance"],
     ["United States"]),
    ("funplus_us_fulltime", "ats:workable:fun-plus", "FunPlus",
     ["FunPlus", "Fun Plus"],
     ["United States"]),
    ("grubmarket_us_fulltime", "ats:workable:grubmarket", "GrubMarket",
     ["GrubMarket"],
     ["United States", "San Francisco, California, United States"]),
    ("risingwave_us_fulltime", "ats:workable:risingwave-labs",
     "RisingWave",
     ["RisingWave", "RisingWave Labs"],
     ["United States", "Remote, United States"]),
    ("autelrobotics_us_fulltime", "ats:workable:autel-robotics",
     "Autel Robotics",
     ["Autel Robotics", "Autel"],
     ["United States"]),
    ("zoomlion_us_fulltime", "ats:workable:zoomlion", "Zoomlion",
     ["Zoomlion", "Zoomlion Heavy Industry"],
     ["United States"]),
    ("wyze_us_fulltime", "ats:workable:wyze", "Wyze",
     ["Wyze", "Wyze Labs"],
     ["United States", "Kirkland, Washington, United States"]),
    ("celerdata_us_fulltime", "ats:rippling:celerdataopenroles",
     "CelerData",
     ["CelerData", "StarRocks", "PhoenixAI"],
     ["United States", "Remote, United States"]),
    ("uniuni_us_fulltime", "ats:rippling:uniuni", "UniUni",
     ["UniUni"],
     ["United States"]),
    ("ankerus_us_fulltime", "ats:feishuhire:anker-in", "Anker (US)",
     ["Anker", "Anker Innovations"],
     ["United States", "Seattle, Washington, United States",
      "Los Angeles, California, United States"]),
    ("bambulab_us_fulltime", "ats:feishuhire:bambulab", "Bambu Lab",
     ["Bambu Lab", "BambuLab"],
     ["United States", "Santa Clara, California, United States",
      "Austin, Texas, United States"]),
    ("ecoflowus_us_fulltime", "ats:feishuhire:ecoflow", "EcoFlow (US)",
     ["EcoFlow"],
     ["United States", "Irvine, California, United States"]),
    ("makeblockus_us_fulltime", "ats:feishuhire:makeblock",
     "Makeblock (US)",
     ["Makeblock", "xTool"],
     ["United States", "Mountain View, California, United States"]),
    ("mammotion_us_fulltime", "ats:feishuhire:mammotion", "Mammotion",
     ["Mammotion"],
     ["United States", "Chino, California, United States"]),
    ("mindrayus_us_fulltime",
     "ats:paylocity:de4f3c8e-1679-4dad-a21b-a3ebaf254893",
     "Mindray North America",
     ["Mindray", "Mindray North America"],
     ["United States", "Mahwah, New Jersey, United States",
      "Remote, United States"]),
    ("canadiansolar_us_fulltime", "canadiansolar|wd5|CanadianSolar",
     "Canadian Solar",
     ["Canadian Solar", "Recurrent Energy"],
     ["United States"]),
]

NEEDLES = [
    ("airwallex_us_fulltime", "AIRWALLEX US,AIRWALLEX"),
    ("gptzero_us_fulltime", "GPTZERO"),
    ("llamaindex_us_fulltime", "LLAMAINDEX"),
    ("onyx_us_fulltime", "DANSWER,ONYX"),
    ("opusclip_us_fulltime", "OPUSCLIP"),
    ("retellai_us_fulltime", "RETELL"),
    ("zingage_us_fulltime", "ZINGAGE"),
    ("heygen_us_fulltime", "HEYGEN"),
    ("okx_us_fulltime", "OKX,OKCOIN"),
    ("otterai_us_fulltime", "OTTERAI,OTTER.AI"),
    ("pingcap_us_fulltime", "PINGCAP"),
    ("riotgames_us_fulltime", "RIOT GAMES"),
    ("streamnative_us_fulltime", "STREAMNATIVE"),
    ("tigergraph_us_fulltime", "TIGERGRAPH"),
    ("alluxio_us_fulltime", "ALLUXIO"),
    ("ambergroup_us_fulltime", "AMBER GROUP"),
    ("binance_us_fulltime", "BINANCE"),
    ("funplus_us_fulltime", "FUNPLUS"),
    ("grubmarket_us_fulltime", "GRUBMARKET"),
    ("risingwave_us_fulltime", "RISINGWAVE"),
    ("autelrobotics_us_fulltime", "AUTEL ROBOTICS"),
    ("zoomlion_us_fulltime", "ZOOMLION"),
    ("wyze_us_fulltime", "WYZE LABS,WYZE"),
    ("celerdata_us_fulltime", "CELERDATA"),
    ("uniuni_us_fulltime", "UNIUNI"),
    ("ankerus_us_fulltime", "ANKER"),
    ("bambulab_us_fulltime", "BAMBULAB,BAMBU"),
    ("ecoflowus_us_fulltime", "ECOFLOW"),
    ("makeblockus_us_fulltime", "MAKEBLOCK"),
    ("mammotion_us_fulltime", "MAMMOTION"),
    ("mindrayus_us_fulltime", "MINDRAY DS USA,MINDRAY"),
    ("canadiansolar_us_fulltime", "CANADIAN SOLAR"),
]


def main() -> int:
    cfg = json.loads(CFG.read_text())
    watches = cfg["watches"]
    known = {w["label"] for w in watches}
    added = 0
    for label, board, company, li, slices in ENTRIES:
        if label in known:
            print(f"skip (exists): {label}")
            continue
        watches.append({
            "label": label,
            "board": board,
            "company": company,
            "country": "United States",
            "li_variants": li,
            "slice_locations": slices,
        })
        added += 1
    CFG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    print(f"watch config: +{added} entries (total {len(watches)})")

    text = WF.read_text()
    m = re.search(r'--multi\s+"([^"]+)"', text)
    if not m:
        raise SystemExit("h1b workflow --multi string not found")
    multi = m.group(1)
    for label, employers in NEEDLES:
        if label in multi:
            print(f"skip (needle exists): {label}")
            continue
        multi += f";{label}:{employers}"
    text = text[:m.start(1)] + multi + text[m.end(1):]
    WF.write_text(text)
    print(f"h1b needles: +{len(NEEDLES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
