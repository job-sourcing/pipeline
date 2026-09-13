# Findings — agent-fetch-kit backend evaluation

_Captured: 2026-08-26, Z.ai sandbox (HK Alibaba AS45102 egress, 2 vCPU/4GB, no sudo, playwright+chromium-1228 cached)_

This document consolidates the measured capabilities of every backend in the kit.
Raw data: `docs/results/<backend>.md` + `docs/results/<backend>/` artifacts.

## 1. Local environment baseline (the thing we're escaping)

| Property | Value |
|---|---|
| Egress IP | 47.57.232.232 / 47.57.242.119 / 8.212.10.159 (rotates across 3 HK Alibaba IPs) |
| ASN | AS45102 Alibaba (US) Technology Co., Ltd. |
| City | Hong Kong |
| curl TLS (urllib/requests) | JA3 `a1ebe7f9...` JA4 `t13d1713h1_ab0a1bf427ad_89ab6efea773` (OpenSSL, non-browser, HTTP/1.1) |
| curl_cffi chrome131 | JA3 `b0a436ca...` JA4 `t13d1516h2_8daaf6152771_02713d6af862` (real Chrome 131 signature, HTTP/2 fp `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p`) |
| Toolchain | python 3.12.13 (venv), git 2.47.3, curl 8.14.1, node 24, bun 1.3.14, jq 1.7 |
| Pre-installed | requests, httpx, aiohttp, playwright + chromium-1200/1228, bs4, lxml |
| Not installed | gh (downloaded to ~/.local/bin), curl-impersonate binary (use curl_cffi instead), chrome/firefox system binaries |
| sudo | NO (password required) |
| Disk | 9.9GB rootfs (9.2GB free), `/home/sync/` ossfs durable, `/home/z/my-project/upload/` durable |

**Key insight:** the local curl/python TLS fingerprint (`a1ebe7f9...`) is trivially detectable as non-browser by any JA3 blocklist. **Always use `curl_cffi` with `impersonate="chrome131"`** instead of plain `requests`/`curl` — it produces a real Chrome 131 JA4 + HTTP/2 fingerprint.

## 2. Backend evaluation matrix

### Tier 0 — local curl_cffi (`backend_local`)

- **Cost:** free
- **Speed:** ~28ms (example.com), ~225ms (ipinfo.io with TLS handshake)
- **Egress:** HK Alibaba AS45102 (same as sandbox)
- **TLS:** real Chrome 131 JA4 `t13d1516h2_8daaf6152771_02713d6af862` + akamai h2 fp `1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p` — passes JA3 blocklists
- **JS render:** ✗
- **CF/DataDome:** ✗ (no JS engine, no IP rotation)
- **Reddit:** ✗ (HTTP 403 — Reddit blocks non-browser UAs on datacenter IPs)
- **Best for:** static pages, APIs, JA3-blocklisted sites where IP reputation is fine
- **Limit:** same HK IP — any per-IP rate limit or IP-reputation block still applies

### Tier 1 — Supabase edge proxy (`backend_supabase`)

