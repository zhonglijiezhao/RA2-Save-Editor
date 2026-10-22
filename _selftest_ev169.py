# -*- coding: utf-8 -*-
"""§169 自检（九档定稿版）：建筑「建造动画」三态判定 + 修复/重播。

★九档 in-game 定稿后的模型（全部断言都围绕它）：
    完成标记 = (+0xFC == 0 且 +0x534 == 1)
      * 标记成立 + +0x6E0 == 1  ->  'done'     渲染完成图
      * 标记成立 + +0x6E0 == 0  ->  'stuck'    卡住、显示不全（唯一真坏态）
      * 标记不成立              ->  'buildup'  合法中间态，进游戏播一遍完整 Buildup

in-game 证据（同一栋 GAPOWR(80,82)）：
    原始档 / I 档 18/1/1/2/0/0  ✅ 播完整建造动画（观感最好）
    原始好档       5/0/100/0/1/1 ✅ 完成图
    A 5/1/1/0/1/0               ❌ 显示不全   D 5/0/1/0/1/0  ❌   B 5/1/100/0/1/0 ❌
    E 5/1/1/0/1/1               ✅ 正常       F 5/0/1/0/1/1  ✅   C/G 5/0/100/0/1/1 ✅
    H 18/1/1/2/0/1              ✅ 播完整建造动画
"""
import sys
import os
import glob
import struct

sys.path.insert(0, '.')
from core.editor_core import SaveDoc

ARC = 'E:/YURI/新建文件夹 (26)/'
ORIG = ARC + '电厂放下 动画未播放完成.SAV'    # 原始档（玩家：重载后播完整建造动画）
ORIG_OK = ARC + '电厂动画播放完成.SAV'        # 原始好档（播完动画后存的）
F_I = 'E:/YURI/电厂修复I_完全还原坏值对照.SAV'  # 内容应与原始档完全一致
F_H = 'E:/YURI/电厂修复H_只改6E0其余保留坏值.SAV'
F_A = ARC + '电厂修复A_三指纹.SAV'            # in-game ❌ 显示不全
F_E = ARC + '电厂修复E_只加6E0.SAV'           # in-game ✅
F_G = ARC + '电厂修复G_自动完成度.SAV'         # in-game ✅

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
    return d, b['base'], d.building_anim_state(b['base'])


print('== 1. I 档必须与原始档逐字节相同（本轮最关键的一条事实）==')
d_orig = SaveDoc(ORIG)
d_i = SaveDoc(F_I)
_a, _b = bytes(d_orig.plain), bytes(d_i.plain)
_diff = sum(1 for i in range(min(len(_a), len(_b))) if _a[i] != _b[i])
print('    明文长度 %d / %d，差异字节 %d' % (len(_a), len(_b), _diff))
ck('I 档明文与原始档完全一致', len(_a) == len(_b) and _diff == 0)
ck('因此 I 档的 in-game 表现 == 原始档（玩家：播完整建造动画）', _diff == 0)

print('== 2. 三态判定（六档全表）==')
expect = [
    ('原始档', ORIG, 'buildup'),
    ('原始好档', ORIG_OK, 'done'),
    ('I 档', F_I, 'buildup'),
    ('H 档(6E0=1 但标记不成立)', F_H, 'buildup'),
    ('A 档(标记成立但 6E0=0)', F_A, 'stuck'),
    ('E 档', F_E, 'done'),
    ('G 档', F_G, 'done'),
]
states = {}
for tag, path, want in expect:
    d, base, st = state_of(path)
    states[tag] = st
    print('    %-26s %s  标记=%-5s 接受=%-5s -> %s' %
          (tag, st['vals'], st['done_mark'], st['accepted'], st['state']))
    ck('%s 判为 %s' % (tag, want), st['state'] == want)

print('== 3. 字段值核对（防止偏移再次搞反）==')
v = states['原始档']['vals']
ck('原始档 +0xFC=2（标记不成立）', v['+0xFC'] == 2)
ck('原始档 +0x534=0（标记不成立）', v['+0x534'] == 0)
ck('原始档 +0x6E0=0', v['+0x6E0'] == 0)
ck('原始档 +0xAC=18', v['+0xAC'] == 18)
ck('A 档 done_mark=True 但 accepted=False', states['A 档(标记成立但 6E0=0)']['done_mark']
   and not states['A 档(标记成立但 6E0=0)']['accepted'])
ck('H 档 done_mark=False 但 accepted=True',
   not states['H 档(6E0=1 但标记不成立)']['done_mark']
   and states['H 档(6E0=1 但标记不成立)']['accepted'])

print('== 4. pending 兼容字段只对 stuck 为真 ==')
ck('buildup 时 pending=False（不再报假警）', states['原始档']['pending'] is False)
ck('stuck 时 pending=True', states['A 档(标记成立但 6E0=0)']['pending'] is True)
ck('done 时 pending=False', states['E 档']['pending'] is False)

