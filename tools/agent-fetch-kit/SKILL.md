---
name: agent-fetch-kit
description: Resilient web fetch toolkit with automatic escalation ladder across local curl_cffi (Chrome TLS impersonation), Supabase edge proxy (15 AWS regions, rotating IP + random JA3), Netlify edge scraper, Firecrawl, Zenrows (residential + antibot), GitHub Actions remote compute (Azure IP), and local patchright stealth browser. Use whenever you need to fetch a URL that may block you due to IP reputation, WAF, JA3 fingerprinting, JS rendering, or rate limits — and you want the agent to auto-pick the cheapest backend that succeeds.
read_when:
  - fetching a URL that returns 403/429/503/504
  - hitting "Just a moment..." or Cloudflare/DataDome/Akamai challenge pages
  - JA3 fingerprint blocklists (curl/python blocked but browsers work)
  - IP rate limits or per-IP throttling
  - geo-restricted content (need a specific country/region egress)
  - JS-rendered SPA where the content loads via JavaScript
  - HK/CN IP reputation blocking (need clean Azure/US/EU IP)
  - bandwidth-heavy pages (want distillation / 1000x reduction)
  - Reddit/Walmart/News sites that hard-block datacenter IPs
metadata:
  clawdbot:
    emoji: "🛰️"
    requires:
      bins: ["python3", "curl"]
      pips: ["curl_cffi", "requests", "patchright"]
allowed-tools: Bash(agent-fetch-kit:*)
---

# agent-fetch-kit — Resilient Web Fetch

