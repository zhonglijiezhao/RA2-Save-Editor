# -*- coding: utf-8 -*-
"""地图俯视渲染内核（纯 Python，无第三方依赖）——「地图」页 / 选位面板共用

设计（2026-10-15）：
  · 只用 stdlib：输出 **RGB 原始缓冲**，GUI 侧包成 PPM 交 tk.PhotoImage（GUI 环境无 Pillow），
    独立脚本侧用 Pillow 写 PNG。
  · 地形坐标是**菱形(iso)坐标系**：`[Map] Size=0,0,W,H` 的图，坐标范围是 0..2W / 0..2H
    （实测：200×200 的图地形跑在 x[6..399] y[1..399]）。画布按内容自动外扩。
  · 缺地形记录的格子 = 地图外（不参与高度判定，画暗色）。
"""
import base64
import os
import re
import struct
import sys

SKILL_TERRAIN = r'C:\Users\zhao5\.workbuddy\skills\ra2-terrain-map-and-mapunits\scripts'
if os.path.isdir(SKILL_TERRAIN) and SKILL_TERRAIN not in sys.path:
    sys.path.insert(0, SKILL_TERRAIN)

# ★§272：解析结果缓存（键 = 绝对路径 + 大小 + mtime）—— 同一张图第二次解析直接返回。
#   实测 `parse_map()` 对 714KB 的任务图要 **10.3 秒**（正则扫 [IsoMapPack5] + 逐格解压），
#   而开档时"地图页复位"就是被它卡住的。缓存后第二次开档秒出。
_PARSE_CACHE = {}
_PARSE_CACHE_MAX = 8


def _cache_key(path):
    try:
        st = os.stat(path)
        return (os.path.abspath(path), st.st_size, int(st.st_mtime))
    except Exception:
        return None



def _read_isomap_blob(raw):
    """定位 `[IsoMapPack5]` 段 → base64 解码后的二进制（分块 LZO 流）。

    ★★§272：技能脚本的 `read_section()` **段定位是对的**，但它用 `b64_decode_lenient`
      **逐字符**解码 224KB —— 实测 **8.75 秒**！这才是"切到地图页要等 9 秒"的真凶
      （不是 LZO 解压，解压只用 0.06 秒）。
    · 段格式：`[IsoMapPack5]` 后每行 `序号=base64`；段末是 `\r\n\r\n[下一段]`。
    · 段结束判定沿用技能脚本的口径（**行首 `[` 且下一个字符是字母/下划线**）——
      因为 base64 行里也会出现 `[`，简单 `find(b"\n[")` 会提前截断。
    · 解码用标准库：去掉每行的 `序号=` 与残留的 `=`（当填充丢弃），
      再补 padding 到 4 的倍数。失败才退回技能脚本的容错解码器。
    """
    key = b'[IsoMapPack5]'
    i = raw.find(key)
    if i < 0:
        return None
    p = i + len(key)
    j = -1
    while True:
        q = raw.find(b'\n[', p)
        if q < 0:
            break
        nxt = raw[q + 2:q + 3]
        if nxt.isalpha() or nxt == b'_':
            j = q
            break
        p = q + 1
    seg = raw[i + len(key): j if j > 0 else len(raw)]
    lines = []
    for ln in seg.split(b'\n'):
        ln = ln.strip()
        if not ln or ln.startswith(b';'):
            continue
        if b'=' in ln:
            ln = ln.split(b'=', 1)[1]          # 去掉行首的 "序号="
        ln = ln.strip().replace(b'=', b'')     # 行内残留的 '=' 一律当填充丢弃
        if ln:
            lines.append(ln)
    s = b''.join(lines)
    if not s:
        return b''
    try:
        return base64.b64decode(s + b'=' * ((-len(s)) % 4), validate=False)
    except Exception:
        pass
    try:
        from ra2_pack import b64_decode_lenient
        return b64_decode_lenient(s)
    except Exception:
        return None


