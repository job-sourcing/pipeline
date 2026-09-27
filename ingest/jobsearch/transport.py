"""HTTP transports for IP/geo-blocked sources.

Three complementary transports:

1. **ZenRows premium proxy** (residential IPs, real browsers) — handles
   both geo-blocks (Akamai on USAJobs) and Cloudflare challenges
   (ZipRecruiter/Glassdoor HTML boards). Costs credits per request.

2. **Netlify edge scraper** (US egress via Netlify Functions on AWS) —
   a zero-credit US-egress fallback when ZenRows credits run out.
   Verified live 2026-08-29: USAJobs direct → 403 from HK; via the
   Netlify fetch engine with forwarded headers → 200 with real results.

Some sources are fine contract-wise but block our egress region at the WAF
level (Akamai geo-blocks Hong Kong datacenter IPs — USAJobs; Cloudflare
challenges — ZipRecruiter/Glassdoor). Both transports forward custom
headers, which lifts the block without touching the source's API contract.

Verified live 2026-08-27:
- USAJobs  direct → 403 Akamai Access Denied (HK egress)
          ZenRows premium_proxy+proxy_country=us+custom_headers → 200, real
          results (Python Developer @ SSA) with the stored key.
Verified live 2026-08-29:
- USAJobs via Netlify fetch engine + forwarded Authorization-Key → 200
  (same request shape that the Akamai edge 403'd from HK).

Cost note: premium proxy burns ZenRows credits per request (25 credits at
the time of writing). Use as a FALLBACK transport, not the default path.
The Netlify scraper is effectively free (site quota), so the USAJobs
adapter prefers it when both are configured.
"""
from __future__ import annotations

import json as _json
import urllib.parse
from typing import Optional

import requests

from .config import Config

ZENROWS_API = "https://api.zenrows.com/v1/"

# Netlify Blob store backing the edge scraper (non-HTML bodies land here).
_NETLIFY_BLOB_API = ("https://api.netlify.com/api/v1/blobs/"
                     "01c2e47f-3ff6-4e09-b45f-604c49ef90fe/"
                     "site:scraper-results")


def zenrows_fetch_json(
    url: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    cfg: Optional[Config] = None,
    proxy_country: str = "us",
    js_render: bool = False,
    method: str = "GET",
    json_body: Optional[dict] = None,
    timeout: Optional[int] = None,
) -> dict | list:
    """GET/POST a JSON API through ZenRows' premium residential proxy.

    Mirrors sources/base.fetch_json's signature subset so adapters can swap
    transports with minimal diff. Raises requests.HTTPError on failure, and
    RuntimeError when ZenRows isn't configured.
    """
    cfg = cfg or Config()
    if not cfg.zenrows_api_key:
        raise RuntimeError(
            "ZenRows transport requested but ZENROWS_API_KEY is not set."
        )

    full_url = url
    if params:
        sep = "&" if "?" in url else "?"
        full_url = f"{url}{sep}{urllib.parse.urlencode(params)}"

    zenrows_params: dict = {
        "apikey": cfg.zenrows_api_key,
        "url": full_url,
        "premium_proxy": "true",
        "proxy_country": proxy_country,
        "custom_headers": "true",
    }
    if js_render:
        zenrows_params["js_render"] = "true"

    r = requests.request(
        method,
        ZENROWS_API,
        params=zenrows_params,
        headers=headers or {"User-Agent": "jobsearch/1.0"},
        json=json_body,
        timeout=timeout or max(cfg.http_timeout_s, 60),
    )
    if r.status_code >= 400:
        # Surface the ZenRows error body (RESP001-style JSON) so callers can
        # distinguish upstream-failure (422) from auth/credit errors (401/402).
        try:
            detail = r.json().get("title", "")
        except ValueError:
            detail = r.text[:120]
        raise RuntimeError(
            f"ZenRows error HTTP {r.status_code}: {detail}")
    try:
        return r.json()
    except ValueError as exc:
        raise RuntimeError(
            f"ZenRows returned non-JSON content (status {r.status_code}, "
            f"first 120 chars: {r.text[:120]!r})"
        ) from exc


