# career-ops Key Code Files — Deep Analysis

Repo: `/home/z/my-project/repos/career-ops/` (cloned `--depth 1`, no `.git`).
Analysis covers 21 `.mjs` files + `interview-prep/` + 5 mode files.

**Headline finding**: career-ops is **far more portable than its README implies**.
Roughly 19 of 21 `.mjs` files are pure Node CLI tools that consume
markdown/TSV/YAML and emit JSON/HTML/markdown — **zero LLM calls, zero Claude
Code dependency**. The Claude Code "agent" layer is *only* the markdown files in
`modes/*.md` and `.claude/commands/`. career-ops is, in effect, a collection of
deterministic Node utilities + a stack of LLM prompts. The two halves are
cleanly separable.

---

## tracker.mjs

**Purpose**: Derived SQLite index over `data/applications.md` (the source of truth). Sync/query/history/export/delete via 5 subcommands.
**LOC**: 569
**Inputs**: `data/applications.md` (markdown table, 9-col: `# | Date | Company | Role | Score | Status | PDF | Report | Notes`); `templates/states.yml` (canonical status aliases).
**Outputs**: `data/applications.db` (SQLite, `applications` + `status_events` + `meta` tables); JSON or markdown table to stdout on query.
**LLM calls**: None.
**External deps**: `node:sqlite` (built into Node >= 22.5), `js-yaml`. Helpers: `./tracker-parse.mjs`, `./tracker-utils.mjs`.
**Standalone runnable?**: ✅ Yes
**Adaptation effort**: Low — drop in, set `CAREER_OPS_TRACKER` env var, requires Node 22.5+.
**Key insight**: Markdown is the source of truth; SQLite is a *derived* index that can be deleted and rebuilt any time. Has nice integrity diagnostics: detects mojibake (`鈥?` from GBK round-trip), score-in-status column drift, stray pipes folded into notes, unknown statuses normalized to `Evaluated` with original preserved in notes. `ensureFresh()` resyncs if markdown hash changes. Cross-process lock via `tracker-utils.mjs`. This is the cleanest "markdown as DB" pattern I've seen.
**Code excerpt**:
```javascript
function syncIndex(db, states) {
  const { apps, diag } = parseTracker(states);
  db.exec('BEGIN');
  db.exec('PRAGMA defer_foreign_keys = ON');
  try {
    db.exec('DELETE FROM applications');
    const insertApp = db.prepare('INSERT INTO applications (...) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)');
    for (const a of apps) insertApp.run(a.id, a.pos, a.date, a.company, a.role, a.score, a.status, a.pdf, a.report, a.notes);
    // status_events: only insert when status changed since last sync
    db.prepare('INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value')
      .run('md_sha256', mdHash());
    db.exec('COMMIT');
  } catch (err) { db.exec('ROLLBACK'); throw err; }
}
```

---

## scan.mjs

