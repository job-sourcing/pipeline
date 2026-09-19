"""Tests for scripts/board_dump.py — the generic board-dump v2 flow.

Covers the CSV v2 contract (design-board-v2.md D1 + review addenda):
- column set exactly matches CSV_COLUMNS (30 cols)
- location enumeration (0A): never "N Locations", primary + additional
  merged, nLocations correct, stateCodes derived, remoteFlag
- description rendering (0B): clean text, descriptionLength
- metadata (0C): postingAgeDays, hiringOrg, questionnaireId (S11:
  the join key into the linked questionnaires.csv — was a bare boolean)
- v2.5 quality round (S11): reqYear REMOVED (JR-number prefix ≠ a
  calendar signal — 0.1% year match); daysLeftToApply blank when the
  "at least until" floor elapsed on a live posting (never negative);
  censored=true on measured repost resets (firstSeenDate < startDate)
- signals (0D): matched / not_checked defaults, dateDeltaDays, matchMethod
- B1 guard: list status records completeness verdict
- CSV round-trip: newline-in-cell + utf-8-sig BOM (Excel-safe)
- _load_jsonl corrupt-tail tolerance (crash-mid-append)
- phase_finish join: reqId exact + title fallback, matchMethod recorded
- phase_details resumability (audit S7-B3 A3): 3-strike error-row retry
  with attempts accumulation, settled rows never re-fetched
- phase_corroborate resumability (audit S7-B3 A3): blocked-card retry
  (done-set excludes blocked), per-card on_record checkpoint surviving
  a mid-batch crash, index accumulate/dedup + offset resume + done skip
"""
from __future__ import annotations

import csv
import datetime as _dt
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # repo root
SCRIPT = REPO_ROOT / "scripts" / "board_dump.py"

# scripts/ is not a package — load by path
_spec = importlib.util.spec_from_file_location("board_dump", SCRIPT)
board_dump = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("board_dump", board_dump)
_spec.loader.exec_module(board_dump)

sys.path.insert(0, str(REPO_ROOT / "ingest"))


def _list_row(req_id="JR2026001", title="Engineer", posted="Posted Today"):
    return {
        "reqId": req_id, "title": title, "company": "NVIDIA",
        "locationsText": "6 Locations", "postedOn": posted,
        "externalPath": f"/job/US-CA-Santa-Clara/Engineer_{req_id}",
        "url": f"https://nvidia.wd5.myworkdayjobs.com/en-US/nvidiaexternalcareersite/job/US-CA-Santa-Clara/Engineer_{req_id}",
        "bulletFields": [req_id],
    }


_TODAY = _dt.date.today()
_TODAY_ISO = _TODAY.isoformat()
_YESTERDAY = (_TODAY - _dt.timedelta(days=1)).isoformat()


def _detail_info(startDate=None, addl=None, desc="<p><b>What you'll do:</b></p><ul><li>Build GPU infrastructure.</li></ul>", questionnaireId="abc123"):
    if startDate is None:
        startDate = _YESTERDAY
    return {
        "title": "Engineer",
        "location": "US, CA, Santa Clara",
        "additionalLocations": addl,
        "startDate": startDate,
        "timeType": "Full time",
        "jobDescription": desc,
        "externalUrl": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Engineer_JR2026001",
        "jobReqId": "JR2026001",
        "questionnaireId": questionnaireId,
        "country": {"descriptor": "United States of America"},
        "id": "d9e6924a46c5",
        "postedOn": "Posted Today",
        "canApply": True, "posted": True, "includeResumeParsing": True,
    }


class TestStateCodes:
    def test_basic(self):
        assert board_dump._state_codes(
            ["US, CA, Santa Clara", "US, NC, Remote"]) == "CA;NC"

    def test_dedup(self):
        assert board_dump._state_codes(
            ["US, CA, Santa Clara", "US, CA, Remote"]) == "CA"

    def test_non_us(self):
        assert board_dump._state_codes(["Israel, Yokneam"]) == ""
        assert board_dump._state_codes(["US, Remote"]) == ""


class TestDeriveCsvRow:
    def _row(self, det=None, sig=None):
        r = _list_row()
        det = det or {"info": _detail_info(), "hiringOrg": "2100 NVIDIA USA",
                      "similarJobsCount": 5}
        return board_dump._derive_csv_row(
            r, det, sig, "NVIDIA", _TODAY_ISO, _TODAY_ISO)

    def test_column_contract(self):
        row = self._row()
        assert set(row) == set(board_dump.CSV_COLUMNS)
        assert len(board_dump.CSV_COLUMNS) == 49   # v2.6: +6 h1b (S11)

    def test_locations_enumerated_not_truncated(self):
        row = self._row(det={"info": _detail_info(addl=[
            "US, NC, Remote", "US, TX, Austin"]), "hiringOrg": "x",
            "similarJobsCount": 0})
        assert row["locations"] == ("US, CA, Santa Clara; "
                                    "US, NC, Remote; US, TX, Austin")
        assert row["nLocations"] == 3
        assert "Locations" not in row["locations"]   # never the truncation

    def test_single_location(self):
        row = self._row()
        assert row["locations"] == "US, CA, Santa Clara"
        assert row["nLocations"] == 1

    def test_remote_flag(self):
        row = self._row(det={"info": _detail_info(addl=["US, Remote"]),
                             "hiringOrg": "x", "similarJobsCount": 0})
        assert row["remoteFlag"] == "true"
        assert self._row()["remoteFlag"] == "false"

    def test_description_clean_text(self):
        row = self._row()
        assert "What you'll do:" in row["description"]
        assert "• Build GPU infrastructure." in row["description"]
        assert "<" not in row["description"]
        assert row["descriptionLength"] == len(row["description"])

    def test_metadata_fields(self):
        row = self._row()
        assert row["postingAgeDays"] == 1          # today − yesterday (dynamic)
        assert row["hiringOrg"] == "2100 NVIDIA USA"
        # S11/v2.5: the join key into questionnaires.csv (was "true")
        assert row["questionnaireId"] == "abc123"
        assert row["similarJobsCount"] == 5
        assert row["country"] == "United States of America"

    def test_questionnaire_id_absent_without_detail(self):
        """No detail record ⇒ no questionnaireId (empty, never a stale
        boolean) — same UNKNOWN-not-absent convention as the other
        detail-derived columns."""
        row = board_dump._derive_csv_row(
            _list_row(), {}, None, "NVIDIA", "2026-09-09", "2026-09-09")
        assert row["questionnaireId"] == ""

    def test_req_year_removed_from_contract(self):
        """S11: reqYear shipped 1966..2026 for an all-2026 board — the
        JR-number prefix is an ID-space artifact (0.1% startDate.year
        match), not a calendar signal. The column is GONE; this pin
        keeps it gone."""
        assert "reqYear" not in board_dump.CSV_COLUMNS
        row = self._row()
        assert "reqYear" not in row

    def test_no_detail_falls_back_to_list_fields(self):
        row = board_dump._derive_csv_row(
            _list_row(), {}, None, "NVIDIA", "2026-09-09", "2026-09-09")
        assert row["title"] == "Engineer"
        assert row["startDate"] == ""
        assert row["nLocations"] == 1      # locationsText fallback
        assert row["locations"] == "6 Locations"   # honest when no detail

    def test_signals_matched(self):
        sig = {
            "linkedin_url": "https://www.linkedin.com/jobs/view/1",
            "linkedin_posted_date": (_TODAY - _dt.timedelta(days=2)).isoformat(),
            "num_applicants": 122,
            "applicants_label": "122 applicants",
            "status": "matched",
            "match_method": "reqId",
            "fetched_at": "2026-09-09T01:00:00",
        }
        row = self._row(sig=sig)
        assert row["linkedinUrl"].endswith("/jobs/view/1")
        assert row["numApplicants"] == 122
        assert row["applicantLabel"] == "122 applicants"
        assert row["dateDeltaDays"] == -1      # LI (today−2) − WD (today−1)
        assert row["corroborationStatus"] == "matched"
        assert row["matchMethod"] == "reqId"
        assert row["corroboratedOn"] == "2026-09-09T01:00:00"

    def test_signals_not_checked_default(self):
        row = self._row()
        assert row["corroborationStatus"] == "not_checked"
        assert row["numApplicants"] == ""
        assert row["linkedinUrl"] == ""

    def test_days_left_blank_when_floor_elapsed(self):
        """S11/v2.5: the parsed sentence is 'accepted AT LEAST until' —
        a floor that auto-extends. A floor already elapsed on a LIVE
        posting means the window extended: daysLeftToApply is UNKNOWN
        (""), never negative (which reads 'closed N days ago' on an
        open posting — the #1 consumer confusion of the v2.4 review)."""
        past = _detail_info(desc=(
            "<p>Role.</p><p>Applications for this job will be accepted "
            "at least until January 1, 2026.</p>"))
        row = self._row(det={"info": past, "hiringOrg": "x",
                             "similarJobsCount": 0})
        assert row["applicationDeadline"] == "2026-01-01"   # kept: source truth
        assert row["daysLeftToApply"] == ""                 # extended: unknown

    def test_days_left_positive_when_floor_future(self):
        future = _detail_info(desc=(
            "<p>Role.</p><p>Applications for this job will be accepted "
            "at least until December 31, 2099.</p>"))
        row = self._row(det={"info": future, "hiringOrg": "x",
                             "similarJobsCount": 0})
        assert row["daysLeftToApply"] != ""
        assert int(row["daysLeftToApply"]) > 0

    def test_censored_on_measured_reset(self):
        """S11: firstSeenDate BEFORE the current startDate is measured
        repost evidence — the watch saw the req alive before the date it
        now carries, so startDate was reset after our first sighting ⇒
        age is a LOWER bound ⇒ censored=true (the mirror image of the
        crossSourceRepostEvidence class, witnessed on our own state)."""
        start = (_TODAY - _dt.timedelta(days=1)).isoformat()
        det = {"info": _detail_info(startDate=start), "hiringOrg": "x",
               "similarJobsCount": 0}
        # first_seen 2 days before startDate → reset happened
        row = board_dump._derive_csv_row(
            _list_row(), det, None, "NVIDIA", _TODAY_ISO,
            (_TODAY - _dt.timedelta(days=3)).isoformat())
        assert row["censored"] == "true"
        # first_seen AFTER startDate → no reset evidence (and startDate
        # is post-seed) → honest uncensored age
        row2 = board_dump._derive_csv_row(
            _list_row(), det, None, "NVIDIA", _TODAY_ISO,
            (_TODAY - _dt.timedelta(days=0)).isoformat())
        assert row2["censored"] == "false"


class TestQuestionnairesPhase:
    """S11: the application-questionnaire DEFINITIONS behind the
    per-posting questionnaireIds (the CSV v2.5 join key). Shared across
    postings (live: 4 distinct ids / 1,541 reqs) — fetched ONCE per id,
    appended to {out}.questionnaires.jsonl, joined at finish into the
    linked {out}.questionnaires.csv."""

    QID = "2f2764b3a82910064296831695150000"
    PAYLOAD = {
        "id": QID,
        "instructions": "<p><i>Immigration support assessment.</i></p>",
        "questions": [
            {"id": "q2", "body": "<p>Sponsorship required?</p>",
             "order": "b",
             "possibleAnswers": [
                 {"id": "a1", "answerText": "Yes", "order": "a"},
                 {"id": "a2", "answerText": "No", "order": "b"}],
             "required": True,
             "type": {"id": "t1",
                      "descriptor": "Multiple Choice - Single Select"}},
            {"id": "q1", "body": "<p>Authorized to work?</p>",
             "order": "a", "possibleAnswers": [], "required": False,
             "type": {"id": "t1",
                      "descriptor": "Multiple Choice - Single Select"}},
        ],
    }

    def _args(self):
        return type("A", (), {"board": "nvidia|wd5|x", "sleep": 0})()

    @staticmethod
    def _write_details(out, reqs):
        board_dump._atomic_write_text(
            out.with_suffix(".details.jsonl"),
            "\n".join(json.dumps({
                "reqId": rid, "info": _detail_info(questionnaireId=qid),
                "hiringOrg": "x", "similarJobsCount": 0})
                for rid, qid in reqs) + "\n")

    def test_fetch_dedups_and_appends(self, tmp_path, monkeypatch):
        from jobsearch.sources import workday
        out = tmp_path / "dump"
        # two reqs, ONE shared questionnaire id (+ one second id)
        self._write_details(out, [("JR1", self.QID), ("JR2", self.QID),
                                  ("JR3", "other999")])
        calls = []

        def fake_fetch(url, cfg=None, headers=None, **kw):
            calls.append(url)
            return json.loads(json.dumps(self.PAYLOAD))

        monkeypatch.setattr(workday, "fetch_json", fake_fetch)
        rc = board_dump.phase_questionnaires(self._args(), out)
        assert rc == 0
        # 2 distinct ids → exactly 2 fetches (dedup across reqs)
        assert len(calls) == 2
        assert any(self.QID in u for u in calls)
        recs = [json.loads(line) for line in
                out.with_suffix(".questionnaires.jsonl").read_text(
                    encoding="utf-8").splitlines() if line]
        assert len(recs) == 2
        by_id = {r["questionnaireId"]: r for r in recs}
        assert by_id[self.QID]["payload"]["questions"][0]["id"] == "q2"
        assert by_id[self.QID]["fetchedAt"]  # provenance stamped
        # RE-RUN: everything fetched → 0 new fetches, file unchanged
        rc2 = board_dump.phase_questionnaires(self._args(), out)
        assert rc2 == 0
        assert len(calls) == 2          # idempotent skip
        recs2 = [json.loads(line) for line in
                 out.with_suffix(".questionnaires.jsonl").read_text(
                     encoding="utf-8").splitlines() if line]
        assert len(recs2) == 2

    def test_failure_leaves_no_record_and_reraises_visibility(self, tmp_path,
                                                              monkeypatch):
        from jobsearch.sources import workday
        out = tmp_path / "dump"
        self._write_details(out, [("JR1", self.QID)])

        def fake_fetch(url, cfg=None, headers=None, **kw):
            return {"errorCode": "HTTP_404", "httpStatus": 404}

        monkeypatch.setattr(workday, "fetch_json", fake_fetch)
        rc = board_dump.phase_questionnaires(self._args(), out)
        assert rc == 1               # failed → chained callers see the gap
        assert not out.with_suffix(".questionnaires.jsonl").exists() or \
            not [l for l in
                 out.with_suffix(".questionnaires.jsonl").read_text(
                     encoding="utf-8").splitlines() if l.strip()]

    def test_no_details_file_is_rc2(self, tmp_path):
        out = tmp_path / "dump"
        assert board_dump.phase_questionnaires(self._args(), out) == 2

    def test_finish_emits_linked_questionnaires_csv(self, tmp_path):
        out = tmp_path / "dump"
        rows = [_list_row(req_id="JR1", title="Alpha Engineer")]
        board_dump._atomic_write_text(
            out.with_suffix(".list.jsonl"),
            "\n".join(json.dumps(r) for r in rows) + "\n")
        self._write_details(out, [("JR1", self.QID)])
        board_dump._atomic_write_text(
            out.with_suffix(".questionnaires.jsonl"),
            json.dumps({"questionnaireId": self.QID,
                        "fetchedAt": "2026-09-17T00:00:00Z",
                        "payload": self.PAYLOAD}) + "\n")
        args = type("A", (), {
            "board": "nvidia|wd5|x", "company": "NVIDIA", "country": "US",
            "time_type": "Full time", "require_details": False})()
        rc = board_dump.phase_finish(args, out)
        assert rc == 0
        # main CSV carries the JOIN KEY
        with open(out.with_suffix(".csv"), newline="",
                  encoding="utf-8-sig") as f:
            csv_rows = list(csv.DictReader(f))
        assert csv_rows[0]["questionnaireId"] == self.QID
        # linked CSV: one row per QUESTION, ordered by the payload's
        # own `order` key (q1 before q2), clean text, answers joined
        with open(out.with_suffix(".questionnaires.csv"), newline="",
                  encoding="utf-8-sig") as f:
            qrows = list(csv.DictReader(f))
        assert len(qrows) == 2
        assert [r["questionId"] for r in qrows] == ["q1", "q2"]
        assert qrows[0]["question"] == "Authorized to work?"
        assert qrows[0]["required"] == "false"
        assert qrows[1]["answers"] == "Yes; No"
        assert qrows[1]["required"] == "true"
        assert qrows[1]["type"] == "Multiple Choice - Single Select"
        assert "Immigration support assessment." in qrows[0]["instructions"]
        # report surfaces the join
        report = out.with_suffix(".report.txt").read_text()
        assert "questionnaire definitions: 1 id(s) covering 1/1 rows" in report

    def test_finish_warns_when_definition_missing(self, tmp_path, capsys):
        out = tmp_path / "dump"
        rows = [_list_row(req_id="JR1", title="Alpha Engineer")]
        board_dump._atomic_write_text(
            out.with_suffix(".list.jsonl"),
            "\n".join(json.dumps(r) for r in rows) + "\n")
        self._write_details(out, [("JR1", self.QID)])
        # NO questionnaires.jsonl — the definition was never fetched
        args = type("A", (), {
            "board": "nvidia|wd5|x", "company": "NVIDIA", "country": "US",
            "time_type": "Full time", "require_details": False})()
        rc = board_dump.phase_finish(args, out)
        assert rc == 0                    # enrichment gap, not a hard fail
        err = capsys.readouterr().err
        assert "WARNING" in err and "questionnaire" in err
        report = out.with_suffix(".report.txt").read_text()
        assert "MISSING" in report


