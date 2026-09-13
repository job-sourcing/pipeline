"""Network ops primitives (ported from T2 career-ops-research
`providers/_http.mjs` + `_dns-cache.mjs` — Step D 2026-08-27).

Three pieces:

1. ``parse_retry_after_ms`` — Retry-After header in either permitted form
   (delta-seconds or HTTP-date) → milliseconds, clamped to
   ``max_delay_ms * 4`` (T2's maxDelayMs*4 clamp rule: honour the header,
   but never let a hostile/broken server pin the pipeline for hours).

2. ``backoff_delay_ms`` — exponential backoff with jitter so concurrent
   retries don't re-collide in lockstep (T2 JITTER_MS=250).

3. ``install_dns_cache`` — in-process DNS memoization with in-flight
   coalescing + resolver pacing, by patching ``socket.getaddrinfo``. The
   load-bearing part is coalescing: requests' connection pool resolves the
   hostname once per NEW connection, so a parallel burst of N requests
   fires N lookups (T2 measured 29 lookups for 30 parallel requests to one
   host; a full sweep hit ~37k lookups for a single hostname and tripped
   the resolver's per-client rate limit). Failed resolutions are never
   cached, so an outage cannot be pinned in.

Env knobs (read at install time):
    JOBSEARCH_NO_DNS_CACHE=1     opt out entirely (no cache, no pacing)
    JOBSEARCH_DNS_LOOKUPS_PER_MIN  resolver-bound lookup cap (default 400;
                                   0 disables pacing but keeps the cache)
"""
from __future__ import annotations

import socket
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Optional

DEFAULT_MAX_DELAY_MS = 8_000
JITTER_MS = 250
DEFAULT_DNS_LOOKUPS_PER_MIN = 400
_DNS_TTL_S = 300.0                    # T2: 5-minute TTL


def parse_retry_after_ms(value: Optional[str],
                         max_delay_ms: int = DEFAULT_MAX_DELAY_MS) -> Optional[int]:
    """Retry-After header → clamped milliseconds.

    Accepts delta-seconds ('120') or an HTTP-date ('Wed, 21 Oct 2026 07:28:00 GMT').
    Returns None when absent/unparseable. Clamped to max_delay_ms * 4 — a
    server that says 'Retry-After: 86400' must not freeze the pipeline for
    a day (T2 clamp rule).
    """
    if not value:
        return None
    value = value.strip()
    clamp = max_delay_ms * 4

    # Form 1: delta-seconds
    try:
        secs = float(value)
        if secs >= 0:
            return int(min(secs * 1000, clamp))
    except ValueError:
        pass

    # Form 2: HTTP-date
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            from datetime import timezone
            when = when.replace(tzinfo=timezone.utc)
        delta_ms = (when.timestamp() - time.time()) * 1000
        if delta_ms < 0:
            delta_ms = 0
        return int(min(delta_ms, clamp))
    except (TypeError, ValueError, OverflowError):
        return None


def backoff_delay_ms(attempt: int, base_delay_ms: int = 500,
                     max_delay_ms: int = DEFAULT_MAX_DELAY_MS) -> int:
    """Exponential backoff with jitter: base * 2^attempt ± jitter, capped."""
    delay = min(base_delay_ms * (2 ** max(0, attempt)), max_delay_ms)
    import random
    return int(delay + random.uniform(0, JITTER_MS))


class DnsCache:
    """TTL cache + in-flight coalescing + token-bucket pacing for getaddrinfo."""

    def __init__(self, ttl_s: float = _DNS_TTL_S,
                 lookups_per_min: int = DEFAULT_DNS_LOOKUPS_PER_MIN):
        self._ttl = ttl_s
        self._rate = lookups_per_min
        self._cache: dict[tuple, tuple[float, tuple]] = {}
        self._inflight: dict[tuple, threading.Event] = {}
        self._lock = threading.Lock()
        self._original = None
        self._minute_window_start = 0.0
        self._minute_count = 0

    # ── pacing ────────────────────────────────────────────────────────
    def _pace(self) -> None:
        """Token-bucket: cap resolver-bound lookups per rolling minute.

        Decision is computed under the lock; the (potentially long) sleep
        happens outside it so coalescing waiters aren't blocked.
        """
        if self._rate <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if now - self._minute_window_start >= 60.0:
                self._minute_window_start = now
                self._minute_count = 0
            self._minute_count += 1
            sleep_for = 0.0
            if self._minute_count > self._rate:
                sleep_for = max(0.0, 60.0 - (now - self._minute_window_start))
        if sleep_for > 0:
            time.sleep(sleep_for)

    # ── patched lookup ────────────────────────────────────────────────
    def _lookup(self, host, port, family=0, type=0, proto=0,
                flags=0, *args, **kwargs):
        key = (host, port, family, type, proto, flags)
        my_event = None
        while True:
            # Fast path: cached answer (return a copy — callers must not be
            # able to mutate the shared cache entry).
            with self._lock:
                hit = self._cache.get(key)
                if hit and time.monotonic() - hit[0] < self._ttl:
                    return list(hit[1])
                # Coalesce: if another thread is resolving this key, wait.
                event = self._inflight.get(key)
                if event is None:
                    # WE become the resolver — register our own event.
                    my_event = threading.Event()
                    self._inflight[key] = my_event
                    break
            event.wait(timeout=30.0)
            # After the wait, loop: re-check the cache (the resolver may
            # have succeeded) or re-register if it failed. Looping — instead
            # of falling straight through to resolve — prevents N waiters
            # of a FAILED resolution from each hitting the resolver (the
            # resolver-rate-limit amplification this cache exists to stop).

        self._pace()
        try:
            result = self._original(host, port, family, type, proto,
                                    flags, *args, **kwargs)
            # Cache the answer BEFORE waking waiters, so a woken waiter's
            # cache re-check always finds the entry (no double-resolve).
            with self._lock:
                self._cache[key] = (time.monotonic(), list(result))
        finally:
            with self._lock:
                # Pop ONLY the event this call created — a waiter that
                # re-registered after a failure owns the new one.
                if self._inflight.get(key) is my_event:
                    del self._inflight[key]
                my_event.set()
        # Never cache failures — getaddrinfo raises on failure, so the
        # caching above only runs on success.
        return result

    # ── install/uninstall ─────────────────────────────────────────────
    def install(self) -> bool:
        """Patch socket.getaddrinfo. Returns True when newly installed."""
        import os
        with self._lock:
            if self._original is not None:
                return False               # already installed
            if os.environ.get("JOBSEARCH_NO_DNS_CACHE") == "1":
                return False
            rate = os.environ.get("JOBSEARCH_DNS_LOOKUPS_PER_MIN")
            if rate is not None and rate.isdigit():
                self._rate = int(rate)
            self._original = socket.getaddrinfo
            socket.getaddrinfo = self._lookup
            return True

    def uninstall(self) -> None:
        if self._original is not None:
            socket.getaddrinfo = self._original
            self._original = None

    def stats(self) -> dict:
        with self._lock:
            return {"cached_entries": len(self._cache),
                    "inflight": len(self._inflight),
                    "lookups_last_minute": self._minute_count,
                    "rate_cap_per_min": self._rate}


_default_cache: Optional[DnsCache] = None


def install_dns_cache() -> bool:
    """Install the process-wide DNS cache (idempotent)."""
    global _default_cache
    if _default_cache is None:
        _default_cache = DnsCache()
    return _default_cache.install()
