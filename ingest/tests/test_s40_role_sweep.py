"""test_s40_role_sweep.py — the S40 integrity battery.

Pins the measured claims of the round (audit/s40 findings):
  - the ukey (url-identity) merge: board-employer rows (F14-salted ck)
    with the SAME url + title are the same posting → merge; the
    compounding seed-union dup class (the indeed:4e33 triple, +1 per
    run) is dead. Different urls keep the F14 guard (distinct rows).
  - the seed-envelope desc fix: the bundle field is 'desc' (reading
    'description' stripped the JD from 1,282 careerjet seed rows on
    lane #5 — descLen=0 corpus-wide for the source).
  - the _row_id basis (ck,title,loc,source,posted,urlh): >7d repost
    pairs and same-day url-variants get DISTINCT ids (16 collision
    rows observed on the 5,026-row production envelope).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import pytest

import role_sweep as rs

from test_s38_role_sweep import _row, sweep_env  # noqa: F401


class _Sweep:
    """shared patcher (the S38/S39 pattern)."""

    @staticmethod
    def _patch_all(monkeypatch, rows_by_source):
        def mk(rows, verdict="ok"):
            def _f(*a, **k):
                return list(rows), verdict
            return _f
        monkeypatch.setattr(rs, "project_l0", lambda: ([], "no-bundle"))
        attr_by_name = {
            "findwork": "fetch_findwork", "adzuna": "fetch_adzuna",
            "dice": "fetch_dice", "careerjet": "fetch_careerjet",
            "jooble": "fetch_jooble", "indeed": "fetch_jobspy_indeed",
            "li_keyword": "fetch_li_keyword", "yc": "fetch_yc",
            "wellfound": "fetch_wellfound", "hn": "fetch_hn",
            "wwr_rss": "fetch_wwr", "free_trio": "fetch_free_trio",
            "talent": "fetch_talent", "muse": "fetch_muse",
            "usajobs": "fetch_usajobs", "gj_role": "fetch_gj_role",
            "builtin": "fetch_builtin", "himalayas": "fetch_himalayas",
        }
        for name, attr in attr_by_name.items():
            monkeypatch.setattr(rs, attr, mk(rows_by_source.get(name, [])))


class TestUkeyIdentityMerge(_Sweep):
    """S40: the url-identity rule — same url + same title + same
    source = one posting, even when the ck is F14-salted
    (board-employer: company literally 'Indeed'/'LinkedIn'/...)."""

    def test_board_employer_same_url_merges(self, sweep_env,
                                            monkeypatch):
        # the production triple shape: company=Indeed (N9 board-
        # employer → ck salted per-row → company-key rules can never
        # fire), same url, same title, query-duplicated + seed-copied
        a = _row(title="Director of Product Management for Candidate "
                       "Trust & Evaluation",
                 company="Indeed", loc="Remote, USA",
                 posted="2026-10-12", source="indeed", layer="l1.5",
                 rid="", url="https://www.indeed.com/viewjob?jk=AAA")
        b = _row(title="Director of Product Management for Candidate "
                       "Trust & Evaluation",
                 company="Indeed", loc="Remote, USA",
                 posted="2026-10-12", source="indeed", layer="l1.5",
                 rid="", url="https://www.indeed.com/viewjob?jk=AAA")
        self._patch_all(monkeypatch, {"indeed": [a, b]})
        rc = rs.sweep(["product manager"])
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["rows_kept"] == 1
        assert env["counters"].get("url_identity_merge", 0) >= 1

    def test_seed_union_board_employer_no_compounding(self, sweep_env,
                                                      monkeypatch):
        """the compounding regression pin: a prior-envelope Indeed row
        re-entering via seed + the fresh copy → ONE row (was +1 per
        run — the observed 0→2→2→3 growth across envelopes)."""
        roles, watch, ui = sweep_env
        prior = {
            "envelope": {"ran_at": "2026-10-10T00:00:00Z"},
            "jobs": [{
                "title": "Director of Product Management", "company":
                "Indeed", "companyKey": "indeed", "loc": "Remote, USA",
                "url": "https://www.indeed.com/viewjob?jk=BBB",
                "postedOn": "2026-10-11", "desc": "runs the trust team",
                "source": "indeed", "id": "indeed:4e3346a1e1a3",
            }],
        }
        (roles / "prior.json").write_text(json.dumps(prior))
        fresh = _row(title="Director of Product Management",
                     company="Indeed", loc="Remote, USA",
                     posted="2026-10-12", source="indeed", layer="l1.5",
                     rid="",
                     url="https://www.indeed.com/viewjob?jk=BBB")
        self._patch_all(monkeypatch, {"indeed": [fresh]})
        rc = rs.sweep(["product manager"],
                      seed_envelope=str(roles / "prior.json"))
        env = json.load(open(roles / "pm_envelope.json"))
        assert env["counters"]["seed_rows_in"] == 1
        assert env["rows_kept"] == 1
        assert env["counters"].get("url_identity_merge", 0) >= 1

    def test_board_employer_diff_urls_stay_distinct(self, sweep_env,
                                                    monkeypatch):
        """F14 guard HOLDS for genuinely distinct postings: different
        urls under the same board-employer company never merge."""
        a = _row(title="Senior Product Manager", company="Indeed",
                 loc="Remote, USA", posted="2026-10-12",
                 source="indeed", layer="l1.5", rid="",
                 url="https://www.indeed.com/viewjob?jk=CCC")
        b = _row(title="Senior Product Manager", company="Indeed",
                 loc="Remote, USA", posted="2026-10-12",
                 source="indeed", layer="l1.5", rid="",
                 url="https://www.indeed.com/viewjob?jk=DDD")
        self._patch_all(monkeypatch, {"indeed": [a, b]})
        rc = rs.sweep(["product manager"])
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["rows_kept"] == 2

    def test_same_url_diff_title_never_merges(self, sweep_env,
                                              monkeypatch):
        """title_norm is part of the ukey — the same url carrying
        different titles (never observed, but the guard) stays
        distinct."""
        a = _row(title="Senior Product Manager", company="Indeed",
                 loc="Remote, USA", posted="2026-10-12",
                 source="indeed", layer="l1.5", rid="",
                 url="https://www.indeed.com/viewjob?jk=EEE")
        b = _row(title="Principal Product Manager", company="Indeed",
                 loc="Remote, USA", posted="2026-10-12",
                 source="indeed", layer="l1.5", rid="",
                 url="https://www.indeed.com/viewjob?jk=EEE")
        self._patch_all(monkeypatch, {"indeed": [a, b]})
        rc = rs.sweep(["product manager"])
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["rows_kept"] == 2


class TestSeedDescField(_Sweep):
    """S40: seed rows carry their JD — the bundle field is 'desc'
    (the 'description' read stripped 1,282 careerjet rows' JDs)."""

    def test_seed_row_carries_desc(self, sweep_env, monkeypatch):
        roles, watch, ui = sweep_env
        prior = {
            "envelope": {"ran_at": "2026-10-10T00:00:00Z"},
            "jobs": [{
                "title": "Senior Product Manager", "company": "Northwind "
                "Analytics", "companyKey": "northwind-analytics",
                "loc": "Lincolnshire, IL",
                "url": "https://cj/1", "postedOn": "2026-10-13",
                "desc": "Northwind Analytics senior PM owning the hybrid work "
                        "portfolio end to end, with roadmaps, metrics, "
                        "and stakeholder cadence",
                "source": "careerjet", "id": "northwind-analytics:57799ed7",
            }],
        }
        (roles / "prior.json").write_text(json.dumps(prior))
        self._patch_all(monkeypatch, {})
        rc = rs.sweep(["product manager"],
                      seed_envelope=str(roles / "prior.json"))
        env = json.load(open(roles / "pm_envelope.json"))
        assert env["rows_kept"] == 1
        bundle = json.load(open(ui / "roles-pm.json"))
        assert (bundle["jobs"][0]["descLen"] or 0) >= 100

    def test_seed_desc_legacy_description_fallback(self, sweep_env,
                                                   monkeypatch):
        """older fixtures with the 'description' key still work."""
        roles, watch, ui = sweep_env
        prior = {
            "envelope": {"ran_at": "2026-10-10T00:00:00Z"},
            "jobs": [{
                "title": "Senior Product Manager", "company": "Acme",
                "companyKey": "acme", "loc": "Austin, TX",
                "url": "https://cj/2", "postedOn": "2026-10-13",
                "description": "legacy field form still carries text "
                               "for the jd graders",
                "source": "careerjet", "id": "acme:1",
            }],
        }
        (roles / "prior.json").write_text(json.dumps(prior))
        self._patch_all(monkeypatch, {})
        rc = rs.sweep(["product manager"],
                      seed_envelope=str(roles / "prior.json"))
        bundle = json.load(open(ui / "roles-pm.json"))
        assert (bundle["jobs"][0]["descLen"] or 0) >= 40


class TestRowIdUniqueness(_Sweep):
    """S40: the row-hash basis includes posted + url — the 16-collision
    class (careerjet >7d repost pairs + same-day url variants) gets
    distinct ids."""

    def test_repost_pair_distinct_ids(self, sweep_env, monkeypatch):
        """the RB Global production shape: same company/title/loc/
        source, different urls, 42d apart — kept as 2 rows BY DESIGN
        (the >7d re-crawl window) and now with DISTINCT ids."""
        a = _row(title="Senior Product Manager", company="RB Global",
                 loc="Westchester, IL", posted="2026-10-06",
                 source="careerjet", layer="l1.5", rid="",
                 url="https://jobviewtrack.com/v2/XDkZw7un")
        b = _row(title="Senior Product Manager", company="RB Global",
                 loc="Westchester, IL", posted="2026-08-25",
                 source="careerjet", layer="l1.5", rid="",
                 url="https://jobviewtrack.com/v2/JvpPxQxpk")
        self._patch_all(monkeypatch, {"careerjet": [a, b]})
        rc = rs.sweep(["product manager"])
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["rows_kept"] == 2
        bundle = json.load(open(sweep_env[2] / "roles-pm.json"))
        ids = [j["id"] for j in bundle["jobs"]]
        assert len(set(ids)) == len(ids), ids

    def test_same_day_url_variants_distinct_ids(self, sweep_env,
                                                monkeypatch):
        """the 'unknown'-company production shape: same day, same
        title, same loc, DIFFERENT urls (anonymized guard keeps the
        rows) — ids must not collide."""
        a = _row(title="Senior Technology Product Owner", company="",
                 loc="Chicago, IL", posted="2026-10-10",
                 source="careerjet", layer="l1.5", rid="",
                 url="https://jobviewtrack.com/v2/_ANCuh")
        b = _row(title="Senior Technology Product Owner", company="",
                 loc="Chicago, IL", posted="2026-10-10",
                 source="careerjet", layer="l1.5", rid="",
                 url="https://jobviewtrack.com/v2/EpbGGf")
        self._patch_all(monkeypatch, {"careerjet": [a, b]})
        rc = rs.sweep(["product manager"])
        bundle = json.load(open(sweep_env[2] / "roles-pm.json"))
        ids = [j["id"] for j in bundle["jobs"]]
        assert len(ids) == 2 and len(set(ids)) == 2, ids

    def test_identical_rows_single_id(self, sweep_env, monkeypatch):
        """fully identical input rows merge to one row/one id."""
        a = _row(title="Senior Product Manager", company="AssetMark",
                 loc="Charlotte, NC", posted="2026-10-12",
                 source="careerjet", layer="l1.5", rid="",
                 url="https://jobviewtrack.com/v2/FrHoXQ")
        b = dict(a)
        self._patch_all(monkeypatch, {"careerjet": [a, b]})
        rc = rs.sweep(["product manager"])
        bundle = json.load(open(sweep_env[2] / "roles-pm.json"))
        assert len(bundle["jobs"]) == 1


# ══════════════════════════════════════════════════════════════════════
# S40 FOLD BATTERY (DESIGN-S40 v2 §"final pin set" — the S40-R1 review
# fold; every pin a measured wave outcome or a TP-guard)
# ══════════════════════════════════════════════════════════════════════

from jobsearch import company_seeds as _cs  # noqa: E402
from jobsearch.rolefamilies import sector_verdict as _sv  # noqa: E402


class TestS40FoldMembership:
    """A5/A6/F7/F8: the OEM flips, variant resolutions, guard removal."""

    def test_oem_flips_to_tech(self):
        for key, name in [("dell", "Dell"),
                          ("dell-technologies", "Dell Technologies"),
                          ("hewlett-packard-enterprise", "HPE"),
                          ("tp-link", "TP-Link"),
                          ("samsung", "Samsung")]:
            assert _cs.tech_seed_hit(key, name), key

    def test_oem_nontech_with_divisions(self):
        for key, name in [("hp", "HP"), ("epson", "Epson"),
                          ("logitech", "Logitech"),
                          ("pure-storage", "Pure Storage"),
                          ("3m", "3M")]:
            assert _cs.nontech_seed_hit(key, name), key
            assert not _cs.tech_seed_hit(key, name), key

    def test_fintech_guard_resolution(self):
        # D-S40-3: ripple+mercury leave the guard, join TECH_SEED
        assert "ripple" not in _cs.FINTECH_GUARD
        assert "mercury" not in _cs.FINTECH_GUARD
        assert _cs.tech_seed_hit("ripple", "Ripple")
        assert _cs.tech_seed_hit("mercury", "Mercury")

    def test_zip_co_limited_not_bank(self):
        # D-S40-3: fintech-software rule; 'zip' is TECH, the limited
        # variant must not grade bank (company_seed_key strips 'limited')
        assert not _cs.bank_seed_hit("zip-co-limited", "Zip Co Limited")

    def test_staff_slug_set(self):
        # A3/F3: slug-keyed agency fold (teksystems/yoh/producto/raydar)
        for key, name in [("teksystems", "TEKsystems"),
                          ("yoh", "Yoh"),
                          ("producto", "Producto"),
                          ("raydar", "Raydar")]:
            assert _cs.staff_seed_hit(key, name), key

    def test_precedence_still_tech_first(self):
        # F11: step-0 precedence intact — no dual memberships
        inter = _cs.TECH_SEED & (_cs.NONTECH_SEED | _cs.BANK_SEED
                                 | _cs.PAYER_SEED)
        assert not inter, inter


class TestS40BankPayerRules:
    """A1/A2/B-S40-2: desc-informed phases, pair rules, orderings."""

    def test_citi_business_first_from_desc(self):
        a, e, r = _sv("Citi Commerce, Product Lead", "Citi",
                      "commerce offerings that give merchants access to "
                      "high value cardmembers", "citi")
        assert a == "bucket" and r == "bank_business_product"

    def test_barclays_martech_desc_keeps(self):
        a, e, r = _sv("Senior Product Owner", "Barclays",
                      "innovative marketing technology solutions "
                      "MarTech capabilities", "barclays")
        assert a == "keep" and r == "bank_tech_division"

    def test_solo_platform_no_longer_auto_tech(self):
        a, e, r = _sv("Senior Product Manager, Global Clearing Platform",
                      "BNY", "", "bny")
        assert a == "pending" and r == "bank_review"

    def test_platform_with_api_co_token_techs(self):
        a, e, r = _sv("Senior Product Manager, Global Payments Platform "
                      "API", "BNY", "", "bny")
        assert a == "keep" and r == "bank_tech_division"

    def test_aml_agentic_solutions_techs(self):
        a, e, r = _sv("Product Owner III - AML Agentic Solutions",
                      "PNC", "", "pnc-financial-services")
        assert a == "keep" and r == "bank_tech_division"

    def test_fraud_payments_business(self):
        a, e, r = _sv("Senior Product Manager, Core Cash Fraud "
                      "Prevention - Payments", "PNC", "",
                      "pnc-financial-services")
        assert a == "bucket" and r == "bank_business_product"

    def test_agentic_strategy_policy_business(self):
        a, e, r = _sv("Senior Product Manager, agentic commerce industry "
                      "strategy policy positions", "Amex", "",
                      "american-express")
        assert a == "bucket" and r == "bank_business_product"

    def test_western_alliance_tp_guard_survives(self):
        # T1 TP-guard: 'Product Technology, Small Business Banking'
        # still techs (the original N3B ordering case)
        a, e, r = _sv("Product Technology, Small Business Banking",
                      "Western Alliance Bank", "", "western-alliance-bank")
        assert a == "keep" and r == "bank_tech_division"

    def test_checking_product_buckets(self):
        a, e, r = _sv("Senior Product Manager - Checking (US)", "TD Bank",
                      "", "td-bank")
        assert a == "bucket" and r == "bank_business_product"


class TestS40SerpJunkAndJdblind:
    """B-S40-5 + A7/F9."""

    def test_serp_chrome_digest_grades_empty(self):
        serp = ("Skip to main content [Accessibility help](https://"
                "support.google.com/websearch?answer=181196&hl=en) "
                "[Go to Google Home](https://www.google.com/webhp?hl=en"
                "&sa=X) some page text")
        a, e, r = _sv("Senior Product Manager", "TechCorp XYZ", serp,
                      "techcorp-xyz")
        assert a == "pending" and r == "no_sector_evidence"

    def test_nontech_jdblind_marker(self):
        a, e, r = _sv("Product Manager", "Sam's Club", "", "sam-s-club")
        assert a == "bucket" and r.endswith("|jdblind")

    def test_nontech_with_jd_no_jdblind_marker(self):
        a, e, r = _sv("Product Manager", "Sam's Club", "We manage the "
                      "club experience end to end with teams.", "sam-s-club")
        assert a == "bucket" and not r.endswith("|jdblind")


class TestS40DivisionRescues:
    """A5: re-derived division entries on actual row text."""

    def test_hp_software_vs_docks(self):
        a, e, r = _sv("Software Product Manager - HP Solutions", "HP",
                      "", "hp")
        assert a == "keep" and r == "division:software product"
        a2, e2, r2 = _sv("Product Owner- Docks and Displays, "
                         "Manageability Solutions", "HP", "", "hp")
        assert a2 == "bucket"

    def test_mckesson_analytics_ampersand(self):
        # F5: the '&' case — 'analytics' catches 'AI & Analytics'
        a, e, r = _sv("Sr. Technical Product Manager, AI & Analytics",
                      "McKesson", "", "mckesson")
        assert a == "keep" and r == "division:analytics"

    def test_seven_eleven_digital_arm(self):
        a, e, r = _sv("Group Digital Product Management", "7-Eleven",
                      "", "7-eleven")
        assert a == "keep"

    def test_northrop_sw_product_owner(self):
        a, e, r = _sv("Sr Principal Software Engineer - SW Product "
                      "Owner", "Northrop Grumman", "", "northrop-grumman")
        assert a == "keep"


class TestS40AgencyOverlay(_Sweep):
    """A3/F3: the fold-STAFF rows resolve agency:repost in the sweep
    (slug-keyed STAFF_SEED + the pending→agency overlay)."""

    def test_fold_staff_row_gets_agency_grade(self, sweep_env,
                                              monkeypatch):
        roles, watch, ui = sweep_env
        row = _row(title="Product Owner", company="TEKsystems",
                   source="jooble", desc="W2 contract for our client, "
                   "12 months, $73/hr pay range")
        row["company_key"] = "teksystems"
        self._patch_all(monkeypatch, {"jooble": [row]})
        rs.sweep(["product manager"])
        bundle = json.load(open(ui / "roles-pm.json"))
        jobs = bundle["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["sectorReason"] == "agency:repost"
        assert jobs[0]["staffingEvidence"] == "agency"

    def test_g1b_gate_double_safety(self, sweep_env, monkeypatch):
        """F7-verified: the G1B gate runs BEFORE grading — even a
        TECH_SEED company's physical-goods title buckets at the gate."""
        roles, watch, ui = sweep_env
        row = _row(title="Product Manager - Kitchen Appliances",
                   company="Dell", source="adzuna")
        row["company_key"] = "dell"
        self._patch_all(monkeypatch, {"adzuna": [row]})
        rs.sweep(["product manager"])
        env = json.load(open(roles / "pm_envelope.json"))
        assert env["rows_kept"] == 0
        dropped = list(csv_reader(roles / "pm_dropped.csv"))
        assert any(d["reason"] == "g1b_title" for d in dropped)


def csv_reader(path):
    import csv as _csv
    with open(path, newline="") as f:
        return list(_csv.DictReader(f))
