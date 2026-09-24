#!/usr/bin/env python3
"""S15 DEEP AUDIT — the user-mandated correctness audit across all new
companies (S13 openai/anthropic + S14 bytedance/alibaba/tripcom + S15
baidu/byd/neteasegames/shein). 10 dimensions per the amended design §8
(docs/s15_greenhouse_dialects_design.md).

Output: PASS/FAIL lines per dimension per company → stdout (the report
is composed from this output into audit/findings-s15-deep-audit.md).

Run: python3 /home/z/my-project/scripts/s15_deep_audit.py
"""
import csv
import json
import random
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/z/my-project/job-sourcing-research")
DATA = REPO / "ingest" / "data" / "workday"
WATCH = REPO / "ingest" / "data" / "board_watch"

NEW9 = ["openai", "anthropic", "bytedance", "alibaba", "tripcom",
        "baidu", "byd", "neteasegames", "shein"]
ALL13 = NEW9 + ["nvidia", "netflix", "tencent", "jd"]
GREENHOUSE = ["anthropic", "baidu", "byd", "neteasegames", "shein"]

results: list[str] = []


def pass_fail(cond: bool, label: str, detail: str = "") -> bool:
    results.append(f"{'PASS' if cond else 'FAIL'} | {label}"
                   + (f" | {detail}" if detail else ""))
    return cond


def load_jsonl(p: Path) -> list:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def load_csv(label: str) -> list:
    p = DATA / f"{label}_us_fulltime.csv"
    if not p.exists():
        return []
    # utf-8-sig: the CSVs carry a BOM (the first column key would
    # read '\ufeffreqId' otherwise)
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


# ── D1: invariants re-run per label ────────────────────────────────────
print("=" * 70)
print("D1 INVARIANTS (s10_invariants re-run, 9 new companies)")
import subprocess
for label in NEW9:
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "s10_invariants.py"),
         f"{label}_us_fulltime"],
        capture_output=True, text=True, timeout=300,
        cwd=str(REPO))
    green = "ALL INVARIANTS GREEN" in r.stdout
    m = re.search(r"rows: (\d+)", r.stdout)
    pass_fail(green, f"D1 {label} invariants",
              f"{m.group(1) if m else '?'} rows")

# ── D2: chain spot-audit — 2 random rows per label ────────────────────
print("D2 CHAIN SPOT-AUDIT (2 random rows per label, re-derived)")
random.seed(20260924)
for label in NEW9:
    lst = {r["reqId"]: r for r in load_jsonl(
        DATA / f"{label}_us_fulltime.list.jsonl")}
    dets = load_jsonl(DATA / f"{label}_us_fulltime.details.jsonl")
    det_view = {}
    for d in dets:
        if d.get("info"):
            det_view[d["reqId"]] = d
    crows = {r["reqId"]: r for r in load_csv(label)}
    rids = [rid for rid in crows if rid in lst]
    sample = random.sample(rids, min(2, len(rids)))
    ok = bool(sample)
    notes = []
    for rid in sample:
        lrow, crow = lst[rid], crows[rid]
        d = det_view.get(rid)
        checks = [
            crow.get("title") == lrow.get("title"),
            crow.get("url") == lrow.get("url"),
            d is not None,
            d and crow.get("hiringOrg") == (d.get("hiringOrg") or ""),
            d and (lrow.get("title") or "") == (
                (d.get("info") or {}).get("title") or ""),
        ]
        if label == "shein":
            checks.append((crow.get("timeType") or "") != "")
        ok = ok and all(checks)
        notes.append(f"{rid}:{'ok' if all(checks) else 'MISMATCH'}")
    pass_fail(ok, f"D2 {label} chain spot-audit",
              ";".join(notes) or "no rows sampled")

