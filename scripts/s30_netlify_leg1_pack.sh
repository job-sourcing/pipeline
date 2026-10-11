#!/bin/bash
# S30 / Task 3 — pack the LEG-1 source zip for POST /sites/{site_id}/builds.
# Round 2: vendored node_modules (no remote npm install), no plugin, bash
# wrapper that never fails and mirrors diagnostics into out/.
set -euo pipefail

REPO=/home/z/research
SRC="$REPO/scripts"
PAY=/tmp/s30leg1_payload
ZIP=/tmp/s30leg1.zip

rm -rf "$PAY" "$ZIP"
mkdir -p "$PAY/out" "$PAY/node_modules/@netlify"

cat > "$PAY/netlify.toml" <<'EOF'
[build]
  command = "bash leg.sh"
  publish = "out"
EOF

cat > "$PAY/package.json" <<'EOF'
{
  "name": "s30-probe-leg",
  "private": true,
  "type": "module",
  "dependencies": { "@netlify/blobs": "8.2.0" }
}
EOF

cp "$SRC/s30_netlify_leg1_probe.mjs"       "$PAY/probe.mjs"
cp "$SRC/s30_netlify_leg1_leg.sh"          "$PAY/leg.sh"
cp "$SRC/s30_netlify_leg1_manifest.json"   "$PAY/manifest.json"
cp -r /tmp/s30leg1/node_modules/@netlify/blobs "$PAY/node_modules/@netlify/blobs"
printf '<!doctype html><title>s30 leg1</title>pending\n' > "$PAY/out/index.html"

cd "$PAY"
zip -r -q "$ZIP" netlify.toml package.json manifest.json probe.mjs leg.sh node_modules out
echo "packed: $ZIP ($(wc -c < "$ZIP") bytes)"
unzip -l "$ZIP" | tail -5
