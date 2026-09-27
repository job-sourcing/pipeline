#!/usr/bin/env python3
"""S9 calibration: measure the feature distribution of GROUND-TRUTH
(reqId-joined) req<->card pairs vs the ambiguous candidate pairs, to
calibrate a fuzzy title+location matcher. READ-ONLY, no network."""
import json, csv, re, collections, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STOP = {'and','the','of','for','a','an','in','to','at','us','usa','united','states',
        'ii','iii','iv','team','teams','new'}
SENIORITY = {'senior','sr','staff','principal','distinguished','architect',
             'manager','director','vp','lead','junior','jr','intern','entry'}

def tokens(s):
    return [t for t in re.findall(r'[a-z0-9]{2,}', (s or '').lower()) if t not in STOP]

def seniority_set(title):
    t = set(tokens(title))
    return t & SENIORITY

def token_f1(a, b):
    sa, sb = set(a), set(b)
    if not sa or not sb: return 0.0
    inter = sa & sb
    if not inter: return 0.0
    prec, rec = len(inter)/len(sb), len(inter)/len(sa)
    return 2*prec*rec/(prec+rec)

def containment(a, b):  # fraction of a's tokens in b
    sa, sb = set(a), set(b)
    return len(sa & sb)/len(sa) if sa else 0.0

def loc_tokens(s):
    return {t for t in re.findall(r'[a-z]{3,}', (s or '').lower())
            if t not in {'united','states','america','remote','hybrid','onsite'}}

rows = list(csv.DictReader(open('data/workday/nvidia_us_fulltime.csv', encoding='utf-8-sig')))
req_by_id = {r['reqId']: r for r in rows}

# ground truth pairs: signals with job_req_id -> (req, card) known SAME role
signals = [json.loads(l) for l in open('data/workday/nvidia_us_fulltime.signals.jsonl') if l.strip()]
gt_pairs = []
for s in signals:
    rid = s.get('job_req_id')
    if rid and rid in req_by_id:
        gt_pairs.append((req_by_id[rid], s))
print(f'ground-truth pairs: {len(gt_pairs)}')

# features on GT pairs
feat = collections.defaultdict(list)
for req, s in gt_pairs:
    rt, ct = tokens(req['title']), tokens(s['title'])
    feat['f1'].append(token_f1(rt, ct))
    feat['req_in_card'].append(containment(rt, ct))
    feat['card_in_req'].append(containment(ct, rt))
    feat['sen_eq'].append(1.0 if seniority_set(req['title']) == seniority_set(s['title']) else 0.0)
    rloc = loc_tokens((req.get('primaryLocation','') or '') + ' ' + (req.get('locations','') or ''))
    cloc = loc_tokens(s.get('location','') or '')
    feat['loc_ov'].append(1.0 if (rloc & cloc) else 0.0)
    # card date proximity to req startDate
    try:
        from datetime import date
        d0 = date.fromisoformat(req['startDate'][:10]) if req.get('startDate') else None
        d1 = date.fromisoformat(s['linkedin_posted_date'][:10]) if s.get('linkedin_posted_date') else None
        feat['date_delta'].append(abs((d0-d1).days) if (d0 and d1) else None)
    except Exception:
        feat['date_delta'].append(None)

def dist(name, vals, cuts):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    out = [f'{name}: n={n}']
    prev = None
    for c in cuts:
        cnt = sum(1 for v in vals if v < c and (prev is None or v >= prev))
        out.append(f'  [{prev},{c}): {cnt} ({100*cnt/n:.0f}%)')
        prev = c
    cnt = sum(1 for v in vals if v >= prev)
    out.append(f'  [{prev},inf): {cnt} ({100*cnt/n:.0f}%)')
    print('\n'.join(out))

print('\n=== GROUND TRUTH feature distributions ===')
dist('token F1', feat['f1'], [0.5, 0.7, 0.85, 0.95, 1.0])
dist('req-tokens-in-card', feat['req_in_card'], [0.5, 0.75, 0.9, 1.0])
dist('card-tokens-in-req', feat['card_in_req'], [0.5, 0.75, 0.9, 1.0])
print(f'seniority exact-equal rate: {sum(feat["sen_eq"])}/{len(feat["sen_eq"])} = {100*sum(feat["sen_eq"])/len(feat["sen_eq"]):.0f}%')
print(f'location overlap rate: {sum(feat["loc_ov"])}/{len(feat["loc_ov"])} = {100*sum(feat["loc_ov"])/len(feat["loc_ov"]):.0f}%')
dd = [d for d in feat['date_delta'] if d is not None]
print(f'startDate vs card-date delta: n={len(dd)} median={sorted(dd)[len(dd)//2]} p90={sorted(dd)[int(len(dd)*0.9)]}')
print('  delta buckets:', dict(collections.Counter('<=2' if d<=2 else '<=7' if d<=7 else '<=30' if d<=30 else '>30' for d in dd)))

# low-F1 ground truth examples (the hard cases a matcher must still catch)
print('\n=== GT pairs with token F1 < 0.6 (hard positives) ===')
shown = 0
for req, s in gt_pairs:
    rt, ct = tokens(req['title']), tokens(s['title'])
    f = token_f1(rt, ct)
    if f < 0.6 and shown < 12:
        print(f'  F1={f:.2f} REQ: {req["title"][:58]}')
        print(f'         CARD: {s["title"][:58]} | {s.get("location","")[:25]}')
        shown += 1
