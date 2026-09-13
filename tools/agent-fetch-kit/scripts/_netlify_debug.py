#!/usr/bin/env python3
"""Debug probe: dump raw Netlify blob status + result for one batch."""
import json, os, pathlib, urllib.request, urllib.error


def _env(*names):
    """os.environ first, then kit .env, then the repo secret store ingest/.env
    (S8-A scrub — tokens are never hardcoded)."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    here = pathlib.Path(__file__).resolve()
    for p in (here.parents[1] / ".env", here.parents[3] / "ingest" / ".env"):
        try:
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() in names and v.strip():
                    return v.strip().strip("'\"")
        except OSError:
            pass
    return ""


SITE_ID = "01c2e47f-3ff6-4e09-b45f-604c49ef90fe"
TOKEN = _env("NETLIFY_TOKEN", "NETLIFY_SCRAPER_TOKEN")
BLOBS = f"https://api.netlify.com/api/v1/blobs/{SITE_ID}/site:scraper-results"

bids = ["batch-1787707208221-lrc3i8","batch-1787707209499-i9vjb0",
        "batch-1787707210503-ilnbd7","batch-1787707211493-9g4y8r"]
for bid in bids:
    print(f"\n=== {bid} ===")
    for path in [f"status/{bid}", f"result/{bid}-0", f"result/{bid}"]:
        req = urllib.request.Request(f"{BLOBS}/{path}", headers={"Authorization":f"Bearer {TOKEN}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read().decode("utf-8","ignore")
                print(f"  GET {path}: HTTP {r.status} len={len(body)}")
                print(f"    body[:600]: {body[:600]}")
        except urllib.error.HTTPError as e:
            print(f"  GET {path}: HTTP {e.code} {e.read().decode('utf-8','ignore')[:200]}")
        except Exception as e:
            print(f"  GET {path}: err {e!r}")
