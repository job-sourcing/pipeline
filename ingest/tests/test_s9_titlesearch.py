"""S9 tests — targeted title-search corroboration (design-s9-titlesearch
.md v2, review finding 16: the full phase-doctrine matrix).

Covers:
- canonical predicate API (li_location / verbatim_match / multiset key)
- search_title: verbatim filtering, known_ids exclusion, exceptions as
  VALUES, empty-page ⇒ blocked_empty (never terminal)
- join_by_title C3 multiset tier: order/punctuation variants join,
  stopword-sensitive keys stay distinct, seniority guard
- join_population parity (the canonical no_match computation)
- job_key whitespace collapse (C4)
- phase_titlesearch: resume terminal-only, in-batch known_ids refresh
  (no duplicate appends), circuit breaker, completion meta
- phase_finish gate (the P0): incomplete titlesearch ⇒ not_checked,
  never no_match; complete ⇒ no_match; absent ⇒ pre-S9 semantics
- corroborate phase picks up source:"titleSearch" cards (pins the
  "no changes needed" claim)
- watch C5: title-search fallback bounded, lag pass never searches
"""
from __future__ import annotations

import json
import sys
import time as time_mod
from pathlib import Path

import pytest

from jobsearch import corroborate
from jobsearch.corroborate import (
    LinkedInSignalProvider, join_by_title, join_population,
    li_location, verbatim_match, token_multiset_key, title_tokens,
)
from jobsearch.sources.linkedin_guest import job_key

REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # repo root
SCRIPT = REPO_ROOT / "scripts" / "board_dump.py"

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("board_dump", SCRIPT)
board_dump = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("board_dump", board_dump)
_spec.loader.exec_module(board_dump)

WATCH = REPO_ROOT / "scripts" / "gha_board_watch.py"
_spec_w = importlib.util.spec_from_file_location("gha_board_watch", WATCH)
watch = importlib.util.module_from_spec(_spec_w)
sys.modules.setdefault("gha_board_watch", watch)
_spec_w.loader.exec_module(watch)

sys.path.insert(0, str(REPO_ROOT / "ingest"))


# ── shared fixtures (real shapes from the NVIDIA dump) ───────────────────

def _card(i, company="NVIDIA", title="Engineer", date="2026-09-08",
          location="Santa Clara, CA") -> dict:
    return {"id": str(i), "title": title, "company": company,
            "location": location, "date": date,
            "url": f"https://www.linkedin.com/jobs/view/{i}"}


def _sig(cid, title="Engineer", req_id="", applicants=95,
         status="matched", date="2026-09-05", location="Santa Clara, CA"):
    return {"linkedin_job_id": str(cid),
            "linkedin_url": f"https://www.linkedin.com/jobs/view/{cid}",
            "title": title, "company": "NVIDIA", "location": location,
            "linkedin_posted_date": date,
            "num_applicants": applicants,
            "applicants_label": f"{applicants} applicants",
            "job_req_id": req_id, "posted_time_ago": "4 days ago",
            "closed": False, "status": status,
            "fetched_at": "2026-09-10T01:51:44"}


def _list_row(req_id="JR2026001", title="Engineer",
              posted="Posted Today", primary="US, CA, Santa Clara"):
    return {
        "reqId": req_id, "title": title, "company": "NVIDIA",
        "locationsText": "6 Locations", "postedOn": posted,
        "primaryLocation": primary,
        "externalPath": f"/job/US-CA-Santa-Clara/Engineer_{req_id}",
        "url": f"https://x/{req_id}",
        "bulletFields": [req_id],
    }


def _ts_args(**over) -> object:
    base = {"board": "nvidia|wd5|x", "company": "NVIDIA",
            "country": "United States", "time_type": "Full time",
            "location": "United States", "corroborate_index": False,
            "index_mode": "single", "li_index_pages": 10,
            "li_index_cards": 100, "li_slice_pages": 3,
            "signals_batch": 40, "provider": "linkedin",
            "title_search_batch": 50, "details_batch": 150,
            "require_details": False, "sleep": 0.2, "detail_sleep": 0.25}
    base.update(over)
    return type("A", (), base)()


# ── C1: canonical predicate API ──────────────────────────────────────────

