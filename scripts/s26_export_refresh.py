#!/usr/bin/env python3
"""s26_export_refresh.py — the daily state-merge CSV export (D-S26-2).

WHY: the board watch refreshes STATE daily, but the chain CSVs (and the
UI bundle built from them) freeze at chain-run time. The S26 e2e audit
measured: 420 US rows in state but in NO CSV; 391 ghost rows in CSVs
whose reqIds the boards have dropped; 5 feishu boards whose US rows
exist in NO artifact; 61 boards with LCA filings but 0 banded rows.

WHAT (per watch label, all-local — zero network):
  1. state.jsonl  → current US membership (the watch's compact rewrite
     drops gone rows daily — the membership ground truth)
  2. existing CSV → survivors (rows whose reqId ∈ state), detail-derived
     fields kept, date-derived fields RE-DERIVED against today's
     snapshot, ALL rows re-banded from {label}.h1b_lca.jsonl
     (level-stripped pools, D-S26-3)
  3. newposts.jsonl feed → the LAST record per reqId; state reqIds never
     in a CSV assemble as NEW rows (full 49-col contract from the
     enriched record; signals → corroboration columns)
  4. ghosts (CSV rows ∉ state) dropped; lastResetDate joined from
     {label}.reposts.jsonl (last event per reqId)
  5. the 5 feishu-class boards (no CSV yet) get one built from
     newposts ∩ state

Then (optional, --bundle): run scripts/build_ui_bundle.py so the UI
data refreshes in the same pass.

HONEST LIMITS (documented, not hidden):
  - detail drift on OLD rows (endDate/description edits) still needs
    the chain rotation (S27 plan) — this export never re-fetches
  - state rows still awaiting enrichment (needs_enrich / pending)
    are NOT exported — they land on the next watch leg
  - new rows carry "" for questionnaireId / similarJobsCount (fields
    only the full detail path produces — honest empty, never guessed)

Run:  python3 scripts/s26_export_refresh.py [--bundle] [--labels a,b]
      (repo-relative paths — works in any clone; on GHA it runs in the
      org repo where CSVs + LCA extracts + states are freshest)
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "ingest"))
sys.path.insert(0, str(HERE))

from board_dump import (          # noqa: E402
    _annualize_wage, _derive_h1b_columns, _h1b_house_title,
    _load_h1b_bands, _parse_application_deadline, _slug_repost_count,
    _state_codes, _title_tokens, WATCH_SEED, _H1B_LEVEL_TOKENS,
)
from jobsearch import corroborate            # noqa: E402

DATA = REPO / "ingest/data"
WORKDAY = DATA / "workday"
WATCH_DIR = DATA / "board_watch"

FIELDS = [
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

_NUM_LOCATIONS_RE = None  # compiled lazily (import re at module use)


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    # U+2028-safe: split ONLY on real newlines (the container gotcha —
    # str.splitlines() would break JSON strings mid-row)
    out = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _load_h1b_bands_levelstripped(csv_path: Path,
                                  company: str = "") -> dict:
    """_load_h1b_bands + D-S26-3: level tokens stripped from POOL KEYS
    (posting-side matching unchanged; raw titles stay audit strings).
    A thin re-implementation so board_dump's own contract stays pinned
    independently — the level-strip change lands there too; this copy
    guarantees the export path even if the chain path lags."""
    import re as _re  # noqa: F401
    pools: dict = {}
    path = Path(str(csv_path).removesuffix(".csv") + ".h1b_lca.jsonl")
    if not path.exists():
        return pools
    for rec in _jsonl(path):
        wage = _annualize_wage(rec)
        if wage is None:
            continue
        toks = _title_tokens(
            _h1b_house_title(company, str(rec.get("jobTitle") or "")))
        toks = frozenset(toks - _H1B_LEVEL_TOKENS)
        if len(toks) < 2:
            continue
        pool = pools.setdefault(toks, {"states": {}, "all": [],
                                       "titles": {}})
        pool["all"].append(wage)
        st = str(rec.get("worksiteState") or "").strip().upper()
        if st:
            pool["states"].setdefault(st, []).append(wage)
        raw = str(rec.get("jobTitle") or "")
        pool["titles"][raw] = pool["titles"].get(raw, 0) + 1
    for pool in pools.values():
        pool["title"] = max(sorted(pool["titles"]),
                            key=pool["titles"].get)
    return pools


def _repost_resets(label: str) -> dict[str, str]:
    """{reqId: last new_startDate} from {label}.reposts.jsonl."""
    out: dict[str, str] = {}
    for e in _jsonl(WATCH_DIR / f"{label}.reposts.jsonl"):
        rid = e.get("reqId") or e.get("req")
        sd = e.get("new_startDate") or e.get("startDate") or ""
        if rid and sd:
            out[rid] = str(sd)[:10]     # last event wins
    return out


def _d(s: str):
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _sig_cols(sig: dict | None) -> dict:
    """Corroboration columns from a signals record (or the not-checked
    defaults) — the same mapping _derive_csv_row applies. The sig dict
    may carry a _startDate seam for dateDeltaDays."""
    if not sig:
        return {
            "linkedinUrl": "", "linkedinPostedDate": "", "numApplicants":
            "", "applicantLabel": "", "dateDeltaDays": "",
            "corroborationStatus": corroborate.STATUS_NOT_CHECKED,
            "matchMethod": "", "corroboratedOn": "",
            "applicantCensored": "",
        }
    li = sig.get("linkedin_posted_date") or ""
    delta = ""
    start = sig.get("_startDate")
    if li and start:
        d1, d2 = _d(li), _d(start)
        if d1 and d2:
            delta = (d1 - d2).days
    return {
        "linkedinUrl": sig.get("linkedin_url") or "",
        "linkedinPostedDate": li,
        "numApplicants": sig.get("num_applicants"),
        "applicantLabel": sig.get("applicants_label") or "",
        "dateDeltaDays": delta,
        "corroborationStatus": sig.get("status") or "",
        "matchMethod": sig.get("match_method") or "",
        "corroboratedOn": sig.get("fetched_at") or "",
        "applicantCensored":
            "true" if corroborate._applicant_censored(
                sig.get("num_applicants"),
                sig.get("applicants_label") or "") else "false",
    }


def _date_derived(row: dict, snapshot: date, reposts: dict[str, str]) -> None:
    """Re-derive the snapshot-relative columns IN PLACE against today.
    Uses the row's own evidence only (startDate / linkedinPostedDate /
    applicationDeadline / description / firstSeenDate). All date
    comparisons are ISO-STRING comparisons (lexicographic = ISO
    chronological; mixing date objects and strings raises TypeError —
    the GHA live test caught exactly that on corroborated boards)."""
    start = (row.get("startDate") or "").strip()
    sd = _d(start)
    row["postingAgeDays"] = (snapshot - sd).days if sd else ""
    li = (row.get("linkedinPostedDate") or "").strip()[:10]
    earliest = start
    basis = "startDate" if sd else ""
    if li:
        if start and li < start[:10]:
            earliest, basis = li, "startDate+linkedin"
        elif not start:
            earliest, basis = li, "startDate+linkedin"
    ed = _d(earliest)
    row["daysOnMarket"] = (snapshot - ed).days if ed and basis else ""
    row["daysOnMarketBasis"] = basis
    first = (row.get("firstSeenDate") or "").strip()[:10]
    row["censored"] = ("true" if (
        not start or not sd
        or start[:10] < WATCH_SEED.isoformat()
        or (first and first < start[:10])) else "false")
    deadline = (row.get("applicationDeadline") or "").strip()
    dd = _d(deadline)
    if dd:
        left = (dd - snapshot).days
        row["daysLeftToApply"] = left if left >= 0 else ""
    else:
        row["daysLeftToApply"] = ""
    row["earliestEvidenceDate"] = earliest
    li_predates = bool(li and start and li < start[:10])
    row["crossSourceRepostEvidence"] = (
        "reqId" if (li_predates
                    and (row.get("matchMethod") or "title") == "reqId")
        else "title" if li_predates else "")
    row["lastResetDate"] = reposts.get(row.get("reqId") or "", "")
    row["dumpDate"] = snapshot.isoformat()


def _newpost_row(rec: dict, w: dict, snapshot: date,
                 reposts: dict[str, str], sig: dict | None) -> dict:
    """Assemble the full 49-col row from an enriched newposts record."""
    import re
    locs = [str(x) for x in (rec.get("locations") or []) if str(x).strip()]
    locs_text = (rec.get("locationsText") or "").strip()
    if not locs and locs_text:
        # the workday terse "N Locations" class: count UNKNOWN, not 1
        if re.fullmatch(r"\d+ Locations?", locs_text, re.I):
            locs, n_loc = [locs_text], ""
        else:
            locs, n_loc = [locs_text], 1
    else:
        n_loc = len(locs)
    desc = rec.get("description") or ""
    url = rec.get("externalUrl") or rec.get("url") or ""
    start = (rec.get("startDate") or "").strip()
    deadline = _parse_application_deadline(desc)
    company = rec.get("company") or w.get("company") or ""
    row = {
        "reqId": rec["reqId"],
        "title": rec.get("title") or "",
        "company": company,
        "hiringOrg": rec.get("hiringOrg") or "",
        "timeType": rec.get("timeType") or "",
        "postedOn": rec.get("postedOn") or "",
        "startDate": start,
        "primaryLocation": locs[0] if locs else "",
        "nLocations": n_loc,
        "locations": "; ".join(locs),
        "remoteFlag": "true" if any(
            "remote" in (l or "").lower() for l in locs + [locs_text]
        ) else "false",
        "stateCodes": _state_codes(locs),
        "country": rec.get("country") or "",
        "questionnaireId": "",
        "similarJobsCount": 0,
        "description": desc,
        "descriptionLength": len(desc),
        "url": url,
        "detailError": rec.get("error") or "",
        "firstSeenDate": (rec.get("first_seen") or "")[:10]
        or snapshot.isoformat(),
        "endDate": "",
        "repostCount": _slug_repost_count(url),
        "applicationDeadline": deadline,
        "workerSubType": "",
        "jobFamilyGroup": "",
    }
    row.update(_sig_cols({**sig, "_startDate": start} if sig else None))
    _date_derived(row, snapshot, reposts)
    return {k: v for k, v in row.items() if k in FIELDS}


def refresh_one(label: str, w: dict, snapshot: date) -> dict:
    """One board's export. Returns stats; writes the CSV only when the
    board has any output rows OR an existing CSV to prune."""
    state = {r["reqId"]: r for r in _jsonl(
        WATCH_DIR / f"{label}.state.jsonl")}
    csv_path = WORKDAY / f"{label}.csv"
    csv_rows: list[dict] = []
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            csv_rows = [r for r in csv.DictReader(f) if r.get("reqId")]

    # the feed: LAST record per reqId wins (append-only contract)
    feed: dict[str, dict] = {}
    for r in _jsonl(WATCH_DIR / f"{label}.newposts.jsonl"):
        feed[r.get("reqId")] = r

    reposts = _repost_resets(label)
    pools = _load_h1b_bands_levelstripped(csv_path, w.get("company", ""))

    csv_ids = {r["reqId"] for r in csv_rows}
    survivors = [dict(r) for r in csv_rows if r["reqId"] in state]
    ghosts = [r for r in csv_rows if r["reqId"] not in state]

    new_rows, pending = [], 0
    for rid in state:
        if rid in csv_ids:
            continue
        rec = feed.get(rid)
        if not rec or rec.get("error"):
            pending += 1            # awaiting enrichment (next leg)
            continue
        new_rows.append(_newpost_row(rec, w, snapshot, reposts,
                                     rec.get("signals")))

    rows = survivors + sorted(new_rows, key=lambda r: str(r["reqId"]))

    # re-derive dates + re-band EVERY row (existing rows: fields update
    # in place; dateDelta recomputed via the sig's own startDate seam)
    banded = 0
    for row in rows:
        if row.get("linkedinPostedDate") and row.get("startDate"):
            d1, d2 = _d(row["linkedinPostedDate"]), _d(row["startDate"])
            row["dateDeltaDays"] = (d1 - d2).days if d1 and d2 else ""
        _date_derived(row, snapshot, reposts)
        h = _derive_h1b_columns(row.get("title") or "",
                                row.get("primaryLocation") or "", pools)
        row.update(h)
        if h.get("h1bFilings"):
            banded += 1

    stats = {
        "label": label, "rows": len(rows),
        "kept": len(survivors), "new": len(new_rows),
        "ghosts_dropped": len(ghosts), "pending": pending,
        "banded": banded, "lca_pools": len(pools),
    }
    if rows or csv_path.exists():
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.DictWriter(f, fieldnames=FIELDS,
                                extrasaction="ignore")
            wr.writeheader()
            for row in rows:
                wr.writerow({k: row.get(k, "") for k in FIELDS})
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="",
                    help="comma-separated label filter (default: all)")
    ap.add_argument("--bundle", action="store_true",
                    help="run build_ui_bundle.py after the export")
    args = ap.parse_args()

    cfg = json.loads((WATCH_DIR / "config.json").read_text(
        encoding="utf-8"))
    watches = cfg["watches"]
    if args.labels:
        keep = {x.strip() for x in args.labels.split(",")}
        watches = [w for w in watches if w["label"] in keep]
    snapshot = date.today()

    tot = {"rows": 0, "new": 0, "ghosts_dropped": 0, "pending": 0,
           "banded": 0}
    n_boards = 0
    print(f"[export] {len(watches)} watches, snapshot {snapshot}", flush=True)
    for w in watches:
        try:
            st = refresh_one(w["label"], w, snapshot)
        except Exception as exc:      # one board never kills the batch
            print(f"[export:{w['label']}] ERROR {type(exc).__name__}: "
                  f"{exc}", file=sys.stderr, flush=True)
            continue
        n_boards += 1
        for k in tot:
            tot[k] += st[k]
        print(f"[export:{st['label']:34}] rows={st['rows']:4} "
              f"kept={st['kept']:4} +new={st['new']:3} "
              f"-ghost={st['ghosts_dropped']:3} pending={st['pending']:3} "
              f"banded={st['banded']:4} pools={st['lca_pools']:3}",
              flush=True)
    print(f"[export] DONE: {n_boards} boards, rows={tot['rows']}, "
          f"+new={tot['new']}, -ghosts={tot['ghosts_dropped']}, "
          f"pending={tot['pending']}, banded={tot['banded']}", flush=True)

    if args.bundle:
        import os
        # UI_OUT overrides the builder's sandbox default (on GHA the
        # runner has no /home/z/my-project — point it at the repo-local
        # ui dir; the builder's own mirror step then no-ops)
        out = os.environ.get("UI_OUT") or None
        cmd = [sys.executable, str(HERE / "build_ui_bundle.py")]
        if out:
            cmd += ["--out", out]
        r = subprocess.run(cmd, cwd=REPO)
        if r.returncode != 0:
            print("[export] bundle build FAILED", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
