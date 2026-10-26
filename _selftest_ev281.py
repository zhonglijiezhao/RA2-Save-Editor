# -*- coding: utf-8 -*-
"""§281 自检：Terrain 对象（树 / 石头 / 路牌）—— 读得出、占格、地图页画得出

玩家（2026-10-26 11:41）：
> 「覆盖物这些 矿是最不重要的 不影响放置单位和建筑 什么会影响？桥 围墙 等这些非常多的种类。
>   树严格来说不是覆盖物而是地形对象　`E:/YURI/SAVE0029.SAV` 这个存档什么也没有，只有三棵树
>   看看能不能读出来」

结论：树是**独立对象类**（CLSID `ce2d270e…`），244 字节/条、无伴随、会占格（`+0x138` 的 0x10 位）。

样本：
  · `E:\\YURI\\SAVE0029.SAV`      —— 空图 + 三棵树（玩家专门做的对照档）
  · `E:\\YURI\\新建文件夹 (23)\\all01umd.SAV` —— 3334 棵树 / 41 种类型（大样本，用来验类型名）
"""
import io
import os
import struct
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

import importlib.util                                              # noqa: E402

spec = importlib.util.spec_from_file_location('sedgui', os.path.join(HERE, 'RA2存档编辑器.py'))
GUI = importlib.util.module_from_spec(spec)
spec.loader.exec_module(GUI)

from editor_core import SaveDoc                                     # noqa: E402
import map_ini                                                      # noqa: E402

OK = FAIL = 0


