"""Configuration: load credentials from .env (committed) or real env vars."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

# Repo root = parent of lib/ (which is parent of this file's dir fetchkit/)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_REPO_ENV = _REPO_ROOT / ".env"

def _parse_env_file(path: Path) -> dict:
    out: dict = {}
    if not path.exists(): return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "): line = line[7:].lstrip()
        k, _, v = line.partition("=")
        k = k.strip(); v = v.strip().strip('"').strip("'")
        # strip inline comments (only if not inside quotes — simple heuristic)
        if " #" in v and not (v.startswith('"') or v.startswith("'")):
            v = v.split(" #", 1)[0].rstrip()
        if k: out[k] = v
    return out

def _load_env_file(path: str = ".env") -> None:
    """Load .env into os.environ (idempotent; does not override real env).

    Order (first wins, mirrors shell `source` semantics): repo-root .env → cwd .env → real env.
    This prevents a cwd-level .env (e.g. /home/z/my-project/.env with DATABASE_URL) from
    shadowing the kit's repo .env when the agent runs from /home/z/my-project.
    """
    merged: dict = {}
    # repo-root .env first (the kit's committed credentials)
    merged.update(_parse_env_file(_REPO_ENV))
    # then cwd .env (local overrides) — does NOT override repo values already set
    cwd_env = Path(path).resolve()
    if cwd_env != _REPO_ENV:
        for k, v in _parse_env_file(cwd_env).items():
            if k not in merged: merged[k] = v
    for k, v in merged.items():
        if k not in os.environ:
            os.environ[k] = v

_load_env_file()

@dataclass
class Config:
    """Credentials + endpoints. Reads from env (or .env). Pass None to disable a backend."""
    # GitHub (for GHA remote compute)
    gh_token: str | None = field(default_factory=lambda: os.environ.get("GH_TOKEN"))
    gh_repo: str | None = field(default_factory=lambda: os.environ.get("GH_REPO", "zmytone/agent-fetch-kit"))
    gh_default_ref: str = field(default_factory=lambda: os.environ.get("GH_REF", "main"))
    gh_workflow_filename: str = field(default_factory=lambda: os.environ.get("GH_WORKFLOW", "scrape.yml"))
    # GitLab (backup remote)
    gitlab_pat: str | None = field(default_factory=lambda: os.environ.get("GITLAB_PAT"))
    gitlab_repo: str | None = field(default_factory=lambda: os.environ.get("GITLAB_REPO"))
    # Firecrawl
    firecrawl_api_key: str | None = field(default_factory=lambda: os.environ.get("FIRECRAWL_API_KEY"))
    # Zenrows
    zenrows_api_key: str | None = field(default_factory=lambda: os.environ.get("ZENROWS_API_KEY"))
    # Supabase edge proxy
    supabase_proxy_url: str | None = field(default_factory=lambda: os.environ.get("SUPABASE_PROXY_URL"))
    supabase_proxy_token: str | None = field(default_factory=lambda: os.environ.get("SUPABASE_PROXY_TOKEN"))
    # Netlify edge scraper
    netlify_scraper_url: str | None = field(default_factory=lambda: os.environ.get("NETLIFY_SCRAPER_URL"))
    netlify_token: str | None = field(default_factory=lambda: os.environ.get("NETLIFY_TOKEN"))
    netlify_site_id: str | None = field(default_factory=lambda: os.environ.get("NETLIFY_SITE_ID"))

    # Defaults
    default_impersonate: str = "chrome131"
    history_path: str = field(default_factory=lambda: os.path.expanduser("~/.agent-fetch-kit/history.json"))

    def has(self, backend: str) -> bool:
        """Whether the credentials for a backend are configured."""
        match backend:
            case "local": return True
            case "supabase": return bool(self.supabase_proxy_url and self.supabase_proxy_token)
            case "netlify": return bool(self.netlify_scraper_url and self.netlify_token)
            case "firecrawl": return bool(self.firecrawl_api_key)
            case "zenrows": return bool(self.zenrows_api_key)
            case "gha": return bool(self.gh_token and self.gh_repo)
            case "browser": return True  # patchright/playwright
            case _: return False

    def missing_creds_reason(self, backend: str) -> str | None:
        """Human-readable reason a backend is not configured (for probe diagnostics)."""
        match backend:
            case "supabase":
                if not self.supabase_proxy_url: return "SUPABASE_PROXY_URL not set"
                if not self.supabase_proxy_token: return "SUPABASE_PROXY_TOKEN not set"
            case "netlify":
                if not self.netlify_scraper_url: return "NETLIFY_SCRAPER_URL not set"
                if not self.netlify_token: return "NETLIFY_TOKEN not set"
            case "firecrawl":
                if not self.firecrawl_api_key: return "FIRECRAWL_API_KEY not set"
            case "zenrows":
                if not self.zenrows_api_key: return "ZENROWS_API_KEY not set"
            case "gha":
                if not self.gh_token: return "GH_TOKEN not set"
                if not self.gh_repo: return "GH_REPO not set"
        return None
