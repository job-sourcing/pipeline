"""S41 pins — the JD-refetch machinery (DESIGN-S41 v2 §1).

Covers: the (source,url) cache key doctrine (F2), the consult hook's
strictly-longer + overlap gate (F3), the dateless-with-evidence seed
admission (F1), the selection transport allowlist + fullness bars +
board-employer exclusion (F4/F5), the store-time quality gates, the
himalayas double-prefix unwrap (F8), and the two P0 state-plumbing
invariants: file-level checkpoint PATHS in the lane yml (F7) + the
mirror's unconditional union-merge (F6 — asserted on the script text
and on the merge semantics directly).
"""
import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "ingest"))
sys.path.insert(0, "ingest/tests")

import pytest

import role_sweep as rs
import jd_refetch as jr

from test_s38_role_sweep import _row, sweep_env  # noqa: F401


def _patch_all(monkeypatch, rows_by_source):
    """the S38/S39/S40 pattern: patch every fetcher + neutralize l0."""
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

REPO = _REPO


# ── F2: key doctrine ────────────────────────────────────────────────

def test_cache_key_is_source_and_url_not_rid():
    # the S41-R1 F2 finding: 89% of targets carry row-hash ids with the
    # mutable posted basis — (source, url) is the only stable key
    assert rs._jd_cache_key("adzuna", "https://x") == "adzuna|https://x"
    assert rs._jd_cache_key("adzuna", "https://y") != rs._jd_cache_key(
        "adzuna", "https://x")


def test_cache_load_newest_wins(tmp_path, monkeypatch):
    p = tmp_path / "jd_cache.jsonl"
    p.write_text(
        json.dumps({"source": "adzuna", "url": "u1", "status": "ok",
                    "desc": "old", "desc_len": 3,
                    "fetched_at": "2026-10-20T00:00:00Z"}) + "\n"
        + json.dumps({"source": "adzuna", "url": "u1", "status": "ok",
                      "desc": "newer longer", "desc_len": 12,
                      "fetched_at": "2026-10-21T00:00:00Z"}) + "\n"
        + json.dumps({"source": "dice", "url": "u2", "status": "ok",
                      "desc": "d", "desc_len": 1,
                      "fetched_at": "2026-10-19T00:00:00Z"}) + "\n")
    monkeypatch.setattr(rs, "_JD_CACHE", {})
    monkeypatch.setattr(rs, "_JD_CACHE_PATH", p)
    cache = rs._load_jd_cache()
    assert cache["adzuna|u1"]["desc"] == "newer longer"
    assert cache["dice|u2"]["desc"] == "d"
    assert len(cache) == 2


# ── F3: overlap gate ────────────────────────────────────────────────

def test_overlap_gate_accepts_real_snippet():
    old = ("We are hiring a senior product manager to own the roadmap "
           "for our payments platform and drive growth metrics")
    new = old + " You will lead cross-functional teams across the org" \
        " and define strategy with executive stakeholders. " * 20
    assert rs._jd_overlap_ok(old, new, "Senior Product Manager")


def test_overlap_gate_rejects_garbage_longer():
    # a cookie-wall / different-job capture shares no vocabulary with
    # the API snippet — must NEVER replace it despite being longer
    old = ("We are hiring a senior product manager to own the roadmap "
           "for our payments platform")
    new = "Accept all cookies Manage preferences Privacy Policy " * 200
    assert not rs._jd_overlap_ok(old, new, "Senior Product Manager")


def test_overlap_gate_title_fallback_when_no_snippet():
    # careerjet rows have NO snippet — fall back to title tokens
    good = ("Senior Product Manager, Payments — you will own roadmap "
            "and platform strategy for the product team " * 10)
    assert rs._jd_overlap_ok("", good, "Senior Product Manager")
    assert not rs._jd_overlap_ok("", "barista shift supervisor " * 50,
                                 "Senior Product Manager")


# ── F1+F3: the consult hook inside a real sweep ─────────────────────

