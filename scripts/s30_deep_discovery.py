#!/usr/bin/env python3
"""Deep-discovery: find API endpoints behind Midea Trakstar, Polestar Workday,
PSI careers, MINISO US (Wix), Conflux SR detail."""
import json
import re
import ssl
import urllib.request
import urllib.error

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"}
CTX = ssl.create_default_context()


def fetch(url, headers=None, data=None, timeout=20):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read()
        except Exception:
            return e.code, b""
    except Exception as e:
        return 0, str(e).encode()


print("=== 1. Midea Trakstar HTML: find API + job count ===")
st, b = fetch("https://midea.hire.trakstar.com/")
html = b.decode("utf-8", "replace")
# find API-ish URLs
for pat in [r'https?://[^"\'\s]+api[^"\'\s]*', r'/api/[^"\'\s]+', r'jobs[^"\'\s]*\.json[^"\'\s]*',
            r'data-[a-z-]+="[^"]*(?:job|api|count)[^"]*"']:
    hits = set(re.findall(pat, html, re.I))
    for h_ in list(hits)[:12]:
        print("  MATCH:", h_[:120])
m = re.search(r'(\d+)\s*(?:open|job)', html, re.I)
print("  count-hint:", m.group(0) if m else None)
print("  len:", len(html))

print("\n=== 2. Polestar workday: find real tenant/site id ===")
for cand in ["https://polestar.wd3.myworkdayjobs.com/en-US/Careers",
             "https://polestar.wd3.myworkdayjobs.com/Careers"]:
    st, b = fetch(cand)
    print("  ", cand, "->", st, len(b))
    if st == 200:
        htm = b.decode("utf-8", "replace")
        for pat in [r'/(?:wday/cxs/[^"\'\s]+)', r'tenant["\']?\s*[:=]\s*["\']([^"\']+)',
                    r'ph-[-a-z]+job[-a-z]+[^"\'>]*']:
            for h_ in sorted(set(re.findall(pat, htm)))[:5]:
                print("    MATCH:", str(h_)[:120])
        break

print("\n=== 3. PSI careers HTML: find ATS backend ===")
st, b = fetch("https://psiengines.com/careers/")
html = b.decode("utf-8", "replace")
for pat in [r'https?://[^"\'\s]*(?:icims|paycom|jobappnetwork|workday|smartrecruiters|adp|oracle)[^"\'\s]*',
            r'careers[^"\'\s]*(?:json|api)[^"\'\s]*', r'href="[^"]*(?:job|career)[^"]*"']:
    for h_ in sorted(set(re.findall(pat, html, re.I)))[:10]:
        print("  MATCH:", h_[:130])

print("\n=== 4. MINISO US Wix: careers data source ===")
st, b = fetch("https://www.miniso-us.com/careers")
html = b.decode("utf-8", "replace")
for pat in [r'https?://[^"\'\s]*(?:icims|paycom|jobappnetwork|workday|smartrecruiters|myworkdayjobs|greenhouse|lever|ashby|adp)[^"\'\s]*',
            r'job[s]?[^"\'\s]{0,30}(?:api|json)[^"\'\s]*']:
    for h_ in sorted(set(re.findall(pat, html, re.I)))[:10]:
        print("  MATCH:", h_[:130])

print("\n=== 5. Conflux SR detail: the single posting ===")
st, b = fetch("https://api.smartrecruiters.com/v1/companies/conflux/postings?limit=5")
d = json.loads(b)
for c in d.get("content", []):
    print("  id:", c.get("id"), "| title:", c.get("name"), "| released:", c.get("releasedDate"),
          "| loc:", c.get("location", {}).get("city"), c.get("location", {}).get("country"))
    # fetch detail for description snippet
    st2, b2 = fetch(f"https://api.smartrecruiters.com/v1/companies/conflux/postings/{c.get('id')}")
    try:
        dd = json.loads(b2)
        desc = (dd.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text") or "")[:200]
        print("  desc:", desc.replace("\n", " "))
    except Exception as e:
        print("  detail err:", e)

print("\n=== 6. Urbanic SR full list ===")
st, b = fetch("https://api.smartrecruiters.com/v1/companies/urbanic/postings?limit=20")
d = json.loads(b)
for c in d.get("content", []):
    print("  ", c.get("name"), "|", c.get("location", {}).get("city"), c.get("location", {}).get("country"), "|", c.get("releasedDate"))
