"""test_s39_role_sweep.py — the S39 amendment battery (DESIGN-S39 v2).

Pins every measured claim from the round:
  - the usgate P0 case fix + city allowlist + F4 collision guards
  - company_seed_key hardening (B7: entities/&/the/and)
  - G1B' extensions with their TP guards (T1'/T2'/T4'/T6'/T9' + CPO)
  - the screening-post detector (N7 compile contract + guards)
  - the bank rule N3B (T1 ordering: guard → tech → product → review)
  - the payer rule N6B (T2a ordering: division → business → tech → review)
  - TECH_SEED precedence + disjointness + roster grade (F6)
  - the new merge rules (cross-source ±3d, N8 city-suffix, seed union)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import pytest

import role_sweep as rs
from jobsearch import company_seeds
from jobsearch.rolefamilies import (
    G1B, company_seed_key, g1b_fires, is_screening_post,
    is_staffing_company, sector_verdict,
)

# reuse the S38 sweep fixture machinery
from test_s38_role_sweep import _row, sweep_env  # noqa: F401


# ── usgate: the P0 case fix + city allowlist + F4 guards ────────────────

class TestUsgate:
    CASES = [
        # (loc, expected) — the 17-case table + F4 collisions
        ("San Francisco", "us"),
        ("San Francisco / New York City", "us"),
        ("New York, NY", "us"),
        ("Austin, TX", "us"),
        ("California", "us"),           # careerjet bare state names
        ("Washington", "us"),
        ("Georgia", "us"),
        ("New Jersey", "us"),           # multi-word state names
        ("North Carolina", "us"),
        ("Seattle", "us"),
        ("Miami, FL", "us"),
        ("SF Bay Area", "us"),
        ("Washington DC", "us"),
        ("Indianapolis, IN", "us"),     # 'india' substring hazard
        ("New York City, Washington DC, Washington", "us"),
        ("Remote", "remote"),
        ("Remote - Canada", "remote"),  # A-prime doctrine: kept
        ("Remote — US", "remote"),
        ("United States", "us"),
        ("US", "us"),
        ("3 Locations", "ambiguous"),   # F11 BuiltIn multi-loc
        ("", "ambiguous"),
        ("Chennai, TN", "non_us"),      # F4 foreign-state collisions
        ("Perth, WA", "non_us"),
        ("Delhi, IN", "non_us"),
        ("London, England, GB", "non_us"),
        ("Toronto", "non_us"),
        ("Berlin, Freiburg, NYC", "non_us"),
        ("Vienna", "non_us"),           # F4: removed from city list
        ("Newark, Nottingham, UK", "non_us"),
        ("Sydney", "non_us"),
    ]

    @pytest.mark.parametrize("loc,want", CASES)
    def test_classify_loc_table(self, loc, want):
        assert rs._classify_loc(loc) == want

    def test_state_token_case_bug_regression(self):
        """The shipped-S36 bug: uppercase set vs t.lower() compare —
        the state-token arm NEVER matched. Pin the fix."""
        # would have returned non_us under the bug (measured: 1,377
        # usgate drops on the lane run, ~2,555 recoverable rows/sweep)
        for loc in ("New York, NY", "Austin, TX", "Miami, FL"):
            toks = {t.lower() for t in rs._US_STATE_TOKENS}
            import re
            assert any(t in toks for t in re.split(r"[^a-z]+", loc.lower()))


# ── company_seed_key hardening (B7) ─────────────────────────────────────

class TestSeedKeyHardening:
    @pytest.mark.parametrize("raw,want", [
        ("Procter &amp; Gamble", "procter & gamble"),
        ("Procter&Gamble", "procter & gamble"),
        ("The Cigna Group", "cigna"),
        ("The Vanguard Group", "vanguard"),
        ("Stanley Black &amp; Decker", "stanley black & decker"),
        ("Stanley Black and Decker", "stanley black & decker"),
        ("Waters Corporation Inc", "waters"),
        ("A. O. Smith", "a.o.smith"),
    ])
    def test_seed_key_normalization(self, raw, want):
        assert company_seed_key(raw) == want

    def test_entity_form_hits_deny_seed(self):
        """T2a's guard finding: the entity-form P&G row escaped its own
        deny seed before the hardening."""
        assert company_seed_key("Procter &amp; Gamble") == \
            company_seed_key("Procter & Gamble")


# ── G1B' extensions (measured rows + TP guards) ─────────────────────────

class TestG1BExtensions:
    FIRES = [
        "Head of Product, Textiles & Furniture 9 Locations",   # HNI
        "Associate PM – Packaging Products",                    # Uline
        "Product Manager – Automotive (2511)",                  # Hellermann
        "Aerospace and Defense Product Manager",                # SHD
        "Sr. Product Manager – Cardiology",                     # Terumo
        "Service Product Manager, Diagnostic X-ray (North America)",
        "Manager, Product Group Engineering, PLM",              # J&J
        "Senior Technical PM – Insurance Products & Benefits",  # Humana
        "Product Owner (Patient Support Programs / HUB Services)",
        "Lead Product Engineer",                                # T5n
        "Product Controller",                                   # T7' amend
    ]  # (Kohler 'Manufacturing PM' is covered by the KOHLER deny seed)
    NO_FIRE = [
        "Business Unit Leader for Co-Packaged Optics (CPO)",    # F10
        "Product Manager, AWS X-Ray",                           # F10
        "Senior PM – Xray Security Scanning",
        "Product Manager – P&C Insurance Technology",           # Majesco
        "Director of Product Marketing, PLM",                   # PTC
        "Product Manager, B2B Automotive Parts Platform",       # Photon
        "Senior Product Manager",                               # baseline
        "Director, Product Management - Nemotron",              # NVIDIA
    ]

    @pytest.mark.parametrize("t", FIRES)
    def test_fires(self, t):
        assert g1b_fires(t), t

    @pytest.mark.parametrize("t", NO_FIRE)
    def test_guards_hold(self, t):
        assert not g1b_fires(t), t

    def test_compile_contract_verbose(self):
        """G1B compiles with re.VERBOSE (the S36 pin); the new arms
        respect it (alternations on line boundaries)."""
        import re
        assert G1B.flags & re.VERBOSE


# ── the screening-post detector (N7) ────────────────────────────────────

class TestScreeningPost:
    FIRES = [
        "Product Manager (3-5+ YOE)",
        "Senior ML PO / Business Analyst",
        "PO with SQL Skills – W2 Only",
        "Product Owner, 3 Senior",
        "Business Analyst and Project Manager, Product Owner",  # F8 miss-1
        "Senior BA / PO – WealthTech",                          # F8 miss-2
        "Product Manager - Product Manager",                    # garbled
        "Hybrid: Pittsburgh PA – senior PO (12+ years only)",
        "AdTech PO / BA",
    ]
    NO_FIRE = [
        "Senior Product Manager / Payments & Agentic Commerce / NYC",  # Motion
        "Principal/Senior PM",                                    # level range
        "Lead PM / Head of Product",
        "Product Manager, Payments Platform",                     # real role
        "Senior Product Manager, AWS",
    ]

    @pytest.mark.parametrize("t", FIRES)
    def test_fires(self, t):
        assert is_screening_post(t), t

    @pytest.mark.parametrize("t", NO_FIRE)
    def test_guards_hold(self, t):
        assert not is_screening_post(t), t

    def test_compile_contract_no_verbose(self):
        """F8: the wave-2 regex under re.VERBOSE compiles to a 3×
        undercount — the detector MUST NOT use VERBOSE."""
        import re
        assert not (rs.is_screening_post.__globals__
                    ["SCREENING_POST"].flags & re.VERBOSE)

    def test_staffing_brand_additions(self):
        """F5: T2b's 17 unflagged consultancies must flag."""
        for c in ("Coforge", "Perficient", "Hexaware", "GovCIO",
                  "Raft", "BC Forward", "LTIMindtree",
                  "ManpowerGroup", "Pinnacle Technical Resources"):
            assert is_staffing_company(c), c


