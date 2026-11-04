# -*- coding: utf-8 -*-
"""§SOV16 自检：`108 创建工具箱` 的参数（奖励箱类型）在 **+164**，不是 +72。

样本：E:\\YURI\\SAVE2CD6.SAV 的 01000005 —— 玩家在地编里把 0..17 全填了一遍（缺 5）。

覆盖：
  1. 17 条 108 行为，参数读成 0,1,2,3,4,6..17（+72 恒 0、+164 才是值）
  2. `arg_text` 翻成中文名（本钱全移回己方 / 随机单位 / … / 一片矿）
  3. `set_action(arg=…)` 写的是 +164：+72 不动、长度不变、改回逐字节复原
  4. `add_action(108, 9)` 新建的行为同样把参数放在 +164
  5. 56/57（局部变量）不受影响（仍走 +164）
  6. `crate_name` 未知值兜底
跑：python _selftest_action_108_crate.py
"""
import hashlib
import os
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))
from editor_core import SaveDoc, ACT_VAR_OFF, OBJ_ARG_OFF      # noqa: E402
from fa2_tables import crate_name                              # noqa: E402

SRC = r'E:\YURI\SAVE2CD6.SAV'
OK = FAIL = 0


def chk(cond, msg, extra=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s%s' % (msg, '' if extra is None else ('  → %r' % (extra,))))


d = SaveDoc(SRC)
acts = d.trigger_actions('01000005')
print('01000005 行为 %d 条' % len(acts))

print('== A. 参数读到的是 +164 ==')
args = [a['arg'] for a in acts]
chk(args == [0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17],
    '★参数 = %s' % args, args)
p72 = [struct.unpack_from('<I', d.plain, a['action_off'] + OBJ_ARG_OFF)[0] for a in acts]
chk(set(p72) == {0}, '同批行为的 +72 全是 0（旧实现就是被它坑了）', set(p72))
p164 = [struct.unpack_from('<I', d.plain, a['action_off'] + ACT_VAR_OFF)[0] for a in acts]
chk(p164 == args, '+164 与读出的参数完全一致', p164)

print('== B. 参数含义翻成中文 ==')
det = d.trigger_detail('01000005')
texts = [a.get('arg_text') for a in (det.get('actions') or [])]
chk(texts[0] == '本钱全移回己方', '0 → 本钱全移回己方', texts[0])
chk(texts[1] == '随机单位', '1 → 随机单位', texts[1])
_by_arg = {a['arg']: a.get('arg_text') for a in (det.get('actions') or [])}
chk(_by_arg.get(12) == '一次核弹', '12 → 一次核弹', _by_arg.get(12))
chk(_by_arg.get(13) == '（空）', '13 → （空）', _by_arg.get(13))
chk(texts[-1] == '一片矿', '17 → 一片矿', texts[-1])
chk(crate_name(99) == '类型#99', '未知值兜底：%s' % crate_name(99))

print('== C. set_action 写 +164 ==')
a0 = acts[0]
o = a0['action_off']
n_bytes = len(d.plain)
md5_0 = hashlib.md5(bytes(d.plain)).hexdigest()
info = d.set_action(a0, arg=9)
chk(struct.unpack_from('<I', d.plain, o + ACT_VAR_OFF)[0] == 9,
    '★+164 变成 9', struct.unpack_from('<I', d.plain, o + ACT_VAR_OFF)[0])
chk(struct.unpack_from('<I', d.plain, o + OBJ_ARG_OFF)[0] == 0,
    '+72 仍然是 0（没写错地方）')
chk(len(d.plain) == n_bytes, '存档长度不变', (n_bytes, len(d.plain)))
d.set_action(a0, arg=0)
chk(hashlib.md5(bytes(d.plain)).hexdigest() == md5_0, '★改回 0 ⇒ 整档 md5 复原')

print('== D. add_action(108, 9) 也写 +164 ==')
tmp = os.path.join(tempfile.gettempdir(), '_sov16_add.SAV')
shutil.copy2(SRC, tmp)
d2 = SaveDoc(tmp)
n0 = len(d2.trigger_actions('01000005'))
try:
    d2.add_action('01000005', 108, 9)
    acts2 = d2.trigger_actions('01000005')
    chk(len(acts2) == n0 + 1, '行为数 %d → %d' % (n0, len(acts2)))
    new = [a for a in acts2 if a['arg'] == 9]
    chk(len(new) >= 1, '★新行为参数 = 9（写在 +164）',
        [(a['type'], a['arg']) for a in acts2[-3:]])
    if new:
        oo = new[-1]['action_off']
        chk(struct.unpack_from('<I', d2.plain, oo + OBJ_ARG_OFF)[0] == 0,
            '新行为的 +72 也是 0')
        chk(struct.unpack_from('<I', d2.plain, oo + ACT_VAR_OFF)[0] == 9,
            '新行为的 +164 = 9')
except Exception as e:
    chk(False, 'add_action 抛异常：%r' % (e,))
finally:
    try:
        os.remove(tmp)
    except Exception:
        pass

print('== E. 56/57（局部变量）不受影响 ==')
hit = None
for r in d.records:
    if r.kind != '触发':
        continue
    for a in d.trigger_actions(r.sid):
        if a['type'] in (56, 57):
            hit = a
            break
    if hit:
        break
if hit:
    v164 = struct.unpack_from('<I', d.plain, hit['action_off'] + ACT_VAR_OFF)[0]
    chk(hit['arg'] == v164, '56/57 的 arg 仍来自 +164（%d）' % v164, (hit['arg'], v164))
else:
    print('  （本档没有 56/57 行为，跳过）')

print('\n§SOV16：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
