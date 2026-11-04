# -*- coding: utf-8 -*-
"""游戏目录虚拟文件系统（VFS）—— 按**引擎真实的读取顺序**取游戏文件。

★★为什么必须有它（玩家 2026-11-04 定案）
  「解密这个还是要存档编辑器内置这个功能的，发布后不能要求玩家把规则文件解出来
    才能用这个软件，地编也是同样的做法。」
  「游戏读取规则是散文件优先，MIX 文件后面数字大的优先，比如 expandmd99.mix、
    expandmd90.mix 这两个文件，那么就是 expandmd99.mix 中的规则生效。」

⇒ 优先级（高 → 低）：
  ① **散文件**：游戏目录下的 `rulesmd.ini` / `soundmd.ini` / …（有就直接用，最快）
  ② **MIX 归档**：文件名末尾数字大的先（`expandmd99.mix` 压 `expandmd90.mix` 压
     `expandmd01.mix`），没有数字的基础包（`ra2.mix` / `language.mix` / `ra2md.mix` /
     `langmd.mix`）排在所有 `expandmd*` 之后
  ③ **嵌套归档**：`ra2md.mix → langmd.mix → soundmd.ini` 这种套娃要能钻进去

MIX 的解密（Westwood RSA + Blowfish）全部在 `core/mix.py` 里用**纯标准库**实现，
不依赖 pycryptodome、也不需要 XCC Mixer ⇒ 玩家装上 exe 就能用，什么都不用先解包。

两个关键工程问题，这里都处理了：
  · **MIX 里的文件名不是标准 CRC32**（`rulesmd.ini` 实测是 0x8218F9F4，标准 CRC 是
    0x1F674573；`soundmd.ini` 实测 0x7A742519，标准 CRC 0x1115C1AE）—— Westwood 用的是
    自己的哈希，XCC 靠 `global mix database.dat` 才能把 ID 翻成名字。
    ⇒ 本模块**按内容特征认文件**（头注释 `; SOUND.INI` / 首段 `[DialogList]` / CSF magic），
      不依赖任何 ID 表。
  · **性能**：一次扫描要读十几个包的索引、钻两层套娃。所以扫描结果**落盘缓存**
    （`%LOCALAPPDATA%\\RA2SED_cache\\vfs_index.json`，按目录里所有归档的
    name+size+mtime 指纹失效），第二次启动是毫秒级。

对外接口（只用这几个）：
    v = game_vfs(r'E:\\YURI')          # 拿缓存实例（同一目录只建一次）
    v.read('rulesmd.ini')              -> (bytes, 'expandmd01.mix') 或 (None, None)
    v.read_text('soundmd.ini')         -> str 或 None
    v.found()                          -> {'soundmd.ini': 'expandmd01.mix', ...}
    v.layers()                         -> [('expandmd99.mix', 1099), ...]（高优先在前）
"""
import hashlib
import json
import os
import re
import time

try:
    import mix as _mix
except Exception:                       # 极端情况下（被打包工具裁掉）不致命
    _mix = None

# ---------------- 归档优先级 ----------------

# 带数字的归档（expandmd99.mix）统一加这个底数，保证**永远压过**没有数字的基础包
_FILE_RANK_BASE = 1000

# 没有数字的归档：按引擎的实际加载顺序（越晚加载越优先）
_BASE_RANK = {
    'ra2.mix': 10,
    'language.mix': 20,
    'ra2md.mix': 30,
    'langmd.mix': 40,
    'multimd.mix': 50,
    'multi.mix': 50,
    'movmd03.mix': 55,
    'mapsmd03.mix': 56,
    'maps01.mix': 56,
    'maps02.mix': 56,
    'thememd.mix': 60,
    'ecache03.mix': 65,
    'ares.mix': 900,            # Ares 平台最后挂载，理论上压过一切
}
_DEFAULT_RANK = 100

# ★玩家实测（2026-10-21）：`expandmd06.mix` 是 **TX 地形包**（任何任务都有、从不含规则），
#   69 MB / 4665 条目。不排除它（万一有人真把规则塞进去），但**降到最低优先**，
#   免得为它白扫 4665 个成员。
TERRAIN_PACKS = {'expandmd06.mix'}