def _decode_isomap_fast(raw):
    """用**本项目自己的 LZO（C 后端 lzo_fast.dll）**解 `[IsoMapPack5]` → (tiles, info)。

    ★§272：技能脚本 `ra2_pack.decode_isomap` 用的是**纯 Python** LZO —— 对一张 714KB
      的任务图要 **8.8 秒**（这就是"切到地图页要等 9 秒"的真凶）。同一条流换成 C 后端
      实测 ~0.05 秒，解出来的格子**逐条一致**（42901 格）。
    · 段定位与 base64 解码**复用技能脚本的成熟实现**（`read_section` + 容错 b64：
      段内可能有行内 `=`、段末 base64 拼起来可能不是 4 的倍数 —— 标准库会直接抛异常）；
    · 只把 `lzo_decompress_chunked` 的纯 Python LZO 换成 C 后端，分块格式不变：
      `[u16 in_size][u16 out_size][in_size 字节 LZO1X]` 循环，`(0,0)` 结束；
    · 末块在字节流里被截断是常态 ⇒ 尽力解到哪儿算哪儿（与技能脚本 tolerant 一致）。
    失败返回 `(None, 原因)`，调用方退回技能脚本。
    """
    try:
        import lzo1x
    except Exception as e:
        return None, 'no lzo1x: %s' % e
    blob = _read_isomap_blob(raw)
    if not blob:
        return None, 'no IsoMapPack5 section'
    out = bytearray()
    p, n = 0, len(blob)
    while p + 4 <= n:
        in_size, out_size = struct.unpack_from('<HH', blob, p)
        p += 4
        if in_size == 0 and out_size == 0:
            break
        if p + in_size > n:
            in_size = n - p
        frag = blob[p:p + in_size]
        p += in_size
        try:
            _dec, _used = lzo1x.lzo1x_decompress(frag, out_size)   # 返回 (数据, 消耗字节)
            out += _dec
        except Exception:
            break                     # 末块截断：尽力而为
    if not out:
        return None, 'LZO 没解出内容'
    cnt = (len(out) - 4) // 11
    mv = struct.Struct('<hhiBBB').unpack_from
    tiles = [mv(out, q * 11) for q in range(cnt)]
    return tiles, 'fast b64=%dB decompressed=%dB records=%d' % (len(blob), len(out), cnt)


MINIMAP_CANDIDATES = [
    r'C:\Users\zhao5\Desktop\HANDAMA随机地图生成器\minimap.ini',
    r'C:\Users\zhao5\Desktop\WorkBuddy\红警MOD制作素材工具与教程\随机地图生成器\minimap.ini',
]
ART_CANDIDATES = [r'E:\YURI\artmd.ini', r'D:\233\RA2YR\artmd.ini',
                  r'D:\233\RA2LB\artmd.ini', r'E:\YURI\art.ini']
DEFAULT_FP = (1, 1)

# 调色板
C_VOID = (18, 20, 24)          # 无地形记录（地图外）
C_FLAT = (92, 108, 72)         # 无地形数据时的默认平地
C_OCC = (208, 76, 62)          # 建筑占格
C_EDGE = (24, 24, 24)          # 建筑描边
C_CLIFF = (176, 32, 32)        # 高度差（悬崖/斜坡）
C_GRID5 = (255, 255, 255)
C_GRID10 = (140, 140, 140)
C_LABEL = (255, 255, 255)
C_SEL = (60, 200, 255)         # 选中格