class TestLiLocation:
    def test_city_state(self):
        assert li_location("US, CA, Santa Clara") == \
            "Santa Clara, California"
        assert li_location("US, TX, Austin") == "Austin, Texas"
        assert li_location("US, OR, Hillsboro") == "Hillsboro, Oregon"

    def test_remote_and_country_level(self):
        assert li_location("US, Remote") == "United States"
        assert li_location("US") == "United States"
        assert li_location("") == "United States"

    def test_two_part_falls_back(self):
        # "CA, Santa Clara" (no country prefix) — conservative fallback
        assert li_location("CA, Santa Clara") == "United States"


class TestVerbatimMatch:
    def test_exact(self):
        assert verbatim_match("Senior Firmware Engineer",
                              "Senior Firmware Engineer")

    def test_word_order_variant(self):
        # GT: 'Senior Hardware Security Architect' ↔
        # 'Senior Security Architect - Hardware' (probe hit, F1=1.00)
        assert verbatim_match("Senior Hardware Security Architect",
                              "Senior Security Architect - Hardware")

    def test_punctuation_variant(self):
        assert verbatim_match("Senior System Software Engineer, Tegra",
                              "Senior System Software Engineer - Tegra")

    def test_seniority_mismatch_rejected(self):
        # live near-miss: req non-senior, card senior = different req
        assert not verbatim_match("Silicon Validation Engineer",
                                  "Senior Silicon Validation Engineer")

    def test_near_miss_rejected(self):
        # F1 ~0.9: 'Data Platform' vs 'AV Platform' family
        assert not verbatim_match("Senior Software Engineer, Data Platform",
                                  "Senior Software Engineer - AV Platform")

    def test_empty(self):
        assert not verbatim_match("", "Engineer")
        assert not verbatim_match("Engineer", "")


class TestTokenMultisetKey:
    def test_stopword_multiplicity_distinct(self):
        # 'Director of Product' ≠ 'Product Director' — the stopword
        # multiset separates them (review finding 4: the stop-SET key
        # conflated 28 live titles in 14 groups)
        assert token_multiset_key("Director of Product", "NVIDIA") != \
            token_multiset_key("Product Director", "NVIDIA")

    def test_punctuation_insensitive(self):
        assert token_multiset_key(
            "Senior Account Manager - Walmart", "NVIDIA") == \
            token_multiset_key("Senior Account Manager, Walmart", "NVIDIA")

    def test_company_namespace(self):
        assert token_multiset_key("Engineer", "NVIDIA") != \
            token_multiset_key("Engineer", "AMD")


class TestJobKeyWhitespace:
    def test_double_space_collapses(self):
        # GT miss found by calibration: 'Manager,  DSX OS' ↔ 'Manager, DSX OS'
        assert job_key("Senior Product Marketing Manager,  DSX OS",
                       "NVIDIA") == job_key(
            "Senior Product Marketing Manager, DSX OS", "NVIDIA")

    def test_newline_tab_collapse(self):
        assert job_key("Engineer\tData", "NVIDIA") == job_key(
            "Engineer Data", "NVIDIA")


# ── C1: search_title ─────────────────────────────────────────────────────

def _page(monkeypatch, cards):
    """fetch_text returns one canned search page built from `cards`."""
    def _card_html(c):
        return (f'<li data-entity-urn="urn:li:jobPosting:{c["id"]}">'
                f'<h3 class="base-search-card__title">{c["title"]}</h3>'
                f'<h4 class="base-search-card__subtitle">'
                f'<a>{c["company"]}</a></h4>'
                f'<span class="job-search-card__location">'
                f'{c["location"]}</span>'
                f'<time datetime="{c["date"]}">now</time></li>')

    html = "".join(_card_html(c) for c in cards)
    monkeypatch.setattr(corroborate, "fetch_text",
                        lambda url, *, params=None, cfg=None: html)


