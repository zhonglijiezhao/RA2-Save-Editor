# -*- coding: utf-8 -*-
"""§193 事件 / 行为「列表化」自检：排序 + 单条删除。

样本策略：**自己造一条多事件/多行为的触发**（`create_trigger_multi`），
不依赖某张图恰好有人写过多事件触发 —— 单变量、可复现。

锁死这五件事：
  1. 排序 = **交换载荷**，明文长度一字节不变；链序真的反过来了（不是只改了显示）
  2. 删事件：长度 −事件尺寸、事件组计数 −1、剩下的链仍然完整
  3. 删行为：长度 −168、行为组计数 −1、后面行为的 `+0x38` 序号各 −1、链头已更新
  4. 只剩 1 条时**必须拒绝**（0 事件 / 0 行为 = 触发永远不成立或没有效果）
  5. 落盘再读回来，结构与顺序都还在（不信内存自证）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
import editor_core as ec
from _paths import need_sav, out

SAV = need_sav('SAVE4CA2.SAV')

n_ok = n_bad = 0
fails = []


def ck(name, cond, extra=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('  PASS  %s' % name)
    else:
        n_bad += 1
        fails.append(name)
        print('  FAIL  %s   %s' % (name, extra))


def ev_types(doc, sid):
    return [e['type'] for e in doc.trigger_event_list(sid)]


def ac_types(doc, sid):
    return [a['type'] for a in doc.trigger_actions(sid)]


print('=== 准备：造一条 2 事件 + 2 行为的触发 ===')
d = ec.SaveDoc(SAV)
SID = '01000292'
r = d.create_trigger_multi('T193排序删除测试', [(13, 2), (13, 5)], [(56, 0)])
SID = r['trigger_id']
print('  新触发 %s，事件 %s，行为 %s'
      % (SID, ev_types(d, SID), ac_types(d, SID)))
ck('新触发有 2 个事件', len(d.trigger_event_list(SID)) == 2)
ck('新触发有 2 条行为', len(d.trigger_actions(SID)) == 2)

print('=== [1] 事件排序：交换载荷、长度不变 ===')
n0 = len(d.plain)
t0 = ev_types(d, SID)
res = d.move_event(SID, 0, +1)
t1 = ev_types(d, SID)
ck('明文长度不变', len(d.plain) == n0, '%d → %d' % (n0, len(d.plain)))
ck('前两个事件已互换', t1[:2] == [t0[1], t0[0]], '%s → %s' % (t0, t1))
ck('事件条数不变', len(t1) == len(t0))
ck('搬动含事件 13 时给了心跳警告', bool(res['warn']))
d.move_event(SID, 1, -1)
ck('再搬回来恢复原序', ev_types(d, SID) == t0, str(ev_types(d, SID)))

print('=== [2] 行为排序 ===')
a0 = ac_types(d, SID)
d.move_action(SID, 0, +1)
a1 = ac_types(d, SID)
ck('前两条行为已互换', a1[:2] == [a0[1], a0[0]], '%s → %s' % (a0, a1))
ck('明文长度仍不变', len(d.plain) == n0)
d.move_action(SID, 1, -1)
ck('再搬回来恢复原序', ac_types(d, SID) == a0)

print('=== [3] 删事件 ===')
size_ev = d._obj_size('ev', SID)
n_before = len(d.plain)
cnt_before = len(d.events())
left = d.remove_event(SID, 0)
ck('长度正好减少一个事件对象（%d B）' % size_ev,
   len(d.plain) == n_before - size_ev, '%d → %d' % (n_before, len(d.plain)))
ck('事件总数 −1', len(d.events()) == cnt_before - 1)
ck('该触发剩 1 个事件且链完整', len(d.trigger_event_list(SID)) == 1,
   str(ev_types(d, SID)))

print('=== [4] 删行为 + 序号回填 ===')
n_before = len(d.plain)
cnt_a = len(d.actions())
d.remove_action(SID, 0)
ck('长度正好减少 168 B', len(d.plain) == n_before - 168)
ck('行为总数 −1', len(d.actions()) == cnt_a - 1, '%d → %d' % (cnt_a, len(d.actions())))
rest = d.trigger_actions(SID)
ck('该触发剩 1 条行为', len(rest) == 1, str(ac_types(d, SID)))
ck('剩下的行为序号已回填成 1（1 基 ordinal）', rest[0]['ordinal'] == 1, str(rest[0]['ordinal']))
inst = d._instance_maps()['inst'][d.runtime_links()[SID.upper()]['inst_key']]
import struct as _s
_head = _s.unpack_from('<I', d.plain, inst + 196)[0]
_mykey = _s.unpack_from('<I', d.plain, rest[0]['action_off'] + 16)[0]
ck('链头（实例 +196）已指向剩下的那条行为', _head == _mykey,
   '%08x vs %08x' % (_head, _mykey))

print('=== [5] 只剩 1 条时必须拒绝 ===')
try:
    d.remove_event(SID, 0)
    ck('删最后一个事件被拒绝', False, '没有抛错')
except RuntimeError:
    ck('删最后一个事件被拒绝', True)
try:
    d.remove_action(SID, 0)
    ck('删最后一条行为被拒绝', False, '没有抛错')
except RuntimeError:
    ck('删最后一条行为被拒绝', True)

print('=== [6] 落盘 → 重新解码校验 ===')
p = out('ev193_列表化.SAV')
d.save(p, backup=False)
d4 = ec.SaveDoc(p)
ck('重新读档：触发仍在', SID.upper() in d4.runtime_links())
ck('重新读档：事件数 = 1', len(d4.trigger_event_list(SID)) == 1,
   str(len(d4.trigger_event_list(SID))))
ck('重新读档：行为数 = 1', len(d4.trigger_actions(SID)) == 1)
ck('重新读档：全档事件/行为总数与内存一致',
   len(d4.events()) == len(d.events()) and len(d4.actions()) == len(d.actions()),
   '%d/%d vs %d/%d' % (len(d4.events()), len(d4.actions()),
                       len(d.events()), len(d.actions())))
print('  产物：%s' % p)

print('\n§193 自检：PASS %d / FAIL %d' % (n_ok, n_bad))
if n_bad:
    print('失败项：' + ', '.join(fails))
sys.exit(1 if n_bad else 0)
