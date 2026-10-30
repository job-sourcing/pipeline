"""S33 — the GoogleJobsAdapter (custom:gjobs:{key}) pins.

Hermetic: _scrape is monkeypatched / the scrape cache is injected —
no firecrawl spend, no network. The fixtures reproduce BOTH live
card shapes (v2 expanded + v1 compact, 2026-10-13 evidence,
audit/s33_gj/probe2_*.md) including the homonym-noise walls that
make the variant guard the board's identity defense.
"""
from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobsearch.config import Config                     # noqa: E402
from jobsearch.sources import site_boards               # noqa: E402
from jobsearch.sources.site_boards import (             # noqa: E402
    GJOBS_CONTRACTS, GoogleJobsAdapter, _gj_age_iso,
    is_site_spec, parse_site)


@pytest.fixture(autouse=True)
def _clear_gj_cache():
    _keys = [k for k in site_boards._GJ_SCRAPE_CACHE]
    site_boards._GJ_SCRAPE_CACHE.clear()
    yield
    site_boards._GJ_SCRAPE_CACHE.clear()


def _expanded_card(title: str, company: str, loc: str, via: str,
                   age: str = "3 days ago", tt: str = "Full-time",
                   jd: str = "Renogy is seeking an engineer.",
                   ) -> str:
    """The v2 firecrawl shape: title / company / 'loc • via X' /
    share-widget junk / age / tt / [Apply] / JD section."""
    return (
        f"![](<Base64-Image-Removed>)\n\n{title}\n\n{company}\n\n"
        f"{loc} • via {via}\n\n# Share\n\nCopy link\n\nFacebook\n\n"
        f"Gmail\n\nX\n\nReddit\n\nWhatsApp\n\n{age}\n\n{tt}\n\n"
        f"[Apply on {via}](https://www.google.com/x)\n\n"
        f"## Job description\n\n{jd}\n\n* * *\n")


def _compact_card(title: str, company: str, loc: str, via: str,
                  age: str = "5 days ago", tt: str = "Full-time",
                  ) -> str:
    """The v1 firecrawl shape (compact, \\xa0 separators observed on
    FR-locale pages): title / 'company · loc · via X' / age / tt."""
    return (f"{title}\n\n{company}\xa0·\xa0{loc}\xa0·\xa0via {via}\n\n"
            f"{age}\n\n{tt}\n")


def _page(*cards: str) -> str:
    head = ("Saved jobs\n\nFollowing\n\nJob postings\n\n")
    return head + "".join(cards)


def _adapter(key: str = "renogy", md: str | None = None,
             calls: list | None = None):
    a = GoogleJobsAdapter(key, Config())

    def fake_scrape():
        if calls is not None:
            calls.append(1)
        assert md is not None
        return md
    a._scrape = fake_scrape
    return a


class TestContracts:
    def test_13_contracts_four_tiers(self):
        # S33 lanes: 4 weekly + 2 monthly dormant
        # S35 tiering (audit/s35_aggregator/gj_gap_probe.md):
        #   +2 monthly REAL (entity-verified; torras excluded — wrong
        #   entity) +5 quarterly NOISE-WALL (0->k flip re-probe class)
        assert len(GJOBS_CONTRACTS) == 13
        assert set(GJOBS_CONTRACTS) == {
            "renogy", "jinkosolar", "eveenergy", "jackery",
            "govee", "vava",
            "centurygames", "skyworth",
            "baseus", "lianlian", "notta", "timekettle", "xgimi"}

    def test_every_contract_shape(self):
        for key, c in GJOBS_CONTRACTS.items():
            assert c["company"] and c["query"], key
            assert c["g_variants"] and all(
                v.strip() for v in c["g_variants"]), key

    def test_jinko_multi_entity_variants_pinned_exactly(self):
        # the measured US-entity strings (probe2, 2026-10-13)
        assert GJOBS_CONTRACTS["jinkosolar"]["g_variants"] == [
            "jinkosolar", "jinko solar u.s. industries, inc.",
            "jinko solar u.s. industries", "jinko solar co."]

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match="unknown contract"):
            GoogleJobsAdapter("nonexistent", Config())


class TestDispatch:
    def test_spec_routes(self):
        assert is_site_spec("custom:gjobs:renogy")
        assert parse_site("custom:gjobs:renogy") == ("gjobs", "renogy")


