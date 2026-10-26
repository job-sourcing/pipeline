#!/bin/bash
# run-first-sweeps.sh — the B4 catch-all's FIRST RUNS (S47).
# Sequential (the three runs share aggregator politeness budgets —
# never concurrent against the same hosts):
#   1. gen  × t2 (California statewide, CORE-30)   — the primary CA run
#   2. gen  × t1a (the 4 weekly CA metros, CORE-30, category=admin-jobs)
#   3. dmm  × t3 (US-wide refresh, the widened CORE-20)
# Each run writes the quartet + the ui bundle into the worktree; the
# chain logs verdicts; the artifacts commit AFTER review (the drift
# ritual — never auto-commit data into main).
set -uo pipefail
cd /home/z/work/wt-b4engine || exit 2
export $(grep -v '^#' ingest/.env | xargs) 2>/dev/null || true

run() {
  local name="$1"; shift
  echo "=== [$(date -u +%FT%TZ)] RUN: $name ==="
  if timeout 3600 python3 scripts/role_sweep.py "$@"; then
    echo "=== [$(date -u +%FT%TZ)] RUN OK: $name ==="
  else
    echo "=== [$(date -u +%FT%TZ)] RUN FAILED (rc=$?): $name ==="
  fi
}

run "gen t2 California"     --family gen --geo-ladder t2
run "gen t1a CA-metros"     --family gen --geo-ladder t1a
run "dmm t3 US-wide (widened)" --family dmm --geo-ladder t3
echo "=== [$(date -u +%FT%TZ)] CHAIN COMPLETE ==="
ls -la ingest/data/roles/ | grep -E "^-" | grep gen
ls -la ingest/data/ui/ | grep -E "roles-(gen|dmm)"
