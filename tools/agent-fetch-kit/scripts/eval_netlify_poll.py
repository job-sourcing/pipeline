#!/usr/bin/env python3
"""Poll the Netlify queue jobs submitted earlier by eval_netlify_submit.py.
Writes docs/results/netlify/queue-results.md.
"""
import json, os, time, pathlib, urllib.request, urllib.error, re

REPO = pathlib.Path("/home/z/agent-kit")
OUT  = REPO / "docs" / "results" / "netlify"


def _env(*names):
    """os.environ first, then kit .env, then the repo secret store ingest/.env
    (S8-A scrub — the token is never hardcoded)."""
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

def blob_get(path, timeout=30):
    req = urllib.request.Request(f"{BLOBS}/{path}", headers={"Authorization":f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8","ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8","ignore")
    except Exception as e:
        return 0, repr(e)

subs = json.loads((OUT/"queue-submissions.json").read_text())
md = ["# Netlify queue results (polled after ~12 min)", "", f"_captured: {time.strftime('%H:%M:%S UTC')}_", ""]
md.append("| label | batch_id | status | result.ok | result.status | size | markers |")
md.append("|---|---|---|---|---|---|---|")
for s in subs:
    bid = s["batch_id"]
    label = s["label"]
    sc, sb = blob_get(f"status/{bid}")
    try:
        sj = json.loads(sb)
        rs = sj.get("results") or []
        r0 = rs[0] if rs else {}
        ok = r0.get("ok"); status = r0.get("status"); size = r0.get("size")
        err = r0.get("error")
        # fetch result body
        rc, rb = blob_get(f"result/{bid}-0")
        bl = (rb or "").lower()
        markers = [m for m in ["just a moment","cf-chl","captcha","cloudflare","forbidden","webdriver","quote"] if m in bl]
        if "quotes.toscrape" in s["target"]:
            qb = (rb or "").count('class="quote')
            md.append(f"| {label} | {bid} | HTTP {sc} | ok={ok} | {status} | size={size} | quote-blocks={qb} err={err} |")
        elif "tls.peet" in s["target"]:
            try:
                tt = json.loads(rb or "{}")
                tls = tt.get("tls",{}); h2 = tt.get("http2",{})
                md.append(f"| {label} | {bid} | HTTP {sc} | ok={ok} | {status} | size={size} | JA3={tls.get('ja3_hash')} JA4={tls.get('ja4')} IP={tt.get('ip')} |")
            except Exception as e:
                md.append(f"| {label} | {bid} | HTTP {sc} | ok={ok} | {status} | size={size} | parse-err={e!r} |")
        else:
            m = re.search(r"<title[^>]*>(.*?)</title>", rb or "", re.I|re.S)
            title = (m.group(1).strip()[:60] if m else "")
            md.append(f"| {label} | {bid} | HTTP {sc} | ok={ok} | {status} | size={size} | title=`{title}` markers={','.join(markers)} err={err} |")
    except Exception as e:
        md.append(f"| {label} | {bid} | HTTP {sc} | parse-err={e!r} |  |  | body[:200]=`{sb[:200]}` |")
md += [""]

# Also dump full status for one chrome_impersonate + one puppeteer result
md += ["## Full result bodies (chrome_impersonate + one puppeteer)", ""]
for s in subs:
    bid = s["batch_id"]
    rc, rb = blob_get(f"result/{bid}-0")
    md.append(f"### {s['label']} (`{bid}`)")
    md.append(f"```\n{(rb or '')[:1200]}\n```")
    md.append("")

(OUT/"queue-results.md").write_text("\n".join(md))
# also update main netlify.md to append queue results
main = (REPO/"docs"/"results"/"netlify.md").read_text()
main += "\n\n## 7. Queue results (polled later)\n\nSee `docs/results/netlify/queue-results.md` for full table.\n"
(REPO/"docs"/"results"/"netlify.md").write_text(main)
print("wrote docs/results/netlify/queue-results.md")
print("\n=== TABLE ===")
for line in md:
    if line.startswith("|"): print(line)
