"""S32 — the LinkedInCompanyAdapter fragment (custom:licompany:{key}).

One class, one contract dict per company (the ssrpage factoring). Every
URN + li_variant live-pinned 2026-10-12 (audit/s32_li/probe7/9 +
dormant_resolution.json). Design vetted by audit/s32_review_findings.md
(P0-1..P0-4, P1-1..P1-6 all incorporated):

  - f_C presence hard-asserted (a dropped URN silently serves the GLOBAL
    US feed — measured, probe6);
  - stop conditions are SIGNATURE-based (26B stub = genuine end;
    0-cards-any-other-shape = blocked_empty, complete=False, retryable,
    never terminal; short page = tail; HTTP!=200 raises);
  - a page with >0 cards but ZERO li_variant matches RAISES (wrong/
    renamed entity — the Onyx-RIP/LeSportsac shape);
  - no runtime URN re-resolution (alert-first; a 0-row week for a live
    company is a stub-day, not a swap trigger);
  - meta.complete honest (a blocked entity makes the aggregate
    incomplete — the driver keeps prior rows, marks no gones);
  - no geo_scope, no client country filter (the feed is server-scoped
    by the location facet; country_client=False);
  - no corroboration lane (config sets skip_li_corroboration — the
    source IS LinkedIn).
"""

# ── the fragment (spliced into site_boards.py) ──────────────────────────

