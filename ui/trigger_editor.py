# -*- coding: utf-8 -*-
"""触发编辑器对话框（原在主程序里的 `TriggerEditor`）。

★2026-10-28（审查 P2-8）：从主程序拆出来 —— 触发页要用它，
  留在主文件里就成了"页面 ↔ 主程序"的循环导入。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, 'core')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from editor_core import ACTION_TYPES
from editor_core import ENGINE_PTYPE_TEXT
from editor_core import EVENT_TYPES
from editor_core import action_name
from editor_core import action_note
from editor_core import action_ptype
from editor_core import event_name
from editor_core import event_note
from editor_core import event_ptype
from tkinter import messagebox
from tkinter import ttk
from ui.const import REPEAT_LABEL
import tkinter as tk
import ui_theme



class TriggerEditor(tk.Toplevel):
    """仿 FA2「触发编辑器」——一个窗口里把一条触发改完（2026-10-20）。

    与截图一致的分区：触发设置 / 事件设置 / 行为设置。
    写入全部走 core 里已定案（或本轮新增并自检过）的接口：
      set_trigger_name / set_trigger_owner / set_trigger_repeat_type /
      set_trigger_flags / set_event / add_action / remove_action /
      create_trigger / copy_trigger / remove_trigger
    ⚠ 所有改动只在内存里，回主窗口「保存」才写盘。
    """

    def __init__(self, app, sid=None, new=False):
        tk.Toplevel.__init__(self, app)
        self.app = app
        self.doc = app.doc
        self.title('触发编辑器（仿地编）')
        self.transient(app)
        # ★2026-10-27：以前写死 880x740，实测内容要 989x813 ⇒ 底部「保存/关闭」整排被裁掉
        #   （玩家截图里根本看不到保存按钮）。改到 `_build()` 之后按内容自适应。
        self.minsize(780, 640)
        self._sids = []
        self._cur = None
        self._new_mode = bool(new)      # 新建模式：下拉先摆成"空白新触发"的默认值
        self._pending_name = None       # 新建模式下"还没写进存档"的名字
        self._build()
        if new and sid is None:      # 新建模式：拿一条"有实例"的触发当展示基准
            for _r in self.doc.records:
                if _r.kind != '触发':
                    continue
                try:
                    if self.doc.trigger_detail(_r.sid)['editable']:
                        sid = _r.sid
                        break
                except Exception:
                    pass
        self.reload(sid)
        self.bind('<Escape>', lambda e: self.destroy())
        if new:
            # ★2026-10-20 玩家反馈：点开就建、关掉也不回滚 ⇒ 改成**惰性创建**：
            #   打开只是进入"新建模式"（摆好默认值），**一个字节都不写**；
            #   真要建，点窗口里的「新建」。
            self._enter_new_mode()
        # ★★2026-10-27：内容宽度要等**列表灌满小队名**之后才定（实测 880→995）
        #   ⇒ 尺寸放到 idle 里再量一次，否则底部「保存/关闭」那排会被裁掉。
        self.after_idle(self._fit_window)

    def _fit_window(self):
        """按内容把窗口缩放到“至少装得下所有控件”（底部保存/关闭按钮永不被裁）。"""
        try:
            self.update_idletasks()
            self.geometry('%dx%d' % (max(880, self.winfo_reqwidth()),
                                     max(740, self.winfo_reqheight())))
        except Exception:
            pass

    # ---------------- 构建 ----------------
    def _build(self):
        pad = dict(padx=6, pady=3)
        # ===== 触发设置 =====
        top = ttk.Labelframe(self, text='触发设置', padding=8)
        top.pack(fill='x', padx=8, pady=(8, 4))
        r0 = ttk.Frame(top)
        r0.pack(fill='x')
        ttk.Label(r0, text='当前触发').pack(side='left')
        self.cb_t = ttk.Combobox(r0, width=44, state='readonly')
        self.cb_t.pack(side='left', padx=6)
        self.cb_t.bind('<<ComboboxSelected>>', lambda e: self._on_pick_trigger())
        ttk.Button(r0, text='新建', width=5, command=self.new_trigger).pack(side='left', padx=2)
        ttk.Button(r0, text='复制', width=5, command=self.copy_trigger).pack(side='left', padx=2)
        ttk.Button(r0, text='删除', width=5, command=self.del_trigger).pack(side='left', padx=2)
        self.btn_reload = ttk.Button(r0, text='↻', width=3, command=lambda: self.reload())
        self.btn_reload.pack(side='left', padx=2)

        r1 = ttk.Frame(top)
        r1.pack(fill='x', pady=(6, 0))
        ttk.Label(r1, text='名称').pack(side='left')
        self.var_name = tk.StringVar()
        e = ttk.Entry(r1, textvariable=self.var_name, width=26)
        self.ent_name = e          # ★§257：新建后自动聚焦到这里（点完就能直接打字改名）
        e.pack(side='left', padx=6)
        e.bind('<Return>', lambda ev: self.apply_name())
        # ★§256（玩家 2026-10-25）：「新触发的名字能不能填入即生效和地编中一样，
        #   不用再点一次改名」⇒ 新建模式下边打边记（`_pending_name`），创建时直接用它。
        self.var_name.trace_add('write', lambda *_a: self._on_name_change())
        ttk.Button(r1, text='改名', command=self.apply_name).pack(side='left')
        ttk.Label(r1, text='  ID：').pack(side='left')
        self.lbl_sid = ttk.Label(r1, text='—', foreground=ui_theme.T['muted'])
        self.lbl_sid.pack(side='left')

        r2 = ttk.Frame(top)
        r2.pack(fill='x', pady=(4, 0))
        ttk.Label(r2, text='国家').pack(side='left')
        self.cb_owner = ttk.Combobox(r2, width=20, state='readonly')
        self.cb_owner.pack(side='left', padx=6)
        ttk.Label(r2, text='重复类型').pack(side='left', padx=(10, 0))
        self.cb_rep = ttk.Combobox(r2, width=18, state='readonly',
                                   values=['0 一次性·任一挂载', '1 一次性·挂载清空',
                                           '2 可重复'])
        self.cb_rep.pack(side='left', padx=6)
        # ★§157：含局部变量事件（36/37）的触发，持久性必须 0（1=永不触发 / 2=反复执行）
        self.lbl_rep_hint = ttk.Label(r2, text='', foreground=ui_theme.T['danger'])
        self.lbl_rep_hint.pack(side='left', padx=(2, 0))
        ttk.Button(r2, text='应用', width=5, command=self.apply_owner_rep).pack(side='left')
        ttk.Label(r2, text='  下级触发').pack(side='left', padx=(12, 0))
        ttk.Label(r2, text='（存档里未定位·待样本）',
                  foreground=ui_theme.T['muted_soft']).pack(side='left')

        r3 = ttk.Frame(top)
        r3.pack(fill='x', pady=(4, 0))
        self.var_dis = tk.BooleanVar()
        self.var_easy = tk.BooleanVar()
        self.var_norm = tk.BooleanVar()
        self.var_hard = tk.BooleanVar()
        ttk.Checkbutton(r3, text='禁用', variable=self.var_dis,
                        command=self.apply_flags).pack(side='left')
        ttk.Checkbutton(r3, text='简单', variable=self.var_easy,
                        command=self.apply_flags).pack(side='left', padx=(12, 0))
        ttk.Checkbutton(r3, text='普通', variable=self.var_norm,
                        command=self.apply_flags).pack(side='left', padx=(8, 0))
        ttk.Checkbutton(r3, text='困难', variable=self.var_hard,
                        command=self.apply_flags).pack(side='left', padx=(8, 0))
        self.lbl_state = ttk.Label(r3, text='', foreground=ui_theme.T['muted'])
        self.lbl_state.pack(side='left', padx=12)
        # ★2026-10-22 §168：引擎只认 CellTag 的剩余挂载数（0x6E53A0 / 0x485250 / 0x485130），
        #   所以"计数漏账"能救回来的唯一办法就是把它改回实际存活数 —— 这里给一个一键入口。
        self.btn_fixcnt = ttk.Button(r3, text='修计数', width=7,
                                     command=self.fix_attach_count)
        self.btn_fixcnt.pack(side='right', padx=(4, 0))
        self.btn_clrflag = ttk.Button(r3, text='清标志', width=7,
                                      command=self.clear_celltag_flags)
        self.btn_clrflag.pack(side='right', padx=(4, 0))

        # ===== 事件设置（§174：可多条，与地编一致） =====
        ev = ttk.Labelframe(self, text='事件设置（可多条 · 同一触发内的多个事件**恒为 AND**）',
                            padding=8)
        ev.pack(fill='x', padx=8, pady=4)
        r4 = ttk.Frame(ev)
        r4.pack(fill='x')
        ttk.Label(r4, text='事件类型').pack(side='left')
        self.cb_ev = ttk.Combobox(r4, width=30, state='readonly')
        self.cb_ev.pack(side='left', padx=6)
        self.cb_ev.bind('<<ComboboxSelected>>', lambda e: self._ev_hint())
        ttk.Label(r4, text='参数').pack(side='left', padx=(8, 0))
        self.var_evarg = tk.StringVar()
        # ★2026-10-20：「所属方型」参数改成下拉（0 = 玩家自己，其余按存档阵营顺序）
        self.cb_evarg = ttk.Combobox(r4, textvariable=self.var_evarg, width=18)
        self.cb_evarg.pack(side='left', padx=6)
        ttk.Button(r4, text='改选中', width=6, command=self.apply_event).pack(side='left')
        ttk.Button(r4, text='＋添加', width=7,
                   command=self.add_event_gui).pack(side='left', padx=2)
        ttk.Button(r4, text='删除', width=5,
                   command=self.del_event_gui).pack(side='left', padx=2)
        self.lbl_evcnt = ttk.Label(r4, text='', foreground=ui_theme.T['muted'])
        self.lbl_evcnt.pack(side='left', padx=8)

        evbody = ttk.Frame(ev)
        evbody.pack(fill='x', pady=(6, 0))
        ecols = ('idx', 'type', 'name', 'arg', 'argtext')
        self.tv_ev = ttk.Treeview(evbody, columns=ecols, show='headings', height=3)
        for c, t, w, a in (('idx', '#', 30, 'center'), ('type', '类型', 44, 'center'),
                           ('name', '事件', 230, 'w'), ('arg', '参数', 66, 'e'),
                           ('argtext', '参数含义', 170, 'w')):
            self.tv_ev.heading(c, text=t)
            self.tv_ev.column(c, width=w, anchor=a)
        self.tv_ev.pack(fill='x')
        self.tv_ev.bind('<<TreeviewSelect>>', lambda e: self._ev_pick())
        self.lbl_evhint = ttk.Label(ev, text='', foreground=ui_theme.T['ink_2'], wraplength=820,
                                    justify='left')
        self.lbl_evhint.pack(anchor='w', pady=(4, 0))

        # ===== 行为设置 =====
        ac = ttk.Labelframe(self, text='行为设置（可多条，按顺序执行）', padding=8)
        ac.pack(fill='both', expand=True, padx=8, pady=(4, 8))
        r5 = ttk.Frame(ac)
        r5.pack(fill='x')
        ttk.Label(r5, text='行为类型').pack(side='left')
        self.cb_ac = ttk.Combobox(r5, width=34, state='readonly')
        self.cb_ac.pack(side='left', padx=6)
        self.cb_ac.bind('<<ComboboxSelected>>', lambda e: self._ac_hint())
        ttk.Label(r5, text='参数').pack(side='left', padx=(8, 0))
        self.var_acarg = tk.StringVar()
        self.cb_acarg = ttk.Combobox(r5, textvariable=self.var_acarg, width=20)
        self.cb_acarg.pack(side='left', padx=6)
        ttk.Button(r5, text='改选中', width=6, command=self.apply_action).pack(side='left')
        ttk.Button(r5, text='添加', width=5, command=self.add_action).pack(side='left', padx=2)
        ttk.Button(r5, text='复制', width=5, command=self.dup_action).pack(side='left', padx=2)
        ttk.Button(r5, text='删除', width=5, command=self.del_action).pack(side='left', padx=2)

        # ★§255：行为 80/107（援军 / 超时空援军）专用行 —— 小队在「参数」下拉里选，
        #   这里填**路径点号**（地编里 80 也是"小队类型 + 路径点"两个字段）。
        self.var_wp = tk.StringVar()
        self.row_wp = ttk.Frame(ac)
        ttk.Label(self.row_wp, text='路径点').pack(side='left')
        ttk.Entry(self.row_wp, textvariable=self.var_wp, width=8).pack(side='left', padx=6)
        ttk.Label(self.row_wp, text='（上面的「参数」= 小队，这里 = 刷出来的位置；留空 = 0）',
                  foreground=ui_theme.T['muted']).pack(side='left')

        body = ttk.Frame(ac)
        body.pack(fill='both', expand=True, pady=(6, 0))
        self.frm_ac_body = body
        cols = ('idx', 'type', 'name', 'arg', 'argtext')
        self.tv_ac = ttk.Treeview(body, columns=cols, show='headings', height=8)
        for c, t, w, a in (('idx', '#', 34, 'center'), ('type', '类型', 46, 'center'),
                           ('name', '行为类型', 240, 'w'), ('arg', '参数', 70, 'e'),
                           ('argtext', '参数含义', 150, 'w')):
            self.tv_ac.heading(c, text=t)
            self.tv_ac.column(c, width=w, anchor=a)
        self.tv_ac.pack(fill='both', expand=True)
        self.tv_ac.bind('<<TreeviewSelect>>', lambda e: self._ac_pick())
        self.lbl_achint = ttk.Label(ac, text='', foreground=ui_theme.T['ink_2'], wraplength=820,
                                    justify='left')
        self.lbl_achint.pack(anchor='w', pady=(4, 0))

        bar = ttk.Frame(self)
        bar.pack(fill='x', padx=8, pady=(0, 8))
        # ★2026-10-20 玩家反馈"没有保存按钮"：底部提示文字太长，把右边的按钮挤没了。
        #   现在**按钮先占位**（右边固定框），文字再填剩下的宽度并自动换行。
        btnbox = ttk.Frame(bar)
        btnbox.pack(side='right')
        ttk.Button(btnbox, text='关闭', width=8, command=self.destroy).pack(side='right')
        # ★2026-10-27 玩家要求「编辑完可以点保存」：直接从这儿把整个存档写盘
        #   （走主窗口同一条路：自动备份到 _备份存档、文件名过长会先提醒）
        ttk.Button(btnbox, text='💾 保存到文件', width=14,
                   command=self.save_to_file).pack(side='right', padx=(0, 2))
        ttk.Button(btnbox, text='✔ 保存该触发', width=14,
                   command=self.save_doc).pack(side='right', padx=(0, 8))
        ttk.Label(bar, text='改动先落在存档内存里（新建模式=真正创建这条触发）；'
                            '要写到文件请回主窗口「保存」。删除无法撤销，建议先备份。',
                  foreground=ui_theme.T['warn'], wraplength=520, justify='left'
                  ).pack(side='left', fill='x', expand=True)

    # ---------------- 新建模式（惰性：不点「新建」就不写存档） ----------------
    def _enter_new_mode(self):
        """进入"新建模式"= 一张**空白表单**（页面上的东西一个字节都不写）。

        ★2026-10-20 玩家要求（第三轮）：
          · 当前触发下拉显示「（新建·尚未保存）」，不再挂着某条真实触发；
          · 行为列表**清空**（以前会把基准触发的行为照抄在下面，看着像"已经加好了"）；
          · 事件/行为都先记在内存的"待建清单"里，点「✔ 保存该触发」才真正 create。
        """
        self._new_mode = True
        n = 1
        names = set()
        for r in self.doc.records:
            if r.kind == '触发':
                names.add(r.name)
        while ('新触发%d' % n) in names:
            n += 1
        self._pending_name = '新触发%d' % n
        self._pending_events = [(8, 0)]   # 事件清单（§174：可多条，默认 8 任何事件/参数 0）
        self._pending_actions = []        # 行为：空白起步，点「添加」才会有一条
        self._cur = None
        self._det = {'actions': [], 'event': None}
        self._load_new_form()

    def _load_new_form(self):
        """把"待建清单"画到界面上（新建模式专用，不读任何真实触发）。"""
        self.cb_t.set('（新建·尚未保存）')
        self.lbl_sid.config(text='（新建·尚未写入存档）', foreground=ui_theme.T['warn'])
        self.var_name.set(getattr(self, '_pending_name', '新触发'))
        # 国家默认游戏里当前玩家阵营
        try:
            hs = [h.get('cn') or h.get('short') for h in (self.doc.houses() or [])]
            self.cb_owner['values'] = hs
            me = self.doc.player_house()
            self.cb_owner.set(me if me in hs else (hs[0] if hs else ''))
        except Exception:
            pass
        self.cb_rep.current(0)
        self.lbl_rep_hint.config(text='')
        self.var_dis.set(False)
        self.var_easy.set(True)
        self.var_norm.set(True)
        self.var_hard.set(True)
        # 事件
        keys = [k for k in sorted(EVENT_TYPES) if k != 0]
        self._ev_keys = keys
        self.cb_ev['values'] = ['%d  %s' % (k, EVENT_TYPES[k][0]) for k in keys]
        pend = getattr(self, '_pending_events', [(8, 0)])
        et, ea = pend[0] if pend else (8, 0)
        if et in keys:
            self.cb_ev.current(keys.index(et))
        self.var_evarg.set(str(ea))
        # 行为
        akeys = [k for k in sorted(ACTION_TYPES) if k != 0]
        self._ac_keys = akeys
        self.cb_ac['values'] = ['%d  %s' % (k, ACTION_TYPES[k][0]) for k in akeys]
        self.cb_ac.set('')
        self.var_acarg.set('')
        self._fill_events()
        self._fill_actions()
        self._ev_hint()
        self._ac_hint()
        self.lbl_state.config(foreground=ui_theme.T['warn'],
                              text='新建模式（未写入存档）：填名称 / 选事件 / 加行为 → 点「✔ 保存该触发」；'
                                   '直接关闭 = 什么都不发生')
        self.app.status.set('新建触发：现在还没写进存档，点「✔ 保存该触发」才创建')

    def _blocked_in_new(self):
        """新建模式还没落盘时，禁止改"当前触发"的其它字段（免得改到基准那条）。"""
        if getattr(self, '_new_mode', False):
            messagebox.showinfo(
                '新建模式', '这条触发**还没建出来**（存档里没有它）。\n\n'
                '把名称/事件类型/行为类型选好后，点左上角「新建」创建；\n'
                '建好之后再改禁用、难度、国家、行为等其它字段。', parent=self)
            return True
        return False

    def _on_pick_trigger(self):
        """下拉里选东西：选到真实触发就退出"新建模式"（新建表单会被丢掉）。"""
        txt = self.cb_t.get()
        if getattr(self, '_new_mode', False) and not txt.startswith('（新建'):
            self._new_mode = False
            self._pending_actions = []
            self.app.status.set('已退出新建模式（未创建任何触发）')
        if txt.startswith('（新建'):
            self._load_new_form()
            return
        self.reload()

    # ---------------- 载入 ----------------
    def reload(self, sid=None, keep=None):
        try:
            self.doc = self.app.doc
            self._sids = []
            vals = []
            for r in self.doc.records:
                if r.kind != '触发':
                    continue
                self._sids.append(r.sid)
                vals.append('%s  %s' % (r.sid, r.name))
            if getattr(self, '_new_mode', False):
                # 新建模式：下拉第一项是占位，别显示成"正在编辑某条真实触发"
                self.cb_t['values'] = ['（新建·尚未保存）'] + vals
                self.cb_t.current(0)
                self._load_new_form()
                return
            self.cb_t['values'] = vals
            if sid is None:
                sid = keep or self._cur
            if sid in self._sids:
                i = self._sids.index(sid)
            else:
                i = 0
            self.cb_t.current(i)
            self._load_one()
        except Exception as e:
            messagebox.showerror('载入失败', str(e), parent=self)

    def _load_one(self):
        if getattr(self, '_new_mode', False):
            self._load_new_form()
            return
        i = self.cb_t.current()
        if i < 0 or i >= len(self._sids):
            return
        sid = self._sids[i]
        self._cur = sid
        det = self.doc.trigger_detail(sid)
        self._det = det
        self.lbl_sid.config(text=sid, foreground=ui_theme.T['muted'])
        self.var_name.set(det['name'])
        # 国家
        hs = [h.get('cn') or h.get('short') for h in (self.doc.houses() or [])]
        self.cb_owner['values'] = hs
        if det['owner'] in hs:
            self.cb_owner.set(det['owner'])
        else:
            self.cb_owner.set(det['owner'] or '（读不出）')
        # 重复类型
        rp = det['repeat'] or '0'
        self.cb_rep.current(int(rp) if rp in ('0', '1', '2') else 0)
        # ★§158：持久性 1 是"计数型"，两种坑要实时提示
        tip, col = '', ui_theme.T['danger']
        try:
            natt = det['attached'] or 0
            etypes = [e['type'] for e in self.doc.trigger_event_list(sid)]
            if 1 in etypes and natt >= 2:
                tip = '⚠ 进入事件挂 %d 个对象：持久1 是已知 BUG（永不执行）' % natt
            elif natt == 0:
                tip = '⚠ 挂接 0 个对象：持久1 永不触发'
                col = ui_theme.T['muted']
            elif len([t for t in etypes if t != 8]) > 1 and rp == '1':
                # ★§163/§166：离场记账被"还没满足的其它事件"卡住（8 任何事件恒真，不算）
                tip = ('⚠ 多条事件 + 挂 %d 个对象：摧毁/变卖必须发生在其它事件满足**之后**，'
                       '否则那几次离场不记账 ⇒ 永不执行' % natt)
        except Exception:
            tip = ''
        self.lbl_rep_hint.config(text=tip, foreground=col)
        # 标志
        self.var_dis.set(bool(det['disabled']))
        self.var_easy.set(bool(det['easy']))
        self.var_norm.set(bool(det['normal']))
        self.var_hard.set(bool(det['hard']))
        state = '有运行期实例' if det['editable'] else '只有定义（只读）'
        if det['has_node']:
            state += ' · TagNode ✓'
        if det['has_cell']:
            state += ' · CellTag ✓（挂接 %d 栋）' % det['attached']
        if det['ticks']:
            state += ' · 计时 %g 秒' % (det['ticks'] / 15.0)
        # ★§159/§160：运行期状态（CellTag 节点消失 = 已执行的唯一可靠指纹）
        try:
            _rs = self.doc.trigger_runtime_state(sid)
            if _rs['has_tag']:
                if _rs.get('suppress'):
                    # ★§168：CellTag +0x34 非 0 ⇒ 0x6E53A0 一进门就 return，连计数都不动
                    state += (' · ⚠ 标签被抑制（+0x34 = %d ⇒ 引擎静默跳过，永不执行）'
                              % _rs['suppress'])
                elif _rs['leak'] and _rs['leak'] > 0:
                    # ★§163：计数 > 实际存活 ⇒ 有离场发生在其它事件满足之前，引擎没记账
                    state += (' · ⚠ 计数漏账 %d（还写着 %d 个 / 实际存活建筑 %d 个 ⇒ 永不执行）'
                              % (_rs['leak'], _rs['remain'], _rs['live']))
                elif _rs['executed']:
                    state += ' · ✔ 已执行（挂载数归零、标签已销毁）'
                else:
                    state += (' · 未执行（节点还在，剩余挂载 %s）'
                              % (_rs['remain'] if _rs['remain'] is not None else '—'))
                if _rs['ev_fired']:
                    state += '·事件已满足'
                # 持久 1 才有意义：把"还差几个"直接摆出来
                if (det.get('repeat') == '1' and not _rs['executed']
                        and not _rs.get('suppress')
                        and not (_rs['leak'] and _rs['leak'] > 0)
                        and isinstance(_rs['remain'], int)):
                    state += '·持久1 还差 %d 个对象离场' % _rs['remain']
        except Exception:
            pass
        self.lbl_state.config(text=state)
        # 事件下拉（选项 = 编号 + 名称）
        # ★2026-10-20：0 = 地编占位项「-无事件-」，写进存档会崩 ⇒ 下拉里**直接不列**
        keys = [k for k in sorted(EVENT_TYPES) if k != 0]
        self._ev_keys = keys
        self.cb_ev['values'] = ['%d  %s' % (k, EVENT_TYPES[k][0]) for k in keys]
        self.cb_ev.current(keys.index(8) if 8 in keys else 0)   # 默认「8 任何事件」
        evd = det['event']
        if evd:
            if evd['type'] in keys:
                self.cb_ev.current(keys.index(evd['type']))
            else:                   # 事件类型 0（占位）等异常值：只显示、不动下拉
                self.cb_ev.set('%d  %s' % (evd['type'], evd['name']))
            self.var_evarg.set(str(evd['arg']))
        else:
            self.cb_ev.set('—')
            self.var_evarg.set('')
        # 行为下拉 + 列表
        akeys = [k for k in sorted(ACTION_TYPES) if k != 0]
        self._ac_keys = akeys
        self.cb_ac['values'] = ['%d  %s' % (k, ACTION_TYPES[k][0]) for k in akeys]
        # ★2026-10-20 玩家要求：**行为类型默认留空**（以前预选 21 让人以为"只能新增一条"）。
        #   选中下面列表的某一行会自动回填，改完点「改选中」即可原地改。
        self.cb_ac.set('')
        self.var_acarg.set('')
        if getattr(self, '_new_mode', False):
            # 新建模式下不继承"当前触发"的事件/行为选择 —— 摆成 8 任何事件 + 21 播放语音
            if 8 in self._ev_keys:
                self.cb_ev.current(self._ev_keys.index(8))
            self.var_evarg.set('0')
            self.cb_ac.set('')
            self.var_acarg.set('')
        self._fill_events()
        self._fill_actions()
        self._ev_hint()
        self._ac_hint()

    # ---------------- 事件列表（§174：多事件） ----------------
    def _fill_events(self):
        """把该触发的事件链画到列表里 —— **从上到下 = 引擎评估顺序**（链头在前）。

        §155/§163：存档里事件是**链头插逆序**，链头 = 地编列表最后一条；
        引擎按链序短路 ⇒ **最上面那条不满足，下面的根本不看**。
        所以这里按链序显示，并在标题里写清楚"任一不满足就短路"。
        """
        try:
            self.tv_ev.delete(*self.tv_ev.get_children())
        except Exception:
            return
        self._ev_rows = []
        if getattr(self, '_new_mode', False):
            for i, (t, a) in enumerate(getattr(self, '_pending_events', []), 1):
                try:
                    at = self.doc._arg_text(event_ptype(t), a)
                except Exception:
                    at = ''
                self.tv_ev.insert('', 'end', iid=str(i), values=(
                    i, t, event_name(t), a, at))
                self._ev_rows.append({'key': None, 'type': t, 'arg': a})
            self._ev_finish()
            return
        if not self._cur:
            self._ev_finish()
            return
        try:
            ch = self.doc.trigger_event_list(self._cur)
        except Exception:
            ch = []
        for i, e in enumerate(ch, 1):
            try:
                at = self.doc._arg_text(event_ptype(e['type']), e['arg'])
            except Exception:
                at = ''
            self.tv_ev.insert('', 'end', iid=str(i), values=(
                i, e['type'], event_name(e['type']), e['arg'], at))
            self._ev_rows.append({'key': e.get('key'), 'type': e['type'],
                                  'arg': e['arg']})
        self._ev_finish()

    def _ev_finish(self):
        """列表重建后的收尾：更新计数 + 选中第一行（= 链头 = 引擎最先评估的那条）。"""
        self._ev_count_hint()
        try:
            kids = self.tv_ev.get_children()
            if kids:
                self.tv_ev.selection_set(kids[0])
                self.tv_ev.focus(kids[0])
        except Exception:
            pass

    def _ev_count_hint(self):
        n = len(getattr(self, '_ev_rows', []) or [])
        if n <= 1:
            txt, col = ('共 %d 条' % n, ui_theme.T['muted'])
        else:
            txt, col = ('共 %d 条（AND：任一不满足即短路，上面先评估）' % n, ui_theme.T['warn'])
        try:
            self.lbl_evcnt.config(text=txt, foreground=col)
        except Exception:
            pass

    def _ev_pick(self):
        """选中列表某行 → 把类型/参数回填到上面的编辑控件（与行为列表同样的手感）。"""
        sel = self.tv_ev.selection()
        if not sel:
            return
        try:
            row = int(sel[0]) - 1
        except ValueError:
            return
        rows = getattr(self, '_ev_rows', [])
        if not (0 <= row < len(rows)):
            return
        r = rows[row]
        if r['type'] in self._ev_keys:
            self.cb_ev.current(self._ev_keys.index(r['type']))
        else:
            self.cb_ev.set('%d  %s' % (r['type'], event_name(r['type'])))
        self.var_evarg.set(str(r['arg']))
        self._ev_hint()

    def add_event_gui(self):
        """＋添加：新建模式先记在清单里，真实模式直接写进存档（链头插）。"""
        i = self.cb_ev.current()
        if i < 0 or i >= len(self._ev_keys):
            messagebox.showwarning('提示', '先在左边选一个事件类型', parent=self)
            return
        try:
            arg = self._parse_arg(self.var_evarg.get())
        except ValueError:
            messagebox.showwarning('提示', '参数必须是整数', parent=self)
            return
        t = self._ev_keys[i]
        if getattr(self, '_new_mode', False):
            self._pending_events = list(getattr(self, '_pending_events', [])) + [(t, arg)]
            self._fill_events()
            self.app.status.set('新建触发：已加事件 %s（参数 %d），共 %d 条 —— 还没写入存档'
                                % (event_name(t), arg, len(self._pending_events)))
            return
        if not self._cur:
            return
        try:
            self.doc.add_event(self._cur, t, arg)
        except Exception as e:
            messagebox.showerror('添加失败（存档未改动）', str(e), parent=self)
            return
        self.after_change('已加事件 %s（参数 %d）' % (event_name(t), arg))

    def del_event_gui(self):
        """删除选中事件（只剩 1 条时拒绝 —— 没有事件的触发引擎不评估）。"""
        sel = self.tv_ev.selection()
        if not sel:
            messagebox.showinfo('提示', '先在下面列表里选中要删的那条事件', parent=self)
            return
        row = int(sel[0]) - 1
        rows = getattr(self, '_ev_rows', [])
        if not (0 <= row < len(rows)):
            return
        if len(rows) <= 1:
            messagebox.showwarning(
                '不能删', '这条触发只剩 1 个事件了。\n\n'
                '没有事件的触发引擎不会评估 —— 要让它失效请用「禁用」或软删除。',
                parent=self)
            return
        r = rows[row]
        if getattr(self, '_new_mode', False):
            pend = list(getattr(self, '_pending_events', []))
            del pend[row]
            self._pending_events = pend
            self._fill_events()
            self.app.status.set('新建触发：已移除 1 条事件（共 %d 条）' % len(pend))
            return
        if not self._cur or not r.get('key'):
            return
        if not messagebox.askyesno(
                '确认删除这条事件？',
                '将从触发里删掉「%s」（参数 %d）。\n\n'
                '其余事件不动；删除无法撤销。' % (event_name(r['type']), r['arg']),
                parent=self):
            return
        try:
            self.doc.remove_event(self._cur, r['key'])
        except Exception as e:
            messagebox.showerror('删除失败（存档未改动）', str(e), parent=self)
            return
        self.after_change('已删事件 %s' % event_name(r['type']))

    def _ac_text(self, atype, arg, team=0, waypoint=0):
        """「参数含义」列的文字：80/107 显示「小队名 · 路径点」。"""
        if self.doc.is_team_action(atype):
            nm = next((lab for lab, k in self._team_choices() if k == team), '')
            nm = nm or ('小队 %08X' % team if team else '（没选小队）')
            if atype in self.doc.SPAWN_ACTIONS:
                return '%s · 路径点 %s' % (nm, waypoint)
            return nm
        return self.doc._arg_text(action_ptype(atype), arg)

    def _fill_actions(self):
        self.tv_ac.delete(*self.tv_ac.get_children())
        if getattr(self, '_new_mode', False):
            for i, it in enumerate(getattr(self, '_pending_actions', []), 1):
                if isinstance(it, dict):
                    t, a = int(it['type']), int(it.get('arg') or 0)
                    tm, wp = it.get('team') or 0, it.get('waypoint') or 0
                else:
                    t, a = int(it[0]), int(it[1])
                    tm, wp = 0, 0
                self.tv_ac.insert('', 'end', iid=str(i), values=(
                    i, t, action_name(t), wp if t in self.doc.SPAWN_ACTIONS else a,
                    self._ac_text(t, a, tm, wp)))
            return
        for i, a in enumerate(self._det.get('actions') or [], 1):
            # ★§219.8：援军行为 80/107 的参数在 +0x44（小队键）/+0x58（路径点），
            #   不是默认的 +0x48（恒 0）。参数列显示路径点，含义列显示小队+路径点。
            argcol = a['arg']
            if a.get('type') in self.doc.SPAWN_ACTIONS:
                argcol = a.get('waypoint', 0)
            elif self.doc.is_team_action(a.get('type')):
                # ★§283.9：小队型（4/5/7）参数列显示**小队 ID**（不是 0，避免看着像空的）
                _tk = a.get('team_key') or a.get('team') or 0
                argcol = next((t['sid'] for t in (self.doc.team_types() or [])
                               if t.get('key') == _tk), _tk)
            self.tv_ac.insert('', 'end', iid=str(i), values=(
                i, a['type'], a['name'], argcol,
                self._ac_text(a['type'], a['arg'],
                              a.get('team') or a.get('team_key') or 0,
                              a.get('waypoint') or 0)))

    # ---------------- 触发设置 ----------------
    def apply_name(self):
        # ★新建模式下 _cur 是 None（还没有真实触发），但名字仍要能记下来
        if getattr(self, '_new_mode', False):
            nm = self.var_name.get().strip()
            if not nm:
                messagebox.showwarning('提示', '名字不能为空', parent=self)
                return
            self._pending_name = nm[:16]
            self.app.status.set('新建名已记为 %r（还没写入存档，点「新建」才生效）'
                                % self._pending_name)
            return
        try:
            self.doc.set_trigger_name(self._cur, self.var_name.get())
        except Exception as e:
            messagebox.showerror('改名失败', str(e), parent=self)
            return
        self.after_change('已改名：%s' % self.var_name.get())

    def apply_owner_rep(self):
        if self._blocked_in_new():
            return
        if not self._cur:
            return
        det = self._det
        rec = self.doc.trigger_rec(self._cur)
        msgs = []
        try:
            owner = self.cb_owner.get()
            if owner and owner != det['owner']:
                self.doc.set_trigger_owner(rec, owner)
                msgs.append('国家 → %s' % owner)
            rep = str(self.cb_rep.current())
            if rep in ('0', '1', '2') and rep != (det['repeat'] or ''):
                # ★§157：含局部变量事件（36/37）的触发，1 = 永不触发、2 = 反复执行
                tip = self.doc.repeat_pitfall(self._cur, rep)
                if tip and not messagebox.askyesno(
                        '⚠ 持久性有坑', tip + '\n\n仍要写 %s 吗？' % rep, parent=self):
                    return
                self.doc.set_trigger_repeat_type(self._cur, int(rep))
                msgs.append('触发类型 → %s' % REPEAT_LABEL[int(rep)])
        except Exception as e:
            messagebox.showerror('写入失败', str(e), parent=self)
            return
        self.after_change('；'.join(msgs) or '无改动')

    # ---------------- 运行期组件修复（§168） ----------------
    def fix_attach_count(self):
        """把 CellTag 的剩余挂载数改回「实际还活着的挂载建筑数」。

        依据（§168 反汇编）：引擎在"对象离场"时只做一件事 —— 若这条触发的**全部事件
        都已满足**，就把该对象身上的 CellTag 摘掉、计数 −1；不满足就什么也不记（对象已
        消失 ⇒ 永久丢账）。所以计数 > 实际存活数就是死档，唯一救法是把计数压回实际值，
        让剩下的每一次离场都能 −1，减到 1 的那次执行。
        """
        if self._blocked_in_new() or not self._cur:
            return
        try:
            st = self.doc.trigger_runtime_state(self._cur)
        except Exception as e:
            messagebox.showerror('读取失败', str(e), parent=self)
            return
        if not st['has_tag'] or st['executed']:
            messagebox.showinfo('无需修复',
                                '这条触发在本档里没有可改的 CellTag（已执行 / 只有定义）。',
                                parent=self)
            return
        if st['live'] is None:
            messagebox.showinfo('统计不到',
                                '统计不到存活的挂载建筑（标签挂的可能是单位或其它对象）。\n'
                                '请用下面的数字框手动指定。', parent=self)
            return
        r = self.doc.repair_attach_leak(self._cur)
        if not r['ok']:
            messagebox.showwarning('救不回来', r['text'], parent=self)
            return
        self.after_change('修计数：%s' % r['text'])
        messagebox.showinfo('已修复', r['text'], parent=self)
        try:
            self._refresh_state_only()
        except Exception:
            pass

    def clear_celltag_flags(self):
        """把 CellTag 的抑制位 `+0x34` / 重入残留 `+0x35` 清零（正常档本来就是 0）。"""
        if self._blocked_in_new() or not self._cur:
            return
        try:
            fl = self.doc.celltag_flags(self._cur)
        except Exception as e:
            messagebox.showerror('读取失败', str(e), parent=self)
            return
        if fl is None:
            messagebox.showinfo('无需修复', '这条触发在本档里没有 CellTag 节点。', parent=self)
            return
        if (fl['suppress'], fl['busy']) == (0, 0):
            messagebox.showinfo('本来就是 0',
                                '抑制位 / 重入位都已经是 0，不用改。', parent=self)
            return
        if not messagebox.askyesno(
                '清零标志字节',
                '抑制位 = %d、重入位 = %d。\n'
                '抑制位非 0 会让引擎一进门就 return（这条触发彻底失效）。\n'
                '确定清零吗？' % (fl['suppress'], fl['busy']), parent=self):
            return
        try:
            r = self.doc.clear_celltag_flags(self._cur)
        except Exception as e:
            messagebox.showerror('写入失败', str(e), parent=self)
            return
        self.after_change('清 CellTag 标志 %s → (0, 0)' % (r['old'],))

    def _refresh_state_only(self):
        """只重刷顶部那一行状态（不重建整个触发列表，保持用户当前选中）。"""
        try:
            det = self.doc.trigger_detail(self._cur)
            rs = self.doc.trigger_runtime_state(self._cur)
        except Exception:
            return
        state = '有运行期实例' if det['editable'] else '只有定义（只读）'
        if det['has_node']:
            state += ' · TagNode ✓'
        if det['has_cell']:
            state += ' · CellTag ✓（挂接 %d 栋）' % det['attached']
        vmap = {'executed': ' · ✔ 已执行（挂载数归零、标签已销毁）',
                'no_tag': ' · 本档无标签对象',
                'suppressed': ' · ⚠ 标签被抑制（+0x34 非 0 ⇒ 引擎静默跳过）'}
        if rs['verdict'] in vmap:
            state += vmap[rs['verdict']]
        elif rs['verdict'] == 'dead':
            state += (' · ⚠ 计数漏账 %d（还写着 %d 个 / 实际存活建筑 %d 个 ⇒ 永不执行）'
                      % (rs['leak'], rs['remain'], rs['live']))
        else:
            state += ' · 未执行（节点还在，剩余挂载 %s）' % rs['remain']
            if det.get('repeat') == '1' and isinstance(rs['remain'], int):
                state += '·持久1 还差 %d 个对象离场' % rs['remain']
        self.lbl_state.config(text=state)

    def apply_flags(self):
        if self._blocked_in_new():
            return
        if not self._cur:
            return
        try:
            self.doc.set_trigger_flags(self._cur, disabled=self.var_dis.get(),
                                       easy=self.var_easy.get(),
                                       normal=self.var_norm.get(),
                                       hard=self.var_hard.get())
        except Exception as e:
            messagebox.showerror('写入失败', str(e), parent=self)
            return
        self.after_change('禁用=%s 简单=%s 普通=%s 困难=%s'
                          % (self.var_dis.get(), self.var_easy.get(),
                             self.var_norm.get(), self.var_hard.get()))

    # ---------------- 事件 ----------------
    @staticmethod
    def _parse_arg(txt):
        """把参数框里的文本转成整数：允许 "8" 或下拉里的 "8 古巴"（取前导数字）。"""
        t = (txt or '').strip()
        if not t:
            return 0
        head = t.split()[0]
        return int(head)

    def _fill_arg_values(self, combo, ptype, cur):
        """按参数类型给参数下拉填候选值（所属方型 = 阵营顺序，0 恒为玩家自己）。"""
        try:
            if ptype == 2:
                hs = self.doc.house_order() or []
                vals = ['%d %s%s' % (i, (h['cn'] or h['short']),
                                     '（玩家）' if h['is_player'] else '')
                        for i, h in enumerate(hs)]
                combo['values'] = vals
                if cur is not None and 0 <= cur < len(vals):
                    combo.set(vals[cur])       # 显示成 "0 苏俄（玩家）"
            else:
                combo['values'] = []           # 其它类型：自由填数字
        except Exception:
            combo['values'] = []

    def _ev_hint(self):
        i = self.cb_ev.current()
        if i < 0:
            self.lbl_evhint.config(text='该触发没有可编辑的事件')
            return
        k = self._ev_keys[i]
        pt = EVENT_TYPES[k][1]
        try:
            cur = self._parse_arg(self.var_evarg.get())
        except Exception:
            cur = None
        self._fill_arg_values(self.cb_evarg, pt, cur)
        if pt == 2:
            hs = self.doc.house_order() or []
            t = ('所属方型：0 = 玩家自己（本档 %s），其余按存档阵营顺序 —— %s'
                 % (next((h['cn'] for h in hs if h['is_player']), '?'),
                    ' / '.join('%d %s' % (j, h['cn'] or h['short'])
                               for j, h in enumerate(hs[:6])) + ' …'))
        elif pt == 0:
            t = '没有参数（参数会被忽略）'
        elif pt == 6:
            t = '数值型（秒数 / 金额 / 数量）'
        else:
            t = '参数类型 %d（对象类型 ID）' % pt
        nt = event_note(k) or ''
        self.lbl_evhint.config(text=t + ('　' + nt if nt else ''))

    def apply_event(self):
        """「改选中」：把编辑控件里的类型/参数写到**列表里选中的那条事件**上。

        ★§174：一个触发可以有多条事件（AND）。要再添一条请点「＋添加」——
          以前这里是"改唯一那条"，有了多事件以后必须明确"改哪一条"。
        """
        i = self.cb_ev.current()
        if i < 0 or i >= len(self._ev_keys):
            messagebox.showwarning('提示', '先在左边选一个事件类型', parent=self)
            return
        try:
            arg = self._parse_arg(self.var_evarg.get())
        except ValueError:
            messagebox.showwarning('提示', '参数必须是整数', parent=self)
            return
        t = self._ev_keys[i]
        sel = self.tv_ev.selection()
        if not sel:
            messagebox.showinfo('提示',
                                '先在下面列表里**选中要改的那条事件**。\n\n'
                                '要给触发再添一个条件，请点「＋添加」。', parent=self)
            return
        try:
            row = int(sel[0]) - 1
        except ValueError:
            return
        rows = getattr(self, '_ev_rows', [])
        if not (0 <= row < len(rows)):
            return
        if getattr(self, '_new_mode', False):
            pend = list(getattr(self, '_pending_events', []))
            pend[row] = (t, arg)
            self._pending_events = pend
            self._fill_events()
            self.app.status.set('新建触发：第 %d 条事件 → %s（参数 %d）—— 还没写入存档'
                                % (row + 1, event_name(t), arg))
            return
        key = rows[row].get('key')
        ev = next((e for e in self.doc.events() if e.get('key') == key), None)
        if ev is None:
            messagebox.showinfo('提示', '找不到这条事件对象（存档可能已变化，请点 ↻）',
                                parent=self)
            return
        try:
            self.doc.set_event(ev, etype=t, arg=arg)
        except Exception as e:
            messagebox.showerror('写入失败', str(e), parent=self)
            return
        note = getattr(self.doc, 'time_sync_note', '') or ''
        # ★2026-10-20：缺运行期组件（TagNode/CellTag）⇒ 引擎根本不执行它。
        #   最容易踩的场景：新建的触发本来是「8 任何事件」，改成「13 流逝时间」后就变成"死触发"。
        #   ⇒ 这里**自动补齐**（等价于「修复缺组件触发」那套），并在状态栏说明。
        rep = ''
        try:
            det2 = self.doc.trigger_detail(self._cur)
            if not det2['has_node']:
                inst = self.doc.trigger_inst_off(self._cur)
                if inst is not None:
                    info = self.doc.repair_trigger_runtime(inst)
                    rep = '；已自动补 TagNode/CellTag（帧 %d）' % info['ticks']
                    for w in info.get('warnings') or []:
                        rep += '　⚠ ' + w
        except Exception as e:
            rep = '；⚠ 自动补组件失败：%s' % e
        self.after_change('事件 → %s（参数 %d）%s%s'
                          % (event_name(t), arg, note, rep))

    # ---------------- 行为 ----------------
    # ---- ★§255：行为 80/107（援军 / 超时空援军）的小队 + 路径点 ----
    def _team_choices(self):
        """地图定义的作战小队 `[('01000241  a飞船转圈A', 键), …]`（AI 生成的不列）。"""
        out = []
        try:
            for t in (self.doc.team_types() or []):
                if not t.get('defined', True):
                    continue
                out.append(('%s  %s' % (t['sid'], t['name']), t['key']))
        except Exception:
            pass
        return out

    def _fill_team_values(self, combo, cur_key):
        ch = self._team_choices()
        combo['values'] = [c[0] for c in ch]
        if cur_key:
            for lab, k in ch:
                if k == cur_key:
                    combo.set(lab)
                    break
        return ch

    def _team_key_from_text(self, text):
        """把「参数」框里的文字解析成小队键：`ID 名称` / `ID` / `0x键`。"""
        s = (text or '').strip()
        if not s:
            return None
        for lab, k in self._team_choices():
            if lab == s:
                return k
        try:
            for t in (self.doc.team_types() or []):
                if t['sid'] and t['sid'] in s:
                    return t['key']
        except Exception:
            pass
        if s.lower().startswith('0x'):
            try:
                return int(s, 16)
            except ValueError:
                return None
        return None

    def _show_wp(self, show):
        try:
            if show:
                if not self.row_wp.winfo_ismapped():
                    self.row_wp.pack(fill='x', pady=(6, 0), before=self.frm_ac_body)
            else:
                self.row_wp.pack_forget()
        except Exception:
            pass

    def _ac_payload(self, atype):
        """按行为类型算提交用的 `(arg, kwargs)` —— 80/107 走「小队 + 路径点」。"""
        if self.doc.is_team_action(atype):
            # ★§283.9：小队型（4 建立 / 5 解散 / 7 援军 / 80 / 107）—— 参数 = 小队键（+0x44）
            tkey = self._team_key_from_text(self.var_acarg.get())
            if not tkey:
                raise ValueError('这条行为的参数是「作战小队」—— 先在「参数」里选一支')
            if atype in self.doc.SPAWN_ACTIONS:
                try:
                    wp = int((self.var_wp.get() or '0').strip())
                except ValueError:
                    raise ValueError('路径点必须是整数')
                return 0, {'team': tkey, 'waypoint': wp}
            return 0, {'team': tkey}
        return self._parse_arg(self.var_acarg.get()), {}

    def _ac_hint(self):
        nt = ''
        sel = self.tv_ac.focus()
        if sel:
            i = int(sel) - 1
            acts = self._det.get('actions') or []
            if 0 <= i < len(acts):
                nt = action_note(acts[i]['type']) or ''
        if not nt:
            j = self.cb_ac.current()
            if j >= 0:
                nt = action_note(self._ac_keys[j]) or ''
        # 参数下拉：按当前类型的参数类型填（所属方型 ⇒ 阵营顺序）
        try:
            # ★★2026-10-27：以前这里只看**列表里那行**的类型，于是「选中某行 → 在上面的
            #   下拉里改成别的类型」时，参数下拉还停在旧类型的候选上（改 4/7 时看着像
            #   “选不了小队”）。现在：下拉里的类型和行类型不一致 ⇒ 以**下拉**为准。
            k = None
            k_row = None
            sel2 = self.tv_ac.focus()
            acts2 = (self._det or {}).get('actions') or []
            if sel2 and 0 <= int(sel2) - 1 < len(acts2):
                k_row = acts2[int(sel2) - 1]['type']
            k_cb = (self._ac_keys[self.cb_ac.current()]
                    if self.cb_ac.current() >= 0 else None)
            k = k_cb if (k_row is None or k_cb != k_row) else k_row
            if k is not None:
                pt = action_ptype(k)
                if self.doc.is_team_action(k):
                    # ★§283.9：小队型（4/5/7/80/107）的「参数」= 作战小队；
                    #   只有 80/107 另外带一个路径点输入。
                    _ch = self._fill_team_values(
                        self.cb_acarg, self._team_key_from_text(self.var_acarg.get())) or []
                    _spawn = k in self.doc.SPAWN_ACTIONS
                    self._show_wp(_spawn)
                    nt = ('小队型：参数下拉 = 作战小队（本档 %d 个）' % len(_ch)) + \
                         ('；「路径点」= 刷出来的位置' if _spawn else '') + \
                         ('　' + nt if nt else '')
                else:
                    try:
                        cur = self._parse_arg(self.var_acarg.get())
                    except Exception:
                        cur = None
                    self._fill_arg_values(self.cb_acarg, pt, cur)
                    self._show_wp(False)
                    if pt == 2:
                        hs = self.doc.house_order() or []
                        nt = ('所属方型：0 = 玩家自己（本档 %s）；其余按存档阵营顺序'
                              % next((h['cn'] for h in hs if h['is_player']), '?')) + \
                             ('　' + nt if nt else '')
        except Exception:
            pass
        # ★2026-10-20：附带「引擎口径」的参数类型（来自地图 [Actions] 真实数据），
        #   避免再拿地编口径的数字去填——两者编号不同（见主文档 §126）。
        try:
            _m = getattr(self.doc, '_map', None)
            if _m is not None and k is not None:
                _pts = (_m.action_param_types() or {}).get(k)
                if _pts:
                    _t = ' / '.join('%d %s' % (x, ENGINE_PTYPE_TEXT.get(x, '?'))
                                    for x in sorted(_pts))
                    nt = ('引擎参数类型：%s' % _t) + ('　' + nt if nt else '')
        except Exception:
            pass
        self.lbl_achint.config(text=nt or '（该行为没有备注）')

    def _ac_pick(self):
        """选中行为行 ⇒ 把它的类型/参数回填到上面两个控件，方便直接改。"""
        self._ac_hint()
        sel = self.tv_ac.focus()
        acts = (self._det or {}).get('actions') or []
        if not sel:
            return
        try:
            a = acts[int(sel) - 1]
        except Exception:
            return
        if a['type'] in getattr(self, '_ac_keys', ()):
            self.cb_ac.current(self._ac_keys.index(a['type']))
        else:
            self.cb_ac.set('%d  %s' % (a['type'], a.get('name') or ''))
        if self.doc.is_team_action(a['type']):
            # ★§283.9：小队型（4/5/7/80/107）—— 小队键在 +0x44；只有 80/107 有路径点
            self._fill_team_values(self.cb_acarg,
                                   a.get('team') or a.get('team_key') or 0)
            self.var_wp.set(str(a.get('waypoint') or 0)
                            if a['type'] in self.doc.SPAWN_ACTIONS else '')
        else:
            self.var_acarg.set(str(a['arg']))
            self.var_wp.set('')
        # ★填完再刷一次提示：所属方型会把 "8" 显示成 "8 古巴"，不然下拉看着像空的
        self._ac_hint()

    def _pending_sel(self):
        """新建模式下：返回选中行在待建清单里的下标（没有则 None）。"""
        sel = self.tv_ac.focus()
        try:
            i = int(sel) - 1
        except Exception:
            return None
        return i if 0 <= i < len(getattr(self, '_pending_actions', [])) else None

    def apply_action(self):
        """把**选中那条行为**改成上面选的类型/参数（原地改 8 字节，不增删对象）。"""
        if getattr(self, '_new_mode', False):
            i = self._pending_sel()
            if i is None:
                self.app.status.set('新建触发：先在列表里选一条行为再改')
                return
            j = self.cb_ac.current()
            if j < 0:
                self.app.status.set('新建触发：先选行为类型')
                return
            _t = self._ac_keys[j]
            try:
                arg, kw = self._ac_payload(_t)
            except ValueError as e:
                self.app.status.set(str(e))
                return
            self._pending_actions[i] = dict({'type': _t, 'arg': arg}, **kw)
            self._fill_actions()
            self.app.status.set('新建触发：第 %d 条行为 → %s（%s）'
                                % (i + 1, ACTION_TYPES[_t][0],
                                   self._ac_text(_t, arg, kw.get('team') or 0,
                                                 kw.get('waypoint') or 0)))
            return
        sel = self.tv_ac.focus()
        acts = (self._det or {}).get('actions') or []
        if not sel or not (0 <= int(sel) - 1 < len(acts)):
            messagebox.showinfo('提示', '先在列表里选一条行为，再改上面的类型/参数', parent=self)
            return
        i = self.cb_ac.current()
        if i < 0:
            messagebox.showinfo('提示', '先选行为类型', parent=self)
            return
        _t = self._ac_keys[i]
        try:
            arg, kw = self._ac_payload(_t)
        except ValueError as e:
            messagebox.showwarning('提示', str(e), parent=self)
            return
        a = acts[int(sel) - 1]
        try:
            self.doc.set_action(a, atype=_t, arg=arg, **kw)
        except Exception as e:
            messagebox.showerror('修改失败', str(e), parent=self)
            return
        self.after_change('行为 #%s → %s（%s）'
                          % (sel, ACTION_TYPES[_t][0],
                             self._ac_text(_t, arg, kw.get('team') or 0,
                                           kw.get('waypoint') or 0)))

    def add_action(self):
        if getattr(self, '_new_mode', False):
            j = self.cb_ac.current()
            if j < 0:
                # 没选类型就点「添加」：只提示，不弹框（玩家要求）
                self.app.status.set('新建触发：先在「行为类型」里选一个再点「添加」')
                return
            _t = self._ac_keys[j]
            try:
                arg, kw = self._ac_payload(_t)
            except ValueError as e:
                self.app.status.set(str(e))
                return
            self._pending_actions.append(dict({'type': _t, 'arg': arg}, **kw))
            self._fill_actions()
            self.app.status.set('新建触发：已加第 %d 条行为 %s（%s）—— 还没写入存档'
                                % (len(self._pending_actions), ACTION_TYPES[_t][0],
                                   self._ac_text(_t, arg, kw.get('team') or 0,
                                                 kw.get('waypoint') or 0)))
            return
        i = self.cb_ac.current()
        if i < 0 or not self._cur:
            return
        _t = self._ac_keys[i]
        try:
            arg, kw = self._ac_payload(_t)
        except ValueError as e:
            messagebox.showwarning('提示', str(e), parent=self)
            return
        try:
            # ★2026-10-27：插入一条行为要重扫全档记录（本档约 2.5 秒）——
            #   先把光标变忙、状态栏写一句并把界面刷出来，别让玩家以为程序死了。
            self.config(cursor='watch')
            self.app.status.set('正在插入行为…（大档约 2 秒）')
            self.app.update_idletasks()
        except Exception:
            pass
        try:
            self.doc.add_action(self._cur, _t, arg, **kw)
        except Exception as e:
            messagebox.showerror('添加失败', str(e), parent=self)
            return
        finally:
            try:
                self.config(cursor='')
            except Exception:
                pass
        self.after_change('已添加行为：%s（%s）'
                          % (ACTION_TYPES[_t][0],
                             self._ac_text(_t, arg, kw.get('team') or 0,
                                           kw.get('waypoint') or 0)))

    def dup_action(self):
        if getattr(self, '_new_mode', False):
            i = self._pending_sel()
            if i is None:
                self.app.status.set('新建触发：先在列表里选一条行为')
                return
            self._pending_actions.insert(i + 1, self._pending_actions[i])
            self._fill_actions()
            self.app.status.set('新建触发：已复制第 %d 条行为' % (i + 1))
            return
        sel = self.tv_ac.focus()
        acts = self._det.get('actions') or []
        if not sel or not (0 <= int(sel) - 1 < len(acts)):
            messagebox.showinfo('提示', '先在列表里选一条行为', parent=self)
            return
        a = acts[int(sel) - 1]
        try:
            self.doc.add_action(self._cur, a['type'], a['arg'])
        except Exception as e:
            messagebox.showerror('复制失败', str(e), parent=self)
            return
        self.after_change('已复制行为：%s' % a['name'])

    def del_action(self):
        if getattr(self, '_new_mode', False):
            i = self._pending_sel()
            if i is None:
                self.app.status.set('新建触发：先在列表里选一条行为')
                return
            it = self._pending_actions.pop(i)
            t = it['type'] if isinstance(it, dict) else it[0]
            self._fill_actions()
            self.app.status.set('新建触发：已删掉第 %d 条行为（%s）' % (i + 1, action_name(t)))
            return
        sel = self.tv_ac.focus()
        acts = self._det.get('actions') or []
        if not sel or not (0 <= int(sel) - 1 < len(acts)):
            messagebox.showinfo('提示', '先在列表里选一条行为', parent=self)
            return
        a = acts[int(sel) - 1]
        # ★2026-10-20 玩家要求：**最后一条也能删**（删完就是"空触发"，引擎不执行任何动作，
        #   这和新建时"一条行为都不加"是同一种形态，已自检过）。只多问一句。
        tail = ('\n\n⚠ 这是最后一条行为：删完这条触发就没有任何动作了'
                '（相当于空触发，引擎不会做任何事）。') if len(acts) == 1 else ''
        if not messagebox.askyesno('确认删除', '删掉这条行为？\n\n#%s %s（参数 %d）%s'
                                   % (sel, a['name'], a['arg'], tail), parent=self):
            return
        try:
            self.doc.remove_action(self._cur, a['key'])
        except Exception as e:
            messagebox.showerror('删除失败', str(e), parent=self)
            return
        self.after_change('已删除行为：%s' % a['name'])

    # ---------------- 新建 / 复制 / 删除 ----------------
    def _next_new_name(self):
        """下一个可用的「新触发N」名字。"""
        n = 1
        try:
            names = {r.name for r in self.doc.records if r.kind == '触发'}
        except Exception:
            names = set()
        while ('新触发%d' % n) in names:
            n += 1
        return '新触发%d' % n

    def new_trigger(self):
        """点「新建」= **立刻建一条**（不再是"先进表单、再点一次才建"两步走）。

        ★§257 玩家 2026-10-25：「已经点了新建了 写入名字后 再点一次新建才会刷新触发名，
          能不能省略这步」⇒ 取消两段式：
          · 点一下就在存档里建出来（名字取「新触发N」，建完自动选中它）；
          · 之后**改名字是实时的**（见 `_on_name_change`），不用再点「改名」；
          · 事件默认给一条「8 任何事件」，下面随时改/加；行为可以先不加（空触发合法）。

        旧路径（`TriggerEditor(new=True)`，脚本用）仍走"待建清单"：那种情况下
        `_pending_name / _pending_events / _pending_actions` 有值，按清单一次建完。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档', parent=self)
            return
        events = list(getattr(self, '_pending_events', []) or [])
        acts = list(getattr(self, '_pending_actions', []) or [])
        if not events:
            events = [(8, 0)]          # 默认「8 任何事件」，玩家在下面改
        nm = (getattr(self, '_pending_name', None) or self._next_new_name())[:16]
        et = events[0][0]
        # ★2026-10-20 玩家要求：**没有行为就建一条空触发**（不再硬塞 21 播放语音，
        #   否则还得再加一条才能把它删掉）。actions=None 就是不建行为对象。
        try:
            info = self.doc.create_trigger_multi(
                nm, events, acts or None,
                owner=self.cb_owner.get() if self.cb_owner.get() else None,
                persistence=self.cb_rep.current() if self.cb_rep.current() >= 0 else 0)
        except Exception as e:
            messagebox.showerror('新建失败', str(e), parent=self)
            return
        sid = info['trigger_id']
        warn = list(info.get('warnings') or [])
        self._new_mode = False
        self._pending_name = None
        self._pending_actions = []
        self._pending_events = []
        # ★§158：持久性 1 是计数型，两个坑在"新建"这一步就要提醒
        _per = self.cb_rep.current() if self.cb_rep.current() >= 0 else 0
        if _per == 1:
            if et == 1:
                warn.append('进入事件（1）+ 持久性 1 挂到多个对象上是**已知 BUG**（永不执行）；'
                            '多建筑请改用「每栋持久 0 进入 → 行为 56 设局部变量 + 汇总触发」')
            else:
                warn.append('持久性 1 要挂到对象上才会计数（挂 0 个对象永不触发）；'
                            '新触发还没有挂接对象，建议先留 0')
        msg = ('已新建 %s（%s）· 事件 %d 条（%s）· 行为 %d 条%s'
               % (sid, nm, len(events),
                  '、'.join(event_name(t) for t, _a in events[:3])
                  + ('…' if len(events) > 3 else ''),
                  len(acts),
                  '（空触发 —— 要加行为就在下面「添加」）' if not acts else ''))
        if warn:
            msg += '　⚠ ' + '；'.join(warn)
        self.after_change(msg, new_sid=sid)
        # ★§257：建完把光标丢进名字框并全选 —— 想改名直接打字覆盖，一步都不用多点
        try:
            self.after(60, lambda: (self.ent_name.focus_set(),
                                    self.ent_name.select_range(0, 'end')))
        except Exception:
            pass

    def copy_trigger(self):
        if self._blocked_in_new():
            return
        if not self._cur:
            return
        try:
            info = self.doc.copy_trigger(self._cur)
        except Exception as e:
            messagebox.showerror('复制失败', str(e), parent=self)
            return
        warn = '；'.join(info.get('warnings') or [])
        self.after_change('已复制为 %s（%s）%s'
                          % (info['sid'], info['name'], ('　⚠ ' + warn) if warn else ''),
                          new_sid=info['sid'])

    def del_trigger(self):
        if self._blocked_in_new():
            return
        """删除 = 两个选项：**软删除**（改条件，永不满足，可逆）/ **真删除**（移除对象）。

        ★2026-10-20 玩家方案：软删除只改 8 个字节，安全性高、随时能改回来；
          真删除干净但动字节最多（实例/标签/事件/N 条行为/TagNode/CellTag），
          还要重排后面所有触发的序号 —— 所以默认推荐软删除。
        """
        if not self._cur:
            return
        det = self._det
        if not det.get('editable'):
            messagebox.showwarning('不能删', det.get('note') or '该记录没有运行期实例', parent=self)
            return
        mode = self._ask_delete_mode(det)
        if not mode:
            return
        if mode == 'soft':
            try:
                info = self.doc.soft_disable_trigger(self._cur)
            except Exception as e:
                messagebox.showerror('软删除失败', str(e), parent=self)
                return
            self.after_change('已软删除 %s（%s）：事件 → %s，参数 %s（%s）'
                              % (info['sid'], info['name'], info['mode'],
                                 info['arg'], info['note']))
            return
        if not messagebox.askyesno(
                '⚠ 确认真删除这条触发？',
                '将整条删除：实例 + 标签 + 事件 + %d 条行为 + TagNode/CellTag'
                '（若挂了建筑，会先把建筑的挂接字段清掉），并重排后面所有触发的序号。\n\n'
                '%s　%s\n\n真删除无法撤销 —— 建议先在主窗口「备份存档」。\n\n确定删除吗？'
                % (len(det.get('actions') or []), det['sid'], det['name']),
                icon='warning', default='no', parent=self):
            return
        try:
            info = self.doc.remove_trigger(self._cur)
        except Exception as e:
            messagebox.showerror('删除失败', str(e), parent=self)
            return
        self.after_change('已真删除触发 %s（%s）：移除 %d 字节（%s）%s'
                          % (info['sid'], info['name'], info['bytes_removed'],
                             '、'.join(info['objects']),
                             '，清掉 %d 处建筑挂接' % len(info['detached'])
                             if info['detached'] else ''))

    def _ask_delete_mode(self, det):
        """让玩家选「软删除 / 真删除 / 取消」，返回 'soft' / 'hard' / None。"""
        dlg = tk.Toplevel(self)
        dlg.title('删除触发 —— 选一种方式')
        dlg.transient(self)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill='both', expand=True)
        att = int(det.get('attached') or 0)
        ttk.Label(frm, text='%s　%s' % (det['sid'], det['name']),
                  font=('', 10, 'bold')).pack(anchor='w')
        ttk.Label(
            frm, justify='left', wraplength=560, foreground=ui_theme.T['ink_2'],
            text='\n软删除（推荐·可逆）：一个对象都不删，只把事件改成永不满足 ——\n'
                 '　 · 挂了建筑（本档 %d 栋）或是计时触发 → 「13 流逝时间」+ 参数拉到上限\n'
                 '　　 （16,777,215 秒 ≈ 194 天连续游戏，实际永远到不了）\n'
                 '　 · 没挂建筑 → 「1 进入事件」参数 0，没有东西会"进入"它\n'
                 '　 只改 8 个字节；以后想恢复，把事件类型/参数改回去即可。\n\n'
                 '真删除（干净但不可逆）：实例 + 标签 + 事件 + %d 条行为 + TagNode/CellTag\n'
                 '　 全部移除，还要重排后面所有触发的序号 —— 动字节最多，建议先在主窗口备份。\n'
                 % (att, len(det.get('actions') or []))).pack(anchor='w', pady=(8, 12))
        out = {'v': None}

        def pick(v):
            out['v'] = v
            dlg.destroy()
        row = ttk.Frame(frm)
        row.pack(fill='x')
        ttk.Button(row, text='软删除（推荐）', command=lambda: pick('soft')).pack(side='left')
        ttk.Button(row, text='真删除…', command=lambda: pick('hard')).pack(side='left', padx=8)
        ttk.Button(row, text='取消', command=dlg.destroy).pack(side='right')
        dlg.grab_set()
        self.wait_window(dlg)
        return out['v']

    # ---------------- 保存 ----------------
    def save_to_file(self):
        """「💾 保存到文件」——把**整个存档**写盘（§283.9，玩家 2026-10-27 要求）。

        走主窗口「原地保存(自动备份)」同一条路：先弹确认、自动备份到 `_备份存档`、
        文件名过长（GBK>50B，实测会读档报错）会先拦一下。本对话框不重载存档，
        保存后可以继续编辑。
        """
        if not self.doc or not getattr(self.doc, 'path', None):
            messagebox.showwarning('提示', '还没有打开存档', parent=self)
            return
        self.app.save_inplace()

    def save_doc(self):
        """「✔ 保存该触发」——**把这一条触发保存进存档内存**，不是保存整个存档文件。

        · 新建模式：真正 create（事件 + 行为清单一起建），建完切到编辑态；
        · 编辑态：这里的改动本来就是即时写进内存的（改字节/增删对象），
          点这个按钮 = 刷新主窗口 + 明确告诉你"已在内存里，写文件请回主窗口"。
        """
        if getattr(self, '_new_mode', False):
            self.new_trigger()
            return
        if not self._cur:
            return
        self.after_change('触发 %s 的改动已应用（在存档内存里）；'
                          '要写文件请回主窗口点「保存」' % self._cur)

    # ---------------- 统一善后 ----------------
    def _on_name_change(self):
        """名字**随输入实时生效**（地编同款，不用再点「改名」）。

        ★§257 玩家 2026-10-25：「写入名字后 再点一次新建才会刷新触发名，能不能省略这步」
          ⇒ 两种情形都做到"打完就是"：
          · 新建模式（旧入口）：实时写 `_pending_name`，点「新建」时就用它；
          · **普通模式（编辑已有触发）：直接改名**（与当前名相同就跳过，
            避免 `reload()` 回填时自己触发一遍写盘）。
        """
        nm = (self.var_name.get() or '').strip()
        if not nm:
            return                       # 清空不写（存档里名字不能为空）
        nm = nm[:16]
        if getattr(self, '_new_mode', False):
            self._pending_name = nm
            try:
                self.app.status.set('新建触发名：%s（点「新建」立即创建）' % nm)
            except Exception:
                pass
            return
        cur = getattr(self, '_cur', None)
        if not cur:
            return
        if nm == ((self._det or {}).get('name') or ''):
            return                        # reload 回填的同名，不用写
        try:
            self.doc.set_trigger_name(cur, nm)
            self._det['name'] = nm
            self.app._lists_dirty = True
            self.app.status.set('触发名 → %s（已写进内存，回主窗口「保存」才落盘）' % nm)
        except Exception:
            pass

    def after_change(self, msg, new_sid=None):
        """改完一处后的收尾。

        ★★§256 性能（玩家 2026-10-25：「触发编辑器每点一个操作都会小卡一下」）：
          主窗口那三张列表是**全量重扫**（`refresh_records` 本档 ~2 秒，它要逐条判
          「运行期状态/是否已执行」）。以前每次改一个字段都刷三张表 ⇒ 编辑器里
          点一下卡两秒。现在只**标脏**，关闭编辑器时刷一次；编辑器自己的界面照常立刻刷新。
        """
        try:
            self.app._lists_dirty = True
        except Exception:
            pass
        self.app.status.set(msg + '（尚未保存）')
        self.reload(new_sid or self._cur)

    def destroy(self):
        """关闭窗口时把主窗口那三张列表刷一次（§256：改的过程中只标脏，免得每步卡 2 秒）。"""
        try:
            if getattr(self.app, '_lists_dirty', False):
                self.app._lists_dirty = False
                self.app.refresh_records()
                self.app.refresh_events()
                self.app.refresh_actions()
        except Exception:
            pass
        try:
            tk.Toplevel.destroy(self)
        except Exception:
            pass