class TestH1bWageBands:
    """S11/v2.6 #44-#49: the DOL H-1B/LCA wage-band join, AUDITED
    semantics (design review round): token-subset pools, domain-over-
    level ranking, role-block suppression, bounded wages, spaced status
    dialect, min-n=3 gates, primaryLocation-derived state."""

    @staticmethod
    def _rec(title, state, wage, unit="Year", status="Certified",
             case="C-1", pw="100000", ft="Y"):
        return {"caseNumber": case, "caseStatus": status,
                "jobTitle": title, "worksiteState": state,
                "wageFrom": str(wage), "wageUnit": unit,
                "prevailingWage": pw, "pwUnit": "Year",
                "fullTimePosition": ft,
                "employerName": "NVIDIA CORPORATION",
                "worksiteCity": "Santa Clara", "sourceFile": "FY2026_Q3"}

    def _pools(self, tmp_path, recs):
        out = tmp_path / "dump"
        board_dump._atomic_write_text(
            out.with_suffix(".h1b_lca.jsonl"),
            "\n".join(json.dumps(r) for r in recs) + "\n")
        pools, recs_out = board_dump._load_h1b_bands(out)
        return pools

    def test_annualization_matrix_and_gates(self):
        a = board_dump._annualize_wage
        assert a(self._rec("T", "CA", 200000)) == 200000.0
        assert a(self._rec("T", "CA", 100, unit="Hour")) == 208000.0
        assert a(self._rec("T", "CA", 10000, unit="Month")) == 120000.0
        # SPACED status dialect (live: "Certified - Withdrawn")
        assert a(self._rec("T", "CA", 150000,
                           status="Certified - Withdrawn")) == 150000.0
        assert a(self._rec("T", "CA", 1, status="Denied")) is None
        assert a(self._rec("T", "CA", 1, status="Withdrawn")) is None
        # part-time filings never band
        assert a(self._rec("T", "CA", 200000, ft="N")) is None
        # WAGE POISON bound: DOL unit-corruption rows (136k/Hour ->
        # $282.9M) are dropped, not averaged
        assert a(self._rec("T", "CA", 136000, unit="Hour")) is None
        assert a(self._rec("T", "CA", 211058, unit="Month")) is None
        assert a(self._rec("T", "CA", "n/a")) is None
        assert a(self._rec("T", "CA", 100, unit="Fortnight")) is None
        # unbounded view keeps the arithmetic for the extract CSV
        assert a(self._rec("T", "CA", 136000, unit="Hour"),
                 bounded=False) == 282880000.0

    def test_title_tokens_dialect_and_folding(self):
        t = board_dump._title_tokens
        # comma-form vs adjective-form share the set; plurals fold
        assert t("Engineer, Senior Systems Software") == \
            t("Senior System Software Engineer")
        assert t("Hardware Engineer, Electronics") == \
            t("Electronics Hardware Engineer")
        # stopwords + L-codes stripped; NO stemming (load-bearing:
        # engineering != engineer keeps managers out of SWE pools)
        assert "of" not in t("Director of Software Engineering")
        assert "l11" not in t("L11 System Product Development Engineer")
        assert t("Engineering Manager") != t("Software Engineer") or \
            "engineer" not in t("Engineering Manager")

    def test_subset_tier_and_domain_over_level_ranking(self, tmp_path):
        pools = self._pools(tmp_path, [
            # generic SWE pool (many)
            self._rec("Software Engineer", "CA", 200000, case="C-a1"),
            self._rec("Software Engineer", "CA", 210000, case="C-a2"),
            self._rec("Software Engineer", "CA", 220000, case="C-a3"),
            self._rec("Software Engineer", "TX", 180000, case="C-a4"),
            # senior-systems-software pool (the comma-form dialect)
            self._rec("Engineer, Senior Systems Software", "CA", 230000,
                      case="C-b1"),
            self._rec("Engineer, Senior Systems Software", "CA", 240000,
                      case="C-b2"),
            self._rec("Engineer, Senior Systems Software", "CA", 250000,
                      case="C-b3"),
        ])
        # posting form of the SAME title -> token-exact (title tier)
        out = board_dump._derive_h1b_columns(
            "Senior System Software Engineer", "US, CA, Santa Clara",
            pools)
        assert out["h1bMatchBasis"] == "title+state"
        assert out["h1bFilings"] == 3
        assert out["h1bMatchTitle"] == "Engineer, Senior Systems Software"
        # SPECIALIZED posting -> the 4-token subset pool (not the
        # generic 2-token one): domain conditioning wins
        out2 = board_dump._derive_h1b_columns(
            "Senior System Software Engineer - AV Platform",
            "US, CA, Santa Clara", pools)
        assert out2["h1bMatchBasis"] == "subset+state"
        assert out2["h1bFilings"] == 3
        # generic posting in TX: the TX state pool has n=1 (< 3) →
        # falls to the all-state pool (basis "title", n=4)
        out3 = board_dump._derive_h1b_columns(
            "Software Engineer", "US, TX, Austin", pools)
        assert out3["h1bMatchBasis"] == "title"
        assert out3["h1bFilings"] == 4

    def test_role_block_suppresses_occupation_changes(self, tmp_path):
        pools = self._pools(tmp_path, [
            self._rec("Software Engineer", "CA", 200000, case="C-1"),
            self._rec("Software Engineer", "CA", 210000, case="C-2"),
            self._rec("Software Engineer", "CA", 220000, case="C-3"),
        ])
        # QA / intern / test / marketing postings contain the SWE tokens
        # but are DIFFERENT occupations — suppressed, honest ""
        for title in ("Software Quality Assurance Engineer",
                      "Software Development Engineer in Test",
                      "Software Engineer Intern - Summer 2027",
                      "Senior Software QA Engineer"):
            out = board_dump._derive_h1b_columns(
                title, "US, CA, Santa Clara", pools)
            assert out["h1bMatchBasis"] == "", title

    def test_engineering_manager_never_bands_as_engineer(self, tmp_path):
        pools = self._pools(tmp_path, [
            self._rec("Software Engineer", "CA", 200000, case="C-1"),
            self._rec("Software Engineer", "CA", 210000, case="C-2"),
            self._rec("Software Engineer", "CA", 220000, case="C-3"),
        ])
        out = board_dump._derive_h1b_columns(
            "Engineering Manager, Deep Learning Inference",
            "US, CA, Santa Clara", pools)
        assert out["h1bMatchBasis"] == ""      # "engineering" not stemmed

    def test_min_n_gate_and_stateless_rows(self, tmp_path):
        pools = self._pools(tmp_path, [
            self._rec("Research Scientist", "CA", 210000, case="C-1"),
            self._rec("Research Scientist", "CA", 220000, case="C-2"),
        ])
        # all-state pool n=2 < 3 → no band
        out = board_dump._derive_h1b_columns(
            "Research Scientist", "US, Remote", pools)
        assert out["h1bMatchBasis"] == ""
        # remote/stateless primaryLocation → no state hop, all-state pool
        pools2 = self._pools(tmp_path, [
            self._rec("Research Scientist", "CA", 210000, case="C-1"),
            self._rec("Research Scientist", "WA", 220000, case="C-2"),
            self._rec("Research Scientist", "TX", 230000, case="C-3"),
        ])
        out2 = board_dump._derive_h1b_columns(
            "Research Scientist", "US, Remote", pools2)
        assert out2["h1bMatchBasis"] == "title"    # no +state, n=3 ok
        assert out2["h1bFilings"] == 3

    def test_percentiles_interpolate(self):
        vals = sorted([100000.0, 120000.0, 140000.0, 200000.0])
        p = board_dump._wage_pct
        assert p(vals, 0.25) == 115000   # k=0.75 → 100k+20k×0.75
        assert p(vals, 0.50) == 130000   # k=1.5  → 120k+20k×0.5
        assert p(vals, 0.75) == 155000   # k=2.25 → 140k+60k×0.25

    def test_no_extract_file_is_all_empty(self, tmp_path):
        pools, recs = board_dump._load_h1b_bands(tmp_path / "none")
        assert pools == {} and recs == []

    def test_finish_emits_bands_and_linked_csv(self, tmp_path):
        out = tmp_path / "dump"
        rows = [_list_row(req_id="JR1", title="Senior System Software "
                                               "Engineer")]
        board_dump._atomic_write_text(
            out.with_suffix(".list.jsonl"),
            "\n".join(json.dumps(r) for r in rows) + "\n")
        board_dump._atomic_write_text(
            out.with_suffix(".details.jsonl"),
            json.dumps({"reqId": "JR1", "info": _detail_info() | {
                "title": "Senior System Software Engineer"},
                "hiringOrg": "x", "similarJobsCount": 0}) + "\n")
        recs = [
            self._rec("Engineer, Senior Systems Software", "CA", 220000,
                      case="C-1"),
            self._rec("Senior Systems Software Engineer", "CA", 240000,
                      case="C-2", status="Certified - Withdrawn"),
            self._rec("Engineer, Senior Systems Software", "TX", 180000,
                      case="C-3"),
            self._rec("Engineer, Senior Systems Software", "CA", 136000,
                      unit="Hour", case="C-4"),     # poison: dropped
            self._rec("Software Engineer", "CA", 190000, case="C-5"),
            self._rec("Software Engineer", "CA", 200000, case="C-6"),
            self._rec("Software Engineer", "CA", 210000, case="C-7"),
        ]
        board_dump._atomic_write_text(
            out.with_suffix(".h1b_lca.jsonl"),
            "\n".join(json.dumps(r) for r in recs) + "\n")
        args = type("A", (), {
            "board": "nvidia|wd5|x", "company": "NVIDIA", "country": "US",
            "time_type": "Full time", "require_details": False})()
        rc = board_dump.phase_finish(args, out)
        assert rc == 0
        with open(out.with_suffix(".csv"), newline="",
                  encoding="utf-8-sig") as f:
            csv_rows = list(csv.DictReader(f))
        r0 = csv_rows[0]
        # token-exact tier (reorder-tolerant): the comma-form pool —
        # CA filings C-1+C-2 = n=2 < 3 → the state hop is REJECTED by
        # the min-n gate, falls to the all-state pool (C-1,C-2,C-3).
        # Poison C-4 dropped everywhere (unit corruption).
        assert r0["h1bMatchBasis"] == "title"
        assert r0["h1bFilings"] == "3"
        assert r0["h1bWageP50"] == "220000"
        assert r0["h1bMatchTitle"] == "Engineer, Senior Systems Software"
        # linked extract CSV carries ALL 7 filings; poison annualizes ""
        with open(out.with_suffix(".h1b_lca.csv"), newline="",
                  encoding="utf-8-sig") as f:
            h_rows = list(csv.DictReader(f))
        assert len(h_rows) == 7
        ann = {r["caseNumber"]: r["annualizedWage"] for r in h_rows}
        assert ann["C-4"] == ""
        assert ann["C-2"] == "240000"
        report = out.with_suffix(".report.txt").read_text()
        assert "h1b wage bands: 1/1 rows banded" in report


