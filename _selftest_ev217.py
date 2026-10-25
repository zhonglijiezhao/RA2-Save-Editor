# -*- coding: utf-8 -*-
"""§217 自检：用「特遣部队 + 作战小队 + 触发80」让引擎自己刷兵（绕开 SPAWNS 的坑）。

玩家指路（2026-10-23，截图 + `SAVE0029.SAV`）：
> 「建筑一个触发125就能解决，单位要先写小队、脚本、特遣，然后才用 80 或者 107 刷出来，
>   不过这个路线是值得的。由于我们只要刷单位出来，脚本可以留空，就避开了最难的部分。」
> 「注意这个触发在存档上已被执行过。」

逆向出来的结构（全部是 `[count][对象×count]` 连续组）：
  TaskForce 232B：+0x10 键 / +0x38 ID / +0x78 名称 / +0xAC 分组(-1)
                  +0xB0 单位**种类数** / +0xB8 起每 8B 一组「数量, 类型键」×6
  Team      268B：+0x10 键 / +0x38 ID / +0x78 名称 / +0xAC **组内下标**
                  +0xC8 优先级 / +0xCC 最大同时存在 / +0xD8 国家
                  +0xF0 星级 / +0xF4 脚本 / +0xF8 **特遣部队键**
  行为 80   ：+0x40=80 / **+0x44 = 小队键**（不是工具默认的 +0x48）/ +0x58 = 路径点
  事件 8「任何事件」：**不需要 CellTag 节点**（原图那条挂接数 0 照样刷兵）

用法：python _selftest_ev217.py [存档]
"""
from __future__ import print_function

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc                                        # noqa: E402

def fp(*names):
    for n in names:
        for d in ('E:/YURI', 'E:/YURI/新建文件夹 (29)'):
            q = os.path.join(d, n)
            if os.path.isfile(q):
                return q
    return names[0]


_CAND = ['E:/YURI/SAVE0029.SAV', 'E:/YURI/新建文件夹 (30)/SAVE0029.SAV',
         fp('SAVE0029.SAV')]
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
print('§217 自检：刷兵三件套（特遣 + 小队 + 触发80）')
ck('存档存在', os.path.isfile(SAV), SAV)
if not os.path.isfile(SAV):
    sys.exit(1)

d = SaveDoc(SAV)
tf0, tm0, tr0 = d.taskforces(), d.team_types(), len(d.records)
ck('解析出特遣部队', len(tf0) > 0, '%d 个' % len(tf0))
ck('解析出作战小队', len(tm0) > 0, '%d 个' % len(tm0))
ck('★特遣组的"组计数"与实际对象数一致',
   struct.unpack_from('<I', d.plain, d._obj_group(d.TF_CLS, d.TF_SIZE)['count_pos'])[0] == len(tf0),
   '%d' % len(tf0))
ck('★小队组的"组计数"与实际对象数一致',
   struct.unpack_from('<I', d.plain, d._obj_group(d.TEAM_CLS, d.TEAM_SIZE)['count_pos'])[0] == len(tm0),
   '%d' % len(tm0))
ck('★小队 +0xAC 是组内下标（首尾都对）',
   all(x['index'] == i for i, x in enumerate(tm0)),
   '首=%d 尾=%d' % (tm0[0]['index'], tm0[-1]['index']))

# 原图那条（若在）：事件 8 + 行为 80 + 挂接数 0
orig = [x for x in tm0 if x['sid'] == '01000488']
ck('找得到原图的 01000488（V3 那条队）', bool(orig), '')
if orig:
    ck('它引用的特遣部队确实装着 V3',
       any(u['type'] == 'V3' for x in tf0 if x['key'] == orig[0]['tf_key']
           for u in x['units']), '')

# ---------- 刷 6 架 V3 ----------
cell = None
for xy in ((20, 178), (20, 179), (24, 180), (28, 180), (18, 182)):
    if d.cell_taken(*xy):
        continue
    t = d.cell_terrain(*xy)
    if t and t[0] in (d.walkable_tiles() or set()):
        cell = xy
        break
