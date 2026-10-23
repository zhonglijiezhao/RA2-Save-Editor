# -*- coding: utf-8 -*-
"""§200 自检：单位"移动方式"必须跟真实样本一致（基洛夫 = Fly，不许钉成 Drive）。

锁死 2026-10-23 玩家实测两轮纠错：
  · 第一轮：基洛夫只看到一个等级徽章、模型不出来 —— 当时误判成"移动方式不对"，
    把 ZEP 钉成 Drive（`_LOCO_OVERRIDE`），结果第二轮玩家反馈
    **「基洛夫出来了，移动方式不对，不起飞，在地面移动」**。
  · 复核 `放V3和ZEP.SAV`（玩家自己在地图上摆的真基洛夫）：伴随 = **Fly / 173 B**
    （`462c6192…`），与同档 62 架 SCHP 完全一致 ⇒ rules 的 `Locomotor=` 就是对的，
    当初"隐形"的真因是**落点贴着地图边缘**。
  · 结论：`_LOCO_OVERRIDE` 必须为空；`_type_locomotor('ZEP')` 必须 == 'Fly'。

用法：python _selftest_ev200.py [含真基洛夫的存档]
"""
from __future__ import print_function

import glob
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

try:
    from core.editor_core import SaveDoc
except Exception:
    from editor_core import SaveDoc

n_ok = n_bad = 0
fails = []


def ck(name, cond, extra=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('  [OK]   %s %s' % (name, ('(%s)' % extra) if extra else ''))
    else:
        n_bad += 1
        fails.append(name)
        print('  [FAIL] %s  %s' % (name, extra))


def pick_sav():
    if len(sys.argv) > 1:
        return sys.argv[1]
    c = glob.glob('E:/YURI/**/放V3和ZEP.SAV', recursive=True)
    if c:
        return c[0]
    for p in glob.glob('E:/YURI/**/*.SAV', recursive=True):
        try:
            d = SaveDoc(p)
            if any(r['type'].upper() == 'ZEP' for r in d.unit_records()):
                return p
        except Exception:
            continue
    return ''


SAV = pick_sav()
print('=' * 72)
print('§200 自检：移动方式（Locomotor）')
ck('找到含真基洛夫的存档', bool(SAV), SAV)
if not SAV:
    sys.exit(1)

# ---------- [1] 覆盖表必须为空 ----------
ck('★`_LOCO_OVERRIDE` 为空（不许再钉 ZEP=Drive）',
   not getattr(SaveDoc, '_LOCO_OVERRIDE', None),
   str(getattr(SaveDoc, '_LOCO_OVERRIDE', None)))

d0 = SaveDoc(SAV)
recs0 = d0.unit_records()
zeps = [r for r in recs0 if r['type'].upper() == 'ZEP']
ck('档内有真基洛夫', bool(zeps), '%d 个' % len(zeps))
real = zeps[0]
ck('真基洛夫伴随 = Fly（173 B）',
   real['loco'] == 'Fly' and real['comp_size'] == 173,
   '%s / %d B' % (real['loco'], real['comp_size']))
ck('同档直升机 SCHP 也是 Fly（对照）',
   any(r['type'].upper() == 'SCHP' and r['loco'] == 'Fly' for r in recs0), '')

# ---------- [2] 目标移动方式判定 ----------
d = SaveDoc(SAV)
ck('`_type_locomotor("ZEP")` == Fly', d._type_locomotor('ZEP', d.unit_records()) == 'Fly',
   d._type_locomotor('ZEP', d.unit_records()))
ck('`_type_locomotor("MTNK")` == Drive（地面载具不受影响）',
   d._type_locomotor('MTNK', d.unit_records()) == 'Drive',
   d._type_locomotor('MTNK', d.unit_records()))
r_zep = d._type_rules('ZEP')
ck('rules 里 ZEP 的 Locomotor 能读出来', bool(r_zep.get('LOCOMOTOR')), r_zep.get('LOCOMOTOR', ''))

# ---------- [3] 同型号克隆：移动方式、高度、血量都不许被带歪 ----------
cell = d.free_cell_near(real['x'] or 40, (real['y'] or 179) + 1, radius=12, clearance=1)
ck('找到空位', cell is not None, str(cell))
info = d.add_unit_by_clone('ZEP', cell[0], cell[1], house=d.player_house(), veterancy=None)
ck('克隆成功（无告警）', not info['warnings'], str(info['warnings']))
new = [r for r in d.unit_records() if r['key'] == info['key']][0]
ck('新基洛夫伴随 = Fly / 173 B',
   new['loco'] == 'Fly' and new['comp_size'] == 173,
   '%s / %d B' % (new['loco'], new['comp_size']))
pl = d.plain
old = [r for r in d.unit_records() if r['key'] == real['key']][0]
a = bytes(pl[old['off']:old['off'] + old['size'] + old['comp_size']])
b = bytes(pl[new['off']:new['off'] + new['size'] + new['comp_size']])
diff = [o for o in range(0, len(a) - 3, 4) if a[o:o + 4] != b[o:o + 4]]
ck('与真基洛夫逐 dword 差异 ≤ 20（实际 %d）' % len(diff), len(diff) <= 20, str(len(diff)))
ck('★高度槽 +0xB8 未被带歪（按模板原值）', a[0xB8:0xBC] == b[0xB8:0xBC],
   '%s vs %s' % (a[0xB8:0xBC][::-1].hex(), b[0xB8:0xBC][::-1].hex()))
ck('★高度槽 +0x270 未被带歪', a[0x270:0x274] == b[0x270:0x274],
   '%s vs %s' % (a[0x270:0x274][::-1].hex(), b[0x270:0x274][::-1].hex()))
ck('血量 = 真基洛夫的满血值', a[0x80:0x84] == b[0x80:0x84] and a[0x84:0x88] == b[0x84:0x88],
   '%d' % struct.unpack_from('<I', b, 0x80)[0])
ck('不报"派生子单位"（ZEP 没有 SPAWNS）', d._type_spawns('ZEP') == '',
   d._type_spawns('ZEP'))

# ---------- [4] 落盘往返 ----------
out = os.path.join(HERE, '_selftest_out', 'ev200_基洛夫Fly.SAV')
if not os.path.isdir(os.path.dirname(out)):
    os.makedirs(os.path.dirname(out))
d.save(out, backup=False)
d2 = SaveDoc(out)
z2 = [r for r in d2.unit_records() if r['key'] == info['key']]
ck('落盘重读：基洛夫还在', bool(z2), '')
ck('落盘重读：仍是 Fly / 173 B',
   bool(z2) and z2[0]['loco'] == 'Fly' and z2[0]['comp_size'] == 173,
   '%s / %s' % (z2[0]['loco'], z2[0]['comp_size']) if z2 else '')
ck('落盘重读：坐标正确', bool(z2) and (z2[0]['x'], z2[0]['y']) == (cell[0], cell[1]),
   str((z2[0]['x'], z2[0]['y']) if z2 else ''))

print('-' * 72)
print('§200 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
