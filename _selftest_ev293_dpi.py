# -*- coding: utf-8 -*-
"""§293 自检：DPI 缩放（窗口尺寸随屏幕 DPI 走 + 写死像素跟着缩放）。

为什么单独立一套（2026-10-28 代码审查 P4-20）：
  以前窗口固定 `1280x820`、列表卡固定 340px、导航条固定 172px。开了 DPI 感知后，
  高 DPI 屏上字体按原生分辨率渲染（变清晰），但**可用像素宽度不变而文字变大**
  ⇒ 150% 缩放下必然横向溢出。所以窗口/最小尺寸/写死像素都要跟着 DPI 缩放。

本机是 96 DPI（100%），跑不到真实的 150% —— 所以这里**直接喂 DPI 值**给
`_apply_geometry()`，把换算逻辑钉住（几何封顶、px() 换算、SCALE 传递）。

跑法：python _selftest_ev293_dpi.py
"""
import io
import os
import sys
import importlib.util

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

spec = importlib.util.spec_from_file_location('g', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
import ui_theme  # noqa: E402

PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


app = m.App()
for _ in range(4):
    app.update()

SW, SH = app.winfo_screenwidth(), app.winfo_screenheight()
print('  屏幕：%dx%d，当前 DPI=%s，_ui_scale=%s'
      % (SW, SH, app.winfo_fpixels('1i'), app._ui_scale))

# ── ① 96 DPI：与改动前完全一致（基线不动）────────────────
ck(abs(app._ui_scale - 1.0) < 1e-6, '96 DPI 下 _ui_scale = 1.0')
ck(abs(ui_theme.SCALE - 1.0) < 1e-6, '96 DPI 下 ui_theme.SCALE = 1.0')
ck(ui_theme.px(340) == 340 and ui_theme.px(172) == 172,
   '96 DPI 下 px() 恒等（340→%d, 172→%d）' % (ui_theme.px(340), ui_theme.px(172)))
ck(app.geometry().startswith('1280x820') or app.winfo_width() <= 1280,
   '默认窗口 ≈1280x820（实得 %s）' % app.geometry())


# ── ② 喂 144 DPI（150%）→ 尺寸与缩放都要跟 ────────────────
_orig_fp = app.winfo_fpixels
try:
    app.winfo_fpixels = lambda *a, **k: 144.0
    app._apply_geometry()
    for _ in range(3):
        app.update()

    ck(abs(app._ui_scale - 1.5) < 1e-6, '144 DPI 下 _ui_scale = 1.5（实得 %s）' % app._ui_scale)
    ck(abs(ui_theme.SCALE - 1.5) < 1e-6, '144 DPI 下 ui_theme.SCALE = 1.5')
    ck(ui_theme.px(340) == 510, '144 DPI 下列表卡 340→%d（期望 510）' % ui_theme.px(340))
    ck(ui_theme.px(172) == 258, '144 DPI 下导航条 172→%d（期望 258）' % ui_theme.px(172))

    geo = app.geometry()
    try:
        gw, gh = [int(x) for x in geo.split('+')[0].split('x')]
    except Exception:
        gw, gh = -1, -1
    exp_w = min(int(1280 * 1.5), max(960, SW - 40))
    exp_h = min(int(820 * 1.5), max(640, SH - 80))
    ck((gw, gh) == (exp_w, exp_h),
       '144 DPI 下窗口 %dx%d（期望 %dx%d，且不超出屏幕）' % (gw, gh, exp_w, exp_h))
    ck(gw <= SW and gh <= SH, '窗口没有超出屏幕（%d≤%d, %d≤%d）' % (gw, SW, gh, SH))

    mw, mh = app.minsize()
    ck(mw >= 960 and mh >= 640 and mw <= SW and mh <= SH,
       '最小尺寸也跟着缩放且不超屏（%dx%d）' % (mw, mh))
finally:
    app.winfo_fpixels = _orig_fp

# ── ③ 极端值不应崩（DPI 读不出来 / 超大 DPI）──────────────
try:
    app.winfo_fpixels = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('no dpi'))
    app._apply_geometry()
    ck(abs(app._ui_scale - 1.0) < 1e-6, 'DPI 读不出来时安全回落到 1.0')
except Exception as e:
    ck(False, 'DPI 读不出来时抛异常了：%s' % e)

try:
    app.winfo_fpixels = lambda *a, **k: 480.0          # 500% —— 应被夹到 2.5
    app._apply_geometry()
    ck(abs(app._ui_scale - 2.5) < 1e-6,
       '500%% DPI 被夹到 2.5（实得 %s）' % app._ui_scale)
    gw = int(app.geometry().split('+')[0].split('x')[0])
    ck(gw <= SW, '500%% DPI 下窗口仍被屏幕封顶（%d ≤ %d）' % (gw, SW))
finally:
    app.winfo_fpixels = _orig_fp
    ui_theme.set_scale(1.0)

app.destroy()

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败项：')
    for f in FAIL:
        print('  - ' + f)
sys.exit(1 if FAIL else 0)
