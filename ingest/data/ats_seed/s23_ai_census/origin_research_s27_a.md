# S27-A Origin Research — Batch A (9 currently-wired gray-zone boards)

Mission: apply the user's S27 criteria ("chinese-descent founders alone don't count —
need china-based / china-owned / china-operated / ops-gravity-to-China") to 9
currently-wired companies. CURRENT state (2025-2026) evidence only. Verdict classes per
origin_research_s27_prompt.md. Agent: S27-R1. Date: 2026-10-07 (all citations live-fetched
this session).

## Summary (verdicts)
| Company | Verdict | One-line basis |
|---|---|---|
| HeyGen | us_only | Shenzhen entity deregistered 2023-12; forcing CN VCs out; 22/22 jobs US/UK/SG |
| TigerGraph | us_only | Shanghai WFOE 注销 (2026 registry record); Cuadrilla (US PE) acquired 2025-07; 3 CA-only jobs |
| Alluxio | china_ops | Live CN site + 北京开元维度 entity (ICP live); 5/9 live Lever postings China-based (3 dated 2025-26, CN-language) |
| RisingWave | china_ops | CN entity 北京奇点无限 + SH branch; official Zhihu hiring posts edited 2025-05 & 2026-06 (广州/北京/上海 bases) |
| StreamNative | china_ops | 3× 存续 CN entities (Beijing parent $20M + GZ/SH branches); CN HQ Beijing; CN hiring currently dormant |
| PingCAP | china_ops | 15× 存续 CN entities (BJ/SH/HZ/CD/GZ/SZ); live Moka CN board 7 roles; pingkai.cn China product line |
| Airwallex | china_ops | 14× active CN entities incl. ¥550M Shanghai hub; ~75 live CN-based openings on Ashby board |
| Lightelligence | china_hq | Shanghai 股份有限公司 listed HKEX 01879.HK 2026-04-28; SH/HZ/NJ/BJ offices; 2027 campus recruiting live |
| PlusAI (Plus) | us_only | China ops sold to 满帮 (FTA) 2025-07 (80.8%, ¥1.376B); US entity SPAC-path; 38/38 jobs US |

KEEP (6): Alluxio, RisingWave, StreamNative, PingCAP, Airwallex, Lightelligence.
REMOVE-from-China-roster (3): HeyGen, TigerGraph, PlusAI (boards stay wired; origin flags change).


## HeyGen
- Verdict: us_only
- China ops / ownership / workforce: HeyGen's China legal entity 诗云科技（深圳）有限公司
  was dissolved and deregistered (注销) on 2023-12-11 by shareholder resolution, ~1.5 months
  after going viral in China — the Shenzhen base is gone, not current. Chinese Wikipedia
  (rev. 2026-07-16) classifies HeyGen as a 美国公司 (US company, HQ moved to LA 2022).
  HeyGen has moreover been actively pushing OUT Chinese capital: it forced Sequoia China
  (HongShan) and ZhenFund to sell their stakes to US investors (FT, Jun 2024 / TMTPost).
  Live Greenhouse board (boards-api.greenhouse.io/heygen, fetched today): 22 open roles,
  ALL in San Francisco / Palo Alto / LA / Toronto / Singapore / UK-remote — zero China
  cities. Contact page lists only a Los Angeles presence.
- Sources: https://www.36kr.com/p/2567512965047685 (2023-12-19, 注销 report),
  https://zh.wikipedia.org/wiki/HeyGen (rev 2026-07-16),
  https://www.sohu.com/a/784547393_116132 (Sequoia China/ZhenFund forced divest),
  https://boards-api.greenhouse.io/v1/boards/heygen/jobs (22 US/UK/SG roles, 0 CN),
  https://www.heygen.com/contact-us (LA only).
- Note: Wired as greenhouse:heygen — board itself is healthy and US-only; wiring can stay,
  but origin flag should change from Chinese-founder class → us_only under S27 policy.
  (Chinese VCs Sequoia China/ZhenFund were minority investors being bought out — never
  control.)

