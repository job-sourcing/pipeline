#!/usr/bin/env bash
# install.sh — set up agent-fetch-kit in a fresh sandbox session.
# Idempotent: safe to re-run. Installs python deps, optional stealth browsers,
# and wires the skill into the skill directories for future sessions.
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=""
if [ -x "/home/z/.venv/bin/python3" ]; then PY="/home/z/.venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then PY="python3"
else echo "no python3 found" >&2; exit 1; fi

echo "=== agent-fetch-kit install (python=$PY) ==="

# 1. Python deps (check exit code, not just pipe)
echo "--- installing python deps ---"
"$PY" -m pip install --quiet --upgrade pip 2>&1 | tail -1 || true
if ! "$PY" -m pip install --quiet curl_cffi requests 2>/dev/null; then
  echo "  WARNING: pip install curl_cffi/requests failed; some backends may not work"
fi
# Optional stealth browser (patchright — reuses existing playwright chromium)
"$PY" -m pip install --quiet patchright 2>/dev/null || echo "  patchright install skipped (optional)"

# 2. Make bin/ executable
chmod +x "$HERE"/bin/wfetch "$HERE"/bin/wprobe "$HERE"/bin/wgha 2>/dev/null || true

# 3. Wire skill into user skill dir (NOT /home/z/my-project/skills/ — that
#    folder is auto-managed by the Z.ai platform runtime and is unrelated to
#    this kit. Installing into it would create stale/confusing copies.)
echo "--- wiring skill into user skill dir ---"
SKILL_NAME="agent-fetch-kit"
SKILL_DIR="/home/user_skills"
if [ -d "$SKILL_DIR" ] || mkdir -p "$SKILL_DIR" 2>/dev/null; then
  DEST="$SKILL_DIR/$SKILL_NAME"
  if [ -d "$DEST" ] || [ -L "$DEST" ]; then rm -rf "$DEST"; fi
  mkdir -p "$DEST"
  cp "$HERE/SKILL.md" "$DEST/SKILL.md" 2>/dev/null || true
  cp -r "$HERE/lib" "$DEST/lib" 2>/dev/null || true
  cp -r "$HERE/bin" "$DEST/bin" 2>/dev/null || true
  cp -r "$HERE/.github" "$DEST/.github" 2>/dev/null || true
  cp -r "$HERE/scripts" "$DEST/scripts" 2>/dev/null || true
  chmod +x "$DEST"/bin/w* 2>/dev/null || true
  echo "  wired: $DEST"
fi

# 4. Symlink bin/ into PATH if possible
if [ -d "$HOME/.local/bin" ]; then
  ln -sf "$HERE/bin/wfetch" "$HOME/.local/bin/wfetch" 2>/dev/null || true
  ln -sf "$HERE/bin/wprobe" "$HOME/.local/bin/wprobe" 2>/dev/null || true
  ln -sf "$HERE/bin/wgha"   "$HOME/.local/bin/wgha"   2>/dev/null || true
  echo "  symlinked bin/ → $HOME/.local/bin/"
fi

# 5. Sanity check: verify imports work
echo "--- sanity check ---"
if "$PY" -c "import curl_cffi, requests" 2>/dev/null; then
  echo "  imports OK (curl_cffi, requests)"
else
  echo "  WARNING: import sanity check failed — pip install may have failed"
fi

# 6. Load .env if present
if [ -f "$HERE/.env" ]; then
  echo "--- .env found (source it: 'source .env') ---"
fi

echo "=== install complete ==="
echo "Quickstart:"
echo "  source $HERE/.env"
echo "  $HERE/bin/wfetch https://example.com --json"
echo "  $HERE/bin/wprobe"
echo "  $HERE/bin/wgha https://ipinfo.io/json --mode impersonate --json"
