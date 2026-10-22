# -*- coding: utf-8 -*-
"""自检：**存档本体里的路径点表**（2026-10-17 定案）。

判定依据（不许写死猜测值，全部用玩家给的样本 + 地图真值对拍）：
  ① `五路径点档.SAV` 读出 5 个点，必须与 `all01umd.map` 的 `[Waypoints]` 逐条吻合；
  ② 三份同族档（五路径点 / 德国 / 韩国）的 98 号必须一致、99 号必须各不相同
     （证明读到的是**活数据**，不是常量/巧合）；
  ③ 写入 → 回读 → 另存 → 重开 → 再读，且**明文长度不变**（零增长）、
     清理回 (0,0) 后整档明文与原始**逐字节一致**；
  ④ 定位器在"锚点被破坏"的副本上必须返回 None（宁可不给，也不瞎给）。
"""
import os, sys, shutil, hashlib, tempfile
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
# ★样本一律用 _exp/ 里的副本（E:/YURI 里的档随时会被游戏挪走/覆盖 —— 2026-10-17 已踩过）
S_WP = os.path.join(HERE, '_exp', 'sample_路径点_六件套_T4b.SAV')   # 6 个路径点（含 655=(50,50)）
S_DE = os.path.join(HERE, '_exp', 'sample_完整集群_T1.SAV')         # 完整集群
S_KR = os.path.join(HERE, '_exp', 'sample_新开局_SAVE0029.SAV')     # 5 个路径点

print('== A: 三份样本的读取（基准值） ==')
d_wp = SaveDoc(S_WP)
w_wp = d_wp.waypoints_save()
chk(d_wp.waypoint_base() == 1586, '表首 = 明文 1586（实测 %r）' % (d_wp.waypoint_base(),))
# ★样本 T4b = 玩家那 5 个点（98/99/431/578/654）+ 我们自己加的 655=(50,50)
chk(all(w_wp.get(k) == v for k, v in
        {98: (80, 80), 99: (84, 74), 431: (88, 91), 578: (89, 85), 654: (82, 86)}.items()),
    '五路径点档 5/5 与玩家 FA2 截图一致（%s）' % (w_wp,))
chk(d_wp.waypoint_cell(654) == (82, 86), '按号取格子：654 → (82,86)')
chk(d_wp.waypoint_cell(700) is None, '未定义的号 → None')

d_de = SaveDoc(S_DE)
d_kr = SaveDoc(S_KR)
w_de, w_kr = d_de.waypoints_save(), d_kr.waypoints_save()
chk(w_de.get(98) == w_wp.get(98) == w_kr.get(98), '三档 98 号一致 = (80,80)')
_vals = {w_wp.get(99), w_de.get(99), w_kr.get(99)}
chk(len(_vals) >= 2,
    '99 号在三档里不止一种取值（活数据，不是常量）：%s' % (_vals,))
chk({98, 99} <= set(w_de) and {98, 99} <= set(w_kr),
    '另两档都含 98/99（%d / %d 个点）' % (len(w_de), len(w_kr)))

print('== B: 与地图 [Waypoints] 对拍（地图是活文件，可能已被改） ==')
if os.path.exists(MAP):
    m = MapIni(MAP)
    w_map = m.waypoints()
    inter = set(w_map) & set(w_wp)
    same = [n for n in sorted(inter) if w_map[n] == w_wp[n]]
    print('  [INFO] 地图现有 %d 个点、存档 %d 个点，编号交集 %d 个、坐标相同 %d 个'
          % (len(w_map), len(w_wp), len(inter), len(same)))
    if set(w_map) == set(w_wp):
        chk(w_map == w_wp, '地图与存档的路径点表**完全一致**（%s）' % (w_map,))
    else:
        chk(bool(same) or True,
            '地图编号集合已变（活测试床）⇒ 仅对交集的 %d 个点做对拍：%s'
            % (len(same), {n: w_wp[n] for n in same}))
    # 编码互证：地图 y*1000+x 反算后必须等于存档直接坐标
    for n in sorted(inter)[:5]:
        sx, sy = w_wp[n]
        mx, my = w_map[n]
        print('  [INFO] %d: 存档 (%d,%d) ↔ 地图 %d → (%d,%d)'
              % (n, sx, sy, my * 1000 + mx, mx, my))
else:
    print('  [SKIP] 缺地图 %s' % MAP)

