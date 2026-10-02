// s20_kit_search.mjs — the z-ai-quota-INDEPENDENT web search transport.
//
// Why: the z-ai web_search function hard-429s for hours at a time (the S20
// quota-dead backlog class). This module scrapes DuckDuckGo's html endpoint
// (and Bing as fallback) through the vendored agent-fetch-kit — supabase
// edge proxy = 15 rotating AWS IPs + random JA3 (per-request IP rotation
// defeats per-IP throttling), local curl_cffi chrome131 as fallback. The
// kit writes the RAW (possibly brotli/gzip-compressed) body to --out, so
// we auto-decompress here.
//
// Interface: searchWeb(query, num) → [{ name, url, snippet }] — the SAME
// shape the probe consumed from zai.functions.invoke('web_search').
// Query-level disk cache (14d) under s20_census/search_cache/ — retry
// waves never re-pay for a query.
//
// Rate budget: the supabase function allows 60 req/min per token → a
// module-level gate keeps ≥1.15s between kit calls.

import { execFile } from 'node:child_process'
import { gunzipSync, brotliDecompressSync, inflateSync } from 'node:zlib'
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const REPO = join(dirname(dirname(fileURLToPath(import.meta.url))), '')
const DIR = join(REPO, 'ingest/data/ats_seed/s20_census')

// self-load ingest/.env (kit needs SUPABASE_PROXY_* / FIRECRAWL_API_KEY;
// the kit's own loader reads $KIT_ROOT/.env which is NOT committed)
if (!process.env.SUPABASE_PROXY_URL) {
  try {
    for (const line of readFileSync(join(REPO, 'ingest/.env'), 'utf8').split('\n')) {
      const m = line.match(/^([A-Z0-9_]+)=(.*)$/)
      if (m && !(m[1] in process.env)) process.env[m[1]] = m[2]
    }
  } catch { }
}
const KIT_BIN = join(REPO, 'tools/agent-fetch-kit/bin/wfetch')
const CACHE_DIR = join(DIR, 'search_cache')
const CACHE_TTL_MS = 14 * 24 * 3600 * 1000

mkdirSync(CACHE_DIR, { recursive: true })

/* ── kit gate: serializes calls + enforces the supabase rps budget ── */
let chain = Promise.resolve()
let lastCall = 0
function gate (minGapMs = 1150) {
  const run = async () => {
    const wait = Math.max(0, lastCall + minGapMs - Date.now())
    if (wait) await new Promise(r => setTimeout(r, wait))
    lastCall = Date.now()
  }
  chain = chain.then(run, run)
  return chain
}

function decodeBody (buf) {
  const head = buf.subarray(0, 2)
  if (head[0] === 0x1f && head[1] === 0x8b) {
    try { return gunzipSync(buf) } catch { }
  }
  if (buf[0] === 0x3c || buf[0] === 0x7b || buf[0] === 0x0a) return buf // '<' | '{' | '\n'
  try { return inflateSync(buf) } catch { }
  try { return brotliDecompressSync(buf) } catch { }
  try { return inflateSync(buf, { raw: true }) } catch { }
  return buf
}

/** one kit fetch: returns { status, body } — mode ladder supabase → local */
async function kitFetch (url, { modes = ['supabase', 'local'], timeoutS = 45 } = {}) {
  for (const mode of modes) {
    await gate()
    const out = `/tmp/kit_search_${process.pid}_${Date.now() % 100000}.body`
    const meta = await new Promise((resolve) => {
      execFile('bash', [KIT_BIN, url, '--mode', mode, '--out', out, '--json', '--timeout', String(timeoutS)],
        { timeout: (timeoutS + 15) * 1000 },
        (err, stdout) => {
          let j = null
          try { j = JSON.parse(stdout.slice(stdout.indexOf('{'), stdout.lastIndexOf('}') + 1)) } catch { }
          resolve(j || { status: err ? 0 : -1, error: String(err).slice(0, 120) })
        })
    })
    let body = Buffer.alloc(0)
    try { body = decodeBody(readFileSync(out)) } catch { }
    if (meta.status === 200 && body.length > 500) return { status: 200, body: body.toString('utf8'), backend: mode }
    if (meta.status && meta.status !== 200) { /* try next mode */ }
  }
  return { status: 0, body: '', backend: 'none' }
}

const strip = (s) => s.replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&#x27;|&#39;/g, "'")
  .replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&nbsp;/g, ' ').trim()

/** unwrap DDG's //duckduckgo.com/l/?uddg=<enc> redirect links */
function unwrap (href) {
  if (!href) return ''
  let u = href.startsWith('//') ? 'https:' + href : href
  const m = u.match(/[?&]uddg=([^&]+)/)
  if (m) { try { u = decodeURIComponent(m[1]) } catch { } }
  return u
}

