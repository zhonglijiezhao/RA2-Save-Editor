# -*- coding: utf-8 -*-
"""§194 自检：「半完成」状态（`+0x534=0` 且 `+0x6E4=1`）的识别与修复。

来源（2026-10-23 玩家三档实测）：
  T18 基线（6E4 = 0/0/1）→ 1 座无头；T19_A（0/1/1）→ 2 座；T19_C（1/1/1）→ 3 座 + 提示无电；
  把 6E4 全改回 0（T21）⇒ **三座全都有头、正常旋转**。
⇒ 这不是"动画播完标记"这么简单：`534=0` 时置 `6E4=1` 会让引擎停在半完成帧（无头），
  而且开始按"已建成"算电力。

本自检锁死：
  [1] T19_C 里三座塔必须一律判成 `half`（此前被错判成 `buildup`）
  [2] `repair_building_anim()` 对 half 的修法是**只写 1 个字节 `+0x6E4=0`**
  [3] 修完变 `buildup`（= 进游戏播一遍动画），且明文长度不变
  [4] `pending` 字段要把 half 算进去（GUI 扫描靠它）
  [5] 落盘重读后仍是 buildup
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
import editor_core as ec
from _paths import need_sav, out

SAV = need_sav('T19_C_三座塔全置6E4_1.SAV')

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


d = ec.SaveDoc(SAV)
towers = [b for b in d.buildings() if b['type'] == 'ATESLA']
print('=== 样本：%s（%d 座光棱塔）===' % (os.path.basename(SAV), len(towers)))

print('=== [1] 状态判定 ===')
states = [d.building_anim_state(b['base']) for b in towers]
ck('三座塔全部判成 half（半完成）', all(s['state'] == 'half' for s in states),
   str([s['state'] for s in states]))
ck('half 被视为"需要修"（pending=True）', all(s['pending'] for s in states),
   str([s['pending'] for s in states]))
ck('half 的说明文字提示"无头/残缺"', all('无头' in s['text'] or '残缺' in s['text']
                                        for s in states))

print('=== [2][3] 修复：只写 1 个字节 ===')
n0 = len(d.plain)
before = bytes(d.plain)
target = towers[0]['base']
r = d.repair_building_anim(target)
st2 = d.building_anim_state(target)
ck('修复返回 changed=True', r['changed'])
ck('修完变成 buildup（进游戏会播一遍动画）', st2['state'] == 'buildup', st2['state'])
ck('+0x6E4 已为 0', st2['vals']['+0x6E4'] == 0, str(st2['vals']['+0x6E4']))
ck('明文长度不变', len(d.plain) == n0)
after = bytes(d.plain)
diff = [i for i in range(len(after)) if after[i] != before[i]]
ck('只有 1 个字节被改（6E4 是 u8）', len(diff) == 1, '改了 %d 字节' % len(diff))

print('=== [4][5] 全部修一遍 + 落盘重读 ===')
for b in towers:
    d.repair_building_anim(b['base'])
ck('三座都变回 buildup',
   all(d.building_anim_state(b['base'])['state'] == 'buildup' for b in towers),
   str([d.building_anim_state(b['base'])['state'] for b in towers]))
ck('三座的 6E4 都是 0',
   all(d.building_anim_state(b['base'])['vals']['+0x6E4'] == 0 for b in towers))
p = out('ev194_半完成已修.SAV')
d.save(p, backup=False)
d2 = ec.SaveDoc(p)
t2 = [b for b in d2.buildings() if b['type'] == 'ATESLA']
ck('落盘重读：仍是 buildup（未回退成 half）',
   all(d2.building_anim_state(b['base'])['state'] == 'buildup' for b in t2),
   str([d2.building_anim_state(b['base'])['state'] for b in t2]))
ck('落盘重读：6E4 仍为 0',
   all(d2.building_anim_state(b['base'])['vals']['+0x6E4'] == 0 for b in t2))
print('  产物：%s' % p)

print('\n§194 自检：PASS %d / FAIL %d' % (n_ok, n_bad))
if n_bad:
    print('失败项：' + ', '.join(fails))
sys.exit(1 if n_bad else 0)
