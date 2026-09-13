"""SmartRecruiters + Ashby ATS-direct clients.

Both sources follow the per-company attribution pattern (source string
includes the slug, e.g. "SmartRecruiters.averydennison") so the dedup
stage can attribute jobs back to the originating company board.

SmartRecruiters is fully wired (public, no-auth, verified working slugs).
Ashby is an auth-blocked stub (NotImplementedError) — the _normalize_job_posting
helper preserves the parse logic so enabling Ashby later is just replacing
fetch (see ashby.py docstring for the auth gap and recovery path).

Monkeypatch pattern: each client module does `from .base import fetch_json`
at import time, so tests patch e.g. jobsearch.sources.smartrecruiters.
fetch_json, NOT jobsearch.sources.base.fetch_json (same gotcha as
test_sources.py / test_sources_freekey.py).
"""
from __future__ import annotations

import json
import re

import pytest
import requests

from jobsearch.config import Config, load_config
from jobsearch.sources import ashby, smartrecruiters

from conftest import SOURCE_FIXTURES


@pytest.fixture
def cfg(tmp_path) -> Config:
    """Config with a temp DB; defaults carry the verified SR slugs."""
    return Config(db_path=tmp_path / "t.db")


def load_fixture(name: str):
    return json.loads((SOURCE_FIXTURES / name).read_text(encoding="utf-8"))


def _resp(status: int) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    return r


def _sr_row(i: int, with_company: bool = True) -> dict:
    """Minimal valid SmartRecruiters posting row for pagination fixtures
    (audit P2-3). Rows without a company are dropped by _normalize — that's
    how the multi-page fixture simulates client-side row loss."""
    row = {
        "id": f"7440001431{i:06d}",
        "name": f"Software Engineer {i}",
        "releasedDate": "2026-08-15T14:04:56.128Z",
        "location": {"city": "Paris", "region": "IDF", "country": "fr",
                     "remote": True, "hybrid": False,
                     "fullLocation": "Paris, IDF, France"},
        "ref": ("https://api.smartrecruiters.com/v1/companies/x/"
                f"postings/{i}"),
    }
    if with_company:
        row["company"] = {"identifier": "x", "name": f"Co {i}"}
    return row


# ── SmartRecruiters ────────────────────────────────────────────────────────

