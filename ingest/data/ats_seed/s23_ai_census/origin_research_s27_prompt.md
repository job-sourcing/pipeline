# S27 Origin Re-Calibration — Research Agent Brief

## Mission context
We track jobs at Chinese-global companies for a Chinese-national user seeking
US/global/expat-oriented roles (NOT China-local roles). The roster question:
which COMPANIES qualify as "Chinese-global".

## The user's S27 criteria (verbatim, authoritative)
> "founder of chinese descent means nothing i don't care, it should be a
> china-based company or one that will be biased towards hiring chinese
> because the operation heavily gravitate towards china / chinese owned /
> chinese operated."

## Verdict classes (choose exactly one per company)
- `china_hq` — HQ / global base in mainland China.
- `cn_owned` — majority ownership/control by Chinese persons/entities
  (incl. 100%-owned US subsidiaries of Chinese parents, e.g. Riot↔Tencent).
- `china_ops` — concrete CURRENT China legal entity, offices, R&D or
  manufacturing at material scale (not historic, not trivial).
- `china_remote_workforce` — remote-first org whose workforce/systematic
  hiring CURRENTLY gravitates to China (MyShell-class: Chinese-language
  remote hiring rounds, RMB payroll, no entity needed).
- `us_only` — US/foreign-operated; the ONLY China link is founder
  descent. (Verdict used to REMOVE from the roster.)

KEEP = china_hq | cn_owned | china_ops | china_remote_workforce.
REMOVE = us_only.

## Evidence rules
- CURRENT state (2025-2026), not founding story: a company that MOVED
  all ops out of China is us_only; a company that OPENED China ops is
  china_ops regardless of where it incorporated.
- Founder descent alone is NEVER sufficient. But "Chinese-owned /
  Chinese-operated / ops-gravity-to-China" IS (that is the user's point).
- Cite a live URL for every claim (company site, LinkedIn, Wikipedia,
  qcc/aiqicha for CN entities, Crunchbase/Craft for ownership, V2EX/Zhihu
  for China-remote hiring rounds, news).
- Distinguish CURRENT from HISTORIC China ops explicitly.
- If evidence is genuinely ambiguous → verdict `us_only` with a note
  "gray: <what a better source would settle>" (fail-conservative for
  roster removal, but record the grayness).

## Output format (write to the assigned file, one section per company)
```
## <Company>
- Verdict: <class>
- China ops / ownership / workforce: <2-5 sentences of evidence>
- Sources: <urls>
- Note: <wiring-relevant facts: board URL, ATS, US role counts if seen>
```
