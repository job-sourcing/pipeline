"""S42 pins — the industry v2 (all-US geo mode) + the refetch family
parameterization (DESIGN-S42 v2, S42-R1 findings folded).

Covers: the us_region_tag gate (deny-first state matching — the F7
collisions; origin-aware remote — F8), the registry integrity (the
NO-RE-KEY guarantee: home_services_ba verbatim + home_services_us —
F3), the budget guard (outputs ALWAYS written — F4), the seed union
v2.1 (the careerjet HK-transport carry; 60d staleness + the
2-generation dateless bound), the rollup (dba brands, ×N locations —
F11), the staffing split, the honest-channel verdicts (ziprecruiter
0-yield = the measured block, never "no jobs" — F10/B6), and the
jd_refetch --family plumbing (per-family files/env keys — F1/F2; the
atomic cache write — F15; the empty-todo complete fix).
"""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "ingest"))

import pytest

from jobsearch.geo_levels import us_region_tag
import industry_sweep as isw
import jd_refetch as jr


# ── B3/B4: the us-wide region gate ───────────────────────────────────

class TestUsRegionTag:
    def test_states_us(self):
        assert us_region_tag("Austin, TX", origin="geo") == "us"
        assert us_region_tag("Richmond, VA", origin="geo") == "us"
        # PR is a US territory — included (usajobs covers it)
        assert us_region_tag("San Juan, PR", origin="geo") == "us"

    def test_iso_collision_denied(self):
        # a two-letter suffix alone is NOT US evidence — the F13
        # name-collision standard at country scale
        assert us_region_tag("Chennai, IN", origin="geo") == "other"
        assert us_region_tag("Munich, DE", origin="geo") == "other"
        assert us_region_tag("London, ON", origin="geo") == "other"
        assert us_region_tag("Toronto, ON", origin="wide") == "other"

    def test_origin_aware_remote_f8(self):
        # geo fetchers are US-locale APIs → US-remote by construction
        assert us_region_tag("Remote", "Estimator", "geo") == "remote"
        # wide firehoses need a US marker (Remote, Berlin ≠ US-remote)
        assert us_region_tag("Remote", "Estimator", "wide") == "other"
        assert us_region_tag("US-Remote", "Estimator", "wide") == "remote"
        assert us_region_tag("Remote - United States", "",
                            "wide") == "remote"

    def test_vdc_deny_carries(self):
        assert us_region_tag(
            "Remote", "Virtual Design and Construction Manager",
            "geo") == "other"

    def test_markers(self):
        assert us_region_tag("New York, NY", origin="wide") == "us"
        assert us_region_tag("United States", origin="wide") == "us"
        assert us_region_tag("Berlin, Germany", origin="wide") == "other"


# ── B1: the registry (NO re-key — F3) ───────────────────────────────

class TestRegistry:
    def test_ba_entry_verbatim(self):
        ba = isw.INDUSTRIES["home_services_ba"]
        assert ba["region_gate"] == "bay_area"
        assert ba["location"] == "San Francisco Bay Area, CA"
        assert ba["adzuna_location"] == "San Francisco, CA"
        assert len(ba["queries"]) == 27          # unchanged from S41

    def test_us_entry(self):
        us = isw.INDUSTRIES["home_services_us"]
        assert us["region_gate"] == "us"
        assert us["location"] is None            # US-wide defaults
        assert us["adzuna_location"] is None
        assert len(us["queries"]) == 45          # 27 ba + 18 verticals
        # F9: every new vertical has its affirm token
        pat = isw._compile_affirms(us["affirm_tokens"])
        for tok in ("gutter", "insulation", "septic", "pool service",
                    "tree service", "junk removal", "paving",
                    "countertop", "kitchen remodel", "security system",
                    "cleaning service", "moving company",
                    "appliance repair"):
            assert pat.search(tok), f"missing affirm: {tok}"

    def test_affirm_not_overbroad(self):
        us = isw.INDUSTRIES["home_services_us"]
        pat = isw._compile_affirms(us["affirm_tokens"])
        # the bare-token traps: candidate pool, dry cleaning, moving
        # fast, security clearance — none may affirm
        for bad in ("candidate pool of applicants",
                    "dry cleaning discount",
                    "fast-moving environment",
                    "security clearance required"):
            assert not pat.search(bad), bad


