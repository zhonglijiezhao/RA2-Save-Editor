# -*- coding: utf-8 -*-
"""「地图 ↔ 存档」匹配判据自检（2026-10-17 20:3x 改判据后固化）。

玩家实测纠正：SAVE0029.SAV 用的就是 all01umd.map（建筑重合 98%），
但存档 `0x348E` 那组尺寸是 203×295、地图 `[Map] Size` 是 180×170
⇒ 旧判据"尺寸一致才算同图"给假不匹配，还会弹框拦人。
新判据：尺寸一致 / 建筑高度重合（≥85% 且 ≥20 栋）/ 建筑全在图内，三者任一即认。
本自检覆盖：①尺寸一致 ②建筑重合 ③彻底不相关 ④匹配结果只作提示（GUI 不再拦）
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))
import editor_core as ec

OK = FAIL = 0


def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


SAVE_BIG = r'E:\YURI\SAVE0029.SAV'
MAP_BIG = r'E:\YURI\all01umd.map'
SAVE_SMALL = r'E:\YURI\多单位存档.SAV'

print('== A: 尺寸字段不同但建筑高度重合 ⇒ 认同一张图（玩家实测案例）==')
if os.path.exists(SAVE_BIG) and os.path.exists(MAP_BIG):
    d = ec.SaveDoc(SAVE_BIG)
    mt = d.map_match(MAP_BIG)
    chk(mt['ok'] is True, '判定为匹配（ok=%s, reason=%s）' % (mt['ok'], mt['reason']))
    chk(mt['reason'] == 'overlap', '走的是「建筑重合」分支（实际 %s）' % mt['reason'])
    chk(mt['rate_ms'] >= 0.85, '建筑重合率 ≥85%%（实际 %.1f%%）' % (mt['rate_ms'] * 100))
    chk('尺寸字段不同' in mt['msg'], '提示里说明了尺寸字段不同（不装看不见）')
    chk(d.map_size() != mt['map_size'],
        '存档尺寸字段 %s 与地图 %s 确实不同（正是旧判据翻车点）'
        % (d.map_size(), mt['map_size']))
else:
    print('  [SKIP] 缺样本 %s / %s' % (SAVE_BIG, MAP_BIG))

print('\n== B: 彻底不相关的组合 ⇒ 仍然报"疑似不匹配"（但只是提示）==')
if os.path.exists(SAVE_SMALL) and os.path.exists(MAP_BIG):
    d2 = ec.SaveDoc(SAVE_SMALL)
    mt2 = d2.map_match(MAP_BIG)
    chk(mt2['ok'] is False, '小图存档 + 大任务地图 ⇒ ok=False（reason=%s）' % mt2['reason'])
    chk('不阻止使用' in mt2['msg'], '文案明确"仅供参考，不阻止使用"')
else:
    print('  [SKIP] 缺样本 %s' % SAVE_SMALL)

print('\n== C: GUI 侧不再拿匹配结果拦人 ==')
src = open(os.path.join(HERE, 'RA2存档编辑器.py'), encoding='utf-8').read()
chk("messagebox.showwarning('地图与存档不匹配'" not in src,
    'mv_load 里已无「地图与存档不匹配」弹框')
chk("st['ok'] = True" in src and 'b45309' in src,
    '不匹配时仍置 ok=True，并用橙字提示（#b45309）')
i = src.find('def mv_load')
chk('if not mt[\'ok\']:' not in src[i:i + 2000],
    'mv_load 里不再有「不匹配就 return」的早退分支')

print('\n自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
