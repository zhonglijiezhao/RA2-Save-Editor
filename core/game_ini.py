# -*- coding: utf-8 -*-
"""从**游戏目录**的 rules(md).ini / art(md).ini 读单位与建筑信息。

为什么要读游戏目录（2026-10-17 玩家要求）：
  · 任务可能有**自制单位**、或者改了名字/属性 —— 用游戏目录里的规则才是"当前这局的样子"；
  · 但"这个型号在这份存档里到底存不存在"**只能看存档**（地图里直接写死的自制单位，
    存档的类型表里一定有；反过来 rules 里有的，这份档未必有）。

所以本模块只负责"**认名字 / 认分类 / 认属性**"，**不参与"能不能放"的判断**。

用法：
    from game_ini import GameTypes
    g = GameTypes()                 # 自动找游戏目录（见 CANDIDATE_DIRS）
    g.dir, g.rules_path, g.art_path
    g.info('E1')      -> {'name':'GI','category':'Soldier','strength':'125','primary':'M60',...}
    g.label('E1')     -> 'GI'
    g.brief('E1')     -> '步兵 · 125HP · M60'

搜索顺序（先到先得，可用 config.json 的 game_dir 覆盖）：
    1) 显式传入的目录
    2) 项目根目录 config.json 里的 "game_dir"
    3) 本机已知目录：D:\\233\\RA2YR、D:\\233\\RA2LB、E:\\YURI
每个目录里按 rulesmd.ini → rules.ini 找规则，artmd.ini → art.ini 找美术。
"""
import io
import json
import os
import re
import sys
import time

# ★2026-10-17 玩家定案：**规则必须跟着"存档所在目录"走**，不能猜别的安装目录。
#   顺序：手动指定 → 存档目录（含上一级）→ **内置规则**（本工程打包的原版快照）。
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
BUILTIN_DIR = os.path.join(HERE, 'ini_builtin')


def config_path():
    """config.json 的位置：**打包后写在 exe 旁边**，开发时写在工程根目录。"""
    cands = []
    if getattr(sys, 'frozen', False):
        cands.append(os.path.join(os.path.dirname(sys.executable), 'config.json'))
    cands.append(os.path.join(PROJ, 'config.json'))
    for c in cands:
        if os.path.isfile(c):
            return c
    return cands[0]

LIST_SECTIONS = [('InfantryTypes', 'infantry'), ('VehicleTypes', 'vehicle'),
                 ('AircraftTypes', 'aircraft'), ('BuildingTypes', 'building')]


def read_text(path):
    """规则文件常是 GBK/ANSI，也见过 UTF-8 ⇒ 依次尝试。"""
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'latin-1'):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode('latin-1', 'ignore')


def parse_ini(path):
    """极简 INI 解析 → {段名小写: {'order': [...], 'kv': {键小写: 值}}}。

    · 去掉 `;` 之后的注释（只在不处于引号内时切）
    · 键值按第一个 `=` 切，键与段名都转小写（RA2 的 INI 本身不区分大小写）
    """
    out = {}
    cur = None
    for line in read_text(path).splitlines():
        s = line.strip()
        if not s or s.startswith(';'):
            continue
        if s.startswith('['):
            end = s.find(']')
            if end > 0:
                cur = out.setdefault(s[1:end].strip().lower(), {'order': [], 'kv': {}})
            continue
        if cur is None or '=' not in s:
            continue
        k, v = s.split('=', 1)
        # 去注释（引号内的 ; 不切）
        q = False
        buf = []
        for ch in v:
            if ch == '"':
                q = not q
            if ch == ';' and not q:
                break
            buf.append(ch)
        val = ''.join(buf).strip().strip('"').strip()
        k = k.strip().lower()
        cur['order'].append(k)
        cur['kv'][k] = val
    return out


def _find_first(d, names):
    for n in names:
        p = os.path.join(d, n)
        if os.path.isfile(p):
            return p
    return None


