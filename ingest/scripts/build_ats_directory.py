#!/usr/bin/env python3
"""Build the slug→ATS directory (Step E, methodology Sprint-1 core).

Seeds company slugs from the external Feashliaa/job-board-aggregator
dataset (the "crown jewel" — 3.2k Ashby + 8.3k Greenhouse + 4.4k Lever +
12.9k Workday slug strings) plus the verified slugs already curated in
this repo's config, then probes each slug against its ATS's public
endpoint to record live job counts.

Output: ingest/data/ats_directory.db
    directory(ats, slug, company, job_count, status, attempts, last_probed_at)
    status ∈ pending | live | dead | error

Design (per HANDOFF Step E):
  - resumable: 'pending' rows are claimed in batches; every batch commits
    (checkpointing — a killed run loses nothing)
  - throttled: per-host pacing via jobsearch.net DNS cache + a per-request
    sleep; Personio-style global courtesy (this is 500–2,500+ requests)
  - split-chunk friendly: `--batch N` probes N slugs then exits cleanly,
    so it fits the 2-minute bash window (run repeatedly to drain)
  - verification-first: probes use the SAME endpoints the adapters use

Usage:
  python3 build_ats_directory.py --seed          # load seeds, create DB
  python3 build_ats_directory.py --batch 60 --ats greenhouse
  python3 build_ats_directory.py --stats
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import quote

# repo layout: ingest/scripts/build_ats_directory.py
HERE = Path(__file__).resolve().parent
INGEST = HERE.parent
sys.path.insert(0, str(INGEST))

from jobsearch.net import install_dns_cache  # noqa: E402

SEED_DIR = INGEST / "data" / "ats_seed"
DB_PATH = INGEST / "data" / "ats_directory.db"
HEARTBEAT = INGEST / "data" / "ats_directory_heartbeat.txt"

# A fresh daemon heartbeat + this env marker = invoked BY the prober
# daemon (which serializes its own batches); anything else racing a live
# daemon would double-probe rows and double-increment `attempts`.
_DAEMON_ENV = "ATS_PROBER_DAEMON"
_HEARTBEAT_FRESH_S = 600

import requests  # noqa: E402

UA = {"User-Agent": "jobsearch/1.0 (ats-directory-builder)"}
_TIMEOUT = 15

# A slug is probed at most this many times in its lifetime (CodeRabbit
# round-2 finding): without a persisted counter, an 'error' row would be
# re-claimed every hour forever. 'error' is transient by definition — after
# _MAX_ATTEMPTS probes it stays as-is for manual triage.
_MAX_ATTEMPTS = 2


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS directory (
            ats TEXT NOT NULL,
            slug TEXT NOT NULL,
            company TEXT DEFAULT '',
            job_count INTEGER,
            status TEXT DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_probed_at TEXT,
            PRIMARY KEY (ats, slug)
        )""")
    # migration for pre-attempts databases (idempotent)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(directory)")}
    if "attempts" not in cols:
        conn.execute("ALTER TABLE directory ADD COLUMN attempts "
                     "INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    return conn


# ── seed loading ─────────────────────────────────────────────────────────────

def seed(conn: sqlite3.Connection) -> None:
    """Load slug seeds into pending rows (idempotent).
    [S50-T2] the 7-list import (DESIGN-S50 §2-T2.6): the 3 originals +
    bamboohr + icims + paylocity (dict shape: guid keys, company names
    from the seed's `name` — RA4's CASE keeps them through probes) +
    the workday triples. Provenance ledger: ats_seed/README-S50.md."""
    # (ats, filename, company-from) — company extractor per list shape
    seeds = [
        ("ashby", "ashby_companies.json", None),
        ("greenhouse", "greenhouse_companies.json", None),
        ("lever", "lever_companies.json", None),
        ("bamboohr", "bamboohr_companies.json", None),
        ("icims", "icims_companies.json", None),
        ("paylocity", "paylocity_companies_clean.json", "name"),
        ("workday", "workday_companies.json", None),
    ]
    total = 0
    for ats, fname, company_key in seeds:
        path = SEED_DIR / fname
        if not path.exists():
            print(f"[seed] missing {path} — download from "
                  f"Feashliaa/job-board-aggregator data/{fname}")
            continue
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            print(f"[seed] {ats}: unreadable JSON in {path}: {exc}")
            continue
        if isinstance(raw, dict):
            raw = list(raw.keys())
        if not isinstance(raw, list):
            print(f"[seed] {ats}: unexpected JSON shape in {path}")
            continue
        rows: list[tuple[str, str]] = []
        skipped = 0
        for ent in raw:
            # [RA17] the paylocity class: a list of DICTS (guid/name/jobs)
            # — the old string filter would silently seed ZERO rows (the
            # silent-no-op zombie class; a crash would have been caught).
            if isinstance(ent, dict):
                guid = ent.get("guid")
                if not isinstance(guid, str) or not guid.strip():
                    skipped += 1
                    continue
                slug = guid.strip()
                company = (ent.get(company_key) or "").strip() \
                    if company_key else ""
            elif isinstance(ent, str) and ent.strip():
                slug = ent.strip()
                company = ""
            else:
                skipped += 1
                continue
            rows.append((slug, company))
        if skipped:
            print(f"[seed] {ats}: skipped {skipped} unusable entries")
        # de-dupe + skip already-present rows
        existing = {r[0] for r in conn.execute(
            "SELECT slug FROM directory WHERE ats = ?", (ats,))}
        fresh = [(ats, s, c) for s, c in dict.fromkeys(rows)
                 if s and s not in existing]
        if fresh:
            conn.executemany(
                "INSERT OR IGNORE INTO directory (ats, slug, company) "
                "VALUES (?, ?, ?)",
                fresh)
            conn.commit()          # per-ATS commit; never leave an open
                                    # snapshot while the daemon holds writes
        total += len(fresh)
        print(f"[seed] {ats}: +{len(fresh)} new slugs "
              f"({len(existing)} already present)")
    # curated SmartRecruiters slugs (verified in this repo's config; no
    # upstream list exists — RB7 measured SR has no cross-tenant search)
    conn.executemany(
        "INSERT OR IGNORE INTO directory (ats, slug) VALUES (?, ?)",
        [("smartrecruiters", s) for s in (
            "smartrecruiters", "averydennison", "geico", "bigcommerce",
            "wework", "yardi", "ServiceNow", "Visa", "Nvidia", "Ubisoft")])
    conn.commit()
    print(f"[seed] total new: {total}")


# ── per-ATS probes (same endpoints the adapters use) ────────────────────────

def probe_greenhouse(slug: str) -> tuple[str, int, str]:
    """(status, job_count, company). GET boards-api/v1/boards/{slug}/jobs."""
    try:
        r = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}/jobs",
            params={"content": "false"}, headers=UA, timeout=_TIMEOUT)
    except requests.RequestException:
        return ("error", 0, "")
    if r.status_code == 404:
        return ("dead", 0, "")
    if r.status_code != 200:
        return ("error", 0, "")
    try:
        data = r.json()
    except ValueError:
        return ("error", 0, "")
    jobs = data.get("jobs") or []
    company = data.get("meta") or {}
    return ("live", len(jobs), company.get("company_name") or "")


