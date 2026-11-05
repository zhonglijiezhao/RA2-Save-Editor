# -*- coding: utf-8 -*-
"""地图 INI 解析（.map / .yrm）—— 触发/事件/行为的**权威真值 + 归属关系**

RA2/YR 地图文件是 INI 文本（UTF-8 或 GBK），相关段：

```
[Triggers] 01000000=Neutral,<none>,测试A,0,1,1,1,0      ← 键 = 触发ID
[Events]   01000000=3,13,0,179,12,0,7965,11,0,2          ← 键 = 触发ID；值 = 个数, (类型,0,参数)×N
[Actions]  01000000=1,16,0,0,0,0,0,0,A                   ← 键 = 触发ID；值 = 个数,
                                                              每块 8 字段 = [行为类型][参数类型][值][×4][路径点字母]
           ★2026-10-20 更正：第 2 个字段不是"参数1"，而是**参数类型**（0 字面数值 / 1 作战小队 /
             2 触发 / 3 标签 / 4 CSF 文本 / 9·10 内联类型名 / 11 数值+附加…）。
             实测样例：`107,1,01000026,0,0,0,0,P` = 行为107 + 参数类型1(小队) + 值01000026 + 路径点 P。
[Tags]     01000001=0,测试A 1,01000000                   ← 键 = 标签ID；末位 = 它指向的触发ID
```

★ 关键价值：这些段**以触发ID 为键**，所以是「哪个触发挂哪些事件/行为」的**唯一权威来源**
（存档里没有这个归属信息）。
"""
import os


# ---------------------------------------------------------------- 路径点字母编码
# ★★2026-11-05 定案：地图 INI 里凡是要写"第几号路径点"的地方（[Actions] 每块第 8 个字段、
#   [TaskForces] 的 `Waypoint=`），用的都是**双射 26 进制（bijective base-26）**：
#       A=1, B=2, … Z=26, AA=27, AB=28, …
#   而**路径点号 = 这个值 − 1**（A=0、Z=25、AA=26）。
#
#   证据链（不再是"看着像"）：
#     ① FA2sp（地编）的 PDB 里有符号 `STDHelpers::WaypointToString` / `Waypoint_To_String`；
#        反汇编 `STDHelpers::WaypointToString`（RVA 0x3CEA50）逐条读出来就是：
#            ecx = n + 1
#            循环 { eax = ecx % 26; if (eax == 0) eax = 26;       // 余 0 当作 26
#                   putchar('A' + eax - 1);                        // 1..26 → 'A'..'Z'
#                   ecx = (ecx - eax) / 26; }                      // 继续取商
#        除法用的是魔数 `0x4EC4EC4F` + `sar edx,3`（= 有符号除以 26），26 在 `lea edi,[esi+0x13]` 里。
#     ② `sov01umd.map`：[Actions] 里出现的 **293 种**字母串，按此公式解码后
#        **293/293** 都落在 `[Waypoints]` 的键里；`所有触发全禁用.map` 330/330。
#     ③ `all01umd.map` 的 `CV` / `CU` → 99 / 98，与存档里行为 108 的 `+0x58` 逐一对上。
#   ⇒ 以前"CV=99、CU=98，不是朴素 36 进制"的疑惑到此结束。
def wp_encode(n):
    """路径点号 → 地图 INI 的字母串（0→`A`、25→`Z`、26→`AA`、99→`CV`）。"""
    n = int(n)
    if n < 0:
        raise ValueError('路径点号不能是负数：%d' % n)
    v = n + 1
    out = ''
    while v > 0:
        v, r = divmod(v - 1, 26)          # 双射：先减 1 再取余，余 0 就是 'A'
        out = chr(0x41 + r) + out
    return out


