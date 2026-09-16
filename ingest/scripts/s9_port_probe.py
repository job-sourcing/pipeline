#!/usr/bin/env python3
"""S9 PORT MIGRATION (design review finding 9): turn the completed
s9_title_probe.jsonl (read-only research probe, 770/770 reqs) into the
PRODUCTION titlesearch state:
  1. terminal probe lines -> {out}.title_search.jsonl (same schema)
  2. hit cards (dedup by id, sibling collisions collapse) appended to
     {out}.li_index.jsonl with source: "titleSearch"
  3. completion meta recomputed by the NEXT phase run (not written here)

Atomicity: state lines and cards are written in one script; a re-run is
idempotent (skips already-ported reqIds / already-appended card ids).
"""
import json, sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "data/workday/nvidia_us_fulltime"
PROBE = "data/workday/s9_title_probe.jsonl"
TS = f"{BASE}.title_search.jsonl"
IDX = f"{BASE}.li_index.jsonl"
TERMINAL = {"hit_new", "hit_indexed", "no_card"}

probe = [json.loads(l) for l in open(PROBE) if l.strip()]
print(f"probe lines: {len(probe)}")
stat = collections.Counter(r["status"] for r in probe)
print("probe statuses:", dict(stat))

# existing production state (idempotent re-run)
ported_reqs = set()
if os.path.exists(TS):
    for l in open(TS):
        if l.strip():
            ported_reqs.add(json.loads(l)["reqId"])
known_cards = set()
idx_lines = []
for l in open(IDX):
    if l.strip():
        c = json.loads(l)
        known_cards.add(str(c.get("id")))
        idx_lines.append(c)
print(f"li_index before: {len(idx_lines)} cards; ported reqs: "
      f"{len(ported_reqs)}")

new_state, new_cards = [], []
for r in probe:
    if r["reqId"] in ported_reqs or r["status"] not in TERMINAL:
        continue
    rec = {k: r[k] for k in ("reqId", "title", "primaryLocation",
                             "query_location", "status")}
    if r.get("hits"):
        rec["hits"] = r["hits"]
    new_state.append(rec)
    for h in r.get("hits", []) or r.get("new_cards", []):
        cid = str(h.get("id"))
        if cid in known_cards:
            continue
        card = {k: h.get(k) for k in
                ("id", "title", "company", "location", "date", "url")}
        # the probe recorded card fields inside hits; company may be
        # absent — default NVIDIA (the probe filtered company already)
        if card.get("company") is None:
            card["company"] = "NVIDIA"   # probe hits lack company (None ≠ absent — setdefault trap)
        card["source"] = "titleSearch"
        new_cards.append(card)
        known_cards.add(cid)

print(f"porting: {len(new_state)} state lines, {len(new_cards)} new cards")
with open(TS, "a", encoding="utf-8") as f:
    for rec in new_state:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
with open(IDX, "a", encoding="utf-8") as f:
    for c in new_cards:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")

total_state = sum(1 for _ in open(TS))
total_idx = sum(1 for _ in open(IDX))
print(f"AFTER: title_search state {total_state} lines, li_index "
      f"{total_idx} cards")