def probe_lever(slug: str) -> tuple[str, int, str]:
    try:
        r = requests.get(
            f"https://api.lever.co/v0/postings/{quote(slug)}",
            params={"mode": "json"}, headers=UA, timeout=_TIMEOUT)
    except requests.RequestException:
        return ("error", 0, "")
    if r.status_code == 404:
        return ("dead", 0, "")
    if r.status_code != 200:
        return ("error", 0, "")
    try:
        data = r.json()
    except ValueError:
        return ("error", 0, "")
    # Lever returns a list directly; company name isn't in the response.
    return ("live", len(data) if isinstance(data, list) else 0, "")


def probe_ashby(slug: str) -> tuple[str, int, str]:
    """HTML board size-threshold probe (same logic as the ashby adapter)."""
    try:
        r = requests.get(f"https://jobs.ashbyhq.com/{quote(slug)}",
                         headers=UA, timeout=_TIMEOUT)
    except requests.RequestException:
        return ("error", 0, "")
    if r.status_code == 404:
        return ("dead", 0, "")
    if r.status_code != 200:
        return ("error", 0, "")
    if len(r.content) < 10_000:
        return ("dead", 0, "")       # not-on-Ashby shell (~7.3 KB)
    # count postings via the appData marker occurrences
    text = r.text
    count = text.count('"title":')
    import re
    m = re.search(r'"name"\s*:\s*"([^"]{1,80})"', text[text.find('"organization"'):][:400])
    company = m.group(1) if m else ""
    return ("live", count, company)


