# -*- coding: utf-8 -*-
"""§271 自检：**可见区域（蓝线）**判定 —— 放/移动/粘贴单位时不许落在游戏看不见的地方。

玩家 2026-10-25 16:07 给了地编「地图参数」截图：
  地图尺寸 180×120，**可见区域 = 5,4,174,107**（格式 左,上,宽,高），并说「可见区域内 就是蓝线内」。

标定（玩家实测三个点）：
  · (16,179) 界内（那里有盟军基地车）
  · (1,180)、(2,179) 界外（游戏里这块地图不显示）
  · (19,177) 界内
⇒ **存档格坐标 = 地编 tile 坐标 × 2**：可见范围换算成编辑器坐标 = x∈[10,358)、y∈[8,222)。
   （按 ×1 换算，(16,179) 的 y=179 就超出 111 会被判界外，与实测矛盾。）

覆盖：
1. `map_ini.local_size()` 能从 `[Map] LocalSize=` 读出 (5,4,174,107)；
2. `cell_visible()` 对玩家那三个点判定正确；
3. 四条边界逐一对（x=10/9、x=357/358、y=8/7、y=221/222）；
4. 地图没挂载时返回 None（= 不拦人，别猜）；
5. GUI `_mv_check_visible()`：界外会弹确认框（桩函数计数），界内不问。
"""
import io
import os
import sys
import importlib.util

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
spec = importlib.util.spec_from_file_location('sedgui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from editor_core import SaveDoc                                        # noqa: E402
from _paths import find_sav                                            # noqa: E402

SAV = find_sav('SAVE4823.SAV')
MAP = r'E:\YURI\ALL01UMD.MAP'
OK = FAIL = 0


def chk(cond, msg, extra=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s   %s' % (msg, extra))


print('存档 %s' % SAV)
d = SaveDoc(SAV)

# ---------- 1) 读 LocalSize ----------
print('\n[1] 地图 [Map] LocalSize')
ls = d.map_local_size()
print('    local_size = %s' % (ls,))
chk(ls == (5, 4, 174, 107), '★读出 (5,4,174,107)（与玩家截图一致）', ls)
import map_ini                                                      # noqa: E402
mp = map_ini.MapIni(MAP)
chk(d.scenario_map_path() == MAP, '存档对应的地图 = %s' % MAP, d.scenario_map_path())
chk(mp.map_size() == (180, 120), '地图尺寸 180×120 也对', mp.map_size())

# ---------- 2) 玩家给的点 ----------
print('\n[2] 玩家给的三个点')
for cell, want in (((16, 179), True), ((1, 180), False), ((2, 179), False),
                   ((19, 177), True), ((18, 177), True), ((19, 179), True)):
    got = d.cell_visible(cell[0], cell[1])
    chk(got is want, '%s → %s' % (cell, '界内' if want else '界外'),
        '期望 %s 实际 %s' % (want, got))

# ---------- 3) 边界 ----------
print('\n[3] 四条边界（×2 换算：x 10..357、y 8..221）')
for cell, want in (((10, 100), True), ((9, 100), False),
                   ((357, 100), True), ((358, 100), False),
                   ((100, 8), True), ((100, 7), False),
                   ((100, 221), True), ((100, 222), False)):
    got = d.cell_visible(cell[0], cell[1])
    chk(got is want, '%s → %s' % (cell, '界内' if want else '界外'), got)

# ---------- 4) 地图没挂 ⇒ None ----------
print('\n[4] 没地图时不拦')
d2 = SaveDoc(SAV)
d2.scenario_map_path = lambda: None          # 假装找不到地图
d2._map = None
d2._localsize_memo = None
chk(d2.cell_visible(1, 180) is None, '地图没有时返回 None（不猜、不拦）',
    d2.cell_visible(1, 180))

# ---------- 4b) 以地图文件为准（玩家 16:17 要求） ----------
print('\n[4b] 以地图文件为准')
chk(mp.local_size() == (5, 4, 174, 107), '地图文件里 [Map] LocalSize 直读 = (5,4,174,107)',
    mp.local_size())
chk(d.cell_visible(1, 180, map_path=MAP) is False, '显式指定 MAP ⇒ 判界外（1,180）')
chk(d.cell_visible(16, 179, map_path=MAP) is True, '显式指定 MAP ⇒ 判界内（16,179）')
chk(d.cell_visible(1, 180, map_path='Z:/__no_such_map__.map') is None,
    '指定一个不存在的 map_path ⇒ None（找不到地图就不拦）',
    d.cell_visible(1, 180, map_path='Z:/__no_such_map__.map'))

# ---------- 5) GUI 确认框 ----------
print('\n[5] GUI _mv_check_visible')
app = m.App()
app.withdraw()
app._set_doc(SaveDoc(SAV))
asks = []


def _ask(*a, **k):
    asks.append(a)
    return False


m.messagebox.askyesno = _ask
r1 = app._mv_check_visible((16, 179), '放置')
chk(r1 is True and not asks, '界内 ⇒ 直接放行，不弹框', (r1, asks))
r2 = app._mv_check_visible((1, 180), '放置')
chk(r2 is False and len(asks) == 1, '界外 ⇒ 弹一次确认框', (r2, len(asks)))
if asks:
    txt = asks[0][1] if len(asks[0]) > 1 else ''
    chk('蓝线' in txt and '5,4,174,107' in txt and '10..357' in txt,
        '确认框里写明可见区域与换算范围', txt[:100])

print('-' * 72)
print('§271 可见区域（蓝线）校验：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