class TestH1bExtractor:
    """scripts/h1b_extract.py — the quarterly DOL LCA extract builder
    (filter + dedup + truncation guard; the download paths are
    transport-dependent and live-validated in the workflow)."""

    @classmethod
    def setup_class(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "h1b_extract", REPO_ROOT / "scripts" / "h1b_extract.py")
        cls.h1b = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("h1b_extract", cls.h1b)
        spec.loader.exec_module(cls.h1b)

    @staticmethod
    def _xlsx_bytes(records):
        """A real (in-memory) LCA-shaped xlsx: header row + records."""
        import io
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        cols = [c for c, _f in TestH1bExtractor.h1b._FIELDS]
        ws.append(cols)
        for rec in records:
            ws.append([rec.get(c, "") for c in cols])
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_extract_filters_employer_and_maps_fields(self):
        body = self._xlsx_bytes([
            {"CASE_NUMBER": "C-1", "EMPLOYER_NAME": "NVIDIA CORPORATION",
             "JOB_TITLE": "Software Engineer",
             "WAGE_RATE_OF_PAY_FROM": "200000",
             "WAGE_UNIT_OF_PAY": "Year",
             "WORKSITE_STATE": "CA"},
            {"CASE_NUMBER": "C-2", "EMPLOYER_NAME": "Acme Inc",
             "JOB_TITLE": "Software Engineer",
             "WAGE_RATE_OF_PAY_FROM": "100000",
             "WAGE_UNIT_OF_PAY": "Year", "WORKSITE_STATE": "NY"},
        ])
        rows, total = self.h1b._extract_rows(body, "NVIDIA", "FY2026_Q3")
        assert total == 2
        assert len(rows) == 1                 # Acme filtered out
        r = rows[0]
        assert r["caseNumber"] == "C-1"
        assert r["jobTitle"] == "Software Engineer"
        assert r["wageFrom"] == "200000"
        assert r["wageUnit"] == "Year"
        assert r["worksiteState"] == "CA"

    def test_extract_multi_employer_or_list(self):
        """S12: --employer accepts a comma list — one company files under
        several legal names (TENCENT AMERICA / Tencent America, Inc.)."""
        body = self._xlsx_bytes([
            {"CASE_NUMBER": "C-1", "EMPLOYER_NAME": "TENCENT AMERICA",
             "JOB_TITLE": "Engineer", "WAGE_RATE_OF_PAY_FROM": "1",
             "WAGE_UNIT_OF_PAY": "Year", "WORKSITE_STATE": "CA"},
            {"CASE_NUMBER": "C-2",
             "EMPLOYER_NAME": "Tencent America, Inc.",
             "JOB_TITLE": "Engineer", "WAGE_RATE_OF_PAY_FROM": "2",
             "WAGE_UNIT_OF_PAY": "Year", "WORKSITE_STATE": "CA"},
            {"CASE_NUMBER": "C-3", "EMPLOYER_NAME": "JD.COM INC.",
             "JOB_TITLE": "Engineer", "WAGE_RATE_OF_PAY_FROM": "3",
             "WAGE_UNIT_OF_PAY": "Year", "WORKSITE_STATE": "CA"},
            {"CASE_NUMBER": "C-4", "EMPLOYER_NAME": "Acme Inc",
             "JOB_TITLE": "Engineer", "WAGE_RATE_OF_PAY_FROM": "4",
             "WAGE_UNIT_OF_PAY": "Year", "WORKSITE_STATE": "NY"},
        ])
        rows, total = self.h1b._extract_rows(
            body, "TENCENT,JD.COM", "FY2026_Q3")
        assert total == 4
        assert {r["caseNumber"] for r in rows} == {"C-1", "C-2", "C-3"}

    def test_extract_dedups_by_case_number(self, tmp_path):
        out_path = tmp_path / "x.h1b_lca.jsonl"
        out_path.write_text(json.dumps(
            {"caseNumber": "C-1", "jobTitle": "T"}) + "\n",
            encoding="utf-8")
        have = self.h1b._existing_case_numbers(out_path)
        assert have == {"C-1"}

    def test_truncated_zip_raises(self):
        body = self._xlsx_bytes([
            {"CASE_NUMBER": "C-1", "EMPLOYER_NAME": "NVIDIA CORPORATION"}])
        with pytest.raises(RuntimeError, match="truncated|zip"):
            self.h1b._extract_rows(body[:len(body) // 2], "NVIDIA", "Q")

    def test_file_url_shape(self):
        assert self.h1b._file_url("FY2025_Q4").endswith(
            "/LCA_Disclosure_Data_FY2025_Q4.xlsx")
        # run-2 live finding: FY2026_Q3 publishes ONLY at /media/ —
        # the canonical /sites/ path 404s for it
        assert "/media/" in self.h1b._file_url("FY2026_Q3", alt=True)
        assert "/sites/" in self.h1b._file_url("FY2026_Q3")

    def test_download_404_propagates_as_FileNotFoundError(self):
        """Run-4 live failure: _download wrapped per-transport 404s in
        a blanket RuntimeError, making the caller's alt-path retry
        (except FileNotFoundError) DEAD CODE. The 404 verdict must
        propagate — including when another transport merely 403s
        (Akamai noise, not a path verdict)."""
        import requests as _rq
        import unittest.mock as _mock

        class Fake404:
            status_code = 404
            content = b""
            headers = {}
            def raise_for_status(self):
                raise _rq.HTTPError("404")

        class Fake403:
            status_code = 403
            def raise_for_status(self):
                raise _rq.HTTPError("403")

        cfg = type("C", (), {})()
        # single-transport 404 → FileNotFoundError (was: RuntimeError)
        with _mock.patch.object(_rq, "get", return_value=Fake404()):
            with pytest.raises(FileNotFoundError):
                self.h1b._download("https://x/y.xlsx", "direct", cfg,
                                   timeout=5)

        # mixed verdict: impersonate 404 (definitive) + direct 403
        # (Akamai noise) → still FileNotFoundError. curl_cffi is a
        # bootstrap extra (not always installed) — stub the module:
        # _fetch_via imports it INSIDE the function, so a sys.modules
        # injection takes effect at call time.
        import types

        class FakeCreq404:
            status_code = 404
            content = b""
            headers = {}
            def raise_for_status(self):
                raise RuntimeError("404")

        fake_creq = types.ModuleType("curl_cffi.requests")
        fake_creq.get = lambda *a, **kw: FakeCreq404()
        fake_cffi = types.ModuleType("curl_cffi")
        fake_cffi.requests = fake_creq
        with _mock.patch.object(_rq, "get", return_value=Fake403()), \
             _mock.patch.dict(sys.modules,
                              {"curl_cffi": fake_cffi,
                               "curl_cffi.requests": fake_creq}):
            with pytest.raises(FileNotFoundError):
                self.h1b._download("https://x/y.xlsx", "auto", cfg,
                                   timeout=5)

    def test_quarter_listing_keeps_fy_prefix(self, monkeypatch):
        """Run-1 live failure: quarters listed WITHOUT 'FY' built
        404 URLs (…_2025_Q1.xlsx). The prefix is part of the id."""
        import requests as _rq
        captured = {}

        class FakeResp:
            text = ('<a href="/x/LCA_Disclosure_Data_FY2025_Q1.xlsx"></a>'
                    '<a href="/x/LCA_Disclosure_Data_FY2026_Q3.xlsx"></a>')
            def raise_for_status(self):
                pass

        def fake_get(url, **kw):
            captured["url"] = url
            return FakeResp()

        monkeypatch.setattr(_rq, "get", fake_get)
        quarters = self.h1b._list_quarters(type("C", (), {
            "supabase_proxy_url": "https://p.example/fn",
            "supabase_proxy_token": "t"})())
        assert quarters == ["FY2025_Q1", "FY2026_Q3"]
        for q in quarters:
            assert self.h1b._file_url(q).count(
                f"LCA_Disclosure_Data_{q}.xlsx") == 1


class TestLoadJsonlTolerance:
    def test_corrupt_tail_tolerated(self, tmp_path):
        p = tmp_path / "x.jsonl"
        p.write_text('{"a": 1}\n{"a": 2}\n{"a": 3', encoding="utf-8")
        rows = board_dump._load_jsonl(p)
        assert [r["a"] for r in rows] == [1, 2]

    def test_empty(self, tmp_path):
        assert board_dump._load_jsonl(tmp_path / "nope.jsonl") == []


class TestCsvRoundTrip:
    def test_newline_in_cell_and_bom(self, tmp_path):
        path = tmp_path / "out.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=board_dump.CSV_COLUMNS)
            w.writeheader()
            w.writerow({"reqId": "JR1", "description": "line1\nline2",
                        **{c: "" for c in board_dump.CSV_COLUMNS
                           if c not in ("reqId", "description")}})
        raw = path.read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf"        # BOM (Excel-safe bullets)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["description"] == "line1\nline2"


class TestListStatusGuard:
    """B1: partial list must be distinguishable — .list.status records the
    completeness verdict; watch refuses gone-events off incomplete lists."""

    def test_status_written(self, tmp_path, monkeypatch):
        from jobsearch.sources import workday

        calls = {"n": 0}

        def fake_page(board, facets, offset, cfg):
            calls["n"] += 1
            if offset == 0:
                return {"total": 30, "jobPostings": [
                    {"title": f"J{i}", "externalPath": f"/job/{i}",
                     "locationsText": "US", "postedOn": "Posted Today",
                     "bulletFields": [f"JR{i}"]} for i in range(20)]}
            return {"total": 0, "jobPostings": []}   # wall → partial

        monkeypatch.setattr(workday, "_page", fake_page)
        args = type("A", (), {"board": "nvidia|wd5|x", "country": "",
                              "time_type": "", "sleep": 0})()
        out = tmp_path / "out"
        rc = board_dump.phase_list(args, out)
        status = json.loads(
            out.with_suffix(".list.status").read_text())
        # 20 rows collected but first-page total 30 → incomplete
        assert status["complete"] is False
        assert status["rows"] == 20
        assert status["total"] == 30
        assert rc == 1


class TestFinishJoin:
    def test_reqid_then_title_with_match_method(self, tmp_path, monkeypatch):
        out = tmp_path / "dump"
        rows = [_list_row(req_id="JR1", title="Alpha Engineer"),
                _list_row(req_id="JR2", title="Beta Engineer"),
                _list_row(req_id="JR3", title="Gamma Engineer")]
        board_dump._atomic_write_text(
            out.with_suffix(".list.jsonl"),
            "\n".join(json.dumps(r) for r in rows) + "\n")
        board_dump._atomic_write_text(
            out.with_suffix(".details.jsonl"),
            "\n".join(json.dumps({
                "reqId": r["reqId"], "info": _detail_info(),
                "hiringOrg": "2100 NVIDIA USA", "similarJobsCount": 1})
                for r in rows) + "\n")
        # JR1 matched by reqId; JR2 matched by title only; JR3 no match
        signals = [
            {"job_req_id": "JR1", "status": "matched",
             "num_applicants": 5, "applicants_label": "5 applicants",
             "linkedin_posted_date": "2026-09-08",
             "linkedin_url": "https://x/1", "title": "Alpha Engineer",
             "company": "NVIDIA"},
            {"job_req_id": "", "status": "matched",
             "num_applicants": 7, "applicants_label": "7 applicants",
             "linkedin_posted_date": "2026-09-08",
             "linkedin_url": "https://x/2", "title": "Beta Engineer",
             "company": "NVIDIA"},
        ]
        board_dump._atomic_write_text(
            out.with_suffix(".signals.jsonl"),
            "\n".join(json.dumps(s) for s in signals) + "\n")
        args = type("A", (), {
            "board": "nvidia|wd5|x", "company": "NVIDIA", "country": "US",
            "time_type": "Full time", "require_details": False})()
        rc = board_dump.phase_finish(args, out)
        assert rc == 0
        with open(out.with_suffix(".csv"), newline="",
                  encoding="utf-8-sig") as f:
            csv_rows = {r["reqId"]: r for r in csv.DictReader(f)}
        assert csv_rows["JR1"]["matchMethod"] == "reqId"
        assert csv_rows["JR1"]["numApplicants"] == "5"
        assert csv_rows["JR2"]["matchMethod"] == "title"
        assert csv_rows["JR2"]["numApplicants"] == "7"
        assert csv_rows["JR3"]["corroborationStatus"] == "not_checked"
        # JSON v2 exists with nested signals
        payload = json.loads(out.with_suffix(".json").read_text())
        assert payload["count"] == 3
        assert payload["signals_matched"] == 2


class TestFinishB5Statuses:
    """B5 posting-level statuses (S7-A2 D): no_match emitted when the index
    exhausted; blocked when the matched card's fetch was blocked;
    not_checked only when the index is incomplete."""

    def _finish(self, tmp_path, signals, index_done=True):
        out = tmp_path / "dump"
        rows = [_list_row(req_id="JR1", title="Alpha Engineer"),
                _list_row(req_id="JR2", title="Beta Engineer")]
        board_dump._atomic_write_text(
            out.with_suffix(".list.jsonl"),
            "\n".join(json.dumps(r) for r in rows) + "\n")
        board_dump._atomic_write_text(
            out.with_suffix(".details.jsonl"),
            "\n".join(json.dumps({"reqId": r["reqId"], "info": _detail_info(),
                                  "hiringOrg": "x", "similarJobsCount": 0})
                      for r in rows) + "\n")
        board_dump._atomic_write_text(
            out.with_suffix(".signals.jsonl"),
            "\n".join(json.dumps(s) for s in signals) + "\n")
        if index_done:
            board_dump._atomic_write_text(
                out.with_suffix(".li_index.meta.json"),
                json.dumps({"done": True}))
        args = type("A", (), {"board": "nvidia|wd5|x", "company": "NVIDIA",
                               "country": "US", "time_type": "Full time",
                               "require_details": False})()
        board_dump.phase_finish(args, out)
        with open(out.with_suffix(".csv"), newline="",
                  encoding="utf-8-sig") as f:
            return {r["reqId"]: r for r in csv.DictReader(f)}

    def test_no_match_when_index_done(self, tmp_path):
        csv_rows = self._finish(tmp_path, signals=[])
        assert csv_rows["JR1"]["corroborationStatus"] == "no_match"
        assert csv_rows["JR2"]["corroborationStatus"] == "no_match"

    def test_not_checked_when_index_incomplete(self, tmp_path):
        csv_rows = self._finish(tmp_path, signals=[], index_done=False)
        assert csv_rows["JR1"]["corroborationStatus"] == "not_checked"

    def test_blocked_card_surfaces_blocked_status(self, tmp_path):
        signals = [{"linkedin_job_id": "9", "linkedin_url": "u/9",
                    "title": "Beta Engineer", "company": "NVIDIA",
                    "location": "x", "status": "blocked",
                    "linkedin_posted_date": "2026-09-08"}]
        csv_rows = self._finish(tmp_path, signals=signals)
        assert csv_rows["JR2"]["corroborationStatus"] == "blocked"
        # and no applicant count fabricated for a blocked fetch
        assert csv_rows["JR2"]["numApplicants"] == ""

    def test_one_card_one_req_no_fanout_in_finish(self, tmp_path):
        # two cards in the SAME key family as BOTH rows ("Alpha Engineer"
        # and "Alpha Engineer - Core" normalize to one key) → greedy 1:1
        # assigns exactly ONE req; the other honestly reads no_match.
        signals = [
            {"linkedin_job_id": "1", "linkedin_url": "u/1",
             "title": "Alpha Engineer", "company": "NVIDIA", "location": "x",
             "status": "matched", "num_applicants": 32,
             "linkedin_posted_date": _YESTERDAY,
             "fetched_at": "2026-09-10T00:00:00"},
            {"linkedin_job_id": "2", "linkedin_url": "u/2",
             "title": "Alpha Engineer - Core", "company": "NVIDIA",
             "location": "y", "status": "matched", "num_applicants": 32,
             "linkedin_posted_date": _YESTERDAY,
             "fetched_at": "2026-09-10T00:00:00"}]
        csv_rows = self._finish(tmp_path, signals=signals)
        matched = [r for r in csv_rows.values()
                   if r["corroborationStatus"] == "matched"]
        # both cards share JR1's key family; JR2 ("Beta") has NO cards.
        # 1:1 greedy: exactly ONE card serves JR1 (never both), JR2
        # honestly reads no_match. (Pre-fix N:1 bug: 1 card → 31 reqs.)
        assert len(matched) == 1
        assert matched[0]["reqId"] == "JR1"
        assert matched[0]["linkedinUrl"] in {"u/1", "u/2"}
        assert matched[0]["corroboratedOn"] == "2026-09-10T00:00:00"
        other = next(r for r in csv_rows.values() if r["reqId"] != "JR1")
        assert other["corroborationStatus"] == "no_match"


class TestFinishLocationTiebreak:
    """S8-E1 (research §f R3b): phase_finish feeds the Workday detail
    locations into the title join — for duplicated titles, the card in
    the req's city wins even when the wrong-city card is date-closer."""

    def test_same_title_reqs_get_location_correct_cards(self, tmp_path):
        out = tmp_path / "dump"
        rows = [_list_row(req_id="JR1", title="Field Engineer"),
                _list_row(req_id="JR2", title="Field Engineer")]
        board_dump._atomic_write_text(
            out.with_suffix(".list.jsonl"),
            "\n".join(json.dumps(r) for r in rows) + "\n")
        details = {
            "JR1": {"reqId": "JR1",
                    "info": dict(_detail_info(),
                                 location="US, CA, Santa Clara"),
                    "hiringOrg": "x", "similarJobsCount": 0},
            "JR2": {"reqId": "JR2",
                    "info": dict(_detail_info(), location="US, TX, Austin"),
                    "hiringOrg": "x", "similarJobsCount": 0},
        }
        board_dump._atomic_write_text(
            out.with_suffix(".details.jsonl"),
            "\n".join(json.dumps(details[r["reqId"]]) for r in rows)
            + "\n")
        # card B (Santa Clara) is 29 days staler than card A (Austin) —
        # location overlap must beat date proximity for BOTH reqs
        signals = [
            {"linkedin_job_id": "A", "linkedin_url": "u/A",
             "title": "Field Engineer", "company": "NVIDIA",
             "location": "Austin, TX", "status": "matched",
             "num_applicants": 40, "linkedin_posted_date": _YESTERDAY,
             "fetched_at": "2026-09-14T00:00:00"},
            {"linkedin_job_id": "B", "linkedin_url": "u/B",
             "title": "Field Engineer", "company": "NVIDIA",
             "location": "Santa Clara, CA", "status": "matched",
             "num_applicants": 60,
             "linkedin_posted_date":
                 (_TODAY - _dt.timedelta(days=30)).isoformat(),
             "fetched_at": "2026-09-14T00:00:00"},
        ]
        board_dump._atomic_write_text(
            out.with_suffix(".signals.jsonl"),
            "\n".join(json.dumps(s) for s in signals) + "\n")
        board_dump._atomic_write_text(
            out.with_suffix(".li_index.meta.json"),
            json.dumps({"done": True}))
        args = type("A", (), {"board": "nvidia|wd5|x", "company": "NVIDIA",
                               "country": "US", "time_type": "Full time",
                               "require_details": False})()
        board_dump.phase_finish(args, out)
        with open(out.with_suffix(".csv"), newline="",
                  encoding="utf-8-sig") as f:
            csv_rows = {r["reqId"]: r for r in csv.DictReader(f)}
        # Santa Clara req ← Santa Clara card; Austin req ← Austin card
        assert csv_rows["JR1"]["linkedinUrl"] == "u/B"
        assert csv_rows["JR2"]["linkedinUrl"] == "u/A"
        assert csv_rows["JR1"]["matchMethod"] == "title"
        assert csv_rows["JR1"]["numApplicants"] == "60"


# ── phase_details resumability (audit S7-B3 A3 — was untested) ────────────
def _li_card(cid, title="Senior Engineer", company="NVIDIA") -> dict:
    """Index-card shape mirroring nvidia_us_fulltime.li_index.jsonl
    (line 1: id/title/company/location/date/url)."""
    return {"id": str(cid), "title": title, "company": company,
            "location": "Santa Clara, CA", "date": "2026-09-05",
            "url": f"https://www.linkedin.com/jobs/view/{cid}"}


def _signal(cid, status="matched", req_id="", title="Senior Engineer",
            company="NVIDIA", location="Santa Clara, CA") -> dict:
    """Signal-record shape mirroring nvidia_us_fulltime.signals.jsonl
    (the provider's 13-key contract)."""
    return {"linkedin_job_id": str(cid),
            "linkedin_url": f"https://www.linkedin.com/jobs/view/{cid}",
            "title": title, "company": company, "location": location,
            "linkedin_posted_date": "2026-09-05",
            "num_applicants": 25, "applicants_label": "25 applicants",
            "job_req_id": req_id, "posted_time_ago": "4 days ago",
            "closed": False, "status": status,
            "fetched_at": "2026-09-10T01:51:44"}


def _det_args(**over) -> object:
    base = {"board": "nvidia|wd5|x", "details_batch": 150,
            "detail_sleep": 0.0, "refetch_similar": False}
    base.update(over)
    return type("A", (), base)()


class TestPhaseDetailsResume:
    """The wave-1 3-strike retry (cd7fd2e) shipped with ZERO tests on the
    orchestration: a regression in the settled/three_strikes filters or
    the attempts accumulation would go green."""

    def _write_list(self, tmp_path, req_ids):
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            "\n".join(json.dumps(_list_row(req_id=rid))
                      for rid in req_ids) + "\n", encoding="utf-8")
        return out

    def _run(self, out, monkeypatch, fail: set):
        def fake_detail(board, path, cfg):
            # path carries the reqId (see _list_row's externalPath)
            rid = path.rsplit("_", 1)[-1]
            if rid in fail:
                return None
            return {"jobPostingInfo": _detail_info(),
                    "hiringOrganization": {"name": "2100 NVIDIA USA"},
                    "similarJobs": [{"t": 1}, {"t": 2}]}

        monkeypatch.setattr(board_dump.workday, "detail_payload",
                            fake_detail)
        return board_dump.phase_details(_det_args(), out)

    def _lines(self, out):
        return [json.loads(x) for x in
                out.with_suffix(".details.jsonl").read_text(
                    encoding="utf-8").splitlines()]

    def test_error_row_retried_and_attempts_accumulate(self, tmp_path,
                                                       monkeypatch):
        out = self._write_list(tmp_path, ["JR1", "JR2", "JR3"])
        out.with_suffix(".details.jsonl").write_text(
            json.dumps({"reqId": "JR1", "error": "detail_unreachable",
                        "attempts": 1}) + "\n" +
            json.dumps({"reqId": "JR2", "error": "detail_unreachable",
                        "attempts": 3}) + "\n", encoding="utf-8")
        rc = self._run(out, monkeypatch, fail={"JR1", "JR2"})
        # S9-audit C4: a batch that writes error records now exits 1
        # (retryable but attention-needing — was fail-green rc 0)
        assert rc == 1
        lines = self._lines(out)
        # JR2 is past the 3-strike cap → SKIPPED (still exactly 1 line);
        # JR1 (attempts=1) retried → attempts=2; JR3 fetched fresh
        by_rid: dict[str, list] = {}
        for d in lines:
            by_rid.setdefault(d["reqId"], []).append(d)
        assert len(by_rid["JR2"]) == 1
        assert len(by_rid["JR1"]) == 2
        assert by_rid["JR1"][-1]["error"] == "detail_unreachable"
        assert by_rid["JR1"][-1]["attempts"] == 2
        assert by_rid["JR3"][0]["info"]["title"] == "Engineer"
        assert by_rid["JR3"][0]["hiringOrg"] == "2100 NVIDIA USA"
        assert by_rid["JR3"][0]["similarJobsCount"] == 2

        # strike 3: next failure settles JR1 for good
        self._run(out, monkeypatch, fail={"JR1", "JR2"})
        assert len(self._lines(out)) == 5   # +JR1(attempts=3) only
        assert self._lines(out)[-1]["attempts"] == 3

        # past the cap: nothing retried, nothing appended
        self._run(out, monkeypatch, fail={"JR1", "JR2"})
        assert len(self._lines(out)) == 5

    def test_empty_info_200_settles_as_error_record_with_attempts(
            self, tmp_path, monkeypatch):
        """S9-audit F3/C1 (the info:{} zombie), end-to-end through the
        REAL workday.detail_payload: an HTTP-200 body WITHOUT
        jobPostingInfo must land in phase_details' error path —
        error='detail_unreachable' + attempts accumulate → the 3-strike
        cap settles it — never a never-settled {"reqId", "info": {}}
        record that re-fetches forever and ships detailError="". """

        def fake_fetch_json(url, *, cfg, headers=None, **k):
            # path carries the reqId (see _list_row's externalPath)
            if url.endswith("_JR1"):
                return {"someOtherKey": 1}    # 200, req taken down
            return {"jobPostingInfo": _detail_info(),
                    "hiringOrganization": {"name": "2100 NVIDIA USA"},
                    "similarJobs": []}

        monkeypatch.setattr(board_dump.workday, "fetch_json",
                            fake_fetch_json)
        out = self._write_list(tmp_path, ["JR1", "JR2"])
        rc = board_dump.phase_details(_det_args(), out)
        # S9-audit C4: error records written this run → rc 1
        assert rc == 1
        lines = self._lines(out)
        by_rid = {d["reqId"]: d for d in lines}
        assert by_rid["JR2"]["info"]["title"] == "Engineer"   # settled
        # the zombie shape is GONE: JR1 is an error record with attempts
        assert "info" not in by_rid["JR1"]
        assert by_rid["JR1"]["error"] == "detail_unreachable"
        assert by_rid["JR1"]["attempts"] == 1

        # strikes 2 and 3: attempts accumulate, then the cap settles it
        board_dump.phase_details(_det_args(), out)
        board_dump.phase_details(_det_args(), out)
        jr1 = [d for d in self._lines(out) if d["reqId"] == "JR1"]
        assert [d["attempts"] for d in jr1] == [1, 2, 3]
        assert len(self._lines(out)) == 4          # JR2 never re-fetched
        board_dump.phase_details(_det_args(), out)
        assert len(self._lines(out)) == 4          # past the cap: no retry

    def test_settled_rows_are_never_refetched(self, tmp_path, monkeypatch):
        out = self._write_list(tmp_path, ["JR1", "JR2"])
        out.with_suffix(".details.jsonl").write_text(
            json.dumps({"reqId": "JR1", "info": _detail_info(),
                        "hiringOrg": "x", "similarJobsCount": 0}) + "\n" +
            json.dumps({"reqId": "JR2", "error": "detail_unreachable",
                        "attempts": 1}) + "\n", encoding="utf-8")
        calls: list = []

        def fake_detail(board, path, cfg):
            calls.append(path)
            return None

        monkeypatch.setattr(board_dump.workday, "detail_payload",
                            fake_detail)
        board_dump.phase_details(_det_args(), out)
        # only JR2's path is fetched — JR1 is settled
        assert calls == ["/job/US-CA-Santa-Clara/Engineer_JR2"]

    def test_no_list_file_is_a_clean_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(board_dump.workday, "detail_payload",
                            lambda b, p, c: None)
        rc = board_dump.phase_details(_det_args(), tmp_path / "nope")
        assert rc == 2


# ── phase_corroborate resumability (audit S7-B3 A3 — was untested) ───────
def _cor_args(**over) -> object:
    base = {"board": "nvidia|wd5|x", "company": "NVIDIA",
            "location": "United States", "corroborate_index": False,
            "li_index_pages": 10, "li_index_cards": 100,
            "signals_batch": 40, "provider": "linkedin",
            "refetch_similar": False}
    base.update(over)
    return type("A", (), base)()


class _RecordingProvider:
    """Provider stand-in at the corroborate.get_provider seam (records
    every index_cards/index_cards_partitioned/fetch_signals call for
    contract assertions)."""

    name = "linkedin"

    def __init__(self, index_cards=None, index_exc=None,
                 fetch=None, fetch_exc_after=None):
        self._index_cards = index_cards or ([], 0, True)
        self.index_exc = index_exc
        self._fetch = fetch
        self.fetch_exc_after = fetch_exc_after
        self.index_calls: list[dict] = []
        self.partitioned_calls: list[dict] = []
        self.fetch_calls: list[list] = []

    def index_cards(self, *, company, location, max_pages, max_cards,
                    start_offset=0):
        self.index_calls.append({"company": company, "location": location,
                                 "max_pages": max_pages,
                                 "max_cards": max_cards,
                                 "start_offset": start_offset})
        if self.index_exc:
            raise self.index_exc
        return self._index_cards

    def index_cards_partitioned(self, *, company, slices=None,
                                max_pages_per_slice=3, max_cards=1000):
        self.partitioned_calls.append({
            "company": company, "slices": slices,
            "max_pages_per_slice": max_pages_per_slice,
            "max_cards": max_cards})
        if self.index_exc:
            raise self.index_exc
        return self._index_cards

    def fetch_signals(self, cards, on_record=None):
        self.fetch_calls.append([c["id"] for c in cards])
        if self._fetch is not None:
            return self._fetch(cards, on_record)
        # default: a matched record per card, checkpointed via on_record
        out = []
        for c in cards:
            rec = _signal(c["id"])
            out.append(rec)
            if on_record:
                on_record(rec)
        return out


class TestPhaseCorroborateBlockedRetry:
    def test_blocked_cards_retried_matched_never(self, tmp_path,
                                                 monkeypatch):
        """S7-A2 G regression: blocked fetches were permanently skipped
        (done-set included them); matched must never re-fetch."""
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            "\n".join(json.dumps(_list_row(req_id=rid))
                      for rid in ("JR1", "JR2")) + "\n", encoding="utf-8")
        out.with_suffix(".li_index.jsonl").write_text(
            "\n".join(json.dumps(_li_card(i)) for i in (1, 2)) + "\n",
            encoding="utf-8")
        out.with_suffix(".signals.jsonl").write_text(
            json.dumps(_signal(1, status="blocked")) + "\n" +
            json.dumps(_signal(2, status="matched", req_id="JR2")) + "\n",
            encoding="utf-8")
        # the phase writes ONLY via on_record (the returned list is for
        # counting — "NO batch append (would duplicate)" in the source);
        # a fetch fake that ignores on_record writes NOTHING. Mirror the
        # real provider contract: emit per record through the callback.
        def re_fetch(cards, on_record):
            recs = []
            for c in cards:
                rec = _signal(c["id"])
                recs.append(rec)
                if on_record:
                    on_record(rec)
            return recs

        provider = _RecordingProvider(fetch=re_fetch)
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: provider)
        rc = board_dump.phase_corroborate(_cor_args(), out)
        assert rc == 0
        # ONLY the blocked card is re-fetched (card 1); matched card 2
        # is settled — never re-fetched
        assert provider.fetch_calls == [["1"]]
        lines = [json.loads(x) for x in
                 out.with_suffix(".signals.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert [l["linkedin_job_id"] for l in lines] == ["1", "2", "1"]
        assert lines[-1]["status"] == "matched"   # blocked → re-fetched OK


class TestPhaseCorroborateCheckpoint:
    def test_on_record_survives_mid_batch_crash(self, tmp_path,
                                                monkeypatch):
        """Per-card checkpoint contract: fetch_signals has a no-raise
        contract in the REAL provider, but a hard kill (runner timeout,
        OOM) mid-batch must lose NOTHING already fetched — the crash
        propagates and records 1-2 are already on disk."""
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row(req_id="JR1")) + "\n", encoding="utf-8")
        out.with_suffix(".li_index.jsonl").write_text(
            "\n".join(json.dumps(_li_card(i)) for i in (1, 2, 3)) + "\n",
            encoding="utf-8")

        def crashing_fetch(cards, on_record):
            for i, c in enumerate(cards):
                if i == 2:
                    raise RuntimeError("mid-batch kill")
                rec = _signal(c["id"])
                if on_record:
                    on_record(rec)
            return []

        provider = _RecordingProvider(fetch=crashing_fetch)
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: provider)
        with pytest.raises(RuntimeError, match="mid-batch kill"):
            board_dump.phase_corroborate(_cor_args(), out)
        # records for cards 1-2 were checkpointed BEFORE the crash —
        # the next run resumes from card 3, not from scratch
        lines = [json.loads(x) for x in
                 out.with_suffix(".signals.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert [l["linkedin_job_id"] for l in lines] == ["1", "2"]
        assert all(l["status"] == "matched" for l in lines)


class TestPhaseCorroborateIndexResume:
    def test_resumes_from_meta_offset_and_dedups(self, tmp_path,
                                                 monkeypatch):
        """Pre-existing index + meta {offset: 20, done: false}: the next
        --corroborate-index run passes start_offset=20, accumulates new
        cards (dedup by id), and rewrites the meta."""
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row(req_id="JR1")) + "\n", encoding="utf-8")
        out.with_suffix(".li_index.jsonl").write_text(
            "\n".join(json.dumps(_li_card(i)) for i in (1, 2)) + "\n",
            encoding="utf-8")
        out.with_suffix(".li_index.meta.json").write_text(
            json.dumps({"offset": 20, "done": False}), encoding="utf-8")
        # card 2 is a DUPLICATE of a prior card (wrap-page quirk) — must
        # not double-appear; 21/22 are new
        new_cards = [_li_card(2), _li_card(21), _li_card(22)]
        provider = _RecordingProvider(
            index_cards=(new_cards, 40, False),
            fetch=lambda cards, on_record: [])
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: provider)
        rc = board_dump.phase_corroborate(
            _cor_args(corroborate_index=True), out)
        assert rc == 0
        # resumed exactly where the meta said
        assert provider.index_calls[0]["start_offset"] == 20
        cards = [json.loads(x) for x in
                 out.with_suffix(".li_index.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert [c["id"] for c in cards] == ["1", "2", "21", "22"]
        meta = json.loads(
            out.with_suffix(".li_index.meta.json").read_text())
        assert meta["offset"] == 40          # next_offset persisted
        assert meta["done"] is False
        assert meta["cards"] == 4            # deduped total

    def test_done_index_is_never_refreshed(self, tmp_path, monkeypatch):
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row(req_id="JR1")) + "\n", encoding="utf-8")
        out.with_suffix(".li_index.jsonl").write_text(
            json.dumps(_li_card(1)) + "\n", encoding="utf-8")
        out.with_suffix(".li_index.meta.json").write_text(
            json.dumps({"offset": 100, "done": True}), encoding="utf-8")
        provider = _RecordingProvider()
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: provider)
        board_dump.phase_corroborate(_cor_args(corroborate_index=True),
                                     out)
        # done=true → the (costly) index refresh is skipped entirely
        assert provider.index_calls == []
        # meta untouched
        assert json.loads(
            out.with_suffix(".li_index.meta.json").read_text())["done"] \
            is True

    def test_page0_blocked_marks_meta_not_the_phase(self, tmp_path,
                                                    monkeypatch):
        """CorroborationBlocked at page 0: recorded in meta (blocked:
        True, offset preserved) and the phase stays green (B5 no-raise
        at the orchestration level)."""
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row(req_id="JR1")) + "\n", encoding="utf-8")
        out.with_suffix(".li_index.meta.json").write_text(
            json.dumps({"offset": 20, "done": False}), encoding="utf-8")
        provider = _RecordingProvider(
            index_exc=board_dump.corroborate.CorroborationBlocked(
                "429 wall"))
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: provider)
        rc = board_dump.phase_corroborate(
            _cor_args(corroborate_index=True), out)
        assert rc == 0                    # blocked ≠ fatal
        meta = json.loads(
            out.with_suffix(".li_index.meta.json").read_text())
        assert meta["blocked"] is True
        assert meta["offset"] == 20       # progress preserved for retry
        assert provider.fetch_calls == []  # signals pointless w/o index

    def test_no_list_file_is_a_clean_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: _RecordingProvider())
        rc = board_dump.phase_corroborate(_cor_args(), tmp_path / "nope")
        assert rc == 2

    def test_join_preview_loads_details_for_population_parity(
            self, tmp_path, monkeypatch, capsys):
        """H11 P2 (E3 gap #4, second half): the corroborate join preview
        MUST load details and feed req_dates/req_locations into
        join_population (board_dump :668-676 — every prior fixture seeded
        an EMPTY details file, so a details-load regression here drifted
        the preview from what finish answers for with the suite green).
        Two same-title reqs + one verbatim card: with details the near-
        date req wins the proximity disambiguation (title-tier match)."""
        title = "Senior Engineer"
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            "\n".join(json.dumps(_list_row(req_id=rid, title=title))
                      for rid in ("JR1", "JR2")) + "\n", encoding="utf-8")
        out.with_suffix(".signals.jsonl").write_text(
            json.dumps(_signal(9, title=title)) + "\n", encoding="utf-8")
        out.with_suffix(".details.jsonl").write_text("\n".join(json.dumps({
            "reqId": rid, "info": dict(
                _detail_info(startDate=sd), title=title,
                additionalLocations=addl)})
            for rid, sd, addl in (("JR1", "2026-01-01", None),
                                   ("JR2", "2026-09-04",
                                    ["US, TX, Austin"]))) + "\n",
            encoding="utf-8")
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: _RecordingProvider())
        rc = board_dump.phase_corroborate(_cor_args(), out)
        assert rc == 0
        out_all = capsys.readouterr().out
        # the preview line reflects the details-driven composition:
        # 0 reqId-tier, 1 title-tier (JR2 by date proximity), 1 signal
        assert "join preview: 0 by reqId, +1 by title, 1 signals total" \
            in out_all


