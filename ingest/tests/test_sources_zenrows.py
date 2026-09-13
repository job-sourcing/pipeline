"""Tests for the Step-C ZenRows-transport adapters (ZipRecruiter, Glassdoor)
and the zenrows_fetch_text transport variant — offline, no credits burned.
"""
from __future__ import annotations

import pytest
import requests

from jobsearch.config import Config
from jobsearch.sources import glassdoor, ziprecruiter
from jobsearch import transport


def _resp(status: int) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    return r


ZIPPY_HTML = """
<html><body>
<article class="job-card">
  <h2>Share this job</h2>
  <h2>Senior Python Developer</h2>
  <p><a>Acme Corp</a></p>
  <span data-testid="job-card-location">Remote</span>
  <span>$120,000 - $150,000 / yr</span>
  <span>Posted 3 days ago</span>
  <a href="/jobs/senior-python-developer-at-acme">apply</a>
</article>
<article class="job-card">
  <h2>Senior Python Developer</h2>
  <p><a>Acme Corp</a></p>
  <span data-testid="job-card-location">Austin, TX</span>
  <a href="/jobs/dup">apply</a>
</article>
<article class="job-card">
  <h2>Backend Engineer</h2>
  <p><a>Globex</a></p>
  <span data-testid="job-card-location">New York, NY</span>
  <span>$95K - $120K / yr</span>
  <span>Posted today</span>
  <a href="/jobs/backend-engineer">apply</a>
</article>
</body></html>
"""

GLASS_JSONLD = """
<script type="application/ld+json">{
  "@type": "ItemList",
  "itemListElement": [
    {"@type": "ListItem", "position": 1, "name": "Data Engineer",
     "url": "https://www.glassdoor.com/job-listing/data-engineer-at-Acme-Corp-Jobs-123456789.htm"},
    {"@type": "ListItem", "position": 2, "name": "ML Engineer",
     "url": "https://www.glassdoor.com/job-listing/ml-engineer-at-Globex-Jobs-987654321.htm"},
    {"@type": "ListItem", "position": 3, "name": "Data Engineer",
     "url": "https://www.glassdoor.com/job-listing/data-engineer-at-Acme-Corp-Jobs-123456789.htm"}
  ]
}</script>
"""


class TestZipRecruiter:
    def test_not_configured_without_zenrows(self, cfg):
        assert ziprecruiter.is_configured(cfg) is False
        with pytest.raises(RuntimeError, match="ZENROWS_API_KEY"):
            ziprecruiter.fetch("python", cfg=cfg)

    def test_parse_articles(self):
        jobs = ziprecruiter._parse_articles(ZIPPY_HTML, "python")
        # promo h2 skipped; Acme duplicate deduped; 2 unique jobs.
        assert len(jobs) == 2
        j = jobs[0]
        assert j.title == "Senior Python Developer"
        assert j.company == "Acme Corp"
        assert j.location == "Remote"
        assert j.remote is True
        assert j.salary_text == "$120,000 - $150,000 / yr"
        assert j.salary_min == 120000.0
        assert j.salary_max == 150000.0
        assert j.link == ("https://www.ziprecruiter.com/jobs/"
                          "senior-python-developer-at-acme")
        assert j.source == "ZipRecruiter"

    def test_posted_to_date(self):
        assert ziprecruiter._posted_to_date("Posted today")
        assert ziprecruiter._posted_to_date("Posted yesterday")
        assert ziprecruiter._posted_to_date("Posted 3 days ago")
        assert ziprecruiter._posted_to_date("weird") == ""

    def test_fetch_uses_transport(self, monkeypatch, cfg):
        seen: dict = {}

        def fake_zenrows_text(url, *, headers=None, cfg=None,
                              proxy_country="us", wait_ms=4000, timeout=None):
            seen["url"] = url
            seen["proxy_country"] = proxy_country
            return ZIPPY_HTML

        monkeypatch.setattr(ziprecruiter, "zenrows_fetch_text", fake_zenrows_text)
        cfg.zenrows_api_key = "zr"
        jobs = ziprecruiter.fetch("python dev", location="Remote", cfg=cfg)
        assert "ziprecruiter.com/jobs-search" in seen["url"]
        assert "search=python+dev" in seen["url"]
        assert seen["proxy_country"] == "us"
        assert len(jobs) == 2

    def test_date_filter_filters_stale_and_keeps_fresh(self, monkeypatch, cfg):
        monkeypatch.setattr(ziprecruiter, "zenrows_fetch_text",
                            lambda *a, **k: ZIPPY_HTML)
        cfg.zenrows_api_key = "zr"
        jobs = ziprecruiter.fetch("python", cfg=cfg, date_filter=1)
        # "3 days ago" Acme job dropped; "today" Backend Engineer kept.
        assert [j.title for j in jobs] == ["Backend Engineer"]
        # No date filter: everything comes back.
        jobs_all = ziprecruiter.fetch("python", cfg=cfg)
        assert {j.title for j in jobs_all} == {
            "Senior Python Developer", "Backend Engineer"}

    def test_k_suffix_salary_multiplier(self):
        html = ZIPPY_HTML.replace(
            "$120,000 - $150,000 / yr", "$120K - $150K / yr")
        jobs = ziprecruiter._parse_articles(html, "python")
        assert jobs[0].salary_min == 120_000.0
        assert jobs[0].salary_max == 150_000.0