**Purpose**: Zero-token portal scanner. Loads providers from `providers/*.mjs`, fetches job postings from each company/board in `portals.yml`, applies title/location/salary/content/visa/country filters, dedups against `data/scan-history.tsv`, writes new offers to `data/pipeline.md`.
**LOC**: 3039 (large — top 200 lines + main() + summary printer reviewed)
**Inputs**: `portals.yml` (tracked_companies + job_boards + filters), `config/profile.yml`, `data/scan-history.tsv` (dedup), `data/blacklist.md` (opt-out), `data/pipeline.md` (output append), `data/scan-runs.tsv`.
**Outputs**: Appends rows to `data/pipeline.md` + `data/scan-history.tsv`; prints summary table; writes portal-health records.
**LLM calls**: None ("Zero Claude API tokens — pure HTTP + JSON").
**External deps**: `js-yaml`, `dotenv` (optional), the `providers/` directory (~95 plugins for Greenhouse/Lever/Ashby/Workday/iCIMS/SmartRecruiters/etc.), `playwright` (only when `--verify` flag is passed — to liveness-check URLs), `./pipeline-lock.mjs` (cross-process lock), `./fingerprint-core.mjs` (cross-listing detection).
**Standalone runnable?**: ✅ Yes (without `--verify`; with `--verify` needs Playwright installed)
**Adaptation effort**: Medium — file is huge and couples to ~10 sibling modules. Most useful as a reference for filter composition (title AND-groups, location word-boundary matching with `(?<![a-z0-9])` lookarounds, remote-title detection that won't match "remote sensing", `max_posting_age_days`, `recheckAfterDays` for stale dedup replay).
**Key insight**: Title-keyword matching is genuinely sophisticated — 2-3 letter acronyms anchored on word boundaries (so "SDR" doesn't match inside "sdram"), multi-word AND-groups (`director + engineering`), lowercase-as-keyword normalization. The dedup system has 3 tiers: exact-URL, company+role (with fuzzy title match via `roleFuzzyMatch`), and JD-text fingerprint (`fingerprint-core.mjs`). SIGINT/Ctrl-C handler records a failure snapshot so a multi-hour scan is auditable.
**Code excerpt** (the location filter that won't false-positive on "Indiana" when blocking "India"):
```javascript
function compileLocationKeyword(keyword) {
  const escaped = keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const startsWord = /[a-z0-9]/.test(keyword[0]);
  const endsWord   = /[a-z0-9]/.test(keyword[keyword.length - 1]);
  const prefix = startsWord ? '(?<![a-z0-9])' : '';
  const suffix = endsWord   ? '(?![a-z0-9])'   : '';
  return (lower) => new RegExp(`${prefix}${escaped}${suffix}`).test(lower);
}
// REMOTE_TITLE_RE blocks "remote sensing" but allows "Remote in MO":
export const REMOTE_TITLE_RE = /(?<![a-z])remote(?=$|\s*[^a-z\s]|\s+in\b)/;
export const REMOTE_NEGATED_RE = /\b(?:non|not|no)[^a-z]*remote/;
```

---

## scan-ats-full.mjs

**Purpose**: Reverse ATS discovery scanner. Walks public company directories at Greenhouse/Lever/Ashby/Workday/iCIMS (sourced from `github.com/Feashliaa/job-board-aggregator` dataset, cached 24h in `data/cache/ats-companies/`), surfaces fresh postings matching your portals.yml filters — no manual company curation.
**LOC**: 1077
**Inputs**: `portals.yml` (reuses `title_filter`, `location_filter`, `content_filter`), the upstream dataset (cached JSON per ATS), `data/scan-history.tsv` (dedup), `data/blacklist.md`.
**Outputs**: Appends to `data/pipeline.md` + `data/scan-history.tsv`; optional `--md-out <dir>` writes dated markdown digest; `--json` mode prints structured JSON to stdout.
**LLM calls**: None ("Zero LLM tokens — pure HTTP + JSON").
**External deps**: `js-yaml`, `./providers/{greenhouse,lever,ashby,workday,icims}.mjs`, `./scan.mjs` (imports `buildTitleFilter`, `buildLocationFilter`, `loadSeenUrls`, etc.), `./seeds/vc-portfolios.mjs`.
**Standalone runnable?**: ✅ Yes
**Adaptation effort**: Low — much more self-contained than scan.mjs. SSRF guard (`entryOnHost`) validates each constructed URL really resolves to the canonical ATS host before fetch; `SLUG_RE` whitelist on dataset entries. Has crash-recovery checkpointing (`--resume` from `data/cache/ats-full-checkpoint.json`, every 500 companies) — useful pattern to lift for any long-running scraper.
**Key insight**: Per-source concurrency tuning — Greenhouse/Lever/Ashby serve their entire directory off ONE hostname, so the default 20-connection fan-out earns HTTP throttles; `SINGLE_HOST_CONCURRENCY = 6` for those, default 20 for Workday (where each tenant is a separate host). Stops after `RESOLVER_FAILURE_LIMIT = 50` consecutive resolver-level failures to avoid burning a multi-hour sweep on a dead DNS. Dataset fingerprint (SHA1 of company list) lets `--resume` detect upstream drift and refuse rather than resume at the wrong offset.
**Code excerpt**:
```javascript
export const SOURCES = {
  greenhouse: {
    provider: greenhouse,
    concurrency: SINGLE_HOST_CONCURRENCY,  // 6 — single hostname, throttle
    dataset: `${DATASET_BASE}/greenhouse_companies.json`,
    toEntry: (slug) => SLUG_RE.test(String(slug))
      ? entryOnHost(String(slug), `https://job-boards.greenhouse.io/${slug}`,
                    h => h === 'job-boards.greenhouse.io')
      : null,
  },
  workday: {
    provider: workday,
    concurrency: CONCURRENCY,  // 20 — each tenant is a separate host
    dataset: `${DATASET_BASE}/workday_companies.json`,
    ...
  },
};
```

---

## salary-gap.mjs

**Purpose**: Desired vs advertised vs actual salary analyzer. Folds observations from 3 sources (reports' Machine Summary `advertised_comp`, `data/salary-observations.tsv`, `config/profile.yml` `compensation.target_range`) using trust tiers, computes per-application gap percentages, currency-bounded aggregates.
**LOC**: 695
**Inputs**: `data/salary-observations.tsv` (9-col: `num | date | type | amount | currency | source | note | round | interviewer`), `reports/*.md` (Machine Summary YAML fence), `config/profile.yml` (`compensation.target_range`).
**Outputs**: JSON to stdout (`{applications, aggregates: {byCurrency, byCompanyRole}, quality: {orphans, unparseable, invalidSources, currencyMismatches, withoutActual, latestObservation}}`); `--summary` mode prints human table; `--stated-for <num>` returns prior verbal comp commitments for an app.
**LLM calls**: None.
**External deps**: `js-yaml`.
**Standalone runnable?**: ✅ Yes
**Adaptation effort**: Low — single file, ~695 lines, pure functional fold. Self-test fixture included.
**Key insight**: Trust tiering is the elegant bit: `actual: contract(3) > offer-letter(2) > recruiter-verbal(1) > user(0)`; `advertised: user(2) > recruiter-verbal(1) > jd(0)`; `desired: user(1) > profile(0)`. Gap math requires proven-shared-currency (strict equality AND neither side UNKNOWN — two UNKNOWNs are never comparable, even with themselves). Has a separate `stated` type that *never folds* — it's the candidate's verbal commitments per round+interviewer, used to prevent contradiction in later rounds. `parseAmount()` handles `$123,684—$254,644 USD` (em dash, both bounds symbol-prefixed, trailing ISO token).
**Code excerpt**:
```javascript
const TRUST = {
  actual:     { contract: 3, 'offer-letter': 2, 'recruiter-verbal': 1, user: 0 },
  desired:    { user: 1, profile: 0 },
  advertised: { user: 2, 'recruiter-verbal': 1, jd: 0 },
};
function pickEffective(type, candidates) {
  const tiers = TRUST[type];
  const usable = candidates.filter(o => o.type === type && o.parsed !== null
    && Object.hasOwn(tiers, o.source));  // hasOwn not `in` — blocks toString() poisoning
  usable.sort((a, b) => (tiers[b.source] - tiers[a.source]) || (a.date < b.date ? 1 : -1));
  const top = usable[0];
  return { value: top.parsed.mid, source: top.source, date: top.date, currency: top.currency, raw: top.amount };
}
```

---

## jd-skill-gap.mjs

**Purpose**: Zero-LLM JD skill-gap checker. Regex-extracts skills from JD requirement sections, classifies each against cv.md into `existing` / `supportedByResume` / `gap` buckets.
**LOC**: 812
**Inputs**: A JD markdown file (`node jd-skill-gap.mjs jds/acme.md`), `cv.md`.
**Outputs**: JSON to stdout (`{existing, supportedByResume, gap, jdSkills, diagnosis}`); `--summary` prints table; `--self-test` runs fixtures.
**LLM calls**: None (explicit: "regex-based, no LLM call — see extractJdSkills()").
**External deps**: `./skill-extract.mjs` (canonicalize + extractSkills — the shared tokenizer).
**Standalone runnable?**: ✅ Yes
**Adaptation effort**: Low. Honest about its limits: it deliberately under-extracts because over-extraction creates phantom gaps. Has a `diagnoseExtraction()` function that distinguishes "empty JD" / "no requirements section recognized" / "no skill candidates matched" — so zero output is never confused with "no gaps found".
**Key insight**: JD requirement sections vary wildly in phrasing — the regex list covers "What we're looking for", "Who you are", "You may be a good fit if", "You have", "It's important to us that you have", "Ideal candidate", etc. Plus a NON_REQUIREMENT_HEADER_RE that closes the block on "Benefits", "Perks", "Compensation", "About us" — without it the benefits list ("401k", "Equity", "Carrot") would get misreported as skill gaps. Canonicalization via skill-extract.mjs closes alias gaps (CV "k8s" + JD "Kubernetes" → same canonical name).
**Code excerpt**:
```javascript
const REQUIREMENT_HEADER_RE = new RegExp('^#{0,6}\\s*(?:' + [
  'required', 'qualifications', 'must[- ]have', 'nice[- ]to[- ]have',
  "what\\s+we(?:'|’)?\\s*re\\s+looking\\s+for",
  'who\\s+you\\s+are', 'about\\s+you',
  'you\\s+(?:may|might|could)\\s+be\\s+a\\s+good\\s+fit',
  "you(?:(?:'|’)ll|\\s+will)?\\s+have",
  'ideal\\s+candidate', 'skills\\s+(?:and|&)\\s+experience',
].join('|') + ')s?\\b.*$', 'im');

const SKILL_TOKEN_RE = /\b([A-Z][A-Za-z0-9+.#]{0,29}[A-Za-z0-9+#](?:\.[a-z]{2,4})?)(?!\w)/g;
// STOPWORDS swallows "Bachelor's degree required", "Strong communication", "Deep fluency"
```

---

## jd-similarity.mjs

**Purpose**: Deterministic CV reuse recommendation for similar JDs. Jaccard token similarity + seniority level hard-mismatch detection → `reuse` / `reuse-with-edits` / `regenerate`.
**LOC**: 178
**Inputs**: Two file paths (`node jd-similarity.mjs new-jd.txt previous-jd-or-cv.txt`).
**Outputs**: JSON to stdout: `{decision, score, reason}`.
**LLM calls**: None.
**External deps**: None (pure Node, no npm).
**Standalone runnable?**: ✅ Yes — the easiest lift in the repo, single 178-line file, zero deps.
**Adaptation effort**: Low. The seniority level table is hardcoded for English + Chinese (中文), so non-CJK/non-English markets would need extension.
**Key insight**: Tiny file, surprisingly well-engineered. The `NON_LEVEL_FOLLOWERS` map handles the classic false-positive trap: "Principal Engineer" = seniority (engineer follows principal), but "Principal responsibilities" = NOT seniority (responsibilities is a follower word). Same for "Lead" (verb when followed by "to/the/a/our", noun-level when followed by "Engineer") and "mid-market" (customer segment, not seniority). Multiple levels in one text → ambiguous → returns -1 → lets the similarity score decide.
**Code excerpt**:
```javascript
export function jaccardSimilarity(left, right) {
  const a = left instanceof Set ? left : tokenize(left);
  const b = right instanceof Set ? right : tokenize(right);
  if (!a.size && !b.size) return 1;
  if (!a.size || !b.size) return 0;
  let intersection = 0;
  for (const token of a) if (b.has(token)) intersection++;
  return intersection / (a.size + b.size - intersection);
}
const NON_LEVEL_FOLLOWERS = {
  principal: ['responsibilities', 'duties', 'accountabilities', 'objectives', 'purpose', 'tasks'],
  lead:      ['to', 'the', 'a', 'an', 'our', 'and', 'or', 'by', 'on', 'in', 'for', 'with', 'from', 'mentoring', 'projects'],
  mid:       ['market', 'size', 'sized', 'cap', 'tier', 'funnel', 'term', 'sized-company'],
};
```

---

## followup-cadence.mjs

**Purpose**: Computes follow-up cadence for active applications — derives next follow-up date, urgency tier (waiting/due/overdue/cold/retired), extracts contacts from notes, handles user overrides (`!next YYYY-MM-DD`) and retirement (`!retired`).
**LOC**: 942
**Inputs**: `data/applications.md`, `data/follow-ups.md`, `config/profile.yml` (followup_cadence overrides), `templates/states.yml` (status aliases, multilingual).
**Outputs**: JSON to stdout: `{entries: [{num, company, role, status, appliedDate, appDateSource, daysSinceApp, followupCount, lastFollowupDate, nextFollowupDate, daysUntilNext, urgency, contacts, reportPath}], summary}`; `--summary` human dashboard; `--overdue-only` filter.
**LLM calls**: None.
**External deps**: `js-yaml`, `./tracker-utils.mjs`, `./tracker-parse.mjs`.
**Standalone runnable?**: ✅ Yes
**Adaptation effort**: Low. Default cadence: applied_first=7d, applied_subsequent=7d, applied_max_followups=2 (then 'cold'), responded_initial=1d, responded_subsequent=3d, interview_thankyou=1d. All overridable from profile.
**Key insight**: Status alias map is *derived* from `templates/states.yml`, not hardcoded — so Turkish `Mülakat`, Spanish `Entrevista`, etc. all flow through. Uses `foldStatusInput` (NFKC + case fold) because JS lowercases Turkish dotted-İ to `i + U+0307` and the mark survives a bare `toLowerCase()`. The "Applied YYYY-MM-DD" date is extracted from notes column (preferred over the eval-date column) with calendar validation, cross-reference exclusion (drops dates cited for sibling requisitions like "#154 is already live (applied 2026-08-04)"). `~YYYY-MM-DD` accepts estimated dates. `!next` overrides + `!retired` directives parsed from follow-ups.md.
**Code excerpt**:
```javascript
export function computeNextFollowupDate(status, appDate, lastFollowupDate, followupCount) {
  if (status === 'applied') {
    if (followupCount >= CADENCE.applied_max_followups) return null; // cold
    if (followupCount === 0)  return addDays(parseDate(appDate), CADENCE.applied_first);
    if (lastFollowupDate)     return addDays(parseDate(lastFollowupDate), CADENCE.applied_subsequent);
    return addDays(parseDate(appDate), CADENCE.applied_first);
  }
  if (status === 'responded') {
    if (lastFollowupDate) return addDays(parseDate(lastFollowupDate), CADENCE.responded_subsequent);
    return addDays(parseDate(appDate), CADENCE.responded_initial);
  }
  if (status === 'interview') {
    if (lastFollowupDate) return addDays(parseDate(lastFollowupDate), CADENCE.responded_subsequent);
    return addDays(parseDate(appDate), CADENCE.interview_thankyou);
  }
  return null;
}
```

---

## analyze-patterns.mjs

**Purpose**: Rejection pattern detector. Parses tracker + linked reports, extracts dimensions (archetype, seniority, remote, gaps, scores, via-channel), classifies outcomes, computes funnel + score-by-outcome + archetype/remote/company-size breakdowns + blocker/discard-reason/tech-stack-gap analyses + via-channel yield (agency vs direct advance rates).
**LOC**: 1413
**Inputs**: `data/applications.md`, `reports/*.md` (each report has a YAML `## Machine Summary` fence with company/role/score/archetype/legitimacy_tier/final_decision/hard_stops/soft_gaps/etc.).
**Outputs**: JSON to stdout (large structured object: funnel, scoreComparison, archetypeBreakdown, blockerAnalysis, discardReasonStats, techStackGaps, viaChannelAnalysis, recommendations); `--summary` human table.
**LLM calls**: None.
**External deps**: `js-yaml`, `./tracker-parse.mjs`.
**Standalone runnable?**: ✅ Yes
**Adaptation effort**: Low-Medium. Depends on the Machine Summary YAML schema in reports — which is *produced* by the LLM-driven eval modes. Without those reports, much of the analysis degrades to "no report data". The funnel + outcome + score stats still work off the tracker alone.
**Key insight**: Has a `--min-vendor-n` floor (default 8) before via-channel yield recommendations fire — explicitly rejects single-bucket claims. Uses three different rate denominators (`discardableBase`, `gapBearingBase`, `enriched.length`) because using `enriched.length` for everything silently deflated derived percentages. ADVANCED_STATUSES (`responded`, `interview`, `offer`, `hired`) is strictly tighter than `outcome == 'positive'` — a bare `applied` doesn't count as advanced past screening. Agency-vs-direct comparison uses `normalizeVia` (NFKC + Unicode letters) so "Hays" / "HAYS " / full-width "ＨＡＹＳ" cluster but リクルートAgent vs パーソルAgent stay distinct.
**Code excerpt**:
```javascript
function classifyOutcome(status) {
  const s = normalizeStatus(status);
  if (['hired', 'interview', 'offer', 'responded', 'applied'].includes(s)) return 'positive';
  if (['rejected', 'discarded'].includes(s)) return 'negative';
  if (['skip'].includes(s)) return 'self_filtered';
  return 'pending'; // evaluated
}
const ADVANCED_STATUSES = new Set(['responded', 'interview', 'offer', 'hired']);
function buildViaChannelAnalysis(submitted, isAdvanced, minSample = MIN_VENDOR_N) {
  const isDirect = (v) => v === '—' || v === '-';
  const agencySubmitted  = submitted.filter(e => { const v = viaOf(e); return v !== '' && !isDirect(v); });
  const directSubmitted  = submitted.filter(e => isDirect(viaOf(e)));
  const rate = (arr) => arr.length > 0 ? Math.round((arr.filter(isAdvanced).length / arr.length) * 100) : 0;
  // ...
}
```

---

## detect-reposts.mjs

**Purpose**: Repost/ghost-job detector. Reads `data/scan-history.tsv`, groups by company, fuzzy-matches role titles, flags any company+role appearing 2+ times with different URLs within a configurable window (default 90 days).
**LOC**: 525
**Inputs**: `data/scan-history.tsv` (cols: url, first_seen, portal, title, company, status, location, + 12th col normalized_company).
**Outputs**: JSON to stdout: `{metadata: {windowDays, totalRows, clusters}, clusters: [{company, role, repostCount, firstSeen, lastSeen, daysSpan, appearances}]}`; `--summary` table.
**LLM calls**: None.
**External deps**: `./role-matcher.mjs` (`roleFuzzyMatch`, `roleTokens`, `BASELINE_TOKENS`), `./invite-match.mjs` (`normalizeCompanyName`), `./lib/cli-flags.mjs`.
**Standalone runnable?**: ✅ Yes
**Adaptation effort**: Low. Only considers rows with `status == 'added'` (ignores `skipped_expired`, `skipped_invalid_url`, `skipped_blocked_host`). Same-URL dedup is preserved.
**Key insight**: Genuinely impressive performance engineering. The naive O(N²) nested loop over `roleFuzzyMatch` (which re-tokenizes both titles on every call) was replaced with a 3-pass algorithm: (1) bucket rows by lowercased title in O(N), (2) tokenize each distinct title once, build an inverted index of *non-baseline* tokens (excludes "engineer", "platform" — they appear in most titles), (3) seed buckets in first-appearance order, only fuzzy-match against buckets sharing ≥1 discriminating token. The gate is a strict subset of `roleFuzzyMatch`'s necessary conditions (≥2 token overlap, Jaccard ≥0.6), so it never changes a verdict — only filters calls.
**Code excerpt**:
```javascript
function detectRepostsInGroup(rows, windowDays) {
  const titleGroups = groupRowsByTitle(rows);
  const results = [];
  for (const group of titleGroups) {
    if (group.length < 2) continue;
    const sorted = [...group].sort((a, b) => (a.date < b.date ? -1 : 1));
    let cluster = [];
    for (const row of sorted) {
      if (cluster.length === 0) { cluster = [row]; continue; }
      const span = daysBetween(cluster[0].date, row.date);
      if (span <= windowDays) cluster.push(row);
      else {
        if (cluster.length >= 2) {
          const result = buildRepostCluster(cluster, windowDays);
          if (result) results.push(result);
        }
        // slide window: drop oldest until new row fits
        cluster = cluster.filter(c => daysBetween(c.date, row.date) <= windowDays);
        cluster.push(row);
      }
    }
    if (cluster.length >= 2) {
      const result = buildRepostCluster(cluster, windowDays);
      if (result) results.push(result);
    }
  }
  return results;
}
```

---

## rejection-latency.mjs

**Purpose**: Post-interview response-latency signal. Cross-references `data/active-interviews.md` (interview round dates) with tracker status; flags companies whose post-interview silence exceeds a courtesy threshold (default 30 days), suggests blacklist entries.
**LOC**: 604
**Inputs**: `data/active-interviews.md`, `data/applications.md`, `config/profile.yml` (`rejection_latency.courtesy_days`).
**Outputs**: JSON: `{flags: [{company, role, lastInterviewDate, daysSince, courtesyDays, blacklistSuggestion}], warnings, companiesChecked}`; `--summary` table. Includes a `DISCLAIMER` constant: "Elapsed-time observation only — not legal advice".
**LLM calls**: None.
**External deps**: `js-yaml`, `./process-quality.mjs` (`parseActiveInterviews`), `./tracker-parse.mjs`, `./role-matcher.mjs` (fuzzy join), `./lib/cli-flags.mjs`.
**Standalone runnable?**: ✅ Yes
**Adaptation effort**: Low. An earlier statutory tier was deliberately removed ("the underlying legal threshold could change and the script has no way to re-verify it"). Suggestion-only: never writes to `data/blacklist.md` — generates ready-to-paste rows.
**Key insight**: Role-aware tracker join — `parseTrackerInterviewRows` filters to `Interview` status, groups by company key; for each interview row, fuzzy-matches the role against the tracker's role column to avoid borrowing silence from an unrelated same-company application. Calendar-validated dates (rejects `2026-06-31`, `2026-13-45`). UTC-midnight day math to avoid the noon-UTC tipover bug. Has a screening-round handler (don't apply the post-interview timer to recruiter screens — they're a different process stage).
**Code excerpt**:
```javascript
export const DISCLAIMER =
  'Elapsed-time observation only — not legal advice or a claim that the ' +
  'employer did anything wrong.';

export function buildBlacklistSuggestion(company, todayStr, reason) {
  return `| ${company} | ${todayStr} | company | ${reason} |`;
}
// tracker holds current state — "still Interview" = deterministic proxy for
// "no employer response after the interview"
```

---

## merge-tracker.mjs

**Purpose**: Merges batch TSV tracker additions (from `batch/tracker-additions/`) into `data/applications.md`. 3-tier dedup (URL key, company+role fuzzy, report-number match), in-place score/status update if dupe with higher score, validates against `states.yml`, archives processed TSVs to `merged/`.
**LOC**: 1362
**Inputs**: `data/applications.md`, `batch/tracker-additions/*.tsv` (9-col or 8-col or pipe-delimited markdown rows), `batch/batch-state.tsv` (cross-check — drops rows whose JSON status said "failed"), `data/pdf-index.tsv`.
**Outputs**: Modified `data/applications.md`; archived TSVs in `batch/tracker-additions/merged/`; triggers `sync-pdf-flags.mjs` and optionally `verify-pipeline.mjs` after.
**LLM calls**: None.
**External deps**: `js-yaml` (none?), `./tracker-links.mjs`, `./role-matcher.mjs`, `./find.mjs` (PDF index), `./tracker-parse.mjs`, `./tracker-utils.mjs` (atomic write + lock), `./url-key.mjs`, `child_process.execFileSync` for `sync-pdf-flags.mjs` + `verify-pipeline.mjs`.
**Standalone runnable?**: ✅ Yes
**Adaptation effort**: Medium — top-level script, no main() function, runs at import. Tightly coupled to the batch-runner workflow (`batch/batch-runner.sh`, `batch-state.tsv`). Cross-checks against `batch-state.tsv`'s failed-status to reject fabricated placeholder scores (a worker can write a well-formed TSV even when its JSON result said "failed" — e.g. it fabricated a `0.0/5 "Suspicious"` score for a posting it never read).
**Key insight**: Three dedup tiers run in priority order: (1) URL key (canonicalized via `url-key.mjs`), (2) company+role fuzzy (via `roleFuzzyMatch`), (3) report number match. The `parseAppLine()` results feed `existingApps[]` *during the run*, so two TSVs for the same company+role in ONE run don't both append. Has a hard-abort case: if the tracker table separator row (`|---|------|...`) is missing, it aborts before writing anything — a damaged header used to silently swallow a whole batch of evaluations while reporting success. Optional `CAREER_OPS_MERGE_HOLD_MS` env var deliberately pauses the merge so a second concurrent merge can enter the critical section — a regression-test hook for the lock.
**Code excerpt** (the hard-abort):
```javascript
if (insertIdx < 0) {
  console.error(`❌ Aborting merge: ${basename(APPS_FILE)} has no table separator row ("|---|------|...").`);
  console.error(`   There is no insert point for ${newLines.length} new row(s), so NOTHING was written and no TSV was archived.`);
  console.error('   Repair the tracker table header, then re-run — the pending additions in');
  console.error(`   ${ADDITIONS_DIR} will merge then. Expected header:`);
  const { header, separator } = buildHeaderRows();
  console.error(`     ${header}`);
  console.error(`     ${separator}`);
  for (const line of newLines) console.error(`   not merged: ${line}`);
  trackerLock.release();
  process.exit(1);
}
```

---

## pipeline-lock.mjs

**Purpose**: Cross-process advisory lock for `data/pipeline.md` (and reusable for any file). Directory-mkdir-based atomic acquisition with owner.json stamp, stale-reclaim behind a second atomic guard, jittered backoff, fair-ish queue.
**LOC**: 510
**Inputs**: A file path to lock.
**Outputs**: A `{lockDir, release()}` handle; throws `LockTimeoutError` on timeout.
**LLM calls**: None.
**External deps**: Pure Node (`fs`, `path`, `crypto`).
**Standalone runnable?**: ✅ Yes — zero deps, single file, drop-in.
**Adaptation effort**: Low. Env vars (`CAREER_OPS_PIPELINE_LOCK_TIMEOUT_MS`, `RETRY_MS`, `STALE_MS`, `MAX_WAIT_MS`) tune timing.
**Key insight**: Battle-tested for Windows contention. mkdir's `EEXIST`/`EPERM`/`EACCES` are all treated as contention (not fatal), and rm's `EPERM`/`EACCES`/`EBUSY`/`ENOTEMPTY` similarly. Distinguishes "lock is being handed round" (fingerprint changing → re-arm deadline, keep waiting) from "lock is wedged" (single holder not letting go → timeout). Jittered backoff `[0.5x, 1.5x) retryMs` measured a 6x reduction in item loss under contention (coupon-collector problem). Two-stage stale reclaim: first take a `<path>.lock.recover` guard dir (atomic), then decide-and-delete the stale lock — without serialization two reclaimers could delete each other's freshly-created lock.
**Code excerpt**:
```javascript
export function acquirePipelineLock(pipelinePath, options = {}) {
  // ...
  for (;;) {
    if (Date.now() > hardDeadline) throw buildTimeoutError(maxWaitMs);
    try {
      mkdirSync(lockDir);
    } catch (err) {
      if (!isMkdirContention(err)) throw err;  // EEXIST, EPERM, EACCES = contention
      // ... take recover guard, decide reclaim, etc.
      if (holderStillWedged()) throw buildTimeoutError();
      await sleep(backoffMs());
      continue;
    }
    writeFileSync(join(lockDir, 'owner.json'), JSON.stringify({
      pid: process.pid, token, started_at: new Date().toISOString(), pipeline: pipelinePath,
    }, null, 2));
    return { lockDir, release() { /* verify ownership, then rmSync */ } };
  }
}
```

---

## generate-pdf.mjs

**Purpose**: HTML → PDF via Playwright Chromium. Single-CV and `--batch=<manifest.json>` modes. Normalizes Unicode for ATS safety (em-dash→hyphen, smart quotes→straight, zero-width→removed, €→"EUR ", etc.), enforces page budget, validates CV section order, inlines local fonts.
**LOC**: 1086
**Inputs**: `<input.html>` (or batch manifest of `{input, output, format?, reportNum?}`), `config/profile.yml` `style:` tokens.
**Outputs**: `<output.pdf>` (binary), `data/pdf-index.tsv` row linking PDF to report number, `*.results.json` for batch runs.
**LLM calls**: None.
**External deps**: **`playwright` (Chromium)** — required. `./theme-style.mjs` for CSS custom properties.
**Standalone runnable?**: ✅ Yes — but requires Playwright + Chromium installed (heavy install).
**Adaptation effort**: Low — Playwright is a standard dependency. The Unicode normalization for ATS is genuinely valuable (preserves only body text, masks `<style>`/`<script>` blocks before sanitization). Section-order validator (`validateCvSectionOrder`) covers English + Polish (with diacritic folding for `ł`).
**Key insight**: Security-conscious renderer: `javaScriptEnabled: false` (CV is static markup), `page.route('**/*', ...)` aborts any non-`file:`/`data:` URL (prevents an injected `<img src="https://...">` from beaconing out). Atomic temp HTML file (`randomUUID`-named). Page-count verifier reads the PDF's root page-tree count (not stream text — page-like text in content streams would false-positive). Batch mode shares one browser across all documents via `renderInPage(browser, html, ...)` — a single failing document is isolated, the rest still render.
**Code excerpt**:
```javascript
context = browser.newContext
  ? await browser.newContext({ javaScriptEnabled: false })  // CV is static
  : null;
