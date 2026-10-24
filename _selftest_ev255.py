# -*- coding: utf-8 -*-
"""§255 自检：触发编辑器能**给行为 80/107 选小队**（写 `+0x44`）+ 读回。

玩家 2026-10-24 23:34：「行为80 不能选择小队（参考地编的页面）」。
地编里 80 是「小队类型 + 路径点」两个字段；本工具以前只给一个纯数字框。

覆盖：
  A. `trigger_actions()` 对 80/107 回填 `team`（读 `+0x44`）
  B. `create_trigger_multi` 带 `team` 的第一条行为**不交给** create_trigger，
     而是建空触发后 `add_action(team=)` 追加 —— 落点必须是 `+0x44`，`+0x48` 必须为 0
  C. `set_action(..., team=, waypoint=)` 原地改这两槽
  D. 落盘重读后仍然对

跑法：D:/py/python.exe _selftest_ev255.py [存档]
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))
import editor_core as ec

_CAND = ['E:/YURI/新建文件夹 (30)/T85_老工程师_修位置副本槽.SAV',
         'E:/YURI/T110_新ID格式.SAV',
         'E:/YURI/新建文件夹 (30)/SAVE0029.SAV']
SAV = sys.argv[1] if len(sys.argv) > 1 else next((x for x in _CAND if os.path.isfile(x)), _CAND[0])

n_ok = n_bad = 0


def ck(name, cond, detail=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('PASS  %s' % name)
    else:
        n_bad += 1
        print('FAIL  %s   %s' % (name, detail))


print('基档：%s' % SAV)
print('-' * 72)
d = ec.SaveDoc(SAV)

# ---------- A. 已有 80 行为能读出 team ----------
found = None
for r in d.records:
    if r.kind != '触发':
        continue
    try:
        acts = d.trigger_actions(r.sid)
    except Exception:
        continue
    for a in acts:
        if a['type'] in d.SPAWN_ACTIONS and a.get('team'):
            found = (r.sid, a)
            break
    if found:
        break
if found:
    sid0, a0 = found
    raw44 = struct.unpack_from('<I', d.plain, a0['action_off'] + 0x44)[0]
    raw48 = struct.unpack_from('<I', d.plain, a0['action_off'] + 0x48)[0]
    ck('A1 已有行为 %d 的 team 非 0（`+0x44`）' % a0['type'], bool(a0['team']), a0.get('team'))
    ck('A2 team 与 `+0x44` 一致', a0['team'] == raw44, '%#x vs %#x' % (a0['team'], raw44))
    ck('A3 `+0x48` 不是小队键（应为 0）', raw48 == 0, hex(raw48))
    ck('A4 该 team 能在 team_types() 里找到',
       any(t['key'] == a0['team'] for t in d.team_types()), hex(a0['team']))
else:
    ck('A1 档内存在带小队的 80/107 行为', False, '没找到')

# ---------- B. 新建：create_trigger_multi 带 team ----------
d2 = ec.SaveDoc(SAV)
tm = d2.create_ai_team([('DISK', 1)], name='EV255队', veteran=1, max_teams=5, script_key=0)
tkey = tm['team']['key']
tsid = tm['team']['sid']
ck('B0 准备：新建小队成功', bool(tkey), tsid)

info = d2.create_trigger_multi('EV255触发', [(13, 1)],
                               [{'type': 80, 'team': tkey, 'waypoint': 226}])
sid = info['trigger_id']
acts = d2.trigger_actions(sid)
ck('B1 新触发有 1 条行为', len(acts) == 1, acts)
if acts:
    a = acts[0]
    ck('B2 行为类型 = 80', a['type'] == 80, a['type'])
    ck('B3 小队键写进 `+0x44`', a.get('team') == tkey, '%#x vs %#x' % (a.get('team') or 0, tkey))
    ck('B4 路径点 = 226', a.get('waypoint') == 226, a.get('waypoint'))
    ck('B5 `+0x48` 仍为 0', struct.unpack_from('<I', d2.plain, a['action_off'] + 0x48)[0] == 0)

# ---------- C. set_action 改小队 + 路径点 ----------
tm2 = d2.create_ai_team([('DISK', 1)], name='EV255队2', veteran=1, max_teams=5, script_key=0)
tkey2 = tm2['team']['key']
acts_now = d2.trigger_actions(sid)      # ★ 新建小队会插入对象 ⇒ 旧偏移已失效，必须重取
if acts_now:
    # ★§255：传 `action_off`（行为对象起点），set_action 会自己 +0x40 换成类型字段
    obj = {'action_off': acts_now[0]['action_off'], 'type': 80, 'arg': 0}
    d2.set_action(obj, atype=80, arg=0, team=tkey2, waypoint=111)
    chk = d2.trigger_actions(sid)[0]
    ck('C1 set_action 改小队生效', chk.get('team') == tkey2, '%#x vs %#x' % (chk.get('team') or 0, tkey2))
    ck('C2 set_action 改路径点生效', chk.get('waypoint') == 111, chk.get('waypoint'))

# ---------- D. 落盘重读 ----------
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_tmp_ev255.SAV')
d2.save(out)
e = ec.SaveDoc(out)
ea = e.trigger_actions(sid)
if ea:
    ck('D1 重读后小队键不变', ea[0].get('team') == tkey2, '%#x' % (ea[0].get('team') or 0))
    ck('D2 重读后路径点不变', ea[0].get('waypoint') == 111, ea[0].get('waypoint'))
    ck('D3 重读后 `+0x48` 仍为 0',
       struct.unpack_from('<I', e.plain, ea[0]['action_off'] + 0x48)[0] == 0)
try:
    os.remove(out)
except Exception:
    pass

print('-' * 72)
print('§255 自检：PASS %d / FAIL %d' % (n_ok, n_bad))
sys.exit(1 if n_bad else 0)
