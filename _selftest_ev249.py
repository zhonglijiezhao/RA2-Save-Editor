# -*- coding: utf-8 -*-
"""§249 自检：克隆必须挑"空闲 + 无任务"的骨架（活内存实证，2026-10-24 晚）。

背景（T103 崩档的活内存分析，`_probe_*.py` 系列）：
  崩溃对象经"存档记录 ↔ 运行期内存"逐字段比对确认**就是我们的克隆飞碟**
  （映射：存档偏移 = 运行期 + 0x14，85/96 字段吻合）。
  T85 的 98 架飞碟里，92 架"正在跑地图脚本"的在 `+0x0D8/+0x54C/+0x654` 上各挂着
  一个**自己私有的指令/脚本对象**（每台取值都不同）；6 架空闲的这些槽是
  **一致的共享值**（0x17B1FB / 0x17B1FB / 0x17B241），`+0x670/+0x114/+0x17C…` 是 0xE1。
  ⇒ 挑到"忙"骨架 ⇒ 新单位读档就朝老目标飞（玩家："有目标，不知道要去干什么"），
    被打掉时引擎去更新那条脚本/指令 ⇒ 跳失效对象弹框。

本自检锁三条：
  ① `_pick_scaffold` 必须挑到"任务组 `+0x298~0x2A4` 全 0"的骨架（档里有的话）；
  ② 挑中的骨架 `+0x198` 必须等于同型号众数（型号级共享值，不清 0）；
  ③ 克隆体的脚本槽（0x0D8/0x54C/0x654/0x670）必须落在"同档空闲单位"的取值集合里。
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from _paths import find_sav          # noqa: E402
import editor_core as ec             # noqa: E402

OK = [0]
BAD = [0]


def ck(cond, msg, extra=''):
    if cond:
        OK[0] += 1
        print('  [PASS] %s' % msg)
    else:
        BAD[0] += 1
        print('  [FAIL] %s   %s' % (msg, extra))


def u32(pl, off):
    return struct.unpack_from('<I', pl, off)[0]


MISSION = (0x298, 0x29C, 0x2A0, 0x2A4)
SCRIPT = (0x0D8, 0x54C, 0x654, 0x670)

print('=== §249 骨架挑选（活内存实证）===')

# 用玩家那张"满档飞碟都在跑脚本"的 T85 当样本：里面 6 架空闲、92 架忙
SRC = find_sav('T85_老工程师_修位置副本槽.SAV')
d = ec.SaveDoc(SRC)
pl = d.plain
recs = [r for r in d.unit_records() if r['type'] == 'DISK']
idle = [r for r in recs if all(u32(pl, r['off'] + o) == 0 for o in MISSION)]
busy = [r for r in recs if r not in idle]
print('  样本：DISK %d 架 = 空闲 %d + 忙 %d' % (len(recs), len(idle), len(busy)))
ck('本档确实存在"忙"骨架（否则测不出区别）', len(busy) >= 2, len(busy))
ck('本档确实存在"空闲"骨架', len(idle) >= 1, len(idle))

scaf = d._pick_scaffold(recs, 'Fly')
so = scaf['off']
print('  挑中骨架 @(%s,%s)：任务组=%s 0x198=%#x'
      % (scaf.get('x'), scaf.get('y'),
         [u32(pl, so + o) for o in MISSION], u32(pl, so + 0x198)))

ck('① 挑中的骨架任务组全 0（原地待命）',
   all(u32(pl, so + o) == 0 for o in MISSION),
   [u32(pl, so + o) for o in MISSION])

# ② 0x198 与同型号众数一致
cnt = {}
for r in recs:
    v = u32(pl, r['off'] + 0x198)
    cnt[v] = cnt.get(v, 0) + 1
modal = max(cnt.items(), key=lambda kv: kv[1])[0]
print('  同型号 +0x198 众数 = %#x（%d/%d）' % (modal, cnt[modal], len(recs)))
ck('② 骨架 +0x198 == 同型号众数', u32(pl, so + 0x198) == modal,
   '%#x vs %#x' % (u32(pl, so + 0x198), modal))

# ③ 克隆体脚本槽落在"空闲单位取值集合"里
info = d.add_unit_by_clone('DISK', 108, 206, house=d.player_house(), veterancy=0)
rec = [r for r in d.unit_records() if r['key'] == info['key']][0]
pl2 = d.plain
idle_sets = {o: {u32(pl, r['off'] + o) for r in idle} for o in SCRIPT}
for o in SCRIPT:
    v = u32(pl2, rec['off'] + o)
    ck('③ +0x%03X 落在同档空闲单位的取值集合里' % o,
       v in idle_sets[o], '%#x ∉ %s' % (v, [hex(x) for x in idle_sets[o]]))

ck('③b 返回值带 scaffold_no_mission', info.get('scaffold_no_mission') is True,
   info.get('scaffold_no_mission'))

# ④ 回归：`import_unit_from` 的 full 模式也走同一套打分
info2 = d.import_unit_from(ec.SaveDoc(find_sav('SAVE4CA2.SAV')), 'DISK',
                           110, 206, house=d.player_house(), slots='safe')
rec2 = [r for r in d.unit_records() if r['key'] == info2['key']][0]
ck('④ safe 导入也挑到无任务骨架',
   info2.get('scaffold_no_mission') is True, info2.get('scaffold_no_mission'))

print('\n§249 自检：PASS %d / FAIL %d' % (OK[0], BAD[0]))
sys.exit(1 if BAD[0] else 0)
