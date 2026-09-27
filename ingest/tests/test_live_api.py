"""Live API tests — hit real job boards. Deselected by default
(pyproject addopts "-m 'not live'"); run explicitly with -m live.
NOT run as part of the unit suite.

Live test policy (PR_REVIEW_TESTS_v2 P0-1, P0-2 fix; CodeRabbit round-2
hardening):
- A live test that returns 0 jobs is a FAILURE, not a pass. The fetch
  succeeded but produced nothing — that's a real signal worth surfacing
  (rate-limit-then-empty, transient outage, shape drift).
- A live test whose fetch fails TRANSIENTLY (geo-block, proxy outage,
  timeout, connection reset, rate limit) skips cleanly via pytest.skip —
  environmental issues must not mask real code bugs.
- A live test whose fetch fails NON-transiently (auth rejected, response
  shape drift, parse error) FAILS via _skip_if_transient — a broken
  adapter must not be able to hide behind pytest.skip.
- A live aggregator test must assert at least ONE source returned jobs —
  "all sources degraded" is NOT the same as "works".
"""
from __future__ import annotations

import pytest

from jobsearch.config import load_config
from jobsearch.models import SourceResult
from jobsearch.sources import DEFAULT_SOURCES, search_all_sources
from jobsearch.sources import remotive

# Transient/environmental failure markers — these justify a skip. Anything
# else (auth, parsing, contract drift) fails the test instead.
_TRANSIENT_MARKERS = (
    "unreachable", "geo", "proxy", "403", "429", "rate",
    "timeout", "timed out", "connection", "no internet", "temporarily",
)


def _skip_if_transient(exc: Exception, what: str) -> None:
    """Skip only when the failure looks environmental; otherwise re-raise
    as a test FAILURE (CodeRabbit round-2: pytest.skip must not swallow
    auth/parse/contract errors)."""
    msg = str(exc).lower()
    if any(marker in msg for marker in _TRANSIENT_MARKERS):
        pytest.skip(f"{what} live API unreachable: {exc}")
    raise AssertionError(
        f"{what} live contract failure (not transient): {exc}") from exc


@pytest.mark.live
def test_live_remotive_fetch():
    """Live fetch from Remotive. P0-1 fix: a 0-job result is a FAILURE."""
    try:
        jobs = remotive.fetch("python", num_results=5, cfg=load_config())
    except RuntimeError as exc:
        _skip_if_transient(exc, "Remotive")
    assert isinstance(jobs, list)
    assert len(jobs) > 0, (
        "Remotive returned 0 jobs — likely transient outage or shape drift. "
        "Investigate before merging changes to remotive.py."
    )
    for j in jobs:
        assert j.source == "Remotive"
        assert j.title and j.company
        assert j.remote is True


@pytest.mark.live
def test_live_search_all_sources_degrades_gracefully():
    """Live aggregator: at least ONE source must produce jobs (P0-2 fix).

    The previous shape asserted `if r.ok: ...` per-source but never asserted
    ANY source was ok — so a fully-broken aggregator (every source degraded)
    would pass. The new assertion closes that loophole.
    """
    # Explicit source list so a future DEFAULT_SOURCES reordering doesn't
    # silently change which sources this test exercises.
    sources = ["Remotive", "Arbeitnow"]
    results = search_all_sources("python", "Remote", sources, 5, load_config())
    assert isinstance(results, list)
    assert len(results) == len(sources)
    for r in results:
        assert isinstance(r, SourceResult)
        if r.ok:
            assert all(j.title and j.company for j in r.jobs)
    # P0-2 fix: at least one source must produce jobs — a fully degraded
    # aggregator is NOT "graceful degradation", it's a broken live path.
    ok_sources = [r.source for r in results if r.ok]
    assert ok_sources, (
        f"All {len(sources)} live sources degraded — live aggregator path "
        f"is broken. Errors: {[r.error for r in results]}"
    )



