// S20-D surface probe v3 — careers-surface discovery at scale.
//
// Modes:
//   --mode identity  — wire_now candidates: live-fetch the matched board,
//                      extract its SELF-REPORTED company name (the authority),
//                      confirm/refute the census match.
//   --mode surface   — probe_A/probe_B candidates: web-search "{brand} jobs
//                      careers" -> triage URLs (aggregator blocklist, brand
//                      token, ATS domain patterns) -> fetch top candidates
//                      -> classify platform via URL + body markers + title.
//
// Resumable: one evidence file per company under probe/<slug>.json.
// Transports: direct fetch (UA: chrome) with proxy fallback for geo-blocks.
// Usage: node s20_surface_probe.mjs --mode surface --tier probeA --limit 20
import ZAI from '../ingest/vendor/z-ai-web-dev-sdk/dist/index.js'
import { searchWeb } from './s20_kit_search.mjs'
import { execFile } from 'node:child_process'
import { readFileSync, writeFileSync, existsSync } from 'node:fs'

const DIR = '/home/z/job-sourcing-research/ingest/data/ats_seed/s20_census'
const QUEUE = `${DIR}/s20_queue.json`
const PROBE = `${DIR}/probe`
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

// ---- aggregator / non-career domains (never a company's own surface) ----
const BLOCK_DOMAINS = [
  'linkedin.com', 'indeed.com', 'glassdoor.com', 'ziprecruiter.com',
  'jooble.org', 'adzuna.com', 'talent.com', 'simplyhired.com',
  'careerbuilder.com', 'monster.com', 'dice.com', 'theladders.com',
  'builtin.com', 'builtinnyc.', 'builtincolorado.', 'builtinaustin.',
  'jobs.com', 'jobstore.com', 'hired.com', 'vettery.com', 'hiringcafe.com',
  'google.com/search', 'bing.com', 'duckduckgo.com', 'yahoo.com',
  'wikipedia.org', 'wikidata.org', 'reddit.com', 'quora.com', 'x.com',
  'twitter.com', 'facebook.com', 'youtube.com', 'medium.com', 'substack.com',
  'crunchbase.com', 'pitchbook.com', 'zoominfo.com', 'apollo.io',
  'glassdoor.', 'ambitionbox.com', 'jobstreet.com', 'seek.com.au',
  'bayt.com', 'naukri.com', '51job.com', 'zhaopin.com', 'liepin.com',
  'bosszhipin.com', 'jobsdb.com', 'ca.indeed.com', 'uk.indeed.com',
  'myvisajobs.com', 'h1bgrader.com', 'h1bdata.info', 'levels.fyi',
  'teamblind.com', 'blind.com', 'glassdoor.co', 'comparably.com',
  'craft.co', 'theorg.com', 'enlyft.io', 'growjo.com', 'signalhire.com',
  'rocketreach.co', 'lusha.com', 'dwapp.io', 'startup.jobs', 'ycombinator.com/jobs',
  'wellfound.com', 'angel.co', 'otta.com', 'himalayas.app', 'remoteok.com',
  'weworkremotely.com', 'flexjobs.com', 'remotive.com', 'hiring.cafe',
  'workatastartup.com', 'jobsineu.', 'europeremotely.com', 'remote.co',
  'builtinboston.com', 'builtinsf.com', 'builtinla.com', 'builtinnyc.com',
  'builtinchicago.com', 'builtintexas.com', 'builtincolorado.com',
  'www.glassdoor', 'techjobs.com', 'jobsflag.com', 'jobcase.com',
  'ziprecruiter', 'monster.globals', 'hk.jobsdb.com', 'amazon.jobs',
  'applytojob.com/jobs', 'jobs2careers.com', 'jobrapido.com', 'trovit.com',
  'jobillico.com', 'neuvoo.com', 'jooble.com', 'careersinhubs.com',
  'mycareersfuture.gov.sg', 'jobbank.gov.sg', 'gov.uk', 'jobstreet.com'
]

