# -*- coding: utf-8 -*-
"""AI 三大件读取：作战小队 TeamType / 特遣部队 TaskForce / 动作脚本 ScriptType。

★2026-10-18 全部在本机 `SAVE0029.SAV` 上逐条核对地图 INI 定案（见主文档 §118）。

三个类在存档里都是「类 GUID + 定长记录」的连续块，头部完全同构：

    偏移    内容
    +0x00   16 B 类 GUID
    +0x10   自键（u32，被别处用指针引用）
    +0x14   4 个指针（运行时）
    +0x24   ffffffff
    +0x38   8 B ASCII 的 ID（如 `01000024`；少数对象这里是 GUID 风格串）
    +0x78   名字（40 B，GBK 或 UTF-8，0 结尾）

各类的差异段：

    TeamType   268 B  +0xAC 全局序号  +0xB0 Group  +0xB4 老兵等级
                      +0xB8/+0xBC/+0xC0/+0xC4 布尔标志位（见 FLAG_BITS）
                      +0xC8 Max  +0xCC Priority  +0xD8 House 指针
                      +0xF4 ScriptType 指针  +0xF8 TaskForce 指针
    TaskForce  232 B  +0xAC Group  +0xB0 成员种数  +0xB8 起 (数量, 型号键) ×6
    ScriptType 584 B  +0xAC 全局序号  +0xB4 条目数  +0xB8 起 (动作, 参数) ×N

核对结果：ID/名字/Group/老兵/Max/Priority/House/Script/TaskForce 指针
`223/223`；TaskForce 成员种数 `144/144`；脚本条目数与动作-参数 `151/151`。
"""
import struct

try:
    from fa2_tables import script_action_name, script_action_ptype
except ImportError:                       # 打包/独立运行时
    from core.fa2_tables import script_action_name, script_action_ptype

AI_CLASSES = {
    'teamtype': (bytes.fromhex('4ea6dbd17807d211aca5006008055bb5'), 268),
    'taskforce': (bytes.fromhex('1e34de617407d211aca5006008055bb5'), 232),
    'scripttype': (bytes.fromhex('47a6f3428907d211aca5006008055bb5'), 584),
}

# ★★2026-10-18 23:5x 定案（用 `新建文件夹 (23)\all01umd.map` 的 223 条 Waypoint 逐条对账，
#   223/223 命中）：`+0xE8` = **小队出现于的路径点号**（0 起；字母 A=0, B=1 … CR=95 …）。
TEAM_WP_OFF = 0xE8

# ★★2026-10-19 00:1x 定案（A/B 存档逐字节对照）：`+0xB9` = **预装载**（0/1）。
#   证据：玩家把「预装载小队」打开后存的一份档（E://YURI//SAVE4823.SAV）与同图同小队的
#   未打开档（E://YURI//SAVE0029.SAV）**整条 268B 记录只差这一字节**（其余差异都是运行期指针重分配）。
TEAM_LOADABLE_OFF = 0xB9

NAME_OFF = 0x78
NAME_LEN = 0x28
ID_OFF = 0x38

# TeamType 布尔标志位（bit → 名字）；来源=逐位与地图 INI 的 yes/no 向量比对
FLAG_BITS = {
    # ★★2026-10-19 00:1x **A/B 实证**：玩家给的 `E://YURI//SAVE4823.SAV`（预装载已打开）与
    #   `E://YURI//SAVE0029.SAV`（同图、同小队、预装载关闭）**只差 +0xB9 这 1 个字节（00→01）**
    #   ⇒ 这个字节就是 FA2「作战小队」里的**预装载**开关。
    #   注：all01umd.map 的 `Full` 向量与这一列 223/223 完全重合 ⇒ 地图 INI 那边它叫 `Full`，
    #   而 FA2 的复选框叫「预装载」；以游戏行为为准，工具里统一叫「预装载」。
    (0xB8, 8): '预装载',
    (0xBC, 0): 'Recruiter',
    (0xBC, 24): 'Reinforce',
    (0xC0, 24): 'Suicide/AvoidThreats',
    (0xC4, 0): 'Droppod',
    (0xC4, 8): 'UseTransportOrigin',
}

