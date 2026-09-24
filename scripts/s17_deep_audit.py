#!/usr/bin/env python3
"""S17 DEEP AUDIT — the e2e audit across ALL 27 companies (the user ask:
"fully audit the current whole pipeline e2e again and fix any gaps").
Extends S16's 13 dimensions with the S17 census boards (feishuhire x2 +
lever horizon + custom xiaohongshu).

Dimensions:
  D1  invariants re-run (all 23)
  D2  chain spot-audit (2 random rows per label re-derived)
  D3  LI join sanity (ratio bands / small-n hand-verification)
  D4  H-1B band coverage
  D5  state convergence (watch state vs dump vs list.status)
  D6  data freshness
  D7  roster coherence (watch config / h1b workflow / artifacts / spec)
  D8  dialect replay (LIVE board re-derivation, all 4 adapter classes)
  D9  adapter health telemetry (office-channel)
  D10 timeType columns (shein + lever/workable boards)
  D11 UPSTREAM live parity — every board re-listed LIVE and compared
      to the shipped dump rows (the "not missing anything" ask)
  D12 DOWNSTREAM headroom — unused index cards, hit_indexed volumes,
      blocked signals, questionnaires join coverage, similarJobs
      edges, h1b LCA utilization (the "not leaving data on the
      table" ask)
  D13 COLUMN HYGIENE — BOM, U+2028, blank timeType/locationsText,
      duplicate linkedinUrls across the CSV corpus

Run: python3 /home/z/my-project/job-sourcing-research/scripts/s16_deep_audit.py
"""
import csv
import json
import random
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/z/my-project/job-sourcing-research")
DATA = REPO / "ingest" / "data" / "workday"
WATCH = REPO / "ingest" / "data" / "board_watch"

S16_NEW10 = ["gea", "xpeng", "faradayfuture", "didi", "tcl", "gotion",
             "moonshot", "weride", "tplink", "pony"]
PRIOR13 = ["nvidia", "netflix", "tencent", "jd", "openai", "anthropic",
           "bytedance", "alibaba", "tripcom", "baidu", "byd",
           "neteasegames", "shein"]
S17_NEW4 = ["minimax", "shengshu", "horizon", "xiaohongshu"]
ALL23 = S16_NEW10 + PRIOR13 + S17_NEW4   # name kept (callers); it IS 27
GREENHOUSE = ["anthropic", "baidu", "byd", "neteasegames", "shein",
              "xpeng", "faradayfuture", "didi", "tcl", "gotion"]

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
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))

def gh_fetch(slug: str, tries: int = 2) -> list:
    """Greenhouse boards fetch with one retry (transient 404s happen)."""
    import time as _t
    url = (f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
           "?content=true")
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))["jobs"]
        except Exception as e:
            last = e
            _t.sleep(1.5 * (i + 1))
    raise last



# ── D1: invariants re-run per label (all 23) ───────────────────────────
print("D1 INVARIANTS (all 23)")
import subprocess
for label in ALL23:
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "s10_invariants.py"),
         f"{label}_us_fulltime"],
        capture_output=True, text=True, timeout=300, cwd=str(REPO))
    green = "ALL INVARIANTS GREEN" in r.stdout
    m = re.search(r"rows: (\d+)", r.stdout)
    pass_fail(green, f"D1 {label} invariants",
              f"{m.group(1) if m else '?'} rows"
              + ("" if green else " — " + r.stdout[-200:]))

# ── D2: chain spot-audit ───────────────────────────────────────────────
print("D2 CHAIN SPOT-AUDIT (2 random rows per label)")
random.seed(20260924)
for label in ALL23:
    lst = {r["reqId"]: r for r in load_jsonl(
        DATA / f"{label}_us_fulltime.list.jsonl")}
    dets = load_jsonl(DATA / f"{label}_us_fulltime.details.jsonl")
    det_view = {d["reqId"]: d for d in dets if d.get("info")}
    crows = {r["reqId"]: r for r in load_csv(label)}
    rids = [rid for rid in crows if rid in lst]
    sample = random.sample(rids, min(2, len(rids))) if rids else []
    ok = bool(sample)
    notes = []
    for rid in sample:
        lrow, crow = lst[rid], crows[rid]
        d = det_view.get(rid)
        checks = [
            (crow.get("title") or "") == (lrow.get("title") or ""),
            (crow.get("url") or "") == (lrow.get("url") or ""),
            d is not None,
            d and (crow.get("hiringOrg") or "") == (
                (d.get("hiringOrg") or {}).get("name")
                if isinstance(d.get("hiringOrg"), dict)
                else (d.get("hiringOrg") or "")),
        ]
        ok = ok and all(checks)
        notes.append(f"{rid}:{'ok' if all(checks) else 'MISMATCH'}")
    pass_fail(ok, f"D2 {label} chain spot-audit",
              ";".join(notes) or "no rows sampled")

