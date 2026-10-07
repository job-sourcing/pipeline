#!/usr/bin/env python3
"""S28: audit the D-S27-2 foreign-row surge in the live bundle.

Reads the org mirror's ui bundle (jobs.json) + per-board CSVs and
answers:
  1. country facet distribution (is the surge real?)
  2. spot-check foreign rows for classification correctness (company,
     location sanity — e.g. no US-mislabeled rows)
  3. remote-flag distribution among foreign rows (D-S27-2: non-CN
     remote rows should flow)
"""
import json, csv, collections, sys

MIRROR = "/home/z/pipeline-mirror"

# 1. bundle facet distribution
idx = json.load(open(f"{MIRROR}/ingest/data/ui/index.json"))
facets = idx.get("facets", {})
country_facet = facets.get("countries") or {}
print("== bundle country facet ==")
for k, v in sorted(country_facet.items(), key=lambda kv: -kv[1]):
    print(f"  {v:6} {k}")

# 2. per-row audit from jobs.json (chunked file — try direct load first)
import os
jobs_path = f"{MIRROR}/ingest/data/ui/jobs.json"
size = os.path.getsize(jobs_path)
print(f"\njobs.json: {size/1e6:.1f} MB")
rows = []
if size < 200_000_000:
    data = json.load(open(jobs_path))
    rows = data if isinstance(data, list) else data.get("jobs", [])
else:
    # chunked: desc/ dir
    ddir = f"{MIRROR}/ingest/data/ui/desc"
    print("chunked bundle; scanning company JSONs in", ddir)

if rows:
    cc = collections.Counter(r.get("country") or "?" for r in rows)
    print("== row country counts ==")
    for k, v in cc.most_common(25):
        print(f"  {v:6} {k}")
    foreign = [r for r in rows if (r.get("country") or "").strip() not in
               ("", "United States", "US", "USA", "U.S.", "United States of America")]
    print(f"\nforeign rows: {len(foreign)}")
    rem = collections.Counter((r.get("remoteFlag") or r.get("remote") or "?") for r in foreign)
    print("  remoteFlag dist:", dict(rem))
    # spot check 12 foreign rows
    import random
    random.seed(28)
    for r in random.sample(foreign, min(12, len(foreign))):
        print(f"  - [{r.get('company','?')}] {str(r.get('title','?'))[:38]:38} | {str(r.get('primaryLocation', r.get('location','?')))[:44]:44} | c={r.get('country')} remote={r.get('remoteFlag')}")