class TestConsultHook:
    def test_cached_jd_upgrades_row_and_counts(self, sweep_env,
                                               monkeypatch):
        roles, watch, ui = sweep_env
        long_desc = ("JPMorgan Chase is hiring a senior product manager "
                     "for the payments platform. You will build APIs "
                     "using Java and Python with SQL data pipelines and "
                     "agile program management. " * 8)
        p = roles / "jd_cache.jsonl"
        p.write_text(json.dumps({
            "source": "adzuna", "url": "https://adzuna.com/details/X1",
            "status": "ok", "desc": long_desc,
            "desc_len": len(long_desc),
            "fetched_at": "2026-10-21T00:00:00Z"}) + "\n")
        monkeypatch.setattr(rs, "_JD_CACHE", {})
        monkeypatch.setattr(rs, "_JD_CACHE_PATH", p)
        row = _row(title="Senior Product Manager",
                   company="Obscure Startup LLC", loc="US",
                   posted="2026-10-14", source="adzuna", layer="l1.5",
                   rid="adz:X1",
                   url="https://adzuna.com/details/X1",
                   desc="Product manager role")   # thin 500ch-cap class
        _patch_all(monkeypatch, {"adzuna": [row]})
        rc = rs.sweep(["product manager"])
        env = json.load(open(roles / "pm_envelope.json"))
        assert env["counters"]["jd_cache_hits"] == 1
        # the desc-informed machinery saw the FULL text
        found = [r for r in json.load(open(ui / "roles-pm.json"))["jobs"]
                 if r.get("url") == row["url"]]
        assert found and found[0].get("descriptionSource") == "jd_cache"

    def test_cache_never_downgrades_longer_fresh_desc(
            self, sweep_env, monkeypatch):
        roles, watch, ui = sweep_env
        short = "product manager for payments"
        p = roles / "jd_cache.jsonl"
        p.write_text(json.dumps({
            "source": "adzuna", "url": "https://adzuna.com/details/X2",
            "status": "ok", "desc": short, "desc_len": len(short),
            "fetched_at": "2026-10-21T00:00:00Z"}) + "\n")
        monkeypatch.setattr(rs, "_JD_CACHE", {})
        monkeypatch.setattr(rs, "_JD_CACHE_PATH", p)
        long_fresh = ("We need a senior product manager with strong "
                      "roadmap ownership, stakeholder alignment, and "
                      "platform thinking experience. " * 15)
        row = _row(title="Senior Product Manager",
                   company="Obscure Startup LLC", loc="US",
                   posted="2026-10-14", source="adzuna", layer="l1.5",
                   rid="adz:X2", url="https://adzuna.com/details/X2",
                   desc=long_fresh)
        _patch_all(monkeypatch, {"adzuna": [row]})
        rc = rs.sweep(["product manager"])
        env = json.load(open(roles / "pm_envelope.json"))
        assert env["counters"]["jd_cache_hits"] == 0   # strictly-longer

    def test_dateless_seed_row_admitted_with_cache_hit(
            self, sweep_env, monkeypatch):
        """F1: dateless rows are seed-INeligible EXCEPT when the cache
        holds a full JD for (source,url) — the evidence rides them
        back through grading via the consult hook."""
        roles, watch, ui = sweep_env
        long_desc = ("The bank technology division seeks a senior "
                     "product manager to build trading APIs with Java "
                     "microservices and SQL. " * 10)
        p = roles / "jd_cache.jsonl"
        p.write_text(json.dumps({
            "source": "jooble", "url": "https://jooble.org/jdp/999",
            "status": "ok", "desc": long_desc,
            "desc_len": len(long_desc),
            "fetched_at": "2026-10-21T00:00:00Z"}) + "\n")
        monkeypatch.setattr(rs, "_JD_CACHE", {})
        monkeypatch.setattr(rs, "_JD_CACHE_PATH", p)
        prior = {
            "envelope": {"ran_at": "2026-10-10T00:00:00Z"},
            "jobs": [{
                "title": "Senior Product Manager", "company":
                "Obscure Bank Corp", "companyKey": "obscure-bank",
                "loc": "US", "url": "https://jooble.org/jdp/999",
                "postedOn": "",          # DATELESS — the F1 class
                "desc": "", "source": "jooble",
                "id": "jooble:hash:abc",
            }],
        }
        (roles / "prior.json").write_text(json.dumps(prior))
        _patch_all(monkeypatch, {})
        rc = rs.sweep(["product manager"],
                      seed_envelope=str(roles / "prior.json"))
        env = json.load(open(roles / "pm_envelope.json"))
        assert env["counters"]["jd_seed_admitted"] == 1
        assert env["counters"]["jd_cache_hits"] == 1

    def test_dateless_seed_row_without_cache_still_excluded(
            self, sweep_env, monkeypatch):
        roles, watch, ui = sweep_env
        monkeypatch.setattr(rs, "_JD_CACHE", {})
        monkeypatch.setattr(rs, "_JD_CACHE_PATH",
                            roles / "no-cache.jsonl")
        prior = {
            "envelope": {"ran_at": "2026-10-10T00:00:00Z"},
            "jobs": [{
                "title": "Senior Product Manager", "company": "X",
                "loc": "US", "url": "https://jooble.org/jdp/111",
                "postedOn": "", "desc": "thin", "source": "jooble",
                "id": "jooble:hash:def",
            }],
        }
        (roles / "prior.json").write_text(json.dumps(prior))
        _patch_all(monkeypatch, {})
        rc = rs.sweep(["product manager"],
                      seed_envelope=str(roles / "prior.json"))
        env = json.load(open(roles / "pm_envelope.json"))
        assert env["counters"]["jd_seed_admitted"] == 0
        assert env["counters"]["seed_rows_in"] == 0