page = context ? await context.newPage() : await browser.newPage();
if (page.route) {
  await page.route('**/*', (route) => {
    const url = route.request().url();
    return url.startsWith('file:') || url.startsWith('data:')
      ? route.continue()
      : route.abort();  // blocks any network beacon
  });
}
await page.goto(pathToFileURL(tmpHtmlPath).href, { waitUntil: 'load' });
await page.evaluate(() => document.fonts.ready);
const pdfBuffer = await page.pdf({
  printBackground: true,
  margin: { top: '0', right: '0', bottom: '0', left: '0' },
  preferCSSPageSize: true,
});
```

---

## build-cv-html.mjs

**Purpose**: Deterministic HTML CV renderer. Takes a JSON payload (produced by the agent/LLM) + an HTML template, merges them via `{{PLACEHOLDER}}` substitution. Owns HTML escaping, contact-row conditional markup, optional section partials from `templates/sections/`.
**LOC**: 941
**Inputs**: `<input.json>` (CV payload: candidate, summary, competencies, experience[], projects[], education[], certifications[], awards[], skills[]), `templates/cv-template.html` (or a profile-selected template via `cv-templates.mjs`).
**Outputs**: `<output.html>`; JSON stats to stdout (`{file, path, sizeKB, counts: {competencies, experienceEntries, ...}, valid}`).
**LLM calls**: None directly — but the JSON payload is the LLM's output (the agent reads cv.md, tailors content, writes structured JSON).
**External deps**: `./cv-sections-core.mjs` (stripEmptySections).
**Standalone runnable?**: ⚠️ Partial — runs standalone, but is useless without an LLM producing the JSON payload. This is the post-LLM renderer half of a 2-step pipeline: agent → JSON → HTML → PDF.
**Adaptation effort**: Low for the renderer; High if you want the full pipeline (need an LLM to produce JSON).
**Key insight**: This is the design pattern that *moves LLM output tokens from full HTML markup down to a structured JSON payload*. The agent emits ~200 lines of JSON instead of ~600 lines of HTML — saves tokens, prevents the model from breaking the template, makes the output deterministic and byte-comparable. URL sanitization explicitly rejects `javascript:`/`data:` schemes. Throws on unresolved `{{PLACEHOLDER}}` rather than shipping a CV with a literal `{{NAME}}` in it.
**Code excerpt**:
```javascript
function renderHtml(template, payload, templatePath) {
  const partials = templatePath ? loadSectionPartials(templatePath) : new Map();
  const { substitutions, candidate } = renderReport(payload, partials);
  let html = template.replace(CONTACT_ROW_RE, () => buildContactRow(candidate));
  html = html.replace(/\{\{PHOTO\}\}/g, () => buildPhoto(candidate, candidate.name));
  html = stripEmptySections(html, payload, 'html');  // drop empty Projects, etc.
  for (const [key, value] of Object.entries(substitutions)) {
    html = html.replace(new RegExp(`\\{\\{${key}\\}\\}`, 'g'), () => value);
  }
  const unresolved = html.match(PLACEHOLDER_RE);
  if (unresolved) throw new Error(`Unresolved placeholders: ${[...new Set(unresolved)].join(', ')}`);
  return html;
}
```

---

## build-cv-latex.mjs

**Purpose**: LaTeX twin of build-cv-html.mjs. Same JSON payload → `.tex` file via LaTeX template substitution. User then renders with `tectonic` or `pdflatex`.
**LOC**: 343
**Inputs**: `<input.json>` (same payload schema as build-cv-html), `templates/cv-template.tex`.
**Outputs**: `<output.tex>`.
**LLM calls**: None (same caveat as HTML builder — needs LLM-produced JSON).
**External deps**: `./lib/latex-escape.mjs` (`escapeLatex`, `sanitizeUrl`), `./cv-templates.mjs`, `./cv-sections-core.mjs`. Needs a LaTeX toolchain to actually produce PDF (not bundled).
**Standalone runnable?**: ✅ Yes (for .tex output; PDF requires separate LaTeX install)
**Adaptation effort**: Low.
**Key insight**: Mirrors the HTML builder's section structure (`buildEducation`, `buildExperience`, `buildProjects`, `buildAwards`, `buildSkills`) using `\resumeSubheading` / `\resumeProjectHeading` / `\resumeItemListStart` macros. The awards section cleverly reuses `\resumeProjectHeading` (bold left column, year right) rather than `\resumeSubheading` which would leave an empty second row. `\emph{$|$ context}` separator matches the HTML builder's `$|$` styling.
**Code excerpt**:
```javascript
function buildExperience(entries) {
  if (!Array.isArray(entries) || entries.length === 0) return '';
  const blocks = [];
  for (const e of entries) {
    if (!e) continue;
    const bullets = Array.isArray(e.bullets)
      ? e.bullets.map(b => `            \\resumeItem{${escapeLatex(b)}}`).join('\n')
      : '';
    blocks.push(`    \\resumeSubheading\n      {${escapeLatex(e.company)}}{${escapeLatex(e.dates)}}\n      {${escapeLatex(e.role)}}{${escapeLatex(e.location)}}\n      \\resumeItemListStart\n${bullets}\n      \\resumeItemListEnd`);
  }
  return blocks.join('\n\n');
}
```

---

## generate-cover-letter.mjs

**Purpose**: Render a cover letter JSON payload to PDF via the same Playwright pipeline as CVs. Fills `templates/cover-letter-template.html`, runs the CV fact gate, then HTML→PDF.
**LOC**: 305
**Inputs**: `--payload payload.json` (candidate + letter: role_title, opening, profile_intro, achievements[], problems_section, closing, signature, footnotes[]).
**Outputs**: `<output.pdf>` (default `output/{company-slug}-{role-slug}-cover.pdf`); can link to tracker via `--report NNN`.
**LLM calls**: None directly. The payload is LLM-produced; this script just renders.
**External deps**: `./verify-cv-facts.mjs` (`assertFacts` — runs *before* Playwright import so a failed gate never creates a PDF artifact), `./cv-templates.mjs`, dynamically imports `./generate-pdf.mjs` (so Playwright only loads on success path).
**Standalone runnable?**: ⚠️ Partial — same as build-cv-html: needs LLM-produced JSON payload.
**Adaptation effort**: Low for the renderer. `buildHtml()` is exported as pure function — testable without Playwright.
**Key insight**: The fact-gate-before-import-Playwright ordering is the security pattern. A failed gate logs advisory phrases but doesn't write a PDF; a blocking gate throws before Playwright is ever loaded. Single-pass `{{TOKEN}}` substitution (not iterative) ensures a substituted value that itself contains `{{TOKEN}}` is left literal — important because user-provided text (achievements, problems_section) could legitimately contain that sequence.
**Code excerpt**:
```javascript
const html = buildHtml(payload);
const factCheck = assertFacts(html, { label: "cover letter" });
if (factCheck.verdict === "warn") {
  console.error(`CV fact check warning: cover letter`);
  for (const phrase of factCheck.warnings) console.error(`  - advisory phrase: ${phrase}`);
}
// Imported only AFTER fact validation so a failed gate does not load Playwright
const { renderHtmlToPdf } = await import("./generate-pdf.mjs");
await renderHtmlToPdf(html, resolve(payload.output_path), {
  format: args.format || "a4",
  reportNum: args.report,
  inputPath: payloadPath,
});
```

---

## openai-tailor.mjs

**Purpose**: OpenAI-compatible CV tailoring engine — the headless companion to `openai-eval.mjs`. Takes a JD + an evaluation report, builds a system prompt from `_shared.md` + `_writing.md` + `pdf.md` + the HTML template + cv.md + profile.yml, calls `/chat/completions`, saves the tailored HTML.
**LOC**: 349
**Inputs**: `--jd <path>` (JD text), `--report <path>` (eval report), `modes/_shared.md`, `modes/_writing.md`, `modes/pdf.md`, `cv.md`, `config/profile.yml`, `templates/cv-template.html`.
**Outputs**: `output/cv-{candidate-slug}-{company-slug}.html`; prints next-step `node generate-pdf.mjs ...` command.
**LLM calls**: ✅ YES — single `POST {baseUrl}/chat/completions`. Messages: [system (large context dump), user ("EVALUATION REPORT: ... JOB DESCRIPTION: ... Output ONLY raw HTML.")]. `temperature: 0.2`, `stream: false`. Default `gpt-4o`, OpenAI-compatible base URL.
**External deps**: `js-yaml`, `dotenv` (optional), native `fetch`. **No Playwright** (it just emits HTML; the user runs `generate-pdf.mjs` separately).
**Standalone runnable?**: ✅ Yes — fully standalone, *already* decoupled from Claude Code. Requires `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `OPENAI_MODEL` env vars. Host-gated: refuses non-HTTPS remote endpoints (loopback exempt). Includes prompt caching for non-OpenAI gateways (OpenRouter, DeepSeek — `cache_control: { type: 'ephemeral' }`).
**Adaptation effort**: Low — this is the **template for how we'd wire our own LLM** (e.g. z-ai-web-dev-sdk). The system prompt construction is explicit and copyable.
**Key insight**: Strips markdown block wrapping if the LLM adds it (```` ```html ... ``` ````) despite instructions. Endpoint security check refuses cleartext remote endpoints. Prompt caching gated on host: `api.openai.com` gets a plain-string system message (caches long prefixes automatically, may reject `cache_control`), other gateways get the ephemeral breakpoint. This is the cleanest "swap Claude Code for any LLM" pattern in the repo — it's already done.
**Code excerpt**:
```javascript
export function buildSystemMessage(prompt, host) {
  if (host === 'api.openai.com') return { role: 'system', content: prompt };
  return {
    role: 'system',
    content: [{ type: 'text', text: prompt, cache_control: { type: 'ephemeral' } }],
  };
}
const res = await fetch(endpoint, {
  method: 'POST',
  headers,
  body: JSON.stringify({
    model: modelName,
    messages: [
      buildSystemMessage(systemPrompt, endpointHost),
      { role: 'user', content: `EVALUATION REPORT:\n\n${reportText}\n\nJOB DESCRIPTION:\n\n${jdText}\n\nNow, generate and output the fully filled HTML CV matching the rules above. Output ONLY raw HTML.` },
    ],
    stream: false, temperature: 0.2,
  }),
  signal: AbortSignal.timeout(timeoutMs),
});
tailoredHtml = data.choices?.[0]?.message?.content?.trim();
tailoredHtml = tailoredHtml.replace(/^\s*```(html)?\s*/i, '').replace(/\s*```\s*$/, '');
```

---

## verify-cv-facts.mjs

**Purpose**: Hallucination checker for generated candidate-facing documents. Verifies that metrics (counts, percentages, multipliers, currency) and non-metric facts (employers, titles, tools) in the generated CV/cover letter actually appear in the source files (cv.md, article-digest.md, config/cv-facts.json allowlist).
**LOC**: 724
**Inputs**: `<generated-cv.html|md|tex>` (positional arg), `--source <path>` (repeatable; defaults `['cv.md', 'article-digest.md']`), `config/cv-facts.json` (allow_metrics, allow_facts, forbidden_phrases, warn_phrases).
**Outputs**: `{verdict: 'pass'|'warn'|'block', invented: [], unsupportedFacts: [{kind, value}], forbidden: [], warnings: []}`; `assertFacts()` throws on `block`. CLI prints human report.
**LLM calls**: None.
**External deps**: Pure Node.
**Standalone runnable?**: ✅ Yes — pure regex/text analysis. Drop-in.
**Adaptation effort**: Low. Solid defensive coding: NFKC digit folding covers Arabic-Indic/Persian/Devanagari/Bengali/Thai/etc., Arabic `%`/`.`/`,` separators folded, space-grouped thousands (`16 181`) joined. Modifier window of 4 words between number and noun handles `~5 live Cloud Run deployments`.
**Key insight**: The metric-claim regex is genuinely clever: `\b(\d[\d,.]*(?:[kKmMbB]\b)?)\s*\+?\s*(?:[A-Za-z][A-Za-z-]*\s+){0,4}(METRIC_NOUN)\b` — captures `50k users`, `5 live Cloud Run deployments`, `1.5M monthly paying customers`, but `50kg users` (k not at boundary) stays as `50` (not `50000`). Both target and sources run through the SAME extraction, so a wider window only ever extracts MORE claims on both sides — can't hide an invented number. Three claim types: count-claims (`N <noun>`), simple-claims (`50%`, `$25k`, `10x`), and fact-claims (employer/title/tool via capitalized-noun chain). v3 includes forbidden_phrases + warn_phrases config (e.g. block "passionate about", warn "rockstar").
**Code excerpt**:
```javascript
const COUNT_CLAIM_RE = new RegExp(
  String.raw`\b(\d[\d,.]*(?:[kKmMbB]\b)?)\s*\+?\s*(?:[A-Za-z][A-Za-z-]*\s+){0,${MODIFIER_WINDOW}}(${METRIC_NOUNS.join('|')})\b`,
  'gi'
);
export function verifyFacts(targetText, { sourcePaths, configPath, cwd } = {}) {
  const sourceText = sourcePaths.map(p => readIfExists(resolveInputPath(p, cwd))).join('\n');
  const config = loadConfig(resolveInputPath(configPath, cwd));
  const allowed = allowedMetricSet(sourceText, config.allow_metrics);
  const targetClaims = metricClaims(targetText);
  const invented = [...targetClaims].filter(claim => !allowed.has(claim));
  const allowedFacts = new Set(config.allow_facts.map(normalizeFact));
  const unsupportedFacts = factClaims(targetText)
    .filter(({ value }) => !sourceContainsFact(sourceNormalized, value) && !allowedFacts.has(value))
    .filter((claim, i, claims) => claims.findIndex(o => o.kind === claim.kind && o.value === claim.value) === i);
  const forbidden = config.forbidden_phrases.filter(p => stripMarkup(targetText).toLowerCase().includes(p.toLowerCase()));
  const warnings  = config.warn_phrases   .filter(p => stripMarkup(targetText).toLowerCase().includes(p.toLowerCase()));
  return { verdict: invented.length || unsupportedFacts.length || forbidden.length ? 'block' : warnings.length ? 'warn' : 'pass', invented, unsupportedFacts, forbidden, warnings };
}
```

---

## company-history.mjs

**Purpose**: Per-company evidence-card aggregator (READ-ONLY). Joins tracker + follow-ups + scan-history per company, renders an evidence card per company covering responsiveness (silence window) + postingChurn (reposts via detect-reposts.mjs). Deliberately reports FACTS not verdicts — never uses "ghost"/"risk".
**LOC**: 891
**Inputs**: `data/applications.md`, `data/follow-ups.md`, `data/scan-history.tsv`, `data/active-interviews.md` (via dynamic import of `funnel-velocity.mjs`).
**Outputs**: JSON to stdout: `{companies: [{name, trackerRows, responsiveness: {everResponded, silentOnYou, ...}, postingChurn: {repostClusters, ...}}], generatedAt}`; `--summary` human cards; `--company "Acme"` single lookup.
**LLM calls**: None.
**External deps**: `js-yaml`, `./detect-reposts.mjs`, `./tracker-utils.mjs`, `./tracker-parse.mjs`, `./followup-cadence.mjs` (parseFollowups, parseAppliedDate, parseDate, daysBetween, normalizeStatus).
**Standalone runnable?**: ✅ Yes
**Adaptation effort**: Low.
**Key insight**: Strict "facts not verdicts" discipline — explicitly avoids "ghost"/"ghosted"/"risk" because "high-volume inboxes, evergreen requisitions, re-opened searches, and the candidate's own unlogged responses all produce the same raw signals as genuine silence". Uses `Intl.Collator('en')` for locale-independent deterministic ordering. `RESPONDED_STATUSES` includes `rejected` — a rejection IS an answer. `OUTCOME_LABELS` includes `hired` — omitting it once labelled a company that hired you `no-history`. Optional `--include-stale` to count >365d-old facts in label computation (default: stale facts archived).
**Code excerpt**:
```javascript
const EXPLANATION_LINE =
  'high-volume inboxes, evergreen requisitions, re-opened searches, and your own unlogged responses ' +
  'all produce these patterns — facts, not verdicts';
const RESPONDED_STATUSES = new Set(['responded', 'interview', 'offer', 'hired', 'rejected']);
const OUTCOME_LABELS = { responded: 'Responded', interview: 'Interview', offer: 'Offer', hired: 'Hired', rejected: 'Rejected' };
```

---

## company-funded.mjs

**Purpose**: Discover recently funded companies from public RSS feeds (TechCrunch, PRNewswire, Guardian) + Hacker News Algolia API, for manual review. V1 is narrow: reads structured feeds, writes a report, never probes company websites or edits portals.yml.
**LOC**: 1023
**Inputs**: RSS feeds (hardcoded URLs), HN Algolia API, optional `--sources` filter.
**Outputs**: `reports/company-funded-{date}.md` + `data/company-funded-{date}.json`; or `--json` to stdout. `--dry-run` skips writes.
**LLM calls**: None — pure HTTP + RSS parsing + regex extraction.
**External deps**: `./providers/_html-entities.mjs` (decodeEntities), `./user-agent.mjs` (BROWSER_LIKE_USER_AGENT), native `fetch`.
**Standalone runnable?**: ✅ Yes
**Adaptation effort**: Low.
**Key insight**: `extractCompanyFromFundingTitle()` regex is hand-tuned against 8 fixture cases — handles "Prime Intellect raises $130M Series A" → "Prime Intellect", "Ex-DeepMind David Silver Raises $1.1B for AI Startup Ineffable" → "Ineffable" (extracts the STARTUP name not the founder), "AI startup valuations raise bubble fears as funding surges" → `` (empty — correctly rejects the noise title). `GENERIC_NAMES` set blocks "ai", "startup", "founder", "valuation", "bubble" etc. from being reported as companies. RSS source ranking: techcrunch=70, prnewswire=65, guardian=50, hacker_news=35, web=5 — for tie-breaking. SSRF guard: `matchesDomain()` validates hostname against expected source hosts.
**Code excerpt**:
```javascript
export function extractCompanyFromFundingTitle(title) {
  // Pattern: "{Company} raises ${amount} {round}" — capture company before "raises"
  // Reject noise titles like "AI startup valuations raise bubble fears"
  // ...
}
export function extractFundingDetails(text) {
  const amountMatch = text.match(/\$?\s*(\d[\d,.]*\s?[kKmMbB]?)\s*(million|billion|M|B|k)?/);
  const roundMatch  = text.match(/(pre-seed|seed|series\s+[a-z]|series\s+[a-z]\d?|growth|series unknown|extension)/i);
  return { amount: amountMatch?.[0], round: roundMatch?.[0] };
}
```

---

## contacts.mjs

**Purpose**: Job-search phonebook → vCard 3.0 exporter. Reads `data/contacts.tsv` (user-layer PII, gitignored), exports to `output/contacts.vcf` for iOS/Android import. Stable deterministic UID (`careerops-{uidPart(name)}--{uidPart(company)}`) so re-importing UPDATES entries instead of duplicating.
**LOC**: 485
**Inputs**: `data/contacts.tsv` (9-col: `name | company | type | title | phone | email | linkedin | tracker# | notes`, no header, `#` comment lines allowed).
**Outputs**: `output/contacts.vcf` (vCard 3.0, CRLF endings, 75-octet line folding counted in BYTES that never splits a multibyte UTF-8 sequence); or JSON to stdout (`{contacts, quality: {shortRows, missingRequired, invalidTypes, duplicates}, total}`).
**LLM calls**: None.
**External deps**: Pure Node + `crypto` (SHA1 for UID), `./lib/cli-flags.mjs`.
**Standalone runnable?**: ✅ Yes — pure CLI, zero deps.
**Adaptation effort**: Low. vCard 3.0 chosen over 4.0 because v4.0 support is still patchy on iOS/Android. `--caller-id` mode formats FN as `"Jane Doe (Acme recruiter)"` for the phone's caller ID.
**Key insight**: Update-in-place semantics — if two lines share name+company (same UID), the LAST line wins for `--vcf` (freshest = truth), but JSON keeps every row and reports the clash in `quality.duplicates`. Cells split BEFORE trimming the line so a leading tab (empty name) surfaces as `missingRequired`, not silent column shift. Notes column folds stray-tab tail cells back in (tab → single space) — a stray tab pasted inside a note must not silently drop the tail. UID part: `{slug}-{8-hex sha1 of normalized value}`, or just the bare 8-hex hash when the slug is empty (e.g. fully CJK name) — the hash folds case/whitespace/NFC noise yet keeps values that slug identically (José/Josè) from colliding.
**Code excerpt**:
```javascript
export function parseContacts(content) {
  const contacts = [];
  const quality = { shortRows: [], missingRequired: [], invalidTypes: [], duplicates: [] };
  let lineNo = 0;
  for (const raw of String(content || '').split('\n')) {
    lineNo++;
    const line = raw.replace(/\r$/, '');
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const cells = line.split('\t').map(c => c.trim());  // split BEFORE trim
    if (cells.length < 4) { quality.shortRows.push({ line: lineNo, cells: cells.length }); continue; }
    const [name, company, type, title = '', phone = '', email = '', linkedin = '', tracker = ''] = cells;
    const notes = cells.slice(8).join(' ');  // fold stray-tab tail into notes
    if (!name || !company) { quality.missingRequired.push({ line: lineNo, name, company }); continue; }
    if (type && !VALID_TYPES.has(type)) quality.invalidTypes.push({ line: lineNo, name, type });
    contacts.push({ name, company, type, title, phone, email, linkedin, tracker: tracker === '-' ? null : tracker || null, notes });
  }
  // ... UID duplicate detection
}
```

---

## interview-prep/

**Purpose**: Storage directory for interview prep artifacts — `story-bank.md`, `question-bank.md`, `{company}-{role}.md` prep files, `retracted-claims.md`, and `sessions/*.md` (transcripts from `interview/debrief` and `interview/practice` modes).
**LOC**: 33 (just `sessions/README.md` + `.gitkeep`)
**Inputs**: None — this is a directory, not a script.
**Outputs**: None directly — the modes (modes/interview-prep.md, modes/interview/{plan,practice,debrief}.md) write files here.
**LLM calls**: None (directory only). The actual prep logic is in `modes/interview-prep.md` (357 LOC markdown) + `modes/interview/{plan,practice,debrief}.md` (169/249/241 LOC respectively).
**External deps**: None (just disk).
**Standalone runnable?**: N/A — directory.
**Adaptation effort**: Low — the schema is documented in `sessions/README.md` (YAML frontmatter: company, role, round, date, interviewer_role, source; body: `## Q1` / `**Interviewer:**` / `<!-- competency: tag -->` / `**Candidate:**`).
**Key insight**: Privacy-conscious: gitignored (only README + `.gitkeep` tracked). Session transcripts contain real interviewer names and companies. The `<!-- competency: tag -->` HTML comment annotation pattern is clean — a consumer can read either speaker side without re-inferring who spoke.
**Code excerpt** (the session format spec from `sessions/README.md`):
```markdown
---
company: Acme Corp
role: Instructional Designer
round: behavioral
date: 2026-06-01
interviewer_role: Senior HR Partner
source: debrief
---

## Q1
**Interviewer:** Tell me about a time you...
<!-- competency: stakeholder-management -->
**Candidate:** ...answer...
```

---

## modes/ — markdown prompt files (deep.md, contacto.md, offer-prep.md, interview-redflag.md, reply-watch.md)

**Purpose**: These are **LLM prompt instructions** consumed by Claude Code's `/career-ops <mode>` slash-command system. They are NOT executable code — they are markdown documents the agent reads and follows. Each mode defines inputs, steps, output format, and guardrails.
**LOC**: deep.md=49, contacto.md=141, offer-prep.md=513, interview-redflag.md=271, reply-watch.md=73. Plus modes/interview-prep.md=357, modes/interview/{plan,practice,debrief}.md=169/249/241.
**Inputs**: Each mode declares a list of files to read (cv.md, profile.yml, reports/, etc.) and user-provided inputs (JD, interview date, contract text, etc.).
**Outputs**: Markdown reports, structured prep files in `interview-prep/`, tracker updates (via merge-tracker.mjs), question-bank updates, etc.
**LLM calls**: The modes ARE the LLM calls — they are the system/user prompt content. The agent (Claude Code) reads them and executes the workflow.
**External deps**: **Claude Code** (or equivalent agent runtime that processes slash-commands as agent instructions). Without an agent runtime, these are inert text files.
**Standalone runnable?**: ❌ No — they are prompt text, not code. They need an LLM CLI to interpret them.
**Adaptation effort**: Medium-High — to use these without Claude Code, we'd need to: (1) build an agent loop that reads a mode file, (2) provides the LLM with the mode's content as system/user prompt, (3) gives the LLM file-read/write tools, (4) gives the LLM WebSearch/WebFetch tools (some modes need them). The mode files themselves are well-structured and could be ported as-is to any agent runtime.
**Key insight per mode**:
- **deep.md** (49 LOC): Generates a 6-axis Perplexity/Claude/ChatGPT deep-research prompt (AI strategy, recent moves, engineering culture, challenges, competitors, candidate angle). Trivial — it's just a prompt template.
- **contacto.md** (141 LOC): Outreach message drafter with persona engine (recruiter/HM/peer/interviewer), 3-sentence framework, LinkedIn char-limit awareness (200 free / 300 Premium, live-confirmed), Greeting variant for BOSS Zhipin 打招呼. Voice DNA application from `voice-dna.md`.
- **offer-prep.md** (513 LOC): Contract reading companion — NOT legal advice, never says "safe to sign" or "risky", no online research (contracts never leave the machine), never states law from memory (only reads `templates/restrictive-covenants.yml`), never runs headless. Hard guards listed explicitly. Adapted from Anthropic's `claude-for-legal` `hiring-review` skill.
- **interview-redflag.md** (271 LOC): Analyzes interviewer-side of session transcripts for 4 signal types (scope ambiguity, defensive closure, evaluator competency gap, process signals) + scope/comp mismatch (Step 2b) + protected-grounds questions (Step 2c, jurisdiction-keyed from `templates/protected-grounds.yml`). Reads `interview-prep/sessions/*.md`. Reads original JD text (user-provided, not scraped).
- **reply-watch.md** (73 LOC): Classifies employer replies into 8 categories (Interview, Responded, Need Action, Rejected, Offer, Auto-confirmation, Noise, Unknown), matches to tracker rows, suggests status updates with human-in-the-loop confirmation. Powered by `reply-watch.mjs` + `reply-matcher.mjs` (the actual classification logic is in `.mjs` files, not the mode).

**Code excerpt** (from `modes/contacto.md` — the persona-aware 3-sentence framework):
```markdown
### Recruiter
- **Sentence 1 (Fit)**: Direct match criteria -- role, relevant experience, availability, or location
- **Sentence 2 (Proof)**: Data that answers their screening questions before they ask them
  (e.g., "5 years building ML pipelines, currently in Berlin, available immediately")
- **Sentence 3 (CTA)**: "Happy to share my CV if this aligns with what you're looking for"

### Hiring Manager
- **Sentence 1 (Hook)**: Specific challenge their team is facing (extracted from JD/blog/news)
- **Sentence 2 (Proof)**: Candidate's greatest quantifiable achievement showing they solved similar problems
- **Sentence 3 (CTA)**: "Would love to hear how your team is approaching [specific challenge]"
```

---

## Summary

- **Total files examined**: 21 `.mjs` files + 1 directory (`interview-prep/`) + 6 mode markdown files (deep, contacto, offer-prep, interview-redflag, reply-watch + the 4 interview/* files) = **28 distinct artifacts**
- **Standalone runnable**: **19** (every `.mjs` file except the two CV/cover-letter *renderers* which need an LLM-produced JSON payload to be useful — they run fine standalone, they're just useless without input)
- **Partial**: **2** (build-cv-html.mjs, build-cv-latex.mjs, generate-cover-letter.mjs — they're full renderers, but the JSON payload they consume is LLM output)
- **Requires Claude Code**: **1** truly (openai-tailor.mjs replaces Claude Code with OpenAI — it's the existing alternative path) + all `modes/*.md` files (these are inert prompt text without an agent runtime)
- **Files that make LLM calls directly**: only **openai-tailor.mjs** (POST to OpenAI-compatible `/chat/completions`). Every other `.mjs` file is zero-LLM.

### Top 5 most valuable files for our integration

1. **`tracker.mjs`** (569 LOC) — The markdown-as-source-of-truth + SQLite-derived-index pattern is *exactly* what we need for our own application tracker. Cleanly designed, integrity diagnostics, cross-process lock, history tracking. Drop-in.
2. **`scan.mjs`** (3039 LOC) + **`scan-ats-full.mjs`** (1077 LOC) — The plugin-based portal scanner with ~95 providers is the crown jewel. Even if we don't use all providers, the filter composition patterns (title AND-groups, location word-boundary, remote-title detection, fingerprint cross-listing, scan-history dedup) are reference-grade. The reverse-ATS discovery with checkpoint/resume is genuinely impressive engineering.
3. **`openai-tailor.mjs`** (349 LOC) — The *template* for swapping Claude Code for any LLM. Already decoupled. System prompt construction is explicit and copyable. Host-gated prompt caching pattern. We can adapt this directly to z-ai-web-dev-sdk with ~50 LOC of changes.
4. **`verify-cv-facts.mjs`** (724 LOC) — The hallucination gate. Pure regex/text, no deps, drop-in. The metric-claim regex with `kKmMbB` suffix + 4-word modifier window + NFKC digit folding is genuinely valuable — we should run this on every CV/cover-letter we generate regardless of which LLM produced it.
5. **`pipeline-lock.mjs`** (510 LOC) — Cross-process advisory lock with Windows-contention handling, jittered backoff, two-stage stale reclaim. Zero deps, single file. Reusable for any markdown-file write contention in our own system.

### Top 5 files NOT worth lifting (better alternatives exist)

1. **`build-cv-latex.mjs`** (343 LOC) — LaTeX CV rendering requires a TeX toolchain install (tectonic/pdflatex), which is a heavy dep. The HTML+Playwright path (`build-cv-html.mjs` + `generate-pdf.mjs`) produces byte-identical visual output without the LaTeX install. Only worth lifting if we specifically need LaTeX source files for human editing.
2. **`generate-pdf.mjs`** (1086 LOC) — Heavy: requires Playwright + Chromium (~300MB install). For our own HTML→PDF, Puppeteer or `weasyprint` or `wkhtmltopdf` are lighter. The ATS Unicode normalization function (`normalizeTextForATS`) IS worth lifting though — it's a pure function we can extract and use with any PDF renderer.
3. **`company-funded.mjs`** (1023 LOC) — RSS scraping of TechCrunch/PRNewswire/Guardian is fragile (feeds change, RSS schemas drift) and the title-regex company extraction is hand-tuned against 8 fixtures — narrow coverage. Better alternatives: Crunchbase API (paid but reliable), PitchBook API, or a proper news-API with NER.
4. **`modes/deep.md`** (49 LOC) — A 6-axis Perplexity/Claude/ChatGPT prompt template. Trivially reproducible; lifting it adds no value over copy-pasting the prompt structure into our own agent.
5. **`merge-tracker.mjs`** (1362 LOC) — Tightly coupled to the `batch/batch-runner.sh` workflow + `batch-state.tsv` cross-check. The 3-tier dedup logic (URL, company+role fuzzy, report number) is *interesting* but the integration cost is high. If we adopt the tracker.mjs pattern, we'd write our own merge logic against our own batch workflow rather than retrofit this.

### Critical realization for the integration strategy

career-ops has **two cleanly separable halves**:

1. **The deterministic Node CLI tools** (21 `.mjs` files, ~17k LOC total) — these are zero-LLM, zero-Claude-Code, fully portable. They consume markdown/TSV/YAML and emit JSON/HTML/markdown. We can lift any subset.

2. **The LLM prompt library** (`modes/*.md`, ~100 files, ~10k LOC of markdown) — these are inert text without an agent runtime. To use them, we need to build (or adopt) an agent loop that: reads a mode file, constructs an LLM prompt from it, gives the LLM file-read/write + WebSearch tools, and lets it execute the workflow. **openai-tailor.mjs is the proof-of-concept that this works** — it's the same mode content (`_shared.md` + `_writing.md` + `pdf.md` + cv.md + profile.yml + template) but called via OpenAI's API instead of Claude Code's slash-command system.

The cleanest integration path: **lift the .mjs tools directly, and use `openai-tailor.mjs` as the template for wiring our own LLM (z-ai-web-dev-sdk) against the modes/*.md prompts**. The modes are already well-structured prompt documents — they just need an agent runtime to consume them.
