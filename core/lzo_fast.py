# -*- coding: utf-8 -*-
"""LZO1X 加速后端（C DLL）：`core/lzo_fast.c` 编出来的 `lzo_fast.dll`。

为什么要有它（2026-10-17 21:5x，玩家问"纯 Python 能用别的语言重构吗"）：
  · 39 MB 明文解压：纯 Python **2.59 s** ／ C **~0.03 s**
  · 611 × 64 KB 块压缩：纯 Python 并行后 **7.1 s** ／ C **~0.3 s**

用法（两个函数都不抛异常，拿不到就返回 None，调用方回退纯 Python）：
    from lzo_fast import decode, encode
    out = decode(packed, 65536)      # 解不出来/没 DLL ⇒ None
    pk  = encode(plain)              # 里面会自检"压完能解回原文"，不过就 None

⚠ DLL 是 `/MT` 静态链接，不依赖 VC 运行库；打包时放进 `_internal/`（见 .spec 的 datas）。
"""
import ctypes
import os
import sys

_lock = None
_dll = None
_tried = False


def _candidates():
    out = []
    base = os.path.dirname(os.path.abspath(__file__))
    out.append(os.path.join(base, 'lzo_fast.dll'))
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        out.append(os.path.join(exe_dir, 'lzo_fast.dll'))
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            out.append(os.path.join(meipass, 'lzo_fast.dll'))
            out.append(os.path.join(meipass, '_internal', 'lzo_fast.dll'))
        out.append(os.path.join(exe_dir, '_internal', 'lzo_fast.dll'))
    return out


def _get():
    """惰性加载；成功返回 ctypes 句柄，否则 None（只尝试一次）。"""
    global _dll, _tried, _lock
    if _tried:
        return _dll
    _tried = True
    try:
        import threading
        _lock = threading.Lock()
    except Exception:
        pass
    for p in _candidates():
        if not os.path.isfile(p):
            continue
        try:
            d = ctypes.CDLL(p)
            d.rlo_decode.restype = ctypes.c_int
            d.rlo_decode.argtypes = [ctypes.c_char_p, ctypes.c_int,
                                     ctypes.c_char_p, ctypes.c_int]
            d.rlo_encode.restype = ctypes.c_int
            d.rlo_encode.argtypes = [ctypes.c_char_p, ctypes.c_int,
                                     ctypes.c_char_p, ctypes.c_int]
            _dll = d
            break
        except Exception:
            _dll = None
    return _dll


def available():
    return _get() is not None


def backend_path():
    d = _get()
    if d is None:
        return None
    try:
        buf = ctypes.create_string_buffer(512)
        # 不提供 GetModuleFileName 的包装，退化为候选列表里的存在项
        for p in _candidates():
            if os.path.isfile(p):
                return p
    except Exception:
        pass
    return 'lzo_fast.dll'


def decode(src, cap):
    """→ bytes（长度即真实解出长度）或 None。"""
    d = _get()
    if d is None or cap <= 0:
        return None
    try:
        buf = ctypes.create_string_buffer(cap)
        n = d.rlo_decode(bytes(src), len(src), buf, cap)
        if n < 0:
            return None
        return buf.raw[:n]
    except Exception:
        return None


def encode(data):
    """→ packed bytes 或 None（含"压完能解回原文"自检）。"""
    d = _get()
    if d is None:
        return None
    n = len(data)
    if n == 0:
        return b'\x11\x00\x00'
    cap = n + n // 16 + 4096
    try:
        buf = ctypes.create_string_buffer(cap)
        k = d.rlo_encode(bytes(data), n, buf, cap)
        if k < 0:
            return None
        pk = buf.raw[:k]
        back = decode(pk, n + 64)
        if back is None or len(back) != n or back != data:
            return None
        return pk
    except Exception:
        return None
