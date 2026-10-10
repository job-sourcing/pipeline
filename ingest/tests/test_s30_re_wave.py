"""S30 pins — the RE wave: 5 new adapter classes + the teamtailor
geo-scope gate + the 6-board wire roster.

smartrecruiters (structured ISO country + remote/hybrid flags, offset
pagination, the totalFound=0 dead-board artifact), trakstar (SSR HTML
?p= pager + ld+json detail with control chars → strict=False), icims
(SSR HTML ?pr= page-INDEX pager — 'page=' silently ignored — + the
US-XX-City structured country prefix), paycom (sessionJWT mint +
mandatory filtersForQuery sub-keys + 153-char description previews),
a123 (custom CMS join_us rows with full inline JDs), teamtailor
non_cn (the polestar wire).
"""
import importlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from jobsearch.sources import site_boards, workday  # noqa: E402
from jobsearch.config import Config  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


# ── fixtures (live-pinned shapes; sources in the probe evidence) ─────────

def _sr_post(pid, name, city, region, cc, full, released, remote=False,
             hybrid=False, tt_label="Full-time", dept=None):
    return {
        "id": str(pid), "name": name, "uuid": "u" + str(pid),
        "releasedDate": released,
        "location": {"city": city, "region": region, "country": cc,
                     "fullLocation": full, "remote": remote,
                     "hybrid": hybrid},
        "typeOfEmployment": {"label": tt_label},
        "department": {"label": dept} if dept else None,
        "function": None,
    }


class TestSmartRecruiters:
    def _board(self, monkeypatch, posts, total=None):
        from jobsearch.sources.site_boards import SmartRecruitersAdapter
        monkeypatch.setattr(site_boards, "_CACHE", {})
        payload = {"content": posts, "totalFound":
                   total if total is not None else len(posts),
                   "offset": 0, "limit": 50}

        def fake_fetch(url, cfg=None):
            return payload
        monkeypatch.setattr(site_boards, "fetch_json", fake_fetch)
        return "ats:smartrecruiters:TestCo"

    def test_row_contract(self, monkeypatch):
        posts = [
            _sr_post(1, "Senior Clinical Trial Manager", "Cambridge",
                     "MA", "us", "Cambridge, MA, United States",
                     "2026-09-18T20:38:32.345Z", hybrid=True),
            _sr_post(2, "BD上海", "Shanghai", None, "cn",
                     "Shanghai, China", "2026-09-01T00:00:00.000Z"),
            _sr_post(3, "EU Remote Analyst", "Remote", None, "cn",
                     "Remote, China", "2026-09-02T00:00:00.000Z",
                     remote=True),
        ]
        spec = self._board(monkeypatch, posts)
        rows, meta = site_boards.list_board(spec, geo_scope="non_cn")
        assert set(rows) == {"1", "3"}          # CN-sited non-remote out
        r = rows["1"]
        assert r["countries"] == ["US"]          # ISO upper-cased
        assert r["locationsText"] == "Cambridge, MA, United States"
        assert r["timeType"] == "Full time"      # _TT normalized
        assert r["remoteType"] == "hybrid"
        assert r["postedOn"].startswith("Posted ")
        assert r["url"].endswith("/TestCo/1")
        assert meta["geo_scope"] == "non_cn"
        assert meta["client_filtered"] == 1
        assert rows["3"]["remoteType"] == "remote"   # remote kept

    def test_us_watch_country_filter(self, monkeypatch):
        posts = [
            _sr_post(1, "US role", "Cambridge", "MA", "us",
                     "Cambridge, MA, United States", "2026-09-18T00:00:00Z"),
            _sr_post(2, "UK role", "London", None, "gb",
                     "London, United Kingdom", "2026-09-18T00:00:00Z"),
        ]
        spec = self._board(monkeypatch, posts)
        rows, meta = site_boards.list_board(
            spec, country="United States")
        assert set(rows) == {"1"}
        assert meta["client_filtered"] == 1

    def test_dead_board_refuses_nothing_but_reports_zero(self, monkeypatch):
        # the S29 count-artifact lesson: 200 + totalFound=0 = dead board
        spec = self._board(monkeypatch, [], total=0)
        rows, meta = site_boards.list_board(spec, geo_scope="non_cn")
        assert rows == {}
        assert meta["total"] == 0
        assert meta["complete"] is True

    def test_detail_payload_serves_sections(self, monkeypatch):
        posts = [_sr_post(7, "QC II", "Darien", "WI", "us",
                          "Darien, WI, United States",
                          "2026-10-01T00:00:00Z")]
        spec = self._board(monkeypatch, posts)
        detail = {"jobAd": {"sections": {
            "jobDescription": {"text": "<p>the JD</p>"},
            "qualifications": {"text": "<ul><li>BS</li></ul>"}}}}

        def fake_fetch(url, cfg=None):
            if "/postings/7" in url:
                return detail
            return {"content": posts, "totalFound": 1}
        monkeypatch.setattr(site_boards, "fetch_json", fake_fetch)
        d = site_boards.detail_payload(spec, "/7")
        assert d["jobPostingInfo"]["title"] == "QC II"
        assert "the JD" in d["jobPostingInfo"]["jobDescription"]
        assert d["jobPostingInfo"]["country"]["descriptor"] == "US"


