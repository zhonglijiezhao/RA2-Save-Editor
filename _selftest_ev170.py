# -*- coding: utf-8 -*-
"""§170 自检（十四档定稿版）：建筑「建造动画」五态判定 + 修复 / 重播。

★★本节推翻了 §169 的两条结论，必须先读懂这张表：
    字段序（对象偏移） AC / BC / D0 / 10C / 534 / 6E4
    档                      五元组            in-game 实测
    ──────────────────────────────────────────────────────────────
    原始A 放下未播完         18/1/1/2/0/0   ✅ 播完整建造动画（观感最好）
    I   完全还原(=原始A)     18/1/1/2/0/0   ✅ 同上
    H   原始值 + 6E4=1      18/1/1/2/0/1   ✅ 播完整建造动画
    原始B 播放完成            5/0/100/0/1/1  ✅ 完成图
    C/E/F/G                  5/*/*/0/1/1    ✅ 正常
    A/B/D                    5/*/*/0/1/0    ❌ 显示不全
    J   只撤 534             5/1/1/0/0/1   ❌ 显示不全
    K   撤 534 + 10C=2       5/1/1/2/0/1   ❌ **一直播放建造动画**
    L   D0=100 撤 534        5/0/100/0/0/1 ❌ 显示不全
    ──────────────────────────────────────────────────────────────
    ⇒ H vs K 只差 +0xAC（18 vs 5）⇒ AC 决定"播完会不会停"
    ⇒ K vs J 只差 +0x10C（2 vs 0）⇒ 10C=0 根本不播
    ⇒ A vs H 只差 +0x6E4 ⇒ 建造动画分支里无关

五态模型：
    if 534 == 1:  6E4==1 -> done | 否则 stuck
    else:         10C==0 -> frozen | AC==5 -> loop | 否则 buildup
"""
import os
import sys
import glob

sys.path.insert(0, '.')
from core.editor_core import SaveDoc

ARC = 'E:/YURI/新建文件夹 (26)/'
E = 'E:/YURI/'
ORIG = ARC + '电厂放下 动画未播放完成.SAV'
ORIG_OK = ARC + '电厂动画播放完成.SAV'


def fp(name):
    """E:/YURI 与 新建文件夹 (26) 两处都可以（玩家习惯把档归到子目录）。"""
    for root in (E, ARC):
        if os.path.exists(root + name):
            return root + name
    return E + name
FILES = [
    ('原始A 放下未播完', ORIG, 'buildup'),
    ('原始B 播放完成', ORIG_OK, 'done'),
    ('I 完全还原', fp('电厂修复I_完全还原坏值对照.SAV'), 'buildup'),
    ('H 原始值+6E4', fp('电厂修复H_只改6E0其余保留坏值.SAV'), 'buildup'),
    ('A 三指纹', ARC + '电厂修复A_三指纹.SAV', 'stuck'),
    ('B 三指纹+完成度', ARC + '电厂修复B_三指纹加完成度.SAV', 'stuck'),
    ('C 全部还原', ARC + '电厂修复C_全部还原.SAV', 'done'),
    ('D 只加BC', ARC + '电厂修复D_只加BC.SAV', 'stuck'),
    ('E 只加6E4', ARC + '电厂修复E_只加6E0.SAV', 'done'),
    ('F 不碰完成度', ARC + '电厂修复F_全字段不碰完成度.SAV', 'done'),
    ('G 自动完成度', ARC + '电厂修复G_自动完成度.SAV', 'done'),
    ('J 只撤534', fp('电厂修复J_撤标记只改534.SAV'), 'frozen'),
    ('K 撤534+10C', fp('电厂修复K_撤标记含FC复核.SAV'), 'loop'),
    ('L D0=100撤534', fp('电厂修复L_完成度100撤标记.SAV'), 'frozen'),
]
NAMES = ['+0xAC', '+0xBC', '+0xD0', '+0x10C', '+0x534', '+0x6E4']

P = F = 0


def ck(name, cond):
    global P, F
    if cond:
        P += 1
        print('  PASS  ' + name)
    else:
        F += 1
        print('  FAIL  ' + name)


def state_of(path, x=80, y=82):
    d = SaveDoc(path)
    b = d.building_at(x, y, 'GAPOWR')
    return d, b, d.building_anim_state(b['base'])


def vec(st):
    return tuple(st['vals'][k] for k in NAMES)


print('== 1. 十四档状态判定（对照玩家 in-game 实测）==')
for label, path, want in FILES:
    try:
        _d, _b, st = state_of(path)
    except Exception as e:
        ck('%s 读取' % label, False)
        print('        %s' % e)
        continue
    got = st['state']
    ck('%-18s %-22s -> %-8s（期望 %s）' % (
        label, '/'.join('%d' % x for x in vec(st)), got, want), got == want)

print()
print('== 2. 关键单变量对照（本轮全部结论都靠它们）==')
_dh, _bh, h = state_of(fp('电厂修复H_只改6E0其余保留坏值.SAV'))
_dk, _bk, k = state_of(fp('电厂修复K_撤标记含FC复核.SAV'))
_dj, _bj, j = state_of(fp('电厂修复J_撤标记只改534.SAV'))
_da, _ba, a = state_of(ORIG)
diff_hk = [n for n in NAMES if h['vals'][n] != k['vals'][n]]
diff_kj = [n for n in NAMES if k['vals'][n] != j['vals'][n]]
diff_ah = [n for n in NAMES if a['vals'][n] != h['vals'][n]]
print('    H vs K 差异字段 = %s' % diff_hk)
print('    K vs J 差异字段 = %s' % diff_kj)
print('    原始A vs H 差异字段 = %s' % diff_ah)
ck('H(完美) 与 K(一直播) 只差 +0xAC', diff_hk == ['+0xAC'])
ck('K(一直播) 与 J(显示不全) 只差 +0x10C', diff_kj == ['+0x10C'])
ck('原始A(完美) 与 H(完美) 只差 +0x6E4', diff_ah == ['+0x6E4'])
ck('+0xAC 差异方向 = 18(停) vs 5(循环)', h['vals']['+0xAC'] == 18 and k['vals']['+0xAC'] == 5)

