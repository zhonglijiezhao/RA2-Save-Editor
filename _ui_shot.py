# -*- coding: utf-8 -*-
"""把存档编辑器整窗截成 PNG（本机无 Pillow ⇒ 像素交给 .NET System.Drawing）。

放在项目根，方便以后改界面时对照：D:/py/python.exe _ui_shot.py 出图.png [页签] [宽] [高] [--open-sav]

为什么这么写：
  · PrintWindow(PW_RENDERFULLCONTENT) 在这台机器上给的是**上下颠倒**的画面，判向很坑；
  · 屏幕 BitBlt 拿到的是正序，只要窗口在最上层即可 ⇒ 用 `-topmost` 抬到最前（不抢键盘焦点），
    截完立刻取消，用户几乎无感；
  · GetDIBits 用 24bpp + 正高度 ⇒ 行是自下而上的，统一 reverse 一次。

用法：
  python _ui_shot.py 输出.bmp [页签序号] [宽] [高] [--open-sav]
  SHOT_CROP="x,y,w,h" 只抓窗口内一块（1:1）；SHOT_FLIP=1 上下翻转（应急）
"""
import ctypes
import ctypes.wintypes as wt
import importlib.util
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

u = ctypes.windll.user32
g = ctypes.windll.gdi32
u.SetProcessDPIAware()

out = sys.argv[1]
tab = int(sys.argv[2]) if len(sys.argv) > 2 else 0
W = int(sys.argv[3]) if len(sys.argv) > 3 else 1280
H = int(sys.argv[4]) if len(sys.argv) > 4 else 820
open_sav = '--open-sav' in sys.argv

spec = importlib.util.spec_from_file_location(
    'editor_gui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

app = m.App()
app.geometry('%dx%d+40+30' % (W, H))
app.update()

if open_sav:
    sav = os.environ.get('SHOT_SAV') or r'E:/YURI/SAVE4823.SAV'
    if os.path.exists(sav):
        _d = m.SaveDoc(sav)
        if os.environ.get('SHOT_NOMAP') == '1':
            # 模拟"没有 .MAP"：把自动挂图掐掉（存档目录/记忆里都不给图）
            _d.auto_attach_map = lambda *a, **k: 'no_map'
        app._set_doc(_d)
        app.update()

if tab:
    app.nb.select(tab)
    app.update()

# 可选：切「地形数据源」（地图 / 地图+存档补格 / 存档）—— 验证没有 .MAP 时的渲染
_src = os.environ.get('SHOT_TERRSRC')
if _src:
    try:
        app.mv_terr_src.set(_src)
        app._mv_on_terr_src()
        app.update()
    except Exception as e:
        print('切地形来源失败:', e)

app.attributes('-topmost', True)
app.update()
time.sleep(0.7)
app.update()

hwnd = app.winfo_id()
frame = u.GetParent(hwnd) or hwnd
rect = wt.RECT()
u.GetWindowRect(frame, ctypes.byref(rect))
x0, y0, x1, y1 = rect.left, rect.top, rect.right, rect.bottom
w, h = x1 - x0, y1 - y0

hscr = u.GetDC(0)
hmem = g.CreateCompatibleDC(hscr)
hbmp = g.CreateCompatibleBitmap(hscr, w, h)
g.SelectObject(hmem, hbmp)
g.BitBlt(hmem, 0, 0, w, h, hscr, x0, y0, 0x00CC0020)      # SRCCOPY


class BIH(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
                ('biPlanes', wt.WORD), ('biBitCount', wt.WORD),
                ('biCompression', wt.DWORD), ('biSizeImage', wt.DWORD),
                ('biXPelsPerMeter', wt.LONG), ('biYPelsPerMeter', wt.LONG),
                ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD)]


bi = BIH()
bi.biSize = ctypes.sizeof(BIH)
bi.biWidth = w
bi.biHeight = h                     # 正高度 = 自下而上
bi.biPlanes = 1
bi.biBitCount = 24
bi.biCompression = 0
stride = ((w * 3 + 3) // 4) * 4
buf = ctypes.create_string_buffer(stride * h)
g.GetDIBits(hmem, hbmp, 0, h, buf, ctypes.byref(bi), 0)
g.DeleteObject(hbmp)
g.DeleteDC(hmem)
u.ReleaseDC(0, hscr)

app.attributes('-topmost', False)

# GetDIBits 用正 biHeight ⇒ 行是自下而上，必须翻一次（正的才是倒的，别被名字骗了）
arr = np.frombuffer(buf.raw, dtype=np.uint8).reshape(h, stride)[
    :, :w * 3].reshape(h, w, 3)[::-1].copy()
if os.environ.get('SHOT_FLIP') == '1':
    arr = arr[::-1].copy()

step = int(os.environ.get('SHOT_STEP', '2'))
arr = arr[::step, ::step]
h, w = arr.shape[0], arr.shape[1]
stride = ((w * 3 + 3) // 4) * 4
pad = np.zeros((h, stride), dtype=np.uint8)
pad[:, :w * 3] = arr.reshape(h, w * 3)
flat = pad.tobytes()

if out.lower().endswith('.png'):
    # ★真 PNG（本机没 Pillow ⇒ 自己按规范写：IHDR + zlib(IDAT) + IEND），
    #   顺手比 BMP 小一个数量级；以前这里是"BMP 套个 .png 后缀"，很脏。
    import zlib

    def _chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))

    # ★PNG 的像素是 RGB，GDI 给的是 BGR ⇒ 存盘前换序（以前这里没换，整张图红蓝互换）
    rgb = arr[:, :, ::-1]
    raw = bytearray()
    for y in range(h):
        raw.append(0)                                  # 每行 filter=0
        raw += rgb[y].tobytes()
    png = (b'\x89PNG\r\n\x1a\n'
           + _chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
           + _chunk(b'IDAT', zlib.compress(bytes(raw), 6))
           + _chunk(b'IEND', b''))
    with open(out, 'wb') as f:
        f.write(png)
else:
    hdr = struct.pack('<2sIHHI', b'BM', 14 + 40 + len(flat), 0, 0, 54)
    info = struct.pack('<IiiHHIIiiII', 40, w, h, 1, 24, 0, len(flat), 2835, 2835, 0, 0)
    with open(out, 'wb') as f:
        f.write(hdr + info + flat)
print('saved', out, w, 'x', h)
app.destroy()
