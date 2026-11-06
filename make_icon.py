# -*- coding: utf-8 -*-
"""从一张「蓝渐变底 + 银色鹰徽」的源图生成程序图标（透明背景）。

为什么不是一个 `resize().save('.ico')`
------------------------------------
1. **要去背景**：源图是照片式的蓝色渐变背景，直接当图标就是一块蓝方块。
   这里按"逐行多项式拟合背景 → 算残差 → 银色部分强制不透明 → 蓝辉光压到 30%"
   做抠图（源图背景是竖直渐变，三次多项式拟合得足够准；翅膀把整行占满的行不参与拟合）。
2. **小尺寸要换构图**：整只鹰横向 1006px，缩到 16px 就是一条细线。
   所以 **分档出图**：≥128 用整只鹰（忠实），48~96 用中心裁切（鹰头+三角），
   ≤32 再放大一档 —— 这是系统图标的常规做法，不是投机。
3. **`.ico` 里要真的装多张不同的图**：Pillow 的 `save(sizes=...)` 只会把同一张图缩成
   多个尺寸，做不出"分档构图" ⇒ 这里手写 ICO 容器（PNG 压缩条目，Vista+ 原生支持）。

用法
----
    python make_icon.py <源图>              # 抠图 + 分档，生成 icon.ico / icon.png
    python make_icon.py <源图> --no-cutout  # 源图已经是透明 PNG 时用

产物
----
    icon.ico   16/24/32/48/64/96/128/256 八档，每档按档位选构图
    icon.png   256×256（整只鹰），给 `iconphoto()` 兜底
"""
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, 'icon.ico')
PNG = os.path.join(HERE, 'icon.png')

SIZES = [16, 24, 32, 48, 64, 96, 128, 256]


# ---------------------------------------------------------------- 抠图
def cutout(im):
    """去掉蓝色背景，只留主体，返回 RGBA（尺寸同源图）。

    ★两代源图都适用（都是"饱和蓝底 + 银白主体"）：
      · 第一版是平滑竖直渐变；
      · 第二版是放射条纹（明暗条纹对比很强）。
      一开始用"逐行多项式拟合背景"只对第一版有效；换成**颜色筛选 + 连通性**后通吃：
        ① 背景 = 饱和的蓝（`B-R>45` 且 `饱和度>0.40`）—— 银色主体的饱和度只有 0.15~0.2，
           天然被排除，这是整套判据的立足点；
        ② 只有**与画布边缘连通**的候选才算"外部"（从四边做迭代膨胀），
           这样主体内部的蓝色发光描边不会被当成背景挖出空洞；
        ③ alpha：外部 0、内部 255，再羽化 1.2px 并收 1px 去掉边缘的背景渗色。

    ⚠ Pillow 13 的 `ImageDraw.floodfill` 阈值语义变了（不再按"与种子色的差"填充），
      实测连纯色块都填不动 ⇒ 这里自己用 numpy 做膨胀，1024×733 约 1 秒。
    """
    a = np.asarray(im.convert('RGB')).astype(np.float32)
    h, w, _ = a.shape
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)

    # ① 背景候选：偏蓝且饱和
    bgish = ((B - R) > 45) & (sat > 0.40)
    if bgish.mean() < 0.15:
        raise SystemExit('几乎找不到蓝色背景（%.0f%%）—— 这张图可能已经是透明底，'
                         '试试 --no-cutout' % (100 * bgish.mean()))

    # ② 从四边膨胀，只保留与边界连通的部分
    cur = np.zeros_like(bgish)
    cur[0, :] |= bgish[0, :]
    cur[-1, :] |= bgish[-1, :]
    cur[:, 0] |= bgish[:, 0]
    cur[:, -1] |= bgish[:, -1]
    while True:
        nb = cur.copy()
        nb[1:, :] |= cur[:-1, :]
        nb[:-1, :] |= cur[1:, :]
        nb[:, 1:] |= cur[:, :-1]
        nb[:, :-1] |= cur[:, 1:]
        nb &= bgish
        if nb.sum() == cur.sum():
            break
        cur = nb

    # ③ 羽化 + 收边
    alpha = np.where(cur, 0, 255).astype(np.uint8)
    al = Image.fromarray(alpha, 'L')
    al = al.filter(ImageFilter.GaussianBlur(1.2))
    al = al.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.7))
    return Image.fromarray(np.dstack([a.astype(np.uint8), np.asarray(al)]), 'RGBA')


# ---------------------------------------------------------------- 分档构图
def tight(im):
    """裁到主体紧包围盒（透明像素不算）。"""
    box = im.split()[-1].getbbox()
    return im.crop(box) if box else im


