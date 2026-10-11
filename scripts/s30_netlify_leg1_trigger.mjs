#!/usr/bin/env node
// S30 / Task 3 — Netlify LEG-1 driver: precheck → zip-build trigger → poll →
// context assert (0-credit guard) → blobs harvest → postcheck.  Run detached
// via scripts/_bg_launch.py.  NEVER prints the PAT (read from
// ingest/.netlify_fleet_keys, referenced by path only).  Writes:
//   /home/z/research/audit/s30_netlify_leg1_results.json  (harvested results)
//   /tmp/s30leg1/run_report.json                          (API observations)
import fs from 'node:fs';

const REPO = '/home/z/research';
const KEYFILE = `${REPO}/ingest/.netlify_fleet_keys`;
const REG = `${REPO}/ingest/data/ats_seed/s21_fleet/sites.json`;
const ZIP = '/tmp/s30leg1.zip';
const RESULTS_OUT = `${REPO}/audit/s30_netlify_leg1_results.json`;
const RUN_JSON = '/tmp/s30leg1/run_report.json';
const API = 'https://api.netlify.com/api/v1';

const key = fs.readFileSync(KEYFILE, 'utf8').split('\n').map((l) => l.trim())
  .filter((l) => l && !l.startsWith('#')).map((l) => l.split(/\s+/)[0])[0];
const reg = JSON.parse(fs.readFileSync(REG, 'utf8'));
const SITE = reg['0'];
const SITE_ID = SITE.siteId;
const AUTH = { Authorization: `Bearer ${key}` };
const log = (...a) => console.log(new Date().toISOString(), ...a);

const run = { site: { name: SITE.site, siteId: SITE_ID }, zipBytes: fs.statSync(ZIP).size, steps: {}, verdict: null };

async function j(url, opts = {}) {
  const res = await fetch(url, opts);
  const text = await res.text();
  let body;
  try { body = JSON.parse(text); } catch { body = text.slice(0, 400); }
  return { status: res.status, rl: res.headers.get('x-ratelimit-remaining'), body };
}

const deploySubset = (d) => d && ({
  id: d.id, state: d.state, context: d.context, deploy_source: d.deploy_source,
  build_id: d.build_id, created_at: d.created_at, deploy_time: d.deploy_time,
  deploy_ssl_url: d.deploy_ssl_url, ssl_url: d.ssl_url, name: d.name, branch: d.branch,
});

async function siteSnapshot(label) {
  const s = await j(`${API}/sites/${SITE_ID}`, { headers: AUTH });
  const b = s.body || {};
  const pick = {
    name: b.name, state: b.state, ssl_url: b.ssl_url, account_id: b.account_id,
    account_slug: b.account_slug, account_name: b.account_name,
    default_branch: b.default_branch, build_settings: b.build_settings,
    capabilities: b.capabilities, build_image: b.build_image,
    created_at: b.created_at, updated_at: b.updated_at, id: b.id,
  };
  const builds = await j(`${API}/sites/${SITE_ID}/builds`, { headers: AUTH });
  const deploys = await j(`${API}/sites/${SITE_ID}/deploys?per_page=10`, { headers: AUTH });
  const blobs = await j(`${API}/blobs/${SITE_ID}/site:compute-results`, { headers: AUTH });
  const out = {
    http: { site: s.status, builds: builds.status, deploys: deploys.status, blobs: blobs.status },
    site: pick,
    builds: Array.isArray(builds.body) ? builds.body.map((x) => ({ id: x.id, done: x.done, error: x.error, created_at: x.created_at })) : builds.body,
    deploys: Array.isArray(deploys.body) ? deploys.body.map(deploySubset) : deploys.body,
    blobs: blobs.body,
  };
  run.steps[label] = out;
  log(`[${label}] site=${s.status} builds=${Array.isArray(builds.body) ? builds.body.length : '?'} deploys=${out.deploys.length || '?'} blobs=${blobs.status}`);
  return out;
}

