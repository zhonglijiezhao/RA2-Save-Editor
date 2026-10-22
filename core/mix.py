# -*- coding: utf-8 -*-
"""极简 MIX 读取（只做"从归档里取出一个文件"，不枚举、不解密到磁盘）。

红警2 / 尤里的复仇的 MIX 头（**未加密**版本）：
    u32 flags          （低 16 位历史上当过数量；`flags & 0x20000` = 加密）
    u16 count
    u32 data_size
    [count × (u32 id, u32 offset, u32 size)]   ← offset 相对 data_start
    data_start = 10 + count*12

文件名不在表里，只有 **CRC32(大写文件名)**：
    rulesmd.ini → 0x1F674573   artmd.ini → 0xAF902833
但中文 mod 常把 ID 改成别的值（本机 `expandmd67.mix` 里 rulesmd.ini 的 ID 是 0x8218F9F4，
crash 一样能读，只要按"内容特征"匹配）。所以本模块提供两种找法：
  ① `find_by_id()`  —— 已知 ID 直接取；
  ② `find_by_tags()` —— 按内容里的段名（`[InfantryTypes]` 等）认，给改过 ID 的 mod 兜底。

加密 MIX（flags&0x20000）：**已实现，零第三方依赖**（2026-10-21）
  布局 = [u32 flags][80B key source][Blowfish/ECB 加密的索引头 6+count*12，向上对齐 8]
  · key source 用 Westwood 公开模数（RSA, e=0x10001）逐 40 字节解，取前 56 字节作 Blowfish 密钥；
  · 索引头两种字节序都试（Westwood 自定义的"小端字序"与标准 ECB），能解析出合理条目即采用。
  算法与本机现成脚本 `红警MOD制作素材工具与教程\\mix解密\\mix_unleasher.py` 一致；
  这里改用纯 Python 实现（RSA 用 pow，Blowfish 用 core/blowfish_tables.py 的标准常量），
  这样 GUI 用的 D://py 环境（没有 pycryptodome）也能解。
"""
import struct
import zlib


def name_id(name):
    """RA2/TS 的文件名哈希 = CRC32(大写文件名)。"""
    return zlib.crc32(name.upper().encode('latin-1', 'ignore')) & 0xFFFFFFFF


def entry_bytes(mix, ent):
    """取某条目的字节（ent = (id, off, size)）。"""
    _fid, off, sz = ent
    a = mix['data_start'] + off
    return mix['data'][a:a + sz]


def find_by_id(mix, fid):
    """按文件名 CRC 找条目 → (id, off, size)；找不到 None。"""
    for e in mix['entries']:
        if e[0] == fid:
            return e
    return None


def find_by_tags(mix, tags, min_size=20000):
    """按内容里的段名找条目（给改过 ID 的 mod 兜底）。"""
    for e in mix['entries']:
        if e[2] < min_size:
            continue
        b = entry_bytes(mix, e)
        if all(t in b for t in tags):
            return e
    return None


def parse_mix_bytes(data, path=None):
    """从**内存里的字节**解析 MIX（支持嵌套归档：把 entry_bytes() 的结果再喂进来即可）。"""
    if len(data) < 12:
        return None
    flags = struct.unpack_from('<I', data, 0)[0]
    if flags & 0x00020000:
        dec = _parse_encrypted(data)
        dec['path'] = path
        return dec
    count, dsize = struct.unpack_from('<HI', data, 4)
    need = 6 + count * 12
    if count <= 0 or need > len(data):
        return None
    ds = 4 + need
    entries = [struct.unpack_from('<III', data, 10 + i * 12) for i in range(count)]
    return {'data': data, 'count': count, 'entries': entries, 'data_start': ds,
            'encrypted': False, 'flags': flags, 'datasize': dsize, 'path': path}


def parse_mix(path):
    """→ {'data','count','entries':[(id, off, size)], 'data_start','encrypted', ...}；打不开返回 None。

    加密 MIX 会自动解密索引（解不开时 'error' 里有原因，不抛异常）。
    """
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except Exception:
        return None
    return parse_mix_bytes(data, path)


