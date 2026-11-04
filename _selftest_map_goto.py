# -*- coding: utf-8 -*-
"""§SOV11 自检：地图·选位页的「定位」—— 按坐标 / 路径点跳转，并把视图居中。

覆盖：
  1. 控件都在（X / Y 输入框、路径点下拉、跳到格 / 跳到点 / 居中选中）
  2. 有地图：`mv_goto()` 选中正确格子，且该格落在**视口正中**
  3. `mv_goto_xy()` 正常 / 非数字 / 越界 三种输入
  4. `mv_goto_wp()` 找得到路径点（坐标对得上）/ 找不到时给候选列表
  5. `mv_click()` 把点到的格回填到 X / Y 输入框
  6. 缩放以视口中心为锚点（不会一缩放就漂回左上）
  7. 无地图网格模式同样能跳转（走 `_mv_grid_safe`）
跑：D:/py/python.exe _selftest_map_goto.py
"""
import importlib.util
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
spec = importlib.util.spec_from_file_location('sedgui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from editor_core import SaveDoc                                      # noqa: E402

SAV = r'E:\YURI\SAVE6784.SAV'
MAP = r'E:\YURI\sov01umd.map'

OK = FAIL = 0


def chk(cond, msg, extra=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s%s' % (msg, '' if extra is None else ('  → %r' % (extra,))))


def silence(app_mod):
    for fn in ('showinfo', 'showwarning', 'showerror'):
        setattr(app_mod.messagebox, fn, lambda *a, **k: None)


def view_center_cell(app):
    """视口正中那一格（浮点；格内位置 0.5 = 正中）。"""
    c = app.mv_canvas
    s = app._mv_scale_now()
    return (c.canvasx(max(1, c.winfo_width()) / 2.0) / float(s),
            c.canvasy(max(1, c.winfo_height()) / 2.0) / float(s))


def near(a, b, tol=1.5):
    return abs(a - b) <= tol


class _Ev(object):
    pass


silence(m)
print('打开 %s + %s' % (os.path.basename(SAV), os.path.basename(MAP)))
app = m.App()
app.geometry('1200x820+20+10')
app.update()
app._set_doc(SaveDoc(SAV))
app.update()
app.nb.select(6)
app.update()

print('== A. 控件齐不齐 ==')
chk(hasattr(app, 'mv_gx') and hasattr(app, 'mv_gy'), '有 X / Y 输入框')
chk(hasattr(app, '_cb_mv_gwp'), '有路径点下拉')
chk(hasattr(app, 'mv_goto_xy') and hasattr(app, 'mv_goto_wp')
    and hasattr(app, 'mv_center_sel'), '有 跳到格 / 跳到点 / 居中选中')

print('== B. 有地图：按坐标跳转 + 居中 ==')
app.mv_load(MAP)
app.update()
st = app.mv_state
chk(bool(st.get('ok')), '地图已加载', st.get('map_err'))
W, H = app._mv_cell_space()
print('     画布格子数 = %d×%d（地图声明 %s）' % (W, H, st['mp']['size'] if st.get('mp') else '—'))
chk(st.get('canv') == (W, H), 'canv 与 _mv_cell_space 一致', st.get('canv'))
st['scale'] = 12
app.mv_render()
app.update()
chk(app.mv_goto(40, 40) is True, 'mv_goto(40,40) 成功')
chk(st['sel'] == (40, 40), '选中格 = (40,40)', st['sel'])
cx, cy = view_center_cell(app)
chk(near(cx, 40.5) and near(cy, 40.5), '★视口正中 = (%.1f,%.1f) ≈ (40.5,40.5)' % (cx, cy), (cx, cy))
chk(app.mv_gx.get() == '40' and app.mv_gy.get() == '40', 'X / Y 回填成 40 / 40',
    (app.mv_gx.get(), app.mv_gy.get()))
chk('(40,40)' in app.mv_info.cget('text'), '信息栏跟着更新', app.mv_info.cget('text')[:40])

print('== C. 输入框：正常 / 非数字 / 越界 ==')
app.mv_gx.set('70')
app.mv_gy.set('30')
app.mv_goto_xy()
app.update()
chk(st['sel'] == (70, 30), '输入 70/30 ⇒ 选中 (70,30)', st['sel'])
cx, cy = view_center_cell(app)
chk(near(cx, 70.5) and near(cy, 30.5), '★视口正中 ≈ (70.5,30.5)', (cx, cy))

app.mv_gx.set('abc')
app.mv_goto_xy()
_why = app.status.get()
print('     状态栏：%s' % _why)
chk('整数' in _why, '非数字被拒', _why)
chk(st['sel'] == (70, 30), '非数字不改选中格', st['sel'])

app.mv_gx.set(str(W + 50))
app.mv_goto_xy()
chk('超出范围' in app.status.get(), '越界被拒（%s）' % app.status.get())
chk(st['sel'] == (70, 30), '越界不改选中格', st['sel'])

app.mv_gx.set('-1')
app.mv_goto_xy()
chk(st['sel'] == (70, 30), '负数被拒', st['sel'])

print('== D. 路径点跳转 ==')
mp_w, only_w = app._mv_wp_points()
pool = dict(only_w)
pool.update(mp_w)                      # 地图定义的优先
chk(bool(pool), '本图/本档有路径点（%d 个）' % len(pool), len(pool))
chk(len(app._cb_mv_gwp.cget('values') or []) == len(set(mp_w) | set(only_w)),
    '下拉项数 = 路径点号数（%d）' % len(set(mp_w) | set(only_w)),
    app._cb_mv_gwp.cget('values'))
if pool:
    num, (px, py) = sorted(pool.items())[0]
    app.mv_gwp.set(str(num))
    app.mv_goto_wp()
    app.update()
    chk(st['sel'] == (px, py),
        '★%d 号路径点 ⇒ 选中 (%d,%d)' % (num, px, py), (st['sel'], (px, py)))
    # 贴边的点滚不到正中（scrollregion 不能为负）⇒ 按钳制后的期望值比
    c0 = app.mv_canvas
    s0 = app._mv_scale_now()
    tw, th = W * s0, H * s0
    vw, vh = max(1, c0.winfo_width()), max(1, c0.winfo_height())
    ex_x = (0 if tw <= vw else max(0, min(tw - vw, (px + 0.5) * s0 - vw / 2.0)) + vw / 2.0) / s0
    ex_y = (0 if th <= vh else max(0, min(th - vh, (py + 0.5) * s0 - vh / 2.0)) + vh / 2.0) / s0
    cx, cy = view_center_cell(app)
    chk(near(cx, ex_x, 1.2) and near(cy, ex_y, 1.2),
        '视图居中/贴边钳制正确（%.1f,%.1f 期望 %.1f,%.1f）' % (cx, cy, ex_x, ex_y),
        (cx, cy, ex_x, ex_y))
app.mv_gwp.set('99999')
app.mv_goto_wp()
chk('没有 99999 号路径点' in app.status.get(), '不存在的号被拒（%s）' % app.status.get()[:40])
chk(st['sel'] == (px, py), '不存在的号不改选中格', st['sel'])

print('== E. 点地图 ⇒ 坐标回填 ==')
app.mv_goto(40, 40)
app.update()
c = app.mv_canvas
s = app._mv_scale_now()
ev = _Ev()
ev.x = int((43 + 0.5) * s - c.canvasx(0))
ev.y = int((40 + 0.5) * s - c.canvasy(0))
app.mv_click(ev)
app.update()
chk(app.mv_gx.get() == '43' and app.mv_gy.get() == '40',
    '★点到 (43,40) ⇒ 输入框回填 43 / 40', (app.mv_gx.get(), app.mv_gy.get(), st['sel']))

print('== F. 缩放锚点 ==')
app.mv_goto(40, 40)
app.update()
before = view_center_cell(app)
app.mv_zoom(1)
app.update()
after = view_center_cell(app)
chk(near(after[0], before[0], 2.0) and near(after[1], before[1], 2.0),
    '★放大后视口中心仍在 (%.1f,%.1f)（原 %.1f,%.1f）' % (after[0], after[1], before[0], before[1]),
    (before, after))
app.destroy()

print('== G. 无地图网格模式 ==')
app2 = m.App()
app2.geometry('1200x820+20+10')
app2.update()
app2._set_doc(SaveDoc(SAV))
app2.update()
app2.nb.select(6)
app2.update()
app2.mv_load(None)          # 没有 .MAP
app2.update()
st2 = app2.mv_state
if st2.get('ok'):
    print('     （本档能从存档格对象重建地形 ⇒ 强制退回网格模式来测这条老分支）')
    st2['ok'] = False
    st2['mp'] = None
app2._mv_grid_safe()
app2.update()
chk(not st2.get('ok'), '网格模式（无 .MAP / 地形重建失败那条路）', st2.get('path'))
st2['scale'] = 10
app2._mv_grid_safe()
app2.update()
W2, H2 = app2._mv_cell_space()
chk(W2 >= 100 and H2 >= 100, '网格模式坐标范围 %d×%d（菱形空间）' % (W2, H2))
chk(app2.mv_goto(60, 50) is True, '网格模式 mv_goto(60,50) 成功')
chk(st2['sel'] == (60, 50), '网格模式选中 (60,50)', st2['sel'])
cx, cy = view_center_cell(app2)
chk(near(cx, 60.5, 2.0) and near(cy, 50.5, 2.0),
    '★网格模式视口正中 ≈ (60.5,50.5)', (cx, cy))
chk(app2.mv_goto(W2 + 5, 10) is False, '网格模式越界返回 False')
app2.destroy()

print('\n§SOV11：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
