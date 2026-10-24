# -*- coding: utf-8 -*-
"""§251 自检：**落点必须是平地**（层高与八邻一致）—— 斜坡上放单位，打掉必弹框。

玩家 T105 A/B 实测（2026-10-24）：
  同一档、同一套克隆配方，**唯一差别是落点**：
    (104,212) tile 1265 层高 7，八邻 8/0/0/4 = 悬崖边 ⇒ **打掉弹框 + 单位"有目标"往外跑**
    (108,206) 层高 1 ⇒ 正常
    (109,206) 层高 0 ⇒ 正常
  反证：玩家验收通过的 T97/T99 落点都是**八邻全 0 的平地**。

⚠ 这个坑藏了 6 轮，因为 `free_cell_near(walkable)` 只判"可通行"，
  而斜坡 tile 是可以通行的 ⇒ 落点校验一路绿灯。
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from _paths import find_sav          # noqa: E402
import editor_core as ec             # noqa: E402

OK = [0]
BAD = [0]


def ck(cond, msg, extra=''):
    if cond:
        OK[0] += 1
        print('  [PASS] %s' % msg)
    else:
        BAD[0] += 1
        print('  [FAIL] %s   %s' % (msg, extra))


print('=== §251 落点平地校验 ===')
d = ec.SaveDoc(find_sav('T85_老工程师_修位置副本槽.SAV'))

# ① 已知的坑：那个悬崖格必须被判成"非平地"
i = d.cell_flat_info(104, 212)
print('  (104,212)：平地=%s 层高=%s tile=%s  %s' % (i['is_flat'], i['level'], i['tile'], i['why']))
ck('① (104,212) 被判成非平地（tile 1265 斜坡）', i['is_flat'] is False,
   'is_flat=%s' % i['is_flat'])
ck('①b 理由里点出了层高不一致', '层高' in (i['why'] or ''), i['why'])

# ② 平地必须被判成平地
flat = d.flat_cell_near(104, 212)
ck('② 能找到一个替补平地', flat is not None, flat)
fk = d.cell_flat_info(*flat) if flat else {}
ck('②b 替补格确实是平地', bool(fk.get('is_flat')), fk.get('why'))
print('  替补平地 = %s（层高 %s）' % (flat, fk.get('level')))

# ③ 默认行为：在斜坡上放置会被**自动挪到平地**，并报告 cell_nudge
info = d.add_unit_by_clone('DISK', 104, 212, house=d.player_house(), veterancy=0)
rec = [r for r in d.unit_records() if r['key'] == info['key']][0]
ck('③ 斜坡落点被自动挪走', bool(info.get('cell_nudge')),
   info.get('cell_nudge'))
ck('③b 挪到的地方是平地', d.cell_flat_info(rec['x'], rec['y'])['is_flat'],
   (rec['x'], rec['y'], d.cell_level(rec['x'], rec['y'])))
ck('③c 实际坐标就是挪后的坐标',
   (rec['x'], rec['y']) == (info['cell_nudge'] or {}).get('to'),
   ((rec['x'], rec['y']), info.get('cell_nudge')))

# ④ exact_cell=True 时原样落位（测试/特定用途需要）
d2 = ec.SaveDoc(find_sav('T85_老工程师_修位置副本槽.SAV'))
info2 = d2.add_unit_by_clone('DISK', 104, 212, house=d2.player_house(),
                             veterancy=0, exact_cell=True)
rec2 = [r for r in d2.unit_records() if r['key'] == info2['key']][0]
ck('④ exact_cell=True 时原样落在 (104,212)', (rec2['x'], rec2['y']) == (104, 212),
   (rec2['x'], rec2['y']))
ck('④b 此时不给 cell_nudge', not info2.get('cell_nudge'), info2.get('cell_nudge'))

# ⑤ import_unit_from 也要走同一套（斜坡同样被挪）
d3 = ec.SaveDoc(find_sav('T85_老工程师_修位置副本槽.SAV'))
ref = ec.SaveDoc(find_sav('SAVE4CA2.SAV'))
info3 = d3.import_unit_from(ref, 'DISK', 104, 212, house=d3.player_house(), slots='safe')
ck('⑤ 直接写入法也会被挪到平地', bool(info3.get('cell_nudge')), info3.get('cell_nudge'))

print('\n§251 自检：PASS %d / FAIL %d' % (OK[0], BAD[0]))
sys.exit(1 if BAD[0] else 0)