const ATS_URL_PATTERNS = [
  { platform: 'greenhouse', re: /boards\.greenhouse\.io\/([a-zA-Z0-9_-]+)/, slug: 1 },
  { platform: 'lever', re: /jobs\.lever\.co\/([a-zA-Z0-9_-]+)/, slug: 1 },
  { platform: 'ashby', re: /jobs\.ashbyhq\.com\/([a-zA-Z0-9_.-]+)/, slug: 1 },
  { platform: 'smartrecruiters', re: /careers\.smartrecruiters\.com\/([a-zA-Z0-9_-]+)/, slug: 1 },
  { platform: 'workday', re: /(?:wd\d*\.|jobs\.)?myworkdayjobs\.com\/([a-zA-Z0-9_-]+)/, slug: 1 },
  { platform: 'workable', re: /apply\.workable\.com\/([a-zA-Z0-9_-]+)/, slug: 1 },
  { platform: 'feishu', re: /([a-zA-Z0-9-]+)\.jobs\.feishu\.cn/, slug: 1 },
  { platform: 'moka', re: /app\.mokahr\.com\/(?:community|campus)?\/?([a-zA-Z0-9_-]+)/, slug: 1 },
  { platform: 'adp', re: /workforcenow\.adp\.com\/mascsr\b.*[?&]cid=([a-f0-9-]{20,40})/i, slug: 1 },
  { platform: 'icims', re: /([a-zA-Z0-9-]+)\.icims\.com/, slug: 1 },
  { platform: 'taleo', re: /(?:[a-zA-Z0-9-]+\.)*taleo\.net/, slug: 0 },
  { platform: 'successfactors', re: /(?:[a-zA-Z0-9-]+\.)?sapsf\.?(?:eu|com)/, slug: 0 },
  { platform: 'eightfold', re: /([a-zA-Z0-9-]+)\.eightfold\.ai/, slug: 1 }
]

const ATS_BODY_MARKERS = [
  ['greenhouse', /grnhse|greenhouse\.io|boards\.greenhouse/],
  ['lever', /jobs\.lever\.co|lever-deployments|lever\.co\/embed/],
  ['ashby', /ashbyhq\.com|ashby\.com\/posting/],
  ['workday', /myworkdayjobs|workdayjobs|wd\d+\.myworkdayjobs/],
  ['smartrecruiters', /smartrecruiters\.com|career\.section/],
  ['workable', /workable\.com|apply\.workable/],
  ['feishu', /jobs\.feishu\.cn|feishuhire/],
  ['moka', /mokahr\.com|moka\.co/],
  ['adp', /workforcenow\.adp\.com|adp\.com\/mascsr/],
  ['icims', /icims\.com|icims-assets/],
  ['taleo', /tbe\.taleo\.net|taleo\.net/],
  ['successfactors', /successfactors|sapsf\.|career\?company=/],
  ['eightfold', /eightfold\.ai|eightfold\./],
  ['phenom', /phenompeople|widgets\.phenom|ph-tracking|phenom\.com/],
  ['paylocity', /paylocity\.recruiting|recruiting\.paylocity\.com/],
  ['radancy', /\/portalpacks\/|tptCore|\/_cms\/\d/],
  ['avature', /avature\.|avaturecloud/],
  ['jobvite', /jobvite\.com/],
  ['recruitee', /recruitee\.com/],
  ['teamtailor', /teamtailor\.com/],
  ['personio', /jobs\.personio\./],
  ['rippling', /ats\.rippling\.com|rippling\.com\/jobs/],
  ['jazzhr', /applytojob\.com|jazzhr\.com/],
  ['teamtailor', /\.teamtailor\.com|teamtailor\.com/],
  ['greenhouse-embed', /boards\.greenhouse\.io\/embed/]
]

function slugify (s) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)
}

const SHORT_HOST_EXACT = new Set(['x.com', 'jobs.com', 'job.com', 'co.com'])
function blocked (url) {
  const u = url.toLowerCase()
  const host = (u.split('/')[2] || '')
  for (const d of BLOCK_DOMAINS) {
    // short/bare domains match by host equality/suffix, NOT substring
    // (u.includes('x.com') would block careers.cox.com / jobs.box.com)
    if (SHORT_HOST_EXACT.has(d)) {
      if (host === d || host.endsWith('.' + d)) return true
    } else if (u.includes(d)) return true
  }
  return false
}

