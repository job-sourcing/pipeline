#!/usr/bin/env node
// S30 / Task 3 — LEG-1 follow-up: keep polling the in-flight build, then harvest.
// Used because the first driver run hit its own 10-min poll timeout while the
// Netlify build (branch-deploy 6acaccba…) was still running.  Reads key from
// ingest/.netlify_fleet_keys (never prints it).  Updates /tmp/s30leg1/run_report.json
// and writes /home/z/research/audit/s30_netlify_leg1_results.json on success.
import fs from 'node:fs';

const REPO = '/home/z/research';
const KEYFILE = `${REPO}/ingest/.netlify_fleet_keys`;
const SITE_ID = JSON.parse(fs.readFileSync(`${REPO}/ingest/data/ats_seed/s21_fleet/sites.json`, 'utf8'))['0'].siteId;
const BUILD_ID = process.argv[2];
const DEPLOY_ID = process.argv[3];
const RESULTS_OUT = `${REPO}/audit/s30_netlify_leg1_results.json`;
const RUN_JSON = '/tmp/s30leg1/run_report.json';
const API = 'https://api.netlify.com/api/v1';
const key = fs.readFileSync(KEYFILE, 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#')).map((l) => l.split(/\s+/)[0])[0];
const AUTH = { Authorization: `Bearer ${key}` };
const log = (...a) => console.log(new Date().toISOString(), ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const run = JSON.parse(fs.readFileSync(RUN_JSON, 'utf8'));

async function j(url, opts = {}) {
  const res = await fetch(url, opts);
  const text = await res.text();
  let body; try { body = JSON.parse(text); } catch { body = text.slice(0, 400); }
  return { status: res.status, body };
}

let deploy = null;
for (let i = 0; i < 45; i++) {
  const b = await j(`${API}/builds/${BUILD_ID}`, { headers: AUTH });
  const mine = b.body || {};
  const d = await j(`${API}/deploys/${DEPLOY_ID}`, { headers: AUTH });
  deploy = d.body || {};
  log(`follow#${i} build.done=${mine.done} build.error=${mine.error || 'null'} deploy=${deploy.state} ctx=${deploy.context} dtime=${deploy.deploy_time}`);
  if (deploy.state === 'ready' || deploy.state === 'error' || mine.done) break;
  await sleep(10000);
}
run.deployFinal = deploy && {
  id: deploy.id, state: deploy.state, context: deploy.context, deploy_source: deploy.deploy_source,
  build_id: deploy.build_id, deploy_time: deploy.deploy_time, created_at: deploy.created_at,
  deploy_ssl_url: deploy.deploy_ssl_url, ssl_url: deploy.ssl_url, branch: deploy.branch,
  required: deploy.required, required_functions: deploy.required_functions,
};

const harvest = {};
if (deploy && deploy.state === 'ready') {
  for (const st of ['compute-results', 's30-leg1-results', 'plugin:s30-leg1-results']) {
    const l = await j(`${API}/blobs/${SITE_ID}/site:${st}`, { headers: AUTH });
    const keys = l.body && Array.isArray(l.body.blobs) ? l.body.blobs.map((x) => x.key) : [];
    harvest[`store_${st}`] = { status: l.status, keys };
    if (keys.length) {
      log(`store site:${st}:`, keys.join(', '));
      for (const k of keys.slice(0, 3)) {
        const v = await fetch(`${API}/blobs/${SITE_ID}/site:${st}/${encodeURIComponent(k)}`, { headers: AUTH });
        const txt = await v.text();
        if (v.status === 200 && txt.length > 100) {
          harvest.blobs = { store: `site:${st}`, key: k, bytes: txt.length };
          if (!harvest.raw) harvest.raw = txt;
        }
      }
    }
  }
  if (deploy.deploy_ssl_url && !harvest.raw) {
    try {
      const p = await (await fetch(`${deploy.deploy_ssl_url}/latest.json`, { signal: AbortSignal.timeout(20000) })).text();
      const ptr = JSON.parse(p);
      const r2 = await fetch(`${deploy.deploy_ssl_url}/${ptr.file}`, { signal: AbortSignal.timeout(30000) });
      const t2 = await r2.text();
      harvest.file = { status: r2.status, bytes: t2.length, url: `${deploy.deploy_ssl_url}/${ptr.file}` };
      if (r2.status === 200) harvest.raw = t2;
    } catch (e) { harvest.file = { error: String(e).slice(0, 200) }; }
  }
}
const raw = harvest.raw;
delete harvest.raw;
run.harvest = Object.assign(run.harvest || {}, harvest);
if (raw) {
  fs.writeFileSync(RESULTS_OUT, raw);
  run.verdict = 'WORKS';
  log('harvested', raw.length, 'bytes via', harvest.blobs ? `blobs ${harvest.blobs.store}/${harvest.blobs.key}` : `file ${harvest.file && harvest.file.url}`);
} else if (deploy && deploy.state === 'ready') {
  run.verdict = 'WORKS (build ok, return path failed)';
} else {
  run.verdict = deploy && deploy.state === 'error' ? 'BROKEN (deploy error)' : 'RESTRICTED (build never finished)';
}
fs.writeFileSync(RUN_JSON, JSON.stringify(run, null, 1));
log('followup done; verdict =', run.verdict);