def to_square(img, size=512, frac=0.96, zoom=1.0, dy=0.0):
    """把任意比例的图放进 size×size 透明画布，横向占 frac（zoom 再放大）。"""
    scale = (size * frac * zoom) / img.width
    nw, nh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
    r = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(r, ((size - nw) // 2, (size - nh) // 2 + int(dy * size)))
    return canvas


def master_for(size, sub):
    """所有尺寸都用**整只鹰**（翅膀不能裁）。

    ★2026-11-07 玩家指出：早先 48 以下用"中心裁切"（放大鹰头+三角）虽然小尺寸更显眼，
      但翅膀被切掉了一半 —— 一放上任务栏就能看出来，观感是坏的。
      现在统一整只鹰：小尺寸会变窄，但形状是完整的。
      为了小尺寸不吃亏，宽度占比随尺寸略微加大（16/24 用满，96 以上留 4% 边）。
    """
    frac = 1.0 if size <= 32 else (0.98 if size <= 64 else 0.96)
    return to_square(sub, 512, frac=frac)


def sharpen(img, size):
    """小尺寸缩完会糊，轻微锐化让银边站住（1~2% 的强度，过犹不及）。"""
    if size > 48:
        return img
    return img.filter(ImageFilter.UnsharpMask(radius=0.8, percent=80, threshold=1))


def add_edge(img, size, strength=0.55):
    """沿 alpha 外缘描一圈深色（只给小尺寸用）。

    16/32 像素下，银白主体压在浅色标题栏上只剩"灰蒙蒙一团"（实测对比图）。
    描边把轮廓勾出来；48 以上不缺这点对比度，描了反而显脏 —— 所以按尺寸开关。
    """
    a = img.split()[-1]
    up = np.asarray(a.filter(ImageFilter.MaxFilter(3)), dtype=np.int16)
    ring = np.clip(up - np.asarray(a, dtype=np.int16), 0, 255).astype(np.uint8)
    layer = Image.new('RGBA', img.size, (12, 20, 34, 0))
    layer.putalpha(Image.fromarray(ring, 'L').point(lambda v: int(v * strength)))
    out = Image.new('RGBA', img.size, (0, 0, 0, 0))
    out.alpha_composite(layer)
    out.alpha_composite(img)
    return out


def add_shadow(img, size):
    """主体下面垫一层很淡的阴影。

    ★为什么需要：主体是**银白色**，Windows 浅色主题的标题栏也是浅色 ——
      纯透明图标在上面几乎看不见（实测对比图里 16/32 像素明显发虚）。
      阴影只加在 alpha 上（纯黑、45% 不透明度、按尺寸缩放模糊半径），
      深色任务栏上几乎看不出来，浅色背景上能把轮廓"压"出来。
    """
    a = img.split()[-1].filter(ImageFilter.GaussianBlur(max(0.5, size * 0.03)))
    a = a.point(lambda v: int(v * 0.45))
    layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    layer.putalpha(a)
    out = Image.new('RGBA', img.size, (0, 0, 0, 0))
    out.alpha_composite(layer, (0, max(1, size // 48)))
    out.alpha_composite(img)
    return out


# ---------------------------------------------------------------- 写 ICO
def write_ico(path, frames):
    """手写 ICO 容器：frames = [(size, PIL.Image RGBA), ...]，每帧存 PNG。

    为什么不 `img.save(..., sizes=[...])`：那样只能把**同一张图**缩成多档，
    而这里每档是**不同构图**。（PNG 压缩条目 Vista+ 原生支持，Windows 10/11 无问题。）
    """
    import io
    blobs = []
    for size, img in frames:
        buf = io.BytesIO()
        img.resize((size, size), Image.LANCZOS).save(buf, format='PNG', optimize=True)
        blobs.append((size, buf.getvalue()))

    head = struct.pack('<HHH', 0, 1, len(blobs))        # reserved/type/count
    offset = 6 + 16 * len(blobs)
    dirs = b''
    for size, blob in blobs:
        wh = 0 if size >= 256 else size                 # 256 记 0
        dirs += struct.pack('<BBBBHHII', wh, wh, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    with open(path, 'wb') as f:
        f.write(head + dirs)
        for _size, blob in blobs:
            f.write(blob)
    return [s for s, _ in blobs]


def build(src_path, do_cutout=True):
    im = Image.open(src_path)
    if do_cutout:
        im = cutout(im)
    elif im.mode != 'RGBA':
        im = im.convert('RGBA')
    sub = tight(im)
    full = to_square(sub, 512, frac=0.96)   # 256 用的母版

    frames = []
    for s in SIZES:
        m = master_for(s, sub)
        px = sharpen(m.resize((s, s), Image.LANCZOS), s)
        if s <= 48:
            px = add_edge(px, s)                 # 小尺寸：先描边（在主体 a 上）
        px = add_shadow(px, s)                   # 再垫阴影
        frames.append((s, px))

    sizes = write_ico(ICO, frames)
    px256 = add_shadow(sharpen(full, 256).resize((256, 256), Image.LANCZOS), 256)
    px256.save(PNG, optimize=True)
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
