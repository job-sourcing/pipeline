"""S32 — the LinkedInCompanyAdapter (custom:licompany:{key}) pins.

Hermetic: LinkedInCompanyAdapter._get is monkeypatched — no network.
The discrimination matrix is the safety net (review s32-r P0-2/P0-3):
this cohort's boards are 1-9 rows, BELOW the watch layer's ≥10-row
EMPTY-LIST ANOMALY guard, so the adapter's own signature discipline is
the only thing between a soft wall and a silent mass-gone event.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobsearch.config import Config                     # noqa: E402
from jobsearch.sources import site_boards               # noqa: E402
from jobsearch.sources.site_boards import (             # noqa: E402
    LICOMPANY_CONTRACTS, LinkedInCompanyAdapter, list_board,
    detail_payload)


@pytest.fixture(autouse=True)
def _clear_li_cache():
    """_rows() caches per-process by contract key — a prior test's
    cached walk would mask later tests' fake responses."""
    for k in ("licompany:vesync", "licompany:firework"):
        site_boards._CACHE.pop(k, None)
    yield
    for k in ("licompany:vesync", "licompany:firework"):
        site_boards._CACHE.pop(k, None)


def _card(jid: str, company: str = "VeSync", title: str = "Engineer",
          loc: str = "Tustin, CA", date: str = "2026-10-01") -> str:
    return (
        f'<li><div class="base-card" data-entity-urn='
        f'"urn:li:jobPosting:{jid}">'
        f'<a class="base-card__full-link" href="https://www.linkedin.com/'
        f'jobs/view/x-at-y-{jid}"><span class="sr-only">{title}</span></a>'
        f'<div class="base-search-card__info">'
        f'<h3 class="base-search-card__title">\n      {title}\n</h3>'
        f'<h4 class="base-search-card__subtitle">'
        f'<a class="hidden-nested-link">\n      {company}\n  </a></h4>'
        f'<span class="job-search-card__location">\n  {loc}\n</span>'
        f'<time class="job-search-card__listdate" datetime="{date}">x</time>'
        f'</div></div></li>')


def _page(cards: list[str]) -> str:
    return "<ul>" + "".join(cards) + "</ul>"


STUB = '<!DOCTYPE html>\n\n<!---->'


def _adapter(key: str = "vesync", responses=None, calls=None):
    a = LinkedInCompanyAdapter(key, Config())
    if responses is not None:
        seq = list(responses)

        def fake_get(url, params):
            if calls is not None:
                calls.append((url, dict(params)))
            return seq.pop(0) if len(seq) > 1 else seq[0]
        a._get = fake_get
    return a


class TestContracts:
    def test_59_contracts_registered(self):
        # 18 daily + 41 dormant (audit/s32_li probe9 + resolution)
        assert len(LICOMPANY_CONTRACTS) == 59

    def test_every_contract_has_urn_and_variants(self):
        for key, c in LICOMPANY_CONTRACTS.items():
            assert re.fullmatch(r"\d+", str(c["urn"])), (key, c["urn"])
            assert c["li_variants"] and all(
                v == v.strip().lower() for v in c["li_variants"]), key
            assert c["company"], key

    def test_wired_roster_companies_absent(self):
        # review P0-1: EcoFlow/MINISO/CelerData are LIVE watches — a
        # licompany contract for them would collide in the variant
        # guard (config-load SystemExit) or double-cover silently.
        for banned in ("ecoflow", "miniso", "celerdata", "starrocks",
                       "phoenixai"):
            assert banned not in LICOMPANY_CONTRACTS

    def test_homonym_exclusions_absent(self):
        for banned in ("fireworks ai", "onyx rip", "vava virtual",
                       "wyczer"):
            flat = [v for c in LICOMPANY_CONTRACTS.values()
                    for v in c["li_variants"]]
            assert banned not in flat

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match="unknown contract"):
            LinkedInCompanyAdapter("nonexistent-co", Config())


class TestStopConditions:
    """The signature matrix (review P0-3): stub=end; 0-cards-other-
    shape=blocked(incomplete); short page=tail; !=200=raise."""

    def test_stub_is_genuine_end(self):
        a = _adapter(responses=[(200, STUB, "")])
        rows, status = a._walk_entity("3374179")
        assert rows == [] and status == "end"

    def test_page0_stub_is_dormant_zero_not_blocked(self):
        a = _adapter(responses=[(200, STUB, "")])
        rows, complete = a._rows()
        assert rows == [] and complete is True

    def test_zero_cards_big_body_is_blocked_not_end(self):
        a = _adapter(responses=[(200, "<html>" + " " * 5000 +
                                 "authwall-ish</html>", "")])
        rows, complete = a._rows()
        assert rows == [] and complete is False

    def test_http_error_raises(self):
        for code in (429, 403, 302, 500):
            a = _adapter(responses=[(code, "x", "/authwall")])
            with pytest.raises(RuntimeError, match="HTTP"):
                a._walk_entity("3374179")

    def test_short_page_is_tail(self):
        a = _adapter(responses=[(200, _page([_card("1"), _card("2")]), "")])
        rows, status = a._walk_entity("3374179")
        assert len(rows) == 2 and status == "tail"

    def test_full_page_advances_by_received(self):
        calls = []
        a = _adapter(
            responses=[(200, _page([_card(str(i)) for i in range(10)]), ""),
                       (200, _page([_card("99")]), "")],
            calls=calls)
        rows, status = a._walk_entity("3374179")
        assert len(rows) == 11 and status == "tail"
        assert calls[1][1]["start"] == "10"   # advanced by CARDS received

    def test_blocked_entity_makes_aggregate_incomplete(self):
        a = _adapter(responses=[(200, "<html>" + " " * 5000 + "</html>", "")])
        rows, meta = a.list_board(progress_label="t")
        assert meta["complete"] is False and rows == {}


