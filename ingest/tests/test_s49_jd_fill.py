"""S49-C pins — the JD-fill completion pass (complaint #3's fix).

Three layers, one law each:

1. EMPTY-FILL SELECTION (jd_refetch --empty-fill): descLen==0 rows
   are selectable REGARDLESS of sectorReason — the title-only-graded
   class (roster/division/etc.) the PENDING-only daily lane never
   selected. The transport table still rules (li/board/wellfound stay
   skip); the pending lane's own selection must be untouched.

2. THE CORPUS→CACHE JOIN (scripts/s49_corpus_to_cache.py): every
   board corpus row with a store desc becomes a jd_cache entry keyed
   (source="board", url) in the jd_refetch store format EXACTLY —
   union-merge (a live-fetched ok never clobbered by the join; a
   failure verdict always overridden by the corpus JD).

3. THE END-TO-END CONSULT UPGRADE (the complaint's pin): an
   EMPTY-desc board row + a cache entry → the zero-fetch re-emit
   carries the desc, descriptionSource="jd_cache". Empty rows ARE
   upgradeable (the overlap gate's title-token fallback).
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
import jd_refetch as jr
import s49_corpus_to_cache as c2c

from test_s38_role_sweep import _row, sweep_env  # noqa: F401

REPO = _REPO


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


# ── 1. the empty-fill selection ──────────────────────────────────────

class TestEmptyFillSelection:
    def _row(self, src="adzuna", desc="", reason="roster",
             company="Acme", url="https://x/1", title="Senior Product "
             "Manager"):
        return {"source": src, "company": company, "url": url,
                "desc": desc, "sectorReason": reason,
                "id": f"{src}:1", "title": title}

    def test_empty_rows_selected_regardless_of_reason(self):
        # THE complaint-#3 gap: rows that graded on title alone were
        # never pending → never selected. Empty-fill picks them ALL.
        jobs = [self._row(reason=r) for r in
                ("roster", "division", "jd_informed", "tech_seed",
                 "bank_review", "no_sector_evidence", "")]
        assert len(jr._select_targets(jobs, "direct",
                                      empty_fill=True)) == 7

    def test_desc_bearing_rows_never_empty_fill_selected(self):
        jobs = [self._row(desc="word " * 100,
                          reason="no_sector_evidence"),
                self._row(desc="", reason="roster")]
        sel = jr._select_targets(jobs, "direct", empty_fill=True)
        assert len(sel) == 1 and not sel[0]["desc"].strip()

    def test_li_board_wellfound_stay_skip(self):
        # the transport table rules the empty-fill too: li = no desc
        # exists (guest cards), board = served by the corpus→cache
        # join, wellfound = JS-walled for urllib legs
        jobs = [self._row(src=s) for s in ("li", "board", "wellfound")]
        assert jr._select_targets(jobs, "direct", empty_fill=True) == []
        assert jr._select_targets(jobs, "hk_only", empty_fill=True) == []
        assert jr._select_targets(jobs, "fc_relay", empty_fill=True) == []

    def test_transports_honored(self):
        jobs = [self._row(src="careerjet"), self._row(src="adzuna")]
        assert [r["source"] for r in jr._select_targets(
            jobs, "hk_only", empty_fill=True)] == ["careerjet"]
        assert [r["source"] for r in jr._select_targets(
            jobs, "direct", empty_fill=True)] == ["adzuna"]

    def test_pending_lane_unchanged_by_the_flag_default(self):
        # the daily lane's contract: WITHOUT the flag the selection is
        # PENDING-only — a roster-graded empty row stays invisible.
        jobs = [self._row(reason="roster"),
                self._row(reason="no_sector_evidence")]
        assert len(jr._select_targets(jobs, "direct")) == 1
        # and WITH it the pending row is empty-selected too
        assert len(jr._select_targets(jobs, "direct",
                                      empty_fill=True)) == 2

    def test_only_source_restricts_the_leg(self):
        jobs = [self._row(src="adzuna"), self._row(src="careerjet")]
        sel = jr._select_targets(jobs, "hk_only", empty_fill=True,
                                 only_source="adzuna")
        assert sel == []      # adzuna is direct — hk_only sees nothing
        sel = jr._select_targets(jobs, "direct", empty_fill=True,
                                 only_source="adzuna")
        assert [r["source"] for r in sel] == ["adzuna"]

    def test_wellfound_fc_override_is_leg_scoped(self):
        # --only-source wellfound --transport fc_relay unlocks the
        # class for THAT leg; any other transport keeps the skip.
        jobs = [self._row(src="wellfound")]
        assert jr._select_targets(jobs, "fc_relay", empty_fill=True,
                                  only_source="wellfound") == jobs
        # without only_source the SELECT_RULES skip stands (the daily
        # fc_relay lane must never burn fleet credits on the class)
        assert jr._select_targets(jobs, "fc_relay",
                                  empty_fill=True) == []
        # and a plain empty-fill leg on any transport keeps it too
        assert jr._select_targets(jobs, "direct", empty_fill=True) == []


# ── 2. the corpus→cache join ─────────────────────────────────────────

class TestCorpusJoin:
    def _corpus(self, tmp_path, rows_by_cid):
        """desc/ files + a corpus jobs list, S49-C shaped."""
        desc_dir = tmp_path / "desc"
        desc_dir.mkdir()
        corpus = []
        for cid, drows in rows_by_cid.items():
            (desc_dir / f"{cid}.json").write_text(
                json.dumps({"rows": drows}))
            for rid, text in drows.items():
                corpus.append({
                    "id": rid, "companyId": cid, "company": cid.title(),
                    "url": f"https://{cid}.example/jobs/{rid}",
                    "title": "Product Manager",
                })
        return corpus, desc_dir

    def test_join_writes_jd_refetch_entry_format_exactly(self, tmp_path):
        # the field set/order contract of a jd_refetch 'ok' store line
        corpus, desc_dir = self._corpus(tmp_path, {
            "acme": {"acme:pm-1": "We are hiring a product manager "
                                   "to own the roadmap. " * 20}})
        entries, stats = c2c.join_corpus_to_cache(
            corpus, desc_dir, "2026-10-29T00:00:00Z", {})
        assert stats["entries_added"] == 1
        ent = entries["board|https://acme.example/jobs/acme:pm-1"]
        # the store contract: same keys, same order as jd_refetch's
        assert list(ent.keys()) == [
            "source", "url", "id", "status", "desc", "desc_len",
            "transport", "fetched_at", "http"]
        assert ent["source"] == "board" and ent["status"] == "ok"
        assert ent["id"] == "acme:pm-1"
        assert ent["transport"] == "corpus_join"
        assert ent["fetched_at"] == "2026-10-29T00:00:00Z"
        assert ent["desc_len"] == len(ent["desc"]) > 0
        # and the line a _load_cache_map round-trip re-reads it
        (tmp_path / "jd_cache.jsonl").write_text(
            json.dumps(ent, ensure_ascii=False) + "\n")
        jr.CACHE = tmp_path / "jd_cache.jsonl"
        m = jr._load_cache_map()
        assert m["board|https://acme.example/jobs/acme:pm-1"][
            "desc_len"] == ent["desc_len"]

    def test_desc_capped_at_the_refetch_store_gate(self, tmp_path):
        corpus, desc_dir = self._corpus(tmp_path, {
            "acme": {"acme:pm-1": "x" * 50_000}})
        entries, _ = c2c.join_corpus_to_cache(
            corpus, desc_dir, "now", {})
        ent = next(iter(entries.values()))
        assert ent["desc_len"] == jr.MAX_FETCH_LEN == 12_000

    def test_union_merge_never_clobbers_a_live_ok(self, tmp_path):
        corpus, desc_dir = self._corpus(tmp_path, {
            "acme": {"acme:pm-1": "corpus JD text " * 50}})
        url = "https://acme.example/jobs/acme:pm-1"
        existing = {f"board|{url}": {
            "source": "board", "url": url, "id": "acme:pm-1",
            "status": "ok", "desc": "live-fetched " * 300,
            "desc_len": 4_200, "transport": "direct",
            "fetched_at": "2026-10-30T00:00:00Z", "http": 200}}
        entries, stats = c2c.join_corpus_to_cache(
            corpus, desc_dir, "2026-10-29T00:00:00Z", existing)
        assert stats["kept_existing_ok"] == 1
        assert entries == {}      # the live fetch is newer → stands

    def test_join_overrides_failure_verdicts(self, tmp_path):
        # a 403 wall recorded for (board, url) is strictly less useful
        # than the corpus JD — the join wins.
        corpus, desc_dir = self._corpus(tmp_path, {
            "acme": {"acme:pm-1": "corpus JD text " * 50}})
        url = "https://acme.example/jobs/acme:pm-1"
        existing = {f"board|{url}": {
            "source": "board", "url": url, "id": "acme:pm-1",
            "status": "http-error:403", "desc": "", "desc_len": 0,
            "transport": "direct", "fetched_at": "2026-10-30T00:00:00Z",
            "http": 403}}
        entries, stats = c2c.join_corpus_to_cache(
            corpus, desc_dir, "2026-10-29T00:00:00Z", existing)
        assert stats["clobbered_verdicts"] == 1
        assert entries[f"board|{url}"]["status"] == "ok"

    def test_store_less_and_url_less_rows_skipped(self, tmp_path):
        corpus, desc_dir = self._corpus(tmp_path, {
            "acme": {"acme:pm-1": "real JD " * 100,
                     "acme:pm-2": "   "}})          # blank store text
        corpus.append({"id": "acme:pm-3", "companyId": "acme",
                       "company": "Acme", "url": "",
                       "title": "Product Manager"})
        entries, stats = c2c.join_corpus_to_cache(
            corpus, desc_dir, "now", {})
        assert stats["entries_added"] == 1
        assert stats["no_store_desc"] == 1
        assert stats["no_url"] == 1


# ── 3. the end-to-end consult upgrade (the complaint's pin) ──────────

class TestConsultUpgradeEndToEnd:
    BOARD_URL = "https://joinbytedance.com/search/7463297208587946248"
    BOARD_TITLE = "Principal Product Manager - Compute AI Infra"
    CORPUS_JD = ("ByteDance is hiring a Principal Product Manager for "
                 "the Compute AI Infra team. You will own the roadmap "
                 "for GPU cluster capacity planning, work with "
                 "engineering on infrastructure, and drive product "
                 "strategy for our AI platform. " * 12)

    def _cache_line(self, url, desc, source="board"):
        return json.dumps({
            "source": source, "url": url, "id": "bytedance:A256504A",
            "status": "ok", "desc": desc, "desc_len": len(desc),
            "transport": "corpus_join",
            "fetched_at": "2026-10-29T00:00:00Z", "http": 200})

    def test_empty_board_row_reemit_carries_the_jd(self, sweep_env,
                                                   monkeypatch):
        """THE pin: an empty-desc board row (the pm projection's desc
        "" class) + a corpus-joined cache entry → the re-emitted bundle
        carries the desc with descriptionSource="jd_cache"."""
        roles, watch, ui = sweep_env
        p = roles / "jd_cache.jsonl"
        p.write_text(self._cache_line(self.BOARD_URL, self.CORPUS_JD)
                     + "\n")
        monkeypatch.setattr(rs, "_JD_CACHE", {})
        monkeypatch.setattr(rs, "_JD_CACHE_PATH", p)
        # the l0 projection shape: desc "" (project_l0's hardcode),
        # description_source "none", us_status stamped by the source
        board = _row(title=self.BOARD_TITLE, company="bytedance",
                     loc="US", posted="2026-10-14", source="board",
                     layer="l0", desc="", url=self.BOARD_URL)
        board["description_source"] = "none"
        board["us_status"] = "us"
        board["us_evidence"] = "board_country"
        board["company_key"] = "bytedance"
        # the rest of project_l0's projection stamp (family/seniority
        # ride every l0 row)
        board["family"] = "pm"
        board["family_evidence"] = "title:product"
        board["seniority"] = "senior"
        _patch_all(monkeypatch, {})          # fetchers empty FIRST —
        # _patch_all re-points project_l0 at the empty projection, so
        # the board row patch must come AFTER it
        monkeypatch.setattr(rs, "project_l0",
                            lambda: ([board], "ok"))
        rc = rs.sweep(["product manager"])
        env = json.load(open(roles / "pm_envelope.json"))
        assert env["counters"]["jd_cache_hits"] == 1
        jobs = json.load(open(ui / "roles-pm.json"))["jobs"]
        found = [r for r in jobs if r.get("url") == self.BOARD_URL]
        assert found and found[0]["descLen"] > 0
        assert found[0]["desc"].startswith("ByteDance is hiring")
        assert found[0]["descriptionSource"] == "jd_cache"

    def test_empty_careerjet_seed_row_reemit_carries_the_jd(
            self, sweep_env, monkeypatch):
        """the twin class: an empty careerjet row (the HK-lane fetch
        target) riding the seed envelope gets its cache fill too."""
        roles, watch, ui = sweep_env
        url = "https://jobviewtrack.com/v2/careerjetrow1"
        jd = ("Senior Product Manager, Payments — you will own roadmap "
              "and platform strategy for the product team. " * 20)
        p = roles / "jd_cache.jsonl"
        p.write_text(self._cache_line(url, jd, source="careerjet")
                     + "\n")
        monkeypatch.setattr(rs, "_JD_CACHE", {})
        monkeypatch.setattr(rs, "_JD_CACHE_PATH", p)
        prior = {
            "envelope": {"ran_at": "2026-10-10T00:00:00Z"},
            "jobs": [{
                "title": "Senior Product Manager", "company":
                "Payments Co", "companyKey": "payments-co",
                "loc": "US", "url": url,
                "postedOn": "2026-10-20",        # ≥60d-fresh
                "desc": "", "source": "careerjet",
                "id": "careerjet:hash:abc",
            }],
        }
        (roles / "prior.json").write_text(json.dumps(prior))
        _patch_all(monkeypatch, {})
        rc = rs.sweep(["product manager"],
                      seed_envelope=str(roles / "prior.json"))
        env = json.load(open(roles / "pm_envelope.json"))
        assert env["counters"]["jd_cache_hits"] == 1
        assert env["counters"]["seed_rows_in"] == 1
        jobs = json.load(open(ui / "roles-pm.json"))["jobs"]
        found = [r for r in jobs if r.get("url") == url]
        assert found and found[0]["descLen"] > 0
        assert found[0]["descriptionSource"] == "jd_cache"

    def test_corpus_entry_never_upgrades_a_row_it_does_not_match(
            self, sweep_env, monkeypatch):
        """the overlap gate still guards the join: a corpus JD whose
        text shares nothing with the title never lands (garbage-longer
        protection — the 2/184 honest-floor class)."""
        roles, watch, ui = sweep_env
        p = roles / "jd_cache.jsonl"
        p.write_text(self._cache_line(
            self.BOARD_URL, "售前顾问要求精通数据库管理和分布式系统 "
            "架构设计，负责客户沟通与项目交付 " * 40) + "\n")
        monkeypatch.setattr(rs, "_JD_CACHE", {})
        monkeypatch.setattr(rs, "_JD_CACHE_PATH", p)
        board = _row(title=self.BOARD_TITLE, company="bytedance",
                     loc="US", posted="2026-10-14", source="board",
                     layer="l0", desc="", url=self.BOARD_URL)
        board["description_source"] = "none"
        board["us_status"] = "us"
        board["company_key"] = "bytedance"
        board["family"] = "pm"
        board["family_evidence"] = "title:product"
        board["seniority"] = "senior"
        _patch_all(monkeypatch, {})
        monkeypatch.setattr(rs, "project_l0",
                            lambda: ([board], "ok"))
        rc = rs.sweep(["product manager"])
        env = json.load(open(roles / "pm_envelope.json"))
        assert env["counters"]["jd_cache_hits"] == 0
        jobs = json.load(open(ui / "roles-pm.json"))["jobs"]
        found = [r for r in jobs if r.get("url") == self.BOARD_URL]
        assert found and found[0]["descLen"] == 0


# ── the state-plumbing separation (the daily lane's files) ───────────

def test_emptyfill_uses_state_report_twins(tmp_path, monkeypatch):
    """--empty-fill writes jd_refetch_emptyfill_* — the pending lane's
    jd_refetch_state.json / report must never see an empty-fill leg."""
    src = (REPO / "scripts/jd_refetch.py").read_text()
    assert '"jd_refetch_emptyfill"' in src
    assert "JD_REFETCH_{fam}{suffix}" in src      # env keys separated
    # the twins derivation is a pure rename of the family-resolved path
    import jd_refetch as jr2
    jr2._set_family("pm")
    st = jr2.STATE.with_name(
        jr2.STATE.name.replace("jd_refetch", "jd_refetch_emptyfill"))
    assert st.name == "jd_refetch_emptyfill_state.json"
    jr2._set_family("gen")
    st = jr2.STATE.with_name(
        jr2.STATE.name.replace("jd_refetch", "jd_refetch_emptyfill"))
    assert st.name == "jd_refetch_emptyfill_gen_state.json"
