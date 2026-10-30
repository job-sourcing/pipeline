"""S50-A pins — the seed-union board skip goes family-wide + the tail
crash hardening (the PM run 16 bilateral defect, 2026-10-29).

THE INCIDENT SHAPE: the S49-C corpus→cache join seeded jd_cache with
board entries, which made the 11 DATELESS pm board seed rows seed-
ELIGIBLE for the first time (the F1 dateless+cache admission path).
They re-enter at layer "l0" where the classify loop never stamps
family/seniority — and one donor-miss put a family-less row into
`merged`, so the post-merge histogram (`fam_hist[r["family"]]`)
threw KeyError AFTER ~20 min of fetch work. The lane died at the
tail, exactly the bilateral-defect class the v3.0 plan flags for
gha_drain (a data seam producing a state nobody re-audited).

THE FIX, two layers:
1. THE SEAM: board seed rows never re-enter for ANY family (the
   S49-B gen doctrine applied family-wide — l0 is re-projected
   fresh every run; seed copies are zero-information merge input).
2. THE TAIL: the histogram + emit read .get — a missing stamp
   surfaces as "?" bucket / null field, never a crash.
"""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "ingest"))
sys.path.insert(0, "ingest/tests")

import role_sweep as rs
from test_s38_role_sweep import _row, sweep_env  # noqa: F401
from test_s41_family import family_spec


def _patch_all(monkeypatch, rows_by_source):
    """the S38/S39 pattern (shared with test_s49_jd_fill): patch every
    fetcher + neutralize l0."""
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
    # [S50-A] gen/dmm (the _B4_FAMILIES) also register zip/gd — an
    # unpatched pair would do REAL network in a gen sweep
    monkeypatch.setattr(rs, "fetch_ziprecruiter", mk([]))
    monkeypatch.setattr(rs, "fetch_glassdoor_zenrows", mk([]))


