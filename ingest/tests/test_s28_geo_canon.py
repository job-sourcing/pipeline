"""S28 pins — D-S28-1 country-column canonicalization at the export
seam.

Live audit found the bundle's country facet carrying: lever's ISO-ish
codes ('Kz' → 9 rows, 'Pk' → 5), blank labels on 22 US rows whose
locations are 'City, ST' (Hisense/bamboohr) or street addresses
(Haidilao/workstream), and GenScript's 'Israel'/'Europe' locations
classifying to blank. The exporter now routes the country column
through _row_country (alias map → classifier, evidence-ordered);
US-scope boards only ever GAIN United States/Remote from
classification. The bundle builder normalizes the same aliases as a
backstop.
"""
import importlib.util
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location(
    "s26_export_refresh", REPO_ROOT / "scripts" / "s26_export_refresh.py")
exporter = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("s26_export_refresh", exporter)
_spec.loader.exec_module(exporter)

_spec2 = importlib.util.spec_from_file_location(
    "build_ui_bundle", REPO_ROOT / "scripts" / "build_ui_bundle.py")
bundle = importlib.util.module_from_spec(_spec2)
sys.modules.setdefault("build_ui_bundle", bundle)
_spec2.loader.exec_module(bundle)


def _rec(country="", locs_text="", locs=None):
    return {"country": country, "locationsText": locs_text,
            "locations": locs or ([locs_text] if locs_text else [])}


class TestRowCountry:
    def test_lever_iso_codes_canonicalized(self):
        f = exporter._row_country
        assert f(_rec("Kz", "Kazakhstan, Astana"), "Kazakhstan, Astana",
                 ["Kazakhstan, Astana"], False) == "Kazakhstan"
        assert f(_rec("Pk", "Pakistan, Islamabad"), "Pakistan, Islamabad",
                 ["Pakistan, Islamabad"], False) == "Pakistan"

    def test_workday_descriptor_alias(self):
        assert exporter._row_country(
            _rec("United States of America", "US, CA, Santa Clara"),
            "US, CA, Santa Clara", ["US, CA, Santa Clara"],
            True) == "United States"
        assert exporter._row_country(
            _rec("united states", "Austin, TX"), "Austin, TX",
            ["Austin, TX"], True) == "United States"

    def test_us_scope_blank_fills_from_state_tokens(self):
        # the Hisense fix: bamboohr rows, no rec country, 'City, ST'
        f = exporter._row_country
        for loc in ("Bentonville, AR", "Suwanee, GA", "Alpharetta, GA"):
            assert f(_rec("", loc), loc, [loc], True) == "United States"
        # the Haidilao fix: workstream street addresses
        assert f(_rec("", "107 E Cermak Rd, Chicago, IL 60616, USA"),
                 "107 E Cermak Rd, Chicago, IL 60616, USA",
                 ["107 E Cermak Rd, Chicago, IL 60616, USA"],
                 True) == "United States"

    def test_us_scope_never_gains_foreign_labels(self):
        f = exporter._row_country
        assert f(_rec("", "Israel"), "Israel", ["Israel"], True) == ""
        assert f(_rec("", "Singapore, Singapore"), "Singapore, Singapore",
                 ["Singapore, Singapore"], True) == ""

    def test_non_cn_classifies_new_keywords(self):
        f = exporter._row_country
        assert f(_rec("", "Israel"), "Israel", ["Israel"], False) == "Israel"
        assert f(_rec("", "Europe"), "Europe", ["Europe"], False) == "Europe"
        assert f(_rec("", "Johannesburg, South Africa"),
                 "Johannesburg, South Africa",
                 ["Johannesburg, South Africa"], False) == "South Africa"

    def test_unknown_full_name_ships_honestly(self):
        assert exporter._row_country(
            _rec("Costa Rica", "San Jose, Costa Rica"),
            "San Jose, Costa Rica", ["San Jose, Costa Rica"],
            False) == "Costa Rica"

    def test_remote_words(self):
        f = exporter._row_country
        assert f(_rec("Remote", "Remote"), "Remote", ["Remote"],
                 True) == "Remote"
        assert f(_rec("", "Remote - Global"), "Remote - Global",
                 ["Remote - Global"], True) == "Remote"


