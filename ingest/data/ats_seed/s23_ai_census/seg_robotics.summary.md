# S23 AI Census — Segment C: Robotics-AI / Autonomous Driving / Vertical AI

**File:** `seg_robotics.jsonl` — **70 companies** (one record per company)
**Agent:** S23-census-C · **Date:** 2026-09-30 (sandbox) · **Sources:** s20_kit_search (30 queries), LCA FY2026Q3 cross-reference (59,649 employers), s20_census probes, s18/s22 wave research, board_watch wired registry.

## Counts

| axis | breakdown |
|---|---|
| total records | **70** |
| robotics-class | 27 (robotics 17, robotics_sensors 4, robotics_home 4, robotics_agri 2) |
| autonomous-driving-class | 19 (autonomous_driving 15, ev_automaker_ai 4) |
| vertical-AI | 24 (medical imaging 7, pharma 6, enterprise SaaS 4, fintech 3, games 2, education 1, education/voice 1) |
| origin | china 63 · us_inc_chinese_founders 7 (AutoX, PlusAI, Sunday Robotics, CreateAI/TuSimple, Faraday Future, Curacloud, Wetour*) |
| already_wired | 11 true (DeepRoute, Pony, WeRide, PlusAI, Horizon, Ant, United Imaging, HoYoverse, NIO, XPeng, Faraday Future) — 59 to research/wire |
| LCA evidence | 23 records with US-entity H-1B filings |
| ats_hint found | 20 records (incl. workable:qcraft, ashby:sunday, feishuhire:momenta/xtalpi/agirobot, gusto:squirrel-ai-learning, consider:autox, zhiye:iflytek…) |
| confidence | high 40 · medium 24 · low 6 |

## Top US-signal findings (wire candidates ranked)

1. **QCraft 轻舟智航** — LIVE Workable board `workable:qcraft` + San Francisco office. Best AD wire candidate.
2. **VisionNav 未来机器人** — VISIONNAV ROBOTICS USA INC, Lawrenceville GA/Atlanta lab, **19 LCA filings**, live US eng roles.
3. **Pudu 普渡** — inaugurated **US HQ in Dallas TX Apr 2026** + PUDU ROBOTICS US INC LCA. Fast-rising.
4. **Sunday Robotics** — ashby:sunday 24-26 US jobs + **22 LCA filings (median $285k)**. PARKED on origin policy (US-inc Chinese founders — ALOHA creators).
5. **Momenta** — MOMENTA USA INC (Sunnyvale, 2 LCA filings); `feishuhire:momenta` portal alias already exists in adapters — near-free wire.
6. **Agrib/robotics sensors cluster with US entities**: Hesai (Sunnyvale roles, own-site board), RoboSense (Plymouth MI LCA), Innovusion (Sunnyvale LCA), Orbbec (Troy MI LCA + static-WP US careers page), Dobot (Carrollton TX LCA), Dreame (Culver City LCA), Ecovacs (San Mateo LCA + ecovacs.com/us/careers), Segway Inc (Plano TX LCA + careers.jsp), Keenon (CA entity 2022, Irvine office), DJI (Burbank CA LCA, borderline AI-first).
7. **Vertical-AI US hiring (no board yet)**: HitGen Inc (US medicinal-chemistry director hires), Accutar (Cranbury NJ + Brooklyn NY careers site), Insilico Medicine (NY office; dual HK/ADU HQ), Infervision-US (VP Sales US), Keya Medical NA (Seattle LCA), Kyligence (San Jose office + kyligence.io/careers), Squirrel AI (Gusto board, remote-US roles, Bellevue WA LCA), XtalPi (Somerville MA LCA; feishu CN portal empty).
8. **AgiBot 智元** — AGIBOT USA LinkedIn + hiring US sales manager (earliest US footprint of the humanoid cohort).

## Homonym / attribution traps (do NOT wire blindly)

- `ashby:fourier` = hydrogen-systems/India homonym, NOT 傅利叶智能 (REFUTED in s20 probe).
- LCA `NARWAL INC` (Cincinnati OH) = data-services firm, NOT the Narwal robot-vacuum maker.
- LCA `KEPLER COMPUTING INC` (San Jose) = likely US chip startup, NOT Kepler Robot (Shanghai).
- LCA `INSILICOM LLC` ≠ Insilico Medicine.
- `UBTech` searches surface Uintah Basin Technical College (US edu homonym).
- XtalPi feishu portal is empty (0 jobs) — CN hiring is on moka (~124 roles).

## Intentional exclusions (other segments)

- Horizontal CV platforms: SenseTime, Megvii, CloudWalk, Yitu → platform segment.
- LLM/foundation-model layer: Moonshot, MiniMax, DeepSeek, StepFun, SiliconFlow, Shengshu, etc. → LLM segment (some already in board_watch).
- LLM-app layer (Butterfly Effect/Manus, Genspark, Monica) → skipped per brief ("skip if uncertain").
- Games/e-comm giants not AI-first and already wired (Tencent, NetEase, Poizon, Webull) → skipped.
- No Chinese legal-AI company with US hiring surface was found — segment noted as empty (see notes in rows for AI-legal adjacency).

## Schema

Per line: `{name, cn, aliases, segment, hq, domain, careers_hint, ats_hint, us_signal, lca{employer,filings}|null, origin(china|us_inc_chinese_founders), already_wired, status, confidence, notes}` — validated: 70 lines, no duplicate names, all fields present.
