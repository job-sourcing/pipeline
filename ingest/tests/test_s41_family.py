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
    DMM_QUERY_CORE,
    DMM_QUERY_TAIL,
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
        # [S47 F17] the firecrawl transports join the dmm/gen t3 roster
        # — mocked here or the sweep test fires LIVE fleet calls
        "ziprecruiter": "fetch_ziprecruiter",
        "glassdoor": "fetch_glassdoor_zenrows",
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
    # [W2a-1 / DESIGN-S47 v2 F4] the grammar-pass additions — the 25
    # B4 titles that routed `other` (the 10-24 refresh dropped 7,264
    # family_other rows; `ad operations specialist` sat in the weekly
    # CORE-20 while its eponymous rows died at the gate):
    ("Ad Operations Specialist", "dmm"),           # weekly CORE-20 slot
    ("Ad Operations Analyst", "dmm"),
    ("Ad Trafficking Specialist", "dmm"),          # the ad-ops cluster
    ("Head of Growth", "dmm"),                     # head/vp-of-growth
    ("VP of Growth", "dmm"),
    ("User Acquisition Manager", "dmm"),           # user-acquisition core
    ("Digital Marketing Associate", "dmm"),        # ROLE_HEAD += associate
    ("Organic Search Manager", "adjacent"),         # the ADJACENT additions
    ("Social Media Analyst", "adjacent"),
    ("Content Manager", "adjacent"),
    ("Content Writer", "adjacent"),
    ("Creative Strategist", "adjacent"),
    ("Ad Creative Specialist", "adjacent"),
    ("Email Campaign Manager", "adjacent"),
    ("CRM Manager", "adjacent"),
    ("Retention Specialist", "adjacent"),
    ("Marketing Automation Specialist", "adjacent"),
    ("Marketing Technology Specialist", "adjacent"),  # spelled-out martech
    ("Martech Specialist", "adjacent"),
    ("Marketing Analytics Manager", "adjacent"),
    ("Marketing Data Analyst", "adjacent"),
    ("Affiliate Manager", "adjacent"),
    # the deny-side guards of the same pass: the widened grammar must
    # NOT leak quota-sales or supply-side rows through the new phrases
    ("Talent Acquisition Manager", "other"),        # user ≠ talent
    ("Warehouse Associate", "other"),               # associate w/o core
    ("Account Coordinator", "other"),               # sales-side (deny)
]


@pytest.mark.parametrize("title,want", DMM_CASES)
def test_dmm_grammar(title, want):
    fam, ev = classify_dmm(title)
    assert fam == want, f"{title!r}: got {fam}, want {want} ({ev})"


def test_dmm_query_census_zero_other():
    """[W2a-1] THE BAR: every DMM_QUERY_CORE+TAIL title (the 100-title
    B4 census) classifies dmm or adjacent — 0 `other`. A query whose
    eponymous rows die at the family gate burns fetch budget on rows
    that can never ship (`ad operations specialist` sat in the weekly
    CORE-20 for exactly this reason). If this pin fails after a query
    list change, widen the GRAMMAR (DEMAND_CORE/TOKEN_HEADS/ADJACENT)
    with the new titles, not just the list."""
    routes = {q: classify_dmm(q)[0]
              for q in DMM_QUERY_CORE + DMM_QUERY_TAIL}
    other = sorted(q for q, fam in routes.items() if fam == "other")
    assert not other, f"dmm census titles routing 'other': {other}"
    # and the census is meaningfully split (the grammar actually fires)
    assert sum(1 for f in routes.values() if f == "dmm") >= 40
    assert sum(1 for f in routes.values() if f == "adjacent") >= 40


# ── the registry ─────────────────────────────────────────────────────

def test_registry_has_both_families_and_distinct_paths():
    pm, dmm = family_spec("pm"), family_spec("dmm")
    assert pm["quartet_prefix"] == "pm"
    assert dmm["quartet_prefix"] == "dmm"
    assert pm["ui_file"] != dmm["ui_file"]
    assert pm["drift_label"] != dmm["drift_label"]
    assert "l0_bundle" in dmm["locked_endpoints"]   # F9-1
    # [S47 F4b/A5] 'wwr_rss' (the ROSTER name — the lock must actually
    # fire; 'wwr' never matched the registered 'wwr_rss' entry)
    for ep in ("talent", "muse", "wwr_rss", "yc", "hn", "himalayas"):
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


class TestSeedLayerForNewSources:
    """[W2a-8] ziprecruiter/glassdoor seed rows re-enter with their
    TRUE layer (l1.5, not the l2 default) and their rids INTACT — the
    rid-prefix whitelist + _SRC_LAYER both missed the two sources F17
    registered, so one union round-trip mislabeled the layer and
    stripped the strongest identity rule (rid-dedup)."""

    def test_zip_gd_seed_rows_keep_layer_and_rid(self, sweep_env,
                                                 monkeypatch):
        roles, watch, ui = sweep_env
        prior = {
            "envelope": {"ran_at": "2026-10-20T00:00:00Z"},
            "jobs": [{
                # the fetcher's own rid shape: ziprecruiter:{coslug}:…
                "title": "Office Manager", "company": "Acme SNF",
                "companyKey": "acme-snf", "loc": "US",
                "url": "https://www.ziprecruiter.com/co/Acme-SNF",
                "postedOn": "2026-10-20", "desc": "run the office",
                "source": "ziprecruiter",
                "id": "ziprecruiter:Acme-SNF:officemanager",
                "geoTier": "t3",
            }, {
                # …and glassdoor:jl{jid}
                "title": "Business Office Manager", "company":
                    "Palmetto", "companyKey": "palmetto", "loc": "US",
                "url": "https://www.glassdoor.com/job-listing/x?jl=123",
                "postedOn": "2026-10-20", "desc": "run the office",
                "source": "glassdoor", "id": "glassdoor:jl123",
                "geoTier": "t3",
            }, {
                # the zenrows-fallback id shape — a per-process salted
                # hash, must STAY stripped (row-hash ids are not rids)
                "title": "Office Coordinator", "company": "Zen Co",
                "companyKey": "zen-co", "loc": "US",
                "url": "https://www.glassdoor.com/job-listing/y?jl=9",
                "postedOn": "2026-10-20", "desc": "coordinate",
                "source": "glassdoor", "id": "gd_3_17427389",
                "geoTier": "t3",
            }],
        }
        (roles / "prior.json").write_text(json.dumps(prior))
        _patch_all(monkeypatch, {})
        rc = rs.sweep(family_spec("gen")["queries_core"],
                      seed_envelope=str(roles / "prior.json"),
                      family="gen")
        jobs = json.load(open(ui / "roles-gen.json"))["jobs"]
        assert len(jobs) == 3
        zp = next(j for j in jobs if j["source"] == "ziprecruiter"
                  and j["title"] == "Office Manager")
        gd = next(j for j in jobs if j["id"] == "glassdoor:jl123")
        assert zp["layer"] == "l1.5"          # not the l2 default
        assert gd["layer"] == "l1.5"
        # the rids survived the whitelist (rid-dedup stays armed)
        assert zp["id"] == "ziprecruiter:Acme-SNF:officemanager"
        # the gd_{n}_{hash} row-hash id stays stripped
        zen = next(j for j in jobs if j["title"] == "Office Coordinator")
        assert not zen["id"].startswith("gd_")   # row-hash, not a rid

