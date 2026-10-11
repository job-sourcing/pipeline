// S30 / Task 3 — Netlify LEG-1 local build plugin (onPostBuild safety net).
// Runs AFTER the build command (probe.mjs). Reads results-latest.json written
// by the build command and pushes it to Netlify Blobs two ways:
//   1. utils.blobs (plugin API — present when the build system provides blobs
//      to plugins; store scope is plugin-managed)
//   2. explicit @netlify/blobs getStore('compute-results') using the plugin
//      process env (NETLIFY_BLOBS_CONTEXT), writing the SAME key the build
//      command uses, so the REST harvest path is uniform.
// Never touches tokens; errors are recorded into the published results file.

const fs = require('node:fs');

async function onPostBuild({ utils, constants, netlifyConfig }) {
  const note = { plugin: 's30-leg1-results', at: new Date().toISOString() };
  let results = null;
  try {
    results = JSON.parse(fs.readFileSync('results-latest.json', 'utf8'));
  } catch (e) {
    console.log('[s30-plugin] no results-latest.json — nothing to push', String(e).slice(0, 120));
    return;
  }
  const key = results.blobsKey;

  // path 1: utils.blobs
  try {
    if (utils && utils.blobs && typeof utils.blobs.set === 'function') {
      await utils.blobs.set(key, JSON.stringify(results));
      note.utilsBlobs = 'ok';
    } else {
      note.utilsBlobs = 'unavailable (utils.blobs missing)';
    }
  } catch (e) {
    note.utilsBlobs = 'error: ' + String(e && e.message ? e.message : e).slice(0, 200);
  }

  // path 2: explicit getStore from plugin env
  try {
    const { getStore } = await import('@netlify/blobs');
    const store = getStore('compute-results');
    await store.set(key, JSON.stringify(results));
    const back = await store.get(key);
    note.pluginGetStore = back ? 'ok' : 'set returned no readback';
  } catch (e) {
    note.pluginGetStore = 'error: ' + String(e && e.message ? e.message : e).slice(0, 200);
  }

  results.pluginBlobs = note;
  try {
    fs.writeFileSync('results-latest.json', JSON.stringify(results));
    if (results.resultFile) fs.writeFileSync(`out/${results.resultFile}`, JSON.stringify(results));
  } catch { /* best effort */ }
  console.log('[s30-plugin] blobs push:', JSON.stringify(note));
}

module.exports = { onPostBuild };
