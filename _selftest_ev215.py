# -*- coding: utf-8 -*-
"""§215 自检：跨型号放**载具**打通了 —— 关键是"骨架必须挑状态干净的"。

玩家实测（2026-10-23，`T72_飞碟骨架_基洛夫可动.SAV`，基档 `平地三V3.SAV`）：
> 「飞碟和基洛夫都在空中 功能正常 打掉也不弹框。」

此前所有失败都指向同一件事：**克隆继承的是模板的"当前状态"**（§214）。
  · 拿 `SCHP` 武直当骨架（实测它 `+0x10C=13/137`、`+0x11C=9`、`+0x120=9` = 已部署/任务中）
    ⇒ 克隆出来的武直趴在地上；拼出来的基洛夫"在空中却不能移动、空血爆一次不死"。
  · 换 `DISK` 飞碟当骨架（`+0x10C/0x11C/0x120` 全 0、`+0xB8=1328/+0x270=1248` = 真在天上）
    ⇒ 基洛夫能飞、能移动、被打掉正常死亡、不弹框。

本自检锁死四条：
  ① `_pick_scaffold()` 在"飞"这个移动方式下必须挑到**状态干净**的实例（不能挑部署着的）；
     ▶ 用 `平地三V3.SAV`（有 DISK，也有大量部署中的 SCHP）
  ② **同型号分支**也走打分（`SCHP` 同型号克隆不能挑中"已部署"的那一架）← 本次修掉的代码 bug
  ③ `import_unit_from(..., slots='safe')` 出来的基洛夫状态指纹 == 真基洛夫
  ④ 落盘往返后指纹不变

用法：python _selftest_ev215.py [目标档(无 ZEP)] [参考档(有真 ZEP)]
"""
from __future__ import print_function

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


def fp(*names):
    """玩家会把测试档挪到 `新建文件夹 (29)` ⇒ 两个目录都找。"""
    for n in names:
        for d in ('E:/YURI', 'E:/YURI/新建文件夹 (29)'):
            q = os.path.join(d, n)
            if os.path.isfile(q):
                return q
    return names[0]


SAV = sys.argv[1] if len(sys.argv) > 1 else fp('平地三V3.SAV')
REF = sys.argv[2] if len(sys.argv) > 2 else fp('有空地.SAV')

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


def fp_of(d, r):
    """一条单位记录的"状态指纹"：(当前高度, 类型高度, 0x10C, 0x11C, 0x120, 目标, 目的地x, 目的地y)"""
    n = r['size'] + r['comp_size']
    b = bytes(d.plain[r['off']:r['off'] + n])
    u = lambda o: struct.unpack_from('<I', b, o)[0]
    return (u(0xB8), u(0x270), u(0x10C), u(0x11C), u(0x120), u(0x198), u(0x658), u(0x680))


def idle(d, r):
    """★§215 定案口径：`+0x10C/+0x11C/+0x120` 全 0 = 模板处于"空闲"。

    这三项就是 T71（13/9/9，失败）与 T72（0/0/0，成功）之间**唯一**的差别 ——
    同档、同落点、同型号、同高度都对齐了。目标指针 `+0x198` 不参与判定（放置时会清）。"""
    _alt, _tal, a, b_, c, _tgt, _dx, _dy = fp_of(d, r)
    return a == 0 and b_ == 0 and c == 0


print('=' * 72)
print('§215 自检：骨架状态 → 跨型号载具能否真正可用')
for f in (SAV, REF):
    ck('文件存在 %s' % os.path.basename(f), os.path.isfile(f), f)
if not all(os.path.isfile(f) for f in (SAV, REF)):
    sys.exit(1)

d = SaveDoc(SAV)
ck('目标档里本来没有 ZEP（才是跨型号场景）',
   not any(r['type'].upper() == 'ZEP' for r in d.unit_records()),
   '%d 个单位' % len(d.unit_records()))

# ---------- ① 打分必须挑"状态干净"的 ----------
fly = [r for r in d.unit_records() if r.get('loco') == 'Fly']
ck('目标档里有"飞"的单位可当骨架', len(fly) > 0, '%d 个' % len(fly))
dirty = [r for r in fly if not idle(d, r)]
ck('★档里确实存在"脏状态"的飞行单位（部署中/任务中）', len(dirty) > 0,
   '%d 个脏 / %d 个总' % (len(dirty), len(fly)))

picked = d._pick_scaffold(fly, 'Fly')
ck('打分给出了模板', picked is not None, picked['type'] if picked else '')
if picked:
    ck('★★挑中的模板处于空闲（0x10C/0x11C/0x120 全 0 —— 这就是 T72 成功的那一条）',
       idle(d, picked), '0x10C=%d 0x11C=%d 0x120=%d' % fp_of(d, picked)[2:5])
    ck('★挑中的模板确实在天上（+0xB8 > 0）', fp_of(d, picked)[0] > 0, str(fp_of(d, picked)[0]))
    ck('★挑中的不是"部署中/任务中"的那 67 架之一（它们的 0x11C=9）',
       fp_of(d, picked)[3] == 0, '0x11C=%d' % fp_of(d, picked)[3])
    ck('挑中的在登记表里（加分项）', d._pick_registered([picked]) is not None, '')

