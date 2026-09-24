#!/usr/bin/env python3
"""S16 in-flight check: netflix convergence verdict.

Read-only: fetches the live netflix global board (list only, no details),
then cross-references:
  - watch state (353 active rows)
  - enrichment feed (country verdicts + token-fallback classes)
Answers: is the current US board == 353, or are rows missing/unclassified?
"""
import json, sys
from pathlib import Path

ROOT = Path("/home/z/my-project/job-sourcing-research")
sys.path.insert(0, str(ROOT / "ingest"))
sys.path.insert(0, str(ROOT / "ingest" / "jobsearch"))

from jobsearch.sources.workday import list_board

LABEL = "netflix_us_fulltime"
WATCH = ROOT / "ingest" / "data" / "board_watch"

rows, meta = list_board("netflix|wd108|Netflix", progress_every=200,
                       time_type="Full time", client_filter=False)
print(f"live global board: {len(rows)} rows | complete={meta.get('complete')} "
      f"| total={meta.get('total')}")

# state + feed
state = set()
for l in (WATCH / f"{LABEL}.state.jsonl").read_text().split("\n"):
    if l.strip():
        state.add(json.loads(l)["reqId"])
feed = {}
for l in (WATCH / f"{LABEL}.newposts.jsonl").read_text().split("\n"):
    if l.strip():
        r = json.loads(l)
        feed[r["reqId"]] = r  # last record wins

cur_rids = set(rows.keys())
print(f"state rows: {len(state)} | current board: {len(cur_rids)}")
in_state = cur_rids & state
print(f"current∩state (active US): {len(in_state)}")

not_in_state = cur_rids - state
print(f"\ncurrent NOT in state: {len(not_in_state)}")
fb_us = fb_foreign = fb_none = fb_err = 0
missing_sample = []
for rid in sorted(not_in_state):
    fr = feed.get(rid)
    if fr is None:
        fb_none += 1
        missing_sample.append((rid, "NO FEED RECORD"))
        continue
    c = fr.get("country")
    if fr.get("error"):
        fb_err += 1
        missing_sample.append((rid, f"ERROR {fr.get('error')} attempts={fr.get('attempts')}"))
    elif c in (None, "", "NONE"):
        fb_none += 1
        missing_sample.append((rid, f"country=NONE title={fr.get('title','')[:50]} loc={fr.get('locationsText','')[:40]}"))
    elif c == "united states of america":
        fb_us += 1  # US verdict but not in state = BUG
        missing_sample.append((rid, f"US-VERDICT-NOT-IN-STATE {fr.get('title','')[:40]}"))
    else:
        fb_foreign += 1
print(f"  breakdown: foreign-verdict={fb_foreign} US-verdict={fb_us} "
      f"country-NONE={fb_none} error={fb_err}")
for m in missing_sample[:25]:
    print("   ", m)

# also: state rows no longer on the board (departed)
departed = state - cur_rids
print(f"\nstate rows NOT on current board (departed/stale): {len(departed)}")



