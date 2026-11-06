# -*- coding: utf-8 -*-
"""从一张源图生成程序图标（`icon.ico` + `icon.png`）。

为什么要脚本
------------
图标不是"拿原图另存为 .ico"就行的：源图是 1006×616 的横幅（两侧翅膀被裁边），
直接塞进图标会得到一个又扁又糊的蓝块。这里做三件事：

1. **方形裁切**，对准中央的鹰徽 + 三角框（`CROP` 常数，改图时只改这几行）；
2. 逐级 **LANCZOS 缩放**；
3. 小尺寸（≤48）额外做一次 **UnsharpMask** —— 缩到 32/16 像素时，
   银边和蓝色辉光的对比会糊成一片，轻微锐化能让轮廓站住。

生成物（跟着仓库走，打包时进 exe 资源 + `_datas`）：
    icon.ico   多尺寸（16/24/32/48/64/128/256），给 exe 图标与窗口标题栏
    icon.png   256×256，给 `iconphoto()`（高 DPI 屏比 iconbitmap 清晰）

用法：
    python make_icon.py <源图路径>
    python make_icon.py            # 不给参数就重新生成已提交的那两个文件
"""
import os
import sys

from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, 'icon.ico')
PNG = os.path.join(HERE, 'icon.png')

# 方形裁切：中心 (503, 345)、边长 500 —— 在 1006×616 的源图里正好包住
# 鹰头 + 三角徽章 + 翅膀根部。三版实测（缩到 16/32 像素后放大比对）：
#   边长 616（全高）翅膀被压成一条线；460 的鹰头贴边被切；500 的 V 形轮廓最站得住。
CROP = (503, 345, 500)

SIZES = [16, 24, 32, 48, 64, 128, 256]


def square(im, cx, cy, side):
    w, h = im.size
    side = min(side, w, h)
    left = max(0, min(int(cx - side / 2), w - side))
    top = max(0, min(int(cy - side / 2), h - side))
    return im.crop((left, top, left + side, top + side))


def build(src_path):
    im = Image.open(src_path).convert('RGB')
    sq = square(im, *CROP)
    # 先放缩到 512 做母版（ICO 最大只要 256，留一倍余量给锐化）
    master = sq.resize((512, 512), Image.LANCZOS)
    master = master.filter(ImageFilter.UnsharpMask(radius=1.2, percent=55, threshold=2))

    master.resize((256, 256), Image.LANCZOS).save(PNG, optimize=True)
    master.save(ICO, format='ICO', sizes=[(s, s) for s in SIZES])

    # 回读核对：Pillow 会把 sizes 里的每一档都真的写进去
    chk = Image.open(ICO)
    got = sorted(chk.ico.sizes())
    return got


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else None
    if not src:
        if not os.path.exists(PNG):
            print('没给源图，且 icon.png 不存在 —— 用法: python make_icon.py <源图>')
            return 2
        src = PNG          # 用已有的 256 母版重建（够用，但不如原图清晰）
    if not os.path.exists(src):
        print('找不到源图：%s' % src)
        return 2
    got = build(src)
    print('已生成：%s' % os.path.basename(ICO))
    print('  含尺寸：%s' % ', '.join('%dx%d' % s for s in got))
    print('        ：%s（256×256）' % os.path.basename(PNG))
    return 0


if __name__ == '__main__':
    sys.exit(main())