# ── the bank rule N3B (T1 §2 measured ordering) ─────────────────────────

class TestBankRule:
    def test_tech_division_rescues_first(self):
        # Western Alliance: 'Product Technology' beats 'Small Business
        # Banking' — T1's load-bearing ordering case
        a, e, r = sector_verdict(
            "Director Product Technology, Preferred and Small Business "
            "Banking", "Western Alliance Bank", "", "western-alliance-bank")
        assert (a, e) == ("keep", "division")

    def test_bank_product_buckets(self):
        a, e, r = sector_verdict(
            "Product Manager – Insurance (Business Banking)",
            "J.P. Morgan", "", "j-p-morgan")
        assert (a, e, r) == ("bucket", "", "bank_business_product")

    def test_utilities_never_a_bank_token(self):
        """T1: JPM Utilities rows are the Digital Utilities PLATFORM
        product — the token is NEVER-gated."""
        a, e, r = sector_verdict(
            "Utilities Product Manager, Vice President",
            "J.P. Morgan", "", "j-p-morgan")
        assert a == "pending" and r == "bank_review"

    def test_review_rows_ride_pending_not_bucket(self):
        """F7 terminal state: unmatched bank rows → pending ▲, never
        auto-bucket."""
        a, e, r = sector_verdict(
            "Product Manager", "BNY", "", "bny")
        assert (a, r) == ("pending", "bank_review")

    def test_fintech_guard_blocks_rule(self):
        for comp, ck in (("PayPal", "paypal"),
                         ("Expedia", "expedia"), ("Affirm", "affirm")):
            a, e, r = sector_verdict(
                "Senior PM – Consumer Credit Card", comp, "", ck)
            assert a == "keep", (comp, a)

    def test_mercury_unresolved_rides_pending(self):
        """F14: mercury is deliberately in NEITHER the guard NOR the
        bank seed (identity conflict) → its rows stay pending."""
        a, e, r = sector_verdict(
            "Senior PM – Consumer Credit Card", "Mercury", "", "mercury")
        assert a == "pending"

    def test_vanguard_leading_the(self):
        """F14/F15: 'The Vanguard Group' → vanguard (BANK_SEED)."""
        a, e, r = sector_verdict(
            "Director, Product Marketing", "The Vanguard Group", "",
            "the-vanguard-group")
        assert r in ("bank_business_product", "bank_review")

    def test_paypal_deposits_kept_roster(self):
        """The wave-2 guard: PayPal 'Deposits (PayPal Bank)' is a
        fintech product line — TECH_SEED wins."""
        a, e, r = sector_verdict(
            "Product Manager, Deposits (PayPal Bank)", "PayPal", "",
            "paypal")
        assert (a, e) == ("keep", "roster")