def wp_decode(s):
    """地图 INI 的字母串 → 路径点号（`A`→0、`Z`→25、`AA`→26）；认不出返回 None。

    ★只认字母，**不要顺手把纯数字也当路径点号**：`[Actions]` 每块第 8 个字段的语义
      随行为类型变 —— 需要落点的行为（80 援军 / 107 / 125 建建筑 / 108 工具箱）是路径点，
      而 **131「闪烁特定类型的建筑…」那一位是「闪烁帧数」**（地编原文：1 秒 = 15 帧），
      实测那张图里就写着 `500` / `1000`。混进来会张冠李戴。
    """
    t = (s or '').strip().upper()
    if not (1 <= len(t) <= 3):
        return None
    v = 0
    for ch in t:
        if not ('A' <= ch <= 'Z'):
            return None
        v = v * 26 + (ord(ch) - 0x40)
    return v - 1


class MapIni(object):
    def __init__(self, path):
        self.path = path
        self.sections = {}
        self.encoding = None
        self._parse()

    # ---------- 触发行为的「参数类型」（引擎口径） ----------
    def action_entries(self):
        """[(触发ID, [行为类型, 参数类型, 值, ..., 路径点])] —— 按 [Actions] 原样切块。"""
        out = []
        for tid, raw in (self.sections.get('actions') or {}).items():
            toks = [t.strip() for t in str(raw).split(',')]
            try:
                n = int(toks[0] or 0)
            except ValueError:
                continue
            for c in range(n):
                ch = toks[1 + c * 8:1 + c * 8 + 8]
                if len(ch) >= 2:
                    out.append((tid, ch))
        return out

    def action_param_types(self):
        """{行为类型: set(参数类型)} —— 直接从地图里的真实数据统计。

        ★这是**引擎口径**的参数类型（0 字面数值 / 1 作战小队 / 2 触发 / 3 标签 …），
          与 `editor_core.action_ptype()` 里那套「地编 FAData 口径」**不是同一套编号**，
          别混用（详见主文档 §126）。
        """
        out = {}
        for _tid, ch in self.action_entries():
            try:
                kind, pt = int(ch[0] or 0), int(ch[1] or 0)
            except ValueError:
                continue
            if kind:
                out.setdefault(kind, set()).add(pt)
        return out

    # ---------- 解析 ----------
    def _read_text(self):
        """整档先按 utf-8/gbk 整体试；都失败则**逐行**判编码。

        ★2026-10-17 实测：`all01umd.map` 是**混合编码**
        （大部分 GBK，个别段是 UTF-8，例如 `[KONLON9]` 的 `Name=` 是 `e6 81 90`=恐 的 UTF-8 字节），
        整体按 GBK 解会把那段解成 `鎭愰緳9鍙?` 这种乱码 ⇒ 逐行兜底：
        「这一行能按 UTF-8 解、且含非 ASCII」就用 UTF-8，否则用 GBK。
        """
        raw = open(self.path, 'rb').read()
        for enc in ('utf-8-sig', 'utf-8'):
            try:
                t = raw.decode(enc)
                self.encoding = enc
                return t
            except Exception:
                continue
        lines = []
        for ln in raw.split(b'\n'):
            body = ln.rstrip(b'\r')
            dec = None
            if any(c > 0x7F for c in body):
                try:
                    dec = body.decode('utf-8')
                except Exception:
                    # 近似判 UTF-8：忽略个别坏字节后，重新编码的长度几乎不变 ⇒ 本来就是 UTF-8
                    # （实测 `[KONLON9]` 的 `Name=` 末字节被作者工具截断成 0x3F，
                    #   严格解码会整行失败退回 GBK，变成 `鎭愰緳9鍙?`）
                    u = body.decode('utf-8', 'ignore')
                    if u and abs(len(u.encode('utf-8')) - len(body)) <= 2:
                        dec = u
            if dec is None:
                try:
                    dec = body.decode('gbk')
                except Exception:
                    dec = body.decode('gbk', 'replace')
            lines.append(dec)
        self.encoding = 'per-line(utf-8/gbk)'
        return '\n'.join(lines)

    def _parse(self):
        cur = None
        for line in self._read_text().splitlines():
            s = line.strip()
            if not s or s.startswith(';'):
                continue
            if s.startswith('[') and s.endswith(']'):
                cur = s[1:-1].strip().lower()
                self.sections.setdefault(cur, {})
                continue
            if cur is None or '=' not in s:
                continue
            k, v = s.split('=', 1)
            self.sections[cur][k.strip()] = v.strip()

    # ---------- 访问 ----------
    @property
    def triggers(self):
        return self.sections.get('triggers', {})

    @property
    def events(self):
        return self.sections.get('events', {})

    @property
    def actions(self):
        return self.sections.get('actions', {})

    @property
    def tags(self):
        return self.sections.get('tags', {})

    @property
    def basic(self):
        return self.sections.get('basic', {})

    @property
    def name(self):
        return self.basic.get('Name', '') or os.path.basename(self.path)

    def trigger_ids(self):
        return set(self.triggers)

    def tag_trigger_map(self):
        """标签ID -> 它指向的触发ID"""
        out = {}
        for tid, v in self.tags.items():
            parts = [p.strip() for p in v.split(',')]
            if parts:
                out[tid] = parts[-1]
        return out

    # ---------- 建筑（挂接管理用，2026-10-14） ----------
    def structures(self):
        """[Structures] → [{'idx','owner','type','x','y','tagid'}]
        行格式: idx=所属方,类型,血量,x,y,朝向,Tag,…（Tag='None' 表示未挂接）"""
        out = []
        for k, v in self.sections.get('structures', {}).items():
            f = [p.strip() for p in v.split(',')]
            if len(f) < 7:
                continue
            try:
                x, y = int(f[3]), int(f[4])
            except Exception:
                continue
            out.append({'idx': k, 'owner': f[0], 'type': f[1],
                        'x': x, 'y': y, 'tagid': f[6]})
        return out

    # ---------- 路径点（waypoint） ----------
    def waypoints(self):
        """`[Waypoints]` → {号: (x, y)}（号 0..701，实测大图有 557 个）。

        ★值编码 = **y×1000 + x**（2026-10-17 实测 `all01umd.map`：
          `98=80080` → (80,80)，`99=79080` → (80,79)；
          而玩家用「将建筑建于… 路径点 99」刷出来的光棱塔正好在 (80,79) ⇒ 编码实锤）。
        ★键是**十进制数字**，别和 `[Actions]` / `[TaskForces]` 里的字母串混
          （那些是 `wp_decode()` 的输入；`A` = 0 号）。2026-11-05 补。
        """
        out = {}
        for k, v in self.sections.get('waypoints', {}).items():
            ks = k.strip()
            vs = v.strip()
            if not ks.isdigit() or not vs.isdigit():
                continue
            n, val = int(ks), int(vs)
            out[n] = (val % 1000, val // 1000)
        return out

    def waypoint_of_cell(self, x, y):
        """给格子找**编号**（没有返回 None）。"""
        for n, (wx, wy) in self.waypoints().items():
            if (wx, wy) == (x, y):
                return n
        return None

    def map_size(self):
        """[Map] Size=left,top,width,height → (宽, 高)；解析失败给 (256,256)"""
        v = ''
        for k, vv in self.sections.get('map', {}).items():
            if k.lower() == 'size':
                v = vv
                break
        try:
            p = [int(x) for x in v.split(',')]
            return p[2], p[3]
        except Exception:
            return 256, 256

    def local_size(self):
        """`[Map] LocalSize=left,top,width,height` → (left, top, w, h)；没有就 None。

        ★这就是地编「地图参数 → **可见区域**」（格式「左, 上, 宽, 高」）那一项，
          玩家俗称的**蓝线内**：整张地图可能 180×120，但真正可玩/可见的只有这一块。
          例：`LocalSize=5,4,174,107` ⇒ 地编坐标 x∈[5,179)、y∈[4,111)。
        ★★2026-10-25 §271：这一项是**地编 tile 坐标**；存档里的格坐标是它的 **2 倍**
          （实测标定：`(16,179)` 在界内、`(1,180)`/`(2,179)` 在界外，
           只有 ×2 换算能同时满足这三条）。
        """
        v = ''
        for k, vv in self.sections.get('map', {}).items():
            if k.lower() == 'localsize':
                v = vv
                break
        if not v:
            return None
        try:
            p = [int(x) for x in v.split(',')]
            if len(p) < 4:
                return None
            return p[0], p[1], p[2], p[3]
        except Exception:
            return None

    @staticmethod
    def _ints(v):
        out = []
        for p in v.split(','):
            p = p.strip().lstrip('-')
            if p.isdigit():
                out.append(int(p))
        return out

    # ---------- 事件 / 行为（带归属） ----------
    # ★2026-10-20 校正（verna20k 逆向注释 + 本机地图实测）：两个段每块都是
    #   [类型][参数类型][值]…，第 2 个字段是**参数类型**而不是"参数1"；
    #   事件块的参数类型=2 时**多带一个类型名 token**（TEvent::Read71F4E0），
    #   所以不能按固定 3 字段硬切（旧实现先过滤成纯数字列表，遇到类型名就整段错位）。
    def event_entries(self):
        """[(触发ID, [(事件类型, 参数类型, 值字符串, 类型名)])] —— 按 token 位置切块。"""
        out = []
        for tid, v in self.events.items():
            toks = [t.strip() for t in str(v).split(',')]
            try:
                cnt = int(toks[0] or 0)
            except ValueError:
                continue
            i, recs = 1, []
            for _ in range(cnt):
                if i + 2 >= len(toks) + 0 and i + 2 > len(toks) - 1:
                    break
                if i + 2 > len(toks):
                    break
                try:
                    kind = int(toks[i] or 0)
                    pt = int(toks[i + 1] or 0)
                except (ValueError, IndexError):
                    break
                val = toks[i + 2] if i + 2 < len(toks) else ''
                i += 3
                name = ''
                if pt == 2 and i < len(toks):
                    name = toks[i]
                    i += 1
                recs.append((kind, pt, val, name))
            out.append((tid, recs))
        return out

    def action_entries_by_trigger(self):
        """[(触发ID, [(行为类型, 参数类型, 值字符串, 路径点)])] —— 每块固定 8 字段。"""
        out = []
        for tid, v in self.actions.items():
            toks = [t.strip() for t in str(v).split(',')]
            try:
                cnt = int(toks[0] or 0)
            except ValueError:
                continue
            recs = []
            for i in range(cnt):
                ch = toks[1 + 8 * i:1 + 8 * (i + 1)]
                if len(ch) < 2:
                    break
                try:
                    kind = int(ch[0] or 0)
                    pt = int(ch[1] or 0)
                except ValueError:
                    continue
                recs.append((kind, pt, ch[2] if len(ch) > 2 else '',
                             ch[7] if len(ch) > 7 else ''))
            out.append((tid, recs))
        return out

    @staticmethod
    def _num(txt):
        t = (txt or '').strip().lstrip('-')
        return int(t) if t.isdigit() else 0

    def events_by_trigger(self):
        """[(触发ID, [(事件类型, 参数), …])] —— 顺序 = 地图文件里的出现顺序（只取数值参数）"""
        return [(tid, [(k, self._num(val)) for k, _pt, val, _nm in recs])
                for tid, recs in self.event_entries()]

    def actions_by_trigger(self):
        """[(触发ID, [(行为类型, 参数), …])]（参数取"值"字段；类型名等非数字按 0）"""
        return [(tid, [(k, self._num(val)) for k, _pt, val, _wp in recs])
                for tid, recs in self.action_entries_by_trigger()]


def find_maps(dirs, exts=('.map', '.yrm')):
    """在若干目录（含一层子目录）里找地图文件"""
    out = []
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except Exception:
            continue
        for n in names:
            p = os.path.join(d, n)
            if os.path.isdir(p):
                try:
                    for n2 in os.listdir(p):
                        if n2.lower().endswith(exts):
                            out.append(os.path.join(p, n2))
                except Exception:
                    pass
            elif n.lower().endswith(exts):
                out.append(p)
    return out
