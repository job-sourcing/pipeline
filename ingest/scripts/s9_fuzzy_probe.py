#!/usr/bin/env python3
"""S9 probe: how many of the 770 no_match reqs have a PLAUSIBLE fuzzy
counterpart among unmatched li_index cards? Token-overlap scoring,
location-aware. READ-ONLY analysis — no network."""
import json, csv, re, collections, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def tokens(s):
    return [t for t in re.findall(r'[a-z0-9]{2,}', (s or '').lower())
            if t not in STOP]

STOP = {'and','the','of','for','a','an','in','to','at','us','usa','united','states','ii','iii','iv'}

def loc_tokens(s):
    return {t for t in re.findall(r'[A-Za-z]{2,}', (s or '').lower())
            if t not in {'united','states','america','us','remote','hybrid','onsite','california','texas','new','york','washington'}}

rows = list(csv.DictReader(open('data/workday/nvidia_us_fulltime.csv', encoding='utf-8-sig')))
unmatched_reqs = [r for r in rows if r['corroborationStatus'] == 'no_match']

# matched reqIds (to exclude their cards)
matched_ids = {r['reqId'] for r in rows if r['corroborationStatus'] == 'matched'}

cards = [json.loads(l) for l in open('data/workday/nvidia_us_fulltime.li_index.jsonl') if l.strip()]
# used cards = linkedinUrl values present on matched CSV rows
used_card_urls = {r['linkedinUrl'].strip() for r in rows
                  if r['corroborationStatus']=='matched' and r.get('linkedinUrl')}
unmatched_cards = [c for c in cards if c.get('url') not in used_card_urls]

print(f'unmatched reqs: {len(unmatched_reqs)}, unmatched cards: {len(unmatched_cards)}')

# req location: CSV primaryLocation like 'US, CA, Santa Clara'
def req_loc(r):
    return r.get('primaryLocation','') + ' ' + (r.get('locations','') or '')

best = []
for r in unmatched_reqs:
    rt = set(tokens(r['title']))
    if not rt: continue
    rloc = loc_tokens(req_loc(r))
    cands = []
    for c in unmatched_cards:
        ct = set(tokens(c['title']))
        if not ct: continue
        # token F1-ish overlap
        inter = rt & ct
        if not inter: continue
        prec = len(inter)/len(ct); rec = len(inter)/len(rt)
        f1 = 2*prec*rec/(prec+rec) if (prec+rec) else 0
        clo = loc_tokens(c.get('location',''))
        loc_bonus = 1 if (rloc & clo) else 0
        cands.append((f1 + 0.15*loc_bonus, loc_bonus, f1, c, len(inter)))
    if cands:
        cands.sort(key=lambda x: (-x[0], x[3]['id']))
        best.append((r, cands[0]))

strong = [(r,c) for r,c in best if c[0] >= 0.85]
good   = [(r,c) for r,c in best if 0.65 <= c[0] < 0.85]
weak   = [(r,c) for r,c in best if 0.45 <= c[0] < 0.65]

print(f'reqs with ANY token overlap: {len(best)}/770')
print(f'  strong (score>=0.85): {len(strong)}')
print(f'  good (0.65-0.85): {len(good)}')
print(f'  weak (0.45-0.65): {len(weak)}')
print(f'  none/below: {770-len(best)}')
print()
print('--- STRONG samples (near-certain matches) ---')
for r, c in strong[:10]:
    print(f'  REQ: {r["title"][:60]} | {r["primaryLocation"][:22]}')
    print(f'  CARD({c[0]:.2f} loc={c[1]}): {c[3]["title"][:60]} | {c[3]["location"][:30]}')
    print()
print('--- GOOD samples ---')
for r, c in good[:8]:
    print(f'  REQ: {r["title"][:60]} | {r["primaryLocation"][:22]}')
    print(f'  CARD({c[0]:.2f} loc={c[1]}): {c[3]["title"][:60]} | {c[3]["location"][:30]}')
    print()

# how many reqs have NO card even weakly
nocard = [r for r in unmatched_reqs if not any(
    set(tokens(r['title'])) & set(tokens(c['title'])) for c in unmatched_cards)]
print(f'reqs with literally ZERO shared title tokens vs unmatched cards: {len(nocard)}')
# are there unmatched reqs whose title tokens appear ONLY among MATCHED cards (fan-out constraint)?
matched_card_titles = [c for c in cards if c.get('url') in used_card_urls]
onlymatched = 0
for r in unmatched_reqs:
    rt = set(tokens(r['title']))
    if not rt: continue
    has_un = any(rt & set(tokens(c['title'])) for c in unmatched_cards)
    has_m  = any(rt & set(tokens(c['title'])) for c in matched_card_titles)
    if has_m and not has_un: onlymatched += 1
print(f'reqs whose similar titles exist ONLY among already-used cards (1:1 exhaustion): {onlymatched}')

# card side: unmatched cards and their date distribution
dates = collections.Counter((c.get('date') or '')[:7] for c in unmatched_cards)
print('unmatched card date months:', dict(dates.most_common(8)))
