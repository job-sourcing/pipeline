"""Core router — the escalation ladder. Tries backends in order, escalates on challenge."""
from __future__ import annotations
import sys, time
from .config import Config
from .detect import is_challenged
from .history import History
from .backends import FetchResult, BACKENDS

# Escalation ladders per scenario (ordered)
_PLAIN_LADDER = ["local", "supabase", "netlify", "firecrawl", "zenrows", "gha"]
_RENDER_LADDER = ["browser", "firecrawl", "zenrows", "gha"]   # JS-rendered SPAs (no hard WAF)
_ANTIBOT_LADDER = ["zenrows", "firecrawl", "gha"]              # hard WAF — go straight to the big guns

# Terminal statuses — DON'T escalate (the URL genuinely doesn't exist, or it's a real client error
# that no backend will fix). Escalating on these would burn Firecrawl/Zenrows credits for nothing.
# 403/429/503/504 are NOT here — those are challenge/block signals that DO warrant escalation.
TERMINAL_STATUSES = {400, 401, 402, 404, 405, 410, 418, 422, 451}

def _backend_kwargs(backend: str, url: str, config: Config, opts: dict) -> dict:
    """Build the kwargs dict for a given backend from the user's options."""
    region = opts.get("region")
    extract = opts.get("extract")
    timeout = opts.get("timeout", 30)
    impersonate = opts.get("impersonate")
    out: dict = {"timeout": timeout}
    if backend == "local":
        if impersonate: out["impersonate"] = impersonate
    elif backend == "supabase":
        out["mode"] = opts.get("supabase_mode", "raw")
        if region: out["region"] = region
        if extract: out["extract"] = extract
    elif backend == "netlify":
        out["engine"] = "fetch"  # only fetch works inline on this deploy
    elif backend == "firecrawl":
        if opts.get("render") or opts.get("antibot"):
            out["wait_for"] = opts.get("wait_ms", 4000)
        if opts.get("antibot"):
            out["proxy"] = "stealth"
        if opts.get("markdown") is False:
            out["formats"] = ("rawHtml",)
    elif backend == "zenrows":
        # mode=auto handles js_render + premium_proxy adaptively for hard targets
        if opts.get("antibot"):
            out["mode"] = "auto"
            if region: out["proxy_country"] = region
        elif opts.get("render"):
            out["js_render"] = True
            if opts.get("wait_ms"): out["wait_ms"] = opts["wait_ms"]
            if region: out["proxy_country"] = region
        else:
            out["mode"] = None  # basic markdown
        # markdown=False → response_type=None (raw HTML); markdown=True → response_type="markdown"
        out["response_type"] = "markdown" if opts.get("markdown", True) else None
    elif backend == "gha":
        if opts.get("render") or opts.get("antibot"):
            out["mode"] = "browser"
            out["wait_ms"] = opts.get("wait_ms", 5000)
        else:
            out["mode"] = "impersonate"
        out["timeout"] = max(timeout, 60)
    elif backend == "browser":
        out["wait_ms"] = opts.get("wait_ms", 4000)
        out["use_patchright"] = True
    return out

