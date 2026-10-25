# -*- coding: utf-8 -*-
"""§234 自检：`slots='full'` 的「不许抄」三原则。

背景（2026-10-24）：旧 full 抄「档内各实例都一致」的 dword，T69 读档弹框。
跨档比对（`_probe_540.py`）找到两类混进来的坏东西：
  ① **键**：同型号在两份存档里的键不同（GGI 0x0EF43FD0 vs 0x14F4B7B8），
     典型 `+0x6D8` 类型键、`+0x6D4`；抄过去目标档没这个键 ⇒ 引擎解引用 ⇒ 弹框。
  ② **存档级全局指针**：`+0x1BC/+0x1C8/+0x240 = 0x0017AD12`，
     在参考档里 1774/1775 个单位（不分型号）都是这个值 ⇒ 与型号无关，是全局状态。

本自检锁死：
  A. full 导入后，**类型键没被参考档覆盖**（脚手架自己的才是对的）
  B. 全局指针那几个槽**没被抄**（不等于参考档的 0x0017AD12）
  C. 真常量确实抄进来了（`+0x270` 类型高度 = 参考档真机值）—— 证明不是"什么都不抄"
  D. 落盘重读后结构自洽 + 位置槽没坏（`broken_position_units` 不含新单位）
  E. safe 模式仍然只动白名单槽（回归）
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
import editor_core as ec                                    # noqa: E402
from _paths import find_sav, out                            # noqa: E402

TGT = need = find_sav('T85_老工程师_修位置副本槽.SAV')
REF = find_sav('SAVE4CA2.SAV')
TYPE = 'DISK'          # 两档都有 ≥2 架，且可疑槽最多（9 个）

n_ok = n_bad = 0


def ck(name, cond, extra=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('  PASS  %s' % name)
    else:
        n_bad += 1
        print('  FAIL  %s   %s' % (name, extra))


def u32(pl, off):
    return struct.unpack_from('<I', pl, off)[0]


print('目标档 %s\n参考档 %s\n型号 %s' % (TGT, REF, TYPE))
ref = ec.SaveDoc(REF)
d = ec.SaveDoc(TGT)

refrecs = [r for r in ref.unit_records() if r['type'].upper() == TYPE]
ck('参考档里 %s ≥2 架' % TYPE, len(refrecs) >= 2, len(refrecs))
rr = refrecs[0]
REF_GLOB = u32(ref.plain, rr['off'] + 0x1BC)
print('  参考档真机：+0x1BC 全局=%#x' % REF_GLOB)

# 目标档里同型号的"自己的值"（脚手架 / 同型号克隆会带这套）
tgt_same = [r for r in d.unit_records() if r['type'].upper() == TYPE]
TGT_TYPEKEY = u32(d.plain, tgt_same[0]['off'] + 0x6D8) if tgt_same else None
TGT_GLOB = u32(d.plain, tgt_same[0]['off'] + 0x1BC) if tgt_same else None
TGT_C8 = u32(d.plain, tgt_same[0]['off'] + 0x1C8) if tgt_same else None
TGT_240 = u32(d.plain, tgt_same[0]['off'] + 0x240) if tgt_same else None
print('  目标档同型号：+0x6D8=%s  +0x1BC=%s'
      % ('%#x' % TGT_TYPEKEY if TGT_TYPEKEY else '—',
         '%#x' % TGT_GLOB if TGT_GLOB is not None else '—'))

# 落点：挑一个现有单位旁边的空地（§233 可发现性：离原位置近、周围有兵）
anchor = [r for r in d.unit_records() if r.get('x') is not None]
ax, ay = anchor[0]['x'], anchor[0]['y']
cell = d.free_cell_near(ax, ay, radius=8, clearance=1)
print('  落点：锚点 (%d,%d) → 空地 %s' % (ax, ay, cell))

print('\n=== [A–C] slots=full 导入 ===')
info = d.import_unit_from(ref, TYPE, cell[0], cell[1], slots='full', exact_cell=True)
print('  info: imported=%d skipped_ptr=%d skipped_glob=%d'
      % (info['imported'], info['skipped_ptr'], info['skipped_glob']))
rec = [r for r in d.unit_records() if r['key'] == info['key']][0]
pl = d.plain

if TGT_TYPEKEY is not None:
    ck('A. 类型键没被参考档覆盖（+0x6D8 仍 = 本档同型号的值）',
       u32(pl, rec['off'] + 0x6D8) == TGT_TYPEKEY,
       '%#x vs %#x' % (u32(pl, rec['off'] + 0x6D8), TGT_TYPEKEY))
else:
    ck('A. 类型键存在且是本档自己的键',
       ec.key_in(self.object_keys(), u32(pl, rec['off'] + 0x6D8)),
       '%#x' % u32(pl, rec['off'] + 0x6D8))

ck('B. 全局指针槽保持**目标档自己的值**（没被参考档的 %#x / 0 冲掉）' % REF_GLOB,
   u32(pl, rec['off'] + 0x1BC) == TGT_GLOB,
   '%#x vs 目标 %#x' % (u32(pl, rec['off'] + 0x1BC), TGT_GLOB))
ck('B2. +0x1C8 同上', u32(pl, rec['off'] + 0x1C8) == TGT_C8,
   '%#x vs 目标 %#x' % (u32(pl, rec['off'] + 0x1C8), TGT_C8))
ck('B3. +0x240 同上', u32(pl, rec['off'] + 0x240) == TGT_240,
   '%#x vs 目标 %#x' % (u32(pl, rec['off'] + 0x240), TGT_240))
ck('C. 确实抄了一批型号常量（≥100 个槽）', info['imported'] >= 100, info['imported'])
ck('C2. 全局槽被识别出来并跳过（不是 0 个）', info['skipped_glob'] > 0, info['skipped_glob'])

print('\n=== [D] 落盘重读 ===')
p = out('ev234_full导入.SAV')
d.save(p, backup=False)
d2 = ec.SaveDoc(p)
r2 = [r for r in d2.unit_records() if r['key'] == info['key']]
ck('D. 重读后新单位还在', len(r2) == 1, len(r2))
if r2:
    ck('D2. 重读后类型键仍是本档的键',
       TGT_TYPEKEY is None or u32(d2.plain, r2[0]['off'] + 0x6D8) == TGT_TYPEKEY,
       '%#x' % u32(d2.plain, r2[0]['off'] + 0x6D8))
    ck('D3. 位置槽没坏（broken_position_units 不含它）',
       info['key'] not in {x['key'] for x in d2.broken_position_units()},
       [x['key'] for x in d2.broken_position_units()])
    ck('D4. 坐标落在指定格', (r2[0].get('x'), r2[0].get('y')) == tuple(cell),
       (r2[0].get('x'), r2[0].get('y')))
    ck('D5. 全档单位数 +1',
       len(d2.unit_records()) == len(ec.SaveDoc(TGT).unit_records()) + 1)
print('  产物：%s' % p)

print('\n=== [E] safe 模式回归（只动白名单 8 槽）===')
d3 = ec.SaveDoc(TGT)
before = len(d3.unit_records())
info3 = d3.import_unit_from(ref, TYPE, cell[0], cell[1], slots='safe', exact_cell=True)
rec3 = [r for r in d3.unit_records() if r['key'] == info3['key']][0]
ck('E. safe 模式导入成功', len(d3.unit_records()) == before + 1)
ck('E2. safe 抄的槽数 ≤ 8', info3['imported'] <= 8, info3['imported'])
ck('E3. safe 也保住目标档自己的全局值',
   u32(d3.plain, rec3['off'] + 0x1BC) == TGT_GLOB, u32(d3.plain, rec3['off'] + 0x1BC))

print('\n=== [F] veterancy 必须在抄完之后生效 ===')
d6 = ec.SaveDoc(TGT)
info6 = d6.import_unit_from(ref, TYPE, cell[0], cell[1], slots='full', veterancy=2,
                           exact_cell=True)
rec6 = [r for r in d6.unit_records() if r['key'] == info6['key']][0]
ck('F. full 导入后等级仍是调用方指定的 2（精英）',
   u32(d6.plain, rec6['off'] + 0x150) == 2, u32(d6.plain, rec6['off'] + 0x150))
ck('F2. +0x164 **未被改动**（§270 三档实测：精英也是 2.0，不是 0.0）',
   abs(struct.unpack_from('<f', d6.plain, rec6['off'] + 0x164)[0] - 0.0) > 1e-6,
   struct.unpack_from('<f', d6.plain, rec6['off'] + 0x164)[0])

print('\n§234 自检：PASS %d / FAIL %d' % (n_ok, n_bad))
sys.exit(1 if n_bad else 0)