def _pair_in(d):
    """某目录里若有一套完整规则 → (目录, rules, art)；art 可缺（缺就 None）。"""
    if not d or not os.path.isdir(d):
        return None
    rules = _find_first(d, ['rulesmd.ini', 'rules.ini'])
    if not rules:
        return None
    art = _find_first(d, ['artmd.ini', 'art.ini'])
    return (d, rules, art)


# 允许按内容识别的扩展名（解密 MIX 后的散装文件常叫 `ID_<hash>.bin` 或没有扩展名）
_INI_LIKE_EXT = ('.ini', '.bin', '.dat', '.txt', '.cfg', '')


def _pair_in_hashed(d):
    """目录里**没有** rulesmd.ini，但有"解密出来的散装文件"时，按内容认规则。

    ★2026-10-17 玩家把 `expandmd99.mix` / `expandmd67.mix` 解密成了文件夹，
      里面全是散装资源（`ad01.shp`、`4mtnk.vxl`、`ID_78xxxxxx.bin`…），
      **文件名不一定是 rulesmd.ini**（MIX 里只有 CRC32 哈希，解密工具常写成 ID_xxx.bin）。
      ⇒ 认法：找"同时含 [InfantryTypes] 和 [VehicleTypes]"的那个文件当 rules；
             再找含 `[Cameo]` 或 `Foundation=` 的当 art。
      只扫 .ini/.bin/.dat/.txt 这类文本扩展名 + 大小 30KB~8MB，不会去啃 shp/vxl。
    """
    if not d or not os.path.isdir(d):
        return None
    try:
        names = os.listdir(d)
    except Exception:
        return None
    rules = art = None
    for fn in names:
        ext = os.path.splitext(fn)[1].lower()
        if ext not in _INI_LIKE_EXT:
            continue
        p = os.path.join(d, fn)
        try:
            sz = os.path.getsize(p)
        except Exception:
            continue
        if not (30000 <= sz <= 8 * 1024 * 1024):
            continue
        try:
            with open(p, 'rb') as f:
                head = f.read(4 * 1024 * 1024)
        except Exception:
            continue
        if rules is None and b'[InfantryTypes]' in head and b'[VehicleTypes]' in head:
            rules = p
            continue
        if art is None and (b'[Cameo]' in head or b'Foundation=' in head):
            art = p
        if rules and art:
            break
    return (d, rules, art) if rules else None


# ---------------- MIX 内的规则（大型任务/中文 mod 的常态） ----------------
# ★2026-10-17 玩家指出：大任务档的规则在 `expandmd99.mix` / `expandmd67.mix` 里，
#   不是散装 ini。实测 `E:\YURI\expandmd67.mix` 里就有 rulesmd.ini（548,576 B，
#   头注释"企鹅第四季Rules / 朱艳艳制作"），**ID 被改成了 0x8218F9F4**
#   （标准 CRC32("RULESMD.INI") = 0x1F674573）⇒ 只能"先按标准 ID，再按内容特征"找。
MIX_RULES_ID = 0x8218F9F4          # 标准 CRC32("RULESMD.INI") = 0x1F674573；mod 常见被改
MIX_RULES_IDS = (0x1F674573, 0x8218F9F4)
MIX_ART_IDS = (0xAF902833, 0x5B47D8D5)
RULES_TAGS = (b'[InfantryTypes]', b'[VehicleTypes]', b'[BuildingTypes]')
# ★2026-10-21 实测：artmd.ini 的头注释是 `; ART.INI`（不是 `ART*.INI`），
#   原来的 (b'[Cameo]', b'Foundation=') 一个都匹配不上 ⇒ 改成认头注释。
ART_TAGS = (b'ART.INI',)


def _extract_cache_dir():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    d = os.path.join(base, 'RA2SED_cache', 'mix_rules')
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return None
    return d