def parse_mix_prefix(buf, off, size):
    """只读**前缀**就解析出一个归档的索引（不复制整包），用于在套娃里快速探查。

    `buf` 可以是外层归档的 bytes/memoryview；返回的 mix['data'] 是该成员的 memoryview，
    `entry_bytes()` 仍然能用（偏移相对成员起点）。
    """
    try:
        head = bytes(buf[off:off + 16])
    except Exception:
        return None
    if len(head) < 12:
        return None
    flags = struct.unpack_from('<I', head, 0)[0]
    sub = memoryview(buf)[off:off + size]
    if flags & 0x00020000:
        if size < 84 + 8:
            return None
        key = rsa_key_from_source(bytes(sub[4:84]))
        for le in (False, True):
            try:
                first = bf_decrypt_ecb(bytes(sub[84:92]), key, le)
                n = struct.unpack_from('<H', first, 0)[0]
                if not (0 < n <= MAX_MIX_ENTRIES):
                    raise ValueError
                clear = 6 + n * 12
                enc_len = (clear + 7) // 8 * 8
                if 84 + enc_len > size:
                    raise ValueError
                h = bf_decrypt_ecb(bytes(sub[84:84 + enc_len]), key, le)[:clear]
                dsize = struct.unpack_from('<I', h, 2)[0]
                if dsize > size - (84 + enc_len):
                    raise ValueError
                entries = []
                for i in range(n):
                    fid, o, sz = struct.unpack_from('<III', h, 6 + i * 12)
                    if o == 0xFFFFFFFF and sz == 0xFFFFFFFF:
                        continue
                    entries.append((fid, o, sz))
                return {'data': sub, 'count': n, 'entries': entries,
                        'data_start': 84 + enc_len, 'encrypted': True,
                        'flags': flags, 'datasize': dsize, 'mode': 'std' if not le else 'le'}
            except Exception:
                continue
        return None
    count, dsize = struct.unpack_from('<HI', head, 4)
    need = 4 + 6 + count * 12
    if count <= 0 or need > size:
        return None
    h = bytes(sub[4:need])
    entries = [struct.unpack_from('<III', h, 6 + i * 12) for i in range(count)]
    return {'data': sub, 'count': count, 'entries': entries, 'data_start': need,
            'encrypted': False, 'flags': flags, 'datasize': dsize}


def looks_like_mix(buf, off, size):
    """成员头看起来像 MIX 吗（marker=0 且 flags 只有低位）。"""
    if size < 12:
        return False
    h = bytes(buf[off:off + 4])
    if len(h) < 4:
        return False
    marker, flags = struct.unpack_from('<HH', h, 0)
    return marker == 0 and flags in (0, 1, 2, 3)


def _entry_ok(b, tags, max_size):
    """内容像不像我们要的 INI：体积合适、开头是文本（';' / '['）、段名出现在全文里。

    ★2026-10-21：只查"前 8KB"太严 —— rulesmd.ini 的 `[InfantryTypes]` 往往在几十 KB 之后。
      bytes 的 `in` 是 C 层 memmem，几 MB 也很快，所以改成全文查 + 开头必须是文本。
    """
    if not b or len(b) > max_size:
        return False
    head = bytes(b[:4])
    if not head or head[0] not in (0x3B, 0x5B, 0x0D, 0x0A, 0xEF, 0x20):   # ; [ CR LF BOM space
        return False
    data = bytes(b)
    return all(t in data for t in tags) if isinstance(tags, tuple) else any(t in data for t in tags)


