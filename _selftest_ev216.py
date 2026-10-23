# -*- coding: utf-8 -*-
"""§216 自检：单位模板库（从存档学"签名"，跨型号放置不再需要参考存档）。

玩家提议（2026-10-23）：
> 「我们是不是应该在存档编辑器内置一份单位和建筑模板，直接可以写入？」

做法与结论：
  · **不做**"内置整条游戏记录"：2316+173 字节里大量是对象键/指针，换一份档就失效，
    还有版权问题；
  · **做**"从玩家自己的存档学签名"：跨型号放置真正需要的只有 ~40 字节
    （移动方式 + `+0x270/+0x51C/+0x940/+0x944/+0x948/+0x94C/+0x954/+0x958`）。
  · 建筑不用做模板库 —— 已有更强的办法（触发法让引擎自己造，`place_building_by_trigger`）。

本自检锁死：
  ① 学出来的签名与"多数票"一致（`有空地.SAV` 8 架基洛夫里 7 架那个值）
  ② 少票数的样本不会覆盖多票数的旧条目
  ③ 目标档**没有该型号实例**时，`add_unit_by_signature()` 也能写出一架状态正确的单位
  ④ 8 个参数全部落到正确位置（`0x940` 之后在**伴随段**，边界要用 size+comp_size）
  ⑤ 落盘往返不变；未知型号会给出可操作的报错

用法：python _selftest_ev216.py [目标档(无该型号)] [参考档(有该型号)]
"""
from __future__ import print_function

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import (SaveDoc, UnitUnavailable, _SIG_SLOTS,      # noqa: E402
                         load_signature_cache, save_signature_cache, signature_cache_path)


def fp(*names):
    for n in names:
        for d in ('E:/YURI', 'E:/YURI/新建文件夹 (29)'):
            q = os.path.join(d, n)
            if os.path.isfile(q):
                return q
    return names[0]


SAV = sys.argv[1] if len(sys.argv) > 1 else fp('平地三V3.SAV')
REF = sys.argv[2] if len(sys.argv) > 2 else fp('有空地.SAV')

n_ok = n_bad = 0
fails = []

# ★自检**绝不许污染真库**：先备份 unit_signatures.json，跑完无条件还原
import atexit
_CACHE_F = signature_cache_path()
_CACHE_BAK = None
try:
    with open(_CACHE_F, 'rb') as _f:
        _CACHE_BAK = _f.read()
except Exception:
    _CACHE_BAK = None


def _restore_cache():
    try:
        if _CACHE_BAK is not None:
            with open(_CACHE_F, 'wb') as _f:
                _f.write(_CACHE_BAK)
        elif os.path.isfile(_CACHE_F):
            os.remove(_CACHE_F)
    except Exception:
        pass


atexit.register(_restore_cache)


