"""Backends — each is a function (url, config, **opts) -> FetchResult.

Tiers:
  0 local     — curl_cffi Chrome TLS impersonation (free, ~1s)
  1 supabase  — edge proxy, 15 AWS regions, rotating IP + random JA3, distill (free, ~1s)
  2 netlify   — edge scraper, fetch engine inline, AWS us-east-2 egress (free, ~1s)
  3 firecrawl — managed scrape + JS render + stealth proxy (credits, ~5-10s)
  4 zenrows   — residential proxy + real browser + antibot mode=auto (credits, ~4-15s)
  5 gha       — GitHub Actions remote compute, Azure IP + curl_cffi + system Chrome (free, ~60s)
  6 browser   — local patchright + chromium-1228 (free, ~5-10s, JS render)
"""
from __future__ import annotations
import json, time, urllib.parse, urllib.request, urllib.error, subprocess, re, hashlib
from dataclasses import dataclass, field
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore

@dataclass
class FetchResult:
    url: str
    backend: str
    status: int | None = None
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    elapsed_ms: int = 0
    error: str | None = None
    challenged: bool = False
    challenge_reason: str | None = None
    metadata: dict = field(default_factory=dict)
    attempts: list = field(default_factory=list)  # filled by router

    @property
    def text(self) -> str:
        try: return self.body.decode("utf-8", "ignore")
        except Exception: return ""

    @property
    def ok(self) -> bool:
        # isinstance guard: some backends (gha) may return non-int status (string/0) on error;
        # without this, `200 <= "error" < 300` raises TypeError and crashes the router.
        return isinstance(self.status, int) and 200 <= self.status < 300 and not self.challenged and not self.error

def _headers_lower(d: dict) -> dict:
    return {k.lower(): v for k, v in (d or {}).items()}

# ---------------------------------------------------------------------------
# Tier 0: local curl_cffi
# ---------------------------------------------------------------------------
def backend_local(url: str, config, *, impersonate: str = None, timeout: int = 30, **_) -> FetchResult:
    from curl_cffi import requests as creq
    started = time.time()
    res = FetchResult(url=url, backend="local")
    try:
        r = creq.get(url, impersonate=impersonate or config.default_impersonate, timeout=timeout,
                     headers={"Accept-Language":"en-US,en;q=0.9"})
        res.status = r.status_code
        res.headers = _headers_lower(dict(r.headers))
        res.body = r.content
    except Exception as e:
        res.error = repr(e)
    res.elapsed_ms = int((time.time() - started) * 1000)
    return res

