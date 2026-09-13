#!/usr/bin/env bash
# bootstrap.sh — recreate the environment after a container recycle (D6/R10).
# Install: .venv first; on venv failure, user-site install with
# --break-system-packages (PEP 668). A failed smoke suite is a LOUD warning,
# not a bootstrap failure (exit 0) — fix it before building on the checkout.
set -euo pipefail
cd "$(dirname "$0")"

USE_VENV=1
if [ ! -d .venv ] && ! python3 -m venv .venv 2>/dev/null; then USE_VENV=0; fi
if [ "$USE_VENV" = 1 ]; then
  echo "==> Python: .venv + editable install"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q --upgrade pip
  pip install -q -e ".[dev]"
else
  echo "==> .venv unavailable — user-site install (PEP 668: --break-system-packages)"
  python3 -m pip install -q --user --break-system-packages -e ".[dev]"
fi

echo "==> Node runtime check (llm/ seam, D1)"
node --version || { echo "ERROR: node not found — required for LLM seam"; exit 1; }
node -e "import('./vendor/z-ai-web-dev-sdk/dist/index.js').then(() => console.log('vendored z-ai SDK OK')).catch(e => { console.error('SDK load failed:', e.message); process.exit(1); })"

echo "==> Smoke test (offline unit tests)"
if python3 -m pytest -q; then
  echo "Smoke suite green."
else
  echo "!!! WARNING: SMOKE TESTS FAILED — environment NOT verified. !!!" >&2
  echo "!!! Bootstrap continues (exit 0), but run: python3 -m pytest -q !!!" >&2
  echo "!!! and fix before building on this checkout.                 !!!" >&2
fi
if [ "$USE_VENV" = 1 ]; then
  echo "Bootstrap complete. New shell: source .venv/bin/activate && search-jobs 'Python Developer' --num 5"
else
  echo "Bootstrap complete. PATH may need: export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo "Then: search-jobs 'Python Developer' --num 5"
fi