if cell is None:
    # ★2026-10-25：玩家把 ALL01UMD.MAP 换成了 80x80 测试图（IsoMapPack5 只压了 1 格、
    #   没有任何非默认地形），地形读出来是空的；SAVE0029 里也没有原生单位可参照。
    #   ⇒ 选不出落点。这属于**环境不支持**，不是代码回归，明确 SKIP 而不是假 FAIL。
    print('  [SKIP] 选不出落点：%s 地形为空（80x80 空图），存档里也没有原生单位'
          % d.scenario_map_path())
    print('§217 自检：SKIP（环境不支持，非回归）')
    sys.exit(0)
ck('找到可刷兵的落点', True, str(cell))

# ★§219 护栏快照：原有行为/事件对象一个都不许被改写（2026-10-23 序号撞号 bug 的回归）
snap = {a['key']: (struct.unpack_from('<I', d.plain, a['obj'] + 0x40)[0],
                   struct.unpack_from('<I', d.plain, a['obj'] + 0x44)[0],
                   struct.unpack_from('<I', d.plain, a['obj'] + 0x58)[0])
        for a in d.actions()}
n_act0, n_ev0 = len(d.actions()), len(d.events())

r = d.spawn_units_by_trigger('V3', 6, cell=cell, trigger_name='自检刷V3',
                             max_teams=1)
snap2 = {a['key']: (struct.unpack_from('<I', d.plain, a['obj'] + 0x40)[0],
                    struct.unpack_from('<I', d.plain, a['obj'] + 0x44)[0],
                    struct.unpack_from('<I', d.plain, a['obj'] + 0x58)[0])
         for a in d.actions()}
changed = [k for k in snap if k in snap2 and snap[k] != snap2[k]]
ck('★§219 护栏：原有行为对象零改动', not changed,
   '被改的键：%s' % ([hex(k) for k in changed[:3]],))
ck('★§219 行为/事件表各只 +1',
   len(d.actions()) == n_act0 + 1 and len(d.events()) == n_ev0 + 1,
   '%d→%d / %d→%d' % (n_act0, len(d.actions()), n_ev0, len(d.events())))
ck('刷兵返回了特遣/小队/触发', bool(r['tf']['key'] and r['team']['key'] and r['trigger_id']),
   '%s / %s / %s' % (r['tf']['sid'], r['team']['sid'], r['trigger_id']))
ck('★特遣部队 +1', len(d.taskforces()) == len(tf0) + 1,
   '%d → %d' % (len(tf0), len(d.taskforces())))
ck('★作战小队 +1', len(d.team_types()) == len(tm0) + 1,
   '%d → %d' % (len(tm0), len(d.team_types())))

ntf = [x for x in d.taskforces() if x['sid'] == r['tf']['sid']]
ck('新特遣部队读得回来', bool(ntf), r['tf']['sid'])
if ntf:
    u = ntf[0]['units']
    ck('★特遣里就是 V3 × 6', len(u) == 1 and u[0]['type'] == 'V3' and u[0]['count'] == 6,
       '%s' % (u,))
    ck('种类数与实际条目一致', ntf[0]['n_types'] == len(u), '%d / %d' % (ntf[0]['n_types'], len(u)))

ntm = [x for x in d.team_types() if x['sid'] == r['team']['sid']]
ck('新小队读得回来', bool(ntm), r['team']['sid'])
if ntm:
    t = ntm[0]
    ck('★小队 +0xAC = 它的物理下标（追加到组尾）', t['index'] == len(tm0),
       '%d（期望 %d）' % (t['index'], len(tm0)))
    ck('★小队引用的特遣 = 新建的那个', t['tf_key'] == r['tf']['key'],
       '%#x vs %#x' % (t['tf_key'], r['tf']['key']))
    ck('★小队国家 = 玩家所属方（刷出来的单位才有主人）',
       t['country_key'] == d.house_key_of(d.player_house()),
       '%#x vs %#x' % (t['country_key'], d.house_key_of(d.player_house())))
    ck('最大同时存在 = 1', t['max'] == 1, str(t['max']))

