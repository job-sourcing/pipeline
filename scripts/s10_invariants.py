#!/usr/bin/env python3
"""S10 invariant validation over the regenerated NVIDIA CSV v2.4.

Read-only checks (the S9 P0-fix proofs, re-run on the 09-17 data):
  INV-1  no duplicate linkedinUrl among matched rows (1:1 card service)
  INV-2  no title-tier row carries a job_req_id belonging to a DIFFERENT
         on-board requisition (foreign-reqId guard)
  INV-3  every title-tier match is verbatim (token-F1 >= 0.95 AND
         seniority equal) under the canonical predicate
  INV-4  every no_match req has a terminal titlesearch line (never an
         unprobed no_match)
  INV-5  matchMethod census consistency (reqId + title + multiset = matched)
  INV-6  CSV shape: 44 columns, firstSeenDate/lastResetDate coverage
"""
import csv
import json
import pathlib
import sys

sys.path.insert(0, "/home/z/my-project/job-sourcing-research/ingest")

from jobsearch.corroborate import verbatim_match  # canonical predicate

D = pathlib.Path("/home/z/my-project/job-sourcing-research/ingest/data/workday")
LABEL = "nvidia_us_fulltime"

rows = list(csv.DictReader((D / f"{LABEL}.csv").read_text(encoding="utf-8-sig").splitlines()))
print(f"rows: {len(rows)}")

matched = [r for r in rows if r["corroborationStatus"] == "matched"]
no_match = [r for r in rows if r["corroborationStatus"] == "no_match"]
not_checked = [r for r in rows if r["corroborationStatus"] == "not_checked"]
print(f"matched={len(matched)} no_match={len(no_match)} "
      f"not_checked={len(not_checked)} "
      f"coverage={len(matched)/len(rows)*100:.1f}%")

# INV-5: matchMethod census
from collections import Counter
mm = Counter(r["matchMethod"] for r in matched)
print(f"matchMethod census: {dict(mm)}")
assert sum(mm.values()) == len(matched), "INV-5 FAIL: census != matched"
assert all(r["matchMethod"] in ("reqId", "title", "titleMultiset")
           for r in matched), "INV-5 FAIL: unknown matchMethod"

# INV-1: duplicate URLs
urls = [r["linkedinUrl"] for r in matched if r["linkedinUrl"]]
dups = [u for u, n in Counter(urls).items() if n > 1]
print(f"INV-1 duplicate linkedinUrls: {len(dups)}")
assert not dups, f"INV-1 FAIL: {dups[:5]}"

# INV-2: foreign-reqId title rows (card's job_req_id points at another
# ON-BOARD req — read from the signal record via the URL id)
import re
sig = {}
for line in (D / f"{LABEL}.signals.jsonl").read_text().splitlines():
    if line.strip():
        s = json.loads(line)
        sig[str(s.get("linkedin_job_id"))] = s
board_ids = {r["reqId"] for r in rows}
foreign = []
for r in matched:
    if r["matchMethod"] in ("title", "titleMultiset"):
        m = re.search(r"/jobs/view/(\d+)", r["linkedinUrl"] or "")
        s = sig.get(m.group(1)) if m else None
        jr = (s.get("job_req_id") or "").strip() if s else ""
        if jr and jr != r["reqId"] and jr in board_ids:
            foreign.append((r["reqId"], jr))
print(f"INV-2 foreign-reqId title rows: {len(foreign)}")
assert not foreign, f"INV-2 FAIL: {foreign[:5]}"

# INV-3: verbatim predicate on every title match (needs the signals'
# original card title — sig loaded above, joined by card id)
bad_verbatim = []
import re
for r in matched:
    if r["matchMethod"] in ("title", "titleMultiset"):
        m = re.search(r"/jobs/view/(\d+)", r["linkedinUrl"] or "")
        s = sig.get(m.group(1)) if m else None
        if not s:
            bad_verbatim.append((r["reqId"], "no-signal-record"))
            continue
        ok = verbatim_match(r["title"], s.get("title") or "")
        if not ok:
            bad_verbatim.append((r["reqId"], f"F1-fail vs {s.get('title')!r}"))
print(f"INV-3 verbatim failures: {len(bad_verbatim)}")
assert not bad_verbatim, f"INV-3 FAIL: {bad_verbatim[:5]}"

# INV-4: every no_match req has a terminal titlesearch line
ts = {}
for line in (D / f"{LABEL}.title_search.jsonl").read_text().splitlines():
    if line.strip():
        t = json.loads(line)
        ts[t["reqId"]] = t
unprobed = [r["reqId"] for r in no_match if r["reqId"] not in ts]
print(f"INV-4 unprobed no_match: {len(unprobed)}")
assert not unprobed, f"INV-4 FAIL: {unprobed[:5]}"

# INV-6: shape + coverage
assert len(rows[0]) == 44, f"INV-6 FAIL: {len(rows[0])} columns"
fsd = sum(1 for r in rows if (r.get("firstSeenDate") or "").strip())
lrd = sum(1 for r in rows if (r.get("lastResetDate") or "").strip())
ac = Counter(r.get("applicantCensored") for r in rows)
print(f"INV-6 cols=44 firstSeenDate={fsd}/{len(rows)} "
      f"lastResetDate={lrd} applicantCensored={dict(ac)}")
assert fsd == len(rows), "INV-6 FAIL: firstSeenDate gaps"

print("\nALL INVARIANTS GREEN")
