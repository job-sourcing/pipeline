# S27 Origin Re-Calibration — Batch B1 (wired gray-zone boards)

Scope: 5 currently-wired companies re-adjudicated under the user's S27 criteria
(Chinese-descent founders alone don't count; need china_hq / cn_owned /
china_ops / china_remote_workforce evidence at CURRENT 2025-2026 state).
Task ID: S27-R3. Verdict classes + evidence rules per
`origin_research_s27_prompt.md`. Live-fetch budget respected (DDG-lite
snippets + Wikipedia + SEC EDGAR primary filings + live ATS APIs).

---

## OKX (okx.com)
- Verdict: cn_owned
- China ops / ownership / workforce: OKX is privately held; founder-CEO Star Xu
  (徐明星 / Xu Mingxing, b. 1985 Hunan; PRC national) remains the controlling
  shareholder — NYSE-parent Intercontinental Exchange's March 2026 investment
  was expressly a MINORITY stake at a $25bn valuation, so control stayed with
  the pre-existing holders around Xu. CURRENT mainland-China ops: none — OKX
  exited mainland-China customers in Oct 2021, the founding team moved out
  ("few outsourced personnel working in the region"), and the live Greenhouse
  board (334 jobs, fetched 2026) shows ZERO mainland-China roles; HQ was
  relocated to San Jose, CA in April 2025 alongside the US relaunch, with
  Singapore (99 roles) and Hong Kong (81+34 roles) as the workforce hubs.
  Chinese-language hiring bias persists but is modest: 3 postings explicitly
  require Mandarin (e.g. "Manager, Customer Service (Mandarin Support)") and
  one bilingual-titled role ("增长与 AI 专项经理"). Separate company, same
  controller: Xu's HKEX-listed OKG Technology Holdings (欧科云链, ex-Leap
  Holdings, acquired 2019 for $60M) carries the Shenzhen/Beijing blockchain-data
  arms — evidence of the controller's ongoing China footprint, not of OKX
  exchange ops.
- Sources: https://en.wikipedia.org/wiki/OKX (HQ San Jose; 2021 mainland exit; ICE $25bn minority stake Mar-2026); https://en.wikipedia.org/wiki/Star_Xu (PRC national, controlling stake in OKG Tech/Leap Holdings); https://grokipedia.com/page/Star_Xu ("holds Chinese nationality"); https://www.fintechfutures.com/m-a/ice-secures-minority-stake-in-okx-at-25bn-valuation; https://boards-api.greenhouse.io/v1/boards/okx/jobs?content=true (live 334 jobs: SG 99 / HK 81+34 / San Jose 15 / mainland 0; 3 Mandarin roles)
- Note: wired board = Greenhouse `okx` (live, 334 jobs, ~19 US-located incl.
  15 San Jose + 4 US-remote). Census `origin: us_chinese_founder` is stale →
  cn_owned (KEEP). Gray: no public % for Xu's OKX Group stake — the ICE
  "minority stake" language + Xu as sole founder/CEO of a never-sold private
  group is the control evidence. HK/SG-heavy workforce means the country gate
  must keep feeding HK/Singapore roles (relevant for the user's global-role
  filter).

## Binance (binance.com)
- Verdict: cn_owned
- China ops / ownership / workforce: Binance has no official HQ and no
  mainland-China entity (founded in China 2017, moved out after the ban —
  HISTORIC), but ownership + workforce gravity are Chinese. Co-founder Changpeng
  Zhao (CZ, b. Jiangsu China; Canadian/UAE citizen) remains majority owner —
  his Forbes net worth hit $111.1bn in April 2026, essentially all Binance
  equity, and the Nov-2023 DOJ settlement expressly let him keep his ownership
  (UAE's MGX/Mubadala took only a $2bn minority stake in 2025). Co-founder Yi He
  (何一, Chinese national) was appointed co-CEO on 2025-12-03. Workforce
  evidence is the strongest of the batch: the live Lever board (302 jobs,
  fetched 2026) is ~75% Greater-China-timezone — 172 "Asia" (remote-Asia), 33
  Hong Kong, 20 Taipei — and 48 postings carry the literal requirement
  "Bilingual English/Mandarin is required to be able to coordinate with
  overseas partners and stakeholders" (including Backend Engineer (Java) -
  KYC/Trading roles), plus 5 "Greater China"-dedicated BD/ops roles and
  bilingual EN/中文 posting bodies (岗位要求 headers). That is systematic
  Chinese-language hiring bias under the user's "operation heavily gravitates
  towards chinese" clause.
