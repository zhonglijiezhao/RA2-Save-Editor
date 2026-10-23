# -*- coding: utf-8 -*-
"""§206/§207 自检：解除挂接**默认只做解除**，不弹选择、不顺手禁用；两头都能继续用。

玩家定案（2026-10-23）：
> 「先禁用再解除 没有必要。挂在建筑上的触发，解除挂接后没任何条件能启动，
>    不做任何操作做为默认；方便解除的建筑能挂别的触发，解除挂接的触发也方便的挂到别的建筑上。」

⇒ `detach_building(..., allow_last=True)` 成为默认（只剩 1 个挂载也能解），
   字段清 0 + 计数−1 之外**什么都不动**；`allow_last=False` 只是留给想恢复旧保护的调用方。

用法：python _selftest_ev206.py [存档]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc, LastAttachRefused                   # noqa: E402

SAV = sys.argv[1] if len(sys.argv) > 1 else 'C:/Users/zhao5/Desktop/SAVE1D3F.SAV'
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
print('§207 自检：解除挂接 = 只解除')
d0 = SaveDoc(SAV)
ov0 = d0.attach_overview()
one = [it for it in ov0 if it['count'] == 1 and it['buildings']]
ck('档里有一个"只挂 1 个"的节点', bool(one),
   ('node=%#x 触发=%s' % (one[0]['node'], one[0]['trigger_id'])) if one else '')
if not one:
    sys.exit(1)
it0 = one[0]
b0 = it0['buildings'][0]
ev_before = d0.trigger_event_list(it0['trigger_id'])[0]

# ---- 1) 默认就能解除（不再抛） ----
d = SaveDoc(SAV)
try:
    old = d.detach_building(it0['node'], b0['field'])
    ck('★默认调用直接解除（不再拒绝）', old != 0, '%#x' % old)
except LastAttachRefused as e:
    ck('★默认调用直接解除（不再拒绝）', False, '仍被拒：%r' % (e,))

nb = [b for b in d.buildings() if b['base'] == b0['field']]
ck('建筑挂接键已清 0', bool(nb) and nb[0]['tag'] == 0,
   hex(nb[0]['tag']) if nb else '建筑没了')
ov = [x for x in d.attach_overview() if x['x'] == it0['x']]
ck('节点还在、挂接数 = 0', bool(ov) and ov[0]['count'] == 0, str([x['count'] for x in ov]))
ck('该对象已从 ② 列表消失',
   bool(ov) and b0['field'] not in {b['field'] for b in ov[0]['buildings']}, '')

# ★"不做任何操作": 事件没被改
ev_after = d.trigger_event_list(it0['trigger_id'])[0]
ck('★没有顺手禁用触发（事件类型/参数不变）',
   (ev_after['type'], ev_after.get('arg')) == (ev_before['type'], ev_before.get('arg')),
   '%s/%s -> %s/%s' % (ev_before['type'], ev_before.get('arg'),
                       ev_after['type'], ev_after.get('arg')))

# ---- 2) 解除后的建筑能挂到**别的**触发 ----
other = [it for it in ov0 if it['trigger_id'] != it0['trigger_id'] and not it.get('orphan')]
ck('找得到另一个可挂的触发', bool(other), str([it['trigger_id'] for it in other[:3]]))
if other:
    o1 = other[0]
    d.attach_trigger_to_building(o1['node'], b0['field'])
    nb2 = [b for b in d.buildings() if b['base'] == b0['field']][0]
    ck('★那栋建筑已改挂到另一个触发', nb2['tag'] == o1['x'],
       '%#x vs %#x' % (nb2['tag'], o1['x']))
    o1b = [x for x in d.attach_overview() if x['x'] == o1['x']][0]
    ck('新触发挂接数 +1', o1b['count'] == o1['count'] + 1,
       '%d -> %d' % (o1['count'], o1b['count']))

# ---- 3) 被解除的触发还能挂到**别的**建筑 ----
pool = [x for x in d.attach_free_buildings() if x['base'] != b0['field']]
ck('还有别的空建筑可挂', bool(pool), '%d 栋' % len(pool))
if pool:
    tgt = pool[0]
    d.attach_trigger_to_building(it0['node'], tgt['base'])
    ov3 = [x for x in d.attach_overview() if x['x'] == it0['x']][0]
    ck('★被解除的触发又能挂到别的建筑上', ov3['count'] == 1,
       'count=%d' % ov3['count'])
    ck('挂的就是新选的那栋',
       any(b['field'] == tgt['base'] for b in ov3['buildings']), '')

# ---- 4) 显式要旧保护时仍然拒绝 ----
d2 = SaveDoc(SAV)
try:
    d2.detach_building(it0['node'], b0['field'], allow_last=False)
    ck('allow_last=False 仍可按旧语义拒绝', False, '没有抛')
except LastAttachRefused:
    ck('allow_last=False 仍可按旧语义拒绝', True, '')

# ---- 5) 落盘往返 ----
out = os.path.join(HERE, '_selftest_out', 'ev207_只解除.SAV')
if not os.path.isdir(os.path.dirname(out)):
    os.makedirs(os.path.dirname(out))
d.save(out, backup=False)
d3 = SaveDoc(out)
ck('落盘重读：建筑数不变', len(d3.buildings()) == len(d0.buildings()),
   '%d vs %d' % (len(d3.buildings()), len(d0.buildings())))
ck('落盘重读：节点数不变', len(d3.celltag_nodes()) == len(d0.celltag_nodes()),
   '%d vs %d' % (len(d3.celltag_nodes()), len(d0.celltag_nodes())))
ck('落盘重读：触发数不变', len(d3.records) == len(d0.records),
   '%d vs %d' % (len(d3.records), len(d0.records)))

print('-' * 72)
print('§207 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
