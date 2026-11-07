# -*- coding: utf-8 -*-
"""自检：地图渲染提速后 **像素必须与原实现逐字节一致**（2026-11-07）

背景：玩家反馈「勾单位 → 地图跳 20 倍 → 卡一会儿」。查下来是两件事：
  ① 20 倍整图重绘（7880×7840 = 6180 万像素，实测 5.2 秒）；
  ② `render_rgb` 里的 `rect()` 是**逐像素** Python 循环。

优化改了 `rect()`（改成"一行 bytes + 按行切片赋值"）和高度差描边的遍历方式，
这两处属于"纯性能改写"，**输出必须一个字节都不变**。

本脚本用**旧实现的复刻**（逐像素 / 全图遍历）当参照，逐字节比对：
  地形、悬崖描边、建筑足迹、网格、选中格 全都覆盖（grid=True/False、sel 有/无）。
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

import map_render as mr                                             # noqa: E402
from editor_core import SaveDoc                                     # noqa: E402

SAV = r'E:\YURI\ALL01UMD.sav'
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


def render_ref(mp, blds, scale=6, sel=None, grid=True):
    """**旧实现复刻**（2026-11-07 之前的版本，逐像素填充 + 全图双重循环）。"""
    W, H = mr.canvas_size(mp, blds)
    tw, th = W * scale, H * scale
    buf = bytearray(mr.C_VOID * (tw * th))
    table, tpath = mr.load_theatre(mp['theater'], None)
    flat = table.get(0, mr.C_FLAT)

    def rect(x0, y0, x1, y1, c):
        for yy in range(max(0, y0), min(th, y1)):
            base = (yy * tw) * 3
            for xx in range(max(0, x0), min(tw, x1)):
                o = base + xx * 3
                buf[o] = c[0]
                buf[o + 1] = c[1]
                buf[o + 2] = c[2]

    terrain = mp['terrain']
    if terrain:
        for (x, y), (idx, sub, level) in terrain.items():
            c = table.get(idx, flat)
            if level:
                k = max(-40, min(40, level * 6))
                c = (max(0, min(255, c[0] + k)), max(0, min(255, c[1] + k)),
                     max(0, min(255, c[2] + k)))
            rect(x * scale, y * scale, (x + 1) * scale, (y + 1) * scale, c)
    else:
        rect(0, 0, tw, th, flat)

    if terrain:
        lv = lambda x, y: terrain.get((x, y), (0, 0, 0))[2]
        for y in range(H):
            for x in range(W):
                if (x, y) not in terrain:
                    continue
                a = lv(x, y)
                if lv(x + 1, y) != a:
                    rect((x + 1) * scale - 1, y * scale, (x + 1) * scale,
                         (y + 1) * scale, mr.C_CLIFF)
                if lv(x, y + 1) != a:
                    rect(x * scale, (y + 1) * scale - 1, (x + 1) * scale,
                         (y + 1) * scale, mr.C_CLIFF)

    for b in blds:
        w, h = b.get('fp') or mr.DEFAULT_FP
        rect(b['x'] * scale, b['y'] * scale,
             (b['x'] + w) * scale, (b['y'] + h) * scale, mr.C_EDGE)
        rect(b['x'] * scale + 1, b['y'] * scale + 1,
             (b['x'] + w) * scale - 1, (b['y'] + h) * scale - 1, mr.C_OCC)

    if grid:
        for x in range(0, W + 1):
            if x % 5 == 0:
                c = mr.C_GRID10 if x % 10 == 0 else mr.C_GRID5
                rect(x * scale, 0, x * scale + 1, th, c)
        for y in range(0, H + 1):
            if y % 5 == 0:
                c = mr.C_GRID10 if y % 10 == 0 else mr.C_GRID5
                rect(0, y * scale, tw, y * scale + 1, c)

    if sel:
        sx, sy = sel
        rect(sx * scale, sy * scale, (sx + 1) * scale, (sy + 1) * scale, mr.C_SEL)
        rect(sx * scale + 1, sy * scale + 1, (sx + 1) * scale - 1,
             (sy + 1) * scale - 1, (30, 120, 200))
    return W, H, buf


d = SaveDoc(SAV)
mp = mr.parse_map(MAP)
blds = d.buildings()
print('地图 %s，建筑 %d 栋' % (os.path.basename(MAP), len(blds)))

for scale, grid, sel in ((4, True, None), (6, False, (17, 33)), (8, True, (120, 200))):
    t0 = time.perf_counter()
    rW, rH, rbuf = render_ref(mp, blds, scale, sel=sel, grid=grid)
    t_ref = time.perf_counter() - t0
    t0 = time.perf_counter()
    nW, nH, nbuf, meta = mr.render_rgb(mp, blds, scale, sel=sel, grid=grid)
    t_new = time.perf_counter() - t0
    same = (rW, rH) == (nW, nH) and bytes(rbuf) == bytes(nbuf)
    diff = 0 if same else sum(1 for a, b in zip(rbuf, nbuf) if a != b)
    chk(same, 'scale=%d grid=%s sel=%s 像素逐字节一致（旧 %.2fs → 新 %.2fs，%.1f×）'
        % (scale, grid, sel, t_ref, t_new, t_ref / max(t_new, 1e-6)))
    if not same:
        print('        差异字节数 = %d / %d' % (diff, len(rbuf)))

print('\n== PNG 编码（level=6 与 level=1 的内容必须一样）==')
import zlib                                                        # noqa: E402
W, H = mr.canvas_size(mp, blds)
Wc, Hc, buf, meta = mr.render_rgb(mp, blds, 8, sel=None, grid=False)
p6 = mr.to_png(Wc * 8, Hc * 8, buf, level=6)
p1 = mr.to_png(Wc * 8, Hc * 8, buf, level=1)


def png_pixels(blob):
    """只解 IDAT，拿回原始扫描线（含每行 filter 字节，这里是恒 0）。"""
    pos = 8
    idat = b''
    while pos < len(blob):
        ln = int.from_bytes(blob[pos:pos + 4], 'big')
        tag = blob[pos + 4:pos + 8]
        data = blob[pos + 8:pos + 8 + ln]
        if tag == b'IDAT':
            idat += data
        pos += 12 + ln
    return zlib.decompress(idat)


chk(png_pixels(p6) == png_pixels(p1), 'level=6 / level=1 解码后像素完全一致')
t0 = time.perf_counter(); mr.to_png(Wc * 8, Hc * 8, buf, level=6); t6 = time.perf_counter() - t0
t0 = time.perf_counter(); mr.to_png(Wc * 8, Hc * 8, buf, level=1); t1 = time.perf_counter() - t0
chk(t1 < t6, 'level=1 更快：%.2fs vs %.2fs（%.1f×），体积 %.1f→%.1f MB'
    % (t1, t6, t6 / max(t1, 1e-6), len(p6) / 1e6, len(p1) / 1e6))

print('\n合计 PASS=%d FAIL=%d' % (OK, FAIL))
sys.exit(0 if FAIL == 0 else 1)