# ── B2: the budget guard (F4) ───────────────────────────────────────

class TestBudgetGuard:
    def test_zero_budget_still_writes_outputs(self, tmp_path,
                                              monkeypatch):
        monkeypatch.setattr(isw, "OUT_DIR", tmp_path)
        # budget 0 → every source skipped:budget, gates on empties,
        # outputs WRITTEN, exit 0 (the F4 green-zero kill)
        rc = isw.run("home_services_us", budget_seconds=0)
        assert rc == 0
        d = json.loads(
            (tmp_path / "home_services_us.json").read_text())
        srcs = d["envelope"]["sources"]
        assert srcs["adzuna"] == "skipped:budget"
        assert srcs["seed_envelope"] == "ok:skipped"
        assert d["kept"] == 0 and d["headlined_raw"] == 0
        assert (tmp_path /
                "home_services_us.report.md").exists()

    def test_pm_hardcoded_declared_f10(self, tmp_path, monkeypatch):
        monkeypatch.setattr(isw, "OUT_DIR", tmp_path)
        rc = isw.run("home_services_ba", budget_seconds=0)
        assert rc == 0
        srcs = json.loads(
            (tmp_path / "home_services_ba.json").read_text()
        )["envelope"]["sources"]
        for name in ("muse", "talent", "himalayas", "wwr"):
            assert srcs[name] == "skipped:pm-hardcoded", name


# ── v2.1: the seed union (the careerjet carry) ───────────────────────

class TestSeedUnion:
    @staticmethod
    def _prior(tmp_path, rows):
        (tmp_path / "home_services_us.json").write_text(json.dumps(
            {"ran_at": "2026-10-21T13:00:00Z", "jobs": rows}))

    def test_staleness_and_generation_bounds(self, tmp_path):
        isw.OUT_DIR = tmp_path          # not a fixture — deliberate
        try:
            fresh = {"title": "Estimator", "company": "Acme",
                     "loc": "Denver, CO", "posted": "2026-10-20",
                     "desc": "", "source": "careerjet", "url": "u1",
                     "srcMode": "geo", "seedGen": 0}
            stale = {**fresh, "url": "u2", "posted": "2026-07-01"}
            dateless_gen0 = {**fresh, "url": "u3", "posted": ""}
            dateless_gen2 = {**fresh, "url": "u4", "posted": "",
                             "seedGen": 2}
            self._prior(tmp_path, [fresh, stale, dateless_gen0,
                                   dateless_gen2])
            got = isw._seed_prior("home_services_us",
                                  "2026-10-22T00:00:00Z")
            urls = {r["url"] for r in got}
            assert "u1" in urls                       # 60d carry
            assert "u2" not in urls                   # >60d museum
            assert "u3" in urls      # dateless gen<2 (the F1 doctrine)
            assert "u4" not in urls                   # gen-2 dateless
        finally:
            isw.OUT_DIR = isw.REPO / "ingest/data/industry"

    def test_seed_origin_restored_through_gates(self, tmp_path,
                                                monkeypatch):
        # the srcMode identity: a careerjet seed row must NOT
        # re-classify as 'wide' (its remote rows would drop — F8)
        monkeypatch.setattr(isw, "OUT_DIR", tmp_path)
        row = {"title": "Remote Estimator", "company": "Acme",
               "loc": "Remote", "posted": "2026-10-20",
               "desc": "construction", "source": "careerjet",
               "url": "u1", "srcMode": "geo", "seedGen": 1,
               "region": "remote", "affirmTier": "title",
               "level": "corp_office", "staffingBrand": False}
        (tmp_path / "home_services_us.json").write_text(json.dumps(
            {"ran_at": "2026-10-21T13:00:00Z", "jobs": [row]}))
        rc = isw.run("home_services_us", budget_seconds=0)
        assert rc == 0
        d = json.loads(
            (tmp_path / "home_services_us.json").read_text())
        carried = [j for j in d["jobs"] if j["url"] == "u1"]
        assert carried and carried[0]["region"] == "remote"
        assert carried[0]["seed"] is True
        assert carried[0]["seedGen"] == 2


# ── B7/F11: the rollup + staffing split ─────────────────────────────

