"""Per-service state persistence.

Each signup target gets a JSON file under data/signup_artifacts/{service}/state.json
that records:
- email_alias used (e.g. "adzuna@priv.email")
- password (generated, persisted so retries work)
- form_fields captured
- email_verify_received_at, code_used
- dashboard_url
- extracted api_key(s) (may be multiple: ADZUNA_APP_ID + ADZUNA_API_KEY, etc.)
- final_env_keys (the .env variable names + values that should land in .env)

This lets each service be re-run independently — if a script crashes mid-flow,
the next invocation picks up where it left off.
"""
from __future__ import annotations

import json
import os
import secrets
import string
from pathlib import Path
from typing import Optional

# Repo-relative (2026-08-27 consolidation): ingest/data/signup_artifacts/
# (lib/ -> parents[3] = ingest/). Override via SIGNUP_ARTIFACTS_DIR env if needed.
ARTIFACTS = Path(os.environ.get("SIGNUP_ARTIFACTS_DIR") or
                 Path(__file__).resolve().parents[3] / "data" / "signup_artifacts")


def state_path(service: str) -> Path:
    p = ARTIFACTS / service / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_state(service: str) -> dict:
    p = state_path(service)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_state(service: str, state: dict) -> None:
    p = state_path(service)
    p.write_text(json.dumps(state, indent=2, sort_keys=True))


def update_state(service: str, **kwargs) -> dict:
    s = load_state(service)
    s.update(kwargs)
    save_state(service, s)
    return s


def gen_password(length: int = 20) -> str:
    """Generate a strong password (mixed-case letters, digits, and symbols)."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        # Ensure complexity requirements met
        if (any(c.isupper() for c in pw) and
            any(c.islower() for c in pw) and
            any(c.isdigit() for c in pw) and
            any(c in "!@#$%^&*" for c in pw)):
            return pw


def env_alias_for_service(service: str) -> str:
    """Return the priv.email alias to use for a service.

    Catch-all on priv.email → all aliases forward to the same hotmail.
    Distinct aliases let us tell apart which verification email came from which service.
    """
    return f"{service}@priv.email"


def get_captured_env(state: dict) -> dict[str, str]:
    """Pull the env_kv dict from the state file (set by each service's
    extract step after pulling the API key from the dashboard)."""
    return state.get("env_kv") or {}