function brandTokens (brand) {
  const toks = brand.toLowerCase().replace(/[^a-z0-9\s]/g, ' ').split(/\s+/)
    .filter(t => t.length > 2 && !['inc', 'ltd', 'llc', 'corp', 'group', 'co', 'the', 'company', 'holdings', 'holding', 'limited', 'technologies'].includes(t))
  return [...new Set(toks)]   // dedupe: "Nan Nan" must not double-count
}

// Generic words that many homonym boards carry ("Group 1 AUTOMOTIVE").
// A brand whose distinctive tokens are ALL generic cannot be name-probed.
const GENERIC_TOKENS = new Set(['automotive', 'financial', 'resources',
  'technology', 'electronics', 'biotech', 'biologics', 'medical', 'digital',
  'mobile', 'media', 'energy', 'healthcare', 'pharma', 'pharmaceutical',
  'science', 'global', 'international', 'america', 'american', 'usa',
  'china', 'chinese', 'pacific', 'industries', 'industrial', 'solutions',
  'systems', 'networks', 'software', 'services', 'capital', 'management'])

// The one surface-identity rule used by every layer. A board/page is
// brand-identified when:
//  - single-token brand (>=4 chars): the token is in host or title
//  - single short brand (<=3, "DJI"): the token must be in the HOST —
//    title-only matches are homonyms ("Automotive" != SG Automotive)
//  - multi-token brand: >=2 significant tokens match ("WuXi Biologics"),
//    OR the first token (>=4 chars) sits in the host (jobs.lenovo.com)
function surfaceMatch (brand, url, title) {
  const toks = brandTokens(brand)
  const host = (url.split('/')[2] || '').toLowerCase()
  const hay = `${url} ${title || ''}`.toLowerCase()
  if (!toks.length) return { ok: false, n: 0 }
  const allGeneric = toks.every(t => GENERIC_TOKENS.has(t))
  if (allGeneric) {
    // only the full brand base in the host can identify a generic-word brand
    const base = brand.toLowerCase().replace(/[^a-z0-9]/g, '')
    return { ok: base.length >= 4 && host.includes(base), n: host.includes(base) ? toks.length : 0 }
  }
  if (toks.length === 1) {
    const t = toks[0]
    const ok = host.includes(t) || (t.length >= 4 && hay.includes(t))
    return { ok, n: ok ? 1 : 0 }
  }
  const n = toks.filter(t => hay.includes(t)).length
  const firstInHost = toks[0].length >= 4 && host.includes(toks[0])
  return { ok: n >= 2 || firstInHost, n }
}

