# -*- coding: utf-8 -*-
"""新写触发产品化自检：创建 → 保存 → 重读全链路 + 独立性 + 老触发不受影响。"""
import os
import sys
import tempfile

sys.path.insert(0, r'C:\Users\zhao5\Desktop\RA2存档分析工具\RA2存档编辑器')
from core.editor_core import SaveDoc

SRC = r'C:\Users\zhao5\Desktop\RA2存档分析工具\RA2存档编辑器\_exp\ALL01UMD_2触发.sav'
ok = True
def check(label, cond, extra=''):
    global ok
    print('[%s] %s %s' % ('PASS ✓' if cond else 'FAIL ✗', label, extra))
    ok = ok and bool(cond)

d = SaveDoc(SRC)
check('can_create_trigger', d.can_create_trigger())

# 场景：先改 B 的流逝时间参数（制造 offset-keyed pending），再创建新触发（验证簿记不乱）
evB = [e for e in d.events() if e['type'] == 8][0]
n_ev_before = len(d.events())
info = d.create_trigger('测试触发D', ev_type=13, ev_arg=30, ac_type=16, ac_arg=0)
print('create ->', info)
check('触发/标签 ID 顺延', info['trigger_id'] == '01000004' and info['tag_id'] == '01000005',
      '%s/%s' % (info['trigger_id'], info['tag_id']))
check('事件/行为总数 +1', len(d.events()) == n_ev_before + 1 and len(d.actions()) == 3,
      '%d/%d' % (len(d.events()), len(d.actions())))
recs = {r.sid: r for r in d.records if r.kind == '触发'}
check('新触发在记录表', '01000004' in recs and recs['01000004'].name == '测试触发D')
check('新触发启用', recs['01000004'].enabled is True)
evD = [e for e in d.events() if e['off'] == info['ev_off']]
check('新事件对象可寻址', len(evD) == 1 and evD[0]['type'] == 13 and evD[0]['arg'] == 30,
      str((evD[0]['type'], evD[0]['arg'])) if evD else '-')
acD = [a for a in d.actions() if a['off'] == info['ac_off']]
check('新行为对象可寻址', len(acD) == 1 and acD[0]['type'] == 16)

# 改新触发自己的事件参数 → 不影响 A/B
d.set_event(evD[0], arg=35)
check('改新事件后 B 事件不受影响', all(e['type'] != 13 or e['arg'] != 35
                                      for e in d.events() if e['off'] != info['ev_off']))

out = os.path.join(tempfile.gettempdir(), '_newtrig_rt.SAV')
d.save(out)
d2 = SaveDoc(out)
recs2 = {r.sid: r for r in d2.records if r.kind == '触发'}
check('重读: 3 触发', len(recs2) == 3, str(sorted(recs2)))
check('重读: 新触发名/启用', recs2.get('01000004') is not None
      and recs2['01000004'].name == '测试触发D' and recs2['01000004'].enabled is True)
evs2 = d2.events()
check('重读: 事件 13/8/13', sorted(e['type'] for e in evs2) == [8, 13, 13],
      str([(e['type'], e['arg']) for e in evs2]))
d_ev = [e for e in evs2 if e['arg'] == 35]
check('重读: 新事件参数 35', len(d_ev) == 1 and d_ev[0]['type'] == 13)
a_ev = [e for e in evs2 if e['type'] == 13 and e['arg'] == 10]
check('重读: A 事件不受影响', len(a_ev) == 1)
acts2 = d2.actions()
check('重读: 行为 32/16/16', sorted(a['type'] for a in acts2) == [16, 16, 32])
tags2 = [r for r in d2.records if r.kind == '标签']
check('重读: 标签 3 个含 C 1', len(tags2) == 3 and any(r.name == '测试触发D 1' for r in tags2),
      str([r.name for r in tags2]))

# 附加：禁用新触发往返
d3 = SaveDoc(out)
rD = [r for r in d3.records if r.sid == '01000004'][0]
d3.toggle_trigger(rD, False)
out2 = out + '2'
d3.save(out2)
d4 = SaveDoc(out2)
rD2 = [r for r in d4.records if r.sid == '01000004'][0]
check('禁用新触发往返', rD2.enabled is False)