def _looks_rules(b):
    """像 rules(md).ini 吗：头注释 `; RULE*.INI`，或正文同时有 InfantryTypes/VehicleTypes。"""
    if not b or len(b) > 6 * 1024 * 1024:
        return False
    head = b[:256]
    if b'RULE' in head and b'.INI' in head:
        return True
    return b'[InfantryTypes]' in b and b'[VehicleTypes]' in b


def _looks_art(b):
    """像 art(md).ini 吗：头注释 `; ART.INI`，且**不能**是规则（排除 rules 里提到 ART.INI）。"""
    if not b or len(b) > 4 * 1024 * 1024:
        return False
    if b'[InfantryTypes]' in b:
        return False
    head = b[:256]
    return b'ART' in head and b'.INI' in head


def _pair_in_mix(d):
    """目录里的 MIX 归档中找 rules/art → (目录, rules路径, art路径, rules所在的mix名)。

    ★2026-10-21 重写（三条硬约束）：
      ① **按文件名序号降序**试（expandmd99 → expandmd67 → …，玩家实测游戏就是这么读的）；
      ② rules 和 art **分别独立挑**优先级最高的那份 —— 它们完全可以来自不同归档
         （本机：规则在 expandmd01.mix，美术在 ra2.mix）；
      ③ Westwood 的 MIX ID **不是标准 CRC32**，所以名字找不到时靠**内容判据**（`_looks_rules`
         / `_looks_art`）递归往套娃里找（`ra2md.mix → localmd.mix → artmd.ini`）。
    """
    if not d or not os.path.isdir(d):
        return None
    try:
        import mix as _mix
    except Exception:
        return None
    cdir = _extract_cache_dir()
    if not cdir:
        return None
    cands = []
    try:
        for fn in os.listdir(d):
            if fn.lower().endswith(('.mix', '.mmx')):
                cands.append((os.path.getsize(os.path.join(d, fn)), fn))
    except Exception:
        return None

    # ★2026-10-21 玩家定案：`expandmd06.mix` 是 **TX 地形包**（任何任务都有、从不含 rules/art），
    #   实测 69 MB / 4665 条目、7 个 ini（地形清单 `;ver2.03 bata`），**没有** [InfantryTypes]。
    #   ⇒ 不删它、但要**降到最低优先**（真有人把规则塞进去仍然找得到，只是排在最后）。
    TERRAIN_PACKS = {'expandmd06.mix'}

    def _rank(item):
        _sz, fn = item
        low = fn.lower()
        if low in TERRAIN_PACKS:
            return (-2, _sz)
        m = re.search(r'(\d{2,})\.[^.]+$', low)
        if m is None and low.startswith(('expand', 'ecache', 'patch')):
            m = re.search(r'(\d+)\.[^.]+$', low)
        return (int(m.group(1)) if m else -1, _sz)

    cands.sort(key=_rank, reverse=True)

    def _dump(sub_mix, ent, fn, tag):
        b = _mix.entry_bytes(sub_mix, ent)
        p = os.path.join(cdir, '%s_%08X_%d_%s.ini' % (fn, ent[0], len(b), tag))
        if not os.path.isfile(p) or os.path.getsize(p) != len(b):
            with open(p, 'wb') as f:
                f.write(b)
        return p

    def _pick(want):
        """按优先级找其中一个文件 → (路径, mix名)；找不到 (None, None)。"""
        if want == 'rules':
            ids, match, mx, name = MIX_RULES_IDS, _looks_rules, 6 * 1024 * 1024, 'rulesmd.ini'
        else:
            ids, match, mx, name = MIX_ART_IDS, _looks_art, 4 * 1024 * 1024, 'artmd.ini'
        for _sz, fn in cands:
            m = _mix.parse_mix(os.path.join(d, fn))
            if not m or not m.get('entries'):
                continue
            e = None
            for fid in ids:
                e = _mix.find_by_id(m, fid)
                if e:
                    break
            sub = m
            if e is not None and not match(bytes(_mix.entry_bytes(m, e))):
                e = None
            if e is None:
                _lvl, sub2, e = _mix.find_nested(m, name, match=match, max_size=mx)
                if e is not None:
                    sub = sub2
            if e is None:
                continue
            try:
                return _dump(sub, e, fn, want), fn
            except Exception:
                continue
        return None, None

    rules, fn_r = _pick('rules')
    if not rules:
        return None
    art, _fn_a = _pick('art')
    return (d, rules, art, fn_r)


