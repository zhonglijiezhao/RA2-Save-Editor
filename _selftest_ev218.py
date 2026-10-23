# -*- coding: utf-8 -*-
"""§218 自检：刷兵的第二种行为 —— 107「超时空传送援军」。

背景：§217.7 遗留第 1 项「行为 107 尚未支持，先只做 80」。
玩家原话（2026-10-23）：
> 「单位要先写小队、脚本、特遣，然后才用 80 或者 107 刷出来。」

硬证据（`E:/YURI/SAVE0029.SAV` 实测，2026-10-23）：
  · 该档有 **47 个行为 80 + 250 个行为 107** 的真对象；
  · 两者布局**完全相同**：`+0x40`=类型、`+0x44`=小队键、`+0x58`=路径点、`+0x48` 恒 0；
  · 小队自己的「出现于」路径点 `+0xE8` 在真地图里**不同步**（46/47、250/250 都是 0）
    ⇒ 引擎读的是行为里的路径点，本函数因此**不动**小队 `+0xE8`（复刻地编行为）。

用法：python _selftest_ev218.py [存档]
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


SAV = sys.argv[1] if len(sys.argv) > 1 else fp('SAVE0029.SAV')
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
print('§218 自检：行为 107（超时空传送援军）刷兵')

# ---------- ① 真对象模式核对（硬证据） ----------
d0 = SaveDoc(SAV)
pl0 = d0.plain
teams0 = {t['key']: t for t in d0.team_types()}
for T in (80, 107):
    objs = [a for a in d0.actions() if a['type'] == T]
    ok_shape = ok_team = ok_zero48 = 0
    for a in objs:
        o = a['obj']
        if struct.unpack_from('<I', pl0, o + 0x44)[0] in teams0:
            ok_team += 1
        if struct.unpack_from('<I', pl0, o + 0x48)[0] == 0:
            ok_zero48 += 1
        if all(struct.unpack_from('<I', pl0, o + x)[0] == 0
               for x in (0x4C, 0x50, 0x54, 0x5C, 0x60, 0x64)):
            ok_shape += 1
    ck('★真对象：%d 个行为 %d 的小队键(+0x44)都指向真小队' % (len(objs), T),
       objs and ok_team == len(objs), '%d/%d' % (ok_team, len(objs)))
    ck('★真对象：%d 的 +0x48 恒 0' % T, objs and ok_zero48 == len(objs),
       '%d/%d' % (ok_zero48, len(objs)))
    ck('★真对象：%d 除三字段外其余参数槽全 0' % T, objs and ok_shape == len(objs),
       '%d/%d' % (ok_shape, len(objs)))

# 找落点
cell = None
for xy in ((20, 178), (20, 179), (24, 180), (28, 180), (18, 182)):
    if d0.cell_taken(*xy):
        continue
    t = d0.cell_terrain(*xy)
    if t and t[0] in (d0.walkable_tiles() or set()):
        cell = xy
        break
ck('找到可刷兵的落点', cell is not None, str(cell))

# ---------- ② 用 107 刷兵 ----------
d = SaveDoc(SAV)
tf0, tm0 = d.taskforces(), d.team_types()
# ★§219 护栏快照：刷兵前把**所有**行为对象的 (键 → 类型, 小队键, 路径点) 记下来
snap = {a['key']: (struct.unpack_from('<I', d.plain, a['obj'] + 0x40)[0],
                   struct.unpack_from('<I', d.plain, a['obj'] + 0x44)[0],
                   struct.unpack_from('<I', d.plain, a['obj'] + 0x58)[0])
        for a in d.actions()}
n_act0, n_ev0 = len(d.actions()), len(d.events())
r = d.spawn_units_by_trigger('V3', 4, cell=cell, trigger_name='自检107',
                             max_teams=1, script_key=0, action=107)
ck('返回 action=107', r['action'] == 107, str(r['action']))
ck('★特遣部队 +1 / 小队 +1',
   len(d.taskforces()) == len(tf0) + 1 and len(d.team_types()) == len(tm0) + 1, '')
# ★★§219：刷兵后逐一比对 —— **已存在的行为一个都不许被改动**
snap2 = {a['key']: (struct.unpack_from('<I', d.plain, a['obj'] + 0x40)[0],
                    struct.unpack_from('<I', d.plain, a['obj'] + 0x44)[0],
                    struct.unpack_from('<I', d.plain, a['obj'] + 0x58)[0])
         for a in d.actions()}
changed = [k for k in snap if k in snap2 and snap[k] != snap2[k]]
ck('★§219 护栏：原有行为对象零改动（不改写别人的触发）',
   not changed, '被改的键：%s' % ([hex(k) for k in changed[:3]],))
ck('★§219 行为表只 +1（追加，不复制）', len(d.actions()) == n_act0 + 1,
   '%d → %d' % (n_act0, len(d.actions())))
ck('★§219 事件表只 +1', len(d.events()) == n_ev0 + 1,
   '%d → %d' % (n_ev0, len(d.events())))
ords = [struct.unpack_from('<I', d.plain, a['obj'] + 0x38)[0] for a in d.actions()]
eords = [struct.unpack_from('<I', d.plain, e['obj'] + 0x38)[0] for e in d.events()]
ck('★§219 行为序号全局唯一（无撞号）', len(set(ords)) == len(ords),
   '%d 个行为 %d 个不同序号' % (len(ords), len(set(ords))))
ck('★§219 事件序号全局唯一（无撞号）', len(set(eords)) == len(eords),
   '%d 个事件 %d 个不同序号' % (len(eords), len(set(eords))))

acts = d.trigger_actions(r['trigger_id'])
ck('新触发有 1 条行为', len(acts) == 1, str(len(acts)))
if acts:
    a = acts[0]
    pl = d.plain
    tk = struct.unpack_from('<I', pl, a['action_off'] + 0x44)[0]
    ck('★行为类型 = 107', a['type'] == 107, str(a['type']))
    ck('★小队键在 +0x44 且指向新小队', tk == r['team']['key'],
       '%#x vs %#x' % (tk, r['team']['key']))
    ck('★路径点正确', a['waypoint'] == r['waypoint'], '%s' % a['waypoint'])
    ck('★路径点解析回原格', a['cell'] == cell, '%s vs %s' % (a['cell'], cell))
    ck('★写出的对象与真 107 同构（其余槽全 0）',
       all(struct.unpack_from('<I', pl, a['action_off'] + x)[0] == 0
           for x in (0x48, 0x4C, 0x50, 0x54, 0x5C, 0x60, 0x64)), '')
    # ★§219：新行为的序号 = 表内 max+1（原 407 个 ⇒ 407），且实例指针链指向它
    ck('★§219 新行为的 +0x38 = 追加序号 %d（不是触发序号）' % n_act0,
       struct.unpack_from('<I', pl, a['action_off'] + 0x38)[0] == n_act0,
       str(struct.unpack_from('<I', pl, a['action_off'] + 0x38)[0]))
    _inst = d.trigger_objects(r['trigger_id']).get('inst')
    if _inst is not None:
        _head = struct.unpack_from('<I', pl, _inst + 196)[0]
        ck('★§219 实例指针链指向的正是我们写参数的那个对象',
           _head == struct.unpack_from('<I', pl, a['action_off'] + 16)[0],
           '%#x vs %#x' % (_head, struct.unpack_from('<I', pl, a['action_off'] + 16)[0]))

evs = d.trigger_event_list(r['trigger_id'])
ck('★§219.7 事件 = 13「流逝时间 1 秒」（不是 8——帧数 0 不会被轮询）',
   len(evs) == 1 and evs[0]['type'] == 13 and evs[0]['arg'] == 1,
   str([(e['type'], e['arg']) for e in evs]))
_to = d.trigger_objects(r['trigger_id'])
if _to.get('node') is not None:
    _tk = struct.unpack_from('<I', d.plain, _to['node'] + 80)[0]
    ck('★§219.7 TagNode 帧数 = 15（1 秒）⇒ 进「定时触发器挂接表」', _tk == 15, str(_tk))

# 小队 +0xE8 = 0（§218：与地编写的援军队一致；引擎实际读行为里的 +0x58）
ntm = [x for x in d.team_types() if x['sid'] == r['team']['sid']][0]
ck('★小队 +0xE8 = 0（复刻地编：真地图被 80/107 引用的 297 个小队全是 0）',
   struct.unpack_from('<I', d.plain, ntm['off'] + 0xE8)[0] == 0,
   '%#x' % struct.unpack_from('<I', d.plain, ntm['off'] + 0xE8)[0])

# ---------- ③ 同档再刷一个 107（两发） ----------
cell2 = None
for xy in ((22, 178), (26, 180), (30, 180), (16, 182), (24, 182)):
    if d.cell_taken(*xy):
        continue
    t = d.cell_terrain(*xy)
    if t and t[0] in (d.walkable_tiles() or set()):
        cell2 = xy
        break
r2 = d.spawn_units_by_trigger('V3', 2, cell=cell2, trigger_name='自检107b',
                              max_teams=1, script_key=0, action=107)
ck('同档第二发 107 也成功',
   r2['action'] == 107 and r2['trigger_id'] != r['trigger_id']
   and r2['team']['key'] != r['team']['key'], r2['trigger_id'])
a2 = d.trigger_actions(r2['trigger_id'])[0]
ck('第二发的小队键/路径点各指各的',
   struct.unpack_from('<I', d.plain, a2['action_off'] + 0x44)[0] == r2['team']['key']
   and a2['waypoint'] == r2['waypoint'] and a2['cell'] == cell2,
   '%s vs %s' % (a2['cell'], cell2))

# ---------- ④ 80 回归（同档混用） ----------
cell3 = None
for xy in ((21, 178), (23, 178), (27, 180), (31, 180), (17, 182)):
    if d.cell_taken(*xy):
        continue
    t = d.cell_terrain(*xy)
    if t and t[0] in (d.walkable_tiles() or set()):
        cell3 = xy
        break
r3 = d.spawn_units_by_trigger('V3', 3, cell=cell3, trigger_name='自检80',
                              max_teams=1, script_key=0, action=80)
a3 = d.trigger_actions(r3['trigger_id'])[0]
ck('★80 回归：默认/显式 80 仍是 80',
   r3['action'] == 80 and a3['type'] == 80
   and struct.unpack_from('<I', d.plain, a3['action_off'] + 0x44)[0] == r3['team']['key'],
   '%s' % a3['type'])

# ---------- ⑤ 落盘往返 ----------
out = os.path.join(HERE, '_selftest_out', 'ev218_107.SAV')
if not os.path.isdir(os.path.dirname(out)):
    os.makedirs(os.path.dirname(out))
d.save(out, backup=False)
d2 = SaveDoc(out)
a = d2.trigger_actions(r['trigger_id'])[0]
ck('落盘重读：行为仍是 107 且键不变',
   a['type'] == 107
   and struct.unpack_from('<I', d2.plain, a['action_off'] + 0x44)[0] == r['team']['key']
   and a['waypoint'] == r['waypoint'], '')
ck('★§219 落盘重读：行为/事件序号仍全局唯一',
   len({struct.unpack_from('<I', d2.plain, x['obj'] + 0x38)[0] for x in d2.actions()})
   == len(d2.actions())
   and len({struct.unpack_from('<I', d2.plain, x['obj'] + 0x38)[0] for x in d2.events()})
   == len(d2.events()), '')
a = d2.trigger_actions(r3['trigger_id'])[0]
ck('落盘重读：80 那条也没被带坏',
   a['type'] == 80
   and struct.unpack_from('<I', d2.plain, a['action_off'] + 0x44)[0] == r3['team']['key'], '')
ck('落盘重读：触发/特遣/小队总数一致',
   len(d2.taskforces()) == len(d.taskforces())
   and len(d2.team_types()) == len(d.team_types()), '')

# ---------- ⑥ 错误输入 ----------
for bad in (0, 7, 99, 125):
    try:
        d2.spawn_units_by_trigger('V3', 1, cell=cell, action=bad)
        ck('拒绝非援军行为 %d' % bad, False, '没有抛异常')
        break
    except ValueError as e:
        ck('拒绝非援军行为 %d（提示 80/107）' % bad,
           '80' in str(e) and '107' in str(e), str(e)[:60])

print('-' * 72)
print('§218 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