def find_nested(mix, name=None, tags=None, depth=3, max_size=6 * 1024 * 1024, match=None):
    """在 MIX（可套娃）里找目标文件 → (层级路径, 命中所在mix, entry)；找不到 (None, None, None)。

    ★2026-10-21（玩家用 XCC 直接搜到 `ra2md.mix - localmd.mix - artmd.ini` 之后重写）：
      · **Westwood 的 MIX ID 不是标准 CRC32**（XCC 靠自带 `global mix database.dat` 才把 ID
        翻成名字，所以我们按 `name_id()` 找永远是空的）；
      · ⇒ 改成 **内容驱动 + 全子档深度优先**：
          ① 每一层先把"文本成员"按 `tags`（段名）认一遍；
          ② 再把**每个**像 MIX 的成员拆开往下（原来是"遇到第一个 MIX 就钻"，会漏掉后面的
             localmd.mix）；
      · `max_size` + 开头必须是文本（`;` / `[`）⇒ 不会把大 MIX 里出现段名误判成 ini。
    """
    fid = name_id(name) if name else None
    stack = [(mix, [])]
    while stack:
        cur, layer = stack.pop()
        if fid is not None:
            e = find_by_id(cur, fid)
            if e is not None:
                return layer, cur, e
        subs = []
        for e in cur['entries']:
            if e[2] < 16:
                continue
            if looks_like_mix(cur['data'], cur['data_start'] + e[1], e[2]):
                if len(layer) + 1 < depth:
                    sub = parse_mix_prefix(cur['data'], cur['data_start'] + e[1], e[2])
                    if sub and sub['entries']:
                        subs.append((sub, layer + [e[0]]))
                continue
            if e[2] < 1000 or e[2] > max_size:
                continue
            b = entry_bytes(cur, e)
            if match is not None:
                if match(bytes(b)):
                    return layer, cur, e
            elif tags and _entry_ok(b, tags, max_size):
                return layer, cur, e
        stack.extend(subs)          # 深度优先：后面的子档也要试
    return None, None, None


# ---------------- 加密 MIX（flags & 0x20000） ----------------
# 算法与 `红警MOD制作素材工具与教程/mix解密/mix_unleasher.py` 一致，但零第三方依赖。
WESTWOOD_RSA_MODULUS = (
    0x51bcda086d39fce4565160d651713fa2e8aa54fa6682b04aabdd0e6af8b0c1e6d1fb4f3daa437f15
)
WESTWOOD_RSA_EXPONENT = 0x10001
MAX_MIX_ENTRIES = 131072

_bf_cache = {}


def _bf_init(key):
    """按 Blowfish 规范用 key 扩展 P/S（结果缓存）。"""
    if key in _bf_cache:
        return _bf_cache[key]
    from blowfish_tables import P_INIT, S_INIT
    P = list(P_INIT)
    S = list(S_INIT)
    klen = len(key)
    if klen == 0:
        return P, S
    j = 0
    for i in range(18):
        v = 0
        for _ in range(4):
            v = ((v << 8) | key[j % klen]) & 0xFFFFFFFF
            j += 1
        P[i] ^= v

    def enc_block(l, r):
        for i in range(16):
            l ^= P[i]
            # F(l)
            a = (l >> 24) & 0xFF
            b = (l >> 16) & 0xFF
            c = (l >> 8) & 0xFF
            d = l & 0xFF
            r ^= (((S[a] + S[256 + b]) & 0xFFFFFFFF) ^ S[512 + c]) + S[768 + d] & 0xFFFFFFFF
            r &= 0xFFFFFFFF
            l, r = r, l
        l, r = r, l
        r ^= P[16]
        l ^= P[17]
        return l & 0xFFFFFFFF, r & 0xFFFFFFFF

    l = r = 0
    for i in range(0, 18, 2):
        l, r = enc_block(l, r)
        P[i], P[i + 1] = l, r
    for i in range(0, 1024, 2):
        l, r = enc_block(l, r)
        S[i], S[i + 1] = l, r
    _bf_cache[key] = (P, S)
    return P, S


def _bf_decrypt_block(l, r, P, S):
    for i in range(17, 1, -1):
        l ^= P[i]
        a = (l >> 24) & 0xFF
        b = (l >> 16) & 0xFF
        c = (l >> 8) & 0xFF
        d = l & 0xFF
        r ^= (((S[a] + S[256 + b]) & 0xFFFFFFFF) ^ S[512 + c]) + S[768 + d] & 0xFFFFFFFF
        r &= 0xFFFFFFFF
        l, r = r, l
    l, r = r, l
    r ^= P[1]
    l ^= P[0]
    return l & 0xFFFFFFFF, r & 0xFFFFFFFF


