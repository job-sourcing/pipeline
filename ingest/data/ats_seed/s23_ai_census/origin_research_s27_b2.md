# S27-B2 Origin Research — Batch B2 (5 currently-wired gray-zone boards)

Mission: apply the user's S27 criteria ("chinese-descent founders alone don't count —
need china-based / china-owned / china-operated / ops-gravity-to-China") to 5
currently-wired companies. CURRENT state (2025-2026) evidence only. Verdict classes per
origin_research_s27_prompt.md. Agent: S27-R4. Date: 2026-10-07 (all citations live-fetched
this session; qcc via agent-fetch-kit firecrawl backend because direct curl hits the
Aliyun WAF; DDG/Bing/Sogou hit bot-challenges mid-session — noted where a re-probe was
not possible).

## Summary (verdicts)
| Company | Verdict | One-line basis |
|---|---|---|
| GrubMarket | us_only | All-SF hiring surface (custom openings page + 0 published Workable roles); no China entity (qcc: only a deregistered trademark-shell match); founder 徐敏毅 descent-only |
| Weee! | china_ops | Shanghai WFOE 上海赛潍网络科技 100%-owned by Weee Inc + Xi'an/Nanjing/Wuhan branches; 251 insured CN employees (2025 annual report); @sayweee.com contacts |
| UniUni | us_only (gray) | 100% North-America ops (600K parcels/day, 50K drivers); founder 鲁俊伟 is a naturalized-Canadian SJTU grad; no mainland entity — only an HK "Uniuni Limited" |
| Dify / LangGenius | china_ops (re-verified) | 苏州语灵 在业, 72 参保 (2025年报), 2026-09 procurement win; own live careers API: 4/4 open roles located in China (Suzhou office), updated 2026-09-18 |
| Creatify | us_only (gray) | Named V2EX RMB-payroll CN-remote rounds are 2024 (last activity 2024-06-12); board today = 5/5 Mountain View roles; no 2026 CN hiring signal |

KEEP (2): Weee! (china_ops), Dify (china_ops).
REMOVE-from-China-roster (3): GrubMarket, UniUni, Creatify (boards stay wired; origin flags change).


## GrubMarket
- Verdict: us_only
- China ops / ownership / workforce: GrubMarket (SF; founder/CEO Mike Xu 徐敏毅, Xiamen
  University → UW-Madison CS, profiled by Forbes China as 硅谷华人) shows NO China
  footprint at CURRENT state: the live custom openings page lists ~15 positions and every
  one is San Francisco (Full Stack Software Engineer - SF, Digital Marketing Manager - SF,
  grocery packers/drivers/customer care, all SF), and the wired Workable account
  (apply.workable.com/api/v1/widget/accounts/grubmarket) currently publishes 0 jobs. qcc
  search for "GrubMarket" returns only 3 fuzzy matches — 上海享麦电子商务有限公司 (a
  trademark-agency shell holding unrelated marks like DEEPGRAM/九天微星; 0 参保,
  DEREGISTERED 2025-09-16 by resolution), 上海权变电子商务有限公司 (detail page
  international-redirects), and a Kunshan trading sole proprietorship — i.e. no operating
  China entity behind GrubMarket. Ownership is US-private: SEC EDGAR (CIK 0001652648,
  GrubMarket, Inc., Delaware) shows only 4 Reg-D filings (2015-2022, latest 2022-03-08)
  and NO public S-1/F-1 as of 2026-10-07 — the reported IPO submission remains confidential
  or deferred; LCA 47 active rows 2025-26 = US H-1B sponsorship in SF, i.e. hiring Chinese
  nationals IN the US, not China gravity.
- Sources: https://www.grubmarket.com/hello/jobs/openings/ (live today, all-SF roles),
  https://apply.workable.com/api/v1/widget/accounts/grubmarket (0 published jobs),
  https://data.sec.gov/submissions/CIK0001652648.json (GrubMarket, Inc., DE; 4× Form D,
  2015-2022, no S-1), https://www.qcc.com/web/search?key=GrubMarket (3 fuzzy/trademark
  matches, no operating entity), https://www.forbeschina.com/entrepreneur/59910 (徐敏毅
  profile), https://zhuanlan.zhihu.com/p/444653552 (founder story: 厦门大学→美国).