# ---------------- 自动探测游戏安装目录（★2026-10-28 玩家要求） ----------------
#
# 为什么需要：新电脑上玩家把存档放在桌面，游戏装在别的盘 ⇒
#   「存档目录 / 上一级 / MIX」三层全落空，工具退化到内置快照（能显示，但
#   "非原版"判定和可建造判定全失效）。以前只能手动去地图页菜单里选目录。
#
# 判据用 **gamemd.exe / ra2md.exe**，不是 "有没有 rulesmd.ini" ——
#   原版安装根本没有散装 rules，规则都在 MIX 里；拿到候选后再由
#   `find_sources` 按「散装 → 内容认 → MIX 抽」逐层验证。

_GAME_EXES = ('gamemd.exe', 'ra2md.exe', 'ra2.exe', 'yuri.exe')
_AUTO_CACHE = None


def _config_game_dir():
    """`config.json` 里的 `game_dir`（玩家手动指定过）→ 路径 或 None。

    ★2026-10-28：以前 `config_path()` 只有"决定写到哪"，**没人读** ——
      地图页菜单「规则目录…」写了 `game_dir`，但 `find_sources` 只看传进来的
      `explicit`（来自 GUI 输入框），于是**下次启动不生效**。这里补上读的一侧。
    """
    p = config_path()
    try:
        with open(p, encoding='utf-8') as f:
            d = (json.load(f) or {}).get('game_dir')
        if isinstance(d, str) and d.strip() and os.path.isdir(d.strip()):
            return d.strip()
    except Exception:
        pass
    return None


def _registry_game_dirs():
    """注册表里的安装目录 → [路径]。只读、静默失败（非 Windows / 无权限都返回 []）。"""
    out = []
    try:
        import winreg
    except Exception:
        return out
    keys = (
        r'SOFTWARE\WOW6432Node\EA Games\Red Alert 2',
        r'SOFTWARE\EA Games\Red Alert 2',
        r'SOFTWARE\WOW6432Node\Westwood\Red Alert 2',
        r'SOFTWARE\Westwood\Red Alert 2',
        r'SOFTWARE\WOW6432Node\Electronic Arts\Red Alert 2',
        r'SOFTWARE\Electronic Arts\Red Alert 2',
        r'SOFTWARE\WOW6432Node\EA Games\Command and Conquer Red Alert II',
        r'SOFTWARE\EA Games\Command and Conquer Red Alert II',
    )
    val_names = ('Install Dir', 'InstallDir', 'Install Path', 'InstallPath',
                 'Path', 'GameDir', 'Directory')
    for hk in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for k in keys:
            try:
                with winreg.OpenKey(hk, k) as key:
                    vals = {}
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        try:
                            nm, val, _t = winreg.EnumValue(key, i)
                            if isinstance(val, str):
                                vals[nm] = val
                        except Exception:
                            continue
            except Exception:
                continue
            for nm in val_names:
                d = vals.get(nm)
                if d and d.strip():
                    out.append(d.strip().rstrip('\\/'))
    return out