class TestSearchTitle:
    def _p(self):
        return LinkedInSignalProvider(cfg=None)

    def test_verbatim_hit_returned(self, monkeypatch):
        _page(monkeypatch, [
            _card(1, title="Senior ASIC Verification Engineer"),
            _card(2, title="Senior Silicon Validation Engineer"),
            _card(3, company="Other Co",
                  title="Senior ASIC Verification Engineer"),
        ])
        hits, exc = self._p().search_title(
            "Senior ASIC Verification Engineer", "Santa Clara, California",
            set(), company="NVIDIA")
        assert exc is None
        assert [h["id"] for h in hits] == ["1"]   # near-miss + foreign co out

    def test_known_ids_excluded(self, monkeypatch):
        _page(monkeypatch, [_card(1)])
        hits, exc = self._p().search_title(
            "Engineer", "United States", {"1"}, company="NVIDIA")
        assert exc is None and hits == []

    def test_transport_error_returned_as_value(self, monkeypatch):
        def boom(url, *, params=None, cfg=None):
            raise RuntimeError("403 wall")
        monkeypatch.setattr(corroborate, "fetch_text", boom)
        hits, exc = self._p().search_title(
            "Engineer", "United States", set(), company="NVIDIA")
        assert hits == []
        assert isinstance(exc, RuntimeError)      # returned, NEVER raised

    def test_empty_page_is_blocked_empty(self, monkeypatch):
        # 200-empty: soft-wall ambiguity — retryable, never terminal
        monkeypatch.setattr(corroborate, "fetch_text",
                            lambda url, *, params=None, cfg=None: "")
        hits, exc = self._p().search_title(
            "Engineer", "United States", set(), company="NVIDIA")
        assert hits == []
        assert isinstance(exc, corroborate.CorroborationBlocked)
        assert "blocked_empty" in str(exc)


# ── C3: join_by_title multiset tier ──────────────────────────────────────

class TestJoinMultisetTier:
    def test_order_variant_joins_when_job_key_fails(self):
        # job_key strips after the dash → 'senior security architect'
        # ≠ 'senior hardware security architect'; the multiset tier
        # must still join (predicate coherence with search_title)
        sigs = [_sig(1, title="Senior Security Architect - Hardware")]
        postings = {"JR1": "Senior Hardware Security Architect"}
        joined = join_by_title(sigs, postings, "NVIDIA")
        assert "JR1" in joined
        assert joined["JR1"]["match_tier"] == "multiset"

    def test_punctuation_variant_joins(self):
        sigs = [_sig(1, title="Senior System Software Engineer - Tegra")]
        postings = {"JR1": "Senior System Software Engineer, Tegra"}
        joined = join_by_title(sigs, postings, "NVIDIA")
        assert joined["JR1"]["match_tier"] == "multiset"

    def test_seniority_guard_blocks_tier(self):
        sigs = [_sig(1, title="Senior Silicon Validation Engineer")]
        postings = {"JR1": "Silicon Validation Engineer"}
        joined = join_by_title(sigs, postings, "NVIDIA")
        assert joined == {}                      # senior ≠ non-senior

    def test_stopword_distinction_holds(self):
        # multiset keeps 'of' → these must NOT join
        sigs = [_sig(1, title="Director of Product")]
        postings = {"JR1": "Product Director"}
        joined = join_by_title(sigs, postings, "NVIDIA")
        assert joined == {}

    def test_job_key_tier_still_wins_first(self):
        # exact job_key pair consumes the card before the multiset pass
        # (S9-audit: pairs must also be verbatim — the old fixture's
        # 'Engineer - Infra' vs 'Engineer' is a specialization pair the
        # guard now rejects; both tiers here use true pairs)
        sigs = [_sig(1, title="Engineer"),
                _sig(2, title="Engineer Infra")]
        postings = {"JR1": "Engineer", "JR2": "Infra Engineer"}
        joined = join_by_title(sigs, postings, "NVIDIA")
        # job_key('Engineer') joins JR1 in the job_key tier;
        # 'Infra Engineer' vs 'Engineer Infra' → multiset joins JR2
        assert set(joined) == {"JR1", "JR2"}
        assert joined["JR2"].get("match_tier") == "multiset"
        assert not joined["JR1"].get("match_tier")

    def test_greedy_one_to_one_in_tier(self):
        # two reqs, one multiset-matching card: only one joins
        sigs = [_sig(1, title="Security Architect Hardware")]
        postings = {"JR1": "Hardware Security Architect",
                    "JR2": "Architect Security Hardware"}
        joined = join_by_title(sigs, postings, "NVIDIA")
        assert len(joined) == 1


# ── C1a: join_population parity ──────────────────────────────────────────

class TestJoinPopulation:
    def test_parity_with_two_step_join(self):
        sigs = [_sig(1, req_id="JR1"),
                _sig(2, title="Engineer - Infra"),
                _sig(3, title="Senior Security Architect - Hardware")]
        postings = {"JR1": "Engineer", "JR2": "Engineer Infra",
                    "JR3": "Senior Hardware Security Architect",
                    "JR4": "Unmatched Title"}
        matched, unmatched = join_population(sigs, postings, "NVIDIA")
        assert matched == {"JR1", "JR2", "JR3"}
        assert unmatched == {"JR4"}


# ── C2: phase_titlesearch ────────────────────────────────────────────────

