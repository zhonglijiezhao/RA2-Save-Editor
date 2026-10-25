# -*- coding: utf-8 -*-
"""§264 自检（core）：整组替换特遣成员 / 整段替换脚本动作 / 改 AI 三大件名字。

这三个 API 是给「双击表格直接编辑」用的（UI 层见 `_ui_ev264.py`）：
- `set_taskforce_units(doc, tf_id, units)`  ← 一次读档一次写完（循环 `set_taskforce_member` 太慢）
- `set_script_entries(doc, script_id, entries)`
- `set_ai_name(doc, kind, ident, name)`

锁住的点：定长（记录长度不变）、多余槽位清零、越界报错、型号不存在报错、落盘重读。
"""
import io
import os
import struct
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
import editor_core as ec                                     # noqa: E402
import ai_objects as aio                                     # noqa: E402
from _paths import find_sav                                  # noqa: E402

OK = FAIL = 0


def ck(cond, name, detail=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  PASS  %s' % name)
    else:
        FAIL += 1
        print('  FAIL  %s   %s' % (name, detail))


SAV = find_sav('SAVE5AF1.SAV')
print('存档 %s' % SAV)

# ---------- A. 特遣部队整组替换 ----------
print('\n[A] set_taskforce_units')
d = ec.SaveDoc(SAV)
tf = next(t for t in d.taskforces() if t.get('defined'))
TFID, TFOFF = tf['sid'], tf['off']
old_units = [(u['type'], u['count']) for u in tf['units']]
old_n = struct.unpack_from('<i', d.plain, TFOFF + 0xB0)[0]
print('    %s「%s」原成员 %s（种数 %d）' % (TFID, tf['name'], old_units, old_n))

total0 = len(d.plain)
r = aio.set_taskforce_units(d, TFID, [('V3', 2), ('DISK', 1)])
ck(len(d.plain) == total0, '★写完全档长度不变（定长字段）', len(d.plain) - total0)
ck(r['new_n'] == 2, '种数写成 2', r['new_n'])
ck(struct.unpack_from('<i', d.plain, TFOFF + 0xB0)[0] == 2, '+0xB0 = 2')

tf2 = next(t for t in d.taskforces() if t['sid'] == TFID)
ck([(u['type'], u['count']) for u in tf2['units']] == [('V3', 2), ('DISK', 1)],
   '重读成员正确', [(u['type'], u['count']) for u in tf2['units']])

# 多余槽位必须清零（数量 + 型号键都清）
tail_zero = all(struct.unpack_from('<i', d.plain, TFOFF + 0xB8 + 8 * i)[0] == 0 and
                struct.unpack_from('<I', d.plain, TFOFF + 0xB8 + 8 * i + 4)[0] == 0
                for i in range(2, 6))
ck(tail_zero, '★第 3~6 槽位（数量+型号键）都已清零')

# 数量 <= 0 的项被丢弃
r2 = aio.set_taskforce_units(d, TFID, [('V3', 0), ('DISK', 3), ('E1', 0)])
ck(r2['new_n'] == 1 and r2['units'] == [('DISK', 3)], '数量 0 的项被丢弃', r2['units'])

# 越界报错
for bad, msg in ((['a'] * 7, '超过 6 种'), (['NOSUCHTYPE'], '型号不存在')):
    try:
        if bad == ['NOSUCHTYPE']:
            aio.set_taskforce_units(d, TFID, [('NOSUCHTYPE', 1)])
        else:
            aio.set_taskforce_units(d, TFID, [('V3', 1)] * 7)
        ck(False, '应报错：%s' % msg)
    except Exception as e:
        ck(True, '应报错：%s（%s）' % (msg, type(e).__name__))

# ---------- B. 动作脚本整段替换 ----------
print('\n[B] set_script_entries')
sc = next(x for x in d.script_types() if x.get('defined'))
SCID, SCOFF = sc['sid'], sc['off']
old_cnt = struct.unpack_from('<i', d.plain, SCOFF + 0xB4)[0]
print('    %s「%s」原条目 %s（%d 条）'
      % (SCID, sc['name'], [(e['action'], e['arg']) for e in sc['entries']], old_cnt))

total0 = len(d.plain)
r = aio.set_script_entries(d, SCID, [(5, 10), (6, 1), (3, 98)])
ck(len(d.plain) == total0, '★写完全档长度不变（定长字段）')
ck(r['new_n'] == 3 and r['old_n'] == old_cnt, '条数 %d → 3' % old_cnt, r)
sc2 = next(x for x in d.script_types() if x['sid'] == SCID)
ck([(e['action'], e['arg']) for e in sc2['entries']] == [(5, 10), (6, 1), (3, 98)],
   '重读条目正确', [(e['action'], e['arg']) for e in sc2['entries']])

# 超出条数的槽位清零
tail_zero = all(struct.unpack_from('<ii', d.plain, SCOFF + 0xB8 + 8 * i) == (0, 0)
                for i in range(3, 50))
ck(tail_zero, '★第 4~50 槽位都已清零')

# 清空
aio.set_script_entries(d, SCID, [])
ck(len(next(x for x in d.script_types() if x['sid'] == SCID)['entries']) == 0,
   '可以清空（= 空脚本）')

# 越界报错
try:
    aio.set_script_entries(d, SCID, [(5, 0)] * 51)
    ck(False, '应报错：超过 50 条')
except Exception as e:
    ck(True, '应报错：超过 50 条（%s）' % type(e).__name__)
try:
    aio.set_script_entries(d, SCID, [(999, 0)])
    ck(False, '应报错：动作号越界')
except Exception as e:
    ck(True, '应报错：动作号越界（%s）' % type(e).__name__)

# ---------- C. 改名字（三类通用） ----------
print('\n[C] set_ai_name')
n1 = aio.set_ai_name(d, 'taskforce', TFID, 'TF改名测试')
ck(n1['new'] == 'TF改名测试', '特遣改名')
n2 = aio.set_ai_name(d, 'script', SCID, 'SC改名测试')
ck(n2['new'] == 'SC改名测试', '脚本改名')
team = next(t for t in d.team_types() if t.get('defined'))
n3 = aio.set_ai_name(d, 'team', team['sid'], 'TM改名测试')
ck(n3['new'] == 'TM改名测试', '小队改名')
ck(n3['old'] == team['name'], '返回了旧名字（%r）' % n3['old'])

# 名字超过 47 字节会被截断，但不会溢出到下一个字段
aio.set_ai_name(d, 'taskforce', TFID, 'X' * 100)
NL = aio.NAME_LEN          # = 0x28 = 40（不是 48，之前记错过一次）
raw = bytes(d.plain[TFOFF + 0x78:TFOFF + 0x78 + NL])
ck(raw.rstrip(b'\x00') == b'X' * (NL - 1),
   '超长名字截断到 %d 字节（NAME_LEN-1，不溢出）' % (NL - 1),
   len(raw.rstrip(b'\x00')))
# 名字字段后面紧跟 ID 字段，确认没被写坏
sid_after = bytes(d.plain[TFOFF + aio.ID_OFF:TFOFF + aio.ID_OFF + 8]).decode('latin1')
ck(sid_after == TFID, '★名字没溢出到后面的 ID 字段（仍是 %s）' % TFID, sid_after)

try:
    aio.set_ai_name(d, 'nosuchkind', TFID, 'x')
    ck(False, '应报错：kind 非法')
except Exception as e:
    ck(True, '应报错：kind 非法（%s）' % type(e).__name__)

# ---------- D. 落盘重读 ----------
print('\n[D] 落盘重读')
aio.set_taskforce_units(d, TFID, [('V3', 4), ('E1', 2)])
aio.set_script_entries(d, SCID, [(5, 7), (11, 0)])
aio.set_ai_name(d, 'taskforce', TFID, '落盘后的名字')
out = os.path.join(HERE, '_tmp_ev264.SAV')
d.save(out)
e = ec.SaveDoc(out)
tf3 = next(t for t in e.taskforces() if t['sid'] == TFID)
ck([(u['type'], u['count']) for u in tf3['units']] == [('V3', 4), ('E1', 2)],
   '特遣成员落盘后正确', [(u['type'], u['count']) for u in tf3['units']])
ck(tf3['name'] == '落盘后的名字', '特遣名字落盘后正确', tf3['name'])
sc3 = next(x for x in e.script_types() if x['sid'] == SCID)
ck([(x['action'], x['arg']) for x in sc3['entries']] == [(5, 7), (11, 0)],
   '脚本站目落盘后正确', [(x['action'], x['arg']) for x in sc3['entries']])
try:
    os.remove(out)
except OSError:
    pass

print('\n' + '-' * 66)
print('§264 核心自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
