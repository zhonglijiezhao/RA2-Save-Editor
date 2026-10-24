# -*- coding: utf-8 -*-
"""§222 自检：ScriptType（动作脚本）解码 + clone_script + spawn 挂新脚本。

逆向结论（SAVE0029 171 个真对象实测）：
  · ScriptType 584B：+0x10 自键 / +0x38 8位ID / +0x78 名称48B / +0xAC **组内下标** /
    +0xB0 标志(沿用模板) / +0xB4 条数 / +0xB8 起每条 8B「动作, 参数」×50（到对象尾）
  · 运行期残留槽：+0x28/+0x34/+0x40/+0x70/+0xA8 + **超出条数的条目区**（引擎跑过的脚本
    这些位置有指针/索引）⇒ 克隆时必须清零
  · 脚本动作号是**独立表**（5=警戒(时间计数)、6=转到行号、58=移动到己方建筑…），
    与触发器「行为」表不是一套编号
"""
from __future__ import print_function

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc                                        # noqa: E402

_CAND = ['E:/YURI/SAVE0029.SAV', 'E:/YURI/新建文件夹 (30)/SAVE0029.SAV']
SAV = sys.argv[1] if len(sys.argv) > 1 else next((x for x in _CAND if os.path.isfile(x)), _CAND[0])
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
print('§222 自检：ScriptType 解码 + clone_script + spawn 挂脚本')

d0 = SaveDoc(SAV)
sc0 = d0.script_types()
ck('解析出动作脚本', len(sc0) > 0, '%d 个' % len(sc0))
g = d0._obj_group(d0.SCRIPT_CLS, d0.SCRIPT_SIZE, '动作脚本')
ck('★脚本组「组计数」与实际对象数一致',
   struct.unpack_from('<I', d0.plain, g['count_pos'])[0] == len(sc0),
   '%d' % len(sc0))
ck('★脚本 +0xAC = 组内下标（0..N-1 全对）',
   all(s['index'] == i for i, s in enumerate(sc0)), '')
# 条目区结构：+0xB4 = 条数，+0xB8+8k = (动作, 参数)
s0 = scs = sc0[0]
o = s0['off']
pl = d0.plain
ck('★+0xB4 = 条数，条目在 +0xB8 起（样例对得上）',
   struct.unpack_from('<i', pl, o + 0xB4)[0] == len(s0['entries'])
   and all(struct.unpack_from('<ii', pl, o + 0xB8 + 8 * k)
           == (e['action'], e['arg']) for k, e in enumerate(s0['entries'])),
   '%d 条' % len(s0['entries']))

# ---------- clone_script ----------
d = SaveDoc(SAV)
ENT = [(5, 10), (6, 0)]          # 警戒 10 → 转到行号 0（原地待命循环）
r = d.clone_script(ENT, name='自检待命')
ck('clone_script 返回 sid/键/下标/条目',
   bool(r['sid'] and r['key']) and r['index'] == len(sc0) and r['count'] == 2,
   'sid=%s index=%d' % (r['sid'], r['index']))
ck('★脚本组 +1', len(d.script_types()) == len(sc0) + 1, '')
new = [x for x in d.script_types() if x['sid'] == r['sid']][0]
ck('★新脚本读得回来且条目一致',
   [(e['action'], e['arg']) for e in new['entries']] == ENT,
   str([(e['action'], e['arg']) for e in new['entries']]))
g2 = d._obj_group(d.SCRIPT_CLS, d.SCRIPT_SIZE, '动作脚本')
ck('★组计数 +1', struct.unpack_from('<I', d.plain, g2['count_pos'])[0] == len(sc0) + 1, '')
ck('★新脚本 +0xAC = 它的物理下标',
   struct.unpack_from('<I', d.plain, new['off'] + 0xAC)[0] == len(sc0), '')
# 运行期残留清零（克隆自被引擎跑过的模板）
ck('★运行期残留槽已清零（+0x28/34/40/70/A8 与超出条目区）',
   all(struct.unpack_from('<I', d.plain, new['off'] + x)[0] == 0
       for x in (0x28, 0x34, 0x40, 0x70, 0xA8))
   and all(struct.unpack_from('<I', d.plain, new['off'] + 0xB8 + 8 * j)[0] == 0
           for j in range(len(ENT), 50)), '')
# 模板的身份字段保留（CLSID/类常量）
ck('★克隆保留了 CLSID 与类常量（只改身份）',
   bytes(d.plain[new['off']:new['off'] + 16]) == d.SCRIPT_CLS
   and bytes(d.plain[new['off'] + 0x14:new['off'] + 0x24])
   == bytes(pl[o + 0x14:o + 0x24]), '')

# ---------- 错误输入 ----------
try:
    d.clone_script([], name='x')
    ck('空条目会报错', False, '没有抛异常')
except ValueError as e:
    ck('空条目会报错', '至少要有一条' in str(e), str(e)[:40])
try:
    d.clone_script([(0, 0)] * 51, name='x')
    ck('超过 50 条会报错', False, '没有抛异常')
except ValueError as e:
    ck('超过 50 条会报错', '50' in str(e), str(e)[:40])

# ---------- 落盘往返 ----------
out = os.path.join(HERE, '_selftest_out', 'ev222_script.SAV')
if not os.path.isdir(os.path.dirname(out)):
    os.makedirs(os.path.dirname(out))
d.save(out, backup=False)
d2 = SaveDoc(out)
new2 = [x for x in d2.script_types() if x['sid'] == r['sid']]
ck('落盘重读：脚本还在且条目不变',
   bool(new2) and [(e['action'], e['arg']) for e in new2[0]['entries']] == ENT
   and struct.unpack_from('<I', d2.plain, new2[0]['off'] + 0xAC)[0] == r['index'], '')

# ---------- spawn 挂新脚本 ----------
cell = None
for xy in ((24, 180), (26, 180), (20, 182), (28, 182)):
    if d2.cell_taken(*xy):
        continue
    t = d2.cell_terrain(*xy)
    if t and t[0] in (d2.walkable_tiles() or set()):
        cell = xy
        break
ck('找到落点', cell is not None, str(cell))
r2 = d2.spawn_units_by_trigger('V3', 3, cell=cell, trigger_name='自检挂脚本',
                               max_teams=1, script_key=None,
                               script_entries=[(5, 10), (6, 0)])
tm = [x for x in d2.team_types() if x['sid'] == r2['team']['sid']][0]
ck('★spawn 自动建了脚本并挂到新小队',
   bool(r2['script']) and tm['script_key'] == r2['script']['key'],
   '脚本 sid=%s' % (r2['script']['sid'] if r2['script'] else '无'))
sc2 = d2.script_types()
ck('★脚本组又 +1（spawn 建的那段）', len(sc2) == len(sc0) + 2,
   '%d → %d' % (len(sc0), len(sc2)))

print('-' * 72)
print('§222 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