- Sources: https://en.wikipedia.org/wiki/Changpeng_Zhao ($111.1bn Apr 2026; retained ownership at DOJ plea; Jiangsu-born); https://en.wikipedia.org/wiki/Binance (no HQ; Yi He co-CEO 2025-12-03; 5,000 employees; MGX $2bn stake); https://api.lever.co/v0/postings/binance?mode=json (live 302 jobs: Asia 172 / HK 33 / Taipei 20 / US 2; 48 postings require "Bilingual English/Mandarin"; 5 Greater-China roles)
- Note: wired board = Lever `binance` (live, 302 jobs, only 2 US-located —
  "Cloud AI Engineer - US", "Regulatory Legal Counsel - US Monitorships").
  Census `origin: us_chinese_founder` is stale → cn_owned (KEEP). Gray: CZ's
  current citizenship is Canadian/UAE (born PRC), but PRC-national co-founder
  Yi He as co-CEO + the Mandarin-required hiring at scale carry the verdict.
  Feed-quality watch: census round S26 noted binance row pollution — keep the
  export gate.

## Webull (NYSE: BULL)
- Verdict: cn_owned (china_ops facts also satisfied at material scale)
- China ops / ownership / workforce: SEC FY2025 20-F (filed 2026-04-09) — the
  primary-source anchor: founder/chairman/CEO Wang Anquan (王安泉, citizen of
  the People's Republic of China) beneficially owns 16.4% of ordinary shares
  including ALL outstanding Class B shares (20 votes/share), i.e. 79.2% of
  total voting power as of 2026-03-31 — a Nasdaq "controlled company". His
  latest Schedule 13G/A (filed 2026-10-02) shows he converted 25M Class B→A on
  2026-09-30 but still holds 58.86M Class B + proxies (18.1% of the enlarged
  Class A class; voting power remains far above majority). China ops at
  material CURRENT scale: mainland-China subsidiary Hunan Weibu Information
  Technology Co., Ltd. (湖南微步信息技术有限公司, "Technology Support and
  Development") employed 863 people = 62% of the group's total workforce as of
  2025-12-31, "subject to the jurisdiction of the PRC"; management entities'
  address of record is Shanghai (Noah Wealth Center, Minhang). US HQ is St.
  Petersburg FL (listed via SPAC Apr 2025), which is why the class is cn_owned
  rather than china_hq. US scrutiny (14 state AGs, House Select CCP Committee,
  senators) is itself evidence of the China gravity.
- Sources: https://www.sec.gov/Archives/edgar/data/1866364/000121390026041653/ea0283691-20f_webull.htm (20-F FY2025: Wang 16.4% shares/79.2% votes; Hunan Weibu 863 employees = 62%; Class B = 20 votes; Shanghai addresses); https://www.sec.gov/Archives/edgar/data/1866364/000206185826000008/primary_doc.xml (13G/A #4, 2026-10-02: PRC citizen, 18.1% of Class A, 25M B→A conversion, proxy shares); https://www.revenuememo.com/p/who-owns-webull (ownership timeline + scrutiny corroboration)
- Note: wired board = Rippling `ats.rippling.com/webull/jobs` (live, JS-only
  render; jobs load via API — unchanged wiring). LCA employer "WEBULL
  TECHNOLOGIES INC." active (18 filings) — US hiring is real but the engineering
  mass sits in Hunan. Census should move from parent_covered/LCA-tier to
  cn_owned (KEEP).

## Amber Group (ambergroup.io)
- Verdict: cn_owned
- China ops / ownership / workforce: Founded 2017 in Hong Kong by six
  ex-Morgan Stanley fixed-income traders (Michael Wu, Thomas Zhu, Tony He,
  Wayne Huo, Luke Li, T.T. Kullander); current HQ is Singapore (SEC
  correspondence address + 2025 Nomura bio), with Hong Kong as the other major
  hub — HK SFC license acquisition in progress (WhaleFin Markets Ltd), HK VASP
  license application, Consensus-HK/Web3-Festival-HK 2026 presence, and a
  bilingual EN/繁體中文 site (© Amber Global Limited). Ownership: the listed
  arm Amber International Holding Ltd (NASDAQ: AMBR, ex-iClick Interactive)
  discloses in its FY2025 20-F that chairman/CEO Michael Wu beneficially owns
  36,233,237 Class B shares = 91.9% of aggregate voting power (2026-03-31) —
  also a Nasdaq "controlled company"; directors/officers as a group hold 92.5%
  of voting power; principal shareholder Amber Global Limited holds 66.0% of
  shares. Series B was led by China Renaissance (华兴资本). Workforce:
  headcount 894 (2023) → 225 (2024) → 295 (2025); live Lever board = 3 jobs
  (2 Hong Kong, 1 Singapore), and the HK "Quant Researcher - Statistical
  Arbitrage" posting requires "knowledge of written Chinese or spoken
  Mandarin". Per the task's criteria Hong Kong counts as China-based, and the
  controlling founders are HK-based Chinese — cn_owned (not china_hq since HQ
  is Singapore, not mainland).
- Sources: https://www.sec.gov/Archives/edgar/data/1697818/000110465926060362/ambr-20251231x20f.htm (20-F FY2025: Wu 91.9% votes, D&O 92.5%, Amber Global 66.0%, 295 employees, SG/HK/Dubai subsidiaries, HK license applications, PRC-defined-incl-HK risk factors); https://www.nomuranow.com/circle/circleds/attachments?type=AEJ&fileName=NIFA2025Bio_MichaelWu.pdf ("Amber Group ... headquartered in Singapore"; co-founder & CEO Michael Wu; About Amber Premium (NASDAQ: AMBR)); https://grokipedia.com/page/Amber_Group (founded 2017 HK by six ex-Morgan Stanley traders; China Renaissance-led Series B); https://api.lever.co/v0/postings/ambergroup?mode=json (3 live jobs: 2 HK + 1 SG; HK quant role requires written Chinese/Mandarin); https://www.ambergroup.io/about (bilingual site, © Amber Global Limited, HK 2026 events)
- Note: wired board = Lever `ambergroup` (live, 3 jobs — 2 HK, 1 SG; no US
  roles). Census `origin: china` already correct → KEEP as cn_owned. Gray:
  founders' exact nationalities are not on the public record (HK-based
  mainland-origin traders; PRC nationality law treats HK Chinese residents as
  Chinese nationals) — a HK company-registry/annual-return pull would settle.
  Wiring unchanged (small board, watch-mode fine).

## Wyze (wyze.com)
- Verdict: us_only (gray: private cap table unverified)
- China ops / ownership / workforce: Wyze Labs, Inc. is an American private
  company, HQ Kirkland, WA (~350 employees), founded 2017 by four ex-Amazon
  Echo-team employees — CEO Yun Zhang (张云) and CPO Dongsheng Song are
  ethnic-Chinese, Dave Crosby and Elana Fishman are not; under the S27 rules
  founder descent alone does not count. No Wyze-owned China legal entity,
  office, or R&D site is findable in any source: core software/firmware is
  designed in-house in the US, and hardware manufacturing is OUTSOURCED to
  Asian ODMs (historically Chinese partners such as Tianjin Hualai Technology
  — a third-party contract manufacturer, not Wyze ops). That ODM exposure is
  also CURRENTLY shrinking: Wyze began moving manufacturing to Vietnam in
  2024-2025 in response to US tariffs (May-2025 tariff bill of $255k on a
  $167k floodlight shipment; company announced it would complete the
  transition away from China "within approximately 60 days"). Ownership: no
  public evidence of Chinese majority shareholders — the disclosed investors
  (2021 round) include Chinese VC Eastern Bell Venture Capital only as a
  minority participant alongside US firms. US-operated evidence: 18 active LCA
  filings (incl. 2026) under "WYZE LABS INC".
- Sources: https://en.wikipedia.org/wiki/Wyze_Labs (American company, Kirkland WA; founders Yun Zhang/Dongsheng Song/Dave Crosby/Elana Fishman; ~350 employees; investors incl. Eastern Bell Venture Capital); https://grokipedia.com/page/Wyze_Labs (ODM outsourcing incl. Tianjin Hualai; 2024-25 Vietnam shift; "transition away from China" ~60 days; supply-chain team in Kirkland; US in-house software/firmware); https://apply.workable.com/wyze (live board)
- Note: wired board = Workable `wyze` (live, 0 open jobs at this check —
  Workable's old public JSON endpoints are gone, board renders JS-only; keep
  watch-mode + LCA signal). Verdict = REMOVE from the China roster (origin flag
  us_chinese_founder was already effectively us_only under new criteria).
  Gray: private cap table is unverified — if a credible source showed the
  Chinese-national founders retain majority ownership AND the user counts
  US-resident Chinese nationals' ownership as "chinese owned", the verdict
  would flip to cn_owned; as filed, the only China links are founder descent
  and third-party ODM manufacturing now migrating to Vietnam.

---

## Batch summary

| Company | Verdict | Key evidence (one line) |
|---|---|---|
| OKX | cn_owned (KEEP) | PRC-national founder-CEO Star Xu controls private OKX Group (ICE Mar-2026 minority stake only); 0 mainland roles on live 334-job board, SG/HK hubs, HQ San Jose. |
| Binance | cn_owned (KEEP) | CZ majority owner ($111.1bn Forbes Apr-2026, kept stake through DOJ deal) + PRC-national co-CEO Yi He; 48/302 live postings require "Bilingual English/Mandarin"; ~75% of jobs Asia/HK/Taipei. |
| Webull | cn_owned (KEEP) | SEC 20-F: PRC-citizen Wang Anquan 79.2% voting power; Hunan Weibu subsidiary = 863 employees = 62% of workforce (2025-12-31). |
| Amber Group | cn_owned (KEEP) | AMBR 20-F: founder Michael Wu 91.9% voting power; HK ops (SFC/VASP apps) + HK quant role requires written Chinese/Mandarin; founded HK by ex-Morgan Stanley traders. |
| Wyze | us_only (REMOVE, gray) | US company (Kirkland WA), no China entity/office; China ODM manufacturing being moved to Vietnam 2024-25; only Chinese link is founder descent (private cap table unverified). |

Fetch budget: ~35 live fetches total (DDG-lite snippets, Wikipedia ×4, SEC
EDGAR primary docs ×5, live ATS APIs ×4, Grokipedia ×3, company sites ×2),
max 6 attempts on any single company, no URL looped more than twice.
