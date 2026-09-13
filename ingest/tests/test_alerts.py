"""Alerts (Sprint 3 — cron + alerts): pending selection, digest format,
Telegram send semantics (the validated autopilot notifier contract), and
the mark-only-on-success rule. Network calls are mocked throughout."""
from __future__ import annotations

import json

import pytest

import jobsearch.alerts as alerts_mod
from jobsearch.alerts import (
    format_digest, pending_alerts, run_alert_pass, send_telegram,
)
from jobsearch.config import Config

from conftest import make_job


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(db_path=tmp_path / "t.db")


def _scored(store, n=3, score=82.0, **kwargs):
    defaults = dict(title_template="Engineer",
                    link="https://example.com/a/{i}", search_query="q")
    jobs = []
    for i in range(n):
        kw = dict(kwargs)
        kw.setdefault("search_query", defaults["search_query"])
        kw.setdefault("link", defaults["link"].format(i=i))
        kw.setdefault("title", f"Engineer {i}")
        jobs.append(make_job(**kw))
    store.upsert_jobs(jobs)
    rows = store.conn.execute("SELECT id FROM jobs").fetchall()
    for r in rows:
        store.update_scores(r["id"], llm={
            "score": score, "reasoning": "ok", "top_matches": [],
            "gaps": []})
    return store.jobs_by_query("q")


# ── selection ──────────────────────────────────────────────────────────────

def test_pending_alerts_selects_high_fit_unalerted(store):
    jobs = _scored(store, n=3, score=82.0)
    assert len(pending_alerts(store, "q")) == 3

    # mark two → only the third remains pending
    store.mark_alerted([j.id for j in jobs[:2]])
    remaining = pending_alerts(store, "q")
    assert len(remaining) == 1
    assert remaining[0].id == jobs[2].id


def test_pending_alerts_threshold(store):
    _scored(store, n=1, score=74.9)
    assert pending_alerts(store, "q") == []          # below 75
    _scored(store, n=1, score=75.0)
    assert len(pending_alerts(store, "q")) >= 1      # boundary inclusive


def test_pending_alerts_excludes_ghosts_and_skipped(store):
    jobs = _scored(store, n=2, score=90.0)
    store.update_ops_signals(jobs[0].id, ghost_candidate=1, repost_count=None)
    store.conn.execute("UPDATE jobs SET status='skipped' WHERE id = ?",
                       (jobs[1].id,))
    store.conn.commit()
    assert pending_alerts(store, "q") == []


def test_pending_alerts_limit_sorted_by_score(store):
    jobs = _scored(store, n=5, score=80.0)
    # give the last one the top score
    store.update_scores(jobs[-1].id, llm={"score": 99.0,
                                           "reasoning": "top",
                                           "top_matches": [], "gaps": []})
    got = pending_alerts(store, "q", limit=3)
    assert len(got) == 3
    assert got[0].llm_score == 99.0                  # best first
    assert got[0].id == jobs[-1].id


def test_pending_alerts_whole_db_when_no_query(store):
    _scored(store, n=2, score=80.0, search_query="q1")
    store.upsert_jobs([make_job(link="https://x.com/1",
                                search_query="q2")])
    row = store.conn.execute(
        "SELECT id FROM jobs WHERE search_query='q2'").fetchone()
    store.update_scores(row["id"], llm={"score": 88.0, "reasoning": "ok",
                                         "top_matches": [], "gaps": []})
    # no query → cross-query scan sees both queries' high-fit jobs
    ids = {j.id for j in pending_alerts(store)}
    assert len(ids) == 3


# ── digest format ──────────────────────────────────────────────────────────

def test_format_digest_html_escapes_and_includes_links(store):
    jobs = _scored(store, n=1)
    digest = format_digest(jobs, query="python <dev>")
    assert "1 new high-fit job" in digest
    assert "python &lt;dev&gt;" in digest              # query escaped
    assert "https://example.com/a/0" in digest
    assert "Engineer 0" in digest


def test_format_digest_pluralization():
    digest = format_digest([], query="q")
    assert "0 new high-fit jobs" in digest


# ── send semantics (validated autopilot contract) ──────────────────────────

def test_send_telegram_unconfigured_returns_false(cfg):
    assert cfg.telegram_bot_token == "" and cfg.telegram_chat_id == ""
    assert send_telegram("hi", cfg) is False