def _scan_game_dirs(max_sub=40):
    """各盘符根 + 名字相关的子目录里找游戏 exe → [路径]。

    ★性能（2026-10-28 实测）：全量扫"每盘 80 个子目录 × 4 个 exe 名"要 **2.5 秒**，
      会拖慢启动。改成：
        ① 根目录必扫（26 盘 × 4 次 stat，几十毫秒）；
        ② 子目录**先按名字筛**（`ra2 / yuri / red alert / cnc / 命令` 之类），
           再只对这几个查 exe —— 名字毫不相关的目录不 stat。
      配合下面的磁盘缓存，正常启动不会触发扫描。
    """
    import string
    out = []
    for ch in string.ascii_uppercase:
        root = '%s:\\' % ch
        try:
            if not os.path.isdir(root):
                continue
        except Exception:
            continue
        for fn in _GAME_EXES:
            try:
                if os.path.isfile(os.path.join(root, fn)):
                    out.append(root.rstrip('\\'))
                    break
            except Exception:
                continue
        subs = []
        try:
            with os.scandir(root) as it:
                for e in it:
                    if len(subs) >= max_sub:
                        break
                    try:
                        if not e.is_dir(follow_symlinks=False):
                            continue
                    except Exception:
                        continue
                    nm = e.name.lower()
                    if any(h in nm for h in _SUB_HINTS):
                        subs.append(e.path)
        except Exception:
            continue
        for d in subs:
            for fn in _GAME_EXES:
                try:
                    if os.path.isfile(os.path.join(d, fn)):
                        out.append(d)
                        break
                except Exception:
                    continue
    return out


_SUB_HINTS = ('ra2', 'redalert', 'red alert', 'red-alert', 'yuri', 'yr', 'cnc',
              'command', 'westwood', 'ea games', '红色', '命令', '尤里', 'mod')


def _auto_cache_path():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    d = os.path.join(base, 'RA2SED_cache')
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return None
    return os.path.join(d, 'game_dirs.json')


