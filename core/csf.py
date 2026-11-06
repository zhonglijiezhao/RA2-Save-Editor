# -*- coding: utf-8 -*-
"""ra2md.csf（Westwood CSF 语言文件）读取器 —— 只读，不做写回。

★2026-10-25 §277（玩家给的"百分百正确"取任务名的办法）：
  > 「地图名字是 ALL01UMD，那么 ra2md.csf 中的 all01md 就是任务名字；
  >   如果地图名字是 sov01umd，那么 ra2md.csf 中的 sov01umd 就是任务名字」

规则：地图文件名当作键名去找 CSF（大小写不敏感），命中就是任务名。
      `ALL01UMD.MAP` → 先去掉末尾的 `U` 得 `ALL01MD` → 键 `name:all01md`
      （同一条名字还有 `name:all01mdsav`，是不带「军事行动：」前缀的短名）。

★文件格式实测（`E:\\YURI\\ra2md.csf`，309,457 字节 / 5219 条）：
```
头 24 字节：magic ' FSC' | version=3 | nLabels | nValues | 8 字节 0
每条 label：' LBL' | nPairs(u32) | keyLen(u32) | key[ASCII, 不补 0]
每个 value：' RTS' | len(u32，**字符数**) | 数据（**len×2 字节**）
```
★★值的编码：**逐字节取反的 UTF-16LE**（`b6 ff b1 ff ab ff ad ff b0 ff`
逐字节 NOT 后是 `I N T R O`）。直接按 UTF-16LE 解会得到一堆半角片假名乱码——
第一版就栽在这（解出来 `깤녴瞳굗`）。所以这里**三种解法都试、取"像人话"的那个**：
  ① 逐字节取反 → UTF-16LE（本机文件就是这种）
  ② UTF-16LE 原样
  ③ GBK
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
import os
import struct

MAG_LBL = b' LBL'
MAG_RTS = b' RTS'
HDR_MAGICS = (b'CSF ', b' FSC')
HDR_LEN = 24          # ★实测 label 表从 0x18 开始（不是 16，也不是 20）

_CACHE = {}           # (路径, 大小, mtime) → Csf


def _find_rts(raw, pos):
    """在 `pos` 附近定位一个 value 头 `RTS` → 它的起始下标（找不到 -1）。

    ★2026-11-04 实测（`E:\\YURI\\ra2md.csf`）：标准写法是 `' RTS'`（4 字节，前导空格），
      但这个 mod 的 csf 里 `VOX:*` 那一批 label 的 **key 长度字段少算了 1 字节** ⇒
      key 后面直接跟 `RTS`（前导空格被 key 吃掉了）。还有极少数写成 `'  RTS'`。
      ⇒ 在 `pos-1 .. pos+2` 里找 `RTS`，谁中就用谁；不靠猜偏移量。
    """
    for i in (pos, pos + 1, pos - 1, pos + 2):
        if i < 0:
            continue
        if raw[i:i + 3] == b'RTS':
            return i
    return -1


def _score(text):
    """给候选解码结果打分：汉字/ASCII 可打印 +2，控制字符 −3。"""
    if not text:
        return -1
    good = 0
    for ch in text:
        o = ord(ch)
        if '\u4e00' <= ch <= '\u9fff':
            good += 2
        elif 0x20 <= o < 0x7f:
            good += 2
        elif o in (0x0a, 0x0d):
            good += 1
        elif o < 0x20 or 0xfff0 <= o <= 0xffff or ch == '\ufffd':
            good -= 3
    return good - (0 if text.strip() else 5)


def _decode(raw):
    """value 原始字节 → 文本（三种解法取最优）。"""
    cands = []
    try:
        cands.append(bytes(b ^ 0xFF for b in raw).decode('utf-16le', 'replace'))
    except Exception:
        _swallow()
    try:
        cands.append(raw.decode('utf-16le', 'replace'))
    except Exception:
        _swallow()
    try:
        cands.append(raw.decode('gbk', 'replace'))
    except Exception:
        _swallow()
    if not cands:
        return ''
    return max(cands, key=_score).strip('\x00').strip()


class Csf(object):
    """一个 ra2md.csf。键大小写不敏感。"""

    def __init__(self, path=None, raw=None):
        """`path` = 磁盘上的 csf；`raw` = **内存里的字节**（从 MIX 里解出来的）。

        ★2026-11-04：`mix_vfs` 从加密 MIX 里取出 ra2md.csf 后直接喂字节，不落盘。
        """
        self.path = path
        self._raw = raw
        self.keys = {}          # 小写键 → 文本（多条 value 取第一条）
        self.error = None
        self.count = 0
        self._load()

    def _load(self):
        try:
            if self._raw is not None:
                raw = self._raw
            else:
                with open(self.path, 'rb') as f:
                    raw = f.read()
        except Exception as e:
            self.error = '读不到：%s' % e
            return
        if raw[:4] not in HDR_MAGICS:
            self.error = '不是 CSF 文件（头 %r）' % raw[:4]
            return
        try:
            _ver, nlab, _nval = struct.unpack_from('<III', raw, 4)
        except Exception as e:
            self.error = '头部解析失败：%s' % e
            return
        self._parse(raw, nlab)

    def _parse(self, raw, nlab):
        """容错扫描：label 头/值头对不上时**重同步到下一个 ` LBL`**，不整体放弃。

        ★2026-11-04 实测（`E:\\YURI\\ra2md.csf`，5211 个 label）：第 1722 条附近
          有一条 label 的 key 长度少算了 1 字节（`VOX:ceva001W` 被记成 11），
          老实现直接 `break` ⇒ 只解出 1721 条，后面 3490 条全丢（含大量任务文本）。
          现在改成"跳过坏记录、从下一个 ` LBL` 继续"，`self.skipped` 记录跳过的条数。
        """
        p, n = HDR_LEN, len(raw)
        self.skipped = 0
        guard = 0
        while p < n and self.count < nlab and guard < nlab * 4:
            guard += 1
            if raw[p:p + 4] != MAG_LBL:
                nxt = raw.find(MAG_LBL, p + 1, min(n, p + 1 + 65536))
                if nxt < 0:
                    self.error = 'label 头不对 @%#x（后面没有 LBL 了）' % p
                    break
                self.skipped += 1
                p = nxt
                continue
            npairs, klen = struct.unpack_from('<II', raw, p + 4)
            if klen > 1024 or npairs > 64:          # 明显是坏记录
                nxt = raw.find(MAG_LBL, p + 1, min(n, p + 1 + 65536))
                if nxt < 0:
                    self.error = 'label 字段异常 @%#x（klen=%d npairs=%d）' % (p, klen, npairs)
                    break
                self.skipped += 1
                p = nxt
                continue
            key_end = p + 12 + klen
            q = _find_rts(raw, key_end)
            if q < 0:
                # key 后面直接是下一条 ` LBL` = 这个 label 没有 value（引擎里有这种空标签）
                if raw[key_end:key_end + 4] == MAG_LBL:
                    p = key_end
                    self.count += 1
                    continue
                nxt = raw.find(MAG_LBL, p + 1, min(n, p + 1 + 65536))
                if nxt < 0:
                    self.error = 'value 头不对 @%#x' % key_end
                    break
                self.skipped += 1
                p = nxt
                continue
            key = raw[p + 12:q].decode('latin-1').strip().lower()
            p = q + 3
            vals = []
            bad = False
            for _j in range(npairs):
                if p + 4 > n:
                    bad = True
                    break
                ln = struct.unpack_from('<I', raw, p)[0]
                p += 4
                if ln > 1 << 20 or p + 2 * ln > n:   # 长度离谱 → 坏记录
                    bad = True
                    break
                body = raw[p:p + 2 * ln]             # ★长度是**字符数**
                p += 2 * ln
                vals.append(_decode(body))
                if _j + 1 < npairs:
                    q2 = _find_rts(raw, p)
                    if q2 < 0:
                        bad = True
                        break
                    p = q2 + 3
            if bad:
                nxt = raw.find(MAG_LBL, p + 1, min(n, p + 1 + 65536))
                if nxt < 0:
                    self.error = 'value 头不对 @%#x' % p
                    break
                self.skipped += 1
                p = nxt
                continue
            if key not in self.keys and vals:
                self.keys[key] = vals[0]
            self.count += 1

    # ---------------- 查询 ----------------
    def get(self, key, default=None):
        return self.keys.get(str(key).lower().strip(), default)

    def map_name_keys(self, map_path):
        """地图文件名 → 候选 CSF 键列表（按优先级）。

        `ALL01UMD.MAP` → ['name:all01md', 'name:all01umd', 'all01md', 'all01umd']
        （先试"去掉末尾 U"，再试原样；带不带 `name:` 前缀都试）
        """
        base = os.path.splitext(os.path.basename(str(map_path)))[0].lower()
        cands = []
        if base.endswith('umd'):
            cands.append(base[:-3] + 'md')   # ALL01UMD → ALL01MD（★不是去掉最后一个字符，
                                             #   是 u 在 md 前面：umd→md，自检抓到过）
        if base.endswith('md'):
            cands.append(base)
        out = []
        for c in cands:
            for pre in ('name:', ''):
                k = pre + c
                if k not in out:
                    out.append(k)
        return out

    def name_for_map(self, map_path):
        """按玩家规则取任务名 → `(文本, 命中的键)`；没命中 `(None, None)`。"""
        for k in self.map_name_keys(map_path):
            v = self.keys.get(k)
            if v:
                return v, k
        return None, None

    def short_name_for_map(self, map_path):
        """短名（`...sav` 那条，不带「军事行动：」前缀）→ `(文本, 键)`。"""
        for k in self.map_name_keys(map_path):
            for kk in (k + 'sav', k.replace('name:', 'name:') + 'sav'):
                v = self.keys.get(kk)
                if v:
                    return v, kk
        return None, None


def load(path):
    """带缓存地打开一个 CSF（键 = 路径+大小+mtime）。读不到返回 None。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_size, int(st.st_mtime))
    except Exception:
        key = (path, 0, 0)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    c = Csf(path)
    if c.error and not c.keys:
        return None
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[key] = c
    return c


def find_csf(*hints):
    """在若干目录/路径里找 ra2md.csf（ra2md.csf 优先，其次 ra2.csf）→ 路径或 None。"""
    names = ('ra2md.csf', 'ra2.csf')
    dirs = []
    for h in hints:
        if not h:
            continue
        h = str(h)
        d = h if os.path.isdir(h) else os.path.dirname(h)
        if d and d not in dirs:
            dirs.append(d)
    for d in dirs:
        for nm in names:
            p = os.path.join(d, nm)
            if os.path.isfile(p):
                return p
    return None


if __name__ == '__main__':
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else r'E:\YURI\ra2md.csf'
    c = Csf(p)
    print('%s：%d 条，错误=%s' % (p, len(c.keys), c.error))
    for k in ('name:all01md', 'name:all01mdsav', 'name:all01', 'theme:intro'):
        print('  %-18s = %r' % (k, c.get(k)))
    print('  ALL01UMD.MAP → %s' % (c.name_for_map('ALL01UMD.MAP'),))
