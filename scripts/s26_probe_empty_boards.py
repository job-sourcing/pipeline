#!/usr/bin/env python3
"""s26_probe_empty_boards.py — verify the 15 empty-state boards are
genuinely 0-US (not a filter artifact).

Uses the pipeline's own list_board (same code path as the watch) with
client_filter=False so we see the RAW rows, then applies the US
predicate manually to compare. Boards where raw rows contain US-token
locations but the watch reports 0 = filter bug (P0); boards with no US
rows anywhere = honest-0 (expected).
"""
import sys
sys.path.insert(0, "ingest")

from jobsearch.config import load_config             # noqa: E402
from jobsearch.sources import site_boards              # noqa: E402
from jobsearch.sources import workday          # noqa: E402

BOARDS = [
    ("tcl_us_fulltime", "ats:greenhouse:tcl", "United States"),
    ("insilico_us_fulltime", "custom:insilico", "United States"),
    ("qcraft_us_fulltime", "ats:workable:qcraft", "United States"),
    ("lightelligence_us_fulltime", "ats:workable:lightelligence", "United States"),
    ("dify_us_fulltime", "custom:dify", "United States"),
    ("zingage_us_fulltime", "ats:ashby:zingage", "United States"),
    ("pingcap_us_fulltime", "ats:greenhouse:pingcap", "United States"),
    ("streamnative_us_fulltime", "ats:greenhouse:streamnative", "United States"),
    ("ambergroup_us_fulltime", "ats:lever:ambergroup", "United States"),
    ("funplus_us_fulltime", "ats:workable:fun-plus", "United States"),
    ("grubmarket_us_fulltime", "ats:workable:grubmarket", "United States"),
    ("risingwave_us_fulltime", "ats:workable:risingwave-labs", "United States"),
    ("autelrobotics_us_fulltime", "ats:workable:autel-robotics", "United States"),
    ("zoomlion_us_fulltime", "ats:workable:zoomlion", "United States"),
    ("wyze_us_fulltime", "ats:workable:wyze", "United States"),
]

US_TOKENS = ("united states", "usa", ", us", "us-", "u.s.", "remote")


def row_us(r: dict) -> bool:
    txt = " ".join(str(v) for k, v in r.items()
                   if k in ("locationsText", "location", "locations",
                            "country", "primaryLocation", "city")).lower()
    return any(t in txt for t in US_TOKENS)


cfg = load_config()
print(f"{'label':36} {'rows':>5} {'us-raw':>6}  sample-locations")
for label, spec, country in BOARDS:
    try:
        rows, meta = site_boards.list_board(
            spec, country=country, time_type=None, cfg=cfg,
            sleep_s=0.2, progress_every=10**6, progress_label="probe",
            client_filter=False, include_remote=True)
        n = len(rows)
        us = sum(1 for r in rows.values() if row_us(r))
        samples = list(rows.values())[:3]
        locs = "; ".join(
            str(s.get("locationsText") or s.get("location") or s.get("city")
                or "?")[:40] for s in samples)
        flag = "  <-- FILTER MISMATCH?" if (us > 0) else ""
        print(f"{label:36} {n:>5} {us:>6}  {locs}{flag}")
    except Exception as exc:
        print(f"{label:36}  ERR {type(exc).__name__}: {str(exc)[:80]}")
