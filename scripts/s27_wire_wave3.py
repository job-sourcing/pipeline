#!/usr/bin/env python3
"""s27_wire_wave3.py — wire the 6 live-verified wave-3 boards (D-S27-1
census adjudication → probe evidence → live board verification).

Every board below was LIVE-VERIFIED on 2026-10-07 with the pipeline's
own adapter stack (see s27_adjudication.jsonl + the worklog):
  frontage     ats:adp:0fad767f…     95 US rows (Exton PA — the 方达
                                    医药 CRO's US arm)
  weichai      ats:adp:173c7f84…      6 US rows (Weichai America Corp)
  luyepharma   ats:greenhouse:…       4 US rows (Princeton NJ)
  laifen       ats:feishuhire:…       honest-0 US today (1 CN-local row
                                     — the board is real; future US
                                     roles surface via the watch)
  rokid        ats:feishuhire:…       honest-0 US (13 CN-local rows)
  petkit       ats:feishuhire:…       honest-0 US (54 rows, CN-local;
                                     Xuzhou is an unmapped city — noted)

Rejected homonym traps from the same probe wave (evidence recorded):
  aptura (ashby) = medical-AI company ≠ Aputure; cobot (ashby) =
  robotics ≠ Cobo; cubic (wd1) = Cubic Corp ≠ Anycubic; lifetime (wd1)
  = Lifetime Products ≠ LiTime; mymoose (wd1) ≠ API7; pokemoncareers
  ≠ Minisforum; databento ≠ Databend; pictsweet ≠ PicWish;
  JOINN paylocity webid format ≠ raw UUID (RE-wave).
"""
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent.parent
CFG = HERE / "ingest/data/board_watch/config.json"
WF = HERE / ".github/workflows/h1b-extract.yml"

# label, board, company, li_variants, slice_locations, needles
ENTRIES = [
    ("frontage_us_fulltime",
     "ats:adp:0fad767f-f389-40ea-b2d1-d8df05098476",
     "Frontage Laboratories",
     ["Frontage Laboratories", "Frontage Labs", "Frontage"],
     ["United States", "Exton, Pennsylvania, United States",
      "Hayward, California, United States"],
     "FRONTAGE LABORATORIES"),
    ("weichai_us_fulltime",
     "ats:adp:173c7f84-bab8-41af-85ea-7c3b3b163cd1",
     "Weichai America",
     ["Weichai America", "Weichai America Corp", "Weichai Power"],
     ["United States", "Rolling Meadows, Illinois, United States"],
     "WEICHAI AMERICA"),
    ("luyepharma_us_fulltime",
     "ats:greenhouse:luyepharmausaltd",
     "Luye Pharma",
     ["Luye Pharma", "Luye Pharma USA", "Luye Pharmaceutical"],
     ["United States", "Princeton, New Jersey, United States"],
     "LUYE PHARMA"),   # geo_scope=non_cn (greenhouse class) applied below
    ("laifen_us_fulltime",
     "ats:feishuhire:f1l5e2ythy",
     "Laifen",
     ["Laifen", "Laifen Technology", "Laifen US"],
     ["United States"],
     "LAIFEN"),
    ("rokid_us_fulltime",
     "ats:feishuhire:rokid-jungle",
     "Rokid",
     ["Rokid", "Rokid Inc", "Rokid US"],
     ["United States", "Palo Alto, California, United States"],
     "ROKID"),
    ("petkit_us_fulltime",
     "ats:feishuhire:jijiapets",
     "Petkit",
     ["Petkit", "PETKIT", "Petkit US"],
     ["United States"],
     "PETKIT"),
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
            entry["geo_scope"] = "non_cn"      # D-S27-2
        cfg["watches"].append(entry)
        added += 1
    CFG.write_text(json.dumps(cfg, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"[config] +{added} watches -> {len(cfg['watches'])} total")

    # h1b needles (single-pass extract; wrong guesses = honest-0)
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
        yml = yml[:m.start()] + m.group(1) + spec + m.group(3) \
            + yml[m.end():]
        n += 1
    WF.write_text(yml, encoding="utf-8")
    print(f"[h1b-extract.yml] +{n} needles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
