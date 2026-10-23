# -*- coding: utf-8 -*-
"""§202 自检：挂接页「可挂建筑」以**存档**为准（不看地图的 Tag 列）。

玩家实测（2026-10-23，`SAVE1D3F.SAV` + `all01umd.map`）：
  · 地图 `[Structures]` 里 YACOMD 的 Tag 列写着 `01000438`（地图认为它挂着触发）
  · 但存档里那栋 (180,96) 的挂接键 = `0x00000000`（真没挂）
  · 旧实现按地图 Tag 判断"已挂接" ⇒ ③ 里搜 YACOMD 一条都没有，玩家没法挂
⇒ 改法：③ = `buildings()` 里挂接键 == 0 的那些；地图只用来补"地图建筑ID"。

用法：python _selftest_ev202.py [存档] [地图]
"""
from __future__ import print_function

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

try:
    from core.editor_core import SaveDoc
except Exception:
    from editor_core import SaveDoc

SAV = sys.argv[1] if len(sys.argv) > 1 else 'C:/Users/zhao5/Desktop/SAVE1D3F.SAV'
MAP = sys.argv[2] if len(sys.argv) > 2 else 'C:/Users/zhao5/Desktop/all01umd.map'

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
print('§202 自检：可挂建筑 = 存档口径')
ck('存档存在', os.path.isfile(SAV), SAV)
if not os.path.isfile(SAV):
    sys.exit(1)

d = SaveDoc(SAV)
allb = d.buildings()
ck('建筑列表非空', len(allb) > 0, '%d 栋' % len(allb))

# ---------- [1] 不挂地图也能列出（存档自足） ----------
free0 = d.attach_free_buildings()
ck('未挂地图也有可挂建筑', len(free0) > 0, '%d 栋' % len(free0))
ck('★全部是挂接键为 0 的建筑',
   all(0 == b['tag'] for b in allb if b['base'] in {x['base'] for x in free0}), '')
ck('★已挂接的一栋都没混进来',
   not (set(x['base'] for x in free0) & set(b['base'] for b in allb if b['tag'])),
   '已挂 %d 栋' % sum(1 for b in allb if b['tag']))
ck('未挂地图时 map_idx 为空', all(x['map_idx'] in (None, '') for x in free0), '')

# ---------- [2] 挂上地图：地图说"已挂"的不许再被藏起来 ----------
mapped = False
if os.path.isfile(MAP):
    d.attach_map(MAP)
    mapped = bool(getattr(d, '_map', None))
ck('地图挂载成功', mapped, MAP if os.path.isfile(MAP) else '（地图不存在，跳过地图相关断言）')

if mapped:
    free1 = d.attach_free_buildings()
    ck('★可挂建筑数量与"存档挂接键=0"一致',
       len(free1) == sum(1 for b in allb if not b['tag']),
       '%d vs %d' % (len(free1), sum(1 for b in allb if not b['tag'])))
    # 地图 Tag 列说已挂、但存档没挂的那种建筑
    ms = {(s['x'], s['y']): s for s in d._map.structures()}
    t2t = d._map.tag_trigger_map()
    tricky = [b for b in allb if not b['tag']
              and any((ms.get((b['x'] + dx, b['y'] + dy)) or {}).get('tagid') in t2t
                      for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)))]
    ck('这份档里确实有「地图说已挂 / 存档没挂」的建筑', len(tricky) > 0, '%d 栋' % len(tricky))
    ck('★它们全部出现在③（不再被地图的 Tag 列藏掉）',
       all(b['base'] in {x['base'] for x in free1} for b in tricky),
       '漏掉 %d 栋' % sum(1 for b in tricky if b['base'] not in {x['base'] for x in free1}))
    y = [x for x in free1 if x['type'].upper() == 'YACOMD']
    ck('YACOMD 在③里能找到', bool(y), str(y[:1]))
    ck('YACOMD 补到了地图建筑ID', bool(y) and y[0]['map_idx'],
       str(y[0]['map_idx']) if y else '')

# ---------- [3] 真挂一栋：挂上后必须从③消失 ----------
ov = d.attach_overview()
node_it = None
for it in ov:
    if it['buildings'] is not None:
        node_it = it
        break
ck('找到可用的 CellTag 节点', node_it is not None,
   ('node=%#x 触发=%s' % (node_it['node'], node_it['trigger_id'])) if node_it else '')
if node_it:
    pool = [x for x in d.attach_free_buildings() if x['type'].upper() != 'YACOMD']
    tgt = pool[0] if pool else None
    ck('有可挂的目标建筑', tgt is not None, str(tgt))
    if tgt:
        # ★§202：直接用手上的记录偏移（`buildings()` 的 base = 挂接字段），
        #   不靠坐标反查（引擎新建的建筑 +160 是 iso 锚点，反查不到）
        f = tgt['base']
        ck('拿到该建筑的挂接字段（记录 base）', f is not None, hex(f or 0))
        if f is not None:
            old = d.attach_trigger_to_building(node_it['node'], f)
            ck('原字段值 = 0', old == 0, str(old))
            nb = [b for b in d.buildings() if b['base'] == f][0]
            ck('挂接后存档里挂接键 = 该 CellTag 的自键',
               nb['tag'] == node_it['x'], '%#x vs %#x' % (nb['tag'], node_it['x']))
            ck('★挂上后它从③消失',
               f not in {x['base'] for x in d.attach_free_buildings()}, '')
            it2 = [o for o in d.attach_overview() if o['node'] == node_it['node']][0]
            ck('该节点挂接建筑数 +1', it2['count'] == node_it['count'] + 1,
               '%d -> %d' % (node_it['count'], it2['count']))

print('-' * 72)
print('§202 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
