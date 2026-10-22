# -*- coding: utf-8 -*-
"""§174 自检：FA2 化触发器（多事件 + 多行为 + 一键局部变量配方）。

不启动 GUI：GUI 侧只做**符号存在性**检查（用 inspect 看一眼方法在不在），
真正跑的是 core 的建档/回读/存盘往返。

跑法：python _selftest_ev174.py
"""
import inspect
import os
import struct
import sys

sys.path.insert(0, '.')
from core.editor_core import SaveDoc          # noqa: E402

OK = FAIL = 0
FAILS = []


def ck(name, cond, detail=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  ✅ %s%s' % (name, ('  [' + str(detail) + ']') if detail else ''))
    else:
        FAIL += 1
        FAILS.append(name)
        print('  ❌ %s  %s' % (name, detail))


def fp(name):
    for root in ('E:/YURI/', 'E:/YURI/新建文件夹 (26)/'):
        if os.path.exists(root + name):
            return root + name
    raise SystemExit('找不到基准存档：' + name)


SRC = fp('电厂放下 动画未播放完成.SAV')
KEYS = ['+0xAC', '+0xBC', '+0xD0', '+0x10C', '+0x534', '+0x6E4']


def vec(d, b):
    st = d.building_anim_state(b['base'])
    return '/'.join('%d' % st['vals'][k] for k in KEYS)


print('基准存档：%s（%d B）' % (SRC, os.path.getsize(SRC)))

# ================= 1. 多事件 =================
print('\n[1] create_trigger_multi：3 事件 + 2 行为')
d = SaveDoc(SRC)
n_ev0, n_ac0 = len(d.events()), len(d.actions())
info = d.create_trigger_multi(
    'FA2多事件',
    events=[(13, 30), (8, 0), (13, 60)],
    actions=[(21, 0), (56, 7)])
sid = info['trigger_id']
ck('建档成功（无警告）', not info['warnings'], info['warnings'])
ck('事件数 2 → 5', len(d.events()) == n_ev0 + 3, '%d → %d' % (n_ev0, len(d.events())))
ck('行为数 1 → 3', len(d.actions()) == n_ac0 + 2, '%d → %d' % (n_ac0, len(d.actions())))
ch = [(e['type'], e['arg']) for e in info['events']]
ck('★链序 = 列表逆序（链头是列表最后一条）',
   ch == [(13, 60), (8, 0), (13, 30)], ch)
ck('行为按列表顺序（执行序）',
   [(a['type'], a['arg']) for a in info['actions']] == [(21, 0), (56, 7)],
   [(a['type'], a['arg']) for a in info['actions']])
inst = d.trigger_inst_off(sid)
head = struct.unpack_from('<I', d.plain, inst + 192)[0]
ck('实例 +192 = 链头 = 最后加的那条',
   head == next(e['key'] for e in info['events'] if e['type'] == 13 and e['arg'] == 60),
   '%08X' % head)
evs, acts = d.events(), d.actions()
ck('事件组计数 = 实际数',
   struct.unpack_from('<I', d.plain, evs[0]['obj'] - 4)[0] == len(evs),
   struct.unpack_from('<I', d.plain, evs[0]['obj'] - 4)[0])
ck('行为组计数 = 实际数',
   struct.unpack_from('<I', d.plain, acts[0]['obj'] - 4)[0] == len(acts),
   struct.unpack_from('<I', d.plain, acts[0]['obj'] - 4)[0])
det = d.trigger_detail(sid)
ck('新触发默认持久性 0', str(det.get('repeat')) == '0', det.get('repeat'))
ck('新触发默认启用（可编辑）', det.get('editable') is True)
ck('新触发所属方 = 玩家阵营', det.get('owner') == d.player_house(),
   det.get('owner'))

# ================= 2. add_event / remove_event 往返 =================
print('\n[2] add_event / remove_event 往返')
before = [(e['type'], e['arg']) for e in d.trigger_event_list(sid)]
r = d.add_event(sid, 48, 0)
mid = [(e['type'], e['arg']) for e in d.trigger_event_list(sid)]
ck('add_event 后链头 = 新加的那个', mid[0] == (48, 0), mid)
ck('add_event 不动其它事件', mid[1:] == before, mid[1:])
d.remove_event(sid, r['key'])
after = [(e['type'], e['arg']) for e in d.trigger_event_list(sid)]
ck('remove_event 完整还原', after == before, after)
ck('remove_event 后组计数仍自洽',
   struct.unpack_from('<I', d.plain, d.events()[0]['obj'] - 4)[0] == len(d.events()))

# ================= 3. 多行为（含 125 带路径点） =================
print('\n[3] add_action(125) 带建筑名/目标格')
freed = []
for b in d.buildings():
    if b.get('tag') == 0:
        freed.append((b['x'], b['y']))
cell = freed[0] if freed else None
if cell:
    try:
        k125 = d.add_action(sid, 125, 0, name='ATESLA', cell=cell)
        nm = struct.unpack_from('<8s', d.plain, k125['obj'] + 104)[0]
        wp = struct.unpack_from('<I', d.plain, k125['obj'] + 88)[0]
        ck('行为 125 写入建筑名 ATESLA', nm.rstrip(b'\x00') == b'ATESLA', nm)
        ck('行为 125 写入路径点号', wp >= 0, wp)
    except Exception as e:
        ck('行为 125 建成功', False, str(e)[:90])
else:
    ck('基准档有未挂接建筑可供 125 使用', False, '没有空位')

# ================= 4. 保存 / 重开 =================
print('\n[4] 保存 → 重开往返')
from _paths import out as _out
P1 = _out('ev174_multi.SAV')
d.save(P1)
d2 = SaveDoc(P1)
ck('重开字节数一致', len(d2.plain) == len(d.plain), '%d vs %d' % (len(d2.plain), len(d.plain)))
rec = next((r for r in d2.records if r.kind == '触发' and r.name == 'FA2多事件'), None)
ck('重开找得到新触发', rec is not None, rec.sid if rec else '')
if rec:
    ck('重开后事件链不变',
       [(e['type'], e['arg']) for e in d2.trigger_event_list(rec.sid)] == after,
       [(e['type'], e['arg']) for e in d2.trigger_event_list(rec.sid)])
    ck('重开后行为链不变',
       [(a['type'], a['arg']) for a in d2.trigger_action_list(rec.sid)][:2]
       == [(21, 0), (56, 7)],
       [(a['type'], a['arg']) for a in d2.trigger_action_list(rec.sid)])

# ================= 5. 局部变量配方 =================
print('\n[5] make_local_var_recipe（3 栋 → 变量 1/2/3）')
d3 = SaveDoc(SRC)
free = [(b['x'], b['y']) for b in d3.buildings() if b.get('tag') == 0][:3]
ck('基准档有 ≥3 栋未挂接建筑', len(free) >= 3, free)
if len(free) >= 3:
    n_ev1, n_ac1 = len(d3.events()), len(d3.actions())
    rec5 = d3.make_local_var_recipe(free, [(21, 0)], var_start=1,
                                    result_name='三栋汇总', per_name='到达%d')
    ck('单栋触发 3 条全部建成', len(rec5['per_building']) == 3,
       len(rec5['per_building']))
    ck('变量号 1/2/3', rec5['vars'] == [1, 2, 3], rec5['vars'])
    ck('每栋都挂上了（attached 非空）',
       all(x.get('attached') for x in rec5['per_building']),
       [len(x.get('attached') or []) for x in rec5['per_building']])
    # ★§181：汇总触发现在多一条 13 心跳（纯电平查询 36 不进挂接表就不评估）
    ck('事件 +7（3 单栋 + 3 汇总 36 + 1 心跳 13）', len(d3.events()) == n_ev1 + 7,
       '%d → %d' % (n_ev1, len(d3.events())))
    ck('行为 +4（3 单栋 + 1 汇总）', len(d3.actions()) == n_ac1 + 4,
       '%d → %d' % (n_ac1, len(d3.actions())))
    res = rec5['result']
    ck('汇总触发建成', res is not None)
    if res:
        ce = [(e['type'], e['arg']) for e in res['events']]
        ce36 = [x for x in ce if x[0] == 36]
        ck('★汇总是 3 × 事件 36（AND）', ce36 == [(36, 3), (36, 2), (36, 1)], ce)
        ck('★汇总带 13 心跳', any(t == 13 for t, _ in ce), ce)
        ck('汇总行为 = 设定值', [(a['type'], a['arg']) for a in res['actions']] == [(21, 0)])
        ck('汇总持久性强制 0',
           str(d3.trigger_detail(res['trigger_id']).get('repeat')) == '0')
    for x in rec5['per_building']:
        dd = d3.trigger_detail(x['trigger_id'])
        ck('单栋 %s 持久性 0' % x['name'], str(dd.get('repeat')) == '0', dd.get('repeat'))
        ck('单栋 %s 事件 = 1 进入' % x['name'],
           [(e['type'], e['arg']) for e in d3.trigger_event_list(x['trigger_id'])]
           == [(1, 0)])
        ck('单栋 %s 行为 = 56 设变量 %d' % (x['name'], x['var']),
           [(a['type'], a['arg']) for a in d3.trigger_action_list(x['trigger_id'])]
           == [(56, x['var'])])
    P2 = _out('ev174_var.SAV')
    d3.save(P2)
    d4 = SaveDoc(P2)
    ck('配方档重开正常', len(d4.plain) > 0)
    n_new = len([r for r in d4.records if r.kind == '触发'
                 and (r.name.startswith('到达') or r.name == '三栋汇总')])
    ck('重开后 4 条新触发都在', n_new == 4, n_new)

# ================= 6. 已挂接建筑的处理 =================
print('\n[6] free_attach_slot 三种模式')
d5 = SaveDoc(SRC)
taken = [(b['x'], b['y']) for b in d5.buildings() if b.get('tag')]
ck('基准档存在已挂接建筑', len(taken) >= 1, taken)
if taken:
    x, y = taken[0]
    f0, why0 = d5.free_attach_slot(x, y, mode='zero')
    ck("mode='zero' 拒绝并说明原因", f0 is None and '已经挂在' in (why0 or ''), why0)
    f1, why1 = d5.free_attach_slot(x, y, mode='any')
    ck("mode='any' 给出现成字段", f1 is not None, f1)
    if len(taken) >= 2:
        f2, why2 = d5.free_attach_slot(x, y, mode='force')
        ck("mode='force' 能解挂并返回字段", f2 is not None, why2 or '')
    # ★要用**全新的档**测"全占用"：上面 force 已经解掉一栋了
    d5b = SaveDoc(SRC)
    try:
        d5b.make_local_var_recipe(taken, [(21, 0)], result_name='不该建')
        ck("'zero' 模式下全占用 ⇒ 应中止", False, '居然没报错')
    except RuntimeError as e:
        ck("'zero' 模式下全占用 ⇒ 中止且不建汇总",
           '汇总触发未建' in str(e), str(e)[:70])
        ck("全占用时一条触发都没建", len(
            [r for r in d5b.records if r.kind == '触发']) == len(
            [r for r in SaveDoc(SRC).records if r.kind == '触发']),
           len([r for r in d5b.records if r.kind == '触发']))

# ================= 7. GUI 符号存在性（不启动窗口） =================
print('\n[7] GUI 侧接口存在（静态检查，不启动窗口）')
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location('ra2gui', 'RA2存档编辑器.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules['ra2gui'] = mod
    spec.loader.exec_module(mod)
    cls = None
    for nm, obj in vars(mod).items():
        if inspect.isclass(obj) and hasattr(obj, '_fill_events'):
            cls = obj
            break
    ck('找到触发编辑器窗口类', cls is not None, cls.__name__ if cls else '')
    if cls:
        for m in ('_fill_events', '_ev_finish', '_ev_pick', 'add_event_gui',
                  'del_event_gui', 'apply_event', 'new_trigger'):
            ck('方法 %s 存在' % m, callable(getattr(cls, m, None)))
    ck('主窗口有 local_var_wizard', any(
        callable(getattr(v, 'local_var_wizard', None)) for v in vars(mod).values()
        if inspect.isclass(v)))
except Exception as e:
    ck('GUI 模块可导入（无窗口）', False, str(e)[:120])

# ================= 8. 建造动画未被破坏 =================
print('\n[8] 建造动画字段未受影响（§173 回归）')
d6 = SaveDoc(SRC)
base_vecs = {b['type'] + str(b['x']) + ',' + str(b['y']): vec(d6, b)
             for b in d6.buildings()}
d6.create_trigger_multi('回归', [(13, 5)], [(21, 0)])
d6.make_local_var_recipe([(b['x'], b['y']) for b in d6.buildings() if not b.get('tag')][:2],
                         [(21, 0)], result_name='回归汇总')
after_vecs = {b['type'] + str(b['x']) + ',' + str(b['y']): vec(d6, b)
              for b in d6.buildings()}
ck('建触发前后建筑动画六元组全等', base_vecs == after_vecs,
   '差异 %d 处' % len([k for k in base_vecs if base_vecs.get(k) != after_vecs.get(k)]))

print('\n' + '=' * 56)
print('SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if FAIL == 0 else 'FAIL', OK, FAIL))
if FAILS:
    print('失败项：')
    for f in FAILS:
        print('  ·', f)
sys.exit(0 if FAIL == 0 else 1)