@pytest.mark.live
def test_live_wttj_fetch():
    """Live WTTJ Algolia flow (Step B): env-cred fetch + Algolia query.
    0-job result = FAILURE; transient fetch errors (CloudFront block) skip
    via _skip_if_transient, cred-rotation/shape errors FAIL."""
    from jobsearch.sources import wttj
    try:
        jobs = wttj.fetch("software engineer", num_results=5,
                          cfg=load_config())
    except RuntimeError as exc:
        _skip_if_transient(exc, "WTTJ")
    assert len(jobs) > 0, (
        "WTTJ returned 0 jobs — /api/env creds rotated or Algolia shape "
        "drifted. Investigate wttj.py.")
    for j in jobs:
        assert j.source == "WTTJ"
        assert j.title and j.company
        assert j.link.startswith("https://www.welcometothejungle.com/")


@pytest.mark.live
def test_live_personio_fetch():
    """Live Personio XML feed (Step B). NOTE: ≥25s global pacing — this
    test sleeps. 0-job result = FAILURE (both default tenants empty is a
    feed-shape signal, not a rate-limit)."""
    from jobsearch.sources import personio
    try:
        jobs = personio.fetch("engineer", num_results=5, cfg=load_config())
    except RuntimeError as exc:
        _skip_if_transient(exc, "Personio")
    assert len(jobs) > 0, (
        "Personio returned 0 jobs across default tenants — XML shape "
        "drifted or both tenants empty. Investigate personio.py.")
    for j in jobs:
        assert j.source.startswith("Personio.")
        assert j.link.startswith("https://") and "/job/" in j.link


@pytest.mark.live
def test_live_careerjet_referer_auth():
    """Live Careerjet via Referer-auth (Step A). Requires CAREERJET_API_KEY
    + CAREERJET_REFERER in env; skips when unconfigured."""
    from jobsearch.sources import careerjet
    cfg = load_config()
    if not (cfg.careerjet_api_key and cfg.careerjet_referer):
        pytest.skip("Careerjet Referer-auth not configured "
                    "(CAREERJET_API_KEY + CAREERJET_REFERER)")
    try:
        jobs = careerjet.fetch("software engineer", num_results=5, cfg=cfg)
    except RuntimeError as exc:
        _skip_if_transient(exc, "Careerjet")
    assert len(jobs) > 0, "Careerjet returned 0 jobs — Referer auth broken?"
    for j in jobs:
        assert j.source == "Careerjet"
        assert j.title and j.company


@pytest.mark.live
def test_live_usajobs_zenrows_fallback():
    """Live USAJobs (Step A): direct → 403 from HK, US-proxy fallback → 200.
    Requires USAJOBS keys + (NETLIFY_SCRAPER_TOKEN or ZENROWS_API_KEY);
    skips when no US-egress transport is configured. 2026-08-29: the
    Netlify edge scraper is the primary fallback (ZenRows credits
    exhausted); the key was re-registered via scripts/usajobs_reregister.py
    (USAJobs now issues base64 keys + a verification email flow)."""
    from jobsearch.sources import usajobs
    cfg = load_config()
    if not (cfg.usajobs_api_key and cfg.usajobs_user_agent
            and (cfg.netlify_scraper_token or cfg.zenrows_api_key)):
        pytest.skip("USAJobs US-proxy fallback not configured")
    try:
        # "software engineer" has ~2 remote-marked federal listings per 25
        # ("python developer" has only 2 listings TOTAL, none remote — a
        # 0-job remote result is the true answer there, verified live
        # 2026-08-29; the pager collects remote rows across pages).
        jobs = usajobs.fetch("software engineer", num_results=5, cfg=cfg)
    except RuntimeError as exc:
        _skip_if_transient(exc, "USAJobs")
    assert len(jobs) > 0, "USAJobs returned 0 jobs — key invalid?"
    for j in jobs:
        assert j.source == "USAJobs"


@pytest.mark.live
def test_live_aggregator_trust_enrichment():
    """Step D wiring: search_all_sources must return trust-enriched jobs
    (every job carries trust_score/flags/level)."""
    sources = ["Remotive", "Arbeitnow"]
    results = search_all_sources("python", "Remote", sources, 3,
                                 load_config())
    ok_results = [r for r in results if r.ok and r.jobs]
    assert ok_results, "no source returned jobs — cannot verify enrichment"
    for r in ok_results:
        for j in r.jobs:
            assert j.trust_score is not None
            assert isinstance(j.trust_flags, list)
            assert j.trust_level in ("high", "medium", "low")
