# -*- coding: utf-8 -*-
"""从玩家给的原图生成程序图标（透明背景、多尺寸分档、小尺寸锐化）。

这套脚本迭代了三版，把踩过的坑都写在这儿
----------------------------------------
**① 去背景需要两套判据**（玩家前后给了两张不同底的图）：

| 源图 | 背景长什么样 | 判据 |
|---|---|---|
| 第一版 | 平滑竖直蓝渐变 | 饱和的蓝：`B-R>45` 且 `饱和度>0.40` |
| 第三版 | 画上去的**棋盘格**（假透明，其实是 RGB） | 很亮的近中性：`饱和度<0.12` 且 `亮度>205`，外加把蓝色外发光一起削掉 |

两种都再做两步收尾：
1. **只认与画布边缘连通的候选当"外部"**（从四边做 numpy 迭代膨胀）——
   这样主体内部的蓝色发光描边不会被挖出空洞；
2. **只保留最大的连通块**——第三版图右下角有"豆包 AI 生成"水印，它跟棋盘格
   不同色、会残留下来；按连通块一筛就干净了。

（`ImageDraw.floodfill` 在 Pillow 13 上实测填不动——阈值语义变了，所以自己实现。）

**② 糊不糊主要取决于缩放与锐化，不是分辨率**（玩家 2026-11-07 反馈
"别的程序在启动栏很清晰，我们做的很糊"）：
- 一步 LANCZOS 缩到 32 会把细线条糊成一片 ⇒ 先缩到 2× 再缩到目标尺寸；
- 小尺寸补一次 `UnsharpMask(0.7, 130)`，轮廓立刻站住（再高就出白边振铃）。

**③ 银白主体在浅色标题栏上几乎看不见** ⇒ 16~32 沿 alpha 外缘描一圈淡深色，
所有尺寸垫一层很淡的阴影（0.35，再重就发脏）。

用法
----
    python make_icon.py <源图>              # 自动判断背景类型
    python make_icon.py <源图> --no-cutout  # 源图已经是真透明 PNG

产物
----
    icon.ico  16/24/32/48/64/96/128/256 八档（手写 ICO 容器，每档各自缩放+锐化）
    icon.png  256×256，给 `iconphoto()` 兜底
"""
import io
import os
import struct
import sys
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, 'icon.ico')
PNG = os.path.join(HERE, 'icon.png')

SIZES = [16, 24, 32, 48, 64, 96, 128, 256]


# ---------------------------------------------------------------- 抠图
def _flood_from_border(cand):
    """从四边做 4 连通迭代膨胀，返回"与画布边缘连通"的候选（= 真正的外部）。"""
    cur = np.zeros_like(cand)
    cur[0, :] |= cand[0, :]
    cur[-1, :] |= cand[-1, :]
    cur[:, 0] |= cand[:, 0]
    cur[:, -1] |= cand[:, -1]
    while True:
        nb = cur.copy()
        nb[1:, :] |= cur[:-1, :]
        nb[:-1, :] |= cur[1:, :]
        nb[:, 1:] |= cur[:, :-1]
        nb[:, :-1] |= cur[:, 1:]
        nb &= cand
        if nb.sum() == cur.sum():
            return cur
        cur = nb


