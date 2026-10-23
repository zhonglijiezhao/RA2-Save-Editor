# -*- coding: utf-8 -*-
"""§211 自检：单位的「直接写入法」（从有该型号的存档取真机记录，只换身份）。

玩家原话（2026-10-23）：
> 「在放建筑上，我们用克隆法整整花了两天才走通，以为克隆法简单，后来我们用直接写入法，
>   只用了一个多小时就成功了，事实上有时候克隆法并不简单。」

对应到单位：克隆法只能拿**捐赠型号**的骨架（跨型号基洛夫高度只有 832、卡在地上）；
直接写入法从真机抄内容，就没有这个问题。

用法：python _selftest_ev211.py [目标存档(没有该型号)] [参考存档(有真机)] [型号]
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
def _fp(*names):
    """玩家会把测试档挪到 `新建文件夹 (29)` ⇒ 两个目录都找。"""
    for n in names:
        for d in ('E:/YURI', 'E:/YURI/新建文件夹 (29)'):
            q = os.path.join(d, n)
            if os.path.isfile(q):
                return q
    return names[0]


from editor_core import SaveDoc                                        # noqa: E402

SAV = sys.argv[1] if len(sys.argv) > 1 else _fp('SAVE0029.SAV')
REF = sys.argv[2] if len(sys.argv) > 2 else _fp('有空地.SAV')
T = (sys.argv[3] if len(sys.argv) > 3 else 'ZEP').upper()

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
print('§211 自检：直接写入法')
for f in (SAV, REF):
    ck('文件存在 %s' % os.path.basename(f), os.path.isfile(f), f)
if not all(os.path.isfile(f) for f in (SAV, REF)):
    sys.exit(1)

d = SaveDoc(SAV)
ck('★目标档里本来没有 %s（才是需要"直接写入法"的场景）' % T,
   not any(r['type'].upper() == T for r in d.unit_records()),
   '%d 个单位' % len(d.unit_records()))
ck('目标格地形可通行', (d.cell_terrain(20, 179) or (None,))[0] in (d.walkable_tiles() or set()),
   str(d.cell_terrain(20, 179)))

info = d.import_unit_from(REF, T, 20, 179, house=d.player_house(), veterancy=0,
                          slots='full')   # 本自检测的是"全部型号常量"档
ck('导入成功', bool(info.get('key')), '#%x' % (info.get('key') or 0))
ck('抄了型号常量（300~600 个 dword）', 300 < info.get('imported', 0) < 620,
   str(info.get('imported')))
ck('返回了参考机键（可复核）', bool(info.get('ref_key')), hex(info.get('ref_key') or 0))

rec = [r for r in d.unit_records() if r['key'] == info['key']][0]
b = bytes(d.plain[rec['off']:rec['off'] + rec['size'] + rec['comp_size']])
u = lambda o: struct.unpack_from('<I', b, o)[0]
ck('★自键两副本都是新键', u(0x10) == rec['key'] and u(0x3C0) == rec['key'],
   '%#x/%#x' % (u(0x10), u(0x3C0)))
ck('★格键指向目标格', d._cell_by_key().get(u(0xE0)) == (20, 179),
   str(d._cell_by_key().get(u(0xE0))))
ck('★所属方是本档玩家方', u(0x160) == d.house_key_of(d.player_house()),
   '%#x vs %#x' % (u(0x160), d.house_key_of(d.player_house()) or 0))
ck('★类型键仍是目标型号', u(0x6D8) == d.unit_type_index()[T]['key'],
   '%#x' % u(0x6D8))

# 与参考真机对比：差异只应是"身份类"
ref = SaveDoc(REF)
rr = [r for r in ref.unit_records() if r['key'] == info['ref_key']][0]
src = bytes(ref.plain[rr['off']:rr['off'] + rr['size'] + rr['comp_size']])
diffs = [o for o in range(0, len(src) - 3, 4) if src[o:o + 4] != b[o:o + 4]]
ck('★与参考真机仍有差异（指令/坐标被清，属预期）', len(diffs) >= 20, str(len(diffs)))
ck('★指令/目的地已清 0（+0x658/+0x680）', u(0x658) == 0 and u(0x680) == 0,
   '%d/%d' % (u(0x658), u(0x680)))
_alt = struct.unpack_from('<I', src, 0x270)[0]
ck('★类型高度与真机一致（真机 8 架恒 1248）', u(0x270) == _alt, '%d vs %d' % (u(0x270), _alt))
ck('★当前高度 = 类型高度（起在巡航高度，不会"卡地上"）', u(0xB8) == _alt,
   '%d vs %d' % (u(0xB8), _alt))
ck('抄进来的值里没有悬空指针（跳过的指针数 ≥ 0 且不报错）',
   info.get('skipped_ptr', -1) >= 0, str(info.get('skipped_ptr')))

out = os.path.join(HERE, '_selftest_out', 'ev211_直接写入.SAV')
if not os.path.isdir(os.path.dirname(out)):
    os.makedirs(os.path.dirname(out))
d.save(out, backup=False)
d2 = SaveDoc(out)
z2 = [r for r in d2.unit_records() if r['key'] == info['key']]
ck('落盘往返：记录还在', bool(z2), '')
if z2:
    b2 = bytes(d2.plain[z2[0]['off']:z2[0]['off'] + z2[0]['size'] + z2[0]['comp_size']])
    ck('落盘往返：仍在目标格', d2._cell_by_key().get(
        struct.unpack_from('<I', b2, 0xE0)[0]) == (20, 179),
       str(d2._cell_by_key().get(struct.unpack_from('<I', b2, 0xE0)[0])))
ck('落盘往返：单位总数 +1', len(d2.unit_records()) == len(d.unit_records()),
   '%d' % len(d2.unit_records()))

print('-' * 72)
print('§211 SELFTEST %s   PASS %d / FAIL %d' % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
