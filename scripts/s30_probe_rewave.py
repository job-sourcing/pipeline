#!/usr/bin/env python3
"""S30 RE-wave surface probes: confirm API shapes for the new-ATS classes.

Evidence-first: for each candidate, hit the API surface, extract COUNT +
sample TITLES (homonym guard), print machine-readable JSON lines.
"""
import json
import ssl
import urllib.request
import urllib.error

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"}
CTX = ssl.create_default_context()
TIMEOUT = 15


def fetch(url, headers=None, data=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, data=data)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read()
        except Exception:
            return e.code, b""
    except Exception as e:
        return 0, str(e).encode()


def safe_json(b):
    try:
        return json.loads(b)
    except Exception:
        return None


PROBES = [
    # --- SmartRecruiters candidates (title evidence = homonym guard) ---
    ("Zai Lab", "sr", "https://api.smartrecruiters.com/v1/companies/ZaiLabUSLLC1/postings?limit=10"),
    ("I-Mab", "sr", "https://api.smartrecruiters.com/v1/companies/I-MabBiopharma/postings?limit=10"),
    ("Conflux", "sr", "https://api.smartrecruiters.com/v1/companies/conflux/postings?limit=10"),
    ("ConfluxNet", "sr", "https://api.smartrecruiters.com/v1/companies/confluxnetwork/postings?limit=10"),
    ("Urbanic", "sr", "https://api.smartrecruiters.com/v1/companies/urbanic/postings?limit=10"),
    ("A123", "sr", "https://api.smartrecruiters.com/v1/companies/a123/postings?limit=10"),
    ("A123full", "sr", "https://api.smartrecruiters.com/v1/companies/a123systemswanxiang/postings?limit=10"),
    ("AFFiNE", "sr", "https://api.smartrecruiters.com/v1/companies/affine/postings?limit=10"),
    ("affine2", "sr", "https://api.smartrecruiters.com/v1/companies/AFFiNE/postings?limit=10"),
    # --- Trakstar (Midea) — discover API shape ---
    ("Midea-trakstar-root", "trakstar", "https://midea.hire.trakstar.com/"),
    ("Midea-trakstar-api", "trakstar", "https://midea.hire.trakstar.com/api/jobs"),
    ("Midea-trakstar-api2", "trakstar", "https://midea.hire.trakstar.com/api/v1/jobs"),
    # --- MINISO US careers — find ATS backend ---
    ("MINISO-US", "page", "https://www.miniso-us.com/careers"),
    # --- LianLian feishu (S29 found 200) ---
    ("LianLian-feishu", "feishu", "https://lianlian.jobs.feishu.cn/index"),
    # --- PSI / Weichai America paycom ---
    ("PSI-careers", "page", "https://psiengines.com/careers/"),
    # --- EcoFlow global mokahr? ---
    ("EcoFlow-moka", "mokahr", "https://app.mokahr.com/portal/ecoflow"),
    ("EcoFlow-careers", "page", "https://www.ecoflow.com/careers"),
    # --- Polestar workday tenant confirm ---
    ("Polestar-wd", "workday", "https://polestar.wd3.myworkdayjobs.com/wday/cxs/polestar/Careers/jobs"),
]

for name, kind, url in PROBES:
    if kind == "workday":
        st, b = fetch(url, {"Accept": "application/json"},
                      data=json.dumps({"appliedFacets": {}, "limit": 10, "offset": 0, "searchText": ""}).encode())
    else:
        st, b = fetch(url)
    d = safe_json(b)
    out = {"name": name, "kind": kind, "url": url, "status": st}
    if d:
        if "content" in d:  # smartrecruiters
            out["count"] = d.get("totalFound")
            out["titles"] = [c.get("name") for c in d["content"][:5]]
        elif isinstance(d.get("jobs"), list):  # trakstar?
            out["count"] = len(d["jobs"])
            out["titles"] = [j.get("title") or j.get("name") for j in d["jobs"][:5]]
        elif "total" in d:  # workday
            out["count"] = d.get("total")
            out["titles"] = [p.get("title") for p in (d.get("jobPostings") or [])[:5]]
        else:
            out["keys"] = list(d.keys())[:10]
    else:
        snippet = b[:200].decode("utf-8", "replace")
        out["snippet"] = snippet
    print(json.dumps(out, ensure_ascii=False))