def auto_detect_game_dirs(force=False):
    """自动找红警目录 → [目录...]（结果缓存到磁盘，7 天内不再扫）。

    缓存的意义：扫描要几百毫秒到几秒，而"游戏装在哪"极少变。
    玩家换了安装位置时，界面上手动指定一次就会写进 `config.json`（优先级更高）。
    """
    global _AUTO_CACHE
    if _AUTO_CACHE is not None and not force:
        return _AUTO_CACHE
    p = _auto_cache_path()
    if p and not force and os.path.isfile(p):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                j = json.load(f) or {}
            ts, dirs = float(j.get('ts') or 0), (j.get('dirs') or [])
            if time.time() - ts < 7 * 86400 and isinstance(dirs, list):
                dirs = [d for d in dirs if isinstance(d, str) and os.path.isdir(d)]
                _AUTO_CACHE = dirs
                return dirs
        except Exception:
            pass
    out, seen = [], set()
    for d in _registry_game_dirs() + _scan_game_dirs():
        if not d:
            continue
        try:
            k = os.path.normcase(os.path.abspath(d))
        except Exception:
            continue
        if k in seen or not os.path.isdir(d):
            continue
        seen.add(k)
        out.append(d)
    _AUTO_CACHE = out
    if p:
        try:
            with open(p, 'w', encoding='utf-8') as f:
                json.dump({'ts': time.time(), 'dirs': out}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return out


def detect_game_dir():
    """自动探测并**验证能用**的第一个目录 → 目录 或 None。

    ★先只试「散装 ini」（毫秒级），全都不行才去解 MIX（每个候选几秒）——
      否则一个没有散装 rules 的候选就能把启动拖到好几秒。
    """
    dirs = auto_detect_game_dirs()
    for d in dirs:
        if _pair_in(d) or _pair_in_hashed(d):
            return d
    for d in dirs:
        if _pair_in_mix(d):
            return d
    return None


def find_sources(save_dir=None, explicit=None):
    """按优先级找规则 → (来源, 目录, rules路径, art路径)。

    来源：'user'（手动指定） / 'save'（存档所在目录或其上一级） /
          **'mix'（同一目录的 MIX 归档里抽出来的）** /
          **'auto'（注册表 / 盘符扫描自动探测到的游戏目录）** /
          'builtin'（内置） / None
    """
    if not explicit:
        explicit = _config_game_dir()      # config.json 里手动指定过的
    p = _pair_in(explicit)
    if p:
        return ('user',) + p
    # ★手动指定的目录里没有 rulesmd.ini 时，按内容认（解密 MIX 出来的散装文件）
    p = _pair_in_hashed(explicit)
    if p:
        return ('user',) + p
    cands = []
    if save_dir:
        cands.append(save_dir)
        cands.append(os.path.dirname(os.path.abspath(save_dir)))
    for d in cands:
        p = _pair_in(d)
        if p:
            return ('save',) + p
    for d in cands:
        p = _pair_in_hashed(d)
        if p:
            return ('save',) + p
    # ★散装找不到 ⇒ 去 MIX 里抽（大型任务/中文 mod 的规则都在这里）
    for d in cands:
        p = _pair_in_mix(d)
        if p:
            return ('mix', p[0], p[1], p[2])
    # ★★2026-10-28：存档不在游戏目录（新电脑常见：存档放桌面，游戏在 D 盘）
    #   ⇒ 自动探测（注册表 + 盘符扫描）。先全试"散装"（快），再试 MIX（慢）。
    autos = auto_detect_game_dirs()
    for d in autos:
        p = _pair_in(d) or _pair_in_hashed(d)
        if p:
            return ('auto',) + p
    for d in autos:
        p = _pair_in_mix(d)
        if p:
            return ('auto', p[0], p[1], p[2])
    p = _pair_in(BUILTIN_DIR)
    if p:
        return ('builtin',) + p
    return (None, None, None, None)


class GameTypes(object):
    """规则/美术信息查询（懒加载，失败不影响主流程）。

    ★2026-10-17 21:0x：**大型任务的自制单位/建筑，规则往往就写在地图 INI 里**
      （实测 `all01umd.map` 里直接有 `[ZDYS]` `[APOCC]` 这些段，而散装 rulesmd.ini 与
      `expandmd67.mix` 里的规则都没有它们 —— 引擎会把地图 INI 的段合并进规则）。
      ⇒ 传入 `map_path` 时，**地图段优先级最高**（同名段/键覆盖 rules/art）。
    """

    def __init__(self, save_dir=None, game_dir=None, map_path=None):
        self.kind, self.dir, self.rules_path, self.art_path = find_sources(save_dir, game_dir)
        self.rules = parse_ini(self.rules_path) if self.rules_path else {}
        self.art = parse_ini(self.art_path) if self.art_path else {}
        # 地图 INI 段（最高优先）→ 同一套 {'order','kv'} 结构
        self.map_path = map_path if (map_path and os.path.isfile(map_path)) else None
        self.map_sec = {}
        if self.map_path:
            try:
                from map_ini import MapIni
                for sec, kv in MapIni(self.map_path).sections.items():
                    if sec in ('map', 'basic', 'header', 'triggers', 'events', 'actions',
                               'tags', 'structures', 'units', 'infantry', 'aircraft',
                               'terrain', 'waypoints', 'countries', 'houses',
                               'isoimapack5', 'overlaypack', 'overlaydatapack',
                               'digest', 'preview', 'previewpack', 'specialflags'):
                        continue
                    low = {str(k).lower(): v for k, v in kv.items()}
                    self.map_sec[sec] = {'order': list(low.keys()), 'kv': low}
            except Exception:
                self.map_sec = {}
        # 类型 ID → (类别, 建造栏序号)
        self.order = {}
        for src in (self.map_sec, self.rules):
            for sec, kind in LIST_SECTIONS:
                d = src.get(sec.lower())
                if not d:
                    continue
                for k, v in d['kv'].items():
                    if not v:
                        continue
                    try:
                        idx = int(k)
                    except ValueError:
                        continue
                    self.order.setdefault(v.strip().upper(), {'kind': kind, 'index': idx})

    # ---------- 查询 ----------
    def ok(self):
        return bool(self.rules) or bool(self.map_sec)

    def source(self):
        """给界面显示的一句话：规则来自哪里、为什么。"""
        if not self.rules_path and not self.map_sec:
            return '未找到 rules(md).ini（只用存档自带信息）'
        art = os.path.basename(self.art_path) if self.art_path else '无 art'
        if self.map_path and self.map_sec:
            return '地图 INI 自带规则（%s，%d 段）＋ %s' % (
                os.path.basename(self.map_path), len(self.map_sec),
                '内置规则' if self.kind == 'builtin' else (self.kind or '规则'))
        if self.kind == 'builtin':
            return '内置规则（本档目录没有规则文件）'
        if self.kind == 'user':
            return '手动指定：%s（%s + %s）' % (self.dir, os.path.basename(self.rules_path), art)
        if self.kind == 'mix':
            mixname = os.path.basename(self.rules_path).split('_')[0]
            return 'MIX 内规则：%s 里的 rulesmd.ini（%s）' % (mixname, art)
        return '本档目录：%s（%s + %s）' % (self.dir, os.path.basename(self.rules_path), art)

    def info(self, type_id):
        """合并 **地图 INI 段 → rules 段 → art 段** → 一个大写键的字典（值都是字符串）。

        地图优先：大型任务的自制单位/建筑只在地图 INI 里有定义（见类文档）。
        """
        t = str(type_id).upper().lower()
        out = {}
        for src in (self.map_sec, self.rules, self.art):
            sec = src.get(t)
            if not sec:
                continue
            for k, v in sec['kv'].items():
                out.setdefault(k.upper(), v)
        return out

    def label(self, type_id, fallback=''):
        """显示名：rules 的 `Name=` 优先，其次 UIName（去掉 `Name:` 前缀），再次 fallback。"""
        i = self.info(type_id)
        for k in ('NAME', 'UINAME'):
            v = (i.get(k) or '').strip()
            if v:
                for pre in ('name:', 'NAME:'):
                    if v.lower().startswith(pre):
                        v = v[len(pre):]
                if v:
                    return v
        return fallback or str(type_id).upper()

    def kind_of(self, type_id):
        e = self.order.get(str(type_id).upper())
        return e['kind'] if e else None

    def index_of(self, type_id):
        e = self.order.get(str(type_id).upper())
        return e['index'] if e else 10 ** 6

    def brief(self, type_id):
        """一行摘要：类别 · 血量 · 主武器 · 造价。"""
        i = self.info(type_id)
        bits = []
        kind = self.kind_of(type_id)
        kind_cn = {'infantry': '步兵', 'vehicle': '载具', 'aircraft': '飞机', 'building': '建筑'}.get(kind)
        cat = (i.get('CATEGORY') or '').strip()
        if kind_cn or cat:
            bits.append(kind_cn or cat)
            if kind_cn and cat and cat.lower() not in kind_cn:
                bits[-1] = '%s/%s' % (kind_cn, cat)
        if i.get('STRENGTH'):
            bits.append('%sHP' % i['STRENGTH'])
        if i.get('PRIMARY'):
            bits.append('主武器 %s' % i['PRIMARY'])
        if i.get('COST'):
            bits.append('$%s' % i['COST'])
        return ' · '.join(bits) if bits else ''


_CACHE = {}


def get_types(save_dir=None, game_dir=None, map_path=None):
    """进程内缓存（同一套规则文件只解析一次）。"""
    g = GameTypes(save_dir, game_dir, map_path)
    key = (g.rules_path, g.art_path, g.map_path)
    if key not in _CACHE:
        _CACHE[key] = g
    return _CACHE[key]


def clear_cache():
    _CACHE.clear()