## TigerGraph
- Verdict: us_only
- China ops / ownership / workforce: TigerGraph's China WFOE 维加星信息科技（上海）有限公司
  (Shanghai FTZ, founded 2015, reg. cap. 75.55M RMB, HK-parent-owned 港澳台法人独资) is
  now DEREGISTERED — qichamao registry mirror shows 经营状态: 注销 (record updated
  2026-07-13, legal rep changed to RAJEEV SHRIVASTAVA, i.e. the US CEO). The China website
  tigergraph.com.cn is DEAD (connection fails, HTTP 000). In July 2025 US PE firm Cuadrilla
  Capital (Santa Barbara) made a strategic/acquisition investment in TigerGraph, and CEO is
  now Rajeev Shrivastava (US professional manager, not founder Yu Xu). Live Greenhouse
  board (fetched today): 3 open roles, all California (Milpitas ×2, Redwood City ×1), zero
  China roles.
- Sources: https://www.qichamao.com/orgcompany/searchitemdtl/77a86bca2ebf89c3fd1f24cce79ac166.html
  (注销 status, RAJEEV SHRIVASTAVA legal rep),
  https://www.innohere.com/ir/106792/business.html (entity facts: 港澳台法人独资, Shanghai
  FTZ, YU XU was legal rep, <50 staff),
  https://www.tigergraph.com/wp-content/uploads/2025/07/TigerGraph-Press-Release.pdf
  (2025-07-15 Cuadrilla investment, CEO Rajeev Shrivastava, Redwood City HQ),
  https://globallegalchronicle.com/cuadrilla-capital-acquires-tigergraph/ (Cuadrilla
  acquires TigerGraph),
  https://boards-api.greenhouse.io/v1/boards/tigergraph/jobs (3 CA-only roles),
  https://www.tigergraph.com/company/ (offices shown: Milpitas + London only).
- Note: wired as greenhouse:tigergraph; board nearly empty (3 roles) — small US-only
  shop post-acquisition. REMOVE from roster under S27 (China entity deregistered; US PE
  ownership; no China workforce signals).

## Alluxio
- Verdict: china_ops
- China ops / ownership / workforce: Alluxio maintains a CURRENT, ACTIVE China operation:
  a live Chinese-language official site alluxio.com.cn (fetched today, ©2023, ICP
  京ICP备2022026075号-1) operated by Beijing entity 北京开元维度科技有限公司 (est. 2021,
  Zhongguancun, Haidian), a Beijing phone (+86 010-82449668), china-marketing@alluxio.com.cn
  and WeChat channels, and a current China-region GM (王晓丹 中国区总经理 — also listed as
  SVP Operations on the global site). The LIVE US Lever board (jobs.lever.com/alluxio,
  fetched today, 9 postings) includes CURRENT China-based roles: 售前解决方案工程师
  (2026-04-20), 高级客户经理 Shanghai (2026-01-22), Staff Software Engineer Beijing/Shanghai
  (2025-10-11), plus a 2024 Beijing posting — i.e. systematic, ongoing hiring for a
  Beijing/Shanghai R&D + field team posted on the same board as the Foster City roles.
  Ownership remains VC-backed US Inc (Founder Haoyuan Li CEO) — China is the ops arm, not
  ownership.
- Sources: https://www.alluxio.com.cn/about-us/ (live CN site, entity, China GM),
  https://www.alluxio.io/about (global team),
  https://api.lever.co/v0/postings/alluxio?mode=json (live board: 5/9 postings China-based,
  3 dated 2025-2026, Chinese-language titles),
  https://blog.csdn.net/FL63Zv9Zou86950w/article/details/121375992 (C-round 2021: 新设中国区总部).
- Note: wired as lever:alluxio. Keep wired — but NOTE: the China postings on the lever
  board are China-local roles; our US-row filter (country gate, D-S26-5 class) should
  exclude them from the US corpus automatically. Historical: founder Berkeley AMPLab;
  historical dev was partly Beijing. Gray? No — current entity + current CN hiring = clear
  china_ops.