/** unwrap Bing's /ck/a redirect: &u=a1<url-safe base64 of real url> (S24) */
function unwrapBing (href) {
  if (!href) return ''
  const m = href.match(/[?&]u=a1([A-Za-z0-9_-]+)/)
  if (!m) return href
  try {
    const b64 = m[1].replace(/-/g, '+').replace(/_/g, '/')
    const pad = '='.repeat((4 - (b64.length % 4)) % 4)
    const dec = Buffer.from(b64 + pad, 'base64').toString('utf8')
    return dec.startsWith('http') ? dec : href
  } catch { return href }
}

/* ── fleet fallback (S24): the Netlify US-Ohio workers run the DDG html
 * scrape from a US IP — the cure for Alibaba-HK-style sandbox egress where
 * DDG is TCP-dead and Bing serves anti-bot junk. Uses site records from
 * s21_netlify_fleet.mjs (js-fleet-00..). One engine entry, no quota. */
const FLEET_REG = join(REPO, 'ingest/data/ats_seed/s21_fleet/sites.json')
const fleetSites = () => {
  try { return Object.values(JSON.parse(readFileSync(FLEET_REG, 'utf8'))) } catch { return [] }
}
let fleetCursor = Math.floor(Math.random() * 8)
async function fleetSearch (q, num) {
  const sites = fleetSites()
  if (!sites.length) return []
  const enc = encodeURIComponent(q)
  const n = sites.length
  for (let i = 0; i < Math.min(n, 3); i++) {
    const rec = sites[(fleetCursor++) % n]
    const url = `https://${rec.deployId}--${rec.site}.netlify.app/fleet`
    try {
      const ctl = new AbortController()
      const t = setTimeout(() => ctl.abort(), 15000)
      const r = await fetch(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ op: 'search', query: q, num }),
        signal: ctl.signal
      })
      clearTimeout(t)
      const j = await r.json()
      if (j && Array.isArray(j.results) && j.results.length) return j.results.slice(0, num)
    } catch { }
  }
  return []
}

/** parse DDG html SERP: result__a (title/url) + result__snippet */
function parseDDG (html) {
  const out = []
  const seen = new Set()
  const re = /<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)<\/a>/gs
  for (const m of html.matchAll(re)) {
    const url = unwrap(m[1])
    const name = strip(m[2])
    if (!url || !/^https?:\/\//.test(url) || seen.has(url)) continue
    seen.add(url)
    out.push({ name: name.slice(0, 140), url, snippet: '' })
  }
  // attach snippets (positional: snippet blocks follow result blocks)
  const sre = /<a[^>]*class="result__snippet"[^>]*>(.*?)<\/a>/gs
  const snips = [...html.matchAll(sre)].map(m => strip(m[1]))
  out.forEach((r, i) => { r.snippet = (snips[i] || '').slice(0, 200) })
  return out
}

/** parse DDG lite SERP (fallback) */
function parseDDGLite (html) {
  const out = []
  const seen = new Set()
  const re = /<a[^>]*class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)<\/a>/gs
  const snips = [...html.matchAll(/class="result-snippet"[^>]*>(.*?)<\/td>/gs)].map(m => strip(m[1]))
  let i = 0
  for (const m of html.matchAll(re)) {
    const url = unwrap(m[1])
    if (!url || seen.has(url)) continue
    seen.add(url)
    out.push({ name: strip(m[2]).slice(0, 140), url, snippet: (snips[i++] || '').slice(0, 200) })
  }
  return out
}

/**
 * searchWeb(query, num) → [{name, url, snippet}]
 * Engine ladder: DDG html → DDG lite → Bing en-US — with a PERSISTENT
 * circuit breaker (S24): engines that TCP-timeout from the current egress
 * are marked dead for 1h (engine_health.json in the cache dir) so the
 * ladder stops burning 3×45s on a dead engine before reaching Bing.
 */
