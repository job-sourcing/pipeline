import openpyxl, re, time, json
from collections import Counter, defaultdict

t0 = time.time()
wb = openpyxl.load_workbook('/home/z/my-project/job-sourcing-research/scripts/probe_tmp/LCA_Disclosure_Data_FY2025_Q4.xlsx', read_only=True)
ws = wb.active
rows = ws.iter_rows(values_only=True)
header = list(next(rows))
idx = {h: i for i, h in enumerate(header)}

employers = Counter()          # distinct employer names (case-insensitive)
employer_countries = Counter()
visa_classes = Counter()
statuses = Counter()
total_rows = 0

# Chinese-company name patterns (known roster + expansions). Regex on employer name + DBA.
CN_PATTERNS = {
  'TikTok/ByteDance': r'\btiktok\b|\bbytedance\b|bytedance inc',
  'Alibaba': r'\balibaba\b|\balipay\b|\baliexpress\b|\blazada\b|\bdingtalk\b|\btaobao\b|\b1688\b',
  'Tencent': r'\btencent\b|tencent america|\briot games\b|\bsupercell\b',  # riot/supercell owned by tencent
  'JD.com': r'\bjd\.?com\b|\bjoybuy\b|\bjingdong\b',
  'Shein': r'\bshein\b|shein distribution|roadget business',
  'Temu/PDD': r'\btemu\b|whaleco|\bpdd\b|pinduoduo',
  'BYD': r'\bbyd\b|byd auto|byd north america|byd electronic',
  'XPeng': r'\bxpeng\b|xpeng motors|guangzhou xiaopeng',
  'NIO': r'\bnio\b|nio usa|nio inc',
  'Li Auto': r'\bli auto\b|\blixiang\b',
  'Zeekr/Polestar/Geely': r'\bzeekr\b|\bpolestar\b|\bgeely\b|\bvolvo cars\b|\blingyun\b',  # polestar geely-backed
  'Faraday Future': r'faraday future',
  'Gotion': r'\bgotion\b',
  'CATL': r'\bcatl\b|contemporary amperex',
  'DiDi': r'\bdidi\b|didichuxing|didilabs|didi labs',
  'TCL': r'\btcl\b|tcl industries|tcl electronics|tcl commun',
  'TP-Link': r'tp-?link',
  'Lenovo': r'\blenovo\b|\bmotorola mobility\b',  # motorola owned by lenovo
  'Huawei': r'\bhuawei\b|futurewei',
  'ZTE': r'\bzte\b|\bzte usa\b',
  'Hikvision': r'\bhikvision\b',
  'Dahua': r'\bdahua\b',
  'DJI': r'\bdji\b|da-?jiang',
  'Xiaomi': r'\bxiaomi\b',
  'NetEase': r'netease|net ease|netease games|\bbladepoint\b',
  'Baidu': r'\bbaidu\b|paddlepaddle',
  'Trip.com': r'trip\.?com|\bctrip\b|trip com|travel holdings \(us',
  'Xiaohongshu/RedNote': r'xiaohongshu|rednote|red note|\bxhs\b',
  'WeRide': r'\bweride\b',
  'Pony.ai': r'pony\.?ai|pony ai inc',
  'Moonshot AI': r'moonshot|moonshotai|kimi ai',
  'MiniMax': r'\bminimax\b|minimax ai',
  'ShengShu': r'shengshu|sheng shu',
  'DeepSeek': r'deepseek',
  'Horizon Robotics': r'horizon robotics',
  'Holo/HiYoyo/HoYoverse': r'hoyoverse|miHoYo|ho yo verse|cognosphere',
  'United Imaging': r'united imaging',
  'GE Appliances/Haier': r'ge appliances|\bhaier\b|qingdao haier|haier us',
  'WuXi': r'wuxi app?tec|wuxi biologics|wuxi advanced|shanghai wuXi|wuxi xp',
  'Mindray': r'mindray|north america mindray',
  'BGI/Genomics': r'\bbgi\b|bgi genomics|mgi tech|complete genomics',
  'GenScript': r'genscript|genscript biotech|legend biotech',  # legend biotech = genScript
  'Anker': r'\banker\b|anker innov|eufy|soundcore|ankerwork',
  'Roborock': r'roborock|roborock inc',
  'Dreame': r'dreame',
  'Ecovacs': r'ecovacs',
  'Narwal': r'narwal|narwal robot',
  'SharkNinja': r'sharkninja',  # Chinese-owned (JS Global) though HQ moves
  'PlusAI': r'plusai|plus automation|plus \(usa\) inc',  # autonomous trucking
  'Wejo/Full Truck(Manbang?)': r'manbang|full truck alliance',
  'Envision': r'envision (solar|digital|energy)',
  'Vivo/Oppo/OnePlus': r'\bvivo\b|\boppo\b|oneplus|one plus',
  'Transsion': r'transsion|tecno|itel mobile|infinix',
  'Honor': r'\bhonor\b.*device|honor terminal|honor device',
  'Kuaishou/Kwai': r'\bkwai\b|kuaishou|kuaishou technology',
  'Meituan': r'\bmeituan\b',
  'Pinduoduo': r'pinduoduo',
  'Luckin': r'luckin',
  'Pop Mart': r'pop ?mart',
  'MINISO': r'miniso',
  'Baba? no-op': r'nevermatch-zzz'
}
compiled = {k: re.compile(v, re.I) for k, v in CN_PATTERNS.items()}

