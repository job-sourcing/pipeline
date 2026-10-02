#!/usr/bin/env python3
import sys, re, subprocess
from html import unescape

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'

def lca(name):
    try:
        r = subprocess.run(['curl','-s','--max-time','15','-A',UA,
            f'https://h1bdata.info/index.php?em={name}'],
            capture_output=True, text=True, timeout=20)
        html = r.stdout
    except Exception as e:
        return f'ERR {e}'
    if not html or '<table' not in html:
        return 'NO-TABLE'
    rows = re.findall(r'<tr>(.*?)</tr>', html, re.S)
    out = []
    for row in rows[1:]:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        if len(tds) >= 5:
            emp = unescape(re.sub(r'<[^>]+>','',tds[0])).strip()
            title = unescape(re.sub(r'<[^>]+>','',tds[1])).strip()[:40]
            loc = unescape(re.sub(r'<[^>]+>','',tds[3])).strip()[:25]
            yr = unescape(re.sub(r'<[^>]+>','',tds[4])).strip()[:10]
            out.append(f'{emp} | {title} | {loc} | {yr}')
    if not out:
        return 'NO-HITS'
    seen = {}
    for o in out:
        k = o.split(' | ')[0]
        seen[k] = seen.get(k, 0) + 1
    years = sorted({o.rsplit('|',1)[1].strip() for o in out if o.rsplit('|',1)[1].strip()})[-3:]
    return f'{len(out)} filings {years}: ' + '; '.join(f'{k}({v})' for k,v in list(seen.items())[:6])

if __name__ == '__main__':
    for name in sys.argv[1:]:
        print(f'== {name} => {lca(name)}', flush=True)
