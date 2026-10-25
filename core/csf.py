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
import os
import struct

MAG_LBL = b' LBL'
MAG_RTS = b' RTS'
HDR_MAGICS = (b'CSF ', b' FSC')
HDR_LEN = 24          # ★实测 label 表从 0x18 开始（不是 16，也不是 20）

_CACHE = {}           # (路径, 大小, mtime) → Csf


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
        pass
    try:
        cands.append(raw.decode('utf-16le', 'replace'))
    except Exception:
        pass
    try:
        cands.append(raw.decode('gbk', 'replace'))
    except Exception:
        pass
    if not cands:
        return ''
    return max(cands, key=_score).strip('\x00').strip()


class Csf(object):
    """一个 ra2md.csf。键大小写不敏感。"""

    def __init__(self, path):
        self.path = path
        self.keys = {}          # 小写键 → 文本（多条 value 取第一条）
        self.error = None
        self.count = 0
        self._load()

    def _load(self):
        try:
            raw = open(self.path, 'rb').read()
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
        p, n = HDR_LEN, len(raw)
        try:
            for _ in range(nlab):
                if raw[p:p + 4] != MAG_LBL:
                    self.error = 'label 头不对 @%#x' % p
                    break
                npairs = struct.unpack_from('<I', raw, p + 4)[0]
                klen = struct.unpack_from('<I', raw, p + 8)[0]
                key = raw[p + 12:p + 12 + klen].decode('latin-1').lower()
                p += 12 + klen
                vals = []
                for _j in range(npairs):
                    if raw[p:p + 4] != MAG_RTS:
                        self.error = 'value 头不对 @%#x' % p
                        raise StopIteration
                    ln = struct.unpack_from('<I', raw, p + 4)[0]
                    p += 8
                    body = raw[p:p + 2 * ln]      # ★长度是**字符数**
                    p += 2 * ln
                    vals.append(_decode(body))
                if key not in self.keys and vals:
                    self.keys[key] = vals[0]
                self.count += 1
        except StopIteration:
            pass
        except Exception as e:
            self.error = '解析中断：%s' % e

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
