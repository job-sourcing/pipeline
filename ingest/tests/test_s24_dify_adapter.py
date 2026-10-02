"""S24 pins — the Dify (LangGenius) custom adapter.

Contract (live-verified 2026-10-02): join.dify.ai/roles.html embeds
GET https://<ref>.supabase.co/functions/v1/public-jobs?lang=en;
jobs carry id/title/department/location/work_site/employment_type/
content/benefits/created_at. Location dialect (the page's own
getCountries()): ' / ' segments; comma-segments are 'Country, City'
ONLY when at least one segment has a comma; Remote/Hybrid never yield
countries. US rule: a country segment equals USA/UNITED STATES.

Pinned here: the location-classification table (the dialect's tricky
part), the row mapping, and the honest empty-US behavior.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "ingest"))

from jobsearch.config import Config  # noqa: E402
from jobsearch.sources import site_boards  # noqa: E402


def _adapter():
    a = site_boards.DifyAdapter("", Config())
    a._jobs = lambda: []      # no network in unit pins
    return a


class TestCountriesDialect:
    """The location → countries table, replicated from the page's own
    getCountries() comments (the S24 RE notes)."""

    def _c(self, loc):
        return site_boards.DifyAdapter._countries(loc)

    def test_china_city_slash_city(self):
        # comma-segments are Country, City; bare Shanghai = city → skipped
        assert self._c("China, Suzhou / Shanghai") == ["China"]

    def test_multi_country_with_commas(self):
        assert self._c("China, Suzhou / USA, Sunnyvale") == ["China", "USA"]

    def test_bare_countries_no_comma(self):
        assert self._c("Singapore / Malaysia / Remote") == ["Singapore",
                                                            "Malaysia"]

    def test_remote_only_yields_other(self):
        assert self._c("Remote") == ["Other"]
        assert self._c("Remote / Hybrid") == ["Other"]

    def test_empty_is_other(self):
        assert self._c("") == ["Other"]

    def test_dedup(self):
        assert self._c("USA, NYC / USA, SF") == ["USA"]


class TestRowMapping:
    def _a(self):
        a = _adapter()
        a._jobs = lambda: [{
            "id": "abc-123", "title": "SRE",
            "department": "Infra", "location": "USA, Sunnyvale",
            "employment_type": "Full-time", "work_site": "on-site",
            "content": "<p>Keep the <b>lights</b> on.</p>",
            "created_at": "2026-09-28T10:00:00Z"}]
        return a

    def test_us_row_shape(self):
        rows, meta = self._a().list_board(country="United States")
        assert meta["complete"] is True and meta["total"] == 1
        r = rows["abc-123"]
        assert r["title"] == "SRE"
        assert r["company"] == "Dify (LangGenius)"
        assert r["locationsText"] == "USA, Sunnyvale"
        assert r["timeType"] == "Full-time"
        assert r["url"] == "https://join.dify.ai/roles.html"
        assert r["bulletFields"] == ["abc-123"]

    def test_china_rows_dropped_for_us_filter(self):
        a = self._a()
        a._jobs = lambda: [{
            "id": "cn-1", "title": "后端开发实习生",
            "location": "China, Suzhou / Shanghai",
            "content": "x", "created_at": ""}]
        rows, meta = a.list_board(country="United States")
        assert rows == {}
        assert meta["total"] == 1 and meta["client_filtered"] == 1

    def test_html_stripped_from_description(self):
        a = self._a()
        rows, _ = a.list_board(country="United States")
        assert "<" not in rows["abc-123"]["description"] \
            if "description" in rows["abc-123"] else True
        d = a.detail_payload("/abc-123")
        assert "lights" in d["jobPostingInfo"]["jobDescription"]
        assert "<b>" not in d["jobPostingInfo"]["jobDescription"]

    def test_detail_country_descriptor(self):
        d = self._a().detail_payload("/abc-123")
        assert d["jobPostingInfo"]["country"]["descriptor"] == "US"
        assert d["jobPostingInfo"]["jobReqId"] == "abc-123"
        assert d["hiringOrganization"]["name"] == "Dify (LangGenius)"

    def test_detail_unknown_rid_is_none(self):
        assert self._a().detail_payload("/nope") is None

    def test_fallback_rid_when_no_id(self):
        a = self._a()
        a._jobs = lambda: [{"title": "Role!", "location": "USA, NYC",
                            "content": "x"}]
        rows, _ = a.list_board(country="United States")
        assert list(rows) == ["role"]


class TestRegistration:
    def test_custom_dify_registered(self):
        assert "dify" in site_boards._ADAPTERS

    def test_spec_routes(self):
        assert site_boards.is_site_spec("custom:dify")
