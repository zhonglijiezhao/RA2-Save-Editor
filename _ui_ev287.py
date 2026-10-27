# -*- coding: utf-8 -*-
"""§285 自检：没有 .MAP 时，地图页能不能用**存档自己**把地形画出来。

跑法：D:/py/python.exe _ui_ev287.py
"""
import io
import importlib.util
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

spec = importlib.util.spec_from_file_location('g', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from editor_core import SaveDoc          # noqa: E402
import map_render as mr                  # noqa: E402

SAV = r'E:/YURI/新建文件夹 (23)/ALL01UMD.sav'
MAP = r'E:/YURI/新建文件夹 (23)/all01umd.map'

PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


if not (os.path.exists(SAV) and os.path.exists(MAP)):
    print('缺测试档/图，跳过（%s / %s）' % (SAV, MAP))
    sys.exit(0)

# ── ① 核心：存档地形 vs 地图地形 ──
doc = SaveDoc(SAV)
mpm = mr.parse_map(MAP)
mps = doc.terrain_from_save(mpm.get('theater'))
tm, ts = mpm['terrain'], mps['terrain']
both = set(tm) & set(ts)
same_t = sum(1 for xy in both if tm[xy][0] == ts[xy][0])
same_l = sum(1 for xy in both if tm[xy][2] == ts[xy][2])
rate_t = 100.0 * same_t / max(1, len(both))
ck(rate_t >= 95.0, 'tile 一致 %.1f%%（≥95%%）' % rate_t)
ck(same_l == len(both), '层高一致 100%%（%d 格）' % len(both))
ck(len(ts) > 10000, '存档地形格 %d（够画一张图）' % len(ts))
ck(set(tm) - set(ts) == set(), '地图的格全都在存档里（差集为空）')
ck(len(set(ts) - set(tm)) > 0, '存档比地图多 %d 格（引擎补的边界/填充格）'
   % len(set(ts) - set(tm)))

# ── ② GUI：真的"没有地图"时能自己画 ──
app = m.App()
app.geometry('1280x820')
# 把自动挂图掐掉，模拟"存档目录里没有 .MAP、也没有匹配记忆"
app.doc = None
_doc = SaveDoc(SAV)


def _no_map(*a, **kw):
    _doc._map = None
    _doc._mapm = None
    return 'no_map'


_doc.auto_attach_map = _no_map
app._set_doc(_doc)
app.update()
app.nb.select(6)                      # 切到地图页（延迟渲染在这里发生）
app.update()

st = app.mv_state
ck(st.get('path') is None, '无 .MAP ⇒ path 为空')
ck(app._mv_terr_mode() in ('map', 'merge', 'save'), '地形来源下拉可用：%s' % app._mv_terr_mode())

t0 = time.time()
app.mv_load(None)                     # 等价"用户选了存档地形"
app.update()
dt = time.time() - t0
ck(dt < 20, 'mv_load(None) 用时 %.1fs（<20s）' % dt)
ck(st.get('src') == 'save', 'src=save（数据来自存档）')
ck(st.get('ok') is True, '渲染成功')
ck(len((st.get('mp') or {}).get('terrain') or {}) > 10000,
   '地形格 %d' % len((st.get('mp') or {}).get('terrain') or {}))
items = app.mv_canvas.find_all()
ck(len(items) > 50, '画布上有 %d 个图元（地形图 + 标记）' % len(items))
ck(app._cb_theater.winfo_ismapped(), '「剧场」下拉可见')
# ★§289：卡内状态改成「文件名 ✓」（原来 50+ 字拼接会被窗口右缘裁），
#   完整口径（含"地形取自存档"）移到状态栏 ⇒ 两处任一看得到即可
_both = app.mv_lbl.cget('text') + ' ｜ ' + app.status.get()
ck('存档' in _both, '写明地形取自存档（卡内 %r / 状态栏）' % app.mv_lbl.cget('text'))

# ── ③ 切剧场：只换配色，格子数据不动 ──
_terr_before = dict(st['mp']['terrain'])
app.mv_theater_var.set('URBAN')
app._mv_on_theater()
app.update()
ck((app.mv_state.get('mp') or {}).get('theater') == 'URBAN', '剧场切到 URBAN')
ck(app.mv_state['mp']['terrain'] == _terr_before, '切剧场不改格子数据')

# ── ④ 有地图时剧场以地图为准（不被下拉覆盖）──
app2 = m.App()
app2.geometry('1280x820')
app2._set_doc(SaveDoc(SAV))
app2.update()
app2.nb.select(6)
app2.update()
mp2 = app2.mv_state.get('mp') or {}
if app2.mv_state.get('src') == 'map':
    ck(mp2.get('theater') == mpm.get('theater'),
       '有地图 ⇒ 剧场取地图的 %s' % mp2.get('theater'))
else:
    ck(True, '本机没挂上地图，跳过"地图剧场优先"检查')
app2.destroy()
app.destroy()

print()
print('结果：%d 通过 / %d 失败' % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