def ck(name, cond, extra=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('  [OK]   %s %s' % (name, ('(%s)' % extra) if extra else ''))
    else:
        n_bad += 1
        fails.append(name)
        print('  [FAIL] %s  %s' % (name, extra))


def val(d, r, o):
    return struct.unpack_from('<I', d.plain, r['off'] + o)[0]


print('=' * 72)
print('§216 自检：单位模板库')
for f in (SAV, REF):
    ck('文件存在 %s' % os.path.basename(f), os.path.isfile(f), f)
if not all(os.path.isfile(f) for f in (SAV, REF)):
    sys.exit(1)

# ---------- ① 学出来的签名 == 多数票 ----------
ref = SaveDoc(REF)
zs = [r for r in ref.unit_records() if r['type'].upper() == 'ZEP']
ck('参考档里有基洛夫', len(zs) >= 2, '%d 架' % len(zs))
learned = ref.learn_unit_signatures(merge=False).get('ZEP')
ck('学到 ZEP 的签名', bool(learned), '')
if learned:
    ck('★签名含全部 8 个机体参数', len(learned['params']) == len(_SIG_SLOTS),
       '%d 个' % len(learned['params']))
    # 手工算多数票对照
    ok = True
    detail = []
    for o in _SIG_SLOTS:
        cnt = {}
        for r in zs:
            v = val(ref, r, o)
            cnt[v] = cnt.get(v, 0) + 1
        want = max(cnt.items(), key=lambda kv: (kv[1], kv[0] == 0))[0]
        got = learned['params'].get('0x%X' % o)
        if want != got:
            ok = False
            detail.append('0x%X 期望 %d 得 %s' % (o, want, got))
    ck('★每个参数都等于"多数票"（不是某一架的、也不是刚出生那一架的）', ok, '; '.join(detail))
    ck('移动方式 = Fly', learned.get('loco') == 'Fly', str(learned.get('loco')))
    ck('证据票数 = 实际架数', learned.get('votes') == len(zs),
       '%s vs %d' % (learned.get('votes'), len(zs)))

# ---------- ② 少票数的真样本不会覆盖多票数的老条目 ----------
ref.learn_unit_signatures()                       # 先把参考档（8 架）学进去
old_before = dict((load_signature_cache().get('types') or {}).get('ZEP') or {})
ck('模板库里已有 ZEP', bool(old_before), '票数 %s' % old_before.get('votes'))

# 找一份"只有 1 架该型号"的真存档（这是最容易把好数据冲掉的场景）
ONE = ''
for cand in ('放V3和ZEP.SAV', 'T54_ZEP_借Drive_20_179.SAV', 'T55_ZEP_飞_40_180.SAV'):
    q = fp(cand)
    if os.path.isfile(q):
        try:
            _t = SaveDoc(q)
            if sum(1 for r in _t.unit_records() if r['type'].upper() == 'ZEP') == 1:
                ONE = q
                break
        except Exception:
            continue
ck('找到"只含 1 架基洛夫"的存档（用于验证覆盖保护）', bool(ONE), ONE)
if old_before and ONE:
    dc = SaveDoc(ONE)
    dc.learn_unit_signatures()
    after = load_signature_cache()['types']['ZEP']
    ck('★1 票的真样本不会顶掉 29 票的老条目',
       after.get('votes') == old_before.get('votes')
       and after.get('params') == old_before.get('params'),
       'votes=%s（旧 %s） 0x270=%s' % (after.get('votes'), old_before.get('votes'),
                                      (after.get('params') or {}).get('0x270')))
    save_signature_cache({'version': 1,
                          'slots': ['0x%X' % o for o in _SIG_SLOTS],
                          'types': {**load_signature_cache()['types'], 'ZEP': old_before}})

# ---------- ③④ 没有该型号实例的档里，靠模板库写一架 ----------
d = SaveDoc(SAV)
ck('目标档里确实没有 ZEP 实例',
   not any(r['type'].upper() == 'ZEP' for r in d.unit_records()),
   '%d 个单位' % len(d.unit_records()))
sig = d.unit_signature('ZEP')
ck('能从模板库取到 ZEP 签名', bool(sig), '票数 %s' % (sig or {}).get('votes'))

cell = d.free_cell_near(30, 182, radius=8, clearance=1) or (30, 182)
info = d.add_unit_by_signature('ZEP', cell[0], cell[1], house=d.player_house(), veterancy=0)
ck('模板库写入成功', bool(info.get('key')), '#%x' % (info.get('key') or 0))
ck('★8 个参数全部应用（不是只应用前半段）', info.get('params_applied') == len(_SIG_SLOTS),
   str(info.get('params_applied')))
ck('★骨架是空闲状态', info.get('scaffold_idle') is True, str(info.get('scaffold_type')))
new = [r for r in d.unit_records() if r['key'] == info['key']][0]
ck('移动方式 Fly / 伴随 173B', new['loco'] == 'Fly' and new['comp_size'] == 173,
   '%s / %d' % (new['loco'], new['comp_size']))

bad = []
for o in _SIG_SLOTS:
    want = (sig.get('params') or {}).get('0x%X' % o)
    got = val(d, new, o)
    if want is None:
        continue
    if want != got:
        bad.append('0x%X %s!=%s' % (o, got, want))
ck('★每个参数落到了正确位置（0x940 之后在伴随段里）', not bad, '; '.join(bad))
ck('★当前高度 = 类型高度（空中，不卡地上）',
   val(d, new, 0xB8) == val(d, new, 0x270) and val(d, new, 0x270) > 0,
   '%d / %d' % (val(d, new, 0xB8), val(d, new, 0x270)))
ck('★没有残余指令（不会自己跑去打人）',
   val(d, new, 0x10C) == 0 and val(d, new, 0x11C) == 0 and val(d, new, 0x120) == 0
   and val(d, new, 0x198) == 0,
   '%d/%d/%d 目标=%#x' % (val(d, new, 0x10C), val(d, new, 0x11C),
                          val(d, new, 0x120), val(d, new, 0x198)))

out = os.path.join(HERE, '_selftest_out', 'ev216_模板库.SAV')
if not os.path.isdir(os.path.dirname(out)):
    os.makedirs(os.path.dirname(out))
d.save(out, backup=False)
d2 = SaveDoc(out)
z2 = [r for r in d2.unit_records() if r['key'] == info['key']]
ck('落盘重读：记录还在', bool(z2), '')
if z2:
    ck('落盘重读：仍在目标格', (z2[0]['x'], z2[0]['y']) == (cell[0], cell[1]),
       str((z2[0]['x'], z2[0]['y'])))
    ck('落盘重读：参数不变',
       all(val(d2, z2[0], o) == val(d, new, o) for o in _SIG_SLOTS), '')

# ---------- ⑤ 未知型号要给出可操作报错 ----------
try:
    d.add_unit_by_signature('ZZZ_NOSUCH', 30, 182, house=d.player_house())
    ck('未知型号会报错', False, '没有抛异常')
except UnitUnavailable as e:
    ck('★未知型号报错里写了"怎么补"', '学习模板' in str(e) or '参考存档' in str(e),
       str(e).replace('\n', ' ')[:60])
except Exception as e:
    ck('未知型号会报错', False, repr(e))

print('-' * 72)
print('§216 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
print('模板库：%s' % signature_cache_path())
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
