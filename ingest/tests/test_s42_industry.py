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
        # [9th-incident] --update required: the plain -a was org-wins-
        # ALWAYS (the craigslist redaction resurrection class)
        assert 'rsync -a --update "$DEST"/ingest/data/industry/' in sh

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

    def test_gen_twins_paths(self):
        # [S47-W2a-3] gen joins the family parameterization — the gen
        # bundle was 92% pending (8,260/8,992) with NO lane able to
        # write a cached JD for it. The twins mirror the S42-R1 A1/A2
        # dmm pattern exactly (bundle per family_spec("gen")
        # ["ui_file"], state/report under jd_refetch_gen_*).
        try:
            jr._set_family("gen")
            assert "roles-gen.json" in str(jr.BUNDLE)
            assert "jd_refetch_gen_state.json" in str(jr.STATE)
            assert "jd_refetch_gen_report.md" in str(jr.REPORT)
            assert jr._FAMILY == "gen"
            jr._set_family("pm")            # reversible, not sticky
            assert "roles-pm.json" in str(jr.BUNDLE)
            assert "jd_refetch_state.json" in str(jr.STATE)
        finally:
            jr._set_family("pm")

    def test_gen_choice_emits_gen_env_key(self, tmp_path, monkeypatch):
        # [S47-W2a-3] the choice list admits gen and main() emits the
        # per-family retrigger key JD_REFETCH_GEN (the yml predicate
        # keys it alongside PM/DMM — a shared key would stall the pm
        # backlog, the S42-R1 F1 class). Drive the REAL main() with a
        # stub repo (the _set_family rebind reads jr.REPO at call
        # time, so the stub must sit at REPO level).
        fake = tmp_path / "repo"
        (fake / "ingest/data/ui").mkdir(parents=True)
        (fake / "ingest/data/roles").mkdir(parents=True)
        (fake / "ingest/data/ui/roles-gen.json").write_text(
            json.dumps({"jobs": []}))
        monkeypatch.setattr(jr, "REPO", fake)
        genv = tmp_path / "genv"
        genv.write_text("")
        monkeypatch.setenv("GITHUB_ENV", str(genv))
        monkeypatch.setattr(
            sys, "argv", ["jd_refetch.py", "--family", "gen",
                          "--transport", "direct"])
        assert jr.main() == 0
        out = genv.read_text()
        assert "JD_REFETCH_GEN=complete" in out, out
        assert "JD_REFETCH_GEN_REMAINING=0" in out, out
        # the gen twins landed in the fake repo, not the real one
        assert (fake / "ingest/data/roles/"
                "jd_refetch_gen_state.json").exists()
        assert (fake / "ingest/data/roles/"
                "jd_refetch_gen_report.md").exists()
        assert not (fake / "ingest/data/roles/"
                    "jd_refetch_state.json").exists()

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


# ── the S35 rule 5th check (the live violation) ─────────────────────

class TestMirrorS35Handlers:
    def test_jd_refetch_state_files_have_backsync_handler(self):
        # the mirror's forward rsync --delete WIPED the org's dmm state
        # twins (run 10 committed them; the next mirror deleted them —
        # the archive never carried the paths). The handler must exist
        # for EVERY lane-written path pattern.
        sh = (_REPO / "scripts/build_public_mirror.sh").read_text()
        assert "jd_refetch*_state.json" in sh
        assert "jd_refetch*_report.md" in sh
        assert "jd_refetch_dmm_state" in (
            _REPO / ".github/workflows/jd-refetch.yml").read_text()