TF_MAX = 6          # 特遣部队最多 6 种型号


def _dec(b):
    """名字字段解码。★名字只有 40 字节，长中文名会在**双字节字符中间**被截断，
    严格解码必然失败 ⇒ 退回 `gbk`+`replace`，把尾部残字去掉并补 '…'。
    """
    s = b.split(b'\x00')[0]
    for enc in ('utf-8', 'gbk'):
        try:
            return s.decode(enc)
        except UnicodeDecodeError:
            continue
    t = s.decode('gbk', 'replace')
    cut = False
    while t and t[-1] == '\ufffd':
        t, cut = t[:-1], True
    return t + ('…' if cut else '')


def _walk(pl, guid, size, limit=6000):
    """走完一个类的连续块 → [记录起点偏移]。

    判据：起点处 16 B 是类 GUID，且 +0x38 是 8 字节可打印 ASCII（ID）。
    起点用「第一个满足判据的 GUID 位置」，之后按 `size` 步进并校验 GUID 未断。
    """
    start = None
    i = 0
    n = len(pl)
    while True:
        j = pl.find(guid, i)
        if j < 0:
            break
        i = j + 1
        s = pl[j + ID_OFF:j + ID_OFF + 8]
        if len(s) == 8 and all(32 <= c < 127 for c in s):
            start = j
            break
    if start is None:
        return []
    out = []
    o = start
    while o + size <= n and pl[o:o + 16] == guid and len(out) < limit:
        out.append(o)
        o += size
    return out


