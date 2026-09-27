"""SQLite storage — the single source of truth (DECISIONS.md D4).

Policy:
  - WAL journal + busy_timeout=5000 on every connection (multi-runtime safety)
  - ALL writes through Python (single-writer); Node opens read-only (D4)
  - Additive-only migrations with version stamp (D12)
  - llm_cache doubles as the telemetry store (D10)
  - quarantine holds malformed LLM outputs — sentinel scores NEVER enter
    rankings (D1.2)
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import Job, _utcnow

SCHEMA_VERSION = 5

_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        company TEXT NOT NULL,
        description TEXT DEFAULT '',
        link TEXT,
        contact_email TEXT,
        source TEXT NOT NULL,
        location TEXT DEFAULT '',
        date_posted TEXT,
        remote INTEGER DEFAULT 0,
        h1b_mention INTEGER DEFAULT 0,
        salary_text TEXT,
        salary_min REAL,
        salary_max REAL,
        search_query TEXT DEFAULT '',
        scraped_at TEXT,
        created_at TEXT,
        updated_at TEXT,
        status TEXT DEFAULT 'shortlisted',
        pipeline_stage TEXT DEFAULT 'saved',
        apply_intent_at TEXT,
        apply_intent_acknowledged_at TEXT,
        tfidf_score REAL,
        llm_score REAL,
        llm_reasoning TEXT,
        llm_matches TEXT,
        llm_gaps TEXT,
        scored_at TEXT
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_link ON jobs(link) WHERE link IS NOT NULL AND link != '';
    CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
    CREATE INDEX IF NOT EXISTS idx_jobs_llm_score ON jobs(llm_score);

    CREATE TABLE IF NOT EXISTS llm_cache (
        cache_key TEXT PRIMARY KEY,
        prompt_version TEXT NOT NULL,
        created_at TEXT,
        response TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS quarantine (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        script TEXT,
        prompt_version TEXT,
        raw_output TEXT,
        reason TEXT
    );

    CREATE TABLE IF NOT EXISTS telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        event TEXT NOT NULL,
        script TEXT,
        duration_ms INTEGER,
        cache_hit INTEGER,
        error_class TEXT,
        detail TEXT
    );

    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT,
        finished_at TEXT,
        command TEXT,
        query TEXT,
        sources_ok TEXT,
        sources_degraded TEXT,
        jobs_found INTEGER,
        jobs_new INTEGER,
        jobs_merged INTEGER
    );
    """,
    # v2 (Step D 2026-08-27): per-job trust columns + per-source rolling
    # stats table. Additive only — v1 DBs migrate forward (D12).
    2: """
    ALTER TABLE jobs ADD COLUMN trust_score INTEGER;
    ALTER TABLE jobs ADD COLUMN trust_flags TEXT;
    ALTER TABLE jobs ADD COLUMN trust_level TEXT;
    CREATE INDEX IF NOT EXISTS idx_jobs_trust ON jobs(trust_score);

    CREATE TABLE IF NOT EXISTS source_stats (
        source TEXT PRIMARY KEY,
        attempts INTEGER DEFAULT 0,
        successes INTEGER DEFAULT 0,
        jobs_returned INTEGER DEFAULT 0,
        total_latency_ms INTEGER DEFAULT 0,
        completeness_sum REAL DEFAULT 0,
        completeness_samples INTEGER DEFAULT 0,
        last_success_at TEXT,
        trust_score INTEGER
    );
    """,
    # v3 (Sprint 3, 2026-08-29): categorization columns (T2 classify-tier /
    # skill-extract / work-mode ports + ats_platform from link detection) and
    # ops-signal columns (§708 ghost flag, repost window count). Additive.
    3: """
    ALTER TABLE jobs ADD COLUMN tier TEXT;
    ALTER TABLE jobs ADD COLUMN skills TEXT;
    ALTER TABLE jobs ADD COLUMN work_mode TEXT;
    ALTER TABLE jobs ADD COLUMN ats_platform TEXT;
    ALTER TABLE jobs ADD COLUMN ghost_candidate INTEGER;
    ALTER TABLE jobs ADD COLUMN repost_count INTEGER;
    CREATE INDEX IF NOT EXISTS idx_jobs_tier ON jobs(tier);
    CREATE INDEX IF NOT EXISTS idx_jobs_ghost ON jobs(ghost_candidate);
    """,
    # v4 (Sprint 3, 2026-08-29): the sector axis (methodology §9 gap — LLM
    # pass on the scoring top-N, prompt v3). Additive.
    4: """
    ALTER TABLE jobs ADD COLUMN sector TEXT;
    CREATE INDEX IF NOT EXISTS idx_jobs_sector ON jobs(sector);
    """,
    # v5 (Sprint 3, 2026-08-29): the real-time posting-alert path (methodology
    # §9: cron + alerts). One row per (job, channel) alert already SENT —
    # pending alerts are re-derived per pass, never re-sent.
    5: """
    CREATE TABLE IF NOT EXISTS alert_log (
        job_id INTEGER NOT NULL,
        channel TEXT NOT NULL,
        sent_at TEXT NOT NULL,
        PRIMARY KEY (job_id, channel)
    );
    """,
}

