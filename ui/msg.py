# -*- coding: utf-8 -*-
"""对话框的统一出口（★2026-10-28，P2-8 拆页后的必要补丁）。

问题
----
拆页之前，测试普遍这么干：

    m.messagebox.showwarning = lambda t, s, **k: said['warn'].append((t, s))

也就是"把主程序模块里的 messagebox 打个补丁"，让弹窗变成记录。
拆页之后每个页面各自 `from tkinter import messagebox`，拿到的是**另一个名字绑定** ⇒
补丁只对主程序那份代码生效，页面里的弹窗照样真弹出来：测试卡在模态框上直到超时。
（`_ui_ev217` / `_ui_ev218` 就是这么挂的，全量跑一遍能看出来。）

做法
----
所有模块（主程序 + 每个页面）都 `from ui.msg import messagebox`，
拿到的是**同一个代理对象**；测试给它塞属性 → 全部模块一起生效。
没打补丁时 `__getattr__` 原样转发给 `tkinter.messagebox`，生产行为一个字节都不变。
"""
from tkinter import filedialog as _filedialog_real
from tkinter import messagebox as _messagebox_real
from tkinter import simpledialog as _simpledialog_real


class _Proxy(object):
    """透明转发；被外部塞过属性（打补丁）时优先用塞进来的那个。"""

    def __init__(self, real):
        object.__setattr__(self, '_real', real)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_real'), name)


messagebox = _Proxy(_messagebox_real)
filedialog = _Proxy(_filedialog_real)
simpledialog = _Proxy(_simpledialog_real)
