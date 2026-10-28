"""S48-B pins — the smbSignal join (B4 §4, the catch-all's remaining half).

Four surfaces pinned:
  1. The builder's normalizer+digest roundtrip — fixture names → the
     digest set → membership from the EMIT side (both sides of the join
     must hash with the ONE shared normalizer, else the join silently
     matches nothing) + the deterministic (mtime=0) write.
  2. The emit classification precedence — cslb beats dca beats dre
     beats fbn (the pool ladder), then harvest_dict / national_known /
     unknown_local / unknown — over STUB digest sets in a tmp dir.
  3. The fail-soft path — no digest files at all (a fresh clone: the
     lane runs BEFORE the builder ever runs): no crash, every row
     stamped `no_lookup`, the one-line warning.
  4. The emit paths end-to-end — a full mocked gen sweep carries
     smb_signal on the bundle rows + the counters/histogram/print; a
     full mocked pm sweep carries NO smb_signal key ANYWHERE (the
     bit-identity doctrine).
"""
import gzip
import importlib.util
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "ingest"))

import pytest

import role_sweep as rs
from jobsearch.rolefamilies import family_spec

from test_s38_role_sweep import _row, sweep_env  # noqa: F401


def _load_builder():
    """Import audit/s48/census/build_smb_lookup_sets.py as a module
    (the audit tree is not a package — the file-path import)."""
    spec = importlib.util.spec_from_file_location(
        "build_smb_lookup_sets",
        _REPO / "audit/s48/census/build_smb_lookup_sets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stub_pool_dir(base: Path, pools: dict) -> Path:
    """Tiny fixture digest sets: {class: [raw company names]} — only
    the given classes get files (a partial lookup is a real state)."""
    d = base / "census"
    d.mkdir(parents=True, exist_ok=True)
    for cls, fname in rs._SMB_POOLS:
        names = pools.get(cls)
        if names is None:
            continue
        digests = sorted(rs._smb_digest(n) for n in names)
        with gzip.open(d / fname, "wt", encoding="utf-8") as f:
            f.write("\n".join(digests) + "\n")
    return d


def _patch_all(monkeypatch, rows_by_source):
    """Neutralize every fetcher (the test_s41 pattern — a live call
    from these sweeps would be a test bug, not a flake)."""
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
        "ziprecruiter": "fetch_ziprecruiter",
        "glassdoor": "fetch_glassdoor_zenrows",
    }
    for name, attr in attr_by_name.items():
        monkeypatch.setattr(rs, attr, mk(rows_by_source.get(name, [])))


# ── 1. the builder's normalizer+digest roundtrip ──────────────────────

class TestBuilderRoundtrip:
    def test_digest_set_membership_from_the_emit_side(self):
        b = _load_builder()
        names = ["Able Plumbing, Inc.", "ABLE PLUMBING LLC",
                 "Able General Engineering dba Able Plumbing",
                 "   ", "!!!"]
        ds = b.digest_set(names)
        # ONE shared normalizer: the builder's digest == the emit
        # side's digest, and the suffix/punct/case variants fold
        assert b.digest_name("Able Plumbing, Inc.") \
            == rs._smb_digest("Able Plumbing Inc.") \
            == rs._smb_digest("able plumbing")
        assert rs._smb_digest("Able Plumbing") in ds   # the dba brand
        # degenerate names are never hashed
        assert len(ds) == 1
        # the normalizer id the manifest carries is the shared one
        assert b.NORMALIZER_ID == "industry_sweep._company_norm"

    def test_pool_write_is_sorted_and_deterministic(self, tmp_path):
        b = _load_builder()
        p1, p2 = tmp_path / "a.gz", tmp_path / "b.gz"
        b._write_digests(p1, {"bb", "aa"})
        b._write_digests(p2, {"aa", "bb"})
        assert p1.read_bytes() == p2.read_bytes()      # mtime=0
        with gzip.open(p1, "rt", encoding="utf-8") as f:
            assert [ln.strip() for ln in f] == ["aa", "bb"]


# ── 2. the emit classification precedence ──────────────────────────────

class TestPrecedence:
    def test_pool_ladder_cslb_dca_dre_fbn(self, tmp_path, monkeypatch):
        # the same company in ALL FOUR pools → cslb wins; stripping
        # the ladder top one at a time walks down dca → dre → fbn
        monkeypatch.setattr(rs, "_SMB_HARVEST", frozenset())
        for drop in range(4):
            pools = {cls: ["Sway Bros Co"]
                     for cls, _f in rs._SMB_POOLS[drop:]}
            monkeypatch.setattr(rs, "_SMB_SETS_DIR",
                                _stub_pool_dir(tmp_path / f"p{drop}",
                                               pools))
            monkeypatch.setattr(rs, "_SMB_LOOKUP", None)
            row = {"company": "Sway Bros Co", "loc": "US",
                   "geoTier": "t3", "company_key": "sway-bros-co"}
            assert rs._smb_classify(row) == rs._SMB_POOLS[drop][0], drop

    def test_harvest_national_local_unknown(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rs, "_SMB_SETS_DIR",
                            _stub_pool_dir(tmp_path,
                                           {"cslb_match": ["Other Co"]}))
        monkeypatch.setattr(rs, "_SMB_LOOKUP", None)
        monkeypatch.setattr(rs, "_SMB_HARVEST",
                            frozenset(["harvest home"]))
        # harvest_dict — the industry-harvest dictionary class (the set
        # stores NORMALIZED names; the row company folds to it)
        assert rs._smb_classify({"company": "Harvest Home Co",
                                 "loc": "US", "geoTier": "t3"}) \
            == "harvest_dict"
        # national_known — the curated brand seeds (seed_hit both-forms)
        assert rs._smb_classify({"company": "McDonald's", "loc": "US",
                                 "geoTier": "t3",
                                 "company_key": "mcdonald-s"}) \
            == "national_known"
        # unknown_local — a CA/metro-coded row hitting NO known pool
        # (by loc string, and by the CA-scoped tier alone)
        assert rs._smb_classify({"company": "Brand New Local Co",
                                 "loc": "Fresno, CA", "geoTier": "t3"}) \
            == "unknown_local"
        assert rs._smb_classify({"company": "Brand New Local Co",
                                 "loc": "US", "geoTier": "t2"}) \
            == "unknown_local"
        # unknown — nothing hits, not CA-coded
        assert rs._smb_classify({"company": "Midwest Remote Co",
                                 "loc": "Omaha, NE", "geoTier": "t3"}) \
            == "unknown"
        # an anonymized employer shell never guesses a pool
        assert rs._smb_classify({"company": "Confidential",
                                 "loc": "Sacramento, CA",
                                 "geoTier": "t1a"}) == "unknown"
        # non-CA trailing tokens never trip the CA-loc regex
        assert rs._smb_classify({"company": "Sahara Africa Co",
                                 "loc": "Africa", "geoTier": "t3"}) \
            == "unknown"


