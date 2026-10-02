#!/usr/bin/env python3
"""S25 — wire the 9 go-back-and-collect boards (the loosened-bar wave).

Adds watch-config entries + h1b needle pairs (in the h1b-extract.yml
--multi string). Board identity + US-role counts live-probed
2026-10-02; LCA legal names verified against h1bdata.info with
homonyms excluded.
"""
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent.parent
CFG = HERE / "ingest/data/board_watch/config.json"
WF = HERE / ".github/workflows/h1b-extract.yml"

# label, board, company, li_variants, slice_locations
ENTRIES = [
    ("creatify_us_fulltime", "ats:ashby:creatify", "Creatify",
     ["Creatify", "Creatify AI"],
     ["United States", "Mountain View, California, United States"]),
    ("pika_us_fulltime", "ats:ashby:pika", "Pika",
     ["Pika", "Pika Labs"],
     ["United States", "Palo Alto, California, United States",
      "Remote, United States"]),
    ("worldlabs_us_fulltime", "ats:ashby:worldlabs", "World Labs",
     ["World Labs", "World Labs AI"],
     ["United States", "San Francisco, California, United States"]),
    ("fireworks_us_fulltime", "ats:ashby:fireworks", "Fireworks AI",
     ["Fireworks AI", "Fireworks"],
     ["United States", "San Mateo, California, United States",
      "San Francisco, California, United States",
      "Remote, United States"]),
    ("togetherai_us_fulltime", "ats:greenhouse:togetherai", "Together AI",
     ["Together AI", "Together"],
     ["United States", "San Francisco, California, United States"]),
    ("cognition_us_fulltime", "ats:ashby:cognition", "Cognition",
     ["Cognition", "Cognition Labs", "Cognition AI"],
     ["United States", "San Francisco, California, United States",
      "New York, New York, United States",
      "Washington, District of Columbia, United States"]),
    ("genspark_us_fulltime", "ats:ashby:genspark", "Genspark",
     ["Genspark", "MainFunc"],
     ["United States", "Palo Alto, California, United States",
      "New York, New York, United States"]),
    ("hyperbolic_us_fulltime", "ats:ashby:hyperbolic", "Hyperbolic",
     ["Hyperbolic", "Hyperbolic Labs"],
     ["United States", "San Francisco, California, United States",
      "Remote, United States"]),
    ("sundayrobotics_us_fulltime", "ats:ashby:sunday", "Sunday Robotics",
     ["Sunday Robotics", "Sunday"],
     ["United States", "Redwood City, California, United States"]),
]

# label, employers (comma-separated needles for the --multi string)
NEEDLES = [
    ("creatify_us_fulltime", "CREATIFY"),
    ("pika_us_fulltime", "PIKA LABS"),
    ("worldlabs_us_fulltime", "WORLD LABS"),
    ("fireworks_us_fulltime", "FIREWORKSAI,FIREWORKS.AI"),
    ("togetherai_us_fulltime", "TOGETHER COMPUTER,TOGETHER AI"),
    ("cognition_us_fulltime", "COGNITION AI"),
    ("genspark_us_fulltime", "GENSPARK,MAINFUNC"),
    ("hyperbolic_us_fulltime", "HYPERBOLIC LABS"),
    ("sundayrobotics_us_fulltime", "SUNDAY ROBOTICS"),
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
    # keep the file's label-sorted convention? config is append-order;
    # preserve as-is (watch driver iterates in order)
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
    # scrub any stray double-semicolon from legacy editing
    multi = multi.replace(";;", ";")
    text = text[:m.start(1)] + multi + text[m.end(1):]
    WF.write_text(text)
    print(f"h1b needles: +{len(NEEDLES)} (multi string now "
          f"{multi.count(';') + 1} specs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
