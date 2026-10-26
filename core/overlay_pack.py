# -*- coding: utf-8 -*-
"""`[OverlayPack]` / `[OverlayDataPack]` 解码（Westwood LCW / format80）——§283

地图 .MAP / .YRM 里的覆盖物（沙袋/围墙/铁丝网/矿/宝石/桥面/铁轨/石头/箱子…）以
**压缩位图**保存：整张 **512×512** 网格、**索引 = y*512 + x**，`0xFF` = 没有覆盖物。
`[OverlayPack]` 存**类型索引**（进 `[OverlayTypes]` 的行号），`[OverlayDataPack]` 存**帧号**。

★★ 之前两版都卡在"LCW 解不对"，根因是 **cmd4 是 4 字节填充命令**（ModdingWiki
   Westwood LCW 有完整规格，见下），把它当成 3 字节的 cmd3 就整条流错位。实测：

  · 空块压缩后正好 7 字节 `81 FF FE FF 1F FF 80`
      `81 FF`            cmd1 短复制：1 个字面量 0xFF
      `FE FF 1F FF`      cmd4 重复：0xFF 写 0x1FFF = 8191 次
      `80`               结束        ⇒ 1+8191 = 8192 ✓
  · 有矿的块 `81 FF FE 4C 0E FF 83 70 70 70 FE FD 01 FF 02 00 FF FD 01 4F 0E FF 00 02 4D 10 FF 01 02 4C 0E FE B2 09 FF 80`
      = 1 + 3660 + 3 + 509 + 3 + 509 + 512 + 513 + 2482 = 8192 ✓（36 字节正好吃完）

命令表（`.` 为位，`__` 为整字节）：
  cmd1 `10xxxxxx`          1B   字面量：照抄 `xxxxxx` 个字节；`0x80`(count 0) = 结束
  cmd2 `0cccpppp __`       2B   已有块**相对**复制：count = (c…)+3，
                                **offset = ((b & 0x0F) << 8) | 下一字节**（12 位）
                                —— 不是"下一字节单独当 offset"，这是第二个坑
  cmd3 `11cccccc __ __`    3B   count = c+3，Pos = u16（相对/绝对取决于流首字节是否为 0）
  cmd4 `0xFE __ __ __`     4B   **重复**：把最后一字节写 u16 次（← 之前漏掉的就是它）
  cmd5 `0xFF __ __ __ __`  5B   u16 count + u16 Pos
复制允许**重叠**（`offset == 1` 就是 RLE 重复上一字节）。
"""
import base64
import struct

OVERLAY_GRID = 512                 # 512×512，索引 = y*512 + x
OVERLAY_PACK_SIZE = OVERLAY_GRID * OVERLAY_GRID      # 262144
OVERLAY_EMPTY = 0xFF               # 网格里的"无覆盖物"
BLOCK_SIZE = 8192                  # 每块解出 8192 字节（512×16 行），共 32 块


def lcw_decode(src, size=None):
    """LCW(format80) 解压。`size` 给了就在解够 size 字节时停（块内定长）。"""
    out = bytearray()
    n = len(src)
    i = 0
    rel = n > 0 and src[0] == 0        # 首字节 0 = 后面的 cmd3/cmd5 用相对偏移
    if rel:
        i = 1
    while i < n and (size is None or len(out) < size):
        b = src[i]
        i += 1
        if b & 0x80:
            if b & 0x40:
                if b == 0xFE:                     # cmd4 重复
                    if i + 3 > n:
                        break
                    cnt = src[i] | (src[i + 1] << 8)
                    val = src[i + 2]
                    i += 3
                    out += bytes((val,)) * cnt
                elif b == 0xFF:                   # cmd5 长复制
                    if i + 4 > n:
                        break
                    cnt = src[i] | (src[i + 1] << 8)
                    pos = src[i + 2] | (src[i + 3] << 8)
                    i += 4
                    st = (len(out) - pos) if rel else pos
                    for k in range(cnt):
                        out.append(out[st + k])
                else:                             # cmd3 中复制
                    if i + 2 > n:
                        break
                    cnt = (b & 0x3F) + 3
                    pos = src[i] | (src[i + 1] << 8)
                    i += 2
                    st = (len(out) - pos) if rel else pos
                    for k in range(cnt):
                        out.append(out[st + k])
            else:                                 # cmd1 字面量
                cnt = b & 0x3F
                if cnt == 0:
                    break
                out += src[i:i + cnt]
                i += cnt
        else:                                     # cmd2 相对短复制
            if i >= n:
                break
            cnt = (b >> 4) + 3
            pos = ((b & 0x0F) << 8) | src[i]
            i += 1
            st = len(out) - pos
            for k in range(cnt):
                out.append(out[st + k] if st + k >= 0 else 0)
    return bytes(out)


def pack_blocks(raw):
    """把解 base64 后的整段切成块：`[u16 压缩长][u16 解出长] + 数据`。"""
    out = []
    p = 0
    while p + 4 <= len(raw):
        comp, unc = struct.unpack_from('<HH', raw, p)
        out.append((raw[p + 4:p + 4 + comp], unc))
        p += 4 + comp
    return out


def decode_pack(raw):
    """整段 → bytes（各块拼接；块定长 8192，标准地图拼出来是 262144）。"""
    buf = bytearray()
    for data, unc in pack_blocks(raw):
        buf += lcw_decode(data, unc if unc else BLOCK_SIZE)
    return bytes(buf)


def ini_values(text, section):
    """把 `[section]` 段里 `N=值` 的**值按 N 顺序**拼起来（RA2 把长 base64 拆成 1=/2=/3=…）。"""
    low = text.lower()
    key = '[' + section.lower() + ']'
    i = low.find(key)
    if i < 0:
        return None
    j = text.find('\n[', i + 1)
    blk = text[i:j if j > 0 else len(text)]
    vals = []
    for line in blk.replace('\r', '').split('\n')[1:]:
        if '=' not in line:
            continue
        k, v = line.split('=', 1)
        k = k.strip()
        if not k.isdigit():
            continue
        vals.append((int(k), v.strip()))
    if not vals:
        return ''
    vals.sort()
    return ''.join(v for _, v in vals)


def read_map_packs(map_path):
    """读地图 → `(types, frames)` 两个 262144 字节网格；没有该段返回 `(None, None)`。"""
    try:
        with open(map_path, 'rb') as f:
            text = f.read().decode('latin-1')
    except Exception:
        return None, None
    types = frames = None
    for sec, slot in (('OverlayPack', 't'), ('OverlayDataPack', 'f')):
        s = ini_values(text, sec)
        if not s:
            continue
        s2 = ''.join(s.split())
        try:
            raw = base64.b64decode(s2 + '=' * (-len(s2) % 4))
            grid = decode_pack(raw)
        except Exception:
            continue
        if len(grid) < OVERLAY_PACK_SIZE:
            grid = grid + bytes(OVERLAY_PACK_SIZE - len(grid))
        if slot == 't':
            types = grid
        else:
            frames = grid
    return types, frames


def map_overlay_at(types, cx, cy):
    """地图网格取值 → 索引 / None（空）。"""
    if not types or not (0 <= cx < OVERLAY_GRID and 0 <= cy < OVERLAY_GRID):
        return None
    v = types[cy * OVERLAY_GRID + cx]
    return None if v == OVERLAY_EMPTY else v