def read_ai(doc):
    """从 SaveDoc 读三张表 → {'teams': [...], 'taskforces': [...], 'scripts': [...]}。

    每项都带 `off`（明文偏移）、`id`、`name`，并把指针解析成对端名字。
    """
    pl = doc.plain
    if isinstance(pl, bytearray):
        pl = bytes(pl)
    # 型号键 → 型号名（单位 + 建筑；诊断用，解不出就显示键）
    tname = {}
    try:
        for k, v in doc.unit_types().items():
            tname[k] = v
    except Exception:
        pass
    try:
        for nm, k in doc.building_types().items():
            tname.setdefault(k, nm)
    except Exception:
        pass
    try:
        hname = doc._house_key_names()
    except Exception:
        hname = {}

    scripts, tfs = [], []
    for o in _walk(pl, *AI_CLASSES['scripttype']):
        cnt = struct.unpack_from('<i', pl, o + 0xB4)[0]
        cnt = max(0, min(cnt, 60))
        ent = []
        for k in range(cnt):
            a, arg = struct.unpack_from('<ii', pl, o + 0xB8 + 8 * k)
            # ★2026-10-19：脚本里的动作号是**脚本动作表**（3=移动到路径点），
            #   与触发器「行为」表（3=生产开始）是两套编号，绝不能混用。
            ent.append({'action': a, 'arg': arg,
                        'name': script_action_name(a),
                        'ptype': script_action_ptype(a)})
        sid_ = _dec(pl[o + ID_OFF:o + ID_OFF + 8])
        scripts.append({'off': o, 'id': sid_, 'is_map': sid_.startswith('010'),
                        'name': _dec(pl[o + NAME_OFF:o + NAME_OFF + NAME_LEN]),
                        'key': struct.unpack_from('<I', pl, o + 16)[0], 'entries': ent})
    for o in _walk(pl, *AI_CLASSES['taskforce']):
        n = struct.unpack_from('<i', pl, o + 0xB0)[0]
        n = max(0, min(n, TF_MAX))
        mem = []
        for k in range(n):
            c, tk = struct.unpack_from('<Ii', pl, o + 0xB8 + 8 * k)
            mem.append({'count': c, 'type_key': tk & 0xFFFFFFFF,
                        'type': tname.get(tk & 0xFFFFFFFF, '#%08X' % (tk & 0xFFFFFFFF))})
        tid_ = _dec(pl[o + ID_OFF:o + ID_OFF + 8])
        tfs.append({'off': o, 'id': tid_, 'is_map': tid_.startswith('010'),
                    'name': _dec(pl[o + NAME_OFF:o + NAME_OFF + NAME_LEN]),
                    'key': struct.unpack_from('<I', pl, o + 16)[0],
                    'group': struct.unpack_from('<i', pl, o + 0xAC)[0],
                    'members': mem})
    tf_by_key = {t['key']: t for t in tfs}
    sc_by_key = {s['key']: s for s in scripts}

    teams = []
    for o in _walk(pl, *AI_CLASSES['teamtype']):
        flags = {}
        for (base, bit), nm in FLAG_BITS.items():
            v = struct.unpack_from('<I', pl, o + base)[0]
            flags[nm] = bool((v >> bit) & 1)
        tkey = struct.unpack_from('<I', pl, o + 0xF8)[0]
        skey = struct.unpack_from('<I', pl, o + 0xF4)[0]
        hkey = struct.unpack_from('<I', pl, o + 0xD8)[0]
        tf = tf_by_key.get(tkey)
        sc = sc_by_key.get(skey)
        if sc is not None and not sc['entries']:
            sc = None          # ★空脚本 = 引擎的占位对象（地图里 Script 为空的那些）⇒ 当作「无脚本」
        tid = _dec(pl[o + ID_OFF:o + ID_OFF + 8])
        teams.append({
            'off': o, 'id': tid, 'is_map': tid.startswith('010'),
            'name': _dec(pl[o + NAME_OFF:o + NAME_OFF + NAME_LEN]),
            'key': struct.unpack_from('<I', pl, o + 16)[0],
            'group': struct.unpack_from('<i', pl, o + 0xB0)[0],
            'waypoint': struct.unpack_from('<i', pl, o + TEAM_WP_OFF)[0],
            'loadable': bool(pl[o + TEAM_LOADABLE_OFF]),
            'veteran': struct.unpack_from('<i', pl, o + 0xB4)[0],
            'max': struct.unpack_from('<i', pl, o + 0xC8)[0],
            'priority': struct.unpack_from('<i', pl, o + 0xCC)[0],
            'house': hname.get(hkey, '#%08X' % hkey),
            'flags': flags,
            'tf_key': tkey, 'script_key': skey,
            'taskforce': tf['name'] if tf else None,
            'taskforce_id': tf['id'] if tf else None,
            'script': sc['name'] if sc else None,
            'script_id': sc['id'] if sc else None,
            'scripts_n': len(sc['entries']) if sc else None,
            'members': tf['members'] if tf else [],
        })
    return {'teams': teams, 'taskforces': tfs, 'scripts': scripts}


# ==================== 写入（2026-10-18 第一步：只改「老兵等级」这一个定长字段） ====================
# ★★2026-10-18 23:0x **游戏内验收通过**（SAVE4823 的 vet2 / vet3 两份样本）：
#   玩家看到「一个是一级、一个是三级」⇒
#       VeteranLevel **2 → 一级（老兵，1 个箭头）**
#       VeteranLevel **3 → 三级（精英，3 个箭头）**
#   ⇒ 这个字段是**从 1 起算**的等级索引：`1=新兵(无星) / 2=老兵(一级) / 3=精英(三级)`，
#     换算成单位记录里的 `+0x150` 就是 `VeteranLevel − 1`（本任务地图里小队全写 3 = 全精英）。
#   ⚠ 0 未实测（预计=未定义/新兵），不要主动写。
TEAM_VET_OFF = 0xB4          # TeamType.VeteranLevel（int，1..3；0 未实测）
# 人话名字（★游戏内验收：2=一级、3=三级）
TEAM_VET_NAMES = {1: '新兵', 2: '老兵(一级)', 3: '精英(三级)'}
TEAM_MAX_OFF = 0xC8
TEAM_PRI_OFF = 0xCC


