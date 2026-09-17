#!/usr/bin/env python3
"""S10 probe: what does the LinkedIn guest search actually return for
(a) a legitimately-zero-result narrow query, (b) a beyond-end offset,
(c) a normal mid-window page? Decides the blocked_empty livelock fix
(H3v P2): which 0-card shapes are walls vs genuine ends."""
import sys
sys.path.insert(0, "/home/z/my-project/job-sourcing-research/ingest")
from urllib.parse import urlencode
from jobsearch import corroborate
from jobsearch.config import Config

SEARCH_URL = corroborate.SEARCH_URL
p = corroborate.LinkedInSignalProvider(cfg=Config())

probes = [
    ("narrow-zero?", "NVIDIA zyxv", "Austin, Texas", 0),
    ("beyond-end", "NVIDIA", "Santa Clara, California", 990),
    ("normal", "NVIDIA", "Santa Clara, California", 0),
]
for name, kw, loc, start in probes:
    url = f"{SEARCH_URL}?{urlencode({'keywords': kw, 'location': loc, 'start': start, 'sortBy': 'DD'})}"
    try:
        html = corroborate.fetch_text(url, cfg=Config())
    except Exception as e:
        print(f"{name}: TRANSPORT ERROR {type(e).__name__}: {str(e)[:100]}")
        continue
    n = len(corroborate._parse_search_results(html))
    body = html or ""
    print(f"{name}: cards={n} body_len={len(body)} "
          f"empty_body={not body.strip()} "
          f"has_chrome={'base-search' in body or 'jobs' in body.lower()[:2000]} "
          f"snippet={body[:120]!r}")
