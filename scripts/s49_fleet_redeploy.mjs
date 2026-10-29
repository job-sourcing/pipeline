#!/usr/bin/env node
// s49_fleet_redeploy.mjs — REST draft-deploy of the js-fleet workers
// (the S49 fix: the deployed workers predate the body-returning fetch op —
// bytes:0/empty-body on every fetch. The S21 deploy() path needs the
// netlify CLI (absent here), so this re-implements the SAME draft deploy
// via the public REST API: deploy object → required-file PUTs → poll.
// Draft deploys are 0 credits (context=deploy-preview, S30 P5).
//
// Usage:
//   node scripts/s49_fleet_redeploy.mjs 1        # redeploy worker 1
//   node scripts/s49_fleet_redeploy.mjs 0 1 2 3 4 5   # the whole fleet
//   node scripts/s49_fleet_redeploy.mjs 1 --test  # then live-test op:fetch + op:ip
//
// The worker source is imported from scripts/s21_netlify_fleet.mjs
// (WORKER_SRC — single source of truth, never duplicated here).
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createHash } from 'node:crypto'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..')
const KEYS_FILE = join(REPO, 'ingest/.netlify_fleet_keys')
const REG = join(REPO, 'ingest/data/ats_seed/s21_fleet/sites.json')

// pull WORKER_SRC from the fleet instrument (module top-level code is
// guarded by argv checks there? no — s21 script runs main on import; so
// we READ the source text and eval-extract instead of importing it).
const s21src = readFileSync(join(REPO, 'scripts/s21_netlify_fleet.mjs'), 'utf8')
const m = s21src.match(/const WORKER_SRC = `([\s\S]*?)`\n/)
if (!m) { console.error('WORKER_SRC not found in s21_netlify_fleet.mjs'); process.exit(2) }
const WORKER_SRC = m[1]

function loadKeys () {
  // same contract as s21_netlify_fleet.mjs: "nfp_<token> @account" lines
  const out = []
  for (const line of readFileSync(KEYS_FILE, 'utf8').split('\n')) {
    const m = line.match(/^(nfp_\S+)\s+@?(\S*)/)
    if (m) out.push(m[1])
  }
  return out
}

async function api (token, path, opts = {}) {
  const r = await fetch('https://api.netlify.com/api/v1' + path, {
    method: opts.method || 'GET',
    headers: { Authorization: `Bearer ${token}`, ...(opts.body ? { 'Content-Type': 'application/json' } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined
  })
  const text = await r.text()
  let json = null
  try { json = JSON.parse(text) } catch { }
  return { status: r.status, json, text }
}

const sha1 = s => createHash('sha1').update(Buffer.from(s, 'utf8')).digest('hex')

async function deployOne (n, keys) {
  const token = keys[n]
  const siteName = 'js-fleet-' + String(n).padStart(2, '0')
  // find-or-create the namespaced site
  const sites = (await api(token, '/sites?filter=all')).json || []
  let site = sites.find(s => s.name === siteName)
  if (!site) {
    const created = await api(token, '/sites', { method: 'POST', body: { name: siteName } })
    site = created.json
    if (!site) throw new Error(`site create failed: ${created.text.slice(0, 160)}`)
  }
  const siteId = site.id
  console.log(`[${siteName}] site ${siteId}`)

  const FILES = {
    'netlify/functions/fleet.mjs': WORKER_SRC,
    'src/index.html': '<!doctype html><title>js-fleet</title>ok\n',
    'netlify.toml': '[build]\n  command = "echo fleet-worker"\n  publish = "src"\n  functions = "netlify/functions"\n\n[functions]\n  node_bundler = "esbuild"\n'
  }
  const filesMap = {}
  for (const [p, c] of Object.entries(FILES)) filesMap[p] = sha1(c)

  // draft deploy (0 credits)
  const dep = await api(token, `/sites/${siteId}/deploys`,
    { method: 'POST', body: { files: filesMap, draft: true, async: true } })
  if (dep.status >= 400 || !dep.json) throw new Error(`deploy create failed: ${dep.status} ${dep.text.slice(0, 160)}`)
  const deployId = dep.json.id
  console.log(`[${siteName}] deploy ${deployId} created (draft)`)

  // upload required files
  const required = new Set(dep.json.required || Object.values(filesMap))
  for (const [p, content] of Object.entries(FILES)) {
    if (!required.has(filesMap[p])) continue
    const put = await fetch(
      `https://api.netlify.com/api/v1/deploys/${deployId}/files/${encodeURIComponent(p)}`,
      { method: 'PUT', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/octet-stream' }, body: Buffer.from(content, 'utf8') })
    if (put.status >= 400) throw new Error(`file PUT ${p} failed: ${put.status} ${(await put.text()).slice(0, 160)}`)
  }
  console.log(`[${siteName}] files uploaded`)

  // poll until ready (draft deploys process fast; cap at ~120s)
  let state = 'processing'
  for (let i = 0; i < 40 && state !== 'ready'; i++) {
    await new Promise(r => setTimeout(r, 3000))
    const d = await api(token, `/deploys/${deployId}`)
    state = d.json && d.json.state
    if (d.json && d.json.state === 'error') throw new Error('deploy errored: ' + JSON.stringify(d.json.error || {}).slice(0, 200))
  }
  console.log(`[${siteName}] state=${state}`)
  if (state !== 'ready') throw new Error('deploy did not reach ready state')

  const record = { n, site: siteName, siteId, deployId, url: `https://${deployId}--${siteName}.netlify.app/fleet`, ts: new Date().toISOString() }
  const reg = existsSync(REG) ? JSON.parse(readFileSync(REG, 'utf8')) : {}
  reg[n] = record
  mkdirSync(dirname(REG), { recursive: true })
  writeFileSync(REG, JSON.stringify(reg, null, 1))
  console.log(`[${siteName}] function URL: ${record.url}`)
  return record
}

async function testOne (rec, token) {
  // the functions path on a DRAFT deploy: /fleet (v2 config.path), served
  // under the deploy-preview host. Live-verify op:ip + a real op:fetch.
  const rIp = await fetch(rec.url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ op: 'ip' }) })
  const jIp = await rIp.json()
  const rFetch = await fetch(rec.url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ op: 'fetch', url: 'https://example.com', timeout_s: 15, max_bytes: 200000 }) })
  const jF = await rFetch.json()
  console.log(`[${rec.site}] ip=${jIp.ip} fetch status=${jF.status} bytes=${jF.bytes} body_len=${(jF.body || '').length}`)
  return (jF.body || '').length > 0 && jF.status === 200
}

const args = process.argv.slice(2).filter(a => a !== '--test')
const doTest = process.argv.includes('--test')
const keys = loadKeys()
const reg = existsSync(REG) ? JSON.parse(readFileSync(REG, 'utf8')) : {}
let allOk = true
for (const a of args) {
  const n = Number(a)
  if (Number.isNaN(n) || !keys[n]) { console.error(`bad key index: ${a}`); process.exit(2) }
  try {
    const rec = await deployOne(n, keys)
    if (doTest) { const ok = await testOne(rec, keys[n]); allOk = allOk && ok }
  } catch (e) { console.error(`FAIL [${a}]: ${e.message}`); allOk = false }
}
if (doTest) { console.log(allOk ? 'ALL TESTS PASS (bodies return)' : 'TESTS FAILED (still empty bodies?)'); process.exit(allOk ? 0 : 1) }
