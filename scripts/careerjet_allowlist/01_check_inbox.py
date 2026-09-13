#!/usr/bin/env python3
"""Check the v3-mail inbox for existing Careerjet emails (CAREERJET_PARTNER_EMAIL).

Goal: find the original signup confirmation / any account emails that tell us
the account state, before attempting the forgot-password flow.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ingest" / "scripts" / "signup"))

from lib import v3mail


def _env_file_value(name: str) -> str:
    """Read `name` from ingest/.env (KEY=value lines) if present."""
    env_path = Path(__file__).resolve().parents[1] / "ingest" / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


# S8-A scrub: the shop alias address is the CAREERJET_PARTNER_EMAIL
# credential value — env-only (os.environ first, ingest/.env fallback).
EMAIL = (os.environ.get("CAREERJET_PARTNER_EMAIL")
         or _env_file_value("CAREERJET_PARTNER_EMAIL"))

emails = v3mail.list_emails(limit=200, address=EMAIL, include_body=False)
print(f"Total emails for {EMAIL}: {len(emails)}")
for e in emails:
    print(f"  id={e['id']} at={e.get('received_at','?')[:19]} from={e.get('from_address','?')[:40]} subj={e.get('subject','')[:80]}")
