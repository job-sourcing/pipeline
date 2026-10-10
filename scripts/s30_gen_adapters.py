#!/usr/bin/env python3
"""S30 RE-wave: generate the 5-adapter insertion block for site_boards.py.

Writes the adapters to a snippet file (kept under scripts/ as the
recoverable artifact), to be inserted before _ADAPTERS registry.
"""
import pathlib

BLOCK = '''
# ── S30 RE wave (5 new classes; contracts live-pinned 2026-10-10 —
#    audit/s30_fetchkit_probes.md + the probe evidence in
#    audit/s30_fetchkit_samples/) ──────────────────────────────────────────

class SmartRecruitersAdapter:
    """ats:smartrecruiters:{org} — api.smartrecruiters.com/v1/companies/
    {org}/postings (public, no auth; the S29 count-artifact lesson: a 200
    with totalFound=0 means DEAD, titles are the only identity proof).

    Field map (live-pinned 2026-10-10 on ZaiLabUSLLC1, 14 postings):
      reqId            id (numeric string)
      title            name
      url              https://careers.smartrecruiters.com/{org}/{id}
      locationsText    location.fullLocation ('Cambridge, MA, United States')
      country          location.country — STRUCTURED ISO alpha-2 lowercase
                       ('us') — AUTHORITATIVE (the S13 lesson: never
                       classify from free text when a structured field
                       exists); upper-cased here.
      timeType         typeOfEmployment.label ('Full-time' → _TT)
      departments      department.label + function.label
      postedOn         releasedDate (EXACT ISO)
      remoteType       location.remote → 'remote'; location.hybrid →
                       'hybrid' (workplaceTypes is usually null)
    geo_scope non_cn: country != CN/CHN OR location.remote. Detail:
    jobAd.sections.{jobDescription,qualifications}.text (HTML kept).
    """

    KIND = "smartrecruiters"
    _BASE = "https://api.smartrecruiters.com/v1/companies"
    _PAGE = 50
    _MAX_PAGES = 40          # 2,000 postings — the D-S28-1 board-size guard

    def __init__(self, org: str, cfg: Config):
        self.org = org
        self.cfg = cfg

    def _jobs(self) -> list[dict]:
        key = f"smartrecruiters:{self.org}"
        hit = _CACHE.get(key)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL:
            return hit[1]
        jobs: list[dict] = []
        offset = 0
        total = None
        for _page in range(self._MAX_PAGES):
            url = (f"{self._BASE}/{self.org}/postings"
                   f"?limit={self._PAGE}&offset={offset}")
            d = fetch_json(url, cfg=self.cfg)
            if not isinstance(d, dict) or not isinstance(
                    d.get("content"), list):
                raise RuntimeError(
                    f"smartrecruiters:{self.org}: {url}: unexpected "
                    f"payload (no content list)")
            batch = d["content"]
            jobs.extend(batch)
            total = d.get("totalFound") or total
            offset += self._PAGE
            if total is not None and offset >= int(total):
                break
            if not batch:
                break
        _CACHE[key] = (time.monotonic(), jobs)
        return jobs

    def _country(self, job: dict) -> str:
        c = str((job.get("location") or {}).get("country")
                or "").strip().upper()
        return c

    def _time_type(self, job: dict) -> str:
        v = str((job.get("typeOfEmployment") or {}).get("label")
                or "").strip()
        return _TT.get(v.lower(), v) if v else ""

    @staticmethod
    def _remote_flag(job: dict) -> bool:
        loc = job.get("location") or {}
        return bool(loc.get("remote")) or bool(loc.get("hybrid"))

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list",
                   include_remote: bool = False,
                   geo_scope: Optional[str] = None,
                   ) -> tuple[dict[str, dict], dict]:
        jobs = self._jobs()
        rows: dict[str, dict] = {}
        dropped_country = dropped_tt = kept_remote = 0
        for job in jobs:
            rid = str(job.get("id") or "")
            if not rid or rid in rows:
                continue
            tt = self._time_type(job)
            if time_type and tt and tt.lower() != time_type.lower():
                dropped_tt += 1
                continue
            c = self._country(job)
            loc = job.get("location") or {}
            loc_text = str(loc.get("fullLocation") or "")
            is_remote = self._remote_flag(job)
            if geo_scope == "non_cn":
                # D-S27-2: structured ISO code authoritative; remote kept
                if c:
                    cn_sited = c in ("CN", "CHN") or \\
                        workday.country_str_matches(c, "china")
                else:
                    cn_sited = _text_is_cn_sited(loc_text)
                if cn_sited and not is_remote:
                    dropped_country += 1
                    continue
            elif country:
                match = (workday.country_str_matches(c, country)
                         if c else
                         _plain_loc_in_country(loc_text, country))
                if not match:
                    if include_remote and is_remote:
                        kept_remote += 1
                    else:
                        dropped_country += 1
                        continue
            label, iso = _posted_label(job.get("releasedDate"))
            deps = [str(x.get("label") or "") for x in
                    (job.get("department"), job.get("function"))
                    if isinstance(x, dict) and x.get("label")]
            rt = ""
            if loc.get("remote"):
                rt = "remote"
            elif loc.get("hybrid"):
                rt = "hybrid"
            rows[rid] = {
                "reqId": rid,
                "title": job.get("name") or "",
                "company": self.org,
                "url": f"https://careers.smartrecruiters.com/"
                       f"{self.org}/{rid}",
                "externalPath": f"/{rid}",
                "locationsText": loc_text,
                "postedOn": label,
                "timeType": tt,
                "bulletFields": [rid],
                "ats": "smartrecruiters",
                "firstPublishedIso": iso,
                "departments": deps,
                "countries": [c] if c else [],
                "remoteType": rt,
            }
        meta = {
            "complete": True, "total": len(jobs),
            "pages": max(1, (len(jobs) + self._PAGE - 1) // self._PAGE),
            "country_client": False,
            "client_filtered": dropped_country + dropped_tt,
            "kept_remote": kept_remote,
            "geo_scope": geo_scope or "",
            "ats": "smartrecruiters",
        }
        print(f"[{progress_label}] smartrecruiters:{self.org}: "
              f"{len(rows)} rows"
              + (f" ({dropped_country} non-{country} + {dropped_tt} "
                 f"non-{time_type} dropped client-side)"
                 if (country or time_type) else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        job = next((x for x in self._jobs()
                    if str(x.get("id")) == rid), None)
        if job is None:
            return None
        d = fetch_json(f"{self._BASE}/{self.org}/postings/{rid}",
                       cfg=self.cfg)
        if not isinstance(d, dict):
            return None
        sections = ((d.get("jobAd") or {}).get("sections") or {})
        desc = " ".join(
            str((sections.get(k) or {}).get("text") or "")
            for k in ("jobDescription", "qualifications",
                      "aboutTheCompany")) if sections else ""
        if not desc:
            desc = str(job.get("jobAdSnippet") or "")
        label, iso = _posted_label(job.get("releasedDate"))
        c = self._country(job)
        loc = job.get("location") or {}
        tt = self._time_type(job)
        if time_type and tt and tt.lower() != time_type.lower():
            return None
        return {
            "jobPostingInfo": {
                "title": job.get("name") or "",
                "location": str(loc.get("fullLocation") or ""),
                "additionalLocations": [],
                "jobDescription": desc,
                "timeType": tt,
                "startDate": "",
                "externalUrl": (f"https://careers.smartrecruiters.com/"
                                f"{self.org}/{rid}"),
                "jobReqId": rid,
                "postedOn": label,
                "country": {"descriptor": c} if c else None,
            },
            "hiringOrganization": {"name": self.org},
            "similarJobs": [],
            "firstPublishedIso": iso,
        }


class TrakstarAdapter:
    """ats:trakstar:{host} — midea.hire.trakstar.com (Trakstar Hire,
    ex-Recruiterbox). Server-rendered HTML list + schema.org
    JobPosting ld+json on detail pages.

    Field map (live-pinned 2026-10-10 on midea, 49 postings):
      reqId            the /jobs/{rid}/ slug ('fk0zowq')
      title            h3.js-job-list-opening-name
      locationsText    meta-job-location-{city,state,country} spans →
                       'Dallas, Texas, United States'
      country          the country span — phrase text ('United States')
      timeType         js-job-list-opening-meta span ('Full-time' → _TT)
      postedOn         NOT SERVED on the list — honest ''; the detail
                       ld+json datePosted is EXACT ISO
    geo_scope non_cn: country-span/CJK classification + remote token.
    """

    KIND = "trakstar"
    _ROW_RE = re.compile(
        r'js-careers-page-job-list-item"[^>]*?data-href="(/jobs/([^/"]+)/)"'
        r'(.*?)(?=js-careers-page-job-list-item|\\Z)', re.S)

    def __init__(self, org: str, cfg: Config):
        self.org = org.rstrip("/")
        self.cfg = cfg

    def _html(self) -> str:
        return _fetch_text_cached(
            f"https://{self.org}/", f"ats:trakstar:{self.org}", self.cfg)

    def _rows(self) -> list[dict]:
        key = f"trakstar:{self.org}"
        hit = _CACHE.get(key)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL:
            return hit[1]
        html = self._html()
        rows: list[dict] = []
        seen: set[str] = set()
        for m in self._ROW_RE.finditer(html):
            href, rid, frag = m.groups()
            if rid in seen:
                continue
            t = re.search(
                r'js-job-list-opening-name[^>]*>([^<]+)<', frag)
            city = re.search(
                r'meta-job-location-city[^>]*>\\s*([^<]+?)\\s*<', frag)
            state = re.search(
                r'meta-job-location-state[^>]*>\\s*([^<]+?)\\s*<', frag)
            ctry = re.search(
                r'meta-job-location-country[^>]*>\\s*([^<]+?)\\s*<', frag)
            meta = re.search(
                r'js-job-list-opening-meta[^>]*>\\s*<span>\\s*([^<]+?)'
                r'\\s*</span>', frag)
            title = unescape((t.group(1) if t else "")).strip()
            if not title:
                continue
            seen.add(rid)
            loc = ", ".join(x for x in (
                (city.group(1) if city else "").strip(),
                (state.group(1) if state else "").strip(),
                (ctry.group(1) if ctry else "").strip()) if x)
            rows.append({
                "rid": rid,
                "href": f"https://{self.org}{href}",
                "title": title,
                "loc": loc,
                "country": (ctry.group(1) if ctry else "").strip(),
                "tt": _TT.get(
                    (meta.group(1) if meta else "").strip().lower(),
                    (meta.group(1) if meta else "").strip()),
            })
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list",
                   include_remote: bool = False,
                   geo_scope: Optional[str] = None,
                   ) -> tuple[dict[str, dict], dict]:
        all_rows = self._rows()
        rows: dict[str, dict] = {}
        dropped_country = dropped_tt = kept_remote = 0
        for j in all_rows:
            if time_type and j["tt"] and \\
                    j["tt"].lower() != time_type.lower():
                dropped_tt += 1
                continue
            loc = j["loc"]
            is_remote = "remote" in (loc + " " + j["title"]).lower()
            if geo_scope == "non_cn":
                if _text_is_cn_sited(loc) and not is_remote:
                    dropped_country += 1
                    continue
            elif country:
                match = (workday.country_str_matches(j["country"], country)
                         if j["country"] else
                         _plain_loc_in_country(loc, country))
                if not match:
                    if include_remote and is_remote:
                        kept_remote += 1
                    else:
                        dropped_country += 1
                        continue
            rows[j["rid"]] = {
                "reqId": j["rid"],
                "title": j["title"],
                "company": self.org.split(".")[0],
                "url": j["href"],
                "externalPath": f"/{j['rid']}",
                "locationsText": loc,
                "postedOn": "",
                "timeType": j["tt"],
                "bulletFields": [j["rid"]],
                "ats": "trakstar",
                "countries": [j["country"]] if j["country"] else [],
                "remoteType": "remote" if is_remote else "",
            }
        meta = {
            "complete": True, "total": len(all_rows), "pages": 1,
            "country_client": False,
            "client_filtered": dropped_country + dropped_tt,
            "kept_remote": kept_remote,
            "geo_scope": geo_scope or "",
            "ats": "trakstar",
        }
        print(f"[{progress_label}] trakstar:{self.org}: {len(rows)} rows"
              + (f" ({dropped_country} non-{country} + {dropped_tt} "
                 f"non-{time_type} dropped client-side)"
                 if (country or time_type) else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        j = next((x for x in self._rows() if x["rid"] == rid), None)
        if j is None:
            return None
        html = _fetch_text_cached(
            j["href"], f"ats:trakstar:{self.org}:detail:{rid}", self.cfg)
        jp = None
        for m in re.finditer(
                r'<script type="application/ld\\+json">\\s*(.*?)'
                r'</script>', html, re.S):
            try:
                d = json.loads(m.group(1))
            except Exception:
                continue
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                jp = d
                break
        desc = ""
        date_posted = ""
        employment = ""
        if jp:
            desc = str(jp.get("description") or "")
            date_posted = str(jp.get("datePosted") or "")
            employment = str(jp.get("employmentType") or "")
        label, iso = _posted_label(date_posted or None)
        tt = _TT.get(employment.lower().replace("_", "-"),
                     employment.replace("_", " ")) if employment else j["tt"]
        if time_type and tt and tt.lower() != time_type.lower():
            return None
        return {
            "jobPostingInfo": {
                "title": (jp or {}).get("title") or j["title"],
                "location": j["loc"] or "See posting",
                "additionalLocations": [],
                "jobDescription": desc,
                "timeType": tt,
                "startDate": "",
                "externalUrl": j["href"],
                "jobReqId": rid,
                "postedOn": label,
                "country": {"descriptor": j["country"]}
                           if j["country"] else None,
            },
            "hiringOrganization": {"name": self.org.split(".")[0]},
            "similarJobs": [],
            "firstPublishedIso": iso,
        }


class ICIMSAdapter:
    """ats:icims:{host} — careers-{company}.icims.com jobs/search SSR
    HTML (server-rendered table, 50 rows/page, 'Page 1 of N' pager).

    Field map (live-pinned 2026-10-10 on careers-miniso-us.icims.com,
    ~700 postings over 14 pages):
      reqId            the /jobs/{id}/ URL id ('3817'; the row's own ID
                       span '2026-3817' is a different key — kept as a
                       bulletField)
      title            a.iCIMS_Anchor title attr / h3
      locationsText    'US-UT-Farmington' (the sr-only Location span)
      country          the ISO-alpha-2 PREFIX of that code ('US') —
                       STRUCTURED, authoritative; unclassified rows
                       fail-open with '' (never guessed)
      timeType         the Position Type JobHeader span ('Part-Time')
      postedOn         NOT SERVED on the list — honest ''; the detail
                       page's ld+json JobPosting datePosted is EXACT ISO
    Detail: /jobs/{id}/{slug}/job — schema.org JobPosting ld+json with
    title/datePosted/employmentType/baseSalary/description/hiring
    Organization('MINISO USA' — the identity proof)/jobLocation.
    """

    KIND = "icims"
    _MAX_PAGES = 60
    _LOC_RE = re.compile(
        r'field-label">Location</span>\\s*<span[^>]*>\\s*([^<]+?)\\s*<')
    _ANCHOR_RE = re.compile(
        r'href="(https?://[^"]*/jobs/(\\d+)/[^"]*)"\\s+class="iCIMS_An"'
        r'|class="iCIMS_An[^"]*"[^>]*href="(https?://[^"]*/jobs/(\\d+)/'
        r'[^"]*)"')
    _TITLE_ATTR_RE = re.compile(
        r'iCIMS_Anchor"[^>]*title="([^"]+)"')

    def __init__(self, org: str, cfg: Config):
        self.org = org.rstrip("/")

    @property
    def cfg(self) -> "Config":  # set post-init (see __init__ contract)
        return self._cfg

    def __init__(self, org: str, cfg: Config):  # noqa: F811 - single def
        self.org = org.rstrip("/")
        self._cfg = cfg

    def _page(self, page_no: int) -> tuple[list[dict], str]:
        url = f"https://{self.org}/jobs/search?in_iframe=1"
        if page_no > 1:
            url += f"&page={page_no}"
        html = _fetch_text_cached(url, f"ats:icims:{self.org}:p{page_no}",
                                  self._cfg)
        rows: list[dict] = []
        for m in re.finditer(r'<li class="iCIMS_JobCardItem">(.*?)</li>',
                             html, re.S):
            frag = m.group(1)
            a = re.search(
                r'href="(https?://[^"]*/jobs/(\\d+)/[^"]*)"', frag)
            if not a:
                continue
            url_, rid = a.groups()
            t = re.search(r'<h3[^>]*>\\s*(.*?)\\s*</h3>', frag, re.S)
            title = unescape(re.sub(r"<[^>]+>", "",
                                    t.group(1) if t else "")).strip()
            if not title:
                ta = re.search(r'title="([^"]+)"', a.group(0))
                title = unescape(ta.group(1)).strip() if ta else ""
            loc = self._LOC_RE.search(frag)
            loc_text = unescape(loc.group(1)).strip() if loc else ""
            own_id = re.search(
                r'field-label">ID</span>\\s*<span[^>]*>\\s*([^<]+?)'
                r'\\s*<', frag)
            pt = re.search(
                r'JobHeaderField">Position Type</dt>\\s*<dd[^>]*>'
                r'\\s*<span[^>]*>\\s*([^<]+?)\\s*<', frag)
            cat = re.search(
                r'JobHeaderField">Category</dt>\\s*<dd[^>]*>\\s*'
                r'<span[^>]*>\\s*([^<]+?)\\s*<', frag)
            rows.append({
                "rid": rid, "url": url_, "title": title,
                "loc": loc_text,
                "own_id": unescape(own_id.group(1)).strip()
                          if own_id else "",
                "tt": _TT.get(unescape(pt.group(1)).strip().lower(),
                             unescape(pt.group(1)).strip())
                      if pt else "",
                "cat": unescape(cat.group(1)).strip() if cat else "",
            })
        return rows, html

    def _rows(self) -> list[dict]:
        key = f"icims:{self.org}"
        hit = _CACHE.get(key)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL:
            return hit[1]
        all_rows: list[dict] = []
        seen: set[str] = set()
        page_no = 1
        for page_no in range(1, self._MAX_PAGES + 1):
            page_rows, html = self._page(page_no)
            new = [r for r in page_rows if r["rid"] not in seen]
            if not new:
                break
            for r in new:
                seen.add(r["rid"])
            all_rows.extend(new)
            if len(page_rows) < 50:      # short page = last page
                break
        _CACHE[key] = (time.monotonic(), all_rows)
        return all_rows

    @staticmethod
    def _country_code(loc_text: str) -> str:
        m = re.match(r"^([A-Z]{2})\\s*-", loc_text.strip())
        return m.group(1) if m else ""

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list",
                   include_remote: bool = False,
                   geo_scope: Optional[str] = None,
                   ) -> tuple[dict[str, dict], dict]:
        all_rows = self._rows()
        rows: dict[str, dict] = {}
        dropped_country = dropped_tt = kept_remote = 0
        for j in all_rows:
            if time_type and j["tt"] and \\
                    j["tt"].lower() != time_type.lower():
                dropped_tt += 1
                continue
            c = self._country_code(j["loc"])
            is_remote = "remote" in j["loc"].lower()
            if geo_scope == "non_cn":
                if c:
                    cn_sited = c in ("CN", "CHN") or \\
                        workday.country_str_matches(c, "china")
                else:
                    cn_sited = _text_is_cn_sited(j["loc"])
                if cn_sited and not is_remote:
                    dropped_country += 1
                    continue
            elif country:
                match = (workday.country_str_matches(c, country)
                         if c else
                         _plain_loc_in_country(j["loc"], country))
                if not match:
                    if include_remote and is_remote:
                        kept_remote += 1
                    else:
                        dropped_country += 1
                        continue
            rows[j["rid"]] = {
                "reqId": j["rid"],
                "title": j["title"],
                "company": self.org.split(".")[0],
                "url": j["url"],
                "externalPath": f"/{j['rid']}",
                "locationsText": j["loc"],
                "postedOn": "",
                "timeType": j["tt"],
                "bulletFields": [j["rid"]] + ([j["own_id"]]
                                              if j["own_id"] else []),
                "ats": "icims",
                "departments": [j["cat"]] if j["cat"] else [],
                "countries": [c] if c else [],
                "remoteType": "remote" if is_remote else "",
            }
        meta = {
            "complete": True, "total": len(all_rows),
            "pages": max(1, (len(all_rows) + 49) // 50),
            "country_client": False,
            "client_filtered": dropped_country + dropped_tt,
            "kept_remote": kept_remote,
            "geo_scope": geo_scope or "",
            "ats": "icims",
        }
        print(f"[{progress_label}] icims:{self.org}: {len(rows)} rows"
              + (f" ({dropped_country} non-{country} + {dropped_tt} "
                 f"non-{time_type} dropped client-side)"
                 if (country or time_type) else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        j = next((x for x in self._rows() if x["rid"] == rid), None)
        if j is None:
            return None
        html = _fetch_text_cached(
            j["url"], f"ats:icims:{self.org}:detail:{rid}", self._cfg)
        jp = None
        for m in re.finditer(
                r'<script type="application/ld\\+json">\\s*(.*?)'
                r'</script>', html, re.S):
            try:
                d = json.loads(m.group(1))
            except Exception:
                continue
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                jp = d
                break
        desc = str((jp or {}).get("description") or "")
        date_posted = str((jp or {}).get("datePosted") or "")
        employment = str((jp or {}).get("employmentType") or "")
        org_name = str((((jp or {}).get("hiringOrganization") or {})
                        .get("name")) or "")
        tt = (_TT.get(employment.lower().replace("_", "-"),
                      employment.replace("_", " ").title())
              if employment else j["tt"])
        if time_type and tt and tt.lower() != time_type.lower():
            return None
        label, iso = _posted_label(date_posted or None)
        return {
            "jobPostingInfo": {
                "title": (jp or {}).get("title") or j["title"],
                "location": j["loc"] or "See posting",
                "additionalLocations": [],
                "jobDescription": desc,
                "timeType": tt,
                "startDate": "",
                "externalUrl": j["url"],
                "jobReqId": rid,
                "postedOn": label,
                "country": {"descriptor": self._country_code(j["loc"])}
                           if self._country_code(j["loc"]) else None,
            },
            "hiringOrganization": {"name": org_name or self.org},
            "similarJobs": [],
            "firstPublishedIso": iso,
            "compensation": (jp or {}).get("baseSalary") or None,
        }


class PaycomAdapter:
    """ats:paycom:{region}/{portal-key} — Paycom's 'sprawl' career
    portal: a JS SPA whose data API is crackable WITHOUT a browser
    (S30 discovery, live-pinned on PSI 39DCF…F2 / clientcode 0NU75):

      1. GET the portal page — configsFromHost embeds a fresh
         sessionJWT (RS256, minted per view) + the mantle base URL.
      2. POST {base}api/ats/job-posting-previews/search
         headers: Authorization: <jwt>, Locale: en,
                  Translation-Highlights: 0
         body: {skip, take, filtersForQuery: {keywordSearchText,
         location, sortOption, filters}} — the sub-keys are MANDATORY
         (a bare filtersForQuery:{} silently returns 0 jobs).
      3. Response: {jobPostingPreviews: [{jobId, jobTitle,
         positionType, remoteType, locations, description,
         postedOn, isHotJob}], jobPostingPreviewsCount}.

    Field map (live-pinned 2026-10-10 on PSI, 45 postings):
      reqId            jobId ('383557')
      locationsText    locations ('WI Darien - Darien, WI 53114; …')
      country          NOT structured — plain-loc classification
                       (state tokens; the S20 case-sensitive rule)
      timeType         positionType ('Full Time' → _TT)
      postedOn         often '' on this portal — honest blank
    geo_scope non_cn: location-text CN-classification + remoteType.
    """

    KIND = "paycom"
    _PORTAL_TMPL = ("https://www.paycomonline.net/v4/ats/web.php/"
                    "portal/{key}/career-page")
    _MAX_ROWS = 500

    def __init__(self, org: str, cfg: Config):
        # org = '{region}/{portalkey}', e.g.
        # 'us-cent/39DCF574C16448FF09ADD3EF809F9EF2'
        parts = org.strip("/").split("/", 1)
        self.region = parts[0] if len(parts) == 2 else "us-cent"
        self.key = parts[-1]
        self._cfg = cfg
        self._company = ""

    @property
    def _base(self) -> str:
        return (f"https://portal-applicant-tracking.{self.region}"
                f".paycomonline.net/")

    @property
    def _portal_url(self) -> str:
        return self._PORTAL_TMPL.format(key=self.key)

    def _jobs(self) -> list[dict]:
        key = f"paycom:{self.region}/{self.key}"
        hit = _CACHE.get(key)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL:
            return hit[1]
        html = _fetch_text_cached(
            self._portal_url, f"ats:paycom:{self.key}", self._cfg)
        m = re.search(r'sessionJWT\\":\\"([^"\\\\]+)\\"', html) or \\
            re.search(r'sessionJWT"\\s*:\\s*"([^"]+)"', html)
        if not m:
            raise RuntimeError(
                f"paycom:{self.key}: no sessionJWT in portal page — "
                f"portal dead or shape changed; refusing")
        jwt = m.group(1)
        mantle = re.search(
            r'atsPortalMantleServiceUrl\\":\\"([^"\\\\]+)\\"', html)
        base = mantle.group(1).replace("\\/", "/") if mantle else \\
            self._base
        if not base.endswith("/"):
            base += "/"
        jobs: list[dict] = []
        skip = 0
        while skip < self._MAX_ROWS:
            body = {"skip": skip, "take": 100,
                    "filtersForQuery": {"keywordSearchText": "",
                                        "location": "",
                                        "sortOption": "",
                                        "filters": []}}
            d = _post_json_urllib(
                f"{base}api/ats/job-posting-previews/search", body,
                headers={"Authorization": jwt, "Locale": "en",
                         "Translation-Highlights": "0"})
            batch = d.get("jobPostingPreviews") or []
            if not isinstance(batch, list):
                raise RuntimeError(
                    f"paycom:{self.key}: unexpected search payload "
                    f"(jobPostingPreviews not a list)")
            jobs.extend(batch)
            skip += 100
            if len(batch) < 100 or skip >= int(
                    d.get("jobPostingPreviewsCount") or 0):
                break
        _CACHE[key] = (time.monotonic(), jobs)
        # company name (identity evidence; lazily, non-fatal)
        try:
            cn = fetch_json(f"{base}api/ats/company-name", cfg=self._cfg)
            if isinstance(cn, dict) and cn.get("companyName"):
                self._company = str(cn["companyName"])
        except Exception:
            pass
        return jobs

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list",
                   include_remote: bool = False,
                   geo_scope: Optional[str] = None,
                   ) -> tuple[dict[str, dict], dict]:
        jobs = self._jobs()
        rows: dict[str, dict] = {}
        dropped_country = dropped_tt = kept_remote = 0
        for job in jobs:
            rid = str(job.get("jobId") or "")
            if not rid or rid in rows:
                continue
            tt_raw = str(job.get("positionType") or "").strip()
            tt = _TT.get(tt_raw.lower(), tt_raw)
            if time_type and tt and tt.lower() != time_type.lower():
                dropped_tt += 1
                continue
            loc = str(job.get("locations") or "")
            rt = str(job.get("remoteType") or "").lower()
            is_remote = "remote" in rt or "remote" in loc.lower()
            if geo_scope == "non_cn":
                if _text_is_cn_sited(loc) and not is_remote:
                    dropped_country += 1
                    continue
            elif country:
                segs = [s for s in loc.split(";") if s.strip()]
                match = any(_plain_loc_in_country(
                    s.rsplit("-", 1)[-1].strip(), country)
                    for s in segs) if segs else False
                if not match:
                    if include_remote and is_remote:
                        kept_remote += 1
                    else:
                        dropped_country += 1
                        continue
            label, iso = _posted_label(None)
            rows[rid] = {
                "reqId": rid,
                "title": str(job.get("jobTitle") or ""),
                "company": self._company or self.key[:8],
                "url": self._portal_url,
                "externalPath": f"/{rid}",
                "locationsText": loc,
                "postedOn": "",
                "timeType": tt,
                "bulletFields": [rid],
                "ats": "paycom",
                "countries": [],
                "remoteType": rt or ("remote" if is_remote else ""),
            }
        meta = {
            "complete": True, "total": len(jobs),
            "pages": max(1, (len(jobs) + 99) // 100),
            "country_client": False,
            "client_filtered": dropped_country + dropped_tt,
            "kept_remote": kept_remote,
            "geo_scope": geo_scope or "",
            "ats": "paycom",
        }
        print(f"[{progress_label}] paycom:{self.key[:12]}…: "
              f"{len(rows)} rows"
              + (f" ({dropped_country} non-{country} + {dropped_tt} "
                 f"non-{time_type} dropped client-side)"
                 if (country or time_type) else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        job = next((x for x in self._jobs()
                    if str(x.get("jobId")) == rid), None)
        if job is None:
            return None
        tt_raw = str(job.get("positionType") or "").strip()
        tt = _TT.get(tt_raw.lower(), tt_raw)
        if time_type and tt and tt.lower() != time_type.lower():
            return None
        return {
            "jobPostingInfo": {
                "title": str(job.get("jobTitle") or ""),
                "location": str(job.get("locations") or "")
                            or "See posting",
                "additionalLocations": [],
                "jobDescription": str(job.get("description") or ""),
                "timeType": tt,
                "startDate": "",
                "externalUrl": self._portal_url,
                "jobReqId": rid,
                "postedOn": "",
                "country": None,
            },
            "hiringOrganization": {
                "name": self._company or self.key[:8]},
            "similarJobs": [],
        }


class A123Adapter:
    """custom:a123 — a123systems.com's ngc-CMS join_us pages (the
    /careers 403 is a dead path, NOT a dead board — S30 lesson: the
    homepage links /join_us.html which 200s from HK egress).

    Server-rendered ROWS WITH FULL JDs (the greenhouse economics: one
    page = complete data, no detail fetch):
      p_loopitem → cbox-24-0 title / -1 dept / -3 location
                   ('Novi, Michigan' | 'Hangzhou, China')
                   / -4 date ('09/02/2026' MM/DD/YYYY)
      the trailing e_richText divs carry the full JD (Job overview,
      Responsibilities, …) — concatenated as description.
    reqId = slugified title (the board serves no per-row id; the View
    button is a JS toggle with no href).
    US filter: _plain_loc_in_country (state tokens; 'michigan' keeps,
    'china' drops). geo_scope NOT accepted (custom honesty convention).
    """

    KIND = "a123"
    _DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y")

    def __init__(self, org: str, cfg: Config):
        if not org:
            org = "www.a123systems.com"   # custom: grammar — host baked
        self.org = org
        self.cfg = cfg

    def _pages(self) -> list[str]:
        pages = ["https://{}/join_us.html".format(self.org)]
        offset = 6
        for _ in range(30):              # 180 rows guard
            url = f"https://{self.org}/join_us/p-{offset}-6.html"
            html = _fetch_text_cached(
                url, f"ats:a123:{self.org}:p{offset}", self.cfg)
            if "p_loopitem" not in html:
                break
            pages.append(url)
            offset += 6
        return pages

    def _rows(self) -> list[dict]:
        key = f"a123:{self.org}"
        hit = _CACHE.get(key)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL:
            return hit[1]
        rows: list[dict] = []
        seen: set[str] = set()
        for page in self._pages():
            html = _fetch_text_cached(
                page, f"ats:a123:page:{page}", self.cfg)
            for m in re.finditer(
                    r'<div class="[^"]*p_loopitem[^"]*"[^>]*>(.*?)'
                    r'(?=<div class="[^"]*p_loopitem|\\Z)', html, re.S):
                frag = m.group(1)

                def cell(idx: int) -> str:
                    mm = re.search(
                        r'cbox-24-{} p_item[^>]*>\\s*<p[^>]*>(.*?)'
                        r'</p>'.format(idx), frag, re.S)
                    if not mm:
                        return ""
                    return unescape(re.sub(
                        r"<[^>]+>", " ", mm.group(1))).strip()

                title = cell(0)
                if not title or title.lower() == "position":
                    continue
                rid = re.sub(r"[^a-z0-9]+", "-",
                             title.lower()).strip("-")
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                dept = cell(1)
                loc = cell(3)
                date_raw = cell(4)
                # full JD: concatenate the rich-text blocks after the row
                desc_parts = re.findall(
                    r'<div class="[^"]*e_richText[^"]*"[^>]*>(.*?)'
                    r'</div>', frag, re.S)
                desc = "\\n".join(
                    re.sub(r"\\n{2,}", "\\n",
                           re.sub(r"<[^>]+>", "\\n",
                                  unescape(x))).strip()
                    for x in desc_parts if x.strip())
                iso = ""
                for fmt in self._DATE_FORMATS:
                    try:
                        iso = datetime.strptime(date_raw, fmt) \\
                            .date().isoformat()
                        break
                    except ValueError:
                        continue
                rows.append({"rid": rid, "title": title, "dept": dept,
                             "loc": loc, "date": iso, "desc": desc})
        _CACHE[key] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list",
                   ) -> tuple[dict[str, dict], dict]:
        all_rows = self._rows()
        rows: dict[str, dict] = {}
        dropped = 0
        for j in all_rows:
            if country and not _plain_loc_in_country(j["loc"], country):
                dropped += 1
                continue
            label, iso = _posted_label(j["date"] or None)
            rows[j["rid"]] = {
                "reqId": j["rid"],
                "title": j["title"],
                "company": "A123 Systems",
                "url": f"https://{self.org}/join_us.html"
                       f"#{j['rid']}",
                "externalPath": f"/{j['rid']}",
                "locationsText": j["loc"],
                "postedOn": label,
                "timeType": "",
                "bulletFields": [j["rid"]],
                "ats": "a123",
                "departments": [j["dept"]] if j["dept"] else [],
                "countries": [],
            }
        meta = {
            "complete": True, "total": len(all_rows), "pages":
                max(1, (len(all_rows) + 5) // 6),
            "country_client": False,
            "client_filtered": dropped,
            "geo_scope": "",
            "ats": "a123",
        }
        print(f"[{progress_label}] a123:{self.org}: {len(rows)} rows"
              + (f" ({dropped} non-{country} dropped)" if country else ""),
              file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/")
        j = next((x for x in self._rows() if x["rid"] == rid), None)
        if j is None:
            return None
        label, iso = _posted_label(j["date"] or None)
        return {
            "jobPostingInfo": {
                "title": j["title"],
                "location": j["loc"] or "See posting",
                "additionalLocations": [],
                "jobDescription": j["desc"],
                "timeType": "",
                "startDate": "",
                "externalUrl": f"https://{self.org}/join_us.html"
                               f"#{j['rid']}",
                "jobReqId": rid,
                "postedOn": label,
                "country": None,
            },
            "hiringOrganization": {"name": "A123 Systems"},
            "similarJobs": [],
            "firstPublishedIso": iso,
        }


'''

path = pathlib.Path("/home/z/research/scripts/s30_adapter_block.py")
path.write_text(BLOCK, encoding="utf-8")
print("wrote", path, len(BLOCK), "chars")