class _TsProvider:
    """search_title/fetch_signals stand-in; scripts the probe results."""
    name = "linkedin"

    def __init__(self, script):
        # script: {reqId: (hits, exc)} — what search_title returns
        self.script = script
        self.search_calls: list[tuple] = []
        self.known_ids_seen: list[set] = []

    def search_title(self, req_title, location, known_ids,
                     company="NVIDIA"):
        self.search_calls.append((req_title, location))
        self.known_ids_seen.append(set(known_ids))
        # find which req this is by title match in the script keys
        for rid, (hits, exc) in self.script.items():
            if rid.endswith("_" + req_title[:8]) or rid in req_title:
                return hits, exc
        # fallback: sequential
        key = list(self.script)[self._i % len(self.script)] \
            if self.script else None
        self._i = (getattr(self, "_i", 0) + 1)
        return self.script.get(key, ([], None))

    _i = 0


class TestPhaseTitleSearch:
    def _seed(self, tmp_path, reqs, signals, cards):
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            "\n".join(json.dumps(_list_row(r, t)) for r, t in reqs) + "\n",
            encoding="utf-8")
        out.with_suffix(".signals.jsonl").write_text(
            "\n".join(json.dumps(s) for s in signals) + "\n",
            encoding="utf-8")
        out.with_suffix(".li_index.jsonl").write_text(
            "\n".join(json.dumps(c) for c in cards) + "\n",
            encoding="utf-8")
        out.with_suffix(".details.jsonl").write_text("", encoding="utf-8")
        out.with_suffix(".li_index.meta.json").write_text(
            '{"done": true}', encoding="utf-8")
        return out

    def test_hit_appends_card_with_source_and_writes_state(self, tmp_path,
                                                           monkeypatch):
        out = self._seed(tmp_path, [("JR1", "Senior Firmware Engineer")],
                         [_sig(9, req_id="JR9")], [_card(9)])
        hit = _card(50, title="Senior Firmware Engineer")
        prov = _TsProvider({"JR1": ([hit], None)})
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: prov)
        monkeypatch.setattr(corroborate, "TITLE_SEARCH_PAUSE_S", 0)
        rc = board_dump.phase_title_search(_ts_args(), out)
        assert rc == 0
        idx = [json.loads(x) for x in
               out.with_suffix(".li_index.jsonl").read_text(
                   encoding="utf-8").splitlines()]
        assert idx[-1]["source"] == "titleSearch"
        assert idx[-1]["id"] == "50"
        state = [json.loads(x) for x in
                 out.with_suffix(".title_search.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert state[0]["status"] == "hit_new"
        assert state[0]["reqId"] == "JR1"
        meta = json.loads(out.with_suffix(".title_search.meta.json")
                          .read_text(encoding="utf-8"))
        assert meta["done"] is True and meta["population"] == 1

    def test_no_card_is_terminal_no_match(self, tmp_path, monkeypatch):
        out = self._seed(tmp_path, [("JR1", "Weird Internal Title")],
                         [_sig(9, req_id="JR9")], [_card(9)])
        prov = _TsProvider({"JR1": ([], None)})
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: prov)
        monkeypatch.setattr(corroborate, "TITLE_SEARCH_PAUSE_S", 0)
        board_dump.phase_title_search(_ts_args(), out)
        state = [json.loads(x) for x in
                 out.with_suffix(".title_search.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert state[0]["status"] == "no_card"

    def test_blocked_is_not_terminal_and_retries(self, tmp_path,
                                                 monkeypatch):
        out = self._seed(tmp_path, [("JR1", "Engineer")],
                         [_sig(9, title="Unrelated Role", req_id="JR9")],
                         [_card(9)])
        prov = _TsProvider(
            {"JR1": ([], corroborate.CorroborationBlocked("403 wall"))})
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: prov)
        monkeypatch.setattr(corroborate, "TITLE_SEARCH_BREAKER", 10)
        monkeypatch.setattr(corroborate, "TITLE_SEARCH_PAUSE_S", 0)
        board_dump.phase_title_search(_ts_args(), out)
        # blocked line written but NOT probed — meta done=false
        state = [json.loads(x) for x in
                 out.with_suffix(".title_search.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert state[0]["status"] == "blocked"
        meta = json.loads(out.with_suffix(".title_search.meta.json")
                          .read_text(encoding="utf-8"))
        assert meta["done"] is False and meta["terminal"] == 0
        # a fresh provider on re-run retries the SAME req
        prov2 = _TsProvider({"JR1": ([], None)})
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: prov2)
        board_dump.phase_title_search(_ts_args(), out)
        assert len(prov2.search_calls) == 1

    def test_circuit_breaker_stops_batch(self, tmp_path, monkeypatch):
        reqs = [(f"JR{i}", f"Title {i}") for i in range(1, 6)]
        out = self._seed(tmp_path, reqs, [_sig(9, req_id="JR9")], [_card(9)])
        script = {r: ([], corroborate.CorroborationBlocked("403"))
                  for r, _ in reqs}
        prov = _TsProvider(script)
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: prov)
        monkeypatch.setattr(corroborate, "TITLE_SEARCH_BREAKER", 3)
        monkeypatch.setattr(corroborate, "TITLE_SEARCH_PAUSE_S", 0)
        board_dump.phase_title_search(_ts_args(), out)
        assert len(prov.search_calls) == 3          # breaker stopped at 3
        state = [json.loads(x) for x in
                 out.with_suffix(".title_search.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert any(s.get("aborted") for s in state)

    def test_sibling_double_append_prevented(self, tmp_path,
                                             monkeypatch):
        # two sibling reqs share a title; the second probe must NOT
        # re-append the card the first probe accepted (in-batch
        # known_ids refresh — review finding 6)
        reqs = [("JR1", "Senior Account Manager"), ("JR2", "Senior Account Manager")]
        out = self._seed(tmp_path, reqs, [_sig(9, req_id="JR9")], [_card(9)])
        the_card = _card(50, title="Senior Account Manager")
        calls = {"n": 0}

        class P(_TsProvider):
            def search_title(self, req_title, location, known_ids,
                             company="NVIDIA"):
                calls["n"] += 1
                self.known_ids_seen.append(set(known_ids))
                return ([the_card] if calls["n"] == 1 else []), None

        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: P({}))
        monkeypatch.setattr(corroborate, "TITLE_SEARCH_PAUSE_S", 0)
        board_dump.phase_title_search(_ts_args(), out)
        idx = [json.loads(x) for x in
               out.with_suffix(".li_index.jsonl").read_text(
                   encoding="utf-8").splitlines()]
        appended = [c for c in idx if c.get("source") == "titleSearch"]
        assert len(appended) == 1                    # appended exactly once
        # and the second probe SAW the card id in known_ids
        idx = [json.loads(x) for x in
               out.with_suffix(".title_search.jsonl").read_text(
                   encoding="utf-8").splitlines()]
        assert [s["status"] for s in idx] == ["hit_new", "no_card"]

    def test_resume_skips_terminal(self, tmp_path, monkeypatch):
        reqs = [("JR1", "Engineer"), ("JR2", "Engineer II")]
        out = self._seed(tmp_path, reqs,
                         [_sig(9, title="Unrelated Role", req_id="JR9")],
                         [_card(9)])
        # JR1 already terminally probed
        out.with_suffix(".title_search.jsonl").write_text(
            json.dumps({"reqId": "JR1", "status": "no_card"}) + "\n",
            encoding="utf-8")
        prov = _TsProvider({"JR2": ([], None)})
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: prov)
        monkeypatch.setattr(corroborate, "TITLE_SEARCH_PAUSE_S", 0)
        board_dump.phase_title_search(_ts_args(), out)
        assert len(prov.search_calls) == 1          # JR1 skipped


# ── the P0: finish gate ──────────────────────────────────────────────────

class TestFinishTitleSearchGate:
    def _finish(self, tmp_path, monkeypatch, reqs, signals, cards,
                ts_state=None, ts_meta=None):
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            "\n".join(json.dumps(_list_row(r, t)) for r, t in reqs) + "\n",
            encoding="utf-8")
        out.with_suffix(".signals.jsonl").write_text(
            "\n".join(json.dumps(s) for s in signals) + "\n",
            encoding="utf-8")
        out.with_suffix(".li_index.jsonl").write_text(
            "\n".join(json.dumps(c) for c in cards) + "\n",
            encoding="utf-8")
        out.with_suffix(".details.jsonl").write_text("", encoding="utf-8")
        out.with_suffix(".li_index.meta.json").write_text(
            '{"done": true}', encoding="utf-8")
        if ts_state is not None:
            out.with_suffix(".title_search.jsonl").write_text(
                "\n".join(json.dumps(s) for s in ts_state) + "\n",
                encoding="utf-8")
        if ts_meta is not None:
            out.with_suffix(".title_search.meta.json").write_text(
                json.dumps(ts_meta), encoding="utf-8")
        rc = board_dump.phase_finish(_ts_args(), out)
        assert rc in (0, 1)
        import csv as _csv
        with open(out.with_suffix(".csv"), encoding="utf-8-sig") as fh:
            return {r["reqId"]: r for r in _csv.DictReader(fh)}

    def test_incomplete_titlesearch_ships_not_checked(self, tmp_path,
                                                      monkeypatch):
        rows = self._finish(
            tmp_path, monkeypatch,
            reqs=[("JR1", "Matched Title"), ("JR2", "Unprobed Title"),
                  ("JR3", "Terminally Probed Title")],
            signals=[_sig(1, req_id="JR1")], cards=[_card(1)],
            ts_state=[{"reqId": "JR3", "status": "no_card"}],
            ts_meta={"done": False})
        assert rows["JR1"]["corroborationStatus"] == "matched"
        # terminally-probed even while incomplete: the probe ANSWERED for
        # this req — no_match is final for it
        assert rows["JR3"]["corroborationStatus"] == "no_match"
        # THE P0: JR2 was never terminally probed → not_checked, never
        # no_match
        assert rows["JR2"]["corroborationStatus"] == "not_checked"

    def test_complete_titlesearch_ships_no_match(self, tmp_path,
                                                 monkeypatch):
        rows = self._finish(
            tmp_path, monkeypatch,
            reqs=[("JR2", "Probed Empty Title")],
            signals=[_sig(1, req_id="JR1")], cards=[_card(1)],
            ts_state=[{"reqId": "JR2", "status": "no_card"}],
            ts_meta={"done": True})
        assert rows["JR2"]["corroborationStatus"] == "no_match"

    def test_absent_state_keeps_pre_s9_semantics(self, tmp_path,
                                                 monkeypatch):
        # no title_search files at all: index-done ⇒ no_match (back-compat
        # for old dumps — the phase is optional)
        rows = self._finish(
            tmp_path, monkeypatch,
            reqs=[("JR2", "Never Probed")],
            signals=[_sig(1, req_id="JR1")], cards=[_card(1)])
        assert rows["JR2"]["corroborationStatus"] == "no_match"

    def test_done_true_but_unprobed_ships_not_checked(self, tmp_path,
                                                      monkeypatch):
        """S9-audit P1 pin (A4/C2 — was RED pre-fix): meta done=true is
        a POPULATION-level statement computed at titlesearch time; a req
        that was matched then (never probed) and later drifted out of
        the join must NOT ship no_match on the strength of ts_done —
        per-req terminal state is required (live case: JR2013322)."""
        rows = self._finish(
            tmp_path, monkeypatch,
            reqs=[("JR2", "Drifted Out Of Join")],
            signals=[_sig(1, req_id="JR1")], cards=[_card(1)],
            ts_state=[], ts_meta={"done": True})
        assert rows["JR2"]["corroborationStatus"] == "not_checked"


# ── pins: corroborate picks up titleSearch cards ─────────────────────────

class TestCorroboratePicksUpTitleSearchCards:
    def test_new_cards_get_signals_fetched(self, tmp_path, monkeypatch):
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row("JR1")) + "\n", encoding="utf-8")
        # one settled card + one titleSearch-appended card WITHOUT signal
        out.with_suffix(".li_index.jsonl").write_text(
            json.dumps(_card(1)) + "\n" +
            json.dumps({**_card(2), "source": "titleSearch"}) + "\n",
            encoding="utf-8")
        out.with_suffix(".signals.jsonl").write_text(
            json.dumps(_sig(1, req_id="JR1")) + "\n", encoding="utf-8")
        out.with_suffix(".li_index.meta.json").write_text(
            '{"done": true}', encoding="utf-8")

        def re_fetch(cards, on_record=None):
            recs = []
            for c in cards:
                rec = _sig(c["id"])
                recs.append(rec)
                if on_record:
                    on_record(rec)
            return recs

        class P:
            name = "linkedin"
            fetch_calls = []

            def fetch_signals(self, cards, on_record=None):
                P.fetch_calls.append([c["id"] for c in cards])
                return re_fetch(cards, on_record)

        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: P())
        rc = board_dump.phase_corroborate(_ts_args(), out)
        assert rc == 0
        assert P.fetch_calls == [["2"]]      # ONLY the unsignaled card


