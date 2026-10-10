#!/usr/bin/env python3
"""S30 probe round 3: iCIMS (MINISO), Paycom + JobAppNetwork (PSI),
Trakstar JSON (Midea), Polestar CXS proper headers."""
import json
import re
import ssl
import urllib.request
import urllib.error

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
      "Accept": "application/json, text/javascript, */*; q=0.01",
      "X-Requested-With": "XMLHttpRequest"}
CTX = ssl.create_default_context()


def fetch(url, headers=None, data=None, timeout=20):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(), dict(e.headers)
        except Exception:
            return e.code, b"", {}
    except Exception as e:
        return 0, str(e).encode(), {}


print("=== 1. iCIMS MINISO: JSON search ===")
# iCIMS guest search API: POST jobs with form data, or GET with Accept json
for url, data, hdr in [
    ("https://careers-miniso-us.icims.com/jobs?hashed=-625860794", None, None),
    ("https://careers-miniso-us.icims.com/jobs/search?hashed=-625860794&in_iframe=1", None, None),
    ("https://careers-miniso-us.icims.com/jobs", b"", None),
]:
    st, b, hd = fetch(url, hdr, data)
    ct = hd.get("Content-Type", "")
    print(f"  {url[:80]} -> {st} ct={ct[:40]} len={len(b)}")
    if "json" in ct:
        try:
            d = json.loads(b)
            print("    keys:", list(d.keys())[:10])
            jobs = d.get("jobs") or d.get("jobsAsArray") or []
            if isinstance(jobs, dict):
                jobs = list(jobs.values())
            print("    n_jobs:", len(jobs))
            for j in jobs[:4]:
                t = j.get("title") or (j.get("jobTitle") or {}).get("title") if isinstance(j.get("jobTitle"), dict) else j.get("jobTitle")
                print("    -", t, "|", j.get("positionLocation", {}).get("city", "") if isinstance(j.get("positionLocation"), dict) else "", "|", j.get("id"))
        except Exception as e:
            print("    parse err:", e, b[:150])
    else:
        print("    snippet:", b[:150].decode("utf-8", "replace").replace("\n", " "))

print("\n=== 2. Paycom PSI career-page: find data endpoint ===")
st, b, hd = fetch("https://www.paycomonline.net/v4/ats/web.php/portal/39DCF574C16448FF09ADD3EF809F9EF2/career-page",
                  {"Accept": "text/html"})
h = b.decode("utf-8", "replace")
print("  status:", st, "len:", len(h))
for pat in [r'https?://[^"\'\s]*(?:paycomonline|jobappnetwork)[^"\'\s]*', r'/ats/[^"\'\s]+',
            r'(?:api|ajax|post)[^"\'\s]{0,60}\.php[^"\'\s]*']:
    for m_ in sorted(set(re.findall(pat, h, re.I)))[:8]:
        print("  MATCH:", m_[:130])

print("\n=== 3. JobAppNetwork PSI ===")
st, b, hd = fetch("https://apply.jobappnetwork.com/powersolutions/en", {"Accept": "text/html"})
h = b.decode("utf-8", "replace")
print("  status:", st, "len:", len(h))
titles = re.findall(r'class="[^"]*job-title[^"]*"[^>]*>([^<]+)<', h)
for t in titles[:6]:
    print("  title:", t.strip())
for pat in [r'https?://[^"\'\s]*(?:rss|feed|json|api)[^"\'\s]*', r'href="[^"]*(?:job|position)[^"]*"']:
    for m_ in sorted(set(re.findall(pat, h, re.I)))[:8]:
        print("  MATCH:", m_[:130])

print("\n=== 4. Trakstar JSON variants (Midea) ===")
for u in ["https://midea.hire.trakstar.com/jobs.json",
          "https://midea.hire.trakstar.com/api/jobs.json",
          "https://midea.hire.trakstar.com/?format=json",
          "https://midea.hire.trakstar.com/jobs/?format=json"]:
    st, b, hd = fetch(u)
    ct = hd.get("Content-Type", "")
    print(f"  {u[:60]} -> {st} ct={ct[:30]} len={len(b)}")
    if "json" in ct:
        print("    body:", b[:200].decode("utf-8", "replace").replace("\n", " "))

print("\n=== 5. Trakstar job detail page shape ===")
st, b, hd = fetch("https://midea.hire.trakstar.com/jobs/fk0ztk9/")
h = b.decode("utf-8", "replace")
print("  status:", st, "len:", len(h))
t = re.search(r'<h1[^>]*>([^<]+)</h1>', h)
print("  h1:", t.group(1).strip() if t else None)
loc = re.search(r'(?:Location|location)[^<]*</[^>]+>\s*<[^>]+>([^<]+)', h)
print("  loc-frag:", loc.group(1)[:60] if loc else None)
# ld+json structured data?
for m_ in re.findall(r'<script type="application/ld\+json">(.*?)</script>', h, re.S)[:2]:
    try:
        d = json.loads(m_)
        print("  ldjson @type:", d.get("@type"), "|", d.get("title"), "|", d.get("jobLocation"))
    except Exception:
        print("  ldjson parse err", m_[:100])

print("\n=== 6. Polestar CXS retry with full headers ===")
body = json.dumps({"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}).encode()
st, b, hd = fetch("https://polestar.wd3.myworkdayjobs.com/wday/cxs/polestar/Careers/jobs",
                  {"Content-Type": "application/json", "Accept": "application/json",
                   "Origin": "https://polestar.wd3.myworkdayjobs.com",
                   "Referer": "https://polestar.wd3.myworkdayjobs.com/en-US/Careers"}, body)
print("  status:", st, "len:", len(b))
try:
    d = json.loads(b)
    print("  total:", d.get("total"))
    for p in (d.get("jobPostings") or [])[:5]:
        print("   -", p.get("title"))
except Exception as e:
    print("  err:", e, b[:200])
