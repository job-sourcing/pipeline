"""S13 custom job-site adapter pins — the generalized non-Workday infra.

Contract under test: site_boards maps public one-call board APIs
(greenhouse/ashby) onto the workday list-row + detail-payload shapes so
the WHOLE dump/watch phase chain runs unchanged. All network is
monkeypatched (fetch_json) — these pins hold the MAPPING, the country
classification, the label computation, and the dispatch routing."""
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobsearch.config import Config  # noqa: E402
from jobsearch.sources import site_boards  # noqa: E402
from jobsearch.sources import workday  # noqa: E402


# ── fixtures: API payload shapes (live-pinned 2026-09-19) ─────────────────

def _gh_job(jid, req_id, title, offices, departments=None, published=None,
            content="<p>desc</p>"):
    return {
        "id": jid, "title": title, "requisition_id": req_id,
        "absolute_url": f"https://job-boards.greenhouse.io/x/jobs/{jid}",
        "location": {"name": " | ".join(o["name"] for o in offices)},
        "offices": offices,
        "departments": [{"id": i, "name": n}
                        for i, n in enumerate(departments or [])],
        "content": content, "first_published": published,
        "company_name": "Acme Corp", "metadata": None,
    }


def _ashby_job(jid, title, country="United States", loc="San Francisco",
               department="Engineering", employment="FullTime",
               published=None, listed=True):
    return {
        "id": jid, "title": title, "location": loc,
        "secondaryLocations": [], "department": department,
        "employmentType": employment, "isListed": listed,
        "jobUrl": f"https://jobs.ashbyhq.com/x/{jid}",
        "descriptionHtml": "<p>desc</p>", "publishedAt": published,
        "address": {"postalAddress": {
            "addressCountry": country, "addressRegion": "CA",
            "addressLocality": "SF"}},
        "workplaceType": "remote",
    }


def _patch_fetch(monkeypatch, jobs):
    calls = []

    def fake(url, *, params=None, cfg=None, **kw):
        calls.append(url)
        return {"jobs": jobs}
    monkeypatch.setattr(site_boards, "fetch_json", fake)
    monkeypatch.setattr(site_boards, "_CACHE", {})   # no cross-test leak
    return calls


@pytest.fixture(autouse=True)
def _clear_cache():
    site_boards._CACHE.clear()
    yield
    site_boards._CACHE.clear()


class TestSpecParsing:
    def test_site_specs(self):
        assert site_boards.is_site_spec("ats:ashby:openai")
        assert site_boards.is_site_spec("ats:greenhouse:anthropic")
        assert site_boards.is_site_spec("ats:lever:weride")          # S16
        assert site_boards.is_site_spec("ats:workable:tp-link-usa-corp")  # S16
        assert not site_boards.is_site_spec("nvidia|wd5|x")
        assert not site_boards.is_site_spec("")
        assert not site_boards.is_site_spec("ats:madeup:x")

    def test_parse_site(self):
        assert site_boards.parse_site("ats:ashby:openai") == ("ashby",
                                                              "openai")
        with pytest.raises(ValueError):
            site_boards.parse_site("nvidia|wd5|x")


class TestGreenhouseAdapter:
    def _board(self, monkeypatch, jobs):
        _patch_fetch(monkeypatch, jobs)
        return "ats:greenhouse:acme"

    def test_rows_shape_and_country_filter(self, monkeypatch):
        us = _gh_job(1, "REQ-1", "Engineer",
                     [{"id": 1, "name": "SF",
                       "location": "San Francisco, California, "
                                   "United States"}],
                     departments=["Engineering"],
                     published=(date.today() - timedelta(days=3)).isoformat()
                     + "T10:00:00-04:00")
        uk = _gh_job(2, "REQ-2", "London Engineer",
                     [{"id": 2, "name": "London",
                       "location": "London, Greater London, "
                                   "United Kingdom"}])
        multi = _gh_job(3, "REQ-3", "Multi",
                        [{"id": 3, "name": "SF",
                          "location": "San Francisco, California, "
                                      "United States"},
                         {"id": 4, "name": "London",
                          "location": "London, Greater London, "
                                      "United Kingdom"}])
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [us, uk, multi]),
            country="United States", cfg=Config(), progress_label="t")
        # ANY-US-office semantics: multi (SF+London) is US-available
        assert set(rows) == {"REQ-1", "REQ-3"}
        r = rows["REQ-1"]
        assert r["title"] == "Engineer"
        assert r["url"].endswith("/jobs/1")
        assert r["locationsText"] == "SF"
        assert r["postedOn"] == "Posted 3 Days Ago"
        assert r["timeType"] == ""            # greenhouse serves none
        assert r["departments"] == ["Engineering"]
        assert meta["country_client"] is False   # classified at list time
        assert meta["ats"] == "greenhouse"
        assert meta["client_filtered"] == 1
        assert meta["complete"] is True

    def test_detail_payload_shape(self, monkeypatch):
        us = _gh_job(7, "REQ-7", "Engineer",
                     [{"id": 1, "name": "SF",
                       "location": "San Francisco, California, "
                                   "United States"}],
                     departments=["Engineering"], content="<p>Body</p>")
        spec = self._board(monkeypatch, [us])
        p = site_boards.detail_payload(spec, "/jobs/7", Config())
        info = p["jobPostingInfo"]
        assert info["title"] == "Engineer"
        assert info["jobDescription"] == "<p>Body</p>"
        assert info["jobReqId"] == "REQ-7"
        assert info["country"] == {"descriptor": "United States"}
        assert p["hiringOrganization"]["name"] == "Acme Corp"
        assert p["similarJobs"] == []
        # workday-shape compatibility: the country classifier works
        assert workday.detail_in_country(p, "united states")
        # unknown path → None (the detail-unreachable convention)
        assert site_boards.detail_payload(spec, "/jobs/999",
                                          Config()) is None

    def test_no_country_no_filter(self, monkeypatch):
        us = _gh_job(1, "REQ-1", "A",
                     [{"id": 1, "name": "SF",
                       "location": "San Francisco, California, "
                                   "United States"}])
        uk = _gh_job(2, "REQ-2", "B",
                     [{"id": 2, "name": "London",
                       "location": "London, United Kingdom"}])
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [us, uk]), cfg=Config(),
            progress_label="t")
        assert set(rows) == {"REQ-1", "REQ-2"}    # global board
        assert meta["client_filtered"] == 0

    def test_reqid_fallback_to_numeric_id(self, monkeypatch):
        job = _gh_job(42, None, "NoReq",
                      [{"id": 1, "name": "SF",
                        "location": "SF, California, United States"}])
        rows, _ = site_boards.list_board(
            self._board(monkeypatch, [job]), cfg=Config(),
            progress_label="t")
        assert set(rows) == {"42"}


# ── S15: the four Greenhouse dialect boards ──────────────────────────────

def _gh_dialect_job(jid, req_id, title, location_name, offices,
                    metadata=None, published=None):
    """Dialect fixture: location.name INDEPENDENT of office names (the
    baidu/byd/neteasegames/shein shapes — _gh_job derives location.name
    from office names, the anthropic shape only)."""
    return {
        "id": jid, "title": title, "requisition_id": req_id,
        "absolute_url": f"https://job-boards.greenhouse.io/x/jobs/{jid}",
        "location": {"name": location_name},
        "offices": offices,
        "departments": [{"id": 1, "name": "Engineering"}],
        "content": "<p>desc</p>", "first_published": published,
        "company_name": "Acme Corp", "metadata": metadata,
    }


# baidu's board-wide default office (id 1570 'Sunnyvale, CA, United
# States' stamped on every job — including the Toronto rows)
_BAIDU_DEFAULT_OFFICE = [{"id": 1570, "name": "Sunnyvale, CA",
                          "location": "Sunnyvale, CA, United States",
                          "child_ids": [], "parent_id": None}]


