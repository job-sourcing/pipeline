#!/usr/bin/env bash
# S30 / Task 3 — LEG-1 round-2 build wrapper: full diagnostics, hard timeouts,
# NEVER returns non-zero (so the deploy always publishes out/ = our log channel).
# Round-1 lesson: a build that fails opaque ("exit code 2") is undebuggable
# because Netlify build logs are UI-only — so all diagnostics are mirrored into
# out/leg.log, readable from the deploy URL after the fact.
T0=$(date +%s)
log() { echo "[leg.sh $(($(date +%s)-T0))s] $*" | tee -a out/leg.log; }

log "start pwd=$(pwd) node=$(node -v 2>&1) npm=$(npm -v 2>&1) bash=$BASH_VERSION"
log "root files: $(ls -A | tr '\n' ' ')"
log "NETLIFY env var NAMES: $(env | sed -n 's/^\(NETLIFY[A-Z_]*\)=.*/\1/p' | sort | tr '\n' ' ')"
if [ -d node_modules/@netlify/blobs ]; then
  log "vendored node_modules OK: $(du -sh node_modules 2>/dev/null | cut -f1) $(node -e 'console.log(require("@netlify/blobs/package.json").version)' 2>&1)"
else
  log "vendored node_modules MISSING — npm install fallback (120s timeout)"
  timeout 120 npm install --no-audit --no-fund --no-progress 2>&1 | tail -4 | tee -a out/leg.log
  log "npm exit=${PIPESTATUS[0]}"
fi

log "running probe.mjs (170s timeout)"
timeout 170 node probe.mjs 2>&1 | tee -a out/probe.stdout.log
log "probe exit=${PIPESTATUS[0]}"
log "out/ files: $(ls -A out | tr '\n' ' ')"
log "done — wrapper exits 0"
exit 0