# --------------------------------------------------------------------------- #
def parse_map(path, use_cache=True):
    """→ {'size':(W,H) 声明值, 'theater', 'terrain':{(x,y):(tile,sub,level)},
          'structures':[{owner,type,x,y,tag}], 'has_terrain':bool, 'info', 'name'}

    ★结果按 (路径, 大小, mtime) 缓存；**每次返回浅拷贝** —— 调用方（如地图页）
      会改 `size` 字段，直接返回缓存对象会被改坏。
    """
    _key = _cache_key(path) if use_cache else None
    if _key is not None and _key in _PARSE_CACHE:
        return dict(_PARSE_CACHE[_key])
    raw = open(path, 'rb').read()
    txt = raw.decode('utf-8', 'replace')

    def sec(name):
        m = re.search(r'(?m)^\[%s\](.*?)(?=^\[|\Z)' % re.escape(name), txt, re.S)
        return m.group(1) if m else ''

    size = (80, 80)
    m = re.search(r'(?m)^Size=(\d+),(\d+),(\d+),(\d+)', sec('Map'))
    if m:
        size = (int(m.group(3)), int(m.group(4)))
    m = re.search(r'(?m)^Theater=(\S+)', sec('Map'))
    theater = (m.group(1).strip().upper() if m else 'TEMPERATE')
    m = re.search(r'(?m)^Name=(.*)$', sec('Basic'))
    name = (m.group(1).strip() if m else '')

    terrain = {}
    # ★先走 C 后端快速版（0.1s）；失败才退回技能脚本的纯 Python 版（8.8s）
    tiles, info = _decode_isomap_fast(raw)
    if tiles is None:
        try:
            from ra2_pack import decode_isomap
            tiles, info = decode_isomap(raw)
        except Exception as e:
            tiles, info = None, 'decode 失败：%s' % e
    try:
        for x, y, idx, sub, level, ice in (tiles or []):
            terrain[(x, y)] = (idx, sub, level)
    except Exception as e:                      # 缺工具/无 IsoMapPack5 都不致命
        info = 'decode 失败：%s' % e

    structs = []
    for ln in sec('Structures').splitlines():
        s = ln.strip()
        if '=' not in s:
            continue
        f = [p.strip() for p in s.split('=', 1)[1].split(',')]
        if len(f) > 6:
            try:
                structs.append({'owner': f[0], 'type': f[1],
                                'x': int(f[3]), 'y': int(f[4]), 'tag': f[6]})
            except Exception:
                pass
    _out = {'path': path, 'size': size, 'theater': theater, 'terrain': terrain,
            'has_terrain': bool(terrain), 'structures': structs, 'info': info,
            'name': name}
    if _key is not None:
        if len(_PARSE_CACHE) >= _PARSE_CACHE_MAX:
            try:
                _PARSE_CACHE.pop(next(iter(_PARSE_CACHE)))
            except Exception:
                _PARSE_CACHE.clear()
        _PARSE_CACHE[_key] = _out
        return dict(_out)
    return _out