class TestGreenhouseDialectLadder:
    """S15 pins — the evidence ladder (design §4) on the four dialect
    boards. Mechanics pinned here; real-payload verdicts pinned in
    TestGreenhouseCensusReplay below."""

    def _board(self, monkeypatch, jobs):
        _patch_fetch(monkeypatch, jobs)
        return "ats:greenhouse:acme"

    def test_baidu_default_office_disqualified(self, monkeypatch):
        """The baidu shape: one office id board-wide (recruiter default)
        → DISQUALIFIED — it must not rescue the Toronto rows via its
        'CA'/'United States' tokens. E2 governs: Sunnyvale/LA kept via
        state tokens, Toronto dropped."""
        sv = _gh_dialect_job(1, "B1", "Engineer", "Sunnyvale, CA",
                             _BAIDU_DEFAULT_OFFICE)
        toronto = _gh_dialect_job(
            2, None, "Client Manager - Canada", "Toronto, ON",
            _BAIDU_DEFAULT_OFFICE)          # office LIES (default)
        toronto2 = _gh_dialect_job(
            3, None, "BD - Canada", "Toronto, Ontario, Canada",
            _BAIDU_DEFAULT_OFFICE)
        la = _gh_dialect_job(4, None, "AE", "Los Angels, CA",
                             [])            # baidu's 2 office-less jobs
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [sv, toronto, toronto2, la]),
            country="United States", cfg=Config(), progress_label="t")
        assert set(rows) == {"B1", "4"}       # Toronto dropped; numeric-id
        # fallback covers the office-less LA row (req_id None → '4')
        assert rows["B1"]["countries"] == ["United States"]
        assert meta["offices_discriminate"] is False
        assert meta["client_filtered"] == 2
        assert meta["client_filtered_country"] == 2

    def test_neteasegames_semicolon_tokens(self, monkeypatch):
        """neteasegames: offices carry ids but no useful locations; the
        geography is location.name's semicolon country tokens. Rung 1
        rescues the US-remote rows; city-only mixed rows drop (rung 4).
        """
        us_multi = _gh_dialect_job(
            1, "984", "Animator",
            "Canada-Remote; Spain-Remote; United Kingdom - Guildford "
            "Onsite; United States-Remote",
            [{"id": 20, "name": "Global", "location": ""},
             {"id": 21, "name": "Guildford", "location": ""}])
        mixed_na = _gh_dialect_job(
            2, "920", "PM",
            "Bothell - Onsite; Irvine - Onsite; Mountain View-Onsite; "
            "Vancouver-Onsite",
            [{"id": 20, "name": "Global", "location": ""},
             {"id": 22, "name": "Vancouver", "location": "Vancouver-Onsite"}])
        sg = _gh_dialect_job(3, "1013", "Producer", "Singapore-Guoco Midtown",
                             [{"id": 20, "name": "Global", "location": ""}])
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [us_multi, mixed_na, sg]),
            country="United States", cfg=Config(), progress_label="t")
        assert set(rows) == {"984"}          # rung 1: 'United States-Remote'
        assert rows["984"]["locationsText"].startswith("Canada-Remote")
        assert meta["client_filtered_country"] == 2

    def test_byd_malformed_offices_state_tokens(self, monkeypatch):
        """byd: office last-segments are 'CA 95337'-shaped (not country
        strings) but offices vary per job (discriminating); rung 3
        token-scans e1+e2 and finds the CA state token."""
        manteca = _gh_dialect_job(
            1, "68", "BD Manager", "Manteca, CA",
            [{"id": 30, "name": "Manteca",
              "location": "1192 Vanderbilt Cir #100, Manteca, CA 95337"}])
        lancaster = _gh_dialect_job(
            2, "149", "Engineer",
            "Lancaster, CA (Office Only - Worksite in Dickinson)",
            [{"id": 31, "name": "Lancaster",
              "location": "170 BYD Energy Road, Lancaster, CA 93535"}])
        cupertino_bare = _gh_dialect_job(
            3, "163", "PM", "Cupertino",
            [{"id": 32, "name": "Cupertino",
              "location": "Cupertino, California, United States"}])
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [manteca, lancaster, cupertino_bare]),
            country="United States", cfg=Config(), progress_label="t")
        assert set(rows) == {"68", "149", "163"}
        assert meta["offices_discriminate"] is True

    def test_she_in_metadata_time_type(self, monkeypatch):
        """shein: Employment Type metadata → _TT-normalized timeType;
        the Part-time row drops under a Full-time filter (structured
        field, not title-guessing); unmapped values pass through."""
        ft = _gh_dialect_job(
            1, "GRQ1", "Buyer", "Los Angeles",
            [{"id": 40, "name": "LA",
              "location": "Los Angeles, California, United States"}],
            metadata=[{"id": 1, "name": "Employment Type",
                       "value": "Full-time", "value_type": "single_select"}])
        pt = _gh_dialect_job(
            2, "GRQ2", "Counsel (Part-Time, Contract)", "Los Angeles",
            [{"id": 40, "name": "LA",
              "location": "Los Angeles, California, United States"}],
            metadata=[{"id": 1, "name": "Employment Type",
                       "value": "Part-time", "value_type": "single_select"}])
        odd = _gh_dialect_job(
            3, "GRQ3", "Specialist", "San Diego",
            [{"id": 41, "name": "SD",
              "location": "San Diego, California, United States"}],
            metadata=[{"id": 1, "name": "Employment Type",
                       "value": "Contract", "value_type": "single_select"}])
        board = self._board(monkeypatch, [ft, pt, odd])
        rows, meta = site_boards.list_board(
            board, country="United States", time_type="Full time",
            cfg=Config(), progress_label="t")
        assert set(rows) == {"GRQ1"}         # Part-time filtered, Contract
        # stays (unmapped pass-through ≠ Full time → also drops under
        # the filter — honest structured filtering)
        assert meta["client_filtered_time"] == 2
        # no filter: all 3 rows, timeType served + pass-through
        rows2, _ = site_boards.list_board(
            board, cfg=Config(), progress_label="t")
        assert set(rows2) == {"GRQ1", "GRQ2", "GRQ3"}
        assert rows2["GRQ1"]["timeType"] == "Full time"
        assert rows2["GRQ2"]["timeType"] == "Part time"
        assert rows2["GRQ3"]["timeType"] == "Contract"

    def test_note_gating_on_metadata(self, monkeypatch, capsys):
        """time filter requested on a board WITHOUT Employment Type
        metadata → the honest NOTE; on a metadata-serving board →
        silent (the filter is real)."""
        no_meta = _gh_dialect_job(
            1, "R1", "A", "Sunnyvale, CA", _BAIDU_DEFAULT_OFFICE)
        site_boards.list_board(
            self._board(monkeypatch, [no_meta]), time_type="Full time",
            country="United States", cfg=Config(), progress_label="t")
        err = capsys.readouterr().err
        assert "NOT applied" in err and "employment-type" in err
        with_meta = _gh_dialect_job(
            2, "R2", "B", "Los Angeles",
            [{"id": 40, "name": "LA",
              "location": "Los Angeles, California, United States"}],
            metadata=[{"id": 1, "name": "Employment Type",
                       "value": "Full-time", "value_type": "x"}])
        site_boards.list_board(
            self._board(monkeypatch, [with_meta]), time_type="Full time",
            country="United States", cfg=Config(), progress_label="t")
        err = capsys.readouterr().err
        assert "NOT applied" not in err

    def test_rung1_segment_not_pooled(self, monkeypatch):
        """The reviewer's #4 semantics pin: rung 1 matches per-SEGMENT
        (phrase tokens hyphen-split inside the segment) — the pooled
        _row_in_country reading would miss '…; United States' after a
        semicolon and add a whole-string 'us' channel."""
        multi = _gh_dialect_job(
            1, "R1", "Multi",
            "London, UK; Ontario, CAN; Remote-Friendly, United States; "
            "San Francisco, CA", [])
        rows, _ = site_boards.list_board(
            self._board(monkeypatch, [multi]), country="United States",
            cfg=Config(), progress_label="t")
        assert set(rows) == {"R1"}

    def test_anthropic_rescue_shape(self, monkeypatch):
        """The +31 census rescues: job whose ONLY office has a NULL
        location ('Remote-Friendly US (Travel Required)') — the office
        channel never sees it; E2 'Remote-Friendly, United States'
        rescues via rung 1."""
        rescued = _gh_dialect_job(
            1, "R1", "Remote Researcher", "Remote-Friendly, United States",
            [{"id": 50, "name": "Remote-Friendly US (Travel Required)",
              "location": None}])
        kept_with_office = _gh_dialect_job(
            2, "R2", "SF Engineer", "San Francisco, CA",
            [{"id": 51, "name": "SF",
              "location": "San Francisco, California, United States"}])
        rows, _ = site_boards.list_board(
            self._board(monkeypatch, [rescued, kept_with_office]),
            country="United States", cfg=Config(), progress_label="t")
        assert set(rows) == {"R1", "R2"}

    def test_null_e2_fallback(self, monkeypatch):
        """Disqualified offices + EMPTY location.name → the office
        channel is still consulted (a board serving zero location
        free-text must not lose all rows to the demotion heuristic)."""
        job = _gh_dialect_job(1, "R1", "A", "", _BAIDU_DEFAULT_OFFICE)
        rows, _ = site_boards.list_board(
            self._board(monkeypatch, [job]), country="United States",
            cfg=Config(), progress_label="t")
        assert set(rows) == {"R1"}

    def test_detail_seam_threaded_and_unthreaded(self, monkeypatch):
        """S15 seam: threaded detail derives country from the SAME
        ladder verdict as the list row; unthreaded stays the legacy
        office-derived descriptor (back-compat with the 09-19 pin)."""
        toronto = _gh_dialect_job(
            1, "T1", "Canada Role", "Toronto, ON", _BAIDU_DEFAULT_OFFICE)
        sv = _gh_dialect_job(2, "S1", "US Role", "Sunnyvale, CA",
                             _BAIDU_DEFAULT_OFFICE)
        board = self._board(monkeypatch, [toronto, sv])
        rows, _ = site_boards.list_board(board, country="United States",
                                         cfg=Config(), progress_label="t")
        assert set(rows) == {"S1"}
        # threaded: the US row's detail agrees with the list verdict
        p = site_boards.detail_payload(board, "/jobs/2", Config(),
                                       country="United States")
        assert p["jobPostingInfo"]["country"] == {
            "descriptor": "United States"}
        assert workday.detail_in_country(p, "united states")
        # threaded: the dropped row's detail honestly says not-US
        p2 = site_boards.detail_payload(board, "/jobs/1", Config(),
                                        country="United States")
        assert p2["jobPostingInfo"]["country"] is None
        assert not workday.detail_in_country(p2, "united states")
        # unthreaded: legacy office-derived descriptor (the default
        # office's last segment) — back-compat
        p3 = site_boards.detail_payload(board, "/jobs/1", Config())
        assert p3["jobPostingInfo"]["country"] == {
            "descriptor": "United States"}

    def test_detail_she_in_time_type(self, monkeypatch):
        """The CSV timeType column reads the DETAIL payload — the
        shein metadata must surface there too (review #7b)."""
        shein = _gh_dialect_job(
            1, "GRQ1", "Buyer", "Los Angeles",
            [{"id": 40, "name": "LA",
              "location": "Los Angeles, California, United States"}],
            metadata=[{"id": 1, "name": "Employment Type",
                       "value": "Full-time", "value_type": "x"}])
        board = self._board(monkeypatch, [shein])
        p = site_boards.detail_payload(board, "/jobs/1", Config())
        assert p["jobPostingInfo"]["timeType"] == "Full time"


_CENSUS_DIR = (Path(__file__).resolve().parents[1]
               / "data" / "ats_seed" / "s15_census")


class TestGreenhouseCensusReplay:
    """Census-replay pins (review #7a): the four committed live
    payloads (2026-09-24, ?content=true) hold their board verdicts —
    the arithmetic of design §4, pinned so any future ladder change
    that shifts a real board's numbers fails HERE first."""

    @staticmethod
    def _board(monkeypatch, org, filename=None):
        payload = json.loads(
            (_CENSUS_DIR / (filename or f"gh_{org}.json")).read_text(
                encoding="utf-8"))
        monkeypatch.setattr(site_boards, "_CACHE", {
            f"ats:greenhouse:{org}": (time.monotonic(),
                                      payload.get("jobs") or [])})
        return f"ats:greenhouse:{org}"

    def test_baidu(self, monkeypatch):
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, "baidu"), country="United States",
            cfg=Config(), progress_label="t")
        assert len(rows) == 25 and meta["total"] == 28
        assert meta["client_filtered_country"] == 3
        assert meta["offices_discriminate"] is False

    def test_byd(self, monkeypatch):
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, "byd"), country="United States",
            cfg=Config(), progress_label="t")
        assert len(rows) == 22 and meta["total"] == 22
        assert meta["client_filtered"] == 0
        assert meta["offices_discriminate"] is True

    def test_neteasegames(self, monkeypatch):
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, "neteasegames"),
            country="United States", cfg=Config(), progress_label="t")
        assert len(rows) == 3 and meta["total"] == 31
        assert meta["client_filtered_country"] == 28

    def test_shein_country_only(self, monkeypatch):
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, "shein"), country="United States",
            cfg=Config(), progress_label="t")
        assert len(rows) == 18 and meta["total"] == 18
        assert meta["client_filtered_country"] == 0

    def test_shein_full_time_filter(self, monkeypatch):
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, "shein"), country="United States",
            time_type="Full time", cfg=Config(), progress_label="t")
        assert len(rows) == 17
        assert meta["client_filtered_time"] == 1
        assert all(r["timeType"] == "Full time" for r in rows.values())

    def test_shein_unmapped_pass_through(self, monkeypatch):
        """The one Part-time row (Los Angeles Marketing Counsel) — its
        timeType value passes through honestly (not blanked)."""
        board = self._board(monkeypatch, "shein")
        rows, _ = site_boards.list_board(board, cfg=Config(),
                                         progress_label="t")
        tts = {r["timeType"] for r in rows.values()}
        assert "Part time" in tts and "Full time" in tts

    def test_anthropic_no_regressions_plus_rescues(self, monkeypatch):
        """The adapter-change audit (review #13a): on the 629-job
        census, the shipped office classifier keeps 469 jobs, the
        ladder keeps 500 (0 regressions, +31 remote-US rescues); the
        LIST row count is 468 after the shipped per-requisition dedup
        (19 duplicate requisition_ids)."""
        board = self._board(monkeypatch, "anthropic",
                            "anthropic_full.json")
        jobs = site_boards._CACHE["ats:greenhouse:anthropic"][1]
        ad = site_boards.GreenhouseAdapter("anthropic", Config())
        disc = ad._offices_discriminate(jobs)
        shipped = sum(
            1 for j in jobs
            if any(workday.country_str_matches(
                ((o or {}).get("location") or "").split(",")[-1].strip(),
                "united states")
                for o in (j.get("offices") or [])))
        ladder_jobs = sum(
            1 for j in jobs if ad._job_in_country(j, "united states",
                                                  disc))
        assert shipped == 469 and ladder_jobs == 500
        rows, meta = site_boards.list_board(board, country="United States",
                                            cfg=Config(),
                                            progress_label="t")
        assert len(rows) == 468 and meta["total"] == 629
        assert meta["client_filtered"] == 129


class TestAshbyAdapter:
    def _board(self, monkeypatch, jobs):
        _patch_fetch(monkeypatch, jobs)
        return "ats:ashby:acme"

    def test_rows_country_and_time_filter(self, monkeypatch):
        us = _ashby_job("a1", "Engineer")
        uk = _ashby_job("a2", "London Engineer", country="United Kingdom",
                        loc="London, UK")
        pt = _ashby_job("a3", "Part timer", employment="PartTime")
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [us, uk, pt]),
            country="United States", time_type="Full time",
            cfg=Config(), progress_label="t")
        assert set(rows) == {"a1"}
        r = rows["a1"]
        assert r["timeType"] == "Full time"       # normalized dialect
        assert r["locationsText"] == "San Francisco"
        assert meta["ats"] == "ashby"
        assert meta["country_client"] is False
        assert meta["client_filtered"] == 2

    def test_address_missing_falls_back_to_location_tail(self, monkeypatch):
        job = _ashby_job("a9", "Engineer", loc="Seattle, US")
        job["address"] = None                     # no structured country
        rows, _ = site_boards.list_board(
            self._board(monkeypatch, [job]), country="United States",
            cfg=Config(), progress_label="t")
        assert set(rows) == {"a9"}

    def test_unlisted_jobs_excluded(self, monkeypatch):
        listed = _ashby_job("a1", "A")
        unlisted = _ashby_job("a2", "B", listed=False)
        rows, _ = site_boards.list_board(
            self._board(monkeypatch, [listed, unlisted]), cfg=Config(),
            progress_label="t")
        assert set(rows) == {"a1"}

    def test_detail_payload_shape(self, monkeypatch):
        job = _ashby_job("a1", "Engineer", published="2026-09-01T00:00:00Z")
        spec = self._board(monkeypatch, [job])
        p = site_boards.detail_payload(spec, "/a1", Config())
        info = p["jobPostingInfo"]
        assert info["title"] == "Engineer"
        assert info["timeType"] == "Full time"
        assert info["country"] == {"descriptor": "United States"}
        assert info["jobReqId"] == "a1"
        assert workday.detail_in_country(p, "united states")
        assert site_boards.detail_payload(spec, "/zzz",
                                          Config()) is None


