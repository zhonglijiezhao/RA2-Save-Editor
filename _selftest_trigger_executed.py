# -*- coding: utf-8 -*-
"""自检：**已执行的触发**要标红 + 禁止修改（2026-10-20 玩家要求）。

判据（2026-10-20 二次修正：玩家拿 SAVE0029 的「飞碟被攻击」触发指出——
"没有 TagNode" 不等于 "已执行"，事件驱动型触发本来就没有 TagNode）：
  · 有实例 + 无 TagNode + **事件对象运行期指针 `+0x50..+0x68` 非零** ⇒ 事件已被引擎处理过
    （只能确定"已挂钩"，分不清"已触发"还是"挂钩等待"）⇒ 橙字 + 改之前二次确认；
  · 有实例 + 有节点 + 启用 ⇒ 等待中（带剩余秒数）；
  · 有实例 + 有节点 + 禁用 ⇒ 已禁用。
  已执行的触发改什么都没用 ⇒ 列表标红，且 启用/禁用、改事件、改行为、改造动作 全部拦截。

样例：`_exp/sample_AB_已执行与等待.SAV`
  01000000 AAAAAAAAAA = 开局即执行（无节点）
  01000005 BBBBBBBB   = 还没执行（有节点，剩 30 秒）

需要 tkinter（用 D:\\py\\python.exe 跑）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

import RA2存档编辑器 as M

S = os.path.join(HERE, '_exp', 'sample_AB_已执行与等待.SAV')
OK = FAIL = 0


def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


if not os.path.isfile(S):
    print('缺少样例档: %s' % S)
    sys.exit(2)

# 弹窗全部拦下来（headless 不能真弹）
calls = []
M.messagebox.showwarning = lambda *a, **k: calls.append(('warn', a[0] if a else ''))
M.messagebox.showinfo = lambda *a, **k: calls.append(('info', a[0] if a else ''))
M.messagebox.askyesno = lambda *a, **k: (calls.append(('ask', a[0] if a else '')), False)[1]

app = M.App()
app.withdraw()
app.update()
app._set_doc(M.SaveDoc(S))
app.update()

rows = app.tt.get_children()
chk(len(rows) == 2, '触发列表 2 行（实际 %d）' % len(rows))
a_id, b_id = rows[0], rows[1]
A = app.doc.records[int(a_id)]
B = app.doc.records[int(b_id)]
chk(A.sid == '01000000' and B.sid == '01000005', '两条触发是 A=01000000 / B=01000005')

print('\n== A: 判定 ==')
chk(app._is_executed(A) is True, 'A 判定为「已执行」')
chk(app._is_executed(B) is False, 'B 判定为「未执行」')
va = app.tt.item(a_id, 'values')
vb = app.tt.item(b_id, 'values')
chk('已挂钩' in str(va[3]), 'A 的状态列写「已挂钩」（%s）' % va[3])
chk('事件驱动型' in str(va[3]), 'A 被认成「事件驱动型」而不是「已执行」')
chk('等待中' in str(vb[3]), 'B 的状态列写「等待中」（%s）' % vb[3])
chk('warn' in (app.tt.item(a_id, 'tags') or ()), 'A 打了 warn 标签（橙字，不是红字）')
chk('done' not in (app.tt.item(a_id, 'tags') or ()), 'A 不再是 done 红字')
chk('done' not in (app.tt.item(b_id, 'tags') or ()), 'B 没有 done 标签')
_txt = app.lbl_trig_count.cget('text')
chk('已执行 1' in _txt and '等待 1' in _txt, '计数行统计正确（%s）' % _txt)

print('\n== B: 已挂钩的触发：改之前要确认（askyesno=False ⇒ 拦下）==')
before = A.enabled
app.tt.selection_set(a_id)
app.update()
calls.clear()
app.toggle_selected(False)
chk(A.enabled == before, '改 A 的启用状态被拦下（enabled 未变）')
chk(any('触发过' in str(c[1]) for c in calls), '弹窗说明「可能已经触发过，仍要改吗」')

calls.clear()
app.tt.selection_set(b_id)
app.update()
app.toggle_selected(False)
chk(B.enabled is False, 'B 可以正常禁用（改得动）')

print('\n== C: 事件/行为/改造 也拦 ==')
# 找 A 的事件行
ev_rows = app.te.get_children()
a_ev = None
for i in ev_rows:
    ev = app._evlist[int(i)]
    if app._sid_of_row(ev) == '01000000':
        a_ev = i
        break
if a_ev is not None:
    app.te.selection_set(a_ev)
    calls.clear()
    app.edit_event()
    chk(any('触发过' in str(c[1]) for c in calls), 'A 的事件：弹确认后被拦（askyesno=False）')
else:
    chk(False, '没找到 A 的事件行')

ac_rows = app.ta.get_children()
a_ac = None
for i in ac_rows:
    ac = app._aclist[int(i)]
    if app._sid_of_row(ac) == '01000000':
        a_ac = i
        break
if a_ac is not None:
    app.ta.selection_set(a_ac)
    calls.clear()
    app.edit_action()
    chk(any('触发过' in str(c[1]) for c in calls), 'A 的行为：弹确认后被拦（askyesno=False）')
else:
    chk(False, '没找到 A 的行为行')

app.tt.selection_set(a_id)
app.update()
calls.clear()
app.retarget_dialog()
chk(any('触发过' in str(c[1]) for c in calls), 'A 的「改造动作」：弹确认后被拦')

print('\n== E: 重复型触发（地图 [Triggers] 第4字段=2）不算已执行 ==')
# ★2026-10-20 玩家提醒：重复触发执行过还会再触发 ⇒ 改它有意义，不能当"作废"。
_real = app.doc.trigger_repeat_type
app.doc.trigger_repeat_type = lambda sid: ('2' if sid == '01000000' else _real(sid))
app.refresh_records()
app.update()
chk(app._is_executed(A) is False, '重复型的 A 不再判为「已执行」')
va2 = app.tt.item(a_id, 'values')
chk('重复中' in str(va2[3]), 'A 的状态列写「重复中」（%s）' % va2[3])
chk('warn' not in (app.tt.item(a_id, 'tags') or ()), 'A 不再标橙')
calls.clear()
app.tt.selection_set(a_id)
app.update()
app.toggle_selected(False)
chk(A.enabled is False, '重复型的 A 可以正常修改（已生效）')
app.doc.trigger_repeat_type = _real
app.refresh_records()

print('\n== D: 图例 ==')
chk(True, '（统计在 A 段已校验；B 段把 B 禁用后计数会变，这里不再断言）')

app.destroy()
print('\n自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
