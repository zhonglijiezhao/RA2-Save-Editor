# -*- coding: utf-8 -*-
"""RA2/YR 存档编辑 — 业务核心层（纯 Python，无 GUI 依赖，可单独测试）

对外 API:
    doc = SaveDoc(path)          # 打开并解码
    doc.info                     # dict: 任务名/阵营/时间/金钱...
    doc.records                  # list[Rec]: 触发/Tag 记录
    doc.set_money(v)             # 改玩家阵营金钱
    doc.houses() / set_house_money(short, v)   # 全部所属方金钱（12 阵营）
    doc.toggle_trigger(rec, on)  # 启用/禁用触发
    doc.events()  / set_event(ev, etype=, arg=)    # 事件（对象签名定位）
    doc.actions() / set_action(ac, atype=, arg=)   # 行为（对象签名定位）
    doc.save(out_path)           # 重编码 + CFB 重建落盘
"""
import sys, os, re, struct, hashlib, datetime, shutil, json, bisect, base64, collections

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from cfb import CFB                      # noqa: E402
from lzo1x import lzo1x_decompress       # noqa: E402
from lzo_compress import compress        # noqa: E402
import cfb_surgery                       # noqa: E402
import cfb_write                         # noqa: E402  (从零重建容器的写入器)
from fa2_tables import (EVENT_TYPES, ACTION_TYPES,     # noqa: E402
                        event_name, event_ptype, action_name, action_ptype,
                        event_note, action_note,
                        SCRIPT_ACTIONS, SCRIPT_PARAM_TYPES,
                        script_action_name, script_action_ptype)
from map_ini import MapIni, find_maps                  # noqa: E402

# ---- 事件 / 行为对象：用「对象签名」确定性定位（2026-10-13 定案）----
# 每个事件/行为对象体内含 16 字节签名（跨档稳定，58 档批量验证 100% 命中）：
#   obj+0 : 4F0EC392/4F0EC393   obj+4 : 11D20A55
#   obj+8 : 6000A7AC            obj+12: B55B0508
# 布局：类型在 obj+64、参数在 obj+72（对外报的「记录起点」即 obj+64）。
# 数组 = `[行为对象…][4 字节][事件对象…]`；**行为对象固定 168 字节**，
# **事件对象尺寸随档变化**（108 / 124，实测）⇒ 禁止用固定步长拼链。
OBJ_SIG = struct.pack('<III', 0x11D20A55, 0x6000A7AC, 0xB55B0508)
OBJ_TYPE_OFF = 64
OBJ_ARG_OFF = 72
ACTION_SIZE = 168

# ---- §175 行为 56/57（设置 / 清除局部变量）的**变量号不在 +72，而在 +164** ----
# 证据（gamemd.exe 静态反汇编，2026-10-22）：
#   ActionClass::Execute 分派 = `edx = [esi+0x2C] - 1; cmp edx,0x90; jmp [edx*4+0x6DFDEC]`
#     （0x6DD8D7 起；表索引 = 行为类型 − 1）
#   表[55] → 0x6DF1DE（case 56）: `mov ecx,[esi+0x90]` → `push 1` → SetLocalVariable(idx, 1)
#   表[56] → 0x6DF201（case 57）: `mov edx,[esi+0x90]` → SetLocalVariable(idx, <值>)
#   ⇒ 运行期字段 +0x90 ↔ 存档记录 +0xA4（差 0x14 记录头，与 类型 +0x2C↔+0x40 同理）
# ⚠ 写错到 +72 时引擎读 +164 得 0 ⇒ **永远去设置变量 0**，看起来"设了但检查的那个变量没动"。
ACT_VAR_OFF = 164
ACTION_VAR_TYPES = (56, 57)

# ---- §175 局部变量表（ScenarioClass 里的定长槽数组）----
# 引擎侧（gamemd.exe）：
#   GetLocalVariable 0x689A00: `if (i >= 0x64) return false;`
#       `al = [this + i*41 + 0x24B2]`      ← 值（1 字节）
#   FindLocalVariableByName 0x689AC0 / 0x689A30: 遍历 100 槽，步长 0x29，名字在 `this + i*41 + 0x248A`
#   SetLocalVariable 0x689910: 写 `[this + i*41 + 0x24B2]`，若值有变则 `this[0x34AA]=1` 并 call 0x6E5820(i)
#   LoadLocalVariableNames 0x689B20: 清空 100 槽后按 `[VariableNames]` 的 01..99 依次填名字
# ⇒ 槽 = **name[40] + value(u8) = 41B**，共 **100 槽（索引 0..99）**，明文基址 **0x248A**。
#   FA2 名称框里的"第 N 个"（1..99）对应引擎索引 **N-1**（事件/行为参数就是引擎索引）。
LOCALVAR_BASE = 0x248A
LOCALVAR_SLOT = 0x29
LOCALVAR_NAME_LEN = 40
LOCALVAR_COUNT = 100

# ★★★2026-10-22 勘误（§176）：**存档自足 —— 读档不会被地图覆盖**（玩家实测：删掉地图
#   存档照样能读、触发照样执行；本工具整条管线也证明了存档自带全部触发/路径点/变量状态）。
#   地图 `[VariableNames]` 只在**读地图 / 开局**时被消费，不是读档路径：
#     `LoadLocalVariableNames 0x689B20`：
#        ① 先把 100 槽的**名字首字节**逐槽清 0（`mov byte [eax],0; add eax,0x29` × 100）；
#        ② 按 `[VariableNames]` 条目数 count（`0x526960` 取键数，≥0x64 截 100）循环：
#           键 → atoi 得**引擎索引**，值串 `Name,Default` → 名字写槽首、默认值写槽尾
#           （`mov byte [ebx+0x24B2], setne(atoi(...))`）。
#     ★它**唯一的调用者**在 `0x68A734`，紧跟其后就是写 `Ranking/ParTimeEasy/ParTimeMedium`
#       （`0x52A760`）—— 那是**地图读/写**（ScenarioClass::Read/WriteScenario）路径，
#       ⇒ 这份表是"地图里的变量定义"，不是"读档时覆盖存档"。
#   所以变量有两处，各管一段（工具两边都写）：
#     · 存档 0x248A 的 100 槽 = **运行期状态**（权威；事件 36 判的就是它）
#     · 地图 `[VariableNames]`（`i=Name,Default`）= **地编里能选中的变量清单 + 开局默认值**
#   玩家原话：「A 变量是我把默认值从 0 改成了 1，所以 AAAAA 的写法一开局就成立；
#              B 默认 0，所以要先 BBBBB 改值，CCCCC 再检测 B=1」。
MAPVAR_SECTION = 'VariableNames'
SCENARIO_EXTS = ('.map', '.mmx', '.yrm', '.mpr')

# ---- §181 ★★★★★「纯电平查询」事件必须挂计时心跳（T10 定案，2026-10-22）----
# 机制（工具里早就写着、但直到 T10 才在局部变量上闭环验证）：
#   create_trigger: `ticks = ev_arg*LOGIC_FPS if ev_type==13 else (15 if ev_type==8 else 0)`
#     （2026-10-24 玩家定案：事件 8「任何事件」帧数**必须 15**；写 0 肯定不评估）
#   `ticks > 0` 才把 CellTag 自键**登记进「定时触发器挂接表」**
#   ⇒ 表外的触发**读档后没有人驱动**，只有靠"对象/标签回调"才会被评估。
# 事件分两类：
#   · 回调型：1 进入 / 7 摧毁 / 29 被摧毁或占领 / 48 被任何事物摧毁 …（有对象触发回调）
#   · **轮询型：36 局部变量≠0 / 37 局部变量==0** —— 纯电平查询，没有回调可依赖
# ⇒ 36/37 若不伴随计时事件 ⇒ 永不评估（读档后完全不动，且工具/FA2 里看着一切正常）。
# T8/T9/T10 实测（每一条都与本模型吻合）：
#   T8-C 13/9s+36(0) 出 ；T8-F 36(3) 单出 ✗ ；T9-P1 13/6s+36(3) 出 ；T9-P4 纯 36 ✗ ；
#   T10-Q1 纯 36 ✗ / Q2 13+36 ✓ / Q3 13 ✓（同格可建）⇒ 唯一差别就是"挂不挂计时事件"。
#   T10 档的「定时触发器挂接表」实测只有 2 个键 = Q2+Q3 的 CellTag，**Q1 不在表里**。
POLL_EVENT_TYPES = frozenset((36, 37))
HEARTBEAT_EVENT = 13          # 「流逝时间」—— 最省心的心跳（arg = 秒）
HEARTBEAT_SEC = 1             # 心跳秒数：1 秒起，对"全部满足才执行"的逻辑无副作用


def _cfg_span(raw, sec):
    """字节流里定位 `[sec]` 段 → `(头起点, 内容起点, 内容终点)`；没有则 None。

    内容终点 = 下一个 `\\r\\n[` / `\\n[` 的换行处 ⇒ 段尾那个空行保持不动。
    """
    tag = ('[' + sec + ']').encode('ascii')
    i = raw.lower().find(tag.lower())
    if i < 0:
        return None
    b = i + len(tag)
    while b < len(raw) and raw[b:b + 1] in (b'\r', b'\n'):
        b += 1
    j = len(raw)
    for pat in (b'\r\n[', b'\n['):
        k = raw.find(pat, b)
        if 0 <= k < j:
            j = k
    return i, b, j


def parse_scenario_variables(raw):
    """解析地图 `[VariableNames]` 段 → `[(idx, name, default), …]`（按 idx 升序）。

    条目形如 `0=A,1`（引擎索引 = 键；名字与默认值用逗号隔开，默认值缺省按 0）。
    """
    sp = _cfg_span(raw, MAPVAR_SECTION)
    if not sp:
        return []
    body = raw[sp[1]:sp[2]].decode('latin1', 'replace')
    items = []
    for ln in body.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        ln = ln.strip()
        if not ln or ln.startswith(';') or '=' not in ln:
            continue
        k, v = ln.split('=', 1)
        try:
            idx = int(k.strip(), 10)
        except ValueError:
            continue
        nm, dv = v, '0'
        if ',' in v:
            nm, dv = v.split(',', 1)
        nm = nm.strip()
        try:
            ival = int(float(dv.strip() or '0'))
        except ValueError:
            ival = 0
        items.append((idx, nm, ival & 0xFF))
    return sorted(items, key=lambda t: t[0])


def read_scenario_variables(path):
    """读地图文件的局部变量定义 → `{'path','items','exists'}`。"""
    if not path or not os.path.exists(path):
        return {'path': path, 'items': [], 'exists': False}
    raw = open(path, 'rb').read()
    return {'path': path, 'items': parse_scenario_variables(raw), 'exists': True}


# ★★★★★ 2026-10-22 §191：**FA2sp 的 `[Digest]` 校验**
#   地图末尾有 `[Digest]` / `1=<base64>`，值是：
#       base64( sha1( 整个地图文件去掉 [Digest] 段、并把 CRLF 换成 LF ) )
#   （两个不同版本的地图都逐字节复现成功 ⇒ 算法确认）
#   游戏/FA2 靠它判断"地图是否被外部改过"。我们改 `[VariableNames]` 之后**必须同步重算**，
#   否则玩家会看到「地图被人修改」的提示（玩家实踩）。
_MAP_DIGEST_SECTION = 'Digest'


def map_digest_of(raw):
    """算一份地图应有的 `[Digest]` 值（base64 字符串）；地图没有该段也照算。"""
    m = re.search(rb'\[Digest\]\r?\n[^\r\n]*\r?\n?', raw)
    body = (raw[:m.start()] + raw[m.end():]) if m else raw
    return base64.b64encode(hashlib.sha1(body.replace(b'\r\n', b'\n')).digest()).decode()


def refresh_map_digest(raw):
    """把 `[Digest]` 段的值改成本次内容对应的正确值（没有该段就原样返回）。

    ★★2026-10-27 铁律之后：**本工具不再写地图**，这个函数只留下"如果哪天真要算"的
      算法说明用途 —— `write_scenario_variables` 已改成直接抛 `MapWriteForbidden`。
    """
    m = re.search(rb'(\[Digest\]\r?\n)1=([A-Za-z0-9+/=]+)', raw)
    if not m:
        return raw
    want = map_digest_of(raw).encode()
    return raw[:m.start(2)] + want + raw[m.end(2):]


# ★★★★★★ 铁律（玩家 2026-10-27 00:20 原话）：
#   「记住一个铁律！任何情况下存档编辑器都不允许有向地图中写入数据的能力」
#   ⇒ 本工具对 `*.map / *.mmx / *.yrm / *.mpr` **只读**。任何"写地图"的入口一律禁止。
#   历史：§174/§176 曾经提供"把局部变量名/默认值写进地图 [VariableNames]"的功能，
#   现在**整体撤除** —— 函数留着只为让旧调用方立刻炸掉，而不是静默改文件。
MAP_WRITE_REFUSED = ('铁律：本工具不允许向地图写入任何数据（玩家 2026-10-27 定）。'
                     '需要改地图请在地编（FA2/FinalAlert）里做。')


class MapWriteForbidden(RuntimeError):
    """任何试图改地图文件的调用都会抛这个（见 `MAP_WRITE_REFUSED`）。"""


def write_scenario_variables(path=None, items=None, backup=True):
    """★已废：地图只读。这里**只抛异常**，永不落盘（保留函数名是为了让旧调用当场报错）。"""
    raise MapWriteForbidden(MAP_WRITE_REFUSED)


# ---------------- 地图路径点（★2026-10-22：玩家要求"125 的落点要写进地图路径点"） ----------------
WAYPOINT_SECTION = 'Waypoints'


def parse_scenario_waypoints(raw):
    """解析地图 `[Waypoints]` → `{号: (x, y)}`；编码是 **`y×1000 + x`**（`0=84068` ⇒ (68,84)）。

    已用存档对拍确认方向：`同触发.SAV` 存档路径点 0 = (68,84)、地图 `0=84068` ⇒ 值 = y*1000+x。
    """
    sp = _cfg_span(raw, WAYPOINT_SECTION)
    if not sp:
        return {}
    body = raw[sp[1]:sp[2]].decode('latin1', 'replace')
    out = {}
    for ln in body.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        ln = ln.strip()
        if not ln or ln.startswith(';') or '=' not in ln:
            continue
        k, v = ln.split('=', 1)
        k, v = k.strip(), v.strip()
        if not k.isdigit():
            continue
        try:
            iv = int(float(v.split(',')[0] or '0'))
        except ValueError:
            continue
        n = int(k, 10)
        if iv <= 0:
            continue
        y, x = divmod(iv, 1000)
        out[n] = (x, y)
    return out


def read_scenario_waypoints(path):
    """读地图文件的 `[Waypoints]` → `{'path','items','exists'}`。"""
    if not path or not os.path.exists(path):
        return {'path': path, 'items': {}, 'exists': False}
    raw = open(path, 'rb').read()
    return {'path': path, 'items': parse_scenario_waypoints(raw), 'exists': True}


def write_scenario_waypoints(path=None, items=None, backup=True):
    """★已废：地图只读（玩家 2026-10-27 铁律）。这里**只抛异常**，永不落盘。

    历史：§125 落地时曾用"把路径点写进地图 [Waypoints]"让地编/重载看得见；
    铁律之后一律不许——路径点只写存档（`set_waypoint()`），地图那份请玩家在地编里自己画。
    """
    raise MapWriteForbidden(MAP_WRITE_REFUSED)


def read_map_ini_size(path):
    """读地图 `[Map] Size=a,b,c,d` → (c, d)（= 右/下边界，单位格）；读不出返回 None。

    ★为什么需要它（2026-10-22 实踩）：官方地图也叫 `ALL01UMD.MAP`。玩家把自己的测试图
    （`Size=0,0,80,80`，9 条路径点）移走后，上溯查找会在 `E:\\` 根目录撞上**原版**
    `ALL01UMD.MAP`（`Size=0,0,120,120`，350 条路径点）。认错图轻则提示错乱，重则
    `register_waypoints_to_map()` 把路径点写进**别人的地图**。⇒ 必须按尺寸核对。
    """
    try:
        raw = open(path, 'rb').read()
    except Exception:
        return None
    low = raw.lower()
    i = low.find(b'[map]')
    if i < 0:
        return None
    j = low.find(b'size=', i, i + 4000)
    if j < 0:
        return None
    k = raw.find(b'\n', j)
    if k < 0:
        k = j + 40
    txt = raw[j + 5:k].decode('latin1').strip()
    parts = [p.strip() for p in txt.split(',')]
    if len(parts) < 4:
        return None
    try:
        return (int(parts[2]), int(parts[3]))
    except Exception:
        return None


def find_scenario_map(save_path, name=None, extra_dirs=None, size=None):
    """按存档内嵌的地图名找地图文件（Windows 不区分大小写）→ 路径 或 None。

    `size` = 存档自带的 (W, H)（`SaveDoc.map_size()`）：
      · 给了就**只接受 `[Map] Size` 右/下值与之一致**的候选（防同名官方图误认）；
      · 候选全都尺寸不符 ⇒ 返回 None，宁可"没找到"也不要写坏别人的图。
    """
    if not name:
        return None
    dirs = []
    try:
        d = os.path.dirname(os.path.abspath(save_path))
        for _ in range(4):               # 存档目录 + 上溯 3 级
            if d and d not in dirs:
                dirs.append(d)
            nd = os.path.dirname(d)
            if nd == d:
                break
            d = nd
    except Exception:
        pass
    for d in (extra_dirs or []):
        if d and d not in dirs:
            dirs.append(d)
    for d in dirs:
        p = os.path.join(d, name)
        if not os.path.exists(p):
            continue
        if size:
            got = read_map_ini_size(p)
            if got and tuple(got) != tuple(size):
                continue                 # 尺寸不符 ⇒ 同名异图，跳过
        return p
    return None

# 遗留常量：仅为兼容旧脚本引用，**定位逻辑已不再使用固定步长**
EVENT_STEP = 108
ACTION_STEP = 168

# ---- 触发「运行时启用位」（2026-10-13 定案，跨图通用）----
# 每个触发对象体内含 16 字节**类 GUID**（触发对象类型）：
#   1D 时实测两图同一常量：243509BA-4CF4-11D2-BC26-00104B8FB04D
# 「启用位」= 该 GUID **前 8 字节**处的 1 个字节：1=启用 / 0=禁用。
# ★ 记录内偏移**随图变化**（新图 +236 / 旧图 +328），因为 GUID 前有变长内容
#   ⇒ 不能写死偏移，必须用 GUID 当锚点。
# 证据：60+ 档统计，10 个「已知游戏内状态」的档 100% 吻合，零误报；
#   其中旧图 6691/6692 与用户游戏内实测（不胜利/胜利）逐字节对应。
TRIG_CLASS_GUID = bytes.fromhex('243509baf44cd211bc2600104b8fb04d')
TRIG_FLAG_BACK = 8

# ---- 自制地图的另一种触发记录格式（2026-10-14 SAVE2DD8 实测）----
# 自制任务地图的触发 ID 是**随机八位十六进制**（064BA67C…），不是 FA2 顺序的 01xxxxxx；
# 记录体 200B 等距：ID@+0、名字@+64（**UTF-8/ASCII**，老档是 GBK）、
# +116 顺序索引、+120..122 = 01 01 01（难度位，与老格式同）、**+123 = 运行期启用位**
# （1=启用；47/47 与地图 [Triggers] 禁用标志完美对拍）、+152 恒定类 GUID
# C02D1591-0A2A-11D2-ACA7-006008055BB5（记录体里无 243509BA ⇒ 老锚点失效）。
TRIG_CLASS_GUID2 = bytes.fromhex('91152dc02a0ad211aca7006008055bb5')
TRIG_CLASS_GUID0 = bytes.fromhex('90152dc0')          # 老格式 TagClass C02D1590-… 的前 4 字节
TRIG_GUID2_HEAD = bytes.fromhex('ba093524f44cd211')   # 老触发 GUID 前 8 字节（判归属用短锚）

# ---- 标签（Tag）类 GUID 与「重复类型」（★2026-10-20 玩家 + ModEnc 定案）----
# TAG_CLASS_GUID 的稳定**尾 12 字节**（首 4 字节会 ±1，不能整串匹配：
#   实测 SAVE18BE 里 C 的标签记录多带 4 字节字段，首 4 字节从 33 e4 f6 54 变 32 e4 f6 54）。
TAG_GUID_TAIL = bytes.fromhex('ed09d211aca5006008055bb5')
TAG_PERSIST_WIN = (124, 168)        # GUID 尾搜索窗（记录带额外字段时会整体漂 ±4）
TAG_PERSIST_OFF = 120               # ★标签记录 +120 (u32) = 持久性 = "重复类型"
                                     #   0=一次性·任一挂载  1=一次性·挂载清空  2=可重复
                                     #   ★"任一/全部"说的是**标签挂在多个对象上时**哪个对象的事件算数，
                                     #     不是"一个触发里多个事件"的关系！（见 §156 纠错）
                                     #   同一触发内的多个事件恒为 AND（全部成立才执行），与持久性无关
TAG_REC_DELTA = 56                  # 记录帧 = 标签对象帧 + 56（⇒ 记录+120 = 对象+0xB0）

# ======== §192（2026-10-22）TagTypeClass 报文全字段表 ========
# 硬证据：
#   · RTTI：`.?AVTagTypeClass@@` TypeDescriptor @0x842CB0 → COL @0x80BCF0 → **vtable 0x7F45C4**
#     （另有 3 个基类子对象 vtable：0x7F45A8 / 0x7F45A0 / 0x7F4598，都在对象 +0/4/8/0xC）
#   · vtable[12] = 0x6E64A0 `mov eax,0xA4; ret` ⇒ **对象体 = 164B**；vtable[11] = 0x6E6490 返回 **0x2D = 45**（ClassID）
#   · `TagTypeClass::WriteINI` @0x6E6160 —— 写地图 `[Tags]` 那一行，格式串 `"%d,%s,%s"`：
#         sprintf(dst, "%d,%s,%s", this+0x9C, this+0x64, trigger->0x24)   ⇒ 持久性/名字/触发ID 三个字段落实
#         （trigger 为空时走 `"%s,<none>"`；键名 = this+0x24）
#   · 实测语料：SAVE4CA2（82 个标签）/ SAVE18BE（83）/ 同触发.SAV / 触发C为地编写入.sav
#     +0x9C 分布 {0:73, 2:9} 与 `tag_persist()` 完全一致；+0x98 = 0..81 严格递增（序号）
#     +0xA0 逐个命中某个 54F6E434（触发实例）记录的自键 ⇒ 标签→触发 的连线
TAG_TYPE_GUID = bytes.fromhex('33e4f654ed09d211aca5006008055bb5')   # 54F6E433（同 TRIG_TAGOBJ_GUID）
TAG_TYPE_REC_SIZE = 184              # 记录 = [16B GUID][4B 自键][164B 对象体]
TAG_TYPE_SIZE = 164                  # 对象体（= Size() 0xA4）
TAG_TYPE_OBJ_DELTA = 20              # 对象体起点 = GUID 起点 + 20
TAG_TYPE_VTABLES = (0x7F45C4, 0x7F45A8, 0x7F45A0, 0x7F4598)   # 跨档恒定，克隆时保留、从零造时照抄
TAG_TYPE_IDLEN_OFF = 0x10            # u32 恒 -1（AbstractClass 的 ID 初值）
TAG_TYPE_ID_OFF = 0x24               # char[8] **唯一 ID 串**（"01000001"…= 0x01000000|计数，%08X）
                                     #   同时是地图 `[Tags]` 的键名
TAG_TYPE_NAME_OFF = 0x64             # char[52] 名字（★GBK，不是 UTF-8）
TAG_TYPE_NAME_LEN = 52
TAG_TYPE_INDEX_OFF = 0x98            # u32 集群内**序号**（0 基，必须与位置一致）
TAG_TYPE_PERSIST_OFF = 0x9C          # u32 持久性 / "重复类型" 0/1/2（= 地图 [Tags] 第 0 字段）
TAG_TYPE_TRIGGER_OFF = 0xA0          # 键 → 触发实例（TriggerTypeClass，C02D1591）
TAG_TYPE_STATIC60_OFF = 0x60         # 静态常量指针 0x00887734（引擎 .data，82/82 恒定，别动）
TAG_TYPE_STATIC60 = 0x00887734
# ★"垃圾槽"：+0x14/+0x20/+0x2C..+0x5F 是**没被序列化使用的残留区**。
#   实测：刚建好就存的档（同触发.SAV / 触发C为地编写入.sav）里全 0；
#         长期任务档 SAVE4CA2 的 82 个标签里有 439 个 dword 非 0，值是旧键(0x13xxxxxx)、
#         裸指针(0x10xxxxxx) 甚至短串(0x584948)，但游戏照样跑 ⇒ **残留本身无害**。
#   工具侧仍然清零（克隆模板会把这些旧值原样搬进新触发，归零 = 让新标签和"刚建的"一致）。
TAG_TYPE_STALE = ((0x14, 0x24), (0x2C, 0x60))

# ---- 触发「流逝时间」的**运行期帧数**（2026-10-13 定案）----
# 触发记录里有个恒定的 4 字节标记 `C4 60 72 00`（游戏数据段里的一个静态指针常量），
# 其**后一个 u32 = 该触发「流逝时间」的等待帧数 = 秒 × 15**（RA2 逻辑帧率 15 fps）。
#
# ★ 为什么必须同时改这里：事件对象里 `+72` 的 u32 是**秒**（= 地图 INI 里写的值），
#   而引擎实际计时用的是这里的**帧**。实测（用户游戏内验证）：
#     · 只把事件 `+72` 由 60 改成 10  ⇒ 游戏仍在 **60 秒**时触发（改动被忽略）
#     · 同时把标记处由 900 改成 150 ⇒ 见 README 的验证记录
#   跨档核对（标记处 ÷15 全部等于某个事件的秒数）：
#     新图 13/60→900 ✓ ; 13/5→75 ✓ ; 13/20→300 ✓ ; 13/4789→71835 ✓ ; 13/1337→20055 ✓
#   ⇒ 秒数×15 恒为 15 的整数倍，可作为校验条件。
TIME_MARK = bytes.fromhex('c4607200')
LOGIC_FPS = 15

# ---- 新写触发（2026-10-14 五轮实验 + V1/V2/V4 游戏内实测通过）----
TRIG_INSTANCE_GUID = bytes.fromhex('91152dc02a0ad211aca7006008055bb5')  # C02D1591 触发实例
TRIG_TAGOBJ_GUID = bytes.fromhex('33e4f654ed09d211aca5006008055bb5')    # 54F6E433 标签对象
TRIG_OBJ_SIZE = 200
TAG_OBJ_SIZE = 184

# ---- 触发「集群」的另两个成员：TagNode / CellTag（2026-10-14 定案）----
# ★★★ 游戏地编写入的权威档（触发C为地编写入.sav）证明：一个可用触发 = **六件套**
#     [标签 184B][CellTag 76B][实例 200B][TagNode 92B]（各带 4B 组计数，同区 0x13xxxxxx 键）
#     + 事件/行为对象。少了 TagNode/CellTag ⇒ 行为触发链断裂：
#     实测「只有电厂自爆生效，地图点亮/胜利都不生效」；补齐后 10 秒点亮、30 秒胜利（玩家实测）。
#   · CellTag +56 = 标签键、+60 = TagNode 键、+64 = 挂接数（无挂接 = 0）；
#   · TagNode +56 = 触发实例键、+80 = 「流逝时间」等待帧数（秒 × 15）、+88 最低位 = 启用状态；
#   · 组内顺序 = 追加到组尾，与权威档一致。
TRIG_NODE_GUID = bytes.fromhex('90152dc02a0ad211aca7006008055bb5')   # C02D1590 TagNode
TRIG_NODE_SIZE = 92
TRIG_CELLGUID = bytes.fromhex('32e4f654ed09d211aca5006008055bb5')   # 54F6E432 CellTag
TRIG_CELL_SIZE = 76
# ★2026-10-17：**新开局的档里没有 TagNode/CellTag 可克隆** —— 用"空壳模板"从零造。
#   类常量（`+20..+35` 四个 vtable 指针、TagNode `+76`）跨档恒定、由引擎写入；
#   其余全 0，键/链接/计时帧/启用位由 `create_trigger` 填。
TRIG_NODE_TEMPLATE = bytes.fromhex(
    '90152dc02a0ad211aca7006008055bb5'
    '00000000' '58587f003c587f0034587f002c587f00' 'ffffffff'
    + '00' * 36 + '52947c00' + '00' * 12)          # 92B
TRIG_CELL_TEMPLATE = bytes.fromhex(
    '32e4f654ed09d211aca5006008055bb5'
    '00000000' 'e0447f00c4447f00bc447f00b4447f00' 'ffffffff'
    + '00' * 36)                                    # 76B
# 「定时触发器激活登记表」在**空表**时的结构指纹：`[u32 0 字段][u32 14][u32 14][u32 0a]`
#   （表 = `[u32 n][cell 键 × n]` 紧跟这段）⇒ 用它反推 count 位置 = 指纹起点 − 4 − 4n。
TIMER_TABLE_TAIL = bytes.fromhex('00000000' '14000000' '14000000' '0a000000')
NODE_TRIGKEY_OFF = 56        # TagNode → 触发实例键
NODE_TICKS_OFF = 80          # TagNode → 等待帧数（秒 × LOGIC_FPS）
NODE_FLAG_OFF = 88           # TagNode → 启用状态（最低位 1=启用 / 0=禁用）
NODE_SPARE76 = 0x007C9452    # TagNode +76：权威档同字段值（引擎似会重算，取实测通过件原值）
CELL_TAGKEY_OFF = 56         # CellTag → 标签键
CELL_NODEKEY_OFF = 60        # CellTag → TagNode 键
CELL_COUNT_OFF = 64          # CellTag → 挂接数（无挂接 = 0）
NODEGRP_BASE_KEY = 0x133FD000   # TagNode/CellTag 键所在区（同区未用键 +0x1000 起）

# 触发记录的字段偏移（★相对**记录起点** = 触发实例对象 + 56 处的 ID 字符串）：
#   +64 名字 / +120..123 难度位+启用副本 / +172 序号 / +192 事件键 / +196 行为键
TRIG_REC_INST_BACK = 56      # 记录起点 − 56 = 触发实例对象起点（本格式判据）
TRIG_REC_IDX_OFF = 172       # 触发/标签对象：集群内**序号**（0 基，必须与位置一致）
TRIG_REC_FLAG_COPY = 123     # 记录 +123 = 启用状态的**副本**（与 TagNode+88 同步写）
TRIG_REC_QUAD_OFF = 120      # 记录 +120..123 = 难度位 01 01 01 + 启用副本
TRIG_REC_QUAD = b'\x01\x01\x01'

# CONTENTS 分块（★★★ 游戏行为，2026-10-14 实测：进档弹框的根因就在分块）
CONTENTS_CHUNK = 65536       # 明文按 65536 切
CONTENTS_UNPACKED = 65536    # 块头 unpacked 恒为 65536（末块可短于声明值）


def trig_flag_offset(pl, off, next_off):
    """在 [off, next_off) 内定位该触发记录的「运行时启用位」绝对偏移；找不到返回 None。

    next_off 用于限定"属于本条记录"的范围（通常 = 下一条记录的起点），
    否则会把后面别的对象的 GUID 误认成自己的。
    """
    p = pl.find(TRIG_CLASS_GUID, off, next_off)
    if p < 0:
        return None
    q = p - TRIG_FLAG_BACK
    if q < off or q >= len(pl) or pl[q] > 1:
        return None
    return q


MONEY_SIG = bytes.fromhex('5c197e00')    # 金钱标记（前置 4 字节签名）
MIRROR_DELTA = 7708                      # 权威镜像 = 所属方记录起点 + 7708 (u24)
HOUSES = ['Americans', 'British', 'French', 'Germans', 'Russians', 'YuriCountry',
          'Africans', 'Alliance', 'Arabs', 'Confederation', 'Neutral']

# ---- 所属方（House）记录数组 ----
# 存档里有一串严格的 464 字节步长记录，以阵营短名开头，顺序 = rules [Countries] 注册序。
# 每个记录起点 + MIRROR_DELTA 就是该所属方的金钱（u24）。已实测：多档金额互不相同。
HOUSE_STEP = 464
HOUSE_CHAIN = ['Americans', 'Alliance', 'French', 'Germans', 'British',
               'Africans', 'Arabs', 'Confederation', 'Russians', 'YuriCountry']
HOUSE_CN = {
    'Americans': '美国', 'Alliance': '韩国', 'French': '法国', 'Germans': '德国',
    'British': '英国', 'Africans': '利比亚', 'Arabs': '伊拉克',
    'Confederation': '古巴', 'Russians': '苏俄', 'YuriCountry': '尤里',
    'Neutral': '中立', 'Special': '特殊', 'Civilian': '平民',
}
HOUSE_MIN_CHAIN = 6                       # 至少要连续命中几项才认这是数组

# 「所属方经济结构」——每个阵营的金钱字段布局（2026-10-13 用独特金额实测确认）：
#   60 字节固定模板（5 个浮点常量 + 5 + 10）
#   → u32 阵营位掩码（= 1 << 在 [Countries] 里的注册序）
#   → u24 金钱
# 再往 +304 字节是同一阵营的第二份副本（值相同）。
# 实测：一次性命中 9 个 AI 阵营，值与地图 Credits×100 逐一吻合。
HOUSE_ECON_TPL = bytes.fromhex(
    '9999f93f000000403333f33f000000a0'
    '9999e93f000000000000f03f2085eb51'
    'e17af43f00000040e17a943f000000e0'
    '51b89e3f050000000a000000')
HOUSE_ECON_GAP = 304
HOUSE_ORDER = HOUSE_CHAIN + ['Neutral', 'Special']    # 12 个（含中立/特殊）
# ---- 「行为的参数类型」有**两套编号**，别混用（2026-10-20 交叉验证）----
#   · ENGINE_PTYPE_TEXT = 引擎口径，出现在地图 `[Actions]` 每块的第 2 个字段；
#     行为对象在存档里只存「类型 + 参数」，参数怎么解释是引擎按这张表内置的。
#     数值含义来自 vera20k 的逆向注释（TActionClass::Read @0x006DD5B0 等）＋ 我们用
#     all01umd.map 的真实数据对拍（见主文档 §126）。
#   · ACTION_TYPES/EVENT_TYPES 里那个 ptype 是**地编 FAData 口径**，只用来给 UI 提示，
#     两套数字不通用（例如「胜利者是」引擎侧=0 字面数值，地编侧=2 所属方）。
ENGINE_PTYPE_TEXT = {
    0: '字面数值',
    1: '作战小队',
    2: '触发',
    3: '标签',
    4: 'CSF 文本标签',
    5: '作战小队(5)',
    6: 'EVA 语音',
    7: '语音 Voc',
    8: '主题 Theme',
    9: '内联类型名 + 路径点',
    10: '内联类型名（建筑/单位）',
    11: '数值 + 附加(token8)',
}

COUNTRY_ORDER = HOUSE_ORDER                           # 所属方型参数的取值顺序（同注册序）
# ★ 2026-10-17：建筑载荷 `+1344` 用的「运行期阵营序号」兜底表（10 国 = 注册序 0..9；Neutral = 12）。
#   ⚠ 只有前 10 国与 Neutral 有实测支撑（母本 / 大任务档两档一致）；Special 未实测（取 11 兜底）。
#   能用时**一律优先从本档已有建筑反解**（`SaveDoc.house_run_index()`），本表只做兜底。
HOUSE_RUN_INDEX = {n: i for i, n in enumerate(HOUSE_CHAIN)}
HOUSE_RUN_INDEX.update({'Neutral': 12, 'Special': 11})
IDRE = re.compile(rb'01[0-9A-F]{6}')


class TriggerUnavailable(RuntimeError):
    """本档**没法新写触发**（缺触发集群 / 缺事件与行为对象组）。

    调用方（GUI/CLI）应当捕获它并**退回「直接落成」**，而不是把技术性 traceback 甩给玩家。
    `reason` 是给玩家看的一句话；`detail` 是给开发者看的诊断。
    """

    def __init__(self, reason, detail=''):
        RuntimeError.__init__(self, reason)
        self.reason = reason
        self.detail = detail


class UnitUnavailable(RuntimeError):
    """本档没法克隆放置该单位（档内没有同类型模板）。"""

    def __init__(self, reason, detail=''):
        RuntimeError.__init__(self, reason)
        self.reason = reason
        self.detail = detail


class LastAttachRefused(RuntimeError):
    """这条标签只剩 1 个挂载对象，解除会留下"计数 0"的节点 —— 默认拒绝。

    ★§206：**不是不能解**，是要玩家明确选一种：
      · `allow_last=True` 直接解除（计数归 0；实测原版档里本来就有 count=0 的节点）；
      · 或者先 `soft_disable_trigger()` 把触发弄成永不满足，再解除。
    `persist`：标签持久性 0/1/2 —— **只有 1（挂载清空型）**才把"计数归 0"当执行条件之一，
    那种情况更建议先禁用。
    """

    def __init__(self, node, count, persist=None):
        msg = '该标签只剩 %d 个挂载对象，解除会把它变成"计数 0"的节点' % max(int(count), 0)
        RuntimeError.__init__(self, msg)
        self.node = node
        self.count = count
        self.persist = persist


class Rec(object):
    """一条记录（触发/Tag/其它）"""
    __slots__ = ('sid', 'name', 'off', 'kind', 'enabled', 'can_toggle', 'note', 'flag_off')

    def __init__(self, sid, name, off, kind, enabled, can_toggle, note=''):
        self.sid = sid
        self.name = name
        self.off = off
        self.kind = kind
        self.flag_off = None        # 触发：运行时启用位的绝对偏移（定位到才有）
        self.enabled = enabled
        self.can_toggle = can_toggle
        self.note = note

    def __repr__(self):
        return '<Rec %s %s %s enabled=%s>' % (self.sid, self.name, self.kind, self.enabled)


def _is_hex4(m):
    return all(0x30 <= c <= 0x39 or 0x41 <= c <= 0x46 for c in m)


def _decode_txt(b):
    """记录名 / 文本解码：新档是 UTF-8，老档是 GBK。两者都可能"成功解码但错"，
    按 CJK 字符数挑更可信的那个（GBK 字节被 UTF-8 误解码时几乎不出 CJK）。"""
    if not b:
        return ''
    s8 = sg = None
    try:
        s8 = b.decode('utf-8')
    except Exception:
        pass
    try:
        sg = b.decode('gbk')
    except Exception:
        pass
    if s8 is not None and sg is not None:
        # ★ UTF-8 CJK 三字节序列（E4-E9 开头）计数：真 UTF-8 中文≥2 个基本必中；
        #   GBK 字节偶合概率低（2026-10-14：GBK 误解出的 CJK 数更多曾把
        #   「测试触发D」翻成「娴嬭瘯瑙﹀彂D」，靠这条判据修正）
        n3 = 0
        for i in range(len(b) - 2):
            if 0xE4 <= b[i] <= 0xE9 and 0x80 <= b[i + 1] <= 0xBF and 0x80 <= b[i + 2] <= 0xBF:
                n3 += 1
        if n3 >= 2:
            return s8
        cjk = lambda s: sum(1 for ch in s if '\u4e00' <= ch <= '\u9fff')
        return s8 if cjk(s8) >= cjk(sg) else sg
    return s8 if s8 is not None else (sg or '')


def _filetime(u64):
    if not u64:
        return '-'
    try:
        return (datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=u64 // 10)
                ).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return '-'


def _lzo_pack_chunks(segs):
    """**多进程 worker**：把一批 64KB 明文块压成 LZO1X（模块级函数才能被 pickle）。

    ★2026-10-17 21:4x：纯 Python 压缩器约 3.5 MB/s，39 MB 的大任务档要 11 秒。
      插入字节会让后面所有块移位 ⇒ 几乎整档都要重压，没法靠"复用旧块"绕过。
      这里只是把块分给几个进程并行；失败（打包环境/多进程被拦）就回退串行。
    """
    from lzo_compress import compress
    return [compress(s) for s in segs]


def _parallel_compress(segs, workers=4):
    """并压缩一批块；不行就返回 None 让调用方串行。"""
    import multiprocessing as mp
    n = max(1, min(workers, (mp.cpu_count() or 1)))
    if n < 2 or len(segs) < 48:
        return None
    k = (len(segs) + n - 1) // n
    parts = [segs[i:i + k] for i in range(0, len(segs), k)]
    try:
        import concurrent.futures as cf
        ctx = mp.get_context('spawn')
        with cf.ProcessPoolExecutor(max_workers=n, mp_context=ctx) as ex:
            res = list(ex.map(_lzo_pack_chunks, parts))
        out = []
        for r in res:
            out.extend(r)
        return out if len(out) == len(segs) else None
    except Exception:
        return None




# ★★★★ 2026-10-23 §216（玩家提议：「是不是应该在存档编辑器内置一份单位和建筑模板，直接可以写入？」）
#   **单位模板库**：跨型号放置时，"内容"其实只需要每型号约 40 字节 ——
#   移动方式（决定借哪种伴随对象）+ 8 个机体参数（`+0x270/+0x51C/+0x940..+0x958`）。
#   实测依据：`有空地.SAV` 8 架真基洛夫，7 架的这 8 个槽完全一致（`0x944=6.0f`、
#   `0x948=12.0f`、`0x954=0.15f` 等），只有 1 架"刚出生"状态不同。
#   ⇒ 不需要内置整条 2316 字节的游戏数据；**从玩家自己的存档里学一份签名表**即可，
#     既没有版权问题（只是若干整数），也不会因为游戏版本/Mod 变了而错配。
_SIG_SLOTS = (0x270, 0x51C, 0x940, 0x944, 0x948, 0x94C, 0x954, 0x958)


def signature_cache_path():
    """模板库文件位置：优先 exe/脚本所在目录（便携版），否则用户目录。"""
    import sys as _sys
    cands = []
    if getattr(_sys, 'frozen', False):
        cands.append(os.path.dirname(_sys.executable))
    else:
        # 开发态优先放在项目根（与 exe 同目录的习惯一致），其次 core/
        cands.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cands.append(os.path.dirname(os.path.abspath(__file__)))
    for d in cands:
        try:
            if not os.access(d, os.W_OK):
                continue
        except Exception:
            continue
        return os.path.join(d, 'unit_signatures.json')
    return os.path.join(os.path.expanduser('~'), '.ra2_se_unit_signatures.json')


def load_signature_cache(path=None):
    """读模板库 → {'types': {...}}；文件不在/坏了就返回空表（不报错）。"""
    import json
    p = path or signature_cache_path()
    try:
        with open(p, 'r', encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get('types'), dict):
            return d
    except Exception:
        pass
    return {'version': 1, 'slots': ['0x%X' % o for o in _SIG_SLOTS], 'types': {}}


def save_signature_cache(cache, path=None):
    import json
    p = path or signature_cache_path()
    try:
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
        return p
    except Exception:
        return None


def read_isomap(map_path):
    """解一个 `.MAP` 的 `[IsoMapPack5]` → { (x,y): (tile, sub, z, level) }；失败返回 {}。

    ★2026-10-23 §210：坐标口径与存档的**格对象同一个空间**（实测地图 42449 格全部落在
      存档 43080 个格对象里）⇒ 可以直接拿存档 (x,y) 查地形，不需要任何坐标转换。
    ★★2026-10-24 §229/§230（实测 ALL01UMD.MAP 42449 格）：元组第 4 个值是**层高**，
      不是 icr —— `sub`/`z` 在该图里恒为 0，而第 4 个值分布 0..27（72.3% 是 0=地面），
      且桥面/引桥一路递增（引桥 1..5、桥面 4..15）⇒ 跨层上不了桥/上不了车。
      **查"单位为什么上不了车/走不过去"时先看这一列。**
    """
    import base64
    import re as _re
    try:
        from .lzo1x import lzo1x_decompress
    except Exception:
        try:
            from lzo1x import lzo1x_decompress
        except Exception:
            return {}
    try:
        raw = open(map_path, 'rb').read()
        txt = raw.decode('gbk', errors='replace')
        i = txt.find('[IsoMapPack5]')
        if i < 0:
            return {}
        j = txt.find('\n[', i + 14)
        sec = txt[i:j if j > 0 else len(txt)]
        acc = []
        for line in sec.splitlines()[1:]:
            if '=' not in line:
                continue
            acc.append(_re.sub(r'[^A-Za-z0-9+/=]', '', line.split('=', 1)[1].strip()).replace('=', ''))
        b64 = ''.join(acc)
        b64 = b64[:len(b64) - (len(b64) % 4)]
        ipk = base64.b64decode(b64)
        buf = bytearray()
        q = 0
        while q + 4 <= len(ipk):
            i_s, o_s = struct.unpack_from('<HH', ipk, q)
            q += 4
            if i_s == 0 and o_s == 0:
                break
            try:
                out, _ = lzo1x_decompress(ipk[q:q + i_s], o_s)
                buf += out
            except Exception:
                pass
            q += i_s
        out = {}
        for q in range(0, len(buf) - 10, 11):
            x, y = struct.unpack_from('<HH', buf, q)
            out[(x, y)] = (struct.unpack_from('<H', buf, q + 4)[0], buf[q + 6], buf[q + 7], buf[q + 8])
        return out
    except Exception:
        return {}


class SaveDoc(object):
    def __init__(self, path, write_cache=True):
        self.path = path
        self.error = None
        # ★2026-10-28（审查 P3-13）：`write_cache=False` 时**只读不写**明文磁盘缓存。
        #   给"只扫元信息"的后台线程用：那里前 12 个档各写一份 26MB ⇒ 光启动就 300MB 磁盘写，
        #   而扫完就 `del doc`，缓存根本不会被复用。
        self.write_cache = bool(write_cache)
        # ★2026-10-28：记下"这份档在哪个目录"，让 core 能就近找到 rules/art
        #   （玩家把游戏装在任意位置时，可建造判定 / Foundation / Power 才不会静默失效）
        try:
            self.note_save_dir(path)
        except Exception:
            pass
        self.load()

    # ---------- 读取 ----------
    # ★2026-10-17 20:5x：**明文磁盘缓存**（大任务档 39MB / 纯 Python LZO 解压要 2.6 秒）。
    #   键 = 原文件 md5（存档一改，缓存键就变，天然不会读到旧数据）；
    #   只在"同一份档反复打开"时生效 —— 玩家改一次、进游戏验一次的工作流正好吃这个红利。
    #   缓存内容 = 明文 + 分块表（_rechunk 复用未改动块的压缩字节要用它）。
    _CACHE_MAGIC = b'RSED1'

    @staticmethod
    def _cache_dir():
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        d = os.path.join(base, 'RA2SED_cache')
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            return None
        return d

    def _load_plain(self, raw, cc):
        """→ (plain, ranges, 来源)。缓存不可用/损坏时回退到真正的 LZO 解压。"""
        md5 = hashlib.md5(raw).hexdigest()
        cdir = self._cache_dir()
        cp = os.path.join(cdir, md5 + '.bin') if cdir else None
        if cp and os.path.exists(cp):
            try:
                with open(cp, 'rb') as f:
                    if f.read(5) == self._CACHE_MAGIC:
                        n = struct.unpack('<I', f.read(4))[0]
                        blk = f.read(16 * n)
                        rs = [struct.unpack_from('<IIII', blk, 16 * i) for i in range(n)]
                        plain = f.read()
                        if rs and rs[-1][0] + rs[-1][1] == len(plain):
                            return plain, [tuple(r) for r in rs], 'cache'
            except Exception:
                pass
        plain, ranges = self._decode(cc)
        if cp and getattr(self, 'write_cache', True):
            try:
                with open(cp, 'wb') as f:
                    f.write(self._CACHE_MAGIC + struct.pack('<I', len(ranges)))
                    for r in ranges:
                        f.write(struct.pack('<IIII', *r))
                    f.write(plain)
                # 只保留最近 4 份（大任务档一份 39MB）
                fs = sorted((os.path.join(cdir, x) for x in os.listdir(cdir)
                             if x.endswith('.bin')), key=os.path.getmtime)
                for old in fs[:-4]:
                    try:
                        os.remove(old)
                    except Exception:
                        pass
            except Exception:
                pass
        return plain, ranges, 'decode'

    def load(self):
        self.raw = open(self.path, 'rb').read()
        self.cfb = CFB(self.raw)
        self.contents = self.cfb.open('CONTENTS')
        self.plain_orig, self.ranges, self.plain_src = self._load_plain(self.raw, self.contents)
        self.plain = bytearray(self.plain_orig)
        self._house_arr = None           # 所属方记录数组缓存
        self._house_econ = None          # 所属方经济结构缓存
        self._house_keys_memo = None     # 阵营短名 → House 对象键 缓存（2026-10-17）
        self._objs = None                # 事件/行为对象缓存（签名定位）
        self._map = None                 # 挂载的地图（权威真值 + 归属）
        self._mapm = None                # 地图对拍结果
        self._events = None              # 事件表缓存
        self._actions = None             # 行为表缓存
        self._ticks = None               # 「流逝时间」帧数块缓存
        self._celltags = None            # CellTag 节点缓存
        self._ctimerlist = None          # 「定时触发器挂接表」定位缓存
        self._blds = None                # 建筑对象缓存（2026-10-15）
        # ★本次会话里由**我们注入**的建筑键（2026-10-17，P1「N27 弹框」定案）：
        #   克隆模板绝不能取这些记录 —— 它们本身是"异类型 + 我们的补丁"的产物，
        #   再拿它当模板会产出**混血记录** ⇒ 载入弹框。
        self._injected_keys = set()
        # §196：签名判据降级为"提示"后，把提示文本放这里供 GUI 显示（不再阻断放置）
        self._last_readiness_note = ''
        self._btypes = None              # 类型对象索引缓存
        self._bld_tmp = None             # 建筑扫描中间表
        self._bnames = None              # 建筑类型名集合（rulesmd [BuildingTypes] ∪ artmd Foundation）
        self.time_sync_note = ''         # 最近一次时间同步的结果说明（供界面提示）
        self._tables_lowconf = False     # 类型空间校验未通过 ⇒ 低置信（只读提示用）
        self._tagrecs = None              # 标签记录缓存（tag_records，按类 GUID 尾识别）
        self.info = self._read_info()
        self.records = self._scan_records()
        self.pending_money = None
        self.pending_houses = {}         # short -> 目标金额
        self.pending_triggers = {}       # sid -> bool
        self.pending_events = {}         # 事件偏移 -> (类型, 参数)
        self.pending_actions = {}        # 行为偏移 -> (类型, 参数)
        self.pending_attach = []         # 挂接操作 [(字段偏移, 旧值)]

    @staticmethod
    def _decode(cc):
        pos = 0
        out = bytearray()
        ranges = []
        while pos + 8 <= len(cc):
            pk, uk = struct.unpack_from('<II', cc, pos)
            if pk == 0:
                break
            seg = cc[pos + 8:pos + 8 + pk]
            o, consumed = lzo1x_decompress(seg, uk)      # 带期望长度 ⇒ 能走 C 后端
            ranges.append((len(out), len(o), pos, pk))
            out += o
            pos += 8 + pk
        return bytes(out), ranges

    def _read_info(self):
        pl = self.plain
        c = self.cfb
        def s(name):
            try:
                return c.open(name) or b''
            except Exception:
                return b''
        info = {}
        self.info = info        # 提前挂上：下面 get_money() 内部要读“玩家阵营”
        info['文件'] = os.path.basename(self.path)
        info['文件大小'] = '%d B' % len(self.raw)
        info['明文大小'] = '%.1f MB' % (len(pl) / 1048576.0)
        info['块数'] = len(self.ranges)
        _ts = self.map_tile_size()
        _sz = self.map_size()
        if _ts:
            info['地图尺寸'] = '%d×%d（格子表反推，与 FA2 同口径）' % _ts
            if _sz and _sz != _ts:
                info['地图尺寸'] += '；0x348E 那组是 %d×%d（不是 [Map] Size 口径）' % _sz
        else:
            info['地图尺寸'] = ('%d×%d（存档自带）' % _sz) if _sz else '未读出'
        # ★★§276：任务名读**存档头部的固定字段**（以前是"猜最长的中文串"，取到的是简报，
        #   还因为试了奇偶两种对齐而多出个乱码首字 `鈀`）。字段布局（827 档实测一致）：
        #     0x1360 UTF-16 显示名（上限 44 字，00 00 结尾，后补零到 0x13B8），如「军事行动：丛林救援」
        #     0x13BA ASCII  地图 `[Basic] Name=` 的原始 CSF 键，如 `Name:ALL01md`（可空）
        #     0x13DA UTF-16 副标题/章节（上限 43 字），官方战役是「美國：導火索」，自制图常是地图名
        #     0x1450 UTF-16 任务简报（正文）
        # ★★§277（玩家给的"百分百正确"办法）：**地图文件名 → ra2md.csf 的键就是任务名**。
        #   `ALL01UMD.MAP` → `name:all01md`；`SOV01UMD.MAP` → `name:sov01md`（有 `...mdsav` 是短名）。
        #   优先级：CSF 命中 ＞ 存档 0x1360 ＞ 启发式兜底。
        _csf_txt = _csf_key = _csf_short = None
        try:
            _csf_txt, _csf_key, _csf_short = self._csf_task_name()
        except Exception:
            pass
        if _csf_txt:
            info['任务名'] = _csf_txt
            info['任务名来源'] = 'CSF：%s' % _csf_key
            info['任务名全称'] = _csf_txt
            info['任务副标题'] = _csf_short or '-'
            info['任务名短'] = _csf_short or (
                _csf_txt[len('军事行动：'):] if _csf_txt.startswith('军事行动：') else _csf_txt)
            info['名字键'] = _csf_key
            try:
                _sn0 = self.scenario_strings()
            except Exception:
                _sn0 = {}
            info['存档内任务名'] = (_sn0.get('name') or '') or '-'
        else:
            try:
                _sn = self.scenario_strings()
            except Exception:
                _sn = {}
            _full = (_sn.get('name') or '').strip()
            _short = _full[len('军事行动：'):] if _full.startswith('军事行动：') else _full
            # 统一口径：两条路都保留原文（CSF 的值本身带「军事行动：」），另给剥前缀的短名
            info['任务名'] = (_full or (_sn.get('subtitle') or '').strip()
                              or self._find_title() or '-')
            info['任务名短'] = _short or info['任务名']
            info['任务名来源'] = '存档 0x1360' if _full else '启发式兜底'
            info['任务名全称'] = _full or '-'
            info['任务副标题'] = (_sn.get('subtitle') or '').strip() or '-'
            info['名字键'] = (_sn.get('name_key') or '').strip() or '-'
            info['存档内任务名'] = _full or '-'
        # 玩家阵营
        ph = s('Player House')
        hid = ph[:4] if len(ph) >= 4 else b''
        info['玩家阵营'] = self._house_of_id(hid) or '-'
        info['玩家阵营ID'] = hid.hex(' ') if hid else '-'
        info['开始时间'] = _filetime(struct.unpack_from('<Q', s('Start Time') + b'\x00' * 8, 0)[0])
        info['存档时间'] = _filetime(struct.unpack_from('<Q', s('Last Save Time') + b'\x00' * 8, 0)[0])
        # 金钱（权威镜像优先）
        self._house_off_cache = None
        info['金钱'] = self.get_money()
        info['金钱字段'] = self._money_field_desc()
        info['所属方数组'] = '已定位 %d 个' % len(self.find_house_array()) \
            if self.find_house_array() else '未找到（仅能改当前玩家）'
        ne = sum(1 for v in self.find_house_econ().values() if v)
        info['经济块'] = '已定位 %d/12 个%s' % (ne, '' if ne == 12 else
                                        '（其余未定位 —— 大型自制任务的档里'
                                        '经济块模板会被 mod 改掉，见 README）')
        if ne < 6:
            # 自定义所属方任务：提前定位（会回填 玩家阵营/金钱/经济块 描述）
            self.find_custom_houses()
        return info

    def _money_field_desc(self):
        main, mirror = self.find_money_offsets()
        if mirror is None:
            return '未定位（该档没有所属方记录数组）'
        d = ['镜像@%d' % mirror]
        if main is not None:
            d.append('主字段@%d' % main)
        return ' / '.join(d)

    SCEN_NAME_OFF = 0x1360      # UTF-16 显示名（上限 44 字，00 00 结尾，后补零到 0x13B8）
    SCEN_KEY_OFF = 0x13BA       # ASCII：地图 `[Basic] Name=` 的原始 CSF 键（如 `Name:ALL01md`，可空）
    #   ★实测在 0x13BA 而不是 0x13B8（名字字段实际给了 45 个字宽，多占 2 字节）
    SCEN_SUB_OFF = 0x13DA       # UTF-16 副标题/章节（上限 43 字，后面是 FF 填充）
    SCEN_BRIEF_OFF = 0x1450     # UTF-16 任务简报正文

    def _utf16_at(self, off, limit, pl=None):
        """明文 `off` 处的 UTF-16LE 零结尾串（最多 `limit` 个字）→ str。"""
        pl = self.plain if pl is None else pl
        buf = bytearray()
        for k in range(int(limit)):
            pair = pl[off + 2 * k: off + 2 * k + 2]
            if len(pair) < 2 or pair == b'\x00\x00':
                break
            buf += pair
        try:
            return buf.decode('utf-16le', 'ignore').strip('\x00').strip()
        except Exception:
            return ''

    def _ascii_at(self, off, limit, pl=None):
        """明文 `off` 处的零结尾 ASCII 串（最多 `limit` 字节）→ str。"""
        pl = self.plain if pl is None else pl
        raw = bytes(pl[off: off + int(limit)]).split(b'\x00')[0]
        if not raw or any(b < 0x20 or b > 0x7e for b in raw):
            return ''
        return raw.decode('ascii', 'ignore')

    def _csf_task_name(self):
        """★§277 按玩家规则从 `ra2md.csf` 取任务名 → `(全称, 命中的键, 短名)`。

        规则：**地图文件名就是 CSF 键**（`ALL01UMD.MAP` → `name:all01md`，
        `SOV01UMD.MAP` → `name:sov01md`；`...mdsav` 那条是不带「军事行动：」的短名）。
        找不到地图 / 找不到 CSF / 键不存在 ⇒ 返回 `(None, None, None)`，由调用方回退存档字段。
        """
        mp = None
        try:
            mp = self.scenario_map_path()
        except Exception:
            mp = None
        try:
            import csf as _csf_mod
        except Exception:
            return None, None, None
        # CSF 的位置：地图所在目录 ＞ 存档所在目录 ＞ 再往上一级（玩家常把档挪进子文件夹）
        hints = [mp, self.path]
        try:
            d = os.path.dirname(os.path.abspath(self.path))
            for _ in range(2):
                d = os.path.dirname(d)
                if d:
                    hints.append(d)
        except Exception:
            pass
        path = _csf_mod.find_csf(*hints)
        if not path:
            return None, None, None
        c = _csf_mod.load(path)
        if c is None:
            return None, None, None
        full = key = None
        if mp:
            full, key = c.name_for_map(mp)
        # ⚠ 不要拿存档 0x13BA 里的 `Name:xxx` 去查 CSF 兜底：实测有反例
        #   （`B0_光棱塔同类型_带格子.SAV` 里 0x13BA=`Name:ALL01md` 但 0x1360=`军事行动：丛林救援`，
        #    那个 ASCII 键会给出别的任务名）。没地图就老实回退存档字段，别猜。
        if not full:
            return None, None, None
        short, _k2 = c.short_name_for_map(mp) if mp else (None, None)
        if not short and key:
            short = c.get(key + 'sav')
        return full, key, short

    def scenario_strings(self):
        """★§276 场景名字段（存档明文头部固定偏移）→ dict。

        827 个存档实测：`0x1360` 处永远是一个合法的任务名串（677 个是「军事行动：xxx」，
        其余是繁体/自行命名的名字，没有前缀）；`0x13B8` 有时是 `Name:ALL01md` 这种
        `[Basic] Name=` 原始 CSF 键、有时为空；`0x13DA` 是副标题；`0x1450` 才是简报正文。

        ⚠ 别再用"扫头部找最长中文串"那套：它会抓到 0x1450 的简报当任务名。
        """
        return {'name': self._utf16_at(self.SCEN_NAME_OFF, 44),
                'name_key': self._ascii_at(self.SCEN_KEY_OFF, 34),
                'subtitle': self._utf16_at(self.SCEN_SUB_OFF, 43),
                'brief': self._utf16_at(self.SCEN_BRIEF_OFF, 600)}

    def _find_title(self):
        """**兜底**：任务名。头部里最长的含 CJK UTF-16LE 串。

        ★§276 起它只在 `scenario_strings()['name']` 为空时才用。
          并且**只试偶对齐**：以前两种对齐都试，奇对齐会解出"看着像中文"的乱码
          （那份档首字被解成 `鈀`），而且乱码串往往更长 ⇒ 反而被选中。
        """
        best = ''
        pat = re.compile(u'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffefA-Za-z0-9 _:\\-]{3,}')
        head = bytes(self.plain[:16384])   # ★§276 读**明文**：容器 raw 的对齐不同，
                                            #   按 raw 偶对齐解出来仍会多一个 `鈀`
        for start in (0,):          # ★§276 只试偶对齐（奇对齐全是乱码，见 docstring）
            try:
                txt = head[start:].decode('utf-16le', 'ignore')
            except Exception:
                continue
            for m in pat.finditer(txt):
                s = m.group().strip()
                cjk = sum(1 for ch in s if '\u4e00' <= ch <= '\u9fff')
                if cjk >= 2 and len(s) > len(best):
                    best = s
        return best

    def _house_of_id(self, hid):
        """玩家阵营 ID -> 阵营短名（找 'X House' 记录）"""
        if not hid or len(hid) < 4:
            return None
        pl = self.plain
        q = 0
        while True:
            i = pl.find(hid, q)
            if i < 0:
                return None
            q = i + 1
            # 记录形式: b"X House\x00" + 若干 0 + hid
            j = i - 1
            while j >= 0 and pl[j] == 0:
                j -= 1
            k = j
            while k >= 0 and 0x20 <= pl[k] < 0x7f:
                k -= 1
            name = pl[k + 1:j + 1]
            try:
                s = name.decode('ascii')
            except Exception:
                continue
            if s.endswith(' House') and 3 <= len(s) <= 24:
                return s[:-6]
        return None

    # ---------- 金钱 ----------
    def _find_house_array_precise(self):
        """精确：以 'Americans' 为锚，其后每 464 字节依次是 Alliance/French/..."""
        pl = self.plain
        q = 0
        while True:
            p = pl.find(b'Americans', q)
            if p < 0:
                return {}
            q = p + 1
            if pl[p + 9:p + 15] == b' House':
                continue
            tmp = {}
            for i, nm in enumerate(HOUSE_CHAIN):
                o = p + HOUSE_STEP * i
                if o + len(nm) > len(pl) or pl[o:o + len(nm)] != nm.encode():
                    break
                tmp[nm] = o
            if len(tmp) >= HOUSE_MIN_CHAIN:
                for k in range(len(HOUSE_CHAIN), len(HOUSE_CHAIN) + 4):
                    o = p + HOUSE_STEP * k
                    if o + 7 <= len(pl) and pl[o:o + 7] == b'Neutral':
                        tmp['Neutral'] = o
                        break
                return tmp

    _HOUSE_NAME_RE = re.compile(rb'[A-Za-z][A-Za-z0-9_]{2,19}(?=\x00)')

    def _find_house_array_loose(self):
        """宽松：不假定名字，找一条 464 间隔、连续 ≥5 项的 ASCII 名字链。

        要求首项是已知阵营名，避免把明文里其它等距字符串误判成所属方数组。

        ★★2026-10-18 性能重写：老写法
              cands = set(m.start() for m in namere.finditer(pl))
          在 39MB 明文上会产出海量候选（正则全扫 + Python 逐个建 set），
          实测单次 **260 秒** —— 这就是大任务档"打开要 4 分钟"的唯一原因。
          现在改为**用已知阵营名做种子**：链首本来就要求是 `HOUSE_CN` 里的名字，
          所以只需在 13 个已知名字的出现处起链（13 次 `bytes.find`，C 速度）；
          链上后续项仍按"464 等距 + 该位置是个 ASCII 名字"判断，不要求是已知名。
          种子一个都没凑出链时，回落到老的全扫实现（`_loose_full`）保证不改变行为。
        """
        pl = self.plain
        namere = self._HOUSE_NAME_RE
        seeds = set()
        for nm in HOUSE_CN:
            pat = nm.encode()
            i = pl.find(pat)
            while i >= 0:
                end = i + len(pat)
                # 必须整词：后面紧跟 \0，前面不是名字字符（避免 'XAmericans' 这类误命中）
                if end < len(pl) and pl[end] == 0:
                    prev = pl[i - 1] if i > 0 else 0
                    if not (prev == 95 or 48 <= prev <= 57 or 65 <= prev <= 90 or 97 <= prev <= 122):
                        seeds.add(i)
                i = pl.find(pat, i + 1)
        best = None
        for p in sorted(seeds):
            n = 0
            for i in range(16):
                if namere.match(pl, p + HOUSE_STEP * i):
                    n += 1
                else:
                    break
            if n >= 5 and (best is None or n > best[1]):
                best = (p, n)
        if best is None:
            return self._find_house_array_loose_full()
        out = {}
        for i in range(20):
            m = namere.match(pl, best[0] + HOUSE_STEP * i)
            if not m:
                break
            out[m.group().decode('ascii', 'ignore')] = best[0] + HOUSE_STEP * i
        return out

    def _find_house_array_loose_full(self):
        """老实现（全扫 ASCII 名字）—— 只在种子法失败时兜底，39MB 档约 260 秒。"""
        pl = self.plain
        namere = self._HOUSE_NAME_RE
        cands = set(m.start() for m in namere.finditer(pl))
        best = None
        for p in sorted(cands):
            n = 0
            for i in range(16):
                if p + HOUSE_STEP * i in cands:
                    n += 1
                else:
                    break
            if n >= 5 and (best is None or n > best[1]):
                best = (p, n)
        if not best:
            return {}
        p0 = best[0]
        first = namere.match(pl, p0)
        if not first or first.group().decode('ascii', 'ignore') not in HOUSE_CN:
            return {}
        out = {}
        for i in range(20):
            m = namere.match(pl, p0 + HOUSE_STEP * i)
            if not m:
                break
            out[m.group().decode('ascii', 'ignore')] = p0 + HOUSE_STEP * i
        return out

    def find_house_array(self):
        """定位「所属方记录数组」，返回 {阵营短名: 记录起点偏移}；找不到返回 {}。

        每个记录起点 + MIRROR_DELTA 即该所属方的金钱（u24）。
        实测依据：多档中各项 +MIRROR_DELTA 处的 u24 互不相同（如 10 / 10000），
                  且与已知玩家金额吻合 ⇒ 数组平行、公式通用。
        先走精确链（名字顺序固定），失败再走宽松链（只要求 464 等距 + 首项是已知阵营）。
        """
        if getattr(self, '_house_arr', None):
            return self._house_arr
        arr = self._find_house_array_precise() or self._find_house_array_loose()
        self._house_arr = arr
        return arr

    # ★★★★★ 2026-10-17 22:5x：**House 对象 + 800 = 该阵营的金钱**（大任务档唯一可用的判据）
    #   验证：玩家档 SAVE4AE1（993795）、OK1（1000000）里 `对象+800` 与"主字段（签名定位）"
    #   逐值相同；SAVE0029（v1/v2/v3 全灭）里 12 个阵营该字段给出 0/13000/2000000/2004000
    #   —— 正是任务预设的 AI 资金。
    HOUSE_MONEY_REL = 800

    def find_house_econ(self):
        """定位每个所属方的「经济结构」金钱字段（带**防重入**与**空结果缓存**）。

        ★★2026-10-18 性能修复（大任务档打开 245s → 秒级）：
          `find_house_econ()` 内部会调 `house_keys()`，而 `house_keys()` 又会回调
          `find_house_econ()` —— 两边都"算完才缓存"，于是**互相递归 498 层**，
          每层都重扫一遍 39MB（cProfile：`bytes.find` 51151 次 / 117s，
          `_house_obj_off` 5959 次 / 105s）。
          现在：① 计算期间置 busy 标志，重入直接返回空表；
                ② 结果（**包括空表**）一律缓存 —— 原来用 `if ...:` 判真假，
                   空表永远不缓存，等于每次都重算。
        """
        if getattr(self, '_house_econ', None) is not None:
            return self._house_econ
        if getattr(self, '_house_econ_busy', False):
            return {}
        self._house_econ_busy = True
        try:
            return self._find_house_econ_impl()
        finally:
            self._house_econ_busy = False

    def _find_house_econ_impl(self):
        """定位每个所属方的「经济结构」金钱字段（真正的实现）。

        结构 = 60 字节固定模板 → u32 位掩码（= 1 << [Countries] 注册序）→ u24 金钱；
        再 +304 字节是同一阵营的**第二份副本**（值相同）。

        返回 {阵营短名: [偏移1, 偏移2]}。
        实测：多个存档里一次性命中 9 个 AI 阵营，值与该阵营在地图里的 Credits×100
        逐一吻合；用户改动地图 Credits 后新开局，这些值随之同步变化 ⇒ 确认就是金钱字段。
        （当前玩家的那份结构不含该浮点模板，故仍由 find_money_offsets 的镜像字段负责。）
        """
        pl = self.plain
        out = {}
        q = 0
        while True:
            p = pl.find(HOUSE_ECON_TPL, q)
            if p < 0:
                break
            q = p + 1
            moff = p + len(HOUSE_ECON_TPL)
            if moff + 8 > len(pl):
                continue
            mask = struct.unpack_from('<I', pl, moff)[0]
            if mask == 0 or (mask & (mask - 1)):     # 必须是 2 的幂
                continue
            idx = mask.bit_length() - 1
            if idx >= len(HOUSE_ORDER):
                continue
            lst = out.setdefault(HOUSE_ORDER[idx], [])
            mo = moff + 4
            if mo + 3 <= len(pl):
                lst.append(mo)
            # ★ +304 字段 = **引擎运行期的活钱**（2026-10-14 定案）：普通档里它与
            #   主字段（初始金钱快照）相等；mod 大任务档里它会随游戏进程分叉
            #   （实测同一档四连拍：主字段纹丝不动、+304 在涨）。
            #   ⇒ 写钱必须**两处一起写**，只写主字段 = 改了不改用
            #   （实测 Neutral 只写主字段 ⇒ 游戏内无效）。
            s = mo + HOUSE_ECON_GAP
            if s + 3 <= len(pl):
                lst.append(s)

        # ---- v2：锚点扫描（大型自制任务格式，2026-10-14 定案）----
        # mod 会把模板里的浮点（每阵营 AI 参数）改掉、位掩码字段也会变，
        # 但「51 B8 9E 3F 05 00 00 00 + 金钱@+16(u32≤0xFFFFFF)」跨档稳定：
        #   · 老档 11 块，金钱值与 v1 完全一致；
        #   · SAVE5C7B/SAVE2E1F 各 10 块，已知阵营（独特值实验 / 掩码法）全部
        #     落在顺序指派的正确槽位上（Alliance..Neutral，Special 的块模板变体
        #     不同、另由 v3 兜底）。
        anchor = bytes.fromhex('51b89e3f05000000')
        v2 = []
        q = 0
        while True:
            t = pl.find(anchor, q)
            if t < 0:
                break
            q = t + 1
            if t + 20 > len(pl):
                continue
            v = struct.unpack_from('<I', pl, t + 16)[0]
            if v <= 0xFFFFFF:
                v2.append((t + 16, v))
        # 顺序指派：11 块 = Alliance..Special；10 块 = Alliance..Neutral
        # （两个独立档都实测缺最后一块 Special —— 它的模板是变体；且已知阵营
        #   的槽位逐一吻合，见上。块数不是 10/11 时不动，避免乱指派。）
        order = HOUSE_ORDER[1:]                       # Alliance..Special
        if len(v2) in (10, 11):
            for (mo, _v), nm in zip(v2, order[:len(v2)]):
                lst = out.setdefault(nm, [])
                if mo not in lst:
                    lst.insert(0, mo)
                # +304 副本 = 引擎活钱（见 v1 处注释），无条件一起写
                s = mo + HOUSE_ECON_GAP
                if s + 3 <= len(pl) and s not in lst:
                    lst.append(s)

        # ---- v3：尾部兜底（Special 的块模板是变体，锚点扫描会漏）----
        # ★★ 2026-10-14 SAVE1F12 案例修订（旧判据已证伪删除）：
        #   旧判据要求「+304 副本 == 主字段」——但 +304 已被证实是**运行期活钱**，
        #   游戏中途存的档必然分叉（实测 8623100 vs 8623055）⇒ 旧判据把真块毙掉了。
        #   另外 Special 变体块的掩码 = **盟友位集**而非 2 的幂（实测 3073 =
        #   Americans|Neutral|Special，正是 FA2 里治安官的盟友列表），v1 也拒收。
        #   ⇒ 新判据（全部来自真块实测，窗口内唯一命中；裸放宽会误报 TaskForce/Script
        #   数组里成片的 `05 00 00 00 0a 00 00 00`，窗口内 496 处，缺一不可）：
        #   ① 金钱 u32 ≤ 0xFFFFFF、第 4 字节 0（★允许 v=0：SAVE5C7B 实测 Special
        #     快照/活钱均为 0 时块仍在，结构特征足以唯一识别——旧"值为 0 跳过"
        #     会让被改成 0 钱的所属方在编辑器里"消失"）；
        #   ② money-12..-9 == `05 00 00 00` 且 money-8..-5 == `0a 00 00 00`（模板尾常量）；
        #   ③ money-16..-13 为 f32 ∈ [0.05, 4.0]（模板 AI 参数浮点，mod 变体也在此范围）；
        #   ④ 掩码 u32@money-4 ≤ 0xFFF 且含 rest[0] 的注册位（Special 恒含自己的 bit；
        #     全档 39MB 内此特征仅命中真块——盟友含 Special 的其它阵营块都在窗口之前）；
        #   ⑤ +304 活钱 u32 第 4 字节 0，且与快照同数量级（|Δ| ≤ max(v,4096)，
        #     允许游戏进程造成的合理漂移；噪声处 v304 几乎全为 0 或相差悬殊）。
        if out:
            last = max(mo for lst in out.values() for mo in lst)
            used = {nm for nm, lst in out.items() if lst}
            rest = [nm for nm in HOUSE_ORDER[1:] if nm not in used]
            limit = min(len(pl) - 312, last + 400000)
            q = last
            while q < limit and rest:
                v = int.from_bytes(pl[q:q + 3], 'little')
                v304 = int.from_bytes(pl[q + 304:q + 307], 'little')
                mask = struct.unpack_from('<I', pl, q - 4)[0] if q >= 4 else 0
                try:
                    f = struct.unpack_from('<f', pl, q - 16)[0]
                except Exception:
                    f = 0.0
                idx0 = HOUSE_ORDER.index(rest[0])
                exp_bit = 1 << idx0
                if (v <= 0xFFFFFF and pl[q + 3] == 0
                        and pl[q - 12:q - 8] == b'\x05\x00\x00\x00'
                        and pl[q - 8:q - 4] == b'\x0a\x00\x00\x00'
                        and 0.05 <= f <= 4.0
                        and mask <= 0xFFF and (mask & exp_bit)
                        and v304 <= 0xFFFFFF and pl[q + 307] == 0
                        and abs(v304 - v) <= max(v, 4096)):
                    nm = rest.pop(0)
                    lst = out.setdefault(nm, [])
                    lst.append(q)
                    lst.append(q + HOUSE_ECON_GAP)
                    q += HOUSE_ECON_GAP
                else:
                    q += 1
        if len([v for v in out.values() if v]) < 6:
            # ★兜底（大任务档）：v1/v2/v3 都定位不到时，用 House 对象 + 800。
            #   有验证依据（见 HOUSE_MONEY_REL 注释），且只在"老判据几乎全灭"时才用，
            #   不会覆盖/干扰普通档已经验证过的经济块偏移。
            try:
                hk = self.house_keys()
            except Exception:
                hk = {}
            for nm in HOUSE_ORDER:
                lst = out.setdefault(nm, [])
                if lst:
                    continue
                k = hk.get(nm)
                off = self._house_obj_off(k) if k else None
                if off is None:
                    continue
                p = off + self.HOUSE_MONEY_REL
                if p + 4 <= len(self.plain):
                    lst.append(p)
        self._house_econ = out
        return out

    # ---- 自定义所属方任务（2026-10-14 SAVE2DD8 实测）----
    # 自制任务的所属方不叫标准 12 阵营，而是地图 [Houses] 里的自定义名
    # （'Player House' / 'BadGuy1 House'…，≤8 家）。每个 HouseClass 一条大记录：
    # 【经济块（模板→盟友位掩码→u32 金钱→+304 活钱）】…【ASCII 名 'X House\0'】。
    # 实测（8 家）：名字都在自己块的后面 ≤250KB，且块序==名序；掩码=盟友位集
    # （Player=1、BadGuy1-3+Civie1=30、Other1-3=32/64/128）；Civie1 的钱 65982400
    # **超出 u24**，金錢字段是 u32（两副本都是）。
    CUSTOM_HOUSE_SIG = re.compile(rb'[\x3d\x3e\x3f]')   # 模板参数浮点的高字节（LE 第 4 字节）

    def find_custom_houses(self):
        """自定义所属方任务的金钱定位；返回 list[dict] 或 []。

        判据三重（缺一不可，全档 11MB 裸签名会误报百余处）：
        ① 结构：f32@-16 与 f32@-24 ∈ [0.05,4]（模板 AI 参数）+ 盟友掩码 u32@-4
           ∈ (0,0xFF] + 金钱 u32 ≤ 0x03FFFFFF + 活钱副本不高于快照的合理倍数；
        ② 配对：每个块后 ≤250KB 处有唯一的 'X House\0' ASCII 名，块↔名按距离
           贪心一一配对（滤掉 'White House'/'Sydney Opera House' 之类建筑名与噪声块）；
        ③ 顺序：配对完成后块序与名序一致（记录连续存放才可信）。
        """
        if getattr(self, '_custom_houses', None) is not None:
            return self._custom_houses
        self._custom_houses = []
        pl = self.plain
        blks = []
        for m in self.CUSTOM_HOUSE_SIG.finditer(pl):
            p = m.start()                      # = money-16 的高字节 → money = p+13
            q = p + 13
            if q + 312 > len(pl):
                continue
            f1 = struct.unpack_from('<f', pl, p - 3)[0]      # money-16
            if not (0.05 <= f1 <= 4.0):
                continue
            f2 = struct.unpack_from('<f', pl, p - 11)[0]     # money-24
            if not (0.05 <= f2 <= 4.0):
                continue
            mask = struct.unpack_from('<I', pl, p + 9)[0]    # money-4
            if not (0 < mask <= 0xFF):
                continue
            v = struct.unpack_from('<I', pl, q)[0]
            v304 = struct.unpack_from('<I', pl, q + 304)[0]
            if v > 0x0FFFFFFF:          # 268M（RA2 金钱显示上限）；噪声多为 2^28 以上
                continue
            if not (v304 <= v or (v > 0 and v304 <= 4 * v + 50000)):
                continue
            blks.append((q, mask))
        if not blks:
            return []
        # 阵营名候选：'X House\0'（ASCII 名向回走到底）
        names = []
        q = 0
        while True:
            i = pl.find(b' House\x00', q)
            if i < 0:
                break
            q = i + 1
            j = i
            while j > 0 and 0x20 <= pl[j - 1] < 0x7f and i - j < 32:
                j -= 1
            nm = pl[j:i].decode('ascii', 'ignore').strip()
            if nm:
                names.append((i, nm + ' House'))
        # 贪心配对（距离短优先，名字必须在块后面）
        pairs = []
        for b, mask in blks:
            for no, nm in names:
                if b < no <= b + 250000:
                    pairs.append((no - b, b, mask, no, nm))
        pairs.sort()
        used_b, used_n = set(), set()
        out = []
        for d, b, mask, no, nm in pairs:
            if b in used_b or no in used_n:
                continue
            used_b.add(b)
            used_n.add(no)
            out.append({'name': nm, 'block': b, 'name_off': no, 'mask': mask})
        out.sort(key=lambda x: x['block'])
        # 顺序校验：块序 == 名序
        noffs = [x['name_off'] for x in out]
        if len(out) < 2 or noffs != sorted(noffs):
            return []
        # 玩家：经济块活钱 == 主字段（5C197E00+4）的那个；兑不了再用掩码=1 兕底
        mainv = None
        mi = pl.find(MONEY_SIG)
        if mi >= 0 and mi + 7 <= len(pl):
            mainv = int.from_bytes(pl[mi + 4:mi + 7], 'little')
        player = None
        for x in out:
            x['money'] = struct.unpack_from('<I', pl, x['block'])[0]
            x['live'] = struct.unpack_from('<I', pl, x['block'] + 304)[0]
        if mainv is not None:
            c = [x for x in out if (x['live'] & 0xFFFFFF) == mainv]
            if len(c) == 1:
                player = c[0]
        if player is None:
            c1 = [x for x in out if x['mask'] == 1]
            if len(c1) == 1:
                player = c1[0]
        for x in out:
            x['is_player'] = (x is player)
        if player is not None and hasattr(self, 'info'):
            self.info['玩家阵营'] = self.norm_house(player['name'])   # ★去掉 ' House' 后缀（2026-10-17）
            self.info['金钱'] = player['live']
            self.info['经济块'] = ('自定义所属方任务：定位 %d 个阵营经济块（u32 双字段）'
                                  % len(out))
        self._custom_houses = out
        return out

    def houses(self):
        """全部所属方：list[dict(short, cn, off, money_off, money_offs, money,
                                is_player, verified)]，按注册序排列、当前玩家置顶。

        money_offs = 写入时要一起改的全部偏移：
          · AI 阵营 = 经济结构里的两份副本
          · 当前玩家 = 镜像字段（+ 经济结构副本），写时另加主字段

        ★2026-10-28（审查 P3-14）：加 memo。实测本档 182 ms / 大任务档 604 ms，
          而 GUI 一次刷新要调 8 次 ⇒ 地图页切页从"卡一下"变成 0。
          缓存的是**结构**（偏移/阵营/宽度），`money` 每次调用现读
          （12 个 3~4 字节读，μs 级）—— 这样"改完钱再读"永远是新值，
          不必在 set_money / set_house_money 里额外维护失效。
          偏移会变的场景（增删字节）由 `invalidate_caches()` 统一清。
        """
        memo = getattr(self, '_houses_memo', None)
        if memo is None:
            memo = self._houses_impl()
            self._houses_memo = memo
        rows, widths = memo
        pl = self.plain
        out = []
        for d, w in zip(rows, widths):
            e = dict(d)                 # 返回副本：调用方改 dict 不会污染缓存
            o = e.get('off')
            if o is None or o + w > len(pl):
                e['money'] = None
            else:
                e['money'] = int.from_bytes(pl[o:o + w], 'little')
            out.append(e)
        return out

    def _houses_impl(self):
        """`houses()` 的实算部分 → (rows, 每行金钱字段宽度)。只在 memo 未命中时跑。"""
        pl = self.plain
        me = self.player_house()
        hk = self.house_keys()          # ★2026-10-17：阵营短名 → House 对象键（写"所属方"要用）
        econ = self.find_house_econ()
        nstd = sum(1 for v in econ.values() if v)
        if nstd < 6:
            # 自定义所属方任务（2026-10-14）：标准 12 阵营几乎全未定位时，
            # 改用自定义阵营列表（按块序，玩家置顶）
            cust = self.find_custom_houses()
            if cust:
                rows = []
                for x in cust:
                    offs = [x['block'], x['block'] + 304]
                    if x['is_player']:
                        mi = self.plain.find(MONEY_SIG)
                        if mi >= 0:
                            offs.append(mi + 4)
                    rows.append({'short': self.norm_house(x['name']),
                                 'cn': self.norm_house(x['name']),
                                 'key': hk.get(self.norm_house(x['name'])),
                                 'off': x['block'], 'money_off': x['block'],
                                 'money_offs': offs,
                                 'u32_offs': (x['block'], x['block'] + 304),
                                 'money': x['money'],
                                 'is_player': x['is_player'], 'verified': True})
                rows.sort(key=lambda d: (0 if d['is_player'] else 1,
                                         d['money_offs'][0]))
                # 自定义阵营的钱是大整数（u32，Civilian 类能到 10 万以上）
                return rows, [4] * len(rows)
        arr = self.find_house_array()
        out = []
        for short in HOUSE_ORDER:
            offs = list(econ.get(short, []))
            is_player = (short == me)
            mirror_off = None
            if is_player and short in arr:
                mo = arr[short] + MIRROR_DELTA
                if mo not in offs:
                    offs.insert(0, mo)
                mirror_off = mo
            v = None
            if offs and offs[0] + 3 <= len(pl):
                v = int.from_bytes(pl[offs[0]:offs[0] + 3], 'little')
            # 经济块副本是 u32（第 4 字节恒 0，写 4 字节不变；Civie1 类大金额需要）；
            # 玩家镜像字段是 u24
            u32 = tuple(mo for mo in offs if mo != mirror_off)
            out.append({'short': short, 'cn': HOUSE_CN.get(short, ''),
                        'key': hk.get(short),
                        'off': offs[0] if offs else None,
                        'money_off': offs[0] if offs else None,
                        'money_offs': offs, 'u32_offs': u32, 'money': v,
                        'is_player': is_player, 'verified': bool(offs)})
        idx = {n: i for i, n in enumerate(HOUSE_ORDER)}
        out.sort(key=lambda d: (0 if d['is_player'] else 1, idx.get(d['short'], 99)))
        # 标准阵营：玩家镜像字段是 u24，所以按 3 字节读
        return out, [3] * len(out)

    def player_house(self):
        """当前玩家阵营短名（info 尚未建立时返回 None）。

        ★2026-10-17 修：`find_custom_houses()` 会把 **"Americans House"** 整串写进
          `info['玩家阵营']`（'X House' 形式），而 `houses()['short']` / `HOUSE_ORDER` 用的是
          **去掉 " House" 的短名** ⇒ 二者不相等会让 `is_player`、镜像金钱、`set_money` 的分支
          全部落空。这里统一去掉 " House" 后缀，成为唯一的"阵营短名"口径。
        """
        h = self.info.get('玩家阵营') if hasattr(self, 'info') else None
        if not h or h == '-':
            return None
        return self.norm_house(h)

    @staticmethod
    def norm_house(name):
        """阵营名归一化：去掉 "X House" 的 " House" 后缀（唯一口径）。"""
        s = str(name or '').strip()
        return s[:-6] if s.endswith(' House') else s

    def find_money_offsets(self):
        """返回 (main_off, mirror_off)，均为 u24。

        镜像偏移**只**来自所属方记录数组；定位不到就是 None。
        （早期版本会退回“短名首次出现 + 7708”，实测在某些存档里那个位置是
          数据区的裸名字串而非记录起点，会读出垃圾值，故已废弃该回退。）
        """
        i = self.plain.find(MONEY_SIG)
        main = i + 4 if i >= 0 else None
        me = self.player_house()
        arr = self.find_house_array()
        mirror = arr[me] + MIRROR_DELTA if (me and me in arr) else None
        return main, mirror

    def get_money(self):
        """当前玩家金钱。未定位到所属方数组（即镜像字段）时返回 None —— 不再猜测，
        避免把 CSF 标签/主字段里的无关数字当成金钱显示出来。"""
        main, mirror = self.find_money_offsets()
        if mirror is None:
            return None
        pl = self.plain
        for off in (mirror, main):
            if off is not None and off + 3 <= len(pl):
                v = int.from_bytes(pl[off:off + 3], 'little')
                if 0 <= v <= 0xFFFFFF:
                    return v
        return None

    @staticmethod
    def _chk_money(value):
        v = int(value)
        if not (0 <= v <= 0xFFFFFF):
            raise ValueError('金额需在 0 ~ 16,777,215 之间')
        return v

    def set_money(self, value):
        """改当前玩家金钱（等价于 set_house_money(玩家阵营, v)）。

        未定位到所属方数组时**拒绝写入** —— 此时任何写入都是基于未知偏移的猜测，
        宁可报错也不要改坏存档里的其它数据。
        """
        me = self.player_house()
        if me:
            for x in self.houses():
                if x['short'] == me:
                    if x['money_offs']:
                        return self.set_house_money(me, value)
                    break
        if me and me in self.find_house_array():
            return self.set_house_money(me, value)
        raise RuntimeError('该存档未定位到所属方记录数组；为避免改错位置，已拒绝写入。\n'
                           '可改用“所属方金钱”页，或选另一个存档。')

    def set_house_money(self, short, value):
        """改指定所属方的金钱。

        定位依据见 find_house_econ() / find_custom_houses()：
        AI 阵营写「经济结构」的两份副本（★u32，2026-10-14： Civie1 实测 65982400
        超出 u24，字段是 4 字节；标准块第 4 字节恒 0，写 4 字节不变）；
        当前玩家写其镜像字段（u24，上限 16777215）+ 经济块副本 + 主字段。
        """
        h = None
        for x in self.houses():
            if x['short'] == short:
                h = x
                break
        if not h or not h['money_offs']:
            raise RuntimeError('该存档里未定位到所属方 %s 的金钱字段' % short)
        value = int(value)
        u32 = set(h.get('u32_offs') or ())
        if not u32 or short == self.player_house():
            if not (0 <= value <= 0xFFFFFF):
                raise ValueError('金额需在 0 ~ 16,777,215 之间'
                                 '（玩家金钱的镜像/主字段是 3 字节 u24）')
        elif not (0 <= value <= 0xFFFFFFFF):
            raise ValueError('金额需在 0 ~ 4,294,967,295 之间')
        old = h['money']
        for mo in h['money_offs']:
            n = 4 if mo in u32 else 3
            if mo + n <= len(self.plain):
                self.plain[mo:mo + n] = (value & 0xFFFFFFFF).to_bytes(n, 'little')
        self.pending_houses[short] = value
        if short == self.player_house():
            i = self.plain.find(MONEY_SIG)
            if i >= 0 and i + 7 <= len(self.plain):
                self.plain[i + 4:i + 7] = value.to_bytes(3, 'little')
            self.pending_money = value
        return old

    # ---------- 事件表 / 行为表（对象签名定位）----------
    def _invalidate_if_shifted(self):
        """明文长度变了 ⇒ 所有"按偏移缓存"的结果作废。

        ★2026-10-17 22:1x 修（玩家 GUI 实测：先放一栋楼、再用触发法就报
          「事件组计数注册字段(0)与实际对象数(615)不符」）：
          放建筑/放单位/新写触发都是**插入**，明文变长后旧的 `_objs` 偏移全部失效，
          于是"组计数"读到了别处的 0。原地改（长度不变）的偏移仍然有效，故用长度做判据。
        """
        n = len(self.plain)
        if getattr(self, '_cache_len', None) == n:
            return
        self._cache_len = n
        self._objs = None            # 事件/行为对象
        self._blds = None            # 建筑对象
        self._coffs = None           # 格对象偏移表
        self._ckey = None            # 格键 → 坐标
        self._events = None
        self._actions = None
        self._celltags = None
        self._btypes = None
        self._house_keys_memo = None
        self._bld_tmp = None
        # ★★§217（自检抓到的真 bug）：这里原来**漏了实例映射表** ——
        #   插入新触发器之后 `_instance_maps()` 仍返回插入前的映射，
        #   于是 `trigger_actions(新触发)` 报「没有行为对象」。
        #   GUI 里更容易复现：打开存档时触发页已经把这些表建好了。
        self._instmaps = None
        self._tagobjs = None
        self._wpbase_memo = None     # §273 路径点表基址（全档 find 定位）
        self._tagrecs = None
        self._ctimerlist = None
        self._navmemo = None
        # ★§256：挂接总览 / House 块也按偏移缓存，插入后必须作废
        self._attov = None
        self._hblocks = None

    def _objects(self):
        self._invalidate_if_shifted()
        """定位全部「事件/行为 对象」，返回按偏移升序的 [dict(obj, off, type, arg)]。

        ★★★ 判据（2026-10-13 实测确立，替代此前的固定步长启发式）
        每个事件/行为对象体内含一段 **16 字节恒定签名**（4 个 u32，跨档稳定，
        7 档 + 58 档批量验证 100% 命中）：
            obj+0  : 4F0EC392 / 4F0EC393（随对象微变）
            obj+4  : 11D20A55
            obj+8  : 6000A7AC
            obj+12 : B55B0508
        对象布局：**类型在 obj+64、参数在 obj+72** ⇒ 我们对外报的「记录起点」= obj+64。

        数组结构：`[行为对象…] [4 字节] [事件对象…]` 连续存放。
        - 行为对象**固定 168 字节**
        - 事件对象尺寸**随档变化**（小任务档 108，大 mod 任务档实测 124）
          ⇒ **绝不能用固定步长**拼链（这正是旧实现锁定到巧合假链的根因）。

        旧实现（按 `[类型][0][参数]` 三元组 + 108 步长拼链）已**删除**：
        明文里有大量巧合三元组（实测同一档 10 万+ 条候选），
        曾把浮点数组当成事件表显示成「类型 63/64/72」等垃圾值 —— 该方案已证伪。
        """
        if getattr(self, '_objs', None) is not None:
            return self._objs
        pl = self.plain
        pos, q = [], 0
        while True:
            i = pl.find(OBJ_SIG, q)          # 签名第 2~4 字
            if i < 0:
                break
            q = i + 1
            o = i - 4                        # 签名第 1 字
            if o < 64 or o + 76 > len(pl):
                continue
            t = int.from_bytes(pl[o + OBJ_TYPE_OFF:o + OBJ_TYPE_OFF + 4], 'little')
            a = int.from_bytes(pl[o + OBJ_ARG_OFF:o + OBJ_ARG_OFF + 4], 'little')
            # ★ 必须**允许 type == 0**：类型 0（-无事件-/-无行为-）虽然危险，但一旦被写进去，
            #   对象依然存在。若在此过滤掉，数组就会出现"空洞" ⇒ 工具自己读错
            #   （实测：把 2 个事件改成 0 后，界面只剩 2 条事件，地图对拍也报"数量不符"）。
            if t > 0xFFFF or a > 0xFFFFFF:
                continue
            pos.append({'obj': o, 'off': o + OBJ_TYPE_OFF, 'type': t, 'arg': a,
                        'key': int.from_bytes(pl[o + 16:o + 20], 'little')})
        pos.sort(key=lambda d: d['obj'])
        self._objs = pos
        return pos

    def _split_tables(self):
        """把对象序列切成 (行为列表, 事件列表)。

        规则：从左侧起，**行为对象之间恒为 168 字节**；第一个 Δ≠168 处即
        「行为段 → 事件段」的分界（实测分界 Δ=172 = 168 + 4 字节间隙）。
        切完做一次**类型空间校验**（行为段类型须全在 ACTION_TYPES，事件段须全在
        EVENT_TYPES），不通过则退回「全是事件」并标记低置信（宁可不给，也不乱给）。
        """
        objs = self._objects()
        if not objs:
            return [], []
        if len(objs) == 1:                   # 只有一个对象：按类型空间判归属
            t = objs[0]['type']
            if t in ACTION_TYPES and t not in EVENT_TYPES:
                return objs, []
            if t in EVENT_TYPES and t not in ACTION_TYPES:
                return [], objs
            return [], objs                 # 两边都合法 ⇒ 保守按事件处理
        cut = len(objs)                # 默认：全是行为（无分界）
        for i in range(len(objs) - 1):
            if objs[i + 1]['obj'] - objs[i]['obj'] != ACTION_SIZE:
                cut = i + 1
                break
        acts, evs = objs[:cut], objs[cut:]
        ok_a = all(o['type'] in ACTION_TYPES for o in acts)
        ok_e = all(o['type'] in EVENT_TYPES for o in evs)
        if not (ok_a and ok_e):             # 校验失败 ⇒ 退化（不猜）
            self._tables_lowconf = True
            return [], objs
        return acts, evs

    # ---------- 「流逝时间」运行期帧数 ----------
    def tick_blocks(self):
        """全档扫描 `C4 60 72 00` 标记块，返回 {帧数字段的绝对偏移: 值}。

        值 = 等待秒数 × 15（校验：必须能被 15 整除，否则视为巧合命中、剔除）。
        """
        if getattr(self, '_ticks', None) is not None:
            return self._ticks
        pl = self.plain
        cur = self.plain
        out = {}
        q = 0
        while True:
            i = pl.find(TIME_MARK, q)
            if i < 0:
                break
            q = i + 1
            if i + 8 > len(pl):
                continue
            v = int.from_bytes(cur[i + 4:i + 8], 'little')   # 取当前值（便于连续编辑）
            if 0 < v <= 0xFFFFFFF and v % LOGIC_FPS == 0:
                out[i + 4] = v
        self._ticks = out
        return out

    def time_ticks(self):
        """当前档里「流逝时间」的帧数值（唯一时返回，多个/没有时返回 None）。"""
        b = self.tick_blocks()
        return list(b.values())[0] if len(b) == 1 else None

    def sync_time_ticks(self, old_arg, new_arg, match_by_value=True):
        """把「流逝时间」的运行期帧数从 old_arg×15 同步成 new_arg×15。

        只在能**唯一确定**目标时写入：优先按"旧值 = old_arg×15"匹配；
        匹配不到时，若全档只有一个帧数块且只有一个事件，也认（单触发地图）。
        其余情况**不猜**，返回 None 并留下说明文字。
        """
        self.time_sync_note = ''
        want, new = int(old_arg) * LOGIC_FPS, int(new_arg) * LOGIC_FPS
        cands = [o for o, v in self.tick_blocks().items() if v == want] if match_by_value \
            else []
        if not cands:
            allb = self.tick_blocks()
            if len(allb) == 1 and len(self.events()) == 1:
                cands = list(allb.keys())      # 单触发 / 单事件 ⇒ 唯一目标，直接写
            else:
                self.time_sync_note = (
                    '⚠ 未找到与 %d 秒匹配的运行期帧数（应为 %d）：本次只改了事件里的秒数，'
                    '读档后等待时间可能仍是原值。' % (old_arg, want))
                return None
        for o in cands:
            self.plain[o:o + 4] = new.to_bytes(4, 'little')
        self._ticks = None
        self.time_sync_note = ('已同步运行期帧数 %d → %d（等待 %d 秒 → %d 秒，%d fps）'
                               % (want, new, old_arg, new_arg, LOGIC_FPS))
        return new

    def events(self):
        self._invalidate_if_shifted()
        """事件数组：list[dict(off, type, name, ptype, arg, arg_text)]

        顺序 = 地编事件列表顺序（同一触发的事件连续）。★ 归属仍不可直接读取
        （存档里没有「每触发挂几个事件」的字段），故不猜归属。
        """
        if getattr(self, '_events', None) is not None:
            return self._events
        _, evs = self._split_tables()
        out = [self._mk('event', o) for o in evs]
        self._events = out
        return out

    def actions(self):
        self._invalidate_if_shifted()
        """行为数组，字段同事件（类型编号与地编行为列表一一对应）。"""
        if getattr(self, '_actions', None) is not None:
            return self._actions
        acts, _ = self._split_tables()
        out = [self._mk('action', o) for o in acts]
        # §175：行为 56/57 的真正参数是 +164 的变量号；+72 恒 0（引擎也不看）
        pl = self.plain
        for d in out:
            if d.get('type') in ACTION_VAR_TYPES:
                o = d.get('obj')
                if o is not None and o + ACT_VAR_OFF + 4 <= len(pl):
                    v = int.from_bytes(pl[o + ACT_VAR_OFF:o + ACT_VAR_OFF + 4], 'little')
                    d['var'] = v
                    d['arg'] = v
        self._actions = out
        return out

    # ================= §175 局部变量表（先有变量，触发才选得中）=================
    # 玩家原话：「在地编中要先写局部变量，写完后写触发时才选择写好的局部变量，
    #           局部变量的总数为 99 条，超过无效」
    # ⇒ 本组 API 就是"先写局部变量"那一步；`make_local_var_recipe` 会自动调用。
    def _lv_sane(self):
        """明文里 0x248A 起是否是变量表（100 槽 × 41B，槽内 name[40]+value）。"""
        need = LOCALVAR_BASE + LOCALVAR_COUNT * LOCALVAR_SLOT
        if len(self.plain) < need:
            return False
        # 抽查前 8 槽：要么全 0，要么是"可打印 ASCII 且以 0 结束"的名字
        for i in range(8):
            off = LOCALVAR_BASE + i * LOCALVAR_SLOT
            raw = bytes(self.plain[off:off + LOCALVAR_NAME_LEN])
            body = raw.split(b'\x00')[0]
            if not body:
                continue
            if len(body) >= LOCALVAR_NAME_LEN or not all(32 <= c < 127 for c in body):
                return False
        return True

    def _lv_offset(self, idx, what='变量'):
        idx = int(idx)
        if not (0 <= idx < LOCALVAR_COUNT):
            raise ValueError('%s索引 %d 越界（引擎只认 0..%d，地编名称框是 1..99）'
                             % (what, idx, LOCALVAR_COUNT - 1))
        off = LOCALVAR_BASE + idx * LOCALVAR_SLOT
        if off + LOCALVAR_SLOT > len(self.plain):
            raise RuntimeError('明文太短（%d B），变量表偏移 0x%X 越界' % (len(self.plain), off))
        return off

    def local_var_table(self):
        """读 100 槽 → `[{index, fa2, name, value, default, source, off}]`。

        `index` = 引擎参数（事件/行为里写的号，0 基）；`fa2` = 地编名称框里的"第几条"。
        `value` = 存档里的运行期值（**读档后生效的就是它**，存档自足）；
        `name`/`default` 优先取**地图 `[VariableNames]`**（`source='map'`，那是地编里能选中的
        清单与开局默认值），地图找不到时退回存档镜像（`source='save'`）。
        """
        pl = self.plain
        mvar = self._map_vars_map()
        out = []
        for i in range(LOCALVAR_COUNT):
            off = LOCALVAR_BASE + i * LOCALVAR_SLOT
            if off + LOCALVAR_SLOT > len(pl):
                break
            raw = bytes(pl[off:off + LOCALVAR_NAME_LEN])
            nm_save = raw.split(b'\x00')[0].decode('latin1', 'replace')
            mv = mvar.get(i)
            out.append({'index': i, 'fa2': i + 1,
                        'name': nm_save,              # ★存档里的名字（本工具编辑的对象）
                        'name_save': nm_save,
                        'name_map': (mv['name'] if mv else None),   # 地图里定义的名字
                        'value': pl[off + LOCALVAR_NAME_LEN],
                        'default': (mv['default'] if mv else None),  # 地图里的默认值
                        'source': 'map' if mv else 'save',
                        'off': off})
        return out

    def _map_vars_map(self, extra_dirs=None):
        """`{index: {name, default}}`（带 path+mtime 缓存，地图一改自动失效）。"""
        p = self.scenario_map_path(extra_dirs)
        if not p:
            return {}
        try:
            ck = (p, os.path.getmtime(p), os.path.getsize(p))
        except OSError:
            return {}
        if getattr(self, '_mapvars_key', None) == ck:
            return getattr(self, '_mapvars_cache', {}) or {}
        try:
            r = read_scenario_variables(p)
        except Exception:
            return {}
        self._mapvars_cache = {i: {'name': n, 'default': d} for (i, n, d) in r['items']}
        self._mapvars_key = ck
        return self._mapvars_cache

    def local_var(self, idx):
        off = self._lv_offset(idx)
        raw = bytes(self.plain[off:off + LOCALVAR_NAME_LEN])
        row = self.local_var_table()[int(idx)]
        return {'index': int(idx), 'fa2': int(idx) + 1,
                'name': row['name'], 'name_save': row['name_save'],
                'name_map': row['name_map'],
                'value': self.plain[off + LOCALVAR_NAME_LEN],
                'default': row['default'], 'source': row['source'], 'off': off}

    def set_local_var_name(self, idx, name):
        """给**存档镜像**里的变量命名（空字符串 = 清名）。

        ⚠这只是**存档运行态**里的名字；地编/FA2 里能选中的变量清单来自地图
          `[VariableNames]`（开局默认值也在那）。要"地编里也看得见"请用 `define_local_var()`。
        """
        off = self._lv_offset(idx)
        s = ('' if name is None else str(name)).strip()
        b = s.encode('ascii', 'ignore')[:LOCALVAR_NAME_LEN - 1]
        self.plain[off:off + LOCALVAR_NAME_LEN] = b.ljust(LOCALVAR_NAME_LEN, b'\x00')
        return self.local_var(idx)

    def set_local_var_value(self, idx, value):
        """**直接写存档里的变量值**（绕开行为 56）。事件 36 判定的是"值 ≠ 0"。

        存档自足（§176 勘误）：这个值读档后就生效。地图里的默认值只影响**新开一局**；
          想让新开局也是 v，请用 `set_local_var_default()`（同时改地图）。
        返回 dict 的 `persist` 表示"该号是否也在地图里有定义"（True = 与地图一致/无冲突）。
        """
        off = self._lv_offset(idx)
        v = int(value)
        if not 0 <= v <= 255:
            raise ValueError('变量值必须是 0..255（引擎按 1 字节存）：%r' % (value,))
        self.plain[off + LOCALVAR_NAME_LEN] = v
        d = self.local_var(idx)
        d['persist'] = (d.get('source') != 'map')
        if d.get('source') == 'map':
            d['note'] = ('该变量在地图里已定义（默认值 %s）⇒ 读档时值会被重置为 %s；'
                         '要改初值请用 set_local_var_default()'
                         % (d.get('default'), d.get('default')))
        return d

    def clear_local_var(self, idx):
        """清空一个变量（名字 + 值都归 0）。"""
        off = self._lv_offset(idx)
        self.plain[off:off + LOCALVAR_SLOT] = b'\x00' * LOCALVAR_SLOT
        return self.local_var(idx)

    def ensure_local_var(self, idx, name=None, value=None, extra_dirs=None):
        """确保某号变量**在存档里有名字**：没有就补一个（默认 `V<n>`），可顺手设值。

        ★★2026-10-27 铁律后：**只写存档**（`0x248A` 那 100 槽），**绝不写地图**。
          `write_map` 参数已删除 —— 旧代码传它会立刻 TypeError（宁可炸，不要静默改地图）。
          地图里那份 `[VariableNames]` 只读，用来在地编侧显示"这号本来叫什么"。
        """
        cur = self.local_var(idx)
        if name is not None:
            self.set_local_var_name(idx, name)
        elif not cur['name_save']:
            self.set_local_var_name(idx, 'V%d' % (int(idx) + 1))
        # 注：地图里的默认值不动（那是"开局初值"，不是本次要改的东西；而且本工具不写地图）
        if value is not None:
            self.set_local_var_value(idx, value)
        return self.local_var(idx)

    def local_var_defined(self):
        """已命名（地图里定义过或镜像里有名）的变量 → `[{index, fa2, name, value, default}]`。"""
        return [v for v in self.local_var_table() if v['name']]

    # ---- ★§176 地图端：变量的**定义**（名字 + 默认值）只认地图 `[VariableNames]` ----
    def scenario_map_name(self):
        """存档里内嵌的场景文件名（如 `ALL01UMD.MAP`）→ str 或 None。

        实测位置：明文 `0x1264` 附近，`[u32 1][名字 + \\0 填充]`，全大写、无目录。
        """
        pl = bytes(self.plain)
        low = pl.lower()
        for ext in SCENARIO_EXTS:
            q = low.find(ext.encode('ascii'))
            while q >= 0:
                s = q
                while s > 0 and 32 <= pl[s - 1] < 127:
                    s -= 1
                name = pl[s:q + len(ext)].decode('latin1')
                if 3 < len(name) <= 80 and '\\' not in name and '/' not in name:
                    return name
                q = low.find(ext.encode('ascii'), q + 1)
        return None

    def scenario_map_path(self, extra_dirs=None, refresh=False):
        """定位场景地图文件 → 路径 或 None（找不到时给出 `map_lookup_hint()`）。"""
        if not refresh and getattr(self, '_map_path_done', False):
            return getattr(self, '_map_path', None)
        nm = self.scenario_map_name()
        self._map_name = nm
        # ★带上存档自带尺寸做同名异图过滤（官方 ALL01UMD.MAP 也是这个名字）
        try:
            sz = self.map_size()
        except Exception:
            sz = None
        self._map_path = find_scenario_map(self.path, nm, extra_dirs, size=sz)
        if not self._map_path:
            # ★§210：存档自带的尺寸表不是每份档都可靠（`有空地.SAV` 读出 (19,177)，
            #   实际地图是 180×120）⇒ 尺寸过滤失败时退回"只按名字找"。
            #   地形检查/空位搜索都依赖这张地图，找不到就等于盲放。
            _cand = find_scenario_map(self.path, nm, extra_dirs, size=None)
            # ★同名不同图的风险：用"地图格集 ⊆ 存档格对象"验真（实测正确图 100% 重合）
            if _cand:
                try:
                    _mk = read_isomap(_cand)
                    _ck = self.cell_offsets()
                    _hit = sum(1 for k in _mk if k in _ck)
                    if _mk and _hit < len(_mk) * 0.5:
                        _cand = None
                except Exception:
                    _cand = None
            self._map_path = _cand
        self._map_path_done = True
        return self._map_path

    def map_lookup_hint(self):
        """没找到地图时给玩家的提示（含内嵌名与搜索过的目录）。"""
        nm = getattr(self, '_map_name', None) or self.scenario_map_name() or '(未识别)'
        return ('存档内嵌场景名 = %s；请在存档同目录（或 E:/YURI）放同名地图文件，'
                '或手动指定地图路径' % nm)

    def map_variables(self, extra_dirs=None):
        """读地图里已定义的变量 → `{'path','exists','items':[{index,fa2,name,default}]}`。"""
        p = self.scenario_map_path(extra_dirs)
        r = read_scenario_variables(p)
        out = {'path': p, 'exists': bool(r['exists']), 'items': [],
               'map_name': self.scenario_map_name()}
        for (i, n, d) in r['items']:
            out['items'].append({'index': i, 'fa2': i + 1, 'name': n, 'default': d})
        if not p:
            out['hint'] = self.map_lookup_hint()
        return out

    def map_waypoints(self, extra_dirs=None):
        """读地图 `[Waypoints]` → `{'path','exists','items':{号:(x,y)}}`（编码 `y*1000+x`）。"""
        p = self.scenario_map_path(extra_dirs)
        r = read_scenario_waypoints(p)
        out = {'path': p, 'exists': bool(r['exists']), 'items': dict(r['items']),
               'map_name': self.scenario_map_name()}
        if not p:
            out['hint'] = self.map_lookup_hint()
        return out

    def register_waypoints_to_map(self, *a, **kw):
        """★已废：本工具不写地图（玩家 2026-10-27 铁律）。旧调用会立刻抛 `MapWriteForbidden`。

        要做"地图里也有这个路径点"，请在地编里自己画（或在 FA2 里打开地图加）。
        """
        raise MapWriteForbidden(MAP_WRITE_REFUSED)

    def define_local_var(self, idx, name=None, default=0, extra_dirs=None, map_path=None):
        """**给某一号变量起名 / 给个初值 —— 只写存档**（`0x248A` 那 100 槽）。

        ★★2026-10-27 铁律：本工具**不允许写地图**，所以这里**不再动 `[VariableNames]`**。
          `extra_dirs` / `map_path` 只为兼容旧调用签名而保留，**一律忽略**。
          ⇒ 名字只存在于存档里：本工具读得到、触发照跑（事件 36/37 判的是存档值），
            但地编 FA2 里选不中 —— 那一步必须由玩家在地图里自己做（本工具不代劳）。
        `name=None` ⇒ 保留原名（存档里没有就补 `V<n>`）；`default` 同时写进当前值。
        返回 `{'index','fa2','name','default','value_in_save','map_written':False}`。
        """
        idx = int(idx)
        if not (0 <= idx < LOCALVAR_COUNT):
            raise ValueError('变量索引 %d 越界（引擎只认 0..%d）' % (idx, LOCALVAR_COUNT - 1))
        if name is not None:
            _n = str(name).strip()
            if _n.isdigit():
                raise ValueError('变量名不能是纯数字（%r 不是合法变量名）' % (_n,))
            if _n:
                self.set_local_var_name(idx, _n)
        elif not self.local_var(idx)['name_save']:
            self.set_local_var_name(idx, 'V%d' % (idx + 1))
        v = int(default or 0) & 0xFF
        self.set_local_var_value(idx, v)
        out = self.local_var(idx)
        out.update({'default': v, 'value_in_save': v, 'map_written': False})
        return out

    def set_local_var_default(self, *a, **kw):
        """★已废：默认值在地图里，而本工具不写地图（玩家 2026-10-27 铁律）。

        想改"这一档里它的值"，用 `set_local_var_value()`（按钮「存档值置 1/0」）。
        """
        raise MapWriteForbidden(MAP_WRITE_REFUSED)

    def remove_local_var(self, *a, **kw):
        """★已废：删除地图里的定义要改地图 —— 禁止。清存档侧请用 `clear_local_var()`。"""
        raise MapWriteForbidden(MAP_WRITE_REFUSED)


    # ★★★ 2026-10-18 定案：**行为是链表** —— 行为对象 `obj + 0x3C` = 下一条行为的自键，
    #   写成 0 就是这条触发的最后一条。实测 SAVE0029（大任务档）：
    #     1197 条指向下一条 + 616 条链尾(0) = 1813 = **全部行为，0 例外**；
    #     616 个触发实例的 `+196` 恰好是 616 条链的头 ⇒ **一条链 = 一个触发的全部行为**。
    #   ⇒ 有了它，不挂地图也能把每条行为归属到触发（以前只有链头那 616 条能认领）。
    ACTION_NEXT_OFF = 0x3C

    def action_owner_sids(self):
        """{行为自键: 触发 sid} —— 沿行为链把**每一条**行为都归属到触发（无需地图）。"""
        pl = self.plain
        by_key = {a.get('key'): a for a in self.actions() if a.get('key')}
        out = {}
        for sid, d in self.runtime_links().items():
            k, seen = d['ac'], set()
            while k and k in by_key and k not in seen:
                seen.add(k)
                out.setdefault(k, sid)
                if by_key[k]['obj'] + self.ACTION_NEXT_OFF + 4 > len(pl):
                    break
                k = struct.unpack_from('<I', pl, by_key[k]['obj'] + self.ACTION_NEXT_OFF)[0]
        return out

    def pending_build_triggers(self):
        """本档里**「行为 125 将建筑建于…」**的触发 → `[(sid, 名字)]`。

        ★2026-10-22 §188（玩家实踩）：这些触发**读档就会建建筑**。
        我们做实验时用「⚡ 触发法」加楼会留下这种触发；之后每次拿这份档当基档做别的实验，
        它们都会额外刷出建筑 ⇒ 实验结果被污染（T14 就是这样废掉的）。
        调用方（GUI/脚本）应当先提示或先 `soft_disable_trigger()`。
        """
        owners = self.action_owner_sids()
        names = {r.sid: r.name for r in self.records if r.kind == '触发'}
        out, checked = [], {}
        for a in self.actions():
            if a.get('type') != 125:
                continue
            sid = owners.get(a.get('key'))
            if not sid:
                continue
            if sid not in checked:
                # ★只报「真的会触发」的：软禁用过的（事件 13 + 参数拉到上限）要排除
                try:
                    ev = (self.trigger_detail(sid) or {}).get('event') or {}
                    dead = (ev.get('type') == 13
                            and int(ev.get('arg') or 0) >= self.SOFTOFF_TIME_SEC)
                except Exception:
                    dead = False
                checked[sid] = not dead
            if checked[sid] and (sid, names.get(sid)) not in out:
                out.append((sid, names.get(sid) or sid))
        return out

    def action_chain(self, head_key, limit=512):
        """从一条行为自键出发，沿 `obj+0x3C` 走完整条链 → [行为自键, ...]。"""
        pl = self.plain
        by_key = {a.get('key'): a for a in self.actions() if a.get('key')}
        out, seen, k = [], set(), head_key
        while k and k in by_key and k not in seen and len(out) < limit:
            out.append(k)
            seen.add(k)
            if by_key[k]['obj'] + self.ACTION_NEXT_OFF + 4 > len(pl):
                break
            k = struct.unpack_from('<I', pl, by_key[k]['obj'] + self.ACTION_NEXT_OFF)[0]
        return out

    # ★★★ 2026-10-21 §157：事件也是链表（`obj+0x3C` = 下一条事件自键），与行为同构。
    #   链头 = 触发实例 `+192`。以前只有行为走了链，事件只看链头那一条。
    EVENT_NEXT_OFF = 0x3C

    def event_chain(self, head_key, limit=512):
        """从一条事件自键出发，沿 `obj+0x3C` 走完整条链 → [事件自键, ...]。"""
        pl = self.plain
        by_key = {e.get('key'): e for e in self.events() if e.get('key')}
        out, seen, k = [], set(), head_key
        while k and k in by_key and k not in seen and len(out) < limit:
            out.append(k)
            seen.add(k)
            if by_key[k]['obj'] + self.EVENT_NEXT_OFF + 4 > len(pl):
                break
            k = struct.unpack_from('<I', pl, by_key[k]['obj'] + self.EVENT_NEXT_OFF)[0]
        return out

    def trigger_event_list(self, sid):
        """触发的事件链 → [{'type','arg','name','key'}]（按链序，链头取实例 +192）。"""
        d = self.runtime_links().get(sid)
        if not d:
            return []
        by_key = {e.get('key'): e for e in self.events() if e.get('key')}
        out = []
        for k in self.event_chain(d.get('ev')):
            e = by_key.get(k)
            if e:
                out.append({'type': e.get('type'), 'arg': e.get('arg'),
                            'name': e.get('name'), 'key': k})
        return out

    def _mk(self, kind, o):        # ★改成实例方法：参数文案要用到阵营顺序（house_order）
        t, v = o['type'], o['arg']
        nm, pt = (event_name(t), event_ptype(t)) if kind == 'event' else \
                 (action_name(t), action_ptype(t))
        return {'off': o['off'], 'obj': o['obj'], 'type': t, 'arg': v,
                'name': nm, 'ptype': pt, 'arg_text': self._arg_text(pt, v),
                'key': o.get('key'),
                'owner': None, 'map_type': None, 'map_arg': None}

    def _revalidate_event(self, ev):
        """校验 ev 的偏移仍指向同一事件对象（恒定签名 + 自键），漂移则重定位。

        ★ 背景：同会话内计时表登记 / 修复等操作会**插字节**，GUI/循环里持有的
        ev 字典偏移会全体漂移 ⇒ 直接按旧 off 写 = 写进别的对象。签名
        （obj+4..16 恒定 12B）+ 自键双校验，不匹配就按键回查新位置。"""
        key = ev.get('key')
        obj = ev.get('obj')
        if key is not None and 0 <= obj and obj + 20 <= len(self.plain) and \
                self.plain[obj + 4:obj + 16] == OBJ_SIG[4:] and \
                int.from_bytes(self.plain[obj + 16:obj + 20], 'little') == key:
            return
        if key is None:
            return
        for od in self._objects():
            if int.from_bytes(self.plain[od['obj'] + 16:od['obj'] + 20], 'little') == key:
                ev['obj'] = od['obj']
                ev['off'] = od['off']
                return
        raise RuntimeError('事件对象（键 %08X）重定位失败 —— 明文结构异常' % key)

    # ---------- 地图对照（权威真值 + 归属）----------
    def attach_map(self, path):
        """挂载地图文件（.map/.yrm）。

        地图 INI 的 `[Events]` / `[Actions]` **以触发ID 为键**，是「哪个触发挂哪些事件/行为」
        的唯一权威来源（存档里没有这个归属字段）。挂上后：
          - `events()` / `actions()` 的每项多出 `owner`（所属触发ID）与 `map_type/map_arg`（地图值）
          - `map_status` 给出对拍结论（值完全一致 / 仅结构一致 / 数量不符 / 无地图）
        """
        self._map = MapIni(path) if path else None
        self._mapm = None
        self._events = None
        self._actions = None
        self._apply_map()
        if path:
            try:
                ids = set(r.sid for r in self.records if r.kind == '触发')
                self._assoc_put(ids, path)
            except Exception:
                pass
        return self._mapm['mode'] if self._mapm else 'no_map'

    # ---------- 路径点表（★★★2026-10-17 定案：**就在存档里**，不看地图） ----------
    #   明文 `1586 + 4×号` 起，共 `702` 项（号 0..701），每项 = `u16 x` + `u16 y`（**x 在前**）。
    #   ⇒ 与地图 `[Waypoints]` 的 `y×1000+x` 编码**无关**；数组起点 1586 `% 4 == 2`
    #     （**不是 4 字节对齐**）、坐标也不是任何 u32 打包值 —— 这正是此前
    #     「按 u32 对齐搜 / 按 y*1000+x 搜 / 按格子索引搜」全部 0 命中的原因。
    #   证据（2026-10-17 一次性锁死）：
    #     ① `五路径点档.SAV` 读出 98/99/431/578/654 五个点，与 `all01umd.map` 的
    #        `[Waypoints]`（80080/74084/91088/85089/86082）**5/5 逐条吻合**；
    #     ② 同一地图族 140+ 份存档全部读出 `98=(80,80) 99=(80,79)`，与地图原值一致；
    #     ③ 400×400 大任务档读出 115 个路径点、坐标 ≤310 ≤ 图幅 ⇒ 跨图幅成立；
    #     ④ 三份同族档（五路径点 / 刷光棱塔德国 / 刷光棱塔韩国）同一条读到**各自不同**的值
    #        （99 = (84,74)/(84,74)/(70,77)）⇒ 是活的存档数据，不是常量。
    #   ⇒ 这同时解释了「删掉地图照样能刷建筑」：落点完全由存档自带。
    WP_BASE = 1586              # 表首地址（明文偏移，1 号路径点 = 1590）
    WP_COUNT = 702             # 项数（号 0..701，与 YR 引擎上限一致）
    WP_COORD_MAX = 511         # 坐标合理性宽上界（400×400 图实测最大 310）
    # 表前 38 字节的固定结构：`02 00 00 00 | 00*4 | FF*4 | <ptr> | <u32> | FF*4 | <ptr>`
    WP_ANCHOR = b'\x02\x00\x00\x00\x00\x00\x00\x00\xff\xff\xff\xff'
    WP_ANCHOR_TO_ARRAY = 38    # 锚点 → 表首的距离

    def waypoint_base(self, pl=None):
        """定位路径点表基址（明文偏移）；定位不到返回 `None`（**绝不瞎猜**）。

        锚点 = 表前 38 字节的固定前缀 + 两个"堆指针形状"的 4 字节 + 中间 `FF FF FF FF`；
        再用「非零项坐标 ≤ WP_COORD_MAX」收口。`ScenarioClass` 头在明文最前面 ⇒ 取最小候选。
        """
        # ★★§273：**必须缓存**。这个函数要全档 find 一遍锚点 + 校验 702 项坐标，
        #   26MB 明文单次 ~20ms。而 GUI 每支小队显示"出现于 x (a,b)"都会调它一次
        #   ⇒ 288 支小队 = 124 次调用 = **2.4 秒**（`refresh_ai` 慢的真凶，不是读 AI 对象本身）。
        #   缓存键 = 明文长度：`set_waypoint()` 是原地改 4 字节（长度不变、定位结果也不变），
        #   增删字节必然改长度 ⇒ 自动作废；`invalidate_caches()` 也会清掉它。
        if pl is None:
            memo = getattr(self, '_wpbase_memo', None)
            if memo is not None and memo[0] == len(self.plain):
                return memo[1]
        pl = self.plain if pl is None else pl
        n = len(pl)
        i = 0
        found = []
        while True:
            j = pl.find(self.WP_ANCHOR, i)
            if j < 0:
                break
            i = j + 1
            base = j + self.WP_ANCHOR_TO_ARRAY
            if base + 4 * self.WP_COUNT > n:
                continue
            p1 = struct.unpack_from('<I', pl, j + 12)[0]
            if not (0x01000000 <= p1 <= 0x20000000):
                continue
            # ★2026-10-18 00:0x：这个槽在别的存档里是 `FF FF FF FF`（空指针），
            #   但在"引擎重存后"的 SAVE4823 里变成了一个真实指针（0x002A8F8C）
            #   ⇒ 只认 FFFFFFFF 会让整张路径点表定位失败（写路径点直接抛异常）。
            #   放宽成"空指针或像样的指针"，靠下面 702 项坐标范围校验收口。
            slot = struct.unpack_from('<I', pl, j + 20)[0]
            if not (slot == 0xFFFFFFFF or 0x00010000 <= slot <= 0x9FFFFFFF):
                continue
            p2 = struct.unpack_from('<I', pl, j + 24)[0]
            if not (0x01000000 <= p2 <= 0x20000000):
                continue
            ok = True
            for k in range(self.WP_COUNT):
                x, y = struct.unpack_from('<HH', pl, base + 4 * k)
                if (x or y) and (x > self.WP_COORD_MAX or y > self.WP_COORD_MAX):
                    ok = False
                    break
            if ok:
                found.append(base)
        base = min(found) if found else None
        if pl is self.plain:
            self._wpbase_memo = (n, base)
        return base

    def waypoint_offset(self, n):
        """第 n 号路径点在明文里的偏移（定位失败 / 号越界 → None）。"""
        base = self.waypoint_base()
        n = int(n)
        if base is None or not (0 <= n < self.WP_COUNT):
            return None
        return base + 4 * n

    def waypoints_save(self):
        """**存档本体**里的路径点表 → {号: (x, y)}（权威来源；定位不到返回 {}）。"""
        base = self.waypoint_base()
        if base is None:
            return {}
        out = {}
        for k in range(self.WP_COUNT):
            x, y = struct.unpack_from('<HH', self.plain, base + 4 * k)
            if x or y:
                out[k] = (x, y)
        return out

    def waypoint_cell(self, n):
        """第 n 号路径点的格子 (x, y)；没定义 → None。"""
        off = self.waypoint_offset(n)
        if off is None:
            return None
        x, y = struct.unpack_from('<HH', self.plain, off)
        return (x, y) if (x or y) else None

    def set_waypoint(self, n, x, y):
        """把第 n 号路径点写到格子 (x, y)。**零增长**（原地改 4 字节，明文长度不变）。

        `x is None or y is None or x < 0 or y < 0` ⇒ 删除该路径点（写 (0, 0)）。
        写完立即回读校验，返回最终 (x, y)。
        """
        off = self.waypoint_offset(n)
        if off is None:
            raise RuntimeError('定位不到存档里的路径点表（不做猜测）')
        if x is None or y is None or x < 0 or y < 0:
            x = y = 0
        x, y = int(x), int(y)
        if x > self.WP_COORD_MAX or y > self.WP_COORD_MAX:
            raise ValueError('坐标超出范围（0..%d）：(%d,%d)'
                             % (self.WP_COORD_MAX, x, y))
        struct.pack_into('<HH', self.plain, off, x, y)
        back = struct.unpack_from('<HH', self.plain, off)[0:2]
        if tuple(back) != (x, y):
            raise RuntimeError('写入后回读不一致：%r != %r' % (tuple(back), (x, y)))
        return (x, y)

    def waypoints_from_map(self):
        """挂载地图的 `[Waypoints]` → {号: (x, y)}（地图 INI 视角，编码 `y×1000+x`）。

        仅作对照用：**存档自足**，落点以 `waypoints_save()` 为准。
        """
        m = self._map
        try:
            return m.waypoints() if m is not None else {}
        except Exception:
            return {}

    def waypoints(self):
        """路径点 {号: (x, y)} —— **优先存档本体**，定位不到才退回挂载地图。"""
        base = self.waypoint_base()
        if base is not None:
            return self.waypoints_save()
        return self.waypoints_from_map()

    def waypoint_of_cell(self, x, y):
        """该格子对应的路径点编号（没有 → None）。存档优先，地图兜底。"""
        for n, (wx, wy) in self.waypoints_save().items():
            if (wx, wy) == (x, y):
                return n
        m = self._map
        try:
            return m.waypoint_of_cell(x, y) if m is not None else None
        except Exception:
            return None

    # ---------------- 地图关联记忆（★2026-10-20） ----------------
    #   玩家："我打开桌面的档不高亮、放 E:\YURI 就高亮" —— 根因是**地图匹配是启发式**：
    #   换个目录，`find_maps` 找到的图集变了、重叠度就不够了。所以把"这份档配过哪张图"
    #   记下来（按 存档名 + 触发ID集合 指纹），下次**哪怕换目录**也优先用回它。
    MAP_ASSOC_FILE = os.path.join(os.path.expanduser('~'), '.ra2_save_editor', 'map_assoc.json')

    @staticmethod
    def _assoc_key(path, ids):
        import hashlib
        h = hashlib.md5('|'.join(sorted(ids)).encode('utf-8')).hexdigest()[:10]
        return '%s|%d|%s' % (os.path.basename(str(path)).lower(), len(ids), h)

    def _assoc_load(self):
        try:
            with open(self.MAP_ASSOC_FILE, encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _assoc_get(self, ids):
        p = self._assoc_load().get(self._assoc_key(self.path, ids))
        return p if p and os.path.isfile(p) else None

    def _assoc_put(self, ids, map_path):
        try:
            d = self._assoc_load()
            d[self._assoc_key(self.path, ids)] = os.path.abspath(map_path)
            os.makedirs(os.path.dirname(self.MAP_ASSOC_FILE), exist_ok=True)
            with open(self.MAP_ASSOC_FILE, 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def auto_attach_map(self, extra_dirs=(), min_overlap=0.6):
        """按「触发ID 集合」在存档所在目录（及其子目录）里找地图并**自动挂载**。

        ★ 必须有足够重叠才算候选（默认 60%）：否则会把无关地图硬挂上（实测踩过 ——
        `D:\\233\\RA2YR` 下 7 个触发的档被挂到了只共 1 个 ID 的 all01umd.map）。
        候选可能仍有多个（同一地图的不同版本常共用触发ID）⇒ 优先选**数值完全对拍成功**的。
        ★2026-10-20：**先试"上次配过的那张图"**（存在关联记忆里），命中就直接挂 ——
          这样存档换个目录、或者原目录里那堆图被挪走，也不会突然"没地图"。
        """
        from map_ini import MapIni as _MI, find_maps as _fm
        here = os.path.dirname(os.path.abspath(self.path))
        dirs = [here] + [d for d in extra_dirs if d]
        ids = set(r.sid for r in self.records if r.kind == '触发')
        # ① 关联记忆优先
        _cached = self._assoc_get(ids)
        if _cached:
            try:
                _m = _MI(_cached)
                _mids = _m.trigger_ids()
                _den = max(len(ids), len(_mids)) or 1
                if _mids and len(ids & _mids) / float(_den) >= min_overlap:
                    self._map, self._mapm = _m, None
                    self._apply_map()
                    if self._mapm:
                        self._mapm['cached'] = True
                        return self._mapm['mode']
            except Exception:
                pass
            self._map = None
            self._mapm = None
        seen = set()
        cands = []
        for p in _fm(dirs)[:80]:
            key = os.path.normcase(os.path.abspath(p))
            if key in seen:                       # 同一文件别来两次（否则排序会打平）
                continue
            seen.add(key)
            try:
                if os.path.getsize(p) > 20 * 1024 * 1024:      # 超大文件跳过（非正常地图）
                    continue
            except OSError:
                continue
            try:
                m = _MI(p)
            except Exception:
                continue
            mids = m.trigger_ids()
            if not mids:
                continue
            # 重叠度按**较大一侧**算（否则小地图会靠"占满自己"蒙混过关 ——
            # 实测：7 个触发的存档曾被只含 1 个触发的杂图判成 100% 重叠）
            denom = max(len(ids), len(mids)) or 1
            ov = len(ids & mids) / float(denom)
            if ov < min_overlap:
                continue
            cands.append((0 if mids == ids else 1, -ov, p, m))
        best = None
        for _, negov, p, m in sorted(cands, key=lambda x: (x[0], x[1])):
            self._map = m
            self._mapm = None
            self._apply_map()
            mm = self._mapm
            rank = (mm['mode'] != 'exact', mm['mode'] != 'structural',
                    negov, mm['ev_mismatch'] + mm['ac_mismatch'])
            if best is None or rank < best[0]:
                best = (rank, p, m, mm)
            if mm['mode'] == 'exact' and negov == -1.0:
                break
        if best is None:
            self._map = None
            self._mapm = None
            return 'no_map'
        self._map, self._mapm = best[2], best[3]
        self._apply_map()
        try:                                  # ★记住"这份档配这张图"，换目录也认
            self._assoc_put(ids, best[1])
        except Exception:
            pass
        # ★ 用地图的权威 ID 集合**修正记录分类** —— 大型自制任务的档里
        #   「触发定义」与「触发实例」是两个独立数组，定义数组的 +120 处不是
        #   01 01 01 01，启发式会把触发判成「未识别结构」（实测 SAVE5C7B：
        #   86 条触发全部中招）。地图 [Triggers]/[Tags] 的 ID 是权威判据。
        m = self._map
        tids = m.trigger_ids()
        tagids = set(m.tag_trigger_map().keys())
        changed = 0
        for r in self.records:
            old = r.kind
            if r.sid in tids:
                r.kind = '触发'
            elif r.sid in tagids:
                r.kind = '标签'
            if r.kind != old:
                changed += 1
        if changed:
            self._assign_trigger_flags(self.records)
            self._mapm['reclass'] = changed
        return self._mapm['mode']

    # ---------------- 重复类型（★2026-10-20 定位 / 2026-10-21 语义纠错） ----------------
    # ⚠ 历史错误：以前把**触发**行 `[Triggers]` 的第 4 字段当"重复类型"。那是**禁用标志**！
    #   ModEnc 权威格式：`ID=HOUSE, LINKED_TRIGGER, NAME, DISABLED, EASY, NORMAL, HARD, TRANSFERABLE`
    #   ⇒ 大档 SAVE48DB 里"地图里一条型2都没有"、`+123` 完美分离两组，就是因为读的是禁用位。
    # ✔ 正确来源（ModEnc + 玩家 2026-10-20 用 SAVE18BE 三条 0/1/2 定案）：**标签行**
    #   `[Tags] TAGID=PERSISTENCE, NAME, TRIGGERID`
    #   第 0 字段 = 持久性 = "重复类型"。ModEnc 原文（[Tags] 页 PERSISTENCE 表）：
    #     0 = Volatile        —— 触发瞬间标签即销毁；挂多个对象时**任一**被触发就够
    #     1 = Semi-persistent —— 维护挂载对象计数，**全部**对象都触发过才真正触发，然后销毁
    #     2 = Persistent      —— 标签永不销毁，条件每次满足都再触发一次
    #   ★★2026-10-21 玩家纠错（§156）：这里的"任一/全部"说的是**标签挂在多个对象上**时
    #     哪个对象的事件算数；**同一触发里的多个事件永远是 AND（少一个都不执行）**，
    #     与持久性取值无关。别再把它读成"多条件是任一满足还是全部满足"。
    #   ModEnc 原话："Whether and when a tag is removed after being triggered is controlled
    #   solely by the PERSISTENCE parameter of the tag entry. Final Sun and Final Alert 2
    #   write the persistence value to the tag, not to the trigger."
    #   ★★2026-10-21/22 玩家实测（§157–§161）：**持久性 1 = 半持久，盯的是"挂载列表清空"**。
    #     引擎只在**一个**地方记账：`CellTag` 记录的 `+0x40`（= 标签还挂着几个对象）。
    #     每有一个挂载对象**离场**就 -1，减到 **0** 才执行一次，随后标签被销毁。
    #     ★★「-1 会发生」不等于「-1 与事件无关」（§162 更正 §161）：
    #       卖 3 栋也执行，原以为证明"计数不看事件"，但 **FA2 地编说明写明事件 48
    #       「被任何事物摧毁」本身就包含**摧毁 / 占领 / 心控 / **变卖**"（事件 7 才只管摧毁）。
    #       ⇒ 卖 3 栋可能是"变卖满足了事件 48"、"也可能是"离场就 -1"，两说都成立，
    #         §161 那句"与事件类型无关"已撤回。
    #       仍成立的部分：卖与炸在存档层面等价（卖 1 与炸 1 都到 count=2 的状态）；
    #         挂 1 栋即执行；计数只此一处。
    #       判别实验（一次只改事件类型）：同样"挂 3 栋 + 全部变卖"，
    #         用 **48**（含变卖）已实测执行；用 **7**（不含变卖）若也执行 ⇒ 计数唯一驱动，
    #         若不执行 ⇒ 必须"事件对该对象满足"才 -1。见 §162.4。
    #     证据（§160 四档，同一张图连存档）：0/1/2 栋被摧毁 ⇒ 该计数 **3/2/1** 严格递减、
    #       与建筑列表里存活的 GAPOWR 数一一相等，触发器一直不执行；第 3 栋一炸 ⇒ 计数归零、
    #       标签记录 + CellTag 节点**双双消失**、行为执行。而 Trigger 定义/实例/事件对象在
    #       "count=1 未执行"与"count=0 已执行"两档之间**逐 u32 完全一致** ⇒ 没有第二个计数器。
    #     证据（§161 三档）：`卖掉一栋` count=2 未执行、`摧毁剩余两栋后结果执行`(卖1+炸2) 已执行、
    #       `卖掉三栋后结果执行`(全程没有摧毁) 已执行、`只挂一栋 摧毁后执行` 已执行
    #       ⇒ **卖也照减**（但"为什么减"见上面的 §162 更正）。
    #       FA2 地编对三个摧毁类事件的差别（`EVENT_NOTES[7/29/48]`）：
    #         7 = 只算摧毁（碎片伤害、占领、变卖、心控**都不算**）；
    #         29 = 摧毁/占领/心控/变卖都算，**不含"渗透后消失"**；
    #         48 = 摧毁/占领/心控/变卖都算，**可用于有子机的单位**。
    #       · 正当用法（玩家原话）：**摧毁事件挂 N 个建筑/单位，全部被摧毁后才执行结果**；
    #       · 坑 1 **挂 0 个对象**（地图级触发）⇒ 没有对象可"-1"，永远到不了 0 ⇒ 永不触发。
    #               实测样本：`01000470` = 3×事件 36（三个局部变量）+ 行为 1（胜利），
    #               `attached = 0`；持久性 0 正常，改 1 就永远不执行。
    #       · 坑 2（引擎 BUG）：**进入事件（1）挂多个对象 + 持久 1 ⇒ 永不执行** ——
    #               进入不会把对象从标签上摘下来，计数恒等于原值、永远降不到 0（§159 实测
    #               三栋全进入后 count 仍是 3）。所以作者们改用**局部变量**：
    #               每栋各一条「持久 0 · 事件 1 → 行为 56 设置局部变量 N」，
    #               再加一条「持久 0 · N×事件 36 → 结果行为」的汇总触发。
    #   ⚠ 这一层是**存档级硬证据 + 玩家实测**，仍未做引擎反汇编；"离场即 -1"的入口在哪未证。
    # ★存档自足：**标签记录 +120 (u32) 就是这个持久性**（SAVE48DB 82/82、SAVE18BE 83/83 对拍）。
    REPEAT_TEXT = {'0': '一次性·任一挂载', '1': '一次性·挂载清空', '2': '可重复'}
    LOCAL_VAR_EVENTS = (36, 37)

    def local_var_events(self, sid):
        """触发里含哪些"局部变量类"事件 → [类型号...]（空 = 一条都没有）。"""
        try:
            return [e['type'] for e in self.trigger_event_list(sid)
                    if e['type'] in self.LOCAL_VAR_EVENTS]
        except Exception:
            return []

    def trigger_attach_count(self, sid):
        """触发挂接的对象数（= 它那条 CellTag 上站着的建筑数）；读不到算 0。"""
        try:
            return int(self.trigger_detail(sid).get('attached') or 0)
        except Exception:
            return 0

    # ★★★ 2026-10-22 §163：**计数会漏账** —— CellTag `+0x40` 不一定等于"实际还活着几个"。
    #   四档实测（持久 1 + 事件 13 流逝时间 + 事件 48 摧毁 + 挂 3 栋电厂）：
    #     · 先等满 30 秒再炸 3 栋 ⇒ 计数 3 → 0、执行（正常）
    #     · **先炸完 3 栋再等 30 秒 ⇒ 计数**仍是 3**、不执行**（3 次离场一次都没记账）
    #     · 同一条触发只留事件 48 时，炸 0/1/2 栋 ⇒ 计数 3/2/1（每次都记）⇒ 说明"记账"被
    #       **同一触发里其它（更晚满足的）事件**卡住了，不是"离场就一定 -1"。
    #   ⇒ 判据：`count 字段 > 实际存活挂载建筑数` = 漏账 ⇒ 这条持久 1 触发已经废了。
    #   ★★§164（同日晚，两档补刀）：**−1 挂在"摧毁类事件被满足"的回调上**，不是挂在"对象离场"上。
    #     只换事件类型、只做变卖（72 小时内两两对照）：
    #       事件 48（认变卖）：卖 1 栋 ⇒ 计数 2；卖 3 栋 ⇒ 归零、执行
    #       事件 7 （不认变卖）：卖 1 栋 ⇒ 计数**仍 3**；卖 3 栋 ⇒ 计数**仍 3**、不执行
    #     ⇒ §162 的"实验 A2"结论 = **必须事件对该对象满足才 −1**。
    #     于是所有"持久 1 永不执行"的坑是同一句话的三种情形（都缺"事件满足 + 对象离场"同时成立）：
    #       ① 事件不认这种离场方式（7 + 变卖；7 也不认碎片伤害/占领/心控）
    #       ② 事件没被接管（多事件、链头未满足，§163）
    #       ③ 压根没有离场（进入事件 1，§159）
    #   ⚠ 只认**建筑**（attach_overview 按格中心坐标反查）；若标签挂的是单位，live 会算成 0，
    #     此时不要急着报漏账（调用方按"是否挂建筑"自行取舍）。
    def attach_live_count(self, sid):
        """实际存活、且标签字段指向这条触发的**建筑**数（几何扫描，不看 count 字段）。

        与 `trigger_attach_count()` 的差 = §163 的"漏账"量。读不到返回 None。
        """
        n = len(self.plain)
        if getattr(self, '_live_len', None) != n:
            self._live_len, self._live_map = n, None
        if getattr(self, '_live_map', None) is None:
            agg = {}
            try:
                for ov in self.attach_overview() or []:
                    tid = ov.get('trigger_id')
                    if not tid:
                        continue
                    agg[tid] = agg.get(tid, 0) + len(ov.get('buildings') or [])
            except Exception:
                return None
            self._live_map = agg
        return self._live_map.get(sid)

    # ★★★ 2026-10-22 §159/§160：运行期状态的存档指纹（六档实测，见主文档）
    #   ① 「已执行」唯一的可靠指纹 = **CellTag 节点消失**（`celltag_nodes()` 里找不到它）。
    #      · 持久 0（局部变量法 A/B/C/D）：4 个节点 → 0 个
    #      · 持久 1（摧毁法，3 栋全炸）：1 个节点 → 0 个
    #   ② 标签记录（TagClass）在执行后**可能整条消失**（摧毁法档 tag_records 为空），
    #      也可能残留（局部变量法档 A/B/C 还在、只有地图级的 D 消失）⇒ **不能**用
    #      "标签记录在不在"反推执行与否；只作附加信息。
    #      （§159 曾写"Tag 记录还在、名字还在"—— 那只对 A/B/C 成立，§160 已更正。）
    #   ③ 事件对象 `+0x68` 是"触发者"槽：局部变量法 A/B/C 由 0 → 指针；但摧毁法四档
    #      **全程为 0**（含已执行档）⇒ 它不普适，别拿它当"跑没跑过"的判据。
    #   ④ CellTag `+0x35`：未执行档里是名字残留 ASCII（108/255），**不是计数器**。
    #   ★ 真正的计数器只有一个：**CellTag 记录 `+0x40` = 还挂着几个对象**（§160 实测
    #     摧毁 0/1/2/3 栋 ⇒ 3/2/1/(节点消失)）。持久 1 就是等它减到 0。
    EVENT_FIRED_PTR_OFF = 0x68
    TAG_RAW_BYTE_OFF = 0x35
    CELLTAG_COUNT_OFF = 0x40

    def semi_persist_remaining(self, sid):
        """持久 1 的「还差几个对象」→ 剩余挂载数；节点已消失返回 0；读不到 None。

        引擎只记这一处（CellTag `+0x40`）：每有一个挂载对象**离场**（被摧毁 / 被卖掉 / 被移除）
        就 -1，减到 0 才执行。所以「持久 1 + 摧毁类」能正常工作，「持久 1 + 进入事件」永远
        降不到 0（§159 BUG）；卖与炸在存档里表现完全一样（§161 三档实测）。
        """
        try:
            o = self.trigger_objects(sid)
        except Exception:
            return None
        c = o.get('cell')
        if c is None:
            return 0 if o.get('tag_obj') is not None else None
        if c + self.CELLTAG_COUNT_OFF + 4 > len(self.plain):
            return None
        return int.from_bytes(
            self.plain[c + self.CELLTAG_COUNT_OFF:c + self.CELLTAG_COUNT_OFF + 4], 'little')

    def trigger_runtime_state(self, sid):
        """这条触发在本档里的**运行期状态**（只读推断，§159/§160 实测）。

        → {'has_tag','has_node','attached','remain','executed','ev_fired','tag_byte',
           'live','leak','text'}
          · executed = CellTag 节点已消失（一次性标签被消费，无论持久 0 还是 1）
          · remain   = 还挂着几个对象（= 持久 1 的"还差几个"），节点消失后为 0
          · ev_fired = 事件 `+0x68` 有触发者指针（**只对部分事件类型写入**，摧毁法全程为 0）
          · tag_byte = CellTag `+0x35` 原始字节（语义未定，别当计数器）
          · live     = 实际存活且挂着这条触发的**建筑**数（几何扫描，§163）
          · leak     = remain − live > 0 ⇒ 有离场没被记账（§163：先摧毁、后满足其它事件）
        """
        st = {'has_tag': False, 'has_node': False, 'attached': 0, 'remain': None,
              'executed': False, 'ev_fired': False, 'tag_byte': 0,
              'live': None, 'leak': None, 'suppress': None, 'busy': None,
              'verdict': '', 'text': ''}
        try:
            o = self.trigger_objects(sid)
        except Exception:
            st['text'] = '读不到这条触发的对象'
            return st
        pl = self.plain
        st['has_tag'] = o['tag_obj'] is not None
        st['has_node'] = o['cell'] is not None
        try:
            st['attached'] = self.trigger_attach_count(sid)
        except Exception:
            pass
        # ★★2026-10-27（玩家："添加行为会卡，然后闪退"）：
        #   `remain` / `live` 都要跑 `attach_overview()`（对每个 CellTag 全档 find，
        #   本档 7.5 秒），而这两个数**只对"持久 1"有意义**（还差几个离场 / 漏账）。
        #   持久 0 和 2 的触发算了也白算 ⇒ 只在 repeat == '1' 时算，其余留 None
        #   （界面显示"—"）。这一改把编辑器每次刷新从 8 秒降到毫秒级。
        try:
            st['repeat'] = str((self.trigger_detail(sid) or {}).get('repeat') or '0')
        except Exception:
            st['repeat'] = '1'          # 读不到就按"需要算"处理（保守，不丢信息）
        if st['repeat'] == '1':
            try:
                st['remain'] = self.semi_persist_remaining(sid)
            except Exception:
                pass
            try:
                st['live'] = self.attach_live_count(sid)
            except Exception:
                pass
        if st['remain'] is not None and st['live'] is not None:
            st['leak'] = st['remain'] - st['live']
        if o['ev'] is not None and o['ev'] + 0x6C <= len(pl):
            st['ev_fired'] = bool(int.from_bytes(
                pl[o['ev'] + self.EVENT_FIRED_PTR_OFF:o['ev'] + self.EVENT_FIRED_PTR_OFF + 4],
                'little'))
        if o['cell'] is not None and o['cell'] + self.TAG_RAW_BYTE_OFF + 1 <= len(pl):
            st['tag_byte'] = int(pl[o['cell'] + self.TAG_RAW_BYTE_OFF])
        try:
            fl = self.celltag_flags(sid)
        except Exception:
            fl = None
        if fl:
            st['suppress'], st['busy'] = fl['suppress'], fl['busy']
        st['executed'] = bool(st['has_tag'] and not st['has_node'])
        # ★2026-10-22 §168：引擎真相（0x6E53A0 / 0x485250 / 0x485130）⇒ 状态收敛成五个之一
        if not st['has_tag']:
            st['verdict'] = 'no_tag'
        elif st['executed']:
            st['verdict'] = 'executed'
        elif st['suppress']:
            st['verdict'] = 'suppressed'
        elif st['leak'] and st['leak'] > 0:
            st['verdict'] = 'dead'
        elif st['remain'] == 1:
            st['verdict'] = 'ready'
        else:
            st['verdict'] = 'waiting'
        if not st['has_tag']:
            st['text'] = '本档找不到这条触发的标签（只有定义，或标签已被销毁且无残留）'
        elif st['executed']:
            st['text'] = ('已执行：CellTag 节点已消失（挂载数归零后执行、标签被销毁）')
        elif st['suppress']:
            st['text'] = ('⚠ 标签被**抑制**：CellTag 标志字节 +0x34 = %d（正常应为 0）⇒ '
                          '引擎在 0x6E53A0 一进门就 return，连计数都不动 ⇒ 这条触发在本档里'
                          '彻底失效。存档若有损坏，可用「清零抑制位」修。' % st['suppress'])
        elif st['leak'] and st['leak'] > 0:
            st['text'] = ('⚠ 计数漏账：CellTag 还写着 %d 个，实际存活挂载建筑只有 %d 个 ⇒ '
                          '有 %d 次离场**没被记账**（持久 1 只在"这条触发全部事件都已满足"的'
                          '窗口里才给离场 −1，窗口外的离场永久丢、不回补：'
                          '① 事件类型不认这种离场方式 —— 如事件 7 不认变卖；'
                          '② 同一触发里还有事件没满足 —— 时间没到 / 对方阵营建筑还在，§163/§165/§167）'
                          '⇒ 这条触发永远不会执行'
                          '（本判断只统计建筑；挂单位时请忽略此提示）'
                          % (st['remain'], st['live'], st['leak']))
        else:
            st['text'] = ('未执行：CellTag 节点还在，还挂 %d 个对象'
                          '（持久 1 要等它减到 0 才执行）' % st['attached'])
        return st

    # ★★★ 2026-10-22 §168：引擎侧反汇编（REA/Ghidra + capstone，细节见主文档 §168）把上面
    #   这些实测全部归到三支函数上：
    #     · SetTag(obj, cellTag)       @0x485250：旧标签 +0x2C--、obj+0x3C = 新标签、新标签 +0x2C++
    #     · RemoveTag(obj, tag)        @0x485130：obj+0x3C == tag ⇒ tag+0x2C--、清 obj+0x3C
    #     · CellTag::判事件并记账       @0x6E53A0：遍历 CellTag+0x28 的实例链，逐个跑
    #       「全部事件是否满足」@0x7264C0（一路 AND）；**只有满足**才在函数尾部走
    #       SetTag(obj, NULL) ⇒ 计数 −1；而 **计数本来就是 1** 的那一次才当场执行行为、
    #       并把 CellTag 登记进待处理表（随后被消费 ⇒ 节点从存档里消失）。
    #   ⇒ 「丢账」不是记账坏了，而是"事件没满足 ⇒ 连摘除都不做"，对象一消失这笔账就永久没了。
    #   ⇒ 能救回来的修法只有一条：**把 CellTag+0x40 改回"实际还活着几个"**（§168.4 推论 1）。
    #   ※ CellTag 记录头是 20 字节（[CLSID 16B][自身键 4B]）+ 对象体 56B ⇒ 共 76B；
    #     所以"记录 +0x40" 对应"对象 +0x2C"，两个数字都是同一个字段，别再当成两处。
    CELLTAG_SUPPRESS_OFF = 0x34   # 非 0 ⇒ 0x6E53A0 一进门就 return（静默失效，最难查）
    CELLTAG_BUSY_OFF = 0x35       # 重入保护，读档后应当回到 0

    def celltag_flags(self, sid):
        """CellTag 的两个标志字节 → {'suppress','busy'}；读不到返回 None。

        · suppress（记录 +0x48 / 对象 +0x34）非 0 ⇒ 引擎**一进门就 return**，
          这条触发在本档里彻底失效（连计数都不动）。正常档应为 0。
        · busy（记录 +0x49 / 对象 +0x35）是重入保护，正常档也应为 0；
          若留成 1，说明存档正好写在一帧中间（罕见，但会让触发卡住）。
        """
        try:
            o = self.trigger_objects(sid)
        except Exception:
            return None
        c = o.get('cell')
        if c is None or c + self.CELLTAG_BUSY_OFF + 1 > len(self.plain):
            return None
        return {'suppress': int(self.plain[c + self.CELLTAG_SUPPRESS_OFF]),
                'busy': int(self.plain[c + self.CELLTAG_BUSY_OFF])}

    def clear_celltag_flags(self, sid):
        """把 CellTag 的 `+0x34`（抑制）/`+0x35`（重入残留）清零 → {'old':(s,b),'new':(0,0)}。

        仅在存档损坏 / 崩溃前落盘的档上需要；正常档这两个字节本来就是 0。
        """
        fl = self.celltag_flags(sid)
        if fl is None:
            raise RuntimeError('这条触发在本档里没有 CellTag 节点')
        o = self.trigger_objects(sid)
        c = o['cell']
        old = (fl['suppress'], fl['busy'])
        if old != (0, 0):
            self.plain[c + self.CELLTAG_SUPPRESS_OFF] = 0
            self.plain[c + self.CELLTAG_BUSY_OFF] = 0
        return {'old': old, 'new': (0, 0)}

    def set_attach_count(self, sid, n):
        """原地改写 CellTag 的**剩余挂载数**（记录 `+0x40` / 对象 `+0x2C`）。

        → {'old','new','off'}。这是 §168 之后唯一能"救回丢账档"的写操作：
        引擎只认这个数，把它改成实际存活数后，剩下的对象每离场一次就 −1，
        减到 1 的那一次就会执行。
        ⚠ 只改 4 字节、不动明文长度 ⇒ 不需要重压。防御：当前值必须是 0..64 的合理数。
        """
        n = int(n)
        if not 0 <= n <= 64:
            raise ValueError('挂载数只能是 0..64')
        try:
            o = self.trigger_objects(sid)
        except Exception:
            raise RuntimeError('读不到触发 %s 的对象' % sid)
        c = o.get('cell')
        if c is None:
            raise RuntimeError('这条触发在本档里没有 CellTag 节点（可能已执行 / 只有定义）')
        p = c + self.CELLTAG_COUNT_OFF
        if p + 4 > len(self.plain):
            raise RuntimeError('CellTag 计数位置越界，已拒绝写入')
        old = int.from_bytes(self.plain[p:p + 4], 'little')
        if old > 64:
            raise RuntimeError('CellTag 计数当前值 = %d（不合理）⇒ 定位可能出错，已拒绝写入'
                               % old)
        self.plain[p:p + 4] = struct.pack('<I', n)
        self._live_len = None
        return {'old': old, 'new': n, 'off': p}

    def repair_attach_leak(self, sid):
        """把"丢账"档的挂载数改回**实际存活建筑数**（§168.4 推论 1 的落地）。

        → {'ok','old','live','new','text'}
          · ok=False 且 live==0 ⇒ 不可救：挂载对象全没了，再也不会有"离场"来触发它。
            这种情况只能把持久性改成 0（条件满足即执行），或重新挂接对象。
          · ok=True ⇒ 计数已改成 live，剩下 live 次离场会依次 −1，最后一次执行。
        """
        st = self.trigger_runtime_state(sid)
        remain, live = st.get('remain'), st.get('live')
        if remain is None:
            return {'ok': False, 'old': None, 'live': live, 'new': None,
                    'text': '读不到这条触发的 CellTag 计数'}
        if live is None:
            return {'ok': False, 'old': remain, 'live': None, 'new': None,
                    'text': '统计不到存活挂载建筑（标签挂的可能是单位/其它对象），请手动填数'}
        if live <= 0:
            return {'ok': False, 'old': remain, 'live': 0, 'new': None,
                    'text': '挂载建筑已经全部离场 ⇒ **救不回来**（不会再有离场来 −1）。'
                            '请把持久性改成 0，或重新给这条触发挂接对象。'}
        if remain == live:
            return {'ok': True, 'old': remain, 'live': live, 'new': live,
                    'text': '计数本来就是 %d，与实际存活一致，无需修复' % live}
        r = self.set_attach_count(sid, live)
        return {'ok': True, 'old': r['old'], 'live': live, 'new': r['new'],
                'text': '已把挂载数从 %d 改成实际存活数 %d ⇒ 剩下 %d 次离场会依次 −1，'
                        '最后一次执行' % (r['old'], live, live)}

    def repeat_pitfall(self, sid, persistence=None, attached=None):
        """持久性选择的**坑**提示串（'' = 没问题）。GUI 写盘前弹确认用。

        ★§158 定案（持久性 1 = 计数型，见上面注释）：
          * 1 + 挂 0 个对象          ⇒ **永不触发**（没有对象可"-1"）
          * 1 + 进入事件(1) + ≥2 对象 ⇒ **已知 BUG，永不执行**（改为局部变量写法）
          * 1 + 摧毁类(7/29/48) + ≥1 对象 ⇒ **正常**（逐个离场 −1，全部离场后执行一次）→ 不报警
          * 1 + 挂 ≥1 对象 + **多条事件** ⇒ 危险（§163）：离场只有在"其它事件已满足"时
            才被记账，先炸后等 ⇒ 计数不减 ⇒ 永不执行
          * 2                          ⇒ 条件满足后会**反复执行**同一条行为
        ⚠ 事件 7 与 29/48 的差别（§162）：7 **不算变卖/占领/心控**；若你打算靠"卖"让对象
          离场，用 7 时事件可能一直不满足 —— 改用 48（29 亦可，但 29 不含"渗透后消失"）。
        """
        cur = persistence
        if cur is None:
            try:
                cur = int(self.trigger_repeat_type(sid) or 0)
            except Exception:
                cur = 0
        cur = int(cur)
        if cur == 0:
            return ''
        try:
            types = [e['type'] for e in self.trigger_event_list(sid)]
        except Exception:
            types = []
        if attached is None:
            attached = self.trigger_attach_count(sid)
        attached = int(attached or 0)
        evs = [t for t in types if t in self.LOCAL_VAR_EVENTS]

        if cur == 1:
            if 1 in types and attached >= 2:
                return ('⚠ 已知 BUG：持久性 1 + **进入事件（1）** 挂到 %d 个对象上 ⇒ '
                        '条件全满足也**永不执行**。\n'
                        '机制（§160/§161 实测）：持久 1 等的是「CellTag 的**剩余挂载数减到 0**」，'
                        '而只有挂载对象被摘除（离场）才会让它 -1；进入不会摘对象，'
                        '计数恒为 %d ⇒ 永远到不了 0。\n'
                        '社区通行写法：每栋各挂一条「持久 0 · 事件 1 进入 → 行为 56 设置局部变量 N」，'
                        '再配一条**持久 0** 的汇总触发（N × 事件 36 局部变量被设置）去执行结果。'
                        % (attached, attached))
            if attached == 0:
                return ('持久性 1 等的是「CellTag 剩余挂载数减到 0」'
                        '（§160/§161 实测：炸掉或卖掉 0/1/2 栋 ⇒ 计数 3/2/1，最后一个离场才归零执行）。\n'
                        '这条触发挂接对象数 = **0** ⇒ 没有对象可以让它 -1 ⇒ **永不执行**'
                        '（玩家实测：三个局部变量全满足仍不执行）。请留 0。')
            if 1 in types:
                return ('持久性 1 + 进入事件（1）只挂了 1 个对象：计数只有 1、又不会被进入摘掉，'
                        '**很可能也永不执行**（挂两个以上是已确认的 BUG）。建议改持久 0。'
                        '当前剩余挂载数 = %d。' % attached)
            if evs and attached >= 0:
                return ('持久性 1 + 局部变量事件（类型 %s）⇒ **永不触发**（§157 玩家实测，'
                        '局部变量全满足也不执行）。持久性请留 0。'
                        % '/'.join(sorted({str(e) for e in evs})))
            # ★§166：8「任何事件」恒真 ⇒ 不会挡记账（玩家实测：48+8 挂 3 栋，开局炸完 3 栋照样执行）
            _blocking = [t for t in types if t != 8]
            if len(_blocking) > 1 and attached >= 1:
                return ('⚠ 持久性 1 + **多条事件** + 挂 %d 个对象：只有这条触发的**全部事件都满足**'
                        '之后，挂载对象的摧毁/变卖才被记账；在那之前的离场**永久丢账**'
                        '（§163/§165 实测：「流逝时间 30/60 秒」+「被摧毁」挂 3 栋，先炸后等 ⇒ '
                        'CellTag 计数一直停在 3、永不执行；**把两条事件换个顺序也没用**）。\n'
                        '稳妥写法：把"等待"放到**另一条**触发（或局部变量）里去满足，'
                        '本条只留摧毁类事件；或让第二条事件**开局即满足**（如「8 任何事件」）。'
                        % attached)
            return ''

        if cur == 2 and (len(types) > 1 or evs):
            desc = ('含 %s局部变量事件（类型 %s）'
                    % (('%d 个' % len(evs)) if len(evs) > 1 else '',
                       '/'.join(sorted({str(e) for e in evs})))) if evs else '多条事件'
            return ('持久性 2（可重复）会让它在条件满足后**反复执行**同一条行为'
                    '（本触发%s，共 %d 个事件）。只想执行一次请留 0。' % (desc, len(types)))
        return ''

    def tag_records(self):
        """存档里的标签记录 [(sid, name, off)] —— 按标签类 GUID 尾识别。

        ★2026-10-20：以前只靠 `_scan_records` 的分类，而分类里的 +120..123 四字节判据
          会把"持久性=1/2"的标签误判成'其它'（SAVE18BE 里 B1/C1 两条就中招）。
          这里改用**类 GUID 尾**匹配（窗 +124..+168，容忍 ±4 漂移），
          实测 SAVE48DB 精确命中 82 条（= 地图 82 个标签，零误报、不混入触发记录）。
        """
        if self._tagrecs is None:
            lo, hi = TAG_PERSIST_WIN
            out = []
            for r in self.records:
                w = self.plain[r.off + lo:r.off + hi]
                if TAG_GUID_TAIL in w:
                    out.append((r.sid, r.name, r.off))
            self._tagrecs = out
        return self._tagrecs

    def tag_persist(self, tag_sid):
        """标签的持久性（= 重复类型）'0'/'1'/'2'；读不到返回 None。"""
        for sid, _nm, off in self.tag_records():
            if sid == tag_sid:
                v = int.from_bytes(
                    self.plain[off + TAG_PERSIST_OFF:off + TAG_PERSIST_OFF + 4],
                    'little')
                return str(v) if 0 <= v <= 3 else None
        return None

    # ---------- 改已有触发的"触发类型"（★2026-10-20 玩家要求） ----------
    def tag_obj_off(self, rec_off):
        """标签**记录帧**偏移 → 标签**对象帧**偏移（差 56B）。

        ★必须做**真校验**，不能拿 `obj+120` 和 `rec+64` 比 —— 那俩本来就是同一段字节
          （曾因此写出一个空转的校验）。真正的锚点是**标签类 GUID**：
          记录帧里 `TAG_GUID_TAIL` 落在 +132..+144，换算到对象帧就是 **+184..+200**。
          实测：SAVE48DB 82/82、AB 样本 2/2、trig2 1/1 全命中；
          非标签记录 0 误报（三份档各验过）。
        """
        obj = rec_off - TAG_REC_DELTA
        if obj < 0 or obj + 204 > len(self.plain):
            return None
        if TAG_GUID_TAIL not in self.plain[obj + 184:obj + 204]:
            return None
        return obj

    # ================= §192 标签（TagTypeClass）全字段 =================
    def tag_type_records(self):
        """逐个 164B 标签对象体读全字段（§192 字段表）。

        返回 [{rec, obj, key, sid, name, index, persist, trig_key, ok_vt}]
          rec      记录起点（= 54F6E433 处）
          obj      对象体起点（= rec + 20）
          key      记录自键（rec + 16）
          sid      +0x24 的 8 字节 ID 串（地图 `[Tags]` 的键名）
          name     +0x64 起 52B（GBK）
          index    +0x98 集群内序号
          persist  +0x9C 持久性/重复类型
          trig_key +0xA0 → 触发实例的键
          ok_vt    4 个 vtable 是否与引擎一致（不一致 ⇒ 记录可能被截断/错位）
        """
        pl = self.plain
        V = TAG_TYPE_VTABLES
        out = []
        i = pl.find(TAG_TYPE_GUID)
        while i >= 0:
            if i + TAG_TYPE_REC_SIZE <= len(pl):
                obj = i + TAG_TYPE_OBJ_DELTA
                u = lambda o: struct.unpack_from('<I', pl, obj + o)[0]
                sid = pl[obj + TAG_TYPE_ID_OFF:obj + TAG_TYPE_ID_OFF + 8]
                nm_raw = pl[obj + TAG_TYPE_NAME_OFF:obj + TAG_TYPE_NAME_OFF + TAG_TYPE_NAME_LEN]
                tk = u(TAG_TYPE_TRIGGER_OFF)
                out.append({
                    'rec': i,
                    'obj': obj,
                    'key': struct.unpack_from('<I', pl, i + 16)[0],
                    'sid': sid.split(b'\x00')[0].decode('latin1'),
                    'name': _decode_txt(nm_raw.split(b'\x00')[0]),
                    'index': u(TAG_TYPE_INDEX_OFF),
                    'persist': u(TAG_TYPE_PERSIST_OFF),
                    'trig_key': tk,
                    'trig_sid': self._sid_of_key(tk),
                    'ok_vt': (u(0), u(4), u(8), u(0xC)) == V,
                })
            i = pl.find(TAG_TYPE_GUID, i + 1)
        return out

    def tag_fields(self, tag_sid):
        """按标签 ID（如 '01000001'）取字段 dict（见 `tag_type_records`）；没有返回 None。"""
        want = str(tag_sid).strip().upper()
        for t in self.tag_type_records():
            if t['sid'].upper() == want:
                t = dict(t)
                t['trig_sid'] = self._sid_of_key(t['trig_key'])
                return t
        return None

    def _sid_of_key(self, key):
        """任一类记录的**自键** → 它的 ID 串（找不到返回 None）。首次调用建一次全档索引。"""
        if not key:
            return None
        m = getattr(self, '_key2sid', None)
        if m is None:
            m = {}
            pl = self.plain
            for guid in (TRIG_INSTANCE_GUID, TRIG_TAGOBJ_GUID, TRIG_NODE_GUID, TRIG_CELLGUID):
                i = pl.find(guid)
                while i >= 0:
                    if i + 24 <= len(pl):
                        k = struct.unpack_from('<I', pl, i + 16)[0]
                        sid = pl[i + TAG_TYPE_OBJ_DELTA + TAG_TYPE_ID_OFF:
                                 i + TAG_TYPE_OBJ_DELTA + TAG_TYPE_ID_OFF + 8]
                        sid = sid.split(b'\x00')[0].decode('latin1')
                        if sid and k not in m:
                            m[k] = sid
                    i = pl.find(guid, i + 1)
            self._key2sid = m
        return m.get(key)

    def set_tag_name(self, tag_sid, name):
        """改标签**名字**（对象体 +0x64，52B 定长、GBK、原地覆盖、长度不变）。

        ★只动内存，不动明文长度 ⇒ 不需要重压。返 {'old','new','len'}。
        """
        f = self.tag_fields(tag_sid)
        if f is None:
            raise RuntimeError('没找到标签 %s' % tag_sid)
        old = f['name']
        try:
            b = str(name).encode('gbk')
        except Exception:
            b = str(name).encode('utf-8', 'ignore')
        span = TAG_TYPE_NAME_LEN
        if len(b) > span - 1:
            raise RuntimeError('标签名太长：GBK %d 字节 > %d（会盖掉后面的序号字段）'
                               % (len(b), span - 1))
        obj = f['obj']
        self.plain[obj + TAG_TYPE_NAME_OFF:obj + TAG_TYPE_NAME_OFF + span] = \
            b + b'\x00' * (span - len(b))
        return {'old': old, 'new': str(name), 'len': len(b)}

    def scrub_tag_stale(self, rec_off):
        """把标签记录里的**垃圾槽**（TAG_TYPE_STALE）清零 —— 克隆模板后必须调。

        实测（SAVE4CA2，82 个标签）：+0x14/+0x20/+0x2C..+0x5F 在 FA2 产物里恒为 0，
        但从存档模板克隆会继承旧住户的 **旧键 0x13xxxxxx / 裸指针 0x10xxxxxx**
        （28~38 个标签碰到过）。§188 那类"引擎把触发跳过"的坑，很可能就来自这些脏槽。
        """
        obj = rec_off + TAG_TYPE_OBJ_DELTA
        n = 0
        for lo, hi in TAG_TYPE_STALE:
            for o in range(lo, hi, 4):
                if any(self.plain[obj + o:obj + o + 4]):
                    n += 1
                self.plain[obj + o:obj + o + 4] = b'\x00' * 4
        return n

    def tag_health(self):
        """标签集群体检 → dict{count, bad_vt, idx_bad, dup_sid, persist_dist}。"""
        ts = self.tag_type_records()
        idx = [t['index'] for t in ts]
        sids = [t['sid'] for t in ts]
        dist = {}
        for t in ts:
            dist[t['persist']] = dist.get(t['persist'], 0) + 1
        return {
            'count': len(ts),
            'bad_vt': [t['sid'] for t in ts if not t['ok_vt']],
            'idx_bad': [ts[k]['sid'] for k, v in enumerate(idx) if v != k],
            'dup_sid': sorted({s for s in sids if sids.count(s) > 1}),
            'persist_dist': dist,
        }

    def set_trigger_repeat_type(self, sid, persistence):
        """改触发的**触发类型（= 标签持久性）**：原地写它挂的每个标签对象 `+0xB0`。

        ★2026-10-20：这是**原地改写 4 字节**（不增删对象、不动明文长度）⇒ 不需要重压，
          安全性很高。返回 {'changed','old','new','tags','total'}。
        ⚠ 取值语义（§156 纠错后）：0 = 一次性·任一挂载（触发即销毁标签）
          1 = 一次性·挂载清空（挂载对象全触发过才触发一次）2 = 可重复。
          **与"同一触发里多个事件是任一还是全部"无关** —— 多事件恒为 AND。
        ★★§157（玩家实测）：触发里若有**局部变量事件 36/37**，持久性必须留 0 ——
          写 1 ⇒ **永远不会触发**；写 2 ⇒ 条件满足后**反复执行**。
          返回值里多一个 `warn`（非空 = 该提醒用户），GUI 用它弹确认。
        """
        persistence = int(persistence)
        if persistence not in (0, 1, 2):
            raise ValueError('触发类型只能是 0/1/2（0 一次性·任一挂载 / '
                             '1 一次性·挂载清空 / 2 可重复）')
        tags = [t for t, g in self.trigger_tag_pairs() if g == sid]
        if not tags:
            raise RuntimeError('没找到触发 %s 对应的标签' % sid)
        tset = set(tags)
        old = None
        n = 0
        done = []
        for tag_sid, _nm, off in self.tag_records():
            if tag_sid not in tset:
                continue
            obj = self.tag_obj_off(off)
            if obj is None:
                continue
            v = int.from_bytes(self.plain[obj + 0xB0:obj + 0xB4], 'little')
            # ★最后一道闸：这个字节本来就只能是 0/1/2/3。若不是 ⇒ 帧差/锚点判断出了问题，
            #   **拒绝写入**（宁可报错也别把 4 字节乱写到别人的字段上）。
            if v not in (0, 1, 2, 3):
                raise RuntimeError(
                    '标签 %s 的持久性字节当前值 = %d（不是 0/1/2/3）⇒ 定位可能出错，已拒绝写入'
                    % (tag_sid, v))
            if old is None:
                old = v
            if v != persistence:
                self.plain[obj + 0xB0:obj + 0xB4] = struct.pack('<I', persistence)
                n += 1
            done.append(tag_sid)
        if not done:
            raise RuntimeError('标签 %s 的对象帧校验失败，已拒绝写入（避免写错位置）'
                               % ','.join(sorted(tset)))
        # ★§157：含局部变量事件（36/37）的触发，写 1 等于让它永不触发、写 2 会反复执行。
        #   这里**不拦**（有的地图作者故意要 2 的重复效果），但把警告串带回去，GUI 弹确认。
        return {'changed': n, 'old': old, 'new': persistence,
                'tags': done, 'total': len(done),
                'warn': self.repeat_pitfall(sid, persistence)}

    def trigger_tag_pairs(self):
        """[(tag_sid, trig_sid)] —— 标签 → 触发。

        优先用**已挂地图**的 `[Tags]` 末字段（权威）；没挂地图则用命名规律：
        FinalSun 把标签命名为「触发名 + " N"」（如 `A2号信标铁幕a` → `A2号信标铁幕a 1`）。
        """
        m = getattr(self, '_map', None)
        if m:
            pairs = [(tid, trig) for tid, trig in m.tag_trigger_map().items() if trig]
            # ★2026-10-20：地图里没有的标签（= 本工具新建的触发，地图还不知道它）
            #   必须用命名规律兜底，否则新触发的"触发类型"永远读不出来。
            known = {t for t, _g in pairs}
            pairs += [(t, g) for t, g in self._tag_pairs_by_name() if t not in known]
            return pairs
        return self._tag_pairs_by_name()

    def _tag_pairs_by_name(self):
        """按命名规律把标签对到触发：FinalSun 把标签命名为「触发名 + " N"」。
        ⚠ 不能从右往左把所有数字段都剥掉（会剥出 `A3号信标铁幕a` 这种不存在的名字），
          改用**最长触发名前缀 + 剩余部分是纯数字**来匹配。"""
        trig_by_name = {}
        for r in self.records:
            if r.kind == '触发' and r.name:
                trig_by_name.setdefault(r.name, []).append(r.sid)
        names = sorted(trig_by_name, key=len, reverse=True)   # 长的优先 ⇒ 最具体的触发名
        out = []
        for sid, nm, _off in self.tag_records():
            if not nm:
                continue
            for tn in names:
                if nm.startswith(tn):
                    rest = nm[len(tn):].strip()
                    if rest.isdigit():
                        # 同名触发无法用名字区分 ⇒ 都挂上（持久性取任一，实测同名同值）
                        for tsid in trig_by_name[tn]:
                            out.append((sid, tsid))
                        break
        return out

    def trigger_repeat_type(self, sid):
        """触发重复类型 '0'/'1'/'2'；都读不到 ⇒ None。

        ★优先级：**存档里的标签持久性**（存档自足） → 地图 `[Tags]` 第 0 字段兜底。
          注意：一个触发可挂多个标签；只有任一标签是持久(2) 才算"会重复"。
        """
        if not sid:
            return None
        vals = []
        for tag_sid, trig_sid in self.trigger_tag_pairs():
            if trig_sid == sid:
                p = self.tag_persist(tag_sid)
                if p is None and getattr(self, '_map', None):
                    # 存档里这条标签没了（执行后销毁）⇒ 退回地图的 [Tags] 字段
                    try:
                        raw = self._map.tags.get(str(tag_sid))
                        if raw:
                            f = [x.strip() for x in raw.split(',')]
                            if f and f[0] in ('0', '1', '2'):
                                p = f[0]
                    except Exception:
                        pass
                if p is not None:
                    vals.append(p)
        if not vals:
            return None
        return '2' if '2' in vals else vals[0]

    def detach_map(self):
        self._map = None
        self._mapm = None
        self._events = None
        self._actions = None

    @property
    def map_path(self):
        return self._map.path if getattr(self, '_map', None) else None

    @property
    def map_status(self):
        """dict: mode / text / ev_mismatch / ac_mismatch / triggers …"""
        return self._mapm

    def _apply_map(self):
        """把地图的归属/真值填进事件、行为列表（不改任何字节）"""
        self._events = None
        self._actions = None
        evs, acs = self.events(), self.actions()
        if not getattr(self, '_map', None):
            self._mapm = None
            return
        m = self._map
        ids = set(r.sid for r in self.records if r.kind == '触发')
        res = {'path': m.path, 'name': m.name, 'encoding': m.encoding,
               'same_ids': (m.trigger_ids() == ids),
               'same_ids_n': len(m.trigger_ids() & ids),
               'triggers': len(m.triggers), 'ev_mismatch': 0, 'ac_mismatch': 0}
        for key, tag, groups, lst in (('ev', '事件', m.events_by_trigger(), evs),
                                      ('ac', '行为', m.actions_by_trigger(), acs)):
            own, mvals, total = self._associate(groups, lst)
            if own is None:                        # 数量不符 ⇒ 归属不给（不猜）
                res[key + '_ok'] = False
                res[key + '_n_map'] = total
                res[key + '_n_save'] = len(lst)
                continue
            res[key + '_ok'] = True
            for it, o, mv in zip(lst, own, mvals):
                it['owner'] = o
                if mv:
                    it['map_type'], it['map_arg'] = mv
            res[key + '_mismatch'] = sum(
                1 for it, mv in zip(lst, mvals) if mv and (it['type'], it['arg']) != mv)
        if res['ev_ok'] and res['ac_ok']:
            res['mode'] = 'exact' if not (res['ev_mismatch'] or res['ac_mismatch']) \
                else 'structural'
        elif res['ev_ok'] or res['ac_ok']:
            res['mode'] = 'partial'
        else:
            res['mode'] = 'count_mismatch'
        res['text'] = self._status_text(res, evs, acs)
        # 每个触发挂几个条件/行为（供「触发」页显示），以及 标签→触发 的指向
        per = {}
        for tid, recs in m.events_by_trigger():
            per.setdefault(tid, {})['ev'] = len(recs)
        for tid, recs in m.actions_by_trigger():
            per.setdefault(tid, {})['ac'] = len(recs)
        res['per_trigger'] = per
        res['tag_to_trigger'] = m.tag_trigger_map()
        self._mapm = res

    @staticmethod
    def _associate(groups, lst):
        """把地图的分组按顺序摊到存档数组上。

        返回 (owner 列表 或 None, 地图值列表, 地图记录总数)。
        总数不符 ⇒ 返回 None（**不猜**，界面显示"无法对应"）。
        """
        total = sum(len(r) for _, r in groups)
        if not groups or total != len(lst):
            return None, [], total
        seq = []
        for tid, recs in groups:
            seq += [(tid, r) for r in recs]
        return [t for t, _ in seq], [r for _, r in seq], total

    @staticmethod
    def _status_text(res, evs, acs):
        ev_ok, ac_ok = res['ev_ok'], res['ac_ok']
        parts = []
        for key, tag, n in (('ev', '事件', len(evs)), ('ac', '行为', len(acs))):
            if not res.get(key + '_ok'):
                parts.append('%s 数量不符（存档 %d / 地图 %d）⇒ 不显示%s归属'
                             % (tag, n, res.get(key + '_n_map'), tag))
            elif res.get(key + '_mismatch'):
                parts.append('%s 有 %d 处数值与地图不同' % (tag, res[key + '_mismatch']))
            else:
                parts.append('%s 已对应 ✓' % tag)
        if ev_ok and ac_ok and not (res['ev_mismatch'] or res['ac_mismatch']):
            head = '地图对照一致 ✓'
        elif ev_ok and ac_ok:
            head = '地图结构相符（数值有差异，地图可能是另一版本）'
        else:
            head = '地图部分对照'
        return '%s：%s' % (head, '；'.join(parts))

    def house_order(self):
        """「所属方序号」的真实顺序（缓存）：**玩家恒为 0**，其余按存档注册序。

        ★★2026-10-20 玩家实测定案：行为/事件的「所属方型」参数用的是**这个序号**，
          不是地编 `[Countries]` 的国家枚举。证据（改.SAV）：
            · 玩家 = Russians；任务自带的「1a最后胜利a」触发行为 1 参数 = **0** ⇒ 0 = 玩家自己；
            · 玩家把参数填成 8（按国家枚举 8=苏俄）⇒ 实际指向**古巴**（本序列表第 9 个）
              ⇒ 读档变成"古巴获胜" ⇒ 玩家失败。与玩家反馈完全一致。
        """
        if getattr(self, '_horder', None) is not None:
            return self._horder
        out = []
        try:
            for h in (self.houses() or []):
                out.append({'short': h.get('short') or '', 'cn': h.get('cn') or '',
                            'is_player': bool(h.get('is_player'))})
        except Exception:
            out = []
        self._horder = out
        return out

    def house_index(self, name):
        """阵营短名/中文名 → 所属方序号（用于写"所属方型"参数）；找不到返回 None。"""
        name = (name or '').strip()
        for i, h in enumerate(self.house_order()):
            if name and name in (h['short'], h['cn']):
                return i
        return None

    def _arg_text(self, ptype, v):
        """把参数按类型翻译成人话（能翻就翻，翻不了给原值）"""
        if ptype == 0:
            return ''
        if ptype == 2:
            ho = self.house_order()
            if 0 <= v < len(ho):
                h = ho[v]
                nm = h['cn'] or h['short']
                return ('%s（玩家）' % nm) if h['is_player'] else nm
            return '所属方#%d' % v
        if ptype == 6:
            return str(v)
        if ptype == 7:                     # ★§283.9 小队型：翻成「ID 名称」（走缓存表）
            lab = self._team_label_map().get(v)
            if lab:
                return lab
            return ('小队 %08X' % v) if v else ''
        return str(v)

    def set_event(self, ev, etype=None, arg=None):
        """改一条事件的类型 / 参数（原地改 u32，不改变长度）

        ⚠ **禁止把类型改成 0（`-无事件-`）**：地编 `FAData.ini` 里 0 是
        `空事件，没作用。` 的**占位项** ⇒ 写成它没有任何用处，只会冒险。
        实测带 0 的档读档直接崩；但注意：**改成合法类型也可能崩**（另有原因，见 排错手册 §11），
        所以这里只做"拒绝无意义/危险写法"的保护，不代表改别的类型就一定安全。
        要"取消一个条件"请**去地图里删掉该事件**（存档里数组长度固定，删不掉），
        或改成永不满足的条件（如 `13 流逝时间` 配一个很大的秒数）。
        """
        if not (0 <= ev['off'] and ev['off'] + 12 <= len(self.plain)):
            raise RuntimeError('事件偏移越界')
        self._revalidate_event(ev)          # ★同会话插字节后 GUI 手里的 ev 可能已漂移
        old_type, old_arg0 = ev['type'], ev['arg']
        if etype is not None:
            etype = int(etype)
            if etype == 0:
                raise ValueError(
                    '不能把事件类型设为 0「-无事件-」：\n'
                    '地编里它是占位项（“空事件，没作用”），写成它没有任何用处。\n\n'
                    '要取消一个条件，请在地编里删掉该事件（存档里数组长度固定），\n'
                    '或改成永不满足的条件，例如「13 流逝时间」并把秒数设得极大。')
            if etype not in EVENT_TYPES:
                raise ValueError('未知事件类型 %d' % etype)
            self.plain[ev['off']:ev['off'] + 4] = etype.to_bytes(4, 'little')
            ev['type'] = etype
            ev['name'] = event_name(etype)
            ev['ptype'] = event_ptype(etype)
        if arg is not None:
            arg = int(arg)
            if not (0 <= arg <= 0xFFFFFF):
                raise ValueError('参数需在 0 ~ 16777215 之间')
            self.plain[ev['off'] + 8:ev['off'] + 12] = arg.to_bytes(4, 'little')
            ev['arg'] = arg
            # ★「13 流逝时间」：事件里的秒数只是"地图 INI 的副本"，引擎计时用的是
            #   该触发 **TagNode +80 的帧数**（= 秒×15，15 fps）⇒ 必须一起改
            #   （2026-10-13 用户游戏内实测确认；2026-10-15 改为按「事件→实例→TagNode」
            #   键链直写，不再按旧值全局匹配 —— 旧写法在 old_arg=0 时会弹
            #   「未找到与 0 秒匹配」的误导性警告，且多事件档定位不到帧数块）。
            if ev['type'] == 13 and (old_type != 13 or old_arg0 != arg):
                node_off, inst_off = self.node_of_event(ev)
                if node_off is not None:
                    old_frames = int.from_bytes(
                        self.plain[node_off + NODE_TICKS_OFF:node_off + NODE_TICKS_OFF + 4],
                        'little')
                    frames = arg * LOGIC_FPS
                    self.plain[node_off + NODE_TICKS_OFF:
                               node_off + NODE_TICKS_OFF + 4] = frames.to_bytes(4, 'little')
                    self._ticks = None
                    extra = ''
                    if old_frames == 0 and frames > 0:
                        # 帧数 0 → 正 ⇒ 该触发此前不是计时触发，要补计时表登记
                        m = self._instance_maps()
                        ck = m['cell_of_tag'].get(m['tag_of_inst'].get(inst_off))
                        if ck is not None:
                            extra = self._timer_ensure_registered(ck)
                            if extra:
                                extra = '　' + extra
                        else:
                            extra = '　⚠ 该触发没有 CellTag，计时表无法登记（请用「修复缺组件触发」）'
                    self.time_sync_note = ('已同步运行期帧数 → %d（等待 %d 秒，%d fps）%s'
                                           % (frames, arg, LOGIC_FPS, extra))
                else:
                    self.time_sync_note = (
                        '⚠ 该触发缺少运行期节点（TagNode）——引擎不会执行它，'
                        '改事件类型/参数都不会生效。请到「触发」页用'
                        '「修复缺组件触发…」补齐后再改。')
        # ★类型级运行期同步（2026-10-15 R2/R4 实测定案）：触发必须被**武装**
        #   （TagNode +80 帧数 > 0 ⇒ 进计时表）引擎才会每帧轮询它；无挂接触发
        #   帧数=0 ⇒ 没有任何机制检查 ⇒ 改什么事件都不生效。
        node_off, inst_off = self.node_of_event(ev)
        if ev['type'] == 8 and node_off is not None:
            old = int.from_bytes(self.plain[node_off + NODE_TICKS_OFF:
                                            node_off + NODE_TICKS_OFF + 4], 'little')
            if old == 0:
                # 「任何事件」永远为真 ⇒ 武装 15 帧（进表即被轮询，进游戏立刻触发）
                self.plain[node_off + NODE_TICKS_OFF:
                           node_off + NODE_TICKS_OFF + 4] = (15).to_bytes(4, 'little')
                self._ticks = None
                m = self._instance_maps()
                ck = m['cell_of_tag'].get(m['tag_of_inst'].get(inst_off))
                extra = ''
                if ck is not None:
                    extra = self._timer_ensure_registered(ck)
                    if extra:
                        extra = '　' + extra
                else:
                    extra = '　⚠ 该触发没有 CellTag，计时表无法登记（请用「修复缺组件触发」）'
                self.time_sync_note = ('「8 任何事件」已武装：TagNode 帧数 0→15'
                                       '（进计时表即被轮询，进入游戏立刻触发）%s' % extra)
        elif node_off is None and not (getattr(self, 'time_sync_note', '') or ''):
            # 非 13 号路径（如改成 8）也要让 GUI 知道根因（一键修复就靠这条提示触发）
            self.time_sync_note = (
                '⚠ 该触发缺少运行期节点（TagNode）——引擎不会执行它，'
                '改事件类型/参数都不会生效。请到「触发」页用'
                '「修复缺组件触发…」补齐后再改。')
        ev['arg_text'] = self._arg_text(ev['ptype'], ev['arg'])
        self._revalidate_event(ev)          # 计时表登记等插字节操作后重定位一次
        self.pending_events[ev['off']] = (ev['type'], ev['arg'])
        return ev

    def set_action(self, ac, atype=None, arg=None, team=None, waypoint=None):
        """改一条行为的类型 / 参数（**行为 80/107 还能改小队与路径点**）

        `ac` 需要带偏移，两种写法都认（§255 修正）：
          · `{'off': …}`      —— **类型字段**的位置（= 行为对象起点 + `0x40`），老口径；
          · `{'action_off': …}` —— **行为对象起点**（`trigger_actions()` 的返回），
            这里自动 `+0x40` 换成类型字段位置。
        ⚠ 旧代码直接拿 `ac['off']` 当"类型字段"，把 `action_off`（对象起点）传进来会
          **写到对象头（CLSID/自键）上**，看起来改成功了，保存后那条行为就"消失"了
          （`_action_runs()` 找不到它）—— 2026-10-24 就是这样把档写坏的。

        `team` / `waypoint`：援军 / 超时空援军的小队键在对象 **`+0x44`**、路径点在 **`+0x58`**
        （§218 实证；`+0x48` 不是小队键）。因为 `off` 是"类型字段"，所以：
          小队 = `off + 4`，路径点 = `off + 0x18`。

        ⚠ 同样**禁止改成 0（`-无行为-`）**：地编里是占位项，写成它没有用处只会冒险。
        """
        off = ac.get('off')
        if off is None and ac.get('action_off') is not None:
            off = int(ac['action_off']) + OBJ_TYPE_OFF
        if off is None:
            raise RuntimeError('这条行为没带偏移信息（需要 off 或 action_off）')
        off = int(off)
        if not (0 <= off and off + 12 <= len(self.plain)):
            raise RuntimeError('行为偏移越界')
        ac['off'] = off
        if atype is not None:
            atype = int(atype)
            if atype == 0:
                raise ValueError(
                    '不能把行为类型设为 0「-无行为-」：\n'
                    '地编里它是占位项，写成它没有任何用处。\n\n'
                    '要取消一个行为，请在地编里删掉它（存档里数组长度固定）。')
            if atype not in ACTION_TYPES:
                raise ValueError('未知行为类型 %d' % atype)
            self.plain[off:off + 4] = atype.to_bytes(4, 'little')
            ac['type'] = atype
            ac['name'] = action_name(atype)
            ac['ptype'] = action_ptype(atype)
        if arg is not None:
            arg = int(arg)
            if not (0 <= arg <= 0xFFFFFF):
                raise ValueError('参数需在 0 ~ 16777215 之间')
            self.plain[off + 8:off + 12] = arg.to_bytes(4, 'little')
            ac['arg'] = arg
        # ★§255：行为 80/107 的「小队键」（对象 +0x44 = off+4）与「路径点」（对象 +0x58 = off+0x18）
        if team is not None:
            self.plain[off + 4:off + 8] = (int(team) & 0xFFFFFFFF).to_bytes(4, 'little')
            ac['team'] = int(team) & 0xFFFFFFFF
        if waypoint is not None:
            self.plain[off + 0x18:off + 0x1C] = (int(waypoint) & 0xFFFFFFFF).to_bytes(4, 'little')
            ac['waypoint'] = int(waypoint) & 0xFFFFFFFF
        ac['arg_text'] = self._arg_text(ac['ptype'], ac['arg'])
        self.pending_actions[off] = (ac['type'], ac['arg'])
        return ac

    # ---------- 记录 ----------
    # 大型自制任务格式（2026-10-14 实测 SAVE5C7B，明文 37.4MB / 599 块）：
    # 「触发/标签 定义」是独立数组（等距 232），类 GUID `61DE341E-0774-11D2-ACA5-00600805B5B5`
    # 在对象 +176；对象 +120 处 **01=触发 / 00=标签**（86 条 vs 地图 82 触发、95 条 vs 82 标签）。
    # 老的小图档里定义+实例挤在一条 352B 记录里（C02D1590@+148、243509BA@+240），两种格式并存。
    # 第三种（2026-10-14 SAVE2DD8）：200B 等距、无 243509BA、随机八位十六进制 ID，见 TRIG_CLASS_GUID2 注释。
    TRIGDEF_GUID = bytes.fromhex('1e34de617407d211aca5006008055bb5')
    GENID_RE = re.compile(rb'(?<![0-9A-Za-z])[0-9A-F]{8}(?=\x00)')
    NAME_MARK = bytes.fromhex('34778800')          # 记录名前的恒定标记（名字紧随其后）
    TAG_CLASS_GUID = bytes.fromhex('33e4f654ed09d211aca5006008055bb5')  # 54F6E433-09ED-11D2
    TRIG_CLASS_GUID_FAM0 = bytes.fromhex('90152dc02a0ad211aca7006008055bb5')  # C02D1590-0A2A-…

    def _scan_records(self):
        pl = self.plain
        out = {}

        def _add(off, m):
            nm_raw = pl[off + 64:off + 96].split(b'\x00')[0]
            nm = _decode_txt(nm_raw)
            if not nm or not (1 <= len(nm) <= 48):
                return
            quad = pl[off + 120:off + 123]
            # ★2026-10-20：先认**标签**。以前靠 +120..123 四字节判据，
            #   会把"持久性=1/2"的标签判成'其它'（SAVE18BE 的 B1/C1 两条就中招）。
            lo, hi = TAG_PERSIST_WIN
            if TAG_GUID_TAIL in pl[off + lo:off + hi]:
                kind = '标签'
            elif m.group().decode() in self._ai_ids():
                # ★★2026-10-20 修正（玩家指出"灰色行和下面的触发重复"）：
                #   存档里**特遣部队/作战小队/动作脚本**和触发/标签共用同一套"8 位 ID + 名字"
                #   的定长记录容器，以前只看 "+176 是不是某个 GUID" 就判成触发，
                #   ⇒ 任务图里的 AI 对象被列成"灰色触发"（SAVE0029/SAVE48DB 各 37 条，
                #     另有 82 条动作脚本被列成"标签"）。现在先按 ID 认 AI 三大件，归 'AI'。
                kind = 'AI'
            elif pl[off + 176:off + 192] == self.TRIGDEF_GUID:
                kind = '触发' if pl[off + 120] == 1 else '标签'
            elif quad == b'\x01\x01\x01':
                kind = '触发'
            elif pl[off + 120:off + 124] == b'\x00\x00\x00\x00':
                kind = '标签'
            else:
                kind = '其它'
            out[off] = Rec(m.group().decode(), nm, off, kind, None, False, '')

        # ---- 第一遍：老 ID 前缀 01xxxxxx（FA2 顺序编号）----
        for m in IDRE.finditer(pl):
            if not _is_hex4(m.group()):
                continue
            off = m.start()
            if off + 400 > len(pl):
                continue
            _add(off, m)

        # ---- 第二遍：随机八位十六进制 ID（自制地图，2026-10-14）----
        # 校验：名字在恒定标记 34778800 之后（偏移可变，标签/触发都适用）
        # + 分类（+120..123 全为 0/1：难度位+启用位 / 标签零段）+ 类 GUID + 成簇。
        gen = {}
        for m in self.GENID_RE.finditer(pl):
            off = m.start()
            if off in out or off + 400 > len(pl):
                continue
            mk = pl.find(self.NAME_MARK, off + 8, off + 120)
            if mk < 0:
                continue
            nm = _decode_txt(pl[mk + 4:off + 160].split(b'\x00')[0])
            if not nm or not (1 <= len(nm) <= 48):
                continue
            span = pl[off:off + 200]
            is_trig = (TRIG_CLASS_GUID2 in span
                       or self.TRIG_CLASS_GUID_FAM0 in span
                       or TRIG_GUID2_HEAD in span)
            is_tag = (self.TAG_CLASS_GUID in span
                      or b'\xe4\xf6\x54\xed\x09\xd2\x11' in span   # GUID 变体（首字节可差 1）
                      or TAG_GUID_TAIL in span)                    # 尾 12 字节（★2026-10-20）
            if is_tag and not is_trig:
                kind = '标签'          # 标签 +120 区是计数/ID，不必为 0/1
            elif is_trig and all(b in (0, 1) for b in pl[off + 120:off + 124]):
                kind = '触发'
            else:
                continue
            gen[off] = (m, kind, nm)
        # 成簇过滤：孤立的八位十六进制候选多半是巧合（真记录都是连续数组）
        goffs = sorted(gen)
        for i, off in enumerate(goffs):
            near = (i > 0 and off - goffs[i - 1] <= 600) or \
                   (i + 1 < len(goffs) and goffs[i + 1] - off <= 600)
            if not near:
                continue
            m, kind, nm = gen[off]
            out[off] = Rec(m.group().decode(), nm, off, kind, None, False, '')

        records = sorted(out.values(), key=lambda r: r.off)
        # ---- 触发「运行时启用位」定位（跨图通用，2026-10-13 定案）----
        self._assign_trigger_flags(records)
        return records

    # AI 三大件的类 GUID（与 ai_objects.AI_CLASSES 同源，这里内联一份避免循环依赖）
    AI_CLASS_GUIDS = (bytes.fromhex('4ea6dbd17807d211aca5006008055bb5'),   # TeamType   268B
                      bytes.fromhex('1e34de617407d211aca5006008055bb5'),   # TaskForce  232B
                      bytes.fromhex('47a6f3428907d211aca5006008055bb5'))   # ScriptType 584B

    def _ai_ids(self):
        """「AI 三大件」（作战小队/特遣部队/动作脚本）的 8 位 ID 集合（缓存）。

        ★2026-10-20（玩家指出灰行与触发"重复"后的定案）：这三类和触发/标签共用
        "8 位 ID + 名字" 的定长记录容器 ⇒ 必须按 ID 先认出来，否则会冒充触发/标签。
        ID 在这三个对象头部 `+0x38`（与 ai_objects.ID_OFF 一致）。
        """
        if getattr(self, '_aiids', None) is not None:
            return self._aiids
        pl = self.plain
        out = set()
        for guid in self.AI_CLASS_GUIDS:
            q = 0
            while True:
                i = pl.find(guid, q)
                if i < 0:
                    break
                q = i + 1
                sid = pl[i + 0x38:i + 0x38 + 8].split(b'\x00')[0]
                try:
                    out.add(sid.decode('ascii'))
                except Exception:
                    pass
        self._aiids = out
        return out

    def _assign_trigger_flags(self, out):
        """按当前 kind 给每条记录设置 启用位 / 只读 状态（分类变更后需重跑）。"""
        pl = self.plain
        offs = sorted(r.off for r in out)

        def _nxt(o):
            return next((x for x in offs if x > o), min(len(pl), o + 2400))

        for r in out:
            if r.kind == '触发':
                # 启用位 = 触发类 GUID 前 8 字节。★搜索窗硬上限 400 字节：
                # 老档 GUID 在记录 +236/+328 处；窗口放开会把**后面别的对象**的
                # GUID 误认成自己的（大档里对象数组彼此挨得很近，实测风险）。
                fo = trig_flag_offset(pl, r.off, min(_nxt(r.off), r.off + 400))
                r.flag_off = fo
                if fo is None and all(b in (0, 1) for b in pl[r.off + 120:r.off + 124]) \
                        and (TRIG_CLASS_GUID2 in pl[r.off:r.off + 200]
                             or self.TRIG_CLASS_GUID_FAM0 in pl[r.off:r.off + 200]):
                    # 新格式（200B 记录，2026-10-14 SAVE2DD8）：无 243509BA GUID，
                    # 启用位固定在 +123（难度位后一字节）；47/47 与地图禁用标志对拍。
                    # ⚠ 老格式里 +123 是地图定义副本（无效），两种格式不要混淆。
                    fo = r.off + 123
                    r.flag_off = fo
                if fo is None:
                    r.enabled = None
                    r.can_toggle = False
                    if pl[r.off + 176:r.off + 192] == self.TRIGDEF_GUID:
                        r.note = ('大任务格式：这里只有「触发定义」，运行期启用位'
                                  '不在这条记录里（未定位）· 只读')
                    else:
                        r.note = '启用位未定位（该记录内无触发类 GUID）· 只读'
                else:
                    r.enabled = bool(pl[fo])
                    r.can_toggle = True
                    r.note = '启用位@+%d = %d' % (fo - r.off, pl[fo])
            elif r.kind == '标签':
                r.flag_off = None
                r.enabled = None
                r.can_toggle = False
                r.note = 'Tag 记录（只读）'
            else:
                r.flag_off = None
                r.enabled = None
                r.can_toggle = False
                r.note = '未识别结构（只读）'
        self.layout_ok = any(r.flag_off is not None for r in out)

    def _trig_flag_off(self, rec):
        """该触发记录的「运行时启用位」相对偏移；定位不到返回 None。

        ★★★ 判据（2026-10-13 定案，跨图通用）：
          触发对象体内含恒定 16 字节**类 GUID** `243509BA-4CF4-11D2-BC26-00104B8FB04D`，
          **启用位 = 该 GUID 前 8 字节处的 1 个字节**（1=启用 / 0=禁用）。

        ★ 记录内偏移**随图变化**（实测新图 +236、旧图 +328）—— 因为 GUID 前有变长内容
          （指针数组等）⇒ **绝不能写死偏移**（我前面几轮就是栽在这上面）。
        ★ 归属：GUID 必须落在「本条记录起点 ~ 下一条记录起点」之间，否则会误认后面对象的 GUID。

        证据链（全部为游戏内可验证的状态）：
          · 外挂禁用实验：T0(启用)/T1(禁用)/T2(恢复) 三档，**只有这一字节** 1→0→1；
          · 旧图成对档 6691(禁用)/6692(启用)：该字节 0/1，且用户游戏内实测「不胜利/胜利」；
          · `测试B禁用.sav`、`无外挂 测试B已禁用.sav`、`单触发 禁用状态.sav` 均读出 0；
          · 60+ 档扫描：该位置**要么不是 0/1（则判只读），要么就是 0/1**，零误报。
        """
        fo = getattr(rec, 'flag_off', None)
        if fo is not None:
            return fo - rec.off
        recs = getattr(self, 'records', None) or []
        offs = sorted(r.off for r in recs)
        nxt = next((x for x in offs if x > rec.off), min(len(self.plain), rec.off + 2400))
        p = trig_flag_offset(self.plain, rec.off, nxt)
        return None if p is None else p - rec.off

    def can_toggle_trigger(self, rec):
        return rec.kind == '触发' and self._trig_flag_off(rec) is not None

    # ---------- 挂接（CellTag）管理 ----------
    # 2026-10-14 五轮实验定案（详见 SKILL.md「正向挂接定案」）：
    #   建筑.tag 字段 → 标签运行期对象 X（CellTag 节点 +16 处的指针）；
    #   CellTag 节点 +64 = 该标签挂的建筑数；
    #   挂上 = 建筑字段←X + 计数+1（两侧缺一不可，游戏内实测：双电厂/跨类型地堡）；
    #   解除 = 字段清 0 + 计数−1（同构）。
    CELLTAG_GUID = bytes.fromhex('32e4f654ed09d211')   # CellTag 运行期类 GUID

    def celltag_nodes(self):
        self._invalidate_if_shifted()
        """扫描全部 CellTag 节点 → [{'node','x','count','count_pos'}]"""
        if getattr(self, '_celltags', None) is not None:
            return self._celltags
        pl = self.plain          # ★ 工作副本（挂/解后计数变化要能读到）
        out = []
        q = 0
        while True:
            i = pl.find(self.CELLTAG_GUID, q)
            if i < 0:
                break
            q = i + 1
            if i + 68 > len(pl):
                continue
            out.append({'node': i,
                        'x': struct.unpack_from('<I', pl, i + 16)[0],
                        'count_pos': i + 64,
                        'count': struct.unpack_from('<I', pl, i + 64)[0]})
        self._celltags = out
        return out

    def _coords_pat(self, cx, cy):
        return struct.pack('<II', cx * 256 + 128, cy * 256 + 128)

    def timer_table_slot(self):
        """**空表**时定位「定时触发器激活登记表」的计数位置；定位不到返回 None。

        空表没有元素可当证据（`celltag_timer_list()` 直接返回 None），只能靠结构指纹：
        表 = `[u32 n][cell 键 × n]`，其后紧跟 `[u32 0][u32 14][u32 14][u32 0a]`
        （2026-10-17 在 all01umd 家族 4 份档上逐字节核对；n=0 时表头正好落在这段之前）。
        要求指纹**全档唯一**，且推出来的 count 位置确实是 u32 0 —— 否则宁可不给。
        """
        pl = self.plain
        hits = []
        i = 0
        while True:
            j = pl.find(TIMER_TABLE_TAIL, i)
            if j < 0:
                break
            hits.append(j)
            i = j + 1
        if len(hits) != 1:
            return None
        pos = hits[0] - 4
        if pos < 0 or struct.unpack_from('<I', pl, pos)[0] != 0:
            return None
        return pos

    def celltag_timer_list(self):
        """定位「定时触发器挂接表」：[count][count × CellTag 键][变长尾]。

        ★★★ 2026-10-14 找到的**新写触发不生效的那一环**（实测 4 档自洽）：
          表里放的是**带计时的触发**（TagNode `+80` 帧数 > 0）所对应的 **CellTag 自身键**。
          证据（帧数 → 表内容）：
            · 原基档  A=150, B=0, C=450  ⇒ 表 = [A的Cell, C的Cell]，count=2；
            · C开     B=0,   C=450       ⇒ 表 = [C的Cell]，count=1；
            · C关     B=0                ⇒ 表 = []，count=0；
            · 工具写出的档 A=150,C=450   ⇒ 表仍只有 [A的Cell]（**漏维护**）——
              这正与「地编写入（地图写，表里有 C）10 秒点亮＋30 秒胜利」
              /「工具写：10 秒点亮、不胜利」的分叉完全对应。
        判据（用于定位，跨图通用）：
          ① 表元素 = 某个 CellTag 对象的自身键（GUID+16）；
          ② 该键在全档**只出现两处**：自己的 +16、以及本表里；
          ③ 元素个数与「TagNode 帧数 > 0 的触发数」一致（用于在多个候选中择优）。
        返回 {'count_pos', 'list_pos', 'keys', 'expected', 'n'}；定位不到返回 None。
        """
        if getattr(self, '_ctimerlist', None) is not None:
            return self._ctimerlist
        pl = self.plain
        keys = set()
        for nd in self.celltag_nodes():
            keys.add(nd['x'])
        if not keys:
            self._ctimerlist = None
            return None
        # 期望元素数 = TagNode 帧数 > 0 的个数
        expected = 0
        q = 0
        while True:
            i = pl.find(TRIG_NODE_GUID, q)
            if i < 0:
                break
            q = i + 1
            if i + NODE_FLAG_OFF + 4 > len(pl):
                continue
            t = struct.unpack_from('<I', pl, i + NODE_TICKS_OFF)[0]
            if 0 < t < 0x7FFFFFFF:
                expected += 1
        # ★2026-10-17 22:5x：**别对 515 个键逐个全档 find**（39MB 档要 7.7 秒）。
        #   取 60 个种子，直接按"连续 CellTag 键区 + 段首计数"认表（与表头位置无关）。
        cands = []
        seen_cp = set()
        seeds = list(keys)[:60]
        for k in seeds:
            pat = struct.pack('<I', k)
            q = 0
            while True:
                o = pl.find(pat, q)
                if o < 0:
                    break
                q = o + 1
                e = o
                while e + 4 <= len(pl):
                    v = struct.unpack_from('<I', pl, e)[0]
                    if v in keys:
                        e += 4
                    else:
                        break
                st = o
                while st - 4 >= 0:
                    v = struct.unpack_from('<I', pl, st - 4)[0]
                    if v in keys:
                        st -= 4
                    else:
                        break
                n = (e - st) // 4
                cp = st - 4
                if cp < 0 or n < 1 or cp in seen_cp:
                    continue
                if struct.unpack_from('<I', pl, cp)[0] != n:
                    continue
                seen_cp.add(cp)
                vals = [struct.unpack_from('<I', pl, st + 4 * j)[0] for j in range(n)]
                cands.append((cp, n, vals))
        # 去重（同一 cp）
        seen, uniq = set(), []
        for cp, n, vals in cands:
            if cp in seen:
                continue
            seen.add(cp)
            uniq.append((cp, n, vals))
        best = None
        for cp, n, vals in uniq:
            if n == expected:
                best = (cp, n, vals)
                break
        if best is None and uniq:
            # ★2026-10-17 22:5x：大任务档里这张表有 **320** 条（旧上限 256 直接滤掉 ⇒
            #   定位失败 ⇒ 新触发不登记 ⇒ **引擎不武装它**）。
            #   择优依据 = "表里装了多少个**有计时**的 CellTag"：真表覆盖 203/204，
            #   其它候选覆盖 0 ⇒ 不靠数量猜。
            node_ticks = {}
            q = 0
            while True:
                i = pl.find(TRIG_NODE_GUID, q)
                if i < 0:
                    break
                q = i + 1
                if i + NODE_TICKS_OFF + 4 <= len(pl):
                    node_ticks[struct.unpack_from('<I', pl, i + 16)[0]] = \
                        struct.unpack_from('<I', pl, i + NODE_TICKS_OFF)[0]
            tick_cells = set()
            for nd0 in self.celltag_nodes():
                nk = struct.unpack_from('<I', pl, nd0['node'] + 60)[0]
                if node_ticks.get(nk, 0) > 0:
                    tick_cells.add(nd0['x'])
            uniq.sort(key=lambda it: (len(set(it[2]) & tick_cells), it[1]), reverse=True)
            if uniq and (set(uniq[0][2]) & tick_cells):
                best = uniq[0]
            elif len(uniq) == 1:
                best = uniq[0]
        self._ctimerlist = None if best is None else {
            'count_pos': best[0], 'list_pos': best[0] + 4,
            'keys': best[2], 'expected': expected, 'n': best[1]}
        return self._ctimerlist

    # ---------- 触发运行期组件：诊断 / 修复（2026-10-15）----------
    # 背景（四档解剖定案，见 _exp/dissect_D.py）：
    #   「修改已有触发的事件类型不生效」的根因**不是** set_event 写错 —— 事件对象确实
    #   改对了（实例键链验证），而是该触发**从根上缺 TagNode/CellTag**（老 V1~V4 式的
    #   残缺集群），引擎根本不会执行它。对照组：新写触发 E（六件套齐全）测试通过。
    #   ⇒ 修复 = 给缺组件的触发补齐 TagNode(+CellTag) + 计时表登记，全部复用
    #     create_trigger 已游戏内验收的克隆机器。

    def _instance_maps(self):
        """扫描触发集群键链（缓存）：
        {'inst': {实例键: 偏移}, 'sid': {实例键: sid}, 'tag_of_inst': {实例键: 标签键},
         'node_of_inst': {实例键: node偏移}, 'cell_of_tag': {标签键: cell**自键**}}"""
        if getattr(self, '_instmaps', None) is not None:
            return self._instmaps
        pl = self.plain
        inst, sid, tag_of, node_of, cell_of = {}, {}, {}, {}, {}
        q = 0
        while True:
            i = pl.find(TRIG_INSTANCE_GUID, q)
            if i < 0:
                break
            q = i + 1
            if i + TRIG_OBJ_SIZE > len(pl):
                continue
            k = int.from_bytes(pl[i + 16:i + 20], 'little')
            inst[k] = i
            sid[k] = pl[i + 56:i + 64].split(b'\x00')[0].decode('ascii', 'replace')
        q = 0
        while True:
            i = pl.find(TRIG_TAGOBJ_GUID, q)
            if i < 0:
                break
            q = i + 1
            if i + TAG_OBJ_SIZE > len(pl):
                continue
            tag_of[int.from_bytes(pl[i + 180:i + 184], 'little')] = \
                int.from_bytes(pl[i + 16:i + 20], 'little')
        q = 0
        while True:
            i = pl.find(TRIG_NODE_GUID, q)
            if i < 0:
                break
            q = i + 1
            if i + TRIG_NODE_SIZE > len(pl):
                continue
            node_of.setdefault(int.from_bytes(pl[i + NODE_TRIGKEY_OFF:
                                                  i + NODE_TRIGKEY_OFF + 4], 'little'), i)
        q = 0
        while True:
            i = pl.find(TRIG_CELLGUID, q)
            if i < 0:
                break
            q = i + 1
            if i + TRIG_CELL_SIZE > len(pl):
                continue
            cell_of.setdefault(int.from_bytes(pl[i + CELL_TAGKEY_OFF:
                                                  i + CELL_TAGKEY_OFF + 4], 'little'),
                               int.from_bytes(pl[i + 16:i + 20], 'little'))
        self._instmaps = {'inst': inst, 'sid': sid, 'tag_of_inst': tag_of,
                          'node_of_inst': node_of, 'cell_of_tag': cell_of}
        return self._instmaps

    def runtime_status(self):
        """每个触发实例的运行期组件状态（GUI「触发」页诊断用）：
        [{'inst_off', 'inst_key', 'sid', 'name', 'tag_key', 'has_node', 'node_off',
          'ticks', 'has_cell', 'record_enabled'}]，按实例偏移升序。"""
        m = self._instance_maps()
        recs = {r.sid: r for r in self.records if r.kind == '触发'}
        out = []
        for k, off in sorted(m['inst'].items(), key=lambda x: x[1]):
            nm = self.plain[off + 120:off + 172].split(b'\x00')[0]
            try:
                nm = nm.decode('utf-8')
            except UnicodeDecodeError:
                nm = nm.decode('gbk', 'replace')
            node_off = m['node_of_inst'].get(k)
            rec = recs.get(m['sid'].get(k, ''))
            out.append({
                'inst_off': off, 'inst_key': k, 'sid': m['sid'].get(k, ''),
                'name': nm, 'tag_key': m['tag_of_inst'].get(k),
                'has_node': node_off is not None, 'node_off': node_off,
                'ticks': int.from_bytes(self.plain[node_off + NODE_TICKS_OFF:
                                                   node_off + NODE_TICKS_OFF + 4], 'little')
                if node_off is not None else None,
                'has_cell': m['tag_of_inst'].get(k) in m['cell_of_tag'],
                'record_enabled': (rec.enabled if rec is not None
                                   and rec.enabled is not None else True),
                # ★运行期启用位 = TagNode+88 最低位（大任务档：定义字节≠运行期状态，
                #   被触发链启用的触发定义里仍是"禁用"——2026-10-15 SAVE194E 实战定案）
                'rt_enabled': bool(int.from_bytes(self.plain[node_off + NODE_FLAG_OFF:
                                                             node_off + NODE_FLAG_OFF + 4],
                                                  'little') & 1)
                if node_off is not None else None})
        return out

    def node_of_event(self, ev):
        """事件 → 所属触发实例 → TagNode。
        返回 (node偏移|None, 实例**自键**|None)。依据：实例 +192 = 事件对象自键（一一对应）。
        ★返回键不返回偏移：插入字节后偏移会漂移，而 `tag_of_inst` 以键为键 ——
          曾因返回偏移导致 0→正帧数时的计时表登记永远查空（2026-10-15 自检抓到）。"""
        pl = self.plain
        if not (0 <= ev['obj'] and ev['obj'] + 20 <= len(pl)):
            return None, None
        evkey = int.from_bytes(pl[ev['obj'] + 16:ev['obj'] + 20], 'little')
        m = self._instance_maps()
        for k, off in m['inst'].items():
            if int.from_bytes(pl[off + 192:off + 196], 'little') == evkey:
                return m['node_of_inst'].get(k), k
        return None, None

    def _timer_ensure_registered(self, cell_key):
        """把 CellTag 自键登记进「定时触发器挂接表」（已登记则不动）。
        返回提示文字。★ 只处理 0→N 方向；N→0（移除）会移动表尾变长数据，尚未需要。
        ★★ 表尾插 4 字节 ⇒ 其后全部明文偏移 +4：必须平移 pending 偏移并失效全部
        缓存（否则同会话的后续编辑会写错位 —— 2026-10-15 自检抓到）。"""
        tl = self.celltag_timer_list()
        if tl is None:
            return '（⚠ 未定位到「定时触发器挂接表」，计时触发可能不武装）'
        if cell_key in tl['keys']:
            return ''
        pl = self.plain
        pos = tl['list_pos'] + 4 * tl['n']
        pl[pos:pos] = struct.pack('<I', cell_key)
        pl[tl['count_pos']:tl['count_pos'] + 4] = \
            struct.pack('<I', tl['n'] + 1)
        self._ctimerlist = None
        # ---- 簿记：低位插入 ⇒ 高位偏移全部 +4 ----
        for store in (self.pending_events, self.pending_actions):
            if store:
                moved = {}
                for k, v in store.items():
                    moved[k + (4 if k >= pos else 0)] = v
                store.clear()
                store.update(moved)
        if self.pending_attach:
            self.pending_attach = [(f + (4 if f >= pos else 0), old)
                                   for f, old in self.pending_attach]
        self._objs = self._events = self._actions = None
        self._ticks = self._celltags = self._ctimerlist = None
        self._instmaps = None
        return ''

    def repair_trigger_runtime(self, inst_off, ticks=None, enabled=None):
        """给**缺运行期组件**的已有触发补齐 TagNode / CellTag（+ 计时表登记）。

        ★ 这就是「修改已有触发的事件类型不生效」的修复（2026-10-15 四档解剖定案）：
          该触发缺 TagNode/CellTag ⇒ 引擎不执行它 ⇒ 改事件/改启用位都无效。
        - inst_off：触发实例的**自键**（推荐，插入字节后仍稳定）或当前明文偏移；
        - ticks=None：按该触发当前事件自动算（13 流逝时间 → 秒×15，否则 0）；
        - enabled=None：按触发记录的启用位（读不到默认 1）；
        - 已有 TagNode 的触发调用本方法 = no-op（返回 already=True）。
        返回 {'inst_key', 'node_key', 'cell_key', 'ticks', 'already', 'warnings'}。
        """
        pl = self.plain
        m = self._instance_maps()
        # 定位实例：优先按自键（★插入字节会使偏移后移，键才是不变量）
        if inst_off in m['inst']:
            ikey = inst_off
            inst_off = m['inst'][ikey]
        else:
            ikey = next((k for k, o in m['inst'].items() if o == inst_off), None)
        if ikey is None:
            raise ValueError('找不到触发实例 @%s' % (hex(inst_off),))
        warnings = []
        if m['node_of_inst'].get(ikey) is not None:
            nd = m['node_of_inst'][ikey]
            return {'inst_key': ikey,
                    'node_key': int.from_bytes(pl[nd + 16:nd + 20], 'little'),
                    'cell_key': m['cell_of_tag'].get(m['tag_of_inst'].get(ikey)),
                    'ticks': int.from_bytes(pl[nd + NODE_TICKS_OFF:nd + NODE_TICKS_OFF + 4],
                                            'little'),
                    'already': True, 'warnings': ['该触发已有 TagNode，无需修复']}
        grps = self._newtrig_groups()
        node_pos, cell_pos = grps.get('node'), grps.get('cell')
        if not node_pos or not cell_pos:
            raise RuntimeError('档内没有 TagNode/CellTag 对象组，无法修复')
        # ticks / enabled 自动值（★直接读明文 —— _objects() 缓存可能已被 set_event 改掉）
        evkey = int.from_bytes(pl[inst_off + 192:inst_off + 196], 'little')
        ev_obj = next((o for o in self._objects()
                       if int.from_bytes(pl[o['obj'] + 16:o['obj'] + 20], 'little') == evkey),
                      None)
        if ticks is None:
            ev_type = int.from_bytes(pl[ev_obj['obj'] + OBJ_TYPE_OFF:
                                        ev_obj['obj'] + OBJ_TYPE_OFF + 4], 'little') \
                if ev_obj is not None else 0
            ev_arg = int.from_bytes(pl[ev_obj['obj'] + OBJ_ARG_OFF:
                                       ev_obj['obj'] + OBJ_ARG_OFF + 4], 'little') \
                if ev_obj is not None else 0
            if ev_type == 13:
                ticks = ev_arg * LOGIC_FPS
            elif ev_type == 8:
                # ★事件 8「任何事件」永远为真，但帧数=0（不进计时表）⇒ 引擎不轮询 ⇒ 永不触发
                #   （2026-10-15 R2/R4 单变量实测定案）⇒ 自动武装为 15 帧（进表即立刻触发）
                ticks = 15
                warnings.append('事件为「8 任何事件」：已自动武装 TagNode 帧数=15 '
                                '（帧数 0 不进计时表、引擎不轮询 ⇒ 永不触发，R2/R4 实测定案）')
            else:
                ticks = 0
        if enabled is None:
            rec = next((r for r in self.records
                        if r.kind == '触发' and r.sid == m['sid'].get(ikey)), None)
            enabled = 1 if rec is None or rec.enabled is None else int(bool(rec.enabled))
        # 新键：同类最大键 +0x1000，全档查重
        used = []
        def fresh(pos_list):
            v = (max(int.from_bytes(pl[p + 16:p + 20], 'little') for p in pos_list)
                 + 0x1000) & 0xFFFFFFFF
            while pl.find(struct.pack('<I', v)) >= 0 or v in used:
                v = (v + 0x1000) & 0xFFFFFFFF
            used.append(v)
            return v
        K_NODE = fresh(node_pos)
        K_CELL = fresh(cell_pos)
        tpl_node = bytes(pl[node_pos[0]:node_pos[0] + TRIG_NODE_SIZE])
        tpl_cell = bytes(pl[cell_pos[0]:cell_pos[0] + TRIG_CELL_SIZE])
        # 构造（与 create_trigger 同款，已游戏内验收）
        nb = bytearray(TRIG_NODE_SIZE)
        nb[0:16] = TRIG_NODE_GUID
        nb[16:20] = struct.pack('<I', K_NODE)
        nb[20:36] = tpl_node[20:36]
        nb[36:40] = b'\xff' * 4
        nb[NODE_TRIGKEY_OFF:NODE_TRIGKEY_OFF + 4] = struct.pack('<I', ikey)
        nb[76:80] = struct.pack('<I', NODE_SPARE76)
        nb[NODE_TICKS_OFF:NODE_TICKS_OFF + 4] = struct.pack('<I', int(ticks))
        nb[NODE_FLAG_OFF:NODE_FLAG_OFF + 4] = struct.pack('<I', 1 if enabled else 0)
        tag_key = m['tag_of_inst'].get(ikey)
        if tag_key is None:
            warnings.append('该触发没有对应的标签对象（tag@+180 落空）——CellTag 已按 0 标签键写出，'
                            '若不生效请反馈样本')
        cb = bytearray(TRIG_CELL_SIZE)
        cb[0:16] = TRIG_CELLGUID
        cb[16:20] = struct.pack('<I', K_CELL)
        cb[20:36] = tpl_cell[20:36]
        cb[36:40] = b'\xff' * 4
        cb[CELL_TAGKEY_OFF:CELL_TAGKEY_OFF + 4] = struct.pack('<I', tag_key or 0)
        cb[CELL_NODEKEY_OFF:CELL_NODEKEY_OFF + 4] = struct.pack('<I', K_NODE)
        cb[64:68] = struct.pack('<I', 0)          # 挂接数 0
        # 插入：node/cell 组尾 + 计时表尾（ticks>0 时）；降序应用
        ins = [(node_pos[-1] + TRIG_NODE_SIZE, bytes(nb)),
               (cell_pos[-1] + TRIG_CELL_SIZE, bytes(cb))]
        if ticks > 0:
            tl = self.celltag_timer_list()
            if tl is None:
                warnings.append('未定位到「定时触发器挂接表」——计时触发可能不触发')
            else:
                ins.append((tl['list_pos'] + 4 * tl['n'], struct.pack('<I', K_CELL)))
                pl[tl['count_pos']:tl['count_pos'] + 4] = struct.pack('<I', tl['n'] + 1)
                self._ctimerlist = None
        for pos, data in sorted(ins, key=lambda x: -x[0]):
            pl[pos:pos] = data
        # 组计数 +1（插入后重新定位；只动 node/cell 两组）
        for guid in (TRIG_NODE_GUID, TRIG_CELLGUID):
            p0 = pl.find(guid)
            if p0 >= 4:
                old = int.from_bytes(pl[p0 - 4:p0], 'little')
                pl[p0 - 4:p0] = struct.pack('<I', old + 1)
        # pending 偏移平移（簿记一致性，同 create_trigger）
        for store in (self.pending_events, self.pending_actions):
            if store:
                moved = {}
                for k, v in store.items():
                    moved[k + sum(len(d) for p, d in ins if p <= k)] = v
                store.clear()
                store.update(moved)
        if self.pending_attach:
            self.pending_attach = [(f + sum(len(d) for p, d in ins if p <= f), old)
                                   for f, old in self.pending_attach]
        # 缓存全失效 + 重扫
        self._objs = self._events = self._actions = None
        self._ticks = self._celltags = self._ctimerlist = None
        self._instmaps = None
        self._house_econ = self._custom_houses = None
        self._house_keys_memo = None
        self._houses_memo = None        # ★P3-14：阵营结构 memo（偏移会变）
        self._tagrecs = None            # ★2026-10-20：标签记录缓存也要失效（否则新建触发的类型读不出）
        self.records = self._scan_records()
        if self._map:
            self._events = None
            self._actions = None
            self._apply_map()
        self.pending_newtrig = True
        return {'inst_key': ikey, 'node_key': K_NODE, 'cell_key': K_CELL,
                'ticks': int(ticks), 'already': False, 'warnings': warnings}

    def set_trigger_never_fire(self, rec):
        """大任务档「等效禁用」：把触发的事件参数改成 9999999 秒（大数法），
        TagNode+80 经 set_event 键链同步 → 两种启用模型（恢复冻结倒计时 /
        按事件参数重新武装）都不会再触发。
        ★适用于「运行期启用位未定位 · 只读」的记录——定义字节≠运行期状态
        （2026-10-15 SAVE194E 实战验证：刷兵2号禁用后 10 秒必崩 → 大数法禁用 → 不崩）。
        返回 {'changed', 'note'}。"""
        rt = {s['name']: s for s in self.runtime_status()}
        s = rt.get(rec.name)
        if s is None:
            raise RuntimeError('按名字 %r 配对不到运行期实例' % rec.name)
        if not s['has_node']:
            return {'changed': False,
                    'note': '该触发没有运行期节点（TagNode）——本来就不会被引擎执行'}
        evkey = int.from_bytes(self.plain[s['inst_off'] + 192:
                                          s['inst_off'] + 196], 'little')
        ev = next((e for e in self.events()
                   if int.from_bytes(self.plain[e['obj'] + 16:e['obj'] + 20],
                                     'little') == evkey), None)
        if ev is None:
            raise RuntimeError('找不到该触发的事件对象（键 %08X）' % evkey)
        old = (ev['type'], ev['arg'])
        self.set_event(ev, arg=9999999)
        return {'changed': True, 'old': old, 'new': (ev['type'], ev['arg']),
                'note': getattr(self, 'time_sync_note', '')}

    def runtime_links(self):
        """运行期键链（**按 sid**，不按名字）：{触发 sid: {'inst_key','ev','ac','name'}}。

        ★2026-10-18 定案：实例对象里的名字只有 **52 字节**（`+120..+172`），长名会被截断
          （大任务档里 "…Clone Clone Clone" 这种名字一截就对不上 records 里的全名），
          所以「选中触发 → 高亮它的事件/行为」**必须走 sid**，不能靠名字比。
        """
        m = self._instance_maps()
        pl = self.plain
        out = {}
        for k, off in m['inst'].items():
            nm = pl[off + 120:off + 172].split(b'\x00')[0]
            try:
                nm = nm.decode('utf-8')
            except UnicodeDecodeError:
                nm = nm.decode('gbk', 'replace')
            out[m['sid'].get(k, '')] = {
                'inst_key': k,
                'ev': int.from_bytes(pl[off + 192:off + 196], 'little'),
                'ac': int.from_bytes(pl[off + 196:off + 200], 'little'),
                'name': nm,
            }
        return out

    def runtime_owner_names(self):
        """(事件键→**实例内名**, 行为键→实例内名) —— 名字是实例里那份原始 52 字节。

        ★2026-10-18 为什么还需要它：`records[].name` 与实例 `+120` 里的名字**长度不同**
          （记录侧更短、会被截断），两边直接相等只能在名字够短时成立。
          GUI 的「选中触发→高亮」用它做**前缀兜底**（sid 对不上时用），
          显示用的全名走 `runtime_owners()`。
        """
        links = self.runtime_links()
        ev, ac = {}, {}
        for d in links.values():
            ev[d['ev']] = d['name']
            ac[d['ac']] = d['name']
        return ev, ac

    def runtime_owners(self):
        """运行期归属（★无需地图）：(事件归属{事件自键: 触发名}, 行为归属{行为自键: 触发名})。

        触发实例 `+192`=事件自键、`+196`=行为自键、`+120`=触发名。
        ★2026-10-18：名字改成取 records 里的**完整名**（同 sid），实例里那份会被 52 字节截断。
        """
        links = self.runtime_links()
        nmap = {r.sid: r.name for r in self.records if r.kind == '触发'}
        ev, ac = {}, {}
        for sid, d in links.items():
            nm = nmap.get(sid) or d['name']
            ev[d['ev']] = nm
            ac[d['ac']] = nm
        return ev, ac

    def locate_building_field(self, cx, cy, mode='zero', x=None):
        """按地图格子坐标定位建筑 tag 字段。

        命中判据（实验定案）：像素坐标对 (cx,cy)×256+128 在明文中命中 2 处且
        **恰好相差 440**（建筑对象内 +104 与 +544 两份坐标副本）→ tag 字段 = 命中−104。
        mode='zero' 要求字段现值 0（未挂接）；'x' 要求字段值 == x（已挂该标签）。
        返回 (字段偏移, None) 或 (None, 错误说明)。
        """
        pl = self.plain          # ★ 工作副本
        pat = self._coords_pat(cx, cy)
        hits = []
        q = 0
        while True:
            i = pl.find(pat, q)
            if i < 0:
                break
            hits.append(i)
            q = i + 1
        hset = set(hits)
        tfs = set()
        for h in hits:
            tf = h - 104
            if tf < 4 or tf + 4 > len(pl):
                continue
            v = struct.unpack_from('<I', pl, tf)[0]
            if mode == 'zero' and v != 0:
                continue
            if mode == 'x' and (x is None or v != x):
                continue
            if (h + 440) not in hset:        # 第二份坐标副本自洽
                continue
            tfs.add(tf)
        if len(tfs) == 1:
            return tfs.pop(), None
        if not tfs:
            return None, ('格(%d,%d) 在存档中未定位到建筑（常见原因：存档比地图旧）' % (cx, cy))
        return None, '格(%d,%d) 定位不唯一（%d 处）' % (cx, cy, len(tfs))

    # 坐标合理性上界：**不能用地图 [Map] Size 当界**——实测 all01umd.map 声明
    # `Size=0,0,80,80` 却有 (85,77) 的建筑（超出声明范围），旧实现因此把该建筑
    # 的挂接从总览里漏掉（2026-10-15 修）。
    MAXPIX = 512 * 256

    def attach_overview(self):
        """挂接总览（建议先挂地图，否则建筑类型解不出）。返回 list of dict:
          {node, x, tag_key, count, count_pos, tagid, trigger_id, trigger_name,
           buildings: [{field, cx, cy, type, map_idx, tagid}]}

        ★2026-10-15：触发/标签改走**键链**解析（与地图、建筑都无关）：
        CellTag+56 = 标签键 → 标签对象+180 = 触发实例键 → 实例+56 = 触发 ID。
        好处：**未挂接任何建筑的 CellTag 也能报出它属于哪个触发**（新建的触发
        挂接数为 0 时依然可选中并挂到建筑上）；建筑列表仍按 X 在全档的出现点
        （+104/+108 像素坐标）反查，命中地图 [Structures] 时补类型。

        ★★§256 性能（玩家 2026-10-25：「触发编辑器每点一个操作都会小卡一下」）：
          本函数对**每个** CellTag 节点都做一次全档 `find`（本档 200 个节点 × 28MB
          ≈ 5.7 万次 find / 1.7 秒）。而 `trigger_detail()` 每次都要调它
          ⇒ 编辑器里改个名字都要卡好几秒。
          现在按 `(明文长度, 触发名指纹)` 缓存整张表；`_invalidate_if_shifted()`
          会清掉它（插入/删除对象后长度变）。
        """
        _key = self._attach_ov_key()
        _c = getattr(self, '_attov', None)
        if _c is not None and _c[0] == _key:
            return _c[1]
        pl = self.plain          # ★ 工作副本
        m = getattr(self, '_map', None)
        smap = {(s['x'], s['y']): s for s in (m.structures() if m else [])}
        t2t = m.tag_trigger_map() if m else {}
        names = {r.sid: r.name for r in self.records}
        # ★★2026-10-23 §204（玩家实测）：「② 该标签挂接的建筑」里类型显示成 `?` ——
        #   因为它只按**地图坐标精确命中**取类型，而地图锚点常常和存档差 1 格
        #   （SAVE1D3F：地图 YACOMD@(181,96)，存档 @(180,96)）。
        #   改：**先按存档记录认**（`buildings()` 的 base 就是挂接字段），地图只兜底。
        try:
            bsave = {b['base']: b for b in self.buildings()}
        except Exception:
            bsave = {}
        _near = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1))

        def _bld_rec(p):
            """按挂接字段偏移找存档里的建筑记录（允许 ±8 字节偏移，实测有差 2 字节的情形）。"""
            b = bsave.get(p)
            if b:
                return b
            for d in (2, 4, 6, 8, -2, -4, -6, -8):
                b = bsave.get(p + d)
                if b:
                    return b
            return None

        # ★§204b：标签也可能挂在**单位**上（实测触发 01000250「飞碟被攻击」挂的是 DISK×6、
        #   还有 SNIPE/SCHP/HOWI）。旧实现只认建筑 ⇒ 这些行全显示成 `?`，玩家根本不知道挂的是谁。
        try:
            _urecs = sorted(self.unit_records(), key=lambda r: r['off'])
        except Exception:
            _urecs = []
        _uoffs = [r['off'] for r in _urecs]

        def _unit_of(p):
            import bisect as _bs
            k = _bs.bisect_right(_uoffs, p) - 1
            if k < 0:
                return None
            r = _urecs[k]
            if r['off'] <= p < r['comp'] + r['comp_size']:
                return r
            return None

        def _map_hit(cx, cy, tname):
            """地图 [Structures] 里的对应条目（先精确、再同类型邻近 ±1 格兜底）。"""
            if cx is None or cy is None:
                return None
            for dx, dy in _near:
                s = smap.get((cx + dx, cy + dy))
                if not s:
                    continue
                if dx == 0 and dy == 0:
                    return s
                if tname and str(s.get('type', '')).upper() == str(tname).upper():
                    return s
            return None

        def _index(guid):
            """类 GUID → {自身键: 对象偏移}"""
            out, q = {}, 0
            while True:
                i = pl.find(guid, q)
                if i < 0:
                    return out
                q = i + 1
                out[struct.unpack_from('<I', pl, i + 16)[0]] = i

        tags_by_key = _index(TRIG_TAGOBJ_GUID)
        insts_by_key = _index(TRIG_INSTANCE_GUID)
        out = []
        for nd in self.celltag_nodes():
            X = nd['x']
            pat = struct.pack('<I', X)
            q = 0
            blds = []
            while True:
                p = pl.find(pat, q)
                if p < 0:
                    break
                q = p + 1
                if p == nd['node'] + 16 or p + 112 > len(pl):
                    continue
                px, py = struct.unpack_from('<II', pl, p + 104)
                if not (0 < px < self.MAXPIX and 0 < py < self.MAXPIX):
                    continue
                cx, cy = (px - 128) // 256, (py - 128) // 256
                # ★先认存档记录（最可靠：type/hp 直接读），地图只补"地图建筑ID"
                sb = _bld_rec(p)
                ur = None if sb else _unit_of(p)
                if not sb and not ur:
                    # 既不是建筑记录、也不在单位记录里 ⇒ 多半是「指针表里的那个键」
                    # （实测 X 会出现在全局指针表里，紧随其后的字节凑巧像坐标）
                    continue
                if sb:
                    tname, cx, cy = sb['type'], sb['x'], sb['y']
                else:
                    tname, cx, cy = ur['type'], ur['x'], ur['y']
                s = _map_hit(cx, cy, tname)
                blds.append({'field': p, 'cx': cx, 'cy': cy,
                             'type': tname or (s['type'] if s else None),
                             'kind': 'building' if sb else 'unit',
                             'hp': sb.get('hp') if sb else None,
                             'in_save': True,
                             'map_idx': s['idx'] if s else None,
                             'tagid': s['tagid'] if s else None})
            # ★键链解析：CellTag → 标签 → 触发（不依赖地图）
            k_tag = int.from_bytes(pl[nd['node'] + 56:nd['node'] + 60], 'little')
            t_off = tags_by_key.get(k_tag)
            k_trig = (int.from_bytes(pl[t_off + 180:t_off + 184], 'little')
                      if t_off else 0)
            i_off = insts_by_key.get(k_trig)
            tid = pl[i_off + 56:i_off + 64].decode('latin1') if i_off else None
            tag_sid = pl[t_off + 56:t_off + 64].decode('latin1') if t_off else None
            if not (tid and tid in names):
                # 兜底：回退到旧路（建筑 → 地图 [Structures] 的 Tag 列 → 触发）
                tagids = set(b['tagid'] for b in blds if b['tagid'])
                mid = tagids.pop() if len(tagids) == 1 else None
                tid = t2t.get(mid) if mid else tid
                tag_sid = tag_sid or mid
            out.append({'node': nd['node'], 'x': X, 'tag_key': k_tag,
                        'count': nd['count'], 'count_pos': nd['count_pos'],
                        'tagid': tag_sid,
                        'trigger_id': tid,
                        'trigger_name': names.get(tid, '') if tid else '',
                        'buildings': blds})
        out.sort(key=lambda d: (-len(d['buildings']), d['node']))
        self._attov = (_key, out)
        return out

    def attach_orphan_tags(self):
        """**有标签对象、却没有 CellTag 节点**的触发（挂接页看不到的那一类）。

        ★2026-10-23 玩家实测发现：「尤里中心铁幕解白宫铁幕」这条触发在挂接页找不到。
          查明原因：挂接页是按 **CellTag 节点**建的，而这条触发**一个节点都没有** ——
          标签 01000438 还在，但 `tag_key` 在全档只出现 1 次（它自己的自键），
          没有任何 CellTag 指向它 ⇒ `trigger_runtime_state()` 判 `verdict='executed'`
          （一次性触发执行完，引擎把节点销毁了）。于是它在挂接页"人间蒸发"。

        返回 [{'tag_off','tag_key','tagid','trigger_id','trigger_name','persist',
               'executed','reason'}]，按 (executed, tagid) 排序。
        `reason`: 'executed'（节点已销毁=已执行） / 'never'（还挂着定义、从未挂过对象）。
        """
        pl = self.plain
        names = {}
        try:
            names = {r.sid: r.name for r in self.records}
        except Exception:
            pass
        # 被节点引用的标签键
        used = set()
        for n in self.celltag_nodes():
            used.add(struct.unpack_from('<I', pl, n['node'] + 56)[0])
        inst_by_key = {}
        tags = []
        q = 0
        while True:
            i = pl.find(TRIG_INSTANCE_GUID, q)
            if i < 0:
                break
            q = i + 1
            inst_by_key[struct.unpack_from('<I', pl, i + 16)[0]] = i
        q = 0
        while True:
            i = pl.find(TRIG_TAGOBJ_GUID, q)
            if i < 0:
                break
            q = i + 1
            if i + 0xA4 > len(pl):
                continue
            k = struct.unpack_from('<I', pl, i + 16)[0]
            if k in used:
                continue                      # 有节点 ⇒ 属于挂接页
            tags.append((i, k))
        out = []
        for off, k in tags:
            tk = struct.unpack_from('<I', pl, off + 180)[0]
            io = inst_by_key.get(tk)
            tid = pl[io + 56:io + 64].decode('latin1') if io else None
            if not tid:
                continue                      # 标签没指触发（地图标签/孤立对象）⇒ 不列
            persist = struct.unpack_from('<i', pl, off + 0x9C)[0]
            ev_fired, ev_types, rep = False, [], None
            try:
                st = self.trigger_runtime_state(tid)
                executed = bool(st.get('executed'))
                ev_fired = bool(st.get('ev_fired'))
            except Exception:
                executed = False
            try:
                ev_types = [e.get('type') for e in self.trigger_event_list(tid)]
            except Exception:
                ev_types = []
            try:
                rep = self.trigger_repeat_type(tid)
            except Exception:
                rep = None
            # ★§203：**别再断言"已执行"**。存档字节只能证明"没有 CellTag 节点"，
            #   "执行过"与"从来没挂上"在这份数据里同形（触发页早就按 §119 的结论不加断言）。
            #   计时型（首事件 13 / 轮询 36·37）必须有节点才被评估 ⇒ 没节点就是"不会被评估"；
            #   回调型（被攻击/被摧毁/进入…）同理 —— 引擎只遍历 CellTag 的实例链。
            kind = 'timer' if (ev_types and ev_types[0] in (13,) or
                               any(t in POLL_EVENT_TYPES for t in ev_types)) else 'callback'
            out.append({'tag_off': off, 'tag_key': k,
                        'tagid': pl[off + 56:off + 64].decode('latin1'),
                        'trigger_id': tid,
                        'trigger_name': names.get(tid, ''),
                        'persist': persist,
                        'executed': executed,
                        'ev_fired': ev_fired,
                        'ev_types': ev_types,
                        'kind': kind,
                        'rep': rep,
                        'reason': 'executed' if executed else 'never'})
        out.sort(key=lambda d: (not d['executed'], d['tagid']))
        return out

    def attach_free_buildings(self):
        """**存档口径**的「可挂接建筑」→ [{base,type,x,y,hp,map_idx,map_tag}]。

        ★★ 2026-10-23 玩家实测纠错（`SAVE1D3F.SAV` + `all01umd.map`）：
          地图 `[Structures]` 里 YACOMD 的 Tag 列写着 `01000438`（地图认为它挂着触发），
          但**存档里那栋 (180,96) 的挂接键 = 0x00000000**（真没挂）。
          旧实现按**地图的 Tag 列**判断"已挂接"⇒ 直接把它从③里藏起来，玩家没法挂。
          编辑器改的是**存档** ⇒ 一律**以存档为准**：挂接键为 0 的就是"可挂"，
          地图只用来补一个"地图建筑ID"（而且地图锚点可能和存档差 1 格，
          实测 YACOMD 地图 (181,96) / 存档 (180,96)，所以要按同类型邻近格兜底）。
        """
        m = getattr(self, '_map', None)
        smap = {}
        if m:
            for s in m.structures():
                smap.setdefault((s['x'], s['y']), s)
        near = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1))
        out = []
        for b in self.buildings():
            if b.get('tag'):
                continue                      # 存档里已挂接 ⇒ 不是"可挂"
            s = smap.get((b['x'], b['y']))
            if s is None and smap and b.get('type'):
                for dx, dy in near:
                    c = smap.get((b['x'] + dx, b['y'] + dy))
                    if c and str(c.get('type', '')).upper() == str(b['type']).upper():
                        s = c
                        break
            out.append({'base': b['base'], 'type': b['type'],
                        'x': b['x'], 'y': b['y'], 'hp': b.get('hp'),
                        'map_idx': (s or {}).get('idx'),
                        'map_tag': (s or {}).get('tagid')})
        out.sort(key=lambda d: (str(d['type']).upper(), d['y'], d['x']))
        return out

    # ★★★★★ 2026-10-23 §211（玩家："克隆法不简单，建筑最后是直接写入法一小时走通的"）：
    #   单位也要有"直接写入法" —— **从一个有该型号的存档里取一台真机记录**，只换身份字段，
    #   其余内容照抄。这样就不存在"捐赠者的机体状态"问题（§209 的根因）。
    #   下面这张表是**必须保留本档脚手架值**的槽（身份/指针/坐标/类型键），其余一律从真机抄。
    IMPORT_KEEP_LOCAL = [
        0x010, 0x024,              # 自键、实例 ID
        0x048,                     # CellTag 指针（本档对象）
        0x0B0, 0x0B4,              # 像素坐标
        0x0E0, 0x0E8,              # 格对象指针 / 格派生
        0x118, 0x128,              # 格对象指针
        0x160, 0x198,              # House / 目标对象指针
        0x224,                     # 所在格键
        0x268, 0x26C,              # 第二套坐标
        0x352, 0x560,              # 所属方
        0x3A8, 0x3C0, 0x3DC,       # 指针 / 自键副本
        0x528, 0x558, 0x5B8, 0x5E8, 0x688,   # 各种对象指针
        0x658, 0x680,              # 目的地像素（真机是"当前任务点"，抄过来会乱飞）
        0x6D8,                     # 类型键（这个必须是目标型号）
        0x91C, 0x928, 0x92C, 0x930, 0x95C, 0x96C, 0x974, 0x978, 0x98C, 0x9A4, 0x9B0,
        0x960, 0x964,              # 伴随段里的目的地格
    ]

    # 常见 CLSID 的"尾部锚点"：(锚点字节, 锚点在 16B 里的起始偏移)
    #   锚点后面就是 `[16B GUID][4B key]` 里的 key
    _KEY_TAILS = ((b'\x00\x60\x08\x05\x5b\xb5', 6),
                  (b'\xb7\x09\x00\xa0\x24\xdd\xaf\xd1', 8))

    def object_keys(self):
        """本档所有“对象键”的集合（按 CLSID 尾部锚点扫，秒级）。

        用途：判断一个 dword 是"值"还是"指向别的对象的指针"。抄记录时指针必须留在本档。
        """
        if getattr(self, '_objkeys', None) is not None:
            return self._objkeys
        pl = self.plain
        ks = set()
        for tail, off in self._KEY_TAILS:
            i = pl.find(tail)
            while i >= 0:
                q = i + off
                if q + 4 <= len(pl):
                    ks.add(struct.unpack_from('<I', pl, q)[0])
                i = pl.find(tail, i + 1)
        self._objkeys = ks
        return ks

    # ---------------- §216 单位模板库 ----------------
    def learn_unit_signatures(self, min_votes=2, merge=True):
        """从**本档**学出每个型号的"签名"（移动方式 + 8 个机体参数）→ {类型: 签名}。

        投票规则：同型号取**多数值**（`有空地.SAV` 8 架基洛夫里 7 架一致、1 架是刚出生态，
        取多数就对了）。只有 1 架时直接采信，但标 `votes=1`（可信度低）。

        ★这里学的是**数值**（每型号 ~40 字节），不是整条记录，所以：
          · 可以放心存成 JSON，没有版权问题；
          · 不会像"内置整条记录"那样在换游戏版本/Mod 后与实际布局错配。
        """
        pl = self.plain
        recs = self.unit_records()
        by = {}
        for r in recs:
            by.setdefault(r['type'].upper(), []).append(r)
        out = {}
        for t, rs in by.items():
            if not rs:
                continue
            r0 = rs[0]
            try:
                n = min(r0['size'] + r0['comp_size'], 0x9C0)
                blobs = [bytes(pl[x['off']:x['off'] + n]) for x in rs]
            except Exception:
                continue
            params = {}
            for o in _SIG_SLOTS:
                if o + 4 > n:
                    continue
                cnt = {}
                for b in blobs:
                    v = struct.unpack_from('<I', b, o)[0]
                    cnt[v] = cnt.get(v, 0) + 1
                if not cnt:
                    continue
                v = max(cnt.items(), key=lambda kv: (kv[1], kv[0] == 0))[0]
                # 只在"多数够硬"时采信（1 架也算，但标 votes）
                params['0x%X' % o] = v
            if not params:
                continue
            out[t] = {
                'class': r0.get('class'),
                'loco': r0.get('loco'),
                'size': r0['size'],
                'comp_size': r0['comp_size'],
                'comp_guid': (r0.get('comp_guid') or b'').hex(),
                'votes': len(rs),
                'params': params,
            }
        if merge:
            cache = load_signature_cache()
            for t, sig in out.items():
                old = cache['types'].get(t)
                # 新样本票数更多才覆盖（避免被"只有 1 架"的档冲掉好数据）
                if old and old.get('votes', 0) > sig['votes']:
                    continue
                sig['src'] = os.path.basename(getattr(self, 'path', '') or '')
                cache['types'][t] = sig
            cache['slots'] = ['0x%X' % o for o in _SIG_SLOTS]
            save_signature_cache(cache)
            self._sigcache = cache
        return out

    def unit_signature(self, t):
        """查模板库 → 签名 dict（没有就返回 None）。"""
        c = getattr(self, '_sigcache', None)
        if c is None:
            c = load_signature_cache()
            self._sigcache = c
        return (c.get('types') or {}).get(str(t).upper())

    def add_unit_by_signature(self, t, cx, cy, house=None, veterancy=None, params=True,
                              auto_nudge=False):
        """**用模板库直接写入**：档里没有该型号、也没有参考存档时，靠签名表放置。

        与 `import_unit_from()` 的区别：那边要一份"含真机的存档"抄内容；
        这边不需要任何参考档 —— 内容来自模板库里那 ~40 字节的签名。
        """
        t = str(t).strip().upper()
        sig = self.unit_signature(t)
        if not sig:
            raise UnitUnavailable(
                '模板库里没有 %s 的签名。\n办法：\n'
                '① 在「放置单位」页点「📚 学习模板」，选一份**含该型号**的存档；\n'
                '② 或者直接选一份含该型号的参考存档导入（直接写入法）。' % t)
        # 脚手架：档内有同型号就同型号克隆；没有就按签名里的移动方式借同类骨架
        info = self.add_unit_by_clone(t, cx, cy, house=house, veterancy=veterancy,
                                      auto_nudge=auto_nudge)
        rec = [r for r in self.unit_records() if r['key'] == info['key']][0]
        n = 0
        if params:
            # ★`+0x940..+0x958` 这几个槽在**伴随段**里（记录 2316 字节之后），
            #   所以边界要用 `size + comp_size`；只按 `size` 判会把 6 个参数全筛掉
            #   （自检 §216 抓到的：8 个参数只应用了 2 个）。
            total = rec['size'] + rec['comp_size']
            for k, v in (sig.get('params') or {}).items():
                o = int(k, 16)
                if o + 4 <= total:
                    self.plain[rec['off'] + o:rec['off'] + o + 4] = struct.pack('<I', v & 0xFFFFFFFF)
                    n += 1
            # 空中单位按类型高度重新对齐（签名里就有 +0x270）
            alt = struct.unpack_from('<I', self.plain, rec['off'] + 0x270)[0]
            if info.get('loco') in ('Fly', 'Aircraft') and alt:
                self.plain[rec['off'] + 0xB8:rec['off'] + 0xB8 + 4] = struct.pack('<I', alt)
        for attr in ('_urecs', '_uoffs', '_occ', '_blds'):
            if hasattr(self, attr):
                setattr(self, attr, None)
        info = dict(info)
        info['params_applied'] = n
        info['signature_votes'] = sig.get('votes')
        info['signature_src'] = sig.get('src')
        return info

    # ================= §217 刷兵三件套：特遣部队 + 作战小队 + 触发(80) =================
    # ★★2026-10-23 玩家指路（截图 + `SAVE0029.SAV` 逆向）：
    #   带 `SPAWNS=` 的单位（V3 / 无畏 / 航母）**没法手写单位记录**（§209 那次隐形就是它），
    #   但引擎自己有正规造法：
    #       特遣部队(TaskForce) → 作战小队(Team) → 触发「8 任何事件 + 80 援军(作战小队)@路径点」
    #   脚本可以沿用模板小队的（`+0xF4` 指向 ScriptType），**这样就绕开了最难的 AI 脚本**。
    #
    #   三件套的二进制（实测 `SAVE0029.SAV`，全部是 [count][对象×count] 连续组）：
    #   ┌ TaskForce 232B：+0x10 键 / +0x38 ID / +0x78 名称 / +0xAC 分组(-1)
    #   │                 +0xB0 单位**种类数** / 从 +0xB8 起每 8B 一组「数量, 类型键」×6
    #   ├ Team      268B：+0x10 键 / +0x38 ID / +0x78 名称 / +0xAC **组内下标**
    #   │                 +0xC8 优先级 / +0xCC 最大同时存在 / +0xD8 国家(所属方键)
    #   │                 **+0xB4 星级**（§252.17 对账地编定案）/ +0xF4 脚本键 / +0xF8 **特遣部队键**
    #   └ 行为 80：+0x40=80 / **+0x44 = 小队键**（不是工具默认的 +0x48！）/ +0x58 = 路径点
    #
    #   ★"任何事件"(事件 8) 型触发**不需要 CellTag 节点**：实测 47 条 80 行为里，
    #     原图那条（`01000494`）挂接数 = 0、没有任何 CellTag，照样在开局刷出单位。
    TF_CLS = bytes.fromhex('1e34de617407d211aca5006008055bb5')
    TEAM_CLS = bytes.fromhex('4ea6dbd17807d211aca5006008055bb5')
    SCRIPT_CLS = bytes.fromhex('47a6f3428907d211aca5006008055bb5')
    TF_SIZE = 232
    TEAM_SIZE = 268
    SCRIPT_SIZE = 584          # §222 实测：相邻 ScriptType 对象差全是 584

    # ★§254（p201）：特遣/小队有两类来源，靠 **ID 格式**区分（T85 实测 100%/100% 互不混）
    #   · **地图定义**（地编里能看见、能编辑）：ID 是 `01000000 + 序号`，形如 `01000241`
    #     —— T85 里 100 个特遣 / 125 个小队属此类，序号 38..1170
    #   · **AI 运行期生成**（AITrigger 实例化出来的，地编里看不到、改了也没用）：
    #     ID 是 8 位随机十六进制，形如 `05C60C4C` / `0D082CFC`；名字是「数量 + 单位名」自动拼的
    #     （"3 GIs, 3 Guards"、"Allied Base Guard - Refine"）
    #   交叉验证：163 个 AI 小队**全部**只引用 AI 特遣、125 个地图小队**全部**只引用地图特遣，0 例外。
    #   ⇒ 建小队/看列表时应默认只显示「地图定义」的那批。
    MAP_SID_RE = re.compile(r'^0100[0-9A-Fa-f]{4}$')

    @classmethod
    def is_map_sid(cls, sid):
        """这个 ID 是不是「地图定义」的（地编里可见）？否则 = AI 运行期生成。"""
        try:
            return bool(cls.MAP_SID_RE.match(str(sid or '').strip()))
        except Exception:
            return False

    def _obj_group(self, cls, size, label='对象'):
        """校验 `[count][对象×count]` 连续组 → {'first','count_pos','count','offs'}。

        任何一处不符就**拒绝写入**（宁可报错，也不写出一份读档会崩的档）。
        """
        pl = bytes(self.plain)
        offs = []
        q = 0
        while True:
            i = pl.find(cls, q)
            if i < 0:
                break
            q = i + 1
            offs.append(i)
        if not offs:
            raise RuntimeError('本档里没有%s（CLSID %s）' % (label, cls.hex()))
        for k in range(len(offs) - 1):
            if offs[k + 1] - offs[k] != size:
                raise RuntimeError(
                    '%s组不连续（第 %d 个间距 %d，期望 %d）⇒ 结构与本工具的认识不符，拒绝写入'
                    % (label, k + 1, offs[k + 1] - offs[k], size))
        cnt = struct.unpack_from('<I', pl, offs[0] - 4)[0]
        if cnt != len(offs):
            raise RuntimeError('%s组计数(%d)与实际对象数(%d)不符 ⇒ 拒绝写入'
                               % (label, cnt, len(offs)))
        return {'first': offs[0], 'count_pos': offs[0] - 4, 'count': len(offs), 'offs': offs}

    def _free_group_key(self, keys):
        """给"组对象"（特遣/小队）分配一个全档唯一的键（0x13xxxxxx 区，步长 0x1000）。"""
        pl = bytes(self.plain)
        k = ((max(keys) + 0x1000) if keys else 0x13400000) & 0xFFFFFFFF
        # ★坑（2026-10-23 自己踩的）：候选键每次变化后必须**重新打包**再查，
        #   否则 `pat` 一直是第一个候选 ⇒ 一旦它已被占用就是死循环。
        while pl.find(struct.pack('<I', k)) >= 0:
            k = (k + 0x1000) & 0xFFFFFFFF
        return k

    def _next_sid(self, count=1):
        """下一个（或接下来 `count` 个**连续**）空闲场景对象 ID —— `0100xxxx` 格式。

        ★§254 修两个 bug：
        1. 旧实现是"全档最大 ID + 1"，而档里存在**引擎 AI 运行期生成**的特遣/小队/脚本，
           它们的 ID 是 8 位随机十六进制（`0ECCF03C` 这种）。于是我们新建的对象会拿到
           `0ECCF03D` —— **长得和 AI 生成的一模一样**：地编里看不见、本工具
           「地图定义」过滤器也会把它隐藏（玩家 2026-10-24 实测：新建的特遣在下拉里找不到）。
        2. `create_trigger` 自己另有一套取号逻辑（靠 `_ai_ids()` 的**缓存**），
           而我新建特遣后缓存没刷新 ⇒ 触发拿到同一个 `01000494`，**ID 撞车**。
           现在两处统一走这里，并且**直接现扫明文**，不读任何缓存。

        实测 T85 里 特遣 39..1170 / 小队 38..1169 / 触发到 `01000494` ⇒ 三类**共享同一序列**。
        `count>1` 时返回 list（触发要"触发ID + 标签ID"两个连号）。
        """
        pl = bytes(self.plain)
        used = set()

        def _take(s):
            s = str(s or '').strip()
            if self.MAP_SID_RE.match(s):
                used.add(int(s[4:], 16))

        for cls, sz in ((self.TF_CLS, self.TF_SIZE), (self.TEAM_CLS, self.TEAM_SIZE),
                        (self.SCRIPT_CLS, self.SCRIPT_SIZE)):
            try:
                g = self._obj_group(cls, sz)
            except Exception:
                continue
            for o in g['offs']:
                try:
                    _take(pl[o + 56:o + 64].decode('latin1'))
                except Exception:
                    pass
        # 兜底：脚本组校验失败时用解析结果补（宁可多扫一遍，也不能撞号）
        try:
            for x in self.script_types():
                _take(x.get('sid'))
        except Exception:
            pass
        for r in getattr(self, 'records', []):
            _take(getattr(r, 'sid', ''))

        if not used:
            # 老档一件 `0100xxxx` 都没有（极罕见）⇒ 退回旧的"全局最大 +1"
            best = 0
            for cls, sz in ((self.TF_CLS, self.TF_SIZE), (self.TEAM_CLS, self.TEAM_SIZE)):
                try:
                    g = self._obj_group(cls, sz)
                except Exception:
                    continue
                for o in g['offs']:
                    try:
                        best = max(best, int(pl[o + 56:o + 64].decode('latin1'), 16))
                    except Exception:
                        pass
            for r in getattr(self, 'records', []):
                try:
                    best = max(best, int(str(r.sid), 16))
                except Exception:
                    pass
            out = ['%08X' % (best + 1 + i) for i in range(max(1, int(count)))]
            return out if count > 1 else out[0]

        n = max(1, int(count))
        got, v = [], max(used) + 1
        while len(got) < n:
            if v > 0xFFFF:
                raise RuntimeError('场景对象编号已用尽（0100FFFF 已存在），无法再新建对象')
            if v not in used:
                got.append('0100%04X' % v)
            v += 1
        return got if count > 1 else got[0]

    def _append_group(self, cls, size, tpl_off, label='对象'):
        """把模板对象复制一份**追加到组尾**，并把组计数 +1 → 新对象偏移。"""
        g = self._obj_group(cls, size, label)
        pl = bytearray(self.plain)
        pos = g['offs'][-1] + size
        pl[pos:pos] = pl[tpl_off:tpl_off + size]
        struct.pack_into('<I', pl, g['count_pos'], g['count'] + 1)
        self.plain = pl
        self._invalidate_if_shifted()
        # ★§254：`_ai_ids()` 是缓存 —— 新建完必须让它重扫，否则"先建特遣、再建触发"
        #   会拿到与特遣相同的 `0100xxxx`（ID 撞车）。
        self._aiids = None
        return pos, g['count']

    def taskforces(self):
        """列出全部特遣部队：[{off,key,sid,name,units:[{type,count}],slots}]。"""
        g = self._obj_group(self.TF_CLS, self.TF_SIZE, '特遣部队')
        pl = self.plain
        tmap = {}
        for k, v in (self.unit_type_index() or {}).items():
            if v.get('key'):
                tmap[v['key']] = k
        out = []
        for off in g['offs']:
            n = struct.unpack_from('<I', pl, off + 0xB0)[0]
            units = []
            for i in range(min(max(n, 0), 6)):
                cnt = struct.unpack_from('<I', pl, off + 0xB8 + 8 * i)[0]
                tk = struct.unpack_from('<I', pl, off + 0xBC + 8 * i)[0]
                units.append({'type': tmap.get(tk, ''), 'count': cnt, 'type_key': tk})
            sid = bytes(pl[off + 56:off + 64]).decode('latin1', 'ignore')
            out.append({'off': off,
                        'key': struct.unpack_from('<I', pl, off + 0x10)[0],
                        'sid': sid,
                        'defined': self.is_map_sid(sid),     # ★§254：False = AI 运行期生成
                        'name': bytes(pl[off + 0x78:off + 0x78 + 48]).split(b'\x00')[0]
                                .decode('gbk', 'ignore'),
                        'n_types': n, 'units': units})
        return out

    def team_types(self):
        """列出全部作战小队：[{off,key,sid,name,tf_key,country_key,priority,max,veteran,script_key}]。

        ★★2026-10-27 性能：**必须缓存**。`_obj_group` 每次都全档 find + 解压，
          实测单次 ~25ms；而 `_arg_text()`（每行行为都调）与 GUI 的 `_team_choices()`
          会把它调上千次 ⇒ 一次「添加行为」卡 45 秒。键 = 明文长度（增删字节自动失效）。
        """
        memo = getattr(self, '_teamtypes', None)
        if memo is not None and memo[0] == len(self.plain):
            return [dict(x) for x in memo[1]]
        g = self._obj_group(self.TEAM_CLS, self.TEAM_SIZE, '作战小队')
        pl = self.plain
        out = []
        for off in g['offs']:
            u = lambda x: struct.unpack_from('<I', pl, off + x)[0]
            sid = bytes(pl[off + 56:off + 64]).decode('latin1', 'ignore')
            out.append({'off': off, 'key': u(0x10),
                        'sid': sid,
                        'defined': self.is_map_sid(sid),     # ★§254：False = AI 运行期生成
                        'name': bytes(pl[off + 0x78:off + 0x78 + 48]).split(b'\x00')[0]
                                .decode('gbk', 'ignore'),
                        'index': u(0xAC), 'group': u(0xB0),
                        'priority': u(0xC8), 'max': u(0xCC),
                        'country_key': u(0xD8),
                        # ★§252.17：星级是 **+0xB4**（旧代码这里读 +0xF0，与"写"一起错，所以
                        #   自检一直自洽、游戏里却从不生效）。FA2 对账：星级3/优先级5/最大5
                        #   ↔ `+0xB4=3 / +0xC8=5 / +0xCC=5`。
                        'veteran': u(0xB4),
                        'script_key': u(0xF4), 'tf_key': u(0xF8)})
        self._teamtypes = (len(self.plain), out)
        return [dict(x) for x in out]

    def _team_label_map(self):
        """`{小队键: "ID 名称"}` —— 给 `_arg_text(ptype=7)` 用（同样按明文长度缓存）。"""
        memo = getattr(self, '_teamlbl', None)
        if memo is not None and memo[0] == len(self.plain):
            return memo[1]
        out = {}
        try:
            for t in (self.team_types() or []):
                k = t.get('key')
                if k:
                    out[k] = ('%s %s' % (t.get('sid') or '', t.get('name') or '')).strip()
        except Exception:
            out = {}
        self._teamlbl = (len(self.plain), out)
        return out

    def clone_taskforce(self, units, tpl_sid=None, name=None, sid=None):
        """新建一个特遣部队。`units` = [(型号, 数量), ...]，最多 6 种。"""
        if not units:
            raise ValueError('特遣部队至少要有一个单位')
        if len(units) > 6:
            raise ValueError('特遣部队最多 6 种单位（本档结构如此），收到 %d 种' % len(units))
        tmap = {k: v['key'] for k, v in (self.unit_type_index() or {}).items() if v.get('key')}
        pairs = []
        for t, n in units:
            tk = tmap.get(str(t).upper())
            if not tk:
                raise RuntimeError('类型表里没有 %s 的类型键，写不了特遣部队' % t)
            pairs.append((int(n), tk))
        tfs = self.taskforces()
        tpl = None
        if tpl_sid:
            tpl = next((x for x in tfs if x['sid'].upper() == str(tpl_sid).upper()), None)
        if tpl is None:
            # 优先挑"已经含目标型号"的地图定义特遣（脚步/参数最接近），否则任意地图定义的，
            # 最后才退到 AI 生成的那批（§254：AI 的那批没有地编语义，只拿来抄布局）
            want = {str(t).upper() for t, _ in units}
            pool = [x for x in tfs if x.get('defined', True)] or tfs
            tpl = next((x for x in pool if any(u['type'].upper() in want for u in x['units'])), None)
        if tpl is None:
            tpl = (pool or tfs)[0]
        pos, idx = self._append_group(self.TF_CLS, self.TF_SIZE, tpl['off'], '特遣部队')
        pl = self.plain
        key = self._free_group_key([x['key'] for x in tfs])
        newsid = sid or self._next_sid()
        struct.pack_into('<I', pl, pos + 0x10, key)
        pl[pos + 56:pos + 64] = newsid.encode('ascii')[:8].ljust(8, b'\x00')
        nm = (name or ('刷-%s' % '+'.join('%s%d' % (t, n) for t, n in units)))[:47]
        pl[pos + 0x78:pos + 0x78 + 48] = b'\x00' * 48
        pl[pos + 0x78:pos + 0x78 + len(nm.encode('gbk', 'ignore'))] = nm.encode('gbk', 'ignore')
        struct.pack_into('<I', pl, pos + 0xAC, 0xFFFFFFFF)     # 分组 -1
        struct.pack_into('<I', pl, pos + 0xB0, len(pairs))     # 种类数
        for i in range(6):
            c, tk = pairs[i] if i < len(pairs) else (0, 0)
            struct.pack_into('<II', pl, pos + 0xB8 + 8 * i, c, tk)
        return {'off': pos, 'key': key, 'sid': newsid, 'name': nm, 'units': pairs,
                'defined': self.is_map_sid(newsid),      # ★§254：True = 地编里可见
                'template': tpl['sid'], 'template_key': tpl['key']}

    def clone_team(self, tf_key, tpl_sid=None, name=None, sid=None,
                   country_key=None, veteran=0, max_teams=1, script_key=None,
                   priority=None):
        """新建一个作战小队（引用 `tf_key` 那个特遣部队）。

        ★★§252.17（2026-10-24 定案）：**星级 = `+0xB4`**。
          旧代码把 `veteran` 写到 `+0xF0`，那是**另一个字段**（本档实测 0x2E 之类），
          所以"新建小队时指定星级"一直**静默无效**（`read_ai`/界面读的是 `+0xB4`，永远拿到 0/1）。
          已用玩家地编对话框对账：星级 3 / 优先级 5 / 最大 5 ↔ `+0xB4=3 / +0xC8=5 / +0xCC=5`。
        """
        teams = self.team_types()
        tpl = None
        if tpl_sid:
            tpl = next((x for x in teams if x['sid'].upper() == str(tpl_sid).upper()), None)
        if tpl is None:
            # 优先挑"特遣部队里含目标单位"的模板；再退而求其次挑引用同国家/同脚本的
            tpl = next((x for x in teams if x['tf_key'] == tf_key), None) or teams[0]
        pos, idx = self._append_group(self.TEAM_CLS, self.TEAM_SIZE, tpl['off'], '作战小队')
        pl = self.plain
        key = self._free_group_key([x['key'] for x in teams])
        newsid = sid or self._next_sid()
        struct.pack_into('<I', pl, pos + 0x10, key)
        pl[pos + 56:pos + 64] = newsid.encode('ascii')[:8].ljust(8, b'\x00')
        nm = (name or ('队-%s' % newsid))[:47]
        pl[pos + 0x78:pos + 0x78 + 48] = b'\x00' * 48
        pl[pos + 0x78:pos + 0x78 + len(nm.encode('gbk', 'ignore'))] = nm.encode('gbk', 'ignore')
        struct.pack_into('<I', pl, pos + 0xAC, idx)            # ★组内下标（必须=物理下标）
        struct.pack_into('<I', pl, pos + 0xCC, int(max_teams))
        if priority is not None:
            # 地编「优先级」——`team_types()` 口径 `+0xC8`（与 FA2 对话框 星级/优先级/最大 的顺序一致）
            struct.pack_into('<I', pl, pos + 0xC8, int(priority))
        # ★§252.17：星级是 **+0xB4**（旧代码写到 +0xF0 ⇒ 指定星级一直没生效）。
        #   FA2 的星级取值 1=新兵 / 2=老兵 / 3=精英；允许 0（=引擎默认）。
        struct.pack_into('<I', pl, pos + 0xB4, int(veteran))
        struct.pack_into('<I', pl, pos + 0xF8, tf_key)          # 特遣部队键
        if country_key:
            struct.pack_into('<I', pl, pos + 0xD8, country_key)
        # 脚本：`None` = 不传（沿用模板队的值）；**传 0 = 显式写"空脚本"**（§217 待验）
        if script_key is not None:
            struct.pack_into('<I', pl, pos + 0xF4, int(script_key) & 0xFFFFFFFF)
        return {'off': pos, 'key': key, 'sid': newsid, 'name': nm,
                'defined': self.is_map_sid(newsid),      # ★§254：True = 地编里可见
                'tf_key': tf_key, 'index': idx, 'template': tpl['sid'],
                'max': int(max_teams), 'veteran': int(veteran),
                'country_key': struct.unpack_from('<I', pl, pos + 0xD8)[0],
                'script_key': struct.unpack_from('<I', pl, pos + 0xF4)[0]}

    # ---------------- ★§253 单独新建「特遣 / 脚本 / 小队」+ 触发挂小队 ----------------
    def team_key_of(self, ident):
        """小队标识 → 小队对象键。`ident` 可以是键(int / '0x1A2B3C4D')、小队 ID('01000241')、名称。"""
        if ident is None:
            return None
        if isinstance(ident, int):
            return ident & 0xFFFFFFFF
        s = str(ident).strip()
        if not s:
            return None
        if s.lower().startswith('0x'):
            try:
                return int(s, 16) & 0xFFFFFFFF
            except ValueError:
                return None
        teams = self.team_types()
        for t in teams:
            if str(t['sid']).upper() == s.upper():
                return t['key']
        for t in teams:
            if str(t['name']).strip() == s:
                return t['key']
        for t in teams:
            if s.upper() in str(t['name']).upper():
                return t['key']
        return None

    def create_ai_team(self, units, name=None, sid=None, veteran=3, priority=None,
                       max_teams=5, house=None, script_entries=None, script_key=None,
                       tf_template=None, team_template=None, tf_name=None,
                       script_name=None):
        """★§253 新建「特遣部队 + （可选）动作脚本 + 作战小队」，**不建触发**。

        这是把昨天刷兵三件套（§217/§222）拆开单独用：
          · `units`   = `[(型号, 数量), …]`，最多 6 种（写进特遣部队）
          · `veteran` = 1 新兵 / 2 老兵 / 3 精英（写 `+0xB4`，与地编「星级」同口径）
          · `script_entries` = `[(动作, 参数), …]`；`None` 且 `script_key` 也为 None ⇒ 沿用模板
            （想要"原地待命"请显式 `script_key=0`）
        返回 `{'tf':…, 'script':…, 'team':…}`。
        """
        units = [(str(t).strip().upper(), int(n)) for t, n in (units or []) if int(n) > 0]
        if not units:
            raise ValueError('至少要有一个单位（型号, 数量）')
        warnings = []
        tf = self.clone_taskforce(units, tpl_sid=tf_template, name=tf_name)
        new_script = None
        if script_entries:
            new_script = self.clone_script(list(script_entries),
                                          name=(script_name or ('动作-%s' % name or ''))[:47])
            script_key = new_script['key']
        if team_template is None:
            same = [x for x in self.team_types() if x['tf_key'] == tf.get('template_key')]
            if same:
                team_template = same[0]['sid']
        try:
            ck = self.house_key_of(house or self.player_house())
        except Exception:
            ck = None
            warnings.append('认不出所属方，国家字段沿用模板')
        mt = int(max_teams)
        tm = self.clone_team(tf['key'], tpl_sid=team_template, name=name, sid=sid,
                            country_key=ck, veteran=int(veteran), max_teams=mt,
                            script_key=script_key)
        # 「出现于」路径点：与地编/§218 口径一致，先置 0（引擎读行为里的 `+0x58`）
        struct.pack_into('<I', self.plain, tm['off'] + 0xE8, 0)
        if priority is not None:
            # ★§252.17 已用 FA2 对话框对账定案：**优先级 = `+0xC8`、最大 = `+0xCC`**
            #   （星级3/优先级5/最大5 ↔ `+0xB4=3 / +0xC8=5 / +0xCC=5`）。
            #   旧代码这里写 `+0xCC`（写到了"最大"上），已改。
            struct.pack_into('<I', self.plain, tm['off'] + 0xC8, int(priority))
        self._invalidate_if_shifted()
        return {'tf': tf, 'script': new_script, 'team': tm, 'warnings': warnings}

    def attach_team_to_trigger(self, trigger_id, team, waypoint=None, cell=None,
                               action=80, reuse=True):
        """★§253 把一支作战小队挂到**已有触发**上（行为 80「援军」/107「超时空传送援军」）。

        · `team` 可以给小队键、小队 ID（如 `01000241`）或名称（走 `team_key_of`）
        · `reuse=True`：该触发已有 80/107 行为就**改写那条**（改小队/路径点），不新增；
          否则 `add_action()` 追加一条。
        · 行为布局（§218 实测，80/107 相同）：`+0x40` 类型、`+0x44` **小队键**、`+0x58` 路径点。
        """
        action = int(action)
        if action not in self.SPAWN_ACTIONS:
            raise ValueError('挂小队只支持行为 %s，收到 %r'
                             % ('/'.join(str(x) for x in self.SPAWN_ACTIONS), action))
        tkey = self.team_key_of(team)
        if not tkey:
            raise RuntimeError('认不出小队 %r（可用小队 ID / 名称 / 键）' % (team,))
        if waypoint is None:
            if cell is None:
                raise RuntimeError('要么给 waypoint（路径点号），要么给 cell=(x,y)')
            wp = self.ensure_waypoint_at(int(cell[0]), int(cell[1]))
            if wp is None:
                raise RuntimeError('格 %s 处建不了路径点（号段用满且无近点可复用）' % (cell,))
        else:
            wp = int(waypoint)
        acts = []
        try:
            acts = self.trigger_actions(trigger_id) or []
        except RuntimeError:
            # 该触发**还没有任何行为**（新建/空触发）→ 直接走"追加一条"
            acts = []
        target = next((a for a in acts if int(a.get('type') or 0) in self.SPAWN_ACTIONS), None) \
            if reuse else None
        if target is not None:
            pl = self.plain
            off = target['action_off']
            struct.pack_into('<I', pl, off + 0x44, tkey)      # 小队键
            struct.pack_into('<I', pl, off + 0x58, wp)        # 路径点
            struct.pack_into('<I', pl, off + 0x48, 0)         # §218：这槽恒 0
            struct.pack_into('<I', pl, off + 0x40, action)    # 顺便纠正类型
            self._invalidate_if_shifted()
            return {'mode': 'reuse', 'trigger_id': trigger_id, 'team_key': tkey,
                    'waypoint': wp, 'action': action, 'action_off': off}
        try:
            r = self.add_action(trigger_id, action, 0, waypoint=wp, team=tkey)
        except RuntimeError as e:
            if '找不到触发' in str(e):
                raise RuntimeError(
                    '触发 %s 没有运行期实例（只有定义，改不了）。'
                    '请在触发器页换一条可编辑的触发（灰/红那种不行）。' % trigger_id)
            raise
        return {'mode': 'add', 'trigger_id': trigger_id, 'team_key': tkey,
                'waypoint': wp, 'action': action, 'action_off': r.get('obj'),
                'action_key': r.get('key')}

    # ---------------- ★§258 作战小队全字段（仿地编对话框） ----------------
    # 记录 268B 的字段布局（与地编 INI 键的对应关系见 §235 / §252.17 / §258 对账）：
    #   名字 +0x78(48B GBK) / 分组 +0xB0 / 星级 +0xB4 / 优先级 +0xC8 / 最大 +0xCC
    #   心控结果 +0xD4（MindControlDecision，0~5，§265） / 国家 +0xD8(键) / 关联标签 +0xE4(键) / 出现于 +0xE8(路径点号)
    #   动作脚本 +0xF4(键) / 特遣部队 +0xF8(键)
    # 布尔标志一字节一个，见 `ai_objects.TEAM_FLAGS`（16 个，地编带 * 的 11 个都在里面）。
    TEAM_FIELD_OFF = {
        'group': 0xB0,
        'veteran': 0xB4,
        'priority': 0xC8,
        'max': 0xCC,
        'mind_control': 0xD4,
        'waypoint': 0xE8,
        # ★§259 定案：运输机起始 = TransportWaypoint = i32 `+0xEC`。
        #   证据（玩家 2026-10-25 给的 SAVE4AE1.SAV + 地编截图）：
        #     ① 地编里三支小队设成 98 / 0 / 99，存档 `+0xEC` 精确是 98 / 0 / 99；
        #     ② 老档（T85 系）125 支地图定义小队是 `+0xE8=0` 而 `+0xEC=-1` ⇒ **两列独立**，
        #        不是「出现于」的副本；
        #     ③ `-1` = 未设置（地编显示 None）；★`0` 是**合法**路径点（0 号），
        #        所以判空只能用 -1，绝不能用 0。
        'transport_waypoint': 0xEC,
    }
    TEAM_KEY_OFF = {
        'house': 0xD8,
        'tag': 0xE4,
        'script': 0xF4,
        'taskforce': 0xF8,
    }
    TEAM_NAME_OFF = 0x78
    TEAM_NAME_LEN = 48

    # ★§258：地编对话框里有、但**目前没有硬证据定位**的字段（不猜，一律灰显）
    TEAM_UNKNOWN_FIELDS = {
        'tech_level': ('科技等级', '0..10；本档 125 个小队全是 0 ⇒ 对账不出偏移'),
        # ★§261 「用于基地防御」**已定案**（`IsBaseDefense` = u8 `+0x10A`），移出本表
        # ★§263 「仅攻击敌方或敌对」**已定案**（`OnlyTargetHouseEnemy` = u8 `+0x10B`），移出本表
        # ★§260 「小队成员可被重组」**已定案**（`AreTeamMembersRecruitable` = u8 `+0x109`），移出本表
        'transports_return_on_unload': ('运输机下客后返回', '同上'),
        'ion_immune': ('无视离子风暴', '同上'),
        'para_drop_aircraft': ('运输机类型', '同上'),
        'loose_recruit_alt': ('解散重组', '同上'),
        # ★§258.6 玩家裁定：科技等级 / 运输机类型 / 无视离子风暴 / 运输机下客后返回 /
        #   解散重组(AreTeamMembersRecruitable) 「没什么用 / 不重要」⇒ 不做。
        # ★§259 「运输机起始」**已定案**（`transport_waypoint` = `+0xEC`），从这里移出去了。
    }

    def team_detail(self, ident):
        """读一个作战小队的**全部已知字段**（仿地编「作战小队」对话框）。

        `ident` = 键 / 小队 ID（`01000241`）/ 名称（走 `team_key_of`）。
        返回 dict；`flags` 是 16 个布尔（见 `ai_objects.TEAM_FLAGS`）。
        """
        key = ident if isinstance(ident, int) else self.team_key_of(ident)
        if not key:
            raise RuntimeError('认不出小队 %r（可用小队 ID / 名称 / 键）' % (ident,))
        rec = None
        for t in self.team_types():
            if t['key'] == key:
                rec = t
                break
        if rec is None:
            raise RuntimeError('小队键 %#x 不在本档' % key)
        pl = self.plain
        off = rec['off']
        import ai_objects as _aio
        flags = {}
        for foff, nm in _aio.TEAM_FLAGS.items():
            flags[nm] = pl[off + foff] != 0
        out = {'key': key, 'sid': rec['sid'], 'defined': rec.get('defined', True),
               'name': rec['name'],
               'group': struct.unpack_from('<i', pl, off + self.TEAM_FIELD_OFF['group'])[0],
               'veteran': struct.unpack_from('<i', pl, off + self.TEAM_FIELD_OFF['veteran'])[0],
               'priority': struct.unpack_from('<i', pl, off + self.TEAM_FIELD_OFF['priority'])[0],
               'max': struct.unpack_from('<i', pl, off + self.TEAM_FIELD_OFF['max'])[0],
               'mind_control': struct.unpack_from('<i', pl, off + self.TEAM_FIELD_OFF['mind_control'])[0],
               'waypoint': struct.unpack_from('<i', pl, off + self.TEAM_FIELD_OFF['waypoint'])[0],
               'transport_waypoint': struct.unpack_from(
                   '<i', pl, off + self.TEAM_FIELD_OFF['transport_waypoint'])[0],
               'flags': flags}
        for k, o in self.TEAM_KEY_OFF.items():
            out[k] = struct.unpack_from('<I', pl, off + o)[0]
        return out

    def set_team_field(self, ident, field, value):
        """改作战小队的一个字段（**只写已知偏移**；未知字段直接报错，绝不猜）。

        `field` 取 `TEAM_FIELD_OFF` / `TEAM_KEY_OFF` 的键，或 `name`，
        或布尔标志名（`Loadable` / `Reinforce` … 走 `ai_objects.set_team_flag`）。
        返回改后的 `team_detail()`。
        """
        import ai_objects as _aio
        key = ident if isinstance(ident, int) else self.team_key_of(ident)
        if not key:
            raise RuntimeError('认不出小队 %r' % (ident,))
        rec = next((t for t in self.team_types() if t['key'] == key), None)
        if rec is None:
            raise RuntimeError('小队键 %#x 不在本档' % key)
        if field in _aio.TEAM_FLAGS.values():
            _aio.set_team_flag(self, rec['sid'], field, bool(value))
            return self.team_detail(key)
        off = rec['off']
        pl = self.plain
        if field == 'name':
            nm = str(value)[:47].encode('gbk', 'ignore')[:self.TEAM_NAME_LEN - 1]
            pl[off + self.TEAM_NAME_OFF:off + self.TEAM_NAME_OFF + self.TEAM_NAME_LEN] = \
                b'\x00' * self.TEAM_NAME_LEN
            pl[off + self.TEAM_NAME_OFF:off + self.TEAM_NAME_OFF + len(nm)] = nm
        elif field in self.TEAM_FIELD_OFF:
            struct.pack_into('<i', pl, off + self.TEAM_FIELD_OFF[field], int(value))
        elif field in self.TEAM_KEY_OFF:
            struct.pack_into('<I', pl, off + self.TEAM_KEY_OFF[field],
                             int(value or 0) & 0xFFFFFFFF)
        elif field in self.TEAM_UNKNOWN_FIELDS:
            raise RuntimeError('%s：偏移还没定案（缺对照档），不敢写' % field)
        else:
            raise KeyError('未知小队字段 %r' % (field,))
        self._invalidate_if_shifted()
        return self.team_detail(key)

    def team_refs(self, ident):
        """「查找引用」：哪些触发用行为 80/107 引用了这支小队 → [{trigger_id, name, action, waypoint}]。"""
        key = ident if isinstance(ident, int) else self.team_key_of(ident)
        if not key:
            raise RuntimeError('认不出小队 %r' % (ident,))
        out = []
        names = {r.sid: r.name for r in self.records}
        for r in self.records:
            if r.kind != '触发':
                continue
            try:
                acts = self.trigger_actions(r.sid)
            except Exception:
                continue
            for a in acts:
                if a['type'] in self.SPAWN_ACTIONS and a.get('team') == key:
                    out.append({'trigger_id': r.sid, 'name': names.get(r.sid, ''),
                                'action': a['type'], 'waypoint': a.get('waypoint'),
                                'cell': a.get('cell')})
        return out

    def script_types(self):
        """列出全部动作脚本：[{off,key,sid,name,index,entries:[{action,arg,name}]}]（§222）。"""
        try:
            import ai_objects as _aio
            have = _aio.read_ai(self)
        except Exception:
            return []
        out = []
        for s in (have.get('scripts') or []):
            _sid = s['id']
            out.append({'off': s['off'], 'key': s.get('key'), 'sid': _sid,
                        'defined': self.is_map_sid(_sid),      # ★§254：False = AI 运行期生成
                        'name': s.get('name', ''), 'index': None,
                        'entries': s.get('entries') or []})
        # 组内下标（+0xAC）补上（read_ai 没解这个槽）
        try:
            g = self._obj_group(self.SCRIPT_CLS, self.SCRIPT_SIZE, '动作脚本')
            pl = self.plain
            for i, off in enumerate(g['offs']):
                for o in out:
                    if o['off'] == off:
                        o['index'] = struct.unpack_from('<I', pl, off + 0xAC)[0]
        except Exception:
            pass
        return out

    def clone_script(self, entries, tpl_sid=None, name=None, sid=None):
        """新建一段**动作脚本**（ScriptType 584B，§222）。

        `entries` = `[(action, arg), …]`，arg 是 32 位原样值
        （如 `(58, 1)` = 移动到己方建筑、`(5, 10)` = 警戒 10、`(6, 1)` = 转到行号 1）；
        每条 8B「动作, 参数」，最多 50 条。

        布局（SAVE0029 171 个真对象实测 + 与 Team/TF 同构）：
          `+0x10` 自键 / `+0x38` 8 位 ID / `+0x78` 名称 48B / `+0xAC` **组内下标** /
          `+0xB0` 标志（沿用模板）/ `+0xB4` 条数 / `+0xB8` 起条目区 ×50。
        ★克隆时必须清零**运行期残留**（`+0x28`/`+0x34`/`+0x40`/`+0x70`/`+0xA8`
          以及**超出条数的条目区**）—— 引擎跑过的脚本这些槽有指针/索引残留，
          不清会带着别人的运行期状态（与事件/行为的 blank_runtime 同一道理）。
        """
        if not entries:
            raise ValueError('脚本至少要有一条动作')
        if len(entries) > 50:
            raise ValueError('脚本最多 50 条动作（584B 结构如此），收到 %d 条' % len(entries))
        try:
            import ai_objects as _aio
            have = _aio.read_ai(self)
        except Exception:
            have = {'scripts': []}
        scs = have.get('scripts') or []
        if not scs:
            raise RuntimeError('这份存档里解析不出动作脚本对象，写不了新脚本')
        tpl = None
        if tpl_sid:
            tpl = next((x for x in scs if x['id'].upper() == str(tpl_sid).upper()), None)
        if tpl is None:
            # 优先挑"已含目标动作"的模板（字段最接近），否则第一个
            want = {int(a) for a, _b in entries}
            tpl = next((x for x in scs
                        if any(e['action'] in want for e in x['entries'])), None)
        if tpl is None:
            tpl = scs[0]
        pos, idx = self._append_group(self.SCRIPT_CLS, self.SCRIPT_SIZE, tpl['off'],
                                      '动作脚本')
        pl = self.plain
        # 新键：与脚本同区（0x134xxxxx），全档查重
        keys = [x['key'] for x in scs if x.get('key')]
        k = ((max(keys) + 0x1000) & 0xFFFFFFFF) if keys else 0x13400000
        while pl.find(struct.pack('<I', k)) >= 0:
            k = (k + 0x1000) & 0xFFFFFFFF
        key = k
        newsid = sid or self._next_sid()
        struct.pack_into('<I', pl, pos + 0x10, key)
        pl[pos + 56:pos + 64] = newsid.encode('ascii')[:8].ljust(8, b'\x00')
        nm = (name or ('脚本-%s' % newsid))[:47]
        pl[pos + 0x78:pos + 0x78 + 48] = b'\x00' * 48
        pl[pos + 0x78:pos + 0x78 + len(nm.encode('gbk', 'ignore'))] = \
            nm.encode('gbk', 'ignore')
        struct.pack_into('<I', pl, pos + 0xAC, idx)            # ★组内下标（必须=物理下标）
        struct.pack_into('<i', pl, pos + 0xB4, len(entries))   # 条数
        # 条目区整体先清零（抹掉模板残留 + 运行期指针），再写前 count 条
        pl[pos + 0xB8:pos + self.SCRIPT_SIZE] = b'\x00' * (self.SCRIPT_SIZE - 0xB8)
        for i, e in enumerate(entries):
            a, arg = int(e[0]), int(e[1])
            struct.pack_into('<ii', pl, pos + 0xB8 + 8 * i, a, arg & 0xFFFFFFFF)
        # 运行期残留槽清零（保留 +0x14..+0x24 类常量与 +0x74 常量）
        for off in (0x28, 0x34, 0x40, 0x70, 0xA8):
            struct.pack_into('<I', pl, pos + off, 0)
        self._invalidate_if_shifted()
        return {'off': pos, 'key': key, 'sid': newsid, 'name': nm,
                'defined': self.is_map_sid(newsid),      # ★§254：True = 地编里可见
                'index': idx, 'count': len(entries),
                'template': tpl['id'], 'entries': [(int(a), int(b) & 0xFFFFFFFF)
                                                    for a, b in entries]}

    # §218：小队法刷兵支持的两种「援军」行为。二者二进制布局**完全相同**
    #   （`+0x40` 类型、`+0x44` 小队键、`+0x58` 路径点，`+0x48` 恒 0），
    #   已用 SAVE0029 的 47 个 80 号 + 250 个 107 号真对象逐一核对。
    SPAWN_ACTIONS = (80, 107)
    # ★★§283.9：**小队型行为**（地编口径参数类型 7）= 4 建立 / 5 解散 / 7 援军 / 80 / 107。
    #   实测（Z17 档，386 支小队）：4/7 的小队键和 80/107 一样写在行为对象 **`+0x44`**，
    #   `+0x48` 恒 0 ⇒ 以前只认 SPAWN_ACTIONS，导致 4/7 读出来 team=0、界面上选不了小队。
    TEAM_ACTIONS = (4, 5, 7, 80, 107)

    @staticmethod
    def is_team_action(atype):
        """该行为的「参数」是不是**作战小队**（4/5/7/80/107）—— 小队键都在 `+0x44`。"""
        try:
            return int(atype) in SaveDoc.TEAM_ACTIONS or action_ptype(int(atype)) == 7
        except Exception:
            return False

    def spawn_units_by_trigger(self, type_name, count=1, cell=None, waypoint=None,
                               house=None, trigger_name=None, max_teams=1,
                               tf_template=None, team_template=None, script_key=None,
                               action=80, script_entries=None):
        """**让引擎自己刷兵**：特遣部队 + 作战小队 + 触发「13 流逝时间1秒 + 援军行为@路径点」。

        为什么需要它（§209）：`SPAWNS=` 单位（V3/无畏/航母）手写记录不显示；
        而这条路上单位是**引擎构造**的，所以完全绕开了那个坑。
        返回 {'tf','team','trigger_id','action','waypoint','cell','warnings'}。

        `action`（§218）：`80` = 援军(作战小队)[在路径点]；`107` = 超时空传送援军
        （带时空转移特效）。两者在存档里的布局一样：小队键 `+0x44`、路径点 `+0x58`。
        ★真地图口径（SAVE0029 实测）：地编**不**同步小队自己的「出现于」路径点
        （46/47 个 80 号、250/250 个 107 号的小队 `+0xE8` 都是 0），
        引擎读的是行为里的路径点 ⇒ 这里把新队 `+0xE8` 也置 0，与地编写的援军队完全一致
        （模板队的 -1 是"未设置"，不带进新队）。
        ★事件为什么用 13 而不是 8（§219.7，T75/T76/T77 三连不出兵的最后一个原因）：
          「任何事件」虽永真，但 TagNode 帧数=0 且未挂接建筑时**引擎不轮询** ⇒ 永不触发；
          13「流逝时间 1 秒」⇒ 帧数 15 ⇒ 进「定时触发器挂接表」⇒ 读档约 1 秒后出兵。
        """
        action = int(action)
        if action not in self.SPAWN_ACTIONS:
            raise ValueError('小队法刷兵只支持行为 %s（援军），收到 %r'
                             % ('/'.join(str(x) for x in self.SPAWN_ACTIONS), action))
        t = str(type_name).strip().upper()
        warnings = []
        # ★§222：给了 script_entries ⇒ 先建一段新脚本，小队挂它（而不是留空/沿用模板）
        new_script = None
        if script_entries:
            new_script = self.clone_script(list(script_entries),
                                           name=('刷%s动作' % t)[:16])
            script_key = new_script['key']
        tf = self.clone_taskforce([(t, int(count))], tpl_sid=tf_template)
        if team_template is None:
            # ★默认拿"引用同一个特遣模板"的那个小队当模板 —— 这样脚本(+0xF4)、国家(+0xD8)、
            #   优先级都跟你原图里那条能用的队一致，等于**复刻**它。
            same = [x for x in self.team_types() if x['tf_key'] == tf.get('template_key')]
            if same:
                team_template = same[0]['sid']
        ck = None
        # 没指定所属方就用玩家自己 —— 否则可能沿用"国家=0"的模板队，刷出来的单位没主人
        try:
            ck = self.house_key_of(house or self.player_house())
        except Exception:
            ck = None
        # 国家：优先用模板队自己的；给了 house 就改成 house 的键
        tm = self.clone_team(tf['key'], tpl_sid=team_template, country_key=ck,
                            veteran=0, max_teams=int(max_teams), script_key=script_key)
        # ★§218：小队「出现于」路径点 `+0xE8` 置 0 —— 与地编写援军队的习惯一致
        #   （SAVE0029 里被 80/107 引用的 297 个小队全是 0；引擎读的是行为里的 `+0x58`）。
        #   必须在 create_trigger 之前写：那之后插入的触发六件套会把小队组整体推移。
        struct.pack_into('<I', self.plain, tm['off'] + 0xE8, 0)
        wp = waypoint
        if wp is None:
            if not cell:
                raise RuntimeError('必须给 cell=(x,y) 或 waypoint=路径点号')
            wp = self.ensure_waypoint_at(int(cell[0]), int(cell[1]))
            if wp is None:
                raise RuntimeError('格 %s 处建不了路径点' % (cell,))
        nm = (trigger_name or ('刷%s%d' % (t, count)))[:16]
        # ★★§219.7：事件必须用 **13「流逝时间 1 秒」**，不能是 8「任何事件」。
        #   §181/2026-10-15 实测定案：「任何事件」永远为真，但 **TagNode 帧数=0 且
        #   未挂接建筑 ⇒ 引擎根本不轮询它 ⇒ 永不触发**（T75/T76/T77 三连不出兵
        #   的最后一个原因；序号撞号修完仍然不出，就是这条）。
        #   事件 13 + arg=1 ⇒ TagNode 帧数 = 1×15 = 15 > 0 ⇒ 自动登记
        #   「定时触发器挂接表」⇒ 读档约 1 秒后评估、立刻刷兵。
        #   （原图那条"事件 8 能刷兵"是因为它**开局就已执行过**，不是被轮询。）
        r = self.create_trigger(nm, 13, 1, action, 0)
        tid = r.get('trigger_id')
        # ★★`create_trigger()` 返回的 `ac_off` 在它自己后续插入对象之后**已经失效**
        #   （实测：按那个偏移去写，结果打到了别的行为对象上，读出来 type=107）。
        #   正确做法：建完之后按**触发器 ID 重新定位**行为对象，再写参数。
        acts = self.trigger_actions(tid)
        if not acts:
            raise RuntimeError('触发器 %s 建好了，但找不到它的行为对象' % tid)
        ac = acts[0]['action_off']
        pl = self.plain
        # ★§219 护栏：写参数前必须确认「按序号查到的行为」就是「实例指针链指向的行为」。
        #   2026-10-23 的 bug（玩家实测刷兵没出来）就是序号撞号让 trigger_actions
        #   定位到**别人的旧行为**，参数写错对象（还把旧行为 type 107 改成 80）。
        #   键对不上就拒绝写 —— 宁可报错，不写坏档。
        _inst = self.trigger_objects(tid).get('inst')
        if _inst is not None:
            _head = struct.unpack_from('<I', pl, _inst + 196)[0]
            _got = struct.unpack_from('<I', pl, ac + 16)[0]
            if _head != _got:
                raise RuntimeError(
                    '定位校验失败：触发器 %s 实例指向的行为键是 %#x，按序号查到的是 %#x'
                    '（序号撞号？）—— 已拒绝写入，避免改错对象' % (tid, _head, _got))
        n0 = struct.unpack_from('<I', pl, ac + 0x40)[0]
        # ★行为 80/107 的两个参数：小队键在 +0x44（不是工具默认的 +0x48！），路径点在 +0x58
        #   （§218：107 与 80 布局相同，SAVE0029 的 250 个真 107 对象全是这个模式）
        struct.pack_into('<I', pl, ac + 0x44, tm['key'])
        struct.pack_into('<I', pl, ac + 0x58, int(wp))
        if n0 != action:
            struct.pack_into('<I', pl, ac + 0x40, action)
            warnings.append('行为类型原本写成了 %d，已改回 %d' % (n0, action))
        self._invalidate_if_shifted()
        return {'tf': tf, 'team': tm, 'trigger_id': r.get('trigger_id'),
                'action': action, 'action_off': ac, 'waypoint': wp,
                'script': new_script,
                'cell': self.waypoint_cell(int(wp)), 'warnings': warnings}

    def import_unit_from(self, ref, type_name, cx, cy, house=None, veterancy=None,
                         slots='safe', exact_cell=True, auto_nudge=False):
        """**直接写入法**：把 `ref`（另一份存档的路径或 SaveDoc）里一台 `type_name` 真机
        的记录内容搬进本档，只保留本档脚手架的身份字段。

        为什么要它（2026-10-23 §209/§211）：克隆法只能拿**捐赠型号**的骨架去改类型键，
        机体的整套状态（高度 `+0xB8`/`+0x270`、`+0x10C`、伴随段参数…）都还是捐赠者的；
        跨型号基洛夫因此"高度只有 832、卡在地上/只看得见徽章"。直接写入法从**真机**抄内容，
        就没这个问题 —— 与建筑那边"克隆法两天没通、直接写入一小时通了"是同一个道理。

        实现：① 先用克隆法在本档搭好脚手架（键/两张指针表/格占用/阵营表/所属方都正确）；
              ② 再把记录里**除身份/指针/坐标以外**的所有 dword 从真机抄过来。
        """
        if not isinstance(ref, SaveDoc):
            ref = SaveDoc(ref)
        t = str(type_name).strip().upper()
        cands = [r for r in ref.unit_records() if r['type'].upper() == t]
        if not cands:
            raise UnitUnavailable('参考档里没有 %s 的真机 ⇒ 没法直接写入' % t)
        rr = self._pick_registered(cands) or cands[0]
        # 脚手架按本档"能拿到的同大类"自动挑（本档可能根本没有该型号），内容随后整体替换
        info = self.add_unit_by_clone(t, cx, cy, house=house, veterancy=veterancy,
                                      exact_cell=exact_cell, auto_nudge=auto_nudge)
        info['warnings'] = [w for w in info.get('warnings') or [] if '隐形' not in w]
        rec = [r for r in self.unit_records() if r['key'] == info['key']][0]
        if rec['size'] != rr['size'] or rec['comp_size'] != rr['comp_size']:
            raise UnitUnavailable('参考档的 %s 记录长度（%d+%d）与本档脚手架（%d+%d）不一致'
                                  % (t, rr['size'], rr['comp_size'], rec['size'], rec['comp_size']))
        # ★★2026-10-23 §212（玩家实测 T67 读档弹框）：**不能整条抄**。
        #   真机的记录里有 72 个"实例/指令"槽（目的地、任务状态…），抄过来会让单位
        #   一边飞一边找目标，甚至任务状态与目标指针不一致 ⇒ 读档崩溃。
        #   正确口径：只抄**同型号各实例之间完全一致**的槽（= 型号常量，实测 ZEP 有 550 个，
        #   含 `+0x270` 类型高度、`+0x51C`、`+0x940..0x958` 机体参数、`+0x6D8` 类型键）。
        # `slots='safe'`（默认）：只抄"机体参数"这几个槽 —— 最小改动，先保证不崩。
        #   实测 7 架真基洛夫之间这些槽完全一致：`+0x270` 类型高度(1248)、`+0x51C`、`+0x940..+0x958`。
        # `slots='full'`：抄全部「档内各实例都相同」的 dword（实测 260~310 个，旧注释写 540）。
        # ★★2026-10-24 §234：旧 full 会弹框，根因找到了（`_probe_540.py` 跨档比对）：
        #   "档内各实例都相同" ≠ "型号常量"，里面混了两类**不能抄**的东西：
        #     ① **键**（0x01000000..0x17FFFFFF，典型 `+0x6D8` 类型键 / `+0x6D4`）——
        #        同一型号在两份存档里的键**不同**（实测 GGI 0x0EF43FD0 vs 0x14F4B7B8），
        #        抄过去目标档没有这个键 ⇒ 引擎按键解引用 ⇒ **读档弹框**。
        #        脚手架自己的类型键本来就是对的，根本不用抄。
        #     ② **存档级全局指针**（典型 `+0x1BC/+0x1C8/+0x240 = 0x0017AD12`）——
        #        在参考档里 **1774/1775 个单位**（不分型号）都是这个值 ⇒ 它跟型号无关，
        #        是本次运行/本档的全局状态；抄进别的档 = 写一个野指针。
        #   ⇒ full 的判据改成「档内一致 **且** 跨型号不通用 **且** 不是键」。
        SAFE_SLOTS = [0x270, 0x51C, 0x940, 0x944, 0x948, 0x94C, 0x954, 0x958]
        insts = [r for r in ref.unit_records() if r['type'].upper() == t]
        same_size = [r for r in insts
                     if r['size'] == rr['size'] and r['comp_size'] == rr['comp_size']]
        n_all = rr['size'] + rr['comp_size']
        blobs = [bytes(ref.plain[x['off']:x['off'] + n_all]) for x in same_size]

        def universal_slots(doc):
            """该档「与型号无关」的槽 → {偏移: 4 字节值}。

            判据：**拿别的型号**来比 —— 同记录长度、但类型 ≠ 当前型号的对象里，
            ≥30%（且至少 3 个）在同一偏移取同一个**非零值**。
            这种值不分型号（实测 `+0x1BC/+0x1C8/+0x240` 在玩家档里 1774/1775 个单位
            都是 `0x0017AD12`）⇒ 是**存档级全局状态 / 全局指针**，不是型号常量。
            零值不算（0 = 默认/空，任何档都一堆，判它没意义）。

            ★★2026-10-24 修正（自检实踩）：**不能拿"同长度全部对象"当分母**。
              DISK 那一档长度（2489）在本档里只有 DISK 自己 ⇒ 任何型号常量都会
              100% 一致 ⇒ 全被判成"全局"跳过 ⇒ `full` 退化成"什么都不抄"
              （实测：full 导入的结果与纯克隆逐字段只差身份/坐标，等于白导）。
              分母只留**别的型号**，型号常量/turbo 全局才分得开。
            """
            probe = [x for x in doc.unit_records()
                     if x['size'] + x['comp_size'] == n_all
                     and x['type'].upper() != t.upper()]
            out = {}
            if len(probe) < 3:
                return out
            need = max(3, int(len(probe) * 0.3))
            for o in range(0, n_all - 3, 4):
                cnt = collections.Counter(
                    bytes(doc.plain[x['off'] + o:x['off'] + o + 4]) for x in probe)
                b, c = cnt.most_common(1)[0]
                if c >= need and b != b'\x00' * 4:
                    out[o] = b
            return out

        # 参考档的全局槽：一个都不抄（抄 `0x0017AD12` = 写野指针进目标档）
        glob_vals = universal_slots(ref) if slots != 'safe' else {}
        if slots == 'safe':
            const = [o for o in SAFE_SLOTS if o + 4 <= n_all]
        elif len(blobs) >= 2:
            const = [o for o in range(0, n_all - 3, 4)
                     if len({b[o:o + 4] for b in blobs}) == 1]
        else:
            # 参考档里只有 1 架 ⇒ 没有"跨实例比较"的依据，退回按块白名单（保守）
            const = [o for o in range(0, min(n_all, 0x960) - 3, 4)]
        keep = set(self.IMPORT_KEEP_LOCAL)
        # ★类型键/次类型键：脚手架已经是目标档的正确值，绝不能被参考档覆盖
        keep |= {0x6D4, 0x6D8}
        # ★目标档自己的全局槽也要保住：参考档这些槽多半是 0，抄过来会把目标档的
        #   全局值（新单位从同型号脚手架继承的正确值）冲成 0 —— 等于制造"跟同伴有差异"的怪单位。
        if slots != 'safe':
            keep |= set(universal_slots(self))
        n = 0
        skipped_ptr = 0
        skipped_glob = 0
        for o in const:
            if o in keep:
                continue
            v = blobs[0][o:o + 4]
            iv = struct.unpack('<I', v)[0]
            # ① 键一律不抄（§234）：跨档键不同，抄了引擎解引用到不存在的对象
            if 0x01000000 <= iv <= 0x17FFFFFF:
                skipped_ptr += 1
                continue
            # ② 参考档里"跨型号通用"的全局状态（如 +0x1BC=0x0017AD12）不抄
            if glob_vals.get(o) == v:
                skipped_glob += 1
                continue
            self.plain[rec['off'] + o:rec['off'] + o + 4] = v
            n += 1
        # 抄完再压一次「任务整组 = 空闲态 + 空中单位起在类型高度」（§252：
        # 目的地/指令组不再清 0 —— 清 0 = 目的地 (0,0) = 往地图角飞 + 空指令对象打掉弹框）
        rec_xy = (rec.get('x'), rec.get('y'))
        self._apply_idle_order_state(self.plain, self.plain, rec['off'],
                                     rec_xy[0], rec_xy[1])
        if info.get('loco') in ('Fly', 'Aircraft'):
            alt = struct.unpack_from('<I', self.plain, rec['off'] + 0x270)[0]
            if alt:
                self.plain[rec['off'] + 0xB8:rec['off'] + 0xB8 + 4] = struct.pack('<I', alt)
        # ★2026-10-24：`veterancy` 必须**在抄完之后**再写一次。
        #   等级 `+0x150` 属于"同型号各实例一致"的槽（参考档里可能全是新兵）
        #   ⇒ full 抄写会把它覆盖回参考档的值，调用方传的 veterancy 就白传了
        #   （自检实踩：传 2 进来，落地仍是 0）。
        # ★★§270（2026-10-25）：**只写 `+0x150`，不写 `+0x164`** ——
        #   玩家三档实测（同一台 E1 在游戏里升到 0/1/2 级）证明记录里只有 `+0x150` 变，
        #   `+0x164` 三份都是 2.0f；旧代码写 float(2-vet) 会让新单位显示新兵。
        if veterancy is not None:
            vv = int(veterancy)
            if vv in (0, 1, 2):
                struct.pack_into('<i', self.plain, rec['off'] + 0x150, vv)
                info = dict(info)
                info['veterancy'] = vv
        for attr in ('_urecs', '_uoffs', '_occ', '_blds'):
            if hasattr(self, attr):
                setattr(self, attr, None)
        info = dict(info)
        info['imported'] = n
        info['skipped_ptr'] = skipped_ptr
        info['skipped_glob'] = skipped_glob
        info['slots'] = slots
        info['ref'] = getattr(ref, 'path', '?')
        info['ref_key'] = rr['key']
        return info

    def ensure_attach_node(self, sid, ticks=0):
        """给「有标签对象、却没有 CellTag 节点」的触发**补建挂接节点**（TagNode + CellTag）。

        ★2026-10-23 §203（玩家实测驱动）：挂接页原来对这类触发只会说"已执行过、不能挂"，
          但玩家指出「触发页显示它是好的」。查清后的真相：
            · 这类触发的**定义完好**（实例/事件/行为都在），只是**存档里没有运行期节点**；
            · 没有 CellTag ⇒ 引擎根本不会评估它（`0x6E53A0` 只遍历 CellTag 的实例链）；
            · 到底"执行过"还是"从来没挂上"，存档字节分不清 —— 所以**别断言**，给用户重建的机会。
        `ticks`：仅计时型（首个事件是 13）需要，`秒 × 15`；回调型（被攻击/被摧毁…）传 0。
        返回 {'created','node','node_key','cell','cell_key','ticks'}。
        """
        o = self.trigger_objects(sid)
        if not o.get('tag_obj'):
            raise RuntimeError('这条触发连标签对象都没有（只有定义）⇒ 不能补建挂接节点。\n'
                               '需要先在「触发」页用「➕ 新建触发」把它重新建一条。')
        if o.get('cell') is not None:
            return {'created': False, 'node': o['cell'], 'node_key': None,
                    'cell': o['cell'], 'cell_key': o['cell_key'], 'ticks': None}
        grps = self._newtrig_groups()
        node_pos, cell_pos = grps.get('node'), grps.get('cell')
        if not node_pos or not cell_pos:
            raise RuntimeError('这份存档里没有 TagNode/CellTag 对象组（老格式或未建集群）'
                               '⇒ 暂不支持补建挂接节点。')
        pl = bytearray(self.plain)

        def patch(buf, off, data):
            buf[off:off + len(data)] = data

        u32v = lambda v: struct.pack('<I', v & 0xFFFFFFFF)
        used = []

        def fresh(keys):
            v = ((max(keys) + 0x1000) & 0xFFFFFFFF) if keys else NODEGRP_BASE_KEY
            while pl.find(u32v(v)) >= 0 or v in used:
                v = (v + 0x1000) & 0xFFFFFFFF
            used.append(v)
            return v

        K_NODE = fresh([int.from_bytes(pl[p + 16:p + 20], 'little') for p in node_pos])
        K_CELL = fresh([int.from_bytes(pl[p + 16:p + 20], 'little') for p in cell_pos
                        if int.from_bytes(pl[p + 16:p + 20], 'little') != K_NODE])
        tpl_node = bytes(pl[node_pos[-1]:node_pos[-1] + TRIG_NODE_SIZE])
        tpl_cell = bytes(pl[cell_pos[-1]:cell_pos[-1] + TRIG_CELL_SIZE])
        nb = bytearray(TRIG_NODE_SIZE)
        patch(nb, 0, TRIG_NODE_GUID)
        patch(nb, 16, u32v(K_NODE))
        patch(nb, 20, tpl_node[20:36])            # 类常量（跨档恒定）
        patch(nb, 36, b'\xff' * 4)
        patch(nb, NODE_TRIGKEY_OFF, u32v(o['inst_key']))
        patch(nb, 76, u32v(NODE_SPARE76))
        patch(nb, NODE_TICKS_OFF, u32v(int(ticks)))
        patch(nb, NODE_FLAG_OFF, u32v(1))         # 启用
        cb = bytearray(TRIG_CELL_SIZE)
        patch(cb, 0, TRIG_CELLGUID)
        patch(cb, 16, u32v(K_CELL))
        patch(cb, 20, tpl_cell[20:36])
        patch(cb, 36, b'\xff' * 4)
        patch(cb, CELL_TAGKEY_OFF, u32v(o['tag_key']))
        patch(cb, CELL_NODEKEY_OFF, u32v(K_NODE))
        # +0x40 挂接数保持 0（调用方随后 attach 时会 +1）
        ins = [(node_pos[-1] + TRIG_NODE_SIZE, bytes(nb)),
               (cell_pos[-1] + TRIG_CELL_SIZE, bytes(cb))]
        for pos, data in sorted(ins, key=lambda x: -x[0]):
            pl[pos:pos] = data
        # 组计数 +1（插入后重新定位组首对象）
        for guid in (TRIG_NODE_GUID, TRIG_CELLGUID):
            p0 = pl.find(guid)
            if p0 >= 4:
                old = int.from_bytes(pl[p0 - 4:p0], 'little')
                patch(pl, p0 - 4, u32v(old + 1))
        self.plain = pl
        for attr in ('_objs', '_events', '_actions', '_ticks', '_celltags', '_ctimerlist',
                     '_instmaps', '_tagobjs', '_tagrecs', '_blds', '_house_econ',
                     '_custom_houses', '_house_keys_memo', '_houses_memo'):
            if hasattr(self, attr):
                setattr(self, attr, None)
        o2 = self.trigger_objects(sid)
        return {'created': True, 'node': o2.get('cell'), 'node_key': K_NODE,
                'cell': o2.get('cell'), 'cell_key': K_CELL, 'ticks': int(ticks)}

    def attach_trigger_to_building(self, node_off, field_off):
        """把 node 的标签挂到指定建筑（字段写 X + 计数+1）。返回字段旧值。"""
        nd = [n for n in self.celltag_nodes() if n['node'] == node_off]
        if not nd:
            raise RuntimeError('CellTag 节点未找到（存档已变化？请重新扫描）')
        nd = nd[0]
        old = struct.unpack_from('<I', self.plain, field_off)[0]
        if old == nd['x']:
            raise RuntimeError('该建筑已挂此触发，无需重复')
        if old != 0:
            raise RuntimeError('该建筑已挂其它触发（须先解除旧挂接）')
        self.plain[field_off:field_off + 4] = struct.pack('<I', nd['x'])
        self.plain[nd['count_pos']:nd['count_pos'] + 4] = \
            struct.pack('<I', nd['count'] + 1)
        self.pending_attach.append((field_off, old))
        self._celltags = None
        # ★§202：`buildings()` 有缓存，不改这里 ⇒ 挂上后列表里那栋的挂接键还是 0
        #   （玩家会以为没挂成功；自检也正是这么抓到它的）
        self._blds = None
        return old

    def celltag_persist(self, node_off):
        """该 CellTag 指向的**标签持久性**（0 一次性·任一挂载 / 1 一次性·挂载清空 / 2 可重复）。

        用途：只剩 1 个挂载时提醒"计数归 0 会不会直接触发"——只有 **持久 1** 才把
        "挂载清空"当执行条件；持久 0 没有对象就永远不会满足，解除到 0 是安全的。
        """
        nd = [n for n in self.celltag_nodes() if n['node'] == node_off]
        if not nd:
            return None
        k_tag = struct.unpack_from('<I', self.plain, nd[0]['node'] + 56)[0]
        off = self._tag_objs_by_key().get(k_tag)
        if off is None:
            return None
        try:
            return struct.unpack_from('<i', self.plain, off + 0x9C)[0]
        except Exception:
            return None

    def detach_building(self, node_off, field_off, allow_last=True):
        """解除挂接（字段清 0 + 计数−1）。返回字段旧值。

        ★2026-10-23 §207（玩家定案）：
          · 旧实现 `count<=1` 一律拒绝，理由"怕留下空挂接节点" —— **这个理由不成立**：
            原版 `ALL01UMD.sav` 78 个节点里本来就有 2 个 `count=0`（引擎完全接受）。
          · 挂在建筑上的触发，**解除之后就没有任何条件能启动它** ⇒ 不需要"顺手禁用"，
            默认**只做解除这一件事**（`allow_last=True`）：这样
            ①那栋建筑能马上挂到别的触发（挂接键已清 0，出现在 ③）；
            ②那条触发也能挂到别的建筑（节点还在、计数 0，①里仍可选）。
          · `allow_last=False` 只是留给想恢复旧"保护"语义的调用方。
        """
        nd = [n for n in self.celltag_nodes() if n['node'] == node_off]
        if not nd:
            raise RuntimeError('CellTag 节点未找到（存档已变化？请重新扫描）')
        nd = nd[0]
        old = struct.unpack_from('<I', self.plain, field_off)[0]
        if old != nd['x']:
            raise RuntimeError('该建筑未挂此标签，无法解除')
        if nd['count'] <= 1 and not allow_last:
            # 只有显式要旧语义的调用方才会走到这里（GUI 默认走 allow_last=True）
            raise LastAttachRefused(node_off, nd['count'], self.celltag_persist(node_off))
        self.plain[field_off:field_off + 4] = b'\x00' * 4
        self.plain[nd['count_pos']:nd['count_pos'] + 4] = \
            struct.pack('<I', nd['count'] - 1)
        self.pending_attach.append((field_off, old))
        self._celltags = None
        self._blds = None
        return old

    def free_attach_slot(self, cx, cy, mode='zero'):
        """给 (cx,cy) 那栋建筑**找一个"可以写 CellTag 键"的字段位置** → `(字段偏移|None, 说明)`。

        FA2 里一栋建筑只能挂一个标签，所以这里要处理"已经挂在别的触发上"的情况：
          * `mode='zero'`（默认，保守）⇒ 已挂接就**拒绝**，说明里带上旧 CellTag 键，让玩家自己决定
          * `mode='force'` ⇒ 先把旧挂接**解掉**（旧 CellTag 计数 −1），再返回字段
            ⚠ 旧标签只剩这一栋时默认也能解除（§207：解除后该标签没有任何启动条件）——
              那种情况请改用"禁用触发"，别硬摘
          * `mode='any'` ⇒ 不看现值，直接返回字段（调用方自己负责计数一致性）
        返回 `(field_off, None)` 表示可用；`(None, 原因)` 表示不可用。
        """
        x, y = int(cx), int(cy)
        f, err = self.locate_building_field(x, y, mode='zero')
        if f is not None:
            return f, None
        # ★注意 mode 默认就是 'zero'，想看"已挂接的那处"必须显式传 'any'
        f2, err2 = self.locate_building_field(x, y, mode='any')
        if f2 is None:
            return None, err2 or err or ('格(%d,%d) 未定位到建筑' % (x, y))
        old = struct.unpack_from('<I', self.plain, f2)[0]
        if mode == 'any':
            return f2, None
        if mode != 'force':
            return None, ('格(%d,%d) 已经挂在 CellTag %08X 上（另有触发在用它）'
                          '—— 想改挂请用 force，或先在「挂接」页解除' % (x, y, old))
        nd = next((n for n in self.celltag_nodes() if n['x'] == old), None)
        if nd is None:
            return None, ('格(%d,%d) 挂着 CellTag %08X，但档里找不到对应节点'
                          '（可能是别的程序写的）—— 已拒绝' % (x, y, old))
        try:
            self.detach_building(nd['node'], f2)
        except Exception as e:
            return None, '格(%d,%d) 解挂失败：%s' % (x, y, e)
        return f2, None

    # ------------------------------------------------------------------ #
    # 建筑（2026-10-15 新解：对象定长 1860B；+0x4EC = 类型对象自键）
    #   类型对象 = [16B 类GUID（尾 8 字节恒为 aca4-006008055bb5）][+8 自键][+48 "类型ID\0"]
    #   建筑对象 = +0 挂接CellTag键 / +40 所属方指针 / +56,+60 耐久 / +104,+108 锚点像素坐标
    #             （+544,+548 坐标副本）/ +0x4EC 类型键
    #   坐标语义 = 格 × 256 + 128；**地图坐标是菱形(iso)系**（Size=0,0,W,H 的图坐标到 2W/2H）
    # ------------------------------------------------------------------ #
    BLD_SIZE = 1860
    # ★★记录长度是**类型特有**的（2026-10-16 定案）：多数类型 1860（载荷 1844），
    #   个别类型（GAAIRC / AMRADR 等）载荷 1856 ⇒ 记录 **1872**。
    #   建筑组**后面紧跟**一条固定结构：`0c 00 00 00` + GUID `d9338bae-1c06-11d2-aca4-008008055bb5`
    #   （四个母本实测全部一致）⇒ **组尾 = 该结构的位置**，不能一律按 `hs[-1] + 1860` 算，
    #   否则末栋是"长记录"时会把新记录插进它的尾部 12 字节里（2026-10-16 弹框真因之一）。
    BLD_TAIL_MARK = b'\x0c\x00\x00\x00' + bytes.fromhex('d9338bae1c06d211aca4006008055bb5')
    COMP_SIZE = 476          # 建筑伴随对象大小（引擎放建筑时新建，实测）
    # ★★伴随对象（2026-10-16 重新定案）：
    #   · 它是**登记在 表0/表1 里的独立对象**（建筑记录的 +1388/+1392/+1396/+1400 是
    #     "伴随对象键"列表，实测一栋可有 1~4 个）；
    #   · 本档伴随对象是 **476B 等距的连续数组**（母本 16 个 / 外挂放置 17 个，间距恒 476）；
    #   · 类 GUID `c32d270e-…` 以 **`c3 2d 27 0e`（Data1 大端写法）** 存在 +0x10，自身键在 +0x20，
    #     格×256 在 +0xC0/+0xC4；
    #   · 逐 dword 统计（母本 16 个 / 外挂 17 个）：**恒定 76~78 个字段**（类常量），变动 41~43 个
    #     —— **连引擎自己新建的那一个也带着临时指针（0x02CAxxxx…）** ⇒ 加载器不依赖变动字段。
    COMP_MARK = bytes.fromhex('c32d270e')   # 类 GUID 前 4 字节（实测存法）
    COMP_KEY_OFF = 0x20
    COMP_CELLX_OFF = 0xC0
    COMP_CELLY_OFF = 0xC4
    # ★★建筑记录里唯一的"自我引用"字段（2026-10-16 逐 dword 扫描定案）：
    #   载荷 +264 恒等于 **自身键 + 0x9C**（母本 11/11、外挂 12/12、手放 12/12）。
    #   克隆模板时留着模板的值 ⇒ 指向模板建筑的内部 ⇒ 注入对象与模板共享自身内存。
    BLD_SELFREF_OFF = 264
    BLD_SELFREF_DELTA = 0x9C
    # ★★★格子占用表（2026-10-16 定案，两档 22 栋 22/22 全中）
    #   存档里有一张 **格 → 占用对象键** 的表：格条目 **348 字节等距**，
    #   格偏移 = base + 348 × (80·cx + 79·cy)；占用对象的键写在 **格+0**，
    #   格 **−8** 处写 1（占用标记）。建筑占格 = {(cx+i, cy+j)}：
    #   i < W, j < H，WxH 取自 artmd.ini 的 `Foundation=`。
    #   ★**缺这张表 = 建筑能显示、却点不中/打不着**（鼠标拾取走格子，不走"类型→art"）。
    CELL_STRIDE = 348
    CELL_XK = 80
    CELL_YK = 79
    CELL_FLAG_OFF = -8
    CELL_FLAG2_OFF = 0x1C
    # ★★★2026-10-18 修正：上面 `CELL_XK/YK` 只在 **80×80 的小图**上成立。
    #   实测大任务图（203×295）的格索引是 `180·x + 179·y − 32400`，
    #   与 `80/79` 完全不符 ⇒ `_cell_base()` 返回 None ⇒ 删除建筑时"清格"整步被静默跳过
    #   （这正是大图删除建筑弹框、小图却正常的真正原因）。
    #   ⇒ 现在改用**与图尺寸无关**的定位：格对象本身是 348B 定长槽，槽 +0 是类 GUID，
    #     连续排布；槽内 +0x38 = (x,y) u16、+0xF8 = 占用对象键。
    #   槽内需要清成"空"的 4 个字段（逐字段对比"未卖电厂→卖掉电厂"得到）：
    CELL_STRUCT_GUID = bytes.fromhex('ce99bfc18c1ad2118175006008055bb5')
    CELL_STRUCT_STRIDE = 348
    CELL_STRUCT_XY = 0x38
    CELL_STRUCT_OCC = 0xF8
    CELL_STRUCT_CLEAR = (0xF0, 0xF8, 0x114, 0x138)
    # ★2026-10-18 玩家经验：FA2 虽然支持到 256×256，但**所有任务都被限制在 200×200 以内**，
    #   从业没见过更大的任务图 ⇒ 读出的尺寸只要超过 200×200，基本就是读错了（口径不对/字段偏移错）。
    MAP_TILE_MAX = 200
    _CELL_STRUCT_MIN = 64          # 少于这么多槽就不认（防止误认单条 GUID）
    # ★2026-10-28（审查 P0-4）：art 同样不再写死开发者路径 —— 主路径走
    #   `_resolve_rule_files()` 里 game_ini 找到的那一份；这份只作兜底。
    _LEGACY_ART_PATHS = [
        r'E:\YURI\artmd.ini',
        r'E:\YURI\artmd - 原版副本.ini',
        r'C:\Users\zhao5\Desktop\WorkBuddy\红警MOD制作素材工具与教程\artmd.ini',
    ]
    _foundation_cache = None
    BLD_TYPEKEY_OFF = 0x4EC
    BLD_HP_OFF = 56
    # ★★★★★ 2026-10-17 定案：**所属方（House）字段 = 记录载荷 +336**（+544 是同一份键的第二副本）。
    #   证据：大任务档 1143 栋 / 12 阵营 —— +336 有 8 种取值且**全部是本档 House 对象的键**
    #   （逐栋核对）；母本 11 栋全部 = 玩家 House 键；**伴随对象里 0/637 个含 House 键**
    #   ⇒ 改所属方只需改建筑记录的这两处。写一定要**两处一起写**。
    #   ⚠ 旧常量 40 是错的（它落在载荷 +96 = 类常量 0x0087E294 上，从不携带信息）。
    #   ⚠ 本类里有两套帧：`g+16` 帧（= 载荷帧，remove_building/bld_rows 用）
    #     与 `buildings()` 的 b 帧（b = 载荷 + 56，由 COORD_RE 命中位置 − back 反推）。
    BLD_OWNER_OFF = 336           # 载荷帧：所属方 House 键
    BLD_OWNER2_OFF = 544          # 载荷帧：同一键的第二副本
    BLD_HOUSE_OFF = 280           # b 帧里的同一字段（= 336 − 56），供 `buildings()['house']` 用
    # ★★★★★ 2026-10-17 补：**载荷 +1344 = 该栋主人的「运行期阵营序号」（小整数）**。
    #   发现法 = 在 1143 栋 / 9 阵营的大任务档里对**每个偏移**做「取值 ↔ 阵营」双射穷举：
    #   只有两个偏移全档双射 —— **+336（House 键）与 +1344（序号）**。
    #   实测映射：Americans 0 / Alliance 1 / French 2 / Germans 3 / British 4 / Africans 5 /
    #   Arabs 6 / Confederation 7 / Russians 8 / YuriCountry 9 / **Neutral 12**（两档一致）。
    #   ⚠ House 对象自己的 +68 是「类型序」（Neutral = 10），**不等于**这个运行期序号，
    #     House 对象里也**没有**存这个序号（0x1200 内全扫过）；档里也没有"12 键成排的阵营数组"。
    #   ⇒ 只能「从本档已有建筑反解（多数票），否则退回 HOUSE_RUN_INDEX 表」——见 `house_run_index()`。
    BLD_OWNER_IDX_OFF = 1344      # 载荷帧：主人阵营的运行期序号（b 帧 = 1400）
    BLD_COORD_OFF = 104
    BLD_COORD2_OFF = 544          # 坐标第二副本（与 +104 相差 440）
    # ★★★★★ 2026-10-22 §169：**建筑「生长动画没播完」的存档指纹**
    #   现象：刚放下（或触发法新建）的建筑会播 Buildup（从地面长出来），此时存档 ⇒
    #         重载后它停在生长中途 = **显示不全**；打它一下（触发重绘/换图）就恢复正常。
    #   来源：「电厂放下 动画未播放完成.SAV」vs「电厂动画播放完成.SAV」，
    #         用**同一档内 新建筑 vs 老建筑**扣掉时间噪音后的四元对照，
    #         再用 **37 个存档 / 8971 栋建筑** 的全库分布交叉验证。
    #     偏移(base帧) 对象偏移  正常值            坏档值  全库分布
    #     +120        +0xAC    5（或 -1）         18      5×7797 / -1×1173 / **18×1**
    #     +136        +0xBC    0                  1       0×8402 / 1×569
    #     base帧   对象偏移  建造中  已完成   全库分布（8971 栋）
    #     +120      +0xAC     18      5       5×7797 / -1×1173 / **18×1**
    #     +136      +0xBC      1      0       0×8402 / 1×569
    #     +156      +0xD0      1      100（类型相关）  100×3834 / 16×1228 / 14 / 15 …
    #     +216      **+0x10C**  2      0       0×8970 / **2×1**
    #     +1280     +0x534      0      1       1×8970 / **0×1**
    #     +1712     **+0x6E4**  0      1       1×6806 / 0×2141（u8）
    #   ★★ v14（§170）纠正两处标签错误：老标签 `+0xFC` 实为 **对象 +0x10C**，
    #      老标签 `+0x6E0` 实为 **对象 +0x6E4**（BLD_ANIM_OFF 存的是 base 帧偏移，
    #      对象偏移 = base 偏移 + 52）。引擎 `A(建造中)→B(完成)` 实写 diff 证明
    #      **+0x6E4 是引擎自己在"播完那一刻"置 1 的**（+0x10C 同时 2→0）。
    #   ⚠ base = **对象体起点 + 52**（vtable 0x7E3EBC 在 `base-52`；记录 = [16B CLSID][4B 自键][对象体]）
    #     ⇒ 读写一律用 `base + 偏移`，不要再 ±52。
    #   ★★2026-10-23（§196）补 `+0xC8`：全库 402 档统计，**所有 done 建筑无一例外
    #     `+0xC8 == -1`**（4294967295）；而"建造中"是 6 / 18 这类**帧号**。
    #     塔写成 done 但 `+0xC8` 还留在 18 ⇒ 引擎照第 18 帧画 ⇒ **无头**（T25_A 实测）。
    BLD_ANIM_OFF = {'+0xAC': 120, '+0xC8': 128, '+0xBC': 136, '+0xD0': 156,
                    '+0x10C': 216, '+0x534': 1280, '+0x6E4': 1712}
    BLD_ANIM_OFF_ALIAS = {'+0xFC': '+0x10C', '+0x6E0': '+0x6E4'}   # 旧标签兼容
    # ★★★「动画已完成」标记 = 这两个值同时成立（8989 栋里**各自只有 1 栋**不同）：
    #     +0x10C == 0   +0x534 == 1
    #   ⚠ `+0xD0` **不能**当通用判据 —— 它在 GAPOWR 上是 100，但 ATESLA/GTGCAN 等
    #     正常就是 14/15/16；只有"同类建筑"之间比它才有意义（见 `repair(phase=…)`）。
    BLD_ANIM_BAD = {'+0x10C': 0, '+0x534': 1}      # 旧名，语义见下（= 完成标记的期望值）
    BLD_ANIM_DONE_MARK = {'+0x10C': 0, '+0x534': 1}   # 「动画已播完」标记
    # ★★跳到"完成态"用的字段组（E 档 in-game 已验正常）：
    #   `+0xBC`（D 档证明无用）与 `+0xD0`（F 档证明不必需）都不写。
    BLD_ANIM_OK = {'+0xAC': 5, '+0xC8': -1, '+0x10C': 0, '+0x534': 1, '+0x6E4': 1}
    # ★★★跳到完成态其实**只需要 1 个字节**：`+0x6E4 = 1`
    #   （A 档 = 5/1/1/0/1/0 显示不全；E 档 = A + 6E4=1 显示正常 ⇒ A→E 只差这一个字节）
    BLD_ANIM_MIN_FIX = {'+0x6E4': 1}
    # ★★★★★§170（2026-10-22，第十四档 in-game）**重播建造动画**的配方
    #   真值表（字段序 AC/BC/D0/M10C/534/M6E4，同一栋 GAPOWR(80,82)）：
    #     档                         五元组           in-game
    #     ────────────────────────────────────────────────────────────
    #     原始A / I 档               18/1/1/2/0/0    ✅ 播完整建造动画
    #     H 档                       18/1/1/2/0/1    ✅ 播完整建造动画  ← 与 K 只差 AC
    #     K 档 撤标记+FC             5/1/1/2/0/1    ❌ **一直播放**（死循环）
    #     J 档 只撤 534              5/1/1/0/0/1    ❌ 显示不全        ← 与 K 只差 10C
    #     L 档 D0=100 撤标记          5/0/100/0/0/1  ❌ 显示不全
    #     ────────────────────────────────────────────────────────────
    #   ⇒ ①`+0xAC` 决定**播完会不会停**：18=会停，5=无限循环
    #     ②`+0x10C` 必须是 2（≠0）才会播；=0 干脆不播 ⇒ 显示不全
    #     ③`+0x6E4` 在建造动画分支里**无关**（A 与 H 只差它，两档都完美）
    #   ⇒ 只撤 `+0x534` **远远不够** —— 这正是 J/K/L 三档全灭的原因。
    # ★★★§171 定案（in-game，P/Q/R + M + N2 四档）：**`+0xD0` 必须是 1**。
    #   老 MIN 配方（只写 AC/10C/534）会留下完成态的 `+0xD0=100` ⇒ 进游戏**播两遍**动画。
    #   P 只把 D0 改 100 ⇒ 播两遍；Q 只把 BC 改 0 ⇒ 播一次（正常）；R 两项都改 ⇒ 播两遍
    #   ⇒ **`+0xD0 ≠ 1` 是"多播一遍"的唯一原因，`+0xBC` 与播放次数无关**。
    #   机制推断：D0 是「建造进度」，=100 表示进度已满；引擎发现"进度满但标记未写"
    #   就再补一遍完整 Buildup，第二遍才把标记落实 ⇒ 播两遍后正常。
    # ★★★★★ §187（2026-10-22 晚，解释 N2 vs T13b 的矛盾）：**`+0x6E4` 必须写 0**。
    #   §170 当年判"6E4 在建造动画分支里无关"（原始A=0 与 H=1 都播一遍）——
    #   但那两档都是**原生建筑（有伴随动画对象）**，伴随对象能兜住；
    #   **工具凭空插的楼没有伴随对象**，这时 6E4 就是唯一开关：
    #     · N2 新加的 ATESLA：模板是"建造中"那栋电厂 ⇒ 继承 `6E4=0` ⇒ **播了一遍** ✅
    #     · T13b 直接落成的电厂：模板是完成态 ⇒ 继承 `6E4=1` ⇒ **不播**（玩家实测）❌
    #   引擎侧语义也对得上：`0x739B6A mov byte ptr [esi+0x6e0], 1` 就是"播完置 1"。
    #   ⇒ 配方统一带上 `6E4=0`（对原生建筑无害：A/H 两档实测都播）。
    BLD_ANIM_BUILDUP_MIN = {'+0xAC': 18, '+0xD0': 1, '+0x10C': 2, '+0x534': 0,
                            '+0x6E4': 0}
    # 照抄 GAPOWR 原始档的完整「建造中」形态（M 档 in-game 已验："播一遍建造动画，正常"）：
    BLD_ANIM_BUILDUP_FULL = {'+0xAC': 18, '+0xBC': 1, '+0xD0': 1,
                             '+0x10C': 2, '+0x534': 0, '+0x6E4': 0}
    # ★★★★★ §195（2026-10-23，T42/T43 同基档对照 in-game 定案）：**「刚放下」态**。
    #   玩家把楼放下去、建造动画还没开始播的那一刻，存档里就是这 5 个值
    #   （黄金档未播完 / T43 三塔全正常，实测一致）：`AC=-1 / BC=0 / 10C=0 / 534=1 / 6E4=0`。
    #   `+0xD0`（建造总帧）**不写**，沿用模板 —— 写错会让动画播两遍或一闪而过。
    #
    #   ★为什么「完成态」（`AC=5 / 6E4=1`，见 `BLD_ANIM_OK`）反而**无头**：
    #     引擎看到 `6E4=1/534=1` 就认为这楼**已经建好了** ⇒ 不再走建造流程 ⇒
    #     **不创建炮塔 / 棱镜这类子对象** ⇒ 模型永远缺一块。
    #     而「刚放下」等于告诉引擎"还没建完"，读档后它自己播一遍、自己把子对象建出来。
    #   ⇒ 2026-10-23 之前所有"写成完成态 / 写成 18/1/1/2/0/0"的实验全失败，根因就在这里。
    #   ★★`+0xC8` 也必须写（2026-10-23 玩家 T44/T45 实测"只出底座"的反例）：
    #     `+0xC8` 是**动画帧号** —— `done` 是 `-1`，"建造中/刚放下"是 **18**（或 6）。
    #     从 `done` 模板克隆时它会继承 `-1` ⇒ 引擎以为"动画已经播到结尾"⇒ **停在底座**。
    #     T43（成功）是 `C8=18`；T44/T45（只出底座）是 `C8=-1`，**其余字段全同** ⇒ 单变量坐实。
    BLD_ANIM_FRESH = {'+0xAC': -1, '+0xC8': 18, '+0xBC': 0,
                      '+0x10C': 0, '+0x534': 1, '+0x6E4': 0}
    # ★§171：`+0xAC=18` 已证实**跨建筑类型通用**（GAPOWR 与 ATESLA/光棱塔 同档实测都播一遍）。
    #   留在表里只为将来万一遇到特例时覆盖；默认不查表，直接 18。
    BLD_ANIM_AC_BUILDUP = {'GAPOWR': 18, 'ATESLA': 18}
    # ★「建造中」应有的 `+0xD0` 值（写入配方用）与「会多播一遍」的判据阈值
    BLD_D0_BUILDUP = 1
    BLD_D0_REPLAY = 2      # +0xD0 >= 2 且 534==0 ⇒ 进游戏会播两遍（P/R 档实测）
    # ★★★**`+0x6E4` 是 u8 标志位，不是 u32**（引擎侧证据，2026-10-22）：
    #   `0x739B00  mov al, byte ptr [esi+0x6e0]`（esi = 对象体-4）`test al,al` / `jne …`
    #   `0x739B6A  mov byte ptr [esi+0x6e0], 1` + `0x739B70 mov byte ptr [esi+0x6e1], 0`
    #   另有 `0x735424 / 0x739B6C / 0x739C6C` 三处写 `mov byte ptr [esi+0x6e0], bl`。
    #   ⇒ 读写必须按 **1 字节**，否则会顺手把高 3 字节清零（现在是 0 所以没炸，但那是运气）。
    #   ⇒ 这也解释了全库二值分布（1×6806 / 0×2141）：它是开关，不是"完成度"。
    BLD_ANIM_U8 = {'+0x6E4'}
    # ★★★in-game 实测全表（2026-10-22 §170，十四档，同一栋 GAPOWR(80,82)）：
    #   档                    AC  BC   D0 10C  534  6E4   in-game
    #   ───────────────────────────────────────────────────────────────
    #   原始A 放下未播完      18   1    1   2    0    0    ✅ 播完整 Buildup（观感最好）
    #   I  完全还原坏值对照   18   1    1   2    0    0    ✅（= 原始A 逐字节 0 差异）
    #   H  原始值 + 6E4=1    18   1    1   2    0    1    ✅ 播完整 Buildup（观感最好）
    #   原始B 播放完成         5   0  100   0    1    1    ✅ 完成图
    #   C  全部还原           5   0  100   0    1    1    ✅ 正常
    #   E  A + {6E4=1}        5   1    1   0    1    1    ✅ 正常      ← 6E4 足以救 stuck
    #   F  A + {BC,6E4}       5   0    1   0    1    1    ✅ 正常      ← D0 不必需
    #   G  工具默认(旧)        5   0  100   0    1    1    ✅ 正常
    #   A {AC=5,10C=0,534=1}  5   1    1   0    1    0    ❌ 显示不全
    #   B  A + {D0=100}       5   1  100   0    1    0    ❌ 显示不全
    #   D  A + {BC=0}         5   0    1   0    1    0    ❌ 显示不全  ← BC 无用
    #   J  只撤 534           5   1    1   0    0    1    ❌ 显示不全
    #   K  撤 534 + 10C=2     5   1    1   2    0    1    ❌ **一直播放建造动画**
    #   L  D0=100 撤 534      5   0  100   0    0    1    ❌ 显示不全
    #   ───────────────────────────────────────────────────────────────
    # ⇒ ★★模型（十四档零例外，五态）：
    #     if 534 == 1:                       // 「建造已完成」
    #         if 6E4 == 1: done              // 完成图（C/E/F/G/原始B）
    #         else:        stuck             // 矛盾态 ⇒ 显示不全（A/B/D）
    #     else:                              // 「还在建造」
    #         if 10C == 0:   frozen          // 根本不播 ⇒ 显示不全（J/L）
    #         elif AC == 5:  loop            // 播不完 ⇒ 一直播（K）
    #         else:          buildup         // 播一遍就停（原始A/I/H）
    #   ⇒ 「动画未播完」是**合法中间态**，重载会自愈并播完整动画，**不该修**。
    #   ⇒ `+0xD0` 与渲染无关（F 档），且**随类型变**（GAPOWR 100 / ATESLA 16 / TESLA 15 /
    #      CAPARS01 14），默认不要改写 —— 只有同类众数才有意义（`anim_phase_default`）。
    BLD_ANIM_PHASE_HINT = {'GAPOWR': 100}   # 仅显式传 phase= 时才用，默认不写
    BLD_VTABLE = 0x7E3EBC         # BuildingClass vtable（写在 base-52）
    MAPSIZE_OFF = 0x348E          # ★存档自带地图尺寸 (W,H) 小端 u16 对（2026-10-15 定案，24 档自洽）
    TYPE_GUID_TAIL = bytes.fromhex('aca4006008055bb5')
    # 「两个 u32 锚点坐标」的快速匹配（低字节 0x80、高字节 0 ⇒ cell×256+128 形态）
    COORD_RE = re.compile(rb'\x80[\x00-\xff][\x00-\x03]\x00'
                          rb'\x80[\x00-\xff][\x00-\x03]\x00', re.DOTALL)

    def building_types(self):
        """存档里的类型对象 → {类型ID: 自键}（含建筑/单位/动画等全部 TypeClass）"""
        if getattr(self, '_btypes', None) is not None:
            return self._btypes
        pl = self.plain
        out, q = {}, 0
        while True:
            i = pl.find(self.TYPE_GUID_TAIL, q)
            if i < 0:
                break
            q = i + 1
            if i + 80 > len(pl):
                continue
            s = pl[i + 48:i + 80].split(b'\x00')[0]
            if 2 <= len(s) <= 20 and all(32 < c < 127 for c in s):
                key = struct.unpack_from('<I', pl, i + 8)[0]
                if key:
                    out.setdefault(s.decode('ascii'), key)
        self._btypes = out
        return out

    # ---------- 单位（步兵/载具）★2026-10-17 新定位 ----------
    def unit_types(self):
        """类型空间（步兵/载具/建筑通用）：{类型键: 'E1'|'MTNK'|…}。

        类型对象布局（实测）：`+0` GUID、`+16` 自键、`+20..+35` 四条类常量、
        `+56` 类型名（16B ASCII，如 `E1`/`MTNK`）、`+80` `"Name:E1"`、`+120` 显示名（"G.I."）。
        定位法：全文找 `"Name:XXX"` → 往前就近找 GUID（`????11d2|11d1` + `00 60 08 05 5B B5` 尾），
        且要求 `+56` 的 16B 名字与 `Name:` 后的一致（防误认）。
        """
        cached = getattr(self, '_utypes', None)
        if cached is not None:
            return cached
        pl = self.plain
        n = len(pl)
        tail = b'\x00\x60\x08\x05\x5b\xb5'
        out, offs = {}, {}
        i = 0
        while True:
            j = pl.find(b'Name:', i)
            if j < 0:
                break
            i = j + 1
            o = None
            for k in range(j - 40, j - 140, -1):
                if k >= 0 and pl[k + 6:k + 8] == b'\xd2\x11' \
                        and bytes(pl[k + 10:k + 16]) == tail:
                    o = k
                    break
            if o is None:
                continue
            nm = bytes(pl[o + 56:o + 72]).split(b'\x00')[0]
            nm2 = bytes(pl[j + 5:j + 21]).split(b'\x00')[0]
            if not nm or nm != nm2:
                continue
            _k = struct.unpack_from('<I', pl, o + 16)[0]
            out.setdefault(_k, nm.decode('ascii', 'ignore'))
            offs.setdefault(_k, o)
        self._utypes = out
        self._utypes_off = offs
        return out

    def house_placements(self, house=None, skip_special=True):
        """读出各阵营的「对象布置表」→ [{'type','x','y','house','off'}]（**建筑与单位混排**）。

        ★结构（2026-10-17 在 SAVE4823 上定位，三条独立证据）：
          `House 块内：[u32 n][(u32 类型键, u16 y, u16 x) × n]`
          ① 该表给出 E1→(75,73)、MTNK→(68,80)；
          ② 格对象 `(75,73)` 的自键 0x132B0820 出现在 **E1 实例簇**里（6913498）；
          ③ 格对象 `(68,80)` 的自键 0x1325EC10 出现在 **灰熊实例簇**里（6241329）。
          ⇒ 表里的 (y,x) 就是单位当前所在格（与格对象 `+56` 同序：y 在前）。
        只收「全部类型键都能在本档类型空间里查到、且坐标在本档坐标范围内」的表 —— 宁缺勿滥。
        """
        pl = self.plain
        types = self.unit_types()
        lim = self.coord_limit()
        want = None if house is None else self.house_label(house)
        res = []
        name_of = {h['key']: h['short'] for h in self.houses()}
        for a, k, b in self._house_blocks():
            short = name_of.get(k, '?%08x' % k)
            if skip_special and short in ('Special', 'Neutral'):
                continue
            if want and short != want and short not in str(house):
                continue
            b = min(b, len(pl))
            hit = None
            for o in range(a, max(a, b - 12)):
                cnt = struct.unpack_from('<I', pl, o)[0]
                if not (1 <= cnt <= 256) or o + 4 + 8 * cnt > b:
                    continue
                p = o + 4
                items = []
                ok = True
                for _ in range(cnt):
                    k = struct.unpack_from('<I', pl, p)[0]
                    if k not in types:
                        ok = False
                        break
                    y, x = struct.unpack_from('<HH', pl, p + 4)
                    # ★宽上界：别拿 [Map] Size 当界（实测有 y=80 正好压线/超声明图幅的单位）
                    if not (0 <= x < 512 and 0 <= y < 512):
                        ok = False
                        break
                    items.append((types[k], x, y, p))
                    p += 8
                if ok and items:
                    hit = (o, items)
                    break
            if hit:
                for t, x, y, p in hit[1]:
                    res.append({'type': t, 'x': x, 'y': y, 'house': short, 'off': p})
        return res

    def units(self, house=None, exclude=()):
        """只取**单位**（步兵/载具）：`house_placements` 里剔掉 `exclude` 中的类型名。

        ⚠这张表是"建筑+单位混排"的布置表，**必须**把本档的建筑类型名传进 `exclude`
        才能只留单位（GUI 侧可传 rules/art 的建筑名单）。不传则等于全部返回。
        """
        ex = {str(t).upper() for t in exclude}
        return [u for u in self.house_placements(house=house)
                if u['type'].upper() not in ex]

    # ---------- 单位（步兵/载具）实例记录：读取 + 克隆放置（2026-10-17 实装） ----------
    UNIT_CLASSES = (
        (b'\xc4\x2d\x27\x0e\x0f\x9c\xd1\x11', b'\x44\x27\x58\x4a\x39\x98\xd1\x11', 'infantry'),
        (b'\xca\x2d\x27\x0e\x0f\x9c\xd1\x11', b'\x41\x27\x58\x4a\x39\x98\xd1\x11', 'vehicle'),
    )
    # 已知的「伴随对象」类 GUID —— 每个都对应一种 **Locomotor（移动方式）**：
    #   4A582744 步兵（Walk）/ 4A582741 地面载具（Drive）/ 92612C46 空中（Fly）
    #   规则里 `Locomotor={...}` 的 GUID 与这里是同一个东西。
    UNIT_COMP_GUIDS = (
        bytes.fromhex('4427584a3998d111b70900a024ddafd1'),   # 步兵
        bytes.fromhex('4127584a3998d111b70900a024ddafd1'),   # 地面载具
        bytes.fromhex('462c61921ff7d111ac9f006008055bb5'),   # 空中（92612C46）
        bytes.fromhex('4727584a3998d111b70900a024ddafd1'),   # 超时空（4A582747）
        bytes.fromhex('4627584a3998d111b70900a024ddafd1'),   # 飞机（4A582746）
        bytes.fromhex('4227584a3998d111b70900a024ddafd1'),   # 悬浮/气垫（4A582742）
        bytes.fromhex('e174ea2bcc7ad311be140010 4b62a16c'.replace(' ', '')),  # 船（2BEA74E1）
        bytes.fromhex('6697b4b776e5d3119bd900104b972fe8'),   # 导弹（B7B49766）
    )
    COMP_GUID_LOCO = {
        bytes.fromhex('4427584a3998d111b70900a024ddafd1'): 'Walk',
        bytes.fromhex('4127584a3998d111b70900a024ddafd1'): 'Drive',
        bytes.fromhex('462c61921ff7d111ac9f006008055bb5'): 'Fly',
        bytes.fromhex('4727584a3998d111b70900a024ddafd1'): 'Chrono',
        bytes.fromhex('4627584a3998d111b70900a024ddafd1'): 'Aircraft',
        bytes.fromhex('4227584a3998d111b70900a024ddafd1'): 'Hover',
        bytes.fromhex('e174ea2bcc7ad311be1400104b62a16c'): 'Ship',
        bytes.fromhex('6697b4b776e5d3119bd900104b972fe8'): 'Missile',
    }
    # 血量（满血 = 规则里的 Strength；两份同改）
    UNIT_HP_OFF = (128, 132)
    # ★2026-10-18 21:4x：**等级（Veterancy）** = 一对孪生字段
    #   `+0x150` = int（0 新兵 / 1 老兵 / 2 精英；-1 见下）,
    #   `+0x164` = float(2 − 等级)（0→2.0 / 1→1.0 / 2→0.0）。
    #   证据（1394 个单位的全档不变量，**0 例违例**）：
    #     +150=0 → +164=2.0f ×1100、+150=1 → 1.0f ×38、+150=2 → 0.0f ×245、
    #     +150=-1 → 0.0f ×11（SLAV 等，疑似"未初始化/不参与"）。
    #   为什么判定它是等级：整档 1394 条记录里，**只有这两列出现 0/1/2 三级取值且严格配对**
    #   （E1 55 条里除它俩外没有任何字段呈 3 值分布）。
    #   ⚠ 改等级必须**两个一起改**（引擎载入可能按其中之一重算另一个）。
    #   ★★2026-10-18 21:43 **游戏内验收通过**（样本 `E:\YURI\等级矩阵_4个大兵.SAV`，
    #     同一个档里 4 个大兵只有这两列不同）：玩家判读 =「0级一个、1级一个、三级二个」。
    #     ⇒ ① **`+0x150` 就是 Veterancy**（0/1/2 依次对应 无星/一级/三级）；
    #        ② **故意错配的那一个（150=2 但 164=2.0）照样显示三级** ⇒ 显示只看 `+0x150`。
    #   ★★★2026-10-25 §270 玩家三档实测（`E1_等级0/1/2_*.SAV`，同一台 E1 @(211,291)
    #     在游戏里升到三个等级）：**记录内只有 `+0x150` 这 1 个字节不同**，
    #     `+0x164` 三份**全是 2.0f**。⇒ 结论修正：
    #       · 写等级**只写 `+0x150`**；
    #       · 旧代码"成对改写" `+0x164 = float(2−vet)`（精英→0.0）是**错的** ——
    #         实测后果：新放的"精英"兵在游戏里显示**新兵**（写 0.0 反而被判成异常状态）。
    #     `UNIT_VET_OFF[1]` 保留只为兼容老脚本，**不要再写它**。
    UNIT_VET_OFF = (0x150, 0x164)
    VET_NAMES = {0: '新兵', 1: '老兵', 2: '精英'}
    CELL_GUID = bytes.fromhex('ce99bfc18c1ad2118175006008055bb5')     # CellClass

    def _cell_by_key(self):
        self._invalidate_if_shifted()
        """{CellClass 自键: (x, y)} —— 12 720 个格对象，用于把记录里的格键翻译成坐标。"""
        if getattr(self, '_ckey', None) is not None:
            return self._ckey
        pl, out, i = self.plain, {}, 0
        coff = {}
        while True:
            j = pl.find(self.CELL_GUID, i)
            if j < 0:
                break
            # ★2026-10-18 修：+0x38 是 **(x, y)**（与建筑记录 +600/+604 同一口径）。
            #   原来按 (y, x) 解析 ⇒ 单位坐标整体转置（放置单位时落到对称格上）。
            x, y = struct.unpack_from('<HH', pl, j + 56)
            k = struct.unpack_from('<I', pl, j + 16)[0]
            out[k] = (x, y)
            coff[j] = (x, y)
            i = j + 1
        self._cell_offs = coff
        self._ckey = out
        return out

    def cell_offsets(self):
        self._invalidate_if_shifted()
        """{(x, y): CellClass 对象起点}（12 720 个；`_cell_by_key()` 的反查）。"""
        if getattr(self, '_coffs', None) is not None:
            return self._coffs
        pl, out, i = self.plain, {}, 0
        while True:
            j = pl.find(self.CELL_GUID, i)
            if j < 0:
                break
            x, y = struct.unpack_from('<HH', pl, j + 56)      # ★同上：+0x38 是 (x, y)
            out.setdefault((x, y), j)
            i = j + 1
        self._coffs = out
        return out

    # ★★★ 2026-10-17 定案：格对象里的「谁站在这一格」登记槽
    #   `格+248` = 站在该格上的**单位对象键**（建筑不写这里，建筑走另一张 348B 占用表）；
    #   `格+308` = 占用标记 u32（占用 0x0001FFFF / 空 0x0000FFFF）；
    #   `格+312` = 占用类型码 u32（步兵 0x08 或 0x10、载具 0x20，**随档不同** ⇒ 从模板格照抄）。
    #   证据：同一份档里 E1/APOC/HTNK 三个真实单位的格对象都在 +248 有各自的自键；空格全 0。
    #   ⇒ 克隆放置必须同时登记，否则"对象说自己在格上、格上却没有它"。
    CELL_OCC_OFF = 248
    CELL_OCC_FLAG_OFF = 308
    CELL_OCC_KIND_OFF = 312
    # ★§275：`+0x138`（=312）的取值语义（实测：载具 574/574 = 0x20、建筑 1088/1113 = 0x80、
    #   单步兵格 604/604 是单个 bit）。引擎读档靠它判断"这格能不能过"⇒ 删/搬单位必须同步。
    CELL_KIND_INF_MASK = 0x0F          # ★§281：步兵只占低 4 位（0x1/0x2/0x4/0x8）
    CELL_KIND_TERRAIN = 0x10           # ★§281：**Terrain 对象**（树/石头/路牌…）占这位
    # ★★§282 `+0x100` = **地形变体索引**，**不是覆盖物**（这条已经证伪过一版，别再来一次）：
    #   初看像 overlay（玩家测试档里 143 格 / 4 组、形状与地编截图里的长条/U 形吻合），
    #   但按"同一 tile 下该字段是否恒定"一验就穿了 —— **tile 989→值3(1038/1038)、
    #   tile 294→值1(779/779)、tile 969→99.6%、tile 422→93.6%**，全档确定性 0.99。
    #   ⇒ 它跟 `+0x4C` 的 tile 一一对应，是**地形派生**（很可能就是 §279 那个"引擎挑的同族变体"）。
    #   真正的覆盖物（沙袋/围墙/矿/铁丝网）**不在存档里**，见 `terrain_variant_cells()` 的类注释。
    TERRAIN_VARIANT_OFF = 0x100
    # ★★★2026-10-26 §283：**覆盖物就在存档里** —— 格对象 `+0x58` = `OverlayTypeIndex`（u32，
    #   `0xFFFFFFFF` = 没有）。§282 那句"覆盖物不在存档里"是**错的**，作废。
    #   证据（两条独立）：
    #   ① `新建文件夹 (23)/ALL01UMD.sav` × `all01umd.map`（同一张图）：地图 `[OverlayPack]`
    #      解出 1525 个覆盖物格，与格 `+0x58` 逐格 u32 比对 **1513/1525 = 99.2%** 全等
    #      （剩 12 格是 0x1E 宝石被引擎清掉，不是偏移错）。
    #   ② `SAVE4823.SAV` 的 `+0x58` 里 **14 个 0x70**，而 `2.map` 的 `[OverlayPack]` 正好是
    #      **14 个 112(0x70) = TIB08 矿石**，形状同为一小片矿 ⇒ 玩家在 FA2 里画的矿进档了。
    #   ⚠ 为什么 §282 扫漏了：那轮用"同一 tile 下该字段是否恒定"筛，而**覆盖物本来就跟地形
    #     强相关**（矿只会长在特定 tile 上）⇒ 每个偏移都 0.99+，把真字段一起误杀。
    #     教训：判"某字段是不是覆盖物/物件"必须用**地图真值逐格比**，不能用 tile 相关性。
    CELL_OVERLAY_OFF = 0x58
    CELL_OVERLAY_NONE = 0xFFFFFFFF
    # 引擎里**没有 INI 段**、行为硬编码的墙类（`[OverlayTypes]` 里有名、rulesmd.ini 里查不到
    #   `Wall=yes`）：链子网/铁刺/木栅栏/铁丝网。列在这里只用于"提示"，不做拦截、不当判据。
    OVERLAY_HARD_WALLS = ('CYCL', 'BARB', 'WOOD', 'FENC')
    CELL_KIND_VEHICLE = 0x20
    CELL_KIND_BUILDING = 0x80

    # ===== §281 Terrain 对象（树 / 石头 / 路牌 / 花…）=====
    #   玩家（2026-10-26 11:41）给了「什么也没有、只有三棵树」的档 `SAVE0029.SAV` 用来定案。
    TERRAIN_GUID = bytes.fromhex('ce2d270e0f9cd111b70900a024ddafd1')
    TERRAIN_SIZE = 244                 # 固定长度，**没有伴随对象**（3334 棵间距全 244）
    TERRAIN_TYPE_OFF = 0xDC            # 类型指针（运行期地址，每档不同 ⇒ 必须在本档类型表里反查名字）
    TERRAIN_HP_OFF = 0x80              # 当前 HP（TREE01 满血 200，与类型表里 Strength 一致）
    TERRAIN_POS_X_OFF = 0xB0           # lepton `(格<<8)|0x80`
    TERRAIN_POS_Y_OFF = 0xB4           # 同上（`+0xCC` 是 y 的孪生副本，全等）
    # 格子占用写法：None=不写 / 'key'=只写格+248（默认）/ 'full'=连标记与类型码一起写
    # ⚠ 'full' 实测会把载具克隆弄崩（V3 vs H3），别默认开。
    UNIT_CELL_OCC_MODE = 'key'
    # ★§212：放置时**必须清零**的"指令/目的地"槽（否则新单位会带着模板的攻击命令乱跑）
    # ★★2026-10-24 晚 回退（T72/T73 是昨天验收通过的档，今天改成"多清 4+2 个槽"结果打掉必崩）：
    #   实测对照（同一台飞碟克隆）：
    #     T72（通）  +0x270/+0xB8=1248/1248  +0x298=60  +0x3A4=+0x3BC=118（**保留骨架值**）
    #     T92（崩）  +0x270/+0xB8=416/416    +0x298=0   +0x3A4=+0x3BC=0（被我们清成 0）
    #   ⇒ `+0x298` 与 `+0x3A4/+0x3BC` **不能清 0**：
    #     `+0x3A4/+0x3BC` 是"当前指令对象"的引用，引擎**不检查是否为 0**，
    #     照样按"有指令"去解引用 ⇒ 调到空/失效对象 ⇒ 跳到垃圾地址（except.txt 15:04，Eax=0）。
    #     `+0x298` 是任务/状态号（T72 那台是 60），清 0 反而让"任务还在、目标却没了"⇒ 奔地图角。
    #   ⇒ 恢复成"只清 0x198/0x658/0x680/0x960/0x964"（昨天 p168 的口径）。
    # ★2026-10-24 §248.8：`+0x198` **不清**（从 §212 起一直在清，是错的）。
    #   实测 T85：原生 98/99 架飞碟这一项 = `0x7093df`；玩家地编放进去、引擎亲手造的新单位
    #   **也是** `0x7093df`（DISK/TELE33/MIND/YTNK/HTNK 这类心灵/传送单位都有）——
    #   两份基准一致，只有我们清成 0。当年 T72/T73 能通，是因为平地三V3 的原生这一项本来就是 0，
    #   "匹配同档原生"这条不变律恰好被满足了。
    #   语义（反汇编 0x41E777/0x41E809）：它是个**小枚举索引**（与 1、2 比较），不是目标指针；
    #   §13552 把它当"目标指针"是早期误判。
    # ★★★§252（2026-10-24 夜，玩家第 7 轮）——**清零路线被数据推翻**。
    #   症状：飞碟"有目标、目标在地图外/不知道要去哪"、"打掉弹框"。
    #   数据（`T106_平地落点.SAV`，同档 98 台原生 DISK 做基准，逐字段 0/98 扫描）：
    #   我们的克隆是**全场唯一**在这 6 个槽上是 0 的单位：
    #     `+0x960/+0x964` = 伴随段（Fly locomotor）+0x54/+0x58 = **目的地格 lepton**
    #        98/98 原生都是合法格（如 164.5/175.5）；我们 = **0 = 格 (0,0) = 地图角**
    #        ⇒ 玩家反复说的"目标在地图外"就是它。**这是"有目标、往外飞"的真身。**
    #     `+0x658/+0x680` = 目的地的 Y（实测恒等于 +0x964，98/98 一致）
    #     `+0x3A4/+0x3BC` = 全档统一的**共享指令对象** `0x17B2DC`（98/98 同一个值）
    #        我们清成 0 ⇒ "有指令槽、里面是空对象" ⇒ §243 当年实测的"打掉弹框"正是这条
    #        （那次误判成"外来指针"，真凶另有其物，但零值本身确实是错的）
    #   ★同时推翻 §248 的依据：那份"地编干净档"里这些槽也是 0，但那是**另一张地图、
    #   单位从未入队**的状态；而**本档**原生一律非 0。判据要跟**本档原生**比（§234 铁律）。
    #   ★地面载具（Drive 伴随段）实测 157/157 原生这几项**本来就是 0** ⇒ 规则必须
    #   "骨架是 0 就保持 0"，不能无脑写坐标，否则光棱反而变成异类（它的克隆现在是 0 差异）。
    UNIT_ORDER_CLEAR = (
                        # 任务号 + 目标点：空闲原生（及地编新放的单位）本来就是 0
                        0x298, 0x29C, 0x2A0, 0x2A4,
                        # ★§245：`+0x5EC` 存的是**另一台单位的键**（野链），克隆会继承成
                        #   指向一台无关单位。T72/T97（验收通过）那台是 0 ⇒ 写 0。
                        0x5EC)
    # 这几项**不抄 0，抄骨架**（§252）：共享指令对象，全档同一个值。
    UNIT_ORDER_KEEP = (0x3A4, 0x3BC)
    # 目的地：`0x960`=x、`0x964`=y（伴随段 +0x54/+0x58）；`0x658/0x680` 恒等于 y。
    UNIT_DEST_X = 0x960
    UNIT_DEST_Y = 0x964
    UNIT_DEST_Y_COPY = (0x658, 0x680)

    # ★2026-10-24 §232：单位记录里的「位置副本槽」——被清零会导致"永远上不了车"
    #   `+0x56C` / `+0x570` = 两个 u16 (x,y) 位置副本。正常单位**至少有一个等于自己所在格**：
    #   实测 1775 个单位 → (空, 自己坐标) 1090、(自己坐标, 别的) 78、(自己坐标, 自己坐标) 54、
    #   (别的, 自己坐标) 78、(别的, 别的) 61、(空, 别的) 122。
    #   **只有 1 个是 (自己坐标, 空)** —— 就是那个"怎么都上不了车"的工程师，`+0x570` 被固化成 (0,0)，
    #   而且跨 3 份存档（位置从 (110,205) 变到 (116,208)）一直是 (0,0) ⇒ 游戏内移动不会刷新它。
    #   ⇒ 判"位置状态是否固化"就看这两个槽有没有一个是自己坐标。
    POS_SLOTS = (0x56C, 0x570)

    def unit_pos_slots(self, rec):
        """该单位的两个位置副本槽 → {'0x56C','0x570','xy','ok'}；`ok=False` 即位置状态异常。"""
        pl, o = self.plain, rec['off']
        xy = (rec.get('x'), rec.get('y'))
        a = struct.unpack_from('<2H', pl, o + 0x56C)
        b = struct.unpack_from('<2H', pl, o + 0x570)
        return {'0x56C': a, '0x570': b, 'xy': xy, 'ok': (a == xy) or (b == xy)}

    def broken_position_units(self, strict=True):
        """[(rec, slots)] —— 位置副本槽异常的单位。

        · `strict=True`（默认）：只报 **`+0x570` 为 (0,0)** 的。实测全场仅 1 个，
          正是"怎么都上不了车"的那个工程师 ⇒ 这是最可疑的固化标记。
        · `strict=False`：再加报"两个槽都不等于自己所在格"的（实测 183 个，
          多为**正在移动中**或**在建筑/运输工具里**，属正常，别乱修）。
        """
        out = []
        for r in self.unit_records():
            if not isinstance(r.get('x'), int):
                continue
            s = self.unit_pos_slots(r)
            if s['0x570'] == (0, 0):
                out.append((r, s))
            elif not strict and not s['ok']:
                out.append((r, s))
        return out

    def fix_unit_position_slots(self, rec):
        """把该单位的两个位置副本槽都对齐到记录里的 (x,y)。返回改动的**字节数**。

        ★这是"上不了车 / 站在原地不执行命令"的最小修复：不动身份、不动指令、不动坐标本身。
        """
        pl, o = self.plain, rec['off']
        xy = (rec['x'], rec['y'])
        n = 0
        for off in self.POS_SLOTS:
            if struct.unpack_from('<2H', pl, o + off) != xy:
                struct.pack_into('<2H', pl, o + off, xy[0], xy[1])
                n += 4
        return n

    # ★2026-10-25 §268：单位「移动」= 只改位置相关的这几处副本（不动身份/指令/血量/等级）
    UNIT_LEPTON_X = (176, 616)        # lepton x 的两份副本（y 在 +4）
    UNIT_CELLKEY_OFF = 224            # 格键（指向格对象自键）
    UNIT_CELLXY_OFF = (1392, 1394)    # u16 格坐标

    # ================= §281 Terrain 对象（树 / 石头 / 路牌 / 花…） =================
    #   玩家 2026-10-26 11:41 给了「什么也没有、只有三棵树」的 `SAVE0029.SAV` 用来定案：
    #   · 记录固定 **244 字节**，**没有伴随对象**（3334 棵相邻间距全是 244）
    #   · 坐标 `+0xB0/+0xB4`（lepton，`(格<<8)|0x80`），`+0xCC` 是 y 的孪生副本（全等）
    #   · 类型 `+0xDC` = **运行期指针**（每档不同），名字要在本档的 TerrainType 表里反查
    #   · HP `+0x80`（TREE01 满血 200，与类型表 Strength 一致）
    #   · 格对象 `+0xF8` 记它的键、`+0x138` 的 **0x10** 位表示"这格有 Terrain"
    #   与地图 `[Terrain]` 段交叉验证：小档 3/3、大档 3282/3334 坐标命中类型。

    @staticmethod
    def _cstr_at(pl, off, maxlen=32):
        """读 `off` 处的 C 字符串（ASCII），读不到返回 ''。"""
        if off < 0 or off >= len(pl):
            return ''
        b = pl[off:off + maxlen]
        z = b.find(b'\x00')
        if z <= 0:
            return ''
        try:
            s = b[:z].decode('ascii')
        except Exception:
            return ''
        return s

    @staticmethod
    def _looks_like_type_name(s):
        """TerrainType 名字长这样：`TREE01` / `TIBTRE01` / `BLOSSOM03` / `HDSTN01` / `SIGN01`。"""
        if not (3 <= len(s) <= 24):
            return False
        if not any('A' <= c <= 'Z' for c in s):
            return False
        return all(('A' <= c <= 'Z') or ('0' <= c <= '9') or c in '_-' for c in s)

    def terrain_objects(self):
        """所有 **Terrain 对象**（树/石头/路牌/花…）→ `[{'off','key','x','y','type','type_ptr','hp'}]`。

        玩家口径：树不算"覆盖物"，它是**地形对象**——确实如此，它是独立对象类
        （CLSID `ce2d270e…`），每棵一条 244 字节记录，会占格（`+0x138` 的 0x10 位）。
        ★这跟"能不能放东西"直接相关：树格上放单位/建筑，游戏里会叠在一起或被挤掉。
        """
        memo = getattr(self, '_terrobjs', None)
        if memo is not None and memo[0] == len(self.plain):
            return memo[1]
        pl = self.plain
        g = self.TERRAIN_GUID
        pos, i = [], 0
        while True:
            j = pl.find(g, i)
            if j < 0:
                break
            pos.append(j)
            i = j + 1
        names = self.terrain_type_names([struct.unpack_from('<I', pl, p + self.TERRAIN_TYPE_OFF)[0]
                                         for p in pos])
        out = []
        for p in pos:
            tp = struct.unpack_from('<I', pl, p + self.TERRAIN_TYPE_OFF)[0]
            out.append({
                'off': p,
                'key': struct.unpack_from('<I', pl, p + 16)[0],
                'x': struct.unpack_from('<I', pl, p + self.TERRAIN_POS_X_OFF)[0] >> 8,
                'y': struct.unpack_from('<I', pl, p + self.TERRAIN_POS_Y_OFF)[0] >> 8,
                'type': names.get(tp) or ('?%08x' % tp),
                'type_ptr': tp,
                'hp': struct.unpack_from('<I', pl, p + self.TERRAIN_HP_OFF)[0],
                'size': self.TERRAIN_SIZE,
            })
        self._terrobjs = (len(pl), out)
        return out

    def terrain_type_names(self, ptrs=None):
        """`{类型指针: 类型名}` —— 从**本档明文里的 TerrainType 表**反查。

        ★为什么不能写死表：`+0xDC` 是**运行期地址**（同一棵 TREE01 在小档是 `0x10253d30`、
          在大档是 `0x104a44c0`）。但类型表结构固定：

              [类型指针 u32] ........ +0x28 ........ 名字 "TREE01"\0 ... Image "Tree"\0 ...
               ^-- 这个指针就在名字起点 **-0x28** 处（自引用）

          实测：`all01umd.SAV` 里 "TREE01" @`0x247f710`，`u32@0x247f6e8` 正好 = `0x104a44c0`；
          41 种类型全部按此法命中，且与地图 `[Terrain]` 逐个对得上。
        算法：先用树记录收集所有类型指针 → 逐个在明文里找它的出现位置 → 检查该位置 **+0x28**
        是不是合法类型名 → 命中即得映射。找不到的就留 `?xxxxxxxx`（不猜）。
        """
        pl = self.plain
        if ptrs is None:
            g = self.TERRAIN_GUID
            ptrs, i = [], 0
            while True:
                j = pl.find(g, i)
                if j < 0:
                    break
                ptrs.append(struct.unpack_from('<I', pl, j + self.TERRAIN_TYPE_OFF)[0])
                i = j + 1
        want = set(ptrs) - {0}
        memo = getattr(self, '_terrnames', None)
        if memo is not None and memo[0] == len(pl) and want <= set(memo[1]):
            return memo[1]
        names = dict(memo[1]) if (memo is not None and memo[0] == len(pl)) else {}
        for pv in want - set(names):
            pat = struct.pack('<I', pv)
            q, found = 0, None
            while True:
                j = pl.find(pat, q)
                if j < 0:
                    break
                q = j + 1
                nm = self._cstr_at(pl, j + 0x28, 24)
                if self._looks_like_type_name(nm):
                    found = nm
                    break
            if found:
                names[pv] = found
        self._terrnames = (len(pl), names)
        return names

    def terrain_at(self, cx, cy):
        """该格上的 Terrain 对象（树/石/路牌…）→ dict / None。"""
        for t in self.terrain_objects():
            if t['x'] == int(cx) and t['y'] == int(cy):
                return t
        return None

    def terrain_count(self):
        """Terrain 对象总数（树 + 石头 + 路牌…）。"""
        return len(self.terrain_objects())

    # ================= §282 覆盖物 Overlay：**存档里没有**（结论） =================
    #   玩家 2026-10-26 11:41 给了「空图 + 只放覆盖物」的测试档并贴了地编截图，要求读出来。
    #   排查过程（都做过，别重复）：
    #   ① 把格对象 348 字节的**每一个偏移**都扫了一遍，统计"非零格数"和"同一 tile 下是否恒定"：
    #      所有稀疏字段（非零 < 400 格）的 tile 确定性都是 **0.99~1.00** ⇒ 全是地形派生；
    #   ② `+0x100` 一度很像 overlay（143 格 / 4 组 / 形状与截图吻合），但被 ① 证伪；
    #   ③ 覆盖物**在存档里没有独立字段**：RA2 的 Overlay 是"格上的一层"，引擎读档时
    #      从**地图文件**的 `[OverlayPack]`（LCW/Format80 压缩，512×512 数组）重建。
    #   ⇒ 要显示覆盖物，只能解地图的 OverlayPack（见 docs §282，块结构已确认，指令流待调）。
    #   唯一在存档里出现过的"覆盖物类对象"是隧道入口 TWNK1（CLSID `c32d270e…`）。

    def overlay_names(self, ini_path=None):
        """`{索引: 名字}` —— 从 `rules(md).ini` 的 `[OverlayTypes]` 段读（`N=名字`，N 就是索引）。

        解出地图 OverlayPack 之后用它把索引翻成名字；没有规则文件就返回空表（不猜）。
        实测 `E://YURI//rulesmd.ini` 250 项：1=GASAND(沙袋) 3=GAWALL(围墙) 5=WOOD(木墙)
        9=DUMMY4 25=BRIDGE1 26=BRIDGE2 105..=TIB01(矿) 181..=CRATE 184..=FENCE01。
        """
        if not ini_path or not os.path.isfile(ini_path):
            return {}
        memo = getattr(self, '_ovl_names', None)
        if memo is not None and memo[0] == ini_path:
            return memo[1]
        out = {}
        try:
            import map_ini
            kv = map_ini.MapIni(ini_path).sections.get('overlaytypes') or {}
            for k, v in kv.items():
                try:
                    n = int(k)
                except Exception:
                    continue
                if v:
                    out[n] = str(v).strip().upper()
        except Exception:
            out = {}
        self._ovl_names = (ini_path, out)
        return out

    def terrain_variant_cells(self):
        """`{(x,y): 值}` —— 格对象 `+0x100`（**地形变体索引**，与 tile 强相关）。

        ★不是覆盖物（多次验证，见类注释）。目前用途：诊断"某个 tile 被引擎换成了哪个变体"，
          对应 docs §279 的"1289 格同族变体"问题。
        """
        memo = getattr(self, '_ovl_memo', None)
        if memo is not None and memo[0] == len(self.plain):
            return memo[1]
        pl = self.plain
        out = {}
        for xy, o in self.cell_offsets().items():
            v = pl[o + self.TERRAIN_VARIANT_OFF]
            if v:
                out[xy] = v
        self._ovl_memo = (len(pl), out)
        return out

    # ===== §283 覆盖物（沙袋/围墙/铁丝网/矿/宝石/桥面/铁轨/石头/箱子…）=====
    def overlay_at(self, cx, cy):
        """该格的**覆盖物类型索引**（进 `[OverlayTypes]` 的行号）→ int / None（没有/没这格）。"""
        o = self.cell_offsets().get((int(cx), int(cy)))
        if o is None:
            return None
        v = struct.unpack_from('<I', self.plain, o + self.CELL_OVERLAY_OFF)[0]
        return None if v == self.CELL_OVERLAY_NONE else v

    def overlay_cells(self):
        """`{(x,y): 索引}` —— 全档有覆盖物的格（缓存，键 = 明文长度）。"""
        memo = getattr(self, '_ovlcells', None)
        if memo is not None and memo[0] == len(self.plain):
            return memo[1]
        pl = self.plain
        out = {}
        for xy, o in self.cell_offsets().items():
            v = struct.unpack_from('<I', pl, o + self.CELL_OVERLAY_OFF)[0]
            if v != self.CELL_OVERLAY_NONE:
                out[xy] = v
        self._ovlcells = (len(pl), out)
        return out

    def overlay_type_info(self, ini_path=None):
        """`{索引: dict}` —— 覆盖物的**判定依据**，全部来自 `rules(md).ini`（不猜）。

        索引 = `[OverlayTypes]` 里 `N=名字` 的 N（实测 250 项），名字再指向同名段取字段：
          `Name` / `Image` / `Land`（改地块类型）/ `Tiberium=yes`（矿·宝石）/
          `Wall=yes`（墙）/ `IsARock`（石头）/ `Overrides`（桥面覆盖）/ `NoUseTileLandType`
        """
        if not ini_path or not os.path.isfile(ini_path):
            return {}
        memo = getattr(self, '_ovl_info', None)
        if memo is not None and memo[0] == ini_path:
            return memo[1]
        out = {}
        try:
            with open(ini_path, 'rb') as f:
                text = f.read().decode('latin-1')
        except Exception:
            return {}

        def sect(name):
            key = '[' + name.lower() + ']'
            low = text.lower()
            i = low.find(key)
            if i < 0:
                return {}
            j = low.find('\n[', i + 1)
            body = text[i:j if j > 0 else len(text)]
            kv = {}
            for line in body.replace('\r', '').split('\n')[1:]:
                if '=' not in line or line.lstrip().startswith(';'):
                    continue
                k, v = line.split('=', 1)
                k = k.strip().lower()
                v = v.split(';')[0].strip()
                if k and k not in kv:
                    kv[k] = v
            return kv

        for idx, nm in (self.overlay_names(ini_path) or {}).items():
            kv = sect(nm)
            land = (kv.get('land') or '').strip()
            out[idx] = {
                'name': nm,
                'display': (kv.get('name') or '').strip(),
                'land': land,
                'tiberium': (kv.get('tiberium') or '').lower() == 'yes',
                'wall': (kv.get('wall') or '').lower() == 'yes',
                'is_rock': bool((kv.get('isarock') or '').strip()),
                'overrides': (kv.get('overrides') or '').lower() == 'yes',
                'has_section': bool(kv),
            }
        self._ovl_info = (ini_path, out)
        return out

    def overlay_label(self, idx, ini_path=None):
        """把索引翻成一句人话（名字 + 类别），翻不出来就只给索引 —— **不编名字**。"""
        if idx is None:
            return ''
        info = self.overlay_type_info(ini_path).get(idx) or {}
        nm = info.get('name') or ''
        if not nm:                       # 名字表里没有 ⇒ 只给索引，**不编名字**
            return '#%d' % idx
        kind = self.overlay_category(idx, ini_path)
        disp = info.get('display') or ''
        bits = ['%s（#%d' % (nm, idx)]
        if disp and disp.upper() != nm.upper():
            bits.append('，%s' % disp)
        bits.append('）')
        s = ''.join(bits)
        if kind:
            s += ' · %s' % kind
        return s

    def overlay_category(self, idx, ini_path=None):
        """覆盖物的大类（中文）—— **只看 INI 字段**，缺字段的按已知硬编码墙名兜底并标注。"""
        if idx is None:
            return ''
        info = self.overlay_type_info(ini_path).get(idx) or {}
        nm = (info.get('name') or '').upper()
        if info.get('tiberium'):
            return '矿石/宝石（不挡路）'
        if info.get('wall'):
            return '墙（挡路）'
        if info.get('overrides'):
            return '桥面'
        if info.get('is_rock'):
            return '石头（挡路）'
        if nm in self.OVERLAY_HARD_WALLS:
            return '墙（引擎硬编码，挡路）'
        land = info.get('land') or ''
        if land:
            return '覆盖地块：%s' % land
        if not info.get('has_section') and nm:
            return '无 INI 段（引擎硬编码）'
        return ''

    def map_overlay_at(self, cx, cy, map_path=None):
        """**地图文件**里该格的覆盖物索引（解 `[OverlayPack]`，§283）→ int / None。

        用途：存档里根本没有这个格对象时（地图外/虚空）拿它兜底；平时**以存档为准**。
        地图读不出来就返回 None（不猜）。
        """
        if not map_path or not os.path.isfile(map_path):
            return None
        try:
            import overlay_pack as _op
        except Exception:
            try:
                from . import overlay_pack as _op       # 包内导入
            except Exception:
                return None
        memo = getattr(self, '_ovl_map', None)
        if memo is None or memo[0] != map_path:
            types, _frames = _op.read_map_packs(map_path)
            memo = (map_path, types)
            self._ovl_map = memo
        return _op.map_overlay_at(memo[1], int(cx), int(cy))

    def overlay_blocks_placement(self, idx, ini_path=None):
        """这个覆盖物索引**挡不挡路/挡不挡放置** → bool（§283 玩家裁定）。

        判据全部来自 INI，四类：
          · `Wall=yes`（沙袋/围墙/铁丝网…）
          · `IsARock`（SROCK/TROCK/LUNRK）
          · **`Land=Rock` / `Land=Water`** —— 这些覆盖物会把整格地块改成岩石/水面
            （`FENCE01..22`、`PALET`、`DRUM`、`WCRATE` 都是这一档）⇒ 地面单位过不去
          · 无 INI 段的硬编码墙 `CYCL/BARB/WOOD/FENC`
        矿/宝石（`Tiberium=yes`）**不挡** —— 玩家明确"矿不影响放置单位和建筑"。
        桥面（`Overrides=yes`）/`Land=Road`/`Railroad` 也不挡。
        """
        if idx is None:
            return False
        info = self.overlay_type_info(ini_path).get(idx) or {}
        if info.get('wall') or info.get('is_rock'):
            return True
        if (info.get('land') or '').strip().lower() in ('rock', 'water'):
            return True
        return (info.get('name') or '').upper() in self.OVERLAY_HARD_WALLS

    def cell_ovl_blocked(self, cx, cy, ini_path=None, map_path=None):
        """该格是否被**挡路的覆盖物**占着 → 覆盖物信息 dict / None。

        与 `cell_overlay_info` 的区别：这里只看墙/石头（矿不算），给"能不能放"用。
        """
        o = self.cell_overlay_info(cx, cy, ini_path=ini_path, map_path=map_path)
        if not o or not o.get('blocks'):
            return None
        return o

    def overlay_free_cell_near(self, cx, cy, radius=8, ini_path=None, map_path=None,
                               need_empty=True):
        """找离 (cx,cy) 最近的**没有挡路覆盖物**的格 → `(x, y, dist)` / None。

        用途：**刷兵**。玩家 2026-10-26 定：刷兵落到墙/石头上时**引擎自己会改到相邻格**，
        所以编辑器也照办（自动挪），而手动放置则是**直接不让放**。
        `need_empty=True` 时还要求该格没被单位/建筑占着（`cell_taken`）。
        """
        sx, sy = int(cx), int(cy)
        if not self.cell_ovl_blocked(sx, sy, ini_path, map_path):
            if not need_empty or not self.cell_taken(sx, sy):
                return (sx, sy, 0)
        best = None
        for d in range(1, int(radius) + 1):
            cand = []
            for dx in range(-d, d + 1):
                for dy in range(-d, d + 1):
                    if max(abs(dx), abs(dy)) != d:
                        continue                     # 只看这一圈
                    x, y = sx + dx, sy + dy
                    if self.cell_offsets().get((x, y)) is None:
                        continue                     # 存档里没这格（地图外）
                    if self.cell_ovl_blocked(x, y, ini_path, map_path):
                        continue
                    if need_empty and self.cell_taken(x, y):
                        continue
                    cand.append((abs(dx) + abs(dy), dx, dy, x, y))
            if cand:
                cand.sort()
                return (cand[0][3], cand[0][4], d)
        return best

    def cell_overlay_info(self, cx, cy, ini_path=None, map_path=None):
        """信息栏/放置校验用：`{index,name,label,category,blocks,source}`。

        `blocks` 只表示"**已知会挡路**"（墙/石头），**不改落点**，只拿来提示；
        储存在格对象 `+0x58` 里；存档里这格没有覆盖物时不回退查地图（存档才是真相）。
        """
        idx = self.overlay_at(cx, cy)
        src = 'save'
        if idx is None:
            if self.cell_offsets().get((int(cx), int(cy))) is not None:
                return None            # 存档里有这格且是空的 ⇒ **以存档为准**，不查地图
            idx = self.map_overlay_at(cx, cy, map_path)
            src = 'map'
            if idx is None:
                return None
        info = self.overlay_type_info(ini_path).get(idx) or {}
        cat = self.overlay_category(idx, ini_path)
        blocks = self.overlay_blocks_placement(idx, ini_path)   # ← 单一判据，别两处各写一份
        return {'index': idx,
                'name': info.get('name') or ('#%d' % idx),
                'display': info.get('display') or '',
                'label': self.overlay_label(idx, ini_path),
                'category': cat,
                'blocks': blocks,
                'source': src}

    def cell_kind(self, cx, cy):
        """格对象的**占用种类掩码**（`+0x138`）→ int / None（没有该格对象）。

        ★★§275（2026-10-25 18:40，玩家实测"删完还占格子、别的单位过不去"）：

        | 值 | 含义 | 证据 |
        |---|---|---|
        | `0x20` | **有载具** | 574 台载具所在格 **574/574 全是 0x20** |
        | `0x80` | **有建筑** | 1113 栋建筑所在格 1088/1113 = 0x80 |
        | `0x01..0x08` | **有步兵**（同格最多 4 个各占一位） | 步兵格实测只用 0x1/0x2/0x4/0x8 |
        | `0x10` | **有 Terrain 对象**（树/石头/路牌…）★§281 | 2855 个纯 `0x10` 格里 **2855 个占用键都指向树** |
        | `0` | 空 | 35576/40653 空格是 0 |

        ★§281 修正：旧注释把 `0x10` 算进"步兵第 5 位"是错的 —— 那是**树的位**。
          依据：`all01umd.SAV` 3334 棵树，`+0x138` 全是 0x10 系（0x10/0x14/0x18/0x1c/0x90），
          而步兵格的位只在 0x1/0x2/0x4/0x8 里组合（仅 6 个例外，是"步兵占了树的格键"）。

        ⇒ 只清 `+248`（占用者键）**不够**：引擎读档时靠这个掩码判断"这格能不能过"，
        掩码留着 `0x20` 就会**看不见的墙**——删掉的车、搬走的车都会留下这种幽灵。
        """
        o = self.cell_offsets().get((int(cx), int(cy)))
        if o is None:
            return None
        return struct.unpack_from('<I', self.plain, o + self.CELL_OCC_KIND_OFF)[0]

    def _kind_bit_of(self, rec, before):
        """这台单位在 `+0x138` 掩码里占哪一位 → `(bit, note)`；判断不了就 `(None, 原因)`。

        · 载具 ⇒ `0x20`（固定，574/574）
        · 步兵 ⇒ 掩码里**只置了一位**时才敢认它（就是这一位）；同格多个步兵时
          "哪一位是它"没有可靠判据（记录里没有任何字段等于 bit 下标，已实测），**不猜**。
        """
        if rec.get('class') == 'vehicle':
            return self.CELL_KIND_VEHICLE, ''
        inf = int(before) & self.CELL_KIND_INF_MASK
        if inf and (inf & (inf - 1)) == 0:
            return inf, ''
        if not inf:
            return None, '该格掩码里没有步兵位'
        return None, '同格有多个步兵，位归属无可靠判据（不猜）'

    def clear_cell_kind(self, rec, cell=None):
        """把该单位在格 `+0x138` 掩码里占的那一位清掉（**不改变长度**）。

        返回 `{'cell','before','after','ok','note'}`；判断不了时 `ok=False` 且原样不动。
        """
        xy = cell if cell is not None else (rec.get('x'), rec.get('y'))
        if not xy or not isinstance(xy[0], int):
            return {'cell': xy, 'before': None, 'after': None, 'ok': False,
                    'note': '单位没有坐标'}
        o = self.cell_offsets().get((int(xy[0]), int(xy[1])))
        if o is None:
            return {'cell': xy, 'before': None, 'after': None, 'ok': False,
                    'note': '没有格对象'}
        p = o + self.CELL_OCC_KIND_OFF
        before = struct.unpack_from('<I', self.plain, p)[0]
        bit, note = self._kind_bit_of(rec, before)
        if bit is None:
            return {'cell': xy, 'before': before, 'after': before, 'ok': False, 'note': note}
        after = before & ~bit & 0xFFFFFFFF
        if after == before:
            return {'cell': xy, 'before': before, 'after': after, 'ok': True,
                    'note': '该位本来就没置'}
        struct.pack_into('<I', self.plain, p, after)
        return {'cell': xy, 'before': before, 'after': after, 'ok': True, 'note': ''}

    def set_cell_kind(self, rec, cx, cy):
        """把该单位在格 `+0x138` 掩码里占的那一位**置上**（移动/放置到新格时用）。

        载具置 `0x20`；步兵置**最低的空闲位**（1→2→4→8→16），与引擎自己分配的口径一致。
        """
        o = self.cell_offsets().get((int(cx), int(cy)))
        if o is None:
            return {'cell': (cx, cy), 'before': None, 'after': None, 'ok': False,
                    'note': '没有格对象'}
        p = o + self.CELL_OCC_KIND_OFF
        before = struct.unpack_from('<I', self.plain, p)[0]
        if rec.get('class') == 'vehicle':
            bit = self.CELL_KIND_VEHICLE
        else:
            bit = None
            for n in range(5):
                b = 1 << n
                if not (before & b):
                    bit = b
                    break
            if bit is None:
                return {'cell': (cx, cy), 'before': before, 'after': before,
                        'ok': False, 'note': '该格步兵位（0x01..0x10）已占满'}
        after = before | bit
        if after == before:
            return {'cell': (cx, cy), 'before': before, 'after': after, 'ok': True,
                    'note': '该位本来就置着'}
        struct.pack_into('<I', self.plain, p, after)
        return {'cell': (cx, cy), 'before': before, 'after': after, 'ok': True, 'note': ''}

    def set_unit_cell(self, rec, cx, cy, fix_pos_slots=True):
        """把**一台单位**挪到格 (cx, cy) → dict（零增长，记录长度不变，不触发偏移重算）。

        ★§232 铁律：位置在记录里共有 **6 处副本**，漏任何一处都会出现
          "看着在那里、实际进不了车 / 站在原地不动"：
            · `+176/+180`、`+616/+620` —— lepton x/y（低字节是子格偏移，原样保留）
            · `+224`                 —— 格键 ⇒ 改成新格格对象的自键
            · `+1392/+1394`          —— u16 格坐标
            · `+0x56C`/`+0x570`      —— 两个 u16 (x,y) 副本槽
              （§232 那个"工程师上不了 FV"就是 `+0x570` 被固化成 (0,0)）
            · 格对象 `+248`          —— 占用者自键：新格写自己，旧格若指向自己则清 0
        ★§275：`+248`（占用键）与 `+0x138`（占用种类掩码：载具 0x20 / 建筑 0x80 / 步兵 1..0x10）
          **必须一起维护** —— 只写 `+248` 会在旧格留下"看不见的墙"（玩家实测）。
          `+308`（`CELL_OCC_FLAG_OFF`）仍然不写（当年"写全 308/312 把克隆弄崩"的教训）。
        ⚠ 目标格必须在地图的格对象表里（否则引擎读档会把它挪走）。
        """
        cx, cy = int(cx), int(cy)
        cells = {v: k for k, v in self._cell_by_key().items()}
        nk = cells.get((cx, cy))
        if nk is None:
            raise RuntimeError('地图里没有格 (%d,%d) 的格对象，拒绝移动' % (cx, cy))
        pl = bytearray(self.plain)
        o = rec['off']
        if rec.get('size') and not (1000 <= rec['size'] <= 5000):
            raise RuntimeError('记录长度 %r 不像单位主体（步兵 1893 / 载具 2316），拒绝写入'
                               % (rec.get('size'),))
        old = (rec.get('x'), rec.get('y'))
        fx = pl[o + self.UNIT_LEPTON_X[0]] & 0xFF      # 子格偏移保留
        fy = pl[o + self.UNIT_LEPTON_X[0] + 4] & 0xFF
        for off in self.UNIT_LEPTON_X:
            struct.pack_into('<I', pl, o + off, (cx << 8) | fx)
            struct.pack_into('<I', pl, o + off + 4, (cy << 8) | fy)
        struct.pack_into('<I', pl, o + self.UNIT_CELLKEY_OFF, nk)
        struct.pack_into('<H', pl, o + self.UNIT_CELLXY_OFF[0], cx)
        struct.pack_into('<H', pl, o + self.UNIT_CELLXY_OFF[1], cy)
        if fix_pos_slots:
            for off in self.POS_SLOTS:
                struct.pack_into('<HH', pl, o + off, cx, cy)
        # ---- 格归属（`+248` 键 + `+0x138` 占用种类掩码，两者缺一不可，见 §275）----
        key = struct.unpack_from('<I', pl, o + 16)[0]
        cofs = self.cell_offsets()
        written = cleared = False
        kind_ops = []
        tgt = cofs.get((cx, cy))
        if tgt is not None:
            struct.pack_into('<I', pl, tgt + self.CELL_OCC_OFF, key)
            written = True
            kp = tgt + self.CELL_OCC_KIND_OFF
            kb = struct.unpack_from('<I', pl, kp)[0]
            if rec.get('class') == 'vehicle':
                bit = self.CELL_KIND_VEHICLE
            else:
                bit = next((1 << n for n in range(5) if not (kb & (1 << n))), None)
            if bit is not None and not (kb & bit):
                struct.pack_into('<I', pl, kp, kb | bit)
                kind_ops.append({'cell': (cx, cy), 'before': kb, 'after': kb | bit})
        if old and isinstance(old[0], int):
            oldc = cofs.get((old[0], old[1]))
            if oldc is not None and oldc != tgt:
                if struct.unpack_from('<I', pl, oldc + self.CELL_OCC_OFF)[0] == key:
                    struct.pack_into('<I', pl, oldc + self.CELL_OCC_OFF, 0)
                    cleared = True
                # ★§275：旧格的占用掩码也要清，否则那格变成"看不见的墙"
                kp = oldc + self.CELL_OCC_KIND_OFF
                kb = struct.unpack_from('<I', pl, kp)[0]
                _bit, _note = self._kind_bit_of(rec, kb)
                if _bit and (kb & _bit):
                    struct.pack_into('<I', pl, kp, kb & ~_bit & 0xFFFFFFFF)
                    kind_ops.append({'cell': (old[0], old[1]), 'before': kb,
                                     'after': kb & ~_bit & 0xFFFFFFFF})
        self.plain = pl
        self._blds = None
        return {'key': key, 'from': old, 'to': (cx, cy), 'cell_key': nk,
                'occ_written': written, 'old_cleared': cleared,
                'kind_ops': kind_ops, 'pos_slots': fix_pos_slots}

    def move_units_at(self, src_cell, dst_cell, fix_pos_slots=True):
        """把**源格上的所有单位**一起挪到目标格 → dict。

        为什么按格而不是按台：地图页点选的最小单位就是格子；同格多台一起搬
        符合"这里一堆兵挪到那边"的直觉，也省得做多选 UI。
        """
        src = (int(src_cell[0]), int(src_cell[1]))
        dst = (int(dst_cell[0]), int(dst_cell[1]))
        if src == dst:
            raise RuntimeError('源格和目标格是同一格 (%d,%d)' % src)
        recs = [r for r in self.unit_records()
                if isinstance(r.get('x'), int) and (r['x'], r['y']) == src]
        if not recs:
            raise RuntimeError('格 (%d,%d) 上没有单位' % src)
        done = [self.set_unit_cell(r, dst[0], dst[1], fix_pos_slots=fix_pos_slots)
                for r in recs]
        return {'from': src, 'to': dst, 'count': len(done), 'items': done}

    def unit_key_refs(self, rec):
        """该单位的**自键**在明文里出现的所有位置 → `[{'off','kind',…}]`（删除前的体检）。

        `kind` 取值：
          · `self`        —— **它自己记录里**（主体 + 伴随段）。AMCV 实测有 4 处：主体 `+0x3C0`、
                             伴随段 `+0x1C/+0x20` —— 是自我引用，随记录一起消失，不用管。
          · `cell+248`    —— 格对象占用者（正常 1 处；可能更多：它还被别的格记着）
          · `ptr_tab`     —— 两张全局指针表（各 1 处）
          · `roster`      —— 阵营对象表条目（**任务图里通常没有**）
          · `foreign_bld` —— ⚠ **某栋建筑记录里存了这个键**（实测是光棱塔的"当前目标"；
                             1113 栋里 1070 栋该字段为 0 ⇒ 是缓存的目标指针，清 0 安全）
          · `foreign_unit`—— ⚠ 另一台单位记录里存了这个键
          · `other`       —— 认不出来的位置（删之前必须人看）
        """
        pl = self.plain
        key = rec['key']
        pat = struct.pack('<I', key)
        pos, i = [], 0
        while True:
            j = pl.find(pat, i)
            if j < 0:
                break
            pos.append(j)
            i = j + 1
        cell_occ = dict((v + self.CELL_OCC_OFF, xy) for xy, v in self.cell_offsets().items())
        roster = {}
        try:
            for u in (self.house_placements(skip_special=False) or []):
                roster[u['off']] = u
        except Exception:
            roster = {}
        tabs = self._key_tables(key)
        tab_pos = set()
        for tb in tabs:
            for k in range(tb['n']):
                tab_pos.add(tb['first'] + 4 * k)
        # 别的单位记录范围
        ur = []
        try:
            for r in (self.unit_records() or []):
                if r is rec or r['off'] == rec['off']:
                    continue
                ur.append((r['off'], r['comp'] + r['comp_size'], r['type']))
        except Exception:
            ur = []
        # 建筑记录范围（用相邻 base 差值当长度，型号不同长度不同）
        br = []
        try:
            bs = sorted((b['base'], b.get('type')) for b in (self.buildings() or []))
            for i2, (o, t) in enumerate(bs):
                br.append((o, bs[i2 + 1][0] if i2 + 1 < len(bs) else len(pl), t))
        except Exception:
            br = []
        lo, hi = rec['off'], rec['comp'] + rec['comp_size']
        out = []
        for p in pos:
            d2 = {'off': p, 'kind': 'other'}
            if lo <= p < hi:
                d2['kind'] = 'self'
            elif p in cell_occ:
                d2['kind'] = 'cell+248'
                d2['cell'] = cell_occ[p]
            elif p in tab_pos:
                d2['kind'] = 'ptr_tab'
            elif p in roster:
                d2['kind'] = 'roster'
                d2['roster'] = roster[p]
            else:
                own_u = next(((a, b, t) for a, b, t in ur if a <= p < b), None)
                own_b = next(((a, b, t) for a, b, t in br if a <= p < b), None)
                if own_u:
                    d2['kind'] = 'foreign_unit'
                    d2['owner'] = own_u[2]
                    d2['rel'] = p - own_u[0]
                elif own_b:
                    d2['kind'] = 'foreign_bld'
                    d2['owner'] = own_b[2]
                    d2['rel'] = p - own_b[0]
            out.append(d2)
        return out

    def remove_unit(self, rec, clear_cell=True, clear_foreign=True,
                    keep_companion=False, clear_comp_refs=True):
        """从存档里**删掉一条单位记录（主体 + 伴随）** —— 与 `add_unit_by_clone` 严格对称。

        四步（★全部按"从大偏移到小"排序后执行：删字节会让后面的偏移前移）：
          ① 两张指针表各删掉该单位自键的 4 字节条目（`[u32 n][key×n]`），计数 −1；
          ② 阵营对象表删掉 `(类型键, y, x)` 那条（**任务图里通常本来就没有**，没有就跳过）；
          ③ 清格占用（格对象 `+248` 写 0 **并且清 `+0x138` 占用掩码**）—— 不涉及长度；
             ★§275 玩家实测：只清 `+248` 会让那格变成"看不见的墙"（删掉的车照样挡路）；
          ④ 删记录 `[off, comp + comp_size)`，组计数 −1。

        ★§274 `keep_companion=True` ⇒ **对照组**：只删主体 `size` 字节，把伴随对象留成**孤儿**
          （`soft_remove_unit()` 就是这个）。建筑那边当年的结论是"删伴随必弹框、只能留孤儿"，
          单位是否同样如此必须 A/B 实测，不能拿建筑的结论套（建筑有替身对象机制）。
          留孤儿时顺手把伴随里**回指该单位自键**的那几处清 0（`clear_comp_refs`），
          否则孤儿还在引用一个已经不存在的键。
        ⚠ 引用体检：如果 `unit_key_refs()` 里出现 `other`（别的对象在引用这台），
          这里会拒绝删除 —— 那种情况得先处理引用，否则读档会撞悬空指针。

        返回 dict；任何一步对不上就抛异常（调用方不得保存，存档不会被写坏）。
        """
        pl = bytearray(self.plain)
        off, size = rec['off'], rec['size']
        comp, csize = rec['comp'], rec['comp_size']
        if not (1000 <= size <= 5000 and 60 <= csize <= 400):
            raise RuntimeError('记录尺寸异常（size=%d comp_size=%d），拒绝删除' % (size, csize))
        key = struct.unpack_from('<I', pl, off + 16)[0]
        refs = self.unit_key_refs(rec)
        others = [r for r in refs if r['kind'] == 'other']
        foreign = [r for r in refs if r['kind'] in ('foreign_unit', 'foreign_bld')]
        if others:
            raise RuntimeError('该单位还被 %d 处认不出的地方引用（偏移 %s）⇒ 拒绝删除，'
                               '先把那些引用处理掉'
                               % (len(others), ', '.join('%#x' % r['off'] for r in others[:6])))
        if foreign and not clear_foreign:
            raise RuntimeError('该单位还被 %d 处外部对象引用（%s）⇒ 拒绝删除；'
                               '用 clear_foreign=True 让工具把它们清 0'
                               % (len(foreign),
                                  '、'.join('%s@+%#x' % (r.get('owner'), r.get('rel') or 0)
                                            for r in foreign[:4])))
        # ---- ⓪ 清掉指向它的外部引用（写 0，不改变长度 —— 必须在删字节之前做）----
        cleared_refs = []
        for r in foreign:
            struct.pack_into('<I', pl, r['off'], 0)
            cleared_refs.append('%s +%#x' % (r.get('owner'), r.get('rel') or 0))
        # ⓪b：留孤儿时，伴随里回指该键的那几处也要清 0（否则孤儿引用一个不存在的键）
        comp_zeroed = []
        if keep_companion and clear_comp_refs:
            for r in refs:
                if comp <= r['off'] < comp + csize:
                    struct.pack_into('<I', pl, r['off'], 0)
                    comp_zeroed.append('伴随 +%#x' % (r['off'] - comp))
        cnt_pos = self._unit_group_count_pos(off)
        if cnt_pos is None:
            raise RuntimeError('定位组计数失败（记录不在标准单位组里）⇒ 拒绝删除')
        n_grp = struct.unpack_from('<I', pl, cnt_pos)[0]

        ops = []      # (pos, kind, data, 说明)

        # ---- ① 指针表：删条目 + 计数 −1 ----
        tabs = self._key_tables(key)
        hit_tabs = 0
        for tb in tabs:
            for i2 in range(tb['n']):
                p = tb['first'] + 4 * i2
                if struct.unpack_from('<I', pl, p)[0] == key:
                    ops.append((p, 'del', 4, '指针表条目'))
                    ops.append((tb['count_pos'], 'dec', None, '指针表计数'))
                    hit_tabs += 1
                    break
        if not hit_tabs:
            raise RuntimeError('指针表里找不到该单位的键 %#010x ⇒ 拒绝删除' % key)

        # ---- ② 阵营对象表（有就删，没有不强求） ----
        gx, gy = struct.unpack_from('<HH', pl, off + 1392)
        roster_removed = 0
        try:
            hp = self.house_placements(skip_special=False) or []
            mine = [u for u in hp if u['type'].upper() == rec['type'].upper()
                    and u['x'] == gx and u['y'] == gy]
            if mine:
                ent = mine[0]['off']
                same_house = [u for u in hp if u['house'] == mine[0]['house']]
                first, last = min(u['off'] for u in same_house), max(u['off'] for u in same_house)
                cnt = first - 4
                if (struct.unpack_from('<I', pl, cnt)[0] == len(same_house)
                        and last + 8 == first + 8 * len(same_house)):
                    ops.append((ent, 'del', 8, '阵营对象表条目'))
                    ops.append((cnt, 'dec', None, '阵营对象表计数'))
                    roster_removed = 1
        except Exception:
            roster_removed = 0

        # ---- ③ 清格占用（`+248` 键 + `+0x138` 掩码；不涉及长度，位置固定）----
        cell_cleared = 0
        kind_cleared = []
        if clear_cell:
            cofs = self.cell_offsets()
            cells_to_clear = set([(gx, gy)])
            for r in refs:
                if r.get('cell'):
                    cells_to_clear.add(tuple(r['cell']))
            hit_cells = set()
            for (cx, cy) in cells_to_clear:
                co = cofs.get((cx, cy))
                if co is None:
                    continue
                if struct.unpack_from('<I', pl, co + self.CELL_OCC_OFF)[0] == key:
                    struct.pack_into('<I', pl, co + self.CELL_OCC_OFF, 0)
                    cell_cleared += 1
                hit_cells.add((cx, cy))
            # ★★§275：**占用种类掩码（+0x138）必须一起清**。玩家实测：只清 +248 的话，
            #   删掉的车格子照样"占着"，别的单位开不过去（一辆看不见的车）。
            #   载具位固定 0x20；步兵位只有在"该格只置了这一位"时才敢认（多步兵不猜）。
            for (cx, cy) in sorted(hit_cells):
                co = cofs.get((cx, cy))
                if co is None:
                    continue
                p = co + self.CELL_OCC_KIND_OFF
                kb = struct.unpack_from('<I', pl, p)[0]
                bit, note = self._kind_bit_of(rec, kb)
                if bit and (kb & bit):
                    after = kb & ~bit & 0xFFFFFFFF
                    struct.pack_into('<I', pl, p, after)
                    kind_cleared.append({'cell': (cx, cy), 'before': kb, 'after': after})
                elif bit is None and note:
                    kind_cleared.append({'cell': (cx, cy), 'before': kb, 'after': kb,
                                         'skipped': note
                                         if (kb & self.CELL_KIND_INF_MASK) else '该格没有对应位'})

        # ---- ④ 删记录（主体 + 伴随）+ 组计数 −1 ----
        if keep_companion:
            ln = size                                   # ★§274 对照组：只删主体
            ops.append((off, 'del', ln, '单位主体（伴随留作孤儿）'))
        else:
            ln = (comp + csize) - off
            ops.append((off, 'del', ln, '单位记录+伴随'))
        ops.append((cnt_pos, 'dec', None, '组计数'))

        # ---- 执行：从大偏移到小 ----
        ops.sort(key=lambda o: (-o[0], 0 if o[1] == 'dec' else 1))
        for pos, kind, data, _nm in ops:
            if kind == 'del':
                del pl[pos:pos + data]
            else:
                struct.pack_into('<I', pl, pos,
                                 struct.unpack_from('<I', pl, pos)[0] - 1)
        self.plain = pl
        # 长度变了 ⇒ 全套按偏移缓存作废（_invalidate_if_shifted 用长度做判据）
        self._invalidate_if_shifted()
        # ★★§274（自检抓到的真 bug）：删字节会让**触发/标签记录表**（`self.records`）整体错位。
        #   `_invalidate_if_shifted()` 只清按偏移缓存、**不会重扫记录表** ⇒ 删完再用
        #   `trigger_tag_pairs()` / 触发页读到的全是错位数据（本档 93 条变 5 条，落盘后重读又是 93）。
        #   只影响"内存里的下一步操作"，不影响已写出的文件，但会让 GUI 删完单位后触发页错乱。
        self.records = self._scan_records()
        self.pending_events.clear()
        self.pending_actions.clear()
        return {'mode': '删除单位（留孤儿伴随）' if keep_companion else '删除单位',
                'type': rec['type'], 'key': key,
                'cell': (gx, gy), 'rec_len': ln, 'size': size, 'comp_size': csize,
                'group_before': n_grp, 'group_after': n_grp - 1,
                'ptr_tables': hit_tabs, 'roster_removed': roster_removed,
                'cell_cleared': cell_cleared, 'delta': -ln,
                'kind_cleared': kind_cleared,
                'kept_companion': bool(keep_companion),
                'comp_zeroed': comp_zeroed,
                'cleared_refs': cleared_refs, 'refs': refs}

    def soft_remove_unit(self, rec, clear_cell=True, clear_foreign=True,
                         clear_comp_refs=True):
        """§274 **对照组**：只摘引用 + 删主体，**把伴随对象留成孤儿**（A/B 实测用）。

        与 `remove_unit()` 的差别只有一处：删的字节数是 `size`（主体）而不是 `size+comp_size`，
        外加把伴随里回指该键的几处清 0。其余（指针表 / 阵营表 / 格占用 / 组计数 / 外部引用）
        与整条删完全一致 ⇒ 两组唯一的变量就是"伴随留不留"。
        """
        return self.remove_unit(rec, clear_cell=clear_cell, clear_foreign=clear_foreign,
                                keep_companion=True, clear_comp_refs=clear_comp_refs)

    def unit_records(self):
        self._invalidate_if_shifted()
        """读出单位**实例记录** → [{'type','class','off','size','comp','comp_size','key','x','y'}]。

        2026-10-17 实测结构（A/B 定案，见 docs §57/§58）：
          组 = `[u32 n][主体×n]`，每个主体后面紧跟一个**伴随对象**（步兵 4A582744 / 载具 4A582741）。
          主体：类 GUID 16B | 自键 +16 | … | 坐标 +176/+180（定点 y/x） | 格键 +224 | 类型键在 +1748 附近。
        """
        pl = self.plain
        types = self.unit_types()
        # ★补全：`unit_types()` 靠 "Name:" 串扫会漏（65 只捞到 32），
        #   型别组枚举出来的名单更全 ⇒ 两者合并（步兵/载具以索引为准）
        for nm, v in self.unit_type_index().items():
            types[v['key']] = nm
        cells = self._cell_by_key()
        hname = self._house_key_names()
        # ★2026-10-17 20:4x 性能：大任务档（1956 单位 / 39MB 明文）原来 **49.5 秒**。
        #   瓶颈不是解析，而是"每条记录都从 ro+1500 开始 find 一遍伴随 GUID" ——
        #   1956 × 8 次 `bytes.find`，几乎每次都要扫到档尾 = 上亿次字节比较。
        #   改成：先把每个伴随 GUID 的**全部出现位置**扫成有序表（一次全档扫描 × 8），
        #   之后每条记录用 bisect 取"第一个 > ro+1500"的位置 ⇒ O(log n)。
        _comp_pos = {}
        for _cg in self.UNIT_COMP_GUIDS:
            _arr, _i = [], 0
            while True:
                _j = pl.find(_cg, _i)
                if _j < 0:
                    break
                _arr.append(_j)
                _i = _j + 1
            _comp_pos[_cg] = _arr
        _bisect = bisect.bisect_right
        out = []
        for guid, cguid, cls in self.UNIT_CLASSES:
            pos, i = [], 0
            while True:
                j = pl.find(guid, i)
                if j < 0:
                    break
                pos.append(j)
                i = j + 1
            # 同一类的记录连续成组：按间距切段，并要求段首前 4 字节 = 段内记录数
            runs, cur = [], []
            for p in pos:
                if cur and p - cur[-1] > 0x8000:
                    runs.append(cur)
                    cur = []
                cur.append(p)
            if cur:
                runs.append(cur)
            for run in runs:
                n = struct.unpack_from('<I', pl, run[0] - 4)[0] if run[0] >= 4 else -1
                if n != len(run):
                    continue                      # 布局不符 ⇒ 不认（宁可不给）
                for k, ro in enumerate(run):
                    nxt = run[k + 1] if k + 1 < len(run) else None
                    # ★2026-10-17 18:4x：伴随对象的类 = 该单位的 **Locomotor**（移动方式）！
                    #   地面载具 4A582741(133B) / 步兵 4A582744(81B) /
                    #   **空中 92612C46(173B)**（= 规则里 ZEP 的 Locomotor GUID）。
                    #   以前只认本类那一个伴随 GUID ⇒ `十个基洛夫.SAV` 只认出 1/10。
                    co = None
                    for _cg in (cguid,) + tuple(g for g in self.UNIT_COMP_GUIDS if g != cguid):
                        _arr = _comp_pos.get(_cg) or []
                        _k = _bisect(_arr, ro + 1500)          # 第一个 > ro+1500 的位置
                        if _k >= len(_arr):
                            continue
                        _c = _arr[_k]
                        if (nxt is None or _c < nxt) and (co is None or _c < co):
                            co = _c
                    if co is None:
                        continue
                    if nxt is None:
                        # 组尾 = 下一个对象组计数 = 下一个「类 GUID」前 4 字节
                        e = None
                        for q in range(co + 16, co + 0x1000):
                            if q + 8 > len(pl):
                                break
                            if pl[q + 6:q + 8] in (b'\xd2\x11', b'\xd1\x11'):
                                e = q
                                break
                        csize = (e - 4 - co) if e else 0
                    else:
                        csize = nxt - co
                    size = co - ro
                    # ★2026-10-17 18:2x：型号键有**固定偏移**（步兵 +1748 / 载具 +1752，见 §60），
                    #   优先直接读；以前只靠"在记录里扫第一个落在类型表里的值"，
                    #   个别记录会扫到无关的键 ⇒ 型号被标错（`10个大兵.SAV` 里 2 个 E1
                    #   被误标成 GAPILE / CABUNK01，害得只认出 8 个）。
                    tk = None
                    _tk_off = 1748 if cls == 'infantry' else 1752
                    if size > _tk_off + 4:
                        _v = struct.unpack_from('<I', pl, ro + _tk_off)[0]
                        if _v in types:
                            tk = _v
                    if tk is None:
                        for q in range(ro, ro + size - 4):
                            v = struct.unpack_from('<I', pl, q)[0]
                            if v in types:
                                tk = v
                                break
                    _vk = struct.unpack_from('<i', pl, ro + self.UNIT_VET_OFF[0])[0] \
                        if size > self.UNIT_VET_OFF[0] + 4 else -1
                    # ★★§269（2026-10-25）：**坐标读取加兜底** —— 原来只按 `+224` 格键查
                    #   `_cell_by_key()`，查不到就把 x/y 记成 None。实测本档 55 台这样"无坐标"，
                    #   包括玩家要删的那台盟军基地车 AMCV：
                    #     `+224 = 0x0000b380`（不是格对象自键！算下来是 (y<<8)|0x80），
                    #     但 `+1392/+1394` = (16,179)、`+0x570` = (16,179)、格 (16,179) 的
                    #     `+248` 正好是它的自键 —— 说明**位置其实是好的**，只是我们的读法太窄。
                    #   ⇒ `+1392/+1394`（u16 格坐标）兜底，55/55 全部有效。
                    #   `pos_src` 把来源记下来（'cell' / 'u16' / 'none'），方便别处区分。
                    _ck = struct.unpack_from('<I', pl, ro + 224)[0]
                    _pxy = cells.get(_ck) or (None, None)
                    _pos_src = 'cell'
                    if _pxy[0] is None:
                        _gx, _gy = struct.unpack_from('<HH', pl, ro + 1392)
                        if (_gx, _gy) != (0, 0):
                            _pxy = (_gx, _gy)
                            _pos_src = 'u16'
                        else:
                            _pos_src = 'none'
                    out.append({'type': types.get(tk, '?%08x' % (tk or 0)), 'class': cls,
                                'off': ro, 'size': size, 'comp': co, 'comp_size': csize,
                                # 等级：-1 / 越界一律给 None（引擎原生档里 SLAV 等出现过 -1）
                                'veterancy': _vk if _vk in self.VET_NAMES else None,
                                'comp_guid': bytes(pl[co:co + 16]),
                                'loco': self.COMP_GUID_LOCO.get(bytes(pl[co:co + 16]), '?'),
                                'key': struct.unpack_from('<I', pl, ro + 16)[0],
                                'cell': _ck,
                                'x': _pxy[0], 'y': _pxy[1], 'pos_src': _pos_src,
                                'house_key': struct.unpack_from('<I', pl, ro + self.UNIT_HOUSE_OFF[0])[0]
                                if size > self.UNIT_HOUSE_OFF[0] + 4 else 0,
                                'house': hname.get(
                                    struct.unpack_from('<I', pl, ro + self.UNIT_HOUSE_OFF[0])[0]
                                    if size > self.UNIT_HOUSE_OFF[0] + 4 else 0)})
        return out

    # ★★★ 2026-10-17 定案：单位实例的**所属方** = 记录 `+352` / `+560` 两份 House 对象键。
    #   证据（同一份档内的干净 A/B，`加尤里方天启档.SAV`）：
    #     玩家 E1  +352/+560 = 0x0EFCFAC0 = Americans
    #     敌方 APOC +352/+560 = 0x0EEC5178 = YuriCountry
    #   两张「指针表」是**全局**对象登记表（同一张表里同时含 E1 与 APOC 的键），
    #   ⇒ 改所属方**不用**动指针表；阵营布置表里本来就没有单位条目（只有建筑/Special）。
    UNIT_HOUSE_OFF = (352, 560)

    def _unit_group_count_pos(self, off):
        """给定一条单位主体记录的起点 `off`，返回它所属**组的计数位置**（= 组首 − 4）。

        ★ 2026-10-17 19:3x 修的 bug（连放两个载具后整组丢记录）：
          旧写法直接用 `模板记录 − 4` 当组计数，**只有模板恰好是组内第一条**时才对。
          模板是第 2 条以后时 ⇒ 计数没加、还把 +1 写进了**前一条记录的尾部字段**
          （实测：`多单位存档.SAV` 里 V3 是载具组第 3 条，放完 MTNK 后
           `unit_records()` 只剩 CLEG/E1 —— 载具组 n=3 但实际 4 条 ⇒ 整组不认）。
        """
        pl = self.plain
        for guid, _cguid, _cls in self.UNIT_CLASSES:
            # ⚠ UNIT_CLASSES 里存的是类 GUID 的**前缀**（8B），不是 16B 全量
            if bytes(pl[off:off + len(guid)]) != guid:
                continue
            pos, i = [], 0
            while True:
                j = pl.find(guid, i)
                if j < 0:
                    break
                pos.append(j)
                i = j + 1
            runs, cur = [], []
            for p in pos:
                if cur and p - cur[-1] > 0x8000:
                    runs.append(cur)
                    cur = []
                cur.append(p)
            if cur:
                runs.append(cur)
            for run in runs:
                if off not in run:
                    continue
                c = run[0] - 4
                if c < 0:
                    return None
                # 计数必须等于**整组**记录数（不是"到 off 为止"的条数）
                return c if struct.unpack_from('<I', pl, c)[0] == len(run) else None
            return None
        return None

    # ★★★ 2026-10-17 定案：单位记录的「每对象唯一 ID / 基址」（重载崩的真凶）
    #   判据（与建筑 +20/+972 同一套）：① 该值在整份明文里**只出现 1 次**（指针会出现多次）；
    #   ② 同一型号跨不同存档**取值不同**（类常量三档相同，如 +0/+20..+36/+988）。
    #   实测：步兵 (248, 1672, 1828)，载具 (248, 1320, 1672, 2332)；其中 +1672 恒等于基址+4。
    #   反证：外挂刷的那辆天启 +2332=0x10256030 / +1672=0x10256034 —— **外挂自己分配了全新基址**，
    #         所以它重载不崩；我们克隆时原样复制模板的基址 ⇒ 两个对象同 ID ⇒
    #         新开载入正常（不归并），**游戏内重载崩**（拆世界/重建时指针修补归并断言对不上）。
    UNIT_ID_OFF = {'infantry': (248, 1672, 1828),
                   'vehicle': (248, 1320, 1672, 2332)}
    UNIT_BASE_OFF = {'infantry': 1828, 'vehicle': 2332}
    # 需要换新基址的类 —— 已停用（实测换基址无效、且会把载具克隆弄崩）
    FRESH_BASE_CLASSES = ()
    UNIT_INST_ID_OFF = 36          # ★实例 ID（与建筑 +20 载荷帧 = g+36 是同一个字段/同一个计数器）

    def _fresh_unit_inst_id(self, blob, pl):
        """给克隆体分配**新的实例 ID**（记录 `+36`），原地覆写、零增长。

        引擎原生实测（`10个大兵.SAV`）：8 个 E1 依次 0xfa357…0xfa360，
        接着的建筑 0xfa361/0xfa364/0xfa367 ⇒ 单位与建筑**共用同一个计数器**，
        所以新 ID 要取「单位 + 建筑」两侧的最大值再顺延。
        """
        off = self.UNIT_INST_ID_OFF
        if len(blob) < off + 4:
            return None
        ids = []
        for r in self.unit_records():
            if r['size'] >= off + 4:
                v = struct.unpack_from('<I', pl, r['off'] + off)[0]
                if v:
                    ids.append(v)
        try:
            for b in self.buildings():          # 建筑：b 帧 = 载荷+56 = g+72 ⇒ g+36 = b-36
                g36 = b['base'] - 36
                if 0 <= g36 < len(pl) - 4:
                    v = struct.unpack_from('<I', pl, g36)[0]
                    if 0 < v < 0x1000000:
                        ids.append(v)
        except Exception:
            pass
        old = struct.unpack_from('<I', blob, off)[0]
        new = (max(ids) if ids else old) + 0x10
        for _ in range(65536):
            if pl.find(struct.pack('<I', new)) < 0 and new not in ids:
                struct.pack_into('<I', blob, off, new)
                return {'old': old, 'new': new}
            new += 0x10
        return None

    def _fresh_unit_ids(self, blob, cls, rec_size, pl):
        """给克隆体分配**全新且远离所有既有对象**的基址（身份），返回诊断信息。

        ★ 2026-10-17 17:2x 症状定案：两份同型号步兵里"攻击一个、目标却指向另一个"
          ⇒ 引擎按**基址（对象地址）**认对象，克隆与模板共用基址 = 同一个身份。
        建筑侧 `+20` 是计数器 ID（换新即可），单位这边是**地址**：既要换新，又要**不落在
        任何已有对象的地块里**。上一版用 `max+0x10`，正好压进模板自己的地块
        （模板 `+248` 就在 `基址+0x50`）⇒ 直接崩。所以这里留 0x10000 的间隔。

        写两处：`伴随+16` = 新基址、记录 `+1672` = 新基址 + 4（这个关系三份档都成立）。
        `+248` 只在"它本来就在模板自己的地块里"时才跟着平移（否则原样沿用）。
        """
        base_off = rec_size + 16                 # 伴随对象 +16 = 对象基址
        sub_off = 1672                           # = 基址 + 4
        if len(blob) < base_off + 4 or len(blob) < sub_off + 4:
            return None
        old_base = struct.unpack_from('<I', blob, base_off)[0]
        if not old_base:
            return None
        bases = []
        for r in self.unit_records():
            if r['size'] >= rec_size:
                b = struct.unpack_from('<I', pl, r['comp'] + 16)[0]
                if b:
                    bases.append(b)
        gap = 0x10000
        new_base = (max(bases) if bases else old_base) + gap
        for _ in range(4096):
            ok = all(abs(new_base - b) >= gap for b in bases)
            ok = ok and pl.find(struct.pack('<I', new_base)) < 0 \
                and pl.find(struct.pack('<I', new_base + 4)) < 0
            if ok:
                break
            new_base += gap
        else:
            return None
        delta = new_base - old_base
        struct.pack_into('<I', blob, base_off, new_base)
        struct.pack_into('<I', blob, sub_off, new_base + 4)
        moved = []
        v248 = struct.unpack_from('<I', blob, 248)[0]
        if v248 and abs(v248 - old_base) < 0x1000:      # 本来就在自己地块里 ⇒ 跟着搬
            struct.pack_into('<I', blob, 248, v248 + delta)
            moved.append(248)
        return {'old_base': old_base, 'new_base': new_base, 'delta': delta, 'moved': moved}

    def _house_key_names(self):
        self._invalidate_if_shifted()
        """{House 对象键: 阵营短名}（反查用，取不到就空表）。"""
        out = {}
        try:
            for nm, k in self.house_keys().items():
                if k:
                    out.setdefault(k, nm)
        except Exception:
            pass
        return out

    def house_key_of(self, house):
        """阵营短名 / 中文名 / `0x…` 键 → House 对象键；认不出返回 None。"""
        if house is None or house == '':
            return None
        if isinstance(house, int):
            return house
        s = str(house).strip()
        try:
            if s.lower().startswith('0x'):
                return int(s, 16)
        except ValueError:
            pass
        hk = self.house_keys()
        s2 = s[:-6].strip() if s.lower().endswith(' house') else s
        cn2short = {v: k for k, v in HOUSE_CN.items()}
        for nm, k in hk.items():
            if k and (nm.lower() == s2.lower() or s2 == nm or s == HOUSE_CN.get(nm)):
                return k
        if s2 in cn2short and hk.get(cn2short[s2]):
            return hk[cn2short[s2]]
        try:
            for h in self.houses():
                if not h.get('key'):
                    continue
                if s in (h.get('short'), h.get('cn'), str(h.get('key'))):
                    return h['key']
        except Exception:
            pass
        return None

    @staticmethod
    def guid_bytes(s):
        """`{92612C46-F71F-11d1-AC9F-006008055BB5}` → 存档里的 16 字节（前三段小端）。"""
        h = str(s).strip().strip('{}').replace('-', '')
        if len(h) != 32:
            return b''
        try:
            return (bytes.fromhex(h[0:8])[::-1] + bytes.fromhex(h[8:12])[::-1]
                    + bytes.fromhex(h[12:16])[::-1] + bytes.fromhex(h[16:32]))
        except ValueError:
            return b''

    def _type_rules(self, t):
        """读该型号在 rules 里的**全部属性**（大写键）→ dict；读不到返回 {}。

        ★2026-10-23 §198：换型号时必须先看 rules ——
          目标型号若声明了 `SPAWNS=`（发射出去的是**一个完整单位**，如 V3 的 `V3ROCKET`），
          克隆换型号会缺那个子单位 ⇒ 引擎读档弹框（玩家实测 V3：`Access address 0x48`、`ecx=0`）。
        """
        try:
            import os
            import game_ini
            g = game_ini.get_types(os.path.dirname(getattr(self, 'path', '') or ''))
            kv = g.rules.get(str(t).lower()) or {}
            if isinstance(kv, dict) and isinstance(kv.get('kv'), dict):
                kv = kv['kv']
            return {str(k).upper(): v for k, v in kv.items()} if isinstance(kv, dict) else {}
        except Exception:
            return {}

    def _type_spawns(self, t):
        """该型号"发射出去的是一个完整单位"时返回那个单位的名字（如 V3 → V3ROCKET），否则 ''。"""
        return str(self._type_rules(t).get('SPAWNS') or '').strip()

    def _type_strength(self, t):
        """目标型号的满血值（规则 `Strength=`）；查不到返回 0。"""
        try:
            import os
            import game_ini
            g = game_ini.get_types(os.path.dirname(getattr(self, 'path', '') or ''))
            kv = g.rules.get(str(t).lower()) or {}
            if isinstance(kv, dict) and isinstance(kv.get('kv'), dict):
                kv = kv['kv']
            v = str(kv.get('strength') or '').strip()
            return int(v) if v.isdigit() else 0
        except Exception:
            return 0

    def comp_library(self):
        """内置「伴随对象（移动方式）」模板库 → {Loco: {'bytes','guid','size','sample'}}。"""
        if getattr(self, '_complib', None) is not None:
            return self._complib
        try:
            import unit_comp_lib
        except Exception:
            try:
                from core import unit_comp_lib
            except Exception:
                self._complib = {}
                return self._complib
        self._complib = unit_comp_lib.load()
        return self._complib

    # ★§200：型号 → 存档里**实际使用的伴随类**（覆盖 rules 的 Locomotor）。
    #   只放"与真实样本逐字节比对过"的型号；加新条目必须有样本依据。
    #   ★★ 2026-10-23 玩家实测纠错：**表已清空，ZEP 必须 Fly**。
    #     曾经为了修"基洛夫隐形"把 ZEP 钉成 Drive（依据是一份档里疑似 Drive 的样本），
    #     结果 T54 里基洛夫**出来了但在地上跑、不起飞**（玩家：移动方式不对）。
    #     复核 `放V3和ZEP.SAV`（玩家自己在地图上摆的）：真实 ZEP 的伴随 = **Fly / 173 B**
    #     （`462c6192…`），与同档 60 多架 SCHP 完全一致 ⇒ rules 的 `Locomotor=` 就是对的。
    #     当初"隐形"的真因是**落点太靠地图边缘**（(15,179)），不是移动方式。
    _LOCO_OVERRIDE = {}

    def _type_locomotor(self, t, recs):
        """目标型号要的**移动方式**（Walk / Drive / Fly …）——决定要借哪种伴随对象。

        ★★ 2026-10-23 §200 **顺序改了**：**规则文件优先，档内样本只作兜底**。
          旧顺序（先看档内实例）会被"一个脏样本"绑架：T54 里我们自己用 Drive 放的
          那个基洛夫，反过来成了下一轮的"权威样本"，于是新放的基洛夫继续在地上跑
          （玩家实测：「基洛夫出来了，移动方式不对，不起飞」）。
          实测依据：玩家亲手摆的真基洛夫 = **Fly / 173 B**，与 rules
          `[ZEP] Locomotor={92612C46…}`（Fly）完全一致 ⇒ **规则才是权威**。
        """
        tt = str(t).upper()
        _ov = self._LOCO_OVERRIDE.get(tt)
        if _ov:
            return _ov
        try:
            import os
            import game_ini
            g = game_ini.get_types(os.path.dirname(getattr(self, 'path', '') or ''))
            sec = g.rules.get(str(t).lower()) or {}
            if isinstance(sec, dict) and isinstance(sec.get('kv'), dict):
                sec = sec['kv']          # 规则段的真实键值在 'kv' 里
            loco = (sec.get('locomotor') or '').strip()
            if loco:
                _m = self.COMP_GUID_LOCO.get(self.guid_bytes(loco))
                if _m:
                    return _m
            if (sec.get('speedtype') or '').strip().lower() == 'foot':
                return 'Walk'
        except Exception:
            pass
        # 规则文件读不到（自制单位 / 没有原版 INI）⇒ 退到档内实例，**取多数**
        # （避免像 T54 那样被单个脏样本带偏）
        cnt = {}
        for r in recs:
            if r['type'].upper() == tt and r.get('loco') not in (None, '?'):
                cnt[r['loco']] = cnt.get(r['loco'], 0) + 1
        if cnt:
            return max(cnt, key=cnt.get)
        return None

    def set_unit_house(self, unit, house):
        """把某个单位实例的**所属方**改成目标阵营（`+352`/`+560` 两份同改）。

        `unit` = `unit_records()` 里的 dict（用其 `off`）或记录自键（int）。
        返回 {'house','house_key','off','from','changed'}；认不出阵营则抛 ValueError。
        """
        tgt = self.house_key_of(house)
        if not tgt:
            raise ValueError('认不出所属方 %r（已知：%s）'
                             % (house, '、'.join(sorted(k for k in self.house_keys() if k))))
        recs = self.unit_records()
        if isinstance(unit, dict):
            off = unit['off']
        else:
            cand = [r for r in recs if r['key'] == unit]
            if not cand:
                raise ValueError('找不到自键 %#x 的单位记录' % unit)
            off = cand[0]['off']
        pl = bytearray(self.plain)
        old = struct.unpack_from('<I', pl, off + self.UNIT_HOUSE_OFF[0])[0]
        for o in self.UNIT_HOUSE_OFF:
            struct.pack_into('<I', pl, off + o, tgt)
        self.plain = pl
        self._blds = None
        nm = self._house_key_names()
        return {'off': off, 'house': nm.get(tgt, house), 'house_key': tgt,
                'from': nm.get(old, '0x%08X' % old),
                'changed': int(old != tgt)}


    TYPE_GROUP_INF = bytes.fromhex('d8338bae1c06d211aca4006008055bb5')   # InfantryTypeClass 型别组
    TYPE_GROUP_UNIT = bytes.fromhex('ea42bddc4605d211aca4006008055bb5')  # UnitTypeClass 型别组

    def unit_type_index(self):
        """{类型名: {'key','off','class'}} —— 完整类型索引（步兵 / 载具）。

        2026-10-17 定案：**型别组内每个对象都以同一个类 GUID 开头**，且 `+56` 就是类型名
        ⇒ 直接枚举「该类 GUID 的全部出现位置」即可拿到**完整**名单
        （`unit_types()` 靠 `Name:` 串扫只能捞到一部分：65 只捞到 32）。
        """
        cached = getattr(self, '_utindex', None)
        if cached is not None:
            return cached
        pl = self.plain
        out = {}
        for guid, cls in ((self.TYPE_GROUP_INF, 'infantry'), (self.TYPE_GROUP_UNIT, 'vehicle')):
            i = 0
            while True:
                o = pl.find(guid, i)
                if o < 0:
                    break
                i = o + 1
                nm = bytes(pl[o + 56:o + 72]).split(b'\x00')[0].decode('ascii', 'ignore')
                if not nm:
                    continue
                out.setdefault(nm.upper(), {'key': struct.unpack_from('<I', pl, o + 16)[0],
                                            'off': o, 'class': cls})
        self._utindex = out
        return out

    def unit_type_display(self, name):
        """存档自带该型号的**显示名**（类型对象 `+120`，运行时已按 CSF 解析过）。

        用途：型号可能在 rules 里找不到（地图里写死的自制单位），那就用存档自带的名字。
        """
        v = self.unit_type_index().get(str(name).upper())
        if not v:
            return ''
        pl = self.plain
        o = v['off']
        s = bytes(pl[o + 120:o + 160]).split(b'\x00')[0]
        return s.decode('latin-1', 'ignore').strip()

    def type_display(self, name):
        """类型对象的**显示名**（存档里 `+120`，引擎按 UI 文本表解析过）→ str（读不到返回 ''）。

        定位法：明文里找 `Name:XXX\0`，**对象起点 = 该串位置 − 81**（实测：`+56` 的 16B 代码名、
        `+80` 的 `Name:XXX`、`+120` 的显示名，逐字节对齐后差 81 —— 早先按 −80 取会吃掉首字母）。
        覆盖**建筑/步兵/载具**全部类型（`unit_type_display()` 只走 infantry/vehicle 两个型别组）。
        ★2026-10-23 §205：GUI 里那张中文对照表有个别是**猜的**（`YACOMD` 被写成"尤里建造场?"），
          玩家当场纠正：YACOMD 是**尤里指挥中心**、尤里建造场是 `YACNST`。
          ⇒ 现在**任何表里没有的类型都能回落到这里的真名**，宁可显示英文名，也不猜中文。
        """
        memo = getattr(self, '_typedisp', None)
        if memo is None:
            import re as _re
            memo = {}
            for mo in _re.finditer(b'Name:([A-Za-z0-9_]{2,24})\x00', self.plain):
                code = mo.group(1).decode('ascii', 'ignore').upper()
                off = mo.start() - 81
                if off < 0 or off + 160 > len(self.plain):
                    continue
                s2 = bytes(self.plain[off + 120:off + 160]).split(b'\x00')[0]
                txt = s2.decode('latin-1', 'ignore').strip()
                if txt and code not in memo:
                    memo[code] = txt
            self._typedisp = memo
        return memo.get(str(name).upper(), '')

    def placeable_unit_types(self):
        """**所有**可放置的单位类型名（步兵 + 载具，按类分组）→ [(名, 'infantry'|'vehicle')]。

        与 `units_placeable()` 不同：这个不看"档里有没有该型号"，
        只要类型表里有、且能归到步兵/载具类，就能借同类模板 + 换型号键放出来。
        """
        idx = self.unit_type_index()
        return [(nm, v['class']) for nm, v in sorted(idx.items()) if v['class']]

    def units_placeable(self):
        """本档**能克隆放置**的单位类型（= 档内已经存在的单位类型名，去重）。"""
        seen, out = set(), []
        for r in self.unit_records():
            t = r['type']
            if t and not t.startswith('?') and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _pick_registered(self, recs, limit=400):
        """在候选里挑第一条**已进全局登记表**（≥2 张表）的记录。

        ★2026-10-17 21:4x：大任务档里有一部分单位不在任何登记表里（实测 76/1956，
          例如 15 个 SLAV 全都没登记：多半待在建筑里/待定状态），
          拿它们当克隆模板会连带继承"未登记"的状态 ⇒ 新单位引擎不认领。
          ⚠ 上限要够大：按记录顺序取前几条很容易全是没登记的，`limit` 太小会白跑。
        """
        for r in recs[:limit]:
            try:
                if len(self._key_tables(r['key'])) >= 2:
                    return r
            except Exception:
                continue
        return None

    def sweep_key_refs(self, keys):
        """把若干**对象键**从所有认得出的登记表里剔掉 → 剔了几处。

        ★§288：删建筑/删单位之后，只要还有一处残留，游戏加载器就会在"键归并"那步
          断言失败 ⇒ 玩家看到的就是 `Internal error` 弹框。所以这件事必须做到底，
          而且要能独立调用（GUI 的「清理残留引用」、自检都用它）。
        """
        pl = self.plain
        removed = 0
        for _round in range(2000):
            hit = None
            for k in sorted(keys, reverse=True):
                pat = struct.pack('<I', k)
                # a) 认得出的键表（段长 == 段首计数）
                for tb in self._key_tables(k):
                    ent, cpos, n = tb['first'], tb['count_pos'], tb['n']
                    for i in range(n):
                        if struct.unpack_from('<I', pl, ent + 4 * i)[0] == k:
                            hit = (ent + 4 * i, cpos, k)
                            break
                    if hit:
                        break
                if hit:
                    break
                # b) 单条键表 `[u32 1][key]`
                i = bytes(pl).find(pat)
                while i >= 0:
                    if i >= 4 and struct.unpack_from('<I', pl, i - 4)[0] == 1:
                        hit = (i, i - 4, k)
                        break
                    i = bytes(pl).find(pat, i + 1)
                if hit:
                    break
            if not hit:
                break
            pos, cpos, k = hit
            cnt = struct.unpack_from('<I', pl, cpos)[0]
            struct.pack_into('<I', pl, cpos, cnt - 1 if cnt > 1 else 0)
            del pl[pos:pos + 4]
            removed += 1
        return removed

    def leftover_refs(self, keys):
        """这些键在明文里还剩几处（>0 ⇒ 载入必弹框）。"""
        pl = bytes(self.plain)
        out = {}
        for k in keys:
            pat = struct.pack('<I', k)
            hits, i = [], pl.find(pat)
            while i >= 0 and len(hits) < 4:
                hits.append(i)
                i = pl.find(pat, i + 1)
            if hits:
                out[k] = hits
        return out

    def _key_tables(self, key):
        """包含该对象键的「指针表」→ [{'count_pos','first','n'}]（加载器归并断言要求 A⊆B）。

        结构：`[u32 n][key × n]`。

        ★★ 2026-10-17 21:3x 重写（大任务档终于放行）：
          旧写法只在"键前面 64 条以内"找计数，等于**只认识表头 64 条**。
          大任务档的登记表有 **8642 / 5189 条**，绝大多数键落在中间 ⇒ 一张都认不出。
          改成**先按"连续合法键区"左右扩张出整段，再用段首前 4 字节 == 段长**认定表头：
          与键在表里的位置无关，见到表头即可；段长/计数不符的一律不认（宁缺勿错）。
        """
        pl = self.plain
        pat = struct.pack('<I', key)
        out, seen = [], set()
        i = 0
        while True:
            j = pl.find(pat, i)
            if j < 0:
                break
            i = j + 1
            # 左右扩张：连续"像对象键"的 u32
            s = j
            while s - 4 >= 0:
                v = struct.unpack_from('<I', pl, s - 4)[0]
                if 0x01000000 <= v <= 0x90000000:
                    s -= 4
                else:
                    break
            e = j
            while e + 4 <= len(pl):
                v = struct.unpack_from('<I', pl, e)[0]
                if 0x01000000 <= v <= 0x90000000:
                    e += 4
                else:
                    break
            # 段首附近找"计数 == 段长"的位置（最多回溯 64 条，防止前面粘着别的键数组）
            for k in range(0, 64):
                st = s + 4 * k
                if st - 4 < 0:
                    break
                n = (e - st) // 4
                if n < 2:
                    break
                if (st - 4) in seen:
                    continue
                if struct.unpack_from('<I', pl, st - 4)[0] == n:
                    seen.add(st - 4)
                    out.append({'count_pos': st - 4, 'first': st, 'n': n})
                    break
        return out

    def _occupancy(self):
        """{(x,y)} 已被**建筑地基**或**单位**占用的格（引擎口径，带缓存）。

        与「格 +0xF8」不是一回事：格表里干净、但落在某建筑地基里的格子很常见，
        往那儿放单位会被引擎挤掉/销毁。建筑地基取自 `foundation_of()`。
        """
        memo = getattr(self, '_occ_memo', None)
        if memo is not None and memo[0] == len(self.plain):
            return memo[1]
        occ = set()
        for b in self.buildings():
            try:
                w, h = self.foundation_of(b['type'])
            except Exception:
                w, h = 1, 1
            for i in range(w):
                for j in range(h):
                    occ.add((b['x'] + i, b['y'] + j))
        for u in self.unit_records():
            if u['x'] is not None:
                occ.add((u['x'], u['y']))
        # ★§281：**Terrain 对象（树/石头/路牌）也占格** —— 玩家口径"树是地形对象"。
        #   实测：格 +0x138 的 `0x10` 位就是它，2855 个纯 0x10 格的占用键**全部**指向树。
        #   以前这里不含树 ⇒ `cell_taken()` 认不出（3 棵树 0/3、大档 2/3334），
        #   于是编辑器允许把单位/建筑放到树上（游戏里会叠着或被挤掉）。
        try:
            for t in self.terrain_objects():
                occ.add((t['x'], t['y']))
        except Exception:
            pass
        self._occ_memo = (len(self.plain), occ)
        return occ

    # ★★★★ 2026-10-23 §210（玩家指出"放的地方和游戏里对不上、那里是悬崖"）：
    #   地形一直是盲区（§197 记过）：引擎的**可通行性**在 `.MAP [IsoMapPack5]` 里，
    #   存档只看得到"格子上有没有占位对象"。放在水/悬崖/不可通行格 ⇒ 引擎读档时把单位
    #   挪到最近的合法格（玩家看到的就是"位置偏了几格 + 那个地方是悬崖"），甚至干脆不画。
    #   下面三个接口把地图地形读进来（lzo 解压，一次缓存），供放置前校验/GUI 提示。
    def map_cells(self, map_path=None):
        """{ (x,y): (tile, sub, z, level) } —— 场景地图的地形（带缓存，见 `read_isomap`）。

        ★`level` = **层高**（0=地面，1..27=高处/坡/桥面）。判定"能不能走过去"必须看它。
        """
        key = map_path or getattr(self, '_mapterr_path', None)
        if key is None:
            try:
                key = self.scenario_map_path()
            except Exception:
                key = None
        if not key:
            return {}
        if getattr(self, '_mapterr_path', None) == key and getattr(self, '_mapterr', None) is not None:
            return self._mapterr
        d = read_isomap(key)
        self._mapterr, self._mapterr_path = d, key
        return d

    def cell_terrain(self, cx, cy):
        """(tile, sub, z, level) 或 None。

        ★§278：地图里没有这一格（或根本没找到地图）时，**回退用存档格对象的 tile**，
          这样"没 .MAP 也能判地形"（level 本来就一直读存档）。
        """
        c = self.map_cells().get((int(cx), int(cy)))
        if c:
            return c
        t = self.cell_tile(cx, cy)
        if t is None:
            return None
        return (t, 0, 0, self.cell_level(cx, cy))

    # ★★2026-10-26 §279 修：存档格对象自带层高，`格+0x12E` 的 u16 = `X | (level << 8)`
    #   —— **层高在高字节（0x12F）**，低字节是最多 12 的小索引（0..12，疑似子格/结构标志）。
    #   旧代码取的是 `& 0xFF`（低字节）⇒ **一直读错**：本档 level=8 的格读成 1..6。
    #   实测（level≠0 的 200 格）：高字节 == 地图 level **200/200**，低字节只对 1/60。
    #   ⚠ 旧注释里"与地图一致率 98.22%"是假象 —— 全档绝大多数格 level 本来就是 0。
    CELL_LEVEL_OFF = 0x12E      # u16：低字节 = X(0..12)，**高字节 = level**
    CELL_TILE_OFF = 0x4C        # u16：地形 tile 索引（与地图 IsoMapPack5 第 1 项一致率 97%）

    def cell_level(self, cx, cy):
        """存档格对象里的层高（`格+0x12F` 字节）；没有这格返回 None。

        0 = 地面；>0 = 高处/坡/桥面。桥面(1262)=11、(116,211)引桥(1203)=3 之类。
        """
        o = self.cell_offsets().get((int(cx), int(cy)))
        if o is None:
            return None
        return struct.unpack_from('<H', self.plain, o + self.CELL_LEVEL_OFF)[0] >> 8

    def cell_levels(self):
        """{ (x,y): level } —— 全图存档层高（一次扫完，比逐格调 cell_level 快）。"""
        out = {}
        pl = self.plain
        for xy, o in self.cell_offsets().items():
            out[xy] = struct.unpack_from('<H', pl, o + self.CELL_LEVEL_OFF)[0] >> 8
        return out

    def cell_tile(self, cx, cy):
        """存档格对象里的**地形 tile 索引**（`格+0x4C` u16）；没有这格返回 None。"""
        o = self.cell_offsets().get((int(cx), int(cy)))
        if o is None:
            return None
        return struct.unpack_from('<H', self.plain, o + self.CELL_TILE_OFF)[0]

    def terrain_from_save(self, theater=None):
        """★★§278 只用**存档自己**重建地形 → 与 `map_render.parse_map()` 同形状的 dict。

        `{'size': (W,H), 'terrain': {(x,y): (tile, sub, level)}, 'theater': …, 'has_terrain': True}`

        为什么要它：玩家把存档挪进子文件夹后地图文件常常找不到，以前地图页就只剩网格。
        实测（`SAVE4823` + `ALL01UMD.MAP`）：tile 一致率 **97.0%**（41612/42901）、
        level **100%**；按它渲染再与 .MAP 渲染逐像素比，**只差 1.10%**。
        ★差的那些不是我们读错：1289 格是引擎挑了"同族 tile 变体"（见 docs §279），
          179 格是引擎补的 tile=0 空白格 —— 存档里的值才是游戏里真正显示的那个。
        """
        memo = getattr(self, '_terrsav', None)
        if memo is not None and memo[0] == len(self.plain) and memo[1] == theater:
            return memo[2]
        cofs = self.cell_offsets()
        pl = self.plain
        terr = {}
        for xy, o in cofs.items():
            terr[xy] = (struct.unpack_from('<H', pl, o + self.CELL_TILE_OFF)[0],
                        0,
                        struct.unpack_from('<H', pl, o + self.CELL_LEVEL_OFF)[0] >> 8)
        if cofs:
            W = max(x for x, _ in cofs) + 1
            H = max(y for _, y in cofs) + 1
        else:
            W = H = 0
        mp = {'path': '<from-save>', 'size': (W, H), 'theater': theater or 'NEWURBAN',
              'terrain': terr, 'has_terrain': bool(terr), 'structures': [],
              'info': {}, 'name': ''}
        self._terrsav = (len(pl), theater, mp)
        return mp

    def bridge_like_tiles(self):
        """这份地图里"属于桥结构"的 tile 集合（tile 上有层高、且成片连续）。

        ★用途：解释/预警"兵上不了车、走不到某格"。桥面格地面单位**只能从引桥进**，
        不能从旁边的地面平着跨上去，即使坐标相邻、层高都是 0 也不行。
        """
        mk = self.map_cells()
        lv = self.cell_levels()
        out = set()
        for xy, t in mk.items():
            if lv.get(xy, 0) > 0:
                out.add(t[0])
        return out

    def walkable_tiles(self):
        """这份档里**真实地面单位/建筑踩过**的 tile 集合 —— 不需要 theater INI 也能判可通行。

        依据：地面单位（vehicle/infantry）站着的格子一定是可通行的；建筑地基同理。
        反过来"从没被踩过的 tile"就高度可疑（水/悬崖/深坑）。
        """
        if getattr(self, '_walktiles', None) is not None:
            return self._walktiles
        mk = self.map_cells()
        s = set()
        if mk:
            for r in self.unit_records():
                if r.get('x') is None:
                    continue
                if r.get('class') in ('vehicle', 'infantry'):
                    t = mk.get((r['x'], r['y']))
                    if t:
                        s.add(t[0])
            for b in self.buildings():
                t = mk.get((b['x'], b['y']))
                if t:
                    s.add(t[0])
        self._walktiles = s
        return s

    def terrain_warning(self, cx, cy, air=False):
        """放置前的地形体检 → 告警文本（没问题返回 ''）。"""
        mk = self.map_cells()
        if not mk:
            return ''
        t = mk.get((int(cx), int(cy)))
        if t is None:
            return '⚠ 地图里没有 (%d,%d) 这一格（在可玩区之外）⇒ 引擎会把单位挪走或直接不画' % (cx, cy)
        tile, sub, z, icr = t
        ok = self.walkable_tiles()
        if ok and tile not in ok:
            return ('⚠ (%d,%d) 的地形（tile %d）在这份档里**从没有地面单位踩过** —— '
                    '很可能是水/悬崖/不可通行格。实测放在这种格上，引擎读档时会把单位'
                    '挪到旁边的合法格（看起来就是"位置偏了几格"），严重时干脆不显示。'
                    '建议换一格：靠近已有单位站的格子。' % (cx, cy, tile))
        return ''

    def cell_taken(self, cx, cy):
        """该格是否被建筑地基/单位占着（O(1)，走 `_occupancy()` 缓存）。"""
        return (int(cx), int(cy)) in self._occupancy()

    # ★★2026-10-25 §271：可见区域（玩家俗称「蓝线」）= 地图 `[Map] LocalSize`。
    def map_local_size(self, path=None):
        """地图 `[Map] LocalSize=左,上,宽,高`（**地编 tile 坐标**）→ tuple 或 None。

        ★**以地图文件为准**（玩家 2026-10-25 16:17 明确要求）：
          · `path` 给了就按它读（GUI 传"当前地图页打开的那张图"）；
          · 没给就用 `scenario_map_path()`（存档对应的那张）；
          · `_map` 已挂载且就是同一个文件时直接用现成对象；
          · 都读不到 ⇒ 返回 None（**调用方不拦人**，不猜）。
        结果按"路径"缓存在 `_localsize_memo`，换图自动失效。
        """
        try:
            p = path or self.scenario_map_path()
        except Exception:
            p = path
        memo = getattr(self, '_localsize_memo', None)
        if memo is not None and memo[0] == p:
            return memo[1]
        ls = None
        try:
            m = self._map
            if m is not None and (p is None or getattr(m, 'path', None) == p):
                ls = m.local_size()
        except Exception:
            ls = None
        if not ls and p and os.path.exists(p):
            try:
                import map_ini
                ls = map_ini.MapIni(p).local_size()
            except Exception:
                ls = None
        self._localsize_memo = (p, ls)
        return ls

    def cell_visible(self, cx, cy, map_path=None):
        """该格是否在**可见区域（蓝线）内** → True / False / None（地图读不出来就别拦）。

        ★换算（§271 玩家标定）：**存档格坐标 = 地编 tile 坐标 × 2**。
          验证：`SAVE4823` 那张图 `LocalSize=5,4,174,107` ⇒ 地编 x∈[5,179)、y∈[4,111)
          ⇒ 存档坐标 x∈[10,358)、y∈[8,222)。
          玩家给的三个点全部吻合：`(16,179)` 界内 ✔、`(1,180)` 界外 ✔、`(19,177)` 界内 ✔。
          （按 ×1 换算则 `(16,179)` 的 y=179 > 111 会被判界外，与实测矛盾 ⇒ 只有 ×2 成立。）
        `map_path`：GUI 传"当前打开的地图"，做到**以你看到的那张图为准**。
        """
        ls = self.map_local_size(map_path)
        if not ls:
            return None
        l, t, w, h = ls
        return (2 * l) <= int(cx) < 2 * (l + w) and (2 * t) <= int(cy) < 2 * (t + h)


    def cell_flat_info(self, cx, cy):
        """落点"是不是平地"的体检（§251）→ dict。

        ★★2026-10-24 §251 定案（玩家 T105 A/B 实测，此前 6 轮全是这个坑）：
          **把单位放在"层高与八邻不一致"的格子上（斜坡/悬崖/引桥），打掉它会弹框**；
          放在全平地（层高 0、八邻也 0）则一切正常。而且它还会"有目标"——引擎认为站位
          不合法，一直想把它挪走，看起来就像"有目的地、往外飞"。

          实证（T105 同一档、同一套克隆配方，**唯一差别是落点**）：
            (104,212) tile 1265 层高 **7**，八邻 8/0/0/4 = 悬崖边 ⇒ **打掉弹框**
            (108,206) 层高 1 ⇒ 正常      (109,206) 层高 0 ⇒ 正常
          反证：玩家验收通过的 T97/T99 落点都是**八邻全 0 的平地**。

        ⚠ 这个坑特别难查，因为 `free_cell_near(walkable)` 只判"可通行"，
          而 tile 1265 这种**斜坡是可以通行的** —— 于是落点校验一路绿灯。
        """
        cx, cy = int(cx), int(cy)
        lv = self.cell_level(cx, cy)
        t = self.cell_terrain(cx, cy)
        nb = {}
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                v = self.cell_level(cx + dx, cy + dy)
                if v is not None:
                    nb[(dx, dy)] = v
        if lv is None:
            return {'level': None, 'tile': None, 'nb': nb, 'is_flat': False,
                    'why': '这一格不在存档里（地图外/虚空）'}
        if not nb:
            return {'level': lv, 'tile': t[0] if t else None, 'nb': nb,
                    'is_flat': lv == 0, 'why': ''}
        diff = sorted({v for v in nb.values()} - {lv})
        if diff:
            return {'level': lv, 'tile': t[0] if t else None, 'nb': nb,
                    'is_flat': False,
                    'why': '层高 %d，但相邻格是 %s —— 斜坡/悬崖边/引桥'
                           % (lv, '/'.join(map(str, diff)))}
        return {'level': lv, 'tile': t[0] if t else None, 'nb': nb,
                'is_flat': True, 'why': ''}

    def flat_cell_near(self, cx, cy, radius=10):
        """找离 (cx,cy) 最近的**平地格**（层高与八邻一致、且没有占位对象）→ (x,y) / None。"""
        best = None
        occ = self._occupancy()
        pl = self.plain
        co = self.cell_offsets()
        for (x, y), off in co.items():
            if abs(x - cx) > radius or abs(y - cy) > radius:
                continue
            if (x, y) in occ:
                continue
            if struct.unpack_from('<I', pl, off + 0xF8)[0]:
                continue
            if struct.unpack_from('<I', pl, off + 0xF0)[0]:
                continue
            if not self.cell_flat_info(x, y)['is_flat']:
                continue
            d = abs(x - cx) + abs(y - cy)
            if best is None or d < best[0]:
                best = (d, x, y)
        return (best[1], best[2]) if best else None

    def free_cell_near(self, cx, cy, radius=6, clearance=2, terrain=True):
        """在 (cx,cy) 附近找一块**引擎认可的空地** → (x,y)；找不到返回 None。

        `clearance` = 四周还要留几格空（避免紧贴建筑，游戏里会被挤）。
        """
        co = self.cell_offsets()
        pl = self.plain
        occ = self._occupancy()
        # ★§210：顺手排除**地形不可通行**的格（水/悬崖/地图外）。
        #   存档只看得到"格上有没有占位对象"，看不到地形 —— 之前就选到悬崖上过。
        mkt = self.map_cells() if terrain else {}
        okt = self.walkable_tiles() if mkt else set()
        best = None
        for (x, y), off in co.items():
            if abs(x - cx) > radius or abs(y - cy) > radius:
                continue
            if struct.unpack_from('<I', pl, off + 0xF8)[0]:
                continue
            if struct.unpack_from('<I', pl, off + 0xF0)[0]:
                continue
            if (x, y) in occ:
                continue
            if mkt:
                _t = mkt.get((x, y))
                if _t is None:
                    continue                       # 地图里没有这一格
                if okt and _t[0] not in okt:
                    continue                       # 地形从没被踩过 ⇒ 水/悬崖
            if clearance:
                if any((x + i, y + j) in occ
                       for i in range(-clearance, clearance + 1)
                       for j in range(-clearance, clearance + 1)):
                    continue
            d = abs(x - cx) + abs(y - cy)
            if best is None or d < best[0]:
                best = (d, x, y)
        return (best[1], best[2]) if best else None

    def _pick_scaffold(self, recs, tgt_loco=None):
        """挑**状态最干净**的模板 —— 克隆继承的是模板的"当前状态"（§214/§215 定案）。

        ★★2026-10-23 §215 玩家实测**定案**（同一份档、同一个落点 (34,182)、同一型号）：

            | 档 | 骨架 `+0x10C` | `+0x11C` | `+0x120` | 玩家看到的结果 |
            |---|---|---|---|---|
            | T71 | 13 | 9 | 9 | 在空中但**不能移动**、空血只爆一次**不死**、之后不能选中 |
            | T72 | **0** | **0** | **0** | 能飞、能移动、能攻击、**被打掉正常死亡不弹框** |

          ⇒ **决定性的是这三个槽必须为 0（模板处于"空闲/idle"）**，与骨架是什么型号无关。
          （§214 当时写成"DISK 才是同类骨架"，那是过度推断 —— T72 实际挑中的是一架
            `0x10C/0x11C/0x120` 恰好全 0 的 SCHP，飞碟只是**碰巧**也是干净的。）

        所以打分以"状态干净"为最高权重：不干净的骨架会让新单位继承"已部署/任务中"，
        于是出现"趴在地上"（武直）、"在空中却不能动、打不死"（基洛夫）这些症状。
        """
        pl = self.plain
        best = None
        # ★2026-10-24 §249：`+0x198` 的"同型号众数"（T85 的飞碟 97/98 = 0x7093df，
        #   玩家地编放进去、引擎亲手造的那台**也是** 0x7093df ⇒ 这是个**型号级共享值**，
        #   不是每台私有的对象）。个别单位是离群值（实测 1/98 = 0x14f36600），
        #   拿它当骨架会把离群值继承过来 ⇒ 优先挑与众数一致的。
        modal_198 = None
        if len(recs) >= 3:
            _c = {}
            for _r in recs:
                try:
                    _v = struct.unpack_from('<I', pl, _r['off'] + 0x198)[0]
                except Exception:
                    continue
                _c[_v] = _c.get(_v, 0) + 1
            if _c:
                modal_198 = max(_c.items(), key=lambda kv: kv[1])[0]
        for r in recs:
            try:
                n = min(r['size'] + r['comp_size'], 0x9C0)
                b = bytes(pl[r['off']:r['off'] + n])
            except Exception:
                continue

            def u(o, b=b):
                return struct.unpack_from('<I', b, o)[0] if o + 4 <= len(b) else 0

            score = 0
            idle = (u(0x10C) == 0 and u(0x11C) == 0 and u(0x120) == 0)
            if self._pick_registered([r]) is not None:
                score += 100                      # 登记表里有的（引擎认领，身份干净）
            # ★2026-10-24 晚：**不要**按"有没有目标点 `0x298`"加减分。
            #   T72（昨天验收通过）那台克隆飞碟的 `+0x298` 就是**60**（继承骨架值），
            #   照样能飞能打能正常死。我中午加的"+150 无目标点"会把骨架换成另一批，
            #   反而偏离了验证过的配方 ⇒ 撤销。
            # ★定案项：空闲状态权重必须**压过**其它所有加分项（否则会被"恰好在对的高度"翻盘）
            if idle:
                score += 200
            else:
                score -= 100                      # 部署中/任务中的骨架一律排后面
            loco = r.get('loco')
            if (tgt_loco or loco) in ('Fly', 'Aircraft'):
                # ★2026-10-24 晚：`+0x270/+0xB8` 是**当前高度**（同型号会有 416/832/1248 几种），
                #   克隆到低空骨架 ⇒ 新单位看着"从地下冒出来"（玩家 T92 实测）。
                #   这里给"飞得高"的骨架加分，优先 1248 那种正常巡航高度。
                if u(0xB8) >= 1000:
                    score += 30
                elif u(0xB8) > 0:
                    score += 10
                if u(0x270) and u(0xB8) == u(0x270):
                    score += 5                    # 在巡航高度（次要）
            else:
                if u(0xB8) == 0:
                    score += 20                   # 在地上
            if u(0x198) == 0:
                score += 6                        # 没有目标对象（放置时也会清）
            # ★2026-10-24 §245：`+0x5EC` = 指向别的单位的键（链指针）。骨架这一项非 0
            #   ⇒ 克隆出来会挂一条指向无关单位的野链（T96 读档崩、T72 那台是 0）。
            # ★★2026-10-24 §249（活内存实证，推翻 §243 的撤销理由）：
            #   克隆继承的是骨架的**整套会话档案**，不只是 `+0x10C/11C/120` 那三项。
            #   T85 实测（98 架飞碟）：92 架"正在跑地图脚本"的在 `+0x0D8/+0x54C/+0x654`
            #   上各挂着一个**自己私有的指令/脚本对象**（每台取值都不同），
            #   而 6 架空闲的这些槽是**一致的共享值**（0x17B1FB / 0x17B241）、
            #   `+0x670/+0x114/+0x17C…` 一串是 0xE1。
            #   ⇒ 挑到"忙"骨架 ⇒ 新单位读档就朝老目标飞（玩家原话："有目标，不知道要去干什么"），
            #     被打掉时引擎去更新那条脚本/指令 ⇒ 跳失效对象弹框（T103 实测）。
            #   §243 曾以"T72 那台 0x298=60 也能跑"为由撤销这项评分 —— **那是误判**：
            #   T72 所在的平地三V3 里所有飞碟本来就处于良性状态，"匹配同档原生"恰好成立。
            # ★权重刻意压在"空闲 200"之下（§215：空闲必须最高优先），但压过其它加分项：
            #   空闲+无任务 320 > 空闲+有任务 200 > 非空闲+无任务 20 > 非空闲+有任务 −160。
            no_mission = (u(0x298) == 0 and u(0x29C) == 0
                          and u(0x2A0) == 0 and u(0x2A4) == 0)
            if no_mission:
                score += 120
            else:
                score -= 60
            # ★§249：`+0x198` 与同型号众数一致（型号级共享值，见函数头）
            if modal_198 is not None:
                score += 20 if u(0x198) == modal_198 else -20
            if u(0x5EC) == 0:
                score += 40
            else:
                score -= 40
            # ★★2026-10-24 §246：**按"外来指针数"挑骨架**（今天所有"打掉就崩"的总根）。
            #   记录里凡是"落在键值域、但本档没有这个键"的 dword，都是骨架**自己那一局**的
            #   运行期指针（链表/队列/传感器…，形如 0x145Cxxxx）。原生单位由引擎在自己的
            #   会话里建好、读档时重建，所以没事；克隆体会把它们带过来 ⇒ 指向一堆
            #   **属于骨架**的运行期对象 ⇒ 死亡/深层更新时顺着走就炸。
            #   实证：T85 的飞碟 47 架带 250+ 个（打掉必崩），51 架只带 7~10 个（正常）；
            #   T98 用的骨架属于前者，T97/T99（玩家验收通过）属于后者。
            # ★权重上限 200：足以在同为"空闲"的骨架里挑出干净的，但**压不过空闲的 ±300**
            #   （§215 定案：空闲状态必须最高优先，否则会挑到"已部署/任务中"的骨架 = T71 症状）。
            _fk = self.object_keys()
            foreign = sum(
                1 for _o in range(0, len(b) - 3, 4)
                if 0x01000000 <= struct.unpack_from('<I', b, _o)[0] <= 0x17FFFFFF
                and struct.unpack_from('<I', b, _o)[0] not in _fk)
            score -= min(foreign, 100) * 2
            if u(0xA8) in (0, 1):
                score += 2
            if best is None or score > best[0]:
                best = (score, r)
        return best[1] if best else (recs[0] if recs else None)

    def _apply_idle_order_state(self, blob, src_plain=None, src_off=None,
                                cx=None, cy=None):
        """把「任务 / 目的地 / 当前指令」整组写成**同档原生空闲单位的样子**（§252）。

        返回 `{'cleared':n,'kept':n,'dest':(x,y)或None}`。

        规则（每一条都有 T106 的 98 台原生 DISK / 157 台 SREF 实测支撑）：
          · `+0x298/+0x29C/+0x2A0/+0x2A4` → 0（= 空闲/无任务；空闲原生本来也是 0）
          · `+0x5EC` → 0（野链，指向别的单位）
          · `+0x3A4/+0x3BC` → **抄骨架**（全档统一的共享指令对象；抄成 0 = 空对象，
            引擎在死亡路径上不判空 ⇒ 打掉弹框，这就是 §243 当年实测的现象）
          · `+0x960/+0x964`（+ `0x658/0x680`）→ **骨架非 0 时**改写成"自己脚下那格"，
            骨架是 0 就保持 0（地面载具 157/157 原生都是 0，不能无脑写坐标）
        """
        out = {'cleared': 0, 'kept': 0, 'dest': None}
        for _o in self.UNIT_ORDER_CLEAR:
            if _o + 4 <= len(blob):
                struct.pack_into('<I', blob, _o, 0)
                out['cleared'] += 1
        if src_plain is not None and src_off is not None:
            for _o in self.UNIT_ORDER_KEEP:
                if _o + 4 <= len(blob) and src_off + _o + 4 <= len(src_plain):
                    struct.pack_into('<I', blob, _o,
                                     struct.unpack_from('<I', src_plain, src_off + _o)[0])
                    out['kept'] += 1
        if cx is None or cy is None or src_plain is None or src_off is None:
            return out
        # ★伴随段长度必须够（步兵的伴随段只有 81 字节，0x960 已经越过记录末尾）——
        #   越过记录末尾去读，读到的是**下一个对象**的字节 ⇒ 会写进垃圾"目的地"。
        if len(blob) < self.UNIT_DEST_Y + 4:
            return out
        if src_off + self.UNIT_DEST_X + 4 > len(src_plain):
            return out
        src_dest = struct.unpack_from('<I', src_plain, src_off + self.UNIT_DEST_X)[0]
        if not src_dest:
            return out            # 骨架本来就没有目的地（地面载具）⇒ 保持 0
        lx = (int(cx) << 8) | 0x80         # 格中心 lepton（原生全是 ...80）
        ly = (int(cy) << 8) | 0x80
        if self.UNIT_DEST_X + 4 <= len(blob):
            struct.pack_into('<I', blob, self.UNIT_DEST_X, lx)
        if self.UNIT_DEST_Y + 4 <= len(blob):
            struct.pack_into('<I', blob, self.UNIT_DEST_Y, ly)
        for _o in self.UNIT_DEST_Y_COPY:
            if _o + 4 <= len(blob):
                struct.pack_into('<I', blob, _o, ly)
        out['dest'] = (cx, cy)
        return out

    def _clean_foreign_ptrs(self, blob, new_key, src):
        """清掉克隆体里**外来的运行期指针**（§246，2026-10-24 晚）→ 清了几个。

        克隆 = 原样复制骨架记录。骨架身上带着它**自己那一局**的运行期指针
        （链表/队列/传感器…），形如 `0x145Cxxxx`：落在键值域里、但**本档并没有这个键**。
        原生单位这些字段由引擎在自己的会话里写好、读档时重建，所以没事；
        克隆体把它们带过来 ⇒ 指向一堆**属于骨架**的运行期对象
        ⇒ 死亡 / 深层更新时顺着走就炸（玩家实测：T98 打掉就弹框，`Eip` 落在堆上）。

        判据（本机实测 + 与**已验收档** T97/T99 逐字段对照；
         §243/§245 的教训是"多清字段必须先对照验收档，不能凭猜"）：
          · 只清"键值域内、但不是本档有效键"的值 —— **合法键原样保留**
            （类型键 `+0x6D8` 在 `object_keys()` 里、所属方键等也是）；
          · **全员共有的偏移不清**：同型号 ≥90% 的记录在该偏移都有这种值
            ⇒ 那是结构字段，不是毒。实测 T85 的 98 架飞碟：
              保留 10 个（覆盖 98~92：`+0x000/+0x004` CLSID、`+0x0F8`、`+0x118`、
              `+0x160/+0x230` 所属方键、`+0x3C0`、`+0x560`、`+0x688`、`+0x910`），
              清除 238 个（覆盖 ≤55 —— 只有"正在执行任务"的那 47 架才有）。
          · `new_key`（刚分配给克隆体的自键，还没进存档键集）也算有效，不能清。

        ★安全性：对**已经干净**的克隆体是**空操作** —— T97 / T99 这两个玩家验收通过的档，
          本函数一个字节都不会改（它们这类值本来就只在全员共有偏移上）。
        """
        n = len(blob)
        if n < 8:
            return 0
        KEY_LO, KEY_HI = 0x01000000, 0x17FFFFFF
        pl = self.plain
        valid = set(self.object_keys())
        valid.add(new_key & 0xFFFFFFFF)
        if 0x6D8 + 4 <= n:                       # 类型键 = 单位身份，必须留
            valid.add(struct.unpack_from('<I', blob, 0x6D8)[0])
        # ★所属方键（+352/+560）**不在 `object_keys()` 里**（House 不是按对象锚点扫的），
        #   只靠"全员共有偏移"侥幸保住不够稳 —— 显式把这两个偏移设为永不清。
        #   （2026-10-24 自检实踩：B2 断言失败，`+0x160` 的值 0xFAAB120 确实不是对象键。）
        never = set(self.UNIT_HOUSE_OFF)
        # ★★2026-10-24 §247：`+0x48` = 单位的**标签挂接字段**（存 CellTag 运行期指针 X）。
        #   它虽然在键值域内，却因为"同型号大多挂着标签"而落进"全员共有偏移"
        #   （实测 T85：91 架飞碟里 85 架带标签 ⇒ 覆盖率 93% ≥ 90%），
        #   所以 §246 的通用规则会把它**误保下来** —— 那等于把新单位挂到骨架所属的触发上。
        #   玩家实测（T100）：克隆飞碟一被打 → 触发 01000250「飞碟被攻击寻敌」放全家 →
        #   打掉就弹框（except.txt 16:50：Eip=0EFF4DAA 在堆上、Ebx/Edx=0x159Bxxxx
        #   正是触发对象所在段、读 −1）。
        #   ⇒ 显式强制清 0：全档 91.9% 的单位这一项本来就是 0，"新单位不挂任何触发"才是正常态。
        force_clear = {0x48}
        # ---- 采样：同型号优先（布局+语义都一致）；太少就按"同主体长度"放大样本 ----
        recs = self.unit_records()
        tn = str(src['type']).upper()
        sample = [r for r in recs if r['type'].upper() == tn]
        if len(sample) < 5:
            sample = [r for r in recs if r['size'] == src['size']]
        if len(sample) < 5:
            sample = recs
        # ---- 全员共有偏移 = 结构字段（不清）----
        universal = set()
        if sample:
            need = max(1, int(len(sample) * 0.9))
            share = {}
            for r in sample:
                base = r['off']
                end = min(n, r['size'] + r['comp_size'])
                for o in range(0, end - 3, 4):
                    v = struct.unpack_from('<I', pl, base + o)[0]
                    if KEY_LO <= v <= KEY_HI and v not in valid:
                        share[o] = share.get(o, 0) + 1
            universal = {o for o, c in share.items() if c >= need}
        # ---- 清 ----
        n_clean = 0
        for o in range(0, n - 3, 4):
            if o in never:
                continue
            v = struct.unpack_from('<I', blob, o)[0]
            if o in force_clear or (KEY_LO <= v <= KEY_HI and v not in valid
                                    and o not in universal):
                if v:
                    struct.pack_into('<I', blob, o, 0)
                    n_clean += 1
        return n_clean

    def add_unit_by_clone(self, type_name, cx, cy, house=None, veterancy=None, borrow_type=None,
                          exact_cell=True, auto_nudge=False):
        """**克隆放置**一个单位（2026-10-17 玩家 U2 游戏内验收通过）。

        做法 = 拿档内**同类型**单位当模板，克隆「主体 + 伴随」，然后办 7 处手续：
          ① 组计数 +1；② 自键换新（克隆体内旧键引用全改）；
          ③ **每对象唯一 ID/基址换新**（+248/+1672/+1828，载具另加 +1320/+2332）
             —— ★2026-10-17 修「新开载入正常、游戏内重载崩」（同建筑 +20/+972 那个坑）；
          ④ 坐标 4 处（+176/+180、+616/+620、+224 格键、+1392/+1394）；
          ⑤ 两张指针表各追加新键（A⊆B 断言）；⑥ 阵营对象表追加 (类型键, y, x)；
          ⑦ **所属方**：`house=` 指定目标阵营时改记录 `+352`/`+560` 两份 House 键。
        实测账目：步兵 +1909 B（1893 记录+伴随 / 8 阵营表 / 4+4 指针表）。
        ⚠ 只能克隆**档内已有**的同类型单位（跨类型会借错子对象，尚未验证）。

        `veterancy`（2026-10-18 新增）：0=新兵 / 1=老兵 / 2=精英；`None` = **沿用模板的等级**
        （默认行为，等于旧版）。非 None 时把记录 `+0x150`(int) 与 `+0x164`(float 2−n)
        **成对改写** —— 这两个字段在全档 1394 个单位里严格孪生，必须一起改。
        """
        # ★2026-10-28 修：原来 `warnings = []` 在函数中段（约 8966 行）才初始化，
        #   而前面"缺移动方式模板"的 else 分支（约 8890 行）就已经 append ⇒ 那条分支
        #   必抛 `UnboundLocalError`（玩家看到的是一句看不懂的
        #   "cannot access local variable 'warnings'"）。警告列表必须一开始就存在。
        warnings = []
        t = str(type_name).strip().upper()
        if not t:
            raise ValueError('单位类型不能为空')
        cx, cy = int(cx), int(cy)
        lim = self.coord_limit()
        if not (0 <= cx < lim[0] and 0 <= cy < lim[1]):
            raise ValueError('格子 (%d,%d) 超出范围（0..%d / 0..%d）'
                             % (cx, cy, lim[0] - 1, lim[1] - 1))
        cells = {v: k for k, v in self._cell_by_key().items()}   # (x,y) → 格对象自键
        if (cx, cy) not in cells:
            raise RuntimeError('该档没有格 (%d,%d) 的格对象，无法落位' % (cx, cy))
        # ★★§251 落点必须平地（否则打掉会弹框 + 单位"有目标"往外跑）。
        #   斜坡格 tile 1265 是**可通行**的，所以老的"可通行"校验拦不住 —— 这里单独判层高。
        # ★★★§252（玩家第 7 轮原话）：「我指定是平地，你选的地方反而更危险」
        #   ⇒ **默认不再自动挪位**：调用方给的格就是最终格，只在不是平地时**大声告警**。
        #   要"自动挪到最近平地"必须显式 `auto_nudge=True`（CLI/GUI 已加开关）。
        #   `exact_cell=False` 保留旧语义（= 允许挪），只为兼容老脚本。
        _nudge = None
        _ci = self.cell_flat_info(cx, cy)
        _allow_nudge = auto_nudge or (exact_cell is False)
        if not _ci['is_flat'] and _allow_nudge:
            _alt = self.flat_cell_near(cx, cy)
            if _alt:
                _nudge = {'from': (cx, cy), 'to': _alt, 'why': _ci.get('why') or '不是平地'}
                cx, cy = _alt
        recs = self.unit_records()
        # ★§210：`borrow_type` = **强制拿某个型号当骨架**（A/B 用：同一份档、同一片地形，
        #   唯一变量是"骨架从哪来"）。不传 = 原来的自动选择。
        forced = None
        if borrow_type:
            bt = str(borrow_type).strip().upper()
            _pf = [r for r in recs if r['type'].upper() == bt]
            if not _pf:
                raise UnitUnavailable('本档没有 %s 可以当骨架' % bt)
            forced = self._pick_registered(_pf) or _pf[0]
        same = [] if forced is not None else [r for r in recs if r['type'].upper() == t]
        swap_type = False
        # ★2026-10-17 18:4x：**移动方式（Locomotor）= 伴随对象的类**。
        #   地面载具借地面模板没问题；但基洛夫（Fly）借地面模板就会**在地上跑**。
        #   所以先确定目标型号要的移动方式，再优先挑"同移动方式"的模板。
        tgt_loco = self._type_locomotor(t, recs)
        src = None
        if same:
            src = self._pick_registered(same)
            # ① 同型号但**全都没进登记表**（大任务档里 15 个 SLAV 就是这样，多半在建筑里/待定）
            # ② 或挑中的模板移动方式不符 ⇒ 都退到"借同类已登记模板 + 换型号键"
            if src is None or (tgt_loco and src.get('loco') not in (None, '?', tgt_loco)):
                src = None
                same = []
        if not same:
            # ★2026-10-17 X1b 验证：型号由记录里的**类型键**决定（引擎载入时按它也重建武器/动画）
            #   ⇒ 档里没有该型号时，借**同大类**的模板克隆，再把类型键换成目标型号即可。
            idx = self.unit_type_index().get(t)
            if not idx or not idx.get('class'):
                raise UnitUnavailable('类型表里没有 %s（或归不到步兵/载具）' % t)
            cls = idx['class']
            pool = [r for r in recs if r['class'] == cls]
            if tgt_loco:
                same_loco = [r for r in pool if r.get('loco') == tgt_loco]
                if same_loco:
                    pool = same_loco
                elif tgt_loco in self.comp_library():
                    # 本档没有同移动方式的单位，但内置库里存了这个移动方式的伴随对象
                    # ⇒ 记录随便借同类的，伴随对象从库里取（见下面 comp_bytes）
                    pass
                else:
                    raise UnitUnavailable(
                        '这份存档里没有「%s」移动方式的单位当模板，内置库也还没有这一种。\n'
                        '办法：① 换一份**本来就含这类单位**的存档当源；'
                        '② 或用 `--learncomp <存档> <型号>` 把该移动方式学进内置库。'
                        % tgt_loco)
            src = self._pick_scaffold(pool, tgt_loco) or pool[0]
            if forced is not None:
                src = forced                      # ★§210 A/B：骨架由调用方指定
            swap_type = (src['type'].upper() != t)
        else:
            # 同型号模板也要挑"登记表里有的"：大任务档里有 76 个单位不在任何登记表
            # （多半刚出生/在建筑里/待定），拿它当模板会连带拿到"未登记"的身份。
            # ★★§215（2026-10-23 玩家实测纠错）：还必须走 `_pick_scaffold()`！
            #   之前这里只按"登记表"挑 ⇒ 同型号克隆照样会挑中"**已部署/任务中**"的那一架，
            #   于是克隆出来的武直一出现就是趴在地上（玩家实测：解除部署后才正常）。
            #   打分里已含 +100 登记表，所以这一句同时满足两个条件。
            src = self._pick_scaffold(same, tgt_loco) or self._pick_registered(same) or src
        # ★★§215：骨架的"是否空闲"必须在**任何插入之前**算好 —— 指针表/记录的插入会让
        #   `src['off']` 整体移位，等到最后再按旧偏移去读就会读到垃圾（自检 ev215 抓到的坑：
        #   实际克隆体 0x10C/0x11C/0x120 是全 0，可这个标记却报 False，GUI 会误弹告警）。
        _scaffold_idle = all(
            struct.unpack_from('<I', self.plain, src['off'] + _o)[0] == 0
            for _o in (0x10C, 0x11C, 0x120)
            if src['off'] + _o + 4 <= len(self.plain))
        # ★§249：骨架"有没有任务/目标"（`+0x298~0x2A4` 全 0 = 原地待命）。
        #   挑不到这种骨架 ⇒ 克隆体会继承一整套私有的脚本/指令对象 ⇒ 自己跑 + 打掉可能弹框。
        _scaffold_no_mission = all(
            struct.unpack_from('<I', self.plain, src['off'] + _o)[0] == 0
            for _o in (0x298, 0x29C, 0x2A0, 0x2A4)
            if src['off'] + _o + 4 <= len(self.plain))
        _scaffold_type = src['type']
        pl = bytearray(self.plain)
        old_key = src['key']
        # ---- 指针表 ----
        # ★2026-10-17 21:1x：**少于 2 张表就拒绝写入**。
        #   加载器的「指针修复归并器」要求 A⊆B（A 表的键必须在 B 表里），
        #   只往一张表里加键 ⇒ 引擎不认领这个对象（单位在游戏里看不见，或读档弹框）。
        #   以前只是 warning 仍然照写 —— 大任务档（表有 5189 条）就踩这个坑，改成硬拒绝。
        tabs = self._key_tables(old_key)
        if len(tabs) < 2:
            raise UnitUnavailable(
                '这份存档里只找到 %d 张全局指针表（正常要 2 张），'
                '新单位的键无处登记 ⇒ 引擎不会认领它（游戏里看不见 / 读档可能弹框）。\n'
                '已知情况：超大任务档的指针表条目数远超旧上限，结构可能不止"两张小表"。\n'
                '安全起见这里**拒绝写入**。要放单位请换一份普通存档，'
                '或把这份档发给作者排查表结构。' % len(tabs))
        used = set()
        for tb in tabs:
            for k in range(tb['n']):
                used.add(struct.unpack_from('<I', pl, tb['first'] + 4 * k)[0])
        new_key = (old_key + 0x8000) & 0xFFFFFFFF
        while new_key in used or pl.find(struct.pack('<I', new_key)) >= 0:
            new_key += 4
        # ---- 伴随对象：优先用本档同移动方式的模板，缺了就取**内置模板库** ----
        comp_bytes = bytes(pl[src['comp']:src['comp'] + src['comp_size']])
        comp_src = 'template:%s' % src['type']
        if tgt_loco and src.get('loco') != tgt_loco:
            lib = self.comp_library().get(tgt_loco)
            if lib:
                comp_bytes = bytearray(lib['bytes'])
                # 补 +16 = 记录 +1672 − 4（基址）；自键由下面统一替换
                struct.pack_into('<I', comp_bytes, 16,
                                 struct.unpack_from('<I', pl, src['off'] + 1672)[0] - 4)
                # ★库里存的是**样本单位**的伴随对象，它里面还带着样本的自键（+28/+32），
                #   必须换成新单位的键（否则两个单位又共用一个键）。
                lib_key = struct.unpack_from('<I', comp_bytes, 28)[0]
                if lib_key:
                    _lb, _nb = struct.pack('<I', lib_key), struct.pack('<I', new_key)
                    _i = 0
                    while True:
                        _j = comp_bytes.find(_lb, _i)
                        if _j < 0:
                            break
                        comp_bytes[_j:_j + 4] = _nb
                        _i = _j + 4
                comp_src = 'library:%s(%s)' % (tgt_loco, lib.get('sample', '?'))
            else:
                warnings.append('本档没有「%s」移动方式的模板、内置库也没有 ⇒ 新单位的移动方式可能不对'
                                % tgt_loco)
        # ---- 克隆体 ----
        blob = bytearray(pl[src['off']:src['comp']] + bytes(comp_bytes))
        oldb, newb = struct.pack('<I', old_key), struct.pack('<I', new_key)
        i, nrep = 0, 0
        while True:
            j = blob.find(oldb, i)
            if j < 0:
                break
            blob[j:j + 4] = newb
            nrep += 1
            i = j + 4
        # 换型号：把克隆体里**所有**模板型号键替换成目标型号键（载具在 +280/+1752 各一处）
        target_key = None
        if swap_type:
            idx = self.unit_type_index().get(t)
            target_key = idx['key'] if idx else None
            tpl_names = [k for k, v in self.unit_types().items() if v == src['type']]
            if target_key is None or not tpl_names:
                raise UnitUnavailable('拿不到 %s 或 %s 的类型键' % (t, src['type']))
            # ★★2026-10-23 §198（玩家实测 V3 弹框）：这类单位"发射出去的是一个完整单位"
            #   （`SPAWNS=` 声明了子单位，V3 → V3ROCKET）。克隆换型号只会换类型键，
            #   那个子单位在档里根本没有对应对象 ⇒ 引擎读档时遍历它的列表拿到空指针
            #   （except.txt: `Access address 0x48` / `Eip 006B7D51` / `ecx=0`）。
            #   ⇒ 直接拒绝，并说清原因（要放它请用「⚡ 触发法」，让引擎自己造）。
            _sp = self._type_spawns(t)
            if _sp:
                raise UnitUnavailable(
                    '「%s」不能跨型号克隆：它在规则里声明了 `SPAWNS=%s` —— '
                    '开火打出去的是**一个完整单位**，而不是普通弹药。\n'
                    '克隆只能复制"一辆车"，造不出那个子单位，引擎读档会报内部错误。\n'
                    '要放它请用「⚡ 触发法」（由引擎自己造，子单位一并生成）。' % (t, _sp))
            tb_old = struct.pack('<I', tpl_names[0])
            tb_new = struct.pack('<I', target_key)
            k = 0
            while True:
                j2 = blob.find(tb_old, k)
                if j2 < 0:
                    break
                blob[j2:j2 + 4] = tb_new
                k = j2 + 4
                nrep += 1
        # ★★2026-10-18 修（玩家实测：在 (214,291) 加 FV，进游戏什么都看不到）：
        #   单位记录里 **+176 是 x、+180 是 y**（与建筑记录 +600/+604 同一口径，
        #   实测 5 个既有载具：+176=A、+180=B、+616=A、+620=B、+1392=A、+1394=B）。
        #   原代码把 (cx,cy) 按 (y,x) 写进去 ⇒ 新单位落到**转置**的格子上，
        #   在 (214,291) 加的东西实际躺在 (291,214)，当然"看不到"。
        fx = pl[src['off'] + 176] & 0xFF      # 子格偏移也跟着换过来
        fy = pl[src['off'] + 180] & 0xFF
        nx, ny = cx * 256 + fx, cy * 256 + fy
        for off in (176, 616):
            struct.pack_into('<I', blob, off, nx)
            struct.pack_into('<I', blob, off + 4, ny)
        struct.pack_into('<I', blob, 224, cells[(cx, cy)])
        struct.pack_into('<H', blob, 1392, cx)
        struct.pack_into('<H', blob, 1394, cy)
        # ★★2026-10-24 §252：清零路线被数据推翻（详见 `UNIT_ORDER_CLEAR` 的注释）。
        #   现在交给 `_apply_idle_order_state()`：任务组清 0、指令组抄骨架、
        #   目的地（空中单位才有）改写成**自己脚下那格** —— 三样都做到"与同档原生一致"。
        _order_state = self._apply_idle_order_state(blob, pl, src['off'], cx, cy)
        # ★★2026-10-24 §246：清掉模板残留的**外来运行期指针** —— 今天所有"克隆单位
        #   打掉就弹框"的总根（T98：159+88 个；验收档 T97/T99 这些位置是 0/合法值）。
        #   详见 `_clean_foreign_ptrs()` 的判据说明（含"对已验收档是空操作"的安全性）。
        _n_foreign = self._clean_foreign_ptrs(blob, new_key, src)
        if tgt_loco in ('Fly', 'Aircraft') and 0x270 + 4 <= len(blob):
            # ★2026-10-24 §248.8：**只抬高、不压低**。
            #   旧代码无条件 `0xB8 = 0x270`（类型高度）——骨架正飞在 1998 时会被强按成 1248，
            #   偏离同档原生众数（T85 的飞碟大多在 1998）。现在只在"当前高度低于类型高度"时抬起来
            #   （避免 §212 那个"骨架在低空 ⇒ 克隆从地下冒出来"的老问题），高的保持骨架原值。
            _alt = struct.unpack_from('<I', blob, 0x270)[0]
            _cur = struct.unpack_from('<I', blob, 0xB8)[0]
            if _alt and _cur < _alt:
                struct.pack_into('<I', blob, 0xB8, _alt)

        # ---- 所属方（★2026-10-17 新增：house 参数真正改主人，不再只挑模板）----
        # ★注意：`warnings` 已在函数开头初始化（见顶部注释），这里**不能**再 `= []`，
        #   否则会把前面"缺移动方式模板"等早先攒下的警告冲掉。
        # ★★2026-10-23 §209（玩家实测：SAVE0029 上四个变体全隐形）：
        #   跨型号克隆**载具**（本档没有该型号）目前拿不到能显示的单位 —— 记录内容已经不是变量：
        #   移动方式/类型键/HP/同档 A/B 找出的 9 个型号派生槽（记录 3 + 伴随 6）/类型对象/指针表/
        #   对象基址 全部逐项排除过了，改哪个都还是"只显示等级徽章、机身隐形"。
        #   同型号克隆（档里本来就有该型号）是好的 —— 那才是验证通过的路线。
        # ★§210：只有"本档真的没有这个型号"才警告。`borrow_type` 强制的 A/B 不算
        #   （那种情况档里有该型号，只是我们故意借别人的骨架）。
        if (swap_type and src.get('class') == 'vehicle'
                and not any(r['type'].upper() == t for r in recs)):
            warnings.append('⚠ 跨型号克隆**载具**：本档没有 %s，实测放出来「只显示等级徽章、'
                            '机身隐形」（§209）。想要能显示的单位，请用**本来就有该型号**的存档'
                            '（同型号克隆），或用「⚡ 触发法」让引擎自己造。' % t)
        # ★§200 2026-10-23 玩家实测：**空中单位落点贴着地图边缘 ⇒ 模型不显示**
        #   （基洛夫放在 (15,179)，游戏里只看到一个等级徽章、机身不出来；
        #     改到 (20,179) 就正常）。这条与移动方式无关，是"画不画"的问题。
        # ★§210：地形体检（水/悬崖/地图外）——存档看不到地形，是放置失败的头号盲区
        _tw = self.terrain_warning(cx, cy, air=(tgt_loco in ('Fly', 'Aircraft')))
        if _tw:
            warnings.append(_tw)
        # ★§252：不再自动挪位，所以"落点不是平地"必须由这里明确喊出来。
        if not _ci['is_flat']:
            warnings.append(
                '⚠ 落点 (%d,%d) **不是平地**（%s）。按你的要求已**原样落位、没有挪动**，'
                '但实测在这种格子上单位会"有目标、自己往外跑"，被打掉时还容易弹框。'
                '要换成最近平地请勾"自动挪到平地"（或传 auto_nudge=True）。'
                % (cx, cy, _ci.get('why') or '层高与相邻格不一致'))
        if tgt_loco in ('Fly', 'Aircraft'):
            _ex = 4
            if (cx < _ex or cy < _ex or cx >= lim[0] - _ex or cy >= lim[1] - _ex):
                warnings.append('%s 是空中单位，落点 (%d,%d) 贴着地图边缘（<4 格）⇒ '
                                '游戏里可能只看到等级徽章、机身不显示，建议往内挪几格'
                                % (t, cx, cy))
        tpl_hkey = struct.unpack_from('<I', pl, src['off'] + self.UNIT_HOUSE_OFF[0])[0]
        tpl_hname = self._house_key_names().get(tpl_hkey) or src.get('house')
        tgt_hkey, tgt_hname, house_changed = None, tpl_hname, False
        if house:
            tgt_hkey = self.house_key_of(house)
            if not tgt_hkey:
                warnings.append('认不出所属方 %r（已知：%s），沿用模板所属方 %s'
                                % (house, '、'.join(sorted(k for k in self.house_keys() if k)),
                                   tpl_hname))
            else:
                tgt_hname = self._house_key_names().get(tgt_hkey, str(house))
                if tgt_hkey != tpl_hkey:
                    for _o in self.UNIT_HOUSE_OFF:
                        struct.pack_into('<I', blob, _o, tgt_hkey)
                    house_changed = True
        # ---- ★★★★★ 实例 ID（记录 +36）：重载崩 + 选中/移动/攻击串到对方的**真凶** ----
        # 2026-10-17 18:2x 用玩家给的引擎原生样本 `10个大兵.SAV` / `一个大兵.SAV` 定案：
        #   8 个引擎原生 E1 的 `+36` = 0x0fa357/358/359/35c/35d/35e/35f/360，
        #   紧接着的建筑是 0x0fa361/364/367 ⇒ **单位与建筑共用同一个实例 ID 计数器**，
        #   每个值在明文里**只出现 1 次**（= ID，不是指针；建筑侧 `+20` 在载荷帧 = g+36，同一个字段）。
        #   我们克隆时原样复制 ⇒ 两个对象同 ID ⇒ 选中/移动/攻击解析到对方、重载拆世界对不上。
        id_info = self._fresh_unit_inst_id(blob, pl)
        if id_info is None:
            warnings.append('分配实例 ID 失败 ⇒ 新单位会与模板共用身份（选中/重载都会错乱）')
        # ---- 格子占用（格+248 = 站在该格上的单位键）----
        # ★2026-10-17 17:5x 玩家点破：**红警里"攻击单位"实际是"攻击它所在的格子"**，
        #   引擎按「格 → 占用者」解析目标。克隆体 `+224` 指向新格，但新格的 `+248` 没登记它
        #   ⇒ 格上没有这个单位 ⇒ 攻击/选中解析落到别人身上（"打一个却指向另一个"）。
        #   所以必须写 `格+248`。
        # ⚠ 但**只写键**：上一轮连 `+308/+312`（标记/类型码）一起写，把本来重载正常的
        #   载具克隆弄崩了（V3 崩 / H3 好）⇒ 那两个字节不碰，交给引擎自己处理。
        # ★2026-10-18：目标格若落在**某建筑的地基**里，给一条明确警告。
        #   实测教训（玩家在 (231,172) 放天启，游戏里那里是密集建筑群）：
        #   格 +0xF8 为空 **不等于** 空地 —— 引擎的占用是拿建筑记录 + 地基算出来的，
        #   格表里那格可能是干净的。放置前请用 `cell_taken()` 判。
        if self.cell_taken(cx, cy):
            warnings.append('目标格 (%d,%d) 已被建筑/单位占用（引擎会把它挤掉或直接销毁）'
                            % (cx, cy))
        cell_ops = []
        if self.UNIT_CELL_OCC_MODE:
            cofs = self.cell_offsets()
            tgt_cell = cofs.get((cx, cy))
            if tgt_cell is not None:
                old_occ = struct.unpack_from('<I', pl, tgt_cell + self.CELL_OCC_OFF)[0]
                if old_occ and old_occ != src['key']:
                    warnings.append('目标格 (%d,%d) 原本已被 %#010x 占用，已覆盖' % (cx, cy, old_occ))
                cell_ops.append((tgt_cell + self.CELL_OCC_OFF, 'write',
                                 struct.pack('<I', new_key), '格子占用键'))
                if self.UNIT_CELL_OCC_MODE == 'full':
                    src_cell = cofs.get((src['x'], src['y'])) if src.get('x') is not None else None
                    if src_cell is not None:
                        flag = pl[src_cell + self.CELL_OCC_FLAG_OFF:src_cell + self.CELL_OCC_FLAG_OFF + 4]
                        kind = pl[src_cell + self.CELL_OCC_KIND_OFF:src_cell + self.CELL_OCC_KIND_OFF + 4]
                    else:
                        flag = struct.pack('<I', 0x0001FFFF)
                        kind = struct.pack('<I', 0x08 if src['class'] == 'infantry' else 0x20)
                    cell_ops.append((tgt_cell + self.CELL_OCC_FLAG_OFF, 'write', bytes(flag), '格子占用标记'))
                    cell_ops.append((tgt_cell + self.CELL_OCC_KIND_OFF, 'write', bytes(kind), '格子占用类型'))
            else:
                warnings.append('找不到格 (%d,%d) 的格对象 ⇒ 未写格子占用' % (cx, cy))
        # ---- 记录 +408 ----
        # 引擎原生 ≈ 自身键 + 0xC0~0xD8（是个地址，不是 ID）。实测改动它**没有帮助**：
        #   M1 改成"新键+0xC8"后不再指向原单位，但那个值恰好撞了别的对象 ⇒ 一选就弹框；
        #   N1 改成唯一值 ⇒ 仍然串。⇒ **保持模板值不动**，真正的身份是 `+36`（见上）。
        # ---- 血量：换成目标型号的满血值（否则克隆体继承模板的血量）----
        # ★2026-10-17 19:0x 玩家实测："基洛夫血量很少" —— 因为抄了天启的血量。
        #   满血单位 `+128`/`+132` 都等于规则里的 `Strength`（灰熊 300 / V3 150 / E1 125）。
        hp = self._type_strength(t)
        if hp:
            for _o in self.UNIT_HP_OFF:
                struct.pack_into('<I', blob, _o, hp)
        # ---- 等级（Veterancy）----
        # 默认 None = **原样继承模板**（旧行为）。指定时成对改 `+0x150`(int) 与 `+0x164`(float)。
        vet_out = struct.unpack_from('<i', blob, self.UNIT_VET_OFF[0])[0]
        if veterancy is not None:
            vet = int(veterancy)
            if vet not in self.VET_NAMES:
                raise ValueError('等级只能是 0（新兵）/1（老兵）/2（精英），收到 %r' % (veterancy,))
            struct.pack_into('<i', blob, self.UNIT_VET_OFF[0], vet)
            # ★★§270：**绝不能动 `+0x164`** —— 玩家三档实测证明那里三份都是 2.0f，
            #   而旧代码写 float(2-vet)（精英→0.0）会让新单位在游戏里显示**新兵**。
            vet_out = vet
            if vet != struct.unpack_from('<i', pl, src['off'] + self.UNIT_VET_OFF[0])[0]:
                warnings.append('等级已改为 %s（模板是 %s）'
                                % (self.VET_NAMES[vet],
                                   self.VET_NAMES.get(
                                       struct.unpack_from('<i', pl, src['off'] + self.UNIT_VET_OFF[0])[0],
                                       '未定义')))
        # ---- 阵营对象表 ----
        # 2026-10-17 实测：任务图里单位**本来就不在** House 的对象清单里（清单只有建筑/Special），
        # 所以"找不到条目"是常态、不算错；只有"地图初始布置"式的档里才会命中。
        hp = self.house_placements()
        cand = [x for x in hp if x['type'].upper() == src['type'].upper()]
        if house and not house_changed:
            h2 = [x for x in cand if x['house'] == house or house in str(x['house'])]
            cand = h2 or cand
        roster_ops = None
        if cand:
            # 改过所属方 ⇒ 条目必须进**目标阵营**的清单；目标没有清单就不加（宁缺勿错位）
            roster_house = cand[0]['house']
            if house_changed:
                roster_house = tgt_hname if any(x['house'] == tgt_hname for x in hp) else None
                if roster_house is None:
                    warnings.append('%s 没有对象清单 ⇒ 未加清单条目（本档单位本来也不在清单里）'
                                    % tgt_hname)
            mine = [x for x in hp if x['house'] == roster_house] if roster_house else []
            if mine:
                first, last = min(x['off'] for x in mine), max(x['off'] for x in mine)
                cnt_pos = first - 4
                if (struct.unpack_from('<I', pl, cnt_pos)[0] == len(mine)
                        and last + 8 == first + 8 * len(mine)):
                    roster_ops = (last + 8, cnt_pos)
                else:
                    warnings.append('阵营对象表布局不符，未追加条目（可能是"隐形"单位）')

        # ---- 写回：严格从高到低 ----
        cnt_pos = self._unit_group_count_pos(src['off'])
        if cnt_pos is None:
            warnings.append('定位组计数失败（模板可能不在标准组里）⇒ 用模板记录前 4 字节兜底')
            cnt_pos = src['off'] - 4
        ops = [(src['comp'] + src['comp_size'], 'insert', bytes(blob), '单位组'),
               (cnt_pos, 'bump', None, '组计数')]
        if roster_ops:
            tkey = None
            for x in hp:
                if x['off'] == cand[0]['off']:
                    tkey = struct.unpack_from('<I', pl, x['off'])[0]
            if swap_type and target_key:
                tkey = target_key          # 换型号时，表里也写目标型号键
            ops.append((roster_ops[0], 'insert',
                        struct.pack('<I', tkey) + struct.pack('<HH', cy, cx), '阵营表'))
            ops.append((roster_ops[1], 'bump', None, '阵营表计数'))
        for tb in tabs:
            ops.append((tb['first'] + 4 * tb['n'], 'insert', newb, '指针表'))
            ops.append((tb['count_pos'], 'bump', None, '指针表计数'))
        ops.extend(cell_ops)
        if len(tabs) < 2:
            warnings.append('只找到 %d 张指针表（通常 2 张）——新单位可能不被引擎认领' % len(tabs))
        ops.sort(key=lambda o: (-o[0], 0 if o[1] == 'bump' else 1))
        base = len(pl)
        for pos, kind, data, _nm in ops:
            if kind == 'insert':
                pl[pos:pos] = data
            elif kind == 'write':
                pl[pos:pos + len(data)] = data
            else:
                struct.pack_into('<I', pl, pos, struct.unpack_from('<I', pl, pos)[0] + 1)
        self.plain = pl
        self._blds = None
        self._coffs = None
        self._ckey = None
        _sk = [k for k, v in self.unit_types().items() if v == src['type']]
        src_key_of_t = _sk[0] if _sk else None
        return {'mode': '克隆放置' if not swap_type else '克隆+换型号',
                'type': t, 'class': src['class'], 'template_type': src['type'],
                'type_key': target_key if swap_type else src_key_of_t,
                'cell': (cx, cy),
                'key': new_key, 'template_key': old_key,
                'house': tgt_hname, 'template_house': tpl_hname,
                'house_changed': house_changed,
                'veterancy': vet_out if vet_out in self.VET_NAMES else None,
                'veterancy_name': self.VET_NAMES.get(vet_out, '未定义'),
                'inst_base': (id_info or {}).get('new'),
                'inst_id': (id_info or {}).get('new'),
                'loco': tgt_loco,            # ★§200：目标移动方式（Fly/Drive/Walk…）
                'comp_from': comp_src,       # 伴随对象来自哪个模板/库
                # ★§215：把"骨架是谁、它当时是不是空闲"一起返回 —— 这是能不能正常行动的决定因素。
                #   （值在插入前就预算好了，别在这里按旧偏移重算：插入会移位）
                'scaffold_key': old_key,
                'scaffold_type': _scaffold_type,
                'scaffold_idle': _scaffold_idle,
                'scaffold_no_mission': _scaffold_no_mission,
                # ★§251：落点被"平地校验"挪走了的话，这里给出 从哪→到哪 / 原因。
                #   斜坡/悬崖格上放单位 ⇒ 打掉必弹框 + 单位"有目标"往外跑（T105 实证）。
                'cell_nudge': _nudge,
                # ★§252：任务/目的地/指令这一组怎么写的 ——
                #   `dest` 非空 = 骨架有目的地（空中单位），已改写成自己脚下那格；
                #   `kept` = 从骨架抄来的共享指令对象个数（0 就是没抄到，可能又变空对象）。
                'order_state': _order_state,
                # ★§246：清掉了多少个"模板残留的外来运行期指针"（打掉就崩的总根）。
                #   干净骨架出来的是 0；数字大说明这个档里同型号大多在"执行任务中"。
                'foreign_ptrs': _n_foreign,
                'delta': len(pl) - base, 'replaced': nrep, 'tables': len(tabs),
                'warnings': warnings,
                'note': '同类型克隆：武器/动画等子对象与模板一致'}

    def buildings(self):
        self._invalidate_if_shifted()
        """存档里的全部建筑对象（不依赖地图，也不依赖类型名单）。

        定位法（2026-10-15 v2，**正则找坐标对 → 反推对象**，比逐类型键更快也更全）：
          1. 正则匹配「两个 u32 坐标（低字节 0x80、高字节 0）」= 锚点像素坐标对 cell×256+128；
          2. 命中位置 h − 104 = 对象起点；对象 `+0x4EC` 必须是已知类型对象的自键；
          3. 耐久 `+56` ∈ (0, 0x100000]。
        返回 [{base,type,type_key,x,y,px,py,hp,house,tag,coord2_ok}]，按 base 排序。
        """
        if getattr(self, '_blds', None) is not None:
            return self._blds
        pl = self.plain
        key2name = {}
        for nm, k in self.building_types().items():
            key2name.setdefault(k, nm)
        out = {}
        cw, ch = self.coord_limit()          # 存档自带尺寸（菱形系 = 2W / 2H）
        limx, limy = (cw + 8) * 256, (ch + 8) * 256
        for m in self.COORD_RE.finditer(pl):
            h = m.start()                       # 命中的"格中心坐标对"（cell×256+128）
            px, py = struct.unpack_from('<II', pl, h)
            if not (0 < px < limx and 0 < py < limy):
                continue
            # ★两种落点都要试（2026-10-15 游戏自建对象实证）：
            #   老对象把坐标写在 +160/+600 两份（都=格中心形式）；**引擎新建的对象**在
            #   +160 写的是 iso 锚点（如 (cx+cy)×128+12），只有 +600 是格中心形式
            #   ⇒ 只按 +160 反推会漏认新建筑（玩家用外挂加的那栋就漏了）。
            for back in (self.BLD_COORD_OFF, self.BLD_COORD2_OFF):
                b = h - back
                if b < 0 or b in out or b + self.BLD_SIZE > len(pl):
                    continue
                key = struct.unpack_from('<I', pl, b + self.BLD_TYPEKEY_OFF)[0]
                nm = key2name.get(key)
                if not nm:
                    continue
                hp = struct.unpack_from('<I', pl, b + self.BLD_HP_OFF)[0]
                if not (0 < hp <= 0x100000):
                    continue
                px2, py2 = struct.unpack_from('<II', pl, b + self.BLD_COORD2_OFF)
                out[b] = {'base': b, 'type': nm, 'type_key': key,
                          'px': px, 'py': py, 'x': (px - 128) // 256,
                          'y': (py - 128) // 256, 'hp': hp,
                          'house': struct.unpack_from('<I', pl, b + self.BLD_HOUSE_OFF)[0],
                          'tag': struct.unpack_from('<I', pl, b)[0],
                          'coord2_ok': (px2 == px and py2 == py)}
                break
        self._blds = sorted(out.values(), key=lambda d: d['base'])
        return self._blds

    # ---------------- 建筑「生长动画」状态（§170 十四档定稿，2026-10-22） ----------------
    # 一句话：**"显示不全"有两种成因** ——
    #   ①`+0x534=1` 但 `+0x6E4=0`（完成标记成立、动画结束标记没写）⇒ 卡住
    #   ②`+0x534=0` 但 `+0x10C=0`（没建完、动画又不在播）⇒ 干脆不播
    # 另有 `+0xAC=5` 且 `+0x534=0` 会**一直播**（K 档实测）。
    # 合法中间态 = `+0x534=0` / `+0x10C≠0` / `+0xAC≠5`（原始档，观感最好）。
    def building_at(self, x=None, y=None, type_name=None, base=None):
        """按坐标或 base 精确取一栋建筑（找不到就报错，不返回 None）。"""
        if base is not None:
            for b in self.buildings():
                if b['base'] == base:
                    return b
            raise RuntimeError('找不到 base=0x%08X 的建筑' % base)
        for b in self.buildings():
            if b['x'] == x and b['y'] == y and (type_name is None or b['type'] == type_name):
                return b
        raise RuntimeError('找不到建筑 (%s,%s)%s' % (
            x, y, (' 类型 ' + type_name) if type_name else ''))

    def building_anim_state(self, base):
        """读一栋建筑的「建造动画」状态（§170 十四档定稿）→ dict。

        ★`state` 五档（每一种都对应过 in-game 实测）：
          * `'done'`    = `+0x534==1` 且 `+0x6E4==1` ⇒ 渲染完成图。正常，不用改。
          * `'buildup'` = `+0x534!=1` 且 `+0x10C!=0` 且 `+0xAC!=5`
                          ⇒ 引擎判"还在建造"⇒ 重载后**播一遍完整 Buildup 就停**，播完正常。
                          **合法中间态，观感最好**（原始A / I / H），**不要修**。
          * `'stuck'`   = `+0x534==1` 但 `+0x6E4==0`
                          ⇒ 矛盾态：既不接受"已完成"也不重播动画 ⇒ **显示不全**（A/B/D）。
                          修法 = 写 `+0x6E4=1`（1 字节）跳到完成图。
          * `'frozen'`  = `+0x534!=1` 但 `+0x10C==0`
                          ⇒ 建造中却"动画不在播"⇒ 根本不播、停在残缺帧 ⇒ **显示不全**（J/L）。
                          修法 = 写 `+0x10C=2`（并保证 `+0xAC!=5`）。
          * `'loop'`    = `+0x534!=1`、`+0x10C!=0` 但 `+0xAC==5`
                          ⇒ 建造动画**播不完** ⇒ 一直重播（K 档实测）。
                          修法 = 写 `+0xAC=18`。

        `pending` 为兼容旧调用保留 = `state in ('stuck','frozen','loop')`（= 需要修）。
        """
        base = int(base)
        if base - 52 < 0 or base + max(self.BLD_ANIM_OFF.values()) + 4 > len(self.plain):
            raise RuntimeError('base=0x%08X 越界' % base)
        vt = struct.unpack_from('<I', self.plain, base - 52)[0]
        if vt != self.BLD_VTABLE:
            raise RuntimeError('base=0x%08X 处不是设施建筑（vtable=%08X，期望 %08X）'
                               % (base, vt, self.BLD_VTABLE))
        vals = {}
        for k, off in self.BLD_ANIM_OFF.items():
            if k in self.BLD_ANIM_U8:
                vals[k] = self.plain[base + off]          # ★+0x6E4 是单字节标志
            else:
                vals[k] = struct.unpack_from('<I', self.plain, base + off)[0]
        finished = (vals['+0x534'] == 1)
        accepted = bool(vals['+0x6E4'])
        playing = (vals['+0x10C'] != 0)
        if finished:
            state = 'done' if accepted else 'stuck'
        elif accepted:
            # ★§193.12/13（2026-10-23 玩家三档实测）：没完工（534=0）却已被"接受"（6E4=1）
            #   ⇒ 引擎认为"已接收但没建成" ⇒ **模型停在半完成帧**（光棱塔就是没头），
            #   而且**开始按已建成算电力**（三座一起才提示"电力不足"）。
            state = 'half'
        elif not playing:
            state = 'frozen'
        elif vals['+0xAC'] == 5:
            state = 'loop'
        else:
            state = 'buildup'
        # 找类型名（仅用于提示）
        nm = ''
        try:
            for b in self.buildings():
                if b['base'] == base:
                    nm = b['type']
                    break
        except Exception:
            pass
        if state == 'half':
            txt = ('⚠ 半完成（显示残缺，如光棱塔"无头"）：+0x534=0（没建完）但 +0x6E4=1'
                   '（已被接受）⇒ 引擎把模型停在半完成帧，**而且已经按建成算电力**。\n'
                   '   修法：把 +0x6E4 改回 0（让它回到"建造中"，进游戏播一遍动画后完整）；'
                   '若想直接显示成品，就写完整完成态 AC=5 / 10C=0 / 534=1 / 6E4=1。')
        elif state == 'stuck':
            txt = ('⚠ 卡住（显示不全）：+0x534=1（已完成）但 +0x6E4=0（动画没结束标记），'
                   '引擎既不播动画也不渲染完成图 ⇒ 这栋在地图上是残缺的。\n'
                   '   修法：写 1 个字节 +0x6E4=1（跳到完成图），'
                   '或改成「建造中」形态让它重播一遍建造动画。')
        elif state == 'frozen':
            txt = ('⚠ 不会播（显示不全）：+0x534=0（没建完）但 +0x10C=0（动画不在播），'
                   '引擎既不播建造动画也不给完成图 ⇒ 又是"显示不全"。\n'
                   '   修法：写 +0x10C=2（并确保 +0xAC≠5）让它重播，或直接写成完成态。')
        elif state == 'loop':
            txt = ('⚠ 会一直播（死循环）：+0x534=0、+0x10C=2 但 +0xAC=5，'
                   '建造动画播不完 ⇒ 无限重播。\n'
                   '   修法：写 +0xAC=18（GAPOWR 的"建造中"值），或直接写成完成态。')
        elif state == 'buildup':
            if vals['+0xD0'] >= self.BLD_D0_REPLAY:
                txt = ('⚠ 建造中，但 +0xD0=%d（≥2，进度已满）⇒ 进游戏会**播两遍**完整建造动画'
                       '才正常（P/R 档 in-game 实测）。\n'
                       '   想只播一遍：把 +0xD0 改成 1（API `make_building_buildup()` 默认就写 1）。'
                       % vals['+0xD0'])
            else:
                txt = ('✅ 建造动画未播完（合法中间态，不是坏档）⇒ 进游戏重载后会自动播一遍'
                       '完整的 Buildup 然后停住，观感最好。\n'
                       '   想让它直接显示完成图才用「跳到完成态」；想要动画就什么都别改。')
        else:
            txt = '✅ 已完成（+0x534=1 且 +0x6E4=1），直接显示完成图'
        return {'base': base, 'type': nm, 'vals': vals, 'state': state,
                'done_mark': finished and accepted, 'accepted': accepted,
                'pending': state in ('stuck', 'frozen', 'loop', 'half'),   # 兼容旧字段 = 需要修
                # ★§171：buildup 且 +0xD0 过大 ⇒ 会播两遍（不是坏档，但可一键优化成一遍）
                'replay': state == 'buildup' and vals['+0xD0'] >= self.BLD_D0_REPLAY,
                'phase': vals['+0xD0'], 'text': txt}

    def anim_phase_default(self, type_key, base=None):
        """取「同类型建筑」的 `+0xD0` 众数，当完成度缺省值（§169 in-game 定案）。

        为什么不硬编码 100：`+0xD0` 的正常值随建筑类型变 —— GAPOWR=100，ATESLA=14/15/16。
        唯一可靠的取法是**在同一存档里找同类建筑的众数**（坏的那栋自己是少数派）。
        → {'phase': int|None, 'src': 'peers'|'whitelist'|'none', 'n': 同类样本数}
        """
        import collections
        cnt = collections.Counter()
        n = 0
        try:
            for b in self.buildings():
                if b['type'] != type_key or b['base'] == base:
                    continue
                off = b['base'] + self.BLD_ANIM_OFF['+0xD0']
                if off + 4 > len(self.plain):
                    continue
                cnt[struct.unpack_from('<I', self.plain, off)[0]] += 1
                n += 1
        except Exception:
            pass
        if cnt:
            return {'phase': cnt.most_common(1)[0][0], 'src': 'peers', 'n': n}
        # 无同类参照：只有"全库已知"的类型才敢给值，其余不写
        if type_key in self.BLD_ANIM_PHASE_HINT:
            return {'phase': self.BLD_ANIM_PHASE_HINT[type_key], 'src': 'whitelist', 'n': 0}
        return {'phase': None, 'src': 'none', 'n': 0}

    def repair_building_anim(self, base, force=False, phase=None, full=False):
        """把「显示不全 / 一直播」的建筑修好（§170 十四档定稿，in-game 已验）→ dict。

        ★六态语义见 `building_anim_state()`：`stuck / frozen / loop / half` 需要修。
          * `'half'`    ⇒ 把 `+0x6E4` 改回 **0**（§193.13 玩家 T21 实测：三塔全 0 ⇒ 全都有头）
          * `'stuck'`   ⇒ 补 `+0x6E4=1`（E 档：A→E 只差这 1 字节，显示正常）
          * `'frozen'`  ⇒ 写全套完成态 4 字段（`AC=5/10C=0/534=1/6E4=1`）
          * `'loop'`    ⇒ 同上（直接跳到完成图，不再无限播）
          * `'buildup'` ⇒ **拒绝**（合法中间态，重载会播完整动画；修了就没动画了）
          * `'done'`    ⇒ 不用改
        ⚠ 想让建筑**播一遍建造动画**别用这个函数，用 `make_building_buildup()`。

        `full=True` ⇒ 写全套 `BLD_ANIM_OK`（4 项），彻底对齐"原始好档"。
        `phase=<int>` ⇒ 额外写 `+0xD0`（默认不写：F 档已证与渲染无关）。
        `force=True` ⇒ 跳过状态判定，硬写。
        ⚠ 只改 1~4 个字段（`+0x6E4` 只写 **1 字节**），不动明文长度 ⇒ 不用改记录长度。
        """
        st = self.building_anim_state(base)
        old = dict(st['vals'])
        state = st['state']
        if not force and state in ('done', 'buildup'):
            why = ('这是合法中间态：进游戏会播一遍完整建造动画后自动正常，不要修'
                   if state == 'buildup' else '已经是完成态，无需修改')
            return {'old': old, 'new': dict(old), 'changed': False, 'state': state,
                    'phase_src': 'skip', 'type': st.get('type', ''), 'reason': why}
        base = int(base)
        if full:
            plan = dict(self.BLD_ANIM_OK)
        elif state == 'stuck':
            plan = dict(self.BLD_ANIM_MIN_FIX)      # 1 字节就够（A→E 已证）
        elif state == 'half':
            # ★半完成：改回"建造中"（6E4=0）⇒ 重载播一遍动画后模型完整。
            #   T21 实测：三座 6E4=0 的塔进游戏全部有头、正常。
            plan = {'+0x6E4': 0}
        else:                                       # frozen / loop
            plan = dict(self.BLD_ANIM_OK)           # 直接写成完成态 4 个字段
        if phase is None:
            # ★F 档实测：`+0xD0` 保持坏值也显示正常 ⇒ 与渲染无关，**默认不碰**
            phase_src = 'skip'
        else:
            plan['+0xD0'] = int(phase)
            phase_src = 'explicit'
        self._apply_anim_plan(base, plan)
        return {'old': old, 'new': plan, 'changed': True, 'state': state,
                'phase_src': phase_src, 'type': st.get('type', ''), 'reason': ''}

    def _apply_anim_plan(self, base, plan):
        """按 {字段: 值} 写动画字段（`+0x6E4` 只写 1 字节）。"""
        base = int(base)
        for k, want in plan.items():
            k = self.BLD_ANIM_OFF_ALIAS.get(k, k)
            o = base + self.BLD_ANIM_OFF[k]
            if k in self.BLD_ANIM_U8:
                self.plain[o] = int(want) & 0xFF      # ★单字节：不碰高 3 字节
            else:
                struct.pack_into('<I', self.plain, o, int(want) & 0xFFFFFFFF)
        self._live_len = None

    def make_building_buildup(self, base, full=True, ac=None, fresh=False):
        """让一栋建筑**重播一遍完整建造动画**（§170 十四档 + §171 四档，in-game 定稿）→ dict。

        `fresh=True`（**2026-10-23 §195 起，「直接落成」走这条**）
            ⇒ 写「**刚放下**」态 `AC=-1 / BC=0 / 10C=0 / 534=1 / 6E4=0`（`+0xD0` 不写，沿用模板）。
            这是玩家档里"刚把楼放下去、还没开始播"的真实状态（T43 三塔全正常的那一档）。
            ★为什么必须用「刚放下」而不是「完成态」：`done` 会让引擎认为楼已建好 ⇒
              **不再走建造流程 ⇒ 不创建炮塔/棱镜等子对象 ⇒ 永远无头**。
              「刚放下」等于告诉引擎"这楼还没建完"，读档后它会自己播一遍并把子对象建出来。
            实测定案（T42/T43 同基档对照）：
              · `AC=5/6E4=1`（done）的塔 → **无头**；
              · `AC=-1/534=1/6E4=0`（刚放下）的塔 → **完整带棱镜**。

        `fresh=False`（默认，向后兼容）⇒ 老配方 `AC=18/BC=1/D0=1/10C=2/534=0`
            = 「**正在播动画**」。§193.20 已证它不等于"刚放下"，引擎读档后**不会从头播**。

        工序：完成态（`AC=5, 10C=0, 534=1, 6E4=1`）→ 建造中（`AC=18, BC=1, D0=1, 10C=2, 534=0`）。
        in-game 已验：M 档（GAPOWR 完成态→建造中）**播一遍建造动画，正常**；
        N2 档（同上配方写在新加的 ATESLA/光棱塔上）**也播一遍，正常** ⇒ **跨类型通用**。

        ★★`+0xD0` **必须写 1**（§171 P/Q/R 三档单变量）：
          只改 `D0=100`（P）⇒ 播两遍；只改 `BC=0`（Q）⇒ 播一遍；两项都改（R）⇒ 播两遍
          ⇒ 「多播一遍」的唯一原因是 `+0xD0 ≠ 1`，`+0xBC` 无关。
          ⚠ 老 MIN 配方只写 AC/10C/534，会把完成态的 `D0=100` 留下 ⇒ 一定播两遍。

        `full=True`（**默认**，= M 档原样）⇒ 5 个字段，含 `+0xBC=1`。
        `full=False`            ⇒ 4 个字段（省 `+0xBC`，Q 档证明播放次数不受影响）。
        `ac=None`   ⇒ 默认 18（跨类型已验证），可显式覆盖。
        `+0x6E4` **一律不写**（A vs H 已证建造分支无关，H 档 `6E4=1` 也照样播）。
        """
        st = self.building_anim_state(base)
        old = dict(st['vals'])
        tk = st.get('type', '')
        if fresh:
            plan = dict(self.BLD_ANIM_FRESH)
        else:
            plan = dict(self.BLD_ANIM_BUILDUP_FULL if full else self.BLD_ANIM_BUILDUP_MIN)
            if ac is not None:
                plan['+0xAC'] = int(ac)
            elif tk in self.BLD_ANIM_AC_BUILDUP:
                plan['+0xAC'] = self.BLD_ANIM_AC_BUILDUP[tk]
        self._apply_anim_plan(base, plan)
        return {'old': old, 'new': plan, 'changed': True, 'type': tk,
                'state_before': st['state'], 'fresh': bool(fresh),
                'replay': bool(st['state'] == 'buildup' and old.get('+0xD0', 0) >= self.BLD_D0_REPLAY)}

    # ---------------- 赠送建筑（2026-10-15 定案，游戏内验收：T3"就绪"✅） ----------------
    # 建筑组：[u32 组计数][ 记录×N ]，记录 = [16B 类 GUID][1844B 载荷] = 1860B
    BLD_REC_GUID = bytes.fromhex('c62d270e0f9cd111b70900a024ddafd1')
    FACTORY_GUID = bytes.fromhex('a8d9ec34b00ad211aca7006008055bb5')
    QUEUE_ANCHOR_GUID = bytes.fromhex('666dbb2e4d0dd2118172006008055bb5')
    HOUSE_GUID = bytes.fromhex('10a9d4d9c687d111b70700a024ddafd1')
    # FactoryClass 104B 模板（「电厂就绪未放下.SAV」逐字节照抄；+20 自键按档改写避撞）
    FACTORY_BLOB = bytes.fromhex(
        '01000000a8d9ec34b00ad211aca7006008055bb5804f2210d0887e00b4887e00'
        'ac887e00a4887e007fa30f009899e93f00000000000000000060221036000000'
        '010000001d0200008bfd620000000000000000000100000034897e0000000000'
        '000000000100ffff')
    QUEUE_CONST_PTR = 0x0f8e63e0      # 队列条目固定指针（三档恒在，照抄）

    # ---- rules 解析（可建造判据：TechLevel>=1，用户钦定 2026-10-15）----
    # ★★2026-10-28（审查 P0-4 / P2-12）：原来这里写死三条**开发者本机**路径，
    #   而 `SaveDoc.__init__` 根本不接受游戏目录 ⇒ 玩家把游戏装在 D:\Games\RA2 时
    #   永远读不到 rules，可建造判定 / Foundation / Power 全部**静默失效**
    #   （不报错，只是结果不对）。现在改成动态解析，优先级：
    #     ① `set_game_dir()` 显式指定（GUI 灌进来）
    #     ② 存档所在目录及其上溯（存档常在 <GameDir>\ 或 <GameDir>\save\ 下）
    #     ③ `game_ini.find_sources()` —— 还能从 MIX 里抽、能回落到内置副本
    #     ④ `_LEGACY_RULES_PATHS`（只为兼容老机器/老版本行为，不再作为主路径）
    GAME_DIR = None                    # 游戏安装目录（None = 未指定，就近找）
    _recent_save_dirs = []             # 最近打开过的存档目录（最多 4 个）
    _rules_files = None                # 解析结果缓存：(rules_path, art_path)
    _rules_all_cache = None            # rules 全量节表 {节名: {键: 值}}（武器/弹头名字表用）
    _LEGACY_RULES_PATHS = [
        r'E:\YURI\rulesmd.ini',
        r'E:\YURI\rulesmd - 原版副本.ini',
        r'C:\Users\zhao5\Desktop\WorkBuddy\红警MOD制作素材工具与教程\rulesmd - 原版副本.ini',
    ]
    _rules_cache = None
    _rules_buildcat = {}          # {类型名: BuildCat}（Combat ⇒ 侧栏防御栏/槽组2）
    _rules_prereq = {}            # {类型名: Prerequisite 原串}（用于就绪赠送的前置诊断）
    _rules_image = {}             # {类型名: Image=}（→ artmd 节名，用于查 Foundation）
    _rules_power = {}             # {类型名: Power=}（正=发电 / 负=耗电；2026-10-17）
    _rules_order = []             # [BuildingTypes] 的 0 基顺序（侧栏槽位 A 值）
    # rules 前置里的别名 ⇒ 实际类型（判"前置是否满足"用）
    _PREREQ_ALIAS = {'POWER': ('GAPOWR', 'NAPOWR'), 'PROC': ('GAREFN', 'NAREFN'),
                     'BARRACKS': ('GAPILE', 'NAHAND'), 'RADAR': ('GAAIRC', 'NARADR'),
                     'FACTORY': ('GAWEAP', 'NAWEAP'), 'TECH': ('GATECH', 'NATECH')}

    # ---- 规则文件定位（2026-10-28 新增，见上面 P0-4 注释）----------------
    @classmethod
    def set_game_dir(cls, d):
        """告诉 core「游戏装在哪」—— GUI 在开档 / 改目录时调一次。

        传 None / 空 / 不存在的目录 = 清掉显式设置，回到"就近找"。总是清缓存。
        """
        nd = (str(d).strip() or None) if d else None
        if nd and not os.path.isdir(nd):
            nd = None
        cls.GAME_DIR = nd
        cls._reset_rule_cache()

    @classmethod
    def note_save_dir(cls, path):
        """记下"刚打开过哪个目录的存档"—— 没配游戏目录的玩家也能就近找到 rules。"""
        try:
            d = os.path.dirname(os.path.abspath(path))
        except Exception:
            return
        if not d:
            return
        lst = cls._recent_save_dirs
        _same = bool(lst) and lst[0] == d
        if d in lst:
            lst.remove(d)
        lst.insert(0, d)
        del lst[4:]                    # 只留最近 4 个，别越攒越多
        if not _same:
            # 目录没变就别清缓存 —— 否则批量开档（列表页预读 12 档）会反复重解析 rules
            cls._reset_rule_cache()

    @classmethod
    def _reset_rule_cache(cls):
        cls._rules_files = None
        cls._rules_cache = None
        cls._rules_all_cache = None
        cls._foundation_cache = None
        cls._rules_buildcat = {}
        cls._rules_prereq = {}
        cls._rules_image = {}
        cls._rules_power = {}
        cls._rules_order = []

    @classmethod
    def _resolve_rule_files(cls):
        """→ `(rules_path, art_path)`，找不到是 `(None, None)`；结果缓存在 `_rules_files`。"""
        if cls._rules_files is not None:
            return cls._rules_files
        save_dir = cls._recent_save_dirs[0] if cls._recent_save_dirs else None
        rp = ap = None
        # ① game_ini 那条成熟链路（显式目录 > 存档目录及上一级 > MIX 抽取 > 内置）
        try:
            import game_ini
            _src, _d, rp, ap = game_ini.find_sources(save_dir, cls.GAME_DIR)
        except Exception:
            rp = ap = None
        # ② 兜底：老清单（保住开发机 / 老版本行为）
        if not rp:
            for p in cls._LEGACY_RULES_PATHS:
                if os.path.exists(p):
                    rp = p
                    if not ap:
                        cand = os.path.join(os.path.dirname(p), 'artmd.ini')
                        ap = cand if os.path.exists(cand) else None
                    break
        cls._rules_files = (rp, ap)
        return cls._rules_files

    @classmethod
    def rules_source(cls):
        """规则文件实际来自哪（给界面显示 / 诊断用）。"""
        rp, ap = cls._resolve_rule_files()
        return {'rules': rp, 'art': ap, 'game_dir': cls.GAME_DIR, 'ok': bool(rp)}

    @classmethod
    def _rules_all(cls):
        """把 rules ini **全量**解析成 `{节名: {键: 值}}`（类级缓存，换游戏目录才失效）。

        `_rules_info` 只抽了 TechLevel/BuildCat/… 那几项；武器/弹头/抛射体要的是
        `Damage`/`ROF`/`Verses`/`Warhead=` 这些，所以单独来一份全量的。
        """
        if cls._rules_all_cache is not None:
            return cls._rules_all_cache
        out = {}
        rp, _ap = cls._resolve_rule_files()
        if rp and os.path.exists(rp):
            try:
                raw = open(rp, 'rb').read()
                txt = None
                for enc in ('utf-16', 'utf-8', 'gbk'):
                    try:
                        txt = raw.decode(enc)
                        if '[' in txt:
                            break
                    except Exception:
                        continue
                cur = None
                for line in (txt or '').splitlines():
                    line = line.split(';')[0].strip()
                    if not line:
                        continue
                    if line.startswith('[') and line.endswith(']'):
                        cur = line[1:-1].strip()
                        out.setdefault(cur, {})
                        continue
                    if cur and '=' in line:
                        k, v = line.split('=', 1)
                        out[cur].setdefault(k.strip(), v.strip())
            except Exception:
                out = {}
        cls._rules_all_cache = out
        return out

    @classmethod
    def _rules_info(cls):
        """解析 rules ini → (建筑类型名集合, {类型名: TechLevel})；读不到返回 ({}, {})"""
        if cls._rules_cache is not None:
            return cls._rules_cache
        buildings, tl = set(), {}
        cls._rules_buildcat = {}
        cls._rules_prereq = {}
        cls._rules_image = {}
        cls._rules_power = {}
        # ★2026-10-28：`_rules_order` 以前用 `if not hasattr(cls, '_rules_order')` 惰性建表，
        #   缓存一失效就**接着往后 append** ⇒ 序号翻倍、侧栏槽位全错。现在每次解析先清空。
        cls._rules_order = []
        _rp, _ap = cls._resolve_rule_files()
        paths = [_rp] if _rp else list(cls._LEGACY_RULES_PATHS)
        for p in paths:
            if not os.path.exists(p):
                continue
            try:
                raw = open(p, 'rb').read()
                for enc in ('utf-16', 'utf-8', 'gbk'):
                    try:
                        txt = raw.decode(enc)
                        if '[' in txt:
                            break
                    except Exception:
                        continue
                sec = None
                for line in txt.splitlines():
                    line = line.split(';')[0].strip()
                    if line.startswith('[') and line.endswith(']'):
                        sec = line[1:-1].strip()
                        continue
                    if not sec or '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    k = k.strip().upper()
                    v = v.strip()
                    if sec.upper() == 'BUILDINGTYPES':
                        nm = v.split(',')[0].split(':')[0].strip().upper()
                        if nm:
                            buildings.add(nm)
                            cls._rules_order.append(nm)
                    elif sec in buildings or k in ('TECHLEVEL', 'BUILDCAT', 'PREREQUISITE',
                                                   'IMAGE', 'POWER'):
                        if k == 'TECHLEVEL':
                            try:
                                tl[sec.upper()] = int(v)
                            except ValueError:
                                pass
                        elif k == 'BUILDCAT':
                            # ★BuildCat 决定侧栏归属：Combat ⇒ 「防御」栏（槽组2），
                            #   其余（Power/Resource/Tech/…）⇒ 「建筑」栏（槽组1）。
                            cls._rules_buildcat[sec.upper()] = v.strip().upper()
                        elif k == 'PREREQUISITE':
                            cls._rules_prereq[sec.upper()] = v.strip().upper()
                        elif k == 'IMAGE':
                            # art 节名可与类型名不同（Foundation 表按 art 节名索引）
                            cls._rules_image[sec.upper()] = v.strip().upper()
                        elif k == 'POWER':
                            try:
                                cls._rules_power[sec.upper()] = int(float(v))
                            except ValueError:
                                pass
                if buildings:
                    break
            except Exception:
                continue
        cls._rules_cache = (buildings, tl)
        return cls._rules_cache

    _art_table_cache = None

    @classmethod
    def _type_sig_table(cls):
        """类型签名表（core/type_signature.json）：每类型"天然实例间恒定"的
        dword 字段快照（排除了键/序号/坐标/耐久/所属方/指针值域）。
        跨类型克隆时整体覆写 —— 只改类型键+美术尺寸不够（ATESLA/GADEPT 显示不全教训）。"""
        if cls._art_table_cache is not None:
            return cls._art_table_cache
        out = {}
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'type_signature.json')
        if not os.path.exists(p) and getattr(sys, 'frozen', False):
            p = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                             '_internal', 'type_signature.json')
        try:
            out = json.load(open(p, encoding='utf-8'))
        except Exception:
            out = {}
        cls._art_table_cache = out
        return out

    _SIG_HANDLED = {0, 20, 112, 116, 1352, 1316, 1608, 1612, 160, 164, 600, 604,
                    1616, 1620, 208, 1828, 1832, 1836, 1840,
                    1388, 1392, 1396, 1400}

    @classmethod
    def _buildable_index(cls, type_name):
        """类型在 rules [BuildingTypes] 的 **0 基序号**（= 侧栏 strip 槽的 A 值，
        2026-10-15 实证：GAPOWR 0→0、GAREFN 1→1、GAPILE 3→3、GAWEAP 7→7、GAOREP 73→73）。
        不可建造（无序号）返回 None。"""
        buildings, _tl = cls._rules_info()
        if not buildings:
            return None
        order = cls._rules_order
        try:
            return order.index(type_name.upper())
        except (ValueError, AttributeError):
            return None

    def template_health(self, g):
        """记录与「类型签名」的吻合度 → `(bad, tot)`；`tot == 0` = 没有该类型签名，无法判断。

        ★§184（2026-10-22）：用来识别"**那栋同类建筑本身就是我们放坏的**"。
        实测（玩家档 ALL01UMD，6 栋）：
          GAPOWR ×4 = 6/352、6/352、6/352、**9/352**；GACNST = 2/309；
          我们早先跨类型放的那栋 ATESLA = **22/313**（带 83 个模板遗留指针）。
        ⇒ 阈值 `bad < max(8, tot*5%)` 能把"原生/健康"与"被我们放坏"分开。
        """
        try:
            key = struct.unpack_from('<I', self.plain, g + 16 + 1316)[0]
        except Exception:
            return 0, 0
        name = next((n for n, k in self.building_types().items() if k == key), None)
        info = self._type_sig_table().get((name or '').upper()) or {}
        sig = info.get('sig') or {}
        if not sig:
            return 0, 0
        bad = tot = 0
        for off_s, v in sig.items():
            off = int(off_s)
            if off in self._SIG_HANDLED or off + 4 > self.BLD_SIZE:
                continue
            tot += 1
            if struct.unpack_from('<I', self.plain, g + 16 + off)[0] != v:
                bad += 1
        return bad, tot

    @staticmethod
    def _health_ok(bad, tot):
        """健康判据：无签名数据时放行；有签名时允许 < 5% 且 < 8 个字段不符。"""
        return (tot == 0) or (bad < max(8, tot * 5 // 100))

    def same_type_best(self, type_name):
        """本档该类型里**最健康**的一栋 → `(g, bad, tot)`；一栋都没有返回 `(None, 0, 0)`。"""
        tkey = self.building_types().get(str(type_name).upper())
        if not tkey:
            return None, 0, 0
        best = (None, 0, 0)
        for g in self._bld_group()[0]:
            if struct.unpack_from('<I', self.plain, g + 16 + 1316)[0] != tkey:
                continue
            bad, tot = self.template_health(g)
            if best[0] is None or (tot and bad < best[1]):
                best = (g, bad, tot)
        return best

    def add_placed_readiness(self, type_name=None):
        """能不能「直接落成」一栋建筑 → `(ok, 给玩家的一句话原因)`。

        2026-10-17：全新开局的对战档里**一栋建筑都没有**（如 `ALL01UMD.sav` / `SAVE4823.SAV`），
        建筑组 `[u32 n][记录…]` 整组不存在 ⇒ 无处克隆、无处追加。
        从零造整组（含三张登记表 / 阵营建筑清单 / 格子占用）还没实装 ⇒ 这里诚实说明。

        ★§184（2026-10-22 玩家「还是显示不全」定案）：给了 `type_name` 就再查一层 ——
          **本档必须有一栋"健康的同类建筑"可当模板**。否则克隆出来就是残缺楼
          （跨类型会把模板的指针表整块带过来；克隆我们早先放坏的那栋更是直接继承残缺）。
          ⇒ 这种情况直接劝退，让玩家走「⚡ 触发法」（引擎自己造，模型一定完整）。
        """
        self._last_readiness_note = ''          # 每次调用先清空，避免残留上一条提示
        try:
            hs, cnt = self._bld_group()
        except Exception as ex:
            return (False, '建筑组结构异常（%s）' % ex)
        if not hs:
            return (False, '这份存档里**一栋建筑都没有**（全新开局），没有建筑组可以克隆/追加')
        if type_name:
            tn = str(type_name).split('（')[0].strip().upper()
            g, bad, tot = self.same_type_best(tn)
            if g is None:
                # ★★★★★ 2026-10-23 §197：**跨类型放置已放开**（本档没有该类型也能放）。
                #   前提：语料库里有该类型的签名数据（184 种，见 core/type_signature.json）。
                #   做法：借一栋"原生 + 已落位"的楼当骨架 → 换类型键/art/hp → 套目标类型签名
                #        → 清模板遗留指针 → 写「刚放下」态（§196）→ 登记所属方/电力/阵营清单。
                #   ⚠ 跨类型仍是高风险：骨架里"必须指向别的对象"的槽没法凭空造出来，
                #     进游戏可能显示不全 —— 所以 UI 要提示"先备份、进游戏确认"。
                info = self._type_sig_table().get(tn)
                if not info or not info.get('sig'):
                    return (False,
                            '本档没有「%s」，而且语料库里也没有该类型的签名数据（目前覆盖 184 种）'
                            '⇒ 无法跨类型放置。\n请改用「⚡ 触发法」。' % tn)
                self._last_readiness_note = (
                    '跨类型放置：本档没有「%s」，将借一栋别的建筑当骨架、套上「%s」的类型签名。\n'
                    '所属方 / 电力 / 阵营建筑清单 / 「刚放下」态都会写对，但骨架里必须指向'
                    '别的对象的槽无法凭空造出来 —— **请先备份存档，进游戏确认模型是否完整**。'
                    % (tn, tn))
                return (True, '')
            # ★★2026-10-23 §195（T43 实测）：**签名判据不再阻断同类型克隆**。
            #   证据：T43 里那座"玩家直接放下、进游戏完全正常"的塔，被签名判成 33/313 不符；
            #   而 T15 那座"当模板克隆出来就无头"的塔，签名反而只有 22/313。
            #   ⇒ 签名吻合度与"能不能当模板"**没有相关性**（签名表本身把静态表地址、
            #   键、帧计时器都当成固定值采样了）。拿它拦人只会拦住健康模板。
            #   现在只把它降级成一条提示，真正的判据交给 `same_type_best()` 的排序 + 实测。
            if not self._health_ok(bad, tot):
                self._last_readiness_note = (
                    '提示：本档「%s」与签名差 %d/%d 个字段。签名表不可靠（会把静态地址/键/'
                    '帧计数当固定值），**不影响克隆**；若进游戏发现新楼残缺，多半是模板本身'
                    '是我们早先放坏的，换一栋同类楼当模板即可。' % (tn, bad, tot))
        return (True, '')

    def can_add_placed(self):
        """直接落成是否可用。"""
        return self.add_placed_readiness()[0]

    def _bld_group(self):
        """建筑组定位。返回 (guid_offs, count_off)；组计数≠记录数时拒绝（防弹框）。"""
        pl = self.plain
        hs, q = [], 0
        while True:
            i = pl.find(self.BLD_REC_GUID, q)
            if i < 0:
                break
            hs.append(i)
            q = i + 1
        if not hs:
            return [], None
        cnt_off = hs[0] - 4
        n = struct.unpack_from('<I', pl, cnt_off)[0]
        if n != len(hs):
            raise RuntimeError('建筑组计数 %d ≠ 记录数 %d（档结构异常，拒绝写入）' % (n, len(hs)))
        return hs, cnt_off

    @staticmethod
    def _free_key(pl, start):
        k = start
        while pl.find(struct.pack('<I', k)) >= 0:
            k += 4
        return k

    def _bld_tail(self, pl, hs):
        """**建筑组的真实组尾**（新记录必须插在这）。

        建筑组后面固定紧跟一条结构：`0c 00 00 00` + GUID
        `d9338bae-1c06-11d2-aca4-008008055bb5`（四个母本实测全一致）。
        ★记录长度**类型特有**（多数 1860；GAAIRC/AMRADR 等载荷 1856 ⇒ 1872），
        所以**不能**一律按 `hs[-1] + 1860` 算组尾 —— 末栋是长记录时那样会把新记录
        插进它尾部的 12 字节里，等于破坏那栋建筑（2026-10-16 弹框真因之一）。
        """
        p = bytes(pl).find(self.BLD_TAIL_MARK, hs[-1])
        if 0 < p - hs[-1] < 4096:
            return p
        return hs[-1] + self.BLD_SIZE

    # ------------------------------------------------------------------ #
    # 克隆模板选择（2026-10-17 定案 = 「N27 弹框」的真因修法）
    # ------------------------------------------------------------------ #
    INJ_KEY_LO = 0x13963000      # 我们分配新建筑键的起点（_free_key 从这开始递增）
    INJ_KEY_HI = 0x13968000      # 上限（只放两三栋，留足余量）

    def _is_injected_key(self, k):
        """该建筑键是否由**我们注入**（而非引擎原生写的记录）。

        两个判据（任一成立即算注入）：
          ① 键在本次会话的注入表里（最可靠）；
          ② 键落在我们的键池区间 —— 存档被关掉再打开时注入表会丢，用区间兜底
             （引擎重存过的新档会**重新分配全部键**，所以区间判据不会长期误伤）。
        """
        if k in getattr(self, '_injected_keys', ()):
            return True
        return self.INJ_KEY_LO <= k < self.INJ_KEY_HI

    def _pick_bld_template(self, hs, pl, tkey):
        """选一份"干净"的克隆模板 → `(tpl 偏移, cross 布尔)`。

        ★★★★★ 2026-10-17 定案（`N27_苏俄电厂+塔` 载入弹框的真因）：
          旧写法跨类型时 `tpl = hs[-1]` = **本档最后一栋**。连着加两栋不同类型时，
          第一栋刚被我们插在组尾 ⇒ 第二栋就**克隆了我们刚注入的那条记录**（异类型 + 我们的补丁），
          再把目标类型的签名盖上去 ⇒ **混血记录** ⇒ 引擎载入弹框。
          实测铁证：N27 的 ATESLA 与能载入的 N20 有 64 处差异，**57 处逐字节等于
          N27 里那栋 GAPOWR**（`+24=0x1b`、`+80=0x1e`、`+100=0x3c`… 全是电厂记录的
          动画/实例态值），而 N20 的是本档 GAAIRC 的值。
        ⇒ 规则（按优先级）：
          ① 同类型 + **原生** + **已落位**（末栋优先）；
          ② 同类型 + **已落位**（哪怕是注入的）；
          ③ 同类型 + 原生（可能是未落位的空壳）；
          ④ 同类型任意；
          ⑤ 跨类型：从**原生 + 已落位**的记录里取（末栋优先）；
          ⑥ 一栋都没有才退回 `hs[-1]`。

        ★★★★★ 2026-10-17 补（玩家档"找不到建筑"暴露）：**"未落位"记录（坐标 = (-1,-1)）是空壳** ——
          它的动画/实例态字段全 0（`+24=3`、`+80=0`、`+100=0`…），拿它当模板克隆出来的建筑
          **在游戏里可能根本不出现**。实测：`韩国无任何建筑.SAV` 的**末栋**正好是 `ATESLA @(-1,-1)`，
          所以 R1/R2 与 GUI 自己放的那栋全都克隆了这个空壳 ⇒ 玩家"找不到建筑"。
          ⇒ 现在按 **(已落位 > 空壳)** 优先；同组内仍取末栋（保持"只加一栋"的旧行为）。
        """
        def placed(g):
            cx = struct.unpack_from('<I', pl, g + 16 + 600)[0]
            cy = struct.unpack_from('<I', pl, g + 16 + 604)[0]
            return ((cx - 128) // 256) >= 0 and ((cy - 128) // 256) >= 0

        same, native = [], []
        for g in hs:
            is_same = struct.unpack_from('<I', pl, g + 16 + 1316)[0] == tkey
            is_native = not self._is_injected_key(struct.unpack_from('<I', pl, g + 16)[0])
            if is_same:
                same.append((g, is_native, placed(g)))
            if is_native:
                native.append((g, placed(g)))
        for want_native, want_placed in ((True, True), (False, True), (True, False), (False, False)):
            grp = [g for g, nv, pv in reversed(same)
                   if nv == want_native and pv == want_placed]
            if grp:
                # ★§184：同类有多栋时取**与类型签名最吻合**的那栋（早先放坏的那栋会被跳过）
                return min(grp, key=lambda _g: self.template_health(_g)[0]), False
        # ★§184：跨类型模板要挑「指针表最干净」的那栋 —— 原来固定取"末栋原生建筑"，
        #   而末栋常常是 **GACNST 建造厂（85 个运行期指针）**，整块指针被克隆进新楼
        #   ⇒ 新楼渲染残缺（玩家：「显示不全，打一下才正常」）。
        #   现在按「0x0F000000~0x14000000 段 dword 数」最少来挑（同分取末栋，保持旧行为）。
        def _ptr_n(g):
            return sum(1 for off in range(16, self.BLD_SIZE - 4, 4)
                       if 0x0F000000 <= struct.unpack_from('<I', pl, g + off)[0]
                       <= 0x14000000)

        _cand = [g for g, pv in native if pv] or [g for g, _pv in native]
        if _cand:
            return min(reversed(_cand), key=_ptr_n), True
        return hs[-1], True

    # ------------------------------------------------------------------ #
    # 伴随对象（476B，类 GUID c32d270e）—— 定位与"从零构造"
    # ------------------------------------------------------------------ #
    def companion_offsets(self, pl=None):
        """定位本档全部伴随对象 → [(对象起点, 自身键)]。"""
        pl = bytes(self.plain) if pl is None else bytes(pl)
        out, q = [], 0
        while True:
            i = pl.find(self.COMP_MARK, q)
            if i < 0:
                break
            q = i + 1
            s = i - 0x10
            if s < 0 or s + self.COMP_SIZE > len(pl):
                continue
            out.append((s, struct.unpack_from('<I', pl, s + self.COMP_KEY_OFF)[0]))
        return out

    def _find_comp_obj(self, pl, ck):
        """按伴随键定位**伴随对象起点**（自身键在 对象+0x20，类 GUID 前 4 字节在 对象+0x10）。
        找不到返回 None。★偏移无关，删字节前后都能用。"""
        pat, q = struct.pack('<I', ck), 0
        while True:
            i = pl.find(pat, q)
            if i < 0:
                return None
            q = i + 1
            s = i - 0x20
            if s >= 0 and s + self.COMP_SIZE <= len(pl) \
                    and pl[s + 0x10:s + 0x14] == self.COMP_MARK:
                return s

    def _next_inst_id(self, pl, hs, extra=()):
        """下一个可用的**实例 ID**。

        ★建筑记录的 `+20` 与伴随对象的 `+52` **共用同一个 ID 计数器**：
        母本 11 栋的 `+20`（0xfa357/0xfa35a/0xfa360/…）与 16 个伴随的 `+52`
        （0xfa35c~0xfa35f/0xfa362/0xfa365/…）**在同一区间交错、互不重复**。
        ⇒ 克隆伴随时也必须给它换一个全新 ID，否则重复（这很可能就是
        `comp_mode='clone'` 当年"游戏卡死"的原因）。

        `extra` = 本次已经分配、但**还没写进 pl** 的 ID（例如同一栋建筑的 `+20`），
        必须一起避开，否则建筑与伴随会撞 ID。
        """
        vals = [struct.unpack_from('<I', pl, g + 16 + 20)[0] for g in hs]
        vals += [struct.unpack_from('<I', pl, s + 0x34)[0]
                 for s, _k in self.companion_offsets(pl)]
        vals += [v for v in extra if v]
        v = (max(vals) + 0x10) if vals else 0x000FA300
        while struct.pack('<I', v) in pl or v in extra:
            v += 4
        return v

    def companion_const_offsets(self, pl=None):
        """本档全部伴随对象里**逐 dword 恒定**的偏移集合（= 类常量，改动它们没意义）。"""
        pl = bytes(self.plain) if pl is None else bytes(pl)
        comps = self.companion_offsets(pl)
        if not comps:
            return set()
        bodies = [pl[s:s + self.COMP_SIZE] for s, _ in comps]
        out = set()
        for off in range(0, self.COMP_SIZE - 3, 4):
            vals = {struct.unpack_from('<I', b, off)[0] for b in bodies}
            if len(vals) == 1:
                out.add(off)
        return out

    def companion_proto(self, pl=None):
        """由本档现有伴随对象求「类常量原型」：逐 dword 取**全档恒定**的值，
        变动字段一律 0。返回 476 字节 body（+0x20/+0xC0/+0xC4 由调用方填），无伴随时 None。

        ★理由：引擎**自己新建**的伴随对象里也带着一堆临时指针（0x02CAxxxx 等），
        说明加载器不依赖变动字段；而"整份克隆模板伴随"会带走模板的悬空指针 ⇒ 游戏卡死。
        ⇒ 正确姿势 = 只抄类常量 + 写自身键/格，其余清零。
        """
        pl = bytes(self.plain) if pl is None else bytes(pl)
        comps = self.companion_offsets(pl)
        if not comps:
            return None
        bodies = [pl[s:s + self.COMP_SIZE] for s, _ in comps]
        body = bytearray(self.COMP_SIZE)
        for off in range(0, self.COMP_SIZE - 3, 4):
            vals = [struct.unpack_from('<I', b, off)[0] for b in bodies]
            if len(set(vals)) == 1:
                struct.pack_into('<I', body, off, vals[0])
        body[0x10:0x20] = bodies[0][0x10:0x20]      # 类 GUID 原样
        struct.pack_into('<I', body, self.COMP_KEY_OFF, 0)
        struct.pack_into('<I', body, self.COMP_CELLX_OFF, 0)
        struct.pack_into('<I', body, self.COMP_CELLY_OFF, 0)
        return bytes(body)

    # ------------------------------------------------------------------ #
    # 建筑动画对象（476B，类 GUID c32d270e）—— §194（2026-10-23 玩家实测闭环）
    # ------------------------------------------------------------------ #
    #   ★为什么必须有它（三档实测，见 docs §193.17 / §193.21 / §194）：
    #     · 只写"完成态字段"（AC=5/10C=0/534=1/6E4=1）⇒ 电力对、但模型停在
    #       **动画第 0 帧**（光棱塔只剩底座）；
    #     · 引擎"一栋楼建好"时**必定新建一个 476B 动画对象**并挂到楼记录 `+0x57C`；
    #     · 所以"直接落成"= 楼记录 + 建造态字段 + **一个动画对象**，三样齐才完整。
    #   ★记录布局（GUID 就是记录起点，长度 476，count 在首条 −4）：
    #     +0x00 CLSID(16) / +0x10 自身键 / +0x14 四个 vtable(16) / +0x24 实例ID
    #     +0x74/+0x7C/+0xA4/+0xF4/+0xFC/+0x124 = K+0x90 / K+0x70 / K+0x71 / K+0x120 / K+0x100 / K+0x70
    #     +0xAC = (K & 0xFFFF0000) | 0x0101
    #     +0xB0/+0xB4 = 格X/格Y **×256** / +0xB8 = Z（地形高度，= 楼记录 +168）
    ANIM_SIZE = 476
    ANIM_KEY_OFF = 0x10
    ANIM_ID_OFF = 0x24
    ANIM_CELLX_OFF = 0xB0
    ANIM_CELLY_OFF = 0xB4
    ANIM_CELLZ_OFF = 0xB8
    ANIM_KREF = ((0x74, 0x90), (0x7C, 0x70), (0xA4, 0x71),
                 (0xF4, 0x120), (0xFC, 0x100), (0x124, 0x70))
    ANIM_KREF_HI = 0xAC
    #   楼记录里指向动画对象的槽（按序取空位；实测 GACNST/AMRADR 会用 2~3 个，
    #   普通楼只用 +0x57C）
    ANIM_SLOTS = (0x57C, 0x580, 0x584, 0x590)
    BLD_Z_OFF = 168                  # 楼记录的 Z（×416），与动画对象 +0xB8 必须相等

    def anim_records(self, pl=None):
        """本档全部**建筑动画对象** → [(记录起点, 自身键)]（记录起点 = GUID 起点）。

        ★与 `companion_offsets()` 的区别（§194 实测，2026-10-23）：
          那个旧函数返回 `GUID − 16`（把上一条记录的尾巴 + 数组 count 也算进"记录"），
          于是"追加到数组末尾"实际落在最后一条记录的**中间**（GUID+460）⇒ 必然写坏。
          本函数返回**真正的记录起点**，记录长 476、count 在首条 −4。
        """
        pl = bytes(self.plain) if pl is None else bytes(pl)
        out, q = [], 0
        while True:
            i = pl.find(self.COMP_MARK, q)
            if i < 0:
                break
            q = i + 1
            if i + self.ANIM_SIZE > len(pl):
                continue
            v = struct.unpack_from('<I', pl, i + 0x14)[0]
            if not (0x007E0000 <= v <= 0x00800000):     # vtable[0] = 0x7E3354 一族
                continue
            out.append((i, struct.unpack_from('<I', pl, i + self.ANIM_KEY_OFF)[0]))
        return out

    def anim_proto(self, pl=None):
        """由本档现有动画对象求「类常量原型」：逐 dword 取全档恒定值，其余清零。

        依据（§194）：引擎**自己新建**这条对象时，那些"每条都不一样"的字段全是 0
        —— 黄金档第 9 条（= 塔建好时新造的那条）119 个 dword 里 76 个恒定，
        其余大面积为 0 ⇒ "只抄类常量 + 现场写键/格" 与引擎做法等价；
        而"整份克隆某条"会把模板的悬空指针一起搬走（历史上 comp_mode='clone' 卡死的原因）。
        """
        pl = bytes(self.plain) if pl is None else bytes(pl)
        recs = self.anim_records(pl)
        if not recs:
            return None
        bodies = [pl[g:g + self.ANIM_SIZE] for g, _k in recs]
        body = bytearray(self.ANIM_SIZE)
        for off in range(0, self.ANIM_SIZE - 3, 4):
            vals = {struct.unpack_from('<I', b, off)[0] for b in bodies}
            if len(vals) == 1:
                struct.pack_into('<I', body, off, vals.pop())
        per = (self.ANIM_KEY_OFF, self.ANIM_ID_OFF, self.ANIM_CELLX_OFF,
               self.ANIM_CELLY_OFF, self.ANIM_CELLZ_OFF, self.ANIM_KREF_HI) + \
              tuple(o for o, _d in self.ANIM_KREF)
        for off in per:                        # 逐实例字段一律清 0，等调用方写
            struct.pack_into('<I', body, off, 0)
        return bytes(body)

    def building_anim_key(self, rec):
        """该楼已挂的动画对象键（没有 = 0）。按 `ANIM_SLOTS` 顺序取第一个有效键。"""
        pl = self.plain
        amap = {k for _g, k in self.anim_records(pl)}
        for off in self.ANIM_SLOTS:
            if rec + off + 4 > len(pl):
                break
            v = struct.unpack_from('<I', pl, rec + off)[0]
            if v and v in amap:
                return v
        return 0

    def add_building_anim(self, base, cell=None, z=None, donor=None, register=False):
        """给一栋楼补上**建筑动画对象**（476B）—— §194。

        · 已经有就什么都不做（返回 `created=False`）；
        · 键按 `K / K+0x70 / K+0x90 / K+0x71 / K+0x100 / K+0x120` 整组避让后分配；
        · 追加到动画对象数组末尾、count +1，并把新键登记进「含动画键」的对象册；
        · 最后挂到楼记录的 `+0x57C`（已被占则顺延 +0x580/+0x584/+0x590）。

        `donor` = 476 字节模板（一般给"同类型已完成楼"的那条记录）；
        不给就按 `anim_proto()` 的「类常量 + 现场写键/格」构造。
        `register` = 是否把新键登记进「含动画键的对象册」。**默认 False**：
        2026-10-23 实测（`T24_试做` 档，count 正确、未登记）进游戏**显示正常**
        ⇒ 引擎认的是楼 `+0x57C` 那个指针，不需要在对象册里挂号；而对象册是"插字节"，
        能不碰就不碰（少一处可能写坏的地方）。
        `z` 默认 **0**（全库 60+ 档、上千条原生 anim 的 +0xB8 全是 0，
        楼记录 `+168` 那个 2 不是 Z —— 旧文档这条是错的，2026-10-23 更正）。

        返回 {'key', 'created', 'slot', 'lists', 'total', 'note'}
        """
        pl = self.plain
        rec = base - 72
        if rec < 0 or rec + 1836 > len(pl):
            raise RuntimeError('楼记录偏移异常（base=0x%X）' % base)
        if base not in {b['base'] for b in self.buildings()}:
            raise RuntimeError('楼记录偏移已过期（本方法会插字节）—— '
                               '请重新取 `buildings()` 里的 base 再调用')
        have = self.building_anim_key(rec)
        if have:
            return {'key': have, 'created': False, 'slot': 0, 'lists': 0,
                    'total': len(self.anim_records(pl)),
                    'note': '该楼已有动画对象 0x%08X' % have}
        recs = self.anim_records(pl)
        if not recs:
            raise RuntimeError('本档没有可作模板的动画对象'
                               '（至少要有一栋已建成的建筑）—— 无法补')
        bkey = struct.unpack_from('<I', pl, rec + 16)[0]
        if cell is None:
            for b in self.buildings():
                if b.get('base') == base:
                    cell = (b['x'], b['y'])
                    break
        if cell is None:
            raise RuntimeError('定位不到这栋楼的坐标，请显式传 cell=(x,y)')
        cx, cy = int(cell[0]), int(cell[1])
        if z is None:
            z = 0
        slot = 0
        for off in self.ANIM_SLOTS:
            if struct.unpack_from('<I', pl, rec + off)[0] == 0:
                slot = off
                break
        if not slot:
            raise RuntimeError('这栋楼的 %d 个动画槽都被占了（%s）—— 请先清掉旧动画'
                               % (len(self.ANIM_SLOTS),
                                  '/'.join('+0x%X' % o for o in self.ANIM_SLOTS)))
        # ---- 分配键：整组（K 及 K+偏移）都要在明文里没出现过 ----
        used = {k for _g, k in recs}
        used |= {struct.unpack_from('<I', pl, b['base'] - 72 + 16)[0]
                 for b in self.buildings()}
        k = ((max(used) + 0x1000) & 0xFFFFFFFF) if used else 0x02A75020
        while True:
            grp = [k, k + 0x70, k + 0x71, k + 0x90, k + 0x100, k + 0x120,
                   (k & 0xFFFF0000) | 0x0101]
            if all(pl.find(struct.pack('<I', x & 0xFFFFFFFF)) < 0 for x in grp):
                break
            k = (k + 0x1000) & 0xFFFFFFFF
        # ---- 造记录 ----
        if donor is not None:
            body = bytearray(bytes(donor)[:self.ANIM_SIZE].ljust(self.ANIM_SIZE, b'\x00'))
        else:
            body = bytearray(self.anim_proto(pl))
        if len(body) != self.ANIM_SIZE:
            raise RuntimeError('模板记录长度不是 %d' % self.ANIM_SIZE)
        struct.pack_into('<I', body, self.ANIM_KEY_OFF, k)
        for off, delta in self.ANIM_KREF:
            struct.pack_into('<I', body, off, (k + delta) & 0xFFFFFFFF)
        struct.pack_into('<I', body, self.ANIM_KREF_HI, (k & 0xFFFF0000) | 0x0101)
        struct.pack_into('<I', body, self.ANIM_CELLX_OFF, (cx * 256) & 0xFFFFFFFF)
        struct.pack_into('<I', body, self.ANIM_CELLY_OFF, (cy * 256) & 0xFFFFFFFF)
        struct.pack_into('<I', body, self.ANIM_CELLZ_OFF, z & 0xFFFFFFFF)
        struct.pack_into('<I', body, self.ANIM_ID_OFF,
                         self._next_inst_id(pl, self._bld_group()[0]))
        # ---- ① 找「含动画键的对象册」（阵营建筑清单里没有动画键 ⇒ 自动排除）----
        akeys = {kk for _g, kk in recs}
        tabs = []
        for cs, c, ent in self._locate_key_lists(
                [struct.unpack_from('<I', pl, b['base'] - 72 + 16)[0]
                 for b in self.buildings()]):
            hit = sum(1 for j in range(c)
                      if struct.unpack_from('<I', pl, ent + 4 * j)[0] in akeys)
            if register and hit >= 1:                   # 含动画键 ⇒ 这张册子收动画
                tabs.append((cs, c, ent))
        # ---- ② 先做**零增长**的写入（此时还没有任何偏移变动）----
        struct.pack_into('<I', pl, rec + slot, k)                     # 楼 → anim
        for cs, c, _ent in tabs:
            struct.pack_into('<I', pl, cs, c + 1)                     # 册子计数 +1
        first_g = recs[0][0]
        cnt = struct.unpack_from('<I', pl, first_g - 4)[0]
        struct.pack_into('<I', pl, first_g - 4, cnt + 1)              # 数组 count +1
        # ---- ③ 所有**插字节**一次性收集、从后往前落 ----
        #   ★不能边插边算（2026-10-23 实踩：登记插了 8 字节后用旧偏移插记录体
        #     ⇒ count 写成 1、最后一条记录被切成两半）
        ins = [(recs[-1][0] + self.ANIM_SIZE, bytes(body))]
        for cs, c, ent in tabs:
            ins.append((ent + 4 * c, struct.pack('<I', k)))
        for pos, data in sorted(ins, key=lambda t: -t[0]):
            pl[pos:pos] = data
        self.invalidate_caches(rescan=True)
        return {'key': k, 'created': True, 'slot': slot, 'lists': len(tabs),
                'total': len(self.anim_records()),
                'note': '已补动画对象 0x%08X（挂到楼 +0x%X，登记 %d 张对象册，格 %d/%d）'
                        % (k, slot, len(tabs), cx, cy)}

    # ------------------------------------------------------------------ #
    # 格子占用表（2026-10-16 定案）
    # ------------------------------------------------------------------ #
    @classmethod
    def _foundation_table(cls):
        """artmd.ini 的 `Foundation=` 表 → {SECTION: (W, H)}（读不到返回 {}）"""
        if cls._foundation_cache is not None:
            return cls._foundation_cache
        out = {}
        _rp, _ap = cls._resolve_rule_files()
        for p in ([_ap] if _ap else list(cls._LEGACY_ART_PATHS)):
            if not os.path.exists(p):
                continue
            try:
                raw = open(p, 'rb').read()
                txt = None
                for enc in ('utf-16', 'utf-8', 'gbk'):
                    try:
                        s = raw.decode(enc)
                        if '[' in s:
                            txt = s
                            break
                    except Exception:
                        continue
                if not txt:
                    continue
                sec = None
                for line in txt.splitlines():
                    line = line.split(';')[0].strip()
                    if line.startswith('[') and line.endswith(']'):
                        sec = line[1:-1].strip().upper()
                        continue
                    if sec and '=' in line:
                        k, v = line.split('=', 1)
                        if k.strip().upper() == 'FOUNDATION':
                            fv = v.strip().lower()
                            if 'x' in fv:
                                try:
                                    w, h = fv.split('x')
                                    out[sec] = (int(w), int(h))
                                except Exception:
                                    pass
                if out:
                    break
            except Exception:
                continue
        cls._foundation_cache = out
        return out

    @classmethod
    def power_of(cls, type_name):
        """rules 里该建筑类型的 `Power=`（**正 = 发电 / 负 = 耗电**；查不到 = 0）。

        例：GAPOWR +200、GATECH −100、ATESLA −75（本机 rulesmd 实测）。
        """
        if not type_name:
            return 0
        cls._rules_info()
        return cls._rules_power.get(str(type_name).upper(), 0)

    @classmethod
    def foundation_of(cls, type_name):
        """建筑占地 (W,H)：先按 rules 的 `Image=` 映射，其次同名 art 节；缺省 (1,1)。"""
        t = (type_name or '').upper()
        tb = cls._foundation_table()
        img = (getattr(cls, '_rules_image', None) or {}).get(t, t)
        return tb.get(img.upper()) or tb.get(t) or (1, 1)

    def cell_offset(self, base, cx, cy):
        """格条目偏移：base + 348 × (80·cx + 79·cy)"""
        return base + self.CELL_STRIDE * (self.CELL_XK * cx + self.CELL_YK * cy)

    # ---------- 格对象数组（2026-10-18 新增：与图尺寸无关） ----------
    def cell_struct_array(self):
        """定位「格对象」定长槽数组 → (起点, 槽数)；找不到返回 (None, 0)。

        判据：槽 +0 是 16B 类 GUID `ce99bfc1-8c1a-d211-8175-006008055bb5`，
        连续等距排布（实测 80×80 小图 12720 槽、203×295 大图 61030 槽）。
        """
        pl = self.plain
        first = pl.find(self.CELL_STRUCT_GUID)
        if first < 0:
            return None, 0
        last = pl.rfind(self.CELL_STRUCT_GUID)
        n = (last - first) // self.CELL_STRUCT_STRIDE + 1
        if n < self._CELL_STRUCT_MIN:
            return None, 0
        step = max(1, n // 64)
        for i in range(0, n, step):
            s = first + i * self.CELL_STRUCT_STRIDE
            if bytes(pl[s:s + 16]) != self.CELL_STRUCT_GUID:
                return None, 0
        return first, n

    def cell_struct_slots(self, key):
        """该对象键占用的全部格槽起点（0 个 = 不占地）。"""
        first, n = self.cell_struct_array()
        if first is None or not key:
            return []
        pl = self.plain
        out = []
        for i in range(n):
            s = first + i * self.CELL_STRUCT_STRIDE
            if struct.unpack_from('<I', pl, s + self.CELL_STRUCT_OCC)[0] == key:
                out.append(s)
        return out

    def clear_cell_slots(self, key):
        """把该键占用的格清成"空"（4 个字段全 0）→ 返回清掉的格数。

        为什么是这 4 个字段：逐字段对比引擎"未卖→卖掉"的同一批格，
        变化的正是 +0xF0(=1→0，绘制标志) / +0xF8(占用键) / +0x114 / +0x138。
        漏掉 +0xF0 的话格子仍被当成"有建筑"，画面会留在原地。
        """
        pl = self.plain
        slots = self.cell_struct_slots(key)
        for s in slots:
            for off in self.CELL_STRUCT_CLEAR:
                struct.pack_into('<I', pl, s + off, 0)
        return len(slots)

    def occupy_cells_exact(self, type_name, cx, cy, key, ref_key):
        """按**真实字段**把 (cx,cy) 起的地基写成 key 占用（零增长）。

        ★2026-10-21（修 #2，放置法卡死的头号嫌疑）：
          旧的 `occupy_cells()` 把键写进 **格+0**，而格+0 是全档常量 `0xC1BF99CE`（类指针）
          ⇒ 写下去就是把类指针改坏；更糟的是它常因"定位不到格表"**静默失败**，
          于是存档变成"建筑记录说占这格、格子上却什么都没有" —— 卡死档就是这么来的
          （实测：cell(98,130) 的 +F0/+F8/+114/+138 全是 0）。
          正确做法与删除法对称：写 `CELL_STRUCT_CLEAR = (+0xF0, +0xF8, +0x114, +0x138)`，
          非 0 值照抄本档一栋同型号建筑那一格的实测值。
        """
        pl = self.plain
        co = self.cell_offsets()
        if not co:
            return False, '定位不到格子表'
        ref_slots = self.cell_struct_slots(ref_key)
        if not ref_slots:
            return False, '参照建筑没有格条目'
        # ★各字段取**同型号已占格的众数**，别只抄一栋 —— 那栋可能在建造中（实测 +F0 有 1/2/512/1024 多种）
        import collections as _c
        tmpl = {}
        try:
            hs, _cnt = self._bld_group()
            tk = self.building_types().get(str(type_name).upper())
            banks = {o: _c.Counter() for o in self.CELL_STRUCT_CLEAR}
            for g in hs:
                if struct.unpack_from('<I', pl, g + 16 + 1316)[0] != tk:
                    continue
                k2 = struct.unpack_from('<I', pl, g + 16)[0]
                for s2 in self.cell_struct_slots(k2)[:1]:
                    if struct.unpack_from('<I', pl, s2 + 0xF8)[0] != k2:
                        continue
                    for o in self.CELL_STRUCT_CLEAR:
                        banks[o][struct.unpack_from('<I', pl, s2 + o)[0]] += 1
            for o in self.CELL_STRUCT_CLEAR:
                if banks[o]:
                    tmpl[o] = banks[o].most_common(1)[0][0]
            # ★+0xF0 用 **1**：删除法那条硬证据是"卖掉时 +0xF0 1→0（绘制标志）"，
            #   即 1 = 有建筑。众数会捞到 512/1024 这类"多格建筑的子格掩码"，对 1x1 反而错。
            tmpl[0xF0] = 1
        except Exception:
            tmpl = {}
        if not tmpl.get(0xF8):
            s0 = ref_slots[0]
            tmpl = {off: struct.unpack_from('<I', pl, s0 + off)[0]
                    for off in self.CELL_STRUCT_CLEAR}
        if not tmpl.get(0xF8):
            return False, '参照格本身没写占用键'
        W, H = self.foundation_of(type_name)
        n, miss = 0, 0
        for i in range(W):
            for j in range(H):
                off = co.get((cx + i, cy + j))
                if not off:
                    miss += 1
                    continue
                if struct.unpack_from('<I', pl, off)[0] != 0xC1BF99CE:
                    # 格+0 不是本档常量 ⇒ 这不是一个"正常格子"，别乱写
                    miss += 1
                    continue
                for fo in self.CELL_STRUCT_CLEAR:
                    v = key if fo == 0xF8 else tmpl[fo]
                    struct.pack_into('<I', pl, off + fo, v)
                n += 1
        if not n:
            return False, '地基 %d 格一格都没写上（格表对不上）' % (W * H)
        note = '占格 %dx%d = %d 格（按 +F0/+F8/+114/+138 写，参照 %08X）' % (
            W, H, n, ref_key)
        if miss:
            note += '；⚠ %d 格没写（越界/非正常格）' % miss
        return True, note

    def _cell_base(self, pl=None):
        """定位格子占用表的基准（每档不同）。用本档已有建筑反解 + 互相印证。

        ★2026-10-18：本函数依赖 `CELL_XK/YK = 80/79`，**只对 80×80 小图成立**；
          203×295 的大任务图上返回 None。删除建筑已改走 `cell_struct_slots()`
          （与图尺寸无关），本函数仅作老路径兜底保留。
        ★2026-10-17 修（玩家档 `韩国无任何建筑.SAV` 暴露）：**未落位**的建筑（坐标 = (-1,-1)，
          本档 35 栋 Neutral/Special 就是）**没有格条目** ⇒ 它们既不能投票也不能计入分母，
          否则"全中才算成功"的判据永远不成立 ⇒ 整张表定位失败（`occupy_cells` 直接放弃写格）。
          ⇒ 现在只统计 `cx,cy ≥ 0` 的建筑。
        """
        pl = self.plain if pl is None else pl
        memo = getattr(self, '_cellbase_memo', None)
        if memo and memo[0] == len(pl):
            return memo[1]
        # ★★2026-10-17 22:3x 快路径（39MB 档 21.8s → 0.1s）：
        #   格对象本身就是那条 348B 步长表里的记录（自键 +16、坐标 +56）⇒
        #   拿任意一个格对象就能反解 base = 记录偏移 − 348×(80x+79y)，再多取几个交叉验证。
        #   旧写法对**每栋建筑**都要全档 find 一次（2054 栋 × 39MB ≈ 21 秒）。
        if pl is self.plain:
            try:
                self._cell_by_key()
                coff = getattr(self, '_cell_offs', None) or {}
                if len(coff) >= 8:
                    items = list(coff.items())[:6]
                    j0, (x0, y0) = items[0]
                    b0 = j0 - self.CELL_STRIDE * (self.CELL_XK * x0 + self.CELL_YK * y0)
                    ok = sum(1 for j, (x, y) in items[1:]
                             if self.cell_offset(b0, x, y) == j)
                    if ok >= max(1, len(items) - 2):
                        self._cellbase_memo = (len(pl), b0)
                        return b0
            except Exception:
                pass
        hs, _ = self._bld_group()
        votes = {}
        placed = []                     # 只留"已落位"的建筑
        for g in hs:
            o = g + 16
            k = struct.unpack_from('<I', pl, o)[0]
            if not k:
                continue
            cx = (struct.unpack_from('<I', pl, o + 600)[0] - 128) // 256
            cy = (struct.unpack_from('<I', pl, o + 604)[0] - 128) // 256
            if cx < 0 or cy < 0:
                continue                # 未落位（-1,-1）⇒ 无格条目
            placed.append((k, cx, cy))
            idx = self.CELL_XK * cx + self.CELL_YK * cy
            pat = struct.pack('<I', k)
            q = 0
            while True:
                i = pl.find(pat, q)
                if i < 0:
                    break
                q = i + 1
                # ★base 是**负锚**（本档可正可负），只做线性锚点，不与 0 比较
                b = i - self.CELL_STRIDE * idx
                votes[b] = votes.get(b, 0) + 1
        best, bestscore, ntot = None, 0, 0
        ntot = len(placed)
        for b, _c in sorted(votes.items(), key=lambda kv: -kv[1]):
            sc = 0
            for (k, cx, cy) in placed:
                p = self.cell_offset(b, cx, cy)
                if 0 <= p <= len(pl) - 4 and struct.unpack_from('<I', pl, p)[0] == k:
                    sc += 1
            if sc > bestscore:
                best, bestscore = b, sc
            if ntot and sc == ntot:
                break
        # ★多数判定：允许"个别建筑没有格条目"（例如工具刚注入还没写格子的那栋）
        need = max(1, ntot - 1) if ntot > 1 else 1
        if bestscore < need:
            best = None
        self._cellbase_memo = (len(pl), best)
        return best

    def _cell_pos_map(self):
        """{(x, y): 格记录偏移} —— 直接来自格对象扫描（`_cell_by_key` 顺手建的偏移表）。

        ★2026-10-17 22:4x：**绕开 `_cell_base` 的线性推算**。
          格表系数（80x+79y）随地图尺寸/朝向而变（大任务档实测是 179x+180y），
          硬算 base 会失败，而失败后要"每栋建筑全档 find"来投票（2054 栋 = 21 秒）。
          既然每个格记录自带坐标(+56)和自键(+16)，直接建 (x,y)→偏移 的字典即可，
          写占用时用字典查，根本不需要 base。
        """
        self._cell_by_key()
        coff = getattr(self, '_cell_offs', None) or {}
        out = {}
        for j, xy in coff.items():
            out.setdefault(xy, j)
        return out

    def occupy_cells(self, pl, type_name, cx, cy, key):
        """把 (cx,cy) 起 WxH 的格子占用写成 key —— **零增长**（只改原地字段）。
        返回 (ok, note)。检测到格子上已有别的占用者会如实报出（不静默覆盖）。"""
        pos_map = self._cell_pos_map() if pl is self.plain else None
        base = None
        if pos_map is None:
            base = self._cell_base(pl)
            if base is None:
                return False, '未能定位格子占用表（本档无可用参照建筑）'
        W, H = self.foundation_of(type_name)
        out, conflict = 0, []
        for i in range(W):
            for j in range(H):
                if pos_map is not None:
                    p = pos_map.get((cx + i, cy + j))
                    if p is None:
                        continue
                else:
                    p = self.cell_offset(base, cx + i, cy + j)
                if p < 8 or p + 4 > len(pl):
                    continue
                cur = struct.unpack_from('<I', pl, p)[0]
                if cur not in (0, key):
                    conflict.append((cx + i, cy + j, cur))
                struct.pack_into('<I', pl, p, key)
                # ★引擎放置时的实测：**同格前后档**（能建 (77,55) 空 → 放下光棱塔 被原生 ATESLA 占用）
                #   `cell−8` 与 `cell+0x1C` 都从 0 → 1。用 `|= 1` 写（原值已 1 的格子不变，
                #   可避免误伤同一 dword 上的其它位）。
                for fo in (self.CELL_FLAG_OFF, self.CELL_FLAG2_OFF):
                    q = p + fo
                    if 0 <= q <= len(pl) - 4:
                        struct.pack_into('<I', pl, q,
                                         struct.unpack_from('<I', pl, q)[0] | 1)
                out += 1
        if not out:
            return False, '%s 的占格落点越界' % type_name
        note = '占格 %dx%d = %d 格' % (W, H, out)
        if conflict:
            note += '；⚠ %d 格上已有其它占用者（已覆盖）：%s' % (
                len(conflict), [(x, y, hex(v)) for x, y, v in conflict[:4]])
        self._cellbase_memo = None
        return True, note

    def house_keys(self):
        """{阵营短名: House 对象键}（**防重入**包装，见 find_house_econ 的说明）。"""
        self._invalidate_if_shifted()
        memo = getattr(self, '_house_keys_memo', None)
        if memo is not None and memo[0] == len(self.plain):
            return memo[1]
        if getattr(self, '_house_keys_busy', False):
            return {}
        self._house_keys_busy = True
        try:
            return self._house_keys_impl()
        finally:
            self._house_keys_busy = False

    def _house_keys_impl(self):
        self._invalidate_if_shifted()
        """{阵营短名: House 对象键}（标准 12 阵营；自定义所属方任务返回自定义名）。

        ★★★ 2026-10-17 定案，两档实测（母本 12/12、大任务档 12/12）：
          · 明文里有 12 个 `HOUSE_GUID`(D9D4A910…) **对象**，键在 `对象+16`；
          · 每个 House 对象**内部**带 ASCII 名串 `"<短名> House"`（如 `Americans House`），
            且该串一定落在「本对象起点 ≤ 串 < 下一个对象起点」区间内
            （母本 0x4ff935 ∈ [0x4e992d,0x50071d) → Americans ✓ …12/12）；
          · 名串缺失时退回「文件序 = HOUSE_ORDER」。
        ⚠ `_player_house_key()` 的"引用最多"启发式**只对 12 阵营对局可靠**：
          大任务档里 Russians 引用 1057 次而玩家 Americans 只有 67 次 —— **别用它认玩家**，
          认阵营一律走本方法（名串）。
        """
        memo = getattr(self, '_house_keys_memo', None)
        if memo is not None and memo[0] == len(self.plain):
            return memo[1]
        pl = bytes(self.plain)
        objs, q = [], 0
        while True:
            i = pl.find(self.HOUSE_GUID, q)
            if i < 0:
                break
            objs.append((i, struct.unpack_from('<I', pl, i + 16)[0]))
            q = i + 1
        out = {}
        if not objs:
            self._house_keys_memo = (len(self.plain), out)
            return out
        # 自定义所属方任务：名字取自 find_custom_houses()
        names = list(HOUSE_ORDER)
        try:
            cust = self.find_custom_houses()
            # ★2026-10-18：如果 econ 正在算（说明是它调我们）⇒ **不要反过来再问它**，
            #   否则就是互相递归（原来正是这样递归了 498 层）。
            #   此时保持标准 12 阵营名，让 econ 拿到完整的 House 键表去跑它的兜底判据；
            #   econ 算完后会用同一份名字（它的结果 ≥6 时本函数也走标准名），结论一致。
            econ_busy = getattr(self, '_house_econ_busy', False)
            if cust and not econ_busy and \
                    sum(1 for v in self.find_house_econ().values() if v) < 6:
                names = [self.norm_house(x['name']) for x in cust]
        except Exception:
            pass
        used = set()
        for nm in names:
            for suffix in ('%s House' % nm, nm):
                pat = suffix.encode('latin1', 'ignore')
                if not pat:
                    continue
                hit = -1
                q = 0
                while True:
                    i = pl.find(pat, q)
                    if i < 0:
                        break
                    q = i + 1
                    # 落在某个 House 对象之后、下一个 House 对象之前 ⇒ 归它
                    cand = None
                    for k, (off, key) in enumerate(objs):
                        if off <= i:
                            cand = (k, key)
                        else:
                            break
                    if cand and cand[1] not in used:
                        hit = cand[1]
                        break
                if hit >= 0:
                    out[nm] = hit
                    used.add(hit)
                    break
        # 名串没覆盖到的：按文件序补
        rest_obj = [k for _o, k in objs if k not in used]
        for nm in names:
            if nm not in out and rest_obj:
                out[nm] = rest_obj.pop(0)
        self._house_keys_memo = (len(self.plain), out)
        return out

    def resolve_house(self, house):
        """把「阵营短名 / 中文名 / House 对象键(int) / None」统一成 House 对象键。

        None ⇒ 返回 None（= 调用方保持原值不改）。认不出 ⇒ 抛异常（不静默）。"""
        if house is None:
            return None
        if isinstance(house, int):
            return house
        s = str(house).strip()
        if not s:
            return None
        if re.fullmatch(r'0[xX][0-9a-fA-F]{1,8}|\d{6,}', s):
            return int(s, 0)
        hk = self.house_keys()
        if s in hk:
            return hk[s]
        s2 = self.norm_house(s)                  # 容忍 "Americans House" 这种带后缀写法
        if s2 in hk:
            return hk[s2]
        for short, key in hk.items():                    # 中文名 / 别名
            if HOUSE_CN.get(short) == s or HOUSE_CN.get(short) == s2:
                return key
        low = s2.lower()
        for short, key in hk.items():
            if short.lower() == low or HOUSE_CN.get(short, '').lower() == low:
                return key
        raise RuntimeError('认不出所属方「%s」；可用：%s'
                           % (house, '、'.join('%s(%s)' % (k, HOUSE_CN.get(k, '')) for k in hk)))

    def house_label(self, key):
        """House 键 → 可读标签 '中文名(短名)'；查不到返回 '?xxxx'。"""
        if not key:
            return ''
        for short, k in self.house_keys().items():
            if k == key:
                cn = HOUSE_CN.get(short, '')
                return ('%s(%s)' % (cn, short)) if cn else short
        return '?%08x' % key

    def house_run_index(self, house):
        """阵营 → **运行期序号**（= 建筑载荷 `+1344` 的那个小整数）。

        优先**从本档已有建筑反解**（同一主人的建筑里取多数票）—— 这是唯一权威来源；
        本档没有该阵营的建筑时退回 `HOUSE_RUN_INDEX` 兜底表（10 国 + Neutral 有实测支撑）。
        认不出返回 None。
        """
        key = self.resolve_house(house)
        if key is None:
            return None
        votes = {}
        try:
            hs, _c = self._bld_group()
        except Exception:
            hs = []
        pl = bytes(self.plain)
        for g in hs:
            o = g + 16
            if struct.unpack_from('<I', pl, o + self.BLD_OWNER_OFF)[0] != key:
                continue
            v = struct.unpack_from('<I', pl, o + self.BLD_OWNER_IDX_OFF)[0]
            votes[v] = votes.get(v, 0) + 1
        if votes:
            return max(votes.items(), key=lambda kv: kv[1])[0]
        for short, k in self.house_keys().items():
            if k == key:
                return HOUSE_RUN_INDEX.get(self.norm_house(short))
        return None

    def buildings_of_house(self, house=None):
        """本档某阵营的**全部建筑** → [{idx, base, key, type, x, y}]（house=None ⇒ 全部）。"""
        want = self.resolve_house(house) if house is not None else None
        hs, _c = self._bld_group()
        out = []
        for i, g in enumerate(hs):
            o = g + 16
            k = struct.unpack_from('<I', self.plain, o)[0]
            ok = struct.unpack_from('<I', self.plain, o + self.BLD_OWNER_OFF)[0]
            if want is not None and ok != want:
                continue
            tkey = struct.unpack_from('<I', self.plain, o + 1316)[0]
            nm = '?%08x' % tkey
            for n, kk in sorted(self.building_types().items()):
                if kk == tkey:
                    nm = n
                    break
            px, py = struct.unpack_from('<II', self.plain, o + 600)
            out.append({'idx': i, 'base': g, 'key': k, 'type': nm,
                        'owner': ok, 'x': (px - 128) // 256, 'y': (py - 128) // 256,
                        'owner_idx': struct.unpack_from('<I', self.plain,
                                                        o + self.BLD_OWNER_IDX_OFF)[0]})
        return out

    def set_building_owner(self, house, idx=None, cx=None, cy=None, key=None):
        """★2026-10-17 新增：改一栋**已有建筑**的所属方（写记录载荷 `+336` 与 `+544`）。

        定位优先级：`key` > `(cx,cy)` > `idx`（0 基，顺序同 `_bld_group()`）。
        只动这两处（**伴随对象不含 House 键**，不用动）；
        **不动该阵营自己的建筑清单/占地统计**（这类结构尚未定位，若游戏内出现
        "改完颜色对但不认领"，就得补写 —— 见 `docs/加建筑_所属方_20261017.md`）。
        """
        want = self.resolve_house(house)
        if want is None:
            raise RuntimeError('必须指定目标所属方')
        if want not in self.house_keys().values():
            raise RuntimeError('目标 House 键 %08x 不在本档 House 对象列表里' % want)
        pl = self.plain
        hs, _c = self._bld_group()
        if not hs:
            raise RuntimeError('这份存档里一栋建筑都没有（全新开局），没有建筑组可用 —— '
                               '请先在游戏里放一栋建筑并保存，再用本工具操作')
        g = None
        if key is not None:
            for gg in hs:
                if struct.unpack_from('<I', pl, gg + 16)[0] == key:
                    g = gg
                    break
        if g is None and cx is not None and cy is not None:
            hit = [b for b in self.buildings_of_house(None)
                   if (b['x'], b['y']) == (int(cx), int(cy))]
            if len(hit) == 1:
                g = hit[0]['base']
            elif len(hit) > 1:
                g = hit[0]['base']
        if g is None and idx is not None and 0 <= int(idx) < len(hs):
            g = hs[int(idx)]
        if g is None:
            raise RuntimeError('定位不到目标建筑（key/cx,cy/idx 都没命中）')
        o = g + 16
        old = struct.unpack_from('<I', pl, o + self.BLD_OWNER_OFF)[0]
        old_idx = struct.unpack_from('<I', pl, o + self.BLD_OWNER_IDX_OFF)[0]
        rix = self.house_run_index(want)
        if rix is None:
            raise RuntimeError('认不出阵营 %s 的运行期序号（载荷 +1344）'
                               % self.house_label(want))
        for off in (self.BLD_OWNER_OFF, self.BLD_OWNER2_OFF):
            struct.pack_into('<I', pl, o + off, want)
        struct.pack_into('<I', pl, o + self.BLD_OWNER_IDX_OFF, rix)
        px, py = struct.unpack_from('<II', pl, o + 600)
        tkey = struct.unpack_from('<I', pl, o + 1316)[0]
        nm = '?%08x' % tkey
        for n, kk in sorted(self.building_types().items()):
            if kk == tkey:
                nm = n
                break
        # ★电力计数器随所属方搬家（2026-10-17）
        pwr_note = ''
        pw = self.power_of(nm)
        if pw and old != want:
            dout, ddrain = max(pw, 0), max(-pw, 0)
            r_old = self.adjust_house_power(old, -dout, -ddrain)
            r_new = self.adjust_house_power(want, dout, ddrain)
            if r_old or r_new:
                pwr_note = '；电力 %s %s→%s / %s %s→%s' % (
                    self.house_label(old),
                    r_old[0] if r_old else '?', r_old[1] if r_old else '?',
                    self.house_label(want),
                    r_new[0] if r_new else '?', r_new[1] if r_new else '?')
        self.pending_bld = True
        self._blds = None
        return {'mode': '改所属方', 'type': nm, 'key': struct.unpack_from('<I', pl, o)[0],
                'cell': ((px - 128) // 256, (py - 128) // 256),
                'old_house': old, 'house': want,
                'old_house_idx': old_idx, 'house_idx': rix,
                'old_house_name': self.house_label(old), 'house_name': self.house_label(want),
                'note': '%s @(%d,%d)：所属方 %s → %s（+336/+544 键、+1344 序号 %d→%d）%s'
                        % (nm, (px - 128) // 256, (py - 128) // 256,
                           self.house_label(old), self.house_label(want), old_idx, rix,
                           pwr_note)}

    # ★★★★★ 2026-10-17 定案：**House 对象 + 0x53B8 = (发电, 耗电) 两个 u32**
    #   证据（三档逐阵营对账）：把本档该阵营的建筑按 rules `Power=` 求和，
    #   与存档里这两个数**完全吻合** —— 母本 玩家 (1200, 210) = 6×GAPOWR200 /
    #   GATECH100+GAREFN50+GAPILE10+GAAIRC50；**无电厂档 玩家 (0, 210)**（发电归零、耗电不变）；
    #   大任务档 9 个有建筑的阵营里 6 个精确吻合（另 3 个差几百 = 我们按 rules 会把"在建/
    #   未激活"的建筑也算进去，属正常偏差）。
    #   ⇒ **"低电力"= 发电机侧的两个数**：`耗电 > 发电` 即低电力（电力建筑停火 / 生产减速）。
    #   ★这解释了 H1 的怪现象：苏俄/尤里的新塔 +336 写对了、但**该阵营的计数器仍是 (0,0)**
    #     ⇒ 0 ≥ 0 「不缺电」⇒ 塔照常开火。**加建筑/改所属方/删建筑都必须同步这台计数器。**
    HOUSE_POWER_REL = 0x53B8

    def _house_obj_off(self, key):
        """House 键 → 该 House 对象在明文里的偏移（无则 None）。"""
        pl = bytes(self.plain)
        q = 0
        while True:
            i = pl.find(self.HOUSE_GUID, q)
            if i < 0:
                return None
            if struct.unpack_from('<I', pl, i + 16)[0] == key:
                return i
            q = i + 1

    HOUSE_LIST_BASE = 48       # 块尾 − (48 + 12×建筑数) = 「本阵营建筑清单」的 count 位置
    HOUSE_TRIGOWNER_OFF = 72   # House 块 +72 = 该阵营的「触发所属方对象」键（2026-10-17 定案）

    def _attach_ov_key(self):
        """`attach_overview()` 的缓存键：明文长度 + 触发名指纹（名字改了也要重算）。"""
        h = 0
        try:
            for r in self.records:
                if r.kind == '触发':
                    h = (h * 131 + hash(r.name)) & 0xFFFFFFFF
        except Exception:
            h = -1
        return (len(self.plain), h)

    def _house_blocks(self):
        """[(对象偏移, House 键, 块尾偏移)]，块尾 = 下一个 House 对象起点（最后一个 = 文件尾）。"""
        # ★§256 缓存：本函数每次全档 find GUID（28MB × 若干个阵营）；`trigger_owner_name`
        #   每次调它 18 次 ⇒ `trigger_detail` 里又多花 0.24 秒。
        _c = getattr(self, '_hblocks', None)
        if _c is not None:
            return _c
        pl = bytes(self.plain)
        objs, q = [], 0
        while True:
            i = pl.find(self.HOUSE_GUID, q)
            if i < 0:
                break
            objs.append((i, struct.unpack_from('<I', pl, i + 16)[0])); q = i + 1
        out = []
        for j, (i, k) in enumerate(objs):
            nxt = objs[j + 1][0] if j + 1 < len(objs) else len(pl)
            out.append((i, k, nxt))
        self._hblocks = out
        return out

    def _house_bld_keys(self, key):
        """该阵营在本档的建筑键列表（按建筑组顺序）。"""
        out = []
        try:
            hs, _c = self._bld_group()
        except Exception:
            return out
        pl = bytes(self.plain)
        for g in hs:
            o = g + 16
            if struct.unpack_from('<I', pl, o + self.BLD_OWNER_OFF)[0] == key:
                out.append(struct.unpack_from('<I', pl, o)[0])
        return out

    # ---------------- 触发的「所属方（国家）」 ----------------
    def house_trigger_owner_key(self, house):
        """该阵营的「触发所属方对象」键 = **House 块 +72**（12 个阵营各一个，独一无二）。

        ★★★★★ 2026-10-17 定案（玩家给的成对档 `刷光棱塔韩国.SAV` / `刷光棱塔德国.SAV`：
          两份明文**逐字节长度一致、触发记录区段只差 3 字节**）：
          两条触发里 **`01000002` 的记录 `+128`**：
            韩国档 = `0x1023ac30`（= Alliance 块 +72），德国档 = `0x1023a8b0`（= Germans 块 +72）
          ⇒ **触发记录的 `+128` 就是"国家"**，值是目标阵营那个"触发所属方对象"的键；
          ⇒ 改国家 = 把 `+128` 换成目标阵营 House 块 +72 的值，**不需要动别的任何东西**。
          该对象本体（类 GUID `1dd43928-11d2046b-…`）里带阵营名 ASCII（如 `Alliance`），
          它同时被阵营块 +72 引用 ⇒ 不需要新建，直接借用即可。
        """
        key = self.resolve_house(house)
        if key is None:
            return None
        pl = bytes(self.plain)
        for (st, k, en) in self._house_blocks():
            if k == key:
                return struct.unpack_from('<I', pl, st + self.HOUSE_TRIGOWNER_OFF)[0]
        return None

    TRIG_OWNER_OFF = 128       # 触发记录 +128 = 国家（= House 块 +72 的键）

    def trigger_rec(self, sid):
        """按 sid 找触发**定义记录**；同 sid 有多条时优先选**有实例对应**的那条。

        ★§224：SAVE0029 有 22 个 sid 带**两条记录**（一条有实例、一条没有的副本，
          副本的 owner 键谁都不认，如 01000002 的 0x103b6a60）。
          旧代码取第一条 ⇒ 22 条触发的 owner/标志全读错（玩家实测：触发编辑器
          把国家显示成 Neutral 而地图里是别的）。
          判据：**记录帧 = 实例帧 + 56**（`TRIG_REC_DELTA`），实例侧有 sid 可对。
        """
        cands = [r for r in self.records if r.kind == '触发' and r.sid == sid]
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        inst = self.trigger_inst_off(sid)
        if inst is not None:
            for r in cands:
                if r.off == inst + 56:      # TRIG_REC_DELTA
                    return r
        # 没有实例（或都对不上）：退第一张有"像样 owner 键"的（能翻译出名字的）
        for r in cands:
            k = self.trigger_owner_key(r)
            if any(self.house_trigger_owner_key(h) == k for h in self.house_keys()):
                return r
        return cands[0]

    def trigger_owner_key(self, rec):
        """读某个触发的国家键（触发记录 +128）。"""
        return struct.unpack_from('<I', self.plain, rec.off + self.TRIG_OWNER_OFF)[0]

    def trigger_owner_name(self, rec):
        """读某个触发的国家名（短名）；认不出返回 '?xxxxxxxx'。"""
        k = self.trigger_owner_key(rec)
        for house in self.house_keys():
            if self.house_trigger_owner_key(house) == k:
                return house
        return '?%08x' % k

    def set_trigger_owner(self, rec, house):
        """把触发的「国家」改成目标阵营（写触发记录 `+128`）。

        返回 (旧键, 新键)；认不出阵营抛异常。★只改这 4 个字节，别的字段一律不动。
        """
        new = self.house_trigger_owner_key(house)
        if new is None:
            raise RuntimeError('认不出阵营 %s 的"触发所属方对象"键（House 块 +72）' % (house,))
        old = self.trigger_owner_key(rec)
        struct.pack_into('<I', self.plain, rec.off + self.TRIG_OWNER_OFF, new)
        return old, new

    def house_build_list(self, house):
        """定位该阵营 House 块里的**本阵营建筑清单** `[count][建筑键 × count]`
        → `(count偏移, count, 条目偏移)`；定位不到 → None。

        ★布局规律（2026-10-17 定案）：清单 count 的位置 = **块尾 − (48 + 12×建筑数)**。
          证据：母本 玩家 n=11 → −180 ✓；SAVE0029 里 n=2..16 的 11 个阵营**全部命中** ✓。
        定位法：
          · n ≥ 1：在块内**按内容精确匹配**（`[n]` + 恰好是该阵营的 n 个键）；
          · n == 0：用上式（块尾 − 48），但**先用本档有建筑的其他阵营验证公式**（全部吻合才采用）。
        这张清单是**电力归属**与"谁是我的建筑"的依据 —— 新建筑要登记进**主人**这张表
        （见 `add_placed_building` 的 `reg`），否则引擎会把它的耗电算到别人头上。
        """
        key = self.resolve_house(house)
        if key is None:
            return None
        pl = bytes(self.plain)
        mine = set(self._house_bld_keys(key))
        blocks = self._house_blocks()
        me = [b for b in blocks if b[1] == key]
        if not me:
            return None
        st, _k, en = me[0]

        def content_hit(start, end, keys):
            n = len(keys)
            q = start
            while True:
                i = pl.find(struct.pack('<I', n), q)
                if i < 0 or i + 4 > end - 4 * n + 4:
                    return None
                q = i + 4
                got = [struct.unpack_from('<I', pl, i + 4 + 4 * j)[0] for j in range(n)]
                if set(got) == keys:
                    return i
        if mine:
            p = content_hit(st, en, mine)
            if p is not None:
                return (p, len(mine), p + 4, True)
            # ★2026-10-17 22:5x 兜底（大任务档实测：块内精确匹配找不到，但清单其实存在）：
            #   拿本阵营的建筑键去"按连续键区"找表，再看成员是不是**基本都属于本阵营**
            #   （≥90% 命中 mine）。SAVE0029 的 812 栋建筑分属 12 个阵营，
            #   每家的清单就是那 548/450/416/331/252… 条的表。
            try:
                cand = self._locate_key_lists(list(mine))
            except Exception:
                cand = []
            for cs, c, ent in cand:
                step = max(1, c // 200)
                vals = [struct.unpack_from('<I', pl, ent + 4 * t)[0]
                        for t in range(0, c, step)][:200]
                inside = sum(1 for v in vals if v in mine)
                if inside >= max(1, int(0.9 * len(vals))):
                    return (cs, c, ent, True)
            return None
        # n == 0：先验证公式
        votes = ok = 0
        for (s2, k2, e2) in blocks:
            if k2 == key:
                continue
            ks2 = set(self._house_bld_keys(k2))
            if not ks2:
                continue
            p2 = content_hit(s2, e2, ks2)
            if p2 is None:
                continue
            votes += 1
            if e2 - p2 == self.HOUSE_LIST_BASE + 12 * len(ks2):
                ok += 1
        if votes and ok == votes:
            p = en - self.HOUSE_LIST_BASE
            if st < p < en and p + 4 <= en:
                # ⚠ 只有 exact=True 才允许改它：n=0 的槽位是**公式外推**出来的，
                #   2026-10-17 实测"按公式插进去 ⇒ 载入弹框"（House 块中段结构的长度并非恒定）
                return (p, 0, p + 4, False)
        return None

    def house_power(self, house):
        """该阵营的（发电, 耗电, 偏移）；定位不到返回 (0, 0, None)。

        定位法：House 对象 + `HOUSE_POWER_REL`；若与"按本档建筑 + rules 算出来的值"不符，
        就在该 House 块内按算出来的那一对回找（更稳），再不行用锚点。
        """
        key = self.resolve_house(house)
        if key is None:
            return (0, 0, None)
        off = self._house_obj_off(key)
        if off is None:
            return (0, 0, None)
        pl = bytes(self.plain)
        p = off + self.HOUSE_POWER_REL
        if p + 8 > len(pl):
            return (0, 0, None)
        out, drain = struct.unpack_from('<II', pl, p)
        exp = self._house_power_calc(key)
        if exp is not None and (out, drain) != exp and exp != (0, 0):
            nxt = pl.find(self.HOUSE_GUID, off + 1)
            if nxt < 0:
                nxt = min(len(pl), off + 0x40000)
            pat = struct.pack('<II', exp[0], exp[1])
            hits, q = [], pl.find(pat, off)
            while q >= 0 and q + 8 <= nxt and len(hits) < 2:
                hits.append(q)
                q = pl.find(pat, q + 1)
            if len(hits) == 1:               # ★只在唯一命中时才改锚点（避免误伤）
                p, out, drain = hits[0], exp[0], exp[1]
        return (out, drain, p)

    def _house_power_calc(self, key):
        """按本档建筑（+336 = key）+ rules `Power=` 算出该阵营应有的 (发电, 耗电)。"""
        try:
            hs, _c = self._bld_group()
        except Exception:
            return None
        pl = bytes(self.plain)
        rev = {}
        for n, kk in self.building_types().items():
            rev.setdefault(kk, n)
        out = drain = 0
        for g in hs:
            o = g + 16
            if struct.unpack_from('<I', pl, o + self.BLD_OWNER_OFF)[0] != key:
                continue
            pw = self.power_of(rev.get(struct.unpack_from('<I', pl, o + 1316)[0]))
            if pw > 0:
                out += pw
            else:
                drain += -pw
        return (out, drain)

    def adjust_house_power(self, house, dout=0, ddrain=0):
        """把该阵营的（发电, 耗电）就地 ±（返回 ((旧), (新), 偏移) 或 None）。"""
        if not dout and not ddrain:
            return None
        cur = self.house_power(house)
        if cur[2] is None:
            return None
        out, drain, p = cur
        no, nd = max(out + dout, 0), max(drain + ddrain, 0)
        struct.pack_into('<II', self.plain, p, no, nd)
        self._blds = None
        return ((out, drain), (no, nd), p)

    def house_of_building(self, o):
        """读某栋建筑的所属方键（载荷帧偏移 `BLD_OWNER_OFF`）。o = 载荷起点（= g+16）。"""
        return struct.unpack_from('<I', self.plain, o + self.BLD_OWNER_OFF)[0]

    def _player_house_key(self):
        """玩家 House 对象键：D9D4A910 类 GUID 对象中**引用最多**者（玩家被全场引用）。
        12 阵营对局实测：玩家 51~63 次、AI 恒 26~27 次，判据稳。找不到返回 None。"""
        pl = bytes(self.plain)
        cands, q = [], 0
        while True:
            i = pl.find(self.HOUSE_GUID, q)
            if i < 0:
                break
            cands.append(struct.unpack_from('<I', pl, i + 16)[0])
            q = i + 1
        if not cands:
            return None
        return max(set(cands), key=lambda k: pl.count(struct.pack('<I', k)))

    def _locate_key_lists(self, keys):
        """定位 [u32 count][键×count] 登记表（U2 配方：成员含 ≥60% 建筑键且 ≥5 个）。
        种子 = 键的全部出现处（任意对齐）回溯找计数头；返回 [(count_off, count, entries_off)]。"""
        pl = bytes(self.plain)
        kset = set(keys)
        # ★大档（>200 栋）直接走"连续键区"判据：老口径要求"一张表里塞下 60% 建筑键"，
        #   2054 栋的档不可能满足，跑一遍纯属浪费（39MB 档要 2 秒）。小档保持原行为不变。
        if len(kset) > 200:
            return self._locate_key_lists_by_run(keys)
        # ★种子抽样：老写法对**每个建筑键**都全档 find，2054 栋的档要 39 秒。
        #   48 个种子足以覆盖所有候选表（表都是含大量建筑键的连续区），结果不变。
        pats = {struct.pack('<I', k) for k in list(kset)[:48]}
        seeds = set()
        for p in pats:
            q = 0
            while True:
                i = pl.find(p, q)
                if i < 0:
                    break
                seeds.add(i)
                q = i + 1
        cands = {}
        for p in sorted(seeds):
            for i in range(0, 300):
                cs = p - 4 * i - 4
                if cs < 0 or cs in cands:
                    break
                c = struct.unpack_from('<I', pl, cs)[0]
                if not (3 <= c <= 4096) or cs + 4 + 4 * c > len(pl) or i >= c:
                    continue
                ks = [struct.unpack_from('<I', pl, cs + 4 + 4 * j)[0] for j in range(c)]
                if not all(0x01000000 <= k <= 0x20000000 for k in ks):
                    continue
                same = sum(1 for k in ks if k in kset)
                if same >= max(4, int(0.6 * len(kset))):
                    cands[cs] = (c, same)
                    break
        out = []
        for cs, (c, same) in sorted(cands.items(), key=lambda kv: -kv[1][1]):
            if any(abs(cs - p) < 4 * cc for p, cc, _s in out):
                continue
            out.append((cs, c, cs + 4))
        if out:
            return out
        # ★2026-10-17 22:1x：老口径只认 `count ≤ 4096` 的表。
        #   大任务档的登记表有 8642 / 5189 条 ⇒ 一张都找不到（GUI 报"没找到建筑登记表"）。
        #   兜底：按"连续合法键区 + 段首计数 == 段长"认表（与键在表里的位置无关）。
        return self._locate_key_lists_by_run(keys)

    def _locate_key_lists_by_run(self, keys):
        """找 `[u32 n][键×n]` 登记表（**大表通吃**）。

        ★2026-10-17 22:2x 最终版：直接复用 `_key_tables(key)` 那套"连续键区 + 段首计数"
          判据（它已经被验证能认出 8642 / 5189 条的大表），这里只多做一步
          "成员里必须有足够多的建筑键"的过滤。
          之前的两种写法都不行：①只试表头 64 条 ⇒ 大表全漏；②从种子一路向左扩 ⇒
          扩过头，真正的表头在 64 条之外 ⇒ 也全漏。
        """
        pl = self.plain
        kset = set(keys)
        hits = {}                      # cs → 命中它的**不同建筑键**的个数
        meta = {}
        for k in list(kset)[:24]:
            for tb in self._key_tables(k):
                cs, c, ent = tb['count_pos'], tb['n'], tb['first']
                meta[cs] = (c, ent)
                hits[cs] = hits.get(cs, 0) + 1
        # ★过滤：一张表里要命中 ≥ min(8, 建筑总数) 个**不同**建筑键，才算"建筑登记表"
        #   （全局登记册/本阵营清单都轻松满足；别的东西偶然含一个键则被滤掉）
        bar = min(8, len(kset))
        out = [(cs, meta[cs][0], meta[cs][1]) for cs, n in hits.items() if n >= bar]
        out.sort(key=lambda t: -t[1])
        ded = []
        for cs, c, ent in out:
            if any(abs(cs - p) < 4 * max(cc, 4) for p, cc, _e in ded):
                continue
            ded.append((cs, c, ent))
        return ded

    def _strip_records(self, pl):
        """侧栏 strip 槽数组：连续 0x34 步长的 [A][07][Y][F=0] 记录（0x90600-0x90A00 一带，
        全明文扫描防跨图漂移）。返回 [(记录起点, A, Y)]。★A = rules 建筑序号-1。"""
        n = len(pl)
        recs = []
        i = 0
        run = []
        while i + 16 <= n:
            a, b, c, f = struct.unpack_from('<IIII', pl, i)
            if b == 7 and a < 0x400 and c < 0x100 and f == 0:
                run.append((i, a, c))
                i += 0x34
                continue
            if len(run) >= 4:
                recs.extend(run)
            run = []
            i += 4
        if len(run) >= 4:
            recs.extend(run)
        return [(i, a, c) for i, a, c in recs]

    @staticmethod
    def _slot_entry(pl, off):
        """off 处是否是侧栏槽**条目**（0x34 步长）。

        条目布局（2026-10-15 逐字节定案，开局档 → 电厂就绪 → 电厂已放下 三档一致）：
          +0  = A（rules [BuildingTypes] 0 基序号）      +4  = 7（占位标记）
          +8  = Y（= BuildCat 码：Tech 1 / Resource 2 / Power 3 / Combat 5）
          +12 = F（工厂键，空态 0）                      +32 = 0x0a（占位态；空槽为 0）
          +44 = 1（**条目存在标记**，空槽也有）
        ⇒ 占位 = (+4==7 且 +0<0x400 且 +8<0x100)；空槽 = 前 12B 全 0；两者 +44 都是 1。
        """
        if off < 0 or off + 0x34 > len(pl):
            return False
        if struct.unpack_from('<I', pl, off + 44)[0] != 1:
            return False
        d0, d1, d2 = struct.unpack_from('<III', pl, off)
        if d1 == 7 and d0 < 0x400 and d2 < 0x100:
            return True
        return d0 == 0 and d1 == 0 and d2 == 0

    def _strip_groups(self, pl):
        """侧栏槽数组，**含空槽**，按组返回。

        返回 [[(off, A, Y, F, occ), …], …]：每组是一条连续的 0x34 条目链
        （从占位槽锚点向两侧按 _slot_entry 扩展）。**组数 = 侧栏栏数**
        （第 1 组 = 建筑栏（非 Combat）、第 2 组 = 防御栏（Combat））。
        每组末尾跟着若干**空槽**（A=0/F=0，+44=1，occ=False）——新增槽应**原地覆写空槽**，
        不要插字节（插入会把数组之后的全部数据后移 ⇒ 2026-10-15 T12 弹框的疑因）。
        """
        seeds = [r[0] for r in self._strip_records(pl)]
        if not seeds:
            return []
        seeds.sort()
        runs, cur = [], [seeds[0]]
        for s in seeds[1:]:
            if s == cur[-1] + 0x34:
                cur.append(s)
            else:
                runs.append(cur)
                cur = [s]
        runs.append(cur)
        out = []
        for r in runs:
            lo, hi = r[0], r[-1]
            while self._slot_entry(pl, lo - 0x34):
                lo -= 0x34
            while self._slot_entry(pl, hi + 0x34):
                hi += 0x34
            ents = []
            off = lo
            while off <= hi:
                a = struct.unpack_from('<I', pl, off)[0]
                y = struct.unpack_from('<I', pl, off + 8)[0]
                f = struct.unpack_from('<I', pl, off + 12)[0]
                occ = struct.unpack_from('<I', pl, off + 4)[0] == 7
                ents.append((off, a, y, f, occ))
                off += 0x34
            out.append(ents)
        return out

    def gift_ready_building(self, type_name, slot2_off=None, allow_new_slot=False):
        """赠送一座「就绪未落地」建筑（侧栏直接"就绪"，玩家自己找位置放）。

        配方（2026-10-15 配对样本拆解 + 游戏内验收）：
          建筑记录（坐标三副本 0 / +208=0 / 不进三表）+ 生产 blob 148B（替换
          首个 2EBB6D66 GUID 前的 `00000000 0a000000` 8B）+ House→工厂反引槽。
        slot2_off：槽 2 偏移（基准明文坐标）。缺省 = 自动定位（玩家经济块 + 0x50B0，
        跨会话恒定）。★两槽必须同写或同不写（引擎加载校验两槽一致，不等⇒故意除零崩档）。
        """
        pl = self.plain
        base_len = len(pl)
        hs, cnt_off = self._bld_group()
        if not hs:
            raise RuntimeError('这份存档里一栋建筑都没有（全新开局），没有建筑组可用 —— '
                               '请先在游戏里放一栋建筑并保存，再用本工具操作')
        tkey = self.building_types().get(type_name)
        if not tkey:
            raise RuntimeError('档里没有类型 %s' % type_name)
        buildings, tl = self._rules_info()
        if tl.get(type_name.upper(), 1) < 1:
            raise RuntimeError('%s 不可建造（TechLevel=%s）——「就绪未落地」只能送可生产建筑；'
                               '建造场等请用「直接放好」' % (type_name, tl.get(type_name.upper())))
        tpl, cross = self._pick_bld_template(hs, pl, tkey)
        if cross:
            # 跨类型模板：档里没有同类型实例 ⇒ 克隆**原生**记录 + 改类型键 + 类型签名整体覆写
            # ★模板必须"原生"（见 `_pick_bld_template`）：取我们刚注入的记录会产出混血 ⇒ 弹框
            info = self._type_sig_table().get(type_name.upper())
            if not info or not info.get('sig'):
                raise RuntimeError(
                    '档里没有 %s 的实例，且语料库没有该类型的类型签名数据，无法跨类型赠送'
                    % type_name)
        if pl.find(self.FACTORY_GUID) >= 0:
            raise RuntimeError('本档已有工厂对象（生产正在进行），不支持再送')
        rec = bytearray(pl[tpl:tpl + self.BLD_SIZE])
        o = 16
        old_key = struct.unpack_from('<I', rec, o)[0]
        for j in range(0, 1844 - 3):
            if struct.unpack_from('<I', rec, o + j)[0] == old_key:
                struct.pack_into('<I', rec, o + j, 0)
        new_key = self._free_key(pl, 0x13963000)
        self._injected_keys.add(new_key)        # ★标记为"我们注入的" ⇒ 后续不再当模板
        struct.pack_into('<I', rec, o + 0, new_key)
        struct.pack_into('<I', rec, o + 208, 0)
        for xo in (160, 164, 600, 604, 1616, 1620):
            struct.pack_into('<I', rec, o + xo, 0)
        if cross:
            struct.pack_into('<I', rec, o + 1316, tkey)
            struct.pack_into('<I', rec, o + 1608, info['art_x'])
            struct.pack_into('<I', rec, o + 1612, info['art_y'])
            for off in (112, 116, 1352):
                struct.pack_into('<I', rec, o + off, info['hp'])
            for off_s, v in info['sig'].items():
                off = int(off_s)
                if off in self._SIG_HANDLED or 0x01000000 <= v <= 0x20000000:
                    continue
                struct.pack_into('<I', rec, o + off, v)
        if struct.unpack_from('<I', rec, o + 1828)[0] != 1:
            struct.pack_into('<I', rec, o + 1828, 1)
        # ★就绪未落地：视口/相机对（1600/1604/1628/1632）与地形高度（168/608/1624）
        #   游戏原生 ready 记录全部为 0（实测）——清零，不抄模板的旧值
        for off in (1600, 1604, 1628, 1632, 168, 608, 1624):
            struct.pack_into('<I', rec, o + off, 0)
        assert bytes(rec).count(struct.pack('<I', new_key)) == 1

        blob = bytearray(self.FACTORY_BLOB)
        fact_self = struct.unpack_from('<I', blob, 20)[0]
        if pl.find(struct.pack('<I', fact_self)) >= 0:
            fact_self = self._free_key(pl, 0x10300000)
            struct.pack_into('<I', blob, 20, fact_self)

        p2 = pl.find(self.QUEUE_ANCHOR_GUID)
        if p2 <= 0 or pl[p2 - 8:p2] != b'\x00\x00\x00\x00\x0a\x00\x00\x00':
            raise RuntimeError('生产区锚点不符（00000000 0a000000 + 2EBB6D66）')
        entry = (struct.pack('<I', new_key) + b'\x00' * 12 + b'\xff\xff\xff\xff'
                 + struct.pack('<I', self._player_house_key() or self.QUEUE_CONST_PTR)
                 + bytes.fromhex('01010000') + bytes.fromhex('00000000'))
        blob148 = bytes(blob) + b'\x00\x00\x00\x00\x0a\x00\x00\x00' + entry \
            + b'\x0a\x00\x00\x00'
        assert len(blob148) == 148

        # 侧栏 strip 槽：[A][07][Y][F] 条目，A = rules [BuildingTypes] 0 基序号（2026-10-15 五点全中），
        # F = 工厂键（空态 0）。★必须写进【目标类型自己的槽】——写进别的类型的槽 =
        # 加载正常但"就绪"显示成别的建筑（T9 教训）。
        # ★★栏位归属（2026-10-15 定案）：**两组槽 = 侧栏两栏**，第 1 组 = 建筑栏（BuildCat≠Combat，
        #   如 GAPOWR/GAREFN/GAPILE/GAWEAP/GAYARD/GAOREP/AMRADR）、第 2 组 = 防御/战斗栏
        #   （BuildCat=Combat，如 GAWALL/NASAM/GACSPH/GAWEAT/GASPYSAT/GAGAP/GAPILL）——14/14 全中。
        # ★★★决定性事实（2026-10-15 深夜，玩家样本 `能建光棱塔.sav` 实证 15/15）：
        #   **侧栏槽数组 = 该玩家「前置全满足」的可建造类型集合**，引擎读档时按科技状态重建。
        #   ⇒ 目标类型没有槽 = 前置未满足 ⇒ **手写槽无效**（T12c 实测：不弹框但侧栏不显示）、
        #     而「有记录/blob 却不写槽」⇒ 校验不一致 ⇒ **二次载入弹框**（T12b 实测）。
        #   ⇒ 就绪赠送必须落在**已存在的槽**上；前置不满足时应拒绝并提示先补前置建筑。
        a_target = self._buildable_index(type_name)
        if a_target is None or a_target < 0:
            raise RuntimeError('%s 不在可建造列表里（TechLevel<1 或 rules 缺失）——'
                               '「就绪未落地」只能送可生产建筑；建造场等请用「直接放好」'
                               % type_name)
        fact_self = struct.unpack_from('<I', blob, 20)[0]
        groups = self._strip_groups(pl)
        slots = [(e[0], e[1], e[2]) for g in groups for e in g if e[4] and e[1] == a_target]
        if len(slots) > 1:
            raise RuntimeError('类型 %s 的侧栏槽定位到 %d 条（A=%d）' %
                               (type_name, len(slots), a_target))
        inplace_strip = None            # (偏移, 52B)：原地覆写空槽（仅 allow_new_slot 时）
        ins_strip = None                # 兜底：找不到空槽才插一条
        if not slots:
            pre = (self._rules_prereq or {}).get(type_name.upper(), '')
            have = {b['type'].upper() for b in self.buildings()}
            miss = []
            for tok in [t.strip() for t in pre.split(',') if t.strip()]:
                need = self._PREREQ_ALIAS.get(tok, (tok,))
                if not any(n in have for n in need):
                    miss.append(tok)
            msg = ('%s 当前不在侧栏（前置未满足%s）——侧栏槽 = 「前置全满足」的类型集合，'
                   '引擎读档会按科技状态重建 ⇒ 手写的槽会被丢弃（T12c 实测不显示）。'
                   '请先让该玩家具备前置建筑（%s），或改送前置已满足的类型。'
                   % (type_name, ('：缺 ' + '/'.join(miss)) if miss else '',
                      pre if pre else '该类型无前置但本档仍不可建（可能缺建造厂/阵营不符）'))
            if not allow_new_slot:
                raise RuntimeError(msg)
            self._gift_slot_note = msg + '（已按 allow_new_slot 强制覆写空槽，风险自负）'
            if not groups:
                raise RuntimeError('本档没有侧栏槽数组（该玩家无基地/建筑栏为空）——'
                                   '请改用「直接放好」')
            cat = (self._rules_buildcat or {}).get(type_name.upper(), '')
            gsel = (groups[-1] if cat == 'COMBAT' else groups[0]) if len(groups) > 1 \
                else groups[0]
            occ = [e for e in gsel if e[4]]                # 占位条目
            # 空槽 = 该组里 (A=0 且 F=0 且非占位) 的条目，优先取末条占位之后的
            empty = [e for e in gsel if not e[4] and e[1] == 0 and e[3] == 0
                     and e[0] > (occ[-1][0] if occ else -1)]
            if not empty:
                empty = [e for e in gsel if not e[4] and e[1] == 0 and e[3] == 0]
            if empty and occ:
                tpl_e = occ[-1]
                rec_e = bytearray(pl[tpl_e[0]:tpl_e[0] + 0x34])
                struct.pack_into('<I', rec_e, 0, a_target)      # A = 目标序号
                struct.pack_into('<I', rec_e, 12, fact_self)    # F = 工厂键
                inplace_strip = (empty[0][0], bytes(rec_e))
            else:
                # 兜底（该组无空槽）：插一条（**会把后续数据后移 ⇒ 弹框风险**，仅强制模式）
                last = [e for g in groups for e in g][-1]
                tpl_rec = bytearray(pl[last[0]:last[0] + 0x34])
                struct.pack_into('<I', tpl_rec, 0, a_target)
                struct.pack_into('<I', tpl_rec, 12, fact_self)
                ins_strip = (last[0] + 0x34, bytes(tpl_rec))
            slots = []          # 新条目的 F 已自带，不走内联写入
        if slots:
            slots = [slots[0][0] + 12]
        if slot2_off is not None:
            slots.append(slot2_off)
        else:
            me = next((h for h in self.houses() if h['is_player'] and h['verified']), None) \
                or next((h for h in self.houses() if h['verified']), None)
            if me and me.get('off'):
                slots.append(me['off'] + 0x50B0)
        slots = sorted(set(slots))
        bad = [s for s in slots
               if struct.unpack_from('<I', pl, s)[0] not in (0, fact_self)]
        if bad:
            # 写不出一致状态 ⇒ 两槽都不写（宁可不显示，也不给校验器崩档机会）
            self._gift_slot_note = ('⚠ 槽2 定位异常 @%s，两槽均未写（加载安全，'
                                    '但侧栏可能不显示就绪项）' % [hex(x) for x in bad])
            slots = []
        # 新写/新插的 strip 条目自带 F=factory（不走这里的 F 写入）
        for s in slots:
            struct.pack_into('<I', pl, s, fact_self)

        # 写入：锚 8B 原地替换为 blob148（净 +140B）+ 记录组尾追加（+1860）+ 计数 +1
        #       （新槽：原地覆写空槽 = 零增长；仅兜底才 +52B）
        struct.pack_into('<I', pl, cnt_off, len(hs) + 1)
        ins = [(p2 - 8, None)]           # 先删除锚 8B（用切片替换实现）
        if ins_strip is not None:
            ins.append(ins_strip)
        ins.append((self._bld_tail(pl, hs), bytes(rec)))
        for pos, data in sorted(ins, key=lambda t: -t[0]):
            if data is None:
                pl[pos:pos + 8] = blob148          # ★替换 8B（净 +140），不是插入！
            else:
                pl[pos:pos] = data
        if inplace_strip is not None:
            pos, data = inplace_strip
            pl[pos:pos + 0x34] = data              # ★原地覆写空槽（零增长）
        grow = self.BLD_SIZE + 140 + (0x34 if ins_strip is not None else 0)
        assert len(pl) == base_len + grow, \
            '明文长度异常：%d ≠ %d + %d' % (len(pl), base_len, grow)
        nk = bytes(pl).count(struct.pack('<I', new_key))
        if nk != 2 or pl.count(self.FACTORY_GUID) != 1:
            raise RuntimeError('自证失败：新键 %d 处（应 2）/ 工厂 GUID %d 处（应 1）'
                               % (nk, pl.count(self.FACTORY_GUID)))
        self._blds = None
        return {'mode': '就绪未落地', 'type': type_name, 'key': new_key,
                'factory_key': fact_self, 'slots': slots,
                'slot_mode': ('覆写空槽@%#x' % inplace_strip[0]) if inplace_strip
                             else ('插入新槽@%#x' % ins_strip[0] if ins_strip else '写已有槽'),
                'note': '侧栏应出现"就绪"的 %s' % type_name}

    def auto_ev_size(self):
        """事件对象尺寸：能实测就实测；档里只有 1 个事件时按结构推断（都带校验）。

        有 ≥2 个事件 ⇒ 直接用步长（`_newtrig_groups`）。
        只有 1 个 ⇒ 事件组是 `[u32 n][对象×n][4B 间隙]`，其后紧跟下一组的
        `[u32 m][对象…]`；所以「下一个对象 GUID 位置 − 8 − 本事件起点」= 对象尺寸
        （本档实测 108；大任务档 124 同法可推）。
        """
        try:
            return self._newtrig_groups()['ev_size']
        except Exception:
            pass
        evs = self.events()
        if not evs:
            raise RuntimeError('档里没有任何事件对象，无法确定事件对象尺寸')
        o = evs[-1]['obj']
        pl = self.plain
        for g in range(o + 64, min(o + 200, len(pl) - 16)):
            if pl[g + 6:g + 8] != b'\xd2\x11':       # GUID 的 Data3 = 0x11D2（小端 d2 11）
                continue
            cnt = struct.unpack_from('<I', pl, g - 4)[0]
            size = g - 8 - o
            if 96 <= size <= 160 and 0 < cnt <= 64:
                return size
        raise RuntimeError('推断不出事件对象尺寸（可显式传 ev_size='
                           'SaveDoc.probe_event_size(同族参考档)）')

    def trigger_readiness(self):
        """能不能在这份档上**新写触发** → `(ok, 给玩家的一句话原因, 开发者诊断)`。

        2026-10-17 玩家实测暴露的两类档：
          · **正常档**（脚本给了触发器/编辑器以前加过）⇒ 触发集群 + 事件/行为对象组都在，`ok=True`；
          · **新开局的干净档**（如 `ALL01UMD.sav` / `SAVE4823.SAV`）⇒ 全文 **0 个**触发/标签/
            TagNode/CellTag 对象，**也没有事件/行为对象**（`events()/actions()` 皆空）。
            这种档要新写触发，得从「全零」凭空造 6 个对象组（tag/cell/trig/node/event/action）
            —— 目前**未实装**（需要游戏内逐条验收，硬造会让指针归并器报错）。
        ⇒ 这里诚实判定并给出原因，让 GUI/CLI 优雅退回「直接落成」。
        """
        pl = self.plain
        tag = pl.find(TRIG_TAGOBJ_GUID) >= 0
        trig = pl.find(TRIG_INSTANCE_GUID) >= 0
        try:
            evs = self.events()
            acts = self.actions()
        except Exception:
            evs = acts = []
        if not tag or not trig:
            return (False,
                    '这份存档里**一个触发器对象都没有**（全文 0 个触发/标签对象，'
                    '新开局的干净档就是这样）',
                    'tag=%s trig=%s events=%d actions=%d'
                    % (tag, trig, len(evs), len(acts)))
        if not evs and not acts:
            return (False,
                    '这份存档里**没有事件/行为对象**，凭空造这两组还没实现',
                    'tag=%s trig=%s events=0 actions=0' % (tag, trig))
        # 组成员齐但事件只有 1 个 ⇒ 用结构推断尺寸（已验证 108 / 大任务档 124）
        try:
            self.auto_ev_size()
        except Exception as ex:
            return (False, '量不出事件对象尺寸（%s）' % ex, 'auto_ev_size 失败：%s' % ex)
        return (True, '', 'ok')

    def can_place_by_trigger(self):
        """触发法是否可用（等价于 `trigger_readiness()[0]`）。"""
        return self.trigger_readiness()[0]

    def place_building_by_trigger(self, type_name, cx, cy, house=None,
                                  ev_type=13, ev_arg=1, name=None, ev_size=None,
                                  register=None):
        """**用「触发法」让引擎自己造一栋建筑**（2026-10-17 玩家定案的主用法）。

        玩家实测的两个好处：
          ① 引擎走正规建造流程 ⇒ **有建造动画、模型完整**（不会出现"无头"这类半初始化显示错）；
          ② 落点由引擎判定 ⇒ **不可建造的地形（悬崖边/水面等）会被直接拒绝**，不会刷进不能放的地方。
        做法（与 T1/T2 完全同一条配方）：
          · 目标格没有路径点就写一个（`ensure_waypoint_at`，号取最大号+1，≤701）；
          · 新写触发：**事件 8「任何事件」** → **行为 125「将建筑建于…」** `<类型>` @ 该路径点；
          · 所属方 = `house`（默认玩家阵营）。
        ★2026-10-17 三轮实测定案：
          · **四件套（tag/trig/ev/ac）引擎不执行**（T3）；只有六件套也不够（T4a）；
          · **必须再登记进「定时触发器激活登记表」**（登记 = 武装开关，T6 事件 8 + 登记才烧）⇒ 这里一律登记；
          · **引擎判定触发的节拍 = 15 帧（1 逻辑秒）** ⇒ 「任何事件」与「流逝 1 秒」等价，
            **"读档立即出现"做不到**，最快就是约 1 秒后（玩家实测）。
          · 结论：默认事件 13 + 1 秒（与任何事件同节拍，但少一层不确定性）。
        返回 dict（含 waypoint / cell / trigger_id / warnings）。
        """
        t = str(type_name).strip().upper()
        if not t:
            raise ValueError('建筑类型不能为空')
        cx, cy = int(cx), int(cy)
        lim = self.coord_limit()
        if not (0 <= cx < lim[0] and 0 <= cy < lim[1]):
            raise ValueError('格子 (%d,%d) 超出范围（0..%d / 0..%d）'
                             % (cx, cy, lim[0] - 1, lim[1] - 1))
        if house is None:
            house = self.info.get('玩家阵营') or None
        # ★先判「这份档能不能写触发」——**必须在改任何字节之前**（否则会留下半截路径点）
        ok_trig, why, diag = self.trigger_readiness()
        if not ok_trig:
            raise TriggerUnavailable(why, diag)
        wp = self.ensure_waypoint_at(cx, cy)
        wp_note = ''
        if wp is None:
            # ★地图声明的路径点号已经用满 ⇒ **不越界硬写**（越界引擎不认：玩家实测 511 无效），
            #   改用离目标格最近的已有路径点，并把实际落点如实报出来。
            near = self.nearest_waypoint(cx, cy)
            if near is None:
                raise RuntimeError('本档既没有空闲路径点号，也没有任何已有路径点可用')
            wp, (nx, ny) = near
            wp_note = ('⚠ 地图只声明了 0..%d 号路径点且已全部占用 ⇒ 改用最近的 #%d @(%d,%d)'
                       '（离目标 (%d,%d) %.0f 格）；建筑会出现在那个格子'
                       % (self.waypoint_limit(), wp, nx, ny, cx, cy,
                          ((nx - cx) ** 2 + (ny - cy) ** 2) ** 0.5))
        if ev_size is None:
            ev_size = self.auto_ev_size()
        nm = (name or ('刷' + t))[:16]
        if register is None:
            register = True          # ★2026-10-17 T6：登记表就是"武装"开关 —— 一律登记
        r = self.create_trigger(nm, ev_type, ev_arg, 125, 0, owner=house,
                                ac_name=t, ac_waypoint=wp, ev_size=ev_size,
                                pieces=True, register=register)
        out = dict(r)
        out.update({'mode': '触发法', 'waypoint': wp, 'cell': (cx, cy),
                    'type': t, 'house': house, 'ev_size': ev_size,
                    'ev_type': ev_type, 'ev_arg': ev_arg,
                    'note': '读档后引擎自己造：有建造动画；不可建造的地形会被引擎拒绝'})
        if wp_note:
            out['warnings'] = list(out.get('warnings') or []) + [wp_note]
            out['waypoint_cell'] = self.waypoint_cell(wp)
        return out

    # ------------------------------------------------------------------ #
    # 引擎「新建态」常量表（2026-10-21，黄金样本 SAVE4823 定案 + 静态复核）
    # ------------------------------------------------------------------ #
    # 来源：玩家用**触发法**让引擎造一栋 ATESLA 后游戏内存档（E:\YURI\SAVE4823.SAV），
    #   拿引擎新塔 @(11,178) key=15DD8410 与同档 66 栋同类楼逐字段比 ⇒
    #   「引擎非 0 而同类全 0」的偏移共 24 个 = 本表 22 个 int + +0x3A8（自引用）
    #   + +0x528（每栋独占的关联指针，**不可跨档搬**，待查）。
    # 复核（`_verify_placed_cand4.py`）：22 个值逐偏移全中；黄金塔「≠0 而同类全 0」
    #   恰好 24 个；同档 66 栋在这 23 个（除 +0x528）偏移上全 0。
    # ⚠ 下表是**记录绝对偏移**；`add_placed_building` 里 `o=16` 是载荷起点，
    #   写的时候要 `o + (X - 16)`（见函数内注释，+0x3A8 踩过同一个坑）。
    # ⚠ 只有**一个**黄金样本（ATESLA）⇒ 是否全型号通用未经游戏验证，默认不写。
    NEW_STATE_CONSTS = (
        (0x0D4, 0x1B), (0x114, 0x1B), (0x3A4, 0x1B),
        (0x17C, 0x19), (0x188, 0x19), (0x1A0, 0x19), (0x1AC, 0x19), (0x1F4, 0x19),
        (0x200, 0x19), (0x210, 0x19), (0x254, 0x19), (0x300, 0x19), (0x36C, 0x19),
        (0x38C, 0x19), (0x3BC, 0x19), (0x3D0, 0x19), (0x53C, 0x19), (0x564, 0x19),
        (0x618, 0x19), (0x6E4, 0x19), (0x6B8, 0x18), (0x42C, 0x01010000),
    )

    def add_placed_building(self, type_name, cx, cy, z=0, sig_mode='full',
                            comp_mode='zero', grow_mode='insert', ref_mode='keep',
                            house=None, require_free=False, new_state=False,
                            buildup='fresh', cross_clean=False, anim=False):
        """（sig_mode: full=套类型签名 / safe=跳过镜像指针 / none=完全不套，诊断用）
           （comp_mode: **zero=不建伴随对象 + 把 4 个伴随指针清 0【默认，2026-10-21 游戏内验收】
                        / none=不建伴随对象，但同类型模板会**保留模板的 4 个指针**
                              ⇒ 新楼与模板共用同一个伴随对象（★未验收，疑似弹框源，勿用）
                        / clone=克隆模板伴随（**已修 +52 实例 ID**，2026-10-16 晚）
                        / clone_clean=克隆但清掉悬空指针（+52 亦已修）
                              ⇒ ★2026-10-21 实测**读档弹框**（#2~#9 全走这条路，全弹）
                        / build=只抄类常量【实测"新开即弹框"，勿用】）
           （grow_mode: insert=登表插字节【默认，已知可用】/ inplace=覆写表预留空位【实验，曾致载入即崩】）
           （house: **所属方** —— None=保持模板值（默认，= 原来那栋的主人）；
                   也可给阵营短名 'Russians' / 中文名 '苏俄' / House 对象键 int）
           （new_state: True=按黄金样本写引擎「新建态」22 个 int 常量【默认 False】，
                       见 NEW_STATE_CONSTS；候选 #5 起用，游戏内验收后再考虑设默认）
           （buildup: **默认 True（§188 定案，玩家 T16 实测）** = 新楼进游戏**播一遍建造动画**，
                       而且**播完模型是完整的**。关键在于配方里必须带 **`+0x6E4 = 0`**：
                         · `6E4=0` ⇒ 引擎认为"这段建造动画还没播" ⇒ 播一遍 Buildup，
                           **播的过程会把模型/子对象重建一遍** ⇒ 播完完整（T16 的 A 栋）；
                         · `6E4=1`（旧版从完成态模板继承来的）⇒ 引擎认为"已播完" ⇒ 不播，
                           停在"记录里的指针是悬空的"状态 ⇒ **显示不全**（T16 的 B 栋）。
                       ⚠ 所以 §186 那条"直接落成播不出动画"的结论**作废**：它当时继承了 `6E4=1`。
                       传 `buildup=False` 才是"立即显示成品"（无动画，且跨类型克隆时可能残缺）。
           （cross_clean: **默认 False**（2026-10-23 §197 改），仅跨类型时生效 —— 把模板记录里
                       `0x0F000000~0x14000000` 段的运行期指针一律清 0（§184）。
                       ★★★**实测必须关掉**：玩家 T51（清）/ T54（不清）同基档对照，
                       清的 ⇒ **读档弹框**（引擎一致性断言失败，主动除零）、
                       不清的 ⇒ **正常，多出一栋目标建筑**。
                       原因：那些槽是引擎校验必需的结构键，清 0 = 两份数据对不上。
                       §184 当时清它是为了修"显示不全"，而那个问题的真因是建造态（§196）。）"""
        sig_mode_note_comp = False
        """凭空放一栋**已落位**建筑（U2 配方 2026-10-15 游戏内验收：记录 + L1/L2/L3 全登）。

        克隆本档同类型记录（无则跨类型：类型键+类型签名）→ 组尾追加、组计数 +1、
        三张登记表追尾追加。
        ★+1600/+1604 = 该建筑在当前相机下的**屏幕坐标**（按目标格推算，不是全档共享值）；
        +1628/+1632 = 相机对（全档共享，抄本档）；Z（168/608/1624）= 地形高度×416，平地 0；
        +208 = 自身键。**+1388..+1400 = 4 个伴随指针**：★2026-10-21 游戏内验收 ——
        **一律清 0（不建伴随对象）才稳**；克隆伴随（clone/clone_clean 造 476B 伴随）实测**读档弹框**。
        同类型保留模板值（'none'）= 与模板共用同一伴随对象，未验收、勿用。
        """
        pl = self.plain
        base_len = len(pl)
        # ★2026-10-21（修 #1）：**不许覆盖别人的格子**。
        #   上一份卡死样本落在 (98,130)，而那格本来就有占用者 —— 旧代码只是"提示已覆盖"。
        #   require_free=True 时直接拒绝，让调用方换一格（或改用触发法）。
        if require_free and self.cell_taken(cx, cy):
            raise RuntimeError(
                '(%d,%d) 这格已经有东西占着（建筑地基 / 单位），不能再往上放。\n\n'
                '换一个空位，或者改用「触发法」—— 触发法由引擎自己造楼，'
                '会自动避开悬崖 / 水面 / 占用格。' % (cx, cy))
        hs, cnt_off = self._bld_group()
        if not hs:
            raise RuntimeError('这份存档里一栋建筑都没有（全新开局），没有建筑组可用 —— '
                               '请先在游戏里放一栋建筑并保存，再用本工具操作')
        tkey = self.building_types().get(type_name)
        if not tkey:
            raise RuntimeError('档里没有类型 %s' % type_name)
        tpl, cross = self._pick_bld_template(hs, pl, tkey)
        # ★2026-10-21（修 #1：伴随对象）：comp_mode 需要克隆伴随时，**模板必须自带伴随**。
        #   本档很多建筑的 +1388（伴随键）是 0，同型号里只有一部分带伴随；
        #   若模板恰好是 0，克隆路径会静默退化成"新楼没伴随 / 与模板共用" ⇒ 引擎行为不可预期。
        #   这里换一栋**同型号且 +1388 != 0** 的原生记录当模板（仍排除我们自己注入的）。
        if comp_mode in ('clone', 'clone_clean', 'build'):
            def _has_comp(g):
                return struct.unpack_from('<I', pl, g + 16 + 1388)[0] != 0
            if not _has_comp(tpl):
                same = [g for g in hs
                        if g not in self._injected_keys
                        and struct.unpack_from('<I', pl, g + 16 + 1316)[0] == tkey
                        and _has_comp(g)]
                if same:
                    tpl = same[0]
        if cross:
            # ★模板必须"原生"（见 `_pick_bld_template`）——取刚注入的记录 ⇒ 混血 ⇒ 弹框
            info = self._type_sig_table().get(type_name.upper())
            if not info or not info.get('sig'):
                raise RuntimeError(
                    '档里没有 %s 的实例，且语料库没有该类型的类型签名数据，无法跨类型赠送'
                    % type_name)
        rec = bytearray(pl[tpl:tpl + self.BLD_SIZE])
        o = 16
        tpl_key = struct.unpack_from('<I', rec, o)[0]   # 模板自身的键（插字节后偏移会漂，必须现在取）
        # ★★★★★ 2026-10-17 23:5x：**坐标槽必须按模板记录实测**，不能写死偏移。
        #   实测所有档（原始/引擎重存）的坐标对都在 `base+104/+108` 与 `base+544/+548`，
        #   而旧代码按 `o+160/+600`（= base+176/+616）去改 —— 那两处根本不是坐标，
        #   于是新记录**没有合法坐标**（工具读回来是 (141,411) 这种鬼值），
        #   引擎只能靠别的线索摆位置，原始档上就干脆不显示。
        #   做法：在模板记录里搜"成对的 (px,py)"，命中的偏移就是坐标槽（通常两处镜像）。
        tinfo = next((b for b in self.buildings() if b['base'] == tpl), None)
        ref = tinfo
        if ref is None or ref['x'] < 0:
            # 模板自己没落位（-1,-1）⇒ 借同类型已落位的一栋当"布局参照"
            cand = [b for b in self.buildings()
                    if b['x'] >= 0 and b['type'] == str(type_name).upper()]
            ref = cand[0] if cand else next((b for b in self.buildings() if b['x'] >= 0), None)
        coord_slots = []
        if ref is not None:
            for k in range(0, self.BLD_SIZE - 8, 4):
                if (struct.unpack_from('<I', rec, k)[0] == ref['px']
                        and struct.unpack_from('<I', rec, k + 4)[0] == ref['py']):
                    coord_slots.append(k)
        if not coord_slots:
            coord_slots = [self.BLD_COORD_OFF, self.BLD_COORD2_OFF]
        scx = ref['x'] if ref else cx
        scy = ref['y'] if ref else cy
        dx, dy = (cx - scx) * 256, (cy - scy) * 256
        new_key = self._free_key(pl, 0x13963000)
        self._injected_keys.add(new_key)        # ★标记为"我们注入的" ⇒ 后续不再当模板
        struct.pack_into('<I', rec, o + 0, new_key)
        # ★★★★★ 2026-10-23 §194：**清掉继承来的动画槽**。
        #   克隆模板会把 `+0x57C/+0x580/+0x584/+0x590` 一起搬过来 ⇒ 新楼指向**模板那栋的**
        #   动画对象（两栋共用一个 anim）—— 这正是历史上"克隆伴随必弹框/显示错乱"的味道。
        #   清 0 之后由 `add_building_anim()` 现场造一个新的挂上去。
        for _off in self.ANIM_SLOTS:
            struct.pack_into('<I', rec, o + _off, 0)
        if cross:
            struct.pack_into('<I', rec, o + 1316, tkey)
            struct.pack_into('<I', rec, o + 1608, info['art_x'])
            struct.pack_into('<I', rec, o + 1612, info['art_y'])
            for off in (112, 116, 1352):
                struct.pack_into('<I', rec, o + off, info['hp'])
            for off_s, v in info['sig'].items():
                off = int(off_s)
                if off in self._SIG_HANDLED or 0x01000000 <= v <= 0x20000000:
                    continue
                if sig_mode == 'none':
                    continue        # 诊断用：完全不套签名（只改类型键/美术/耐久）
                if v == 0 and sig_mode != 'zero':
                    # ★★★★★ 2026-10-23 §197（玩家 T51/T54 对照定案）：
                    #   **签名里的 0 不要覆盖模板的值**。签名是"某个真实样本"的快照，
                    #   它那里是 0 不代表新楼也该是 0 —— 而模板那些槽往往是**引擎校验必需的
                    #   结构键**（实测：套签名把 9 个非零指针槽压到 6 个 ⇒ 读档一致性断言
                    #   失败 ⇒ 主动除零崩溃）。只写"非 0"的类常量，模板结构原样保留。
                    continue
                if sig_mode == 'safe' and 0x00400000 <= v < 0x01000000:
                    continue        # ★安全模式：跳过镜像指针类（0x007Exxxx 等）——跨档抄会变悬空
                struct.pack_into('<I', rec, o + off, v)
        # ★★★+208 = **自身键**（2026-10-16 定案，同类型/跨类型都写）：本档 11 栋建筑的 `+208`
        #   **全部等于自身键**。克隆模板时若留着模板的值 ⇒ 它指向**模板那栋建筑**（本档有真身！）
        #   ⇒ 游戏"攻击/选中"会解析到模板建筑身上：跨类型（模板=空指部）⇒ 打空指部；同类型
        #   （模板=另一栋同型建筑）⇒ 打那一栋（B1/B3/B4/B6 实测症状全中）。
        #   ⚠ 历史教训：引擎"本会话新建"的记录里 +208 是**别的池的键**（放下光棱塔.sav 的 ATESLA
        #   = 0x130EA2E0，不是本档任何建筑的键），那种值才不会指向别人 ⇒ 写自身键同样安全（本档惯例）。
        struct.pack_into('<I', rec, o + 208, new_key)        # ★2026-10-21（修 #4，黄金样本 SAVE4823 定案）：**+0x3A8 也写自身键**。
        #   引擎用触发法造出来的那栋 ATESLA：+0x3A8 = 15DD8410 = 它自己的键；
        #   而本档同型号 66 栋正常建筑的 +0x3A8 **全是 0** ⇒ 这是"新建时初始化"的**自引用**字段。
        #   我们一直没写它（克隆模板 ⇒ 要么 0、要么模板的值）⇒ 加载器解析这条自引用时会落到
        #   别的对象/空上，正是"内部错误弹框"的典型来源。
        # ⚠ 记录内绝对偏移 0x3A8 = 载荷偏移 0x398（本函数所有 +xxx 都是**载荷**偏移，o = 16）
        struct.pack_into('<I', rec, o + 0x398, new_key)

        # ★2026-10-21（修 #5，黄金样本 SAVE4823 的「新建态常量表」，§141.1 + 静态复核）：
        #   引擎放下的那栋 ATESLA 与同档 66 栋同类楼比，「引擎非 0 而同类全 0」的字段共 24 个
        #   = 22 个 int 常量 + +0x3A8 自引用（修 #4）+ +0x528 关联指针（待查，见 §142）。
        #   22 个 int 已由 `_verify_placed_cand4.py` 在黄金塔上**逐偏移复核**：
        #   值全中（0x1B×3 / 0x19×17 / 0x18 / 0x01010000）、66 栋同类在这些偏移上全 0。
        #   ⚠ 偏移约定（§141.2 的坑）：表里是**记录绝对偏移**；本函数 `o + xxx` 是载荷偏移
        #     ⇒ 绝对 X 必须写 `o + (X - 16)`。写错位置不报错（非键值，自证断言抓不到）。
        #   ⚠ 样本只有一个（ATESLA）⇒ 默认关闭，候选 #5 显式开启；游戏内验收后再议默认。
        if new_state:
            for _abs, _v in self.NEW_STATE_CONSTS:
                struct.pack_into('<I', rec, o + (_abs - 16), _v)

        # ★★★+264 = **自身键 + 0x9C**（2026-10-16 定案，**唯一一个"自我引用"字段**）：
        #   对三份样本（母本 11/11 栋、外挂放置 12/12、手放 12/12）逐 dword 扫描
        #   "值 == 自身键 + 常量" ⇒ **只有 +264 恒等于 自身键 + 0x9C**（其余 1 栋的命中都是巧合）。
        #   它是**指向自己内部的指针**（记录载荷 +264，而 key 在载荷 +0）。
        #   ⚠ 后记（2026-10-16 17:25）：本条**不是**重载崩的原因（真因是下面的 `+20`）。
        #   而且实测引擎**只是把 +264 原样搬运**（重存档里它 = 上一份存档的旧键+0x9C，那个键早已不存在），
        #   说明加载器**并不校验** +264 —— 写 `自身键+0x9C` 只是"跟随本档归一化形态"，无害且更一致。
        struct.pack_into('<I', rec, o + 264, new_key + 0x9C)
        # ★★★★★ +20 / +972 = **每栋唯一的实例 ID**（2026-10-16 傍晚定案，**重载崩 + "攻击指向模板"的真因**）
        #   证据链：
        #   ① 同类型 6 栋 GAPOWR 逐 dword 求"组内每栋都不同" ⇒ 13 个字段，其中 +20/+972 是**非指针**小值；
        #   ② 这两个值在**整份明文里只出现 1 次**（就在字段本身）⇒ **不是指针，是 ID**；
        #   ③ **引擎原生放下**的一栋，其 +20 是**全新顺延值**（母本末栋 `0xfa378` → 原生新栋 `0xfa387`），
        #      全档 +20 **互不重复**；
        #   ④ 我们克隆模板时**把模板的 +20 原样带过来** ⇒ **两栋共用一个 ID**；
        #   ⑤ **引擎重存档把这个重复原样保留**（§16）⇒ 所以"引擎自己写一遍"也救不回来（§17.2）。
        #   ⇒ 症状全部对上：**攻击/选中被解析到"模板那栋"**（跨类型 = 空指部），
        #      且**拆世界时 ID 表对不上 ⇒ 重载崩**。
        _ids20 = [struct.unpack_from('<I', pl, g + 16 + 20)[0] for g in hs]
        new_id20 = self._next_inst_id(pl, hs)
        struct.pack_into('<I', rec, o + 20, new_id20)
        _ids972 = {struct.unpack_from('<I', pl, g + 16 + 972)[0] for g in hs}
        new_id972 = int(hashlib.md5(bytes(rec[:64]) + struct.pack('<I', new_key)).hexdigest()[:8], 16) & 0xFFFF
        for _ in range(0x10000):
            if new_id972 and new_id972 not in _ids972:
                break
            new_id972 = (new_id972 + 1) & 0xFFFF
        struct.pack_into('<I', rec, o + 972, new_id972)
        # ★★★+232 / +1304 = **每栋独有的两个对象指针**（2026-10-16 定案，只有它们"每栋都不同"）。
        #   实测它们的值**不出现在任何一张键表**里，也不在明文里出现第二次
        #   ⇒ 指向的**不是独立序列化对象**（加载器不可能靠存档值恢复）。
        #   我们克隆模板时留着模板的值 ⇒ 疑似"新建筑寄生于模板"（2026-10-16 实测症状：
        #   **攻击新光棱塔会解析到空指部**，而空指部正是跨类型时用的模板 `hs[-1]`；
        #   同类型时同理 ⇒ 指向模板那栋）。
        #   `ref_mode='clear'`：把它们清 0（诊断用单变量；默认 'keep' 保持旧行为）。
        if ref_mode == 'clear':
            for off in (232, 1304):
                struct.pack_into('<I', rec, o + off, 0)
        if comp_mode == 'zero':
            # ★★★★★ 2026-10-21 17:40 **游戏内验收：这是唯一可用的伴随策略（现为默认）**。
            #   玩家实测三档同落点 (14,179)：
            #     ① 修9  = clone_clean（克隆 476B 伴随）      ⇒ **弹框**
            #     ② 修11 = zero（不建伴随 + 4 指针清 0）       ⇒ **进游戏，蓝色光棱塔正常出现**
            #     ③ 触发法（引擎自己造）                      ⇒ 进游戏，红色光棱塔
            #   ⇒ 弹框的唯一原因 = **伴随对象**（克隆出来的那个），与记录/登记/落点/所属方全无关。
            #   机理推测：伴随对象内部有一批指向「子对象」的指针（引擎新建的黄金伴随里是
            #   0x0f4de4xx 一类本档地址），`clone_clean` 把它们一律清 0 ⇒ 加载器按"伴随存在
            #   就必须修复其内部指针"去处理 ⇒ 撞空 ⇒ 弹框。而**没有伴随**时这一段根本不进。
            #   ★2026-10-21 19:5x Ghidra 定案（主文档 §151）：这 4 个 dword 是
            #   **BuildingClass::Anims[3..6]**（对象偏移 0x568/0x56C/0x570/0x574），
            #   属于 `Load` 里 `lea esi,[edi+0x55C]; ebp=0x15` 那个 21 槽 swizzle 循环。
            #   载荷偏移 = 对象偏移 + 4（o=16 处还含 4B 自键）⇒ 1388 对应对象 1384。
            #   合法性依据：母本 1112 栋里 **846 栋 Anims[3] == 0**（不播动画是引擎常态）。
            #   ⇒ 清 0 = 回到"该槽无对象"的原生形态，绝不新建伴随对象。
            for off in (1388, 1392, 1396, 1400):
                struct.pack_into('<I', rec, o + off, 0)
        if cross:
            # ★★★跨类型必须清掉 **Anims[3..6]**（载荷 +1388/+1392/+1396/+1400 = 对象 0x568..0x574）：
            #   这 4 个槽在 `BuildingClass::Load` 的 swizzle 名单里（§151 汇编实证），
            #   槽里任何非 0 值都必须能在本档的「新地址表」里找到，否则合并器走 `idiv 0` 必弹框。
            #   留着模板（**异类型**）的值 ⇒ 建筑**不能被攻击**（A1 实测：+1388 指向 GAAIRC 的伴随对象；
            #   而 A0 同类型克隆指向同类型伴随 → 可攻击）。清 0 ⇒ 显示正常 + 可攻击
            #   （A3 维修厂 / A4 苏联实验室实测通过 —— 它们的 +1388 恰好被签名表清成了 0）。
            #   ⇒ 不再依赖"签名表里恰好有 1388 这一项"。
            for off in (1388, 1392, 1396, 1400):
                struct.pack_into('<I', rec, o + off, 0)
            # ⚠ `comp_mode='clone'`（强行克隆模板伴随对象）**实验性**：A2 曾卡死（当时的产物还缺
            #   +208 自键与格子）。现在允许显式使用以做单变量实验；默认仍是 'none'。
        # ★★★★★ 2026-10-18 00:3x **两套坐标槽并存**（这是本任务档的真相）：
        #   作者手放的建筑：坐标对在 base+104/+108 与 base+544/+548；
        #   引擎运行期新建的建筑（实测那栋 (80,245) 的电厂）：坐标对在 base+176/+180 与 base+616/+620。
        #   老代码一律写 +176/+616 ⇒ 克隆"作者式"模板时写到了垃圾字段，真坐标仍是模板的位置
        #   ⇒ 新建筑要么叠在模板身上、要么不显示；而乱扫槽位会命中无关指针 ⇒ 卡死（D 档）。
        #   ⇒ 正解：**只在这 4 个候选偏移里挑"确实等于模板自身坐标"的那几个**写。
        tinfo = next((b for b in self.buildings() if b['base'] == tpl), None)
        npx, npy = cx * 256 + 128, cy * 256 + 128
        slots = []
        if tinfo is not None and tinfo['x'] >= 0:
            pair = struct.pack('<II', tinfo['px'], tinfo['py'])
            slots = [k for k in (104, 544, 176, 616)
                     if 0 <= k <= self.BLD_SIZE - 8 and bytes(rec[k:k + 8]) == pair]
        if not slots:
            slots = [176, 616]              # 认不出样式 ⇒ 按"引擎式"写（至少不会写坏别的字段）
        for k in slots:
            struct.pack_into('<II', rec, k, npx, npy)
        if struct.unpack_from('<I', rec, o + 1828)[0] != 1:
            struct.pack_into('<I', rec, o + 1828, 1)
        # ★★+1600/+1604 = **该建筑在当前相机下的屏幕坐标**（2026-10-15 定案，不是"全档共享视口"）：
        #     screenX = 30*(cx−cy) + camX、screenY = 15*(cx+cy) + camY
        #   （K2 的 7 栋 GAPOWR 逐位全中；同类型锚点偏移相同 ⇒ 直接用「模板值 + 格差」推算，
        #     跨类型退化为用本档第一栋反解，误差 ≤1 格）。**照抄别栋 = 位置错 ⇒ 裁剪/显示异常**。
        #   +1628/+1632 = 相机对（真·全档共享，抄本档现有建筑）。
        #   Z（168/608/1624）= 地形高度×416（悬崖层级），平地 0。
        ref = (tpl if not cross else hs[0]) + 16
        rpx, rpy = struct.unpack_from('<II', pl, ref + 600)
        rcx, rcy = (rpx - 128) // 256, (rpy - 128) // 256
        dcx, dcy = cx - rcx, cy - rcy
        sx = struct.unpack_from('<i', pl, ref + 1600)[0] + 30 * (dcx - dcy)
        sy = struct.unpack_from('<i', pl, ref + 1604)[0] + 15 * (dcx + dcy)
        struct.pack_into('<i', rec, o + 1600, sx)
        struct.pack_into('<i', rec, o + 1604, sy)
        for off in (1628, 1632):
            struct.pack_into('<I', rec, o + off,
                             struct.unpack_from('<I', pl, hs[0] + 16 + off)[0])
        for off in (168, 608, 1624):
            struct.pack_into('<I', rec, o + off, z)
        # ★★★★★ 所属方（2026-10-17 新增功能）：记录载荷 **+336 与 +544 是同一份 House 对象键的两份副本**
        #   （大任务档 1143 栋 / 12 阵营实测：两处都写着该栋的主人的 House 键；
        #   母本 11 栋全部 = 玩家键；伴随对象里 0/637 个含 House 键 ⇒ 不用改伴随）。
        #   `house=None` ⇒ 保持模板值（= 原来那栋的主人，旧行为不变）。
        owner_key = self.resolve_house(house)
        owner_idx = None
        if owner_key:
            owner_idx = self.house_run_index(owner_key)
            if owner_idx is None:
                raise RuntimeError('认不出阵营 %s 的运行期序号（载荷 +1344）'
                                   % self.house_label(owner_key))
            for off in (self.BLD_OWNER_OFF, self.BLD_OWNER2_OFF):
                struct.pack_into('<I', rec, o + off, owner_key)
            struct.pack_into('<I', rec, o + self.BLD_OWNER_IDX_OFF, owner_idx)
        # ★+208 / +920 不动（实测：**非加载必需**）——原生"新放"记录里 +208 是另一池的键
        #   （放下光棱塔.sav 的 ATESLA +208=0x130EA2E0），而"重新保存过"的档里才归一成自身键；
        #   模板值 0x6F6E30 / 0 在 T3/T8/T9b 里都通过。既然不确定，就不动。

        # ★★★★★ §184（2026-10-22 玩家「还是显示不全」定案）：**跨类型必须清掉模板的指针表**
        #   实测（玩家自己的图 ALL01UMD，6 栋建筑）：
        #     0x0F000000~0x14000000 段（运行期指针/地址）的 dword 数：
        #       GACNST 建造厂 **85 个** / GAPOWR 3~4 个 / **引擎造的 ATESLA 0 个**
        #       我们跨类型放出来的 ATESLA **83 个**（全是从 GACNST 模板带过来的）
        #   ⇒ 这 83 个地址指向的是**模板那栋的子对象**，新楼根本没有 ⇒ 渲染残缺
        #     （玩家看到的就是"显示不全，打一下才正常"：受击触发重绘才把画面纠正过来）。
        #   跨类型模板几乎总是"末栋原生建筑"（常是 GACNST），所以必然踩到。
        #   ⇒ 跨类型时：把这些"本档不可能存在"的指针值一律清 0（白名单里的键/坐标/所属方不动）。
        #   依据：ATESLA 的类型签名里**一个 0x0F-0x14 段的值都没有** ⇒ 清 0 才是该类型的原生形态。
        cross_clean_note = ''
        if cross and cross_clean:
            _keep = set(self._SIG_HANDLED) | {int(k) for k in (info['sig'] or {})}
            _keep |= {16, 24, 264, 972, 208, 0x398, 1828, 336, 544, 1344,
                      1600, 1604, 1628, 1632, 168, 608, 1624}
            _keep |= set(slots)
            _nz = 0
            for _off in range(16, len(rec) - 4, 4):
                if _off in _keep:
                    continue
                _v = struct.unpack_from('<I', rec, _off)[0]
                # ★新键本身也落在 0x13xxxxxx 段 —— 绝不能把自己的自引用清掉（自证会当场抓到）
                if _v == new_key or _v == new_key + 0x9C:
                    continue
                if 0x0F000000 <= _v <= 0x14000000:
                    struct.pack_into('<I', rec, _off, 0)
                    _nz += 1
            if _nz:
                cross_clean_note = '；跨类型已清掉 %d 个模板遗留指针（§184）' % _nz

        keys = [struct.unpack_from('<I', pl, g + 16)[0] for g in hs]
        lists = self._locate_key_lists(keys)
        if not lists:
            raise RuntimeError('没找到建筑登记表（L1/L2/L3），无法登记')
        # ★★★★★ 2026-10-21（修 #6，弹框真因，玩家实测 #4/#5 均弹框后定案）：
        #   `_locate_key_lists` 在**大档**上会把**别的阵营的「本阵营建筑清单」当成全局登记表** ——
        #   SAVE0029 实测：它返回的 3 张"全局表"里，第 3 张 count@0x103BE53 其实是
        #   **YuriCountry 的阵营建筑清单**（536 条），于是新键被同时写进
        #   「Alliance（=新楼主人，正确）」和「YuriCountry（错误）」两张阵营清单
        #   ⇒ 引擎读档做「清单成员必须属于该阵营」校验时对不上 ⇒ 弹框。
        #   **小图为什么没事**：SAVE6784 只有 8 栋建筑，第 3 张表恰好就是
        #   玩家(=新楼主人)自己的清单 ⇒ 同一张表只登记一次，没有污染。
        #   ⇒ 正确做法：**先把所有"阵营建筑清单"从全局表里剔除**，再显式把主人的那份加回来。
        house_lists = {}
        try:
            for _hk in set((self.house_keys() or {}).values()):
                try:
                    _bl = self.house_build_list(_hk)
                except Exception:
                    _bl = None
                if _bl and _bl[3]:                 # 只要"块内精确匹配"的，公式外推的绝不碰
                    house_lists[_hk] = _bl
        except Exception:
            house_lists = {}
        house_cs = {bl[0]: hk for hk, bl in house_lists.items()}
        n_polluted = sum(1 for (cs, _c, _e) in lists if cs in house_cs)
        lists = [t for t in lists if t[0] not in house_cs]
        # 登记目标 = **全局对象登记册** + **主人自己的「本阵营建筑清单」**；
        #   若主人不是玩家，则**不登记进玩家的清单**（依据：玩家实测"敌方塔照样有电"）。
        eff_owner = owner_key or struct.unpack_from('<I', pl, tpl + 16 + self.BLD_OWNER_OFF)[0]
        bl_owner = house_lists.get(eff_owner) or self.house_build_list(eff_owner)
        owner_list_note = ''
        if not lists:
            raise RuntimeError('没找到全局建筑登记表（候选全是阵营清单），无法安全登记')
        if bl_owner and bl_owner[3]:
            reg = list(lists)
            if all(cs != bl_owner[0] for cs, _c, _e in reg):
                reg.append(bl_owner[:3])
            lists = reg
        else:
            # 目标阵营本档**还没有建筑** ⇒ 无法安全定位它的清单
            #   （n=0 的槽位只能靠公式外推，实测插入后**载入弹框** ⇒ 绝不动它）
            #   ⇒ 维持旧行为：只登记全局册（+ 顺带找到的玩家清单）；电力会被算到玩家头上。
            owner_list_note = ('；⚠ 该阵营本档还没有建筑 ⇒ 未写它的建筑清单'
                               '（引擎可能按玩家的电算；建议先给它放一栋建筑，之后的添加就精确了）')
        # ★★伴随对象（记录 +1388 指向的 476B 对象，类 GUID c32d270e-…）：引擎放建筑时
        #   **必定新建一个并登记**（外挂样本实测：+2340 = 记录 1860 + 伴随 476 + 4）。
        #   我们过去让新记录**共用模板的伴随键** —— 而引擎从不让两栋共用 ⇒ 疑似弹框根因。
        #   这里按引擎配方克隆模板的伴随对象：改自身键(+0x20) 与格(+0xC0/+0xC4)，
        #   插到模板伴随对象之后，并把新键登记到「对象表」（计数 > 建筑数的那些表）。
        comp_key, comp_body, comp_at = None, None, None
        if comp_mode in ('clone', 'build', 'clone_clean'):
            tcomp = struct.unpack_from('<I', pl, tpl + 16 + 1388)[0]
            body = None
            # ★2026-10-21（修 #3，弹框根因）：**必须从伴随即数组里的"真起点"复制**。
            #   旧代码用 `pl.find(伴随键)` 再挑 >0x900000 的第一个 —— 实测那个位置
            #   （0x0F4B11B−0x20）根本不是伴随起点，复制出来的 476B 没有类 GUID
            #   ⇒ 插进去的东西不是合法伴随对象 ⇒ 读档弹框。
            #   正确来源：`companion_offsets()` 的 {键: 起点} 映射（官方起点 +0x20 就是该键）。
            comp_map = {k: o for o, k in self.companion_offsets(pl)}
            if comp_mode in ('clone', 'clone_clean') and tcomp in comp_map:
                _cofs = comp_map[tcomp]          # ⚠ 别用 o —— 那个是记录载荷偏移
                body = bytearray(pl[_cofs:_cofs + self.COMP_SIZE])
            elif comp_mode == 'build':
                # ★★新路线（2026-10-16）：**从零构造** —— 只保留本档"全档恒定"的类常量，
                #   变动字段一律 0，再写自身键(+0x20)与格(+0xC0/+0xC4)。
                #   依据：引擎**自己新建**的伴随对象里同样带着一堆临时指针（0x02CAxxxx…）
                #   ⇒ 加载器并不依赖这些变动字段；而"整份克隆"会带走模板的悬空指针 ⇒ 卡死。
                proto = self.companion_proto(pl)
                if proto is not None:
                    body = bytearray(proto)
            if body is not None:
                comp_key = self._free_key(pl, 0x02B00000)
                struct.pack_into('<I', body, 0x20, comp_key)       # 自身键
                struct.pack_into('<I', body, 0xC0, cx * 256)       # 格 X（×256）
                struct.pack_into('<I', body, 0xC4, cy * 256)       # 格 Y
                # ★★★★★ 2026-10-21（修 #8，弹框真因）：伴随对象的 **+0xC8 = 该栋的 Z（地形高度×416）**，
                #   必须与建筑记录 +168/+608/+1624 **完全相等**。硬证据（SAVE0029 逐个核对）：
                #     模板 ATESLA@(254,90)：记录 1248 / 伴随 1248 ✔
                #     原生 ATESLA@(95,130) ：记录  832 / 伴随  832 ✔
                #     原生 ATESLA@(203,159)：记录 1144 / 伴随 1144 ✔
                #     引擎黄金塔 SAVE4823  ：记录    0 / 伴随    0 ✔
                #     ⇒ 266 栋有伴随的建筑**无一例外**（Z=0/416/832/1248 四档全覆盖）。
                #   我们过去只写 +0xC0/+0xC4（x,y×256）**漏了 +0xC8** ⇒ 克隆模板伴随会带走
                #   **模板那栋的悬崖高度**：模板在 Z=3 的悬崖上 ⇒ 1248，而新楼落在地面 ⇒ 记录 0
                #   ⇒ 记录说"地面"、伴随说"第 3 层" ⇒ 读档校验不一致 ⇒ **弹框**。
                #   这解释了全部观测：#1~#7 全都建伴随（全弹）、#6 修阵营清单/#7 改属主都没碰它（仍弹）、
                #   而玩家验收过的小图成功档是**跨类型**路径（cross 分支把 4 个伴随指针清 0 ⇒ 根本没有伴随
                #   ⇒ 不存在这个不一致）⇒ 能进。⚠ 抄**刚写好的记录**而不是抄 z 参数，保证两侧永远相等。
                struct.pack_into('<I', body, 0xC8,
                                 struct.unpack_from('<I', rec, o + 168)[0])   # Z（×416，与记录同源）
                struct.pack_into('<I', rec, o + 1388, comp_key)    # 记录指向新伴随
                # ★伴随也有自己的实例 ID（+52），与建筑的 +20 **共用同一计数器** ⇒ 必须换新值，
                #   否则与模板伴随撞 ID（疑似 `comp_mode='clone'` "卡死" 的原因）。
                struct.pack_into('<I', body, 0x34,
                                 self._next_inst_id(pl, hs, extra=(new_id20,)))
                if comp_mode == 'clone_clean':
                    # ★折中路线（2026-10-16 傍晚）：**克隆模板伴随，但只清掉"悬空指针"** ——
                    #   「整份克隆」⇒ 带走模板的悬空内部指针 ⇒ 游戏卡死（A2/E1/E2）；
                    #   「只留类常量、其余清零」⇒ 新开载入即弹框（SR2/SR3/SR4，疑因清掉了必需的非指针字段）。
                    #   本路线：保留**全部非指针字段**（含类常量与实例态），只把
                    #   "值 >= 0x01000000（任何堆池）且**不是类常量**"的字段清 0。
                    consts = self.companion_const_offsets(pl)
                    for off in range(0, self.COMP_SIZE - 3, 4):
                        if off in consts or off in (0x20, 0xC0, 0xC4):
                            continue
                        if struct.unpack_from('<I', body, off)[0] >= 0x01000000:
                            struct.pack_into('<I', body, off, 0)
                comp_body = bytes(body)
                # 插到伴随对象数组的**末尾**（本档伴随实测是 476B 等距的连续数组）
                comps = self.companion_offsets(pl)
                comp_at = (comps[-1][0] + self.COMP_SIZE) if comps else \
                    (self._bld_tail(pl, hs) + self.BLD_SIZE)
        struct.pack_into('<I', pl, cnt_off, len(hs) + 1)
        ins = [(self._bld_tail(pl, hs), bytes(rec), '记录')]
        if comp_body is not None:
            ins.append((comp_at, comp_body, '伴随对象'))
        for cs, c, ent in lists:
            add = [new_key]
            if comp_key is not None and c > len(keys):
                # 该表同时收「对象键」（计数 > 建筑数）⇒ 伴随键也要登记
                add.append(comp_key)
            # ★★★登记 = **优先覆写表的"预留空位"（零增长）**（2026-10-16 定案）：
            #   表的 `count` 之后是一段**预留位**（能建母本实测：表0 有 4 个 0、表2 有 9 个 0，
            #   表1 没有 —— 它后面紧贴着别的数据）。
            #   引擎放置建筑时就是**原地覆写预留位 + 把 count +1/+2**（C1 实测：表0 28→30、
            #   表2 11→12，而**表尾的预留区字节完全没变** ⇒ 零增长）。我们过去一律插字节，
            #   ⚠ 后记（2026-10-16 17:25）：登记方式**不是**重载崩的原因（真因是 `+20` 实例 ID 重复）。
            #   `'insert'`（默认）与引擎写法一致，保持即可；`'inplace'` 会写坏别的结构（G1~G4 载入即弹框）。
            free = 0
            while free < 64:
                q = ent + 4 * (c + free)
                if q + 4 > len(pl) or struct.unpack_from('<I', pl, q)[0] != 0:
                    break
                free += 1
            struct.pack_into('<I', pl, cs, c + len(add))
            # ⚠⚠ **默认 'insert'（插字节）= 已知可用行为**。'inplace'（覆写预留位）虽然与引擎写法一致，
            #   但 2026-10-16 03:38 实测 **G1~G4 全部"载入即弹框"**（回归）⇒ 默认关闭，仅在显式实验时使用，
            #   见 docs/handoff-20261016-033817.md §3。
            if grow_mode == 'inplace' and free >= len(add):
                for j, k in enumerate(add):           # 零增长：原地覆写
                    struct.pack_into('<I', pl, ent + 4 * (c + j), k)
            else:
                for k in reversed(add):               # 插字节（默认）
                    ins.append((ent + 4 * c, struct.pack('<I', k),
                                '登记表@%#X' % cs))
        for pos, data, tag in sorted(ins, key=lambda t: -t[0]):
            pl[pos:pos] = data
        nk = bytes(pl).count(struct.pack('<I', new_key))
        # 预期出现处 = 记录+0（自身键） + 各登记目标 + 记录+208（自身键） + 记录+0x3A8（自引用）
        #   ★2026-10-21：多了 +0x3A8 这一处（黄金样本要求），所以基线从 1+len+1 变成 1+len+2
        if nk != 1 + len(lists) + 2:
            raise RuntimeError('自证失败：新键 %d 处 ≠ 1+%d+2' % (nk, len(lists)))
        # ★★★格子占用表（2026-10-16 定案）：引擎放建筑时会把新键写进**它占的每一格**
        #   （格偏移 = base + 348×(80cx+79cy)，键在格+0、格−8 写 1）。
        #   缺这张表 ⇒ 建筑**能显示但点不中/打不着**（鼠标拾取走格子，不走类型→art）。
        #   零增长：只改原地字段，不动长度。
        self._cellbase_memo = None
        cell_note = ''
        try:
            okc, note_c = self.occupy_cells_exact(type_name, cx, cy, new_key, tpl_key)
            cell_note = note_c if okc else ('⚠ 未写格子占用：' + note_c)
            if okc:
                nk2 = bytes(pl).count(struct.pack('<I', new_key))
                if nk2 <= nk:
                    raise RuntimeError('写格子占用后未观测到新键增加（%d → %d）' % (nk, nk2))
        except Exception as e:
            cell_note = '⚠ 写格子占用失败：%s' % e
        self._blds = None
        extra = ''
        if sig_mode_note_comp:
            extra = '；⚠ 跨类型已忽略"克隆伴随对象"（会卡死），伴随指针清 0'
        # ★★★★★ 电力计数器（2026-10-17）：新建筑必须计入**主人阵营**的发电/耗电，
        #   否则"不属于本局阵营"的新建筑会落在 (0,0) 的计数器上 ⇒ 0 ≥ 0「不缺电」
        #   ⇒ 电力建筑（如光棱塔 Powered=yes）**照常开火**（H1 苏俄/尤里塔的怪现象就是这个）。
        pw = self.power_of(type_name)
        if pw:
            try:
                eff = None
                for g2 in self._bld_group()[0]:
                    if struct.unpack_from('<I', self.plain, g2 + 16)[0] == new_key:
                        eff = struct.unpack_from('<I', self.plain,
                                                 g2 + 16 + self.BLD_OWNER_OFF)[0]
                        break
                if eff in set(self.house_keys().values()):
                    r2 = self.adjust_house_power(eff, max(pw, 0), max(-pw, 0))
                    if r2:
                        extra = '%s；主人电力 %s→%s' % (extra, r2[0], r2[1])
            except Exception as e:
                extra = '%s；⚠ 电力计数器未更新：%s' % (extra, e)
        owner_name = self.house_label(owner_key)
        if owner_name:
            extra = (extra + '；所属方 = %s' % owner_name) if extra else '；所属方 = %s' % owner_name
        if owner_list_note:
            extra += owner_list_note
        if n_polluted:
            extra += ('；已从全局表里剔除 %d 张「阵营建筑清单」（旧代码会把新键写进别的阵营的清单⇒弹框）'
                      % n_polluted)
        # ★★★★★ §171（2026-10-22，in-game 已验 M 档 GAPOWR / N2 档 ATESLA 光棱塔）：
        #   让新加的楼**自带一遍完整建造动画**。配方 `AC=18/BC=1/D0=1/10C=2/534=0`，
        #   进游戏播一遍后自动正常；`+0xD0` 若留 100 会播两遍（P/R 档实测）。
        #
        # ★★★★★ §195（2026-10-23，T42/T43 同基档对照）：默认改成 **`'fresh'`（刚放下）**。
        #   实测对照（同一份档、同一批克隆）：
        #     · `done`（AC=5/6E4=1）      → **无头**（引擎认为已建好，不再建炮塔子对象）
        #     · `18/1/1/2/0/0`（正在播）   → 引擎读档后**不会从头播**，也停在半路
        #     · **`AC=-1/534=1/6E4=0`（刚放下）** → **完整带棱镜、正常旋转** ← 唯一正确
        #   `buildup='fresh'` 写刚放下态；`buildup=True` 走老"正在播"配方；
        #   `buildup=False` 完全不碰建造态（保留模板原样）。
        anim_note = ''
        if buildup:
            try:
                nb_base = None
                for g2 in self._bld_group()[0]:
                    if struct.unpack_from('<I', self.plain, g2 + 16)[0] == new_key:
                        nb_base = g2 + 72        # 记录 = [16B CLSID][4B 键][对象体]，base = 对象体+52
                        break
                if nb_base is None:
                    anim_note = '；⚠ 没定位到新楼记录，未写建造动画'
                else:
                    rb = self.make_building_buildup(
                        nb_base, fresh=(buildup == 'fresh' or buildup == 'Fresh'))
                    anim_note = ('；已设为「%s」（%s）'
                                 % ('刚放下 · 读档后自己播一遍建造动画' if rb.get('fresh')
                                    else '进游戏播一遍建造动画',
                                    ' '.join('%s=%d' % (k, v) for k, v in
                                             sorted(rb['new'].items()))))
            except Exception as e:
                anim_note = '；⚠ 写建造动画失败：%s' % e
        # ★★★★★ §194（2026-10-23）：**补动画对象**（476B anim）。
        #   缺它 ⇒ 模型停在动画第 0 帧（光棱塔只剩底座），而且不参与"完整建筑"逻辑。
        #   ⚠ 默认 False：配方还在等 in-game 判定（T24_A 原型版 / T24_B 金模板版）。
        anim_key, anim_created = None, None
        if anim:
            try:
                nb = None
                for _b in self.buildings():
                    if struct.unpack_from('<I', self.plain, _b['base'] - 72 + 16)[0] == new_key:
                        nb = _b['base']
                        break
                if nb is None:
                    extra += '；⚠ 没定位到新楼记录，未补动画'
                else:
                    ra = self.add_building_anim(nb)
                    anim_key, anim_created = ra['key'], ra['created']
                    extra += '；%s' % ra['note']
            except Exception as e:
                extra += '；⚠ 补动画失败：%s' % e
        if cross_clean_note:
            extra += cross_clean_note
        if anim_note:
            extra += anim_note
        return {'mode': '直接放好', 'type': type_name, 'key': new_key,
                'cell': (cx, cy), 'lists': len(lists), 'companion': comp_key,
                'cross': cross, 'tpl_key': tpl_key, 'polluted_lists': n_polluted,
                'cells_note': cell_note, 'house': owner_key,
                'house_idx': owner_idx, 'house_name': owner_name,
                'buildup': bool(buildup), 'buildup_note': anim_note,
                'anim': bool(anim), 'anim_key': anim_key, 'anim_created': anim_created,
                'note': '(%d,%d) 应出现一栋 %s（%s%s）' % (
                    cx, cy, type_name, cell_note, extra)}

    # ------------------------------------------------------------------ #
    # 删除建筑（2026-10-16）—— 严格反向的 add_placed_building
    # ------------------------------------------------------------------ #
    BLD_REC_MIN = 1700          # 记录长度合理区间（实测 1860 / 1872）
    BLD_REC_MAX = 2000

    def remove_building(self, idx, drop_companion=False, clear_cells=True, orphan_cell='auto',
                        trim_lists=True):
        r"""从存档里**删掉第 idx 栋建筑**（0 基，顺序同 `_bld_group()`）。

        四件必做的事（与 `add_placed_building` 严格对称）：
          ① 删整条记录（长度 = 下一条记录起点 − 本条起点；**末栋用 `_bld_tail` 的真实组尾**
             —— 记录长度是类型特有的，不能按 1860 硬算）；
          ② 建筑组计数 −1；
          ③ 从各登记表剔掉该建筑的键（每张表各自 −1 计数）；
          ④ 清掉该建筑的全部格子占用（键清 0，并把格−8 / 格+0x1C 的标志位清掉）。
        ⑤ 伴随对象（0~4 个，键在建筑 `+1388..+1400`）二选一：
             · `drop_companion=True`  ⇒ **删掉伴随对象**并连登记一起剔 —— **⚠ 会弹框，别用！**
             · `drop_companion=False`（**默认**）⇒ **保留孤儿伴随**，但把它的格
               （`+0xC0/+0xC4`）搬到 `orphan_cell`（默认 `(0, 0)`）—— **✅ 实测可用**。

        ★★★★★ **2026-10-16 23:40 玩家实测定案（干净母本 `原存档.SAV`，单变量）**：
          · `A1 删末位空指部(删伴随)` ❌弹框 ；`A2 删中间电厂(删伴随)` ❌弹框 ；
            **`A3 删中间电厂(留孤儿伴随)` ✅能进**，但原地留一个"显示不正常"的建筑图像；
          · **`D1 把孤儿伴随的格搬到 (300,300)` ✅ 建筑彻底消失，完美**；
            **`D2 搬到 (0,0)` ✅ 同样完美**；
          · `C3 删伴随对象但**保留**它的登记表键` ❌**仍弹框** ⇒ 致命的是「**删对象本身**」，
            不是"剔表键"。
          ⇒ **结论：删伴随对象必然弹框（与被删建筑的位置无关，5 组数据点全中）；
            但"保留孤儿 + 把它的格搬走"既安全又视觉正确 ⇒ 已定为默认行为。**
          ⚠ 成因尚未完全解明：引擎自己卖建筑时**是删伴随的，但同时会新建 1 个 `c42d270e`
            (0x0E272DC4) 类对象**（两次样本都有：键 `136a2808` / `0f352050`，头布局与建筑记录同构、
            约 1909B、登记进表0/表1 + 12 个所属方记录）—— **疑为"替身对象"，少了它加载器不认**。
            要改成"引擎原教旨"就必须先能合成它。详见 `docs/卖建筑_引擎配方_20261016.md`。

        ★难点：**删字节会让后面全部偏移前移** ⇒ 每步都重新定位；多张表**从偏移大的往小的删**，
          且同一张表内**按位置倒序删**（否则表头偏移会漂）。
        返回 dict；任何一步对不上就抛异常 —— 抛异常时调用方不得保存，存档不会被写坏。
        """
        pl = self.plain
        hs, cnt_off = self._bld_group()
        n = len(hs)
        if not (0 <= idx < n):
            raise RuntimeError('建筑序号越界：idx=%d，本档共 %d 栋' % (idx, n))
        g = hs[idx]
        ln = (hs[idx + 1] - g) if idx + 1 < n else (self._bld_tail(pl, hs) - g)
        if not (self.BLD_REC_MIN <= ln <= self.BLD_REC_MAX):
            raise RuntimeError('第 %d 栋的记录长度 %d 不在合理区间 [%d,%d]，拒绝删除'
                               % (idx, ln, self.BLD_REC_MIN, self.BLD_REC_MAX))
        key = struct.unpack_from('<I', pl, g + 16)[0]
        tkey = struct.unpack_from('<I', pl, g + 16 + 1316)[0]
        px, py = struct.unpack_from('<II', pl, g + 16 + 600)
        cx, cy = (px - 128) // 256, (py - 128) // 256
        cks = [struct.unpack_from('<I', pl, g + 16 + off)[0]
               for off in (1388, 1392, 1396, 1400)]
        cks = [k for k in cks if k]
        owner_before = struct.unpack_from('<I', pl, g + 16 + self.BLD_OWNER_OFF)[0]
        tname = None
        for nm, k in sorted(self.building_types().items()):
            if k == tkey:
                tname = nm
                break
        out = {'mode': '删除建筑', 'idx': idx, 'type': tname, 'key': key,
               'cell': (cx, cy), 'rec_len': ln, 'companions': list(cks)}
        # ---------------- ① 删记录 + ② 组计数 −1 ----------------
        # 顺序很重要：先删字节再改计数，中间不能调用 _bld_group（它会校验计数==记录数）
        del pl[g:g + ln]
        struct.pack_into('<I', pl, cnt_off, n - 1)
        out['count'] = n - 1
        # ---------------- ④ 清格子占用（先做：它只读，不影响长度） ----------------
        # ★2026-10-18：优先走**与图尺寸无关**的格对象数组（cell_struct_slots）。
        #   老路径 `_cell_base()` 依赖 CELL_XK/YK=80/79，只对 80×80 小图成立；
        #   大任务图（203×295）上它返回 None，于是清格被**静默跳过** ——
        #   这就是"小图删建筑正常、大图一删就弹框"的根本原因。
        cells = 0
        if clear_cells:
            cells = self.clear_cell_slots(key)          # 4 个字段：+0xF0/+0xF8/+0x114/+0x138
        if clear_cells and not cells and tname:
            # 兜底：老路径（仅小图有效）
            self._cellbase_memo = None
            base = self._cell_base(pl)
            if base is not None:
                W, H = self.foundation_of(tname)
                for i in range(W):
                    for j in range(H):
                        p = self.cell_offset(base, cx + i, cy + j)
                        if not (8 <= p <= len(pl) - 4):
                            continue
                        if struct.unpack_from('<I', pl, p)[0] == key:
                            struct.pack_into('<I', pl, p, 0)
                            for fo in (self.CELL_FLAG_OFF, self.CELL_FLAG2_OFF):
                                q = p + fo
                                if 8 <= q <= len(pl) - 4:
                                    struct.pack_into(
                                        '<I', pl, q,
                                        struct.unpack_from('<I', pl, q)[0] & ~1)
                            cells += 1
        out['cells_cleared'] = cells
        self._cellbase_memo = None
        # ---------------- ⑤ 伴随对象：删（弹框！）或「搬走」（默认，实测可用） ----------------
        removed_comp, moved_comp = [], []
        # ★2026-10-18：默认 'auto' = 挪到**地图外**（尺寸 +50 格）。
        #   原来的 (0,0) 在地图角上：格子清干净后建筑本体已经没了，但伴随对象的画面还在，
        #   若玩家探索过那个角就会看到一栋"幽灵电厂"。挪出地图就彻底看不见。
        if orphan_cell == 'auto':
            _sz = self.map_size()
            orphan_cell = (_sz[0] + 50, _sz[1] + 50) if _sz else (400, 400)
        if cks and (drop_companion or orphan_cell is not None):
            for ck in list(cks):
                obj = self._find_comp_obj(pl, ck)
                if obj is None:
                    continue
                if drop_companion:
                    still = False
                    for gg in self._bld_group()[0]:
                        for off in (1388, 1392, 1396, 1400):
                            if struct.unpack_from('<I', pl, gg + 16 + off)[0] == ck:
                                still = True
                                break
                        if still:
                            break
                    if still:
                        continue                    # 还有别的建筑在用它 ⇒ 不能删
                    del pl[obj:obj + self.COMP_SIZE]
                    removed_comp.append(ck)
                else:
                    # ★★★ 2026-10-16 实测（D1/D2）：把孤儿伴随的格搬走 ⇒ 残影消失、载入正常
                    struct.pack_into('<II', pl, obj + self.COMP_CELLX_OFF,
                                     orphan_cell[0] * 256, orphan_cell[1] * 256)
                    moved_comp.append(ck)
        out['companions_removed'] = removed_comp
        out['companions_moved'] = moved_comp
        # ---------------- ③ 剔登记表里的键 ----------------
        drop = ({key} | set(removed_comp)) if trim_lists else set()
        alive = [struct.unpack_from('<I', pl, gg + 16)[0]
                 for gg in self._bld_group()[0]]
        lists = self._locate_key_lists(alive) if alive else []
        trimmed, found_any = 0, set()
        # 从偏移大的表往小的删（前面表的表头偏移不受影响）
        # ★每张表都要**各自独立**查找：同一个键在多张表里都登记着，
        #   剔完一张就把它从待删集合里丢掉 ⇒ 后面的表会漏剔（实测少剔 2 张 ⇒ 多出 8 字节）
        for (coff, cnt, eoff) in sorted(lists, key=lambda t: -t[0]):
            hits = [t for t in range(cnt)
                    if struct.unpack_from('<I', pl, eoff + 4 * t)[0] in drop]
            for t in sorted(hits, reverse=True):          # 表内按位置倒序删
                found_any.add(struct.unpack_from('<I', pl, eoff + 4 * t)[0])
                del pl[eoff + 4 * t:eoff + 4 * t + 4]
            if hits:
                struct.pack_into('<I', pl, coff, cnt - len(hits))
                trimmed += 1
        out['lists_trimmed'] = trimmed
        # ★2026-10-18：还有一类「单条键表」`[u32 count=1][key]`（计数就在键前 4 字节）。
        #   `_locate_key_lists` 的"≥8 个不同建筑键"门槛会把它整张滤掉 ⇒
        #   大图上就残留一处引用 ⇒ 载入弹框（小图的同类表有 12 条，能过门槛，所以小图不出事）。
        singles = 0
        for k in sorted(drop, reverse=True):
            pat = struct.pack('<I', k)
            i = pl.find(pat)
            while i >= 0:
                if i >= 4 and struct.unpack_from('<I', pl, i - 4)[0] == 1:
                    struct.pack_into('<I', pl, i - 4, 0)   # 计数 1 → 0
                    del pl[i:i + 4]                        # 再删掉那 4 字节条目
                    singles += 1
                    found_any.add(k)
                    break
                i = pl.find(pat, i + 1)
        out['single_tables_trimmed'] = singles
        # ---------------- ⑥ ★2026-10-27 §287 兜底扫尾：混合登记表 ----------------
        # 主逻辑按「表里含 ≥8 个不同建筑键」筛表，而**混合表**（本档 228 条）里
        # 可能只有一两个建筑键 ⇒ 整张被滤掉 ⇒ 该键残留 ⇒ 载入时加载器的键归并断言失败
        # ⇒ 玩家看到的就是"删完建筑进游戏弹框"（INT_DIVIDE_BY_ZERO 是 MSVC 的断言陷阱）。
        # 这里对每个待删键，把 `_key_tables()` 认得出的表（段首计数==段长）全过一遍，剔干净。
        swept = 0
        for _round in range(400):
            victim = None
            for k in sorted(drop, reverse=True):
                for tb in self._key_tables(k):
                    ent, cpos, cnt_n = tb['first'], tb['count_pos'], tb['n']
                    for i in range(cnt_n):
                        if struct.unpack_from('<I', pl, ent + 4 * i)[0] == k:
                            victim = (ent + 4 * i, cpos, k)
                            break
                    if victim:
                        break
                if victim:
                    break
            if not victim:
                break
            pos, cpos, k = victim
            struct.pack_into('<I', pl, cpos,
                             struct.unpack_from('<I', pl, cpos)[0] - 1)
            del pl[pos:pos + 4]
            swept += 1
            found_any.add(k)
        out['stray_swept'] = swept
        # 扫尾后再算"残留"：任何表里还能找到的键 = 载入必炸，直接报出来
        leftover = {}
        for k in sorted(drop):
            blob = bytes(pl)
            pat = struct.pack('<I', k)
            hits, i = [], blob.find(pat)
            while i >= 0 and len(hits) < 4:
                hits.append(i)
                i = blob.find(pat, i + 1)
            if hits:
                leftover[k] = hits
        # ★§288：扫尾可能仍有漏网（新表型 / 旧档）⇒ 用 sweep_key_refs 再剔一遍
        if leftover:
            _n2 = self.sweep_key_refs(set(leftover))
            if _n2:
                out['swept2'] = _n2
                leftover = self.leftover_refs(drop)
        if leftover:
            # 清不掉就别让它悄悄存盘（进游戏必弹框）
            self._unsafe_leftover = dict(leftover)
        out['leftover_refs'] = leftover
        out['untabled_keys'] = sorted(drop - found_any)   # 任何表里都没找到的键（正常应为空）
        self._blds = None
        self.pending_bld = True
        # ★电力计数器：把这栋的 Power 从原主人的发电/耗电里减掉（2026-10-17）
        pw = self.power_of(tname)
        if pw:
            try:
                r3 = self.adjust_house_power(owner_before, -max(pw, 0), -max(-pw, 0))
                if r3:
                    out['power'] = r3
            except Exception as e:
                out['power_error'] = str(e)
        out['note'] = ('已删除第 %d 栋 %s @(%d,%d)：记录 %dB、格 %d 个、'
                       '伴随删 %d / 搬走 %d / 留 %d、登记表 %d 张' % (
                           idx, tname, cx, cy, ln, cells,
                           len(removed_comp), len(moved_comp),
                           len(cks) - len(removed_comp) - len(moved_comp), trimmed))
        return out

    # ------------------------------------------------------------------ #
    # 武器 / 弹头 类型对象（★2026-10-28 新发现，只读）
    #
    # 结论先写在这儿：**武器/抛射体/弹头不是"每个单位一份"，而是存档里一张
    # 全局类型表**。单位实例只存"我是哪个型号"，型号决定用哪把武器。
    # 但——这张表**确实在存档明文里**，而且是完整数值（不只是名字）。
    #
    # 实测布局（`X` = 对象第一份名字缓冲的起点，本档 SAVE0665 逐项验过）：
    #   X + 0x00   ID    串（64 字节定长，null 补齐）
    #   X + 0x40   Name  串（同一份，64 字节定长）—— **这个"双名 +0x40"是
    #              存档里所有 Type 类（武器/弹头/抛射体/动画/建筑…）的通用特征**
    #   X + 0x80   Damage   u32
    #   X + 0x84   （未定，与 Damage 无关，本档 121/50/113… 随武器变）
    #   X + 0x8C   ROF      u32（帧）
    #   X + 0x90   Range    u32（**lepton**，1 格 = 256；Range=5.5 ⇒ 1408）
    #
    # 弹头（WarheadType）：
    #   X + 0x7C   Verses   **11 个 double**（不是 float！），对应 11 种装甲，
    #              顺序见 `ARMOR_ORDER`。实测 PrismWarhead / SA / AP 三个全中。
    #
    # 验证记录（2026-10-28，`E:/YURI/SAVE0665_修改.SAV`，199 把武器）：
    #   与 rulesmd.ini 逐项比对 ⇒ 184 把完全一致（10 处 Range 小数舍入属正常），
    #   **15 处真不一致**（如 SonicZap Damage rules 100 / 存档 4、
    #   Maverick3 1100/750、ChaosAttack ROF 30/45、IvanBomber 600/400）
    #   ⇒ 存档存的是**存盘当时的有效值**（地图 INI / MIX 里的规则覆盖也会体现在这儿），
    #     不是"照抄散装 rulesmd.ini"。
    #
    # ⚠ 还没验的事：**读档时引擎到底用存档这份、还是重新读 rules**。
    #   要坐实得进游戏实测（改 PrismShot Damage 120→999，读档打一枪看是否秒杀）。
    # ------------------------------------------------------------------ #
    ARMOR_ORDER = ('none', 'flak', 'plate', 'light', 'medium', 'heavy',
                   'wood', 'steel', 'concrete', 'special1', 'special2')
    WEAPON_DMG_OFF = 0x80
    WEAPON_ROF_OFF = 0x8C
    WEAPON_RANGE_OFF = 0x90
    WARHEAD_VERSES_OFF = 0x7C
    VERSES_COUNT = 11

    def _type_objects(self):
        """扫出所有"双名缓冲"的 Type 对象 → `[(偏移, 名字)]`（缓存到 `_typeobjs_memo`）。

        特征：`X` 起是可打印 ASCII 名（2~31 字节、null 结尾），且 `X+0x40` 处是**同一份**。
        这个特征覆盖存档里**全部** Type 类（武器/弹头/抛射体/动画/建筑/步兵/阵营…），
        所以还要靠 rules 的名字表再分类（见 `weapon_objects` / `warhead_objects`）。

        ★首字符必须允许**数字** —— `105mm` `120mm` `2Inch` 这类武器名就是以数字开头的，
          第一版只认 `A-z` 漏了 36 把（自检 §295 逮到）。

        ★性能：逐字节 Python 循环扫 26.7MB 要 **4.2 秒**；改成 `re.finditer` 走 C 引擎
          只要 **203ms**（同一份档、结果逐项一致，实测 1196 个对象）。
        """
        memo = getattr(self, '_typeobjs_memo', None)
        if memo is not None:
            return memo
        pl = bytes(self.plain)
        rx = getattr(SaveDoc, '_RX_TYPE_NAME', None)
        if rx is None:
            rx = re.compile(rb'[0-9A-Za-z_.\-]{2,31}\x00')
            SaveDoc._RX_TYPE_NAME = rx
        out = []
        for m in rx.finditer(pl):
            i = m.start()
            ln = m.end() - 1 - i
            if ln < 2:
                continue
            if pl[i + 0x40:i + 0x40 + ln] == pl[i:i + ln] and pl[i + 0x40 + ln] == 0:
                out.append((i, pl[i:i + ln].decode('ascii', 'replace')))
        self._typeobjs_memo = out
        return out

    @classmethod
    def _rules_list(cls, r, sec):
        """`[Warheads] 1=SA 2=…` 这类"总表" → 名字集合。"""
        out = set()
        for k, v in (r.get(sec) or {}).items():
            if k.isdigit() and v:
                out.add(v.strip())
        return out

    @classmethod
    def _names_by_key(cls, r, keys):
        """所有节里 `keys` 这些键的**取值**集合（如 Primary/Secondary → 武器名）。"""
        out = set()
        for _sec, kv in r.items():
            for k in keys:
                v = kv.get(k)
                if v:
                    out.add(v.strip())
        return out

    def _weapon_name_set(self):
        r = self._rules_all()
        if not r:
            return None                     # 没 rules ⇒ 交给结构特征兜底
        names = {s for s, kv in r.items() if 'Damage' in kv and 'ROF' in kv}
        names |= self._rules_list(r, 'WeaponTypes')
        names |= self._names_by_key(r, ('Primary', 'Secondary',
                                        'ElitePrimary', 'EliteSecondary'))
        return names

    def _warhead_name_set(self):
        r = self._rules_all()
        if not r:
            return None
        names = {s for s, kv in r.items() if 'Verses' in kv}
        names |= self._rules_list(r, 'Warheads')
        names |= self._names_by_key(r, ('Warhead',))
        return names

    def weapon_objects(self):
        """→ `[{'name','off','damage','rof','range','range_cells'}]`（只读）。

        名字以 rules 为准（`Damage`+`ROF` 的节 / `Primary` 等键的取值），
        存档里按名字找对象读数值；没有 rules 时退回结构特征
        （`+0x8C` ROF ∈ (0,2000]、`+0x90` Range ≤ RANGE_MAX_L、`+0x80` Damage ≤ 100000）。

        ★2026-10-28（玩家把射程改成 90 格后这把武器**从列表里消失**了，才发现这里还写死
          `0x4000`（64 格）—— 那是旧护栏。改值上限放宽到 255 格后，扫描过滤必须跟着放宽，
          否则"改完就看不见"，而且 `_find_weapon` 会报"存档里没有这把武器"。
        
        """
        pl = self.plain
        want = self._weapon_name_set()
        out = []
        for off, nm in self._type_objects():
            try:
                dmg = struct.unpack_from('<I', pl, off + self.WEAPON_DMG_OFF)[0]
                rof = struct.unpack_from('<I', pl, off + self.WEAPON_ROF_OFF)[0]
                rng = struct.unpack_from('<I', pl, off + self.WEAPON_RANGE_OFF)[0]
            except Exception:
                continue
            if not (0 < rof <= 2000 and dmg <= 100000
                    and 0 < rng <= self.RANGE_MAX_L):
                continue
            if want is not None:
                if nm not in want:
                    continue
            elif rng % 32:                  # 无 rules 时：至少要是 1/8 格
                continue
            out.append({'name': nm, 'off': off, 'damage': dmg, 'rof': rof,
                        'range': rng, 'range_cells': rng / 256.0})
        return out

    def warhead_objects(self):
        """→ `[{'name','off','verses':[11 个系数]}]`（只读）。

        ★必须靠 rules 的名字表定位 —— 纯结构特征（11 个 double）会把**阵营对象**
          （Americans/Alliance/…）误判成弹头，第一版就踩了这个坑（自检 §295）。
          没有 rules 时返回空列表（宁可没有，也不给错的）。
        """
        pl = self.plain
        want = self._warhead_name_set()
        if not want:
            return []
        out = []
        for off, nm in self._type_objects():
            if nm not in want:
                continue
            try:
                vs = struct.unpack_from('<%dd' % self.VERSES_COUNT,
                                        pl, off + self.WARHEAD_VERSES_OFF)
            except Exception:
                continue
            if all(-0.01 <= v <= 10.0 for v in vs) and sum(1 for v in vs if v >= 0.5) >= 6:
                out.append({'name': nm, 'off': off, 'verses': list(vs)})
        # 同名可能命中两个对象（实测 `Special` 就是 2 个）⇒ 只留第一份，避免界面重复
        seen, uniq = set(), []
        for x in out:
            if x['name'] in seen:
                continue
            seen.add(x['name'])
            uniq.append(x)
        return uniq

    def weapon_warhead_map(self):
        """`{武器名: 弹头名}` —— 从 rules 反查（武器对象里弹头是**指针**，不是名字）。"""
        memo = getattr(self, '_wpnwh_memo', None)
        if memo is not None:
            return memo
        out = {}
        for nm, kv in (self._rules_all() or {}).items():
            wh = kv.get('Warhead')
            if wh:
                out[nm] = wh.strip()
        self._wpnwh_memo = out
        return out

    # ------------------------------------------------------------------ #
    # 写：改武器 / 弹头数值（★2026-10-28）
    #
    # ★★为什么这条路可行（玩家 2026-10-28 亲口确认）：
    #    **改 rulesmd.ini 不会影响已存盘存档里的武器效果** ⇒ 读档时引擎用的是
    #    存档里这份类型表，不是重新读 ini。所以改这里的定长字段是**真正生效**的，
    #    而且只影响这一份存档（不动 rules、不影响别的档、不影响联机同步的其他人）。
    #
    # ★安全性：Damage/ROF/Range 都是**定长 u32**，Verses 是**定长 double**
    #   ⇒ 只改值、不改长度 ⇒ 明文偏移一个都不动 ⇒ 不需要 `invalidate_caches`，
    #   也不会碰"残留键/块布局"那些雷。这是本项目里最安全的一类写入。
    # ------------------------------------------------------------------ #
    # ★2026-10-28（玩家问「射程上限凭什么是 64 格」）：**这是我设的护栏，不是引擎限制**。
    #   实测游戏自带数据的真实分布：
    #     · 本档 213 把武器：最大 25 格（HornetLauncher / DredLauncher），中位数 6 格
    #     · rulesmd.ini 全部 328 个 Range=：最大 40 格（EMPulseWeapon）
    #     · rulesmd.ini 全部 576 个 Sight=：最大 20 格（DREDA）
    #   字段本身是 u32（lepton），塞多大都不会溢出；真正的边界是"地图只有 200×200"，
    #   所以上限放到 **255 格**（超过地图对角线，再多没意义），并保留 1/8 格精度。
    #   超过 40 格会给一条**软警告**（不拦），因为能不能开火还要看单位 Sight。
    RANGE_MIN_L = 32           # 1/8 格
    RANGE_MAX_L = 255 * 256    # 255 格
    RANGE_TYPICAL_L = 40 * 256 # 游戏自带数据的最大值（超过就提醒一句）
    WEAPON_FIELDS = {
        # 键名       偏移                    下限          上限            单位
        'damage': (WEAPON_DMG_OFF, 0, 100000, '点'),
        'rof':    (WEAPON_ROF_OFF, 1, 2000, '帧（**越小越快**）'),
        'range':  (WEAPON_RANGE_OFF, RANGE_MIN_L, RANGE_MAX_L, '格'),
    }

    def _find_weapon(self, name):
        """按名字找**唯一**一个武器对象；找不到/重名都报清楚。"""
        n = str(name).strip()
        hit = [x for x in self.weapon_objects() if x['name'] == n]
        if not hit:
            cands = [x['name'] for x in self.weapon_objects()
                     if n.lower() in x['name'].lower()][:8]
            raise KeyError('存档里没有武器「%s」%s'
                           % (n, ('；相近的有：' + '、'.join(cands)) if cands else ''))
        if len(hit) > 1:
            raise KeyError('武器「%s」在存档里有 %d 个对象，不确定改哪个' % (n, len(hit)))
        return hit[0]

    def set_weapon_field(self, name, field, value):
        """改某把武器的 `damage` / `rof` / `range`。**定长写，明文长度不变。**

        `range` 传**格**（如 5.5），内部换成 lepton（×256，且必须是 1/8 格的整数倍）。
        → `{'name','field','old','new','off'}`；越界抛 `ValueError`。
        """
        f = str(field).strip().lower()
        if f not in self.WEAPON_FIELDS:
            raise ValueError('字段只能是 %s（收到 %r）'
                             % ('/'.join(self.WEAPON_FIELDS), field))
        off_delta, lo, hi, unit = self.WEAPON_FIELDS[f]
        w = self._find_weapon(name)

        if f == 'range':
            try:
                cells = float(value)
            except Exception:
                raise ValueError('range 要传格数（如 5.5），收到 %r' % (value,))
            raw = int(round(cells * 256))
            if raw % 32:
                raise ValueError('range 精度只到 1/8 格（0.125），收到 %r' % (value,))
        else:
            try:
                raw = int(float(value))
            except Exception:
                raise ValueError('%s 要传整数，收到 %r' % (f, value))
        if not (lo <= raw <= hi):
            if f == 'range':
                raise ValueError('射程超出范围 %g ~ %g 格（收到 %g 格）。'
                                 '字段本身放得下，但地图只有 200×200，再远没有意义'
                                 % (lo / 256.0, hi / 256.0, raw / 256.0))
            raise ValueError('%s 超出范围 %s..%s（%s），收到 %s'
                             % (f, lo, hi, unit, raw))

        off = w['off'] + off_delta
        old = struct.unpack_from('<I', self.plain, off)[0]
        # 软警告（不拦）：射程超过游戏自带的最大值
        warn = ''
        if f == 'range' and raw > self.RANGE_TYPICAL_L:
            warn = ('射程 %g 格超出游戏自带的最大值（%g 格）；实际能不能开火还要看'
                    '单位的 Sight（全游戏最大 20 格），太远可能"看得见打不着"或干脆不索敌'
                    % (raw / 256.0, self.RANGE_TYPICAL_L / 256.0))
        if old == raw:
            return {'name': w['name'], 'field': f, 'old': old, 'new': raw,
                    'off': off, 'changed': False, 'warn': warn}
        struct.pack_into('<I', self.plain, off, raw)
        return {'name': w['name'], 'field': f, 'old': old, 'new': raw,
                'off': off, 'changed': True, 'warn': warn}

    def set_warhead_verse(self, name, armor, percent):
        """改某个弹头对某类装甲的伤害系数。**定长写（double），明文长度不变。**

        `armor` 传 1..11 的序号，或 `ARMOR_ORDER` 里的名字（none/flak/…/special2）。
        `percent` 传百分比（100 = 100%，可以是 200、0、12.5…）。
        → `{'name','armor','index','old','new','off'}`。
        """
        n = str(name).strip()
        hit = [x for x in self.warhead_objects() if x['name'] == n]
        if not hit:
            cands = [x['name'] for x in self.warhead_objects()
                     if n.lower() in x['name'].lower()][:8]
            raise KeyError('存档里没有弹头「%s」%s'
                           % (n, ('；相近的有：' + '、'.join(cands)) if cands else ''))
        wh = hit[0]
        # 装甲：序号（1 基）或名字
        idx = None
        s = str(armor).strip()
        if s.isdigit():
            i = int(s)
            if not (1 <= i <= self.VERSES_COUNT):
                raise ValueError('装甲序号只能是 1..%d（收到 %d）'
                                 % (self.VERSES_COUNT, i))
            idx = i - 1
        else:
            low = s.lower()
            if low not in self.ARMOR_ORDER:
                raise ValueError('装甲名只能是 %s（收到 %r）'
                                 % ('/'.join(self.ARMOR_ORDER), armor))
            idx = self.ARMOR_ORDER.index(low)
        try:
            pct = float(percent)
        except Exception:
            raise ValueError('百分比要传数字（100 = 100%%），收到 %r' % (percent,))
        if not (0 <= pct <= 1000):
            raise ValueError('百分比超出 0..1000（收到 %g）' % pct)
        new = pct / 100.0
        off = wh['off'] + self.WARHEAD_VERSES_OFF + 8 * idx
        old = struct.unpack_from('<d', self.plain, off)[0]
        if abs(old - new) < 1e-12:
            return {'name': wh['name'], 'armor': self.ARMOR_ORDER[idx], 'index': idx + 1,
                    'old': old, 'new': new, 'off': off, 'changed': False}
        struct.pack_into('<d', self.plain, off, new)
        return {'name': wh['name'], 'armor': self.ARMOR_ORDER[idx], 'index': idx + 1,
                'old': old, 'new': new, 'off': off, 'changed': True}

    def map_tile_size(self):
        """★2026-10-18 新增：**真正的地图尺寸**（与 FA2「地图参数」的 宽度/高度 同口径）。

        为什么不用 `0x348E`：那组 (W,H) 在自制/任务图上并不等于 `[Map] Size`
        （实测 180×170 的图它写的是 203×295，玩家用 FA2 核对过）。

        反推依据 —— 格对象数组（348B 定长槽）的索引规律：
            idx = W·(x + y) − y − W²
        其中 x,y 是**菱形坐标**（0 .. W+H-2）。于是
            W = Δ(idx + y) / Δ(x + y)          ← 取 (x+y) 最小 / 最大的两个格样本
            H = (最大格坐标 + 1) − W
        校验：槽数 ≈ 2·W·H − H（两个实测档：80×80→12720、180×170→61030，误差 0）。
        返回 (W, H)；格表缺失或反推不自洽返回 None。
        """
        pl = self.plain
        memo = getattr(self, '_tile_size_memo', None)
        if memo and memo[0] == len(pl):
            return memo[1]
        self._tile_size_memo = (len(pl), None)
        first, n = self.cell_struct_array()
        if first is None:
            return None
        lo = hi = None
        mx = 0
        for i in range(n):                              # 全扫（61030 槽 ≈0.15s，已缓存）
            x, y = struct.unpack_from('<HH', pl, first + i * self.CELL_STRUCT_STRIDE
                                      + self.CELL_STRUCT_XY)
            if x > mx:
                mx = x
            if y > mx:
                mx = y
            t = x + y
            if lo is None or t < lo[0]:
                lo = (t, i, y)
            if hi is None or t > hi[0]:
                hi = (t, i, y)
        if lo is None or hi is None or hi[0] == lo[0]:
            return None
        w = ((hi[1] + hi[2]) - (lo[1] + lo[2])) / float(hi[0] - lo[0])
        if abs(w - round(w)) > 0.02:
            return None
        w = int(round(w))
        h = (mx + 1) - w
        if not (1 <= w <= self.MAP_TILE_MAX and 1 <= h <= self.MAP_TILE_MAX):
            return None                      # >200×200 ⇒ 视为读错（见 MAP_TILE_MAX 注释）
        expect = 2 * w * h - h
        if abs(expect - n) > max(16, n // 100):
            return None
        self._tile_size_memo = (len(pl), (w, h))
        return (w, h)

    def map_size(self):
        """★存档自带的**地图尺寸**（2026-10-15 定案）—— 与建筑存活无关的稳定锚点。

        偏移 0x348E 起是一张「(W, H) 小端 u16 对」表（实测重复 8 次）：
          · E:\\YURI 测试档：80 80 80 80 …              → (80,80)
          · D:\\233 大任务档：200 200 …                  → (200,200)
          · SAVE03F9/2E1F/4CA2/5C7B/6811/69D1：260 179 … → (260,179)
        交叉验证：260×179 的档里建筑最大格 (338,357)，而菱形坐标上限 = 2W-1/2H-1 = 519/357
        ⇒ 恰好贴边，判据成立（24 档全自洽）。
        返回 (W, H)；读不出/不可信返回 None。
        """
        pl = self.plain
        try:
            w, h = (struct.unpack_from('<HH', pl, self.MAPSIZE_OFF))
        except Exception:
            return None
        if not (1 <= w <= self.MAP_TILE_MAX and 1 <= h <= self.MAP_TILE_MAX):
            # >200×200 ⇒ 按玩家经验判定为**读错**（自制任务图实测 180×170 这里却写 203×295）
            return None
        # 该表会重复出现 ⇒ 再验一组，避免偶合
        try:
            w2, h2 = struct.unpack_from('<HH', pl, self.MAPSIZE_OFF + 4)
        except Exception:
            return None
        if (w2, h2) != (w, h):
            return None
        return (w, h)

    def coord_limit(self):
        """坐标上限（菱形系）；取几种口径里**最宽松**的那个，绝不因改口径而变小。"""
        vals = []
        t = self.map_tile_size()
        if t:
            vals += [t[0] + t[1] + 8, 2 * t[0], 2 * t[1]]
        s = self.map_size()
        if s:
            vals += [2 * s[0], 2 * s[1]]
        if vals:
            m = max(vals)
            return (m, m)
        return (self.MAXPIX // 256, self.MAXPIX // 256)

    def map_match(self, map_path):
        """「地图 ↔ 存档」匹配判定 —— ★**以不变量为准，不看建筑存活**（2026-10-15 用户要求）。

        用户指出：按建筑匹配不科学（后期档建筑多被摧毁 ⇒ 假不匹配）。
        **不会变的**是：地图尺寸、地图范围。故判据：
          ① 主判据（硬）：地图 `[Map] Size` 的 (W,H) == **存档自带尺寸** `map_size()`
             （「80×80 的图，存档也一定是 80×80」）；
          ② 辅判据（硬，当 ① 读不到存档尺寸时才当主判据）：存档建筑全部落在
             地图坐标范围 0..2W-1 / 0..2H-1 内（越界 ⇒ 拿错图）；
          ③ 建筑重合率：**只作提示**（后期档大量建筑被毁属正常，绝不因此拒绝）。
        返回 {'ok', 'msg', 'map_size', 'save_size', 'map_n', 'save_n', 'hit',
              'rate_ms', 'rate_in', 'reason'}
        """
        from map_ini import MapIni
        m = MapIni(map_path) if isinstance(map_path, str) else map_path
        mw, mh = m.map_size()
        # ★2026-10-18：优先用**格子表反推**的真实尺寸（与 [Map] Size 同口径）；
        #   0x348E 那组在自制/任务图上不是这个口径（180×170 的图它写 203×295）。
        sw, sh = (self.map_tile_size() or self.map_size() or (None, None))
        ms = m.structures()
        mcells = {(s['x'], s['y']) for s in ms}
        blds = self.buildings()
        lx, ly = 2 * mw, 2 * mh
        out_of_map = [b for b in blds if not (0 <= b['x'] < lx and 0 <= b['y'] < ly)]
        rate_in = 1.0 - len(out_of_map) / float(len(blds)) if blds else 1.0
        hit = len(mcells & {(b['x'], b['y']) for b in blds})
        rate_ms = hit / float(len(mcells)) if mcells else 0.0

        info = ('地图 %s = %d×%d ｜ 存档自带尺寸 = %s ｜ 建筑重合 %d/%d（%.0f%%，仅供参考）'
                % (os.path.basename(getattr(m, 'path', '?')), mw, mh,
                   ('%d×%d' % (sw, sh)) if sw else '未读出',
                   hit, len(mcells), rate_ms * 100))

        if sw is not None:
            if (sw, sh) == (mw, mh):
                return {'ok': True, 'reason': 'size', 'map_size': (mw, mh),
                        'save_size': (sw, sh), 'map_n': len(mcells),
                        'save_n': len(blds), 'hit': hit, 'rate_ms': rate_ms,
                        'rate_in': rate_in,
                        'msg': '匹配（尺寸一致）：%s' % info}
            # ★ 2026-10-17 20:3x 玩家实测纠正：**尺寸只是辅助判据，不是铁证**。
            #   `E:\YURI\SAVE0029.SAV`（任务「救出超时空」）用 all01umd.map（[Map] Size = 180×170）
            #   被判"不是同一张图"，但**建筑重合 2024/2056 = 98%**，玩家确认就是这一张。
            #   原因：存档 `0x348E` 那组 (W,H) 在大任务/自制地图档里未必等于地图 INI 的 Size
            #   （203×295 这种值明显不是 "[Map] Size" 的口径）。
            #   ⇒ 判据改为：尺寸一致 **或** 建筑高度重合 **或** 建筑全在图上 ⇒ 认同一张图；
            #     三者都不满足才给"疑似不匹配"，而且**只是提示**（GUI 不再拦人，见 mv_load）。
            if hit >= 20 and rate_ms >= 0.85:
                return {'ok': True, 'reason': 'overlap', 'map_size': (mw, mh),
                        'save_size': (sw, sh), 'map_n': len(mcells),
                        'save_n': len(blds), 'hit': hit, 'rate_ms': rate_ms,
                        'rate_in': rate_in,
                        'msg': ('匹配（建筑重合 %.0f%%，尺寸字段不同：地图 %d×%d / 存档 %d×%d）：%s'
                                % (rate_ms * 100, mw, mh, sw, sh, info))}
            if rate_in >= 0.98 and (rate_ms >= 0.4 or hit >= 3):
                return {'ok': True, 'reason': 'range', 'map_size': (mw, mh),
                        'save_size': (sw, sh), 'map_n': len(mcells),
                        'save_n': len(blds), 'hit': hit, 'rate_ms': rate_ms,
                        'rate_in': rate_in,
                        'msg': ('匹配（存档建筑 %.0f%% 落在图内，尺寸字段不同）：%s'
                                % (rate_in * 100, info))}
            return {'ok': False, 'reason': 'size', 'map_size': (mw, mh),
                    'save_size': (sw, sh), 'map_n': len(mcells),
                    'save_n': len(blds), 'hit': hit, 'rate_ms': rate_ms,
                    'rate_in': rate_in,
                    'msg': ('疑似不是同一张图（地图尺寸 %d×%d，存档尺寸字段 %d×%d，'
                            '建筑重合仅 %.0f%%，落在图内 %.0f%%，两张都低）——仅供参考，不阻止使用。\n%s'
                            % (mw, mh, sw, sh, rate_ms * 100, rate_in * 100, info))}
        # 读不到存档尺寸 ⇒ 退化为"范围包含 + 建筑率"
        ok = bool(blds) and rate_in >= 0.98 and (rate_ms >= 0.4 or hit >= 3)
        return {'ok': ok, 'reason': 'range', 'map_size': (mw, mh),
                'save_size': None, 'map_n': len(mcells), 'save_n': len(blds),
                'hit': hit, 'rate_ms': rate_ms, 'rate_in': rate_in,
                'msg': ('%s：未能读出存档自带尺寸，改用「建筑是否在图上」判定（存档建筑在图上 %.0f%%）。\n%s'
                        % ('匹配' if ok else '不匹配', rate_in * 100, info))}

    def toggle_trigger(self, rec, on):
        """启用 / 禁用触发 —— **只写「触发类 GUID 前 8 字节」那 1 个字节**。

        ★ 实测判据（2026-10-13）：
          · 该字节 = 1 → 读档后触发**执行**（旧图 6692 实测"胜利"）；
          · 该字节 = 0 → 同一档触发**不执行**（6691 实测"不胜利"；外挂实验 T1 同）；
          · 两档/三档之间**只差这 1 个字节**，其它字段（含 `+123`、事件、行为）完全一致。
        ⚠ **不要动 `+123`**（那只是地图级定义的副本，改它不影响行为，实测无效）。
        ⚠ 定位不到（该处不是 0/1）时**拒绝写入** —— 说明记录布局与本工具预期不同，
          宁可不给，也不乱写。
        """
        if rec.kind != '触发':
            raise RuntimeError('只有「触发」记录支持启用 / 禁用')
        off = self._trig_flag_off(rec)
        if off is None:
            raise RuntimeError(
                '这条触发记录的启用位**未定位到**（记录里找不到触发类 GUID，\n'
                '或 GUID 前 8 字节不是 0/1）—— 记录布局可能与本工具预期不同。\n'
                '为避免改坏数据，已拒绝写入。\n\n'
                '可先用「事件」页把它的条件改成永不满足（等效禁用），\n'
                '或在地编（FA2）里改启用状态后重开新局。')
        self.plain[rec.off + off] = 1 if on else 0
        rec.enabled = bool(on)
        rec.note = '启用位@+%d = %d' % (off, self.plain[rec.off + off])
        rec.flag_off = rec.off + off
        self.pending_triggers[rec.sid] = bool(on)

    # ---------- 新写触发（2026-10-14 五轮实验 + V1/V2/V4 游戏内实测通过）----------
    def can_create_trigger(self):
        """该档是否支持「新写触发」：对象流须为 [u32 计数][定长对象] 布局
        （触发实例 200B / 标签 184B，事件对象尺寸可实测）。不满足则诚实拒绝。"""
        try:
            self._newtrig_groups()
            return True
        except Exception:
            return False

    def _pick_trigger_template(self, grps):
        """挑"最干净"的实例对象当克隆骨架，返回 (实例帧偏移, 标签帧偏移)。

        ★2026-10-20（玩家质疑"是不是拿第一个触发当模板"）：
          实例对象里 `+40/+52/+80..+116/+180` 是**运行期指针**，历史实现克隆时**不清零**
          （只有事件/行为对象会 blank_runtime）。若模板恰好是"活的"触发，这些指针会被整片抄过去。
          引擎读档会重算它们（已游戏内验收的"触发法"走的也是这条路），但没必要留着。
          ⇒ 优先挑 **没有 TagNode 的实例**（引擎已回收 / 从未武装 ⇒ 这些字段多为 0），
            实在没有才退回组内第一个。
        """
        pl = self.plain
        try:
            m = self._instance_maps()
            tobjs = self._tag_objs_by_key()
        except Exception:
            return grps['trig'][0], grps['tag'][0]
        cand = []
        for k, off in sorted(m['inst'].items(), key=lambda kv: kv[1]):
            tk = m['tag_of_inst'].get(k)
            tobj = tobjs.get(tk)
            live = m['node_of_inst'].get(k) is not None
            cand.append((live, off, tobj))
        if not cand:
            return grps['trig'][0], grps['tag'][0]
        # ★打分：实例 +72..+108 是**运行期指针数组**（长度随触发的行为链变化），
        #   克隆时会整片抄过去 ⇒ 挑"这片指针最少"的那条当骨架（实测有全 0 的）。
        def junk(off):
            return sum(1 for o in range(64, 120, 4)
                       if int.from_bytes(pl[off + o:off + o + 4], 'little'))
        cand.sort(key=lambda c: (junk(c[1]), c[0], c[1]))
        pick = cand[0]
        return pick[1], (pick[2] if pick[2] is not None else grps['tag'][0])

    def _newtrig_groups(self, ev_size=None):
        """定位并校验 触发实例/标签 两组（TagNode/CellTag 为可选）；返回
        {'trig': [...], 'tag': [...], 'node': [...]|None, 'cell': [...]|None, 'ev_size': n}。

        `ev_size`：档内事件对象 **不足 2 个**时无法实测尺寸（新开局的档很常见），
        从同族参考档用 `SaveDoc.probe_event_size(ref)` 量一个传进来即可（普通档 108 / 大任务档 124）。
        """
        pl = self.plain
        out = {}
        for nm, guid, size in (('tag', TRIG_TAGOBJ_GUID, TAG_OBJ_SIZE),
                               ('trig', TRIG_INSTANCE_GUID, TRIG_OBJ_SIZE),
                               ('node', TRIG_NODE_GUID, TRIG_NODE_SIZE),
                               ('cell', TRIG_CELLGUID, TRIG_CELL_SIZE)):
            pos, q = [], 0
            while True:
                i = pl.find(guid, q)
                if i < 0:
                    break
                pos.append(i)
                q = i + 1
            if not pos:
                if nm in ('node', 'cell'):
                    out[nm] = None          # 可选：老档/未建集群的档允许缺
                    continue
                raise RuntimeError('档内没有%s对象组' % nm)
            cnt = int.from_bytes(pl[pos[0] - 4:pos[0]], 'little') if pos[0] >= 4 else -1
            if cnt != len(pos) or any(pos[k + 1] - pos[k] != size
                                      for k in range(len(pos) - 1)):
                if nm in ('node', 'cell'):
                    out[nm] = None
                    continue
                raise RuntimeError(
                    '%s对象组布局不符（计数=%d，实际=%d，步长不齐）——'
                    '该存档格式不支持新写触发' % (nm, cnt, len(pos)))
            out[nm] = pos
        # 事件对象尺寸：由事件表实测（大任务档 124、普通档 108，不能写死）
        _, evs = self._split_tables()
        if len(evs) < 2:
            if ev_size is None:
                raise RuntimeError('档内事件对象不足 2 个，无法实测事件对象尺寸'
                                   '（可传 ev_size=SaveDoc.probe_event_size(同族参考档)）')
            if not (64 <= int(ev_size) <= 512):
                raise RuntimeError('ev_size=%r 不合理' % (ev_size,))
            out['ev_size'] = int(ev_size)
            return out
        ds = set(evs[k + 1]['obj'] - evs[k]['obj'] for k in range(len(evs) - 1))
        if len(ds) != 1:
            raise RuntimeError('事件对象尺寸不一致，无法克隆')
        out['ev_size'] = ds.pop()
        return out

    @staticmethod
    def probe_event_size(path):
        """从「事件对象 ≥2」的**同族参考档**实测事件对象尺寸（普通档 108 / 大任务档 124）。

        用途：新开局的档往往只有 1 个事件，`_newtrig_groups()` 量不出步长，
        把它传给 `create_trigger(..., ev_size=...)` 就能照常新写触发。
        """
        return SaveDoc(path)._newtrig_groups()['ev_size']

    def waypoint_limit(self):
        """路径点号上限（含）—— **702 个槽，号 0..701**。

        ★2026-10-17 23:2x **修正**：上一版我按"地图 [Waypoints] 最大号"当上限（510），
          判断 511 越界 —— **错的**。玩家指出并给出历史证据：
            · `T2`（`刷光棱塔` 配方）当年就是 `set_waypoint(655, 50, 50)` **新写**一个号，
              游戏内验收通过（玩家原话见文档 §50）⇒ **写地图没有的号是可行的**；
            · 引擎的路径点数组是定长 702（0..701），地图 `[Waypoints]` 只是"预先定义的"那些，
              不限制可写范围。
          ⇒ 上限恒为 701；`map_waypoint_max()` 只用于提示（"这个号地图里没有"）。
        """
        return self.WP_COUNT - 1

    def map_waypoint_max(self):
        """地图 `[Waypoints]` 里声明过的最大号（仅作提示用，不是写入上限）。"""
        m = getattr(self, '_map', None)
        if m is not None:
            try:
                wp = m.sections.get('waypoints', {}) or {}
                ids = [int(k) for k in wp if str(k).strip().isdigit()]
                if ids:
                    return max(ids)
            except Exception:
                pass
        return None

    def nearest_waypoint(self, x, y):
        """离 (x,y) 最近的**已有**路径点 → (号, (cx,cy))；没有就 None。"""
        best = None
        for n, c in self.waypoints_save().items():
            if c == (0, 0):
                continue
            d = (c[0] - x) ** 2 + (c[1] - y) ** 2
            if best is None or d < best[0]:
                best = (d, n, c)
        return None if best is None else (best[1], best[2])

    def ensure_waypoint_at(self, x, y, prefer=None, limit=None):
        """确保格子 (x, y) 上有一个路径点，返回其编号（**不改动已有路径点**）。

        2026-10-17：行为 125 的落点 = 路径点号，而路径点表就在存档里（`1586 + 4×号`）
        ⇒ 「想刷到任意格」只需在该格写一个路径点。策略：
          ① 该格已经有路径点 ⇒ 直接复用它的号（同格多点时取编号最小的）；
          ② 否则 `prefer` 优先；再取 **当前最大号 +1**；
          ③ 上界 = **地图 [Waypoints] 的最大号**（`waypoint_limit()`）——
             ★写超过地图声明范围的号，引擎不认（2026-10-17 玩家实测 511 无效）；
          ④ 范围内没有空号 ⇒ 返回 None，让调用方决定（通常改用最近的已有路径点）。
        """
        x, y = int(x), int(y)
        same = sorted(n for n, c in self.waypoints_save().items() if c == (x, y))
        if same:
            return same[0]
        lim = self.waypoint_limit() if limit is None else int(limit)
        used = set(self.waypoints_save())
        if prefer is not None:
            p = int(prefer)
            if 0 <= p <= lim and p not in used:
                self.set_waypoint(p, x, y)
                return p
        n = (max(used) + 1) if used else 1          # 优先「最大号 +1」：不碰低号的多方起始点
        if n <= lim and n not in used:
            self.set_waypoint(n, x, y)
            return n
        for n in range(1, lim + 1):                 # 范围内找最小空号
            if n not in used:
                self.set_waypoint(n, x, y)
                return n
        return None                                 # 范围内没有空号（不越界硬写）

    def _action_runs(self):
        """算出每条触发的**行为条数**（2026-10-18 实测规则，两份任务档 100% 验证）：

        触发实例 `+0xC4` = 它的**第一条行为对象的键**（行为对象 `+0x10` 存同键）；
        同一触发的多条行为在动作表里**按序号连续排列**。
        ⇒ 条数 = **下一条触发的首行为序号 − 本条首行为序号**；排在最后的用
        「动作总数 − 首行为序号」。
        （验证：三建筑档 616 条触发、1815 条行为，覆盖 1815、零异常；
          光棱塔档 616 条触发、1813 条行为，覆盖 1813、零异常。）

        返回 {触发ID: {'start':首序号, 'count':条数}}
        """
        pl = self.plain
        m = self._instance_maps()
        acts, _evs = self._split_tables()
        total = len(acts)
        bykey = {}
        for a in acts:
            bykey.setdefault(struct.unpack_from('<I', pl, a['obj'] + 16)[0], a['obj'])
        ent = {}
        for k, sid in m['sid'].items():
            o = m['inst'][k]
            a0 = bykey.get(struct.unpack_from('<I', pl, o + 0xC4)[0])
            if a0 is None:
                continue
            ent[sid] = struct.unpack_from('<I', pl, a0 + 0x38)[0]
        starts = sorted(set(ent.values()))
        out = {}
        for sid, s in ent.items():
            nxt = next((x for x in starts if x > s), total)
            out[sid] = {'start': s, 'count': max(int(nxt) - int(s), 0)}
        return out

    def trigger_actions(self, trigger_id, limit=32):
        """列出某触发的**全部行为**（多动作触发也能列）。

        返回 [{ordinal, index, action_off, type, arg, name, waypoint, cell}, ...]
        """
        pl = self.plain
        sid = str(trigger_id).upper()
        runs = self._action_runs()
        r = runs.get(sid)
        if r is None:
            raise RuntimeError('触发 %s 没有行为对象（或该档结构不支持）' % trigger_id)
        acts, _evs = self._split_tables()
        byidx = {}
        for a in acts:
            byidx.setdefault(struct.unpack_from('<I', pl, a['obj'] + 0x38)[0], a['obj'])
        out = []
        for i in range(min(r['count'], limit)):
            a = byidx.get(r['start'] + i)
            if a is None:
                break
            wp = struct.unpack_from('<I', pl, a + 88)[0]
            atp = struct.unpack_from('<I', pl, a + OBJ_TYPE_OFF)[0]
            argv = struct.unpack_from('<I', pl, a + OBJ_ARG_OFF)[0]
            if atp in ACTION_VAR_TYPES:          # §175：56/57 的变量号在 +164
                argv = struct.unpack_from('<I', pl, a + ACT_VAR_OFF)[0]
            # ★§255：行为 80/107（援军 / 超时空援军）的**小队键在 `+0x44`**
            #   （`+0x48` 不是！§218 实证）。这里统一回填，触发编辑器才能反查出小队名。
            # ★§283.9：4/5/7 也是小队型（小队键同样在 +0x44），别只认 80/107
            team = struct.unpack_from('<I', pl, a + 0x44)[0] if self.is_team_action(atp) else 0
            out.append({'ordinal': i + 1,
                        'index': r['start'] + i,
                        'action_off': a,
                        'type': atp,
                        'arg': argv,
                        'team': team,
                        'name': bytes(pl[a + 104:a + 112]).split(b'\x00')[0]
                                  .decode('ascii', 'ignore').strip(),
                        'waypoint': wp, 'cell': self.waypoint_cell(wp),
                        'key': struct.unpack_from('<I', pl, a + 16)[0]})
        return out

    def trigger_action(self, trigger_id, ac_index=1):
        """读一条**已有触发**第 `ac_index` 条行为（默认第 1 条）。

        定位链（2026-10-18 修正后）：记录 ID → 实例对象 → `+0xC4` 键 → 行为对象 `+0x10`；
        条数与后续行为用 `_action_runs()` 的连续序号规则。
        返回 dict：{trigger_id,index,action_off,count,ordinal,type,arg,name,waypoint,cell}
        （count = 该触发的行为条数；>1 时用 ac_index 选第几条）
        """
        pl = self.plain
        sid = str(trigger_id).upper()
        inst = next((r for r in self.records if r.sid == sid), None)
        if inst is None:
            raise RuntimeError('找不到触发 %s' % trigger_id)
        m = self._instance_maps()
        ks = [k for k, v in m['sid'].items() if v == sid]
        if not ks:
            raise RuntimeError('触发 %s 没有实例对象' % trigger_id)
        o = m['inst'][ks[0]]
        idx = struct.unpack_from('<I', pl, o + 172)[0]
        rows = self.trigger_actions(sid)
        if not rows:
            raise RuntimeError('触发 %s（序号 %d）没有动作对象，无法改造' % (trigger_id, idx))
        n = int(ac_index or 1)
        if not (1 <= n <= len(rows)):
            raise RuntimeError('触发 %s 只有 %d 条行为，取不到第 %d 条'
                               % (trigger_id, len(rows), n))
        r = rows[n - 1]
        return {'trigger_id': sid, 'index': idx, 'action_off': r['action_off'],
                'count': len(rows), 'ordinal': n, 'type': r['type'], 'arg': r['arg'],
                'name': r['name'], 'waypoint': r['waypoint'], 'cell': r['cell']}

    def retarget_trigger(self, trigger_id, ac_type=125, ac_name='GAPOWR',
                         ac_cell=None, ac_waypoint=None, ac_arg=None, dry_run=False,
                         ac_index=1):
        """把**档里已存在的一条触发**的动作改写成「行为 ac_type」（默认 125 建建筑）。

        为什么要有这个方法：本任务档上"**新建**触发"能写进去、能挂进容器，
        但引擎就是不执行（2026-10-18 内存取证：对象在索引数组里也不跑）；
        而"**改已有触发**的行为"是游戏内验证通过的（M1）⇒ 这是目前唯一可靠的路子。

        只写动作对象里的 4 处（长度**不变**，不影响容器/索引/7MB 红线）：
          +64/+60  行为类型 / 参数
          +104     8 字节 ASCII 建筑类型名（行为 125）
          +88      路径点编号（行为 125）
        `ac_cell` 给格子时自动补该格路径点（`ensure_waypoint_at`，不碰已有路径点）。

        dry_run=True 只读不改，返回现状（GUI 用来预览）。

        返回 dict：{trigger_id,index,action_off,waypoint,cell,type,name,arg,
                    old_type,old_name,old_waypoint,count,warnings}
        """
        info = self.trigger_action(trigger_id, ac_index=ac_index)
        a = info['action_off']
        info.update({'old_type': info['type'], 'old_name': info['name'],
                     'old_waypoint': info['waypoint'], 'warnings': []})
        warnings = info['warnings']
        if info['count'] > 1:
            warnings.append('该触发共 %d 条行为，本次改第 %d 条（偏移 %d）'
                            % (info['count'], info.get('ordinal', 1), a))
        if dry_run:
            return info
        ac_type = int(ac_type)
        if ac_type == 125:
            if not ac_name:
                raise RuntimeError('行为 125「将建筑建于…」必须给 ac_name（建筑类型名）')
            if ac_waypoint is None:
                if ac_cell is None:
                    raise RuntimeError('行为 125 需要 ac_waypoint 或 ac_cell')
                cx, cy = int(ac_cell[0]), int(ac_cell[1])
                had = sorted(n for n, cc in self.waypoints_save().items() if cc == (cx, cy))
                ac_waypoint = self.ensure_waypoint_at(cx, cy)
                if ac_waypoint is None:
                    near = self.nearest_waypoint(cx, cy)
                    if near is None:
                        raise RuntimeError('路径点号已用满且没有可复用的已有路径点')
                    ac_waypoint = near[0]
                    warnings.append('⚠ 路径点号已占满 ⇒ 改用最近的 #%d @(%d,%d)'
                                    % (near[0], near[1][0], near[1][1]))
                elif had:
                    warnings.append('复用该格已有路径点 %d' % ac_waypoint)
                else:
                    warnings.append('目标格 (%d,%d) 原来没有路径点 ⇒ 已写路径点 %d'
                                    % (cx, cy, ac_waypoint))
            pl = self.plain                    # ensure_waypoint_at 可能动过明文
            nm = str(ac_name).strip().upper().encode('ascii', 'ignore')[:8].ljust(8, b'\x00')
            struct.pack_into('<I', pl, a + OBJ_TYPE_OFF, 125)
            struct.pack_into('<I', pl, a + OBJ_ARG_OFF,
                             0 if ac_arg is None else int(ac_arg))
            pl[a + 104:a + 112] = nm
            struct.pack_into('<I', pl, a + 88, int(ac_waypoint))
            info.update({'type': 125, 'arg': 0 if ac_arg is None else int(ac_arg),
                         'name': str(ac_name).strip().upper(),
                         'waypoint': int(ac_waypoint),
                         'cell': self.waypoint_cell(int(ac_waypoint))})
        else:
            pl = self.plain
            struct.pack_into('<I', pl, a + OBJ_TYPE_OFF, ac_type)
            if ac_arg is not None:
                if ac_type in ACTION_VAR_TYPES:      # §175：56/57 变量号在 +164
                    struct.pack_into('<I', pl, a + OBJ_ARG_OFF, 0)
                    struct.pack_into('<I', pl, a + ACT_VAR_OFF, int(ac_arg) & 0xFFFFFFFF)
                else:
                    struct.pack_into('<I', pl, a + OBJ_ARG_OFF, int(ac_arg))
                info['arg'] = int(ac_arg)
            if ac_name or ac_cell is not None or ac_waypoint is not None:
                warnings.append('ac_name/ac_cell/ac_waypoint 只对行为 125 有效，已忽略')
            info['type'] = ac_type
        info['action_off'] = a
        self._invalidate_if_shifted()
        # ★§175：原地改类型/参数时长度不变 ⇒ _invalidate_if_shifted 不会作废缓存，
        #   但对象里的 type/arg 已经变了（retarget 到 56 后 actions() 还会给旧值）
        self.invalidate_caches()
        return info

    def create_trigger(self, name, ev_type, ev_arg, ac_type, ac_arg=0, attach=None,
                       owner=None, ac_name=None, ac_waypoint=None, ac_cell=None,
                       ev_size=None, pieces=None, register=None, persistence=0,
                       hb_warn=True):
        """在存档里**新写一个触发**（克隆现有对象集群改键，非从零合成）。

        2026-10-14 定案（**游戏内实测通过**：10 秒地图点亮、30 秒胜利）：
          · 一个可用触发 = **六件套**：标签(184B) + CellTag(76B) + 触发实例(200B)
            + TagNode(92B) + 事件 + 行为。只写「标签 + 实例 + 事件 + 行为」
            （V1~V4 版）**写得出、读得进、但不生效** —— 实测只有与建筑绑定的
            那条（电厂自爆）能动，纯计时/胜利条件一律不触发。
          · 键用**同区未用地址**：实例/标签 0x02Axxxxx 区、TagNode/CellTag
            0x13Fxxxxx 区（异区键实测载入崩）；
          · ★**序号**必须写对：实例/标签对象 `+172`、事件/行为对象 `+56`
            = 该触发在集群里的 0 基序号（追加到末尾 ⇒ = 原有触发数）。
            写 0 时能读档但引擎找不到它（实测「加了两个触发不报错也不生效」）；
          · ★事件/行为对象里**克隆源的运行期指针必须清零**（只留身份+类型+参数+序号），
            否则继承上一个对象的指针（权威档：13/30 事件 +40/+52/+76..+100 全 0）；
          · ★TagNode `+80` = 「流逝时间」等待帧数（秒 × 15）、`+88` 最低位 = 启用状态；
            CellTag `+56` = 标签键、`+60` = TagNode 键、`+64` = 挂接数（无挂接 0）；
          · 各组**末尾插入**并各自把组计数 +1（明文变长，save() 已支持）；
          · 可选 `attach=[(格x, 格y), …]`：顺手把新触发挂到这些建筑上 —— 建筑 tag
            字段 ← 本触发 CellTag 自键、CellTag `+64` = 挂接数。**不动激活登记表**
            （该表只收计时触发；对象事件触发（如 B = 进入事件）不在表里也照常工作）。
          · ★行为 125（`ac_type=125`）：`ac_name` = 建筑类型名、`ac_waypoint` = 路径点号，
            或直接给 **`ac_cell=(x, y)`** —— 该格没有路径点就**自动写一个**（见
            `ensure_waypoint_at`）⇒ 配合上面的路径点表，触发法可以"刷到任意格"。
          · `ev_size`：**新开局的档只有 1 个事件**时量不出事件对象步长，
            传 `ev_size=SaveDoc.probe_event_size(同族参考档)`（普通档 108 / 大任务档 124）即可。
          · `persistence`（**2026-10-20 新增**）＝**触发类型（重复类型）**，写进
            **标签对象 `+0xB0`(=+176)**，对应地图 `[Tags]` 第 0 字段：
            `0` 一次性·任一挂载 / `1` 一次性·挂载清空 / `2` 可重复。
            ⚠ 该字节以前被注释成"是否参与评估"，其实是同一个位置的两种读法 —— 实测
            写上 1 会让触发不执行（Z4/Z5 单变量对照），所以**默认 0（游戏内已验证）**；
            想让新触发"跑完还能再触发"就传 2。
        返回 {'trigger_id', 'tag_id', 'index', 'ev_off', 'ac_off', 'attached', 'warnings'}。
        """
        if not name:
            raise ValueError('触发名不能为空')
        name = str(name)[:16]   # 名字区 52B（120..171），之后是序号/难度位，不能覆盖
        # ★2026-10-20 玩家要求：允许 ac_type=None ⇒ **一条行为都不建**（空触发）。
        #   这样"新建"不会硬塞一条 21 播放语音让你还得再加一条才能删它。
        no_action = ac_type is None
        ev_type, ev_arg = int(ev_type), int(ev_arg)
        ac_arg = 0 if no_action else int(ac_arg)
        if ev_type == 0 or ev_type not in EVENT_TYPES:
            raise ValueError('事件类型 %s 无效（0 是占位项，禁止使用）' % ev_type)
        if not no_action and (ac_type == 0 or ac_type not in ACTION_TYPES):
            raise ValueError('行为类型 %s 无效（0 是占位项，禁止使用）' % ac_type)
        ac_type = 0 if no_action else int(ac_type)
        if not (0 <= ev_arg <= 0xFFFFFF and 0 <= ac_arg <= 0xFFFFFF):
            raise ValueError('参数需在 0 ~ 16777215 之间')
        persistence = int(persistence)
        if persistence not in (0, 1, 2):
            raise ValueError('触发类型 persistence 只能是 0/1/2（0 一次性·任一挂载 / '
                             '1 一次性·挂载清空 / 2 可重复）')
        grps = self._newtrig_groups(ev_size=ev_size)
        pl = self.plain
        ev_size = grps['ev_size']
        node_pos, cell_pos = grps.get('node'), grps.get('cell')
        idx = len(grps['trig'])                  # 集群内序号（追加到末尾）
        # ★2026-10-24 玩家定案（纠正 §219.7 的写法）：事件 8「任何事件」的 TagNode
        #   帧数**必须是 15**（1 逻辑秒）。帧数 0 ⇒ 不进「定时触发器挂接表」⇒ 引擎
        #   不轮询 ⇒ 永不评估（R2/R4 + §219.7 双向实测）。旧写法 `else 0` 会让
        #   "事件 8" 建出来就是废触发，而且与 `set_event` / `repair_tag_node` 的口径
        #   （两处早就把 8 自动武装成 15）不一致 —— 现在统一按 15 写。
        ticks = ev_arg * LOGIC_FPS if ev_type == 13 else (15 if ev_type == 8 else 0)
        warnings = []
        if ev_type == 8:
            warnings.append('事件 8「任何事件」：TagNode 帧数已武装为 15（1 秒）—— '
                            '帧数 0 不进计时表、引擎不轮询 ⇒ 永不评估')
        # ★§181：事件 36/37 是纯电平查询，没有对象/标签回调 —— 只有进了
        #   「定时触发器挂接表」（条件：ticks>0，即首个事件是 13）才会被轮询。
        #   这里只提示、不擅自改事件列表（调用方可能随后还会 add_event 补计时）。
        if hb_warn and ev_type in POLL_EVENT_TYPES and ticks <= 0:
            warnings.append(
                '⚠ 事件 %d 是纯电平查询（局部变量），本触发没有计时事件 ⇒ 不进'
                '「定时触发器挂接表」，读档后不会被评估。请再补一条「13 流逝时间」。'
                % ev_type)
        # ★★2026-10-20 修正（玩家实测反馈）：默认**一律六件套**。
        #   旧逻辑是「ticks<=0 且没 attach ⇒ 不建 TagNode/CellTag」，结果：
        #   用编辑器新建一条「事件 8」触发 → 没有运行期节点；之后把它改成
        #   「13 流逝时间」→ 照样没有节点 ⇒ 引擎根本不执行，列表里立刻显示"已执行/无组件"，
        #   等于**新建出来就是一条废触发**（玩家一眼就看出来了）。
        #   而游戏内验收通过的"触发法刷建筑"（place_building_by_trigger）本来就**显式传 pieces=True**，
        #   §51.2 的结论也是"凡是新写触发，一律补齐六件套"（四件套不执行）。
        #   ⇒ 默认改成 True；只有显式传 pieces=False 才省（老实验脚本用）。
        #   注意：ticks<=0 时不登记「定时触发器挂接表」—— 与触发法完全一致（已验证）。
        need_pieces = True if pieces is None else bool(pieces)
        # ★2026-10-17 实测：**四件套（只有 tag/trig/ev/ac）的触发引擎不执行** ——
        #   SAVE0029 上用事件 8 写的"任何事件→125"读档后没刷出建筑（T3）；
        #   同样条件补上 TagNode/CellTag（T2 事件13）就成功 ⇒ 默认按"需要六件套"处理。
        synth = None                              # (cell 计数槽, node 计数槽)
        if not need_pieces:
            if node_pos or cell_pos:
                warnings.append('本触发无计时 ⇒ 按参数不建 TagNode/CellTag')
            else:
                warnings.append('档内没有 TagNode/CellTag 对象组 —— 按参数跳过')
            node_pos = cell_pos = None
        elif not node_pos or not cell_pos:
            # 组根本不存在 ⇒ 在"组空位"补建（cell 槽 = tag 组末尾，node 槽 = trig 组末尾，
            # 两处都必须是 u32 0 —— 引擎自己没建过这两组时就是这样）
            cslot = grps['tag'][-1] + TAG_OBJ_SIZE
            nslot = grps['trig'][-1] + TRIG_OBJ_SIZE
            try:
                zc = struct.unpack_from('<I', pl, cslot)[0]
                zn = struct.unpack_from('<I', pl, nslot)[0]
            except Exception:
                zc = zn = -1
            if zc == 0 and zn == 0:
                synth = (cslot, nslot)
                node_pos = cell_pos = []          # 标记"要建"，插入位置另算
                warnings.append('档内原本没有 TagNode/CellTag 对象组 —— 已在组空位补建（+168B）')
            else:
                node_pos = cell_pos = None
                warnings.append('档内没有 TagNode/CellTag 对象组，且组空位不是 0（%r/%r）'
                                '—— 未补建；该触发可能不生效' % (zc, zn))

        # 模板：**优先挑"干净"的实例**（见 _pick_trigger_template）/ 对应标签 /
        #       最后一个事件 / 最后一个行为
        tpl_inst, tpl_tag = self._pick_trigger_template(grps)
        t_tpl = bytes(pl[tpl_inst:tpl_inst + TRIG_OBJ_SIZE])
        g_tpl = bytes(pl[tpl_tag:tpl_tag + TAG_OBJ_SIZE])
        _, evs = self._split_tables()
        acts, _ = self._split_tables()
        ev_src, ac_src = evs[-1], acts[-1]
        # ★★★2026-10-23 §219 修复（玩家实测「刷兵没出来」的根因）：
        #   事件/行为对象的 `+0x38` **不是"所属触发的序号"，而是它在事件表/行为表里的
        #   物理序号**（实测：SAVE0029 的 407 个行为 +0x38 严格 = 0..406 且唯一；
        #   93 个事件 = 0..92）。旧代码写 idx（触发序号 93）⇒ 与表里已有的 93 号
        #   对象**撞号**：`trigger_actions()` 的 byidx 是 setdefault（同序号取第一个）
        #   ⇒ 定位到**别人的旧对象**，spawn 把参数写到它身上（还把 type 107→80），
        #   而实例真正指向的新对象永远没参数 ⇒ 读档不出兵。
        #   （`add_action` 同日已按"物理下标"修过；create_trigger/add_event 漏修。）
        #   正确值 = 表内 max+1（追加到组尾 ⇒ 就是 max+1）。
        ev_ord = max(int.from_bytes(pl[e['obj'] + 56:e['obj'] + 60], 'little')
                     for e in evs) + 1
        ac_ord = max(int.from_bytes(pl[a['obj'] + 56:a['obj'] + 60], 'little')
                     for a in acts) + 1
        ev_guid = bytes(pl[ev_src['obj']:ev_src['obj'] + 16])   # ★插入前快照（插入会推移这些字节）
        ac_guid = bytes(pl[ac_src['obj']:ac_src['obj'] + 16])
        if node_pos and cell_pos:
            tpl_node = bytes(pl[node_pos[0]:node_pos[0] + TRIG_NODE_SIZE])
            tpl_cell = bytes(pl[cell_pos[0]:cell_pos[0] + TRIG_CELL_SIZE])
        else:                                    # 档里没有可克隆的 ⇒ 用空壳模板
            tpl_node, tpl_cell = TRIG_NODE_TEMPLATE, TRIG_CELL_TEMPLATE
        # ★事件/行为组的计数注册字段（组首对象前 4 字节）—— 2026-10-14 读档崩溃归因：
        #   向导只加了 tag/trig 的计数，事件/行为组仍是旧值 ⇒ 引擎按旧 count 读流，
        #   多出的对象被当成下一组数据 ⇒ 载入即崩（实测 MD_修改.SAV）。
        cnt_ev_pos = evs[0]['obj'] - 4
        cnt_ac_pos = acts[0]['obj'] - 4
        for nm, p0, n in (('事件', cnt_ev_pos, len(evs)), ('行为', cnt_ac_pos, len(acts))):
            cur = int.from_bytes(pl[p0:p0 + 4], 'little')
            if cur != n:
                raise RuntimeError(
                    '%s组计数注册字段(%d)与实际对象数(%d)不符 —— '
                    '该档布局不支持新写触发（为避免写坏已拒绝）' % (nm, cur, n))

        # 同区新键（同类最大键 +0x1000 起，全档查重）
        used = []
        def fresh(keys, base):
            v = ((max(keys) + 0x1000) & 0xFFFFFFFF) if keys else base
            while pl.find(struct.pack('<I', v)) >= 0 or v in used:
                v = (v + 0x1000) & 0xFFFFFFFF
            used.append(v)
            return v
        def fresh2(pos_list):
            return fresh([int.from_bytes(pl[p + 16:p + 20], 'little') for p in pos_list]
                         + used, 0x02A75020)
        K_TRIG = fresh2(grps['trig'])
        K_TAG = fresh2(grps['tag'])
        obj_pos = [o['obj'] for o in self._objects()]
        K_EV = fresh2(obj_pos)
        K_AC = None if no_action else fresh2(obj_pos)
        K_NODE = K_CELL = None
        if node_pos is not None:          # 空列表 = 要补建（键从基址起查重）
            # ★TagNode / CellTag 同属 0x13xxxxxx 区（与实例/标签键**不通用**，异区键载入崩）；
            #   两份键各按「自己那一类最大键 +0x1000」取，且互查重（fresh 内部查 used）
            K_NODE = fresh([int.from_bytes(pl[p + 16:p + 20], 'little') for p in node_pos],
                           NODEGRP_BASE_KEY)
            K_CELL = fresh([int.from_bytes(pl[p + 16:p + 20], 'little') for p in cell_pos],
                           NODEGRP_BASE_KEY)

        # 新 ID：**统一走 `_next_sid(2)`**（§254）。
        #   · 它只扫 `0100xxxx` 那一段（AI 三大件里那批 GUID 风格随机 ID 不算 ——
        #     算进去新 ID 会跳到 GUID 区，既不符合 FA2 的顺序编号，记录扫描也找不到）；
        #   · 三类（特遣/小队/脚本）+ 触发/标签**共享同一序列**，一起取最大值；
        #   · ★以前这里自己算一套、且 `_ai_ids()` 是**缓存**的 ⇒ 同一批操作里
        #     "先建特遣、再建触发" 两边拿到同一个 `01000494`，ID 撞车（§254 抓到）。
        #   · FA2 里触发和标签各占一个连号 ⇒ 一次取 2 个。
        sid_t, sid_g = self._next_sid(2)

        def patch(buf, off, data):
            buf[off:off + len(data)] = data
        u32v = lambda v: struct.pack('<I', v)
        def setname(buf, off, span, text):
            buf[off:off + span] = b'\x00' * span
            # ★名字在存档里是 **GBK**（`_scan_records` 用 `_decode_txt` 解 +64 起的那段）；
            #   先 GBK，编不出来再退回 UTF-8。
            try:
                b = text.encode('gbk')
            except Exception:
                b = text.encode('utf-8', 'ignore')
            b = b[:span - 1]
            buf[off:off + len(b)] = b
        def blank_runtime(buf, size):
            """清掉克隆源的运行期指针（保留 0..+36：身份签名 + 类常量 + FFFFFFFF）。"""
            for off in range(40, size, 4):
                buf[off:off + 4] = b'\x00' * 4

        # ---- 构造对象 ----
        t = bytearray(t_tpl)
        patch(t, 16, u32v(K_TRIG))
        patch(t, 56, sid_t.encode())
        # ★★2026-10-18 15:0x 重大修正：实例 **+0xB0 是"启用/难度"标志字**（4 个字节）。
        #   旧版这里**沿用模板**（模板 = 档里第一个触发实例）。若那一代档的模板是"被禁用"的，
        #   我们克隆出来的触发就**生下来就是禁用的** —— 引擎永远不会评估它。
        #   实测（Z1_最小复现）：从"所有触发全禁用"的基档克隆 ⇒ 新实例 +0xB0 = 0x00010101，
        #   而地编自己建、游戏里确实会执行的那条 = 0x01010101 ⇒ 读档一分钟什么都没发生。
        #   全档统计：能跑的触发全是 0x01010101；0x00010101 全是玩家在地编里勾掉"禁用"的。
        #   ⇒ 新写触发必须**强制启用**（四字节全 1 = 启用 + 三个难度都开）。
        patch(t, 0xB0, u32v(0x01010101))
        # ★名字区：**实例对象 +120..+171**（= ASCII 记录帧的 +64..+115，帧差 56B）
        #   `_scan_records` 读到名字在记录 +64 ⇒ 这里必须写 +120（旧值是对的，别改）。
        setname(t, 120, 52, name)
        patch(t, TRIG_REC_IDX_OFF, u32v(idx))         # ★集群内序号
        patch(t, 192, u32v(K_EV))
        patch(t, 196, u32v(K_AC or 0))        # 空触发 ⇒ 行为链头 = 0
        # ★2026-10-17：触发所属方（国家）= **记录 +128**（= 目标阵营 House 块 +72 的键）。
        #   `t` 是**实例对象帧**，ASCII 记录帧比它晚 **56 字节**（t+56 就是记录 +0 的 ID）
        #   ⇒ 在 t 帧上要写 `TRIG_OWNER_OFF + 56`。
        #   不传 owner ⇒ 保持模板的所属方（旧行为）。
        TRIG_REC_DELTA = 56
        # ★★2026-10-18 15:1x 实测（Z7/Z8 单变量对照，同一份大任务档、同一个格子）：
        #   · 新建触发把建筑建给 **Neutral** ⇒ 建筑**闪一下就消失**（引擎会移除无主/中立建筑）；
        #   · 建给 **真实阵营**（玩家 Americans）⇒ **稳稳留住**（蓝色、无电状态，正常）。
        #   地编那条刷电厂能留住，是因为它的动作里带了自己的所属信息；我们克隆的动作没有，
        #   于是完全依赖触发所属方 ⇒ **新写触发必须给真实阵营**。
        #   owner 缺省 = 存档当前玩家阵营（存档编辑器里这几乎总是想要的）；
        #   想保留旧行为（沿用模板所属方）显式传 owner='template'。
        if owner is None:
            try:
                owner = self.player_house()
            except Exception:
                owner = None
        elif str(owner).strip().lower() == 'template':
            owner = None
        if owner:
            _ok = self.house_trigger_owner_key(owner)
            if _ok is None:
                raise RuntimeError('认不出阵营 %s 的"触发所属方对象"键（House 块 +72）' % (owner,))
            patch(t, self.TRIG_OWNER_OFF + TRIG_REC_DELTA, u32v(_ok))
        g_ = bytearray(g_tpl)
        patch(g_, 16, u32v(K_TAG))
        patch(g_, 56, sid_g.encode())
        # ★★标签对象 **+0xB0(=+176) = 触发类型（标签持久性 / "重复类型"）**。
        #   ⚠ 2026-10-18 曾把它当成"是否参与评估"标志（Z4/Z5 单变量对照：写 1 就不执行）。
        #   2026-10-20 定案：这个字节就是地图 `[Tags]` 第 0 字段 PERSISTENCE
        #   （0 一次性·任一挂载 / 1 一次性·挂载清空 / 2 可重复），
        #   而记录帧（Rec.off，= 对象帧+56）的 +120 正是它 —— SAVE48DB 82/82 与地图对拍一致。
        #   ⇒ 写 1 不执行，其实是"半持久"语义；**默认 0**（多年游戏内实测稳定），
        #     要让触发"跑完还能再触发"才显式传 2。
        patch(g_, 0xB0, u32v(persistence))
        setname(g_, 120, 52, name + ' 1')             # 标签：实例 +120..+171（同上，帧差 56B）
        patch(g_, TRIG_REC_IDX_OFF, u32v(idx))        # ★序号（与实例同值）
        patch(g_, 180, u32v(K_TRIG))
        # ★★★★★ 2026-10-18 11:2x：**标签对象里有两处"残留运行期指针"必须清零**。
        #   实测（内存+存档双证）：FA2 亲手建的标签在 `+40`、`+52` 是 0；
        #   我们克隆模板时会**继承模板的旧指针**（例：0x147F9AE0 / 0x147F9300），
        #   这种指向已释放/别的对象的指针会让引擎在"评估触发"阶段把这条跳过。
        #   ⇒ 克隆后一律置 0（与 FA2 产物一致）。
        # ★§192（2026-10-22）扩表：82 个标签的实测统计显示脏槽不止这两个 ——
        #   对象体 **+0x14 / +0x20**（= 帧 +40/+52）与 **+0x2C..+0x5F**（= 帧 +0x40..+0x73）
        #   在 FA2 产物里全 0，而从模板克隆会带进旧键(0x13xxxxxx)/裸指针(0x10xxxxxx)。
        #   ⇒ 整段清零（帧 0x28..0x38 与 0x40..0x74），保留 +0x60 的静态常量。
        patch(g_, 0x28, b'\x00' * 0x10)      # 对象 +0x14..+0x23
        patch(g_, 0x40, b'\x00' * 0x34)      # 对象 +0x2C..+0x5F
        e_ = bytearray(pl[ev_src['obj']:ev_src['obj'] + ev_size])
        blank_runtime(e_, ev_size)
        patch(e_, 16, u32v(K_EV))
        patch(e_, 56, u32v(ev_ord))                    # ★事件表内序号（max+1，§219）
        patch(e_, OBJ_TYPE_OFF, u32v(ev_type))
        patch(e_, OBJ_ARG_OFF, u32v(ev_arg))
        a_ = None
        if not no_action:
            a_ = bytearray(pl[ac_src['obj']:ac_src['obj'] + ACTION_SIZE])
            blank_runtime(a_, ACTION_SIZE)
            patch(a_, 16, u32v(K_AC))
            patch(a_, 56, u32v(ac_ord))                # ★行为表内序号（max+1，§219）
            patch(a_, OBJ_TYPE_OFF, u32v(ac_type))
            patch(a_, OBJ_ARG_OFF, u32v(ac_arg))
            if ac_type in ACTION_VAR_TYPES:      # §175：56/57 的变量号在 +164
                patch(a_, OBJ_ARG_OFF, u32v(0))
                patch(a_, ACT_VAR_OFF, u32v(ac_arg & 0xFFFFFFFF))
        # ★2026-10-17：行为 125「将建筑建于…」的两个专有参数（其它行为不传即不写）：
        #   +88  = 路径点编号（建筑落在该路径点上）
        #   +104 = 8 字节 ASCII 建筑类型名（如 b"ATESLA"）
        ac_note = ''
        if ac_type == 125:
            if not ac_name or (ac_waypoint is None and ac_cell is None):
                raise RuntimeError('行为 125「将建筑建于…」必须同时给 ac_name（建筑类型名）'
                                   '与 ac_waypoint（路径点编号）或 ac_cell（目标格子 x,y）')
            if ac_waypoint is None:
                ac_waypoint = self.ensure_waypoint_at(ac_cell[0], ac_cell[1])
                if ac_waypoint is None:
                    near = self.nearest_waypoint(int(ac_cell[0]), int(ac_cell[1]))
                    if near is None:
                        raise RuntimeError('路径点号已用满且没有可复用的已有路径点')
                    ac_waypoint = near[0]
                    warnings.append('⚠ 地图只声明 0..%d 号路径点且已占满 ⇒ 改用最近的 #%d @(%d,%d)'
                                    % (self.waypoint_limit(), near[0], near[1][0], near[1][1]))
                else:
                    warnings.append('路径点 %d = (%d,%d)（该格原来没有路径点，已写进存档）'
                                    % (ac_waypoint, int(ac_cell[0]), int(ac_cell[1])))
            _nm = str(ac_name).upper().encode('ascii', 'ignore')[:8].ljust(8, b'\x00')
            patch(a_, 104, _nm)
            patch(a_, 88, u32v(int(ac_waypoint)))
            ac_note = '行为125：%s @ 路径点 %d' % (ac_name, int(ac_waypoint))
        elif ac_name or ac_waypoint is not None or ac_cell is not None:
            warnings.append('ac_name/ac_waypoint/ac_cell 只对行为 125 有效，已忽略')
        nb = cb = None
        if node_pos is not None:
            nb = bytearray(TRIG_NODE_SIZE)
            patch(nb, 0, TRIG_NODE_GUID)
            patch(nb, 16, u32v(K_NODE))
            patch(nb, 20, tpl_node[20:36])            # 类常量（跨档恒定）
            patch(nb, 36, b'\xff' * 4)
            patch(nb, NODE_TRIGKEY_OFF, u32v(K_TRIG))
            patch(nb, 76, u32v(NODE_SPARE76))
            patch(nb, NODE_TICKS_OFF, u32v(ticks))    # ★流逝时间帧数
            patch(nb, NODE_FLAG_OFF, u32v(1))         # ★启用状态 = 1
            cb = bytearray(TRIG_CELL_SIZE)
            patch(cb, 0, TRIG_CELLGUID)
            patch(cb, 16, u32v(K_CELL))
            patch(cb, 20, tpl_cell[20:36])
            patch(cb, 36, b'\xff' * 4)
            patch(cb, CELL_TAGKEY_OFF, u32v(K_TAG))
            patch(cb, CELL_NODEKEY_OFF, u32v(K_NODE))
            # +64 挂接数保持 0（新触发未挂任何建筑/格子）

        # ★★★「定时触发器挂接表」必须补登记（2026-10-14 定案：**这就是新写触发不生效的那一环**）
        #   带计时的触发（TagNode 帧数 > 0）除六件套之外，还必须在 [count][CellTag 键…]
        #   这张表里挂上自己的 CellTag 键；漏了 ⇒ 六件套齐全、序号正确、能读档，
        #   但计时器不武装 ⇒ **永远不触发**。地图写的档（地编写入）表里有，工具写的没有。
        timer_ins = None
        _want_reg = (ticks > 0) if register is None else bool(register)
        if _want_reg and K_CELL is not None:
            tl = self.celltag_timer_list()
            if tl is not None:
                timer_ins = tl
            else:
                # 空表（新开局的档）：用结构指纹定位计数槽，从零建表 + 登记本触发的 cell 键
                slot = self.timer_table_slot()
                if slot is not None:
                    timer_ins = {'count_pos': slot, 'list_pos': slot + 4,
                                 'keys': [], 'expected': 0, 'n': 0, 'empty': True}
                    warnings.append('「定时触发器挂接表」原本是空的 —— 已建表并登记本触发的 cell 键')
                else:
                    warnings.append('未定位到「定时触发器挂接表」—— 该档里这张表为空或缺席，'
                                    '新触发可能不触发（请把此档留档以便继续定位）')
        # 插入位置（各组末尾）+ 降序应用（低位插入会推移高位坐标）
        ins = [(grps['tag'][-1] + TAG_OBJ_SIZE, bytes(g_)),
               (grps['trig'][-1] + TRIG_OBJ_SIZE, bytes(t)),
               (ev_src['obj'] + ev_size, bytes(e_))]
        if a_ is not None:
            ins.append((ac_src['obj'] + ACTION_SIZE, bytes(a_)))
        if node_pos is not None:
            if synth:
                ins.append((synth[1] + 4, bytes(nb)))     # node 计数槽之后
                ins.append((synth[0] + 4, bytes(cb)))     # cell 计数槽之后
            else:
                ins.append((node_pos[-1] + TRIG_NODE_SIZE, bytes(nb)))
                ins.append((cell_pos[-1] + TRIG_CELL_SIZE, bytes(cb)))
        if timer_ins is not None:
            # 追加到表尾（与触发序号顺序一致）；表名"变长"，插 4 字节即可，
            # 其后的数据整体后移（本函数的多点插入已按降序应用，互不干扰）
            ins.append((timer_ins['list_pos'] + 4 * timer_ins['n'], struct.pack('<I', K_CELL)))
            # 计数字段在最低位（低于全部插入点）⇒ 位置稳定，可直接 +1
            patch(pl, timer_ins['count_pos'],
                  u32v(int.from_bytes(pl[timer_ins['count_pos']:timer_ins['count_pos'] + 4],
                                      'little') + 1))
        else:
            if _want_reg:
                warnings.append('未登记「定时触发器挂接表」：本档无法定位该表')
        for pos, data in sorted(ins, key=lambda x: -x[0]):
            pl[pos:pos] = data

        # 组计数 +1（插入后**重新定位**各组首对象；tag 组尾紧挨 cell 计数字段，必须重定位）
        for nm, guid in (('tag', TRIG_TAGOBJ_GUID), ('trig', TRIG_INSTANCE_GUID),
                         ('node', TRIG_NODE_GUID), ('cell', TRIG_CELLGUID)):
            if nm in ('node', 'cell') and node_pos is None:
                continue
            p0 = pl.find(guid)
            if p0 < 4:
                continue
            old = int.from_bytes(pl[p0 - 4:p0], 'little')
            patch(pl, p0 - 4, u32v(old + 1))
        # ★事件/行为组计数同样 +1（漏加 = 读档崩溃，2026-10-14 实测归因）。
        #   ⚠ 必须在插入后**重新定位**组首对象：事件组紧跟行为组，行为组尾部插入
        #   会把事件计数字段推移 —— 用插入前坐标会写进别的对象（第一次修复就栽在这）。
        self._objs = None
        self._events = None
        self._actions = None
        acts2, evs2 = self._split_tables()
        _cnt_items = [('事件', evs2[0]['obj'], len(evs2) - 1)]
        if not no_action:
            _cnt_items.append(('行为', acts2[0]['obj'], len(acts2) - 1))
        for nm, first, n_old in _cnt_items:
            p0 = first - 4
            cur = int.from_bytes(pl[p0:p0 + 4], 'little')
            if cur != n_old:
                raise RuntimeError('%s组计数注册字段=%d，期望旧值=%d —— 布局异常，已中止'
                                   % (nm, cur, n_old))
            patch(pl, p0, u32v(n_old + 1))
        # 新对象最终位置：降序插入后低位插入会推移高位对象 ⇒ 按「类GUID+自键」回查（最稳；
        # 单键会撞上触发对象里的 ev/ac 指针字段）
        def locate(key, guid):
            i = pl.find(guid + struct.pack('<I', key))
            if i < 0:
                raise RuntimeError('新对象回查失败（键 %08X）' % key)
            return i + OBJ_TYPE_OFF
        ev_off = locate(K_EV, ev_guid)
        ac_off = None if no_action else locate(K_AC, ac_guid)
        # 旧的事件/行为/挂接 pending 偏移若在插入点之后需要平移（簿记一致性）
        for delta_map, store in (
                ([(p, len(d)) for p, d in ins], self.pending_events),
                ([(p, len(d)) for p, d in ins], self.pending_actions)):
            if store:
                moved = {}
                for k, v in store.items():
                    nk = k + sum(sz for p, sz in delta_map if p <= k)
                    moved[nk] = v
                store.clear()
                store.update(moved)
        if self.pending_attach:
            # ★2026-10-22 修 bug：`ins` 的元素是 `(pos, data)`，这里却按 `(p, size)` 解包
            #   ⇒ `sz` 拿到的是 bytes，`p <= f` 之后 `f + bytes` 直接 TypeError。
            #   以往只有在「本会话已有 pending_attach（待补挂接）且随后的 create_trigger
            #   又要插对象」时才会踩到 —— 局部变量配方（先建 N 条挂接触发、再建汇总触发）
            #   正好踩中，汇总触发直接建不出来。
            self.pending_attach = [
                (f + sum(len(d) for p, d in ins if p <= f), old)
                for f, old in self.pending_attach]
        # 缓存全部失效 + 重扫
        self._objs = None
        self._events = None
        self._actions = None
        self._ticks = None
        self._celltags = None
        self._ctimerlist = None
        self._house_econ = None
        self._custom_houses = None
        self._house_keys_memo = None
        self._houses_memo = None        # ★P3-14
        self._tagrecs = None            # ★2026-10-20：同上，新建触发的标签也要能被认出
        self._instmaps = None           # ★2026-10-20：不失效它 ⇒ 新触发"没有运行期实例"，
        #                                  接着 add_action/copy 全部失败（自检抓到）
        self.records = self._scan_records()
        if self._map:
            self._events = None
            self._actions = None
            self._apply_map()
        self.pending_newtrig = True
        # ---- 可选：顺带挂到建筑（2026-10-15 定案，与「挂接」页同一套机制）----
        #   挂接 = ① 建筑对象的 tag 字段 ← 本触发 CellTag 的自键
        #          ② 本触发 CellTag 的 +64 计数 = 挂接建筑数（0 → N）
        #   ★不需要动激活登记表（那张表只收「TagNode 帧数 > 0」的计时触发；
        #     对照：基档 B = 进入事件（无计时）、挂在 NASAM 上，不在表里也照常工作）。
        attached, fails = [], []
        if attach:
            if not K_CELL:
                warnings.append('该档没有 CellTag 对象组 ⇒ 无法挂接建筑（attach 已忽略）')
            else:
                for item in attach:
                    try:
                        cx, cy = int(item[0]), int(item[1])
                    except Exception:
                        fails.append('坐标 %r 无法解析（应形如 (72, 79)）' % (item,))
                        continue
                    f, err = self.locate_building_field(cx, cy, mode='zero')
                    if f is None:
                        fails.append('格(%d,%d)：%s' % (cx, cy, err))
                        continue
                    self.plain[f:f + 4] = u32v(K_CELL)
                    attached.append((cx, cy, f))
                if attached:
                    self._celltags = None
                    for nd in self.celltag_nodes():
                        if nd['x'] == K_CELL:
                            patch(self.plain, nd['count_pos'], u32v(len(attached)))
                            break
                    # ★再失效一次：上面 celltag_nodes() 建的是**改计数之前**的缓存
                    #   （否则总览会读回 count=0 —— 2026-10-15 自检抓到）
                    self._celltags = None
                    self.pending_attach.extend([(f, 0) for _a, _b, f in attached])
                warnings.extend(fails)
        return {'trigger_id': sid_t, 'tag_id': sid_g, 'index': idx,
                'ev_off': ev_off, 'ac_off': ac_off, 'owner': owner,
                'persistence': persistence,
                'ac_note': ac_note,
                'attached': attached, 'warnings': warnings}

    # ---------- FA2 风格：一条触发 = 多事件 + 多行为 ----------
    def create_trigger_multi(self, name, events, actions=None, **kw):
        """**像地编那样一次建一条触发：多个事件 + 多个行为**（§174）→ dict。

        `events`  = `[(type, arg), …]` 或 `[{'type':…, 'arg':…}, …]`，至少 1 条
        `actions` = 同上；行为 125 可给 `{'type':125, 'name':'ATESLA', 'cell':(x,y)}`
        `**kw`    = 透传 `create_trigger`（`attach` / `owner` / `persistence` / `ev_size` / `pieces`）

        ★语义（§156 已实测）：**同一触发内的多个事件恒为 AND**（全部满足才执行）；
          想表达 OR 就建多条触发。
        ★顺序：事件在存档里是**链头插逆序**，本函数按 `events` 的**列表顺序**逐个加
          ⇒ 列表最后一条 = 链头 = 引擎**最先评估**的那条（§163 短路就是从这里开始的）。
          行为反之，按列表顺序**依次执行**。

        返回同 `create_trigger`，另加：
          `events`（链序，链头在前）/ `actions`（执行序）/ `warnings`（含追加失败项）。
        """
        def _pair(o):
            if isinstance(o, dict):
                return int(o['type']), int(o.get('arg') or 0)
            seq = list(o)
            return int(seq[0]), (int(seq[1]) if len(seq) > 1 else 0)

        evs = [_pair(e) for e in (events or [])]
        if not evs:
            raise ValueError('至少需要 1 个事件（FA2 里空触发不可评估）')
        # ★§181：`create_trigger` 只按**第一个**事件算 ticks（13 ⇒ arg×15，其余 ⇒ 0），
        #   而 ticks>0 才登记「定时触发器挂接表」。调用方把计时事件写在后面时
        #   （如 [(36,3),(13,6)]）就不登记 ⇒ 整条触发读档后不动。
        #   ⇒ 只要列表里有计时事件，就把它提到第一位。AND 语义不变（只影响短路顺序）。
        for _k, (_t, _a) in enumerate(evs):
            if _t == HEARTBEAT_EVENT:
                if _k:
                    evs.insert(0, evs.pop(_k))
                break
        _has_tick = any(t == HEARTBEAT_EVENT for t, _ in evs)
        _hb_warn = ''
        if not _has_tick and any(t in POLL_EVENT_TYPES for t, _ in evs):
            _hb_warn = ('⚠ 本触发含事件 36/37（纯电平查询），却没有计时事件 ⇒ 不进'
                        '「定时触发器挂接表」，读档后**不会被评估**。'
                        '建议加一条「13 流逝时间」当心跳（如 (13,1)）。')
        acts = []
        for a in (actions or []):
            if isinstance(a, dict):
                acts.append({'type': int(a['type']), 'arg': int(a.get('arg') or 0),
                             'name': a.get('name'), 'waypoint': a.get('waypoint'),
                             'cell': a.get('cell'),
                             # ★§255：80/107 的小队键（走 `+0x44`，不能塞进 arg）
                             'team': a.get('team')})
            else:
                acts.append({'type': _pair(a)[0], 'arg': _pair(a)[1]})

        first_ac = acts[0] if acts else None
        # ★§255：第一条行为若是"援军型 + 指定了小队"，不能交给 `create_trigger` 的第一条
        #   去建（那条只认 ac_name/waypoint/cell，写不了小队）⇒ 先建**空触发**，
        #   所有行为统一用 `add_action(team=…)` 追加。
        _post_first = bool(first_ac and first_ac['type'] in self.SPAWN_ACTIONS
                           and first_ac.get('team'))
        info = self.create_trigger(
            name, evs[0][0], evs[0][1],
            None if (first_ac is None or _post_first) else first_ac['type'],
            0 if (first_ac is None or _post_first) else first_ac['arg'],
            ac_name=first_ac.get('name') if (first_ac and not _post_first) else None,
            ac_waypoint=first_ac.get('waypoint') if (first_ac and not _post_first) else None,
            ac_cell=first_ac.get('cell') if (first_ac and not _post_first) else None,
            hb_warn=False,          # 多事件版自己按全量事件判断心跳，避免重复提示
            **kw)
        sid = info['trigger_id']
        warns = list(info.get('warnings') or [])
        if _hb_warn:
            warns.append(_hb_warn)
        for t, a in evs[1:]:
            try:
                self.add_event(sid, t, a)
            except Exception as e:
                warns.append('追加事件失败（类型 %s）：%s' % (t, e))
        for a in (acts if _post_first else acts[1:]):
            try:
                self.add_action(sid, a['type'], a['arg'],
                                name=a.get('name'), waypoint=a.get('waypoint'),
                                cell=a.get('cell'), team=a.get('team'))
            except Exception as e:
                warns.append('追加行为失败（类型 %s）：%s' % (a['type'], e))
        # 回读实际结果（以引擎口径的链序为准，别信我们自己的记账）
        try:
            ch = self.trigger_event_list(sid)
        except Exception:
            ch = []
        try:
            acs = [a for a in self.trigger_action_list(sid)]
        except Exception:
            acs = []
        info['events'] = ch
        info['actions'] = acs
        info['warnings'] = warns
        return info

    def make_local_var_recipe(self, targets, result_actions, var_start=0,
                              result_name='汇总条件', per_name=None, owner=None,
                              attach_all=True, attach_mode='zero', per_event=1,
                              per_arg=0, var_name=None,
                              heartbeat=True, heartbeat_sec=HEARTBEAT_SEC):
        """**一键「局部变量法」**（§158 配方，社区通行写法）→ dict。

        场景：想让「N 栋建筑**全部**进入/到达」才执行结果。**别**用「持久 1 + 进入事件」
        ——§159 实测那是引擎 BUG（进入事件摘不掉对象 ⇒ 计数永不减 ⇒ 永不执行）。
        正解是拆成 N+1 条触发：

          · 每栋一条：`持久 0` · 事件 1（进入事件，挂接该栋）→ 行为 56（设置局部变量 V_i）
          · 汇总一条：`持久 0` · **N × 事件 36**（局部变量 V_i 被设置，AND 关系）→ 结果行为

        `targets`        = `[(x, y), …]` 建筑格（每栋一个变量）
        `result_actions` = 汇总触发要执行的行为，格式同 `create_trigger_multi`
        `var_start`      = 起始变量号，**用引擎索引（0 起）**，默认 0。
                           ⚠ 地编名称框里显示的是"第 N 个"（1..99）⇒ 引擎索引 = N−1
        `var_name`       = 变量名模板（`%d` = 地编编号，即 1..99）；不给就补 `V<编号>`
                           ★§175：**变量必须先有名字**（地编流程：先写局部变量 → 触发里才选得中）
                           ，本函数会自动把名字写进变量表（已有的名字不动）
        `per_name`       = 单栋触发名模板（`%d` = 变量号，如 `'到达%d'`）
        `result_name`    = 汇总触发名
        `attach_mode`    = 建筑已挂在别的触发上时怎么办，见 `free_attach_slot()`
                           （默认 `'zero'` = 拒绝并跳过那栋，不建"永不满足"的废触发）
        `per_event`      = 单栋触发用的**事件类型**，默认 1（进入事件）。常用：
                           `48` 被任何事物摧毁（摧毁/占领/心控/变卖都算）、
                           `29` 被摧毁或占领（不含变卖）、`7` 只算摧毁
                           ⇒ 用 48 就是"每栋被打掉一个变量 + 汇总全掉才算数"，
                             同样是**持久 0**，绕开 §163 的持久 1 丢账坑。

        ★持久性**强制 0**：§157 实测「持久 1 + 局部变量事件 36/37」永不触发。
        ★§181（T10 定案）：**汇总触发一定带一条「13 流逝时间」当心跳**（默认 1 秒）。
          事件 36 是纯电平查询，没有对象回调 —— 只有进了「定时触发器挂接表」（`ticks>0`，
          即**第一个事件是 13**）才会被轮询。只挂 36 的汇总触发读档后完全不动
          （工具/FA2 里看着一切正常，进游戏就是不出）。
          `heartbeat=False` 可关，但会明确警告；`heartbeat_sec` 可改心跳秒数。
        返回 `{'per_building': [info…], 'result': info, 'vars': [...],
               'local_vars': [...], 'warnings': [...]}`。
        """
        tg = [(int(t[0]), int(t[1])) for t in (targets or [])]
        if not tg:
            raise ValueError('targets 不能为空（至少要挂 1 栋建筑）')
        if len(tg) > 24:
            raise ValueError('局部变量法一次最多 24 栋（再多请分批，别把触发表撑爆）')
        out = {'per_building': [], 'result': None, 'vars': [], 'warnings': [],
               'skipped': [], 'local_vars': []}
        out['map'] = self.scenario_map_path()
        if not out['map']:
            out['warnings'].append(
                '⚠ 找不到地图文件（' + self.map_lookup_hint() + '）⇒ 变量只能写进存档镜像，'
                '读档会被地图覆盖（若地图里本来没有这些变量，事件 36 会读不到改变）')
        for i, (x, y) in enumerate(tg):
            if attach_all:
                f, why = self.free_attach_slot(x, y, mode=attach_mode)
                if f is None:
                    out['warnings'].append(why)
                    out['skipped'].append((x, y, why))
                    continue        # 不建"永远等不到"的废触发
            v = int(var_start) + len(out['per_building'])
            if v >= LOCALVAR_COUNT:
                out['warnings'].append('变量号用尽（引擎只认 0..%d），后面 %d 栋跳过'
                                       % (LOCALVAR_COUNT - 1, len(tg) - i))
                break
            # ★§176：先把变量**定义进地图**（名字 + 默认值 0），再建触发 —— 与地编顺序一致
            try:
                nm_var = (str(var_name) % (v + 1)) if var_name else None
                lv = self.ensure_local_var(v, name=nm_var, value=None,
                                           )
                out['local_vars'].append(lv)
                if lv.get('source') == 'map' and (lv.get('default') or 0):
                    out['warnings'].append(
                        '变量 %d（%s）在地图里的默认值是 %d ⇒ 事件 36 会一读档就成立，'
                        '汇总触发可能立刻跑；建议换一个默认值为 0 的号'
                        % (v, lv.get('name') or lv.get('name_map'), lv.get('default')))
            except Exception as e:
                out['warnings'].append('变量 %d 定义失败：%s' % (v, e))
            if per_name:
                try:
                    nm = per_name % v
                except TypeError:
                    try:
                        nm = per_name % (i + 1)
                    except TypeError:
                        nm = per_name
            else:
                nm = '到达%d' % v
            nm = str(nm)[:12]
            try:
                # ★§181：单栋触发通常用回调型事件（1/48/29/7）→ 不需要心跳；
                #   但若调用方给了 36/37（纯轮询型），同样得挂心跳，否则永不评估。
                per_evs = [(int(per_event), int(per_arg))]
                if int(per_event) in POLL_EVENT_TYPES:
                    per_evs.insert(0, (HEARTBEAT_EVENT, int(heartbeat_sec)))
                info = self.create_trigger_multi(
                    nm, per_evs, [(56, v)],
                    attach=[(x, y)] if attach_all else None,
                    owner=owner, persistence=0)
                info['var'] = v
                info['cell'] = (x, y)
                info['name'] = nm
                out['per_building'].append(info)
                out['vars'].append(v)
                out['warnings'].extend(info.get('warnings') or [])
            except Exception as e:
                out['warnings'].append('第 %d 栋 (%d,%d) 建档失败：%s' % (i + 1, x, y, e))
                out['skipped'].append((x, y, str(e)))
        ok_vars = list(out['vars'])
        if not ok_vars:
            raise RuntimeError('所有单栋触发都没建成功，已中止（汇总触发未建）：%s'
                               % '；'.join(out['warnings'][:3]))
        # ★§181（T10 定案）：事件 36 是**纯电平查询** —— 没有对象回调可依赖，
        #   只有进了「定时触发器挂接表」才会被轮询；而登记条件只有一个：
        #   **第一个事件是 13 且 arg > 0**（`ticks = arg × 15`）。
        #   ⇒ 汇总触发若只有一串 36，读档后**永远不会被评估**（本函数此前的隐性 bug：
        #     生成的配方在工具里看完全正常，进游戏建筑就是不出）。
        #   修法：在**列表首位**放一条 13 心跳（create_trigger 正是拿首位算 ticks）。
        evs = [(36, v) for v in ok_vars]
        if heartbeat:
            evs.insert(0, (HEARTBEAT_EVENT, int(heartbeat_sec)))
        else:
            out['warnings'].append(
                '⚠ 已按参数不加心跳：汇总触发只有事件 36 ⇒ 不进「定时触发器挂接表」，'
                '读档后不会执行（除非另有触发/对象回调带它）。')
        try:
            out['result'] = self.create_trigger_multi(
                str(result_name or '汇总条件')[:12], evs, result_actions,
                owner=owner, persistence=0)
            out['warnings'].extend(out['result'].get('warnings') or [])
        except Exception as e:
            out['warnings'].append('汇总触发建立失败：%s' % e)
        if out['skipped']:
            out['warnings'].append(
                '有 %d 栋没参与 ⇒ 汇总条件只含 %d 个变量，按"剩下这些全部满足"生效'
                % (len(out['skipped']), len(ok_vars)))
        return out

    # ---------- 搜索 ----------
    def search(self, text, limit=200):
        try:
            if re.fullmatch(r'(?:[0-9A-Fa-f]{2}\s*)+', text.strip()):
                pat = bytes.fromhex(re.sub(r'\s', '', text))
            else:
                pat = text.encode('gbk', 'ignore')
        except Exception:
            return []
        if not pat:
            return []
        out = []
        q = 0
        while len(out) < limit:
            i = self.plain.find(pat, q)
            if i < 0:
                break
            out.append(i)
            q = i + 1
        return out

    # ---------- 保存 ----------
    def dirty(self):
        return (self.pending_money is not None or bool(self.pending_houses)
                or bool(self.pending_triggers) or bool(self.pending_events)
                or bool(self.pending_actions) or bool(self.pending_attach)
                or bool(getattr(self, 'pending_bld', False))
                or getattr(self, 'pending_newtrig', False))

    def modified(self):
        """明文是否和**打开时**不一样 —— 判断"有没有未保存改动"的**权威判据**。

        ★2026-10-28（审查 P1-5）：`dirty()` 只统计 `pending_*` 那几类"延迟写"操作，
          而像 `set_waypoint` / `add_action` / `remove_building` 这类**直接改明文**的
          操作它看不到（实测 `set_waypoint` 改了明文但 `dirty()` 仍返回 False）⇒
          拿 `dirty()` 做"关窗提醒"会漏报。这里直接比明文，C 层比较，
          26.7MB 实测 2.2ms（有差异时 0.02ms 就短路返回）。
        """
        try:
            return self.plain != self.plain_orig
        except Exception:
            return self.dirty()

    def diff(self):
        """→ `[(偏移, 旧字节, 新字节), ...]`。

        ★2026-10-28（审查 P3-16）：原来是 `for i in range(len(a))` 逐字节扫 26MB 明文，
          一次要 5~10 秒（还是纯 Python 循环）。改成**先按 64KB 块比**，只对不相同的块
          逐字节扫 —— 常见改动只有几处，耗时降到毫秒级。返回格式不变（`_selftest.py` 在用）。
        """
        a, b = self.plain_orig, bytes(self.plain)
        if len(a) != len(b):
            # 增删对象会改变明文长度 ⇒ 退化到逐字节（只在自检脚本里会走到）
            n = min(len(a), len(b))
            return [(i, a[i], b[i]) for i in range(n) if a[i] != b[i]]
        out = []
        CH = 65536
        for base in range(0, len(a), CH):
            sa = a[base:base + CH]
            sb = b[base:base + CH]
            if sa == sb:
                continue
            for i in range(len(sa)):
                if sa[i] != sb[i]:
                    out.append((base + i, sa[i], sb[i]))
        return out

    def _rechunk(self, new_plain):
        """按 65536 重切 CONTENTS（**与游戏一致**），返回 (新 CONTENTS, 重编码块数)。

        ★★★ 2026-10-14 玩家游戏内实测定案（**弹框的根因就在这一环**）：
          · 游戏写任何档都是「明文按 65536 切、块头 unpacked **一律 65536**（末块可短于声明值）」；
          · 旧写法在明文变长时**多追加一个小块**（157/158 块、unpacked=660/168）
            ⇒ **进游戏 internal error**；对照实测 Y2/Y4(156 块) 能进图、Y3(158 块) 崩。
        · 未改动的块**直接沿用原 packed 字节**（既保留游戏自己的 LZO 压缩率，又让物理改动最小）。
        """
        cc = self.contents
        ranges = self.ranges
        orig = self.plain_orig
        out = bytearray()
        n = (len(new_plain) + CONTENTS_CHUNK - 1) // CONTENTS_CHUNK
        recompressed = 0
        changed = []            # [(out 内偏移, 明文段)] 供分块回验
        # 第一遍：定下"哪些块要重压"，把要压的段收集起来（便于并行）
        plan = []               # [(块号, 段)] 或 ('reuse', packed 字节)
        todo = []
        for k in range(n):
            a = k * CONTENTS_CHUNK
            seg = new_plain[a:a + CONTENTS_CHUNK]
            # 与**原明文**逐字节相同 ⇒ 该块没被动过 ⇒ 直接沿用游戏自己的 packed 字节
            # ★三个条件缺一不可：
            #   ① 原块起点 == 本块起点（对齐）；
            #   ② 原块**解出来的长度** == 本块长度（否则原块是「短块」，复用它会把
            #      本块后半截的明文**静默丢掉** —— 实测踩过：金标准档尾两个 168/660 短块
            #      ⇒ 少 828 字节）；
            #   ③ 原块 unpacked 声明 == 65536（旧工具写出的坏块一律不复用）。
            if k < len(ranges) and ranges[k][0] == a and ranges[k][1] == len(seg) \
                    and orig[a:a + len(seg)] == seg:
                cpos = ranges[k][2]
                pk2, uk2 = struct.unpack_from('<II', cc, cpos)
                if uk2 == CONTENTS_UNPACKED:
                    plan.append(('reuse', cc[cpos:cpos + 8 + pk2]))
                    continue
            plan.append(('pack', len(todo)))
            todo.append(bytes(seg))
        # 第二遍：压缩（有 C 后端就串行调用，它够快；没有才上多进程 + 纯 Python）
        packed = None
        try:
            import lzo_fast
            fast = lzo_fast.available()
        except Exception:
            fast = False
        if not fast and len(todo) >= 48:
            packed = _parallel_compress(todo)
        if packed is None:
            packed = [compress(s) for s in todo]
        for item in plan:
            if item[0] == 'reuse':
                out += item[1]
                continue
            comp = packed[item[1]]
            changed.append((len(out), todo[item[1]]))
            out += struct.pack('<II', len(comp), CONTENTS_UNPACKED) + comp
            recompressed += 1
        out = bytes(out)
        # ★自证（2026-10-17 20:5x 改成"只回验重压过的块"）：
        #   以前把整档 39MB 解一遍做自证 —— 大任务档光这一步就要 2.6 秒。
        #   未改动的块是**原样拷贝**（且已逐块确认过起点/长度/声明），本来就等于原明文；
        #   真正需要回验的只有 `compress()` 新写出来的那几块 ⇒ 逐块解回来比对即可，
        #   保证力度不降（仍然是"压缩→解压→逐字节相等"）。
        for off, seg in changed:
            pk, uk = struct.unpack_from('<II', out, off)
            back, _ = lzo1x_decompress(out[off + 8:off + 8 + pk], uk)   # 带期望长度 ⇒ 走 C 后端
            if back != seg:
                raise RuntimeError('重切块自检失败：第 %d 字节起的块解回 %d 字节 ≠ 原文 %d 字节'
                                   % (off, len(back), len(seg)))
        if len(out) == 0:
            raise RuntimeError('重切结果为空')
        return out, recompressed

    @staticmethod
    def _block_layout(cc):
        """数 CONTENTS 里的块 → `(块数, [(序号, 声明 unpacked), ...] 异常块)`。

        2026-10-28 从 `save()` 里抽出来，好让"写盘前"和"回读后"共用同一份判据。
        """
        p, n, bad = 0, 0, []
        while p + 8 <= len(cc):
            pk, uk = struct.unpack_from('<II', cc, p)
            if pk == 0:
                break
            n += 1
            if uk != CONTENTS_UNPACKED:
                bad.append((n, uk))
            p += 8 + pk
        return n, bad

    def save(self, out_path, backup=True):
        """重建 CONTENTS 并写盘（**块布局与游戏一致**：65536 × N，无短尾块）。

        写入容器两条路：先试 `cfb_surgery`（原位改扇区/链，物理改动最小），
        写完**立即解码回验**；若回验不过（旧档 FAT 不干净会被它截断）则改用
        `cfb_write.rebuild`（从零重建容器）重写一遍 —— 两条路都回验，不允许静默写坏。

        ★★2026-10-27 铁律：**本工具的写盘只允许落到存档（.SAV）**。这里先挡一道：
          目标扩展名像地图（.map/.mmx/.yrm/.mpr）就直接抛 `MapWriteForbidden`，
          免得任何人（包括我以后改代码）把"另存为"指到地图上把玩家的图冲掉。
        """
        _low = str(out_path).lower()
        if _low.endswith(('.map', '.mmx', '.yrm', '.mpr')):
            raise MapWriteForbidden(MAP_WRITE_REFUSED + '（目标路径 %s 是地图文件）' % out_path)
        # ★§288：上一次编辑留下了"清不掉的键引用" ⇒ 这份档进游戏必弹框，不许写盘
        _left = getattr(self, '_unsafe_leftover', None)
        if _left:
            raise RuntimeError(
                '拒绝保存：还有 %d 处对象键引用没剔干净（%s）。\n'
                '带着它们进游戏会弹 Internal error。请用「清理残留引用」重试，'
                '或者撤销这次改动后换新版重新操作。'
                % (len(_left), '、'.join('%08X' % k for k in list(_left)[:3])))
        # ★2026-10-28（审查 P3-15）：原来这里 `bytes(self.plain)` 复制了两次
        #   （一次给 new_plain，一次只为和 plain_orig 比大小）—— 26MB 明文白抄一遍。
        #   bytearray 可以直接和 bytes 比较，省掉第二次复制。
        new_plain = bytes(self.plain)
        if self.plain == self.plain_orig:
            raise RuntimeError('没有任何修改')
        new_cc, recompressed = self._rechunk(new_plain)
        if not new_cc:
            raise RuntimeError('重建 CONTENTS 为空')
        # ★★2026-10-28（审查 P0-3）：块布局自检以前是"只算不判"——`bad_declared`/
        #   `blocks_expect` 算出来后塞进返回值，全项目**没有任何调用方读它**，块数不符
        #   照样返回成功。docstring 写着"块数必须与游戏一致"，那就得真的卡住。
        #   放在**写盘前**判：不通过就一个字节都不落盘，不会给玩家留半份坏档。
        _nb, _bad = self._block_layout(new_cc)
        _expect = (len(new_plain) + CONTENTS_CHUNK - 1) // CONTENTS_CHUNK
        if _bad or _nb != _expect:
            raise RuntimeError(
                '块布局自检不通过，已拒绝写盘：块数 %d（应为 %d，明文 %d B ÷ %d），'
                '声明 unpacked 异常的块 %r'
                % (_nb, _expect, len(new_plain), CONTENTS_CHUNK, _bad[:5]))
        if backup and os.path.abspath(out_path) == os.path.abspath(self.path):
            bdir = os.path.join(os.path.dirname(os.path.abspath(self.path)), '_备份存档')
            os.makedirs(bdir, exist_ok=True)
            ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
            bak = os.path.join(bdir, os.path.basename(self.path) + '.bak-' + ts)
            shutil.copy2(self.path, bak)
        # ★2026-10-17 23:3x：surgery 自带的 read-back 断言在某些"引擎重写过"的档上会失败
        #   （实测 SAVE0364：插字节后读回不一致）。它是"最小侵入"路线，失败不该中断保存 ——
        #   下面本来就有 end-to-end 校验 + 从零重建容器（cfb_write.rebuild）的兜底，
        #   所以这里把异常接住，交给兜底路径。
        try:
            cfb_surgery.surgery(self.path, out_path, [('rep', 0, self.contents, new_cc)])
            surgery_err = None
        except Exception as e_surg:
            surgery_err = e_surg
        # 终验：重新解码并比对明文；不过关则改用「从零重建容器」重写一遍
        def _verify(p):
            raw = open(p, 'rb').read()
            pl2, _ = self._decode(CFB(raw).open('CONTENTS'))
            return raw, pl2

        writer = 'surgery'
        try:
            chk_raw, pl2 = _verify(out_path)
        except Exception:
            pl2 = None
            chk_raw = b''
        if surgery_err is not None or pl2 != new_plain:
            # ★surgery 可能已经写了一半（assert 在写完之后才校验）⇒ 先删掉残档再重建
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except Exception:
                pass
            info = cfb_write.rebuild(self.path, {'CONTENTS': new_cc}, out_path)
            chk_raw, pl2 = _verify(out_path)
            writer = 'rebuild(%d 扇区)' % info['sectors']
            if pl2 != new_plain:
                raise RuntimeError('保存后校验失败：解码明文不一致（surgery 与 rebuild 都不通过）'
                                   + ('；surgery 异常：%s' % surgery_err if surgery_err else ''))
        # 分块自检：块数必须与游戏一致（明文/65536 向上取整），块头 unpacked 恒 65536
        cc2 = CFB(chk_raw).open('CONTENTS')
        nblocks, bad = self._block_layout(cc2)
        expect = (len(new_plain) + CONTENTS_CHUNK - 1) // CONTENTS_CHUNK
        # 写盘前已经判过一次；这里是**落盘后回读**的二次确认（容器层可能改过块表）
        if bad or nblocks != expect:
            try:
                if os.path.abspath(out_path) != os.path.abspath(self.path):
                    os.remove(out_path)      # 别把坏档留在玩家选的位置上
            except Exception:
                pass
            raise RuntimeError(
                '保存后回读的块布局不对，已删除该输出文件：块数 %d（应为 %d），异常块 %r'
                % (nblocks, expect, bad[:5]))
        res = {'out': out_path, 'recompressed': recompressed,
               'contents': (len(self.contents), len(new_cc)),
               'file': len(chk_raw), 'md5': hashlib.md5(chk_raw).hexdigest(),
               'blocks': nblocks, 'blocks_expect': expect, 'writer': writer,
               'bad_declared': bad}
        # ★★2026-10-28（审查 P1-5 连带修）：保存成功后把"基准"推进到刚写出的状态。
        #   以前这里什么都不更新，后果有三：
        #     ① `plain_orig` 还是**打开时**的明文 ⇒ `modified()` 永远 True，
        #        标题栏的 `*未保存` 标记再也消不掉、关窗一直追问；
        #     ② 另存为到新文件后 `self.path` 仍指原档 ⇒ 再点「原地保存」会去覆盖**原档**；
        #     ③ `self.contents` 还是旧容器流 ⇒ 第二次保存的 surgery 定位会落空（白走 rebuild）。
        self.path = out_path
        self.plain_orig = new_plain
        self.plain = bytearray(new_plain)
        self.contents = new_cc
        self.raw = chk_raw                # 让 info['文件大小'] 等也跟着更新
        # `ranges` = [(明文偏移, 明文长度, 容器内偏移, packed 长度)]，`_rechunk` 下次靠它
        # 复用未改动块的原始压缩字节 ⇒ 必须重算成新容器的块表，否则第二次保存会定位错。
        try:
            rs, pos2, off2 = [], 0, 0
            while pos2 + 8 <= len(new_cc):
                pk2, uk2 = struct.unpack_from('<II', new_cc, pos2)
                if pk2 == 0:
                    break
                rs.append((off2, uk2, pos2, pk2))
                off2 += uk2
                pos2 += 8 + pk2
            if rs:
                self.ranges = rs
        except Exception:
            pass
        # 清掉"延迟写"账本，`dirty()` 也跟着归零
        self.pending_money = None
        self.pending_houses = {}
        self.pending_triggers = {}
        self.pending_events = {}
        self.pending_actions = {}
        self.pending_attach = {}
        self.pending_bld = False
        self.pending_newtrig = False
        try:
            self.invalidate_caches()
        except Exception:
            pass
        return res

    # ---------- 工具 ----------
    def hexdump(self, off, length=256):
        pl = bytes(self.plain)
        lo = max(0, off - length // 2)
        rows = []
        for o in range(lo, min(len(pl), lo + length), 16):
            seg = pl[o:o + 16]
            asc = ''.join(chr(x) if 32 <= x < 127 else '.' for x in seg)
            rows.append('%08X  %-47s  |%s|' % (o, seg.hex(' '), asc))
        return '\n'.join(rows)

    # ==================== 触发编辑器（仿 FA2「触发编辑器」）· 2026-10-20 ====================
    # 布局定案（SAVE48DB 逐项对拍，82/82）：
    #   · 实例对象 200B（GUID 91152dc0）= 记录帧 起点 +56；
    #   · 记录帧：+0 ID(8 ASCII) / +64 名字(52B,GBK) / +116 序号 / +120..123 标志字 /
    #     +128 国家(阵营 House 块 +72 的键)；
    #   · **标志字 4 字节**：[简单, 普通, 困难, 启用]（+120/+121/+122/+123）。
    #     启用位 = +123 = 实例帧 +179：与地图 `[Triggers]` 第 3 字段 DISABLED **82/82 反相对拍**。
    #     ⚠ 旧文档说"+123 只是副本、改了无效" —— 那是另一种（老）记录格式；本格式下它就是生效位。
    #   · 一个触发 = 实例 + 标签 + 1 个事件 + N 条行为（行为是链表，obj+0x3C = 下一条）+ TagNode + CellTag。
    TRIG_FLAG_REC_OFF = 123
    TRIG_SEQ_OFF = 172
    TRIG_FLAG_WORD_OFF = 120       # 记录帧；实例帧为 176

    def invalidate_caches(self, rescan=False):
        """把"跟着明文偏移走"的缓存全部作废（增删字节后必须调）。"""
        for a in ('_objs', '_events', '_actions', '_ticks', '_celltags', '_ctimerlist',
                  '_instmaps', '_house_econ', '_custom_houses', '_house_keys_memo',
                  '_houses_memo',          # ★P3-14：houses() 的结构 memo
                  '_tagrecs', '_mapm', '_aiids',
                  # ★2026-10-27：`_horder` 只是 [{'short','cn','is_player'}]，**与偏移无关**，
                  #   以前每次增删对象都清它 ⇒ 下次 `_arg_text` 要重算 1.3 秒（本档）。
                  #   实测这是「添加行为」里最大的一笔浪费，故从清理表里移除。
                  '_tagobjs', '_wpbase_memo',        # §273 两个全档扫描缓存
                  '_terrobjs', '_terrnames', '_terrsav', '_occ_memo',     # §281 Terrain
                  '_ovlcells', '_ovl_info', '_ovl_map',                   # §283 覆盖物/地图包
                  '_teamtypes', '_teamlbl',                              # §283.9 小队表缓存
                  '_ovl_memo', '_ovl_names',
                  # ★2026-10-28：武器/弹头类型表扫描（偏移相关，增删字节后必须重扫）
                  '_typeobjs_memo', '_wpnwh_memo'):
            if hasattr(self, a):
                setattr(self, a, None)
        if rescan:
            self.records = self._scan_records()
            self.pending_events.clear()
            self.pending_actions.clear()
            self.pending_attach.clear()

    @staticmethod
    def _gbk_name(text, span):
        """名字写入用的 GBK 编码（超长截断到 span-1 字节）。"""
        try:
            b = str(text).encode('gbk')
        except Exception:
            b = str(text).encode('utf-8', 'ignore')
        return b[:span - 1]

    def _read_name_at(self, off, span=52):
        raw = bytes(self.plain[off:off + span]).split(b'\x00')[0]
        for code in ('gbk', 'utf-8'):
            try:
                return raw.decode(code)
            except Exception:
                pass
        return raw.decode('gbk', 'replace')

    def _write_name_at(self, off, text, span=52):
        b = self._gbk_name(text, span)
        self.plain[off:off + span] = b'\x00' * span
        self.plain[off:off + len(b)] = b

    def trigger_inst_off(self, sid):
        """sid → 触发实例对象帧偏移；找不到返回 None（只有"定义"没有实例的老格式）。"""
        m = self._instance_maps()
        for k, s in m['sid'].items():
            if s == sid:
                return m['inst'].get(k)
        return None

    def _tag_objs_by_key(self):
        """标签自键 → 标签对象帧偏移。

        ★★§273：**必须缓存**。本档 92 个 CellTag 对象 ⇒ 每次调用 = 92 次全档 find
        （26MB 明文 ~14ms）。而 `trigger_objects()` 每次都会调它，触发表 495 行里有 98 行走到
        ⇒ 单这一项 **1.3 秒**。缓存键 = 明文长度，增删字节自动作废；`invalidate_caches()` 也清。
        """
        memo = getattr(self, '_tagobjs', None)
        if memo is not None and memo[0] == len(self.plain):
            return memo[1]
        pl = self.plain
        out, q = {}, 0
        while True:
            i = pl.find(TRIG_TAGOBJ_GUID, q)
            if i < 0:
                break
            q = i + 1
            out[int.from_bytes(pl[i + 16:i + 20], 'little')] = i
        self._tagobjs = (len(pl), out)
        return out

    def trigger_objects(self, sid):
        """把一条触发牵涉的全部对象偏移一次找齐（详情/复制/删除共用）。

        返回 dict：rec/rec_off/inst/inst_key/tag_key/tag_obj/ev/ev_size/acs/node/cell/cell_key
        找不到的项为 None（实例缺失 = 只有定义的老格式，本工具不做增删）。
        """
        pl = self.plain
        rec = self.trigger_rec(sid)
        if rec is None:
            raise RuntimeError('找不到触发 %s' % sid)
        out = {'sid': sid, 'name': rec.name, 'rec': rec, 'rec_off': rec.off,
               'inst': None, 'inst_key': None, 'tag_key': None, 'tag_obj': None,
               'ev': None, 'ev_size': None, 'acs': [], 'node': None, 'cell': None,
               'cell_key': None}
        inst = self.trigger_inst_off(sid)
        if inst is None:
            return out
        out['inst'] = inst
        out['inst_key'] = int.from_bytes(pl[inst + 16:inst + 20], 'little')
        m = self._instance_maps()
        tk = m['tag_of_inst'].get(out['inst_key'])
        out['tag_key'] = tk
        out['tag_obj'] = self._tag_objs_by_key().get(tk)
        evs = self.events()
        evkey = int.from_bytes(pl[inst + 192:inst + 196], 'little')
        ev = next((e for e in evs if e.get('key') == evkey), None)
        if ev is not None:
            out['ev'] = ev['obj']
            if len(evs) >= 2:
                out['ev_size'] = evs[1]['obj'] - evs[0]['obj']
        ack = int.from_bytes(pl[inst + 196:inst + 200], 'little')
        byk = {a.get('key'): a for a in self.actions()}
        for k in self.action_chain(ack):
            if k in byk:
                out['acs'].append((byk[k]['obj'], k))
        out['node'] = m['node_of_inst'].get(out['inst_key'])
        ck = m['cell_of_tag'].get(tk)
        out['cell_key'] = ck
        if ck is not None:
            for nd in self.celltag_nodes():
                if nd['x'] == ck:
                    out['cell'] = nd['node']
                    break
        return out

    def trigger_detail(self, sid):
        """读一条触发的全部可编辑字段 → dict（GUI「触发编辑器」用）。

        {'sid','name','index','owner','owner_key','disabled','easy','normal','hard',
         'repeat','repeat_text','event',{'type','arg','name','ptype','arg_text','obj','key'},
         'actions':[…同 event 结构…],'has_node','has_cell','ticks','attached',
         'editable','note'}
        """
        pl = self.plain
        o = self.trigger_objects(sid)
        rec = o['rec']
        d = {'sid': sid, 'name': rec.name, 'index': None, 'rec_off': rec.off,
             'owner': self.trigger_owner_name(rec), 'disabled': None,
             'easy': None, 'normal': None, 'hard': None,
             'repeat': None, 'repeat_text': '', 'event': None, 'actions': [],
             'has_node': o['node'] is not None, 'has_cell': o['cell'] is not None,
             'ticks': None, 'attached': 0, 'editable': o['inst'] is not None,
             'note': ''}
        p = self.rec_off_word = rec.off + self.TRIG_FLAG_WORD_OFF
        w = bytes(pl[p:p + 4])
        if len(w) == 4:
            d['easy'], d['normal'], d['hard'] = bool(w[0]), bool(w[1]), bool(w[2])
            d['disabled'] = (w[3] == 0)
        try:
            d['repeat'] = self.trigger_repeat_type(sid)
        except Exception:
            d['repeat'] = None
        d['repeat_text'] = (getattr(self, 'REPEAT_TEXT', {}).get(d['repeat'], '')
                            if d['repeat'] else '')
        if o['inst'] is not None:
            d['index'] = int.from_bytes(pl[o['inst'] + self.TRIG_SEQ_OFF:
                                              o['inst'] + self.TRIG_SEQ_OFF + 4], 'little')
            if o['node'] is not None:
                d['ticks'] = int.from_bytes(pl[o['node'] + NODE_TICKS_OFF:
                                               o['node'] + NODE_TICKS_OFF + 4], 'little')
        if o['cell_key'] is not None:
            # ★★2026-10-27（玩家："添加行为会卡，然后闪退"）：
            #   这里只需要"这一格挂接了 N 个对象"，以前却去调 `attach_overview()` ——
            #   那个函数对**每个 CellTag 节点**都在全档明文里 find 一次键（本档 200 节点 × 6MB
            #   ≈ 8 秒），而且它的缓存键是"明文长度"，我们每插一条行为长度就变 ⇒ 缓存全废
            #   ⇒ 编辑器里每点一次「添加」就卡 8 秒，看着像卡死。
            #   `celltag_nodes()` 本身就带 `count`，而且只按 GUID 扫、有缓存（毫秒级）。
            try:
                for nd in (self.celltag_nodes() or []):
                    if nd.get('x') == o['cell_key']:
                        d['attached'] = nd.get('count') or 0
                        break
            except Exception:
                pass
        evs = self.events()
        byk = {a.get('key'): a for a in self.actions()}
        # ★2026-10-20：事件对象 `+0x50..+0x68` 是**运行期指针区**：
        #   全 0 = 这个事件还没被引擎"接管"（事件驱动型触发本来就不需要 TagNode）；
        #   非 0 = 引擎已经处理过它（= 执行过）。
        #   对照实证：sample_AB 的 A（玩家确认开局即执行）无节点但这段非 0；
        #   SAVE0029 的 01000250（玩家确认**没**执行）无节点且这段全 0。
        d['ev_live'] = False
        if o['ev'] is not None:
            d['ev_live'] = any(pl[o['ev'] + 0x50:o['ev'] + 0x68])
        if o['inst'] is not None:
            evkey = int.from_bytes(pl[o['inst'] + 192:o['inst'] + 196], 'little')
            ev = next((e for e in evs if e.get('key') == evkey), None)
            if ev is not None:
                d['event'] = ev
        for obj, k in o['acs']:
            a = byk.get(k)
            if a is not None:
                # ★§219.8：行为 80/107（援军类）的真正参数**不在默认槽**：
                #   小队键在 `+0x44`、路径点在 `+0x58`（`+0x48` 恒 0）。
                #   GUI 原来只显示 +0x48 ⇒ 看着"参数 0、什么都没有"，玩家找不到小队/路径点。
                # ★★§283.9：4 建立 / 5 解散 / 7 援军 也是**小队型**（小队键同样在 +0x44），
                #   以前只认 80/107 ⇒ 触发编辑器里这三类"选不了小队"。
                if self.is_team_action(a.get('type')):
                    tk = int.from_bytes(pl[obj + 0x44:obj + 0x48], 'little')
                    wp = (int.from_bytes(pl[obj + 0x58:obj + 0x5C], 'little')
                          if a.get('type') in self.SPAWN_ACTIONS else 0)
                    tmn = ''
                    if tk:
                        for _t in self.team_types():
                            if _t['key'] == tk:
                                tmn = _t['name'] or _t['sid']
                                break
                    try:
                        cell = self.waypoint_cell(wp) if wp else None
                    except Exception:
                        cell = None
                    a = dict(a)
                    a['team_key'] = tk
                    a['team'] = tk
                    a['waypoint'] = wp
                    a['cell'] = cell
                    if a.get('type') in self.SPAWN_ACTIONS:
                        a['arg_text'] = ('小队 %s%s @ 路径点 %s%s'
                                         % ('%08X' % tk, ('「%s」' % tmn) if tmn else '',
                                            wp, (' 格%s' % (cell,)) if cell else ''))
                    else:
                        a['arg_text'] = ('小队 %s%s'
                                         % ('%08X' % tk, ('「%s」' % tmn) if tmn else ''))
                d['actions'].append(a)
        if not d['editable']:
            d['note'] = '这条记录在本档里只有「触发定义」，没有运行期实例 ⇒ 只能看'
        return d

    def trigger_event_live(self, sid):
        """事件对象是否带运行期指针（= 引擎已经处理过这个事件）。

        这是区分「已执行」与「事件驱动型、还没轮到」的关键：
        后者**本来就没有 TagNode**（被攻击/被摧毁这类事件靠对象回调，不靠计时器）。
        """
        try:
            o = self.trigger_objects(sid)
        except Exception:
            return False
        if o['ev'] is None:
            return False
        return any(self.plain[o['ev'] + 0x50:o['ev'] + 0x68])

    def set_trigger_name(self, sid, name):
        """改触发名（记录帧 +64，52B，GBK）；标签名同步成「<名> N」（沿用原来的序号后缀）。"""
        name = str(name).strip()
        if not name:
            raise ValueError('触发名不能为空')
        if len(name.encode('gbk', 'ignore')) > 51:
            raise ValueError('名字太长：GBK 最多 51 字节（约 25 个汉字）')
        o = self.trigger_objects(sid)
        if o['inst'] is None:
            raise RuntimeError('该触发没有运行期实例（只有定义），不能改名')
        old = o['name']
        self._write_name_at(o['inst'] + 120, name)
        tag = o['tag_obj']
        if tag is not None:
            tag_old = self._read_name_at(tag + 120)
            suffix = tag_old[len(old):].strip() if tag_old.startswith(old) else ''
            if not suffix.isdigit():
                suffix = '1'
            self._write_name_at(tag + 120, '%s %s' % (name, suffix))
        self.invalidate_caches(rescan=True)
        return {'old': old, 'new': name}

    def set_trigger_flags(self, sid, disabled=None, easy=None, normal=None, hard=None):
        """禁用 / 简单 / 普通 / 困难 —— 写记录帧 +120..+123 这 4 个字节。

        ★启用位（+123）已与地图 `[Triggers]` DISABLED 字段 **82/82** 反相对拍；
          +120/+121/+122 按地图字段顺序（EASY, NORMAL, HARD）对应"简单/普通/困难"，
          ⚠ 这三位的**先后顺序尚未游戏内验证**（现有档全是 1/1/1）。
        """
        rec = self.trigger_rec(sid)
        if rec is None:
            raise RuntimeError('找不到触发 %s' % sid)
        p = rec.off + self.TRIG_FLAG_WORD_OFF
        w = list(self.plain[p:p + 4])
        nop = self.plain[p:p + 4]
        if len(w) != 4:
            raise RuntimeError('标志字越界，已拒绝写入')
        if easy is not None:
            w[0] = 1 if easy else 0
        if normal is not None:
            w[1] = 1 if normal else 0
        if hard is not None:
            w[2] = 1 if hard else 0
        if disabled is not None:
            w[3] = 0 if disabled else 1
        self.plain[p:p + 4] = bytes(w)
        rec.enabled = bool(w[3])
        rec.flag_off = p + 3
        rec.note = '启用位@+%d = %d' % (p + 3 - rec.off, w[3])
        return {'old': bytes(nop), 'new': bytes(w)}

    # ---------- 行为（可多条，链表） ----------
    def trigger_action_list(self, sid):
        """触发的行为链 → [{'type','arg','name','key'}]（按执行序，链头取实例 +196）。"""
        d = self.runtime_links().get(sid)
        if not d:
            return []
        by_key = {a.get('key'): a for a in self.actions() if a.get('key')}
        out = []
        for k in self.action_chain(d.get('ac')):
            a = by_key.get(k)
            if a:
                out.append({'type': a.get('type'), 'arg': a.get('arg'),
                            'name': a.get('name'), 'key': k})
        return out

    def add_event(self, sid, etype, arg=0):
        """给触发**追加一个事件**（接**链尾**）→ dict。

        ★方向（§155.3 全档统计定案）：**接链尾**。
          地图自带的档是"链头插"写出来的（`TEventClass` 链上 seq 递减 `[84,83,82]`），
          所以**地图第 1 个事件落在链尾**；而本工具 GUI 的显示顺序 = **链序 = 引擎评估
          顺序**（`_fill_events` 里写死"从上到下 = 评估顺序"）。两者叠加 ⇒ 往已有触发
          加事件，只有接**链尾**才满足两件事：
            ① 新事件排在列表最后（= 引擎最后评估），不动已有事件的短路顺序；
            ② 不会把「13 流逝时间」心跳挤出链头（§181：36/37 触发的心跳必须第一位）。
          （2026-10-24 说明：§219 那天为修撞号把它改成过"头插"，方向其实是退步，
            §193 自检 `[7] 新事件接在链尾` 也一直是按链尾写的。）

        ★序号（§219 定案，这条才是真 bug）：事件对象 `+0x38`(=+56) 是**事件表内物理
          序号**（实测 0..N-1 连续唯一），不是"所属触发的序号"。写触发序号会与表里
          同号旧对象撞号 ⇒ 引擎定位到别人的旧对象。这里写 **表内 max+1**（追加到组尾，
          物理上就是 max+1）。

        `ev_size` 优先按档内实测（`events[1].obj - events[0].obj`，普通档 108 / 大任务档 124），
        档里只有 1 个事件时退回 `_newtrig_groups()` 的推断值。
        克隆源 = 档内**最后一个**事件对象；运行期指针（`+40` 起）全部清零，
        只留身份签名 + 类常量 + 类型 + 参数 + 序号。
        """
        pl = self.plain
        etype, arg = int(etype), int(arg)
        if etype == 0 or etype not in EVENT_TYPES:
            raise ValueError('事件类型 %s 无效（0 是占位项，禁止使用）' % etype)
        o = self.trigger_objects(sid)
        inst = o['inst']
        if inst is None:
            raise RuntimeError('该触发没有运行期实例，不能加事件')
        evs = self.events()
        if not evs:
            raise RuntimeError('档内没有可克隆的事件对象')
        ev_size = (evs[1]['obj'] - evs[0]['obj']) if len(evs) >= 2 else None
        if not ev_size or ev_size < 64:
            ev_size = self._newtrig_groups()['ev_size']
        tpl = evs[-1]['obj']
        if not ev_size or tpl + ev_size > len(pl):
            raise RuntimeError('量不出事件对象尺寸（本档事件不是连续排放）—— 已拒绝')
        # 新键：与事件/行为同区，全档查重
        keys = [e.get('key') for e in evs if e.get('key')] + \
               [a.get('key') for a in self.actions() if a.get('key')]
        k = ((max(keys) + 0x1000) if keys else 0x02A75020) & 0xFFFFFFFF
        while pl.find(struct.pack('<I', k)) >= 0:
            k = (k + 0x1000) & 0xFFFFFFFF
        # 旧链头（实例在触发组里，位置低于事件组 ⇒ 插入不会推移它）
        old_head = int.from_bytes(pl[inst + 192:inst + 196], 'little')
        # ★§219：`+0x38` 是**事件表内物理序号**（不是触发序号）—— 追加到组尾 ⇒ max+1。
        #   旧代码写触发序号会与表里同号旧事件撞号（SAVE0029 实测 0..92 连续唯一）。
        ev_ord = max(int.from_bytes(pl[e['obj'] + 56:e['obj'] + 60], 'little')
                     for e in evs) + 1
        buf = bytearray(pl[tpl:tpl + ev_size])
        for off in range(40, ev_size, 4):            # 清运行期指针（保留 0..+36 身份签名）
            buf[off:off + 4] = b'\x00' * 4
        buf[16:20] = struct.pack('<I', k)
        buf[56:60] = struct.pack('<I', ev_ord)       # ★事件表内序号（max+1，§219）
        buf[OBJ_TYPE_OFF:OBJ_TYPE_OFF + 4] = struct.pack('<I', etype)
        buf[OBJ_ARG_OFF:OBJ_ARG_OFF + 4] = struct.pack('<I', arg)
        buf[self.EVENT_NEXT_OFF:self.EVENT_NEXT_OFF + 4] = struct.pack('<I', 0)  # 链尾
        pl[tpl + ev_size:tpl + ev_size] = bytes(buf)
        # ★接**链尾**（见 docstring / §155.3）：改链尾的 next，不动链头；
        #   链是空的（实例 +192 为 0）才把它当链头。
        if not old_head:
            pl[inst + 192:inst + 196] = struct.pack('<I', k)
        else:
            byk = {e.get('key'): e for e in evs if e.get('key')}
            tail = byk.get(self.event_chain(old_head)[-1]) if self.event_chain(old_head) else None
            if tail is None:
                raise RuntimeError('事件链异常（链尾对象找不到）—— 请勿保存')
            pl[tail['obj'] + self.EVENT_NEXT_OFF:
               tail['obj'] + self.EVENT_NEXT_OFF + 4] = struct.pack('<I', k)
        self.invalidate_caches(rescan=True)
        evs2 = self.events()
        if evs2:
            p0 = evs2[0]['obj'] - 4
            n = int.from_bytes(pl[p0:p0 + 4], 'little')
            pl[p0:p0 + 4] = struct.pack('<I', n + 1)
        self.invalidate_caches(rescan=True)
        return {'key': k, 'obj': tpl + ev_size, 'type': etype, 'arg': arg,
                'next': 0, 'chain_head': int.from_bytes(
                    pl[inst + 192:inst + 196], 'little'), 'ev_size': ev_size}

    def add_action(self, sid, atype, arg=0, name=None, waypoint=None, cell=None, team=None):
        """给触发**追加一条行为**（§193 修正版）。

        ★2026-10-23 修正（原实现在 7MB 小档上"静默无效"的根因）：
          行为的顺序由 **`+0x38` 全局序号**决定，序号 = 它在动作数组里的**物理下标**；
          某个触发的行为区间 = [它的首序号, 下一条触发的首序号)。
          原实现把新对象**追加到全表末尾**、却把 `+0x38` 写成"触发自己的序号" ⇒
          序号与位置对不上 ⇒ `trigger_actions()` 根本看不见它（看起来"没生效"）。
          正确做法：**插到该触发最后一条行为之后**，序号 = start+count，并把
          序号 ≥ 新序号的所有行为各 +1（它们在数组里整体后移了一位）。

        ★行为 125「将建筑建于…」需要 `name`（建筑类型名，8B ASCII 写 `+104`）+
          `waypoint`（路径点号，写 `+88`）或 `cell=(x,y)`（该格没路径点就自动建一个）。
        """
        pl = self.plain
        atype, arg = int(atype), int(arg)
        if atype not in ACTION_TYPES:
            raise ValueError('行为类型 %s 无效' % atype)
        o = self.trigger_objects(sid)
        inst = o['inst']
        if inst is None:
            raise RuntimeError('该触发没有运行期实例，不能加行为')
        acts = self.actions()
        if not acts:
            raise RuntimeError('档内没有可克隆的行为对象')
        # ---- 定位插入点与序号 ----
        runs = self._action_runs() or {}
        r = runs.get(str(sid).upper())
        if r:
            start, count = int(r['start']), int(r['count'])
            pos = acts[start + count - 1]['obj'] + ACTION_SIZE
            neword = start + count
            tpl = acts[start + count - 1]['obj']
        else:                                  # 该触发还没有行为 ⇒ 追加到表尾
            pos = acts[-1]['obj'] + ACTION_SIZE
            neword = len(acts)
            tpl = acts[-1]['obj']
        # 新键：取全档最大键 +0x1000，且避开明文里已出现的值
        keys = [x.get('key') for x in acts if x.get('key')] + \
               [e.get('key') for e in self.events() if e.get('key')]
        k = ((max(keys) + 0x1000) if keys else 0x02A75020) & 0xFFFFFFFF
        while pl.find(struct.pack('<I', k)) >= 0:
            k = (k + 0x1000) & 0xFFFFFFFF
        # 序号后移（先做：此时偏移还没变）
        for a in acts:
            v = struct.unpack_from('<I', pl, a['obj'] + 0x38)[0]
            if v >= neword:
                pl[a['obj'] + 0x38:a['obj'] + 0x3C] = struct.pack('<I', v + 1)
        # ---- 造对象（克隆模板，清运行期指针，保留 0..36 类签名）----
        buf = bytearray(pl[tpl:tpl + ACTION_SIZE])
        for off in range(40, ACTION_SIZE, 4):
            buf[off:off + 4] = b'\x00' * 4
        buf[16:20] = struct.pack('<I', k)
        buf[0x38:0x3C] = struct.pack('<I', neword)          # ★序号 = 物理下标
        buf[self.ACTION_NEXT_OFF:self.ACTION_NEXT_OFF + 4] = b'\x00' * 4
        buf[OBJ_TYPE_OFF:OBJ_TYPE_OFF + 4] = struct.pack('<I', atype)
        buf[OBJ_ARG_OFF:OBJ_ARG_OFF + 4] = struct.pack('<I', arg)
        if atype in ACTION_VAR_TYPES:
            buf[OBJ_ARG_OFF:OBJ_ARG_OFF + 4] = struct.pack('<I', 0)
            buf[ACT_VAR_OFF:ACT_VAR_OFF + 4] = struct.pack('<I', arg & 0xFFFFFFFF)
        if atype == 125:
            if not name or (waypoint is None and cell is None):
                raise RuntimeError('行为 125「将建筑建于…」必须同时给 name（建筑类型名）'
                                   '与 waypoint（路径点号）或 cell（目标格子 x,y）')
            if waypoint is None:
                wp = self.ensure_waypoint_at(int(cell[0]), int(cell[1]))
                if wp is None:
                    near = self.nearest_waypoint(int(cell[0]), int(cell[1]))
                    if near is None:
                        raise RuntimeError('路径点号已用满且没有可复用的已有路径点')
                    wp = near[0]
                waypoint = wp
            buf[104:112] = str(name).upper().encode('ascii', 'ignore')[:8].ljust(8, b'\x00')
            buf[88:92] = struct.pack('<I', int(waypoint))
        if self.is_team_action(atype):
            # ★§253：行为 80「援军」/107「超时空传送援军」——「触发挂载作战小队」。
            #   布局（§218 实测，两者相同）：`+0x40` 类型 / **`+0x44` 小队键** /
            #   `+0x58` 路径点 / `+0x48` 恒 0。注意小队键**不是** `+0x48`（工具默认参数槽）。
            # ★★§283.9（2026-10-27）：**4 建立 / 5 解散 / 7 援军 也是小队型**，小队键同样在
            #   `+0x44`（Z17 档实测 8/8 命中本档 Team 键），只是**没有路径点**。
            #   以前这里只认 SPAWN_ACTIONS ⇒ 添加 4/7 时 team 参数被**静默丢掉**、`+0x44` 写 0。
            if team is None:
                raise RuntimeError('行为 %d 要挂作战小队：必须给 team（小队键 / 小队 ID / 名称）'
                                   % atype)
            tkey = self.team_key_of(team)
            if not tkey:
                raise RuntimeError('认不出小队 %r（可用小队 ID / 名称 / 键）' % (team,))
            if atype in self.SPAWN_ACTIONS and waypoint is None:
                if cell is None:
                    raise RuntimeError('行为 %d 还要给 waypoint（路径点号）或 cell=(x,y)' % atype)
                waypoint = self.ensure_waypoint_at(int(cell[0]), int(cell[1]))
                if waypoint is None:
                    raise RuntimeError('格 %s 处建不了路径点（号段用满且无近点可复用）' % (cell,))
            buf[0x44:0x48] = struct.pack('<I', tkey)
            buf[0x48:0x4C] = b'\x00' * 4
            if atype in self.SPAWN_ACTIONS:
                buf[0x58:0x5C] = struct.pack('<I', int(waypoint))
        pl[pos:pos] = bytes(buf)
        self.invalidate_caches(rescan=True)
        pl = self.plain
        # ---- 接到链尾（键不变，偏移会漂，按新扫描的 acts 找）----
        head = int.from_bytes(pl[inst + 196:inst + 200], 'little')
        chain = self.action_chain(head)
        if not chain:
            pl[inst + 196:inst + 200] = struct.pack('<I', k)
        else:
            byk = {x.get('key'): x for x in self.actions()}
            last = byk.get(chain[-1])
            if last is None:
                raise RuntimeError('行为链异常（链尾对象找不到）—— 请勿保存')
            pl[last['obj'] + self.ACTION_NEXT_OFF:
               last['obj'] + self.ACTION_NEXT_OFF + 4] = struct.pack('<I', k)
        self.invalidate_caches(rescan=True)
        # ---- 组计数 +1 ----
        acts2 = self.actions()
        if acts2:
            p0 = acts2[0]['obj'] - 4
            n = int.from_bytes(pl[p0:p0 + 4], 'little')
            pl[p0:p0 + 4] = struct.pack('<I', n + 1)
        self.invalidate_caches(rescan=True)
        rest = self.trigger_actions(sid)
        # ★2026-10-24：返回里补 `obj`（新对象的明文偏移）。旧返回值只有 key/ordinal，
        #   调用方要校验刚写进去的字段（如行为 125 的建筑名 +104 / 路径点 +88）还得自己
        #   再遍历一遍 —— `_selftest_ev174` 就是因此报 KeyError 'obj'。
        obj_off = next((a.get('action_off') for a in rest if a.get('key') == k), None)
        if obj_off is None:
            obj_off = next((x['obj'] for x in self.actions() if x.get('key') == k), None)
        return {'key': k, 'type': atype, 'arg': arg, 'ordinal': neword,
                'total': len(rest), 'obj': obj_off}

    # ---------- 复制 / 删除 ----------
    def copy_trigger(self, sid, new_name=None):
        """复制一条触发（新 ID + 新六件套），事件/行为/国家/触发类型/禁用/难度一并照抄。"""
        d = self.trigger_detail(sid)
        if not d['editable']:
            raise RuntimeError('该记录只有触发定义（没有实例），复制它不会生效 —— 已拒绝')
        ev = d['event']
        if ev is None:
            raise RuntimeError('该触发没有事件，无法复制')
        acs = d['actions'] or []
        name = (new_name or (d['name'] + '副本'))[:16]
        info = self.create_trigger(
            name, ev['type'], ev['arg'],
            acs[0]['type'] if acs else 8, acs[0]['arg'] if acs else 0,
            owner=d['owner'] if d['owner'] not in (None, '') else None,
            persistence=int(d['repeat'] or 0))
        newsid = info['trigger_id']
        warns = list(info.get('warnings') or [])
        for a in acs[1:]:
            try:
                self.add_action(newsid, a['type'], a['arg'])
            except Exception as e:
                warns.append('复制行为失败（类型 %s）：%s' % (a['type'], e))
        if d['disabled'] is not None:
            self.set_trigger_flags(newsid, disabled=d['disabled'], easy=d['easy'],
                                   normal=d['normal'], hard=d['hard'])
        self.invalidate_caches(rescan=True)
        return {'sid': newsid, 'name': name, 'warnings': warns,
                'actions': len(acs)}

    # ================= §193 事件 / 行为「列表化」：排序 + 单条删除 =================
    # 结构前提（§168 已定案，本节沿用）：
    #   · 事件 = **键链**：触发实例 +192 = 链头键，每个事件对象 +0x3C = 下一个键
    #   · 行为 = **键链 + 连续数组**：实例 +196 = 链头键；对象 +0x38 = 序号（0 基，必须连续）
    #   · 两组的**组计数**都在「组内第一个对象的 obj-4」处（u32）
    # ★排序用**交换载荷**而不是改链：链是按"键"走的，把两个对象的载荷互换、
    #   键留在原位 ⇒ 链指向的顺序自然就反过来了，**一个字节的长度都不变**，
    #   是最安全的做法（改链也行，但要同时改链头 + 每个 next，出错面更大）。
    def owner_sid_of_event(self, key):
        """事件**自键** → 所属触发 sid（走运行期键链，不需要地图）。找不到返回 None。"""
        for sid, d in self.runtime_links().items():
            if key in self.event_chain(d['ev']):
                return sid
        return None

    def owner_sid_of_action(self, action_off):
        """行为**对象偏移** → 所属触发 sid（行为是按 `+0x38` 序号排的连续数组）。"""
        for sid in list(self.runtime_links().keys()):
            try:
                for a in self.trigger_actions(sid):
                    if a['action_off'] == action_off:
                        return sid
            except Exception:
                continue
        return None

    def _obj_size(self, kind, sid):
        """量出本档该触发的事件/行为对象尺寸（事件尺寸随档变化：108/124）。"""
        if kind == 'ac':
            return ACTION_SIZE
        o = self.trigger_objects(sid)
        return o.get('ev_size') or 108

    def _swap_payload(self, a, b, size, skip_index=False):
        """交换两个对象的**载荷**，保留三个"身份/结构"字段：
            [0,20)   GUID + 自键
            [0x38,0x40)  序号（行为）/「所属触发序号」（事件） + **next 键**（链就靠它）
        ⇒ 只换 [20,0x38) 与 [0x40,size)。★next 键必须留在原位，否则链会自环成 1 条
          （2026-10-22 实踩：只跳过序号时，交换后事件链只剩 1 个）。
        """
        pl = self.plain
        del skip_index          # 两类对象的保护区完全相同，参数保留只为兼容调用
        for lo, hi in ((20, 56), (64, size)):
            ba = bytes(pl[a + lo:a + hi])
            bb = bytes(pl[b + lo:b + hi])
            pl[a + lo:a + hi] = bb
            pl[b + lo:b + hi] = ba

    def _chain_objs(self, sid, kind):
        """→ (inst_off, head_off, [(key, obj)], next_off)（按**链序**）。"""
        d = self.runtime_links().get(str(sid).upper())
        if not d:
            raise RuntimeError('没找到触发 %s 的实例对象' % sid)
        inst = self._instance_maps()['inst'].get(d['inst_key'])
        if inst is None:
            raise RuntimeError('触发 %s 的实例对象定位失败' % sid)
        if kind == 'ev':
            head_off, keys = inst + 192, self.event_chain(d['ev'])
            pool = {e.get('key'): e['obj'] for e in self.events() if e.get('key')}
            next_off = self.EVENT_NEXT_OFF
        else:
            head_off, keys = inst + 196, self.action_chain(d['ac'])
            pool = {a.get('key'): a['obj'] for a in self.actions() if a.get('key')}
            next_off = self.ACTION_NEXT_OFF
        seq = [(k, pool[k]) for k in keys if k in pool]
        return inst, head_off, seq, next_off

    def move_event(self, sid, index, delta):
        """把某触发「链序」第 index 个事件上移(-1)/下移(+1)一格。

        ★零长度改动：只交换两个对象体的载荷（GUID/自键留在原位）。
        ⚠ 含事件 13（流逝时间）时会被搬出首位 ⇒ 返回值里带 `warn`（§175：只有第一位
          的 13 才算心跳，搬走等于这条触发不再被计时驱动）。
        """
        inst, head_off, seq, _nx = self._chain_objs(sid, 'ev')
        delta = int(delta)
        if delta not in (-1, 1):
            raise ValueError('delta 只能是 -1（上移）或 +1（下移）')
        j = int(index) + delta
        if not (0 <= int(index) < len(seq) and 0 <= j < len(seq)):
            raise RuntimeError('事件序号越界（共 %d 条）' % len(seq))
        size = self._obj_size('ev', sid)
        a, b = seq[int(index)][1], seq[j][1]
        ta = struct.unpack_from('<I', self.plain, a + OBJ_TYPE_OFF)[0]
        tb = struct.unpack_from('<I', self.plain, b + OBJ_TYPE_OFF)[0]
        self._swap_payload(a, b, size)
        self.invalidate_caches(rescan=True)
        warn = ''
        if ta == 13 or tb == 13:
            if int(index) == 0 or j == 0:
                warn = ('⚠ 事件 13「流逝时间」被搬离/搬入首位：引擎**只认第一位**的 13 '
                        '（心跳，§175）。若这条触发里有 36/37 局部变量事件，搬走后就永不评估。')
        return {'order': [k for k, _o in seq], 'moved': int(index), 'to': j, 'warn': warn}

    def move_action(self, sid, index, delta):
        """把某触发第 index 个行为上移/下移一格（★行为按顺序执行，顺序=执行顺序）。

        ★行为**不是**用键链排序的：`trigger_actions()` 是按 `+0x38` 序号排的连续数组，
          所以这里直接交换两个对象的载荷（序号与 next 键留在原位）。
        """
        acts = self.trigger_actions(sid)
        delta = int(delta)
        if delta not in (-1, 1):
            raise ValueError('delta 只能是 -1（上移）或 +1（下移）')
        j = int(index) + delta
        if not (0 <= int(index) < len(acts) and 0 <= j < len(acts)):
            raise RuntimeError('行为序号越界（共 %d 条）' % len(acts))
        if acts[int(index)]['type'] == acts[j]['type'] == 0:
            pass
        a, b = acts[int(index)]['action_off'], acts[j]['action_off']
        self._swap_payload(a, b, ACTION_SIZE)
        self.invalidate_caches(rescan=True)
        return {'order': [x['type'] for x in self.trigger_actions(sid)],
                'moved': int(index), 'to': j, 'warn': ''}

    def remove_event(self, sid, index):
        """删除某触发链序第 index 个事件（**最后一个不能删**，否则触发永不成立）。

        步骤：链重连（前驱 +0x3C 或链头 +192 → 被删者 next）→ 删字节 → 事件组计数 −1。
        """
        inst, head_off, seq, next_off = self._chain_objs(sid, 'ev')
        # ★兼容两种调用（2026-10-24 实踩）：小整数 = **链序**（0 基，列表化编辑器用）；
        #   大整数 = **事件自键**（旧 API、「触发编辑器」、`_selftest_ev174` 在用）。
        #   自键形如 0x02A7xxxx，绝不会落进 0..len 区间 —— 原实现只按序号解释，
        #   GUI 的「删除选中事件」传的是 `r['key']` ⇒ 一点就报「事件序号越界」。
        orig = index
        index = int(index)
        if len(seq) <= 1:
            raise RuntimeError('这条触发只有 1 个事件，删掉它触发就永远不会成立了 —— 已拒绝')
        if not (0 <= index < len(seq)):
            idx2 = next((i for i, (k, _o) in enumerate(seq) if k == orig), None)
            if idx2 is None:
                raise RuntimeError('事件序号/键 %s 不在这条触发里（共 %d 条）'
                                   % (orig, len(seq)))
            index = idx2
        size = self._obj_size('ev', sid)
        obj = seq[index][1]
        nxt = struct.unpack_from('<I', self.plain, obj + next_off)[0]
        pl = self.plain
        # ① 链重连
        if index == 0:
            pl[head_off:head_off + 4] = struct.pack('<I', nxt)
        else:
            prev = seq[index - 1][1]
            pl[prev + next_off:prev + next_off + 4] = struct.pack('<I', nxt)
        # ② 组计数（在组首 obj−4，位置恒在 obj 之前 ⇒ 删字节不影响它）
        evs = self.events()
        cpos = evs[0]['obj'] - 4
        n = struct.unpack_from('<I', pl, cpos)[0]
        # ③ 删字节（降序无所谓，只有一个区间）
        del pl[obj:obj + size]
        self.invalidate_caches(rescan=True)
        pl = self.plain
        pl[cpos:cpos + 4] = struct.pack('<I', max(0, n - 1))
        self.invalidate_caches(rescan=True)
        return {'removed': index, 'size': size, 'left': len(seq) - 1,
                'order': [k for k, _o in seq if k != seq[index][0]]}

    def remove_action(self, sid, index):
        """删除某触发第 index 个行为（**最后一条也能删**：删完 = 无行为对象的空触发，
        实例行为链头写 0 —— §219 证明引擎能读这种形态）。

        行为的序号 `+0x38` 必须与位置一致 ⇒ 删完要把后面同触发行为的序号各 −1；
        删的是第一条时，实例 `+196` 链头要指向新的第一条（键 = 新第一条对象的 +16）。
        """
        d = self.runtime_links().get(str(sid).upper())
        if not d:
            raise RuntimeError('没找到触发 %s 的实例对象' % sid)
        inst = self._instance_maps()['inst'].get(d['inst_key'])
        if inst is None:
            raise RuntimeError('触发 %s 的实例对象定位失败' % sid)
        acts = self.trigger_actions(sid)
        # ★兼容两种调用：小整数 = 序号（0 基，新 GUI 用）；大整数 = 行为自键（旧 API /
        #   「触发编辑器」在用）。自键形如 0x02A7xxxx，绝不会落进 0..len 区间。
        if not (0 <= int(index) < len(acts)):
            idx2 = next((i for i, a in enumerate(acts) if a.get('key') == index), None)
            if idx2 is None:
                raise RuntimeError('行为序号/键 %s 不在这条触发里' % index)
            index = idx2
        if len(acts) <= 1:
            # ★§257：**最后一条也能删**（§231 玩家要求"删完就是空触发"；
            #   §219 已证明「无行为对象的空触发」引擎能读 —— 实例行为链头写 0 即可，
            #   `create_trigger(ac_type=None)` 建的就是这种形态）。
            #   旧实现直接拒绝 ⇒ GUI 里"删最后一条"一直弹「删除失败」。
            pl = self.plain
            obj = acts[index]['action_off']
            first = self.actions()[0]['obj']
            cpos = first - 4
            n = struct.unpack_from('<I', pl, cpos)[0]
            pl[inst + 196:inst + 200] = b'\x00' * 4        # 行为链头 = 0（空触发）
            del pl[obj:obj + ACTION_SIZE]
            self.invalidate_caches(rescan=True)
            pl = self.plain
            pl[cpos:cpos + 4] = struct.pack('<I', max(0, n - 1))
            self.invalidate_caches(rescan=True)
            return {'removed': index, 'size': ACTION_SIZE, 'left': 0, 'order': []}
        if not (0 <= index < len(acts)):
            raise RuntimeError('行为序号越界（共 %d 条）' % len(acts))
        pl = self.plain
        obj = acts[index]['action_off']
        # ① 后面同触发行为的序号 −1（先写，此时偏移还没变）
        for r in acts[index + 1:]:
            o2 = r['action_off']
            v = struct.unpack_from('<I', pl, o2 + 0x38)[0]
            if v > 0:
                pl[o2 + 0x38:o2 + 0x3C] = struct.pack('<I', v - 1)
        # ② 删的是第一条 ⇒ 链头改成新的第一条（用它自己的键）
        if index == 0:
            newkey = struct.unpack_from('<I', pl, acts[1]['action_off'] + 16)[0]
            pl[inst + 196:inst + 200] = struct.pack('<I', newkey)
        # ③ 组计数
        first = self.actions()[0]['obj']
        cpos = first - 4
        n = struct.unpack_from('<I', pl, cpos)[0]
        del pl[obj:obj + ACTION_SIZE]
        self.invalidate_caches(rescan=True)
        pl = self.plain
        pl[cpos:cpos + 4] = struct.pack('<I', max(0, n - 1))
        self.invalidate_caches(rescan=True)
        rest = self.trigger_actions(sid)
        return {'removed': index, 'size': ACTION_SIZE, 'left': len(rest),
                'order': [x['type'] for x in rest]}

    def remove_trigger(self, sid, clear_attach=True):
        """删除一条触发：实例 + 标签 + 事件 + 全部行为 + TagNode + CellTag + 计时表条目。

        顺序：① 清建筑挂接字段（否则建筑指向已删的 CellTag）② 摘计时表条目
              ③ **降序**删对象字节 ④ 四类组计数 −1、事件/行为计数 −1
              ⑤ 重排剩余触发的**序号**（实例/标签 +172、事件/行为 +56 = 数组位置）
        ⚠ 只支持"有实例"的触发（老格式只有定义的那种拒绝删除）。
        """
        pl = self.plain
        o = self.trigger_objects(sid)
        if o['inst'] is None:
            raise RuntimeError('这条记录在本档里只有「触发定义」，没有实例/标签/事件 —— '
                               '本工具不做删除（删了也不会改变引擎行为）')
        spans = [(o['inst'], TRIG_OBJ_SIZE, '实例')]
        if o['tag_obj'] is not None:
            spans.append((o['tag_obj'], TAG_OBJ_SIZE, '标签'))
        if o['ev'] is not None:
            if not o['ev_size']:
                raise RuntimeError('档内事件不足 2 个，量不出事件对象尺寸 ⇒ 拒绝删除')
            spans.append((o['ev'], o['ev_size'], '事件'))
        for obj, _k in o['acs']:
            spans.append((obj, ACTION_SIZE, '行为'))
        if o['node'] is not None:
            spans.append((o['node'], TRIG_NODE_SIZE, 'TagNode'))
        if o['cell'] is not None:
            spans.append((o['cell'], TRIG_CELL_SIZE, 'CellTag'))
        # ① 建筑挂接
        detached = []
        if clear_attach and o['cell_key'] is not None:
            ck = o['cell_key']
            try:
                for b in self.attach_overview() or []:
                    if b.get('x') != ck:
                        continue
                    for bb in (b.get('buildings') or []):
                        f = bb.get('field')
                        if f:
                            pl[f:f + 4] = b'\x00' * 4
                            detached.append((bb.get('cx'), bb.get('cy')))
            except Exception:
                pass
        # ② 计时表条目
        timer = False
        if o['cell_key'] is not None:
            tl = self.celltag_timer_list()
            if tl and o['cell_key'] in tl['keys']:
                i = tl['keys'].index(o['cell_key'])
                spans.append((tl['list_pos'] + 4 * i, 4, '计时表'))
                timer = True
        # ③ 降序删
        for start, size, _lab in sorted(spans, key=lambda x: -x[0]):
            if start + size > len(pl):
                raise RuntimeError('删除区间越界，已中止（未改动存档）')
            del pl[start:start + size]
        self.invalidate_caches(rescan=True)
        # ④ 计时表计数
        if timer:
            tl2 = self.celltag_timer_list()
            if tl2:
                pl[tl2['count_pos']:tl2['count_pos'] + 4] = struct.pack(
                    '<I', len(tl2['keys']))
        # 组计数（tag/trig/node/cell 各 −1）
        for guid in (TRIG_TAGOBJ_GUID, TRIG_INSTANCE_GUID,
                     TRIG_NODE_GUID, TRIG_CELLGUID):
            p0 = pl.find(guid)
            if p0 >= 4:
                n = int.from_bytes(pl[p0 - 4:p0], 'little')
                pl[p0 - 4:p0] = struct.pack('<I', max(0, n - 1))
        # 事件/行为组计数 −1
        acts, evs = self._split_tables()
        for first, cnt in ((acts[0]['obj'] if acts else None, len(o['acs'])),
                           (evs[0]['obj'] if evs else None, 1 if o['ev'] else 0)):
            if first is None or not cnt:
                continue
            p0 = first - 4
            n = int.from_bytes(pl[p0:p0 + 4], 'little')
            pl[p0:p0 + 4] = struct.pack('<I', max(0, n - cnt))
        self.invalidate_caches(rescan=True)
        # ⑤ 重排序号
        self.renumber_triggers()
        self.invalidate_caches(rescan=True)
        return {'sid': sid, 'name': o['name'], 'bytes_removed': sum(s[1] for s in spans),
                'objects': [s[2] for s in spans], 'detached': detached,
                'timer_entry': timer}

    def renumber_triggers(self):
        """把「集群内序号」重写成**数组位置**（实例/标签 +172、事件/行为 +56）。

        实测 3 档：序号 == 位置（SAVE48DB 82/82、AB 2/2、SAVE6784 3/3）。
        删中间一条触发后必须重排，否则引擎按序号找不到对象。
        """
        pl = self.plain
        m = self._instance_maps()
        insts = sorted(m['inst'].items(), key=lambda kv: kv[1])
        tobj = self._tag_objs_by_key()
        byk = {a.get('key'): a for a in self.actions()}
        evs = {e.get('key'): e for e in self.events()}
        for i, (k, off) in enumerate(insts):
            pl[off + self.TRIG_SEQ_OFF:off + self.TRIG_SEQ_OFF + 4] = struct.pack('<I', i)
            t = tobj.get(m['tag_of_inst'].get(k))
            if t is not None:
                pl[t + self.TRIG_SEQ_OFF:t + self.TRIG_SEQ_OFF + 4] = struct.pack('<I', i)
            ev = evs.get(int.from_bytes(pl[off + 192:off + 196], 'little'))
            if ev is not None:
                pl[ev['obj'] + 56:ev['obj'] + 60] = struct.pack('<I', i)
            for ack in self.action_chain(int.from_bytes(pl[off + 196:off + 200],
                                                        'little')):
                a = byk.get(ack)
                if a is not None:
                    pl[a['obj'] + 56:a['obj'] + 60] = struct.pack('<I', i)
        return len(insts)

    # ---------- 软删除（2026-10-20 玩家方案）：不删对象，只把条件改成永不满足 ----------
    SOFTOFF_TIME_SEC = 0xFFFFFF          # 参数上限 = 16,777,215 秒（≈194 天连续游戏）

    def soft_disable_trigger(self, sid):
        """把一条触发改成"永不满足"，效果等同删除，但**一个对象都不删**。

        玩家定下的规则（2026-10-20）：
          · **挂了建筑**的触发 → 事件改「13 流逝时间」+ 参数拉到上限（16,777,215 秒）。
            理由：挂建筑的那种，事件改成"进入事件"仍会在有单位进楼时满足，不可靠。
          · **没挂建筑**的触发 → 事件改「1 进入事件」，参数 0 ⇒ 没有东西会"进入"，
            永远不满足。
        只改 8 个字节（事件类型 4B + 参数 4B）；计时型触发会顺带把 TagNode 帧数同步成
        `参数量 × 15`（set_event 本来就这么做），所以耗时会变成 251,658,225 帧 —— 仍在 u32 内。
        ★完全可逆：想恢复就把事件类型/参数改回去（软删除不改任何结构）。

        返回 {'sid','name','mode','event','arg','attached','ticks','note'}
        """
        d = self.trigger_detail(sid)
        ev = d['event']
        if ev is None:
            raise RuntimeError('该触发没有可编辑的事件对象，无法软删除')
        attached = int(d.get('attached') or 0)
        if attached or d['has_node']:
            self.set_event(ev, etype=13, arg=self.SOFTOFF_TIME_SEC)
            mode = '流逝时间 %d 秒' % self.SOFTOFF_TIME_SEC
            note = ('挂了 %d 栋建筑/原本就是计时触发 ⇒ 用「流逝时间」拉到参数上限'
                    '（%d 秒 ≈ 194 天连续游戏），实际上永远不会到点'
                    % (attached, self.SOFTOFF_TIME_SEC))
        else:
            self.set_event(ev, etype=1, arg=0)
            mode = '进入事件（参数 0）'
            note = '没挂建筑 ⇒ 改成「进入事件」，没有东西会"进入"它 ⇒ 永远不满足'
        d2 = self.trigger_detail(sid)
        self.invalidate_caches(rescan=True)
        return {'sid': sid, 'name': d['name'], 'mode': mode,
                'event': (d2['event'] or {}).get('type'),
                'arg': (d2['event'] or {}).get('arg'),
                'attached': attached, 'ticks': d2.get('ticks'), 'note': note}
