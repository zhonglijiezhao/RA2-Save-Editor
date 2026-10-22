# -*- coding: utf-8 -*-
"""自检/脚本共用的存档定位器。

★2026-10-22 教训：玩家会时不时把 `E:/YURI/*.SAV` 归档进 `新建文件夹 (NN)/` 或 `_备份存档/`，
写死路径的自检会突然集体"样本不存在"。这里统一**递归找**（默认深度 3），找不到再回落到写死路径。
"""
import os

ROOTS = ('E:/YURI', 'E:/YURI/新建文件夹 (27)', 'E:/YURI/新建文件夹 (26)')


def find_sav(name, roots=ROOTS, depth=3):
    """按文件名找存档 → 绝对路径；找不到返回 None。"""
    for r in roots:
        p = os.path.join(r, name)
        if os.path.exists(p):
            return p
    base = os.path.basename(name)
    for r in roots:
        if not os.path.isdir(r):
            continue
        root_depth = r.rstrip('/').count('/')
        for dirpath, dirnames, filenames in os.walk(r):
            if dirpath.rstrip('/').count('/') - root_depth >= depth:
                dirnames[:] = []
                continue
            for fn in filenames:
                if fn.lower() == base.lower():
                    return os.path.join(dirpath, fn)
    return None


def need_sav(name):
    """找不到就抛 —— 自检里宁可红，也不要静默跳过。"""
    p = find_sav(name)
    if not p:
        raise SystemExit('样本不存在：%s（E:/YURI 递归 %d 层也没找到）' % (name, 3))
    return p


# ---------------------------------------------------------------- #
# ★2026-10-22 §189（玩家投诉"每次生成存档都多几个莫名其妙的档"）：
#   **自检/探针的产物一律写进工程目录下的 `_selftest_out/`，不许再往 E:/YURI 里丢。**
#   E:/YURI 是玩家的游戏目录，只放"要进游戏验收的档"（T 系列）。
# ---------------------------------------------------------------- #
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_selftest_out')


def out(name):
    """自检产物的标准落点 → 绝对路径（目录不存在会自动建）。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    return os.path.join(OUT_DIR, name)
