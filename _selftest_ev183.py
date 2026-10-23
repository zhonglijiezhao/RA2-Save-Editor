# -*- coding: utf-8 -*-
"""§188 自检：直接落成**默认播一遍建造动画**，开关是配方里的 `+0x6E4=0`。

玩家 T16 实测（一档两栋、只差 6E4）：
  · A (70,90) `6E4=0` → **播一遍建造动画**，播完**模型完整**
  · B (77,90) `6E4=1` → 不播，且**显示不全**
⇒ 结论：`6E4` 既管"播不播"，也管"显示全不全"（播的过程会把模型/子对象重建一遍）。
   §186 那条"直接落成播不出动画"**作废** —— 当时继承了模板的 `6E4=1`。

本自检锁死：
  [1] 默认（不传 buildup）⇒ `buildup=True`、六元组 18/1/1/2/0/**0**、state=buildup
  [2] `buildup=False` ⇒ 完成态（逃生口：立即显示成品、无动画）
  [3] GUI：放置传 `buildup=True`、赠送页默认勾上、文案承诺"播一遍建造动画"
"""
import io
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.editor_core import SaveDoc
from _paths import need_sav

BASE = need_sav('T11_工具配方验收.SAV')
TYPE = 'ATESLA'
KEYS = ['+0xAC', '+0xBC', '+0xD0', '+0x10C', '+0x534', '+0x6E4']

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


def free_cell(doc, start=(20, 20)):
    lim = doc.coord_limit()
    for y in range(start[1], min(lim[1], start[1] + 120)):
        for x in range(start[0], min(lim[0], start[0] + 120)):
            if not doc.cell_taken(x, y):
                return x, y
    return None


def place(**kw):
    d = SaveDoc(BASE)
    c = free_cell(d)
    info = d.add_placed_building(TYPE, c[0], c[1], comp_mode='zero', **kw)
    g = None
    for x in d._bld_group()[0]:
        if struct.unpack_from('<I', d.plain, x + 16)[0] == info['key']:
            g = x
            break
    st = d.building_anim_state(g + 72)
    anims = [struct.unpack_from('<I', d.plain, g + 16 + 1388 + 4 * i)[0] for i in range(4)]
    return info, st, anims


print('=' * 72)
print('§188 自检：直接落成默认播一遍建造动画（开关 = 6E4=0）')

# ---------- [1] 默认：播一遍 ----------
# ★2026-10-23 §196：默认值已从 `buildup=True` 改成 `buildup='fresh'`（刚放下态）。
#   [1] 保留对**老配方**（显式 buildup=True）的验证，[1b] 新增对**新默认**的验证。
i1, s1, a1 = place(buildup=True)
v1 = s1['vals']
print('  [1] buildup=True（老配方）→ state=%s 六元组=%s'
      % (s1['state'], '/'.join('%d' % v1[k] for k in KEYS)))
ck('显式 buildup=True ⇒ 六元组 18/1/1/2/0/0',
   [v1[k] for k in KEYS] == [18, 1, 1, 2, 0, 0], str([v1[k] for k in KEYS]))
ck('显式 buildup=True ⇒ state = buildup', s1['state'] == 'buildup', s1['state'])
ck('不播两遍（replay=False）', s1['replay'] is False, str(s1['replay']))

i1b, s1b, _a1b = place()
v1b = s1b['vals']
print('  [1b] 默认（fresh）→ state=%s 六元组=%s'
      % (s1b['state'], '/'.join('%d' % v1b[k] for k in KEYS)))
ck('默认 buildup 不是 False（默认写刚放下态）', i1b.get('buildup') is not False,
   repr(i1b.get('buildup')))
ck('默认 ⇒ AC=-1（刚放下，引擎读档后自己播）',
   v1b['+0xAC'] in (-1, 0xFFFFFFFF), str(v1b['+0xAC']))
ck('默认 ⇒ 534=1（算已落位、吃电）', v1b['+0x534'] == 1, str(v1b['+0x534']))
ck('默认 ⇒ 6E4=0（没播完 ⇒ 引擎会走建造流程建炮塔）', v1b['+0x6E4'] == 0,
   str(v1b['+0x6E4']))
ck('默认 ⇒ state 不是 done（done 会无头）', s1b['state'] != 'done', s1b['state'])
ck('Anims[3..6] 全 0 —— 播不播不靠伴随对象，靠 6E4', all(a == 0 for a in a1), str(a1))

# ---------- [2] 逃生口 ----------
i2, s2, a2 = place(buildup=False)
print('  [2] buildup=False → state=%s 六元组=%s'
      % (s2['state'], '/'.join('%d' % s2['vals'][k] for k in KEYS)))
ck('buildup=False 时 state = done（立即成品、无动画）', s2['state'] == 'done', s2['state'])

# ---------- [3] GUI 口径 ----------
src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'RA2存档编辑器.py'), encoding='utf-8').read()
ck('GUI 放置时传 buildup=\'fresh\'', "buildup='fresh'" in src)
ck('GUI 赠送页默认勾上带建造动画',
   'self.gift_buildup = tk.BooleanVar(value=True)' in src)
ck('GUI 文案承诺"播一遍建造动画"', '播一遍建造动画' in src)

print('-' * 72)
print('§188 SELFTEST %s   PASS %d / FAIL %d'
      % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
