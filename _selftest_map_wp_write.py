# -*- coding: utf-8 -*-
"""§SOV13 自检：地图页「✏ 写到X/Y」—— 改路径点坐标（只写存档）。

覆盖：
  1. 写入成功：坐标变、**存档长度不变**、其它路径点一个不动
  2. 再写回原值 ⇒ 整档明文逐字节复原（可逆）
  3. 非数字 / 越界坐标 ⇒ 只给提示，一个字节都不动
  4. 原来空的号 ⇒ 当新建（返回值/状态栏说明）
  5. 地图 [Waypoints] 里有同号但在别处 ⇒ 状态栏报"不一致"，画布多画橙圈（号*）
  6. 写入后路径点层立刻重画（不靠整图重渲染）
跑：D:/py/python.exe _selftest_map_wp_write.py
"""
import hashlib
import importlib.util
import io
import os
import shutil
import struct
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
spec = importlib.util.spec_from_file_location('sedgui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from editor_core import SaveDoc                                      # noqa: E402

SRC = r'E:\YURI\SAVE6784.SAV'
MAP = r'E:\YURI\sov01umd.map'
TMP = os.path.join(tempfile.gettempdir(), '_sov13_wp_write.SAV')
shutil.copy2(SRC, TMP)

OK = FAIL = 0


def chk(cond, msg, extra=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s%s' % (msg, '' if extra is None else ('  → %r' % (extra,))))


for fn in ('showinfo', 'showwarning', 'showerror'):
    setattr(m.messagebox, fn, lambda *a, **k: None)
m.messagebox.askyesno = lambda *a, **k: True

app = m.App()
app.geometry('1280x860+16+8')
app.update()
app._set_doc(SaveDoc(TMP))
app.update()
app.nb.select(6)
app.update()
app.mv_load(MAP)
app.update()
d = app.doc
st = app.mv_state
st['scale'] = 8
app.mv_render()
app.update()

W, H = app._mv_cell_space()
print('图 %d×%d ｜ 路径点 %d 个' % (W, H, len(d.waypoints_save())))

print('== A. 写入一个已有路径点 ==')
sv0 = dict(d.waypoints_save())
n0 = sorted(sv0)[0]
old0 = sv0[n0]
n_len = len(d.plain)
n_hash = hashlib.md5(bytes(d.plain)).hexdigest()
new0 = ((old0[0] + 3) % W, (old0[1] + 2) % H)
app.mv_gwp.set(str(n0))
app.mv_gx.set(str(new0[0]))
app.mv_gy.set(str(new0[1]))
app.mv_set_wp_xy()
app.update()
chk(d.waypoint_cell(n0) == new0, '★%d 号 %s → %s' % (n0, old0, new0), d.waypoint_cell(n0))
chk(len(d.plain) == n_len, '存档长度不变（原地 4 字节）', (n_len, len(d.plain)))
sv1 = dict(d.waypoints_save())
chk(all(sv1[k] == sv0[k] for k in sv0 if k != n0), '其它 %d 个路径点一个没动' % (len(sv0) - 1),
    [(k, sv0[k], sv1.get(k)) for k in sv0 if k != n0 and sv1.get(k) != sv0[k]][:3])
chk(app.mv_gx.get() == str(new0[0]) and app.mv_gy.get() == str(new0[1]),
    'X / Y 框停在新坐标', (app.mv_gx.get(), app.mv_gy.get()))
chk('路径点（存档）' in app.mv_info.cget('text')
    and str(n0) in app.mv_info.cget('text'),
    '信息栏按**存档**报这格有路径点 %d' % n0, app.mv_info.cget('text')[:70])

# ★§SOV14：画布上的绿圆必须画在**存档坐标**（以前画的是地图坐标 ⇒ 改完看着没动）
def _wp_label_pos(num):
    for i in app.mv_canvas.find_withtag('wp'):
        if app.mv_canvas.type(i) == 'text' and app.mv_canvas.itemcget(i, 'text') == str(num):
            return app.mv_canvas.coords(i)
    return None
_s = app._mv_scale_now()
_pos = _wp_label_pos(n0)
chk(_pos is not None and abs(_pos[0] - (new0[0] + 0.5) * _s) < 2
    and abs(_pos[1] - (new0[1] + 0.5) * _s) < 2,
    '★%d 号标签画在存档坐标 %s（画布 %s，期望 %.0f,%.0f）'
    % (n0, new0, _pos, (new0[0] + 0.5) * _s, (new0[1] + 0.5) * _s), _pos)

print('== B. 写回原值 ⇒ 逐字节复原 ==')
app.mv_gwp.set(str(n0))
app.mv_gx.set(str(old0[0]))
app.mv_gy.set(str(old0[1]))
app.mv_set_wp_xy()
app.update()
chk(d.waypoint_cell(n0) == old0, '坐标回到 %s' % (old0,), d.waypoint_cell(n0))
chk(hashlib.md5(bytes(d.plain)).hexdigest() == n_hash, '★整档明文 md5 与开始时一致')

print('== C. 坏输入 / 越界：一个字节都不动 ==')
h_before = hashlib.md5(bytes(d.plain)).hexdigest()
app.mv_gx.set('abc')
app.mv_set_wp_xy()
chk('整数' in app.status.get(), '非数字被拒（%s）' % app.status.get()[:40])
app.mv_gx.set(str(W + 40))
app.mv_set_wp_xy()
chk('超出范围' in app.status.get(), '越界被拒（%s）' % app.status.get()[:50])
app.mv_gwp.set('abc')
app.mv_gx.set('10')
app.mv_set_wp_xy()
chk('数字' in app.status.get(), '路径点号非数字被拒（%s）' % app.status.get()[:40])
app.mv_gwp.set('9999')
app.mv_gx.set('10')
app.mv_set_wp_xy()
chk('0..' in app.status.get(), '路径点号越界被拒（%s）' % app.status.get()[:40])
chk(hashlib.md5(bytes(d.plain)).hexdigest() == h_before, '★三次坏输入后整档 md5 没变')

print('== D. 空的号 = 新建 ==')
empty = [k for k in range(d.WP_COUNT) if k not in sv0]
if empty:
    n1 = empty[0]
    app.mv_gwp.set(str(n1))
    app.mv_gx.set('12')
    app.mv_gy.set('34')
    app.mv_set_wp_xy()
    app.update()
    chk(d.waypoint_cell(n1) == (12, 34), '%d 号（原来是空）现在 = (12,34)' % n1,
        d.waypoint_cell(n1))
    chk('新建' in app.status.get(), '状态栏说明是新建（%s）' % app.status.get()[:40])
    # 清回去，并**重画**路径点层 —— 否则画布上还留着刚写的那个点，后面数图元会差 1
    app.doc.set_waypoint(n1, 0, 0)
    st['wpdata'] = None
    app.mv_canvas.delete('wp')
    app._mv_draw_waypoints(app.mv_canvas)
    app.update()
else:
    print('  （本档 702 个号全满，跳过）')

print('== E. 地图有同号但在别处 ⇒ 报不一致 + 橙圈 ==')
mp_w, only_w = app._mv_wp_points()
diverge = [k for k in sv0
           if k in mp_w and (int(mp_w[k][0]), int(mp_w[k][1])) != sv0[k]]
if diverge:
    n2 = diverge[0]
    old2 = sv0[n2]
    mpos = mp_w[n2]
    new2 = ((old2[0] + 5) % W, (old2[1] + 1) % H)
    cnt0 = len(app.mv_canvas.find_withtag('wp'))
    app.mv_gwp.set(str(n2))
    app.mv_gx.set(str(new2[0]))
    app.mv_gy.set(str(new2[1]))
    app.mv_set_wp_xy()
    app.update()
    chk(d.waypoint_cell(n2) == new2, '%d 号改成 %s（地图里是 %s）' % (n2, new2, mpos))
    chk('不一致' in app.status.get(), '状态栏报「与地图不一致」（%s）' % app.status.get()[:60])
    cnt1 = len(app.mv_canvas.find_withtag('wp'))
    # 这个号改之前**已经在分叉**（地图一份、存档一份不同）⇒ 图元数不变，只是橙圈挪了位置。
    # 真正要保证的是"重画没丢东西"。
    chk(cnt1 >= cnt0, '★路径点层重画后图元没丢（%d → %d）' % (cnt0, cnt1), (cnt0, cnt1))
    chk(d.waypoint_cell(n2) == new2, '存档里这号确实是 %s' % (new2,),
        d.waypoint_cell(n2))
    # 复原
    d.set_waypoint(n2, old2[0], old2[1])
    st['wpdata'] = None
    app.mv_canvas.delete('wp')
    app._mv_draw_waypoints(app.mv_canvas)
    app.update()
    chk(len(app.mv_canvas.find_withtag('wp')) == cnt0, '复原后图元数回到 %d' % cnt0)
else:
    print('  （本图与存档没有坐标不一致的号，跳过）')

app.destroy()
os.remove(TMP)
print('\n§SOV13：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