def bf_decrypt_ecb(ciphertext, key, westwood_le=True):
    """Blowfish/ECB 解密。

    `westwood_le=True` 走 Westwood 的自定义字序：把每个 8 字节块按 `<II` 取两个小端字，
    拼成大端再交给标准 Blowfish，输出再换回 `<II`（与 mix_unleasher.py 完全一致）。
    """
    if len(ciphertext) % 8:
        raise ValueError('加密 MIX 头长度不是 8 的倍数')
    P, S = _bf_init(key)
    out = bytearray()
    for off in range(0, len(ciphertext), 8):
        block = ciphertext[off:off + 8]
        if westwood_le:
            # ★按 mix_unleasher 的原样：两个 32 位字**按小端读出来当字值**，
            #   直接送进 Blowfish（不要先做字节序重解释 —— 2026-10-21 第一版就错在这，
            #   解出来的 datasize=432MB > 文件 195MB，一眼假）。
            l, r = struct.unpack_from('<II', block)
            dl, dr = _bf_decrypt_block(l, r, P, S)
            out += struct.pack('<II', dl, dr)
        else:
            l, r = struct.unpack('>II', block)
            dl, dr = _bf_decrypt_block(l, r, P, S)
            out += struct.pack('>II', dl, dr)
    return bytes(out)


def rsa_key_from_source(key_source):
    """80 字节 key source → 56 字节 Blowfish 密钥（Westwood 公开模数 RSA 解密）。"""
    if len(key_source) != 80:
        raise ValueError('加密 MIX 的 key source 必须是 80 字节')
    out = bytearray()
    for off in (0, 40):
        v = int.from_bytes(key_source[off:off + 40], 'little')
        plain = pow(v, WESTWOOD_RSA_EXPONENT, WESTWOOD_RSA_MODULUS)
        out += plain.to_bytes(39, 'little')
    return bytes(out[:56])


def _parse_encrypted(data):
    """解出加密 MIX 的索引头 → 与 parse_mix() 同结构；解不开返回带 error 的 dict。

    ★2026-10-21 实测教训（ra2md.mix）：
      · 密钥与解密出来的字节都对，但**两种字节序里只有一个是真的** —— 错的那个也能解出
        "看起来合理"的条目数（22796），必须靠**边界校验**否掉：
        `数据区大小 <= 文件剩余` 且 `每条 offset+size <= 数据区大小`。
        真值：ra2md.mix = 25 条 / datasize≈204MB / mode=standard-ecb。
      · 因此这里**两种模式都试，谁通过校验用谁**（与 mix_unleasher.py 的判定顺序一致）。
    """
    flags = struct.unpack_from('<I', data, 0)[0]
    base = {'data': data, 'count': 0, 'entries': [], 'data_start': 0,
            'encrypted': True, 'flags': flags}
    try:
        key = rsa_key_from_source(data[4:84])
    except Exception as e:
        base['error'] = '解 key source 失败：%s' % e
        return base
    body = 4 + 80
    errs = []
    for le in (False, True):          # ★先试 standard-ecb（实测 raptor：ra2md.mix 就是它）
        tag = 'westwood-le' if le else 'standard-ecb'
        try:
            first = bf_decrypt_ecb(data[body:body + 8], key, le)
            n = struct.unpack_from('<H', first, 0)[0]
            if not (0 < n <= MAX_MIX_ENTRIES):
                raise ValueError('条目数不合理 %d' % n)
            clear = 6 + n * 12
            enc_len = (clear + 7) // 8 * 8
            if body + enc_len > len(data):
                raise ValueError('索引头越界')
            head = bf_decrypt_ecb(data[body:body + enc_len], key, le)[:clear]
            dsize = struct.unpack_from('<I', head, 2)[0]
            dstart = body + enc_len
            if dsize > len(data) - dstart:
                raise ValueError('数据区 %d 字节 > 文件剩余 %d' % (dsize, len(data) - dstart))
            entries = []
            for i in range(n):
                fid, off, sz = struct.unpack_from('<III', head, 6 + i * 12)
                if off == 0xFFFFFFFF and sz == 0xFFFFFFFF:
                    continue                    # 引擎的"空槽"
                if off + sz > dsize:
                    raise ValueError('第 %d 条越界（off=%d size=%d）' % (i, off, sz))
                entries.append((fid, off, sz))
            return {'data': data, 'count': n, 'entries': entries, 'data_start': dstart,
                    'encrypted': True, 'flags': flags, 'datasize': dsize, 'mode': tag}
        except Exception as e:
            errs.append('%s: %s' % (tag, e))
    base['error'] = '索引头解不开（%s）' % '；'.join(errs)
    return base