def _largest_blob(fg, shrink=4):
    """只保留最大的连通块（丢掉水印、杂点）。在 1/shrink 分辨率上 BFS，够快。"""
    small = fg[::shrink, ::shrink]
    sh, sw = small.shape
    seen = np.zeros_like(small, bool)
    best, best_n = [], 0
    for y0 in range(sh):
        for x0 in range(sw):
            if not small[y0, x0] or seen[y0, x0]:
                continue
            q = deque([(y0, x0)])
            seen[y0, x0] = True
            comp = []
            while q:
                y, x = q.popleft()
                comp.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < sh and 0 <= nx < sw and small[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
            if len(comp) > best_n:
                best_n, best = len(comp), comp
    keep = np.zeros_like(small, bool)
    for y, x in best:
        keep[y, x] = True
    return keep


def cutout(im):
    """去掉背景，返回 RGBA（尺寸同源图）。背景类型自动判断，见文件头表格。"""
    a = np.asarray(im.convert('RGB')).astype(np.float32)
    h, w, _ = a.shape
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    lum = 0.299 * R + 0.587 * G + 0.114 * B
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)

    checker = (sat < 0.12) & (lum > 205)           # 棋盘格（假透明）
    if checker.mean() > 0.5:
        # 棋盘格底：顺带削掉蓝色外发光（留着会在翼缘挂一圈蓝边）
        glow = (B > 150) & ((B - np.maximum(R, G)) > 45) & (lum > 110)
        cand = checker | glow
        mode = '棋盘格'
    else:
        cand = ((B - R) > 45) & (sat > 0.40)        # 饱和蓝底
        mode = '蓝色背景'
    if cand.mean() < 0.10:
        raise SystemExit('找不到背景（候选只有 %.0f%%）—— 这张图可能已经是透明底，'
                         '用 --no-cutout' % (100 * cand.mean()))

    outside = _flood_from_border(cand)
    keep = _largest_blob(~outside)
    up = Image.fromarray((keep * 255).astype(np.uint8), 'L').resize((w, h), Image.BILINEAR)
    alpha = np.where(np.asarray(up) > 40, 255, 0).astype(np.uint8)

    al = Image.fromarray(alpha, 'L')
    al = al.filter(ImageFilter.GaussianBlur(1.0))                                   # 羽化
    al = al.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.6))   # 收边去渗色
    print('  背景判断：%s（%d×%d）' % (mode, w, h))
    return Image.fromarray(np.dstack([a.astype(np.uint8), np.asarray(al)]), 'RGBA')


# ---------------------------------------------------------------- 构图
def tight(im):
    """裁到主体紧包围盒（透明像素不算）。"""
    box = im.split()[-1].getbbox()
    return im.crop(box) if box else im