class TestPhaseCorroboratePartitionedIndex:
    """S8-E1 (research §c/§f R1): --index-mode partitioned routes the
    index refresh through provider.index_cards_partitioned (slice
    matrix); 'single' stays the default for back-compat."""

    def test_li_reindex_reopens_a_done_meta(self, tmp_path, monkeypatch):
        """S10: done:true pins the index to its completion date — a
        same-mode refresh cycle MUST be able to force a re-run
        (--li-reindex) to discover cards posted since. Without the flag
        the refresh is skipped (existing semantics preserved)."""
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row(req_id="JR1")) + "\n", encoding="utf-8")
        # a COMPLETED partitioned index (the live 09-13 shape)
        out.with_suffix(".li_index.jsonl").write_text(
            json.dumps(_li_card(1)) + "\n", encoding="utf-8")
        out.with_suffix(".li_index.meta.json").write_text(
            json.dumps({"offset": 0, "done": True, "mode": "partitioned",
                        "cards": 1,
                        "indexed_at": "2026-09-13T12:19:56"}), )
        provider = _RecordingProvider(
            index_cards=([_li_card(1), _li_card(9)], 0, True))
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: provider)
        # WITHOUT the flag: same-mode done meta → refresh SKIPPED
        rc = board_dump.phase_corroborate(
            _cor_args(corroborate_index=True, index_mode="partitioned"),
            out)
        assert rc == 0
        assert provider.partitioned_calls == []
        # WITH the flag: re-opened, slice matrix re-runs, new card
        # accumulates (dedup keeps card 1 once)
        rc = board_dump.phase_corroborate(
            _cor_args(corroborate_index=True, index_mode="partitioned",
                      li_reindex=True), out)
        assert rc == 0
        assert len(provider.partitioned_calls) == 1
        cards = [json.loads(x) for x in
                 out.with_suffix(".li_index.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert [c["id"] for c in cards] == ["1", "9"]
        meta = json.loads(
            out.with_suffix(".li_index.meta.json").read_text())
        assert meta["done"] is True
        assert meta["mode"] == "partitioned"
        assert meta["cards"] == 2

    def test_partitioned_mode_calls_partitioned_index(self, tmp_path,
                                                       monkeypatch):
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row(req_id="JR1")) + "\n", encoding="utf-8")
        # prior single-mode progress: card 1 already indexed; the slice
        # matrix re-serves 1 (dupe) + new 2 — union-dedup keeps both once
        out.with_suffix(".li_index.jsonl").write_text(
            json.dumps(_li_card(1)) + "\n", encoding="utf-8")
        provider = _RecordingProvider(
            index_cards=([_li_card(1), _li_card(2)], 0, True))
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: provider)
        rc = board_dump.phase_corroborate(
            _cor_args(corroborate_index=True, index_mode="partitioned",
                      li_slice_pages=3), out)
        assert rc == 0
        assert provider.index_calls == []       # single path NOT used
        assert len(provider.partitioned_calls) == 1
        call = provider.partitioned_calls[0]
        assert call["company"] == "NVIDIA"
        assert call["max_pages_per_slice"] == 3
        assert call["max_cards"] \
            == board_dump.corroborate.PARTITIONED_MAX_CARDS
        assert call["slices"] is None      # provider derives the matrix
        cards = [json.loads(x) for x in
                 out.with_suffix(".li_index.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert [c["id"] for c in cards] == ["1", "2"]
        meta = json.loads(
            out.with_suffix(".li_index.meta.json").read_text())
        assert meta["mode"] == "partitioned"
        assert meta["done"] is True
        assert meta["offset"] == 0          # slices restart at 0 on re-run
        assert meta["cards"] == 2

    def test_default_single_mode_untouched(self, tmp_path, monkeypatch):
        """Back-compat: args WITHOUT index_mode (old callers / fixtures)
        still route through index_cards with the meta resume offset."""
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row(req_id="JR1")) + "\n", encoding="utf-8")
        provider = _RecordingProvider(
            index_cards=([_li_card(5)], 10, False))
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: provider)
        rc = board_dump.phase_corroborate(
            _cor_args(corroborate_index=True), out)   # no index_mode key
        assert rc == 0
        assert provider.partitioned_calls == []
        assert len(provider.index_calls) == 1
        assert provider.index_calls[0]["start_offset"] == 0
        meta = json.loads(
            out.with_suffix(".li_index.meta.json").read_text())
        assert meta["mode"] == "single"

    def test_partitioned_blocked_page0_marks_meta(self, tmp_path,
                                                   monkeypatch):
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row(req_id="JR1")) + "\n", encoding="utf-8")
        out.with_suffix(".li_index.meta.json").write_text(
            json.dumps({"offset": 0, "done": False}), encoding="utf-8")
        provider = _RecordingProvider(
            index_exc=board_dump.corroborate.CorroborationBlocked(
                "429 wall"))
        monkeypatch.setattr(board_dump.corroborate, "get_provider",
                            lambda name, cfg=None: provider)
        rc = board_dump.phase_corroborate(
            _cor_args(corroborate_index=True, index_mode="partitioned"),
            out)
        assert rc == 0                         # blocked ≠ fatal (B5)
        meta = json.loads(
            out.with_suffix(".li_index.meta.json").read_text())
        assert meta["blocked"] is True
        assert provider.fetch_calls == []       # signals pointless w/o index