def zenrows_fetch_text(
    url: str,
    *,
    headers: Optional[dict] = None,
    cfg: Optional[Config] = None,
    proxy_country: str = "us",
    wait_ms: int = 4000,
    timeout: Optional[int] = None,
) -> str:
    """Fetch a rendered HTML page through ZenRows (js_render + premium proxy).

    Used by the Cloudflare-walled HTML boards (ZipRecruiter, Glassdoor).
    The response is ZenRows' rendered HTML of the target page.

    Mirrors scrape_ziprecruiter_zenrows / scrape_glassdoor_zenrows from
    scripts/job_sourcer.py (validated 2026-08-26: ZipRecruiter 5/5
    successes; Glassdoor 200 + JSON-LD ItemList — partial, sometimes a
    challenge page).
    """
    cfg = cfg or Config()
    if not cfg.zenrows_api_key:
        raise RuntimeError(
            "ZenRows transport requested but ZENROWS_API_KEY is not set."
        )

    r = requests.get(
        ZENROWS_API,
        params={
            "apikey": cfg.zenrows_api_key,
            "url": url,
            "js_render": "true",
            "premium_proxy": "true",
            "proxy_country": proxy_country,
            "original_status": "true",
            "wait": str(wait_ms),
            "custom_headers": "true" if headers else "false",
        },
        headers=headers or {"User-Agent": "jobsearch/1.0"},
        timeout=timeout or max(cfg.http_timeout_s, 120),
    )
    if r.status_code >= 400:
        try:
            detail = r.json().get("title", "")
        except ValueError:
            detail = r.text[:120]
        raise RuntimeError(
            f"ZenRows error HTTP {r.status_code}: {detail}")
    return r.text


def supabase_fetch_json(
    url: str,
    *,
    params: Optional[dict] = None,
    cfg: Optional[Config] = None,
    region: Optional[str] = None,
    timeout: Optional[int] = None,
) -> dict | list:
    """GET a JSON API through the Supabase edge proxy (agent-fetch-kit tier 1).

    A free, rotating-IP proxy on Supabase Edge Functions (15 AWS regions,
    random JA3 per request, target status code passed through). Comes from
    the tools/agent-fetch-kit integration (2026-09-08); the kit's
    evaluation measured ~1s latency, 5/5 distinct IPs across calls, and
    region pinning via the `x-region` header (us-east-1 → Ashburn US
    egress).

    CONTRACT LIMITS (measured, see tools/agent-fetch-kit/docs/results/
    supabase.md): GET only (POST → 405) and NO custom-header forwarding —
    so it cannot serve APIs that require forwarded Authorization headers
    (USAJobs stays on the Netlify transport). Use it as a resilience
    fallback for plain-GET JSON boards when our egress is rate-limited or
    geo-blocked: chain direct → supabase → netlify → zenrows.

    Mirrors netlify_fetch_json's exception contract: RuntimeError when the
    transport is not configured or fails, requests.HTTPError when the
    TARGET API returns >= 400 (the proxy passes target statuses through).
    """
    cfg = cfg or Config()
    if not cfg.supabase_proxy_url or not cfg.supabase_proxy_token:
        raise RuntimeError(
            "Supabase proxy transport requested but SUPABASE_PROXY_URL/"
            "SUPABASE_PROXY_TOKEN are not set."
        )

    full_url = url
    if params:
        sep = "&" if "?" in url else "?"
        full_url = f"{url}{sep}{urllib.parse.urlencode(params)}"

    query = urllib.parse.urlencode({"url": full_url, "mode": "raw"})
    headers = {"Authorization": f"Bearer {cfg.supabase_proxy_token}"}
    if region:
        headers["x-region"] = region
    r = requests.get(
        f"{cfg.supabase_proxy_url}?{query}",
        headers=headers,
        timeout=timeout or max(cfg.http_timeout_s, 60),
    )
    if r.status_code >= 400:
        raise RuntimeError(
            f"Supabase proxy transport error HTTP {r.status_code}: "
            f"{r.text[:120]}")
    try:
        return r.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Supabase proxy transport returned non-JSON content (HTTP "
            f"{r.status_code}, first 120 chars: {r.text[:120]!r})"
        ) from exc