def probe_smartrecruiters(slug: str) -> tuple[str, int, str]:
    try:
        r = requests.get(
            f"https://api.smartrecruiters.com/v1/companies/{quote(slug)}/postings",
            params={"limit": 1}, headers=UA, timeout=_TIMEOUT)
    except requests.RequestException:
        return ("error", 0, "")
    if r.status_code == 404:
        return ("dead", 0, "")
    if r.status_code != 200:
        return ("error", 0, "")
    try:
        data = r.json()
    except ValueError:
        return ("error", 0, "")
    return ("live", data.get("totalFound") or 0, slug)


# [S50-T2] ── the four NEW probes (all live-verified 2026-10-30 per
# DESIGN-S50 §0; every contract mirrors its production adapter) ───────

def probe_workday(triple: str) -> tuple[str, int, str]:
    """slug = 'tenant|instance|site' (the seed's format; the adapter's
    parse_board contract). CXS POST limit=1 → page-0 `total` (the
    established trust anchor — the total=0-after-page-1 quirk never
    applies at offset 0). [RB6] total is CAPPED at exactly 2,000 — the
    count is an honest FLOOR. [RA5] 404 → dead; connection/timeout/
    non-200 → error (a DNS blip must never freeze a live board
    terminal-dead for 95 days). Company = tenant (the resolve() seam
    joins canonical names later)."""
    try:
        tenant, inst, site = (triple.split("|") + [""] * 3)[:3]
        if not tenant or not inst or not site:
            return ("dead", 0, "")
        r = requests.post(
            f"https://{tenant}.{inst}.myworkdayjobs.com/wday/cxs/"
            f"{tenant}/{site}/jobs",
            json={"appliedFacets": {}, "limit": 1, "offset": 0,
                  "searchText": ""},
            headers={"Content-Type": "application/json", **UA},
            timeout=_TIMEOUT)
    except requests.RequestException:
        return ("error", 0, "")
    # [RB6 live-measured] the wildcard-DNS host answers UNKNOWN tenants
    # with 422 {"errorCode":"HTTP_422",...} — a DEFINITIVE no-such-board
    # answer, not a transient error (a DNS blip never reaches here).
    # 404 (the classic dead) and 422 both → dead; anything else → error.
    if r.status_code in (404, 422):
        return ("dead", 0, "")
    if r.status_code != 200:
        return ("error", 0, "")
    try:
        total = int(r.json().get("total") or 0)
    except ValueError:
        return ("error", 0, "")
    return ("live", total, tenant)

