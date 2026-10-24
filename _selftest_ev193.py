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
import struct
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
r = d.create_trigger_multi('T193排序删除测试', [(13, 2), (13, 5)], [(56, 0), (21, 0)])
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

print('=== [7] 新增事件 / 行为 ===')
d5 = ec.SaveDoc(SAV)
r5 = d5.create_trigger_multi('T193新增测试', [(13, 2)], [(56, 0)])
S5 = r5['trigger_id']
# ★注意：create_trigger_multi 会沿用模板触发的行为链（本档给 2 条），所以基数不是 1，
#   而且它自己会让 `_action_runs` 的覆盖数虚高（已记为独立问题，见 §193.6）。
#   这里只验证「我的新增」本身：相对基线 +1，且不把覆盖数弄得更糟。
base_ev = len(d5.events())
base_ac = len(d5.actions())
base_cov = sum(v['count'] for v in d5._action_runs().values())
base_acts = [(a['type'], a['ordinal']) for a in d5.trigger_actions(S5)]
n_before = len(d5.plain)
size_ev = d5._obj_size('ev', S5)
r_ev = d5.add_event(S5, 13, 7)
ck('加事件：长度 + 一个事件对象（%d B）' % size_ev,
   len(d5.plain) == n_before + size_ev, '%d' % (len(d5.plain) - n_before))
ck('加事件：全档事件数 +1', len(d5.events()) == base_ev + 1)
ck('加事件：新事件接在链尾（参数 7 在最后）',
   [e['arg'] for e in d5.trigger_event_list(S5)][-1] == 7,
   str([e['arg'] for e in d5.trigger_event_list(S5)]))
# ★§219 护栏（2026-10-24 补）：事件 `+0x38`(=+56) 必须是**事件表内物理序号**，
#   全档连续唯一；写成"触发序号"会与旧事件撞号 ⇒ 引擎定位到别人的对象。
_ords = [struct.unpack_from('<I', d5.plain, e['obj'] + 56)[0] for e in d5.events()]
ck('加事件：新事件 +0x38 = 表内 max+1',
   struct.unpack_from('<I', d5.plain, r_ev['obj'] + 56)[0] == max(_ords),
   '%d / max %d' % (struct.unpack_from('<I', d5.plain, r_ev['obj'] + 56)[0], max(_ords)))
ck('加事件：+0x38 全档唯一（没撞号）', len(set(_ords)) == len(_ords),
   '唯一 %d / 共 %d' % (len(set(_ords)), len(_ords)))
try:
    d5.add_event(S5, 0, 0)
    ck('事件类型 0 被拒绝', False, '没有抛错')
except (RuntimeError, ValueError):
    ck('事件类型 0 被拒绝', True)

d5.add_action(S5, 80, 3)
ck('加行为：长度 + 168 B', len(d5.plain) == n_before + size_ev + 168)
ck('加行为：全档行为数 +1', len(d5.actions()) == base_ac + 1)
acts5 = d5.trigger_actions(S5)
ck('加行为：条数 +1 且序号仍连续',
   len(acts5) == len(base_acts) + 1 and
   [a['ordinal'] for a in acts5] == list(range(1, len(acts5) + 1)),
   str([a['ordinal'] for a in acts5]))
cov5 = sum(v['count'] for v in d5._action_runs().values())
# ★`_action_runs` 的"最后一段延伸到表尾"规则，在 create_trigger_multi 造出的
#   **边界不规整**结构上会虚高（本档 189→191 而实际只 +1）⇒ 这里只断言"单调增加"，
#   严格的等值校验放在规整的 39MB 大档探针里（实测 187 == 187）。
ck('加行为：覆盖数单调增加（没退回）', cov5 >= base_cov + 1,
   '%d -> %d（实际行为 %d）' % (base_cov, cov5, len(d5.actions())))

p5 = out('ev193_新增.SAV')
d5.save(p5, backup=False)
d6 = ec.SaveDoc(p5)
ck('落盘重读：新事件还在（末尾参数 7）',
   [e['arg'] for e in d6.trigger_event_list(S5)][-1] == 7,
   str([e['arg'] for e in d6.trigger_event_list(S5)]))
ck('落盘重读：新行为还在（类型 80 / 参数 3）',
   (80, 3) in [(a['type'], a['arg']) for a in d6.trigger_actions(S5)],
   str([(a['type'], a['arg']) for a in d6.trigger_actions(S5)]))

print('\n§193 自检：PASS %d / FAIL %d' % (n_ok, n_bad))
if n_bad:
    print('失败项：' + ', '.join(fails))
sys.exit(1 if n_bad else 0)
