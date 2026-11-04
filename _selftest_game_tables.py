# -*- coding: utf-8 -*-
"""§SOV20 游戏侧列表表（rulesmd.ini 的 [Animations]/[Particles]/[ParticleSystems]）。

玩家 2026-11-04：
> 41播放动画在  列表是在rulesmd中的
> 注意存档是不同任务的存档  用的规则是不同的 以游戏下的规则为准

⇒ 两条要求：
  1. 参数（编号）要按 rulesmd.ini 的表翻成名字 / 给下拉；
  2. 表必须**按当前存档所在游戏目录**取，不能写死（不同任务/不同 MOD 的 rules 不同）。

跑：python _selftest_game_tables.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from game_tables import (lists_for_dir, find_rules, load_rules,   # noqa: E402
                         label_of, choices_of, PTYPE_TABLES)
from editor_core import SaveDoc                                   # noqa: E402

OK = FAIL = 0


def chk(cond, msg, got=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s%s' % (msg, ('　→ 实际 %r' % (got,)) if got is not None else ''))


GAME = r'E:\YURI'
OTHER = r'E:\红色警戒2杂交版v0.3.0'

print('== A. YURI 目录（当前任务）==')
tb = lists_for_dir(GAME)
chk(tb is not None, '找到 rulesmd.ini', None if tb is None else tb['path'])
if tb:
    print('     表：animations=%d voxel=%d particles=%d systems=%d' % (
        len(tb['animations']), len(tb['voxel_anims']),
        len(tb['particles']), len(tb['particle_systems'])))
    chk(len(tb['animations']) > 300, '[Animations] 有 %d 项' % len(tb['animations']))
    chk(len(tb['particle_systems']) == 13, '[ParticleSystems] 13 项',
        len(tb['particle_systems']))
    chk(tb['animations'].get(25) == 'PARACH', '25 → PARACH', tb['animations'].get(25))
    chk(tb['particle_systems'].get(3) == 'BigGreySmokeSys', '粒子系统 3 → BigGreySmokeSys',
        tb['particle_systems'].get(3))
    chk(256 not in tb['animations'],
        '★256 号在这个 mod 的 [Animations] 里不存在（不是工具读错）',
        tb['animations'].get(256))
    chk(label_of(tb['animations'], 25) == '25 PARACH', 'label_of 25 = "25 PARACH"',
        label_of(tb['animations'], 25))
    chk('表里没有' in label_of(tb['animations'], 256), 'label_of 256 明确说“表里没有”',
        label_of(tb['animations'], 256))
    v = choices_of(tb['animations'])
    chk(len(v) == len(tb['animations']) and v[0].split()[0].isdigit(),
        '下拉候选 %d 条、以编号开头' % len(v), v[:2])

print('\n== B. 换一个 MOD 目录 ⇒ 表必须跟着变（玩家：以游戏下的规则为准）==')
if os.path.isfile(os.path.join(OTHER, 'rulesmd.ini')):
    tb2 = lists_for_dir(OTHER)
    chk(tb2 is not None and tb2['path'].lower().startswith(OTHER.lower()),
        '别处的存档取到的是它自己的 rulesmd.ini',
        None if tb2 is None else tb2['path'])
    if tb2:
        print('     该 MOD：animations=%d systems=%d' % (
            len(tb2['animations']), len(tb2['particle_systems'])))
        chk(tb2['path'] != tb['path'], '两份表的路径不同（没有复用 YURI 的）', tb2['path'])
    # SaveDoc.game_lists 走存档目录
    d_other = SaveDoc(os.path.join(OTHER, 'SOV01UMD.SAV')) if os.path.exists(
        os.path.join(OTHER, 'SOV01UMD.SAV')) else None
    if d_other is None:
        print('     （该目录没有存档样本，跳过 SaveDoc.game_lists 交叉验证）')
else:
    print('  [SKIP] 没找到第二份 rulesmd.ini')

print('\n== C. 找不到 rules 的目录 ⇒ 返回 None（不乱翻）==')
import tempfile
tmpd = tempfile.mkdtemp(prefix='_sov20_')
chk(find_rules(tmpd) is None, '空目录里没有 rulesmd.ini')
chk(lists_for_dir(tmpd) is None, 'lists_for_dir(空目录) = None')
try:
    os.rmdir(tmpd)
except Exception:
    pass

print('\n== D. SaveDoc._arg_text 接上表（ptype 25/26）==')
d = SaveDoc(r'E:\YURI\SOV01UMD_修改_2.SAV')
tbx = d.game_lists()
chk(tbx is not None and tbx['path'].lower().startswith(GAME.lower()),
    '存档 → 取到同目录的 rulesmd.ini', None if tbx is None else tbx['path'])
chk(d._arg_text(25, 25) == '25 PARACH', 'ptype25 / 25 → "25 PARACH"', d._arg_text(25, 25))
chk('表里没有' in d._arg_text(25, 256), 'ptype25 / 256 → 明确提示表里没有',
    d._arg_text(25, 256))
chk(d._arg_text(26, 3) == '3 BigGreySmokeSys', 'ptype26 / 3 → 粒子系统名',
    d._arg_text(26, 3))
chk(d._arg_text(0, 0) == '', 'ptype0 不受影响（仍返回空文案）', d._arg_text(0, 0))

print('\n== E. ptype → 表的映射 ==')
chk(PTYPE_TABLES.get(25) == ('animations', '动画（rulesmd [Animations]）'),
    'ptype25 → animations 表')
chk(PTYPE_TABLES.get(26) == ('particle_systems', '粒子系统（rulesmd [ParticleSystems]）'),
    'ptype26 → particle_systems 表')

print('\n§SOV20：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