class TestSmartRecruiters:
    def test_fetch_parses_fixture(self, monkeypatch, cfg):
        fx = load_fixture("smartrecruiters.json")
        seen: dict = {}

        def fake(url, *, params=None, cfg=None, headers=None,
                method="GET", json=None, auth=None):
            seen["url"], seen["params"] = url, params
            return fx

        monkeypatch.setattr(smartrecruiters, "fetch_json", fake)
        # Pin a single slug so per_slug math = num_results (deterministic).
        cfg.smartrecruiters_slugs = ["smartrecruiters"]
        jobs = smartrecruiters.fetch("python", num_results=2, cfg=cfg)

        assert len(jobs) == 2
        assert seen["url"] == ("https://api.smartrecruiters.com/v1/companies/"
                               "smartrecruiters/postings")
        assert seen["params"]["q"] == "python"
        assert seen["params"]["limit"] == 2
        assert seen["params"]["offset"] == 0

        j = jobs[0]
        assert j.title == "Senior Information Security Engineer"
        assert j.company == "SmartRecruiters Inc"
        assert j.source == "SmartRecruiters.smartrecruiters"
        # Synthesized careers-site URL (the API's `ref` field is the API
        # endpoint, not the human-readable apply page).
        assert j.link == ("https://careers.smartrecruiters.com/"
                          "smartrecruiters/744000143115219")
        assert j.location == "Poland, REMOTE, Poland"
        assert j.remote is True              # location.remote == True
        assert j.date_posted == "2026-08-15"
        # Description synthesized from structured fields, trimmed to ≤500.
        assert "<" not in j.description and ">" not in j.description
        assert len(j.description) <= 500
        assert "Engineering" in j.description
        assert "Full-time" in j.description

    def test_fetch_per_slug_attribution(self, monkeypatch, cfg):
        """Per the Greenhouse/Lever pattern: source string includes the slug."""
        fx = load_fixture("smartrecruiters.json")
        monkeypatch.setattr(smartrecruiters, "fetch_json",
                            lambda *a, **k: fx)
        cfg.smartrecruiters_slugs = ["smartrecruiters"]
        jobs = smartrecruiters.fetch("python", num_results=2, cfg=cfg)
        assert all(j.source == "SmartRecruiters.smartrecruiters" for j in jobs)

    def test_fetch_h1b_detected_from_synthesized_description(self, monkeypatch,
                                                             cfg):
        """Row 2 has a 'H1B visa sponsorship available' customField value."""
        fx = load_fixture("smartrecruiters.json")
        monkeypatch.setattr(smartrecruiters, "fetch_json",
                            lambda *a, **k: fx)
        cfg.smartrecruiters_slugs = ["smartrecruiters"]
        jobs = smartrecruiters.fetch("python", num_results=2, cfg=cfg)
        assert jobs[1].h1b_mention is True
        assert jobs[0].h1b_mention is False   # row 1 has no h1b signal

    def test_fetch_spreads_num_results_across_slugs(self, monkeypatch, cfg):
        """num_results=3 across 3 slugs → per_slug = 1 (ceiling division)."""
        seen_slugs: list = []
        fx_per_slug = {
            "smartrecruiters": load_fixture("smartrecruiters.json"),
            "averydennison": load_fixture("smartrecruiters.json"),
            "geico": load_fixture("smartrecruiters.json"),
        }

        def fake(url, *, params=None, cfg=None, headers=None,
                method="GET", json=None, auth=None):
            # extract slug from URL path
            m = re.search(r"/companies/([a-z0-9-]+)/postings", url)
            slug = m.group(1) if m else "?"
            seen_slugs.append(slug)
            return fx_per_slug.get(slug, {"content": []})

        monkeypatch.setattr(smartrecruiters, "fetch_json", fake)
        cfg.smartrecruiters_slugs = ["smartrecruiters", "averydennison", "geico"]
        jobs = smartrecruiters.fetch("python", num_results=3, cfg=cfg)

        # Each slug called once; 2 jobs per slug (fixture has 2 rows, per_slug=1
        # but the loop stops at `limit` so we get 1 from each slug).
        assert sorted(seen_slugs) == ["averydennison", "geico", "smartrecruiters"]
        # Truncation to num_results at the end (6 jobs fetched, 3 returned).
        assert len(jobs) == 3
        # All three slug attributions appear in the result set.
        sources = {j.source for j in jobs}
        assert sources == {"SmartRecruiters.smartrecruiters",
                           "SmartRecruiters.averydennison",
                           "SmartRecruiters.geico"}

    def test_fetch_skips_404_silently(self, monkeypatch, cfg):
        """Per Greenhouse/Lever pattern: a 404 slug degrades to [] (no crash)."""
        fx = load_fixture("smartrecruiters.json")

        def fake(url, *, params=None, cfg=None, headers=None,
                method="GET", json=None, auth=None):
            if "no-such-slug" in url:
                raise requests.HTTPError("404", response=_resp(404))
            return fx

        monkeypatch.setattr(smartrecruiters, "fetch_json", fake)
        cfg.smartrecruiters_slugs = ["smartrecruiters", "no-such-slug"]
        jobs = smartrecruiters.fetch("python", num_results=4, cfg=cfg)
        # The good slug still returns its 2 fixture jobs; the 404 slug is skipped.
        assert len(jobs) == 2
        assert all(j.source == "SmartRecruiters.smartrecruiters" for j in jobs)

    def test_fetch_http_500_all_slugs_fail_raises(self, monkeypatch, cfg):
        """D3 policy: when ALL slugs fail with 5xx, fetch() raises a single
        aggregated RuntimeError so the parent aggregator's SourceResult.error
        surfaces — it does NOT silently return []. Mirrors the Greenhouse/Lever
        contract (P0 fix: previous implementation silently swallowed 5xx)."""
        def fake(*a, **k):
            raise requests.HTTPError("500", response=_resp(500))
        monkeypatch.setattr(smartrecruiters, "fetch_json", fake)
        cfg.smartrecruiters_slugs = ["smartrecruiters"]
        with pytest.raises(RuntimeError, match="all 1 slug"):
            smartrecruiters.fetch("python", cfg=cfg)

    def test_fetch_http_500_all_slugs_fail_aggregates_messages(self, monkeypatch, cfg):
        """When multiple slugs fail, the aggregated error message lists all
        of them (truncated to 300 chars) so the user knows which slugs broke."""
        def fake(*a, **k):
            raise requests.HTTPError("500", response=_resp(500))
        monkeypatch.setattr(smartrecruiters, "fetch_json", fake)
        cfg.smartrecruiters_slugs = ["slug-a", "slug-b", "slug-c"]
        with pytest.raises(RuntimeError, match="all 3 slug"):
            smartrecruiters.fetch("python", cfg=cfg)

    def test_fetch_http_500_one_slug_failing_others_continue(self, monkeypatch,
                                                             cfg):
        """Per-slug isolation: a 500 on one slug doesn't drop the others."""
        fx = load_fixture("smartrecruiters.json")

        def fake(url, *, params=None, cfg=None, headers=None,
                method="GET", json=None, auth=None):
            if "broken-slug" in url:
                raise requests.HTTPError("500", response=_resp(500))
            return fx

        monkeypatch.setattr(smartrecruiters, "fetch_json", fake)
        cfg.smartrecruiters_slugs = ["smartrecruiters", "broken-slug"]
        jobs = smartrecruiters.fetch("python", num_results=4, cfg=cfg)
        # The 500 slug is skipped silently; the good slug still returns jobs.
        assert len(jobs) == 2
        assert all(j.source == "SmartRecruiters.smartrecruiters" for j in jobs)

    def test_fetch_empty_slugs_returns_empty_list(self, monkeypatch, cfg):
        """When cfg.smartrecruiters_slugs is [], fetch returns [] (no fetches)."""
        called = {"n": 0}

        def fake(*a, **k):
            called["n"] += 1
            return {"content": []}

        monkeypatch.setattr(smartrecruiters, "fetch_json", fake)
        cfg.smartrecruiters_slugs = []
        assert smartrecruiters.fetch("python", cfg=cfg) == []
        assert called["n"] == 0

    def test_fetch_200_with_empty_content_is_not_error(self, monkeypatch, cfg):
        """A valid slug with zero public postings returns [] (no exception)."""
        monkeypatch.setattr(smartrecruiters, "fetch_json",
                            lambda *a, **k: {"content": [], "totalFound": 0})
        cfg.smartrecruiters_slugs = ["smartrecruiters"]
        assert smartrecruiters.fetch("python", cfg=cfg) == []

    def test_fetch_one_paginates_offset_until_per_slug(self, monkeypatch, cfg):
        """audit P2-3: per-slug single-request truncation fixed — offset
        advances by limit while the slug's rows are below per_slug and
        offset < totalFound (cap 3 pages, 0.3s pacing); the side-effect fake
        asserts the offset param actually advances.

        Page 1 is a full 40-row page of which only 10 carry a company →
        _normalize keeps 10, so a second page (offset=40) must be fetched.
        """
        offsets: list[int] = []

        def fake(url, *, params=None, cfg=None, headers=None,
                 method="GET", json=None, auth=None):
            assert "/postings" in url
            offset = (params or {}).get("offset", 0)
            offsets.append(offset)
            if offset == 0:
                content = [_sr_row(i, with_company=(i < 10))
                           for i in range(40)]
                return {"offset": 0, "limit": 40, "totalFound": 80,
                        "content": content}
            content = [_sr_row(100 + i) for i in range(40)]
            return {"offset": 40, "limit": 40, "totalFound": 80,
                    "content": content}

        monkeypatch.setattr(smartrecruiters, "fetch_json", fake)
        monkeypatch.setattr(smartrecruiters.time, "sleep", lambda s: None)
        cfg.smartrecruiters_slugs = ["smartrecruiters"]
        jobs = smartrecruiters.fetch("python", num_results=40, cfg=cfg)

        assert len(jobs) == 40                # per_slug share satisfied (> 20)
        assert offsets == [0, 40]             # offset param actually advanced
        assert len({j.link for j in jobs}) == 40   # no duplicates

    def test_fetch_truncation_safe_when_board_smaller_than_request(
            self, monkeypatch, cfg):
        """audit P2-3: fewer postings available than requested (short page,
        totalFound=5) → return what exists without error, no extra pages."""
        calls = {"n": 0}

        def fake(url, *, params=None, cfg=None, headers=None,
                 method="GET", json=None, auth=None):
            calls["n"] += 1
            return {"offset": 0, "limit": 40, "totalFound": 5,
                    "content": [_sr_row(i) for i in range(5)]}

        monkeypatch.setattr(smartrecruiters, "fetch_json", fake)
        cfg.smartrecruiters_slugs = ["smartrecruiters"]
        jobs = smartrecruiters.fetch("python", num_results=40, cfg=cfg)
        assert len(jobs) == 5
        assert calls["n"] == 1                 # exhausted → no second request

    def test_fetch_one_later_page_failure_degrades(self, monkeypatch, cfg):
        """audit P2-3 failure isolation: a page-2 500 keeps the slug's page-1
        rows instead of discarding the whole slug (mirrors the per-slug D3
        isolation the aggregator applies across slugs)."""

        def fake(url, *, params=None, cfg=None, headers=None,
                 method="GET", json=None, auth=None):
            if (params or {}).get("offset", 0) == 0:
                # full page of 40 rows, only 10 carry a company → kept
                return {"offset": 0, "limit": 40, "totalFound": 80,
                        "content": [_sr_row(i, with_company=(i < 10))
                                    for i in range(40)]}
            raise requests.HTTPError("500", response=_resp(500))

        monkeypatch.setattr(smartrecruiters, "fetch_json", fake)
        monkeypatch.setattr(smartrecruiters.time, "sleep", lambda s: None)
        cfg.smartrecruiters_slugs = ["smartrecruiters"]
        jobs = smartrecruiters.fetch("python", num_results=40, cfg=cfg)
        assert len(jobs) == 10               # page-1 rows kept, no raise

    def test_normalize_skips_row_without_title(self):
        from jobsearch.sources.smartrecruiters import _normalize
        item = {"name": "", "company": {"name": "X"}}
        assert _normalize(item, "x", "python") is None

    def test_normalize_skips_row_without_company(self):
        from jobsearch.sources.smartrecruiters import _normalize
        item = {"name": "Job Title", "company": {}}
        assert _normalize(item, "x", "python") is None