## RisingWave (RisingWave Labs)
- Verdict: china_ops
- China ops / ownership / workforce: RisingWave (fka Singularity Data; founder/CEO 吴英骏
  Ying-Jun Wu, ex-Amazon Redshift) has a real, CURRENT China organization: China entity
  北京奇点无限数据科技有限公司 (est. 2021, plus a Shanghai branch 北京奇点无限数据科技有限公司
  上海分公司 per qcc) running R&D offices in Beijing, Shanghai and Guangzhou. The official
  Zhihu org account (RisingWave 中文开源社区) is running CURRENT Chinese-language hiring
  rounds for database-kernel engineers via hr@risingwave-labs.com: one post edited
  2025-05-15 in Beijing listing 办公地址 "中国（北京、上海和广州）、新加坡、美国、远程", and
  another edited 2026-06-23 in Singapore listing base "广州 / 新加坡" for DB dev engineers.
  Boss直聘 carries company recruiting pages for both the Beijing entity and its Shanghai
  branch (2026-titled pages), and the company's China HR (hezijiangjiang) runs recurring
  V2EX/Zhihu/北邮-forum hiring rounds (V2EX t/1075453, 2024-09, 北京&上海&广州&Remote) and
  intern (数据库内核开发实习生) recruitment. LinkedIn: HQ San Francisco, 51-200 employees —
  the org is remote-first but its engineering hiring systematically includes China bases
  with a Chinese legal entity behind them.
- Sources: https://zhuanlan.zhihu.com/p/28055039077 (edited 2025-05-15, China bases BJ/SH/GZ,
  hr@risingwave-labs.com), https://zhuanlan.zhihu.com/p/2052760499398389822 (edited
  2026-06-23, base 广州/新加坡), https://www.modb.pro/wiki/6834 (entity 北京奇点无限 +
  BJ/SH R&D offices, founder 吴英骏, 云启资本 seed), https://www.qcc.com/firm/632786a77243826078c170995859d089.html
  (北京奇点无限数据科技有限公司), https://www.qcc.com/firm/b0b8d33633a862337d9200dd09f2d827.html
  (Shanghai branch), https://www.zhipin.com/gongsi/job/8cf8c1a6a282049c1nx-2Ni_GVI~.html
  (Boss直聘 2026 奇点无限 recruiting page), https://www.v2ex.com/api/topics/show.json?id=1075453
  (2024-09-24 BJ/SH/GZ/Remote round), https://cn.linkedin.com/company/risingwave (SF HQ,
  51-200 employees).
- Note: wired as workable:risingwave — LIVE CHECK: the Workable board
  (apply.workable.com/risingwave-labs) currently returns 0 open jobs, so the board is
  effectively EMPTY; their active hiring runs through Zhihu/V2EX/Boss直聘 + hr@ email
  instead. Board stays wired but expect 0 rows; consider a manual-watch or V2EX monitor
  for CN hiring rounds. Note the census alias says SF + London/Singapore — Guangzhou is
  the active China base per the 2026 post.

