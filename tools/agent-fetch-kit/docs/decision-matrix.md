# Decision matrix — when to use which backend

This is the routing strategy that `bin/wfetch` (auto mode) implements automatically.
Manual override: `bin/wfetch URL --mode <backend>`.

## TL;DR — the routing flowchart

```
Need to fetch a URL?
├── Is it a known hard-WAF target (CF/DataDome/Akamai/Reddit)?
│   └── YES → `--antibot` → zenrows mode=auto → firecrawl stealth → gha browser
├── Does it require JavaScript rendering?
│   └── YES → `--render` → browser (patchright) → firecrawl waitFor → zenrows js_render → gha browser
├── Is the HK egress IP blocked/rate-limited?
│   └── YES → supabase (rotating AWS IP) → netlify (us-east-2) → gha (Azure)
├── Is it geo-restricted?
│   └── YES → supabase --region <code> → zenrows --region <country>
├── Heavy page, want only a fragment?
│   └── YES → supabase --extract <css> (distill, 1000x reduction)
├── Bulk crawling / parallelism?
│   └── YES → firecrawl (managed crawl) or gha (matrix parallel jobs)
└── None of the above (plain static/API)
    └── local curl_cffi chrome131 (free, ~1s, defeats JA3 blocklists)
```

## Detailed decision matrix

| Scenario | First choice | Escalation order | Why | Cost |
|---|---|---|---|---|
| Plain API / static HTML | `local` (curl_cffi chrome131) | `supabase` → `netlify` → `firecrawl` → `zenrows` → `gha` | Free, fast, real Chrome TLS | free → credits |
| JA3 blocklist (curl blocked but browser works) | `local` (chrome131 impersonation) | `supabase` (random JA3) → `zenrows` | TLS-layer bypass | free → credits |
| Per-IP rate limit (429) | `supabase` (rotating IP, raw mode) | `zenrows` (residential) → `gha` (Azure) | 15K AWS IPs, rotates per call | free → credits |
| IP-reputation graylist (HK/CN egress blocked) | `gha` (Azure US/EU IP) | `supabase` (AWS) → `zenrows` (residential) | Clean IP class | free → credits |
| ASN-blocked (AWS/Azure banned) | `zenrows` (residential) | `local` (HK Alibaba sometimes OK) | Residential IP class | 0.025 credits |
| Geo-restricted content | `supabase --region eu-west-1` | `zenrows --region US` → `gha` | Region-pin per request | free → credits |
| JS-rendered SPA, no WAF | `browser` (patchright+chromium-1228) | `firecrawl waitFor` → `zenrows js_render` → `gha browser` | Local browser is free + fast | free → credits |
| Cloudflare JS challenge | `zenrows mode=auto` (`--antibot`) | `firecrawl stealth` → `gha browser` | Residential + real browser | 0.025 credits |
| nowsecure.nl / hard CF | `zenrows mode=auto` | `firecrawl stealth` → `gha browser` | Zenrows proven 4s clean pass | 0.025 credits |
| DataDome / Akamai hard | `zenrows mode=auto` (`--antibot`) | `firecrawl stealth` | Residential IP + real browser | 0.025 credits |
| Reddit / hard social | `zenrows mode=auto` (`--antibot`) | `gha` | Proven 200 in 15s (Firecrawl stealth 403) | 0.025 credits |
| Heavy page, want fragment | `supabase --extract "css"` (distill) | `firecrawl --no-markdown` | 1000x bandwidth reduction | free → credits |
| Bulk crawling (many URLs) | `gha` (matrix parallel jobs, one Azure IP per job) | `firecrawl` (single-URL scrape; no bulk crawl endpoint exposed) | Parallelism | free → credits |
| Need clean Azure IP + JS | `gha browser` | — | Azure IP + system Chrome | free (~60s) |
| Need clean Azure IP + chrome TLS (no JS) | `gha impersonate` | — | Azure IP + curl_cffi chrome131 | free (~60s) |

## Backend capability matrix (measured)