def probe_bamboohr(slug: str) -> tuple[str, int, str]:
    """embed2.php widget feed (BambooHRAdapter._feed's endpoint). The
    blankState JSON (departments []) is a LIVE tenant with 0 open
    roles — live:0, never dead (DESIGN-S50 risk #1; the deliberate
    divergence from the adapter's refuse-on-empty is documented for
    the S51 harvest). [RA19] company = '' (the feed carries no name)."""
    try:
        r = requests.get(
            f"https://{quote(slug)}.bamboohr.com/jobs/embed2.php",
            params={"version": "1.0.0", "format": "json"},
            headers=UA, timeout=_TIMEOUT)
    except requests.RequestException:
        return ("error", 0, "")
    # [RB6 live-measured] the wildcard-DNS host answers unknown tenants
    # with 200 + an EMPTY body (the feed endpoint only exists for real
    # tenants; a real 0-job tenant serves the blankState JSON with
    # "departments":[] — live:0, never dead). So: 200 + parseable JSON
    # carrying the departments key = live; 200 + empty/unparseable =
    # dead; 404 = dead; anything else = error.
    if r.status_code == 404:
        return ("dead", 0, "")
    if r.status_code != 200:
        return ("error", 0, "")
    try:
        data = r.json()
    except ValueError:
        return ("dead", 0, "")    # 200-empty = the no-tenant answer
    if not isinstance(data, dict) or "departments" not in data:
        return ("error", 0, "")     # 200-but-not-a-feed = parking page
    count = sum(len(d.get("positions") or [])
                for d in data.get("departments") or []
                if isinstance(d, dict))
    return ("live", count, "")


def probe_icims(slug: str) -> tuple[str, int, str]:
    """{slug}.icims.com/jobs/search — slug INCLUDES the 'careers-'
    prefix where present (the seed's convention; adapter org = full
    host). Redirect-follow ON (the 301/302 class is common); the
    marker gate (iCIMS_JobCardItem / the pager) is the live proof
    (RA: never misclassify a redirect-to-marketing parking page).
    [RA13] count is a CAP-BOUNDED ESTIMATE: cards + (N-1)*50 when the
    'Page 1 of N' pager is present, else the card count. [RA19]
    company = slug minus the careers- prefix."""
    import re
    try:
        # [RB6 live-measured] the BARE /jobs/search redirects to a portal
        # home with NO job markers — the SSR job table only renders with
        # the adapter's pinned query form (ICIMSAdapter._page's shape)
        r = requests.get(
            f"https://{quote(slug)}.icims.com/jobs/search"
            f"?pr=0&in_iframe=1&searchRelation=keyword_all",
            allow_redirects=True, headers=UA, timeout=_TIMEOUT)
    except requests.RequestException:
        return ("error", 0, "")
    if r.status_code == 404:
        return ("dead", 0, "")
    if r.status_code != 200:
        return ("error", 0, "")
    text = r.text or ""
    cards = text.count("iCIMS_JobCardItem")
    if cards == 0:
        # the adapter's other live shape: SSR job links without the
        # card wrapper — /jobs/{id}/ hrefs
        if not re.search(r"/jobs/\d+/", text):
            return ("error", 0, "")  # 200-without-markers = parking page
        cards = 1
    total = cards
    m = re.search(r"Page\s*1\s*of\s*(\d+)", text)
    if m:
        n = int(m.group(1))
        if n > 1:
            total = cards + (n - 1) * 50   # cap-bounded estimate [RA13]
    company = slug[8:] if slug.startswith("careers-") else slug
    return ("live", total, company)