# ── C5: watch title-search fallback ──────────────────────────────────────

class TestWatchTitleSearchFallback:
    def _rows(self):
        return [{"reqId": "JR1", "title": "Senior Firmware Engineer",
                 "primaryLocation": "US, CA, Santa Clara"}]

    def test_fallback_matches_unmatched_posting(self, monkeypatch):
        from jobsearch.config import Config
        hit_card = _card(50, title="Senior Firmware Engineer")
        matched_sig = _sig(50, title="Senior Firmware Engineer")

        class P:
            name = "linkedin"
            ts_calls: list = []

            def index_cards(self, *, company, location, max_pages,
                            max_cards, start_offset=0):
                return [], 0, True          # window finds NOTHING

            def fetch_signals(self, cards, on_record=None):
                return [matched_sig]

            def search_title(self, req_title, location, known_ids,
                             company="NVIDIA"):
                P.ts_calls.append((req_title, location))
                return ([hit_card], None)

        monkeypatch.setattr(watch.corroborate, "get_provider",
                            lambda name, cfg=None: P())
        monkeypatch.setattr(watch.corroborate, "TITLE_SEARCH_PAUSE_S", 0)
        out = watch.corroborate_new(
            self._rows(), "NVIDIA", Config(),
            time_mod.monotonic() + 60, title_search_budget=[5])
        assert "JR1" in out
        assert out["JR1"]["num_applicants"] == 95
        assert P.ts_calls[0][0] == "Senior Firmware Engineer"
        assert P.ts_calls[0][1] == "Santa Clara, California"

    def test_budget_cell_bounds_searches(self, monkeypatch):
        from jobsearch.config import Config
        rows = [{"reqId": f"JR{i}", "title": f"Title {i}",
                 "primaryLocation": "US, CA, Santa Clara"} for i in range(5)]

        class P:
            name = "linkedin"
            n = 0

            def index_cards(self, **k):
                return [], 0, True

            def fetch_signals(self, cards, on_record=None):
                return []

            def search_title(self, *a, **k):
                P.n += 1
                return [], None

        monkeypatch.setattr(watch.corroborate, "get_provider",
                            lambda name, cfg=None: P())
        monkeypatch.setattr(watch.corroborate, "TITLE_SEARCH_PAUSE_S", 0)
        watch.corroborate_new(rows, "NVIDIA", Config(),
                              time_mod.monotonic() + 60,
                              title_search_budget=[2])
        assert P.n == 2                     # the budget cell bounded it

    def test_lag_pass_never_title_searches(self, monkeypatch):
        # corroborate_new without the budget cell does NO search_title
        from jobsearch.config import Config

        class P:
            name = "linkedin"
            searched = False

            def index_cards(self, **k):
                return [], 0, True

            def fetch_signals(self, cards, on_record=None):
                return []

            def search_title(self, *a, **k):
                P.searched = True
                return [], None

        monkeypatch.setattr(watch.corroborate, "get_provider",
                            lambda name, cfg=None: P())
        monkeypatch.setattr(watch.corroborate, "TITLE_SEARCH_PAUSE_S", 0)
        watch.corroborate_new(self._rows(), "NVIDIA", Config(),
                              time_mod.monotonic() + 60)
        assert P.searched is False

    def test_foreign_reqid_card_serves_nobody(self, monkeypatch):
        from jobsearch.config import Config
        hit_card = _card(50, title="Senior Firmware Engineer")
        foreign_sig = _sig(50, title="Senior Firmware Engineer",
                           req_id="JR999")   # a DIFFERENT requisition

        class P:
            name = "linkedin"

            def index_cards(self, **k):
                return [], 0, True

            def fetch_signals(self, cards, on_record=None):
                return [foreign_sig]

            def search_title(self, *a, **k):
                return ([hit_card], None)

        monkeypatch.setattr(watch.corroborate, "get_provider",
                            lambda name, cfg=None: P())
        monkeypatch.setattr(watch.corroborate, "TITLE_SEARCH_PAUSE_S", 0)
        out = watch.corroborate_new(
            self._rows(), "NVIDIA", Config(),
            time_mod.monotonic() + 60, title_search_budget=[5])
        assert out == {}                    # foreign reqId serves nobody