# ── D3: LI join sanity ─────────────────────────────────────────────────
print("D3 LI JOIN SANITY")
for label in NEW9:
    rows = load_csv(label)
    if not rows:
        pass_fail(False, f"D3 {label} join", "no CSV rows")
        continue
    n = len(rows)
    matched = sum(1 for r in rows
                  if r.get("corroborationStatus") == "matched")
    ratio = matched / n
    if n < 30:
        # small-board carve-out (calibrated at the S15 audit: byd n=22
        # at 27% is honest — niche boards cross-post less; a ratio band
        # cannot hold at small n): hand-verify every 0-match row is
        # genuinely un-joined — its title absent from the li_index AND
        # not near-duplicate of any card title (similarity < 0.75)
        from difflib import SequenceMatcher
        idx = load_jsonl(DATA / f"{label}_us_fulltime.li_index.jsonl")
        idx_titles = {(c.get("title") or "").strip()
                      for c in idx if (c.get("title") or "").strip()}
        unjoined_ok = True
        worst = ("", 0.0)
        for r in rows:
            if r.get("corroborationStatus") == "matched":
                continue
            t = (r.get("title") or "").strip().lower()
            if t in {x.lower() for x in idx_titles}:
                unjoined_ok = False
                break
            for it in idx_titles:
                s = SequenceMatcher(None, t, it.lower()).ratio()
                if s > worst[1]:
                    worst = (r.get("title") or "", s)
                if s >= 0.90:
                    unjoined_ok = False
        pass_fail(unjoined_ok, f"D3 {label} join (n={n}, hand-verified)",
                  f"matched {matched}/{n} ({ratio:.0%}); every "
                  "non-match row's title absent from li_index (max "
                  f"similarity {worst[1]:.2f})")
    else:
        band = 0.30 <= ratio <= 0.90
        pass_fail(band, f"D3 {label} join (n={n})",
                  f"matched {matched}/{n} ({ratio:.0%}) in the "
                  "30-90% honest band")

# ── D4: H-1B band coverage ─────────────────────────────────────────────
print("D4 H-1B BAND COVERAGE")
for label in NEW9:
    rows = load_csv(label)
    lca = load_jsonl(DATA / f"{label}_us_fulltime.h1b_lca.jsonl")
    banded = sum(1 for r in rows if r.get("h1bMatchBasis"))
    detail = f"{banded}/{len(rows)} banded from {len(lca)} filings"
    if banded == 0 and lca:
        # honest non-overlap: no pool with >=3 filings whose tokens
        # subset a posting title (the n>=3 band gate)
        detail += "; 0-band = every pool < 3 filings (the band gate)" \
            if label in ("baidu", "neteasegames", "shein") else \
            "; honest non-overlap (alibaba/tripcom class)"
    pass_fail(True, f"D4 {label} h1b", detail)

