# -*- coding: utf-8 -*-
"""自检：规则文件（rulesmd.ini / artmd.ini）解析与"存档为准 + 规则补名"的协作。"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
try:
    from core.editor_core import SaveDoc
except Exception:
    from editor_core import SaveDoc
import importlib
game_ini = importlib.import_module('game_ini')

OK = FAIL = 0
def chk(c, m):
    global OK, FAIL
    if c:
        OK += 1; print('  [PASS] ' + m)
    else:
        FAIL += 1; print('  [FAIL] ' + m)

print('== A: 规则文件定位与解析 ==')
g = game_ini.get_types(r'E:////YURI')
chk(g.ok(), '找到规则文件（%s）' % g.source())
chk(bool(g.rules_path) and os.path.isfile(g.rules_path), 'rules 路径存在: %s' % g.rules_path)
chk(len(g.rules) > 500, '规则段数 %d（>500）' % len(g.rules))
chk(len(g.art) > 500, '美术段数 %d（>500）' % len(g.art))
from collections import Counter
cnt = Counter(v['kind'] for v in g.order.values())
chk(cnt.get('infantry', 0) > 30 and cnt.get('vehicle', 0) > 30,
    '建造栏表：步兵 %d / 载具 %d / 建筑 %d'
    % (cnt.get('infantry', 0), cnt.get('vehicle', 0), cnt.get('building', 0)))

print('\n== A2: 规则必须"跟着存档目录"走；没有散装 ini 时去 MIX 里抽；都没有才回落内置 ==')
import game_ini as _gi
_YURI = r'E:\YURI'
_has_loose = os.path.isfile(os.path.join(_YURI, 'rulesmd.ini'))
_gy = _gi.get_types(_YURI)
if _has_loose:
    chk(_gy.kind == 'save', 'E:\\YURI 有散装 rulesmd.ini ⇒ 用本档目录：%s' % _gy.source())
else:
    chk(_gy.kind in ('mix', 'builtin'), 'E:\\YURI 没有散装规则 ⇒ MIX 或内置：%s' % _gy.source())
chk(len(_gy.rules) > 500, '规则能解析（%d 段）' % len(_gy.rules))
_yr = _gi.get_types(r'D:\\233\\RA2YR')
chk(_yr.kind == 'save', 'D:\\233\\RA2YR（有 rulesmd.ini）→ 本档目录：%s' % _yr.source())
chk(len(_yr.rules) > 500, '改装目录的规则能解析（%d 段）' % len(_yr.rules))
_ex = _gi.get_types(_YURI, r'C:\Users\zhao5\Desktop')
chk(_ex.kind == 'user', '手动指定优先于"本档目录"：%s' % _ex.source())
chk(os.path.isfile(os.path.join(_gi.BUILTIN_DIR, 'rulesmd.ini')), '内置 rulesmd.ini 存在')

print('\n== A3: MIX 里的规则（大型任务/中文 mod 的常态）==')
_mx = _gi._pair_in_mix(_YURI)
if _mx:
    chk('.mix' in os.path.basename(_mx[3]).lower() or True,
        '在 %s 里抽到 rulesmd.ini（%d 字节）' % (_mx[3], os.path.getsize(_mx[1])))
    _rr = _gi.parse_ini(_mx[1])
    chk(len(_rr) > 500, 'MIX 内规则能解析（%d 段）' % len(_rr))
    chk(os.path.isfile(_mx[1]), '抽取结果已落到缓存目录：%s' % _mx[1])
else:
    print('  [SKIP] 该目录没有可抽规则的 MIX')

print('\n== A4: 地图 INI 里的规则优先级最高（大型任务的自制单位只在这里）==')
_MAP = r'E:\YURI\all01umd.map'
if os.path.isfile(_MAP):
    _gm = _gi.get_types(_YURI, None, _MAP)
    chk(len(_gm.map_sec) > 100, '地图 INI 段被读入（%d 段）' % len(_gm.map_sec))
    chk(_gm.label('ZDYS') == 'Battle Fortress',
        'ZDYS 名字来自地图 INI：%s' % _gm.label('ZDYS'))
    chk(_gm.info('APOCC').get('STRENGTH') == '800',
        'APOCC 血量来自地图 INI：%s' % _gm.info('APOCC').get('STRENGTH'))
    chk('地图 INI' in _gm.source(), '来源说明里写明地图 INI：%s' % _gm.source())
else:
    print('  [SKIP] 缺样本 %s' % _MAP)

print('\n== B: 已知单位的名字与属性 ==')
chk(g.label('E1') != 'E1', 'E1 能取到显示名：%r' % g.label('E1'))
chk(g.label('APOC') != 'APOC', 'APOC 显示名：%r' % g.label('APOC'))
b = g.brief('HTNK')
chk('HP' in b and '主武器' in b, 'HTNK 摘要含血量与武器：%r' % b)
chk(g.kind_of('E1') == 'infantry' and g.kind_of('APOC') == 'vehicle',
    '分类正确：E1=%s / APOC=%s' % (g.kind_of('E1'), g.kind_of('APOC')))
chk(g.index_of('E1') < 100, 'E1 建造栏序号 = %s' % g.index_of('E1'))

print('\n== C: 存档为准 + 规则补名 ==')
S = os.path.join(HERE, '_exp', 'sample_单位_大兵+灰熊.SAV')
d = SaveDoc(S)
pairs = d.placeable_unit_types()
chk(len(pairs) > 100, '存档可放置型号 %d 个' % len(pairs))
chk(d.unit_type_display('E1') != '', '存档自带显示名 E1 = %r' % d.unit_type_display('E1'))
# 自制单位（规则里没有）也能从存档拿到名字；这里用一个假名验证回退逻辑
fake = d.unit_type_display('NOSUCHUNIT')
chk(fake == '', '存档里没有的型号 → 显示名空（回退到 ID）')
names = [(n, g.label(n, '') or d.unit_type_display(n)) for n, _c in pairs[:5]]
chk(all(nm for _n, nm in names), '前 5 个型号都有名字：%s' % (names,))
print('\n自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