- Note: wired as ats:workable:grubmarket — board healthy-but-empty (0 published roles);
  the real hiring surface is the custom openings page (SF-only, ~15 roles), which a
  custom: adapter would need to pick up. gray: Chinese-language media profile the company
  as a Chinese-immigrant-run SF business with H-1B-heavy engineering (47 active LCA rows)
  — if the user ever wants a "Chinese-operated-in-US" class this is a candidate — but
  under the S27 classes there is no China entity/office/workforce, so us_only.

## Weee!
- Verdict: china_ops
- China ops / ownership / workforce: Weee! (WEEE! INC, Fremont) runs a CURRENT, material
  China organization: Shanghai WFOE 上海赛潍网络科技有限公司 (est. 2020-12-15, Jiading;
  外国法人独资 — qcc lists the single shareholder as "Weee Inc., 美国实际控制人",
  holding since 2021-04-28; legal rep 谢祖铭; paid-in capital ¥132.98万; registered email
  tao.bu@sayweee.com) with status 存续 and 144 参保 (2025 annual report) PLUS three
  active branches — 西安分公司 (Yanta, est. 2021-03-28, 在业), 南京分公司 (Yuhuatai,
  est. 2023-05-11, 在业) and 武汉分公司 (在业) — whose employees add another 107 参保
  (2025 annual report): ~251 insured China employees across Shanghai/Xi'an/Nanjing/Wuhan,
  business scope = computer/network tech R&D + internet sales (an engineering/supply-chain
  arm, with import-export registration). A related legacy entity, 上海贝海网络科技有限公司
  (est. 2014-04-22, same legal rep 谢祖铭, email xiezuming@sayweee.com, still 存续 but 0
  参保 in the 2025 report), shows the China presence predates the WFOE. All China hiring
  runs through this entity structure, NOT through the US Greenhouse board.
- Sources: https://www.qcc.com/firm/7e0db2e8bf76833d6438efe247733bd6.html (上海赛潍:
  存续, Weee Inc. shareholder, 谢祖铭, 参保 144 + branches 107, @sayweee.com email),
  https://www.qcc.com/firm/922975d7921b7062152822434bf1052b.html (西安分公司 在业),
  https://www.qcc.com/firm/e69f1f2f7b8d0d73abc2230707ce160b.html (南京分公司 在业),
  https://www.qcc.com/firm/b393dcead45eeda171a6a7091d35abd3.html (武汉分公司),
  https://www.qcc.com/firm/ec83b3802a965702ee1e6f36bd68eb0b.html (贝海: @sayweee.com,
  0 参保), https://www.qcc.com/firm/z0010db42d9c63cbf4884eb2a84c4f12.html (Weee Inc. qcc
  record), https://boards-api.greenhouse.io/v1/boards/weee/jobs (18 jobs, 18/18 US:
  Clifton NJ ×8, Houston ×3, Landover ×3, Milpitas/La Mirada CA, Hodgkins/Chicago IL),
  https://www.weee.com/company/home ("America's largest online Asian supermarket",
  US-only footprint).
- Note: wired as ats:greenhouse:weee — board healthy, 100% US roles (warehouse/ops
  heavy); the ~250-person China arm hires separately (BOSS直聘/China channels), so like
  PingCAP/Alluxio the wired board alone under-represents the China gravity. Entity-name
  correction for the pipeline: the census hint "weee 微米科技? / 上海旺洋经贸" is wrong —
  the real entity is 赛潍 (≈"Saywee"); homonym traps: 微米科技 (unrelated Hangzhou co),
  贝海网络 is the legacy not the operating entity. KEEP under S27.

