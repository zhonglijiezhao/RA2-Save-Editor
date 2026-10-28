# -*- coding: utf-8 -*-
"""§295 自检：存档里的武器 / 弹头类型表（只读）。

为什么单独立一套（2026-10-28 玩家问"单位和防御建筑的武器/抛射体/弹头是什么、能不能改"）：
  存档里其实有一张**全局武器类型表**，每个 WeaponType 对象带完整数值，不是只有名字。
  实测布局（`X` = 第一份名字缓冲起点）：
      X+0x00  ID 串（64B 定长）   X+0x40  Name 串（同一份，64B）
      X+0x80  Damage u32          X+0x8C  ROF u32       X+0x90  Range u32（lepton，1 格=256）
      弹头：X+0x7C 起 **11 个 double** 的 Verses

  本自检把这套结论钉死：值必须和 rulesmd.ini 逐项对上，且不能把阵营对象当成弹头
  （第一版就踩了：`Americans` 等 5 个阵营对象被误判成弹头）。

跑法：python _selftest_ev295_weapon_table.py
"""
import io
import os
import sys
import time

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

SaveDoc.set_game_dir(r'E:/YURI')
t0 = time.time()
d = SaveDoc(SRC)
ck(time.time() - t0 < 60, '存档已载入（%.1fs）' % (time.time() - t0))

ck(len(SaveDoc.ARMOR_ORDER) == 11, 'ARMOR_ORDER 有 11 项（Verses 的 11 个百分比）')
ck(SaveDoc.ARMOR_ORDER[5] == 'heavy' and SaveDoc.ARMOR_ORDER[7] == 'steel',
   '装甲顺序正确（第 6 项 heavy / 第 8 项 steel）')

# ── ① 扫描性能 ────────────────────────────────────────────
t0 = time.time()
ws = d.weapon_objects()
t_scan = time.time() - t0
ck(t_scan < 1.5, '武器表扫描 %.0f ms（<1500ms；逐字节版要 4200ms）' % (t_scan * 1000))
ck(len(ws) > 150, '识别出 %d 把武器（>150）' % len(ws))

names = {x['name']: x for x in ws}
ck(len(names) == len(ws), '武器名无重复（%d 个唯一）' % len(names))

# ── ② 关键武器逐项对 rules ─────────────────────────────────
# (名字, Damage, ROF, Range 格) —— 全部来自 rulesmd.ini 实测
CASES = [('M60', 15, 20, 4.0),
         ('M60E', 25, 20, 4.0),
         ('Vulcan2', 50, 26, 5.5),
         ('PrismShot', 120, 45, 8.0),
         ('105mm', 65, 60, 5.0),
         ('105mmE', 65, 50, 5.0)]
for nm, dmg, rof, rng in CASES:
    x = names.get(nm)
    if not x:
        ck(False, '%s 应该在武器表里' % nm)
        continue
    ck(x['damage'] == dmg and x['rof'] == rof and abs(x['range_cells'] - rng) < 1e-6,
       '%s: Damage %d / ROF %d / Range %.2f 格（实得 %d/%d/%.2f）'
       % (nm, dmg, rof, rng, x['damage'], x['rof'], x['range_cells']))

# 数字开头的名字必须能扫到（第一版漏了 36 把）
ck('105mm' in names, '数字开头的武器名（105mm）能被扫到')
ck(any(n[0].isdigit() for n in names), '武器表里确实有数字开头的名字')

# ── ③ 弹头：Verses 11 项，且不能把阵营对象当弹头 ─────────────
whs = {x['name']: x for x in d.warhead_objects()}
ck(len(whs) > 50, '识别出 %d 个弹头（>50）' % len(whs))
for bad in ('Americans', 'Alliance', 'French', 'Germans', 'British', 'Russians'):
    ck(bad not in whs, '阵营对象 %s 没有被误判成弹头' % bad)

WANT = {
    'SA': [1, .8, .8, .5, .25, .25, .75, .5, .25, 1, 1],
    'AP': [.25, .25, .15, .75, 1, 1, .65, .45, .6, .6, 1],
    'PrismWarhead': [2, 1, 1, 1, 1, 1, .5, .5, .5, 2, 1],
}
for nm, want in WANT.items():
    x = whs.get(nm)
    if not x:
        ck(False, '弹头 %s 应该在表里' % nm)
        continue
    ck(len(x['verses']) == 11, '%s: Verses 是 11 项' % nm)
    ck(all(abs(a - b) < 1e-9 for a, b in zip(x['verses'], want)),
       '%s: Verses 逐项对得上 rules（%s）'
       % (nm, '/'.join('%g' % (v * 100) for v in x['verses'])))

# ── ④ 武器 → 弹头 映射 ─────────────────────────────────────
mp = d.weapon_warhead_map()
ck(mp.get('PrismShot') == 'PrismWarhead', 'PrismShot → PrismWarhead')
ck(mp.get('105mm') == 'AP', '105mm → AP')
ck(mp.get('M60') == 'SA', 'M60 → SA')
ck(mp.get('Vulcan2') == 'SA', 'Vulcan2 → SA')

# ── ⑤ 缓存要能被 invalidate_caches 清掉（偏移变了必须重扫）─────
d._typeobjs_memo = None
d._wpnwh_memo = None
d.invalidate_caches()
ck(getattr(d, '_typeobjs_memo', None) is None, 'invalidate_caches 会清掉类型表缓存')
ws2 = d.weapon_objects()
ck(len(ws2) == len(ws), '重扫结果一致（%d 把）' % len(ws2))

# ── ⑥ 只读：这两个接口不许改明文 ───────────────────────────
before = bytes(d.plain)
d.weapon_objects()
d.warhead_objects()
d.weapon_warhead_map()
ck(bytes(d.plain) == before, '三个查询接口都是只读（明文未变）')

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败项：')
    for f in FAIL:
        print('  - ' + f)
sys.exit(1 if FAIL else 0)