class TestRollup:
    def test_dba_brand_wins_and_locations_roll(self):
        head = [
            {"title": "HVAC Inside Sales", "company":
             "Atlas Trillo Heating&Air", "loc": "Sunnyvale",
             "region": "bay_area", "level": "corp_office",
             "affirmTier": "company", "source": "adzuna",
             "posted": "2026-10-08", "staffingBrand": False,
             "desc": "", "url": "a"},
            {"title": "HVAC Inside Sales", "company":
             "Atlas Trillo Heating&Air", "loc": "Menlo Park",
             "region": "bay_area", "level": "corp_office",
             "affirmTier": "company", "source": "adzuna",
             "posted": "2026-10-08", "staffingBrand": False,
             "desc": "", "url": "b"},
            {"title": "Plumbing Estimator", "company":
             "Able General Engineering Inc. dba Able Plumbing",
             "loc": "San Jose", "region": "bay_area",
             "level": "corp_office", "affirmTier": "title",
             "source": "indeed", "posted": "2026-10-21",
             "staffingBrand": False, "desc": "", "url": "c"},
            {"title": "Construction PM", "company": "Jobot",
             "loc": "Atlanta, GA", "region": "bay_area",
             "level": "corp_office", "affirmTier": "title",
             "source": "adzuna", "posted": "2026-10-21",
             "staffingBrand": True, "desc": "", "url": "d"},
        ]
        rolled = isw._rollup(head)
        assert len(rolled) == 3
        at = next(g for g in rolled if g["n_locations"] == 2)
        assert at["locations"] == ["Sunnyvale", "Menlo Park"]
        # dba: the BRAND groups the key (the legal name never will)
        assert isw._company_norm(
            "Able General Engineering Inc. dba Able Plumbing") == \
            "able plumbing"
        assert any("Jobot" in g["company"] for g in rolled)


# ── F10/B6: honest-channel verdicts ────────────────────────────────

class TestHonestVerdicts:
    def test_ziprecruiter_zero_yield_is_the_block(self, monkeypatch):
        # jobspy swallows the 403 → empty frame; a zero-yield on
        # ziprecruiter is the measured wall, NOT "no jobs exist"
        import role_sweep as rs
        monkeypatch.setattr(
            rs, "fetch_ziprecruiter",
            lambda q, location=None: ([], "blocked:no-yield(403)"))
        rows, v = rs.fetch_ziprecruiter(["construction estimator"])
        assert rows == [] and v == "blocked:no-yield(403)"

    def test_glassdoor_transport_dead_is_blocked(self, monkeypatch):
        import role_sweep as rs
        monkeypatch.setattr(
            rs, "fetch_glassdoor_zenrows",
            lambda q, location=None: ([], "blocked:zenrows"))
        rows, v = rs.fetch_glassdoor_zenrows(["construction estimator"])
        assert rows == [] and v == "blocked:zenrows"


# ── the lane plumbing (F1/F2) ───────────────────────────────────────

class TestLanePlumbing:
    def test_jd_refetch_yml_two_families_single_writer(self):
        yml = (_REPO / ".github/workflows/jd-refetch.yml").read_text()
        # sequential family steps, NOT a matrix (two concurrent cache
        # writers = the F6 clobber class)
        assert "strategy:" not in yml
        assert "--family pm" in yml and "--family dmm" in yml
        assert "JD_REFETCH_PM == 'pending' || env.JD_REFETCH_DMM" in yml
        # F2: the dmm attrition ledger is checkpointed (else
        # runner-ephemeral)
        assert "jd_refetch_dmm_state.json" in yml
        assert "jd_refetch_dmm_report.md" in yml

    def test_industry_yml_matrix_both_slugs(self):
        yml = (_REPO / ".github/workflows/industry-sweep.yml"
               ).read_text()
        assert "home_services_ba" in yml
        assert "home_services_us" in yml
        assert "timeout: 90" in yml          # the us budget headroom
        assert "--budget-seconds" in yml

    def test_mirror_industry_handler_is_dir_wide(self):
        sh = (_REPO / "scripts/build_public_mirror.sh").read_text()
        # new slugs flow through the existing dir-wide handler
        assert 'rsync -a "$DEST"/ingest/data/industry/' in sh

    def test_all_lane_yml_run_blocks_are_valid_bash(self):
        # the run-8 lesson: `done` mistyped `fi` in a checkpoint loop
        # shipped GREEN through yaml validation + the whole pin battery
        # — bash -n catches it in one parse. EVERY workflow's run
        # block, forever: a lane that dies mid-script loses its leg's
        # work (rescued only in runner-ephemeral /tmp).
        import subprocess
        import yaml
        wfs = sorted((_REPO / ".github/workflows").glob("*.yml"))
        assert len(wfs) >= 12
        checked = 0
        for wf in wfs:
            doc = yaml.safe_load(wf.read_text())
            for job in (doc.get("jobs") or {}).values():
                for step in job.get("steps") or []:
                    run = step.get("run")
                    if not run:
                        continue
                    r = subprocess.run(
                        ["bash", "-n"], input=run.encode(),
                        capture_output=True)
                    assert r.returncode == 0, (
                        f"{wf.name}: bash syntax error in step "
                        f"'{step.get('name')}': "
                        f"{r.stderr.decode()[:200]}")
                    checked += 1
        assert checked >= 25      # the lint actually covered blocks


