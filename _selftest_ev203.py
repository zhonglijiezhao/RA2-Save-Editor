# -*- coding: utf-8 -*-
"""§203 自检：没有 CellTag 节点的触发 —— 不再断言"已执行"，并且能补建节点挂上去。

玩家实测（2026-10-23，`ALL01UMD.sav`）：触发 01000437「a尤里中心铁幕解白宫铁幕！」
在触发页显示正常，挂接页却说"已执行过、不能挂"。查明：
  · 触发定义完好（实例 + 1 事件 + 8 行为 + 标签对象），只是**存档里没有 CellTag 节点**；
  · 引擎只遍历 CellTag 实例链 ⇒ 没节点 = 这条触发在本档不会被评估；
  · 但"执行过"与"从来没挂上"在这份字节里同形 ⇒ **不许下结论**（§119 早就定过这条）。
⇒ 改法：① 摘要按证据分情况（ev_fired / 计时型 / 回调型），不再说"已执行"；
        ② 新增 `ensure_attach_node()` 克隆 TagNode+CellTag 补建节点；③ GUI 加「🧩 补建挂接节点」。

用法：python _selftest_ev203.py [存档] [地图]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
try:
    from core.editor_core import SaveDoc
except Exception:
    from editor_core import SaveDoc

SAV = sys.argv[1] if len(sys.argv) > 1 else 'E:/YURI/ALL01UMD.sav'
MAP = sys.argv[2] if len(sys.argv) > 2 else 'C:/Users/zhao5/Desktop/all01umd.map'
SID = '01000437'

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
print('§203 自检：无节点触发的判定与补建')
ck('存档存在', os.path.isfile(SAV), SAV)
if not os.path.isfile(SAV):
    sys.exit(1)

d = SaveDoc(SAV)
d.attach_map(MAP)
orph = {o['trigger_id']: o for o in d.attach_orphan_tags()}
ck('这条触发确实在"无节点"名单里', SID in orph, str(list(orph)[:4]))
o = orph.get(SID) or {}
ck('标签对象还在（定义完好）', bool(d.trigger_objects(SID).get('tag_obj')), '')
ck('有事件/行为定义',
   len(d.trigger_event_list(SID)) >= 1 and len(d.trigger_actions(SID)) >= 1,
   '%d 事件 / %d 行为' % (len(d.trigger_event_list(SID)), len(d.trigger_actions(SID))))
ck('★不再报 executed 当结论用（接口保留但另有证据字段）',
   'ev_fired' in o and 'kind' in o and 'ev_types' in o,
   'ev_fired=%s kind=%s ev=%s' % (o.get('ev_fired'), o.get('kind'), o.get('ev_types')))
ck('这条的事件是"被摧毁"(7) ⇒ 回调型', o.get('kind') == 'callback' and 7 in (o.get('ev_types') or []),
   str(o.get('ev_types')))

# ---- 补建节点 ----
d2 = SaveDoc(SAV)
d2.attach_map(MAP)
n0 = len(d2.celltag_nodes())
r = d2.ensure_attach_node(SID, ticks=0)
ck('补建成功', bool(r.get('created')), str({k: r.get(k) for k in ('node_key', 'cell_key')}))
ck('节点数 +1', len(d2.celltag_nodes()) == n0 + 1, '%d -> %d' % (n0, len(d2.celltag_nodes())))
ov = [it for it in d2.attach_overview() if it['trigger_id'] == SID]
ck('挂接总览里出现了这条触发', len(ov) == 1, str(len(ov)))
ck('初始挂接数 = 0', bool(ov) and ov[0]['count'] == 0, str(ov[0]['count']) if ov else '')
ck('它已从"无节点"名单里消失',
   not any(x['trigger_id'] == SID for x in d2.attach_orphan_tags()), '')
ck('重复调用只补一次', d2.ensure_attach_node(SID)['created'] is False, '')

# ---- 挂一栋建筑 + 落盘往返 ----
y = [b for b in d2.buildings() if b['type'].upper() == 'YACOMD']
ck('档里有 YACOMD 可挂', bool(y), str([(b['x'], b['y']) for b in y][:2]))
if y and ov:
    d2.attach_trigger_to_building(ov[0]['node'], y[0]['base'])
    nb = [b for b in d2.buildings() if b['base'] == y[0]['base']][0]
    ck('建筑挂接键 = 新 CellTag 的自键', nb['tag'] == r['cell_key'],
       '%#x vs %#x' % (nb['tag'], r['cell_key']))
    out = os.path.join(HERE, '_selftest_out', 'ev203_补建节点.SAV')
    if not os.path.isdir(os.path.dirname(out)):
        os.makedirs(os.path.dirname(out))
    d2.save(out, backup=False)
    d3 = SaveDoc(out)
    d3.attach_map(MAP)
    ov3 = [it for it in d3.attach_overview() if it['trigger_id'] == SID]
    ck('落盘重读：节点还在、挂接数 = 1',
       len(ov3) == 1 and ov3[0]['count'] == 1,
       str([it['count'] for it in ov3]))
    ck('落盘重读：挂接的就是那栋 YACOMD',
       bool(ov3) and any(abs(b['cx'] - y[0]['x']) <= 1 and abs(b['cy'] - y[0]['y']) <= 1
                         for b in ov3[0]['buildings']),
       str([(b['cx'], b['cy']) for b in ov3[0]['buildings']]) if ov3 else '')
    ck('落盘重读：建筑数不变（没写坏别的对象）',
       len(d3.buildings()) == len(d.buildings()), '%d vs %d' % (len(d3.buildings()), len(d.buildings())))
    ck('落盘重读：触发总数不变', len(d3.records) == len(d.records),
       '%d vs %d' % (len(d3.records), len(d.records)))

print('-' * 72)
print('§203 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