def set_team_veteran(doc, team_id, level):
    """把某个**作战小队**的老兵等级改成 `level`（0..3），返回改动信息。

    ⚠ 只写 `+0xB4` 这 4 个字节，长度不变 ⇒ 不会触发容器重排（7.14 MB 红线无关）。
    ⚠ 改的是**定义**（TeamTypeClass）。若该小队的 Team 实例已经刷出来了，
      引擎可能仍用实例里的旧值 ⇒ 需要"读档后才会重新刷"的情况请新开一局验证。
    """
    lv = int(level)
    if not (0 <= lv <= 3):
        raise ValueError('老兵等级只能是 1..3（0 未实测），收到 %r' % (level,))
    ai = read_ai(doc)
    t = next((x for x in ai['teams'] if x['id'] == str(team_id)), None)
    if t is None:
        raise KeyError('这份存档里没有 ID 为 %r 的作战小队（共 %d 个）'
                       % (team_id, len(ai['teams'])))
    pl = doc.plain
    if not isinstance(pl, bytearray):
        raise RuntimeError('doc.plain 不是可写 bytearray，无法改字节')
    off = t['off'] + TEAM_VET_OFF
    old = struct.unpack_from('<i', pl, off)[0]
    struct.pack_into('<i', pl, off, lv)
    # 缓存失效：等级变了，之前读出来的 teams 表作废
    for attr in ('_ai_cache', '_ai'):
        if hasattr(doc, attr):
            try:
                setattr(doc, attr, None)
            except Exception:
                pass
    return {'team_id': t['id'], 'name': t['name'], 'off': off,
            'old': old, 'new': lv, 'house': t['house'],
            'taskforce': t['taskforce'], 'script': t['script']}


# ==================== 写入第二步：特遣部队成员（数量 / 型号，定长槽位） ====================
TF_COUNT_OFF = 0xB0          # 成员**种数**（1..6）
TF_MEM_OFF = 0xB8            # 成员数组：(数量, 型号键) ×6，每项 8 字节


def _type_index(doc):
    """{型号名(大写): 类型键} —— 单位 + 建筑。"""
    out = {}
    try:
        for k, v in doc.unit_types().items():
            out[str(v).upper()] = k
    except Exception:
        pass
    try:
        for nm, k in doc.building_types().items():
            out.setdefault(str(nm).upper(), k)
    except Exception:
        pass
    return out


def set_taskforce_member(doc, tf_id, index, count=None, type_name=None, type_key=None,
                         allow_new=False):
    """改特遣部队第 `index` 项（0 起）：数量 和/或 型号。返回改动信息。

    ★2026-10-18：只写 `+0xB8 + 8×index` 这 8 个字节里的对应 dword，**记录长度不变**。
      - `count`     ：该型号要几个（>=0；0 = 不放该型号，槽位仍留着）
      - `type_name` ：型号名（如 `E1` / `APOC`），在档内的类型表里查键
      - `type_key`  ：直接给键（优先于 type_name）
      - `allow_new` ：允许写**超出当前种数**的空槽位（配合 `set_taskforce_size()` 加项用）
    改「种类数」（`+0xB0`）请用 `set_taskforce_size()`。
    """
    ai = read_ai(doc)
    tf = next((x for x in ai['taskforces'] if x['id'] == str(tf_id)), None)
    if tf is None:
        raise KeyError('这份存档里没有 ID 为 %r 的特遣部队（共 %d 个）'
                       % (tf_id, len(ai['taskforces'])))
    idx = int(index)
    n = len(tf['members'])
    if not (0 <= idx < n):
        if not (allow_new and 0 <= idx < TF_MAX):
            raise IndexError('第 %d 项不存在（这条特遣部队只有 %d 项；要新增请 allow_new=True）'
                             % (idx, n))
    pl = doc.plain
    if not isinstance(pl, bytearray):
        raise RuntimeError('doc.plain 不是可写 bytearray')
    base = tf['off'] + TF_MEM_OFF + 8 * idx
    old_cnt = struct.unpack_from('<i', pl, base)[0]
    old_key = struct.unpack_from('<I', pl, base + 4)[0]
    new_key = old_key
    if type_key is not None:
        new_key = int(type_key) & 0xFFFFFFFF
    elif type_name:
        ix = _type_index(doc)
        k = ix.get(str(type_name).strip().upper())
        if k is None:
            raise KeyError('档里找不到型号 %r 的类型对象（已知 %d 个）'
                           % (type_name, len(ix)))
        new_key = k
    new_cnt = old_cnt if count is None else int(count)
    if new_cnt < 0:
        raise ValueError('数量不能为负（收到 %r）' % (count,))
    if count is not None:
        struct.pack_into('<i', pl, base, new_cnt)
    if new_key != old_key:
        struct.pack_into('<I', pl, base + 4, new_key)
    return {'tf_id': tf['id'], 'name': tf['name'], 'index': idx, 'off': base,
            'old_count': old_cnt, 'new_count': new_cnt,
            'old_key': old_key, 'new_key': new_key,
            'changed': (new_cnt != old_cnt) or (new_key != old_key)}