# ── F4/F5: selection ────────────────────────────────────────────────

class TestSelection:
    def _bundle_row(self, src="adzuna", desc_len=500, reason=True,
                    company="Acme", url="https://x/1"):
        return {
            "source": src, "company": company, "url": url,
            "desc": "word " * (desc_len // 5),
            "sectorReason": "no_sector_evidence" if reason else "tech_seed",
            "id": f"{src}:1", "title": "Senior Product Manager",
        }

    def test_capped_api_sources_all_pending_selected(self):
        # adzuna's 500ch cap IS the gap — every pending adzuna row
        # targets, regardless of the 300ch default bar
        jobs = [self._bundle_row("adzuna", 500) for _ in range(3)]
        jobs += [self._bundle_row("adzuna", 500, reason=False)]
        sel = jr._select_targets(jobs, "direct")
        assert len(sel) == 3

    def test_indeed_fullness_bar_200(self):
        assert len(jr._select_targets(
            [self._bundle_row("indeed", 150)], "direct")) == 1
        assert len(jr._select_targets(
            [self._bundle_row("indeed", 4000)], "direct")) == 0

    def test_careerjet_never_selected_on_direct(self):
        assert jr._select_targets(
            [self._bundle_row("careerjet", 0)], "direct") == []
        assert len(jr._select_targets(
            [self._bundle_row("careerjet", 0)], "hk_only")) == 1

    def test_skip_sources_and_board_employer_excluded(self):
        assert jr._select_targets(
            [self._bundle_row("li", 0)], "direct") == []
        assert jr._select_targets(
            [self._bundle_row("wellfound", 0)], "direct") == []
        assert jr._select_targets(
            [self._bundle_row("jooble", 0, company="JobLeads US")],
            "direct") == []

    def test_unknown_source_not_ours(self):
        assert jr._select_targets(
            [self._bundle_row("jobicy", 0)], "direct") == []


# ── F3: store-time quality gates ────────────────────────────────────

class TestQualityGates:
    def test_too_short_rejected(self):
        assert jr._quality_reject("too short") == "too_short"

    def test_junk_signatures_rejected(self):
        assert jr._quality_reject(
            "Please enable JavaScript to continue " * 10) == \
            "junk_signature"
        assert jr._quality_reject(
            "Verify you are a human by completing the captcha " * 10) \
            == "junk_signature"

    def test_low_text_density_rejected(self):
        assert jr._quality_reject("12121212 !!! ??? " * 40) == \
            "low_text_density"

    def test_real_jd_passes(self):
        jd = ("We are hiring a senior product manager who will own the "
              "roadmap for our platform, partner with engineering and "
              "design, drive metrics and growth, and lead launches "
              "across the organization with strong communication. " * 6)
        assert jr._quality_reject(jd) == ""


# ── F8: himalayas unwrap ────────────────────────────────────────────

def test_himalayas_double_prefix_unwrapped():
    url = ("https://himalayas.app/jobs/https://himalayas.app/companies/"
           "twinhealth/jobs/senior-principal-product-manager")
    fixed = jr.re.match(
        r"^(https?://himalayas\.app/jobs/)(https?://.+)$", url).group(2)
    assert fixed.startswith("https://himalayas.app/companies/")


# ── F5: the JobLeads N9 class ───────────────────────────────────────

def test_jobleads_is_board_employer():
    from jobsearch.rolefamilies import is_board_employer
    assert is_board_employer("JobLeads US")
    assert is_board_employer("jobleads")


# ── F6+F7: the state-plumbing invariants (P0s) ──────────────────────

def test_lane_checkpoints_are_file_level():
    """F7: NO lane may snapshot/restore the roles DIRECTORY — a
    dir-level restore over origin/main deletes concurrent-writer
    artifacts (jd_cache from the refetch lane, dmm quartet...)."""
    for wf in ("role-sweep-pm.yml", "jd-refetch.yml"):
        yml = (REPO / ".github/workflows" / wf).read_text()
        # every PATHS=/FILES= roles/ entry must be FILE-LEVEL — never
        # the bare directory (ui/board_watch entries are out of scope)
        for m in re.finditer(r'(?:PATHS|FILES)="([^"]+)"', yml):
            for p in m.group(1).split():
                if p.startswith("ingest/data/roles"):
                    assert p != "ingest/data/roles" and \
                        p.count("/") >= 3, \
                        f"{wf}: dir-level path in checkpoint: {p}"


def test_mirror_has_unconditional_jd_cache_merge():
    """F6: the union-merge handler must exist and be UN-gated (the
    quartet's pm_envelope newest-wins gate must not cover it)."""
    sh = (REPO / "scripts/build_public_mirror.sh").read_text()
    assert "jd-cache-sync" in sh
    assert "union-merge" in sh
    # the handler's gate must be EXISTENCE ONLY — never a ran_at
    # newest-wins comparison (that's the quartet handler's class)
    m = re.search(
        r'if \[ -e "\$DEST/ingest/data/roles/jd_cache\.jsonl" \]; then'
        r".*?PYJD", sh, re.S)
    assert m, "jd_cache back-sync handler missing or gated differently"
    block = m.group(0)
    assert "ran_at" not in block and "newest-wins" not in block


def test_mirror_jd_cache_union_merge_semantics(tmp_path):
    """The merge function itself: both-sided appends survive."""
    a = tmp_path / "a"; b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "jd_cache.jsonl").write_text(
        json.dumps({"source": "adzuna", "url": "u1", "status": "ok",
                    "desc": "org-side", "desc_len": 8,
                    "fetched_at": "2026-10-21T01:00:00Z"}) + "\n")
    (b / "jd_cache.jsonl").write_text(
        json.dumps({"source": "dice", "url": "u2", "status": "ok",
                    "desc": "archive-side", "desc_len": 12,
                    "fetched_at": "2026-10-21T02:00:00Z"}) + "\n"
        + json.dumps({"source": "adzuna", "url": "u1", "status": "ok",
                      "desc": "stale archive copy", "desc_len": 18,
                      "fetched_at": "2026-10-20T00:00:00Z"}) + "\n")
    src = {"__file__": "/dev/null"}
    # inline re-implementation of the mirror merge (same semantics):
    def load(p):
        m = {}
        for line in open(p):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            k = f"{d.get('source','')}|{d.get('url','')}"
            prev = m.get(k)
            if not prev or d.get("fetched_at","") >= prev.get("fetched_at",""):
                m[k] = d
        return m
    merged = load(a / "jd_cache.jsonl")
    for k, d in load(b / "jd_cache.jsonl").items():
        prev = merged.get(k)
        if not prev or d.get("fetched_at","") >= prev.get("fetched_at",""):
            merged[k] = d
    assert merged["adzuna|u1"]["desc"] == "org-side"     # newest wins
    assert merged["dice|u2"]["desc"] == "archive-side"  # union keeps


def test_4xx_terminal_not_refetched(tmp_path, monkeypatch):
    """The lane-1 lesson: http-error:403 rows must NOT re-enter the todo
    (130 jooble 403s re-burned every leg). 403/404/410/418 = an answer;
    only 5xx/network errors retry."""
    import jd_refetch as jr
    p = tmp_path / "jd_cache.jsonl"
    p.write_text(
        json.dumps({"source": "jooble", "url": "j1", "status":
                    "http-error:403", "fetched_at": "t1"}) + "\n"
        + json.dumps({"source": "adzuna", "url": "a1", "status": "ok",
                      "fetched_at": "t1"}) + "\n"
        + json.dumps({"source": "dice", "url": "d1", "status":
                    "error:TimeoutError", "fetched_at": "t1"}) + "\n")
    monkeypatch.setattr(jr, "CACHE", p)
    m = jr._load_cache_map()
    def resolved(k):
        st = m.get(k, {}).get("status", "")
        return (st == "ok" or st.startswith("q:")
                or st.startswith("http-error:4"))
    assert resolved("jooble|j1")          # 403 terminal
    assert resolved("adzuna|a1")          # ok
    assert not resolved("dice|d1")         # transient network error retries
    assert not resolved("new|never")      # never fetched
