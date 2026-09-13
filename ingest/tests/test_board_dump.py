"""Tests for scripts/board_dump.py — the generic board-dump v2 flow.

Covers the CSV v2 contract (design-board-v2.md D1 + review addenda):
- column set exactly matches CSV_COLUMNS (30 cols)
- location enumeration (0A): never "N Locations", primary + additional
  merged, nLocations correct, stateCodes derived, remoteFlag
- description rendering (0B): clean text, descriptionLength
- metadata (0C): postingAgeDays, reqYear, hiringOrg, questionnaire
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


def _detail_info(startDate=None, addl=None, desc="<p><b>What you'll do:</b></p><ul><li>Build GPU infrastructure.</li></ul>"):
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
        "questionnaireId": "abc123",
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
        assert len(board_dump.CSV_COLUMNS) == 41   # v2.1: +10 (S8-E3)

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
        assert row["remoteFlag"] is True
        assert self._row()["remoteFlag"] is False

    def test_description_clean_text(self):
        row = self._row()
        assert "What you'll do:" in row["description"]
        assert "• Build GPU infrastructure." in row["description"]
        assert "<" not in row["description"]
        assert row["descriptionLength"] == len(row["description"])

    def test_metadata_fields(self):
        row = self._row()
        assert row["postingAgeDays"] == 1          # today − yesterday (dynamic)
        assert row["reqYear"] == "2026"
        assert row["hiringOrg"] == "2100 NVIDIA USA"
        assert row["questionnaire"] is True
        assert row["similarJobsCount"] == 5
        assert row["country"] == "United States of America"

    def test_req_year_from_old_prefix(self):
        r = _list_row(req_id="JR2022034")
        row = board_dump._derive_csv_row(
            r, {"info": _detail_info(), "hiringOrg": "x",
                "similarJobsCount": 0}, None, "NVIDIA", "2026-09-09",
            "2026-09-09")
        assert row["reqYear"] == "2022"

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
             "linkedin_url": "https://x/2", "title": "Beta Engineer - Core",
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
            "detail_sleep": 0.0}
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
        assert rc == 0
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
            "signals_batch": 40, "provider": "linkedin"}
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


class TestPhaseCorroboratePartitionedIndex:
    """S8-E1 (research §c/§f R1): --index-mode partitioned routes the
    index refresh through provider.index_cards_partitioned (slice
    matrix); 'single' stays the default for back-compat."""

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
            today.isoformat(), today.isoformat())

    def test_first_raw_record_derives_cleanly(self):
        r, det = _load_raw_pair()
        info = det["info"]
        for k in self.INFO_KEYS_READ:
            assert k in info, (
                f"RAW jobPostingInfo missing {k!r} — the fixture family "
                f"has drifted from reality (audit A8 class)")
        row = self._derive(r, det)
        # full column contract off REAL data (extras silently dropped
        # by DictWriter otherwise)
        assert set(row) == set(board_dump.CSV_COLUMNS)
        # startDate passthrough + postingAgeDays = today − startDate
        assert row["startDate"] == info["startDate"]
        assert row["postingAgeDays"] == (
            _dt.date.today()
            - _dt.date.fromisoformat(info["startDate"])).days
        # description = html_to_text of the REAL jobDescription; exact
        assert row["description"] == \
            board_dump.html_to_text(info["jobDescription"])
        assert row["descriptionLength"] == len(row["description"])
        assert "<" not in row["description"]
        # questionnaire == bool(questionnaireId) (never a count/string)
        assert row["questionnaire"] is bool(info.get("questionnaireId"))
        # locations enumerated from info: [location] + additionalLocations
        addl = info.get("additionalLocations") or []
        expected = [info["location"]] + list(addl)
        assert row["locations"] == "; ".join(expected)
        assert row["nLocations"] == len(expected)
        assert row["primaryLocation"] == info["location"]
        assert row["stateCodes"] == \
            board_dump._state_codes(expected)
        assert row["remoteFlag"] == any(
            "remote" in loc.lower() for loc in expected)
        # metadata passthroughs from RAW
        assert row["timeType"] == info["timeType"]
        assert row["country"] == info["country"]["descriptor"]
        assert row["title"] == info["title"]
        assert row["hiringOrg"] == det.get("hiringOrg")
        assert row["similarJobsCount"] == det.get("similarJobsCount") or 0
        assert row["url"] == r["url"]
        assert row["reqYear"] == re.match(
            r"[A-Za-z]+(\d{4})", r["reqId"]).group(1)
        assert row["detailError"] == ""    # info present → no error col

    def test_raw_multi_location_record(self):
        """0A against reality: a record that ACTUALLY has
        additionalLocations (the fixture family never models one)."""
        r, det = _load_raw_pair(require_addl=True)
        info = det["info"]
        row = self._derive(r, det)
        expected = [info["location"]] + list(info["additionalLocations"])
        assert len(expected) >= 2
        assert row["locations"] == "; ".join(expected)
        assert row["nLocations"] == len(expected)
        assert "Locations" not in row["locations"][:12]  # never trunc.
        assert row["remoteFlag"] == any(
            "remote" in loc.lower() for loc in expected)
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
        # postingAgeDays stays the CURRENT-epoch age (same computation
        # while the only evidence is startDate — semantics documented)
        assert row["postingAgeDays"] == row["daysOnMarket"]

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

    def test_resume_skips_done_pairs(self, tmp_path, monkeypatch):
        """A crash mid-value leaves rows without a marker — the pair is
        RE-RUN (harmless duplicate rows, last-wins at join); completed
        pairs (marker present) are never re-fetched."""
        out = tmp_path / "dump"
        out.with_suffix(".list.jsonl").write_text(
            json.dumps(_list_row(req_id="JR1")) + "\n", encoding="utf-8")
        # prior run completed the WS1 pair (rows + marker)
        board_dump._atomic_write_text(
            out.with_suffix(".facet_tags.jsonl"),
            json.dumps({"reqId": "JR1", "workerSubType":
                        "Intern (Fixed Term)"}) + "\n" +
            json.dumps({"reqId": "JR2", "workerSubType":
                        "Intern (Fixed Term)"}) + "\n" +
            json.dumps({"facetDone": "workerSubType",
                        "value": "Intern (Fixed Term)"}) + "\n")
        calls: list = []
        monkeypatch.setattr(board_dump.workday, "_page",
                            self._fake_page(calls))
        rc = board_dump.phase_facet_tags(_tag_args(), out)
        assert rc == 0
        # only WS2 + JF1 sub-lists were fetched this run
        sub_facets = [c for c in calls if c]
        assert [c.get("workerSubType") or c.get("jobFamilyGroup")
                for c in sub_facets] == [["WS2"], ["JF1"]]
        lines = self._lines(out)
        # the WS1 rows are NOT duplicated (pair skipped wholesale)
        ws1_rows = [l for l in lines
                    if l.get("workerSubType") == "Intern (Fixed Term)"]
        assert len(ws1_rows) == 2

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
