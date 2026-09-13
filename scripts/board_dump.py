#!/usr/bin/env python3
"""Generic exhaustive per-company board snapshot (board-dump v2).

Generalizes scripts/workday_dump.py (the NVIDIA pilot) into the repeatable
flow the user asked to productize (design-board-v2.md D3):

  list → details → corroborate → finish

- **list**: exhaustively paginate one ATS board (server-side facets for
  country / timeType), streaming JSONL, wrap-guards per the CXS quirks
  (offset past total WRAPS; near-end page total=0; limit<=20).
- **details**: per-posting detail GET, RAW-preserved (every jobPostingInfo
  field + hiringOrganization + similarJobs), resumable in bounded batches.
- **corroborate**: join aggregator-platform signals (LinkedIn guest:
  applicant counts, cross-post dates, req-id exact join) — resumable,
  circuit-breakered, status-enummed (matched|no_match|blocked|not_checked).
- **finish**: assemble {out}.json + the v2 CSV (full locations, clean-text
  description, every metadata field, signal columns) + a validation report.

Board providers: workday is the first (the CXS facts); the phase split is
board-agnostic — corroborate/finish operate purely on the JSONL files.

Sandbox doctrine: batch + checkpoint (2-min bash limit); every phase is
idempotent and resumable; files are the state.

Usage:
  python3 scripts/board_dump.py --board nvidia|wd5|nvidiaexternalcareersite \
      --company NVIDIA --label nvidia_us_fulltime --phase list
  ... --phase details   (repeat while rows remain)
  ... --phase corroborate (repeat while cards remain; then signals)
  ... --phase finish
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "ingest"))

from jobsearch.config import Config  # noqa: E402
from jobsearch.htmltext import html_to_text  # noqa: E402
from jobsearch.sources import workday  # noqa: E402
from jobsearch import corroborate  # noqa: E402

# ── CSV v2 column contract (design-board-v2.md D1 + review addenda) ──────
CSV_COLUMNS = [
    "reqId",                 # 1  requisition id (JR…)
    "title",                 # 2
    "company",               # 3
    "hiringOrg",             # 4  legal hiring entity ("2100 NVIDIA USA")
    "timeType",              # 5  "Full time"
    "postedOn",              # 6  relative label ("Posted Today")
    "startDate",             # 7  ISO posting date
    "postingAgeDays",        # 8  today − startDate
    "reqYear",               # 9  from JR2022xxxx → 2022 (req age signal)
    "primaryLocation",       # 10 "US, CA, Santa Clara"
    "nLocations",            # 11 total locations
    "locations",             # 12 ALL locations "; "-joined — 0A
    "remoteFlag",            # 13 any location mentions Remote
    "stateCodes",            # 14 "CA;NC;TX" derived
    "country",               # 15 country descriptor
    "questionnaire",         # 16 application questionnaire present
    "similarJobsCount",      # 17 related postings on the board
    "description",           # 18 clean text (html_to_text) — 0B
    "descriptionLength",     # 19 chars of clean text
    "url",                   # 20 canonical apply URL
    "detailError",           # 21 "" | detail_unreachable (detail-derived
                             #    cols above are UNKNOWN, not absent)
    "linkedinUrl",           # 22 matched LI posting — 0D
    "linkedinPostedDate",    # 22 LI cross-post date
    "numApplicants",         # 23 LI applicant count
    "applicantLabel",        # 24 raw label ("Over 200 applicants")
    "dateDeltaDays",         # 25 LI date − startDate (repost lag)
    "corroborationStatus",   # 26 matched|no_match|blocked|not_checked
    "matchMethod",           # 27 reqId|title|""
    "firstSeenDate",         # 28 first sighting by us (dump/watch)
    "corroboratedOn",        # 29 ISO timestamp of signal fetch
    "dumpDate",              # 30 dump generation date
]

_STATE_RE = re.compile(r"^[A-Z]{2},\s*([A-Z]{2}),")


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    # corrupt trailing line (crash mid-append) — tolerate
                    print(f"[warn] skipping corrupt line in {path.name}",
                          file=sys.stderr)
    return rows


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.flush()


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ── phase: list ──────────────────────────────────────────────────────────

def phase_list(args, out: Path) -> int:
    cfg = Config()
    list_path = out.with_suffix(".list.jsonl")
    # honest status from the START (audit S7-A2 M): a crash mid-list must
    # not leave the PREVIOUS run's "complete: true" next to a truncated
    # list file. Rewritten on completion below.
    _atomic_write_text(out.with_suffix(".list.status"), json.dumps(
        {"rows": 0, "total": 0, "complete": False, "pages": 0}))
    try:
        rows, meta = workday.list_board(
            args.board, country=args.country or None,
            time_type=args.time_type or None, cfg=cfg,
            sleep_s=args.sleep, progress_every=10)
    except ValueError as exc:
        print(f"[list] {exc}")
        return 2
    except Exception as exc:
        print(f"[list] page-0 fetch failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    with open(list_path, "w", encoding="utf-8") as lf:
        lf.write("\n".join(
            json.dumps(r, ensure_ascii=False)
            for r in rows.values()) + "\n")
    complete = bool(meta.get("complete"))
    print(f"[list] done: {len(rows)} unique postings "
          f"(first-page total={meta.get('total')}, "
          f"pages={meta.get('pages')}) → {list_path}", flush=True)
    _atomic_write_text(out.with_suffix(".list.status"), json.dumps({
        "rows": len(rows), "total": meta.get("total"),
        "complete": complete, "pages": meta.get("pages")}))
    if not complete:
        print(f"[list] WARNING: INCOMPLETE listing (network break) — "
              "re-run --phase list; watch must NOT emit gone-events")
        return 1
    return 0


# ── phase: details (v2 — RAW full payload) ───────────────────────────────

def phase_details(args, out: Path) -> int:
    board = workday.parse_board(args.board)
    list_path = out.with_suffix(".list.jsonl")
    det_path = out.with_suffix(".details.jsonl")
    if not list_path.exists():
        print(f"no {list_path} — run --phase list first")
        return 2
    rows = _load_jsonl(list_path)
    done: dict[str, dict] = {}
    for d in _load_jsonl(det_path):
        done[d["reqId"]] = d
    # error rows retry with a 3-strike cap (audit S7-A2 H: transient 429s
    # were permanently losing descriptions). attempts accumulate per req.
    settled = {rid for rid, d in done.items() if d.get("info")}
    three_strikes = {rid for rid, d in done.items()
                     if (d.get("error") == "detail_unreachable"
                         and int(d.get("attempts") or 1) >= 3)}
    todo = [r for r in rows
            if r["reqId"] not in settled and r["reqId"] not in three_strikes]
    print(f"[details] {len(rows)} rows, {len(settled)} settled, "
          f"{len(done) - len(settled)} error rows "
          f"({len(three_strikes)} past 3-strike cap), "
          f"{len(todo)} to fetch (batch={args.details_batch})", flush=True)
    if not todo:
        return 0
    workday._DETAIL_SLEEP_S = args.detail_sleep
    cfg = Config()          # hoisted (was per-row: 150 mkdir+env-scans/batch)
    batch = todo[:args.details_batch]
    # per-row checkpointing (the 2-min-bash doctrine): EVERY record is
    # appended+flushed the moment it is fetched — a timeout kill loses
    # nothing (v1 behavior; a memory-accumulated batch lost 125 rows to
    # a timeout kill on 2026-09-09 — never again).
    with open(det_path, "a", encoding="utf-8") as df:
        for i, r in enumerate(batch, 1):
            payload = workday.detail_payload(
                board, r["externalPath"], cfg)
            rec: dict = {"reqId": r["reqId"]}
            if payload:
                info = payload.get("jobPostingInfo") or {}
                # RAW preservation — every jobPostingInfo field
                rec["info"] = info
                org = payload.get("hiringOrganization") or {}
                rec["hiringOrg"] = org.get("name")
                rec["similarJobsCount"] = len(
                    payload.get("similarJobs") or [])
            else:
                prev = done.get(r["reqId"]) or {}
                rec["error"] = "detail_unreachable"
                rec["attempts"] = int(prev.get("attempts") or 0) + 1
            df.write(json.dumps(rec, ensure_ascii=False) + "\n")
            df.flush()
            if i % 25 == 0:
                print(f"[details] {i}/{len(batch)} this batch "
                      f"({len(settled) + i}/{len(rows)} total)", flush=True)
    remaining = len(todo) - len(batch)
    if remaining:
        print(f"[details] batch done — {remaining} rows remaining "
              f"(re-run --phase details)", flush=True)
    else:
        print(f"[details] ALL {len(rows)} rows enriched", flush=True)
    return 0


# ── phase: corroborate ───────────────────────────────────────────────────

def phase_corroborate(args, out: Path) -> int:
    """Two sub-steps, each checkpointed to its own JSONL (review Q2):
    1. LinkedIn index → {out}.li_index.jsonl (cards; ACCUMULATES across
       invocations, resume offset in {out}.li_index.meta.json)
    2. per-card signals → {out}.signals.jsonl (resumable by card id)
    Blocked is a recorded status, never fatal (B5 no-raise contract).
    """
    list_path = out.with_suffix(".list.jsonl")
    if not list_path.exists():
        print(f"no {list_path} — run --phase list first")
        return 2
    rows = _load_jsonl(list_path)
    req_ids = {r["reqId"] for r in rows}
    idx_path = out.with_suffix(".li_index.jsonl")
    sig_path = out.with_suffix(".signals.jsonl")
    provider = corroborate.get_provider(args.provider)

    # ── sub-step 1: index (accumulates; resumable via meta offset) ──
    indexed: list[dict] = _load_jsonl(idx_path)
    index_meta = {"offset": 0, "done": False}
    meta_path = out.with_suffix(".li_index.meta.json")
    if meta_path.exists():
        try:
            index_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    if args.corroborate_index and not index_meta.get("done"):
        try:
            cards, next_offset, exhausted = provider.index_cards(
                company=args.company, location=args.location,
                max_pages=args.li_index_pages,
                max_cards=args.li_index_cards,
                start_offset=int(index_meta.get("offset", 0)))
        except corroborate.CorroborationBlocked as exc:
            print(f"[corroborate] INDEX BLOCKED at page 0: {exc}",
                  file=sys.stderr)
            _atomic_write_text(meta_path, json.dumps({
                "offset": int(index_meta.get("offset", 0)), "done": False,
                "blocked": True, "at": datetime.now().isoformat()}))
            return 0      # signals sub-step pointless without an index
        # accumulate (dedup by card id) — never lose prior progress
        have = {c["id"] for c in indexed}
        for c in cards:
            if c["id"] not in have:
                indexed.append(c)
        _atomic_write_text(idx_path, "\n".join(
            json.dumps(c, ensure_ascii=False) for c in indexed) + "\n")
        index_meta = {
            "offset": next_offset, "done": exhausted,
            "blocked": False, "cards": len(indexed),
            "indexed_at": datetime.now().isoformat()}
        _atomic_write_text(meta_path, json.dumps(index_meta))
        print(f"[corroborate] index: +{len(cards)} new "
              f"(total {len(indexed)}, next_offset={next_offset}, "
              f"exhausted={exhausted})", flush=True)
    elif not indexed:
        print(f"[corroborate] no index yet at {idx_path} "
              "(pass --corroborate-index)", flush=True)

    # ── sub-step 2: signals for index cards (resumable by card id) ──
    # blocked = fetch failed (429 wall / circuit-open) — NOT done; the
    # audit (S7-A2 G) found blocked cards were permanently skipped. Retry
    # them; only matched records count as settled (blocked records are
    # replaced, not accumulated, by re-fetch).
    done_ids = {s.get("linkedin_job_id") for s in _load_jsonl(sig_path)
                if s.get("status") != "blocked"}
    todo_cards = [c for c in indexed if c["id"] not in done_ids]
    print(f"[corroborate] {len(indexed)} cards indexed, "
          f"{len(done_ids)} signals done, {len(todo_cards)} to fetch",
          flush=True)
    if todo_cards:
        batch = todo_cards[:args.signals_batch]
        # per-card checkpointing (on_record fires the moment each record
        # exists — a mid-batch kill loses NOTHING; an IO-failed record
        # simply isn't in the file → retried next run. The returned recs
        # are for counting only; NO batch append (would duplicate).
        with open(sig_path, "a", encoding="utf-8") as sf:

            def _checkpoint(rec: dict) -> None:
                sf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                sf.flush()

            recs = provider.fetch_signals(batch, on_record=_checkpoint)
        n_ok = sum(1 for r in recs if r["status"] == "matched")
        n_blocked = sum(1 for r in recs if r["status"] == "blocked")
        remaining = len(todo_cards) - len(recs)
        print(f"[corroborate] signals +{len(recs)} "
              f"({n_ok} matched, {n_blocked} blocked, "
              f"{remaining} cards remaining)", flush=True)

    # ── join preview ──
    signals = _load_jsonl(sig_path)
    matched = corroborate.join_by_req_id(signals, req_ids)
    title_matched = corroborate.join_by_title(
        [s for s in signals if s.get("status") == "matched"],
        {r["reqId"]: r["title"] for r in rows}, args.company)
    only_title = {k: v for k, v in title_matched.items() if k not in matched}
    print(f"[corroborate] join preview: {len(matched)} by reqId, "
          f"+{len(only_title)} by title, {len(signals)} signals total",
          flush=True)
    return 0

# ── phase: finish — CSV v2 + JSON v2 + validation report ─────────────────

def _state_codes(locations: list[str]) -> str:
    """Extract 'US, XX,' state codes → 'CA;NC;TX' ("" when none)."""
    codes = []
    for loc in locations:
        m = _STATE_RE.match(loc.strip())
        if m and m.group(1) not in codes:
            codes.append(m.group(1))
    return ";".join(codes)


def _derive_csv_row(r: dict, det: dict, sig: Optional[dict],
                    company: str, dump_date: str,
                    first_seen: str) -> dict:
    info = det.get("info") or {}
    primary = info.get("location") or r.get("locationsText") or ""
    additional = info.get("additionalLocations") or []
    locations = ([primary] if primary else []) + list(additional)
    if not locations and r.get("locationsText"):
        locations = [r["locationsText"]]
    today = date.today()
    start = info.get("startDate") or ""
    age = ""
    if start:
        try:
            age = (today - date.fromisoformat(start)).days
        except ValueError:
            pass
    req_year = ""
    m = re.match(r"[A-Za-z]+(\d{4})", r["reqId"] or "")
    if m:
        req_year = m.group(1)
    desc_html = info.get("jobDescription") or ""
    desc_text = html_to_text(desc_html)
    country = (info.get("country") or {}).get("descriptor", "")
    detail_error = det.get("error") or ""
    # detail-unreachable rows: locations/nLocations are UNKNOWN — surface
    # it honestly instead of nLocations=1 reading as "single location"
    # (audit S7-A1 F1: 2 rows shipped locations="6 Locations" nLocations=1)
    if detail_error and not info:
        locations = [r.get("locationsText") or ""]
    row = {
        "reqId": r["reqId"],
        "title": info.get("title") or r.get("title") or "",
        "company": company,
        "hiringOrg": det.get("hiringOrg") or "",
        "timeType": info.get("timeType") or "",
        "postedOn": r.get("postedOn") or info.get("postedOn") or "",
        "startDate": start,
        "postingAgeDays": age,
        "reqYear": req_year,
        "primaryLocation": primary,
        "nLocations": len(locations),
        "locations": "; ".join(locations),
        "remoteFlag": any("remote" in loc.lower() for loc in locations),
        "stateCodes": _state_codes(locations),
        "country": country,
        "questionnaire": bool(info.get("questionnaireId")),
        "similarJobsCount": det.get("similarJobsCount") or 0,
        "description": desc_text,
        "descriptionLength": len(desc_text),
        "url": r.get("url") or info.get("externalUrl") or "",
        "detailError": detail_error,
    }
    if sig:
        li_date = sig.get("linkedin_posted_date") or ""
        delta = ""
        if li_date and start:
            try:
                delta = (date.fromisoformat(li_date)
                         - date.fromisoformat(start)).days
            except ValueError:
                pass
        row.update({
            "linkedinUrl": sig.get("linkedin_url") or "",
            "linkedinPostedDate": li_date,
            "numApplicants": sig.get("num_applicants"),
            "applicantLabel": sig.get("applicants_label") or "",
            "dateDeltaDays": delta,
            "corroborationStatus": sig.get("status") or "",
            "matchMethod": sig.get("match_method") or "",
            "corroboratedOn": sig.get("fetched_at") or "",
        })
    else:
        row.update({
            "linkedinUrl": "", "linkedinPostedDate": "",
            "numApplicants": "", "applicantLabel": "", "dateDeltaDays": "",
            "corroborationStatus": corroborate.STATUS_NOT_CHECKED,
            "matchMethod": "", "corroboratedOn": "",
        })
    row["firstSeenDate"] = first_seen
    row["dumpDate"] = dump_date
    return row


def phase_finish(args, out: Path) -> int:
    list_path = out.with_suffix(".list.jsonl")
    if not list_path.exists():
        print("no list file — run --phase list first")
        return 2
    rows = _load_jsonl(list_path)
    details: dict[str, dict] = {}
    for d in _load_jsonl(out.with_suffix(".details.jsonl")):
        details[d["reqId"]] = d
    signals = _load_jsonl(out.with_suffix(".signals.jsonl"))
    req_ids = {r["reqId"] for r in rows}

    # index completeness (B5): when the index finished, an unmatched
    # posting is genuinely no_match — "not cross-posted" must never
    # masquerade as "not checked" (audit S7-A2 D)
    index_done = False
    meta_path = out.with_suffix(".li_index.meta.json")
    if meta_path.exists():
        try:
            index_done = bool(
                json.loads(meta_path.read_text(encoding="utf-8"))
                .get("done"))
        except json.JSONDecodeError:
            pass

    # join: reqId exact first, then title fallback (1:1 greedy,
    # matchMethod recorded). req_dates = detail startDates for the
    # proximity disambiguation.
    matched = [s for s in signals if s.get("status") == "matched"]
    blocked = [s for s in signals if s.get("status") == "blocked"]
    req_dates = {rid: (details.get(rid) or {}).get("info", {}).get(
        "startDate") or "" for rid in req_ids}
    sig_by_req = corroborate.join_by_req_id(matched, req_ids)
    title_join = corroborate.join_by_title(
        matched, {r["reqId"]: r["title"] for r in rows}, args.company,
        req_dates={k: v for k, v in req_dates.items() if v})
    # blocked cards: title-join so blocked reqs read `blocked` (B5)
    blocked_join = corroborate.join_by_title(
        blocked, {r["reqId"]: r["title"] for r in rows}, args.company,
        req_dates={k: v for k, v in req_dates.items() if v})
    matched_rows = 0
    csv_rows: list[dict] = []
    enriched_rows: list[dict] = []
    dump_date = date.today().isoformat()
    first_seen = dump_date          # one-shot dump: first sighting = today
    for r in rows:
        det = details.get(r["reqId"]) or {}
        sig = sig_by_req.get(r["reqId"])
        if sig:
            sig = dict(sig)
            sig.setdefault("match_method", "reqId")
        else:
            sig = title_join.get(r["reqId"])
            if sig:
                sig = dict(sig)
                sig["match_method"] = "title"
            elif r["reqId"] in blocked_join:
                # posting maps to a card whose fetch was blocked — status
                # `blocked`, retryable, never "no_match" (B5)
                sig = dict(blocked_join[r["reqId"]])
            elif index_done:
                # index exhausted + no card for this posting = honestly
                # not cross-posted (within the indexed window)
                sig = {"status": "no_match"}
            else:
                sig = None
        if sig and not sig.get("fetched_at"):
            sig["fetched_at"] = sig.get("fetched_at") or ""
        row = _derive_csv_row(r, det, sig, r.get("company") or args.company,
                              dump_date, first_seen)
        csv_rows.append(row)
        enriched = dict(r)
        enriched["detail"] = det.get("info") or None
        enriched["hiringOrg"] = det.get("hiringOrg")
        enriched["similarJobsCount"] = det.get("similarJobsCount") or 0
        enriched["error"] = det.get("error")
        if sig:
            enriched["signals"] = sig
        enriched_rows.append(enriched)
        if det.get("info"):
            matched_rows += 1

    # JSON v2 (machine consumption — full nested)
    out.with_suffix(".json").write_text(json.dumps({
        "board": args.board,
        "company": args.company,
        "country": args.country,
        "time_type": args.time_type,
        "dumpDate": dump_date,
        "count": len(rows),
        "details_merged": matched_rows,
        "signals_matched": len(sig_by_req) + len(
            [k for k in title_join if k not in sig_by_req]),
        "rows": enriched_rows,
    }, indent=1, ensure_ascii=False), encoding="utf-8")

    # CSV v2 (user-facing) — utf-8-sig: Excel-safe bullets/newlines
    csv_path = out.with_suffix(".csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for row in csv_rows:
            w.writerow(row)

    # validation report
    n_desc = sum(1 for r in csv_rows if r["description"])
    n_loc = sum(1 for r in csv_rows if r["locations"]
                and "Locations" not in str(r["locations"])[:12])
    n_date = sum(1 for r in csv_rows if r["startDate"])
    n_sig = sum(1 for r in csv_rows
                if r["corroborationStatus"] == "matched")
    n_sig_blocked = sum(1 for r in csv_rows
                        if r["corroborationStatus"] == "blocked")
    n_sig_nomatch = sum(1 for r in csv_rows
                        if r["corroborationStatus"] == "no_match")
    n_sig_notchecked = sum(1 for r in csv_rows
                           if r["corroborationStatus"] == "not_checked")
    uniq = len({r["reqId"] for r in csv_rows})
    loc_sum = sum(r["nLocations"] for r in csv_rows)
    report = [
        f"rows: {len(csv_rows)} (unique reqIds: {uniq})",
        f"details merged: {matched_rows}",
        f"descriptions present: {n_desc}",
        f"locations enumerated (not 'N Locations'): {n_loc}",
        f"startDate present: {n_date}",
        f"signals: matched={n_sig} no_match={n_sig_nomatch} "
        f"blocked={n_sig_blocked} not_checked={n_sig_notchecked}",
        f"location entries total (sum nLocations): {loc_sum}",
    ]
    report_text = "\n".join(report)
    _atomic_write_text(out.with_suffix(".report.txt"), report_text)
    print(f"[finish] wrote {len(csv_rows)} rows ({matched_rows} details):")
    print(f"  {out.with_suffix('.json')}")
    print(f"  {csv_path}")
    print(report_text)
    missing = len(rows) - matched_rows
    return 1 if (missing and args.require_details) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", default="nvidia|wd5|nvidiaexternalcareersite",
                    help="'tenant|instance|site' (workday seed format)")
    ap.add_argument("--company", default="NVIDIA",
                    help="company display name (corroboration target)")
    ap.add_argument("--label", default="nvidia_us_fulltime",
                    help="output filename prefix")
    ap.add_argument("--country", default="United States",
                    help="locationHierarchy1 facet value (empty = all)")
    ap.add_argument("--time-type", default="Full time",
                    help="timeType facet value (empty = all)")
    ap.add_argument("--location", default="United States",
                    help="LinkedIn corroboration search location")
    ap.add_argument("--phase", default="finish",
                    choices=["list", "details", "corroborate", "finish"])
    ap.add_argument("--details-batch", type=int, default=150)
    ap.add_argument("--require-details", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--detail-sleep", type=float, default=0.25)
    ap.add_argument("--corroborate-index", action="store_true",
                    help="(corroborate phase) refresh the LinkedIn index")
    ap.add_argument("--li-index-pages", type=int, default=10,
                    help="LinkedIn index pages per invocation (10 cards/pg)")
    ap.add_argument("--li-index-cards", type=int, default=100)
    ap.add_argument("--signals-batch", type=int, default=40,
                    help="LinkedIn detail fetches per invocation")
    ap.add_argument("--provider", default="linkedin",
                    choices=sorted(corroborate.PROVIDERS))
    ap.add_argument("--out-dir", default=str(
        REPO / "ingest" / "data" / "workday"))
    args = ap.parse_args()

    out = Path(args.out_dir) / args.label
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.phase == "list":
        return phase_list(args, out)
    if args.phase == "details":
        return phase_details(args, out)
    if args.phase == "corroborate":
        return phase_corroborate(args, out)
    return phase_finish(args, out)


if __name__ == "__main__":
    sys.exit(main())
