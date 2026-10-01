# S23 Census — Segment B: Computer Vision / Speech / NLP / AI-Chips / AI-Infra

File: `seg_vision.jsonl` (69 records) — built by sub-agent S23-census-B.
Method: prior-domain knowledge (COMPLETE over perfect) + 26 web-search verifications (s20_kit_search, cap ~30) + LCA FY2026Q3 cross-reference of all candidate names (2 batch passes, ~110 name variants over 59,649 employers).

## Counts
- **Total: 69 companies** (target was 40–60; went complete)
- Segments: vision 22 · chips 23 · nlp 10 · speech 4 · ai_infra 6 · robotics 2 · edge 2
- Origin: china 67 · us_inc_chinese_founders 2 (Lightelligence, Innovusion)
- Status: active 67 · merged 2 (Sogou→Tencent, DeePhi→Xilinx/AMD)
- Confidence: high 49 · medium 20 (no low)
- already_wired: 6 (Horizon Robotics, Baidu, Tencent, Alibaba, ByteDance, OmniVision — marked, not re-researched)
- Not-wired new candidates: 63

## US-signal highlights (best probe/wire candidates)
1. **Insta360 / Arashi Vision** — STRONGEST: US-inc parent (Arashi Vision Inc), "Arashi Vision US LLC dba Insta360" is a myvisajobs visa sponsor; LCA: **ARASHI IMAGE INC (LIC, NY) 3 filings**; 67 US LinkedIn roles; `insta360.com/jobs`.
2. **Black Sesame Technologies** — **BLACK SESAME TECHNOLOGIES INC (San Jose, CA) 8 LCA filings**; live San Jose postings (AI Framework eng) on LinkedIn/Indeed/Glassdoor; careers `blacksesame.com/en/join-us/`.
3. **Tuya Smart** — NYSE: TUYA; Santa Clara office (Glassdoor E2412662); 615 US-tagged LinkedIn roles; `tuya.com/careers`.
4. **CloudMinds** — CloudMinds Technology Inc, Silicon Valley + Los Angeles (Built In LA employer); cloud robotics.
5. **Mobvoi** — dedicated US jobs page `mobvoi.com/us/jobs/us.html`; HK 2341; US consumer line.
6. **SenseTime** (explicitly requested) — `sensetime.com/en/join-us`; historical NJ US sub wound down post-2019 Entity List, but North-America BD head posting 2024-25 + LinkedIn US roles; SenseCore alias for AI-infra.
7. **SOPHGO** — SuccessFactors board at `jobs.sophgo.com` (directly probeable); SOPHGO Inc Santa Clara.
8. **Inspur** — "Inspur USA" myvisajobs employer entry (historic filings); 120 US LinkedIn roles.
9. US-LISTED cos (US-secular signal even w/o US headcount): Xiao-I (AIXI), Canaan (CAN, +Canaan US Inc LCA×2), Hesai (HSAI), ECARX (ECX), Tuya (TUYA).
10. LCA one-filers (Detroit/SV auto-perception): RoboSense Inc (Plymouth MI), Innovusion Inc (Sunnyvale CA), Amlogic USA (Mountain View).

## LCA FY2026Q3 hits (8)
| company | LCA employer | filings | HQ |
|---|---|---|---|
| Insta360 | ARASHI IMAGE INC. | 3 | Long Island City, NY |
| DJI | DJI TECHNOLOGY INC. | 3 | Burbank, CA |
| Black Sesame | BLACK SESAME TECHNOLOGIES INC | 8 | San Jose, CA |
| Bitmain | BITMAIN DELAWARE HOLDING COMPANY, INC. | 2 | Irvine, CA |
| Canaan | CANAAN US INC. | 2 | Campbell, CA |
| Amlogic | AMLOGIC (USA) LTD. | 1 | Mountain View, CA |
| RoboSense | ROBOSENSE INC. | 1 | Plymouth, MI |
| Innovusion | INNOVUSION, INC. | 1 | Sunnyvale, CA |

No LCA hit for: SenseTime, Megvii, iFlytek, Cambricon, Hygon, Moore Threads, Biren, Enflame, CloudMinds, Tuya (office exists but no FY26 filings), Hesai.

## Name-collision traps (for probe/adjudication)
- "CloudWalk, Inc." / lp.cloudwalk.io = Brazilian fintech, ≠ CloudWalk 云从科技.
- "Canaan Partners" (careers.canaan.com) = US VC, ≠ Canaan Inc (嘉楠, platform.canaan.com).
- HIPA PHOTONICS INC (Santa Clara, 3 filings) name-adjacent to Hesai — attribution unverified, left as lead only.
- "Moore Threads" myvisajobs page exists but no FY26 LCA filings (auto-generated page, weak).
- ABADJIS SYSTEMS LTD (CA) shares nothing with DJI beyond search noise; only DJI TECHNOLOGY INC is DJI's.

## Known gaps / next actions
- Entity-List cos (SenseTime, Megvii, Yitu, CloudWalk, Intellifusion, Hikvision, Dahua, iFlytek, Sugon, Hygon) have no US hiring now — probe will mostly find CN-only boards (zhiye.com / feishu / moka).
- Feishu-hiring company found: **Westwell** (westwell.jobs.feishu.cn) — s18 feishu prober can consume directly.
- SuccessFactors company found: **SOPHGO** (jobs.sophgo.com).
- Zhiye (51job) ATS companies: iFlytek, Unisound, AISpeech, AInnovation.
- Verify-status flags: CloudMinds (financial stress reports), Megvii listing status (private; IPOs lapsed), Vimicro listing, MetaX STAR IPO completion, Biren HK IPO status.
- Huawei row intentionally included for ai_infra completeness but flagged for cross-segment dedup (likely conglomerate/telecom segment owns it); DJI similarly flagged (drone/hardware segment).
- LLM-lab companies (Zhipu, DeepSeek, MiniMax, Moonshot, 01.AI, StepFun, Baichuan) intentionally excluded — belongs to sibling census segment; Minimax/Moonshot already wired.
- SOPHGO/Bitmain US footprints interlinked (SOPHGO spun from Bitmain; both have CA entities).