def to_square(img, size=512, frac=0.98):
    """放进 size×size 透明画布，横向占 frac（翼尖必须完整，不能贴边裁）。"""
    scale = (size * frac) / img.width
    nw, nh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
    r = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(r, ((size - nw) // 2, (size - nh) // 2))
    return canvas


def downscale(img, size):
    """两段缩放（先 2× 再目标）—— 一步缩到 32 会把细线条糊掉。"""
    if size >= 256:
        return img.resize((size, size), Image.LANCZOS)
    mid = img.resize((size * 2, size * 2), Image.LANCZOS)
    return mid.resize((size, size), Image.LANCZOS)


def contrast(img, k):
    """围绕中灰拉一次对比度（只动 RGB，不动 alpha）。

    为什么需要：这张图的翅膀是"银色 + 细密深色条纹"，缩到 32 像素时条纹被平均成
    一片灰，整只鹰看着又淡又糊（玩家反馈的就是这个）。把对比拉 1.3 倍后，
    深蓝天鹅绒面压下去、银边亮起来，小尺寸轮廓立刻站住。
    """
    a = np.asarray(img).astype(np.float32)
    rgb = np.clip((a[:, :, :3] - 128.0) * k + 128.0, 0, 255)
    return Image.fromarray(np.dstack([rgb, a[:, :, 3:]]).astype(np.uint8), 'RGBA')


def plate(size):
    """深蓝竖向渐变的圆角底板。

    ★为什么最后改成"有底板"：这幅画的宽高比是 1.64:1（翅膀很宽），塞进正方形图标格
      宽度占满、高度只剩六成 —— 放到任务栏里（24×24）跟旁边那些方形图标一比，
      明显"小一号"（玩家 2026-11-07 量出来的：别人 24×24，我们 24×14）。
      加一层铺满格子的底板后，视觉占位跟其它程序一致。
      **这一版就不再是"透明底"了**，是玩家的取舍。
    圆角用 4× 超采样后缩小，缩到 16px 也不出毛边。
    """
    ss = size * 4
    img = Image.new('RGBA', (ss, ss), (0, 0, 0, 0))
    grad = Image.new('RGBA', (1, ss))
    for y in range(ss):
        t = y / max(1, ss - 1)
        grad.putpixel((0, y), (int(24 - 14 * t), int(56 - 38 * t), int(104 - 74 * t), 255))
    grad = grad.resize((ss, ss))
    mask = Image.new('L', (ss, ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, ss - 1, ss - 1],
                                           radius=int(ss * 0.20), fill=255)
    img.paste(grad, (0, 0), mask)
    return img.resize((size, size), Image.LANCZOS)


def make_frame(base, size):
    """出某一档的图：底板 + 主体（按尺寸调对比/锐化）。"""
    inner = max(8, int(round(size * 0.90)))
    art = base
    usm = 0
    if size <= 32:
        art = contrast(base, 1.45)          # 底板是深色，主体再压对比才不"发灰"
        usm = 160
    elif size <= 64:
        art = contrast(base, 1.20)
        usm = 110
    art = downscale(art, inner)
    canvas = plate(size)
    off = (size - inner) // 2
    canvas.alpha_composite(art, (off, off))
    if usm:
        canvas = canvas.filter(ImageFilter.UnsharpMask(radius=0.6, percent=usm, threshold=1))
    return canvas


def add_edge(img, strength=0.40):
    """沿 alpha 外缘描一圈淡深色：银白主体压在浅色标题栏上也能看见。"""
    a = img.split()[-1]
    up = np.asarray(a.filter(ImageFilter.MaxFilter(3)), dtype=np.int16)
    ring = np.clip(up - np.asarray(a, dtype=np.int16), 0, 255).astype(np.uint8)
    layer = Image.new('RGBA', img.size, (10, 18, 32, 0))
    layer.putalpha(Image.fromarray(ring, 'L').point(lambda v: int(v * strength)))
    out = Image.new('RGBA', img.size, (0, 0, 0, 0))
    out.alpha_composite(layer)
    out.alpha_composite(img)
    return out


def add_shadow(img, size, strength=0.35):
    """很淡的一层阴影（深色任务栏上几乎看不出，浅色标题栏上能把轮廓压出来）。"""
    a = img.split()[-1].filter(ImageFilter.GaussianBlur(max(0.5, size * 0.03)))
    a = a.point(lambda v: int(v * strength))
    layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    layer.putalpha(a)
    out = Image.new('RGBA', img.size, (0, 0, 0, 0))
    out.alpha_composite(layer, (0, max(1, size // 48)))
    out.alpha_composite(img)
    return out


# ---------------------------------------------------------------- 写 ICO
def write_ico(path, frames):
    """手写 ICO 容器：frames = [(size, RGBA), ...]，每帧存 PNG。

    `img.save(sizes=[...])` 只会把同一张图缩成多档，而这里每档的缩放/锐化都不同，
    所以自己拼 ICONDIR + 16 字节目录项 + PNG 条目（Vista+ 原生支持）。
    """
    blobs = []
    for size, img in frames:
        buf = io.BytesIO()
        img.resize((size, size), Image.LANCZOS).save(buf, format='PNG', optimize=True)
        blobs.append((size, buf.getvalue()))

    head = struct.pack('<HHH', 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    dirs = b''
    for size, blob in blobs:
        wh = 0 if size >= 256 else size
        dirs += struct.pack('<BBBBHHII', wh, wh, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    with open(path, 'wb') as f:
        f.write(head + dirs)
        for _s, blob in blobs:
            f.write(blob)
    return [s for s, _ in blobs]


def build(src_path, do_cutout=True):
    im = Image.open(src_path)
    if do_cutout:
        im = cutout(im)
    elif im.mode != 'RGBA':
        im = im.convert('RGBA')
    else:
        im = im.copy()
    sub = tight(im)
    base = to_square(sub, 512, frac=0.98)
    print('  主体 %d×%d → 画布 512' % sub.size)

    frames = [(s, make_frame(base, s)) for s in SIZES]

    sizes = write_ico(ICO, frames)
    make_frame(base, 256).save(PNG, optimize=True)
    return sizes


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print('用法: python make_icon.py <源图> [--no-cutout]')
        return 2
    src = args[0]
    if not os.path.exists(src):
        print('找不到源图：%s' % src)
        return 2
    sizes = build(src, do_cutout=('--no-cutout' not in sys.argv))
    print('已生成 %s（%s）' % (os.path.basename(ICO),
                              ', '.join('%dx%d' % (s, s) for s in sizes)))
    print('已生成 %s（256×256）' % os.path.basename(PNG))
    return 0


if __name__ == '__main__':
    sys.exit(main())