# ── 3. the fail-soft path ──────────────────────────────────────────────

class TestFailSoft:
    def test_missing_sets_degrade_to_no_lookup(self, tmp_path,
                                               monkeypatch, capsys):
        monkeypatch.setattr(rs, "_SMB_SETS_DIR", tmp_path / "empty")
        monkeypatch.setattr(rs, "_SMB_LOOKUP", None)
        (tmp_path / "empty").mkdir()
        assert rs._smb_load_lookup() == {}
        rows = [{"company": "Able Plumbing, Inc.",
                 "loc": "Sacramento, CA", "geoTier": "t1a"}]
        counters: dict = {}
        hist = rs._smb_classify_rows(rows, counters)
        assert rows[0]["smb_signal"] == "no_lookup"   # stamped, honest
        assert counters["smb_no_lookup"] == 1
        assert hist == {"no_lookup": 1}
        out = capsys.readouterr().out
        assert "build_smb_lookup_sets" in out and "no_lookup" in out

    def test_partial_lookup_classifies_present_pools(self, tmp_path,
                                                     monkeypatch):
        # only the fbn file exists → a member still matches fbn_new;
        # a non-member falls through to the loc classes (NOT
        # no_lookup — the present pools still classify)
        monkeypatch.setattr(rs, "_SMB_HARVEST", frozenset())
        monkeypatch.setattr(rs, "_SMB_SETS_DIR",
                            _stub_pool_dir(tmp_path,
                                           {"fbn_new": ["Gentle Glow"]}))
        monkeypatch.setattr(rs, "_SMB_LOOKUP", None)
        assert rs._smb_classify({"company": "Gentle Glow LLC",
                                 "loc": "US", "geoTier": "t3"}) \
            == "fbn_new"
        assert rs._smb_classify({"company": "Never Filed Co",
                                 "loc": "Chino, CA", "geoTier": "t1a"}) \
            == "unknown_local"


# ── 4. the emit paths end-to-end ────────────────────────────────────────

class TestEmitPaths:
    def test_gen_sweep_carries_smb_signal(self, sweep_env, tmp_path,
                                           monkeypatch, capsys):
        roles, watch, ui = sweep_env
        monkeypatch.setattr(rs, "_SMB_SETS_DIR",
                            _stub_pool_dir(tmp_path,
                                           {"cslb_match": ["Able Plumbing, Inc."]}))
        monkeypatch.setattr(rs, "_SMB_LOOKUP", None)
        monkeypatch.setattr(rs, "_SMB_HARVEST", frozenset())
        row = _row(title="Office Manager", company="Able Plumbing, Inc.",
                   loc="Sacramento, CA", posted="2026-10-20",
                   source="adzuna", layer="l1.5", rid="adz:SMB1",
                   url="https://adzuna.com/details/SMB1")
        _patch_all(monkeypatch, {"adzuna": [row]})
        rs.sweep(family_spec("gen")["queries_core"], family="gen")
        bundle = json.load(open(ui / "roles-gen.json"))
        assert bundle["rows"] >= 1
        sig = {j["company"]: j.get("smb_signal")
               for j in bundle["jobs"]}
        assert sig["Able Plumbing, Inc."] == "cslb_match"
        env = json.load(open(roles / "gen_envelope.json"))
        assert env["counters"]["smb_cslb_match"] >= 1
        assert env["smb_signal_histogram"].get("cslb_match") >= 1
        assert "smb_signal" in capsys.readouterr().out
        # the inventory CSV schema stays pre-S48 (extrasaction ignore)
        head = (roles / "gen_inventory.csv").read_text().splitlines()[0]
        assert "smb_signal" not in head

    def test_pm_sweep_rows_carry_no_smb_signal(self, sweep_env,
                                               monkeypatch, capsys):
        roles, watch, ui = sweep_env
        row = _row(title="Senior Product Manager", company="Stripe",
                   loc="US", source="adzuna", layer="l1.5",
                   rid="adz:PM1", url="https://adzuna.com/details/PM1")
        _patch_all(monkeypatch, {"adzuna": [row]})
        rs.sweep(family_spec("pm")["queries_core"], family="pm")
        bundle = json.load(open(ui / "roles-pm.json"))
        assert bundle["rows"] >= 1
        for j in bundle["jobs"]:
            assert "smb_signal" not in j          # the identity doctrine
        env = json.load(open(roles / "pm_envelope.json"))
        assert "smb_signal_histogram" not in env
        assert not [k for k in env["counters"]
                    if k.startswith("smb_")]
        assert "smb_signal" not in capsys.readouterr().out