TRAKSTAR_LIST = """
<div class="js-card list-item js-careers-page-job-list-item"
     data-href="/jobs/fk0zowq/"><a href="/jobs/fk0zowq/">
<div class="row"><div class="col-md-6"><h3
 class="rb-h3 js-job-list-opening-name"> Project Manager - Data Center </h3>
<div class="rb-text-6 js-job-list-opening-loc">
<span class="meta-job-location-city">Dallas</span>,
<span class="meta-job-location-state">Texas</span>,
<span class="meta-job-location-country">United States</span>
</div></div>
<div class="rb-text-4 js-job-list-opening-meta"><span>Full-time</span>
</div></div></a></div>
<div class="js-card list-item js-careers-page-job-list-item"
     data-href="/jobs/fk0cn9/"><a href="/jobs/fk0cn9/">
<div class="row"><div class="col-md-6"><h3
 class="rb-h3 js-job-list-opening-name"> Shanghai Sales Director </h3>
<div class="rb-text-6 js-job-list-opening-loc">
<span class="meta-job-location-city">Shanghai</span>,
<span class="meta-job-location-country">China</span>
</div></div></div></a></div>
"""

TRAKSTAR_DETAIL = """<html><head><script type="application/ld+json">{
 "@context": "http://schema.org/", "@type": "JobPosting",
 "title": "Project Manager - Data Center",
 "datePosted": "2026-08-26", "employmentType": "FULL_TIME",
 "description": "Line1\nLine2 with\tcontrol chars",
 "jobLocation": {"@type": "Place", "address": {"addressCountry": "US"}}
}</script></head><body></body></html>"""


class TestTrakstar:
    def _board(self, monkeypatch, html):
        from jobsearch.sources.site_boards import TrakstarAdapter
        monkeypatch.setattr(site_boards, "_CACHE", {})
        monkeypatch.setattr(site_boards, "_TEXT_CACHE", {})

        def fake_text(url, spec, cfg):
            return html
        monkeypatch.setattr(site_boards, "_fetch_text_cached", fake_text)
        return "ats:trakstar:midea.hire.trakstar.com"

    def test_list_parse_and_non_cn_gate(self, monkeypatch):
        spec = self._board(monkeypatch, TRAKSTAR_LIST)
        rows, meta = site_boards.list_board(spec, geo_scope="non_cn")
        assert set(rows) == {"fk0zowq"}       # Shanghai row dropped
        r = rows["fk0zowq"]
        assert r["title"] == "Project Manager - Data Center"
        assert r["locationsText"] == ("Dallas, Texas, "
                                      "United States")
        assert r["timeType"] == "Full time"
        assert r["postedOn"] == ""            # honest — list has no date
        assert meta["geo_scope"] == "non_cn"
        assert meta["client_filtered"] == 1

    def test_us_watch_filter_uses_country_span(self, monkeypatch):
        spec = self._board(monkeypatch, TRAKSTAR_LIST)
        rows, _ = site_boards.list_board(
            spec, country="United States")
        assert set(rows) == {"fk0zowq"}

    def test_detail_ldjson_with_control_chars(self, monkeypatch):
        from jobsearch.sources.site_boards import TrakstarAdapter
        spec = self._board(monkeypatch, TRAKSTAR_LIST)

        def route(url, _spec, _cfg):
            return (TRAKSTAR_DETAIL if "/jobs/fk0zowq" in url
                    else TRAKSTAR_LIST)
        monkeypatch.setattr(site_boards, "_fetch_text_cached", route)
        d = site_boards.detail_payload(spec, "/fk0zowq/")
        jpi = d["jobPostingInfo"]
        assert jpi["title"] == "Project Manager - Data Center"
        assert jpi["postedOn"].startswith("Posted ")
        assert "control chars" in jpi["jobDescription"]
        assert jpi["timeType"] == "Full time"   # FULL_TIME → _TT


