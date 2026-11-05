# -*- coding: utf-8 -*-
"""游戏侧列表表 —— 触发行为里那些"编号 → 名字"的下拉数据。

为什么要它：触发行为有一批参数存的是**列表序号**，不是名字 ——
  19/98/99 播放音效(p16)、20 播放音乐(p17)、21 播放语音(p18)、41 播放动画在(p25)、
  88 播放粒子动画在(p26)、10/117 播放影片(p12)、11/103 文本(p13)。
地编的做法就是按 ptype 去对应的表里枚举候选、把**序号**存进存档。

★★2026-11-04 玩家定案（本条是硬要求）：
  「解密这个还是要存档编辑器内置这个功能的，发布后不能要求玩家把规则文件解出来
    才能用这个软件，地编也是同样的做法。」
  「游戏读取规则是散文件优先，MIX 文件后面数字大的优先。」

  ⇒ 取文件的活全部交给 `mix_vfs`（散文件 > MIX 数字大优先 > 套娃，RSA+Blowfish
     纯标准库解密，零外部工具、零手动解包）。本模块只负责"把字节解析成表"。

数据来自哪儿（地编 `FAData.ini` 的 `[Filenames]` 段就是这么规定的）：
    ptype 12 影片    → art(md).ini      `[Movies]`
    ptype 13 文本    → ra2md.csf        （CSF 标签表）
    ptype 16 音效    → sound(md).ini    `[SoundList]`
    ptype 17 音乐    → theme(md).ini    `[Themes]`
    ptype 18 语音    → eva(md).ini      `[DialogList]`
    ptype 25 动画    → rules(md).ini    `[Animations]`
    ptype 26 粒子    → rules(md).ini    `[ParticleSystems]`

用法：
    from game_tables import lists_for_dir, label_of, choices_of
    tb = lists_for_dir(r'E:\\YURI')          # 给存档目录/游戏目录都行
    tb['animations'].get(256)                # → 'CAMSC09' 或 None
    label_of(tb['sounds'], 12)               # → '12 CivAllDie'
"""
import os
import re

try:
    import mix_vfs
except Exception:                        # pragma: no cover
    mix_vfs = None

_CACHE = {}          # 目录 → (指纹, data)


# ---------------- 文本与 INI 解析 ----------------

def _decode_bytes(raw):
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'latin-1'):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode('latin-1', 'replace')


def _read_text(path):
    try:
        with open(path, 'rb') as f:
            return _decode_bytes(f.read())
    except Exception:
        return ''


# 值以 ';' 开头的条目 = 被注释掉了（`2=;Grinder`）：编号仍在、但引擎不认。
_COMMENT_VAL = ';'


def _parse_num_map(text, section):
    """解析 `[section]` 里 `编号=值` 的行 → {int: str}。

    · 行首 `;` 的整行注释跳过
    · 值里 `;` 之后当注释切掉（引号内的不算）
    · 切完为空但原文有内容（`2=;Grinder`）⇒ 记成 `';Grinder'`：
      编号占位、但标记为「不可用」（`choices_of` 会跳过它）
    · ★★`+=名字`（Ares / Phobos 的**追加注册**语法）：按**出现顺序**接在当前最大编号之后。
      本机 `桌面/RA/rulesmd.ini` 里 [Animations] 有 **650 条**、[ParticleSystems] 3 条
      是这么注册的 —— 以前一条都读不到（正则只认 `^数字=`），下拉里少一大半。
    """
    if not text:
        return {}
    m = re.search(r'^\s*\[' + re.escape(section) + r'\]\s*$', text, re.M | re.I)
    if not m:
        return {}
    rest = text[m.end():]
    nxt = re.search(r'^\s*\[', rest, re.M)
    body = rest[:nxt.start()] if nxt else rest
    out = {}
    adds = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith(';'):
            continue
        if s.startswith('+='):
            val = _cut_comment(s[2:]).strip()
            if val:
                adds.append(val)
            continue
        mm = re.match(r'^(\d+)\s*=\s*(.*)$', s)
        if not mm:
            continue
        n = int(mm.group(1))
        raw = mm.group(2)
        val = _cut_comment(raw).strip()
        if not val:
            stripped = raw.strip()
            if stripped.startswith(';'):        # `2=;Grinder` = 注释掉的条目
                out[n] = stripped
            else:
                out[n] = ''
        else:
            out[n] = val
    if adds:
        # ★编号 = 当前最大 + 1，依次递增。
        #   实测这类 mod 的编号是连续的（桌面 RA：[Animations] 713 项、max=712），
        #   max+1 与引擎"追加到列表末尾"的结果一致。
        cur = (max(out) + 1) if out else 0
        for val in adds:
            while cur in out:
                cur += 1
            out[cur] = val
            cur += 1
    return out


def _cut_comment(v):
    """去掉值里的 `;` 注释（引号内的不切）。"""
    q = False
    buf = []
    for ch in v:
        if ch == '"':
            q = not q
        if ch == ';' and not q:
            break
        buf.append(ch)
    return ''.join(buf).strip().strip('"').strip()


