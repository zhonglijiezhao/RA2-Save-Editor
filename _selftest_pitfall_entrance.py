# -*- coding: utf-8 -*-
"""§SOV09 回归：**进入事件 + 挂接多个对象，只有重复类型 1 才永不执行**。

玩家 2026-11-04（SAVE6784：一条触发挂在 18 个单元标记上）：
> 这个提示是BUG永不执行 只有一种情况，这个触发是进入事件，挂接多个建筑，重复类型为1。
> 对于重复类型为0和2的情况不是不执行，而是不管挂接多少个建筑，只要进入一个建筑，
> 这个事件就被满足。进入事件挂接到单元标记的也是同样，只要重复类型不为1，必然是有效的

本自检直接对着 `repeat_pitfall()`（编辑器红字与列表 ⚠ 都走它）验：
  A. 持久 0 + 进入 + 挂 18 个 ⇒ **不报错**（以前会报"已知 BUG"）
  B. 持久 2 + 进入 + 挂 18 个 ⇒ 不报"永不执行"
  C. 持久 1 + 进入 + 挂 18 个 ⇒ 报"⚠ 已知 BUG … 永不执行"
  D. 持久 1 + 挂 0 个 ⇒ 报"永不执行"（另一种坑，与本次无关但要一起守住）
"""
import os
import shutil
import struct
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'core'))
from editor_core import SaveDoc   # noqa: E402

SAV = r'E:\YURI\SAVE6784.SAV'
SID = '01000002'          # 进入事件 + 挂 18 个单元标记，原持久 0
OK = FAIL = 0


def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s' % msg)


def set_repeat(d, sid, v):
    """直接写标签对象 +0xB0（持久性），然后作废缓存。"""
    o = d.trigger_objects(sid)
    d.plain[o['tag_obj'] + 0xB0:o['tag_obj'] + 0xB4] = struct.pack('<I', v)
    d.invalidate_caches(rescan=True)


def first_line(s):
    return (s or '').split('\n')[0]


def main():
    if not os.path.exists(SAV):
        print('SKIP：缺样本 SAVE6784.SAV')
        return 0
    tmp = os.path.join(tempfile.gettempdir(), '_selftest_pitfall.sav')
    shutil.copy2(SAV, tmp)
    d = SaveDoc(tmp)

    evs = [e['type'] for e in d.trigger_event_list(SID)]
    det = d.trigger_detail(SID)
    print('样本 %s：事件 %s · 挂接 %d 个 · 自身持久性 %s'
          % (SID, evs, det['attached'], det['repeat']))
    chk(evs == [1], '事件是「进入事件」（1）')
    chk(int(det['attached']) >= 2, '挂接 %d 个对象（≥2）' % det['attached'])

    print('== A. 持久 0 + 进入 + 挂多个 ⇒ 正常，不报错 ==')
    chk(d.repeat_pitfall(SID, '0') == '',
        '持久0 的坑提示为空（实际 %r）' % first_line(d.repeat_pitfall(SID, '0')))

    print('== B. 持久 2 + 进入 + 挂多个 ⇒ 不报"永不执行" ==')
    msg2 = d.repeat_pitfall(SID, '2')
    chk('永不执行' not in msg2 and '已知 BUG' not in msg2,
        '持久2 不提"永不执行"（实际 %r）' % first_line(msg2))

    print('== C. 持久 1 + 进入 + 挂多个 ⇒ 已知 BUG ==')
    set_repeat(d, SID, 1)
    chk(d.trigger_repeat_type(SID) == '1', '已把持久性改成 1（%s）' % d.trigger_repeat_type(SID))
    msg1 = d.repeat_pitfall(SID, '1')
    chk(msg1.startswith('⚠ 已知 BUG'), '报的是"⚠ 已知 BUG"：%s' % first_line(msg1))
    chk('永不执行' in msg1 and '进入事件' in msg1, '文案点明"进入事件 + 永不执行"')

    print('== D. 持久 1 + 挂 0 个 ⇒ 另一种坑（守住）==')
    set_repeat(d, SID, 0)
    o = d.trigger_objects(SID)
    d.plain[o['cell'] + 64:o['cell'] + 68] = struct.pack('<I', 0)   # CellTag 挂接数 → 0
    d.invalidate_caches(rescan=True)
    msg0 = d.repeat_pitfall(SID, '1')
    chk('永不执行' in msg0, '挂 0 个 + 持久1 也报永不执行')
    chk(not msg0.startswith('⚠ 已知 BUG'), '但不再是那句"已知 BUG"（是挂 0 个的另一种）')

    os.remove(tmp)
    print('\n§SOV09 回归：PASS %d / FAIL %d' % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