_JOB_COLUMNS = (
    "id", "title", "company", "description", "link", "contact_email", "source",
    "location", "date_posted", "remote", "h1b_mention", "salary_text",
    "salary_min", "salary_max", "search_query", "scraped_at", "created_at",
    "updated_at", "status", "pipeline_stage", "apply_intent_at",
    "apply_intent_acknowledged_at", "tfidf_score", "llm_score", "llm_reasoning",
    "llm_matches", "llm_gaps", "scored_at",
    "trust_score", "trust_flags", "trust_level",
    "tier", "skills", "work_mode", "ats_platform",
    "ghost_candidate", "repost_count", "sector",
)


def connect(db_path: Path | str, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection with the mandatory pragmas (D4)."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    uri = f"file:{p.resolve()}?mode=ro" if readonly else f"file:{p.resolve()}"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Apply additive migrations up to SCHEMA_VERSION (D12).

    Each migration runs inside an explicit transaction and is guarded so a
    partially-applied migration (crash between ALTERs, or two concurrent
    Store() opens) can't brick later opens with 'duplicate column name'.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current = row["v"] or 0
    for version in sorted(_MIGRATIONS):
        if version > current:
            _apply_migration(conn, version, _MIGRATIONS[version])
    conn.commit()


def _apply_migration(conn: sqlite3.Connection, version: int,
                     script: str) -> None:
    """One migration, atomically, tolerating already-applied ALTERs."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing_cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(jobs)")}
        stmts = [s.strip() for s in script.split(";") if s.strip()]
        for stmt in stmts:
            # Guard additive ALTERs so a half-applied migration doesn't
            # permanently fail ('duplicate column name: trust_score').
            m = re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", stmt,
                         re.IGNORECASE)
            if m and m.group(2).lower() in {
                    r["name"].lower() for r in conn.execute(
                        f"PRAGMA table_info({m.group(1)})")}:
                continue                     # column already there — skip
            conn.execute(stmt)
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (version,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def cache_key(prompt: str, prompt_version: str) -> str:
    """D8: cache key = hash(prompt + prompt_template_version)."""
    h = hashlib.sha256()
    h.update(prompt_version.encode("utf-8"))
    h.update(b"\x00")
    h.update(prompt.encode("utf-8"))
    return h.hexdigest()


class Store:
    """All DB operations. The only writer in the system (D4)."""

    def __init__(self, db_path: Path | str):
        self.conn = connect(db_path)
        init_db(self.conn)

    # ── jobs ────────────────────────────────────────────────────────────

    def upsert_jobs(self, jobs: Iterable[Job]) -> tuple[int, int]:
        """Insert new jobs / merge fields into existing (by unique link).

        Returns (new_count, merged_count).
        """
        new = merged = 0
        for job in jobs:
            if not job.title or not job.company:
                continue
            now = _utcnow()
            existing = None
            if job.link:
                row = self.conn.execute(
                    "SELECT id, description, salary_text, contact_email, location, "
                    "date_posted, remote, h1b_mention, trust_score, "
                    "trust_flags, trust_level, tier, skills, work_mode, "
                    "ats_platform, ghost_candidate, repost_count, sector "
                    "FROM jobs WHERE link = ?",
                    (job.link,),
                ).fetchone()
                existing = row
            else:
                # Linkless jobs (Glassdoor JSON-LD, Ashby missing job_id, …)
                # have no unique-link identity. Without a fallback lookup the
                # scheduler would INSERT one duplicate row per cycle forever
                # (audit P2-1: verified 2 rows after one re-run). Fall back
                # to the (source, title, company) fingerprint — same source
                # re-serving the same linkless posting = same row.
                row = self.conn.execute(
                    "SELECT id, description, salary_text, contact_email, location, "
                    "date_posted, remote, h1b_mention, trust_score, "
                    "trust_flags, trust_level, tier, skills, work_mode, "
                    "ats_platform, ghost_candidate, repost_count, sector "
                    "FROM jobs WHERE (link IS NULL OR link = '') "
                    "AND source = ? AND title = ? AND company = ?",
                    (job.source or "", job.title, job.company),
                ).fetchone()
                existing = row
            if existing is not None:
                # Merge: fill NULL/empty fields only (never overwrite data).
                updates: list[tuple[str, Any]] = []
                for col, val in (
                    ("description", job.description),
                    ("salary_text", job.salary_text),
                    ("contact_email", job.contact_email),
                    ("location", job.location),
                    ("date_posted", job.date_posted),
                ):
                    if val and not existing[col]:
                        updates.append((col, val))
                for col, val in (("remote", int(job.remote)), ("h1b_mention", int(job.h1b_mention))):
                    if val and not existing[col]:
                        updates.append((col, val))
                # Trust fields: fill when the stored row has none (the
                # enrichment runs on every fetch, but v1-era rows and rows
                # stored before the trust wiring have NULLs).
                if job.trust_score is not None and existing["trust_score"] is None:
                    updates.append(("trust_score", job.trust_score))
                if job.trust_flags and existing["trust_flags"] is None:
                    updates.append(("trust_flags", job.to_dict()["trust_flags"]))
                if job.trust_level and existing["trust_level"] is None:
                    updates.append(("trust_level", job.trust_level))
                # Categorization (v3): same fill-only policy as trust — the
                # enrichment runs every fetch, but only NULLs are filled so
                # a v2-era row gains its tier without clobbering anything.
                for col, val in (
                    ("tier", job.tier),
                    ("work_mode", job.work_mode),
                    ("ats_platform", job.ats_platform),
                    ("sector", job.sector),
                ):
                    if val and existing[col] is None:
                        updates.append((col, val))
                if job.skills and existing["skills"] is None:
                    updates.append(("skills", job.to_dict()["skills"]))
                if updates:
                    sets = ", ".join(f"{c} = ?" for c, _ in updates)
                    self.conn.execute(
                        f"UPDATE jobs SET {sets}, updated_at = ? WHERE id = ?",
                        [v for _, v in updates] + [now, existing["id"]],
                    )
                merged += 1
            else:
                d = job.to_dict()
                cols = [c for c in _JOB_COLUMNS if c != "id"]
                self.conn.execute(
                    f"INSERT INTO jobs ({', '.join(cols)}) "
                    f"VALUES ({', '.join('?' for _ in cols)})",
                    [d[c] for c in cols],
                )
                new += 1
        self.conn.commit()
        return new, merged

    def update_ops_signals(self, job_id: int, ghost_candidate: Optional[int],
                           repost_count: Optional[int]) -> None:
        """Persist §708 ghost flag / repost-window count (Sprint 3).

        Ghost semantics are FLAG-NOT-DROP (methodology §708): the row stays
        in the primary table; consumers filter on ghost_candidate=1 to build
        the cold-storage view. Passing None skips that field.
        """
        sets, params = ["updated_at = ?"], [_utcnow()]
        if ghost_candidate is not None:
            sets.append("ghost_candidate = ?")
            params.append(int(bool(ghost_candidate)))
        if repost_count is not None:
            sets.append("repost_count = ?")
            params.append(int(repost_count))
        params.append(job_id)
        self.conn.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()

    def export_jsonl(self, path: Path | str, query: Optional[str] = None,
                     limit: int = 5000, contract: Optional[str] = "facet01",
                     max_description_chars: Optional[int] = None) -> int:
        """Write stored jobs as JSONL.

        contract="facet01" (default, Wave-R R2): the durable corpus
        contract — `jobsearch.export.facet01_row` maps each Job onto the
        MAIN repo facet-01 loader's field names (job_id/experience_level/
        work_type/remote_allowed/listed_time/… plus provenance extras).
        Spec: docs/jsonl-export-spec.md.

        contract=None: the legacy Sprint-2 debug shape — the raw model
        dump with model field names as keys.

        One JSON object per line, list fields as real JSON arrays.
        Returns the number of lines written. Query=None exports the whole
        tracker (cap: limit). `max_description_chars` truncates the
        EXPORTED description only (facet01 contract) — the DB rows are
        never modified.
        """
        if query:
            rows = self.conn.execute(
                "SELECT * FROM jobs WHERE search_query = ? "
                "ORDER BY COALESCE(llm_score, -1) DESC, "
                "COALESCE(tfidf_score, -1) DESC, date_posted DESC LIMIT ?",
                (query, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM jobs ORDER BY id LIMIT ?", (limit,)).fetchall()
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with out.open("w", encoding="utf-8") as fh:
            for row in rows:
                job = self._row_to_job(row)
                if contract == "facet01":
                    from .export import facet01_row
                    d = facet01_row(job,
                                   max_description_chars=max_description_chars)
                else:
                    d = job.to_dict()
                    # to_dict JSON-encodes list fields for SQLite; the JSONL
                    # contract wants REAL arrays — decode them back.
                    for k in ("llm_matches", "llm_gaps", "trust_flags",
                              "skills"):
                        if isinstance(d.get(k), str):
                            try:
                                d[k] = json.loads(d[k])
                            except json.JSONDecodeError:
                                pass
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
                n += 1
        return n

    def jobs_by_query(self, query: str, limit: int = 100) -> list[Job]:
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE search_query = ? "
            "ORDER BY COALESCE(llm_score, -1) DESC, COALESCE(tfidf_score, -1) DESC, "
            "date_posted DESC LIMIT ?",
            (query, limit),
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def unscored_llm(self, query: str, limit: int) -> list[Job]:
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE search_query = ? AND llm_score IS NULL "
            "ORDER BY COALESCE(tfidf_score, -1) DESC LIMIT ?",
            (query, limit),
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def update_scores(self, job_id: int, tfidf: float | None = None,
                      llm: dict | None = None) -> None:
        sets, params = ["updated_at = ?"], [_utcnow()]
        if tfidf is not None:
            sets.append("tfidf_score = ?")
            params.append(tfidf)
        if llm is not None:
            sets.extend(["llm_score = ?", "llm_reasoning = ?", "llm_matches = ?",
                         "llm_gaps = ?", "scored_at = ?"])
            params.extend([
                llm.get("score"),
                llm.get("reasoning"),
                json.dumps(llm.get("top_matches") or [], ensure_ascii=False),
                json.dumps(llm.get("gaps") or [], ensure_ascii=False),
                _utcnow(),
            ])
            # Sector (prompt v3): COALESCE-style fill — never blank an
            # already-inferred sector with a later None.
            if llm.get("sector"):
                sets.append("sector = ?")
                params.append(llm["sector"])
        params.append(job_id)
        self.conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()

    # ── LLM cache + telemetry + quarantine (D8/D10/D1.2) ────────────────

    def cache_get(self, key: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT response FROM llm_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        if row is None:
            self.log_telemetry("cache_miss")
            return None
        self.log_telemetry("cache_hit")
        try:
            return json.loads(row["response"])
        except json.JSONDecodeError:
            return None

    def cache_put(self, key: str, prompt_version: str, response: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_cache (cache_key, prompt_version, created_at, response) "
            "VALUES (?, ?, ?, ?)",
            (key, prompt_version, _utcnow(), json.dumps(response, ensure_ascii=False)),
        )
        self.conn.commit()

    def quarantine(self, script: str, prompt_version: str, raw: str, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO quarantine (created_at, script, prompt_version, raw_output, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (_utcnow(), script, prompt_version, raw[:20000], reason),
        )
        self.conn.commit()

    def log_telemetry(self, event: str, script: str = "", duration_ms: int = 0,
                      cache_hit: Optional[bool] = None, error_class: str = "",
                      detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO telemetry (ts, event, script, duration_ms, cache_hit, error_class, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_utcnow(), event, script, duration_ms,
             None if cache_hit is None else int(cache_hit), error_class, detail[:2000]),
        )
        self.conn.commit()

    def start_run(self, command: str, query: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, command, query) VALUES (?, ?, ?)",
            (_utcnow(), command, query),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, *, sources_ok: list[str],
                   sources_degraded: list[str], jobs_found: int,
                   jobs_new: int, jobs_merged: int) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, sources_ok = ?, sources_degraded = ?, "
            "jobs_found = ?, jobs_new = ?, jobs_merged = ? WHERE id = ?",
            (_utcnow(), json.dumps(sources_ok), json.dumps(sources_degraded),
             jobs_found, jobs_new, jobs_merged, run_id),
        )
        self.conn.commit()

    # ── source stats (Step D — per-source rolling trust) ───────────────

    def mark_alerted(self, job_ids: Iterable[int], channel: str = "telegram"
                     ) -> int:
        """Record that these job ids had an alert SENT on `channel`.

        Idempotent (PRIMARY KEY job_id+channel). Returns rows written.
        Called ONLY after a successful send — an unconfigured/failed channel
        never marks, so the same jobs alert later when the channel works.
        """
        now = _utcnow()
        cur = self.conn.executemany(
            "INSERT OR IGNORE INTO alert_log (job_id, channel, sent_at) "
            "VALUES (?, ?, ?)", [(int(j), channel, now) for j in job_ids])
        self.conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    def alerted_ids(self, channel: str = "telegram") -> set[int]:
        """Job ids that already had an alert sent on `channel`."""
        return {r[0] for r in self.conn.execute(
            "SELECT job_id FROM alert_log WHERE channel = ?", (channel,))}

    def record_source_result(self, source: str, *, ok: bool,
                             jobs_count: int, duration_ms: int,
                             completeness: float | None = None) -> int:
        """Roll one fetch outcome into source_stats; returns the new
        per-source trust score (methodology §6.2 four-dimension score)."""
        from .trust import source_trust_score

        row = self.conn.execute(
            "SELECT attempts, successes, jobs_returned, total_latency_ms, "
            "completeness_sum, completeness_samples FROM source_stats "
            "WHERE source = ?", (source,)).fetchone()
        if row is None:
            row = {k: 0 for k in ("attempts", "successes", "jobs_returned",
                                  "total_latency_ms", "completeness_sum",
                                  "completeness_samples")}
        attempts = row["attempts"] + 1
        successes = row["successes"] + (1 if ok else 0)
        jobs_total = row["jobs_returned"] + jobs_count
        latency_total = row["total_latency_ms"] + duration_ms
        comp_sum = row["completeness_sum"]
        comp_n = row["completeness_samples"]
        if completeness is not None:
            comp_sum += completeness
            comp_n += 1

        score = source_trust_score(source, {
            "attempts": attempts,
            "successes": successes,
            "avg_latency_ms": (latency_total / attempts) if attempts else None,
            "avg_field_completeness": (comp_sum / comp_n) if comp_n else None,
        })
        self.conn.execute(
            "INSERT INTO source_stats (source, attempts, successes, "
            "jobs_returned, total_latency_ms, completeness_sum, "
            "completeness_samples, last_success_at, trust_score) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source) DO UPDATE SET attempts=excluded.attempts, "
            "successes=excluded.successes, jobs_returned=excluded.jobs_returned, "
            "total_latency_ms=excluded.total_latency_ms, "
            "completeness_sum=excluded.completeness_sum, "
            "completeness_samples=excluded.completeness_samples, "
            "last_success_at=COALESCE(excluded.last_success_at, "
            "                          source_stats.last_success_at), "
            "trust_score=excluded.trust_score",
            (source, attempts, successes, jobs_total, latency_total,
             comp_sum, comp_n,
             _utcnow() if ok else None, score))
        self.conn.commit()
        return score

    def source_stats(self, source: str | None = None) -> list[dict]:
        """All (or one) source_stats rows as dicts."""
        if source:
            rows = self.conn.execute(
                "SELECT * FROM source_stats WHERE source = ?",
                (source,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM source_stats ORDER BY trust_score DESC").fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        d = dict(row)
        for k in ("llm_matches", "llm_gaps", "trust_flags", "skills"):
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except json.JSONDecodeError:
                    d[k] = None
            else:
                d[k] = None
        d["remote"] = bool(d.get("remote"))
        d["h1b_mention"] = bool(d.get("h1b_mention"))
        return Job(**{k: d.get(k) for k in _JOB_COLUMNS})
