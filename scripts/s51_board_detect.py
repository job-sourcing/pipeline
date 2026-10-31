#!/usr/bin/env python3
"""s51_board_detect.py — the S51-C board-detection pass (the GenAI
sector's per-company job-capture bootstrap).

For every company site in the master list: try the common careers-URL
candidates, fetch (curl_cffi direct → the vendor relay fleet as the
blocked-class carrier), detect the ATS platform (role_sweep's
_PLATFORM_PATTERNS + ats_resolve.detect_platform), and record the
board spec for the fold into board-watch / the ATS adapters.

Output: ingest/data/ats_seed/s51_genai_boards.json + a summary MD in
audit/s51_genai/. Free tier only (direct + the netlify relay).
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))

REPO = Path(__file__).resolve().parent.parent
MASTER = REPO / "audit/s51_genai/MASTER_COMPANY_LIST.md"
OUT = REPO / "ingest/data/ats_seed/s51_genai_boards.json"
SUMMARY = REPO / "audit/s51_genai/board_detection_summary.md"

CANDIDATES = ["careers", "jobs", "company/careers", "about/careers",
              "en/careers", "careers.html", "join-us", "work-with-us"]

_MARKERS = [
    ("greenhouse", "boards.greenhouse.io"),
    ("greenhouse-embed", "boards.greenhouse.com/embed"),
    ("lever", "jobs.lever.co"),
    ("ashby", "jobs.ashbyhq.com"),
    ("ashby-embed", "jobs.ashbyhq.com/embed"),
    ("workable", "apply.workable.com"),
    ("workable-api", "jobs.workable.com"),
    ("teamtailor", ".teamtailor.com"),
    ("breezy", ".breezy.hr"),
    ("smartrecruiters", "smartrecruiters.com"),
    ("icims", ".icims.com"),
    ("workday", ".myworkdayjobs.com"),
    ("bamboohr", ".bamboohr.com"),
    ("paylocity", "recruiting.paylocity.com"),
    ("jobvite", ".jobvite.com"),
    ("jazzhr", "apply.jazzhr.com"),
    ("recruitee", ".recruitee.com"),
    ("personio", ".jobs.personio.de"),
    ("oracle-hcm", ".oraclecloud.com/hcmUI"),
    ("successfactors", ".successfactors.com"),
    ("myworkdaysite", "myworkdaysite.com"),
    ("workstream", ".workstream.is"),
    ("paycom", "paycom.com"),
    ("adp", "adp.com/"),
    ("gusto", "gusto.com/"),
    ("rippling", "rippling.com/"),
    ("gem", "gem.com/"),
    ("greenhouse-jobboard", "job-boards.greenhouse.io"),
    ("comparator-ats", "comparator.com"),
    ("eightfold", "eightfold.ai"),
    ("phenom", "phenompeople.com"),
]


def _extract_sites() -> list[dict]:
    """Parse the master list MD tables → [{company, hq, site, cat}]."""
    cat = ""
    rows: list[dict] = []
    for line in MASTER.read_text().splitlines():
        m = re.match(r"^## \d+\. (.+)$", line)
        if m:
            cat = m.group(1).strip()
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0] in ("Company", "Company (EN)",
                                          "---", "—"):
            continue
        company, hq, site = cells[0], cells[1], cells[2]
        if not site or site in ("—", "-", "wikipedia", "aicp.com directory",
                                "UK Screen Alliance members",
                                "AICP member companies (~283)"):
            continue
        if "." not in site or " " in site and "http" not in site:
            continue
        if "TODO" in site or "verify" in site.lower():
            pass                       # still try — verification IS the job
        site = site.replace("✦", "").strip()
        if not site:
            continue
        rows.append({"company": company, "hq": hq, "site": site,
                     "cat": cat})
    return rows


def _fetch(url: str, timeout: float = 12.0) -> tuple[str, int, str]:
    """curl_cffi direct → the relay fleet. Returns (body, status, mode)."""
    try:
        from curl_cffi import requests as cffi
        r = cffi.get(url, impersonate="chrome131", timeout=timeout,
                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0;"
                               " Win64; x64) AppleWebKit/537.36 Chrome/"
                               "124.0.0.0 Safari/537.36"},
                     allow_redirects=True)
        return r.text[:200_000], r.status_code, "direct"
    except Exception:                          # noqa: BLE001
        pass
    from jobsearch.vendor_relay import relay_fetch
    res = relay_fetch(url, method="GET", timeout=timeout)
    if res["status"] == 200:
        return res["text"][:200_000], 200, "relay"
    return "", res["status"] or 0, "relay"


def _detect(body: str, final_url: str) -> str:
    hay = body[:60_000].lower() + " " + final_url.lower()
    for plat, marker in _MARKERS:
        if marker in hay:
            return plat
    if re.search(r"job openings|open positions|view jobs|join our team",
                 hay):
        return "custom-html"
    return ""


def main() -> int:
    sites = _extract_sites()
    print(f"[board-detect] {len(sites)} companies from the master list")
    results: list[dict] = []
    for i, row in enumerate(sites):
        site = row["site"]
        if not site.startswith("http"):
            site = f"https://{site}"
        base = site.rstrip("/")
        found = ""
        for cand in CANDIDATES:
            url = f"{base}/{cand}"
            try:
                body, status, mode = _fetch(url)
            except Exception as exc:              # noqa: BLE001
                status, body, mode = 0, f"err:{exc}", ""
            if status == 200 and len(body) > 500:
                plat = _detect(body, url)
                if plat or "job" in body[:30000].lower():
                    found = plat or "custom?"
                    results.append({**row, "careers_url": url,
                                    "platform": found, "mode": mode,
                                    "body_len": len(body)})
                    print(f"  ✓ {row['company'][:28]:28} {found:14} {url}")
                    break
            time.sleep(0.6)
        if not found:
            results.append({**row, "careers_url": "", "platform": "none",
                            "mode": "", "body_len": 0})
            print(f"  ✗ {row['company'][:28]:28} (no careers candidate)")
        time.sleep(0.4)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
         "n": len(results), "boards": results}, ensure_ascii=False,
        indent=1))
    # summary
    ok = [r for r in results if r["platform"] not in ("", "none")]
    ats: dict = {}
    for r in ok:
        ats[r["platform"]] = ats.get(r["platform"], 0) + 1
    SUMMARY.write_text(
        "# S51-C board detection summary\n\n"
        f"- companies probed: {len(results)}\n"
        f"- careers pages found: {len(ok)}\n"
        f"- by platform: {json.dumps(ats, indent=1)}\n\n"
        "| Company | Platform | Careers URL |\n|---|---|---|\n"
        + "\n".join(f"| {r['company']} | {r['platform']} | "
                    f"{r['careers_url']} |" for r in ok) + "\n")
    print(f"[board-detect] DONE: {len(ok)}/{len(results)} boards found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
