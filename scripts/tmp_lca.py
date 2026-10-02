#!/usr/bin/env python3
"""LCA lookup helper: tmp_lca.py "Name1" "Name2" ... prints employer + count."""
import sys, re, subprocess
from collections import Counter

def lca(name):
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "15", "-A", "Mozilla/5.0",
                            f"https://h1bdata.info/index.php?em={name}&year=all"],
                           capture_output=True, text=True, timeout=20)
        h = r.stdout
    except Exception:
        return None
    m = re.search(r"<table[^>]*>(.*?)</table>", h, re.S)
    if not m:
        return None
    rows = re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S)
    recs = []
    for r in rows:
        tds = [re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", c)) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        if tds and tds[0]:
            recs.append(tds)
    if not recs:
        return None
    emps = Counter(x[0] for x in recs)
    top = emps.most_common(3)
    years = Counter(x[5][:4] if len(x) > 5 else "?" for x in recs)
    return {"top": top, "n": len(recs), "recent": sorted(years.items(), reverse=True)[:3]}

if __name__ == "__main__":
    for n in sys.argv[1:]:
        print(f"== {n} ==")
        res = lca(n.replace(" ", "+"))
        print(res if res else "no rows")
