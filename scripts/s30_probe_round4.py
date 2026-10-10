#!/usr/bin/env python3
"""S30 probe round 4: iCIMS embedded data, Paycom API discovery,
Trakstar list structure, Polestar SPA config, LianLian/A123 surfaces."""
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


print("=== 1. iCIMS MINISO: hunt embedded JSON / XHR path in HTML ===")
st, b, hd = fetch("https://careers-miniso-us.icims.com/jobs/search?hashed=-625860794&in_iframe=1")
h = b.decode("utf-8", "replace")
print("  iframe page:", st, len(h))
for pat in [r'"(?:searchResults|jobs|positions|totalResults|jobCount)"\s*:\s*[\[{]',
            r'iCIMS[^"\']{0,20}(?:API|api)[^"\']{0,40}', r'/jobs[^"\'\s]*(?:json|api)[^"\'\s]*',
            r'data-icims[^=>]+="[^"]{0,60}"', r'window\.[a-zA-Z_]+\s*=\s*\{.{0,60}job']:
    for m_ in sorted(set(re.findall(pat, h)))[:6]:
        print("  MATCH:", m_[:120])
# iCIMS classic: page contains initial search results in a JS var
m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\});?\s*</script>', h, re.S)
if m:
    print("  INITIAL_STATE found, len:", len(m.group(1)))
# try the known iCIMS XHR endpoint
for u in ["https://careers-miniso-us.icims.com/jobs/search/results?hashed=-625860794",
          "https://careers-miniso-us.icims.com/jobs/search?hashed=-625860794&in_iframe=1&format=json"]:
    st2, b2, hd2 = fetch(u, {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
    print("  XHR", u[:70], "->", st2, hd2.get("Content-Type", "")[:30], len(b2))
    if "json" in hd2.get("Content-Type", ""):
        print("    body:", b2[:250].decode("utf-8", "replace").replace("\n", " "))

print("\n=== 2. Paycom: portal-applicant-tracking API discovery ===")
CK = "39DCF574C16448FF09ADD3EF809F9EF2"
for u in [f"https://portal-applicant-tracking.us-cent.paycomonline.net/career-portal/sprawl.json",
          f"https://portal-applicant-tracking.us-cent.paycomonline.net/career-portal/api/v1/jobs?clientkey={CK}",
          f"https://portal-applicant-tracking.us-cent.paycomonline.net/career-portal/api/jobs?clientkey={CK}",
          f"https://portal-applicant-tracking.us-cent.paycomonline.net/career-portal/rest/jobs?clientkey={CK}"]:
    st2, b2, hd2 = fetch(u, {"Accept": "application/json"})
    print("  ", u[:95], "->", st2, hd2.get("Content-Type", "")[:30], len(b2))
    if st2 == 200 and "json" in hd2.get("Content-Type", ""):
        print("    body:", b2[:300].decode("utf-8", "replace").replace("\n", " "))

print("\n=== 3. Trakstar list structure (rows with title+location) ===")
st, b, hd = fetch("https://midea.hire.trakstar.com/")
h = b.decode("utf-8", "replace")
# rows around data-href
rows = re.findall(r'data-href="(/jobs/[^"]+/)"(.{0,600}?)</(?:div|li|tr)>', h, re.S)
print("  rows found:", len(rows))
for href, frag in rows[:3]:
    tt = re.search(r'(?:job-title|title)[^>]*>\s*([^<]+)', frag) or re.search(r'>([^<]{5,60})<', frag)
    print("   ", href, "|", (tt.group(1).strip() if tt else "?")[:50], "|", frag[:80].replace("\n", " "))

print("\n=== 4. Polestar SPA shell config ===")
st, b, hd = fetch("https://polestar.wd3.myworkdayjobs.com/")
h = b.decode("utf-8", "replace")
print("  root:", st, len(h))
for pat in [r'href="(/[^"]*)"[^>]*>\s*[^<]{2,40}\s*(?:Careers|careers|jobs)', r'"siteName"\s*:\s*"([^"]+)"',
            r'ph-[-a-zA-Z]+="[^"]{0,60}"']:
    for m_ in sorted(set(re.findall(pat, h)))[:8]:
        print("  MATCH:", str(m_)[:100])
# also: try jobs.polestar.com
st2, b2, hd2 = fetch("https://jobs.polestar.com/")
h2 = b2.decode("utf-8", "replace")
print("  jobs.polestar.com:", st2, len(h2))
for pat in [r'https?://[^"\'\s]*(?:myworkdayjobs|greenhouse|lever|smartrecruiters|ashby|workday)[^"\'\s]*']:
    for m_ in sorted(set(re.findall(pat, h2, re.I)))[:5]:
        print("  MATCH:", m_[:110])

print("\n=== 5. LianLian Global careers page ATS backend ===")
st, b, hd = fetch("https://global.lianlianpay.com/company/join")
h = b.decode("utf-8", "replace")
print("  join page:", st, len(h))
for pat in [r'https?://[^"\'\s]*(?:mokahr|feishu|lianjia|zhaopin|liepin|booming)[^"\'\s]*',
            r'https?://[^"\'\s]*(?:apply|careers?|jobs?)[^"\'\s]*\.[^"\'\s]{4,60}']:
    for m_ in sorted(set(re.findall(pat, h, re.I)))[:8]:
        print("  MATCH:", m_[:120])

print("\n=== 6. A123 surface guesses (RecruitingBrow family + SuccessFactors) ===")
for u in ["https://a123.applytojob.com/",
          "https://a123systems.applytojob.com/",
          "https://careers.a123systems.com/"]:
    st2, b2, hd2 = fetch(u)
    print("  ", u, "->", st2, len(b2))

print("\n=== 7. EcoFlow mokahr portal slugs ===")
for slug in ["ecoflow", "EcoFlow", "ecoflow-global", "ecoflowus", "ecoflow2"]:
    st2, b2, hd2 = fetch(f"https://app.mokahr.com/portal/{slug}")
    print(f"  portal/{slug} -> {st2} {len(b2)}")
