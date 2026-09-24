# S16 CN Global Company Census (live-probed 2026-09-24)

> Method: ATS directory (9,705 live boards) name/slug search + live probes of
> careers pages + direct ATS API checks. Every "US jobs" number below was
> measured live today (not guessed). Verdicts: EASY = public no-auth JSON API
> in a supported adapter class; NEW-ADAPTER = public no-auth JSON API but a
> platform we don't have a site adapter for yet; HARD = auth/CF-gated; NO-US.

## Actionable finds (live-verified US postings)

| company | platform | spec | US jobs | verdict |
|---|---|---|---|---|
| GE Appliances (Haier) | workday | `haider\|wd3\|GE_Appliances` → `haier\|wd3\|GE_Appliances` | 178 total, US-located (LaFayette GA etc.) | EASY (config-only) |
| TP-Link USA | workable | `apply.workable.com/api/v1/widget/accounts/tp-link-usa-corp?details=true` | 86 (77 FT, Irvine CA + 6 cities) | NEW-ADAPTER (workable) |
| Faraday Future | greenhouse | `faradayfuture` | 70 (all-US, El Segundo CA) | EASY (config-only) |
| XPENG | greenhouse | `xpengmotors` | ~20 of 24 (Santa Clara "SV Office"; campus/intern heavy) | EASY (config-only) |
| WeRide | lever | `api.lever.co/v0/postings/weride?mode=json` | 10 of 17 (San Jose CA) | NEW-ADAPTER (lever) |
| Pony.ai | workable | `apply.workable.com/api/v1/widget/accounts/pony-dot-ai?details=true` | 10 (8 FT, Fremont CA) | NEW-ADAPTER (workable) |
| DiDi Labs | greenhouse | `didi` | 9 (all-US, San Jose CA) | EASY (config-only) |
| Moonshot AI | ashby | `moonshot` (NOT the directory's stale `moonshot-ai`) | 4 (all-US, 2 USA + 2 New York) | EASY (config-only) |
| TCL North America | greenhouse | `tcl` | 4 (Irvine CA, "189 Technology Drive") | EASY (config-only) |
| Gotion Inc. | greenhouse | `gotion` | 1 of 145 (Fremont CA; rest non-US) | EASY (marginal — include for completeness) |
| **FIX: Alibaba Cloud host** | alibaba/lumos | `careers-alibabacloud.com` is NXDOMAIN globally (Google+CF DNS Status 3); the live host is **`careers.alibabacloud.com`** (same Lumos API, cookie flow verified, 246 rows, 25 US) | +25 US rows on the EXISTING alibaba board | ONE-LINE host fix |

## Probed and excluded (evidence)

- **Honor (greenhouse `honor`)**: FALSE POSITIVE — that board is Honor Technology
  Inc (US home-care company: "Client Care Advisor", "Home Instead Caregiver"
  titles; Chicago/Dallas/San Antonio/Pittsburgh). NOT the Shenzhen phone maker.
- **HoYoverse (greenhouse `hoyoverse`)**: board exists but **0 jobs** (empty
  board; they post on their own JS site hoyoverse.com/careers).
- **Xiaomi (workable `xiaomi`)**: account exists, 0 jobs posted there; global
  careers (xiaomi-careers.com) is CF-hardened from this egress (0 bytes).
  MEDIUM/HARD. Future candidate.
- **Lenovo (jobs.lenovo.com)**: Phenom CMS (JS; /widgets POST didn't serve
  JSON from this egress). MEDIUM. Future candidate (large US employer).
- **WuXi AppTec**: `careers-wuxiapptec.icims.com` (iCIMS) + STA on
  **Beisen** (`wuxiapptec.zhiye.com/social/jobs`, JS app w/ BSGlobal tenant
  key). MEDIUM/HARD. Future candidate.
- **NIO (nio.com/careers)**: Next.js app; API not discoverable from static
  HTML. MEDIUM. ~23 US roles per LinkedIn. Future candidate.
- **DJI (we.dji.com)**: own JS app, API guesses 404. MEDIUM/HARD.
- **Polestar (Geely)**: polestar.com/careers JS app; jobs.polestar.com /
  careers.polestar.com don't resolve. MEDIUM. Future candidate.
- **Zeekr (zeekr.com/us/careers)**: live JS app. MEDIUM. US hiring unclear.
- **Huawei (career.huawei.com)**: live own-platform (200); US hiring minimal
  (sanctions era). NO-US-ish. Documented, not pursued.
- **CATL (career.catl.com)**: DNS fails from this egress. Defer.
- **Kwai/Kuaishou, Temu/PDD, Hikvision US, Hisense, Anker, EcoFlow, Unitree,
  Insta360, Transsion, Meituan, Envision, Hikvision**: no accessible public
  job API found in quick probes (empty workable accounts, JS apps, or no US
  board). Defer to a dedicated discovery round if needed.

## Recommended action for this session

1. One-line host fix (alibaba) + alibaba re-dump → recovers 25 US rows.
2. Config-only wires: gea, xpeng, faradayfuture, didi, tcl, moonshot (all
   existing adapter classes — workday + greenhouse + ashby).
3. Two new site adapters (lever + workable) — both trivial public JSON APIs,
   same contract as greenhouse/ashby → unlocks weride, tp-link-usa, pony.ai
   (and directory-scale future coverage: lever + workable are big surfaces).
4. Gotion: include (1 US row; honest coverage).
