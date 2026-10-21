"""S41 pins — Strategy 2: the industry×region sweep (DESIGN-S41 v2 §3).

Covers: the shared geo_levels module (F13 — VDC deny, city-list gaps,
name-collision cities, PINS AT EXTRACTION since none existed), the
industrial level taxonomy (F11 — token guards, qualified licensed
forms, unclassified default), the affirm gate (F12 — boundary matching,
1099-boilerplate, desc-tier), the fetcher geo-param bit-identity
(default None = the exact pre-S41 request), and the S35 plumbing
invariants (gitignore exception F16 + the mirror handler shipped in
the same change).
"""
import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "ingest"))

import pytest

from jobsearch.geo_levels import (
    BAY_AREA,
    region_tag,
    retail_level_tag,
    industrial_level_tag,
)


# ── F13: the region gate ────────────────────────────────────────────

class TestRegionTag:
    def test_vdc_deny_f13(self):
        # "Virtual Design and Construction" is an ON-SITE role — never
        # remote, even with 'virtual' in the title/loc blob
        assert region_tag(
            "Foster City, CA",
            "Virtual Design and Construction (VDC) Manager") == \
            "bay_area"          # the loc still places it — but not remote
        assert region_tag(
            "Various, US",
            "Senior Virtual Design & Construction Engineer") == "other"
        assert region_tag(
            "", "VDC Coordinator (Virtual Design in Construction)") \
            == "other"

    def test_remote_positive_and_negative(self):
        assert region_tag("", "Remote - Growth Marketing Manager") == \
            "remote"
        assert region_tag("Remote, USA", "Estimator") == "remote"
        assert region_tag("Hybrid - Oakland, CA", "Dispatcher") == \
            "bay_area"
        assert region_tag("On-site, San Jose CA", "Superintendent") == \
            "bay_area"

    def test_lane1_collision_cities_stay_ca_gated(self):
        for city in ("Antioch, TN", "Dublin, OH", "Newark, NJ",
                     "Brentwood, TN", "Richmond, KY", "Lafayette, MS",
                     "Martinez, GA", "Oakley, MI"):
            assert region_tag(city, "") == "other", city
        for city in ("Antioch, CA", "Dublin, CA", "Richmond, CA",
                     "Brentwood, CA", "Lafayette, CA", "Oakley, CA"):
            assert region_tag(city, "") == "bay_area", city

    def test_f13_new_cities_in_list(self):
        for city in ("san leandro", "san carlos", "belmont",
                     "san bruno", "brisbane", "east palo alto",
                     "windsor", "healdsburg"):
            assert city in BAY_AREA
        assert region_tag("San Leandro, CA", "") == "bay_area"
        assert region_tag("East Palo Alto, California", "") == "bay_area"

    def test_no_ca_context_other(self):
        assert region_tag("Napa, IL", "") == "other"
        assert region_tag("Saratoga, WY", "") == "other"
        assert region_tag("Saratoga, CA", "") == "bay_area"


# ── F11: the industrial level taxonomy ──────────────────────────────

LEVEL_CASES = [
    # corp_office (the head-lined cut)
    ("Construction Estimator", "corp_office"),
    ("Senior Estimator", "corp_office"),
    ("Plumbing Dispatcher", "corp_office"),
    ("Construction Project Coordinator", "corp_office"),
    ("Assistant Project Manager", "corp_office"),
    ("Office Manager", "corp_office"),
    ("Bookkeeper", "corp_office"),
    ("Accounts Payable Specialist", "corp_office"),
    ("Customer Service Representative", "corp_office"),
    ("Inside Sales Representative", "corp_office"),
    ("Appointment Setter", "corp_office"),
    ("Purchasing Coordinator", "corp_office"),
    ("HR Coordinator", "corp_office"),
    ("Construction Marketing Coordinator", "corp_office"),
    # F11 token guards — the labor tokens must NOT eat these
    ("Crew Scheduler", "corp_office"),
    ("Crew Dispatcher", "corp_office"),
    ("Driver Recruiter", "corp_office"),
    ("Installation Coordinator", "corp_office"),
    ("Paint Sales Representative", "corp_office"),
    # F11 qualified licensed forms — HVAC PM is corp, HVAC tech is not
    ("HVAC Project Manager", "corp_office"),
    ("Plumbing Service Manager", "corp_office"),
    ("Electrical Estimator", "corp_office"),
    ("HVAC Sales Representative", "corp_office"),
    # licensed_professional (excluded)
    ("Licensed Electrician", "licensed_professional"),
    ("Apprentice Electrician", "licensed_professional"),
    ("Journeyman Plumber", "licensed_professional"),
    ("HVAC Technician", "licensed_professional"),
    ("Licensed Contractor", "licensed_professional"),
    ("Professional Engineer", "licensed_professional"),
    ("Registered Architect", "licensed_professional"),
    ("Pest Control Applicator", "licensed_professional"),
    # pure_labor (excluded)
    ("General Laborer", "pure_labor"),
    ("Construction Helper", "pure_labor"),
    ("Drywaller", "pure_labor"),
    ("Roofer", "pure_labor"),
    ("Equipment Operator", "pure_labor"),
    ("Warehouse Associate", "pure_labor"),
    ("Flooring Installer", "pure_labor"),
    # field_mgmt (kept, secondary) — checked BEFORE corp_office (the
    # generic *Manager pattern would otherwise eat site management)
    ("Superintendent", "field_mgmt"),
    ("Foreman", "field_mgmt"),
    ("Site Manager", "field_mgmt"),
    ("Field Engineer", "field_mgmt"),
    # retail_service (excluded)
    ("Cashier", "retail_service"),
    ("Retail Sales Associate - Store #1234", "retail_service"),
    # unclassified (F11: counted default, never silent)
    ("Project Engineer", "unclassified"),
    ("BIM Coordinator", "unclassified"),
    ("Payroll Administrator", "corp_office"),
    # preconstruction = the estimating/bidding phase — office-side
    ("Preconstruction Manager", "corp_office"),
    # generic *Manager without field vocabulary = office-grade
    ("Safety Manager", "corp_office"),
]


