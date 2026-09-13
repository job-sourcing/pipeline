"""Unified config loader (DECISIONS.md D6/R12).

Lifted components bring three config styles (career-ops YAML, ResumeWing
env, ai-job-search markdown+CSV). This module maps them all into one object:
  1. .env at repo root (committed — git is our disk; documented policy)
  2. process environment (overrides .env)
  3. defaults
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser (no python-dotenv dependency)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


@dataclass
class Config:
    # Paths
    db_path: Path = field(default_factory=lambda: REPO_ROOT / "data" / "tracker.db")
    llm_dir: Path = field(default_factory=lambda: REPO_ROOT / "llm")
    node_bin: str = "node"

    # Sourcing
    http_timeout_s: int = 15
    max_jobs_per_source: int = 20

    # Scoring pipeline (D8: relative top-N, never absolute cutoff)
    llm_top_n: int = 20
    llm_batch_size: int = 10
    llm_batch_delay_s: float = 1.0

    # Seam (D1.1)
    subprocess_timeout_s: int = 120
    llm_max_retries: int = 2
    llm_retry_backoff_s: float = 8.0

    # Dedup thresholds (job-ops defaults)
    dedup_title_threshold: int = 90
    dedup_employer_threshold: int = 85

    # Optional API keys (free tiers, D3). Resolved from the process env by
    # __post_init__ so a bare Config() — used by the sources/__init__.py
    # registry at import time and by the live source tests — reflects runtime
    # keys without forcing callers through load_config(). File-based .env
    # values for the same keys are additionally picked up by load_config().
    adzuna_app_id: str = ""
    adzuna_api_key: str = ""
    usajobs_api_key: str = ""
    usajobs_user_agent: str = ""
    findwork_api_key: str = ""
    jooble_api_key: str = ""
    careerjet_api_key: str = ""
    # Registered publisher site — sent as the Referer header. Requests WITH
    # this Referer are authorized by the API key directly; requests WITHOUT
    # it fall back to the publisher IP-allowlist (which our multi-egress
    # container can't reliably satisfy). Verified live 2026-08-27.
    careerjet_referer: str = ""
    jsearch_api_key: str = ""
    # ZenRows premium-proxy transport (geo-blocked sources: USAJobs fallback,
    # ZipRecruiter/Glassdoor). Empty = transport disabled.
    zenrows_api_key: str = ""
    # Netlify edge-scraper transport (US egress — the ZenRows fallback
    # when credits run out; verified live 2026-08-29 on USAJobs).
    # Empty = transport disabled.
    netlify_scraper_token: str = ""
    netlify_scraper_url: str = ("https://6a7fb9ac8af4eca2cb615414--"
                                "transcendent-cheesecake-03f934.netlify.app")

    # JobSpy aggregator site list (SRC-JOBSPY-2). Default excludes LinkedIn,
    # Bayt, Naukri to avoid overlap with our LinkedIn Guest source and to keep
    # request volume polite. User overrides via JOBSY_SITES env (comma-sep).
    jobspy_sites: list[str] = field(default_factory=lambda: [
        "indeed", "glassdoor", "google_jobs", "ziprecruiter",
    ])

    # ATS-direct lists (SRC-ATS-AS). Each entry is one company's job board.
    # smartrecruiters_slugs default = verified working slugs on the public
    #   GET /v1/companies/{slug}/postings endpoint (200 OK with content).
    #   Override via SMARTRECRUITERS_SLUGS env (comma-separated).
    # Ashby tenant slugs for the HTML-board path (rebuilt 2026-08-27 Step B:
    # jobs.ashbyhq.com/{slug} needs no auth). Empty = the adapter's curated
    # verified defaults (openai/notion/ramp/linear/ashby). Override via
    # ASHBY_ORGS. The per-customer keyed API (ASHBY_API_KEY) stays
    # documented in ashby.py for later.
    ashby_orgs: list[str] = field(default_factory=list)
    # Personio tenants ({slug}.jobs.personio.de) — XML feed + HTML fallback,
    # domain-throttled (≥25s global spacing enforced in the adapter).
    personio_slugs: list[str] = field(default_factory=list)
    ashby_api_key: str = ""               # per-org Ashby key (ASHBY_API_KEY env)
    smartrecruiters_slugs: list[str] = field(default_factory=lambda: [
        "smartrecruiters",   # SR's own board (~8 public postings)
        "averydennison",     # ~400+ postings (largest of the verified set)
        "geico",
        "bigcommerce",
        "wework",
        "yardi",
    ])

    # Supabase edge-proxy transport (S6-bis, from the agent-fetch-kit
    # integration 2026-09-08): free GET-only rotating-IP proxy with region
    # pinning (x-region). No custom-header forwarding → NOT usable for
    # authed APIs (USAJobs); a resilience fallback for plain-GET JSON
    # boards. See transport.supabase_fetch_json.
    supabase_proxy_url: str = ""
    supabase_proxy_token: str = ""

    # Workday CXS boards ("tenant|instance|site" — the seed format; see
    # sources/workday.py). Default = the NVIDIA pilot board (1,428 US
    # full-time roles verified exhaustive 2026-09-08). Override via
    # WORKDAY_BOARDS env (comma-separated).
    workday_boards: list[str] = field(default_factory=lambda: [
        "nvidia|wd5|nvidiaexternalcareersite",
    ])

    # ATS-direct lists (SRC-ATS-GL). Each entry is one company's job board.
    # greenhouse_boards default = 15 well-known tech-company board tokens on
    #   boards-api.greenhouse.io/v1/boards/{token}/jobs (no-auth JSON; main
    #   agent curl-verified Stripe = 580 jobs; SRC-ATS-GL verified all 15
    #   return >= 16 jobs 2026-08). Override via GREENHOUSE_BOARDS env
    #   (comma-separated).
    # lever_slugs default = 8 company slugs verified live during SRC-ATS-GL on
    #   api.lever.co/v0/postings/{slug}?mode=json (Notion + the original main-
    #   agent list all 404'd; the 8 below are the only ones that returned real
    #   postings). Override via LEVER_SLUGS env (comma-separated).
    greenhouse_boards: list[str] = field(default_factory=lambda: [
        "stripe", "datadog", "anthropic", "databricks", "cloudflare",
        "brex", "scaleai", "airbnb", "coinbase", "figma",
        "reddit", "robinhood", "asana", "gusto", "vercel",
    ])
    lever_slugs: list[str] = field(default_factory=lambda: [
        "spotify", "ro", "capital", "qonto", "wealthfront",
        "moonpay", "tri", "newton",
    ])

    # Secrets that must NEVER come from files — runtime env only
    github_pat: str = ""

    # Telegram alerts (Sprint 3 — cron+alerts). Empty = channel
    # unconfigured: the alert pass is silently skipped, results still
    # persist (the validated autopilot notifier semantics).
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # HN "Ask HN: Who is hiring?" monthly thread (SRC-HN). Empty string means
    # auto-discover the most recent monthly thread via the HN Algolia API at
    # fetch time. Set to a specific numeric thread ID (e.g. "49156683" for the
    # August 2026 thread) to pin to a specific month — useful for replayable
    # runs against a known-good thread when the current month's thread hasn't
    # been posted yet (the thread goes live on the first weekday of each
    # month around 15:00 UTC).
    hn_thread_id: str = ""

    def __post_init__(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Resolve the optional API-key fields from the process env so the
        # source registry's import-time configured-flags and the live tests
        # see real keys without a load_config() call. Only the key fields are
        # touched here — paths/ints stay whatever the constructor was given
        # (avoids clobbering an explicit db_path=tmp_path/... in tests).
        for env_key, (attr, cast) in _ENV_MAP.items():
            if attr in _OPTIONAL_KEY_FIELDS:
                raw = os.environ.get(env_key)
                if raw:
                    try:
                        setattr(self, attr, cast(raw))
                    except (TypeError, ValueError):
                        continue


# Fields __post_init__ is allowed to populate from the process env. Kept
# narrow on purpose: paths and tuning ints are NOT in here so an explicit
# Config(db_path=...) in tests is never silently overwritten by env.
_OPTIONAL_KEY_FIELDS = frozenset({
    "adzuna_app_id", "adzuna_api_key",
    "usajobs_api_key", "usajobs_user_agent",
    "findwork_api_key", "jooble_api_key",
    "careerjet_api_key", "careerjet_referer", "jsearch_api_key",
    "zenrows_api_key", "netlify_scraper_token", "netlify_scraper_url",
    "github_pat",
    "telegram_bot_token", "telegram_chat_id",
    "jobspy_sites",
    "ashby_orgs", "ashby_api_key", "personio_slugs",
    "smartrecruiters_slugs",
    "greenhouse_boards", "lever_slugs",
    "workday_boards",
    "supabase_proxy_url", "supabase_proxy_token",
    "hn_thread_id",
})


def _split_csv(value: str) -> list[str]:
    """Comma-separated env value → stripped non-empty list.

    Used for env-list fields: jobspy_sites, ashby_orgs, smartrecruiters_slugs.
    """
    return [s.strip() for s in value.split(",") if s.strip()]


_ENV_MAP = {
    "JOBSEARCH_DB": ("db_path", Path),
    "JOBSEARCH_HTTP_TIMEOUT": ("http_timeout_s", int),
    "JOBSEARCH_MAX_JOBS": ("max_jobs_per_source", int),
    "JOBSEARCH_LLM_TOP_N": ("llm_top_n", int),
    "JOBSEARCH_LLM_TIMEOUT": ("subprocess_timeout_s", int),
    "ADZUNA_APP_ID": ("adzuna_app_id", str),
    "ADZUNA_API_KEY": ("adzuna_api_key", str),
    "USAJOBS_API_KEY": ("usajobs_api_key", str),
    "USAJOBS_USER_AGENT": ("usajobs_user_agent", str),
    "FINDWORK_API_KEY": ("findwork_api_key", str),
    "JOOBLE_API_KEY": ("jooble_api_key", str),
    "CAREERJET_API_KEY": ("careerjet_api_key", str),
    "CAREERJET_REFERER": ("careerjet_referer", str),
    "ZENROWS_API_KEY": ("zenrows_api_key", str),
    "NETLIFY_SCRAPER_TOKEN": ("netlify_scraper_token", str),
    "NETLIFY_SCRAPER_URL": ("netlify_scraper_url", str),
    "TELEGRAM_BOT_TOKEN": ("telegram_bot_token", str),
    "TELEGRAM_CHAT_ID": ("telegram_chat_id", str),
    "JSEARCH_API_KEY": ("jsearch_api_key", str),
    "GH_PAT": ("github_pat", str),
    "JOBSY_SITES": ("jobspy_sites", _split_csv),
    "ASHBY_ORGS": ("ashby_orgs", _split_csv),
    "PERSONIO_SLUGS": ("personio_slugs", _split_csv),
    "ASHBY_API_KEY": ("ashby_api_key", str),
    "SMARTRECRUITERS_SLUGS": ("smartrecruiters_slugs", _split_csv),
    "GREENHOUSE_BOARDS": ("greenhouse_boards", _split_csv),
    "LEVER_SLUGS": ("lever_slugs", _split_csv),
    "WORKDAY_BOARDS": ("workday_boards", _split_csv),
    "SUPABASE_PROXY_URL": ("supabase_proxy_url", str),
    "SUPABASE_PROXY_TOKEN": ("supabase_proxy_token", str),
    "HN_THREAD_ID": ("hn_thread_id", str),
}


def load_config(env_file: Path | None = None) -> Config:
    """Build Config from .env + process env (process env wins)."""
    file_values = _load_env_file(env_file or REPO_ROOT / ".env")
    cfg = Config()
    for env_key, (attr, cast) in _ENV_MAP.items():
        raw = os.environ.get(env_key) or file_values.get(env_key)
        if raw:
            try:
                setattr(cfg, attr, cast(raw))
            except (TypeError, ValueError):
                continue
    return cfg
