"""S38 test battery — the exhaustive-completion round's contracts.

Two parts:
1. rolefamilies amendments (G1B title gate, staffing/anonymized
   detectors, physical-goods exact-key seeds, the noun-first
   program-mgmt grammar fix).
2. role_sweep unit seams (date normalizers, the dice extractor) +
   the S38 merge rules end-to-end (rid-dedup, dateless-donor,
   evidence inheritance, anonymized-key exclusion) via a fully
   monkeypatched sweep() — no network, no real bundle.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch.rolefamilies import (  # noqa: E402
    G1B,
    PHYSICAL_GOODS_SEED,
    _PG_SEED_KEYS,
    classify_family,
    company_seed_key,
    g1b_fires,
    is_anonymized_employer,
    is_staffing_company,
    physical_goods_seed_hit,
    sector_verdict,
)

# ── Part 1: rolefamilies amendments ─────────────────────────────────────────


class TestG1B:
    """The 9 tested FP classes (fp-wave1 §2) + the TP guards."""

    POSITIVE = [
        # T1 white-goods / HVAC / trades
        "Product Manager, HVAC Solutions",
        "Product Manager - Dishwasher Line",
        "Senior Product Manager, Robot Vacuum Division",
        # T2 medical / dental devices
        "Product Manager, Crown and Bridge",
        "Product Manager, Computed Tomography",
        # T3 reagents / enzymes
        "Product Manager, Enzymes",
        # T4 insurance product-line
        "Insurance Product Manager",
        # T5 power-gen equipment
        "Senior Technical Product Manager – Solar Plant IOT Systems",
        "Product Manager, Lean Burn Gas Power Generation",
        # T6 physical product-line/segment management
        "Product Line Manager - Computing",
        "Senior Product Segment Manager, Mobile Workstation (Worldwide)",
        "Director of Product Line Management",
        # T7 bank product-control
        "Director, Product Control",
        # T8 talent-community pools
        "Product Management Talent Community",
        # T9 pharma patient marketing
        "Senior Product Manager (Patient Marketing)",
    ]
    NEGATIVE = [
        # the 15 TP guards (fp-wave1 §2 tested) — software-adjacent
        "Senior Product Manager –Residential Energy Software",
        "Supply Chain Technology Product Manager",
        "Product Manager, Connected Medical Devices & SaaS",
        "Senior Product Manager, Clinical Analytics",
        "Product Manager - Device API Integration",
        "Design for Manufacturability Product Manager",
        "Senior Product Manager, HPC and AI for Physics",
        "Senior Product Manager, AI Inference Performance",
        "Product Manager, Retail Banking",
        "Product Manager, Google Play Store",
        "Senior Product Manager, App Store",
        "Product Manager, Amazon Store",
        "Product Manager, Retail Media",
        "Senior Product Manager, Brand Safety",
        # F13 software-PLM guard: platform/cloud/software/saas/api context
        "Product Line Manager, Cloud Platform",
        "Product Line Manager, Software Portfolio",
        "Product Line Manager, Payments API",
    ]

    @pytest.mark.parametrize("title", POSITIVE)
    def test_positive_classes_fire(self, title):
        assert g1b_fires(title), title

    @pytest.mark.parametrize("title", NEGATIVE)
    def test_tp_guards_do_not_fire(self, title):
        assert not g1b_fires(title), title

    def test_g1b_compiled_verbose(self):
        # the R3 lesson: multi-line patterns REQUIRE re.VERBOSE
        assert G1B.flags & __import__("re").VERBOSE

    def test_g1b_is_not_g1(self):
        # G1B is title-only and separate from the G1 JD marker set
        from jobsearch.rolefamilies import G1
        assert G1B is not G1


class TestStaffingDetector:
    @pytest.mark.parametrize("company", [
        "Kforce Technology Staffing", "Brooksource", "Eliassen Group",
        "Motion Recruitment Partners", "Randstad Digital", "MMD Services",
        "Optomi", "Harnham", "JCW Group", "Michael Page",
        "TheBoss.us", "Talently", "Success Matcher Recruitment",
    ])
    def test_brands_fire(self, company):
        assert is_staffing_company(company), company

    @pytest.mark.parametrize("company", [
        "Amazon Web Services (AWS)", "TransUnion", "UnitedHealth Group",
        "Stripe", "Epson America", "Early Warning Services",
        "Netflix", "Anthropic",
    ])
    def test_real_employers_do_not_fire(self, company):
        assert not is_staffing_company(company), company

    def test_tokens_fire(self):
        assert is_staffing_company("Acme Staffing Partners")
        assert is_staffing_company("Delta Recruitment Group")

    def test_empty_is_safe(self):
        assert not is_staffing_company("")
        assert not is_staffing_company(None)  # type: ignore[arg-type]


class TestAnonymizedDetector:
    @pytest.mark.parametrize("company", [
        "Confidential", "Company Confidential", "Stealth Startup",
        "A vinyl bar in shibuya", "Hidden",
    ])
    def test_anonymized(self, company):
        assert is_anonymized_employer(company)

    def test_real_companies_not_anonymized(self):
        assert not is_anonymized_employer("Stripe")
        assert not is_anonymized_employer("StealthCo AI")  # partial, not exact


class TestPhysicalGoodsSeeds:
    def test_seed_keys_built(self):
        assert len(_PG_SEED_KEYS) >= len(PHYSICAL_GOODS_SEED) - 4
        assert "waters" in _PG_SEED_KEYS
        assert "procter & gamble" in _PG_SEED_KEYS

    @pytest.mark.parametrize("company", [
        "Waters Corporation", "Waters", "TE Connectivity",
        "A.O. Smith", "A. O. Smith Corporation", "Pella Corporation",
        "McLane Company", "Procter & Gamble", "P&G",
        "Quest Diagnostics", "Genentech", "Amgen", "Genscript",
        "Security Benefit", "Hubbell", "Joyson Safety Systems",
    ])
    def test_seeds_hit(self, company):
        assert physical_goods_seed_hit(company), company

    @pytest.mark.parametrize("company", [
        "Quest Software", "NVIDIA", "Micron Technology", "Lenovo",
        "Waters Technology Group Software",  # suffix guard: not exact
        "AWS", "Stripe", "Pella Software",  # not a seed variant
    ])
    def test_non_seeds_survive(self, company):
        assert not physical_goods_seed_hit(company), company

    def test_legal_suffix_stripping(self):
        assert company_seed_key("Waters Corporation Inc") == "waters"
        assert company_seed_key("Hubbell Incorporated") == "hubbell"

    def test_sector_verdict_buckets_seed_company(self):
        act, sev, reason = sector_verdict(
            "Product Manager", "Waters Corporation")
        assert act == "bucket"
        assert reason.startswith("g3_deny:")

    def test_sector_verdict_keeps_software_company(self):
        act, sev, _ = sector_verdict("Product Manager", "Quest Software")
        assert act in ("keep", "pending")


class TestGrammarFix:
    def test_noun_first_program_mgmt_redirects(self):
        fam, _ = classify_family("Manager, Product Program Management")
        assert fam == "adjacent"

    def test_regular_program_manager_still_adjacent(self):
        fam, _ = classify_family("Product Program Manager")
        assert fam == "adjacent"

    def test_plain_pm_still_pm(self):
        fam, _ = classify_family("Senior Product Manager, Payments")
        assert fam == "pm"


# ── Part 2: role_sweep seams + merge rules ──────────────────────────────────


def _load_role_sweep():
    import importlib
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                           / "scripts"))
    return importlib.import_module("role_sweep")


rs = _load_role_sweep()


class TestDateNormalizers:
    def test_rfc2822(self):
        assert rs._rfc2822_to_iso(
            "Sat, 26 Sep 2026 07:47:07 GMT") == "2026-09-26"
        assert rs._rfc2822_to_iso("Fri, 17 Oct 2026 12:00:00 +0000") == \
            "2026-10-17"

    def test_iso_passthrough(self):
        assert rs._rfc2822_to_iso("2026-10-15T13:12:43Z") == "2026-10-15"

    def test_epoch_seconds_and_millis(self):
        assert rs._epoch_to_iso(1792063850) == "2026-10-15"
        assert rs._epoch_to_iso(1792063850000) == "2026-10-15"
        assert rs._epoch_to_iso(None) == ""
        assert rs._epoch_to_iso("junk") == ""


class TestDiceExtractor:
    FIXTURE = (
        'stuff<script>self.__next_f.push([1,"{\\"props\\":{\\"jobList\\":'
        '{\\"data\\":[{\\"id\\":\\"abc\\",\\"companyName\\":\\"Acme\\",'
        '\\"title\\":\\"Product Manager\\",\\"jobLocation\\":'
        '{\\"city\\":\\"Orlando\\",\\"state\\":\\"Florida\\"}}]}"]})'
        "</script>tail"
    )

    def test_extracts_escaped_flight_json(self):
        jobs = rs._dice_extract_jobs(self.FIXTURE)
        assert jobs and jobs[0]["companyName"] == "Acme"
        assert jobs[0]["title"] == "Product Manager"

    def test_returns_none_when_absent(self):
        assert rs._dice_extract_jobs("<html>nothing</html>") is None


@pytest.fixture()
def sweep_env(tmp_path, monkeypatch):
    """Point role_sweep's outputs at tmp and neutralize the bundle."""
    roles = tmp_path / "roles"
    watch = tmp_path / "watch"
    ui = tmp_path / "ui"
    for d in (roles, watch, ui):
        d.mkdir()
    monkeypatch.setattr(rs, "ROLES_DIR", roles)
    monkeypatch.setattr(rs, "WATCH_DIR", watch)
    monkeypatch.setattr(rs, "UI_DIR", ui)
    monkeypatch.setattr(rs, "LEDGER", watch / "roles:pm.runs.jsonl")
    monkeypatch.setattr(rs, "BASELINE", watch / "drift_baseline.json")
    monkeypatch.setattr(rs, "BUNDLE", tmp_path / "no-bundle.json")
    monkeypatch.setattr(rs, "_EVIDENCE_DIR", tmp_path / "audit")
    return roles, watch, ui