def _page_data_jobs(html: str) -> int | None:
    """[RA9] the brace-matched window.pageData extractor (the adapter's
    tolerant fallback shape, site_boards.py _extract_page_data) — these
    literals are not always clean JSON, and braces INSIDE quoted strings
    (HTML fragments in teaser descriptions) must not close the match.
    Returns None when no pageData / no Jobs key (the adapter's two
    refuse-cases)."""
    marker = "window.pageData = "
    i = html.find(marker)
    if i < 0:
        return None
    s = html.find("{", i)
    if s < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    quote = ""
    end = -1
    for j in range(s, len(html)):
        c = html[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                in_str = False
            continue
        if c in "\"'":
            in_str = True
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    if end < 0:
        return None
    blob = html[s:end]
    try:
        d = json.loads(blob)
    except ValueError:
        return None
    jobs = d.get("Jobs")
    return len(jobs) if isinstance(jobs, list) else None


def probe_paylocity(guid: str) -> tuple[str, int, str]:
    """recruiting.paylocity.com/recruiting/jobs/All/{guid} (the adapter's
    _BASE + the entity-scoped All path). [RA9] the two refuse-cases
    (200-without-pageData / pageData-without-Jobs) map to error — a
    wrong GUID or a parking page is neither live nor dead. Company
    stays '' (the probe cannot know the name; the SEED carries it and
    RA4's CASE-update preserves it across probes)."""
    try:
        r = requests.get(
            f"https://recruiting.paylocity.com/recruiting/jobs/All/"
            f"{quote(guid)}",
            allow_redirects=True, headers=UA, timeout=_TIMEOUT)
    except requests.RequestException:
        return ("error", 0, "")
    if r.status_code == 404:
        return ("dead", 0, "")
    if r.status_code != 200:
        return ("error", 0, "")
    n = _page_data_jobs(r.text or "")
    if n is None:
        return ("error", 0, "")
    return ("live", n, "")


PROBES = {
    "greenhouse": probe_greenhouse,
    "lever": probe_lever,
    "ashby": probe_ashby,
    "smartrecruiters": probe_smartrecruiters,
    "workday": probe_workday,
    "bamboohr": probe_bamboohr,
    "icims": probe_icims,
    "paylocity": probe_paylocity,
}


# ── batch probing ────────────────────────────────────────────────────────────

def _daemon_is_draining() -> bool:
    """True when the prober daemon's heartbeat is fresh. R4-opus-review
    P2: a manual --batch run racing the daemon double-probes rows (and
    prematurely burns the attempts cap) — refuse instead."""
    if os.environ.get(_DAEMON_ENV) == "1":
        return False            # we ARE the daemon's child
    try:
        age = time.time() - HEARTBEAT.stat().st_mtime
    except OSError:
        return False            # no heartbeat file
    return age < _HEARTBEAT_FRESH_S


def _iso_now_minus_days(days: float) -> str:
    import datetime as _dt
    t = _dt.datetime.now() - _dt.timedelta(days=days)
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def run_batch(conn: sqlite3.Connection, ats: str | None, batch: int,
              sleep_s: float, retry_errors: bool = False,
              refresh_days: int = 0, dead_refresh_days: int = 0) -> None:
    """Probe up to `batch` rows (optionally one ATS only).

    [S50-T2] THE REFRESH MODE (DESIGN-S50 §2-T2.1): with refresh_days/
    dead_refresh_days armed, the claim order is (a) true pending, then
    (b) refresh-eligible live (staleness-FIRST — the campaign front is
    honest), then (c) refresh-eligible dead (the resurrection check),
    then (d) the aged error sweep + (e) the attempts-capped error
    ROLLOVER (aged past dead_refresh_days — the iCIMS parking-page
    class must not accrete terminal; disjoint from (d) by the ≥2
    attempts bound, mirroring gha_drain.needs_work_parts EXACTLY —
    the golden cross-file pin).

    Claim semantics (RA-B reviewed): status is NEVER flipped — a
    refresh-eligible row is claimed by a pure SELECT and its attempts
    RESET to 0 at claim time (per-cycle budget — the cap never marches
    across cycles); a mid-batch crash loses ≤ batch rows to re-claim
    (crash-resumable: eligibility is a pure function of status + age,
    and only completed probes mutate them). A dead board that probed
    live flips to live (the resurrection class); a re-probed live board
    updates job_count + last_probed_at. [RA4] company is written via
    CASE — an empty probe-returned company NEVER clobbers a learned
    name (paylocity seed names + the greenhouse name-backfill lane).

    Transient 'error' rows are NOT re-queued by default — pass
    retry_errors=True to re-claim error rows aged ≥1h (only after
    pending rows drain), capped at _MAX_ATTEMPTS probes per slug so
    permanently failing endpoints don't retry forever.
    Refuses to run while the prober daemon is draining (fresh heartbeat)
    unless invoked by the daemon itself.
    """
    if _daemon_is_draining():
        print("[batch] refusing to start: the prober daemon is draining "
              "this directory (fresh heartbeat). Stop the daemon first, "
              "or wait — a racing batch would double-probe rows.")
        return
    install_dns_cache()
    live_cutoff = _iso_now_minus_days(refresh_days) if refresh_days else ""
    dead_cutoff = _iso_now_minus_days(dead_refresh_days) \
        if dead_refresh_days else ""
    rows: list = []
    claimed_refresh: list[tuple[str, str]] = []

    def _claim(sql: str, args: list, limit: int) -> list:
        return conn.execute(sql + " LIMIT ?", [*args, limit]).fetchall()

    # (a) true pending — RANDOM keeps resumable batches spread across
    # ATSes (the per-host politeness spread)
    query = ("SELECT ats, slug FROM directory WHERE status = 'pending' "
             "AND attempts < ?")
    args: list = [_MAX_ATTEMPTS]
    if ats:
        query += " AND ats = ?"
        args.append(ats)
    query += " ORDER BY RANDOM() LIMIT ?"
    args.append(batch)
    rows = conn.execute(query, args).fetchall()

    # (b)/(c)/(e) the refresh classes (S50-T2) — staleness-first, the
    # honest campaign front; NOT attempts-gated (the claim-time reset
    # handles the cap — RA3)
    if not rows and refresh_days and dead_refresh_days:
        for cls, sql, cargs in (
            ("refresh:live",
             "SELECT ats, slug FROM directory WHERE status = 'live'"
             " AND (last_probed_at IS NULL OR last_probed_at < ?)",
             [live_cutoff]),
            ("refresh:dead",
             "SELECT ats, slug FROM directory WHERE status = 'dead'"
             " AND (last_probed_at IS NULL OR last_probed_at < ?)",
             [dead_cutoff]),
            ("refresh:error-rollover",
             "SELECT ats, slug FROM directory WHERE status = 'error'"
             " AND attempts >= 2"
             " AND (last_probed_at IS NULL OR last_probed_at < ?)",
             [dead_cutoff]),
        ):
            if ats:
                sql += " AND ats = ?"
                cargs = [*cargs, ats]
            sql += " ORDER BY last_probed_at ASC LIMIT ?"
            got = conn.execute(sql, [*cargs, batch]).fetchall()
            if got:
                print(f"[batch] claiming {len(got)} rows ({cls})")
                rows = got
                claimed_refresh = [(r[0], r[1]) for r in got]
                break            # one class per batch — the claim order holds
    # (d) the aged error sweep (retry_errors only, as today)
    if not rows and retry_errors:
        rq = ("SELECT ats, slug FROM directory WHERE status = 'error' "
              "AND attempts < ? "
              "AND (last_probed_at IS NULL OR last_probed_at < ?)")
        rargs: list = [_MAX_ATTEMPTS, _now_minus_hours(1)]
        if ats:
            rq += " AND ats = ?"
            rargs.append(ats)
        rq += " ORDER BY RANDOM() LIMIT ?"
        rargs.append(min(batch, max(batch // 2, 10)))
        rows = conn.execute(rq, rargs).fetchall()
        if rows:
            print(f"[batch] retrying {len(rows)} transient-error rows")
    if not rows:
        print("[batch] nothing pending")
        return

    # [S50-T2] the claim-time attempts reset — per-cycle budget; committed
    # with the first probe checkpoint so a crash between reset and probe
    # leaves harmless attempts=0 on still-eligible rows
    if claimed_refresh:
        conn.executemany(
            "UPDATE directory SET attempts = 0 "
            "WHERE ats = ? AND slug = ?", claimed_refresh)
        conn.commit()

    print(f"[batch] probing {len(rows)} slugs "
          f"({'all' if not ats else ats}; {sleep_s}s spacing)")
    t0 = time.time()
    done = 0
    for ats_name, slug in rows:
        probe = PROBES.get(ats_name)
        if probe is None:
            conn.execute(
                "UPDATE directory SET status = 'dead', "
                "attempts = attempts + 1, last_probed_at = ? "
                "WHERE ats = ? AND slug = ?",
                (_now(), ats_name, slug))
            continue
        status, count, company = probe(slug)
        # [RA4] the company-preserving UPDATE: an empty probe-returned
        # company NEVER clobbers a learned name (paylocity seed names,
        # the greenhouse name-backfill lane's work)
        conn.execute(
            "UPDATE directory SET status = ?, job_count = ?, "
            "company = CASE WHEN ? <> '' THEN ? ELSE company END, "
            "attempts = attempts + 1, last_probed_at = ? "
            "WHERE ats = ? AND slug = ?",
            (status, count, company, company, _now(), ats_name, slug))
        done += 1
        if done % 10 == 0:
            conn.commit()                       # checkpoint every 10
            print(f"  {done}/{len(rows)} probed "
                  f"({time.time() - t0:.0f}s elapsed)", flush=True)
        time.sleep(sleep_s)
    conn.commit()
    print(f"[batch] done: {done} slugs in {time.time() - t0:.0f}s")


def stats(conn: sqlite3.Connection) -> None:
    rows = conn.execute("""
        SELECT ats, status, COUNT(*) AS n, SUM(job_count) AS jobs
        FROM directory GROUP BY ats, status ORDER BY ats, status""").fetchall()
    print(f"{'ATS':<18} {'status':<8} {'slugs':>7} {'jobs':>9}")
    print("-" * 46)
    for ats, status, n, jobs in rows:
        print(f"{ats:<18} {status:<8} {n:>7} {jobs or 0:>9}")
    total = conn.execute("SELECT COUNT(*) FROM directory").fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM directory WHERE status='pending'").fetchone()[0]
    print(f"\ntotal: {total} slugs ({pending} pending)")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _now_minus_hours(hours: float) -> str:
    import datetime
    t = datetime.datetime.now() - datetime.timedelta(hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true",
                    help="load slug seeds into the directory")
    ap.add_argument("--batch", type=int, default=50,
                    help="number of pending slugs to probe this run")
    ap.add_argument("--ats", default=None,
                    choices=list(PROBES),
                    help="restrict probing to one ATS")
    ap.add_argument("--sleep", type=float, default=0.4,
                    help="seconds between probes (per-host courtesy)")
    ap.add_argument("--retry-errors", action="store_true",
                    help="also re-claim transient error rows (aged 1h+, "
                         f"max {_MAX_ATTEMPTS} attempts per slug)")
    ap.add_argument("--refresh-days", type=int, default=0,
                    help="[S50-T2] live-board re-probe staleness window "
                         "(0 = off). The drain passes 30 (DRAIN_REFRESH_DAYS).")
    ap.add_argument("--dead-refresh-days", type=int, default=0,
                    help="[S50-T2] dead/error-board resurrection window "
                         "(0 = off). The drain passes 95.")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    conn = _connect()
    try:
        if args.seed:
            seed(conn)
        if args.stats:
            stats(conn)
        if not args.stats and not args.seed:
            run_batch(conn, args.ats, args.batch, args.sleep,
                      retry_errors=args.retry_errors,
                      refresh_days=args.refresh_days,
                      dead_refresh_days=args.dead_refresh_days)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