def set_taskforce_size(doc, tf_id, n, zero_tail=True):
    """改特遣部队的成员**种数**（`+0xB0`，1..6）。只写这 4 个字节，不动槽位内容。

    ★★2026-10-18 23:3x **实测定案**（SAVE4823 三份对照）：
      - 只把 n 从 2 改成 1 ⇒ 天启就不刷了 ⇒ **引擎按 n 截断读取**；
      - 把第 1 项数量改 0（n 不动）⇒ 天启也不刷 ⇒ 两种办法都有效。
      ⇒ 默认 `zero_tail=True`：n 变小时顺手把 `n..6` 槽位的**数量清零**（双保险，
        总代价 4·(6-n) 字节，长度不变）。加项时不动这些槽位（新槽位由调用者写）。
    """
    ai = read_ai(doc)
    tf = next((x for x in ai['taskforces'] if x['id'] == str(tf_id)), None)
    if tf is None:
        raise KeyError('这份存档里没有 ID 为 %r 的特遣部队' % (tf_id,))
    n = int(n)
    if not (0 <= n <= 6):
        raise ValueError('种数只能是 0..6（0 = 空特遣，等于删掉这个小队，收到 %r）' % (n,))
    pl = doc.plain
    if not isinstance(pl, bytearray):
        raise RuntimeError('doc.plain 不是可写 bytearray')
    off = tf['off'] + TF_COUNT_OFF
    old = struct.unpack_from('<i', pl, off)[0]
    struct.pack_into('<i', pl, off, n)
    zeroed = 0
    if zero_tail and n < old:
        for i in range(max(0, n), 6):
            p = tf['off'] + TF_MEM_OFF + 8 * i
            if struct.unpack_from('<i', pl, p)[0]:
                struct.pack_into('<i', pl, p, 0)
                zeroed += 1
    return {'tf_id': tf['id'], 'name': tf['name'], 'off': off, 'old': old, 'new': n,
            'members': len(tf['members']), 'zeroed': zeroed}


def clear_taskforce(doc, tf_id, zero_keys=False):
    """把一条特遣部队**清空**（= 变相删掉用它的那个小队）。

    ★2026-10-18 23:3x 玩家实测结论：
      - 把特遣部队里的兵种**全部删掉** ⇒ 小队刷不出任何东西，**不报错**，效果就是"没有这个小队"；
      - 但**直接删特遣部队对象** ⇒ **报错**（因为 TeamType 用指针引用它，删了就是悬空指针）。
    做法：6 个槽位的**数量全清零** + 种数写 0（两条路都堵死，引擎按 n 读或读满 6 槽都不出兵）。
    `zero_keys=True` 时连型号键一起清零（更彻底，但没必要 —— 默认保留，便于日后恢复）。
    """
    ai = read_ai(doc)
    tf = next((x for x in ai['taskforces'] if x['id'] == str(tf_id)), None)
    if tf is None:
        raise KeyError('这份存档里没有 ID 为 %r 的特遣部队' % (tf_id,))
    pl = doc.plain
    if not isinstance(pl, bytearray):
        raise RuntimeError('doc.plain 不是可写 bytearray')
    before = [(struct.unpack_from('<iI', pl, tf['off'] + TF_MEM_OFF + 8 * i))
              for i in range(6)]
    for i in range(6):
        p = tf['off'] + TF_MEM_OFF + 8 * i
        struct.pack_into('<i', pl, p, 0)
        if zero_keys:
            struct.pack_into('<I', pl, p + 4, 0)
    struct.pack_into('<i', pl, tf['off'] + TF_COUNT_OFF, 0)
    return {'tf_id': tf['id'], 'name': tf['name'], 'off': tf['off'],
            'old_n': len(tf['members']), 'before': before}


