# -*- coding: utf-8 -*-
"""AI页（原 `App.ai`）

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

from editor_core import SCRIPT_PARAM_TYPES
from editor_core import script_action_name
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk
import ai_objects
import os
import tkinter as tk
import traceback
import ui_theme


class AiPage(object):
    def _tab_ai(self, nb):
        """AI 三大件：作战小队 TeamType / 特遣部队 TaskForce / 动作脚本 ScriptType（只读）。

        ★2026-10-18 新增（玩家要求）。字段与判据见 core/ai_objects.py 模块注释；
          全部字段都在 SAVE0029 上与地图 INI 逐条核对过（主文档 §118）。
        ★2026-10-18 22:5x 玩家反馈「起码要能看到这三项」⇒ 改成三个子页 + 互相关联高亮：
          选小队 → 特遣/脚本页里它的那两条会被选中；选特遣 → 反查哪些小队在用。
        """
        f = ttk.Frame(nb, padding=8)
        _ph = ui_theme.page_header(f, '作战小队', '小队 / 特遣 / 脚本（只读）')
        _ph.pack(fill='x', padx=2, pady=(2, 8))
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
        ttk.Checkbutton(qrow, text='含引擎生成的 AI 小队',
                        variable=self._ai_all,
                        command=self._fill_ai_all).pack(side='left')
        ttk.Label(qrow, text='（Yuri - Infantry - M2 那批是引擎按 rules 自动生成的，'
                             '99% 的任务用不到；地图作者写的在 010 开头那批里，'
                             '本工具新写的也会显示）',
                  foreground=ui_theme.T['muted_soft']).pack(side='left', padx=(4, 0))
        self.lbl_ai_count = ttk.Label(qrow, text='', foreground=ui_theme.T['muted'])
        self.lbl_ai_count.pack(side='left', padx=10)
        ttk.Button(qrow, text='导出 CSV…', command=self._ai_export).pack(side='right')

        # ★§253（玩家 2026-10-24 22:1x 要求）：「新建特遣部队 / 动作脚本 / 作战小队」+「触发挂小队」。
        #   昨天已经有"刷兵三件套一把做完"（§217/§218，GUI 里是地图页的「🔫 刷单位(小队法)」），
        #   这一排是把它**拆开单独用**，并且补上"给已有触发挂小队"这一步。
        nrow = ttk.Frame(f)
        nrow.pack(fill='x', pady=(0, 6))
        ttk.Label(nrow, text='新建：', foreground=ui_theme.T['info']).pack(side='left')
        ttk.Button(nrow, text='＋ 特遣部队',
                   command=self._ai_new_tf).pack(side='left', padx=2)
        ttk.Button(nrow, text='＋ 动作脚本',
                   command=self._ai_new_script).pack(side='left', padx=2)
        # ★§258：改成完整编辑器（仿地编：新建/复制/编辑全字段）
        ttk.Button(nrow, text='✏ 作战小队…',
                   command=self._ai_new_team).pack(side='left', padx=2)
        ttk.Button(nrow, text='🔗 触发挂小队…',
                   command=self._ai_attach_team).pack(side='left', padx=(14, 2))
        ttk.Label(nrow, text='（挂小队 = 给触发器写「80 援军 / 107 超时空援军」行为：'
                             '小队键 +0x44、路径点 +0x58，与刷兵是同一条路）',
                  foreground=ui_theme.T['muted']).pack(side='left', padx=6)

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
        # ★§264 玩家要求：**双击一行 ⇒ 直接打开「作战小队」对话框改这一支**
        self.tat.bind('<Double-1>', self._ai_team_dblclick)
        self.tat.configure(cursor='hand2')      # 提示"这行能点"
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
        # ★§264 双击 ⇒ 直接改这一支特遣部队
        self.tft.bind('<Double-1>', self._ai_tf_dblclick)
        self.tft.configure(cursor='hand2')
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
                  foreground=ui_theme.T['muted']).pack(side='left', padx=6)
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
        # ★§264 双击 ⇒ 直接改这一段脚本
        self.tst.bind('<Double-1>', self._ai_script_dblclick)
        self.tst.configure(cursor='hand2')
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
        # ★§226：动作脚本下拉（含「（空）」= 清空 +0xF4）
        ttk.Label(rowE, text='动作脚本').pack(side='left', padx=(12, 2))
        self.ai_script = ttk.Combobox(rowE, width=20, state='readonly', values=())
        self.ai_script.pack(side='left')
        ttk.Button(rowE, text='改脚本', command=self._ai_apply_script).pack(side='left', padx=4)
        ttk.Label(rowE, text='（选「（空）」= 与地编「脚本留空」一致：引擎按无脚本小队处理）',
                  foreground=ui_theme.T['muted']).pack(side='left', padx=2)
        # ★2026-10-19 第五步：预装载（+0xB9，1 字节）
        self.ai_load = tk.BooleanVar(value=False)
        ttk.Checkbutton(rowE, text='预装载', variable=self.ai_load).pack(side='left', padx=(10, 2))
        ttk.Button(rowE, text='应用', command=self._ai_apply_loadable).pack(side='left', padx=2)
        # ★§235：16 个布尔标志 + 心控重建，一屏勾选
        ttk.Button(rowE, text='标志…', command=self._ai_flags_dialog).pack(side='left', padx=(8, 2))
        ttk.Button(rowE, text='关联标签…', command=self._ai_tag_dialog).pack(side='left', padx=2)
        ttk.Label(rowE, text='只改 +0xB4/+0xB9/+0xD8/+0xE8/+0xE4，长度不变',
                  foreground=ui_theme.T['warn']).pack(side='left', padx=6)

        ttk.Label(f, text='选中任一页里的一行 ⇒ 下方显示明细，并在另外两页里高亮它关联的对象'
                          '（选小队看它的特遣/脚本；选特遣看谁在用它）',
                  foreground=ui_theme.T['muted']).pack(anchor='w', pady=(6, 2))
        self.ai_text = tk.Text(f, height=8, wrap='none',
                               font=('Consolas', 9), background=ui_theme.T['surface_2'])
        self.ai_text.pack(fill='both', expand=True)
        self._ai = None
        self._ai_teams, self._ai_tfs, self._ai_scripts = {}, {}, {}
        self._ai_rows = {'team': [], 'tf': [], 'script': []}
        # ★§223：本工具本次会话新写的小队/特遣/脚本 ID（默认显示，不当"引擎生成"藏掉）
        self._ai_ours = set()
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
        self._ai_ours = set()          # §223：换档清空（sid 空间按档独立）
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
        # ★§273：路径点表**先取一次**（`_wp_text` 每支小队都要查号 → 坐标）
        try:
            self._wp_cache = self.doc.waypoints_save() or {}
        except Exception:
            self._wp_cache = {}
        for t in ai['teams']:
            # ★§235：标志现在解全了（16 个），表格里只列前 3 个开着的，其余用 … 表示
            _on = [ai_objects.TEAM_FLAG_LABELS.get(k, k) for k, v in t['flags'].items() if v]
            fl = '/'.join(_on[:3]) + ('…' if len(_on) > 3 else '') if _on else '—'
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
        # §226：动作脚本下拉 = 「（空）」+ 脚本 ID
        # ★§262：只列**地图定义**的脚本 —— 玩家 2026-10-25：「AI 的脚本不要显示，这个没用，
        #   只显示玩家自己的脚本即可」。判据同 §254（`is_map` = `0100xxxx`）。
        if hasattr(self, 'ai_script'):
            svals = self._ai_script_values(self.ai_script.get())
            self.ai_script['values'] = svals
            if self.ai_script.get() not in svals:
                self.ai_script.set('（空）')
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
        ours = getattr(self, '_ai_ours', None) or set()
        out = []
        hidden = 0
        for kind, tv in (('team', self.tat), ('tf', self.tft), ('script', self.tst)):
            tv.delete(*tv.get_children())
            rows = self._ai_rows.get(kind, [])
            shown = 0
            for iid, vals, is_map in rows:
                # ★§223：默认只显示「地图定义的(010 开头) + 本工具本次会话新写的」；
                #   引擎按 rules 生成的 AI 小队（Yuri - Infantry - M2 那批）默认隐藏 ——
                #   99% 的任务不用 AI 小队，任务作者用的是自己写的那批。
                if not is_map and iid not in ours and not show_all:
                    hidden += 1
                    continue
                if q and not any(q in str(v).lower() for v in vals):
                    continue
                tv.insert('', 'end', iid=iid, values=vals)
                shown += 1
            out.append('%s %d/%d' % ({'team': '小队', 'tf': '特遣', 'script': '脚本'}[kind],
                                     shown, len(rows)))
        if hasattr(self, 'lbl_ai_count'):
            tip = ('　已隐藏引擎生成的 %d 个（勾选左边可看）' % hidden) if hidden else ''
            self.lbl_ai_count.config(text='　'.join(out) + tip)
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
        # §226：脚本下拉跟着选中的小队同步（选中谁就显示谁当前的脚本）
        # ★§262：候选只列地图定义脚本；当前值若是 AI 脚本就临时补进列表，
        #   否则下拉会显示成「（空）」—— 把真实值藏起来比显示它更糟
        if hasattr(self, 'ai_script'):
            _s = self._ai_teams.get(team_id) if team_id else None
            _cur = (_s.get('script_id') or '（空）') if _s else '（空）'
            self.ai_script['values'] = self._ai_script_values(_cur)
            self.ai_script.set(_cur)
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
            L.append('  标志：' + '　'.join(
                '%s=%s' % (ai_objects.TEAM_FLAG_LABELS.get(k, k), '是' if v else '否')
                for k, v in t['flags'].items()))
            _mcv = int(t.get('mc_decision', 0) or 0)
            L.append('  心控结果（MindControlDecision）= %d（%s）'
                     % (_mcv, ai_objects.TEAM_MC_LABELS.get(_mcv, '未定义')))
            L.append('  关联标签 = %s（+0xE4；挂上后该标签上的触发会把本队当事件对象）'
                     % (t.get('tag_id') or '（无）'))
            L.append('  明文偏移 +%#x（只读）' % t['off'])
            # ★§225：挂着"非地图定义"的脚本时给一行说明 —— 本图地图的
            #   [ScriptTypes] 只有 ID 索引列表，地编/第三方工具对这类脚本常显示 None，
            #   但存档指针有效、引擎读档后照常执行（别被"None"误导）。
            if t.get('script_id'):
                _sc = self._ai_scripts.get(t['script_id'])
                if _sc is not None and not _sc.get('is_map', True):
                    L.append('  ⚠ 动作脚本 %s 不是地图定义的（引擎生成/经典脚本）—— '
                             '地编里可能显示 None，但引擎会照常执行它' % _sc['id'])
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

    # ================= §253 新建 AI 三大件 + 触发挂小队 =================
    def _dlg_form(self, title, rows, note=''):
        """简易表单对话框。`rows` = [(标签, key, 默认值, 'text'|'combo', [选项…])]。

        返回 `{key: 字符串}`；取消返回 None。
        """
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=10)
        body.pack(fill='both', expand=True)
        vs = {}
        for i, row in enumerate(rows):
            label, key, default, kind = row[0], row[1], row[2], row[3]
            vals = row[4] if len(row) > 4 else None
            ttk.Label(body, text=label).grid(row=i, column=0, sticky='w', pady=3)
            v = tk.StringVar(value=str(default))
            vs[key] = v
            if kind == 'combo':
                ttk.Combobox(body, textvariable=v, values=list(vals or []),
                             width=46, state='readonly').grid(
                    row=i, column=1, sticky='we', padx=(8, 0), pady=3)
            else:
                ttk.Entry(body, textvariable=v, width=48).grid(
                    row=i, column=1, sticky='we', padx=(8, 0), pady=3)
        if note:
            ttk.Label(body, text=note, foreground=ui_theme.T['muted'], wraplength=540,
                      justify='left').grid(row=len(rows), column=0, columnspan=2,
                                           sticky='w', pady=(6, 0))
        res = {}

        def _ok():
            res.update({k: v.get() for k, v in vs.items()})
            win.destroy()

        bar = ttk.Frame(body)
        bar.grid(row=len(rows) + 1, column=0, columnspan=2, sticky='e', pady=(10, 0))
        ttk.Button(bar, text='确定', command=_ok).pack(side='left', padx=4)
        ttk.Button(bar, text='取消', command=win.destroy).pack(side='left')
        win.grab_set()
        self.wait_window(win)
        return res or None

    @staticmethod
    def _parse_units_spec(s):
        out = []
        for part in str(s).replace('，', ',').split(','):
            part = part.strip()
            if not part:
                continue
            sep = ':' if ':' in part else ('=' if '=' in part else None)
            if not sep:
                raise ValueError('单位要写成「型号:数量」，例如 DISK:2 —— 收到 %r' % part)
            t, n = part.split(sep, 1)
            out.append((t.strip().upper(), int(n)))
        if not out:
            raise ValueError('至少填一个单位')
        return out

    @staticmethod
    def _parse_entries_spec(s):
        out = []
        for part in str(s).replace('，', ',').split(','):
            part = part.strip()
            if not part:
                continue
            sep = ':' if ':' in part else ('=' if '=' in part else None)
            if not sep:
                raise ValueError('动作要写成「动作:参数」，例如 5:10 —— 收到 %r' % part)
            a, arg = part.split(sep, 1)
            out.append((int(a, 0), int(arg, 0)))
        return out

    def _mk_modal(self, title, w, h):
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.geometry('%dx%d' % (w, h))
        win.grab_set()
        return win

    # ★2026-10-28 删：这里原有一个 5 行的 `_unit_type_names`（返回裸 ID），
    #   它在同一个类里**覆盖**了前面 4874 行那个 33 行的正式版本 ⇒ 地图页全部单位下拉
    #   都退化成 `ADOG` 这种裸 ID，丢了「ADOG（警犬）」的中文名与 rules 建造栏排序。
    #   它当初是提交 62a4d23a 为了迁就 `_dlg_tf.add()` 没做 split('（') 而加的补丁；
    #   现在 add() 已改成先 split，故删除本定义，统一走 4874 行那一版。

    def _dlg_tf(self, name='新特遣部队', units=None, ident=None):
        """特遣部队对话框（仿地编：名称 / 分组 / 单位列表 / 添加 / 删除 / 数目 / 类型）。

        ★§264：给了 `ident`（特遣的 8 位 ID）= **编辑已有的那一支**（表格里双击进来的）；
          不给 = 新建。`units` 格式 `[(数量, 型号), ...]`。
        """
        units = [list(u) for u in (units or [])]
        win = self._mk_modal(('编辑特遣部队 %s' % ident) if ident else '特遣部队', 560, 430)
        top = ttk.Frame(win, padding=8)
        top.pack(fill='x')
        ttk.Label(top, text='名称').pack(side='left')
        v_name = tk.StringVar(value=name)
        ttk.Entry(top, textvariable=v_name, width=40).pack(side='left', padx=6)
        ttk.Label(top, text='分组 -1（与地编一致）',
                  foreground=ui_theme.T['muted']).pack(side='left', padx=6)
        body = ttk.Frame(win, padding=(8, 0))
        body.pack(fill='both', expand=True)
        tv = ttk.Treeview(body, columns=('i', 'cnt', 'type'), show='headings', height=10)
        for c, t, w, a in (('i', '#', 40, 'e'), ('cnt', '数目', 70, 'e'),
                           ('type', '类型', 160, 'w')):
            tv.heading(c, text=t)
            tv.column(c, width=w, anchor=a)
        tv.pack(fill='both', expand=True)

        def refill(sel=None):
            tv.delete(*tv.get_children())
            for i, (cnt, ty) in enumerate(units):
                tv.insert('', 'end', iid=str(i), values=(i + 1, cnt, ty))
            if sel is not None and str(sel) in tv.get_children():
                tv.selection_set(str(sel))

        refill()
        row = ttk.Frame(win, padding=8)
        row.pack(fill='x')
        ttk.Label(row, text='数目').pack(side='left')
        v_cnt = tk.StringVar(value='1')
        ttk.Spinbox(row, from_=0, to=999, width=5, textvariable=v_cnt).pack(side='left', padx=(2, 8))
        ttk.Label(row, text='类型').pack(side='left')
        v_ty = tk.StringVar(value=(self._unit_type_names() or [''])[0])
        ttk.Combobox(row, textvariable=v_ty, values=self._unit_type_names(),
                     width=16).pack(side='left', padx=2)

        def add():
            try:
                n = int(v_cnt.get() or 0)
            except ValueError:
                return
            ty = (v_ty.get() or '').split('（')[0].strip().upper()
            # ★2026-10-28：下拉项是 `ID（显示名）`（见 _unit_type_names），必须先去括号再当型号，
            #   否则会把 "ADOG（警犬）" 整个写进特遣部队的类型键。
            if not ty or n <= 0:
                return
            if len(units) >= 6:
                messagebox.showwarning('特遣部队', '最多 6 种单位（结构如此）', parent=win)
                return
            for u in units:                     # 同型号合并
                if u[1] == ty:
                    u[0] += n
                    break
            else:
                units.append([n, ty])
            refill(len(units) - 1)

        def rm():
            sel = tv.selection()
            if not sel:
                return
            for i in sorted((int(x) for x in sel), reverse=True):
                units.pop(i)
            refill()

        ttk.Button(row, text='添加', command=add).pack(side='left', padx=(7, 2))
        ttk.Button(row, text='删除', command=rm).pack(side='left', padx=2)
        bar = ttk.Frame(win, padding=8)
        bar.pack(fill='x')
        res = {}

        def ok():
            if not units:
                messagebox.showwarning('特遣部队', '至少要有一个单位', parent=win)
                return
            res.update({'name': v_name.get().strip() or '新特遣部队',
                        'units': [(u[1], int(u[0])) for u in units],
                        'ident': ident})
            win.destroy()

        ttk.Button(bar, text='确定', command=ok).pack(side='right', padx=4)
        ttk.Button(bar, text='取消', command=win.destroy).pack(side='right')
        self.wait_window(win)
        return res or None

    def _dlg_script(self, name='新脚本', entries=None, ident=None):
        """动作脚本对话框（仿地编：行为列表 + 行为类型(中文名) + 参数 + 行为描述）。

        ★§264：给了 `ident` = **编辑已有的那一段脚本**（表格里双击进来的）；不给 = 新建。
        """
        try:
            import script_actions as sa          # 只有"行为描述"
        except Exception:
            sa = None
        try:
            import fa2_tables as _ft             # 动作中文名（已有表，别重复维护）
        except Exception:
            _ft = None

        def _nm(i):
            return _ft.script_action_name(int(i)) if _ft else '#%d' % int(i)
        entries = [list(e) for e in (entries or [])]
        win = self._mk_modal(('编辑动作脚本 %s' % ident) if ident else '动作脚本', 820, 540)
        top = ttk.Frame(win, padding=8)
        top.pack(fill='x')
        ttk.Label(top, text='名称').pack(side='left')
        v_name = tk.StringVar(value=name)
        ttk.Entry(top, textvariable=v_name, width=40).pack(side='left', padx=6)
        ttk.Label(top, text='（空脚本 = 小队原地待命）',
                  foreground=ui_theme.T['muted']).pack(side='left', padx=6)

        body = ttk.Frame(win, padding=(8, 0))
        body.pack(fill='both', expand=True)
        left = ttk.Frame(body)
        left.pack(side='left', fill='both', expand=True)
        tv = ttk.Treeview(left, columns=('i', 'nm', 'arg'), show='headings', height=13)
        for c, t, w, a in (('i', '行', 40, 'e'), ('nm', '动作（地编名称）', 400, 'w'),
                           ('arg', '参数', 90, 'e')):
            tv.heading(c, text=t)
            tv.column(c, width=w, anchor=a)
        tv.pack(fill='both', expand=True)
        right = ttk.LabelFrame(body, text='行为描述', padding=6)
        right.pack(side='left', fill='both', padx=(8, 0))
        txt = tk.Text(right, width=42, height=14, wrap='word', state='disabled')
        txt.pack(fill='both', expand=True)

        def refill(sel=None):
            tv.delete(*tv.get_children())
            for i, (a, g) in enumerate(entries):
                tv.insert('', 'end', iid=str(i), values=(i + 1, _nm(a), g))
            if sel is not None and str(sel) in tv.get_children():
                tv.selection_set(str(sel))
                show_desc()

        def show_desc(*_a):
            sel = tv.selection()
            txt.config(state='normal')
            txt.delete('1.0', 'end')
            if sel:
                a = entries[int(sel[0])][0]
                txt.insert('end', (sa.action_desc(a) if sa else '') or '（地编没有这条的说明）')
            txt.config(state='disabled')

        tv.bind('<<TreeviewSelect>>', show_desc)

        cl = ttk.Frame(win, padding=8)
        cl.pack(fill='x')
        ttk.Label(cl, text='行为类型').pack(side='left')
        labels = (['%d - %s' % (i, _ft.script_action_name(i))
                   for i in sorted(_ft.SCRIPT_ACTIONS)] if _ft else [])
        v_act = tk.StringVar(value=(labels[5] if len(labels) > 5 else ''))
        cb = ttk.Combobox(cl, textvariable=v_act, values=labels, width=44)
        cb.pack(side='left', padx=4)
        ttk.Label(cl, text='参数/路径点').pack(side='left', padx=(10, 0))
        v_arg = tk.StringVar(value='0')
        ttk.Entry(cl, textvariable=v_arg, width=10).pack(side='left', padx=4)

        bar2 = ttk.Frame(win, padding=(8, 0))
        bar2.pack(fill='x')

        def act_id():
            txt = v_act.get().strip()
            try:
                return int(txt.split(' ')[0])
            except Exception:
                return 5

        def add():
            try:
                g = int(v_arg.get() or 0)
            except ValueError:
                g = 0
            if len(entries) >= 50:
                messagebox.showwarning('动作脚本', '最多 50 条（结构如此）', parent=win)
                return
            entries.append([act_id(), g])
            refill(len(entries) - 1)

        def dup():
            sel = tv.selection()
            if not sel:
                return
            i = int(sel[0])
            entries.insert(i + 1, list(entries[i]))
            refill(i + 1)

        def rm():
            sel = tv.selection()
            if not sel:
                return
            for i in sorted((int(x) for x in sel), reverse=True):
                entries.pop(i)
            refill()

        def mv(d):
            sel = tv.selection()
            if not sel:
                return
            i = int(sel[0])
            j = i + d
            if 0 <= j < len(entries):
                entries[i], entries[j] = entries[j], entries[i]
                refill(j)

        ttk.Button(bar2, text='添加', command=add).pack(side='left', padx=2)
        ttk.Button(bar2, text='复制', command=dup).pack(side='left', padx=2)
        ttk.Button(bar2, text='删除', command=rm).pack(side='left', padx=2)
        ttk.Button(bar2, text='上移', command=lambda: mv(-1)).pack(side='left', padx=2)
        ttk.Button(bar2, text='下移', command=lambda: mv(1)).pack(side='left', padx=2)
        res = {}

        def ok():
            # 新建时必须至少一条；★编辑已有的允许清空（= 把这段脚本改成空脚本）
            if not entries and not ident:
                messagebox.showwarning('动作脚本', '至少加一条动作（要"原地待命"就别建脚本）',
                                       parent=win)
                return
            res.update({'name': v_name.get().strip() or '新脚本',
                        'entries': [(int(a), int(g) & 0xFFFFFFFF) for a, g in entries],
                        'ident': ident})
            win.destroy()

        ttk.Button(bar2, text='确定', command=ok).pack(side='right', padx=4)
        ttk.Button(bar2, text='取消', command=win.destroy).pack(side='right')
        refill(0)
        self.wait_window(win)
        return res or None

    # ---- ★§258 作战小队（仿地编全字段）----
    # 地编截图里带 `*` 的复选框才有用（玩家 2026-10-25 原话），所以只列这 12 个。
    # 前 9 个偏移已在 `ai_objects.TEAM_FLAGS` 定案；后 3 个还缺对照档 ⇒ 灰显不可改。
    TEAM_FLAG_UI = (
        ('Loadable', '可装载*', True),
        ('Full', '预装载小队*', True),
        ('Droppod', '运输机空投*', True),
        ('Aggressive', '侵略部队*', True),
        ('Autocreate', '自动建造*', True),
        ('Reinforce', '增援部队*', True),
        ('AvoidThreats', '躲避威胁*', True),
        ('LooseRecruit', '忽视分组*', True),
        ('Whiner', '忽视阻拦*', True),
        ('AreTeamMembersRecruitable', '小队成员可被重组*', True),   # ★§260 定案 +0x109
        ('IsBaseDefense', '用于基地防御*', True),          # ★§261 定案 +0x10A
        ('OnlyTargetHouseEnemy', '仅攻击敌方或敌对*', True),   # ★§263 定案 +0x10B
    )
    # ★§265：取值与中文名**逐字照 FA2 地编「心控结果」下拉**（玩家 2026-10-25 订正）。
    #   旧版错填成「我方基地/敌方基地/最短路径/最富/其他」—— 那是 AI 任务目标列表，
    #   地编里没有这几项。真实语义：本小队心控单位控制的敌方单位，接下来干什么。
    TEAM_MC_CHOICES = [(v, '%d - %s' % (v, t)) for v, t in ai_objects.TEAM_MC_CHOICES]

    def _team_defs(self):
        """地图定义的作战小队 [(显示名, sid, key)]。"""
        out = []
        try:
            for t in self.doc.team_types():
                if t.get('defined', True):
                    out.append(('%s  %s' % (t['sid'], t['name']), t['sid'], t['key']))
        except Exception:
            pass
        return out

    def _dlg_team(self, ident=None, new=False):
        """「作战小队」对话框（仿地编，§258）——新建 / 复制 / 编辑全字段。

        返回 `{'saved': True, 'sid':…, 'key':…}` 或 None（取消）。
        ★地编里带 `*` 的复选框才有用，这里只放那 12 个；其中 3 个偏移未定案 ⇒ 灰显。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return None
        teams = self._team_defs()
        if not teams and not new:
            messagebox.showwarning('作战小队', '本档没有地图定义的作战小队')
            return None
        state = {'key': None}
        win = self._mk_modal('作战小队', 700, 700)

        top = ttk.Frame(win, padding=8)
        top.pack(fill='x')
        ttk.Label(top, text='作战小队连接到特遣部队和动作脚本，并可被触发调用。'
                            '（带 * 的标志是地编里真正生效的那些）',
                  foreground=ui_theme.T['ink_2']).pack(side='left')
        ttk.Button(top, text='查找引用', command=lambda: self._team_refs_dlg(state['key'])
                   ).pack(side='right')

        rowt = ttk.Frame(win, padding=(8, 0))
        rowt.pack(fill='x', pady=(4, 6))
        v_cur = tk.StringVar()
        cb_cur = ttk.Combobox(rowt, textvariable=v_cur, width=44, state='readonly',
                              values=[x[0] for x in teams])
        ttk.Label(rowt, text='当前小队').pack(side='left')
        cb_cur.pack(side='left', padx=6)

        g = ttk.Frame(win, padding=(8, 0))
        g.pack(fill='both', expand=True)

        def lab(i, j, text, w=None):
            ttk.Label(g, text=text).grid(row=i, column=j * 2, sticky='w', pady=3)

        v_name = tk.StringVar()
        v_house = tk.StringVar()
        v_tf = tk.StringVar()
        v_sc = tk.StringVar()
        v_tag = tk.StringVar()
        v_vet = tk.StringVar()
        v_pri = tk.StringVar()
        v_max = tk.StringVar()
        v_grp = tk.StringVar()
        v_wp = tk.StringVar()
        v_twp = tk.StringVar()          # ★§259 运输机起始（+0xEC，-1=未设置）
        v_mc = tk.StringVar()
        f_vars = {}

        lab(0, 0, '名称')
        ttk.Entry(g, textvariable=v_name, width=52).grid(
            row=0, column=1, columnspan=3, sticky='we', padx=6, pady=3)
        lab(1, 0, '国家')
        cb_house = ttk.Combobox(g, textvariable=v_house, width=50, state='readonly')
        cb_house.grid(row=1, column=1, columnspan=3, sticky='we', padx=6, pady=3)
        lab(2, 0, '特遣部队')
        cb_tf = ttk.Combobox(g, textvariable=v_tf, width=50, state='readonly')
        cb_tf.grid(row=2, column=1, columnspan=3, sticky='we', padx=6, pady=3)
        lab(3, 0, '动作脚本')
        cb_sc = ttk.Combobox(g, textvariable=v_sc, width=50, state='readonly')
        cb_sc.grid(row=3, column=1, columnspan=3, sticky='we', padx=6, pady=3)
        lab(4, 0, '关联标签')
        cb_tag = ttk.Combobox(g, textvariable=v_tag, width=50, state='readonly')
        cb_tag.grid(row=4, column=1, columnspan=3, sticky='we', padx=6, pady=3)

        lab(5, 0, '星级')
        ttk.Combobox(g, textvariable=v_vet, width=8, state='readonly',
                     values=['1', '2', '3']).grid(row=5, column=1, sticky='w', padx=6)
        lab(5, 2, '优先级')
        ttk.Spinbox(g, from_=0, to=99, width=8, textvariable=v_pri).grid(
            row=5, column=3, sticky='w', padx=6)
        lab(6, 0, '最大')
        ttk.Spinbox(g, from_=0, to=99, width=8, textvariable=v_max).grid(
            row=6, column=1, sticky='w', padx=6)
        lab(6, 2, '分组')
        ttk.Spinbox(g, from_=-1, to=99, width=8, textvariable=v_grp).grid(
            row=6, column=3, sticky='w', padx=6)
        lab(7, 0, '小队出现于（路径点号）')
        ttk.Entry(g, textvariable=v_wp, width=10).grid(row=7, column=1, sticky='w', padx=6)
        lab(7, 2, '心控结果')
        cb_mc = ttk.Combobox(g, textvariable=v_mc, width=22, state='readonly',
                             values=[x[1] for x in self.TEAM_MC_CHOICES])
        cb_mc.grid(row=7, column=3, sticky='w', padx=6)
        # ★§259：运输机起始（TransportWaypoint，`+0xEC`）。勾了「运输机空投」才有意义。
        lab(8, 0, '运输机起始（路径点号）')
        ttk.Entry(g, textvariable=v_twp, width=10).grid(row=8, column=1, sticky='w', padx=6)
        lbl_twp = ttk.Label(g, text='', foreground=ui_theme.T['muted'])
        lbl_twp.grid(row=8, column=2, columnspan=2, sticky='w', padx=6)
        ttk.Label(g, text='★' + ai_objects.TEAM_MC_NOTE,
                  foreground=ui_theme.T['info'], wraplength=640, justify='left').grid(
            row=9, column=0, columnspan=4, sticky='w', pady=(2, 0))
        ttk.Label(g, text='★「运输机起始」= 空投飞机**起飞**的路径点，只有勾了「运输机空投*」才有意义；'
                          '-1 = 未设置（地编显示 None），0 是合法路径点',
                  foreground=ui_theme.T['info'], wraplength=640, justify='left').grid(
            row=10, column=0, columnspan=4, sticky='w', pady=(2, 0))

        # ---- 复选框（只放带 * 的）----
        box = ttk.Labelframe(win, text='小队标志（地编里带 * 的才有用）', padding=8)
        box.pack(fill='x', padx=8, pady=(8, 0))
        fgrid = ttk.Frame(box)
        fgrid.pack()
        for i, (nm, txt, ok) in enumerate(self.TEAM_FLAG_UI):
            var = tk.BooleanVar(value=False)
            f_vars[nm] = var
            cb = ttk.Checkbutton(fgrid, text=txt, variable=var)
            cb.grid(row=i % 3, column=i // 3, sticky='w', padx=8, pady=2)
            if not ok:
                cb.state(['disabled'])
        ttk.Label(box, text='（灰掉的两个/三个：偏移还没定案 —— 需要一份"这些勾选有开有关"的存档来对账）',
                  foreground=ui_theme.T['muted_soft']).pack(anchor='w', pady=(4, 0))

        bar = ttk.Frame(win, padding=8)
        bar.pack(fill='x')

        # ---- 数据装载 ----
        def load(key):
            state['key'] = key
            det = self.doc.team_detail(key)
            v_name.set(det['name'])
            hs = [h.get('cn') or h.get('short') for h in (self.doc.houses() or [])]
            cb_house['values'] = hs
            hk = det['house']
            nmh = next((h.get('cn') or h.get('short') for h in (self.doc.houses() or [])
                        if h.get('key') == hk), None)
            v_house.set(nmh or (hs[0] if hs else ''))
            tfs = [('%s  %s' % (t['sid'], t['name']), t['key'])
                   for t in self.doc.taskforces() if t.get('defined', True)]
            cb_tf['values'] = [x[0] for x in tfs]
            v_tf.set(next((x[0] for x in tfs if x[1] == det['taskforce']),
                          tfs[0][0] if tfs else ''))
            scs = [('（空脚本 · 原地待命）', 0)] + \
                  [('%s  %s' % (x['sid'], x['name']), x['key'])
                   for x in self.doc.script_types() if x.get('defined', True)]
            cb_sc['values'] = [x[0] for x in scs]
            v_sc.set(next((x[0] for x in scs if x[1] == det['script']), scs[0][0]))
            tgs = [('None', 0)] + \
                  [('%s  %s' % (x.get('sid'), x.get('name')), x.get('key'))
                   for x in (__import__('ai_objects').list_tags(self.doc) or [])]
            cb_tag['values'] = [x[0] for x in tgs]
            v_tag.set(next((x[0] for x in tgs if x[1] == det['tag']), 'None'))
            v_vet.set(str(det['veteran'] or 1))
            v_pri.set(str(det['priority']))
            v_max.set(str(det['max']))
            v_grp.set(str(det['group']))
            v_wp.set(str(det['waypoint']))
            v_twp.set(str(det.get('transport_waypoint', -1)))
            try:
                _tw = int(det.get('transport_waypoint', -1))
                if _tw < 0:
                    lbl_twp.config(text='（未设置）')
                else:
                    _c = self.doc.waypoint_cell(_tw)
                    lbl_twp.config(text=('格 %s' % (_c,)) if _c else '')
            except Exception:
                lbl_twp.config(text='')
            v_mc.set(next((x[1] for x in self.TEAM_MC_CHOICES
                           if x[0] == det['mind_control']), self.TEAM_MC_CHOICES[0][1]))
            for nm, var in f_vars.items():
                var.set(bool(det['flags'].get(nm)))
            cb_cur.set(next((x[0] for x in teams if x[2] == key),
                            cb_cur.get()))
            self.status.set('作战小队 %s「%s」' % (det['sid'], det['name']))

        def on_pick(*_a):
            txt = v_cur.get()
            hit = next((x for x in teams if x[0] == txt), None)
            if hit:
                load(hit[2])

        cb_cur.bind('<<ComboboxSelected>>', on_pick)

        def refresh_cur():
            nonlocal teams
            teams = self._team_defs()
            cb_cur['values'] = [x[0] for x in teams]

        def do_new():
            tfs = [t for t in self.doc.taskforces() if t.get('defined', True)]
            if not tfs:
                messagebox.showwarning('新建小队', '本档没有特遣部队，请先「＋ 特遣部队」',
                                       parent=win)
                return
            n = 1
            names = {t['name'] for t in self.doc.team_types()}
            while ('新小队%d' % n) in names:
                n += 1
            try:
                r = self.doc.clone_team(tfs[0]['key'], name='新小队%d' % n,
                                        veteran=1, max_teams=5, script_key=0)
            except Exception as e:
                messagebox.showerror('新建失败', str(e), parent=win)
                return
            refresh_cur()
            load(r['key'])

        def do_copy():
            if not state['key']:
                return
            src = self.doc.team_detail(state['key'])
            try:
                r = self.doc.clone_team(src['taskforce'], name=(src['name'] + '_copy')[:16],
                                        country_key=src['house'] or None,
                                        veteran=src['veteran'], max_teams=src['max'],
                                        script_key=src['script'], priority=src['priority'])
            except Exception as e:
                messagebox.showerror('复制失败', str(e), parent=win)
                return
            refresh_cur()
            load(r['key'])

        def do_del():
            messagebox.showinfo(
                '暂不支持删除',
                '删除作战小队要同时拆掉「触发 80/107 对它的引用」并重排组内序号，\n'
                '风险高，本版先不做。想让它失效可以直接把「星级」调 1、或把特遣清空。',
                parent=win)

        ttk.Button(rowt, text='新建', width=6, command=do_new).pack(side='left', padx=3)
        ttk.Button(rowt, text='复制', width=6, command=do_copy).pack(side='left', padx=3)
        ttk.Button(rowt, text='删除', width=6, command=do_del).pack(side='left', padx=3)
        for c in range(4):
            g.columnconfigure(c, weight=1 if c % 2 else 0)

        res = {}

        def ok():
            key = state['key']
            if not key:
                win.destroy()
                return
            try:
                tfk = next((x[1] for x in
                            [('%s  %s' % (t['sid'], t['name']), t['key'])
                             for t in self.doc.taskforces() if t.get('defined', True)]
                            if x[0] == v_tf.get()), None)
                sck = next((x[1] for x in
                            [('（空脚本 · 原地待命）', 0)] +
                            [('%s  %s' % (x['sid'], x['name']), x['key'])
                             for x in self.doc.script_types() if x.get('defined', True)]
                            if x[0] == v_sc.get()), 0)
                tgk = next((x[1] for x in
                            [('None', 0)] +
                            [('%s  %s' % (x.get('sid'), x.get('name')), x.get('key'))
                             for x in (__import__('ai_objects').list_tags(self.doc) or [])]
                            if x[0] == v_tag.get()), 0)
                hk = self.doc.house_key_of(v_house.get()) or 0
                fields = [('name', v_name.get().strip()[:16]),
                          ('house', hk), ('taskforce', tfk or 0),
                          ('script', sck or 0), ('tag', tgk or 0),
                          ('veteran', int(v_vet.get() or 1)),
                          ('priority', int(v_pri.get() or 0)),
                          ('max', int(v_max.get() or 0)),
                          ('group', int(v_grp.get() or -1)),
                          ('waypoint', int(v_wp.get() or 0)),
                          ('transport_waypoint',
                           int(v_twp.get() or 0) if v_twp.get().strip() else -1),
                          ('mind_control', next((x[0] for x in self.TEAM_MC_CHOICES
                                                 if x[1] == v_mc.get()), 0))]
                for nm, var in f_vars.items():
                    if dict((a, c) for a, _b, c in self.TEAM_FLAG_UI)[nm]:
                        fields.append((nm, bool(var.get())))
                for f, val in fields:
                    self.doc.set_team_field(key, f, val)
            except Exception as e:
                messagebox.showerror('保存失败', str(e), parent=win)
                return
            self._lists_dirty = True      # ★这里 self 是 App 本身（不是 TriggerEditor）
            res.update({'saved': True, 'key': key})
            win.destroy()

        ttk.Button(bar, text='确定', width=8, command=ok).pack(side='right', padx=4)
        ttk.Button(bar, text='取消', width=8, command=win.destroy).pack(side='right')

        # 初始选中
        init = ident
        if init is None and teams:
            init = teams[0][2]
        if init:
            try:
                load(init)
            except Exception as e:
                self.status.set('装载失败：%s' % e)
        self.wait_window(win)
        if res.get('saved'):
            self._fill_ai_all()
        return res or None

    def _team_refs_dlg(self, key):
        """「查找引用」：列出用行为 80/107 引用这支小队的触发。"""
        if not key:
            messagebox.showinfo('查找引用', '先选一支小队', parent=self)
            return
        try:
            refs = self.doc.team_refs(key)
        except Exception as e:
            messagebox.showerror('查找引用失败', str(e), parent=self)
            return
        if not refs:
            messagebox.showinfo('查找引用', '没有任何触发引用这支小队', parent=self)
            return
        lines = ['%s  %s  行为%d 路径点%s%s'
                 % (r['trigger_id'], r['name'], r['action'], r['waypoint'],
                    (' 格%s' % (r['cell'],)) if r.get('cell') else '')
                 for r in refs[:20]]
        messagebox.showinfo('查找引用（%d 条）' % len(refs),
                            '\n'.join(lines) + ('\n…' if len(refs) > 20 else ''),
                            parent=self)

    def _ai_new_tf(self):
        """按钮入口：新建一个特遣部队（只建特遣，不动小队/触发）。"""
        self._ai_tf_dialog()

    def _ai_tf_dialog(self, ident=None):
        """「特遣部队」对话框：`ident=None` 新建；给了 ident 就是**改已有的那一支**（§264 双击进来）。

        ★改已有用的是 `ai_objects.set_taskforce_units()`：一次读档、一次写完
          （`set_taskforce_member` 每调一次都要全档扫，改 6 项太慢）。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        name, units = '新特遣部队', []
        if ident:
            tf = next((x for x in self.doc.taskforces() if x['sid'] == str(ident)), None)
            if tf is None:
                messagebox.showwarning('编辑特遣部队', '找不到特遣部队 %s' % ident)
                return
            name = tf['name']
            units = [(int(u['count']), u['type']) for u in (tf.get('units') or [])]
        v = self._dlg_tf(name=name, units=units, ident=ident)
        if not v:
            return
        try:
            if v.get('ident'):
                import ai_objects as _aio
                res = _aio.set_taskforce_units(self.doc, v['ident'], v['units'])
                if v['name'] and v['name'] != name:
                    _aio.set_ai_name(self.doc, 'taskforce', v['ident'], v['name'])
                sid, nm = v['ident'], (v['name'] or name)
            else:
                r = self.doc.clone_taskforce(v['units'], name=v['name'])
                sid, nm = r['sid'], r['name']
        except Exception as e:
            messagebox.showerror('保存特遣部队失败', str(e))
            return
        self._lists_dirty = True
        self.refresh_ai()
        self.status.set('%s特遣部队 %s「%s」（尚未保存）'
                        % ('已修改' if v.get('ident') else '已新建', sid, nm))
        if not v.get('ident'):
            messagebox.showinfo('新建特遣部队',
                                '已新建特遣部队\n\nID：%s\n名称：%s\n成员：%s\n\n'
                                '下一步：在「✏ 作战小队…」里选它。'
                                % (sid, nm,
                                   '、'.join('%s×%d' % (t, n) for t, n in v['units'])))

    def _ai_new_script(self):
        """按钮入口：新建一段动作脚本（ScriptType）。"""
        self._ai_script_dialog()

    def _ai_script_dialog(self, ident=None):
        """「动作脚本」对话框：`ident=None` 新建；给了 ident 就是**改已有的那一段**（§264 双击进来）。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        name, entries = '新脚本', []
        if ident:
            sc = next((x for x in self.doc.script_types() if x['sid'] == str(ident)), None)
            if sc is None:
                messagebox.showwarning('编辑动作脚本', '找不到动作脚本 %s' % ident)
                return
            name = sc['name']
            entries = [(int(e['action']), int(e['arg'])) for e in (sc.get('entries') or [])]
        v = self._dlg_script(name=name, entries=entries, ident=ident)
        if not v:
            return
        try:
            import fa2_tables as _ft
        except Exception:
            _ft = None
        try:
            if v.get('ident'):
                import ai_objects as _aio
                res = _aio.set_script_entries(self.doc, v['ident'], v['entries'])
                if v['name'] and v['name'] != name:
                    _aio.set_ai_name(self.doc, 'script', v['ident'], v['name'])
                sid, nm, cnt = v['ident'], (v['name'] or name), res['new_n']
            else:
                r = self.doc.clone_script(v['entries'], name=v['name'])
                sid, nm, cnt = r['sid'], r['name'], r['count']
        except Exception as e:
            messagebox.showerror('保存动作脚本失败', str(e))
            return
        self._lists_dirty = True
        self.refresh_ai()
        self.status.set('%s动作脚本 %s「%s」%d 条（尚未保存）'
                        % ('已修改' if v.get('ident') else '已新建', sid, nm, cnt))
        if not v.get('ident'):
            messagebox.showinfo('新建动作脚本',
                                '已新建动作脚本\n\nID：%s\n名称：%s\n条数：%d\n%s'
                                % (sid, nm, cnt,
                                   '、'.join('%s(%s)' % ((_ft.script_action_name(a) if _ft else a), g)
                                            for a, g in v['entries'])))

    def _ai_tf_dblclick(self, event):
        """双击「特遣部队」表格的一行 ⇒ 改这一支（§264）。"""
        tv = event.widget
        try:
            iid = tv.identify_row(event.y)
        except Exception:
            iid = ''
        if not iid:
            return
        try:
            tv.selection_set(iid)
            tv.focus(iid)
        except Exception:
            pass
        self._ai_tf_dialog(iid)

    def _ai_script_dblclick(self, event):
        """双击「动作脚本」表格的一行 ⇒ 改这一段（§264）。"""
        tv = event.widget
        try:
            iid = tv.identify_row(event.y)
        except Exception:
            iid = ''
        if not iid:
            return
        try:
            tv.selection_set(iid)
            tv.focus(iid)
        except Exception:
            pass
        self._ai_script_dialog(iid)

    def _ai_team_dblclick(self, event):
        """双击「作战小队」表格里的一行 ⇒ 打开「作战小队」对话框编辑这一支（★§264）。

        玩家 2026-10-25：这个界面里点一行只能看明细，要改还得走别的入口
        ⇒ 双击就该能直接改。

        ★用 `identify_row(event.y)` 取被双击的那一行，**不用** `selection()`：
          双击时"选中变化"和"双击"两个事件的先后顺序不保证，按坐标定位才稳。
        ★双击表头 / 空白区（`identify_row` 返回空串）直接忽略。
        """
        tv = event.widget
        try:
            iid = tv.identify_row(event.y)
        except Exception:
            iid = ''
        if not iid:
            return
        try:
            tv.selection_set(iid)
            tv.focus(iid)
        except Exception:
            pass
        # 先把明细/下拉同步到这一支（对话框的初始状态要用）
        self._ai_syncing = False
        try:
            self._ai_pick('team')
        except Exception:
            pass
        try:
            r = self._dlg_team(ident=iid)
        except Exception as e:
            messagebox.showerror('打开失败', str(e))
            return
        if r and r.get('saved'):
            try:
                self._ai_select(self.tat, iid)
                self._ai_pick('team')
            except Exception:
                pass
            self.status.set('已修改小队 %s（尚未保存，回主窗口「保存」才写盘）' % iid)

    def _ai_new_team(self):
        """打开「作战小队」对话框（仿地编：新建 / 复制 / 编辑全字段，§258）。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        r = self._dlg_team()
        if r:
            self._fill_ai_all()
            self.status.set('作战小队已保存到内存（回主窗口「保存」才写盘）')

    def _ai_attach_team(self):
        """把一支作战小队挂到**已有触发器**上（行为 80 援军 / 107 超时空援军）。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        try:
            teams = self.doc.team_types()
        except Exception as e:
            messagebox.showerror('读取失败', str(e))
            return
        disp = ['%s  %s' % (t['sid'], t['name']) for t in teams]
        v = self._dlg_form('触发挂载作战小队', [
            ('触发ID', 'tid', getattr(self, '_cur', '') or '', 'text'),
            ('作战小队', 'team', disp[0] if disp else '', 'combo', disp),
            ('路径点号（留空则用下面的格子）', 'wp', '', 'text'),
            ('或格子 x,y（如 110,206）', 'cell', '', 'text')],
            '写的是行为「80 援军」：小队键 → +0x44、路径点 → +0x58（与刷兵同一条路）。\n'
            '若该触发器已经有 80/107 行为，会**改写那一条**，不会重复加。')
        if not v:
            return
        tid = str(v['tid']).strip()
        if not tid:
            messagebox.showwarning('提示', '要填触发 ID（在触发器页选中一条就能拿到）')
            return
        try:
            team = str(v['team']).split()[0] if str(v['team']).strip() else ''
            wp = int(str(v['wp']).strip()) if str(v['wp']).strip() else None
            cell = None
            if wp is None:
                s = str(v['cell']).replace('，', ',').strip()
                if not s:
                    messagebox.showwarning('提示', '路径点号和格子至少要填一个')
                    return
                a, b = s.split(',')
                cell = (int(a), int(b))
            r = self.doc.attach_team_to_trigger(tid, team, waypoint=wp, cell=cell)
        except Exception as ex:
            messagebox.showerror('挂载失败', str(ex))
            return
        self._fill_ai_all()
        try:
            self._fill_trig_tree()
        except Exception:
            pass
        self.status.set('已把小队挂到触发 %s' % tid)
        messagebox.showinfo('触发挂载作战小队',
                            '%s\n\n触发器：%s\n小队键：%#x\n路径点：%d\n行为：%d（%s）'
                            % ('已改写原来那条 80/107 行为' if r['mode'] == 'reuse'
                               else '已追加一条行为',
                               tid, r['team_key'], r['waypoint'], r['action'],
                               '援军' if r['action'] == 80 else '超时空援军'))

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
        """路径点号的显示文本：`0 (54,86)`。

        ★§273：先查 `self._wp_cache`（一次 `waypoints_save()` 取全表），**别逐支小队调
          `doc.waypoint_cell()`** —— 那会每次都全档 find 定位路径点表（26MB × 124 次 = 2.4 秒）。
        """
        if n is None or n < 0:
            return '—'
        c = None
        cache = getattr(self, '_wp_cache', None)
        if isinstance(cache, dict) and cache:
            c = cache.get(int(n))
        else:
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

    def _ai_script_values(self, cur=None):
        """动作脚本下拉的候选列表（★§262）。

        玩家 2026-10-25：那个「改脚本」下拉里全是 `0C9C878C` 这种 **AI 运行期生成**的脚本，
        「这个没用，只显示玩家自己的脚本即可」。判据与 §254 一致：
        **地图定义** = `0100xxxx`（`ai_objects` 的 `is_map`），AI 生成 = 8 位随机十六进制。

        ★当前值若恰好是 AI 脚本，**临时补进列表**：否则下拉会显示成「（空）」，
        等于把真实值藏起来 —— 那比显示它更糟（用户会以为这支小队没脚本）。
        """
        try:
            ids = sorted({x['id'] for x in (self._ai or {}).get('scripts', [])
                          if x.get('is_map')})
        except Exception:
            ids = []
        vals = ['（空）'] + ids
        if cur and cur not in vals:
            vals.append(cur)
        return vals

    def _ai_apply_script(self):
        """改选中小队挂的**动作脚本**（+0xF4；选「（空）」= 清空，§226）。"""
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
        txt = (self.ai_script.get() or '').strip()
        if not txt:
            messagebox.showwarning('提示', '请先选一个脚本（或「（空）」）')
            return
        want = None if txt == '（空）' else txt.split(' ')[0]
        cur = t.get('script_id') or '（空）'
        if (want or '（空）') == cur:
            self.status.set('没变化：小队 %s 的脚本就是 %s' % (sid, cur))
            return
        if not messagebox.askyesno(
                '改小队动作脚本',
                '小队 %s「%s」\n\n动作脚本 %s → %s\n\n'
                '只写 +0xF4（脚本对象键），长度不变。\n'
                '★清空（写 0）= 与地编「脚本留空」一致：引擎按无脚本小队处理，原地待命。\n'
                '⚠ 已刷出的小队可能仍用旧脚本 ⇒ 建议新开一局验证。'
                % (sid, t['name'], cur, want or '（空）')):
            return
        try:
            info = ai_objects.set_team_script(self.doc, sid, want)
        except Exception as e:
            messagebox.showerror('改不了', str(e))
            return
        self.refresh_ai()
        self._ai_select(self.tat, sid)
        self._ai_pick('team')
        self.status.set('已改小队 %s「%s」动作脚本 %s → %s（尚未保存）'
                        % (sid, info['name'], info['old'], info['new']))

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

    def _ai_tag_dialog(self):
        """§247：给选中的作战小队挂/换「关联标签」（小队记录 `+0xE4`）。

        依据：T85 小队 01000241「a飞碟转圈A」的关联标签 01000251 就在 `+0x0E4`，
        存的是**标签对象(TagType)的键**（与单位记录 `+0x48` 存 CellTag 指针 X 不同）。
        只改 4 字节，记录长度不变。
        """
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
        try:
            tags = ai_objects.list_tags(self.doc)
        except Exception as e:                       # noqa: BLE001
            messagebox.showerror('关联标签', '读不到本档标签对象：%s' % e, parent=self)
            return
        if not tags:
            messagebox.showwarning('关联标签', '这份存档里一个标签对象都没有', parent=self)
            return
        win = tk.Toplevel(self)
        win.title('关联标签 — %s「%s」' % (sid, t['name']))
        win.transient(self)
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill='both', expand=True)
        cur = t.get('tag_id') or '（无）'
        ttk.Label(frm, text='小队 %s「%s」当前关联标签：%s'
                          % (sid, t['name'], cur)).grid(row=0, column=0, columnspan=2,
                                                        sticky='w', pady=(0, 6))
        ttk.Label(frm, text='挂上后，挂在该标签上的触发就会把这支小队当作事件对象'
                            '（如「小队被攻击 → 全部寻敌」）。',
                  foreground=ui_theme.T['muted']).grid(row=1, column=0, columnspan=2,
                                             sticky='w', pady=(0, 8))
        opts = ['（无）'] + ['%s → 触发 %s' % (x['id'], x['trigger_id'] or '?') for x in tags]
        ids = [None] + [x['id'] for x in tags]
        var = tk.StringVar(value=cur if cur in ids else '（无）')
        ttk.Combobox(frm, textvariable=var, values=opts, width=40,
                     state='readonly').grid(row=2, column=0, columnspan=2,
                                            sticky='w', pady=(0, 10))

        def apply():
            pick = var.get()
            want = None if pick == '（无）' else pick.split(' → ')[0].strip()
            try:
                info = ai_objects.set_team_tag(self.doc, sid, want)
            except Exception as e:                   # noqa: BLE001
                messagebox.showerror('关联标签', str(e), parent=win)
                return
            if not info.get('changed'):
                self.status.set('小队 %s：关联标签没有变化' % sid)
            else:
                self.status.set('已改小队 %s「%s」关联标签 → %s（+0xE4，尚未保存）'
                                % (sid, t['name'], info['tag_id'] or '（无）'))
            win.destroy()
            self.refresh_ai()
            self._ai_select(self.tat, sid)
            self._ai_pick('team')

        bar = ttk.Frame(frm)
        bar.grid(row=3, column=0, columnspan=2, sticky='e')
        ttk.Button(bar, text='取消', command=win.destroy).pack(side='right', padx=4)
        ttk.Button(bar, text='应用', command=apply).pack(side='right')

    def _ai_flags_dialog(self):
        """§235：小队 16 个布尔标志 + 「心控结果」一屏编辑。

        依据：引擎写盘函数 `0x6F10E0–0x6F13C0`（键 ↔ 偏移）+ ALL01UMD.MAP 125 条小队
        对账 125/125。**每个标志是 1 个字节**（不是位域），心控结果是 `+0xD4` 的 dword。
        取值 = FA2 地编「心控结果」下拉 6 档（§265）。
        只改勾选项：先比对、只写有变化的那些字节。
        """
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
        win = tk.Toplevel(self)
        win.title('小队标志 — %s「%s」' % (sid, t['name']))
        win.transient(self)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill='both', expand=True)
        ttk.Label(frm, text='勾选即写入（每个标志只占 1 个字节，记录长度不变）',
                  foreground=ui_theme.T['muted']).grid(row=0, column=0, columnspan=4,
                                             sticky='w', pady=(0, 8))
        vars_ = {}
        order = ['Full', 'Loadable', 'Reinforce', 'Prebuild', 'Autocreate', 'Recruiter',
                 'GuardSlower', 'Annoyance', 'Aggressive', 'Suicide', 'Droppod',
                 'UseTransportOrigin', 'OnTransOnly', 'AvoidThreats', 'LooseRecruit',
                 'Whiner']
        for i, name in enumerate(order):
            v = tk.BooleanVar(value=bool(t['flags'].get(name)))
            vars_[name] = v
            ttk.Checkbutton(frm, text=ai_objects.TEAM_FLAG_LABELS.get(name, name),
                            variable=v).grid(row=1 + i // 4, column=i % 4,
                                             sticky='w', padx=6, pady=2)
        r0 = 1 + (len(order) + 3) // 4
        ttk.Separator(frm, orient='horizontal').grid(
            row=r0, column=0, columnspan=4, sticky='ew', pady=8)
        ttk.Label(frm, text='心控结果（MindControlDecision）：').grid(
            row=r0 + 1, column=0, columnspan=2, sticky='w')
        # ★§265：改成下拉（地编同名 6 档），不再让人手打数字
        _mc0 = next((x[1] for x in self.TEAM_MC_CHOICES
                     if x[0] == t.get('mc_decision', 0)), self.TEAM_MC_CHOICES[0][1])
        mc = tk.StringVar(value=_mc0)
        ttk.Combobox(frm, textvariable=mc, width=20, state='readonly',
                     values=[x[1] for x in self.TEAM_MC_CHOICES]).grid(
            row=r0 + 1, column=2, columnspan=2, sticky='w')
        ttk.Label(frm, text='= 本小队心控单位控制住的敌方单位接下来做什么（+0xD4，4 字节）',
                  foreground=ui_theme.T['muted']).grid(row=r0 + 2, column=0, columnspan=4,
                                             sticky='w', pady=(2, 0))

        def apply():
            changed, failed = [], []
            for name, v in vars_.items():
                want = bool(v.get())
                if want == bool(t['flags'].get(name)):
                    continue
                try:
                    ai_objects.set_team_flag(self.doc, sid, name, want)
                    changed.append('%s=%s' % (ai_objects.TEAM_FLAG_LABELS.get(name, name),
                                              '是' if want else '否'))
                except Exception as e:
                    failed.append('%s：%s' % (name, e))
            mv = next((x[0] for x in self.TEAM_MC_CHOICES if x[1] == mc.get()), None)
            if mv is not None and mv != int(t.get('mc_decision', 0)):
                try:
                    ai_objects.set_team_mc_decision(self.doc, sid, mv)
                    changed.append('心控结果=%d（%s）'
                                   % (mv, ai_objects.TEAM_MC_LABELS.get(mv, '')))
                except Exception as e:
                    failed.append('心控结果：%s' % e)
            if failed:
                messagebox.showerror('部分没改成', '\n'.join(failed), parent=win)
            if not changed:
                self.status.set('小队 %s：没有改动' % sid)
            else:
                self.status.set('已改小队 %s「%s」：%s（尚未保存）'
                                % (sid, t['name'], '，'.join(changed)))
            win.destroy()
            self.refresh_ai()
            self._ai_select(self.tat, sid)
            self._ai_pick('team')

        bar = ttk.Frame(frm)
        bar.grid(row=r0 + 2, column=0, columnspan=4, sticky='e', pady=(10, 0))
        ttk.Button(bar, text='取消', command=win.destroy).pack(side='right', padx=4)
        ttk.Button(bar, text='应用', command=apply).pack(side='right')

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