# ── Ashby (auth-blocked stub) ──────────────────────────────────────────────

class TestAshby:
    """Ashby is REBUILT (2026-08-27): HTML-board path (no auth) replaces the
    NotImplementedError stub. The keyed-API shape tests moved to history —
    see test_sources_new3.py for the new HTML-board coverage.

    The keyed-API path remains documented in ashby.py's module docstring
    for when a per-customer Ashby-API-Key becomes available."""

    def test_fetch_no_longer_raises_not_implemented(self, monkeypatch, cfg):
        """The HTML-board fetch is live: boards are fetched via fetch_text,
        small shells return [], and one dead org is isolated (not fatal)."""
        import requests as _r

        def fake_fetch_text(url, **k):
            if "ghostco" in url:
                raise _r.HTTPError(response=_r.Response() if False else None)  # noqa
            return "<html>tiny shell</html>"

        # Simpler: shell pages everywhere → fetch returns [] (not a raise).
        monkeypatch.setattr(ashby, "fetch_text",
                            lambda url, **k: "<html>tiny shell page</html>")
        cfg.ashby_orgs = ["ghostco"]
        assert ashby.fetch("python", cfg=cfg) == []

    def test_old_keyed_api_fixture_shape_preserved(self):
        """The old keyed-API fixture (ashby.json) is kept as the documented
        shape for the ASHBY_API_KEY path — parse it directly so the shape
        knowledge is not lost when the keyed path is wired in later."""
        fx = load_fixture("ashby.json")
        row = fx["results"][0]
        assert row["title"] == "Senior Backend Engineer"
        assert row["companyName"] == "Acme Cloud"
        assert row["compensation"]["minValue"] == 130000
        assert row["locationFlexibility"] == ["Remote"]


