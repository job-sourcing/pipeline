#!/bin/bash
# build_public_mirror.sh — sync the private job-sourcing-research tree to the
# PUBLIC org repo job-sourcing/pipeline (fresh history, secrets NEVER enter).
#
# Excluded (secrets / internal-only / stale):
#   ingest/.env                     — the secret store (materialized on GHA from INGEST_ENV secret)
#   research_data/, validation_results/ — scraped third-party HTML (foreign tokens inside)
#   audit/, .agents/                — internal research/ops docs
#   PLAN.md HANDOFF.md worklog.md DECISIONS.md CONSOLIDATION.md job_sourcing_methodology.md
#   .github/workflows/ats-drain.yml ats-backfill.yml — surprise chains (port later, deliberately)
#   *.har, tool-results/, ingest/data/workday_v1_archive/
# Redaction: <local>@priv.email → redacted@priv.email in all text files.
#
# Idempotent: re-run to refresh the mirror and push (commit only if changed).
set -euo pipefail

SRC=/home/z/my-project/job-sourcing-research
DEST=/home/z/pipeline-mirror
PAT="${GITHUB_PAT:?GITHUB_PAT env var required}"
REMOTE="https://${PAT}@github.com/job-sourcing/pipeline.git"

mkdir -p "$DEST"

echo "== pull latest runtime state from the org repo (GHA checkpoint commits) =="
cd "$DEST"
if [ ! -d .git ]; then
  git init -b main
  git remote add origin "$REMOTE"
fi
git config user.name "mirror-sync-bot"
git config user.email "mirror-sync@users.noreply.github.com"
git fetch origin || true
if git rev-parse --verify origin/main >/dev/null 2>&1; then
  git reset --hard origin/main   # take the org repo's state (GHA checkpoints)
fi

echo "== back-sync live watch state into the private archive =="
rsync -a --delete "$DEST/ingest/data/board_watch/" "$SRC/ingest/data/board_watch/"
(cd "$SRC" && git add ingest/data/board_watch 2>/dev/null && \
  git diff --cached --quiet || git commit -q -m "watch state back-sync from org runtime")

echo "== back-sync GHA-produced H-1B LCA extracts into the private archive =="
# The h1b-extract workflow commits {label}.h1b_lca.jsonl on the ORG repo
# (US egress downloads the DOL xlsx; the HK sandbox + Netlify are
# Akamai-blocked, the supabase proxy truncates at ~10.5MB). Pull ONLY
# the extract files back — the rest of ingest/data/workday flows
# archive → mirror (the rsync --delete below would otherwise WIPE the
# extract from the mirror since the archive doesn't have it yet).
mkdir -p "$SRC/ingest/data/workday"
if ls "$DEST"/ingest/data/workday/*.h1b_lca.jsonl >/dev/null 2>&1; then
  rsync -a "$DEST"/ingest/data/workday/*.h1b_lca.jsonl "$SRC/ingest/data/workday/"
  (cd "$SRC" && git add ingest/data/workday/*.h1b_lca.jsonl && \
    git diff --cached --quiet || git commit -q -m "h1b extract back-sync from org runtime")
fi

echo "== pre-rsync cleanup (leaked untracked files; rsync --delete cannot remove excluded dest paths) =="
rm -f "$DEST/ingest/.coverage"

echo "== rsync tree (with exclusions) =="
rsync -a --delete \
  --exclude='.git/' \
  --exclude='ingest/.env' \
  --exclude='research_data/' \
  --exclude='validation_results/' \
  --exclude='audit/' \
  --exclude='.agents/' \
  --exclude='PLAN.md' --exclude='HANDOFF.md' --exclude='worklog.md' \
  --exclude='DECISIONS.md' --exclude='CONSOLIDATION.md' \
  --exclude='job_sourcing_methodology.md' \
  --exclude='.github/workflows/ats-drain.yml' \
  --exclude='.github/workflows/ats-backfill.yml' \
  --exclude='*.har' \
  --exclude='tool-results/' \
  --exclude='ingest/data/workday_v1_archive/' \
  --exclude='.env' \
  --exclude='ingest/.coverage' \
  "$SRC"/ "$DEST"/

echo "== redact priv.email addresses in text files =="
find "$DEST" -type f \( -name '*.md' -o -name '*.py' -o -name '*.txt' \
  -o -name '*.json' -o -name '*.jsonl' -o -name '*.yml' -o -name '*.yaml' \
  -o -name '*.example' -o -name '*.sh' -o -name '*.cfg' -o -name '*.toml' \) \
  -exec sed -i -E 's/[A-Za-z0-9._%+-]+@priv\.email/redacted@priv.email/g' {} +

echo "== public .gitignore additions =="
cat >> "$DEST/.gitignore" <<'EOF'

# --- public-mirror guards (never commit these here) ---
ingest/.env
research_data/
validation_results/
audit/
.agents/
*.har
tool-results/
EOF

echo "== public README =="
cat > "$DEST/README.md" <<'EOF'
# job-sourcing pipeline

Job-source ingestion pipeline + **board-watch**: a daily diff-and-alert layer
over company job boards (Workday CXS boards today; config-as-data — one JSON
line per company in `ingest/data/board_watch/config.json`).

What's here:
- `ingest/` — the Python pipeline: 25 registered job sources (ATS-direct +
  aggregators), SQLite storage, dedup, scoring seam, ghost/repost detection,
  corroboration module (LinkedIn guest applicant-count signals joined 1:1 to
  Workday requisitions).
- `scripts/` — operational runners: `board_dump.py` (exhaustive list →
  details → corroborate → finish CSV), `gha_board_watch.py` (the daily watch
  runner used by the GitHub Actions workflow).
- `.github/workflows/board-watch.yml` — the durable daily watch chain
  (race-proof checkpointed state committed back to this repo).
- `ingest/data/` — committed operational state: watch state, the NVIDIA v2
  reference dump, ATS directory snapshot.

Secrets are **never committed here** — `ingest/.env` is materialized at
runtime from the `INGEST_ENV` repo secret (see the workflow). Local dev:
`cp ingest/.env.example ingest/.env` and fill keys as needed (most sources
work keyless).

Quick start:
```bash
pip install -e ingest
python3 -m pytest -q          # offline suite
python3 scripts/gha_board_watch.py   # run the watch locally (idempotent)
```

This is the public runtime mirror of a private research archive; sensitive
credentials, internal research docs, and scraped third-party HTML stay in the
private copy.
EOF

echo "== git init / commit / push =="
cd "$DEST"
if [ ! -d .git ]; then
  git init -b main
  git remote add origin "$REMOTE"
fi
git config user.name "mirror-sync-bot"
git config user.email "mirror-sync@users.noreply.github.com"
git add -A
if git diff --cached --quiet; then
  echo "no changes to sync"
else
  SRC_SHA=$(cd "$SRC" && git rev-parse --short HEAD)
  git commit -m "mirror sync from private archive @ ${SRC_SHA}"
fi
git push origin main
echo "== DONE: public mirror at $(git rev-parse --short HEAD) =="
