"""Tests for scripts/s26_export_refresh.py — the daily state-merge CSV
export (D-S26-2).

Pins the contract the S26 e2e audit demanded:
- membership: CSV := state reqIds (ghosts dropped, new rows from the
  newposts feed, pending-enrichment rows NOT exported)
- date re-derivation against the snapshot (postingAgeDays /
  daysOnMarket / censored / dumpDate / daysLeftToApply floor rule)
- re-banding from the local LCA extract (level-stripped pools, D-S26-3)
- the feishu class: a board with NO prior CSV gets one built from
  newposts ∩ state
- idempotency: a second pass is row-stable
- U+2028 safety in the JSONL reader (the container gotcha)
- one-board failure never kills the batch (fail-loudly per board)
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "s26_export_refresh.py"

_spec = importlib.util.spec_from_file_location("s26_export_refresh", SCRIPT)
exporter = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("s26_export_refresh", exporter)
_spec.loader.exec_module(exporter)

FIELDS = exporter.FIELDS
SNAPSHOT = date(2026, 10, 3)


def _state_row(rid: str, first: str = "2026-09-20") -> dict:
    return {"reqId": rid, "title": f"Job {rid}", "first_seen": first,
            "last_seen": "2026-10-03", "last_postedOn": "",
            "last_startDate": "2026-09-18"}


def _feed_row(rid: str, **over) -> dict:
    rec = {
        "reqId": rid, "title": f"Job {rid}", "company": "TestCo",
        "first_seen": "2026-09-20", "locationsText": "US, CA, Santa Clara",
        "postedOn": "Posted 3 Days Ago", "url": f"https://x/{rid}",
        "locations": ["US, CA, Santa Clara"], "startDate": "2026-09-28",
        "description": "Do things. " * 20,
        "timeType": "Full time", "hiringOrg": "TestCo US",
        "externalUrl": f"https://x/{rid}", "country": "United States",
    }
    rec.update(over)
    return rec


def _csv_row(rid: str, **over) -> dict:
    row = {k: "" for k in FIELDS}
    row.update({
        "reqId": rid, "title": f"Job {rid}", "company": "TestCo",
        "timeType": "Full time", "startDate": "2026-09-18",
        "primaryLocation": "US, CA, Santa Clara", "nLocations": "1",
        "locations": "US, CA, Santa Clara", "remoteFlag": "false",
        "stateCodes": "CA", "country": "United States",
        "similarJobsCount": "0", "description": "Old desc",
        "descriptionLength": "8", "url": f"https://x/{rid}",
        "postingAgeDays": "15", "dumpDate": "2026-09-18",
        "firstSeenDate": "2026-09-10", "censored": "false",
    })
    row.update(over)
    return row


def _write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _setup(tmp_path: Path, state_rows, feed_rows, csv_rows=None,
           lca_recs=None, reposts=None):
    """Materialize a watch dir + workday dir under tmp_path and point the
    exporter module at them (monkeypatches module globals)."""
    watch = tmp_path / "board_watch"
    workday = tmp_path / "workday"
    watch.mkdir(); workday.mkdir()
    (watch / "config.json").write_text(json.dumps(
        {"watches": [{"label": "testco_us_fulltime",
                      "board": "ats:greenhouse:testco",
                      "company": "TestCo", "country": "United States"}]}))
    (watch / "testco_us_fulltime.state.jsonl").write_text(
        "\n".join(json.dumps(r) for r in state_rows) + "\n")
    (watch / "testco_us_fulltime.newposts.jsonl").write_text(
        "\n".join(json.dumps(r) for r in feed_rows) + "\n")
    if csv_rows is not None:
        _write_csv(workday / "testco_us_fulltime.csv", csv_rows)
    if lca_recs is not None:
        (workday / "testco_us_fulltime.h1b_lca.jsonl").write_text(
            "\n".join(json.dumps(r) for r in lca_recs) + "\n")
    if reposts:
        (watch / "testco_us_fulltime.reposts.jsonl").write_text(
            "\n".join(json.dumps(r) for r in reposts) + "\n")
    exporter.WATCH_DIR = watch
    exporter.WORKDAY = workday
    return watch, workday


def _lca(title: str, wage: float, st: str = "CA", case: str = "C1") -> dict:
    return {"jobTitle": title, "caseStatus": "Certified",
            "fullTimePosition": "Y", "wageFrom": f"{wage:,.0f}",
            "wageUnit": "Year", "wageTo": f"{wage:,.0f}",
            "worksiteState": st, "employerName": "TESTCO",
            "caseNumber": case, "caseSubmitted": "2026-01-01",
            "decisionDate": "2026-01-01", "jobLocation": "X",
            "socCode": "Y", "socTitle": "Z", "sourceFile": "q.xlsx"}


W = {"label": "testco_us_fulltime", "board": "ats:greenhouse:testco",
     "company": "TestCo", "country": "United States"}


class TestMembership:
    def test_ghosts_dropped_new_exported_pending_waits(self, tmp_path):
        _setup(tmp_path,
               state_rows=[_state_row("R1"), _state_row("R2"),
                           _state_row("R3")],
               feed_rows=[_feed_row("R2", startDate="2026-09-28")],
               csv_rows=[_csv_row("R1"), _csv_row("RGONE")])
        st = exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        assert st["kept"] == 1 and st["new"] == 1
        assert st["ghosts_dropped"] == 1 and st["pending"] == 1
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert [r["reqId"] for r in rows] == ["R1", "R2"]
        assert list(rows[0].keys()) == FIELDS      # the 49-col contract

    def test_feishu_class_builds_csv_from_newposts(self, tmp_path):
        _setup(tmp_path,
               state_rows=[_state_row("R1"), _state_row("R2")],
               feed_rows=[_feed_row("R1"), _feed_row("R2")],
               csv_rows=None)
        st = exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        assert st["new"] == 2 and st["rows"] == 2
        p = tmp_path / "workday/testco_us_fulltime.csv"
        assert p.exists()
        with open(p, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["description"].startswith("Do things.")
        assert rows[0]["hiringOrg"] == "TestCo US"
        assert rows[0]["primaryLocation"] == "US, CA, Santa Clara"
        assert rows[0]["stateCodes"] == "CA"
        assert rows[0]["remoteFlag"] == "false"
        assert rows[0]["firstSeenDate"] == "2026-09-20"
        assert rows[0]["dumpDate"] == "2026-10-03"
        assert rows[0]["timeType"] == "Full time"

    def test_idempotent_second_pass(self, tmp_path):
        _setup(tmp_path,
               state_rows=[_state_row("R1")],
               feed_rows=[_feed_row("R1")],
               csv_rows=None)
        exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            first = list(csv.DictReader(f))
        st = exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        assert st["new"] == 0 and st["kept"] == 1
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            second = list(csv.DictReader(f))
        assert first == second


class TestDateDerivation:
    def test_recompute_against_snapshot(self, tmp_path):
        _setup(tmp_path,
               state_rows=[_state_row("R1")],
               feed_rows=[],
               csv_rows=[_csv_row("R1", firstSeenDate="2026-09-05")])
        exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            r = list(csv.DictReader(f))[0]
        # startDate 2026-09-18, snapshot 10-03 → 15 days
        assert r["postingAgeDays"] == "15"
        assert r["daysOnMarket"] == "15"
        assert r["daysOnMarketBasis"] == "startDate"
        assert r["dumpDate"] == "2026-10-03"
        # firstSeenDate 2026-09-05 < startDate 2026-09-18 → censored
        assert r["censored"] == "true"

    def test_days_left_floor_rule(self, tmp_path):
        _setup(tmp_path,
               state_rows=[_state_row("R1")],
               feed_rows=[],
               csv_rows=[_csv_row(
                   "R1", applicationDeadline="2026-09-01")])
        exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            r = list(csv.DictReader(f))[0]
        # elapsed floor on an open posting = UNKNOWN (""), never negative
        assert r["daysLeftToApply"] == ""

    def test_n_locations_unknown_class(self, tmp_path):
        # the terse workday "6 Locations" dialect: count is UNKNOWN
        _setup(tmp_path,
               state_rows=[_state_row("R1")],
               feed_rows=[_feed_row("R1", locations=[],
                                    locationsText="6 Locations")],
               csv_rows=None)
        exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            r = list(csv.DictReader(f))[0]
        assert r["nLocations"] == "" and r["locations"] == "6 Locations"


class TestBanding:
    def test_level_stripped_pool_bands(self, tmp_path):
        _setup(tmp_path,
               state_rows=[_state_row("R1")],
               feed_rows=[],
               csv_rows=[_csv_row("R1", title="Senior Software Engineer")],
               lca_recs=[_lca("Software Engineer II", 200000,
                              case="C1"),
                         _lca("Software Engineer III", 220000,
                              case="C2"),
                         _lca("Software Engineer", 240000, case="C3")])
        exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            r = list(csv.DictReader(f))[0]
        # leveled filings pool to {software, engineer} (D-S26-3);
        # posting carries 'senior' → subset tier, level-mixed band
        assert r["h1bFilings"] == "3"
        assert r["h1bMatchBasis"] == "subset+state"
        assert r["h1bWageP50"] == "220000"

    def test_no_lca_file_leaves_columns_empty(self, tmp_path):
        _setup(tmp_path,
               state_rows=[_state_row("R1")],
               feed_rows=[],
               csv_rows=[_csv_row("R1")])
        st = exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        assert st["banded"] == 0 and st["lca_pools"] == 0
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            r = list(csv.DictReader(f))[0]
        assert r["h1bFilings"] == ""


class TestReposts:
    def test_last_reset_date_joined(self, tmp_path):
        _setup(tmp_path,
               state_rows=[_state_row("R1")],
               feed_rows=[],
               csv_rows=[_csv_row("R1")],
               reposts=[{"reqId": "R1", "new_startDate": "2026-09-25",
                         "run_date": "2026-09-26"},
                        {"reqId": "R1", "new_startDate": "2026-09-28",
                         "run_date": "2026-10-01"}])
        exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            r = list(csv.DictReader(f))[0]
        assert r["lastResetDate"] == "2026-09-28"   # last event wins


class TestJsonlSafety:
    def test_u2028_rows_survive(self, tmp_path):
        # a description containing U+2028 must not break the reader
        # (str.splitlines() would split mid-JSON — split ONLY on \n)
        rec = _feed_row("R1")
        rec["description"] = "line one\u2028line two"
        watch = tmp_path / "board_watch"
        workday = tmp_path / "workday"
        watch.mkdir(); workday.mkdir()
        (watch / "config.json").write_text("{}")
        (watch / "testco_us_fulltime.state.jsonl").write_text(
            json.dumps(_state_row("R1")) + "\n")
        (watch / "testco_us_fulltime.newposts.jsonl").write_text(
            json.dumps(rec) + "\n")
        exporter.WATCH_DIR = watch
        exporter.WORKDAY = workday
        st = exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        assert st["new"] == 1
        with open(workday / "testco_us_fulltime.csv", newline="",
                  encoding="utf-8-sig") as f:
            r = list(csv.DictReader(f))[0]
        assert "line two" in r["description"]


class TestFailureIsolation:
    def test_one_board_error_never_kills_batch(self, tmp_path, capsys):
        watch, workday = _setup(tmp_path, [], [])
        # a corrupt state file (not JSON) → that board errors, main goes on
        (watch / "testco_us_fulltime.state.jsonl").write_text(
            "this is not json\n")
        (watch / "config.json").write_text(json.dumps(
            {"watches": [W, {"label": "goodco_us_fulltime",
                             "board": "ats:greenhouse:goodco",
                             "company": "GoodCo",
                             "country": "United States"}]}))
        (watch / "goodco_us_fulltime.state.jsonl").write_text(
            json.dumps(_state_row("R1")) + "\n")
        (watch / "goodco_us_fulltime.newposts.jsonl").write_text(
            json.dumps(_feed_row("R1")) + "\n")
        rc = exporter.main.__wrapped__ if hasattr(
            exporter.main, "__wrapped__") else None
        # main() reads argparse argv — drive it directly
        import sys as _sys
        _sys.argv = ["s26_export_refresh.py"]
        rc = exporter.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "ERROR" in capsys.readouterr().err or "goodco" in out
        assert (workday / "goodco_us_fulltime.csv").exists()