# ── RAW-preservation pin (audit S7-B3 A8 — fixture-fidelity drift) ────────
RAW_DIR = REPO_ROOT / "ingest" / "data" / "workday"
RAW_STEM = RAW_DIR / "nvidia_us_fulltime"


def _load_raw_pair(require_addl: bool = False):
    """A (list-row, detail-record) pair from the COMMITTED NVIDIA v2
    dump artifacts — REAL payload shapes, not the hand-rolled fixtures.
    (509/1360 real detail records carry additionalLocations; the local
    fixture family never did — that drift is exactly what this pins.)"""
    det_path = RAW_STEM.with_suffix(".details.jsonl")
    list_path = RAW_STEM.with_suffix(".list.jsonl")
    if not det_path.exists() or not list_path.exists():
        pytest.skip("committed RAW workday artifacts absent (fresh "
                    "clone without data/?)")
    list_rows = {json.loads(x)["reqId"]: json.loads(x) for x in
                 list_path.read_text(encoding="utf-8").splitlines()
                 if x.strip()}
    for line in det_path.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        info = d.get("info") or {}
        if not info:
            continue
        if require_addl and not info.get("additionalLocations"):
            continue
        r = list_rows.get(d["reqId"])
        if r:
            return r, d
    pytest.skip("no RAW list/detail pair with the required shape")


class TestRawFidelity:
    """Run the REAL committed record through the REAL _derive_csv_row —
    the RAW-preservation contract had no pinning test anywhere (audit
    S7-B3 A8: the _detail_info fixture omits 3 of the 17 real
    jobPostingInfo fields, so 'every jobPostingInfo field survives
    phase_details' was asserted by nothing)."""

    # every jobPostingInfo key the info path of _derive_csv_row reads —
    # the tripwire for both directions of drift: the API dropping a
    # field, or the fixture family omitting one the real payload has
    INFO_KEYS_READ = ("title", "location", "jobDescription", "startDate",
                      "timeType", "postedOn", "externalUrl", "country",
                      "questionnaireId")

    def _derive(self, r, det):
        today = _dt.date.today()
        return board_dump._derive_csv_row(
            r, det, None, r.get("company") or "NVIDIA",
            today.isoformat(), today.isoformat()), today

    def test_first_raw_record_derives_cleanly(self):
        r, det = _load_raw_pair()
        info = det["info"]
        for k in self.INFO_KEYS_READ:
            assert k in info, (
                f"RAW jobPostingInfo missing {k!r} — the fixture family "
                f"has drifted from reality (audit A8 class)")
        row, today = self._derive(r, det)
        # full column contract off REAL data (extras silently dropped
        # by DictWriter otherwise)
        assert set(row) == set(board_dump.CSV_COLUMNS)
        # startDate passthrough + postingAgeDays = today − startDate
        assert row["startDate"] == info["startDate"]
        # S9-audit B2r: postingAgeDays is snapshot-frozen; this derive
        # passes no snapshot_date so the snapshot IS today. Assert
        # against the CAPTURED today (a fresh date.today() here is a
        # midnight-crossing time bomb — P3, S9-CLOSE residual).
        assert row["postingAgeDays"] == (
            today - _dt.date.fromisoformat(info["startDate"])).days
        # description = html_to_text of the REAL jobDescription; exact
        assert row["description"] == \
            board_dump.html_to_text(info["jobDescription"])
        assert row["descriptionLength"] == len(row["description"])
        assert "<" not in row["description"]
        # questionnaireId passes through (v2.5 join key — was a boolean)
        assert row["questionnaireId"] == (info.get("questionnaireId")
                                         or "")
        # locations enumerated from info: [location] + additionalLocations
        addl = info.get("additionalLocations") or []
        expected = [info["location"]] + list(addl)
        assert row["locations"] == "; ".join(expected)
        assert row["nLocations"] == len(expected)
        assert row["primaryLocation"] == info["location"]
        assert row["stateCodes"] == \
            board_dump._state_codes(expected)
        assert row["remoteFlag"] == ("true" if any(
            "remote" in loc.lower() for loc in expected) else "false")
        # metadata passthroughs from RAW
        assert row["timeType"] == info["timeType"]
        assert row["country"] == info["country"]["descriptor"]
        assert row["title"] == info["title"]
        assert row["hiringOrg"] == det.get("hiringOrg")
        assert row["similarJobsCount"] == det.get("similarJobsCount") or 0
        assert row["url"] == r["url"]
        # S11/v2.5: the raw questionnaireId PASSES THROUGH (join key)
        assert row["questionnaireId"] == (info.get("questionnaireId")
                                          or "")
        assert row["detailError"] == ""    # info present → no error col

    def test_raw_multi_location_record(self):
        """0A against reality: a record that ACTUALLY has
        additionalLocations (the fixture family never models one)."""
        r, det = _load_raw_pair(require_addl=True)
        info = det["info"]
        row, _today = self._derive(r, det)
        expected = [info["location"]] + list(info["additionalLocations"])
        assert len(expected) >= 2
        assert row["locations"] == "; ".join(expected)
        assert row["nLocations"] == len(expected)
        assert "Locations" not in row["locations"][:12]  # never trunc.
        assert row["remoteFlag"] == ("true" if any(
            "remote" in loc.lower() for loc in expected) else "false")
        # stateCodes dedup across primary+additional
        assert row["stateCodes"] == board_dump._state_codes(expected)


# ── v2.1 helpers: slug reqId/repost counter + deadline parsing (S8-E3) ────

class TestSlugHelpers:
    def test_req_id_from_slug(self):
        assert board_dump._slug_req_id(
            "/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/"
            "Senior-Software-Engineer_JR2024930-1") == "JR2024930"
        assert board_dump._slug_req_id(
            "/job/US-CA-Santa-Clara/Engineer_JR2026001") == "JR2026001"
        assert board_dump._slug_req_id("/job/Engineer") == ""
        assert board_dump._slug_req_id("") == ""

    def test_repost_count_from_slug(self):
        # real suffix shapes from the 09-09 dump (148×-1, 2×-2, 1×-3)
        assert board_dump._slug_repost_count(
            "/job/Applied-AI-Engineer_JR2018179-3") == 3
        assert board_dump._slug_repost_count(
            "/job/US-CA-Santa-Clara/Engineer_JR2026001") == 0
        # a number INSIDE the title is not a suffix — only the trailing
        # -N on the reqId token counts
        assert board_dump._slug_repost_count(
            "/job/Title--With-42_JR2026001") == 0
        assert board_dump._slug_repost_count("/job/Engineer") == 0
        assert board_dump._slug_repost_count("") == 0


class TestApplicationDeadline:
    def test_primary_phrasing(self):
        # the board's standard sentence (1337/1360 real rows)
        desc = ("NVIDIA is hiring for this team. The minimum salary is "
                "listed. Applications for this job will be accepted at "
                "least until April 11, 2026. NVIDIA is an equal opportunity "
                "employer.")
        assert board_dump._parse_application_deadline(desc) == "2026-04-11"

    def test_variant_phrasing(self):
        assert board_dump._parse_application_deadline(
            "Applications are accepted until May 1, 2026.") == "2026-05-01"

    def test_wrapped_sentence(self):
        # clean text can wrap mid-sentence (html_to_text line breaks)
        assert board_dump._parse_application_deadline(
            "Applications for this job will be accepted at least until\n"
            "September 30, 2026.") == "2026-09-30"

    def test_absent(self):
        assert board_dump._parse_application_deadline("No dates here.") == ""
        assert board_dump._parse_application_deadline("") == ""


# ── v2.1 CSV columns (S8-C §7 / S8-D gap #5) ──────────────────────────────

def _v21_row(start="2026-09-08", end_date=None, req_id="JR2026001",
             suffix="", snapshot="2026-09-13", facet_tags=None,
             desc=None):
    r = _list_row(req_id=req_id)
    r["externalPath"] = f"/job/US-CA-Santa-Clara/Engineer_{req_id}{suffix}"
    info = _detail_info(startDate=start,
                        desc=desc if desc is not None else "<p>Build.</p>")
    if end_date is not None:
        info["endDate"] = end_date
    det = {"info": info, "hiringOrg": "2100 NVIDIA USA", "similarJobsCount": 0}
    return board_dump._derive_csv_row(
        r, det, None, "NVIDIA", snapshot, snapshot,
        facet_tags=facet_tags, snapshot_date=snapshot)


class TestV21Columns:
    def test_watch_seed_constant(self):
        # 2026-09-09 = the board-watch's first observation (S8-C §7.2)
        assert board_dump.WATCH_SEED.isoformat() == "2026-09-09"

    def test_days_on_market_uses_snapshot_date(self):
        row = _v21_row(start="2026-09-08", snapshot="2026-09-13")
        assert row["daysOnMarket"] == 5
        assert row["daysOnMarketBasis"] == "startDate"
        # S9-audit B2r flip (E2 time-bomb #4): postingAgeDays is now
        # FROZEN to the snapshot like every other derived date (was
        # wall-clock today — regenerating a day later silently shifted
        # the whole column)
        assert row["postingAgeDays"] == 5

    def test_days_on_market_missing_start_date(self):
        row = _v21_row(start="")
        assert row["daysOnMarket"] == ""
        assert row["daysOnMarketBasis"] == ""
        assert row["censored"] == "true"    # no evidence = pure lower bound

    def test_censored_seed_boundary(self):
        # startDate < 2026-09-09 → age is a lower bound only
        assert _v21_row(start="2026-09-08")["censored"] == "true"
        # startDate == the seed → observed from (possible) birth onward
        assert _v21_row(start="2026-09-09")["censored"] == "false"
        assert _v21_row(start="2026-09-10")["censored"] == "false"

    def test_end_date_passthrough(self):
        assert _v21_row(end_date="2026-11-30")["endDate"] == "2026-11-30"
        assert _v21_row()["endDate"] == ""

    def test_v23_earliest_evidence_when_card_predates(self):
        # S9: card date 2026-04-25 < startDate 2026-09-08 → floor moves,
        # basis says so, evidence flagged reqId (confirmed repost)
        sig = {"status": "matched", "match_method": "reqId",
               "linkedin_posted_date": "2026-04-25",
               "num_applicants": 12, "fetched_at": "2026-09-15T00:00:00"}
        row = board_dump._derive_csv_row(
            _list_row(), {"info": _detail_info(startDate="2026-09-08")},
            sig, "NVIDIA", "2026-09-15", "2026-09-15",
            snapshot_date="2026-09-15")
        assert row["earliestEvidenceDate"] == "2026-04-25"
        assert row["crossSourceRepostEvidence"] == "reqId"
        assert row["daysOnMarketBasis"] == "startDate+linkedin"
        assert row["daysOnMarket"] == 143          # 2026-09-15 − 2026-04-25

    def test_v23_no_evidence_when_card_newer(self):
        # cross-post lag: card NEWER than startDate → startDate stays floor
        sig = {"status": "matched", "match_method": "title",
               "linkedin_posted_date": "2026-09-12",
               "num_applicants": 12, "fetched_at": "2026-09-15T00:00:00"}
        row = board_dump._derive_csv_row(
            _list_row(), {"info": _detail_info(startDate="2026-09-08")},
            sig, "NVIDIA", "2026-09-15", "2026-09-15",
            snapshot_date="2026-09-15")
        assert row["earliestEvidenceDate"] == "2026-09-08"
        assert row["crossSourceRepostEvidence"] == ""
        assert row["daysOnMarketBasis"] == "startDate"
        assert row["daysOnMarket"] == 7

    def test_v23_unmatched_row_empty(self):
        row = _v21_row(start="2026-09-10")
        assert row["earliestEvidenceDate"] == "2026-09-10"
        assert row["crossSourceRepostEvidence"] == ""

    def test_repost_count_from_slug(self):
        assert _v21_row(suffix="-3")["repostCount"] == 3
        assert _v21_row()["repostCount"] == 0

    def test_last_reset_date_reserved_empty(self):
        assert _v21_row()["lastResetDate"] == ""

    def test_deadline_from_description_text(self):
        desc = ("<p>Applications for this job will be accepted at least "
                "until December 31, 2026.</p>")
        row = _v21_row(desc=desc, snapshot="2026-09-13")
        assert row["applicationDeadline"] == "2026-12-31"
        assert row["daysLeftToApply"] == 109

    def test_deadline_falls_back_to_structured_end_date(self):
        row = _v21_row(desc="<p>No deadline sentence.</p>",
                       end_date="2026-11-30", snapshot="2026-09-13")
        assert row["applicationDeadline"] == "2026-11-30"
        assert row["daysLeftToApply"] == 78

    def test_text_deadline_preferred_over_end_date(self):
        desc = "<p>Applications are accepted until September 20, 2026.</p>"
        row = _v21_row(desc=desc, end_date="2026-11-30",
                       snapshot="2026-09-13")
        assert row["applicationDeadline"] == "2026-09-20"
        assert row["daysLeftToApply"] == 7

    def test_no_deadline_at_all(self):
        row = _v21_row(desc="<p>Nothing.</p>")
        assert row["applicationDeadline"] == ""
        assert row["daysLeftToApply"] == ""

    def test_facet_tag_columns(self):
        tags = {"JR2026001": {"workerSubType": "Intern (Fixed Term)",
                              "jobFamilyGroup": "Engineering"}}
        row = _v21_row(facet_tags=tags)
        assert row["workerSubType"] == "Intern (Fixed Term)"
        assert row["jobFamilyGroup"] == "Engineering"
        # absent tags / phase never run → empty columns
        row = _v21_row(facet_tags={"JR9999": {"workerSubType": "X"}})
        assert row["workerSubType"] == ""
        assert row["jobFamilyGroup"] == ""

    def test_snapshot_date_defaults_to_today(self):
        row = board_dump._derive_csv_row(
            _list_row(), {"info": _detail_info(), "hiringOrg": "x",
                          "similarJobsCount": 0}, None, "NVIDIA",
            _TODAY_ISO, _TODAY_ISO)
        assert row["daysOnMarket"] == row["postingAgeDays"]


