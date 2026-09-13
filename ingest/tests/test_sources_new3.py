"""Tests for the three 2026-08-27 adapter builds (SRC-WTTJ / Ashby HTML /
Personio XML) — offline fixtures, no network.

- WTTJ: Algolia hit fixture (wttj.json) — cred refresh flow mocked.
- Ashby: HTML-board appData fixture (ashby_board.json) — _extract_app_data
  brace matching, posting mapping, size threshold, per-org isolation.
- Personio: XML feed fixture (personio.xml) — parse + HTML fallback +
  global pacing behavior.
"""
from __future__ import annotations

import json
import re
import time

import pytest
import requests

from jobsearch.config import Config
from jobsearch.sources import ashby, personio, wttj

from conftest import SOURCE_FIXTURES


def load_fixture(name: str):
    return json.loads((SOURCE_FIXTURES / name).read_text(encoding="utf-8"))


def _resp(status: int) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    return r


def _reset_wttj_cache():
    wttj._env_cache = None


# ── WTTJ ─────────────────────────────────────────────────────────────────────

class TestWTTJ:
    def setup_method(self):
        _reset_wttj_cache()

    def test_is_configured_always(self, cfg):
        assert wttj.is_configured(cfg) is True

    def test_fetch_parses_fixture(self, monkeypatch, cfg):
        fx = load_fixture("wttj.json")
        seen: dict = {}

        def fake_env(force=False):
            return "APPID1", "apikey1"

        def fake_fetch_json(url, *, params=None, headers=None, method="GET",
                            json=None, cfg=None, auth=None):
            seen["url"], seen["json"], seen["headers"] = url, json, headers
            return fx

        monkeypatch.setattr(wttj, "_fetch_env_creds", fake_env)
        monkeypatch.setattr(wttj, "fetch_json", fake_fetch_json)
        jobs = wttj.fetch("python", location="Remote", cfg=cfg)

        assert seen["url"] == "https://APPID1-dsn.algolia.net/1/indexes/wttj_jobs_production_en/query"
        assert seen["json"] == {"params": "query=python&hitsPerPage=50&page=0"}
        assert seen["headers"]["x-algolia-application-id"] == "APPID1"
        assert seen["headers"]["x-algolia-api-key"] == "apikey1"
        assert seen["headers"]["Referer"] == "https://www.welcometothejungle.com/"

        # hit-3 has empty title → skipped; hit-1 remote=yes kept;
        # hit-2 partial kept (remote search keeps partial).
        assert len(jobs) == 2
        j = jobs[0]
        assert j.title == "Senior Python Developer"
        assert j.company == "Alan"
        assert j.source == "WTTJ"
        assert j.remote is True
        assert j.date_posted == "2026-08-25"
        assert j.salary_text == "EUR 55,000 – 70,000"
        assert j.salary_min == 55000.0 and j.salary_max == 70000.0
        assert j.location == "Paris, France"
        assert j.link == ("https://www.welcometothejungle.com/fr/companies/"
                          "alan/jobs/senior-python-developer_paris")
        assert "pricing engine" in j.description
        assert "full-time" in j.description          # contract folded into desc

    def test_location_filter_specific_city(self, monkeypatch, cfg):
        fx = load_fixture("wttj.json")
        monkeypatch.setattr(wttj, "_fetch_env_creds", lambda force=False: ("A", "k"))
        monkeypatch.setattr(wttj, "fetch_json", lambda *a, **k: fx)
        jobs = wttj.fetch("python", location="Nantes", cfg=cfg)
        assert [j.title for j in jobs] == ["Software Engineer - Stage R&D - Nantes"]

    def test_remote_only_search_keeps_partial(self, monkeypatch, cfg):
        fx = load_fixture("wttj.json")
        monkeypatch.setattr(wttj, "_fetch_env_creds", lambda force=False: ("A", "k"))
        monkeypatch.setattr(wttj, "fetch_json", lambda *a, **k: fx)
        jobs = wttj.fetch("python", location="", cfg=cfg)
        # Remote search KEEPS partial-remote listings (EU reality) but flags
        # them remote=False — only fully-remote hits get remote=True.
        assert len(jobs) == 2
        assert jobs[0].remote is True           # hit-1 remote=yes
        assert jobs[1].remote is False          # hit-2 remote=partial, kept

    def test_parse_env_body(self):
        body = ('<script>window.env = {"PUBLIC_ALGOLIA_API_KEY_CLIENT":"abc",'
                '"PUBLIC_ALGOLIA_APPLICATION_ID":"XYZ"};</script>')
        assert wttj._parse_env_body(body) == ("XYZ", "abc")

    def test_parse_env_body_missing_keys_raises(self):
        with pytest.raises(RuntimeError, match="missing Algolia keys"):
            wttj._parse_env_body("window.env = {}")

    def test_cred_cache_ttl(self, monkeypatch):
        monkeypatch.setattr(wttj, "_FALLBACK_CREDS", ("FB", "fbk"))
        # env fetch fails (curl_cffi import error) → fallback creds used
        wttj._env_cache = None
        import builtins
        real_import = builtins.__import__

        def no_cffi(name, *a, **k):
            if name.startswith("curl_cffi"):
                raise ImportError(name)
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_cffi)
        app_id, key = wttj._fetch_env_creds()
        assert (app_id, key) == ("FB", "fbk")
        # cached now: second call within TTL returns cache without re-import
        app_id2, key2 = wttj._fetch_env_creds()
        assert (app_id2, key2) == ("FB", "fbk")

    def test_algolia_403_triggers_cred_refresh_and_retry(self, monkeypatch, cfg):
        fx = load_fixture("wttj.json")
        env_calls: list[bool] = []

        def fake_env(force=False):
            env_calls.append(force)
            return "A", "k"

        state = {"calls": 0}

        def fake_fetch_json(url, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                raise requests.HTTPError(response=_resp(403))
            return fx

        monkeypatch.setattr(wttj, "_fetch_env_creds", fake_env)
        monkeypatch.setattr(wttj, "fetch_json", fake_fetch_json)
        jobs = wttj.fetch("python", cfg=cfg)
        assert len(jobs) == 2
        assert state["calls"] == 2
        assert env_calls == [False, True]        # second call forced refresh

    @staticmethod
    def _algolia_hit(i: int, remote: str = "yes") -> dict:
        """Minimal valid WTTJ Algolia hit for pagination fixtures."""
        return {
            "objectID": f"pg-{i}",
            "name": f"Python Developer {i}",
            "slug": f"python-developer-{i}",
            "published_at_date": "2026-08-25",
            "contract_type": "full_time",
            "remote": remote,
            "offices": [{"city": "Paris", "country": "France",
                         "country_code": "FR"}],
            "organization": {"name": f"Co {i}", "slug": f"co-{i}"},
            "summary": "Build Python services.",
        }

    def test_pagination_loops_until_num_results(self, monkeypatch, cfg):
        """audit P2-3: page=0-only truncation fixed — the adapter pages
        (paced, capped) while client-side filters leave num_results unmet.

        Page 1 is a FULL page (50 hits = hits_per_page) of which only 10
        pass the remote filter, so a second page must be requested; the
        side-effect fake asserts the `page` param actually advances.
        """
        pages_seen: list[int] = []

        def fake_fetch_json(url, *, params=None, headers=None, method="GET",
                            json=None, cfg=None, auth=None):
            assert json and "params" in json
            page = int(re.search(r"page=(\d+)", json["params"]).group(1))
            pages_seen.append(page)
            if page == 0:
                hits = [self._algolia_hit(i, "yes" if i < 10 else "no")
                        for i in range(50)]
                return {"nbHits": 60, "page": 0, "nbPages": 2,
                        "hitsPerPage": 50, "hits": hits}
            hits = [self._algolia_hit(100 + i) for i in range(50)]
            return {"nbHits": 60, "page": 1, "nbPages": 2,
                    "hitsPerPage": 50, "hits": hits}

        monkeypatch.setattr(wttj, "_fetch_env_creds",
                            lambda force=False: ("A", "k"))
        monkeypatch.setattr(wttj, "fetch_json", fake_fetch_json)
        monkeypatch.setattr(wttj.time, "sleep", lambda s: None)
        jobs = wttj.fetch("python", location="Remote", num_results=25,
                          cfg=cfg)

        assert len(jobs) == 25               # num_results satisfied (> 20)
        assert pages_seen == [0, 1]          # page param actually advanced
        assert all(j.remote for j in jobs)   # only remote-filtered rows kept

    def test_pagination_truncation_safe_when_exhausted(self, monkeypatch, cfg):
        """audit P2-3: fewer available than requested → return what exists,
        no error, and no extra requests once Algolia signals the last page."""
        pages_seen: list[int] = []

        def fake_fetch_json(url, *, params=None, headers=None, method="GET",
                            json=None, cfg=None, auth=None):
            page = int(re.search(r"page=(\d+)", json["params"]).group(1))
            pages_seen.append(page)
            return {"nbHits": 10, "page": 0, "nbPages": 1,
                    "hitsPerPage": 50,
                    "hits": [self._algolia_hit(i) for i in range(10)]}

        monkeypatch.setattr(wttj, "_fetch_env_creds",
                            lambda force=False: ("A", "k"))
        monkeypatch.setattr(wttj, "fetch_json", fake_fetch_json)
        jobs = wttj.fetch("python", location="Remote", num_results=25,
                          cfg=cfg)
        assert len(jobs) == 10               # everything the index had
        assert pages_seen == [0]             # nbPages=1 → no second request


# ── Ashby (HTML board) ───────────────────────────────────────────────────────

def _board_html(app_data: dict, pad_to_bytes: int = 0) -> str:
    html = ("<html><head><title>Jobs</title></head><body>"
            f"<script>window.__appData = {json.dumps(app_data)};</script>"
            "</body></html>")
    if pad_to_bytes and len(html) < pad_to_bytes:
        html += f"<!-- {'x' * (pad_to_bytes - len(html) - 10)} -->"
    return html


class TestAshbyHTML:
    def test_extract_app_data_brace_matches(self):
        app = load_fixture("ashby_board.json")
        html = _board_html(app)
        out = ashby._extract_app_data(html)
        assert out is not None
        assert out["organization"]["name"] == "Acme Cloud"
        assert len(out["jobBoard"]["jobPostings"]) == 3

    def test_extract_app_data_nested_strings_with_braces(self):
        # JSON strings containing braces must not break the brace matcher.
        html = ('<script>window.__appData = {"a": "literal } brace", '
                '"jobBoard": {"jobPostings": []}};</script>')
        out = ashby._extract_app_data(html)
        assert out is not None
        assert out["a"] == "literal } brace"

    def test_posting_to_job_mapping(self):
        app = load_fixture("ashby_board.json")
        teams = {t["id"]: t["name"] for t in app["jobBoard"]["teams"]}
        job = ashby._posting_to_job(
            app["jobBoard"]["jobPostings"][0], "acme",
            app["organization"]["name"], teams, "python", "Remote")
        assert job is not None
        assert job.title == "Senior Python Engineer"
        assert job.company == "Acme Cloud"
        assert job.source == "Ashby.acme"
        assert job.remote is True                    # workplaceType=Remote
        assert job.location == "San Francisco"
        assert job.salary_text == "$180K – $220K • Offers Equity"
        assert job.salary_min == 180000.0
        assert job.salary_max == 220000.0
        assert job.link == ("https://jobs.ashbyhq.com/acme/"
                            "11111111-2222-3333-4444-555555555555")
        assert "Engineering" in job.description

    def test_posting_without_title_returns_none(self):
        app = load_fixture("ashby_board.json")
        teams = {t["id"]: t["name"] for t in app["jobBoard"]["teams"]}
        assert ashby._posting_to_job(
            app["jobBoard"]["jobPostings"][2], "acme", "Acme",
            teams, "k", "Remote") is None

    def test_salary_with_bonus_token_uses_range_endpoints(self):
        """CodeRabbit round-3: '$100K - $150K plus $20K bonus' must map to
        (100000, 150000) — the third $-token must not overwrite/corrupt
        the range (bonus detail stays in salary_text)."""
        posting = {"title": "Engineer", "id": "x", "workplaceType": "Remote",
                   "compensationTierSummary": "$100K - $150K plus $20K bonus"}
        job = ashby._posting_to_job(posting, "acme", "Acme", {}, "k", "Remote")
        assert job is not None
        assert job.salary_min == 100000.0
        assert job.salary_max == 150000.0
        assert job.salary_text == "$100K - $150K plus $20K bonus"

    def test_salary_single_amount_is_min_and_max(self):
        posting = {"title": "Engineer", "id": "x", "workplaceType": "Remote",
                   "compensationTierSummary": "$120,000"}
        job = ashby._posting_to_job(posting, "acme", "Acme", {}, "k", "Remote")
        assert job is not None
        assert job.salary_min == 120000.0
        assert job.salary_max == 120000.0

    def test_salary_reversed_range_is_normalized(self):
        posting = {"title": "Engineer", "id": "x", "workplaceType": "Remote",
                   "compensationTierSummary": "$90K - $60K"}
        job = ashby._posting_to_job(posting, "acme", "Acme", {}, "k", "Remote")
        assert job is not None
        assert job.salary_min == 60000.0
        assert job.salary_max == 90000.0

    def test_location_filter_on_site_search(self):
        app = load_fixture("ashby_board.json")
        teams = {t["id"]: t["name"] for t in app["jobBoard"]["teams"]}
        job = ashby._posting_to_job(
            app["jobBoard"]["jobPostings"][1], "acme", "Acme",
            teams, "k", "New York")
        assert job is not None                      # locationName matches
        job2 = ashby._posting_to_job(
            app["jobBoard"]["jobPostings"][1], "acme", "Acme",
            teams, "k", "Berlin")
        assert job2 is None

    def test_fetch_org_skips_small_board_shell(self, monkeypatch, cfg):
        monkeypatch.setattr(ashby, "fetch_text",
                            lambda url, **k: "<html>tiny 7.3KB shell</html>")
        assert ashby._fetch_org("ghostco", "python", "Remote", 10, cfg) == []

    def test_fetch_org_parses_board(self, monkeypatch, cfg):
        app = load_fixture("ashby_board.json")
        html = _board_html(app, pad_to_bytes=ashby._MIN_BOARD_BYTES + 100)
        monkeypatch.setattr(ashby, "fetch_text", lambda url, **k: html)
        jobs = ashby._fetch_org("acme", "python", "Remote", 10, cfg)
        # Remote search keeps only workplaceType=Remote postings → 1 of 2.
        assert [j.title for j in jobs] == ["Senior Python Engineer"]

    def test_fetch_org_404_is_empty_not_error(self, monkeypatch, cfg):
        def raise_404(url, **k):
            raise requests.HTTPError(response=_resp(404))
        monkeypatch.setattr(ashby, "fetch_text", raise_404)
        assert ashby._fetch_org("nonexistent", "python", "Remote", 10, cfg) == []

    def test_fetch_aggregates_orgs_with_failure_isolation(self, monkeypatch, cfg):
        app = load_fixture("ashby_board.json")
        html = _board_html(app, pad_to_bytes=ashby._MIN_BOARD_BYTES + 100)

        def fake_fetch_text(url, **k):
            if "linear" in url:
                raise requests.HTTPError(response=_resp(500))
            return html

        monkeypatch.setattr(ashby, "fetch_text", fake_fetch_text)
        cfg.ashby_orgs = ["acme", "linear"]
        jobs = ashby.fetch("engineer", location="", cfg=cfg)
        # linear's 500 is isolated; acme's board still contributes.
        assert all(j.source == "Ashby.acme" for j in jobs)
        assert jobs

    def test_keyword_filter_falls_back_when_zero_matches(self, monkeypatch, cfg):
        app = load_fixture("ashby_board.json")
        html = _board_html(app, pad_to_bytes=ashby._MIN_BOARD_BYTES + 100)
        monkeypatch.setattr(ashby, "fetch_text", lambda url, **k: html)
        cfg.ashby_orgs = ["acme"]
        jobs = ashby.fetch("cobol", location="", cfg=cfg)
        # No cobol matches → fallback to unfiltered board (caller ranks).
        assert jobs

    def test_default_orgs_curated_verified_list(self, tmp_path):
        cfg = Config(db_path=tmp_path / "t.db")
        assert cfg.ashby_orgs == []              # env override empty by default
        assert set(ashby._DEFAULT_ORGS) >= {"openai", "notion", "linear"}


# ── Personio ─────────────────────────────────────────────────────────────────

PERSONIO_XML = (SOURCE_FIXTURES / "personio.xml").read_text(encoding="utf-8")
PERSONIO_HTML = """
<html><body>
<a href="/job/36748?language=en">(Senior) Site Reliability Engineer (m/w/d)</a>
<a href="/job/586322?language=en">Product Manager Platform (m/w/d)</a>
</body></html>
"""


class TestPersonio:
    def setup_method(self):
        # Reset pacing state between tests so sleeps don't cascade.
        personio._last_request_ts = 0.0

    def test_is_configured_always(self, cfg):
        assert personio.is_configured(cfg) is True

    def test_xml_to_jobs_parses_fixture(self):
        jobs = personio._xml_to_jobs(PERSONIO_XML, "kiwigrid", "python")
        assert len(jobs) == 2
        j = jobs[0]
        assert j.title == "(Senior) Site Reliability Engineer (m/w/d)"
        assert j.company == "Kiwigrid"
        assert j.source == "Personio.kiwigrid"
        assert j.location == "Dresden"
        assert j.date_posted == "2026-08-20"
        assert j.link == ("https://kiwigrid.jobs.personio.de/job/36748"
                          "?language=en")
        assert "Site Reliability Engineer" in j.description
        assert j.remote is False
        assert jobs[1].remote is True              # keywords contain remote
        assert jobs[1].location == "Remote"

    def test_html_to_jobs_parses_links(self):
        jobs = personio._html_to_jobs(PERSONIO_HTML, "kiwigrid", "k")
        assert len(jobs) == 2
        assert jobs[0].title == ("(Senior) Site Reliability Engineer (m/w/d)")
        assert jobs[0].link == ("https://kiwigrid.jobs.personio.de/job/36748"
                                "?language=en")

    def test_fetch_uses_xml_primary(self, monkeypatch, cfg):
        urls: list[str] = []

        def fake_fetch_text(url, **k):
            urls.append(url)
            if url.endswith("/xml"):
                return PERSONIO_XML
            raise AssertionError("HTML fallback should not fire when XML works")

        monkeypatch.setattr(personio, "fetch_text", fake_fetch_text)
        cfg.personio_slugs = ["kiwigrid"]
        jobs = personio.fetch("reliability", location="", cfg=cfg)
        assert urls == ["https://kiwigrid.jobs.personio.de/xml"]
        # keyword filter: only the SRE job matches "reliability"
        assert [j.title for j in jobs] == ["(Senior) Site Reliability Engineer (m/w/d)"]

    def test_fetch_falls_back_to_html_on_404(self, monkeypatch, cfg):
        urls: list[str] = []

        def fake_fetch_text(url, **k):
            urls.append(url)
            if url.endswith("/xml"):
                raise requests.HTTPError(response=_resp(404))
            return PERSONIO_HTML

        monkeypatch.setattr(personio, "fetch_text", fake_fetch_text)
        cfg.personio_slugs = ["kiwigrid"]
        jobs = personio.fetch("reliability", location="", cfg=cfg)
        assert urls == ["https://kiwigrid.jobs.personio.de/xml",
                        "https://kiwigrid.jobs.personio.de/?language=en"]
        assert [j.title for j in jobs] == ["(Senior) Site Reliability Engineer (m/w/d)"]

    def test_fetch_429_reports_rate_limited(self, monkeypatch, cfg):
        def raise_429(url, **k):
            raise requests.HTTPError(response=_resp(429))

        monkeypatch.setattr(personio, "fetch_text", raise_429)
        cfg.personio_slugs = ["kiwigrid"]
        with pytest.raises(RuntimeError, match="429|spacing"):
            personio.fetch("engineer", cfg=cfg)

    def test_pacer_enforces_global_spacing(self, monkeypatch):
        # Fast-forward time so no real sleeping happens in tests.
        clock = {"now": 1000.0}
        monkeypatch.setattr(personio.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(personio.time, "sleep",
                            lambda s: clock.__setitem__("now", clock["now"] + s))
        personio._last_request_ts = 0.0
        with personio._Pacer():
            pass                                    # first call: no wait
        assert personio.time.monotonic() == 1000.0
        clock["now"] = 1010.0                       # only 10s elapsed
        with personio._Pacer():
            pass                                    # must wait 15 more
        assert personio.time.monotonic() == 1025.0  # 10 + 15 = 25s spacing

    def test_default_slugs(self, tmp_path):
        cfg = Config(db_path=tmp_path / "t.db")
        assert cfg.personio_slugs == []
        assert set(personio._DEFAULT_SLUGS) >= {"personio", "kiwigrid"}