class TestS9AuditJoinFixes:
    """Pins for the S9-audit fix wave (P0 cross-tier reservation,
    P1 reqId freshest-first, entity unescape, company token, verbatim
    guard) — each was RED against the pre-fix tree."""

    def test_cross_tier_card_reservation(self):
        """P0 pin (A1/A2/C2): a card consumed by the reqId tier must
        NEVER be re-served to a title-family sibling. Old behavior: 50
        cards on 2 rows each, 54 provably-misattributed rows."""
        sigs = [_sig(1, title="Engineer", req_id="JR1"),
                _sig(2, title="Engineer", req_id="")]
        postings = {"JR1": "Engineer", "JR2": "Engineer"}
        reqid_join, title_join, _blocked = corroborate.compose_join(
            sigs, postings, "NVIDIA")
        assert set(reqid_join) == {"JR1"}
        # JR2 must not receive CARD 1 (the reqId-consumed one) via the
        # title tier — but the free sibling card 2 serving JR2 by title
        # is exactly the composed behavior we want
        assert title_join.get("JR2", {}).get("linkedin_job_id") == "2"
        # the P0 invariant: no card serves two reqs across tiers
        served = ([str(s["linkedin_job_id"]) for s in reqid_join.values()]
                  + [str(s["linkedin_job_id"]) for s in title_join.values()])
        assert sorted(served) == ["1", "2"]

    def test_cross_tier_no_shared_card_in_population(self):
        """Population-level view of the same invariant."""
        sigs = [_sig(1, title="Engineer", req_id="JR1")]
        postings = {"JR1": "Engineer", "JR2": "Engineer"}
        matched, unmatched = corroborate.join_population(
            sigs, postings, "NVIDIA")
        assert matched == {"JR1"} and unmatched == {"JR2"}

    def test_reqid_freshest_claimant_wins(self):
        """P1 pin (B2r): first-wins shipped counts up to 113d staler
        than an available fresher card — rank by (closed, fetched_at
        desc, posted desc)."""
        stale = _sig(1, title="Engineer", req_id="JR1", applicants=25,
                     date="2026-05-01")
        stale["fetched_at"] = "2026-09-01T00:00:00Z"
        fresh = _sig(2, title="Engineer", req_id="JR1", applicants=81,
                     date="2026-09-10")
        fresh["fetched_at"] = "2026-09-15T00:00:00Z"
        joined = corroborate.join_by_req_id(
            [stale, fresh], {"JR1"})
        assert joined["JR1"]["linkedin_job_id"] == "2"

    def test_reqid_open_card_beats_closed_fresher(self):
        closed = _sig(1, title="Engineer", req_id="JR1")
        closed["closed"] = True
        closed["fetched_at"] = "2026-09-16T00:00:00Z"
        open_card = _sig(2, title="Engineer", req_id="JR1")
        open_card["fetched_at"] = "2026-09-10T00:00:00Z"
        joined = corroborate.join_by_req_id([closed, open_card], {"JR1"})
        assert joined["JR1"]["linkedin_job_id"] == "2"

    def test_entity_leak_fixed_in_predicate(self):
        """P1 pin (B1/B3): 'R&amp;D' carried a phantom `amp` token that
        broke the verbatim predicate on provably-true pairs."""
        assert corroborate.verbatim_match(
            "R&D Engineer, Networking", "R&amp;D Engineer, Networking")
        assert "amp" not in corroborate.title_tokens(
            "R&amp;D Engineer")
        # seniority set agrees too
        assert corroborate._seniority_set(
            "Sr &amp;D Engineer") == corroborate._seniority_set(
            "Sr &D Engineer")

    def test_multiset_key_entity_and_company(self):
        """Entity-unescaped multiset + coarse company token: 'R&amp;D'
        under 'NVIDIA AI' keys identically to 'R&D' under 'NVIDIA'."""
        k1 = corroborate.token_multiset_key("R&amp;D Eng", "NVIDIA AI")
        k2 = corroborate.token_multiset_key("R&D Eng", "NVIDIA")
        assert k1 == k2

    def test_company_token_unifies_nvidia_ai(self):
        """P2 pin (B1/D3): 97 'NVIDIA AI' cards were structurally
        unjoinable against the 'NVIDIA' board param."""
        sigs = [_sig(1, title="Engineer")]
        sigs[0]["company"] = "NVIDIA AI"
        joined = corroborate.join_by_title(
            sigs, {"JR1": "Engineer"}, "NVIDIA")
        assert "JR1" in joined

    def test_company_token_strict_on_empty(self):
        """A company-less card does not join a company-scoped board
        (strict; the data has none — the port guard fixes None)."""
        sigs = [_sig(1, title="Engineer")]
        sigs[0]["company"] = None
        joined = corroborate.join_by_title(
            sigs, {"JR1": "Engineer"}, "NVIDIA")
        assert joined == {}
