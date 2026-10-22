# -*- coding: utf-8 -*-
"""自检：触发页选中 → 事件/行为页对应行**高亮**（2026-10-20 玩家要求）。

判据（不依赖地图、不依赖运行期组件）：
  ① 选中一条触发，它的事件/行为行要被打上 `hit` 标签（浅琥珀底色）；
  ② 换一条触发，高亮要跟着换；
  ③ 取消选中，高亮要清掉；
  ④ 事件页搜索框把这些行藏起来时，选中触发要先清空过滤再把高亮打回去；
  ⑤ 取消选中后 `_ev_hits/_ac_hits` 为空（不会留残影）。

需要一个 GUI 环境（tkinter）。样例档用工程自带的 `_exp/sample_trig2.SAV`
（两条触发 + 各一条事件/行为，运行期链完整）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

import RA2存档编辑器 as M

S = os.path.join(HERE, '_exp', 'sample_trig2.SAV')
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

app = M.App()
app.withdraw()
app.update()
app._set_doc(M.SaveDoc(S))
app.update()

rows = app.tt.get_children()
chk(len(rows) >= 2, '触发页至少 2 行（实际 %d）' % len(rows))

print('\n== A: 选中触发 → 事件/行为高亮 ==')
app.tt.selection_set(rows[0])
app._remember_trigger_sel()
app.update()
ev_tags = {i: app.te.item(i, 'tags') for i in app.te.get_children()}
ac_tags = {i: app.ta.item(i, 'tags') for i in app.ta.get_children()}
chk(any('hit' in (t or ()) for t in ev_tags.values()), '有事件行被高亮（%s）' % ev_tags)
chk(any('hit' in (t or ()) for t in ac_tags.values()), '有行为行被高亮（%s）' % ac_tags)
chk(len(app._ev_hits) >= 1 and len(app._ac_hits) >= 1,
    '命中集合非空（事件 %s / 行为 %s）' % (app._ev_hits, app._ac_hits))

print('\n== B: 换一条触发，高亮跟着换 ==')
hits_a = set(app._ev_hits)
app.tt.selection_set(rows[1])
app._remember_trigger_sel()
app.update()
chk(set(app._ev_hits) != hits_a or len(rows) == 1,
    '命中集合随选中触发改变（%s → %s）' % (hits_a, app._ev_hits))
ev_tags2 = {i: app.te.item(i, 'tags') for i in app.te.get_children()}
chk(any('hit' in (t or ()) for t in ev_tags2.values()), '新触发的事件同样高亮')

print('\n== C: 取消选中 → 高亮清掉 ==')
app.tt.selection_remove(*app.tt.selection())
app._remember_trigger_sel()
app.update()
chk(not app._ev_hits and not app._ac_hits, '命中集合已清空')
ev_tags3 = {i: app.te.item(i, 'tags') for i in app.te.get_children()}
chk(not any('hit' in (t or ()) for t in ev_tags3.values()), '事件行不再带 hit 标签')

print('\n== D: 搜索过滤挡住目标行时，选中触发要能清空过滤并高亮 ==')
app.tt.selection_set(rows[0])
app._remember_trigger_sel()
app.update()
if len(app.te.get_children()) > 1:
    app._q_ev.set('zzz_不可能匹配_zzz')
    app._fill_ev_tree()
    app.update()
    chk(len(app.te.get_children()) == 0, '搜索后事件表为空')
    app.tt.selection_set(rows[0])
    app._remember_trigger_sel()
    app.update()
    chk(len(app.te.get_children()) > 0, '选中触发后过滤被清空（重新有行）')
    chk(any('hit' in (app.te.item(i, 'tags') or ()) for i in app.te.get_children()),
        '清空过滤后高亮仍在')
else:
    chk(True, '事件只有 1 条，跳过过滤用例')

app.destroy()
print('\n自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