# ---------------------------------------------------------------------------
# Tier 1: Supabase edge proxy
# ---------------------------------------------------------------------------
def backend_supabase(url: str, config, *, mode: str = "raw", region: str | None = None,
                     extract: str | None = None, timeout: int = 30, **_) -> FetchResult:
    proxy = config.supabase_proxy_url
    token = config.supabase_proxy_token
    started = time.time()
    res = FetchResult(url=url, backend="supabase")
    if not proxy or not token:
        res.error = "supabase credentials not configured"; return res
    q = {"url": url, "mode": mode}
    if extract: q["extract"] = extract
    full = f"{proxy}?{urllib.parse.urlencode(q)}"
    headers = {"Authorization": f"Bearer {token}"}
    if region: headers["x-region"] = region
    req = urllib.request.Request(full, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res.status = r.status
            res.headers = _headers_lower(dict(r.headers))
            res.body = r.read()
            res.metadata = {
                "edge_region": res.headers.get("x-sb-edge-region"),
                "proxy_timing_ms": res.headers.get("x-proxy-timing-ms"),
                "proxy_mode": res.headers.get("x-proxy-mode"),
            }
    except urllib.error.HTTPError as e:
        res.status = e.code
        res.headers = _headers_lower(dict(e.headers))
        res.body = e.read()
    except Exception as e:
        res.error = repr(e)
    res.elapsed_ms = int((time.time() - started) * 1000)
    return res

# ---------------------------------------------------------------------------
# Tier 2: Netlify edge scraper (fetch engine inline)
# ---------------------------------------------------------------------------
def backend_netlify(url: str, config, *, engine: str = "fetch", timeout: int = 60, **_) -> FetchResult:
    base = (config.netlify_scraper_url or "").rstrip("/")
    token = config.netlify_token
    started = time.time()
    res = FetchResult(url=url, backend="netlify")
    if not base or not token:
        res.error = "netlify credentials not configured"; return res
    payload = json.dumps({"jobs":[{"url":url,"engine":engine}], "result_mode":"inline"}).encode()
    req = urllib.request.Request(f"{base}/api/scrape", data=payload, method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res.status = r.status
            res.headers = _headers_lower(dict(r.headers))
            j = json.loads(r.read().decode("utf-8","ignore"))
            results = j.get("results") or []
            r0 = results[0] if results else {}
            res.status = r0.get("status") or res.status
            res.body = (r0.get("inline_body") or "").encode("utf-8","ignore")
            res.metadata = {"batch_id": j.get("batch_id"), "engine": r0.get("engine"),
                            "size": r0.get("size"), "content_type": r0.get("content_type")}
    except urllib.error.HTTPError as e:
        res.status = e.code
        res.body = e.read()
        res.error = f"netlify HTTP {e.code}"
    except Exception as e:
        res.error = repr(e)
    res.elapsed_ms = int((time.time() - started) * 1000)
    return res

# ---------------------------------------------------------------------------
# Tier 3: Firecrawl v2
# ---------------------------------------------------------------------------
def backend_firecrawl(url: str, config, *, formats=("markdown",), proxy: str | None = None,
                      wait_for: int | None = None, timeout: int = 60, **_) -> FetchResult:
    key = config.firecrawl_api_key
    started = time.time()
    res = FetchResult(url=url, backend="firecrawl")
    if not key:
        res.error = "firecrawl key not configured"; return res
    body = {"url": url, "formats": list(formats), "onlyMainContent": False, "timeout": timeout*1000}
    if proxy: body["proxy"] = proxy
    if wait_for: body["waitFor"] = wait_for
    req = urllib.request.Request("https://api.firecrawl.dev/v2/scrape",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout+20) as r:
            res.status = r.status
            res.headers = _headers_lower(dict(r.headers))
            j = json.loads(r.read().decode("utf-8","ignore"))
            d = j.get("data", {}) or {}
            # Firecrawl can return HTTP 200 with success=false (scrape timeout, bad proxy, target unreachable).
            # In that case, surface the real target status from metadata so the router can escalate.
            if j.get("success") is False:
                target_status = d.get("metadata", {}).get("statusCode") or 522
                res.status = target_status if isinstance(target_status, int) else 522
                res.error = f"firecrawl success=false (target status={target_status})"
                res.body = b""
            else:
                res.body = (d.get("markdown") or d.get("rawHtml") or "").encode("utf-8","ignore")
                res.metadata = {"success": j.get("success"), "metadata": d.get("metadata", {}),
                                "proxy_used": d.get("metadata",{}).get("proxyUsed")}
    except urllib.error.HTTPError as e:
        res.status = e.code
        res.body = e.read()
        res.error = f"firecrawl HTTP {e.code}"
    except Exception as e:
        res.error = repr(e)
    res.elapsed_ms = int((time.time() - started) * 1000)
    return res

# ---------------------------------------------------------------------------
# Tier 4: Zenrows v1 (mode=auto for adaptive stealth)
# ---------------------------------------------------------------------------
def backend_zenrows(url: str, config, *, response_type: str | None = "markdown", mode: str | None = "auto",
                    js_render: bool = False, wait_ms: int | None = None, proxy_country: str | None = None,
                    timeout: int = 90, **_) -> FetchResult:
    # response_type: "markdown" (default), "plaintext", "pdf", or None (omit param = raw HTML)
    key = config.zenrows_api_key
    started = time.time()
    res = FetchResult(url=url, backend="zenrows")
    if not key:
        res.error = "zenrows key not configured"; return res
    if requests is None:
        res.error = "requests library required for zenrows"; return res
    q = {"apikey": key, "url": url}
    if response_type: q["response_type"] = response_type  # None → omit param → Zenrows returns raw HTML
    if mode: q["mode"] = mode
    if js_render: q["js_render"] = "true"
    if wait_ms: q["wait"] = str(wait_ms)
    if proxy_country: q["proxy_country"] = proxy_country
    try:
        r = requests.get("https://api.zenrows.com/v1/", params=q, timeout=timeout+20,
                         headers={"User-Agent":"agent-fetch-kit/1.0"})
        res.status = r.status_code
        res.headers = _headers_lower(dict(r.headers))
        res.body = r.text.encode("utf-8","ignore")
        res.metadata = {"request_cost": res.headers.get("x-request-cost"),
                        "request_id": res.headers.get("x-request-id"),
                        "final_url": res.headers.get("zr-final-url")}
    except Exception as e:
        res.error = repr(e)
    res.elapsed_ms = int((time.time() - started) * 1000)
    return res

# ---------------------------------------------------------------------------
# Tier 5: GitHub Actions remote compute (Azure IP + curl_cffi + system Chrome)
# ---------------------------------------------------------------------------
def backend_gha(url: str, config, *, mode: str = "impersonate", wait_ms: int = 4000,
                timeout: int = 60, poll_interval: int = 8, max_polls: int | None = None, **_) -> FetchResult:
    """Dispatch a GHA workflow, poll for completion, download artifact, return body.

    Race-fix: records dispatch_time and only accepts runs whose created_at >= dispatch_time - 10s
    (clock-skew slack), filtered by head_branch=main + workflow name. This prevents grabbing
    a previous run on back-to-back dispatches.
    """
    import io, zipfile
    from datetime import datetime, timezone
    token = config.gh_token
    repo = config.gh_repo
    ref = getattr(config, "gh_default_ref", "main")
    workflow_file = getattr(config, "gh_workflow_filename", "scrape.yml")
    started = time.time()
    dispatch_time = started  # for race-fix timestamp comparison
    res = FetchResult(url=url, backend="gha")
    if not token or not repo:
        res.error = "gha credentials not configured (need GH_TOKEN+GH_REPO)"; return res
    api = f"https://api.github.com/repos/{repo}"
    hdrs = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28"}

    def _api(method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{api}/{path}", data=data, method=method,
            headers={**hdrs, "Content-Type":"application/json"} if data else hdrs)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8","ignore")
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8","ignore")
            return e.code, (json.loads(raw) if raw else {})
        except Exception as e:
            return 0, {"error": repr(e)}

    def _parse_created_at_epoch(run: dict) -> float:
        ca = run.get("created_at") or run.get("run_started_at")
        if not ca: return 0.0
        try:
            return datetime.fromisoformat(ca.replace("Z","+00:00")).timestamp()
        except Exception:
            return 0.0

    # 1. dispatch
    st, j = _api("POST", f"actions/workflows/{workflow_file}/dispatches",
                 {"ref":ref, "inputs":{"urls": json.dumps([url]), "mode": mode,
                                           "wait_ms": str(wait_ms), "timeout_s": str(timeout)}})
    if st not in (200, 201, 204):
        res.error = f"dispatch failed: HTTP {st} {json.dumps(j)[:200]}"; return res

    # 2. find the run (filter by created_at >= dispatch_time - 10s + head_branch + workflow name)
    run_id = None
    skew = 10  # seconds
    for _ in range(10):  # ~30s to find the run
        time.sleep(3)
        st, j = _api("GET", "actions/runs?event=workflow_dispatch&per_page=5")
        runs = j.get("workflow_runs") or []
        for r in runs:
            if r.get("head_branch") != ref: continue
            if r.get("name") and r.get("name") != "remote-scrape": continue
            ca_epoch = _parse_created_at_epoch(r)
            if ca_epoch >= dispatch_time - skew:
                run_id = r.get("id")
                break
        if run_id: break
    if not run_id:
        res.error = "could not find dispatched run (race-filter rejected all candidates)"
        res.elapsed_ms = int((time.time() - started) * 1000)
        return res

    # 3. poll for completion — derive budget from timeout (default: ~2x timeout in polls)
    if max_polls is None:
        max_polls = max(2, (timeout * 2) // poll_interval)
    conclusion = None
    for _ in range(max_polls):
        time.sleep(poll_interval)
        st, j = _api("GET", f"actions/runs/{run_id}")
        if j.get("status") == "completed":
            conclusion = j.get("conclusion")
            break
    else:
        res.error = f"run {run_id} timed out after {max_polls * poll_interval}s"
        res.metadata = {"run_id": run_id, "conclusion": "timeout"}
        res.elapsed_ms = int((time.time() - started) * 1000)
        return res

    # 4. download artifact
    st, j = _api("GET", f"actions/runs/{run_id}/artifacts")
    arts = j.get("artifacts") or []
    if not arts:
        res.error = f"no artifacts for run {run_id} (conclusion={conclusion})"
        res.metadata = {"run_id": run_id, "conclusion": conclusion}; return res
    art = arts[0]
    zip_url = art["archive_download_url"]
    # NOTE: GitHub's archive_download_url is on api.github.com and returns a 302
    # redirect to a pre-signed Azure Blob Storage URL. urllib's default redirect
    # handler forwards the Authorization header to the redirected host, which
    # Azure rejects with HTTP 401 (InvalidAuthenticationInfo — it expects an
    # Azure bearer token, not a GitHub PAT). Fix: use _StripAuthOnRedirect
    # (module-level for testability) to strip host-specific headers on redirect.
    opener = urllib.request.build_opener(_StripAuthOnRedirect())
    req = urllib.request.Request(zip_url, headers=hdrs)
    try:
        with opener.open(req, timeout=60) as r:
            zip_bytes = r.read()
        z = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = z.namelist()
        slug = re.sub(r'[^a-z0-9]+','-', url.lower())[:80].strip('-') or 'root'
        body_name = next((n for n in names if n.endswith(f"{slug}.body")), None)
        meta_name = next((n for n in names if n.endswith(f"{slug}.meta.json")), None)
        if body_name:
            res.body = z.read(body_name)
        if meta_name:
            meta = json.loads(z.read(meta_name).decode("utf-8","ignore"))
            # coerce status: gha_fetch may return string "error" or int 0 on failure → normalize
            s = meta.get("status")
            res.status = s if isinstance(s, int) and s > 0 else None
            res.metadata = {"run_id": run_id, "conclusion": conclusion, "gha_meta": meta,
                            "runner_ip_json": _read_zip_member(z, "runner-ip.json") or _read_zip_member(z, "out/runner-ip.json"),
                            "runner_tls_json": _read_zip_member(z, "runner-tls.json") or _read_zip_member(z, "out/runner-tls.json")}
            if meta.get("error"):
                res.error = f"gha_fetch error: {meta.get('error')}"
        else:
            res.metadata = {"run_id": run_id, "conclusion": conclusion}
    except Exception as e:
        res.error = f"artifact download err: {e!r}"
    res.elapsed_ms = int((time.time() - started) * 1000)
    return res

def _read_zip_member(z, name: str) -> str | None:
    try:
        # Try exact name first; if not, try basename match (some zips store
        # at root, some under out/ — the actions/upload-artifact@v4 with
        # path: out/ flattens the dir structure to just the basename)
        if name in z.namelist():
            return z.read(name).decode("utf-8","ignore")
        # Try basename match if name has a slash
        if "/" in name:
            base = name.rsplit("/", 1)[-1]
            for n in z.namelist():
                if n == base or n.endswith("/" + base) or n.endswith("/" + base.split("/")[-1]):
                    return z.read(n).decode("utf-8","ignore")
    except Exception:
        pass
    return None


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """urllib redirect handler that strips host-specific headers on cross-host redirects.

    GitHub's artifact archive_download_url returns a 302 to a pre-signed Azure
    Blob Storage URL. urllib's default behavior forwards the Authorization
    header to the redirected host, which Azure rejects with HTTP 401
    InvalidAuthenticationInfo (it expects an Azure bearer token, not a GitHub
    PAT). This handler removes Authorization + Accept + X-GitHub-Api-Version
    from the redirected request so Azure sees only the pre-signed query string.

    Implementation note: add_unredirected_header() does NOT overwrite an
    existing header — it goes into a separate `unredirected_hdrs` dict that
    gets merged at send-time. header_items() returns both, so callers would
    still see the leaked secret. We must del from `headers` directly.

    Tested in tests/test_review_fixes.py::TestGhaArtifactRedirect.
    """
    _STRIP_HEADERS = ('Authorization', 'Accept', 'X-GitHub-Api-Version', 'Host', 'Content-Length', 'Content-Type')

    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        new_req = super().redirect_request(req, fp, code, msg, hdrs, newurl)
        if new_req is not None:
            for h in self._STRIP_HEADERS:
                # del from both dicts to actually remove
                new_req.headers.pop(h, None)
                # urllib normalizes header keys to capitalized form
                new_req.headers.pop(h.capitalize(), None)
                new_req.headers.pop(h.lower(), None)
                new_req.headers.pop(h.upper(), None)
                new_req.unredirected_hdrs.pop(h, None)
                new_req.unredirected_hdrs.pop(h.capitalize(), None)
                new_req.unredirected_hdrs.pop(h.lower(), None)
                new_req.unredirected_hdrs.pop(h.upper(), None)
        return new_req


# ---------------------------------------------------------------------------
# Tier 6: local stealth browser (patchright + chromium-1228)
# ---------------------------------------------------------------------------
CHROMIUM_PATHS = [
    "/home/z/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome",
    "/home/z/.cache/ms-playwright/chromium-1200/chrome-linux64/chrome",
]
def _find_chromium() -> str | None:
    import os
    for p in CHROMIUM_PATHS:
        if os.path.exists(p): return p
    return None

def backend_browser(url: str, config, *, headless: bool = True, wait_ms: int = 4000,
                    timeout: int = 30, use_patchright: bool = True, **_) -> FetchResult:
    started = time.time()
    res = FetchResult(url=url, backend="browser")
    chrome = _find_chromium()
    if not chrome:
        res.error = "no local chromium found"; return res
    b = None
    try:
        if use_patchright:
            from patchright.sync_api import sync_playwright
        else:
            from playwright.sync_api import sync_playwright
        args = ["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled","--disable-gpu"]
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=headless, executable_path=chrome, args=args)
            ctx = b.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                                locale="en-US", viewport={"width":1920,"height":1080})
            page = ctx.new_page()
            response = page.goto(url, timeout=timeout*1000, wait_until="domcontentloaded")
            page.wait_for_timeout(wait_ms)
            res.body = page.content().encode("utf-8","ignore")
            # use the real HTTP response status (not hardcoded 200) so 4xx/5xx pages escalate
            res.status = response.status if response else None
            res.metadata = {"title": page.title(), "browser": "patchright" if use_patchright else "playwright",
                            "final_url": page.url}
            b.close()
            b = None
    except Exception as e:
        res.error = repr(e)
    finally:
        if b is not None:
            try: b.close()
            except Exception: pass
    res.elapsed_ms = int((time.time() - started) * 1000)
    return res

# Registry for the router
BACKENDS = {
    "local": backend_local,
    "supabase": backend_supabase,
    "netlify": backend_netlify,
    "firecrawl": backend_firecrawl,
    "zenrows": backend_zenrows,
    "gha": backend_gha,
    "browser": backend_browser,
}
