#!/usr/bin/env python3
"""S29 wire round: the live-verified board-surface sweep winners.

All three verified with title evidence (2026-10-09 live probes):
  - Reolink  greenhouse:reolink  73 postings — SG 27 / KL 17 / DE 6 /
    LA 2 / Jakarta … (HK/Shenzhen HQ, Singapore ops hub — the global
    network is the product; ALL rows flow under non_cn)
  - Sany     greenhouse:sany      3 postings — Heavy Equipment CDL
    Driver / Shop Technician (SANY America, Peachtree City GA class)
  - 1MORE    workable:1more       2 postings — Renewables Coordinator
    (Shenzhen audio; workable class, geo_scope)

Homonym guard applied: pokemoncareers (29 postings, Bellevue/
London/Redmond, merchandise roles) = The Pokémon Company
International — NOT Minisforum; rejected with evidence (again).
"""
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent.parent
CFG = HERE / "ingest/data/board_watch/config.json"
WF = HERE / ".github/workflows/h1b-extract.yml"

ENTRIES = [
    ("reolink_us_fulltime",
     "ats:greenhouse:reolink",
     "Reolink",
     ["Reolink", "Reolink US", "Shenzhen Reolink Technology"],
     ["United States", "Los Angeles, California, United States",
      "Singapore", "Kuala Lumpur, Malaysia"],
     "REOLINK"),
    ("sany_us_fulltime",
     "ats:greenhouse:sany",
     "SANY America",
     ["SANY America", "SANY", "Sany Heavy Industry"],
     ["United States", "Peachtree City, Georgia, United States"],
     "SANY AMERICA,SANY HEAVY INDUSTRY"),
    ("onemore_us_fulltime",
     "ats:workable:1more",
     "1MORE",
     ["1MORE", "1More USA", "ONE MORE"],
     ["United States"],
     "1MORE"),
]


def main() -> int:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    labels = {w["label"] for w in cfg["watches"]}
    added = 0
    for label, board, company, variants, slices, _needle in ENTRIES:
        if label in labels:
            print(f"[skip] {label} already present")
            continue
        entry = {
            "label": label, "board": board, "company": company,
            "country": "United States", "li_variants": variants,
            "slice_locations": slices,
        }
        if board.startswith(("ats:greenhouse:", "ats:ashby:",
                             "ats:lever:", "ats:workable:")):
            entry["geo_scope"] = "non_cn"      # D-S27-2/S28-2
        cfg["watches"].append(entry)
        added += 1
    CFG.write_text(json.dumps(cfg, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"[config] +{added} watches -> {len(cfg['watches'])} total")

    yml = WF.read_text(encoding="utf-8")
    n = 0
    for label, _, _, _, _, needle in ENTRIES:
        if f"{label}:" in yml:
            continue
        pat = re.compile(r'(python3 scripts/h1b_extract\.py \$Q '
                         r'--transport auto --multi\s+")([^"]+)(")')
        m = pat.search(yml)
        assert m, "needle insertion point not found"
        spec = f"{m.group(2)}{label}:{needle};"
        yml = (yml[:m.start()] + m.group(1) + spec + m.group(3)
               + yml[m.end():])
        n += 1
    WF.write_text(yml, encoding="utf-8")
    print(f"[h1b-extract.yml] +{n} needles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
