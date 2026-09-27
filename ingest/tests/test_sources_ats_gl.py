"""Greenhouse + Lever ATS-direct clients (SRC-ATS-GL).

Both sources follow the per-company attribution pattern (source string is
`f"Greenhouse.{token}"` / `f"Lever.{slug}"`) so the dedup layer treats
cross-ATS dupes correctly: the same Stripe posting on Greenhouse vs an
aggregator gets a different `source` string and is deduped by (title +
company + link), not by source.

Both sources are no-auth public-API endpoints. Defaults are pinned to the
verified-live company slugs/tokens found during SRC-ATS-GL discovery:
  • Greenhouse: 15 well-known tech-company board tokens (Stripe = 580 jobs).
  • Lever: 8 verified-live slugs out of the 30+ the main agent and this
    sub-agent probed — Notion, Brex, Plaid, Stripe, Figma, Vercel, Datadog,
    Anthropic, OpenAI, Replit, Mercury, Mozilla, Hashicorp, Intercom,
    Substack, Patreon, et al. ALL 404'd on Lever (migrated to Greenhouse/
    Ashby/Workday). Spotify, ro, capital, qonto, wealthfront, moonpay,
    tri, newton are the only slugs returning real postings 2026-08.

Monkeypatch pattern: each client module does `from .base import fetch_json`
at import time, so tests patch e.g. jobsearch.sources.greenhouse.fetch_json,
NOT jobsearch.sources.base.fetch_json (same gotcha as test_sources.py /
test_sources_freekey.py / test_sources_ats_as.py).
"""
from __future__ import annotations

import json
import re

import pytest
import requests

from jobsearch.config import Config
from jobsearch.sources import greenhouse, lever

from conftest import SOURCE_FIXTURES


@pytest.fixture
def cfg(tmp_path) -> Config:
    """Config with a temp DB; defaults carry the verified boards/slugs."""
    return Config(db_path=tmp_path / "t.db")


def load_fixture(name: str):
    return json.loads((SOURCE_FIXTURES / name).read_text(encoding="utf-8"))


def _resp(status: int) -> requests.Response:
    """Build a real-enough Response for HTTPError(response=...)."""
    r = requests.Response()
    r.status_code = status
    return r


# ── Greenhouse ────────────────────────────────────────────────────────────

