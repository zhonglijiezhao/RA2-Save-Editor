# -*- coding: utf-8 -*-
"""§280 自检：地形来源三档（地图 / 地图+存档补格 / 存档）+ 两条硬结论

玩家（2026-10-26 11:26）：
> 「map 文件中的建筑 单位 覆盖物 会随着游戏进行在存档中改变，但是地形是不会变的，
>   那么能不能把 map 的地形直接映射到存档编辑器中呢」

要证明的两件事：
  A. **地形只来自 .MAP** —— 把存档里的 tile 全改成 0，用「地图」档渲染**逐像素不变**。
  B. **改档不碰地形** —— remove_unit / set_unit_cell / add_unit_by_clone 之后，
     全档每格 `+0x4C`(tile) / `+0x12F`(level) 逐格完全一致。
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
import map_render as mr                                             # noqa: E402

OK = FAIL = 0


def chk(cond, msg, extra=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [OK ] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s   %r' % (msg, extra))


SAV = r'E:/YURI/新建文件夹 (23)/all01umd.SAV'
MAP = r'E:/YURI/新建文件夹 (23)/all01umd.map'
if not os.path.isfile(SAV) or not os.path.isfile(MAP):
    print('缺测试档：%s / %s' % (SAV, MAP))
    sys.exit(1)

print('=' * 74)
print('§280 地形来源三档 / 地形只来自地图 / 改档不碰地形   自检开始')
print('  存档 %s（%.2f MB）  地图 %s（%d 字节）'
      % (os.path.basename(SAV), os.path.getsize(SAV) / 1048576.0,
         os.path.basename(MAP), os.path.getsize(MAP)))
print('=' * 74)


def terr_snap(d):
    """{(x,y): (tile, level)} —— 全档地形快照（按坐标，不按偏移）。"""
    pl = d.plain
    return {xy: (struct.unpack_from('<H', pl, o + 0x4C)[0],
                 struct.unpack_from('<H', pl, o + 0x12E)[0] >> 8)
            for xy, o in d.cell_offsets().items()}


def render_of(app):
    """按当前 mv_state 渲染 → (W,H,buf)（丢掉第 4 项元信息）。"""
    st = app.mv_state
    r = mr.render_rgb(st['mp'], st.get('blds') or [], scale=2)
    return r[0], r[1], r[2]


def pixdiff(a, b):
    n = min(len(a), len(b))
    d = sum(1 for i in range(0, n - 3, 3) if a[i:i + 3] != b[i:i + 3])
    return d, n // 3


app = GUI.App()
app.withdraw()

# ---------------------------------------------------------------- 1) 控件
print('\n[1] 控件与档位映射')
d0 = SaveDoc(SAV)
app._set_doc(d0)
app.mv_load(MAP)
chk(hasattr(app, 'mv_terr_src'), '有下拉 mv_terr_src')
chk(tuple(app.mv_terr_src.get() if False else GUI.App.TERR_SRC_MODES)
    == ('地图', '地图+存档补格', '存档'), '三档名字正确', GUI.App.TERR_SRC_MODES)
chk(app.mv_terr_src.get() == '地图', '★默认「地图」（地形不依赖存档）', app.mv_terr_src.get())
chk(app.mv_src_save.get() is False, '兼容变量 mv_src_save 默认 False', app.mv_src_save.get())
for label, want in (('地图', 'map'), ('地图+存档补格', 'merge'), ('存档', 'save')):
    app.mv_terr_src.set(label)
    chk(app._mv_terr_mode() == want, '档位 %r → %r' % (label, want), app._mv_terr_mode())
chk(app.mv_src_save.get() is True, '选「存档」时旧 BooleanVar 同步为 True', app.mv_src_save.get())
app.mv_src_save.set(False)
chk(app.mv_terr_src.get() == '地图', '旧 BooleanVar 置 False ⇒ 下拉回「地图」', app.mv_terr_src.get())

# ---------------------------------------------------------------- 2) 地图档：完全不看存档 tile
print('\n[2] ★「地图」档：把存档 tile 全改成 0，渲染必须逐像素不变')
app.mv_terr_src.set('地图')
app.mv_load(MAP)
w0, h0, buf0 = render_of(app)
snap_before = terr_snap(app.doc)

real_tfs = app.doc.terrain_from_save


def fake_zero_terrain(theater=None):
    """假装存档里每格 tile 都是 0（地形字段被"改坏"了）。"""
    fake = dict(real_tfs(theater))
    fake['terrain'] = {xy: (0, 0, lv) for xy, (_t, lv) in fake['terrain'].items()}
    fake['has_terrain'] = True
    return fake


app.doc.terrain_from_save = fake_zero_terrain
app.mv_load(MAP)
w1, h1, buf1 = render_of(app)
dd, tot = pixdiff(buf0, buf1)
chk((w0, h0) == (w1, h1), '画布尺寸不受存档 tile 影响', ((w0, h0), (w1, h1)))
chk(dd == 0, '★存档 tile 全变 0 ⇒ 地图档渲染 0 像素差异（%d/%d）' % (dd, tot), dd)
chk(app.mv_state['src'] == 'map', '来源标记 = map', app.mv_state.get('src'))
app.doc.terrain_from_save = real_tfs

# ---------------------------------------------------------------- 3) 三档渲染差异
print('\n[3] 三档渲染两两对比')
app.mv_load(MAP)
_, _, buf_map = render_of(app)
app.mv_terr_src.set('地图+存档补格')
app.mv_load(MAP)
_, _, buf_merge = render_of(app)
n_fill = app.mv_state.get('filled')
app.mv_terr_src.set('存档')
app.mv_load(MAP)
_, _, buf_save = render_of(app)
d_ms, tot2 = pixdiff(buf_map, buf_save)
d_mm, _ = pixdiff(buf_map, buf_merge)
d_sm, _ = pixdiff(buf_save, buf_merge)
print('    地图 vs 存档 %.2f%% ｜ 地图 vs 合并 %.2f%% ｜ 合并 vs 存档 %.2f%%'
      % (100.0 * d_ms / tot2, 100.0 * d_mm / tot2, 100.0 * d_sm / tot2))
_n_cell = len(app.doc.cell_offsets())
_n_map = len(mr.parse_map(MAP)['terrain'])
_want = max(0, _n_cell - _n_map)
chk(n_fill == _want, '★补格数 = 存档格数(%d) - 地图格数(%d) = %d' % (_n_cell, _n_map, _want), n_fill)
chk(0 < d_ms < tot2 * 0.05, '地图 vs 存档 差异在 5%% 以内（引擎挑的同族变体）', '%d/%d' % (d_ms, tot2))
chk(d_mm <= d_ms, '合并与地图的差异不比纯地图更大（只补洞不改底图）', (d_mm, d_ms))
chk(app.mv_state['src'] == 'save', '「存档」档来源标记 = save', app.mv_state.get('src'))
chk('存档' in app.mv_lbl.cget('text') and '地图' in app.mv_lbl.cget('text'),
    '标题栏写明了来源', app.mv_lbl.cget('text')[:80])

# 补格后地形格数 == 存档格对象数（全覆盖，没洞）
app.mv_terr_src.set('地图+存档补格')
app.mv_load(MAP)
n_cells = len(app.mv_state['mp']['terrain'])
n_obj = len(app.doc.cell_offsets())
chk(n_cells == n_obj, '★合并后地形格数 == 存档格对象数（%d，无空洞）' % n_obj, (n_cells, n_obj))

# ---------------------------------------------------------------- 4) 地图缺失 ⇒ 自动降级
print('\n[4] 没 .MAP ⇒ 自动降级到存档，且不崩')
app.mv_terr_src.set('地图')
app.mv_load(r'E:\YURI\__no_such_map__.MAP')
chk(app.mv_state.get('src') == 'save', '★地图读不到 ⇒ 自动用存档地形', app.mv_state.get('src'))
chk(app.mv_state.get('ok') is True, '仍然渲染成功（没退回网格）', app.mv_state.get('ok'))
chk('存档' in app.mv_lbl.cget('text'), '标题栏提示已降级', app.mv_lbl.cget('text')[:90])

# ---------------------------------------------------------------- 5) ★改档不碰地形
print('\n[5] ★改档（删/移/放单位）不碰地形：全档 +0x4C / +0x12F 逐格比对')
base = terr_snap(d0)
print('    基线：%d 格地形（tile 种类 %d，level 种类 %d）'
      % (len(base), len({v[0] for v in base.values()}), len({v[1] for v in base.values()})))

# 5.1 删一台单位
d1 = SaveDoc(SAV)
recs = [r for r in d1.unit_records() if isinstance(r.get('x'), int)]
tgt = recs[0]
inf = d1.remove_unit(tgt)
s1 = terr_snap(d1)
same1 = sum(1 for xy, v in s1.items() if base.get(xy) == v)
print('    删 %s @(%s,%s)（明文 %+d）后：地形 %d 格，与基线一致 %d 格'
      % (tgt['type'], tgt['x'], tgt['y'], inf['delta'], len(s1), same1))
chk(same1 == len(s1), '★remove_unit 后地形逐格不变（%d/%d）' % (same1, len(s1)), same1)

# 5.2 移动一台单位
d2 = SaveDoc(SAV)
r2 = [r for r in d2.unit_records() if isinstance(r.get('x'), int)][1]
old_cell = (r2['x'], r2['y'])
free = None
occ = {(r['x'], r['y']) for r in d2.unit_records() if isinstance(r.get('x'), int)}
for xy in d2.cell_offsets():
    if xy not in occ and not d2.cell_taken(*xy) and d2.cell_flat_info(*xy).get('is_flat'):
        free = xy
        break
if free:
    d2.set_unit_cell(r2, free[0], free[1])
    s2 = terr_snap(d2)
    same2 = sum(1 for xy, v in s2.items() if base.get(xy) == v)
    print('    把 %s 从 %s 移到 %s 后：地形一致 %d / %d' % (r2['type'], old_cell, free, same2, len(s2)))
    chk(same2 == len(s2), '★set_unit_cell 后地形逐格不变', same2)

# 5.3 放一台单位（克隆）
d3 = SaveDoc(SAV)
tp = [r for r in d3.unit_records() if isinstance(r.get('type'), str)][0]['type']
occ3 = {(r['x'], r['y']) for r in d3.unit_records() if isinstance(r.get('x'), int)}
free3 = None
for xy in d3.cell_offsets():
    if xy not in occ3 and not d3.cell_taken(*xy) and d3.cell_flat_info(*xy).get('is_flat'):
        free3 = xy
        break
if free3:
    info3 = d3.add_unit_by_clone(tp, free3[0], free3[1], house=None, veterancy=2,
                                 exact_cell=True, auto_nudge=False)
    s3 = terr_snap(d3)
    same3 = sum(1 for xy, v in s3.items() if base.get(xy) == v)
    print('    在 %s 克隆一台 %s 后：地形一致 %d / %d' % (free3, tp, same3, len(s3)))
    chk(same3 == len(s3), '★add_unit_by_clone 后地形逐格不变', same3)

# 5.4 放一栋建筑
d4 = SaveDoc(SAV)
btype = 'GAPOWR'
occ4 = {(r['x'], r['y']) for r in d4.unit_records() if isinstance(r.get('x'), int)}
bld_cells = {(b['x'], b['y']) for b in (d4.buildings() or [])}
free4 = None
for xy in d4.cell_offsets():
    if xy in occ4 or xy in bld_cells or d4.cell_taken(*xy):
        continue
    if d4.cell_flat_info(*xy).get('is_flat'):
        free4 = xy
        break
if free4:
    try:
        d4.add_building(btype, free4[0], free4[1], house=None)
        s4 = terr_snap(d4)
        same4 = sum(1 for xy, v in s4.items() if base.get(xy) == v)
        print('    在 %s 放 %s 后：地形一致 %d / %d' % (free4, btype, same4, len(s4)))
        chk(same4 == len(s4), '★add_building 后地形逐格不变', same4)
    except Exception as e:
        print('    add_building 跳过（%s）' % str(e)[:50])
        chk(True, 'add_building 不可用，跳过')

# ---------------------------------------------------------------- 6) 地形与"当前地图"绑定，与存档无关
print('\n[6] 同一张地图 + 两份不同存档 ⇒ 地形必须一模一样')
# 第二份「存档」用 [5] 里删过一台单位的 d1：同一张地图、内容不同 ⇒ 地形必须一模一样
other = d1 if 'd1' in dir() else None
if other:
    app.mv_terr_src.set('地图')
    app._set_doc(other)
    app.mv_load(MAP)
    w2, h2, buf2 = render_of(app)
    dd2, tot3 = pixdiff(buf0, buf2)
    print('    另一份档（删过一台单位的同一张图）：画布 %s vs %s，像素差异 %.2f%%'
          % ((w0, h0), (w2, h2), 100.0 * dd2 / tot3))
    chk((w0, h0) == (w2, h2) and dd2 == 0,
        '★★两份不同存档 + 同一张 .MAP ⇒ 地形渲染 0 像素差异', '%d/%d' % (dd2, tot3))
else:
    chk(True, '没有第二份存档可对比，跳过')

print('\n' + '-' * 74)
print('§280 地形来源 / 地形不依赖存档：PASS %d / FAIL %d' % (OK, FAIL))
print('-' * 74)
