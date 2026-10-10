#!/usr/bin/env python3
"""S30: pin the iCIMS detail page structure + non-hashed search URL."""
import re
import ssl
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"}
CTX = ssl.create_default_context()


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


print("=== non-hashed search ===")
st, h = fetch("https://careers-miniso-us.icims.com/jobs/search?in_iframe=1")
print("status:", st, "len:", len(h), "job links:", len(re.findall(r'/jobs/\d+/', h)))

print("\n=== detail page 3769 structure ===")
st, h = fetch("https://careers-miniso-us.icims.com/jobs/3769/store-manager/job?in_iframe=1")
print("status:", st, "len:", len(h))
# the iCIMS detail fields: look for the header block
for pat in [r'<span\s+class="sr-only field-label">([^<]+)</span>\s*<span[^>]*>\s*([^<]{2,70})</span>',
            r'iCIMS_JobHeaderField">([^<]+)</dt>\s*<dd[^>]*>\s*<span[^>]*>\s*([^<]{0,70})',
            r'<h1[^>]*>\s*([^<]{2,90})</h1>']:
    for m_ in re.findall(pat, h)[:12]:
        print("  F:", m_)
# description container
m = re.search(r'<div[^>]+class="[^"]*iCIMS_ExtendedDescription[^"]*"[^>]*>(.*?)</div>\s*(?:<|$)', h, re.S)
if m:
    print("  ExtendedDescription found, len:", len(m.group(1)))
else:
    # any big text container
    for cl in ["iCIMS_JobContent", "jobContent", "iCIMS_InfoMsg", "iCIMS_JobIntro"]:
        i = h.find(cl)
        if i > 0:
            print(f"  {cl} at {i}:", re.sub(r'\s+', ' ', h[i:i + 300])[:300])
            break
