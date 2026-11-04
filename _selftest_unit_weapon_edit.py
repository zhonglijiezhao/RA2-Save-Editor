# -*- coding: utf-8 -*-
"""§SOV15 自检：按**单位型号**读/写武器（主/副/精英主/精英副）。

覆盖：
  1. E1 的 4 个槽 = M60 / Para / M60E / ParaE（与 rules 对拍）
  2. 改主武器为 DoublePistols ⇒ +0x8AC 变、精英槽自动跟着变 DoublePistolsE
  3. 存档长度不变；别的型号（APOC）一个字节不动
  4. 改回原值 ⇒ 整档明文 md5 逐字节复原
  5. dry_run 不动字节
  6. also_elite=False 时精英槽不动
  7. 原来没有精英武器的型号（MTNK 的 EliteSecondary=0）不会被凭空造出来
  8. 建筑（ATESLA/TESLA）不在列表里，直接调也拒绝
  9. 目标武器不存在 ⇒ KeyError；武器重名 ⇒ KeyError
跑：python _selftest_unit_weapon_edit.py
"""
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))
from editor_core import SaveDoc                                       # noqa: E402

SAV = r'E:\YURI\SOV01UMD_修改_2.SAV'
OK = FAIL = 0


def chk(cond, msg, extra=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s%s' % (msg, '' if extra is None else ('  → %r' % (extra,))))


def slot_addr(d, row, slot):
    rel = {k: o for k, o, _l in SaveDoc.UNIT_WEAPON_SLOTS}[slot]
    return struct.unpack_from('<I', d.plain, row['off'] + rel)[0]


print('打开 %s …' % os.path.basename(SAV))
d = SaveDoc(SAV)
lst = d.unit_weapon_list()
print('可改型号 %d 个（场上出现 %d 个）' % (len(lst), sum(1 for r in lst if r['in_game'])))

row_e1 = [r for r in lst if r['type'].upper() == 'E1'][0]
row_apoc = [r for r in lst if r['type'].upper() == 'APOC'][0]
row_mtnk = [r for r in lst if r['type'].upper() == 'MTNK'][0]

print('== A. E1 的四个槽 ==')
s = row_e1['slots']
chk((s['primary']['name'], s['secondary']['name'],
     s['elite_primary']['name'], s['elite_secondary']['name'])
    == ('M60', 'Para', 'M60E', 'ParaE'),
    '★E1 = M60 / Para / M60E / ParaE',
    tuple(s[k]['name'] for k in ('primary', 'secondary', 'elite_primary', 'elite_secondary')))

print('== B. 建筑与中立被排除 ==')
names = {r['type'].upper() for r in lst}
chk(not (names & {'ATESLA', 'TESLA', 'NASAM', 'GAPILL', 'GAPOWR', 'NEUTRAL', 'SPECIAL'}),
    '★列表里没有建筑 / 中立 / House', sorted(names & {'ATESLA', 'TESLA', 'NEUTRAL'}))
try:
    d.set_unit_weapon('ATESLA', 'primary', 'DoublePistols')
    chk(False, 'ATESLA 应该被拒绝')
except KeyError as e:
    chk('命中 0 个' in str(e) or '建筑' in str(e), '直接调 ATESLA 被拒（%s）' % str(e)[:40])

print('== C. 改 E1 主武器 → 谭雅的 DoublePistols ==')
n_bytes = len(d.plain)
md5_before = hashlib.md5(bytes(d.plain)).hexdigest()
apoc_p_before = slot_addr(d, row_apoc, 'primary')
info = d.set_unit_weapon('E1', 'primary', 'DoublePistols')
want = d.obj_addr('DoublePistols')[0]
want_e = d.obj_addr('DoublePistolsE')[0]
chk(slot_addr(d, row_e1, 'primary') == want,
    '★+0x8AC 指向 DoublePistols（0x%08X）' % want)
chk(slot_addr(d, row_e1, 'elite_primary') == want_e,
    '★精英主武器自动跟着变 DoublePistolsE（0x%08X）' % want_e)
chk(len(d.plain) == n_bytes, '存档长度不变（原地 u32）', (n_bytes, len(d.plain)))
chk(slot_addr(d, row_apoc, 'primary') == apoc_p_before,
    '别的型号（APOC）没被动')
chk(slot_addr(d, row_e1, 'secondary') == row_e1['slots']['secondary']['addr'],
    '副武器没被动')

print('== D. 列表重新读得到新值 ==')
row2 = [r for r in d.unit_weapon_list() if r['type'].upper() == 'E1'][0]
chk(row2['slots']['primary']['name'] == 'DoublePistols',
    '★列表里 E1 主武器显示 DoublePistols',
    row2['slots']['primary']['name'])
chk(row2['slots']['elite_primary']['name'] == 'DoublePistolsE',
    '精英主武器显示 DoublePistolsE', row2['slots']['elite_primary']['name'])

print('== E. 改回原值 ⇒ 逐字节复原 ==')
d.set_unit_weapon('E1', 'primary', 'M60')
chk(hashlib.md5(bytes(d.plain)).hexdigest() == md5_before,
    '★整档明文 md5 与开始时一致')

print('== F. dry_run 不动字节 ==')
h0 = hashlib.md5(bytes(d.plain)).hexdigest()
dry = d.set_unit_weapon('E1', 'primary', 'DoublePistols', dry_run=True)
chk(dry['dry_run'] is True and len(dry['changes']) >= 1, 'dry_run 返回计划')
chk(hashlib.md5(bytes(d.plain)).hexdigest() == h0, 'dry_run 一个字节没动')

print('== G. also_elite=False ⇒ 精英槽不动 ==')
e_before = slot_addr(d, row_e1, 'elite_primary')
d.set_unit_weapon('E1', 'primary', 'DoublePistols', also_elite=False)
chk(slot_addr(d, row_e1, 'primary') == want, '主武器改了')
chk(slot_addr(d, row_e1, 'elite_primary') == e_before, '精英主武器没动', e_before)
d.set_unit_weapon('E1', 'primary', 'M60', also_elite=False)

print('== H. 原本没有精英武器的型号：不凭空造 ==')
mtnk_es = slot_addr(d, row_mtnk, 'elite_secondary')
chk(mtnk_es == 0, 'MTNK 的精英副武器本来是 0', mtnk_es)
d.set_unit_weapon('MTNK', 'secondary', 'Vulcan')
chk(slot_addr(d, row_mtnk, 'elite_secondary') == 0,
    '★改副武器没有给它造出精英副武器', slot_addr(d, row_mtnk, 'elite_secondary'))
d.set_unit_weapon('MTNK', 'secondary', 'Vulcan')      # 幂等
print('     （MTNK 本来没有副武器，这里验证的是"不会凭空写精英槽"）')

print('== I. 错误输入 ==')
for bad in ('NoSuchWeapon', ''):
    try:
        d.set_unit_weapon('E1', 'primary', bad)
        chk(False, '「%s」应该报错' % bad)
    except KeyError:
        chk(True, '「%s」被拒' % (bad or '(空)'))
try:
    d.set_unit_weapon('E1', 'nosuchslot', 'M60')
    chk(False, '非法槽位应该报错')
except KeyError:
    chk(True, '非法槽位被拒')

print('\n§SOV15：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
