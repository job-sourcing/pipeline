"""S41 pins — the family registry + the DMM stress family (§2).

The stress test IS the parameterization: a second role family must
run through the SAME engine with zero PM-path leakage (file-level
isolation), honest locked-endpoint measurement (F9), family-scoped
merge keys (F10/F9-10), and a grammar battery hard enough to catch
the supply-side/ad-sales/organic confusions (F10).
"""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "ingest"))

import pytest

import role_sweep as rs
from jobsearch.rolefamilies import (
    classify_dmm,
    family_spec,
    title_core_family,
    title_core,
)

from test_s38_role_sweep import _row, sweep_env  # noqa: F401


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


# ── the grammar battery (F10) ────────────────────────────────────────

DMM_CASES = [
    # (title, expected family)
    # demand core — the explicit user cut
    ("Performance Marketing Manager", "dmm"),
    ("Senior Manager, Performance Marketing", "dmm"),
    ("Digital Marketing Manager", "dmm"),
    ("Digital Marketing Specialist", "dmm"),
    ("Digital Marketing Director", "dmm"),
    ("Vice President of Digital Marketing", "dmm"),
    ("Head of Digital Marketing", "dmm"),
    ("Paid Search Manager", "dmm"),
    ("Paid Social Media Buyer", "dmm"),
    ("Growth Marketing Lead", "dmm"),
    ("Search Engine Marketing Manager", "dmm"),     # F10-1 spelled form
    ("Online Marketing Coordinator", "dmm"),
    ("Digital Advertising Analyst", "dmm"),
    # token heads
    ("PPC Specialist", "dmm"),
    ("Senior PPC Manager", "dmm"),
    ("SEM Analyst", "dmm"),
    ("Ad Ops Manager", "dmm"),
    ("Ad-Ops Specialist", "dmm"),                  # F10-7 hyphen
    ("Advertising Operations Manager", "dmm"),
    ("Programmatic Trader", "dmm"),
    ("Programmatic Media Buyer", "dmm"),
    # supply side / ad sales / admin — never dmm
    ("Staff Software Engineer, Ads Infrastructure", "other"),
    ("Adtech Platform Engineer", "other"),
    ("Data Scientist, Advertising Technology", "other"),
    ("Digital Media Sales Manager", "other"),      # F10-4
    ("Advertising Sales Executive", "other"),
    ("Sales Director — Digital Marketing Department", "other"),
    ("Executive Assistant, Digital Marketing", "other"),  # F10-5
    ("Administrative Assistant (Marketing Team)", "other"),
    ("Sales Associate (Digital Marketing)", "other"),
    ("Marketing Receptionist", "other"),
    # organic / general marketing — adjacent, kept never head-lined
    ("SEO Manager", "adjacent"),
    ("SEO Content Strategist", "adjacent"),        # F10-3
    ("Content Marketing Manager", "adjacent"),
    ("Social Media Manager", "adjacent"),
    ("Email Marketing Specialist", "adjacent"),
    ("Marketing Manager", "adjacent"),              # generic w/o token
    ("Brand Manager", "adjacent"),
    ("Marketing Communications Director", "adjacent"),
    ("Media Buyer", "adjacent"),                    # F10-6 unqualified
    ("Media Planner", "adjacent"),
    ("Marketing Intern", "adjacent"),
    ("Copywriter", "adjacent"),
    # noise
    ("Barista", "other"),
    ("Product Manager", "other"),                   # PM family, not dmm
    ("Senior Java Engineer", "other"),
    ("Digital Design Manager", "other"),            # design ≠ marketing
]


@pytest.mark.parametrize("title,want", DMM_CASES)
def test_dmm_grammar(title, want):
    fam, ev = classify_dmm(title)
    assert fam == want, f"{title!r}: got {fam}, want {want} ({ev})"


# ── the registry ─────────────────────────────────────────────────────

def test_registry_has_both_families_and_distinct_paths():
    pm, dmm = family_spec("pm"), family_spec("dmm")
    assert pm["quartet_prefix"] == "pm"
    assert dmm["quartet_prefix"] == "dmm"
    assert pm["ui_file"] != dmm["ui_file"]
    assert pm["drift_label"] != dmm["drift_label"]
    assert "l0_bundle" in dmm["locked_endpoints"]   # F9-1
    for ep in ("talent", "muse", "wwr", "yc", "hn", "himalayas"):
        assert ep in dmm["locked_endpoints"]        # F9 hardcode set


def test_unknown_family_slug_raises():
    with pytest.raises(ValueError):
        family_spec("nope")