class TestParser:
    def test_expanded_card_fields(self):
        a = _adapter(md=_page(_expanded_card(
            "Technical Support Engineer", "Renogy",
            "Ontario, CA", "Trabajo.org", age="19 days ago")))
        rows, meta = a.list_board()
        assert len(rows) == 1 and meta["complete"] is True
        r = next(iter(rows.values()))
        assert r["title"] == "Technical Support Engineer"
        assert r["company"] == "Renogy"
        assert r["locationsText"] == "Ontario, CA"
        assert r["timeType"] == "Full time"
        assert r["postedOn"] == "Posted 19 Days Ago"
        assert r["url"].startswith(
            "https://www.google.com/search?q=")
        assert r["externalPath"].startswith("/")

    def test_compact_shape_v1_fr_tolerant(self):
        # \xa0 separators + no age line (the FR-locale evidence shape)
        a = _adapter(md=_page(
            "Solar Sales Manager\n\nRenogy\xa0·\xa0Montclair, CA"
            "\xa0·\xa0via Terra.do\n\nÀ plein temps\n"))
        # 'À plein temps' is NOT a pinned tt — best-effort, ships ''
        rows, _ = a.list_board()
        assert len(rows) == 1
        r = next(iter(rows.values()))
        assert r["title"] == "Solar Sales Manager"
        assert r["postedOn"] == ""          # honest blank (no age line)
        assert r["timeType"] == ""          # localized tt not guessed

    def test_double_print_dedupe_and_jd_window(self):
        # the LIVE layout (probe2 positions 12881/13168/16444): the
        # expanded card header, then the compact double-print, THEN
        # the JD section — the window must span the double-print.
        md = (
            "Job postings\n\n"
            "![](<Base64-Image-Removed>)\n\n"
            "Engineer\n\nRenogy\n\n"
            "Ontario, CA • via Trabajo.org\n\n"
            "# Share\n\nCopy link\n\nWhatsApp\n\n"
            "2 days ago\n\nFull-time\n\n"
            "[Apply on Trabajo.org](https://g/x)\n\n"
            "Engineer\n\nRenogy\xa0·\xa0Ontario, CA\xa0·\xa0"
            "via Trabajo.org\n\n2 days ago\n\nFull-time\n\n"
            "## Job description\n\nYou will build things.\n\n"
            "* * *\n\nNEXT CARD\n\nOther Co\n\n"
            "Remote • via BeBee\n\n1 day ago\n\nFull-time\n\n")
        a = _adapter(md=md)
        rows, _ = a.list_board()
        assert len(rows) == 1            # double print deduped; noise
        rid = next(iter(rows))           # card (Other Co) dropped
        d = a.detail_payload(f"/{rid}")
        jd = d["jobPostingInfo"]["jobDescription"]
        assert "You will build things." in jd
        assert "Job description" not in jd   # heading line dropped
        assert "NEXT CARD" not in jd

    def test_rid_stability(self):
        md = _page(_expanded_card("Engineer", "Renogy",
                                  "Ontario, CA", "Trabajo.org"))
        r1 = next(iter(_adapter(md=md).list_board()[0]))
        r2 = next(iter(_adapter(md=md).list_board()[0]))
        assert r1 == r2 and len(r1) == 16


class TestVariantGuard:
    def test_homonym_noise_dropped_honest_zero(self):
        # the measured VAVA wall: 86 virtual-assistant noise cards →
        # a COMPLETE 0-row board (the licompany asymmetry: Google
        # ALWAYS serves noise; 0 matched is the dormant verdict)
        md = _page(
            _expanded_card("Virtual Assistant", "VaVa Virtual Assist",
                           "Remote", "BeBee"),
            _expanded_card("Executive Assistant", "Vaco",
                           "Tampa, FL", "BeBee"))
        a = _adapter(key="vava", md=md)
        rows, meta = a.list_board()
        assert rows == {} and meta["complete"] is True

    def test_exact_match_not_substring(self):
        # "US Government Jobs" must NOT match govee
        md = _page(_expanded_card("Program Analyst",
                                  "US Government Jobs",
                                  "Seattle, WA", "Careers In Government"))
        a = _adapter(key="govee", md=md)
        rows, meta = a.list_board()
        assert rows == {} and meta["complete"] is True

    def test_multi_entity_variants_all_match(self):
        md = _page(
            _expanded_card("MES Engineer", "JinkoSolar",
                           "Jacksonville, FL", "Talents By Vaia"),
            _expanded_card("PQE Supervisor",
                           "JINKO SOLAR U.S. INDUSTRIES, Inc.",
                           "Jacksonville, FL", "Talents By Vaia"),
            _expanded_card("SQE", "Jinko Solar Co.", "Kentucky",
                           "BeBee"))
        a = _adapter(key="jinkosolar", md=md)
        rows, _ = a.list_board()
        assert len(rows) == 3