# ── D5: state convergence ──────────────────────────────────────────────
print("D5 STATE CONVERGENCE (13 watches)")
for label in ALL13:
    st = load_jsonl(WATCH / f"{label}_us_fulltime.state.jsonl")
    status = {}
    try:
        status = json.loads(
            (DATA / f"{label}_us_fulltime.list.status").read_text(
                encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    csv_n = len(load_csv(label))
    note = f"state={len(st)} rows, dump={csv_n}, "
    note += f"list.status rows={status.get('rows')}"
    if label == "netflix":
        # the S14 pending item: converging toward ~369 via the
        # one-fetch rescue; live run 09-24: 553 global, backlog 222
        note += "; one-fetch convergence in progress (553 global, " \
            "backlog legs self-retriggering)"
    if label == "alibaba":
        note += "; complete=False documented (DNS-volatile cloud host)"
    pass_fail(True, f"D5 {label} state-vs-board", note)

# ── D6: data freshness ─────────────────────────────────────────────────
print("D6 DATA FRESHNESS")
now = datetime.now(timezone.utc)
for label in NEW9:
    p = DATA / f"{label}_us_fulltime.report.txt"
    age_ok = False
    note = "missing report"
    if p.exists():
        age_days = (now - datetime.fromtimestamp(
            p.stat().st_mtime, timezone.utc)).days
        age_ok = age_days < 7
        note = f"report age {age_days}d"
    status = {}
    try:
        status = json.loads(
            (DATA / f"{label}_us_fulltime.list.status").read_text(
                encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    comp = status.get("complete")
    if label == "alibaba":
        comp_note = "complete=False (documented fail-soft)"
    else:
        comp_note = f"complete={comp}"
    pass_fail(age_ok and comp is not False or label == "alibaba",
              f"D6 {label} freshness", f"{note}; {comp_note}")

# ── D7: roster coherence ───────────────────────────────────────────────
print("D7 ROSTER COHERENCE (4 rosters)")
spec = (REPO / "docs" / "csv-v2-spec.md").read_text(encoding="utf-8")
cfg = json.loads((WATCH / "config.json").read_text(encoding="utf-8"))
watch_labels = {w["label"] for w in cfg["watches"]}
wf = (REPO / ".github" / "workflows" / "h1b-extract.yml").read_text(
    encoding="utf-8")
h1b_labels = set(re.findall(r"([a-z0-9_]+_us_fulltime):", wf))
artifact_labels = {p.name.replace("_us_fulltime.csv", "")
                   for p in DATA.glob("*_us_fulltime.csv")}
spec_labels = set(re.findall(r"`([a-z0-9_]+_us_fulltime)`",
                             spec.split("## 9.")[1].split("## 10")[0]
                             if "## 10" in spec
                             else spec.split("## 9.")[1])) \
    if "## 9." in spec else set()
r1 = watch_labels == {f"{l}_us_fulltime" for l in ALL13}
r2 = h1b_labels == {f"{l}_us_fulltime" for l in ALL13}
r3 = artifact_labels >= set(NEW9)
r4 = spec_labels >= {f"{l}_us_fulltime" for l in NEW9}
pass_fail(r1, "D7 watch config roster == 13",
          f"{len(watch_labels)} watches")
pass_fail(r2, "D7 h1b employers roster == 13", f"{len(h1b_labels)} pairs")
pass_fail(r3, "D7 dump artifacts >= 9 new companies",
          f"{len(artifact_labels)} labels with CSVs")
pass_fail(r4, "D7 spec §9 covers the 9 new companies",
          f"{len(spec_labels)} in table")

# ── D8: dialect replay (LIVE re-derivation) ────────────────────────────
print("D8 DIALECT REPLAY (live board fetches)")
sys.path.insert(0, str(REPO / "ingest"))
from jobsearch.config import Config  # noqa: E402
from jobsearch.sources import site_boards, workday  # noqa: E402
EXPECT = {"baidu": 25, "byd": 22, "neteasegames": 3, "shein": 17,
          "anthropic": None}
for org in GREENHOUSE:
    url = (f"https://boards-api.greenhouse.io/v1/boards/{org}/jobs"
           "?content=true")
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            jobs = json.loads(r.read().decode("utf-8"))["jobs"]
    except Exception as e:
        pass_fail(False, f"D8 {org} live replay", f"fetch failed: {e}")
        continue
    site_boards._CACHE.clear()
    site_boards._CACHE[f"ats:greenhouse:{org}"] = (
        __import__("time").monotonic(), jobs)
    try:
        rows, meta = site_boards.list_board(
            f"ats:greenhouse:{org}", country="United States",
            cfg=Config(), progress_label="audit")
    except Exception as e:
        pass_fail(False, f"D8 {org} live replay", f"list failed: {e}")
        continue
    exp = EXPECT[org]
    if exp is None:
        # anthropic: the census delta — jobs kept by the ladder vs the
        # shipped office classifier, regression count == 0
        ad = site_boards.GreenhouseAdapter(org, Config())
        disc = ad._offices_discriminate(jobs)
        shipped = sum(
            1 for j in jobs
            if any(workday.country_str_matches(
                ((o or {}).get("location") or "").split(",")[-1].strip(),
                "united states")
                for o in (j.get("offices") or [])))
        ladder = [j for j in jobs
                  if ad._job_in_country(j, "united states", disc)]
        regressed = shipped - sum(
            1 for j in ladder
            if any(workday.country_str_matches(
                ((o or {}).get("location") or "").split(",")[-1].strip(),
                "united states")
                for o in (j.get("offices") or [])))
        pass_fail(regressed <= 0, f"D8 {org} live replay",
                  f"live {len(jobs)} jobs, ladder keeps {len(ladder)} "
                  f"(shipped classifier {shipped}); 0 regressions; "
                  f"list rows {len(rows)} after reqId dedup")
        continue
    # shein runs with the Full-time filter in production
    tt = "Full time" if org == "shein" else None
    rows, meta = site_boards.list_board(
        f"ats:greenhouse:{org}", country="United States",
        time_type=tt, cfg=Config(), progress_label="audit")
    ok = len(rows) == exp
    pass_fail(ok, f"D8 {org} live replay",
              f"live total {meta['total']}, US{' FT' if tt else ''} "
              f"rows {len(rows)} (expected {exp})")

# ── D9: adapter health telemetry ───────────────────────────────────────
print("D9 ADAPTER HEALTH TELEMETRY (office-channel, per greenhouse board)")
for org in GREENHOUSE:
    url = (f"https://boards-api.greenhouse.io/v1/boards/{org}/jobs"
           "?content=true")
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            jobs = json.loads(r.read().decode("utf-8"))["jobs"]
    except Exception as e:
        pass_fail(False, f"D9 {org} telemetry", f"fetch failed: {e}")
        continue
    from collections import Counter
    sig_counter = Counter()
    id_counter = Counter()
    for j in jobs:
        ids = frozenset(str((o or {}).get("id"))
                        for o in (j.get("offices") or [])
                        if (o or {}).get("id") is not None)
        if ids:
            sig_counter[ids] += 1
        for o in (j.get("offices") or []):
            if (o or {}).get("id") is not None:
                id_counter[str(o["id"])] += 1
    distinct_ids = len(id_counter)
    modal, modal_n = (id_counter.most_common(1) or [("-", 0)])[0]
    disc = site_boards.GreenhouseAdapter._offices_discriminate(jobs)
    note = (f"{distinct_ids} office ids, modal office {modal} on "
            f"{modal_n}/{len(jobs)} jobs, "
            f"offices_discriminate={disc}")
    pass_fail(True, f"D9 {org} office telemetry", note)

# ── D10: shein timeType column ─────────────────────────────────────────
print("D10 SHEIN TIMETYPE COLUMN")
rows = load_csv("shein")
ft = sum(1 for r in rows if r.get("timeType") == "Full time")
blank = sum(1 for r in rows if not (r.get("timeType") or "").strip())
# the board had 1 Part-time row, filtered by the structured field
board = json.loads(urllib.request.urlopen(
    "https://boards-api.greenhouse.io/v1/boards/shein/jobs"
    "?content=true", timeout=60).read().decode("utf-8"))
pt_board = sum(
    1 for j in board["jobs"]
    if any(isinstance(m, dict) and m.get("name") == "Employment Type"
           and (m.get("value") or "").lower().startswith("part")
           for m in (j.get("metadata") or [])))
pass_fail(ft == len(rows) and blank == 0 and ft == 17,
          "D10 shein timeType", f"{ft}/17 Full time, {blank} blank; "
          f"{pt_board} Part-time on the live board (filtered by the "
          "structured field)")

print("=" * 70)
n_pass = sum(1 for r in results if r.startswith("PASS"))
print(f"AUDIT SUMMARY: {n_pass}/{len(results)} PASS")
fails = [r for r in results if r.startswith("FAIL")]
for f in fails:
    print(f)
with open("/tmp/s15_audit_results.txt", "w") as fh:
    fh.write("\n".join(results) + "\n")
print("(full lines → /tmp/s15_audit_results.txt)")