class TestPostedLabel:
    def test_label_and_iso(self):
        iso = (date.today() - timedelta(days=5)).isoformat() \
            + "T12:00:00+00:00"
        label, norm = site_boards._posted_label(iso)
        assert label == "Posted 5 Days Ago"
        assert norm == (date.today() - timedelta(days=5)).isoformat()

    def test_today(self):
        label, _ = site_boards._posted_label(
            date.today().isoformat() + "T00:00:00Z")
        assert label == "Posted Today"

    def test_long_tenure_never_censored(self):
        # workday censors at 30+; adapters know the exact date — the
        # label stays numeric (parse_posted_on handles any N)
        iso = (date.today() - timedelta(days=87)).isoformat() \
            + "T00:00:00Z"
        label, _ = site_boards._posted_label(iso)
        assert label == "Posted 87 Days Ago"

    def test_bad_iso(self):
        assert site_boards._posted_label("") == ("", "")
        assert site_boards._posted_label("not-a-date") == ("", "")


class TestCache:
    def test_one_fetch_per_process(self, monkeypatch):
        jobs = [_ashby_job("a1", "A")]
        calls = _patch_fetch(monkeypatch, jobs)
        spec = "ats:ashby:acme"
        site_boards.list_board(spec, cfg=Config(), progress_label="t")
        site_boards.list_board(spec, cfg=Config(), progress_label="t")
        site_boards.detail_payload(spec, "/a1", Config())
        site_boards.detail_payload(spec, "/a1", Config())
        assert len(calls) == 1          # list + 2 details → ONE network hit


class TestDispatchRouting:
    def test_workday_spec_routes_to_workday(self, monkeypatch):
        seen = {}

        def fake_list_board(spec, **kw):
            seen["spec"] = spec
            seen["kw"] = kw
            return {"JR1": {"reqId": "JR1"}}, {"complete": True}
        monkeypatch.setattr(site_boards.workday, "list_board",
                            fake_list_board)
        rows, meta = site_boards.list_board(
            "nvidia|wd5|site", country="United States", client_filter=False)
        assert seen["spec"] == "nvidia|wd5|site"
        assert seen["kw"]["country"] == "United States"
        assert seen["kw"]["client_filter"] is False
        assert rows == {"JR1": {"reqId": "JR1"}}

    def test_workday_detail_routing(self, monkeypatch):
        def fake_detail(board, path, cfg):
            assert board == ("nvidia", "wd5", "site")
            return {"jobPostingInfo": {"title": "T"}}
        monkeypatch.setattr(site_boards.workday, "detail_payload",
                            fake_detail)
        p = site_boards.detail_payload("nvidia|wd5|site", "/job/X",
                                       Config())
        assert p["jobPostingInfo"]["title"] == "T"


# ═════════════════════════ S14: custom own-platform boards ═════════════════

class _FakeResp:
    def __init__(self, status=200, cookies=None):
        self.status_code = status
        self.cookies = _FakeJar(cookies or {})


class _FakeJar:
    def __init__(self, kv):
        self.jar = [_FakeCookie(k, v) for k, v in kv.items()]


class _FakeCookie:
    def __init__(self, name, value):
        self.name = name
        self.value = value


def _bd_post(code="A1", country="United States of America",
             city="Seattle", rt="Regular", cat_parent="R&D",
             cat_child="Data", title="T", pid="7", desc="D", req="R"):
    return {"id": pid, "code": code, "title": title,
            "description": desc, "requirement": req,
            "recruit_type": {"en_name": rt},
            "job_category": {"en_name": cat_child,
                             "parent": {"en_name": cat_parent}},
            "city_info": {"en_name": city, "parent": {
                "en_name": "Washington", "parent": {
                    "en_name": country}}}}


def _patch_bd(monkeypatch, pages, post_calls=None, fail_codes=None):
    """Fake the bytedance pagination: pages = list of job_post_lists."""
    calls = post_calls if post_calls is not None else []
    state = {"emptied_first": fail_codes == "empty-first"}

    def fake_post(url, body, headers=None, timeout=30.0):
        calls.append((url, body, headers))
        if (state["emptied_first"] and body["offset"] == 0
                and not state.get("did_retry")):
            state["did_retry"] = True
            return {"data": {"job_post_list": []}}
        idx = body["offset"] // body["limit"]
        page = pages[idx] if idx < len(pages) else []
        return {"data": {"job_post_list": page}}

    monkeypatch.setattr(site_boards, "_imp_post_json", fake_post)
    monkeypatch.setattr(site_boards, "_CACHE", {})
    return calls


class TestCustomGrammar:
    def test_custom_specs(self):
        for s in ("custom:bytedance", "custom:alibaba", "custom:tripcom"):
            assert site_boards.is_site_spec(s), s
        assert not site_boards.is_site_spec("custom:unknown")
        assert not site_boards.is_site_spec("custom:")
        assert site_boards.parse_site("custom:bytedance") == \
            ("bytedance", "")

    def test_unknown_custom_rejected_with_kinds(self):
        with pytest.raises(ValueError, match="bytedance"):
            site_boards.parse_site("custom:unknown")


class TestByteDanceAdapter:
    def _board(self, monkeypatch, posts):
        return _patch_bd(monkeypatch, [posts])

    def test_row_mapping_and_country(self, monkeypatch):
        posts = [_bd_post(code="A1", country="United States of America"),
                 _bd_post(code="A2", country="Singapore", city="Singapore",
                          pid="8"),
                 _bd_post(code="A3", rt="Intern"),
                 _bd_post(code="A4", country="United Kingdom",
                          city="London", pid="9")]
        _patch_bd(monkeypatch, [posts])
        rows, meta = site_boards.list_board(
            "custom:bytedance", country="United States")
        assert set(rows) == {"A1", "A3"}   # A3 is US (no time filter)
        r = rows["A1"]
        assert r["reqId"] == "A1" and r["title"] == "T"
        assert r["timeType"] == "Full time"        # Regular → dialect
        assert r["externalPath"] == "/bytedance/A1"
        assert r["url"].startswith("https://joinbytedance.com/search/7")
        assert r["postedOn"] == ""                  # honest: no dates
        assert r["departments"] == ["R&D", "Data"]
        assert meta["country_client"] is False
        assert meta["client_filtered"] == 2         # SG + UK dropped
        assert meta["complete"] is True
    def test_united_kingdom_phrase_not_united(self, monkeypatch):
        # multi-word country: 'United Kingdom' must NOT match 'united'
        posts = [_bd_post(code="A9", country="United Kingdom",
                          city="London", pid="99")]
        _patch_bd(monkeypatch, [posts])
        rows, meta = site_boards.list_board(
            "custom:bytedance", country="United States")
        assert rows == {} and meta["client_filtered"] == 1

    def test_null_city_info_dropped_loud(self, monkeypatch):
        p = _bd_post(code="A1")
        p["city_info"] = None
        _patch_bd(monkeypatch, [[p]])
        rows, meta = site_boards.list_board(
            "custom:bytedance", country="United States")
        assert rows == {}
        assert meta["unresolved_dropped"] == 1      # SEV-6: never claimed

    def test_time_type_filter_interns(self, monkeypatch):
        posts = [_bd_post(code="A1"), _bd_post(code="A5", rt="Intern")]
        _patch_bd(monkeypatch, [posts])
        rows, meta = site_boards.list_board(
            "custom:bytedance", country="United States",
            time_type="Full time")
        assert set(rows) == {"A1"}
        assert meta["client_filtered"] >= 1

    def test_pagination_short_page_terminates(self, monkeypatch):
        pages = [[_bd_post(code=f"A{i}", pid=str(i)) for i in range(200)],
                 [_bd_post(code="A201", pid="201")]]
        calls = _patch_bd(monkeypatch, pages)
        rows, meta = site_boards.list_board("custom:bytedance")
        assert meta["total"] == 201 and len(rows) == 201
        assert len(calls) == 2                       # terminated on short

    def test_empty_first_page_retried_once(self, monkeypatch):
        pages = [[_bd_post(code="A1")]]
        calls = _patch_bd(monkeypatch, pages, fail_codes="empty-first")
        rows, _ = site_boards.list_board("custom:bytedance")
        assert len(rows) == 1
        assert len(calls) == 2                       # retry then success

    def test_non_200_raises(self, monkeypatch):
        def fake_post(url, body, headers=None, timeout=30.0):
            raise RuntimeError("HTTP 508 (impersonated POST)")
        monkeypatch.setattr(site_boards, "_imp_post_json", fake_post)
        monkeypatch.setattr(site_boards, "_CACHE", {})
        with pytest.raises(RuntimeError, match="508"):
            site_boards.list_board("custom:bytedance")

    def test_detail_roundtrip_code_and_id(self, monkeypatch):
        posts = [_bd_post(code="A1", pid="7", desc="Long desc")]
        _patch_bd(monkeypatch, [posts])
        site_boards.list_board("custom:bytedance",
                               country="United States")
        for path in ("/bytedance/A1", "A1", "/bytedance/7", "7"):
            d = site_boards.detail_payload("custom:bytedance", path,
                                           Config())
            assert d, path
            assert d["jobPostingInfo"]["jobReqId"] == "A1"
            assert "Long desc" in d["jobPostingInfo"]["jobDescription"]
        assert site_boards.detail_payload("custom:bytedance", "nope",
                                          Config()) is None

    def test_website_path_header_sent(self, monkeypatch):
        posts = [_bd_post()]
        calls = _patch_bd(monkeypatch, [posts])
        site_boards.list_board("custom:bytedance")
        url, body, headers = calls[0]
        assert headers["website-path"] == "en"       # en-portal selector
        assert body["limit"] == site_boards.ByteDanceAdapter._PAGE


def _ali_row(code, locs=("Sunnyvale",), publish=1789637099000,
             name="Ali Job", pid=700, hostkey="aidc"):
    return {"code": code, "id": pid, "name": name,
            "description": "desc", "requirement": "req",
            "publishTime": publish, "workLocations": list(locs),
            "categories": ["Integration - Procurement"],
            "positionUrl": f"/en/off-campus/position-detail?positionId={pid}"}


def _patch_ali(monkeypatch, host_rows, fail_hosts=()):
    """Fake the alibaba multi-host sweep. host_rows: {hostkey: [rows]}.
    S16: the sweep tests exercise the merge/classification logic — all
    hosts take the FAKE impersonated path (the per-host transport
    choice is live behavior, pinned by its own test)."""
    session = _FakeResp(200, {"XSRF-TOKEN": "csrf-123"})
    monkeypatch.setattr(site_boards, "_imp_get",
                        lambda url, headers=None, timeout=25.0: session)
    monkeypatch.setattr(
        site_boards.AlibabaAdapter, "_PLAIN_HOSTS", set())
    posts = []
    post_bodies = []

    def fake_post(url, body, headers=None, timeout=30.0):
        post_bodies.append((url, body, headers))
        hostkey = "aidc" if "aidc-jobs" in url else \
            "cloud" if "alibabacloud" in url else \
            "holding" if "talent-holding" in url else "tongyi"
        rows = host_rows.get(hostkey, [])
        return {"content": {"datas": rows,
                            "totalCount": len(rows)}}

    monkeypatch.setattr(site_boards, "_imp_post_json", fake_post)
    monkeypatch.setattr(site_boards, "_imp_session",
                        lambda: session)
    monkeypatch.setattr(site_boards, "_CACHE", {})
    site_boards._ALIBABA_SKIPPED["v"] = []
    return post_bodies