class TestGlassdoor:
    def test_not_configured_without_zenrows(self, cfg):
        assert glassdoor.is_configured(cfg) is False
        with pytest.raises(RuntimeError, match="ZENROWS_API_KEY"):
            glassdoor.fetch("python", cfg=cfg)

    def test_extract_jsonld(self):
        data = glassdoor._extract_jsonld_itemlist(GLASS_JSONLD)
        assert data is not None
        assert data["@type"] == "ItemList"
        assert len(data["itemListElement"]) == 3

    def test_extract_jsonld_missing_returns_none(self):
        assert glassdoor._extract_jsonld_itemlist("<html>no jsonld</html>") is None

    def test_items_to_jobs_dedupes_and_extracts_company(self):
        data = glassdoor._extract_jsonld_itemlist(GLASS_JSONLD)
        jobs = glassdoor._items_to_jobs(data, "data", "Remote")
        assert len(jobs) == 2                     # duplicate URL dropped
        assert jobs[0].title == "Data Engineer"
        assert jobs[0].company == "Acme Corp"     # from -at-...-Jobs slug
        assert jobs[1].company == "Globex"
        assert jobs[0].source == "Glassdoor"

    def test_fetch_returns_empty_on_challenge_page(self, monkeypatch, cfg):
        monkeypatch.setattr(glassdoor, "zenrows_fetch_text",
                            lambda *a, **k: "<html>challenge page</html>")
        cfg.zenrows_api_key = "zr"
        assert glassdoor.fetch("python", cfg=cfg) == []


class TestZenrowsFetchText:
    def test_request_shape(self, monkeypatch):
        seen: dict = {}

        class FakeR:
            status_code = 200
            text = "<html>ok</html>"

            def raise_for_status(self):
                pass

        def fake_get(url, *, params=None, headers=None, timeout=None):
            seen.update(url=url, params=params, timeout=timeout)
            return FakeR()

        monkeypatch.setattr(transport.requests, "get", fake_get)
        cfg = Config()
        cfg.zenrows_api_key = "zr-key"
        cfg.http_timeout_s = 30
        out = transport.zenrows_fetch_text(
            "https://www.ziprecruiter.com/jobs-search?search=x", cfg=cfg,
            proxy_country="us", wait_ms=4000)

        assert out == "<html>ok</html>"
        assert seen["params"]["js_render"] == "true"
        assert seen["params"]["premium_proxy"] == "true"
        assert seen["params"]["proxy_country"] == "us"
        assert seen["params"]["wait"] == "4000"
        assert seen["params"]["original_status"] == "true"
        assert seen["timeout"] >= 120

    def test_not_configured_raises(self):
        with pytest.raises(RuntimeError, match="ZENROWS_API_KEY"):
            transport.zenrows_fetch_text("https://example.com")