print()
print('== 3. 常量表自洽 ==')
ck('BLD_ANIM_U8 只含 u8 字段 +0x6E4', SaveDoc.BLD_ANIM_U8 == {'+0x6E4'})
ck('完成态配方 = AC5/10C0/534_1/6E4_1',
   SaveDoc.BLD_ANIM_OK == {'+0xAC': 5, '+0x10C': 0, '+0x534': 1, '+0x6E4': 1})
ck('建造中最小配方 = AC18/D01/10C2/534_0（§171 起含 +0xD0=1）',
   SaveDoc.BLD_ANIM_BUILDUP_MIN == {'+0xAC': 18, '+0xD0': 1, '+0x10C': 2, '+0x534': 0})
ck('建造中整套配方 = AC18/BC1/D01/10C2/534_0',
   SaveDoc.BLD_ANIM_BUILDUP_FULL == {'+0xAC': 18, '+0xBC': 1, '+0xD0': 1,
                                     '+0x10C': 2, '+0x534': 0})
ck('§171 常量：建造中 D0=1、D0>=2 会多播一遍', SaveDoc.BLD_D0_BUILDUP == 1
   and SaveDoc.BLD_D0_REPLAY == 2)
ck('老标签别名', SaveDoc.BLD_ANIM_OFF_ALIAS.get('+0x6E0') == '+0x6E4')

print()
print('== 4. make_building_buildup：完成态 → 建造中（存档内实测 + 回验）==')
d, b, st0 = state_of(ARC + '电厂修复G_自动完成度.SAV')
ck('G 档起点是 done', st0['state'] == 'done')
r = d.make_building_buildup(b['base'])
st1 = d.building_anim_state(b['base'])
ck('改完状态 = buildup', st1['state'] == 'buildup')
ck('默认写 5 个字段（§171 起 full 为默认）',
   set(r['new']) == {'+0xAC', '+0xBC', '+0xD0', '+0x10C', '+0x534'})
ck('改了 +0xAC -> 18', st1['vals']['+0xAC'] == 18)
ck('改了 +0x10C -> 2', st1['vals']['+0x10C'] == 2)
ck('改了 +0x534 -> 0', st1['vals']['+0x534'] == 0)
ck('★§171：+0xD0 -> 1（写 100 会播两遍，P/R 档 in-game 实测）',
   st1['vals']['+0xD0'] == 1)
ck('+0x6E4 没动', st1['vals']['+0x6E4'] == st0['vals']['+0x6E4'])
from _paths import out as _out
p = _out('ev170_buildup.SAV')
d.save(p)
d2 = SaveDoc(p)
st2 = d2.building_anim_state(b['base'])
ck('存盘重读后仍是 buildup 且字段一致', st2['state'] == 'buildup' and vec(st2) == vec(st1))

print()
print('== 5. repair_building_anim：三种坏态各自修好 ==')
for label, path, want_before in (
        ('stuck  A 档', ARC + '电厂修复A_三指纹.SAV', 'stuck'),
        ('frozen J 档', fp('电厂修复J_撤标记只改534.SAV'), 'frozen'),
        ('loop   K 档', fp('电厂修复K_撤标记含FC复核.SAV'), 'loop')):
    d, b, st = state_of(path)
    ck('%s 起点 = %s' % (label, want_before), st['state'] == want_before)
    r = d.repair_building_anim(b['base'])
    st_after = d.building_anim_state(b['base'])
    ck('%s 修完 = done（写 %s）' % (label, [k for k in r['new']]),
       st_after['state'] == 'done')
    ck('%s 只动最小字段（不碰 +0xD0/+0xBC）' % label,
       all(k in ('+0xAC', '+0x10C', '+0x534', '+0x6E4') for k in r['new']))

print()
print('== 6. 合法中间态必须拒修（buildup 不该被"修"）==')
d, b, st = state_of(ORIG)
ck('原始档是 buildup', st['state'] == 'buildup')
r = d.repair_building_anim(b['base'])
ck('repair 拒绝改动', r['changed'] is False and bool(r['reason']))
st2 = d.building_anim_state(b['base'])
ck('字段原封不动', vec(st2) == vec(st))

print()
print('== 7. 全库回归：E:/YURI 下所有存档都能判态不抛异常 ==')
n_ok = n_err = 0
stat = {}
for f in sorted(glob.glob(fp('*.SAV'))):
    try:
        dd = SaveDoc(f)
        bl = dd.buildings()
    except Exception:
        continue
    for bb in bl:
        try:
            s = dd.building_anim_state(bb['base'])['state']
            stat[s] = stat.get(s, 0) + 1
            n_ok += 1
        except Exception:
            n_err += 1
print('    判态成功 %d 栋，失败 %d 栋，分布 %s' % (n_ok, n_err, stat))
ck('全库判态无异常', n_err == 0 and n_ok > 0)
ck('全库没有 loop 态（除 K 档那只实验品）', stat.get('loop', 0) <= 1)

print()
print('=' * 60)
print('PASS %d / FAIL %d' % (P, F))
sys.exit(0 if F == 0 else 1)
