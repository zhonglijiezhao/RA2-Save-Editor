# -*- coding: utf-8 -*-
"""RA2 / 尤里的复仇 存档编辑器 —— 图形界面

用法: 双击“启动.bat”，或  D:\\py\\pythonw.exe RA2存档编辑器.py

功能:
  1. 扫描游戏目录的存档，显示 任务名 / 玩家阵营 / 时间 / 金钱
  2. 改金钱（权威镜像 + 主字段，双写）
  3. 触发列表：显示启用状态（= 记录 +123 字节）；支持 启用 / 禁用（只写这 1 个字节）
  4. 明文搜索（GBK 文本 或 十六进制）与十六进制浏览
  5. 所属方金钱：12 个阵营各自的资金（经济结构，双副本同写）
  6. 事件 / 行为：下拉框选类型（编号与地编一一对应）+ 参数编辑（只改已启用的触发，安全）
  7. 保存：默认另存为新文件（绝不覆盖原档）；可选原地保存（自动备份）
"""
import os, sys, re, struct, threading, queue, time, traceback
import json, subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'core'))
from editor_core import (SaveDoc, MONEY_SIG, MIRROR_DELTA,   # noqa: E402
                         EVENT_TYPES, ACTION_TYPES, event_name, event_ptype,
                         script_action_name, SCRIPT_PARAM_TYPES,
                         action_name, action_ptype, event_note, action_note,
                         ENGINE_PTYPE_TEXT)
import game_ini                                            # noqa: E402  规则文件（rules/art）
import ai_objects                                          # noqa: E402  AI 三大件（小队/特遣/脚本）

GAME_DIR_DEFAULT = r'E:\YURI'
BG = '#f5f6f8'
FG = '#1f2937'
ACCENT = '#2563eb'
UNIT_HOUSE_INHERIT = '（沿用模板）'      # 放单位时"不改主人"的选项（默认）
UNIT_VET_INHERIT = '（沿用模板）'        # 放单位时"不改等级"的选项（默认）
UNIT_VET_CHOICES = (UNIT_VET_INHERIT, '新兵', '老兵', '精英')
_VET_BY_LABEL = {'新兵': 0, '老兵': 1, '精英': 2}


