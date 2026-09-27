# S17 Census — Major CN AI startups: remote-friendly / US-based jobs

**Date:** 2026-09-25 · **Method:** ATS-directory queries + live board probes
(feishu-hire API, lever API, own-site APIs, web search) + full-board pagination.

## The API discovery (the round's big unlock)

**Feishu-Hire jobs boards** (`{portal}.jobs.feishu.cn`) — the standard ATS for
CN AI startups — serve a **public JSON API** (reverse-engineered via browser
network capture):

1. `POST https://{portal}.jobs.feishu.cn/api/v1/csrf/token` → `{"data":{"token":...}}`
2. `POST https://{portal}.jobs.feishu.cn/api/v1/search/job/posts?keyword=&limit=10&offset=0&...&portal_type=6&portal_entrance=1`
   with headers `X-Csrf-Token: {token}` and **body** `{"offset": N, "limit": 10}` →
   `{data: {job_post_list: [...], count: total}}`
   - ⚠️ **offset AND limit must be in the BODY** (URL params are silently
     ignored → the naive probe sees only the first 10 of N jobs — census
     undercount trap); body limit serves up to **200/call** (peer-review
     live-verified: minimax 185/185 in ONE request; agirobot pages 200)
   - `data.count` is the authoritative total; the envelope's `code` MUST
     be 0 (a 200-with-code≠0 is an error, never an empty page)
3. Detail: `GET /api/v1/job/posts/{id}?portal_type=6&with_recommend=false` →
   `{data: {job_post_detail: {title, description, requirement, city_list,
   recruit_type, publish_time (epoch-ms → exact dates), ...}}}`
   (search rows carry null description/requirement — details are per-id fetches)

Same throne-platform family as the ByteDance jobs API. Plain urllib works
(no impersonation, no signature — the `_signature` URL param is optional).

## Findings table (all notable CN AI startups, US/remote-job availability)

| Company | Surface | Full board | US jobs | Verdict |
|---|---|---|---|---|
| **MiniMax (Hailuo)** | feishu `vrfi1sk8a0` | 185 | **15 SF-touching** (Research Lead LLMs, AI Talent Lead, Global Marketing Mgr, Global DevRel, Global Ent AE, FDE, 10x Team…) | **WIRE (new feishuhire adapter class)** |
| **Horizon Robotics** | **lever `horizon`** | 1 | **1 (Research Scientist, Cupertino CA, hybrid)** | **WIRE (config-only, existing lever adapter)** |
| **Shengshu / Vidu** | feishu `shengshu` | 106 | 1 (海外大客户销售 / Overseas KA Sales, SF) | WIRE (same adapter; tiny but real) |
| Moonshot AI (Kimi) | ashby | — | 4 | already wired (S16) |
| Zhipu AI (Z.ai) | feishu `zhipu-ai` | 237 | 0 (all CN) | documented |
| 01.AI | feishu `01ai` | 55 | 0 | documented |
| Baichuan | feishu `cq6qe6bvfr6` | 15 | 0 | documented |
| ModelBest (面壁) | feishu `modelbest` | 96 | 0 | documented |
| AgiBot (智元) | feishu `agirobot` | 834 | 0 (1 London) | documented |
| SenseTime | feishu `sensetime` | 85 | 0 (sanctioned; 1 SG) | documented |
| DeepSeek | own site (CN) | — | 0 (China-only hiring, by policy) | documented |
| StepFun | CN campus channels | — | 0 | documented |
| Manus (Butterfly Effect) | no structured board (SPA, no public API; manus.im/careers client-rendered, no job JSON) | — | remote-first company but no machine-readable surface | documented |
| Unitree | own public API `api.unitree.com/website/job/list` | 26 | 0 (ALL Hangzhou) | documented (API pinned for future) |
| PixVerse (AIsphere) | careers → LinkedIn company page only | — | US jobs exist (Santa Clara) but LinkedIn-only | documented |
| QCraft | feishu-ish careers (CN) | — | 0 | documented |
| Genspark | CF-blocked SPA | — | ? | documented |
| Kwai (Kuaishou intl) | **Workday `kwai|wd3|?`** | — | WAF-406 from our egress; Google-indexed titles look Brazil-ops-heavy; US R&D Center site (usrdc.kuaishou.com) is a static EEO page, no jobs | future candidate — try a GHA (US egress) probe |
| Fourier | ashby `fourier` | 8 | 0 (India) | documented |
| NIO | feishu `nio` | 2,091 | 0 US (verified at full pagination) | documented |
| DCAR (懂车帝, ByteDance auto spinoff) | feishu `dcar` | 681 | 0 | not relevant |
| Megvii / CloudWalk / Yitu / iFlytek / Hikvision | — | — | 0 (US Entity List — no US hiring) | excluded by sanctions |
| Enflame / Cambricon / Biren / Moore Threads / MetaX / Black Sesame | CN | — | 0 (export controls) | excluded |
| UBTech / Rokid / Galbot / PPIO / BAAI / HiDream / Kunlun Skywork | CN | — | 0 found | documented |

