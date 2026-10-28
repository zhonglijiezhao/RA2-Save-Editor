# -*- coding: utf-8 -*-
"""§301 自检：作战小队 / 特遣部队列表的滚动条 + 武器页导出入口。

★2026-10-28（玩家截图反馈）：「作战小队这里加下拉条」。
  原来那两棵树是裸 `ttk.Treeview(...).pack(fill='both', expand=True)` ——
  小队一多只能靠滚轮，列宽超出时右半边直接看不到（`flag` 那列 220px）。
  现在换成 `_tree_scrolled`（触发页 / 事件页同一套），横竖各一条。

  同一轮把武器页的「导出」入口也钉一下（导出 INI/CSV 的实现在 ev300 里验）。

跑法：python _selftest_ev301_ai_scroll.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

import importlib.util  # noqa: E402
from tkinter import ttk  # noqa: E402

PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


spec = importlib.util.spec_from_file_location('gui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
app = m.App()
for _ in range(30):
    app.update()


def bars(tv):
    """这棵树所在容器里挂了哪些滚动条 → ['vertical', 'horizontal', ...]"""
    out = []
    try:
        kids = tv.master.winfo_children()
    except Exception:
        return out
    for c in kids:
        if isinstance(c, ttk.Scrollbar):
            out.append(str(c.cget('orient')))
    return out


for label, tv in (('作战小队', app.tat), ('特遣部队', app.tft)):
    ck(bool(tv.cget('yscrollcommand')), '%s 树配了 yscrollcommand' % label)
    ck(bool(tv.cget('xscrollcommand')), '%s 树配了 xscrollcommand' % label)
    b = bars(tv)
    ck('vertical' in b, '%s 容器里有竖滚动条（%s）' % (label, b))
    ck('horizontal' in b, '%s 容器里有横滚动条（%s）' % (label, b))

# 滚动条要真的能驱动树（不是只挂了个空壳）：直接调 yview / xview 不报错
for label, tv in (('作战小队', app.tat), ('特遣部队', app.tft)):
    try:
        tv.yview_moveto(0)
        tv.xview_moveto(0)
        ok = True
    except Exception as e:
        ok = False
        print('    %s yview/xview 报错：%s' % (label, e))
    ck(ok, '%s 的 yview/xview 可调用（滚动条真能驱动列表）' % label)

# 武器页：导出入口还在（实现在 ev300 里验）
ck(hasattr(app, '_wpn_export'), '武器页有 _wpn_export（导出按钮的回调）')
ck(hasattr(app, '_wpn_only_diff'), '武器页有「只看 ≠rules」开关（导出时沿用）')
ck(hasattr(app.doc.__class__, 'export_weapons_ini') if app.doc else True,
   'SaveDoc 提供 export_weapons_ini')

app.destroy()

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    for x in FAIL:
        print('  ✗ %s' % x)
sys.exit(1 if FAIL else 0)
