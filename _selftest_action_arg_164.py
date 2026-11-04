# -*- coding: utf-8 -*-
"""§SOV19：行为参数在 `+164` 还是 `+72` —— 按地编参数类型（ptype）判定。

玩家 2026-11-04 实测：`01000007 新触发1` 行为 23「计时开始」+ 27「计时设置(600)」，
地编写的进游戏右下角有 600 秒倒计时，存档编辑器写的没有。

真因：**ptype=6（数值型）的参数在对象 `+164`，不是 `+72`**。
      地编档实证 `(+72=0, +164=600)`；工具旧代码写成 `(+72=600, +164=0)` ⇒ 引擎读 0。

覆盖：判据表 / 读地编档 / 读写往返 / add_action / create_trigger 首条 / 非 164 类型不退化。
跑：python _selftest_action_arg_164.py
"""
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import (SaveDoc, action_arg_off, action_arg_is_164,   # noqa: E402
                         OBJ_ARG_OFF, ACT_VAR_OFF, ACTION_SIZE)

OK = FAIL = 0


def chk(cond, msg, got=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s%s' % (msg, ('　→ 实际 %r' % (got,)) if got is not None else ''))


def u32(pl, off):
    return struct.unpack_from('<I', pl, off)[0]


def argpair(d, act_off):
    """(对象 +72, 对象 +164)"""
    return u32(d.plain, act_off + OBJ_ARG_OFF), u32(d.plain, act_off + ACT_VAR_OFF)


DJ = r'E:\YURI\地编写触发.SAV'      # 地编写入、游戏内正常
TMP = os.path.join(tempfile.gettempdir(), '_sov19_arg164.SAV')

print('== A. 判据表（按 ptype）==')
cases = [(23, 72), (24, 72), (16, 72), (125, 72), (40, 72), (80, 72),
         (25, 164), (26, 164), (27, 164), (73, 164), (110, 164), (127, 164),
         (139, 164), (142, 164), (56, 164), (57, 164), (108, 164)]
bad = [(t, w, action_arg_off(t)) for t, w in cases if action_arg_off(t) != w]
chk(not bad, '17 个类型的位置全对（23/24/16/125/40/80 → +72；25/26/27/73/110/127/139/142/56/57/108 → +164）', bad)
chk(action_arg_is_164(27) and not action_arg_is_164(23), 'is_164：27 是、23 不是')

print('\n== B. 读地编档（游戏内 600 秒倒计时的真身）==')
if os.path.exists(DJ):
    d0 = SaveDoc(DJ)
    acts = d0.trigger_actions('01000007')
    a27 = next(a for a in acts if a['type'] == 27)
    p72, p164 = argpair(d0, a27['action_off'])
    chk(p72 == 0 and p164 == 600,
        '★行为 27 是 (+72=0, +164=600)（以前工具读成 600、位置错）', (p72, p164))
    chk(a27['arg'] == 600, 'trigger_actions 读到的参数 = 600', a27['arg'])
    det = d0.trigger_detail('01000007')
    d27 = next(a for a in det['actions'] if a['type'] == 27)
    chk(d27['arg'] == 600 and d27.get('arg_text') == '600', 'detail 也是 600 / 文案 "600"',
        (d27['arg'], d27.get('arg_text')))
    a23 = next(a for a in det['actions'] if a['type'] == 23)
    chk(a23['arg'] == 0, '行为 23（ptype 0）仍在 +72 读到 0', a23['arg'])
else:
    print('  [SKIP] 没有 %s' % DJ)

print('\n== C. set_action 写回 +164（并清 +72）==')
shutil.copy2(DJ, TMP)
d = SaveDoc(TMP)
acts = d.trigger_actions('01000007')
a27 = next(a for a in acts if a['type'] == 27)
off27 = a27['action_off']
before = (bytes(d.plain[off27:off27 + ACTION_SIZE]),
          len(d.plain))
info = d.set_action(a27, arg=0x0ABCDEF)
p72, p164 = argpair(d, off27)
chk(p72 == 0 and p164 == 0x0ABCDEF,
    '★写 0x0ABCDEF(11253039) → (+72=0, +164=11253039)', (p72, p164))
chk(len(d.plain) == before[1], '存档长度不变（原地 u32）')
chk(d.trigger_actions('01000007')[1]['arg'] == 0x0ABCDEF, '回读参数 = 0x0ABCDEF',
    d.trigger_actions('01000007')[1]['arg'])
chk(all(argpair(d, x['action_off'])[1] == 0
        for x in d.trigger_actions('01000007') if x['type'] != 27),
    '其它行为（23）的 +164 没被乱写')

d.set_action(d.trigger_actions('01000007')[1], arg=600)
p72, p164 = argpair(d, off27)
chk(p72 == 0 and p164 == 600, '改回 600 → 仍是 (+72=0, +164=600)', (p72, p164))
chk(bytes(d.plain[off27:off27 + ACTION_SIZE]) == before[0],
    '★改回原值 ⇒ 该行为对象逐字节复原')

print('\n== D. 非 164 类型不被带歪（23 仍写 +72）==')
a23 = d.trigger_actions('01000007')[0]
off23 = a23['action_off']
d.set_action(a23, arg=7)
p72, p164 = argpair(d, off23)
chk(p72 == 7 and p164 == 0, '行为 23 写 7 → (+72=7, +164=0)', (p72, p164))
d.set_action(d.trigger_actions('01000007')[0], arg=0)

print('\n== E. add_action(27, 600) 也写 +164 ==')
shutil.copy2(DJ, TMP + '.2')
d2 = SaveDoc(TMP + '.2')
n0 = len(d2.trigger_actions('01000007'))
info = d2.add_action('01000007', 27, 600)
acts2 = d2.trigger_actions('01000007')
chk(len(acts2) == n0 + 1, '行为数 %d → %d' % (n0, len(acts2)))
new27 = acts2[-1]
p72, p164 = argpair(d2, new27['action_off'])
chk(p72 == 0 and p164 == 600, '★新行为 27： (+72=0, +164=600)', (p72, p164))

print('\n== F. create_trigger 的第一条行为同样走 +164 ==')
info = d2.create_trigger('自检计时器', 13, 3, 27, 600, pieces=True, persistence=0)
sid = info['trigger_id']
rr = d2.trigger_actions(sid)
chk(len(rr) == 1 and rr[0]['type'] == 27, '新触发有 1 条行为 27', [x['type'] for x in rr])
p72, p164 = argpair(d2, rr[0]['action_off'])
chk(p72 == 0 and p164 == 600, '★首条行为 27： (+72=0, +164=600)', (p72, p164))

print('\n== G. 108（奖励箱）不退化 ==')
shutil.copy2(r'E:\YURI\SAVE2CD6.SAV', TMP + '.3')
d3 = SaveDoc(TMP + '.3')
vals = [a['arg'] for a in d3.trigger_actions('01000005')]
chk(len(vals) == 17 and sorted(vals) == sorted([0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]),
    '108 的 17 个参数仍是 0..4,6..17', vals)

for f in (TMP, TMP + '.2', TMP + '.3'):
    try:
        os.remove(f)
    except Exception:
        pass

print('\n§SOV19：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