class TestClassifyGeoKeywords:
    def test_s28_new_keywords(self):
        c = exporter._classify_geo
        assert c("Kazakhstan, Astana") == "Kazakhstan"
        assert c("Pakistan, Islamabad") == "Pakistan"
        assert c("Israel") == "Israel"
        assert c("Tel Aviv, Israel") == "Israel"
        assert c("Europe") == "Europe"
        assert c("Istanbul, Turkey") == "Turkey"
        assert c("Johannesburg, South Africa") == "South Africa"
        assert c("Riyadh, Saudi Arabia") == "Saudi Arabia"
        assert c("Auckland, New Zealand") == "New Zealand"


class TestBundleBackstop:
    def test_norm_country_aliases(self):
        n = bundle.norm_country
        assert n("Kz") == "Kazakhstan"
        assert n("kz") == "Kazakhstan"
        assert n("Pk") == "Pakistan"
        assert n("Israel") == "Israel"
        assert n("Europe") == "Europe"
        assert n("united states of america") == "United States"
        assert n("United States of America") == "United States"


class TestRefreshOneCountry:
    """E2E through refresh_one: the CSV country column is canonical."""

    def _setup(self, tmp_path, w_over, state_rows, feed_rows):
        watch = tmp_path / "board_watch"
        workday = tmp_path / "workday"
        watch.mkdir(parents=True)
        workday.mkdir(parents=True)
        (watch / "config.json").write_text(
            json.dumps({"watches": []}), encoding="utf-8")
        with open(watch / "testco_us_fulltime.state.jsonl", "w") as f:
            for r in state_rows:
                f.write(json.dumps(r) + "\n")
        with open(watch / "testco_us_fulltime.newposts.jsonl", "w") as f:
            for r in feed_rows:
                f.write(json.dumps(r) + "\n")
        exporter.WATCH_DIR = watch
        exporter.WORKDAY = workday
        w = {"label": "testco_us_fulltime", "company": "TestCo",
             "country": "United States"}
        w.update(w_over)
        return w

    def test_lever_kz_e2e(self, tmp_path):
        # the binance case: feed carries lever's 'Kz' ISO code
        w = self._setup(
            tmp_path, {"geo_scope": "non_cn"},
            [{"reqId": "kz-1"}],
            [{"reqId": "kz-1", "title": "Compliance", "company": "TestCo",
              "country": "Kz", "locationsText": "Kazakhstan, Astana",
              "locations": ["Kazakhstan, Astana"],
              "postedOn": "Posted 3 Days Ago", "url": "https://x/kz-1",
              "startDate": "2026-09-28", "description": "Do. " * 20,
              "timeType": "Full time", "first_seen": "2026-09-20"}])
        exporter.refresh_one("testco_us_fulltime", w,
                             exporter.date(2026, 10, 7))
        import csv as _csv
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            out = {r["reqId"]: r for r in _csv.DictReader(f)}
        assert out["kz-1"]["country"] == "Kazakhstan"

    def test_us_scope_blank_location_fill_e2e(self, tmp_path):
        # the Hisense case: no rec country, 'Suwanee, GA' location
        w = self._setup(
            tmp_path, {},
            [{"reqId": "hs-1"}],
            [{"reqId": "hs-1", "title": "Trainer", "company": "TestCo",
              "locationsText": "Suwanee, GA", "locations": ["Suwanee, GA"],
              "postedOn": "Posted 3 Days Ago", "url": "https://x/hs-1",
              "startDate": "2026-09-28", "description": "Do. " * 20,
              "timeType": "Full time", "first_seen": "2026-09-20"}])
        exporter.refresh_one("testco_us_fulltime", w,
                             exporter.date(2026, 10, 7))
        import csv as _csv
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            out = {r["reqId"]: r for r in _csv.DictReader(f)}
        assert out["hs-1"]["country"] == "United States"