FRAGMENT = r'''

# ── S32: LinkedIn guest company feeds (custom:licompany:{key}) ──────────

_LI_GUEST_SEARCH = ("https://www.linkedin.com/jobs-guest/jobs/api/"
                    "seeMoreJobPostings/search")
_LI_STUB_MAX_B = 100          # chromeless end-of-serving stub bound


class LinkedInCompanyAdapter:
    """custom:licompany:{key} — a LinkedIn-guest company feed as a board.

    Discovery (S32, measured): jobs-guest/api/typeaheadHands — the
    typeahead endpoint resolves company display names to numeric URNs
    without auth; the URN pins the f_C company facet on the GUEST
    search endpoint (the main-SPA f_C is authwalled — never probed
    pre-S32). ~10 cards/page, `start` true offset, f_TPR 30-day window
    (matching postings older than 30d are not served — a gone-mark on
    this board means "left the 30d window", documented D-S32-1).

    Contracts (LICOMPANY_CONTRACTS below): company / urn / li_variants
    (EXACT card company-strings, lowercased — equality, never substring:
    "firework" must not match "fireworks ai"). Multi-entity companies
    measured zero rows on non-primary entities (probe9) — v1 pins ONE
    urn per company; entity aliases are a future additive.

    Honesty guards (audit/s32_review_findings.md P0-2/P0-3):
      - f_C hard-asserted non-empty numeric on EVERY request (a dropped
        param serves the GLOBAL US feed under this company's name);
      - stub signature (non-empty, <_LI_STUB_MAX_B bytes, no card
        chrome) = genuine end-of-serving (also the 0-posting verdict);
      - 0 cards + any OTHER body shape = blocked_empty: complete=False
        (retryable, never terminal — the S9/A8 doctrine);
      - >0 cards + zero li_variant matches = ValueError (wrong or
        renamed entity — the Onyx-RIP/LeSportsac card shape);
      - HTTP != 200 raises (429/403/302-authwall/5xx fail loudly);
      - meta.complete False when ANY entity walk was blocked.
    """

    KIND = "licompany"

    def __init__(self, org: str, cfg: Config):
        if org not in LICOMPANY_CONTRACTS:
            raise ValueError(
                f"licompany: unknown contract {org!r} (known: "
                f"{sorted(LICOMPANY_CONTRACTS)})")
        self.key = org
        self.c = LICOMPANY_CONTRACTS[org]
        self.company = self.c["company"]
        self.ats = "licompany"
        self.cfg = cfg

    # -- transport: curl_cffi chrome131 primary (all S32 evidence was
    # gathered with it), plain requests fallback (fresh runners).
    def _get(self, url: str, params: dict) -> tuple[int, str, str]:
        last: Exception | None = None
        try:
            from curl_cffi import requests as cffi_requests
            r = cffi_requests.get(
                url, params=params, impersonate="chrome131",
                timeout=self.cfg.http_timeout_s,
                headers={"User-Agent": USER_AGENT},
                allow_redirects=False)
            return r.status_code, r.text, (r.headers.get("location") or "")
        except ImportError:
            pass
        except Exception as exc:            # network-class only — try plain
            last = exc
        import requests as _rq
        r = _rq.get(url, params=params, timeout=self.cfg.http_timeout_s,
                    headers={"User-Agent": USER_AGENT},
                    allow_redirects=False)
        return r.status_code, r.text, (r.headers.get("location") or "")

    @staticmethod
    def _parse_cards(html: str) -> list[dict]:
        out = []
        for m in re.finditer(
                r'data-entity-urn="urn:li:jobPosting:(\d+)"(.*?)</li>',
                html, re.DOTALL):
            jid, card = m.groups()
            t = re.search(r"base-search-card__title[^>]*>\s*(.*?)\s*"
                          r"</h3>", card, re.DOTALL)
            c = re.search(r"hidden-nested-link[^>]*>\s*(.*?)\s*</a>",
                          card, re.DOTALL)
            l = re.search(r"job-search-card__location[^>]*>\s*(.*?)"
                          r"\s*</span>", card, re.DOTALL)
            d = re.search(r'datetime="([^"]+)"', card)
            out.append({
                "rid": jid,
                "title": _strip_tags(t.group(1)) if t else "",
                "company": _strip_tags(c.group(1)) if c else "",
                "loc": _strip_tags(l.group(1)) if l else "",
                "iso": (d.group(1) or "")[:10] if d else "",
            })
        return out

    def _walk_entity(self, urn: str) -> tuple[list[dict], str]:
        """One entity's f_C feed. Returns (cards, status) where status
        is 'end' | 'tail' | 'blocked' (complete) or raises on hard
        failures (HTTP != 200, wrong-entity signature)."""
        if not (urn and str(urn).isdigit()):
            raise ValueError(
                f"licompany:{self.key}: URN {urn!r} is not a non-empty "
                f"numeric id — refusing to request an unscoped feed "
                f"(a dropped f_C serves the GLOBAL US feed)")
        rows: list[dict] = []
        offset = 0
        for _page in range(10):            # 10 pages x ~10 cards cap
            code, body, loc_hdr = self._get(_LI_GUEST_SEARCH, {
                "f_C": str(urn), "location": self.c.get(
                    "location", "United States"),
                "f_TPR": "r2592000", "sortBy": "DD", "start": str(offset)})
            if code != 200:
                hint = f" (302 -> {loc_hdr[:60]})" if code in (301, 302) \
                    else ""
                raise RuntimeError(
                    f"licompany:{self.key}: LinkedIn guest feed HTTP "
                    f"{code}{hint} — wall/rate-limit, failing loudly")
            cards = self._parse_cards(body)
            if not cards:
                stub = (0 < len(body) < _LI_STUB_MAX_B
                        and "base-search-card" not in body)
                if stub:
                    return rows, "end"
                # 0 cards + any other shape: ambiguous soft wall —
                # retryable, NEVER terminal (S9/A8 blocked_empty).
                return rows, "blocked"
            matched = [c for c in cards
                       if c["company"].strip().lower() in self.c[
                           "li_variants"]]
            if not matched:
                raise ValueError(
                    f"licompany:{self.key}: feed page at start={offset} "
                    f"served {len(cards)} cards but NONE match the "
                    f"pinned li_variants {self.c['li_variants']} "
                    f"(companies seen: "
                    f"{sorted({c['company'] for c in cards})[:4]}) — "
                    f"wrong or renamed LinkedIn entity (URN drift)")
            rows.extend(matched)
            if len(cards) < 10:
                return rows, "tail"
            offset += len(cards)
            time.sleep(1.0)               # _PAGE_PAUSE politeness
        return rows, "end"

    def _rows(self) -> tuple[list[dict], bool]:
        key = f"licompany:{self.key}"
        hit = _CACHE.get(key)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL:
            return hit[1]
        complete = True
        agg: list[dict] = []
        seen: set[str] = set()
        for urn in [self.c["urn"]] + list(self.c.get("entity_ids", [])):
            cards, status = self._walk_entity(urn)
            for c in cards:
                if c["rid"] in seen:
                    continue
                seen.add(c["rid"])
                c["url"] = f"https://www.linkedin.com/jobs/view/{c['rid']}"
                agg.append(c)
            if status == "blocked":
                complete = False
        val = (agg, complete)
        _CACHE[key] = (time.monotonic(), val)
        return val

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list",
                   include_remote: bool = False,
                   geo_scope: Optional[str] = None,
                   ) -> tuple[dict[str, dict], dict]:
        rows_raw, complete = self._rows()
        rows: dict[str, dict] = {}
        for j in rows_raw:
            rows[j["rid"]] = {
                "reqId": j["rid"],
                "title": j["title"],
                "company": self.company,
                "url": j["url"],
                "externalPath": f"/{j['rid']}",
                "locationsText": j["loc"],
                "postedOn": _posted_label(j["iso"] or None)[0],
                "timeType": "",
                "bulletFields": [j["rid"]],
                "ats": self.ats,
                "departments": [],
                "countries": [],
                "remoteType": "",
            }
        meta = {
            "complete": complete, "total": len(rows_raw), "pages": 1,
            "country_client": False,      # server-scoped (location facet)
            "client_filtered": 0, "kept_remote": 0,
            "geo_scope": "", "ats": self.ats,
        }
        if not complete:
            print(f"[{progress_label}] licompany:{self.key}: BLOCKED "
                  f"page shape (incomplete list; prior rows retained)",
                  file=sys.stderr, flush=True)
        print(f"[{progress_label}] licompany:{self.key}: {len(rows)} rows"
              f" (feed-scoped US)", file=sys.stderr, flush=True)
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").split("?")[0]
        if not rid.isdigit():
            return None
        code, body, _ = self._get(
            "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/"
            + rid, {})
        if code != 200:
            return None
        rows, _ = self._rows()
        j = next((x for x in rows if x["rid"] == rid), None)
        jd_m = re.search(
            r'show-more-less-html__markup[^>]*>(.*?)</div>', body,
            re.DOTALL)
        jd = _block_text(jd_m.group(1)) if jd_m else ""
        text = " ".join(jd.split())
        mode_m = re.search(r"\b(Remote|Hybrid|On-site)\b", text, re.I)
        mode = mode_m.group(1) if mode_m else ""
        caption = re.search(
            r"num-applicants__caption[^>]*>(.*?)</span>", body, re.DOTALL)
        applicants = (caption.group(1).strip()
                      if caption else "").split("\n")[0][:80]
        closed = "no longer accepting applications" in text.lower()
        title = (j or {}).get("title", "")
        iso = (j or {}).get("iso", "")
        label, iso_n = _posted_label(iso or None)
        return {
            "jobPostingInfo": {
                "title": title,
                "location": ((j or {}).get("loc") or "See posting"),
                "additionalLocations": [],
                "jobDescription": jd[:12000],
                "timeType": "",
                "startDate": "",
                "externalUrl": f"https://www.linkedin.com/jobs/view/{rid}",
                "jobReqId": rid,
                "postedOn": label,
                "country": None,
            },
            "hiringOrganization": {"name": self.company},
            "similarJobs": [],
            "firstPublishedIso": iso_n,
            "workMode": mode,
            "numApplicants": applicants,
            "closed": closed,
        }


LICOMPANY_CONTRACTS: dict[str, dict] = {
    # ── daily lane (live US postings measured 2026-10-12, probe9) ──────
    "vesync": {"company": "VeSync", "urn": "3374179",
               "li_variants": ["vesync"]},
    "temu": {"company": "Temu", "urn": "90534028",
             "li_variants": ["temu"]},
    "otterai": {"company": "Otter.ai", "urn": "35593855",
                "li_variants": ["otter.ai"]},
    "heygen": {"company": "HeyGen", "urn": "82350899",
               "li_variants": ["heygen"]},
    "heytea": {"company": "HeyTea", "urn": "29502606",
               "li_variants": ["heytea"]},
    "uniuni": {"company": "UniUni", "urn": "19172866",
               "li_variants": ["uniuni"]},
    "opusclip": {"company": "OpusClip", "urn": "80267401",
                 "li_variants": ["opusclip"]},
    "wyze": {"company": "Wyze", "urn": "11286858",
             "li_variants": ["wyze"]},
    "renpho": {"company": "Renpho", "urn": "19105082",
               "li_variants": ["renpho"]},
    "shokz": {"company": "Shokz", "urn": "3038016",
              "li_variants": ["shokz"]},
    "zepp": {"company": "Zepp Health", "urn": "71672713",
             "li_variants": ["zepp health"]},
    "firework": {"company": "Firework", "urn": "18700473",
                 "li_variants": ["firework"]},
    "grubmarket": {"company": "GrubMarket", "urn": "4824711",
                   "li_variants": ["grubmarket inc", "grubmarket"]},
    "olight": {"company": "Olight", "urn": "89798652",
               "li_variants": ["olight world", "olight"]},
    "apture": {"company": "Aputure", "urn": "1215351",
               "li_variants": ["apture"]},
    "creality": {"company": "Creality", "urn": "13719766",
                 "li_variants": ["creality"]},
    "mixue": {"company": "Mixue Group", "urn": "71363620",
              "li_variants": ["mixue group", "mixue"]},
    "gptzero": {"company": "GPTZero", "urn": "92972565",
                "li_variants": ["gptzero"]},
    # ── dormant lane (exact-name entities, 0 rows measured — the
    # weekly re-probe flips these to live the day they start posting) ──
    "govee": {"company": "Govee", "urn": "82532514",
              "li_variants": ["govee"]},
    "vava": {"company": "VAVA (Sunvalley Group)", "urn": "89680414",
             "li_variants": ["vava"]},
    "eveenergy": {"company": "EVE Energy", "urn": "3994381",
                  "li_variants": ["eve energy co,ltd", "eve energy"]},
    "jinkosolar": {"company": "JinkoSolar", "urn": "81503115",
                   "li_variants": ["jinkosolar co.", "jinkosolar"]},
    "renogy": {"company": "Renogy", "urn": "2039334",
               "li_variants": ["renogy"]},
    "jackery": {"company": "Jackery", "urn": "3586843",
                "li_variants": ["jackery"]},
    "tigergraph": {"company": "TigerGraph", "urn": "3693966",
                   "li_variants": ["tigergraph"]},
    "tdengine": {"company": "TDengine", "urn": "74276373",
                 "li_variants": ["tdengine"]},
    "centurygames": {"company": "Century Games", "urn": "14523383",
                     "li_variants": ["century games"]},
    "dreo": {"company": "Dreo", "urn": "96943605",
             "li_variants": ["dreo"]},
    "bluetti": {"company": "Bluetti", "urn": "74359099",
                "li_variants": ["bluetti"]},
    "zendure": {"company": "Zendure", "urn": "3280993",
                "li_variants": ["zendure"]},
    "hollyland": {"company": "Hollyland", "urn": "3652142",
                  "li_variants": ["hollyland technology", "hollyland"]},
    "xreal": {"company": "XREAL", "urn": "13368668",
              "li_variants": ["xreal"]},
    "anycubic": {"company": "Anycubic", "urn": "73793049",
                 "li_variants": ["anycubic"]},
    "elegoo": {"company": "Elegoo", "urn": "13756996",
               "li_variants": ["elegoo"]},
    "baseus": {"company": "Baseus", "urn": "7796164",
               "li_variants": ["baseus"]},
    "xtransfer": {"company": "XTransfer", "urn": "18215108",
                  "li_variants": ["xtransfer"]},
    "lianlian": {"company": "LianLian Global", "urn": "8448273",
                 "li_variants": ["lianlian"]},
    "skyworth": {"company": "Skyworth", "urn": "14573471",
                 "li_variants": ["skyworth"]},
    "bentoml": {"company": "BentoML", "urn": "18263037",
                "li_variants": ["bentoml (acquired by modular)",
                                "bentoml"]},
    "affine": {"company": "AFFiNE", "urn": "1827639",
               "li_variants": ["affine"]},
    "llamaindex": {"company": "LlamaIndex", "urn": "1500334",
                   "li_variants": ["llamaindex"]},
    "pixverse": {"company": "PixVerse", "urn": "99075429",
                 "li_variants": ["pixverse"]},
    "databend": {"company": "Databend", "urn": "79502817",
                 "li_variants": ["databend"]},
    "notta": {"company": "Notta", "urn": "96075985",
              "li_variants": ["notta"]},
    "polloai": {"company": "Pollo AI", "urn": "109632440",
                "li_variants": ["pollo ai"]},
    "vidnoz": {"company": "Vidnoz", "urn": "91115441",
               "li_variants": ["vidnoz"]},
    "fotor": {"company": "Fotor", "urn": "18128937",
              "li_variants": ["fotor"]},
    "bougerv": {"company": "BougeRV", "urn": "143534950",
                "li_variants": ["bougerv"]},
    "sengled": {"company": "Sengled", "urn": "2682145",
                "li_variants": ["sengled"]},
    "moondrop": {"company": "Moondrop", "urn": "105178785",
                 "li_variants": ["moondrop"]},
    "sharge": {"company": "Sharge", "urn": "10949919",
               "li_variants": ["sharge"]},
    "xgimi": {"company": "XGIMI", "urn": "31955044",
              "li_variants": ["xgimi technology", "xgimi"]},
    "torras": {"company": "Torras", "urn": "43183657",
               "li_variants": ["torras"]},
    "timekettle": {"company": "Timekettle", "urn": "13345480",
                   "li_variants": ["timekettle"]},
    "zhiyun": {"company": "Zhiyun", "urn": "10369569",
               "li_variants": ["zhiyun-tech", "zhiyun"]},
    "aqara": {"company": "Aqara", "urn": "13635222",
              "li_variants": ["aqara"]},
    "switchbot": {"company": "SwitchBot", "urn": "53500716",
                  "li_variants": ["switchbot"]},
    "petlibro": {"company": "Petlibro", "urn": "54363614",
                 "li_variants": ["petlibro"]},
    "merach": {"company": "Merach", "urn": "35947146",
               "li_variants": ["merach"]},
    # excluded with evidence (audit/s32_li/dormant_resolution.json):
    # onyx (entity 11120 is NOT Danswer), sider (SIDER = steel co),
    # miniso group / tims china / retell ai / finalround ai (no
    # typeahead entity at all — honest park, re-check quarterly).
}
'''
