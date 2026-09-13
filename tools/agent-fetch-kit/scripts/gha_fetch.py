#!/usr/bin/env python3
"""GHA-side fetcher. Invoked by .github/workflows/scrape.yml.

Env (set by the workflow):
  TARGET_URL  url to fetch
  MODE        curl | impersonate | browser
  WAIT_MS     browser/JS virtual-time budget
  TIMEOUT_S   per-URL timeout
Writes ./out/<slug>.{body,meta.json}.
"""
from __future__ import annotations
import os, sys, json, time, hashlib, subprocess, re, pathlib, socket

def slug(u: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', u.lower())[:80].strip('-') or 'root'

def main() -> int:
    url = os.environ.get("TARGET_URL", "").strip()
    mode = os.environ.get("MODE", "impersonate").strip().lower()
    try:
        wait_ms = int(os.environ.get("WAIT_MS", "4000"))
    except ValueError:
        wait_ms = 4000
    try:
        timeout_s = int(os.environ.get("TIMEOUT_S", "30"))
    except ValueError:
        timeout_s = 30
    if not url:
        print("TARGET_URL not set", file=sys.stderr); return 2
    outdir = pathlib.Path("out"); outdir.mkdir(exist_ok=True)
    s = slug(url)
    body_path = outdir / f"{s}.body"
    meta_path = outdir / f"{s}.meta.json"
    started = time.time()
    status: int | str | None = None
    err = None
    try:
        if mode == "curl":
            import requests  # type: ignore
            r = requests.get(url, timeout=timeout_s, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            })
            status = r.status_code
            body_path.write_bytes(r.content)
        elif mode == "impersonate":
            from curl_cffi import requests as creq  # type: ignore
            r = creq.get(url, impersonate="chrome131", timeout=timeout_s)
            status = r.status_code
            body_path.write_bytes(r.content)
        elif mode == "browser":
            import shutil
            chrome = (shutil.which("google-chrome")
                      or shutil.which("google-chrome-stable")
                      or shutil.which("chromium"))
            if not chrome:
                raise RuntimeError("no chrome/chromium binary on runner")
            cmd = [chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
                   "--disable-dev-shm-usage", "--no-first-run",
                   f"--virtual-time-budget={wait_ms}", "--dump-dom", url]
            p = subprocess.run(cmd, capture_output=True, timeout=timeout_s + 30)
            # chrome returns 0 on success — translate to HTTP 200; non-zero → 500 (browser-side error)
            status = 200 if p.returncode == 0 else 500
            body_path.write_bytes(p.stdout)
            if p.stderr:
                (outdir / f"{s}.chrome.stderr").write_bytes(p.stderr[:20000])
        else:
            raise ValueError(f"unknown mode {mode!r}")
    except Exception as e:
        err = repr(e)
        # leave status as None on error (don't use string "error" — backend_gha normalizes anyway,
        # but None is cleaner and avoids any TypeError in the router's r.ok check)
        if status is None:
            status = None
    elapsed_ms = int((time.time() - started) * 1000)
    body = body_path.read_bytes() if body_path.exists() else b""
    title = None
    m = re.search(rb"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    if m:
        title = m.group(1).decode("utf-8", "ignore").strip()[:300]
    meta = {
        "url": url, "mode": mode, "wait_ms": wait_ms, "timeout_s": timeout_s,
        "status": status, "elapsed_ms": elapsed_ms, "error": err,
        "title": title, "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest()[:16] if body else None,
        "hostname": socket.gethostname(),
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    # return non-zero on error so the workflow step fails (and conclusion != 'success');
    # the Upload step uses if: always() so the artifact (with meta.json) is still uploaded.
    return 0 if err is None else 1

if __name__ == "__main__":
    sys.exit(main())
