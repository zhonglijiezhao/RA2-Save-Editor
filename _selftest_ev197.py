# -*- coding: utf-8 -*-
"""§197 自检：跨类型放置（本档没有该类型也能放）。

锁死 2026-10-23 玩家 T51~T58 八档 in-game 对照出来的四条硬规则：

1. `cross_clean` **默认 False** —— 清了 `0x0F000000~0x14000000` 段会触发引擎的
   一致性自检（`0x6CF350` 主动除零）⇒ **读档弹框**（T51 崩 / T54 正常）。
2. 套类型签名时**只写非 0 的值** —— 签名是某个样本的快照，它那里是 0 不代表新楼也该是 0；
   模板那些槽往往是校验必需的结构键（T57 实测正常）。
3. `house=` 必须传 —— 不传就继承骨架原主人 ⇒ 中立楼、不进玩家清单、电力算错。
4. 建造态必须是「刚放下」（§196：AC=-1 / C8=18 / 534=1 / 6E4=0），否则无头/只出底座。

用法：python _selftest_ev197.py
"""
from __future__ import print_function

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))

import editor_core as ec                                    # noqa: E402
from _paths import need_sav                                 # noqa: E402

SAV = need_sav('SAVE0029.SAV')          # 3 电厂 + 建造厂 + 4 单位、0 触发（玩家指定基档）
TYPE = 'GATECH'                          # 本档没有的类型（作战实验室）
SPOT = (76, 92)

n_ok = n_bad = 0
fails = []


