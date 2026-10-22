# -*- coding: utf-8 -*-
"""§171 自检：建造动画「播两遍 vs 播一遍」定案 + 跨类型配方通用性。

in-game 真值（字段序 AC/BC/D0/10C/534/6E4）：
    原始A / M          18/1/1/2/0/0|1   → 播**一遍** ✅
    Q（只 BC=0）       18/0/1/2/0/0     → 播**一遍** ✅     ⇒ BC 与次数无关
    P（只 D0=100）     18/1/100/2/0/0   → 播**两遍** ❌     ⇒ D0 是元凶
    R（BC=0 + D0=100） 18/0/100/2/0/0   → 播**两遍** ❌     ⇒ 只看 D0
    N2（新加 ATESLA）  18/1/1/2/0/0     → 播**一遍** ✅     ⇒ AC=18 跨类型通用
    自检产物           18/0/100/2/0/1   → 播**两遍**（同上，D0=100 惹的）

本自检把这些全部固化成断言，并把「replay 标志」当成可回归的判据。
"""
import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.editor_core import SaveDoc

E = 'E:/YURI/'
ARC = E + '新建文件夹 (26)/'
ORIG = ARC + '电厂放下 动画未播放完成.SAV'
KEYS = ['+0xAC', '+0xBC', '+0xD0', '+0x10C', '+0x534', '+0x6E4']


def fp(name):
    """找档：玩家会随时把 E:/YURI/*.SAV 归档进 `新建文件夹 (NN)` ⇒ 走递归定位器。"""
    from _paths import find_sav
    return find_sav(name) or (E + name)   # 找不到时返回默认位置，交给上层报错

n_ok = n_bad = 0
fails = []


