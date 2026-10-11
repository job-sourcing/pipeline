// s30 netlify leg1 probe worker — CommonJS, ZERO npm deps (Netlify build image
// preinstalls Node 20: global fetch, AbortController). Reads manifest.json,
// fetches 20 URLs, records status + first ~200 chars, prints ONE line per
// event (never one huge stdout write — Netlify log ingest chokes on MB writes),
// writes results to /tmp/leg1-results/ AND copies results.json into the
// publish dir (out/) so it lands on the deploy. 600s self-kill writes partials.
'use strict';
const fs = require('fs');
const path = require('path');

const START = Date.now();
const SELF_KILL_MS = 600 * 1000; // 10 min hard stop (15-min build cap headroom)
const FETCH_TIMEOUT_MS = 12 * 1000;
const CONCURRENCY = 5;
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';

const manifestPath = path.join(__dirname, 'manifest.json');
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const targets = manifest.targets;

function log(line) {
  // single short line per event; flush immediately
  process.stdout.write(String(line).replace(/[\r\n]+/g, ' ').slice(0, 400) + '\n');
}

function snippet(body) {
  if (!body) return '';
  const s = String(body).replace(/\s+/g, ' ').trim();
  return s.slice(0, 200);
}

const results = [];
let finished = false;

function writeResults(reason) {
  if (finished) return;
  finished = true;
  const payload = {
    run: 's30-netlify-leg1',
    reason,
    started_at: new Date(START).toISOString(),
    finished_at: new Date().toISOString(),
    wall_ms: Date.now() - START,
    env: {
      node: process.version,
      site_name: process.env.SITE_NAME || null,
      deploy_id: process.env.DEPLOY_ID || null,
      build_id: process.env.BUILD_ID || null,
      branch: process.env.BRANCH || null,
      context: process.env.CONTEXT || null,
      region: process.env.NETLIFY_REGION || null,
    },
    counts: {
      total: targets.length,
      attempted: results.length,
      ok200: results.filter((r) => r.status === 200).length,
      errors: results.filter((r) => r.error).length,
    },
    results,
  };
  const json = JSON.stringify(payload, null, 1);
  try { fs.mkdirSync('/tmp/leg1-results', { recursive: true }); } catch (e) {}
  try { fs.writeFileSync('/tmp/leg1-results/results.json', json); } catch (e) { log('[warn] /tmp write failed: ' + e.message); }
  try {
    fs.mkdirSync(path.join(__dirname, 'out'), { recursive: true });
    fs.writeFileSync(path.join(__dirname, 'out', 'results.json'), json);
    log('[out] wrote out/results.json bytes=' + json.length);
  } catch (e) {
    log('[warn] out write failed: ' + e.message);
  }
}

const killTimer = setTimeout(() => {
  log('[selfkill] 600s budget reached — writing partial results (' + results.length + '/' + targets.length + ')');
  writeResults('selfkill-600s');
  process.exit(0);
}, SELF_KILL_MS);
killTimer.unref();

async function fetchOne(t, idx) {
  const t0 = Date.now();
  const rec = { i: idx + 1, label: t.label, url: t.url, status: null, ok: false, final_url: null, bytes: null, content_type: null, ms: null, snippet: '', error: null };
  const ac = new AbortController();
  const to = setTimeout(() => ac.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(t.url, {
      method: 'GET',
      redirect: 'follow',
      signal: ac.signal,
      headers: {
        'user-agent': UA,
        accept: 'text/html,application/json;q=0.9,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9',
      },
    });
    rec.status = res.status;
    rec.ok = res.ok;
    rec.final_url = res.url;
    rec.content_type = res.headers.get('content-type');
    const len = res.headers.get('content-length');
    let text = '';
    if (t.body !== false) {
      text = await res.text();
    }
    rec.bytes = len ? Number(len) : (text ? Buffer.byteLength(text) : 0);
    rec.snippet = snippet(text);
  } catch (e) {
    rec.error = String(e && e.name === 'AbortError' ? 'timeout-' + FETCH_TIMEOUT_MS + 'ms' : (e.message || e)).slice(0, 160);
  } finally {
    clearTimeout(to);
  }
  rec.ms = Date.now() - t0;
  log('[' + rec.i + '/' + targets.length + '] ' + rec.label + ' status=' + rec.status + ' bytes=' + rec.bytes + ' ms=' + rec.ms + (rec.error ? ' err=' + rec.error : ''));
  return rec;
}

(async () => {
  log('[start] s30-netlify-leg1 node=' + process.version + ' targets=' + targets.length + ' concurrency=' + CONCURRENCY);
  // egress witness FIRST, alone, so its line is unambiguous
  const witness = await fetchOne(targets[0], 0);
  results.push(witness);
  log('[witness] ' + witness.snippet);
  const rest = targets.slice(1);
  let cursor = 0;
  const workers = Array.from({ length: Math.min(CONCURRENCY, rest.length) }, async () => {
    while (true) {
      const i = cursor++;
      if (i >= rest.length) break;
      const rec = await fetchOne(rest[i], i + 1);
      results.push(rec);
    }
  });
  await Promise.all(workers);
  results.sort((a, b) => a.i - b.i);
  writeResults('complete');
  log('[done] attempted=' + results.length + ' ok200=' + results.filter((r) => r.status === 200).length + ' wall_ms=' + (Date.now() - START));
  process.exit(0);
})().catch((e) => {
  log('[fatal] ' + String(e && e.message || e).slice(0, 200));
  writeResults('fatal');
  process.exit(0);
});