## StreamNative
- Verdict: china_ops
- China ops / ownership / workforce: StreamNative (Apache Pulsar commercial arm; founder/CEO
  郭斯杰 Sijie Guo) has THREE currently-registered ACTIVE China entities per a live qcc
  search (fetched today): 北京原流数据科技发展有限公司 (Beijing Haidian, Zhongguancun
  East Ascension park, est. 2019-08-12, reg. cap. $20M USD, legal rep 郭斯杰, status
  存续), plus Guangzhou Nansha branch (est. 2021-10-25, 存续) and Shanghai Jing'an branch
  (est. 2021-10-13, 存续) — cn_ga@streamnative.io on all records. Zhaopin company profile
  (live) states "公司中国总部位于北京，美国总部位于旧金山" (China HQ Beijing, US HQ SF).
  Chinese capital is in the cap table (红杉中国种子基金 seed, 源码资本 Pre-A, 华泰创新 +
  Prosperity7/Aramco Series A $23.7M per Baidu Baike). CURRENT workforce gravity is
  ambiguous-but-real: the org is remote-first cross-timezone (careers: "global team...
  Sunnyvale office and regional hubs"); Boss直聘 company page shows 0 open CN roles right
  now; the CN V2EX/nowcoder hiring rounds are 2021-2022 (historic); Greenhouse board has 1
  remote role (updated 2026-08). The streamnative.cn domain now serves parked/paid-content
  junk (unreachable from sandbox; firecrawl shows a content-farm page) — the CN marketing
  site is no longer a good signal either way.
- Sources: https://www.qcc.com/web/search?key=北京原流数据科技 (3× 存续 entities, 郭斯杰,
  $20M cap, CN/US emails), https://www.zhaopin.com/companydetail/CZL1201568310.htm (中国总部
  北京/美国总部旧金山, verified profile), https://baike.baidu.com/item/北京原流数据科技发展有限公司/56568370
  (history + investors), https://streamnative.io/careers (remote-first, Sunnyvale office,
  ©2026), https://streamnative.io/contact/ (only US office listed: 440 N Wolfe Rd Sunnyvale),
  https://boards-api.greenhouse.io/v1/boards/streamnative/jobs (1 remote role,
  2026-08-28), https://www.zhipin.com/gongsi/c1aae0d48be290771nd639y7FlQ~.html (Boss直聘:
  B轮, 20-99人, 0 在招职位).
- Note: wired as greenhouse:streamnative; board live with 1 remote role. KEEP under S27
  (active CN entities + CN HQ + Chinese VC backing), but flag: current CN hiring is dormant
  (0 Boss直聘 roles) — monitor. Also note the greenhouse board is the EU instance
  (job-boards.eu.greenhouse.io/streamnative) — already working in our wiring per census.

## PingCAP
- Verdict: china_ops
- China ops / ownership / workforce: PingCAP (TiDB; founders 刘奇/黄东旭/崔秋, all Chinese,
  2015) runs one of the largest CURRENT China-entity networks of this batch: a live qcc
  search returns 15 records, ALL status 存续 (active), including 北京平凯星辰科技发展有限公司
  (Beijing Haidian, est. 2015-08-02, reg. cap. $250M USD, legal rep 崔秋, unicorn-tagged),
  平凯星辰（北京）科技有限公司 (est. 2015-04-16, ¥100M, D轮/高新技术企业/专精特新, same
  address), Shanghai subsidiary 上海楷浦科技发展有限公司 (¥100M), and branches in Hangzhou,
  Chengdu, Guangzhou, Shenzhen (est. 2020-12→2021-01). The China business is a going
  concern: pingcap.cn redirects to pingkai.cn (平凯数据库 — the domestic TiDB Enterprise
  edition, © 2026, ICP 京ICP备20022552号-8, passed the 分布式数据库安全可靠测评 gov
  certification), and its LIVE Moka hiring board careers.pingcap.com/apply/pingcap/39950/
  (fetched today) lists CURRENT social-hire openings (产品&研发 ×3, 商业化 ×3, 职能 ×1 =
  7 roles) plus a separate campus-hiring board; the join page quotes 刘奇 as 平凯星辰
  founder/CEO and says the tech team is 60% of the company. Global side: pingcap.com
  careers lists Singapore/KL/Tokyo offices and the Greenhouse board has 7 roles (Tokyo ×5,
  Bay-Area remote, Korea remote). Ownership is VC-mixed (no single Chinese parent), so
  cn_owned doesn't apply — but ops gravity is unambiguously dual with a very substantial
  China side.
- Sources: https://www.qcc.com/web/search?key=北京平凯星辰 (15× 存续 records incl. branches),
  https://pingkai.cn/ (live China business, © 2026), https://pingkai.cn/join-us (刘奇 CEO,
  60% tech team, 社招+校招 boards), https://careers.pingcap.com/apply/pingcap/39950/
  (live Moka board, 7 CN roles today), https://www.pingcap.com/careers/ (global offices
  SG/KL/Tokyo), https://boards-api.greenhouse.io/v1/boards/pingcap/jobs (7 global roles).