class TestHonestyDoctrine:
    def test_cardless_small_page_is_blocked_not_terminal(self):
        a = _adapter(md="Passer au contenu principal\n" + " " * 500)
        rows, meta = a.list_board()
        assert rows == {} and meta["complete"] is False

    def test_scrape_failure_raises_loudly(self):
        a = GoogleJobsAdapter("renogy", Config())
        a._scrape = lambda: (_ for _ in ()).throw(
            RuntimeError("firecrawl scrape failed"))
        with pytest.raises(RuntimeError, match="firecrawl"):
            a.list_board()

    def test_missing_key_raises(self):
        a = GoogleJobsAdapter("renogy", Config())
        with patch.object(site_boards, "_firecrawl_key",
                          return_value=""):
            with pytest.raises(RuntimeError, match="FIRECRAWL_API_KEY"):
                a._scrape()

    def test_firecrawl_success_false_raises(self):
        # [S51] the seam moved: _scrape calls vendor_relay.fc_api (the
        # relay transport) — pin the NEW seam, not urllib.urlopen
        a = GoogleJobsAdapter("renogy", Config())
        from jobsearch import vendor_relay as _vr
        with patch.object(_vr, "fc_api",
                          return_value=({"success": False,
                                         "error": "boom"}, "ok")):
            with pytest.raises(RuntimeError, match="success=false"):
                a._scrape()

    def test_relay_transport_death_raises_loudly(self):
        # [S51] a dead relay must surface as a scrape failure (never a
        # silent direct fallback — the owner IP-hygiene directive)
        a = GoogleJobsAdapter("renogy", Config())
        from jobsearch import vendor_relay as _vr
        with patch.object(_vr, "fc_api", return_value=(None,
                                                       "relay:wrapper-http401")):
            with pytest.raises(RuntimeError, match="relay"):
                a._scrape()

    def test_empty_markdown_raises(self):
        # [S51] seam-moved twin of the success=false pin
        a = GoogleJobsAdapter("renogy", Config())
        from jobsearch import vendor_relay as _vr
        with patch.object(_vr, "fc_api",
                          return_value=({"success": True,
                                         "data": {"markdown": ""}}, "ok")):
            with pytest.raises(RuntimeError, match="EMPTY"):
                a._scrape()


class TestCreditDiscipline:
    def test_one_scrape_per_run(self):
        # list + many details = exactly ONE firecrawl credit
        calls: list = []
        md = _page(*[
            _expanded_card(f"Engineer {i}", "Renogy", "Ontario, CA",
                           "Trabajo.org")
            for i in range(5)])
        a = _adapter(md=md, calls=calls)
        rows, _ = a.list_board()
        for rid in rows:
            a.detail_payload(f"/{rid}")
            a.detail_payload(f"/{rid}")     # repeat = still cached
        assert calls == [1]

    def test_cache_ttl_outlives_a_run(self):
        assert site_boards._GJ_TTL >= 6 * 3600


class TestAgeIso:
    def test_day_math(self):
        assert _gj_age_iso("19 days ago") == (
            date.today() - timedelta(days=19)).isoformat()

    def test_week_month(self):
        assert _gj_age_iso("2 weeks ago") == (
            date.today() - timedelta(days=14)).isoformat()
        assert _gj_age_iso("1 month ago") == (
            date.today() - timedelta(days=30)).isoformat()

    def test_hours_and_just_now_today(self):
        assert _gj_age_iso("3 hours ago") == date.today().isoformat()
        assert _gj_age_iso("just now") == date.today().isoformat()

    def test_blank_and_garbage(self):
        assert _gj_age_iso("") == ""
        assert _gj_age_iso("yesterday-ish") == ""


class TestDetailPayload:
    def _live_like_page(self):
        return _page(_expanded_card(
            "Customer Success Manager", "Renogy", "Elkhart, IN",
            "Terra.do", age="4 days ago",
            jd="Renogy is hiring a CSM based in Elkhart. This is an "
               "on-site role.\n\n## More info\n\ntail junk"))

    def test_shape_mirrors_licompany(self):
        a = _adapter(md=self._live_like_page())
        rows, _ = a.list_board()
        rid = next(iter(rows))
        d = a.detail_payload(f"/{rid}")
        assert set(d) >= {"jobPostingInfo", "hiringOrganization",
                          "similarJobs", "firstPublishedIso",
                          "workMode", "numApplicants", "closed"}
        j = d["jobPostingInfo"]
        assert j["jobReqId"] == rid
        assert j["country"] is None
        assert j["postedOn"] == "Posted 4 Days Ago"
        assert d["hiringOrganization"]["name"] == "Renogy"
        assert d["workMode"].lower() == "on-site"
        assert "tail junk" not in j["jobDescription"]

    def test_unknown_rid_returns_none(self):
        a = _adapter(md=self._live_like_page())
        assert a.detail_payload("/deadbeef00000000") is None

    def test_jd_cleanup(self):
        md = _page(_expanded_card(
            "Engineer", "Renogy", "Ontario, CA", "Trabajo.org",
            jd="Read [the policy](https://x/p) first\\.\n\n\n\n"
               "Second line."))
        a = _adapter(md=md)
        rid = next(iter(a.list_board()[0]))
        jd = a.detail_payload(f"/{rid}")["jobPostingInfo"][
            "jobDescription"]
        assert "[the policy](https://x/p)" not in jd
        assert "the policy" in jd
        assert "first." in jd              # md escape flattened
        assert "\n\n\n" not in jd
