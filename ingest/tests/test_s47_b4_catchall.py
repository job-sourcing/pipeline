"""S47 pins — the B4 catch-all lane: the gen family + the geo ladder.

Three surfaces pinned:
  1. classify_gen — the §3.5 deny boundary (position-aware: the deny
     phrase must occupy the head-noun slot; the office-side forms of
     the SAME industries stay kept). The FP surface is WIDER than the
     PM grammar's (every trade title ever posted) — pinned hard per
     the design's "pin hard before first full run".
  2. The query lists — GEN CORE-30/TAIL-25 (the §3.2a verbatim tables)
     + the DMM widening CORE-20/TAIL-80 (the §3.2b 100-title table).
  3. The geo ladder — the tier config mapping (scope/distance/category
     per tier x family), the --geo-ladder CLI (pm rejected, gen/dmm
     accepted), the geoTier row stamp, and the merge preference (the
     more specific tier survives an equal-layer tie).
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "ingest"))

import pytest

import role_sweep as rs
from jobsearch.rolefamilies import (
    GEN_QUERY_CORE,
    GEN_QUERY_TAIL,
    DMM_QUERY_CORE,
    DMM_QUERY_TAIL,
    FAMILIES,
    family_spec,
)

# ── 1. the gen grammar: the §3.5 boundary, position-aware ────────────

GEN_KEEP = [
    # group A office core
    "office manager", "business office manager", "office administrator",
    "office coordinator", "office assistant", "administrative assistant",
    "administrative coordinator", "receptionist", "executive assistant",
    "front desk coordinator",
    # group B finance office
    "bookkeeper", "full charge bookkeeper", "accounting assistant",
    "accounts payable specialist", "billing specialist", "payroll specialist",
    # group C coordination/ops — the OFFICE-side forms of trade industries
    "project coordinator", "operations coordinator", "service coordinator",
    "scheduling coordinator", "dispatcher", "appointment coordinator",
    "estimates coordinator", "field service coordinator",
    # group D/E/F
    "hr coordinator", "customer service coordinator", "marketing coordinator",
    "sales support coordinator",
    # positional keeps — the head noun is gen, the trade word is a MODIFIER
    "office manager for plumbing company", "hvac company office manager",
    "construction company office administrator", "dispatcher - plumbing",
    # natural variants
    "Front Desk Coordinator", "Part Time Receptionist",
    "Executive Assistant to CEO", "Account Coordinator",
    "field visit coordinator", "Warehouse Office Manager",
]

GEN_DENY = [
    # trades/labor heads — the owner's NOT-labor line, verbatim §3.5
    "electrician", "plumber", "hvac technician", "hvac installer",
    "installer", "mechanic", "technician", "welder", "carpenter",
    "laborer", "apprentice", "journeyman", "construction worker",
    "painter", "roofer", "glazier", "mason", "bricklayer",
    "drywall finisher", "concrete finisher", "framer",
    "flooring installer", "gutter installer", "solar installer",
    "fence installer", "landscaper", "groundskeeper", "tree trimmer",
    "arborist", "pest control technician", "pool technician",
    "septic technician", "chimney sweep", "insulation installer",
    "countertop installer", "equipment operator", "forklift operator",
    "warehouse associate", "warehouse worker", "stocker",
    "delivery driver", "truck driver", "cdl driver", "courier",
    "mover", "house cleaner", "housekeeper", "janitor", "custodian",
    # clinical/licensed
    "registered nurse", "rn", "lpn", "cna", "nursing assistant",
    "medical assistant", "dental assistant", "dental hygienist",
    "pharmacist", "pharmacy technician", "physical therapist",
    "home health aide", "caregiver", "radiology tech", "teacher",
    "professor", "attorney",
    # retail floor
    "store manager", "assistant store manager", "retail sales associate",
    "cashier", "shift supervisor", "key holder", "barista", "server",
    "line cook", "food prep", "host", "merchandiser",
    # quota/field sales
    "outside sales representative", "in-home sales consultant",
    "canvasser", "solar sales rep",
    # decorated denies (noise forms seen live in the harvest)
    "hiring immediately HVAC technician", "Warehouse Associate II",
    "Solar Installer - Roofing Division", "Apprentice Electrician",
    "Pool Service Technician", "Assistant Store Manager, Arden Fair",
    "Experienced Plumber", "CDL Driver - Local Routes",
]


@pytest.mark.parametrize("title", GEN_KEEP)
def test_gen_keeps(title):
    fam, _ = FAMILIES["gen"]["gate"](title)
    assert fam == "gen", f"kept-title denied: {title!r}"


@pytest.mark.parametrize("title", GEN_DENY)
def test_gen_denies(title):
    fam, _ = FAMILIES["gen"]["gate"](title)
    assert fam == "other", f"deny-title leaked through: {title!r}"


def test_gen_gate_rejects_non_string():
    assert FAMILIES["gen"]["gate"](None) == ("other", "grammar")
    assert FAMILIES["gen"]["gate"]("") == ("other", "grammar")
    assert FAMILIES["gen"]["gate"](123) == ("other", "grammar")


# ── 2. the query lists — the §3.2 tables, verbatim lengths + heads ───


def test_gen_query_list_lengths():
    assert len(GEN_QUERY_CORE) == 30
    assert len(GEN_QUERY_TAIL) == 25
    assert len(set(GEN_QUERY_CORE + GEN_QUERY_TAIL)) == 55


def test_gen_query_list_heads():
    # the §3.2a anchors — first + last of each rotation
    assert GEN_QUERY_CORE[0] == "office manager"
    assert GEN_QUERY_CORE[-1] == "sales support coordinator"
    assert GEN_QUERY_TAIL[0] == "administrative specialist"
    assert GEN_QUERY_TAIL[-1] == "marketing manager"
    # the owner's own phrases from the directive
    assert "estimates coordinator" in GEN_QUERY_CORE
    assert "field visit coordinator" in GEN_QUERY_TAIL
    assert "field service coordinator" in GEN_QUERY_CORE


def test_dmm_widened_lengths():
    assert len(DMM_QUERY_CORE) == 20
    assert len(DMM_QUERY_TAIL) == 80
    assert len(set(DMM_QUERY_CORE + DMM_QUERY_TAIL)) == 100


def test_dmm_widened_anchors():
    # the v1 carry-overs stay (§3.2b ● marks) + the owner's phrases
    for q in ("performance marketing manager", "digital marketing manager",
              "paid media buyer", "growth marketing manager"):
        assert q in DMM_QUERY_CORE + DMM_QUERY_TAIL
    assert "ad creative specialist" in DMM_QUERY_CORE + DMM_QUERY_TAIL
    assert "campaign performance" not in DMM_QUERY_CORE + DMM_QUERY_TAIL  # cluster phrase, not a title
    assert "lifecycle marketing manager" in DMM_QUERY_CORE + DMM_QUERY_TAIL
    assert "amazon ppc specialist" in DMM_QUERY_CORE + DMM_QUERY_TAIL


def test_gen_family_registry_entry():
    spec = family_spec("gen")
    assert spec["ui_file"] == "roles-gen.json"
    assert spec["drift_label"] == "roles:gen"
    assert spec["quartet_prefix"] == "gen"
    # [S49-B / audit F13] the all-roles expansion landed: the l0
    # projection is WIRED for gen (sweep dispatches project_l0_gen)
    # and the startup lock is EMPTIED (the six sources are
    # family-parameterized now). dmm keeps the S47 lock unchanged —
    # that pin lives in test_s41_family.py.
    assert spec["l0_bundle"] is True
    assert spec["locked_endpoints"] == ()
    assert spec["slug"] == "gen"


# ── 3. the geo ladder — the tier config + the CLI + the stamps ──────


class TestGeoTierConfig:
    def test_t3_is_us_wide_with_no_distance(self):
        cfg = rs._geo_tier_config("t3", "gen")
        assert cfg["scopes"] == [(None, None)]
        assert cfg["remote"] is False
        assert cfg["category"] is None          # gen unfiltered at t3

    def test_t1a_covers_the_four_weekly_metros(self):
        cfg = rs._geo_tier_config("t1a", "gen")
        locs = [loc for loc, _ in cfg["scopes"]]
        assert locs == ["Sacramento, CA", "San Francisco, CA",
                        "Los Angeles, CA", "San Diego, CA"]
        assert all(dist == 30 for _, dist in cfg["scopes"])
        assert cfg["category"] == "admin-jobs"  # the metro noise cut

    def test_t1b_covers_the_four_monthly_metros(self):
        cfg = rs._geo_tier_config("t1b", "gen")
        locs = [loc for loc, _ in cfg["scopes"]]
        assert locs == ["Irvine, CA", "Riverside, CA",
                        "Fresno, CA", "Bakersfield, CA"]

    def test_t2_is_california_statewide_no_distance(self):
        cfg = rs._geo_tier_config("t2", "gen")
        assert cfg["scopes"] == [("California", 0)]
        assert cfg["category"] is None          # unfiltered at t2

    def test_t4_is_remote_flagged(self):
        cfg = rs._geo_tier_config("t4", "gen")
        assert cfg["remote"] is True
        assert cfg["category"] is None

    def test_dmm_carries_the_marketing_category_everywhere(self):
        for tier in ("t1a", "t1b", "t2", "t3", "t4"):
            cfg = rs._geo_tier_config(tier, "dmm")
            assert cfg["category"] == "pr-advertising-marketing-jobs", tier


class TestGeoLadderCli:
    def test_pm_family_is_rejected(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", [
            "role_sweep.py", "--family", "pm", "--geo-ladder", "t1a",
            "--skip", "all"])
        rc = rs.main()
        assert rc == 2
        assert "bit-identity" in capsys.readouterr().err

    def test_the_flag_is_wired_into_the_sweep_call(self):
        src = Path(rs.__file__).read_text()
        assert "geo_ladder=args.geo_ladder" in src
        assert '"--geo-ladder"' in src


class TestGeoTierMergePreference:
    def test_more_specific_tier_survives_equal_layer(self):
        metro = {"layer": "l1.5", "geoTier": "t1a"}
        uswide = {"layer": "l1.5", "geoTier": "t3"}
        # the rank order: t1a(0) < t3(3)
        assert rs.GEO_TIER_RANK["t1a"] < rs.GEO_TIER_RANK["t3"]

    def test_geo_tier_rank_order(self):
        assert (rs.GEO_TIER_RANK["t1a"] < rs.GEO_TIER_RANK["t1b"]
                < rs.GEO_TIER_RANK["t2"] < rs.GEO_TIER_RANK["t3"]
                < rs.GEO_TIER_RANK["t4"])


def test_adzuna_fetch_carries_category_and_remote_kwargs():
    import inspect
    sig = inspect.signature(rs.fetch_adzuna)
    for kw in ("location", "distance", "category", "what_and"):
        assert kw in sig.parameters


def test_jooble_and_indeed_carry_remote_kwargs():
    import inspect
    for fn in (rs.fetch_jooble, rs.fetch_jobspy_indeed):
        sig = inspect.signature(fn)
        assert "remote" in sig.parameters


def test_ui_rows_carry_geo_tier():
    src = Path(rs.__file__).read_text()
    assert '"geoTier": r.get("geoTier", "")' in src


def test_scoped_tier_skips_location_blind_sources():
    # the t1a run's fetcher roster = ONLY the 4 location-aware fetchers
    # per scope (the location-blind boards ride the lane's t3 run)
    src = Path(rs.__file__).read_text()
    assert "the geo-ladder dispatch" in src
    assert 'fetchers.append(("adzuna", fetch_adzuna' in src


class TestSeedTierPreservation:
    """[S46-B4 join-i] the cross-tier union correctness: seed rows keep
    their OWN geoTier (a t1a seed unioned into a t2 run stays t1a); the
    dispatch stamp is a SETDEFAULT so fresh rows get the run's tier
    without clobbering the seeds'."""

    def test_the_stamp_is_a_setdefault(self):
        src = Path(rs.__file__).read_text()
        assert 'r.setdefault("geoTier", geo_ladder)' in src
        # the unconditional overwrite form is retired
        assert 'r["geoTier"] = geo_ladder' not in src

    def test_the_seed_row_carries_its_tier(self):
        src = Path(rs.__file__).read_text()
        assert 'if row.get("geoTier"):' in src
        assert 'all_rows[-1]["geoTier"] = row["geoTier"]' in src

    def test_the_merge_preference_exists(self):
        src = Path(rs.__file__).read_text()
        assert "GEO_TIER_RANK.get(r.get(\"geoTier\"), 9) <" in src
        assert "SPECIFIC geo tier survives" in src