- **Cost:** free (60 req/min/token, 500K/month)
- **Speed:** ~200ms proxy timing, ~700ms total (incl. ipinfo roundtrip from HK to AWS edge)
- **Egress:** 15 AWS edge regions, ~1000 rotating IPs each (~15K total). Auto-routes to nearest (ap-northeast-2 Seoul from HK); pin via `x-region` header.
- **Region pinning:** ✅ all 8 tested regions return correct geo IPs (us-east-1→Ashburn, eu-west-1→Dublin, ap-southeast-1→Singapore, sa-east-1→São Paulo, ca-central-1→Montréal, etc.)
- **IP rotation:** ✅ 5/5 unique IPs in 5 sequential calls
- **TLS:** rustls (NOT real Chrome). JA3 randomizes per call (5/5 distinct hashes), but JA4 is stable per mode: `t13d1011h2_61a7ad8aa9b6_3fcd1a44f3e3` (fetch/http2) or `t13d1011h1_...` (raw, HTTP/1.1). **Sophisticated WAFs can identify rustls JA4** (10 cipher suites vs Chrome's 15-16).
- **traceparent header:** `fetch` mode ADDS traceparent (target sees `Root=1-...`); `raw` mode does NOT. Use `raw` for stealth.
- **Distill:** ✅ `extract=title/h1/.css` returns distilled JSON (1000x bandwidth reduction)
- **POST:** ✗ returns 405 on this instance (GET-only in practice)
- **JS render:** ✗
- **CF/DataDome:** ✗ (rustls JA4 detectable + AWS ASNs blocklisted by CF)
- **Best for:** per-IP rate limits, geo-pinning, bandwidth reduction via distill
- **Limit:** AWS ASNs (AS16509/14618) — sites blocking AWS datacenter IPs still block; rustls TLS fingerprint (not real Chrome)

### Tier 2 — Netlify edge scraper (`backend_netlify`)

- **Cost:** free (50 sync jobs / 500 queue)
- **Speed:** ~70-130ms/URL (fetch engine inline)
- **Egress:** AWS us-east-2 (Columbus Ohio, AS16509 — 3.143.247.159)
- **TLS:** Node.js/Deno fetch fingerprint — JA4 `t13d5212h1_b262b3658495_8e6e362c5eac` (**52 cipher suites, HTTP/1.1** — trivially detectable as non-browser)
- **Engines:** `fetch` (inline sync ✓), `chrome_impersonate` (requires queue=true — **queue stuck pending, never processes**), `puppeteer` (requires queue=true — **same queue issue**)
- **Batch + blob storage:** ✅ (3-job batch completed in 388ms; blob retrieval via Netlify Blobs API works)
- **JS render:** ✗ (only fetch engine works; puppeteer queue broken)
- **CF/DataDome:** ✗
- **Best for:** batch fetch with alternate AWS region (us-east-2, different from Supabase's auto-routed edge) + blob storage for async retrieval
- **Limit:** queue mode broken on this deploy (chrome_impersonate/puppeteer jobs stay "pending" indefinitely). Treat as fetch-only.

### Tier 3 — Firecrawl v2 (`backend_firecrawl`)

- **Cost:** free plan 500 credits one-time. basic=1, stealth≈5-10, JS render=+1
- **Speed:** ~900ms basic, ~10s stealth
- **Egress (basic proxy):** residential! 195.64.115.143 = AS22773 Cox Communications (Arlington VA) — NOT datacenter
- **TLS:** managed (not exposed)
- **JS render:** ✅ `waitFor` parameter (quotes.toscrape.com/js/ rendered correctly)
- **Cloudflare:**
  - basic proxy: ✗ HTTP 408 timeout (90s) — basic can't pass CF
  - **stealth proxy: ✅ HTTP 200 in ~10s** — passes scrapingcourse.com/cloudflare-challenge AND nowsecure.nl
- **Reddit:** ✗ HTTP 403 even with stealth (Reddit hard-blocks)
- **Markdown:** ✅ clean markdown extraction with metadata (title, sourceURL, proxyUsed)
- **Best for:** JS-rendered SPAs, mild CF challenges, markdown extraction, residential egress
- **Limit:** can't pass Reddit/DataDome hard; credits are one-time on free plan

### Tier 4 — Zenrows v1 (`backend_zenrows`)

- **Cost:** free plan 1000 credits. basic markdown=0.001, **mode=auto (js_render+premium_proxy)=0.025** (~40 mode=auto calls or 1000 basic)
- **Speed:** ~1s basic, ~4-15s mode=auto
- **Egress:** residential (55M IP pool per docs; Zenrows blocks IP-echo endpoints so can't measure directly)
- **TLS:** managed + real browser (mode=auto)
- **JS render:** ✅ (via mode=auto)
- **Cloudflare:**
  - **nowsecure.nl (hard CF): ✅ HTTP 200 in 4s, NO CF markers** (genuine pass — got "NOWSECURE BY NODRIVER" content, not the challenge page)
  - scrapingcourse CF challenge: ✅ HTTP 200 in 12s
- **Reddit:** ✅ **HTTP 200 in 15s** (mode=auto passes Reddit's antibot — Firecrawl stealth FAILED here with 403)
- **Adaptive Stealth Mode (`mode=auto`):** auto-manages js_render + premium_proxy; billed only for successful attempts
- **Blocks:** quotes.toscrape.com (REQS001 "Requests to this domain are forbidden" — Zenrows blocks known scraping test sites), ipinfo.io (IP-harvesting block)
- **Best for:** hard WAF (CF/DataDome/Akamai), Reddit, residential IP needs
- **Limit:** blocks scraping test sites + IP echo endpoints; 0.025 credits per mode=auto call (budget-limited)

### Tier 5 — GitHub Actions remote compute (`backend_gha`)

- **Status:** ⚠️ BLOCKED on GitHub PAT (old PAT auto-revoked by secret scanning). Code implemented + unit-tested; live e2e pending fresh PAT.
- **Cost:** free 2000 min/month for private repos (~1 min per dispatch)
- **Speed:** ~60s end-to-end (runner startup ~30s + scrape ~5-20s + artifact download)
- **Egress:** Azure (GitHub-hosted ubuntu runners on Azure — clean US/EU IPs, distinct from HK Alibaba + AWS)
- **Modes:** `curl` (plain requests), `impersonate` (curl_cffi chrome131 — real Chrome TLS from Azure IP), `browser` (system google-chrome --headless=new --dump-dom)
- **Matrix:** `urls` input as JSON array → parallel jobs, each on a separate Azure VM (distinct IP per job)
- **Artifacts:** uploaded via actions/upload-artifact@v4; wgha client downloads + extracts
- **JS render:** ✅ (browser mode)
- **CF/DataDome:** limited (Azure datacenter IPs still blocklisted by hard WAF, but better than HK for IP-reputation-only blocks)
- **Best for:** IP-reputation graylist (HK/CN egress blocked), bulk parallel scraping, clean Azure IP class
- **Limit:** ~60s overhead per dispatch (cold start); GHA private repo minute quota; Azure datacenter IPs still blocklisted by advanced WAF

### Tier 6 — Local stealth browser (`backend_browser`)

- **Cost:** free
- **Speed:** ~5-10s (with 4-8s JS wait)
- **Egress:** HK Alibaba (same as sandbox — IP-reputation blocks still apply)
- **Stack:** patchright (drop-in for playwright, patches at C++ level) + chromium-1228 (cached at `/home/z/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome`)
- **JS render:** ✅
- **Key flags:** `--no-sandbox --disable-dev-shm-usage --disable-blink-features=AutomationControlled` (the last one alone makes vanilla playwright pass webdriver detection)
- **bot.sannysoft.com results:**
  - vanilla playwright + `--disable-blink-features=AutomationControlled`: **PASSES** WebDriver (New) + WebDriver Advanced checks
  - **manual `navigator.webdriver` override via `Object.defineProperty`: COUNTERPRODUCTIVE** — sannysoft detects the override itself ("WebDriver (New)" → red FAIL). Don't use naive init scripts.
  - patchright (no init script): PASSES webdriver checks (same as vanilla + flag)
- **nowsecure.nl (CF):** intermittent — vanilla passed once, patchright failed once, patchright+stealth passed once. Don't rely on local browser for hard CF.
- **Best for:** JS-rendered SPAs without hard WAF
- **Limit:** same HK IP (IP-reputation blocks apply); intermittent on hard CF; no better than `--disable-blink-features=AutomationControlled` flag alone

## 3. WAF ladder (head-to-head)

| Target | local | supabase | netlify | firecrawl basic | firecrawl stealth | zenrows mode=auto | local browser | gha* |
|---|---|---|---|---|---|---|---|---|
| example.com (no WAF) | ✅ 200 | ✅ 200 | ✅ 200 | ✅ 200 | ✅ 200 | ✅ 200 | ✅ 200 | ✅* |
| ipinfo.io/json | ✅ 200 | ✅ 200 | ✅ 200 | ✅ 200 | ✅ 200 | ❌ 400 (blocked) | ✅ 200 | ✅* |
| quotes.toscrape.com/js/ (JS) | ❌ no JS | ❌ no JS | ❌ no JS | ✅ 200 (waitFor) | ✅ 200 | ❌ 400 (REQS001) | ✅ 200 | ✅* |
| scrapingcourse CF challenge | ❌ | ❌ | ❌ | ❌ 408 (90s) | ✅ 200 (10s) | ✅ 200 (12s) | intermittent | limited* |
| nowsecure.nl (hard CF) | ❌ | ❌ | ❌ | ❌ | ✅ 200 (8.8s) | ✅ 200 (4s, clean) | intermittent | limited* |
| reddit.com/r/technology.json | ❌ 403 | ❌ 403 | ❌ 403 | ❌ | ❌ 403 | ✅ 200 (15s) | ❌ | limited* |

*gha column: code implemented but live test blocked on fresh GitHub PAT.

## 4. Credit economics summary

| Backend | Free quota | Per-call cost | Notes |
|---|---|---|---|
| local curl_cffi | unlimited | 0 | — |
| supabase | 500K/month, 60/min | 0 | — |
| netlify | 50 sync / 500 queue | 0 | queue broken on this deploy |
| firecrawl basic | 500 one-time | 1 credit | markdown + JS render |
| firecrawl stealth | 500 one-time | ~5-10 credits | CF bypass |
| zenrows basic | 1000 | 0.001 credits | markdown |
| zenrows mode=auto | 1000 | 0.025 credits | js_render + premium_proxy + antibot |
| gha | 2000 min/month (private) | ~1 min | ~60s overhead per dispatch |
| local browser | unlimited | 0 | — |

**Strategy:** local → supabase → netlify → firecrawl basic → zenrows mode=auto → gha. The router's domain memory caches which backend succeeded per-domain, so subsequent fetches skip the escalation and go straight to the working backend (saving credits).

## 5. Key gotchas

1. **GitHub PATs shared in chat get auto-revoked by secret scanning.** Always verify the PAT works (`curl -H "Authorization: token ..." https://api.github.com/user`) before relying on it. Ask user for fresh PAT if 401.
2. **GitLab WAF blocks HK IPs ~1/3 of the time** (probabilistic 403). Retry pushes 2-3 times.
3. **Watchdog force-checks-out `/home/z/my-project` to `main` every ~20s.** Work in `/home/z/agent-kit` (or another path under `/home/z/`). Never rely on `/home/z/my-project` for durable work.
4. **Terminal lockout hazard:** never loop `caddy` commands or curl-loop internal ports 12600/19001/19005/19006. Causes irreversible 403 session lockout. Keep bash toolcalls under 120s.
5. **Process persistence:** `nohup ... &` dies at toolcall end. Use double-fork (see `scripts/_gitlab_create_daemon.py`) for daemons.
6. **Recycle wipes overlay** (`/home/z/agent-kit`, `~/.gitconfig`, `~/.local/`). Durable: `/home/sync/`, `/home/z/my-project/upload/`, git remotes. Always push before risky ops.
7. **Naive `navigator.webdriver` JS overrides are counterproductive** — sannysoft detects the `Object.defineProperty` trace. Use patchright (C++ level patches) or just `--disable-blink-features=AutomationControlled`.
8. **Zenrows blocks IP-echo endpoints** (ipinfo.io/json returns 400 REQS001) — can't directly measure Zenrows's residential egress IP. Use `bot.sannysoft.com` or a target page that shows the caller IP.
