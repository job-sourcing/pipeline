#!/usr/bin/env python3
"""S9 TARGETED TITLE-SEARCH PROBE (ground-truth discovery test).

Question: for no_match reqs (no card in the 1,009-card index), does a
LinkedIn card EXIST that the index just never surfaced? Method: guest
search with keywords = the req's exact title (+ location), relevance
sort, ONE page (10 cards) — GT fact: cross-post titles are verbatim, so
the card ranks top-10 for its own title. Hit criterion: token-F1>=0.95
+ seniority equality vs the req title, company NVIDIA.

Resumable: writes one JSONL line per req probed to
data/workday/s9_title_probe.jsonl; skips reqIds already probed.
Run in batches:  python3 scripts/s9_title_probe.py [N]
(N = how many reqs to probe this invocation, default 25).
"""
import json, csv, re, sys, os, time, random, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobsearch.config import Config
from jobsearch.sources.base import fetch_text
from jobsearch.sources.linkedin_guest import _parse_search_results, SEARCH_URL
from urllib.parse import urlencode

STOP = {'and','the','of','for','a','an','in','to','at','us','usa','united','states',
        'ii','iii','iv','team','teams','new'}
SENIORITY = {'senior','sr','staff','principal','distinguished','architect',
             'manager','director','vp','lead','junior','jr','intern','entry'}

def tokens(s):
    return set(t for t in re.findall(r'[a-z0-9]{2,}', (s or '').lower()) if t not in STOP)

def f1(sa, sb):
    if not sa or not sb: return 0.0
    inter = sa & sb
    if not inter: return 0.0
    p, r = len(inter)/len(sb), len(inter)/len(sa)
    return 2*p*r/(p+r)


# --- location translation: 'US, CA, Santa Clara' -> 'Santa Clara, California'
STATES = {'AL':'Alabama','AK':'Alaska','AZ':'Arizona','AR':'Arkansas','CA':'California',
          'CO':'Colorado','CT':'Connecticut','DE':'Delaware','FL':'Florida','GA':'Georgia',
          'HI':'Hawaii','ID':'Idaho','IL':'Illinois','IN':'Indiana','IA':'Iowa',
          'KS':'Kansas','KY':'Kentucky','LA':'Louisiana','ME':'Maine','MD':'Maryland',
          'MA':'Massachusetts','MI':'Michigan','MN':'Minnesota','MS':'Mississippi',
          'MO':'Missouri','MT':'Montana','NE':'Nebraska','NV':'Nevada','NH':'New Hampshire',
          'NJ':'New Jersey','NM':'New Mexico','NY':'New York','NC':'North Carolina',
          'ND':'North Dakota','OH':'Ohio','OK':'Oklahoma','OR':'Oregon','PA':'Pennsylvania',
          'RI':'Rhode Island','SC':'South Carolina','SD':'South Dakota','TN':'Tennessee',
          'TX':'Texas','UT':'Utah','VT':'Vermont','VA':'Virginia','WA':'Washington',
          'WV':'West Virginia','WI':'Wisconsin','WY':'Wyoming','DC':'District of Columbia'}

def li_location(primary):
    # primary like 'US, CA, Santa Clara' | 'US, Remote' | 'US, TX, Austin'
    parts = [p.strip() for p in (primary or '').split(',')]
    if len(parts) >= 3 and parts[1].upper() in STATES:
        return f"{parts[2]}, {STATES[parts[1].upper()]}"
    return "United States"

OUT = 'data/workday/s9_title_probe.jsonl'
LOG = 'data/workday/s9_title_probe_log.jsonl'

def main(n=25):
    cfg = Config()
    rows = list(csv.DictReader(open('data/workday/nvidia_us_fulltime.csv', encoding='utf-8-sig')))
    unmatched = [r for r in rows if r['corroborationStatus'] == 'no_match']
    # known card ids (to detect NEW cards vs already-indexed)
    known_ids = {str(c['id']) for c in
                 (json.loads(l) for l in open('data/workday/nvidia_us_fulltime.li_index.jsonl') if l.strip())}
    probed = set()
    if os.path.exists(OUT):
        for l in open(OUT):
            if l.strip():
                probed.add(json.loads(l)['reqId'])
    todo = [r for r in unmatched if r['reqId'] not in probed]
    random.Random(20260915).shuffle(todo)
    batch = todo[:n]
    print(f'probed so far: {len(probed)}, this batch: {len(batch)} (of {len(todo)} remaining)')
    consecutive_blocks = 0
    out = open(OUT, 'a')
    log = open(LOG, 'a')
    for i, r in enumerate(batch):
        title = r['title'].strip()
        loc = li_location(r.get('primaryLocation',''))
        url = f"{SEARCH_URL}?{urlencode({'keywords': title, 'location': loc, 'start': 0})}"
        rec = {'reqId': r['reqId'], 'title': title, 'primaryLocation': r.get('primaryLocation',''),
               'query_location': loc}
        try:
            html = fetch_text(url, cfg=cfg)
            cards = _parse_search_results(html)
            nv = [c for c in cards if (c.get('company') or '').strip().lower() == 'nvidia']
            rec['page_cards'] = len(cards)
            rec['nvidia_cards'] = len(nv)
            rt = tokens(title)
            rs = set(t for t in re.findall(r'[a-z0-9]{2,}', title.lower()) if t in SENIORITY)
            hits = []
            for c in nv:
                ct = tokens(c['title'])
                if not ct: continue
                score = f1(rt, ct)
                if score < 0.95: continue
                cs = set(t for t in re.findall(r'[a-z0-9]{2,}', c['title'].lower()) if t in SENIORITY)
                if cs != rs: continue
                hits.append({'id': str(c['id']), 'title': c['title'], 'location': c.get('location',''),
                             'date': c.get('date',''), 'url': c.get('url',''),
                             'already_indexed': str(c['id']) in known_ids,
                             'score': round(score, 3)})
            rec['hits'] = hits
            rec['new_cards'] = [h for h in hits if not h['already_indexed']]
            rec['status'] = 'hit_new' if rec['new_cards'] else ('hit_indexed' if hits else 'no_card')
            consecutive_blocks = 0
        except Exception as exc:
            rec['status'] = 'blocked' if '403' in str(exc) or 'blocked' in str(exc).lower() else 'error'
            rec['error'] = str(exc)[:200]
            if rec['status'] == 'blocked':
                consecutive_blocks += 1
                if consecutive_blocks >= 4:
                    rec['aborted'] = True
                    out.write(json.dumps(rec)+'\n'); out.close(); log.close()
                    print('CIRCUIT BREAKER: 4 consecutive blocks — stopping batch.')
                    return
            else:
                consecutive_blocks = 0
        out.write(json.dumps(rec, ensure_ascii=False)+'\n'); out.flush()
        log.write(json.dumps({'t': time.strftime('%H:%M:%S'), 'reqId': r['reqId'],
                              'status': rec['status']})+'\n'); log.flush()
        print(f"  [{i+1}/{len(batch)}] {r['reqId']} {rec['status']}"
              + (f" ({rec['nvidia_cards']} nv cards)" if 'nvidia_cards' in rec else ''))
        time.sleep(1.5)
    out.close(); log.close()
    # summary
    stat = collections.Counter()
    for l in open(OUT):
        if l.strip(): stat[json.loads(l)['status']] += 1
    print('\nCUMULATIVE:', dict(stat), f'total probed {sum(stat.values())}')

if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 25)