class TestAlibabaAdapter:
    def test_us_row_mapping_raw_reqid(self, monkeypatch):
        _patch_ali(monkeypatch, {"aidc": [
            _ali_row("GP1", ("Sunnyvale",), pid=701),
            _ali_row("GP2", ("Karachi",), pid=702),
            _ali_row("GP3", ("Mumbai",), pid=703)]})
        rows, meta = site_boards.list_board(
            "custom:alibaba", country="United States")
        assert set(rows) == {"GP1"}
        r = rows["GP1"]
        assert r["reqId"] == "GP1"                    # RAW (SEV-1)
        assert r["externalPath"] == "/aidc/GP1"       # host provenance
        assert r["locationsText"] == "Sunnyvale"
        assert r["postedOn"].startswith("Posted ")    # epoch-ms → label
        assert meta["complete"] is True
        assert meta["client_filtered"] == 2

    def test_multi_host_merge_and_dedup(self, monkeypatch):
        # same code on two hosts → ONE row (keep-first) + loud count
        _patch_ali(monkeypatch, {
            "aidc": [_ali_row("GP1", ("Sunnyvale",), pid=701)],
            "holding": [_ali_row("GP1", ("Sunnyvale",), pid=999),
                        _ali_row("GP2", ("Bellevue",), pid=702)]})
        rows, meta = site_boards.list_board(
            "custom:alibaba", country="United States")
        assert set(rows) == {"GP1", "GP2"}
        assert meta["duplicate_codes"] == 1
        assert rows["GP2"]["externalPath"] == "/holding/GP2"

    def test_unknown_city_dropped_loud(self, monkeypatch):
        _patch_ali(monkeypatch, {"aidc": [
            _ali_row("GPX", ("Nowhere City",), pid=710)]})
        rows, meta = site_boards.list_board(
            "custom:alibaba", country="United States")
        assert rows == {}
        assert meta["unresolved_dropped"] == 1        # SEV-6

    def test_any_us_location_semantics(self, monkeypatch):
        _patch_ali(monkeypatch, {"aidc": [
            _ali_row("GP1", ("Hangzhou", "Sunnyvale"), pid=711)]})
        rows, _ = site_boards.list_board(
            "custom:alibaba", country="United States")
        assert set(rows) == {"GP1"}

    def test_dns_fail_soft_complete_false(self, monkeypatch):
        # cloud host unreachable → rows from survivors, complete=False
        session = _FakeResp(200, {"XSRF-TOKEN": "csrf-123"})
        monkeypatch.setattr(site_boards, "_imp_session",
                            lambda: session)
        monkeypatch.setattr(
            site_boards.AlibabaAdapter, "_PLAIN_HOSTS", set())

        def fake_get(url, headers=None, timeout=25.0):
            if "alibabacloud" in url:
                raise RuntimeError("Could not resolve host")
            return session

        monkeypatch.setattr(site_boards, "_imp_get", fake_get)

        def fake_post(url, body, headers=None, timeout=30.0):
            assert "_csrf=" in url                     # XSRF wired
            return {"content": {"datas": [_ali_row("GP1")],
                                "totalCount": 1}}

        monkeypatch.setattr(site_boards, "_imp_post_json", fake_post)
        monkeypatch.setattr(site_boards, "_CACHE", {})
        site_boards._ALIBABA_SKIPPED["v"] = []
        rows, meta = site_boards.list_board(
            "custom:alibaba", country="United States")
        assert set(rows) == {"GP1"}                    # survivors served
        assert meta["complete"] is False               # SEV-2
        assert meta["hosts_skipped"] == ["cloud"]

    def test_cloud_host_uses_plain_transport(self, monkeypatch):
        """S16: the NEW cloud host (careers.alibabacloud.com, after the
        old hyphenated host went NXDOMAIN) REJECTS chrome-impersonated
        POSTs (HTTP 405, live-measured 2026-09-24) — the sweep must
        dispatch PLAIN transport for it, impersonated for the rest."""
        calls: dict[str, str] = {}

        def fake_plain(self, base):
            calls["plain"] = base
            return [], True

        def fake_imp(self, base):
            if not isinstance(calls.get("imp"), list):
                calls["imp"] = []
            calls["imp"].append(base)
            return [], True

        monkeypatch.setattr(
            site_boards.AlibabaAdapter, "_host_rows_plain", fake_plain)
        monkeypatch.setattr(
            site_boards.AlibabaAdapter, "_host_rows_imp", fake_imp)
        a = site_boards.AlibabaAdapter("", None)
        imp_hosts = []
        for hostkey, host in a._HOSTS:
            a._host_rows(hostkey, host)
        assert calls.get("plain") == "https://careers.alibabacloud.com"
        assert isinstance(calls.get("imp"), list) and len(calls["imp"]) == 3

    def test_country_client_false_finish_not_gated(self, monkeypatch):
        # the flag means 'already classified at list time' — the netflix
        # countryfilter-pending flow must NOT trigger (live-found bug)
        _patch_ali(monkeypatch, {"aidc": [_ali_row("GP1")]})
        _, meta = site_boards.list_board(
            "custom:alibaba", country="United States")
        assert meta["country_client"] is False

    def test_detail_roundtrip(self, monkeypatch):
        _patch_ali(monkeypatch, {"aidc": [_ali_row("GP1", pid=701)]})
        site_boards.list_board("custom:alibaba",
                               country="United States")
        d = site_boards.detail_payload("custom:alibaba", "/aidc/GP1",
                                       Config())
        assert d["jobPostingInfo"]["jobReqId"] == "GP1"
        assert d["hiringOrganization"]["name"] == "Alibaba Group"
        assert d["jobPostingInfo"]["startDate"]  # exact publish date

    def test_publish_time_epoch_ms_utc(self, monkeypatch):
        # 2026-09-15T~ epoch ms → 'Posted N Days Ago' on a UTC basis
        from datetime import datetime, timezone
        dt = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
        _patch_ali(monkeypatch, {"aidc": [
            _ali_row("GP1", publish=int(dt.timestamp() * 1000))]})
        rows, _ = site_boards.list_board("custom:alibaba",
                                         country="United States")
        assert "posted" in rows["GP1"]["postedOn"].lower()
        assert rows["GP1"]["firstPublishedIso"].startswith("2026-09-15")


def _tc_job(from_id="MJ003945", title="Biz Dev (MJ003945)", city="Miami",
            kind="Regular", publish="2026-09-10", jid="uuid-1",
            family="Commercial", bu="Trip.com"):
    return {"id": "30119146", "fromId": from_id, "jobId": jid,
            "jobTitle": title, "publishDate": publish, "city": "Miami_Hier",
            "cityName": city, "requirements": "<p>req</p>",
            "duty": "<p>duty</p>", "jobFamilyGroupName": family,
            "buName": bu, "kind": 1, "kindName": kind}


def _patch_tc(monkeypatch, jobs, loc_entries=None):
    calls = []

    def fake_post(url, body, headers=None, timeout=30.0):
        calls.append((url, body, headers))
        if "getLocation" in url:
            return {"retValue": loc_entries or [
                {"type": "OverseasCareersCountry", "code": "USA",
                 "name": "United States"}]}
        return {"retValue": {"total": len(jobs),
                             "recruitJobAdList": jobs}}

    monkeypatch.setattr(site_boards, "_imp_post_json", fake_post)
    monkeypatch.setattr(site_boards, "_CACHE", {})
    return calls


class TestTripComAdapter:
    def test_row_mapping_and_mj_strip(self, monkeypatch):
        _patch_tc(monkeypatch, [_tc_job()])
        rows, meta = site_boards.list_board(
            "custom:tripcom", country="United States")
        r = rows["MJ003945"]
        assert r["title"] == "Biz Dev"               # (MJ…) artifact gone
        assert r["reqId"] == "MJ003945"              # code survives
        assert r["timeType"] == "Full time"          # Regular → dialect
        assert r["postedOn"].startswith("Posted ")
        assert r["departments"][0] == "Commercial"
        assert meta["country_client"] is False       # server-side filter

    def test_country_request_shape_iso3(self, monkeypatch):
        calls = _patch_tc(monkeypatch, [_tc_job()])
        site_boards.list_board("custom:tripcom",
                               country="United States")
        api = [c for c in calls if "getOverseaJobAd" in c[0]][0]
        url, body, headers = api
        assert body["condition"]["country"] == ["USA"]  # ISO-3 taxonomy
        assert body["pager"]["index"] == "1"          # STRING pager
        assert headers["Accept"] == "application/json"  # XML guard

    def test_kind_blank_time_type_blank(self, monkeypatch):
        j = _tc_job(title="No Kind")
        j["kindName"] = ""
        _patch_tc(monkeypatch, [j])
        rows, _ = site_boards.list_board("custom:tripcom",
                                         country="United States")
        assert rows["MJ003945"]["timeType"] == ""    # never guessed

    def test_unknown_country_raises(self, monkeypatch):
        _patch_tc(monkeypatch, [])
        with pytest.raises(RuntimeError, match="ISO-3"):
            site_boards.list_board("custom:tripcom",
                                   country="Atlantis")

    def test_detail_roundtrip(self, monkeypatch):
        _patch_tc(monkeypatch, [_tc_job()])
        site_boards.list_board("custom:tripcom",
                               country="United States")
        d = site_boards.detail_payload("custom:tripcom",
                                       "/tripcom/MJ003945", Config())
        assert d["jobPostingInfo"]["title"] == "Biz Dev"
        assert "<p>duty</p>" in d["jobPostingInfo"]["jobDescription"]
        assert d["jobPostingInfo"]["startDate"] == "2026-09-10"
        assert d["hiringOrganization"]["name"] == "Trip.com Group"


class TestImpRetry:
    """S14 run-#42: a single CDN hiccup (curl-28, 0 bytes) must not
    fail a whole watch run — one retry with a session reset; a
    persistent outage still raises (the fail-safe stays intact)."""

    def test_transient_timeout_retried_once(self, monkeypatch):
        import time as _t
        from types import SimpleNamespace
        monkeypatch.setattr(
            site_boards, "time",
            SimpleNamespace(sleep=lambda s: None,
                            monotonic=_t.monotonic))
        state = {"calls": 0, "resets": 0}
        real_reset = site_boards._imp_reset_session

        def fake_reset():
            state["resets"] += 1

        monkeypatch.setattr(site_boards, "_imp_reset_session", fake_reset)

        def fake_post(url, body, headers=None, timeout=30.0):
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError(
                    "Timeout: Failed to perform, curl: (28) Operation "
                    "timed out after 30002 milliseconds with 0 bytes")
            return {"data": {"job_post_list": [_bd_post()]}}
        monkeypatch.setattr(site_boards, "_imp_post_json", fake_post)
        monkeypatch.setattr(site_boards, "_CACHE", {})
        rows, meta = site_boards.list_board("custom:bytedance")
        assert len(rows) == 1                     # recovered
        assert state["calls"] == 2 and state["resets"] == 1

    def test_persistent_outage_raises_after_retry(self, monkeypatch):
        import time as _t
        from types import SimpleNamespace
        monkeypatch.setattr(
            site_boards, "time",
            SimpleNamespace(sleep=lambda s: None,
                            monotonic=_t.monotonic))
        calls = []
        monkeypatch.setattr(
            site_boards, "_imp_post_json",
            lambda url, body, headers=None, timeout=30.0:
            (calls.append(url), None)[1]
            or (_ for _ in ()).throw(RuntimeError("curl: (28) timeout")))
        monkeypatch.setattr(site_boards, "_CACHE", {})
        with pytest.raises(RuntimeError, match="curl"):
            site_boards.list_board("custom:bytedance")
        assert len(calls) == 2                    # one retry, then loud

    def test_retry_wrapper_resets_session_between_attempts(
            self, monkeypatch):
        import time as _t
        from types import SimpleNamespace
        monkeypatch.setattr(
            site_boards, "time",
            SimpleNamespace(sleep=lambda s: None,
                            monotonic=_t.monotonic))
        seq = []

        def flaky(url, body, headers=None, timeout=30.0):
            seq.append("post")
            if len(seq) == 1:
                raise RuntimeError("curl: (28)")
            return {"ok": True}

        monkeypatch.setattr(site_boards, "_imp_post_json", flaky)
        monkeypatch.setattr(
            site_boards, "_imp_reset_session",
            lambda: seq.append("reset"))
        assert site_boards._imp_post_json_retry(
            "https://x", {}) == {"ok": True}
        assert seq == ["post", "reset", "post"]