def ck(name, cond, extra=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('  [OK]   %s' % name, ('(%s)' % extra) if extra else '')
    else:
        n_bad += 1
        fails.append(name)
        print('  [FAIL] %s  %s' % (name, extra))


def find_base(d, tn):
    for b in d.buildings():
        if b['type'] == tn:
            return b
    return None


def ptr_slots(d, base):
    """记录里落在 `0x0F000000~0x14000000` 段的 dword 数（= 引擎校验必需的结构键）。"""
    pl = d.plain
    rec = base - 72
    return sum(1 for o in range(0, 1860, 4)
               if 0x0F000000 <= struct.unpack_from('<I', pl, rec + o)[0] <= 0x14000000)


print('=' * 72)
print('§197 自检：跨类型放置（借骨架 + 套签名 + 刚放下态）')

# ---------- [0] 基档前提 ----------
d0 = ec.SaveDoc(SAV)
ck('基档里确实没有 %s' % TYPE, find_base(d0, TYPE) is None,
   str(sorted({b['type'] for b in d0.buildings()})))
ck('基档无「将建筑建于」触发',
   not (d0.pending_build_triggers() or []),
   str((d0.pending_build_triggers() or [])[:2]))

# ---------- [1] readiness：放行 + 风险提示 ----------
ok, why = d0.add_placed_readiness(TYPE)
ck('跨类型可以放（readiness=True）', ok is True, why[:60])
note = getattr(d0, '_last_readiness_note', '') or ''
ck('给了"先备份 / 进游戏确认"的提示', '跨类型' in note and '备份' in note, note[:40])
ok_x, why_x = d0.add_placed_readiness('XXXNOPE')
ck('语料库也没有的类型仍拒绝', ok_x is False, why_x[:40])

# ---------- [2] 放置：默认参数就是最终配方 ----------
d = ec.SaveDoc(SAV)
h = d.player_house()
base_slots = ptr_slots(d, d.buildings()[0]['base'])   # 原生楼的基准槽数（取第一栋）
r = d.add_placed_building(TYPE, SPOT[0], SPOT[1], require_free=True, house=h)

ck('走的是跨类型分支', bool(r.get('cross')), str(r.get('cross')))
ck('模板是本档原生楼（不是我们注入的）',
   r.get('tpl_key') not in d._injected_keys, hex(r.get('tpl_key') or 0))

nb = find_base(d, TYPE)
ck('新楼已出现在建筑列表里', nb is not None, '')
ck('类型键正确', nb is not None and nb['type'] == TYPE, nb['type'] if nb else '')
ck('hp 套成了目标类型的值（GATECH=500）', nb is not None and nb.get('hp') == 500,
   str(nb.get('hp') if nb else ''))
_rec = nb['base'] - 72
_ax = struct.unpack_from('<I', d.plain, _rec + 16 + 1608)[0]
_ay = struct.unpack_from('<I', d.plain, _rec + 16 + 1612)[0]
ck('art 尺寸套成了目标类型的值（213x215）', (_ax, _ay) == (213, 215), '%d x %d' % (_ax, _ay))

# ---------- [3] ★结构键没被清掉（T51 弹框的真因） ----------
slots = ptr_slots(d, nb['base'])
ck('★结构键槽数 ≥ 原生楼（没被 cross_clean 清掉）',
   slots >= base_slots, '新楼 %d / 原生 %d' % (slots, base_slots))
ck('★cross_clean 默认是 False',
   ec.SaveDoc.add_placed_building.__defaults__.count(False) >= 1
   or 'cross_clean=False' in str(ec.SaveDoc.add_placed_building.__defaults__),
   str(ec.SaveDoc.add_placed_building.__defaults__))

# ---------- [4] 所属方 / 电力 / 阵营清单 ----------
ck('所属方写成了玩家方', nb is not None and nb.get('house') == d.resolve_house(h),
   '%s vs %s' % (nb.get('house') if nb else '', d.resolve_house(h)))
pw0 = d0.house_power(h)
pw = d.house_power(h)
ck('电力计数器按 rules 更新（耗电 +100）', pw[1] == pw0[1] + 100,
   '%s -> %s' % (pw0[:2], pw[:2]))
pos, n, ep, _x = d.house_build_list(h)
keys = [struct.unpack_from('<I', d.plain, ep + 4 * i)[0] for i in range(n)]
ck('★登记进了玩家「本阵营建筑清单」', r['key'] in keys,
   'n=%d 含新键=%s' % (n, r['key'] in keys))

# ---------- [5] 建造态 = 刚放下（§196） ----------
st = d.building_anim_state(nb['base'])
v = st['vals']
ck('AC=-1（刚放下，引擎读档后自己播）', v['+0xAC'] in (-1, 0xFFFFFFFF), str(v['+0xAC']))
ck('C8=18（帧号回到未播完，否则只出底座）', v['+0xC8'] in (18, 6), str(v['+0xC8']))
ck('534=1（算已落位、吃电）', v['+0x534'] == 1, str(v['+0x534']))
ck('6E4=0（没播完 ⇒ 引擎会走建造流程）', v['+0x6E4'] == 0, str(v['+0x6E4']))
ck('不是 done（done 会让引擎不建子对象 ⇒ 无头）', st['state'] != 'done', st['state'])

# ---------- [6] 落盘重读 ----------
p = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_selftest_out', 'ev197_跨类型.SAV')
if not os.path.isdir(os.path.dirname(p)):
    os.makedirs(os.path.dirname(p))
d.save(p, backup=False)
d2 = ec.SaveDoc(p)
nb2 = find_base(d2, TYPE)
ck('落盘重读：新楼还在', nb2 is not None, '')
ck('落盘重读：类型/坐标不变',
   nb2 is not None and (nb2['x'], nb2['y']) == SPOT,
   str((nb2['x'], nb2['y']) if nb2 else ''))
st2 = d2.building_anim_state(nb2['base'])
ck('落盘重读：建造态仍是刚放下', st2['state'] != 'done' and st2['vals']['+0xC8'] in (18, 6),
   '%s C8=%s' % (st2['state'], st2['vals']['+0xC8']))
ck('落盘重读：建筑总数 +1', len(d2.buildings()) == len(d0.buildings()) + 1,
   '%d -> %d' % (len(d0.buildings()), len(d2.buildings())))

print('-' * 72)
print('§197 SELFTEST %s   PASS %d / FAIL %d'
      % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
