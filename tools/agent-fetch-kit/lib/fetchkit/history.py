"""Per-domain memory — cache which backend succeeded so the router can try it first next time."""
from __future__ import annotations
import json, time, os
from pathlib import Path
from urllib.parse import urlparse

def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower() or "unknown"
    except Exception:
        return "unknown"

class History:
    """JSON-backed per-domain memory of fetch attempts."""
    def __init__(self, path: str = None):
        self.path = Path(path or os.path.expanduser("~/.agent-fetch-kit/history.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {}

    def _save(self) -> None:
        # atomic write: tempfile + os.replace (prevents truncated-file corruption on kill-mid-write)
        import os, tempfile
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(json.dumps(self.data, indent=2))
                os.replace(tmp, self.path)
            except Exception:
                try: os.unlink(tmp)
                except Exception: pass
        except Exception:
            pass  # silent — history is best-effort; _load tolerates corruption

    def remember(self, url: str, backend: str, success: bool, elapsed_ms: int) -> None:
        d = _domain(url)
        entry = self.data.setdefault(d, {})
        entry["last_backend"] = backend
        entry["last_success"] = success
        entry["last_ts"] = int(time.time())
        entry["last_elapsed_ms"] = elapsed_ms
        # per-backend attempt counts
        attempts = entry.setdefault("attempts", {})
        rec = attempts.setdefault(backend, {"ok": 0, "fail": 0})
        if success: rec["ok"] += 1
        else: rec["fail"] += 1
        self._save()

    def preferred_backend(self, url: str) -> str | None:
        """Return the last SUCCESSFUL backend for this domain, or None."""
        entry = self.data.get(_domain(url))
        if not entry: return None
        if entry.get("last_success"): return entry.get("last_backend")
        return None

    def stats(self) -> dict:
        return dict(self.data)
