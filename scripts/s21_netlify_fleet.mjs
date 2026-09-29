#!/usr/bin/env node
// s21_netlify_fleet.mjs — the Netlify fleet instrument (draft-deploy functions).
//
// WHAT THIS IS: scale-out compute for I/O-bound fetch/search work, per the
// netlify-free-tier-maxxing reference (AGENTS.md). Each nfp_ key = an account
// with a 300-cr/mo pool; draft deploys are 0 credits; sync functions bill
// ~0.0006-0.167 cr per invocation. The function runs in AWS Lambda (cmh,
// US Ohio) = US-egress IP diversification, per-site distinct.
//
// SAFETY RULES (from the reference, empirically probed):
//   - NEVER --prod / production deploys (15 cr each). Draft only.
//   - One fleet site per key, namespaced js-fleet-<n> (keys are SHARED —
//     other automations run on the same accounts; never touch their sites).
//   - Sync functions: 30s hard kill on draft deploys — the function must
//     fetch fast (10s timeout) and stream small payloads back.
//
// LAYOUT (deployed via REST API draft deploys — deploy object + file PUTs):
//   /.netlify/functions/fleet.mjs — the generic worker (below)
//   /index.html                    — publish stub
//
// USAGE:
//   node scripts/s21_netlify_fleet.mjs keys          — list keys + account ids
//   node scripts/s21_netlify_fleet.mjs deploy <n>    — create/refresh site + deploy function for key n
//   node scripts/s21_netlify_fleet.mjs invoke <n> '{"op":"fetch","url":"..."}'  — POST the worker
//   node scripts/s21_netlify_fleet.mjs sweep         — health-check every key (accounts reachable?)
//
// The FLEET_KEYS file lives at ingest/.netlify_fleet_keys (git-is-disk policy,
// same rationale as ingest/.env).

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { createHash } from 'node:crypto'

const REPO = join(dirname(dirname(fileURLToPath(import.meta.url))), '')
const KEYS_FILE = join(REPO, 'ingest/.netlify_fleet_keys')
const API = 'https://api.netlify.com/api/v1'

// ---------- keys ----------
function loadKeys () {
  const out = []
  for (const line of readFileSync(KEYS_FILE, 'utf8').split('\n')) {
    const m = line.match(/^(nfp_\S+)\s+@?(\S*)/)
    if (m) out.push({ token: m[1], account: m[2] || '' })
  }
  return out
}

const api = async (key, path, opts = {}) => {
  const r = await fetch(API + path, {
    ...opts,
    headers: {
      Authorization: `Bearer ${key}`,
      'Content-Type': 'application/json',
      ...(opts.headers || {})
    }
  })
  const text = await r.text()
  let json = null
  try { json = JSON.parse(text) } catch { }
  return { status: r.status, json, text }
}

const sha1 = (s) => createHash('sha1').update(s).digest('hex')

// ---------- the worker function source (deployed as-is) ----------
const WORKER_SRC = `export default async (req) => {
  const started = Date.now()
  const cors = {
    'access-control-allow-origin': '*',
    'content-type': 'application/json'
  }
  if (req.method === 'OPTIONS') return new Response('', { status: 204, headers: cors })
  if (req.method === 'GET') {
    return Response.json({ ok: true, worker: 'js-fleet v1', ts: started }, { headers: cors })
  }
  let job = {}
  try { job = await req.json() } catch { return Response.json({ err: 'bad json' }, { status: 400, headers: cors }) }
  const out = { ok: true, ts: started, took_ms: 0 }
  try {
    if (job.op === 'fetch') {
      const ctl = new AbortController()
      const t = setTimeout(() => ctl.abort(), (job.timeout_s || 10) * 1000)
      const r = await fetch(job.url, {
        signal: ctl.signal,
        method: job.method || 'GET',
        headers: job.headers || { 'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36', accept: 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8', 'accept-language': 'en-US,en;q=0.9' },
        redirect: 'follow'
      })
      clearTimeout(t)
      const ab = new ArrayBuffer(await r.arrayBuffer())
      const bytes = ab.byteLength
      let text = ''
      try { text = new TextDecoder().decode(ab).slice(0, (job.max_bytes || 120000)) } catch { }
      out.status = r.status
      out.final_url = r.url
      out.bytes = bytes
      out.body = text
    } else if (job.op === 'search') {
      // DDG html SERP scrape, run from this function's own US IP
      const q = encodeURIComponent(job.query || '')
      const ctl = new AbortController()
      const t = setTimeout(() => ctl.abort(), 12000)
      const r = await fetch('https://html.duckduckgo.com/html/?q=' + q, {
        signal: ctl.signal,
        headers: { 'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36' }
      })
      clearTimeout(t)
      const html = await r.text()
      out.status = r.status
      const results = []
      const seen = new Set()
      for (const m of html.matchAll(/<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)<\\/a>/gs)) {
        let u = m[1].startsWith('//') ? 'https:' + m[1] : m[1]
        const um = u.match(/[?&]uddg=([^&]+)/)
        if (um) { try { u = decodeURIComponent(um[1]) } catch { } }
        const name = m[2].replace(/<[^>]+>/g, '').trim()
        if (!/^https?:\\/\\//.test(u) || seen.has(u)) continue
        seen.add(u)
        results.push({ name: name.slice(0, 140), url: u })
        if (results.length >= (job.num || 8)) break
      }
      out.results = results
    } else if (job.op === 'ip') {
      const r = await fetch('https://api.ipify.org')
      out.ip = await r.text()
    } else {
      return Response.json({ err: 'unknown op ' + job.op }, { status: 400, headers: cors })
    }
  } catch (e) {
    out.ok = false
    out.err = String(e).slice(0, 200)
  }
  out.took_ms = Date.now() - started
  return Response.json(out, { headers: cors })
}
export const config = { path: '/fleet' }
`

