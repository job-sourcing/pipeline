"""Real-time posting alerts (Sprint 3 — methodology §9 "cron + alerts").

T2 has the downstream notification layer but no real-time posting-alert
path; this module IS that path. Design carries over the validated
Telegram-notifier semantics from the track1 autopilot experiment
(archive/track1-experiments/07_autopilot_jobhunt — POST to
api.telegram.org/bot{token}/sendMessage with HTML parse_mode; returns
False on failure without crashing; SILENTLY SKIPPED when the token/chat-id
are absent so results always persist regardless):

- pending_alerts(): new shortlisted jobs with llm_score >= threshold that
  have NOT yet been alerted on the channel (alert_log guards re-sends).
- format_digest(): one compact HTML digest per pass (not per-job spam).
- run_alert_pass(): select → send → mark. Marks ONLY on successful send —
  an unconfigured or failing channel leaves the jobs pending, so they
  alert later when the channel works (flag, never drop — notifications are
  additive, persistence is primary).

The scheduler daemon (scripts/search_scheduler.py) drives this on a cron
cadence; the CLI --alerts flag drives it per-run.
"""
from __future__ import annotations

import html
from typing import Optional

import requests

from .config import Config
from .models import Job
from .storage import Store

__all__ = [
    "pending_alerts", "format_digest", "send_telegram",
    "run_alert_pass", "ALERT_MIN_LLM_SCORE",
]

ALERT_MIN_LLM_SCORE = 75.0       # "strong fit" band floor (BANDS: 75+)
_TELEGRAM_TIMEOUT_S = 15


def pending_alerts(store: Store, query: Optional[str] = None,
                   min_llm_score: float = ALERT_MIN_LLM_SCORE,
                   limit: int = 10, channel: str = "telegram") -> list[Job]:
    """High-fit jobs not yet alerted on `channel`.

    Selection: shortlisted (not skipped), llm_score >= min_llm_score, id not
    in alert_log for the channel. Ghost candidates are excluded — the §708
    policy keeps them out of the primary index, and an alert is the most
    primary-index thing there is. Query=None scans the whole tracker (the
    scheduler's cross-query digest).
    """
    alerted = store.alerted_ids(channel)
    if query:
        jobs = store.jobs_by_query(query, limit=500)
    else:
        rows = store.conn.execute(
            "SELECT * FROM jobs WHERE llm_score IS NOT NULL "
            "ORDER BY llm_score DESC, date_posted DESC LIMIT 500").fetchall()
        jobs = [store._row_to_job(r) for r in rows]
    selected = [
        j for j in jobs
        if j.id is not None
        and j.id not in alerted
        and j.llm_score is not None
        and j.llm_score >= min_llm_score
        and (j.status or "shortlisted") == "shortlisted"
        and not j.ghost_candidate
    ]
    selected.sort(key=lambda j: (j.llm_score or 0), reverse=True)
    return selected[:limit]


def format_digest(jobs: list[Job], query: str = "") -> str:
    """One Telegram HTML message for the pass (title/company/score/link)."""
    label = html.escape(query) if query else "all tracked queries"
    lines = [f"<b>{len(jobs)} new high-fit job"
             f"{'s' if len(jobs) != 1 else ''}</b> ({label})"]
    for j in jobs:
        title = html.escape((j.title or "")[:80])
        company = html.escape((j.company or "")[:40])
        score = j.llm_score if j.llm_score is not None else "-"
        line = f"• <b>{company}</b> — {title} [{score}]"
        if j.link:
            line += f"\n  {html.escape(j.link)}"
        lines.append(line)
    return "\n".join(lines)


def send_telegram(html_text: str, cfg: Config) -> bool:
    """POST the digest to the Telegram Bot API (validated pattern).

    Returns True on a successful send, False otherwise (HTTP error, network
    error, or unconfigured channel). NEVER raises — notifications are
    additive; a failed send must not fail the search run.
    """
    token = getattr(cfg, "telegram_bot_token", "") or ""
    chat_id = getattr(cfg, "telegram_chat_id", "") or ""
    if not token or not chat_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": html_text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=_TELEGRAM_TIMEOUT_S)
        return r.status_code == 200
    except requests.RequestException:
        return False


def run_alert_pass(store: Store, cfg: Config, query: Optional[str] = None,
                   min_llm_score: float = ALERT_MIN_LLM_SCORE,
                   limit: int = 10, channel: str = "telegram"
                   ) -> dict:
    """Select → send → mark. Returns a summary dict for the CLI report.

    {'pending': N, 'sent': bool, 'channel_configured': bool}
    - Unconfigured channel: pending stays unmarked (alerts fire later).
    - Failed send: same — only a 200 response marks the log.
    """
    jobs = pending_alerts(store, query, min_llm_score, limit, channel)
    if not jobs:
        return {"pending": 0, "sent": False, "channel_configured":
                bool(getattr(cfg, "telegram_bot_token", "")
                     and getattr(cfg, "telegram_chat_id", ""))}
    digest = format_digest(jobs, query or "")
    sent = send_telegram(digest, cfg)
    if sent:
        store.mark_alerted((j.id for j in jobs), channel=channel)
    return {"pending": len(jobs), "sent": sent, "channel_configured":
            bool(getattr(cfg, "telegram_bot_token", "")
                 and getattr(cfg, "telegram_chat_id", ""))}