def layer_rank(filename):
    """归档文件名的优先级（数字大的高）。

    ★只对**扩展包**（`expandmd99` / `expand01` / `patch02`）按末尾数字排序 ——
      `ra2.mix`、`ra2md.mix` 名字里也有数字 2，但那是"红警2"，不是包序号
      （第一版就栽在这：ra2.mix 被算成 1002，压过了 expandmd01）。
    """
    low = os.path.basename(filename).lower()
    if low in TERRAIN_PACKS:
        return -1000
    if low in _BASE_RANK:
        return _BASE_RANK[low]
    stem = os.path.splitext(low)[0]
    m = re.match(r'^(expandmd|expand|patch)(\d+)$', stem)
    if m:
        return _FILE_RANK_BASE + int(m.group(2))
    return _DEFAULT_RANK


# ---------------- 按内容认文件 ----------------
#
# 每条 = (标准文件名, 头部 64 字节内必须同时出现的字节, 不能出现的字节)
# 头注释实测：
#   rulesmd.ini  "; RULE.INI"      artmd.ini  "; ART.INI"
#   soundmd.ini  "; SOUND.INI"     thememd.ini "; THEME.INI"
#   evamd.ini    "\\r\\n[DialogList]"
# 注意 RULE 与 ART 互斥（`; RULE.INI` 里没有 "ART"），可以安全用「同时包含」判据。
_HEAD_SIGS = (
    ('rulesmd.ini', (b'RULE', b'.INI'), (b'ART',)),
    ('artmd.ini', (b'ART', b'.INI'), (b'RULE',)),
    ('soundmd.ini', (b'SOUND', b'.INI'), (b'RULE', b'ART', b'THEME')),
    ('thememd.ini', (b'THEME', b'.INI'), (b'RULE', b'ART', b'SOUND')),
    ('evamd.ini', (b'[DialogList]',), (b'[InfantryTypes]',)),
)

CSF_MAGICS = (b'CSF ', b' FSC')

# 文本头（用来挡掉 shp/vxl/wav 之类）
_TEXT_HEADS = (0x3B, 0x5B, 0x0D, 0x0A, 0x20, 0xEF)     # ; [ CR LF space BOM


def _looks_text(head):
    return bool(head) and head[0] in _TEXT_HEADS


def identify(head, more=None):
    """头部字节 → 标准文件名（认不出返回 None）。

    `more` = 该成员的前 8 KB（头 64 字节认不出时才用，用于"头注释被 mod 改掉"的兜底）。
    """
    if not head:
        return None
    if head[:4] in CSF_MAGICS:
        return 'ra2md.csf'
    for name, need, deny in _HEAD_SIGS:
        if all(t in head for t in need) and not any(d in head for d in deny):
            return name
    if more and _looks_text(head):
        # 兜底：正文里有类型段 = 规则；有 Cameo/Foundation 且**没有**类型段 = 美术
        if b'[InfantryTypes]' in more and b'[VehicleTypes]' in more:
            return 'rulesmd.ini'
        if b'[InfantryTypes]' not in more and (b'[Cameo]' in more or b'Foundation=' in more):
            return 'artmd.ini'
        if b'[SoundList]' in more:
            return 'soundmd.ini'
        if b'[Themes]' in more:
            return 'thememd.ini'
        if b'[DialogList]' in more:
            return 'evamd.ini'
    return None


# ---------------- 目录里的归档 ----------------

def _list_layers(game_dir):
    """目录里的归档 → [(rank, filename)]，**高优先在前**。"""
    out = []
    try:
        names = os.listdir(game_dir)
    except Exception:
        return out
    for fn in names:
        low = fn.lower()
        if not low.endswith(('.mix', '.mmx')):
            continue
        p = os.path.join(game_dir, fn)
        try:
            if not os.path.isfile(p):
                continue
        except Exception:
            continue
        out.append((layer_rank(fn), fn))
    # rank 降序；同 rank 按名字降序，保证每次结果一致
    out.sort(key=lambda x: (x[0], x[1].lower()), reverse=True)
    return out


def _loose_file(game_dir, name):
    """目录下的**散文件**（Windows 不区分大小写，但大小写混写的 mod 目录也有）→ 路径或 None。"""
    p = os.path.join(game_dir, name)
    try:
        if os.path.isfile(p):
            return p
    except Exception:
        pass
    low = name.lower()
    try:
        for fn in os.listdir(game_dir):
            if fn.lower() == low and os.path.isfile(os.path.join(game_dir, fn)):
                return os.path.join(game_dir, fn)
    except Exception:
        pass
    return None


# ---------------- 磁盘索引缓存 ----------------

def _cache_dir():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    d = os.path.join(base, 'RA2SED_cache')
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return None
    return d


