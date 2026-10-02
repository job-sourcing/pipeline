# S24 Sanctions Sweep — US-sanctioned entities in the China-AI census
Decision: sanctioned companies are OUT OF SCOPE (user directive 2026-10-02).

Scope: all 187 census companies (20 wired_already, 7 wire_now, 133 probe_A, 0 probe_B, 22 origin_review, 5 rejected).
Verdict classes: `sanctioned_skip` (clear BIS Entity List / OFAC SDN or comparable US sanctions hit), `restricted_watch` (DoD 1260H / Commerce MEU or similar watch/restriction but NOT Entity List), `clear` (no known US sanctions).
Method: s20_kit_search live queries per flagged company + master-list cross-reference sweep; evidence recorded per row (which list, when added).
Census tiers for wired_already companies: sector_queue.json `counts.wired_already` (in-pipeline already; flagged here for the orchestrator's wiring decision).

| Company | Census tier | Sanctioned? | List + evidence | Verdict |
|---|---|---|---|---|
| Huawei | probe_A | YES | BIS Entity List eff. 2019-05-16 (Fed. Register 2019-10616, 84 FR 22961; +68 affiliates Aug 2019); also DoD 1260H | sanctioned_skip |
| iFLYTEK | probe_A | YES | BIS Entity List eff. 2019-10-09 (Fed. Register 2019-22210 — the 28-entity Xinjiang rule: Dahua, Hikvision, Megvii, SenseTime, Yitu, Meiya Pico, SenseNets) | sanctioned_skip |
| SenseTime | probe_A | YES | BIS Entity List eff. 2019-10-09 (FR 2019-22210, 28-entity Xinjiang rule); also OFAC NS-CMIC (non-SDN Chinese Military-Industrial Complex investment ban) | sanctioned_skip |
| Megvii | probe_A | YES | BIS Entity List eff. 2019-10-09 (FR 2019-22210); also OFAC NS-CMIC (surveillance-tech determination, controls Beijing Kuangshi) | sanctioned_skip |
| Hikvision | probe_A | YES | BIS Entity List eff. 2019-10-09 (FR 2019-22210); + OFAC NS-CMIC (E.O. 13959/14032) + DoD 1260H — OpenSanctions: "sanctioned, debarred, subject to export controls" | sanctioned_skip |
| Dahua Technology | probe_A | YES | BIS Entity List 2019-10-09 (CSL/Sanctiometer + sanctionschecklist.com; the 28-entity Xinjiang rule) | sanctioned_skip |
| Sugon | probe_A | YES | BIS Entity List eff. 2019-06-24 (FR 2019-13245; 5-entity supercomputer action: Sugon, Higon, Chengdu Haiguang IC + Microelectronics, Wuxi Jiangnan Inst.) | sanctioned_skip |
| Hygon | probe_A | YES | BIS Entity List 2019-06-24 (same Sugon/Higon action — Hygon's design arms Chengdu Haiguang Integrated Circuit + Chengdu Haiguang Microelectronics are listed entities) | sanctioned_skip |
| CloudMinds | probe_A | YES | BIS Entity List May 2020 (Cloudminds Inc. + Cloudminds (Hong Kong) Ltd — Wikipedia/sanctionschecklist; May-2020 33-entity rule) | sanctioned_skip |
| YITU Technology | probe_A | YES | BIS Entity List eff. 2019-10-09 (FR 2019-22210 — one of the 8 surveillance/AI firms; Yitu Technologies also on sanctionschecklist/semidata) | sanctioned_skip |
| Zhipu AI | probe_A | YES | BIS Entity List Jan 2025 (Fed. Register 2025-00704; pre-confirmed live by orchestrator, corroborated by sanctionschecklist + sanctions-finder) | sanctioned_skip |
| Cambricon | probe_A | YES | BIS Entity List (pre-confirmed live by orchestrator via sanctionschecklist.com + sanctions-finder.com) | sanctioned_skip |
| SOPHGO | wire_now | YES | BIS Entity List Jan 2025 (FR 2025-00480, 16-entity rule; Sophgo Technologies Ltd + Qingdao Sophgo + Sophgo Technologies Pte. — Huawei-linked chip acquisition concern; The Wire China confirms) | sanctioned_skip |
| Bitmain | probe_A | NO | No Entity List/OFAC hit (searches return only generic OFAC tools; Bitmain cut ties with blacklisted Sophgo spinoff) — note: spinoff SOPHGO IS listed (see above) | clear |
| Moore Threads | probe_A | YES | BIS Entity List Oct 2023 (FR 2023-23048, 13-entity rule; Wikipedia + DataCenterDynamics confirm; job cuts followed blacklisting) | sanctioned_skip |
| Biren Technology | probe_A | YES | BIS Entity List Oct 2023 (FR 2023-23048 — Beijing/Guangzhou/Hangzhou Biren entries; Wikipedia confirms) | sanctioned_skip |
| Inspur | probe_A | YES | BIS Entity List 2025-03-28 (FR 2025-05427, 90 FR — six Inspur Electronic Information entities added by ERC) + DoD 1260H (Inspur Group Co., Ltd.) | sanctioned_skip |
| CloudWalk | probe_A | YES | OFAC NS-CMIC / CMIC-EO13959 program (sanctionssearch.ofac.treas.gov id=33112; OpenSanctions "sanctioned and debarred" — surveillance-tech facial-recognition determination). Not on BIS Entity List (not in the Oct-2019 rule). Same company as "CloudWalk Technology" entry below. | sanctioned_skip (OFAC CMIC; see note) |
| CloudWalk Technology | probe_A | YES | OFAC NS-CMIC / CMIC-EO13959 (Cloudwalk Technology Co., Ltd. — sanctionschecklist OFAC_33112_CON + Sanctiometer). Not on BIS Entity List. | sanctioned_skip (OFAC CMIC; see note) |
| Intellifusion | probe_A | YES | BIS Entity List (sanctionschecklist.com BIS_9fd7cd8b270d + sanctions-finder; Shenzhen Intellifusion/Intellifusion Technologies — Chinese surveillance-AI wave 2019-2020) | sanctioned_skip |
| Qihoo 360 | probe_A | YES | BIS Entity List 2020-05-22 (the 33-entity rule; KrASIA "Qihoo 360 added to US Entity List" + Yicai + globaltradeandsanctionslaw.com) | sanctioned_skip |

## S24 COMPLETION (orchestrator addendum — the sweep agent's disk-first
## table above survived its empty response; the cross-check finished here)

- **New H3C / H3C**: New H3C Semiconductor Technologies Co., Ltd. IS on the
  BIS Entity List (sanctions-finder + sanctionschecklist + getembargo) → sanctioned_skip.
- **4Paradigm AND Fourth Paradigm**: same company (4Paradigm's official
  English name — census duplicate). Entity-Listed (sanctions-finder +
  SCMP: "files for IPO again after US sanctions") → both sanctioned_skip.
- **Bitmain**: clear (no Entity List/OFAC hit; spinoff SOPHGO IS listed).
- **Cleared by master-list cross-check** (no hits found): Iluvatar Corex,
  MetaX, Enflame, Westwell, TongDun, Terminus Group, Deepglint,
  Allwinner, Rockchip, Amlogic, Canaan, Innosilicon, Kunlunxin, and the
  remaining census names (146 cross-checked 2026-10-02).
- **DJI**: restricted_watch (DoD 1260H + MEU, NOT Entity List).
- **SOPHGO was in wire_now** — never wired (its successfactors wall
  deferred it in S23, which by luck kept the pipeline clean). Removed
  from wire_now; sanctioned_skip.
- Census verdicts applied to sector_queue.json (new tiers
  sanctioned_skip / restricted_watch) + probe evidence files
  (s24_verdict/s24_note). Policy recorded in DECISIONS.md.

**Final sanctioned set: 24 census entries (21 distinct companies —
CloudWalk and 4Paradigm/Fourth-Paradigm are census duplicates counted
twice). Nothing sanctioned was ever wired into the 83-watch config
(verified 2026-10-02).**