- Note: wired as greenhouse:pingcap (global board). KEEP under S27 — the strongest keeper
  in this batch: active CN entity web + live CN hiring + China product line. US/global
  roles are the ones our board tracks; China-local roles live on the separate Moka board
  (careers.pingcap.com) — potential future wire target if user wants CN-local roles (out
  of scope for this roster).

## Airwallex
- Verdict: china_ops
- China ops / ownership / workforce: Airwallex (空中云汇; founders Jack Zhang / Lucy Liu /
  Max Li — Chinese-Australian; HQ'd globally, SF) has a LARGE CURRENT China footprint:
  a live qcc search returns 14 records, all 存续/在业, including 空中云汇（深圳）网络科技有限公司
  (Shenzhen Qianhai, est. 2016-07-14, ¥50M, H轮 unicorn-tag), 空中云汇（上海）网络科技有限公司
  (Shanghai FTZ, est. 2018-09-05, ¥550M registered capital, 技术先进型服务企业 — the
  engineering hub), 空中云汇（上海）信息科技有限公司 (¥50M, 2020), 北京空中云汇网络科技有限公司
  (2020), a Hangzhou branch (2021), 西安空中云汇网络科技有限公司 (2021), and a still-registered
  HK trading entity; all reachable via marketing.team.cn@airwallex.com / airwallex.com.cn
  (China site live today). The LIVE Ashby board (jobs.ashbyhq.com/airwallex, fetched today,
  543 openings) includes ~75+ CURRENT mainland-China-based openings: CN-Shanghai ×32,
  CN-Shenzhen ×16, CN-Guangzhou ×11, CN-Hangzhou ×9, CN-Xiamen ×4, CN-Yiwu ×3, plus CN
  titled roles ("Account Executive... CN", "Associate Director, Revenue Strategy, CN") —
  China is one of its top-2 hiring geographies. Ownership is global-VC (DST, Greenoaks,
  HongShan/Sequoia China, Hillhouse etc.) — not cn_owned; ops gravity is what qualifies it.
- Sources: https://www.qcc.com/web/search?key=空中云汇 (14× active CN entities, ¥550M
  Shanghai entity), https://www.airwallex.com.cn/ (live CN site, 空中云汇),
  https://jobs.ashbyhq.com/airwallex (live board: 543 roles, CN-Shanghai 32 / CN-Shenzhen 16
  / CN-GZ 11 / CN-HZ 9 / CN-XM 4 / CN-YW 3).
- Note: wired as ashby:airwallex; board healthy with both US rows (SF ×111, NY ×26, remote
  ×9) and many CN rows — the D-S26-5 country gate will keep CN-local rows out of the US
  corpus. Strongest KEEP in this batch by hiring volume. careers.airwallex.com itself 403s
  for curl but the ashbyhq.com board (our wired URL) renders fully.

## Lightelligence
- Verdict: china_hq
- China ops / ownership / workforce: Lightelligence (曦智科技; MIT photonic-computing spin-off
  of 沈亦晨 Yichen Shen, Lightelligence Inc. incorporated in the US 2017) has FLIPPED to a
  China-headquartered listed company: 上海曦智科技股份有限公司 (Shanghai FTZ, est. 2018-02-26,
  ¥94M, status 存续, 沈亦晨 chairman/CEO + 张弘 CFO + 孟怀宇 CTO) listed on the Hong Kong
  Stock Exchange on 2026-04-28 as 曦智科技-P (01879.HK) — "world's first AI silicon photonics
  chip stock" per the company's own live profile page. The live CN contact page lists offices
  Shanghai (Pudong 软件园) / Hangzhou / Nanjing / Beijing (compliance email runs on
  @guangzhiyuan.com.cn — its Beijing entity 北京光智元科技); the EN site adds San Jose, Boston,
  Singapore (US entity retained as R&D/sales, not HQ). CURRENT China hiring is active: the
  homepage banner announces "曦智科技·2027届校园招聘正式启动" (2027-class campus recruiting
  launched) and the recruiting portal xztechai.zhiye.com (上海曦智, ©2026, Beisen-powered)
  runs separate 社会招聘 + 校园招聘 boards with HR email xizhi.hr@xztech.ai; qcc shows 45
  procurement-bid records and Hangzhou entity 杭州曦智科技有限公司. (S24-C batch 2 had already
  classified it china_ops_major with Tencent/Sequoia China among investors — this session
  upgrades to china_hq on the 2026-04-28 HK listing of the Shanghai parent.)