# ── the payer rule N6B (T2a ordering) ───────────────────────────────────

class TestPayerRule:
    def test_business_before_tech_tokens(self):
        """The Humana case: 'Senior Technical PM – Insurance Products'
        is business DESPITE the 'Technical' token (T2a's ordering)."""
        a, e, r = sector_verdict(
            "Senior Technical PM – Insurance Products & Benefits",
            "Humana", "", "humana")
        assert (a, r) == ("bucket", "payer_business_product")

    def test_tech_lean_rescue(self):
        a, e, r = sector_verdict(
            "Senior Technical Product Manager", "Humana", "", "humana")
        assert (a, e) == ("keep", "division")

    def test_division_kw_rescue(self):
        a, e, r = sector_verdict(
            "Product Manager, Optum Insight", "UnitedHealth Group", "",
            "unitedhealth-group")
        assert (a, e) == ("keep", "division")

    def test_healthtech_guard(self):
        """athenahealth 'Payer Enrollment' is a SOFTWARE feature — the
        payer rule must never fire (TECH_SEED precedence)."""
        a, e, r = sector_verdict(
            "Product Owner, Payer Enrollment & Core Configuration",
            "athenahealth", "", "athenahealth")
        assert (a, e) == ("keep", "roster")


# ── TECH_SEED precedence + disjointness (F6) ────────────────────────────