hits = defaultdict(lambda: {'cases': 0, 'workers': 0, 'states': Counter(), 'titles': Counter(), 'names': Counter()})
CN_NAME_FIELDS = (idx['EMPLOYER_NAME'], idx['TRADE_NAME_DBA'])

for row in rows:
    total_rows += 1
    ename = row[idx['EMPLOYER_NAME']] or ''
    dba = row[idx['TRADE_NAME_DBA']] or ''
    key = (str(ename) + '||' + str(dba)).lower()
    employers[key] += 1
    ctry = row[idx['EMPLOYER_COUNTRY']]
    if ctry:
        employer_countries[str(ctry).strip().upper()] += 1
    visa_classes[str(row[idx['VISA_CLASS']] or '')] += 1
    statuses[str(row[idx['CASE_STATUS']] or '')] += 1
    blob = key
    for label, rx in compiled.items():
        if rx.search(blob):
            h = hits[label]
            h['cases'] += 1
            try:
                h['workers'] += int(row[idx['TOTAL_WORKER_POSITIONS']] or 0)
            except (TypeError, ValueError):
                pass
            st = row[idx['WORKSITE_STATE']]
            if st:
                h['states'][str(st)] += 1
            jt = row[idx['JOB_TITLE']]
            if jt:
                h['titles'][str(jt).strip()[:60]] += 1
            h['names'][str(ename).strip()[:70]] += 1
            break  # one bucket per row

wb.close()

print(f'rows={total_rows} distinct_employer_names={len(employers)} elapsed={round(time.time()-t0,1)}s')
print('TOP_EMPLOYER_COUNTRIES:', employer_countries.most_common(10))
print('VISA_CLASSES:', visa_classes.most_common(10))
print('STATUSES:', statuses.most_common(8))
print()
print('=== CHINESE COMPANY HITS (FY2025 LCA, full year) ===')
for label in sorted(hits, key=lambda k: -hits[k]['cases']):
    h = hits[label]
    top_states = ','.join(f'{s}:{n}' for s, n in h['states'].most_common(4))
    top_titles = ' / '.join(f'{t}({n})' for t, n in h['titles'].most_common(3))
    top_names = ' / '.join(f'{n}({c})' for n, c in h['names'].most_common(2))
    print(f"{label:28s} cases={h['cases']:5d} workers={h['workers']:5d} states[{top_states}] employer[{top_names}]")
    print(f"{'':28s} titles: {top_titles[:150]}")

with open('/home/z/my-project/job-sourcing-research/scripts/probe_tmp/lca_fy2025_cn_hits.json', 'w') as f:
    json.dump({k: {'cases': v['cases'], 'workers': v['workers'], 'states': dict(v['states']),
                   'top_names': dict(v['names'].most_common(5))} for k, v in hits.items()}, f, indent=1)
print('saved json')