# ── S16: lever + workable adapter pins ────────────────────────────────────

def _patch_fetch_list(monkeypatch, jobs):
    """Lever's API serves a TOP-LEVEL LIST (not {jobs: []})."""
    calls = []

    def fake(url, *, params=None, cfg=None, **kw):
        calls.append(url)
        return jobs
    monkeypatch.setattr(site_boards, "fetch_json", fake)
    monkeypatch.setattr(site_boards, "_CACHE", {})
    return calls


class TestLeverAdapter:
    def _board(self, monkeypatch, jobs):
        _patch_fetch_list(monkeypatch, jobs)
        return "ats:lever:weride"

    def test_row_mapping_and_country_code_authoritative(self, monkeypatch):
        # live-pinned 2026-09-24 on weride (17 postings; 9 US / 3 AE /
        # 3 SG / 2 CN) — the structured 'country' code is the verdict
        us = {"id": "aaa-1", "text": "Application Engineer", "country": "US",
              "hostedUrl": "https://jobs.lever.co/weride/aaa-1",
              "createdAt": 1760000000000, "workplaceType": "onsite",
              "categories": {"commitment": "Full-time",
                             "location": "San Jose, CA",
                             "team": "Software Engineering",
                             "allLocations": ["San Jose, CA"]}}
        dubai = dict(us, id="bbb-2", country="AE",
                     text="Data Annotator",
                     categories={"commitment": "Full-time",
                                 "location": "Dubai", "team": "Data"})
        rows, meta = site_boards.list_board(self._board(monkeypatch, [us, dubai]),
                                            country="United States")
        assert set(rows) == {"aaa-1"}            # AE dropped via the code
        r = rows["aaa-1"]
        assert r["title"] == "Application Engineer"
        assert r["timeType"] == "Full time"      # _TT-normalized
        assert r["countries"] == ["United States"]   # code → full name
        assert r["locationsText"] == "San Jose, CA"
        assert r["departments"] == ["Software Engineering"]
        assert r["remoteType"] == "onsite"
        assert meta["country_client"] is False
        assert meta["client_filtered"] == 1
        assert meta["ats"] == "lever"

    def test_time_type_filter_drops_contract(self, monkeypatch):
        us = {"id": "ccc-3", "text": "Talent Acquisition (Contractor)",
              "country": "US", "createdAt": 1760000000000,
              "categories": {"commitment": "Contract",
                             "location": "San Jose, CA"}}
        rows, meta = site_boards.list_board(self._board(monkeypatch, [us]),
                                            country="United States",
                                            time_type="Full time")
        assert rows == {} and meta["client_filtered"] == 1

    def test_created_at_epoch_ms_to_label_and_iso(self, monkeypatch):
        from datetime import datetime, timezone, date
        ts = int(datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
                 .timestamp() * 1000)
        us = {"id": "ddd-4", "text": "X", "country": "US",
              "createdAt": ts,
              "categories": {"commitment": "Full-time",
                             "location": "San Jose, CA"}}
        rows, _ = site_boards.list_board(self._board(monkeypatch, [us]))
        r = rows["ddd-4"]
        days = (date.today() - date(2026, 9, 20)).days
        assert r["postedOn"] == f"Posted {days} Days Ago"
        assert r["firstPublishedIso"].startswith("2026-09-20")

    def test_detail_roundtrip_id_key(self, monkeypatch):
        us = {"id": "eee-5", "text": "Perception Engineer", "country": "US",
              "hostedUrl": "https://jobs.lever.co/weride/eee-5",
              "createdAt": 1760000000000,
              "descriptionBody": "<p>AV perception</p>",
              "categories": {"commitment": "Full-time",
                             "location": "San Jose, CA",
                             "allLocations": ["San Jose, CA"]}}
        self._board(monkeypatch, [us])
        det = site_boards.detail_payload("ats:lever:weride", "/eee-5")
        info = det["jobPostingInfo"]
        assert info["title"] == "Perception Engineer"
        assert info["jobReqId"] == "eee-5"
        assert info["country"]["descriptor"] == "United States"
        assert info["externalUrl"].endswith("/weride/eee-5")
        assert "perception" in info["jobDescription"]
        assert det["hiringOrganization"]["name"] == "weride"

    def test_missing_code_location_fallback(self, monkeypatch):
        # no structured code → location last-segment fallback (never
        # override an authoritative mismatch, but absent = fall through)
        us = {"id": "fff-6", "text": "X", "country": None,
              "createdAt": 1760000000000,
              "categories": {"commitment": "Full-time",
                             "location": "San Jose, United States"}}
        foreign = dict(us, id="ggg-7",
                       categories={"commitment": "Full-time",
                                   "location": "Dubai, United Arab Emirates"})
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [us, foreign]),
            country="United States")
        assert set(rows) == {"fff-6"}
        assert meta["client_filtered"] == 1


class TestWorkableAdapter:
    def _board(self, monkeypatch, jobs):
        _patch_fetch(monkeypatch, jobs)
        return "ats:workable:tp-link-usa-corp"

    def test_row_mapping_and_country(self, monkeypatch):
        # live-pinned 2026-09-24 on tp-link-usa-corp (86 jobs; 80 Irvine
        # + 6 single-city satellites; country = full names)
        us = {"shortcode": "F943A617EC",
              "title": "2026 Early Career Embedded Software Engineer",
              "country": "United States", "city": "Irvine",
              "state": "California", "employment_type": "Full-time",
              "telecommuting": False, "published_on": "2026-05-12",
              "url": "https://apply.workable.com/j/F943A617EC",
              "department": "R&D - Product Engineering",
              "description": "<p>embedded</p>", "locations": []}
        foreign = dict(us, shortcode="XX1111", country="Germany",
                       city="Berlin", state="")
        remote = dict(us, shortcode="RR2222", telecommuting=True)
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [us, foreign, remote]),
            country="United States")
        assert set(rows) == {"F943A617EC", "RR2222"}
        r = rows["F943A617EC"]
        assert r["timeType"] == "Full time"
        assert r["locationsText"] == "Irvine, California"
        assert r["remoteType"] == ""
        assert rows["RR2222"]["remoteType"] == "Remote"
        assert rows["RR2222"]["locationsText"].endswith("(Remote)")
        assert r["departments"][0] == "R&D - Product Engineering"
        assert r["postedOn"].startswith("Posted ")
        assert r["firstPublishedIso"] == "2026-05-12"
        assert meta["country_client"] is False
        assert meta["client_filtered"] == 1
        assert meta["ats"] == "workable"

    def test_blank_employment_type_passes_ft_filter(self, monkeypatch):
        # live-observed: 9/86 tp-link rows carry employment_type "" —
        # honest blank passes (the greenhouse no-field convention)
        blank = {"shortcode": "BB1", "title": "T", "country": "United States",
                 "city": "Irvine", "state": "California",
                 "employment_type": "", "telecommuting": False,
                 "published_on": "2026-05-01", "locations": []}
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [blank]),
            country="United States", time_type="Full time")
        assert set(rows) == {"BB1"} and meta["client_filtered"] == 0

    def test_detail_roundtrip_shortcode_key(self, monkeypatch):
        us = {"shortcode": "F943A617EC", "title": "Antenna Engineer",
              "country": "United States", "city": "Irvine",
              "state": "California", "employment_type": "Full-time",
              "telecommuting": False, "published_on": "2026-05-12",
              "url": "https://apply.workable.com/j/F943A617EC",
              "description": "<p>antennas</p>",
              "locations": [{"city": "Boston", "state": "Massachusetts",
                             "country": "United States"}]}
        self._board(monkeypatch, [us])
        det = site_boards.detail_payload(
            "ats:workable:tp-link-usa-corp", "/j/F943A617EC")
        info = det["jobPostingInfo"]
        assert info["jobReqId"] == "F943A617EC"
        assert info["country"]["descriptor"] == "United States"
        assert info["additionalLocations"] == ["Boston, Massachusetts"]
        assert "antennas" in info["jobDescription"]

    def test_multi_site_locations_joined(self, monkeypatch):
        us = {"shortcode": "MS1", "title": "T", "country": "United States",
              "city": "Irvine", "state": "California",
              "employment_type": "Full-time", "telecommuting": False,
              "published_on": "2026-05-01",
              "locations": [{"city": "Irvine", "state": "California"},
                            {"city": "Boston", "state": "Massachusetts"}]}
        rows, _ = site_boards.list_board(self._board(monkeypatch, [us]))
        assert rows["MS1"]["locationsText"] == (
            "Irvine, California | Boston, Massachusetts")