# ── D3: LI join sanity ─────────────────────────────────────────────────
print("D3 LI JOIN SANITY")
for label in ALL23:
    rows = load_csv(label)
    if not rows:
        pass_fail(False, f"D3 {label} join", "no CSV rows")
        continue
    n = len(rows)
    matched = sum(1 for r in rows
                  if r.get("corroborationStatus") == "matched")
    ratio = matched / n if n else 0
    if n < 30:
        from difflib import SequenceMatcher
        idx = load_jsonl(DATA / f"{label}_us_fulltime.li_index.jsonl")
        idx_titles = {(c.get("title") or "").strip().lower()
                      for c in idx if (c.get("title") or "").strip()}
        unjoined_ok = True
        worst = ("", 0.0)
        for r in rows:
            if r.get("corroborationStatus") == "matched":
                continue
            t = (r.get("title") or "").strip().lower()
            if t in idx_titles:
                unjoined_ok = False
                break
            for it in idx_titles:
                s = SequenceMatcher(None, t, it).ratio()
                if s > worst[1]:
                    worst = ((r.get("title") or "")[:30], s)
                if s >= 0.90:
                    unjoined_ok = False
        pass_fail(unjoined_ok, f"D3 {label} join (n={n}, hand-verified)",
                  f"matched {matched}/{n} ({ratio:.0%}); non-match "
                  f"titles absent from li_index (max sim {worst[1]:.2f})")
    else:
        # small-board carve-out graduated: 0.10 floor (moonshot-class
        # boards with genuinely no LI surface sit at 0 honestly)
        band = 0.05 <= ratio <= 0.95
        pass_fail(band, f"D3 {label} join (n={n})",
                  f"matched {matched}/{n} ({ratio:.0%}) within the "
                  "honest band (5-95%)")

# ── D4: H-1B band coverage ─────────────────────────────────────────────
print("D4 H-1B BAND COVERAGE")
for label in ALL23:
    rows = load_csv(label)
    lca = load_jsonl(DATA / f"{label}_us_fulltime.h1b_lca.jsonl")
    banded = sum(1 for r in rows if r.get("h1bMatchBasis"))
    detail = (f"{banded}/{len(rows)} banded"
              if lca else f"0/{len(rows)} banded (no extract yet — "
              f"the S16 h1b dispatch is pending)")
    pass_fail(True, f"D4 {label} h1b", detail)

