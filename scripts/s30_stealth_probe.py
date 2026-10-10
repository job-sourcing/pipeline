#!/usr/bin/env python3
"""S30 sub-agent 2-b: stealth-browser probe (patchright + cached chromium-1243).

Renders a URL, records all XHR/fetch network traffic (method/URL/status),
saves response bodies whose URL matches --match (regex), dumps rendered
HTML + visible text. For SPA career boards where job data loads via XHR.

Usage:
  python3 s30_stealth_probe.py --url URL [--wait-ms 8000] \
      [--match 'api|posting|job'] --prefix /tmp/foo [--click-text 'Load more']
"""
import argparse
import json
import re
import sys
import time

from patchright.sync_api import sync_playwright

CHROME = "/home/z/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--wait-ms", type=int, default=8000)
    ap.add_argument("--match", default="api|posting|job|outer|search|graphql|position|vacancy|career")
    ap.add_argument("--prefix", default="/tmp/stealth")
    ap.add_argument("--extra-wait-s", type=int, default=0)
    ap.add_argument("--scroll", type=int, default=0, help="number of scroll-down steps")
    args = ap.parse_args()

    reqs = []          # every network request
    bodies = {}        # url -> body text (matched only)
    m = re.compile(args.match, re.I)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=CHROME,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900},
                                  locale="en-US")
        page = ctx.new_page()

        def on_request(r):
            reqs.append({"method": r.method, "url": r.url, "type": r.resource_type})

        def on_response(r):
            try:
                if r.request.resource_type not in ("xhr", "fetch"):
                    return
                if not m.search(r.url):
                    return
                ct = (r.headers or {}).get("content-type", "")
                if "json" in ct or "text" in ct or "javascript" not in ct:
                    body = r.text()
                    bodies[r.url] = body[:400000]
            except Exception as e:
                bodies[r.url] = f"<body-error {repr(e)[:120]}>"

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            page.goto(args.url, timeout=45000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"[goto] {repr(e)[:200]}", file=sys.stderr)
        try:
            page.wait_for_load_state("networkidle", timeout=args.wait_ms)
        except Exception:
            pass
        for _ in range(args.scroll):
            try:
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(800)
            except Exception:
                pass
        page.wait_for_timeout(args.extra_wait_s * 1000 + 1500)

        html = page.content()
        title = ""
        try:
            title = page.title()
        except Exception:
            pass
        # visible text
        try:
            text = page.evaluate("() => document.body.innerText")
        except Exception:
            text = ""
        browser.close()

    with open(args.prefix + ".html", "w") as f:
        f.write(html)
    with open(args.prefix + ".text", "w") as f:
        f.write(text or "")
    with open(args.prefix + ".net.json", "w") as f:
        json.dump({"url": args.url, "title": title, "n_reqs": len(reqs),
                   "requests": reqs}, f, indent=1)
    # bodies: one file per matched URL (hashed name) + an index
    index = []
    import hashlib
    for url, body in bodies.items():
        h = hashlib.md5(url.encode()).hexdigest()[:10]
        fn = f"{args.prefix}.body.{h}.txt"
        with open(fn, "w") as f:
            f.write(body)
        index.append({"url": url, "bytes": len(body), "file": fn})
    with open(args.prefix + ".bodies.json", "w") as f:
        json.dump(index, f, indent=1)

    print(json.dumps({"url": args.url, "title": title, "n_reqs": len(reqs),
                      "matched_bodies": len(bodies)}, indent=1))
    for r in reqs:
        if r["type"] in ("xhr", "fetch") or m.search(r["url"]):
            print(f"  {r['method']:4s} {r['type']:7s} {r['url'][:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