class TestLiVariantsSplitHeuristic:
    """S16: --li-variants values may CONTAIN commas (legal-name card
    strings). Tight-comma convention + space-rejoin disambiguates."""

    _MOD = None

    @classmethod
    def _split(cls, raw):
        if cls._MOD is None:
            import importlib.util
            p = (Path(__file__).resolve().parent.parent.parent
                 / "scripts" / "board_dump.py")
            spec = importlib.util.spec_from_file_location(
                "board_dump_mod", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            cls._MOD = mod
        return cls._MOD._split_variants(raw)

    def test_plain_list_unchanged(self):
        assert self._split("SHEIN,SHEIN U.S.") == ["SHEIN", "SHEIN U.S."]
        assert self._split("BYD,BYD North America") == [
            "BYD", "BYD North America"]

    def test_comma_containing_variant_rejoins(self):
        assert self._split(
            "GE Appliances,GE Appliances, a Haier company") == [
            "GE Appliances", "GE Appliances, a Haier company"]
        assert self._split("Baidu, Inc.") == ["Baidu, Inc."]

    def test_empty_and_whitespace(self):
        assert self._split("") == []
        assert self._split("  ") == []
        assert self._split("A,,B") == ["A", "B"]


class TestFeishuHireAdapter:
    """S17 pins — live-pinned 2026-09-25 on minimax (vrfi1sk8a0, 185
    posts / 15 US rows) + shengshu (106 / 1 US). The feishu portal API:
    csrf-token POST + body-offset search; details per-id GET."""

    def _board(self, monkeypatch, posts, details=None):
        from jobsearch.sources.site_boards import FeishuHireAdapter
        monkeypatch.setattr(site_boards, "_CACHE", {})

        def fake_rows(self):
            return posts
        monkeypatch.setattr(FeishuHireAdapter, "_fetch_rows", fake_rows)

        def fake_get(self, path):
            rid = path.split("/job/posts/")[1].split("?")[0]
            hit = (details or {}).get(rid)
            if hit is None:
                raise RuntimeError("404")
            return {"data": {"job_post_detail": hit}}
        monkeypatch.setattr(FeishuHireAdapter, "_get_json", fake_get)
        return "ats:feishuhire:vrfi1sk8a0"

    @staticmethod
    def _post(rid, title, cities, tt="Full-time", publish=1789097209208):
        return {"id": rid, "title": title,
                "city_list": [{"en_name": c, "code": "CT_%d" % i}
                              for i, c in enumerate(cities)],
                "recruit_type": {"en_name": tt},
                "publish_time": publish,
                "job_category": {"en_name": "Internet / Electronics / Games"},
                "job_function": {"en_name": "R&D"},
                "description": None, "requirement": None}

    def test_spec_registered_and_parsed(self):
        assert site_boards.is_site_spec("ats:feishuhire:vrfi1sk8a0")
        kind, org = site_boards.parse_site("ats:feishuhire:shengshu")
        assert (kind, org) == ("feishuhire", "shengshu")

    def test_row_mapping_multicity_us_keep(self, monkeypatch):
        # MiniMax dialect: 'Beijing/Shanghai/San Francisco' — the US
        # opening inside a CN-hybrid role IS the target (netflix-class)
        hy = self._post("111", "大模型算法负责人",
                        ["Beijing", "Shanghai", "San Francisco"])
        cn = self._post("222", "云原生架构师", ["Beijing", "Shanghai"])
        spec = self._board(monkeypatch, [hy, cn])
        rows, meta = site_boards.list_board(spec, country="United States")
        assert set(rows) == {"111"}
        r = rows["111"]
        assert r["timeType"] == "Full time"          # _TT-normalized
        assert r["locationsText"] == "Beijing | Shanghai | San Francisco"
        assert r["countries"] == ["United States"]   # ANY-city rule
        assert r["company"] == "MiniMax"              # portal registry
        assert r["ats"] == "feishuhire"
        assert r["postedOn"].startswith("Posted ")
        assert r["firstPublishedIso"].startswith("2026-09")
        assert r["externalPath"].endswith("/111")     # id key contract
        assert r["url"].endswith("/index/position/111/detail")
        assert meta["complete"] is True
        assert meta["client_filtered"] == 1           # the CN row dropped
        assert meta["unresolved_dropped"] == 0

    def test_unknown_city_unresolved_never_false_gone(self, monkeypatch):
        # unmapped city → row dropped LOUDLY + complete=False (a US row
        # may hide behind an unmapped name — B1 contract)
        uk = self._post("333", "Mystery", ["Pleasantville"])
        spec = self._board(monkeypatch, [uk])
        rows, meta = site_boards.list_board(spec, country="United States")
        assert rows == {}
        assert meta["unresolved_dropped"] == 1
        assert meta["complete"] is False

    def test_time_type_filter(self, monkeypatch):
        intern = self._post("444", "AI芯片设计实习生", ["San Francisco"],
                            tt="Internship")
        spec = self._board(monkeypatch, [intern])
        rows, _ = site_boards.list_board(spec, country="United States",
                                         time_type="Full time")
        assert rows == {}

    def test_consultant_passes_unmapped_tt(self, monkeypatch):
        # 'Consultant' is not in _TT — passes through verbatim (honest)
        cons = self._post("555", "10x Team Fellowship", ["San Francisco"],
                          tt="Consultant")
        spec = self._board(monkeypatch, [cons])
        rows, _ = site_boards.list_board(spec, country="United States")
        assert rows["555"]["timeType"] == "Consultant"

    def test_detail_fetch_joins_description_requirement(self, monkeypatch):
        post = self._post("7687134817943472447",
                          "Research Lead, Large Language Models",
                          ["San Francisco"])
        details = {"7687134817943472447": {
            "title": "Research Lead, Large Language Models",
            "description": "About the Role...",
            "requirement": "Requirements...",
            "city_list": [{"en_name": "San Francisco"}],
        }}
        spec = self._board(monkeypatch, [post], details)
        det = site_boards.detail_payload(spec, "/index/position/7687134817943472447")
        info = det["jobPostingInfo"]
        assert info["title"] == "Research Lead, Large Language Models"
        assert info["location"] == "San Francisco"
        assert "About the Role" in info["jobDescription"]
        assert "Requirements" in info["jobDescription"]   # joined
        assert info["country"]["descriptor"] == "United States"
        assert info["jobReqId"] == "7687134817943472447"
        assert info["externalUrl"].endswith(
            "/index/position/7687134817943472447/detail")
        assert det["hiringOrganization"]["name"] == "MiniMax"

    def test_detail_survives_fetch_error(self, monkeypatch):
        # detail-fetch failure ≠ data loss (B1): list row still serves
        post = self._post("999", "X", ["San Francisco"])
        spec = self._board(monkeypatch, [post], details={})
        det = site_boards.detail_payload(spec, "/999")
        assert det is not None
        assert det["jobPostingInfo"]["title"] == "X"
        assert det["jobPostingInfo"]["jobDescription"] == ""

    def test_body_offset_pagination_shape(self, monkeypatch):
        # the S17 discovery: offset AND limit work ONLY in the body —
        # pin the request shape the adapter must keep sending (page
        # size 200, live-verified 2026-09-26)
        from jobsearch.sources.site_boards import FeishuHireAdapter
        sent = []

        def fake_post(self, path, body, token=True):
            sent.append(body)
            n = len(sent)
            page = [TestFeishuHireAdapter._post(str(1000 + i), "J%d" % i,
                                                ["Beijing"])
                    for i in range(200)]
            if n == 1:
                return {"code": 0, "data": {"job_post_list": page[:185],
                                            "count": 400}}
            # page 2: offset 200 → the NEXT 200 (proves body-offset
            # honored; len 200 == _PAGE so pagination continues)
            return {"code": 0, "data": {"job_post_list": page[0:200],
                                        "count": 400}}

        # NOTE: 400-count board → 185+200 = 385 distinct... make page 2
        # return exactly 215 (short page at count)
        def fake_post2(self, path, body, token=True):
            sent.append(body)
            n = len(sent)
            if n == 1:
                page = [TestFeishuHireAdapter._post(str(i), "J%d" % i,
                                                    ["Beijing"])
                        for i in range(200)]
                return {"code": 0, "data": {"job_post_list": page,
                                            "count": 400}}
            page = [TestFeishuHireAdapter._post(str(1000 + i), "J%d" % i,
                                                ["Beijing"])
                    for i in range(200)]
            return {"code": 0, "data": {"job_post_list": page,
                                        "count": 400}}

        monkeypatch.setattr(FeishuHireAdapter, "_post_json", fake_post2)
        monkeypatch.setattr(FeishuHireAdapter, "_token_refresh",
                            lambda self: "tok")
        monkeypatch.setattr(site_boards, "_CACHE", {})
        adapter = FeishuHireAdapter("vrfi1sk8a0", None)
        posts = adapter._fetch_rows()
        assert len(posts) == 400
        assert sent[0] == {"offset": 0, "limit": 200}
        assert sent[1] == {"offset": 200, "limit": 200}

    def test_envelope_code_error_raises(self, monkeypatch):
        # peer-review SEV-1: 200-with-code≠0 mid-pagination must RAISE
        # (silently treating it as an empty page = truncated board with
        # complete=True = the false-gone hole)
        from jobsearch.sources.site_boards import FeishuHireAdapter

        def fake_post(self, path, body, token=True):
            return {"code": 401, "message": "token expired",
                    "data": None}
        monkeypatch.setattr(FeishuHireAdapter, "_post_json", fake_post)
        monkeypatch.setattr(FeishuHireAdapter, "_token_refresh",
                            lambda self: "tok")
        monkeypatch.setattr(site_boards, "_CACHE", {})
        adapter = FeishuHireAdapter("vrfi1sk8a0", None)
        with pytest.raises(RuntimeError, match="envelope code=401"):
            adapter._fetch_rows()
        # nothing cached on the error path
        assert "ats:feishuhire:vrfi1sk8a0" not in site_boards._CACHE

    def test_distinct_ids_vs_count_assertion(self, monkeypatch):
        # peer-review SEV-1 (b): body-offset drift (API ignores our
        # offset → deduped pages) must RAISE, not silently return ~1 page
        from jobsearch.sources.site_boards import FeishuHireAdapter
        same_page = [TestFeishuHireAdapter._post(str(i), "J%d" % i,
                                                 ["Beijing"])
                     for i in range(200)]

        def fake_post(self, path, body, token=True):
            # every page serves the SAME 200 rows (offset ignored)
            return {"code": 0, "data": {"job_post_list": same_page,
                                        "count": 834}}
        monkeypatch.setattr(FeishuHireAdapter, "_post_json", fake_post)
        monkeypatch.setattr(FeishuHireAdapter, "_token_refresh",
                            lambda self: "tok")
        monkeypatch.setattr(site_boards, "_CACHE", {})
        adapter = FeishuHireAdapter("agirobot", None)
        with pytest.raises(RuntimeError, match="pagination drift"):
            adapter._fetch_rows()

    def test_genuine_empty_board_vs_shape_anomaly(self, monkeypatch):
        # count==0 + empty list = legitimate empty board (0 rows,
        # complete=True); count MISSING + empty list = shape anomaly →
        # raise, never cache an ambiguous empty (peer-review SEV-2 #2)
        from jobsearch.sources.site_boards import FeishuHireAdapter
        monkeypatch.setattr(site_boards, "_CACHE", {})

        def mk(resp):
            def fake_post(self, path, body, token=True):
                return resp
            return fake_post

        a = FeishuHireAdapter("moonshot", None)
        monkeypatch.setattr(FeishuHireAdapter, "_post_json",
                            mk({"code": 0, "data": {"job_post_list": [],
                                                     "count": 0}}))
        monkeypatch.setattr(FeishuHireAdapter, "_token_refresh",
                            lambda self: "tok")
        assert a._fetch_rows() == []
        assert site_boards._CACHE["ats:feishuhire:moonshot"][1] == []

        monkeypatch.setattr(site_boards, "_CACHE", {})
        b = FeishuHireAdapter("moonshot", None)
        monkeypatch.setattr(FeishuHireAdapter, "_post_json",
                            mk({"code": 0, "data": None}))
        with pytest.raises(RuntimeError, match="shape anomaly"):
            b._fetch_rows()
        assert "ats:feishuhire:moonshot" not in site_boards._CACHE

    def test_transport_retry_refreshes_token(self, monkeypatch):
        # peer-review SEV-2 #5: one transport error → retry with fresh
        # token + fresh opener, run succeeds
        from jobsearch.sources.site_boards import FeishuHireAdapter
        attempts = []
        refreshed = []

        def fake_post(self, path, body, token=True):
            attempts.append((path, body))
            if len(attempts) == 1:
                raise OSError("connection reset")
            page = [TestFeishuHireAdapter._post(str(i), "J%d" % i,
                                                ["San Francisco"])
                    for i in range(3)]
            return {"code": 0, "data": {"job_post_list": page,
                                        "count": 3}}

        monkeypatch.setattr(FeishuHireAdapter, "_post_json", fake_post)
        monkeypatch.setattr(FeishuHireAdapter, "_token_refresh",
                            lambda self: refreshed.append(1) or "tok2")
        monkeypatch.setattr(site_boards, "_CACHE", {})
        adapter = FeishuHireAdapter("vrfi1sk8a0", None)
        posts = adapter._fetch_rows()
        assert len(posts) == 3
        assert refreshed == [1, 1]  # initial + retry refresh

    def test_empty_city_list_unresolved(self, monkeypatch):
        # peer-review SEV-2 #3: no-city rows are UNRESOLVED (the unified
        # blank-country rule), not quietly non-US — complete=False
        noc = {"id": "888", "title": "Remote Anywhere", "city_list": [],
               "recruit_type": {"en_name": "Full-time"},
               "publish_time": 1789097209208, "job_category": {}}
        spec = self._board(monkeypatch, [noc])
        rows, meta = site_boards.list_board(spec, country="United States")
        assert rows == {}
        assert meta["unresolved_dropped"] == 1
        assert meta["complete"] is False

    def test_outsourced_maps_to_contract(self, monkeypatch):
        # peer-review finding 7: 'Outsourced' is a live-observed
        # recruit_type — maps to Contract (the bytedance third-party
        # associate class)
        out = self._post("777", "Outsourced SDE", ["San Francisco"],
                         tt="Outsourced")
        spec = self._board(monkeypatch, [out])
        rows, _ = site_boards.list_board(spec, country="United States")
        assert rows["777"]["timeType"] == "Contract"

    def test_unmapped_cities_surfaced_in_meta(self, monkeypatch):
        # peer-review finding 10: the operator must SEE the unmapped
        # names without re-probing
        odd = self._post("666", "X", ["Pleasantville"])
        spec = self._board(monkeypatch, [odd])
        _, meta = site_boards.list_board(spec, country="United States")
        assert meta["unmapped_cities"] == ["Pleasantville"]

    def test_detail_code_nonzero_warns_empty_desc(self, monkeypatch):
        # peer-review finding 6: 200-with-code≠0 detail body → loud warn,
        # row survives with empty description (B1)
        post = self._post("555-2", "X", ["San Francisco"])
        from jobsearch.sources.site_boards import FeishuHireAdapter

        def fake_get(self, path):
            return {"code": 404, "message": "not found", "data": None}
        spec = self._board(monkeypatch, [post])
        monkeypatch.setattr(FeishuHireAdapter, "_get_json", fake_get)
        det = site_boards.detail_payload(spec, "/555-2")
        assert det is not None
        assert det["jobPostingInfo"]["jobDescription"] == ""
        assert det["jobPostingInfo"]["title"] == "X"

    def test_cache_contract_second_instance_no_refetch(self, monkeypatch):
        # peer-review finding 13: a second adapter instance (fresh token
        # state, empty cookie jar) on a warm cache makes ZERO POSTs —
        # the dispatch seam constructs a new adapter per detail call
        from jobsearch.sources.site_boards import FeishuHireAdapter
        calls = []
        post = self._post("4321", "Cached", ["San Francisco"])

        def fake_post(self, path, body, token=True):
            calls.append(path)
            return {"code": 0, "data": {"job_post_list": [post],
                                        "count": 1}}
        monkeypatch.setattr(FeishuHireAdapter, "_post_json", fake_post)
        monkeypatch.setattr(FeishuHireAdapter, "_token_refresh",
                            lambda self: "tok")
        monkeypatch.setattr(site_boards, "_CACHE", {})
        first = FeishuHireAdapter("vrfi1sk8a0", None)
        first._fetch_rows()
        n = len(calls)
        second = FeishuHireAdapter("vrfi1sk8a0", None)  # fresh instance
        posts = second._fetch_rows()
        assert len(calls) == n          # zero additional POSTs
        assert len(posts) == 1

    def test_city_map_data_canaries(self):
        from jobsearch.sources.site_boards import _FEISHU_CITY_COUNTRY as M
        assert M["San Jose"] == "United States"
        assert M["Sao Paulo"] == "Brazil"
        assert M["lle-de-France"] == "France"
        assert M["Hong Kong (China)"] == "Hong Kong"
        # ambiguous names stay UNMAPPED by design (loud, not guessed)
        assert "Cambridge" not in M

    def test_no_country_filter_returns_all(self, monkeypatch):
        # regression: list without a country filter keeps every row
        us = self._post("a1", "US Role", ["San Francisco"])
        cn = self._post("a2", "CN Role", ["Beijing"])
        spec = self._board(monkeypatch, [us, cn])
        rows, meta = site_boards.list_board(spec)
        assert set(rows) == {"a1", "a2"}
        assert meta["complete"] is True


class TestXiaohongshuAdapter:
    """S17 pins — live-pinned 2026-09-26 on the 20-US-row board (the
    863-position social board filtered server-side by workplaces=["840"])."""

    def _board(self, monkeypatch, jobs, total=None, responses=None):
        from jobsearch.sources.site_boards import XiaohongshuAdapter
        monkeypatch.setattr(site_boards, "_CACHE", {})
        calls = []

        def fake_post(url, body, headers=None):
            calls.append(body)
            if responses:
                return responses.pop(0)
            return {"success": True, "data": {
                "total": total if total is not None else len(jobs),
                "totalPage": 1, "list": jobs}}
        monkeypatch.setattr(site_boards, "_post_json_urllib", fake_post)
        return "custom:xiaohongshu", calls

    @staticmethod
    def _job(rid, title="iOS Software Engineer - rednote",
             workplace="美国", pub="2026-09-22"):
        return {"positionId": rid, "positionName": title,
                "workplace": workplace, "workplaceIds": "840",
                "publishTime": pub, "recruitStatus": "in_recruitment",
                "duty": "Build the rednote app.",
                "qualification": "Swift + 3y.",
                "jobType": "iOS", "directionName": "技术",
                "subDirectionName": "客户端"}

    def test_spec_registered(self):
        assert site_boards.is_site_spec("custom:xiaohongshu")
        kind, org = site_boards.parse_site("custom:xiaohongshu")
        assert (kind, org) == ("xiaohongshu", "")

    def test_row_mapping_and_server_side_us_filter(self, monkeypatch):
        us = self._job(19557)
        multi = self._job(1, "海外TnS政策专家",
                          workplace="美国，新加坡，上海市，北京市")
        spec, calls = self._board(monkeypatch, [us, multi])
        rows, meta = site_boards.list_board(spec, country="United States")
        assert set(rows) == {"19557", "1"}
        # the ANY-match multi-site row IS a US row (server filtered)
        assert rows["1"]["countries"] == ["United States"]
        r = rows["19557"]
        assert r["title"] == "iOS Software Engineer - rednote"
        assert r["locationsText"] == "美国"
        assert r["timeType"] == ""          # honest blank (no field)
        assert r["ats"] == "xiaohongshu"
        assert r["postedOn"].startswith("Posted ")
        assert "rednote app" in r["description"]
        assert "Swift" in r["description"]
        assert r["url"].endswith("/social/position/19557")
        assert meta["country_client"] is False
        assert meta["complete"] is True
        # the workplaces param is the SERVER-side filter
        assert calls[0]["workplaces"] == ["840"]
        assert calls[0]["recruitType"] == "social"

    def test_non_us_country_refused(self, monkeypatch):
        spec, _ = self._board(monkeypatch, [])
        with pytest.raises(RuntimeError, match="refusing to guess"):
            site_boards.list_board(spec, country="Germany")

    def test_error_envelope_raises(self, monkeypatch):
        # success != true must NEVER serve as a board (the false-gone
        # guard — same class as the feishuhire envelope-code check)
        from jobsearch.sources.site_boards import XiaohongshuAdapter
        monkeypatch.setattr(site_boards, "_CACHE", {})

        def fake_post(url, body, headers=None):
            return {"success": False, "errorCode": 999,
                    "errorMsg": "招聘类型参数异常", "data": None}
        monkeypatch.setattr(site_boards, "_post_json_urllib", fake_post)
        with pytest.raises(RuntimeError, match="success=False"):
            XiaohongshuAdapter("", None)._fetch_pages(["840"])

    def test_pagination_drift_refused(self, monkeypatch):
        # total=30 but only page 1 ever serves 30 rows on BOTH pages →
        # 60 collected vs 30 distinct... construct: same page served
        # twice → duplicate ids must raise, not silently return
        from jobsearch.sources.site_boards import XiaohongshuAdapter
        page = [self._job(i) for i in range(30)]

        def fake_post(url, body, headers=None):
            return {"success": True, "data": {"total": 35,
                                              "totalPage": 2,
                                              "list": page}}
        monkeypatch.setattr(site_boards, "_CACHE", {})
        monkeypatch.setattr(site_boards, "_post_json_urllib", fake_post)
        # 30 rows/page < 35 total → second page serves the SAME 30 rows
        # → 60 collected, 30 distinct, total 35 → drift → raise
        with pytest.raises(RuntimeError, match="drift|collected"):
            XiaohongshuAdapter("", None)._fetch_pages(["840"])

    def test_detail_from_list_row(self, monkeypatch):
        us = self._job(19557, workplace="美国，新加坡")
        spec, _ = self._board(monkeypatch, [us])
        det = site_boards.detail_payload(spec, "/social/position/19557",
                                         country="United States")
        info = det["jobPostingInfo"]
        assert info["location"] == "美国"
        assert info["additionalLocations"] == ["新加坡"]
        assert info["country"]["descriptor"] == "United States"
        assert "rednote app" in info["jobDescription"]
        assert info["jobReqId"] == "19557"

    def test_genuine_zero_board(self, monkeypatch):
        from jobsearch.sources.site_boards import XiaohongshuAdapter
        monkeypatch.setattr(site_boards, "_CACHE", {})

        def fake_post(url, body, headers=None):
            return {"success": True, "data": {"total": 0,
                                              "totalPage": 0, "list": []}}
        monkeypatch.setattr(site_boards, "_post_json_urllib", fake_post)
        assert XiaohongshuAdapter("", None)._fetch_pages(["840"]) == []


class TestS17Round2Pins:
    """Round-2 peer-review amendments (F1 time-refusal, F2 mixed-city
    keep, F4 detail refusal, detail recompute, cache-key separation)."""

    # ── feishuhire F2: mixed US + unmapped cities ────────────────────

    def test_feishuhire_mixed_us_unmapped_kept(self, monkeypatch):
        # [San Francisco, Pleasantville] → PROVABLY US: kept, sibling
        # surfaced (round-2 F2 — the alibaba precedent)
        mixed = TestFeishuHireAdapter._post(
            "mix1", "US role with odd sibling",
            ["San Francisco", "Pleasantville"])
        spec = TestFeishuHireAdapter._board(None, monkeypatch, [mixed])
        rows, meta = site_boards.list_board(spec, country="United States")
        assert set(rows) == {"mix1"}
        assert rows["mix1"]["countries"] == ["United States"]
        assert meta["unmapped_cities"] == ["Pleasantville"]
        assert meta["unresolved_dropped"] == 0
        assert meta["complete"] is True

    def test_feishuhire_all_unmapped_still_unresolved(self, monkeypatch):
        # [Pleasantville] alone → unresolved (US-ness in doubt)
        odd = TestFeishuHireAdapter._post("odd1", "Mystery", ["Pleasantville"])
        spec = TestFeishuHireAdapter._board(None, monkeypatch, [odd])
        rows, meta = site_boards.list_board(spec, country="United States")
        assert rows == {}
        assert meta["unresolved_dropped"] == 1
        assert meta["complete"] is False

    def test_feishuhire_non_us_mapped_drops(self, monkeypatch):
        cn = TestFeishuHireAdapter._post("cn1", "CN role",
                                         ["Beijing", "London"])
        spec = TestFeishuHireAdapter._board(None, monkeypatch, [cn])
        rows, meta = site_boards.list_board(spec, country="United States")
        assert rows == {}
        assert meta["client_filtered_country"] == 1
        assert meta["unresolved_dropped"] == 0

    # ── xiaohongshu F1: time filter refused ──────────────────────────

    def test_xhs_time_type_filter_refused(self, monkeypatch):
        spec, _ = TestXiaohongshuAdapter._board(None, monkeypatch, [])
        with pytest.raises(RuntimeError, match="unanswerable"):
            site_boards.list_board(spec, country="United States",
                                   time_type="Full time")

    def test_xhs_detail_foreign_country_refused(self, monkeypatch):
        us = TestXiaohongshuAdapter._job(1)
        spec, _ = TestXiaohongshuAdapter._board(None, monkeypatch, [us])
        with pytest.raises(RuntimeError, match="refusing to serve"):
            site_boards.detail_payload(spec, "/social/position/1",
                                       country="Germany")

    # ── feishuhire detail recompute (round-1 amendment, round-2 pin) ─

    def test_feishuhire_detail_cities_win_recompute(self, monkeypatch):
        # search says Beijing-only; detail says San Francisco → the
        # payload's country must follow the DETAIL cities (consistency
        # by construction)
        post = TestFeishuHireAdapter._post("rc1", "Relocated role",
                                           ["Beijing"])
        details = {"rc1": {
            "title": "Relocated role",
            "description": "D", "requirement": "R",
            "city_list": [{"en_name": "San Francisco"}],
        }}
        spec = TestFeishuHireAdapter._board(None, monkeypatch, [post], details)
        det = site_boards.detail_payload(spec, "/rc1")
        info = det["jobPostingInfo"]
        assert info["location"] == "San Francisco"
        assert info["country"]["descriptor"] == "United States"

    # ── cache-key separation (workplaces in the xhs cache key) ───────

    def test_xhs_cache_key_separates_filters(self, monkeypatch):
        from jobsearch.sources.site_boards import XiaohongshuAdapter
        calls = []
        us_page = [TestXiaohongshuAdapter._job(1, "US role")]
        all_page = [TestXiaohongshuAdapter._job(2, "Any role"),
                    TestXiaohongshuAdapter._job(3, "Other role",
                                                workplace="北京市")]

        def fake_post(url, body, headers=None):
            calls.append(body.get("workplaces"))
            return {"success": True, "data": {
                "total": (1 if body.get("workplaces") else 2),
                "totalPage": 1,
                "list": us_page if body.get("workplaces") else all_page}}
        monkeypatch.setattr(site_boards, "_CACHE", {})
        monkeypatch.setattr(site_boards, "_post_json_urllib", fake_post)
        a = XiaohongshuAdapter("", None)
        assert len(a._fetch_pages(["840"])) == 1
        assert len(a._fetch_pages([])) == 2       # separate cache entry
        assert len(a._fetch_pages(["840"])) == 1  # cached, no new call
        assert len(calls) == 2

    def test_xhs_error_envelope_nothing_cached(self, monkeypatch):
        # round-2 F10: the error path must not pollute the cache
        from jobsearch.sources.site_boards import XiaohongshuAdapter
        monkeypatch.setattr(site_boards, "_CACHE", {})

        def fake_post(url, body, headers=None):
            return {"success": False, "errorCode": 999, "data": None}
        monkeypatch.setattr(site_boards, "_post_json_urllib", fake_post)
        with pytest.raises(RuntimeError, match="success=False"):
            XiaohongshuAdapter("", None)._fetch_pages(["840"])
        assert not any("xiaohongshu" in k
                       for k in site_boards._CACHE)


_S18_CENSUS_DIR = (Path(__file__).resolve().parents[1]
                   / "data" / "ats_seed" / "s18_census")

_PAYLOCITY_GUID = "d527ad39-680d-45fa-9178-38a81898aec2"


def _paylocity_page(jobs, module_title="United Imaging North America",
                    live_marker=True):
    page = {"ModuleTitle": module_title, "ModuleId": 31727,
            "Jobs": jobs, "Departments": ["All Departments"],
            "Locations": ["All Locations", "Remote"]}
    html = ("<html><body><span>Job Opportunities</span>"
            if live_marker else "<html><body>")
    html += "<script>window.pageData = " + json.dumps(page) + ";</script>"
    html += "</body></html>"
    return html


class TestPaylocityAdapter:
    """S18: ats:paylocity:{CompanyId} — server-rendered window.pageData
    board (United Imaging NA live-pinned 2026-09-25, 41 jobs)."""

    @staticmethod
    def _job(jid, title="Product Sales Specialist", loc="West Coast region",
             country="USA", published="2026-09-22T15:54:27-05:00",
             remote=True):
        return {"JobId": jid, "JobTitle": title, "LocationName": loc,
                "ShouldDisplayLocation": True,
                "PublishedDate": published,
                "Description": "Who we are?United Imaging is a leading...",
                "IsRemote": remote, "IndeedRemoteType": "2",
                "HiringDepartment": None,
                "JobLocation": {"LocationId": 4388720, "ModuleId": 31727,
                                "Name": loc, "Country": country,
                                "City": None, "State": None,
                                "Address": None}}

    def _board(self, monkeypatch, jobs, live_marker=True):
        html = _paylocity_page(jobs, live_marker=live_marker)
        monkeypatch.setattr(site_boards, "_CACHE", {})
        monkeypatch.setattr(
            site_boards, "fetch_text",
            lambda url, cfg=None, **kw: html)
        return f"ats:paylocity:{_PAYLOCITY_GUID}"

    def test_spec_registered_and_parsed(self):
        assert site_boards.is_site_spec(
            f"ats:paylocity:{_PAYLOCITY_GUID}")
        kind, org = site_boards.parse_site(
            f"ats:paylocity:{_PAYLOCITY_GUID}")
        assert (kind, org) == ("paylocity", _PAYLOCITY_GUID)

    def test_row_mapping_and_country(self, monkeypatch):
        us = self._job("4463447")
        ca = self._job("4463448", country="Canada", loc="Toronto, ON")
        spec = self._board(monkeypatch, [us, ca])
        rows, meta = site_boards.list_board(spec, country="United States")
        assert set(rows) == {"4463447"}
        r = rows["4463447"]
        assert r["title"] == "Product Sales Specialist"
        assert r["company"] == "United Imaging North America"
        assert r["locationsText"] == "West Coast region"
        assert r["countries"] == ["United States"]     # USA normalized
        assert r["timeType"] == ""                     # honest blank
        assert r["ats"] == "paylocity"
        assert r["postedOn"].startswith("Posted ")
        assert r["firstPublishedIso"].startswith("2026-09-22")
        assert r["url"].endswith("/Recruiting/Jobs/Details/4463447")
        assert r["remoteType"] == "Remote"
        assert meta["complete"] is True
        assert meta["total"] == 2
        assert meta["client_filtered"] == 1            # Canada dropped
        # ashby-class semantics: the listing IS the {country} population
        # (country_client=False) — a True here would force board_dump's
        # detail-based countryfilter phase on an already-classified list
        # (the S18 unitedimaging finish-refusal bug, pinned)
        assert meta["country_client"] is False
        assert meta["ats"] == "paylocity"

    def test_location_name_fallback_to_joblocation(self, monkeypatch):
        j = self._job("1", loc="")
        j["JobLocation"]["Name"] = "West Coast region"
        spec = self._board(monkeypatch, [j])
        rows, _ = site_boards.list_board(spec, country="United States")
        assert rows["1"]["locationsText"] == "West Coast region"

    def test_time_type_filter_refused(self, monkeypatch):
        # the XHS class: no employment-type field → REFUSE, never
        # drop-all-with-complete=True (the one-CLI-default trap)
        spec = self._board(monkeypatch, [self._job("1")])
        with pytest.raises(RuntimeError, match="unanswerable"):
            site_boards.list_board(spec, country="United States",
                                   time_type="Full time")

    def test_b1_no_marker_refused(self, monkeypatch):
        monkeypatch.setattr(site_boards, "_CACHE", {})
        monkeypatch.setattr(
            site_boards, "fetch_text",
            lambda url, cfg=None, **kw: "<html>404-ish page</html>")
        with pytest.raises(RuntimeError, match="no window.pageData"):
            site_boards.list_board(
                f"ats:paylocity:{_PAYLOCITY_GUID}",
                country="United States")

    def test_b1_shape_anomaly_refused(self, monkeypatch):
        # a live board page whose pageData has NO Jobs list = shape
        # anomaly — refuse, never serve an empty complete board
        page = {"ModuleTitle": "X", "Departments": []}
        html = ("<html>Job Opportunities<script>window.pageData = "
                + json.dumps(page) + ";</script></html>")
        monkeypatch.setattr(site_boards, "_CACHE", {})
        monkeypatch.setattr(
            site_boards, "fetch_text",
            lambda url, cfg=None, **kw: html)
        with pytest.raises(RuntimeError, match="shape anomaly"):
            site_boards.list_board(
                f"ats:paylocity:{_PAYLOCITY_GUID}",
                country="United States")

    def test_detail_sections_and_entities(self, monkeypatch):
        spec = self._board(monkeypatch, [self._job("4463447")])
        detail_html = (
            "<html><body>"
            '<span class="job-preview-title left"><span>Product Sales '
            'Specialist - Digital Radiography (DR)</span></span>'
            '<div class="preview-location">Fully Remote              '
            '<span> &bull; </span>  West Coast region</div>'
            '<div class="job-listing-header">Description</div>'
            "<div><p><strong>Who we are?</strong></p>"
            "<p>United Imaging is a leading global medical device "
            "developer.</p></div>"
            '<div class="job-listing-header">Requirements</div>'
            '<div data-bind="html: Job.Requirements"><ul><li>3+ years '
            "of quota-carrying sales experience.</li></ul></div>"
            "</body></html>")
        monkeypatch.setattr(
            site_boards, "fetch_text",
            lambda url, cfg=None, **kw: (detail_html
                                         if "Details" in url
                                         else _paylocity_page(
                                             [self._job("4463447")])))
        p = site_boards.detail_payload(
            spec, "/Recruiting/Jobs/Details/4463447",
            country="United States")
        info = p["jobPostingInfo"]
        assert info["title"] == ("Product Sales Specialist - "
                                 "Digital Radiography (DR)")
        assert info["location"] == "Fully Remote • West Coast region"
        assert "Who we are?" in info["jobDescription"]
        assert "quota-carrying" in info["jobDescription"]
        assert info["timeType"] == ""
        assert info["country"]["descriptor"] == "United States"
        assert info["externalUrl"].endswith(
            "/Recruiting/Jobs/Details/4463447")
        assert p["hiringOrganization"]["name"] == (
            "United Imaging North America")

    def test_detail_unknown_id_none(self, monkeypatch):
        spec = self._board(monkeypatch, [self._job("1")])
        p = site_boards.detail_payload(
            spec, "/Recruiting/Jobs/Details/999")
        assert p is None

    def test_board_html_cached_once(self, monkeypatch):
        calls = []

        def fake_fetch(url, cfg=None, **kw):
            calls.append(url)
            return _paylocity_page([self._job("1")])
        monkeypatch.setattr(site_boards, "_CACHE", {})
        monkeypatch.setattr(site_boards, "fetch_text", fake_fetch)
        spec = f"ats:paylocity:{_PAYLOCITY_GUID}"
        site_boards.list_board(spec, country="United States")
        site_boards.list_board(spec, country="United States")
        assert len(calls) == 1          # one fetch per process

    def test_census_replay_unitedimaging_evidence(self, monkeypatch):
        # the S15 evidence-payload standard: the committed live census
        # payload (41 real US rows) replays through the adapter to the
        # same verdict — any classifier change that shifts the real
        # board's numbers fails HERE first.
        payload = json.loads(
            (_S18_CENSUS_DIR / "unitedimaging_pageJobs.json").read_text(
                encoding="utf-8"))
        assert len(payload) == 41        # evidence integrity
        monkeypatch.setattr(site_boards, "_CACHE", {})
        monkeypatch.setattr(
            site_boards, "fetch_text",
            lambda url, cfg=None, **kw: _paylocity_page(payload))
        rows, meta = site_boards.list_board(
            f"ats:paylocity:{_PAYLOCITY_GUID}", country="United States")
        assert len(rows) == 41
        assert meta["complete"] is True
        assert meta["client_filtered"] == 0
        # live-pinned details (2026-09-25 census):
        # - every row's JobLocation.Country == 'USA'
        # - Seattle R&D rows exist
        seattle = [r for r in rows.values()
                   if "Seattle" in r["locationsText"]]
        assert seattle, "expected the UIHA Seattle (R&D) rows"
        assert all(r["countries"] == ["United States"]
                   for r in rows.values())
        # the newest rows are Sept 2026 (freshness pin — the
        # hoyoverse-smartrecruiters stale-board trap is the counterexample)
        newest = max(r["firstPublishedIso"] for r in rows.values()
                     if r["firstPublishedIso"])
        assert newest >= "2026-09-21"


class TestPostJsonUrllibRetry:
    """S18 (watch run #59): one transport retry on connection-class
    blips; HTTP verdicts and the non-JSON structural guard surface
    immediately (a verdict is not a blip)."""

    def test_blip_retried_then_ok(self, monkeypatch):
        import urllib.request as _u
        calls = []

        def fake_urlopen(req, timeout=30):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("Network is unreachable")  # Errno 101 class
            return _u.request._urlopen(req, timeout=timeout)

        real_open = _u.urlopen

        class _R:
            def __init__(self, raw):
                import io
                self._f = io.BytesIO(raw)

            def read(self):
                return self._f.read()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_open(req, timeout=30):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("[Errno 101] Network is unreachable")
            return _R(b'{"success": true}')
        monkeypatch.setattr(_u, "urlopen", fake_open)
        d = site_boards._post_json_urllib("https://x.example/api", {})
        assert d == {"success": True}
        assert len(calls) == 2

    def test_http_error_not_retried(self, monkeypatch):
        import urllib.error as _ue
        import urllib.request as _u
        calls = []

        class _406(_ue.HTTPError):
            def __init__(self):
                super().__init__("https://x.example", 406, "NA", None, None)

        def fake_open(req, timeout=30):
            calls.append(1)
            raise _406()
        monkeypatch.setattr(_u, "urlopen", fake_open)
        with pytest.raises(_ue.HTTPError):
            site_boards._post_json_urllib("https://x.example/api", {})
        assert len(calls) == 1

    def test_nonjson_guard_not_retried(self, monkeypatch):
        import urllib.request as _u
        calls = []

        class _R:
            def __init__(self, raw):
                import io
                self._f = io.BytesIO(raw)

            def read(self):
                return self._f.read()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_open(req, timeout=30):
            calls.append(1)
            return _R(b"<html>login</html>")
        monkeypatch.setattr(_u, "urlopen", fake_open)
        with pytest.raises(RuntimeError, match="non-JSON"):
            site_boards._post_json_urllib("https://x.example/api", {})
        assert len(calls) == 1
