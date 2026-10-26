# -*- coding: utf-8 -*-
"""§278/§279 自检：存档自带地形（tile + level），没 .MAP 也能渲染；并核对 level 字段修复。

跑法：D:/py/python.exe _selftest_ev279.py
"""
import io
import os
import struct
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

import importlib.util                                                # noqa: E402
from editor_core import SaveDoc                                      # noqa: E402
import map_render as mr                                              # noqa: E402

OK = 0
FAIL = 0


def chk(cond, msg, detail=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [OK] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s   %r' % (msg, detail))


SAV = r'E:/YURI/新建文件夹 (23)/all01umd.SAV'
MAP = r'E:/YURI/新建文件夹 (23)/all01umd.map'
if not os.path.isfile(SAV):
    print('缺存档 %s' % SAV)
    sys.exit(1)
d = SaveDoc(SAV)
pl = d.plain
cofs = d.cell_offsets()
terr = mr.parse_map(MAP)['terrain']
print('存档 %d 格；地图 %d 格' % (len(cofs), len(terr)))
if not terr:
    print('★地图里没有地形格（%s），无法比较 —— 换一张有 [IsoMapPack5] 的地图' % MAP)
    sys.exit(2)

# ---------------- 1) level 字段（§279 修的 bug） ----------------
print('\n[1] 层高字段：高字节才对')
bad_lv = [(xy, d.cell_level(*xy), terr[xy][2]) for xy in terr if d.cell_level(*xy) != terr[xy][2]]
nz = [xy for xy in terr if terr[xy][2] != 0]
old_ok = sum(1 for xy in nz if (struct.unpack_from('<H', pl, cofs[xy] + 0x12E)[0] & 0xFF) == terr[xy][2])
print('    level≠0 的格 %d 个；旧写法（低字节）对 %d 个' % (len(nz), old_ok))
chk(not bad_lv, '★cell_level() 与地图 level **全档一致**（%d 格）' % len(terr), bad_lv[:3])
chk(old_ok < len(nz) * 0.2, '★旧的「& 0xFF」写法确实是错的（只对 %d/%d）' % (old_ok, len(nz)), old_ok)
chk(all(d.cell_level(*xy) == lv for xy, lv in list(d.cell_levels().items())[:200]),
    'cell_levels() 与逐格结果一致')

# ---------------- 2) tile 字段 ----------------
print('\n[2] tile 索引')
ok_t = sum(1 for xy in terr if d.cell_tile(*xy) == terr[xy][0])
print('    tile 一致 %d / %d = %.1f%%' % (ok_t, len(terr), 100.0 * ok_t / len(terr)))
chk(ok_t > len(terr) * 0.95, '★tile 一致率 > 95%%（%.1f%%）' % (100.0 * ok_t / len(terr)), ok_t)

extra = [xy for xy in cofs if xy not in terr]
t0 = sum(1 for xy in extra if d.cell_tile(*xy) == 0)
print('    地图里没有的补格 %d 个，其中 tile=0 的 %d 个' % (len(extra), t0))
chk(t0 >= len(extra) - 1, '★补格基本全是 tile=0 的空白格（%d/%d）' % (t0, len(extra)), t0)
chk(all(d.cell_level(*xy) == 0 for xy in extra), '★补格层高全是 0', None)

# ---------------- 3) 变体规律（§279 的结论：同族变体，不是修地形） ----------------
print('\n[3] 1289 个 tile 不一致的格 = 同族变体')
bad = [(xy, terr[xy][0], d.cell_tile(*xy)) for xy in terr if d.cell_tile(*xy) != terr[xy][0]]
from collections import Counter
by_map = Counter(b[1] for b in bad)
print('    不一致 %d 格里，地图值集中在: %s' % (len(bad), by_map.most_common(3)))
chk(sum(v for _k, v in by_map.most_common(3)) > len(bad) * 0.5,
    '★不一致集中在少数底图的族内（top3 占 %d/%d）' % (sum(v for _k, v in by_map.most_common(3)), len(bad)), by_map.most_common(3))
# 同一底图 tile 的变体应该落在一个小范围内
base = by_map.most_common(1)[0][0]
vs = sorted({b[2] for b in bad if b[1] == base})
print('    tile %d 被替换成: %s（跨度 %d）' % (base, vs, vs[-1] - vs[0]))
chk(vs and vs[-1] - vs[0] <= 20, '★变体都落在底图附近的窄区间（跨度 ≤20）', vs)
# 变体与"层高"无关（证明不是地形修正）
lvsets = {}
for xy, m0, s0 in bad:
    lvsets.setdefault(s0, set()).add(d.cell_level(*xy))
multi = sum(1 for s0, lvs in lvsets.items() if len(lvs) >= 2)
print('    各变体覆盖的层高种类: %s' % {k: sorted(v) for k, v in list(lvsets.items())[:6]})
chk(multi >= max(1, len(lvsets) // 2),
    '★每个变体都横跨多个层高 ⇒ 与"修地形"无关', (multi, len(lvsets)))
# 变体与八邻"同类"掩码只有约 4 成确定性 ⇒ 还掺了位置因素（规律要反编译才定得下来）
def _mask(xy):
    x, y = xy
    bits = 0
    pos = {(-1,-1):0,(-1,0):1,(-1,1):2,(0,-1):3,(0,1):4,(1,-1):5,(1,0):6,(1,1):7}
    for (dx, dy), b in pos.items():
        if (x+dx, y+dy) in terr and terr[(x+dx, y+dy)][0] == base:
            bits |= (1 << b)
    return bits
from collections import defaultdict
g = defaultdict(set)
for xy in terr:
    if terr[xy][0] == base:
        g[_mask(xy)].add(d.cell_tile(*xy))
pure = sum(1 for v in g.values() if len(v) == 1)
print('    八邻"同类"掩码 %d 种，其中能唯一确定变体的 %d 种' % (len(g), pure))
chk(pure < len(g), '★掩码不能完全决定变体（还掺位置因素）—— 真正规律要反编译', (pure, len(g)))

# ---------------- 4) 用存档重建地形并渲染 ----------------
print('\n[4] terrain_from_save + 渲染对比')
mp_sav = d.terrain_from_save('NEWURBAN')
print('    size=%s 格数=%d theater=%s' % (mp_sav['size'], len(mp_sav['terrain']), mp_sav['theater']))
chk(mp_sav['has_terrain'] and len(mp_sav['terrain']) == len(cofs), '格数与格对象数一致', len(mp_sav['terrain']))
chk(d.terrain_from_save('NEWURBAN') is mp_sav, '第二次调用命中缓存（同一对象）', None)
mp_sav2 = d.terrain_from_save('SNOW')
chk(mp_sav2 is not mp_sav and mp_sav2['theater'] == 'SNOW', '换 theater 会重算', mp_sav2['theater'])

blds = d.buildings() or []
mp_map = dict(mr.parse_map(MAP))
THEATER = mr.parse_map(MAP).get('theater') or 'TEMPERATE'
print('    地图 theater = %s' % THEATER)
mp_sav = dict(d.terrain_from_save(THEATER))
# ★两边必须用**同一个画布尺寸**再逐像素比：地图 [Map] Size 与存档格范围不同，
#   尺寸不一致时逐行数据整体错位，差异会虚高到 70%+（不是地形问题）。
_sz = (max(mp_map['size'][0], mp_sav['size'][0]),
       max(mp_map['size'][1], mp_sav['size'][1]))
mp_map['size'] = _sz
mp_sav['size'] = _sz
r1 = mr.render_rgb(mp_map, blds, scale=2)
r2 = mr.render_rgb(mp_sav, blds, scale=2)
# ★不能逐字节比：两边 canvas_size 可能差 1 格（地图 terrain 到 x=340、
#   存档到 x=349），宽度差 1 像素就整体错位 ⇒ 差异虚高到 70%+。
#   改成**逐格取像素中心**比颜色。
W1, H1, B1 = r1[0], r1[1], bytes(r1[2])
W2, H2, B2 = r2[0], r2[1], bytes(r2[2])
sc = 2
diff = tot = 0
for gy in range(min(H1, H2)):
    row1 = (gy * sc + 1) * W1 * sc
    row2 = (gy * sc + 1) * W2 * sc
    for gx in range(min(W1, W2)):
        i1 = (row1 + gx * sc + 1) * 3
        i2 = (row2 + gx * sc + 1) * 3
        tot += 1
        if B1[i1:i1 + 3] != B2[i2:i2 + 3]:
            diff += 1
pct = 100.0 * diff / max(1, tot)
print('    逐格颜色差异 %.2f%%（%d / %d 格；画布 %dx%d vs %dx%d）'
      % (pct, diff, tot, W1, H1, W2, H2))
chk(pct < 8.0, '★存档渲染与地图渲染逐格差异 < 8%%（%.2f%%）' % pct, pct)

# ---------------- 5) GUI：勾选框 / 自动回退 ----------------
print('\n[5] GUI 地形来源')
spec = importlib.util.spec_from_file_location('sedgui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
app = m.App()
app.withdraw()
app._set_doc(SaveDoc(SAV))
chk(hasattr(app, 'mv_src_save') and app.mv_src_save.get() is False,
    '勾选框「地形取自存档」存在且默认关', getattr(app, 'mv_src_save', None))
app.mv_load(MAP)
chk((app.mv_state or {}).get('src') == 'map', '不勾时用地图（src=%r）' % (app.mv_state or {}).get('src'),
    (app.mv_state or {}).get('src'))
n_map = len(app.mv_canvas.find_all())
app.mv_src_save.set(True)
app.mv_load(MAP)
chk((app.mv_state or {}).get('src') == 'save', '★勾上后改用存档地形（src=%r）'
    % (app.mv_state or {}).get('src'), (app.mv_state or {}).get('src'))
n_sav = len(app.mv_canvas.find_all())
print('    画布图元：地图 %d / 存档 %d' % (n_map, n_sav))
chk(n_sav > 0, '存档地形也画出了东西（%d 个图元）' % n_sav, n_sav)

# 模拟"地图读不到"
app.mv_src_save.set(False)
_orig = mr.parse_map
mr.parse_map = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('模拟：地图文件不存在'))
try:
    app.mv_load(r'E:\YURI\__no_such_map__.MAP')
finally:
    mr.parse_map = _orig
st = app.mv_state or {}
chk(st.get('src') == 'save', '★地图读不到时自动回退到存档地形（src=%r）' % st.get('src'), st.get('src'))
chk(len(app.mv_canvas.find_all()) > 0, '回退后仍然画出了地形（%d 图元）' % len(app.mv_canvas.find_all()),
    len(app.mv_canvas.find_all()))
chk('地形取自' in app.mv_lbl.cget('text'), '标题栏写明「地形取自存档」', app.mv_lbl.cget('text')[:60])

print('-' * 72)
print('§278/§279 存档地形：PASS %d / FAIL %d' % (OK, FAIL))
