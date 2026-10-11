#!/usr/bin/env python3
"""s31-a fetcher: biopharma boards. Tier-0 curl_cffi chrome131, verify=False."""
import sys, os, json
from curl_cffi import requests as cr

OUT = "/home/z/research/archive/ingest/data/ats_seed/s31_wave/samples"
os.makedirs(OUT, exist_ok=True)

TARGETS = [
    ("oncoc4", "https://www.oncoc4.com/en/careers/job-opportunities/", False),
    ("burningrock", "https://www.brbiotech.com/careers.php", False),
    ("bioduro", "https://www.bioduro-sundia.com/careers/", False),
    ("chempartner", "https://www.chempartner.com/careers/", False),
    ("medicilon", "https://www.medicilon.com/careers", False),
    ("sirnaomics", "https://www.sirnaomics.com/en/careers/", False),
    ("porton", "https://www.portonpharma.com/career", False),
    ("hutchmed", "https://www.hutch-med.com/career-opportunities/", False),
    ("asymchem_root", "https://www.asymchem.com/", False),
    ("asymchem_careers", "https://www.asymchem.com/careers", False),
    ("fosunpharma", "https://fosunpharma.com/en/careers/", False),
    ("henlius", "https://www.henlius.com/job-opportunities.html", False),
    ("innovent_en", "https://en.innoventbio.com/Careers", False),
    ("microportortho", "https://www.microportortho.com/about-us/microport-orthopedics-careers/careers-north-america", False),
    ("microportsurgical", "https://www.microportsurgical.com/talent/", False),
    ("morehealth", "https://morehealth.com/careers", False),
]

results = {}
for name, url, vflag in TARGETS:
    dest = os.path.join(OUT, f"{name}.html")
    try:
        r = cr.get(url, impersonate="chrome131", timeout=30,
                   headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"},
                   verify=vflag)
        body = r.text
        n = len(body)
        with open(dest, "w", encoding="utf-8", errors="replace") as f:
            f.write(body[:300000])
        results[name] = {"url": url, "status": r.status_code, "len": n,
                         "ct": r.headers.get("content-type", ""), "final_url": str(r.url)}
        print(f"{name}: {r.status_code} len={n} ct={r.headers.get('content-type','')}")
    except Exception as e:
        results[name] = {"url": url, "error": repr(e)[:300]}
        print(f"{name}: ERROR {e!r}")

with open(os.path.join(OUT, "_fetch_index.json"), "w") as f:
    json.dump(results, f, indent=2)
