# -*- coding: utf-8 -*-
"""自检：局部变量型参数（ptype=3）的下拉与含义翻译（2026-11-07 §SOV32）

玩家报的问题：新建局部变量（V1）之后，触发编辑器里选「56 设置局部变量」，
**参数下拉表是空的** —— 只能盲填数字。

根因：`ui/trigger_editor.py::_fill_arg_values()` 没有 ptype==3 这一支，
落到最后的 `else: combo['values'] = []`。地编 ptype=3 = 局部变量型，
行为 56/57（设置·清除局部变量）、事件 36/37（局部变量被设置·清除）都是它。

断言（headless，不开窗口）：
  A local_var_table() 读得出 V1（engine index 0）
  B _var_choices() 非空、含 "0 V1"
  C _fill_arg_values(combo, 3, 0) 把候选填进下拉，且显示 "0 V1"
  D _arg_text(3, 0) 翻成 "0 V1"（参数含义列不再是光秃秃的数字）
  E 未定义的号也能翻成 "N（未定义）"
"""
import io
import os
import sys
import types

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc
from ui.trigger_editor import TriggerEditor

SAV = next((p for p in (r'E:\YURI\ALL01UMD.sav', r'E:\YURI\改.SAV')
            if os.path.exists(p)), None)
OK = FAIL = 0


def chk(c, msg):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


class Stub:
    """只给 _var_choices/_var_label/_fill_arg_values 提供 self.doc 的壳。"""

    def __init__(self, doc):
        self.doc = doc



class ComboStub:
    def __init__(self):
        self.values = []
        self.text = ''

    def __setitem__(self, k, v):
        if k == 'values':
            self.values = list(v)

    def set(self, t):
        self.text = t


if not SAV:
    print('跳过：找不到存档样本')
    sys.exit(0)

print('存档: %s' % SAV)
d = SaveDoc(SAV)
st = Stub(d)
st.doc = d
# 绑定：Stub 实例上的方法要能拿到 self（MethodType 已绑到类，实例调用自动传 self）
for _n in ('_var_choices', '_var_label', '_fill_arg_values'):
    setattr(st, _n, types.MethodType(getattr(TriggerEditor, _n), st))

print('\n== A. 局部变量表 ==')
rows = d.local_var_table()
named = [r for r in rows if (r.get('name') or r.get('name_map'))]
print('  共 %d 槽，已命名 %d 条；前 3 条：%s'
      % (len(rows), len(named),
         [(r['index'], r['name'] or r['name_map']) for r in rows[:3]]))
chk(len(rows) > 0, 'local_var_table() 有数据（%d 槽）' % len(rows))

if not named:
    # ★玩家截图里的场景：先在「局部变量」窗口新建 V1（只写存档、不写地图），再选行为 56
    print('  （本档没有命名变量 → 模拟玩家的"新建局部变量"：写 V1 到引擎索引 0）')
    d.define_local_var(0, 'V1', 0)
    rows = d.local_var_table()
    named = [r for r in rows if (r.get('name') or r.get('name_map'))]
    print('  新建后：%s' % [(r['index'], r['name']) for r in named])

chk(bool(named), '至少一条局部变量有名字（%d 条）' % len(named))

print('\n== B. _var_choices() 候选 ==')
ch = st._var_choices()
print('  候选 %d 条，前 3 条：%s' % (len(ch), [c[0] for c in ch[:3]]))
chk(len(ch) == 99, '候选 = 99 条（地编口径）')
chk(bool(ch) and ch[0][1] == 0, '第一条的引擎索引是 0')
if named:
    r0 = named[0]
    want = '%d %s' % (r0['index'], (r0['name'] or r0['name_map']).strip())
    chk(any(lab.startswith(want) for lab, _i in ch), '候选里含 "%s"' % want)

print('\n== C. 参数下拉真的被填满 ==')
cb = ComboStub()
st._fill_arg_values(cb, 3, 0)
print('  combo.values = %d 条，显示 = %r' % (len(cb.values), cb.text))
chk(len(cb.values) > 0, '_fill_arg_values(3, 0) 后下拉非空（修复前是 0 条）')
chk(cb.text.startswith('0'), '当前值 0 显示成 "%s"' % cb.text)

print('\n== D/E. 参数含义列翻译 ==')
t0 = d._arg_text(3, 0)
t9 = d._arg_text(3, 9)
print('  _arg_text(3, 0) = %r' % t0)
print('  _arg_text(3, 9) = %r' % t9)
chk(t0.startswith('0 V1'), '0 号翻成 %r（不再是纯数字）' % t0)
chk('未定义' in t9 or t9 != '9', '9 号翻成 %r' % t9)

print('\n== F. 事件 36/37 也走同一支（ptype=3）==')
from fa2_tables import EVENT_TYPES, ACTION_TYPES
chk(EVENT_TYPES[36][1] == 3 and EVENT_TYPES[37][1] == 3, '事件 36/37 的 ptype = 3')
chk(ACTION_TYPES[56][1] == 3 and ACTION_TYPES[57][1] == 3, '行为 56/57 的 ptype = 3')
cb2 = ComboStub()
st._fill_arg_values(cb2, 3, 1)
chk(len(cb2.values) > 0, '事件参数下拉同样非空（%d 条）' % len(cb2.values))

print('\n== G. 写入闭环：下拉选中的号真写进 +164，存盘再读回 ==')
import shutil                                                          # noqa: E402
_tmp = os.path.join(HERE, '_tmp_localvar')
os.makedirs(_tmp, exist_ok=True)
_tp = os.path.join(_tmp, 'selftest_lv.SAV')
shutil.copy2(SAV, _tp)
d2 = SaveDoc(_tp)
d2.define_local_var(0, 'V1', 0)
d2.define_local_var(1, 'V2', 0)
_sid = [r for r in d2.records if r.kind == '触发'][0].sid
d2.add_action(_sid, 56, 0)
_ac = [a for a in d2.trigger_actions(_sid) if a['type'] == 56][-1]
d2.set_action(_ac, arg=1)          # = 下拉里选 "1 V2"
_v = [a for a in d2.trigger_actions(_sid) if a['type'] == 56][-1]['arg']
chk(_v == 1, '改完读回参数 = %r（写进 +164，不是 +72）' % _v)
chk('V2' in d2._arg_text(3, _v), '含义列翻成 %r' % d2._arg_text(3, _v))
d2.save(_tp, backup=False)
d3 = SaveDoc(_tp)
_v2 = [a for a in d3.trigger_actions(_sid) if a['type'] == 56][-1]['arg']
chk(_v2 == 1, '存盘重开后参数仍是 %r' % _v2)

print('\n合计 PASS=%d FAIL=%d' % (OK, FAIL))
sys.exit(0 if FAIL == 0 else 1)