class TestGreenhouse:
    def test_fetch_parses_fixture(self, monkeypatch, cfg):
        """Single-board happy path: list + 2 detail fetches, both with
        descriptions; source attribution is `Greenhouse.{board}`."""
        fx = load_fixture("greenhouse.json")
        seen_urls: list = []

        def fake(url, *, params=None, cfg=None, headers=None):
            seen_urls.append(url)
            if url.endswith("/jobs"):
                # list endpoint returns {"jobs": [...]}
                return {"jobs": fx["jobs_list"]}
            # detail endpoint: URL ends with /jobs/{jid}
            jid = url.rstrip("/").split("/")[-1]
            return fx["details"].get(jid, {})

        monkeypatch.setattr(greenhouse, "fetch_json", fake)
        # Pin a single board so per-board math is deterministic.
        cfg.greenhouse_boards = ["stripe"]
        jobs = greenhouse.fetch("engineer", num_results=4, cfg=cfg)

        # 2 of 4 fixture rows match "engineer" (Backend / API Engineer, x2);
        # the Account Executive rows are excluded by the keyword filter.
        assert len(jobs) == 2

        # First call = list; then 2 detail calls (cap = _DETAIL_CAP_PER_BOARD)
        list_calls = [u for u in seen_urls if u.endswith("/jobs")]
        detail_calls = [u for u in seen_urls if "/jobs/" in u]
        assert len(list_calls) == 1
        assert len(detail_calls) == 2     # capped at 2 detail fetches

        j = jobs[0]
        assert j.title == "Backend / API Engineer, Metronome (Billing)"
        assert j.company == "stripe"                     # board token
        assert j.source == "Greenhouse.stripe"           # per-board attribution
        assert j.link == ("https://stripe.com/jobs/search"
                          "?gh_jid=7737237")
        assert j.location == "Toronto, Canada"
        assert j.date_posted == "2026-08-18"              # updated_at[:10]
        assert j.contact_email is None                    # ATS postings rarely
        # Description was HTML-entity-escaped in the payload (`&lt;h2&gt;`),
        # unescaped then tag-stripped — no leftover `<` or `>`.
        assert "<" not in j.description and ">" not in j.description
        assert "Stripe" in j.description                  # body text preserved
        assert len(j.description) > 50                    # not just title-only

    def test_per_board_attribution(self, monkeypatch, cfg):
        """Source string includes the board token — dedup treats each
        company's Greenhouse board as a distinct source."""
        fx = load_fixture("greenhouse.json")
        monkeypatch.setattr(greenhouse, "fetch_json",
                            lambda *a, **k: {"jobs": fx["jobs_list"]})
        cfg.greenhouse_boards = ["stripe"]
        jobs = greenhouse.fetch("engineer", num_results=4, cfg=cfg)
        assert all(j.source == "Greenhouse.stripe" for j in jobs)

    def test_multi_board_iteration(self, monkeypatch, cfg):
        """Two boards → both contribute matches; both source attributions
        appear in the result set; total capped at num_results."""
        fx = load_fixture("greenhouse.json")
        seen_boards: list = []

        def fake(url, *, params=None, cfg=None, headers=None):
            # extract board token from URL: /v1/boards/{token}/jobs[/{jid}]
            m = re.search(r"/boards/([a-z0-9]+)/jobs", url)
            if m:
                seen_boards.append(m.group(1))
            if url.endswith("/jobs"):
                return {"jobs": fx["jobs_list"]}
            jid = url.rstrip("/").split("/")[-1]
            return fx["details"].get(jid, {})

        monkeypatch.setattr(greenhouse, "fetch_json", fake)
        cfg.greenhouse_boards = ["stripe", "datadog"]
        jobs = greenhouse.fetch("engineer", num_results=4, cfg=cfg)

        # Both boards queried; both contributed.
        assert sorted(set(seen_boards)) == ["datadog", "stripe"]
        # Sources from both boards appear.
        sources = {j.source for j in jobs}
        assert sources == {"Greenhouse.stripe", "Greenhouse.datadog"}

    def test_fetch_skips_404_silently(self, monkeypatch, cfg):
        """A 404 board (renamed / migrated / typo) degrades to [] (no crash).
        The other boards still contribute their matches."""
        fx = load_fixture("greenhouse.json")

        def fake(url, *, params=None, cfg=None, headers=None):
            if "no-such-board" in url:
                raise requests.HTTPError("404", response=_resp(404))
            if url.endswith("/jobs"):
                return {"jobs": fx["jobs_list"]}
            jid = url.rstrip("/").split("/")[-1]
            return fx["details"].get(jid, {})

        monkeypatch.setattr(greenhouse, "fetch_json", fake)
        cfg.greenhouse_boards = ["stripe", "no-such-board"]
        jobs = greenhouse.fetch("engineer", num_results=4, cfg=cfg)
        # The good board still returns its 2 matches; the 404 board is skipped.
        assert len(jobs) == 2
        assert all(j.source == "Greenhouse.stripe" for j in jobs)

    def test_fetch_http_500_raises_runtime(self, monkeypatch, cfg):
        """A 5xx on the only configured board surfaces as RuntimeError
        (D3 — a fully-failed source must surface, not silently return [])."""
        def fake(*a, **k):
            raise requests.HTTPError("500", response=_resp(500))
        monkeypatch.setattr(greenhouse, "fetch_json", fake)
        cfg.greenhouse_boards = ["stripe"]
        with pytest.raises(RuntimeError, match="Greenhouse"):
            greenhouse.fetch("python", cfg=cfg)

    def test_fetch_empty_boards_returns_empty_list(self, monkeypatch, cfg):
        """When cfg.greenhouse_boards is [], fetch returns [] (no fetches)."""
        called = {"n": 0}

        def fake(*a, **k):
            called["n"] += 1
            return {"jobs": []}

        monkeypatch.setattr(greenhouse, "fetch_json", fake)
        cfg.greenhouse_boards = []
        assert greenhouse.fetch("python", cfg=cfg) == []
        assert called["n"] == 0

    def test_fetch_caps_at_num_results(self, monkeypatch, cfg):
        """num_results=1 truncates the cross-board aggregate to 1."""
        fx = load_fixture("greenhouse.json")
        monkeypatch.setattr(greenhouse, "fetch_json",
                            lambda *a, **k: {"jobs": fx["jobs_list"]})
        cfg.greenhouse_boards = ["stripe"]
        jobs = greenhouse.fetch("engineer", num_results=1, cfg=cfg)
        assert len(jobs) == 1

    def test_fetch_caps_detail_fetches_at_2_per_board(self, monkeypatch, cfg):
        """Even if 4 matches, only the first 2 get a detail fetch (polite cap)."""
        fx = load_fixture("greenhouse.json")
        detail_calls = {"n": 0}

        def fake(url, *, params=None, cfg=None, headers=None):
            if url.endswith("/jobs"):
                return {"jobs": fx["jobs_list"]}
            # ALL 4 fixture rows match "engineer" if we don't filter — trick
            # the keyword filter by passing empty keywords so all 4 match,
            # then assert detail calls cap at 2.
            detail_calls["n"] += 1
            jid = url.rstrip("/").split("/")[-1]
            return fx["details"].get(jid, {})

        monkeypatch.setattr(greenhouse, "fetch_json", fake)
        cfg.greenhouse_boards = ["stripe"]
        # empty keywords → no filter → all 4 list rows become matches
        jobs = greenhouse.fetch("", num_results=10, cfg=cfg)
        assert len(jobs) == 4
        assert detail_calls["n"] == 2     # _DETAIL_CAP_PER_BOARD

    def test_parse_gh_jid_from_url(self):
        assert greenhouse._parse_gh_jid(
            "https://stripe.com/jobs/search?gh_jid=7737237") == "7737237"
        assert greenhouse._parse_gh_jid(
            "https://acme.com/careers?gh_jid=42") == "42"
        # No gh_jid param → None (detail fetch skipped).
        assert greenhouse._parse_gh_jid("https://acme.com/careers/7737237") is None
        assert greenhouse._parse_gh_jid("") is None
        assert greenhouse._parse_gh_jid(None) is None

    def test_coerce_date_handles_iso_with_offset(self):
        assert greenhouse._coerce_date(
            "2026-08-18T17:59:41-04:00") == "2026-08-18"
        assert greenhouse._coerce_date("2026-08-19") == "2026-08-19"
        assert greenhouse._coerce_date(None) is None
        assert greenhouse._coerce_date("") is None
        assert greenhouse._coerce_date("not a date") is None

    def test_detail_fetch_404_keeps_title_only_job(self, monkeypatch, cfg):
        """A 404 on the detail endpoint (posting pulled) is silently caught;
        the Job keeps its title + link with an empty description."""
        fx = load_fixture("greenhouse.json")

        def fake(url, *, params=None, cfg=None, headers=None):
            if url.endswith("/jobs"):
                return {"jobs": fx["jobs_list"]}
            # Every detail call 404s
            raise requests.HTTPError("404", response=_resp(404))

        monkeypatch.setattr(greenhouse, "fetch_json", fake)
        cfg.greenhouse_boards = ["stripe"]
        jobs = greenhouse.fetch("engineer", num_results=4, cfg=cfg)
        # Matches still returned (detail failure didn't kill the row).
        assert len(jobs) == 2
        # Descriptions stay empty (no detail body fetched).
        assert all(j.description == "" for j in jobs)
        # But title + link + source still set from the list payload.
        assert jobs[0].title and jobs[0].link and jobs[0].source


