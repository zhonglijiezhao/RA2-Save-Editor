# -*- coding: utf-8 -*-
"""「修复缺组件触发」自检（2026-10-15）：
基档（D/B 缺 TagNode/CellTag）→ 修 D → 全链路断言 + 保存重读 + 老触发不受影响。
覆盖：set_event 缺 node 时的根因提示、键链直写帧数、计时表登记、组计数、幂等（already）。"""
import os
import sys
import struct
import tempfile

sys.path.insert(0, r'C:\Users\zhao5\Desktop\RA2存档分析工具\RA2存档编辑器')
from core.editor_core import SaveDoc

SRC = r'E:\YURI\新建文件夹 (5)\原档 三触发启用.sav'
if not os.path.exists(SRC):
    import glob
    cands = glob.glob(r'E:\YURI\新建文件夹 (*)\原档 三触发启用.sav')
    SRC = max(cands) if cands else SRC   # 玩家会不断新建"新建文件夹"，取最新的
ok = True
def check(label, cond, extra=''):
    global ok
    print('[%s] %s %s' % ('PASS ✓' if cond else 'FAIL ✗', label, extra))
    ok = ok and bool(cond)

u32 = lambda b, o: struct.unpack_from('<I', b, o)[0]

d = SaveDoc(SRC)
st = {s['name']: s for s in d.runtime_status()}
check('诊断: A 有组件', st['A']['has_node'] and st['A']['has_cell'])
check('诊断: D 缺 node/cell', not st['D']['has_node'] and not st['D']['has_cell'])
check('诊断: B 也缺', not st['B']['has_node'] and not st['B']['has_cell'])
tl0 = d.celltag_timer_list()
n0 = len(d.events())
ncell0 = len(d.celltag_nodes())

# ---- 场景 1：把 D 事件改成 13/10 → 应得「缺 TagNode」根因提示（而不是旧的"应为 0"）----
instD = st['D']['inst_off']
ek = u32(d.plain, instD + 192)
evD = next(e for e in d.events() if u32(d.plain, e['obj'] + 16) == ek)
d.set_event(evD, etype=13, arg=10)
note = getattr(d, 'time_sync_note', '')
check('set_event 提示指向根因(缺TagNode)', 'TagNode' in note and '不会生效' in note, note[:40])
check('set_event 提示不再出现"应为 0"', '应为' not in note)

# ---- 场景 2：修复 D（ticks 应自动 = 10×15 = 150，enabled 按记录=1）----
info = d.repair_trigger_runtime(st['D']['inst_key'])
check('修复: not already', info['already'] is False)
check('修复: ticks=150', info['ticks'] == 150, str(info['ticks']))
m = d._instance_maps()
node_off = m['node_of_inst'][info['inst_key']]
check('修复: node+56 = D实例键', u32(d.plain, node_off + 56) == info['inst_key'])
check('修复: node+80 = 150', u32(d.plain, node_off + 80) == 150)
check('修复: node+88 = 1', u32(d.plain, node_off + 88) == 1)
cell_key = m['cell_of_tag'][m['tag_of_inst'][info['inst_key']]]
check('修复: cell_of_tag 存自键', cell_key == info['cell_key'], hex(cell_key))
cell_off = next(c['node'] for c in d.celltag_nodes() if c['x'] == cell_key)
check('修复: cell+56 = D标签键', u32(d.plain, cell_off + 56) == m['tag_of_inst'][info['inst_key']])
check('修复: cell+60 = node键', u32(d.plain, cell_off + 60) == info['node_key'])
check('修复: cell+64 = 0', u32(d.plain, cell_off + 64) == 0)
tl = d.celltag_timer_list()
check('修复: 计时表 = [A_cell, D_cell]', tl['keys'] == [tl0['keys'][0], info['cell_key']]
      and tl['n'] == 2, str([hex(k) for k in tl['keys']]))
# 组计数：node/cell 各 +1
from core.editor_core import TRIG_NODE_GUID, TRIG_CELLGUID
for guid, want in ((TRIG_NODE_GUID, 2), (TRIG_CELLGUID, 2)):
    p0 = d.plain.find(guid)
    check('修复: 组计数 %d' % want, u32(d.plain, p0 - 4) == want)
check('修复: 事件总数不变', len(d.events()) == n0)
check('修复: A 不受影响（仍有 node）',
      next(s for s in d.runtime_status() if s['name'] == 'A')['has_node'])

