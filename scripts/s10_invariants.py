#!/usr/bin/env python3
"""S10-S12 invariant validation over a regenerated board CSV.

Read-only checks (the S9 P0-fix proofs, re-run per regen;
S11 added INV-7..INV-9 for the v2.5 quality round; S12 made the
label a CLI arg so every company's CSV gets the same proofs):
  INV-1  no duplicate linkedinUrl among matched rows (1:1 card service)
  INV-2  no title-tier row carries a job_req_id belonging to a DIFFERENT
         on-board requisition (foreign-reqId guard)
  INV-3  every title-tier match is verbatim (token-F1 >= 0.95 AND
         seniority equal) under the canonical predicate
  INV-4  every no_match req has a terminal titlesearch line (never an
         unprobed no_match)
  INV-5  matchMethod census consistency (reqId + title + multiset = matched)
  INV-6  CSV shape: 49 columns (v2.6), firstSeenDate/lastResetDate coverage
  INV-7  daysLeftToApply NEVER negative (elapsed floors ship "" — S11)
  INV-8  every non-empty questionnaireId has a linked questionnaires.csv
         row (join integrity — S11)
  INV-9  censored consistency: firstSeenDate < startDate ⇒ censored=true
         (measured repost resets — S11)
  INV-10 h1b wage-band integrity: banded rows carry all 4 stats + a
         basis; unbanded rows carry NONE (all-empty, never partial)

Usage: s10_invariants.py [label] (default nvidia_us_fulltime)
"""
import csv
import json
import pathlib
import sys

sys.path.insert(0, "/home/z/my-project/job-sourcing-research/ingest")

from jobsearch.corroborate import verbatim_match  # canonical predicate

D = pathlib.Path("/home/z/my-project/job-sourcing-research/ingest/data/workday")
LABEL = sys.argv[1] if len(sys.argv) > 1 else "nvidia_us_fulltime"

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

# INV-6: shape + coverage (v2.6: 48 columns — h1b bands appended)
assert len(rows[0]) == 49, f"INV-6 FAIL: {len(rows[0])} columns"
fsd = sum(1 for r in rows if (r.get("firstSeenDate") or "").strip())
lrd = sum(1 for r in rows if (r.get("lastResetDate") or "").strip())
ac = Counter(r.get("applicantCensored") for r in rows)
print(f"INV-6 cols=49 firstSeenDate={fsd}/{len(rows)} "
      f"lastResetDate={lrd} applicantCensored={dict(ac)}")
assert fsd == len(rows), "INV-6 FAIL: firstSeenDate gaps"

# INV-7 (S11): daysLeftToApply never negative — an elapsed "at least
# until" floor on a live posting means the window auto-extended;
# days-left ships UNKNOWN (""), never a misleading "closed N days ago"
neg = [r["reqId"] for r in rows
       if r.get("daysLeftToApply", "") not in ("",)
       and (r["daysLeftToApply"].lstrip("-").isdigit()
            and int(r["daysLeftToApply"]) < 0)]
print(f"INV-7 negative daysLeftToApply: {len(neg)}")
assert not neg, f"INV-7 FAIL: {neg[:5]}"

# INV-8 (S11): join integrity — every non-empty questionnaireId in the
# main CSV has at least one row in the linked questionnaires.csv
qcsv = D / f"{LABEL}.questionnaires.csv"
assert qcsv.exists(), "INV-8 FAIL: questionnaires.csv missing"
qrows = list(csv.DictReader(
    qcsv.read_text(encoding="utf-8-sig").splitlines()))
linked_ids = {q["questionnaireId"] for q in qrows}
csv_ids = {r["questionnaireId"] for r in rows if r["questionnaireId"]}
unlinked = sorted(csv_ids - linked_ids)
print(f"INV-8 questionnaire join: {len(csv_ids)} id(s) in CSV, "
      f"{len(linked_ids)} defined ({len(qrows)} questions); "
      f"unlinked={unlinked}")
assert not unlinked, f"INV-8 FAIL: ids without definitions: {unlinked[:5]}"

# INV-9 (S11): measured resets censor the age — firstSeenDate before
# startDate is impossible without a repost reset
bad_cens = [r["reqId"] for r in rows
            if (r.get("firstSeenDate") or "")[:10]
            < (r.get("startDate") or "")[:10]
            and r.get("censored") != "true"
            and r.get("startDate")]
print(f"INV-9 reset-evidence rows not censored: {len(bad_cens)}")
assert not bad_cens, f"INV-9 FAIL: {bad_cens[:5]}"

# INV-10 (S11/v2.6): h1b band integrity — a banded row carries ALL of
# filings/P25/P50/P75/basis; an unbanded row carries NONE (partial
# bands = a broken join, e.g. a filled median with an empty basis)
H1B_COLS = ("h1bFilings", "h1bWageP25", "h1bWageP50", "h1bWageP75",
            "h1bMatchBasis", "h1bMatchTitle")
bad_h1b = []
for r in rows:
    filled = [c for c in H1B_COLS if (r.get(c) or "") != ""]
    if 0 < len(filled) < 6:
        bad_h1b.append((r["reqId"], filled))
    if r.get("h1bMatchBasis") and r["h1bMatchBasis"] not in (
            "title+state", "title", "subset+state", "subset"):
        bad_h1b.append((r["reqId"], r["h1bMatchBasis"]))
    if r.get("h1bMatchBasis") and not r.get("h1bMatchTitle"):
        bad_h1b.append((r["reqId"], "banded-without-matchTitle"))
n_banded = sum(1 for r in rows if r.get("h1bMatchBasis"))
print(f"INV-10 h1b band integrity: {n_banded}/{len(rows)} banded, "
      f"{len(bad_h1b)} partial/invalid")
assert not bad_h1b, f"INV-10 FAIL: {bad_h1b[:5]}"

print("\nALL INVARIANTS GREEN")
