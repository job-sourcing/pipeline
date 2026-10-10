#!/usr/bin/env python3
"""S30: extract one full iCIMS job row + verify smartrecruiters pagination
shape + trakstar row completeness (posted date?)."""
import json
import re
import ssl
import urllib.request
import urllib.error

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"}
CTX = ssl.create_default_context()


def fetch(url, headers=None, timeout=25):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


print("=== 1. iCIMS full row markup (first job link context) ===")
st, b = fetch("https://careers-miniso-us.icims.com/jobs/search?hashed=-625860794&in_iframe=1")
h = b.decode("utf-8", "replace")
i = h.find('iCIMS_Anchor" title=') if 'iCIMS_Anchor" title=' in h else h.find('jobs/3767')
print(h[i - 700:i + 1600].replace("\n", " ")[:2200])

print("\n=== 2. iCIMS detail page structure (job 3769) ===")
st, b = fetch("https://careers-miniso-us.icims.com/jobs/3769/store-manager/job?in_iframe=1")
h = b.decode("utf-8", "replace")
print("  status:", st, "len:", len(h))
for pat in [r'<title>([^<]+)</title>',
            r'(?:Location|City|State|Country|Posted|Requisition)[^<]{0,10}(?:</[^>]+>\s*<[^>]+>|\s*</[^>]+>)[^<]{0,5}\s*([^<]{2,60})<',
            r'iCIMS_InfoMsg[^>]*>([^<]{2,100})<']:
    for m_ in sorted(set(re.findall(pat, h)))[:10]:
        print("  F:", str(m_)[:100])

print("\n=== 3. SmartRecruiters pagination shape ===")
st, b = fetch("https://api.smartrecruiters.com/v1/companies/ZaiLabUSLLC1/postings?limit=10&offset=0")
d = json.loads(b)
print("  keys:", list(d.keys()))
print("  totalFound:", d.get("totalFound"), "offset in resp:", d.get("offset"), "limit:", d.get("limit"))
# one full posting record
if d.get("content"):
    c = d["content"][0]
    print("  posting keys:", list(c.keys()))
    print("  sample:", json.dumps({k: c.get(k) for k in
          ("id", "name", "releasedDate", "location", "workplaceTypes",
           "department", "typeOfEmployment", "ref", "experienceLevel")},
          ensure_ascii=False)[:400])

print("\n=== 4. Trakstar: posted date on list? ===")
st, b = fetch("https://midea.hire.trakstar.com/")
h = b.decode("utf-8", "replace")
i = h.find('js-job-list-opening-name')
seg = h[i - 200:i + 1600]
# look for date-ish spans
for m_ in re.findall(r'(?:date|time|posted|meta)[a-z-]*[^>]{0,30}>([^<]{4,40})<', seg, re.I)[:10]:
    print("  D:", m_.strip())
print("  raw tail:", re.sub(r'\s+', ' ', seg[-600:])[:600])