print('== 5. 修复行为：buildup 要拒绝，stuck 只写 1 字节 ==')
d = SaveDoc(ORIG)
b = d.building_at(80, 82, 'GAPOWR')
n0 = len(d.plain)
r = d.repair_building_anim(b['base'])
ck('对 buildup 拒绝修复（changed=False）', r['changed'] is False)
ck('拒绝时给出理由', bool(r.get('reason')))
ck('拒绝后字段值一个没动', r['old'] == r['new'])
ck('拒绝后明文长度不变', len(d.plain) == n0)

d_a = SaveDoc(F_A)
ba = d_a.building_at(80, 82, 'GAPOWR')
o6 = ba['base'] + d_a.BLD_ANIM_OFF['+0x6E0']
hi_before = bytes(d_a.plain[o6 + 1:o6 + 4])
before_vals = dict(d_a.building_anim_state(ba['base'])['vals'])
r_a = d_a.repair_building_anim(ba['base'])
after_vals = dict(d_a.building_anim_state(ba['base'])['vals'])
ck('对 stuck 修复 changed=True', r_a['changed'] is True)
ck('最小修复集只有 +0x6E0', set(r_a['new']) == {'+0x6E0'})
ck('+0x6E0 只写 1 字节（高 3 字节不变）', hi_before == bytes(d_a.plain[o6 + 1:o6 + 4]))
ck('其余 5 个字段一个没动',
   all(after_vals[k] == before_vals[k] for k in before_vals if k != '+0x6E0'))
ck('修复后 state=done', d_a.building_anim_state(ba['base'])['state'] == 'done')
ck('A + 6E0=1 == E 档（in-game 已验证正常）',
   all(after_vals[k] == states['E 档']['vals'][k] for k in after_vals))

print('== 6. make_building_buildup：让完成态重播建造动画 ==')
d_e = SaveDoc(F_E)
be = d_e.building_at(80, 82, 'GAPOWR')
rc = d_e.make_building_buildup(be['base'])
ck('对 done 建筑写入成功', rc['changed'] is True)
ck('只改 +0x534', set(rc['new']) == {'+0x534'})
st = d_e.building_anim_state(be['base'])
ck('改完 state=buildup', st['state'] == 'buildup')
ck('+0x6E0 未被改动（H 档证明它=1 也照样播）',
   st['vals']['+0x6E0'] == states['E 档']['vals']['+0x6E0'])
ck('明文字节数不变', len(d_e.plain) == len(SaveDoc(F_E).plain))

print('== 7. 存盘 / 重读（不做坏件）==')
from _paths import out as _out
out = _out('ev169_out.SAV')
d_a.save(out)
dr = SaveDoc(out)
st_r = dr.building_anim_state(dr.building_at(80, 82, 'GAPOWR')['base'])
ck('重读后 state=done', st_r['state'] == 'done')
ck('重读后 +0x6E0=1', st_r['vals']['+0x6E0'] == 1)
ck('重读后建筑数没少（6 栋）', len(dr.buildings()) == 6)
try:
    os.remove(out)
except OSError:
    pass

print('== 8. 全库扫描：自然存档里 stuck 应为 0，buildup 是常态 ==')
cand = sorted(glob.glob('E:/YURI/*.SAV')) + sorted(glob.glob('E:/YURI/新建文件夹*/*.SAV'))
cand = [f for f in cand if '电厂修复' not in f and '_selftest' not in f][:16]
if ORIG not in cand:            # 原始档文件名排序靠后，必须显式纳入（它是 buildup 的自然样本）
    cand.insert(0, ORIG)
stuck, buildup, done, nfiles, nbl = [], 0, 0, 0, 0
for f in cand:
    try:
        dd = SaveDoc(f)
        bl = dd.buildings()
    except Exception:
        continue
    nfiles += 1
    for bb in bl:
        try:
            s = dd.building_anim_state(bb['base'])
        except Exception:
            continue
        nbl += 1
        if s['state'] == 'stuck':
            stuck.append((os.path.basename(f), bb['type'], bb['x'], bb['y'], s['vals']))
        elif s['state'] == 'buildup':
            buildup += 1
        else:
            done += 1
print('    %d 档 / %d 栋：done=%d  buildup=%d  stuck=%d'
      % (nfiles, nbl, done, buildup, len(stuck)))
print('    stuck 清单:', stuck[:3])
ck('自然存档里没有 stuck 建筑（%d 档 / %d 栋）' % (nfiles, nbl), len(stuck) == 0)
ck('buildup 是常态而非异常（全库应有 >0 栋）', buildup > 0)

print('== 9. 三份撤标记实验档已生成（J/K/L，等 in-game）==')
for n in ('电厂修复J_撤标记只改534', '电厂修复K_撤标记含FC复核', '电厂修复L_完成度100撤标记'):
    p = 'E:/YURI/' + n + '.SAV'
    ok = os.path.exists(p)
    ck('%s 存在' % n, ok)
    if ok:
        _d, _base, _s = state_of(p)
        ck('%s state=buildup' % n, _s['state'] == 'buildup')

print('\n== 结果: PASS %d / FAIL %d ==' % (P, F))
sys.exit(0 if F == 0 else 1)
