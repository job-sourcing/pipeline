#!/usr/bin/env bash
# s49_ship_explorer.sh — the scripted one-command ship of the
# job-explorer UI data (S49-D). The root-cause fix for the data rot:
# the /roles data (and the corpus bundle) lands in the explorer clone
# via ONE reproducible command instead of ad-hoc copies.
#
#   bash scripts/s49_ship_explorer.sh
#
# What it does:
#   1. builds the UI-ready role-family bundles + the families index
#      (scripts/s49_build_ui_roles.py: reads ingest/data/ui/roles-
#      {pm,gen,dmm}.json READ-ONLY; writes public/data/roles-
#      {pm,gen,dmm}.ui.json + roles-families.json)
#   2. refreshes the corpus bundle (scripts/build_ui_bundle.py — the
#      same exporter that made the current index.json/jobs.json/desc/;
#      note: it also refreshes its own durable mirror under
#      ingest/data/ui/, which is that exporter's designed behavior)
#   3. validates every shipped JSON (json.loads) — fails loudly
#   4. commits public/data + src on main and pushes (NO force, ever)
#
# Guards: refuses to run against /home/z/my-project (a separate
# watched copy — the explorer clone is /home/z/work/job-explorer);
# refuses if the jsr role bundles are missing; refuses if the
# explorer's origin is not the strbtwc/job-explorer repo.
set -euo pipefail

JSR="${JSR:-/home/z/work/job-sourcing-research}"
EXPLORER="${EXPLORER:-/home/z/work/job-explorer}"
BRANCH="${BRANCH:-main}"

say()  { printf '%s\n' "$*"; }
die()  { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- guards
[ -d "$JSR" ] || die "jsr repo not found: $JSR"
[ -d "$EXPLORER" ] || die "explorer clone not found: $EXPLORER"

case "$EXPLORER" in
  /home/z/my-project*) die "EXPLORER=$EXPLORER is the watched copy — NEVER ship there. Use /home/z/work/job-explorer." ;;
esac
resolved="$(readlink -f "$EXPLORER" 2>/dev/null || true)"
case "$resolved" in
  /home/z/my-project*) die "EXPLORER resolves to $resolved (the watched copy) — refusing." ;;
esac

git -C "$EXPLORER" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "$EXPLORER is not a git work tree"
origin="$(git -C "$EXPLORER" remote get-url origin 2>/dev/null || true)"
case "$origin" in
  *strbtwc/job-explorer*) : ;;
  *) die "explorer origin is not strbtwc/job-explorer (got: ${origin:-none})" ;;
esac

# the jsr role bundles must exist BEFORE anything else runs
for fam in pm gen dmm; do
  src="$JSR/ingest/data/ui/roles-$fam.json"
  [ -s "$src" ] || die "missing jsr role bundle: $src — run the role-sweep export first (roles-{pm,gen,dmm}.json)"
done
[ -f "$JSR/scripts/s49_build_ui_roles.py" ] || die "missing $JSR/scripts/s49_build_ui_roles.py"
[ -f "$JSR/scripts/build_ui_bundle.py" ] || die "missing $JSR/scripts/build_ui_bundle.py"

say "== S49 explorer ship =="
say "   jsr:      $JSR"
say "   explorer: $EXPLORER (branch $BRANCH)"

# ------------------------------------------------- 1. role-family bundles
python3 "$JSR/scripts/s49_build_ui_roles.py" \
  --from "$JSR/ingest/data/ui" \
  --out "$EXPLORER/public/data"

# ------------------------------------------------- 2. corpus bundle
python3 "$JSR/scripts/build_ui_bundle.py" --out "$EXPLORER/public/data"

# the pre-S49 single-family file is replaced by roles-pm.ui.json
# (indexed by roles-families.json); remove it so public/data never
# carries a stale duplicate.
rm -f "$EXPLORER/public/data/roles-pm.json"

# ------------------------------------------------- 3. validate JSON
python3 - "$EXPLORER/public/data" <<'PYEOF'
import json, sys, pathlib
data = pathlib.Path(sys.argv[1])
checked = 0
for p in sorted(data.glob("*.json")) + sorted((data / "desc").glob("*.json")):
    with open(p, encoding="utf-8") as f:
        json.load(f)
    checked += 1
print(f"validated {checked} JSON files under {data}")
PYEOF

# ------------------------------------------------- 4. commit + push
cd "$EXPLORER"
git add -A -- public/data src
if git diff --cached --quiet; then
  say "nothing staged — data unchanged since the last ship; nothing to push."
  exit 0
fi

MSG="$(python3 - "$EXPLORER/public/data" <<'PYEOF'
import json, pathlib, sys
data = pathlib.Path(sys.argv[1])
idx = json.loads((data / "roles-families.json").read_text())
parts = []
snaps = []
for fam in idx["families"]:
    parts.append(f"{fam['label']} {fam['rows']:,}")
    snaps.append(f"{fam['id']}@{fam['snapshotDate']}")
corpus = json.loads((data / "index.json").read_text())
corpus_rows = corpus.get("totalRows", 0)
corpus_snap = corpus.get("snapshotDate", "?")
print(
    f"roles bundle: three-family ship — {' + '.join(parts)} rows "
    f"({', '.join(snaps)}); /roles family switcher (default {idx['default']}) "
    f"+ Tier chips; corpus {corpus_rows:,} rows @ {corpus_snap}"
)
PYEOF
)"

say "== committing =="
git commit -m "$MSG"
HEAD="$(git rev-parse --short HEAD)"
say "== pushing =="
git push origin "$BRANCH"

say ""
say "== ship summary =="
python3 - "$EXPLORER/public/data" <<'PYEOF'
import json, pathlib, sys
data = pathlib.Path(sys.argv[1])
idx = json.loads((data / "roles-families.json").read_text())
for fam in idx["families"]:
    p = data / fam["file"]
    mb = p.stat().st_size / 1e6
    print(f"  {fam['id']:4s} [{fam['label']}] {fam['rows']:>6,} rows "
          f"@ {fam['snapshotDate']} · {fam['file']} {mb:.1f}MB")
corpus = json.loads((data / "index.json").read_text())
print(f"  corpus: {corpus.get('totalRows', 0):,} rows @ "
      f"{corpus.get('snapshotDate', '?')} (index.json + jobs.json + desc/)")
print(f"  switcher index: roles-families.json (default {idx['default']})")
PYEOF
say "  commit: $HEAD  →  origin/$BRANCH (pushed)"
say "== done =="