A self-contained kit that lets an agent fetch any URL from a constrained sandbox
(e.g. the Z.ai container with HK Alibaba egress that's often blocklisted). It
ships an automatic **escalation ladder** — try cheap local tactics first,
escalate to paid/remote only when the target actually resists. Keys are read
from `.env` (see the SECRETS section) — no values are inlined in this doc.

---

## ⚡ TL;DR — pick a tool in 5 seconds

| Symptom you're seeing | Command to run first |
|---|---|
| "I just need to fetch a URL, don't know what's there" | `bin/wfetch <URL> --json` |
| Got 403/429/503 or "Just a moment..." CF page | `bin/wfetch <URL> --antibot --json` |
| Page is JS-rendered SPA (React/Vue, content loads after) | `bin/wfetch <URL> --render --json` |
| Need to geo-pin to a country | `bin/wfetch <URL> --region US --json` |
| Need to force a specific backend (debug) | `bin/wfetch <URL> --mode supabase --json` |
| Health-check all 7 backends + local egress | `bin/wprobe` |
| Need a clean Azure US/EU IP for ~60s | `bin/wgha <URL> --json` |

If unsure → start with `bin/wfetch <URL> --json` (auto mode picks the cheapest
backend that works). Escalation happens automatically.

---

## 🧭 STRATEGY — WHEN to use WHAT

Three orthogonal signals drive the choice. Read them in order:

### Signal 1: Is the target **hardening** against you?

- **No** (plain API, static HTML, blog, doc site) → use **plain fetch** (no flags).
  Router tries `local → supabase → netlify → firecrawl → zenrows → gha`.
- **Yes, mild** (403/429 from rate limits, JA3 blocklist) → use **plain fetch**;
  `supabase` (rotating AWS IP + random JA3) usually clears it.
- **Yes, JS challenge** (Cloudflare "Just a moment", DataDome, Akamai bot-detect)
  → add `--antibot`. This routes through `zenrows mode=auto` first (residential
  + real browser + adaptive stealth — proven to clear CF in ~4s).
- **Yes, hard WAF + need browser** (page renders via JS AND is behind CF) →
  `--antibot` already covers this; the antibot ladder falls through to a real
  browser backend.
- **JS-rendered SPA, no WAF** (quotes.toscrape.com/js/, single-page apps) → add
  `--render`. Router tries `browser → firecrawl → zenrows → gha`.

### Signal 2: Do you need to **pin** something?

- **Geography** (need a US/EU/AWS-region egress) → `--region <code>`.
  - supabase: `--region eu-west-1` (or any of 15 AWS regions)
  - zenrows:  `--region US` (2-letter ISO)
- **Distill** (only want one selector, e.g. `<title>` or `.price`) →
  `--extract <css>`. Only supabase supports this currently.
- **Raw HTML** (zenrows/firecrawl return markdown by default) → `--no-markdown`.
- **Longer wait** (slow SPA) → `--wait-ms 8000`.
- **Force backend** (debug, A/B) → `--mode <name>`.

### Signal 3: **Cost ceiling** — how much budget per call?

| Backend | Cost | When to spend it |
|---|---|---|
| `local` curl_cffi chrome131 | free | default for any non-blocked URL |
| `supabase` (rotating AWS) | free | 403/429 from per-IP rate limits |
| `netlify` (us-east-2 edge) | free | alt path; queued-scraper mode broken on free plan, but inline fetch works |
| `browser` (patchright) | free | JS-rendered SPAs without WAF |
| `gha` (Azure runner) | free (2000 min/mo) | bulk crawling via matrix; clean IP for ~60s |
| `firecrawl` basic | 1 credit | paid residential egress when free backends fail |
| `firecrawl` stealth | 5–10 credits | CF challenge that zenrows can't clear (rare) |
| `zenrows` mode=auto | 0.025 credits | hard CF/DataDome — proven best for CF |
| `zenrows` basic markdown | 0.001 credits | cheap markdown distillation if you don't need render |

**Monthly free budget:** Firecrawl 500 credits one-time; Zenrows 1000 credits
one-time; Supabase 500K req/month; GHA 2000 min/month. **The router
auto-preferences free backends** — you only spend credits when free ones fail.

### Decision matrix (one-shot reference)

| Scenario | First choice | Escalation | Why |
|---|---|---|---|
| Plain API / static page | `local` | `supabase` → `netlify` → `firecrawl` → `zenrows` → `gha` | free, fast, defeats JA3 blocklists |
| 403/429 per-IP rate limit | `supabase` | `zenrows` | 15 AWS IPs, rotates per call |
| Geo-restricted content | `supabase --region eu-west-1` | `zenrows --region US` → `gha` | region-pin per request |
| JA3 blocklist (non-browser TLS blocked) | `local` | `supabase` | chrome131 TLS impersonation |
| Cloudflare JS challenge | `--antibot` → `zenrows` | `firecrawl stealth` → `gha browser` | residential + real browser |
| DataDome / Akamai hard | `--antibot` → `zenrows` | `firecrawl stealth` | residential IP class |
| JS-rendered SPA, no WAF | `--render` → `browser` | `firecrawl waitFor` → `zenrows js_render` → `gha browser` | JS engine |
| Heavy pages / bandwidth | `supabase --extract css` | `firecrawl --no-markdown` | 1000x reduction |
| Bulk crawling | `firecrawl` or `gha` (matrix parallelism) | — | parallelism |
| IP-reputation graylist (HK/CN egress blocked) | `gha` | `supabase` → `zenrows` | clean IP class |
| Reddit / hard social | `--antibot` → `zenrows` | `gha` | proven 200 in 15s |

---

## 🔑 SECRETS — read them from `.env`, never inline real values

The kit reads these from `.env` at `$KIT_ROOT/.env` (loaded automatically by
`lib/fetchkit/config.py`). Real values live in your private secret store
(in this repo: `ingest/.env`, git-ignored in the public copy) — the block
below is a TEMPLATE of the variable names the kit expects. Fill it in from
your own accounts; do NOT commit real keys.

```bash
# === agent-fetch-kit .env (TEMPLATE — placeholder values only) ===
# GitHub PAT (scopes: repo, workflow) — used by bin/wgha to dispatch remote-scrape workflow
GH_TOKEN=ghp-your-github-pat-placeholder
GH_REPO=zmytone/agent-fetch-kit

# GitLab mirror (durable backup remote)
GITLAB_PAT=glpat-your-gitlab-pat-placeholder
GITLAB_REPO=ansgareutychisO/agent-fetch-kit

# Firecrawl (500 credits free plan; basic=1, stealth=5-10, JS render=+1)
FIRECRAWL_API_KEY=fc-your-firecrawl-key-placeholder

# Zenrows (1000 credits free plan; mode=auto antibot = 0.025 per call)
ZENROWS_API_KEY=your-zenrows-api-key-placeholder

# Supabase edge proxy (60 req/min/token, 500K/month, free)
SUPABASE_PROXY_URL=https://your-project-ref.supabase.co/functions/v1/proxy
SUPABASE_PROXY_TOKEN=your-supabase-proxy-token-placeholder

# Netlify scraper (inline fetch via blobs; free)
NETLIFY_SCRAPER_URL=https://your-site-name.netlify.app
NETLIFY_TOKEN=your-netlify-token-placeholder
NETLIFY_SITE_ID=your-netlify-site-id
NETLIFY_BLOBS_API=https://api.netlify.com/api/v1/blobs/your-netlify-site-id/site:scraper-results
```

**NOTE on GH PAT revocation:** GitHub's secret scanner auto-revokes PATs that
appear in chat or in any pushed git history. If `bin/wgha` returns 401, the
token in your `.env` has been revoked — ask the user for a fresh PAT (scopes:
`repo`, `workflow`) and update `GH_TOKEN` in `.env`.

---

## 🛠️ HOW — every tool, with copy-paste commands

### 0. Bootstrap (fresh sandbox, < 30s)

```bash
# Clone or pull — git IS the disk in the Z sandbox (overlay is ephemeral)
git clone https://github.com/zmytone/agent-fetch-kit.git /home/z/agent-kit \
  || (cd /home/z/agent-kit && git pull --ff-only)
cd /home/z/agent-kit

# GitLab mirror is the durable backup (pat valid)
git remote add gitlab https://oauth2:${GITLAB_PAT}@gitlab.com/ansgareutychisO/agent-fetch-kit.git 2>/dev/null || true

git config --global user.name "zmytone"
git config --global user.email "309033959+zmytone@users.noreply.github.com"

# Load credentials (auto-loaded by config.py at runtime; explicit here for shells)
source .env

# Install python deps + chmod bin/ + symlink into ~/.local/bin/
./install.sh

# Smoke test: health-check all 7 backends
bin/wprobe
```

### 1. `bin/wfetch URL [options]` — the universal fetcher (90% of uses)

Auto-picks the cheapest backend that succeeds; escalates on challenge markers.
Body → stdout (or `--out FILE`); metadata → stdout as JSON with `--json`.

```bash
# Plain fetch (auto ladder: local → supabase → netlify → firecrawl → zenrows → gha)
bin/wfetch https://example.com --json

# Hard CF challenge
bin/wfetch https://nowsecure.nl --antibot --json

# Reddit (hard social)
bin/wfetch https://reddit.com/r/technology.json --antibot --json

# Geo-pin to EU via supabase
bin/wfetch https://ipinfo.io/json --mode supabase --region eu-west-1 --json

# JS-rendered SPA
bin/wfetch https://quotes.toscrape.com/js/ --render --json

# Distill one selector (supabase only)
bin/wfetch https://example.com --mode supabase --extract title --json

# Write body to file (still get JSON metadata on stdout)
bin/wfetch https://example.com --out page.html --json

# Force a specific backend (debug)
bin/wfetch https://example.com --mode local --json
```

**Options (flags must come AFTER the URL — argparse subcommand):**
- `--mode auto|local|supabase|netlify|firecrawl|zenrows|gha|browser` — force a backend
- `--render` — JS rendering needed (SPA)
- `--antibot` — hard anti-bot target (CF/DataDome/Akamai); **takes precedence over `--render`**
- `--region CODE` — geo-pin (supabase x-region like `eu-west-1`; zenrows 2-letter like `US`)
- `--extract CSS` — supabase distillation (e.g. `title`, `h1`, `.price`)
- `--no-markdown` — prefer raw HTML (default: markdown for zenrows/firecrawl)
- `--timeout SECONDS` (default 30)
- `--impersonate TARGET` (default `chrome131`)
- `--wait-ms MS` (default 4000) — browser/JS wait
- `--out FILE` — write body to file
- `--json` — output JSON metadata (backend/status/attempts/elapsed) instead of body
- `--verbose` — print each attempt to stderr (does NOT contaminate stdout body)

**JSON output shape (when `--json`):**
```json
{
  "url": "...",
  "backend": "supabase",
  "mode": "auto",
  "status": 200,
  "ok": true,
  "challenged": false,
  "bytes": 1234,
  "elapsed_ms": 950,
  "attempts": [
    {"backend": "local", "status": 403, "challenged": true, "elapsed_ms": 800},
    {"backend": "supabase", "status": 200, "challenged": false, "elapsed_ms": 950}
  ]
}
```

### 2. `bin/wprobe` — ladder health snapshot

```bash
bin/wprobe
# Returns JSON: local egress IP/ASN, per-backend configured? status + elapsed_ms
# Exit 0 if ≥1 backend reachable; exit 1 if none
```

Use this **before** any non-trivial fetch campaign to know which backends are
live in this session. Also use it after a `wfetch` failure to see whether a
backend went down.

### 3. `bin/wgha URL [options]` — GHA remote compute shortcut

Dispatches the `remote-scrape` workflow on a GitHub-hosted Ubuntu runner
(Azure US/EU IP), polls for completion, downloads the body+meta artifact.

```bash
# Azure IP + chrome131 TLS impersonation (default)
bin/wgha https://ipinfo.io/json --json

# Azure + system Chrome headless (JS-rendered)
bin/wgha https://quotes.toscrape.com/js/ --mode browser --json
```

**Modes:** `curl` (plain requests) | `impersonate` (curl_cffi chrome131 — default) | `browser` (system Chrome headless)

**When to use `wgha` vs `wfetch --mode gha`:** They're the same backend under
the hood. `wgha` is a shortcut for "skip the ladder, go straight to Azure".
Use it when you know in advance the local + paid backends will fail (e.g.
HK IP hard-blocked by target).

---

## 📊 Per-backend measured capabilities

Backed by raw eval data in `docs/results/`. See `docs/findings.md` for full
methodology and `docs/decision-matrix.md` for the tradeoff table.

| Backend | Egress | JA3/JA4 | JS render | CF pass | Reddit pass | Cost | Speed |
|---|---|---|---|---|---|---|---|
| local curl_cffi | HK Alibaba | Chrome 131 ✓ | ✗ | ✗ | ✗ | free | ~1s |
| supabase | 15 AWS regions (rotating) | rustls (random JA3) | ✗ | ✗ | ✗ | free | ~1s |
| netlify | AWS us-east-2 | Node/Deno (non-browser JA4) | ✗ (inline) | ✗ | ✗ | free | ~1s |
| firecrawl basic | residential (Cox US) | managed | ✓ (waitFor) | ✗ | ✗ (403) | 1 credit | ~1-5s |
| firecrawl stealth | residential | managed + browser | ✓ | ✓ (10s) | ✗ (403) | 5-10 credits | ~10s |
| zenrows mode=auto | residential (55M pool) | managed + real browser | ✓ | ✓ (4s, clean) | ✓ (15s, 200) | 0.025 credits | ~4-15s |
| gha (Azure runner) | Azure US/EU (clean) | curl_cffi chrome131 / system Chrome | ✓ (browser) | limited | limited | free | ~60s |
| local patchright | HK Alibaba | Chrome 131 + stealth | ✓ | intermittent | ✗ | free | ~5-10s |

---

## 🧠 How the router decides (auto mode)

1. Check `~/.agent-fetch-kit/history.json` for a cached "winning backend" for this
   domain. If found and not stale (24h), try it first.
2. Otherwise walk the ladder for the selected mode (plain / `--render` / `--antibot`).
3. After each attempt, call `is_challenged(status, body)`:
   - Status in {403, 429, 503, 504} → challenged → escalate
   - Body contains markers (`just a moment`, `cf-chl`, `cf-ray-`, `datadome`,
     `px-captcha`, `incapsula`, `verify you are human`) → challenged → escalate
   - Status in {404, 410, 422} (terminal) → **DO NOT escalate** — the URL genuinely doesn't exist
4. On success or terminal status, return the FetchResult.
5. On all-backends-fail, return the last attempt's result (not None) so the
   caller gets the most informative failure.
6. History is rewritten after each attempt (success or failure), so the next
   call on the same domain starts from the known-winner.

---

## 🧪 Testing & iterating

```bash
# Full suite (82 tests, ~0.5s)
python3 -m pytest tests/ -q

# Run a specific backend live
bin/wfetch https://example.com --mode local --json
bin/wfetch https://nowsecure.nl --antibot --json      # zenrows mode=auto

# Re-run an evaluation (raw data → docs/results/)
python3 scripts/eval_zenrows.py
```

To add a new backend:
1. Implement `backend_<name>(url, config, **opts) -> FetchResult` in `lib/fetchkit/backends.py`
2. Add it to `BACKENDS` dict in the same file
3. Insert it into the relevant ladder in `lib/fetchkit/core.py` (`_PLAIN_LADDER`,
   `_RENDER_LADDER`, `_ANTIBOT_LADDER`)
4. Add a `Config.has_<name>()` method in `lib/fetchkit/config.py` (used by router
   for graceful "not configured" skip)
5. Add tests in `tests/test_unit.py` or `tests/test_review_fixes.py`

---

## ⚠️ Z-sandbox gotchas (read once, never again)

- **Egress:** HK Alibaba AS45102 (47.57.x.x / 8.212.10.x). Local TLS baseline is
  OpenSSL/non-browser (JA3 `a1ebe7f9...`) — easily blocklisted. Always use
  `local` (curl_cffi chrome131) instead of plain `requests`/`curl`.
- **No sudo** — can't apt install. Use pip + the existing `/home/z/.venv` venv.
  Playwright chromium cache exists at the default location.
- **Watchdog:** `/home/z/my-project` is force-checked-out to `main` every ~20s.
  Work in `/home/z/agent-kit` (or another path under `/home/z/`) — NOT in
  `/home/z/my-project`. The `/home/z/my-project/skills/` folder is **platform
  noise** (auto-provided by the Z.ai runtime) — do NOT install or depend on it.
- **Terminal lockout hazard:** NEVER loop `caddy run/start/stop/reload/file-server`
  commands, NEVER curl-loop internal ports 12600/19001/19005/19006. These cause
  an irreversible 403 session lockout. Keep bash toolcalls under 120s; split
  long polling into separate toolcalls.
- **Process persistence:** `nohup ... &` dies at toolcall end. Use double-fork
  (see `scripts/_gitlab_create_daemon.py` pattern) for daemons that must survive.
- **Recycle:** overlay (`/home/z/agent-kit`, `~/.gitconfig`, `~/.local/`) is
  ephemeral. `/home/sync/` (ossfs) + `/home/z/my-project/upload/` + git remotes
  are durable. **Always push before risky operations.**
---

## 📁 Repo layout (orientation)

```
agent-fetch-kit/
├── SKILL.md                      # this file — first thing an agent reads
├── README.md                     # human-readable project overview
├── .env                          # live secrets (committed; git IS the disk)
├── install.sh                    # bootstrap script (deps + bin/ + ~/.local/bin)
├── pyproject.toml                # pytest config
├── bin/
│   ├── wfetch                    # universal fetcher (bash wrapper around cli.py)
│   ├── wprobe                    # ladder health snapshot
│   └── wgha                      # GHA remote compute shortcut
├── lib/fetchkit/
│   ├── __init__.py               # public exports (fetch, probe, Config, FetchResult)
│   ├── __main__.py               # `python -m fetchkit` entry
│   ├── config.py                 # .env loader, Config class, has_<backend> checks
│   ├── detect.py                 # is_challenged(status, body) — challenge markers
│   ├── history.py                # ~/.agent-fetch-kit/history.json (domain memory)
│   ├── backends.py               # 7 backends: local/supabase/netlify/firecrawl/zenrows/gha/browser
│   ├── core.py                   # router (ladders), probe(), FetchResult
│   └── cli.py                    # argparse CLI (fetch/probe/gha subcommands)
├── scripts/
│   ├── gha_fetch.py              # GHA-side fetcher (invoked by remote-scrape.yml)
│   └── eval_*.py                 # raw evaluation scripts (re-runnable)
├── tests/
│   ├── test_unit.py              # 39 original tests
│   └── test_review_fixes.py      # 43 review-fix tests (round-1 peer review)
├── docs/
│   ├── findings.md               # full evaluation findings
│   ├── decision-matrix.md       # when-to-use-what deep dive
│   ├── results/                  # raw eval data (json + md per backend)
│   └── worklog.md                # build log (compact)
└── .github/workflows/
    └── scrape.yml                # remote-scrape workflow (workflow_dispatch)
```
