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
from ui.msg import messagebox


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

        # ── ④ 存档修复：清除空气墙（★2026-11-01 新增）──
        #   判据见 SaveDoc.air_wall_cells()：+0x138 占用标记非零、+0xF8 占用键为 0。
        #   载具读这个标记、步兵不读 ⇒ 症状永远是"只挡载具"。
        #   ★压成一行：首页在 700 高的最小窗口下不能溢出（原底部 606px）。
        row3 = tk.Frame(f, bg=T['canvas'])
        row3.pack(fill='x', pady=(12, 0))
        acard, abody = ui_theme.card(row3, '存档修复 · 空气墙')
        acard.pack(fill='both', expand=True)

        arow = tk.Frame(abody, bg=T['surface'])
        arow.pack(fill='x')
        self.lbl_airwall = ttk.Label(
            arow,
            text='清理「格子上空无一物、却仍登记着被占用」的残留标记（只挡载具）',
            style='Value.TLabel', anchor='w')
        self.lbl_airwall.pack(side='left', fill='x', expand=True)
        ttk.Button(arow, text='一键清除', style='Primary.TButton',
                   command=self.fix_air_walls).pack(side='right')
        ttk.Button(arow, text='检查', command=self.scan_air_walls).pack(
            side='right', padx=(0, 8))
        return f

    # ------------------------------------------------------------------
    #  清除空气墙（★2026-11-01）
    #  真实战绩：SAVE2CD6（超时空+铁幕留下的 9 格 0x20）、SAVE72AE（5 格）
    #  存档改完读档即消失 —— 玩家在游戏里实测确认过。
    # ------------------------------------------------------------------
    def _airwall_doc(self):
        doc = getattr(self, 'doc', None)
        if not doc:
            messagebox.showinfo('清除空气墙', '请先打开一个存档。', parent=self)
            return None
        return doc

    def scan_air_walls(self):
        """只扫描、不写 → 刷新首页那一行结果。"""
        doc = self._airwall_doc()
        if doc is None:
            return
        try:
            n, cells = doc.air_wall_cells()
        except Exception as exc:
            messagebox.showerror('清除空气墙', '扫描失败：%s' % exc, parent=self)
            return
        if n == 0:
            self.lbl_airwall.config(text='这个档里找不到格子表，无法检查')
            return
        if not cells:
            self.lbl_airwall.config(text='干净：%d 个格槽全部正常' % n)
            return
        self.lbl_airwall.config(text='发现 %d 处空气墙（共 %d 个格槽）'
                                     % (len(cells), n))
        preview = '、'.join('(%d,%d)' % (c['x'], c['y'])
                            for c in sorted(cells, key=lambda c: (c['y'], c['x']))[:16])
        more = ' …' if len(cells) > 16 else ''
        messagebox.showinfo(
            '清除空气墙',
            '发现 %d 处空气墙：\n\n%s%s\n\n点「一键清除」可以全部修掉。'
            % (len(cells), preview, more),
            parent=self)

    def fix_air_walls(self):
        """真正清除（只动 +0x138 这 1 个字节）+ 提示保存。"""
        doc = self._airwall_doc()
        if doc is None:
            return
        try:
            n, cells = doc.clear_air_walls()
        except Exception as exc:
            messagebox.showerror('清除空气墙', '清除失败：%s' % exc, parent=self)
            return
        if n == 0:
            self.lbl_airwall.config(text='这个档里找不到格子表，没有改动')
            return
        if not cells:
            self.lbl_airwall.config(text='干净：%d 个格槽全部正常，无需修复' % n)
            messagebox.showinfo('清除空气墙', '这个档没有空气墙，什么都没改。',
                                parent=self)
            return
        self.lbl_airwall.config(
            text='已清除 %d 处空气墙（共 %d 个格槽）—— 记得保存' % (len(cells), n))
        try:
            self._refresh_title()
        except Exception:
            pass
        messagebox.showinfo(
            '清除空气墙',
            '已清除 %d 处空气墙。\n\n'
            '接下来：保存存档 → 进游戏读这个档，空气墙就没了。\n'
            '（通行状态是读档那一刻按标记重建的，游戏运行中改无效）' % len(cells),
            parent=self)
