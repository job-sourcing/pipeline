#!/usr/bin/env python3
"""s26_e2e_audit.py — the S26 end-to-end pipeline audit.

User directive: "ensure current companies are being refreshed daily and then
audit the whole process / pipeline e2e for any gaps or data issues."

Audits the FULL chain against LIVE data:
  census → watch config (129) → watch state (org, daily-refreshed)
        → chain CSVs (org, frozen-at-chain-time) → h1b banding
        → UI bundle (job-explorer checkout)

Sources of truth read:
  ORG      /home/z/pipeline-mirror   (the GHA runtime repo — freshest data;
                                      cloned fresh at audit time)
  ARCHIVE  /home/z/research          (this repo — census + config + spec)
  BUNDLE   /home/z/my-project/public/data  (the live UI data)

Checks (each prints a section; P0/P1/P2 severity where action is needed):
  1  coverage reconciliation (watches vs CSVs vs bundle companies)
  2  CSV staleness (git-log age per CSV, org repo)
  3  watch-state freshness (per-board last_seen — THE daily-refresh evidence)
  4  ghost rows (CSV reqIds the board has since dropped)
  5  duplicate reqIds inside each CSV
  6  unexported newposts (user-invisible new jobs — rows in newposts.jsonl
     that never made a CSV)
  7  h1b banding coverage (LCA extracts vs banded CSV rows)
  8  sanctions compliance (sanctioned brands must not be wired)
  9  remote-OK policy (include_remote boards actually carry remote rows)
  10 bundle consistency (rows/companies vs CSV ground truth)
  11 CSV column contract (49 columns, exact order, all files)
  12 quick column-quality scan (url/title/location null rates)

Output: audit/findings-s26-e2e.txt (append-friendly, evidence-first).
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

ORG = Path("/home/z/pipeline-mirror")
ARC = Path("/home/z/research")
BUNDLE = Path("/home/z/my-project/public/data")
OUT = ARC / "audit" / "findings-s26-e2e.txt"

TODAY = date.today()
NOW = datetime.now(timezone.utc)

CSV_HEADER_EXPECTED = [
    "reqId", "title", "company", "hiringOrg", "timeType", "postedOn",
    "startDate", "postingAgeDays", "primaryLocation", "nLocations",
    "locations", "remoteFlag", "stateCodes", "country", "questionnaireId",
    "similarJobsCount", "description", "descriptionLength", "url",
    "detailError", "linkedinUrl", "linkedinPostedDate", "numApplicants",
    "applicantLabel", "dateDeltaDays", "corroborationStatus", "matchMethod",
    "firstSeenDate", "corroboratedOn", "dumpDate", "endDate", "daysOnMarket",
    "daysOnMarketBasis", "censored", "repostCount", "lastResetDate",
    "applicationDeadline", "daysLeftToApply", "workerSubType",
    "jobFamilyGroup", "earliestEvidenceDate", "crossSourceRepostEvidence",
    "applicantCensored", "h1bFilings", "h1bWageP25", "h1bWageP50",
    "h1bWageP75", "h1bMatchBasis", "h1bMatchTitle",
]

lines: list[str] = []


def sec(title: str) -> None:
    lines.append("")
    lines.append("=" * 72)
    lines.append(title)
    lines.append("=" * 72)


def note(msg: str) -> None:
    lines.append(msg)
    print(msg)


def read_state(label: str, org: bool = True) -> dict[str, dict]:
    base = ORG if org else ARC
    p = base / "ingest/data/board_watch" / f"{label}.state.jsonl"
    out: dict[str, dict] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            d = json.loads(line)
            out[d["reqId"]] = d
        except Exception:
            pass
    return out


def read_newposts(label: str, org: bool = True) -> list[dict]:
    base = ORG if org else ARC
    p = base / "ingest/data/board_watch" / f"{label}.newposts.jsonl"
    rows: list[dict] = []
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def git_last_commit(repo: Path, path: str) -> str:
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%ci", "--", path],
            cwd=repo, capture_output=True, text=True, timeout=15)
        return r.stdout.strip()
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────
# 1. coverage reconciliation
# ─────────────────────────────────────────────────────────────────────────
sec("1. COVERAGE RECONCILIATION (watches → CSVs → bundle)")

cfg = json.loads((ARC / "ingest/data/board_watch/config.json").read_text())
watches = cfg["watches"]
labels = [w["label"] for w in watches]
w_by_label = {w["label"]: w for w in watches}
note(f"watch config: {len(labels)} watches "
     f"({len(set(labels))} unique — dupe labels would be a P0)")

org_csvs = {p.name.removesuffix(".csv"): p
            for p in (ORG / "ingest/data/workday").glob("*_us_fulltime.csv")}
arc_csvs = {p.name.removesuffix(".csv") for p in
            (ARC / "ingest/data/workday").glob("*_us_fulltime.csv")}

org_states = {p.name.removesuffix(".state.jsonl") for p in
              (ORG / "ingest/data/board_watch").glob("*.state.jsonl")}

idx = json.loads((BUNDLE / "index.json").read_text())
bundle_companies = {c["id"] if isinstance(c, dict) and "id" in c else c
                    for c in idx.get("companies", [])}
bundle_total = idx.get("totalRows")

no_csv = sorted(set(labels) - set(org_csvs))
no_state = sorted(set(labels) - org_states)
org_only_csvs = sorted(set(org_csvs) - arc_csvs)

note(f"CSVs on org runtime:  {len(org_csvs)}")
note(f"CSVs in archive:      {len(arc_csvs)}  "
     f"(back-sync hole: {len(org_only_csvs)} org-only)")
note(f"watch states on org:  {len(org_states)}  (missing: {no_state or 'none'})")
note(f"watches WITHOUT CSV:  {len(no_csv)} → {no_csv}")
note(f"bundle companies:     {len(bundle_companies)} rows={bundle_total}")
csv_companies = {c.removesuffix('_us_fulltime') for c in org_csvs}
bundle_vs_csv = sorted(csv_companies - bundle_companies)
note(f"CSV companies missing from bundle: {bundle_vs_csv or 'none'}")

if no_csv:
    lines.append(f"P1 — {len(no_csv)} watches have state but NO CSV anywhere: "
                 f"their US rows live only in newposts.jsonl (user-invisible).")
if org_only_csvs:
    lines.append(f"P1 — {len(org_only_csvs)} CSVs exist ONLY on the org repo "
                 f"(archive/source-of-truth incomplete; no GitLab//home/sync "
                 f"copy either).")

# ─────────────────────────────────────────────────────────────────────────
# 2. CSV staleness (org repo git-log)
# ─────────────────────────────────────────────────────────────────────────
sec("2. CSV STALENESS (org repo, last commit that touched each CSV)")

ages = []
for cid, p in sorted(org_csvs.items()):
    last = git_last_commit(ORG, f"ingest/data/workday/{p.name}")
    if not last:
        ages.append((999, cid, "never-committed"))
        continue
    dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S %z")
    ages.append(((NOW - dt).total_seconds() / 86400, cid,
                 dt.strftime("%m-%d %H:%MZ")))
buckets = Counter()
for a, cid, when in ages:
    buckets["0-1d" if a <= 1 else "1-2d" if a <= 2 else
            "2-7d" if a <= 7 else "7d+"] += 1
note(f"age buckets: {dict(buckets)}  (N={len(ages)} CSVs)")
stale = sorted([(a, cid) for a, cid, _ in ages if a > 1.0], reverse=True)
if stale:
    lines.append(f"P1 — {len(stale)} CSVs older than 24h "
                 f"(the daily watch does NOT rewrite CSVs — by design today, "
                 f"but that is the freshness gap):")
    for a, cid in stale[:40]:
        lines.append(f"    {a:5.1f}d  {cid}")
else:
    note("all CSVs touched within 24h")

# ─────────────────────────────────────────────────────────────────────────
# 3. watch-state freshness — the REAL daily-refresh evidence
# ─────────────────────────────────────────────────────────────────────────
sec("3. WATCH-STATE FRESHNESS (per-board max last_seen — refresh evidence)")

state_fresh = []
for lbl in labels:
    st = read_state(lbl, org=True)
    if not st:
        state_fresh.append((999, lbl, 0, "no-state"))
        continue
    mx = max((r.get("last_seen", "") for r in st.values()), default="")
    state_fresh.append((
        (TODAY - date.fromisoformat(mx)).days if mx else 999,
        lbl, len(st), mx))
fb = Counter(a for a, *_ in state_fresh)
note(f"last_seen age buckets (days): {dict(sorted(fb.items()))}")
not_fresh = sorted([x for x in state_fresh if x[0] > 1], reverse=True)
if not_fresh:
    lines.append(f"P2 — {len(not_fresh)} boards whose state last_seen > 1d "
                 f"(slow-drain feishu / backlog boards expected here; "
                 f"a healthy board refreshes its whole state daily):")
    for a, lbl, n, mx in not_fresh[:40]:
        lines.append(f"    {a:3d}d  {lbl:32} rows={n:4}  max_last_seen={mx}")
else:
    note("all 129 boards' states touched within 1 day — DAILY REFRESH OK")

# ─────────────────────────────────────────────────────────────────────────
# 4/5/6. per-CSV row integrity vs state
# ─────────────────────────────────────────────────────────────────────────
sec("4. GHOST ROWS · 5. DUPLICATE reqIds · 6. UNEXPORTED NEWPOSTS")

ghost_total = 0
dupe_total = 0
unexported_total = 0
ghost_detail: list[str] = []
dupe_detail: list[str] = []
unexp_detail: list[str] = []

for cid, p in sorted(org_csvs.items()):
    lbl = p.name.removesuffix(".csv")
    with open(p, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    ids = [r["reqId"] for r in rows]
    dupes = {i: c for i, c in Counter(ids).items() if c > 1}
    if dupes:
        dupe_total += sum(dupes.values()) - len(dupes)
        dupe_detail.append(f"    {lbl}: {dupes}")
    st = read_state(lbl)
    if st:
        st_max = max((date.fromisoformat(r.get("last_seen", "2000-01-01"))
                      for r in st.values()), default=date(2000, 1, 1))
        # ghost = CSV row whose reqId state last saw >2d before the board's
        # own freshest sighting (grace for slow drains)
        ghosts = [i for i in set(ids)
                  if i in st and st_max
                  and date.fromisoformat(
                      st[i].get("last_seen", "2000-01-01"))
                  < st_max - __import__("datetime").timedelta(days=2)]
        never_seen = [i for i in set(ids) if i not in st]
        if ghosts or never_seen:
            ghost_total += len(ghosts) + len(never_seen)
            ghost_detail.append(
                f"    {lbl}: {len(ghosts)} stale-in-state, "
                f"{len(never_seen)} not-in-state "
                f"(state rows={len(st)}, csv rows={len(rows)})")
    # unexported US rows: state membership ∉ CSV (the one-fetch contract
    # keeps foreign rows in the newposts FEED as audit — check #6 must
    # measure the state, not the feed)
    st = read_state(lbl)
    if st:
        csv_ids = set(ids)
        state_ids = set(st)
        new_ids = [rid for rid in state_ids if rid not in csv_ids]
        if new_ids:
            unexported_total += len(new_ids)
            unexp_detail.append(f"    {lbl}: {len(new_ids)} US state rows "
                                f"not in the CSV")

note(f"ghost rows total:  {ghost_total}")
lines.extend(ghost_detail[:30])
note(f"dupe reqId excess: {dupe_total}")
lines.extend(dupe_detail[:20])
note(f"UNEXPORTED US state rows (in state, not in CSV): {unexported_total}")
lines.extend(unexp_detail[:30])
if unexported_total:
    lines.append(f"P1 — {unexported_total} current US postings sit in the "
                 f"watch state but appear in NO CSV → invisible to the UI "
                 f"(the csv-export workflow closes this).")

# ─────────────────────────────────────────────────────────────────────────
# 7. h1b banding coverage
# ─────────────────────────────────────────────────────────────────────────
sec("7. H-1B BANDING COVERAGE")

lca_files = {p.name.removesuffix(".h1b_lca.jsonl"): p for p in
             (ORG / "ingest/data/workday").glob("*.h1b_lca.jsonl")}
note(f"LCA extract files on org: {len(lca_files)}")
gaps = []
for cid, p in sorted(org_csvs.items()):
    with open(p, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    banded = sum(1 for r in rows if (r.get("h1bFilings") or "").strip()
                 not in ("", "0"))
    lca_n = 0
    if cid in lca_files:
        lca_n = sum(1 for line in lca_files[cid].read_text(
            encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#"))
    if lca_n > 0 and banded == 0:
        gaps.append(f"    {cid}: {lca_n} LCA filings, 0 banded CSV rows")
    elif lca_n == 0 and banded > 0:
        gaps.append(f"    {cid}: {banded} banded rows but NO lca file "
                    f"(band from stale data?)")
note(f"banding anomalies: {len(gaps)}")
lines.extend(gaps[:30])

# ─────────────────────────────────────────────────────────────────────────
# 8. sanctions compliance
# ─────────────────────────────────────────────────────────────────────────
sec("8. SANCTIONS COMPLIANCE (wired boards vs sanctioned brands)")

q = json.loads((ARC / "ingest/data/ats_seed/s23_ai_census/"
                "sector_queue.json").read_text())
san_brands = {rec.get("name") or rec.get("brand")
              for rec in q.get("sanctioned_skip", [])}
restricted = {rec.get("name") or rec.get("brand")
              for rec in q.get("restricted_watch", [])}
note(f"sanctioned brands on file: {len(san_brands)}; "
     f"restricted_watch: {len(restricted)}")

company_names = {w["company"] for w in watches}
hit_san = sorted(c for c in company_names if c in san_brands)
hit_res = sorted(c for c in company_names if c in restricted)
note(f"wired companies matching sanctioned list: {hit_san or 'NONE — OK'}")
note(f"wired companies matching restricted list: {hit_res or 'none'} "
     f"(DJI-class = watch-only by policy)")
if hit_san:
    lines.append("P0 — SANCTIONED COMPANY IS WIRED — remove immediately")

# fuzzy double-check (brand substring in company name)
fuzzy = []
for c in company_names:
    for b in san_brands:
        if b and (b.lower() in c.lower() or c.lower() in b.lower()) \
                and len(b) > 3:
            fuzzy.append((c, b))
note(f"fuzzy matches (review): {fuzzy or 'none'}")

# ─────────────────────────────────────────────────────────────────────────
# 9. remote-OK policy
# ─────────────────────────────────────────────────────────────────────────
sec("9. REMOTE-OK POLICY (include_remote boards carry remote rows)")

for w in watches:
    if w.get("include_remote"):
        cid = w["label"].removesuffix("_us_fulltime")
        p = org_csvs.get(cid)
        if not p:
            note(f"    {cid}: include_remote=true but NO CSV")
            continue
        with open(p, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        remote = [r for r in rows if (r.get("remoteFlag") or "").lower()
                  == "true" or "remote" in (r.get("primaryLocation")
                                            or "").lower()]
        note(f"    {cid}: {len(rows)} CSV rows, {len(remote)} remote-flagged")

# ─────────────────────────────────────────────────────────────────────────
# 10. bundle consistency
# ─────────────────────────────────────────────────────────────────────────
sec("10. BUNDLE CONSISTENCY (live UI data vs CSV ground truth)")

jobs = json.loads((BUNDLE / "jobs.json").read_text())
if isinstance(jobs, dict):
    jobs = jobs.get("jobs", jobs.get("rows", []))
note(f"bundle jobs.json rows: {len(jobs)}  (index.json totalRows="
     f"{bundle_total})")
csv_rows_total = 0
per_csv_counts = {}
for cid, p in org_csvs.items():
    with open(p, newline="", encoding="utf-8-sig") as f:
        n = sum(1 for _ in csv.DictReader(f))
    per_csv_counts[cid] = n
    csv_rows_total += n
note(f"CSV rows total (org, ground truth): {csv_rows_total}")
delta = len(jobs) - csv_rows_total
note(f"bundle − CSV delta: {delta:+d} "
     f"(≠0 → bundle stale vs CSVs; expected ≠0 while CSVs keep moving)")
if abs(delta) > 50:
    lines.append(f"P2 — bundle is {abs(delta)} rows out of sync with the "
                 f"CSV ground truth — regenerate")

# ─────────────────────────────────────────────────────────────────────────
# 11. CSV column contract
# ─────────────────────────────────────────────────────────────────────────
sec("11. CSV COLUMN CONTRACT (49 columns, exact order)")

bad_contract = []
for cid, p in org_csvs.items():
    with open(p, newline="", encoding="utf-8-sig") as f:
        rdr = csv.reader(f)
        try:
            header = next(rdr)
        except StopIteration:
            bad_contract.append((cid, "EMPTY FILE"))
            continue
    if header != CSV_HEADER_EXPECTED:
        bad_contract.append((cid, f"{len(header)} cols"))
note(f"contract violations: {len(bad_contract)} / {len(org_csvs)}")
for cid, why in bad_contract[:20]:
    lines.append(f"    {cid}: {why}")
if bad_contract:
    lines.append("P1 — CSV contract violations (docs/csv-v2-spec.md)")

# ─────────────────────────────────────────────────────────────────────────
# 12. column quality quick scan
# ─────────────────────────────────────────────────────────────────────────
sec("12. COLUMN QUALITY (null rates on key columns)")

agg = Counter()
n_rows = 0
for cid, p in sorted(org_csvs.items()):
    with open(p, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            n_rows += 1
            for col in ("reqId", "title", "url", "primaryLocation",
                        "postedOn", "startDate"):
                if not (r.get(col) or "").strip():
                    agg[col] += 1
note(f"rows scanned: {n_rows}")
for col, n in sorted(agg.items()):
    pct = 100.0 * n / max(n_rows, 1)
    note(f"    {col:18} empty: {n:6} ({pct:.2f}%)")
    if col in ("reqId", "title", "url") and pct > 1:
        lines.append(f"P1 — {col} empty rate {pct:.2f}%")

# ─────────────────────────────────────────────────────────────────────────
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"\n[audit] written → {OUT} ({len(lines)} lines)")
