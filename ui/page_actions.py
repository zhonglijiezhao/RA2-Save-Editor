# -*- coding: utf-8 -*-
"""行为页（原 `App.actions`）

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

from editor_core import ACTION_TYPES
from editor_core import action_name
from editor_core import action_note
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk
import os
import tkinter as tk
import ui_theme


class ActionsPage(object):
    def _tab_actions(self, nb):
        f = ttk.Frame(nb, padding=8)
        _ph = ui_theme.page_header(f, '行为', '选中触发对应的行为链')
        _ph.pack(fill='x', padx=2, pady=(2, 8))
        cols = ('idx', 'type', 'name', 'arg', 'argtext', 'owner', 'off')
        # ★2026-10-28：带横竖滚动条（同事件页）
        self._ta_wrap, self.ta = self._tree_scrolled(f, cols, 16)
        for c, t, w, a in (('idx', '#', 36, 'center'), ('type', '类型', 50, 'center'),
                           ('name', '行为类型', 220, 'w'), ('arg', '参数', 78, 'e'),
                           ('argtext', '参数含义', 108, 'w'), ('owner', '所属触发', 150, 'w'),
                           ('off', '明文位置', 92, 'w')):
            self.ta.heading(c, text=t)
            self.ta.column(c, width=w, anchor=a)
        # ★2026-10-18：同款搜索框（按 类型/名称/参数/所属触发/位置 搜）
        qrow = ttk.Frame(f)
        qrow.pack(fill='x', pady=(0, 6))
        ttk.Label(qrow, text='搜索：').pack(side='left')
        self._q_ac = tk.StringVar()
        ent = ttk.Entry(qrow, textvariable=self._q_ac, width=32)
        ent.pack(side='left')
        ent.bind('<KeyRelease>', lambda e: self._fill_ac_tree())
        ent.bind('<Escape>', lambda e: (self._q_ac.set(''), self._fill_ac_tree()))
        ttk.Button(qrow, text='✕', width=3,
                   command=lambda: (self._q_ac.set(''), self._fill_ac_tree())
                   ).pack(side='left', padx=(2, 8))
        self.lbl_ac_count = ttk.Label(qrow, text='', foreground=ui_theme.T['muted'])
        self.lbl_ac_count.pack(side='left')
        self._ta_wrap.pack(fill='both', expand=True)      # ★带滚动条的容器
        self.ta.tag_configure('bad', foreground=ui_theme.T['danger'])
        self.ta.bind('<Double-1>', lambda e: self.edit_action())
        self.ta.bind('<Return>', lambda e: self.edit_action())

        row = ttk.Frame(f)
        row.pack(fill='x', pady=(8, 0))
        ttk.Button(row, text='修改选中行为…', command=self.edit_action).pack(side='left')
        # ★2026-10-22 §193：行为**按顺序执行** ⇒ 上下移就是改执行顺序
        ttk.Button(row, text='↑ 上移', width=7,
                   command=lambda: self.move_sel_action(-1)).pack(side='left', padx=(10, 2))
        ttk.Button(row, text='↓ 下移', width=7,
                   command=lambda: self.move_sel_action(+1)).pack(side='left', padx=2)
        ttk.Button(row, text='🗑 删除该行为',
                   command=self.remove_sel_action).pack(side='left', padx=6)
        ttk.Button(row, text='➕ 添加行为…',
                   command=self.add_action_dialog).pack(side='left', padx=6)
        ttk.Label(row, text='双击一行也可修改', foreground=ui_theme.T['muted']).pack(side='left', padx=10)
        self.lbl_map_ac = ttk.Label(f, text='', foreground=ui_theme.T['muted'], wraplength=880,
                                    justify='left')
        self.lbl_map_ac.pack(anchor='w', pady=(6, 0))
        return f

    def refresh_actions(self):
        if not hasattr(self, 'ta'):
            return
        self.ta.delete(*self.ta.get_children())
        self._aclist = []
        self._ac_rows = []
        if not self.doc:
            return
        try:
            acs = self.doc.actions()
        except Exception as e:
            self.status.set('行为扫描失败: %s' % e)
            return
        self._aclist = acs
        tnames = {r.sid: r.name for r in self.doc.records}
        # ★2026-10-18：行为是**链表**（对象 +0x3C = 下一条行为键，0=链尾），
        #   沿链能把 1813/1813 条行为都归属到触发 ⇒ 未挂地图也不再整列「—」。
        try:
            aown = self.doc.action_owner_sids()
        except Exception:
            aown = {}
        for i, a in enumerate(acs):
            own = a.get('owner')
            sid2 = aown.get(a.get('key'))
            if own:
                tag = '%s %s' % (own, tnames.get(own, ''))
                if a.get('map_type') is not None and \
                        (a['type'], a['arg']) != (a['map_type'], a['map_arg']):
                    tag += ' ⚠与地图不符'
            elif sid2:
                tag = '★%s %s（行为链）' % (sid2, tnames.get(sid2, ''))
            else:
                tag = '—'
            bad = (a['type'] == 0)
            if bad:
                tag += ' ⚠非法类型(0)会崩档'
            self._ac_rows.append((i, (i, a['type'], a['name'], a['arg'],
                                      a['arg_text'], tag, '0x%X' % a['off']),
                                  ('bad',) if bad else ()))
        self._fill_ac_tree()
        if getattr(self.doc, '_tables_lowconf', False):
            self.status.set('事件/行为定位未通过校验 —— 请勿按此修改（可能不是行为表）')
        self._refresh_map_labels()

    def _fill_ac_tree(self):
        """按搜索词填行为树（iid 仍是 `_aclist` 下标 ⇒ 选中/修改逻辑不变）。"""
        if not hasattr(self, 'ta'):
            return
        self.ta.delete(*self.ta.get_children())
        q = ((self._q_ac.get() if hasattr(self, '_q_ac') else '') or '').strip().lower()
        rows = getattr(self, '_ac_rows', [])
        shown = 0
        for i, vals, tags in rows:
            if q and not any(q in str(v).lower() for v in vals):
                continue
            self.ta.insert('', 'end', iid=str(i), values=vals, tags=tags)
            shown += 1
        if hasattr(self, 'lbl_ac_count'):
            self.lbl_ac_count.config(text='显示 %d / %d 条' % (shown, len(rows)),
                                     foreground=ui_theme.T['muted'])
        # ★2026-10-20：重填（搜索/刷新）后把触发页选中项的高亮补回来
        self._mark_hits(self.ta, rows, getattr(self, '_ac_hits', set()))

    def _refresh_map_labels(self):
        """把地图对照结论写到两个页面的提示行"""
        ms = getattr(self.doc, 'map_status', None) if self.doc else None
        if not hasattr(self, 'lbl_map_ac'):
            return
        if ms:
            txt = '地图：%s（%s）\n%s' % (os.path.basename(ms['path']),
                                        ms.get('encoding', ''), ms['text'])
        else:
            txt = ('未挂地图 —— 事件/行为只能按顺序对照地编。'
                   '点上方「自动匹配地图」或「选择地图…」可加载同目录的地图文件。')
        for name in ('lbl_map_ev', 'lbl_map_ac'):
            w = getattr(self, name, None)
            if w is not None:
                w.config(text=txt)

    def auto_map(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        mode = self.doc.auto_attach_map([self.game_dir.get()])
        self.refresh_events()
        self.refresh_actions()
        if mode == 'no_map':
            messagebox.showinfo('未找到地图',
                                '在存档目录和游戏目录里没找到可用地图。\n'
                                '请点「选择地图…」手动指定 .map 文件。')
        else:
            self.status.set('地图已挂载：%s' % self.doc.map_status['text'])

    def choose_map(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        p = filedialog.askopenfilename(
            title='选择该存档对应的地图',
            initialdir=os.path.dirname(self.doc.path),
            filetypes=[('红警地图', '*.map *.yrm'), ('所有文件', '*.*')])
        if not p:
            return
        self.doc.attach_map(p)
        self.refresh_events()
        self.refresh_actions()
        self.status.set('地图已挂载：%s' % self.doc.map_status['text'])

    def edit_action(self):
        if not self.doc or not getattr(self, '_aclist', None):
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.ta.selection()
        if not sel:
            messagebox.showwarning('提示', '请先选中一条行为')
            return
        ac0 = self._aclist[int(sel[0])]
        if self._guard_executed(self._sid_of_row(ac0)):   # ★所属触发已执行 ⇒ 改不生效
            return
        self._action_dialog(ac0)

    def _action_dialog(self, ac):
        dlg = tk.Toplevel(self)
        dlg.title('修改行为 — 明文位置 0x%X' % ac['off'])
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        # ⚠ 0 = 「-无行为-」：同上，禁止写入
        keys = [k for k in sorted(ACTION_TYPES) if k != 0]

        ttk.Label(dlg, text='行为类型').grid(row=0, column=0, sticky='e', padx=(12, 6), pady=(12, 4))
        cvar = tk.StringVar()
        cb = ttk.Combobox(dlg, textvariable=cvar, width=48, state='readonly',
                          values=['%d - %s' % (k, ACTION_TYPES[k][0]) for k in keys])
        cb.current(keys.index(ac['type']) if ac['type'] in keys else 0)
        cb.grid(row=0, column=1, sticky='w', padx=(0, 12), pady=(12, 4))

        ttk.Label(dlg, text='参数').grid(row=1, column=0, sticky='e', padx=(12, 6))
        avar = tk.StringVar(value=str(ac['arg']))
        ent = ttk.Entry(dlg, textvariable=avar, width=18)
        ent.grid(row=1, column=1, sticky='w', padx=(0, 12))

        hint = ttk.Label(dlg, text='', foreground=ui_theme.T['muted'], wraplength=430, justify='left')
        hint.grid(row=2, column=0, columnspan=2, sticky='w', padx=12, pady=(4, 0))

        def upd(*_a):
            k = keys[cb.current()]
            pt = ACTION_TYPES[k][1]
            if pt == 0:
                txt = '该行为没有参数（参数会被忽略）。'
            elif pt == 2:
                txt = ('所属方型 —— 0美国 1韩国 2法国 3德国 4英国 5利比亚\n'
                       '6伊拉克 7古巴 8苏俄 9尤里 10中立 11特殊')
            elif pt == 6:
                txt = '数值型（数量 / 秒数 / 金额等）。'
            else:
                txt = '参数类型 %d（对象类型 ID，见地编编号表）。' % pt
            nt = action_note(k)
            hint.config(text=txt + ('\n' + nt if nt else ''))

        cb.bind('<<ComboboxSelected>>', upd)
        upd()

        def ok():
            try:
                v = int(avar.get().strip() or '0')
            except ValueError:
                messagebox.showwarning('提示', '参数必须是整数', parent=dlg)
                return
            try:
                self.doc.set_action(ac, atype=keys[cb.current()], arg=v)
            except Exception as e:
                messagebox.showerror('出错', str(e), parent=dlg)
                return
            self.refresh_actions()
            self.status.set('行为已改：%s，参数 %d（尚未保存）' % (action_name(ac['type']), ac['arg']))
            dlg.destroy()

        ttk.Button(dlg, text='应用', command=ok).grid(row=3, column=0, padx=12, pady=12)
        ttk.Button(dlg, text='取消', command=dlg.destroy).grid(row=3, column=1, sticky='w', pady=12)
        ent.focus_set()
