# -*- coding: utf-8 -*-
"""§224 自检：触发国家(owner)读取 —— 重复 sid 选对记录 + 与地图 [Triggers] 全量对照。

背景（玩家实测 2026-10-24 00:34）：触发编辑器把 01000494 的国家显示成 Neutral，
玩家认为应是苏俄。查证：**地图 `[Triggers]` 里 01000494 就是 Neutral**（88 个 Neutral
里的一条），工具读得没错——刷出的单位属苏俄是因为它引用的**小队**国家是 Russians，
触发 owner 只决定触发归谁评估。
但对照时抓到一个真 bug：SAVE0029 有 22 个 sid 带**两条记录**（一条有实例、一条
没有的副本，副本 owner 键 0x103b6a60 谁都不认），旧代码取第一条 ⇒ 22 条全读错。
修复：`trigger_rec()` 优先选「记录帧 == 实例帧 + 56」的那条。
"""
from __future__ import print_function

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc                                        # noqa: E402
from map_ini import MapIni                                             # noqa: E402


def fp(*names):
    for n in names:
        for dd in ('E:/YURI', 'E:/YURI/新建文件夹 (29)'):
            q = os.path.join(dd, n)
            if os.path.isfile(q):
                return q
    return names[0]


SAV = sys.argv[1] if len(sys.argv) > 1 else fp('SAVE0029.SAV')
MAP = sys.argv[2] if len(sys.argv) > 2 else 'E:/YURI/ALL01UMD.MAP'
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


print('=' * 72)
print('§224 自检：触发 owner 读取（重复 sid 选对记录）')

d = SaveDoc(SAV)
mi = MapIni(MAP)
tg = mi.sections.get('triggers') or {}
ck('地图有 [Triggers] 段（权威真值）', bool(tg), '%d 行' % len(tg))
first = {}
for sid, v in tg.items():
    first.setdefault(sid, str(v).split(',')[0].strip())

# ① 重复 sid 的记录选择：选中有实例的那条（记录帧 == 实例帧 + 56）
dups = {}
for r in d.records:
    if r.kind == '触发':
        dups.setdefault(r.sid, []).append(r)
dup_sids = [s for s, rs in dups.items() if len(rs) > 1]
ck('★本档确有重复 sid 的触发', bool(dup_sids), '%d 个（%s…）' % (len(dup_sids), dup_sids[:3]))
ok_pick = 0
for sid in dup_sids:
    inst = d.trigger_inst_off(sid)
    rec = d.trigger_rec(sid)
    if inst is None or rec is None or rec.off == inst + 56:
        ok_pick += 1
ck('★重复 sid 全部选中「有实例对应」的记录', ok_pick == len(dup_sids),
   '%d/%d' % (ok_pick, len(dup_sids)))

# ② 全量对照：工具读出的 owner == 地图 [Triggers] 第一字段
mism = []
for sid, want in first.items():
    rec = d.trigger_rec(sid)
    if rec is None:
        continue
    got = d.trigger_owner_name(rec)
    if want != got:
        mism.append((sid, want, got))
ck('★全量对照：工具 owner 与地图 [Triggers] 零不一致', not mism,
   '不一致 %d 条 %s' % (len(mism), mism[:3]))

# ③ 玩家问的那条：01000494 = Neutral（地图双证，不是苏俄）
rec = d.trigger_rec('01000494')
ck('★01000494 的国家 = Neutral（与地图一致；刷出的单位属苏俄是小队的国家）',
   rec is not None and d.trigger_owner_name(rec) == first.get('01000494') == 'Neutral',
   '%r / 地图 %r' % (d.trigger_owner_name(rec), first.get('01000494')))

# ④ trigger_detail 的 owner 走的是同一条路
det = d.trigger_detail('01000494')
ck('★trigger_detail 的 owner 也一致', det.get('owner') == 'Neutral', str(det.get('owner')))

# ⑤ 单一 sid 的触发不受影响（抽 5 条对照）
single = [s for s, rs in dups.items() if len(rs) == 1][:5]
ok5 = all(d.trigger_owner_name(d.trigger_rec(s)) == first.get(s) for s in single)
ck('★单一 sid 的触发读取不受影响（抽 5 条）', ok5, str(single))

print('-' * 72)
print('§224 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
