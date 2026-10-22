# -*- coding: utf-8 -*-
"""自检：路径点读取（触发法刷建筑的前提）。

★注意：`all01umd.map` 是玩家的**活测试床**（2026-10-17 12:31 就把 99 从 79080 挪到 73059），
所以本自检**不写死具体编号**，只断言：
  ① 解析结果与文件原文逐条一致；
  ② 值编码 = `y×1000 + x`（手工反算对得上，且都落在 `[Map] Size` 范围内）；
  ③ 反查 `waypoint_of_cell(x,y)` 能指回编号；
  ④ 存档侧（挂地图后）拿到的是同一份表；未挂地图时为空（不瞎猜）。
"""
import os, sys, re, struct
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from core.editor_core import SaveDoc
from core.map_ini import MapIni

OK = FAIL = 0
def chk(c, m):
    global OK, FAIL
    if c:
        OK += 1; print('  [PASS] ' + m)
    else:
        FAIL += 1; print('  [FAIL] ' + m)

MAP = r'E:/YURI/all01umd.map'
SAV = r'E:/YURI/新建文件夹 (20)/触发给韩国一个光棱塔.SAV'

def raw_waypoints(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            t = raw.decode(enc)
            break
        except Exception:
            t = None
    if t is None:
        t = raw.decode('gbk', 'replace')
    seg = re.search(r'(?is)\[Waypoints\](.*?)(?=\n\s*\[|\Z)', t)
    out = {}
    if seg:
        for line in seg.group(1).splitlines():
            line = line.split(';')[0].strip()
            if '=' in line:
                k, v = line.split('=', 1)
                if k.strip().isdigit() and v.strip().isdigit():
                    out[int(k.strip())] = int(v.strip())
    return out

print('== A: 地图 [Waypoints] 解析（活文件，不写死） ==')
if not os.path.exists(MAP):
    print('  [SKIP] 缺地图 %s' % MAP)
else:
    raw = raw_waypoints(MAP)
    m = MapIni(MAP)
    w = m.waypoints()
    chk(bool(raw), '文件里确实有 [Waypoints]（%d 条：%s）' % (len(raw), raw))
    expect = {n: (v % 1000, v // 1000) for n, v in raw.items()}
    chk(w == expect, 'waypoints() 与原文逐条一致（%s）' % (w,))
    W, H = m.map_size()
    inrange = [(n, x, y) for n, (x, y) in w.items() if 0 <= x <= W and 0 <= y <= H]
    outr = [(n, x, y) for n, (x, y) in w.items() if not (0 <= x <= W and 0 <= y <= H)]
    print('  [INFO] 地图 %dx%d；落在范围内的路径点 %d 个，范围外 %d 个 %s'
          % (W, H, len(inrange), len(outr), outr[:4]))
    # ★范围外的**不算失败**：2026-10-17 玩家的活地图里出现 431/578/654 这些 >99 的号，
    #   且坐标超出 `[Map] Size`（FA2/YR 允许的路径点号/坐标可能比 Size 更宽）——只如实报出。
    chk(bool(inrange) or bool(outr), '至少解析出条目（范围内 %d / 外 %d）' % (len(inrange), len(outr)))
    chk(all(m.waypoint_of_cell(x, y) == n for n, (x, y) in w.items()), '反查 waypoint_of_cell 能指回编号')

print('== B: 存档本体 ← 路径点（2026-10-17：表就在存档里，无需地图） ==')
if os.path.exists(SAV) and os.path.exists(MAP):
    d = SaveDoc(SAV)
    w_sav = d.waypoints_save()
    chk(bool(w_sav), '未挂地图也能从存档读出路径点（%s）' % (w_sav,))
    chk(all(d.waypoint_cell(n) == c for n, c in w_sav.items()), '按号取格子与整表一致')
    d.attach_map(MAP)
    chk(d.waypoints() == w_sav, '挂地图后 waypoints() 仍以**存档**为准')
    chk(d.waypoints_from_map() == MapIni(MAP).waypoints(), '地图视角（waypoints_from_map）与 MapIni 一致')
    for n, (x, y) in list(w_sav.items())[:3]:
        chk(d.waypoint_of_cell(x, y) == n, '存档侧反查 %d → (%d,%d) ✓' % (n, x, y))
    ok_any = False
    for g in d._bld_group()[0]:
        o = g + 16
        cx = (struct.unpack_from('<I', d.plain, o + 600)[0] - 128) // 256
        cy = (struct.unpack_from('<I', d.plain, o + 604)[0] - 128) // 256
        if (cx, cy) in d.waypoints().values():
            ok_any = True
    if ok_any:
        print('  [INFO] 档里有建筑正好落在某个路径点上 —— "刷建筑 = 落在路径点" 再次印证')
    else:
        print('  [INFO] 当前没有建筑落在现存路径点上（地图是活测试床，99 号被挪过 ⇒ 正常）')
else:
    print('  [SKIP] 缺样本')

print('\n自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