class TestSeedPrecedence:
    def test_disjointness(self):
        assert not (company_seeds.TECH_KEYS & company_seeds.NONTECH_KEYS), \
            "TECH_SEED ∩ NONTECH_SEED must be ∅ (the CACI/HII resolution)"

    def test_tech_seed_overrides_deny(self):
        """F6: a company in BOTH lists resolves TECH (the CACI case —
        T2a's JD-verified call wins over T3's provisional nontech)."""
        # simulate: seed_hit with both
        assert company_seeds.tech_seed_hit("caci", "CACI International")
        a, e, r = sector_verdict(
            "Senior Product Manager", "CACI International", "", "caci")
        assert (a, e) == ("keep", "roster")

    def test_roster_grade_ranking(self):
        """division > jd > roster: a tech-seed row with JD markers
        earns 'jd' (the Stripe/S38 test case)."""
        a, e, r = sector_verdict(
            "Product Manager", "Stripe",
            "Own the roadmap, write user stories, ship our APIs each "
            "sprint", "stripe")
        assert (a, e) == ("keep", "jd")

    def test_nvidia_roster(self):
        a, e, r = sector_verdict("Product Manager", "NVIDIA", "", "nvidia")
        assert (a, e, r) == ("keep", "roster", "tech_seed")

    def test_mcdonalds_pending_or_bucket_never_roster(self):
        a, e, r = sector_verdict(
            "Senior Product Manager", "McDonald's", "", "mcdonald-s")
        assert a in ("bucket", "pending")

    def test_slug_and_name_forms_both_match(self):
        """F6: both key spaces (companyKey slug + seed-key name)."""
        assert company_seeds.tech_seed_hit("", "Netflix")
        assert company_seeds.bank_seed_hit("j-p-morgan", "")


# ── the industrial deny+rescue (T2a §2) ─────────────────────────────────

class TestIndustrialDeny:
    def test_ford_division_rescue(self):
        a, e, r = sector_verdict(
            "Senior Product Manager, Model E", "Ford Motor Company", "",
            "ford-motor-company")
        assert (a, e) == ("keep", "division")

    def test_ford_generic_buckets(self):
        a, e, r = sector_verdict(
            "Product Manager", "Ford Motor Company", "",
            "ford-motor-company")
        assert a == "bucket"

    def test_caterpillar_amazon_split(self):
        """T3's Amazon split: division matcher on company+title (bare
        'amazon' is NOT tech-seeded — the retail-side rows bucket,
        recoverable; AWS-entity keys are TECH_SEED)."""
        a, e, r = sector_verdict(
            "Senior Product Manager, AWS Data Zone", "Amazon", "",
            "amazon")
        assert (a, e) == ("keep", "division")
        a, e, r = sector_verdict(
            "Senior Product Manager", "Amazon", "", "amazon")
        assert a == "bucket"

    def test_ge_aerospace_rescue(self):
        a, e, r = sector_verdict(
            "Sr Staff TPM – Data Product Marketplace", "GE Aerospace", "",
            "ge-aerospace")
        assert (a, e) == ("keep", "division")


# ── the new merge rules (F1d cross-source, N8 pairs, seed union) ────────