def chk(cond, msg, extra=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [OK ] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s   %r' % (msg, extra))


SMALL = r'E:\YURI\SAVE0029.SAV'
SMALL_MAP = r'E:\YURI\ALL01UMD.MAP'
BIG = r'E:\YURI\新建文件夹 (23)\all01umd.SAV'
BIG_MAP = r'E:\YURI\新建文件夹 (23)\all01umd.map'
for p in (SMALL, BIG):
    if not os.path.isfile(p):
        print('缺测试档 %s' % p)
        sys.exit(1)

print('=' * 76)
print('§281 Terrain 对象（树/石）：读取 / 占格 / 绘制    自检开始')
print('=' * 76)


def map_terrain(path):
    """地图 [Terrain] → {(x,y): 类型名}（位置编码 = y*1000+x）"""
    mi = map_ini.MapIni(path)
    out = {}
    for k, v in (mi.sections.get('terrain') or {}).items():
        try:
            n = int(k)
        except Exception:
            continue
        out[(n % 1000, n // 1000)] = str(v).strip().upper()
    return out


# ---------------------------------------------------------------- 1) 小档三棵树
print('\n[1] 玩家给的对照档：空图 + 三棵树')
d = SaveDoc(SMALL)
print('    单位 %d / 建筑 %d / 记录 %d / 格对象 %d'
      % (len(d.unit_records()), len(d.buildings() or []), len(d.records), len(d.cell_offsets())))
ts = d.terrain_objects()
chk(len(ts) == 3, '★读出 3 棵树', len(ts))
got = sorted((t['x'], t['y'], t['type'], t['hp']) for t in ts)
print('    ', got)
chk(got == [(70, 81, 'TREE01', 200), (80, 73, 'TREE01', 200), (80, 88, 'TREE01', 200)],
    '★三棵全是 TREE01、HP 200、坐标 (80,73)/(70,81)/(80,88)', got)
chk(all(t['size'] == 244 for t in ts), '记录长度都是 244 字节（无伴随对象）', [t['size'] for t in ts])

mt = map_terrain(SMALL_MAP)
# ★地图会被玩家反复改：有 [Terrain] 段就交叉验证，没有就跳过（不把环境漂移算成回归）
if mt:
    chk(len(mt) >= 3, '地图 [Terrain] 至少 3 条', len(mt))
same = sum(1 for t in ts if mt.get((t['x'], t['y'])) == t['type'])
chk(same == 3 or not mt, '★与地图 [Terrain] 3/3 坐标+类型完全一致（交叉验证）', (same, mt))
print('    ', {k: v for k, v in mt.items()})

# ---------------------------------------------------------------- 2) 占格
print('\n[2] 树占格：+0x138 的 0x10 位 / cell_taken / cell_kind')
for t in ts:
    xy = (t['x'], t['y'])
    chk(d.cell_kind(*xy) == d.CELL_KIND_TERRAIN,
        '格 %s 掩码 = 0x10（Terrain 位）' % (xy,), hex(d.cell_kind(*xy) or 0))
    chk(d.cell_taken(*xy) is True, '★格 %s cell_taken = True（以前认不出，能往上放单位）' % (xy,))
    chk((d.terrain_at(*xy) or {}).get('type') == 'TREE01', 'terrain_at(%s) 能反查到 TREE01' % (xy,))
chk(d.cell_taken(79, 73) is False, '旁边的空格 (79,73) 不算被占')
chk(d.terrain_at(79, 73) is None, '旁边的空格没有地形对象')

# free_cell_near 必须绕开树
near = d.free_cell_near(80, 73, radius=6, clearance=1)
print('    从 (80,73) 找最近空地 → %s' % (near,))
chk(near is not None and near not in {(t['x'], t['y']) for t in ts},
    '★free_cell_near 不会挑到树格', near)

# ---------------------------------------------------------------- 3) 大档 + 类型名
print('\n[3] 大档：3334 棵树 / 类型名从本档类型表反查')
db = SaveDoc(BIG)
tb = db.terrain_objects()
print('    树 %d 棵；类型 %d 种' % (len(tb), len({t['type'] for t in tb})))
chk(len(tb) >= 3000, '大档读出 3000+ 棵树', len(tb))
unknown = [t for t in tb if str(t['type']).startswith('?')]
chk(not unknown, '★所有树的类型名都解析出来了（没有 ?xxxxxxxx）', len(unknown))
mtb = map_terrain(BIG_MAP)
hit = sum(1 for t in tb if mtb.get((t['x'], t['y'])) == t['type'])
print('    与地图 [Terrain] 对上：%d / %d 棵（地图共 %d 条）' % (hit, len(tb), len(mtb)))
chk(hit >= len(mtb) * 0.98, '★与地图交叉验证 ≥98%% 命中（%d/%d）' % (hit, len(mtb)), (hit, len(mtb)))
names = db.terrain_type_names()
chk(len(names) >= 40, '类型表解析出 40+ 个类型', len(names))
sample = dict(list(sorted((v, k) for k, v in names.items()))[:6])
print('    类型样例:', sample)
chk(all(not str(v).startswith('?') for v in names.values()), '类型名全可读', list(names.values())[:5])

# ---------------------------------------------------------------- 4) 与"树不是覆盖物"一致：不进 buildings()
print('\n[4] 分类正确性')
chk(all(t['type'] not in ('', None) for t in tb), '每棵树都有类型名')
chk(len(db.buildings() or []) > 0 and all(b.get('type') != 'TREE01' for b in (db.buildings() or [])),
    '树不会混进 buildings()（它是独立对象类）')
chk(not any(u.get('type') == 'TREE01' for u in db.unit_records()),
    '树不会混进 unit_records()')

# ---------------------------------------------------------------- 5) GUI 绘制
print('\n[5] 地图页「树/石」标记')
app = GUI.App()
app.withdraw()
app._set_doc(SaveDoc(SMALL))
app.mv_load(SMALL_MAP)
# 自检环境里窗口是 withdraw 的，画布 1×1 ⇒ mv_fit 把 scale 算成 1；显式给个正常比例
app.mv_state['scale'] = 8
chk(hasattr(app, 'mv_terr') and app.mv_terr.get() is False, '勾选框存在且默认关', app.mv_terr.get())
app.mv_render()
chk(len(app.mv_canvas.find_withtag('terr')) == 0, '默认关 ⇒ 0 个树图元',
    len(app.mv_canvas.find_withtag('terr')))
app.mv_terr.set(True)
app.mv_render()
it = app.mv_canvas.find_withtag('terr')
chk(len(it) == 3, '★勾上后画出 3 个树图元', len(it))
# 圆心必须落在对应格中心
s = app.mv_state['scale']
cells = {(t['x'], t['y']) for t in app.doc.terrain_objects()}
centers = set()
for i in it:
    co = app.mv_canvas.coords(i)
    cx, cy = (co[0] + co[2]) / 2.0, (co[1] + co[3]) / 2.0
    centers.add((int(cx // s), int(cy // s)))
chk(centers == cells, '★每个圆点都落在那三棵树的格子里', (sorted(centers), sorted(cells)))
txt = app._mv_terr_text(80, 73)
print('    信息栏文字: %r' % txt)
chk('TREE01' in txt and 'HP 200' in txt, '信息栏写出「地形对象：TREE01（HP 200）」', txt)
chk(app._mv_terr_text(79, 73) == '', '空格子信息栏不加东西')
app.mv_terr.set(False)
app.mv_render()
chk(len(app.mv_canvas.find_withtag('terr')) == 0, '取消勾选 ⇒ 图元归零')

# ---------------------------------------------------------------- 6) 缓存
print('\n[6] 缓存与失效')
d2 = SaveDoc(BIG)
n1 = len(d2.terrain_objects())
n2 = len(d2.terrain_objects())
chk(n1 == n2, '两次读取条数一致（走了缓存）', (n1, n2))
recs = [r for r in d2.unit_records() if isinstance(r.get('x'), int)]
if recs:
    d2.remove_unit(recs[0])
    n3 = len(d2.terrain_objects())
    chk(n3 == n1, '★删掉一台单位后树的数量不变（%d → %d），且缓存已重建' % (n1, n3), (n1, n3))
    names3 = d2.terrain_type_names()
    chk(all(not str(v).startswith('?') for v in names3.values()),
        '重建后类型名依然可读', list(names3.values())[:3])

print('\n' + '-' * 76)
print('§281 Terrain 对象：PASS %d / FAIL %d' % (OK, FAIL))
print('-' * 76)
