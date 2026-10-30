"""vendor_relay — the S51 vendor-API relay transport.

THE LAW (agent-fetch-kit round-5, owner directive 2026-10-30): firecrawl
and zenrows API calls from a pool of free-tier accounts must NEVER ride
one direct IP — many accounts behind one egress is the textbook
multi-accounting signal. ALL vendor traffic routes through the free
netlify edge-proxy fleet (deploy-scoped URLs, per-call rotation; the
kit's fetchkit-fleet, 64 endpoints / 6 concurrent egress IPs measured
2026-10-30, pool drifts within hours). The supabase 15-region relay was
the original best-in-class carrier but its free project was purged
(NXDOMAIN 2026-10-30) — redeploy on a fresh project when an owner
account appears.

Contract (verified live 2026-10-30 against the fleet):
  POST {base}/api/scrape
       {"jobs": [{"url", "engine": "fetch", "method", "headers", "body"}],
        "result_mode": "inline"}
       Authorization: Bearer <NETLIFY_RELAY_TOKEN>
  → results[0] = {ok, status, size, content_type, error, inline_body,
                  inline_body_truncated, blob_key}
  * headers (incl. upstream Authorization) forwarded verbatim
  * body forwarded (firecrawl POST-JSON verified end-to-end: a real
    /v2/scrape returned markdown through the relay, 1 credit)
  * inline_body caps at 32,768 chars; larger bodies set
    inline_body_truncated + blob_key → fetch via
    {NETLIFY_RELAY_BLOBS_API}/{blob_key} with the admin PAT
  * fake-success guard (the kit's round-3 P0): a wrapper HTTP-200 with
    inner error + status∈{None,0} is a TRANSPORT failure (DNS dead,
    connection refused) — rotate to the next base, never surface it as
    a target answer
  * wrapper 401/403/404 = THIS endpoint is unusable (deleted deploy,
    bad token) — rotate base; it must never poison a vendor-key verdict

Env (all optional; absent = the legacy direct transport, unchanged):
  NETLIFY_RELAY_URLS        comma-separated deploy URLs (the fleet)
  NETLIFY_RELAY_TOKEN       the fleet bearer (netlify PAT)
  NETLIFY_RELAY_BLOBS_API   blobs endpoint for truncated-body retrieval
  NETLIFY_RELAY_ADMIN_TOKEN admin PAT for the blob fetch
  FC_RELAY_MODE             auto (default) | relay | direct
                            auto = relay when URLs configured, else direct
  ZENROWS_KEYS              comma/space pool of zenrows apikeys
                            (ZENROWS_API_KEY joins the pool as a member)

Public API:
  relay_fetch(url, method, headers, body, timeout) -> dict
  fc_api(path, payload, key, timeout) -> (parsed | None, status_str)
  fc_credit_usage(key) -> int | None     (free endpoint, relayed)
  zenrows_keys() -> list[str]
  zenrows_fetch(params, timeout) -> (parsed | None, status_str)
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent  # …/jobsearch/../..

_CUR: dict = {"i": 0}          # per-process rotation cursor (serial loops)
_ENV_CACHE: dict = {"mt": 0.0, "vals": {}}


def _envfile_vals() -> dict:
    """ingest/.env parsed (mtime-cached) — the GHA runtime materializes
    the INGEST_ENV secret there; local runs read the tracked copy."""
    p = _REPO / "ingest/.env"
    try:
        mt = p.stat().st_mtime
    except OSError:
        return {}
    if _ENV_CACHE.get("mt") != mt:
        vals: dict = {}
        for line in p.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                # multi-line list semantics (mirrors _firecrawl_keys):
                # a repeated FIRECRAWL_KEYS= line ACCUMULATES (comma-
                # joined), never overwrites — the S46/S51 batches ship
                # as separate lines
                if k in vals and v:
                    vals[k] = f"{vals[k]},{v}"
                else:
                    vals.setdefault(k, v)
        _ENV_CACHE.update(mt=mt, vals=vals)
    return _ENV_CACHE["vals"]


def _envval(name: str) -> str:
    if name in os.environ:
        return os.environ[name]
    return _envfile_vals().get(name, "")


def _csv(name: str) -> list[str]:
    out: list[str] = []
    for v in re.split(r"[,\s]+", _envval(name) or ""):
        v = v.strip()
        if v and v not in out:
            out.append(v)
    return out


def relay_bases() -> list[str]:
    """The fleet, deduped, order-preserving. [] = relay unconfigured."""
    return _csv("NETLIFY_RELAY_URLS")


def relay_configured() -> bool:
    return bool(relay_bases() and _envval("NETLIFY_RELAY_TOKEN"))


def _fc_mode() -> str:
    m = (_envval("FC_RELAY_MODE") or "auto").strip().lower()
    return m if m in ("auto", "relay", "direct") else "auto"


# ── the transport ───────────────────────────────────────────────────────────

def _blob_fetch(blob_key: str, timeout: float) -> bytes | None:
    api = _envval("NETLIFY_RELAY_BLOBS_API").rstrip("/")
    tok = _envval("NETLIFY_RELAY_ADMIN_TOKEN")
    if not api or not tok or not blob_key:
        return None
    try:
        req = urllib.request.Request(
            f"{api}/{blob_key}",
            headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:                           # noqa: BLE001
        return None


def relay_fetch(url: str, *, method: str = "GET",
                headers: dict | None = None, body: str | None = None,
                timeout: float = 90.0, max_bases: int = 3) -> dict:
    """One vendor-API call through the rotating fleet.

    Returns {"status": int|None, "text": str, "err": str, "base": str,
    "truncated": bool}. TRANSPORT failures (wrapper 4xx, non-JSON
    wrapper, inner fetch-failed, blob unrecoverable) rotate to the next
    base — up to max_bases per call. The TARGET's status is always an
    answer (returned verbatim, never retried here).
    """
    bases = relay_bases()
    token = _envval("NETLIFY_RELAY_TOKEN")
    if not bases or not token:
        return {"status": None, "text": "", "err": "relay-unconfigured",
                "base": "", "truncated": False}
    n = len(bases)
    start = _CUR["i"] % n
    _CUR["i"] = (_CUR["i"] + 1) % n
    last_err = "relay:no-attempt"
    for k in range(min(max_bases, n)):
        base = bases[(start + k) % n].rstrip("/")
        job: dict = {"url": url, "engine": "fetch", "method": method}
        if headers:
            job["headers"] = headers
        if body is not None:
            job["body"] = body
        try:
            req = urllib.request.Request(
                f"{base}/api/scrape",
                data=json.dumps({"jobs": [job],
                                 "result_mode": "inline"}).encode(),
                method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                wrapper = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            # wrapper-level auth/deploy failures: THIS endpoint is dead
            last_err = f"wrapper-http{e.code}"
            continue
        except Exception as e:                  # noqa: BLE001
            last_err = f"wrapper-{type(e).__name__}"
            continue
        results = wrapper.get("results") or []
        if not results:
            last_err = "wrapper:no-results"
            continue
        res = results[0]
        status = res.get("status")
        err = res.get("error")
        # fake-success guard: inner fetch failed (DNS/refused/timeout)
        if err and status in (None, 0, False):
            last_err = f"inner-fetch-failed:{str(err)[:40]}"
            continue
        text = res.get("inline_body") or ""
        truncated = False
        if res.get("inline_body_truncated"):
            blob = _blob_fetch(res.get("blob_key") or "", timeout)
            if blob is not None:
                text = blob.decode("utf-8", "replace")
            else:
                truncated = True     # keep the 32KB prefix + flag it
        return {"status": status, "text": text, "err": "",
                "base": base, "truncated": truncated}
    return {"status": None, "text": "", "err": last_err,
            "base": "", "truncated": False}


# ── firecrawl ───────────────────────────────────────────────────────────────

def _direct_post_json(url: str, payload: dict, key: str,
                      timeout: float) -> tuple[dict | None, str]:
    """The legacy direct path (FC_RELAY_MODE=direct / relay unconfigured)
    — identical semantics to the pre-S51 _fc_scrape core."""
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")), "ok"
    except urllib.error.HTTPError as h:
        return None, f"http{h.code}"
    except Exception as e:                      # noqa: BLE001
        return None, f"error:{type(e).__name__}"


def fc_api(path: str, payload: dict, key: str,
           *, timeout: float = 120.0) -> tuple[dict | None, str]:
    """firecrawl API POST. Relay-first when configured (auto), else the
    legacy direct path. Returns (parsed | None, status_str) — the exact
    contract role_sweep._fc_scrape expects (raise-free; key-level
    401/402/403 rotate keys at the CALLER, target 5xx return verbatim).
    """
    url = f"https://api.firecrawl.dev{path}"
    mode = _fc_mode()
    if mode == "direct" or (mode == "auto" and not relay_configured()):
        return _direct_post_json(url, payload, key, timeout)
    # relay path — STRICT for both 'relay' and 'auto' modes: a dead
    # relay NEVER silently re-exposes the caller's IP (the owner
    # directive; a fail-soft lane verdict is the honest outcome).
    # FC_RELAY_MODE=direct is the explicit emergency escape hatch.
    r = relay_fetch(url, method="POST",
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {key}"},
                    body=json.dumps(payload), timeout=timeout)
    if r["status"] is None:
        return None, f"relay:{r['err'][:40]}"
    if r["status"] >= 400:
        return None, f"http{r['status']}"
    try:
        return json.loads(r["text"]), "ok"
    except (json.JSONDecodeError, ValueError):
        return None, "relay:bad-json"


def fc_credit_usage(key: str, *, timeout: float = 45.0) -> int | None:
    """Remaining credits for one key — the FREE usage endpoint, always
    relayed when configured (balance checks ride the relay too: the
    kit's rule #3). None = unreachable (best-effort per key)."""
    url = "https://api.firecrawl.dev/v1/team/credit-usage"
    if relay_configured() and _fc_mode() != "direct":
        r = relay_fetch(url, method="GET",
                        headers={"Authorization": f"Bearer {key}"},
                        timeout=timeout)
        if r["status"] != 200:
            return None
        try:
            return int(((json.loads(r["text"]).get("data") or {})
                        .get("remaining_credits")))
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
    try:
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read().decode("utf-8", "replace"))
            return int((d.get("data") or {}).get("remaining_credits"))
    except Exception:                           # noqa: BLE001
        return None