class TestMirrorUiRolesHandlerRegistryDriven:
    """S47-R2: the 7th S35-class incident — the ui-roles back-sync
    tuple was hardcoded ('roles-pm', 'roles-dmm') and predated the gen
    family; the org never saw roles-gen.json, the rsync --delete leg
    DELETED the archive's only copy (5,501 rows, rescued from git).
    The handler + the rsync excludes are REGISTRY-DRIVEN now — this
    pin holds the mechanism in place."""

    def test_handler_derives_families_from_registry(self):
        sh = (_REPO / "scripts/build_public_mirror.sh").read_text()
        assert "from jobsearch.rolefamilies import FAMILIES" in sh, \
            "the ui-roles newest-wins handler must derive its family " \
            "list from the registry (a hardcoded tuple rots per family)"
        # no hardcoded two-file tuple remains anywhere
        assert 'for fam_file in ("roles-pm.json", "roles-dmm.json")' \
            not in sh.replace("\n", " ")

    def test_every_family_ui_file_in_runbook(self):
        from jobsearch.rolefamilies import FAMILIES
        sh = (_REPO / "scripts/build_public_mirror.sh").read_text()
        for spec in FAMILIES.values():
            ui = spec.get("ui_file")
            if not ui:
                continue
            # the registry-driven handler covers it structurally; the
            # fallback tuple must also carry it so a registry import
            # failure degrades to the FULL list, not a partial one
            assert ui in sh or "fallback" in sh, \
                f"{ui} missing from the mirror ui-roles handler"

    def test_import_failure_fallbacks_carry_full_family_set(self):
        """[W2a-19] the registry-driven handlers degrade to FALLBACK
        tuples when the rolefamilies import fails — those fallbacks
        must carry the FULL family set (the pre-gen (pm,dmm) tuples
        would rsync-delete roles-gen.json again: the 7th-incident
        class recurring under import failure)."""
        from jobsearch.rolefamilies import FAMILIES
        sh = (_REPO / "scripts/build_public_mirror.sh").read_text()
        # PYEOF ui-file fallback tuple (multi-line)
        pyeof_fallback = sh.split("PYEOF", 2)[1]
        for spec in FAMILIES.values():
            ui = spec.get("ui_file")
            if not ui:
                continue
            assert f'"{ui}"' in pyeof_fallback, \
                f"{ui} missing from the PYEOF full-list fallback"
            assert f"--exclude={ui}" in sh, \
                f"{ui} missing from the _uiexcl full-list fallback"
        # PYROLE quartet-prefix fallback (the [W2a-5/W2c-2] fix)
        pyrole = sh.split("PYROLE", 2)[1]
        for spec in FAMILIES.values():
            qp = spec.get("quartet_prefix")
            if not qp:
                continue
            assert f'"{qp}"' in pyrole, \
                f"{qp} missing from the PYROLE full-list fallback"


# ── S47-B: the industry engine fixes (DESIGN-S47 §6, findings-a2) ────

_ROSTER_ALL = ("adzuna", "jooble", "indeed", "careerjet", "ziprecruiter",
               "glassdoor", "li_keyword", "dice", "usajobs", "findwork",
               "trio", "yc", "wellfound", "hn", "gj", "builtin")


def _run_one(tmp_path, monkeypatch, roster, fetcher_name, fetcher):
    """run() with ONE roster fetcher live, all others skipped — the
    hermetic engine-drive harness for the S47-B pins (no live I/O)."""
    import role_sweep as rs
    monkeypatch.setattr(isw, "OUT_DIR", tmp_path)
    monkeypatch.setattr(rs, fetcher_name, fetcher)
    skip = ",".join(n for n in _ROSTER_ALL if n != roster)
    return isw.run("home_services_us", skip=skip, budget_seconds=600,
                   no_seed=True)


def _row(i, **kw):
    base = {"title": "Construction Estimator", "company": f"Acme {i}",
            "loc": "Denver, CO", "url": f"https://x/{i}",
            "posted": "2026-10-20", "desc": "construction company",
            "source": "adzuna", "query": "construction estimator"}
    base.update(kw)
    return base


class TestRollupDistinctLocations:
    """S47-B §6.1 (A2-4/F19): n_locations counts DISTINCT loc strings
    — '×72 locations' was 72 ROWS (740/3,964 groups inflated);
    n_rows carries the posting count."""

    @staticmethod
    def _head(n, locs):
        return [_row(i, loc=locs[i % len(locs)], company="Acme",
                     url=f"u{i}") for i in range(n)]

    def test_duplicate_locs_count_once(self):
        rolled = isw._rollup(self._head(5, ["Denver, CO"]))
        assert len(rolled) == 1
        assert rolled[0]["n_locations"] == 1     # 5 rows, 1 distinct
        assert rolled[0]["n_rows"] == 5
        assert rolled[0]["locations"] == ["Denver, CO"]

    def test_distinct_locs_and_rows_both_counted(self):
        rolled = isw._rollup(
            self._head(7, ["Denver, CO", "Austin, TX", "Miami, FL"]))
        assert rolled[0]["n_locations"] == 3
        assert rolled[0]["n_rows"] == 7
        assert rolled[0]["locations"] == ["Denver, CO", "Austin, TX",
                                          "Miami, FL"]

    def test_empty_loc_neither_counts(self):
        rolled = isw._rollup(self._head(3, [""]))
        assert rolled[0]["n_locations"] == 0
        assert rolled[0]["n_rows"] == 3