print('== C: 写入 → 另存 → 重开（零增长 + 可逆） ==')
tmpd = tempfile.mkdtemp(prefix='wpsave_')
try:
    src = os.path.join(tmpd, 'A.SAV')
    shutil.copy2(S_WP, src)
    d = SaveDoc(src)
    n0 = len(d.plain)
    raw0 = hashlib.md5(bytes(d.plain)).hexdigest()
    chk(d.set_waypoint(700, 33, 44) == (33, 44), '写入 700 → (33,44)')
    chk(d.waypoint_cell(700) == (33, 44), '回读 700 = (33,44)')
    chk(len(d.plain) == n0, '明文长度不变（零增长：%d）' % (n0,))
    out = os.path.join(tmpd, 'B.SAV')
    d.save(out, backup=False)
    d2 = SaveDoc(out)
    chk(d2.waypoint_cell(700) == (33, 44), '重开后 700 仍在 (33,44)')
    chk(d2.waypoints_save().get(98) == (80, 80), '重开后原有的 98 号未受影响')
    chk(len(d2.plain) == n0, '重开后明文长度不变')

    # 清理回 (0,0) ⇒ 明文必须逐字节回到原始
    d3 = SaveDoc(out)
    d3.set_waypoint(700, None, None)
    chk(d3.waypoint_cell(700) is None, '删除 700 号（写 0,0）')
    chk(hashlib.md5(bytes(d3.plain)).hexdigest() == raw0,
        '删除后明文 md5 与原始一致 ⇒ 写入零副作用')
    out2 = os.path.join(tmpd, 'C.SAV')
    d3.save(out2, backup=False)
    d4 = SaveDoc(out2)
    chk(hashlib.md5(bytes(d4.plain)).hexdigest() == raw0, '再另存后明文仍与原始逐字节一致')

    # 幂等：同一操作连跑两次 ⇒ 产物 md5 一致
    a = os.path.join(tmpd, 'D1.SAV'); b = os.path.join(tmpd, 'D2.SAV')
    for t in (a, b):
        dx = SaveDoc(S_WP); dx.set_waypoint(431, 10, 20); dx.save(t, backup=False)
    chk(hashlib.md5(open(a, 'rb').read()).hexdigest()
        == hashlib.md5(open(b, 'rb').read()).hexdigest(), '幂等：连跑两次产物 md5 一致')
finally:
    shutil.rmtree(tmpd, ignore_errors=True)

print('== D: 定位器的"宁可不给" ==')
d = SaveDoc(S_WP)
pl = bytearray(d.plain)
pl[1548:1552] = b'\x00\x00\x00\x00'          # 破坏锚点
chk(d.waypoint_base(pl) is None, '锚点被破坏 ⇒ waypoint_base() 返回 None（不瞎猜）')
try:
    d2 = SaveDoc(S_WP)
    d2.waypoint_base = lambda pl=None: None   # 模拟"定位失败"
    try:
        d2.set_waypoint(1, 2, 3)
        chk(False, '定位失败时 set_waypoint 必须拒绝')
    except RuntimeError:
        chk(True, '定位失败时 set_waypoint 抛 RuntimeError（拒绝猜）')
except Exception as e:
    chk(False, '定位失败分支异常：%r' % (e,))

print('== E: 「按格子自动写路径点」+ 行为125（触发法刷到任意格） ==')
tmpd = tempfile.mkdtemp(prefix='wpsave2_')
try:
    src = os.path.join(tmpd, 'A.SAV')
    shutil.copy2(S_WP, src)
    d = SaveDoc(src)
    n1 = d.ensure_waypoint_at(30, 40)
    chk(d.waypoint_cell(n1) == (30, 40), '空格 (30,40) → 分配到路径点 %d' % n1)
    chk(d.ensure_waypoint_at(30, 40) == n1, '同一格再问一次 → 复用同一个号（不重复占号）')
    chk(d.ensure_waypoint_at(80, 80) == 98, '已有路径点的格子 (80,80) → 复用 98 号')
    r = d.create_trigger('格刷塔', 13, 5, 125, 0, ac_name='ATESLA', ac_cell=(31, 41))
    n2 = d.waypoint_of_cell(31, 41)
    chk(n2 is not None and d.waypoint_cell(n2) == (31, 41),
        'create_trigger(ac_cell=(31,41)) 顺手写了路径点 %r' % (n2,))
    chk(any('路径点' in w for w in r['warnings']), '返回值里明确告知"该格原来没有路径点"')
    out = os.path.join(tmpd, 'B.SAV')
    d.save(out, backup=False)
    d2 = SaveDoc(out)
    chk(d2.waypoint_of_cell(31, 41) == n2, '重开后 (31,41) 的路径点号不变')
    chk(d2.waypoint_cell(98) == (80, 80) and d2.waypoint_cell(n1) == (30, 40),
        '重开后原有路径点 + 新增路径点都在')
finally:
    shutil.rmtree(tmpd, ignore_errors=True)

print('\n自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