# ── similarJobs capture in phase_details (S8-D gap #1) ────────────────────

class TestSimilarJobsCapture:
    def _payload(self, similar):
        return {"jobPostingInfo": _detail_info(),
                "hiringOrganization": {"name": "2100 NVIDIA USA"},
                "similarJobs": similar}

    def _run(self, tmp_path, monkeypatch, similar):
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row(req_id="JR1")) + "\n", encoding="utf-8")
        monkeypatch.setattr(board_dump.workday, "detail_payload",
                            lambda b, p, c: self._payload(similar))
        rc = board_dump.phase_details(_det_args(), out)
        assert rc == 0
        return json.loads(out.with_suffix(".details.jsonl").read_text(
            encoding="utf-8").splitlines()[0])

    def test_capture_trims_entries_with_derived_reqids(self, tmp_path,
                                                       monkeypatch):
        similar = [
            {"title": "Senior Engineer",
             "externalPath": "/x/job/US-CA/Senior-Engineer_JR9",
             "timeType": "Full time", "locationsText": "US, CA, Santa Clara",
             "postedOn": "Posted 3 Days Ago", "startDate": "2026-09-10"},
            {"title": "Staff Engineer",
             "externalPath": "/x/job/US-CA/Staff-Engineer_JR10-2"},
            {"title": "No Slug Entry"},
        ]
        rec = self._run(tmp_path, monkeypatch, similar)
        assert rec["similarJobsCount"] == 3       # the count keeps RAW truth
        assert rec["similarJobs"] == [
            {"reqId": "JR9", "title": "Senior Engineer",
             "externalPath": "/x/job/US-CA/Senior-Engineer_JR9"},
            {"reqId": "JR10", "title": "Staff Engineer",
             "externalPath": "/x/job/US-CA/Staff-Engineer_JR10-2"},
            {"reqId": "", "title": "No Slug Entry", "externalPath": None},
        ]

    def test_capture_bounds_at_five_entries(self, tmp_path, monkeypatch):
        similar = [{"title": f"T{i}", "externalPath": f"/x/job/T{i}_JR{i}"}
                   for i in range(7)]
        rec = self._run(tmp_path, monkeypatch, similar)
        assert rec["similarJobsCount"] == 7       # raw count preserved
        assert len(rec["similarJobs"]) == 5       # list bounded (API cap)


# ── finish: similar_edges.jsonl + facet-tag join + census report ──────────

def _finish_args(**over):
    base = {"board": "nvidia|wd5|x", "company": "NVIDIA", "country": "US",
            "time_type": "Full time", "require_details": False}
    base.update(over)
    return type("A", (), base)()


def _write_dump_files(out, req_ids=("JR1", "JR2"), similar_by_req=None):
    board_dump._atomic_write_text(
        out.with_suffix(".list.jsonl"),
        "\n".join(json.dumps(_list_row(req_id=rid)) for rid in req_ids)
        + "\n")
    det_lines = []
    for rid in req_ids:
        det = {"reqId": rid, "info": _detail_info(), "hiringOrg": "x",
               "similarJobsCount": 0}
        if similar_by_req and rid in similar_by_req:
            det["similarJobs"] = similar_by_req[rid]
            det["similarJobsCount"] = len(similar_by_req[rid])
        det_lines.append(json.dumps(det))
    board_dump._atomic_write_text(out.with_suffix(".details.jsonl"),
                                  "\n".join(det_lines) + "\n")


class TestFinishEdges:
    def test_edges_file_rank_and_skip_unidentifiable(self, tmp_path):
        out = tmp_path / "dump"
        _write_dump_files(out, similar_by_req={
            "JR1": [{"reqId": "JR9", "title": "Senior Engineer",
                     "externalPath": "/x/JR9"},
                    {"reqId": "JR10", "title": "Staff Engineer",
                     "externalPath": "/x/JR10"}],
            "JR2": [{"reqId": "", "title": "No Slug",
                     "externalPath": None},
                    {"reqId": "JR9", "title": "Senior Engineer",
                     "externalPath": "/x/JR9"}],
        })
        rc = board_dump.phase_finish(_finish_args(), out)
        assert rc == 0
        edges = [json.loads(l) for l in
                 out.with_suffix(".similar_edges.jsonl").read_text(
                     encoding="utf-8").splitlines()]
        assert edges == [
            {"reqId": "JR1", "similar_reqId": "JR9",
             "similar_title": "Senior Engineer", "rank": 1},
            {"reqId": "JR1", "similar_reqId": "JR10",
             "similar_title": "Staff Engineer", "rank": 2},
            # the unidentifiable entry is skipped; rank still reflects
            # the ORIGINAL list position (2nd entry → rank 2)
            {"reqId": "JR2", "similar_reqId": "JR9",
             "similar_title": "Senior Engineer", "rank": 2},
        ]
        report = out.with_suffix(".report.txt").read_text()
        assert "similar-job edges: 3" in report
        payload = json.loads(out.with_suffix(".json").read_text())
        assert payload["similar_edges"] == 3

    def test_no_similar_jobs_yields_empty_edges_file(self, tmp_path):
        out = tmp_path / "dump"
        _write_dump_files(out)
        board_dump.phase_finish(_finish_args(), out)
        edges_path = out.with_suffix(".similar_edges.jsonl")
        assert edges_path.exists()
        assert edges_path.read_text(encoding="utf-8").strip() == ""
        assert "similar-job edges: 0" in \
            out.with_suffix(".report.txt").read_text()


class TestFinishFacetTags:
    def _finish(self, tmp_path, tag_lines=None):
        out = tmp_path / "dump"
        _write_dump_files(out, req_ids=("JR1", "JR2"))
        if tag_lines is not None:
            board_dump._atomic_write_text(
                out.with_suffix(".facet_tags.jsonl"),
                "\n".join(json.dumps(t) for t in tag_lines) + "\n")
        rc = board_dump.phase_finish(_finish_args(), out)
        assert rc == 0
        with open(out.with_suffix(".csv"), newline="",
                  encoding="utf-8-sig") as f:
            csv_rows = {r["reqId"]: r for r in csv.DictReader(f)}
        report = out.with_suffix(".report.txt").read_text()
        return csv_rows, report

    def test_tags_joined_onto_csv_and_reported(self, tmp_path):
        csv_rows, report = self._finish(tmp_path, tag_lines=[
            {"reqId": "JR1", "workerSubType": "Intern (Fixed Term)"},
            {"reqId": "JR2", "workerSubType": "Regular Employee"},
            {"facetDone": "workerSubType",
             "value": "Intern (Fixed Term)"},
            {"reqId": "JR1", "jobFamilyGroup": "Engineering"},
            {"facetDone": "jobFamilyGroup", "value": "Engineering"},
        ])
        assert csv_rows["JR1"]["workerSubType"] == "Intern (Fixed Term)"
        assert csv_rows["JR1"]["jobFamilyGroup"] == "Engineering"
        assert csv_rows["JR2"]["workerSubType"] == "Regular Employee"
        assert csv_rows["JR2"]["jobFamilyGroup"] == ""   # untagged → empty
        assert "facet-tagged rows: workerSubType=2 jobFamilyGroup=1" \
            in report

    def test_absent_file_leaves_columns_empty(self, tmp_path):
        csv_rows, report = self._finish(tmp_path, tag_lines=None)
        assert csv_rows["JR1"]["workerSubType"] == ""
        assert csv_rows["JR1"]["jobFamilyGroup"] == ""
        assert "facet-tagged rows" not in report

    def test_duplicate_tag_lines_are_last_wins(self, tmp_path):
        csv_rows, _ = self._finish(tmp_path, tag_lines=[
            {"reqId": "JR1", "workerSubType": "Regular Employee"},
            {"reqId": "JR1", "workerSubType": "Intern (Fixed Term)"},
        ])
        assert csv_rows["JR1"]["workerSubType"] == "Intern (Fixed Term)"

    def test_coverage_line_and_warning_when_untagged(self, tmp_path,
                                                     capsys):
        """S9-C3 P2: finish makes the artifact's coverage gap VISIBLE —
        a zero-tag row (the Sep-15 drift shape) AND a partial row (has
        workerSubType, misses jobFamilyGroup — the incomplete-tail
        shape) both count as untagged, in report.txt, stdout and stderr
        (rc semantics unchanged)."""
        out = tmp_path / "dump"
        _write_dump_files(out, req_ids=("JR1", "JR2", "JR3"))
        board_dump._atomic_write_text(
            out.with_suffix(".facet_tags.jsonl"),
            "\n".join(json.dumps(t) for t in [
                {"reqId": "JR1", "workerSubType": "Intern (Fixed Term)"},
                {"reqId": "JR1", "jobFamilyGroup": "Engineering"},
                {"reqId": "JR2", "workerSubType": "Regular Employee"},
                # JR2: no jobFamilyGroup (param present via JR1 →
                # partial row); JR3: no tags at all
            ]) + "\n")
        assert board_dump.phase_finish(_finish_args(), out) == 0
        report = out.with_suffix(".report.txt").read_text()
        assert "facet tag coverage: 1/3 rows (2 untagged)" in report
        captured = capsys.readouterr()
        assert "facet tag coverage: 1/3 rows (2 untagged)" in captured.out
        assert "WARNING: 2 board row(s) have no facet tags" in captured.err
        assert "re-run --phase tagfacets" in captured.err

    def test_coverage_line_confirms_full_coverage(self, tmp_path, capsys):
        """Positive direction: full coverage is stated explicitly — the
        operator no longer has to diff two report lines to know the
        artifact covers the board (and no stderr warning fires)."""
        out = tmp_path / "dump"
        _write_dump_files(out, req_ids=("JR1", "JR2"))
        board_dump._atomic_write_text(
            out.with_suffix(".facet_tags.jsonl"),
            "\n".join(json.dumps(t) for t in [
                {"reqId": "JR1", "workerSubType": "Intern (Fixed Term)"},
                {"reqId": "JR2", "workerSubType": "Regular Employee"},
                {"reqId": "JR1", "jobFamilyGroup": "Engineering"},
                {"reqId": "JR2", "jobFamilyGroup": "Engineering"},
            ]) + "\n")
        assert board_dump.phase_finish(_finish_args(), out) == 0
        assert ("facet tag coverage: 2/2 rows (0 untagged)"
                in out.with_suffix(".report.txt").read_text())
        assert "no facet tags" not in capsys.readouterr().err


# ── phase_list: facet census persistence + capped-total status (S8-E3) ────

def _cxs_post(rid: str, title: str = "Engineer") -> dict:
    """A raw CXS list-card (the shape _page returns in jobPostings)."""
    return {"title": f"{title} {rid}", "externalPath": f"/job/E_{rid}",
            "locationsText": "US, CA, Santa Clara",
            "postedOn": "Posted Today", "bulletFields": [rid]}


class TestPhaseListFacetCensus:
    def test_facets_json_persisted_boardwide_plus_filtered(self, tmp_path,
                                                           monkeypatch):
        discovery = {"total": 10, "jobPostings": [_cxs_post("JR1"),
                                                  _cxs_post("JR2")],
                     "facets": [
                         {"facetParameter": "locationHierarchy1",
                          "values": [
                              {"descriptor": "United States", "id": "USID",
                               "count": 2},
                              {"descriptor": "India", "id": "INID",
                               "count": 8}]},
                         {"facetParameter": "timeType", "values": [
                             {"descriptor": "Full time", "id": "TTFULL",
                              "count": 10}]},
                     ]}
        filtered = {"total": 2, "jobPostings": [_cxs_post("JR1"),
                                                  _cxs_post("JR2")],
                     "facets": [
                         {"facetParameter": "locationHierarchy1",
                          "values": [
                              {"descriptor": "United States", "id": "USID",
                               "count": 2}]},
                         {"facetParameter": "timeType", "values": [
                             {"descriptor": "Full time", "id": "TTFULL",
                              "count": 2}]},
                     ]}

        def fake_page(board, facets, offset, cfg):
            if offset == 0 and not facets:
                return discovery
            if offset == 0 and facets == {"locationHierarchy1": ["USID"]}:
                return filtered
            raise AssertionError(f"unexpected page call {facets}@{offset}")

        monkeypatch.setattr(board_dump.workday, "_page", fake_page)
        args = type("A", (), {"board": "nvidia|wd5|x",
                              "country": "United States",
                              "time_type": "", "sleep": 0})()
        out = tmp_path / "out"
        rc = board_dump.phase_list(args, out)
        assert rc == 0
        census = json.loads(out.with_suffix(".facets.json").read_text())
        assert census["scope"] == "board"
        assert census["appliedFacets"] == {
            "locationHierarchy1": "United States"}
        # board-wide counts survive even though the dump is US-filtered
        assert census["facets"]["locationHierarchy1"] == [
            {"descriptor": "United States", "id": "USID", "count": 2},
            {"descriptor": "India", "id": "INID", "count": 8}]
        # …and the within-filter census is kept alongside
        assert census["facets_filtered"]["timeType"] == [
            {"descriptor": "Full time", "id": "TTFULL", "count": 2}]
        status = json.loads(out.with_suffix(".list.status").read_text())
        assert status == {"rows": 2, "total": 2, "complete": True,
                          "pages": 1}


class TestPhaseListCappedTotal:
    def test_capped_board_partitions_and_records_status(self, tmp_path,
                                                        monkeypatch,
                                                        capsys):
        """Integration: a 2,000-capped board flows through the partition
        fallback — .list.status records total_capped/partitions, the list
        file carries the recovered union, facets.json is still written."""
        def fake_page(board, facets, offset, cfg):
            if offset == 0 and not facets:
                return {"total": 2000, "jobPostings": [_cxs_post("JR0")],
                        "facets": [
                            {"facetParameter": "locationHierarchy1",
                             "values": [
                                 {"descriptor": "United States",
                                  "id": "USID", "count": 5},
                                 {"descriptor": "India", "id": "INID",
                                  "count": 3}]}]}
            if facets == {"locationHierarchy1": ["USID"]}:
                return {"total": 5,
                        "jobPostings": [_cxs_post(f"JR{i}")
                                        for i in range(5)]}
            if facets == {"locationHierarchy1": ["INID"]}:
                return {"total": 3, "jobPostings": [
                    _cxs_post("JR3"), _cxs_post("JR5"), _cxs_post("JR6")]}
            raise AssertionError(f"unexpected page call {facets}@{offset}")

        monkeypatch.setattr(board_dump.workday, "_page", fake_page)
        args = type("A", (), {"board": "nvidia|wd5|x", "country": "",
                              "time_type": "", "sleep": 0})()
        out = tmp_path / "out"
        rc = board_dump.phase_list(args, out)
        assert rc == 0
        status = json.loads(out.with_suffix(".list.status").read_text())
        assert status["rows"] == 7             # 5 US + 3 India − 1 overlap
        assert status["total"] == 2000         # the capped server claim
        assert status["total_capped"] is True
        assert status["partition_facet"] == "locationHierarchy1"
        assert status["complete"] is True
        assert len(status["partitions"]) == 2
        list_rows = [json.loads(l) for l in
                     out.with_suffix(".list.jsonl").read_text(
                         encoding="utf-8").splitlines()]
        assert len(list_rows) == 7
        assert len({r["reqId"] for r in list_rows}) == 7
        # the loud warning + recovery note are surfaced by the phase
        err = capsys.readouterr().err
        assert "CAPS" in err
        census = json.loads(out.with_suffix(".facets.json").read_text())
        assert census["total"] == 2000


# ── phase: tagfacets (S8-D gaps #2/#3) ────────────────────────────────────

def _tag_args(**over):
    base = {"board": "nvidia|wd5|x", "country": "United States",
            "time_type": "Full time", "sleep": 0}
    base.update(over)
    return type("A", (), base)()


