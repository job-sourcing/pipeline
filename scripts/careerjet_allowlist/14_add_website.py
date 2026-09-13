#!/usr/bin/env python3
"""Add a website to the Careerjet publisher account (no Turnstile on this form).

This triggers API-key issuance. After this, /partners/publisher/api/{key}
becomes the key management page (with the ip-submit form).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "ingest" / "scripts" / "signup"))

import cj_session as cj

STATE = HERE.parents[1] / "ingest" / "data" / "signup_artifacts" / "careerjet" / "state.json"


def partner_login() -> None:
    state = json.loads(STATE.read_text())
    status, html, _ = cj.get(f"{cj.CJ}/login?redir=PARTNER_PUBLISHER_OVERVIEW")
    csrf = cj.csrf_of(html)
    status, body, final = cj.post(f"{cj.CJ}/login", {
        "csrf_token": csrf, "redir": "PARTNER_PUBLISHER_OVERVIEW", "universe": "jobseeker",
        "email": state["email"], "password": state["password"],
    }, referer=f"{cj.CJ}/login?redir=PARTNER_PUBLISHER_OVERVIEW")
    assert "/login" not in final, "partner login failed"
    print("partner login: OK")


def main() -> int:
    partner_login()
    status, html, final = cj.get(f"{cj.CJ}/partners/publisher/add-website")
    csrf = cj.csrf_of(html)
    data = {
        "csrf_token": csrf,
        "site_url": "https://transcendent-cheesecake-03f934.netlify.app",
        "site_country_code": "US",
        "site_monthly_unique_visitors": "A",
        "site_description": (
            "Personal job-market research project: an open-source aggregator "
            "that collects publicly posted tech-sector job listings from "
            "ATS boards and public APIs for labor-market analysis."),
    }
    status, body, final = cj.post(f"{cj.CJ}/partners/publisher/add-website", data,
                                  referer=f"{cj.CJ}/partners/publisher/add-website")
    text = cj.text_of(body)
    print(f"POST -> {status}, final={final}")
    print(f"body: {text[:700]}")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    (STATE.parent / "cj_addsite_resp.html").write_text(body)

    # look for the API key (anchored extraction — the page shows site ids
    # too, and an unanchored 32-hex match can store the wrong one)
    key, anchored = cj.extract_api_key(body)
    state = json.loads(STATE.read_text())
    if key:
        state["env_kv"] = {"CAREERJET_API_KEY_2": key}
        state["key_extraction_status"] = ("ok_from_add_website" if anchored
                                          else "unanchored_verify")
        STATE.write_text(json.dumps(state, indent=2, sort_keys=True))
        print(f"\nAPI KEY FOUND: {key[:6]}...{key[-4:]} "
              f"(anchored={anchored})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
