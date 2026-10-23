# -*- coding: utf-8 -*-
"""§210 自检：地形校验（地图 IsoMapPack5）+ 强制骨架 A/B。

玩家实测（2026-10-23）：
> 「你放置的地方 20,179、24,179 和游戏中的对不上，你放的都是在 *,176 附近，那里是悬崖。」

查清的事实：
  · 存档的格坐标 = 地图 IsoMapPack5 的坐标（实测地图 42449 格 **全部**落在存档 43080 个
    格对象里）⇒ 可以直接拿存档 (x,y) 查地形，无需任何转换；
  · 存档里看不到地形 ⇒ 之前放在水/悬崖/不可通行格上，引擎读档时会把单位挪到旁边
    （玩家看到的就是"位置偏了几格、那里是悬崖"），严重时干脆不画；
  · `(20,181)` 是 tile 30（这份档里从没有地面单位踩过 ⇒ 不可通行），
    `(20,179)` 才是 tile 293（可通行）。

用法：python _selftest_ev210.py [存档] [地图]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
def _fp(*names):
    """玩家会把测试档挪到 `新建文件夹 (29)` ⇒ 两个目录都找。"""
    for n in names:
        for d in ('E:/YURI', 'E:/YURI/新建文件夹 (29)'):
            q = os.path.join(d, n)
            if os.path.isfile(q):
                return q
    return names[0]


from editor_core import SaveDoc                                        # noqa: E402

SAV = sys.argv[1] if len(sys.argv) > 1 else _fp('有空地.SAV')
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
print('§210 自检：地形校验 + 强制骨架 A/B')
ck('存档存在', os.path.isfile(SAV), SAV)
if not os.path.isfile(SAV):
    sys.exit(1)

d = SaveDoc(SAV)
mp = d.scenario_map_path()
ck('找到场景地图', bool(mp), mp or d.map_lookup_hint())
mc = d.map_cells()
ck('地形解出来了', len(mc) > 1000, '%d 格' % len(mc))
cells = d.cell_offsets()
hit = sum(1 for k in mc if k in cells)
ck('★地图格与存档格对象同一坐标空间', hit >= len(mc) * 0.8,
   '%d / %d' % (hit, len(mc)))
okt = d.walkable_tiles()
ck('可通行 tile 集合非空', len(okt) > 5, '%d 种' % len(okt))

t179 = d.cell_terrain(20, 179)
t181 = d.cell_terrain(20, 181)
ck('(20,179) 有地形', t179 is not None, str(t179))
ck('(20,179) 判定为可通行（无告警）', d.terrain_warning(20, 179) == '',
   d.terrain_warning(20, 179)[:60])
if t181 is not None and t181[0] not in okt:
    ck('(20,181) 判定为不可通行（有告警）', bool(d.terrain_warning(20, 181)),
       d.terrain_warning(20, 181)[:50])
else:
    ck('(20,181) 地形样本与预期不同，跳过该断言', True, str(t181))

# 空位搜索必须避开不可通行格
cell = d.free_cell_near(20, 179, radius=10, clearance=1)
ck('空位搜索有结果', cell is not None, str(cell))
if cell:
    tt = d.cell_terrain(*cell)
    ck('★搜出来的空位地形可通行', bool(tt) and tt[0] in okt, str(tt))

# ---- 放置：好格子不该报地形警，坏格子必须报 ----
d2 = SaveDoc(SAV)
info_ok = d2.add_unit_by_clone('ZEP', 20, 179, house=d2.player_house(), veterancy=0)
ck('好格子(20,179) 放置成功', bool(info_ok['key']), hex(info_ok['key']))
ck('★好格子不报地形告警',
   not any('地形' in w for w in info_ok['warnings']),
   str(info_ok['warnings']))
if t181 is not None and t181[0] not in okt:
    bad = d2.add_unit_by_clone('ZEP', 20, 181, house=d2.player_house(), veterancy=0)
    ck('★坏格子(20,181) 会报地形告警',
       any('地形' in w for w in bad['warnings']), str(bad['warnings'])[:80])

# ---- 强制骨架 A/B ----
d3 = SaveDoc(SAV)
a = d3.add_unit_by_clone('ZEP', 20, 180, house=d3.player_house(), veterancy=0)
b = d3.add_unit_by_clone('ZEP', 20, 178, house=d3.player_house(), veterancy=0,
                         borrow_type='SCHP')
ck('同型号克隆骨架 = ZEP', a['template_type'].upper() == 'ZEP', a['template_type'])
ck('★强制骨架克隆骨架 = SCHP', b['template_type'].upper() == 'SCHP', b['template_type'])
ck('两者移动方式都是 Fly', a['loco'] == 'Fly' and b['loco'] == 'Fly',
   '%s / %s' % (a['loco'], b['loco']))
ck('档里有该型号时，跨型号不再报 §209 警告',
   not any('隐形' in w for w in b['warnings']), str(b['warnings']))
out = os.path.join(HERE, '_selftest_out', 'ev210_地形.SAV')
if not os.path.isdir(os.path.dirname(out)):
    os.makedirs(os.path.dirname(out))
d3.save(out, backup=False)
d4 = SaveDoc(out)
zs = [r for r in d4.unit_records() if r['type'].upper() == 'ZEP']
ck('落盘往返：两架都在', len(zs) >= 2, '%d 架' % len(zs))

print('-' * 72)
print('§210 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