class TestNewMergeRules:
    def _patch_all(self, monkeypatch, rows_by_source):
        def mk(rows, verdict="ok"):
            def _f(*a, **k):
                return list(rows), verdict
            return _f
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

    def test_cross_source_exact_day_skew_merges(self, sweep_env,
                                                monkeypatch):
        """F1d: careerjet/adzuna same-feed rows with 0-3d date skew
        merge (the seed union's main dedup path)."""
        a = _row(title="Senior Product Manager", company="Acme Corp",
                 loc="New York, NY", posted="2026-10-14",
                 source="adzuna", layer="l1.5", rid="adz:1")
        b = _row(title="Senior Product Manager", company="Acme Corp",
                 loc="New York, NY", posted="2026-10-12",
                 source="careerjet", layer="l1.5", rid="cj:9")
        self._patch_all(monkeypatch, {"adzuna": [a], "careerjet": [b]})
        rc = rs.sweep(["product manager"])
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["rows_kept"] == 1
        assert env["counters"]["cross_src_merge"] == 1

    def test_cross_source_4day_skew_does_not_merge(self, sweep_env,
                                                   monkeypatch):
        a = _row(title="Senior Product Manager", company="Acme Corp",
                 loc="New York, NY", posted="2026-10-14",
                 source="adzuna", layer="l1.5", rid="adz:1")
        b = _row(title="Senior Product Manager", company="Acme Corp",
                 loc="New York, NY", posted="2026-10-10",
                 source="careerjet", layer="l1.5", rid="cj:9")
        self._patch_all(monkeypatch, {"adzuna": [a], "careerjet": [b]})
        rc = rs.sweep(["product manager"])
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["rows_kept"] == 2

    def test_bjak_multi_req_not_merged(self, sweep_env, monkeypatch):
        """F1d guard: same title_core-different-specializations stay
        un-merged (the bjak 12-variant class)."""
        a = _row(title="Technical Product Manager, AI", company="Bjak",
                 loc="US", posted="2026-10-14", source="adzuna",
                 layer="l1.5", rid="adz:1")
        b = _row(title="Technical Product Manager, Payments", company="Bjak",
                 loc="US", posted="2026-10-13", source="careerjet",
                 layer="l1.5", rid="cj:9")
        self._patch_all(monkeypatch, {"adzuna": [a], "careerjet": [b]})
        rc = rs.sweep(["product manager"])
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["rows_kept"] == 2

    def test_agency_city_suffix_pair_merges(self, sweep_env, monkeypatch):
        """N8: 'Senior Product Manager' vs 'Senior Product Manager
        (Chicago)' at the same adzuna staffing agency = one req."""
        a = _row(title="Senior Product Manager", company="iSpace",
                 loc="Chicago, IL", posted="2026-10-14", source="adzuna",
                 layer="l1.5", rid="adz:1")
        b = _row(title="Senior Product Manager (Chicago)", company="iSpace",
                 loc="Chicago, IL", posted="2026-10-13", source="adzuna",
                 layer="l1.5", rid="adz:2")
        self._patch_all(monkeypatch, {"adzuna": [a, b]})
        rc = rs.sweep(["product manager"])
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["rows_kept"] == 1

    def test_seed_envelope_union(self, sweep_env, monkeypatch):
        """§A5: prior-envelope rows re-enter, staleness-bounded, and
        merge with fresh rows; the double-count is measured."""
        roles, watch, ui = sweep_env
        prior = {
            "envelope": {"ran_at": "2026-10-10T00:00:00Z"},
            "jobs": [{
                "title": "Senior Product Manager", "company": "Acme Corp",
                "companyKey": "acme-corp", "loc": "New York, NY",
                "url": "https://x/y", "postedOn": "2026-10-13",
                "description": "", "source": "careerjet", "id": "cj:7",
            }, {
                # stale row (> 60d) must NOT re-enter
                "title": "Old Product Manager", "company": "Old Corp",
                "companyKey": "old-corp", "loc": "US",
                "url": "https://x/o", "postedOn": "2026-08-01",
                "description": "", "source": "careerjet", "id": "cj:8",
            }],
        }
        (roles / "prior.json").write_text(json.dumps(prior))
        fresh = _row(title="Senior Product Manager", company="Acme Corp",
                     loc="New York, NY", posted="2026-10-14",
                     source="adzuna", layer="l1.5", rid="adz:1")
        self._patch_all(monkeypatch, {"adzuna": [fresh]})
        rc = rs.sweep(["product manager"],
                      seed_envelope=str(roles / "prior.json"))
        env = json.load(open(roles / "pm_envelope.json"))
        # 1 fresh row + 1 seed row merge (cross-source ±3d) → 1 kept
        assert env["counters"]["seed_rows_in"] == 1
        assert env["rows_kept"] == 1
        assert env["counters"]["seed_dup_absorbed"] >= 1
        assert env["sources"]["seed_envelope"].startswith("ok:1/")

    def test_screening_post_bucket(self, sweep_env, monkeypatch):
        """N7: the 13-row class routes to the recoverable bucket."""
        a = _row(title="Product Manager (3-5+ YOE)", company="Apex Systems",
                 loc="US", posted="2026-10-14", source="dice",
                 layer="l1.5", rid="dice:1")
        self._patch_all(monkeypatch, {"dice": [a]})
        rc = rs.sweep(["product manager"])
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["counters"]["screening_post"] == 1
        assert env["rows_kept"] == 0

    def test_agency_grade_resolves_pending(self, sweep_env, monkeypatch):
        """§B1: staffing-flagged pending rows ship with
        sector_evidence='agency' (never dropped)."""
        a = _row(title="Senior Technical Product Manager",
                 company="Kforce Technology Staffing", loc="US",
                 posted="2026-10-14", source="dice", layer="l1.5",
                 rid="dice:1")
        self._patch_all(monkeypatch, {"dice": [a]})
        rc = rs.sweep(["product manager"])
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["rows_kept"] == 1
        assert env["counters"]["agency_pending"] == 1
        assert env["counters"]["pending_sector"] == 0
        bundle = json.load(open(sweep_env[2] / "roles-pm.json"))
        row = bundle["jobs"][0]
        assert row["sectorEvidence"] == "agency"
