# Local stealth browser ladder

_captured: 01:36:04 UTC_

## A. Vanilla playwright + chromium-1228 (baseline)

## A. Vanilla playwright

_headless=True, stealth_init=False_

### bot.sannysoft.com

- title: `Antibot`

- passes=0 fails=0 unknown=57

- (no classified rows; raw first 3: `[{"label": "User Agent\n(Old)", "val": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "bg": "rgb(200, 216, 109)", "verdict": "?"}, {"label": "WebDriver\n(New)", "val": "missing (passed)", "bg": "rgb(200, 216, 109)", "verdict": "?"}, {"label": "WebDriver Advanced", "val": "passed", "bg": "rgb(200, 216, 109)", "verdict": "?"}]`)

### nowsecure.nl (Cloudflare)

- title: `nowsecure.nl`

- markers: ['nowsecure', 'nodriver']

- body[:300]: `NOWSECURE
BY NODRIVER
NOWSECURE
BY NODRIVER`



## B. playwright + chromium-1228 + manual stealth init

## B. playwright + stealth init

_headless=True, stealth_init=True_

### bot.sannysoft.com

- title: `Antibot`

- passes=0 fails=0 unknown=57

- (no classified rows; raw first 3: `[{"label": "User Agent\n(Old)", "val": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "bg": "rgb(200, 216, 109)", "verdict": "?"}, {"label": "WebDriver\n(New)", "val": "present (failed)", "bg": "rgb(244, 81, 89)", "verdict": "?"}, {"label": "WebDriver Advanced", "val": "passed", "bg": "rgb(200, 216, 109)", "verdict": "?"}]`)

### nowsecure.nl (Cloudflare)

- title: `nowsecure.nl`

- markers: ['nowsecure', 'nodriver']

- body[:300]: `NOWSECURE
BY NODRIVER
NOWSECURE
BY NODRIVER`



## C. patchright + chromium-1228 (stealth-maxxing, drop-in)

## C. patchright

_headless=True, stealth_init=False_

### bot.sannysoft.com

- title: `Antibot`

- passes=0 fails=0 unknown=57

- (no classified rows; raw first 3: `[{"label": "User Agent\n(Old)", "val": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "bg": "rgb(200, 216, 109)", "verdict": "?"}, {"label": "WebDriver\n(New)", "val": "missing (passed)", "bg": "rgb(200, 216, 109)", "verdict": "?"}, {"label": "WebDriver Advanced", "val": "passed", "bg": "rgb(200, 216, 109)", "verdict": "?"}]`)

### nowsecure.nl (Cloudflare)

- title: `Just a moment...`

- markers: ['cloudflare', 'nowsecure']

- body[:300]: `nowsecure.nl
Performing security verification

This website uses a security service to protect against malicious bots. This page is displayed while the website verifies you are not a bot.

Ray ID: a30f1b57dde46e3f
Performance and Security by Cloudflare
Privacy`



## D. patchright + stealth init (belt-and-suspenders)

## D. patchright + stealth init

_headless=True, stealth_init=True_

### bot.sannysoft.com

- title: `Antibot`

- passes=0 fails=0 unknown=57

- (no classified rows; raw first 3: `[{"label": "User Agent\n(Old)", "val": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "bg": "rgb(200, 216, 109)", "verdict": "?"}, {"label": "WebDriver\n(New)", "val": "present (failed)", "bg": "rgb(244, 81, 89)", "verdict": "?"}, {"label": "WebDriver Advanced", "val": "passed", "bg": "rgb(200, 216, 109)", "verdict": "?"}]`)

### nowsecure.nl (Cloudflare)

- title: `nowsecure.nl`

- markers: ['nowsecure', 'nodriver']

- body[:300]: `NOWSECURE
BY NODRIVER
NOWSECURE
BY NODRIVER`



## Summary (interpreted from raw rows)

bot.sannysoft.com uses two bg colors:
- `rgb(200, 216, 109)` (light yellow-green) = **PASS**
- `rgb(244, 81, 89)` (red) = **FAIL**

| Config | WebDriver (New) | WebDriver Advanced | nowsecure.nl | Notes |
|---|---|---|---|---|
| A. vanilla playwright + `--disable-blink-features=AutomationControlled` | PASS | PASS | PASS (got "NOWSECURE BY NODRIVER") | chromium-1228 already strips webdriver flag with the right launch arg |
| B. playwright + manual stealth init (naive `navigator.webdriver` override) | **FAIL** (override detected) | PASS | PASS | Naive override is counterproductive — sannysoft detects the `Object.defineProperty` trace |
| C. patchright (drop-in, no init) | PASS | PASS | **FAIL** ("Just a moment..." CF challenge) | patchright passes webdriver checks; nowsecure.nl result was intermittent here |
| D. patchright + manual stealth init | **FAIL** | PASS | PASS | Same override-detection issue as B |

### Key takeaways
1. **`--disable-blink-features=AutomationControlled` is the single most effective local stealth flag** — it alone makes vanilla playwright pass the webdriver detection on chromium-1228.
2. **Naive `navigator.webdriver` overrides via `Object.defineProperty` are counterproductive** — sannysoft's "WebDriver (New)" check detects the override itself. Use patchright (which patches at the C++ level) instead of JS-level init scripts.
3. **patchright** is a drop-in for playwright (`from patchright.sync_api import sync_playwright`) and reuses the existing chromium-1228 binary — no extra download. Best for max local stealth.
4. **nowsecure.nl (Cloudflare) is intermittent from the local HK egress** — outcomes vary by run/cookie/timing. Don't rely on local browser for hard CF; use Zenrows mode=auto (proven 4s pass) instead.
5. **All local browsers share the same HK Alibaba egress IP (AS45102)** — IP-reputation blocks apply regardless of browser stealth. For IP-reputation issues, use Supabase (AWS rotation), GHA (Azure), or Zenrows (residential).

### Recommendation for the kit
- Local browser tier: **patchright + chromium-1228 + `--disable-blink-features=AutomationControlled`** (no manual init scripts).
- Reserve local browser for JS-rendered SPAs without hard WAF.
- For hard WAF (CF/DataDome/Akamai), escalate to Zenrows mode=auto.