# ── zenrows ─────────────────────────────────────────────────────────────────

def zenrows_keys() -> list[str]:
    """ZENROWS_KEYS pool (comma/space) + ZENROWS_API_KEY (legacy single,
    joins as a member not an override). Deduped, order-preserving."""
    keys = _csv("ZENROWS_KEYS")
    single = (_envval("ZENROWS_API_KEY") or "").strip()
    if single and single not in keys:
        keys.append(single)
    return keys


_ZR_STATE: dict = {"idx": 0, "dead": set()}


def zenrows_fetch(params: dict, *, timeout: float = 120.0,
                  max_keys: int = 3) -> tuple[dict | None, str]:
    """GET api.zenrows.com/v1/ with apikey rotated across the pool —
    ALWAYS through the relay when configured (the apikey rides the
    query string; a relayed GET carries it identically). Params must
    NOT include 'apikey' (added here). Returns (parsed | None, status):
      parsed = the zenrows JSON body on HTTP 200
      status = "ok" | "http<code>" | "key-exhausted" | "relay:…" |
               "no-key" | "error:<Exc>"
    Key-level auth/quota failures (401/402/403, AUTH004) rotate keys;
    target-level 4xx/5xx return verbatim (an answer, not a retry)."""
    keys = [k for k in zenrows_keys() if k not in _ZR_STATE["dead"]]
    if not keys:
        return None, "no-key"
    last = ""
    for attempt in range(min(max_keys, len(keys))):
        key = keys[(_ZR_STATE["idx"] + attempt) % len(keys)]
        p = dict(params)
        p["apikey"] = key
        if relay_configured() and _fc_mode() != "direct":
            qs = urllib.parse.urlencode(p)
            r = relay_fetch(f"https://api.zenrows.com/v1/?{qs}",
                            method="GET", timeout=timeout)
            if r["status"] is None:
                return None, f"relay:{r['err'][:40]}"
            status, text = r["status"], r["text"]
        else:
            try:
                req = urllib.request.Request(
                    "https://api.zenrows.com/v1/?" + urllib.parse.urlencode(p))
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    status, text = resp.status, resp.read().decode(
                        "utf-8", "replace")
            except urllib.error.HTTPError as h:
                status, text = h.code, h.read().decode("utf-8", "replace")
            except Exception as e:              # noqa: BLE001
                last = f"error:{type(e).__name__}"
                continue
        if status in (401, 402, 403) or "AUTH004" in text[:400]:
            _ZR_STATE["dead"].add(key)          # key-level: rotate
            last = "key-exhausted"
            continue
        if status != 200:
            return None, f"http{status}"        # target answer
        try:
            _ZR_STATE["idx"] = (_ZR_STATE["idx"] + 1) % len(keys)
            return json.loads(text), "ok"
        except (json.JSONDecodeError, ValueError):
            return None, "error:bad-json"
    return None, last or "key-exhausted"


