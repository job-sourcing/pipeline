#!/usr/bin/env python3
"""S30 probe round 6: Paycom JS bundle API paths, Polestar site names,
A123 applytojob content, iCIMS pagination + total, MokaHR redirect."""
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


print("=== 1. Paycom main JS bundle: extract API endpoints ===")
st, b, hd = fetch("https://portal-applicant-tracking.us-cent.paycomonline.net/career-portal/main/index-sprawl-OLC7O5R5.js")
js = b.decode("utf-8", "replace")
print("  status:", st, "len:", len(js))
api_paths = sorted(set(re.findall(r'["\'](/(?:api|career-portal)[a-z0-9/_-]{3,60})["\']', js, re.I)))
for p in api_paths[:25]:
    print("  API:", p)
# also look for fetch/axios base URLs
bases = sorted(set(re.findall(r'https?://[a-z0-9.-]+paycomonline[a-z0-9./_-]{0,40}', js)))[:8]
for x in bases:
    print("  BASE:", x)

print("\n=== 2. Polestar CXS: try site name variants ===")
for site in ["Careers", "External", "polestar", "Polestar", "External_Careers", "Global"]:
    body = json.dumps({"appliedFacets": {}, "limit": 5, "offset": 0, "searchText": ""}).encode()
    st, b, hd = fetch(f"https://polestar.wd3.myworkdayjobs.com/wday/cxs/polestar/{site}/jobs",
                      {"Content-Type": "application/json", "Accept": "application/json",
                       "Origin": "https://polestar.wd3.myworkdayjobs.com"}, body)
    tot = None
    try:
        tot = json.loads(b).get("total")
    except Exception:
        pass
    print(f"  site={site!r}: {st} total={tot}")

print("\n=== 3. A123 applytojob content ===")
st, b, hd = fetch("https://a123.applytojob.com/")
h = b.decode("utf-8", "replace")
# strip scripts/styles for visible text
vis = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', h, flags=re.S)
vis = re.sub(r'<[^>]+>', ' ', vis)
vis = re.sub(r'\s+', ' ', vis)
print("  visible text:", vis[:600])
# titles/links with other patterns
for pat in [r'href="([^"]*(?:job|position|listing)[^"]*)"', r'<title>([^<]+)</title>']:
    for m_ in sorted(set(re.findall(pat, h)))[:8]:
        print("  MATCH:", m_[:100])

print("\n=== 4. iCIMS pagination + total count ===")
st, b, hd = fetch("https://careers-miniso-us.icims.com/jobs/search?hashed=-625860794&in_iframe=1&page=2")
h = b.decode("utf-8", "replace")
print("  page2 status:", st, "job links:", len(re.findall(r'/jobs/\d+', h)))
st, b, hd = fetch("https://careers-miniso-us.icims.com/jobs/search?hashed=-625860794&in_iframe=1")
h = b.decode("utf-8", "replace")
# iCIMS pagination markup
m = re.search(r'(?:Results|Showing)[^<]{0,80}(?:of|共)\s*(?:<[^>]+>\s*)*(\d+)', h, re.I)
print("  total-hint:", m.group(0)[:80] if m else None)
pag = re.findall(r'href="([^"]*page=\d+[^"]*)"[^>]*>\s*(\d+)', h)
print("  pagination links:", pag[:6])
# count pages by walking
for pg in [2, 3, 4]:
    st, b, hd = fetch(f"https://careers-miniso-us.icims.com/jobs/search?hashed=-625860794&in_iframe=1&page={pg}")
    print(f"  page={pg}: {st} links={len(re.findall(r'/jobs/' + chr(92) + 'd+', b.decode('utf-8', 'replace')))}")

print("\n=== 5. MokaHR yintong: follow redirect ===")
class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None
op = urllib.request.build_opener(NoRedir)
try:
    r = op.open(urllib.request.Request("https://app.mokahr.com/social-recruitment/yintong/45487", headers=UA), timeout=25)
    print("  no-redirect status:", r.status)
except urllib.error.HTTPError as e:
    print("  redirect ->", e.code, e.headers.get("Location"))