async function accountSnapshot(label, accountId) {
  if (!accountId) { run.steps[label] = { skipped: 'no account_id' }; return; }
  const acct = await j(`${API}/accounts/${accountId}`, { headers: AUTH });
  const audit = await j(`${API}/accounts/${accountId}/audit`, { headers: AUTH });
  const out = {
    http: { account: acct.status, audit: audit.status },
    account: acct.body && typeof acct.body === 'object' ? {
      id: acct.body.id, name: acct.body.name, slug: acct.body.slug, type: acct.body.type,
      capabilities: acct.body.capabilities, billing: acct.body.billing,
      teams_allowed: acct.body.teams_allowed, extra_seats: acct.body.extra_seats,
    } : acct.body,
    audit: Array.isArray(audit.body) ? audit.body.slice(0, 6).map((e) => ({ type: e.type, created_at: e.created_at, account_id: e.account_id })) : audit.body,
    auditCount: Array.isArray(audit.body) ? audit.body.length : null,
  };
  run.steps[label] = out;
  log(`[${label}] account=${acct.status} audit=${audit.status}${Array.isArray(audit.body) ? ` (${audit.body.length} events, newest=${audit.body[0] && audit.body[0].type})` : ''}`);
  return out;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- 1. prechecks ----------
log('LEG-1 driver start; site js-fleet-00; zip', run.zipBytes, 'bytes');
const before = await siteSnapshot('before');
const accountId = before.site.account_id;
await accountSnapshot('before_account', accountId);
if (before.http.site !== 200) { run.verdict = 'BROKEN'; log('precheck failed'); fs.writeFileSync(RUN_JSON, JSON.stringify(run, null, 1)); process.exit(1); }

let branch = 'probe';
const mainBranch = before.site.default_branch || (before.site.build_settings && before.site.build_settings.repo_branch) || null;
if (mainBranch === branch) branch = 'probe2';
run.branch = branch;
run.mainBranch = mainBranch;
log(`branch=${branch} (site default=${mainBranch ?? 'none'})`);

// ---------- 2. trigger ----------
let trig;
try {
  const zipBuf = fs.readFileSync(ZIP);
  trig = await j(`${API}/sites/${SITE_ID}/builds?branch=${branch}&title=s30-leg1`, {
    method: 'POST', headers: { ...AUTH, 'Content-Type': 'application/zip' }, body: zipBuf,
  });
  run.trigger = { mode: 'raw-zip', status: trig.status, body: trig.body && typeof trig.body === 'object' ? { id: trig.body.id, deploy_id: trig.body.deploy_id, done: trig.body.done, error: trig.body.error, sha: trig.body.sha, created_at: trig.body.created_at } : trig.body };
  log('trigger(raw-zip) →', trig.status, JSON.stringify(run.trigger.body));
} catch (e) {
  trig = { status: 0, body: String(e) };
  run.trigger = { mode: 'raw-zip', status: 0, error: String(e).slice(0, 300) };
}

if (trig.status >= 400 || trig.status === 0) {
  log('raw-zip failed → trying multipart/form-data variant');
  const zipBuf = fs.readFileSync(ZIP);
  const fd = new FormData();
  fd.append('zip', new Blob([zipBuf], { type: 'application/zip' }), 'site.zip');
  trig = await j(`${API}/sites/${SITE_ID}/builds?branch=${branch}&title=s30-leg1`, {
    method: 'POST', headers: { ...AUTH }, body: fd,
  });
  run.triggerFallback = { mode: 'multipart', status: trig.status, body: trig.body && typeof trig.body === 'object' ? { id: trig.body.id, deploy_id: trig.body.deploy_id, done: trig.body.done, error: trig.body.error, created_at: trig.body.created_at } : trig.body };
  log('trigger(multipart) →', trig.status, JSON.stringify(run.triggerFallback.body));
}

const BUILD_ID = trig.body && trig.body.id;
if (!BUILD_ID || trig.status >= 400) {
  run.verdict = 'BROKEN';
  run.triggerError = trig.body;
  log('TRIGGER FAILED — no build id. Aborting (no deploy to babysit).');
  const after = await siteSnapshot('after').catch(() => null);
  fs.writeFileSync(RUN_JSON, JSON.stringify(run, null, 1));
  process.exit(1);
}
const t0 = Date.now();
const triggerIso = new Date().toISOString();

// ---------- 3. poll ----------
let deploy = null; let buildState = null;
for (let i = 0; i < 78; i++) {
  await sleep(10000);
  const b = await j(`${API}/sites/${SITE_ID}/builds?per_page=5`, { headers: AUTH });
  const mine = Array.isArray(b.body) ? b.body.find((x) => x.id === BUILD_ID) : null;
  buildState = mine ? { id: mine.id, done: mine.done, error: mine.error, deploy_id: mine.deploy_id, created_at: mine.created_at } : null;
  const d = await j(`${API}/sites/${SITE_ID}/deploys?per_page=6`, { headers: AUTH });
  const news = Array.isArray(d.body) ? d.body.filter((x) => (x.build_id === BUILD_ID) || (x.created_at && x.created_at >= triggerIso && x.id !== 'x')) : [];
  deploy = news[0] ? deploySubset(news[0]) : null;
  log(`poll#${i} build=${buildState ? `${buildState.done ? 'done' : 'running'}${buildState.error ? ` err=${String(buildState.error).slice(0, 80)}` : ''}` : '?'} deploy=${deploy ? `${deploy.id} ${deploy.state} ctx=${deploy.context}` : 'none'} elapsed=${Math.round((Date.now() - t0) / 1000)}s`);
  const deployDone = deploy && (deploy.state === 'ready' || deploy.state === 'error');
  const buildDone = buildState && (buildState.done === true || buildState.done === false && buildState.error);
  if (deployDone || (buildDone && i > 3)) {
    if (deployDone) break;
    if (buildDone && !deploy) { await sleep(8000); break; }
  }
  if (Date.now() - t0 > 780000) { log('poll timeout 10min'); break; }
}
run.build = buildState;
run.deploy = deploy;

// ---------- 4. context assert (the 0-credit guard) ----------
if (deploy && deploy.context === 'production') {
  run.alert = 'PRODUCTION CONTEXT — 15-credit deploy! Cancelling + stopping.';
  log('!!! PRODUCTION CONTEXT DETECTED — attempting cancel');
  const cancel = await j(`${API}/deploys/${deploy.id}/cancel`, { method: 'POST', headers: AUTH });
  run.cancelAttempt = { status: cancel.status, body: cancel.body && cancel.body.state ? { state: cancel.body.state } : cancel.body };
  const after = await siteSnapshot('after').catch(() => null);
  await accountSnapshot('after_account', accountId).catch(() => {});
  run.verdict = 'RESTRICTED';
  fs.writeFileSync(RUN_JSON, JSON.stringify(run, null, 1));
  process.exit(2);
}
if (!deploy || deploy.state !== 'ready') {
  log('deploy not ready — recording and continuing to postcheck');
  run.deployNotReady = true;
}

// ---------- 5. harvest ----------
const harvest = { blobs: null, file: null, source: null };
if (deploy && deploy.state === 'ready') {
  const stores = ['compute-results', 's30-leg1-results', 'plugin:s30-leg1-results'];
  for (const st of stores) {
    const l = await j(`${API}/blobs/${SITE_ID}/site:${st}`, { headers: AUTH });
    harvest[`store_${st}`] = { status: l.status, body: l.body };
    const keys = l.body && Array.isArray(l.body.blobs) ? l.body.blobs.map((b) => b.key) : [];
    if (keys.length) {
      log(`store site:${st} has ${keys.length} keys:`, keys.join(', '));
      for (const k of keys.slice(0, 3)) {
        const v = await fetch(`${API}/blobs/${SITE_ID}/site:${st}/${encodeURIComponent(k)}`, { headers: AUTH });
        const txt = await v.text();
        if (v.status === 200 && txt.length > 100) {
          harvest.blobs = { store: `site:${st}`, key: k, status: v.status, bytes: txt.length };
          if (!harvest.raw) harvest.raw = txt;
          log(`blob read ${k} → ${v.status}, ${txt.length} bytes`);
        }
      }
    }
  }
  if (deploy.deploy_ssl_url) {
    for (const f of ['latest.json', 'index.html']) {
      try {
        const r = await fetch(`${deploy.deploy_ssl_url}/${f}`, { signal: AbortSignal.timeout(20000) });
        const t = await r.text();
        harvest[`file_${f}`] = { status: r.status, bytes: t.length, head: t.slice(0, 300) };
        if (r.status === 200 && f === 'latest.json' && !harvest.raw) {
          const pointer = JSON.parse(t);
          if (pointer.file) {
            const r2 = await fetch(`${deploy.deploy_ssl_url}/${pointer.file}`, { signal: AbortSignal.timeout(30000) });
            const t2 = await r2.text();
            harvest.file = { url: `${deploy.deploy_ssl_url}/${pointer.file}`, status: r2.status, bytes: t2.length };
            if (r2.status === 200 && !harvest.raw) harvest.raw = t2;
          }
        }
      } catch (e) { harvest[`file_${f}`] = { error: String(e).slice(0, 200) }; }
    }
  }
}
run.harvest = harvest;
const primary = harvest.raw || null;
if (primary) {
  fs.writeFileSync(RESULTS_OUT, primary);
  harvest.source = harvest.blobs ? `blobs site:${harvest.blobs.store} key ${harvest.blobs.key}` : (harvest.file ? `deploy file ${harvest.file.url}` : 'unknown');
  log('harvested', primary.length, 'bytes via', harvest.source, '→', RESULTS_OUT);
} else {
  log('HARVEST FAILED — no blobs, no deploy file');
}

// ---------- 6. postchecks ----------
await siteSnapshot('after');
await accountSnapshot('after_account', accountId);
if (run.steps.after && run.steps.before) {
  const beforeIds = new Set(run.steps.before.deploys.map((d) => d.id));
  run.newDeploys = (run.steps.after.deploys || []).filter((d) => !beforeIds.has(d.id));
  log('new deploys:', JSON.stringify(run.newDeploys));
}
run.verdict = primary ? 'WORKS' : (deploy && deploy.state === 'ready' ? 'WORKS (build ok, return-path failed)' : (deploy ? 'RESTRICTED' : 'BROKEN'));
fs.writeFileSync(RUN_JSON, JSON.stringify(run, null, 1));
log('driver done; verdict =', run.verdict);
process.exit(0);
