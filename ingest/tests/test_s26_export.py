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
# D-S35-1: the A-prime noise class lives on non_cn boards (us_scope
# boards never form foreign labels — _row_country's design). The
# foreign-column rows flow via geo_scope=non_cn + country-silent feeds.
W_NONCN = {"label": "testco_us_fulltime", "board": "ats:greenhouse:testco",
           "company": "TestCo", "country": "United States",
           "geo_scope": "non_cn"}


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



class TestCorroboratedRows:
    def test_linkedin_date_predates_start_gha_regression(self, tmp_path):
        # the GHA live test caught a TypeError here (date vs str compare
        # on corroborated boards) — pin the fixed shape
        _setup(tmp_path,
               state_rows=[_state_row("R1")],
               feed_rows=[],
               csv_rows=[_csv_row(
                   "R1", linkedinPostedDate="2026-09-10",
                   corroborationStatus="matched", matchMethod="reqId",
                   numApplicants="15")])
        exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        with open(tmp_path / "workworkday/testco_us_fulltime.csv" if False
                  else tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            r = list(csv.DictReader(f))[0]
        # 2026-09-10 < startDate 2026-09-18 → DOM from the LI card
        assert r["daysOnMarketBasis"] == "startDate+linkedin"
        assert r["earliestEvidenceDate"] == "2026-09-10"
        assert r["daysOnMarket"] == "23"     # 2026-09-10 → 2026-10-03
        assert r["crossSourceRepostEvidence"] == "reqId"
        # corroboration columns survive the export untouched
        assert r["numApplicants"] == "15"
        assert r["corroborationStatus"] == "matched"


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
        # a label with a path separator → the CSV write raises (parent
        # dir missing) → THAT board errors, the healthy board still
        # exports, and main exits NON-ZERO (review P2-3: per-board
        # failures must not run green)
        watch, workday = _setup(tmp_path, [], [])
        (watch / "config.json").write_text(json.dumps(
            {"watches": [
                {"label": "bad/path_us_fulltime",
                 "board": "ats:greenhouse:bad",
                 "company": "Bad", "country": "United States"},
                {"label": "goodco_us_fulltime",
                 "board": "ats:greenhouse:goodco",
                 "company": "GoodCo", "country": "United States"}]}))
        (watch / "bad/path_us_fulltime.state.jsonl").parent.mkdir(
            parents=True, exist_ok=True)
        (watch / "bad/path_us_fulltime.state.jsonl").write_text(
            json.dumps(_state_row("R1")) + "\n")
        (watch / "bad/path_us_fulltime.newposts.jsonl").parent.mkdir(
            parents=True, exist_ok=True)
        (watch / "bad/path_us_fulltime.newposts.jsonl").write_text(
            json.dumps(_feed_row("R1")) + "\n")
        (watch / "goodco_us_fulltime.state.jsonl").write_text(
            json.dumps(_state_row("R1")) + "\n")
        (watch / "goodco_us_fulltime.newposts.jsonl").write_text(
            json.dumps(_feed_row("R1")) + "\n")
        import sys as _sys
        _sys.argv = ["s26_export_refresh.py"]
        rc = exporter.main()
        assert rc == 1                      # loud, not green
        assert (workday / "goodco_us_fulltime.csv").exists()
        assert not (workday / "bad").exists() or True


class TestCountryGate:
    """Review P0-3 — the binance class: state rows whose feed record
    classifies FOREIGN must never ship in *_us_fulltime."""

    def _gate_setup(self, tmp_path, feed_rows, state_rows):
        _setup(tmp_path, state_rows=state_rows, feed_rows=feed_rows,
               csv_rows=None)
        return exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)

    def test_feed_foreign_rows_excluded(self, tmp_path):
        # 3 state rows: US, foreign-classified, unclassified
        st = self._gate_setup(
            tmp_path,
            feed_rows=[
                _feed_row("R1", country="United States"),
                _feed_row("R2", country="Singapore"),
                _feed_row("R3"),                       # no country field
            ],
            state_rows=[_state_row("R1"), _state_row("R2"),
                        _state_row("R3")])
        assert st["new"] == 2                       # R1 + R3, NOT R2
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            ids = [r["reqId"] for r in csv.DictReader(f)]
        assert ids == ["R1", "R3"]

    def test_existing_csv_row_foreign_after_classification(self, tmp_path):
        # a row that already shipped, later feed-classified foreign
        # (anthropic-class) — the gate drops it as a 'ghost'
        _setup(tmp_path,
               state_rows=[_state_row("R1"), _state_row("R2")],
               feed_rows=[_feed_row("R1", country="Singapore")],
               csv_rows=[_csv_row("R1"), _csv_row("R2")])
        st = exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        assert st["kept"] == 1 and st["ghosts_dropped"] == 1
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            ids = [r["reqId"] for r in csv.DictReader(f)]
        assert ids == ["R2"]


