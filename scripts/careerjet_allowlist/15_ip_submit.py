#!/usr/bin/env python3
"""Submit IPs to the Careerjet publisher API allowlist (no Turnstile needed).

The ip-submit form takes csrf_token + ip (newline-separated list, up to 8 IPs).
This is THE automation the user asked for: keep the container egress IP
allowlisted on Careerjet so the search API adapter works.

Usage:
  python3 15_ip_submit.py 47.57.232.232 [more IPs...]
  python3 15_ip_submit.py --auto          # auto-detect egress IP and submit
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "ingest" / "scripts" / "signup"))

import cj_session as cj

STATE = HERE.parents[1] / "ingest" / "data" / "signup_artifacts" / "careerjet" / "state.json"
KEY = "71d024f1fc21a6bbbad342396de1b9b3"
API_PAGE = f"{cj.CJ}/partners/publisher/api/{KEY}"
EGRESS_IP_URL = "https://api.ipify.org"


def partner_login() -> None:
    state: dict = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text())
        except json.JSONDecodeError:
            state = {}
    email, password = state.get("email"), state.get("password")
    if not (email and password):
        # state.json is gitignored and can be lost on recycle — fall back
        # to the durable copy in ingest/.env (git-is-disk policy).
        env = {}
        env_path = HERE.parents[1] / "ingest" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
        email = email or env.get("CAREERJET_PARTNER_EMAIL", "")
        password = password or env.get("CAREERJET_PARTNER_PASSWORD", "")
    assert email and password, (
        "no Careerjet partner credentials: state.json missing and "
        "CAREERJET_PARTNER_EMAIL/PASSWORD absent from ingest/.env")
    status, html, _ = cj.get(f"{cj.CJ}/login?redir=PARTNER_PUBLISHER_OVERVIEW")
    csrf = cj.csrf_of(html)
    status, body, final = cj.post(f"{cj.CJ}/login", {
        "csrf_token": csrf, "redir": "PARTNER_PUBLISHER_OVERVIEW", "universe": "jobseeker",
        "email": email, "password": password,
    }, referer=f"{cj.CJ}/login?redir=PARTNER_PUBLISHER_OVERVIEW")
    assert "/login" not in final, "partner login failed"


def get_current_ips() -> list[str]:
    status, html, _ = cj.get(API_PAGE)
    m = re.search(r'<textarea[^>]*name="ip"[^>]*>([^<]*)</textarea>', html, re.S)
    if not m:
        return []
    return [ip.strip() for ip in m.group(1).split() if ip.strip()]


def get_csrf() -> str:
    status, html, _ = cj.get(API_PAGE)
    return cj.csrf_of(html)


def submit_ips(ips: list[str]) -> bool:
    """POST the full allowlist (existing + new). Returns success."""
    csrf = get_csrf()
    data = {"csrf_token": csrf, "ip": "\n".join(ips)}
    status, body, final = cj.post(
        f"{API_PAGE}/ip-submit", data, referer=API_PAGE)
    ok = status == 200 and "/ip-submit" not in final
    print(f"ip-submit POST -> {status}, final={final} | success={ok}")
    return ok


def detect_egress_ip() -> str:
    with urllib.request.urlopen(EGRESS_IP_URL, timeout=15) as r:
        return r.read().decode().strip()


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    auto = "--auto" in sys.argv

    partner_login()
    current = get_current_ips()
    print(f"current allowlist: {current}")

    if auto:
        egress = detect_egress_ip()
        print(f"detected egress IP: {egress}")
        targets = [ip for ip in current if ip != egress] + [egress]
    else:
        new_ips = args
        targets = list(dict.fromkeys(current + new_ips))

    if len(targets) > 8:
        print(f"WARNING: {len(targets)} IPs exceeds the 8-IP cap; keeping the last 8")
        targets = targets[-8:]

    if targets == current:
        print("allowlist already up to date; nothing to do")
        return 0

    print(f"submitting allowlist: {targets}")
    ok = submit_ips(targets)
    if ok:
        after = get_current_ips()
        print(f"allowlist now: {after}")
        # Persist to state.json — bootstrapping it from the .env creds if
        # the gitignored file was lost to a recycle (the wizard scripts
        # 10/13 also read this state).
        state: dict = {}
        if STATE.exists():
            try:
                state = json.loads(STATE.read_text())
            except json.JSONDecodeError:
                state = {}
        if not state.get("email"):
            env = {}
            env_path = HERE.parents[1] / "ingest" / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip()
            state.setdefault(
                "email", env.get("CAREERJET_PARTNER_EMAIL", ""))
            state.setdefault(
                "password", env.get("CAREERJET_PARTNER_PASSWORD", ""))
        state["allowlist_ips"] = after
        state["allowlist_updated_at"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%S")
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=2, sort_keys=True))
        return 0 if after == targets else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