def _tag_facets_payload() -> list[dict]:
    return [
        {"facetParameter": "locationHierarchy1", "values": [
            {"descriptor": "United States", "id": "USID", "count": 3}]},
        {"facetParameter": "timeType", "values": [
            {"descriptor": "Full time", "id": "TTFULL", "count": 3}]},
        {"facetParameter": "workerSubType", "values": [
            {"descriptor": "Intern (Fixed Term)", "id": "WS1", "count": 2},
            {"descriptor": "Regular Employee", "id": "WS2", "count": 1}]},
        {"facetParameter": "jobFamilyGroup", "values": [
            {"descriptor": "Engineering", "id": "JF1", "count": 3}]},
    ]


class TestPhaseFacetTags:
    def _fake_page(self, calls):
        def fake_page(board, facets, offset, cfg):
            calls.append(dict(facets))
            if offset == 0 and not facets:
                return {"total": 3, "jobPostings": [_cxs_post("JRX")],
                        "facets": _tag_facets_payload()}
            sub = {"locationHierarchy1": ["USID"], "timeType": ["TTFULL"]}
            if facets == {**sub, "workerSubType": ["WS1"]}:
                return {"total": 2, "jobPostings": [_cxs_post("JR1"),
                                                    _cxs_post("JR2")]}
            if facets == {**sub, "workerSubType": ["WS2"]}:
                return {"total": 1, "jobPostings": [_cxs_post("JR3")]}
            if facets == {**sub, "jobFamilyGroup": ["JF1"]}:
                return {"total": 3, "jobPostings": [
                    _cxs_post("JR1"), _cxs_post("JR2"), _cxs_post("JR3")]}
            raise AssertionError(f"unexpected facets {facets}")
        return fake_page

    def _lines(self, out):
        return [json.loads(l) for l in
                out.with_suffix(".facet_tags.jsonl").read_text(
                    encoding="utf-8").splitlines()]

    def test_tags_written_with_completion_markers(self, tmp_path,
                                                  monkeypatch):
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row(req_id="JR1")) + "\n", encoding="utf-8")
        calls: list = []
        monkeypatch.setattr(board_dump.workday, "_page",
                            self._fake_page(calls))
        rc = board_dump.phase_facet_tags(_tag_args(), out)
        assert rc == 0
        lines = self._lines(out)
        row_lines = [l for l in lines if "reqId" in l]
        markers = [l for l in lines if "facetDone" in l]
        assert row_lines == [
            {"reqId": "JR1", "workerSubType": "Intern (Fixed Term)"},
            {"reqId": "JR2", "workerSubType": "Intern (Fixed Term)"},
            {"reqId": "JR3", "workerSubType": "Regular Employee"},
            {"reqId": "JR1", "jobFamilyGroup": "Engineering"},
            {"reqId": "JR2", "jobFamilyGroup": "Engineering"},
            {"reqId": "JR3", "jobFamilyGroup": "Engineering"},
        ]
        assert {(m["facetDone"], m["value"]) for m in markers} == {
            ("workerSubType", "Intern (Fixed Term)"),
            ("workerSubType", "Regular Employee"),
            ("jobFamilyGroup", "Engineering")}
        # every sub-list carried the dump's facets + the tag facet
        sub_calls = [c for c in calls if c]
        assert all(c["locationHierarchy1"] == ["USID"]
                   and c["timeType"] == ["TTFULL"] for c in sub_calls)

    def test_resume_after_board_growth_retags(self, tmp_path,
                                               monkeypatch):
        """The Sep-15 drift class, pinned as FIXED (S9-C3 P1): the board
        gains a row UNDER AN EXISTING facet value between runs. The
        markers were computed over the OLD population, so the board
        fingerprint changes, every marker is superseded and ALL values
        re-tag (row lines are idempotent last-wins at finish, so
        re-emission is safe)."""
        out = tmp_path / "dump"
        board_rows = [_list_row(req_id=rid) for rid in ("JR1", "JR2", "JR3")]
        out.with_suffix(".list.jsonl").write_text(
            "\n".join(json.dumps(r) for r in board_rows) + "\n",
            encoding="utf-8")
        # mutable server state: sub-lists keyed by (param, facet id)
        sub_lists = {
            ("workerSubType", "WS1"): [_cxs_post("JR1"), _cxs_post("JR2")],
            ("workerSubType", "WS2"): [_cxs_post("JR3")],
            ("jobFamilyGroup", "JF1"): [_cxs_post("JR1"), _cxs_post("JR2"),
                                        _cxs_post("JR3")],
        }
        calls: list = []

        def fake_page(board, facets, offset, cfg):
            calls.append(dict(facets))
            if offset == 0 and not facets:
                return {"total": 3, "jobPostings": [_cxs_post("JRX")],
                        "facets": _tag_facets_payload()}
            sub = {"locationHierarchy1": ["USID"], "timeType": ["TTFULL"]}
            for (param, vid), posts in sub_lists.items():
                if facets == {**sub, param: [vid]}:
                    return {"total": len(posts), "jobPostings": posts}
            raise AssertionError(f"unexpected facets {facets}")

        monkeypatch.setattr(board_dump.workday, "_page", fake_page)
        assert board_dump.phase_facet_tags(_tag_args(), out) == 0
        assert len([c for c in calls if c]) == 3          # WS1, WS2, JF1

        # board refresh: JR4 posted under the ALREADY-DONE WS1 + JF1
        # values — the population-blind marker would have skipped them
        board_rows.append(_list_row(req_id="JR4"))
        out.with_suffix(".list.jsonl").write_text(
            "\n".join(json.dumps(r) for r in board_rows) + "\n",
            encoding="utf-8")
        sub_lists[("workerSubType", "WS1")].append(_cxs_post("JR4"))
        sub_lists[("jobFamilyGroup", "JF1")].append(_cxs_post("JR4"))

        calls.clear()
        assert board_dump.phase_facet_tags(_tag_args(), out) == 0
        # population change ⇒ every value re-fetched and the NEW row
        # carries both tags
        assert len([c for c in calls if c]) == 3
        jr4 = [l for l in self._lines(out) if l.get("reqId") == "JR4"]
        assert jr4 == [{"reqId": "JR4",
                        "workerSubType": "Intern (Fixed Term)"},
                       {"reqId": "JR4", "jobFamilyGroup": "Engineering"}]
        # append-only supersede: both population records and the ORIGINAL
        # markers stay on disk — only markers after the LAST facetPop
        # record are live
        lines = self._lines(out)
        assert len([l for l in lines if "facetPop" in l]) == 2
        assert len([l for l in lines if "facetDone" in l]) == 6
        # the re-tagged population is now current: a third run skips
        calls.clear()
        assert board_dump.phase_facet_tags(_tag_args(), out) == 0
        assert [c for c in calls if c] == []
        assert len([l for l in self._lines(out)
                    if l.get("reqId") == "JR4"]) == 2     # +0 rows

    def test_resume_unchanged_board_skips_done_pairs(self, tmp_path,
                                                     monkeypatch):
        """Markers now carry the board population fingerprint (facetPop
        record): a re-run over an UNCHANGED list.jsonl trusts them and
        fetches ZERO sub-lists — the cheap crash-resume contract the
        population-blind marker was originally built for (S9-C3 P1)."""
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            "\n".join(json.dumps(_list_row(req_id=rid))
                      for rid in ("JR1", "JR2", "JR3")) + "\n",
            encoding="utf-8")
        calls: list = []
        monkeypatch.setattr(board_dump.workday, "_page",
                            self._fake_page(calls))
        assert board_dump.phase_facet_tags(_tag_args(), out) == 0
        assert len([c for c in calls if c]) == 3
        lines_after_run1 = self._lines(out)
        calls.clear()
        assert board_dump.phase_facet_tags(_tag_args(), out) == 0
        # only the census page-0 was fetched — zero sub-list requests
        assert [c for c in calls if c] == []
        assert self._lines(out) == lines_after_run1     # +0 lines

    def test_incomplete_sublist_withholds_marker(self, tmp_path,
                                                 monkeypatch):
        """B1 mid-list failure (S9-C3 P2): rows fetched before the break
        are kept (idempotent last-wins) but the facetDone marker is NOT
        written — the tail is re-fetched on the next run, and only then
        is the pair marked done."""
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            "\n".join(json.dumps(_list_row(req_id=rid))
                      for rid in ("JR1", "JR2", "JR3")) + "\n",
            encoding="utf-8")
        broken = {"yes": True}

        def fake_page(board, facets, offset, cfg):
            if offset == 0 and not facets:
                return {"total": 3, "jobPostings": [_cxs_post("JRX")],
                        "facets": _tag_facets_payload()}
            sub = {"locationHierarchy1": ["USID"], "timeType": ["TTFULL"]}
            if facets == {**sub, "workerSubType": ["WS1"]}:
                if offset == 0:
                    # 2 of 3 cards on page-0; page-1 dies (B1 break)
                    return {"total": 3, "jobPostings": [_cxs_post("JR1"),
                                                        _cxs_post("JR2")]}
                if broken["yes"]:
                    raise RuntimeError("network down mid-list")
                return {"total": 3, "jobPostings": [_cxs_post("JR3")]}
            if facets == {**sub, "workerSubType": ["WS2"]}:
                return {"total": 1, "jobPostings": [_cxs_post("JR3")]}
            if facets == {**sub, "jobFamilyGroup": ["JF1"]}:
                return {"total": 3, "jobPostings": [
                    _cxs_post("JR1"), _cxs_post("JR2"), _cxs_post("JR3")]}
            raise AssertionError(f"unexpected facets {facets}")

        monkeypatch.setattr(board_dump.workday, "_page", fake_page)
        assert board_dump.phase_facet_tags(_tag_args(), out) == 0
        lines = self._lines(out)
        # partial rows kept, but NO marker for the broken value
        assert {"reqId": "JR1",
                "workerSubType": "Intern (Fixed Term)"} in lines
        assert {"reqId": "JR2",
                "workerSubType": "Intern (Fixed Term)"} in lines
        assert not any(l.get("facetDone") == "workerSubType"
                       and l.get("value") == "Intern (Fixed Term)"
                       for l in lines)
        # the two healthy values earned theirs and the phase exits 0
        assert {(l["facetDone"], l["value"]) for l in lines
                if "facetDone" in l} == {
            ("workerSubType", "Regular Employee"),
            ("jobFamilyGroup", "Engineering")}
        # network healed: the tail is re-fetched and only THEN marked
        broken["yes"] = False
        assert board_dump.phase_facet_tags(_tag_args(), out) == 0
        lines = self._lines(out)
        assert {"reqId": "JR3",
                "workerSubType": "Intern (Fixed Term)"} in lines
        assert any(l.get("facetDone") == "workerSubType"
                   and l.get("value") == "Intern (Fixed Term)"
                   for l in lines)

    def test_legacy_markers_without_population_record_superseded(
            self, tmp_path, monkeypatch):
        """A marker file from the pre-fingerprint code (plain facetDone
        lines, no facetPop record — the a7c5aea-era artifact) is not
        trusted for ANY population: the first re-run supersedes it and
        re-tags everything (self-heal of the data-only Sep-15 "fix")."""
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            "\n".join(json.dumps(_list_row(req_id=rid))
                      for rid in ("JR1", "JR2", "JR3")) + "\n",
            encoding="utf-8")
        board_dump._atomic_write_text(
            out.with_suffix(".facet_tags.jsonl"),
            "\n".join(json.dumps(t) for t in [
                {"reqId": "JR1", "workerSubType": "Intern (Fixed Term)"},
                {"reqId": "JR2", "workerSubType": "Intern (Fixed Term)"},
                {"facetDone": "workerSubType",
                 "value": "Intern (Fixed Term)"},
                {"reqId": "JR3", "workerSubType": "Regular Employee"},
                {"facetDone": "workerSubType", "value": "Regular Employee"},
                {"reqId": "JR1", "jobFamilyGroup": "Engineering"},
                {"reqId": "JR2", "jobFamilyGroup": "Engineering"},
                {"reqId": "JR3", "jobFamilyGroup": "Engineering"},
                {"facetDone": "jobFamilyGroup", "value": "Engineering"},
            ]) + "\n")
        calls: list = []
        monkeypatch.setattr(board_dump.workday, "_page",
                            self._fake_page(calls))
        assert board_dump.phase_facet_tags(_tag_args(), out) == 0
        # every value re-fetched despite the file claiming all three done
        assert len([c for c in calls if c]) == 3
        lines = self._lines(out)
        assert any("facetPop" in l for l in lines)   # baseline established
        # the original markers stay on disk (append-only) but are dead:
        # 3 stale + 3 fresh
        assert len([l for l in lines if "facetDone" in l]) == 6

    def test_no_list_file_is_clean_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(board_dump.workday, "_page",
                            self._fake_page([]))
        rc = board_dump.phase_facet_tags(_tag_args(), tmp_path / "nope")
        assert rc == 2

    def test_unknown_country_is_clean_error(self, tmp_path, monkeypatch):
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row(req_id="JR1")) + "\n", encoding="utf-8")
        monkeypatch.setattr(board_dump.workday, "_page", self._fake_page([]))
        rc = board_dump.phase_facet_tags(_tag_args(country="Atlantis"), out)
        assert rc == 2

    def test_board_without_tag_facets_stays_silent(self, tmp_path,
                                                   monkeypatch):
        """No workerSubType/jobFamilyGroup on the board → no sub-lists, no
        file, rc 0 (finish leaves the columns empty)."""
        def fake_page(board, facets, offset, cfg):
            return {"total": 1, "jobPostings": [_cxs_post("JR1")],
                    "facets": [
                        {"facetParameter": "locationHierarchy1",
                         "values": [{"descriptor": "United States",
                                     "id": "USID", "count": 1}]}]}
        monkeypatch.setattr(board_dump.workday, "_page", fake_page)
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row(req_id="JR1")) + "\n", encoding="utf-8")
        rc = board_dump.phase_facet_tags(
            _tag_args(country="United States", time_type=""), out)
        assert rc == 0
        # no tag facets → no sub-lists; at most an empty file is created
        # (finish reads zero tags off it → empty columns either way)
        tag_path = out.with_suffix(".facet_tags.jsonl")
        assert not tag_path.exists() or tag_path.read_text(
            encoding="utf-8").strip() == ""

    def test_tagfacets_end_to_end_into_csv(self, tmp_path, monkeypatch):
        """list → tagfacets → finish (no corroborate): the tags land in the
        CSV columns — the optional-phase back-compat contract."""
        out = tmp_path / "dump"
        board_dump._atomic_write_text(
            out.with_suffix(".list.jsonl"),
            "\n".join(json.dumps(_list_row(req_id=rid))
                      for rid in ("JR1", "JR2")) + "\n")
        calls: list = []
        monkeypatch.setattr(board_dump.workday, "_page",
                            self._fake_page(calls))
        rc = board_dump.phase_facet_tags(_tag_args(), out)
        assert rc == 0
        _write_dump_files(out, req_ids=("JR1", "JR2"))
        rc = board_dump.phase_finish(_finish_args(), out)
        assert rc == 0
        with open(out.with_suffix(".csv"), newline="",
                  encoding="utf-8-sig") as f:
            csv_rows = {r["reqId"]: r for r in csv.DictReader(f)}
        assert csv_rows["JR1"]["workerSubType"] == "Intern (Fixed Term)"
        assert csv_rows["JR2"]["workerSubType"] == "Intern (Fixed Term)"
        assert csv_rows["JR1"]["jobFamilyGroup"] == "Engineering"
        assert csv_rows["JR2"]["jobFamilyGroup"] == "Engineering"