# ==================== 写入第三步：小队所属国家（House 指针 +0xD8） ====================
TEAM_HOUSE_OFF = 0xD8        # TeamType.House —— **指向 House 对象的键**（223/223 与地图 INI 对上）


def set_team_house(doc, team_id, house):
    """把作战小队的所属国家改成 `house`（阵营名，如 'Russians'；或直接给 House 键）。

    ★只写 `+0xD8` 这 4 个字节。**必须**换成"本档存在的 House 对象键"，否则就是悬空指针
      （加载器的指针修复归并器会炸）⇒ 这里强制校验，查不到直接报错、不写。
    ★改完后该小队刷出来的兵**归这个国家**（颜色/归属/敌我关系都跟着变）。
    """
    ai = read_ai(doc)
    t = next((x for x in ai['teams'] if x['id'] == str(team_id)), None)
    if t is None:
        raise KeyError('这份存档里没有 ID 为 %r 的作战小队（共 %d 个）'
                       % (team_id, len(ai['teams'])))
    try:
        hn = doc._house_key_names()
    except Exception:
        hn = {}
    if not hn:
        raise RuntimeError('读不到本档的 House 对象表，拒绝写入（避免悬空指针）')
    new_key = None
    if isinstance(house, int):
        new_key = house & 0xFFFFFFFF
        if new_key not in hn:
            raise KeyError('键 %#010x 不是本档的 House 对象' % new_key)
    else:
        q = str(house).strip().lower()
        for k, v in hn.items():
            if str(v).lower() == q:
                new_key = k
                break
        if new_key is None:
            raise KeyError('认不出国家 %r（可选：%s）'
                           % (house, '、'.join(sorted(str(v) for v in hn.values()))))
    pl = doc.plain
    if not isinstance(pl, bytearray):
        raise RuntimeError('doc.plain 不是可写 bytearray')
    off = t['off'] + TEAM_HOUSE_OFF
    old_key = struct.unpack_from('<I', pl, off)[0]
    old_name = hn.get(old_key, '#%08X' % old_key)
    struct.pack_into('<I', pl, off, new_key)
    return {'team_id': t['id'], 'name': t['name'], 'off': off,
            'old_key': old_key, 'old_house': old_name,
            'new_key': new_key, 'new_house': hn.get(new_key, '?')}


# ==================== 写入第四步：小队「出现于」的路径点（+0xE8） ====================
TEAM_WP_LIMIT = 701          # 与 waypoint_limit() 一致：0..701


def set_team_waypoint(doc, team_id, wp, ensure_cell=None):
    """改作战小队「出现于」的路径点号（`+0xE8`，0..701），返回改动信息。

    ★数值就是**存档路径点表的序号**（`waypoints_save()` 同一套编号），
      地图 INI 里 `Waypoint=A` 就是 0、`B`=1 ……（本次用 223 条对账，223/223 命中）。
    `ensure_cell=(x, y)`：先把该路径点设成这个格子（零增长 `set_waypoint`），
      再把小队指过去 —— 目标路径点没定义（(0,0)）时刷兵会出现在地图角上，所以要一起设。

    ★★2026-10-19 00:0x 玩家定案（重要）：
      **只有「援军(作战小队)」行为 7** 刷出来的小队才用这里填的路径点；
      **「超时空传送作战小队」行为 107 会以触发里的路径点为准**，游戏直接忽略小队上的设置
      ⇒ 遇到「改了没反应」先确认该小队是哪条行为刷的。
    """
    ai = read_ai(doc)
    t = next((x for x in ai['teams'] if x['id'] == str(team_id)), None)
    if t is None:
        raise KeyError('这份存档里没有 ID 为 %r 的作战小队（共 %d 个）'
                       % (team_id, len(ai['teams'])))
    wp = int(wp)
    if not (0 <= wp <= TEAM_WP_LIMIT):
        raise ValueError('路径点号只能是 0..%d（收到 %r）' % (TEAM_WP_LIMIT, wp))
    pl = doc.plain
    if not isinstance(pl, bytearray):
        raise RuntimeError('doc.plain 不是可写 bytearray')
    cell = None
    if ensure_cell is not None:
        x, y = int(ensure_cell[0]), int(ensure_cell[1])
        old = None
        try:
            old = doc.waypoint_cell(wp)
        except Exception:
            old = None
        if old != (x, y):
            doc.set_waypoint(wp, x, y)
            cell = (x, y)
    off = t['off'] + TEAM_WP_OFF
    old_wp = struct.unpack_from('<i', pl, off)[0]
    struct.pack_into('<i', pl, off, wp)
    try:
        now_cell = doc.waypoint_cell(wp)
    except Exception:
        now_cell = None
    return {'team_id': t['id'], 'name': t['name'], 'off': off,
            'old': old_wp, 'new': wp, 'cell': now_cell, 'cell_written': cell}


