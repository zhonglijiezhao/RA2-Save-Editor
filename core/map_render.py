# -*- coding: utf-8 -*-
"""地图俯视渲染内核（纯 Python，无第三方依赖）——「地图」页 / 选位面板共用

设计（2026-10-15）：
  · 只用 stdlib：输出 **RGB 原始缓冲**，GUI 侧包成 PPM 交 tk.PhotoImage（GUI 环境无 Pillow），
    独立脚本侧用 Pillow 写 PNG。
  · 地形坐标是**菱形(iso)坐标系**：`[Map] Size=0,0,W,H` 的图，坐标范围是 0..2W / 0..2H
    （实测：200×200 的图地形跑在 x[6..399] y[1..399]）。画布按内容自动外扩。
  · 缺地形记录的格子 = 地图外（不参与高度判定，画暗色）。
"""
# ★2026-11-06（审核 §3.3）：静默异常统一记账（绝不抛、默认只记内存）。
#   本文件里原来的 `except ...: pass` 已换成 `_swallow()` —— 行为完全不变，
#   但被吞掉的东西会进 diag 缓冲区，随错误日志/退出时落盘，排障不再靠猜。
try:
    from diag import swallow as _swallow
except Exception:                    # diag 不可达（极端情况）⇒ 退化成原来的 pass
    try:
        from core.diag import swallow as _swallow
    except Exception:
        def _swallow(*a, **k):
            pass
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
        _swallow()
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
                _swallow()
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
               grid=True, xscale=1):
    """→ (W_cells, H_cells, bytearray RGB, 元信息 dict)

    scale: 每格**高**像素；`xscale`: 每格宽 = scale × xscale（默认 1 = 正方形格）。

    ★★2026-11-07（玩家：「地编中的缩略图」作参照）：RA2 的 cell 在屏幕上本来就是 2:1
      的扁格（tile 60×30）。俯视展开时按 `xscale=2` 画，才和地编 minimap 一个比例。
    """
    xscale = max(1, int(xscale or 1))
    W, H = canvas_size(mp, blds)
    tw, th = W * scale * xscale, H * scale
    buf = bytearray(C_VOID * (tw * th))
    table, tpath = load_theatre(mp['theater'], theatre_ini)
    flat = table.get(0, C_FLAT)

    def put(x, y, c):
        if 0 <= x < tw and 0 <= y < th:
            o = (y * tw + x) * 3
            buf[o] = c[0]
            buf[o + 1] = c[1]
            buf[o + 2] = c[2]

    tw3 = tw * 3

    def SX(v):
        """格坐标×scale → 像素 x（再乘横向倍率）。"""
        return v * xscale

    def rect(x0, y0, x1, y1, c):
        """填一块矩形。

        ★★2026-11-07（玩家：「勾单位后跳 20 倍，卡一会儿」）：原来是**逐像素** Python 循环，
          20 倍下 7880×7840 = 6180 万像素 ⇒ 单是地形就要 4.1 秒。改成"一行 bytes 造一次 +
          按行切片赋值"，同样内容走的是 C 层 memcpy（实测 20 倍 4.1s → 0.35s，快约 12 倍）。
        """
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(tw, x1)
        y1 = min(th, y1)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(c) * (x1 - x0)
        o0 = x0 * 3
        o1 = x1 * 3
        for yy in range(y0, y1):
            base = yy * tw3
            buf[base + o0: base + o1] = row

    # ① 地形
    terrain = mp['terrain']
    if terrain:
        for (x, y), (idx, sub, level) in terrain.items():
            c = table.get(idx, flat)
            if level:                       # 高层略亮、低层略暗，便于看台地
                k = max(-40, min(40, level * 6))
                c = (max(0, min(255, c[0] + k)), max(0, min(255, c[1] + k)),
                     max(0, min(255, c[2] + k)))
            rect(SX(x * scale), y * scale, SX((x + 1) * scale), (y + 1) * scale, c)
    else:
        rect(0, 0, tw, th, flat)

    # ② 高度差描边（悬崖/斜坡）
    if terrain:
        # ★2026-11-07：原写法遍历 W×H 的**全部**格（15.4 万次哈希）只为找 4.3 万条地形；
        #   改成直接遍历地形字典，省掉"空格空跑"这一层（结果完全一致：缺格按 level 0 处理）。
        for (x, y), (_ti, _ts, a) in terrain.items():
            nb = terrain.get((x + 1, y))
            if (nb[2] if nb else 0) != a:
                rect(SX((x + 1) * scale) - 1, y * scale, SX((x + 1) * scale),
                     (y + 1) * scale, C_CLIFF)
            nb = terrain.get((x, y + 1))
            if (nb[2] if nb else 0) != a:
                rect(SX(x * scale), (y + 1) * scale - 1, SX((x + 1) * scale),
                     (y + 1) * scale, C_CLIFF)

    # ③ 建筑足迹
    for b in blds:
        w, h = b.get('fp') or DEFAULT_FP
        rect(SX(b['x'] * scale), b['y'] * scale,
             SX((b['x'] + w) * scale), (b['y'] + h) * scale, C_EDGE)
        rect(SX(b['x'] * scale) + 1, b['y'] * scale + 1,
             SX((b['x'] + w) * scale) - 1, (b['y'] + h) * scale - 1, C_OCC)

    # ④ 网格
    if grid:
        for x in range(0, W + 1):
            if x % 5 == 0:
                c = C_GRID10 if x % 10 == 0 else C_GRID5
                rect(SX(x * scale), 0, SX(x * scale) + 1, th, c)
        for y in range(0, H + 1):
            if y % 5 == 0:
                c = C_GRID10 if y % 10 == 0 else C_GRID5
                rect(0, y * scale, tw, y * scale + 1, c)

    # ⑤ 选中格
    if sel:
        sx, sy = sel
        rect(SX(sx * scale), sy * scale, SX((sx + 1) * scale), (sy + 1) * scale, C_SEL)
        rect(SX(sx * scale) + 1, sy * scale + 1, SX((sx + 1) * scale) - 1,
             (sy + 1) * scale - 1, (30, 120, 200))

    return W, H, buf, {'scale': scale, 'xscale': xscale, 'theatre_ini': tpath, 'px': (tw, th),
                       'occupancy': occupancy(blds)}


