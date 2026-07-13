import json, glob, os, csv, collections, datetime

WEEK = datetime.datetime(2026, 7, 6, 0, 0, 0).timestamp()

# 备份 manifest -> label
bak = 'markush-run/_backup_before_reorganize_20260704-145842/benchmark_previous_contents/ep_application_candidates_overnight_20260703-175215_merged.json'
bm = json.loads(open(bak, encoding='utf-8-sig').read())
brecs = bm['records'] if isinstance(bm, dict) and 'records' in bm else bm
label_of = {}
for r in brecs:
    app = r.get('application_number')
    if app: label_of[app] = (r.get('benchmark_label') or '')

# 第一批 209 完整闭环 (audit min_complete=True), 记录 case + path
audit = 'markush-run/benchmark/patents/_raw_file_completeness_audit.csv'
first_rows = []
with open(audit, encoding='utf-8-sig', newline='') as f:
    for r in csv.DictReader(f):
        if r.get('min_complete') == 'True':
            first_rows.append((r['case'], r['path']))

def latest_pdf_mtime(casedir):
    mts = []
    for sub in ('docs', 'original-application'):
        for p in glob.glob(os.path.join(casedir, sub, '*.pdf')):
            try: mts.append(os.path.getmtime(p))
            except OSError: pass
    return max(mts) if mts else None

print('=== 第一批 209 完整闭环: 正/负例 + 抓取时间 ===')
lab_cnt = collections.Counter()
neg_this_week = 0; neg_before = 0; neg_dirs_missing = 0
neg_examples = []
for case, path in first_rows:
    lab = label_of.get(case, '')
    tag = 'positive' if lab.startswith('positive') else ('negative' if lab.startswith('negative') else 'other')
    lab_cnt[tag] += 1
    if tag == 'negative':
        mt = latest_pdf_mtime(path) if os.path.isdir(path) else None
        if mt is None:
            neg_dirs_missing += 1
        elif mt >= WEEK:
            neg_this_week += 1
        else:
            neg_before += 1
            if len(neg_examples) < 8:
                neg_examples.append((case, datetime.datetime.fromtimestamp(mt).strftime('%Y-%m-%d %H:%M')))
print(' label:', dict(lab_cnt))
print(f' 负例中: 本周(>=7.6)抓的={neg_this_week}, 7.6之前抓的={neg_before}, 目录缺失/无PDF={neg_dirs_missing}')
print(' 7.6之前抓的负例样本(最新PDF时间):')
for c, t in neg_examples:
    print('   ', c, t)

# 第一批完整闭环这些 case 的物理位置在哪个顶层目录?
print('\n=== 第一批209 case 的物理根目录分布 ===')
rootcnt = collections.Counter()
for case, path in first_rows:
    parts = path.replace('\\','/').split('/')
    # find markush-run index
    try:
        i = parts.index('markush-run')
        rootcnt['/'.join(parts[i:i+3])] += 1
    except ValueError:
        rootcnt[path[:40]] += 1
for k, v in rootcnt.most_common():
    print(f'  {k}: {v}')