## Excluded-with-evidence (non-AI false positives)
- `manus-meta.com` = Dutch hardware company (Manus Meta), NOT the Manus AI agent
  company (manus.im) — name-collision trap.
- `jobs.b.capital` = B Capital Group (VC), NOT Baichuan.
- `zhipu-ai` ashby match earlier was spurious; real surface = feishu.

## Feishu portal index (for future boards)
`vrfi1sk8a0`=MiniMax · `zhipu-ai`=Zhipu · `01ai`=01.AI · `cq6qe6bvfr6`=Baichuan ·
`modelbest`=面壁 · `agirobot`=AgiBot · `sensetime`=SenseTime · `shengshu`=Shengshu ·
`nio`=NIO · `dcar`=懂车帝 · `moonshot`=Moonshot (0 public; intl on ashby) ·
`horizon` (lever, not feishu) = Horizon Robotics.

## Peer-review round 1 additions (2026-09-26)

- **Xiaohongshu / RedNote — THE census miss, now wired** (custom:xiaohongshu
  adapter): own API `job.xiaohongshu.com/websiterecruit/position/
  pageQueryPosition` (POST, no anti-bot, one-call board with duty+qualification
  in list rows, exact publishTime); SERVER-side workplace filter code **840 =
  美国**; **20 US rows** (rednote iOS/backend engineers, TnS Policy
  Specialist, SRE, DevOps, IT infra). LI surface = cards under company
  'rednote' (5 cards probed); bilingual titles → honest 0% verbatim join.
- **Live feishu portals found by the review** (0 US today, wire-ready):
  `momenta` (Momenta, 248 posts) · `infinigence` (Infini-AI/无问芯穹, 59
  posts, San Jose US entity exists — watch for US roles appearing).
- **Scope notes**: Tencent America (Hunyuan/AMS, Bellevue/San Jose/Palo
  Alto — the largest CN AI-US employer) is out of startup scope but
  record-kept: careers.tencent.com/global own-platform. Zhipu is on the
  US Entity List (Jan 2025) — its 0-US row belongs with the sanctioned
  cohort. Hesai (lidar, Sunnyvale entity; DoD 1260H but NOT Commerce
  Entity List) + Pudu (Seattle sales) + AutoX (San Jose) + DeepRoute =
  documented own-site candidates. Humanoid wave (LimX/EngineAI/Robot
  Era) = CN-only. Dify + Yarbo = remote-friendly small candidates
  (own-site careers). NIO full board = 2,091 posts, 0 US (verified at
  full pagination by the review).

## Decision
1. Build **FeishuHire adapter** (ats:feishuhire:{portal}) — minimax + shengshu.
2. Wire **Horizon Robotics** via the existing lever adapter (config-only).
3. Wire **Xiaohongshu** (custom:xiaohongshu — peer-review round 1's find).
4. Document everything else with evidence; Kwai workday = future GHA-egress
   probe (WAF-406 from this egress).

## Evidence payloads (the S15 committed-payload standard, review finding 8)
- `vrfi1sk8a0_page0_redacted.json` — minimax search page 0 (count 185, one
  full schema row + trimmed rows)
- `shengshu_page0_redacted.json` — shengshu page 0 (count 106)
- `xiaohongshu_us_page_redacted.json` — XHS US-filtered page (total 20)
NOTE: `scripts/s17_feishu_probe.py` sends body={} (the naive 10-of-N trap —
kept for the one-page quick probe ONLY); the census instrument of record is
the ADAPTER (body-offset/limit-200 pagination + envelope-code guard).
