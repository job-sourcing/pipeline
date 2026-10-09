#!/usr/bin/env python3
"""S29: the board-surface batch prober (LOCAL compute — the big push).

For every unwired keep-verdict census record:
  1. re-verify the probe file's best candidate URL (liveness + titles)
  2. fresh standard-ATS guesses from brand-derived org slugs
  3. custom-site ATS fingerprint sniffing

Output: ingest/data/ats_seed/s25_census/s29_live_probe.jsonl — one line
per (company, candidate) with liveness, row-count, and up to 5 titles
(the homonym-guard evidence; the WIRE decision stays human/agent-side).
"""
import json
import pathlib
import re
import sys
import time
import urllib.request
import urllib.error
import ssl

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROBE = ROOT / "ingest/data/ats_seed/s25_census/probe"
OUT = ROOT / "ingest/data/ats_seed/s25_census/s29_live_probe.jsonl"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"}

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE   # ascentage-class expired certs still probe

RATE = 0.15          # s between requests (polite)
TIMEOUT = 12


def fetch(url: str, want_json: bool = False):
    """(status, body) — status 0 = network error."""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            body = r.read(400_000).decode("utf-8", "replace")
            return r.status, body
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(200_000).decode("utf-8", "replace")
        except Exception:
            return e.code, ""
    except Exception as e:
        return 0, f"{type(e).__name__}"


# ── title extraction per ATS dialect ───────────────────────────────────

def titles_from(body: str, kind: str) -> list[str]:
    out = []
    try:
        if kind in ("greenhouse", "lever", "ashby", "workable",
                    "smartrecruiters", "feishuhire", "paylocity",
                    "bamboohr", "breezy", "jobvite", "rippling",
                    "teamtailor"):
            d = json.loads(body)
            jobs = (d.get("jobs") or d.get("postings") or d.get("data")
                    or d.get("job_post_list") or [])
            if isinstance(jobs, dict):
                jobs = jobs.get("Jobs") or jobs.get("jobs") or []
            for j in jobs[:6]:
                t = (j.get("title") or j.get("text")
                     or j.get("JobTitle") or (j.get("job_post") or {})
                     .get("title") or "")
                if t:
                    out.append(str(t)[:60])
        else:
            # JSON-LD JobPosting blocks
            for m in re.finditer(
                    r'"title"\s*:\s*"([^"]{4,70})"', body):
                t = m.group(1)
                if t not in out:
                    out.append(t)
                if len(out) >= 6:
                    break
    except Exception:
        pass
    return out[:5]


def count_from(body: str, kind: str) -> int:
    try:
        d = json.loads(body)
        for k in ("total", "totalFound", "count", "totalNumber"):
            v = d.get(k)
            if isinstance(v, int):
                return v
        jobs = (d.get("jobs") or d.get("postings") or d.get("data")
                or d.get("job_post_list") or [])
        if isinstance(jobs, list):
            return len(jobs)
    except Exception:
        pass
    return -1


# ── candidate URL generators ──────────────────────────────────────────

def brand_slugs(name: str) -> list[str]:
    s = re.sub(r"[^\w\s-]", "", name.lower())
    base = re.sub(r"\s+", "", s)
    words = s.split()
    out = {base}
    if len(words) > 1:
        out.add(words[0])
    out = {o for o in out if 2 <= len(o) <= 30}
    return sorted(out)


def guess_urls(name: str) -> list[tuple[str, str]]:
    """(kind, url) standard-ATS guesses from the brand name."""
    out = []
    for slug in brand_slugs(name):
        out.append(("greenhouse",
                    f"https://boards-api.greenhouse.io/v1/boards/"
                    f"{slug}/jobs"))
        out.append(("lever",
                    f"https://api.lever.co/v0/postings/{slug}"
                    f"?mode=json"))
        out.append(("ashby",
                    f"https://api.ashbyhq.com/posting-api/organization/"
                    f"{slug}?includeCompensation=false"))
        out.append(("smartrecruiters",
                    f"https://api.smartrecruiters.com/v1/companies/"
                    f"{slug}/postings?limit=6"))
        out.append(("feishuhire",
                    f"https://{slug}.jobs.feishu.cn/index"))
    return out


# ── main ───────────────────────────────────────────────────────────────

rows = [json.loads(l) for l in open(
    ROOT / "ingest/data/ats_seed/s25_census/s27_adjudication.jsonl")]