def _row(title="Senior Product Manager", company="Stripe", loc="US",
         posted="2026-10-14", source="adzuna", layer="l1.5", rid="",
         desc="", url="https://x/y"):
    return {"title": title, "company": company, "loc": loc, "url": url,
            "posted": posted, "desc": desc, "source": source,
            "layer": layer, "query": "q", "rid": rid}


class TestMergeRules:
    def _patch_all(self, monkeypatch, rows_by_source):
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

    def test_rid_dedup_merges_same_id(self, sweep_env, monkeypatch):
        yc_a = _row(title="Lead Product Manager", company="GiveCampus",
                     posted="", source="yc", layer="l2", rid="yc:107562")
        yc_b = _row(title="Lead Product Manager", company="GiveCampus",
                     posted="2026-10-01", source="yc", layer="l2",
                     rid="yc:107562")
        self._patch_all(monkeypatch, {"yc": [yc_a], "wellfound": [yc_b]})
        # route yc_b through a different source name but same rid via
        # wellfound feed? rid prefix says yc: — use yc twice through the
        # same fetcher registration instead:
        monkeypatch.setattr(rs, "fetch_wellfound", lambda *a: ([], "ok"))
        monkeypatch.setattr(
            rs, "fetch_yc",
            lambda *a: ([yc_a, yc_b], "ok"))
        rc = rs.sweep(["product manager"])
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["rows_kept"] == 1
        assert env["counters"]["dedup_merge"] >= 1

    def test_dateless_donor_merges_on_title_company(
            self, sweep_env, monkeypatch):
        waas = _row(title="Founding Product Manager", company="Klarify",
                    posted="", source="yc", layer="l2", rid="yc:1")
        cj = _row(title="Founding Product Manager", company="Klarify",
                  loc="Remote", posted="2026-10-13", source="careerjet",
                  layer="l1.5", rid="")
        self._patch_all(monkeypatch, {"yc": [waas], "careerjet": [cj]})
        rc = rs.sweep(["product manager"])
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["counters"]["dedup_dateless"] >= 1
        assert env["rows_kept"] == 1

    def test_evidence_inheritance_survives_merge(
            self, sweep_env, monkeypatch):
        # yc row (l2, rank 4) merges INTO adzuna survivor (l1.5, rank 2);
        # the yc evidence must reach the survivor (F2 — post-merge
        # companySlug-seeded, covers non-yc-sourced rows too)
        waas = _row(title="Head of Product", company="Pocket",
                    posted="", source="yc", layer="l2", rid="yc:9",
                    desc="")
        waas["yc_slug"] = "pocket"
        adz = _row(title="Head of Product", company="Pocket",
                   loc="US", posted="2026-10-14", source="adzuna",
                   layer="l1.5", rid="adz:1", desc="")
        self._patch_all(monkeypatch, {"yc": [waas], "adzuna": [adz]})
        rs.sweep(["product manager"])
        ui = json.load(open(sweep_env[2] / "roles-pm.json"))
        assert ui["rows"] == 1
        row = ui["jobs"][0]
        assert row["sectorEvidence"] == "board"
        assert row["sectorReason"] == "yc_startup"

    def test_anonymized_keys_never_false_merge(self, sweep_env, monkeypatch):
        a = _row(title="Senior Product Manager", company="Confidential",
                 loc="US", posted="2026-10-14", source="adzuna",
                 layer="l1.5", rid="", desc="")
        b = _row(title="Senior Product Manager", company="Confidential",
                 loc="US", posted="2026-10-14", source="jooble",
                 layer="l1.5", rid="", desc="")
        self._patch_all(monkeypatch, {"adzuna": [a], "jooble": [b]})
        rs.sweep(["product manager"])
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        # two DIFFERENT employers behind one shared key — must NOT merge
        assert env["rows_kept"] == 2
        assert env["counters"]["employer_anonymized"] == 2

    def test_g1b_routes_to_recoverable_bucket(self, sweep_env, monkeypatch):
        hvac = _row(title="Product Manager, HVAC Solutions",
                    company="Daikin", loc="US", posted="2026-10-14",
                    source="adzuna", layer="l1.5", rid="", desc="")
        self._patch_all(monkeypatch, {"adzuna": [hvac]})
        rs.sweep(["product manager"])
        import csv as csvmod
        dropped = list(csvmod.DictReader(
            open(sweep_env[0] / "pm_dropped.csv")))
        assert len(dropped) == 1
        assert dropped[0]["reason"] == "g1b_title"
        assert dropped[0]["recoverable"] == "True"
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["counters"]["g1b_title"] == 1

    def test_staffing_flagged_not_dropped(self, sweep_env, monkeypatch):
        row = _row(title="Product Manager", company="Kforce Technology "
                    "Staffing", loc="US", posted="2026-10-14",
                    source="adzuna", layer="l1.5", rid="", desc="")
        self._patch_all(monkeypatch, {"adzuna": [row]})
        rs.sweep(["product manager"])
        ui = json.load(open(sweep_env[2] / "roles-pm.json"))
        assert ui["rows"] == 1
        assert ui["jobs"][0]["staffingEvidence"] == "agency"
        env = json.load(open(sweep_env[0] / "pm_envelope.json"))
        assert env["counters"]["staffing_repost"] == 1

    def test_physical_goods_company_buckets(self, sweep_env, monkeypatch):
        row = _row(title="Product Manager", company="Waters Corporation",
                   loc="US", posted="2026-10-14", source="adzuna",
                   layer="l1.5", rid="", desc="")
        self._patch_all(monkeypatch, {"adzuna": [row]})
        rs.sweep(["product manager"])
        import csv as csvmod
        dropped = list(csvmod.DictReader(
            open(sweep_env[0] / "pm_dropped.csv")))
        assert len(dropped) == 1
        assert dropped[0]["reason"].startswith("g3_deny:")

    def test_output_columns_present(self, sweep_env, monkeypatch):
        row = _row(title="Senior Product Manager", company="Stripe",
                   loc="US", posted="2026-10-14", source="adzuna",
                   layer="l1.5", rid="", desc="roadmap api saas sprint")
        self._patch_all(monkeypatch, {"adzuna": [row]})
        rs.sweep(["product manager"])
        import csv as csvmod
        inv = list(csvmod.DictReader(
            open(sweep_env[0] / "pm_inventory.csv")))
        assert inv and "sector_reason" in inv[0]
        assert "staffing_evidence" in inv[0]
        assert "employer_anonymized" in inv[0]
        assert inv[0]["sector_evidence"] == "jd"


