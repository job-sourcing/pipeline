#!/usr/bin/env python3
"""s49_direct_reprobe.py — classify the fc-served probe fetches' direct-tier
failure classes (why they escalated to firecrawl). Standalone: NO repo
imports (the sweep is being edited concurrently). Polite: 1s sleeps.
Output: audit/s49/direct_reprobe.md row + stdout table.
"""
import time
import urllib3
from curl_cffi import requests as cffi

urllib3.disable_warnings()

UA_HEADERS = {
    "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/131.0.0.0 Safari/537.36"),
    "accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "*/*;q=0.8"),
    "accept-language": "en-US,en;q=0.9",
}


def classify(status: int, body: str) -> str:
    low = (body or "").lower()
    if status in (403, 401):
        if "cloudflare" in low or "cf-ray" in low or "just a moment" in low:
            return "cf_challenge"
        return "http_blocked"
    if status == 404:
        return "http_404"
    if status >= 500:
        return "http_5xx"
    if status == 0:
        return "conn_fail"
    if status == 200:
        if len(body) < 200:
            return "js_wall_thin"     # 200 but empty shell → JS render needed
        if ("cloudflare" in low and "challenge" in low) or \
                "checking your browser" in low:
            return "cf_soft"
        return "http_200_ok"
    return f"http_{status}"


def main() -> None:
    urls = []
    for line in open("/tmp/fc_urls.txt"):
        parts = line.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].startswith("http"):
            urls.append((parts[0], parts[1]))
    print(f"re-probing {len(urls)} fc-served fetches, direct curl_cffi")
    from collections import Counter
    out_rows = []
    cls = Counter()
    for url, path in urls:
        try:
            r = cffi.get(url, impersonate="chrome131", timeout=20,
                         headers=UA_HEADERS, verify=False)
            body = r.text or ""
            c = classify(r.status_code, body)
        except Exception as exc:                 # noqa: BLE001
            body = ""
            c = "conn_fail" if "timed out" in str(exc) or \
                "Connection" in str(exc) or "SSL" in str(exc) else \
                f"exc:{type(exc).__name__}"
        cls[c] += 1
        out_rows.append(f"| {url[:60]} | {c} |")
        time.sleep(1.0)
    print()
    print("| url | direct class |")
    print("|---|---|")
    for row in out_rows:
        print(row)
    print()
    print("CLASS HISTOGRAM:", cls.most_common())
    stealth_fixable = sum(v for k, v in cls.items()
                          if k.startswith(("js_wall", "cf_soft")))
    us_ip_fixable = sum(v for k, v in cls.items()
                        if k in ("cf_challenge", "http_blocked"))
    retryable = sum(v for k, v in cls.items() if k in ("conn_fail", "http_5xx"))
    print(f"stealth-browser would displace: {stealth_fixable}; "
          f"US-egress would displace: {us_ip_fixable}; "
          f"plain retry may fix: {retryable}; "
          f"already-ok direct (fc was wasted): {cls.get('http_200_ok', 0)}")


if __name__ == "__main__":
    main()
