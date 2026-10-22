# -*- coding: utf-8 -*-
"""挂接管理自检：概览解析 + 挂上/解除往返（不落盘）。
用法: python _selftest_attach.py [存档] [地图]
"""
import sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc

SAV = sys.argv[1] if len(sys.argv) > 1 else r'E:\YURI\ALL01UMD.sav'
MAP = sys.argv[2] if len(sys.argv) > 2 else r'E:\YURI\all01umd.map'

ok = True
d = SaveDoc(SAV)
nodes = d.celltag_nodes()
print('[1] CellTag 节点: %d 个' % len(nodes))

mode = d.attach_map(MAP if os.path.exists(MAP) else None)
print('[2] 挂载地图 %s: %s' % (MAP, mode))

ov = d.attach_overview()
resolved = [it for it in ov if it['buildings'] and it['tagid']]
print('[3] 挂接总览: %d 节点，其中解出建筑+标签的 %d 个' % (len(ov), len(resolved)))
for it in resolved[:8]:
    bsum = '；'.join('%s@(%d,%d)' % (b['type'], b['cx'], b['cy'])
                     for b in it['buildings'])
    print('    %s %r ← %s: %s' % (it['trigger_id'], it['trigger_name'],
                                  it['tagid'], bsum))

# 往返：选一个已挂标签 + 一栋未挂建筑
m = d._map
t2t = m.tag_trigger_map()
free = [s for s in m.structures() if s['tagid'] not in t2t]
tgt = None
for s in free:
    field, err = d.locate_building_field(s['x'], s['y'], mode='zero')
    if field is not None:
        tgt = (s, field)
        break
if not tgt:
    print('[4] 未找到可定位的未挂接建筑，跳过往返测试')
    sys.exit(1)
s, field = tgt
node = resolved[0]['node']
print('[4] 往返目标: %s(%d,%d) 字段@%d ← 触发 %s' % (
    s['type'], s['x'], s['y'], field, resolved[0]['trigger_id']))

old = d.attach_trigger_to_building(node, field)
pl2 = d.plain
v_after_attach = struct_ok = None
import struct
v_after_attach = struct.unpack_from('<I', pl2, field)[0]
cnt = struct.unpack_from('<I', pl2, [n for n in d.celltag_nodes()
                                     if n['node'] == node][0]['count_pos'])[0]
struct_ok = (v_after_attach == resolved[0]['x'] and cnt == resolved[0]['count'] + 1)
print('[5] 挂上: 字段=0x%08X 计数=%d  %s' % (
    v_after_attach, cnt, 'PASS ✓' if struct_ok else 'FAIL ✗'))
ok &= struct_ok

back = d.detach_building(node, field)
v2 = struct.unpack_from('<I', pl2, field)[0]
cnt2 = struct.unpack_from('<I', pl2, [n for n in d.celltag_nodes()
                                      if n['node'] == node][0]['count_pos'])[0]
back_ok = (v2 == 0 and cnt2 == resolved[0]['count'] and back == resolved[0]['x'])
print('[6] 解除: 字段=0x%08X 计数=%d  %s' % (
    v2, cnt2, 'PASS ✓' if back_ok else 'FAIL ✗'))
ok &= back_ok

# 防护测试：重复挂/解
try:
    d.attach_trigger_to_building(node, field)
    d.attach_trigger_to_building(node, field)
    print('[7] 重复挂接防护: FAIL ✗（竟然允许）')
    ok = False
except RuntimeError:
    print('[7] 重复挂接防护: PASS ✓（已挂时拒绝）')

# [8] ★2026-10-15：新写触发（挂接数 0）也必须出现在总览里，挂接后能解析出建筑
#     （旧实现只列「有建筑」的 CellTag ⇒ 刚建好的触发在挂接页里完全选不到）
import struct as _st
try:
    d2 = SaveDoc(SAV)
    d2.attach_map(MAP if os.path.exists(MAP) else None)
    tgt2 = None
    if d2._map and d2.can_create_trigger():
        for s_ in d2._map.structures():
            if s_['tagid'] in t2t:
                continue
            f_, e_ = d2.locate_building_field(s_['x'], s_['y'], mode='zero')
            if f_ is not None:
                tgt2 = (s_, f_)
                break
    if tgt2 is None:
        print('[8] 新写触发挂接测试: 跳过（无可用建筑 / 该档布局不支持）')
    else:
        s_, f_ = tgt2
        info = d2.create_trigger('自检挂接', 48, 0, 1, 0)
        ov2 = [x for x in d2.attach_overview() if x['trigger_id'] == info['trigger_id']]
        ok0 = bool(ov2) and ov2[0]['count'] == 0 and not ov2[0]['buildings']
        print('[8] 未挂接的新触发出现在总览: %s（count=%s）'
              % ('PASS ✓' if ok0 else 'FAIL ✗', ov2[0]['count'] if ov2 else '-'))
        ok &= ok0
        if ok0:
            d2.attach_trigger_to_building(ov2[0]['node'], f_)
            ov3 = [x for x in d2.attach_overview()
                   if x['trigger_id'] == info['trigger_id']][0]
            coords = [(b['cx'], b['cy']) for b in ov3['buildings']]
            ok1 = ov3['count'] == 1 and coords == [(s_['x'], s_['y'])]
            print('[9] 挂接后总览解析建筑: %s（count=%d 建筑=%s）'
                  % ('PASS ✓' if ok1 else 'FAIL ✗', ov3['count'], coords))
            ok &= ok1
except Exception as e:
    print('[8] 新写触发挂接测试: FAIL ✗（异常 %s）' % e)
    ok = False

print('结果:', 'PASS ✓' if ok else 'FAIL ✗')
sys.exit(0 if ok else 1)