@pytest.mark.parametrize("title,want", LEVEL_CASES)
def test_industrial_levels(title, want):
    got = industrial_level_tag(title)
    assert got == want, f"{title!r}: got {got}, want {want}"


def test_retail_level_tag_unchanged():
    assert retail_level_tag("Barista") == "retail_hourly"
    assert retail_level_tag("Store Manager") == "retail_mgmt"
    assert retail_level_tag("Software Engineer") == "corp"


# ── F12: the affirm gate ─────────────────────────────────────────────

def test_affirm_boundary_matching():
    import industry_sweep as isw
    ind = dict(isw.INDUSTRIES["home_services_ba"])
    ind["_affirm_pat"] = isw._compile_affirms(ind["affirm_tokens"])
    # company tier
    assert isw.industry_affirm(ind, "Bay Area Plumbing Inc", "Dispatcher",
                               "") == "company"
    # title tier
    assert isw.industry_affirm(ind, "Acme Corp", "Construction "
                               "Project Coordinator", "") == "title"
    # desc tier
    assert isw.industry_affirm(ind, "Acme", "Office Manager",
                              "We are a remodeling company serving "
                              "the East Bay...") == "desc"
    # F12: 1099 boilerplate NEVER affirms ("contractor" only via
    # qualified phrases)
    assert isw.industry_affirm(ind, "Law Offices of Smith", "Office "
                              "Manager", "This is an independent "
                              "contractor position per IRS 1099 "
                              "rules") == ""
    # F12: door/window/paint are boundary-matched
    assert isw.industry_affirm(ind, "Doordash Technologies", "Ops "
                              "Manager", "") == ""
    assert isw.industry_affirm(ind, "Microsoft", "Windows Platform "
                              "Program Manager", "") == ""
    # but a real window company affirms
    assert isw.industry_affirm(ind, "Bay Window Replacement Co",
                               "Office Manager", "") == "company"
    # the KNOWN residual class (documented, not fixable lexically): a
    # software product literally named "Paint" affirms at title-tier —
    # boundary matching killed the lexical class (Doordash/Windows);
    # semantic FPs ride the affirmTier label for wave-time review
    assert isw.industry_affirm(ind, "Adobe", "Paint product marketing "
                               "manager", "") == "title"


# ── F14: the fetcher geo-param bit-identity ──────────────────────────

def test_fetcher_location_default_is_bit_identical():
    import inspect
    import role_sweep as rs
    # the signature gains an optional kwarg ONLY
    for fn in (rs.fetch_adzuna, rs.fetch_jooble, rs.fetch_careerjet,
               rs.fetch_jobspy_indeed):
        sig = inspect.signature(fn)
        loc = sig.parameters.get("location")
        assert loc is not None and loc.default is None, \
            f"{fn.__name__}: location param must default to None"


# ── F16 + S35: the plumbing invariants ───────────────────────────────

def test_industry_dir_not_gitignored():
    gi = (_REPO / ".gitignore").read_text()
    assert re.search(r"^!ingest/data/industry/$", gi, re.M), \
        "the !ingest/data/industry/ exception is missing (F16 — the " \
        "lane's git add fails on run 1 without it)"


def test_mirror_has_industry_backsync_handler():
    sh = (_REPO / "scripts/build_public_mirror.sh").read_text()
    assert re.search(
        r"DEST/ingest/data/industry", sh), \
        "the S35-rule industry back-sync handler must ship in the " \
        "same change as the artifact path"


def test_industry_registry_has_the_ba_entry():
    import industry_sweep as isw
    entry = isw.INDUSTRIES["home_services_ba"]
    assert entry["location"] == "San Francisco Bay Area, CA"
    assert len(entry["queries"]) >= 20
    assert "construction estimator" in " ".join(entry["queries"])
    # the F12 qualified-phrase set
    assert "general contractor" in entry["affirm_tokens"]
    assert "contractor" not in entry["affirm_tokens"]