def to_png(w, h, buf, level=6):
    """纯 Python 生成 PNG（GUI 环境无 Pillow；tk 8.6 认 PNG）

    ★2026-11-07：原来逐行 `raw += 切片`（每次 realloc），20 倍下 7840 行要 1.1 秒。
      改成"一次性预分配 + 按行切片赋值"，再把 zlib 压缩级别做成参数：
      level=1 时 20 倍 1.10s → 0.25s（体积 1.2MB → 3.6MB，Tk 解码同样更快）。
    """
    import struct
    import zlib
    stride = w * 3
    step = stride + 1
    raw = bytearray(h * step)
    for y in range(h):
        o = y * step
        # 每行 filter=0（raw[o] 预分配时已是 0）
        raw[o + 1:o + 1 + stride] = buf[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(bytes(raw), level)) + chunk(b'IEND', b''))


def rot_map(mp, blds, size=None, kind=1):
    """把地图数据旋转 90° 的整数倍（玩家 2026-11-07：「地图能不能向左旋转 90 度 更符合观感」）。

    存档地形是**等距坐标**（0..2W × 0..2H 的菱形空间），按直角坐标画出来就是一块
    斜 45° 的菱形。旋转只改变"画到哪"，数据含义不变：

        kind=1 左旋 90°（逆时针）：(x,y) → (y, W0-1-x)        新宽高 = H0, W0
        kind=2 180°              ：(x,y) → (W0-1-x, H0-1-y)   新宽高 = W0, H0
        kind=3 右旋 90°（顺时针）：(x,y) → (H0-1-y, x)        新宽高 = H0, W0

    建筑足迹跟着转（矩形转 90° 仍是轴对齐矩形，w/h 互换）。
    返回 `(新 mp, 新 blds, (W1, H1))`；输入不被修改。
    """
    W0, H0 = size or canvas_size(mp, blds)
    W0, H0 = int(W0), int(H0)
    kind = int(kind or 0) % 4
    if kind == 0:
        return mp, blds, (W0, H0)

    def _pt(x, y):
        x, y = int(x), int(y)
        if kind == 1:
            return y, W0 - 1 - x
        if kind == 2:
            return W0 - 1 - x, H0 - 1 - y
        return H0 - 1 - y, x

    tr = {}
    for (x, y), v in (mp.get('terrain') or {}).items():
        tr[_pt(x, y)] = v
    nb = []
    for b in (blds or []):
        w, h = b.get('fp') or DEFAULT_FP
        d = dict(b)
        # 左上角 + 右下角都转 ⇒ 新左上角就是两个像的最小值
        ax, ay = _pt(b['x'], b['y'])
        bx, by = _pt(int(b['x']) + int(w) - 1, int(b['y']) + int(h) - 1)
        d['x'] = min(ax, bx)
        d['y'] = min(ay, by)
        d['fp'] = (h, w) if kind in (1, 3) else (w, h)
        nb.append(d)
    out = dict(mp)
    out['terrain'] = tr
    out['size'] = (H0, W0) if kind in (1, 3) else (W0, H0)
    return out, nb, out['size']