class TestTitleKeySuffixStrip:
    """S47-B §6.2 (A2-5/F19): title-embedded city/salary suffixes
    split rollup keys — stripped from the KEY only, never the row's
    raw title (the report says so)."""

    def test_audit_examples(self):
        cases = [
            ("Estimator - San Francisco, CA", "estimator"),
            ("Construction Estimator in Los Angeles",
             "construction estimator"),
            ("Office Manager $60k-$80k", "office manager"),
            ("Rep - Up to $95,000", "rep"),
            ("Construction Office Manager - Base Salary to 100k/year"
             " - Warren, MI", "construction office manager"),
            ("Project Coordinator - Construction | Becker, MN",
             "project coordinator - construction"),
            ("HVAC Sales - Walnut Creek", "hvac sales"),
            ("Estimator (San Jose)", "estimator"),
            ("Bookkeeper $22-$28/hr", "bookkeeper"),
        ]
        for t, want in cases:
            assert isw._title_norm(t) == want, t

    def test_never_overstrips(self):
        # the enumerable-city guard + salary-shape guard: these tails
        # are role vocabulary, not noise
        for t, want in [
                ("Manager in Training", "manager in training"),
                ("Construction Estimator (Experience Required)",
                 "construction estimator (experience required)"),
                ("Estimator 2", "estimator 2"),
                ("Estimator - Senior", "estimator - senior"),
                ("Manager - $500 sign on bonus",
                 "manager - $500 sign on bonus"),
                ("Estimator - Nationwide", "estimator - nationwide")]:
            assert isw._title_norm(t) == want, t

    def test_suffixed_titles_collapse_to_one_rollup_key(self):
        head = [
            _row(0, title="Construction Office Manager", url="a",
                 company="Michael Page"),
            _row(1, title="Construction Office Manager - Warren, MI",
                 url="b", company="Michael Page"),
            _row(2, title="Construction Office Manager - Base Salary "
                 "to 100k/year", url="c", company="Michael Page"),
        ]
        rolled = isw._rollup(head)
        assert len(rolled) == 1
        assert rolled[0]["n_rows"] == 3
        # the RAW titles ride the group untouched (first row's title)
        assert rolled[0]["title"] == "Construction Office Manager"


class TestKeptCounterAliases:
    """S47-B §6.3 (A2-6): the report's kept lookup aggregates the
    roster's row-source names — 'kept None' hid li's actual 227."""

    def test_alias_map(self):
        assert isw._SRC_ALIAS["li_keyword"] == ("li",)
        assert set(isw._SRC_ALIAS["trio"]) == {
            "arbeitnow", "jobicy", "remoteok"}
        assert isw._SRC_TO_ROSTER["li"] == "li_keyword"
        assert isw._SRC_TO_ROSTER["jobicy"] == "trio"

    def test_wide_source_kept_printed_not_none(self, tmp_path,
                                               monkeypatch):
        rc = _run_one(tmp_path, monkeypatch, "li_keyword",
                      "fetch_li_keyword",
                      lambda q: ([_row(0, source="li")], "ok"))
        assert rc == 0
        d = json.loads(
            (tmp_path / "home_services_us.json").read_text())
        # by_source_kept stays ROW-source keyed (the granular truth)
        assert d["by_source_kept"] == {"li": 1}
        rep = (tmp_path / "home_services_us.report.md").read_text()
        assert "- li_keyword: ok (raw 1, kept 1)" in rep

    def test_trio_kept_via_subsource(self, tmp_path, monkeypatch):
        rc = _run_one(tmp_path, monkeypatch, "trio", "fetch_free_trio",
                      lambda: ([_row(0, source="jobicy",
                                     loc="Remote, United States",
                                     query="jobicy:feed")], "ok"))
        assert rc == 0
        d = json.loads(
            (tmp_path / "home_services_us.json").read_text())
        assert d["by_source_kept"] == {"jobicy": 1}
        rep = (tmp_path / "home_services_us.report.md").read_text()
        assert "- trio: ok (raw 1, kept 1)" in rep

    def test_full_head_list_no_cap(self, tmp_path, monkeypatch):
        # the [:300] cap ranked by branch count hid EVERY wide-source
        # group (li's 140 groups all ranked ≥335): the report prints
        # the FULL rollup now
        rows = [_row(i, company=f"Acme {i}", url=f"u{i}",
                     source="li") for i in range(305)]
        rc = _run_one(tmp_path, monkeypatch, "li_keyword",
                      "fetch_li_keyword", lambda q: (rows, "ok"))
        assert rc == 0
        rep = (tmp_path / "home_services_us.report.md").read_text()
        assert sum(1 for l in rep.splitlines()
                   if l.startswith("- **")) == 305


