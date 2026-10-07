#!/usr/bin/env python3
"""S28 follow-up: inspect blank-country rows + Kz/Pk ISO-code leaks."""
import json, collections

data = json.load(open("/home/z/pipeline-mirror/ingest/data/ui/jobs.json"))
rows = data if isinstance(data, list) else data.get("jobs", [])

print("== blank-country rows by company ==")
blank = [r for r in rows if not (r.get("country") or "").strip()]
print("count:", len(blank))
byc = collections.Counter(r.get("company", "?") for r in blank)
for k, v in byc.most_common():
    print(f"  {v:3} {k}")
print("\n== sample blank rows ==")
for r in blank[:12]:
    print(f"  - [{r.get('company')}] {str(r.get('title'))[:40]} | loc={str(r.get('primaryLocation'))[:50]} | remote={r.get('remoteFlag')}")

print("\n== Kz / Pk rows ==")
for r in rows:
    if (r.get("country") or "").strip() in ("Kz", "Pk"):
        print(f"  - [{r.get('company')}] {str(r.get('title'))[:40]} | loc={str(r.get('primaryLocation'))[:55]} | c={r.get('country')}")