class TestVariantGuard:
    def test_off_variant_cards_dropped_silently_only_when_partial(self):
        page = _page([_card("1", company="VeSync"),
                      _card("2", company="Another Co")])
        a = _adapter(responses=[(200, page, "")])
        rows, status = a._walk_entity("3374179")
        assert [r["rid"] for r in rows] == ["1"] and status == "tail"

    def test_zero_variant_matches_on_full_page_raises(self):
        page = _page([_card(str(i), company="Fireworks AI")
                      for i in range(10)])
        a = _adapter(responses=[(200, page, "")])
        with pytest.raises(ValueError, match="NONE match"):
            a._walk_entity("18700473")

    def test_empty_urn_refuses_request(self):
        # P0-2: a dropped f_C serves the GLOBAL US feed — refuse first
        a = _adapter()
        with pytest.raises(ValueError, match="not a non-empty numeric"):
            a._walk_entity("")
        with pytest.raises(ValueError, match="not a non-empty numeric"):
            a._walk_entity("evgo")


class TestListBoard:
    def test_rows_shape_and_meta(self):
        a = _adapter(responses=[
            (200, _page([_card("4444737286", title="Security Analyst")]), "")])
        rows, meta = a.list_board(progress_label="t")
        assert list(rows) == ["4444737286"]
        r = rows["4444737286"]
        assert r["company"] == "VeSync" and r["ats"] == "licompany"
        assert r["postedOn"] and r["url"].endswith("/4444737286")
        assert r["externalPath"] == "/4444737286"
        assert meta["country_client"] is False   # server-scoped facet
        assert meta["complete"] is True and meta["geo_scope"] == ""

    def test_no_country_client_filter(self):
        # the feed IS the US facet — a country=United States arg must
        # not re-filter rows ("Tustin, CA" stays; review P1-3)
        a = _adapter(responses=[
            (200, _page([_card("1", loc="Tustin, CA")]), "")])
        rows, _ = a.list_board(country="United States",
                               progress_label="t")
        assert len(rows) == 1


class TestDetailPayload:
    DETAIL = """
    <section class="top-card-layout">
      <h4 class="top-card-layout__subtitle"><a class="hidden-nested-link">
      VeSync</a></h4>
    </section>
    <div class="show-more-less-html__markup"><p>The Company: VeSync is a
    portfolio company. This role is Remote within the US.</p></div>
    <span class="num-applicants__caption"><span>Be among the first 25
    applicants</span></span>"""

    def test_detail_shape(self, monkeypatch):
        a = _adapter(responses=[
            (200, _page([_card("4444737286")]), ""),
            (200, self.DETAIL, "")])
        a._rows()                      # seed the list-page walk
        d = a.detail_payload("/4444737286")
        assert d is not None
        jpi = d["jobPostingInfo"]
        jd_text = " ".join(jpi["jobDescription"].split())
        assert "VeSync is a portfolio company" in jd_text
        assert jpi["jobReqId"] == "4444737286"
        assert jpi["externalUrl"].endswith("/4444737286")
        assert d["hiringOrganization"]["name"] == "VeSync"
        assert d["numApplicants"].startswith("Be among the first 25")

    def test_detail_non_numeric_rid_returns_none(self):
        d = detail_payload("custom:licompany:vesync", "/abc",
                           cfg=Config())
        assert d is None


class TestGrammar:
    def test_custom_grammar_routes(self):
        assert site_boards.is_site_spec("custom:licompany:vesync")
        kind, org = site_boards.parse_site("custom:licompany:vesync")
        assert (kind, org) == ("licompany", "vesync")
        # unknown CONTRACT keys raise at adapter construction (the
        # ssrpage contract-dict discipline), not at spec parse
        assert site_boards.is_site_spec("custom:licompany:whatever")

    def test_registry_dispatch(self):
        # the _ADAPTERS registry maps the kind to the class
        from jobsearch.sources.site_boards import _ADAPTERS
        assert _ADAPTERS["licompany"] is LinkedInCompanyAdapter


class TestVariantGuardIntegration:
    """The driver-side guard (s32-r P0-1): a licompany contract must
    self-agree with its own watch entry and cross-fail against any
    OTHER owner."""

    def test_config_loads_with_licompany_watches(self):
        # the live 212-watch config passes the guard (the first splice
        # run self-collided — the owner-label fix)
        import subprocess, sys as _sys
        r = subprocess.run(
            [_sys.executable, "scripts/gha_board_watch.py", "--label",
             "no-such-label-xyz"],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True, text=True, timeout=60)
        # unknown label exits 2 AFTER config-load + guard pass
        assert "CONFIG ERROR" not in (r.stdout + r.stderr)

    def test_cross_claim_detected(self):
        # RED-CHECK: a hypothetical EcoFlow contract (company
        # EcoFlow — already watched by ecoflowus) would collide.
        # The ecoflowus watch claims company "ecoflow" (config side);
        # the fake contract claim fires the collision.
        owners = {}

        def claim(name, label):
            owner = owners.get(name)
            if owner is not None and owner != label:
                raise SystemExit(f"collision {name}")
            owners.setdefault(name, label)
        claim("ecoflow", "ecoflowus_us_fulltime")   # the real config
        with pytest.raises(SystemExit):
            claim("ecoflow", "licompany:ecoflow")   # a future contract