# ★2026-10-15：create_trigger(attach=[(格x,格y)]) —— 新写触发 + 挂建筑 单次调用
MAP3 = r'E:\YURI\all01umd.map'
dA = SaveDoc(SRC)
dA.attach_map(MAP3 if os.path.exists(MAP3) else None)
t2t = dA._map.tag_trigger_map() if dA._map else {}
free = None
if dA._map and dA.can_create_trigger():
    for s in dA._map.structures():
        if s['tagid'] in t2t:
            continue
        f, err = dA.locate_building_field(s['x'], s['y'], mode='zero')
        if f is not None:
            free = (s, f)
            break
if free is None:
    check('attach: 跳过（无可用未挂建筑 / 布局不支持）', True)
else:
    s, f = free
    inf = dA.create_trigger('挂接自检E', 48, 0, 1, 0, attach=[(s['x'], s['y'])])
    check('attach: 单次调用返回 attached', len(inf['attached']) == 1 and not inf['warnings'],
          '%s %s' % (inf['attached'], inf['warnings']))
    ov = [x for x in dA.attach_overview() if x['trigger_id'] == inf['trigger_id']]
    coords = [(b['cx'], b['cy']) for b in ov[0]['buildings']] if ov else []
    check('attach: 总览解析建筑=目标', bool(ov) and ov[0]['count'] == 1
          and coords == [(s['x'], s['y'])], '%s count=%s' % (coords, ov[0]['count'] if ov else '-'))
    out3 = os.path.join(tempfile.gettempdir(), '_newtrig_attach.SAV')
    dA.save(out3)
    dR = SaveDoc(out3)
    ovR = [x for x in dR.attach_overview() if x['trigger_id'] == inf['trigger_id']]
    check('attach: 存盘重读后挂接保持', bool(ovR) and ovR[0]['count'] == 1
          and [(b['cx'], b['cy']) for b in ovR[0]['buildings']] == [(s['x'], s['y'])])
    # 事件/行为类型原样 + 六组计数一致
    evR = [e for e in dR.events() if e['type'] == 48]
    check('attach: 新触发事件=48', len(evR) == 1)
    os.remove(out3)

# ★★★引擎可见的六组计数注册字段必须与实际对象数一致（2026-10-14 读档崩溃归因：
#   create_trigger 曾漏加 事件/行为 组计数 ⇒ 引擎按旧 count 读流 ⇒ 载入即崩；
#   我们的解析器走签名扫描不读计数，所以必须显式断言）。
#   2026-10-14 二次归因：少建 TagNode/CellTag 会「能读档但不生效」，
#   所以 node/cell 两组也要一起断言。
from core.editor_core import (TRIG_INSTANCE_GUID, TRIG_TAGOBJ_GUID, TRIG_NODE_GUID,
                              TRIG_CELLGUID, TRIG_OBJ_SIZE, TAG_OBJ_SIZE,
                              TRIG_NODE_SIZE, TRIG_CELL_SIZE)
for tag_, path_ in (('创建后', out), ('禁用后', out2)):
    dd = SaveDoc(path_)
    ppl = bytes(dd.plain)
    groups = []
    for nm, guid, size in (('tag', TRIG_TAGOBJ_GUID, TAG_OBJ_SIZE),
                           ('trig', TRIG_INSTANCE_GUID, TRIG_OBJ_SIZE),
                           ('node', TRIG_NODE_GUID, TRIG_NODE_SIZE),
                           ('cell', TRIG_CELLGUID, TRIG_CELL_SIZE)):
        pos, q = [], 0
        while True:
            i = ppl.find(guid, q)
            if i < 0:
                break
            pos.append(i)
            q = i + 1
        groups.append((nm, int.from_bytes(ppl[pos[0] - 4:pos[0]], 'little'), len(pos)))
    for nm, items in (('event', dd.events()), ('action', dd.actions())):
        objs = sorted(o['obj'] for o in items)
        # 首对象的完整 16 字节 GUID ⇒ 全档数同类对象；计数注册字段 = 首对象前 4 字节
        guid16 = bytes(ppl[objs[0]:objs[0] + 16])
        pos, q = [], 0
        while True:
            i = ppl.find(guid16, q)
            if i < 0:
                break
            pos.append(i)
            q = i + 1
        groups.append((nm, int.from_bytes(ppl[objs[0] - 4:objs[0]], 'little'), len(pos)))
    bad = ['%s 字段=%d 实际=%d' % t for t in groups if t[1] != t[2]]
    check('%s: 六组计数注册字段一致' % tag_, not bad, '; '.join(bad))
os.remove(out)
os.remove(out2)

print()
print('ALL PASS ✓✓✓' if ok else '存在 FAIL ✗')
sys.exit(0 if ok else 1)