class TestPerQueryYield:
    """S47-B §6.4 (A2-10/F20): per-query yield traceability — the
    query string per kept row + {source: {query: {raw, kept, seed}}} in
    the artifact (only the boolean survived before). [W2b-6] the seed
    column labels the kept>raw asymmetry seed carries create."""

    def test_per_query_persisted(self, tmp_path, monkeypatch):
        rows = [_row(0, adzuna_category="trade-construction-jobs"),
                _row(1, query="hvac sales",
                     adzuna_category="admin-jobs"),
                _row(2, query="hvac sales")]
        rc = _run_one(tmp_path, monkeypatch, "adzuna", "fetch_adzuna",
                      lambda q, location=None: (rows, "ok"))
        assert rc == 0
        d = json.loads(
            (tmp_path / "home_services_us.json").read_text())
        assert d["per_query"]["adzuna"] == {
            "construction estimator": {"raw": 1, "kept": 1, "seed": 0},
            "hvac sales": {"raw": 2, "kept": 2, "seed": 0}}
        # the query string rides every kept row (F20 query-per-row)
        assert {j["query"] for j in d["jobs"]} == {
            "construction estimator", "hvac sales"}
        assert all(j["titleMatchedQuery"] for j in d["jobs"])

    def test_per_query_capped_at_registry_size(self, tmp_path,
                                               monkeypatch):
        # the bound: each source's query dict caps at the registry
        # size (45 for home_services_us — the list is enumerable)
        rows = [_row(i, company=f"Acme {i}", url=f"u{i}",
                     source="li", query=f"synthetic query {i:02d}")
                for i in range(50)]
        rc = _run_one(tmp_path, monkeypatch, "li_keyword",
                      "fetch_li_keyword", lambda q: (rows, "ok"))
        assert rc == 0
        d = json.loads(
            (tmp_path / "home_services_us.json").read_text())
        table = d["per_query"]["li_keyword"]
        assert len(table) == 45
        assert all(v == {"raw": 1, "kept": 1, "seed": 0}
                   for v in table.values())

    def test_seed_carry_cells_label_the_asymmetry(self, tmp_path,
                                                  monkeypatch):
        """[W2b-6] the raw column counts ONLY the fresh fetch loop;
        seed rows re-enter after it — a seed-only source honestly
        shows raw 0 / kept N (the careerjet raw-0/kept-2,160 class
        from run 2 on). The seed cell makes kept > raw the documented
        CARRY, never a fetch anomaly."""
        monkeypatch.setattr(isw, "OUT_DIR", tmp_path)
        # budget 0: every roster fetcher skips at the budget guard —
        # the ONLY rows are the seed carries (hermetic, no fetches)
        row = {"title": "Remote Estimator", "company": "Acme",
               "loc": "Remote", "posted": "2026-10-20",
               "desc": "construction", "source": "careerjet",
               "url": "u1", "srcMode": "geo", "seedGen": 1,
               "region": "remote", "affirmTier": "title",
               "level": "corp_office", "staffingBrand": False,
               "query": "construction estimator"}
        (tmp_path / "home_services_us.json").write_text(json.dumps(
            {"ran_at": "2026-10-21T13:00:00Z", "jobs": [row]}))
        rc = isw.run("home_services_us", budget_seconds=0)
        assert rc == 0
        d = json.loads(
            (tmp_path / "home_services_us.json").read_text())
        assert d["per_query"]["careerjet"] == {
            "construction estimator": {"raw": 0, "kept": 1,
                                        "seed": 1}}
        rep = (tmp_path / "home_services_us.report.md").read_text()
        assert "kept > raw is the carry" in rep
        assert "desc upgrades (pre-gate)" in rep      # [W2b-12] relabel