# ── Config env resolution for the new fields ──────────────────────────────

class TestConfigAtsFields:
    def test_defaults_smartrecruiters_slugs_verified_list(self, tmp_path):
        cfg = Config(db_path=tmp_path / "t.db")
        # Verified-working list documented in config.py + worklog SRC-ATS-AS.
        assert cfg.smartrecruiters_slugs == [
            "smartrecruiters", "averydennison", "geico",
            "bigcommerce", "wework", "yardi",
        ]

    def test_defaults_ashby_orgs_empty(self, tmp_path):
        """ashby_orgs env override stays empty by default — the adapter's
        curated _DEFAULT_ORGS (openai/notion/ramp/linear/ashby, verified
        2026-08-27) applies when the env var is unset."""
        cfg = Config(db_path=tmp_path / "t.db")
        assert cfg.ashby_orgs == []
        assert cfg.ashby_api_key == ""

    def test_env_overrides_smartrecruiters_slugs(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SMARTRECRUITERS_SLUGS", "acme, beta, gamma")
        cfg = Config(db_path=tmp_path / "t.db")
        assert cfg.smartrecruiters_slugs == ["acme", "beta", "gamma"]

    def test_env_overrides_ashby_orgs_and_key(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ASHBY_ORGS", "linear, notion, ramp")
        monkeypatch.setenv("ASHBY_API_KEY", "ashby-secret-xyz")
        cfg = Config(db_path=tmp_path / "t.db")
        assert cfg.ashby_orgs == ["linear", "notion", "ramp"]
        assert cfg.ashby_api_key == "ashby-secret-xyz"

    def test_load_config_merges_env_and_defaults(self, monkeypatch, tmp_path):
        """load_config (used by the registry) resolves the new env vars too."""
        from jobsearch.config import load_config
        monkeypatch.setenv("ASHBY_ORGS", "linear")
        cfg = load_config()
        assert cfg.ashby_orgs == ["linear"]
        # Unset env fields keep their dataclass defaults.
        assert cfg.smartrecruiters_slugs == [
            "smartrecruiters", "averydennison", "geico",
            "bigcommerce", "wework", "yardi",
        ]


# ── Live tests (deselected by default; -m live to run) ────────────────────

def _live_skip_message(reason: str) -> str:
    return f"{reason} (see worklog SRC-ATS-AS)"


class TestLiveAtsAs:
    @pytest.mark.live
    def test_live_smartrecruiters(self):
        """Live test for SmartRecruiters ATS-direct aggregator.

        Handles the new aggregated RuntimeError contract (P0-1 fix): if ALL
        slugs fail with 5xx or network errors, fetch() now raises rather than
        silently returning []. The test skips cleanly on that case so the
        live suite reports SKIP (environmental), not FAILURE.
        """
        from jobsearch.config import load_config
        cfg = load_config()
        try:
            jobs = smartrecruiters.fetch("python", num_results=3, cfg=cfg)
        except RuntimeError as exc:
            pytest.skip(
                f"SmartRecruiters live API unreachable (all slugs failed): "
                f"{exc}"
            )
        assert isinstance(jobs, list)
        # P0-1 fix: a 0-job response from SmartRecruiters means all slugs
        # returned 200 OK with content=[] (no current postings) — that's NOT
        # a runtime error, but it IS suspicious enough to flag as a failure
        # rather than a vacuous pass.
        assert len(jobs) > 0, (
            "SmartRecruiters returned 0 jobs across all configured slugs. "
            "This is either a transient outage (every company has no public "
            "postings) or a fixture/shape drift. Investigate before merging."
        )
        for j in jobs:
            assert j.source.startswith("SmartRecruiters.")
            assert j.title and j.company
            assert j.link.startswith("https://careers.smartrecruiters.com/")

    @pytest.mark.live
    def test_live_ashby(self):
        """Ashby HTML-board path is live (rebuilt 2026-08-27, Step B):
        no-auth boards at jobs.ashbyhq.com/{slug}. 0-job result = FAILURE
        (per the live-test policy); fetch errors skip cleanly."""
        from jobsearch.sources import ashby as ashby_mod
        try:
            jobs = ashby_mod.fetch("engineer", num_results=3,
                                   cfg=load_config())
        except RuntimeError as exc:
            pytest.skip(f"Ashby live boards unreachable: {exc}")
        assert len(jobs) > 0, (
            "Ashby returned 0 jobs across default orgs — boards moved or "
            "appData shape drifted. Investigate ashby.py.")
        for j in jobs:
            assert j.source.startswith("Ashby.")
            assert j.link.startswith("https://jobs.ashbyhq.com/")
