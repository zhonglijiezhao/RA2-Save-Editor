# -*- coding: utf-8 -*-
"""Treeview 列宽自适应 + 单元格悬浮看全文（§SOV04）。

玩家报（2026-11-03）：「触发的文字显示不全，下方的拖动条也不能调整」。

**滚动条没坏**，问题在列宽写死（70/142/44/122/88/44/104/118 = 732 px）：
表格可视宽度比它大 ⇒ 横向滚动条**没有可滚的东西**（滑块占满整条，拖不动），
而单元格里的长文本（「说明」列会写「启用位@+123 = 1 · 共用「…」的标签」）
被**列宽**切掉了 —— 拖滚动条永远看不到，因为那是"列内裁切"，不是"内容超宽"。

两个配套动作：
  · `autosize(tv)`  —— 按**实测像素宽**重算每列（每列只量最长那个值，最多量 sample 行），
    带上限；总宽不足可视宽度时把余量按权重分给长文本列（名字/说明/状态），
    超过可视宽度时横向滚动条就真的能拖了。
  · `attach_tooltip(tv)` —— 鼠标停在**被裁切**的单元格上，弹小窗显示全文。
"""
# ★2026-11-06（审核 §3.3）：静默异常统一记账（绝不抛、默认只记内存）。
#   本文件里原来的 `except ...: pass` 已换成 `_swallow()` —— 行为完全不变，
#   但被吞掉的东西会进 diag 缓冲区，随错误日志/退出时落盘，排障不再靠猜。
try:
    from diag import swallow as _swallow
except Exception:                    # diag 不可达（极端情况）⇒ 退化成原来的 pass
    try:
        from core.diag import swallow as _swallow
    except Exception:
        def _swallow(*a, **k):
            pass
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# 列宽上限（像素）——别让一条超长说明把整行撑到屏幕外
DEFAULT_CAPS = {'id': 100, 'name': 230, 'kind': 60, 'state': 190, 'rep': 175,
                'tags': 64, 'mapinfo': 160, 'note': 430,
                'idx': 46, 'type': 66, 'arg': 80, 'argtext': 260, 'owner': 130,
                'tag': 120, 'name2': 230}


def _row_font(tv):
    """取 Treeview 正文实际用的字体（量宽度必须用同一个字体）。"""
    try:
        f = tv.cget('font')
        if f:
            return tkfont.Font(font=f)
    except Exception:
        _swallow()
    try:
        fs = ttk.Style().lookup('Treeview', 'font')
        if fs:
            return tkfont.Font(font=fs)
    except Exception:
        _swallow()
    try:
        return tkfont.nametofont('TkDefaultFont')
    except Exception:
        return None


def autosize(tv, caps=None, minw=46, pad=26, sample=400, weights=None, stretch=False):
    """按内容重算列宽（像素）。返回 {列: 宽度}。

    `weights` = 总宽不足可视宽度时，余量怎么分（默认给 名字/说明/状态/类型）。
    `stretch` = 列是否允许 ttk 自动压缩/拉伸。★★必须给 **False**：
      ttk.Treeview 在 `stretch=True`（默认）时会把列**按比例压到正好填满控件**，
      于是"总列宽永远等于可视宽度" ⇒ 横向滚动条永远没东西可滚（滑块占满整条、拖不动），
      被压窄的列又把文本裁掉 —— 这正是玩家报的"文字不全、拖动条不能调"。
    """
    try:
        cols = list(tv['columns'])
    except Exception:
        return {}
    if not cols:
        return {}
    cap = dict(DEFAULT_CAPS)
    cap.update(caps or {})
    f = _row_font(tv)
    head = {}
    for c in cols:
        try:
            head[c] = str(tv.heading(c, 'text') or '')
        except Exception:
            head[c] = ''
    longest = dict((c, '') for c in cols)
    try:
        ids = tv.get_children()
    except Exception:
        ids = ()
    for iid in ids[:sample]:
        try:
            vals = tv.item(iid, 'values')
        except Exception:
            continue
        for k, c in enumerate(cols):
            if k < len(vals):
                s = str(vals[k])
                if len(s) > len(longest[c]):
                    longest[c] = s

    def _w(text):
        if not text:
            return 0
        if f is not None:
            try:
                return f.measure(text)
            except Exception:
                _swallow()
        return len(text) * 8

    want = {}
    for c in cols:
        w = max(_w(head[c]) + 24, _w(longest[c]) + pad, minw)
        want[c] = min(int(w), cap.get(c, 430))
    try:
        tv.update_idletasks()
        avail = tv.winfo_width()
    except Exception:
        avail = 0
    total = sum(want.values())
    if avail > 60 and total < avail:
        wts = weights or {'name': 3, 'note': 4, 'state': 2, 'rep': 2, 'mapinfo': 1,
                          'argtext': 4, 'owner': 1}
        wts = dict((k, v) for k, v in wts.items() if k in want)
        extra = int(avail - total) - 4
        if wts and extra > 0:
            s = float(sum(wts.values()))
            for c, wt in wts.items():
                want[c] += int(extra * wt / s)
    elif avail > 60 and total > avail:
        # 超出可视宽度 ⇒ 先压缩"能让"的长文本列（说明→名字→参数含义…）到下限，
        # 尽量一屏放下；压到底还超，才让横向滚动条真滚动（内容仍能被悬浮提示看到）。
        order = ('note', 'name', 'argtext', 'state', 'rep', 'mapinfo')
        floor = {'note': 200, 'name': 140, 'argtext': 160, 'state': 110,
                 'rep': 120, 'mapinfo': 110}
        over = total - avail
        for c in order:
            if over <= 0:
                break
            if c not in want:
                continue
            can = max(0, want[c] - floor.get(c, 120))
            cut = min(can, over)
            if cut > 0:
                want[c] -= cut
                over -= cut
    for c in cols:
        try:
            tv.column(c, width=want[c], minwidth=min(want[c], 46),
                      stretch=(True if stretch is None else bool(stretch)))
        except Exception:
            _swallow()
    return want