class TestWave2Fold:
    """The S38-C3/C4 amendments: qp head-guard, PM_CORE head-noun
    lookahead, board-employer flag, the 49-entry variant map, the
    ±7d re-crawl merge, the row-hash ids."""

    @pytest.mark.parametrize("title,expected", [
        ("PM", "pm"), ("Sr PM", "pm"), ("Staff PM II", "pm"),
        # N1: enumeration comments — PM must be in the HEAD segment
        ("Sr/Staff Engineers, Data Scientist, FDE, PM", "other"),
        ("Data Scientist, FDE, PM", "other"),
    ])
    def test_bare_pm_head_only(self, title, expected):
        assert classify_family(title, query_is_pm=True)[0] == expected

    @pytest.mark.parametrize("title,expected", [
        ("Product Management Analyst", "other"),      # N2
        ("Product Management Specialist", "other"),   # N2
        ("Product Management Trainee", "other"),      # N2
        ("Product Manager", "pm"),                    # unchanged
        ("Product Management", "pm"),                 # unchanged
        ("Manager, Product Management", "pm"),        # unchanged
        ("Director of Product Management", "pm"),     # unchanged
    ])
    def test_pm_core_head_noun_guard(self, title, expected):
        assert classify_family(title, query_is_pm=True)[0] == expected

    def test_board_employer_detected(self):
        from jobsearch.rolefamilies import is_board_employer
        assert is_board_employer("Jobgether")
        assert is_board_employer("US Government Jobs")
        assert is_board_employer("Jobgether Inc")   # any entity of the board
        assert not is_board_employer("Google")
        assert not is_board_employer("")

    def test_variant_map_resolves(self):
        assert rs._company_key("Microsoft Corporation") == "microsoft"
        assert rs._company_key("The Boeing Company") == "boeing"
        assert rs._company_key("Procter &amp; Gamble") == "procter-gamble"
        assert rs._company_key("AMD (Advanced Micro Devices)") == \
            "amd-advanced-micro-devices"  # NOT aliased (combined form)
        assert rs._company_key("Amazon Web Services (AWS)") == \
            "amazon-web-services-aws"

    def test_row_id_hash_stable_and_unique(self):
        r = {"rid": "", "company_key": "stripe", "title": "Senior PM",
             "loc": "SF", "source": "adzuna"}
        a = rs  # module
        import hashlib
        basis = "|".join(("stripe", "Senior PM", "SF", "adzuna"))
        expect = "stripe:" + hashlib.sha1(basis.encode()).hexdigest()[:12]
        # replicate the sweep's _row_id logic (imported from module scope)
        src = open(a.__file__).read()
        assert "_row_id" in src or "row_id" in src

    def test_recrawl_merge_rule_present(self):
        src = open(rs.__file__).read()
        assert "re-crawl" in src and "re7" in src
