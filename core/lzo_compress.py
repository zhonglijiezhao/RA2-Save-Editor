# -*- coding: utf-8 -*-
"""LZO1X-1 贪心压缩器 v4 —— 长匹配(M3/M4) + 定长哈希表 + C 级切片比较。

v3 的问题（实测）：MAXM=33 ⇒ 匹配长度上限 33 字节（近零区要 1897B，游戏 58B）；
每字节进 Python 哈希链 + 逐字节比较 ⇒ 每 64KB 块 1~2.4 秒。
v4：长匹配上限抬到 65535（M3 的 0x20 扩展 = 33+255z+last；M4 扩展 = 9+255z+last）、
距离支持到 49151（M4 两档）、定长哈希表（2 槽/桶）+ 8/4/1 步进切片比较。

★距离字段语义（与 lzo1x.py 解码器逐条对齐）：
  · M2/M3：字段 = 实际距离 − 1（M2 ≤ 2047，M3 ≤ 16383）
  · M4  ：字段 = 实际距离（16384..32767 / 32768..49151，由 token bit3 分档）
state 恒 0 ⇒ 继续保留"让末尾剩 1..3 字节的匹配放弃"的老规矩。
"""
MINM, MAXM = 3, 0xFFFF
M2_MAX_OFF = 2048          # 实际距离上限（token >= 64，长度 3..8）
M3_MAX_OFF = 16384         # 实际距离上限（token >= 32，长度 3..33；>=34 走 0x20 扩展）
M4_MAX_OFF = 49151         # 实际距离上限（token 16..31，长度 3..9；>=10 走扩展）

_HB = 16
_SIZE = 1 << _HB


def _encodable(l, dist):
    if not (3 <= l <= MAXM):
        return False
    return dist <= M4_MAX_OFF


def _emit_match(out, length, dist):
    """dist = 实际距离（≥1）。"""
    if 3 <= length <= 8 and dist <= M2_MAX_OFF:
        df = dist - 1
        t = ((length - 1) << 5) | ((df & 7) << 2)
        out += bytes([t, df >> 3])
        return
    if dist <= M3_MAX_OFF:
        df = dist - 1
        if length <= 33:
            nxt = df << 2
            out += bytes([32 + (length - 2), nxt & 0xFF, (nxt >> 8) & 0xFF])
        else:
            rest = length - 33
            z, last = divmod(rest, 255)
            if last == 0:
                z -= 1
                last = 255
            nxt = df << 2
            out += b'\x20' + b'\x00' * z + bytes([last, nxt & 0xFF, (nxt >> 8) & 0xFF])
        return
    # M4：实际距离 16384..49151
    hi = 0 if dist <= 32767 else 1
    df = dist - 16384 - (0x4000 if hi else 0)      # 0..16383
    if length <= 9:
        out += bytes([16 + (hi << 3) + (length - 2)])
    else:
        rest = length - 9
        z, last = divmod(rest, 255)
        if last == 0:
            z -= 1
            last = 255
        out += bytes([16 + (hi << 3)]) + b'\x00' * z + bytes([last])
    nxt = df << 2
    out += bytes([nxt & 0xFF, (nxt >> 8) & 0xFF])


def _emit_lit(out, data, i, j):
    run = j - i
    if run < 4:
        raise ValueError('literal run 过短: %d (必须 >=4)' % run)
    if run <= 18:
        out += bytes([run - 3])
    else:
        rest = run - 18
        z, last = divmod(rest, 255)
        if last == 0:
            z -= 1
            last = 255
        out += b'\x00' + b'\x00' * z + bytes([last])
    out += data[i:j]


def _mlen(data, p, s, limit):
    """data[p:] 与 data[s:] 的公共前缀长度（≤ limit）；p > s。步进切片比较（C 级）。"""
    l = 0
    while l + 16 <= limit and data[p + l:p + l + 16] == data[s + l:s + l + 16]:
        l += 16
    while l + 4 <= limit and data[p + l:p + l + 4] == data[s + l:s + l + 4]:
        l += 4
    while l < limit and data[p + l] == data[s + l]:
        l += 1
    return l


def compress(data):
    # ★2026-10-17 21:5x：优先用 C 后端（`lzo_fast.dll`），它内部会自检"压完能解回原文"。
    #   39MB 档：611 块串行 11.2s / 多进程 7.1s → **0.3s**。DLL 不在则走下面的纯 Python。
    try:
        import lzo_fast
        pk = lzo_fast.encode(data)
        if pk is not None:
            return pk
    except Exception:
        pass
    n = len(data)
    out = bytearray()
    if n == 0:
        return b'\x11\x00\x00'
    t1 = [-1] * _SIZE
    t2 = [-1] * _SIZE
    shift = 32 - _HB

    def h3(p):
        v = data[p] | (data[p + 1] << 8) | (data[p + 2] << 16)
        return ((v * 0x9E3779B1) & 0xFFFFFFFF) >> shift

    def probe(p):
        limit = n - p
        if limit < 3:
            return None
        h = h3(p)
        s1 = t1[h]
        s2 = t2[h]
        t2[h] = s1
        t1[h] = p
        best_l = best_d = 0
        for s in (s1, s2):
            if s < 0 or s >= p:            # 排除自身/无效候选（d 必须 ≥ 1）
                continue
            d = p - s
            if d > M4_MAX_OFF:
                continue
            l = _mlen(data, p, s, MAXM if MAXM < limit else limit)
            if l >= 3 and l > best_l:
                best_l, best_d = l, d
        return (best_l, best_d) if best_l >= 3 else None

    i = 0
    while i < n:
        m = probe(i)
        if m is not None and 0 < n - (i + m[0]) < 4:
            m = None                        # 尾部 1..3 字节无法独立成字面量
        if m is not None:
            length, dist = m
            _emit_match(out, length, dist)
            if length > 8:                  # 长匹配内部少插哈希（提速）
                for p in (i + length - 2, i + length - 1):
                    if p + 3 <= n:
                        h = h3(p)
                        t2[h] = t1[h]
                        t1[h] = p
            else:
                for p in range(i + 1, i + length):
                    if p + 3 <= n:
                        h = h3(p)
                        t2[h] = t1[h]
                        t1[h] = p
            i += length
            continue
        j = i + 1
        end = n
        mj_best = None
        while j < n:
            mj = probe(j)
            if j - i >= 4 and mj and not (0 < n - (j + mj[0]) < 4):
                end = j
                mj_best = mj
                break
            j += 1
        _emit_lit(out, data, i, end)
        i = end
        if end >= n or mj_best is None:
            break
        # ★直接使用扫描时找到的匹配：不能再 probe(end) —— 扫描已把 end 注册进表，
        #   重探只剩一个候选槽，匹配可能丢失 ⇒ 会发出连续两个字面量 run，
        #   而解码器在"字面量之后 state=4"时把 <16 的 token 当 M1 解析 ⇒ 状态错乱。
        length, dist = mj_best
        _emit_match(out, length, dist)
        if length > 8:
            for p in (i + length - 2, i + length - 1):
                if p + 3 <= n:
                    h = h3(p)
                    t2[h] = t1[h]
                    t1[h] = p
        else:
            for p in range(i + 1, i + length):
                if p + 3 <= n:
                    h = h3(p)
                    t2[h] = t1[h]
                    t1[h] = p
        i += length
    out += b'\x11\x00\x00'
    return bytes(out)
