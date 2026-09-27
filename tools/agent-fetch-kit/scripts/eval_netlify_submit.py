#!/usr/bin/env python3
"""Submit Netlify queue jobs (chrome_impersonate + puppeteer), save batch_ids, return immediately."""
import json, os, time, pathlib, urllib.request, urllib.error

REPO = pathlib.Path("/home/z/agent-kit")
OUT  = REPO / "docs" / "results" / "netlify"


def _env(*names):
    """os.environ first, then kit .env, then the repo secret store ingest/.env
    (S8-A scrub — URL/token are never hardcoded)."""
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


BASE = _env("NETLIFY_SCRAPER_URL")
TOKEN = _env("NETLIFY_TOKEN", "NETLIFY_SCRAPER_TOKEN")

def post(jobs, timeout=60):
    req = urllib.request.Request(f"{BASE}/api/scrape",
        data=json.dumps({"jobs":jobs,"result_mode":"blob","queue":True}).encode(), method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8","ignore")
    except Exception as e:
        return 0, repr(e)

jobs = [
    {"label":"chrome_impersonate/tls.peet.ws", "url":"https://tls.peet.ws/api/all", "engine":"chrome_impersonate"},
    {"label":"puppeteer/bot.sannysoft", "url":"https://bot.sannysoft.com/", "engine":"puppeteer", "wait_ms":5000},
    {"label":"puppeteer/quotes.toscrape.js", "url":"https://quotes.toscrape.com/js/", "engine":"puppeteer", "wait_ms":4000},
    {"label":"puppeteer/nowsecure.nl", "url":"https://nowsecure.nl", "engine":"puppeteer", "wait_ms":5000},
]
submitted = []
for j in jobs:
    label = j.pop("label")
    st, body = post([j])
    try:
        bj = json.loads(body)
        batch_id = bj.get("batch_id")
    except Exception:
        batch_id = None
    submitted.append({"label":label,"target":j["url"],"engine":j["engine"],"submit_status":st,"batch_id":batch_id})
    print(f"  {label}: HTTP {st} batch_id={batch_id}")
    time.sleep(0.5)

(OUT/"queue-submissions.json").write_text(json.dumps(submitted, indent=2))
print(f"\nsubmitted {len(submitted)} jobs; saved to docs/results/netlify/queue-submissions.json")
print("poll later with eval_netlify_poll.py")
