#!/usr/bin/env python3
"""Try Careerjet publisher wizard step 1 via plain HTTP (no turnstile token).

If the server enforces turnstile_token, we fall back to a ZenRows browser
session for the whole wizard.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cj_session as cj


def main() -> int:
    assert cj.login(), "login failed"
    status, html, final = cj.get(f"{cj.CJ}/partners/register/as-publisher")
    csrf = cj.csrf_of(html)
    print(f"csrf: {csrf[:12]}...")

    data = {
        "csrf_token": csrf,
        "partner_type": "individual",
        "company_name": "",
        "company_vat_number": "",
        "fname": "Alex",
        "lname": "Sourcer",
        "phone_number": "",
        "address": "1 Market St",
        "address2": "",
        "city": "San Francisco",
        "state": "CA",
        "postal_code": "94105",
        "country": "US",
    }
    status, body, final = cj.post(
        f"{cj.CJ}/partners/register/as-publisher", data,
        referer=f"{cj.CJ}/partners/register/as-publisher")
    text = cj.text_of(body)
    print(f"POST -> {status}, final={final}")
    print(f"body excerpt: {text[:600]}")
    # Save for inspection (CodeRabbit round-3: parenthesize — the method
    # call binds to the string literal, not the Path, without them).
    artifact = HERE / "artifacts" / "pub_step1_resp.html"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(body)
    # What fields does the response form have now?
    import re
    names = sorted(set(re.findall(r'name="([a-z_0-9]+)"', body)))
    print(f"response form fields: {names}")
    print("turnstile present:", "turnstile" in body.lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
