#!/usr/bin/env python3
"""Board-watch — daily GHA watch over Workday CXS boards (durable pattern #3).

The productized watch layer on top of scripts/board_dump.py (design-board-v2
.md D4): the dump is a one-shot exhaustive snapshot; the WATCH is the daily
diff engine that turns it into an alerting pipeline. Reference instances of
the durable pattern: scripts/gha_drain.py (drain) + gha_name_backfill.py.

Flow per watch (config: ingest/data/board_watch/config.json — one entry per
board; a new company is ONE JSON line):
  1. LIST current postings — server-facet filtered (country/timeType),
     total-driven pagination, dedup, wrap-guards (the board_dump list
     machinery, in-memory). ~72 pages ≈ 60s for NVIDIA US full-time.
  2. DIFF vs state ({label}.state.jsonl): new = current − state,
     gone = state − current. B1 GUARD: gone is computed ONLY when the list
     completed — a partial list never marks postings as removed.
  3. ENRICH new postings only, bounded (DETAILS_MAX/run): full detail
     payload (description, startDate, ALL locations) + LinkedIn
     corroboration (bounded index over recent cards + signals for matched)
     — the same v2 field quality as the dump, appended to
     {label}.newposts.jsonl (the durable "new postings" feed).
  4. STATE update: new lines appended, compacted rewrite (first_seen kept,
     last_seen=today for all still-current postings).
  5. ALERT: digest → appended to {label}.alerts.log (committed — durable
     alert history, works with zero configured notification channels) +
     $GITHUB_STEP_SUMMARY (the Actions UI) + Telegram via
     alerts.send_telegram when TELEGRAM_BOT_TOKEN/CHAT_ID ever appear in
     ingest/.env (never raises; additive-only by design).

Budget guards (the 2026-09-09 GHA-burn lesson — SKILL §A3):
  - DETAILS_MAX per run (default 40) — the enrich backlog self-retriggers
    only while WATCH_RESULT=backlog, and the chain is capped at
    LEGS_MAX_PER_DAY legs/day (legs meta is date-keyed; excess work waits
    for tomorrow's cron — never an unbounded retrigger loop).
  - BUDGET_SECONDS wall-clock guard (default 240) — stop enriching in an
    orderly way, checkpoint state, report backlog.

Machine-readable result (for the workflow):
  - appends WATCH_RESULT=complete|backlog|failed to $GITHUB_ENV
  - always exits 0 (the workflow branches on WATCH_RESULT, not rc)

Seeding (B3, review addendum): --seed-from <list.jsonl> [--first-seen DATE]
imports a full board_dump list into the state so the FIRST watch run does
not alert on ~1,400 pre-existing postings.

Usage:
  python3 scripts/gha_board_watch.py                      # all watches
  python3 scripts/gha_board_watch.py --label nvidia_us_fulltime
  python3 scripts/gha_board_watch.py --seed-from ingest/data/workday/\
nvidia_us_fulltime.list.jsonl --first-seen 2026-09-10
Env knobs: WATCH_BUDGET_SECONDS, WATCH_DETAILS_MAX, WATCH_LEGS_MAX_PER_DAY,
  WATCH_LI_INDEX_PAGES, WATCH_DETAIL_SLEEP, WATCH_LIST_SLEEP.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "ingest"))

from jobsearch.config import Config, load_config  # noqa: E402
from jobsearch.htmltext import html_to_text  # noqa: E402
from jobsearch.sources import workday  # noqa: E402
from jobsearch import corroborate  # noqa: E402

WATCH_DIR = REPO / "ingest" / "data" / "board_watch"
CONFIG_PATH = WATCH_DIR / "config.json"

# ── budget guards (env-overridable) ──────────────────────────────────────
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


BUDGET_SECONDS = _int_env("WATCH_BUDGET_SECONDS", 240)
DETAILS_MAX = _int_env("WATCH_DETAILS_MAX", 40)
LEGS_MAX_PER_DAY = _int_env("WATCH_LEGS_MAX_PER_DAY", 6)
LI_INDEX_PAGES = _int_env("WATCH_LI_INDEX_PAGES", 10)
RECORROBORATE_DAYS = _int_env("WATCH_RECORROBORATE_DAYS", 2)
def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


DETAIL_SLEEP = _float_env("WATCH_DETAIL_SLEEP", 0.25)
LIST_SLEEP = _float_env("WATCH_LIST_SLEEP", 0.2)

_digest_char_limit = 3500   # Telegram hard cap is 4096; leave headroom


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _append_jsonl(path: Path, recs: list[dict]) -> None:
    if not recs:
        return
    with open(path, "a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:                       # corrupt-tail tolerance (crash mid-append)
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ── step 1: list (the SHARED CXS primitive — S7-B1 SA-1: this loop was
# copy #3, and the timeType-facet omission shipped from that drift) ──────
def current_postings(board_spec: str, country: str, time_type: str,
                     cfg: Config) -> tuple[dict[str, dict], bool]:
    """One full listing pass → {reqId: row} + complete flag (B1: the diff
    only trusts 'gone' when the listing completed naturally)."""
    try:
        rows, meta = workday.list_board(
            board_spec, country=country or None,
            time_type=time_type or None, cfg=cfg,
            sleep_s=LIST_SLEEP, progress_every=20,
            progress_label="watch")
    except ValueError as exc:
        print(f"[watch] facet error on {board_spec}: {exc}", file=sys.stderr)
        return {}, False
    except Exception as exc:                    # page-0 failure
        print(f"[watch] list page-0 failed on {board_spec}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
    return rows, bool(meta.get("complete"))


# ── step 3: enrich new postings (detail + LinkedIn signals, bounded) ─────
def _locations_of(info: dict, row: dict) -> list[str]:
    locs: list[str] = []
    if info:
        locs.append(info.get("location") or "")
        locs += [str(x) for x in (info.get("additionalLocations") or [])]
    locs = [x for x in locs if x]
    if not locs:
        locs = [row.get("locationsText") or ""]
    return [x for x in locs if x]


def enrich_new(new_rows: list[dict], board_spec: str, company: str,
               cfg: Config, deadline: float,
               prior_feed: Optional[dict] = None) -> list[dict]:
    """Bounded detail + corroboration pass over the new postings.

    Returns enriched records for {label}.newposts.jsonl (v2 field quality:
    full locations, startDate, clean-text description, signals). Enrichment
    is best-effort — an unreachable detail still yields a record with
    error status; the record itself is what makes the alert feed durable.
    """
    board = workday.parse_board(board_spec)
    workday._DETAIL_SLEEP_S = DETAIL_SLEEP
    out: list[dict] = []
    todo = new_rows[:DETAILS_MAX]
    for i, r in enumerate(todo, 1):
        rec: dict = {
            "reqId": r["reqId"], "title": r.get("title") or "",
            "company": company, "first_seen": date.today().isoformat(),
            "locationsText": r.get("locationsText") or "",
            "postedOn": r.get("postedOn") or "",
            "url": r.get("url") or "",
        }
        payload = workday.detail_payload(board, r.get("externalPath", ""), cfg)
        info = (payload or {}).get("jobPostingInfo") or {}
        if info:
            rec["locations"] = _locations_of(info, r)
            rec["startDate"] = info.get("startDate") or ""
            rec["description"] = html_to_text(info.get("jobDescription") or "")
            rec["timeType"] = info.get("timeType") or ""
            rec["hiringOrg"] = (payload.get("hiringOrganization") or {}).get("name")
            rec["externalUrl"] = info.get("externalUrl") or rec["url"]
        else:
            rec["error"] = "detail_unreachable"
            # 3-strike (B6, S7-V1): a transient detail outage must not
            # permanently degrade the record — attempts accumulate in the
            # feed; the recovery query retries error rows until 3.
            prev = (prior_feed or {}).get(r["reqId"]) or {}
            rec["attempts"] = int(prev.get("attempts") or 0) + 1
        out.append(rec)
        if time.monotonic() > deadline:
            print(f"[watch] budget stop at {i}/{len(todo)} enrichments",
                  flush=True)
            break
        time.sleep(DETAIL_SLEEP)
    return out


def corroborate_new(new_rows: list[dict], company: str, cfg: Config,
                    deadline: float) -> dict[str, dict]:
    """LinkedIn signals for the new postings (bounded index + bounded
    signals; circuit-breakered inside the provider). Returns
    {reqId: signal_record} — reqId-exact when the description carries one,
    title-match otherwise. A blocked index is a no-op (B5: recorded, not
    raised — next run retries)."""
    if not new_rows:
        return {}
    if time.monotonic() > deadline:
        print("[watch] budget exhausted before corroboration — skipped",
              flush=True)
        return {}
    provider = corroborate.get_provider("linkedin", cfg=cfg)
    try:
        cards, _, _ = provider.index_cards(
            company=company, location="United States",
            max_pages=LI_INDEX_PAGES, max_cards=LI_INDEX_PAGES * 10,
            start_offset=0)
    except corroborate.CorroborationBlocked as exc:
        print(f"[watch] LinkedIn index blocked: {exc}", file=sys.stderr)
        return {}
    if not cards:
        return {}
    # match cards → new postings on the normalized title key (the LinkedIn
    # index covers the ~100 most recent cards; new postings sort to the top
    # under sortBy=DD — the right window for a daily watch)
    from jobsearch.sources.linkedin_guest import job_key
    new_by_key: dict[str, list[str]] = {}
    for r in new_rows:
        k = job_key(r.get("title") or "", company)
        if k:
            new_by_key.setdefault(k, []).append(r["reqId"])
    matched: list[tuple[dict, list[str]]] = []
    for c in cards:
        k = job_key(c.get("title") or "", c.get("company") or "")
        reqs = new_by_key.get(k)
        if reqs:
            matched.append((c, reqs))
    if not matched:
        print(f"[watch] 0/{len(new_rows)} new postings cross-posted to "
              f"LinkedIn (of {len(cards)} recent cards)", flush=True)
        return {}
    signals = provider.fetch_signals([c for c, _ in matched][:DETAILS_MAX])
    sig_by_card = {str(s.get("linkedin_job_id")): s for s in signals
                   if s.get("status") == "matched"}
    # GREEDY 1:1 (S7-V1: this could fan out one card's applicants onto a
    # whole title family — the same N:1 the dump's join had): each card
    # serves at most ONE req; reqId-exact wins when the extracted reqId
    # is actually among THIS input's postings (no foreign reqIds).
    input_rids = {r["reqId"] for r in new_rows}
    out: dict[str, dict] = {}
    served_cards: set[str] = set()
    for c, reqs in matched:
        s = sig_by_card.get(str(c["id"]))
        if not s or str(c["id"]) in served_cards:
            continue
        jr = s.get("job_req_id")
        if jr:
            if jr in input_rids and jr not in out:
                out[jr] = s                   # reqId-exact
                served_cards.add(str(c["id"]))
            # jr NOT in input rids: this LinkedIn posting IS a different
            # requisition that merely shares the title family — serving
            # our rows with it would misattribute; skip the card entirely
            continue
        for rid in reqs:                      # title fallback, 1:1
            if rid in input_rids and rid not in out:
                out[rid] = s
                served_cards.add(str(c["id"]))
                break
    print(f"[watch] corroborated {len(out)}/{len(new_rows)} new postings "
          f"({len(cards)} cards indexed)", flush=True)
    return out


# ── step 4+5: state update + alert digest ────────────────────────────────
def format_digest(label: str, company: str, new_rows: list[dict],
                  enriched: list[dict], signals: dict[str, dict],
                  gone_rows: list[dict], current_count: int) -> str:
    """Plain-text digest → alerts.log + job summary (Telegram sends HTML
    from the same lines; chunking keeps every channel under its cap)."""
    today = date.today().isoformat()
    lines = [
        f"{company} board-watch {today}: +{len(new_rows)} new, "
        f"-{len(gone_rows)} gone ({current_count} active "
        f"[{label}])"]
    enr = {e["reqId"]: e for e in enriched}
    for r in new_rows[:25]:         # digest keeps 25 (S7-A3 J: an unseeded
        e = enr.get(r["reqId"]) or {}   # first run must not send 35 chunks)
        s = signals.get(r["reqId"]) or {}
        loc = "; ".join(e.get("locations") or [r.get("locationsText") or "?"])
        extra = ""
        if s.get("num_applicants"):
            extra = f" — {s['num_applicants']} applicants"
        age = e.get("startDate") or r.get("postedOn") or ""
        url = e.get("externalUrl") or r.get("url") or ""
        lines.append(f"NEW {r.get('title')} | {loc} | {age}{extra} | {url}")
    if len(new_rows) > 25:
        lines.append(f"NEW … and {len(new_rows) - 25} more "
                     "(newposts.jsonl)")
    for r in gone_rows[:20]:        # digest keeps 20; full list in state
        lines.append(f"GONE {r.get('title')} "
                     f"(first seen {r.get('first_seen')})")
    if len(gone_rows) > 20:
        lines.append(f"GONE … and {len(gone_rows) - 20} more (state file)")
    return "\n".join(lines)


def _send_alerts(label: str, digest: str, cfg: Config) -> None:
    """Durable-first alerting: alerts.log always; job summary always;
    Telegram when configured (chunked, never raises)."""
    with open(WATCH_DIR / f"{label}.alerts.log", "a",
              encoding="utf-8") as lf:
        lf.write(digest + f"\n({_now_iso()})\n\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"```\n{digest}\n```\n")
    import html as _html
    from jobsearch import alerts
    for i in range(0, len(digest), _digest_char_limit):
        chunk = _html.escape(digest[i:i + _digest_char_limit])
        alerts.send_telegram(f"<pre>{chunk}</pre>", cfg)


# ── orchestration ────────────────────────────────────────────────────────
def _legs_bump(label: str) -> int:
    """Date-keyed leg counter — the retrigger cap (GHA-burn lesson)."""
    meta = WATCH_DIR / f"{label}.legs.json"
    today = date.today().isoformat()
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        m = {}
    if m.get("date") != today:
        m = {"date": today, "legs": 0}
    m["legs"] = int(m.get("legs", 0)) + 1
    _atomic_write(meta, json.dumps(m))
    return m["legs"]


def run_watch(w: dict, cfg: Config) -> str:
    """One watch pass. Returns 'complete' | 'backlog' | 'failed'."""
    label = w["label"]
    state_path = WATCH_DIR / f"{label}.state.jsonl"
    leg = _legs_bump(label)
    print(f"[watch:{label}] leg {leg}/{LEGS_MAX_PER_DAY} today", flush=True)

    def _fail_summary(reason: str) -> None:
        """S7-A3 P1-1: a persistent failure must never run green — write
        the failure to the job summary AND the alerts log (durable)."""
        line = f"board-watch {label} FAILED: {reason}"
        print(f"[watch:{label}] {line}", file=sys.stderr, flush=True)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"\n**{line}**\n")
        with open(WATCH_DIR / f"{label}.alerts.log", "a",
                  encoding="utf-8") as lf:
            lf.write(f"{line}\n({_now_iso()})\n\n")

    try:
        current, list_complete = current_postings(
            w["board"], w.get("country", ""), w.get("time_type", ""), cfg)
    except Exception as exc:
        _fail_summary(f"list fetch crashed ({type(exc).__name__}: {exc})")
        return "failed"
    if not current and not list_complete:
        _fail_summary("LIST FAILED — nothing to diff; state untouched "
                      "(fail-safe)")
        return "failed"

    prior = {r["reqId"]: r for r in _load_jsonl(state_path)}
    if list_complete and not current and len(prior) >= 10:
        # a real 200-with-zero on a board that had >=10 postings yesterday
        # is almost certainly an API anomaly — never mass-mark gone (B1
        # spirit: a suspicious list is not a trustworthy list)
        _fail_summary(f"EMPTY-LIST ANOMALY (had {len(prior)} postings "
                      "yesterday) — state untouched")
        return "failed"
    new_rows = [current[rid] for rid in current if rid not in prior]
    # B1: only a completed listing may mark postings as gone
    gone_rows = [prior[rid] for rid in prior
                 if list_complete and rid not in current]
    print(f"[watch:{label}] {len(current)} on board, "
          f"+{len(new_rows)} new, -{len(gone_rows)} gone "
          f"(list_complete={list_complete})", flush=True)

    # P0-1 recovery (audit S7-A2 C / S7-A3 P0-1): postings already in
    # state but never enriched (past DETAILS_MAX / budget stop / crash)
    # are re-fed into enrichment until they land in newposts.jsonl. The
    # digest still reports only the FIRST sighting — recovery is silent.
    feed_path = WATCH_DIR / f"{label}.newposts.jsonl"
    enriched_feed: dict[str, dict] = {}
    for e in _load_jsonl(feed_path):
        enriched_feed[e["reqId"]] = e            # last line wins
    today_iso = date.today().isoformat()
    def _recoverable(rid: str, p: dict) -> bool:
        feed_rec = enriched_feed.get(rid)
        if feed_rec is None:
            # never enriched: today's rows + crash survivors (flagged)
            return bool(p.get("needs_enrich")
                        or p.get("first_seen") == today_iso)
        # enriched-with-error: retry until 3 strikes (B6)
        return bool(feed_rec.get("error")) \
            and int(feed_rec.get("attempts") or 0) < 3

    backlog_rows = [p for rid, p in prior.items()
                    if rid in current and _recoverable(rid, p)]
    if backlog_rows:
        print(f"[watch:{label}] backlog recovery: {len(backlog_rows)} "
              "state postings awaiting enrichment", flush=True)

    # enrich the NEW postings (bounded; budget-guarded)
    deadline = time.monotonic() + BUDGET_SECONDS
    enrich_input = (new_rows + backlog_rows)[:DETAILS_MAX * 2]         if backlog_rows else new_rows
    enriched = enrich_new(enrich_input, w["board"], w["company"], cfg,
                          deadline, prior_feed=enriched_feed)
    signals = corroborate_new(enrich_input, w["company"], cfg, deadline)
    for e in enriched:
        s = signals.get(e["reqId"])
        if s:
            e["signals"] = s
    _append_jsonl(WATCH_DIR / f"{label}.newposts.jsonl", enriched)

    # cross-post lag pass: LinkedIn often cross-posts 1-2 days AFTER the
    # Workday listing — postings first-seen within RECORROBORATE_DAYS that
    # still carry no signals get another bounded corroboration attempt.
    # The feed is append-only: an updated record supersedes the older line
    # for the same reqId (consumers read the LAST line per reqId).
    if deadline - time.monotonic() > 30:      # only when budget remains
        try:
            horizon = date.today().toordinal() - RECORROBORATE_DAYS
            recent: dict[str, dict] = {}
            for r in _load_jsonl(WATCH_DIR / f"{label}.newposts.jsonl"):
                fs = r.get("first_seen") or ""
                try:
                    ok = date.fromisoformat(fs).toordinal() >= horizon
                except ValueError:
                    ok = False
                if ok and not r.get("signals"):
                    recent[r["reqId"]] = r          # last line wins
            for rid in signals:                    # just-corroborated skip
                recent.pop(rid, None)
            if recent:
                sigs2 = corroborate_new(list(recent.values()),
                                        w["company"], cfg, deadline)
                updates = []
                for rid, s in sigs2.items():
                    r = recent.get(rid)
                    if r:
                        r2 = dict(r)
                        r2["signals"] = s
                        updates.append(r2)
                if updates:
                    _append_jsonl(
                        WATCH_DIR / f"{label}.newposts.jsonl", updates)
                    signals.update(sigs2)
                    print(f"[watch:{label}] cross-post-lag pass: "
                          f"+{len(updates)} newly corroborated",
                          flush=True)
        except Exception as exc:   # additive feature — never fatal
            print(f"[watch:{label}] re-corroborate pass skipped: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)

    # state: compact rewrite (first_seen preserved; last_seen=today for
    # everything still current; gone rows dropped from state — their
    # history lives in newposts.jsonl + alerts.log). B1: when the listing
    # is INCOMPLETE, prior rows absent from the partial list are kept
    # verbatim (unknown fate ≠ gone — a dropped row would re-alert as
    # "new" on the next complete run).
    today = date.today().isoformat()
    enr_by_rid = {e["reqId"]: e for e in enriched if e.get("reqId")}
    need_set = {r["reqId"] for r in new_rows}
    need_set.update(r["reqId"] for r in backlog_rows)
    keep = []
    for rid, r in current.items():
        p = prior.get(rid) or {}
        rec = {
            "reqId": rid, "title": r.get("title") or "",
            "first_seen": p.get("first_seen") or today,
            "last_seen": today,
            "last_postedOn": r.get("postedOn") or "",
            "last_startDate": enr_by_rid.get(rid, {}).get("startDate")
            or p.get("last_startDate") or "",
        }
        if rid in need_set and rid not in enr_by_rid:
            rec["needs_enrich"] = True    # survives crash / leg cap
        keep.append(rec)
    if not list_complete:
        keep.extend(p for rid, p in prior.items() if rid not in current)
        keep.sort(key=lambda r: r["reqId"])
    _atomic_write(state_path, "\n".join(
        json.dumps(r, ensure_ascii=False) for r in keep) + "\n")

    digest = format_digest(label, w["company"], new_rows, enriched,
                           signals, gone_rows, len(current))
    if new_rows or gone_rows:
        _send_alerts(label, digest, cfg)
    print(f"[watch:{label}] digest:\n{digest}", flush=True)

    # backlog = postings (new OR recovered) still awaiting enrichment
    # (DETAILS_MAX/budget bound) — drives the self-retrigger
    need = {r["reqId"] for r in new_rows}
    need.update(r["reqId"] for r in backlog_rows)
    enriched_rids = {e["reqId"] for e in enriched if e.get("reqId")}
    unenriched = len(need - enriched_rids)
    if unenriched > 0 and leg < LEGS_MAX_PER_DAY:
        print(f"[watch:{label}] backlog: {unenriched} new postings "
              "awaiting enrichment — will self-retrigger", flush=True)
        return "backlog"
    if unenriched > 0:
        print(f"[watch:{label}] {unenriched} unenriched but leg cap "
              f"reached — tomorrow's cron continues", flush=True)
    return "complete"


def seed_state(w: dict, list_path: Path, first_seen: str) -> None:
    """B3: import a full board_dump list into the state (the first watch
    run must not alert on ~1,400 pre-existing postings)."""
    rows = _load_jsonl(list_path)
    state_path = WATCH_DIR / f"{w['label']}.state.jsonl"
    if state_path.exists():
        print(f"[watch:{w['label']}] state exists — seed refused "
              "(delete the state file to force)", file=sys.stderr)
        return
    today = date.today().isoformat()
    keep = [{
        "reqId": r["reqId"], "title": r.get("title") or "",
        "first_seen": first_seen or today,
        "last_seen": first_seen or today,
        "last_postedOn": r.get("postedOn") or "",
        "last_startDate": "",
    } for r in rows]
    _atomic_write(state_path, "\n".join(
        json.dumps(r, ensure_ascii=False) for r in keep) + "\n")
    print(f"[watch:{w['label']}] seeded {len(keep)} postings "
          f"(first_seen={first_seen or today})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--label", help="run a single watch by label")
    ap.add_argument("--seed-from", help="list.jsonl to seed the state from")
    ap.add_argument("--first-seen", help="date for --seed-from (default today)")
    args = ap.parse_args()

    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps({"watches": []}, indent=1) + "\n",
                               encoding="utf-8")
        print(f"[watch] no config — created empty {CONFIG_PATH}")
        return 0
    try:
        watches = json.loads(
            CONFIG_PATH.read_text(encoding="utf-8")).get("watches", [])
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[watch] config unreadable: {exc}", file=sys.stderr)
        return 0                       # exit-0 contract; tomorrow retries
    if args.label:
        watches = [w for w in watches if w["label"] == args.label]
        if not watches:
            print(f"[watch] unknown label {args.label!r}")
            return 2
    if args.seed_from and len(watches) != 1:
        print("[watch] --seed-from requires --label (seeding EVERY watch "
              "from one list would cross-contaminate state)",
              file=sys.stderr)
        return 2

    cfg = load_config()
    results: dict[str, str] = {}
    for w in watches:
        if args.seed_from:
            seed_state(w, Path(args.seed_from), args.first_seen or "")
            continue
        try:
            results[w["label"]] = run_watch(w, cfg)
        except Exception as exc:      # fail-safe: one watch never kills all
            print(f"[watch:{w['label']}] FAILED: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a", encoding="utf-8") as f:
                    f.write(f"\n**board-watch {w['label']} CRASHED: "
                            f"{type(exc).__name__}: {exc}**\n")
            results[w["label"]] = "failed"

    gha_env = os.environ.get("GITHUB_ENV")
    if gha_env:
        vals = set(results.values())
        # failed is LOUDER than backlog (S7-V1 P3-1): a dead watch must
        # never be masked by another watch's pending work
        overall = ("failed" if "failed" in vals
                   else "backlog" if "backlog" in vals else "complete")
        with open(gha_env, "a", encoding="utf-8") as f:
            f.write(f"WATCH_RESULT={overall}\n")
            f.write(f"WATCH_RESULTS_JSON={json.dumps(results)}\n")
    print(f"[watch] results: {results or 'seeded'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
