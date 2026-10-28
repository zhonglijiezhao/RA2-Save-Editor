# -*- coding: utf-8 -*-
"""§296 自检：改存档里的武器 / 弹头数值（定长写入）。

为什么单独立一套（2026-10-28）：
  玩家确认「改 rulesmd.ini **不会**影响已存盘存档里的武器效果」⇒ 读档时引擎用的是
  存档里那份武器类型表。所以改这些字段是真正生效的，而且只影响这一份存档。

  这条路之所以安全，是因为 **Damage/ROF/Range 是定长 u32、Verses 是定长 double**
  ⇒ 只改值不改长度 ⇒ 明文偏移一个都不动，不碰"残留键 / 块布局"那些雷。
  本自检把"长度必须不变"当成第一硬断言。

跑法：python _selftest_ev296_setweapon.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc  # noqa: E402

SRC = r'E:/YURI/SAVE0665_修改.SAV'
OUT = os.path.join(HERE, '_selftest_out', '_ev296.SAV')
PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


def expect_raise(fn, exc, msg):
    try:
        fn()
    except exc as e:
        ck(True, msg + '（%s: %s）' % (type(e).__name__, str(e)[:48]))
        return
    except Exception as e:
        ck(False, msg + '（抛了 %s，不是 %s）' % (type(e).__name__, exc.__name__))
        return
    ck(False, msg + '（居然没报错）')


if not os.path.exists(SRC):
    print('样本不存在：%s —— 跳过' % SRC)
    sys.exit(0)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
if os.path.exists(OUT):
    os.remove(OUT)

SaveDoc.set_game_dir(r'E:/YURI')
d = SaveDoc(SRC)
L0 = len(d.plain)

# ── ① 改武器：值生效、长度不变 ─────────────────────────────
r = d.set_weapon_field('PrismShot', 'damage', 999)
ck(r['changed'] and r['old'] == 120 and r['new'] == 999,
   'PrismShot Damage 120 → 999（实得 %s → %s）' % (r['old'], r['new']))
ck(len(d.plain) == L0, '★ 明文长度未变（%d，定长写入）' % len(d.plain))

r = d.set_weapon_field('PrismShot', 'range', 12.5)
ck(r['new'] == 3200, 'Range 12.5 格 → 3200 lepton（12.5×256，实得 %s）' % r['new'])
ck(len(d.plain) == L0, '明文长度仍未变')

r = d.set_weapon_field('Vulcan2', 'rof', 5)
ck(r['old'] == 26 and r['new'] == 5, 'Vulcan2 ROF 26 → 5')

# 读回（内存里立刻可见）
w = {x['name']: x for x in d.weapon_objects()}
ck(w['PrismShot']['damage'] == 999, '内存里 PrismShot Damage = 999')
ck(abs(w['PrismShot']['range_cells'] - 12.5) < 1e-9, '内存里 PrismShot Range = 12.5 格')
ck(w['Vulcan2']['rof'] == 5, '内存里 Vulcan2 ROF = 5')
ck(w['M60']['damage'] == 15, '没动的武器不受影响（M60 Damage 仍 15）')

# 改成同值 ⇒ changed=False，不该写
r = d.set_weapon_field('PrismShot', 'damage', 999)
ck(r['changed'] is False, '改成相同值时 changed=False（不做无谓写入）')

# ── ② 改弹头 Verses ────────────────────────────────────────
r = d.set_warhead_verse('PrismWarhead', 'heavy', 500)
ck(r['index'] == 6 and r['old'] == 1.0 and r['new'] == 5.0,
   'PrismWarhead 对 heavy(第 6 项) 100%% → 500%%（实得 %s → %s）'
   % (r['old'], r['new']))
r = d.set_warhead_verse('PrismWarhead', 1, 0)          # 序号 1 = none
ck(r['armor'] == 'none' and r['new'] == 0.0,
   '用序号 1 改 none 为 0%%（实得 %s）' % r['armor'])
ck(len(d.plain) == L0, '★ 改完弹头，明文长度仍未变')

wh = {x['name']: x for x in d.warhead_objects()}['PrismWarhead']
ck(abs(wh['verses'][5] - 5.0) < 1e-9 and abs(wh['verses'][0]) < 1e-9,
   '内存里 Verses[heavy]=5.0 / Verses[none]=0.0')
ck(abs(wh['verses'][7] - 0.5) < 1e-9, '没动的项不变（Verses[steel] 仍 0.5）')

# ── ③ 越界 / 非法输入必须报错 ──────────────────────────────
expect_raise(lambda: d.set_weapon_field('PrismShot', 'damage', -1),
             ValueError, 'Damage = -1 被拒')
expect_raise(lambda: d.set_weapon_field('PrismShot', 'rof', 0),
             ValueError, 'ROF = 0 被拒（射速不能为 0）')
expect_raise(lambda: d.set_weapon_field('PrismShot', 'range', 0.1),
             ValueError, 'Range = 0.1 被拒（精度只到 1/8 格）')
expect_raise(lambda: d.set_weapon_field('PrismShot', 'range', 300),
             ValueError, 'Range = 300 格被拒（超 255 上限）')
expect_raise(lambda: d.set_weapon_field('没这把枪', 'damage', 1),
             KeyError, '不存在的武器被拒')
expect_raise(lambda: d.set_weapon_field('PrismShot', 'xxx', 1),
             ValueError, '非法字段名被拒')
expect_raise(lambda: d.set_warhead_verse('PrismWarhead', 12, 100),
             ValueError, '装甲序号 12 被拒（只有 1..11）')
expect_raise(lambda: d.set_warhead_verse('PrismWarhead', 'heavvy', 100),
             ValueError, '拼错的装甲名被拒')
expect_raise(lambda: d.set_warhead_verse('没这个弹头', 1, 100),
             KeyError, '不存在的弹头被拒')

# ── ③.5 大射程：能写、给软警告、且不会"改完就从列表里消失" ──────
# 2026-10-28 玩家把射程改成 90 格，发现武器从列表里没了 —— 根因是 `weapon_objects()`
# 的过滤还写死 `0x4000`（64 格）。这条断言专门守这个坑。
r = d.set_weapon_field('EiffelBolt', 'range', 90)
ck(r['new'] == 23040 and r['changed'], 'EiffelBolt 射程改成 90 格（23040 lepton）')
ck(bool(r['warn']), '90 格超出游戏自带最大值 ⇒ 给出软警告（不拦）')
names_now = {x['name'] for x in d.weapon_objects()}
ck('EiffelBolt' in names_now, '★ 改成 90 格后它**仍在武器列表里**（不会消失）')
ck(next(x for x in d.weapon_objects() if x['name'] == 'EiffelBolt')['range_cells'] == 90.0,
   '列表里读回来就是 90 格')
r = d.set_weapon_field('EiffelBolt', 'range', 25)
ck(r['warn'] == '', '25 格（游戏自带最大值以内）没有警告')
r = d.set_weapon_field('EiffelBolt', 'range', 200)
ck(r['new'] == 51200, '上限附近 200 格也能写（51200 lepton）')

# ── ④ 落盘 + 重开核对（真正的验收）──────────────────────────
res = d.save(OUT)
ck(res['blocks'] == res['blocks_expect'],
   '保存后块布局核对通过（%s/%s）' % (res['blocks'], res['blocks_expect']))

d2 = SaveDoc(OUT)
w2 = {x['name']: x for x in d2.weapon_objects()}
wh2 = {x['name']: x for x in d2.warhead_objects()}['PrismWarhead']
ck(w2['PrismShot']['damage'] == 999, '重开：PrismShot Damage = 999')
ck(abs(w2['PrismShot']['range_cells'] - 12.5) < 1e-9, '重开：PrismShot Range = 12.5 格')
ck(w2['Vulcan2']['rof'] == 5, '重开：Vulcan2 ROF = 5')
ck(abs(wh2['verses'][5] - 5.0) < 1e-9, '重开：PrismWarhead 对 heavy = 500%')
ck(abs(wh2['verses'][7] - 0.5) < 1e-9, '重开：PrismWarhead 对 steel 仍 50%')

# 其余部分必须完好
ck(len(d2.records) == len(d.records), '触发记录数不变（%d）' % len(d2.records))
ck(len(d2.buildings()) == len(d.buildings()),
   '建筑数不变（%d）' % len(d2.buildings()))
ck(len(w2) == len(w), '武器对象总数不变（%d）' % len(w2))

try:
    os.remove(OUT)
except Exception:
    pass

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败项：')
    for f in FAIL:
        print('  - ' + f)
sys.exit(1 if FAIL else 0)
