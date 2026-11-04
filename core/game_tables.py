# -*- coding: utf-8 -*-
"""§SOV20 游戏侧列表表（rulesmd.ini / thememd.ini / CSF 里的"编号 → 名字"）。

为什么需要它：触发行为里有一批参数存的是**列表序号**，不是名字 ——
  19/98/99 播放音效(p16)、20 播放音乐(p17)、21 播放语音(p18)、41 播放动画在(p25)、
  88 播放粒子动画在(p26)、10/117 播放影片(p12)、11/103 文本(p13)。
地编的做法就是按 ptype 去对应的表里枚举出候选、把**序号**存进存档。

玩家确认（2026-11-04）：「41 播放动画在 的列表就在 rulesmd 里」——
实测 rulesmd.ini 确实有：
  [Animations]       编号=动画名（611 项，本 mod 缺号较多）
  [VoxelAnims]       体素动画
  [Particles]        粒子（22 项）
  [ParticleSystems]  粒子系统（13 项）

音效/语音/影片的列表**不在** rulesmd.ini（该 mod 没有 [SoundList]/[EVA]/[Movies]），
它们在 soundmd.ini / evamd.ini / movies 里（打包在 MIX，需要另行解包）⇒ 这里先做能读的。

用法：
    from game_tables import lists_for_dir
    tb = lists_for_dir(r'E:\\YURI')          # 给存档目录/游戏目录都行
    tb['animations'].get(256)                # → 'CAMSC09' 或 None
    tb['animations_label'](256)              # → '256 CAMSC09' / '256（rulesmd 里没有）'
"""
import os
import re

_CACHE = {}          # abspath(rulesmd.ini) → (mtime, data)


def _read_text(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'latin-1'):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode('latin-1', 'replace')


def _parse_num_map(text, section):
    """解析 `[section]` 里 `编号=值` 的行 → {int: str}（跳过注释/空行）。"""
    m = re.search(r'^\s*\[' + re.escape(section) + r'\]\s*$', text, re.M | re.I)
    if not m:
        return {}
    rest = text[m.end():]
    nxt = re.search(r'^\s*\[', rest, re.M)
    body = rest[:nxt.start()] if nxt else rest
    out = {}
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith(';'):
            continue
        mm = re.match(r'^(\d+)\s*=\s*([^;\s]+)', s)
        if mm:
            out[int(mm.group(1))] = mm.group(2)
    return out


def find_rules(base_dir):
    """在 `base_dir` 及其上两级里找 rules(md).ini，返回路径或 None。"""
    if not base_dir:
        return None
    cur = os.path.abspath(base_dir)
    for _ in range(3):
        for name in ('rulesmd.ini', 'rules.ini'):
            p = os.path.join(cur, name)
            if os.path.isfile(p):
                return p
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def load_rules(path):
    """解析一个 rules(md).ini → {'animations':{…}, 'voxel_anims':{…}, 'particles':{…},
    'particle_systems':{…}, 'path':…}（按 mtime 缓存）。"""
    if not path:
        return None
    key = os.path.abspath(path)
    try:
        mt = os.path.getmtime(key)
    except Exception:
        return None
    hit = _CACHE.get(key)
    if hit and hit[0] == mt:
        return hit[1]
    text = _read_text(key)
    data = {
        'path': key,
        'animations': _parse_num_map(text, 'Animations'),
        'voxel_anims': _parse_num_map(text, 'VoxelAnims'),
        'particles': _parse_num_map(text, 'Particles'),
        'particle_systems': _parse_num_map(text, 'ParticleSystems'),
    }
    _CACHE[key] = (mt, data)
    return data


def lists_for_dir(base_dir):
    """给存档所在目录（或游戏目录）→ 列表 dict（找不到 rules 时返回 None）。"""
    return load_rules(find_rules(base_dir))


# ---- ptype → (用哪张表, 中文说明) ----
# 地编的参数类型编号（fa2_tables.ACTION_TYPES 里的第二项）
PTYPE_TABLES = {
    25: ('animations', '动画（rulesmd [Animations]）'),
    26: ('particle_systems', '粒子系统（rulesmd [ParticleSystems]）'),
}
# 这些 ptype 的列表不在 rulesmd.ini 里（需要 soundmd.ini / evamd.ini / movies）
PTYPE_EXTERNAL = {
    12: '影片列表（movies，需 soundmd/movies 表）',
    13: 'CSF 文本',
    16: '音效列表（soundmd.ini [SoundList]，需从 MIX 取）',
    17: '音乐主题（thememd.ini [Themes] / CSF THEME:*）',
    18: '语音列表（evamd.ini，需从 MIX 取）',
}


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
        return '%d %s' % (n, name)
    return '%d（表里没有这个号）' % n


def choices_of(table, limit=4000):
    """下拉候选：`编号 名字`（按编号排序）。"""
    if not table:
        return []
    return ['%d %s' % (k, table[k]) for k in sorted(table)][:limit]