def zenrows_fetch_html(params: dict, *, timeout: float = 120.0,
                       max_keys: int = 3) -> tuple[str, str]:
    """zenrows_fetch for HTML/markdown bodies (glassdoor JSON-LD class):
    returns (body_text, status) with the same key-rotation semantics."""
    keys = [k for k in zenrows_keys() if k not in _ZR_STATE["dead"]]
    if not keys:
        return "", "no-key"
    last = ""
    for attempt in range(min(max_keys, len(keys))):
        key = keys[(_ZR_STATE["idx"] + attempt) % len(keys)]
        p = dict(params)
        p["apikey"] = key
        if relay_configured() and _fc_mode() != "direct":
            qs = urllib.parse.urlencode(p)
            r = relay_fetch(f"https://api.zenrows.com/v1/?{qs}",
                            method="GET", timeout=timeout)
            if r["status"] is None:
                return "", f"relay:{r['err'][:40]}"
            status, text = r["status"], r["text"]
            if r["truncated"]:
                # zenrows HTML pages exceed 32KB often — the blob path
                # already recovered it inside relay_fetch when possible;
                # truncated=True here means the prefix is all we got
                last = "relay:truncated"
        else:
            try:
                req = urllib.request.Request(
                    "https://api.zenrows.com/v1/?" + urllib.parse.urlencode(p))
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    status, text = resp.status, resp.read().decode(
                        "utf-8", "replace")
            except urllib.error.HTTPError as h:
                status, text = h.code, h.read().decode("utf-8", "replace")
            except Exception as e:              # noqa: BLE001
                last = f"error:{type(e).__name__}"
                continue
        if status in (401, 402, 403) or "AUTH004" in text[:400]:
            _ZR_STATE["dead"].add(key)
            last = "key-exhausted"
            continue
        if status != 200:
            return text, f"http{status}"
        if text:
            _ZR_STATE["idx"] = (_ZR_STATE["idx"] + 1) % len(keys)
            return text, "ok"
        last = "error:empty-body"
    return "", last or "key-exhausted"