## UniUni
- Verdict: us_only (gray)
- China ops / ownership / workforce: UniUni (Vancouver, founded 2019 by 鲁俊伟 Peter Lu —
  Shanghai Jiao Tong University CS, ex-Ericsson/PCCW engineer, immigrated to Canada 2003,
  naturalized Canadian; Forbes China 全球华人精英 TOP100 2022; co-founders Kevin Wang /
  Jason Wang; 蓝驰创投-backed) is a 100% North-American operation: per its own About page,
  600K deliveries/day, 50K registered drivers, 600+ employees across Canada and the US,
  last-mile for cross-border e-commerce (Shein/Temu class volume), ~$1.4B valuation with
  an imminent listing per May-2026 Chinese-Canadian press. The live Rippling board (20
  roles) spans US (Pittsburgh/Atlanta/DFW/Hartford/Spokane/Fontana/Medford), Canada
  (Vancouver/Toronto/Montreal), India and Vietnam — ZERO China roles, though several are
  explicitly Mandarin-language ops roles ("Operations Assistant (Mandarin Required) -
  Fontana, LA", "Bilingual Client Success Specialist" Vancouver) and BD roles target
  Vietnam/SEA cross-border lanes. qcc search finds NO mainland entity — only a Hong Kong
  "Uniuni Limited" (仍注册, offshore structure), no Shanghai/Beijing/Hangzhou R&D, no
  China hiring channel found.
- Sources: https://baike.baidu.com/item/鲁俊伟/61992602 (founder bio: SJTU, Ericsson/PCCW,
  2003 Canada immigration, Forbes China 2022), https://www.cyzone.cn/article/722063.html
  (创业邦 founder story; 蓝驰创投 investment, co-founders), https://zhuanlan.zhihu.com/p/647046947
  (100位华人企业家 profile), https://wcweekly.com/2026/05/厉害了！大温华人移民创办快递公司-估值$14亿即将上市-曾卖掉房子退钱/
  (2026-05: $1.4B, about to list), https://www.uniuni.com/about-us/ (milestones: 2019
  Vancouver → 2024 600K/day, 50K drivers, 600+ employees, NA-only), 
  https://ats.rippling.com/uniuni/jobs (live board: 20 roles US/CA/IN/VN, 0 CN,
  Mandarin-required NA roles), https://www.qcc.com/web/search?key=UniUni (no mainland
  entity; HK "Uniuni Limited" 仍注册).
- Note: wired as ats:rippling:uniuni — board healthy (20 roles; ~9 US rows survive the
  country gate). gray: this is the flagship "Chinese-operated-in-North-America" case —
  Mandarin-required NA ops, WeChat-based driver recruitment, Chinese-founder control,
  Chinese VC minority (蓝驰) — which arguably satisfies the user's "biased towards hiring
  Chinese" intent, but the S27 verdict classes only count China-located gravity, and
  none exists (no mainland entity/workforce/payroll). A China dev office or mainland
  entity discovery would flip it to china_ops. REMOVE under S27 as classes stand.

## Dify / LangGenius (re-verification)
- Verdict: china_ops (re-confirmed, CURRENT)
- China ops / ownership / workforce: All three prior signals re-checked TODAY and still
  current: (1) Suzhou entity 苏州语灵人工智能科技有限公司 is 在业 (qcc page updated
  2026-09-16, with 2026-09-27 trademark registration and a 2026-09-11 新增中标 "Dify
  大语言模型应用开发平台项目-Dify 企业版采购框架项目" — an active China enterprise
  business), legal rep/founder 张路宇, 参保 72 (2025 annual report), registered at Suzhou
  Industrial Park 自贸区, shareholders incl. 张路宇, 苏州语芯创投 and 浙江阿里巴巴云计算有限公司
  (Alibaba Cloud); (2) Dify's OWN live careers API (the JS backend of join.dify.ai/roles.html,
  fetched now) lists 4 open positions, ALL with location "China" / work-site "Office
  priority (Suzhou)" or "Remote / Based in Suzhou" — created 2026-03-04 → 2026-09-18,
  updated 2026-09-18 (LTS Backend Engineer, DevRel Intern, Backend Dev Intern, UI/UX
  Designer); (3) the Shanghai-office claim from the S23-B1 round (aiqicha: "国内主要办公
  地点为苏州和上海") could not be re-probed this session (aiqicha geo-blocks overseas IPs;
  BOSS直聘 now login-walls the search page) — current postings themselves center on
  Suzhou, so Shanghai remains prior-evidence.
- Sources: https://www.qcc.com/firm/ac517cada0d89cdc012a8349115883b1.html (在业, 72 参保
  2025年报, 张路宇, Alibaba Cloud shareholder, 2026-09 procurement win + trademark),
  https://qcnurokxgtyuimuixztr.supabase.co/functions/v1/public-jobs?lang=en (live: 4/4
  roles location=China, Suzhou office, updated 2026-09-18; same in lang=zh),
  https://join.dify.ai/roles.html (wired surface — roles JS-loaded from that Supabase
  function), prior: https://aiqicha.baidu.com/details/ugknowledge?id=bf586cd0d328a309bdb879d98250684f
  (苏州+上海 offices, Sep-2026 B1 probe).
- Note: wired as custom:dify — the board's Supabase endpoint
  (…supabase.co/functions/v1/public-jobs?lang=en) is the parseable API (the HTML page is
  a JS shell — this endpoint is the fix for the "loads via JS / 0 static rows" problem
  noted in S23-B1). CURRENT state: 4/4 roles are China-located, so the country gate
  yields 0 US rows today; US hiring (5 historical LCA rows for LANGGENIUS, INC. Menlo
  Park) is dormant on the board. KEEP — china_ops is stronger than ever (own-board China
  hiring + China enterprise revenue + 72-person insured entity).

