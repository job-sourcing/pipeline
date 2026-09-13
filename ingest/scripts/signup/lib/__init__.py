"""Shared helpers for sign-up automation.

Design:
- Stealth Playwright contexts (one factory function).
- ImprovMX email polling — wait for the next email to a specific priv.email alias.
- Per-service state on disk so each service can be retried independently.
- All credentials land in <repo>/ingest/data/signup_artifacts/ (repo-relative since 2026-08-27)
  (gitignored) and the final .env at repo root is written by apply_env.py.
"""