ICIMS_PAGE = """
<ul class="container-fluid iCIMS_JobsTable">
<li class="iCIMS_JobCardItem"> <div class="row">
<div class="col-xs-6 header left"> <span class="sr-only field-label">Location</span> <span > US-UT-Farmington</span> </div>
<div class="col-xs-6 header right"> <span class="sr-only field-label">ID</span> <span > 2026-3817</span> </div>
<div class="col-xs-12 title"> <a
 href="https://careers-test.icims.com/jobs/3817/part-time-shift-leader/job?in_iframe=1"
 class="iCIMS_Anch" title="3817 - Part-Time Shift Leader">
<span class="sr-only field-label">Title</span> <h3 > Part-Time Shift Leader</h3> </a> </div>
<div class="col-xs-12 additionalFields"><dl class="iCIMS_JobHeaderGroup">
<div class="iCIMS_JobHeaderTag"> <dt class="iCIMS_JobHeaderField">Category</dt>
<dd class="iCIMS_JobHeaderData"><span > Retail Operations</span> </dd> </div>
<div class="iCIMS_JobHeaderTag"> <dt class="iCIMS_JobHeaderField">Position Type</dt>
<dd class="iCIMS_JobHeaderData"><span > Part-Time</span> </dd> </div>
</dl> </div> </div> </li>
<li class="iCIMS_JobCardItem"> <div class="row">
<div class="col-xs-6 header left"> <span class="sr-only field-label">Location</span> <span > CN-Hangzhou</span> </div>
<div class="col-xs-6 header right"> <span class="sr-only field-label">ID</span> <span > 2026-9900</span> </div>
<div class="col-xs-12 title"> <a
 href="https://careers-test.icims.com/jobs/9900/store-manager-cn/job?in_iframe=1"
 class="iCIMS_Anch" title="9900 - Store Manager CN">
<span class="sr-only field-label">Title</span> <h3 > Store Manager CN</h3> </a> </div>
</div> </li>
</ul>
"""

ICIMS_DETAIL = """<html><head><script type="application/ld+json">{
 "validThrough": "2027-10-08T04:00:00.000Z",
 "employmentType": "FULL_TIME", "@type": "JobPosting",
 "title": "Part-Time Shift Leader",
 "datePosted": "2026-10-08T04:00:00.000Z",
 "description": "<h2>Job Summary</h2>\n<p>The Shift Leader assists.</p>",
 "hiringOrganization": {"@type": "Organization", "name": "MINISO USA"},
 "jobLocation": [{"address": {"addressCountry": "US"}}]
}</script></head><body></body></html>"""


