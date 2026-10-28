# -*- coding: utf-8 -*-
"""所属方页（原 `App.houses`）

★2026-10-28（审查 P2-8）：`App` 原来 8400 行、222 个方法，是典型的 God Class。
  这里按"页"切开，**方法体一行没改** —— 用 mixin 而不是把 `self` 换成 `self.app`，
  所以 `self.xxx` 照旧指向同一个 App 实例，行为零变化。
  边界 = 原来 `_tab_xxx()` 那整段（含它引用的私有辅助方法）。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, 'core')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tkinter import ttk
import ui_theme


class HousesPage(object):
    def _tab_houses(self, nb):
        f = ttk.Frame(nb, padding=8)
        _ph = ui_theme.page_header(f, '所属方金钱', '每个所属方的金钱；双击行可改')
        _ph.pack(fill='x', padx=2, pady=(2, 8))
        cols = ('cn', 'short', 'money', 'note')
        self.th = ttk.Treeview(f, columns=cols, show='headings', height=14)
        for c, t, w in (('cn', '阵营', 90), ('short', '标识', 130),
                        ('money', '金钱', 120), ('note', '说明', 260)):
            self.th.heading(c, text=t)
            self.th.column(c, width=w, anchor='w')
        self.th.pack(fill='both', expand=True)
        self.th.bind('<Double-1>', lambda e: self.edit_house())
        self.th.bind('<Return>', lambda e: self.edit_house())

        row = ttk.Frame(f)
        row.pack(fill='x', pady=(8, 0))
        ttk.Button(row, text='修改选中', command=self.edit_house).pack(side='left')
        ttk.Button(row, text='全部设为…', command=self.set_all_houses).pack(side='left', padx=6)

        box = ttk.Labelframe(f, text='快捷增减（对选中的所属方）', padding=8)
        box.pack(fill='x', pady=(8, 0))
        ttk.Label(box, text='数值').pack(side='left')
        self.ent_delta = ttk.Entry(box, width=12)
        self.ent_delta.insert(0, '10000')
        self.ent_delta.pack(side='left', padx=6)
        ttk.Button(box, text=' + ', width=4,
                   command=lambda: self.bump_house(1)).pack(side='left')
        ttk.Button(box, text=' − ', width=4,
                   command=lambda: self.bump_house(-1)).pack(side='left', padx=4)
        ttk.Label(box, text='可按住 Ctrl 多选；玩家上限 16,777,215，自定义任务 AI 方上限更大（u32）',
                  foreground=ui_theme.T['muted']).pack(side='left', padx=8)

        ttk.Label(f, text='说明：每个所属方的金钱存在「经济结构」里 —— 60 字节固定模板 → 阵营位掩码(=1<<注册序) → 3 字节金额，'
                          '其后 +304 字节还有一份副本，写入时两份一起改。当前玩家另写它自己的镜像字段与主字段。'
                          '定位方法已用「独特金额 + 重开新档」实测验证（值与该阵营在地图里的 Credits×100 逐一吻合）。',
                  foreground=ui_theme.T['muted'], wraplength=880, justify='left').pack(anchor='w', pady=(8, 0))
        return f