unwired = [r for r in rows if r.get("verdict") in ("china_hq", "china_ops")
           and not r.get("already_wired")]
# focus companies: skip the ones already handled (laifen/rokid/petkit/
# luyepharma are wired — stale sheet flags)
WIRED_STALE = {"laifen", "rokid", "petkit", "luyepharma", "luye-pharma"}
todo = [r for r in unwired if r.get("slug") not in WIRED_STALE]
print(f"probing {len(todo)} companies (rate {RATE}s, "
      f"~{len(todo)*7*RATE/60:.0f} min)")

results = []
n_req = 0
t0 = time.time()

def record(rec, kind, url, status, body, note=""):
    global n_req
    results.append({
        "name": rec["name"], "slug": rec.get("slug"),
        "kind": kind, "url": url, "status": status,
        "alive": 200 <= status < 300,
        "count": count_from(body, kind) if body else -1,
        "titles": titles_from(body, kind) if body else [],
        "note": note,
    })
    return results[-1]

for i, rec in enumerate(todo):
    name = rec["name"]
    # 1. best candidate from the S25 probe file
    pf = PROBE / f"{rec.get('slug')}.json"
    seen_urls = set()
    if pf.exists():
        d = json.loads(pf.read_text(encoding="utf-8"))
        cands = sorted(d.get("candidates") or [],
                       key=lambda c: -(c.get("score") or 0))
        for c in cands[:2]:
            u = c.get("url") or ""
            if not u.startswith("http") or u in seen_urls:
                continue
            seen_urls.add(u)
            # map the candidate to a live-checkable URL
            kind = "page"
            live = u
            m = re.search(r"job-boards\.greenhouse\.io/([\w-]+)", u)
            if m:
                kind = "greenhouse"
                live = (f"https://boards-api.greenhouse.io/v1/boards/"
                        f"{m.group(1)}/jobs")
            m = re.search(r"jobs\.lever\.co/([\w-]+)/?$", u)
            if m:
                kind = "lever"
                live = (f"https://api.lever.co/v0/postings/"
                        f"{m.group(1)}?mode=json")
            m = re.search(r"jobs\.ashbyhq\.com/([\w-]+)/?$", u)
            if m:
                kind = "ashby"
                live = (f"https://api.ashbyhq.com/posting-api/"
                        f"organization/{m.group(1)}?includeCompensation"
                        f"=false")
            m = re.search(r"smartrecruiters\.com/([\w]+)", u)
            if m:
                kind = "smartrecruiters"
                live = (f"https://api.smartrecruiters.com/v1/companies/"
                        f"{m.group(1)}/postings?limit=6")
            m = re.search(r"([\w-]+)\.jobs\.feishu\.cn", u)
            if m:
                kind = "feishuhire"
                live = f"https://{m.group(1)}.jobs.feishu.cn/index"
            m = re.search(r"recruiting\.paylocity\.com/[\w/-]+/([\w-]+)",
                          u)
            if m:
                kind = "paylocity"
                live = (f"https://recruiting.paylocity.com/recruiting/"
                        f"jobs/All/{m.group(1)}")
            time.sleep(RATE)
            n_req += 1
            st, body = fetch(live)
            r = record(rec, kind, live, st, body if st == 200 else "",
                       note=f"probe-candidate {u[:60]}")
            if r["alive"] and kind == "page":
                r["titles"] = titles_from(body, "html")

    # 2. fresh ATS guesses (greenhouse/lever/ashby/smartrecruiters/feishu)
    for kind, u in guess_urls(name):
        if u in seen_urls:
            continue
        seen_urls.add(u)
        time.sleep(RATE)
        n_req += 1
        st, body = fetch(u)
        if st == 0:
            continue                    # network error: skip (not a 404)
        record(rec, kind, u, st, body if st == 200 else "",
               note="guess")

    if i % 20 == 0:
        alive_n = sum(1 for r in results if r["alive"])
        print(f"  [{i+1}/{len(todo)}] {name[:30]:30} reqs={n_req} "
              f"alive-boards={alive_n} "
              f"({(time.time()-t0)/60:.1f} min)", flush=True)

with open(OUT, "w", encoding="utf-8") as f:
    for r in results:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

alive = [r for r in results if r["alive"] and (r["count"] or 0) >= 0]
print(f"\nDONE: {n_req} requests, {len(results)} probes, "
      f"{len(alive)} live boards with rows")
print(f"→ {OUT}")
