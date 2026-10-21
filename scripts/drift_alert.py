#!/usr/bin/env python3
"""drift_alert.py — D-S35-2: durable drift alerting for expected-vs-
actual gaps the self-healing stack cannot close.

The gap (validated live 2026-10-14: LI #7's otterai failed both the
initial pass AND the deferred retry — the red run scrolls away with
the next green run; nothing durable tracks it). This auditor runs as
the LAST audit step of csv-export.yml (BEFORE the checkpoint — so
baseline/manifest updates get committed) and turns unhealable drift
into GitHub issues (dedup by title, recurrence as comments,
auto-close when healed).

Data sources (all committed; board_watch/ is mirror-safe — the
forward rsync never wipes it, the back-sync pulls org→archive):
  - ingest/data/board_watch/{label}.runs.jsonl    per-run verdicts
  - ingest/data/board_watch/{label}.state.jsonl   rows + last_seen
  - ingest/data/workday/{label}_us_fulltime.export.json  manifests
  - ingest/data/board_watch/drift_baseline.json   row-count series

Drift classes (thresholds = DESIGN-S35 §3.2, review round-2):
  board-fail-streak  >=2 consecutive 'failed' verdicts (the deferred
                       retry already failed once — unhealable in-run)
  row-collapse        current < 40% of the 7d-max baseline AND
                       (baseline - current) >= 3 rows
  row-zero            baseline > 0 and current == 0
  window-decline      tail < 50% of the 28d-ago head (slow collapse)
  stale-board         state max(last_seen) > cadence x 2 (cadence from
                       config tags; never-yielded labels exempt)
  unexported-drift    manifest identity broken OR unexported count
                       deviates from the by-design manifest classes
  corroboration-      >=3 consecutive ledger lines with
  blocked-streak      corroboration_blocked=true (S37-W1 / D-S37-1
                       A1: the enrich-phase LinkedIn outage — the lane
                       runs green while the signals layer freezes)

Usage:
  python3 scripts/drift_alert.py [--dry-run]
GHA mode (issue sink) activates when GITHUB_TOKEN + REPO env are set
AND --dry-run is absent; local runs default to report-only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCH_DIR = REPO_ROOT / "ingest/data/board_watch"
WORKDAY = REPO_ROOT / "ingest/data/workday"
BASELINE = WATCH_DIR / "drift_baseline.json"
SERIES_KEEP = 120          # entries per label (append-only, capped)

CADENCE_DAYS = {           # config tag -> expected refresh interval
    "li": 1, "weekly": 7, "li_dormant": 7, "gj": 7,
    "gj_dormant": 30, "cf": 7, "monthly": 30,
    # S37-W1 (D-S37-1 A12 / review P2-5): the three lanes that fell to
    # DEFAULT_CADENCE=1 wrong-by-accident — 66 boards (ra 59 +
    # gj_quarterly 5 + gj_monthly 2) treated as DAILY. Harmless today
    # ONLY because those lanes emit no workday CSVs (the stale-board
    # class's has_csv gate exempts them); any future CSV emission for
    # those lanes would storm false-positive stale-board issues.
    "ra": 7, "gj_quarterly": 95, "gj_monthly": 35,
}
DEFAULT_CADENCE = 1

# DOCUMENTED BLIND SPOT (A12): the 98 no-CSV boards (ra 59 +
# li_dormant 39) have NO row monitoring at all — no workday CSV means
# _rows_now()=0 and the has_csv gate exempts them from stale-board, so
# row-collapse/row-zero/window-decline never see them either. Their
# coverage is VERDICT-based only (board-fail-streak +
# corroboration-blocked-streak over runs.jsonl). The compensating read
# is lane-recency (the refresher-health script, D-S37-1 R6/A11) — a
# lane that never ran leaves no per-label evidence at all.


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _rows_now(label: str) -> int:
    # config labels already carry the _us_fulltime suffix — file names
    # are {full-label}.export.json / {full-label}.csv
    m = WORKDAY / f"{label}.export.json"
    if m.exists():
        try:
            return int(json.loads(m.read_text()).get("rows", 0))
        except (json.JSONDecodeError, ValueError):
            pass
    c = WORKDAY / f"{label}.csv"
    if c.exists():
        return sum(1 for r in _read_jsonl(c) if r) if c.suffix == ".jsonl" \
            else _csv_rows(c)
    return 0


def _csv_rows(c: Path) -> int:
    import csv
    with open(c, newline="", encoding="utf-8-sig") as f:
        return sum(1 for r in csv.DictReader(f) if r.get("reqId"))


def _last_seen(label: str) -> date | None:
    st = _read_jsonl(WATCH_DIR / f"{label}.state.jsonl")
    best = None
    for r in st:
        try:
            d = date.fromisoformat((r.get("last_seen") or "")[:10])
            if best is None or d > best:
                best = d
        except ValueError:
            pass
    return best


def _fail_streak(label: str, n: int = 2) -> int:
    runs = _read_jsonl(WATCH_DIR / f"{label}.runs.jsonl")
    streak = 0
    for r in reversed(runs):
        if r.get("verdict") == "failed":
            streak += 1
        else:
            break
    return streak


def _corroboration_blocked_streak(label: str, n: int = 3) -> int:
    """S37-W1 (D-S37-1 A1 / review P0-1): consecutive runs.jsonl lines
    carrying corroboration_blocked=true for this label — the durable
    marker gha_board_watch writes when corroborate_new's LinkedIn
    index is blocked (the enrich-phase outage the lane used to run
    GREEN straight through). Threshold N=3 consecutive runs: one
    blocked run can be a transient wall (the in-run deferred retry +
    next-day retry both heal), two is suspicious, three daily runs
    with a frozen signals layer (numApplicants / linkedinUrl /
    matchMethod) is a real outage worth a GitHub issue. Mirrors the
    _fail_streak shape (trailing run of a boolean field; any clean
    line breaks it)."""
    runs = _read_jsonl(WATCH_DIR / f"{label}.runs.jsonl")
    streak = 0
    for r in reversed(runs):
        if r.get("corroboration_blocked"):
            streak += 1
        else:
            break
    return streak


def _series_append(baseline: dict, label: str, rows: int) -> None:
    """APPEND-ONLY (the round-1 P1-6 lesson): never upsert — a same-day
    downsert would erase the baseline max and hide a collapse in
    progress. Multiple entries per day are fine; _series_stats takes
    the window max. Capped at SERIES_KEEP entries."""
    s = baseline.setdefault(label, [])
    s.append({"d": date.today().isoformat(), "rows": rows})
    del s[:-SERIES_KEEP]


def _series_stats(s: list[dict]) -> tuple[int, int]:
    """(7d-max, 28d-ago head) for the collapse/decline math."""
    today = date.today()
    mx7, head28 = 0, None
    for e in s:
        try:
            d = date.fromisoformat(e["d"])
        except (KeyError, ValueError):
            continue
        if (today - d).days <= 7:
            mx7 = max(mx7, e["rows"])
        if (today - d).days >= 28 and head28 is None:
            head28 = e["rows"]
    return mx7, (head28 if head28 is not None else 0)


def detect(watches: list[dict], baseline: dict) -> list[dict]:
    """Returns a list of drift findings: {class, label, evidence}."""
    findings = []
    for w in watches:
        label = w["label"]
        tags = set(w.get("tags") or [])
        cadence = next((CADENCE_DAYS[t] for t in tags
                        if t in CADENCE_DAYS), DEFAULT_CADENCE)
        rows = _rows_now(label)
        seeded = label in baseline
        _series_append(baseline, label, rows)

        # 1. board-fail-streak (unhealable within-run failures)
        streak = _fail_streak(label)
        if streak >= 2:
            findings.append({"class": "board-fail-streak", "label": label,
                             "evidence": f"{streak} consecutive failed "
                                          "runs (deferred-retry exhausted)"})
        # 1b. corroboration-blocked-streak (S37-W1 / D-S37-1 A1: the
        # enrich-phase outage — verdict-independent, the lane can run
        # green while the signals layer freezes; >=3 consecutive
        # flagged ledger lines -> the issue sink)
        cb = _corroboration_blocked_streak(label)
        if cb >= 3:
            findings.append({
                "class": "corroboration-blocked-streak", "label": label,
                "evidence": f"{cb} consecutive run(s) with the LinkedIn "
                             "corroboration index blocked (signals "
                             "frozen — no numApplicants/linkedinUrl/"
                             "matchMethod; postings still collected)"})
        # 2-4. row classes (only for labels with baseline history)
        if seeded:
            mx7, head28 = _series_stats(baseline[label])
            if mx7 > 0 and rows == 0:
                # severity order: zero fires first (the collapse class
                # subsumes it for large boards; zero is the cleaner tag)
                findings.append({"class": "row-zero", "label": label,
                                 "evidence": f"0 rows (7d-max {mx7})"})
            elif mx7 and (mx7 - rows) >= 3 and rows < 0.4 * mx7:
                findings.append({"class": "row-collapse", "label": label,
                                 "evidence": f"{rows} rows vs 7d-max "
                                              f"{mx7} (-"
                                              f"{100 * (mx7 - rows) // mx7}"
                                              "%)"})
            if head28 and rows < 0.5 * head28 and (head28 - rows) >= 3:
                findings.append({"class": "window-decline", "label": label,
                                 "evidence": f"{rows} rows vs 28d-ago "
                                              f"{head28} (-"
                                              f"{100 * (head28 - rows) // head28}"
                                              "%)"})
        # 5. stale-board (never-yielded labels exempt — no CSV, no
        #    expectation; the honest-zero class is not drift)
        ls = _last_seen(label)
        has_csv = (WORKDAY / f"{label}.csv").exists()
        if ls and has_csv:
            age = (date.today() - ls).days
            if age > cadence * 2 + 1:
                findings.append({"class": "stale-board", "label": label,
                                 "evidence": f"last_seen {ls} "
                                              f"({age}d; cadence "
                                              f"{cadence}d)"})
        # 6. unexported-drift (manifest identity)
        m = WORKDAY / f"{label}.export.json"
        if m.exists():
            try:
                mm = json.loads(m.read_text())
            except json.JSONDecodeError:
                findings.append({"class": "unexported-drift", "label": label,
                                 "evidence": "manifest unparseable"})
                mm = None
            if mm:
                ident = (mm.get("kept", 0) + mm.get("gate_dropped_survivors", 0)
                         + mm.get("ghosts_in_state", 0) + mm.get("new", 0)
                         + mm.get("gate_dropped_new", 0)
                         + mm.get("pending", 0)
                         + mm.get("feed_excluded_state", 0))
                if ident != mm.get("state_rows"):
                    findings.append({
                        "class": "unexported-drift", "label": label,
                        "evidence": f"manifest identity {ident} != "
                                    f"state {mm.get('state_rows')}"})
    return findings


# ── the GitHub issue sink ──────────────────────────────────────────

def _api(method: str, url: str, token: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def sink_issues(findings: list[dict], token: str, repo: str) -> None:
    """One issue per (class, label): create if absent, comment on
    recurrence, auto-close healed combos. Fail-loudly on API errors."""
    open_issues = _api("GET",
                       f"https://api.github.com/repos/{repo}/issues"
                       "?state=open&labels=drift-alert&per_page=100", token)
    by_title = {i["title"]: i for i in open_issues
                if "drift-alert" in [l["name"] for l in i.get("labels", [])]}
    for f in findings:
        title = f"[drift:{f['class']}] {f['label']}"
        if title in by_title:
            _api("POST",
                 f"https://api.github.com/repos/{repo}/issues/"
                 f"{by_title[title]['number']}/comments", token,
                 {"body": f"still drifting @ {_now()}: {f['evidence']}"})
        else:
            _api("POST", f"https://api.github.com/repos/{repo}/issues",
                 token, {"title": title, "labels": ["drift-alert"],
                         "body": f"**D-S35-2 drift alert**\n\n"
                                 f"class: `{f['class']}`\n"
                                 f"label: `{f['label']}`\n\n"
                                 f"evidence: {f['evidence']}\n\n"
                                 f"detected: {_now()}\n\n"
                                 "(auto-generated by drift_alert.py; "
                                 "auto-closes when healed)"})
    # auto-close: open drift issues whose (class, label) is clean now
    live = {(f["class"], f["label"]) for f in findings}
    for title, iss in by_title.items():
        if title.startswith("[drift:"):
            try:
                cls = title[len("[drift:"):].split("]")[0]
                label = title.split("] ", 1)[1]
            except IndexError:
                continue
            if (cls, label) not in live:
                _api("PATCH",
                     f"https://api.github.com/repos/{repo}/issues/"
                     f"{iss['number']}", token,
                     {"state": "closed",
                      "state_reason": "completed"})


def detect_sweep(baseline: dict) -> list[dict]:
    """S36 sweep-mode: the roles:* series (ledger + baseline fold —
    DESIGN-S36 §5). Label discovery is self-registered: the sweep run
    writes the ledger + the baseline entry; this auditor consumes.
    S41 family generalization: glob roles:*.runs.jsonl — every family
    lane (pm, dmm, ...) gets its own drift audit (the F9-9 fix).
    Classes: sweep-fail-streak (verdict), sweep-source-death (the same
    engine dead >=2 consecutive runs), row-collapse/zero/decline (the
    shared row classes, label roles:<family>)."""
    findings = []
    ledgers = sorted(WATCH_DIR.glob("roles:*.runs.jsonl"))
    if not ledgers and not any(k.startswith("roles:")
                               for k in baseline):
        return []                    # no sweep ever ran — nothing to audit
    for ledger_path in ledgers:
        label = ledger_path.name[:-len(".runs.jsonl")]
        findings += _sweep_label_findings(label, baseline)
    return findings


def _sweep_label_findings(label: str, baseline: dict) -> list[dict]:
    ledger = _read_jsonl(WATCH_DIR / f"{label}.runs.jsonl")
    if not ledger and label not in baseline:
        return []                    # this family never ran
    findings = []
    streak = 0
    for e in reversed(ledger):
        if e.get("verdict") == "failed":
            streak += 1
        else:
            break
    if streak >= 2:
        findings.append({"class": "sweep-fail-streak", "label": label,
                         "evidence": f"{streak} consecutive failed sweeps"})
    # source-death streaks (per engine)
    dead_streak: dict[str, int] = {}
    for e in ledger:
        dead = e.get("sources_dead") or []
        for s in dead:
            dead_streak[s] = dead_streak.get(s, 0) + 1
        for s in list(dead_streak):
            if s not in dead and dead_streak[s] < 2:
                dead_streak.pop(s)      # healed before confirming
            elif s not in dead:
                dead_streak[s] = 0      # healed — reset (no finding)
    last_dead = set(ledger[-1].get("sources_dead") or []) if ledger else set()
    for s, n in dead_streak.items():
        if n >= 2 and s in last_dead:
            findings.append({"class": "sweep-source-death", "label": label,
                             "evidence": f"engine {s} dead in {n} "
                                          "consecutive sweeps"})
    # row classes on the baseline series (the sweep run appends it)
    series = baseline.get(label) or []
    if series:
        rows = series[-1].get("rows", 0)
        mx7, head28 = _series_stats(series)
        if mx7 > 0 and rows == 0:
            findings.append({"class": "row-zero", "label": label,
                             "evidence": f"0 rows (7d-max {mx7})"})
        elif mx7 and (mx7 - rows) >= 3 and rows < 0.4 * mx7:
            findings.append({"class": "row-collapse", "label": label,
                             "evidence": f"{rows} rows vs 7d-max {mx7} (-"
                                          f"{100 * (mx7 - rows) // mx7}%)"})
        if head28 and rows < 0.5 * head28 and (head28 - rows) >= 3:
            findings.append({"class": "window-decline", "label": label,
                             "evidence": f"{rows} rows vs 28d-ago {head28}"
                                          f" (-{100 * (head28 - rows) // head28}%)"})
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report findings, no issue writes, no baseline "
                         "file write")
    args = ap.parse_args()
    cfg = json.loads((WATCH_DIR / "config.json").read_text(
        encoding="utf-8"))
    watches = cfg["watches"]
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    findings = detect(watches, baseline)
    findings += detect_sweep(baseline)

    print(f"[drift] {len(watches)} watches, {len(findings)} finding(s)")
    for f in findings:
        print(f"  [drift:{f['class']}] {f['label']}: {f['evidence']}")
    if args.dry_run:
        print("[drift] dry-run — no writes")
        return 0
    BASELINE.write_text(json.dumps(baseline, indent=1, sort_keys=True)
                        + "\n", encoding="utf-8")
    token = os.environ.get("GITHUB_TOKEN") or ""
    repo = os.environ.get("DRIFT_REPO") or ""
    if token and repo and os.environ.get("GITHUB_ACTIONS"):
        try:
            sink_issues(findings, token, repo)
            print(f"[drift] issue sink synced ({len(findings)} live)")
        except Exception as exc:
            # fail-loudly: a broken alerting path must never be green
            print(f"::error::drift issue sink FAILED: {exc}",
                  file=sys.stderr)
            return 1
    else:
        print("[drift] local mode — baseline written, no issue sink "
              "(set GITHUB_TOKEN + DRIFT_REPO + GITHUB_ACTIONS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