# ── merge-key family scoping (F9-10) ─────────────────────────────────

def test_title_core_pm_expansion_is_family_scoped():
    assert title_core("Digital PM") == \
        title_core_family("Digital PM", "pm")
    assert "product manager" in title_core_family("Digital PM", "pm")
    # on a dmm title the expansion must NOT fire
    assert "product manager" not in title_core_family(
        "Digital PM", "dmm")
    # generic expansions stay family-invariant
    assert title_core_family("Mktg Mgr", "dmm") == \
        title_core_family("Mktg Mgr", "pm") == "mktg manager"


# ── the isolation E2E (the stress test's core assertion) ────────────

class TestFamilyIsolation:
    def test_dmm_sweep_writes_dmm_paths_only(self, sweep_env,
                                             monkeypatch):
        """--family dmm must write the dmm quartet + roles-dmm.json and
        NEVER touch a pm_* artifact (one engine, zero leakage)."""
        roles, watch, ui = sweep_env
        row = _row(title="Digital Marketing Manager",
                   company="Acme Growth Agency", loc="US",
                   posted="2026-10-14", source="adzuna", layer="l1.5",
                   rid="adz:D1", url="https://adzuna.com/details/D1",
                   desc="Runs paid search and paid social campaigns")
        noise = _row(title="Senior Product Manager", company="TechCo",
                     loc="US", posted="2026-10-14", source="adzuna",
                     layer="l1.5", rid="adz:D2",
                     url="https://adzuna.com/details/D2",
                     desc="Owns the product roadmap")
        _patch_all(monkeypatch, {"adzuna": [row, noise]})
        rc = rs.sweep(family_spec("dmm")["queries_core"],
                      family="dmm")
        # dmm artifacts exist
        assert (roles / "dmm_envelope.json").exists()
        assert (roles / "dmm_inventory.csv").exists()
        assert (ui / "roles-dmm.json").exists()
        # pm artifacts do NOT
        assert not (roles / "pm_envelope.json").exists()
        assert not (ui / "roles-pm.json").exists()
        # the pm row was family-gated OUT; the dmm row kept
        env = json.load(open(roles / "dmm_envelope.json"))
        assert env["rows_kept"] == 1
        fams = {j["family"] for j in json.load(
            open(ui / "roles-dmm.json"))["jobs"]}
        assert fams == {"dmm"}
        # locked endpoints recorded honestly (F9)
        assert "locked_endpoints" in env["sources"]

    def test_dmm_absent_seed_is_ok_skipped(self, sweep_env,
                                           monkeypatch):
        """F17: roles-dmm.json does not exist on run 1 — the envelope
        must record ok:skipped, NOT error:FileNotFoundError (which the
        drift computation would count as a dead source)."""
        roles, watch, ui = sweep_env
        _patch_all(monkeypatch, {})
        # hermetic: point at the TMP ui dir (absent by construction).
        # The repo-relative form silently broke once the DMM lane
        # landed a real roles-dmm.json — the pin's absent-precondition
        # must never depend on live repo data.
        rc = rs.sweep(family_spec("dmm")["queries_core"],
                      seed_envelope=str(ui / "roles-dmm.json"),
                      family="dmm")
        env = json.load(open(roles / "dmm_envelope.json"))
        assert env["sources"]["seed_envelope"] == "ok:skipped"

    def test_pm_typo_seed_stays_fail_loud(self, sweep_env, monkeypatch):
        roles, watch, ui = sweep_env
        _patch_all(monkeypatch, {})
        rc = rs.sweep(["product manager"],
                      seed_envelope="ingest/data/ui/NOPE.json",
                      family="pm")
        env = json.load(open(roles / "pm_envelope.json"))
        assert env["sources"]["seed_envelope"] == \
            "error:FileNotFoundError"

    def test_default_pm_unchanged_paths(self, sweep_env, monkeypatch):
        """the default sweep stays bit-identical: pm_* artifacts,
        no locked-endpoint record."""
        roles, watch, ui = sweep_env
        row = _row(title="Senior Product Manager", company="TechCo",
                   loc="US", posted="2026-10-14", source="adzuna",
                   layer="l1.5", rid="adz:P1",
                   url="https://adzuna.com/details/P1")
        _patch_all(monkeypatch, {"adzuna": [row]})
        rc = rs.sweep(["product manager"])
        assert (roles / "pm_envelope.json").exists()
        assert not (roles / "dmm_envelope.json").exists()
        env = json.load(open(roles / "pm_envelope.json"))
        assert "locked_endpoints" not in env["sources"]