def fetch(url: str, *, mode: str = "auto", render: bool = False, antibot: bool = False,
          region: str | None = None, extract: str | None = None, markdown: bool = True,
          timeout: int = 30, impersonate: str | None = None, wait_ms: int = 4000,
          config: Config | None = None, history: History | None = None,
          verbose: bool = False) -> FetchResult:
    """Universal fetch with auto-escalation. Returns a FetchResult with .attempts filled.

    Escalation logic:
      - r.ok (200, no challenge, no error) → return immediately
      - terminal status (404/410/422/etc.) → return that result (don't burn credits escalating)
      - challenge (403/429/503/504 or body markers) → escalate to next backend
      - last attempt → return it (even if failed)
    """
    config = config or Config()
    history = history or History(config.history_path)
    opts = {"region":region, "extract":extract, "markdown":markdown, "timeout":timeout,
            "impersonate":impersonate, "render":render, "antibot":antibot, "wait_ms":wait_ms}

    # Decide ladder (note: --antibot takes precedence over --render)
    if mode != "auto":
        ladder = [mode]
    elif antibot:
        ladder = list(_ANTIBOT_LADDER)
    elif render:
        ladder = list(_RENDER_LADDER)
    else:
        ladder = list(_PLAIN_LADDER)

    # Prepend domain-memory preferred backend (if it's in the ladder)
    preferred = history.preferred_backend(url)
    if preferred and preferred in ladder:
        ladder.remove(preferred)
        ladder.insert(0, preferred)

    # Filter to configured backends
    ladder = [b for b in ladder if config.has(b)]
    if not ladder:
        r = FetchResult(url=url, backend="none", error="no backends configured for this ladder")
        r.attempts = []
        return r

    attempts = []
    final: FetchResult | None = None
    for i, backend_name in enumerate(ladder):
        fn = BACKENDS[backend_name]
        kwargs = _backend_kwargs(backend_name, url, config, opts)
        if verbose:
            print(f"[wfetch] try {i+1}/{len(ladder)}: {backend_name} {kwargs}", file=sys.stderr)
        r = fn(url, config, **kwargs)
        # challenge detection (only for non-200 or marker-containing bodies)
        chal, reason = is_challenged(r.status, r.body)
        r.challenged = chal
        r.challenge_reason = reason
        attempts.append({"backend": backend_name, "status": r.status, "elapsed_ms": r.elapsed_ms,
                         "challenged": chal, "reason": reason, "error": r.error})
        r.attempts = list(attempts)
        history.remember(url, backend_name, r.ok, r.elapsed_ms)
        if r.ok:
            final = r
            break
        # terminal statuses (404/410/422/etc.) — don't escalate, the URL genuinely doesn't exist
        if isinstance(r.status, int) and r.status in TERMINAL_STATUSES and not chal:
            final = r
            break
        # if this was the last attempt, keep it as final (even if failed)
        if i == len(ladder) - 1:
            final = r
    return final or FetchResult(url=url, backend="none", error="no attempts completed")

def probe(config: Config | None = None) -> dict:
    """Health/probe: local egress IP + each backend's basic reachability.

    Uses CHEAP modes to avoid burning credits: zenrows basic markdown (0.001 credits, not mode=auto
    which is 0.025), firecrawl basic (1 credit — unavoidable). Includes unconfigured backends with
    the missing-creds reason so the agent can see which env vars to source.
    """
    config = config or Config()
    out: dict = {"backends": {}}
    # local egress
    try:
        from curl_cffi import requests as creq
        r = creq.get("https://ipinfo.io/json", impersonate="chrome131", timeout=15)
        out["local_egress"] = r.json()
    except Exception as e:
        out["local_egress"] = {"error": repr(e)}
    # each backend — include unconfigured ones with the reason
    for name, fn in BACKENDS.items():
        if not config.has(name):
            out["backends"][name] = {"configured": False,
                                      "reason": config.missing_creds_reason(name) or "unknown"}
            continue
        try:
            if name == "gha":
                out["backends"][name] = {"configured": True, "note": "skipped (dispatch-only; use 'wfetch gha URL' to run)"}
                continue
            if name == "zenrows":
                # cheap mode: basic markdown (0.001 credits), NOT mode=auto (0.025)
                r = fn("https://example.com", config, mode=None, response_type="markdown", timeout=20)
            elif name == "firecrawl":
                # basic (1 credit) — unavoidable for firecrawl
                r = fn("https://example.com", config, formats=("markdown",), timeout=20)
            else:
                r = fn("https://ipinfo.io/json", config, timeout=20)
            chal, reason = is_challenged(r.status, r.body)
            out["backends"][name] = {"configured": True, "status": r.status, "elapsed_ms": r.elapsed_ms,
                                      "error": r.error, "challenged": chal, "challenge_reason": reason}
        except Exception as e:
            out["backends"][name] = {"configured": True, "error": repr(e)}
    return out
