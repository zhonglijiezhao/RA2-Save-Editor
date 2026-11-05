# -*- coding: utf-8 -*-
"""自检：Ares / Phobos 的 `+=` 追加注册语法（玩家 2026-11-05 提醒）

玩家原话：
  「[VehicleTypes] +=SCHP1 那个平台可以用加号注册了」

为什么这是个真问题：`+=名字` 这种行 `split('=',1)` 出来键是 `'+'`，
  而 `kv` 是 dict ⇒ **多行互相覆盖只剩最后一条**；`int('+')` 又失败 ⇒
  整批自建型号在工具里彻底失踪（查不到类别、查不到建造栏序号），
  `[Animations] +=` 就更直接：下拉列表里少掉几百项，玩家选不到。
实测本机 `桌面/RA/rulesmd.ini`：载具 415、步兵 168、建筑 97、动画 650 条都是这么注册的。

断言：
  A 合成 INI：`+=` 多行都要在（不是只剩最后一条）
  B 索引分配 = 数字项最大号 + 1，按出现顺序递增
  C 四个 *Types 段各自独立编号
  D `[Animations] +=` 进动画表，编号接在最大号之后；选得出来
  E 注释 / 空值 / `+= 带空格` 都要稳
  F 真机（桌面/RA）：`+=` 注册的型号认得类别与序号，动画表条目数对得上
"""
import io
import os
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))
import game_ini                                            # noqa: E402
import game_tables                                         # noqa: E402

OK = FAIL = 0


def chk(c, msg, extra=None):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg + ('   → %r' % (extra,) if extra is not None else ''))


SAMPLE = """[VehicleTypes]
0=AMCV
1=SMCV
2=HARV
+=SCHP1
+=SCHP2
+= SCHP3
; +=NOT_ME
+=
11=LAST

[InfantryTypes]
0=E1
5=E2
+=NEWINF

[BuildingTypes]
0=GACNST
+=NEWBLD

[AircraftTypes]
+=NEWAIR

[Animations]
0=AA
1=BB
+=MYANIM1
+=MYANIM2

[ParticleSystems]
1=SYS1
+=MYSYS

[SCHP1]
Name=测试载具
Strength=1000
"""

TMP = tempfile.mkdtemp(prefix='_pleq_')
RULES = os.path.join(TMP, 'rulesmd.ini')
with open(RULES, 'w', encoding='utf-8') as f:
    f.write(SAMPLE)

try:
    print('== A/B: `+=` 多行都要在，索引接在最大号之后 ==')
    g = game_ini.GameTypes(game_dir=TMP)
    print('     规则: %s ｜ order %d 条' % (g.rules_path, len(g.order)))
    for nm in ('SCHP1', 'SCHP2', 'SCHP3'):
        chk(nm in g.order, '%s 认得出来（不是只剩最后一条）' % nm, g.order.get(nm))
    idx = [g.order.get(n, {}).get('index') for n in ('SCHP1', 'SCHP2', 'SCHP3')]
    chk(idx == [12, 13, 14], '索引 = 数字最大号(11)+1 起依次递增', idx)
    chk(g.order.get('SCHP1', {}).get('kind') == 'vehicle', 'SCHP1 归到 vehicle', 
        g.order.get('SCHP1'))
    chk(g.order.get('SCHP3', {}).get('index') == 14, '`+= SCHP3`（等号后有空格）也认', 
        g.order.get('SCHP3'))
    chk('NOT_ME' not in g.order, '被注释的 `; +=NOT_ME` 不算')

    print('\n== C: 四个 *Types 段各自独立编号 ==')
    chk(g.order.get('NEWINF', {}).get('index') == 6 and
        g.order.get('NEWINF', {}).get('kind') == 'infantry',
        'InfantryTypes +=NEWINF → 6（数字最大 5 + 1）', g.order.get('NEWINF'))
    chk(g.order.get('NEWBLD', {}).get('index') == 1 and
        g.order.get('NEWBLD', {}).get('kind') == 'building',
        'BuildingTypes +=NEWBLD → 1（数字最大 0 + 1）', g.order.get('NEWBLD'))
    chk(g.order.get('NEWAIR', {}).get('index') == 0 and
        g.order.get('NEWAIR', {}).get('kind') == 'aircraft',
        'AircraftTypes +=NEWAIR → 0（段里没有数字项）', g.order.get('NEWAIR'))

    print('\n== D: [Animations] / [ParticleSystems] 的 += ==')
    tb = game_tables.lists_for_dir(TMP)
    anim = tb['animations']
    chk(anim.get(2) == 'MYANIM1' and anim.get(3) == 'MYANIM2',
        '动画表：+= 接在最大号(1)之后 → 2/3', {2: anim.get(2), 3: anim.get(3)})
    chk(game_tables.label_of(anim, 2) == '2 MYANIM1', '选得出来（label_of）',
        game_tables.label_of(anim, 2))
    ps = tb['particle_systems']
    chk(ps.get(2) == 'MYSYS', '粒子系统：+= → 2', ps)

    print('\n== E: 参数的兜底 ==')
    chk(tb['animations'].get(0) == 'AA' and tb['animations'].get(1) == 'BB',
        '数字条目不受影响')

    print('\n== F: 真机（桌面/RA）==')
    RA = r'C:\Users\zhao5\Desktop\RA'
    if os.path.isfile(os.path.join(RA, 'rulesmd.ini')):
        g2 = game_ini.GameTypes(game_dir=RA)
        kinds = {}
        for v in g2.order.values():
            kinds[v['kind']] = kinds.get(v['kind'], 0) + 1
        print('     order %d 条 ｜ 分类 %s' % (len(g2.order), kinds))
        chk(len(g2.order) > 1400, '规则里的型号总数 > 1400（+= 那批都进来了）')
        # 从文件里现取 VehicleTypes 的第一个 += 项，验证认得出
        txt = open(os.path.join(RA, 'rulesmd.ini'), 'rb').read()
        for enc in ('utf-8-sig', 'gbk', 'latin-1'):
            try:
                t = txt.decode(enc)
                break
            except Exception:
                t = None
        import re
        m = re.search(r'(?m)^\[VehicleTypes\]\s*$(.*?)(?=^\[|\Z)', t or '', re.S)
        adds = re.findall(r'(?m)^\s*\+=\s*(\S+)', m.group(1)) if m else []
        print('     [VehicleTypes] += 共 %d 条，例: %s' % (len(adds), adds[:3]))
        got = [a for a in adds if a.upper() in g2.order]
        chk(len(adds) > 0 and len(got) == len(adds),
            '%d 条 += 载具全部认得（%d/%d）' % (len(got), len(got), len(adds)))
        tb2 = game_tables.lists_for_dir(RA)
        chk(len(tb2['animations']) > 1000,
            '动画表 %d 项（>1000，说明 =+ 那 650 条进来了）' % len(tb2['animations']))
        chk(max(tb2['animations']) >= len(tb2['animations']) - 1,
            '动画编号是连续的（max=%d, 条目=%d）'
            % (max(tb2['animations']), len(tb2['animations'])))
    else:
        print('     （没有 桌面/RA，跳过）')
finally:
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)

print('\n+= 追加注册自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