async function fetchPage (url, timeoutMs = 15000) {
  const ac = new AbortController()
  const t = setTimeout(() => ac.abort(), timeoutMs)
  try {
    const r = await fetch(url, {
      signal: ac.signal, redirect: 'follow',
      headers: { 'user-agent': UA, accept: 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8', 'accept-language': 'en-US,en;q=0.9' }
    })
    const text = await r.text()
    return { status: r.status, url: r.url, text: text.slice(0, 200000), bytes: text.length }
  } catch (e) { return { status: 0, url, text: '', bytes: 0, err: String(e).slice(0, 120) } } finally { clearTimeout(t) }
}

/** kit-backed page fetch: used when direct fetch fails (status 0/403/429/
 *  503, tiny body, or CF-challenge markers) — supabase rotating AWS IP
 *  first, zenrows antibot for hard CF (the BeOne class). */
const KIT_BIN = '/home/z/job-sourcing-research/tools/agent-fetch-kit/bin/wfetch'
async function fetchPageKit (url, { antibot = false } = {}) {
  const args = [KIT_BIN, url, '--out', '/tmp/s20p_kit.body', '--json', '--timeout', '40']
  if (antibot) args.push('--antibot')
  else args.push('--mode', 'supabase')
  const meta = await new Promise((resolve) => {
    execFile('bash', args, { timeout: 60 * 1000 }, (err, stdout) => {
      let j = null
      try { j = JSON.parse(stdout.slice(stdout.indexOf('{'), stdout.lastIndexOf('}') + 1)) } catch { }
      resolve(j || { status: err ? 0 : -1, error: String(err).slice(0, 100) })
    })
  })
  let text = ''
  try { text = readFileSync('/tmp/s20p_kit.body', 'utf8') } catch { }
  return { status: meta.status || 0, url, text: text.slice(0, 200000), bytes: text.length, backend: meta.backend }
}

async function fetchPageResilient (url) {
  const page = await fetchPage(url)
  const cf = page.status === 0 || page.status === 403 || page.status === 429 || page.status === 503 ||
    page.bytes < 2000 || /just a moment|challenge-platform|cf-chl|captcha/i.test(page.text.slice(0, 4000))
  if (!cf) return page
  const k = await fetchPageKit(url)
  if (k.status === 200 && k.bytes > 2000 && !/just a moment|challenge-platform|cf-chl/i.test(k.text.slice(0, 4000))) return k
  const z = await fetchPageKit(url, { antibot: true })
  if (z.status === 200 && z.bytes > 1000) return z
  return k.status === 200 ? k : page
}

function extractTitle (html) {
  const m = html.match(/<title[^>]*>([^<]{0,200})<\/title>/i)
  return m ? m[1].trim() : ''
}

function classifyBody (text) {
  const hits = []
  for (const [p, re] of ATS_BODY_MARKERS) if (re.test(text)) hits.push(p)
  return hits
}

function jsonLdJobCount (html) {
  const n = (html.match(/"@type"\s*:\s*"JobPosting"/g) || []).length
  return n
}

// ---------------- identity mode (wire_now) ----------------
async function probeIdentity (rec, zai) {
  const a = (rec.ats || [])[0] || {}
  const spec = `${a.platform}:${a.slug}`
  const out = { brand: rec.brand, tier: 'wire_now', spec, ts: new Date().toISOString() }
  let page = null
  if (a.platform === 'greenhouse') {
    page = await fetchPage(`https://boards.greenhouse.io/${a.slug}`)
    out.board_title = extractTitle(page.text)
    out.board_company = out.board_title.replace(/\s*[-|·].*jobs.*$/i, '').replace(/jobs\s*(at|@)?\s*/i, '').trim()
  } else if (a.platform === 'ashby') {
    const r = await fetchPage(`https://api.ashbyhq.com/posting-api/job-board/${a.slug}`)
    try {
      const j = JSON.parse(r.text)
      out.board_company = j.companyName || ''
      out.jobs = (j.jobs || []).length
      out.board_title = `${out.board_company} (${out.jobs} jobs)`
    } catch { out.board_title = extractTitle(r.text) || `status ${r.status}` }
  } else if (a.platform === 'lever') {
    page = await fetchPage(`https://jobs.lever.co/${a.slug}`)
    out.board_title = extractTitle(page.text)
    out.board_company = out.board_title.replace(/\s*[|·-]\s*.*$/g, '').trim()
  } else {
    out.unsupported_platform = true
  }
  if (page) { out.status = page.status; out.bytes = page.bytes }

  // LLM pair-judgment only with a USABLE self-identified name — junk
  // ("status 200", "(8 jobs)", empty) feeds hallucinated confirms
  const junkTitle = !out.board_company && /^((status \d+)|\s*\(\d+ jobs\)\s*)?$/i.test(out.board_title || '')
  if ((out.board_company || out.board_title) && !junkTitle) {
    try {
      const r = await zai.chat.completions.create({
        messages: [{
          role: 'user',
          content:
            `The census claims company "${rec.brand}" (Chinese-origin, sector ${rec.sector}) runs the ${a.platform} board "${a.slug}". ` +
            `The board's LIVE self-identification: title="${out.board_title}", company="${out.board_company}", jobs=${out.jobs ?? 'unknown'}.\n` +
            `Also LCA employer candidates: ${(rec.lca_employers || []).slice(0, 2).join('; ') || 'none'}.\n` +
            `Judge: (1) is this board REALLY "${rec.brand}"'s own jobs board (not a homonym company)? (2) is "${rec.brand}" itself genuinely Chinese-origin (mainland/HK founded, or Chinese-group-owned brand)?\n` +
            `Reply ONLY JSON: {"board_is_brand": bool, "chinese_origin": bool, "verdict": "confirm|mismatch|unreachable", "note": "short clause"}`
        }],
        temperature: 0, max_tokens: 200
      })
      let t = r.choices[0].message.content.trim().replace(/^```(json)?/i, '').replace(/```$/, '').trim()
      const c = t.indexOf('{'); if (c > 0) t = t.slice(c)
      Object.assign(out, JSON.parse(t))
    } catch (e) { out.llm_err = String(e).slice(0, 100) }
  }
  return out
}

// ---------------- deterministic discovery layer ----------------
// Slug dictionary attack on the platform APIs: each serves a DEFINITIVE
// identity/liveness signal (this is how the S18 feishu sweep worked).
// Cheaper and more reliable than web search; search is the fallback layer.
function slugCandidates (brand) {
  const base = brand.toLowerCase().replace(/[^a-z0-9]/g, '')
  const hyp = brand.toLowerCase().replace(/[^a-z0-9]+/g, '-')
  const first = brand.toLowerCase().split(/\s+/)[0]?.replace(/[^a-z0-9]/g, '')
  const s = new Set()
  for (const x of [base, hyp, first, base + 'us', base + '-us', base + 'usa',
    base + '-usa', base + 'global', base + 'tech', base + 'technologies',
    base + 'americas', base + 'america', first + 'us', first + '-us']) {
    if (x && x.length > 1) s.add(x)
  }
  return [...s].slice(0, 12)
}

async function dictAttack (brand) {
  const found = []
  const slugs = slugCandidates(brand)
  // greenhouse: jobs API 200+meta = live; identity via HTML title
  for (const slug of slugs) {
    const r = await fetchPage(`https://boards-api.greenhouse.io/v1/boards/${slug}/jobs`, 8000)
    if (r.status === 200 && r.text.trim().startsWith('{') && r.bytes > 30) {
      const page = await fetchPage(`https://boards.greenhouse.io/${slug}`, 8000)
      const title = extractTitle(page.text)
      if (!/page not found|no such board/i.test(title)) {
        found.push({ platform: 'greenhouse', slug, spec: `greenhouse:${slug}`, title, jobs_hint: (r.text.match(/"title":/g) || []).length })
      }
    }
    if (found.length) break
  }
  // ashby: companyName is definitive
  for (const slug of found.length ? [] : slugs) {
    const r = await fetchPage(`https://api.ashbyhq.com/posting-api/job-board/${slug}`, 8000)
    try {
      const j = JSON.parse(r.text)
      if (j && (j.jobs || j.companyName)) {
        found.push({ platform: 'ashby', slug, spec: `ashby:${slug}`, title: `${j.companyName || ''} (${(j.jobs || []).length} jobs)`, jobs: (j.jobs || []).length, self_name: j.companyName })
        break
      }
    } catch { /* not a board */ }
  }
  // lever: HTML title
  for (const slug of found.length ? [] : slugs) {
    const r = await fetchPage(`https://jobs.lever.co/${slug}`, 8000)
    if (r.status === 200 && r.bytes > 2000) {
      const title = extractTitle(r.text)
      if (title && !/page not found|404|lever/i.test(title.slice(0, 12)) && /lever\.co/.test(r.url)) {
        found.push({ platform: 'lever', slug, spec: `lever:${slug}`, title, jobs_hint: (r.text.match(/posting-url/g) || []).length })
        break
      }
    }
  }
  // smartrecruiters: company API carries name + postings total (definitive)
  for (const slug of found.length ? [] : slugs) {
    const r = await fetchPage(`https://api.smartrecruiters.com/v1/companies/${slug}`, 8000)
    try {
      const j = JSON.parse(r.text)
      if (j && j.identifier && (j.postings?.total > 0 || j.name)) {
        found.push({ platform: 'smartrecruiters', slug, spec: `smartrecruiters:${slug}`, title: `${j.name} (${j.postings?.total || '?'} jobs)`, self_name: j.name })
        break
      }
    } catch { /* not a company */ }
  }
  // feishu portal existence (the S18 sweep pattern)
  if (!found.length) {
    for (const slug of slugs) {
      const r = await fetchPage(`https://${slug}.jobs.feishu.cn`, 8000)
      if (r.status === 200 && r.bytes > 1000) {
        found.push({ platform: 'feishu', slug, spec: `feishuhire:${slug}`, title: extractTitle(r.text) })
        break
      }
    }
  }
  return found
}

// URL guessing for custom careers sites (jobs.lenovo.com class).
// CRITICAL: after redirects the surface must STILL be brand-identified —
// careers.csc.com redirects to DXC (the merger); that is a collision, not
// a surface. Same brandish rule as the search layer.
async function guessUrls (brand) {
  const b = brand.toLowerCase().replace(/[^a-z0-9]/g, '')
  const first = brand.toLowerCase().split(/\s+/)[0]?.replace(/[^a-z0-9]/g, '')
  const guesses = [
    `https://jobs.${b}.com`, `https://careers.${b}.com`,
    `https://www.${b}.com/careers`, `https://${b}.com/careers`,
    `https://jobs.${first}.com`, `https://careers.${first}.com`,
    `https://www.${first}.com/careers`, `https://${first}.com/careers`,
    `https://www.${b}.com/en/careers`, `https://www.${b}.com/us/en/careers`,
    `https://www.${b}.us`, `https://${b}.us/careers`
  ].slice(0, 8)
  const toks = brandTokens(brand)
  const stillBrandish = (finalUrl, title) => surfaceMatch(brand, finalUrl, title).ok
  const found = []
  for (const u of guesses) {
    const r = await fetchPage(u, 9000)
    if (r.status === 200 && r.bytes > 5000) {
      const title = extractTitle(r.text)
      const careersish = /job|career|position|hiring|opportunit|join us|work with/i.test(title)
      if (careersish && !/just a moment|challenge/i.test(title) && stillBrandish(r.url, title)) {
        const markers = classifyBody(r.text)
        found.push({ url: r.url, title, markers, platform: markers.length ? markers[0] : 'custom' })
        break
      }
    }
  }
  return found
}

// ---------------- surface mode (probe_A / probe_B) ----------------
async function probeSurface (rec, zai, noSearch = false) {
  const out = { brand: rec.brand, tier: rec.evidence || 'probe', lca: rec.lca_employers?.[0] || null, ts: new Date().toISOString(), candidates: [], results: [], dict_hits: [] }

  // 1) deterministic: platform slug dictionary attack
  let dict = []
  try { dict = await dictAttack(rec.brand) } catch { }
  out.dict_hits = dict
  if (dict.length) {
    const d = dict[0]
    out.best = { spec: d.spec, platform: d.platform, title: d.title, self_name: d.self_name, via: 'dict' }
    // identity: the platform's SELF-REPORTED name (authoritative) vs brand
    // identity: the platform's SELF-REPORTED name (authoritative) vs brand.
    // CRITICAL: no fake-host trick — a slug-derived host ALWAYS contains
    // the slug, so it would auto-pass. With no self_name the check must
    // stay UNKNOWN (job-content adjudication is the human's call).
    const selfName = d.self_name || (d.title || '').replace(/^Jobs at /, '')
    let idm = null
    if (selfName && selfName.trim() && !/^(Recruitment|\s*\(\d+ jobs\))/.test(selfName)) {
      idm = surfaceMatch(rec.brand, 'https://noname.invalid/', selfName)
      out.identity_check = { self: selfName, brand: rec.brand, ok: idm.ok, n: idm.n }
    } else {
      out.identity_check = { self: null, brand: rec.brand, ok: null, note: 'no self-name — job-content adjudication required' }
    }
    if (idm && idm.ok) {
      out.verdict = 'ats_surface'
      return out
    }
    if (idm) {
      out.verdict = 'dict_collision'   // slug matched but identity refuted
      return out
    }
    // no self-name: surface found, identity UNRESOLVED (jobs adjudication)
    out.verdict = 'dict_unverified'
    return out
  }

  // 2) deterministic: custom-site URL guessing
  let guessed = []
  try { guessed = await guessUrls(rec.brand) } catch { }
  if (guessed.length) {
    const g = guessed[0]
    out.best = { url: g.url, platform: g.platform, title: g.title, via: 'guess' }
    out.verdict = g.markers?.length ? 'marker_surface' : 'custom_surface'
    return out
  }

  // 3) web search (variance-prone; retried across runs). --no-search
  // runs the deterministic layers only. --engine kit scrapes DDG-html
  // via agent-fetch-kit (supabase rotating IPs) — immune to the z-ai
  // web_search 429 quota; --engine auto (default) tries z-ai then falls
  // back to the kit engine on 429/empty.
  if (noSearch) {
    out.verdict = 'no_results'
    out.search_skipped = true
    return out
  }
  const engine = (process.env.S20_SEARCH_ENGINE || 'auto')
  const queries = [`${rec.brand} careers`, `${rec.brand} jobs`, `${rec.brand} careers jobs hiring`]
  const results = []
  let searchBackend = null
  for (const q of queries) {
    if (engine === 'kit') {
      try { results.push(...(await searchWeb(q, 8)).map(x => ({ t: x.name?.slice(0, 110), u: x.url, s: x.snippet?.slice(0, 160) }))); searchBackend = 'kit' } catch (e) { out.search_err = String(e).slice(0, 100) }
    } else {
      try {
        const r = await zai.functions.invoke('web_search', { query: q, num: 8 })
        results.push(...(r || []).map(x => ({ t: x.name?.slice(0, 110), u: x.url, s: x.snippet?.slice(0, 160) })))
        searchBackend = 'zai'
      } catch (e) {
        out.search_err = String(e).slice(0, 100)
        // z-ai 429 (quota-dead window) → kit engine transparent fallback
        try { results.push(...(await searchWeb(q, 8)).map(x => ({ t: x.name?.slice(0, 110), u: x.url, s: x.snippet?.slice(0, 160) }))); searchBackend = 'kit-fallback' } catch { }
      }
    }
    await new Promise(r => setTimeout(r, 1200))
  }
  if (searchBackend) out.search_backend = searchBackend
  out.results = results.slice(0, 20)

  // triage: drop aggregators; score ATS-pattern hits + brand-token domains
  const seen = new Set()
  const ranked = []
  for (const x of results) {
    if (!x.u || seen.has(x.u) || blocked(x.u)) continue
    seen.add(x.u)
    let score = 0
    const u = x.u.toLowerCase()
    for (const p of ATS_URL_PATTERNS) {
      if (p.re.test(u)) { score += 10; break }
    }
    const toks = brandTokens(rec.brand)
    const host = u.split('/')[2] || ''
    for (const t of toks) if (u.includes(t)) { score += 3; break }
    if (/career|jobs|careers|job\b/.test(u)) score += 2
    if (u.includes(rec.brand.toLowerCase().split(/\s+/)[0]?.slice(0, 6))) score += 2
    ranked.push({ ...x, score })
  }
  ranked.sort((a, b) => b.score - a.score)

  // fetch top candidates (max 3, only score>0 or careers-ish URLs)
  const top = ranked.filter(x => x.score > 0 || /career|jobs/.test(x.u)).slice(0, 3)
  for (const x of top) {
    // greenhouse/ashby/lever/smartrecruiters/workday URL pattern = direct spec
    let spec = null
    for (const p of ATS_URL_PATTERNS) {
      const m = x.u.match(p.re)
      if (m) { spec = p.slug ? `${p.platform}:${m[p.slug]}` : `${p.platform}:${x.u.split('/')[2]}`; break }
    }
    const page = await fetchPageResilient(x.u)
    const title = extractTitle(page.text)
    const markers = classifyBody(page.text)
    const jd = jsonLdJobCount(page.text)
    const cf = /just a moment|challenge-platform|cf-chl/i.test(page.text.slice(0, 3000)) || /just a moment/i.test(title)
    out.candidates.push({
      url: x.u, title, status: page.status, bytes: page.bytes,
      markers, jsonld_jobs: jd, spec, score: x.score, snippet: x.s,
      backend: page.backend || 'direct',
      cf_challenge: cf || undefined
    })
    await new Promise(r => setTimeout(r, 1200))
  }

  // pick best surface
  const withSpec = out.candidates.filter(c => c.spec && c.status === 200)
  const brandish = (c) => surfaceMatch(rec.brand, c.url, c.title).ok
  const markered = out.candidates.filter(c => !c.spec && c.status === 200 && c.markers.length && brandish(c))
  // custom surface: the brand's OWN domain careers page (title carries the
  // jobs/careers signal + brand token) — the Radancy/Phenom/SPA class where
  // job listings load client-side; the dump phase reverse-engineers the API
  const customOk = out.candidates.filter(c => !c.spec && c.status === 200 &&
    !c.markers.length && /job|career|hiring|position|opportunit/i.test(c.title || '') && brandish(c))
  if (withSpec.length) {
    // smartrecruiters serves 200 for ANY slug: validate identity + jobs live
    const pick = withSpec.sort((a, b) => b.score - a.score)[0]
    if (pick.spec.startsWith('smartrecruiters:')) {
      const slug = pick.spec.split(':')[1]
      const api = await fetchPage(`https://api.smartrecruiters.com/v1/companies/${slug}`)
      try {
        const j = JSON.parse(api.text)
        pick.sr_company = j.name || null
        pick.sr_jobs = (j.postings || {}).total || 0
        pick.spec = j.name ? pick.spec : pick.spec + ' (unverified)'
      } catch { pick.sr_err = api.status }
    }
    out.best = pick
    out.verdict = 'ats_surface'
  } else if (markered.length) {
    const c = markered.sort((a, b) => b.score - a.score)[0]
    out.best = { url: c.url, platform: c.markers[0], title: c.title }
    out.verdict = 'marker_surface'
  } else if (customOk.length) {
    const c = customOk.sort((a, b) => b.score - a.score)[0]
    out.best = { url: c.url, platform: 'custom', title: c.title }
    out.verdict = 'custom_surface'
  } else {
    out.verdict = out.candidates.length ? 'no_clear_surface' : 'no_results'
  }
  return out
}

async function main () {
  const args = Object.fromEntries(process.argv.slice(2).map((v, i, a) => v.startsWith('--') ? [v.slice(2), a[i + 1]] : []).filter(x => x[0]))
  if (args.engine) process.env.S20_SEARCH_ENGINE = args.engine
  const mode = args.mode || 'surface'
  const tier = args.tier || 'probe_A'
  const limit = parseInt(args.limit || '15')
  const offset = parseInt(args.offset || '0')

  const queue = JSON.parse(readFileSync(QUEUE, 'utf8'))
  const list = mode === 'identity' ? queue.wire_now : (queue[tier] || [])
  const slice = list.slice(offset, offset + limit)
  const zai = await ZAI.create()

  for (const rec of slice) {
    const f = `${PROBE}/${slugify(rec.brand) || slugify(rec.name)}.json`
    if (existsSync(f)) {
      const prior = JSON.parse(readFileSync(f, 'utf8'))
      const good = ['ats_surface', 'marker_surface', 'custom_surface', 'dict_collision'].includes(prior.verdict)
      if (good || !args.retry) { console.log('skip', good ? '(done)' : '(exists, use --retry)', rec.brand); continue }
      // retry mode: only failed verdicts get re-probed (search variance,
      // rate-limit blanks, CF challenges)
    }
    let out
    try {
      out = mode === 'identity' ? await probeIdentity(rec, zai) : await probeSurface(rec, zai, args.search === 'false')
    } catch (e) { out = { brand: rec.brand, err: String(e).slice(0, 200) } }
    writeFileSync(f, JSON.stringify(out, null, 1))
    const best = out.best ? (out.best.spec || out.best.platform || out.best.url) : (out.verdict || 'err')
    console.log(`${out.verdict || 'identity'} | ${rec.brand} -> ${String(best).slice(0, 90)}`)
    await new Promise(r => setTimeout(r, 2500))
  }
  console.log('probe done for', slice.length, 'records from', tier)
}

main().catch(e => { console.error(String(e).slice(0, 300)); process.exit(1) })
