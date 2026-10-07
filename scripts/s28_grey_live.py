#!/usr/bin/env python3
"""S28: the 9 grey census records — one live look each.

Evidence target per D-S27-1: is the company China-based, or do its
operations (hiring locations, team distribution) gravitate heavily
toward China? Founder descent alone counts for NOTHING.
Output: per-company hiring-location evidence → wire-or-out verdicts.
"""
import json
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

# ── 1. AFFiNE / ToEverything — smartrecruiters careers ──────────────
print("=== AFFiNE (smartrecruiters) ===")
st, body = fetch("https://careers.smartrecruiters.com/Affine?limit=100")
if st:
    # smartrecruiters renders client-side; their REST API:
    st2, body2 = fetch(
        "https://api.smartrecruiters.com/v1/companies/Affine/postings?limit=100")
    if st2:
        d = json.loads(body2)
        locs = {}
        for p in d.get("content", []):
            loc = (p.get("location") or {}).get("city", "?") + ", " + \
                  ((p.get("location") or {}).get("country", "?") or "?")
            locs[loc] = locs.get(loc, 0) + 1
        print(f"  postings: {d.get('totalFound')}")
        for k, v in sorted(locs.items(), key=lambda kv: -kv[1]):
            print(f"    {v:3} {k}")
    else:
        print("  API fail:", body2[:100])
else:
    print("  page fail:", str(body)[:100])

# ── 2. Alluxio — lever board ─────────────────────────────────────────
print("=== Alluxio (lever) ===")
st, body = fetch("https://api.lever.co/v0/postings/alluxio?mode=json")
if st:
    posts = json.loads(body)
    locs = {}
    for p in posts:
        cat = " | ".join(sorted(
            str(c).strip() for c in (p.get("categories") or {}).get("location", []) if c)) \
            if isinstance((p.get("categories") or {}).get("location"), list) \
            else str((p.get("categories") or {}).get("location", "?"))
        locs[cat] = locs.get(cat, 0) + 1
    print(f"  postings: {len(posts)}")
    for k, v in sorted(locs.items(), key=lambda kv: -kv[1]):
        print(f"    {v:3} {k}")
else:
    print("  fail:", str(body)[:100])

# ── 3. PingCAP — greenhouse board (already wired; re-flag check) ─────
print("=== PingCAP (greenhouse) ===")
st, body = fetch("https://boards-api.greenhouse.io/v1/boards/pingcap/jobs")
if st:
    d = json.loads(body)
    locs = {}
    for j in d.get("jobs", []):
        locs[j.get("location", {}).get("name", "?")] = \
            locs.get(j.get("location", {}).get("name", "?"), 0) + 1
    print(f"  postings: {len(d.get('jobs', []))}")
    for k, v in sorted(locs.items(), key=lambda kv: -kv[1]):
        print(f"    {v:3} {k}")
else:
    print("  fail:", str(body)[:100])

# ── 4. AutoMQ — careers page ─────────────────────────────────────────
print("=== AutoMQ (site) ===")
for u in ("https://www.automq.com/careers", "https://www.automq.com/join-us",
          "https://www.automq.com/about"):
    st, body = fetch(u)
    if st:
        txt = body
        hits = [w for w in ("Hangzhou", "Beijing", "Shanghai", "Shenzhen",
                            "Remote", "remote", "US", "Singapore", "中国", "杭州", "北京")
                if w in txt]
        print(f"  {u} -> {st}, geo-terms: {hits[:15]}")
        break
    else:
        print(f"  {u} -> {str(body)[:80]}")

# ── 5. Databend — careers page ───────────────────────────────────────
print("=== Databend (site) ===")
for u in ("https://databend.com/careers", "https://www.databend.com/careers",
          "https://databend.com/join-us", "https://www.databend.com/"):
    st, body = fetch(u)
    if st:
        hits = [w for w in ("Hangzhou", "Beijing", "Shanghai", "Remote",
                            "remote", "US", "Singapore", "中国", "杭州", "北京", "hiring")
                if w in body]
        print(f"  {u} -> {st}, geo-terms: {hits[:15]}")
        break
    else:
        print(f"  {u} -> {str(body)[:80]}")

# ── 6. RSS3 — careers ────────────────────────────────────────────────
print("=== RSS3 (site) ===")
for u in ("https://rss3.io/careers", "https://family.rss3.io/careers",
          "https://rss3.io/join"):
    st, body = fetch(u)
    if st:
        hits = [w for w in ("Hangzhou", "Beijing", "Shanghai", "Remote",
                            "remote", "Singapore", "中国", "杭州", "北京")
                if w in body]
        print(f"  {u} -> {st}, geo-terms: {hits[:15]}")
        break
    else:
        print(f"  {u} -> {str(body)[:80]}")

# ── 7. TabbyML — careers ─────────────────────────────────────────────
print("=== TabbyML (site) ===")
for u in ("https://tabbyml.com/careers", "https://tabbyml.com/join-us",
          "https://tabbyml.com/"):
    st, body = fetch(u)
    if st:
        hits = [w for w in ("Hangzhou", "Beijing", "Shanghai", "Remote",
                            "remote", "US", "中国", "杭州", "北京", "hiring", "jobs")
                if w in body]
        print(f"  {u} -> {st}, geo-terms: {hits[:15]}")
        break
    else:
        print(f"  {u} -> {str(body)[:80]}")