def ck(name, cond, extra=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('  PASS  %s' % name)
    else:
        n_bad += 1
        fails.append(name)
        print('  FAIL  %s   %s' % (name, extra))


def vec(st):
    return '/'.join('%d' % st['vals'][k] for k in KEYS)


def vec5(st):
    """只看 AC/BC/D0/10C/534 —— `+0x6E4` 在建造动画分支里**无关**（§170：原始A=0 与 H=1 都播一遍），
    克隆模板不同会让它继承 0 或 1，两种都合法（§184 换模板后这里实测出现过 1）。"""
    return '/'.join('%d' % st['vals'][k] for k in KEYS[:5])


def at(path, t, x, y):
    d = SaveDoc(path)
    for b in d.buildings():
        if b['type'] == t and b['x'] == int(x) and b['y'] == int(y):
            return d, b, d.building_anim_state(b['base'])
    raise RuntimeError('%s 里找不到 %s(%d,%d)' % (os.path.basename(path), t, x, y))


print('== 1. P/Q/R 三档：只有 +0xD0 决定播几次 ==')
CASES = [
    ('原始A', ORIG, 'GAPOWR', 80, 82, '18/1/1/2/0/0', 'buildup', False),
    ('Q 只改 BC=0', fp('电厂修复Q_只改BC为0.SAV'), 'GAPOWR', 80, 82,
     '18/0/1/2/0/0', 'buildup', False),
    ('P 只改 D0=100', fp('电厂修复P_只改完成度100.SAV'), 'GAPOWR', 80, 82,
     '18/1/100/2/0/0', 'buildup', True),
    ('R BC0+D0100', fp('电厂修复R_BC0加D0100.SAV'), 'GAPOWR', 80, 82,
     '18/0/100/2/0/0', 'buildup', True),
    ('M 完成态→建造中', fp('电厂修复M_老电厂转建造中.SAV'), 'GAPOWR', 73, 87,
     '18/1/1/2/0/1', 'buildup', False),
    # §172 修掉 `BLD_ANIM_BUILDUP_MIN` 缺 +0xD0=1 之后，产物变成"播一遍"的合法中间态
    # ★§189：ev170 的产物已改到工程内 `_selftest_out/`（不再污染 E:/YURI）
    # §187 起配方统一带 `6E4=0`（原来继承模板的 1）⇒ 期望值跟着改
    ('自检产物', __import__('_paths').out('ev170_buildup.SAV'), 'GAPOWR', 80, 82,
     '18/1/1/2/0/0', 'buildup', False),
]
for name, path, t, x, y, want_vec, want_state, want_replay in CASES:
    if not os.path.exists(path):
        print('  SKIP  %s（样本不存在：%s；先跑 _selftest_ev170.py 会生成）'
              % (name, os.path.basename(path)))
        continue
    if not os.path.exists(path):
        ck('%s 文件存在' % name, False, path)
        continue
    d, b, st = at(path, t, x, y)
    ck('%s 六元组 = %s' % (name, want_vec), vec(st) == want_vec, vec(st))
    ck('%s state = %s' % (name, want_state), st['state'] == want_state, st['state'])
    ck('%s replay = %s' % (name, want_replay), bool(st.get('replay')) == want_replay,
       str(st.get('replay')))

print()
print('== 2. 归因断言（三组硬对照，只差一个字段）==')
_, _, sA = at(ORIG, 'GAPOWR', 80, 82)
_, _, sQ = at(fp('电厂修复Q_只改BC为0.SAV'), 'GAPOWR', 80, 82)
_, _, sP = at(fp('电厂修复P_只改完成度100.SAV'), 'GAPOWR', 80, 82)
_, _, sR = at(fp('电厂修复R_BC0加D0100.SAV'), 'GAPOWR', 80, 82)
dv = lambda a, b: [k for k in KEYS if a['vals'][k] != b['vals'][k]]
ck('原始A vs Q 只差 +0xBC', dv(sA, sQ) == ['+0xBC'], dv(sA, sQ))
ck('原始A vs P 只差 +0xD0', dv(sA, sP) == ['+0xD0'], dv(sA, sP))
ck('Q vs R 只差 +0xD0（BC 已经同为 0）', dv(sQ, sR) == ['+0xD0'], dv(sQ, sR))
ck('★BC 改了不影响 replay（Q replay=False）', sQ['state'] == 'buildup' and not sQ.get('replay'))
ck('★D0=100 一定 replay（P / R 都是）', sP.get('replay') and sR.get('replay'))
ck('判据等价于 D0 >= BLD_D0_REPLAY',
   all((bool(st.get('replay')) == (st['vals']['+0xD0'] >= SaveDoc.BLD_D0_REPLAY))
       for st in (sA, sQ, sP, sR)))

print()
print('== 3. N2：AC=18 跨建筑类型通用 ==')
P_N2 = fp('电厂修复N2_ATESLA全套建造中.SAV')
ck('N2 基档是 0.75MB 级（没有拿错大档）', os.path.getsize(P_N2) < 1500000,
   '%d B' % os.path.getsize(P_N2))
d2, b2, s2 = at(P_N2, 'ATESLA', 68, 85)
ck('N2 里 ATESLA(68,85) 六元组 = 18/1/1/2/0/0', vec(s2) == '18/1/1/2/0/0', vec(s2))
ck('N2 ATESLA state = buildup', s2['state'] == 'buildup')
ck('N2 ATESLA replay = False（播一遍）', not s2.get('replay'))
# 同档实测：新加的 ATESLA 与原始A 的老电厂字段完全一致 ⇒ 跨类型通用
_, _, sA2 = at(P_N2, 'GAPOWR', 80, 82)
ck('同档 GAPOWR(80,82) 与 ATESLA(68,85) 六元组一致（跨类型通用）',
   sA2['vals'] == s2['vals'], '%s vs %s' % (vec(sA2), vec(s2)))
ck('BLD_ANIM_AC_BUILDUP 已登记 ATESLA', SaveDoc.BLD_ANIM_AC_BUILDUP.get('ATESLA') == 18)

print()
print('== 4. make_building_buildup 默认 = 全套 6 字段（含 6E4=0）+ D0=1 ==')
d3 = SaveDoc(fp('电厂修复G_自动完成度.SAV'))
b3 = d3.buildings()[0]
r = d3.make_building_buildup(b3['base'])
st = d3.building_anim_state(b3['base'])
# §187：配方额外带 `+0x6E4=0` —— 凭空插的楼没有伴随对象，这个开关决定播不播
ck('默认写 6 字段（含 6E4=0）',
   set(r['new']) == {'+0xAC', '+0xBC', '+0xD0', '+0x10C', '+0x534', '+0x6E4'},
   str(sorted(r['new'])))
ck('默认 AC=18', st['vals']['+0xAC'] == 18)
ck('默认 BC=1', st['vals']['+0xBC'] == 1)
ck('★默认 D0=1（不会播两遍）', st['vals']['+0xD0'] == 1)
ck('默认 10C=2', st['vals']['+0x10C'] == 2)
ck('默认 534=0', st['vals']['+0x534'] == 0)
ck('改完 replay = False', not st.get('replay'))
ck('改完状态 = buildup', st['state'] == 'buildup')
# 反过来：把 D0 写成 100 ⇒ replay 立刻变 True
struct.pack_into('<I', d3.plain, b3['base'] + SaveDoc.BLD_ANIM_OFF['+0xD0'], 100)
d3._live_len = None
ck('★手动把 D0 写回 100 后 replay 立刻变 True',
   d3.building_anim_state(b3['base']).get('replay') is True)
# full=False 分支只少 BC
r4 = d3.make_building_buildup(b3['base'], full=False)
ck('full=False 写 5 字段（省 BC，仍带 6E4=0）',
   set(r4['new']) == {'+0xAC', '+0xD0', '+0x10C', '+0x534', '+0x6E4'})
ck('full=False 仍然 D0=1', d3.building_anim_state(b3['base'])['vals']['+0xD0'] == 1)

print()
print('== 5. 全库扫描：replay 态有多少（不该把正常档判成坏档）==')
tot = n_rep = n_bad_state = 0
for p in (ORIG, fp('电厂修复M_老电厂转建造中.SAV'), P_N2,
          fp('电厂修复P_只改完成度100.SAV'), fp('电厂修复R_BC0加D0100.SAV')):
    d = SaveDoc(p)
    for b in d.buildings():
        try:
            st = d.building_anim_state(b['base'])
        except Exception:
            continue
        tot += 1
        if st.get('replay'):
            n_rep += 1
        if st['state'] in ('stuck', 'frozen', 'loop'):
            n_bad_state += 1
print('  共 %d 栋：replay(会播两遍) %d 栋，真坏态(stuck/frozen/loop) %d 栋'
      % (tot, n_rep, n_bad_state))
ck('replay 不算坏态，真坏态仍为 0', n_bad_state == 0, '%d' % n_bad_state)
ck('P/R 两档确实各贡献了 replay', n_rep >= 2, '%d' % n_rep)
_dn, _bn, _ = at(P_N2, 'ATESLA', 68, 85)
ck('repair_building_anim 对 replay(buildup) 仍拒修（不误伤合法中间态）',
   not _dn.repair_building_anim(_bn['base'])['changed'])

print()
print('== 6. add_placed_building(buildup=True)：新楼自带一遍建造动画 ==')
d5 = SaveDoc(ORIG)
n0 = len(d5.buildings())
cell = d5.free_cell_near(76, 85, radius=14, clearance=3)
info = d5.add_placed_building('GAPOWR', cell[0], cell[1],
                              comp_mode='zero', new_state=True, buildup=True)
ck('加建筑成功且标记 buildup=True', info.get('buildup') is True)
ck('栋数 +1', len(d5.buildings()) == n0 + 1, '%d → %d' % (n0, len(d5.buildings())))
_nb = [b for b in d5.buildings() if (b['x'], b['y']) == cell][0]
_ns = d5.building_anim_state(_nb['base'])
ck('新楼五元组 = 18/1/1/2/0（6E4 无关）', vec5(_ns) == '18/1/1/2/0', vec(_ns))
ck('新楼 state = buildup', _ns['state'] == 'buildup')
ck('★新楼 replay = False（播一遍，不是两遍）', not _ns.get('replay'))
ck('新楼位置 = 找的空地 %s' % (cell,), (_nb['x'], _nb['y']) == cell)
# ★★ 2026-10-22 修正：**自检产物绝不能写进玩家的验收档名**
#   （原来自检写的就是 `电厂修复S_加建筑带建造动画.SAV`，把玩家 15:00 的验收档冲掉了，
#     害得 §185 拿覆盖版当证据 —— 详见主文档 §185.7 / §186）
from _paths import out as _out
_pS = _out('ev171_S_加建筑带建造动画.SAV')
d5.save(_pS)
_d6 = SaveDoc(_pS)
_n6 = [b for b in _d6.buildings() if (b['x'], b['y']) == cell][0]
ck('存盘回读后新楼仍是 18/1/1/2/0 且 buildup',
   vec5(_d6.building_anim_state(_n6['base'])) == '18/1/1/2/0'
   and _d6.building_anim_state(_n6['base'])['state'] == 'buildup')
# ★§188（2026-10-22 玩家 T16 实测定案）：默认**是 True** —— 直接落成会播一遍建造动画，
#   前提是配方带 `+0x6E4=0`（继承到 1 就"不播 + 显示不全"）。§186 那条"播不出"已作废。
d7 = SaveDoc(ORIG)
c7 = d7.free_cell_near(76, 85, radius=14, clearance=3)
_i7 = d7.add_placed_building('GAPOWR', c7[0], c7[1], comp_mode='zero', new_state=True)
ck('§188 不传 buildup 时默认 True', _i7.get('buildup') is True)
_b7 = [b for b in d7.buildings() if (b['x'], b['y']) == c7][0]
_an7 = [struct.unpack_from('<I', d7.plain, _b7['base'] - 72 + 16 + 1388 + 4 * i)[0]
        for i in range(4)]
ck('§188 新楼 Anims[3..6] 全 0（播不播不靠伴随对象，靠 6E4）',
   all(a == 0 for a in _an7), str(_an7))
_vals7 = d7.building_anim_state(_b7['base'])['vals']
ck('§188 新楼 6E4=0（这是"会播一遍"的开关）', _vals7['+0x6E4'] == 0, str(_vals7))

d8 = SaveDoc(ORIG)
c8 = d8.free_cell_near(76, 85, radius=14, clearance=3)
_i8 = d8.add_placed_building('GAPOWR', c8[0], c8[1], comp_mode='zero',
                             new_state=True, buildup=False)
ck('buildup=False 时 info.buildup=False（不动动画字段）', _i8.get('buildup') is False)
_b8 = [b for b in d8.buildings() if (b['x'], b['y']) == c8][0]
_keyof = lambda dd, bb: struct.unpack_from('<I', dd.plain, bb['base'] - 72 + 16)[0]
_tpl = [b for b in d8.buildings() if _keyof(d8, b) == _i8.get('tpl_key')]
ck('buildup=False 时动画字段 = **模板那栋**的原值（原样继承，碰运气）',
   bool(_tpl) and vec(d8.building_anim_state(_b8['base']))
   == vec(d8.building_anim_state(_tpl[0]['base'])),
   '%s vs 模板 %s' % (vec(d8.building_anim_state(_b8['base'])),
                      vec(d8.building_anim_state(_tpl[0]['base'])) if _tpl else 'N/A'))

print()
print('结果：PASS %d / FAIL %d' % (n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  - %s' % f)
print('SELFTEST %s' % ('PASS' if n_bad == 0 else 'FAIL'))
sys.exit(0 if n_bad == 0 else 1)
