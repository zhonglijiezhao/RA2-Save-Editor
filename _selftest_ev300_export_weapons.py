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
head = rows[3]
ck(len(head) == 12, '表头 12 列（实际 %d）' % len(head))
ck(head[1] == '武器' and '差异' in head[-1], '表头含「武器」「差异」：%s' % head[-1])
ck(all(r[0].startswith('#') for r in rows[:3]), '前 3 行是来源注释（存档/规则/单位说明）')
body = [r for r in rows[4:] if r]
ck(len(body) == n_all, '数据行 %d = 导出行数 %d' % (len(body), n_all))

# ---------------- ② / ③ 差异表 ----------------
n_diff, p_diff = doc.export_weapons_csv(tmp2, only_diff=True)
with io.open(p_diff, encoding='utf-8-sig', newline='') as f:
    drows = [r for r in list(csv.reader(f))[4:] if r]
ck(len(drows) == n_diff, '差异表行数 %d = 返回的 %d' % (len(drows), n_diff))
ck(all(r[-1].strip() for r in drows), '差异表每一行的「差异」列都非空')

# 独立复算：不复用 export_weapons_csv 的任何判断
rules = doc._rules_all() or {}


def _num(kv, key):
    try:
        return float(str(kv.get(key, '')).split(';')[0].strip())
    except Exception:
        return None


expect = set()
for w in ws:
    kv = rules.get(w['name']) or {}
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

for p in (tmp, tmp2, p3):
    try:
        os.remove(p)
    except Exception:
        pass

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    for m in FAIL:
        print('  ✗ %s' % m)
sys.exit(1 if FAIL else 0)
