# -*- coding: utf-8 -*-
"""事件页（原 `App.events`）

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

from editor_core import EVENT_TYPES
from editor_core import event_name
from editor_core import event_note
from ui.msg import messagebox
from tkinter import ttk
import tkinter as tk
import ui_theme


class EventsPage(object):
    def _tab_events(self, nb):
        f = ttk.Frame(nb, padding=8)
        _ph = ui_theme.page_header(f, '事件', '选中触发对应的事件链')
        _ph.pack(fill='x', padx=2, pady=(2, 8))
        cols = ('idx', 'type', 'name', 'arg', 'argtext', 'owner', 'off')
        # ★2026-10-28：带横竖滚动条（原来只有滚轮，列超宽看不到右半边）
        self._te_wrap, self.te = self._tree_scrolled(f, cols, 16)
        for c, t, w, a in (('idx', '#', 36, 'center'), ('type', '类型', 50, 'center'),
                           ('name', '事件类型', 220, 'w'), ('arg', '参数', 78, 'e'),
                           ('argtext', '参数含义', 108, 'w'), ('owner', '所属触发', 150, 'w'),
                           ('off', '明文位置', 92, 'w')):
            self.te.heading(c, text=t)
            self.te.column(c, width=w, anchor=a)
        # ★2026-10-18：大任务档事件上千条 ⇒ 同款搜索框（按 类型/名称/参数/所属触发/位置 搜）
        qrow = ttk.Frame(f)
        qrow.pack(fill='x', pady=(0, 6))
        ttk.Label(qrow, text='搜索：').pack(side='left')
        self._q_ev = tk.StringVar()
        ent = ttk.Entry(qrow, textvariable=self._q_ev, width=32)
        ent.pack(side='left')
        ent.bind('<KeyRelease>', lambda e: self._fill_ev_tree())
        ent.bind('<Escape>', lambda e: (self._q_ev.set(''), self._fill_ev_tree()))
        ttk.Button(qrow, text='✕', width=3,
                   command=lambda: (self._q_ev.set(''), self._fill_ev_tree())
                   ).pack(side='left', padx=(2, 8))
        self.lbl_ev_count = ttk.Label(qrow, text='', foreground=ui_theme.T['muted'])
        self.lbl_ev_count.pack(side='left')
        self._te_wrap.pack(fill='both', expand=True)      # ★带滚动条的容器（位置同原来的表格）
        self.te.tag_configure('bad', foreground=ui_theme.T['danger'])
        self.te.bind('<Double-1>', lambda e: self.edit_event())
        self.te.bind('<Return>', lambda e: self.edit_event())

        row = ttk.Frame(f)
        row.pack(fill='x', pady=(8, 0))
        ttk.Button(row, text='修改选中事件…', command=self.edit_event).pack(side='left')
        ttk.Button(row, text='设为「永不满足」= 等效禁用',
                   command=self.make_never_satisfied).pack(side='left', padx=6)
        # ★2026-10-22 §193：列表化 —— 同一触发内的事件**上下移**（事件 13 心跳必须首位，
        #   搬动时会警告）与**单条删除**（最后一个不允许删）
        ttk.Button(row, text='↑ 上移', width=7,
                   command=lambda: self.move_sel_event(-1)).pack(side='left', padx=(10, 2))
        ttk.Button(row, text='↓ 下移', width=7,
                   command=lambda: self.move_sel_event(+1)).pack(side='left', padx=2)
        ttk.Button(row, text='🗑 删除该事件',
                   command=self.remove_sel_event).pack(side='left', padx=6)
        ttk.Button(row, text='➕ 添加事件…',
                   command=self.add_event_dialog).pack(side='left', padx=6)
        ttk.Label(row, text='双击一行也可修改', foreground=ui_theme.T['muted']).pack(side='left', padx=10)
        self.lbl_map_ev = ttk.Label(f, text='', foreground=ui_theme.T['muted'], wraplength=880,
                                    justify='left')
        self.lbl_map_ev.pack(anchor='w', pady=(6, 0))
        return f

    def refresh_events(self):
        if not hasattr(self, 'te'):
            return
        self.te.delete(*self.te.get_children())
        self._evlist = []
        self._ev_rows = []
        if not self.doc:
            return
        try:
            evs = self.doc.events()
        except Exception as e:
            self.status.set('事件扫描失败: %s' % e)
            return
        self._evlist = evs
        tnames = {r.sid: r.name for r in self.doc.records}
        # ★运行期归属（无需地图）：触发实例键链（+192=事件自键）
        try:
            ev_own, _ac_own = self.doc.runtime_owners()
        except Exception:
            ev_own = {}
        nbad = 0
        for i, e in enumerate(evs):
            own = e.get('owner')
            rto = ev_own.get(e.get('key'))
            if own:
                tag = '%s %s' % (own, tnames.get(own, ''))
                if e.get('map_type') is not None and \
                        (e['type'], e['arg']) != (e['map_type'], e['map_arg']):
                    tag += ' ⚠与地图不符'
            elif rto:
                tag = '★%s（运行期链）' % rto
            else:
                tag = '—'
            bad = (e['type'] == 0)
            if bad:
                nbad += 1
                # ★§208（玩家实测）：**空事件（类型 0）是无害的**，不必当成危险。
                #   玩家原话：「空的触发没用，但是也无害，不会引起游戏任何问题」。
                tag += ' （空事件·无害）'
            self._ev_rows.append((i, (i, e['type'], e['name'], e['arg'],
                                      e['arg_text'], tag, '0x%X' % e['off']),
                                  ('bad',) if bad else ()))
        self._fill_ev_tree()
        if nbad:
            # ★§208：**不再弹框**。空事件（0「-无事件-」）不会让游戏出问题，玩家实测确认；
            #   只在状态栏说一声，并把行标成「（空事件·无害）」。想清理的可以双击改成正常类型。
            self.status.set('本档有 %d 条事件是「-无事件-」(0)：空事件，引擎不处理、也不影响游戏'
                            '（要清理可双击改成正常类型）' % nbad)
            return
        if getattr(self.doc, '_tables_lowconf', False):
            self.status.set('事件/行为定位未通过校验 —— 请勿按此修改（可能不是事件表）')
        elif not evs:
            self.status.set('该存档未定位到事件表')
        else:
            t = self.doc.time_ticks()
            tinfo = '' if t is None else '　|　「流逝时间」运行期帧数 %d（= %g 秒，%d fps）' % (
                t, t / 15.0, 15)
            self.status.set('扫到 %d 条事件%s%s' % (
                len(evs), '' if self.doc.map_status else '（未挂地图，无归属）', tinfo))

    def _fill_ev_tree(self):
        """按搜索词填事件树（iid 仍是 `_evlist` 下标 ⇒ 选中/修改逻辑不变）。"""
        if not hasattr(self, 'te'):
            return
        self.te.delete(*self.te.get_children())
        q = ((self._q_ev.get() if hasattr(self, '_q_ev') else '') or '').strip().lower()
        rows = getattr(self, '_ev_rows', [])
        shown = 0
        for i, vals, tags in rows:
            if q and not any(q in str(v).lower() for v in vals):
                continue
            self.te.insert('', 'end', iid=str(i), values=vals, tags=tags)
            shown += 1
        if hasattr(self, 'lbl_ev_count'):
            self.lbl_ev_count.config(text='显示 %d / %d 条' % (shown, len(rows)),
                                     foreground=ui_theme.T['muted'])
        # ★2026-10-20：重填（搜索/刷新）后把触发页选中项的高亮补回来
        self._mark_hits(self.te, rows, getattr(self, '_ev_hits', set()))

    def make_never_satisfied(self):
        """把选中的事件改成「永不满足」的条件 —— **等效禁用**该触发。

        ★ 这是"禁用触发"**唯一可靠**的手段（实测有效）：
          触发记录里的启用状态在存档层面改不了（见触发页说明：未生效时那段是活的运行实例数据；
          已结束时能写但引擎不会重建实例），而把事件条件改成永不成立，效果等同禁用。
          做法：类型 → `13 流逝时间`；参数 → `9999999`（≈115 天）。
        """
        if not self.doc or not getattr(self, '_evlist', None):
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.te.selection()
        if not sel:
            messagebox.showwarning('提示', '请先选中要禁用的事件（可 Ctrl 多选）')
            return
        for _iid in sel:
            try:
                if self._guard_executed(self._sid_of_row(self._evlist[int(_iid)])):
                    return
            except Exception:
                pass
        if not messagebox.askyesno(
                '等效禁用',
                '把选中的 %d 条事件改成「永不满足」？\n\n'
                '    类型 → 13 流逝时间\n'
                '    参数 → 9999999（≈115 天）\n\n'
                '这样该触发就永远不会执行（效果等同禁用）。\n\n'
                '⚠ 一个触发通常有多个事件（全部满足才触发）。若只用其中一个当条件，'
                '改它一个就够；若是多条件 AND，请把该触发的事件都改掉。\n'
                '（挂上地图后「所属触发」列会告诉你哪些事件属于同一个触发。）'
                % len(sel), parent=self):
            return
        n = 0
        for iid in sel:
            try:
                ev = self._evlist[int(iid)]
            except Exception:
                continue
            try:
                self.doc.set_event(ev, etype=13, arg=9999999)
                n += 1
            except Exception as e:
                messagebox.showerror('出错', str(e))
        if n:
            self.refresh_events()
            self.status.set('已把 %d 条事件设为「永不满足」（尚未保存）' % n)

    def edit_event(self):
        if not self.doc or not getattr(self, '_evlist', None):
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.te.selection()
        if not sel:
            messagebox.showwarning('提示', '请先选中一条事件')
            return
        ev0 = self._evlist[int(sel[0])]
        if self._guard_executed(self._sid_of_row(ev0)):   # ★所属触发已执行 ⇒ 改不生效
            return
        self._event_dialog(ev0)

    def _event_dialog(self, ev):
        dlg = tk.Toplevel(self)
        dlg.title('修改事件 — 明文位置 0x%X' % ev['off'])
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        # ⚠ 0 = 「-无事件-」：引擎没有该类型的处理器，写进去会让**读档崩溃** ⇒ 下拉框里去掉
        keys = [k for k in sorted(EVENT_TYPES) if k != 0]
        # ★ 该事件所属触发的运行期组件状态（缺 TagNode ⇒ 引擎不执行，改了也白改）
        try:
            _node_off, _inst_off = self.doc.node_of_event(ev)
        except Exception:
            _node_off, _inst_off = None, None
        if _inst_off is not None and _node_off is None:
            rt = tk.StringVar(value='⚠ 该事件所属触发缺运行期组件（TagNode）——'
                                   '引擎不会执行它，修改不会生效。应用后可一键修复。')
            rtc = ui_theme.T['danger']
        else:
            ticks = None
            if _node_off is not None:
                ticks = int.from_bytes(self.doc.plain[_node_off + 80:_node_off + 84],
                                       'little')
            rt = tk.StringVar(value='' if ticks is None else
                              '运行期正常：该触发按 TagNode %d 帧计时（= %g 秒）。'
                              % (ticks, ticks / 15.0))
            rtc = ui_theme.T['muted']

        ttk.Label(dlg, text='事件类型').grid(row=0, column=0, sticky='e', padx=(12, 6), pady=(12, 4))
        cvar = tk.StringVar()
        cb = ttk.Combobox(dlg, textvariable=cvar, width=48, state='readonly',
                          values=['%d - %s' % (k, EVENT_TYPES[k][0]) for k in keys])
        cb.current(keys.index(ev['type']) if ev['type'] in keys else 0)
        cb.grid(row=0, column=1, sticky='w', padx=(0, 12), pady=(12, 4))

        ttk.Label(dlg, text='参数').grid(row=1, column=0, sticky='e', padx=(12, 6))
        avar = tk.StringVar(value=str(ev['arg']))
        ent = ttk.Entry(dlg, textvariable=avar, width=18)
        ent.grid(row=1, column=1, sticky='w', padx=(0, 12))

        ttk.Label(dlg, textvariable=rt, foreground=rtc, wraplength=430,
                  justify='left').grid(row=2, column=0, columnspan=2, sticky='w',
                                       padx=12, pady=(4, 0))

        hint = ttk.Label(dlg, text='', foreground=ui_theme.T['muted'], wraplength=430, justify='left')
        hint.grid(row=3, column=0, columnspan=2, sticky='w', padx=12, pady=(4, 0))

        tk_lbl = ttk.Label(dlg, text='', foreground=ui_theme.T['ok'], wraplength=430, justify='left')
        tk_lbl.grid(row=4, column=0, columnspan=2, sticky='w', padx=12, pady=(4, 10))

        def time_hint(*_a):
            """「流逝时间」的真实落点提示（秒数只是地图副本，引擎按帧计时）。"""
            try:
                k = keys[cb.current()]
                v = int(avar.get().strip() or '0')
            except (ValueError, IndexError, tk.TclError):
                tk_lbl.config(text='')
                return
            if k != 13:
                tk_lbl.config(text='')
                return
            t = None
            if _node_off is not None:
                t = int.from_bytes(self.doc.plain[_node_off + 80:_node_off + 84], 'little')
            if t is None:
                tk_lbl.config(text='⚠ 未找到该触发自己的「运行期帧数」（TagNode）——'
                                   '该触发当前不会被引擎执行；应用后可一键修复。',
                              foreground=ui_theme.T['danger'])
            else:
                tk_lbl.config(text='该触发实际按「%d 帧」计时（= %g 秒，游戏逻辑 %d 帧/秒）；'
                                   '保存时会一并改成 %d 帧。'
                                   % (t, t / 15.0, 15, v * 15), foreground=ui_theme.T['ok'])

        def upd(*_a):
            k = keys[cb.current()]
            pt = EVENT_TYPES[k][1]
            if pt == 0:
                txt = '该事件没有参数（参数会被忽略）。'
            elif pt == 2:
                txt = ('所属方型 —— 0美国 1韩国 2法国 3德国 4英国 5利比亚\n'
                       '6伊拉克 7古巴 8苏俄 9尤里 10中立 11特殊')
            elif pt == 6:
                txt = '数值型（时间秒数 / 金额 / 数量等）。'
            else:
                txt = '参数类型 %d（对象类型 ID，见地编编号表）。' % pt
            nt = event_note(k)
            hint.config(text=txt + ('\n' + nt if nt else ''))
            time_hint()

        cb.bind('<<ComboboxSelected>>', upd)
        ent.bind('<KeyRelease>', time_hint)
        upd()
        if ev['type'] == 0:
            hint.config(text='⚠ 当前类型是「-无事件-」(0)：地编里的占位项，没有任何用处，\n'
                             '实测带它的档读档会崩。请改成正常的事件类型。', foreground=ui_theme.T['danger'])

        def ok():
            try:
                v = int(avar.get().strip() or '0')
            except ValueError:
                messagebox.showwarning('提示', '参数必须是整数', parent=dlg)
                return
            new_type = keys[cb.current()]
            try:
                self.doc.set_event(ev, etype=new_type, arg=v)
            except Exception as e:
                messagebox.showerror('出错', str(e), parent=dlg)
                return
            note = getattr(self.doc, 'time_sync_note', '') or ''
            # ★ 触发缺运行期组件 ⇒ 改了也不会生效：提供一键修复（2026-10-15）
            if note.startswith('⚠') and _inst_off is not None and _node_off is None:
                if messagebox.askyesno(
                        '触发缺少运行期组件', note + '\n\n是否立即补齐（修复）？\n'
                        '（修复 = 补 TagNode + CellTag + 计时表登记，'
                        '帧数按刚改的事件自动算）', parent=dlg):
                    try:
                        info = self.doc.repair_trigger_runtime(_inst_off)
                        self.refresh_records()
                        note = ('已修复：补 TagNode(键 %08X, 帧 %d) + CellTag(键 %08X)'
                                % (info['node_key'], info['ticks'], info['cell_key']))
                        for w in info.get('warnings', []):
                            note += '\n⚠ ' + w
                    except Exception as e:
                        messagebox.showerror('修复失败', str(e), parent=dlg)
                else:
                    note += '（未修复：该触发仍不会被引擎执行）'
            elif note.startswith('⚠'):
                messagebox.showwarning('时间未同步', note, parent=dlg)
            self.refresh_events()
            self.status.set('事件已改：%s，参数 %d（尚未保存）%s'
                            % (event_name(new_type), v,
                               ('　|　' + note.replace('\n', '　')) if note else ''))
            dlg.destroy()

        ttk.Button(dlg, text='应用', command=ok).grid(row=5, column=0, padx=12, pady=12)
        ttk.Button(dlg, text='取消', command=dlg.destroy).grid(row=5, column=1, sticky='w', pady=12)
        ent.focus_set()

    # ---------------- 行为 ----------------
