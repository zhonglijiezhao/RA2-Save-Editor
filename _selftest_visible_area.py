# -*- coding: utf-8 -*-
"""自检：「可见区域（蓝线）」判定 —— 换成**正交 cell 坐标**（2026-11-07 修正）

玩家报的 bug：路径点 256 明明在 FA2 蓝框内，地图页却提示「⚠ 在蓝线外（游戏里不显示这块地图）」。

根因：地编「地图参数 ▸ 可见区域」写的是**正交 cell 坐标**（本图 `2,4,190,190`），
而 `cell_visible()` 的入参是**存档等距坐标**（wp256 = (197,391)）。旧实现按
"存档坐标 = tile×2" 硬乘 ⇒ 391 > 388 判界外。

修正：统一走 `iso_to_cell()`（与俯视展开同一套公式）：
    i = (x - y - u0) // 2      j = (x + y - v0) // 2
本档 wp256 ⇒ 正交 (2,193)，可见区 2..191 × 4..193 ⇒ **界内** ✓

（旧版脚本用的是 2026-10-25 那张 180×120 的地图样本，那个 `all01umd.map` 后来被
  换成 200×200 的大任务了，旧断言已无从验证 —— 这里按现在的样本重写。）
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc                                     # noqa: E402

SAV = next((p for p in (r'E:\YURI\SAVE0029.SAV', r'E:\YURI\ALL01UMD.sav')
            if os.path.exists(p)), None)
MAP = r'E:\YURI\ALL01UMD.MAP'
OK = FAIL = 0


def chk(c, msg):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


if not SAV or not os.path.exists(MAP):
    print('跳过：样本缺失')
    sys.exit(0)

d = SaveDoc(SAV)
ls = d.map_local_size(MAP)
print('样本 %s ｜ 地图 %s' % (os.path.basename(SAV), os.path.basename(MAP)))
print('可见区域 [Map] LocalSize = %s（正交 cell：左,上,宽,高）' % (ls,))
print('等距基准 u0/v0 = %s' % (d.ortho_origin(MAP),))

print('\n== A. 坐标换算 ==')
wp = dict(d.waypoints_save() or {})
for k in (256, 0, 1):
    if k not in wp:
        continue
    x, y = wp[k]
    ij = d.iso_to_cell(x, y, map_path=MAP)
    print('  wp%-4d iso(%3d,%3d) → 正交 %s' % (k, x, y, ij))
chk(d.iso_to_cell(197, 391, map_path=MAP) == (2, 193), 'wp256(197,391) → 正交 (2,193)')
chk(d.iso_to_cell(200, 200, map_path=MAP) == (99, 99), '中心 (200,200) → 正交 (99,99)')

print('\n== B. 玩家报的那一格必须判「界内」==')
chk(d.cell_visible(197, 391, map_path=MAP) is True,
    '路径点 256 (197,391) ⇒ 界内（修好前是 False，UI 误报"在蓝线外"）')
chk(d.cell_visible(200, 200, map_path=MAP) is True, '地图中心 ⇒ 界内')

print('\n== C. 界面别的点该判界外 ==')
chk(d.cell_visible(50, 50, map_path=MAP) is False, '(50,50) 在地图菱形外 ⇒ 界外')
chk(d.cell_visible(16, 2, map_path=MAP) is False, '(16,2) 左上角外 ⇒ 界外')

print('\n== D. 四条边界的判定（用正交格反算 iso 坐标）==')


def iso_of(i, j):
    """正交格 → iso（由 u=x-y=-199+2i、v=x+y=201+2j 反解）：x = i+j+1, y = j-i+200"""
    return (i + j + 1, j - i + 200)


l, t, w, h = ls
for (i, j, want, why) in ((l - 1, t + 50, False, '左边界外 1 格'),
                          (l, t + 50, True, '左边界第 1 格'),
                          (l + w - 1, t + 50, True, '右边界内最后 1 格'),
                          (l + w, t + 50, False, '右边界外 1 格'),
                          (l + 50, t - 1, False, '上边界外 1 格'),
                          (l + 50, t, True, '上边界第 1 格'),
                          (l + 50, t + h - 1, True, '下边界内最后 1 格'),
                          (l + 50, t + h, False, '下边界外 1 格')):
    x, y = iso_of(i, j)
    got = d.cell_visible(x, y, map_path=MAP)
    chk(got is want, '正交(%d,%d) iso(%d,%d) ⇒ %s（%s）'
        % (i, j, x, y, got, why))

print('\n== E. 读不到地图 ⇒ None（不拦人）==')
chk(d.cell_visible(10, 10, map_path='Z:/__no_such_map__.map') is None,
    '地图路径不存在 ⇒ None')

print('\n== F. 与俯视展开用的是同一套公式 ==')
import map_render as mr                                            # noqa: E402
mi, _bi, sz, info = mr.ortho_map(mr.parse_map(MAP), [])
chk((info['s0'], info['d0']) == d.ortho_origin(MAP),
    '两边基准一致 s0/d0=%s' % ((info['s0'], info['d0']),))
x, y = wp.get(256, (197, 391))
mine = ((x - y - info['s0']) // 2, (x + y - info['d0']) // 2)
chk(d.iso_to_cell(x, y, map_path=MAP) == mine, 'wp256 在两边算出的正交格相同 %s' % (mine,))
chk(tuple(sz) == (200, 200), '展开尺寸 = 地图 [Map] Size %s' % (sz,))

print('\n合计 PASS=%d FAIL=%d' % (OK, FAIL))
sys.exit(0 if FAIL == 0 else 1)