# ==================== 写入第五步：小队「预装载」（+0xB9） ====================
def set_team_loadable(doc, team_id, on=True):
    """开/关作战小队的**预装载**（`+0xB9`，0/1）。只写 1 个字节，记录长度不变。

    ★语义（玩家实测）：打开后，该小队刷出来时**直接装在运输工具里**（配合小队里的载具）。
    ★定位证据见 `TEAM_LOADABLE_OFF` 注释（A/B 存档逐字节对照）。
    """
    ai = read_ai(doc)
    t = next((x for x in ai['teams'] if x['id'] == str(team_id)), None)
    if t is None:
        raise KeyError('这份存档里没有 ID 为 %r 的作战小队（共 %d 个）'
                       % (team_id, len(ai['teams'])))
    pl = doc.plain
    if not isinstance(pl, bytearray):
        raise RuntimeError('doc.plain 不是可写 bytearray')
    off = t['off'] + TEAM_LOADABLE_OFF
    old = pl[off]
    new = 1 if on else 0
    pl[off] = new
    return {'team_id': t['id'], 'name': t['name'], 'off': off,
            'old': old, 'new': new}


# ==================== 删除特遣部队的第 i 项（槽位前移） ====================
def remove_taskforce_member(doc, tf_id, index):
    """删掉特遣部队第 `index` 项：后面各项**向前补位**，末位清零，种数 −1。

    ★只动 `+0xB0` 与 `+0xB8` 起的 6 个槽位，记录长度不变（232 B 定长）。
    ★引擎按种数 `+0xB0` 截断读取（2026-10-18 实测），所以前移 + 减一即可，不会留空洞。
    """
    ai = read_ai(doc)
    tf = next((x for x in ai['taskforces'] if x['id'] == str(tf_id)), None)
    if tf is None:
        raise KeyError('这份存档里没有 ID 为 %r 的特遣部队' % (tf_id,))
    pl = doc.plain
    if not isinstance(pl, bytearray):
        raise RuntimeError('doc.plain 不是可写 bytearray')
    n = len(tf['members'])
    idx = int(index)
    if not (0 <= idx < n):
        raise IndexError('第 %d 项不存在（这条特遣部队只有 %d 项）' % (idx, n))
    base = tf['off'] + TF_MEM_OFF
    moved = 0
    for k in range(idx, n - 1):
        struct.pack_into('<Ii', pl, base + 8 * k, *struct.unpack_from('<Ii', pl, base + 8 * (k + 1)))
        moved += 1
    struct.pack_into('<Ii', pl, base + 8 * (n - 1), 0, 0)      # 末位清零
    struct.pack_into('<i', pl, tf['off'] + TF_COUNT_OFF, n - 1)
    return {'tf_id': tf['id'], 'name': tf['name'], 'index': idx,
            'old_n': n, 'new_n': n - 1, 'moved': moved,
            'removed': tf['members'][idx]}