class TestICIMS:
    def _board(self, monkeypatch, page_html=None, detail_html=None):
        from jobsearch.sources.site_boards import ICIMSAdapter
        monkeypatch.setattr(site_boards, "_CACHE", {})
        monkeypatch.setattr(site_boards, "_TEXT_CACHE", {})
        page_html = page_html if page_html is not None else ICIMS_PAGE

        def fake_text(url, spec, cfg):
            if "/jobs/3817" in url:
                return (detail_html if detail_html is not None
                        else ICIMS_DETAIL)
            return page_html
        monkeypatch.setattr(site_boards, "_fetch_text_cached", fake_text)
        return "ats:icims:careers-test.icims.com"

    def test_list_parse_and_non_cn_gate(self, monkeypatch):
        spec = self._board(monkeypatch)
        rows, meta = site_boards.list_board(spec, geo_scope="non_cn")
        assert set(rows) == {"3817"}          # CN- prefix row dropped
        r = rows["3817"]
        assert r["title"] == "Part-Time Shift Leader"
        assert r["locationsText"] == "US-UT-Farmington"
        assert r["countries"] == ["US"]        # the ISO prefix — structured
        assert r["timeType"] == "Part time"
        assert r["bulletFields"] == ["3817", "2026-3817"]
        assert r["departments"] == ["Retail Operations"]
        assert meta["geo_scope"] == "non_cn"

    def test_us_watch_country_filter(self, monkeypatch):
        spec = self._board(monkeypatch)
        rows, _ = site_boards.list_board(
            spec, country="United States")
        assert set(rows) == {"3817"}           # US- prefix matches

    def test_detail_ldjson(self, monkeypatch):
        spec = self._board(monkeypatch)
        d = site_boards.detail_payload(spec, "/3817/")
        jpi = d["jobPostingInfo"]
        assert jpi["title"] == "Part-Time Shift Leader"
        assert jpi["postedOn"] == "Posted 1 Days Ago" or \
            jpi["postedOn"].startswith("Posted ")
        assert "Shift Leader assists" in jpi["jobDescription"]
        assert d["hiringOrganization"]["name"] == "MINISO USA"
        assert jpi["country"]["descriptor"] == "US"

    def test_pager_uses_pr_page_index(self, monkeypatch):
        # the S30 lesson: 'page=' is IGNORED; ?pr={N-1} is the pager
        from jobsearch.sources.site_boards import ICIMSAdapter
        monkeypatch.setattr(site_boards, "_TEXT_CACHE", {})
        seen = []

        def fake_text(url, spec, cfg):
            seen.append(url)
            return ICIMS_PAGE
        monkeypatch.setattr(site_boards, "_fetch_text_cached", fake_text)
        a = ICIMSAdapter("careers-test.icims.com", Config())
        a._page(1)
        a._page(2)
        assert len(seen) == 2
        assert "pr=0" in seen[0] and "page=" not in seen[0]
        assert "pr=1" in seen[1] and "page=" not in seen[1]
        assert "searchRelation=keyword_all" in seen[0]


PAYCOM_PORTAL = """<html><script>
var configsFromHost = {"clientcode":"0NU75","sessionJWT":"eyJfake.jwt.sig",
 "atsPortalMantleServiceUrl":\\"https://portal-applicant-tracking.us-cent.paycomonline.net/\\"};
</script></html>"""

PAYCOM_SEARCH = {
    "jobPostingPreviews": [
        {"jobId": "383557", "jobTitle": "Quality Engineer II",
         "positionType": "Full Time", "remoteType": "",
         "locations": ("WI Darien - Darien, WI 53114; "
                       "WI Beloit - Beloit, WI 53511"),
         "description": "Pay Rate: $88,000 - $94,000/year",
         "postedOn": "", "isHotJob": "True"},
        {"jobId": "389900", "jobTitle": "Hangzhou Buyer",
         "positionType": "Full Time", "remoteType": "",
         "locations": "CN Hangzhou - Hangzhou",
         "description": "desc", "postedOn": ""},
    ],
    "jobPostingPreviewsCount": 2,
}


class TestPaycom:
    def _board(self, monkeypatch):
        from jobsearch.sources.site_boards import PaycomAdapter
        monkeypatch.setattr(site_boards, "_CACHE", {})
        monkeypatch.setattr(site_boards, "_TEXT_CACHE", {})
        calls = {}

        def fake_text(url, spec, cfg):
            calls["portal"] = url
            return PAYCOM_PORTAL
        monkeypatch.setattr(site_boards, "_fetch_text_cached", fake_text)

        def fake_post(url, body, headers=None, **kw):
            calls["search"] = (url, body, headers)
            return json.loads(json.dumps(PAYCOM_SEARCH))
        monkeypatch.setattr(site_boards, "_post_json_urllib", fake_post)

        class _NoNet:
            def __init__(self, req, timeout=20):
                raise RuntimeError("no network in tests")
        monkeypatch.setattr(site_boards.urllib.request,
                            "Request", _NoNet)
        return "ats:paycom:us-cent/FAKEKEY0000000000000000000000000", calls

    def test_search_contract_and_non_cn(self, monkeypatch):
        spec, calls = self._board(monkeypatch)
        rows, meta = site_boards.list_board(spec, geo_scope="non_cn")
        assert set(rows) == {"383557"}          # CN locations dropped
        url, body, headers = calls["search"]
        assert url.endswith(
            "api/ats/job-posting-previews/search")
        assert body["filtersForQuery"]["keywordSearchText"] == ""
        assert body["filtersForQuery"]["location"] == ""
        assert body["filtersForQuery"]["filters"] == []
        assert headers["Authorization"].startswith("eyJ")
        assert headers["Locale"] == "en"
        assert headers["Translation-Highlights"] == "0"
        r = rows["383557"]
        assert r["title"] == "Quality Engineer II"
        assert r["timeType"] == "Full time"
        assert "Darien" in r["locationsText"]
        assert meta["geo_scope"] == "non_cn"

    def test_us_watch_state_token_filter(self, monkeypatch):
        spec, _ = self._board(monkeypatch)
        rows, meta = site_boards.list_board(
            spec, country="United States")
        assert set(rows) == {"383557"}
        assert meta["client_filtered"] == 1

    def test_bare_filtersforquery_is_never_sent(self, monkeypatch):
        # the silent-0-jobs trap: sub-keys are ALWAYS present
        spec, calls = self._board(monkeypatch)
        site_boards.list_board(spec, geo_scope="non_cn")
        _, body, _ = calls["search"]
        assert set(body["filtersForQuery"]) >= {
            "keywordSearchText", "location", "sortOption", "filters"}