class TestSeedBoardSkipFamilyWide:
    """Layer 1 — the seam. Board rows must never re-enter via the
    seed, for pm exactly like gen (S49-B), and the churned-twin shape
    (a board seed row whose corpus twin is gone) must neither crash
    nor resurrect the posting."""

    def _seed(self, url, posted, family="pm"):
        return {
            "envelope": {"ran_at": "2026-10-28T00:00:00Z"},
            "jobs": [{
                "title": "Staff Product Manager, Payments",
                "company": "airwallex", "companyKey": "airwallex",
                "loc": "US", "url": url, "postedOn": posted,
                "desc": "own the payments roadmap " * 30,
                "source": "board",
                "id": "airwallex:boardrow1",
                "family": family, "seniority": "senior",
                "usStatus": "us",
            }],
        }

    def test_fresh_pm_board_seed_with_no_twin_neither_crashes_nor_reenters(
            self, sweep_env, monkeypatch):
        """THE incident pin: a 60d-fresh board seed row + an EMPTY l0
        projection (the churn shape — the corpus twin is gone) must
        complete the sweep (no KeyError) and must NOT re-enter the
        bundle (l0 attrition is by corpus verdict, not seed)."""
        roles, watch, ui = sweep_env
        seed = self._seed("https://boards.airwallex.com/j/1",
                          "2026-10-27")
        (roles / "prior.json").write_text(json.dumps(seed))
        _patch_all(monkeypatch, {})
        monkeypatch.setattr(rs, "project_l0", lambda: ([], "ok"))
        rc = rs.sweep(family_spec("pm")["queries_core"],
                      seed_envelope=str(roles / "prior.json"),
                      family="pm")
        jobs = json.load(open(ui / "roles-pm.json"))["jobs"]
        assert not [j for j in jobs
                    if j["url"] == "https://boards.airwallex.com/j/1"]
        env = json.load(open(roles / "pm_envelope.json"))
        assert "?" not in env["family_histogram"]

    def test_dateless_pm_board_seed_with_cache_never_reenters(
            self, sweep_env, monkeypatch):
        """THE PM-16 exact shape: a DATELESS board seed row + a
        corpus-joined jd_cache entry for (board, url) + NO fresh l0
        twin. Pre-S50-A this row was admitted (the F1 dateless+cache
        path) and crashed the histogram; now it must stay out."""
        roles, watch, ui = sweep_env
        url = "https://boards.bytedance.com/j/ppm-compute-ai-infra"
        seed = self._seed(url, "")
        (roles / "prior.json").write_text(json.dumps(seed))
        # the corpus-joined cache entry (the S49-C join's exact shape)
        p = roles / "jd_cache.jsonl"
        p.write_text(json.dumps({
            "source": "board", "url": url, "id": "bytedance:X1",
            "status": "ok", "desc": "own the AI infra roadmap " * 40,
            "desc_len": 640, "transport": "corpus_join",
            "fetched_at": "2026-10-29T00:00:00Z", "http": 200,
        }) + "\n")
        monkeypatch.setattr(rs, "_JD_CACHE", {})
        monkeypatch.setattr(rs, "_JD_CACHE_PATH", p)
        _patch_all(monkeypatch, {})
        monkeypatch.setattr(rs, "project_l0", lambda: ([], "ok"))
        rc = rs.sweep(family_spec("pm")["queries_core"],
                      seed_envelope=str(roles / "prior.json"),
                      family="pm")
        jobs = json.load(open(ui / "roles-pm.json"))["jobs"]
        assert not [j for j in jobs if j["url"] == url]

    def test_gen_board_seed_still_skipped(self, sweep_env, monkeypatch):
        """the S49-B behavior is PRESERVED under the family-wide skip
        (gen board seeds never re-entered; still must not)."""
        roles, watch, ui = sweep_env
        seed = self._seed("https://boards.airwallex.com/j/2",
                          "2026-10-27", family="gen")
        (roles / "prior.json").write_text(json.dumps(seed))
        _patch_all(monkeypatch, {})
        monkeypatch.setattr(rs, "project_l0", lambda: ([], "ok"))
        rs.sweep(family_spec("gen")["queries_core"],
                 seed_envelope=str(roles / "prior.json"),
                 family="gen")
        jobs = json.load(open(ui / "roles-gen.json"))["jobs"]
        assert not [j for j in jobs
                    if j["url"] == "https://boards.airwallex.com/j/2"]

    def test_non_board_seed_rows_still_reenter(self, sweep_env,
                                               monkeypatch):
        """the skip is board-scoped: an adzuna seed row still re-enters
        (the careerjet-doctrine union — the whole point of the seed)."""
        roles, watch, ui = sweep_env
        seed = {
            "envelope": {"ran_at": "2026-10-28T00:00:00Z"},
            "jobs": [{
                "title": "Senior Product Manager", "company": "Stripe",
                "companyKey": "stripe", "loc": "US",
                "url": "https://www.adzuna.com/details/123",
                "postedOn": "2026-10-27", "desc": "own apis",
                "source": "adzuna", "id": "adz:123",
            }],
        }
        (roles / "prior.json").write_text(json.dumps(seed))
        _patch_all(monkeypatch, {})
        monkeypatch.setattr(rs, "project_l0", lambda: ([], "ok"))
        rs.sweep(family_spec("pm")["queries_core"],
                 seed_envelope=str(roles / "prior.json"),
                 family="pm")
        jobs = json.load(open(ui / "roles-pm.json"))["jobs"]
        assert [j for j in jobs
                if j["url"] == "https://www.adzuna.com/details/123"]


class TestTailHardening:
    """Layer 2 — defense in depth. A merged row that somehow reaches
    the emit without family/seniority ships null + a "?" histogram
    bucket; the sweep NEVER dies at the tail again."""

    def test_unstamped_l0_row_ships_null_not_crash(self, sweep_env,
                                                   monkeypatch):
        roles, watch, ui = sweep_env
        # a bare l0 row straight from the projection, no family /
        # seniority stamped (the shape the seed path used to inject)
        bare = _row(title="Product Manager, Infra", company="acme",
                    loc="US", posted="2026-10-27", source="board",
                    layer="l0", url="https://boards.acme.com/j/9")
        bare.pop("family", None)
        bare.pop("seniority", None)
        bare["company_key"] = "acme"
        _patch_all(monkeypatch, {})
        monkeypatch.setattr(rs, "project_l0",
                            lambda: ([bare], "ok"))
        rc = rs.sweep(family_spec("pm")["queries_core"],
                      family="pm")
        assert rc in (0, 1)
        jobs = json.load(open(ui / "roles-pm.json"))["jobs"]
        found = [j for j in jobs
                 if j["url"] == "https://boards.acme.com/j/9"]
        assert found and found[0]["family"] is None
        env = json.load(open(roles / "pm_envelope.json"))
        assert env["family_histogram"].get("?") == 1
        assert env["seniority_histogram"].get("?") == 1