def load_theatre(theater, path=None):
    """读 minimap.ini 的 [THEATER] 段 → {tile_index: (r,g,b)}。

    ★ 修 2026-10-16：`pos` 指向的**下一行就是段头** `[SNOW]`，旧写法 `if line.startswith('['): break`
      会在**第一行**（段头）就 break ⇒ 段内一条都读不到（实测：SNOW 返回 0 条目，
      于是所有格子都退回同一默认色，看上去"地形全是同一种绿"）。改为：**跳过段头**、
      遇到**下一个真段头**才结束。
    """
    cands = ([path] if path else []) + MINIMAP_CANDIDATES
    for p in cands:
        if not (p and os.path.exists(p)):
            continue
        raw = open(p, 'rb').read().decode('utf-8', 'replace')
        pos = raw.find('[%s]' % theater.upper())
        if pos < 0:
            continue
        table = {}
        for line in raw[pos:].splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith('['):            # 段头：跳过本段头，遇下一段头则结束
                if table:
                    break
                continue
            if '=' not in line:
                continue
            k, v = line.split('=', 1)
            if not k.strip().isdigit():
                continue
            nums = [int(t) for t in re.findall(r'\d+', v)]
            # minimap.ini 是 (暗色 r,g,b + 亮色 r,g,b) 两套，取均值；全 0 的条目以 (0,0,0) 记录
            if len(nums) >= 6:
                table[int(k.strip())] = tuple(
                    (nums[i] + nums[i + 3]) // 2 for i in range(3))
            elif len(nums) >= 3:
                table[int(k.strip())] = tuple(nums[:3])
        if table:
            return table, p
    return {}, None


def load_foundations(paths=None):
    """art(md).ini 的 [TYPE] Foundation= → {TYPE: (w,h)}"""
    out = {}
    for p in (paths or ART_CANDIDATES):
        if not os.path.exists(p):
            continue
        raw = open(p, 'rb').read()
        try:
            txt = raw.decode('utf-8')
        except Exception:
            txt = raw.decode('gbk', 'replace')
        cur = None
        for line in txt.splitlines():
            s = line.strip()
            if s.startswith('[') and s.endswith(']'):
                cur = s[1:-1].strip().upper()
                continue
            if cur and s.lower().startswith('foundation='):
                m = re.match(r'(\d+)\s*x\s*(\d+)', s.split('=', 1)[1].strip().lower())
                if m:
                    out[cur] = (int(m.group(1)), int(m.group(2)))
        if out:
            break
    return out


RULES_CANDIDATES = [r'E:\YURI\rulesmd.ini', r'D:\233\RA2YR\rulesmd.ini',
                    r'E:\YURI\rules.ini', r'D:\233\RA2LB\rulesmd.ini']


def load_building_type_names(paths=None):
    """rules(md).ini 的 [BuildingTypes] 名单 → {大写名}（用于把"建筑类型"从全部 TypeClass 里挑出来）"""
    out = set()
    for p in (paths or RULES_CANDIDATES):
        if not os.path.exists(p):
            continue
        raw = open(p, 'rb').read()
        try:
            txt = raw.decode('utf-8')
        except Exception:
            txt = raw.decode('gbk', 'replace')
        m = re.search(r'(?m)^\[BuildingTypes\](.*?)(?=^\[|\Z)', txt, re.S)
        if m:
            for v in m.group(1).split(','):
                v = v.strip()
                if v and not v.isdigit():
                    out.add(v.upper())
        if out:
            break
    return out


# --------------------------------------------------------------------------- #
def occupancy(blds):
    occ = {}
    for b in blds:
        w, h = b.get('fp') or DEFAULT_FP
        for dx in range(w):
            for dy in range(h):
                occ[(b['x'] + dx, b['y'] + dy)] = b
    return occ


def canvas_size(mp, blds, pad=2):
    """画布格子数：声明尺寸与内容取大（★声明 Size 常小于实际坐标）"""
    w, h = mp['size']
    if blds:
        w = max(w, max(b['x'] + (b.get('fp') or DEFAULT_FP)[0] for b in blds) + pad)
        h = max(h, max(b['y'] + (b.get('fp') or DEFAULT_FP)[1] for b in blds) + pad)
    if mp['terrain']:
        w = max(w, max(k[0] for k in mp['terrain']) + pad)
        h = max(h, max(k[1] for k in mp['terrain']) + pad)
    return w, h


def render_rgb(mp, blds, scale=6, theatre_ini=None, sel=None, show_label=True,
               grid=True):
    """→ (W_cells, H_cells, bytearray RGB, 元信息 dict)

    scale: 每格像素。返回的缓冲可直接包成 PPM（GUI）或写 PNG（脚本）。
    """
    W, H = canvas_size(mp, blds)
    tw, th = W * scale, H * scale
    buf = bytearray(C_VOID * (tw * th))
    table, tpath = load_theatre(mp['theater'], theatre_ini)
    flat = table.get(0, C_FLAT)

    def put(x, y, c):
        if 0 <= x < tw and 0 <= y < th:
            o = (y * tw + x) * 3
            buf[o] = c[0]
            buf[o + 1] = c[1]
            buf[o + 2] = c[2]

    def rect(x0, y0, x1, y1, c):
        for yy in range(max(0, y0), min(th, y1)):
            base = (yy * tw) * 3
            for xx in range(max(0, x0), min(tw, x1)):
                o = base + xx * 3
                buf[o] = c[0]
                buf[o + 1] = c[1]
                buf[o + 2] = c[2]

    # ① 地形
    terrain = mp['terrain']
    if terrain:
        for (x, y), (idx, sub, level) in terrain.items():
            c = table.get(idx, flat)
            if level:                       # 高层略亮、低层略暗，便于看台地
                k = max(-40, min(40, level * 6))
                c = (max(0, min(255, c[0] + k)), max(0, min(255, c[1] + k)),
                     max(0, min(255, c[2] + k)))
            rect(x * scale, y * scale, (x + 1) * scale, (y + 1) * scale, c)
    else:
        rect(0, 0, tw, th, flat)

    # ② 高度差描边（悬崖/斜坡）
    if terrain:
        lv = lambda x, y: terrain.get((x, y), (0, 0, 0))[2]
        for y in range(H):
            for x in range(W):
                if (x, y) not in terrain:
                    continue
                a = lv(x, y)
                if lv(x + 1, y) != a:
                    rect((x + 1) * scale - 1, y * scale, (x + 1) * scale,
                         (y + 1) * scale, C_CLIFF)
                if lv(x, y + 1) != a:
                    rect(x * scale, (y + 1) * scale - 1, (x + 1) * scale,
                         (y + 1) * scale, C_CLIFF)

    # ③ 建筑足迹
    for b in blds:
        w, h = b.get('fp') or DEFAULT_FP
        rect(b['x'] * scale, b['y'] * scale,
             (b['x'] + w) * scale, (b['y'] + h) * scale, C_EDGE)
        rect(b['x'] * scale + 1, b['y'] * scale + 1,
             (b['x'] + w) * scale - 1, (b['y'] + h) * scale - 1, C_OCC)

    # ④ 网格
    if grid:
        for x in range(0, W + 1):
            if x % 5 == 0:
                c = C_GRID10 if x % 10 == 0 else C_GRID5
                rect(x * scale, 0, x * scale + 1, th, c)
        for y in range(0, H + 1):
            if y % 5 == 0:
                c = C_GRID10 if y % 10 == 0 else C_GRID5
                rect(0, y * scale, tw, y * scale + 1, c)

    # ⑤ 选中格
    if sel:
        sx, sy = sel
        rect(sx * scale, sy * scale, (sx + 1) * scale, (sy + 1) * scale, C_SEL)
        rect(sx * scale + 1, sy * scale + 1, (sx + 1) * scale - 1,
             (sy + 1) * scale - 1, (30, 120, 200))

    return W, H, buf, {'scale': scale, 'theatre_ini': tpath, 'px': (tw, th),
                       'occupancy': occupancy(blds)}


def to_png(w, h, buf):
    """纯 Python 生成 PNG（GUI 环境无 Pillow；tk 8.6 认 PNG）"""
    import struct
    import zlib
    stride = w * 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)                       # 每行 filter=0
        raw += buf[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(bytes(raw), 6)) + chunk(b'IEND', b''))


