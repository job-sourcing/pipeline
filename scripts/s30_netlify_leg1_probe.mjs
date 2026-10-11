// S30 / Task 3 — Netlify compute-as-build LEG-1 probe worker.
// Runs as the site's BUILD COMMAND inside a Netlify remote build (zip-build path).
//   - fetches ~20 board/API URLs (US-egress witness + real ATS endpoints)
//   - records status / final_url / bytes / sha256 / first 200 B (b64) — never parses
//   - writes results JSON to:
//       1. out/results-<ts>.json + out/latest.json  (published files = guaranteed
//          return path on the branch-deploy URL)
//       2. @netlify/blobs getStore('compute-results') key run/<ts>-leg1.json
//          (the plan §2.3 recommended return path; works if the build image
//          provides NETLIFY_BLOBS_CONTEXT to build commands)
//     Round-1 note: a hanging blobs write can wedge the whole build — so every
//     blobs call is time-boxed (25 s) and the hard watchdog stays at 165 s.
//   - round 2: vendored node_modules (no npm install), no plugin; diagnostics
//     mirrored into out/probe.log + out/leg.log (readable from the deploy URL).
//   - NEVER prints or persists token-ish env values; only whitelisted names.

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
const CONCURRENCY = 8;
const FETCH_TIMEOUT_MS = 12000;
const WATCHDOG_MS = 165000;

const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19); // 2026-10-11T03-04-05
const BLOBS_KEY = `run/${stamp}-leg1.json`;
const RESULT_FILE = `results-${stamp}.json`;

const dlog = (...a) => {
  const line = `[probe ${Math.round(process.uptime())}s] ${a.join(' ')}`;
  console.log(line);
  try { fs.appendFileSync('out/probe.log', line + '\n'); } catch {}
};

// ---- env diagnostics (names only; values only for a benign whitelist) ----
const SAFE_ENV_VALUES = new Set([
  'NETLIFY_BUILD_ID', 'NETLIFY_SITE_ID', 'NETLIFY_SITE_NAME', 'NODE_VERSION',
  'NETLIFY_REGION', 'NETLIFY_USE_YARN', 'NETLIFY_BLOBS_USE_ADVANCED_CACHE',
  'NETLIFY_IMAGES_CDN_DOMAIN', 'NETLIFY_PURGE_API_URL',
]);
const envNames = Object.keys(process.env).filter((k) => /NETLIFY|BLOB/i.test(k));
const envDiag = {};
for (const k of envNames) envDiag[k] = SAFE_ENV_VALUES.has(k) ? String(process.env[k]).slice(0, 120) : '<redacted-name-only>';

// ---- payload ----
const manifest = JSON.parse(fs.readFileSync('manifest.json', 'utf8'));
const urls = manifest.urls;

