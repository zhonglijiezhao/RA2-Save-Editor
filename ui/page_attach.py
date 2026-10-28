# -*- coding: utf-8 -*-
"""挂接页（原 `App.attach`）

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

from tkinter import messagebox
from tkinter import ttk
import ui_theme


class AttachPage(object):
    def _tab_attach(self, nb):
        f = ttk.Frame(nb, padding=8)
        _ph = ui_theme.page_header(f, '挂接', '触发 ↔ 对象挂接统计')
        _ph.pack(fill='x', padx=2, pady=(2, 8))
        _note, self._note_btn_attach = ui_theme.note_box(f, _ph)
        self._note_box_attach = _note          # 折叠说明的容器（默认不 pack）
        ttk.Label(_note, text='触发 ↔ 建筑挂接（2026-10-14 五轮实验定案，游戏内实测通过）。'
                          '★③ 可挂建筑**以存档为准**（挂接键为 0 的建筑），不需要地图；'
                          '地图只用来补"地图建筑ID"（①② 解建筑类型仍靠地图）。',
                  foreground=ui_theme.T['ink_2'], wraplength=900, justify='left').pack(anchor='w')
        # 上：挂接概览（含**未挂接**的标签 —— 新建的触发挂接数为 0 时也能在这里选中挂建筑）
        ttk.Label(f, text='① 存档里的挂接标签（选中一行查看/操作其建筑；挂接数 0 的是可挂上去的新触发）。'
                          '★列表末尾还会列出「有标签、但没有 CellTag 节点」的触发 —— '
                          '一次性触发执行完引擎会把节点销毁，它们就只剩标签（这些行不能挂/解，只看）',
                  foreground=ui_theme.T['muted'], wraplength=900, justify='left').pack(anchor='w', pady=(6, 2))
        # ★§208（玩家要求）：① 加搜索框 —— 大档 130+ 条触发，按 ID / 名字 / 标签ID 过滤
        _sf = ttk.Frame(f)
        _sf.pack(fill='x', pady=(0, 2))
        ttk.Label(_sf, text='搜索①（触发ID / 名字 / 标签ID）：').pack(side='left')
        self.ent_attach_trig = ttk.Entry(_sf, width=26)
        self.ent_attach_trig.pack(side='left', padx=4)
        self.ent_attach_trig.bind('<KeyRelease>', lambda e: self._attach_fill_trig())
        ttk.Label(_sf, text='（空格可分词，多个词全中才显示）',
                  foreground=ui_theme.T['muted']).pack(side='left')
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
                  foreground=ui_theme.T['muted']).pack(anchor='w', pady=(6, 2))
        cols = ('kind', 'typ', 'pos', 'hp', 'mid', 'off')
        self.tb = ttk.Treeview(f, columns=cols, show='headings', height=5)
        for c, t, w in (('kind', '种类', 60), ('typ', '建筑/单位类型', 210), ('pos', '格坐标', 100),
                        ('hp', '耐久', 70), ('mid', '地图建筑ID', 100), ('off', '字段偏移', 110)):
            self.tb.heading(c, text=t)
            self.tb.column(c, width=w, anchor='w')
        self.tb.pack(fill='both', expand=False)
        # 下：可挂建筑（★以存档为准：挂接键为 0 的建筑）
        rowf = ttk.Frame(f)
        rowf.pack(fill='x', pady=(8, 2))
        ttk.Label(rowf, text='③ 可挂建筑（**存档里挂接键为 0 的建筑**；地图只用来补编号；类型过滤：').pack(side='left')
        self.ent_attach_filter = ttk.Entry(rowf, width=14)
        self.ent_attach_filter.pack(side='left', padx=4)
        self.ent_attach_filter.bind('<KeyRelease>', lambda e: self._attach_fill_free())
        ttk.Label(rowf, text='）').pack(side='left')
        cols = ('mid', 'typ', 'pos', 'mtag')
        self.tc = ttk.Treeview(f, columns=cols, show='headings', height=6)
        for c, t, w in (('mid', '地图建筑ID', 100), ('typ', '建筑类型', 240),
                        ('pos', '格坐标', 110), ('mtag', '地图记的挂接（忽略）', 150)):
            self.tc.heading(c, text=t)
            self.tc.column(c, width=w, anchor='w')
        self.tc.pack(fill='both', expand=False)
        # 按钮
        row = ttk.Frame(f)
        row.pack(fill='x', pady=(8, 0))
        ttk.Button(row, text='扫描挂接', command=self._attach_scan).pack(side='left')
        ttk.Button(row, text='把①选中触发 挂到 ③选中建筑',
                   command=self.attach_do).pack(side='left', padx=6)
        ttk.Button(row, text='🧩 补建挂接节点（无节点的触发）',
                   command=self.attach_build_node).pack(side='left', padx=6)
        ttk.Button(row, text='解除 ②选中建筑 的挂接',
                   command=self.detach_do).pack(side='left', padx=6)
        ttk.Label(f, text='原理：挂上 = 目标建筑的 tag 字段 ← 该 CellTag 的自键 + CellTag 计数+1'
                          '（两侧缺一不可，游戏内实测：双电厂 / 跨类型地堡 / 新写触发）。'
                          '解除 = 字段清 0 + 计数−1（只剩 1 栋也能解；解完这条触发没有启动条件，节点保留、可随时挂到别的建筑上）。\n'
                          '流程：用「触发」页的「✏ 触发编辑器…」造触发（窗口里点「新建」进入新建模式），'
                          '或在这里把任意一个挂接标签挂到更多建筑；① 里挂接数为 0 的行就是还没挂出去的新触发。\n'
                          '★口径（2026-10-23 §202）：③ 按**存档**的挂接键判断"是否已挂"——'
                          '地图 [Structures] 的 Tag 列常常与存档不一致（实测 all01umd.map 里 '
                          'YACOMD 记着 01000438，存档里却是 0），以存档为准；'
                          '挂接直接写建筑记录里的字段（不再依赖坐标反查）。\n'
                          '改完请「另存为新文件」或「原地保存」。',
                  foreground=ui_theme.T['ink_2'], wraplength=900, justify='left').pack(anchor='w', pady=(6, 0))
        return f

    # ---------------- 挂接 ----------------
    # ★2026-10-23 §205（玩家当场纠错）：这张表以前有几条是**猜的**（`YACOMD` 写成"尤里建造场?"）。
    #   现规则：**只收确信无疑的**；表里没有的类型一律回落到存档自带显示名（`type_display()`，
    #   引擎解析过的真名，如 `Yuri Command Center`），再不行才显示代码 —— 宁可显示英文，也不猜中文。
    TYPE_CN = {
        # ---- 盟军 ----
        'GAPOWR': '盟军发电厂', 'GAPILE': '盟军兵营', 'GAPILL': '机枪碉堡',
        'GAREFN': '盟军矿石精炼厂', 'GACNST': '盟军建造场', 'GAWEAP': '盟军战车工厂',
        'GADEPT': '盟军维修厂', 'GAAIRC': '盟军空军指挥部', 'GAYARD': '盟军造船厂',
        'GATECH': '盟军作战实验室', 'GASPYSAT': '间谍卫星', 'GAGAP': '裂缝产生器',
        'GACSPH': '超时空传送仪', 'GAWEAT': '天气控制器', 'GAROBO': '机器人控制中心',
        'ATESLA': '光棱塔', 'GTGCAN': '法国巨炮',
        # ---- 苏军 ----
        'NAPOWR': '苏军发电厂', 'NAHAND': '苏军兵营', 'NAPILL': '哨戒炮',
        'NAREFN': '苏军矿石精炼厂', 'NACNST': '苏军建造场', 'NAWEAP': '苏军战车工厂',
        'NATECH': '苏军作战实验室', 'TESLA': '磁暴线圈', 'NAIRON': '铁幕装置',
        'NAMISL': '核弹发射井', 'NAPSYB': '心灵信标', 'NACLON': '复制中心',
        # ---- 尤里 ----
        'YACNST': '尤里建造场', 'YAPOWR': '生化反应炉', 'YABRCK': '尤里兵营',
        'YAREFN': '尤里矿石精炼厂', 'YAWEAP': '尤里战车工厂', 'YATECH': '尤里作战实验室',
        'YACOMD': '尤里指挥中心', 'YAPSYT': '尤里雕像', 'YAGRND': '粉碎机',
        # ---- 常见单位 ----
        'DISK': '飞碟', 'TANY': '谭雅', 'BORIS': '鲍里斯', 'ZEP': '基洛夫空艇',
    }

    def _type_disp(self, t):
        """类型代码 → 中文名；表里没有就用**存档自带的显示名**；再没有才退回代码。"""
        if not t:
            return '?'
        cn = self.TYPE_CN.get(str(t).upper())
        if cn:
            return cn
        try:
            if self.doc:
                nm = self.doc.type_display(t)
                if nm:
                    return nm
        except Exception:
            pass
        return t

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
        self._attach_docid = id(self.doc)
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
            # ★§207：挂到/解除/补建之后，各处理函数都会把 `_attach_scanned` 置 False 再调本函数
            #   —— 旧行为是退化成"（未扫描）"，整页内容被清空（玩家得自己再点「扫描挂接」）。
            #   改成：**同一份存档扫过就直接重扫**，页面内容保持连续。
            if (getattr(self, '_attach_ov', None)
                    and getattr(self, '_attach_docid', None) == id(self.doc)):
                return self._attach_scan()
            self.tma.insert('', 'end', values=('（未扫描）', '', '', '点「扫描挂接」开始'))
            self._attach_free = []
            return
        has_map = bool(getattr(self.doc, '_map', None))
        self._attach_rows = []          # ★§208：① 的行先收集，再由 _attach_fill_trig() 按搜索词落地
        self._attach_ov = self.doc.attach_overview()
        for it in self._attach_ov:
            blds = it['buildings']
            if blds:
                def _one(b):
                    _k = '单位·' if b.get('kind') == 'unit' else ''
                    if b.get('cx') is None:
                        return '%s%s' % (_k, self._type_disp(b['type']))
                    return '%s%s@(%d,%d)' % (_k, self._type_disp(b['type']), b['cx'], b['cy'])
                summ = '；'.join(_one(b) for b in blds[:6])
                if len(blds) > 6:
                    summ += ' …共%d栋' % len(blds)
            elif has_map:
                summ = '（未挂接任何建筑 —— 可在③选建筑后点「挂到」）'
            else:
                summ = '（未挂接；未挂地图 ⇒ 建筑类型/坐标解不出）'
            tname = it['trigger_name'] or ('（未知触发）' if not it['trigger_id']
                                          else it['trigger_id'])
            it['iid'] = 'N%x' % it['node']
            it['orphan'] = False
            self._attach_rows.append((it['iid'],
                                      ('%s %s' % (it['trigger_id'] or '?', tname),
                                       it['tagid'] or '?', it['count'], summ)))
        # ★2026-10-23 §201：**有标签对象、但没有 CellTag 节点**的触发 —— 以前在挂接页"人间蒸发"
        #   （玩家实测：「尤里中心铁幕解白宫铁幕」找不到；真相是一次性触发执行完、节点被引擎销毁）。
        #   这类只列出来给人看（选不中挂接动作），并在摘要里说清它为什么没有挂接。
        try:
            orphans = self.doc.attach_orphan_tags()
        except Exception:
            orphans = []
        self._attach_orphans = orphans
        for it in orphans:
            it['iid'] = 'T%x' % it['tag_off']
            it['orphan'] = True
            it['node'] = None
            it['count'] = 0
            it['buildings'] = []
            # ★§203（2026-10-23 玩家实测纠错）：**别断言"已执行"**。
            #   存档字节只能证明"没有 CellTag 节点"；"执行过"与"从来没挂上"在这份数据里同形
            #   （触发页早就按 §119 的结论不加断言）。玩家实测：触发页显示正常、这里却说已执行 ⇒ 自相矛盾。
            _ev = [e for e in (it.get('ev_types') or []) if e]
            _evname = ''
            try:
                _evname = '、'.join(str(e) for e in _ev[:3])
            except Exception:
                _evname = ''
            if it.get('ev_fired'):
                summ = ('⚠ 没有 CellTag 节点，且**事件运行期指针已被写过** ⇒ 这条触发执行过')
            elif it.get('kind') == 'timer':
                summ = ('⚠ 没有 CellTag 节点（计时/轮询型必须有节点才会被评估）'
                        '⇒ 本档里它不会执行')
            else:
                summ = ('⚠ 没有 CellTag 节点（事件 %s 属回调型）：引擎只遍历 CellTag 实例链，'
                        '没有节点就不会评估它 —— 可能是执行过，也可能从来没挂上（字节分不清）'
                        % (_evname or '?'))
            summ += '　⇒ 选中后点「🧩 补建挂接节点」可重建节点再挂到建筑上'
            tname = it['trigger_name'] or it['trigger_id'] or '?'
            self._attach_rows.append((it['iid'],
                                      ('%s %s' % (it['trigger_id'] or '?', tname),
                                       it['tagid'] or '?', '—', summ)))
        self._attach_fill_trig()
        self._attach_fill_free()

    def _attach_fill_trig(self):
        """按①搜索框把行填进 Treeview（多词 AND；ID / 名字 / 标签ID 都参与匹配）。

        ★2026-10-23 §208（玩家要求）：① 原来没有搜索，大档 130+ 条触发只能翻。
        """
        if not hasattr(self, 'tma'):
            return
        try:
            flt = (self.ent_attach_trig.get() or '').strip().upper()
        except Exception:
            flt = ''
        words = [w for w in flt.split() if w]
        sel = self.tma.selection()
        self.tma.delete(*self.tma.get_children())
        n = 0
        for iid, vals in (getattr(self, '_attach_rows', None) or []):
            hay = (' '.join(str(v) for v in vals)).upper()
            if words and not all(w in hay for w in words):
                continue
            self.tma.insert('', 'end', iid=iid, values=vals)
            n += 1
        if words and n == 0:
            self.status.set('① 搜索「%s」没有匹配（共 %d 行）'
                            % (flt, len(getattr(self, '_attach_rows', None) or [])))
        # 搜索不该把用户已选中的行弄丢（还在结果里就恢复选中）
        keep = [s0 for s0 in sel if s0 in self.tma.get_children()]
        if keep:
            self.tma.selection_set(keep[0])

    def _attach_cur(self):
        sel = self.tma.selection()
        if not sel or not getattr(self, '_attach_ov', None):
            return None
        key = sel[0]
        for it in self._attach_ov:
            if it.get('iid') == key:
                return it
        for it in (getattr(self, '_attach_orphans', None) or []):
            if it.get('iid') == key:
                return it
        return None

    def _attach_show_blds(self):
        self.tb.delete(*self.tb.get_children())
        it = self._attach_cur()
        if not it:
            return
        for b in it['buildings']:
            # ★§204：类型以**存档记录**为准（地图锚点常差 1 格 ⇒ 以前显示成 `?`）；
            #   实在认不出（存档里没这栋）也要显示出来，否则玩家没法判断该解除哪一栋。
            if b.get('type'):
                disp = self._type_disp(b['type'])
                label = b['type'] if disp == b['type'] else '%s（%s）' % (b['type'], disp)
            else:
                label = '⚠ 存档里找不到（只能按偏移解除）'
            kind = '单位' if b.get('kind') == 'unit' else '建筑'
            pos = ('(%d,%d)' % (b['cx'], b['cy'])) if b.get('cx') is not None else '（无格）'
            self.tb.insert('', 'end', iid=str(b['field']),
                           values=(kind, label, pos,
                                   b.get('hp') if b.get('hp') is not None else '—',
                                   b['map_idx'] or '—', '0x%X' % b['field']))

    def _attach_free_list(self):
        """**存档口径**的可挂建筑 [{base,type,x,y,map_idx,map_tag}]（挂接键 == 0）。

        ★2026-10-23 §202 玩家实测：旧实现用**地图 [Structures] 的 Tag 列**判断"已挂接"，
          于是 `all01umd.map` 里 Tag 写着 01000438、但**存档里挂接键是 0** 的 YACOMD
          被当成"已挂"藏了起来（挂接页找不到它，放置页却能看到）。
          编辑器改的是存档 ⇒ 以存档为准。
        """
        if not self.doc:
            return []
        try:
            return self.doc.attach_free_buildings()
        except Exception:
            return []

    def _attach_fill_free(self):
        self.tc.delete(*self.tc.get_children())
        flt = (self.ent_attach_filter.get() or '').strip().upper()
        n = 0
        self._attach_free_shown = []
        for s in getattr(self, '_attach_free_list', lambda: [])():
            disp = self._type_disp(s['type'])
            if flt and flt not in str(s['type']).upper() and flt not in disp.upper():
                continue
            # 显示成「代码（中文）」—— 玩家在游戏/FA2 里看到的是代码，光给中文对不上
            label = s['type'] if disp == s['type'] else '%s（%s）' % (s['type'], disp)
            iid = '%s|%d|%d' % (s['base'], s['x'], s['y'])
            self.tc.insert('', 'end', iid=iid,
                           values=(s.get('map_idx') or '—', label, '(%d,%d)' % (s['x'], s['y']),
                                   (s.get('map_tag') or '')))
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
        if it.get('orphan'):
            _ql = [x for x in self.tc.selection()]
            _what = ''
            if _ql:
                _b = dict(self._attach_free_shown).get(_ql[0])
                if _b:
                    _what = '建筑 %s(%d,%d)' % (self._type_disp(_b['type']), _b['x'], _b['y'])
            if not _ql:
                messagebox.showwarning(
                    '这条触发没有挂接节点',
                    '「%s」（触发 %s｜标签 %s）在存档里**没有 CellTag 节点**。\n\n'
                    '%s\n\n'
                    '要点：**没有节点 ≠ 已执行**。存档字节只能证明节点不在，'
                    '"执行过"和"从来没挂上"看起来一样（触发页也一直没敢这么断言）。\n'
                    '现在可以重建：先在③选中要挂的建筑，再点「🧩 补建挂接节点」。'
                    % (it.get('trigger_name') or '?', it.get('trigger_id') or '?',
                       it.get('tagid') or '?',
                       ('事件运行期指针已被写过 ⇒ 大概率执行过' if it.get('ev_fired')
                        else '事件运行期指针全是 0 ⇒ 没有"已执行"的证据')))
                return
            if not messagebox.askyesno(
                    '补建挂接节点',
                    '「%s」在存档里**没有 CellTag 节点**（标签还在、定义完好）。\n\n'
                    '要为它**新建一个挂接节点**，挂到 %s 上吗？\n\n'
                    '做法：克隆档内现成的 TagNode + CellTag 结构，指向这条触发的标签，'
                    '然后按正常挂接写入（建筑挂接键 + 计数）。\n'
                    '⚠ 这是写入存档的新操作，**请先备份**；改完先进游戏读档确认。'
                    % (it.get('trigger_name') or it.get('trigger_id') or '?', _what)):
                return
            try:
                _ticks = 0
                _evs = it.get('ev_types') or []
                if _evs and _evs[0] == 13:
                    try:
                        _sec = self.doc.trigger_event_list(it['trigger_id'])[0].get('arg') or 0
                        _ticks = int(_sec) * 15
                    except Exception:
                        _ticks = 0
                r = self.doc.ensure_attach_node(it['trigger_id'], ticks=_ticks)
                self._attach_scanned = False
                self.refresh_attach()
                self.status.set('已为「%s」补建挂接节点（节点键 %#x）+%s'
                                % (it.get('trigger_name') or '?', r.get('cell_key') or 0,
                                   '（含 %d 帧计时）' % _ticks if _ticks else ''))
                messagebox.showinfo(
                    '节点已补建',
                    '已补建挂接节点：CellTag 键 %#x（TagNode 键 %#x）。\n\n'
                    '请重新在①里选中这条触发、在③里选中目标建筑，再点「挂到」。\n'
                    '⭐ 保存后先进游戏读档，确认这条触发确实按预期工作。'
                    % (r.get('cell_key') or 0, r.get('node_key') or 0))
            except Exception as e:
                messagebox.showerror('补建失败', str(e))
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
            # ★§202：优先用**建筑记录里的字段偏移**（`buildings()` 的 base 就是挂接字段）。
            #   旧路是按坐标反查（`locate_building_field`），它要求"两份坐标副本都等于格中心"，
            #   引擎新建的建筑 +160 存的是 iso 锚点 ⇒ 反查不到（玩家会看到"定位不到建筑"）。
            field = info.get('base')
            if not field:
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

    def attach_build_node(self):
        """「🧩 补建挂接节点」：给没有 CellTag 节点的触发重建节点，并（若③有选中）直接挂上。

        为什么需要它（§203）：引擎只遍历 CellTag 的实例链 —— **没有节点就等于这条触发不存在**。
        但存档字节分不清"执行过"和"从来没挂上"，所以不给结论、直接给重建能力。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        it = self._attach_cur()
        if not it:
            messagebox.showwarning('提示', '请先在①中选中一条触发（列表末尾"⚠ 没有 CellTag 节点"的那些）')
            return
        if not it.get('orphan'):
            messagebox.showinfo('不需要补建',
                                '「%s」在存档里已经有 CellTag 节点（挂接数 %s），直接用「挂到」即可。'
                                % (it.get('trigger_name') or '?', it.get('count')))
            return
        _b = None
        sel = self.tc.selection()
        if sel:
            _b = dict(self._attach_free_shown).get(sel[0])
        if not messagebox.askyesno(
                '补建挂接节点',
                '「%s」（触发 %s｜标签 %s）在存档里**没有 CellTag 节点**。\n\n'
                '%s\n\n'
                '要克隆档内现成的 TagNode + CellTag 结构，为它**新建一个挂接节点**吗？%s\n\n'
                '⚠ 写操作：请先备份存档，改完先进游戏读档确认。'
                % (it.get('trigger_name') or '?', it.get('trigger_id') or '?',
                   it.get('tagid') or '?',
                   ('事件运行期指针已被写过 ⇒ 大概率执行过' if it.get('ev_fired')
                    else '事件运行期指针全是 0 ⇒ 没有"已执行"的证据'),
                   ('\n挂完会顺手挂到③选中的 %s(%d,%d)。'
                    % (self._type_disp(_b['type']), _b['x'], _b['y'])) if _b else '')):
            return
        try:
            _ticks = 0
            _evs = it.get('ev_types') or []
            if _evs and _evs[0] == 13:
                try:
                    _sec = self.doc.trigger_event_list(it['trigger_id'])[0].get('arg') or 0
                    _ticks = int(_sec) * 15
                except Exception:
                    _ticks = 0
            r = self.doc.ensure_attach_node(it['trigger_id'], ticks=_ticks)
            note = ''
            if _b:
                try:
                    self.doc.attach_trigger_to_building(r['cell'], _b['base'])
                    note = '，并已挂到 %s(%d,%d)' % (self._type_disp(_b['type']), _b['x'], _b['y'])
                except Exception as e:
                    note = '；挂到建筑时出错：%s' % e
            self._attach_scanned = False
            self.refresh_attach()
            self.status.set('已补建挂接节点（CellTag %#x / TagNode %#x）%s'
                            % (r.get('cell_key') or 0, r.get('node_key') or 0, note))
            messagebox.showinfo('节点已补建',
                                '已补建挂接节点：CellTag 键 %#x（TagNode 键 %#x）%s。\n\n'
                                '⭐ 保存后先进游戏读档，确认这条触发真的按预期工作。'
                                % (r.get('cell_key') or 0, r.get('node_key') or 0, note))
        except Exception as e:
            messagebox.showerror('补建失败', str(e))

    def detach_do(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        it = self._attach_cur()
        if not it:
            messagebox.showwarning('提示', '请先在①中选中一个标签')
            return
        if it.get('orphan'):
            messagebox.showwarning('这条触发没有挂接节点',
                                   '它在存档里没有 CellTag 节点（一次性触发执行后节点被引擎销毁，'
                                   '或从未挂到建筑上）⇒ 没有可解除的挂接。')
            return
        sel = self.tb.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在②中选中一栋要解除的建筑')
            return
        field = int(sel[0])
        try:
            # ★§207（玩家定案）：默认只做"解除"这一件事，不弹三选一、也不顺手禁用。
            #   理由（玩家原话）：挂在建筑上的触发，解除挂接之后没有任何条件能启动它。
            #   好处：①那栋建筑的挂接键清 0 ⇒ 马上能挂别的触发（在 ③ 里选它）；
            #         ②这条触发的节点还在、计数 0 ⇒ ①里仍可选，随时挂到别的建筑上。
            old = self.doc.detach_building(it['node'], field, allow_last=True)
        except Exception as e:
            messagebox.showerror('出错', str(e))
            return
        self._attach_scanned = False
        self.refresh_attach()
        self.status.set('已解除建筑(字段 0x%X，旧值 0x%08X) 的挂接（尚未保存）'
                        % (field, old))

    # ---------------- 地图视图 / 选位（2026-10-15） ----------------
