# -*- coding: utf-8 -*-
"""§205 自检：类型中文名 —— 表里只留确信的；表外一律回落到**存档自带显示名**，不许猜。

玩家当场纠错（2026-10-23）：`YACOMD` 被显示成"尤里建造场?"。
  · `YACOMD` = **尤里指挥中心**（Yuri Command Center）
  · 尤里建造场 = **`YACNST`**（Yuri Construction Yard）
⇒ 老表里有几条是"猜的"（还带问号）。新规则：表里没有的类型，用存档 `+120` 的引擎真名，
   宁可显示英文也不猜中文。

用法：python _selftest_ev205.py [存档]
"""
import importlib.util
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc                                          # noqa: E402

spec = importlib.util.spec_from_file_location('sedgui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SAV = sys.argv[1] if len(sys.argv) > 1 else 'C:/Users/zhao5/Desktop/SAVE1D3F.SAV'
n_ok = n_bad = 0
fails = []


def ck(name, cond, extra=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('  [OK]   %s %s' % (name, ('(%s)' % extra) if extra else ''))
    else:
        n_bad += 1
        fails.append(name)
        print('  [FAIL] %s  %s' % (name, extra))


print('=' * 72)
print('§205 自检：类型中文名')
d = SaveDoc(SAV)
app = m.App()
app.withdraw()
try:
    app._set_doc(d)
    ck('★YACOMD = 尤里指挥中心', app._type_disp('YACOMD') == '尤里指挥中心', app._type_disp('YACOMD'))
    ck('★YACNST = 尤里建造场', app._type_disp('YACNST') == '尤里建造场', app._type_disp('YACNST'))
    ck('GACNST = 盟军建造场', app._type_disp('GACNST') == '盟军建造场', app._type_disp('GACNST'))
    bad = {k: v for k, v in m.App.TYPE_CN.items() if (not v) or ('?' in v) or ('？' in v)}
    ck('★中文表里没有带问号的猜测项', not bad, str(bad))
    ck('中文表里没有重复代码', len(m.App.TYPE_CN) == len(set(m.App.TYPE_CN)), str(len(m.App.TYPE_CN)))

    # 表外回落：存档自带显示名
    en = d.type_display('GTGCAN') or ''
    ck('存档能读出引擎真名（GTGCAN）', bool(en), en)
    ck('表外类型回落到真名而不是裸代码',
       app._type_disp('CAEURO05') not in ('CAEURO05', '?', ''),
       app._type_disp('CAEURO05'))
    ck('表内类型优先用中文（GTGCAN=法国巨炮）', app._type_disp('GTGCAN') == '法国巨炮',
       app._type_disp('GTGCAN'))
    ck('完全未知的类型退回代码本身', app._type_disp('ZZZ_NOPE') == 'ZZZ_NOPE',
       app._type_disp('ZZZ_NOPE'))
finally:
    try:
        app.destroy()
    except Exception:
        pass

print('-' * 72)
print('§205 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
