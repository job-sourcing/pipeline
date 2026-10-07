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

SRC="${MIRROR_SRC:-/home/z/research}"
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
# STATE files flow org→archive (GHA checkpoints are newer). config.json is
# the ONE exception: it is locally-edited (archive→org one-way). The 2026-09-19
# incident: a mirror-behind-archive sync rsync'd the org's stale 1-watch config
# over the archive's 4-watch config and then SHIPPED the regression to the
# runtime — GHA silently kept running nvidia-only. Never back-sync config.
# NO --delete either (2nd incident, same day): a NEW watch's bootstrap state
# is seeded ONLY in the archive (openai/anthropic) — a deleting back-sync
# removed it before the push-out could ship it. Stale state files for removed
# watches linger harmlessly; the FORWARD rsync (--delete) cleans the org.
rsync -a --exclude=config.json "$DEST/ingest/data/board_watch/" "$SRC/ingest/data/board_watch/"
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

echo "== back-sync GHA-produced board CSVs + the UI bundle (S26 D-S26-2) =="
# The chains (board_dump) and the S26 csv-export workflow write
# {label}.csv on the ORG repo; without this back-sync the archive —
# the SOURCE OF TRUTH — silently misses them (S26 audit: 31 of 124
# CSVs were org-only). Same org→archive flow as the LCA extracts:
# copy BEFORE the forward rsync (which would otherwise wipe them from
# the mirror), then commit. The {label}.h1b_lca.csv linked views ride
# along. The UI bundle (ingest/data/ui/) is GHA-built too — same hole,
# same fix.
if ls "$DEST"/ingest/data/workday/*_us_fulltime.csv >/dev/null 2>&1; then
  # S27 (the resurrection lesson): membership-aware back-sync. CSVs
  # whose label is NOT in config.json (removed boards — zingage + the
  # 21 us_only removals) must NOT resurrect into the archive; they are
  # DELETED org-side so the forward rsync deletes them runtime-side
  # too, and the next bundle build drops the company.
  python3 - "$DEST/ingest/data/workday" \
    "$SRC/ingest/data/board_watch/config.json" <<'PYMEM'
import json, pathlib, sys
dest = pathlib.Path(sys.argv[1])
labels = {w["label"] for w in
          json.loads(open(sys.argv[2]).read())["watches"]}
removed = []
for f in sorted(dest.glob("*_us_fulltime.csv")):
    if f.stem not in labels:
        f.unlink()
        view = f.with_name(f.name.replace("_us_fulltime.csv",
                                           "_us_fulltime.h1b_lca.csv"))
        if view.exists():
            view.unlink()
        removed.append(f.stem)
print(f"[back-sync-membership] pruned {len(removed)} removed-board "
      f"CSVs org-side: {', '.join(removed[:8])}"
      f"{' …' if len(removed) > 8 else ''}")
PYMEM
  rsync -a "$DEST"/ingest/data/workday/*_us_fulltime.csv \
    "$DEST"/ingest/data/workday/*.h1b_lca.csv \
    "$SRC/ingest/data/workday/" 2>/dev/null || \
    rsync -a "$DEST"/ingest/data/workday/*_us_fulltime.csv \
      "$SRC/ingest/data/workday/"
  (cd "$SRC" && git add ingest/data/workday 2>/dev/null && \
    git diff --cached --quiet || \
    git commit -q -m "board CSV back-sync from org runtime (chains + csv-export)")
fi
if [ -d "$DEST/ingest/data/ui" ]; then
  mkdir -p "$SRC/ingest/data/ui"
  rsync -a --delete "$DEST"/ingest/data/ui/ "$SRC/ingest/data/ui/"
  (cd "$SRC" && git add ingest/data/ui 2>/dev/null && \
    git diff --cached --quiet || \
    git commit -q -m "UI bundle back-sync from org runtime (csv-export)")
fi

echo "== back-sync the refresh-watchdog heartbeat ledger (P0-1) =="
# The watchdog commits logs/refresh_watchdog.jsonl on the ORG repo; the
# forward rsync --delete below would REMOVE it (the archive has no such
# file — observed live at e590cc5). Pull it back first, then the
# forward sync keeps it.
if [ -f "$DEST/logs/refresh_watchdog.jsonl" ]; then
  mkdir -p "$SRC/logs"
  rsync -a "$DEST/logs/refresh_watchdog.jsonl" "$SRC/logs/"
  (cd "$SRC" && git add logs/refresh_watchdog.jsonl && \
    git diff --cached --quiet || \
    git commit -q -m "watchdog ledger back-sync from org runtime")
fi

echo "== back-sync sector-census probe evidence (GHA sector-probe legs) =="
# S23: the sector-probe workflow commits probe/<slug>.json evidence on
# the ORG repo (US-egress reads). Pull them back into the archive —
# the same org-to-archive state-flow as h1b extracts above (the forward
# rsync would otherwise wipe them from the mirror).
if [ -d "$DEST/ingest/data/ats_seed" ]; then
  for d in "$DEST"/ingest/data/ats_seed/*/; do
    name=$(basename "$d")
    if [ -d "$d/probe" ]; then
      mkdir -p "$SRC/ingest/data/ats_seed/$name/probe"
      # MERGE, never overwrite: an incoming org-side probe file must
      # not clobber archive-side ADJUDICATION/s23_verdict fields (the
      # S23-audit P1: f40a1a4 wiped 37 verdicts). Python merge keeps
      # the incoming probe body + re-applies archive verdict keys.
      (cd "$SRC" && python3 - "$d/probe" "ingest/data/ats_seed/$name/probe" <<'PYEOF'
import json, sys, shutil
from pathlib import Path
incoming, target = Path(sys.argv[1]), Path(sys.argv[2])
VERDICT_KEYS = ("adjudication", "s23_verdict")
n_merged = 0
for f in incoming.glob("*.json"):
    dst = target / f.name
    try:
        new = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        shutil.copyfile(f, dst); continue
    if dst.exists():
        try:
            old = json.loads(dst.read_text(encoding="utf-8"))
        except Exception:
            shutil.copyfile(f, dst); continue
        for k in VERDICT_KEYS:
            if k in old and k not in new:
                new[k] = old[k]; n_merged += 1
    dst.write_text(json.dumps(new, indent=1, ensure_ascii=False),
                   encoding="utf-8")
print(f"[back-sync-merge] {incoming.name}: {n_merged} verdict key(s) preserved")
PYEOF
)
    fi
  done
  (cd "$SRC" && git add ingest/data/ats_seed 2>/dev/null && \
    git diff --cached --quiet || git commit -q -m "sector-probe evidence back-sync from org runtime (adjudication-merge)")
fi

echo "== pre-rsync cleanup (leaked untracked files; rsync --delete cannot remove excluded dest paths) =="
rm -f "$DEST/ingest/.coverage"

echo "== rsync tree (with exclusions) =="
rsync -a --delete \
  --exclude='.git/' \
  --exclude='ingest/.env' \
  --exclude='ingest/.netlify_fleet_keys' \
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