class TestS9AuditDetailsFixes(TestPhaseDetailsResume):
    """Pins for the S9-audit G5 fix wave — each was RED pre-fix."""

    def test_failed_refetch_does_not_shadow_good_record(self, tmp_path,
                                                        monkeypatch):
        """C1/A5 P1: a settled row whose --refetch-similar re-fetch FAILS
        must keep its good payload at finish (was: the error record
        last-wins-replaced it — detailError + blank columns + count 0
        until a retry, then 3-strike froze the downgrade)."""
        out = self._write_list(tmp_path, ["JR1"])
        # count-only era shape: similarJobsCount present, LIST absent
        good = {"reqId": "JR1", "info": _detail_info(),
                "hiringOrg": "2100 NVIDIA USA", "similarJobsCount": 2}
        out.with_suffix(".details.jsonl").write_text(
            json.dumps(good) + "\n" +
            json.dumps({"reqId": "JR1", "error": "detail_unreachable",
                        "attempts": 1}) + "\n", encoding="utf-8")
        args = _det_args(refetch_similar=True)
        # the refetch fails (network seam mocked to fail)
        monkeypatch.setattr(
            board_dump.workday, "detail_payload",
            lambda *a, **k: None)
        rc = board_dump.phase_details(args, out)
        assert rc == 1                     # error written → loud
        # finish sees the GOOD record, not the error
        good_map, attempts = board_dump._load_details_state(
            out.with_suffix(".details.jsonl"))
        assert good_map["JR1"]["info"]["title"] == "Engineer"
        assert attempts["JR1"] == 2      # 1 prior + this failed refetch
        # and the strike did NOT destroy the settled status
        rows = list(board_dump._load_jsonl(
            out.with_suffix(".details.jsonl")))
        assert any(r.get("info") for r in rows)

    def test_refetch_strike_cap_keeps_good_record(self, tmp_path):
        """Three failed refetches stop re-fetching but the good payload
        survives forever (the freeze is on the WORK, not the data)."""
        out = self._write_list(tmp_path, ["JR1"])
        lines = [json.dumps({"reqId": "JR1", "info": _detail_info(),
                             "hiringOrg": "X", "similarJobsCount": 1})]
        for i in range(1, 4):
            lines.append(json.dumps(
                {"reqId": "JR1", "error": "detail_unreachable",
                 "attempts": i}))
        out.with_suffix(".details.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        good, attempts = board_dump._load_details_state(
            out.with_suffix(".details.jsonl"))
        assert good["JR1"]["info"]["title"] == "Engineer"
        assert attempts["JR1"] == 3

    def test_detail_records_carry_fetched_at(self, tmp_path, monkeypatch):
        """C1 P2: detail records are timestamped (vintage auditing was
        git-only before)."""
        out = self._write_list(tmp_path, ["JR1"])
        rc = self._run(out, monkeypatch, fail=set())
        assert rc == 0
        rec = self._lines(out)[0]
        assert rec.get("fetched_at")
        assert "T" in rec["fetched_at"]      # ISO timestamp

    def test_repair_jsonl_tail_drops_partial_line(self, tmp_path):
        """B7 torn-write guard: a crash mid-append leaves a partial line;
        the repair truncates it BEFORE the next append welds onto it."""
        p = tmp_path / "x.jsonl"
        good_line = json.dumps({"reqId": "JR1", "info": {}}) + "\n"
        p.write_text(good_line + '{"reqId": "JR2", "inf', encoding="utf-8")
        board_dump._repair_jsonl_tail(p)
        assert p.read_text(encoding="utf-8") == good_line
        # clean tail → no-op
        board_dump._repair_jsonl_tail(p)
        assert p.read_text(encoding="utf-8") == good_line


class TestCsvV24Columns:
    """Pins for the S9-audit G6 wave (CSV v2.4): applicantCensored
    recomputed, firstSeenDate from watch state, lastResetDate from
    repost events, nLocations unknown on error rows, snapshot-frozen
    postingAgeDays."""

    def _derive(self, sig=None, det=None, r=None, first_seen="",
                last_reset="", snapshot="2026-09-16"):
        r = r or {"reqId": "JR1", "title": "Engineer",
                  "postedOn": "Posted Today", "url": "u",
                  "externalPath": "/job/JR1"}
        return board_dump._derive_csv_row(
            r, det or {}, sig, "NVIDIA", "2026-09-16",
            first_seen or "2026-09-16", snapshot_date=snapshot,
            last_reset=last_reset)

    def test_applicant_censored_recomputed(self):
        """#44 recomputes from (num, label) — not the stored flag (752
        pre-S8 signals lack the key; projecting it under-reports)."""
        bucket_floor = {"num_applicants": 25,
                        "applicants_label": "among first 25 applicants"}
        row = self._derive(sig={"status": "matched",
                                "match_method": "reqId", **bucket_floor})
        assert row["applicantCensored"] == "true"
        # same count, non-censoring label → still true (bucket boundary)
        row = self._derive(sig={"status": "matched",
                                "match_method": "reqId",
                                "num_applicants": 25,
                                "applicants_label": "25 applicants"})
        assert row["applicantCensored"] == "true"
        # label-named censoring at a NON-boundary count
        row = self._derive(sig={"status": "matched",
                                "match_method": "reqId",
                                "num_applicants": 500,
                                "applicants_label": "Over 500 applicants"})
        assert row["applicantCensored"] == "true"
        # exact mid-range count
        row = self._derive(sig={"status": "matched",
                                "match_method": "reqId",
                                "num_applicants": 37,
                                "applicants_label": "37 applicants"})
        assert row["applicantCensored"] == "false"
        # no signal → empty (unknown, not false)
        assert self._derive()["applicantCensored"] == ""

    def test_first_seen_and_last_reset_plumb_through(self):
        row = self._derive(first_seen="2026-09-09",
                           last_reset="2026-09-14")
        assert row["firstSeenDate"] == "2026-09-09"
        assert row["lastResetDate"] == "2026-09-14"

    def test_watch_maps_read_state_and_reposts(self, tmp_path):
        """finish's watch-state loaders: {reqId: first_seen} from
        state.jsonl, {reqId: new_startDate} from reposts.jsonl (last
        event wins); missing files → {}."""
        watch = tmp_path / "board_watch"
        watch.mkdir()
        out = tmp_path / "workday" / "dump"
        out.parent.mkdir(exist_ok=True)
        (watch / "dump.state.jsonl").write_text(
            json.dumps({"reqId": "JR1", "first_seen": "2026-09-09",
                        "posting": {}}) + "\n" +
            json.dumps({"reqId": "JR2", "first_seen": "2026-09-10",
                        "posting": {}}) + "\n", encoding="utf-8")
        (watch / "dump.reposts.jsonl").write_text(
            json.dumps({"reqId": "JR1", "new_startDate": "2026-09-13"}) +
            "\n" + json.dumps({"reqId": "JR1",
                                "new_startDate": "2026-09-14"}) + "\n",
            encoding="utf-8")
        fs = board_dump._watch_first_seen(out)
        assert fs == {"JR1": "2026-09-09", "JR2": "2026-09-10"}
        rs = board_dump._watch_repost_resets(out)
        assert rs == {"JR1": "2026-09-14"}     # last event wins
        # missing watch dir → empty maps
        out2 = tmp_path / "workday" / "other"
        assert board_dump._watch_first_seen(out2) == {}
        assert board_dump._watch_repost_resets(out2) == {}

    def test_nlocations_unknown_on_error_rows(self):
        row = self._derive(
            det={"reqId": "JR1", "error": "detail_unreachable",
                 "attempts": 1},
            r={"reqId": "JR1", "title": "Engineer",
               "postedOn": "Posted Today", "url": "u",
               "externalPath": "/j/JR1",
               "locationsText": "6 Locations"})
        assert row["nLocations"] == ""          # UNKNOWN, not 1
        assert "6 Locations" in row["locations"]

    def test_posting_age_frozen_to_snapshot(self):
        """B2r: regenerating a day later must not shift the column —
        the snapshot is the reference, not wall-clock today."""
        row = self._derive(
            det={"info": {"startDate": "2026-09-08"}})
        assert row["postingAgeDays"] == 8       # 2026-09-16 − 09-08


class TestNeverSettledErrorMarker(TestPhaseDetailsResume):
    """S9-audit H2 P1: never-settled rows (all records are errors) must
    keep their detailError marker at finish — the good-record view used
    to drop them from the payload view entirely (detailError="" +
    nLocations=1 lie)."""

    def test_never_settled_row_keeps_error_marker(self, tmp_path):
        out = self._write_list(tmp_path, ["JR1"])
        out.with_suffix(".details.jsonl").write_text(
            json.dumps({"reqId": "JR1", "error": "detail_unreachable",
                        "attempts": 3, "fetched_at": "2026-09-16T00:00:00+00:00"})
            + "\n", encoding="utf-8")
        view, attempts = board_dump._load_details_state(
            out.with_suffix(".details.jsonl"))
        assert view["JR1"]["error"] == "detail_unreachable"
        assert attempts["JR1"] == 3
        # settled derivation stays honest (an error record is NOT settled)
        settled = {rid for rid, d in view.items() if d.get("info")}
        assert "JR1" not in settled

    def test_good_record_beats_error_fallback(self, tmp_path):
        out = self._write_list(tmp_path, ["JR1"])
        out.with_suffix(".details.jsonl").write_text(
            json.dumps({"reqId": "JR1", "info": _detail_info(),
                        "hiringOrg": "X", "similarJobsCount": 1}) + "\n" +
            json.dumps({"reqId": "JR1", "error": "detail_unreachable",
                        "attempts": 1}) + "\n", encoding="utf-8")
        view, _ = board_dump._load_details_state(
            out.with_suffix(".details.jsonl"))
        assert view["JR1"].get("info")            # the GOOD record wins



class TestFinishSurvivesErrorRows:
    """S9-audit H6r P1: never-settled rows ship nLocations='' (honest
    unknown) — finish's loc_sum must not crash on the mixed types."""

    def test_finish_survives_never_settled_row(self, tmp_path, monkeypatch):
        out = tmp_path / "dump"
        rows = [_list_row(req_id="JR1", title="Engineer"),
                _list_row(req_id="JR2", title="Unmatched Role")]
        board_dump._atomic_write_text(
            out.with_suffix(".list.jsonl"),
            "\n".join(json.dumps(r) for r in rows) + "\n")
        # JR1: never settled (error record only) — the honest-unknown path
        board_dump._atomic_write_text(
            out.with_suffix(".details.jsonl"),
            json.dumps({"reqId": "JR1", "error": "detail_unreachable",
                        "attempts": 3,
                        "fetched_at": "2026-09-16T00:00:00+00:00"}) + "\n")
        board_dump._atomic_write_text(
            out.with_suffix(".signals.jsonl"),
            json.dumps({"job_req_id": "JR2", "status": "matched",
                        "num_applicants": 5, "applicants_label": "5 applicants",
                        "linkedin_posted_date": "2026-09-08",
                        "linkedin_url": "https://x/9", "title": "Unmatched Role",
                        "company": "NVIDIA", "linkedin_job_id": "9"}) + "\n")
        args = type("A", (), {"board": "nvidia|wd5|x", "company": "NVIDIA",
                              "country": "US", "time_type": "Full time",
                              "require_details": False})()
        rc = board_dump.phase_finish(args, out)
        assert rc == 0                       # was TypeError at loc_sum
        import csv as _csv
        with open(out.with_suffix(".csv"), encoding="utf-8-sig") as f:
            got = {r["reqId"]: r for r in _csv.DictReader(f)}
        assert got["JR1"]["detailError"] == "detail_unreachable"
        assert got["JR1"]["nLocations"] == ""
        assert (out.with_suffix(".report.txt")).exists()


# ── S13: detail-based country classification (phase countryfilter) ───────

class TestCountryFilterPhase:
    """Client-country boards (netflix/tencent/jd) list the FULL global
    board now; --phase countryfilter rewrites list.jsonl to the
    {country} population using the detail payload's authoritative
    jobPostingInfo.country. Foreign rows are archived (never silent);
    unresolved rows are KEPT (unknown ≠ foreign, B1 spirit)."""

    def _args(self):
        return type("A", (), {"board": "netflix|wd108|Netflix",
                              "country": "United States", "sleep": 0})()

    def _setup(self, out, rows, details, country_pending=True):
        board_dump._atomic_write_text(
            out.with_suffix(".list.jsonl"),
            "\n".join(json.dumps(r) for r in rows) + "\n")
        status = {"rows": len(rows), "total": len(rows), "complete": True,
                  "pages": 1, "country_client": True,
                  "country": "United States"}
        if country_pending:
            status["country_filter_pending"] = True
        board_dump._atomic_write_text(
            out.with_suffix(".list.status"), json.dumps(status))
        if details is not None:
            board_dump._atomic_write_text(
                out.with_suffix(".details.jsonl"),
                "\n".join(json.dumps(d) for d in details) + "\n")

    @staticmethod
    def _row(rid, loc):
        return {"reqId": rid, "title": f"T {rid}", "url": f"u/{rid}",
                "locationsText": loc, "externalPath": f"/job/{rid}",
                "company": "Netflix"}

    @staticmethod
    def _det(rid, country_descriptor):
        info = {"title": f"T {rid}",
                "country": {"descriptor": country_descriptor}}
        return {"reqId": rid, "info": info, "hiringOrg": "Netflix",
                "similarJobsCount": 0}

    def test_us_kept_foreign_dropped_unresolved_kept(self, tmp_path):
        out = tmp_path / "dump"
        self._setup(
            out,
            rows=[self._row("JR1", "Los Gatos"),     # city-only, US
                  self._row("JR2", "Los Gatos"),     # city-only, Canada
                  self._row("JR3", "2 Locations")],  # no detail yet
            details=[self._det("JR1", "United States of America"),
                     self._det("JR2", "Canada")])
        rc = board_dump.phase_countryfilter(self._args(), out)
        assert rc == 0
        kept = [json.loads(x) for x in
                out.with_suffix(".list.jsonl").read_text().splitlines()]
        assert {r["reqId"] for r in kept} == {"JR1", "JR3"}
        foreign = [json.loads(x) for x in
                   out.with_suffix(".list.foreign.jsonl").read_text()
                   .splitlines()]
        assert [r["reqId"] for r in foreign] == ["JR2"]
        assert foreign[0]["country"] == "canada"   # auditable
        status = json.loads(out.with_suffix(".list.status").read_text())
        assert status["rows"] == 2
        assert status["country_filter_pending"] is False
        cf = status["country_filtered"]
        assert cf["kept"] == 2 and cf["dropped"] == 1
        assert cf["unresolved"] == 1 and cf["basis"] == "detail"

    def test_idempotent_rerun(self, tmp_path):
        out = tmp_path / "dump"
        self._setup(out, rows=[self._row("JR1", "Los Gatos"),
                               self._row("JR2", "Seoul")],
                    details=[self._det("JR1", "United States of America"),
                             self._det("JR2", "South Korea")])
        assert board_dump.phase_countryfilter(self._args(), out) == 0
        first = out.with_suffix(".list.jsonl").read_text()
        assert board_dump.phase_countryfilter(self._args(), out) == 0
        assert out.with_suffix(".list.jsonl").read_text() == first
        # foreign archive: exactly one line per row (no dup on rerun)
        ftxt = out.with_suffix(".list.foreign.jsonl").read_text()
        assert len(ftxt.splitlines()) == 1

    def test_later_settled_detail_reclassifies(self, tmp_path):
        # a pending row's detail settles → a re-run classifies it
        out = tmp_path / "dump"
        self._setup(out, rows=[self._row("JR3", "2 Locations")],
                    details=[])
        assert board_dump.phase_countryfilter(self._args(), out) == 0
        status = json.loads(out.with_suffix(".list.status").read_text())
        assert status["country_filtered"]["unresolved"] == 1
        # detail arrives (retry succeeded)
        self._setup(out, rows=[self._row("JR3", "2 Locations")],
                    details=[self._det("JR3", "Canada")],
                    country_pending=False)
        # restore pending marker for the re-run scenario
        st = json.loads(out.with_suffix(".list.status").read_text())
        st["country_filter_pending"] = True
        board_dump._atomic_write_text(out.with_suffix(".list.status"),
                                      json.dumps(st))
        assert board_dump.phase_countryfilter(self._args(), out) == 0
        kept = board_dump._load_jsonl(out.with_suffix(".list.jsonl"))
        assert kept == []   # classified foreign → dropped

    def test_facet_board_noop(self, tmp_path):
        out = tmp_path / "dump"
        self._setup(out, rows=[self._row("JR1", "US, CA, X")],
                    details=None, country_pending=False)
        # nvidia-style: no country_client marker → no-op
        status = {"rows": 1, "total": 1, "complete": True, "pages": 1}
        board_dump._atomic_write_text(out.with_suffix(".list.status"),
                                      json.dumps(status))
        before = out.with_suffix(".list.jsonl").read_text()
        rc = board_dump.phase_countryfilter(self._args(), out)
        assert rc == 0
        assert out.with_suffix(".list.jsonl").read_text() == before

    def test_no_list_file_rc2(self, tmp_path):
        rc = board_dump.phase_countryfilter(
            self._args(), tmp_path / "missing")
        assert rc == 2

    def test_finish_refuses_while_filter_pending(self, tmp_path):
        """The hard gate: a listing that never ran countryfilter would
        ship the FULL GLOBAL board as the CSV — finish refuses."""
        out = tmp_path / "dump"
        rows = [self._row("JR1", "Los Gatos")]
        self._setup(out, rows=rows,
                    details=[self._det("JR1",
                                       "United States of America")])
        args = type("A", (), {
            "board": "netflix|wd108|Netflix", "label": "x",
            "company": "Netflix", "country": "United States",
            "out_dir": str(tmp_path), "sleep": 0,
            "li_variants": None, "slice_locations": None,
            "corroborate_index": False, "index_mode": None,
            "li_reindex": False, "reprobe_no_card_days": None,
            "require_details": False, "refetch_similar": False,
            "titlesearch_limit": 0, "titlesearch_search_locations": None,
            "questionnaire_dir": None, "h1b": None,
        })()
        rc = board_dump.phase_finish(args, out)
        assert rc == 2
