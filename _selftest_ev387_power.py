# -*- coding: utf-8 -*-
"""自检 ev387：建筑「耗能/工作」（地编 POWERED_ON）= BuildingClass::HasPower

2026-11-17 定案：base 帧 +1580（HasPower）/ +1684（WasOnline）/ +1718（StuffEnabled）
三处一起写才生效（只写一处会被引擎按实时电力重算覆盖）。

素材：玩家 2026-11-17 做的对照档 `E:\\YURI\\SAVE41BB.SAV`
      两座 GAPILE，地图里 `[Structures]` 一座 POWERED_ON=0、一座 =1，游戏自己存的档。
      缺素材时 SKIP（不 FAIL）—— 实验档随时会被覆盖。
"""
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, '_pylibs'))
from core.editor_core import SaveDoc    # noqa: E402

SRC = r'E:\YURI\SAVE41BB.SAV'

if not os.path.exists(SRC):
    print('SKIP：缺素材档 %s（玩家临时做的对照档，不在也不影响其它自检）' % SRC)
    sys.exit(0)

ok = fail = 0


def chk(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print('  x %s' % msg)


print('素材：%s' % SRC)
d = SaveDoc(SRC)
bs = d.buildings()
chk(len(bs) >= 2, '素材里至少 2 栋建筑（实际 %d）' % len(bs))
if len(bs) < 2:
    print('\n结果：绿 %d / 红 %d' % (ok, fail))
    sys.exit(1)

print('\n== 1) 读：地图 0/1 那座在存档里就是 0/1 ==')
states = {}
for b in bs:
    r = d.building_power(b['base'])
    states[(b['x'], b['y'])] = r
    print('  %-8s (%3d,%3d)  on=%s  vals=%s  mixed=%s  Power=%s'
          % (b['type'], b['x'], b['y'], r['on'],
             [r['vals'][o] for o in d.BLD_POWER_OFFS], r['mixed'], r['power']))
vals = list(states.values())
chk(len(set(r['on'] for r in vals)) == 2, '两栋的 on 一个 True 一个 False（对照档）')
chk(all(not r['mixed'] for r in vals), '每栋三字节一致（不是中间态）')
chk(all((r['power'] or 0) < 0 for r in vals), 'GAPILE 是耗电建筑（Power<0），这个开关适用')

off_b = [b for b in bs if not d.building_power(b['base'])['on']][0]
on_b = [b for b in bs if d.building_power(b['base'])['on']][0]

print('\n== 2) 写：三字节必须同时变 ==')
r = d.set_building_power(False, base=on_b['base'])
now = d.building_power(on_b['base'])
chk(now['on'] is False, '把通电那栋写成断电后 on=False')
chk([now['vals'][o] for o in d.BLD_POWER_OFFS] == [0, 0, 0],
    '三字节都是 0（实际 %s）' % [now['vals'][o] for o in d.BLD_POWER_OFFS])
chk(not now['mixed'], '写完后不是中间态')

r = d.set_building_power(True, base=off_b['base'])
now2 = d.building_power(off_b['base'])
chk(now2['on'] is True, '把断电那栋写成通电后 on=True')
chk([now2['vals'][o] for o in d.BLD_POWER_OFFS] == [1, 1, 1],
    '三字节都是 1（实际 %s）' % [now2['vals'][o] for o in d.BLD_POWER_OFFS])

print('\n== 3) 存盘回读一致 ==')
tmp = os.path.join(tempfile.gettempdir(), '_ev387_power.SAV')
d.save(tmp, backup=False)
d2 = SaveDoc(tmp)
for key, want in (((on_b['x'], on_b['y']), False), ((off_b['x'], off_b['y']), True)):
    hit = [b for b in d2.buildings() if (b['x'], b['y']) == key]
    chk(len(hit) == 1, '回读能找到 (%d,%d)' % key)
    if hit:
        rr = d2.building_power(hit[0]['base'])
        chk(rr['on'] is want, '回读 (%d,%d) on=%s' % (key[0], key[1], want))
try:
    os.remove(tmp)
except Exception:
    pass

print('\n== 4) 防御：非建筑 / 越界必须报错 ==')
bad = 0
for base in (0, 12, len(d.plain) - 10):
    try:
        d.building_power(base)
    except Exception:
        bad += 1
chk(bad == 3, '非建筑 base 全部报错（%d/3）' % bad)
try:
    d.set_building_power(False, base=0)
    chk(False, 'set_building_power(base=0) 应该报错')
except Exception:
    chk(True, '')

print('\n== 5) 发电建筑会被标出来（电厂档存在时） ==')
GEN = r'E:\YURI\SAVE5AF1.SAV'
if os.path.exists(GEN):
    g = SaveDoc(GEN)
    for b in g.buildings():
        rr = g.building_power(b['base'])
        if b['type'].upper().endswith('POWR'):
            chk((rr['power'] or 0) > 0, '%s 的 Power>0（会被 UI 标成"发电建筑，不适用"）'
                % b['type'])
else:
    print('  （未找到 %s，跳过）' % GEN)

print('\n结果：绿 %d / 红 %d' % (ok, fail))
sys.exit(0 if fail == 0 else 1)
