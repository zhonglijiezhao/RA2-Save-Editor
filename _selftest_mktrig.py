"""_selftest_mktrig.py —— 「新建触发」修复的验收自检（只读源档，写到临时目录）。

覆盖 2026-10-18 15:3x 的两处修复：
  ① 新建触发的**标签 +0xB0 必须 = 0**（= 1 的标签会被引擎排除在评估之外，
     实测同一份大任务档：1 ⇒ 什么都不出；0 ⇒ 光棱塔当场刷出）；
  ② `trigger_action()` 改用「实例 +0xC4 = 动作对象的键（动作 +0x10）」定位，
     地编/后来追加的触发不会再找错动作。

用法：python _selftest_mktrig.py
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc            # noqa: E402

BASE = r'C:\Users\zhao5\Desktop\1RA2存档分析工具\RA2存档编辑器'


def _find(name, root=r'E:\YURI'):
    """游戏会把读过的档挪进「新建文件夹 (n)」⇒ 按文件名全盘找，别写死路径。"""
    for dp, _dn, fn in os.walk(root):
        if name in fn:
            return os.path.join(dp, name)
    return os.path.join(root, name)


SRC_BIG = _find('加觖发刷电厂.SAV')
SRC_FA2 = _find('有电厂 电厂还未刷出时存档.SAV')

ok = True


def check(name, cond, detail=''):
    global ok
    print(('  OK   ' if cond else '  FAIL ') + name + (('  ← ' + detail) if detail and not cond else ''))
    if not cond:
        ok = False


def key_of_action(doc, pl, o):
    return struct.unpack_from('<I', pl, o + 16)[0]


def main():
    print('[1] create_trigger 产物字段（大任务基档）')
    tmp = tempfile.mkdtemp(prefix='mktrig_')
    out = os.path.join(tmp, 'mk.SAV')
    d = SaveDoc(SRC_BIG)
    info = d.create_trigger('SelfTestTrig', 13, 10, 125, 0, ac_name='ATESLA',
                            ac_cell=(89, 250), owner='Neutral')
    d.save(out)
    e = SaveDoc(out)
    pl = bytes(e.plain)
    m = e._instance_maps()
    ks = [k for k, v in m['sid'].items() if v == info['trigger_id']]
    check('新触发有实例对象', bool(ks), info['trigger_id'])
    o = m['inst'][ks[0]]
    v = struct.unpack_from('<I', pl, o + 0xB0)[0]
    check('实例 +0xB0 = 0x01010101（启用 + 三难度）', v == 0x01010101, hex(v))

    g = e.TAG_CLASS_GUID
    i = pl.find(g)
    tg = None
    while i >= 0:
        if bytes(pl[i + 0x38:i + 0x40]) == info['tag_id'].encode():
            tg = i
            break
        i = pl.find(g, i + 1)
    check('标签对象存在', tg is not None, info['tag_id'])
    if tg is not None:
        tv = struct.unpack_from('<I', pl, tg + 0xB0)[0]
        check('★标签 +0xB0 = 0（不然引擎不评估）', tv == 0, hex(tv))

    ke = struct.unpack_from('<I', pl, o + 0xC0)[0]
    ka = struct.unpack_from('<I', pl, o + 0xC4)[0]
    acts, evs = e._split_tables()
    eo = [x['obj'] for x in evs if struct.unpack_from('<I', pl, x['obj'] + 16)[0] == ke]
    ao = [x['obj'] for x in acts if struct.unpack_from('<I', pl, x['obj'] + 16)[0] == ka]
    check('事件对象（按 +0xC0 键）', len(eo) == 1)
    check('动作对象（按 +0xC4 键）', len(ao) == 1)
    if eo:
        et = struct.unpack_from('<I', pl, eo[0] + 0x40)[0]
        ep = struct.unpack_from('<I', pl, eo[0] + 0x48)[0]
        check('事件类型 13 / 参数 = 10（秒）', (et, ep) == (13, 10), '%s/%s' % (et, ep))
    if ao:
        at = struct.unpack_from('<I', pl, ao[0] + 0x40)[0]
        aw = struct.unpack_from('<I', pl, ao[0] + 0x58)[0]
        an = bytes(pl[ao[0] + 0x68:ao[0] + 0x70]).split(b'\x00')[0]
        check('动作 125 / ATESLA / 落点 (89,250)',
              at == 125 and an == b'ATESLA' and e.waypoint_cell(aw) == (89, 250),
              '%s %r %r' % (at, an, e.waypoint_cell(aw)))

    print('[2] trigger_action 定位链（地编追加的触发不再找错）')
    f = SaveDoc(SRC_FA2)
    r = f.trigger_action('01001748')
    check('01001748 → 行为 125 / GAPOWR / (81,245)',
          r['type'] == 125 and r['name'] == 'GAPOWR' and r['cell'] == (81, 245),
          repr(r))

    print('[3] 缺省所属方 = 玩家阵营（存档编辑器里最符合预期）')
    d2 = SaveDoc(SRC_FA2)
    info2 = d2.create_trigger('OwnerDefault', 13, 10, 125, 0, ac_name='ATESLA',
                              ac_cell=(88, 249))
    tmp2 = os.path.join(tmp, 'owner.SAV')
    d2.save(tmp2)
    e2 = SaveDoc(tmp2)
    pl2 = bytes(e2.plain)
    m2 = e2._instance_maps()
    o2 = m2['inst'][[k for k, v in m2['sid'].items() if v == info2['trigger_id']][0]]
    want = e2.house_trigger_owner_key(e2.player_house())
    got = struct.unpack_from('<I', pl2, o2 + e2.TRIG_OWNER_OFF + 56)[0]
    check('新建触发的所属方 = 玩家阵营键', got == want, '%#x vs %#x' % (got, want))
    g2 = e2.TAG_CLASS_GUID
    j = pl2.find(g2)
    tg2 = None
    while j >= 0:
        if bytes(pl2[j + 0x38:j + 0x40]) == info2['tag_id'].encode():
            tg2 = j
            break
        j = pl2.find(g2, j + 1)
    check('第二条新建触发的标签 +0xB0 = 0',
          tg2 is not None and struct.unpack_from('<I', pl2, tg2 + 0xB0)[0] == 0)

    print('')
    print('SELFTEST MKTRIG ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
