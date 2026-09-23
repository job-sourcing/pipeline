"""S13 custom job-site adapter pins — the generalized non-Workday infra.

Contract under test: site_boards maps public one-call board APIs
(greenhouse/ashby) onto the workday list-row + detail-payload shapes so
the WHOLE dump/watch phase chain runs unchanged. All network is
monkeypatched (fetch_json) — these pins hold the MAPPING, the country
classification, the label computation, and the dispatch routing."""
import json
import sys
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
        assert not site_boards.is_site_spec("nvidia|wd5|x")
        assert not site_boards.is_site_spec("")
        assert not site_boards.is_site_spec("ats:lever:x")  # no adapter yet

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
    """Fake the alibaba multi-host sweep. host_rows: {hostkey: [rows]}."""
    session = _FakeResp(200, {"XSRF-TOKEN": "csrf-123"})
    monkeypatch.setattr(site_boards, "_imp_get",
                        lambda url, headers=None, timeout=25.0: session)
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
