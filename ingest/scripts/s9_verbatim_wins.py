#!/usr/bin/env python3
"""S9: the honest matcher ceiling — no_match reqs whose unmatched card is
token-SET-identical (F1>=0.95 + seniority equal). These are the only safe
fuzzy wins; everything below is a different role (GT: real pairs are
verbatim). READ-ONLY."""
import json, csv, re, collections, sys, os

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

def loc_tokens(s):
    return {t for t in re.findall(r'[a-z]{3,}', (s or '').lower())
            if t not in {'united','states','america','remote','hybrid','onsite'}}

rows = list(csv.DictReader(open('data/workday/nvidia_us_fulltime.csv', encoding='utf-8-sig')))
unmatched_reqs = [r for r in rows if r['corroborationStatus'] == 'no_match']
used_urls = {r['linkedinUrl'].strip() for r in rows
             if r['corroborationStatus'] == 'matched' and r.get('linkedinUrl')}
cards = [json.loads(l) for l in open('data/workday/nvidia_us_fulltime.li_index.jsonl') if l.strip()]
unmatched_cards = [c for c in cards if c.get('url') not in used_urls]
print(f'no_match reqs: {len(unmatched_reqs)}, unmatched cards: {len(unmatched_cards)}')

# tier-1: token-set identical + seniority equal (near-verbatim, order-free)
wins = []
for r in unmatched_reqs:
    rt = tokens(r['title'])
    rs = set(t for t in re.findall(r'[a-z0-9]{2,}', r['title'].lower()) if t in SENIORITY)
    rloc = loc_tokens((r.get('primaryLocation','') or '') + ' ' + (r.get('locations','') or ''))
    best = None
    for c in unmatched_cards:
        ct = tokens(c['title'])
        if not ct: continue
        score = f1(rt, ct)
        if score < 0.95: continue
        cs = set(t for t in re.findall(r'[a-z0-9]{2,}', c['title'].lower()) if t in SENIORITY)
        if cs != rs: continue  # seniority must match exactly (GT: 100%)
        clo = loc_tokens(c.get('location','') or '')
        loc = 1 if (rloc & clo) else 0
        cand = (score + 0.1*loc, loc, c)
        if best is None or cand[0] > best[0]: best = cand
    if best: wins.append((r, best))

print(f'\nVERBATIM-ORDER-FREE wins available in current index: {len(wins)}')
for r, (s, loc, c) in wins[:15]:
    print(f'  REQ: {r["title"][:62]} | {r["primaryLocation"][:20]}')
    print(f'  CARD(loc={loc}): {c["title"][:62]} | {c["location"][:28]}')

# what are the unmatched cards, really? classify them
matched_req_ids = {r['reqId'] for r in rows if r['corroborationStatus'] == 'matched'}
signals = [json.loads(l) for l in open('data/workday/nvidia_us_fulltime.signals.jsonl') if l.strip()]
sig_by_url = {s.get('linkedin_url'): s for s in signals}
cat = collections.Counter()
foreign = 0
for c in unmatched_cards:
    s = sig_by_url.get(c.get('url'))
    rid = (s or {}).get('job_req_id')
    if rid and rid in matched_req_ids:
        cat['dup-card of matched req (repost/second posting)'] += 1
    elif rid:
        cat['reqId points at removed/unknown req'] += 1
    else:
        # title-token check vs any matched req title (verbatim dup)
        ct = tokens(c['title'])
        dup = any(ct == tokens(mr['title']) for mr in rows if mr['corroborationStatus']=='matched')
        cat['title-dup of a matched req' if dup else 'no reqId, not a title-dup'] += 1
print('\nunmatched card composition:')
for k, v in cat.most_common(): print(f'  {k}: {v}')
