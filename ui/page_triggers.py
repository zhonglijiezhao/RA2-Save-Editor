# -*- coding: utf-8 -*-
"""触发器页（原 `App.triggers`）

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
from editor_core import event_name
from ui.msg import filedialog
from ui.msg import messagebox
from tkinter import ttk
from ui.trigger_editor import TriggerEditor
import os
import re
import tkinter as tk
import ui_theme


class TriggersPage(object):
    def _tab_triggers(self, nb):
        f = ttk.Frame(nb, padding=8)
        _ph = ui_theme.page_header(f, '触发', '启停 / 类型 / 备注；双击行切换启用')
        _ph.pack(fill='x', padx=2, pady=(2, 8))
        # ★2026-10-20（交接 next-step 3）：**触发类型提成独立列**（以前只塞在状态文字尾部），
        #   顺手加「标签数」——一个触发可挂多个标签，标签数 >1 时类型取"任一为 2 算重复"。
        cols = ('id', 'name', 'kind', 'state', 'rep', 'tags', 'mapinfo', 'note')
        # 列变多了 ⇒ 给树加横竖滚动条（窄窗口也能看全）
        tvw = ttk.Frame(f, height=18)
        self.tt = ttk.Treeview(tvw, columns=cols, show='headings', height=18)
        for c, t, w in (('id', '触发ID', 70), ('name', '名称', 142), ('kind', '类别', 44),
                        ('state', '状态', 122), ('rep', '触发类型', 88),
                        ('tags', '标签', 44), ('mapinfo', '地图（条件/行为）', 104),
                        ('note', '说明', 118)):
            self.tt.heading(c, text=t)
            self.tt.column(c, width=w, anchor='w')
        self._tt_vsb = ttk.Scrollbar(tvw, orient='vertical', command=self.tt.yview)
        self._tt_hsb = ttk.Scrollbar(tvw, orient='horizontal', command=self.tt.xview)
        self.tt.configure(yscrollcommand=self._tt_vsb.set, xscrollcommand=self._tt_hsb.set)
        self._tt_vsb.pack(side='right', fill='y')
        self._tt_hsb.pack(side='bottom', fill='x')
        self.tt.pack(side='left', fill='both', expand=True)
        # ★2026-10-18 22:0x（玩家要求）：大任务档触发成百上千 ⇒ 加搜索框；
        #   「标签」记录和触发混在一张表里，既不能改也没用 ⇒ 默认隐藏（勾选可看）。
        qrow = ttk.Frame(f)
        qrow.pack(fill='x', pady=(0, 6))
        ttk.Label(qrow, text='搜索：').pack(side='left')
        self._q_trig = tk.StringVar()
        ent = ttk.Entry(qrow, textvariable=self._q_trig, width=32)
        ent.pack(side='left')
        ent.bind('<KeyRelease>', lambda e: self._fill_trig_tree())
        ent.bind('<Escape>', lambda e: (self._q_trig.set(''), self._fill_trig_tree()))
        ttk.Button(qrow, text='✕', width=3,
                   command=lambda: (self._q_trig.set(''), self._fill_trig_tree())
                   ).pack(side='left', padx=(2, 8))
        self._show_tags = tk.BooleanVar(value=False)
        # ★2026-10-20：AI 三大件（作战小队/特遣部队/动作脚本）也被一并隐藏 ——
        #   它们和触发共用"8 位 ID + 名字"的记录容器，以前会冒充成"灰色触发"。
        ttk.Checkbutton(qrow, text='显示「标签/AI」记录', variable=self._show_tags,
                        command=self._fill_trig_tree).pack(side='left')
        self.lbl_trig_count = ttk.Label(qrow, text='', foreground=ui_theme.T['muted'])
        self.lbl_trig_count.pack(side='left', padx=10)
        ttk.Label(qrow, text='（Esc 清空；可按 触发ID / 名称 / 说明 / 状态 / 触发类型 搜）',
                  foreground=ui_theme.T['muted_soft']).pack(side='left')
        tvw.pack(fill='both', expand=True)
        self.tt.bind('<Double-1>', lambda e: self.toggle_selected())
        self.tt.bind('<space>', lambda e: self.toggle_selected())
        self.tt.bind('<<TreeviewSelect>>', self._remember_trigger_sel)
        self._sel_trigger_names = set()
        self._sel_trigger_sids = set()
        self._trig_rows = []
        row = ttk.Frame(f)
        row.pack(fill='x', pady=(6, 0))
        ttk.Button(row, text='启用选中', command=lambda: self.toggle_selected(True)).pack(side='left')
        ttk.Button(row, text='禁用选中', command=lambda: self.toggle_selected(False)).pack(side='left', padx=6)
        # ★2026-10-20：触发类型（标签持久性）可直接改 —— 原地写标签对象 +0xB0，4 字节、不动长度
        ttk.Button(row, text='🏷 改触发类型…',
                   command=self.set_repeat_dialog).pack(side='left', padx=6)
        # ★2026-10-22 §192：TagTypeClass 全字段（ID/名字/序号/持久性/触发连线/残留槽）
        ttk.Button(row, text='🔖 标签字段…',
                   command=self.tag_fields_dialog).pack(side='left', padx=6)
        # ★2026-10-25 §256 玩家：「这两个功能 有点重复了 只留触发编辑器就行」
        #   —— 「➕ 新建触发…」已删；新建统一走「✏ 触发编辑器…」里的「新建」按钮
        #   （在那个窗口里点「新建」= 进入新建模式，填完再点一次才真正创建）。
        # ★2026-10-20：仿 FA2「触发编辑器」——一个窗口改完名字/国家/类型/难度/事件/行为/删除
        ttk.Button(row, text='✏ 触发编辑器…',
                   command=self.open_trigger_editor).pack(side='left', padx=6)
        # ★2026-10-22 §174：一键「局部变量法」—— N 栋建筑全部到达才执行结果。
        #   避开两个已知引擎坑：持久 1 的离场记账被其它事件卡住（§163）、
        #   进入事件挂 ≥2 对象是 BUG（§159）。
        # ★2026-10-22 §175：地编流程第一步「先写局部变量」——名称 + 值直接落在
        #   明文 0x248A 起的 100 个 41B 定长槽里（name[40] + value u8）。
        mb = ttk.Menubutton(row, text='更多 ▾', style='Ghost.TButton')
        menu = tk.Menu(mb, tearoff=0)
        menu.add_command(label='局部变量…', command=self.local_var_editor)
        menu.add_command(label='局部变量配方…', command=self.local_var_wizard)
        menu.add_command(label='修复缺组件触发…', command=self.repair_runtime_dialog)
        menu.add_command(label='改造选中触发的动作…', command=self.retarget_dialog)
        mb['menu'] = menu
        mb.pack(side='left', padx=(6, 0))
        # ★2026-10-18 交接任务 B：改造选中触发的动作 —— 已并入「更多 ▾」菜单
        self._trig_note_box = ttk.Frame(f)
        self._trig_note_on = tk.BooleanVar(value=False)

        def _toggle_note():
            on = not self._trig_note_on.get()
            self._trig_note_on.set(on)
            if on:
                self._trig_note_box.pack(fill='x', pady=(6, 0))
            else:
                self._trig_note_box.pack_forget()
            self.btn_trig_note.config(text='说明 ▾' if on else '说明 ▸')

        # 说明开关放页标题右侧（和别的页一致；放按钮行里会被挤成 1px）
        self.btn_trig_note = ttk.Button(_ph, text='说明 ▸', style='Ghost.TButton',
                                        command=_toggle_note)
        self.btn_trig_note.pack(side='right')

        ttk.Label(self._trig_note_box,
                  text='启用 / 禁用 = 写触发对象里的 **1 个字节**：该对象含恒定「触发类 GUID」，'
                          '**GUID 前 8 字节**处即启用位（1=启用 / 0=禁用）。\n'
                          '    ★ 记录内偏移**随图变化**（实测新图 +236、旧图 +328）⇒ 用 GUID 当锚点定位，不写死偏移。\n'
                          '    ★ 证据：外挂禁用实验 T0/T1/T2 三档**只差这一字节**；旧图成对档 6691/6692 '
                          '游戏内实测「不胜利 / 胜利」逐字节对应；60+ 档扫描零误报。\n'
                          '    ⚠ 不要动 `+123`（那只是地图级定义的副本，改它无效）。'
                          '启用位未定位到的记录（显示"-"）为只读。\n'
                          '★ 状态列会写运行期情况：\n'
                          '    · 等待中（黑字）= 有 TagNode，正在计时（后面是剩余秒数）；\n'
                          '    · 事件驱动型·未挂钩（黑字）= 没有 TagNode，且事件对象还没被引擎碰过'
                          '（+0x50..+0x68 全 0）⇒ 还没轮到它，改它有效；\n'
                          '    · 事件驱动型·已挂钩（橙字）= 没有 TagNode，但事件对象已被引擎写过'
                          '⇒ 只能确定「挂钩了」，分不清「已触发」还是「等回调」；改之前会弹确认；\n'
                          '    · 未启用 / 已禁用、未实例化（只有定义）（灰字）。\n'
                          '   ★ 关键教训（2026-10-20 玩家用「飞碟被攻击」触发指出）：'
                          '「没有 TagNode」≠「已执行」—— 「被攻击 / 被摧毁 / 进房子」这类触发靠对象'
                          '回调驱动，本来就不建 TagNode。\n'
                          '   ★ 判「到底跑没跑过」只看一处：**CellTag 节点在不在**'
                          '（六档实测：跑完节点必消失，标签记录可能残留也可能整条消失）。'
                          '事件 `+0x68` 的「触发者」槽只对部分事件类型写入，摧毁类全程为 0，别拿它当判据。\n'
                          '   ★ 重复类型 = 它挂的**标签**的持久性（`[Tags]` 第 0 字段：'
                          '0 一次性·任一挂载 / 1 一次性·挂载清空 / 2 可重复），'
                          '**存档里就能读到**（标签记录 +120），不依赖地图。\n'
                          '   ⚠ 「任一/全部挂载」说的是**标签挂在多个对象上**时哪个对象的事件算数；'
                          '**同一触发里的多个事件永远是「全部成立才执行」（少一个都不执行）**，'
                          '与持久性取值无关（2026-10-21 玩家纠正）。\n'
                          '   📋 「触发类型」现在是**独立一列**（以前只跟在状态后面）：'
                          '一次性·任一挂载 / 一次性·挂载清空 / 可重复；'
                          '「标签数」= 它挂几个标签 —— 挂多个时**任一为重复就算重复型**。\n'
                          '   🏷 「改触发类型…」= 选中触发后直接改这个持久性'
                          '（原地写标签对象 +0xB0，4 字节、不动明文长度）。\n'
                          '   🛠 「修复缺组件触发…」= 诊断并补齐**运行期组件（TagNode/CellTag）**'
                          '—— 缺这两个对象的触发引擎根本不会执行（症状：改事件/启用都没反应）。\n'
                          '       ⚠ 对「已经执行过」的单次型触发点修复 = 重新武装，'
                          '条件再满足会再触发一次（对话框里有红字名单 + 二次确认）。',
                  foreground=ui_theme.T['muted'], font=(ui_theme.FONT_UI, 8),
                  wraplength=880, justify='left').pack(anchor='w')
        return f

    def open_trigger_editor(self, sid=None):
        """打开仿地编的「触发编辑器」窗口（默认编辑当前选中的触发）。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        if sid is None:
            sel = self.tt.selection() if hasattr(self, 'tt') else ()
            if sel:
                try:
                    r = self.doc.records[int(sel[0])]
                    if r.kind == '触发':
                        sid = r.sid
                except Exception:
                    pass
        try:
            TriggerEditor(self, sid)
        except Exception as e:
            messagebox.showerror('打开失败', str(e), parent=self)

    def new_trigger_via_editor(self):
        """「➕ 新建触发…」= 打开触发编辑器并直接走它的新建流程（取代旧向导）。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        try:
            TriggerEditor(self, sid=None, new=True)
        except Exception as e:
            messagebox.showerror('新建失败', str(e), parent=self)

    def refresh_records(self):
        """重填触发/标签列表（新写触发后也用它刷新）。"""
        if not self.doc or not hasattr(self, 'tt'):
            return
        ms = getattr(self.doc, 'map_status', None)
        per = (ms or {}).get('per_trigger', {})
        t2t = (ms or {}).get('tag_to_trigger', {})
        # ★运行期真实状态（TagNode+88 最低位 + 剩余计时）——大任务档定义字节≠运行期状态
        rtmap = {}
        try:
            rtmap = {s['sid']: s for s in self.doc.runtime_status()}
        except Exception:
            pass
        self._rtmap = rtmap
        # ★2026-10-20：标签→触发只算一次（每行都调 trigger_tag_pairs() 在大档上是 O(n²)）
        try:
            _pairs = self.doc.trigger_tag_pairs()
        except Exception:
            _pairs = []
        tags_of = {}
        for _tsid, _trig in _pairs:
            tags_of.setdefault(_trig, set()).add(_tsid)
        rows = []
        stcnt = {}
        # ★2026-10-20：哪些触发**没有运行期组件**（存档里找不到它的事件/行为）
        #   ⇒ 列表里灰掉并注明，否则玩家点了发现"没高亮"会以为是功能坏了。
        try:
            _links = self.doc.runtime_links()
        except Exception:
            _links = {}
        nort = set()
        # 事件对象是否带运行期指针（= 引擎处理过）—— 判"已执行"用，见下
        _evlive = {}
        for _r in self.doc.records:
            if _r.kind == '触发':
                try:
                    _evlive[_r.sid] = self.doc.trigger_event_live(_r.sid)
                except Exception:
                    _evlive[_r.sid] = False
        for i, r in enumerate(self.doc.records):
            state = '-' if r.enabled is None else ('启用' if r.enabled else '禁用')
            if r.kind == '触发':
                c = per.get(r.sid)
                mi = '条件 %d · 行为 %d' % (c.get('ev', 0), c.get('ac', 0)) if c else \
                     ('—（地图未列该触发）' if ms else '—')
                # ★2026-10-20：**执行状态**判据（本档实测，见文档 §119）
                #   运行期节点(TagNode)在 ⇒ 还没结束；节点没了而定义是启用的 ⇒ **已执行完**。
                #   证据：A2/A3 号信标铁幕（流逝时间 45 秒）无节点、A4~A13 有节点且剩 45 秒；
                #   无节点的 34 条里 25 条是流逝时间、8 条是"被摧毁"—— 都是"已经发生过"的类型。
                _rep = None
                try:
                    _rep = self.doc.trigger_repeat_type(r.sid)
                except Exception:
                    _rep = None
                _rep_text = getattr(self.doc, 'REPEAT_TEXT', {})
                rt = rtmap.get(r.sid)
                if rt is None:
                    # ★2026-10-20（玩家问"灰色的是什么状态"）：这些记录在本档里
                    #   只有「触发定义」、引擎还没实例化它 ⇒ 没有事件/行为/TagNode，
                    #   既不执行也改不了。写清楚，别让人以为是"坏了"。
                    rts = '未实例化（只有定义）'
                    st_key = '无组件'
                elif not rt['has_node']:
                    if r.enabled is False:
                        rts = '未启用（定义即禁用）'
                        st_key = '未启用'
                    elif _rep == '2':
                        # 重复型：上一轮跑完了，但引擎会重新武装 ⇒ 不是"作废"
                        rts = '重复中（已跑过，会再触发）'
                        st_key = '等待'
                    elif not _evlive.get(r.sid):
                        # ★2026-10-20（玩家实测纠正）：**没有 TagNode ≠ 已执行**。
                        #   「被攻击/被摧毁/进房子」这类事件驱动触发本来就不建 TagNode，
                        #   引擎靠对象回调驱动它。判"是否执行过"要看**事件对象的运行期指针**
                        #   （+0x50..+0x68）有没有被引擎写过：
                        #     全 0 ⇒ 还没轮到它（未执行）；
                        #     非 0 ⇒ 执行过（sample_AB 的 A 就是这个形态，玩家确认开局即执行）。
                        rts = '事件驱动型·未挂钩（未触发）'
                        st_key = '等待'
                    else:
                        # ★2026-10-20：事件对象已被引擎写过，但**分不清**是"挂钩等待回调"
                        #   还是"已经触发过"（游戏内已验收的 sample_AB A 与待触发的同类请求
                        #   在这一段字节上是同形）⇒ 只说"已挂钩"，不再断言"已执行"。
                        rts = '事件驱动型·已挂钩（可能已触发）'
                        st_key = '已执行'
                elif rt['rt_enabled']:
                    rts = '等待中'
                    if rt['ticks']:
                        rts += ' 剩%.0f秒' % (rt['ticks'] / 15.0)
                    st_key = '等待'
                else:
                    rts = '已禁用'
                    st_key = '已禁用'
                stcnt[st_key] = stcnt.get(st_key, 0) + 1
                if _rep == '2' and st_key != '等待':
                    rts = '重复·' + rts            # 重复型：执行过还会再触发
                # ★2026-10-20：状态列只写"启用/执行"；**触发类型单独一列**（见 rep 列）
                state = (state + '｜' + rts) if state != '-' else rts
                rep_cell = (_rep_text.get(_rep, '型' + _rep) if _rep
                            else '未读出（无标签）')
                # ★§158：持久性 1 的两种坑（挂 0 个对象 / 进入事件挂多个对象）
                #   直接在"触发类型"列打 ⚠，不用点开也能看见。只对 1 判，避免拖慢列表。
                if _rep == '1':
                    try:
                        if self.doc.repeat_pitfall(r.sid, '1'):
                            rep_cell += ' ⚠'
                    except Exception:
                        pass
                ntags = len(tags_of.get(r.sid, ()))
                tags_cell = ('%d' % ntags) if ntags else '—'
            else:
                mi = ('→ %s' % t2t[r.sid]) if r.sid in t2t else '—'
                rep_cell = getattr(self.doc, 'REPEAT_TEXT', {}).get(
                    self.doc.tag_persist(r.sid) or '', '') or '—'
                tags_cell = '—'
            note = r.note
            if r.kind == '触发' and r.sid not in _links:
                nort.add(i)
                note = (note + ' · ' if note else '') + '⚠本档只有「触发定义」、未实例化（引擎不执行·只读）'
            rows.append((i, (r.sid, r.name, r.kind, state, rep_cell, tags_cell, mi, note)))
        self._trig_rows = rows
        self._trig_nort = nort
        self._trig_stcnt = stcnt
        self._fill_trig_tree()

    def _is_executed(self, rec):
        """这条触发是不是**已经执行过**（游戏已回收它的运行期节点）。

        判据（2026-10-20 玩家用 SAVE0029 双触发档定案）：
          · 有运行期实例，但 **TagNode 不在了**；
          · 且定义里是**启用**的（定义本来就禁用 ⇒ 是"未启用"，不是"已执行"）。
        实测：SAVE0029 里 A 触发（开局即执行）无节点、B 触发有节点且剩 30 秒。
        ⚠ 已执行的触发**改什么都没用**（引擎不再评估它），所以界面上标红并禁止修改。
        """
        if rec is None:
            return False
        x = (getattr(self, '_rtmap', {}) or {}).get(rec.sid)
        if not (x is not None and not x['has_node'] and rec.enabled is not False):
            return False
        # ★2026-10-20：光"没有 TagNode"不够 —— 事件驱动型触发本来就没有节点。
        #   必须再看事件对象 `+0x50..+0x68` 有没有被引擎写过（非 0 = 处理过）。
        try:
            if not self.doc.trigger_event_live(rec.sid):
                return False
        except Exception:
            return False
        # ★2026-10-20：**重复**型（标签持久性 = 2，可重复）执行过还会再触发
        #   ⇒ 不能当"已执行作废"，改它是有意义的。持久性从存档标签记录 +120 读，不依赖地图。
        try:
            if self.doc.trigger_repeat_type(rec.sid) == '2':
                return False
        except Exception:
            pass
        return True

    def _sid_of_row(self, item):
        """事件/行为行 → 所属触发 sid（优先地图归属，其次运行期键链）。"""
        try:
            if item.get('owner'):
                return item['owner']
        except Exception:
            pass
        try:
            for sid, d in self.doc.runtime_links().items():
                if d.get('ev') == item.get('key') or d.get('ac') == item.get('key'):
                    return sid
        except Exception:
            pass
        return None

    def _guard_executed(self, sid, what='这条触发'):
        """已执行的触发禁止修改：弹窗说明并返回 True（表示已拦截）。

        ★2026-10-20：**重复型**触发跑完还会再触发，改它有意义 ⇒ 不拦。
          "重复类型"= 它挂的**标签**的持久性（`[Tags]` 第 0 字段：
          0 一次性·任一挂载 / 1 一次性·挂载清空 / 2 可重复），
          **存档里就能读到**（标签记录 +120），不依赖地图。
          只有持久性 = 2 才算会重复 ⇒ 其余（0/1）执行过就是真作废。
        """
        if not sid:
            return False
        rec = self.doc.trigger_rec(sid)
        if rec is None or not self._is_executed(rec):
            return False
        try:
            rep = self.doc.trigger_repeat_type(rec.sid)
        except Exception:
            rep = None
        if rep is None:
            return not messagebox.askyesno(
                '这条触发可能已经执行过',
                '「%s」的运行期节点已经没了，看起来**已经执行过**（改它多半不生效）。\n\n'
                '但它也可能是**重复型**触发（跑完会再触发，改它有用）。\n'
                '当前没能从存档里标出它的重复类型，无法确认。\n\n'
                '仍要修改吗？' % rec.name, parent=self)
        rep = (rep, getattr(self.doc, 'REPEAT_TEXT', {}).get(rep, ''))
        # ★2026-10-20：字节上分不清"挂钩等待"与"已触发" ⇒ 改成让玩家自己判断
        return not messagebox.askyesno(
            '这条触发可能已经触发过',
            '「%s」没有计时节点，事件对象已经被引擎写过，\n'
            '所以它**可能已触发过**，也可能只是挂上了钩子、还在等条件（单次型·%s %s）。\n\n'
            '如果它已经触发过，改它不会生效；如果只是挂钩等待，改了会生效。\n\n'
            '仍要修改吗？' % (rec.name, rep[0], rep[1]), parent=self)

    def _fill_trig_tree(self):
        """把 `_trig_rows` 按「搜索词 + 是否显示标签」填进触发树。

        ★iid 仍是 `self.doc.records` 的下标 ⇒ 过滤只是少插行，选中/启用/改造全都照旧可用。
        """
        if not hasattr(self, 'tt'):
            return
        self.tt.delete(*self.tt.get_children())
        try:
            self.tt.tag_configure('nort', foreground=ui_theme.T['muted_soft'])      # 无运行期组件 ⇒ 灰
            self.tt.tag_configure('done', foreground=ui_theme.T['danger'])      # 已执行 ⇒ 红（改不了）
            self.tt.tag_configure('warn', foreground=ui_theme.T['warn'])      # 已挂钩/可能已触发 ⇒ 橙
        except Exception:
            pass
        q = ((self._q_trig.get() if hasattr(self, '_q_trig') else '') or '').strip().lower()
        show_tags = bool(self._show_tags.get()) if hasattr(self, '_show_tags') else False
        rows = getattr(self, '_trig_rows', [])
        nort = getattr(self, '_trig_nort', set())
        shown = 0
        for i, vals in rows:
            if vals[2] != '触发' and not show_tags:
                continue
            if q and not any(q in str(v).lower() for v in vals):
                continue
            _rec = self.doc.records[i] if (self.doc and 0 <= i < len(self.doc.records)) else None
            _tags = ()
            if i in nort:
                _tags = ('nort',)
            elif _rec is not None and self._is_executed(_rec):
                _tags = ('warn',)      # 事件驱动型·已挂钩 ⇒ 橙字（不再判定为"已执行"红字）
            self.tt.insert('', 'end', iid=str(i), values=vals, tags=_tags)
            shown += 1
        if hasattr(self, 'lbl_trig_count'):
            c = getattr(self, '_trig_stcnt', {}) or {}
            extra = '｜已执行 %d · 等待 %d · 已禁用 %d · 未启用 %d · 无组件 %d' % (
                c.get('已执行', 0), c.get('等待', 0), c.get('已禁用', 0),
                c.get('未启用', 0), c.get('无组件', 0))
            self.lbl_trig_count.config(
                text='显示 %d / %d 条%s%s' % (shown, len(rows),
                                            '' if show_tags else '（标签已隐藏）', extra))


    # ★2026-10-28 删：这里原本有一份 128 行的 `repair_runtime_dialog` 和一份 `_repair_verdict`，
    #   与下面（原 882 行起）的版本**逐字重复 / 逻辑被取代**，属于编辑时的复制粘贴残留。
    #   同名后定义会覆盖前定义，前一份从来没被执行过 —— 直接删掉，只留下面这一份。
    def repair_runtime_dialog(self):
        """诊断 / 修复「缺运行期组件（TagNode/CellTag）」的触发。

        ★ 背景（2026-10-15 四档解剖定案）：缺 TagNode/CellTag 的触发引擎不会执行 ——
          症状就是「改事件类型 / 改参数 / 启用了都没反应」。修复 = 补齐组件 + 计时表登记，
          复用新写触发那套已游戏内验收的克隆机器。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        try:
            st = self.doc.runtime_status()
        except Exception as e:
            messagebox.showerror('出错', '诊断失败：%s' % e, parent=self)
            return
        bad = [s for s in st if not s['has_node'] or not s['has_cell']]
        if not bad:
            messagebox.showinfo('诊断结果', '所有触发都有运行期组件（TagNode/CellTag），无需修复。')
            return
        dlg = tk.Toplevel(self)
        dlg.title('修复缺组件触发（补 TagNode/CellTag + 计时表登记）')
        dlg.transient(self)
        frm = ttk.Frame(dlg, padding=12)
        frm.grid(sticky='nsew')
        ttk.Label(frm, text='以下触发缺少运行期组件，引擎不会执行它们（改事件/启用都无效）：',
                  foreground=ui_theme.T['danger']).grid(row=0, column=0, columnspan=2, sticky='w')
        cols = ('sel', 'name', 'id', 'st', 'rep', 'node', 'cell', 'ticks')
        tv = ttk.Treeview(frm, columns=cols, show='headings', height=9)
        for c, t, w in (('sel', '修复', 42), ('name', '触发名', 140), ('id', 'ID', 76),
                        ('st', '诊断', 190), ('rep', '触发类型', 100),
                        ('node', 'TagNode', 62), ('cell', 'CellTag', 62),
                        ('ticks', '计时帧', 56)):
            tv.heading(c, text=t)
            tv.column(c, width=w, anchor='w' if c in ('name', 'st', 'rep') else 'center')
        tv.grid(row=1, column=0, columnspan=2, sticky='nsew', pady=(6, 4))
        try:
            tv.tag_configure('risk', foreground=ui_theme.T['danger'])     # 确定已执行 ⇒ 再触发风险
            tv.tag_configure('mid', foreground=ui_theme.T['warn'])      # 分不清/类型未读出 ⇒ 仍可能再触发
        except Exception:
            pass
        self._repair_iids = []
        n_risk = n_mid = 0
        for s in bad:
            txt, lvl, rep_txt = self._repair_verdict(s)
            n_risk += (lvl == 'high')
            n_mid += (lvl == 'mid')
            iid = tv.insert('', 'end', values=(
                '☐', s['name'], s['sid'], txt, rep_txt,
                '✗ 缺失' if not s['has_node'] else '✓',
                '✗ 缺失' if not s['has_cell'] else '✓',
                s['ticks'] if s['ticks'] is not None else '—'),
                tags=(('risk',) if lvl == 'high' else ('mid',) if lvl == 'mid' else ()))
            self._repair_iids.append((iid, s, lvl))
        tv.bind('<Double-1>', lambda e: self._toggle_repair_row(tv))

        def toggle_sel():
            for iid, _s, _l in self._repair_iids:
                cur = tv.set(iid, 'sel')
                tv.set(iid, 'sel', '☑' if cur == '☐' else '☐')
        ttk.Button(frm, text='全选/反选', command=toggle_sel).grid(
            row=2, column=0, sticky='w', padx=(0, 6))
        ttk.Label(frm, text='计时帧数按该触发当前事件自动算（13 流逝时间 → 秒×15）。',
                  foreground=ui_theme.T['muted']).grid(row=2, column=1, sticky='w')

        # ★2026-10-20（交接 next-step 2）：**已执行警示** —— 对跑过的单次型触发点修复
        #   = 重新武装，条件再满足会**再触发一次**。以前这里一声不响，容易把玩家坑了。
        ttk.Label(
            frm,
            text='⚠ 红字行 = 确定已执行过（CellTag 还在，TagNode 被引擎回收）：修复 = 把这条\n'
                 '　 单次型触发「重新武装」，条件一满足就会「再触发一次」\n'
                 '　（刷建筑 / 给钱 / 放援军都会再来一遍）。\n'
                 '　 橙字行 = 组件全无或重复类型读不出，分不清跑没跑过 —— 同样可能再触发。\n'
                 '　 修复前会二次确认，并列出具体是哪几条。',
            foreground=ui_theme.T['danger'], justify='left').grid(
                row=3, column=0, columnspan=2, sticky='w', pady=(6, 0))
        if n_risk or n_mid:
            ttk.Label(frm, text='本档：确定已执行 %d 条　分不清/类型未读出 %d 条（共 %d 条缺组件）'
                                % (n_risk, n_mid, len(bad)),
                      foreground=ui_theme.T['ink_2']).grid(
                row=4, column=0, columnspan=2, sticky='w', pady=(4, 0))

        def do_repair():
            todo = [(iid, s, lvl) for iid, s, lvl in self._repair_iids
                    if tv.set(iid, 'sel') == '☑']
            if not todo:
                messagebox.showinfo('提示', '没有勾选任何触发。', parent=dlg)
                return
            risky = [(s, lvl) for _i, s, lvl in todo if lvl in ('high', 'mid')]
            if risky:
                names = ['%s（%s）' % (s['name'], '确定已执行' if lvl == 'high'
                                       else '分不清/类型未读出') for s, lvl in risky]
                head = '\n'.join('　· ' + n for n in names[:8])
                more = ('\n　…等共 %d 条' % len(names)) if len(names) > 8 else ''
                if not messagebox.askyesno(
                        '⚠ 修复 = 重新武装（可能再触发一次）',
                        '勾选项里有 %d 条可能已经执行过（引擎把运行期组件回收了）：\n\n%s%s\n\n'
                        '补回 TagNode/CellTag 等于把它们「重新武装」：条件一满足就会\n'
                        '「再触发一次」（刷建筑 / 给钱 / 放援军都会再来一遍）。\n\n'
                        '确定要重新武装这 %d 条吗？' % (len(risky), head, more, len(risky)),
                        icon='warning', default='no', parent=dlg):
                    return
            done, fails = [], []
            for _iid, s, _lvl in todo:
                try:
                    info = self.doc.repair_trigger_runtime(s['inst_key'])
                    (done if not info.get('already') else fails).append(
                        '%s：已补 TagNode+CellTag（帧 %d）' % (s['name'], info['ticks'])
                        if not info.get('already') else '%s：已有组件，跳过' % s['name'])
                    for w in info.get('warnings', []):
                        fails.append('%s：%s' % (s['name'], w))
                except Exception as e:
                    fails.append('%s：%s' % (s['name'], e))
            self.refresh_records()
            self.refresh_events()
            self.refresh_actions()
            self.status.set('修复完成：%s%s（尚未保存，请另存/原地保存）'
                            % ('；'.join(done) or '无', '　|　' + '；'.join(fails) if fails else ''))
            msg = '已修复 %d 个触发。\n%s' % (len(done), ('\n'.join(fails)) if fails else '')
            if done and risky:
                msg += '\n\n⚠ 其中 %d 条是重新武装的：进游戏后条件满足会再触发一次。' % len(risky)
            if done:
                messagebox.showinfo('修复完成', msg.strip(), parent=self)
            else:
                messagebox.showwarning('未修复', msg.strip(), parent=self)
            dlg.destroy()
        ttk.Button(frm, text='修复勾选项', command=do_repair).grid(
            row=5, column=0, sticky='w', pady=(8, 0))
        ttk.Button(frm, text='关闭', command=dlg.destroy).grid(
            row=5, column=1, sticky='w', pady=(8, 0))

    def _repair_verdict(self, s):
        """给「修复缺组件触发」里的一条触发定性 → (诊断文字, 等级, 触发类型文字)。

        等级：'high' = **确定**是执行后被回收（CellTag 还在 ⇒ 引擎建过这对组件）
              'mid'  = 组件全无、定义启用 ⇒ 分不清"已执行"还是"从未武装"
              'low'  = 重复型本来就该再跑 / 定义即禁用 / 只缺 CellTag —— 补上就是它该有的样子

        ★2026-10-20 自检修正：最初把「node/cell 都没有」当成"从未有组件 ⇒ 安全"，
          结果把 `_exp/sample_AB_已执行与等待.SAV` 里**已执行**的 A（node/cell 全无）判成了 low。
          正确模型（§119，玩家游戏内实测过）：**有实例 + 无 TagNode + 定义启用 ⇒ 已执行**，
          与 CellTag 在不在**无关**（单次型标签(持久性0)触发后连标签一起销毁）。
          所以"有 CellTag"只能证明"确实是执行后回收"，不能反过来证明"没 CellTag 就没跑过"。
        """
        rep = None
        try:
            rep = self.doc.trigger_repeat_type(s['sid'])
        except Exception:
            rep = None
        rep_txt = (getattr(self.doc, 'REPEAT_TEXT', {}).get(rep, '型' + rep)
                   if rep else '未读出')
        if s['record_enabled'] is False:
            return ('定义即禁用（未武装）', 'low', rep_txt)
        if s['has_node']:
            return ('有 TagNode、缺 CellTag', 'low', rep_txt)
        # 走到这里 = 有实例但没 TagNode ⇒ 引擎已经不管它了
        if rep == '2':
            return ('重复型跑完（本来就会再跑）', 'low', rep_txt)
        if s['has_cell']:
            # CellTag 还在 ⇒ 引擎确实建过这对组件 ⇒ 铁证"执行后被回收"
            if rep is None:
                return ('执行后被回收？类型未读出', 'mid', rep_txt)
            return ('执行后被回收（CellTag 还在）', 'high', rep_txt)
        # 组件全无：单次型标签触发后连标签一起销毁 ⇒ 只靠字节分不清
        return ('组件全无：可能已执行，也可能从未武装', 'mid', rep_txt)

    @staticmethod
    def _toggle_repair_row(tv):
        sel = tv.focus()
        if sel:
            tv.set(sel, 'sel', '☑' if tv.set(sel, 'sel') == '☐' else '☐')

    @staticmethod
    def _name_like(a, b):
        """两个触发名是否「同一个」——容忍一侧被截断（记录侧短、实例侧 52 字节）。

        只做**前缀**比较（不做包含），且短的一方至少 6 个字符，避免误配。
        """
        if not a or not b:
            return False
        if a == b:
            return True
        s, l = (a, b) if len(a) <= len(b) else (b, a)
        return len(s) >= 6 and l.startswith(s)

    def _trigger_hit_keys(self):
        """选中触发 → (事件键集合, 行为键集合)。

        两条路先定「到底是哪几个触发」，再取键：
          ① sid 直配：`runtime_links()`（实例 `+192` 事件 / `+196` 行为链头）；
          ② 名字兜底：记录名常被**截断**（比实例里那份短）⇒ 前缀匹配；前缀命中多个就只认精确。
        行为再经 `action_owner_sids()` 沿链展开 ⇒ 整条链都算（不是只有链头）。
        """
        names = getattr(self, '_sel_trigger_names', set())
        sids = set(getattr(self, '_sel_trigger_sids', set()))
        links, aown = {}, {}
        try:
            links = self.doc.runtime_links()
        except Exception:
            pass
        try:
            aown = self.doc.action_owner_sids()
        except Exception:
            pass
        hit_sids = {s for s in sids if s in links}
        if names and links:
            all_names = {d['name'] for d in links.values()}
            allow = set()
            for n in names:
                cand = [r for r in all_names if self._name_like(r, n)]
                if len(cand) > 1:
                    cand = [r for r in cand if r == n]
                allow.update(cand)
            hit_sids |= {s for s, d in links.items() if d['name'] in allow}
        evk = {links[s]['ev'] for s in hit_sids}
        ack = {k for k, s in aown.items() if s in hit_sids}
        return evk, ack

    def _remember_trigger_sel(self, _e=None):
        """触发页选中变化：记下选中触发的名字 + ID，并**立刻**刷新事件/行为页的高亮。

        ★2026-10-18：以前只记名字、等切页时才高亮；一旦页签事件没触发/名字被打断就整个失灵。
          现在选中即高亮（两个页都刷），切页时再补一次，双保险。
        """
        if not self.doc or not hasattr(self, 'tt'):
            return
        names, sids = set(), set()
        for iid in self.tt.selection():
            try:
                r = self.doc.records[int(iid)]
            except Exception:
                continue
            if r.kind == '触发':
                names.add(r.name)
                sids.add(r.sid)
        self._sel_trigger_names = names
        self._sel_trigger_sids = sids
        if not names and not sids:
            # ★2026-10-20：选中的是「标签」行（或未识别行）⇒ **保持原高亮**，只提示。
            #   以前这里会直接把高亮清掉，看着就像"点了没反应"。
            sel = self.tt.selection() if hasattr(self, 'tt') else ()
            if sel:
                self.status.set('选中的是「标签」行 —— 标签本身没有事件/行为，请选一条「触发」')
                return
            # 真的没选中任何东西 ⇒ 把两个页的高亮一起清掉
            self._ev_hits = set()
            self._ac_hits = set()
            try:
                self._mark_hits(self.te, getattr(self, '_ev_rows', []), set())
                self._mark_hits(self.ta, getattr(self, '_ac_rows', []), set())
            except Exception:
                pass
            return
        try:
            self._highlight_events_for_trigger()
            self._highlight_actions_for_trigger()
        except Exception:
            pass

    # ---------------- 触发 ↔ 事件/行为 高亮（★2026-10-20） ----------------
    HIT_BG = ui_theme.MAP['hit_bg']          # 命中行底色（浅琥珀，浅色主题下足够醒目）
    HIT_FG = ui_theme.MAP['hit_fg']

    def _mark_hits(self, tree, rows, hits, tag='hit'):
        """给命中的行打高亮标签，未命中的行清掉（保留原有的 bad 等标签）。

        为什么用 tag 而不是只靠 selection：Treeview 失去焦点时选中行是**浅灰**的，
        切到事件/行为页时几乎看不出来；tag 的底色则始终可见。
        """
        if tree is None:
            return
        try:
            tree.tag_configure(tag, background=self.HIT_BG, foreground=self.HIT_FG)
            tree.tag_raise(tag)
        except Exception:
            pass
        try:
            present = set(tree.get_children())
        except Exception:
            present = set()
        for i, _vals, tags in rows or []:
            iid = str(i)
            if iid not in present:
                continue
            base = tuple(t for t in (tags or ()) if t != tag)
            newtags = base + ((tag,) if iid in hits else ())
            try:
                tree.item(iid, tags=newtags)
            except Exception:
                pass

    def _owner_col_matches(self, col, names, sids):
        """「所属触发」列文字是否属于选中的触发（运行期链 = `★名字（运行期链）`，地图 = `sid 名字`）。"""
        s = str(col or '').strip()
        if not s or s == '—':
            return False
        if s.startswith('★'):
            inner = s[1:].split('（运行期链）')[0].strip()
            if inner in sids:
                return True
            for n in names:
                if n and (inner == n or inner.startswith(n) or n.startswith(inner)):
                    return True
            return False
        parts = s.split(None, 1)
        if parts and parts[0] in sids:
            return True
        return len(parts) > 1 and parts[1].strip() in names

    def _on_tab_changed(self, _e=None):
        try:
            # ★2026-10-27：用户切走 ⇒ 立刻作废后台元信息扫描（它每次要解压 12 个整档）
            self._scan_gen = getattr(self, '_scan_gen', 0) + 1
            idx = self.nb.index(self.nb.select())
            if idx == 3:                                # 事件页（第4个标签）
                self._highlight_events_for_trigger()
            elif idx == 4:                              # ★行为页（第5个标签）
                self._highlight_actions_for_trigger()
            elif idx == 6:                              # 地图 / 选位页（第7个）
                # ★§290：切进来就把建筑/单位下拉填好（原来要点「↻」）
                self._mv_ensure_types()
                # ★§272：开档时为了快，地图是"延迟渲染"的 ⇒ 第一次切到本页才画
                if getattr(self, 'mv_deferred', False):
                    self.mv_deferred = False
                    _p = getattr(self, '_mv_defer_path', None) \
                        or (getattr(self, 'mv_state', None) or {}).get('path')
                    try:
                        # ★§285：_p 为空 = 没有 .MAP ⇒ 也要画（地形来自存档格对象）
                        if _p:
                            self.status.set('正在解析地图 %s …' % os.path.basename(_p))
                        else:
                            self.status.set('没有 .MAP：正在用存档格对象重建地形…')
                        self.update_idletasks()
                        self.mv_load(_p)
                    except Exception:
                        pass
            elif idx == 10:                             # ★2026-10-28 武器·弹头页（第 11 个）
                # 懒加载：扫类型表要 ~230ms，开档时不做，第一次切进来才填
                if not getattr(self, '_wpn_loaded', False):
                    self.refresh_weapons()
        except Exception:
            pass

    def _highlight_actions_for_trigger(self):
        """把触发页选中的触发对应的**行为**高亮 + 定位（整条行为链）。"""
        names = getattr(self, '_sel_trigger_names', set())
        sids = getattr(self, '_sel_trigger_sids', set())
        if not self.doc or not getattr(self, 'ta', None) or not getattr(self, '_aclist', None):
            return
        if not names and not sids:
            self._ac_hits = set()
            self._mark_hits(self.ta, getattr(self, '_ac_rows', []), set())
            return
        _evk, ack = self._trigger_hit_keys()
        hits = set()
        for i, a in enumerate(self._aclist):
            if a.get('key') in ack or (a.get('owner') and a.get('owner') in sids):
                hits.add(str(i))
        if not hits:
            for i, vals, _t in getattr(self, '_ac_rows', []):
                if len(vals) > 5 and self._owner_col_matches(vals[5], names, sids):
                    hits.add(str(i))
        self._ac_hits = hits
        if not hits:
            self._mark_hits(self.ta, getattr(self, '_ac_rows', []), set())
            nm = '、'.join(sorted(names or sids))
            self.status.set('「%s」定位不到行为（这份档里它可能没有运行期组件，引擎不会执行它）' % nm)
            if hasattr(self, 'lbl_ac_count'):
                self.lbl_ac_count.config(
                    text='「%s」在这份档里没有可定位的行为' % nm, foreground=ui_theme.T['warn'])
            return
        # 搜索过滤可能把这些行藏起来了 ⇒ 先清空过滤再选中
        try:
            present = set(self.ta.get_children())
            if any(h not in present for h in hits) and hasattr(self, '_q_ac'):
                if (self._q_ac.get() or '').strip():
                    self._q_ac.set('')
                    self._fill_ac_tree()
        except Exception:
            pass
        self._mark_hits(self.ta, getattr(self, '_ac_rows', []), hits)
        if hasattr(self, 'lbl_ac_count'):
            self.lbl_ac_count.config(
                text='显示 %d / %d 条' % (len(self.ta.get_children()),
                                          len(getattr(self, '_ac_rows', []))),
                foreground=ui_theme.T['muted'])
        order = sorted(hits, key=lambda x: int(x))
        self.ta.selection_set(order)
        self.ta.focus(order[0])
        self.ta.see(order[0])
        self.status.set('已按触发页选中项定位：%d 条行为属于「%s」'
                        % (len(hits), '、'.join(sorted(names or sids))))

    def _highlight_events_for_trigger(self):
        """把触发页当前选中的触发对应的事件高亮 + 滚动定位（运行期键链，按 sid，无需地图）。"""
        names = getattr(self, '_sel_trigger_names', set())
        sids = getattr(self, '_sel_trigger_sids', set())
        if not self.doc or not getattr(self, 'te', None) or not getattr(self, '_evlist', None):
            return
        if not names and not sids:
            self._ev_hits = set()
            self._mark_hits(self.te, getattr(self, '_ev_rows', []), set())
            return
        evk, _ack = self._trigger_hit_keys()
        hits = set()
        for i, e in enumerate(self._evlist):
            if e.get('key') in evk or (e.get('owner') and e.get('owner') in sids):
                hits.add(str(i))
        # 兜底：键链没命中时，按「所属触发」列的文字（名字/ID）匹配
        if not hits:
            for i, vals, _t in getattr(self, '_ev_rows', []):
                if len(vals) > 5 and self._owner_col_matches(vals[5], names, sids):
                    hits.add(str(i))
        self._ev_hits = hits
        if not hits:
            self._mark_hits(self.te, getattr(self, '_ev_rows', []), set())
            nm = '、'.join(sorted(names or sids))
            self.status.set('「%s」定位不到事件（这份档里它可能没有运行期组件，引擎不会执行它）' % nm)
            if hasattr(self, 'lbl_ev_count'):
                self.lbl_ev_count.config(
                    text='「%s」在这份档里没有可定位的事件' % nm, foreground=ui_theme.T['warn'])
            return
        # 搜索过滤可能把这些行藏起来了 ⇒ 先清空过滤再选中
        try:
            present = set(self.te.get_children())
            if any(h not in present for h in hits) and hasattr(self, '_q_ev'):
                if (self._q_ev.get() or '').strip():
                    self._q_ev.set('')
                    self._fill_ev_tree()
        except Exception:
            pass
        self._mark_hits(self.te, getattr(self, '_ev_rows', []), hits)
        if hasattr(self, 'lbl_ev_count'):        # 命中后把提示色/文字恢复成普通计数
            self.lbl_ev_count.config(
                text='显示 %d / %d 条' % (len(self.te.get_children()),
                                          len(getattr(self, '_ev_rows', []))),
                foreground=ui_theme.T['muted'])
        order = sorted(hits, key=lambda x: int(x))
        self.te.selection_set(order)
        self.te.focus(order[0])
        self.te.see(order[0])
        self.status.set('已按触发页选中项定位：%d 条事件属于「%s」'
                        % (len(hits), '、'.join(sorted(names or sids))))

    def local_var_editor(self):
        """🔢 局部变量（§176 定版）—— "先写局部变量，写触发时才选得中"那一步。

        引擎机制（gamemd.exe 反汇编 + 玩家 `同触发.SAV` / `T3` 两档对照，2026-10-22）：
          · 存档 `0x248A` 起 100 槽 = `name[40] + value(u8) = 41B`，只是**运行期镜像**
          · ★`LoadLocalVariableNames 0x689B20`：按**地图 `[VariableNames]`**（`i=Name,Default`）
            填名字 + 默认值。它唯一的调用者在**地图读/写**路径（读完紧接着写 Ranking/ParTime）
            ⇒ 那是"读地图/开局"用的，**不是读档**：存档自足，读档不会被地图覆盖
          · 事件 36 = 值 ≠ 0（电平，不是"被设置那一帧"）；事件 37 = 值 == 0
          · 行为 56 = 写 1；行为 57 = 写 0
          · ★变量号：事件在事件对象 **+0x48**，行为在行为对象 **+0xA4**
          · 地编名称框第 1..99 条 = 引擎索引 0..98；地图里的键就是引擎索引
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        try:
            sane = self.doc._lv_sane()
        except Exception:
            sane = False
        if not sane:
            messagebox.showerror('不支持', '这个档的明文在 0x248A 处不像局部变量表'
                                           '（长度不够或内容异常），已拒绝编辑。')
            return
        w = tk.Toplevel(self)
        w.title('局部变量（只写存档 · 本工具不写地图）')
        # ★★2026-10-27 铁律（玩家原话）："任何情况下存档编辑器都不允许有向地图中写入数据的能力"
        #   ⇒ 原来那套"把名字/默认值写进地图 [VariableNames] + 重算 [Digest]"的功能**整体撤除**，
        #     这里只剩"读地图"（显示这号在地图里原本叫什么）与"写存档"（名字/值）。
        w.transient(self)
        # ★2026-10-27：原来 760x640，实测 reqheight=686 ⇒ 底部三行脚注被挤掉（玩家根本没看到），
        #   而且那三行正是"两处各管一段"的关键说明。放到 700。
        w.geometry('760x700')
        pad = dict(padx=8, pady=3)

        # ---- 地图状态条 ----
        mrow = ttk.Frame(w)
        mrow.pack(fill='x', **pad)
        lbl_map = ttk.Label(mrow, text='', foreground=ui_theme.T['ink'], wraplength=520,
                            justify='left')
        lbl_map.pack(side='left')

        def _pick_map():
            p = filedialog.askopenfilename(
                title='选择这个存档用的地图（.map/.mmx/.yrm）',
                filetypes=[('RA2 地图', '*.map *.mmx *.yrm *.mpr'), ('所有文件', '*.*')])
            if not p:
                return
            self.doc._map_path = p
            self.doc._map_path_done = True
            self.doc._mapvars_key = None
            _refresh_map_label()
            _fill()

        ttk.Button(mrow, text='📂 指定地图…', command=_pick_map).pack(side='right')

        def _refresh_map_label():
            try:
                mp = self.doc.scenario_map_path()
            except Exception:
                mp = None
            if mp:
                lbl_map.config(text='地图：%s —— 只读参考（本工具不写地图，一个字节都不改）'
                                    % mp, foreground=ui_theme.T['ok'])
            else:
                lbl_map.config(
                    text='⚠ 找不到地图文件（%s）—— 不影响存档运行（存档自足）；'
                         '本工具本来也不写地图' % self.doc.map_lookup_hint(),
                    foreground=ui_theme.T['danger'])

        ttk.Label(w, text='铁律：本工具只读地图、只写存档（玩家 2026-10-27 定：任何情况下都不允许向地图写数据）。\n'
                          '这一页能做的：① 给某一号起名字　② 改它的当前值 —— 两个都只写进存档；'
                          '事件 36/37 判的就是存档里这个值。\n'
                          '做不到（也不许做）：把名字/默认值写进地图 [VariableNames]。要在地编里选得中，'
                          '请你自己在 FA2 里加（或让地图作者加）。\n'
                          '不写地图照样能跑：触发用的是引擎索引号，名字只是给人看的；读档以存档为准。',
                  foreground=ui_theme.T['ink'], wraplength=735, justify='left').pack(anchor='w', **pad)

        box = ttk.Frame(w)
        box.pack(fill='both', expand=True, padx=8, pady=4)
        tv = ttk.Treeview(box, columns=('fa2', 'idx', 'name', 'defv', 'val', 'src'),
                          show='headings', height=17)
        for c, txt, wd, anchor in (('fa2', '地编第几条', 90, 'center'),
                                   ('idx', '引擎索引', 80, 'center'),
                                   ('name', '变量名', 220, 'w'),
                                   ('defv', '默认值(地图)', 100, 'center'),
                                   ('val', '当前值(存档)', 100, 'center'),
                                   ('src', '定义来源', 90, 'center')):
            tv.heading(c, text=txt)
            tv.column(c, width=wd, anchor=anchor)
        vs = ttk.Scrollbar(box, orient='vertical', command=tv.yview)
        tv.configure(yscrollcommand=vs.set)
        tv.pack(side='left', fill='both', expand=True)
        vs.pack(side='right', fill='y')
        tv.tag_configure('undef', foreground=ui_theme.T['muted_soft'])
        tv.tag_configure('set', foreground=ui_theme.T['ok'])
        tv.tag_configure('maponly', foreground=ui_theme.T['info'])

        def _fill(keep=None):
            sel = keep if keep is not None else (tv.selection() or [None])[0]
            tv.delete(*tv.get_children())
            rows = self.doc.local_var_table()
            for row in rows:
                if row['index'] >= 99:        # 地编只开放 99 条
                    continue
                nm = row['name'] or (row.get('name_map') and '[地图] ' + row['name_map']) \
                    or '（未定义）'
                dv = '' if row['default'] is None else row['default']
                tag = ('set' if row['value'] else '')
                if row['source'] != 'map':
                    tag = 'undef' if not row['name'] else tag
                iid = tv.insert('', 'end',
                                values=(row['fa2'], row['index'], nm, dv,
                                        row['value'],
                                        '地图' if row['source'] == 'map' else '仅存档'),
                                tags=(tag,))
                if sel and iid == sel:
                    tv.selection_set(iid)
                    tv.see(iid)
            n_map = len([r for r in rows if r['source'] == 'map' and r['index'] < 99])
            n_save = len([r for r in rows if r['source'] != 'map'
                          and r['name'] and r['index'] < 99])
            lbl2.config(text='地图里已定义 %d 条（只读参考）；名字只在存档里的 %d 条'
                             '（这些照样能跑，只是 FA2 里选不中 —— 本工具不写地图，'
                             '要在地编里选得中请自己在 FA2 里加）'
                             % (n_map, n_save))

        def _cur():
            s = tv.selection()
            if not s:
                messagebox.showinfo('提示', '先在列表里选一条变量', parent=w)
                return None
            return int(tv.item(s[0], 'values')[1])

        def _define():
            i = _cur()
            if i is None:
                return
            cur = self.doc.local_var(i)
            dlg = tk.Toplevel(w)
            dlg.title('变量 %d：写名字 + 初值（只写存档）' % i)
            dlg.transient(w)
            dlg.grab_set()
            f0 = ttk.Frame(dlg)
            f0.pack(fill='x', padx=10, pady=(8, 2))
            ttk.Label(f0, text='本工具不写地图：名字只存进存档，地编 FA2 里选不中（那一步请自己在 FA2 加）。',
                      foreground=ui_theme.T['warn'], wraplength=430, justify='left').pack(anchor='w')
            f1 = ttk.Frame(dlg)
            f1.pack(fill='x', padx=10, pady=6)
            ttk.Label(f1, text='名字').pack(side='left')
            v_nm = tk.StringVar(value=cur['name'] or cur.get('name_map')
                                or ('V%d' % (i + 1)))
            ttk.Entry(f1, textvariable=v_nm, width=22).pack(side='left', padx=6)
            f2 = ttk.Frame(dlg)
            f2.pack(fill='x', padx=10, pady=6)
            ttk.Label(f2, text='这一档里的当前值（0..255）').pack(side='left')
            v_dv = tk.StringVar(value=str(cur['value'] if cur['value'] is not None else 0))
            ttk.Entry(f2, textvariable=v_dv, width=8).pack(side='left', padx=6)

            def _ok():
                try:
                    info = self.doc.define_local_var(
                        i, v_nm.get().strip(), int(v_dv.get().strip() or '0', 0))
                except Exception as e:
                    messagebox.showerror('写存档失败', str(e), parent=dlg)
                    return
                dlg.destroy()
                _fill()
                self.status.set('变量 %d = %s（值 %d）已写进存档（没动地图）'
                                % (i, info['name'], info['default']))
            fb = ttk.Frame(dlg)
            fb.pack(fill='x', padx=10, pady=8)
            ttk.Button(fb, text='写入存档', command=_ok).pack(side='right')
            ttk.Button(fb, text='取消', command=dlg.destroy).pack(side='right', padx=6)

        def _setval(val):
            i = _cur()
            if i is None:
                return
            try:
                v = self.doc.set_local_var_value(i, val)
            except Exception as e:
                messagebox.showerror('写入失败', str(e), parent=w)
                return
            _fill()
            if v.get('persist') is False:
                self.status.set('变量 %d 存档值 = %d（存档自足，读档即生效；'
                                '地图里该号默认值是 %s，只影响新开局）'
                                % (i, val, v.get('default')))
            else:
                self.status.set('变量 %d 存档值 = %d（尚未保存）' % (i, val))

        def _del():
            i = _cur()
            if i is None:
                return
            if not messagebox.askyesno('确认', '清空"地编第 %d 条"在**存档**里的名字和值？\n'
                                              '（存档只改这一条；地图不动，本工具也不写地图）'
                                              % (i + 1), parent=w):
                return
            try:
                self.doc.clear_local_var(i)
            except Exception as e:
                messagebox.showerror('清空失败', str(e), parent=w)
                return
            _fill()
            self.status.set('变量 %d 在存档里的名字和值已清空（地图没动）' % i)

        bar = ttk.Frame(w)
        bar.pack(fill='x', padx=8, pady=3)
        ttk.Button(bar, text='✏ 写名字+初值…（写存档）', command=_define).pack(side='left')
        ttk.Button(bar, text='存档值置 1', command=lambda: _setval(1)).pack(side='left', padx=4)
        ttk.Button(bar, text='存档值置 0', command=lambda: _setval(0)).pack(side='left', padx=4)
        ttk.Button(bar, text='🧹 清空这一条（写存档）', command=_del).pack(side='left', padx=8)
        ttk.Button(bar, text='关闭', command=w.destroy).pack(side='right')

        lbl2 = ttk.Label(w, text='', foreground=ui_theme.T['ink_2'])
        lbl2.pack(anchor='w', padx=8, pady=(0, 2))
        ttk.Label(w, text='※ 存档那 100 槽（0x248A）是运行态 —— 事件 36/37 判的就是它，读档以存档为准'
                          '（§176 实测：地图默认 1、存档 0 时触发不响应）⇒ 改「存档值」当场就能用。\n'
                          '※ 地图 [VariableNames] 只影响"FA2 里能选中哪些变量 + 新开局的初值"。'
                          '本工具**只读不写**它；要在地编里选得中，请你自己在 FA2 里加。\n'
                          '※ 变量号怎么对：事件 36/37 参数 = 引擎索引（0 起，地编第 N 条 = N−1）；'
                          '行为 56/57 的变量号在行为对象 +164（工具已自动处理）。',
                  foreground=ui_theme.T['muted'], wraplength=735, justify='left').pack(anchor='w', padx=8)
        _refresh_map_label()
        _fill()


    def local_var_wizard(self):
        """🧩 一键「局部变量配方」（§158 配方产品化，2026-10-22 §174）。

        解决的问题：想让「N 栋建筑**全部**到达/进入」才执行结果。
        **别**用「持久 1 + 进入事件」（§159 实测引擎 BUG：进入事件摘不掉对象 ⇒ 计数永不降）。
        正解 = N+1 条触发：
          每栋 `持久 0 · 事件 1 → 行为 56（设置变量 V_i）`
          汇总 `持久 0 · 13 流逝 1 秒（心跳） AND N × 事件 36 → 结果行为`

        ★§181（T10 定案）：汇总触发**必须**带一条计时事件当心跳。事件 36 是纯电平查询，
          没有对象回调；只有进了「定时触发器挂接表」（`ticks>0` = 首个事件是 13）才会被
          轮询 —— 只挂 36 的汇总触发读档后完全不动（工具里看着正常，进游戏就是不出）。
          本向导生成的配方已自动带心跳，不用手加。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        w = tk.Toplevel(self)
        w.title('局部变量配方（N 栋建筑全部到达 → 执行结果）')
        w.transient(self)
        w.geometry('620x520')
        pad = dict(padx=8, pady=3)
        ttk.Label(w, text='★ 汇总触发会自动带一条「13 流逝时间 1 秒」心跳：事件 36 是纯电平查询，'
                          '没有计时事件就不会被引擎轮询（§181 实测）',
                  foreground=ui_theme.T['muted'], wraplength=580, justify='left').pack(anchor='w', **pad)
        # ★★2026-10-27 铁律：整个工具都**不写地图**（玩家：任何情况下都不允许向地图写数据）
        ttk.Label(w, text='★ 铁律：本工具只读地图、只写存档 —— 变量名/值都只落在存档里。'
                          '想在地编 FA2 里也能选中这些变量、或给新开局默认值，'
                          '请你自己在 FA2 里加（本工具不代劳，也不会碰你的 .map）',
                  foreground=ui_theme.T['danger'], wraplength=580, justify='left').pack(anchor='w', **pad)

        ttk.Label(w, text='① 参与的建筑格（每行一个，或逗号/分号分隔，如 73,87）',
                  foreground=ui_theme.T['ink']).pack(anchor='w', **pad)
        tbox = tk.Text(w, height=6, width=58, wrap='word')
        tbox.pack(fill='x', padx=8)
        ttk.Label(w, text='③ 汇总满足后要执行的行为', foreground=ui_theme.T['ink']).pack(anchor='w', **pad)
        r1 = ttk.Frame(w)
        r1.pack(fill='x', padx=8)
        cb_a = ttk.Combobox(r1, width=34, state='readonly')
        akeys = [k for k in sorted(ACTION_TYPES) if k != 0]
        cb_a['values'] = ['%d  %s' % (k, ACTION_TYPES[k][0]) for k in akeys]
        cb_a.pack(side='left')
        if 21 in akeys:
            cb_a.current(akeys.index(21))
        ttk.Label(r1, text='参数').pack(side='left', padx=(10, 0))
        var_aa = tk.StringVar(value='0')
        ttk.Entry(r1, textvariable=var_aa, width=10).pack(side='left', padx=4)

        rp = ttk.Frame(w)
        rp.pack(fill='x', **pad)
        ttk.Label(rp, text='② 单栋触发用的事件').pack(side='left')
        cb_pe = ttk.Combobox(rp, width=30, state='readonly')
        pe_opts = [(1, '进入事件（建筑被单位进入）'),
                   (48, '被任何事物摧毁（含占领/心控/变卖）'),
                   (29, '被摧毁或占领（不含变卖）'),
                   (7, '只算摧毁（碎片/占领/变卖都不算）')]
        cb_pe['values'] = ['%d  %s' % (t, n) for t, n in pe_opts]
        cb_pe.current(0)
        cb_pe.pack(side='left', padx=6)
        ttk.Label(rp, text='←「每栋被打掉一个变量」选 48',
                  foreground=ui_theme.T['muted']).pack(side='left')

        r2 = ttk.Frame(w)
        r2.pack(fill='x', **pad)
        ttk.Label(r2, text='④ 变量起始号（引擎索引，0 = 地编第 1 条）').pack(side='left')
        var_v0 = tk.StringVar(value='0')
        ttk.Entry(r2, textvariable=var_v0, width=6).pack(side='left', padx=4)
        ttk.Label(r2, text='变量名模板').pack(side='left', padx=(8, 0))
        var_nm = tk.StringVar(value='V%d')
        ttk.Entry(r2, textvariable=var_nm, width=8).pack(side='left', padx=4)
        ttk.Label(r2, text='建筑已挂别的触发时：').pack(side='left', padx=(8, 0))
        var_mode = tk.StringVar(value='zero')
        ttk.Radiobutton(r2, text='跳过它', variable=var_mode,
                        value='zero').pack(side='left')
        ttk.Radiobutton(r2, text='强制改挂', variable=var_mode,
                        value='force').pack(side='left', padx=(6, 0))

        r3 = ttk.Frame(w)
        r3.pack(fill='x', **pad)
        ttk.Label(r3, text='⑤ 命名').pack(side='left')
        ttk.Label(r3, text='单栋前缀').pack(side='left', padx=(8, 0))
        var_pn = tk.StringVar(value='到达')
        ttk.Entry(r3, textvariable=var_pn, width=10).pack(side='left', padx=4)
        ttk.Label(r3, text='汇总名').pack(side='left', padx=(10, 0))
        var_rn = tk.StringVar(value='汇总条件')
        ttk.Entry(r3, textvariable=var_rn, width=14).pack(side='left', padx=4)

        lbl = ttk.Label(w, text='', foreground=ui_theme.T['ink_2'], wraplength=590, justify='left')
        lbl.pack(anchor='w', padx=8, pady=(6, 0))

        def _cells():
            raw = tbox.get('1.0', 'end')
            out = []
            for line in raw.replace(';', '\n').split('\n'):
                for seg in line.split(' '):
                    seg = seg.strip().strip('()（）,')
                    if not seg:
                        continue
                    m = re.findall(r'-?\d+', seg)
                    if len(m) >= 2:
                        v = (int(m[0]), int(m[1]))
                        if v not in out:
                            out.append(v)
            return out

        def _from_map():
            b, _i, err = self._mv_sel_bld()
            if err:
                messagebox.showwarning('提示', err, parent=w)
                return
            tbox.insert('end', '%d,%d\n' % (b['x'], b['y']))
            lbl.config(text='已加入 %s (%d,%d)' % (b['type'], b['x'], b['y']))

        def _preview():
            cs = _cells()
            if not cs:
                lbl.config(text='⚠ 还没填建筑格', foreground=ui_theme.T['danger'])
                return None
            a_i = cb_a.current()
            if a_i < 0:
                lbl.config(text='⚠ 先选结果行为', foreground=ui_theme.T['danger'])
                return None
            try:
                v0 = int(var_v0.get().strip() or '1', 0)
                aa = int(var_aa.get().strip() or '0', 0)
            except ValueError:
                lbl.config(text='⚠ 变量号/参数必须是整数', foreground=ui_theme.T['danger'])
                return None
            pe_t = pe_opts[cb_pe.current()][0] if cb_pe.current() >= 0 else 1
            nmp = var_nm.get().strip() or 'V%d'
            lines = ['将新建 %d + 1 = **%d 条触发**（并先给变量命名 %s）：'
                     % (len(cs), len(cs) + 1,
                        '/'.join(nmp.replace('%d', str(v0 + j + 1)) for j in range(len(cs))))]
            for j, (x, y) in enumerate(cs):
                lines.append('  · 「%s%d」持久0 · 事件 %d %s(挂 %d,%d) → 行为 56 设变量 %d（地编第 %d 条）'
                             % (var_pn.get().strip() or '到达', v0 + j + 1, pe_t,
                                event_name(pe_t), x, y, v0 + j, v0 + j + 1))
            lines.append('  · 「%s」持久0 · %d × 事件 36（变量 %s 全被设置）→ 行为 %s(%d)'
                         % (var_rn.get().strip() or '汇总条件', len(cs),
                            '/'.join(str(v0 + j + 1) for j in range(len(cs))),
                            action_name(akeys[a_i]), aa))
            lines.append('※ 同一触发内多事件恒为 AND；持久性强制 0（§157 实测 1 = 永不触发）。')
            lines.append('※ §175：行为 56 的变量号写行为对象 +164（引擎读这里，不是 +0x48）。')
            lbl.config(text='\n'.join(lines), foreground=ui_theme.T['ink_2'])
            return cs, akeys[a_i], aa

        def _go():
            r = _preview()
            if not r:
                return
            cs, at, aa = r
            if not messagebox.askyesno(
                    '确认建立',
                    '将新建 %d 条触发（%d 条单栋 + 1 条汇总），会写进存档内存。\n\n'
                    '改完记得回主窗口「保存」；建议先备份。\n\n继续？' % (len(cs) + 1),
                    parent=w):
                return
            try:
                info = self.doc.make_local_var_recipe(
                    cs, [(at, aa)], var_start=int(var_v0.get().strip() or '0', 0),
                    result_name=(var_rn.get().strip() or '汇总条件'),
                    per_name=(var_pn.get().strip() or '到达') + '%d',
                    var_name=(var_nm.get().strip() or 'V%d'),
                    attach_mode=var_mode.get(),
                    per_event=(pe_opts[cb_pe.current()][0]
                               if cb_pe.current() >= 0 else 1))
            except Exception as e:
                messagebox.showerror('创建失败', str(e), parent=w)
                return
            try:
                self.refresh_records()
                self.refresh_events()
                self.refresh_actions()
            except Exception:
                pass
            n = len(info['per_building'])
            msg = '已建 %d 条单栋 + %s（变量 %s）' % (
                n, '1 条汇总' if info['result'] else '汇总（失败）', info['vars'])
            if info['skipped']:
                msg += '；跳过 %d 栋' % len(info['skipped'])
            self.status.set(msg + '　（尚未保存 —— 请回主窗口「保存」）')
            tail = ('\n\n⚠ ' + '\n⚠ '.join(info['warnings'][:4])) if info['warnings'] else ''
            messagebox.showinfo('完成', msg + tail, parent=w)
            w.destroy()

        bar = ttk.Frame(w)
        bar.pack(fill='x', padx=8, pady=8)
        ttk.Button(bar, text='＋ 用地图页选中格', command=_from_map).pack(side='left')
        ttk.Button(bar, text='预览', command=_preview).pack(side='left', padx=6)
        ttk.Button(bar, text='✔ 生成', command=_go).pack(side='left', padx=6)
        ttk.Button(bar, text='关闭', command=w.destroy).pack(side='right')
        ttk.Label(w, text='说明：单栋触发用「事件 1 进入事件」+「行为 56 设置局部变量」，'
                          '汇总触发用「13 流逝 1 秒（心跳）+ N × 事件 36 局部变量被设置」'
                          '—— 这是社区通行写法，绕开「持久 1 + 多对象」的引擎 BUG（§158/§159）。'
                          '★§181：那条 13 不能省（事件 36 是纯电平查询，没有计时事件就不进'
                          '「定时触发器挂接表」⇒ 读档后永不评估）。',
                  foreground=ui_theme.T['muted'], wraplength=590, justify='left').pack(anchor='w', padx=8)

    def retarget_dialog(self):
        """🔧 改造选中触发的动作 —— 把 M1（游戏内已验证）那条路做成界面。

        为什么必须有它（2026-10-18 交接任务 B）：
          本任务档上「**新建**触发」能写进存档、能挂进容器、内存里也在索引数组里，
          但引擎就是不评估它（内存取证已全部排除，见主文档 §95~§101）；
          而「**改已有触发**的行为」是**游戏内验收通过**的（M1）⇒
          ⇒ 在真调试器把"为什么不评估"查清之前，这是唯一可靠的刷建筑路子。

        只写动作对象的 4 处（长度不变，不碰 7MB 容器红线）：
          +64 行为类型（125 = 将建筑建于…）、+60 参数、+104 建筑名、+88 路径点号。
        目标格没有路径点时自动补一个（`ensure_waypoint_at`，不动已有路径点）。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        idxs = []
        try:
            idxs = [int(x) for x in self.tt.selection()]
        except Exception:
            idxs = []
        trigs = [self.doc.records[i] for i in idxs
                 if 0 <= i < len(self.doc.records) and self.doc.records[i].kind == '触发']
        if not trigs:
            messagebox.showwarning('提示', '请先在「触发」列表里选中一条触发（只能改触发，不能改标签）')
            return
        if len(trigs) > 1:
            messagebox.showwarning('提示', '一次只能改一条触发，请只选一行')
            return
        rec = trigs[0]
        if self._guard_executed(rec.sid):      # ★已执行 ⇒ 改了不生效
            return
        try:
            rows = self.doc.trigger_actions(rec.sid)
            self._rt_rows = rows
            cur = self.doc.trigger_action(rec.sid, ac_index=1)
        except Exception as e:
            messagebox.showerror('读不到动作', '这条触发的动作对象定位失败：\n%s' % e)
            return
        try:
            names = self._bld_type_names()
        except Exception:
            names = []
        vals = ['%s（%s）' % (n, self._type_disp(n)) for n in names]
        ac_name = self.mv_type.get().split('（')[0].strip() if getattr(self, 'mv_type', None) else ''
        sel_cell = (self.mv_state or {}).get('sel') if hasattr(self, 'mv_state') else None

        dlg = tk.Toplevel(self)
        dlg.title('改造选中触发的动作（行为 125：建建筑）')
        dlg.transient(self)
        frm = ttk.Frame(dlg, padding=12)
        frm.grid(sticky='nsew')
        ttk.Label(frm, text='触发：%s　%s（序号 %d，动作偏移 %d）'
                           % (rec.sid, rec.name, cur['index'], cur['action_off']),
                  foreground=ui_theme.T['ink']).grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(frm, text='现状：行为 %d　参数 %d　建筑名 %r　路径点 %d → %s'
                           % (cur['type'], cur['arg'], cur['name'] or '（空）',
                              cur['waypoint'], cur['cell']),
                  foreground=ui_theme.T['muted']).grid(row=1, column=0, columnspan=3, sticky='w', pady=(2, 8))
        ttk.Label(frm, text='建筑类型：').grid(row=2, column=0, sticky='w')
        cb = ttk.Combobox(frm, width=34, state='readonly', values=vals)
        if ac_name in names:
            cb.set(vals[names.index(ac_name)])
        cb.grid(row=2, column=1, columnspan=2, sticky='w', pady=2)
        ttk.Label(frm, text='目标格 x / y：').grid(row=3, column=0, sticky='w')
        rowc = ttk.Frame(frm)
        rowc.grid(row=3, column=1, columnspan=2, sticky='w', pady=2)
        ex = ttk.Entry(rowc, width=7)
        ey = ttk.Entry(rowc, width=7)
        ex.pack(side='left')
        ttk.Label(rowc, text=' , ').pack(side='left')
        ey.pack(side='left')
        if sel_cell:
            ex.insert(0, str(sel_cell[0]))
            ey.insert(0, str(sel_cell[1]))
        ttk.Label(frm, text='（地图页点过格子就自动填好；该格没有路径点会自动补一个）',
                  foreground=ui_theme.T['muted']).grid(row=4, column=1, columnspan=2, sticky='w')
        ttk.Label(frm, text='行为类型：').grid(row=5, column=0, sticky='w', pady=(6, 0))
        rowt = ttk.Frame(frm)
        rowt.grid(row=5, column=1, columnspan=2, sticky='w', pady=(6, 0))
        ea = ttk.Entry(rowt, width=7)
        ea.insert(0, '125')
        ea.pack(side='left')
        ttk.Label(rowt, text='（125 = 将建筑建于…；改成别的类型就不需要建筑名/格子）',
                  foreground=ui_theme.T['muted']).pack(side='left', padx=6)
        cb_ai = ttk.Combobox(frm, width=34, state='readonly',
                             values=['第 %d 条：%s %s @%s' % (r['ordinal'], r['type'],
                                     r['name'] or '（空）', r['cell']) for r in rows])
        if len(rows) > 1:
            ttk.Label(frm, text='改第几条行为：').grid(row=7, column=0, sticky='w')
            cb_ai.current(0)
            cb_ai.grid(row=7, column=1, columnspan=2, sticky='w', pady=(4, 0))
        ttk.Label(frm, text='⚠ 这是**唯一**游戏内验收过的刷建筑路子（M1）：新写触发引擎不执行。\n'
                            '    改完请「另存为新文件」，进游戏后**等该触发自己的计时到期**才会看到建筑；\n'
                            '    悬崖/水面等不可建地形仍会被引擎拒绝。',
                  foreground=ui_theme.T['danger'], justify='left').grid(row=6, column=0, columnspan=3,
                                                             sticky='w', pady=(10, 4))

        def _aci():
            try:
                return int(cb_ai.current()) + 1
            except Exception:
                return 1

        def do_write():
            try:
                ac = int((ea.get() or '125').strip())
            except ValueError:
                messagebox.showerror('参数错', '行为类型要是数字', parent=dlg)
                return
            try:
                if ac == 125:
                    t = cb.get().split('（')[0].strip()
                    if not t:
                        messagebox.showerror('参数错', '请选一个建筑类型', parent=dlg)
                        return
                    cx, cy = int(ex.get().strip()), int(ey.get().strip())
                    lim = self.doc.coord_limit()
                    if not (0 <= cx < lim[0] and 0 <= cy < lim[1]):
                        messagebox.showerror('参数错', '格子 (%d,%d) 超出坐标范围' % (cx, cy),
                                             parent=dlg)
                        return
                    info = self.doc.retarget_trigger(rec.sid, ac_type=125,
                                                     ac_name=t, ac_cell=(cx, cy),
                                                     ac_index=_aci())
                else:
                    info = self.doc.retarget_trigger(rec.sid, ac_type=ac, ac_index=_aci())
            except Exception as e:
                messagebox.showerror('改造失败（存档未改动）', str(e), parent=dlg)
                return
            dlg.destroy()
            try:
                self.refresh_actions()
                self.refresh_records()
            except Exception:
                pass
            msg = ('✅ 已改造触发 %s（%s）：行为 %d→%d，建筑名 %r→%r，路径点 %d→%d %s'
                   % (rec.sid, rec.name, cur['type'], info['type'], cur['name'],
                      info['name'], cur['waypoint'], info['waypoint'], info['cell']))
            self.status.set(msg)
            self._gift_log('[改造触发] ' + msg)
            for w in (info.get('warnings') or []):
                self._gift_log('    ' + w)
            try:                       # 水/坡警告：(71,241) 实测是水，引擎静默拒绝
                near = self._adj_buildings(info.get('cell'))
                if near == 0:
                    self._gift_log('    ⚠ 目标格 %s 周围 2 格内没有已建建筑 —— 水里/坡上会被'
                                   '引擎静默拒绝（写进去了但游戏里什么都不刷）。'
                                   '建议改用紧邻已有建筑的格子。' % (info.get('cell'),))
            except Exception:
                pass
            if hasattr(self, 'mv_info'):
                self.mv_info.config(text='✅ 已改造触发 %s：目标路径点 %d %s（尚未保存到磁盘）'
                                        % (rec.sid, info['waypoint'], info['cell']))
            base = os.path.splitext(os.path.basename(self.doc.path))[0]
            p = filedialog.asksaveasfilename(
                title='改造结果另存为', defaultextension='.SAV',
                initialfile=(base[:8] + '_改' + (info['name'] or ''))[:16] + '.SAV',
                initialdir=os.path.dirname(self.doc.path))
            if p:
                self._finish_save(p)
            else:
                self.status.set('改造完成（未保存到磁盘）')

        ttk.Button(frm, text='写回存档', command=do_write).grid(row=7, column=0, sticky='w')
        ttk.Button(frm, text='关闭', command=dlg.destroy).grid(row=7, column=1, sticky='w')

    def set_repeat_dialog(self):
        """改选中触发的**触发类型（= 它挂的标签的持久性）**。

        ★2026-10-20：触发类型在存档里的位置 = 标签对象 `+0xB0`（= 标签记录 +120）。
          这是**原地改写 4 字节**：不增删对象、不动明文长度 ⇒ 不需要重压，安全性高。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.tt.selection()
        if not sel:
            messagebox.showwarning('提示', '请先选中一条触发（可 Ctrl 多选）')
            return
        recs = [self.doc.records[int(i)] for i in sel]
        recs = [r for r in recs if r.kind == '触发']
        if not recs:
            messagebox.showwarning('提示', '选中的不是触发记录')
            return
        cur = []
        for r in recs:
            try:
                cur.append(self.doc.trigger_repeat_type(r.sid))
            except Exception:
                cur.append(None)
        dlg = tk.Toplevel(self)
        dlg.title('改触发类型')
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=12)
        frm.grid(sticky='nsew')
        ttk.Label(frm, text='选中 %d 条触发：' % len(recs)).grid(row=0, column=0, sticky='w')
        ttk.Label(frm, text='；'.join('%s(%s)' % (r.name, c or '读不出')
                                     for r, c in zip(recs, cur)),
                  wraplength=430, justify='left').grid(row=1, column=0, columnspan=2,
                                                       sticky='w', pady=(0, 8))
        OPTS = ['0 - 一次性·任一挂载（跑完就作废，改已执行的没意义）',
                '1 - 一次性·挂载清空（挂 N 个对象全触发；进入事件是 BUG）',
                '2 - 可重复（跑完还能再触发）']
        ttk.Label(frm, text='新的触发类型：').grid(row=2, column=0, sticky='w')
        cb = ttk.Combobox(frm, width=46, state='readonly', values=OPTS)
        # 预选：全部一致的用当前值，否则默认 0
        same = cur[0] if cur and all(c == cur[0] for c in cur) else None
        cb.current(int(same) if same in ('0', '1', '2') else 0)
        cb.grid(row=2, column=1, sticky='w')
        ttk.Label(frm, text='触发类型 = 它挂的**标签**的持久性（`[Tags]` 第 0 字段），'
                            '存档里就能读到/改写。\n'
                            '这块是**原地改写 4 字节**（标签对象 +0xB0），'
                            '不增删对象、不动明文长度。\n'
                            '⚠ 只有 0 是多年游戏内实测稳定的；1/2 请自行验证。\n'
                            '★持久性 1 = 等「挂载对象全部离场」：引擎只在 CellTag 记一个数'
                            '（还挂着几个对象），有一个离场就 −1，减到 0 才执行一次并销毁标签'
                            '（存档实测：摧毁 0/1/2/3 栋 ⇒ 计数 3/2/1/归零）。\n'
                            '★「离场」不分**炸掉还是卖掉**：卖 3 栋照样 3/2/1/归零并执行结果；'
                            '挂 1 栋炸掉即执行（§161 八档实测）。\n'
                            '⚠ 但三个摧毁类事件不一样（§162，FA2 地编说明）：'
                            '**7 被任一所属方摧毁 = 只算摧毁**（碎片伤害/占领/变卖/心控都不算）；'
                            '**29 = 摧毁/占领/心控/变卖都算，不含"渗透后消失"**；'
                            '**48 = 摧毁/占领/心控/变卖都算，可用于有子机的单位**。\n'
                            '★两个坑：① **挂接 0 个对象**（地图触发）写 1 ⇒ 永远减不到 0 ⇒ 永不触发；'
                            '② **进入事件（1）挂多个对象**写 1 ⇒ 进入不会摘对象、计数不降 ⇒'
                            '**已知 BUG，永不执行** ——'
                            '改用「每栋一条持久 0 的进入触发 → 行为 56 设置局部变量，'
                            '再一条持久 0 的汇总触发（事件 36）」。\n'
                            '⚠ 「任一/全部挂载」只管**标签挂在多个对象**时的事件归属；'
                            '同一触发内的多个事件**始终是全部成立才执行**。\n'
                            '⚠ 已执行（红字）的单次型触发改成 2，'
                            '能否让它"复活"取决于引擎 —— 需要你进游戏验证。',
                  foreground=ui_theme.T['ink_2'], wraplength=430, justify='left'
                  ).grid(row=3, column=0, columnspan=2, sticky='w', pady=(8, 0))

        def ok():
            per = cb.current()
            # ★§157 硬警告：含局部变量事件（36/37）的触发写 1 = 永不触发、写 2 = 反复执行
            if per != 0:
                hits = [r for r in recs if self.doc.local_var_events(r.sid)]
                if hits:
                    tip = self.doc.repeat_pitfall(hits[0].sid, per) or ''
                    if not messagebox.askyesno(
                            '⚠ 局部变量触发别用持久性 ≠ 0',
                            '下面 %d 条触发含局部变量事件（36/37）：\n%s\n\n%s\n\n仍要写 %d 吗？'
                            % (len(hits),
                               '、'.join((r.name or r.sid) for r in hits[:8]),
                               tip, per), parent=dlg):
                        return
            n = ok_n = 0
            errs = []
            for r in recs:
                try:
                    info = self.doc.set_trigger_repeat_type(r.sid, per)
                    n += 1
                    if info['changed']:
                        ok_n += info['changed']
                except Exception as e:
                    errs.append('%s: %s' % (r.name, e))
            if errs:
                messagebox.showerror('部分失败', '\n'.join(errs[:6]), parent=dlg)
            if n:
                self.refresh_records()
                self.status.set('已把 %d 条触发的触发类型改为 %d（%s），共写 %d 个标签'
                                '（尚未保存）' % (n, per, OPTS[per].split(' - ', 1)[1], ok_n))
            dlg.destroy()
        bf = ttk.Frame(frm)
        bf.grid(row=4, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(bf, text='确定', command=ok).pack(side='left', padx=6)
        ttk.Button(bf, text='取消', command=dlg.destroy).pack(side='left')

    # ---------------- §192 标签字段（TagTypeClass，184B 记录 / 164B 对象体）----------------
    def tag_fields_dialog(self):
        """查看/改**标签对象**的全部字段（§192 逆向出来的字段表）。

        字段表（对象体偏移，硬证据 = RTTI + `TagTypeClass::WriteINI` @0x6E6160 + 82 个标签实测）：
          +0x00 4 个 vtable / +0x10 恒 -1 / +0x24 8B ID 串 / +0x64 52B GBK 名字 /
          +0x98 序号 / +0x9C 持久性(= 触发类型) / +0xA0 触发实例键
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.tt.selection()
        if sel:
            recs = [self.doc.records[int(i)] for i in sel]
            sids = [r.sid for r in recs if r.kind == '触发']
        else:
            sids = []
        dlg = tk.Toplevel(self)
        dlg.title('标签字段（TagTypeClass）')
        dlg.transient(self)
        dlg.grab_set()
        frm = ttk.Frame(dlg, padding=10)
        frm.grid(sticky='nsew')
        cols = ('sid', 'name', 'idx', 'per', 'trig', 'vt')
        tv = ttk.Treeview(frm, columns=cols, show='headings', height=14)
        for c, t, w, a in (('sid', '标签 ID', 90, 'w'), ('name', '名字（GBK 52B）', 260, 'w'),
                           ('idx', '序号 +0x98', 84, 'center'),
                           ('per', '持久性 +0x9C', 96, 'center'),
                           ('trig', '触发 +0xA0', 96, 'w'),
                           ('vt', 'vtable', 66, 'center')):
            tv.heading(c, text=t)
            tv.column(c, width=w, anchor=a)
        tv.grid(row=0, column=0, columnspan=3, sticky='nsew')

        tags = self.doc.tag_type_records()
        if sids:
            tags = [t for t in tags if t['trig_sid'] in sids] or tags
        for t in tags:
            tv.insert('', 'end', iid=t['sid'],
                      values=(t['sid'], t['name'], t['index'], t['persist'],
                              t['trig_sid'] or ('0x%08x' % t['trig_key']),
                              'OK' if t['ok_vt'] else '异常'))
        if not sids and not tags:
            ttk.Label(frm, text='这个存档里没有标签对象（= 没有触发）。').grid(
                row=0, column=0, sticky='w')

        ttk.Label(frm, text='新名字（GBK ≤ 51 字节，超出会被拒绝）：').grid(
            row=1, column=0, sticky='w', pady=(8, 0))
        ent = ttk.Entry(frm, width=40)
        ent.grid(row=1, column=1, sticky='w', pady=(8, 0))

        def pick(_e=None):
            sel2 = tv.selection()
            if sel2:
                f = self.doc.tag_fields(sel2[0])
                if f:
                    ent.delete(0, 'end')
                    ent.insert(0, f['name'])
        tv.bind('<<TreeviewSelect>>', pick)

        def apply_name():
            sel2 = tv.selection()
            if not sel2:
                messagebox.showwarning('提示', '先在上表选一个标签', parent=dlg)
                return
            try:
                info = self.doc.set_tag_name(sel2[0], ent.get().strip())
            except Exception as e:
                messagebox.showerror('改名失败', str(e), parent=dlg)
                return
            tv.set(sel2[0], 'name', info['new'])
            self.status.set('标签 %s 名字：%r → %r（%d 字节，尚未保存）'
                            % (sel2[0], info['old'], info['new'], info['len']))

        ttk.Button(frm, text='✔ 应用改名', command=apply_name).grid(
            row=1, column=2, sticky='w', pady=(8, 0), padx=(6, 0))
        h = self.doc.tag_health()
        ttk.Label(frm, text='共 %d 个标签；持久性分布 %s；序号异常 %d 条；ID 重复 %d 个；'
                            'vtable 异常 %d 条\n'
                            '★+0x9C 持久性 = 地图 [Tags] 第 0 字段 = 「触发类型」；'
                            '+0xA0 指向触发实例（C02D1591）。\n'
                            '⚠ 这里只改内存，点你自己的保存流程才写盘。'
                  % (h['count'], h['persist_dist'] or {}, len(h['idx_bad']),
                     len(h['dup_sid']), len(h['bad_vt'])),
                  foreground=ui_theme.T['ink_2'], justify='left', wraplength=560).grid(
            row=2, column=0, columnspan=3, sticky='w', pady=(8, 0))
        ttk.Button(frm, text='关闭', command=dlg.destroy).grid(
            row=3, column=0, columnspan=3, pady=(8, 0))

    # ---------------- §193 新增事件 / 行为（需要先在「触发」页选中一条触发）----------------
    def _target_trigger_sid(self):
        """新增对象的落点 = 「触发」页当前选中的那条触发。"""
        sel = self.tt.selection() if hasattr(self, 'tt') else ()
        if not sel:
            messagebox.showwarning('提示', '请先在「触发」页选中一条触发，再回来点「添加」')
            return None
        try:
            r = self.doc.records[int(sel[0])]
        except Exception:
            return None
        if r.kind != '触发':
            messagebox.showwarning('提示', '选中的不是触发行')
            return None
        return r.sid

    def _add_obj_dialog(self, kind):
        """kind='ev' 添加事件 / 'ac' 添加行为：类型下拉 + 参数。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sid = self._target_trigger_sid()
        if not sid:
            return
        from core import fa2_tables as _ft
        table = _ft.EVENT_TYPES if kind == 'ev' else _ft.ACTION_TYPES
        items = sorted(table.items(), key=lambda kv: int(kv[0]))
        opts = ['%s - %s' % (k, v) for k, v in items if int(k) != 0]
        dlg = tk.Toplevel(self)
        dlg.title('添加事件' if kind == 'ev' else '添加行为')
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=12)
        frm.grid(sticky='nsew')
        ttk.Label(frm, text='目标触发：%s' % sid).grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(frm, text='类型').grid(row=1, column=0, sticky='e', padx=(0, 6), pady=(8, 4))
        cb = ttk.Combobox(frm, width=42, values=opts, state='readonly')
        cb.current(0)
        cb.grid(row=1, column=1, sticky='w', pady=(8, 4))
        ttk.Label(frm, text='参数').grid(row=2, column=0, sticky='e', padx=(0, 6))
        var = tk.StringVar(value='0')
        ttk.Spinbox(frm, from_=0, to=9999, textvariable=var, width=12).grid(
            row=2, column=1, sticky='w')
        ttk.Label(frm, text='★事件 13「流逝时间」的参数 = 秒数；\n'
                            '行为 56/57 的参数 = 局部变量号（引擎索引，0 起）。\n'
                            '新增的事件会接在**链尾**（不会抢走第一位的"心跳"）。',
                  foreground=ui_theme.T['ink_2'], justify='left').grid(
            row=3, column=0, columnspan=2, sticky='w', pady=(8, 0))

        def ok():
            try:
                num = int(str(cb.get()).split(' - ', 1)[0])
                argv = int(var.get() or 0)
            except Exception:
                messagebox.showerror('参数无效', '类型/参数必须是数字', parent=dlg)
                return
            try:
                if kind == 'ev':
                    r = self.doc.add_event(sid, num, argv)
                    self.refresh_events()
                else:
                    r = self.doc.add_action(sid, num, argv)
                    self.refresh_actions()
            except Exception as e:
                messagebox.showerror('添加失败', str(e), parent=dlg)
                return
            self.status.set('已给触发 %s 添加%s（类型 %s，参数 %s；共 %d 个，尚未保存）'
                            % (sid, '事件' if kind == 'ev' else '一条行为', num, argv,
                               r['total']))
            dlg.destroy()

        bf = ttk.Frame(frm)
        bf.grid(row=4, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(bf, text='确定', command=ok).pack(side='left', padx=6)
        ttk.Button(bf, text='取消', command=dlg.destroy).pack(side='left')

    def add_event_dialog(self):
        self._add_obj_dialog('ev')

    def add_action_dialog(self):
        self._add_obj_dialog('ac')

    # ---------------- §193 事件 / 行为 列表化：上下移 + 单条删除 ----------------
    def _owner_sids(self):
        try:
            return self.doc.runtime_owners()
        except Exception:
            return ({}, {})

    def _sel_event(self):
        sel = self.te.selection() if hasattr(self, 'te') else ()
        if not sel:
            messagebox.showwarning('提示', '请先在事件列表里选中一条')
            return None
        try:
            return self._evlist[int(sel[0])]
        except Exception:
            return None

    def _sel_action(self):
        sel = self.ta.selection() if hasattr(self, 'ta') else ()
        if not sel:
            messagebox.showwarning('提示', '请先在行为列表里选中一条')
            return None
        try:
            return self._aclist[int(sel[0])]
        except Exception:
            return None

    def move_sel_event(self, delta):
        """把选中事件在**它所属触发内**上移/下移一格（§193）。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        ev = self._sel_event()
        if not ev:
            return
        sid = self.doc.owner_sid_of_event(ev.get('key'))
        if not sid:
            messagebox.showwarning('提示', '这条事件没有运行期归属（不属于完整六件套触发），无法排序')
            return
        lst = self.doc.trigger_event_list(sid)                    # 链序（引擎看的顺序）
        idx = next((i for i, e in enumerate(lst) if e.get('key') == ev.get('key')), None)
        if idx is None:
            messagebox.showwarning('提示', '在触发 %s 的事件链里没找到这条事件，已放弃' % sid)
            return
        # ★按钮要跟着**界面显示的顺序**走：列表是按明文里对象先后填的，而链序可能是反的
        #   （新建触发是头插）。所以按"显示顺序"找相邻行，再换算成链序的 ±1。
        shown = sorted([e for e in self.doc.events()
                        if self.doc.owner_sid_of_event(e.get('key')) == sid],
                       key=lambda e: e['obj'])
        pos = next((i for i, e in enumerate(shown) if e.get('key') == ev.get('key')), None)
        if pos is None or not (0 <= pos + delta < len(shown)):
            self.status.set('已经是%s了（共 %d 条事件）' % ('第一条' if delta < 0 else '最后一条',
                                                        len(shown)))
            return
        nkey = shown[pos + delta].get('key')
        nidx = next((i for i, e in enumerate(lst) if e.get('key') == nkey), None)
        d_chain = 0 if nidx is None else (nidx - idx)
        if d_chain not in (-1, 1):
            messagebox.showwarning('提示', '这两条事件在链上不相邻，暂不支持直接交换')
            return
        try:
            r = self.doc.move_event(sid, idx, d_chain)
        except Exception as e:
            messagebox.showerror('排序失败', str(e))
            return
        self.refresh_events()
        self.status.set('触发 %s：事件顺序已调整（第 %d 位 → 第 %d 位，尚未保存）'
                        % (sid, idx + 1, r['to'] + 1))
        if r.get('warn'):
            messagebox.showwarning('⚠ 心跳事件被搬动', r['warn'])

    def move_sel_action(self, delta):
        """把选中行为在该触发内上移/下移一格 —— 行为是按顺序执行的。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        ac = self._sel_action()
        if not ac:
            return
        sid = self.doc.owner_sid_of_action(ac.get('obj'))
        if not sid:
            messagebox.showwarning('提示', '这条行为没有运行期归属（不属于完整六件套触发），无法排序')
            return
        lst = self.doc.trigger_actions(sid)
        idx = next((i for i, a in enumerate(lst)
                    if a['action_off'] == ac.get('obj')), None)
        if idx is None:
            messagebox.showwarning('提示', '在触发 %s 的行为表里没找到这一条，已放弃' % sid)
            return
        if not (0 <= idx + delta < len(lst)):
            self.status.set('已经是%s了（共 %d 条行为）' % ('第一条' if delta < 0 else '最后一条',
                                                        len(lst)))
            return
        try:
            self.doc.move_action(sid, idx, delta)
        except Exception as e:
            messagebox.showerror('排序失败', str(e))
            return
        self.refresh_actions()
        self.status.set('触发 %s：第 %d 条行为已%s（尚未保存）'
                        % (sid, idx + 1, '上移' if delta < 0 else '下移'))

    def remove_sel_event(self):
        """删除选中的**事件对象**（§193）；最后一个不允许删（触发会永远不成立）。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        ev = self._sel_event()
        if not ev:
            return
        sid = self.doc.owner_sid_of_event(ev.get('key'))
        if not sid:
            messagebox.showwarning('提示', '这条事件没有运行期归属，无法删除')
            return
        lst = self.doc.trigger_event_list(sid)
        idx = next((i for i, e in enumerate(lst) if e.get('key') == ev.get('key')), None)
        if idx is None:
            messagebox.showwarning('提示', '在这个触发的事件链里没找到它，已放弃')
            return
        if not messagebox.askyesno(
                '删除事件',
                '要从触发 %s 里删掉这条事件吗？\n\n    %s（类型 %s，参数 %s）\n\n'
                '会从存档明文里移除一整个事件对象，长度会变短；\n'
                '建议先「另存为新文件」再改。' % (sid, ev.get('name'), ev.get('type'),
                                                 ev.get('arg'))):
            return
        try:
            r = self.doc.remove_event(sid, idx)
        except Exception as e:
            messagebox.showerror('删除失败', str(e))
            return
        self.refresh_events()
        self.status.set('已删除触发 %s 的 1 个事件（移除 %d 字节，剩 %d 个，尚未保存）'
                        % (sid, r['size'], r['left']))

    def remove_sel_action(self):
        """删除选中的**行为对象**（§193）；最后一条不允许删（触发就没效果了）。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        ac = self._sel_action()
        if not ac:
            return
        sid = self.doc.owner_sid_of_action(ac.get('obj'))
        if not sid:
            messagebox.showwarning('提示', '这条行为没有运行期归属，无法删除')
            return
        lst = self.doc.trigger_actions(sid)
        idx = next((i for i, a in enumerate(lst) if a['action_off'] == ac.get('obj')), None)
        if idx is None:
            messagebox.showwarning('提示', '在这个触发的行为表里没找到它，已放弃')
            return
        if not messagebox.askyesno(
                '删除行为',
                '要从触发 %s 里删掉这条行为吗？\n\n    %s（类型 %s，参数 %s）\n\n'
                '会移除 168 字节并把后面行为的序号各减 1。' % (
                    sid, ac.get('name'), ac.get('type'), ac.get('arg'))):
            return
        try:
            r = self.doc.remove_action(sid, idx)
        except Exception as e:
            messagebox.showerror('删除失败', str(e))
            return
        self.refresh_actions()
        self.status.set('已删除触发 %s 的 1 条行为（剩 %d 条，尚未保存）' % (sid, r['left']))

    def _tree_scrolled(self, parent, columns, height=16):
        """建一棵**带横竖滚动条**的 Treeview，返回 `(容器, tree)`（不 pack 容器，由调用方定位置）。

        ★2026-10-28（玩家要求）：事件 / 行为页原来只有 `pack(fill=both, expand=True)`，
          既没有竖滚动条也没有横滚动条 —— 大任务档上千条只能靠滚轮，列宽超出时还看不到右半边。
          触发页早就有这两条（340 行起），这里抽出来复用，样式跟触发页保持一致。
        """
        wrap = ttk.Frame(parent)
        tv = ttk.Treeview(wrap, columns=columns, show='headings', height=height)
        vsb = ttk.Scrollbar(wrap, orient='vertical', command=tv.yview)
        hsb = ttk.Scrollbar(wrap, orient='horizontal', command=tv.xview)
        tv.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side='right', fill='y')
        hsb.pack(side='bottom', fill='x')
        tv.pack(side='left', fill='both', expand=True)
        return wrap, tv