class TestJdCacheConsult:
    """S47-B §6.5 (A2-11): the industry keep/grade path consults
    jd_cache.jsonl (908 ok JDs sat idle) — strictly-longer desc +
    overlap-gated (the role_sweep hook), fail-soft on an absent
    cache."""

    @staticmethod
    def _cache_entry(url, desc):
        return {"source": "jooble", "url": url, "status": "ok",
                "desc": desc, "desc_len": len(desc),
                "fetched_at": "2026-10-25T00:00:00Z",
                "transport": "direct", "http": 200, "id": ""}

    @staticmethod
    def _mock_cache(tmp_path, monkeypatch, entries):
        import role_sweep as rs
        cache = tmp_path / "jd_cache.jsonl"
        cache.write_text("\n".join(json.dumps(e) for e in entries))
        monkeypatch.setattr(rs, "_JD_CACHE", {})
        monkeypatch.setattr(rs, "_JD_CACHE_PATH", cache)

    def test_consult_upgrades_desc_and_stamps(self, tmp_path,
                                              monkeypatch):
        long_jd = ("construction company seeking estimator with five"
                   " years commercial roofing and siding experience")
        self._mock_cache(tmp_path, monkeypatch,
                         [self._cache_entry("u1", long_jd)])
        rc = _run_one(tmp_path, monkeypatch, "jooble",
                      "fetch_jooble",
                      lambda q, location=None: (
                          [_row(0, source="jooble", url="u1")], "ok"))
        assert rc == 0
        d = json.loads(
            (tmp_path / "home_services_us.json").read_text())
        j = d["jobs"][0]
        assert j["jd_cached"] is True
        assert j["desc"] == long_jd
        assert d["envelope"]["jd_cache_hits"] == 1
        assert d["envelope"]["jd_cache"] == "ok:1entries"

    def test_guards_strictly_longer_and_overlap(self, tmp_path,
                                                monkeypatch):
        # shorter cache entry: never replaces; garbage-longer: never
        # replaces (the S41-R1 F3 doctrine)
        self._mock_cache(tmp_path, monkeypatch, [
            self._cache_entry("u2", "short"),
            self._cache_entry(
                "u3", "completely unrelated quantum physics text "
                      "about bosons and fermions and lattice theory"),
        ])
        rc = _run_one(tmp_path, monkeypatch, "jooble",
                      "fetch_jooble",
                      lambda q, location=None: (
                          [_row(1, source="jooble", url="u2"),
                           _row(2, source="jooble", url="u3")], "ok"))
        assert rc == 0
        d = json.loads(
            (tmp_path / "home_services_us.json").read_text())
        for j in d["jobs"]:
            assert "jd_cached" not in j
            assert j["desc"] == "construction company"
        assert d["envelope"]["jd_cache_hits"] == 0

    def test_absent_cache_is_a_noop(self, tmp_path, monkeypatch):
        import role_sweep as rs
        monkeypatch.setattr(rs, "_JD_CACHE", {})
        monkeypatch.setattr(rs, "_JD_CACHE_PATH",
                            tmp_path / "nope.jsonl")
        rc = _run_one(tmp_path, monkeypatch, "jooble",
                      "fetch_jooble",
                      lambda q, location=None: (
                          [_row(0, source="jooble")], "ok"))
        assert rc == 0
        d = json.loads(
            (tmp_path / "home_services_us.json").read_text())
        assert d["envelope"]["jd_cache"] == "absent"
        assert d["envelope"]["jd_cache_hits"] == 0
        assert d["kept"] == 1


