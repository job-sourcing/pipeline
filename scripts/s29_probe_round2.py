#!/usr/bin/env python3
"""S29 round 2: probe the missed supported classes (workable/breezy/
bamboohr/jobvite/teamtailor/rippling) + smartrecruiters re-parse for
the 162 unwired companies. Appends to s29_live_probe.jsonl."""
import json
import pathlib
import re
import ssl
import time
import urllib.request
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "ingest/data/ats_seed/s25_census/s29_live_probe.jsonl"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
RATE = 0.15
TIMEOUT = 12


def fetch(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) \
                as r:
            return r.status, r.read(400_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def brand_slugs(name):
    s = re.sub(r"[^\w\s-]", "", name.lower())
    base = re.sub(r"\s+", "", s)
    words = s.split()
    out = {base}
    if len(words) > 1:
        out.add(words[0])
    return sorted(o for o in out if 2 <= len(o) <= 30)


def parse(kind, body):
    """(count, titles) best-effort per dialect."""
    titles, count = [], -1
    if not body:
        return count, titles
    try:
        if kind == "workable":
            d = json.loads(body)
            jobs = d.get("jobs") or []
            count = len(jobs)
            titles = [str(j.get("title") or "")[:60] for j in jobs[:5]]
        elif kind == "breezy":
            d = json.loads(body)
            count = len(d)
            titles = [str(j.get("title") or "")[:60] for j in d[:5]
                      if isinstance(j, dict)]
        elif kind == "bamboohr":
            # embed2.php is HTML — <li><a class="resumator-job-title">
            titles = [m.group(1)[:60] for m in re.finditer(
                r'class="resumator-job-title"[^>]*>([^<]{4,70})<',
                body)][:5]
            count = len(titles)
        elif kind == "jobvite":
            titles = [m.group(1)[:60] for m in re.finditer(
                r'class="jv-job-list-name"[^>]*>\s*([^<]{4,70})<',
                body)][:5] or [m.group(1)[:60] for m in re.finditer(
                r'<a[^>]+class="jobTitle[^"]*"[^>]*>\s*([^<]{4,70})',
                body)][:5]
            count = len(titles)
        elif kind == "teamtailor":
            d = json.loads(body)
            jobs = d.get("data") or []
            count = int(d.get("meta", {}).get("total") or len(jobs))
            titles = [str((j.get("attributes") or {})
                          .get("title") or "")[:60] for j in jobs[:5]]
        elif kind == "rippling":
            titles = [m.group(1)[:60] for m in re.finditer(
                r'"name"\s*:\s*"([^"]{4,70})"', body)][:5]
            count = len(titles)
        elif kind == "smartrecruiters":
            d = json.loads(body)
            jobs = d.get("content") or []
            count = int(d.get("totalFound") or len(jobs))
            titles = [str(j.get("name") or "")[:60] for j in jobs[:5]]
    except Exception:
        pass
    return count, [t for t in titles if t.strip()][:5]


GUESSES = [
    ("workable",
     lambda s: f"https://apply.workable.com/api/v1/widget/accounts/"
               f"{s}?details=true"),
    ("breezy", lambda s: f"https://{s}.breezy.hr/json"),
    ("bamboohr", lambda s: f"https://{s}.bamboohr.com/jobs/embed2.php"),
    ("jobvite", lambda s: f"https://jobs.jobvite.com/{s}/search"),
    ("teamtailor", lambda s: f"https://{s}.teamtailor.com/jobs.json"),
    ("rippling", lambda s: f"https://ats.rippling.com/{s}/jobs"),
]

rows = [json.loads(l) for l in open(
    ROOT / "ingest/data/ats_seed/s25_census/s27_adjudication.jsonl")]
unwired = [r for r in rows if r.get("verdict") in ("china_hq", "china_ops")
           and not r.get("already_wired")]
WIRED_STALE = {"laifen", "rokid", "petkit", "luyepharma", "luye-pharma"}
todo = [r for r in unwired if r.get("slug") not in WIRED_STALE]

results = []
n = 0
t0 = time.time()
out = open(OUT, "a", encoding="utf-8")
for i, rec in enumerate(todo):
    name = rec["name"]
    for kind, mk in GUESSES:
        for slug in brand_slugs(name):
            u = mk(slug)
            time.sleep(RATE)
            n += 1
            st, body = fetch(u)
            if st != 200 or not body:
                continue
            count, titles = parse(kind, body)
            if count is None or (count <= 0 and not titles):
                continue
            r = {"name": name, "slug": rec.get("slug"), "kind": kind,
                 "url": u, "status": st, "alive": True, "count": count,
                 "titles": titles, "note": "round2-guess"}
            results.append(r)
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
            out.flush()
    if i % 20 == 0:
        print(f"  [{i+1}/{len(todo)}] {name[:30]:30} reqs={n} "
              f"boards={len(results)} ({(time.time()-t0)/60:.1f}m)",
              flush=True)

# smartrecruiters re-parse (round-1 hit 2 with count but no titles)
SR_RECHECK = ["conflux", "urbanic", "ZaiLab", "Zai-Lab", "imab",
              "ZhejiangHuahai", "huahai"]
for slug in SR_RECHECK:
    u = (f"https://api.smartrecruiters.com/v1/companies/{slug}"
         f"/postings?limit=10")
    time.sleep(RATE)
    n += 1
    st, body = fetch(u)
    if st == 200 and body:
        count, titles = parse("smartrecruiters", body)
        if titles:
            r = {"name": f"SR-recheck:{slug}", "slug": slug,
                 "kind": "smartrecruiters", "url": u, "status": st,
                 "alive": True, "count": count, "titles": titles,
                 "note": "round2-sr-recheck"}
            results.append(r)
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
out.close()
print(f"DONE round2: {n} requests, {len(results)} live boards "
      f"({(time.time()-t0)/60:.1f} min)")
for r in results:
    print(f"  {r['name']:28} {r['kind']:14} n={r['count']:4} "
          f"{r['titles'][0][:45] if r['titles'] else ''}")