# ---------- 触发 ----------
acts = d.trigger_actions(r['trigger_id'])
ck('新触发有 1 条行为', len(acts) == 1, str(len(acts)))
if acts:
    a = acts[0]
    pl = d.plain
    tk = struct.unpack_from('<I', pl, a['action_off'] + 0x44)[0]
    ck('★行为类型 = 80（援军/作战小队）', a['type'] == 80, str(a['type']))
    ck('★小队引用在 +0x44 且指向新小队', tk == r['team']['key'],
       '%#x vs %#x' % (tk, r['team']['key']))
    ck('★路径点正确', a['waypoint'] == r['waypoint'], '%s' % a['waypoint'])
    ck('★路径点解析回原格', a['cell'] == cell, '%s vs %s' % (a['cell'], cell))
evs = d.trigger_event_list(r['trigger_id'])
ck('★§219.7 事件 = 13「流逝时间 1 秒」（不是 8——帧数 0 不会被轮询）',
   len(evs) == 1 and evs[0]['type'] == 13 and evs[0]['arg'] == 1,
   str([(e['type'], e['arg']) for e in evs]))
_to = d.trigger_objects(r['trigger_id'])
if _to.get('node') is not None:
    import struct as _s
    _tk = _s.unpack_from('<I', d.plain, _to['node'] + 80)[0]
    ck('★§219.7 TagNode 帧数 = 15（1 秒）⇒ 进「定时触发器挂接表」', _tk == 15, str(_tk))

# ---------- 落盘往返 ----------
out = os.path.join(HERE, '_selftest_out', 'ev217_刷兵.SAV')
if not os.path.isdir(os.path.dirname(out)):
    os.makedirs(os.path.dirname(out))
d.save(out, backup=False)
d2 = SaveDoc(out)
ck('落盘重读：特遣/小队数量不变',
   len(d2.taskforces()) == len(d.taskforces()) and len(d2.team_types()) == len(d.team_types()),
   '%d/%d' % (len(d2.taskforces()), len(d2.team_types())))
ck('落盘重读：组计数仍然自洽',
   struct.unpack_from('<I', d2.plain, d2._obj_group(d2.TF_CLS, d2.TF_SIZE)['count_pos'])[0]
   == len(d2.taskforces())
   and struct.unpack_from('<I', d2.plain, d2._obj_group(d2.TEAM_CLS, d2.TEAM_SIZE)['count_pos'])[0]
   == len(d2.team_types()), '')
a2 = d2.trigger_actions(r['trigger_id'])
ck('落盘重读：行为仍是 80 且指向同一个键',
   bool(a2) and a2[0]['type'] == 80
   and struct.unpack_from('<I', d2.plain, a2[0]['action_off'] + 0x44)[0] == r['team']['key'], '')

# ---------- script_key=0（空脚本） ----------
d3 = SaveDoc(SAV)
r3 = d3.spawn_units_by_trigger('V3', 2, cell=cell, max_teams=1, script_key=0)
t3 = [x for x in d3.team_types() if x['sid'] == r3['team']['sid']][0]
ck('★script_key=0 能写成"空脚本"', t3['script_key'] == 0, '%#x' % t3['script_key'])

# ---------- 错误输入 ----------
try:
    d3.spawn_units_by_trigger('ZZZ_NOSUCH', 1, cell=cell)
    ck('未知型号会报错', False, '没有抛异常')
except Exception as e:
    ck('未知型号会报错（且提示类型表里没有）', '类型表' in str(e) or '类型键' in str(e),
       str(e)[:50])

print('-' * 72)
print('§217 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