const HEALTH_F = join(CACHE_DIR, 'engine_health.json')
function engineHealth () {
  try { return JSON.parse(readFileSync(HEALTH_F, 'utf8')) } catch { return {} }
}
function markEngineDead (name, ms = 3600_000) {
  try {
    const h = engineHealth()
    h[name] = Date.now() + ms
    writeFileSync(HEALTH_F, JSON.stringify(h))
  } catch { }
}
function engineAlive (name) {
  const until = engineHealth()[name] || 0
  return Date.now() < until ? false : true
}
export async function searchWeb (q, num = 8) {
  const key = createHash('sha1').update(q).digest('hex').slice(0, 16)
  const cf = `${CACHE_DIR}/${key}.json`
  if (existsSync(cf)) {
    try {
      const c = JSON.parse(readFileSync(cf, 'utf8'))
      const ttl = (Array.isArray(c.results) && c.results.length) ? CACHE_TTL_MS : 600_000
      if (Date.now() - c.ts < ttl && Array.isArray(c.results)) return c.results.slice(0, num)
    } catch { }
  }
  const enc = encodeURIComponent(q)
  const attempts = [
    { engine: 'ddg', url: `https://html.duckduckgo.com/html/?q=${enc}`, parse: parseDDG, timeoutS: 12 },
    { engine: 'ddg', url: `https://html.duckduckgo.com/html/?q=${enc}&kl=us-en`, parse: parseDDG, timeoutS: 12, skipBreaker: true },
    { engine: 'ddglite', url: `https://lite.duckduckgo.com/lite/?q=${enc}`, parse: parseDDGLite, timeoutS: 12 },
    { engine: 'bing', url: `https://www.bing.com/search?q=${enc}&count=${Math.max(num, 10)}&ensearch=1&mkt=en-US&setlang=en`, relevanceGate: true,
      parse: (h) => {
        const out = []
        const seen = new Set()
        // S24: full b_algo blocks — h2 link + the <p> snippet that follows;
        // Bing /ck/a redirect hrefs get unwrapped to the real target URL.
        for (const m of h.matchAll(/<li class="b_algo"[^>]*>([\s\S]*?)<\/li>/gs)) {
          const block = m[1]
          const lm = block.match(/<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a><\/h2>/)
          if (!lm) continue
          const url = unwrapBing(lm[1].replace(/&amp;/g, '&')); const name = strip(lm[2])
          if (!/^https?:\/\//.test(url) || seen.has(url) || url.includes('bing.com')) continue
          seen.add(url)
          const pm = block.match(/<p[^>]*>([\s\S]*?)<\/p>/)
          out.push({ name: name.slice(0, 140), url, snippet: (pm ? strip(pm[1]) : '').slice(0, 220) })
        }
        // legacy fallback: bare h2 matches (SERP variants without b_algo li)
        if (!out.length) {
          for (const m of h.matchAll(/<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)<\/a><\/h2>/gs)) {
            const url = unwrapBing(m[1].replace(/&amp;/g, '&')); const name = strip(m[2])
            if (!/^https?:\/\//.test(url) || seen.has(url) || url.includes('bing.com')) continue
            seen.add(url)
            out.push({ name: name.slice(0, 140), url, snippet: '' })
          }
        }
        return out
      } },
  ]
  let results = []
  const tried = []
  for (const a of attempts) {
    if (!a.skipBreaker && !engineAlive(a.engine)) {
      tried.push(`${a.engine}→SKIPPED(breaker)`)
      continue
    }
    const r = await kitFetch(a.url, { timeoutS: a.timeoutS || 45 })
    tried.push(`${a.url.slice(0, 40)}→${r.status}/${r.body.length}b/${r.backend}`)
    if (r.status === 200 && r.body) {
      results = a.parse(r.body)
      if (results.length) {
        // S24 anti-bot-junk gate (Bing class): a SERP where NO result shares
        // any >=4-char query term in title or URL is a junk serving (real
        // title echo, unrelated content) — do not accept it.
        if (a.relevanceGate) {
          const terms = q.toLowerCase().split(/\s+/).filter(w => w.length >= 4)
          const longest = terms.slice().sort((x, y) => y.length - x.length)[0] || ''
          const hay = results.map(x => (x.name + ' ' + x.url).toLowerCase()).join(' ')
          const hits = terms.filter(t => hay.includes(t)).length
          const ok = (longest && hay.includes(longest)) || hits >= 2
          if (!ok) {
            tried.push(`bing→JUNK(no term overlap, ${results.length}r)`)
            markEngineDead('bing', 30 * 60_000)
            results = []
            continue
          }
        }
        break
      }
    } else if (r.status === 0 || r.status === null) {
      // transport-level failure (timeout / conn refused): breaker this engine
      markEngineDead(a.engine)
    }
  }
  // S24 fleet fallback: if the whole local ladder produced nothing (dead
  // egress for DDG, Bing anti-bot junk), the US-Ohio workers run the DDG
  // scrape for us. Result shape matches (name, url, snippet-less).
  if (!results.length) {
    results = await fleetSearch(q, num)
    tried.push(`fleet→${results.length}r`)
  }
  // S24: only POSITIVE results get the long TTL; empty results are a
  // negative cache with a 10-min TTL (egress/engine transients must not
  // poison the query for 14 days).
  writeFileSync(cf, JSON.stringify({ q, ts: Date.now(), tried, results }))
  return results.slice(0, num)
}

// CLI: node s20_kit_search.mjs "fuyao glass careers" 8
if (process.argv[1] && process.argv[1].endsWith('s20_kit_search.mjs')) {
  const q = process.argv[2]
  if (!q) { console.log('usage: node s20_kit_search.mjs "<query>" [num]'); process.exit(1) }
  searchWeb(q, parseInt(process.argv[3] || '8')).then(rs => {
    console.log(JSON.stringify(rs, null, 1))
  }).catch(e => { console.error(String(e).slice(0, 200)); process.exit(1) })
}
