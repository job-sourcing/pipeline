# S23 AI Census — Segment: LLM / Foundation-Model / GenAI (seg_llm)

**Agent:** S23-census-A · **Date:** 2026-10 · **File:** `ingest/data/ats_seed/s23_ai_census/seg_llm.jsonl` (72 records, one JSON object per line)

## Counts

| Metric | Value |
|---|---|
| Total records | **72** |
| Not already wired (new) | 63 |
| Already wired (marked, not re-researched) | 9 (Moonshot, MiniMax, Shengshu, Baidu, Alibaba, ByteDance, Tencent, JD.com, Xiaohongshu) |
| origin = china | 52 |
| origin = us_inc_chinese_founders | 20 (incl. UK-inc Haiper — noted) |
| status = active / merged | 70 / 2 (Lepton AI → NVIDIA; OmniML → NVIDIA) |
| confidence high / medium / low | 38 / 25 / 9 |
| LCA (H-1B FY2026Q3) hits | 19 records with verified employer filings |
| ATS slugs discovered | 12 records |
| Search queries used | 30 / ~30 budget |
| AV companies from wired list (DeepRoute, Pony.ai, WeRide, PlusAI, Horizon) | intentionally excluded — robotics/AD segment, not LLM |

## LCA cross-reference results (H-1B FY2026Q3, 59,649-employer file)

Genuine employer hits for this segment:

- **Tencent America LLC** — 58 filings, Palo Alto; **26 of them are "Hunyuan AIGC Algorithm Researcher (World Model Foundation / Omni-Modal)"** → Tencent America is actively hiring US GenAI researchers (plus Tencent Cloud LLC, 1)
- **Together Computer, Inc.** (Together AI) — 47 filings, SF
- **Fireworks.ai, Inc.** — 45 filings, Redwood City
- **ByteDance Inc.** (576) + TikTok Inc. (394) + TikTok USDS JV (178) — wired
- **Cognition AI Inc.** (+ comma variant) — 9 filings, SF
- **Arena Intelligence Inc.** — 7 filings, SF → **confirmed = LMArena legal entity** (Bloomberg: "doing business as LMArena")
- **World Labs Technologies, Inc.** — 6 filings, SF
- **LangGenius, Inc.** (Dify) — 5 filings, Menlo Park
- **Boson AI USA Inc.** — 4 filings, Santa Clara (HK-inc remote-first company of Mu Li 李沐)
- **Genspark Inc.** — 4 filings, Palo Alto
- **Hyperbolic Labs, Inc.** — 3 filings, SF
- **JD.com American Technologies Corp.** — 2 filings, Mountain View (wired)
- **Nexa AI, Inc.** (2, Cupertino) · **Aizip, Inc.** (2, Saratoga) · **Akool Inc** (2, Palo Alto)
- **Zilliz Inc.** — 1 filing, Redwood City · **NetEase Information Technology Corp.** — 1 filing, Irvine
- **Alibaba Cloud US LLC** (16) + Alibaba.com US (10) + Alibaba Group (U.S.) Inc (2) — wired
- **ZERO filings** despite expectations: Baidu (US hiring appears paused), Huawei (sanctioned/frozen), Xiaohongshu, Moonshot, MiniMax, Shengshu, DeepSeek, Zhipu, SenseTime, iFlytek, Meituan, Kuaishou, 01.AI, Baichuan, StepFun, Butterfly Effect/Manus

## US-signal highlights (best probe candidates, non-wired)

1. **Dify (LangGenius)** — global US/Singapore/China/Europe team, "remote-friendly roles in the US" (join.dify.ai), 5 US LCA filings. Highest-priority remote-friendly target.
2. **Boson AI** — HK-inc, remote-first, US entity (Santa Clara) already sponsoring H-1B; Lever board live (`lever:bosonai`).
3. **Fireworks AI / Together AI** — 45 + 47 LCA filings; large US hiring (Fireworks careers page; Together on Greenhouse `greenhouse:togetherai`).
4. **Genspark (MainFunc)** — Ashby board live, 4 LCA filings, Palo Alto HQ.
5. **Meituan (LongCat)** — dedicated **global AI talent program** at zhaopin.meituan.com/longcatprogram ("面向全球AI人才") — no US entity yet, but strongest big-tech global-recruiting signal.
6. **Manus (Butterfly Effect)** — Singapore HQ since 2025-06, English careers board live (careers.manus.im/en/jobs), overseas hiring expansion.
7. **AISphere (PixVerse)** — $2B+ valuation video-gen, global consumer product, Singapore overseas entity; no US entity yet.
8. **LMArena / Cognition / World Labs / Pika / Hyperbolic / Creatify / Nexa / Aizip / Akool / Zilliz / CAMEL-AI / MyShell / Haiper / Sonauto** — US-inc (or UK) Chinese-founder LLM companies; several with live boards: `ashby:cognition`, `ashby:hyperbolic`, `ashby:pika`, `ashby:genspark`, `ashby:creatify`, `lever:jina-ai`, `lever:zilliz`, `greenhouse:worldlabs`.

## Segment caveats

- **Sanctioned / entity-listed (low US-job relevance):** Zhipu AI (Jan 2025), SenseTime, iFlytek, Megvii, CloudWalk, YITU (Oct 2019), Fourth Paradigm (2022), Huawei (frozen), Qihoo 360 (sanctions history).
- **Pivots:** 01.AI wound down its LLM open platform → enterprise agents (Wanzhi 2.5), IPO filing reported. Butterfly Effect → Singapore. MyShell → US. Lepton/OmniML merged into NVIDIA.
- **Origin policy is a user decision:** 20 records flagged `us_inc_chinese_founders` (incl. mixed founding teams: Cognition, LMArena, Hyperbolic, Together; and UK-inc Haiper).
- Low-confidence records to verify in probe wave: Sonauto (origin unverified), APUS (LCA entity linkage unverified), AISpeech, DataGrand, DeepLang, Unisound, OrionStar, YITU, CAMEL-AI.

## Method notes

- LCA: single-pass substring search over `s20_census/lca_employers_fy2026q3.json` for ~90 name variants; noise (e.g. Skyworks Solutions, Butterfly Research, Pika International, "operations" matches) manually filtered.
- Web: 30 DuckDuckGo queries via `scripts/s20_kit_search.mjs` (careers/US-signal verification); cross-checked prior artifacts in `s18_census/searches/` (Dify, Genspark, StepFun, 4Paradigm, Monica, SiliconFlow).
- Incremental writes: file built in 5 appends (15+18+17+13+9 records) per crash-resilience protocol.