| Backend | Egress IP | TLS fingerprint | JS render | CF pass | Reddit pass | Markdown | Cost | Speed |
|---|---|---|---|---|---|---|---|---|
| local curl_cffi | HK Alibaba (rotates 3) | Chrome 131 JA4 ✓ | ✗ | ✗ | ✗ (403) | ✗ | free | ~1s |
| supabase | 15 AWS regions (rotating) | rustls (random JA3, non-Chrome JA4) | ✗ | ✗ | ✗ (403) | ✗ | free | ~1s |
| netlify | AWS us-east-2 | Node/Deno (52-cipher non-browser JA4) | ✗ (fetch only; queue broken) | ✗ | ✗ (403) | ✗ | free | ~1s |
| firecrawl basic | residential (Cox US) | managed | ✓ (waitFor) | ✗ (408) | ✗ (403) | ✓ | 1 credit | ~1-5s |
| firecrawl stealth | residential | managed + browser | ✓ | ✓ (10s) | ✗ (403) | ✓ | 5-10 credits | ~10s |
| zenrows mode=auto | residential (55M pool) | managed + real browser | ✓ | ✓ (4s, clean) | ✓ (200, 15s) | ✓ | 0.025 credits | ~4-15s |
| gha (Azure runner) | Azure US/EU (clean) | curl_cffi chrome131 / system Chrome | ✓ (browser) | limited* | limited* | ✗ | free | ~60s |
| local patchright | HK Alibaba | Chrome 131 + stealth | ✓ | intermittent | ✗ | ✗ | free | ~5-10s |

*gha: code implemented; live test blocked on fresh GitHub PAT.

## Per-backend one-liners

```bash
# local (curl_cffi chrome131 — real Chrome TLS from HK)
bin/wfetch https://example.com --mode local --json

# supabase (rotating AWS IP, raw mode hides traceparent)
bin/wfetch https://example.com --mode supabase --json
# supabase with geo-pin
bin/wfetch https://ipinfo.io/json --mode supabase --region eu-west-1
# supabase with distill (extract only the title — 1000x bandwidth reduction)
bin/wfetch https://example.com --mode supabase --extract title --json

# netlify (alt AWS us-east-2 edge, batch + blob)
bin/wfetch https://example.com --mode netlify --json

# firecrawl (managed scrape — residential egress, markdown, JS render, stealth for CF)
bin/wfetch https://example.com --mode firecrawl --json
# firecrawl stealth for CF
bin/wfetch https://nowsecure.nl --mode firecrawl --antibot --json

# zenrows (residential + real browser + antibot — best for CF/Reddit)
bin/wfetch https://example.com --mode zenrows --json
# zenrows mode=auto for hard WAF
bin/wfetch https://reddit.com/r/technology.json --mode zenrows --antibot --json
# zenrows with geo-pin
bin/wfetch https://example.com --mode zenrows --antibot --region US --json

# gha (Azure IP + curl_cffi/browser — clean IP class)
bin/wgha https://ipinfo.io/json --mode impersonate --json
bin/wgha https://quotes.toscrape.com/js/ --mode browser --json

# local stealth browser (patchright + chromium-1228)
bin/wfetch https://quotes.toscrape.com/js/ --mode browser --json
```

## Auto-escalation (the default)

```bash
# auto: tries local → supabase → netlify → firecrawl → zenrows → gha
# escalates on 403/429/503 or challenge markers (cf/datadome/captcha/etc.)
bin/wfetch https://example.com --json

# --render: skips local/supabase/netlify (no JS), tries browser → firecrawl → zenrows → gha
bin/wfetch https://quotes.toscrape.com/js/ --render --json

# --antibot: skips everything except zenrows → firecrawl stealth → gha browser
bin/wfetch https://nowsecure.nl --antibot --json
```

The router caches which backend succeeded per-domain in `~/.agent-fetch-kit/history.json`
and tries it first on subsequent fetches of the same domain — saving credits on repeat visits.