def test_send_telegram_posts_html_payload(cfg, monkeypatch):
    cfg.telegram_bot_token = "tok"
    cfg.telegram_chat_id = "chat"
    calls = {}

    class FakeResp:
        status_code = 200

    def fake_post(url, json=None, timeout=None):
        calls["url"], calls["payload"], calls["timeout"] = url, json, timeout
        return FakeResp()

    monkeypatch.setattr(alerts_mod.requests, "post", fake_post)
    assert send_telegram("<b>digest</b>", cfg) is True
    assert calls["url"] == "https://api.telegram.org/bottok/sendMessage"
    assert calls["payload"]["parse_mode"] == "HTML"
    assert calls["payload"]["chat_id"] == "chat"
    assert calls["payload"]["disable_web_page_preview"] is True


def test_send_telegram_http_error_returns_false(cfg, monkeypatch):
    cfg.telegram_bot_token = "tok"
    cfg.telegram_chat_id = "chat"

    class FakeResp:
        status_code = 401

    monkeypatch.setattr(alerts_mod.requests, "post",
                        lambda *a, **kw: FakeResp())
    assert send_telegram("hi", cfg) is False


def test_send_telegram_network_error_returns_false(cfg, monkeypatch):
    cfg.telegram_bot_token = "tok"
    cfg.telegram_chat_id = "chat"
    import requests as real_requests

    def boom(*a, **kw):
        raise real_requests.RequestException("net down")

    monkeypatch.setattr(alerts_mod.requests, "post", boom)
    assert send_telegram("hi", cfg) is False


# ── the pass: mark only on success ─────────────────────────────────────────

def test_run_alert_pass_unconfigured_does_not_mark(store, cfg):
    _scored(store, n=2)
    summary = run_alert_pass(store, cfg, query="q")
    assert summary == {"pending": 2, "sent": False,
                       "channel_configured": False}
    assert store.alerted_ids() == set()               # stays pending


def test_run_alert_pass_success_marks(store, cfg, monkeypatch):
    cfg.telegram_bot_token = "tok"
    cfg.telegram_chat_id = "chat"
    monkeypatch.setattr(alerts_mod.requests, "post",
                        lambda *a, **kw: type("R", (), {"status_code": 200})())
    jobs = _scored(store, n=2)
    summary = run_alert_pass(store, cfg, query="q")
    assert summary["sent"] is True and summary["pending"] == 2
    assert store.alerted_ids() == {j.id for j in jobs}
    # second pass: nothing pending anymore
    assert run_alert_pass(store, cfg, query="q")["pending"] == 0


def test_run_alert_pass_failed_send_does_not_mark(store, cfg, monkeypatch):
    cfg.telegram_bot_token = "tok"
    cfg.telegram_chat_id = "chat"
    monkeypatch.setattr(alerts_mod.requests, "post",
                        lambda *a, **kw: type("R", (), {"status_code": 500})())
    _scored(store, n=1)
    summary = run_alert_pass(store, cfg, query="q")
    assert summary["sent"] is False and summary["pending"] == 1
    assert store.alerted_ids() == set()               # retry later


def test_alert_log_migration_and_helpers(store):
    # v5 table exists on a fresh DB
    tables = {r["name"] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "alert_log" in tables
    jobs = _scored(store, n=2)
    n = store.mark_alerted([j.id for j in jobs])
    assert n >= 1
    assert store.alerted_ids() == {j.id for j in jobs}
    # idempotent re-mark
    store.mark_alerted([j.id for j in jobs])
    assert len(store.alerted_ids()) == 2


# ── CLI wiring ─────────────────────────────────────────────────────────────

def test_cli_alerts_flag_reports_pending(tmp_path, monkeypatch):
    import jobsearch.cli as cli_module
    from click.testing import CliRunner
    from jobsearch.models import SourceResult
    from jobsearch.cli import search_jobs

    def fake_search(keywords, location, sources, num, cfg):
        return [SourceResult(source="Remotive", jobs=[make_job(
            title="Senior Python Dev", link="https://ex.com/a/1",
            search_query=keywords)])]

    def fake_score(jobs, resume_text, cfg, store, seam=None, top_n=None):
        for j in jobs:
            store.update_scores(j.id, llm={
                "score": 88.0, "reasoning": "great", "top_matches": [],
                "gaps": []})
        return jobs

    monkeypatch.setattr(cli_module, "search_all_sources", fake_search)
    monkeypatch.setattr(cli_module, "score_pipeline", fake_score)
    runner = CliRunner()
    result = runner.invoke(search_jobs, [
        "python dev", "--db", str(tmp_path / "t.db"), "--alerts"])
    assert result.exit_code == 0, result.output
    # Telegram unconfigured in tests → pending message, nothing marked
    assert "pending" in result.output
    import sqlite3
    conn = sqlite3.connect(tmp_path / "t.db")
    assert conn.execute("SELECT COUNT(*) FROM alert_log").fetchone()[0] == 0
    conn.close()