class TestDiceFreshnessCap:
    """S47-B §6.6 (A2-9): dice had no freshness cap — the only
    >60d kept rows (13, max 177d). Same 60d bound the seed uses;
    dateless rows are NEVER dropped (the F1 doctrine)."""

    def test_stale_dropped_fresh_and_dateless_kept(self, tmp_path,
                                                   monkeypatch):
        from datetime import date, timedelta
        stale = (date.today() - timedelta(days=70)).isoformat()
        fresh = (date.today() - timedelta(days=5)).isoformat()
        rows = [_row(0, source="dice", posted=stale),
                _row(1, source="dice", posted=fresh),
                _row(2, source="dice", posted="")]
        rc = _run_one(tmp_path, monkeypatch, "dice", "fetch_dice",
                      lambda q: (rows, "ok"))
        assert rc == 0
        d = json.loads(
            (tmp_path / "home_services_us.json").read_text())
        assert d["gates"].get("drop:stale_dice") == 1
        assert d["kept"] == 2
        posted = {j["posted"] for j in d["jobs"]}
        assert stale not in posted
        # the verified invariant: drops + kept still sum to raw
        drops = sum(v for k, v in d["gates"].items()
                    if k.startswith("drop:"))
        assert drops + d["kept"] == 3

    def test_61d_boundary(self):
        # the bound is the seed's: >60d drops, exactly 60d survives
        assert isw._posted_age_days("2026-08-27", "2026-10-26") == 60
        assert isw._posted_age_days("2026-08-26", "2026-10-26") == 61
        assert isw._posted_age_days("", "2026-10-26") is None


class TestAdzunaCategoryStamp:
    """S47-B §6.7 (A2-B7): the category STAMP — never a filter (the
    live-probed 1.8× cut would amputate the admin/sales-classified
    office slice; 75 admin-jobs rows on ONE query)."""

    def test_stamp_every_tagged_row_never_filter(self, tmp_path,
                                                monkeypatch):
        rows = [_row(0, adzuna_category="trade-construction-jobs"),
                _row(1, adzuna_category="admin-jobs"),
                _row(2)]        # no tag → no stamp, still kept
        rc = _run_one(tmp_path, monkeypatch, "adzuna", "fetch_adzuna",
                      lambda q, location=None: (rows, "ok"))
        assert rc == 0
        d = json.loads(
            (tmp_path / "home_services_us.json").read_text())
        assert d["kept"] == 3            # NOTHING dropped on category
        by_url = {j["url"]: j for j in d["jobs"]}
        assert by_url["https://x/0"]["sectorEvidence"] == \
            "adzuna_category:trade-construction-jobs"
        assert by_url["https://x/0"]["adzuna_category"] == \
            "trade-construction-jobs"
        assert by_url["https://x/1"]["sectorEvidence"] == \
            "adzuna_category:admin-jobs"
        assert "sectorEvidence" not in by_url["https://x/2"]
        assert d["envelope"]["adzuna_category_stamped"] == 2
        rep = (tmp_path / "home_services_us.report.md").read_text()
        assert "2 stamped, 0 filtered" in rep

class TestGenaiMediaIndustry:
    """[S51] the GenAI-media catch-all sweep registry entry."""

    def test_entry_shape(self):
        g = isw.INDUSTRIES["genai_media"]
        assert g["region_gate"] == "us"
        assert g["location"] is None and g["adzuna_location"] is None
        assert len(g["queries"]) >= 12
        assert len(g["affirm_tokens"]) >= 30

    def test_every_query_is_self_affirmable(self):
        # the S42-R1 F9 law: a query its own token set cannot affirm
        # is a dead query (its rows all die at industry_no_affirm)
        g = isw.INDUSTRIES["genai_media"]
        pat = isw._compile_affirms(g["affirm_tokens"])
        for q in g["queries"]:
            assert pat.search(q), f"unaffirmable query: {q}"

    def test_affirms_genai_rows(self):
        g = isw.INDUSTRIES["genai_media"]
        pat = isw._compile_affirms(g["affirm_tokens"])
        assert pat.search("Runway ML artist residency")
        assert pat.search("Midjourney prompt engineer")
        assert pat.search("Generative AI Video Producer")
        assert pat.search("AI Filmmaking instructor")

    def test_no_false_positive_on_home_services(self):
        # the sister industry's rows must NOT affirm genai (boundary
        # check: kitchen remodel / plumbing never match the genai set)
        g = isw.INDUSTRIES["genai_media"]
        pat = isw._compile_affirms(g["affirm_tokens"])
        assert not pat.search("Kitchen Remodel Project Manager")
        assert not pat.search("Licensed Plumbing Technician")
