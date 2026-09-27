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

const KIT_BIN = '/home/z/job-sourcing-research/tools/agent-fetch-kit/bin/wfetch'
const CACHE_DIR = '/home/z/job-sourcing-research/ingest/data/ats_seed/s20_census/search_cache'
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
 * Engine ladder: DDG html (supabase → local) → DDG lite → Bing en-US.
 * Cached per-query on disk (14d TTL).
 */
export async function searchWeb (q, num = 8) {
  const key = createHash('sha1').update(q).digest('hex').slice(0, 16)
  const cf = `${CACHE_DIR}/${key}.json`
  if (existsSync(cf)) {
    try {
      const c = JSON.parse(readFileSync(cf, 'utf8'))
      if (Date.now() - c.ts < CACHE_TTL_MS && Array.isArray(c.results)) return c.results.slice(0, num)
    } catch { }
  }
  const enc = encodeURIComponent(q)
  const attempts = [
    { url: `https://html.duckduckgo.com/html/?q=${enc}`, parse: parseDDG },
    { url: `https://html.duckduckgo.com/html/?q=${enc}&kl=us-en`, parse: parseDDG },
    { url: `https://lite.duckduckgo.com/lite/?q=${enc}`, parse: parseDDGLite },
    { url: `https://www.bing.com/search?q=${enc}&count=${Math.max(num, 10)}&ensearch=1&mkt=en-US&setlang=en`,
      parse: (h) => {
        const out = []
        const seen = new Set()
        for (const m of h.matchAll(/<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)<\/a><\/h2>/gs)) {
          const url = m[1]; const name = strip(m[2])
          if (!/^https?:\/\//.test(url) || seen.has(url) || url.includes('bing.com')) continue
          seen.add(url)
          out.push({ name: name.slice(0, 140), url, snippet: '' })
        }
        return out
      } },
  ]
  let results = []
  const tried = []
  for (const a of attempts) {
    const r = await kitFetch(a.url)
    tried.push(`${a.url.slice(0, 40)}→${r.status}/${r.body.length}b/${r.backend}`)
    if (r.status === 200 && r.body) {
      results = a.parse(r.body)
      if (results.length) break
    }
  }
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