A123_PAGE = """
<div class="cbox-23 p_loopitem wow fadeInUp" data-wow-delay="0.0s">
<div class="e_container-24 s_layout hover_con">
<div class="cbox-24-0 p_item"><p class="e_text-30 s_title fnt_16"> Field Customer Quality Engineer </p></div>
<div class="cbox-24-1 p_item"><p class="e_text-29 s_title fnt_16"> Engineering </p></div>
<div class="cbox-24-2 p_item"><p class="e_text-28 s_title fnt_16"> Bachelor's degree </p></div>
<div class="cbox-24-3 p_item"><p class="e_text-27 s_title fnt_16"> Novi, Michigan </p></div>
<div class="cbox-24-4 p_item"><p class="e_text-26 s_title fnt_16"> 09/02/2026 </p></div>
<div class="cbox-24-5 p_item"><p class="e_text-50 s_title fnt_16"> View </p></div>
</div><div class="e_container-32 s_layout">
<div class="cbox-32-0 p_item"><p class="e_text-33 s_title fnt_18"> Job overview: </p>
<div class="e_richText-34 s_title clearfix fnt_16"> <p>Warranty support for ESS products.</p> </div>
<p class="e_text-35 s_title fnt_18"> Responsibilities: </p>
<div class="e_richText-36 s_title clearfix fnt_16"> <ul><li>Lead warranty investigations.</li></ul> </div>
</div></div>
<div class="cbox-23 p_loopitem wow fadeInUp" data-wow-delay="0.3s">
<div class="e_container-24 s_layout hover_con">
<div class="cbox-24-0 p_item"><p class="e_text-30 s_title fnt_16"> Quality Engineer </p></div>
<div class="cbox-24-1 p_item"><p class="e_text-29 s_title fnt_16"> Quality&amp;After-sales </p></div>
<div class="cbox-24-3 p_item"><p class="e_text-27 s_title fnt_16"> Hangzhou, China </p></div>
</div></div>
"""


class TestA123:
    def _board(self, monkeypatch, page_html=None):
        from jobsearch.sources.site_boards import A123Adapter
        monkeypatch.setattr(site_boards, "_CACHE", {})
        monkeypatch.setattr(site_boards, "_TEXT_CACHE", {})

        def fake_text(url, spec, cfg):
            return page_html if page_html is not None else A123_PAGE
        monkeypatch.setattr(site_boards, "_fetch_text_cached", fake_text)
        return "custom:a123"

    def test_us_watch_keeps_michigan_drops_china(self, monkeypatch):
        spec = self._board(monkeypatch)
        rows, meta = site_boards.list_board(
            spec, country="United States")
        assert set(rows) == {"field-customer-quality-engineer"}
        r = rows["field-customer-quality-engineer"]
        assert r["locationsText"] == "Novi, Michigan"
        assert r["departments"] == ["Engineering"]
        assert r["postedOn"].startswith("Posted ")
        assert r["company"] == "A123 Systems"
        assert meta["client_filtered"] == 1

    def test_detail_serves_inline_jd(self, monkeypatch):
        spec = self._board(monkeypatch)
        d = site_boards.detail_payload(
            spec, "/field-customer-quality-engineer/")
        jpi = d["jobPostingInfo"]
        assert "Warranty support" in jpi["jobDescription"]
        assert "Lead warranty investigations" in jpi["jobDescription"]
        assert jpi["location"] == "Novi, Michigan"

    def test_no_country_keeps_all(self, monkeypatch):
        spec = self._board(monkeypatch)
        rows, _ = site_boards.list_board(spec)
        assert len(rows) == 2


