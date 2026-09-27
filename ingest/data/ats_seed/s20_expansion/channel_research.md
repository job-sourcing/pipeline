# S20 expansion — channel research: discovering ALL Chinese companies hiring US roles

> Directive: exhaustive, systematic, at-scale discovery of Chinese companies
> (origin mainland China / HK) hiring US-based roles — all industries
> (tech, e-commerce, EV/auto, biotech, consumer, gaming, logistics,
> finance). NOT ad-hoc name lists. Research first: which channels can
> enumerate the universe, and how do we mine them?
>
> Research date: 2026-09-26 (S20). Live probes + web search (2 batches,
> 26 queries) + the repo's own capability inventory.

## 1. Channel inventory and verdicts

| # | Channel | What it enumerates | Scale | Access (measured) | Chinese-classification path | Verdict |
|---|---------|--------------------|-------|-------------------|---------------------------|---------|
| 1 | **DOL LCA disclosure files (quarterly xlsx)** | EVERY US employer filing H-1B/H-1B1/E-3 LCAs: employer name, job title, SOC, worksite city/state, wage | ~50k+ distinct employers/yr (DOL's own estimate ~50k; modern FY larger); 83–251MB per quarter file | **SOLVED** — our `scripts/h1b_extract.py` + `h1b-extract.yml` GHA workflow already download+parse these exact files quarterly (run #16, 30 employers); full-file transport = GHA runner (HK is Akamai-403; supabase proxy caps at 10.5MB < file size) | LCA records carry EMPLOYER NAME only — classification needs a second signal (seed-dictionary fuzzy join + LLM batch classify on name+titles+worksite+SOC). Neutral-name subsidiaries ("TikTok Inc", "WeRide Corp", "GE Appliances (Haier)") FAIL name heuristics → must lean on LLM + cross-channels | **ENUMERATION-BACKBONE** — the only near-complete universe of US-hiring employers; captures industries the ATS directories miss (biotech CROs, logistics, banks, manufacturers) |
| 2 | **ATS board directories (in-repo)** | Companies with public job boards on greenhouse / ashby / lever (+workable/workday seeds) | 15,872 slugs seeded; **9,705 LIVE** boards probed (gh 5,294 / ashby 2,437 / lever 1,964); **7,704 carry resolved company names** (gh 5,267 + ashby 2,437; lever postings API serves no name — needs HTML-title probe) | SOLVED in-repo (`ingest/data/ats_directory.db`, `build_ats_directory.py`; slug source = Feashliaa/job-board-aggregator dataset) | LLM batch-classify the 7,704 NAMES (one-time, ~80 batches) → Chinese-origin flags → board specs already in hand (config-only seeding!) | **ATS-NATIVE PATH** — directly yields pipeline-wireable boards; coverage limited to these ATS platforms |
| 3 | **CGCC-USA (China General Chamber of Commerce – USA)** | Chinese companies operating in the US (their membership + annual survey universe) | ~1,500–4,000 entities (reports reference member counts; full member list not published as data) | cgccusa.org reachable; annual Business Survey Report PDFs (2024/2025) public via PRNewswire/site | Report PDFs name example companies; membership directory itself is gated | **SECONDARY/VALIDATION** — good for recall backstop and industry mix, not a download |
| 4 | **Vertical curated lists (web)** | Sector-specific Chinese-company-in-US lists: biotech (WuXi AppTec/Bio, GenScript, Zai Lab), EV/battery (Gotion, CATL, BYD, Chery, GWM, NIO, Zeekr, Polestar), consumer/DTC (Anker, EcoFlow, Roborock, Dreame, Insta360, Temu, Shein), banks (ICBC, BoC, CMB, ABC, BOCOM...), gaming (miHoYo/HoYoverse, Tencent, NetEase, Perfect World), logistics (COSCO, SF), AI labs (DeepSeek, Moonshot, Zhipu, MiniMax, Shengshu, 01.AI) | hundreds of names across searches | web-search (quality varies; Chinese-language queries surface Zh lists) | Names are already the signal — feed the SEED DICTIONARY | **SEED DICTIONARY SOURCE** — high-precision candidates, zero enumeration completeness |
| 5 | H-1B aggregators (MyVisaJobs, H1BGrader, h1bdata.info) | Same DOL data, re-served with UI | same as #1 | **403 from HK egress** (measured live); kit ladder could open them | they add employer-country facets on some views | **REDUNDANT — SKIP** (middlemen of #1; our DOL transport is better) |
| 6 | LinkedIn / Indeed / Glassdoor company search | The full jobs universe faceted by company | enormous | auth-walled / blocked classes (S11 methodology: Indeed GeoTrust-blocked, LinkedIn authwall — only guest cards work, which we already use for corroboration) | — | **CORROBORATION ONLY** (already wired into the pipeline) |
| 7 | Wikipedia `Category:Companies of China` tree | All notable Chinese companies (not US-hiring-filtered) | ~thousands of articles | reachable; category API/dumps enumerable | names → seed dictionary + LLM | **SEED DICTIONARY SOURCE** (board) |

## 2. The strategy that emerges (three-instrument convergence)

No single channel is both enumeration-complete AND Chinese-labeled. The
convergent design:

1. **LCA backbone** (#1): full-quarter employer universe (ALL industries,
   measurable US-hiring intensity via filing counts/worksites/wages).
   Classification: two-tier (seed-dictionary fuzzy join → residual LLM
   batch classify). Output: the CENSUS of Chinese companies hiring in
   the US, ranked by US hiring intensity.
2. **ATS-native cross-check** (#2): classify the 7,704 named boards →
   every Chinese company on greenhouse/ashby/lever with a LIVE board and
   job count → config-only pipeline seeding (no discovery needed — the
   board spec IS the deliverable).
3. **Seed dictionary** (#3+#4+#7 + our roster 30 + s16-census defer
   list): the high-precision join key that makes tier-1 classification
   recall high before any LLM spend.

**Joins**: LCA∩ATS = direct seed with hiring evidence. LCA-only =
needs board discovery (web search / probe — the S16 census method,
generalized). ATS-only = Chinese company with a live board (US-share
must be probed — the countryfilter phase already does this).

## 3. Known-limitations register (honesty)

- LCA misses employers that hire ONLY citizens/PR (rare for CN
  companies' US arms — most import CN staff on H-1B — but nonzero).
- LCA overweights H-1B-heavy consultancies/body-shops; the classifier
  must tag `product_company` vs `staffing/consulting` class or the wave
  drowns in noise (hundreds of CN-owned IT consultancies exist).
- ATS directories miss: custom careers sites (the bytedance/alibaba/
  tripcom/xiaohongshu class), ICIMS/Phenom/SuccessFactors/Taleo boards
  (Lenovo, WuXi AppTec, NIO, DJI...), and boards created after the
  directory snapshot.
- Lever names: 1,964 live boards with no name — needs an HTML-title
  probe pass (cheap: GET title per slug).
- CGCC/bank/vertical lists: no API, manual distillation.

## 4. Decided instrument plan (this session)

| Instrument | Script | What it does |
|---|---|---|
| A | `scripts/s20_classify_ats_directory.py` | LLM-batch classify 7,704 named boards (+ lever title probe) → `ats_chinese.csv` |
| B | GHA h1b-extract `--all-employers` mode | full-quarter employer aggregation → `lca_employers.csv` |
| C | `scripts/s20_seed_dictionary.py` + join | seed dict (roster + verticals + wiki tree + s16 defers) → tier-1 LCA join, tier-2 LLM residual → `candidates.csv` |
| D | probe+seed wave | candidates → board discovery (S16 census method) → config seeds |

## 5. Evidence log (live probes, 2026-09-26)

- dol.gov performance page: HK direct = Akamai-blocked (known matrix);
  quarters+files reachable via GHA (proven by run #16); file regex
  `LCA_Disclosure_Data_FY\d{4}_Q\d\.xlsx`; sizes 83–251MB/quarter
  (from h1b_extract.py design notes).
- myvisajobs.com → 403; h1bgrader.com → 403 (HK egress, plain curl).
- ats_directory.db: 15,872 rows / 9,705 live / 7,704 named (measured
  today via sqlite).
- Web search batches: /tmp/s20_search1.json + /tmp/s20_search2.json
  (26 queries; key vertical names captured above).
- CGCC-USA: cgccusa.org + PRNewswire 2025 report announcement (report
  exists; member data gated).
