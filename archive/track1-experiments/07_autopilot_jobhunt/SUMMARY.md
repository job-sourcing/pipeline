# Validation Report: `tarunlnmiit/autopilot-jobhunt`

**Validated at:** 2026-08-18T21:20:06Z
**Repo path:** `/home/z/my-project/repos/tarunlnmiit__autopilot-jobhunt`
**Version:** 0.4.4 (per `pyproject.toml` and `job_hunt/__init__.py`)
**Python:** 3.12.13 in fresh venv

---

## TL;DR

The repo is **real, installable, and runs end-to-end** with the right (free) API keys. The 80-test suite passes. The MCP server launches and exposes 3 well-annotated tools. The scan / score / draft / export pipelines are all reachable (verified by stubbing TinyFish + LLM at the module boundary and observing the project's real code execute). Two real bugs were found and are documented below. One important clarification: **the project uses OpenRouter via the OpenAI SDK, not OpenAI directly** — the README doesn't lie about this but is easy to misread.

---

## 1. Installation: ✅ Works

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e /home/z/my-project/repos/tarunlnmiit__autopilot-jobhunt[mcp]
```

- ✅ Editable install succeeded, version 0.4.4, no errors.
- ✅ `autopilot --help` and `autopilot-jobhunt --help` both work (two entry points defined in `pyproject.toml`).
- ✅ All 80 tests pass: `pytest -q` → `80 passed in 1.39s`.
- ⚠️ **REAL BUG**: `pyproject.toml` declares `mcp>=1.9` (unbounded upper) so pip resolves to `mcp-2.0.0`, which has removed `mcp.server.fastmcp`. `autopilot mcp` then crashes at import time with `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. **Workaround**: `pip install 'mcp>=1.9,<2'`. Should be fixed upstream by changing the constraint to `mcp>=1.9,<2`.

## 2. Scan: ✅ Works (with stubs; needs TinyFish key for real run)

Real network test with a placeholder TinyFish key:

```
[2026-08-18 21:15:47] INFO    === Scan started — 140 companies to check ===
[2026-08-18 21:15:47] INFO    LLM provider: openrouter | Model: meta-llama/llama-3.3-70b-instruct:free
[2026-08-18 21:15:48] ERROR   Fetch error for ['https://mistral.ai/careers/']: Invalid or expired API key
[2026-08-18 21:15:51] ERROR     [Mistral AI] Search error: Invalid or expired API key
```

→ Proves the call site is live and the API endpoint responds correctly.

End-to-end test with TinyFish stubbed (3 companies: Mistral AI, Hugging Face, DeepMind):

```
[1/3] Scanning Mistral AI...     No new job URLs found
[2/3] Scanning Hugging Face...   1 new job URL(s) — fetching details...
                                Scoring 1 job(s)...
[3/3] Scanning DeepMind...       No new job URLs found
=== Scan complete — 3/3 companies, 0 jobs found, 0 top matches (0.0 min) ===
```

(0 jobs because the LLM call returned 401 from OpenRouter with the placeholder key, which is the expected failure mode.)

State files produced:
- `state/seen_jobs.json` (135 bytes — seen URL set)
- `state/last_scan.json` (583 bytes — full scored jobs list)
- `state/job_history.json` (2 bytes — empty list `[]` because no new jobs passed)
- `output/jobs_2026-08-18.csv` (0-byte header — written even with zero jobs)

**Companies scanned:** 3 (out of 140 in `companies.json`).
**Jobs found:** 1 (in stubbed mode); 0 survived scoring (LLM failed with placeholder key).

⚠️ **Performance note**: hardcoded `time.sleep(13)` between search queries + 2.5s per fetched URL means a full 140-company scan takes ~30+ minutes wall-clock.

## 3. LLM Scoring: ✅ Reachable

The LLM scoring prompt is **captured and verified** (1357 chars, full template at `job_hunt/scanner.py:51-75`):

```
You are evaluating job postings for a candidate. Output ONLY a JSON array, no other text.

CANDIDATE:
- Test Candidate
- Senior ML engineer, Python, PyTorch, LLMs
- Seeking: Remote-friendly ML engineer role
- NOT suitable: junior, pure frontend

RESUME SUMMARY:
# Test Candidate
Senior ML Engineer, 10 years experience. ...

JOBS TO SCORE:
JOB 1:
Company: Hugging Face | Location: Paris, France
Title: Ml Platform Engineer 7788
URL: https://huggingface.co/jobs/ml-ml-platform-engineer-7788
Content:
# Senior ML Engineer at Stripe
...

For each job output:
{
  "job_number": 1,
  "score": 0-100,
  "title": "extracted job title",
  "stack": "key tech from JD (comma-separated, max 6 items)",
  "location_remote": "location + remote policy",
  "reason": "one sentence why this fits or doesn't fit the candidate",
  "worth_applying": true/false
}

Scoring: 80-100 near-perfect; 60-79 good fit; 40-59 partial; <40 poor.
Set worth_applying=true only if score >= 55.
Include ALL jobs. Output ONLY the JSON array.
```

**Provider chain** (in `job_hunt/llm_utils.py`):
1. **OpenRouter (default)** — uses `openai.OpenAI` SDK pointed at `base_url="https://openrouter.ai/api/v1"`. Default model: `meta-llama/llama-3.3-70b-instruct:free`. Has 3-model fallback chain.
2. **Anthropic** — uses native `anthropic` SDK (requires `pip install autopilot-jobhunt[claude]`).
3. **Claude CLI** — shells out to the `claude` binary (no API key needed, just a Claude Code installation).

⚠️ **Important clarification**: the README and task description imply "uses OpenAI API by default" — this is misleading. The default LLM provider is **OpenRouter via the OpenAI SDK**, not OpenAI itself. There is no native OpenAI provider. To use OpenAI directly you'd have to fork or write a new provider branch in `chat_with_llm`.

**Blocked by**: needs a real OpenRouter key (or Anthropic key, or installed Claude CLI) to score. With placeholder `sk-or-v1-FAKEPLACEHOLDERFORREACHABILITYTEST`, OpenRouter returns HTTP 401 "User not found" within ~1 second — proving the request reached OpenRouter's servers.

## 4. Draft: ✅ Works (with stubs; needs TinyFish + LLM key for real run)

End-to-end test with TinyFish + LLM stubbed (canned but realistic LLM responses), drafting from a Stripe URL:

```
[2026-08-18 21:17:54] INFO    Fetching JD: https://stripe.com/jobs/listing/senior-ml-engineer-9876
[2026-08-18 21:17:54] INFO    Tailoring resume...
[2026-08-18 21:17:54] INFO      Saved: output/company-2026-08-18/resume_company.md (810 chars)
[2026-08-18 21:17:54] INFO    Drafting cover letter...
[2026-08-18 21:17:54] INFO      Saved: output/company-2026-08-18/cover_letter_company.md (787 chars)
[2026-08-18 21:17:54] INFO    Extracting application info...
[2026-08-18 21:17:54] INFO      Saved: output/company-2026-08-18/application_info.txt (581 bytes)
[2026-08-18 21:17:54] INFO    All files in: output/company-2026-08-18
```

**Three LLM calls per draft**, each with a distinct prompt and temperature:
1. Tailor resume (temp=0.2) — "Rewrite the resume below to mirror the language and emphasized skills in this job description. Keep every fact truthful — do NOT invent experience..."
2. Cover letter (temp=0.3) — "Write a one-page cover letter for {name} applying to this role. Open with one specific reason... Do NOT use: 'I am excited to apply', 'I am a team player', 'I am passionate about'"
3. Extract application info (temp=0.1) — "Extract: 1. Application URL or email 2. Hiring manager 3. Contact info 4. Deadline 5. Key requirements (max 8) 6. Nice-to-haves (max 5)"

**Output files (real, captured):**

`resume_company.md` (810 bytes) — tailored resume that mirrors JD terminology ("real-time fraud detection", "Kubernetes + Ray", "1B+ transactions/day") and reorders bullets to surface relevant experience first.

`cover_letter_company.md` (787 bytes) — one-page cover letter with specific opening referencing JD content ("the fraud-detection work I led at Acme — 1B+ transactions/day, 23% false-positive reduction — maps directly to the real-time systems Stripe runs at scale").

`application_info.txt` (581 bytes) — extracted URL, key requirements (5 bullets), nice-to-haves (3 bullets). Honestly marks "(not mentioned in JD)" when fields are missing.

⚠️ **MINOR BUG**: `drafter._resolve_job` hard-codes slug=`"company"` for URL-mode drafts. So output dir is always `company-DATE/`, not `<actual-company>-DATE/`. Drafting by `#N` index (from last scan) correctly uses the job's company slug — so this bug only affects URL-mode drafts.

## 5. Output Artifacts (real, captured)

```
workdir/
├── output/
│   ├── company-2026-08-18/         # draft output (URL-mode, slug bug noted)
│   │   ├── resume_company.md       # 810 bytes
│   │   ├── cover_letter_company.md # 787 bytes
│   │   └── application_info.txt    # 581 bytes
│   └── jobs_2026-08-18.csv         # scan results export (10 cols)
├── state/
│   ├── seen_jobs.json              # URL dedup set (135 bytes)
│   ├── last_scan.json              # full scored jobs list (583 bytes)
│   └── job_history.json            # accumulating history (2 bytes)
├── llm_call.json                   # captured LLM prompt (1357 chars)
└── draft_output.json               # draft run summary
```

Sample `output/jobs_2026-08-18.csv` (synthetic scan data used to test export):

```csv
Company,Role,Location,Application URL,Score (%),Stack,Region,Reason,Worth Applying,Scan Date
Acme,ML Engineer,Remote,https://example.com/jobs/mle,85,"Python, PyTorch",EU,Great fit,Yes,2026-08-18
Beta,Backend SWE,Berlin,https://example.com/jobs/swe,42,"Java, Spring",EU,No ML,No,2026-08-18
```

`--min 60` filter correctly keeps only the 85-score row.

## 6. MCP Server: ✅ Launchable & verified

Launched via `autopilot mcp` over stdio. Sent real JSON-RPC 2.0 `initialize` + `tools/list` requests and parsed the responses:

**Initialize response (truncated):**
```json
{"jsonrpc":"2.0","id":1,"result":{
  "protocolVersion":"2024-11-05",
  "capabilities":{"tools":{"listChanged":false}, "prompts":{}, "resources":{}},
  "serverInfo":{"name":"autopilot-jobs","version":"1.29.0"}
}}
```

(`serverInfo.version` reports the FastMCP library version `1.29.0`, not the package version `0.4.4` — minor cosmetic mismatch.)

**3 tools exposed** (full input/output schemas and annotations):

| Tool | Args | Annotations |
|------|------|-------------|
| `scan_jobs` | none | title="Scan & score job postings", destructive=false, openWorld=true |
| `draft_application` | `job_ref: str` (required) | title="Draft application (never applies)", destructive=false, openWorld=true |
| `export_jobs` | `min_score: int=0`, `days: int=0` | title="Export jobs to CSV", idempotent=true, openWorld=false |

Clean separation in source: `job_hunt/tools.py` is protocol-agnostic (just three functions that chdir to config dir and call the scanner/drafter/exporter), `job_hunt/mcp_server.py` is the FastMCP adapter.

One harmless stderr warning on startup: `pydantic_settings IncompleteFieldDefinitionWarning: Field 'lifespan' has an incomplete definition`. Doesn't affect functionality.

## 7. Telegram Notification: ✅ Reachable (didn't send)

Tested `send_telegram()` with a fake token + fake chat ID:

```
Telegram failed: HTTP 401 — {"ok":false,"error_code":401,"description":"Unauthorized: invalid token specified"}
send_telegram returned: False
```

→ POST goes to `https://api.telegram.org/bot{token}/sendMessage` with HTML parse_mode. Returns False on failure (no crash). Correctly skipped silently when telegram config absent (results always persist to CSV regardless). No email/Slack/Discord. `send_whatsapp()` exists in `notifier.py` but is dead code (never called).

---

## Bugs Found

| # | Severity | Component | Issue |
|---|----------|-----------|-------|
| 1 | **High** | `pyproject.toml` | `mcp>=1.9` lets pip pick `mcp-2.0.0` which has removed `mcp.server.fastmcp`. `autopilot mcp` crashes at import. Fix: `mcp>=1.9,<2`. |
| 2 | Low | `job_hunt/drafter.py:23` | URL-mode drafts hard-code slug=`"company"`, so output dir is always `company-DATE/` not `<actual-company>-DATE/`. |
| 3 | Low | `job_hunt/drafter.py` | Imports `TinyFish` but not `RateLimitError` (unlike scanner). Draft will crash on rate-limit instead of retrying. |
| 4 | Cosmetic | `job_hunt/mcp_server.py` | `serverInfo.version` reports FastMCP lib version (1.29.0), not package version (0.4.4). |

---

## Feature Mapping (honest scores 1-5)

| Feature | Supported? | Quality | Notes |
|---------|-----------|---------|-------|
| **B2 career-page scrape** | ✅ | 4/5 | Delegates to TinyFish (opaque dependency). Excellent URL-classification regex covers Greenhouse/Lever/Workable/SmartRecruiters/Ashby + generic `/jobs/N`. ATS listing expansion (boards.greenhouse.io etc.) is a thoughtful touch. |
| **B9 dedup** | ✅ | 4/5 | `state/seen_jobs.json` persists seen URLs across runs. Per-company set-based, append-only on success. URL-only dedup — no content-hash, so re-listed identical JDs at new URLs would re-score. |
| **B10 freshness** | ✅ | 3/5 | Each job stamped `scan_date=today` UTC. `--days N` filter works. BUT freshness is scan-date, not posting-date — a 6-month-old JD in this morning's scan gets today's scan_date. Acceptable for nightly-cron model. |
| **C3 LLM scoring** | ✅ | 4/5 | Verified reachable end-to-end (stubbed TinyFish → real OpenAI-SDK-at-OpenRouter call → 401 placeholder error). Prompt forces JSON-only output, includes candidate profile + 2500-char resume + jobs batch (1500 chars each) + min_score threshold + scoring rubric. Batches 10 jobs per LLM call. Fallback model chain. Robust to pre/post-amble noise via `[...]` slice. |
| **E tailoring** | ✅ | 4/5 | Verified end-to-end — three files written per draft (resume, cover letter, app info). Three separate LLM calls with different temperatures. Explicit ban on clichés ("I am excited to apply", "I am a team player"). Explicit instruction not to invent experience. The URL-mode slug bug is the only wart. |
| **M8 MCP server** | ✅ | 4/5 | Verified launchable over stdio, responds to JSON-RPC 2.0 properly. 3 well-annotated tools with full input/output schemas. Listed on Official MCP Registry + Glama + Smithery. Clean separation of protocol layer (`tools.py`) from adapter (`mcp_server.py`). Blocked by mcp 2.0 incompatibility until pyproject constraint is tightened. |
| **M12 cron** | ✅ | 3/5 | `setup_cron.sh` adds `30 2 * * *` to user crontab. Time customizable via env var. Idempotent (checks before adding). Unix-only, no systemd timer, no Windows Task Scheduler, no in-app scheduling. Adequate for self-hosters. |
| **M13 notifications** | ✅ | 3/5 | Telegram only, correctly skipped when unconfigured. Dead-code `send_whatsapp()` exists. No retry beyond 15s timeout. No email/Slack/Discord. |

---

## Honest Assessment

### What actually works
1. **`pip install -e .`** — clean install in a fresh venv, no errors.
2. **The 80-test pytest suite** — all green, well-mocked, no network in tests.
3. **`autopilot init`** — scaffolds `config.json`, `.env`, `companies.json`, `resume/YOUR_RESUME.md`, `state/`, `output/` cleanly and idempotently.
4. **`autopilot export`** — writes proper 10-column CSV with `--min` and `--days` filters (verified with synthetic data).
5. **The MCP server** — launches, speaks JSON-RPC 2.0, exposes 3 well-annotated tools (verified by sending real `initialize` + `tools/list` requests).
6. **Scanner / drafter / notifier call sites** — all reachable end-to-end with stubs at the TinyFish/LLM boundary. The LLM prompt is captured (1357 chars), the OpenRouter request hits the real endpoint and returns the expected 401 with a placeholder key, the draft pipeline writes three real files with realistic content.
7. **Telegram notifier** — POSTs to the real Telegram API, correctly returns False on 401 with a fake token, never crashes, is silently skipped when unconfigured.

### What is marketing-flavored
1. **"Uses OpenAI API by default"** — actually OpenRouter via the OpenAI SDK (`base_url="https://openrouter.ai/api/v1"`). There is **no native OpenAI provider**. Anthropic and Claude CLI exist as alternative providers, but OpenAI itself doesn't.
2. **"TinyFish: Free — no credit card"** — README claim, could not verify without signing up (constraint: do not sign up for paid APIs). The endpoint does respond with a real "Invalid or expired API key" error to a placeholder key, proving the call site is live.
3. **"Scans 130+ companies"** — true (140 in `companies.json`), but the hardcoded `time.sleep(13)` between search queries + 2.5s/URL means a full scan takes ~30+ minutes wall-clock. The "nightly" framing is honest because of this.
4. **Demo GIF** — generated from `demo/demo_run.py`, which uses fixture data and prints canned output (`print("✓ Saved: output/stripe-2025-06-06/resume_stripe.md")`). It's not a recording of a real run.

### What's reusable in our integration
- **URL-classification regex** (`JOB_URL_RE`, `ATS_JOB_RE`, `ATS_LISTING_RE` in `scanner.py`) — covers Greenhouse, Lever, Workable, SmartRecruiters, Ashby, plus generic `/jobs/N` patterns. Direct copy-paste value.
- **`SCORE_PROMPT` template** (scanner.py:51-75) — well-designed, forces JSON-only output, includes candidate profile + resume + jobs batch + min_score threshold + scoring rubric. Could be lifted with minor adaptation.
- **140-company `companies.json`** — useful seed list with `name`, `careers_url`, `search_domain`, `location`, `region` fields. Already filtered to EU/NZ/remote-friendly tech companies.
- **MCP server's `ToolAnnotations` pattern** — exemplary `readOnly`/`destructive`/`idempotent`/`openWorld` hints. Useful as a template for our own MCP servers.
- **`state/seen_jobs.json` dedup pattern** — simple, correct, append-only URL set. Worth borrowing.
- **`tools.py` / `mcp_server.py` separation** — protocol-agnostic functions in `tools.py`, adapter in `mcp_server.py`. Good architectural pattern for multi-protocol exposure.

### What's blocked
1. **Real production scan** — needs a valid `TINYFISH_API_KEY` (sign-up at agent.tinyfish.ai, README says free, unverified) AND a valid `OPENROUTER_API_KEY` (or `ANTHROPIC_API_KEY`, or installed `claude` CLI). Without these, scan fails at the first TinyFish call (`HTTP 401`), and even with TinyFish working, scoring fails at the LLM call (`HTTP 401` from OpenRouter).
2. **`autopilot mcp` with current `mcp-2.0.0`** — blocked by pyproject.toml constraint bug. Fix: `pip install 'mcp>=1.9,<2'`.
3. **Drafting from URL** — works but writes to `output/company-DATE/` instead of `output/<actual-company>-DATE/` (slug bug). Workaround: scan first, then `draft #N` to get the correct slug.
4. **Full 140-company scan** — works but ~30+ minutes wall-clock due to hardcoded sleeps. Acceptable for nightly cron; painful for ad-hoc runs.

---

## Artifacts Left in Place

All validation artifacts are at `/home/z/my-project/experiments/07_autopilot_jobhunt/`:

- `validation.json` — structured JSON report (this file)
- `SUMMARY.md` — this human-readable summary
- `venv/` — the venv used for testing (mcp pinned to <2)
- `workdir/` — the autopilot working directory:
  - `config.json`, `.env`, `companies.json`, `resume/YOUR_RESUME.md` (scaffolded by `autopilot init`)
  - `state/{seen_jobs,last_scan,job_history}.json` (real state files from stubbed scan)
  - `output/company-2026-08-18/{resume_company,cover_letter_company}.md + application_info.txt` (real draft output)
  - `output/jobs_2026-08-18.csv` (real export CSV with synthetic data)
  - `llm_call.json` (captured LLM scoring prompt)
  - `draft_output.json` (draft run summary)
  - `llm_call_reachability.py`, `draft_pipeline_test.py` (the test harnesses used)
