# -*- coding: utf-8 -*-
"""§SOV04 回归：表格列宽自适应 + 横向滚动条**真的能拖**。

玩家报（2026-11-03）：「触发的文字显示不全，下方的拖动条也不能调整」。

根因不是滚动条坏了，而是：
  ① 列宽写死且**总和小于可视宽度** ⇒ 滚动条滑块占满整条，拖不动（没内容可滚）；
  ② `ttk.Treeview` 的列默认 `stretch=True` ⇒ 引擎会把所有列**按比例压到正好填满控件**，
     所以"总列宽永远等于可视宽度"，永远不会溢出 ⇒ 滚动条永远无效，列还被压窄裁字。
  ③ 单元格文本被**列宽**裁切 —— 拖滚动条也没用（那是列内裁切）。

修法：`ui/tree_fit.py`
  · `autosize()` 按实测像素宽设列宽，并 `stretch=False`（关键）；
  · 超宽时先压"可让"的长文本列到下限，仍超才横向滚动；
  · `attach_tooltip()` 鼠标停在被裁的单元格上弹全文。

本自检在**独立窗口**里跑（不依赖存档）：造一张 8 列、内容很长的表，验证
  ① 列宽合计 > 可视宽度  ② xview 能滚动（右端不是 1.0）  ③ 列 stretch 已关闭。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

OK = FAIL = 0


def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s' % msg)


def main():
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        print('SKIP: 当前 python 没有 tkinter（用 D:/py/python.exe 跑）')
        return 0
    from ui import tree_fit

    root = tk.Tk()
    root.geometry('640x260')
    try:
        root.attributes('-alpha', 0.0)
    except Exception:
        pass
    cols = ('id', 'name', 'kind', 'state', 'rep', 'tags', 'mapinfo', 'note')
    tv = ttk.Treeview(root, columns=cols, show='headings')
    for c, t in zip(cols, ('触发ID', '名称', '类别', '状态', '触发类型', '标签',
                           '地图（条件/行为）', '说明')):
        tv.heading(c, text=t)
        tv.column(c, width=80, anchor='w')
    hsb = ttk.Scrollbar(root, orient='horizontal', command=tv.xview)
    tv.configure(xscrollcommand=hsb.set)
    hsb.pack(side='bottom', fill='x')
    tv.pack(fill='both', expand=True)
    rows = [
        ('096E2DDC', 'Player_Has_Plant8', '触发', '启用｜等待中 剩5秒',
         '（共用）一次性·任一挂载', '1', '条件 2 · 行为 1',
         '启用位@+123 = 1 · 共用「Player_Has_Plant8」的标签'),
        ('01000001', 'AI Auto Production 美国(American)', '触发', '启用｜重复·已禁用',
         '可重复', '1', '条件 1 · 行为 2',
         '启用位@+123 = 0 · 本档只有「触发定义」、未实例化（引擎不执行·只读）'),
    ]
    for r in rows:
        tv.insert('', 'end', values=r)
    root.update()
    avail = tv.winfo_width()
    print('表格可视宽度 = %d px' % avail)

    want = tree_fit.autosize(tv)
    root.update()
    total = sum(want.values())
    print('自适应列宽 = %s（合计 %d）' % (want, total))
    chk(total > avail, '列宽合计 %d > 可视 %d（有内容可滚）' % (total, avail))
    chk(all(tv.column(c, 'stretch') == 0 for c in cols),
        '所有列 stretch=False（否则 ttk 会把列压回去，滚动条又失效）')
    x0, x1 = tv.xview()
    print('xview = (%.3f, %.3f)' % (x0, x1))
    chk(x1 < 0.999, '横向滚动条有滚动范围（右端 %.3f < 1.0）' % x1)
    tv.xview_moveto(1.0)
    root.update()
    y0, y1 = tv.xview()
    print('拖到最右后 xview = (%.3f, %.3f)' % (y0, y1))
    chk(y1 >= 0.999 and y0 > 0.0, '滚动条真的能拖动（左端 %.3f > 0）' % y0)
    root.destroy()
    print('\n§SOV04 回归：PASS %d / FAIL %d' % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