def check_cell(mp, blds, cx, cy, fp=DEFAULT_FP):
    """落位判定：越界/占用/场地跨高度/紧邻不同高度"""
    W, H = canvas_size(mp, blds)
    terrain = mp['terrain']
    occ = occupancy(blds)
    w, h = fp
    msgs = []
    if not (0 <= cx and cx + w - 1 < W and 0 <= cy and cy + h - 1 < H):
        msgs.append('✗ 越出可放范围 [%d×%d]' % (W, H))
    hit = [p for p in ((x, y) for x in range(cx, cx + w) for y in range(cy, cy + h))
           if p in occ]
    if hit:
        names = sorted({occ[p]['type'] for p in hit})
        msgs.append('✗ 与 %d 个已占格重叠（%s）' % (len(hit), '/'.join(names[:4])))
    if terrain:
        lvs = {terrain.get((x, y), (0, 0, 0))[2]
               for x in range(cx, cx + w) for y in range(cy, cy + h)}
        if len(lvs) > 1:
            msgs.append('⚠ 场地跨 %d 个高度层 %s（斜坡/悬崖边）' % (len(lvs), sorted(lvs)))
        else:
            msgs.append('✓ 场地同高度 level=%s' % lvs.pop())
        base = terrain.get((cx, cy), (0, 0, 0))[2]
        near = [(x, y) for x in range(cx - 1, cx + w + 1)
                for y in range(cy - 1, cy + h + 1)
                if terrain.get((x, y), (0, 0, 0))[2] != base]
        if near:
            msgs.append('⚠ 紧邻 %d 格不同高度（悬崖边）' % len(near))
    else:
        msgs.append('· 该地图文件不含地形数据 ⇒ 只做占用/越界判定')
    return msgs
