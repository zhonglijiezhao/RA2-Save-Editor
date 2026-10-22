# -*- coding: utf-8 -*-
"""LZO1X 解码器 —— 严格按 Linux kernel lzo1x_decompress_safe 移植。
验证基准 1: 地图 IsoMapPack5 (8192B/块, 社区公认 miniLZO)
验证基准 2: 存档 CONTENTS 的 64KB 块
"""
import sys, struct, re, base64

M2_MAX_OFFSET = 0x800

class LZOError(Exception):
    pass

def lzo1x_decompress(inp, expect_out=None):
    """返回 (bytes, consumed_ip)。严格移植 kernel 版。

    ★2026-10-17 21:5x：有 `lzo_fast.dll` 且知道解压后长度（`expect_out`）时走 C 后端
      （39MB：2.59s → 0.03s）；DLL 不在/失败则继续用下面的纯 Python 实现。
    """
    if expect_out:
        try:
            import lzo_fast
            o = lzo_fast.decode(inp, expect_out)
            if o is not None and len(o) == expect_out:
                return o, len(inp)
        except Exception:
            pass
    n = len(inp)
    ip = 0
    out = bytearray()
    state = 0
    bv = 0
    if n < 3:
        raise LZOError('input_overrun')
    if n >= 5 and inp[0] == 17:
        bv = inp[1]
        ip = 2
    if inp[ip] > 17:
        t = inp[ip] - 17
        ip += 1
        if t < 4:
            out += inp[ip:ip + t]; ip += t
            state = t
        else:
            out += inp[ip:ip + t]; ip += t
            state = 4
    while True:
        if ip >= n:
            raise LZOError('input_overrun @%d' % ip)
        t = inp[ip]; ip += 1
        m_pos = None
        if t < 16:
            if state == 0:
                if t == 0:
                    ip_last = ip
                    while inp[ip] == 0:
                        ip += 1
                        if ip >= n: raise LZOError('input_overrun')
                    offset = ip - ip_last
                    t += (offset << 8) - offset + 15 + inp[ip]; ip += 1
                t += 3
                if ip + t > n: raise LZOError('input_overrun (lit %d)' % t)
                out += inp[ip:ip + t]; ip += t
                state = 4
                continue
            elif state != 4:
                nxt = t & 3
                m_pos = len(out) - 1 - (t >> 2) - (inp[ip] << 2); ip += 1
                if m_pos < 0: raise LZOError('lookbehind %d' % m_pos)
                out += out[m_pos:m_pos + 2]
                state = nxt; t = nxt
                if ip + t > n: raise LZOError('input_overrun')
                out += inp[ip:ip + t]; ip += t
                continue
            else:
                nxt = t & 3
                m_pos = len(out) - (1 + M2_MAX_OFFSET) - (t >> 2) - (inp[ip] << 2); ip += 1
                if m_pos < 0: raise LZOError('lookbehind %d' % m_pos)
                t = 3
        elif t >= 64:
            nxt = t & 3
            m_pos = len(out) - 1 - ((t >> 2) & 7) - (inp[ip] << 3); ip += 1
            if m_pos < 0: raise LZOError('lookbehind %d' % m_pos)
            t = (t >> 5) - 1 + 2
        elif t >= 32:
            t = (t & 31) + 2
            if t == 2:
                ip_last = ip
                while inp[ip] == 0:
                    ip += 1
                    if ip >= n: raise LZOError('input_overrun')
                offset = ip - ip_last
                t += (offset << 8) - offset + 31 + inp[ip]; ip += 1
            m_pos = len(out) - 1
            nxt = inp[ip] | (inp[ip + 1] << 8); ip += 2
            m_pos -= nxt >> 2
            if m_pos < 0: raise LZOError('lookbehind %d' % m_pos)
            nxt &= 3
        else:  # 16..31
            if ip + 2 > n: raise LZOError('input_overrun')
            nxt = inp[ip] | (inp[ip + 1] << 8)
            if ((nxt & 0xfffc) == 0xfffc) and ((t & 0xf8) == 0x18) and bv:
                t &= 7; t |= inp[ip + 2] << 3
                t += 0x400
                out += b'\x00' * t
                nxt &= 3
                ip += 3
                state = nxt; t = nxt
                out += inp[ip:ip + t]; ip += t
                continue
            m_pos = len(out)
            m_pos -= (t & 8) << 11
            t = (t & 7) + 2
            if t == 2:
                ip_last = ip
                while inp[ip] == 0:
                    ip += 1
                    if ip >= n: raise LZOError('input_overrun')
                offset = ip - ip_last
                t += (offset << 8) - offset + 7 + inp[ip]; ip += 1
                nxt = inp[ip] | (inp[ip + 1] << 8)
            ip += 2
            m_pos -= nxt >> 2
            nxt &= 3
            if m_pos == len(out):
                if t != 3: raise LZOError('eof t=%d' % t)
                return bytes(out), ip
            m_pos -= 0x4000
            if m_pos < 0: raise LZOError('lookbehind %d' % m_pos)
        # match copy (允许重叠)
        for k in range(t):
            out.append(out[m_pos + k])
        # match_next
        state = nxt; t = nxt
        if ip + t > n: raise LZOError('input_overrun')
        out += inp[ip:ip + t]; ip += t
        if expect_out is not None and len(out) > expect_out:
            raise LZOError('output_overrun')