def _is_disabled(val):
    return isinstance(val, str) and val.startswith(_COMMENT_VAL)


# ---------------- 取游戏文件 ----------------

FILES = ('rulesmd.ini', 'artmd.ini', 'soundmd.ini', 'evamd.ini', 'thememd.ini', 'ra2md.csf')


def _pick_dir(base_dir):
    """把"存档目录 / 游戏目录"归一成一个真的游戏目录（本目录 → 上一级）。"""
    if not base_dir or not os.path.isdir(base_dir):
        return None
    cur = os.path.abspath(base_dir)
    for _ in range(3):
        if _looks_game_dir(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def _looks_game_dir(d):
    """"像游戏目录" = 有 exe / 有归档 / 有散装 ini，三者之一。"""
    try:
        names = [n.lower() for n in os.listdir(d)]
    except Exception:
        return False
    if any(n in ('gamemd.exe', 'ra2md.exe', 'ra2.exe', 'yuri.exe') for n in names):
        return True
    if any(n.endswith(('.mix', '.mmx')) for n in names):
        return True
    return any(n in ('rulesmd.ini', 'rules.ini') for n in names)


def _fingerprint(v):
    """目录指纹：归档签名 + 6 个散装文件的 mtime（任何一个变就重新解析）。"""
    parts = []
    try:
        parts.append(v.signature())
    except Exception:
        parts.append('?')
    for n in FILES:
        p = mix_vfs._loose_file(v.dir, n) if mix_vfs else None
        try:
            parts.append('%s:%s' % (n, os.path.getmtime(p) if p else '-'))
        except Exception:
            parts.append('%s:?' % n)
    return '|'.join(parts)


# ---------------- 主入口 ----------------

def load_tables(game_dir):
    """解析一个游戏目录 → 表 dict；认不出任何东西返回 None。

    返回的 dict 里除各张表，还有：
        'dir'     游戏目录
        'source'  {文件名: '散文件' / 'expandmd99.mix' / 'ra2md.mix → 内嵌'}
    """
    if not mix_vfs:
        return None
    d = _pick_dir(game_dir)
    if not d:
        return None
    v = mix_vfs.game_vfs(d)
    fp = _fingerprint(v)
    key = os.path.normcase(d)
    hit = _CACHE.get(key)
    if hit and hit[0] == fp:
        return hit[1]

    texts, src = {}, {}
    for n in FILES:
        b, s = v.read(n)
        if b:
            texts[n] = _decode_bytes(b)
            src[n] = s
    if not texts:
        _CACHE[key] = (fp, None)
        return None

    rules = texts.get('rulesmd.ini') or texts.get('rules.ini') or ''
    art = texts.get('artmd.ini') or ''
    data = {
        'dir': d,
        'source': src,
        'path': src.get('rulesmd.ini'),
        'animations': _parse_num_map(rules, 'Animations'),
        'voxel_anims': _parse_num_map(rules, 'VoxelAnims'),
        'particles': _parse_num_map(rules, 'Particles'),
        'particle_systems': _parse_num_map(rules, 'ParticleSystems'),
        'sounds': _parse_num_map(texts.get('soundmd.ini'), 'SoundList'),
        'eva': _parse_num_map(texts.get('evamd.ini'), 'DialogList'),
        'themes': _parse_num_map(texts.get('thememd.ini'), 'Themes'),
        'movies': _parse_num_map(art, 'Movies'),
    }
    # ★影片：地编 `FAData.ini` 里 `[MovieList] Start=66` —— 1..65 是原版过场，
    #   下拉里只列 66 之后的（玩家要改的一般是自己的片子）。表里保留全量。
    data['movies_from'] = 66
    # CSF（ptype 13 文本）：label 表 + 文本
    csf = _load_csf(v)
    data['csf_keys'] = csf['keys']
    data['csf_text'] = csf['text']
    data['csf'] = csf['index']
    _CACHE[key] = (fp, data)
    return data


def _load_csf(v):
    """从 VFS 取 ra2md.csf → {'keys':[标签...], 'text':{标签:文本}, 'index':{序号:标签}}。"""
    empty = {'keys': [], 'text': {}, 'index': {}}
    try:
        b, _src = v.read('ra2md.csf')
    except Exception:
        b = None
    if not b:
        return empty
    try:
        import csf as _csf
    except Exception:
        return empty
    try:
        raw = _csf.Csf(raw=b)
    except Exception:
        return empty
    keys = sorted(raw.keys)
    # 地编把 CSF 文本当下拉列表时按标签名字母序排（编号 = 序号）
    return {'keys': keys,
            'text': dict(raw.keys),
            'index': {i + 1: k for i, k in enumerate(keys)}}


# ---------------- 兼容旧接口 ----------------

def find_rules(base_dir):
    """向后兼容：返回 rules 的**来源描述**（散文件路径 / `mix:expandmd01.mix`）。"""
    d = _pick_dir(base_dir)
    if not d or not mix_vfs:
        return None
    v = mix_vfs.game_vfs(d)
    p = mix_vfs._loose_file(d, 'rulesmd.ini') or mix_vfs._loose_file(d, 'rules.ini')
    if p:
        return p
    rec = v.index().get('rulesmd.ini') or v.index().get('rules.ini')
    return ('mix:%s' % rec['mix']) if rec else None


def load_rules(path_or_hint):
    """向后兼容：给目录也能用（旧签名是"给 rulesmd.ini 路径"）。"""
    if not path_or_hint:
        return None
    if os.path.isdir(path_or_hint):
        return load_tables(path_or_hint)
    d = os.path.dirname(os.path.abspath(path_or_hint))
    return load_tables(d)


def lists_for_dir(base_dir):
    """给存档所在目录（或游戏目录）→ 表 dict（找不到任何游戏文件时返回 None）。"""
    return load_tables(base_dir)


# ---------------- ptype → 表 ----------------

# 地编的参数类型编号（`fa2_tables.ACTION_TYPES` 里的第二项）
PTYPE_TABLES = {
    12: ('movies', '影片（artmd.ini [Movies]，地编只列 66 号以后）'),
    13: ('csf', '文本（ra2md.csf 标签）'),
    16: ('sounds', '音效（soundmd.ini [SoundList]）'),
    17: ('themes', '音乐主题（thememd.ini [Themes]）'),
    18: ('eva', '语音（evamd.ini [DialogList]）'),
    25: ('animations', '动画（rulesmd.ini [Animations]）'),
    26: ('particle_systems', '粒子系统（rulesmd.ini [ParticleSystems]）'),
}

# 这些 ptype 的表不从上面几处来（保留旧名字，别的地方可能引用）
PTYPE_EXTERNAL = {}


def label_of(table, n):
    """把编号翻成 `编号 名字`；不在表里就 `编号（表里没有）`。"""
    if n is None:
        return ''
    try:
        n = int(n)
    except Exception:
        return str(n)
    name = (table or {}).get(n)
    if name:
        if _is_disabled(name):
            return '%d %s（表里已注释，不可用）' % (n, name[1:])
        return '%d %s' % (n, name)
    if table:
        return '%d（表里没有这个号）' % n
    return str(n)


def choices_of(table, limit=4000, skip_disabled=True):
    """下拉候选：`编号 名字`（按编号排序，默认跳过被注释掉的条目）。"""
    if not table:
        return []
    out = []
    for k in sorted(table):
        v = table[k]
        if not v:
            continue
        if skip_disabled and _is_disabled(v):
            continue
        out.append('%d %s' % (k, v[1:] if _is_disabled(v) else v))
        if len(out) >= limit:
            break
    return out


def arg_choices(tb, ptype, limit=4000):
    """按 ptype 给参数下拉填候选（`编号 名字`）；没有对应表就返回 []。"""
    ent = PTYPE_TABLES.get(ptype)
    if not tb or not ent:
        return []
    tbl = tb.get(ent[0]) or {}
    if ptype == 13:
        # CSF 有 5000+ 条，下拉限流；每条带上正文前 24 字，否则光看标签名认不出是什么
        text = tb.get('csf_text') or {}
        out = []
        for k in sorted(tbl)[:2000]:
            lab = tbl[k]
            s = ' '.join((text.get(lab) or '').split())[:24]
            out.append('%d %s%s' % (k, lab, ('｜' + s) if s else ''))
        return out
    return choices_of(tbl, limit=limit)


def arg_label(tb, ptype, v):
    """按 ptype 把参数值翻成人话（翻不了就给原值）。"""
    ent = PTYPE_TABLES.get(ptype)
    if not tb or not ent:
        return str(v)
    tbl = tb.get(ent[0]) or {}
    if ptype == 13:
        # CSF 文本：`编号 标签｜内容前 40 字`
        key = tbl.get(v)
        if not key:
            return '%s（CSF 里没有这个号）' % v
        txt = ' '.join(((tb.get('csf_text') or {}).get(key) or '').split())
        if txt:
            return '%s %s｜%s%s' % (v, key, txt[:40], '…' if len(txt) > 40 else '')
        return '%s %s' % (v, key)
    return label_of(tbl, v)


def describe(base_dir):
    """给界面用的一句话：这套表是从哪儿来的（用于排查"为什么下拉是空的"）。"""
    d = _pick_dir(base_dir)
    if not d:
        return '没找到游戏目录（存档目录/上一级都没有 exe 或 MIX）'
    tb = load_tables(d)
    if not tb:
        return '游戏目录 %s 里读不到规则（MIX 全部解不开？）' % d
    bits = ['%d 动画' % len(tb['animations']), '%d 音效' % len(tb['sounds']),
            '%d 语音' % len(tb['eva']), '%d 主题' % len(tb['themes']),
            '%d 影片' % len(tb['movies']), '%d 文本' % len(tb['csf'])]
    srcs = '，'.join('%s←%s' % (k, v) for k, v in sorted(tb['source'].items()))
    return '%s ｜ %s ｜ %s' % (d, ' / '.join(bits), srcs)


def clear_cache():
    _CACHE.clear()
    if mix_vfs:
        mix_vfs.clear_cache()
