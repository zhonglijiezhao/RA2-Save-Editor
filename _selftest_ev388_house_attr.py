# -*- coding: utf-8 -*-
"""自检 ev388：地编「所属方」窗口的四个属性（IQ / 科技等级 / 起始资金 / 玩家控制）

2026-11-17 定案，落点 = House 对象 GUID 位置 +0x1E4 / +0x1E8 / +0x1F0 / +0x201。
素材：`E:\\YURI\\SAVE01EB.SAV`（对应 `all01umd.map`，地图里 Americans 5/8/100/yes、
Alliance 1/2/0/no）。缺素材时 SKIP。
"""
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, '_pylibs'))
from core.editor_core import SaveDoc    # noqa: E402

SRC = r'E:\YURI\SAVE01EB.SAV'
if not os.path.exists(SRC):
    print('SKIP：缺素材档 %s' % SRC)
    sys.exit(0)

ok = fail = 0


def chk(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print('  x %s' % msg)


d = SaveDoc(SRC)
print('素材：%s' % SRC)

print('\n== 1) 与地图 [X House] 逐项核对（12 阵营）==')
# all01umd.map 里的值
MAP = {                       # 短名: (IQ, TechLevel, Credits×100, PlayerControl)
    'Americans': (5, 8, 10000, True), 'Alliance': (1, 2, 0, False),
    'French': (5, 10, 0, False), 'Germans': (5, 10, 0, False),
    'British': (5, 10, 0, False), 'Africans': (5, 10, 0, False),
    'Arabs': (5, 10, 0, False), 'Confederation': (5, 10, 0, False),
    'Russians': (5, 10, 0, False), 'YuriCountry': (5, 10, 0, False),
    'Neutral': (5, 10, 0, False), 'Special': (5, 10, 0, False),
}
rows = []
for nm, want in MAP.items():
    try:
        a = d.house_attr(nm)
    except Exception as e:
        chk(False, '%s 读不出来：%s' % (nm, e))
        continue
    rows.append(nm)
    chk((a['iq'], a['tech'], a['credits'], a['player_ctrl']) == want,
        '%s 应为 %s，实际 (%d, %d, %d, %s)'
        % (nm, want, a['iq'], a['tech'], a['credits'], a['player_ctrl']))
print('  核对了 %d 个阵营' % len(rows))

print('\n== 2) 写 + 回读 ==')
before = d.house_attr('Alliance')
d.set_house_attr('Alliance', iq=5, tech=9, credits=12345, player_ctrl=True)
after = d.house_attr('Alliance')
chk(after['iq'] == 5 and after['tech'] == 9 and after['credits'] == 12345
    and after['player_ctrl'] is True,
    '四项写入后回读一致（实际 %d/%d/%d/%s）'
    % (after['iq'], after['tech'], after['credits'], after['player_ctrl']))
chk(after['cur_player'] is True, '+0x200（当前玩家）与 +0x201 一起写')
# 只改一项不能碰别的
d.set_house_attr('Alliance', iq=2)
only = d.house_attr('Alliance')
chk(only['iq'] == 2 and only['tech'] == 9 and only['credits'] == 12345,
    '只传 iq 时其它三项不动（实际 %d/%d/%d）'
    % (only['iq'], only['tech'], only['credits']))

print('\n== 3) 范围校验 ==')
for bad, kw in ((6, {'iq': 6}), (-1, {'iq': -1}), (11, {'tech': 11}),
                (2147483648, {'credits': 2147483648}), (-1, {'credits': -1})):
    try:
        d.set_house_attr('Americans', **kw)
        chk(False, '应拒绝 %s' % kw)
    except Exception:
        chk(True, '')
# 边界值要放行
try:
    d.set_house_attr('Americans', iq=0, tech=0, credits=2147483647)
    a = d.house_attr('Americans')
    chk(a['iq'] == 0 and a['tech'] == 0 and a['credits'] == 2147483647,
        '边界值 0/0/2147483647 能写入')
except Exception as e:
    chk(False, '边界值被误拒：%s' % e)

print('\n== 4) 存盘回读一致 ==')
tmp = os.path.join(tempfile.gettempdir(), '_ev388_house.SAV')
d.save(tmp, backup=False)
d2 = SaveDoc(tmp)
a2 = d2.house_attr('Alliance')
chk((a2['iq'], a2['tech'], a2['credits'], a2['player_ctrl']) == (2, 9, 12345, True),
    '另存后回读一致（实际 %d/%d/%d/%s）'
    % (a2['iq'], a2['tech'], a2['credits'], a2['player_ctrl']))
try:
    os.remove(tmp)
except Exception:
    pass

print('\n== 5) 定位稳健性 ==')
o = d.house_obj_off('Americans')
chk(o is not None and struct.unpack_from('<I', d.plain, o + 16)[0]
    == d.house_keys()['Americans'], 'house_obj_off 的键与 house_keys 一致')
chk(d.house_obj_off('不存在的阵营') is None, '认不出的阵营返回 None')
chk(all(d.house_obj_off(n) is not None for n in MAP), '12 个阵营全部能定位')

print('\n结果：绿 %d / 红 %d' % (ok, fail))
sys.exit(0 if fail == 0 else 1)