def netlify_fetch_json(
    url: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    cfg: Optional[Config] = None,
    method: str = "GET",
    timeout: Optional[int] = None,
) -> dict | list:
    """Fetch a JSON API through the Netlify edge scraper (US egress).

    The scraper is a Netlify Function (AWS us-east): requests exit from
    the US, which lifts Akamai geo-blocks on data.usajobs.gov (verified
    live 2026-08-29 — the same request that 403s from the HK container
    returns 200 through the function). Custom headers are forwarded
    verbatim (Authorization-Key, User-Agent). Non-HTML bodies (JSON) are
    stored in the site's blob store and fetched transparently.

    Mirrors zenrows_fetch_json's signature subset so adapters can swap
    transports with a one-line change. Raises RuntimeError when the
    transport isn't configured or the target returns non-JSON, and
    requests.HTTPError when the target API errors.
    """
    cfg = cfg or Config()
    if not cfg.netlify_scraper_token:
        raise RuntimeError(
            "Netlify transport requested but NETLIFY_SCRAPER_TOKEN is "
            "not set."
        )

    full_url = url
    if params:
        sep = "&" if "?" in url else "?"
        full_url = f"{url}{sep}{urllib.parse.urlencode(params)}"

    base = (cfg.netlify_scraper_url or "").rstrip("/")
    job: dict = {"url": full_url, "engine": "fetch",
                 "result_mode": "inline", "method": method}
    if headers:
        job["headers"] = headers
    r = requests.post(
        f"{base}/api/scrape",
        json={"jobs": [job]},
        headers={"Authorization": f"Bearer {cfg.netlify_scraper_token}"},
        timeout=timeout or max(cfg.http_timeout_s, 60),
    )
    if r.status_code >= 400:
        # "US-proxy" in the message keeps this a transient/skippable
        # failure in the live tests (site outage ≠ adapter bug).
        raise RuntimeError(
            f"Netlify US-proxy transport error HTTP {r.status_code}: "
            f"{r.text[:120]}")
    try:
        out = r.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Netlify US-proxy transport returned non-JSON (HTTP "
            f"{r.status_code}): {r.text[:120]!r}") from exc
    if not out.get("results"):
        raise RuntimeError(
            f"Netlify US-proxy transport error: "
            f"{out.get('error') or 'no results'}")
    res = out["results"][0]

    # Check the target's status BEFORE following the blob (an error
    # response's body is diagnostics, not payload — no blob request).
    status = res.get("status", 0)
    if status >= 400:
        # Mirror fetch_json's contract: HTTPError for upstream API errors
        # (the caller's except-clauses key off the status code).
        resp = requests.Response()
        resp.status_code = status
        raise requests.HTTPError(
            f"Netlify transport: target returned HTTP {status}",
            response=resp)

    # Inline for HTML, blob for JSON — follow the blob transparently.
    text = res.get("inline_body")
    if text is None and res.get("blob_key"):
        b = requests.get(
            f"{_NETLIFY_BLOB_API}/{res['blob_key']}",
            headers={"Authorization": f"Bearer {cfg.netlify_scraper_token}"},
            timeout=timeout or max(cfg.http_timeout_s, 30),
        )
        if b.status_code >= 400:
            raise RuntimeError(
                f"Netlify US-proxy blob fetch error HTTP {b.status_code}")
        text = b.text

    try:
        return _json.loads(text or "")
    except ValueError as exc:
        raise RuntimeError(
            f"Netlify US-proxy transport returned non-JSON content (status "
            f"{status}, first 120 chars: {(text or '')[:120]!r})"
        ) from exc
