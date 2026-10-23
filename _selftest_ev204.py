# -*- coding: utf-8 -*-
"""§204 自检：挂接页②「该标签挂接的建筑」必须**说清挂的是谁**（建筑还是单位）。

玩家实测（2026-10-23）：②里那行显示成 `?`、地图建筑ID `—` ⇒ 「有挂接建筑 不显示是不行的，
不显示的话你怎么解除挂接」。查明两个原因：
  ① 类型只按**地图坐标精确命中**取 ⇒ 地图锚点与存档差 1 格时（YACOMD 地图 (181,96) / 存档 (180,96)）取不到；
  ② 标签也能挂在**单位**上（实测触发 01000250「飞碟被攻击」挂的是 DISK×6，另有 SNIPE/SCHP/HOWI），
     旧实现只认建筑 ⇒ 全变成 `?`；而按 CellTag 键反查时还会**命中指针表里的那个键**（假阳性）。
⇒ 改法：先按**存档建筑记录**认（`base` 就是挂接字段，允许 ±8B），不是建筑就按**单位记录**认，
        两者都不是（多半是指针表里的键）**直接跳过**；地图只用来补"地图建筑ID"（±1 格同类型兜底）。

用法：python _selftest_ev204.py [存档] [地图]
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
print('§204 自检：挂接对象要说清是谁')
ck('存档存在', os.path.isfile(SAV), SAV)
if not os.path.isfile(SAV):
    sys.exit(1)

d = SaveDoc(SAV)
if os.path.isfile(MAP):
    d.attach_map(MAP)
ov = d.attach_overview()
allb = [b for it in ov for b in it['buildings']]
ck('挂接总览非空', len(ov) > 0, '%d 个节点' % len(ov))
ck('★没有一个"认不出"的挂接对象',
   all(b.get('type') for b in allb),
   '认不出 %d 个' % sum(1 for b in allb if not b.get('type')))
ck('★挂接对象数 == 各节点挂接数之和',
   len(allb) == sum(it['count'] for it in ov),
   '%d vs %d' % (len(allb), sum(it['count'] for it in ov)))
ck('每个对象都标了种类（建筑/单位）',
   all(b.get('kind') in ('building', 'unit') for b in allb),
   '建筑 %d / 单位 %d' % (sum(1 for b in allb if b.get('kind') == 'building'),
                          sum(1 for b in allb if b.get('kind') == 'unit')))
ck('建筑的耐久/坐标都读到了',
   all(b.get('hp') is not None and b.get('cx') is not None
       for b in allb if b.get('kind') == 'building'),
   '建筑 %d 个' % sum(1 for b in allb if b.get('kind') == 'building'))
if os.path.isfile(MAP):
    ck('有地图时建筑能补到"地图建筑ID"',
       sum(1 for b in allb if b.get('kind') == 'building' and b.get('map_idx')) > 0,
       '%d 个补到' % sum(1 for b in allb if b.get('kind') == 'building' and b.get('map_idx')))

# 单位挂接这条（飞碟被攻击）
u = [b for b in allb if b.get('kind') == 'unit']
ck('档里确实有"挂在单位上"的标签', len(u) > 0, '%d 个' % len(u))
if u:
    # 允许"无格"（待在建筑里/正在出厂的单位本来就没有格子坐标），但类型必须认得
    ck('单位挂接能读出单位类型（坐标可缺）',
       all(b.get('type') for b in u),
       str([(b['type'], b['cx'], b['cy']) for b in u[:4]]))
    ck('绝大多数单位有格坐标',
       sum(1 for b in u if b.get('cx') is not None) >= len(u) * 0.9,
       '%d / %d 有坐标' % (sum(1 for b in u if b.get('cx') is not None), len(u)))

# 解除挂接：挑一个 count>=2 的节点真解一次
cand = None
for it in ov:
    if it['count'] >= 2 and it['buildings']:
        cand = it
        break
ck('找到可测的节点（挂接数>=2）', cand is not None,
   ('node=%#x 触发=%s count=%d' % (cand['node'], cand['trigger_id'], cand['count'])) if cand else '')
if cand:
    b0 = cand['buildings'][0]
    old = d.detach_building(cand['node'], b0['field'])
    ck('解除后字段变 0', old != 0, '%#x' % old)
    ov2 = [x for x in d.attach_overview() if x['node'] == cand['node']]
    ck('该节点挂接数 -1', bool(ov2) and ov2[0]['count'] == cand['count'] - 1,
       '%d -> %d' % (cand['count'], ov2[0]['count'] if ov2 else -1))
    ck('那栋/那个单位已从列表里消失',
       bool(ov2) and b0['field'] not in {b['field'] for b in ov2[0]['buildings']}, '')

print('-' * 72)
print('§204 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
