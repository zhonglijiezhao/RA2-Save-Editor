# -*- coding: utf-8 -*-
"""武器页（原 `App.weapons`）

★2026-10-28（审查 P2-8）：`App` 原来 8400 行、222 个方法，是典型的 God Class。
  这里按"页"切开，**方法体一行没改** —— 用 mixin 而不是把 `self` 换成 `self.app`，
  所以 `self.xxx` 照旧指向同一个 App 实例，行为零变化。
  边界 = 原来 `_tab_xxx()` 那整段（含它引用的私有辅助方法）。
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
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _slot_text(sl):
    """武器槽的显示文字：有名字就给名字，**有指针但认不出**就显示 `?地址`，真的空才 `—`。

    ★2026-11-05（玩家 SAVE6952）：以前一律 `name or '—'`，于是「指针明明有、只是那个
      武器对象没在名字表里」的槽看着跟"没装武器"一模一样（HORNET/ORCA/TNKD 三处）。
    """
    nm = (sl or {}).get('name')
    if nm:
        return nm
    a = (sl or {}).get('addr') or 0
    return ('?%08X' % a) if a else '—'
for _p in (_ROOT, os.path.join(_ROOT, 'core')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from editor_core import SaveDoc
from ui.msg import filedialog, messagebox
from tkinter import ttk
import tkinter as tk
import ui_theme


class WeaponsPage(object):
    def _tab_weapons(self, nb):
        T = ui_theme.T
        f = ttk.Frame(nb, padding=8)
        _ph = ui_theme.page_header(
            f, '武器 · 弹头',
            '存档里的武器类型表；改这里只影响这一份存档，rulesmd.ini 一个字节不动')
        _ph.pack(fill='x', padx=2, pady=(2, 8))

        # ★★§SOV15（2026-11-04 玩家："这个武器列表能不能以单位显示，改这个单位的主武器
        #   副武器，比如把大兵的武器换成谭雅的"）⇒ 顶部加「武器 / 单位」视图切换。
        mrow = ttk.Frame(f)
        mrow.pack(fill='x', padx=2, pady=(0, 6))
        ttk.Label(mrow, text='视图').pack(side='left')
        self._wpn_mode = tk.StringVar(value='weapon')
        ttk.Radiobutton(mrow, text='武器（按把）', value='weapon',
                        variable=self._wpn_mode,
                        command=self._wpn_switch).pack(side='left', padx=(6, 0))
        ttk.Radiobutton(mrow, text='单位（按型号改主/副武器）', value='unit',
                        variable=self._wpn_mode,
                        command=self._wpn_switch).pack(side='left', padx=(10, 0))
        self.lbl_wpn_mode = ttk.Label(mrow, text='', foreground=T['muted'],
                                      font=(ui_theme.FONT_UI, 8))
        self.lbl_wpn_mode.pack(side='left', padx=(10, 0))

        body = tk.Frame(f, bg=T['canvas'])
        body.pack(fill='both', expand=True)

        # ── 左：武器列表（★宽度按"正文可用宽度"配比：主窗口 1280 时正文约 740，
        #      左 330 + 右 ~370 刚好不溢出。改这里先量 row.winfo_reqwidth()）──
        lf = tk.Frame(body, bg=T['canvas'], width=ui_theme.px(330))
        lf.pack(side='left', fill='both')
        lf.pack_propagate(False)

        qrow = ttk.Frame(lf)
        qrow.pack(fill='x', pady=(0, 6))
        self._wpn_qrow = qrow
        self._q_wpn = tk.StringVar()
        ent = ttk.Entry(qrow, textvariable=self._q_wpn, width=11)
        ent.pack(side='left')
        ent.bind('<KeyRelease>', lambda e: self.refresh_weapons())
        ent.bind('<Escape>', lambda e: (self._q_wpn.set(''), self.refresh_weapons()))
        self._wpn_only_diff = tk.BooleanVar(value=False)
        ttk.Checkbutton(qrow, text='只看 ≠原版', variable=self._wpn_only_diff,
                        command=self.refresh_weapons).pack(side='left', padx=(6, 0))

        cols = ('name', 'dmg', 'rof', 'rng', 'wh')
        wrap, self.tw = self._tree_scrolled(lf, cols, 20)
        wrap.pack(fill='both', expand=True)
        for c, t, w, a in (('name', '武器', 118, 'w'), ('dmg', '伤害', 42, 'e'),
                           ('rof', '射速', 38, 'e'), ('rng', '射程', 46, 'e'),
                           ('wh', '弹头', 70, 'w')):
            self.tw.heading(c, text=t)
            self.tw.column(c, width=w, anchor=a)
        self.tw.tag_configure('diff', foreground=T['danger'])
        self.tw.bind('<<TreeviewSelect>>', self._wpn_pick)
        # ★§SOV15：单位视图的列表（同一个位置，切换时显隐）
        self._wpn_wrap = wrap
        u_wrap, self.utw = self._tree_scrolled(
            lf, ('type', 'cn', 'p', 's'), 20)
        self._unit_wrap = u_wrap
        # 4 列刚好塞进 330px 左栏（带竖滚动条），精英武器在右侧卡片里看
        for c, t, w, a in (('type', '型号', 60, 'w'), ('cn', '名称', 86, 'w'),
                           ('p', '主武器', 82, 'w'), ('s', '副武器', 74, 'w')):
            self.utw.heading(c, text=t)
            self.utw.column(c, width=w, anchor=a)
        self.utw.tag_configure('ingame', foreground=T['ink'])
        self.utw.bind('<<TreeviewSelect>>', self._unit_pick)
        bot = ttk.Frame(lf)
        bot.pack(fill='x', pady=(4, 0))
        self._wpn_bot = bot
        self.lbl_wpn_count = ttk.Label(bot, text='', foreground=T['muted'],
                                       font=(ui_theme.FONT_UI, 8))
        self.lbl_wpn_count.pack(side='left')
        # ★2026-10-28：导出（玩家问「能不能把存档里的武器提取出来」；
        #   随后又要求「导出成 INI，更符合红警的格式」⇒ 一个按钮 + 文件类型选格式）
        self._btn_wpn_export = ttk.Button(bot, text='导出…', width=9,
                                          command=self._wpn_export)
        self._btn_wpn_export.pack(side='right')

        # ── 右：详情 + 编辑 ──
        rt = tk.Frame(body, bg=T['canvas'])
        rt.pack(side='left', fill='both', expand=True, padx=(10, 0))

        # ① 武器数值
        o1, in1 = ui_theme.card(rt, '武器数值')
        o1.pack(fill='x')
        self._o_wpn_stats = o1
        self.lbl_wpn_name = tk.Label(in1, text='（左侧选一把武器）', bg=T['surface'],
                                     fg=T['ink'], font=(ui_theme.FONT_UI, 12, 'bold'),
                                     anchor='w')
        self.lbl_wpn_name.grid(row=0, column=0, columnspan=6, sticky='w')
        self.lbl_wpn_diff = tk.Label(in1, text='', bg=T['surface'], fg=T['muted'],
                                     font=(ui_theme.FONT_UI, 9), anchor='w',
                                     justify='left', wraplength=ui_theme.px(330))
        self.lbl_wpn_diff.grid(row=1, column=0, columnspan=6, sticky='w', pady=(2, 0))

        self._wpn_var = {}
        for i, (key, lab, unit) in enumerate((('damage', '伤害', '点'),
                                              ('rof', '射速', '帧·越小越快'),
                                              ('range', '射程', '格'))):
            g = tk.Frame(in1, bg=T['surface'])
            g.grid(row=2, column=i * 2, columnspan=2, sticky='w',
                   padx=(0 if i == 0 else 8, 0), pady=(8, 0))
            tk.Label(g, text=lab, bg=T['surface'], fg=T['muted'],
                     font=(ui_theme.FONT_UI, 9)).pack(side='left')
            v = tk.StringVar()
            self._wpn_var[key] = v
            ttk.Entry(g, textvariable=v, width=6).pack(side='left', padx=(3, 2))
            tk.Label(g, text=unit, bg=T['surface'], fg=T['muted_soft'],
                     font=(ui_theme.FONT_UI, 8)).pack(side='left')
        bar = tk.Frame(in1, bg=T['surface'])
        bar.grid(row=3, column=0, columnspan=6, sticky='w', pady=(10, 0))
        ttk.Button(bar, text='应用数值', style='Primary.TButton',
                   command=self._wpn_apply).pack(side='left')
        ttk.Button(bar, text='按 rules 还原', style='Ghost.TButton',
                   command=self._wpn_revert).pack(side='left', padx=6)
        self.lbl_wpn_msg = tk.Label(bar, text='', bg=T['surface'], fg=T['muted'],
                                    font=(ui_theme.FONT_UI, 9))
        self.lbl_wpn_msg.pack(side='left', padx=6)
        # 哪些型号用它（一行灰字，不单独占一张卡 —— 否则小窗口会被挤成一条缝）
        self.lbl_wpn_users = tk.Label(in1, text='—', bg=T['surface'], fg=T['ink_2'],
                                      font=(ui_theme.FONT_UI, 9), anchor='w',
                                      justify='left', wraplength=ui_theme.px(330))
        self.lbl_wpn_users.grid(row=4, column=0, columnspan=6, sticky='w', pady=(8, 0))

        # ② 弹头克制表（11 项，两列）
        o2, in2 = ui_theme.card(rt, '弹头克制表')
        o2.pack(fill='both', expand=True, pady=(8, 0))
        self._o_wpn_verse = o2

        # ②b 单位武器（★§SOV15，默认不显示）
        o3, in3 = ui_theme.card(rt, '单位武器（改的是型号）')
        self._o_unit = o3
        self.lbl_unit_name = tk.Label(in3, text='（左侧选一个单位）', bg=T['surface'],
                                      fg=T['ink'], font=(ui_theme.FONT_UI, 12, 'bold'),
                                      anchor='w')
        self.lbl_unit_name.grid(row=0, column=0, columnspan=4, sticky='w')
        tk.Label(in3, text='改的是「型号」：该型号所有单位（含敌方、以后新造的）都会换。'
                           '按「还原选中型号」可回退到 rules 的配置。',
                 bg=T['surface'], fg=T['muted'], font=(ui_theme.FONT_UI, 8),
                 anchor='w', justify='left', wraplength=ui_theme.px(330)).grid(
            row=1, column=0, columnspan=4, sticky='w', pady=(2, 6))
        self._unit_slot_var = {}
        self._unit_slot_cb = {}
        self._unit_slot_old = {}
        for i, (key, _rel, lab) in enumerate(SaveDoc.UNIT_WEAPON_SLOTS):
            r = 2 + i
            tk.Label(in3, text=lab, bg=T['surface'], fg=T['muted'],
                     font=(ui_theme.FONT_UI, 9)).grid(row=r, column=0, sticky='w',
                                                      pady=3)
            v = tk.StringVar()
            self._unit_slot_var[key] = v
            cb = ttk.Combobox(in3, textvariable=v, width=24)
            cb.grid(row=r, column=1, sticky='w', padx=(6, 4), pady=3)
            self._unit_slot_cb[key] = cb
            tk.Label(in3, text='', bg=T['surface'], fg=T['muted_soft'],
                     font=(ui_theme.FONT_UI, 8)).grid(row=r, column=2, sticky='w')
        self._unit_also_elite = tk.BooleanVar(value=True)
        ttk.Checkbutton(in3, text='同时改精英武器（用 …E 版）',
                        variable=self._unit_also_elite).grid(
            row=6, column=0, columnspan=3, sticky='w', pady=(6, 0))
        bar3 = tk.Frame(in3, bg=T['surface'])
        bar3.grid(row=7, column=0, columnspan=4, sticky='w', pady=(8, 0))
        ttk.Button(bar3, text='应用改动', style='Primary.TButton',
                   command=self._unit_apply).pack(side='left')
        ttk.Button(bar3, text='还原选中型号', style='Ghost.TButton',
                   command=self._unit_revert).pack(side='left', padx=6)
        self.lbl_unit_msg = tk.Label(in3, text='', bg=T['surface'], fg=T['ink_2'],
                                     font=(ui_theme.FONT_UI, 9), anchor='w',
                                     justify='left', wraplength=ui_theme.px(330))
        self.lbl_unit_msg.grid(row=8, column=0, columnspan=4, sticky='w',
                               pady=(6, 0))
        self.lbl_wh_name = tk.Label(in2, text='—', bg=T['surface'], fg=T['ink'],
                                    font=(ui_theme.FONT_UI, 10, 'bold'), anchor='w')
        self.lbl_wh_name.grid(row=0, column=0, columnspan=8, sticky='w')
        tk.Label(in2, text='伤害 × 该系数（100 = 100%）', bg=T['surface'],
                 fg=T['muted'], font=(ui_theme.FONT_UI, 8), anchor='w').grid(
            row=1, column=0, columnspan=8, sticky='w')
        self._verse_var = []
        for i in range(SaveDoc.VERSES_COUNT):
            col = (i // 6) * 3
            row = 2 + (i % 6)
            nm = SaveDoc.ARMOR_ORDER[i]
            tk.Label(in2, text='%d %s' % (i + 1, nm), bg=T['surface'], fg=T['muted'],
                     font=(ui_theme.FONT_MONO, 8), anchor='w', width=10).grid(
                row=row, column=col, sticky='w', pady=1)
            v = tk.StringVar()
            self._verse_var.append(v)
            ttk.Entry(in2, textvariable=v, width=5).grid(row=row, column=col + 1,
                                                         sticky='w', pady=1, padx=(2, 1))
            tk.Label(in2, text='%', bg=T['surface'], fg=T['muted_soft'],
                     font=(ui_theme.FONT_UI, 8)).grid(row=row, column=col + 2, sticky='w',
                                                      padx=(0, 10), pady=1)
        bar2 = tk.Frame(in2, bg=T['surface'])
        bar2.grid(row=8, column=0, columnspan=8, sticky='w', pady=(8, 0))
        ttk.Button(bar2, text='应用克制表', command=self._wpn_verse_apply).pack(side='left')
        self.lbl_verse_msg = tk.Label(bar2, text='', bg=T['surface'], fg=T['muted'],
                                      font=(ui_theme.FONT_UI, 9))
        self.lbl_verse_msg.pack(side='left', padx=6)
        return f

    # ---------------- ★§SOV15 单位视图：按型号改主/副武器 ----------------
    _UNIT_RULE_KEYS = {'primary': 'Primary', 'secondary': 'Secondary',
                      'elite_primary': 'ElitePrimary', 'elite_secondary': 'EliteSecondary'}

    def _wpn_switch(self):
        """武器视图 ⇄ 单位视图（只切显隐，控件都是一次性建好的）。"""
        unit = (self._wpn_mode.get() == 'unit')
        try:
            if unit:
                self._wpn_qrow.pack_forget()       # 「只看 ≠原版」对单位视图没意义
                try:
                    self._btn_wpn_export.pack_forget()   # 导出的武器表，单位视图下不适用
                    self.lbl_wpn_count.config(text='')
                except Exception:
                    _swallow()
                self._wpn_wrap.pack_forget()
                self._unit_wrap.pack(fill='both', expand=True, before=self._wpn_bot)
                self._o_wpn_stats.pack_forget()
                self._o_wpn_verse.pack_forget()
                self._o_unit.pack(fill='both', expand=True)
                self.refresh_units()
            else:
                self._unit_wrap.pack_forget()
                self._wpn_qrow.pack(fill='x', pady=(0, 6), before=self._wpn_wrap)
                try:
                    self._btn_wpn_export.pack(side='right')
                except Exception:
                    _swallow()
                self._wpn_wrap.pack(fill='both', expand=True, before=self._wpn_bot)
                self._o_unit.pack_forget()
                self._o_wpn_stats.pack(fill='x')
                self._o_wpn_verse.pack(fill='both', expand=True, pady=(8, 0))
                self.refresh_weapons()
        except Exception as e:
            try:
                self.lbl_wpn_mode.config(text='切换视图失败：%s' % e)
            except Exception:
                _swallow()

    def refresh_units(self):
        """填「单位」列表：型号 / 名称 / 主武器 / 副武器 / 精英主武器。"""
        if not hasattr(self, 'utw'):
            return
        try:
            self.utw.delete(*self.utw.get_children())
        except Exception:
            _swallow()
        try:
            self.lbl_unit_msg.config(text='')
        except Exception:
            _swallow()
        self._unit_rows = {}
        rows = []
        try:
            rows = self.doc.unit_weapon_list() if self.doc else []
        except Exception as e:
            try:
                self.lbl_wpn_mode.config(text='读不到单位表：%s' % e)
            except Exception:
                _swallow()
        for i, r in enumerate(rows):
            sl = r['slots']
            self.utw.insert('', 'end', iid=str(i),
                            values=(r['type'], r['cn'] or '—',
                                    _slot_text(sl['primary']),
                                    _slot_text(sl['secondary'])),
                            tags=('ingame',) if r['in_game'] else ())
            self._unit_rows[str(i)] = r
        try:
            self.lbl_wpn_mode.config(
                text='可改型号 %d 个（深色 = 本局场上出现过）' % len(rows))
        except Exception:
            _swallow()
        try:
            names = sorted({w['name'] for w in self.doc.weapon_objects()}) if self.doc else []
        except Exception:
            names = []
        for cb in getattr(self, '_unit_slot_cb', {}).values():
            try:
                cb['values'] = names
            except Exception:
                _swallow()

    def _unit_pick(self, _e=None):
        """选中一个型号 ⇒ 把它的 4 个槽填进下拉。"""
        sel = ()
        try:
            sel = self.utw.selection()
        except Exception:
            _swallow()
        if not sel:
            return
        r = (getattr(self, '_unit_rows', None) or {}).get(sel[0])
        if not r:
            return
        self._unit_cur = r
        try:
            self.lbl_unit_name.config(
                text='%s ｜ %s%s' % (r['type'], r['cn'] or '—',
                                     '' if r['in_game'] else '（本局没造）'))
        except Exception:
            _swallow()
        for key, _rel, _lab in SaveDoc.UNIT_WEAPON_SLOTS:
            nm = r['slots'][key]['name'] or ''
            try:
                self._unit_slot_var[key].set(nm)
            except Exception:
                _swallow()
            self._unit_slot_old[key] = nm
        # ★这里**不要**清消息：`selection_set()` 排队的 <<TreeviewSelect>> 会在
        #   update 时才派发，如果这里清空，就会把「应用」刚写好的提示擦掉
        #   （2026-11-04 自检抓到的）。消息由 refresh_units() 统一清。

    def _unit_apply(self):
        """把下拉里改过的槽写进存档（定长 u32）。"""
        r = getattr(self, '_unit_cur', None)
        if not r:
            try:
                self.lbl_unit_msg.config(text='先在左边选一个单位')
            except Exception:
                _swallow()
            return
        todo = []
        for key, _rel, _lab in SaveDoc.UNIT_WEAPON_SLOTS:
            try:
                new = (self._unit_slot_var[key].get() or '').strip()
            except Exception:
                new = ''
            old = self._unit_slot_old.get(key, '')
            if new and new != old:
                todo.append((key, new))
        if not todo:
            try:
                self.lbl_unit_msg.config(text='没有改动')
            except Exception:
                _swallow()
            return
        lab = {k: l for k, _o, l in SaveDoc.UNIT_WEAPON_SLOTS}
        lines = ['%s（%s）' % (r['type'], r['cn'] or '—'), '',
                 '改的是型号 ⇒ 该型号的所有单位都会换（含敌方、以后新造的）。', '']
        for key, new in todo:
            lines.append('　· %s：%s → %s'
                         % (lab[key], self._unit_slot_old.get(key) or '(空)', new))
        lines += ['', '只写存档（原地 u32），rulesmd.ini 一个字节不动。',
                  '不可撤销 —— 建议先在主窗口「备份存档」。', '', '确定应用吗？']
        if not messagebox.askyesno('改单位武器', '\n'.join(lines), parent=self):
            return
        done, errs = [], []
        for key, new in todo:
            try:
                done.append(self.doc.set_unit_weapon(
                    r['type'], key, new,
                    also_elite=bool(self._unit_also_elite.get())))
            except Exception as e:
                errs.append('%s → %s：%s' % (lab[key], new, e))
        self.refresh_units()
        for iid, row in (getattr(self, '_unit_rows', None) or {}).items():
            if row['type'] == r['type']:
                try:
                    self.utw.selection_set(iid)
                    self.utw.see(iid)
                except Exception:
                    _swallow()
                self._unit_pick()
                break
        if errs:
            messagebox.showwarning('部分没改成', '\n'.join(errs), parent=self)
        bits = []
        for info in done:
            for c in info['changes']:
                if c['changed']:
                    bits.append('%s %s→%s' % (lab[c['slot']], c['old_name'] or '(空)',
                                              c['new_name']))
        if bits:
            try:
                self.lbl_unit_msg.config(text='已改：' + '；'.join(bits)[:140])
            except Exception:
                _swallow()
            try:
                self.status.set('已改 %s 的武器：%s' % (r['type'], '；'.join(bits)[:90]))
            except Exception:
                _swallow()

    def _unit_revert(self):
        """把选中型号的武器按 rules 还原（只在 rules 里有值时才动）。"""
        r = getattr(self, '_unit_cur', None)
        if not r:
            try:
                self.lbl_unit_msg.config(text='先在左边选一个单位')
            except Exception:
                _swallow()
            return
        kv = (SaveDoc._rules_all() or {}).get(r['type']) or {}
        todo = []
        for key, _rel, _lab in SaveDoc.UNIT_WEAPON_SLOTS:
            want = (kv.get(self._UNIT_RULE_KEYS[key]) or '').strip()
            if want and want != (r['slots'][key]['name'] or ''):
                todo.append((key, want))
        if not todo:
            try:
                self.lbl_unit_msg.config(text='已经和 rules 一致')
            except Exception:
                _swallow()
            return
        lab = {k: l for k, _o, l in SaveDoc.UNIT_WEAPON_SLOTS}
        lines = ['%s（%s）按 rules 还原：' % (r['type'], r['cn'] or '—'), '']
        for key, want in todo:
            lines.append('　· %s：%s → %s'
                         % (lab[key], r['slots'][key]['name'] or '(空)', want))
        lines += ['', '只写存档。确定吗？']
        if not messagebox.askyesno('按 rules 还原', '\n'.join(lines), parent=self):
            return
        errs = []
        for key, want in todo:
            try:
                self.doc.set_unit_weapon(r['type'], key, want, also_elite=False)
            except Exception as e:
                errs.append('%s → %s：%s' % (lab[key], want, e))
        self.refresh_units()
        for iid, row in (getattr(self, '_unit_rows', None) or {}).items():
            if row['type'] == r['type']:
                try:
                    self.utw.selection_set(iid)
                    self.utw.see(iid)
                except Exception:
                    _swallow()
                self._unit_pick()
                break
        if errs:
            messagebox.showwarning('部分没还原', '\n'.join(errs), parent=self)
        else:
            try:
                self.lbl_unit_msg.config(text='已按 rules 还原 %d 个槽' % len(todo))
            except Exception:
                _swallow()

    def _wpn_export(self):
        """导出武器表（★2026-10-28：玩家要求 INI 格式，更符合红警）。

        - **INI**（默认）：每把武器一段，`[Vulcan] Damage=… ROF=… Range=… Verses=…`，
          跟 rulesmd.ini 长得一样，能直接贴进去覆盖同名武器；GBK + CRLF，红警原生。
        - **CSV**：表格，便于跟官方值逐列对照 / 发帖。

        按输出文件的扩展名走，不用点两次。
        """
        if not getattr(self, 'doc', None):
            messagebox.showwarning('提示', '请先打开一个存档', parent=self)
            return
        base = os.path.splitext(os.path.basename(self.doc.path))[0]
        only = bool(self._wpn_only_diff.get())
        suffix = '_武器表_非原版' if only else '_武器表'
        p = filedialog.asksaveasfilename(
            title='导出武器表', defaultextension='.ini',
            initialfile=base + suffix + '.ini',
            initialdir=os.path.dirname(self.doc.path),
            filetypes=[('INI（红警规则格式，可直接贴进 rulesmd.ini）', '*.ini'),
                       ('CSV 表格（Excel 打开）', '*.csv'),
                       ('所有文件', '*.*')])
        if not p:
            return
        is_csv = p.lower().endswith('.csv')
        try:
            if is_csv:
                n, path = self.doc.export_weapons_csv(p, only_diff=only)
            else:
                n, path = self.doc.export_weapons_ini(p, only_diff=only)
        except Exception as e:
            messagebox.showerror('导出失败', '%s' % e, parent=self)
            return
        self.status.set('已导出%s %d %s → %s'
                        % ('CSV' if is_csv else 'INI', n,
                           '行' if is_csv else '段', os.path.basename(path)))
        if is_csv:
            messagebox.showinfo(
                '导出完成',
                '已导出 %d 把武器%s。\n\n%s\n\n'
                'Excel 双击就能打开（UTF-8 带 BOM，中文不乱码）。\n'
                '「与官方规则的差异」那一列非空的，就是跟 rules 不一致的。'
                % (n, '（只含与原版不同的）' if only else '', path),
                parent=self)
        else:
            messagebox.showinfo(
                '导出完成',
                '已导出 %d 段%s。\n\n%s\n\n'
                '格式跟原版 rules 一样：每把武器一段（Damage/ROF/Range/Projectile/'
                'Speed/Warhead/Report/Anim/Burst），\n后面跟上它用到的 [抛射体] 段和 [弹头] 段。\n'
                '编码 GBK + CRLF（红警原生），贴进 rulesmd.ini 就能用。\n\n'
                '· Damage/ROF/Range/Burst、弹头的 CellSpread/Verses 是**这份存档的真实值**\n'
                '· Projectile/Speed/Report/Anim 和整段抛射体是**照 rules 抄的**（存档不存这些）'
                % (n, '（只含与原版不同的武器）' if only else '', path),
                parent=self)

    def _wpn_users(self, weapon):
        """rules 里哪些型号把 `weapon` 当主/副/精英武器 → ['E1 GI（Primary）', …]。"""
        out = []
        try:
            for sec, kv in (SaveDoc._rules_all() or {}).items():
                for k in ('Primary', 'Secondary', 'ElitePrimary', 'EliteSecondary'):
                    if kv.get(k, '').strip() == weapon:
                        out.append('%s %s（%s）' % (sec, kv.get('Name', ''), k))
        except Exception:
            _swallow()
        return out

    def _wpn_diff_of(self, w):
        """和**官方原版**比 → 差异描述（空串 = 一致）。

        ★2026-10-28：基准从"当前 rules"改成"官方快照"。玩家自己写进 rules 的武器
          值和 rules 一致，按当前 rules 比会被判成"原版" —— 那"只看 ≠rules"就永远
          筛不出他自己加的武器。官方里没有的，这里会标「新增」。
        """
        try:
            marks, _official = self.doc._weapon_diff(
                w['name'], w['damage'], w['rof'], w['range_cells'])
            return '；'.join(marks)
        except Exception:
            return ''

    def _wpn_refresh_if_visible(self):
        """当前正停在武器页时立刻重填（换档后 `nb.select(同一页)` 不触发事件）。

        不在武器页就什么都不做 —— 扫类型表要 230ms，别在开档路径上白花。
        """
        try:
            if not hasattr(self, 'tw'):
                return
            if self.nb.index(self.nb.select()) != 10:
                return
            # ★§SOV15：停在「单位」视图时刷单位表，别只刷武器
            if getattr(self, '_wpn_mode', None) is not None \
                    and self._wpn_mode.get() == 'unit':
                self.refresh_units()
            else:
                self.refresh_weapons()
        except Exception:
            _swallow()

    def refresh_weapons(self):
        """填左侧武器列表（★2026-10-28）。"""
        if not hasattr(self, 'tw'):
            return
        keep = getattr(self, '_wpn_cur', None)     # 刷新前记下选中的那把
        self.tw.delete(*self.tw.get_children())    # ★delete 会清空 selection
        self._wpn_rows = {}
        self._wpn_loaded = True
        if not self.doc:
            self.lbl_wpn_count.config(text='')
            return
        try:
            ws = self.doc.weapon_objects()
        except Exception as e:
            self.status.set('武器表扫描失败：%s' % e)
            return
        whmap = self.doc.weapon_warhead_map()
        q = (self._q_wpn.get() or '').strip().upper()
        only = self._wpn_only_diff.get()
        n = 0
        for w in ws:
            if q and q not in w['name'].upper():
                continue
            diff = self._wpn_diff_of(w)
            if only and not diff:
                continue
            self._wpn_rows[w['name']] = w
            self.tw.insert('', 'end', iid=w['name'],
                           values=(('★ ' if diff else '') + w['name'],
                                   w['damage'], w['rof'],
                                   '%.2f' % w['range_cells'],
                                   whmap.get(w['name'], '?')),
                           tags=('diff',) if diff else ())
            n += 1
        ndiff = sum(1 for w in ws if self._wpn_diff_of(w))
        self.lbl_wpn_count.config(
            text='%d / %d 把（%d 把 ≠原版）' % (n, len(ws), ndiff))
        # 恢复选中：优先保持原来那把；它被搜索/过滤藏掉了才退回第一行
        target = keep if (keep and self.tw.exists(keep)) else (
            self.tw.get_children()[0] if self.tw.get_children() else None)
        if target:
            self.tw.selection_set(target)
            self.tw.focus(target)

    def _wpn_pick(self, _e=None):
        """选中一把武器 → 右侧填数值 + 弹头克制表 + 使用它的型号。"""
        sel = self.tw.selection()
        if not sel:
            return
        name = sel[0]
        w = (getattr(self, '_wpn_rows', None) or {}).get(name)
        if not w:
            return
        same = (name == getattr(self, '_wpn_cur', None))
        self._wpn_cur = name
        whname = self.doc.weapon_warhead_map().get(name, '?')
        self.lbl_wpn_name.config(text='%s   →   弹头 %s' % (name, whname))
        d = self._wpn_diff_of(w)
        self.lbl_wpn_diff.config(
            text=('与官方原版不同：' + d) if d else '与官方原版一致',
            fg=(ui_theme.T['danger'] if d else ui_theme.T['ok']))
        self._wpn_var['damage'].set(str(w['damage']))
        self._wpn_var['rof'].set(str(w['rof']))
        self._wpn_var['range'].set('%g' % w['range_cells'])
        if not same:
            # ★只有"换了另一把武器"才清提示 —— 应用数值后刷新会重选同一把，
            #   那时清掉就把刚写的"已改 N 项"抹了（自检 §297 逮到）
            self.lbl_wpn_msg.config(text='', fg=ui_theme.T['muted'])
        # 克制表
        wh = None
        try:
            wh = next((x for x in self.doc.warhead_objects() if x['name'] == whname), None)
        except Exception:
            _swallow()
        self.lbl_wh_name.config(text=whname if wh else '%s（存档里没有这个弹头对象）' % whname)
        for i, v in enumerate(self._verse_var):
            if wh:
                v.set('%g' % (wh['verses'][i] * 100))
            else:
                v.set('')
        if not same:
            self.lbl_verse_msg.config(text='')
        # 使用它的型号
        users = self._wpn_users(name)
        self.lbl_wpn_users.config(
            text=('\n'.join(users) if users else '（rules 里没有型号把它当主/副武器）'))

    def _wpn_apply(self):
        """把右侧三个数值写进存档（定长）。"""
        if not self.doc or not getattr(self, '_wpn_cur', None):
            return
        name = self._wpn_cur
        try:
            r1 = self.doc.set_weapon_field(name, 'damage', self._wpn_var['damage'].get())
            r2 = self.doc.set_weapon_field(name, 'rof', self._wpn_var['rof'].get())
            r3 = self.doc.set_weapon_field(name, 'range', self._wpn_var['range'].get())
        except Exception as e:
            messagebox.showerror('改不了', '%s' % e, parent=self)
            return
        ch = [x for x in (r1, r2, r3) if x['changed']]
        warns = [x.get('warn') for x in (r1, r2, r3) if x.get('warn')]
        # 先刷新列表（选中项会保持），再写提示
        self.refresh_weapons()
        if warns:
            self.lbl_wpn_msg.config(text=warns[0], fg=ui_theme.T['warn'])
        else:
            self.lbl_wpn_msg.config(
                text=('已改 %d 项（尚未保存）' % len(ch)) if ch else '数值没变',
                fg=ui_theme.T['muted'])
        if ch:
            self.status.set('%s：%s（尚未保存，记得另存/原地保存）'
                            % (name, '、'.join('%s %s→%s' % (
                                {'damage': '伤害', 'rof': '射速', 'range': '射程'}[x['field']],
                                x['old'] if x['field'] != 'range' else '%g格' % (x['old'] / 256.0),
                                x['new'] if x['field'] != 'range' else '%g格' % (x['new'] / 256.0))
                                for x in ch)))
        else:
            self.status.set('%s 数值未变' % name)

    def _wpn_verse_apply(self):
        """把 11 项克制系数写进存档。"""
        if not self.doc or not getattr(self, '_wpn_cur', None):
            return
        whname = self.doc.weapon_warhead_map().get(self._wpn_cur, None)
        if not whname:
            messagebox.showwarning('没有弹头', '这把武器在 rules 里没有 Warhead 定义', parent=self)
            return
        n = 0
        try:
            for i, v in enumerate(self._verse_var):
                s = (v.get() or '').strip()
                if not s:
                    continue
                r = self.doc.set_warhead_verse(whname, i + 1, s)
                if r['changed']:
                    n += 1
        except Exception as e:
            messagebox.showerror('改不了', '%s' % e, parent=self)
            return
        self.lbl_verse_msg.config(text=('已改 %d 项（尚未保存）' % n) if n else '数值没变')
        self.status.set('%s 的克制表改了 %d 项（尚未保存）' % (whname, n))
        self.refresh_weapons()          # 列表里的数值列不用变，但保持选中态一致

    def _wpn_revert(self):
        """把当前武器三个数值按 rulesmd.ini 还原（改错了的后悔药）。"""
        if not self.doc or not getattr(self, '_wpn_cur', None):
            return
        r = (SaveDoc._rules_all() or {}).get(self._wpn_cur) or {}
        if not r.get('Damage'):
            messagebox.showinfo('还原', 'rules 里没有这把武器的定义，没法还原', parent=self)
            return
        for key, rk in (('damage', 'Damage'), ('rof', 'ROF'), ('range', 'Range')):
            if r.get(rk):
                self._wpn_var[key].set(r[rk])
        self.lbl_wpn_msg.config(text='已填入 rules 的值 —— 点「应用数值」才写入')