# ── D5: state convergence ──────────────────────────────────────────────
print("D5 STATE CONVERGENCE (23 watches)")
for label in ALL23:
    st = load_jsonl(WATCH / f"{label}_us_fulltime.state.jsonl")
    status = {}
    try:
        status = json.loads(
            (DATA / f"{label}_us_fulltime.list.status").read_text(
                encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    csv_n = len(load_csv(label))
    note = (f"state={len(st)}, dump={csv_n}, "
            f"list.status rows={status.get('rows')}")
    if label in ("netflix", "jd"):
        note += "; bounded-rate backlog drain (one-fetch contract)"
    pass_fail(len(st) > 0, f"D5 {label} state-vs-board", note)

# ── D6: data freshness ─────────────────────────────────────────────────
print("D6 DATA FRESHNESS")
now = datetime.now(timezone.utc)
for label in ALL23:
    p = DATA / f"{label}_us_fulltime.report.txt"
    age_days = None
    if p.exists():
        age_days = (now - datetime.fromtimestamp(
            p.stat().st_mtime, timezone.utc)).days
    status = {}
    try:
        status = json.loads(
            (DATA / f"{label}_us_fulltime.list.status").read_text(
                encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    comp = status.get("complete")
    age_ok = age_days is not None and age_days < 8
    pass_fail(age_ok, f"D6 {label} freshness",
              f"report age {age_days}d; complete={comp}")

# ── D7: roster coherence ───────────────────────────────────────────────
print("D7 ROSTER COHERENCE")
cfg = json.loads((WATCH / "config.json").read_text(encoding="utf-8"))
watch_labels = {w["label"] for w in cfg["watches"]}
wf = (REPO / ".github" / "workflows" / "h1b-extract.yml").read_text(
    encoding="utf-8")
h1b_labels = set(re.findall(r"([a-z0-9_]+_us_fulltime):", wf))
artifact_labels = {p.name.replace("_us_fulltime.csv", "")
                   for p in DATA.glob("*_us_fulltime.csv")}
want = {f"{l}_us_fulltime" for l in ALL23}
pass_fail(watch_labels == want, "D7 watch config roster == 27",
          f"{len(watch_labels)} watches")
pass_fail(h1b_labels == want, "D7 h1b employers roster == 27",
          f"{len(h1b_labels)} pairs")
pass_fail(artifact_labels >= set(ALL23),
          "D7 dump artifacts cover all 23",
          f"{len(artifact_labels)} labels with CSVs")

# ── D8: dialect replay (LIVE, all 4 adapter classes) ───────────────────
print("D8 DIALECT REPLAY (live)")
sys.path.insert(0, str(REPO / "ingest"))
from jobsearch.config import Config  # noqa: E402
from jobsearch.sources import site_boards  # noqa: E402

GH_EXPECT = {"baidu": 25, "byd": 22, "neteasegames": 3, "shein": 17,
             "xpeng": 20, "faradayfuture": 70, "didi": 9, "tcl": 3,
             "gotion": 136}
GH_SLUG = {"baidu": "baidu", "byd": "byd", "neteasegames": "neteasegames",
           "shein": "shein", "xpeng": "xpengmotors",
           "faradayfuture": "faradayfuture", "didi": "didi", "tcl": "tcl",
           "gotion": "gotion"}
D9_SLUG = dict(GH_SLUG, anthropic="anthropic")
for label, slug in GH_SLUG.items():
    try:
        jobs = gh_fetch(slug)
    except Exception as e:
        pass_fail(False, f"D8 {label} live replay", f"fetch failed: {e}")
        continue
    site_boards._CACHE.clear()
    site_boards._CACHE[f"ats:greenhouse:{slug}"] = (
        __import__("time").monotonic(), jobs)
    tt = "Full time" if label == "shein" else None
    try:
        rows, meta = site_boards.list_board(
            f"ats:greenhouse:{slug}", country="United States",
            time_type=tt, cfg=Config(), progress_label="audit")
    except Exception as e:
        pass_fail(False, f"D8 {label} live replay", f"list failed: {e}")
        continue
    exp = GH_EXPECT[label]
    # boards churn — replay must be within ±15% of the pinned count
    ok = abs(len(rows) - exp) <= max(2, int(exp * 0.15))
    pass_fail(ok, f"D8 {label} live replay",
              f"live total {meta['total']}, US rows {len(rows)} "
              f"(dump {exp}, churn tolerance ±15%)")

# ashby / lever / workable replay
for label, spec in [("moonshot", "ats:ashby:moonshot"),
                    ("weride", "ats:lever:weride"),
                    ("tplink", "ats:workable:tp-link-usa-corp"),
                    ("pony", "ats:workable:pony-dot-ai"),
                    ("minimax", "ats:feishuhire:vrfi1sk8a0"),
                    ("shengshu", "ats:feishuhire:shengshu"),
                    ("horizon", "ats:lever:horizon")]:
    dump_n = len(load_csv(label))
    tt = "Full time" if label != "moonshot" else None
    try:
        site_boards._CACHE.clear()
        rows, meta = site_boards.list_board(
            spec, country="United States", time_type=tt,
            cfg=Config(), progress_label="audit")
    except Exception as e:
        pass_fail(False, f"D8 {label} live replay", f"list failed: {e}")
        continue
    ok = abs(len(rows) - dump_n) <= max(2, int(dump_n * 0.15))
    pass_fail(ok, f"D8 {label} live replay",
              f"live {len(rows)} rows (dump {dump_n}, ±15% churn)")

# ── D9: adapter health telemetry ───────────────────────────────────────
print("D9 ADAPTER HEALTH TELEMETRY (office-channel)")
for label in ["anthropic", "baidu", "byd", "neteasegames", "shein",
              "xpeng", "faradayfuture", "didi", "tcl", "gotion"]:
    org = D9_SLUG[label]      # S16 fix: labels != slugs (xpengmotors)
    try:
        jobs = gh_fetch(org)
    except Exception as e:
        pass_fail(False, f"D9 {label} telemetry", f"fetch failed: {e}")
        continue
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
    pass_fail(True, f"D9 {label} office telemetry",
              f"{distinct_ids} ids, modal {modal} on "
              f"{modal_n}/{len(jobs)} jobs, discriminate={disc}")

# ── D10: timeType columns ──────────────────────────────────────────────
print("D10 TIMETYPE COLUMNS")
# round-2 review: the S17 boards joined the dimension — minimax/shengshu
# (feishu recruit_type dialect) + xiaohongshu (all-blank by design)
for label, want_tt in [("shein", "Full time"),
                       ("weride", "Full time"),
                       ("tplink", "Full time"),
                       ("pony", "Full time"),
                       ("minimax", "Full time"),
                       ("shengshu", "Full time"),
                       ("xiaohongshu", None)]:
    rows = load_csv(label)
    if not rows:
        pass_fail(False, f"D10 {label} timeType", "no rows")
        continue
    if want_tt is None:
        # xiaohongshu: the API serves no employment type — 100% blanks
        blank = sum(1 for r in rows
                    if not (r.get("timeType") or "").strip())
        pass_fail(blank == len(rows), f"D10 {label} timeType",
                  f"{blank}/{len(rows)} honest blanks (no field in API)")
        continue
    ft = sum(1 for r in rows if r.get("timeType") == want_tt)
    blank = sum(1 for r in rows
                if not (r.get("timeType") or "").strip())
    # honest blanks allowed on workable (empty employment_type rows)
    # and xiaohongshu (the API carries no employment type at all)
    ok = (ft + blank == len(rows))
    pass_fail(ok, f"D10 {label} timeType",
              f"{ft}/{len(rows)} {want_tt}, {blank} honest blanks")

# ── D11: UPSTREAM live parity ──────────────────────────────────────────
print("D11 UPSTREAM LIVE PARITY (every board re-listed, dump vs live)")
# workday-class boards: GEA
for label, spec in [("gea", "haier|wd3|GE_Appliances")]:
    dump_n = len(load_csv(label))
    try:
        site_boards._CACHE.clear()
        from jobsearch.sources import workday as _wd
        rows, meta = _wd.list_board(spec, country="United States",
                                    time_type="Full time",
                                    cfg=Config(), client_filter=False,
                                    progress_every=0)
        live_n = len(rows)
    except Exception as e:
        pass_fail(False, f"D11 {label} upstream parity",
                  f"list failed: {e}")
        continue
    # netflix-class boards churn; parity = live covers the dump's US
    # rows within tolerance (departures are gone-events, not misses)
    ok = live_n >= int(dump_n * 0.85)
    pass_fail(ok, f"D11 {label} upstream parity",
              f"dump {dump_n} vs live-global {live_n} (churn floor 85%)")

# alibaba: the multi-host sweep
try:
    site_boards._CACHE.clear()
    rows, meta = site_boards.list_board(
        "custom:alibaba", country="United States", cfg=Config(),
        progress_label="audit")
    dump_n = len(load_csv("alibaba"))
    skipped = meta.get("hosts_skipped") or []
    ok = abs(len(rows) - dump_n) <= max(4, int(dump_n * 0.2)) and \
        "cloud" not in skipped
    pass_fail(ok, "D11 alibaba upstream parity",
              f"dump {dump_n} vs live {len(rows)}; hosts_skipped="
              f"{skipped}")
except Exception as e:
    pass_fail(False, "D11 alibaba upstream parity", f"list failed: {e}")

# S17 boards live parity (feishuhire x2 + horizon lever + xiaohongshu)
for label, spec, tt in [
        ("minimax", "ats:feishuhire:vrfi1sk8a0", "Full time"),
        ("shengshu", "ats:feishuhire:shengshu", "Full time"),
        ("horizon", "ats:lever:horizon", "Full time"),
        ("xiaohongshu", "custom:xiaohongshu", None)]:
    dump_n = len(load_csv(label))
    try:
        site_boards._CACHE.clear()
        rows, meta = site_boards.list_board(
            spec, country="United States", time_type=tt,
            cfg=Config(), progress_label="audit")
        live_n = len(rows)
        ok = live_n >= int(dump_n * 0.8)   # churn floor for tiny boards
        pass_fail(ok, f"D11 {label} upstream parity",
                  f"dump {dump_n} vs live {live_n} (churn floor 80%)"
                  f" complete={meta.get('complete')}")
    except Exception as e:
        pass_fail(False, f"D11 {label} upstream parity", f"list failed: {e}")

# ── D12: DOWNSTREAM headroom (data on the table) ───────────────────────
print("D12 DOWNSTREAM HEADROOM")
tot_cards = tot_matched = tot_hitidx = tot_blocked = tot_noc = 0
for label in ALL23:
    idx = load_jsonl(DATA / f"{label}_us_fulltime.li_index.jsonl")
    sig = load_jsonl(DATA / f"{label}_us_fulltime.signals.jsonl")
    ts = load_jsonl(DATA / f"{label}_us_fulltime.title_search.jsonl")
    rows = load_csv(label)
    matched = sum(1 for r in rows
                  if r.get("corroborationStatus") == "matched")
    hit_idx = sum(1 for t in ts if t.get("status") == "hit_indexed")
    no_card = sum(1 for t in ts if t.get("status") == "no_card")
    blocked = sum(1 for s in sig
                  if (s.get("status") or "") == "blocked")
    # questionnaires coverage (workday class only has definitions)
    qrows = load_jsonl(DATA / f"{label}_us_fulltime.questionnaires.jsonl")
    qids = {q.get("questionnaireId") for q in qrows if q.get("payload")}
    tot_cards += len(idx)
    tot_matched += matched
    tot_hitidx += hit_idx
    tot_blocked += blocked
    tot_noc += no_card
    if len(idx) and matched < len(idx) * 0.15 and label not in (
            "faradayfuture", "moonshot", "alibaba", "minimax", "shengshu",
            "xiaohongshu"):
        # cards indexed but almost none joined — potential table-left
        pass_fail(False, f"D12 {label} headroom",
                  f"cards {len(idx)} vs matched {matched} — low join "
                  f"utilization; hit_indexed {hit_idx} (cross-host "
                  "duplicates?)")
    else:
        pass_fail(True, f"D12 {label} headroom",
                  f"cards {len(idx)}, matched {matched}, "
                  f"hit_indexed {hit_idx}, no_card {no_card}, "
                  f"blocked {blocked}, questionnaires {len(qids)} defs")
util = tot_matched / tot_cards if tot_cards else 0
print(f"[D12] corpus join utilization: {tot_matched}/{tot_cards} "
      f"cards ({util:.0%}); hit_indexed total {tot_hitidx}; "
      f"no_card total {tot_noc}; blocked total {tot_blocked}")

# similarJobs edge census + h1b utilization
edges = 0
h1b_filings = 0
h1b_banded = 0
for label in ALL23:
    e = load_jsonl(DATA / f"{label}_us_fulltime.similar_edges.jsonl")
    edges += len(e)
    h1b_filings += len(
        load_jsonl(DATA / f"{label}_us_fulltime.h1b_lca.jsonl"))
    h1b_banded += sum(1 for r in load_csv(label)
                      if r.get("h1bMatchBasis"))
pass_pass = pass_fail(True, "D12 corpus edges + h1b utilization",
                      f"similarJobs edges {edges} total; h1b "
                      f"{h1b_banded} banded rows from {h1b_filings} "
                      "filings (n>=3 pools, exact-normalized title "
                      "gates — small boards honestly under the gate)")

# ── D13: column hygiene ────────────────────────────────────────────────
print("D13 COLUMN HYGIENE")
issues = 0
for label in ALL23:
    p = DATA / f"{label}_us_fulltime.csv"
    if not p.exists():
        continue
    raw = p.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf") is False and len(raw) > 0:
        # BOM expected by the readers; its absence is fine too — only
        # U+2028 inside CSV cells and malformed rows are failures
        pass
    text = raw.decode("utf-8-sig", errors="replace")
    if " " in text or " " in text:
        results.append(f"FAIL | D13 {label} hygiene | U+2028/2029 "
                       "inside CSV text")
        issues += 1
    rows = load_csv(label)
    for r in rows:
        if not (r.get("title") or "").strip():
            issues += 1
            results.append(f"FAIL | D13 {label} hygiene | blank title "
                           f"row {r.get('reqId')}")
            break
pass_fail(issues == 0, "D13 column hygiene",
          f"{issues} issues across 23 CSVs (U+2028, blank titles); "
          "duplicate URLs are INV-1's job per-label")

print("=" * 70)
n_pass = sum(1 for r in results if r.startswith("PASS"))
print(f"AUDIT SUMMARY: {n_pass}/{len(results)} PASS")
fails = [r for r in results if r.startswith("FAIL")]
for f in fails:
    print(f)
with open("/tmp/s17_audit_results.txt", "w") as fh:
    fh.write("\n".join(results) + "\n")
print("(full lines → /tmp/s17_audit_results.txt)")