def rot_left90_map(mp, blds, size=None):
    """`rot_map(kind=1)` 的别名（左旋 90°，最早的一个入口）。"""
    return rot_map(mp, blds, size, kind=1)


def ortho_map(mp, blds=None):
    """等距（菱形）坐标 → **正交俯视**格 —— 把菱形"展开"成矩形（玩家 2026-11-07）。

    存档地形是等距坐标：屏幕上是个斜 45° 的菱形。真正的俯视展开是**等距逆变换**：

        u = x - y ;  v = x + y
        i = (u - u0) // 2        j = (v - v0) // 2      （u0 = min(u), v0 = min(v)）

    ★本档实测（ALL01UMD）：展开后是 **200×200**，**正好等于地图 `[Map] Size`** ——
      这就是"地图本体"的正交形状，也就是玩家截图里那个矩形。
    ★x+y 的奇偶各占一半 ⇒ 每两个 iso 点并进一个正交格（约 1.94:1），保留先到的那个。
      反过来"正交 → iso"用 `cells` 反查表最准（点击选格走它，避免取整偏一格）。

    返回 `(mp2, blds2, (Wi, Hj), info)`，`info = {'s0','d0','cells':{(i,j):(x,y)}}`。
    建筑足迹转 45° 后无法再用轴对齐矩形表示 ⇒ 用**包围盒**近似（位置准、形状方块化），
    落位判定仍走原始的 `check_cell()`（那是 iso 坐标，不受显示影响）。
    """
    terr = mp.get('terrain') or {}
    if not terr:
        return mp, blds, tuple(mp.get('size') or (0, 0)), {'s0': 0, 'd0': 0, 'cells': {}}
    # ★★2026-11-07 修正（玩家：「地图本身是不缺角的」「路径点 256 应该在左下角」）：
    #   正确的等距逆变换是 **u = x - y, v = x + y**（不是 x+y / y-x）：
    #     · 菱形四个**尖角**正好落到矩形四个**角**上 ⇒ 展开后四角饱满，不缺角；
    #     · 路径点 256 = iso(197,391) ⇒ 正交 (2,193) = **左下角**，与地编一致。
    #   旧写法 (x+y, y-x) 会把"四边中点"放到四角 ⇒ 四角各缺一块（玩家截图里的缺口）。
    s0 = min(x - y for (x, y) in terr)
    d0 = min(x + y for (x, y) in terr)
    # 每两个 iso 点并进一个正交格（x+y 奇偶各一）⇒ **优先取"格中心"那一个**
    # （x、y 都是偶数的点），否则先到先得 —— 否则随机取半个，同一格会闪不同 tile。
    out_tr = {}
    cells = {}
    center_done = set()
    for (x, y), v in terr.items():
        k = ((int(x) - int(y) - s0) // 2, (int(x) + int(y) - d0) // 2)
        is_center = (int(x) % 2 == 0 and int(y) % 2 == 0)
        if k not in out_tr or (is_center and k not in center_done):
            out_tr[k] = v
            if is_center:
                cells[k] = (int(x), int(y))
                center_done.add(k)
            else:
                cells.setdefault(k, (int(x), int(y)))
    Wi = max(k[0] for k in cells) + 1
    Hj = max(k[1] for k in cells) + 1
    # ★2026-11-07（玩家：「路径点 256 落到黑色区域」）：展开后边界处**没有 tile 的格**
    #   会留成底色（深黑），看着像地图缺角。这里用邻格颜色做 1~2 轮"膨胀"填掉，
    #   只填画布内的空格，不改变尺寸、不动已有地形（纯显示层）。
    for _ in range(2):
        add = {}
        for (i, j), v in out_tr.items():
            for nb in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
                if (0 <= nb[0] < Wi and 0 <= nb[1] < Hj
                        and nb not in out_tr and nb not in add):
                    add[nb] = v
        if not add:
            break
        out_tr.update(add)

    def _ij(x, y):
        return ((int(x) - int(y) - s0) // 2, (int(x) + int(y) - d0) // 2)

    nb = []
    for b in (blds or []):
        w, h = b.get('fp') or DEFAULT_FP
        cx = int(b['x']) + (int(w) - 1) / 2.0
        cy = int(b['y']) + (int(h) - 1) / 2.0
        ci, cj = _ij(cx, cy)
        # 45° 后足迹的包围盒边长 ≈ (w + h) / 2
        side = max(1, int(round((int(w) + int(h)) / 2.0)))
        d = dict(b)
        d['x'] = int(ci) - side // 2
        d['y'] = int(cj) - side // 2
        d['fp'] = (side, side)
        nb.append(d)
    out = dict(mp)
    out['terrain'] = out_tr
    out['size'] = (Wi, Hj)
    return out, nb, (Wi, Hj), {'s0': s0, 'd0': d0, 'cells': cells}


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