def _dir_signature(game_dir, layers):
    """目录指纹：所有归档的 name+size+mtime，变了就重扫。"""
    h = hashlib.md5()
    h.update(os.path.normcase(os.path.abspath(game_dir)).encode('utf-8', 'replace'))
    for _rank, fn in layers:
        p = os.path.join(game_dir, fn)
        try:
            st = os.stat(p)
            h.update(('%s|%d|%d;' % (fn.lower(), st.st_size, int(st.st_mtime))).encode('utf-8'))
        except Exception:
            h.update(('%s|?;' % fn.lower()).encode('utf-8'))
    return h.hexdigest()


def _cache_path():
    d = _cache_dir()
    return os.path.join(d, 'vfs_index.json') if d else None


def _load_cache(path):
    if not path:
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            j = json.load(f) or {}
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}


def _save_cache(path, obj):
    if not path:
        return
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


# ---------------- 扫描 ----------------

MAX_DEPTH = 2            # 套娃深度（ra2md.mix → langmd.mix → 文件 = 2 层）
MAX_TEXT = 12 * 1024 * 1024
CSF_MIN_SIZE = 50000     # 真正的 ra2md.csf 有 300 KB+；小的是 ares.csf 之类


class GameVFS(object):
    """一个游戏目录的虚拟文件系统。"""

    def __init__(self, game_dir):
        self.dir = os.path.abspath(game_dir) if game_dir else None
        self._layers = None
        self._index = None          # name(小写) → {'rank','mix','chain':[id...],'id','size'}
        self._cache = {}            # name(小写) → bytes
        self._cache_src = {}        # name(小写) → 来源描述
        self.error = None

    # ---------- 基础 ----------
    def ok(self):
        return bool(self.dir and os.path.isdir(self.dir))

    def layers(self):
        if self._layers is None:
            self._layers = _list_layers(self.dir) if self.ok() else []
        return self._layers

    def signature(self):
        """目录里所有归档的指纹（变了就说明换了 MOD / 补了包）。"""
        if not hasattr(self, '_sig'):
            self._sig = _dir_signature(self.dir, self.layers()) if self.ok() else ''
        return self._sig

    def _parse(self, fn):
        if not _mix:
            return None
        try:
            return _mix.parse_mix(os.path.join(self.dir, fn))
        except Exception:
            return None

    # ---------- 扫描 ----------
    def _scan_mix(self, m, rank, mixname, out, depth=0, chain=()):
        """遍历一个归档：文本成员按内容认名字，像 MIX 的成员递归钻（深度受限）。"""
        entries = m.get('entries') or []
        data = m['data']
        base = m['data_start']
        for e in entries:
            fid, off, size = e[0], e[1], e[2]
            if size < 200 or size > MAX_TEXT:
                continue
            a = base + off
            try:
                head = bytes(data[a:a + 64])
            except Exception:
                continue
            if len(head) < 16:
                continue
            if _mix.looks_like_mix(data, a, size):
                if depth < MAX_DEPTH:
                    sub = _mix.parse_mix_prefix(data, a, size)
                    if sub and sub.get('entries'):
                        self._scan_mix(sub, rank, mixname, out, depth + 1, chain + (fid,))
                continue
            if not _looks_text(head) and head[:4] not in CSF_MAGICS:
                continue
            name = identify(head)
            if name is None:
                # 头注释认不出 → 多读 8KB 再认一次（mod 改过头注释的 INI）
                more = bytes(data[a:a + 8192]) if size > 4096 else bytes(data[a:a + size])
                name = identify(head, more)
            if not name:
                continue
            if name == 'ra2md.csf' and size < CSF_MIN_SIZE:
                # ★ares.mix 里有一份 1.7 KB 的 ares.csf，magic 一样 —— 不能当成
                #   ra2md.csf（本机 ra2md.csf 是 334 KB / 5219 条）。
                continue
            key = name.lower()
            rec = {'rank': rank, 'mix': mixname, 'chain': list(chain),
                   'id': fid, 'size': size}
            old = out.get(key)
            # 高优先的层先扫到 → 只保留第一次命中
            if old is None or (rec['rank'] > old['rank']):
                out[key] = rec

    def _scan(self):
        """全扫一遍 → 索引（结果按指纹落盘缓存）。"""
        out = {}
        layers = self.layers()
        for rank, fn in layers:
            m = self._parse(fn)
            if not m:
                continue
            if not m.get('entries') and m.get('error'):
                self.error = self.error or ('%s: %s' % (fn, m['error']))
                continue
            try:
                self._scan_mix(m, rank, fn, out)
            except Exception as e:
                self.error = self.error or ('%s 扫描失败：%s' % (fn, e))
        return out

    def index(self):
        if self._index is not None:
            return self._index
        if not self.ok():
            self._index = {}
            return self._index
        layers = self.layers()
        sig = self.signature()
        cp = _cache_path()
        j = _load_cache(cp)
        dirs = j.get('dirs') or {}
        key = os.path.normcase(self.dir)
        hit = dirs.get(key)
        if hit and hit.get('sig') == sig and isinstance(hit.get('files'), dict):
            self._index = {k.lower(): v for k, v in hit['files'].items()}
            return self._index
        files = self._scan()
        self._index = files
        dirs[key] = {'sig': sig, 'ts': time.time(),
                     'files': {k: v for k, v in files.items()}}
        # 只留最近 24 个目录，免得缓存文件无限长
        if len(dirs) > 24:
            for k2, _v in sorted(dirs.items(), key=lambda kv: kv[1].get('ts', 0))[:-24]:
                dirs.pop(k2, None)
        _save_cache(cp, {'dirs': dirs})
        return self._index

    def found(self):
        """{文件名: 来源描述}（给界面显示"规则从哪来"）。"""
        out = {}
        for name, rec in self.index().items():
            out[name] = rec['mix'] if not rec.get('chain') else '%s → 内嵌' % rec['mix']
        # 散文件覆盖归档（它优先级最高）
        for name in list(_HEAD_SIGS_NAME) + ['ra2md.csf']:
            p = _loose_file(self.dir, name) if self.ok() else None
            if p:
                out[name] = '散文件'
        return out

    # ---------- 读取 ----------
    def _entry_bytes(self, fn, rec):
        """按索引记录把字节取出来（含套娃逐层定位）。"""
        m = self._parse(fn)
        if not m:
            return None
        for fid in (rec.get('chain') or []):
            ent = _mix.find_by_id(m, fid)
            if not ent:
                return None
            b = bytes(_mix.entry_bytes(m, ent))
            m = _mix.parse_mix_bytes(b, fn)
            if not m:
                return None
        ent = _mix.find_by_id(m, rec['id'])
        if not ent:
            return None
        return bytes(_mix.entry_bytes(m, ent))

    def read(self, name):
        """按优先级读一个文件 → (bytes, 来源) / (None, None)。

        散文件优先；没有就按归档优先级（数字大的先）从索引里取，**自动解密**。
        """
        key = name.lower()
        if key in self._cache:
            b = self._cache[key]
            return (b, self._cache_src.get(key, '')) if b is not None else (None, None)
        # ① 散文件
        p = _loose_file(self.dir, name) if self.ok() else None
        if p:
            try:
                with open(p, 'rb') as f:
                    b = f.read()
                self._cache[key] = b
                self._cache_src[key] = '散文件 %s' % os.path.basename(p)
                return b, self._cache_src[key]
            except Exception:
                pass
        # ② 归档
        rec = self.index().get(key)
        if rec:
            try:
                b = self._entry_bytes(rec['mix'], rec)
            except Exception:
                b = None
            if b:
                self._cache[key] = b
                src = rec['mix'] if not rec.get('chain') else '%s → 内嵌' % rec['mix']
                self._cache_src[key] = src
                return b, src
        self._cache[key] = None
        return None, None

    def read_text(self, name):
        b, _src = self.read(name)
        if not b:
            return None
        for enc in ('utf-8-sig', 'utf-8', 'gbk', 'latin-1'):
            try:
                return b.decode(enc)
            except Exception:
                continue
        return b.decode('latin-1', 'replace')


# 用来列举散文件名的固定清单（`found()` 里用）
_HEAD_SIGS_NAME = tuple(s[0] for s in _HEAD_SIGS)

_VFS_CACHE = {}


def game_vfs(game_dir):
    """拿某个目录的 VFS（进程内缓存，同一目录只建一次）。"""
    if not game_dir:
        return None
    try:
        key = os.path.normcase(os.path.abspath(game_dir))
    except Exception:
        return None
    if key not in _VFS_CACHE:
        _VFS_CACHE[key] = GameVFS(game_dir)
    return _VFS_CACHE[key]


def clear_cache():
    """测试用：清掉进程内实例（磁盘索引不动）。"""
    _VFS_CACHE.clear()


def describe_layers(game_dir, limit=10):
    """给界面/自检用：列出该目录的归档优先级（高 → 低）。"""
    rows = _list_layers(game_dir) if game_dir else []
    return [('%s' % fn, rank) for rank, fn in rows[:limit]]