class App(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        self.title('RA2 / 尤里的复仇 — 存档编辑器')
        self.geometry('1180x720')
        self.minsize(980, 600)
        self.configure(bg=BG)
        self.doc = None
        self._warned_zero = False        # 「-无事件-」(0) 告警每次载入只弹一次
        self.game_dir = tk.StringVar(value=GAME_DIR_DEFAULT)
        self.status = tk.StringVar(value='就绪')
        self.q = queue.Queue()
        # 后台任务状态 —— 用于「看门狗」，避免进度条永远转（见 _poll）
        self._pending_load = False
        self._t_load = 0.0
        self._saving = False
        self._t_save = 0.0
        self._scanning = False
        self._build_style()
        self._build_ui()
        self.after(120, self._poll)
        self.refresh_list()

    # ---------------- UI ----------------
    def _build_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use('clam')
        except Exception:
            pass
        st.configure('.', background=BG, foreground=FG, font=('Microsoft YaHei UI', 9))
        st.configure('TFrame', background=BG)
        st.configure('TLabel', background=BG, foreground=FG)
        st.configure('TButton', padding=(10, 5))
        st.configure('Accent.TButton', padding=(10, 5))
        st.configure('Treeview', rowheight=22, fieldbackground='white')
        st.configure('Treeview.Heading', font=('Microsoft YaHei UI', 9, 'bold'))
        st.configure('TLabelframe', background=BG)
        st.configure('TLabelframe.Label', background=BG, foreground=ACCENT,
                     font=('Microsoft YaHei UI', 9, 'bold'))

    def _build_ui(self):
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill='x')
        ttk.Label(top, text='游戏目录').pack(side='left')
        ttk.Entry(top, textvariable=self.game_dir, width=34).pack(side='left', padx=6)
        ttk.Button(top, text='刷新列表', command=self.refresh_list).pack(side='left')
        ttk.Button(top, text='打开存档…', command=self.open_dialog).pack(side='left', padx=6)
        ttk.Separator(top, orient='vertical').pack(side='left', fill='y', padx=8)
        self.btn_save_new = ttk.Button(top, text='另存为新文件', command=self.save_as,
                                       state='disabled')
        self.btn_save_new.pack(side='left')
        self.btn_save_in = ttk.Button(top, text='原地保存(自动备份)', command=self.save_inplace,
                                      state='disabled')
        self.btn_save_in.pack(side='left', padx=6)
        ttk.Separator(top, orient='vertical').pack(side='left', fill='y', padx=8)
        self.btn_map_auto = ttk.Button(top, text='自动匹配地图', command=self.auto_map,
                                       state='disabled')
        self.btn_map_auto.pack(side='left')
        self.btn_map_pick = ttk.Button(top, text='选择地图…', command=self.choose_map,
                                       state='disabled')
        self.btn_map_pick.pack(side='left', padx=6)

        body = ttk.PanedWindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=10, pady=6)

        # 左：存档列表
        left = ttk.Labelframe(body, text='存档列表', padding=6)
        cols = ('file', 'title', 'time', 'money')
        self.tree = ttk.Treeview(left, columns=cols, show='headings', height=20)
        for c, t, w in (('file', '文件', 180), ('title', '任务', 150),
                        ('time', '存档时间', 130), ('money', '金钱', 80)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor='w')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<Double-1>', lambda e: self.open_selected())
        self.tree.bind('<Return>', lambda e: self.open_selected())
        left.pack(in_=body, side='left', fill='both', expand=False)
        body.add(left)

        # 右：功能页
        nb = ttk.Notebook(body)
        nb.pack(in_=body, side='left', fill='both', expand=True)
        body.add(nb)

        nb.add(self._tab_overview(nb), text='  概览 / 金钱  ')
        nb.add(self._tab_houses(nb), text='  所属方金钱  ')
        nb.add(self._tab_triggers(nb), text='  触发  ')
        nb.add(self._tab_events(nb), text='  事件  ')
        nb.add(self._tab_actions(nb), text='  行为  ')
        nb.add(self._tab_attach(nb), text='  挂接  ')
        nb.add(self._tab_mapview(nb), text='  地图 / 选位  ')
        nb.add(self._tab_gift(nb), text='  赠送建筑  ')
        nb.add(self._tab_search(nb), text='  明文搜索 / 浏览  ')
        # ★2026-10-18 新增：AI 三大件（作战小队 / 特遣部队 / 动作脚本）—— 只读
        nb.add(self._tab_ai(nb), text='  作战小队  ')
        self.nb = nb
        nb.bind('<<NotebookTabChanged>>', self._on_tab_changed)

        bar = ttk.Frame(self, padding=(10, 4))
        bar.pack(fill='x')
        ttk.Label(bar, textvariable=self.status, foreground='#4b5563').pack(side='left')
        self.pbar = ttk.Progressbar(bar, mode='indeterminate', length=140)
        self.pbar.pack(side='right')

    def _tab_overview(self, nb):
        f = ttk.Frame(nb, padding=12)
        self.info_labels = {}
        keys = ['文件', '文件大小', '明文大小', '块数', '任务名', '玩家阵营', '开始时间', '存档时间']
        for i, k in enumerate(keys):
            ttk.Label(f, text=k, width=10, anchor='e').grid(row=i, column=0, sticky='e', pady=3)
            v = ttk.Label(f, text='-', anchor='w')
            v.grid(row=i, column=1, sticky='w', padx=8)
            self.info_labels[k] = v

        mf = ttk.Labelframe(f, text='金钱', padding=10)
        mf.grid(row=len(keys) + 1, column=0, columnspan=3, sticky='we', pady=(14, 4))
        ttk.Label(mf, text='当前').grid(row=0, column=0, sticky='e')
        self.lbl_money = ttk.Label(mf, text='-', foreground=ACCENT,
                                   font=('Consolas', 12, 'bold'))
        self.lbl_money.grid(row=0, column=1, sticky='w', padx=8)
        ttk.Label(mf, text='改为').grid(row=1, column=0, sticky='e', pady=(8, 0))
        self.ent_money = ttk.Entry(mf, width=16)
        self.ent_money.grid(row=1, column=1, sticky='w', padx=8, pady=(8, 0))
        ttk.Button(mf, text='应用', command=self.apply_money).grid(row=1, column=2, padx=6,
                                                                   pady=(8, 0))
        ttk.Label(mf, text='（范围 0 ~ 16,777,215；写入权威镜像与主字段）',
                  foreground='#6b7280').grid(row=2, column=0, columnspan=3, sticky='w',
                                             pady=(8, 0))
        self.lbl_money_field = ttk.Label(f, text='', foreground='#6b7280')
        self.lbl_money_field.grid(row=len(keys) + 2, column=0, columnspan=3, sticky='w')
        return f

    def _tab_houses(self, nb):
        f = ttk.Frame(nb, padding=8)
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
                  foreground='#6b7280').pack(side='left', padx=8)

        ttk.Label(f, text='说明：每个所属方的金钱存在「经济结构」里 —— 60 字节固定模板 → 阵营位掩码(=1<<注册序) → 3 字节金额，'
                          '其后 +304 字节还有一份副本，写入时两份一起改。当前玩家另写它自己的镜像字段与主字段。'
                          '定位方法已用「独特金额 + 重开新档」实测验证（值与该阵营在地图里的 Credits×100 逐一吻合）。',
                  foreground='#6b7280', wraplength=880, justify='left').pack(anchor='w', pady=(8, 0))
        return f

    def _tab_triggers(self, nb):
        f = ttk.Frame(nb, padding=8)
        # ★2026-10-20（交接 next-step 3）：**触发类型提成独立列**（以前只塞在状态文字尾部），
        #   顺手加「标签数」——一个触发可挂多个标签，标签数 >1 时类型取"任一为 2 算重复"。
        cols = ('id', 'name', 'kind', 'state', 'rep', 'tags', 'mapinfo', 'note')
        # 列变多了 ⇒ 给树加横竖滚动条（窄窗口也能看全）
        tvw = ttk.Frame(f, height=18)
        self.tt = ttk.Treeview(tvw, columns=cols, show='headings', height=18)
        for c, t, w in (('id', '触发ID', 82), ('name', '名称', 176), ('kind', '类别', 52),
                        ('state', '状态', 150), ('rep', '触发类型', 104),
                        ('tags', '标签数', 54), ('mapinfo', '地图（条件/行为）', 130),
                        ('note', '说明', 170)):
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
        self.lbl_trig_count = ttk.Label(qrow, text='', foreground='#6b7280')
        self.lbl_trig_count.pack(side='left', padx=10)
        ttk.Label(qrow, text='（Esc 清空；可按 触发ID / 名称 / 说明 / 状态 / 触发类型 搜）',
                  foreground='#9ca3af').pack(side='left')
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
        # ★2026-10-20 玩家要求：原「新写触发…」向导已删除，新建统一走「触发编辑器」
        ttk.Button(row, text='➕ 新建触发…',
                   command=self.new_trigger_via_editor).pack(side='left', padx=6)
        # ★2026-10-20：仿 FA2「触发编辑器」——一个窗口改完名字/国家/类型/难度/事件/行为/删除
        ttk.Button(row, text='✏ 触发编辑器…',
                   command=self.open_trigger_editor).pack(side='left', padx=6)
        # ★2026-10-22 §174：一键「局部变量法」—— N 栋建筑全部到达才执行结果。
        #   避开两个已知引擎坑：持久 1 的离场记账被其它事件卡住（§163）、
        #   进入事件挂 ≥2 对象是 BUG（§159）。
        # ★2026-10-22 §175：地编流程第一步「先写局部变量」——名称 + 值直接落在
        #   明文 0x248A 起的 100 个 41B 定长槽里（name[40] + value u8）。
        ttk.Button(row, text='🔢 局部变量…',
                   command=self.local_var_editor).pack(side='left', padx=6)
        ttk.Button(row, text='🧩 局部变量配方…',
                   command=self.local_var_wizard).pack(side='left', padx=6)
        ttk.Button(row, text='🛠 修复缺组件触发…',
                   command=self.repair_runtime_dialog).pack(side='left', padx=6)
        # ★2026-10-18 交接任务 B：把 M1 验证过的「改已有触发的动作」产品化。
        #   新建触发引擎不执行（内存取证已定案）⇒ 改已有触发是唯一游戏内验收过的刷建筑路子。
        ttk.Button(row, text='🔧 改造选中触发的动作…',
                   command=self.retarget_dialog).pack(side='left', padx=6)
        ttk.Label(row, text='双击 / 空格 也可切换；选中多行可批量',
                  foreground='#6b7280').pack(side='left', padx=10)
        ttk.Label(f, text='启用 / 禁用 = 写触发对象里的 **1 个字节**：该对象含恒定「触发类 GUID」，'
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
                  foreground='#374151', wraplength=880, justify='left').pack(anchor='w', pady=(6, 0))
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
        rec = next((r for r in self.doc.records if r.kind == '触发' and r.sid == sid), None)
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
            self.tt.tag_configure('nort', foreground='#9ca3af')      # 无运行期组件 ⇒ 灰
            self.tt.tag_configure('done', foreground='#b91c1c')      # 已执行 ⇒ 红（改不了）
            self.tt.tag_configure('warn', foreground='#b45309')      # 已挂钩/可能已触发 ⇒ 橙
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
                  foreground='#b91c1c').grid(row=0, column=0, columnspan=2, sticky='w')
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
            tv.tag_configure('risk', foreground='#b91c1c')     # 确定已执行 ⇒ 再触发风险
            tv.tag_configure('mid', foreground='#b45309')      # 分不清/类型未读出 ⇒ 仍可能再触发
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
                  foreground='#6b7280').grid(row=2, column=1, sticky='w')

        # ★2026-10-20（交接 next-step 2）：**已执行警示** —— 对跑过的单次型触发点修复
        #   = 重新武装，条件再满足会**再触发一次**。以前这里一声不响，容易把玩家坑了。
        ttk.Label(
            frm,
            text='⚠ 红字行 = 确定已执行过（CellTag 还在，TagNode 被引擎回收）：修复 = 把这条\n'
                 '　 单次型触发「重新武装」，条件一满足就会「再触发一次」\n'
                 '　（刷建筑 / 给钱 / 放援军都会再来一遍）。\n'
                 '　 橙字行 = 组件全无或重复类型读不出，分不清跑没跑过 —— 同样可能再触发。\n'
                 '　 修复前会二次确认，并列出具体是哪几条。',
            foreground='#b91c1c', justify='left').grid(
                row=3, column=0, columnspan=2, sticky='w', pady=(6, 0))
        if n_risk or n_mid:
            ttk.Label(frm, text='本档：确定已执行 %d 条　分不清/类型未读出 %d 条（共 %d 条缺组件）'
                                % (n_risk, n_mid, len(bad)),
                      foreground='#374151').grid(
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

        ★2026-10-20 二次修正（玩家实测：「被攻击」类触发本来就没有 TagNode，不是废触发）：
          判"执行过没有"不能只看 TagNode 在不在，要看**事件对象的运行期指针**
          （`+0x50..+0x68`）有没有被引擎写过 —— 见 `SaveDoc.trigger_event_live()`。

        等级：'high' = 确定执行过（CellTag 还在 / 事件指针非零）⇒ 修复 = 重新武装
              'mid'  = 分不清（组件全无且事件指针读不出来）
              'low'  = 事件驱动型（本来就没有 TagNode）/ 重复型 / 定义禁用 / 只缺 CellTag
        """
        rep = None
        try:
            rep = self.doc.trigger_repeat_type(s['sid'])
        except Exception:
            rep = None
        rep_txt = (getattr(self.doc, 'REPEAT_TEXT', {}).get(rep, '型' + rep)
                   if rep else '未读出')
        try:
            live = self.doc.trigger_event_live(s['sid'])
        except Exception:
            live = True          # 读不出来就按"可能执行过"处理（宁可提示）
        if s['record_enabled'] is False:
            return ('定义即禁用（未武装）', 'low', rep_txt)
        if s['has_node']:
            return ('有 TagNode、缺 CellTag', 'low', rep_txt)
        # 走到这里 = 有实例但没 TagNode ⇒ 要看事件的运行期指针
        if rep == '2':
            return ('重复型跑完（本来就会再跑）', 'low', rep_txt)
        if not live:
            # 事件驱动型：被攻击/被摧毁/进房子这类，引擎靠回调驱动，天然没有 TagNode
            return ('事件驱动型（本来就没有 TagNode·未执行）', 'low', rep_txt)
        if s['has_cell']:
            return ('执行后被回收（CellTag 还在）', 'high', rep_txt)
        return ('执行后被回收（事件已处理·组件全回收）', 'high', rep_txt)

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
                  foreground='#b91c1c').grid(row=0, column=0, columnspan=2, sticky='w')
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
            tv.tag_configure('risk', foreground='#b91c1c')     # 确定已执行 ⇒ 再触发风险
            tv.tag_configure('mid', foreground='#b45309')      # 分不清/类型未读出 ⇒ 仍可能再触发
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
                  foreground='#6b7280').grid(row=2, column=1, sticky='w')

        # ★2026-10-20（交接 next-step 2）：**已执行警示** —— 对跑过的单次型触发点修复
        #   = 重新武装，条件再满足会**再触发一次**。以前这里一声不响，容易把玩家坑了。
        ttk.Label(
            frm,
            text='⚠ 红字行 = 确定已执行过（CellTag 还在，TagNode 被引擎回收）：修复 = 把这条\n'
                 '　 单次型触发「重新武装」，条件一满足就会「再触发一次」\n'
                 '　（刷建筑 / 给钱 / 放援军都会再来一遍）。\n'
                 '　 橙字行 = 组件全无或重复类型读不出，分不清跑没跑过 —— 同样可能再触发。\n'
                 '　 修复前会二次确认，并列出具体是哪几条。',
            foreground='#b91c1c', justify='left').grid(
                row=3, column=0, columnspan=2, sticky='w', pady=(6, 0))
        if n_risk or n_mid:
            ttk.Label(frm, text='本档：确定已执行 %d 条　分不清/类型未读出 %d 条（共 %d 条缺组件）'
                                % (n_risk, n_mid, len(bad)),
                      foreground='#374151').grid(
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
    HIT_BG = '#fde68a'          # 命中行底色（浅琥珀，浅色主题下足够醒目）
    HIT_FG = '#7c2d12'

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
            idx = self.nb.index(self.nb.select())
            if idx == 3:                                # 事件页（第4个标签）
                self._highlight_events_for_trigger()
            elif idx == 4:                              # ★行为页（第5个标签）
                self._highlight_actions_for_trigger()
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
                    text='「%s」在这份档里没有可定位的行为' % nm, foreground='#b45309')
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
                foreground='#6b7280')
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
                    text='「%s」在这份档里没有可定位的事件' % nm, foreground='#b45309')
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
                foreground='#6b7280')
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
        w.title('局部变量（名字/默认值写地图，触发里的号 = 引擎索引）')
        # ★§191：写地图时会**自动重算 [Digest]**（FA2sp 的完整性校验），
        #   否则游戏会提示「地图被人修改」。首次改写留 .bak。
        w.transient(self)
        w.geometry('760x640')
        pad = dict(padx=8, pady=3)

        # ---- 地图状态条 ----
        mrow = ttk.Frame(w)
        mrow.pack(fill='x', **pad)
        lbl_map = ttk.Label(mrow, text='', foreground='#111827', wraplength=520,
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
                lbl_map.config(text='地图：%s（地编里能选中的变量 + 开局默认值在这；'
                                    '存档里那份是运行态，读档以存档为准）' % mp,
                               foreground='#065f46')
            else:
                lbl_map.config(
                    text='⚠ 找不到地图文件（%s）——不影响存档运行（存档自足），但 FA2 里'
                         '看不到这些变量、新开局也没有默认值' % self.doc.map_lookup_hint(),
                    foreground='#b91c1c')

        ttk.Label(w, text='地编流程：先在这里写变量（名字 + 默认值）→ 再写触发时选它。'
                          '　默认值就是"读档时它的值"；游戏里用行为 56/57 改它。',
                  foreground='#111827', wraplength=735, justify='left').pack(anchor='w', **pad)

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
        tv.tag_configure('undef', foreground='#9ca3af')
        tv.tag_configure('set', foreground='#047857')
        tv.tag_configure('maponly', foreground='#1d4ed8')

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
            lbl2.config(text='地图里已定义 %d 条；只在存档镜像里有名字的 %d 条'
                             '（那些读档会消失）' % (n_map, n_save))

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
            dlg.title('定义变量 %d（写地图）' % i)
            dlg.transient(w)
            dlg.grab_set()
            f1 = ttk.Frame(dlg)
            f1.pack(fill='x', padx=10, pady=6)
            ttk.Label(f1, text='名字').pack(side='left')
            v_nm = tk.StringVar(value=cur['name'] or cur.get('name_map')
                                or ('V%d' % (i + 1)))
            ttk.Entry(f1, textvariable=v_nm, width=22).pack(side='left', padx=6)
            f2 = ttk.Frame(dlg)
            f2.pack(fill='x', padx=10, pady=6)
            ttk.Label(f2, text='默认值（读档后的初值 0..255）').pack(side='left')
            v_dv = tk.StringVar(value=str(cur['default'] if cur['default'] is not None else 0))
            ttk.Entry(f2, textvariable=v_dv, width=8).pack(side='left', padx=6)

            def _ok():
                try:
                    info = self.doc.define_local_var(
                        i, v_nm.get().strip(), int(v_dv.get().strip() or '0', 0))
                except Exception as e:
                    messagebox.showerror('写地图失败', str(e), parent=dlg)
                    return
                dlg.destroy()
                _fill()
                self.status.set('变量 %d = %s（默认 %d）已写进地图 %s'
                                % (i, info['name'], info['default'], info['map']))
            fb = ttk.Frame(dlg)
            fb.pack(fill='x', padx=10, pady=8)
            ttk.Button(fb, text='写入地图', command=_ok).pack(side='right')
            ttk.Button(fb, text='取消', command=dlg.destroy).pack(side='right', padx=6)

        def _setdef():
            i = _cur()
            if i is None:
                return
            cur = self.doc.local_var(i)
            if cur['source'] != 'map':
                messagebox.showinfo('提示', '这条还没在地图里定义，请先用「定义/改名…」写地图',
                                    parent=w)
                return
            v = simpledialog.askinteger(
                '默认值', '变量 %d（%s）读档后的初值：' % (i, cur['name']),
                initialvalue=int(cur['default'] or 0), minvalue=0, maxvalue=255, parent=w)
            if v is None:
                return
            try:
                self.doc.set_local_var_default(i, v)
            except Exception as e:
                messagebox.showerror('写入失败', str(e), parent=w)
                return
            _fill()
            self.status.set('变量 %d 默认值 = %d（已写进地图，读档即生效）' % (i, v))

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
            if not messagebox.askyesno('确认', '从地图里删掉"地编第 %d 条"的定义？' % (i + 1),
                                       parent=w):
                return
            try:
                self.doc.remove_local_var(i)
            except Exception as e:
                messagebox.showerror('删除失败', str(e), parent=w)
                return
            _fill()
            self.status.set('变量 %d 已从地图删除（存档镜像同步清空）' % i)

        bar = ttk.Frame(w)
        bar.pack(fill='x', padx=8, pady=3)
        ttk.Button(bar, text='✏ 定义/改名…（写地图）', command=_define).pack(side='left')
        ttk.Button(bar, text='默认值…', command=_setdef).pack(side='left', padx=4)
        ttk.Button(bar, text='存档值置 1', command=lambda: _setval(1)).pack(side='left')
        ttk.Button(bar, text='存档值置 0', command=lambda: _setval(0)).pack(side='left', padx=4)
        ttk.Button(bar, text='🗑 从地图删除', command=_del).pack(side='left', padx=8)
        ttk.Button(bar, text='关闭', command=w.destroy).pack(side='right')

        lbl2 = ttk.Label(w, text='', foreground='#374151')
        lbl2.pack(anchor='w', padx=8, pady=(0, 2))
        ttk.Label(w, text='※ 两处各管一段：**存档**那 100 槽是运行态（事件 36 判的就是它，'
                          '读档不会被地图覆盖）；**地图 [VariableNames]** 决定"地编里能选中哪些'
                          '变量 + 开局默认值"。所以本工具两边同时写。\n'
                          '※ 变量号怎么对：事件 36/37 参数 = 引擎索引（0 起，地编第 N 条 = N−1）；'
                          '行为 56/57 的变量号在行为对象 +164（工具已自动处理）。',
                  foreground='#6b7280', wraplength=735, justify='left').pack(anchor='w', padx=8)
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
                  foreground='#6B7280', wraplength=580, justify='left').pack(anchor='w', **pad)
        # ★§191：**这个窗口绝不改地图文件**（玩家明确要求：工具不能动他的 .MAP）
        ttk.Label(w, text='★ 本窗口**不会修改地图文件**：变量只写进存档镜像。'
                          '若要在 FA2 里也能选中/给新开局默认值，请到「🔢 局部变量…」页显式写入地图'
                          '（那里会自动重算地图的 [Digest]，不会让游戏提示「地图被修改」）',
                  foreground='#b91c1c', wraplength=580, justify='left').pack(anchor='w', **pad)

        ttk.Label(w, text='① 参与的建筑格（每行一个，或逗号/分号分隔，如 73,87）',
                  foreground='#111827').pack(anchor='w', **pad)
        tbox = tk.Text(w, height=6, width=58, wrap='word')
        tbox.pack(fill='x', padx=8)
        ttk.Label(w, text='③ 汇总满足后要执行的行为', foreground='#111827').pack(anchor='w', **pad)
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
                  foreground='#6b7280').pack(side='left')

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

        lbl = ttk.Label(w, text='', foreground='#374151', wraplength=590, justify='left')
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
                lbl.config(text='⚠ 还没填建筑格', foreground='#b91c1c')
                return None
            a_i = cb_a.current()
            if a_i < 0:
                lbl.config(text='⚠ 先选结果行为', foreground='#b91c1c')
                return None
            try:
                v0 = int(var_v0.get().strip() or '1', 0)
                aa = int(var_aa.get().strip() or '0', 0)
            except ValueError:
                lbl.config(text='⚠ 变量号/参数必须是整数', foreground='#b91c1c')
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
            lbl.config(text='\n'.join(lines), foreground='#374151')
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
                  foreground='#6b7280', wraplength=590, justify='left').pack(anchor='w', padx=8)

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
                  foreground='#1f2937').grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(frm, text='现状：行为 %d　参数 %d　建筑名 %r　路径点 %d → %s'
                           % (cur['type'], cur['arg'], cur['name'] or '（空）',
                              cur['waypoint'], cur['cell']),
                  foreground='#6b7280').grid(row=1, column=0, columnspan=3, sticky='w', pady=(2, 8))
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
                  foreground='#6b7280').grid(row=4, column=1, columnspan=2, sticky='w')
        ttk.Label(frm, text='行为类型：').grid(row=5, column=0, sticky='w', pady=(6, 0))
        rowt = ttk.Frame(frm)
        rowt.grid(row=5, column=1, columnspan=2, sticky='w', pady=(6, 0))
        ea = ttk.Entry(rowt, width=7)
        ea.insert(0, '125')
        ea.pack(side='left')
        ttk.Label(rowt, text='（125 = 将建筑建于…；改成别的类型就不需要建筑名/格子）',
                  foreground='#6b7280').pack(side='left', padx=6)
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
                  foreground='#b91c1c', justify='left').grid(row=6, column=0, columnspan=3,
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
                  foreground='#374151', wraplength=430, justify='left'
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
                  foreground='#374151', justify='left', wraplength=560).grid(
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
                  foreground='#374151', justify='left').grid(
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

    def _tab_events(self, nb):
        f = ttk.Frame(nb, padding=8)
        cols = ('idx', 'type', 'name', 'arg', 'argtext', 'owner', 'off')
        self.te = ttk.Treeview(f, columns=cols, show='headings', height=16)
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
        self.lbl_ev_count = ttk.Label(qrow, text='', foreground='#6b7280')
        self.lbl_ev_count.pack(side='left')
        self.te.pack(fill='both', expand=True)
        self.te.tag_configure('bad', foreground='#b91c1c')
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
        ttk.Label(row, text='双击一行也可修改', foreground='#6b7280').pack(side='left', padx=10)
        self.lbl_map_ev = ttk.Label(f, text='', foreground='#6b7280', wraplength=880,
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
                tag += ' ⚠非法类型(0)会崩档'
            self._ev_rows.append((i, (i, e['type'], e['name'], e['arg'],
                                      e['arg_text'], tag, '0x%X' % e['off']),
                                  ('bad',) if bad else ()))
        self._fill_ev_tree()
        if nbad:
            self.status.set('⚠ 有 %d 条事件类型是「-无事件-」(0) —— 读档会崩，请改成正常类型' % nbad)
            if not getattr(self, '_warned_zero', False):
                self._warned_zero = True      # 每次载入只提醒一次，别每次刷新都弹
                messagebox.showwarning(
                    '危险：存在「-无事件-」类型',
                    '本存档里有 %d 条事件的类型是 0「-无事件-」。\n\n'
                    '引擎没有这个类型的处理器 —— 带它读档会直接 internal error 崩溃。\n'
                    '请双击这些行，改成正常的事件类型（例如「13 流逝时间」）。' % nbad)
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
                                     foreground='#6b7280')
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
            rtc = '#b91c1c'
        else:
            ticks = None
            if _node_off is not None:
                ticks = int.from_bytes(self.doc.plain[_node_off + 80:_node_off + 84],
                                       'little')
            rt = tk.StringVar(value='' if ticks is None else
                              '运行期正常：该触发按 TagNode %d 帧计时（= %g 秒）。'
                              % (ticks, ticks / 15.0))
            rtc = '#6b7280'

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

        hint = ttk.Label(dlg, text='', foreground='#6b7280', wraplength=430, justify='left')
        hint.grid(row=3, column=0, columnspan=2, sticky='w', padx=12, pady=(4, 0))

        tk_lbl = ttk.Label(dlg, text='', foreground='#0b6b3a', wraplength=430, justify='left')
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
                              foreground='#b91c1c')
            else:
                tk_lbl.config(text='该触发实际按「%d 帧」计时（= %g 秒，游戏逻辑 %d 帧/秒）；'
                                   '保存时会一并改成 %d 帧。'
                                   % (t, t / 15.0, 15, v * 15), foreground='#0b6b3a')

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
                             '实测带它的档读档会崩。请改成正常的事件类型。', foreground='#b91c1c')

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
    def _tab_actions(self, nb):
        f = ttk.Frame(nb, padding=8)
        cols = ('idx', 'type', 'name', 'arg', 'argtext', 'owner', 'off')
        self.ta = ttk.Treeview(f, columns=cols, show='headings', height=16)
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
        self.lbl_ac_count = ttk.Label(qrow, text='', foreground='#6b7280')
        self.lbl_ac_count.pack(side='left')
        self.ta.pack(fill='both', expand=True)
        self.ta.tag_configure('bad', foreground='#b91c1c')
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
        ttk.Label(row, text='双击一行也可修改', foreground='#6b7280').pack(side='left', padx=10)
        self.lbl_map_ac = ttk.Label(f, text='', foreground='#6b7280', wraplength=880,
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
                                     foreground='#6b7280')
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

        hint = ttk.Label(dlg, text='', foreground='#6b7280', wraplength=430, justify='left')
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

    def _tab_attach(self, nb):
        f = ttk.Frame(nb, padding=8)
        ttk.Label(f, text='触发 ↔ 建筑挂接（2026-10-14 五轮实验定案，游戏内实测通过）。'
                          '需要先「自动匹配地图」才能显示建筑类型/坐标。',
                  foreground='#374151', wraplength=900, justify='left').pack(anchor='w')
        # 上：挂接概览（含**未挂接**的标签 —— 新建的触发挂接数为 0 时也能在这里选中挂建筑）
        ttk.Label(f, text='① 存档里的挂接标签（选中一行查看/操作其建筑；挂接数 0 的是可挂上去的新触发）',
                  foreground='#6b7280').pack(anchor='w', pady=(6, 2))
        cols = ('trig', 'tag', 'cnt', 'blds')
        tfrm = ttk.Frame(f)
        tfrm.pack(fill='both', expand=False)
        self.tma = ttk.Treeview(tfrm, columns=cols, show='headings', height=7)
        for c, t, w in (('trig', '触发', 240), ('tag', '标签ID', 90),
                        ('cnt', '挂接建筑数', 90), ('blds', '建筑摘要', 400)):
            self.tma.heading(c, text=t)
            self.tma.column(c, width=w, anchor='w')
        sbv = ttk.Scrollbar(tfrm, orient='vertical', command=self.tma.yview)
        self.tma.configure(yscrollcommand=sbv.set)
        self.tma.pack(side='left', fill='both', expand=True)
        sbv.pack(side='right', fill='y')
        self.tma.bind('<<TreeviewSelect>>', lambda e: self._attach_show_blds())
        # 中：选中标签的建筑明细
        ttk.Label(f, text='② 该标签挂接的建筑（选中一行可解除）',
                  foreground='#6b7280').pack(anchor='w', pady=(6, 2))
        cols = ('typ', 'pos', 'mid', 'off')
        self.tb = ttk.Treeview(f, columns=cols, show='headings', height=5)
        for c, t, w in (('typ', '建筑类型', 200), ('pos', '格坐标', 110),
                        ('mid', '地图建筑ID', 100), ('off', '字段偏移', 110)):
            self.tb.heading(c, text=t)
            self.tb.column(c, width=w, anchor='w')
        self.tb.pack(fill='both', expand=False)
        # 下：可挂建筑（地图中未挂接的）
        rowf = ttk.Frame(f)
        rowf.pack(fill='x', pady=(8, 2))
        ttk.Label(rowf, text='③ 可挂建筑（地图中未挂接的；类型过滤：').pack(side='left')
        self.ent_attach_filter = ttk.Entry(rowf, width=14)
        self.ent_attach_filter.pack(side='left', padx=4)
        self.ent_attach_filter.bind('<KeyRelease>', lambda e: self._attach_fill_free())
        ttk.Label(rowf, text='）').pack(side='left')
        cols = ('mid', 'typ', 'pos')
        self.tc = ttk.Treeview(f, columns=cols, show='headings', height=6)
        for c, t, w in (('mid', '地图建筑ID', 100), ('typ', '建筑类型', 240),
                        ('pos', '格坐标', 110)):
            self.tc.heading(c, text=t)
            self.tc.column(c, width=w, anchor='w')
        self.tc.pack(fill='both', expand=False)
        # 按钮
        row = ttk.Frame(f)
        row.pack(fill='x', pady=(8, 0))
        ttk.Button(row, text='扫描挂接', command=self._attach_scan).pack(side='left')
        ttk.Button(row, text='把①选中触发 挂到 ③选中建筑',
                   command=self.attach_do).pack(side='left', padx=6)
        ttk.Button(row, text='解除 ②选中建筑 的挂接',
                   command=self.detach_do).pack(side='left', padx=6)
        ttk.Label(f, text='原理：挂上 = 目标建筑的 tag 字段 ← 该 CellTag 的自键 + CellTag 计数+1'
                          '（两侧缺一不可，游戏内实测：双电厂 / 跨类型地堡 / 新写触发）。'
                          '解除 = 字段清 0 + 计数−1（只剩 1 栋时拒绝，请改用触发禁用）。\n'
                          '流程：用「触发」页的「➕ 新建触发…」（触发编辑器）造触发，'
                          '或在这里把任意一个挂接标签挂到更多建筑；① 里挂接数为 0 的行就是还没挂出去的新触发。\n'
                          '限制：建筑需能在存档中按坐标定位（存档比地图旧时定位不到：请先开新局存档）；'
                          '改完请「另存为新文件」或「原地保存」。',
                  foreground='#374151', wraplength=900, justify='left').pack(anchor='w', pady=(6, 0))
        return f

    # ---------------- 挂接 ----------------
    TYPE_CN = {
        'GAPOWR': '盟军电厂', 'GAPILE': '盟军兵营', 'GAPILL': '榴弹碉堡',
        'GAREFN': '盟军矿石精炼厂', 'GACNST': '盟军建造场', 'GAWEAP': '盟军战车工厂',
        'GADEPT': '盟军维修厂', 'GAAIRC': '盟军空指部', 'GAWEAT': '盟军作战实验室',
        'GAPILLBOX': '机枪碉堡', 'ATESLA': '光棱塔', 'TESLA': '磁暴线圈(苏)',
        'NAPSYB': '心灵信标(苏)', 'NAMISL': '核弹发射井(苏)', 'NAIRON': '铁幕装置(苏)',
        'NACLON': '复制中心(苏)', 'NACNST': '苏俄建造场', 'NAPOWR': '苏俄电厂',
        'YACOMD': '尤里建造场?', 'YAPSYT': '尤里雕像', 'GAROBO': '机器人控制中心',
        'CALOND04': '伦敦雕像', 'NAPSYA': '心灵主宰(尤)', 'YAPSYT2': '尤里雕像2',
    }

    def _type_disp(self, t):
        if not t:
            return '?'
        return self.TYPE_CN.get(t, t)

    def _attach_scan(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        self.status.set('正在扫描挂接（大档约需几秒）…')
        self.update_idletasks()
        try:
            ov = self.doc.attach_overview()
        except Exception as e:
            messagebox.showerror('出错', '挂接扫描失败: %s' % e)
            return
        self._attach_ov = ov
        self._attach_scanned = True
        self.refresh_attach()
        self.status.set('挂接扫描完成：%d 个挂接标签（未解出建筑的标签多为挂在格子/单位上）' % len(ov))

    def refresh_attach(self):
        if not hasattr(self, 'tma'):
            return
        for tv in (self.tma, self.tb, self.tc):
            tv.delete(*tv.get_children())
        if not self.doc:
            return
        if not getattr(self, '_attach_scanned', False):
            self.tma.insert('', 'end', values=('（未扫描）', '', '', '点「扫描挂接」开始'))
            self._attach_free = []
            return
        has_map = bool(getattr(self.doc, '_map', None))
        self._attach_ov = self.doc.attach_overview()
        for it in self._attach_ov:
            blds = it['buildings']
            if blds:
                summ = '；'.join('%s@(%d,%d)' % (self._type_disp(b['type']), b['cx'], b['cy'])
                                 for b in blds[:6])
                if len(blds) > 6:
                    summ += ' …共%d栋' % len(blds)
            elif has_map:
                summ = '（未挂接任何建筑 —— 可在③选建筑后点「挂到」）'
            else:
                summ = '（未挂接；未挂地图 ⇒ 建筑类型/坐标解不出）'
            tname = it['trigger_name'] or ('（未知触发）' if not it['trigger_id']
                                          else it['trigger_id'])
            self.tma.insert('', 'end', iid=str(it['node']),
                           values=('%s %s' % (it['trigger_id'] or '?', tname),
                                   it['tagid'] or '?', it['count'], summ))
        self._attach_fill_free()

    def _attach_cur(self):
        sel = self.tma.selection()
        if not sel or not getattr(self, '_attach_ov', None):
            return None
        node = int(sel[0])
        for it in self._attach_ov:
            if it['node'] == node:
                return it
        return None

    def _attach_show_blds(self):
        self.tb.delete(*self.tb.get_children())
        it = self._attach_cur()
        if not it:
            return
        for b in it['buildings']:
            self.tb.insert('', 'end', iid=str(b['field']),
                           values=(self._type_disp(b['type']),
                                   '(%d,%d)' % (b['cx'], b['cy']),
                                   b['map_idx'] or '—', '0x%X' % b['field']))

    def _attach_free_list(self):
        """地图里未挂接的建筑 [{idx,type,x,y,tagid}]（tagid 不是任何标签的）"""
        m = getattr(self.doc, '_map', None) if self.doc else None
        if not m:
            return []
        t2t = m.tag_trigger_map()
        out = []
        for s in m.structures():
            tg = s['tagid']
            if tg and tg in t2t:
                continue                      # 已挂接
            out.append(s)
        return out

    def _attach_fill_free(self):
        self.tc.delete(*self.tc.get_children())
        flt = (self.ent_attach_filter.get() or '').strip().upper()
        n = 0
        self._attach_free_shown = []
        for s in getattr(self, '_attach_free_list', lambda: [])():
            disp = self._type_disp(s['type'])
            if flt and flt not in s['type'].upper() and flt not in disp.upper():
                continue
            iid = '%s|%s' % (s['idx'], s['type'])
            self.tc.insert('', 'end', iid=iid,
                           values=(s['idx'], disp, '(%d,%d)' % (s['x'], s['y'])))
            self._attach_free_shown.append((iid, s))
            n += 1
            if n >= 800:
                break

    def attach_do(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        it = self._attach_cur()
        if not it:
            messagebox.showwarning('提示', '请先在①中选中一个已挂接的触发')
            return
        sel = self.tc.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在③中选中一栋未挂接的建筑')
            return
        info = dict(self._attach_free_shown).get(sel[0])
        if not info:
            messagebox.showwarning('提示', '建筑选择失效，请重新选择')
            return
        try:
            field, err = self.doc.locate_building_field(info['x'], info['y'], mode='zero')
            if field is None:
                messagebox.showerror('定位失败', err or '未知错误')
                return
            self.doc.attach_trigger_to_building(it['node'], field)
        except Exception as e:
            messagebox.showerror('出错', str(e))
            return
        self._attach_scanned = False
        self.refresh_attach()
        self.status.set('%s(%d,%d) 已挂上触发（尚未保存，请另存/原地保存）'
                        % (self._type_disp(info['type']), info['x'], info['y']))

    def detach_do(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        it = self._attach_cur()
        if not it:
            messagebox.showwarning('提示', '请先在①中选中一个标签')
            return
        sel = self.tb.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在②中选中一栋要解除的建筑')
            return
        field = int(sel[0])
        try:
            old = self.doc.detach_building(it['node'], field)
        except Exception as e:
            messagebox.showerror('出错', str(e))
            return
        self._attach_scanned = False
        self.refresh_attach()
        self.status.set('已解除建筑(字段 0x%X，旧值 0x%08X) 的挂接（尚未保存）'
                        % (field, old))

    # ---------------- 地图视图 / 选位（2026-10-15） ----------------
    def _tab_mapview(self, nb):
        f = ttk.Frame(nb, padding=8)
        ttk.Label(f, text='地图俯视图 + 落位判定　★安全闸：以不变量判定「存档 ↔ 地图」是否同一张图'
                          '——主判据是「地图 [Map] Size == 存档自带尺寸」（存档固定头部 0x348E 的 (W,H)），'
                          '与建筑是否被摧毁无关（后期档建筑大量被毁也照常匹配）；'
                          '建筑重合率只作参考提示，不参与放行。尺寸读不出时才退化为「建筑是否都在图上」。',
                  foreground='#374151', wraplength=980, justify='left').pack(anchor='w')
        self.mv_warn = ttk.Label(
            f, text='⚠ 只要地图尺寸与存档尺寸一致，本工具就认定是同一张图 —— 动手改之前请先备份存档'
                    '（点右边「备份存档」一键搞定；改完也建议先另存为新文件再进游戏验证）。',
            foreground='#b91c1c', wraplength=980, justify='left')
        self.mv_warn.pack(anchor='w', pady=(4, 0))
        row = ttk.Frame(f)
        row.pack(fill='x', pady=(6, 2))
        ttk.Button(row, text='选择地图…', command=self.mv_choose).pack(side='left')
        ttk.Button(row, text='自动匹配', command=self.mv_auto).pack(side='left', padx=6)
        ttk.Button(row, text='🗺 用地编打开', command=self.fa2_open).pack(side='left', padx=6)
        ttk.Button(row, text='备份存档', command=self.mv_backup).pack(side='left', padx=6)
        self.mv_lbl = ttk.Label(row, text='未加载地图', foreground='#6b7280')
        self.mv_lbl.pack(side='left', padx=6)

        row2 = ttk.Frame(f)
        row2.pack(fill='x', pady=(2, 2))
        ttk.Label(row2, text='缩放：').pack(side='left')
        ttk.Button(row2, text='－', width=3, command=lambda: self.mv_zoom(-1)).pack(side='left')
        ttk.Button(row2, text='＋', width=3, command=lambda: self.mv_zoom(1)).pack(side='left', padx=3)
        ttk.Button(row2, text='适应窗口', command=self.mv_fit).pack(side='left', padx=6)
        self.mv_grid = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text='网格', variable=self.mv_grid,
                        command=self.mv_render).pack(side='left', padx=6)
        ttk.Button(row2, text='重绘', command=self.mv_render).pack(side='left', padx=6)
        ttk.Label(row2, text='（点格子选位：红=已占 / 深红细线=高度差(悬崖/斜坡) / 暗色=地图外）',
                  foreground='#6b7280').pack(side='left', padx=8)

        rowp = ttk.Frame(f)
        rowp.pack(fill='x', pady=(2, 2))
        ttk.Label(rowp, text='放置建筑（直接落成）：').pack(side='left')
        self.mv_type = ttk.Combobox(rowp, width=22, state='readonly')
        self.mv_type.pack(side='left')
        ttk.Button(rowp, text='刷新类型', command=self._fill_type_lists).pack(side='left', padx=4)
        # ★2026-10-17 新增：**所属方**选择（写建筑记录载荷 +336/+544 的 House 键）
        ttk.Label(rowp, text='所属方：').pack(side='left', padx=(10, 0))
        self.mv_house = ttk.Combobox(rowp, width=16, state='readonly')
        self.mv_house.pack(side='left')
        ttk.Button(rowp, text='⬛ 放置建筑到选中格', command=self.mv_place).pack(side='left', padx=6)
        ttk.Button(rowp, text='❌ 删除选中格建筑',
                   command=self.mv_delete).pack(side='left', padx=6)
        ttk.Button(rowp, text='🏳 改选中格建筑所属方',
                   command=self.mv_set_owner).pack(side='left', padx=6)

        # ★2026-10-22 §169/§183：这一排是**修旧楼**用的（放置时已自动播一遍建造动画，
        #   不需要再手动"重播"——那个按钮 §183 按玩家要求删掉了，能力留在 core：
        #   `make_building_buildup()`）。
        rowa = ttk.Frame(f)
        rowa.pack(fill='x', pady=(0, 2))
        ttk.Button(rowa, text='⏩ 修成完成态（选中格）',
                   command=self.mv_fix_anim).pack(side='left')
        ttk.Button(rowa, text='🔍 扫描全档卡住的建筑',
                   command=self.mv_scan_anim).pack(side='left', padx=6)
        ttk.Button(rowa, text='✨ 补齐建筑动画(anim)',
                   command=self.mv_add_anim).pack(side='left', padx=6)
        ttk.Label(rowa, text='修的是旧楼：存档在 Buildup 播完前保存 ⇒ 重载后显示不全',
                  foreground='#6b7280').pack(side='left', padx=6)

        # ★2026-10-17 玩家定案：放置建筑**默认走触发法**（引擎自己造）
        #   好处：① 有建造动画、模型完整（不会"无头"）；② 不可建造的地形（悬崖边/水面）引擎会直接拒绝。
        # ★2026-10-21 17:50 另一条路也已**游戏内完整验收**：不勾 = 直接落成
        #   （立即出现、可指定所属方、光棱塔实测正常开火）—— 只是不自动避开悬崖/水面，
        #   所以放进 require_free 的占用检查来兜底。
        rowq = ttk.Frame(f)
        rowq.pack(fill='x', pady=(0, 2))
        self.mv_via = tk.BooleanVar(value=True)
        ttk.Checkbutton(rowq, text='⚡ 触发法（引擎自己造）',
                        variable=self.mv_via).pack(side='left')
        ttk.Label(rowq, text='事件：').pack(side='left', padx=(10, 0))
        self.mv_ev = ttk.Combobox(rowq, width=15, state='readonly',
                                  values=('流逝时间 1 秒', '任何事件(立即)', '流逝时间 5 秒'))
        self.mv_ev.current(0)                       # ★默认 1 秒：实测唯一稳的那条
        self.mv_ev.pack(side='left')
        ttk.Label(rowq, text='有建造动画/模型完整；悬崖水面等不可建地形会被引擎拒绝',
                  foreground='#6b7280').pack(side='left', padx=4)
        ttk.Label(f, text='　不勾 = 直接落成：立即写进档案、可指定所属方、已验收可正常开火'
                          '（不会自动避开悬崖/水面，靠占用检查兜底）',
                  foreground='#6b7280').pack(anchor='w')
        # ★2026-10-22 §188 定案（玩家 T16 实测）：直接落成 = **播一遍建造动画，播完模型完整**。
        #   开关是配方里的 `+0x6E4=0`；继承了 1 就会"不播 + 显示不全"（§187/T16-B 栋）。
        ttk.Label(f, text='　直接落成 = 引擎播一遍建造动画（读档后 1~2 秒长出来，播完模型完整）。'
                          '只能加本档已有的类型，且模板必须健康（§184）',
                  foreground='#1d4ed8', wraplength=980, justify='left').pack(anchor='w', pady=(0, 4))

        # ★2026-10-17 新增：**放单位**（同类型克隆；U2 游戏内验收通过）
        rowu = ttk.Frame(f)
        rowu.pack(fill='x', pady=(0, 2))
        ttk.Label(rowu, text='放置单位：').pack(side='left')
        self.mv_unit = ttk.Combobox(rowu, width=30, state='readonly')
        self.mv_unit.pack(side='left')
        self.mv_unit.bind('<<ComboboxSelected>>', lambda e: self._unit_sel_info())
        # ★2026-10-17 新增：单位**所属方**（写记录 +352/+560 的 House 键）——
        #   默认「沿用模板」，选别的阵营就是**给对面刷兵**。
        ttk.Label(rowu, text='所属方：').pack(side='left', padx=(10, 0))
        self.mv_u_house = ttk.Combobox(rowu, width=18, state='readonly',
                                       values=(UNIT_HOUSE_INHERIT,))
        self.mv_u_house.current(0)
        self.mv_u_house.pack(side='left')
        # ★2026-10-18 新增：单位**等级**（记录 +0x150 int / +0x164 float，成对改写）
        #   ★游戏内验收（21:43）：显示等级**只由 +0x150 决定**。默认选「新兵」——
        #     档里的模板常是 Neutral/平民的**精英**兵（三级），沿用模板会莫名其妙出三颗星。
        ttk.Label(rowu, text='等级：').pack(side='left', padx=(10, 0))
        self.mv_u_vet = ttk.Combobox(rowu, width=12, state='readonly',
                                     values=UNIT_VET_CHOICES)
        self.mv_u_vet.current(1)                    # 默认「新兵」
        self.mv_u_vet.pack(side='left')
        ttk.Button(rowu, text='🪖 放单位到选中格',
                   command=self.mv_putunit).pack(side='left', padx=6)
        self.mv_urule = ttk.Label(rowu, text='', foreground='#6b7280')
        self.mv_urule.pack(side='left', padx=6)
        ttk.Button(rowu, text='规则目录…',
                   command=self.mv_pick_rules).pack(side='left')

        cw = ttk.Frame(f)
        cw.pack(fill='both', expand=True, pady=(6, 4))
        self.mv_canvas = tk.Canvas(cw, bg='#12141a', highlightthickness=0)
        vs = ttk.Scrollbar(cw, orient='vertical', command=self.mv_canvas.yview)
        hs = ttk.Scrollbar(cw, orient='horizontal', command=self.mv_canvas.xview)
        self.mv_canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.mv_canvas.grid(row=0, column=0, sticky='nsew')
        vs.grid(row=0, column=1, sticky='ns')
        hs.grid(row=1, column=0, sticky='ew')
        cw.rowconfigure(0, weight=1)
        cw.columnconfigure(0, weight=1)
        self.mv_canvas.bind('<Button-1>', self.mv_click)

        self.mv_info = ttk.Label(f, text='', foreground='#111827', justify='left',
                                 wraplength=980)
        self.mv_info.pack(anchor='w')
        self.mv_state = {'mp': None, 'ok': False, 'scale': 8, 'img': None,
                         'blds': [], 'occ': {}, 'sel': None, 'path': None,
                         'gdata': None}
        return f

    def _mv_reset(self):
        st = getattr(self, 'mv_state', None)
        if st is None:
            return
        p = getattr(getattr(self.doc, '_map', None), 'path', st.get('path')) if self.doc else None
        st.update({'mp': None, 'ok': False, 'img': None, 'blds': [],
                   'occ': {}, 'sel': None, 'path': p, 'gdata': None, 'dims': None})
        self.mv_canvas.delete('all')
        self.mv_info.config(text='')
        if self.doc and p and os.path.exists(p):
            self.mv_load(p)
        else:
            self.mv_lbl.config(text='未加载地图（网格模式：可直接点选格子；放置建筑不需要地图）'
                                   + self._mv_ready_text(),
                               foreground='#6b7280')
            if self.doc:
                self.mv_fit()          # 无地图 ⇒ 画坐标网格

    def _mv_ready_text(self):
        """一句话说明**这份档**能怎么放建筑（玩家一眼就知道哪种方式可用）。"""
        if not self.doc:
            return ''
        try:
            trig_ok = self.doc.can_place_by_trigger()
        except Exception:
            trig_ok = False
        try:
            direct_ok = self.doc.can_add_placed()
        except Exception:
            direct_ok = False
        if trig_ok and direct_ok:
            return '　｜　触发法 ✅　直接落成 ✅'
        if direct_ok:
            return '　｜　触发法 ❌（本档没有触发对象）　直接落成 ✅'
        if trig_ok:
            return '　｜　触发法 ✅　直接落成 ❌'
        return '　｜　⚠ 本档没有建筑组也没有触发对象（全新开局）⇒ 两条路都不可用'

    def mv_choose(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        p = filedialog.askopenfilename(
            title='选择该存档对应的地图（必须与存档匹配）',
            initialdir=os.path.dirname(self.doc.path),
            filetypes=[('红警地图', '*.map *.yrm'), ('所有文件', '*.*')])
        if p:
            self.mv_load(p)

    def mv_auto(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        p = getattr(getattr(self.doc, '_map', None), 'path', None)
        if not p:
            self.doc.auto_attach_map([self.game_dir.get()])
            p = getattr(getattr(self.doc, '_map', None), 'path', None)
        if not p:
            messagebox.showinfo('未找到地图',
                                '在存档目录和游戏目录里没找到地图。\n请点「选择地图…」手动指定。')
            return
        self.refresh_events()
        self.refresh_actions()
        self.mv_load(p)

    def mv_load(self, path):
        """加载地图 —— ★2026-10-17 20:3x 玩家定案：**不强制匹配，也绝不拦人**。

        「地图不是必须的，不能因为匹配不上连打开都不行」⇒ 这里只做**提示**：
          · 匹配（尺寸一致 / 建筑高度重合 / 建筑全在图内）⇒ 绿字；
          · 疑似不匹配 ⇒ **橙字**说明原因，但地图照样画、选位照样点、放建筑照样放；
          · 地图本身解析失败 ⇒ 退回网格模式，红字说明（仍然不影响放建筑）。
        旧行为（`messagebox.showwarning` 弹框 + 不加载）已移除。
        """
        st = self.mv_state
        st['path'] = path
        self.status.set('正在校验「地图 ↔ 存档」…')
        self.update_idletasks()
        try:
            mt = self.doc.map_match(path)
        except Exception as e:
            mt = {'ok': False, 'msg': '匹配校验没跑通（不影响使用）：%s' % e}
        try:
            import map_render as mr
            mp = mr.parse_map(path)
        except Exception as e:
            st['ok'] = False
            st['mp'] = None
            self.mv_fit()              # 落回网格模式
            self.mv_lbl.config(text='地图读取失败，已退回网格模式（放建筑不受影响）：%s' % e,
                               foreground='#b91c1c')
            self.status.set('地图读取失败：%s' % e)
            return
        st['mp'] = mp
        self.status.set('正在读取存档建筑…')
        self.update_idletasks()
        st['blds'] = self.doc.buildings()
        # 画布范围：优先用**存档自带尺寸**（菱形系 2W×2H），再与内容取大
        mp_sz = st['mp']['size']
        cl = self.doc.coord_limit()
        st['mp']['size'] = mr.canvas_size(st['mp'], st['blds'])
        st['mp']['size'] = (max(st['mp']['size'][0], min(cl[0], 2 * mp_sz[0])),
                            max(st['mp']['size'][1], min(cl[1], 2 * mp_sz[1])))
        st['ok'] = True
        ok = bool(mt.get('ok'))
        self.mv_lbl.config(text='%s ｜ %s%s' % (os.path.basename(path), mt.get('msg', ''),
                                               self._mv_ready_text()),
                           foreground='#0b6b3a' if ok else '#b45309')
        self.mv_fit()
        self.mv_info.config(text=('提示：点地图上的格子选位，然后在上方「放置建筑」选类型点放置；'
                                 '下方会给出落位判定。（写入已开放：直接放好 = U2 配方，游戏内验证通过）'
                                 + ('' if ok else '　⚠ 地图与存档的匹配只是提示，不影响任何操作。')))
        # ★挂上地图后规则来源变了（大型任务的自制单位/建筑规则写在地图 INI 里）⇒ 刷新下拉与说明
        try:
            self._refresh_unit_rule_label()
            if hasattr(self, 'mv_unit'):
                self.mv_unit['values'] = self._unit_type_names()
        except Exception:
            pass

    def _mv_dims(self):
        """网格模式的坐标范围 = **菱形坐标空间**（0..2W-1 / 0..2H-1）∪ 实际数据。

        ★2026-10-17：之前用 `map_size()`（80×80）当范围，但放置/建筑/单位用的都是**菱形坐标**
        （`coord_limit()` = 2W×2H = 160×160），所以 y>80 的格子在网格里根本点不到
        ⇒ 现在直接用 `coord_limit()`，再与实际数据取并集（防个别越界坐标漏画）。
        """
        st = self.mv_state
        if st.get('dims'):
            return st['dims']
        try:
            lim = self.doc.coord_limit()
            W, H = int(lim[0]), int(lim[1])
        except Exception:
            W, H = 160, 160
        try:
            bld, unit, wp = self._mv_grid_data()
            for (x, y) in list(bld) + list(unit):
                W, H = max(W, x + 1), max(H, y + 1)
            for (x, y) in wp.values():
                W, H = max(W, x + 1), max(H, y + 1)
        except Exception:
            pass
        st['dims'] = (max(16, min(W, 512)), max(16, min(H, 512)))
        return st['dims']

    def _mv_grid_data(self):
        """无地图模式要画的东西（建筑/单位/路径点）—— 算一次缓存，别每次点都重扫。"""
        st = self.mv_state
        if st.get('gdata') is not None:
            return st['gdata']
        bld, unit, wp = {}, {}, {}
        try:
            for b in self.doc.buildings():
                x, y = b.get('x'), b.get('y')
                if x is not None and y is not None and x >= 0 and y >= 0:
                    bld[(x, y)] = b.get('type')
        except Exception:
            pass
        try:
            for u in self.doc.house_placements():
                unit[(u['x'], u['y'])] = '%s/%s' % (u['type'], u['house'])
        except Exception:
            pass
        try:
            wp = dict(self.doc.waypoints_save())
        except Exception:
            pass
        st['gdata'] = (bld, unit, wp)
        return st['gdata']

    def _mv_grid_safe(self):
        """网格模式的入口：任何异常都在画布上写清楚，绝不留黑屏。"""
        try:
            self._mv_render_grid()
        except Exception:
            import traceback
            try:
                self.mv_canvas.delete('all')
                self.mv_canvas.create_text(
                    16, 16, anchor='nw', fill='#b91c1c', width=880, justify='left',
                    text='网格绘制出错（请把这段发给开发者）：\n\n' + traceback.format_exc()[-900:])
            except Exception:
                pass

    def _mv_render_grid(self):
        """**无地图模式**：按坐标范围画格子 —— 依然可以点选格子（触发法放置不需要地图）。"""
        st = self.mv_state
        c = self.mv_canvas
        W, H = self._mv_dims()
        s = max(2, int(st.get('scale') or 8))
        st['scale'] = s
        bld, unit, wp = self._mv_grid_data()
        c.delete('all')
        c.configure(scrollregion=(0, 0, W * s, H * s))
        c.create_rectangle(0, 0, W * s, H * s, fill='#f7f7f3', outline='')
        for x in range(0, W + 1):
            c.create_line(x * s, 0, x * s, H * s, fill='#dcdcd4' if x % 10 else '#b9b9ae')
        for y in range(0, H + 1):
            c.create_line(0, y * s, W * s, y * s, fill='#dcdcd4' if y % 10 else '#b9b9ae')
        for x in range(0, W, 10):
            c.create_text(x * s + 2, 2, text=str(x), anchor='nw',
                          fill='#8a8a80', font=('Consolas', 7))
        for y in range(0, H, 10):
            c.create_text(2, y * s + 2, text=str(y), anchor='nw',
                          fill='#8a8a80', font=('Consolas', 7))
        for (x, y), t in bld.items():
            if x < W and y < H:
                c.create_rectangle(x * s, y * s, (x + 1) * s, (y + 1) * s,
                                   fill='#e8927c', outline='', stipple='gray50')
        for (x, y), t in unit.items():
            if x < W and y < H:
                c.create_oval(x * s + 1, y * s + 1, (x + 1) * s - 1, (y + 1) * s - 1,
                              fill='#7aa7e0', outline='')
        for n, (x, y) in wp.items():
            if x < W and y < H:
                c.create_text(x * s + s / 2, y * s + s / 2, text=str(n),
                              fill='#2f7d32', font=('Consolas', 7))
        if st.get('sel'):
            x, y = st['sel']
            c.create_rectangle(x * s, y * s, (x + 1) * s, (y + 1) * s,
                               outline='#d9480f', width=2)

    def mv_render(self):
        import base64
        import map_render as mr
        st = getattr(self, 'mv_state', None)
        if not st:
            return
        if not st['ok'] or not st['mp']:
            if self.doc:
                self._mv_grid_safe()
            return
        W, H, buf, meta = mr.render_rgb(st['mp'], st['blds'], st['scale'],
                                        sel=st['sel'], grid=bool(self.mv_grid.get()))
        png = mr.to_png(W * st['scale'], H * st['scale'], buf)
        try:
            st['img'] = tk.PhotoImage(data=base64.b64encode(png))
        except tk.TclError:                 # 退路：落临时文件再读
            import tempfile
            fp = os.path.join(tempfile.gettempdir(), '_ra2_mapview.png')
            open(fp, 'wb').write(png)
            st['img'] = tk.PhotoImage(file=fp)
        st['occ'] = meta['occupancy']
        c = self.mv_canvas
        c.delete('all')
        c.create_image(0, 0, anchor='nw', image=st['img'])
        c.configure(scrollregion=(0, 0, W * st['scale'], H * st['scale']))

    def mv_zoom(self, d):
        st = getattr(self, 'mv_state', None)
        if not st or not st['ok']:
            return
        st['scale'] = max(1, min(24, st['scale'] + d))
        self.mv_render()

    def mv_fit(self):
        st = getattr(self, 'mv_state', None)
        if not st:
            return
        if not st['ok'] or not st['mp']:
            if not self.doc:
                return
            self.update_idletasks()
            cw = max(self.mv_canvas.winfo_width(), 400)
            ch = max(self.mv_canvas.winfo_height(), 300)
            W, H = self._mv_dims()
            st['scale'] = max(4, min(24, int(min(cw / float(W), ch / float(H))) or 4))
            self._mv_grid_safe()
            return
        self.update_idletasks()
        cw = max(self.mv_canvas.winfo_width(), 400)
        ch = max(self.mv_canvas.winfo_height(), 300)
        W, H = st['mp']['size']
        st['scale'] = max(1, min(16, int(min(cw / float(W), ch / float(H))) or 1))
        self.mv_render()

    def mv_click(self, event):
        import map_render as mr
        st = getattr(self, 'mv_state', None)
        if not st:
            return
        if not st['ok'] or not st['mp']:
            # ★无地图模式：照样按网格选位（触发法放置不需要地图）
            if not self.doc:
                return
            s = max(2, int(st.get('scale') or 8))
            x = int(self.mv_canvas.canvasx(event.x) // s)
            y = int(self.mv_canvas.canvasy(event.y) // s)
            W, H = self._mv_dims()
            if not (0 <= x < W and 0 <= y < H):
                return
            st['sel'] = (x, y)
            self._mv_grid_safe()
            bld, unit, wp = self._mv_grid_data()
            head = '选中格 (%d,%d)' % (x, y)
            head += '　建筑：%s' % bld[(x, y)] if (x, y) in bld else '　建筑：无'
            if (x, y) in unit:
                head += '　单位：%s' % unit[(x, y)]
            wps = [n for n, c in wp.items() if c == (x, y)]
            if wps:
                head += '　路径点：%s' % ','.join(str(n) for n in wps)
            head += '\n（未挂地图：按存档坐标 %d×%d 画网格；放置建筑可直接用触发法，不依赖地图）' % (W, H)
            self.mv_info.config(text=head)
            return
        s = st['scale']
        x = int(self.mv_canvas.canvasx(event.x) // s)
        y = int(self.mv_canvas.canvasy(event.y) // s)
        st['sel'] = (x, y)
        self.mv_render()
        msgs = mr.check_cell(st['mp'], st['blds'], x, y)
        b = st['occ'].get((x, y))
        head = '选中格 (%d,%d)' % (x, y)
        if b:
            head += '　该格建筑：%s（耐久 %d，所属方指针 0x%08X，挂接键 0x%08X，对象 @0x%X）' % (
                b['type'], b['hp'], b['house'], b['tag'], b['base'])
        else:
            head += '　空（无建筑）'
        self.mv_info.config(text=head + '\n' + '\n'.join(msgs))

    def mv_backup(self):
        """一键备份当前存档（只复制、不删改；用户钦定：尺寸一致即认定同图 ⇒ 动手前先备份）"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        import shutil
        ts = time.strftime('%Y%m%d-%H%M%S')
        base = self.doc.path
        dst = '%s.bak-%s' % (base, ts)
        n = 0
        while os.path.exists(dst):
            n += 1
            dst = '%s.bak-%s(%d)' % (base, ts, n)
        try:
            shutil.copy2(base, dst)
        except Exception as e:
            messagebox.showerror('备份失败', str(e))
            return
        dirty = self.doc.dirty() if hasattr(self.doc, 'dirty') else False
        msg = '已备份到：\n%s\n\n大小 %.1f KB' % (dst, os.path.getsize(dst) / 1024.0)
        if dirty:
            msg += ('\n\n注意：备份的是**磁盘上的原文件**；当前编辑器里还有未保存的改动，'
                    '若要连改动一起留档，请再「另存为新文件」。')
        self.status.set('已备份存档 → %s' % os.path.basename(dst))
        messagebox.showinfo('备份完成', msg)

    # ---------------- 赠送建筑（2026-10-15 配方，游戏内验收通过） ----------------
    # ------------------------------------------------------------------ #
    # 用地图编辑器（FinalAlert2 / HDM Edition）打开当前地图
    #   —— 本工具的地图页是"示意图"（按 minimap.ini 给格子上色）；
    #      想看真实地形画面，直接把挂好的 .map 交给地编显示最直观。
    # ------------------------------------------------------------------ #
    _FA2_DIRS = [
        r'C:\Users\zhao5\Desktop\WorkBuddy\红警MOD制作素材工具与教程\HJDB',
        r'C:\Users\zhao5\Desktop\红色警戒\FinalAlert2SP 1.6.1.0 LBW 20230525',
        r'C:\Users\zhao5\Desktop\红色警戒\3232\FinalAlert2SP 1.6.1.0 LBW 20230525',
        r'C:\Users\zhao5\Desktop\红色警戒\3232\FinalAlert2SP 1.4.0.1',
        r'E:\Mental Omega\Map Editor',
    ]
    _FA2_EXES = ('FA2SPLaunch.exe', 'FinalAlert2SP.exe', 'FinalAlert2MO.exe')

    @staticmethod
    def _app_dir():
        if getattr(sys, 'frozen', False):
            return os.path.dirname(os.path.abspath(sys.executable))
        return _HERE

    def _cfg_get(self, key, default=None):
        try:
            with open(os.path.join(self._app_dir(), 'editor_config.json'),
                      encoding='utf-8') as f:
                return json.load(f).get(key, default)
        except Exception:
            return default

    def _cfg_set(self, key, val):
        p = os.path.join(self._app_dir(), 'editor_config.json')
        try:
            d = {}
            if os.path.exists(p):
                with open(p, encoding='utf-8') as f:
                    d = json.load(f)
            d[key] = val
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _fa2_launcher(self, ask=True):
        r"""返回 (kind, 工作目录)。优先级：**FA2SPLaunch.exe**（新版 Unicode 启动器）
        > Syringe+FinalAlert2YR.dat（老 bat 那套）> 其它 FA2 exe。

        ⚠ 实测（2026-10-16 18:17）：**直接用 Syringe.exe 会被拒**——Syringe 的 syringe.log 记着
        `WinMain: arguments = "…Syringe.exe FinalAlert2YR.dat E:\YURI\all01umd.map"` 之后
        报 `No or invalid command line arguments given`（它判定目标无效）。而且 HJDB 里
        `RunFA2sp.bat` 是 2024 年的、`FA2SPLaunch.exe` 是 2026 年的 ⇒ 新入口才是它。
        """
        seen, cands = set(), []
        d0 = self._cfg_get('fa2_dir')
        for c in ([d0] if d0 else []) + self._FA2_DIRS:
            if c and c.lower() not in seen:
                seen.add(c.lower())
                cands.append(c)
        for c in cands:
            if not os.path.isdir(c):
                continue
            for nm in ('FA2SPLaunch.exe',):
                if os.path.exists(os.path.join(c, nm)):
                    return os.path.join(c, nm), c
            if (os.path.exists(os.path.join(c, 'Syringe.exe'))
                    and os.path.exists(os.path.join(c, 'FinalAlert2YR.dat'))):
                return 'syringe', c
            for nm in self._FA2_EXES:
                if os.path.exists(os.path.join(c, nm)):
                    return os.path.join(c, nm), c
        if not ask:
            return None, None
        c = filedialog.askdirectory(
            title='没找到地编，请选择 FinalAlert2（地编）所在目录')
        if not c:
            return None, None
        self._cfg_set('fa2_dir', c)
        return self._fa2_launcher(ask=False)

    @staticmethod
    def _fa2_touch_recent(d, mp):
        """把地图写进地编的「最近文件」列表（FinalAlert.ini 是 **GBK** 编码）。

        兜底用：万一地编不吃命令行参数，打开后也能在最近文件里一眼点到这张图。
        会先备份成 `FinalAlert.ini.bak-编辑器`。返回 True/False。
        """
        ini = os.path.join(d, 'FinalAlert.ini')
        if not os.path.exists(ini):
            return False
        try:
            raw = open(ini, 'rb').read()
            txt = raw.decode('gbk', 'replace')
            m = re.search(r'(?m)^\[Files\][ \t]*\r?\n((?:[ \t]*\d+=.*\r?\n?)*)', txt)
            if not m:
                return False
            items = []
            for line in m.group(1).splitlines():
                line = line.strip()
                if '=' in line:
                    items.append(line.split('=', 1)[1])
            items = [p for p in items
                     if os.path.normcase(p) != os.path.normcase(mp)]
            items.insert(0, mp)
            items = items[:10]
            new = '[Files]\n' + ''.join('%d=%s\n' % (i, p)
                                        for i, p in enumerate(items))
            bak = ini + '.bak-编辑器'
            if not os.path.exists(bak):
                open(bak, 'wb').write(raw)
            open(ini, 'wb').write((txt[:m.start()] + new + txt[m.end():]).encode('gbk', 'replace'))
            return True
        except Exception:
            return False

    def fa2_open(self):
        """把当前存档挂的地图交给地编打开（真实地形画面，比示意图直观）。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        # 优先用地图页**当前已加载**的那张图；否则退回存档**已挂载**的地图
        mp = None
        try:
            mp = (getattr(self, 'mv_state', None) or {}).get('path')
        except Exception:
            mp = None
        if not mp:
            try:
                mp = self.doc.map_path          # ★这是 property，不是方法
            except Exception:
                mp = None
        if not mp or not os.path.exists(mp):
            messagebox.showwarning(
                '提示', '还没有可用的地图文件。\n\n'
                        '请先在地图页点「自动匹配」或「选择地图…」把地图载入，再点「用地编打开」。')
            return
        kind, wd = self._fa2_launcher()
        if not kind:
            return
        # 兜底：先把它塞进地编的「最近文件」列表（万一不吃命令行参数）
        recent_ok = self._fa2_touch_recent(wd, mp)
        try:
            if kind == 'syringe':
                cmd = [os.path.join(wd, 'Syringe.exe'), 'FinalAlert2YR.dat', mp]
            else:
                cmd = [kind, mp]
            subprocess.Popen(cmd, cwd=wd)
            extra = '；已加入地编「最近文件」' if recent_ok else ''
            self.status.set('已交给地编打开：%s（%s）%s' % (os.path.basename(mp), wd, extra))
        except Exception as e:
            messagebox.showerror('失败', '启动地图编辑器失败：%s' % e)

    def _tab_gift(self, nb):
        f = ttk.Frame(nb, padding=8)
        ttk.Label(f, text='给玩家赠送一座建筑（2026-10-16 游戏内验收通过：**新开载入 ✅ / 游戏内重载 ✅**）。\n'
                          '· 直接放好：立即出现在指定格子（记录 + 三张登记表全登 + 格子占用 + 实例 ID 唯一化）。\n'
                          '· 就绪未落地：侧栏直接出现"就绪"图标，进游戏自己找位置放。\n'
                          '　仅支持**可生产建筑（TechLevel≥1）**——建造场等不可生产类型会拒送，请用直接放好。\n'
                          '· 档里没有的类型也能放（跨类型克隆：自动改类型键/美术尺寸/耐久，数据来自存档语料库）。\n'
                          '★ 直接放好 = **进游戏播一遍建造动画，播完模型完整**（§188 定案，T16 实测）。\n'
                          '★ 改完会提示另存为新文件，进游戏前请先备份。',
                  foreground='#374151', wraplength=980, justify='left').pack(anchor='w')
        row = ttk.Frame(f)
        row.pack(fill='x', pady=(8, 2))
        ttk.Label(row, text='建筑类型：').pack(side='left')
        self.gift_type = ttk.Combobox(row, width=24, state='readonly')
        self.gift_type.pack(side='left')
        ttk.Button(row, text='刷新类型列表', command=self._gift_fill_types).pack(side='left', padx=6)
        self.gift_mode = tk.StringVar(value='ready')
        ttk.Radiobutton(row, text='就绪未落地（推荐）', variable=self.gift_mode,
                        value='ready').pack(side='left', padx=(12, 4))
        ttk.Radiobutton(row, text='直接放好 → 格 X:', variable=self.gift_mode,
                        value='placed').pack(side='left', padx=(12, 2))
        self.gift_x = ttk.Spinbox(row, from_=0, to=999, width=5)
        self.gift_x.pack(side='left')
        ttk.Label(row, text=' Y:').pack(side='left')
        self.gift_y = ttk.Spinbox(row, from_=0, to=999, width=5)
        self.gift_y.pack(side='left')
        # ★2026-10-17：所属方（仅「直接放好」分支生效；就绪未落地固定为玩家阵营）
        ttk.Label(row, text='　所属方：').pack(side='left')
        self.gift_house = ttk.Combobox(row, width=14, state='readonly')
        self.gift_house.pack(side='left')
        row2 = ttk.Frame(f)
        row2.pack(fill='x', pady=(2, 2))
        # ★2026-10-22 §188 定案：带建造动画（配方含 6E4=0）⇒ 进游戏播一遍、播完模型完整。
        self.gift_buildup = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text='带建造动画（默认：进游戏播一遍，播完模型/炮塔完整）',
                        variable=self.gift_buildup).pack(side='left', padx=(0, 12))
        ttk.Label(row2, text='高级：工厂反引槽2 偏移（hex，仅同会话档需要；'
                             '跨档留空 = 只写槽1，待游戏验证）：').pack(side='left')
        self.gift_slot2 = ttk.Entry(row2, width=14)
        self.gift_slot2.pack(side='left', padx=4)
        row3 = ttk.Frame(f)
        row3.pack(fill='x', pady=(6, 2))
        ttk.Button(row3, text='🎁 赠送并另存为…', command=self._gift_do).pack(side='left')
        self.gift_out = tk.Text(f, height=9, bg='#0b0e14', fg='#d8e0ea',
                                insertbackground='#d8e0ea')
        self.gift_out.pack(fill='both', expand=True, pady=(6, 0))
        return f

    def _gift_log(self, s):
        self.gift_out.insert('end', s + '\n')
        self.gift_out.see('end')

    def _adj_buildings(self, cell, radius=2):
        """cell 周围 radius 格内的已建建筑数；0 ⇒ 很可能是水/坡（行为125 会被引擎静默拒绝）。"""
        if not cell:
            return -1
        try:
            bset = {(b['x'], b['y']) for b in self.doc.buildings()}
        except Exception:
            return -1
        cx, cy = int(cell[0]), int(cell[1])
        return sum(1 for dx in range(-radius, radius + 1) for dy in range(-radius, radius + 1)
                   if (dx or dy) and (cx + dx, cy + dy) in bset)

    def _bld_type_names(self):
        """可赠送/放置的建筑类型全集：rules 建筑名单 ∩ 档内类型对象，并入档内实例。"""
        all_types = self.doc.building_types()
        inst = {b['type'] for b in self.doc.buildings()}
        bset, _tl = SaveDoc._rules_info()
        if bset:
            names = {n for n in all_types if n.upper() in bset}
        else:                       # 无 rules 时退回：语料库美术表 + 档内实例
            art = SaveDoc._type_sig_table()
            names = {n for n in art if n in all_types} | inst
        names |= inst
        return sorted(names)

    def _fill_type_lists(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        try:
            names = self._bld_type_names()
        except Exception as e:
            messagebox.showerror('失败', str(e))
            return
        vals = ['%s（%s）' % (n, self._type_disp(n)) for n in names]
        self.gift_type['values'] = vals
        self.mv_type['values'] = vals
        try:
            self.mv_unit['values'] = self._unit_type_names()
        except Exception:
            pass
        self._fill_house_lists()
        self._refresh_unit_rule_label()
        self._gift_log('类型列表刷新：%d 种（rules 建筑 ∩ 档内类型对象）；'
                       '就绪模式仅支持 TechLevel≥1 的可生产建筑' % len(names))

    def _picked_house(self, attr):
        """读「所属方」下拉 → 阵营短名（认不出/未选 ⇒ None = 保持原值）。"""
        cb = getattr(self, attr, None)
        s = (cb.get() if cb else '').strip()
        if not s or s.startswith('（'):
            return None
        return s.split('（')[0].strip()

    def _fill_house_lists(self, log=True):
        """填充「所属方」下拉（地图页 + 赠送页）。

        ★2026-10-17 新增：值 = `阵营短名（中文名）`，默认选**当前玩家**。
          写的是建筑记录载荷 +336/+544 的 House 对象键（`add_placed_building(house=…)`）。
          就绪未落地（侧栏槽是玩家的）暂不支持改阵营。
        """
        if not self.doc:
            return
        try:
            hs = self.doc.houses()
        except Exception as e:
            if log:
                self._gift_log('⚠ 所属方列表定位失败：%s' % e)
            return
        vals, me_disp = [], None
        for h in hs:
            if not h.get('key'):
                continue
            disp = '%s（%s）' % (h['short'], h.get('cn') or '')
            vals.append(disp)
            if h.get('is_player'):
                me_disp = disp
        for cb in (getattr(self, 'mv_house', None), getattr(self, 'gift_house', None)):
            if cb is None:
                continue
            cb['values'] = vals
            if vals:
                keep = cb.get()
                cb.set(keep if keep in vals else (me_disp or vals[0]))
        # ★2026-10-17：单位用的所属方下拉**多一个「沿用模板」**并默认选它
        #   （默认就改主人会让"任务图里唯一模板是敌方单位"的档被误翻成玩家方）
        ucb = getattr(self, 'mv_u_house', None)
        if ucb is not None:
            ucb['values'] = [UNIT_HOUSE_INHERIT] + vals
            keep = ucb.get()
            ucb.set(keep if keep in ucb['values'] else UNIT_HOUSE_INHERIT)
        if log:
            self._gift_log('所属方列表：%d 个（建筑默认 = 当前玩家；单位默认 = 沿用模板）' % len(vals))

    def _gift_fill_types(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        self._fill_type_lists()

    def _save_dir(self):
        """当前存档所在目录 —— 规则文件跟着它走（2026-10-17 玩家定案）。"""
        if self.doc and getattr(self.doc, 'path', None):
            return os.path.dirname(os.path.abspath(self.doc.path))
        return None

    def _map_path(self):
        """当前挂上的地图 —— 大型任务的自制单位/建筑规则写在地图 INI 里（2026-10-17）。"""
        m = getattr(self.doc, '_map', None) if self.doc else None
        return getattr(m, 'path', None) if m else None

    def _rules_types(self, game_dir=None):
        """统一的"取规则"入口：存档目录 + 地图 INI（地图优先）。"""
        return game_ini.get_types(self._save_dir(), game_dir, self._map_path())

    def _unit_type_names(self):
        """**可放置的全部单位型号** → 下拉字符串 `ID（显示名）`。

        名单以**存档**为准（地图里写死的自制单位，存档类型表里一定有）；
        显示名优先取**游戏目录 rules(md).ini**，取不到用存档自带的（类型对象 +120）；
        排序按 rules 的建造栏序号（`[InfantryTypes] 1=E1` 那种），档里没有的排后面。
        """
        if not self.doc:
            return []
        try:
            pairs = self.doc.placeable_unit_types()
        except Exception:
            pairs = []
        if not pairs:
            return []
        g = self._rules_types()
        if g.ok():
            pairs = sorted(pairs, key=lambda p: (g.index_of(p[0]), p[0]))
        out = []
        for n, cls in pairs:
            nm = ''
            if g.ok():
                nm = g.label(n, '') or ''
            if not nm:
                try:
                    nm = self.doc.unit_type_display(n) or ''
                except Exception:
                    nm = ''
            if nm and nm.strip().upper() != n.upper():
                out.append('%s（%s）' % (n, nm.strip()))
            else:
                out.append(n)
        return out

    def _refresh_unit_rule_label(self):
        """界面底部一行：规则文件来自哪个目录（找不到就明说）。"""
        if not hasattr(self, 'mv_urule'):
            return
        g = self._rules_types()
        if g.ok():
            self.mv_urule.config(text='规则：' + g.source(), foreground='#0b6b3a')
        else:
            self.mv_urule.config(text='⚠ 没找到 rules(md).ini（显示名改用存档自带）',
                                 foreground='#b91c1c')

    def mv_pick_rules(self):
        """手动指定游戏目录（含 rulesmd.ini / artmd.ini）→ 写 config.json 并刷新。"""
        d = filedialog.askdirectory(title='选择游戏目录（含 rulesmd.ini / artmd.ini）')
        if not d:
            return
        cfg = game_ini.config_path()
        data = {}
        if os.path.exists(cfg):
            try:
                with open(cfg, 'r', encoding='utf-8') as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
        data['game_dir'] = d
        try:
            with open(cfg, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror('保存失败', str(e))
            return
        game_ini.clear_cache()
        self._refresh_unit_rule_label()
        if self.doc:
            self.mv_unit['values'] = self._unit_type_names()
        self._gift_log('规则目录改为 %s（%s）' % (d, self._rules_types(d).source()))

    def _unit_sel_info(self):
        """选中型号时，把 rules 里的属性摘要写到下面的信息栏。"""
        cur = self.mv_unit.get() if hasattr(self, 'mv_unit') else ''
        if not cur:
            return
        t = cur.split('（')[0].strip()
        g = self._rules_types()
        brief = g.brief(t) if g.ok() else ''
        if brief:
            self.mv_info.config(text='型号 %s：%s' % (t, brief))
        else:
            self.mv_info.config(text='型号 %s：规则里没有这一项（可能是地图自带的自制单位）' % t)

    def mv_putunit(self):
        """地图页：把选中类型的单位**克隆放置**到点选的格子（同类型克隆，U2 已验收）。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        st = self.mv_state
        sel = st.get('sel')
        if not sel:
            messagebox.showwarning('提示', '请先在地图/网格上点选一个格子')
            return
        cur = self.mv_unit.get() if hasattr(self, 'mv_unit') else ''
        if not cur:
            messagebox.showwarning('提示', '本档没有可克隆的单位类型（先刷新类型）')
            return
        tname = cur.split('（')[0].strip()
        house = self._picked_house('mv_u_house')
        _vl = self.mv_u_vet.get() if hasattr(self, 'mv_u_vet') else UNIT_VET_INHERIT
        vet = _VET_BY_LABEL.get(_vl)
        # ★2026-10-18 新增：目标格被建筑/单位占着 ⇒ 主动**挪到最近的空地**（玩家要求）
        #   引擎的占用口径 = 建筑记录 + 地基（`cell_taken`），跟格表 +0xF8 不是一回事。
        try:
            taken = self.doc.cell_taken(sel[0], sel[1])
        except Exception:
            taken = False
        if taken:
            near = None
            try:
                near = self.doc.free_cell_near(sel[0], sel[1], radius=8, clearance=1)
            except Exception:
                near = None
            if not near:
                messagebox.showwarning('放置单位', '格 (%d,%d) 已被建筑/单位占用，附近 8 格内也没找到空地。'
                                       % (sel[0], sel[1]))
                return
            if not messagebox.askyesno(
                    '放置单位',
                    '格 (%d,%d) 已被建筑/单位占用（引擎会把它挤掉或直接销毁）。\n\n'
                    '改放到最近的空地 (%d,%d) 吗？' % (sel[0], sel[1], near[0], near[1])):
                return
            sel = (near[0], near[1])
            st['sel'] = sel
            self.mv_render()
        self.status.set('正在放置单位 %s @(%d,%d)%s%s…'
                        % (tname, sel[0], sel[1],
                           ('，所属方 %s' % house) if house else '',
                           ('，等级 %s' % _vl) if vet is not None else ''))
        self.update_idletasks()
        try:
            info = self.doc.add_unit_by_clone(tname, sel[0], sel[1],
                                              house=house, veterancy=vet)
        except Exception as e:
            self.status.set('放置单位失败')
            messagebox.showerror('放置单位失败', str(e))
            return
        st['gdata'] = None
        self.mv_render()
        _w = info.get('warnings') or []
        _h = info.get('house') or '?'
        _hc = '（改主人：%s → %s）' % (info.get('template_house'), _h) \
            if info.get('house_changed') else '（所属方 %s）' % _h
        self.mv_info.config(
            text='✅ 已克隆放置单位 %s @(%d,%d) %s（自键 %#x，等级 %s，指针表 %d 张，明文 +%d B）%s'
                 % (tname, sel[0], sel[1], _hc, info['key'],
                    info.get('veterancy_name') or '未定义', info['tables'], info['delta'],
                    ('｜' + '；'.join(_w)) if _w else ''))
        self._gift_log('[放单位] %s @(%d,%d)：%s' % (tname, sel[0], sel[1], info))
        base = os.path.splitext(os.path.basename(self.doc.path))[0]
        p = filedialog.asksaveasfilename(
            title='放置结果另存为', defaultextension='.SAV',
            initialfile=(base[:8] + '_兵' + tname)[:16] + '.SAV',
            initialdir=os.path.dirname(self.doc.path))
        if p:
            self._finish_save(p)
        else:
            self.status.set('单位已写入（未保存到磁盘）')

    def mv_place(self):
        """地图页：在点选的格子里放一栋建筑。

        ★2026-10-17：**不要求挂地图**（玩家要求）——
          触发法只吃「格子 + 路径点」，直接落成只吃「格子」；地图只用于看地形/看占用。
          挂不挂地图、匹配不匹配，都不再阻止放置（匹配与否只在打开地图时提示）。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        st = self.mv_state
        sel = st.get('sel')
        if not sel:
            messagebox.showwarning('提示', '请先在地图/网格上点选一个格子')
            return
        sel_t = self.mv_type.get()
        if not sel_t:
            messagebox.showwarning('提示', '请先选择建筑类型（点「刷新类型」）')
            return
        tname = sel_t.split('（')[0].strip()
        lim = self.doc.coord_limit()
        if not (0 <= sel[0] < lim[0] and 0 <= sel[1] < lim[1]):
            messagebox.showerror('失败', '格子 (%d,%d) 超出坐标范围（0..%d / 0..%d）'
                                 % (sel[0], sel[1], lim[0] - 1, lim[1] - 1))
            return
        via = bool(getattr(self, 'mv_via', None) and self.mv_via.get())
        # ---- 先判可用性：不可用时说清原因，并且在另一条路可用时直接问要不要换 ----
        if via:
            ok, why, _diag = self.doc.trigger_readiness()
            other_ok, _ = self.doc.add_placed_readiness(tname)
            other_name = '直接落成'
        else:
            ok, why = self.doc.add_placed_readiness(tname)
            other_ok, _, _ = self.doc.trigger_readiness()
            other_name = '⚡ 触发法'
        if not ok:
            self.status.set('无法放置')
            tip = ('%s 不可用：\n\n%s\n\n' % ('⚡ 触发法' if via else '直接落成', why))
            if other_ok:
                tip += '要不要改用「%s」？（%s）' % (
                    other_name,
                    '引擎自己造，有建造动画；但这份档里没有触发机制'
                    if not via else '立刻写进档案，但没有建造动画')
                if messagebox.askyesno('换一种方式？', tip):
                    via = not via
                    try:
                        self.mv_via.set(via)
                    except Exception:
                        pass
                else:
                    return
            else:
                messagebox.showerror(
                    '这份存档放不了建筑',
                    tip + '两条路都不可用 —— 这通常是**全新开局的干净档**：\n'
                          '· 一栋建筑都没有 ⇒ 没有建筑组可克隆；\n'
                          '· 一个触发/事件对象都没有 ⇒ 也写不了触发。\n\n'
                          '最快的办法：进游戏里随便盖一栋建筑（或让地图带一个触发器）存一次档，\n'
                          '再拿那份档来加建筑。')
                return
        self.status.set('正在放置 %s @(%d,%d)…' % (tname, sel[0], sel[1]))
        self.update_idletasks()
        house = self._picked_house('mv_house')
        try:
            if via:
                _ev = {'流逝时间 1 秒': (13, 1), '任何事件(立即)': (8, 0),
                       '流逝时间 5 秒': (13, 5)}.get(self.mv_ev.get(), (13, 1))
                info = self.doc.place_building_by_trigger(tname, sel[0], sel[1], house=house,
                                                          ev_type=_ev[0], ev_arg=_ev[1])
            else:
                # ★2026-10-21 17:40 **放置法已在游戏内验收通过**（蓝色光棱塔正常出现）。
                #   真因 = 克隆伴随对象 ⇒ 弹框；不建伴随（comp_mode='zero'）⇒ 正常。
                #   该策略现已设为 add_placed_building 的默认值，这里再显式传一次。
                # ★2026-10-22 §183/§188：以前一律传 buildup=True（写 18/1/1/2/0/0）。
                # ★★★★★ 2026-10-23 §196 定案（T42/T43 同基档对照，in-game）：
                #   **`buildup='fresh'` = 写「刚放下」态（AC=-1 / 534=1 / 6E4=0）**。
                #   · `done`（AC=5 / 6E4=1）      → 引擎认为已建好 ⇒ 不建炮塔子对象 ⇒ **无头**
                #   · `18/1/1/2/0/0`（正在播）    → 引擎读档后不从头上播 ⇒ 停在半路
                #   · **`AC=-1 / 534=1 / 6E4=0`** → 读档后自己播一遍、自己建炮塔 ⇒ **完整带棱镜**
                #   `new_state` 也不再写（T17/T18 的 A/B 已证那 22 个常量无可观察影响）。
                info = self.doc.add_placed_building(tname, sel[0], sel[1], house=house,
                                                    require_free=True,
                                                    comp_mode='zero', new_state=False,
                                                    buildup='fresh')
        except Exception as e:
            self.status.set('放置失败')
            messagebox.showerror('放置失败', str(e))
            return
        try:
            self.mv_via.set(via)          # 分支可能被上面的"换一种方式"改过
        except Exception:
            pass
        if via:
            # 触发法：引擎读档后才造，建筑列表里暂时没有它 ⇒ 不要重绘建筑层
            _wd = info.get('warnings') or []
            _evname = {'(13, 1)': '流逝时间 1 秒', '(13, 5)': '流逝时间 5 秒',
                       '(8, 0)': '任何事件'}.get(str((info.get('ev_type'), info.get('ev_arg'))),
                                                '流逝时间')
            self.mv_info.config(
                text='✅ 已写触发：%s @(%d,%d)（路径点 %s，事件「%s」→ 行为 125）'
                     '——请另存后进游戏：读档约 1 秒后由引擎自己造，有建造动画；'
                     '悬崖/水面这类不可建地形会被引擎拒绝。%s'
                % (tname, sel[0], sel[1], info.get('waypoint'), _evname,
                   ('｜' + '；'.join(_wd)) if _wd else ''))
            self._gift_log('[触发法] %s @(%d,%d) 路径点 %s：%s'
                           % (tname, sel[0], sel[1], info.get('waypoint'), info))
            base = os.path.splitext(os.path.basename(self.doc.path))[0]
            p = filedialog.asksaveasfilename(
                title='放置结果另存为', defaultextension='.SAV',
                initialfile=(base[:8] + '_触' + tname)[:16] + '.SAV',
                initialdir=os.path.dirname(self.doc.path))
            if p:
                self._finish_save(p)
            else:
                self.status.set('触发已写入（未保存到磁盘）')
            return
        st['blds'] = self.doc.buildings()
        st['gdata'] = None                 # 建筑变了 ⇒ 网格缓存作废
        self.mv_render()
        _tips = []
        if info.get('house_name'):
            _tips.append('所属方 %s' % info['house_name'])
        # ★P4 提示（2026-10-17）：两条实测结论必须让玩家看到，否则容易误判成"功能坏了"
        # ★§188：直接落成会播一遍建造动画（T16 实测），提示说清"读档后等 1~2 秒"。
        _tips.append('读档后会播一遍建造动画（1~2 秒）再显示完成图，模型/炮塔完整')
        # ★§188：本档若还留着"会自己建建筑"的触发（以前用触发法加楼留下的），
        #   读档时它们会额外刷建筑 —— 做对照实验时必须先知道这件事。
        try:
            _pb = self.doc.pending_build_triggers()
        except Exception:
            _pb = []
        if _pb:
            _tips.append('⚠ 本档还有 %d 条「将建筑建于」触发（%s）——读档时会额外刷建筑，'
                         '对照实验前请先在「触发」页禁用它们'
                         % (len(_pb), '、'.join(n for _s, n in _pb[:4])))
        if '未写它的建筑清单' in (info.get('note') or ''):
            _tips.append('该阵营本档还没建筑 ⇒ 它的建筑清单没写，引擎可能把耗电算到玩家头上')
        self.mv_info.config(text='✅ 已放置 %s @(%d,%d)（登记表 %d 张）——请另存后进游戏验证；%s'
                            % (tname, sel[0], sel[1], info['lists'], '；'.join(_tips)))
        self._gift_log('[直接放好] %s @(%d,%d)：%s' % (tname, sel[0], sel[1], info))
        base = os.path.splitext(os.path.basename(self.doc.path))[0]
        p = filedialog.asksaveasfilename(
            title='放置结果另存为', defaultextension='.SAV',
            initialfile=(base[:8] + '_放' + tname)[:16] + '.SAV',
            initialdir=os.path.dirname(self.doc.path))
        if p:
            self._finish_save(p)
        else:
            self.status.set('放置完成（未保存到磁盘）')

    def _mv_sel_bld(self):
        """选中格上的建筑 dict + 它在建筑组里的序号；失败返回 (None, None, 原因)。"""
        st = getattr(self, 'mv_state', None)
        if not st or not st.get('ok'):
            return None, None, '请先选择并匹配地图'
        sel = st.get('sel')
        if not sel:
            return None, None, '请先在地图上点选一个格子'
        b = st['occ'].get(tuple(sel))
        if not b:
            return None, None, '选中格 (%d,%d) 上没有建筑' % (sel[0], sel[1])
        try:
            hs, _ = self.doc._bld_group()
        except Exception as e:
            return None, None, '无法定位建筑组：%s' % e
        idx = None
        for i, g in enumerate(hs):
            if g + 72 == b['base']:
                idx = i
                break
        if idx is None:
            for i, bb in enumerate(self.doc.buildings()):
                if bb is b:
                    idx = i
                    break
        if idx is None:
            return None, None, '找不到该建筑在建筑组里的序号'
        return b, idx, None

    def mv_set_owner(self):
        """地图页：把**选中格建筑**的所属方改成「所属方」下拉里选的那个（2026-10-17 新增）。

        只写建筑记录载荷 `+336`/`+544`（House 对象键）—— 与「放置建筑」的所属方是同一个字段。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        b, idx, err = self._mv_sel_bld()
        if err:
            messagebox.showwarning('提示', err)
            return
        house = self._picked_house('mv_house')
        if not house:
            messagebox.showwarning('提示', '请先在「所属方」下拉里选一个阵营')
            return
        if not messagebox.askyesno(
                '确认改所属方',
                '把这一栋的所属方改掉：\n\n    类型：%s\n    位置：(%d,%d)\n'
                '    改为：%s\n\n只改建筑记录的所属方字段（+336/+544）；'
                '原档不会被覆盖，改完请「另存为新文件」。'
                % (b['type'], b['x'], b['y'], house)):
            return
        try:
            r = self.doc.set_building_owner(house, idx=idx)
        except Exception as e:
            self.status.set('改所属方失败')
            messagebox.showerror('改所属方失败（存档未改动）', str(e))
            return
        st = self.mv_state
        st['blds'] = self.doc.buildings()
        self.mv_render()
        self.status.set('✅ ' + r['note'])
        self._gift_log('[改所属方] %s' % r)

    def mv_scan_anim(self):
        """地图页：扫描全档，只列出真正有问题的建筑（§170 十四档定稿）。

        ★十四档 in-game 定稿后，五种状态里只有三种是坏的：
          * `buildup`（534=0 / 10C≠0 / AC≠5）⇒ **合法中间态**，进游戏会自己播一遍完整
            建造动画后停住，观感最好 —— **不能报**，报了就是在教用户把动画修没。
          * `done`（534=1 / 6E4=1）⇒ 正常，不报。
          * `stuck`（534=1 但 6E4=0）⇒ 显示不全；修法 = `+0x6E4=1`。
          * `frozen`（534=0 但 10C=0）⇒ 显示不全；修法 = `+0x10C=2` + `+0xAC=18`。
          * `loop`（534=0 / 10C≠0 / AC=5）⇒ 一直播；修法 = `+0xAC=18`。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        bad = []
        n_buildup = 0
        try:
            for b in self.doc.buildings():
                try:
                    st = self.doc.building_anim_state(b['base'])
                except Exception:
                    continue
                if st['state'] in ('stuck', 'frozen', 'loop', 'half'):
                    bad.append((b, st))
                elif st['state'] == 'buildup':
                    n_buildup += 1
        except Exception as e:
            messagebox.showerror('扫描失败', str(e))
            return
        total = len(self.doc.buildings())
        tail = ('\n（另有 %d 栋处于「建造动画未播完」的合法中间态，进游戏会自己播完，不用管）'
                % n_buildup) if n_buildup else ''
        if not bad:
            self.status.set('✅ 全档 %d 栋建筑，没有卡住的（%d 栋待播动画属正常）'
                            % (total, n_buildup))
            messagebox.showinfo('扫描结果',
                                '全档 %d 栋建筑，没有「卡住 / 不播 / 一直播」的。%s\n\n'
                                '如果你在游戏里仍然看到某栋显示不全，'
                                '说明那不是建造动画问题（可能是 SHP/art 缺帧）。'
                                % (total, tail))
            return
        ZH = {'stuck': '卡住', 'frozen': '不播(显示不全)', 'loop': '一直播',
              'half': '半完成(无头/残缺)'}
        lines = ['%-10s (%3d,%3d)  %-12s  AC=%d 10C=%d 534=%d 6E4=%d'
                 % (b['type'], b['x'], b['y'], ZH[st['state']],
                    st['vals']['+0xAC'], st['vals']['+0x10C'],
                    st['vals']['+0x534'], st['vals']['+0x6E4'])
                 for b, st in bad[:20]]
        more = '' if len(bad) <= 20 else '\n…另有 %d 栋' % (len(bad) - 20)
        if not messagebox.askyesno(
                '发现 %d 栋建造动画异常' % len(bad),
                '这 %d 栋在地图上是残缺的：\n\n%s%s%s\n\n'
                '现在就一键全部修好？\n'
                '（卡住 → 补 +0x6E4=1；不播 → 补 +0x10C=2 / +0xAC=18；'
                '一直播 → 改 +0xAC=18）\n'
                '⚠ 修好 = 直接显示完成图 / 正常播一遍，**不会**给你保建造动画。'
                % (len(bad), '\n'.join(lines), more, tail)):
            self.status.set('发现 %d 栋异常（未修复）' % len(bad))
            return
        ok = 0
        for b, _st in bad:
            try:
                if self.doc.repair_building_anim(b['base'])['changed']:
                    ok += 1
            except Exception:
                pass
        self.mv_state['blds'] = self.doc.buildings()
        self.mv_render()
        self.status.set('✅ 已修复 %d 栋（记得另存为新文件）' % ok)
        self._gift_log('[建造动画] 扫描 %d 栋命中 %d，修复 %d'
                       % (total, len(bad), ok))

    def mv_add_anim(self):
        """地图页：给建筑补**动画对象**（476B anim）—— §194（2026-10-23）。

        为什么需要它（玩家三档实测闭环）：
          · 只把字段改成"完成态" ⇒ 电力对，但模型停在**动画第 0 帧**（光棱塔只剩底座）；
          · 引擎"一栋楼建好"时必定新建一个 476B 动画对象，挂在楼记录 `+0x57C`；
          · 所以缺了它 = 少了一半，补上才完整。

        选中格有楼 ⇒ 只补那一栋；没选 ⇒ 补全档所有缺动画的楼。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        b, _idx, err = self._mv_sel_bld()
        if b:
            targets = [b]
            ask = '给 %s (%d,%d) 补一个动画对象？' % (b['type'], b['x'], b['y'])
        else:
            targets = [x for x in self.doc.buildings()
                       if not self.doc.building_anim_key(x['base'] - 72)]
            if not targets:
                messagebox.showinfo('都齐了', '全档每栋楼都已经有动画对象，不用补。')
                return
            ask = ('当前没选中格子 —— 要给**全档 %d 栋缺动画的楼**都补上吗？\n\n'
                   '%s' % (len(targets), '、'.join(
                       '%s(%d,%d)' % (t['type'], t['x'], t['y']) for t in targets[:8])
                       + ('……' if len(targets) > 8 else '')))
        if not messagebox.askyesno('确认', ask + '\n\n（会往存档里插字节，改完记得另存；'
                                              '建议先备份）'):
            return
        ok = fail = 0
        msgs = []
        for t in targets:
            # ★每轮都重新取 buildings()：插字节后旧偏移全部作废
            cur = [x for x in self.doc.buildings()
                   if x['type'] == t['type'] and x['x'] == t['x'] and x['y'] == t['y']
                   and not self.doc.building_anim_key(x['base'] - 72)]
            if not cur:
                continue
            try:
                r = self.doc.add_building_anim(cur[0]['base'])
                ok += 1
                msgs.append('%s(%d,%d)→%08X' % (t['type'], t['x'], t['y'], r['key']))
            except Exception as e:
                fail += 1
                msgs.append('%s(%d,%d) 失败：%s' % (t['type'], t['x'], t['y'], e))
        for fn in ('refresh_buildings', 'refresh_events'):
            f = getattr(self, fn, None)
            if callable(f):
                try:
                    f()
                except Exception:
                    pass
        self.status.set('补齐建筑动画：成功 %d，失败 %d（尚未保存）' % (ok, fail))
        messagebox.showinfo('完成',
                            '成功 %d 栋，失败 %d 栋。\n\n%s\n\n'
                            '★还没保存 —— 请用「另存为」写到新文件再进游戏验证。'
                            % (ok, fail, '\n'.join(msgs[:12])))

    def mv_fix_anim(self):
        """地图页：把选中格建筑修成「直接显示完成图」（§170 十四档定稿）。

        ★修法按状态分派（全部 in-game 已验）：
          * `stuck`  ⇒ 写 1 个字节 `+0x6E4=1`（A→E 档已证显示正常）
          * `frozen` ⇒ `+0x10C=2` + `+0xAC=18`（J/L 档：只撤标记不写 10C 会显示不全）
          * `loop`   ⇒ `+0xAC=18`（K 档：AC=5 会无限播）
        `buildup`（动画没播完）是**合法中间态**，进游戏会自己播完整建造动画，
          修它反而会把动画弄没 ⇒ 明确劝退（什么都不用做，进游戏会自己播完）。
        `done` 不用说。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        b, idx, err = self._mv_sel_bld()
        if err:
            messagebox.showwarning('提示', err)
            return
        try:
            st = self.doc.building_anim_state(b['base'])
        except Exception as e:
            messagebox.showerror('读取失败', str(e))
            return
        head = '%s (%d,%d)' % (b['type'], b['x'], b['y'])
        v = st['vals']
        cur = 'AC=%d  10C=%d  534=%d  6E4=%d' % (
            v['+0xAC'], v['+0x10C'], v['+0x534'], v['+0x6E4'])
        if st['state'] == 'done':
            messagebox.showinfo(
                '无需修复',
                '%s 已经是完成态（%s），不用改。\n\n'
                '若游戏里还是显示不全，那不是建造动画问题。' % (head, cur))
            return
        if st['state'] == 'buildup':
            messagebox.showinfo(
                '这是好事，别修',
                '%s 的建造动画「没播完」——这是**合法中间态**，不是坏档。\n\n'
                '这种档进游戏会**播一遍完整建造动画**，播完就正常，观感最好。\n\n'
                '把它修成完成态，等于把这遍动画跳掉。\n'
                '若确实想让它直接显示成品（不要动画），再点一次这个按钮并选「仍然跳到完成态」。'
                % head)
            if not messagebox.askyesno('仍然跳到完成态？',
                                       '确定要把 %s 的建造动画跳过、直接显示完成图吗？'
                                       % head):
                return
        ZH = {'stuck': '卡住（+0x534=1 但 +0x6E4=0）',
              'frozen': '不播（+0x534=0 但 +0x10C=0）',
              'loop': '一直播（+0xAC=5 播不完）',
              'half': '半完成（没建完却已置 6E4=1 ⇒ 无头/残缺，还按建成吃电）'
                      }.get(st['state'], st['state'])
        todo = {'stuck': '+0x6E4=1',
                'frozen': '+0x10C=2 且 +0xAC=18',
                'loop': '+0xAC=18'}.get(st['state'], '全套完成态 4 个字段')
        if not messagebox.askyesno(
                '确认修复（跳到完成图）',
                '%s   当前状态：%s\n\n'
                '  当前：%s\n'
                '  改为：%s\n\n'
                '（§183 起：新放的楼本来就带一遍建造动画，不用手动重播。）\n\n'
                '原档不覆盖，改完请「另存为新文件」。' % (head, ZH, cur, todo)):
            return
        try:
            r = self.doc.repair_building_anim(b['base'], force=True)
        except Exception as e:
            messagebox.showerror('修复失败（存档未改动）', str(e))
            return
        if not r['changed']:
            self.status.set('未改动（状态已正常）')
            return
        self.mv_state['blds'] = self.doc.buildings()
        self.mv_render()
        self.status.set('✅ 已把 %s 修成完成态（记得另存为新文件）' % head)
        self._gift_log('[建造动画] %s  %s → %s' % (head, r['old'], r['new']))

    def mv_delete(self):
        """地图页：删除选中格上的建筑（2026-10-16 新增，2026-10-18 大图验收通过）。

        与「放置建筑」严格互逆：删记录 + 组计数 −1 + 剔登记表的键（含单条键表）+ 清格子占用
        （4 个字段 +0xF0/+0xF8/+0x114/+0x138）+ **保留无主的伴随对象并把它的格搬到地图外**
        （见 `core.SaveDoc.remove_building`，`orphan_cell='auto'`）。
        ★ 直接删伴随对象会被加载器拒绝（弹框）；伴随对象就是建筑画面本体，
          不搬走会留残影（引擎卖掉后 5 秒内也会自己收走它）。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        st = getattr(self, 'mv_state', None)
        _via = bool(getattr(self, 'mv_via', None) and self.mv_via.get())
        if not st or (not st.get('ok') and not (_via and self.doc)):
            messagebox.showwarning('提示', '请先选择并匹配地图（或勾选「触发法」—— 触发法不需要地图）')
            return
        sel = st.get('sel')
        if not sel:
            messagebox.showwarning('提示', '请先在网格/地图上点选一个格子')
            return
        b = st['occ'].get(tuple(sel))
        if not b:
            messagebox.showinfo('提示', '选中格 (%d,%d) 上没有建筑' % (sel[0], sel[1]))
            return
        # 定位该建筑在建筑组里的序号（用记录偏移硬对，不靠列表顺序猜）
        try:
            hs, _ = self.doc._bld_group()
        except Exception as e:
            messagebox.showerror('无法定位建筑组', str(e))
            return
        idx = None
        for i, g in enumerate(hs):
            if g + 72 == b['base']:          # 实测：buildings()['base'] == 组偏移 + 72
                idx = i
                break
        if idx is None:
            for i, bb in enumerate(self.doc.buildings()):
                if bb is b:
                    idx = i
                    break
        if idx is None or idx >= len(hs):
            messagebox.showerror('定位失败', '找不到该建筑在建筑组里的序号，已中止（存档未改动）')
            return
        if not messagebox.askyesno(
                '确认删除建筑',
                '将从存档里彻底删除这一栋：\n\n'
                '    类型：%s\n    位置：(%d,%d)\n    序号：第 %d 栋（共 %d 栋）\n\n'
                '会一并清掉它的格子占用、剔掉登记表里的键，'
                '并把它的伴随对象（图形件）搬到地图角 (0,0) 闲置\n'
                '（直接删伴随对象会被加载器拒绝 · 实测弹框）。\n\n'
                '⚠ 改完请点上方「另存为新文件」把结果写盘（原档不会被覆盖）。'
                % (b['type'], b['x'], b['y'], idx, len(hs))):
            return
        self.status.set('正在删除 %s @(%d,%d)…' % (b['type'], b['x'], b['y']))
        self.update_idletasks()
        try:
            r = self.doc.remove_building(idx)
        except Exception as e:
            self.status.set('删除失败')
            messagebox.showerror('删除失败（存档未改动）', str(e))
            return
        st['blds'] = self.doc.buildings()
        st['sel'] = None
        self.mv_render()
        self.status.set('✅ ' + r['note'])
        self.mv_info.config(
            text='✅ %s\n（还剩 %d 栋；点上方「另存为新文件」写盘，原档不受影响）'
                 % (r['note'], r['count']))

    def _gift_do(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.gift_type.get()
        if not sel:
            messagebox.showwarning('提示', '请先刷新并选择建筑类型')
            return
        tname = sel.split('（')[0].strip()
        mode = self.gift_mode.get()
        slot2 = None
        s2 = self.gift_slot2.get().strip()
        if s2:
            try:
                slot2 = int(s2, 16)
            except ValueError:
                messagebox.showerror('失败', '槽2 偏移不是合法 hex：%r' % s2)
                return
        self.status.set('正在赠送 %s…' % tname)
        self.update_idletasks()
        try:
            if mode == 'ready':
                self._gift_log('ℹ 「就绪未落地」的侧栏槽属于**当前玩家** ⇒ 固定赠给玩家阵营'
                               '（想给别的阵营就用「直接放好」分支）。')
                info = self.doc.gift_ready_building(tname, slot2_off=slot2)
            else:
                try:
                    cx, cy = int(self.gift_x.get()), int(self.gift_y.get())
                except ValueError:
                    raise RuntimeError('格子坐标不是数字')
                lim = self.doc.coord_limit()
                if not (0 <= cx < lim[0] and 0 <= cy < lim[1]):
                    raise RuntimeError('格子超出地图范围（0..%d / 0..%d）' % (lim[0] - 1, lim[1] - 1))
                info = self.doc.add_placed_building(tname, cx, cy,
                                                    house=self._picked_house('gift_house'),
                                                    require_free=True,
                                                    comp_mode='zero', new_state=True,
                                                    buildup=bool(self.gift_buildup.get()))
        except Exception as e:
            self.status.set('赠送失败')
            messagebox.showerror('赠送失败', str(e))
            return
        self._gift_log('[%s] %s：%s' % (info['mode'], tname, info))
        base = os.path.splitext(os.path.basename(self.doc.path))[0]
        sug = (base[:8] + '_送' + tname)[:16]
        p = filedialog.asksaveasfilename(
            title='赠送结果另存为', defaultextension='.SAV',
            initialfile=sug + '.SAV', initialdir=os.path.dirname(self.doc.path))
        if p:
            self._finish_save(p)
        else:
            self._gift_log('（未选保存路径——修改仍在本档内存里，可用「另存」继续保存）')
            self.status.set('赠送完成（未保存到磁盘）')

    def _tab_search(self, nb):
        f = ttk.Frame(nb, padding=8)
        row = ttk.Frame(f)
        row.pack(fill='x')
        ttk.Label(row, text='查找').pack(side='left')
        self.ent_search = ttk.Entry(row, width=36)
        self.ent_search.pack(side='left', padx=6)
        ttk.Button(row, text='搜索', command=self.do_search).pack(side='left')
        ttk.Label(row, text='（支持中文/英文，或十六进制如 5C197E00）',
                  foreground='#6b7280').pack(side='left', padx=8)
        self.lst_hits = tk.Listbox(f, height=8, font=('Consolas', 9))
        self.lst_hits.pack(fill='x', pady=6)
        self.lst_hits.bind('<<ListboxSelect>>', self.on_hit_select)
        self.txt_hex = tk.Text(f, height=18, font=('Consolas', 9), wrap='none')
        self.txt_hex.pack(fill='both', expand=True)
        return f

    # ---------------- 列表 ----------------
    def refresh_list(self):
        d = self.game_dir.get()
        self.tree.delete(*self.tree.get_children())
        if not os.path.isdir(d):
            self.status.set('目录不存在: %s' % d)
            return
        files = []
        for n in os.listdir(d):
            if n.lower().endswith(('.sav', '.sav.bak')):
                files.append(os.path.join(d, n))
        files.sort(key=lambda p: -os.path.getmtime(p))
        for p in files[:200]:
            self.tree.insert('', 'end', iid=p, values=(os.path.basename(p), '', '', ''),
                             tags=())
        self.status.set('共 %d 个存档（按时间倒序）' % len(files))
        self._scan_meta(list(self.tree.get_children())[:12])

    def _scan_meta(self, items):
        """后台读取前 N 个存档的元信息（任务名/时间/金钱需要完整解码，较慢）。
        ★ 同一时刻只跑一个扫描线程（重复点刷新不再堆积）。"""
        if getattr(self, '_scanning', False):
            return
        self._scanning = True

        def work():
            try:
                for p in items[:12]:
                    try:
                        raw = open(p, 'rb').read()
                        from cfb import CFB
                        c = CFB(raw)
                        import datetime

                        def ft(u64):
                            if not u64:
                                return '-'
                            return (datetime.datetime(1601, 1, 1) +
                                    datetime.timedelta(microseconds=u64 // 10)
                                    ).strftime('%m-%d %H:%M')
                        st = c.open('Last Save Time') or b''
                        t = ft(struct.unpack_from('<Q', st + b'\0' * 8, 0)[0])
                        doc = SaveDoc(p)
                        self.q.put(('row', p, doc.info.get('任务名', '-'), t,
                                    doc.info.get('金钱')))
                    except Exception:
                        self.q.put(('row', p, '?', '?', None))
            finally:
                self._scanning = False
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        """常驻后台消息轮询 —— **永远不停**（之前的 bug：载入时另开了一条临时等待链，
        一旦队列头不是 'loaded' 就 return 且不再安排下一次，导致进度条永远转、档打不开）。"""
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == 'row':
                    p, title, t, money = msg[1:]
                    if self.tree.exists(p):
                        self.tree.item(p, values=(os.path.basename(p), title, t,
                                                  '' if money is None else money))
                elif kind == 'status':
                    self.status.set(msg[1])
                elif kind == 'loaded':
                    self.pbar.stop()
                    self._pending_load = False
                    if msg[1] is not None:
                        self._set_doc(msg[1])
                elif kind == 'done':
                    self.pbar.stop()
                    self._saving = False
                    self.status.set(msg[1])
                    messagebox.showinfo('完成', msg[1])
                elif kind == 'err':
                    self.pbar.stop()
                    self._pending_load = False
                    self._saving = False
                    messagebox.showerror('出错', msg[1])
        except queue.Empty:
            pass
        except Exception as e:                    # 单条坏消息不能让轮询停摆
            self.status.set('后台消息处理异常：%s' % e)
        # ---- 看门狗：长时间没结果就把进度条停掉，绝不"一直转" ----
        now = time.time()
        if self._pending_load and now - self._t_load > 120:
            self._pending_load = False
            self.pbar.stop()
            self.status.set('解码超时（>120 秒）—— 请重试，或换一个存档')
        if self._saving and now - self._t_save > 180:
            self._saving = False
            self.pbar.stop()
            self.status.set('保存超时（>180 秒）—— 请确认文件未被占用后重试')
        self.after(120, self._poll)

    # ---------------- 打开 ----------------
    def open_selected(self):
        sel = self.tree.selection()
        if sel:
            self.load_doc(sel[0])

    def open_dialog(self):
        p = filedialog.askopenfilename(title='选择存档',
                                       filetypes=[('红警存档', '*.sav *.SAV'), ('所有文件', '*.*')])
        if p:
            self.load_doc(p)

    def load_doc(self, path):
        if getattr(self, '_pending_load', False):
            self.status.set('正在打开上一个存档，请稍候…')
            return
        self._pending_load = True
        self._t_load = time.time()
        self.pbar.start(12)
        self.status.set('正在解码 %s …' % os.path.basename(path))

        def work():
            try:
                doc = SaveDoc(path)
                self.q.put(('loaded', doc))
            except Exception as e:
                self.q.put(('err', '解码 %s 失败：\n%s'
                            % (os.path.basename(path), traceback.format_exc())))
        threading.Thread(target=work, daemon=True).start()
        # 结果统一由常驻的 _poll() 处理（不要在这里另开"临时等待链"）

    def _set_doc(self, doc):
        self.doc = doc
        self._warned_zero = False
        # 有地图就**优先挂上**（用地图的权威真值给 事件/行为 加归属、给触发表加条件/行为数）
        try:
            mode = doc.auto_attach_map([self.game_dir.get()])
        except Exception:
            mode = 'no_map'
        for k, lbl in self.info_labels.items():
            lbl.config(text=str(doc.info.get(k, '-')))
        self.lbl_money.config(text=str(doc.info.get('金钱')))
        self.lbl_money_field.config(text='字段: %s' % doc.info.get('金钱字段', ''))
        self.refresh_records()
        self.btn_save_new.config(state='normal')
        self.btn_save_in.config(state='normal')
        self.btn_map_auto.config(state='normal')
        self._refresh_unit_rule_label()          # 规则跟着"存档所在目录"走 ⇒ 换档要刷新
        if hasattr(self, 'mv_unit'):
            try:
                self.mv_unit['values'] = self._unit_type_names()
            except Exception:
                pass
        self.btn_map_pick.config(state='normal')
        self.refresh_houses()
        try:
            self._fill_house_lists(log=False)      # ★所属方下拉随开档就绪（2026-10-17）
        except Exception:
            pass
        self.refresh_events()
        self.refresh_actions()
        self.refresh_ai()
        self._attach_scanned = False
        self.refresh_attach()
        self._mv_reset()
        ms = getattr(doc, 'map_status', None)
        self.status.set('已载入 %s：%d 条记录 · %s' % (
            os.path.basename(doc.path), len(doc.records),
            '未挂地图' if mode == 'no_map' else (ms or {}).get('text', '')))
        # ★§169：开档后延迟静默体检一次「停在生长动画中途」的建筑，只在状态栏提示
        try:
            self.after(400, self._auto_scan_anim)
        except Exception:
            pass

    def _auto_scan_anim(self):
        """开档静默体检：数一遍**真正异常**的建筑（§170 十四档定稿），只更新状态栏。

        ★只报 `stuck / frozen / loop` 三种（地图上都表现为残缺或抽搐）：
            卡住 = +0x534=1 但 +0x6E4=0；不播 = +0x534=0 但 +0x10C=0；
            一直播 = +0x534=0、+0x10C≠0 但 +0xAC=5。
        `buildup`（动画没播完）是**合法中间态**，进游戏会自己播完整建造动画，
        报它等于告假警 —— 玩家已经实测确认这类档"观感最好"。
        """
        if not self.doc:
            return
        try:
            bl = self.doc.buildings()
        except Exception:
            return
        bad = []
        n_buildup = 0
        for b in bl:
            try:
                st = self.doc.building_anim_state(b['base'])
            except Exception:
                continue
            if st['state'] in ('stuck', 'frozen', 'loop'):
                bad.append((b['type'], b['x'], b['y']))
            elif st['state'] == 'buildup':
                n_buildup += 1
        try:
            self.mv_state['anim_bad'] = bad
            self.mv_state['anim_buildup'] = n_buildup
        except Exception:
            pass
        if not bad:
            return
        names = '、'.join('%s(%d,%d)' % t for t in bad[:3])
        if len(bad) > 3:
            names += '…'
        try:
            cur = self.status.get() if hasattr(self.status, 'get') else ''
            self.status.set('%s　⚠ %d 栋建造动画异常（%s）→ 地图页「⏩ 修成完成态」'
                            % (cur, len(bad), names))
        except Exception:
            pass

    # ---------------- 操作 ----------------
    def apply_money(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        s = self.ent_money.get().strip().replace(',', '')
        if not s.isdigit():
            messagebox.showwarning('提示', '请输入 0 ~ 16777215 的整数')
            return
        try:
            self.doc.set_money(int(s))
        except Exception as e:
            messagebox.showerror('出错', str(e))
            return
        self.lbl_money.config(text='%s → %s' % (self.doc.info.get('金钱'), s))
        self.status.set('金钱已改为 %s（尚未保存，点“另存为新文件”或“原地保存”）' % s)

    # ---------------- 所属方金钱 ----------------
    def refresh_houses(self):
        if not hasattr(self, 'th'):
            return
        self.th.delete(*self.th.get_children())
        if not self.doc:
            return
        try:
            hs = self.doc.houses()
        except Exception as e:
            self.status.set('所属方扫描失败: %s' % e)
            return
        for h in hs:
            cell = '—' if h['money'] is None else h['money']
            if h['is_player']:
                note = '← 当前玩家'
            elif h['verified']:
                note = ''
            else:
                note = '未定位（只读）'
            self.th.insert('', 'end', iid=h['short'],
                           values=(h['cn'] or '-', h['short'], cell, note))
        if not hs:
            self.th.insert('', 'end', iid='__none__',
                           values=('—', '未定位', '',
                                   '该存档没有 464 步长的所属方记录数组'))
            self.status.set('该存档未找到所属方记录数组（只能改当前玩家的金钱）')

    def _apply_house(self, short, v):
        try:
            self.doc.set_house_money(short, v)
        except Exception as e:
            messagebox.showerror('出错', str(e))
            return False
        if self.th.exists(short):
            vals = list(self.th.item(short, 'values'))
            vals[2] = v
            self.th.item(short, values=vals)
        if short == self.doc.player_house():
            self.lbl_money.config(text=str(v))
            self.lbl_money_field.config(text='字段: %s' % self.doc.info.get('金钱字段', ''))
        return True

    def edit_house(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.th.selection()
        if not sel:
            messagebox.showwarning('提示', '请先选中一个所属方')
            return
        short = sel[0]
        if short == '__none__':
            return
        try:
            cur = int(self.th.item(short, 'values')[2])
        except Exception:
            cur = 0
        v = simpledialog.askinteger('修改所属方金钱', '%s 的金钱：' % short,
                                    initialvalue=cur, minvalue=0, maxvalue=4294967295,
                                    parent=self)
        if v is None:
            return
        if self._apply_house(short, v):
            self.status.set('%s 金钱已改为 %d（尚未保存）' % (short, v))

    def set_all_houses(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        hs = [h for h in self.doc.houses() if h.get('verified')]
        if not hs:
            messagebox.showwarning('提示', '该存档里没有定位到可写入的所属方')
            return
        v = simpledialog.askinteger('全部设为',
                                    '把全部 %d 个所属方的金钱设为：' % len(hs),
                                    initialvalue=10000, minvalue=0, maxvalue=4294967295,
                                    parent=self)
        if v is None:
            return
        n = 0
        for h in hs:
            if self._apply_house(h['short'], v):
                n += 1
        self.status.set('已把 %d 个所属方设为 %d（尚未保存）' % (n, v))

    def bump_house(self, sign):
        if not self.doc:
            return
        sel = self.th.selection()
        if not sel:
            messagebox.showwarning('提示', '请先选中所属方（可 Ctrl 多选）')
            return
        me = self.doc.player_house()
        sel = [s for s in sel if s != '__none__']
        if not sel:
            messagebox.showwarning('提示', '选中的行不是有效的所属方')
            return
        s = self.ent_delta.get().strip().replace(',', '')
        if not s.lstrip('-').isdigit():
            messagebox.showwarning('提示', '请输入整数增减量')
            return
        d = int(s) * (1 if sign > 0 else -1)
        n = 0
        for short in sel:
            try:
                cur = int(self.th.item(short, 'values')[2])
            except Exception:
                continue
            nv = max(0, min(4294967295, cur + d))
            if self._apply_house(short, nv):
                n += 1
        self.status.set('已调整 %d 个所属方（尚未保存）' % n)

    def toggle_selected(self, on=None):
        """启用 / 禁用选中触发（写「触发类 GUID 前 8 字节」那 1 个字节，见 core.toggle_trigger）。"""
        if not self.doc:
            return
        sel = self.tt.selection()
        if not sel:
            messagebox.showwarning('提示', '请先选中一条触发')
            return
        done = skipped = never = blocked = 0
        for iid in sel:
            r = self.doc.records[int(iid)]
            if self._is_executed(r):        # ★已执行 ⇒ 引擎不再评估，改了没用
                blocked += 1
                continue
            if not r.can_toggle:
                # 大任务档：运行期启用位未定位 ⇒ 「禁用」走大数法等效禁用（改计时不碰启用位）
                if on is False:
                    if not messagebox.askyesno(
                            '大数法等效禁用',
                            '「%s」的运行期启用位未定位（大任务档），无法直接写字节。\n\n'
                            '改用「等效禁用」：把它的计时改成 9999999 秒（≈115 天），\n'
                            '引擎两种启用模型下都不会再触发（实测验证）。\n'
                            '★只改计时不碰启用位。' % r.name, parent=self):
                        skipped += 1
                        continue
                    try:
                        info = self.doc.set_trigger_never_fire(r)
                    except Exception as e:
                        messagebox.showerror('出错', str(e))
                        continue
                    if info.get('changed'):
                        never += 1
                    else:
                        messagebox.showinfo('无需禁用', info.get('note', ''), parent=self)
                    continue
                skipped += 1
                continue
            target = (not bool(r.enabled)) if on is None else on
            try:
                self.doc.toggle_trigger(r, target)
            except Exception as e:
                messagebox.showerror('出错', str(e))
                continue
            vals = list(self.tt.item(iid, 'values'))
            vals[3] = '启用' if target else '禁用'
            vals[7] = r.note          # ★2026-10-20：列变多了（类型/标签数各占一列）⇒ 说明是第 8 列
            self.tt.item(iid, values=vals)
            done += 1
        if blocked:
            messagebox.showwarning(
                '有 %d 条触发可能已经触发过' % blocked,
                '已跳过 %d 条「事件驱动型·已挂钩」的触发 —— 它们可能已经触发过，\n'
                '改启用状态未必生效（列表里显示橙字）。\n\n'
                '若你确认它只是挂钩等待，可以在「触发编辑器」里改（会先弹确认）。' % blocked,
                parent=self)
        if done or never:
            self.status.set('已修改 %d 条触发状态（大数法 %d 条，跳过已执行 %d 条）（尚未保存）'
                            % (done, never, blocked))
            self.refresh_records()
        elif skipped:
            self.status.set('选中的记录不支持切换（启用位未定位 · 只读；「禁用」可选大数法等效禁用）')

    def do_search(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        text = self.ent_search.get().strip()
        if not text:
            return
        hits = self.doc.search(text, limit=300)
        self.lst_hits.delete(0, 'end')
        for h in hits:
            self.lst_hits.insert('end', '%d  (0x%X)' % (h, h))
        self.status.set('命中 %d 处' % len(hits))
        if hits:
            self.lst_hits.selection_set(0)
            self.on_hit_select()

    def on_hit_select(self, evt=None):
        sel = self.lst_hits.curselection()
        if not sel or not self.doc:
            return
        off = int(self.lst_hits.get(sel[0]).split()[0])
        self.txt_hex.delete('1.0', 'end')
        self.txt_hex.insert('end', self.doc.hexdump(off, 384))

    # ---------------- 保存 ----------------
    def _finish_save(self, path):
        if getattr(self, '_saving', False):
            self.status.set('正在保存上一个文件，请稍候…')
            return
        # ★★文件名过长会让游戏「載入遊戲發生錯誤」（2026-10-15 玩家双盲实测：同一 md5 的档，
        #   长名(约77 GBK字节)必崩、短名(约36字节)正常；疑引擎 64 字节缓冲）⇒ 保存前拦截提醒
        try:
            stem_gbk = len(os.path.splitext(os.path.basename(path))[0].encode('gbk'))
        except Exception:
            stem_gbk = 0
        if stem_gbk > 50 and not getattr(self, '_allow_long_name', False):
            if not messagebox.askyesno(
                    '存档文件名过长',
                    '文件名主干约 %d 字节（GBK）。\n\n'
                    '★实测：同一存档，长文件名（约 77 字节）读档必报'
                    '「載入遊戲發生錯誤」，改短后正常（疑引擎 64 字节缓冲上限）。\n'
                    '建议把文件名改短到 25 个汉字以内。\n\n'
                    '仍要用这个名字保存吗？' % stem_gbk):
                return
            self._allow_long_name = True
        self._saving = True
        self._t_save = time.time()
        self.pbar.start(12)
        self.status.set('正在重建压缩块并写盘…')

        def work():
            try:
                res = self.doc.save(path)
                self.q.put(('done', '已保存：%s\n重编码 %d 块，文件 %d B，md5 %s' % (
                    res['out'], res['recompressed'], res['file'], res['md5'][:12])))
            except Exception as e:
                self.q.put(('err', '%s\n%s' % (e, traceback.format_exc())))
        threading.Thread(target=work, daemon=True).start()

    def save_as(self):
        if not self.doc:
            return
        base = os.path.splitext(os.path.basename(self.doc.path))[0]
        sug = base + '_修改'
        # ★长文件名读档会崩（见 _finish_save 注释）⇒ 默认名过长时自动截短
        try:
            if len(sug.encode('gbk', 'ignore')) > 40:
                sug = base[:8] + '_修改'
        except Exception:
            pass
        p = filedialog.asksaveasfilename(title='另存为', defaultextension='.SAV',
                                         initialfile=sug + '.SAV',
                                         initialdir=os.path.dirname(self.doc.path))
        if p:
            self._finish_save(p)

    def _tab_ai(self, nb):
        """AI 三大件：作战小队 TeamType / 特遣部队 TaskForce / 动作脚本 ScriptType（只读）。

        ★2026-10-18 新增（玩家要求）。字段与判据见 core/ai_objects.py 模块注释；
          全部字段都在 SAVE0029 上与地图 INI 逐条核对过（主文档 §118）。
        ★2026-10-18 22:5x 玩家反馈「起码要能看到这三项」⇒ 改成三个子页 + 互相关联高亮：
          选小队 → 特遣/脚本页里它的那两条会被选中；选特遣 → 反查哪些小队在用。
        """
        f = ttk.Frame(nb, padding=8)
        qrow = ttk.Frame(f)
        qrow.pack(fill='x', pady=(0, 6))
        ttk.Label(qrow, text='搜索：').pack(side='left')
        self._q_ai = tk.StringVar()
        ent = ttk.Entry(qrow, textvariable=self._q_ai, width=32)
        ent.pack(side='left')
        ent.bind('<KeyRelease>', lambda e: self._fill_ai_all())
        ent.bind('<Escape>', lambda e: (self._q_ai.set(''), self._fill_ai_all()))
        ttk.Button(qrow, text='✕', width=3,
                   command=lambda: (self._q_ai.set(''), self._fill_ai_all())
                   ).pack(side='left', padx=(2, 8))
        self._ai_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(qrow, text='含引擎生成/运行期对象',
                        variable=self._ai_all,
                        command=self._fill_ai_all).pack(side='left')
        self.lbl_ai_count = ttk.Label(qrow, text='', foreground='#6b7280')
        self.lbl_ai_count.pack(side='left', padx=10)
        ttk.Button(qrow, text='导出 CSV…', command=self._ai_export).pack(side='right')

        sub = ttk.Notebook(f)
        sub.pack(fill='both', expand=True)
        self.ai_sub = sub

        # ① 作战小队
        f1 = ttk.Frame(sub, padding=4)
        c1 = ('id', 'name', 'house', 'max', 'pri', 'vet', 'grp', 'wp', 'tf', 'sc', 'n', 'flag')
        self.tat = ttk.Treeview(f1, columns=c1, show='headings', height=10)
        for c, t, w, a in (('id', '小队ID', 84, 'w'), ('name', '名称', 240, 'w'),
                           ('house', '国家', 92, 'w'), ('max', 'Max', 42, 'e'),
                           ('pri', '优先', 42, 'e'), ('vet', '等级', 42, 'e'),
                           ('grp', '编组', 42, 'e'), ('wp', '出现于', 96, 'w'),
                           ('tf', '特遣部队', 96, 'w'),
                           ('sc', '脚本', 96, 'w'), ('n', '兵力', 44, 'e'),
                           ('flag', '标志', 220, 'w')):
            self.tat.heading(c, text=t)
            self.tat.column(c, width=w, anchor=a)
        self.tat.pack(fill='both', expand=True)
        self.tat.bind('<<TreeviewSelect>>', lambda e: self._ai_pick('team'))
        sub.add(f1, text='  作战小队 TeamType  ')

        # ② 特遣部队
        f2 = ttk.Frame(sub, padding=4)
        c2 = ('id', 'name', 'grp', 'n', 'mem')
        self.tft = ttk.Treeview(f2, columns=c2, show='headings', height=10)
        for c, t, w, a in (('id', '特遣ID', 84, 'w'), ('name', '名称', 300, 'w'),
                           ('grp', '编组', 50, 'e'), ('n', '种数', 50, 'e'),
                           ('mem', '成员', 460, 'w')):
            self.tft.heading(c, text=t)
            self.tft.column(c, width=w, anchor=a)
        self.tft.pack(fill='both', expand=True)
        self.tft.bind('<<TreeviewSelect>>', lambda e: self._ai_pick('tf'))
        # ★2026-10-18 第二步写入：改「数量 / 型号」（定长槽位 +0xB8 + 8×序号）
        # ★2026-10-19 仿 FA2「特遣部队」对话框：成员列表 + 数目/类型 + 添加/删除/应用
        box = ttk.LabelFrame(f2, text='选中的特遣部队（成员表，仿地编样式）', padding=6)
        box.pack(fill='x', pady=(6, 0))

        rowL = ttk.Frame(box)
        rowL.pack(fill='both', expand=True)
        self.tfm = ttk.Treeview(rowL, columns=('i', 'cnt', 'type', 'name'),
                                show='headings', height=6, selectmode='browse')
        for c, t, w, a in (('i', '#', 34, 'e'), ('cnt', '数目', 60, 'e'),
                           ('type', '类型', 110, 'w'), ('name', '名称', 260, 'w')):
            self.tfm.heading(c, text=t)
            self.tfm.column(c, width=w, anchor=a)
        _sbm = ttk.Scrollbar(rowL, orient='vertical', command=self.tfm.yview)
        self.tfm.configure(yscrollcommand=_sbm.set)
        self.tfm.pack(side='left', fill='both', expand=True)
        _sbm.pack(side='left', fill='y')
        self.tfm.bind('<<TreeviewSelect>>', lambda e: self._ai_tf_load_mem())

        rowE2 = ttk.Frame(box)
        rowE2.pack(fill='x', pady=(6, 0))
        ttk.Label(rowE2, text='数目').pack(side='left')
        self.tf_cnt = tk.StringVar(value='1')
        ttk.Spinbox(rowE2, from_=0, to=200, width=5,
                    textvariable=self.tf_cnt).pack(side='left', padx=(2, 8))
        ttk.Label(rowE2, text='类型').pack(side='left')
        self.tf_type = ttk.Combobox(rowE2, width=24, values=())
        self.tf_type.pack(side='left', padx=2)
        ttk.Button(rowE2, text='应用到选中项',
                   command=self._ai_apply_tf).pack(side='left', padx=(8, 2))
        ttk.Button(rowE2, text='添加', command=self._ai_tf_add).pack(side='left', padx=2)
        ttk.Button(rowE2, text='删除', command=self._ai_tf_del).pack(side='left', padx=2)

        rowTF = ttk.Frame(box)
        rowTF.pack(fill='x', pady=(6, 0))
        ttk.Label(rowTF, text='种数').pack(side='left')
        self.tf_n = tk.StringVar(value='2')
        ttk.Spinbox(rowTF, from_=0, to=6, width=3,
                    textvariable=self.tf_n).pack(side='left', padx=2)
        ttk.Button(rowTF, text='改种数', command=self._ai_apply_tf_n).pack(side='left', padx=4)
        # ★2026-10-18 玩家实测：清空特遣 = 变相删小队（不报错）；直接删特遣对象会报错
        ttk.Button(rowTF, text='清空（=删小队）',
                   command=self._ai_clear_tf).pack(side='left', padx=(12, 4))
        ttk.Label(rowTF, text='只写槽位/种数这几个 dword，记录长度不变',
                  foreground='#6b7280').pack(side='left', padx=6)
        sub.add(f2, text='  特遣部队 TaskForce  ')

        # ③ 动作脚本
        f3 = ttk.Frame(sub, padding=4)
        c3 = ('id', 'name', 'n', 'brief')
        self.tst = ttk.Treeview(f3, columns=c3, show='headings', height=10)
        for c, t, w, a in (('id', '脚本ID', 84, 'w'), ('name', '名称', 300, 'w'),
                           ('n', '条目', 50, 'e'), ('brief', '动作摘要', 520, 'w')):
            self.tst.heading(c, text=t)
            self.tst.column(c, width=w, anchor=a)
        self.tst.pack(fill='both', expand=True)
        self.tst.bind('<<TreeviewSelect>>', lambda e: self._ai_pick('script'))
        sub.add(f3, text='  动作脚本 ScriptType  ')

        # ★2026-10-18 第一步写入：只改「老兵等级」这一个定长字段（+0xB4，4 字节）
        rowE = ttk.Frame(f)
        rowE.pack(fill='x', pady=(6, 0))
        ttk.Label(rowE, text='把选中的小队改成：').pack(side='left')
        self.ai_vet = ttk.Combobox(rowE, width=16, state='readonly',
                                   values=('1 新兵(无星)', '2 老兵(一级)', '3 精英(三级)'))
        self.ai_vet.current(2)          # ★默认 3=三级（游戏内已验证）
        self.ai_vet.pack(side='left')
        ttk.Button(rowE, text='应用（只改 4 字节）',
                   command=self._ai_apply_vet).pack(side='left', padx=6)
        ttk.Label(rowE, text='所属国家').pack(side='left', padx=(12, 2))
        self.ai_house = ttk.Combobox(rowE, width=14, state='readonly', values=())
        self.ai_house.pack(side='left')
        ttk.Button(rowE, text='改国家', command=self._ai_apply_house).pack(side='left', padx=4)
        ttk.Label(rowE, text='出现于').pack(side='left', padx=(12, 2))
        self.ai_wp = ttk.Combobox(rowE, width=16, state='readonly', values=())
        self.ai_wp.pack(side='left')
        ttk.Button(rowE, text='改出生点', command=self._ai_apply_wp).pack(side='left', padx=4)
        # ★2026-10-19 第五步：预装载（+0xB9，1 字节）
        self.ai_load = tk.BooleanVar(value=False)
        ttk.Checkbutton(rowE, text='预装载', variable=self.ai_load).pack(side='left', padx=(10, 2))
        ttk.Button(rowE, text='应用', command=self._ai_apply_loadable).pack(side='left', padx=2)
        ttk.Label(rowE, text='只改 +0xB4/+0xB9/+0xD8/+0xE8，长度不变',
                  foreground='#b45309').pack(side='left', padx=6)

        ttk.Label(f, text='选中任一页里的一行 ⇒ 下方显示明细，并在另外两页里高亮它关联的对象'
                          '（选小队看它的特遣/脚本；选特遣看谁在用它）',
                  foreground='#6b7280').pack(anchor='w', pady=(6, 2))
        self.ai_text = tk.Text(f, height=8, wrap='none',
                               font=('Consolas', 9), background='#fbfbfd')
        self.ai_text.pack(fill='both', expand=True)
        self._ai = None
        self._ai_teams, self._ai_tfs, self._ai_scripts = {}, {}, {}
        self._ai_rows = {'team': [], 'tf': [], 'script': []}
        sub.bind('<<NotebookTabChanged>>', lambda e: self._ai_reapply())
        return f

    def refresh_ai(self):
        """重读 AI 三大件（开档 / 换档后调用）。"""
        if not hasattr(self, 'tat'):
            return
        for tv in (self.tat, self.tft, self.tst):
            tv.delete(*tv.get_children())
        self._ai, self._ai_teams, self._ai_tfs, self._ai_scripts = None, {}, {}, {}
        self._ai_rows = {'team': [], 'tf': [], 'script': []}
        self.ai_text.delete('1.0', 'end')
        if not self.doc:
            return
        try:
            ai = ai_objects.read_ai(self.doc)
        except Exception as e:
            self.ai_text.insert('end', '读取失败：%s\n%s' % (e, traceback.format_exc()))
            return
        self._ai = ai
        self._ai_teams = {t['id']: t for t in ai['teams']}
        self._ai_tfs = {t['id']: t for t in ai['taskforces']}
        self._ai_scripts = {t['id']: t for t in ai['scripts']}
        for t in ai['teams']:
            fl = '/'.join(k.split('/')[0][:4] for k, v in t['flags'].items() if v) or '—'
            self._ai_rows['team'].append((t['id'], (
                t['id'], t['name'], t['house'], t['max'], t['priority'], t['veteran'],
                t['group'], self._wp_text(t.get('waypoint')),
                t['taskforce_id'] or '—', t['script_id'] or '—',
                sum(m['count'] for m in t['members']), fl), bool(t.get('is_map', True))))
        for t in ai['taskforces']:
            mem = '  '.join('%d×%s' % (m['count'], m['type']) for m in t['members'])
            self._ai_rows['tf'].append((t['id'], (
                t['id'], t['name'], t['group'], len(t['members']), mem or '（空）'),
                bool(t.get('is_map', True))))
        for t in ai['scripts']:
            br = '  '.join('%d %s(%d)' % (e['action'], script_action_name(e['action']), e['arg'])
                           for e in t['entries'][:4])
            if len(t['entries']) > 8:
                br += '  …(%d 条)' % len(t['entries'])
            self._ai_rows['script'].append((t['id'], (
                t['id'], t['name'], len(t['entries']), br or '（空）'),
                bool(t.get('is_map', True))))
        # 阵营下拉：跟着本档的 House 对象表走（换档就换列表）
        try:
            houses = sorted(str(v) for v in self.doc._house_key_names().values())
        except Exception:
            houses = []
        if hasattr(self, 'ai_house'):
            self.ai_house['values'] = houses
            if houses and self.ai_house.get() not in houses:
                self.ai_house.set(houses[0])
        # 路径点下拉：只列"本档已定义"的路径点（号 + 格子）
        try:
            wp = self.doc.waypoints_save() or {}
        except Exception:
            wp = {}
        self._wp_cache = dict(wp)
        if hasattr(self, 'ai_wp'):
            def _fmt(n, c):
                return '%d → (%d,%d)' % (n, c[0], c[1])
            # 目标格未定义时 team 会刷在地图角上 ⇒ 把 0..99 里未定义的也列出来（标"未定义"）
            vals = [_fmt(n, wp[n]) for n in sorted(wp)]
            for n in range(0, 100):
                if n not in wp:
                    vals.append('%d → 未定义' % n)
            self.ai_wp['values'] = vals
            if vals and self.ai_wp.get() not in vals:
                self.ai_wp.set(vals[0])
        self._fill_ai_all()
        n_map = sum(1 for t in ai['teams'] if t.get('is_map'))
        self.status.set('AI：小队 %d（地图定义 %d）　特遣部队 %d　动作脚本 %d'
                        % (len(ai['teams']), n_map, len(ai['taskforces']),
                           len(ai['scripts'])))

    def _fill_ai_all(self):
        self._tf_type_labels = None
        if not hasattr(self, 'tat'):
            return
        q = ((self._q_ai.get() if hasattr(self, '_q_ai') else '') or '').strip().lower()
        show_all = bool(self._ai_all.get()) if hasattr(self, '_ai_all') else False
        out = []
        for kind, tv in (('team', self.tat), ('tf', self.tft), ('script', self.tst)):
            tv.delete(*tv.get_children())
            rows = self._ai_rows.get(kind, [])
            shown = 0
            for iid, vals, is_map in rows:
                if not is_map and not show_all:
                    continue
                if q and not any(q in str(v).lower() for v in vals):
                    continue
                tv.insert('', 'end', iid=iid, values=vals)
                shown += 1
            out.append('%s %d/%d' % ({'team': '小队', 'tf': '特遣', 'script': '脚本'}[kind],
                                     shown, len(rows)))
        if hasattr(self, 'lbl_ai_count'):
            self.lbl_ai_count.config(text='　'.join(out)
                                     + ('' if show_all else '（已隐藏引擎生成的）'))
        self._ai_reapply()

    # ---- 三个页面的互相关联 ----
    def _ai_pick(self, kind):
        """某一页选中变化 ⇒ 更新明细 + 在另外两页高亮关联对象。

        ★2026-10-19 修 **点任何小队都卡死**：`_ai_reapply` 会去另外两页 `selection_set`，
          而那两个树也绑了 `<<TreeviewSelect>>` ⇒ 回调又回到这里、又反向选中本页，
          形成 tat→tft→tat 的**无限互相触发**（事件驱动，纯调用方法测不出来）。
          用 `_ai_syncing` 闸门：程序化选中期间，回调控一下直接返回。
        """
        if not getattr(self, '_ai', None):
            return
        if getattr(self, '_ai_syncing', False):
            return
        self._ai_sel = kind
        self._ai_reapply()

    def _ai_reapply(self):
        """按当前选中项算关联键，在另外两个树上选中它们，并刷新明细文本。"""
        if not getattr(self, '_ai', None) or not hasattr(self, 'ai_text'):
            return
        team_id = (self._ai_focus(self.tat) if hasattr(self, 'tat') else None)
        tf_id = self._ai_focus(self.tft) if hasattr(self, 'tft') else None
        sc_id = self._ai_focus(self.tst) if hasattr(self, 'tst') else None
        kind = getattr(self, '_ai_sel', None)

        if kind == 'team' and team_id in self._ai_teams:
            t = self._ai_teams[team_id]
            tf_id, sc_id = t['taskforce_id'], t['script_id']
            self._ai_select(self.tft, tf_id)
            self._ai_select(self.tst, sc_id)
        elif kind == 'tf' and tf_id in self._ai_tfs:
            users = [t for t in self._ai['teams'] if t['taskforce_id'] == tf_id]
            sc_id = None
            if users:
                self._ai_select(self.tat, users[0]['id'])
                team_id = users[0]['id']
                sc_id = users[0]['script_id']
                self._ai_select(self.tst, sc_id)
        elif kind == 'script' and sc_id in self._ai_scripts:
            users = [t for t in self._ai['teams'] if t['script_id'] == sc_id]
            if users:
                self._ai_select(self.tat, users[0]['id'])
                team_id = users[0]['id']
                tf_id = users[0]['taskforce_id']
                self._ai_select(self.tft, tf_id)
        # 成员表跟着当前特遣刷新（仿地编：选中队伍 → 下面列成员）
        try:
            self._ai_tf_fill_mem(tf_id)
        except Exception:
            pass
        # 选中哪条小队，就把「预装载」勾选框同步成它的当前值
        if hasattr(self, 'ai_load'):
            _t = self._ai_teams.get(team_id) if team_id else None
            self.ai_load.set(bool(_t.get('loadable')) if _t else False)
        self._ai_detail(kind, team_id, tf_id, sc_id)

    def _ai_unsync(self):
        """after_idle 回调：把联动闸门放下（等已排队的 <<TreeviewSelect>> 派完）。"""
        self._ai_syncing = False

    @staticmethod
    def _ai_focus(tv):
        sel = tv.selection()
        return sel[0] if sel else None

    def _ai_select(self, tv, iid):
        """在树里选中指定 iid（不在当前过滤结果里就跳过，不报错）。

        ★2026-10-19：`selection_set` 会**同步**触发 `<<TreeviewSelect>>`，
          这里把 `_ai_syncing` 闸门打开，避免三页互相选中打成死循环。
        """
        if not iid:
            return
        # ★闸门必须**跨事件队列**有效：ttk 的 <<TreeviewSelect>> 是虚拟事件，
        #   不是同步回调（同步 finally 复原 = 闸门形同虚设，实测仍会死循环）。
        #   ⇒ 选中前置位，恢复交给 after_idle（Tk 会先把已排队的虚拟事件派完、再跑 idle）。
        self._ai_syncing = True
        try:
            if str(iid) in set(tv.get_children()):
                tv.selection_set(str(iid))
                tv.see(str(iid))
        except Exception:
            pass
        try:
            self.after_idle(self._ai_unsync)
        except Exception:
            self._ai_syncing = False

    def _ai_detail(self, kind, team_id, tf_id, sc_id):
        L = []
        t = self._ai_teams.get(team_id)
        if t and kind == 'team':
            L.append('作战小队 %s「%s」' % (t['id'], t['name']))
            L.append('  国家 %s　Max %d　优先级 %d　老兵等级 %d（%s）　编组 %d'
                     % (t['house'], t['max'], t['priority'], t['veteran'],
                        ai_objects.TEAM_VET_NAMES.get(t['veteran'], '未定义'), t['group']))
            L.append('  标志：' + '　'.join('%s=%s' % (k, '是' if v else '否')
                                          for k, v in t['flags'].items()))
            L.append('  明文偏移 +%#x（只读）' % t['off'])
        tf = self._ai_tfs.get(tf_id)
        if tf:
            L.append('')
            L.append('特遣部队 %s「%s」　编组 %d　%d 种　共 %d 个'
                     % (tf['id'], tf['name'], tf['group'], len(tf['members']),
                        sum(m['count'] for m in tf['members'])))
            for m in tf['members']:
                L.append('    %2d × %-14s (键 %#010x)' % (m['count'], m['type'], m['type_key']))
            users = [x['id'] for x in self._ai['teams'] if x['taskforce_id'] == tf_id]
            L.append('    被 %d 个小队使用%s' % (len(users),
                                         ('：' + '、'.join(users[:6]) +
                                          ('…' if len(users) > 6 else '')) if users else ''))
        sc = self._ai_scripts.get(sc_id)
        if sc:
            L.append('')
            L.append('动作脚本 %s「%s」　%d 条' % (sc['id'], sc['name'], len(sc['entries'])))
            for i, e in enumerate(sc['entries']):
                # ★2026-10-19：这里必须用**脚本动作表**（3=移动到路径点），
                #   以前误用触发器行为表（3=生产开始）⇒ 玩家一眼看出读错。
                nm = script_action_name(e['action'])
                extra = ''
                pt = SCRIPT_PARAM_TYPES.get(e.get('ptype') or 0, '')
                if e.get('ptype') == 2:            # 路径点
                    try:
                        c = self.doc.waypoint_cell(e['arg']) if self.doc else None
                    except Exception:
                        c = None
                    extra = '  → 路径点 %d%s' % (e['arg'], (' (%d,%d)' % c) if c else '')
                elif pt:
                    extra = '（%s）' % pt
                L.append('    %2d. %-2d %-22s 参数 %d%s'
                         % (i, e['action'], nm, e['arg'], extra))
        if not L:
            L.append('（在上面三页里选一行看明细）')
        self.ai_text.delete('1.0', 'end')
        self.ai_text.insert('end', '\n'.join(L))

    def _ai_apply_vet(self):
        """把「作战小队」子页里选中的那条改成指定老兵等级（★唯一的写入功能，先易后难第一步）。"""
        if not self.doc or not getattr(self, '_ai', None):
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.tat.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在「作战小队」子页里选中一条小队')
            return
        sid = sel[0]
        t = self._ai_teams.get(sid)
        if not t:
            return
        try:
            lv = int((self.ai_vet.get() or '0').split()[0])
        except Exception:
            lv = 0
        if not messagebox.askyesno(
                '改小队老兵等级',
                '小队 %s「%s」\n\n老兵等级 %d → %d\n\n'
                '只写 TeamType 的 +0xB4 这 4 个字节，不改变文件结构。\n'
                '⚠ 若该小队已经刷出来过，引擎可能仍用旧值，建议新开一局验证。'
                % (sid, t['name'], t['veteran'], lv)):
            return
        try:
            info = ai_objects.set_team_veteran(self.doc, sid, lv)
        except Exception as e:
            messagebox.showerror('改不了', str(e))
            return
        self.refresh_ai()
        self._ai_select(self.tat, sid)
        self._ai_pick('team')
        self.status.set('已改小队 %s「%s」老兵等级 %d → %d（尚未保存）'
                        % (sid, info['name'], info['old'], info['new']))

    # ---- 特遣部队成员编辑（仿 FA2 对话框）----
    def _ai_tf_types(self):
        """类型下拉的候选：`E1 — G.I.`（显示名取存档自带 +120）。返回 {标签: 型号名}。"""
        if getattr(self, '_tf_type_labels', None):
            return self._tf_type_labels
        out = {}
        # ★2026-10-19 玩家指出：这里只能列**兵种**（步兵/载具），不能把建筑拉进来。
        #   `unit_type_index()` 只枚举 TYPE_GROUP_INF / TYPE_GROUP_UNIT 两组类 GUID，
        #   天然排除建筑/动画等（本图 194 项 vs unit_types() 的 471 项）。
        try:
            names = sorted((self.doc.unit_type_index() or {}).keys())
        except Exception:
            names = []
        extra = set()
        for tf in getattr(self, '_ai_tfs', {}).values():
            for m in tf.get('members') or []:
                t = str(m.get('type') or '')
                if t and not t.startswith('#') and t.upper() not in {n.upper() for n in names}:
                    extra.add(t)
        for nm in sorted(set(names) | extra):
            disp = ''
            try:
                disp = self.doc.unit_type_display(nm) or ''
            except Exception:
                disp = ''
            out[('%s — %s' % (nm, disp)) if disp else nm] = nm
        self._tf_type_labels = out
        if hasattr(self, 'tf_type'):
            self.tf_type['values'] = list(out)
        return out

    def _ai_tf_fill_mem(self, tf_id=None):
        """把当前选中特遣的成员表刷进下面的列表（仿地编里那个"单位"框）。

        `tf_id` 由 `_ai_reapply` 传进来 —— 程序化选中期间树事件被闸门挡住，
        不能依赖 `self.tft.selection()`（那一刻可能还没同步）。
        """
        if not hasattr(self, 'tfm'):
            return
        self._ai_tf_types()          # 顺便把类型下拉填好（带缓存）
        self.tfm.delete(*self.tfm.get_children())
        if not tf_id:
            sel = self.tft.selection() if hasattr(self, 'tft') else ()
            tf_id = sel[0] if sel else None
        tf = self._ai_tfs.get(tf_id) if tf_id else None
        if not tf:
            return
        for i, m in enumerate(tf['members']):
            disp = ''
            try:
                disp = self.doc.unit_type_display(m['type']) or ''
            except Exception:
                disp = ''
            self.tfm.insert('', 'end', iid=str(i),
                            values=(i, m['count'], m['type'], disp))

    def _ai_tf_load_mem(self):
        """列表里点某一行 ⇒ 把「数目 / 类型」填成该行的值。"""
        sel = self.tfm.selection() if hasattr(self, 'tfm') else ()
        if not sel:
            return
        self.tf_cnt.set(str(self.tfm.set(sel[0], 'cnt')))
        name = self.tfm.set(sel[0], 'type')
        labels = self._ai_tf_types()
        for lab, nm in labels.items():
            if nm == name:
                self.tf_type.set(lab)
                break
        else:
            self.tf_type.set(name)

    def _tf_selected(self):
        """→ (tf, 选中成员序号 or None)。"""
        if not self.doc or not getattr(self, '_ai', None):
            messagebox.showwarning('提示', '请先打开一个存档')
            return None, None
        sel = self.tft.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在「特遣部队」子页里选中一条')
            return None, None
        tf = self._ai_tfs.get(sel[0])
        if not tf:
            return None, None
        msel = self.tfm.selection() if hasattr(self, 'tfm') else ()
        idx = int(msel[0]) if msel else None
        return tf, idx

    def _tf_after_edit(self, tf_id, msg):
        self.refresh_ai()
        self._ai_select(self.tft, tf_id)
        self._ai_pick('tf')
        # `_ai_select` 会把联动闸门抬起（after_idle 才放下），这期间 `_ai_pick` 会被挡掉
        # ⇒ 这里显式再刷一次成员表，保证改完立刻能看到新结果
        try:
            self._ai_tf_fill_mem(tf_id)
        except Exception:
            pass
        self.status.set(msg + '（尚未保存）')

    def _ai_apply_tf(self):
        """把「数目 / 类型」写到列表里选中的那一项（定长槽位，长度不变）。"""
        tf, idx = self._tf_selected()
        if not tf:
            return
        if idx is None:
            messagebox.showwarning('提示', '请先在上面的成员表里点一行')
            return
        try:
            cnt = int(self.tf_cnt.get())
        except Exception:
            messagebox.showwarning('提示', '数目必须是整数')
            return
        lab = (self.tf_type.get() or '').strip()
        tname = self._ai_tf_types().get(lab, lab) or None
        m = tf['members'][idx]
        if m['count'] == cnt and (not tname or tname.upper() == m['type'].upper()):
            self.status.set('这一项已经是 %d × %s，无需修改' % (cnt, m['type']))
            return
        try:
            info = ai_objects.set_taskforce_member(self.doc, tf['id'], idx,
                                                   count=cnt, type_name=tname)
        except Exception as e:
            messagebox.showerror('改不了', str(e))
            return
        old = '%d × %s' % (m['count'], m['type'])
        new = '%d × %s' % (info['new_count'], tname or m['type'])
        self._tf_after_edit(tf['id'], '已改特遣 %s 第 %d 项：%s → %s'
                            % (tf['id'], idx, old, new))

    def _ai_tf_add(self):
        """在末尾追加一项（种数 <6 才行），数量/类型取当前输入。"""
        tf, _idx = self._tf_selected()
        if not tf:
            return
        n = len(tf['members'])
        if n >= 6:
            messagebox.showwarning('已满', '这条特遣部队已经有 6 项（引擎槽位上限）')
            return
        try:
            cnt = int(self.tf_cnt.get())
        except Exception:
            messagebox.showwarning('提示', '数目必须是整数')
            return
        lab = (self.tf_type.get() or '').strip()
        tname = self._ai_tf_types().get(lab, lab)
        if not tname:
            messagebox.showwarning('提示', '请先选一个类型')
            return
        try:
            ai_objects.set_taskforce_member(self.doc, tf['id'], n, count=cnt,
                                            type_name=tname, allow_new=True)
            ai_objects.set_taskforce_size(self.doc, tf['id'], n + 1, zero_tail=False)
        except Exception as e:
            messagebox.showerror('加不了', str(e))
            return
        self._tf_after_edit(tf['id'], '已给特遣 %s 添加第 %d 项：%d × %s'
                            % (tf['id'], n, cnt, tname))

    def _ai_tf_del(self):
        """删掉列表里选中的那一项（后面各项前移，种数 −1）。"""
        tf, idx = self._tf_selected()
        if not tf:
            return
        if idx is None:
            messagebox.showwarning('提示', '请先在上面的成员表里点一行')
            return
        if len(tf['members']) <= 1:
            messagebox.showwarning('提示', '只剩一项了：要清空请用「清空（=删小队）」')
            return
        m = tf['members'][idx]
        if not messagebox.askyesno('删掉这一项',
                                   '特遣 %s「%s」第 %d 项：%d × %s\n\n'
                                   '后面各项会前移，种数 %d → %d。\n'
                                   '只动槽位与种数，记录长度不变。'
                                   % (tf['id'], tf['name'], idx, m['count'],
                                      m['type'], len(tf['members']),
                                      len(tf['members']) - 1)):
            return
        try:
            info = ai_objects.remove_taskforce_member(self.doc, tf['id'], idx)
        except Exception as e:
            messagebox.showerror('删不了', str(e))
            return
        self._tf_after_edit(tf['id'], '已删特遣 %s 第 %d 项（%d × %s），种数 %d → %d'
                            % (tf['id'], idx, m['count'], m['type'],
                               info['old_n'], info['new_n']))

    def _ai_apply_tf_n(self):
        """改「特遣部队」的成员种数（+0xB0）。减项建议同时把多余槽位数量改成 0。"""
        if not self.doc or not getattr(self, '_ai', None):
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.tft.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在「特遣部队」子页里选中一条')
            return
        tf_id = sel[0]
        tf = self._ai_tfs.get(tf_id)
        if not tf:
            return
        try:
            n = int(self.tf_n.get())
        except Exception:
            messagebox.showwarning('提示', '种数必须是整数')
            return
        if not (1 <= n <= 6):
            messagebox.showwarning('提示', '种数只能 1..6')
            return
        cur = len(tf['members'])
        if not messagebox.askyesno(
                '改特遣部队种数',
                '特遣部队 %s「%s」\n\n成员种数 %d → %d\n\n'
                '只写 +0xB0 这 4 个字节，不动槽位内容。\n'
                '⚠ 删项时引擎是否按种数截断读取尚未验证 —— 保险做法是同时把多余槽位的'
                '「数量」改成 0（用上面那行）。' % (tf_id, tf['name'], cur, n)):
            return
        try:
            info = ai_objects.set_taskforce_size(self.doc, tf_id, n)
        except Exception as e:
            messagebox.showerror('改不了', str(e))
            return
        self.refresh_ai()
        self._ai_select(self.tft, tf_id)
        self._ai_pick('tf')
        self.status.set('已改特遣 %s 种数 %d → %d（尚未保存）'
                        % (tf_id, info['old'], info['new']))

    def _ai_clear_tf(self):
        """把选中的特遣部队清空 —— 玩家验证过的「变相删小队」办法。"""
        if not self.doc or not getattr(self, '_ai', None):
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.tft.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在「特遣部队」子页里选中一条')
            return
        tf_id = sel[0]
        tf = self._ai_tfs.get(tf_id)
        if not tf:
            return
        users = [t['id'] for t in self._ai['teams'] if t.get('taskforce_id') == tf_id]
        if not messagebox.askyesno(
                '清空特遣部队（= 删小队）',
                '特遣部队 %s「%s」\n\n当前 %d 种：%s\n\n'
                '会做两件事：6 个槽位数量全部清零 + 种数写 0。\n'
                '效果：用它的小队刷不出任何东西（等于没有这个小队），**不报错**。\n'
                '⚠ 不要「删除特遣对象」本身 —— 小队用指针引用它，删了会报错。\n\n'
                '受影响的小队 %d 个：%s'
                % (tf_id, tf['name'], len(tf['members']),
                   '  '.join('%d×%s' % (m['count'], m['type']) for m in tf['members']) or '（空）',
                   len(users), '、'.join(users[:6]) or '（无）')):
            return
        try:
            info = ai_objects.clear_taskforce(self.doc, tf_id)
        except Exception as e:
            messagebox.showerror('清空失败', str(e))
            return
        self.refresh_ai()
        self._ai_select(self.tft, tf_id)
        self._ai_pick('tf')
        self.status.set('已清空特遣 %s（%d 种 → 0），用它的小队将不再刷兵' % (tf_id, info['old_n']))

    def _ai_apply_house(self):
        """改「作战小队」的所属国家（+0xD8 指向的 House 对象键）。"""
        if not self.doc or not getattr(self, '_ai', None):
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.tat.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在「作战小队」子页里选中一条')
            return
        sid = sel[0]
        t = self._ai_teams.get(sid)
        if not t:
            return
        house = (self.ai_house.get() or '').strip()
        if not house:
            messagebox.showwarning('提示', '请先选一个所属国家')
            return
        if not messagebox.askyesno(
                '改小队所属国家',
                '小队 %s「%s」\n\n所属国家 %s → %s\n\n'
                '只写 +0xD8（指向本档 House 对象的键），长度不变。\n'
                '改完后该小队刷出来的兵就归这个国家。\n'
                '⚠ 若小队已经刷出过，引擎可能仍用旧值 ⇒ 建议新开一局验证。'
                % (sid, t['name'], t['house'], house)):
            return
        try:
            info = ai_objects.set_team_house(self.doc, sid, house)
        except Exception as e:
            messagebox.showerror('改不了', str(e))
            return
        self.refresh_ai()
        self._ai_select(self.tat, sid)
        self._ai_pick('team')
        self.status.set('已改小队 %s「%s」所属国家 %s → %s（尚未保存）'
                        % (sid, info['name'], info['old_house'], info['new_house']))

    def _wp_text(self, n):
        """路径点号的显示文本：`0 (54,86)`。"""
        if n is None or n < 0:
            return '—'
        try:
            c = self.doc.waypoint_cell(n) if self.doc else None
        except Exception:
            c = None
        return ('%d (%d,%d)' % (n, c[0], c[1])) if c else ('%d (未定义)' % n)

    def _ai_apply_wp(self):
        """改「作战小队」的出生路径点（+0xE8 = 小队出现于）。"""
        if not self.doc or not getattr(self, '_ai', None):
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.tat.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在「作战小队」子页里选中一条')
            return
        sid = sel[0]
        t = self._ai_teams.get(sid)
        if not t:
            return
        txt = (self.ai_wp.get() or '').strip()
        try:
            wp = int(txt.split('→')[0].strip())
        except Exception:
            messagebox.showwarning('提示', '请先选一个路径点')
            return
        cell = self._wp_cache.get(wp) if hasattr(self, '_wp_cache') else None
        tail = ('该路径点已定义在 (%d,%d)' % cell) if cell else                ('⚠ 该路径点**未定义**（(0,0)），小队会刷在地图角上 —— 建议先给这个号设格子')
        if not messagebox.askyesno(
                '改小队出生点',
                '小队 %s「%s」\n\n出现于 路径点 %s → %d\n%s\n\n'
                '只写 +0xE8（小队出现于的路径点号），长度不变。\n'
                '⚠ 已刷出的小队可能仍用旧值 ⇒ 建议新开一局验证。'
                % (sid, t['name'], self._wp_text(t.get('waypoint')), wp, tail)):
            return
        try:
            info = ai_objects.set_team_waypoint(self.doc, sid, wp)
        except Exception as e:
            messagebox.showerror('改不了', str(e))
            return
        self.refresh_ai()
        self._ai_select(self.tat, sid)
        self._ai_pick('team')
        self.status.set('已改小队 %s「%s」出现于 路径点 %d → %d（%s，尚未保存）'
                        % (sid, info['name'], info['old'], info['new'],
                           ('(%d,%d)' % info['cell']) if info['cell'] else '未定义'))

    def _ai_apply_loadable(self):
        """开/关选中小队的「预装载」（+0xB9，1 字节）。"""
        if not self.doc or not getattr(self, '_ai', None):
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.tat.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在「作战小队」子页里选中一条')
            return
        sid = sel[0]
        t = self._ai_teams.get(sid)
        if not t:
            return
        on = bool(self.ai_load.get())
        if bool(t.get('loadable')) == on:
            messagebox.showinfo('无需修改', '小队 %s 的预装载已经是「%s」' % (sid, '开' if on else '关'))
            return
        if not messagebox.askyesno(
                '改小队预装载',
                '小队 %s「%s」\\n\\n预装载：%s → %s\\n\\n'
                '只写 +0xB9 这 1 个字节，记录长度不变。\\n'
                '打开后该小队刷出来时直接装在运输工具里。'
                % (sid, t['name'], '开' if t.get('loadable') else '关', '开' if on else '关')):
            return
        try:
            info = ai_objects.set_team_loadable(self.doc, sid, on)
        except Exception as e:
            messagebox.showerror('改不了', str(e))
            return
        self.refresh_ai()
        self._ai_select(self.tat, sid)
        self._ai_pick('team')
        self.status.set('已改小队 %s「%s」预装载 %d → %d（尚未保存）'
                        % (sid, info['name'], info['old'], info['new']))

    def _ai_export(self):
        """把三张表导出成一份 CSV（UTF-8 带 BOM，Excel 直接打开不乱码）。"""
        if not getattr(self, '_ai', None):
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        base = os.path.splitext(os.path.basename(self.doc.path))[0]
        p = filedialog.asksaveasfilename(
            title='导出 AI 表', defaultextension='.csv',
            initialfile=(base[:10] + '_AI表.csv'),
            filetypes=[('CSV', '*.csv')])
        if not p:
            return
        import csv
        try:
            with open(p, 'w', newline='', encoding='utf-8-sig') as fh:
                w = csv.writer(fh)
                w.writerow(['类型', 'ID', '名称', '国家', 'Max', '优先级', '老兵',
                            '编组', '特遣部队', '脚本', '成员/动作'])
                for t in self._ai['teams']:
                    w.writerow(['小队', t['id'], t['name'], t['house'], t['max'],
                                t['priority'], t['veteran'], t['group'],
                                t['taskforce_id'] or '', t['script_id'] or '',
                                '  '.join('%d×%s' % (m['count'], m['type'])
                                          for m in t['members'])])
                for t in self._ai['taskforces']:
                    w.writerow(['特遣部队', t['id'], t['name'], '', '', '', '',
                                t['group'], '', '',
                                '  '.join('%d×%s' % (m['count'], m['type'])
                                          for m in t['members'])])
                for t in self._ai['scripts']:
                    w.writerow(['动作脚本', t['id'], t['name'], '', '', '', '',
                                '', '', '',
                                '  '.join('%d,%d' % (e['action'], e['arg'])
                                          for e in t['entries'])])
        except Exception as e:
            messagebox.showerror('导出失败', str(e))
            return
        self.status.set('已导出 %s' % p)
        messagebox.showinfo('导出完成', '已写出：\n%s' % p)

    def save_inplace(self):
        if not self.doc:
            return
        if not messagebox.askyesno('确认', '将覆盖原存档（自动备份到 _备份存档 目录）：\n%s'
                                   % self.doc.path):
            return
        self._finish_save(self.doc.path)


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
        self.geometry('880x740')
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
        e.pack(side='left', padx=6)
        e.bind('<Return>', lambda ev: self.apply_name())
        ttk.Button(r1, text='改名', command=self.apply_name).pack(side='left')
        ttk.Label(r1, text='  ID：').pack(side='left')
        self.lbl_sid = ttk.Label(r1, text='—', foreground='#6b7280')
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
        self.lbl_rep_hint = ttk.Label(r2, text='', foreground='#b91c1c')
        self.lbl_rep_hint.pack(side='left', padx=(2, 0))
        ttk.Button(r2, text='应用', width=5, command=self.apply_owner_rep).pack(side='left')
        ttk.Label(r2, text='  下级触发').pack(side='left', padx=(12, 0))
        ttk.Label(r2, text='（存档里未定位·待样本）',
                  foreground='#9ca3af').pack(side='left')

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
        self.lbl_state = ttk.Label(r3, text='', foreground='#6b7280')
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
        self.lbl_evcnt = ttk.Label(r4, text='', foreground='#6b7280')
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
        self.lbl_evhint = ttk.Label(ev, text='', foreground='#374151', wraplength=820,
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

        body = ttk.Frame(ac)
        body.pack(fill='both', expand=True, pady=(6, 0))
        cols = ('idx', 'type', 'name', 'arg', 'argtext')
        self.tv_ac = ttk.Treeview(body, columns=cols, show='headings', height=8)
        for c, t, w, a in (('idx', '#', 34, 'center'), ('type', '类型', 46, 'center'),
                           ('name', '行为类型', 240, 'w'), ('arg', '参数', 70, 'e'),
                           ('argtext', '参数含义', 150, 'w')):
            self.tv_ac.heading(c, text=t)
            self.tv_ac.column(c, width=w, anchor=a)
        self.tv_ac.pack(fill='both', expand=True)
        self.tv_ac.bind('<<TreeviewSelect>>', lambda e: self._ac_pick())
        self.lbl_achint = ttk.Label(ac, text='', foreground='#374151', wraplength=820,
                                    justify='left')
        self.lbl_achint.pack(anchor='w', pady=(4, 0))

        bar = ttk.Frame(self)
        bar.pack(fill='x', padx=8, pady=(0, 8))
        # ★2026-10-20 玩家反馈"没有保存按钮"：底部提示文字太长，把右边的按钮挤没了。
        #   现在**按钮先占位**（右边固定框），文字再填剩下的宽度并自动换行。
        btnbox = ttk.Frame(bar)
        btnbox.pack(side='right')
        ttk.Button(btnbox, text='关闭', width=8, command=self.destroy).pack(side='right')
        ttk.Button(btnbox, text='✔ 保存该触发', width=14,
                   command=self.save_doc).pack(side='right', padx=(0, 8))
        ttk.Label(bar, text='改动先落在存档内存里（新建模式=真正创建这条触发）；'
                            '要写到文件请回主窗口「保存」。删除无法撤销，建议先备份。',
                  foreground='#b45309', wraplength=520, justify='left'
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
        self.lbl_sid.config(text='（新建·尚未写入存档）', foreground='#b45309')
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
        self.lbl_state.config(foreground='#b45309',
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
        self.lbl_sid.config(text=sid, foreground='#6b7280')
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
        tip, col = '', '#b91c1c'
        try:
            natt = det['attached'] or 0
            etypes = [e['type'] for e in self.doc.trigger_event_list(sid)]
            if 1 in etypes and natt >= 2:
                tip = '⚠ 进入事件挂 %d 个对象：持久1 是已知 BUG（永不执行）' % natt
            elif natt == 0:
                tip = '⚠ 挂接 0 个对象：持久1 永不触发'
                col = '#6b7280'
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
                    state += ' · 未执行（节点还在，剩余挂载 %s）' % _rs['remain']
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
            txt, col = ('共 %d 条' % n, '#6b7280')
        else:
            txt, col = ('共 %d 条（AND：任一不满足即短路，上面先评估）' % n, '#b45309')
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

    def _fill_actions(self):
        self.tv_ac.delete(*self.tv_ac.get_children())
        if getattr(self, '_new_mode', False):
            for i, (t, a) in enumerate(getattr(self, '_pending_actions', []), 1):
                self.tv_ac.insert('', 'end', iid=str(i), values=(
                    i, t, action_name(t), a, self.doc._arg_text(action_ptype(t), a)))
            return
        for i, a in enumerate(self._det.get('actions') or [], 1):
            self.tv_ac.insert('', 'end', iid=str(i), values=(
                i, a['type'], a['name'], a['arg'], a['arg_text']))

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
        rec = next((r for r in self.doc.records if r.kind == '触发' and r.sid == self._cur),
                   None)
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
            k = None
            sel2 = self.tv_ac.focus()
            acts2 = (self._det or {}).get('actions') or []
            if sel2 and 0 <= int(sel2) - 1 < len(acts2):
                k = acts2[int(sel2) - 1]['type']
            elif self.cb_ac.current() >= 0:
                k = self._ac_keys[self.cb_ac.current()]
            if k is not None:
                pt = action_ptype(k)
                try:
                    cur = self._parse_arg(self.var_acarg.get())
                except Exception:
                    cur = None
                self._fill_arg_values(self.cb_acarg, pt, cur)
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
        self.var_acarg.set(str(a['arg']))
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
            try:
                arg = self._parse_arg(self.var_acarg.get())
            except ValueError:
                self.app.status.set('参数必须是整数')
                return
            self._pending_actions[i] = (self._ac_keys[j], arg)
            self._fill_actions()
            self.app.status.set('新建触发：第 %d 条行为 → %s（参数 %d）'
                                % (i + 1, ACTION_TYPES[self._ac_keys[j]][0], arg))
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
        try:
            arg = self._parse_arg(self.var_acarg.get())
        except ValueError:
            messagebox.showwarning('提示', '参数必须是整数', parent=self)
            return
        a = acts[int(sel) - 1]
        try:
            self.doc.set_action(a, atype=self._ac_keys[i], arg=arg)
        except Exception as e:
            messagebox.showerror('修改失败', str(e), parent=self)
            return
        self.after_change('行为 #%s → %s（参数 %d）'
                          % (sel, ACTION_TYPES[self._ac_keys[i]][0], arg))

    def add_action(self):
        if getattr(self, '_new_mode', False):
            j = self.cb_ac.current()
            if j < 0:
                # 没选类型就点「添加」：只提示，不弹框（玩家要求）
                self.app.status.set('新建触发：先在「行为类型」里选一个再点「添加」')
                return
            try:
                arg = self._parse_arg(self.var_acarg.get())
            except ValueError:
                self.app.status.set('参数必须是整数')
                return
            self._pending_actions.append((self._ac_keys[j], arg))
            self._fill_actions()
            self.app.status.set('新建触发：已加第 %d 条行为 %s（参数 %d）—— 还没写入存档'
                                % (len(self._pending_actions),
                                   ACTION_TYPES[self._ac_keys[j]][0], arg))
            return
        i = self.cb_ac.current()
        if i < 0 or not self._cur:
            return
        try:
            arg = self._parse_arg(self.var_acarg.get())
        except ValueError:
            messagebox.showwarning('提示', '参数必须是整数', parent=self)
            return
        try:
            self.doc.add_action(self._cur, self._ac_keys[i], arg)
        except Exception as e:
            messagebox.showerror('添加失败', str(e), parent=self)
            return
        self.after_change('已添加行为：%s（参数 %d）' % (ACTION_TYPES[self._ac_keys[i]][0], arg))

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
            t = self._pending_actions.pop(i)[0]
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
    def new_trigger(self):
        """把"待建清单"提交成一条真触发（= 玩家说的"保存这个写好的触发"）。

        §174：**一次可以带多个事件 + 多条行为**（走 `create_trigger_multi`）。
        事件顺序 = 清单顺序；存档里链头是**清单最后一条**（引擎最先评估它）。
        """
        events = list(getattr(self, '_pending_events', []) or [])
        acts = list(getattr(self, '_pending_actions', []) or [])
        if not events:
            messagebox.showwarning(
                '提示', '至少要有一个事件。\n\n先在「事件设置」里选类型/参数，再点「＋添加」。',
                parent=self)
            return
        et, ea = events[0]
        # ★2026-10-20 玩家要求：**没有行为就建一条空触发**（不再硬塞 21 播放语音，
        #   否则还得再加一条才能把它删掉）。actions=None 就是不建行为对象。
        src_name = (getattr(self, '_pending_name', None) or self.var_name.get().strip()
                    or '新触发')[:16]
        nm = src_name
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
            frm, justify='left', wraplength=560, foreground='#374151',
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
    def after_change(self, msg, new_sid=None):
        try:
            self.app.refresh_records()
            self.app.refresh_events()
            self.app.refresh_actions()
        except Exception:
            pass
        self.app.status.set(msg + '（尚未保存）')
        self.reload(new_sid or self._cur)


REPEAT_LABEL = {0: '一次性·任一挂载', 1: '一次性·挂载清空', 2: '可重复'}


if __name__ == '__main__':
    App().mainloop()
