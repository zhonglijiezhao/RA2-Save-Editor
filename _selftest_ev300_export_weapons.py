# -*- coding: utf-8 -*-
"""§300 自检：武器表导出 CSV（只读）。

为什么单独立一套（2026-10-28 玩家问「这个存档里的武器能提取出来吗，尤其是非原版的」）：
  存档里那张武器表是**存盘当时生效的那份**（地图 INI / MIX 覆盖也算），
  所以它跟散装 rulesmd.ini 不一定一致 —— 导出来才有比对/发帖的价值。

  本自检把导出的语义钉死：
    ① 全表行数 = `weapon_objects()` 的条数，一把不漏、一把不多；
    ② 差异表 ⊆ 全表，且"差异"列非空；
    ③ 差异集合用**另一条路径**（直接比 rules 的 Damage/ROF/Range）独立复算，
       两边必须一致 —— 否则就是导出逻辑自己算错了；
    ④ 官方三列取自 rules，抽样核对；
    ⑤ 有弹头的行必须给满 11 项 Verses；
    ⑥ 射程 lepton 与格数自洽（1 格 = 256）；
    ⑦ 导出**不碰明文**（前后 hash 一致）；
    ⑧ 不传路径时落在存档同目录、文件名带「武器表」。

跑法：python _selftest_ev300_export_weapons.py
"""
import csv
import hashlib
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc  # noqa: E402

SRC = r'E:/YURI/SAVE0665_修改.SAV'
PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


if not os.path.exists(SRC):
    print('样本不存在：%s —— 跳过' % SRC)
    sys.exit(0)

doc = SaveDoc(SRC)
ws = doc.weapon_objects()
print('样本 %s：武器 %d 把' % (os.path.basename(SRC), len(ws)))

tmp = os.path.join(HERE, '_ev300_out.csv')
tmp2 = os.path.join(HERE, '_ev300_diff.csv')
for p in (tmp, tmp2):
    if os.path.exists(p):
        os.remove(p)

h0 = hashlib.sha256(doc.plain).hexdigest()

# ---------------- ① 全表 ----------------
n_all, p_all = doc.export_weapons_csv(tmp)
ck(n_all == len(ws), '全表行数 = weapon_objects() 的 %d 把（实际 %d）' % (len(ws), n_all))
ck(os.path.exists(p_all), '全表文件真的落盘了')

with io.open(p_all, encoding='utf-8-sig', newline='') as f:
    rows = list(csv.reader(f))
head = rows[4]
ck(len(head) == 12, '表头 12 列（实际 %d）' % len(head))
ck(head[1] == '武器' and '差异' in head[-1], '表头含「武器」「差异」：%s' % head[-1])
ck(all(r[0].startswith('#') for r in rows[:4]), '前 4 行是来源注释（存档/原版基准/当前规则/单位说明）')
body = [r for r in rows[5:] if r]
ck(len(body) == n_all, '数据行 %d = 导出行数 %d' % (len(body), n_all))

# ---------------- ② / ③ 差异表 ----------------
n_diff, p_diff = doc.export_weapons_csv(tmp2, only_diff=True)
with io.open(p_diff, encoding='utf-8-sig', newline='') as f:
    drows = [r for r in list(csv.reader(f))[5:] if r]   # 头 4 行 + 表头 1 行
ck(len(drows) == n_diff, '差异表行数 %d = 返回的 %d' % (len(drows), n_diff))
ck(all(r[-1].strip() for r in drows), '差异表每一行的「差异」列都非空')

# 独立复算：不复用 export_weapons_csv 的任何判断
# ★基准必须是**官方快照**（导出就是这么算的），不是当前 rules
rules = doc._official_rules() or {}


def _num(kv, key):
    try:
        return float(str(kv.get(key, '')).split(';')[0].strip())
    except Exception:
        return None


expect = set()
for w in ws:
    kv = rules.get(w['name']) or {}
    if not kv:                      # 官方原版没有这把武器 ⇒ 也算差异（新增）
        expect.add(w['name'])
        continue
    od, orf, org = _num(kv, 'Damage'), _num(kv, 'ROF'), _num(kv, 'Range')
    if ((od is not None and int(od) != w['damage'])
            or (orf is not None and int(orf) != w['rof'])
            or (org is not None and abs(org - w['range_cells']) > 0.01)):
        expect.add(w['name'])
got = {r[1] for r in drows}
ck(got == expect, '差异集合与独立复算一致（导出 %d / 复算 %d，差集 %s）'
   % (len(got), len(expect), sorted(got ^ expect)[:4]))