# ── Lever ──────────────────────────────────────────────────────────────────

class TestLever:
    def test_fetch_parses_fixture(self, monkeypatch, cfg):
        """Single-slug happy path: postings parsed; per-slug source attribution."""
        fx = load_fixture("lever.json")
        seen: dict = {}

        def fake(url, *, params=None, cfg=None, headers=None):
            seen["url"], seen["params"] = url, params
            return fx["postings"]

        monkeypatch.setattr(lever, "fetch_json", fake)
        cfg.lever_slugs = ["spotify"]
        jobs = lever.fetch("engineer", num_results=4, cfg=cfg)

        # 1 of 2 fixture postings matches "engineer" (Android Engineer).
        assert len(jobs) == 1
        # API endpoint + query params correct
        assert seen["url"] == "https://api.lever.co/v0/postings/spotify"
        assert seen["params"]["mode"] == "json"
        assert seen["params"]["limit"] == 4

        j = jobs[0]
        assert j.title == "Android Engineer - Advertising"
        assert j.company == "spotify"                       # slug
        assert j.source == "Lever.spotify"                # per-slug attribution
        assert j.link == ("https://jobs.lever.co/spotify/"
                          "a0fa7da3-4c3c-4fa2-97bd-7d6eb01eb9e5")
        assert j.location == "New York, NY"
        # createdAt = 1773857225234 (epoch ms) → 2026-03-18 (UTC)
        assert j.date_posted == "2026-03-18"
        # workplaceType=remote → remote=True
        assert j.remote is True
        # descriptionPlain + additionalPlain folded together; no HTML tags.
        assert "<" not in j.description and ">" not in j.description
        assert len(j.description) > 50

    def test_per_slug_attribution(self, monkeypatch, cfg):
        fx = load_fixture("lever.json")
        monkeypatch.setattr(lever, "fetch_json",
                            lambda *a, **k: fx["postings"])
        cfg.lever_slugs = ["spotify"]
        jobs = lever.fetch("engineer", num_results=4, cfg=cfg)
        assert all(j.source == "Lever.spotify" for j in jobs)

    def test_fetch_multi_slug_iteration(self, monkeypatch, cfg):
        """Two slugs → both contribute; both source attributions present."""
        fx = load_fixture("lever.json")
        seen_slugs: list = []

        def fake(url, *, params=None, cfg=None, headers=None):
            # extract slug from URL: /v0/postings/{slug}
            m = re.search(r"/postings/([a-z0-9_-]+)", url)
            if m:
                seen_slugs.append(m.group(1))
            return fx["postings"]

        monkeypatch.setattr(lever, "fetch_json", fake)
        cfg.lever_slugs = ["spotify", "qonto"]
        jobs = lever.fetch("engineer", num_results=4, cfg=cfg)

        assert sorted(set(seen_slugs)) == ["qonto", "spotify"]
        sources = {j.source for j in jobs}
        assert sources == {"Lever.spotify", "Lever.qonto"}

    def test_fetch_skips_404_silently(self, monkeypatch, cfg):
        """A 404 slug degrades to [] (no crash). The other slug still works."""
        fx = load_fixture("lever.json")

        def fake(url, *, params=None, cfg=None, headers=None):
            if "no-such-slug" in url:
                raise requests.HTTPError("404", response=_resp(404))
            return fx["postings"]

        monkeypatch.setattr(lever, "fetch_json", fake)
        cfg.lever_slugs = ["spotify", "no-such-slug"]
        jobs = lever.fetch("engineer", num_results=4, cfg=cfg)
        # Spotify still returns its 1 match; the 404 slug is skipped.
        assert len(jobs) == 1
        assert jobs[0].source == "Lever.spotify"

    def test_fetch_skips_document_not_found_dict(self, monkeypatch, cfg):
        """Lever returns {"ok": false, "error": "Document not found"} for
        migrated-off slugs (the Notion case from main-agent probe). Treat
        that as empty — no crash."""
        fx = load_fixture("lever.json")

        def fake(url, *, params=None, cfg=None, headers=None):
            if "notion" in url:
                return {"ok": False, "error": "Document not found"}
            return fx["postings"]

        monkeypatch.setattr(lever, "fetch_json", fake)
        cfg.lever_slugs = ["spotify", "notion"]
        jobs = lever.fetch("engineer", num_results=4, cfg=cfg)
        # Spotify still returns its 1 match; Notion's dict response is skipped.
        assert len(jobs) == 1
        assert jobs[0].source == "Lever.spotify"

    def test_fetch_http_500_raises_runtime(self, monkeypatch, cfg):
        """A 5xx on the only configured slug surfaces as RuntimeError."""
        def fake(*a, **k):
            raise requests.HTTPError("500", response=_resp(500))
        monkeypatch.setattr(lever, "fetch_json", fake)
        cfg.lever_slugs = ["spotify"]
        with pytest.raises(RuntimeError, match="Lever"):
            lever.fetch("python", cfg=cfg)

    def test_fetch_empty_slugs_returns_empty_list(self, monkeypatch, cfg):
        """When cfg.lever_slugs is [], fetch returns [] (no fetches)."""
        called = {"n": 0}

        def fake(*a, **k):
            called["n"] += 1
            return []

        monkeypatch.setattr(lever, "fetch_json", fake)
        cfg.lever_slugs = []
        assert lever.fetch("python", cfg=cfg) == []
        assert called["n"] == 0

    def test_fetch_caps_at_num_results(self, monkeypatch, cfg):
        """num_results=1 truncates the cross-slug aggregate to 1."""
        fx = load_fixture("lever.json")
        monkeypatch.setattr(lever, "fetch_json",
                            lambda *a, **k: fx["postings"])
        cfg.lever_slugs = ["spotify"]
        jobs = lever.fetch("", num_results=1, cfg=cfg)
        assert len(jobs) == 1

    def test_description_plain_preferred_over_html(self, monkeypatch, cfg):
        """When both descriptionPlain and description exist, descriptionPlain wins
        (it's already plain text — no HTML stripping needed)."""
        # Reuse the fixture — both fixture rows have descriptionPlain set.
        fx = load_fixture("lever.json")
        monkeypatch.setattr(lever, "fetch_json",
                            lambda *a, **k: fx["postings"])
        cfg.lever_slugs = ["spotify"]
        jobs = lever.fetch("", num_results=4, cfg=cfg)
        # Sorted newest-first; the first row is the Advertiser Solutions
        # Vendor Lead (createdAt=1784569799619 → 2026-07-20). Its
        # descriptionPlain starts with "Sell what you love…" — no HTML tags.
        assert jobs[0].description.startswith("Sell what you love")
        assert "<" not in jobs[0].description and ">" not in jobs[0].description

    def test_description_falls_back_to_cleaned_html(self, monkeypatch, cfg):
        """When descriptionPlain is empty, description is stripped of HTML."""
        postings = [{
            "id": "lv-html-1",
            "text": "Backend Engineer",
            "categories": {"location": "Remote", "team": "Backend",
                           "department": "Engineering", "commitment": "Full"},
            "createdAt": 1784569799619,
            "descriptionPlain": "",   # empty — triggers HTML fallback
            "description": "<div>Python, FastAPI, PostgreSQL required.</div>",
            "hostedUrl": "https://jobs.lever.co/acme/lv-html-1",
            "applyUrl": "https://jobs.lever.co/acme/lv-html-1/apply",
            "workplaceType": "remote",
            "additionalPlain": "",
        }]
        monkeypatch.setattr(lever, "fetch_json",
                            lambda *a, **k: postings)
        cfg.lever_slugs = ["acme"]
        jobs = lever.fetch("backend", num_results=4, cfg=cfg)
        assert len(jobs) == 1
        # Tags stripped, body text preserved.
        assert "<" not in jobs[0].description and ">" not in jobs[0].description
        assert "FastAPI" in jobs[0].description
        assert jobs[0].remote is True

    def test_lever_date_epoch_ms_to_yyyy_mm_dd(self):
        # 1784569799619 ms = 2026-07-20 (UTC) — Spotify fixture's first posting
        assert lever._lever_date(1784569799619) == "2026-07-20"
        # 1773857225234 ms = 2026-03-18 (UTC) — Spotify fixture's second posting
        assert lever._lever_date(1773857225234) == "2026-03-18"
        assert lever._lever_date(None) is None
        assert lever._lever_date("") is None
        assert lever._lever_date("not a number") is None

    def test_lever_date_iso_fallback(self):
        # Legacy ISO 8601 string — handled by the fallback branch.
        assert lever._lever_date("2026-08-20T10:00:00Z") == "2026-08-20"

    def test_is_remote_detection(self):
        assert lever._is_remote("Remote", "remote") is True
        assert lever._is_remote("Anywhere", "") is True
        assert lever._is_remote("New York, NY", "remote") is True
        assert lever._is_remote("New York, NY", "on-site") is False
        assert lever._is_remote("London", "hybrid") is False

    def test_link_prefers_hosted_over_apply(self, monkeypatch, cfg):
        """When both hostedUrl and applyUrl exist, hostedUrl is used."""
        postings = [{
            "id": "lv1",
            "text": "Engineer",
            "categories": {"location": "Remote"},
            "createdAt": 1784569799619,
            "descriptionPlain": "desc",
            "hostedUrl": "https://jobs.lever.co/acme/lv1",
            "applyUrl": "https://jobs.lever.co/acme/lv1/apply",
            "workplaceType": "remote",
        }]
        monkeypatch.setattr(lever, "fetch_json",
                            lambda *a, **k: postings)
        cfg.lever_slugs = ["acme"]
        jobs = lever.fetch("engineer", num_results=4, cfg=cfg)
        assert jobs[0].link == "https://jobs.lever.co/acme/lv1"

    def test_link_falls_back_to_apply_url(self, monkeypatch, cfg):
        """When hostedUrl is missing, applyUrl is used."""
        postings = [{
            "id": "lv2",
            "text": "Engineer",
            "categories": {"location": "Remote"},
            "createdAt": 1784569799619,
            "descriptionPlain": "desc",
            "hostedUrl": "",
            "applyUrl": "https://jobs.lever.co/acme/lv2/apply",
            "workplaceType": "remote",
        }]
        monkeypatch.setattr(lever, "fetch_json",
                            lambda *a, **k: postings)
        cfg.lever_slugs = ["acme"]
        jobs = lever.fetch("engineer", num_results=4, cfg=cfg)
        assert jobs[0].link == "https://jobs.lever.co/acme/lv2/apply"

    def test_h1b_detected_in_description(self, monkeypatch, cfg):
        """additionalPlain carries EEO text — folded into description so
        detect_h1b can scan it (Spotify fixture's additionalPlain has no
        H1B mention; we craft one with explicit sponsorship language)."""
        postings = [{
            "id": "lv3",
            "text": "Senior Backend Engineer",
            "categories": {"location": "Remote"},
            "createdAt": 1784569799619,
            "descriptionPlain": "Python, FastAPI, PostgreSQL.",
            "additionalPlain": "We offer H1B visa sponsorship for strong candidates.",
            "hostedUrl": "https://jobs.lever.co/acme/lv3",
            "applyUrl": "https://jobs.lever.co/acme/lv3/apply",
            "workplaceType": "remote",
        }]
        monkeypatch.setattr(lever, "fetch_json",
                            lambda *a, **k: postings)
        cfg.lever_slugs = ["acme"]
        jobs = lever.fetch("engineer", num_results=4, cfg=cfg)
        assert jobs[0].h1b_mention is True