// ---- probe engine ----
async function probeOne(entry) {
  const t0 = Date.now();
  const rec = { label: entry.label, url: entry.url, tag: entry.tag };
  try {
    const res = await fetch(entry.url, {
      headers: {
        'user-agent': UA,
        accept: 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9',
      },
      redirect: 'follow',
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    const buf = Buffer.from(await res.arrayBuffer());
    rec.ok = res.ok;
    rec.status = res.status;
    rec.contentType = res.headers.get('content-type');
    rec.finalUrl = res.url;
    rec.bytes = buf.length;
    rec.sha256 = crypto.createHash('sha256').update(buf).digest('hex');
    rec.headB64 = buf.subarray(0, 200).toString('base64');
    if (entry.parse === 'ip') {
      try { rec.parsed = JSON.parse(buf.toString('utf8')); } catch { /* keep raw head only */ }
    }
  } catch (e) {
    rec.ok = false;
    rec.error = String(e && e.name && e.message ? `${e.name}: ${e.message}` : e).slice(0, 300);
  }
  rec.ms = Date.now() - t0;
  return rec;
}

async function pool(items, n, fn) {
  const out = [];
  let i = 0;
  const workers = Array.from({ length: n }, async () => {
    while (i < items.length) out[items.indexOf(items[i])] = await fn(items[i++]);
  });
  await Promise.all(workers);
  return out;
}

const results = {
  leg: 's30-netlify-leg1',
  startedAt: new Date().toISOString(),
  node: process.version,
  blobsKey: BLOBS_KEY,
  resultFile: RESULT_FILE,
  env: envDiag,
  meta: {},
  probes: [],
};

let finished = false;
async function finish(timedOut) {
  if (finished) return;
  finished = true;
  results.finishedAt = new Date().toISOString();
  results.timedOut = Boolean(timedOut);
  results.summary = {
    total: results.probes.length,
    ok: results.probes.filter((p) => p.ok).length,
    byStatus: results.probes.reduce((m, p) => { m[p.status || 'ERR'] = (m[p.status || 'ERR'] || 0) + 1; return m; }, {}),
  };
  const ipRec = results.probes.find((p) => p.tag === 'egress-witness');
  if (ipRec && ipRec.parsed && ipRec.parsed.ip) results.meta.egressIp = ipRec.parsed.ip;

  // (1) published files = guaranteed return path
  try { fs.mkdirSync('out', { recursive: true }); } catch {}
  fs.writeFileSync(path.join('out', RESULT_FILE), JSON.stringify(results));
  fs.writeFileSync(path.join('out', 'latest.json'), JSON.stringify({ key: BLOBS_KEY, file: RESULT_FILE, ts: results.finishedAt }));
  fs.writeFileSync(path.join('out', 'index.html'), `<!doctype html><title>s30 leg1</title><a href="${RESULT_FILE}">results</a>\n`);
  fs.writeFileSync('results-latest.json', JSON.stringify(results)); // for the onPostBuild plugin

  // (2) blobs via build-command context (plan §2.3 primary) — time-boxed!
  const withTimeout = (p, ms, what) => Promise.race([
    p,
    new Promise((_, rej) => { const t = setTimeout(() => rej(new Error(`timeout ${what} ${ms}ms`)), ms); t.unref?.(); }),
  ]);
  let blobs = { attempted: true, via: 'build-command getStore' };
  try {
    const { getStore } = await import('@netlify/blobs');
    const store = getStore('compute-results');
    await withTimeout(store.set(BLOBS_KEY, JSON.stringify(results)), 25000, 'blobs.set');
    const back = await withTimeout(store.get(BLOBS_KEY), 15000, 'blobs.get');
    blobs.ok = Boolean(back);
    blobs.key = BLOBS_KEY;
  } catch (e) {
    blobs.ok = false;
    blobs.error = String(e && e.message ? e.message : e).slice(0, 300);
  }
  results.blobs = blobs;
  if (blobs.ok) {
    try {
      const { getStore } = await import('@netlify/blobs');
      await withTimeout(getStore('compute-results').set(BLOBS_KEY, JSON.stringify(results)), 25000, 'blobs.re-set');
    } catch { /* best effort re-set with blobs info embedded */ }
  }
  fs.writeFileSync(path.join('out', RESULT_FILE), JSON.stringify(results));
  fs.writeFileSync('results-latest.json', JSON.stringify(results));
  dlog(`done: ${results.summary.ok}/${results.summary.total} ok, egress=${results.meta.egressIp || '?'} blobs=${blobs.ok ? 'OK' : 'FAIL: ' + (blobs.error || '?')}`);
}

const watchdog = setTimeout(() => finish(true).then(() => process.exit(0)), WATCHDOG_MS);
watchdog.unref?.();

dlog(`start: ${urls.length} urls, key=${BLOBS_KEY}, node=${process.version}`);
results.probes = await pool(urls, CONCURRENCY, probeOne);
dlog(`probes done: ${results.probes.filter((p) => p.ok).length}/${results.probes.length} ok`);
await finish(false);
process.exit(0);