# 全表里"差异列空"的行 = 总数 - 差异数
empty = {r[1] for r in body if not r[-1].strip()}
ck(len(empty) == len(ws) - len(expect),
   '全表里差异为空的有 %d 把，应等于 %d - %d' % (len(empty), len(ws), len(expect)))
ck(not (empty & expect), '差异为空的集合与差异集合不相交')

# ---------------- ④ 官方三列抽样 ----------------
by_name = {r[1]: r for r in body}
if 'PrismShot' in by_name:
    kv = rules.get('PrismShot') or {}
    r = by_name['PrismShot']
    ck(r[2] == str(ws and int(kv.get('Damage', -1))),
       'PrismShot 伤害列 %s = rules 的 %s' % (r[2], kv.get('Damage')))
    ck(abs(float(r[10]) - float(kv.get('Range', -99))) < 0.01,
       'PrismShot 官方射程列 %s = rules 的 %s' % (r[10], kv.get('Range')))

# ---------------- ⑤ Verses ----------------
whs = {x['name'] for x in doc.warhead_objects()}
with_v = [r for r in body if r[6] in whs]
ck(bool(with_v), '有 %d 行填了弹头名' % len(with_v))
ck(all(len([x for x in r[7].split(';') if x.strip()]) == 11 for r in with_v),
   '凡是填了弹头的行，Verses 都是 11 项')

# ---------------- ⑥ lepton 自洽 ----------------
# ⚠ 不能写成 `格×256 == lepton`：格数列是 2 位小数（给人看的），
#   1 lepton = 1/256 格 = 0.0039，四舍五入到 0.01 会差半个 lepton。
#   判据要反过来：lepton/256 与格数列的差 ≤ 0.005（即一个末位）。
bad = [r[1] for r in body if abs(float(r[4]) - int(r[5]) / 256.0) > 0.005]
ck(not bad, '射程「格」与 lepton 一致（不符：%s）' % bad[:3])

# ---------------- ⑦ 只读 ----------------
ck(hashlib.sha256(doc.plain).hexdigest() == h0, '导出前后明文 hash 一致（只读，没动存档）')

# ---------------- ⑧ 默认路径 ----------------
n3, p3 = doc.export_weapons_csv()
ck(os.path.dirname(os.path.abspath(p3)) == os.path.dirname(os.path.abspath(SRC)),
   '不传路径时落在存档同目录：%s' % os.path.basename(p3))
ck('武器表' in os.path.basename(p3), '默认文件名带「武器表」：%s' % os.path.basename(p3))
ck(n3 == n_all, '默认路径导出的行数与显式路径一致')

# ---------------- ⑨ INI 导出（★原版 rules 写法：武器 / 抛射体 / 弹头）----------------
# 玩家 2026-10-28 拿原版段落来对格式：
#   [120mmx] ;武器   /   [Cannon];抛射体   /   [ApocAP];弹头
# 所以导出要三段齐全、字段顺序跟原版一致，而不是只给武器。
tmpi = os.path.join(HERE, '_ev300_out.ini')
tmpi2 = os.path.join(HERE, '_ev300_diff.ini')
for p in (tmpi, tmpi2):
    if os.path.exists(p):
        os.remove(p)

ni, pi = doc.export_weapons_ini(tmpi)
raw = open(pi, 'rb').read()
try:
    itxt = raw.decode('gbk')
    ck(not raw.startswith(b'\xef\xbb\xbf'),
       'INI 是 GBK 且无 BOM（红警是 ANSI 程序，BOM 会污染第一个键名）')
except UnicodeDecodeError:
    itxt = raw.decode('utf-8-sig')
    ck(raw.startswith(b'\xef\xbb\xbf'), 'INI 退回 UTF-8 时带 BOM')
ck(raw.count(b'\n') == raw.count(b'\r\n'), 'INI 全是 CRLF（没有裸 LF）')

lines = itxt.split('\r\n')
secs = [l.strip() for l in lines if l.strip().startswith('[')]


def _sname(s):
    return s[1:s.index(']')] if ']' in s else ''


