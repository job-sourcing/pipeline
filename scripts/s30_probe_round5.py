#!/usr/bin/env python3
"""S30 probe round 5: parse iCIMS HTML rows, sprawl.json, Trakstar HTML,
A123 applytojob, MokaHR yintong API."""
import json
import re
import ssl
import urllib.request
import urllib.error

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"}
CTX = ssl.create_default_context()


def fetch(url, headers=None, data=None, timeout=25):
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


print("=== 1. Paycom sprawl.json content ===")
st, b, hd = fetch("https://portal-applicant-tracking.us-cent.paycomonline.net/career-portal/sprawl.json",
                  {"Accept": "application/json"})
print("  status:", st)
print("  body:", b.decode("utf-8", "replace"))

print("\n=== 2. iCIMS iframe HTML: job row structure ===")
st, b, hd = fetch("https://careers-miniso-us.icims.com/jobs/search?hashed=-625860794&in_iframe=1")
h = b.decode("utf-8", "replace")
# iCIMS classic markup: <div class="iCIMS_JobsTable"> rows or data-icims-job-id
for pat in [r'data-icims[a-z-]*="[^"]+"', r'class="iCIMS_[^"]+"', r'href="[^"]*/jobs/[^"]+"[^>]*>']:
    hits = sorted(set(re.findall(pat, h)))[:10]
    for m_ in hits:
        print("  ", m_[:110])
n = len(re.findall(r'/jobs/\d+', h))
print("  /jobs/ID link count:", n)
m = re.search(r'(\d+)\s*(?:Results|results|matches|jobs)', h)
print("  results-count-hint:", m.group(0) if m else None)
# first job title extraction
t = re.search(r'href="[^"]*/jobs/(\d+)[^"]*"[^>]*(?:title="([^"]+)")?[^>]*>\s*([^<]{5,90})', h)
if t:
    print("  first job:", t.groups())

print("\n=== 3. Trakstar: raw context around first data-href ===")
st, b, hd = fetch("https://midea.hire.trakstar.com/")
h = b.decode("utf-8", "replace")
i = h.find('data-href="/jobs/')
print(h[i - 400:i + 900].replace("\n", " ")[:1300])

print("\n=== 4. A123 applytojob: parse jobs ===")
st, b, hd = fetch("https://a123.applytojob.com/")
h = b.decode("utf-8", "replace")
print("  status:", st, "len:", len(h))
n = len(re.findall(r'/job/[A-Za-z0-9-]+', h))
print("  /job/ link count:", n)
titles = re.findall(r'<a[^>]+href="/job/[^"]+"[^>]*>\s*([^<]{4,80})', h)
for t_ in titles[:8]:
    print("  title:", t_.strip())
# what engine is this? (RecruitingBrow / Indeed apply / Jobs2Web?)
for pat in [r'generator[^>]+content="[^"]+"', r'powered\s*by[^<]{0,60}', r'https?://[^"\'\s]*(?:recruitingbrow|jobs2web|jobappnetwork|recruiting)[^"\'\s]*']:
    for m_ in sorted(set(re.findall(pat, h, re.I)))[:6]:
        print("  MATCH:", m_[:110])

print("\n=== 5. MokaHR yintong portal API ===")
# MokaHR public API patterns
for u, data in [
    ("https://app.mokahr.com/api/outer/social-recruitment/yintong/45487/positions?page=1&limit=10", None),
    ("https://app.mokahr.com/api/outer/portal/yintong/jobs?page=1", None),
]:
    st2, b2, hd2 = fetch(u, {"Accept": "application/json"})
    print("  ", u[:80], "->", st2, hd2.get("Content-Type", "")[:30], len(b2))
    if st2 == 200 and b2:
        print("    body:", b2[:200].decode("utf-8", "replace").replace("\n", " "))
# the portal page itself
st, b, hd = fetch("https://app.mokahr.com/social-recruitment/yintong/45487")
h = b.decode("utf-8", "replace")
print("  portal page:", st, len(h))
for pat in [r'orgId["\']?\s*[:=]\s*["\']?(\w+)', r'window\.__[^=]{0,30}=', r'/api/[a-z0-9/_-]+']:
    for m_ in sorted(set(re.findall(pat, h)))[:8]:
        print("  MATCH:", str(m_)[:100])
