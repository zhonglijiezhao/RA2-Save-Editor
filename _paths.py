# -*- coding: utf-8 -*-
"""自检/脚本共用的存档定位器。

★2026-10-22 教训：玩家会时不时把 `E:/YURI/*.SAV` 归档进 `新建文件夹 (NN)/` 或 `_备份存档/`，
写死路径的自检会突然集体"样本不存在"。这里统一**递归找**（默认深度 3），找不到再回落到写死路径。
"""
import os

ROOTS = ('E:/YURI', 'E:/YURI/新建文件夹 (27)', 'E:/YURI/新建文件夹 (26)')


def find_sav(name, roots=ROOTS, depth=3, newest=True):
    """按文件名找存档 → 绝对路径；找不到返回 None。

    ★★2026-10-25 §275：改成**取 mtime 最新的那份**。原来是"按 ROOTS 顺序先命中先返回"，
      于是 ROOTS 里写死的 `新建文件夹 (26)` 会盖掉玩家刚挪进 `新建文件夹 (31)` 的新档
      （实测：找 `SAVE4823.SAV` 拿到一份 2 天前的旧档，自检直接报"样本不存在"）。
    """
    base = os.path.basename(name)
    hits = []
    for r in roots:
        p = os.path.join(r, name)
        if os.path.exists(p):
            hits.append(p)
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
                    hits.append(os.path.join(dirpath, fn))
    if not hits:
        return None
    if not newest:
        return hits[0]
    uniq = []
    for p in hits:
        if p not in uniq:
            uniq.append(p)
    try:
        return max(uniq, key=lambda p: os.path.getmtime(p))
    except Exception:
        return uniq[0]


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