def isomappack5_chunks(map_path):
    raw = open(map_path, 'rb').read()
    txt = raw.decode('gbk', errors='replace')
    m = re.search(r'^\[IsoMapPack5\]\s*$', txt, re.M)
    n = re.search(r'^\[', txt[m.end():], re.M)
    sec = txt[m.end():m.end() + (n.start() if n else 400000)]
    lines = [l.strip() for l in sec.splitlines() if l.strip()]
    buf = bytearray()
    for l in lines:
        b = l.split('=', 1)[1]
        buf += base64.b64decode(b + '=' * (-len(b) % 4))
    ipk = bytes(buf)
    chunks = []
    p = 0
    while p + 4 <= len(ipk):
        i_s, o_s = struct.unpack_from('<HH', ipk, p); p += 4
        if i_s == 0 and o_s == 0:
            break
        chunks.append((ipk[p:p + i_s], o_s))
        p += i_s
    return chunks


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    # 基准 1: IsoMapPack5
    chunks = isomappack5_chunks(r'E:\YURI\all01umd.map')
    print('IsoMapPack5 共 %d 块' % len(chunks))
    okc = 0
    for k, (data, o_s) in enumerate(chunks[:12]):
        try:
            out, consumed = lzo1x_decompress(data)
            good = (len(out) == o_s) and (consumed == len(data))
            print('  块%-3d in=%-6d out=%-6d/%-6d consumed=%-6d/%-6d %s  head=%s' %
                  (k, len(data), len(out), o_s, consumed, len(data),
                   'OK' if good else 'SIZE MISMATCH', out[:12].hex(' ')))
            okc += good
        except LZOError as e:
            print('  块%-3d FAIL: %s' % (k, e))
    print('基准1 结果: %d/%d OK' % (okc, min(12, len(chunks))))

    # 基准 2: 存档 64KB 块
    sys.path.insert(0, '.')
    from cfb import CFB
    cc = CFB(open(r'C:\Users\zhao5\Desktop\T1.SAV', 'rb').read()).open('CONTENTS')
    pos = 0
    okc = 0
    for k in range(6):
        packed, unpacked = struct.unpack_from('<II', cc, pos)
        data = cc[pos + 8:pos + 8 + packed]
        try:
            out, consumed = lzo1x_decompress(data)
            good = (len(out) == unpacked) and (consumed == packed)
            print('存档块%-3d packed=%-6d unpacked=%-6d -> out=%-6d consumed=%-6d %s  head=%s' %
                  (k, packed, unpacked, len(out), consumed, 'OK' if good else 'SIZE MISMATCH', out[:16].hex(' ')))
            okc += good
        except LZOError as e:
            print('存档块%-3d FAIL: %s' % (k, e))
        pos += 8 + packed
    print('基准2 结果: %d/6 OK' % okc)
