"""CLI — Week 1 deliverable: `search-jobs "Python Developer" --num 20`.

Pipeline: sources (parallel) → dedup (fuzzy merge) → SQLite upsert →
TF-IDF rank → LLM top-N score → ranked table + run report.
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from .config import load_config
from .dedup import dedup_jobs
from .llm import LLMSeam, SeamError
from .models import Job
from .pipeline_lock import PipelineLock
from .scoring import score_pipeline
from .sources import DEFAULT_SOURCES, all_source_names, search_all_sources
from .trust import field_completeness
from .storage import Store

DEFAULT_RESUME = """
Title: Senior Backend Engineer
Skills: Python (10y), Go (3y), TypeScript (2y), PostgreSQL (8y), Redis (6y),
Docker (5y), Kubernetes (3y), AWS (5y), FastAPI (4y), Django (5y).
Experience: Led microservices migration, real-time fraud detection pipeline,
LLM-powered support triage. B.S. Computer Science.
"""


@click.command()
@click.argument("keywords")
@click.option("--location", default="Remote", show_default=True,
              help="Location filter ('Remote' or city/state).")
@click.option("--num", default=20, show_default=True,
              help="Max results per source.")
@click.option("--sources", default=",".join(DEFAULT_SOURCES),
              show_default=True, help="Comma-separated source names.")
@click.option("--score/--no-score", default=True, show_default=True,
              help="Run LLM scoring on the TF-IDF top-N.")
@click.option("--resume-file", type=click.Path(exists=True), default=None,
              help="Resume text file (defaults to built-in sample profile).")
@click.option("--top-n", default=None, type=int,
              help="LLM scoring pool size (default from config: 20).")
@click.option("--db", default=None, help="SQLite path override.")
@click.option("--jsonl", "jsonl_path", default=None, type=click.Path(),
              help="Export the run's stored jobs to a JSONL file after the run.")
@click.option("--ops/--no-ops", default=True, show_default=True,
              help="Run repost + ghost-job detection over the stored jobs.")
@click.option("--alerts/--no-alerts", default=False, show_default=True,
              help="Send a Telegram digest of new high-fit (llm_score>=75) "
                   "jobs after scoring. Needs TELEGRAM_BOT_TOKEN + "
                   "TELEGRAM_CHAT_ID (skipped silently otherwise).")
@click.option("--verbose", "-v", is_flag=True, help="Per-source report on stderr.")
def search_jobs(keywords: str, location: str, num: int, sources: str,
                score: bool, resume_file: str | None, top_n: int | None,
                db: str | None, jsonl_path: str | None, ops: bool,
                alerts: bool, verbose: bool):
    """Search job boards, dedup, store, and rank by fit against the resume."""
    # Step D: process-wide DNS memoization + resolver pacing (opt out with
    # JOBSEARCH_NO_DNS_CACHE=1; cap via JOBSEARCH_DNS_LOOKUPS_PER_MIN).
    from .net import install_dns_cache
    install_dns_cache()

    cfg = load_config()
    if db:
        cfg.db_path = Path(db)
    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    unknown = [s for s in source_list if s not in all_source_names()]
    if unknown:
        click.echo(f"Unknown sources: {', '.join(unknown)}. "
                   f"Available: {', '.join(all_source_names())}", err=True)
        sys.exit(2)
    resume_text = (Path(resume_file).read_text(encoding="utf-8")
                   if resume_file else DEFAULT_RESUME)

    # Store construction (which runs migrations) stays INSIDE the pipeline
    # lock so two concurrent CLIs can't race init_db on a fresh DB.
    with PipelineLock(cfg.db_path):
        store = Store(cfg.db_path)
        try:
            _run_pipeline(store, cfg, keywords, location, num, source_list,
                          score, resume_text, top_n, verbose,
                          jsonl_path=jsonl_path, ops=ops, alerts=alerts)
        finally:
            store.close()


def _run_pipeline(store: Store, cfg, keywords: str, location: str, num: int,
                  source_list: list[str], score: bool, resume_text: str,
                  top_n: int | None, verbose: bool,
                  jsonl_path: str | None = None, ops: bool = True,
                  alerts: bool = False) -> None:
        run_id = store.start_run("search-jobs", keywords)
        if verbose:
            click.echo(f"Searching {len(source_list)} sources for "
                       f"'{keywords}' ({location})...", err=True)
        results = search_all_sources(keywords, location, source_list, num, cfg)

        # Step D: roll per-source stats (stability/latency/completeness →
        # per-source trust score persisted in source_stats).
        for r in results:
            store.record_source_result(
                r.source, ok=r.ok, jobs_count=len(r.jobs),
                duration_ms=r.duration_ms,
                completeness=field_completeness(r.jobs) if r.jobs else None)

        ok_sources = [r.source for r in results if r.ok]
        degraded = [f"{r.source}: {r.error}" for r in results if not r.ok]
        for r in degraded:
            click.echo(f"⚠ degraded: {r}", err=True)

        jobs: list[Job] = []
        for r in sorted(results, key=lambda r: r.source):
            # Deterministic source order → dedup's first-seen identity is
            # stable across runs (as_completed order varies otherwise).
            for j in r.jobs:
                j.search_query = keywords
            jobs.extend(r.jobs)
        deduped = dedup_jobs(jobs)
        new, merged = store.upsert_jobs(deduped)

        # Reload from DB so Job.ids are real database ids.
        stored = store.jobs_by_query(keywords, limit=500)
        llm_ok = False
        if score and stored:
            try:
                seam = LLMSeam(cfg, store)
                score_pipeline(stored, resume_text, cfg, store, seam,
                               top_n=top_n or cfg.llm_top_n)
                llm_ok = True
            except SeamError as exc:
                # D3 degrade policy: LLM failure never aborts the run —
                # TF-IDF ranking (already persisted above) still ships.
                click.echo(f"⚠ LLM scoring degraded ({exc.code}): "
                           f"falling back to TF-IDF ranking only", err=True)
            stored = store.jobs_by_query(keywords, limit=500)

        store.finish_run(run_id, sources_ok=ok_sources,
                         sources_degraded=[d.split(":")[0] for d in degraded],
                         jobs_found=len(deduped), jobs_new=new, jobs_merged=merged)

        # Sprint 3 ops pass: repost clusters + §708 ghost candidates. Runs on
        # the PRE-dedup per-source rows (dedup merges same-(company,title)
        # sightings into one stored row — the multi-URL repost evidence only
        # exists before the merge); signals then persist onto the surviving
        # stored rows via link matching (flag, never drop).
        ops_summary = None
        if ops:
            ops_summary = _run_ops_pass(store, keywords, pre_dedup_jobs=jobs)

        # Sprint 3 alert pass: new high-fit jobs → one Telegram digest.
        # Notifications are additive: an unconfigured/failed channel never
        # marks the alert log, so the same jobs alert later when it works.
        if alerts:
            from .alerts import run_alert_pass
            alert_summary = run_alert_pass(store, cfg, query=keywords)
            if alert_summary["pending"]:
                if alert_summary["sent"]:
                    click.echo(f"⚡ Telegram: sent digest with "
                               f"{alert_summary['pending']} new high-fit "
                               f"jobs", err=True)
                elif not alert_summary["channel_configured"]:
                    click.echo(f"⚡ Alerts: {alert_summary['pending']} new "
                               f"high-fit jobs pending — Telegram not "
                               f"configured (TELEGRAM_BOT_TOKEN + "
                               f"TELEGRAM_CHAT_ID)", err=True)
                else:
                    click.echo(f"⚠ Alerts: Telegram send failed — "
                               f"{alert_summary['pending']} jobs stay "
                               f"pending (unmarked, will retry)", err=True)

        if jsonl_path:
            n = store.export_jsonl(jsonl_path, query=keywords)
            click.echo(f"Exported {n} jobs → {jsonl_path}", err=True)

        _print_report(store, keywords, llm_ok, ops_summary)


def _run_ops_pass(store: Store, query: str,
                  pre_dedup_jobs: list[Job] | None = None) -> dict:
    """Repost + ghost detection for a query's rows (Sprint 3).

    Detection runs on `pre_dedup_jobs` when given (the per-source rows BEFORE
    dedup collapsed same-(company,title) sightings — the repost evidence is
    the set of distinct URLs, which dedup merges away); it falls back to the
    stored rows when not. Signals persist onto the stored rows whose link
    appears in a cluster / ghost group (the deduped survivor keeps exactly
    one of the cluster's links — the higher-ladder source's).

    Returns {'repost_clusters': [...], 'ghosts': [...]} with counts; marks
    rows via update_ops_signals. Repost count = distinct-URL appearances in
    the cluster; ghost = §708 aggregator-only + >30d rule.
    """
    from . import reposts as reposts_mod

    rows = pre_dedup_jobs if pre_dedup_jobs is not None \
        else store.jobs_by_query(query, limit=500)
    clusters = reposts_mod.detect_reposts(rows)
    ghosts = reposts_mod.ghost_candidates(rows)

    repost_by_link: dict[str, int] = {}
    for c in clusters:
        for a in c.appearances:
            repost_by_link[a["url"]] = max(
                repost_by_link.get(a["url"], 0), c.repost_count)
    ghost_links = {link for g in ghosts for link in g.links}

    # Persist onto the STORED rows (link matching — the deduped survivor
    # keeps exactly one of the cluster's links).
    for j in store.jobs_by_query(query, limit=500):
        if j.link and (j.link in repost_by_link or j.link in ghost_links):
            store.update_ops_signals(
                j.id,
                ghost_candidate=1 if j.link in ghost_links else None,
                repost_count=repost_by_link.get(j.link))

    return {
        "repost_clusters": [c.to_dict() for c in clusters],
        "ghosts": [g.to_dict() for g in ghosts],
    }


def _print_report(store: Store, query: str, scored: bool,
                  ops_summary: dict | None = None) -> None:
    jobs = store.jobs_by_query(query, limit=100)
    if not jobs:
        click.echo("No jobs found.")
        return
    click.echo(f"\n{'='*100}")
    click.echo(f"  RESULTS for '{query}' — {len(jobs)} tracked jobs "
               f"({'LLM-ranked' if scored else 'TF-IDF ranked'})")
    click.echo(f"{'='*100}")
    header = f"{'#':>3}  {'score':>6}  {'title':<42.42}  {'company':<24.24}  {'source':<22.22}  {'date':<10}"
    click.echo(header)
    click.echo("-" * 100)
    for i, j in enumerate(jobs[:50], start=1):
        if j.llm_score is not None:
            score_str = f"{j.llm_score:>5.1f}L"
        elif j.tfidf_score is not None:
            score_str = f"{j.tfidf_score:>5.1f}T"
        else:
            score_str = "    -"
        click.echo(f"{i:>3}  {score_str}  {j.title:<42.42}  {j.company:<24.24}  "
                   f"{j.source:<22.22}  {j.date_posted or '-':<10}")
    if len(jobs) > 50:
        click.echo(f"... and {len(jobs) - 50} more (use the tracker DB)")
    click.echo(f"\nTop reasoning:")
    for j in [x for x in jobs if x.llm_reasoning][:3]:
        click.echo(f"  • {j.title} @ {j.company}: {j.llm_reasoning}")
    click.echo("\nLegend: score suffix L = LLM rubric score, T = TF-IDF only "
               "(outside LLM top-N). Data: see the tracker DB.")

    if ops_summary:
        ghosts = ops_summary["ghosts"]
        clusters = ops_summary["repost_clusters"]
        if ghosts:
            click.echo(f"\nGhost-job candidates (§708 — flagged, not dropped): "
                       f"{len(ghosts)}")
            for g in ghosts[:5]:
                click.echo(f"  • {g['title'][:38]:<38.38} @ "
                           f"{g['company'][:20]:<20.20}  "
                           f"{g['days_old']}d old  seen on {len(g['sources'])} "
                           f"aggregators, no own-ATS sighting")
            if len(ghosts) > 5:
                click.echo(f"  ... and {len(ghosts) - 5} more "
                           f"(ghost_candidate=1 in the DB)")
        if clusters:
            click.echo(f"\nRepost clusters (90-day window): {len(clusters)}")
            for c in clusters[:5]:
                click.echo(f"  • {c['role'][:38]:<38.38} @ "
                           f"{c['company'][:20]:<20.20}  "
                           f"{c['repost_count']} listings over "
                           f"{c['days_span']}d ({c['first_seen']} → "
                           f"{c['last_seen']})")
            if len(clusters) > 5:
                click.echo(f"  ... and {len(clusters) - 5} more "
                           f"(repost_count in the DB)")
        if not ghosts and not clusters:
            click.echo("\nOps pass: no repost clusters or ghost candidates "
                       "in this query's rows.")


if __name__ == "__main__":
    search_jobs()
