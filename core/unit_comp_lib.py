# -*- coding: utf-8 -*-
"""伴随对象模板库（Locomotor 库）。

存档里每个单位后面跟一个「伴随对象」，它的**类 = 该单位的移动方式**（Locomotor）：
    Walk  4A582744 (81B)   步兵
    Drive 4A582741 (133B)  地面载具
    Fly   92612C46 (173B)  空中（基洛夫等）
    还有 船 / 飞机 / 悬浮 / 传送 / 导弹 等（见规则里的 Locomotor=）

克隆一个型号时，如果**本档没有同移动方式的单位**当模板，就只能借到别的伴随对象
（基洛夫会因此在地上跑）。本库把这些伴随对象各存一份，缺的时候从这里取。

每个文件：`<Loco>.bin` = 该移动方式的伴随对象原始字节；
`index.json` = {Loco: {"guid": "...", "size": N, "sample": "取样的型号"}}。

★ 使用时只需要改两处（其余都是共享的运行时指针）：
    +16 = 对象基址（= 记录 +1672 − 4）
    +28/+32 = 自身键
"""
import os
import json

LIB_DIRNAME = 'unit_comp_lib'


def lib_dir():
    """模板库目录（源码运行 / PyInstaller 打包后都能找到）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, LIB_DIRNAME),
             os.path.join(getattr(__import__('sys'), '_MEIPASS', here), LIB_DIRNAME)]
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0]


def load():
    """{Loco: {'bytes': bytes, 'guid': hex, 'size': N, 'sample': 'ZEP'}}"""
    d = lib_dir()
    idx_path = os.path.join(d, 'index.json')
    out = {}
    if not os.path.isfile(idx_path):
        return out
    try:
        with open(idx_path, encoding='utf-8') as f:
            idx = json.load(f)
    except Exception:
        return out
    for loco, meta in idx.items():
        p = os.path.join(d, '%s.bin' % loco)
        if not os.path.isfile(p):
            continue
        with open(p, 'rb') as f:
            raw = f.read()
        if raw:
            out[loco] = dict(meta, bytes=raw)
    return out


def save_loco(loco, raw, guid_hex, sample):
    """把一个伴随对象样本写进库（覆盖同名）。"""
    d = lib_dir()
    os.makedirs(d, exist_ok=True)
    idx_path = os.path.join(d, 'index.json')
    idx = {}
    if os.path.isfile(idx_path):
        try:
            with open(idx_path, encoding='utf-8') as f:
                idx = json.load(f)
        except Exception:
            idx = {}
    with open(os.path.join(d, '%s.bin' % loco), 'wb') as f:
        f.write(raw)
    idx[loco] = {'guid': guid_hex, 'size': len(raw), 'sample': sample}
    with open(idx_path, 'w', encoding='utf-8') as f:
        json.dump(idx, f, ensure_ascii=False, indent=2, sort_keys=True)
    return os.path.join(d, '%s.bin' % loco)