wsec = [_sname(s) for s in secs if s.endswith(';武器')]
whsec = [_sname(s) for s in secs if s.endswith(';弹头')]
pjsec = [_sname(s) for s in secs if s.endswith(';抛射体')]
ck(len(wsec) == len(ws), '武器段 %d 个 = %d 把' % (len(wsec), len(ws)))
ck(set(wsec) == {w['name'] for w in ws}, '武器段名集合 = weapon_objects()')
ck(bool(whsec), '带弹头段 %d 个（`[X];弹头`）' % len(whsec))
ck(bool(pjsec), '带抛射体段 %d 个（`[X];抛射体`）' % len(pjsec))
ck(ni == len(secs), '返回段数 %d = 文件里段行 %d（武器+抛射体+弹头）' % (ni, len(secs)))
ck('[WeaponTypes]' not in [s for s in secs if s.startswith('[WeaponTypes')],
   '不写 [WeaponTypes] 注册表（顺序错一位整个武器表错位）')

# 解析成 {段名: {键: 值}}（段名行后面的 `;武器` 不算键）
blocks, cur = {}, None
for l in lines:
    s = l.strip()
    if s.startswith('[') and ']' in s:
        cur = _sname(s)
        blocks[cur] = {}
    elif cur and '=' in s and not s.startswith(';'):
        k, v = s.split('=', 1)
        blocks[cur][k.strip()] = v.strip()

by = {w['name']: w for w in ws}
bad = []
for nm in list(by)[:8]:
    kv = blocks.get(nm) or {}
    w = by[nm]
    if int(kv.get('Damage', -1)) != w['damage']:
        bad.append('%s 伤害' % nm)
    if int(kv.get('ROF', -1)) != w['rof']:
        bad.append('%s 射速' % nm)
    if abs(float(kv.get('Range', -1)) - w['range_cells']) > 0.005:
        bad.append('%s 射程' % nm)
ck(not bad, '抽 8 段核对 Damage/ROF/Range 与存档一致（不符：%s）' % bad[:3])

# 原版格式的两条硬要求：武器段带 Projectile= / Warhead=
withpj = [n for n in wsec if 'Projectile' in (blocks.get(n) or {})]
withwh = [n for n in wsec if 'Warhead' in (blocks.get(n) or {})]
ck(len(withpj) >= len(wsec) * 0.9,
   '%d/%d 把武器写了 Projectile=（原版格式必有）' % (len(withpj), len(wsec)))
ck(len(withwh) >= len(wsec) * 0.9,
   '%d/%d 把武器写了 Warhead=（原版格式必有）' % (len(withwh), len(wsec)))

# 字段顺序：Damage → ROF → Range → Projectile → Speed → Warhead
order_bad = []
for nm in wsec[:20]:
    keys = list((blocks.get(nm) or {}).keys())
    head = [k for k in keys if k in ('Damage', 'ROF', 'Range', 'Projectile',
                                     'Speed', 'Warhead', 'Report', 'Anim',
                                     'Burst', 'Bright')]
    if head != sorted(head, key=lambda k: ['Damage', 'ROF', 'Range', 'Projectile',
                                           'Speed', 'Warhead', 'Report', 'Anim',
                                           'Burst', 'Bright'].index(k)):
        order_bad.append(nm)
ck(not order_bad, '武器段字段顺序与原版一致（不符：%s）' % order_bad[:3])

bad_v = [n for n in whsec
         if 'Verses' in (blocks.get(n) or {})
         and len([x for x in blocks[n]['Verses'].split(',') if x.strip()]) != 11]
ck(not bad_v, '弹头段 Verses 都是 11 项（不符：%s）' % bad_v[:3])
ck(all(('Image' in (blocks.get(n) or {})) or ('Arcing' in (blocks.get(n) or {}))
       or (';' in itxt) for n in pjsec), '抛射体段从 rules 抄到了字段')
ck(all(('CellSpread' in (blocks.get(n) or {})) for n in whsec[:20]),
   '弹头段都有 CellSpread=')

ni2, pi2 = doc.export_weapons_ini(tmpi2, only_diff=True)
itxt2 = open(pi2, 'rb').read().decode('gbk', errors='replace')
dsecs = [_sname(l.strip()) for l in itxt2.split('\r\n')
         if l.strip().startswith('[') and l.strip().endswith(';武器')]
ck(set(dsecs) == got, '差异 INI 的武器段 = 差异集合（%d 段）' % len(dsecs))
ck(hashlib.sha256(doc.plain).hexdigest() == h0, 'INI 导出同样没碰明文')

for p in (tmp, tmp2, p3, tmpi, tmpi2):
    try:
        os.remove(p)
    except Exception:
        pass

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    for m in FAIL:
        print('  ✗ %s' % m)
sys.exit(1 if FAIL else 0)
