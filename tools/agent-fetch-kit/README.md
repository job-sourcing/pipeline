# agent-fetch-kit

A reusable **agent kit for resilient web fetching** from the Z.ai sandbox (and similar
constrained environments where the local egress IP is often blocklisted and naïve
HTTP clients get fingerprinted/blocked).

The kit ships an automatic **escalation ladder** that tries cheap local tactics first
and only escalates to paid/remote options when the target actually resists:

| Tier | Backend | Defeats | Cost | Speed |
|------|---------|---------|------|-------|
| 0 | local `curl_cffi` (Chrome TLS impersonation) | JA3 blocklists, UA filters | free | ~1s |
| 1 | Supabase edge proxy (15 AWS regions, rotating IP + random JA3, distill) | per-IP rate limits, geo, bandwidth billing | free | ~1s |
| 2 | Netlify edge scraper (fetch engine inline; chrome_impersonate/puppeteer queue broken on this deploy) | IP-class & JA3 diversity, batch + blob storage | free | ~1s inline |
| 3 | Firecrawl (managed scrape + JS render + stealth) | JS-rendered SPAs, mild CF | credits | ~5s |
| 4 | Zenrows (residential proxy + real browser + antibot) | Cloudflare / DataDome / Akamai hard | credits | ~10s |
| 5 | GitHub Actions remote compute (Azure IP + curl_cffi + system Chrome) | HK/CN IP reputation, ASN blocks | free | ~60s |
| 6 | Local stealth browser (patchright + chromium-1228, reuses playwright cache) | JS-rendered + fingerprint | free | ~5s |

## Quick start
```bash
./install.sh                 # pip install curl_cffi (+ optional stealth browsers), wires skill
source .env                  # load API keys (committed — git IS the disk here)
bin/wfetch https://example.com
bin/wfetch https://nowsecure.nl --antibot          # CF challenge → zenrows mode=auto
bin/wprobe                   # local egress + proxy + ladder health snapshot
bin/wgha https://ipinfo.io/json --mode impersonate   # remote Azure compute
```

## Docs
- `SKILL.md` — generalized skill instructions for future agent sessions (trigger & usage)
- `docs/findings.md` — full measured evaluation of every backend
- `docs/decision-matrix.md` — when to use which (the routing strategy)
- `docs/results/` — raw JSON/markdown from each evaluation run