## Creatify (re-verification)
- Verdict: us_only (gray)
- China ops / ownership / workforce: The China-remote hiring evidence did NOT survive the
  CURRENT-state test: the two named V2EX 酷工作 rounds (t/1039784, 2024-05-11, "30k+/mo
  [远程支持] Creatify.ai 招募前后端工程师"; t/1046997, 2024-06-05, "年薪 20 万 RMB 起…
  本次为第三轮招聘, 前两轮已完成") are re-confirmed via the V2EX API today but their
  last_touched timestamps are 2024-05-12 and 2024-06-12 — i.e. HISTORIC, 2+ years stale.
  The same V2EX recruiter account (guancyxx4king, email guancyxx@guancyxx.cn) kept
  posting through 2025-06, but the 2025 threads are either explicitly his own Sichuan
  county-level venture (2025-03/05/06) or unnamed "硅谷创业团队 - AI 方向" Shenzhen-ONSITE
  rounds (2025-02-17 ×2, business blurb "彻底改变人们投放视频广告的方式" matches
  Creatify's video-ads domain) — attribution to Creatify is plausible but unverified, and
  there is zero 2026 activity. Current board state: the live Ashby posting API returns
  exactly 5 open roles, ALL Mountain View (AI Research Engineer, Full-Stack Engineer,
  Creative Director, PM Intern, SWE Intern); no China legal entity has ever been found
  (S23-B1 confirmed none); LCA shows CREATIFY LAB INC ×18 filings in 2026 (US H-1B
  sponsorship — hiring Chinese nationals in Mountain View, not China gravity).
- Sources: https://www.v2ex.com/api/topics/show.json?id=1039784 (2024-05-11, last_touched
  2024-05-12), https://www.v2ex.com/api/topics/show.json?id=1046997 (2024-06-05,
  last_touched 2024-06-12, "第三轮招聘"), https://www.v2ex.com/api/topics/show.json?username=guancyxx4king
  (recruiter's full topic history: Creatify rounds 2024; unnamed Shenzhen-onsite
  "硅谷创业团队" video-ads rounds 2025-02-17 t/1111981 + t/1111871; own Sichuan venture
  2025-03→06; nothing 2026), https://api.ashbyhq.com/posting-api/job-board/creatify (5
  roles, all Mountain View, live today), https://yuancheng.work/company/6665ab48429eb
  (RMB-payroll remote mirror of the 2024 round), https://creatify.ai/careers.
- Note: wired as ats:ashby:creatify — board healthy, 5/5 US roles (Mountain View); the
  Ashby posting API endpoint (api.ashbyhq.com/posting-api/job-board/creatify) is the
  curl-able backend if the JS-shell page ever blocks the adapter. gray: a named 2025/2026
  V2EX/BOSS直聘 round, or a LinkedIn scan showing China-based Creatify engineers hired in
  2024 still employed, would flip this back to china_remote_workforce; as probed today
  the MyShell-class pattern is historic-only. REMOVE under S27 (fail-conservative), with
  the strongest-flip-case of this batch.

## Cross-batch notes for the pipeline
- Weee! entity correction: China arm = 上海赛潍网络科技有限公司 (Suzhou homonym trap:
  NOT 微米科技/旺洋) + Xi'an/Nanjing/Wuhan branches + legacy 贝海网络; qcc record
  z0010db42d9c63cbf4884eb2a84c4f12 = Weee Inc. (US controller).
- GrubMarket qcc "GrubMarket" search matches (享麦/权变/昆山萨尔恩) are trademark-shell
  noise, not operating entities — do not wire on them.
- Dify board API: qcnurokxgtyuimuixztr.supabase.co/functions/v1/public-jobs?lang=en (also
  ?lang=zh) — returns clean JSON with location/work_site fields; recommended custom:dify
  adapter target.
- BOSS直聘 web search now login-walls anonymous fetches (was scrapeable in S27-R1/B1) —
  future re-verifications need the firecrawl render path or a manual probe.
- Fetch budget: ~30 live fetches total (≤6 per company incl. board probes + engine
  searches; every failing URL abandoned after ≤2 attempts). qcc via kit firecrawl;
  DDG/Bing/Sogou hit bot-challenges mid-session (paced DDG re-use; Mojeek/Google blocked).
