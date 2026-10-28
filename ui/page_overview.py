# -*- coding: utf-8 -*-
"""总览页（原 `App.overview`）

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
import tkinter as tk
import ui_theme


class OverviewPage(object):
    def _tab_overview(self, nb):
        T = ui_theme.T
        f = tk.Frame(nb, bg=T['canvas'])
        ui_theme.page_header(f, '概览', '存档头信息与金钱').pack(fill='x', padx=2, pady=(2, 10))

        # ── ① 存档信息（整宽，双栏 6 行；高度随内容）──
        card, body = ui_theme.card(f, '存档信息')
        card.pack(fill='x')
        for c in (1, 3):
            body.columnconfigure(c, weight=1, uniform='v')

        self.info_labels = {}
        # ★§276：「任务名」读存档头部固定字段（0x1360 → 剥掉「军事行动：」前缀），
        #   「任务副标题」= 0x13DA（官方战役是「國家：章節」，自制图常是地图名）。
        keys = ['文件', '文件大小', '明文大小', '块数', '地图尺寸', '名字键',
                '任务名', '任务副标题', '玩家阵营', '所属方数组', '开始时间', '存档时间']
        for n, k in enumerate(keys):
            r, col = divmod(n, 2)                    # 双栏：5 行 × 2 组
            lab = ttk.Label(body, text=k, style='Field.TLabel', anchor='w', width=9)
            lab.grid(row=r, column=col * 2, sticky='w', pady=6,
                     padx=(0 if col == 0 else 40, 0))
            v = ttk.Label(body, text='-', style='Value.TLabel', anchor='w')
            v.grid(row=r, column=col * 2 + 1, sticky='w', padx=(14, 0), pady=6)
            self.info_labels[k] = v

        # ── ② 金钱条（横向：当前值 / 改为 / 应用 / 说明）──
        row2 = tk.Frame(f, bg=T['canvas'])
        row2.pack(fill='x', pady=(12, 0))

        mcard, mbody = ui_theme.card(row2, '金钱')
        mcard.pack(side='left', fill='both', expand=True)

        ttk.Label(mbody, text='当前', style='Field.TLabel', width=6).grid(
            row=0, column=0, sticky='w')
        self.lbl_money = ttk.Label(mbody, text='-', style='Num.TLabel')
        self.lbl_money.grid(row=0, column=1, sticky='w', padx=(8, 0))
        self.lbl_money_field = ttk.Label(mbody, text='', style='Field.TLabel',
                                        font=(ui_theme.FONT_MONO, 8))
        self.lbl_money_field.grid(row=0, column=2, sticky='e', padx=(16, 0))

        ttk.Label(mbody, text='改为', style='Field.TLabel', width=6).grid(
            row=1, column=0, sticky='w', pady=(12, 0))
        self.ent_money = ttk.Entry(mbody, width=14)
        self.ent_money.grid(row=1, column=1, sticky='w', padx=(8, 0), pady=(12, 0))
        ttk.Button(mbody, text='应用', style='Primary.TButton',
                   command=self.apply_money).grid(row=1, column=2, sticky='e',
                                                  pady=(12, 0))

        # ── ③ 地图与规则（贴铁律：本工具只读地图）──
        gcard, gbody = ui_theme.card(row2, '地图与规则')
        gcard.pack(side='left', fill='both', expand=True, padx=(12, 0))
        for c in (1, 3):
            gbody.columnconfigure(c, weight=1, uniform='v')
        self.lbl_map = ttk.Label(gbody, text='-', style='Value.TLabel', anchor='w')
        self.lbl_map_mode = ttk.Label(gbody, text='-', style='Value.TLabel', anchor='w')
        for r, (lab, wid) in enumerate((('地图文件', self.lbl_map),
                                        ('挂接方式', self.lbl_map_mode))):
            col = 0
            ttk.Label(gbody, text=lab, style='Field.TLabel', width=9).grid(
                row=r, column=col, sticky='w', pady=6)
            wid.grid(row=r, column=col + 1, sticky='w', padx=(14, 0), pady=6)
        ttk.Label(gbody, text='本工具只读地图：不写 .map，一个字节都不改',
                  style='Field.TLabel').grid(row=2, column=0, columnspan=4,
                                             sticky='w', pady=(6, 0))
        return f