class TestTeamtailorGeoScope:
    def _board(self, monkeypatch, items):
        from jobsearch.sources.site_boards import TeamtailorAdapter
        monkeypatch.setattr(site_boards, "_CACHE", {})
        feed = {"version": "jsonfeed-1.1", "items": items}
        monkeypatch.setattr(site_boards, "fetch_json",
                            lambda url, cfg=None: feed)
        return "ats:teamtailor:polestar"

    @staticmethod
    def _item(rid, title, cc, locality="Gothenburg", region="",
              published="2026-09-28T00:00:00+02:00"):
        return {"title": title, "url": f"https://x.teamtailor.com/jobs/{rid}",
                "date_published": published,
                "_jobposting": {"identifier": {"value": rid},
                                "jobLocation": [{"address": {
                                    "addressLocality": locality,
                                    "addressRegion": region,
                                    "addressCountry": cc}}]}}

    def test_non_cn_drops_cn_rows_keeps_eu(self, monkeypatch):
        items = [
            self._item(1, "Customs Ops", "SE"),
            self._item(2, "Retail Internship", "DE"),
            self._item(3, "Shanghai Ops", "CN", locality="Shanghai"),
            self._item(4, "CN remote analyst", "CN",
                       locality="Remote, China"),
        ]
        spec = self._board(monkeypatch, items)
        rows, meta = site_boards.list_board(spec, geo_scope="non_cn")
        assert set(rows) == {"1", "2", "4"}    # CN non-remote dropped
        assert rows["4"]["remoteType"] == "remote"
        assert meta["geo_scope"] == "non_cn"

    def test_geo_scope_in_dispatcher_set(self):
        assert "teamtailor" in site_boards._ADAPTER_GEO_SCOPE


class TestS30WireRoster:
    def _cfg(self):
        p = REPO_ROOT / "ingest/data/board_watch/config.json"
        return json.loads(p.read_text(encoding="utf-8"))

    def test_six_new_wires_present(self):
        w = self._cfg()["watches"]
        boards = {x["board"] for x in w}
        for b in ("ats:smartrecruiters:ZaiLabUSLLC1",
                  "ats:trakstar:midea.hire.trakstar.com",
                  "ats:icims:careers-miniso-us.icims.com",
                  "ats:paycom:us-cent/39DCF574C16448FF09ADD3EF809F9EF2",
                  "custom:a123", "ats:teamtailor:polestar"):
            assert b in boards, b
        assert len(w) == 122

    def test_wire_specs_all_parse(self):
        w = self._cfg()["watches"]
        for x in w:
            assert site_boards.is_site_spec(x["board"]) or \
                "|" in x["board"], x["board"]

    def test_registry_and_dispatch_flags(self):
        for k in ("smartrecruiters", "trakstar", "icims", "paycom",
                  "a123"):
            assert k in site_boards._ADAPTERS
            assert site_boards.is_site_spec(
                f"ats:{k}:x") or k == "a123"
        for k in ("smartrecruiters", "trakstar", "icims", "paycom",
                  "teamtailor"):
            assert k in site_boards._ADAPTER_GEO_SCOPE
        for k in ("smartrecruiters", "trakstar", "icims", "paycom"):
            assert site_boards._ADAPTER_ACCEPTS_REMOTE.get(k) is True

    def test_h1b_needles_extended(self):
        p = REPO_ROOT / ".github/workflows/h1b-extract.yml"
        src = p.read_text(encoding="utf-8")
        for lbl in ("zailab_us_fulltime", "midea_us_fulltime",
                    "minisous_us_fulltime", "psi_us_fulltime",
                    "a123_us_fulltime", "polestar_us_fulltime"):
            assert lbl in src, lbl
