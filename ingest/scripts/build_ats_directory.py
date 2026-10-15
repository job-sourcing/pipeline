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
    """Load slug seeds into pending rows (idempotent)."""
    seeds = [
        ("ashby", "ashby_companies.json"),
        ("greenhouse", "greenhouse_companies.json"),
        ("lever", "lever_companies.json"),
    ]
    total = 0
    for ats, fname in seeds:
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
        # keep string entries only (external datasets can drift shape)
        skipped = sum(1 for s in raw if not isinstance(s, str))
        slugs = [s.strip() for s in raw
                 if isinstance(s, str) and s.strip()]
        if skipped:
            print(f"[seed] {ats}: skipped {skipped} non-string entries")
        # de-dupe + skip already-present rows
        existing = {r[0] for r in conn.execute(
            "SELECT slug FROM directory WHERE ats = ?", (ats,))}
        fresh = [(ats, s) for s in dict.fromkeys(slugs)
                 if s and s not in existing]
        if fresh:
            conn.executemany(
                "INSERT OR IGNORE INTO directory (ats, slug) VALUES (?, ?)",
                fresh)
            conn.commit()          # per-ATS commit; never leave an open
                                    # snapshot while the daemon holds writes
        total += len(fresh)
        print(f"[seed] {ats}: +{len(fresh)} new slugs "
              f"({len(existing)} already present)")
    # curated SmartRecruiters slugs (verified in this repo's config)
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


PROBES = {
    "greenhouse": probe_greenhouse,
    "lever": probe_lever,
    "ashby": probe_ashby,
    "smartrecruiters": probe_smartrecruiters,
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


def run_batch(conn: sqlite3.Connection, ats: str | None, batch: int,
              sleep_s: float, retry_errors: bool = False) -> None:
    """Probe up to `batch` pending slugs (optionally one ATS only).

    Transient 'error' rows (timeouts, 429s, connection resets) are NOT
    re-queued by default — pass retry_errors=True to re-claim error rows
    aged ≥1h (only after pending rows drain), capped at _MAX_ATTEMPTS
    probes per slug so permanently failing endpoints don't retry forever.
    Refuses to run while the prober daemon is draining (fresh heartbeat)
    unless invoked by the daemon itself.
    """
    if _daemon_is_draining():
        print("[batch] refusing to start: the prober daemon is draining "
              "this directory (fresh heartbeat). Stop the daemon first, "
              "or wait — a racing batch would double-probe rows.")
        return
    install_dns_cache()
    query = ("SELECT ats, slug FROM directory WHERE status = 'pending' "
             "AND attempts < ?")
    args: list = [_MAX_ATTEMPTS]
    if ats:
        query += " AND ats = ?"
        args.append(ats)
    query += " ORDER BY RANDOM() LIMIT ?"      # random → resumable batches
    args.append(batch)                          # spread across ATSes
    rows = conn.execute(query, args).fetchall()
    if not rows and retry_errors:
        # nothing pending — sweep up transient errors aged ≥1h that still
        # have attempts left (CodeRabbit: honor the --batch limit here too;
        # --batch 1 must not claim 10 error rows).
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
        conn.execute(
            "UPDATE directory SET status = ?, job_count = ?, company = ?, "
            "attempts = attempts + 1, last_probed_at = ? "
            "WHERE ats = ? AND slug = ?",
            (status, count, company, _now(), ats_name, slug))
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
                      retry_errors=args.retry_errors)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