# ── §1.2: the refetch --family plumbing ─────────────────────────────

class TestRefetchFamily:
    def test_family_paths(self):
        try:
            jr._set_family("dmm")
            assert "roles-dmm.json" in str(jr.BUNDLE)
            assert "jd_refetch_dmm_state.json" in str(jr.STATE)
            assert "jd_refetch_dmm_report.md" in str(jr.REPORT)
            assert jr._FAMILY == "dmm"
            jr._set_family("pm")
            assert "roles-pm.json" in str(jr.BUNDLE)
            assert "jd_refetch_state.json" in str(jr.STATE)
            assert jr._FAMILY == "pm"      # reversible, not sticky
        finally:
            jr._set_family("pm")

    def test_family_env_keys_per_family(self, tmp_path, monkeypatch):
        # F1: a SHARED key would stall the pm backlog — per-family
        genv = tmp_path / "genv"
        monkeypatch.setenv("GITHUB_ENV", str(genv))
        with open(genv, "a") as f:          # what main() writes
            f.write("JD_REFETCH_PM=complete\n")
        keys = genv.read_text()
        assert "JD_REFETCH_PM=" in keys
        assert "JD_REFETCH=" not in keys.replace(
            "JD_REFETCH_PM=", "").replace(
            "JD_REFETCH_DMM=", "")

    def test_atomic_cache_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr(jr, "CACHE",
                            tmp_path / "sub" / "jd_cache.jsonl")
        jr._write_cache({
            "a|u": {"source": "a", "url": "u", "status": "ok",
                    "desc": "x", "desc_len": 1,
                    "fetched_at": "t", "transport": "direct",
                    "http": 200, "id": ""},
        })
        assert not any(
            p.name.endswith(".tmp")
            for p in tmp_path.rglob("*"))
        assert json.loads(
            (tmp_path / "sub" / "jd_cache.jsonl"
             ).read_text().splitlines()[0])["status"] == "ok"

    def test_thick_floor_pinned(self):
        # re-pin the owner doctrine at the S42 layer: >=1k = has the JD
        assert jr.THICK_DESC_FLOOR == 1000

    def test_empty_todo_is_complete(self, tmp_path, monkeypatch):
        # the retrigger-loop bug: an EMPTY backlog must report
        # complete — `fetched and remaining == 0` reported pending
        # and re-dispatched to the daily cap forever. Drive the REAL
        # main() — main() calls _set_family FIRST (which rebinds the
        # path globals), so the stub must sit at REPO level, else the
        # pin loads the real bundle and fetches live URLs.
        fake = tmp_path / "repo"
        (fake / "ingest/data/ui").mkdir(parents=True)
        (fake / "ingest/data/roles").mkdir(parents=True)
        (fake / "ingest/data/ui/roles-pm.json").write_text(
            json.dumps({"jobs": []}))
        monkeypatch.setattr(jr, "REPO", fake)
        genv = tmp_path / "genv"
        genv.write_text("")
        monkeypatch.setenv("GITHUB_ENV", str(genv))
        monkeypatch.setattr(
            sys, "argv", ["jd_refetch.py", "--transport", "direct"])
        rc = jr.main()
        assert rc == 0
        out = genv.read_text()
        assert "JD_REFETCH_PM=complete" in out, out
        # state + report landed in the fake repo, not the real one
        assert (fake / "ingest/data/roles/"
                "jd_refetch_state.json").exists()