class TestT3RosterReopenings:
    """[S49, the S48 queue item 4] the firecrawl re-openings join the
    t3 roster for the B4 families ONLY: ziprecruiter + glassdoor ride
    --skip-gated, fail-soft, fleet-rotated; pm's t3 stays bit-identical
    (the identity doctrine — the additions are family-gated)."""

    def test_the_reopenings_are_wired_into_the_t3_roster(self):
        src = Path(rs.__file__).read_text()
        assert 'fetchers.append(("ziprecruiter", fetch_ziprecruiter,' in src
        assert 'fetchers.append(("glassdoor", fetch_glassdoor_zenrows,' in src
        # family-gated: pm's t3 roster is UNTOUCHED by the additions
        assert "if family in _B4_FAMILIES:\n            fetchers.append((\"ziprecruiter\"" in src

    def test_the_reopenings_are_firecrawl_transport(self):
        # both fetchers ride _fc_scrape (the fleet rotation) — the
        # transport that measured OPEN on 2026-10-24
        src = Path(rs.__file__).read_text()
        for fn in ("def fetch_ziprecruiter", "def fetch_glassdoor_zenrows"):
            start = src.index(fn)
            body = src[start:start + 3000]
            assert "_fc_scrape" in body, f"{fn} must ride the firecrawl fleet"

    def test_skip_gate_covers_the_reopenings(self):
        # the additions land BEFORE the skip_set filter — --skip
        # ziprecruiter/glassdoor disables them like any roster member
        src = Path(rs.__file__).read_text()
        anchor = 'fetchers.append(("glassdoor", fetch_glassdoor_zenrows,'
        filt = ("fetchers = [(n, f, a, s) for n, f, a, s in fetchers\n"
                "                    if n not in skip_set]")
        assert src.index(filt, src.index(anchor)) > src.index(anchor)
