# S18 Census — CN AI landscape, round 2 (go broader)

**Date:** 2026-09-25/26 · **Method:** exhaustive web-search sweep + live probes
(own-site HTML, schema.org JobPosting hints, ATS-directory grep, feishu-portal
sweep via the ADAPTER, browser network capture) · **User ask:** "what about
kimi, z.ai, deepseek, and others. go broader do some exhaustive web search
first and then build the company-based pipeline."

## The user-named trio (verified)

| Company | Verdict |
|---|---|
| **Kimi / Moonshot AI** | already wired S16 (ashby `moonshot`); re-verified LIVE: 4 US rows (Staff/Sr Backend & Platform — FinOps/IAM/Blockchain, Growth Lead NY, Sr Backend NY) |
| **Z.ai (Zhipu global brand)** | NO careers surface on the global site (`/careers` → 404, redirects to chat.z.ai); parent Zhipu = feishu `zhipu-ai` 237 jobs / 0 US (US Entity List, Jan 2025) → stays documented |
| **DeepSeek** | EN careers 404; China-only hiring (by policy, census'd S17) → stays documented |

## NEW WIRES (this round — 27 → 30 roster)

| Company | Surface | US rows | Notes |
|---|---|---|---|
| **HoYoverse (miHoYo)** | **ashby `hoyoverse`** | **6** (Los Angeles) | Global BD Social Platform Partnerships (Remote), PR Manager (Remote), Sr Engine Programmer Generalist (Hybrid), Sr Audio Designer (Remote), User Researcher + Associate (LA). Published 2026-08 — LIVE. Found via the careers SPA's embed. ⚠️ census traps: their **smartrecruiters board is STALE** (7 US rows all 2023-02) and their **greenhouse board is empty** — the live board is ashby (also absent from our 3,161-slug ashby directory snapshot). |
| **PlusAI (Plus.ai, autonomous trucking)** | **lever `plus-2`** | **41 (all-US board)** | Santa Clara 34 · Fremont 4 · Dallas 2 · San Antonio 1; Full-time ×41; fresh createdAt epochs (2026). Discovery chain: ziprecruiter/builtin listings → plus.ai/careers custom GUID site (41 static rows, all Santa Clara) → **schema.org JobPosting on detail page reveals the LEVER underlay** `jobs.lever.co/plus-2` → config-only wire. CN-founded (SF/Cupertino HQ + Shanghai R&D). |
| **United Imaging North America (UIHA)** | **paylocity (NEW ADAPTER CLASS #9)** | **41 (US-only board, mostly Fully Remote)** | CN medical-imaging giant's US entity (Houston HQ + Seattle R&D). Surface: `recruiting.paylocity.com/recruiting/jobs/All/{CompanyId}/United-Imaging-North-America` — the whole board is server-side in `window.pageData.Jobs` (JobId, JobTitle, LocationName, PublishedDate, IsRemote, IndeedRemoteType; Description = 110-char teaser); full JD per row on `/Recruiting/Jobs/Details/{JobId}` (Description + Requirements HTML sections). Posted Sept 2026 — LIVE. |

## Documented with evidence (0-US or no surface — the go-broader sweep)

| Company | Surface probed | Verdict |
|---|---|---|
| Dify (LangGenius) | own Supabase fn `qcnurokxgtyuimuixztr.supabase.co/functions/v1/public-jobs` (open!) | 4 jobs, ALL China (Suzhou, remote-within-CN). LinkedIn's "16 Dify jobs in United States" = aggregator mirror noise, NOT real roles. The join-page's "remote-friendly roles in the US" is marketing. |
| AutoX | consider.com board (empty: "No jobs found") + autox.ai/en/careers.html (240B stub) | no live surface; US jobs exist historically (San Jose) — LinkedIn/ziprecruiter only |
| Hesai (lidar) | hesaitech.com/careers = "Open Positions **Archive**" | board retired; 4 roles worldwide LinkedIn-only; Sunnyvale entity real (DoD 1260H, not Commerce-EL) |
| DeepRoute.ai (Fremont) | deeproute.ai/jobs & /careers → 404; no career nav | Fremont roles visible only via ziprecruiter/career.io mirrors — no structured surface |
| Genspark | genspark.ai/careers → CF "Performing security verification" even in browser | CF-hardened class (same as Glassdoor/ZipRecruiter posture — out of reach by policy) |
| Tuya (AIoT) | tuya.com/en/careers → 404 | no surface |
| Ecovacs | ecovacs.com/en-us/careers → redirects to US storefront | no careers surface (sales via storefront only) |
| 4Paradigm | en.4paradigm.com | no US jobs surface; HK-listed, sanctions-adjacent |
| Infervision | global.infervision.com (contact/about only) | no jobs page; LinkedIn-only |
| Squirrel AI / APUS / Fourier / Genspark-adjacent app cos | no live US surface | documented |
| HoYoverse smartrecruiters board | 7 US rows ALL 2023-02 | **the stale-board census class** — a live board with dead listings; census must check releasedDate freshness, not just row count |

## The generalized instruments built this round

1. **`scripts/s18_feishu_sweep.py`** — reusable feishu-portal census sweep: probes
   `{slug}.jobs.feishu.cn` existence, then runs the REAL FeishuHireAdapter
   (envelope guard, body-offset pagination, ANY-city rule) per live portal.
   S18 run: **77 slugs → 14 live portals** (10 = S17 control group reproduced
   exactly; 4 new: `moonshot` [0 public], `qcraft` [19 jobs, 0 US],
   `thinkingdata` [0], `growingio` [0]). **Ceiling finding: feishu portal
   slugs are NOT guessable at scale** (MiniMax uses opaque `vrfi1sk8a0`;
   63/77 guesses no-portal) — the real discovery surface is career-page
   links + web search, NOT slug enumeration.
2. **PaylocityAdapter** (ats:paylocity:{CompanyId}) — adapter class #9,
   window.pageData server-side board + per-id detail HTML (the tripcom/xhs
   one-call-board pattern).
3. **The schema.org-JobPosting census trick** (PlusAI): a custom careers site
   that mirrors an ATS often leaks the underlay in its JobPosting JSON-LD
   (`"url": "https://jobs.lever.co/..."`) — check JSON-LD BEFORE building a
   custom adapter.

## Roster delta

27 → **30**: +hoyoverse (ashby) · +plusai (lever `plus-2`) · +unitedimaging
(paylocity). Census evidence payloads: `unitedimaging_pageJobs.json`,
`feishu_sweep_s18.json`, `searches/*.json` (29 query result sets).