class CellTip(object):
    """鼠标停在被裁切的单元格上 ⇒ 弹一个小窗显示全文（不遮挡、不失焦）。"""

    def __init__(self, tv, delay=380):
        self.tv = tv
        self.delay = delay
        self._job = None
        self._win = None
        self._key = None
        tv.bind('<Motion>', self._on_motion, add='+')
        tv.bind('<Leave>', lambda e: self.hide(), add='+')
        tv.bind('<ButtonPress>', lambda e: self.hide(), add='+')
        tv.bind('<MouseWheel>', lambda e: self.hide(), add='+')

    # ---- 内部 ----
    def hide(self):
        if self._job is not None:
            try:
                self.tv.after_cancel(self._job)
            except Exception:
                _swallow()
            self._job = None
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                _swallow()
            self._win = None
        self._key = None

    def _on_motion(self, ev):
        tv = self.tv
        try:
            row = tv.identify_row(ev.y)
            colid = tv.identify_column(ev.x)
        except Exception:
            self.hide()
            return
        if not row or not colid:
            self.hide()
            return
        try:
            cols = list(tv['columns'])
            ci = int(colid.replace('#', '')) - 1
            if not (0 <= ci < len(cols)):
                self.hide()
                return
            name = cols[ci]
            text = str(tv.set(row, name))
            width = int(tv.column(name, 'width'))
        except Exception:
            self.hide()
            return
        f = _row_font(tv)
        try:
            need = f.measure(text) if f is not None else len(text) * 8
        except Exception:
            need = len(text) * 8
        if need + 12 <= width or not text.strip():
            self.hide()
            return
        key = (row, name)
        if key == self._key and self._win is not None:
            return
        self.hide()
        self._key = key
        self._job = tv.after(self.delay, lambda: self._popup(ev.x_root, ev.y_root, text))

    def _popup(self, x, y, text):
        self._job = None
        try:
            win = tk.Toplevel(self.tv)
            win.wm_overrideredirect(True)
            win.attributes('-topmost', True)
            lbl = tk.Label(win, text=text, justify='left', bg='#ffffe1',
                           relief='solid', borderwidth=1, wraplength=520, padx=6, pady=4)
            lbl.pack()
            win.update_idletasks()
            w = win.winfo_width()
            sw = win.winfo_screenwidth()
            if x + w + 8 > sw:
                x = max(0, sw - w - 8)
            win.wm_geometry('+%d+%d' % (x + 12, y + 18))
            self._win = win
        except Exception:
            self._win = None


def attach_tooltip(tv, delay=380):
    """给 Treeview 挂一个 CellTip（重复调用返回同一个，不重复绑定）。"""
    tip = getattr(tv, '_cell_tip', None)
    if tip is None:
        tip = CellTip(tv, delay=delay)
        try:
            tv._cell_tip = tip
        except Exception:
            _swallow()
    return tip


def fit_on_resize(tv, caps=None, delay=120):
    """控件尺寸一变就（防抖后）重算列宽 —— 首次填表时控件往往还没映射，
    量出来的可用宽度是 1，必须等真正显示出来再算一次。"""
    def _later(_e=None):
        job = getattr(tv, '_fit_job', None)
        if job is not None:
            try:
                tv.after_cancel(job)
            except Exception:
                _swallow()
        try:
            tv._fit_job = tv.after(delay, lambda: autosize(tv, caps=caps))
        except Exception:
            tv._fit_job = None
    try:
        tv.bind('<Configure>', _later, add='+')
    except Exception:
        _swallow()