- Sources: https://www.qcc.com/web/search?key=曦智科技 (上海曦智股份有限公司 存续, 01879.HK tag,
  沈亦晨, ¥94M, 45 bids), https://application.lightelligence.ai/about-us/company-profile
  ("On April 28, 2026, Lightelligence was listed on the Hong Kong Stock Exchange (01879.HK)"),
  https://www.xztech.ai/ (live CN site, 2027 campus recruiting banner),
  https://www.xztech.ai/about-us/contact-us (Shanghai/Hangzhou/Nanjing/Beijing offices),
  https://application.lightelligence.ai/about-us/contact-us (San Jose/Boston/Singapore),
  https://xztechai.zhiye.com/ (Beisen recruiting portal, xizhi.hr@xztech.ai).
- Note: NOT currently wired (census already_wired=false, origin us_inc_chinese_founders —
  outdated). careers_hint application.lightelligence.ai/about-us/join-us is a static values
  page with NO job listings; the real ATS is the Beisen-powered zhiye.com portal
  (xztechai.zhiye.com, JS-rendered — needs a new adapter class if ever wired; jobs are
  China-local so likely out of scope for the US-oriented roster anyway). LCA null in FY26.

## PlusAI / Plus (autonomous trucking)
- Verdict: us_only
- China ops / ownership / workforce: The wired company is the US/overseas entity PlusAI Inc
  (plus.ai), and its China operations are GONE — divested, not just shrunk: Plus split its
  US and China operations in 2023 (Reuters, 2023-10-11), and in July 2025 sold 80.8% of the
  China entity (智加科技有限公司, Suzhou/Shanghai operations, smartxtruck.com) to Full Truck
  Alliance 满帮集团 for ¥1.376B — the China business is now a FTA subsidiary (虎嗅/经观感知,
  2026-09-07; Xueqiu notes FTA began consolidating 智加科技). The US entity is on a US-only
  path: third SPAC attempt announced 2025-09 (Texas Ventures Acquisition III Corp, ~$800M
  valuation; prior Churchill Capital IX attempt terminated 2026-04). Live signals: the
  plus.ai site lists offices ONLY in Santa Clara CA, Fremont CA and Munich Germany; the
  live Lever board (lever:plus-2, fetched today, 38 roles) is 100% US — Santa Clara ×31,
  Fremont ×4, Dallas ×2, San Antonio ×1 (incl. Class A Truck Drivers — US fleet ops);
  LCA = PLUSAI INC Santa Clara. Founder 刘万千 (David Liu) is Chinese-descent, which under
  the S27 criteria does not count; the China gravity now belongs to a different owner (FTA).
- Sources: https://www.huxiu.com/article/4889112.html (2026-09: 2023 split, 2025-07 80.8%
  sale to 满帮 for ¥1.376B, US SPAC path),
  https://www.reuters.com/business/autos-transportation/self-driving-startup-plus-splits-us-china-operations-amid-tensions-2023-10-11/
  (2023 split), https://www.plus.ai/about-us (offices: Santa Clara/Fremont/Munich only),
  https://api.lever.co/v0/postings/plus-2?mode=json (38 US-only roles),
  https://www.smartxtruck.com/ (the China company's site, 苏ICP, now FTA-side),
  https://en.wikipedia.org/wiki/Plus_(autonomous_trucking) (split context, FTA investor
  since 2018).
- Note: wired as lever:plus-2 — board is healthy and 100% US; keep the WIRING but re-origin
  the company to us_only under S27 (China ops sold to FTA 2025-07). If the roster is
  strictly Chinese-global-only, this board is a REMOVE candidate per policy (origin flag,
  not the board, is what changes).