# ── Config defaults + env resolution ───────────────────────────────────────

class TestConfigAtsGlFields:
    def test_defaults_greenhouse_boards_verified_list(self, tmp_path):
        cfg = Config(db_path=tmp_path / "t.db")
        # 15 well-known tech-company board tokens verified live 2026-08.
        assert cfg.greenhouse_boards == [
            "stripe", "datadog", "anthropic", "databricks", "cloudflare",
            "brex", "scaleai", "airbnb", "coinbase", "figma",
            "reddit", "robinhood", "asana", "gusto", "vercel",
        ]

    def test_defaults_lever_slugs_verified_list(self, tmp_path):
        cfg = Config(db_path=tmp_path / "t.db")
        # 8 verified-live slugs (the only ones returning real postings during
        # SRC-ATS-GL discovery; Notion, Brex, Plaid, Stripe, Figma, Vercel,
        # Datadog, Anthropic, OpenAI, Replit, Mercury, Mozilla, Hashicorp,
        # Intercom, Substack, Patreon, etc. ALL 404'd on Lever).
        assert cfg.lever_slugs == [
            "spotify", "ro", "capital", "qonto", "wealthfront",
            "moonpay", "tri", "newton",
        ]

    def test_env_overrides_greenhouse_boards(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GREENHOUSE_BOARDS", "acme, beta, gamma")
        cfg = Config(db_path=tmp_path / "t.db")
        assert cfg.greenhouse_boards == ["acme", "beta", "gamma"]

    def test_env_overrides_lever_slugs(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LEVER_SLUGS", "acme, beta")
        cfg = Config(db_path=tmp_path / "t.db")
        assert cfg.lever_slugs == ["acme", "beta"]

    def test_load_config_merges_env_and_defaults(self, monkeypatch, tmp_path):
        """load_config (used by the registry) resolves the new env vars too."""
        from jobsearch.config import load_config
        monkeypatch.setenv("LEVER_SLUGS", "ro")
        cfg = load_config()
        assert cfg.lever_slugs == ["ro"]
        # Unset env fields keep their dataclass defaults.
        assert cfg.greenhouse_boards == [
            "stripe", "datadog", "anthropic", "databricks", "cloudflare",
            "brex", "scaleai", "airbnb", "coinbase", "figma",
            "reddit", "robinhood", "asana", "gusto", "vercel",
        ]


# ── Live tests (deselected by default; -m live to run) ────────────────────

def _live_skip_message(reason: str) -> str:
    return f"{reason} (see worklog SRC-ATS-GL)"


class TestLiveAtsGl:
    @pytest.mark.live
    def test_live_greenhouse_stripe(self):
        """Stripe's Greenhouse board — confirmed 580 jobs at build time."""
        from jobsearch.config import load_config
        cfg = load_config()
        # Pin to just stripe to keep the live test fast + bounded.
        cfg.greenhouse_boards = ["stripe"]
        jobs = greenhouse.fetch("engineer", num_results=3, cfg=cfg)
        assert isinstance(jobs, list)
        for j in jobs:
            assert j.source == "Greenhouse.stripe"
            assert j.title and j.company
            assert j.link.startswith("https://")
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", j.date_posted or "")

    @pytest.mark.live
    def test_live_lever_spotify(self):
        """Spotify's Lever board — confirmed 95 postings at build time."""
        from jobsearch.config import load_config
        cfg = load_config()
        cfg.lever_slugs = ["spotify"]
        jobs = lever.fetch("engineer", num_results=3, cfg=cfg)
        assert isinstance(jobs, list)
        for j in jobs:
            assert j.source == "Lever.spotify"
            assert j.title and j.company
            assert j.link.startswith("https://jobs.lever.co/spotify/")
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", j.date_posted or "")