// ---------- deploy (CLI draft deploy — the validated 0-credit path) ----------
const WORKER_DIR = join(REPO, 'tools/fleet_worker')

async function deploy (n) {
  const keys = loadKeys()
  const k = keys[Number(n)]
  if (!k) throw new Error('no key index ' + n)
  const siteName = 'js-fleet-' + String(n).padStart(2, '0')

  // materialize the worker project (single source of truth = WORKER_SRC)
  mkdirSync(join(WORKER_DIR, 'netlify/functions'), { recursive: true })
  mkdirSync(join(WORKER_DIR, 'src'), { recursive: true })
  writeFileSync(join(WORKER_DIR, 'netlify/functions/fleet.mjs'), WORKER_SRC)
  writeFileSync(join(WORKER_DIR, 'src/index.html'), '<!doctype html><title>js-fleet</title>ok\n')
  writeFileSync(join(WORKER_DIR, 'netlify.toml'), '[build]\n  command = "echo fleet-worker"\n  publish = "src"\n  functions = "netlify/functions"\n\n[functions]\n  node_bundler = "esbuild"\n')

  // find-or-create our namespaced site
  let siteId = null
  const sites = (await api(k.token, '/sites?filter=all')).json || []
  const mine = sites.find(s => s.name === siteName)
  if (mine) { siteId = mine.id } else {
    const created = await api(k.token, '/sites', { method: 'POST', body: JSON.stringify({ name: siteName }) })
    siteId = created.json && created.json.id
    if (!siteId) throw new Error('site create failed: ' + created.text.slice(0, 200))
  }
  console.log('site:', siteName, siteId)

  // CLI draft deploy (0 credits; build+bundling on THIS machine; functions
  // register correctly — the REST-zip path needs a pre-created bundle id)
  const CLI = join(REPO, '../node_modules/.bin/netlify')
  const fs = await import('node:fs')
  const cli = fs.existsSync(CLI) ? CLI : 'netlify'
  const env = { ...process.env, NETLIFY_AUTH_TOKEN: k.token, NETLIFY_SITE_ID: siteId }
  const { spawnSync } = await import('node:child_process')
  const r = spawnSync(cli, ['deploy', '--draft', '--site', siteId, '--dir', 'src', '--functions', 'netlify/functions'], {
    cwd: WORKER_DIR, env, encoding: 'utf8', timeout: 240000
  })
  const out = (r.stdout || '') + (r.stderr || '')
  const depIdM = out.match(/https?:\/\/([0-9a-f]{8,})--[^ ]*netlify\.app/i)
  const depId = depIdM ? depIdM[1] : null
  console.log(out.split('\n').filter(l => /Draft deploy|Live draft|deploy|https|error|Error/i.test(l)).slice(-6).join('\n'))
  if (r.status !== 0 && !depId) throw new Error('CLI deploy failed (exit ' + r.status + ')')
  const record = { n: Number(n), site: siteName, siteId, deployId: depId, url: `https://${depId}--${siteName}.netlify.app/fleet`, ts: new Date().toISOString() }
  const REG = join(REPO, 'ingest/data/ats_seed/s21_fleet/sites.json')
  mkdirSync(dirname(REG), { recursive: true })
  const reg = existsSync(REG) ? JSON.parse(readFileSync(REG, 'utf8')) : {}
  reg[n] = record
  writeFileSync(REG, JSON.stringify(reg, null, 1))
  console.log('function URL:', record.url)
  console.log('registry ->', REG)
}

// ---------- invoke ----------
async function invoke (n, jobJson) {
  const keys = loadKeys()
  const k = keys[Number(n)]
  const REG = join(REPO, 'ingest/data/ats_seed/s21_fleet/sites.json')
  const reg = existsSync(REG) ? JSON.parse(readFileSync(REG, 'utf8')) : {}
  const rec = reg[n]
  if (!rec) throw new Error('no site record for key ' + n + ' — run deploy first')
  const url = process.env.FLEET_URL || `https://${rec.deployId}--${rec.site}.netlify.app/fleet`
  const r = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: jobJson })
  const text = await r.text()
  try { console.log(JSON.stringify(JSON.parse(text), null, 1).slice(0, 3000)) } catch { console.log(r.status, text.slice(0, 500)) }
}

// ---------- sweep ----------
async function sweep () {
  const keys = loadKeys()
  let ok = 0; let bad = 0
  for (const k of keys) {
    const r = await api(k.token, '/accounts')
    if (r.status === 200 && Array.isArray(r.json)) {
      ok++
      if (process.env.VERBOSE) console.log('OK  ', k.account, r.json.map(a => a.slug).join(','))
    } else { bad++; if (process.env.VERBOSE) console.log('BAD ', k.account, r.status) }
  }
  console.log(`keys: ${keys.length}, reachable: ${ok}, dead: ${bad}`)
}

const [cmd, a, b] = process.argv.slice(2)
if (cmd === 'keys') { loadKeys().forEach((k, i) => console.log(i, k.token.slice(0, 12) + '…', k.account)) }
else if (cmd === 'deploy') deploy(a).catch(e => { console.error('FAIL:', e.message); process.exit(1) })
else if (cmd === 'invoke') invoke(a, b || '{}').catch(e => { console.error('FAIL:', e.message); process.exit(1) })
else if (cmd === 'sweep') sweep().catch(e => { console.error('FAIL:', e.message); process.exit(1) })
else console.log('usage: keys | deploy <n> | invoke <n> <json> | sweep')