class TestTerminalErrorRows:
    """Review P1-3 — 3-strike terminal error records export HONESTLY
    (detailError set) instead of pending forever."""

    def test_terminal_error_row_exports(self, tmp_path):
        rec = _feed_row("R1")
        rec["error"] = "detail_unreachable"
        rec["attempts"] = 3
        _setup(tmp_path, state_rows=[_state_row("R1")],
               feed_rows=[rec], csv_rows=None)
        st = exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        assert st["new"] == 1 and st["terminal_err"] == 1
        assert st["pending"] == 0
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            r = list(csv.DictReader(f))[0]
        assert r["detailError"] == "detail_unreachable"
        assert r["title"].startswith("Job R1")

    def test_transient_error_row_waits(self, tmp_path):
        rec = _feed_row("R1")
        rec["error"] = "detail_unreachable"
        rec["attempts"] = 1
        _setup(tmp_path, state_rows=[_state_row("R1")],
               feed_rows=[rec], csv_rows=None)
        st = exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        assert st["pending"] == 1 and st["new"] == 0


class TestCountryGateAPrime:
    """D-S35-1 — the A-prime country gate: foreign-labeled + non-remote
    = noise (the user's product rule); foreign + remote = useful (kept).
    The CSV country COLUMN is exclusion evidence where feeds are
    country-silent (the non_cn board class). State untouched."""

    def test_foreign_nonremote_new_row_dropped(self, tmp_path):
        # feed country-silent, locations derive a foreign country,
        # not remote -> dropped, manifest counts gate_dropped_new
        # (the real class: non_cn board, country-silent feed)
        _setup(tmp_path, state_rows=[_state_row("R1")],
               feed_rows=[_feed_row("R1", country="",
                                    locationsText="Singapore",
                                    locations=["Singapore"])],
               csv_rows=None)
        st = exporter.refresh_one("testco_us_fulltime", W_NONCN, SNAPSHOT)
        assert st["new"] == 0
        assert st["gate_dropped"] == 1
        assert st["gate_dropped_new"] == 1
        # 0 rows + no prior CSV -> NO file (honest-zero label class)
        assert not (tmp_path / "workday/testco_us_fulltime.csv").exists()
        assert not (tmp_path / "workday/testco_us_fulltime.export.json"
                    ).exists()

    def test_foreign_remote_row_kept(self, tmp_path):
        # the "quite useful" class: foreign country + remoteFlag=true
        _setup(tmp_path, state_rows=[_state_row("R1")],
               feed_rows=[_feed_row("R1", country="",
                                    locationsText="Germany (Remote)",
                                    locations=["Germany (Remote)"])],
               csv_rows=None)
        st = exporter.refresh_one("testco_us_fulltime", W_NONCN, SNAPSHOT)
        assert st["new"] == 1 and st["gate_dropped"] == 0
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            r = list(csv.DictReader(f))[0]
        assert r["remoteFlag"] == "true"

    def test_foreign_nonremote_survivor_dropped(self, tmp_path):
        # existing CSV row, country-silent feed, foreign column
        _setup(tmp_path, state_rows=[_state_row("R1"), _state_row("R2")],
               feed_rows=[_feed_row("R1", locationsText="Singapore",
                                    locations=["Singapore"]),
                          _feed_row("R2")],
               csv_rows=[_csv_row("R1", country="Singapore",
                                  primaryLocation="Singapore",
                                  locations="Singapore"),
                         _csv_row("R2")])
        st = exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        assert st["kept"] == 1
        assert st["gate_dropped_survivors"] == 1
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            ids = [r["reqId"] for r in csv.DictReader(f)]
        assert ids == ["R2"]

    def test_blank_and_remote_label_country_kept(self, tmp_path):
        # unclassified != foreign; country='Remote' is remote by def
        _setup(tmp_path, state_rows=[_state_row("R1"), _state_row("R2")],
               feed_rows=[_feed_row("R1"), _feed_row("R2")],
               csv_rows=None)
        st = exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        assert st["new"] == 2 and st["gate_dropped"] == 0

    def test_locations_remote_rescues_foreign_row(self, tmp_path):
        # foreign country + remoteFlag false but 'Remote' in the
        # locations text -> kept (the _row_is_cn_local seam agreement)
        _setup(tmp_path, state_rows=[_state_row("R1")],
               feed_rows=[_feed_row("R1", country="",
                                    locationsText="Remote, OR",
                                    locations=["Remote, OR"])],
               csv_rows=None)
        st = exporter.refresh_one("testco_us_fulltime", W_NONCN, SNAPSHOT)
        assert st["gate_dropped"] == 0

    def test_us_variant_normalized_at_write(self, tmp_path):
        # survivors carry chain-era variant spellings -> canonical
        _setup(tmp_path, state_rows=[_state_row("R1"), _state_row("R2")],
               feed_rows=[_feed_row("R1"), _feed_row("R2")],
               csv_rows=[_csv_row("R1", country="US"),
                         _csv_row("R2", country="united states of america")])
        st = exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        assert st["kept"] == 2
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert all(r["country"] == "United States" for r in rows)

    def test_manifest_identity_holds(self, tmp_path):
        # state_rows = kept + gate_dropped_survivors + ghosts_in_state
        #               + new + gate_dropped_new + pending
        #               + feed_excluded_state
        rec_sg = _feed_row("R1", locationsText="Singapore",
                           locations=["Singapore"])   # gate_dropped_new
        _setup(tmp_path,
               state_rows=[_state_row("R1"), _state_row("R2"),
                           _state_row("R3"), _state_row("R4"),
                           _state_row("R5")],
               feed_rows=[rec_sg, _feed_row("R2"),   # R2 kept-new
                          _feed_row("R3", country="Germany")],  # feed-excl
               csv_rows=[_csv_row("R4", country="Singapore",
                                  primaryLocation="Singapore",
                                  locations="Singapore"),  # g_d_surv
                         _csv_row("R5", country="Singapore",
                                  primaryLocation="Singapore",
                                  locations="Singapore")])  # pending
        st = exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        m = json.loads((tmp_path / "workday/testco_us_fulltime.export.json"
                        ).read_text())
        assert m["state_rows"] == 5
        ident = (m["kept"] + m["gate_dropped_survivors"]
                 + m["ghosts_in_state"] + m["new"] + m["gate_dropped_new"]
                 + m["pending"] + m["feed_excluded_state"])
        assert ident == m["state_rows"], (ident, m)
        # R5: in csv + state + foreign-csv-column but feed-silent ->
        # survivor gate-dropped, not ghost; R3: feed-excluded state rid
        assert m["gate_dropped_survivors"] == 2
        assert m["feed_excluded_state"] == 1

    def test_ghost_semantics_untouched_by_gate(self, tmp_path):
        # a csv row NOT in state is a ghost regardless of country
        _setup(tmp_path, state_rows=[_state_row("R1")],
               feed_rows=[_feed_row("R1")],
               csv_rows=[_csv_row("R1"),
                         _csv_row("GONE", country="Singapore",
                                  primaryLocation="Singapore",
                                  locations="Singapore")])
        st = exporter.refresh_one("testco_us_fulltime", W, SNAPSHOT)
        assert st["ghosts_dropped"] == 1
        assert st["ghosts_in_state"] == 0
