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

# ---------------- ⑨ INI 导出（★玩家要求：更符合红警格式）----------------
tmpi = os.path.join(HERE, '_ev300_out.ini')
tmpi2 = os.path.join(HERE, '_ev300_diff.ini')
for p in (tmpi, tmpi2):
    if os.path.exists(p):
        os.remove(p)

ni, pi = doc.export_weapons_ini(tmpi)
raw = open(pi, 'rb').read()
ck(ni == len(ws), 'INI 段数 = %d 把（实际 %d）' % (len(ws), ni))
# 红警是 ANSI 程序 ⇒ 期望 GBK、无 BOM、全 CRLF；编不了才退回 UTF-8+BOM
try:
    itxt = raw.decode('gbk')
    ck(not raw.startswith(b'\xef\xbb\xbf'), 'INI 是 GBK 且无 BOM（红警原生，贴进 rulesmd.ini 不会因 BOM 出错）')
except UnicodeDecodeError:
    ck(raw.startswith(b'\xef\xbb\xbf'), 'INI 退回 UTF-8 时带 BOM')
    itxt = raw.decode('utf-8-sig')
ck(raw.count(b'\n') == raw.count(b'\r\n'), 'INI 全是 CRLF（没有裸 LF）')
ck(itxt.startswith(';'), '首行是注释（不会让引擎把 BOM/注释当段名）')

secs = [l.strip() for l in itxt.split('\r\n') if l.startswith('[')]
ck(len(secs) == ni, '段行数 %d = 返回段数 %d' % (len(secs), ni))
# 只看**真段行** —— 头注释里会出现「[WeaponTypes]」这个词，不能被当成段
ck('[WeaponTypes]' not in secs, '不写 [WeaponTypes] 注册表（写错会让整个武器表错位）')
ck(sorted(s[1:-1] for s in secs) == sorted(w['name'] for w in ws),
   '段名集合 = weapon_objects() 的名字集合')

# 逐段核对数值（抽 8 段，避免太慢）
blocks = {}
cur = None
for line in itxt.split('\r\n'):
    s = line.strip()
    if s.startswith('[') and s.endswith(']'):
        cur = s[1:-1]
        blocks[cur] = {}
    elif cur and '=' in s and not s.startswith(';'):
        k, v = s.split('=', 1)
        blocks[cur][k.strip()] = v.strip()
by = {w['name']: w for w in ws}
bad = []
for nm, kv in list(blocks.items())[:8]:
    w = by[nm]
    if int(kv.get('Damage', -1)) != w['damage']:
        bad.append('%s 伤害' % nm)
    if int(kv.get('ROF', -1)) != w['rof']:
        bad.append('%s 射速' % nm)
    if abs(float(kv.get('Range', -1)) - w['range_cells']) > 0.005:
        bad.append('%s 射程' % nm)
ck(not bad, '抽 8 段核对 Damage/ROF/Range 与存档一致（不符：%s）' % bad[:3])
ck(all(('Verses' not in kv)
       or len([x for x in kv['Verses'].split(',') if x.strip()]) == 11
       for kv in blocks.values()),
   'Verses 要么不写、要么正好 11 项')
ck(any('Warhead' in kv for kv in blocks.values()), '有段写了 Warhead=')

ni2, pi2 = doc.export_weapons_ini(tmpi2, only_diff=True)
itxt2 = open(pi2, 'rb').read().decode('gbk', errors='replace')
secs2 = {l.strip()[1:-1] for l in itxt2.split('\r\n')
         if l.startswith('[') and l.strip().endswith(']')}
ck(secs2 == got, '差异 INI 的段名 = 差异武器集合（%d 段）' % len(secs2))
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