# ---- 场景 3：幂等 —— 再修一次应 already=True，不插对象（用键定位，偏移已后移）----
info2 = d.repair_trigger_runtime(st['D']['inst_key'])
check('幂等: already=True', info2['already'] is True and info2['node_key'] == info['node_key'])
check('幂等: 计时表仍 2 项', d.celltag_timer_list()['n'] == 2)

# ---- 场景 4：修复后再改秒数 → 键链直写 node+80（不弹警告）----
evD2 = next(e for e in d.events() if u32(d.plain, e['obj'] + 16) == ek)
d.set_event(evD2, arg=30)
check('修复后改秒数: node+80=450', u32(d.plain, node_off + 80) == 450)
check('修复后改秒数: 提示=已同步', (getattr(d, 'time_sync_note', '') or '').startswith('已同步'))

# ---- 场景 4b：修 B（事件1=进入事件 → ticks=0），再改成 8 号事件 → 应自动武装帧 15 + 登记计时表
infoB = d.repair_trigger_runtime(st['B']['inst_key'])
check('修B: ticks=0（事件1无计时）', infoB['ticks'] == 0, str(infoB['ticks']))
mB = d._instance_maps()
nodeB = mB['node_of_inst'][infoB['inst_key']]
instB = mB['inst'][st['B']['inst_key']]   # ★修 D 插入过字节 ⇒ 必须按键重解偏移
ekB = u32(d.plain, instB + 192)
evB = next(e for e in d.events() if u32(d.plain, e['obj'] + 16) == ekB)
d.set_event(evB, etype=8, arg=0)
nodeB2 = d._instance_maps()['node_of_inst'][infoB['inst_key']]  # 登记插字节后重定位
check('B改8号: node+80=15（自动武装）', u32(d.plain, nodeB2 + 80) == 15,
      str(u32(d.plain, nodeB2 + 80)))
check('B改8号: 提示=已武装', '已武装' in (getattr(d, 'time_sync_note', '') or ''))
check('B改8号: 计时表 n=3', d.celltag_timer_list()['n'] == 3)
check('B改8号: 表含 B_cell', infoB['cell_key'] in d.celltag_timer_list()['keys'])
# 基档上的 D 事件改 8（缺组件）→ 应得根因提示（这是用户实测踩到的缺口）
d0 = SaveDoc(SRC)
st0 = {s['name']: s for s in d0.runtime_status()}
ek0 = u32(d0.plain, st0['D']['inst_off'] + 192)
ev0 = next(e for e in d0.events() if u32(d0.plain, e['obj'] + 16) == ek0)
d0.set_event(ev0, etype=8, arg=0)
check('缺组件改8号: 根因提示', (getattr(d0, 'time_sync_note', '') or '').startswith('⚠')
      and 'TagNode' in (getattr(d0, 'time_sync_note', '') or ''))

# ---- 场景 5：保存 → 重读全链路 ----
out = os.path.join(tempfile.gettempdir(), '_repair_rt.SAV')
d.save(out)
d2 = SaveDoc(out)
st2 = {s['name']: s for s in d2.runtime_status()}
check('重读: D 有 node+cell', st2['D']['has_node'] and st2['D']['has_cell'])
check('重读: D ticks=450', st2['D']['ticks'] == 450, str(st2['D']['ticks']))
check('重读: B 已武装 ticks=15', st2['B']['has_node'] and st2['B']['ticks'] == 15,
      str(st2['B']['ticks']))
evs2 = d2.events()
ek2 = u32(d2.plain, st2['D']['inst_off'] + 192)
ev2 = next(e for e in evs2 if u32(d2.plain, e['obj'] + 16) == ek2)
check('重读: D 事件 13/30', ev2['type'] == 13 and ev2['arg'] == 30)
tl2 = d2.celltag_timer_list()
check('重读: 计时表保持 3 项', tl2 is not None and tl2['n'] == 3,
      str([hex(k) for k in tl2['keys']]) if tl2 else '-')
check('重读: 组计数 node/cell=3', all(
    u32(d2.plain, d2.plain.find(g) - 4) == 3 for g in (TRIG_NODE_GUID, TRIG_CELLGUID)))

print()
print('REPAIR SELFTEST %s' % ('PASS' if ok else 'FAIL'))
sys.exit(0 if ok else 1)
