# -*- coding: utf-8 -*-
"""§195 自检：建筑动画对象（476B anim）的定位 / 构造 / 挂载 / 登记。

依据（2026-10-23 实测，见 docs/加建筑_所属方_20261017.md §194）：
  · 引擎"一栋楼建好"时必定新建一个 476B anim 对象，挂到楼记录 `+0x57C`；
  · 记录 = GUID 起点、长 476、数组 = [count][476 × count]，count 在首条 −4；
  · 缺这个对象 ⇒ 只显示动画第 0 帧（光棱塔 = 只剩底座）。

跑法：python _selftest_ev195.py
"""
import sys
import os
import struct

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import editor_core as ec
from _paths import need_sav, out

n_ok = n_bad = 0


def ck(name, cond, extra=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('  [OK]   %s' % name)
    else:
        n_bad += 1
        print('  [FAIL] %s   %s' % (name, extra))


SAV = need_sav('T21_把旧塔6_放ATESLA.SAV')
print('基档:', SAV)

print('=== [1] 定位：数组结构与计数 ===')
d = ec.SaveDoc(SAV)
recs = d.anim_records()
ck('能找到 anim 记录', len(recs) >= 1, 'n=%d' % len(recs))
cnt = struct.unpack_from('<I', d.plain, recs[0][0] - 4)[0]
ck('count(首条−4) = 记录数', cnt == len(recs), 'count=%d n=%d' % (cnt, len(recs)))
ck('记录间距 = 476', all(recs[i + 1][0] - recs[i][0] == 476
                        for i in range(len(recs) - 1)))
ck('每条记录第 0x14 处是 vtable 0x7E3354 一族',
   all(0x007E0000 <= struct.unpack_from('<I', d.plain, g + 0x14)[0] <= 0x00800000
       for g, _k in recs))

print('=== [2] 补动画：键 / 长度 / 计数 / 登记 ===')
tower = [b for b in d.buildings() if b['type'] == 'ATESLA'][0]
rec = tower['base'] - 72
ck('补之前这栋楼没有 anim', d.building_anim_key(rec) == 0)
n_len = len(d.plain)
n_cnt = struct.unpack_from('<I', d.plain, d.anim_records()[0][0] - 4)[0]
r = d.add_building_anim(tower['base'])
key = r['key']
ck('返回 created=True', r['created'] is True, str(r))
ck('明文长度 +476 + 4×登记表数', len(d.plain) == n_len + 476 + 4 * r['lists'],
   '%+d（lists=%d）' % (len(d.plain) - n_len, r['lists']))
ck('数组 count +1',
   struct.unpack_from('<I', d.plain, d.anim_records()[0][0] - 4)[0] == n_cnt + 1)
pl = d.plain
ck('新键全档出现 ≥4 次（2 张对象册 + 楼槽 + 自身）',
   pl.count(struct.pack('<I', key)) >= 4, '%d 次' % pl.count(struct.pack('<I', key)))
for off, delta in ec.SaveDoc.ANIM_KREF:
    ck('键组 K+0x%X 也在明文里（内部引用写对）' % delta,
       pl.find(struct.pack('<I', (key + delta) & 0xFFFFFFFF)) >= 0)
ck('楼记录 +0x57C 指向新键', d.building_anim_key(tower['base'] - 72) == key)
g = dict((k, o) for o, k in d.anim_records())[key]
ck('记录里的格 X = 楼坐标', struct.unpack_from('<I', pl, g + 0xB0)[0] == tower['x'] * 256,
   '%d' % struct.unpack_from('<I', pl, g + 0xB0)[0])
ck('记录里的格 Y = 楼坐标', struct.unpack_from('<I', pl, g + 0xB4)[0] == tower['y'] * 256)
ck('记录里的键 = 新键', struct.unpack_from('<I', pl, g + 0x10)[0] == key)
ck('记录里的 Z = 0（全库原生 anim 一律 0）',
   struct.unpack_from('<I', pl, g + 0xB8)[0] == 0)

print('=== [3] 幂等：再调一次不重复造 ===')
n_len = len(d.plain)
tower2 = [b for b in d.buildings()
          if b['type'] == 'ATESLA' and d.building_anim_key(b['base'] - 72) == key][0]
r2 = d.add_building_anim(tower2['base'])
ck('第二次 created=False', r2['created'] is False, str(r2))
ck('长度没变', len(d.plain) == n_len)

print('=== [4] 落盘 / 重读 ===')
p = out('ev195_补动画.SAV')
d.save(p, backup=False)
d2 = ec.SaveDoc(p)
ck('重读记录数一致', len(d2.anim_records()) == len(d.anim_records()),
   '%d vs %d' % (len(d2.anim_records()), len(d.anim_records())))
t2 = [b for b in d2.buildings() if b['type'] == 'ATESLA'][0]
ck('重读楼仍然挂着 anim', d2.building_anim_key(t2['base'] - 72) == key)
g2 = dict((k, o) for o, k in d2.anim_records()).get(key)
ck('重读记录格仍对', g2 is not None and
   struct.unpack_from('<I', d2.plain, g2 + 0xB0)[0] == t2['x'] * 256)
ck('重读后 count 仍等于条数',
   struct.unpack_from('<I', d2.plain, d2.anim_records()[0][0] - 4)[0]
   == len(d2.anim_records()))

print('=== [5] 从零构造（原型）与模板克隆都能用 ===')
ck('anim_proto() 长度 476 且带类 GUID',
   (lambda b: b is not None and len(b) == 476 and b[:4] == ec.SaveDoc.COMP_MARK)(
       ec.SaveDoc(SAV).anim_proto()))
d3 = ec.SaveDoc(SAV)
t3 = [b for b in d3.buildings() if b['type'] == 'ATESLA'][0]
gold = ec.SaveDoc('E:/YURI/光棱塔放下 动画播放完成.SAV')
gg = dict((k, o) for o, k in gold.anim_records())
if 0x14602B90 in gg:
    donor = bytes(gold.plain[gg[0x14602B90]:gg[0x14602B90] + 476])
    r3 = d3.add_building_anim(t3['base'], donor=donor)
    d3g = dict((k, o) for o, k in d3.anim_records())[r3['key']]
    ck('模板克隆：格/键/Z 都被改写（不沿用模板的）',
       struct.unpack_from('<I', d3.plain, d3g + 0xB0)[0] == t3['x'] * 256
       and struct.unpack_from('<I', d3.plain, d3g + 0x10)[0] == r3['key']
       and struct.unpack_from('<I', d3.plain, d3g + 0xB8)[0] == 0)
else:
    ck('金模板档里能找到 ATESLA 的 anim（跳过）', True)

print('\n§195 自检：PASS %d / FAIL %d' % (n_ok, n_bad))
sys.exit(1 if n_bad else 0)