# ---------- ② 同型号分支也走打分（本次修的 bug） ----------
schps = [r for r in d.unit_records() if r['type'].upper() == 'SCHP']
ck('档里有武直 SCHP（对照用）', len(schps) > 0, '%d 架' % len(schps))
if schps:
    ck('★武直里确实有"已部署"的（0x11C != 0）',
       any(fp_of(d, r)[3] != 0 for r in schps),
       '%d 架部署中' % sum(1 for r in schps if fp_of(d, r)[3] != 0))
    best = d._pick_scaffold(schps, 'Fly')
    ck('★★同型号分支也按状态打分（挑到的武直 0x11C == 0）',
       best is not None and fp_of(d, best)[3] == 0,
       '0x11C=%d（旧实现会挑到 9 的那一架）' % (fp_of(d, best)[3] if best else -1))

# ---------- ③ 直接写入法出来的基洛夫，指纹必须与真机一致 ----------
ref = SaveDoc(REF)
real = [r for r in ref.unit_records() if r['type'].upper() == 'ZEP' and r['comp_size'] == 173]
ck('参考档里有真基洛夫（173B 伴随）', len(real) > 0, '%d 架' % len(real))
if not real:
    sys.exit(1)
real = sorted(real, key=lambda r: r['y'])[0]
rt = fp_of(ref, real)
ck('真基洛夫指纹 = 有高度 / 类型高度 1248 / 无残余指令',
   rt[0] > 0 and rt[1] == 1248 and rt[2] == 0 and rt[3] == 0 and rt[4] == 0,
   '高度=%d/%d 0x10C=%d 0x11C=%d 0x120=%d' % rt[:5])

cell = d.free_cell_near(30, 182, radius=8, clearance=1) or (30, 182)
info = d.import_unit_from(REF, 'ZEP', cell[0], cell[1], house=d.player_house(), veterancy=0)
ck('导入成功（默认 slots="safe"）', bool(info.get('key')), '#%x' % (info.get('key') or 0))
_sc = [r for r in d.unit_records() if r['key'] == info.get('scaffold_key')] if info.get('scaffold_key') else []
ck('骨架（由调用方记录）处于空闲状态',
   bool(_sc) and idle(d, _sc[0]) if _sc else True,
   info.get('template_type', ''))
new = [r for r in d.unit_records() if r['key'] == info['key']][0]
nt = fp_of(d, new)
ck('★新基洛夫的残余指令全 0（能与真机一样正常行动）',
   nt[2] == 0 and nt[3] == 0 and nt[4] == 0,
   '0x10C=%d 0x11C=%d 0x120=%d' % nt[2:5])
# ★§248.8：`+0x198` **不清 0** —— 它是型号级共享值（T85 飞碟 97/98 = 0x7093df，
#   玩家地编放进去、引擎亲手造的那台也是同一个值）。这里改成"与同型号众数一致"。
_m198 = {}
for _r in d.unit_records():
    if _r['type'].upper() == 'ZEP':
        _v = struct.unpack_from('<I', d.plain, _r['off'] + 0x198)[0]
        _m198[_v] = _m198.get(_v, 0) + 1
_modal198 = max(_m198.items(), key=lambda kv: kv[1])[0] if _m198 else 0
ck('★+0x198 与同型号众数一致（型号级共享值，不清 0）',
   nt[5] == _modal198, '%#x vs 众数 %#x' % (nt[5], _modal198))
ck('★新基洛夫没有目的地（不会自己跑去打人）',
   nt[6] == 0 and nt[7] == 0, '目的地=(%d,%d)' % (nt[6] >> 8, nt[7] >> 8))
ck('★新基洛夫在空中（+0xB8 == +0x270，都在 1248 一带）',
   nt[0] > 0 and nt[0] == nt[1], '%d / %d' % (nt[0], nt[1]))
ck('★类型高度与真机一致', nt[1] == rt[1], '%d vs %d' % (nt[1], rt[1]))
ck('伴随 = Fly / 173B（与真机同类）',
   new['loco'] == 'Fly' and new['comp_size'] == 173,
   '%s / %d' % (new['loco'], new['comp_size']))

# ---------- ④ 落盘往返 ----------
out = os.path.join(HERE, '_selftest_out', 'ev215_飞碟骨架.SAV')
if not os.path.isdir(os.path.dirname(out)):
    os.makedirs(os.path.dirname(out))
d.save(out, backup=False)
d2 = SaveDoc(out)
z2 = [r for r in d2.unit_records() if r['key'] == info['key']]
ck('落盘重读：记录还在', bool(z2), '')
if z2:
    nt2 = fp_of(d2, z2[0])
    ck('落盘重读：状态指纹不变', nt2 == nt, '%s vs %s' % (nt2[:5], nt[:5]))
    ck('落盘重读：仍在目标格', (z2[0]['x'], z2[0]['y']) == (cell[0], cell[1]),
       str((z2[0]['x'], z2[0]['y'])))

print('-' * 72)
print('§215 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
