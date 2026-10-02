#!/usr/bin/env python3
"""S25 cn_hardware census — append records to seg_cn_hardware.jsonl (idempotent by name)."""
import json, sys
from pathlib import Path

OUT = Path("/home/z/research/ingest/data/ats_seed/s25_census/seg_cn_hardware.jsonl")

def rec(name, cn, aliases, hq, domain, careers_hint, ats_hint, us_signal, lca, origin,
        confidence="high", notes="", already_wired=False, status="active"):
    return {"name": name, "cn": cn, "aliases": aliases, "segment": "cn_hardware",
            "hq": hq, "domain": domain, "careers_hint": careers_hint,
            "ats_hint": ats_hint, "us_signal": us_signal, "lca": lca,
            "origin": origin, "already_wired": already_wired, "status": status,
            "confidence": confidence, "notes": notes}

RECORDS = json.loads(sys.stdin.read())
existing = {}
if OUT.exists():
    for line in OUT.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            existing[d["name"].lower()] = d
for r in RECORDS:
    existing[r["name"].lower()] = r  # later wins
with OUT.open("w") as f:
    for d in existing.values():
        f.write(json.dumps(d, ensure_ascii=False) + "\n")
print(f"wrote {len(existing)} records to {OUT}")
