# -*- coding: utf-8 -*-
"""赠送页（原 `App.gift`）

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

from editor_core import SaveDoc
from ui.msg import filedialog
from ui.msg import messagebox
from tkinter import ttk
from ui.const import UNIT_HOUSE_INHERIT
from ui.const import UNIT_VET_INHERIT
from ui.const import _VET_BY_LABEL
import game_ini
import json
import os
import struct
import tkinter as tk
import ui_theme


class GiftPage(object):
    def _tab_gift(self, nb):
        f = ttk.Frame(nb, padding=8)
        _ph = ui_theme.page_header(f, '赠送建筑', '按模板批量添加建筑')
        _ph.pack(fill='x', padx=2, pady=(2, 8))
        _note, self._note_btn_gift = ui_theme.note_box(f, _ph)
        self._note_box_gift = _note          # 折叠说明的容器（默认不 pack）
        ttk.Label(_note, text='给玩家赠送一座建筑（2026-10-16 游戏内验收通过：**新开载入 ✅ / 游戏内重载 ✅**）。\n'
                          '· 直接放好：立即出现在指定格子（记录 + 三张登记表全登 + 格子占用 + 实例 ID 唯一化）。\n'
                          '· 就绪未落地：侧栏直接出现"就绪"图标，进游戏自己找位置放。\n'
                          '　仅支持**可生产建筑（TechLevel≥1）**——建造场等不可生产类型会拒送，请用直接放好。\n'
                          '· 档里没有的类型也能放（跨类型克隆：自动改类型键/美术尺寸/耐久，数据来自存档语料库）。\n'
                          '★ 直接放好 = **进游戏播一遍建造动画，播完模型完整**（§188 定案，T16 实测）。\n'
                          '· 高级「槽2 偏移」= 工厂反引槽 2 的 hex 值：**只有同一会话内的档**需要填；'
                          '跨档一般留空（只写槽 1），具体待游戏验证。\n'
                          '★ 改完会提示另存为新文件，进游戏前请先备份。',
                  foreground=ui_theme.T['ink_2'], wraplength=700, justify='left').pack(anchor='w')
        # ★§289：原来把「类型 / 刷新 / 两种模式 / 坐标 / 所属方」全塞一行（需 ~1010px
        #   ⇒ 右半截被窗口裁掉）⇒ 拆成「类型＋所属方」「模式＋坐标」「动画＋高级」三行。
        row = ttk.Frame(f)
        row.pack(fill='x', pady=(8, 2))
        ttk.Label(row, text='建筑类型：').pack(side='left')
        self.gift_type = ttk.Combobox(row, width=24, state='readonly')
        self.gift_type.pack(side='left', padx=(4, 0))
        ttk.Button(row, text='刷新类型列表',
                   command=self._gift_fill_types).pack(side='left', padx=(6, 0))
        # ★2026-10-17：所属方（仅「直接放好」分支生效；就绪未落地固定为玩家阵营）
        ttk.Label(row, text='所属方：').pack(side='left', padx=(16, 0))
        self.gift_house = ttk.Combobox(row, width=14, state='readonly')
        self.gift_house.pack(side='left', padx=(4, 0))

        row_mode = ttk.Frame(f)
        row_mode.pack(fill='x', pady=(6, 2))
        self.gift_mode = tk.StringVar(value='ready')
        ttk.Radiobutton(row_mode, text='就绪未落地（推荐）', variable=self.gift_mode,
                        value='ready').pack(side='left')
        ttk.Radiobutton(row_mode, text='直接放好 → 格', variable=self.gift_mode,
                        value='placed').pack(side='left', padx=(16, 4))
        ttk.Label(row_mode, text='X:').pack(side='left')
        self.gift_x = ttk.Spinbox(row_mode, from_=0, to=999, width=5)
        self.gift_x.pack(side='left', padx=(2, 0))
        ttk.Label(row_mode, text='Y:').pack(side='left', padx=(8, 0))
        self.gift_y = ttk.Spinbox(row_mode, from_=0, to=999, width=5)
        self.gift_y.pack(side='left', padx=(2, 0))

        row2 = ttk.Frame(f)
        row2.pack(fill='x', pady=(2, 2))
        # ★2026-10-22 §188 定案：带建造动画（配方含 6E4=0）⇒ 进游戏播一遍、播完模型完整。
        self.gift_buildup = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text='带建造动画（进游戏播一遍，播完模型/炮塔完整）',
                        variable=self.gift_buildup).pack(side='left')
        ttk.Label(row2, text='高级：槽2 偏移(hex)').pack(side='left', padx=(16, 0))
        self.gift_slot2 = ttk.Entry(row2, width=12)
        self.gift_slot2.pack(side='left', padx=(4, 0))
        row3 = ttk.Frame(f)
        row3.pack(fill='x', pady=(6, 2))
        ttk.Button(row3, text='🎁 赠送并另存为…', command=self._gift_do).pack(side='left')
        self.gift_out = tk.Text(f, height=9, bg=ui_theme.MAP['terminal'], fg=ui_theme.MAP['terminal_text'],
                                insertbackground=ui_theme.MAP['terminal_text'])
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

    def _mv_ensure_types(self):
        """切到「地图 · 选位」时把三个下拉填好（每份档只做一次）。

        ★§290（玩家 2026-10-27）：原来要手动点「↻」才有建筑类型可选 —— 那个按钮已删除。
        只做便宜的：建筑类型（~70ms）+ 单位类型（~0ms）；`_fill_house_lists()` 要 ~600ms
        （内部 `doc.houses()` 扫全档），而开档路径已经填过 ⇒ 这里只在**空**时兜底。
        """
        if not self.doc or getattr(self, '_mv_types_loaded', False):
            return
        try:
            names = self._bld_type_names()
            vals = ['%s（%s）' % (n, self._type_disp(n)) for n in names]
            self.mv_type['values'] = vals
            # 赠送建筑页用同一个列表（那边也就不用先点「刷新类型列表」）
            if not self.gift_type.cget('values'):
                self.gift_type['values'] = vals
            try:
                self.mv_unit['values'] = self._unit_type_names()
            except Exception:
                pass
            if not self.mv_house.cget('values'):
                self._fill_house_lists(log=False)
            self._mv_types_loaded = True
        except Exception:
            # 填不上不算错（玩家还能用「模板库」那条路），别打断切页
            pass

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
        # ★2026-10-17：单位用的所属方下拉**多一个「沿用模板」**。
        # ★★§252（玩家实测反馈：「你这次放的是美国啊 玩家方是苏俄」）：
        #   默认改成**当前玩家方**。原来默认「沿用模板」—— 而模板常常是敌方/别的国家的
        #   单位（本档 98 架原生飞碟全是 Americans），于是放出来直接变成对面的。
        #   想"给对面刷兵"就在下拉里显式选那个阵营。
        ucb = getattr(self, 'mv_u_house', None)
        if ucb is not None:
            ucb['values'] = [UNIT_HOUSE_INHERIT] + vals
            ucb.set(me_disp or UNIT_HOUSE_INHERIT)
        if log:
            self._gift_log('所属方列表：%d 个（建筑/单位默认 = 当前玩家；'
                           '单位下拉可切「沿用模板」）' % len(vals))

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
            self.mv_urule.config(text='规则：' + g.source(), foreground=ui_theme.T['ok'])
        else:
            self.mv_urule.config(text='⚠ 没找到 rules(md).ini（显示名改用存档自带）',
                                 foreground=ui_theme.T['danger'])

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

    def _cell_brief(self, cx, cy):
        """落点体检（§221）：占用 / 地形 / 离边缘距离，返回多行文本。"""
        d = self.doc
        L = []
        try:
            if d.cell_taken(cx, cy):
                _t = None
                try:
                    _t = d.terrain_at(cx, cy)
                except Exception:
                    _t = None
                # ★§281：树/石头是 Terrain 对象，占格（+0x138 的 0x10 位），但不属于建筑/单位
                if _t:
                    L.append('⚠ 该格长着 %s（地形对象，会挡住单位/建筑）' % _t.get('type'))
                else:
                    L.append('⚠ 该格已被占用（建筑地基或单位站着）')
        except Exception:
            pass
        try:
            w = d.terrain_warning(cx, cy)
            if w:
                L.append(w)
        except Exception:
            pass
        try:
            mk = d.map_cells() or {}
            if mk:
                xs = [k[0] for k in mk]
                ys = [k[1] for k in mk]
                edge = min(cx - min(xs), max(xs) - cx,
                           cy - min(ys), max(ys) - cy)
                L.append('离地图边缘 %d 格%s' % (
                    edge, '（⚠ 不足 12 格：空军单位可能不显示，§212 实测）' if edge < 12 else ''))
        except Exception:
            pass
        return '\n'.join(L) if L else '✓ 该格看起来可用（地形通行、无占用、离边缘够远）'

    def _spawn_dialog(self, tname, cell):
        """刷兵一屏对话框（§221）：数量 / 援军行为 / 动作脚本。

        返回 {'n','action','script'} 或 None（取消）。
        抽成方法是为了 UI 自检能直接驱动，不必连点三个模态窗。
        """
        dlg = tk.Toplevel(self)
        dlg.title('刷单位（小队法）')
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=12)
        frm.grid(sticky='nsew')
        ttk.Label(frm, text='在 (%d,%d) 刷 %s（引擎自己造，SPAWNS 单位也能刷）'
                  % (cell[0], cell[1], tname),
                  font='', wraplength=440, justify='left'
                  ).grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(frm, text=self._cell_brief(cell[0], cell[1]),
                  wraplength=440, justify='left', foreground=ui_theme.T['warn']
                  ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(2, 8))
        ttk.Label(frm, text='数量：').grid(row=2, column=0, sticky='w')
        sp = ttk.Spinbox(frm, from_=1, to=60, width=6)
        sp.set(4)
        sp.grid(row=2, column=1, sticky='w', pady=2)
        act = tk.IntVar(value=80)
        ttk.Label(frm, text='援军行为：').grid(row=3, column=0, sticky='w', pady=(8, 0))
        ttk.Radiobutton(frm, text='80 援军（直接出现在路径点）',
                        variable=act, value=80
                        ).grid(row=3, column=1, sticky='w')
        ttk.Radiobutton(frm, text='107 超时空传送援军（带时空特效）',
                        variable=act, value=107
                        ).grid(row=4, column=1, sticky='w')
        scr = tk.IntVar(value=0)
        ttk.Label(frm, text='动作脚本：').grid(row=5, column=0, sticky='w', pady=(8, 0))
        ttk.Radiobutton(frm, text='留空（原地待命，推荐）',
                        variable=scr, value=0
                        ).grid(row=5, column=1, sticky='w')
        ttk.Radiobutton(frm, text='沿用模板队的脚本（可能自己行动）',
                        variable=scr, value=1
                        ).grid(row=6, column=1, sticky='w')
        ttk.Radiobutton(frm, text='★新建一段「原地警戒」脚本（警戒 10 秒循环，不乱跑）',
                        variable=scr, value=2
                        ).grid(row=7, column=1, sticky='w')
        ttk.Label(frm, text='触发器用「13 流逝时间 1 秒」⇒ 读档约 1 秒后出兵。',
                  foreground=ui_theme.T['muted']
                  ).grid(row=7, column=0, columnspan=2, sticky='w', pady=(8, 0))
        ret = {}
        row = ttk.Frame(frm)
        row.grid(row=8, column=0, columnspan=2, sticky='e', pady=(10, 0))

        def _ok():
            try:
                ret['n'] = int(sp.get())
            except Exception:
                ret['n'] = 4
            ret['action'] = int(act.get())
            ret['script'] = int(scr.get())
            dlg.destroy()

        ttk.Button(row, text='取消', command=dlg.destroy).pack(side='right', padx=(6, 0))
        ttk.Button(row, text='写入', command=_ok).pack(side='right')
        dlg.wait_window()
        return ret or None

    def mv_spawn_team(self):
        """🔫 刷单位（小队法）：特遣部队 + 作战小队 + 触发「13 流逝时间1秒 + 援军行为@路径点」。

        ★§217：这是**让引擎自己造单位**，所以带 `SPAWNS=` 的 V3/无畏/航母也能正常刷出来
          （手写单位记录那条路对它们是走不通的，§209）。
        ★§218：援军行为可选 80（普通援军）/ 107（超时空传送援军，带时空特效），
          二者在存档里的布局相同（小队键 +0x44、路径点 +0x58）。
        ★§221：参数合并成一屏对话框（落点体检 + 数量 + 行为 + 脚本）。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        st = getattr(self, 'mv_state', None) or {}
        sel = st.get('sel')
        if not sel or sel[0] is None:
            messagebox.showwarning('提示', '请先在右侧地图上**点一个格子**作为刷兵位置')
            return
        cur = self.mv_unit.get() if hasattr(self, 'mv_unit') else ''
        if not cur:
            messagebox.showwarning('提示', '请先在上面选一个单位类型')
            return
        tname = cur.split('（')[0].strip().upper()
        # ★★§283 玩家 2026-10-26 定案：刷兵落点**什么都不必做** ——
        #   「存档中添加刷兵，什么也不必做，刷到占地时游戏引擎会自动改到相邻格」。
        #   ⇒ 墙/石头上照样写下去，**不拦也不挪**（挪了反而丢信息：引擎自己会在读档时挪，
        #     而路径点本身仍是玩家指定的那一格）。只有"手动放置/粘贴/移动"才硬拦，
        #     因为那是直接写单位记录，不会经过引擎的落点纠偏。
        ans = self._spawn_dialog(tname, (sel[0], sel[1]))
        if not ans:
            return
        n, chrono, scr = ans['n'], (ans['action'] == 107), ans['script']
        # 脚本三选（§222）：0 留空 / 1 沿用模板 / 2 新建一段「原地警戒」
        script_key = 0 if scr == 0 else (None if scr == 1 else 'NEW_IDLE')
        script_entries = [(5, 10), (6, 0)] if scr == 2 else None
        try:
            r = self.doc.spawn_units_by_trigger(
                tname, int(n), cell=(sel[0], sel[1]),
                trigger_name=('刷%s%d' % (tname, n))[:16],
                max_teams=1,
                script_key=script_key,
                script_entries=script_entries,
                action=107 if chrono else 80)
        except Exception as e:
            self.status.set('刷兵失败')
            messagebox.showerror('刷兵失败', str(e))
            return
        self.status.set('已刷 %d 个 %s @(%d,%d)：特遣 %s / 小队 %s / 触发 %s（路径点 %s，行为 %d）'
                        % (n, tname, sel[0], sel[1], r['tf']['sid'], r['team']['sid'],
                           r['trigger_id'], r['waypoint'], r['action']))
        # ★§219.9/§221/§223：新小队/特遣的 ID 不是 010 开头（只写进存档、没写地图）⇒
        #   AI 页默认会把它们当"引擎生成对象"过滤掉。登记进 `_ai_ours`（本次会话新建），
        #   刷新后它们照常显示并自动选中（三页联动顺带高亮它的特遣/脚本）。
        try:
            self._ai_ours.add(r['team']['sid'])
            self._ai_ours.add(r['tf']['sid'])
            if r.get('script'):
                self._ai_ours.add(r['script']['sid'])
            self.refresh_ai()
            self._ai_select(self.tat, r['team']['sid'])
        except Exception:
            pass
        script_line = '空（待命）' if scr == 0 else (
            '沿用模板' if scr == 1 else '新建「原地警戒」%s' % r['script']['sid'])
        messagebox.showinfo(
            '刷兵已写入',
            '类型：%s × %d\n位置：(%d,%d) → 路径点 %s\n\n'
            '特遣部队：%s（单位 %s）\n作战小队：%s（脚本 %s）\n'
            '触发器：%s（事件「13 流逝时间 1 秒」+ 行为 %s）\n\n'
            '⭐ 请**另存为**新文件再进游戏验证：读档约 1 秒后单位应出现在该格附近。\n\n'
            '🔍 AI 页已自动刷新并选中新小队；搜索框输 %s 或 %s 可随时找回。\n'
            '（地编 FA2 里看不到它们 —— 它们只写进存档，没写进地图 INI，这是正常的。）'
            % (tname, n, sel[0], sel[1], r['waypoint'],
               r['tf']['sid'], r['tf']['units'],
               r['team']['sid'], script_line,
               r['trigger_id'],
               '107 超时空传送援军' if r['action'] == 107 else '80 援军',
               r['team']['sid'], r['tf']['sid']))

    def mv_learn_sig(self):
        """📚 学习模板：从一份（或多份）存档里学出"每型号的移动方式 + 8 个机体参数"。

        ★§216：为什么学"数值"而不是把整条记录内置进程序？
          · 整条记录 2316+173 字节，且里面大量是对象指针/键，换一份档就失效；
          · 实测跨型号放置真正需要的只有 ~40 字节（`+0x270/+0x51C/+0x940..+0x958`）；
          · 从**你自己的存档**里学，就没有版权问题，也不会因为游戏版本/Mod 变了而错配。
        """
        from editor_core import signature_cache_path
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        ans = messagebox.askyesno(
            '学习单位模板',
            '从存档里学习"每个型号的移动方式 + 8 个机体参数"，存进模板库。\n\n'
            '建议选一份**单位种类多、而且该型号确实在场上**的存档（越大越准）。\n\n'
            '选「是」→ 挑一份存档；选「否」→ 直接用当前打开的这一份。')
        path = self.doc.path
        if ans:
            path = filedialog.askopenfilename(
                title='选择要学习的存档',
                filetypes=[('存档', '*.SAV *.sav'), ('全部', '*.*')],
                initialdir=os.path.dirname(self.doc.path))
            if not path:
                return
        try:
            from editor_core import SaveDoc as _SD
            d = self.doc if os.path.abspath(path) == os.path.abspath(self.doc.path) else _SD(path)
            got = d.learn_unit_signatures()
            self.doc._sigcache = None
            self.status.set('已从 %s 学到 %d 个型号 → %s'
                            % (os.path.basename(path), len(got), signature_cache_path()))
            messagebox.showinfo(
                '学习完成',
                '从「%s」学到 **%d 个型号**的签名。\n\n'
                '模板库位置：\n%s\n\n'
                '以后在**没有该型号**的存档里放单位，会优先用模板库直接写入，'
                '不必再找参考存档。' % (os.path.basename(path), len(got), signature_cache_path()))
        except Exception as e:
            messagebox.showerror('学习失败', str(e))

    def mv_show_sig(self):
        """查看模板库里已有的型号（只读）。"""
        from editor_core import load_signature_cache, signature_cache_path
        c = load_signature_cache()
        ts = c.get('types') or {}
        dlg = tk.Toplevel(self)
        dlg.title('单位模板库（%d 个型号）' % len(ts))
        dlg.geometry('760x460')
        dlg.transient(self)
        ttk.Label(dlg, text='文件：%s' % signature_cache_path(),
                  foreground=ui_theme.T['muted']).pack(anchor='w', padx=8, pady=(8, 2))
        cols = ('t', 'loco', 'cls', 'votes', 'p270', 'p51c', 'p940', 'p948', 'src')
        tv = ttk.Treeview(dlg, columns=cols, show='headings')
        for col, txt, w in (('t', '型号', 90), ('loco', '移动方式', 80), ('cls', '类别', 80),
                            ('votes', '样本数', 60), ('p270', '0x270', 70), ('p51c', '0x51C', 60),
                            ('p940', '0x940', 60), ('p948', '0x948(f)', 80), ('src', '来自', 200)):
            tv.heading(col, text=txt)
            tv.column(col, width=w, anchor='w')
        tv.pack(fill='both', expand=True, padx=8, pady=4)
        for t in sorted(ts):
            sg = ts[t]
            p = sg.get('params') or {}

            def g(k):
                return p.get(k, '')

            def fv(k):
                v = p.get(k)
                if v is None:
                    return ''
                try:
                    return '%.3g' % struct.unpack('<f', struct.pack('<I', v))[0]
                except Exception:
                    return ''

            tv.insert('', 'end', values=(t, sg.get('loco') or '', sg.get('class') or '',
                                         sg.get('votes') or '', g('0x270'), g('0x51C'),
                                         g('0x940'), fv('0x948'), sg.get('src') or ''))
        ttk.Button(dlg, text='关闭', command=dlg.destroy).pack(pady=6)
        if not ts:
            messagebox.showinfo('模板库是空的',
                                '还没有学到任何型号。点「📚 学习模板」，选一份含单位的存档。')

    # ---------------- 单位：复制 / 粘贴 / 移动（§268，2026-10-25 玩家要求） ---------------- #
    def _mv_sel_cell(self, need_units=False):
        """取「当前选中格 + 该格单位记录」；不满足条件时弹提示并返回 None。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return None
        sel = self.mv_state.get('sel')
        if not sel:
            messagebox.showwarning('提示', '请先在地图上点选一个格子')
            return None
        recs = [r for r in (self.doc.unit_records() or [])
                if isinstance(r.get('x'), int) and (r['x'], r['y']) == (sel[0], sel[1])]
        if need_units and not recs:
            messagebox.showinfo('提示', '格 (%d,%d) 上没有单位' % (sel[0], sel[1]))
            return None
        return sel, recs

    def _mv_clip_text(self):
        c = getattr(self, 'mv_clip', None)
        if not c:
            return '剪贴板：空'
        kinds = {}
        for u in c['units']:
            kinds[u['type']] = kinds.get(u['type'], 0) + 1
        txt = '、'.join(('%s×%d' % (t, n)) if n > 1 else t for t, n in kinds.items())
        return '剪贴板：%d 台（%s）· 源格 (%d,%d)' % (len(c['units']), txt,
                                                  c['cell'][0], c['cell'][1])

    def _mv_check_visible(self, cell, action='放置'):
        """目标格在**可见区域（蓝线）外**时问一句；返回 True = 继续。

        ★§271：蓝线 = 地图 `[Map] LocalSize`（地编「地图参数 → 可见区域」）。
          存档格坐标 = 地编 tile 坐标 × 2。地图没读出来时一律放行（不猜）。
        """
        _mp = (getattr(self, 'mv_state', None) or {}).get('path')
        try:
            v = self.doc.cell_visible(cell[0], cell[1], map_path=_mp)
        except Exception:
            v = None
        if v is not False:
            return True
        tip = ''
        try:
            ls = self.doc.map_local_size(_mp)
            if ls:
                tip = ('\n\n地图可见区域（地编「地图参数 → 可见区域」）= %d,%d,%d,%d\n'
                       '换算成编辑器格坐标：x %d..%d、y %d..%d'
                       % (ls[0], ls[1], ls[2], ls[3],
                          2 * ls[0], 2 * (ls[0] + ls[2]) - 1,
                          2 * ls[1], 2 * (ls[1] + ls[3]) - 1))
        except Exception:
            tip = ''
        return messagebox.askyesno(
            '%s位置在「蓝线」外' % action,
            '格 (%d,%d) 在**可见区域（蓝线）之外** —— 游戏里这块地图根本不显示，\n'
            '%s了也看不见（等于白做）。%s\n\n仍要继续吗？'
            % (cell[0], cell[1], action, tip), icon='warning')

    def _mv_ovl_text(self, x, y):
        """信息栏补充文字：该格的**覆盖物**（矿/宝石/墙/桥面/铁轨…）—— 读格对象 `+0x58`（§283）。"""
        try:
            o = self.doc.cell_overlay_info(x, y, ini_path=self._mv_rules_path())
        except Exception:
            o = None
        if not o:
            return ''
        s = o['name']
        if o.get('category'):
            s += ' · ' + o['category']
        return '　覆盖物：%s' % s

    def _mv_rules_path(self):
        """当前规则文件（rulesmd.ini）路径 —— 覆盖物名字/类别都从这里查，查不到就留空。"""
        memo = getattr(self, '_mv_rules', 'unset')
        if memo != 'unset':
            return memo
        p = ''
        try:
            gt = self._rules_types()
            p = getattr(gt, 'rules_path', '') or ''
        except Exception:
            p = ''
        self._mv_rules = p
        return p

    def _mv_check_ovl(self, cell, action='放置'):
        """目标格有**挡路的覆盖物**（墙/沙袋/铁丝网/石头）→ **直接不让放**；True = 放行。

        ★§283 玩家 2026-10-26 定案：
          > 「墙类应该不让放，强放上去会报错，地编中是不让放的；
          >   但是刷兵不一样，刷兵刷到墙等占地时**会自动改到相邻格**」
        ⇒ 手动放置/粘贴/移动 = **硬拦**（不改落点、不给"仍要继续"）；
          刷兵走 `doc.overlay_free_cell_near()` **自动挪到相邻格**（见 `mv_spawn_team`）。
        判据全部来自 `rules(md).ini`（`Wall=yes` / `IsARock` / 硬编码墙 CYCL·BARB·WOOD·FENC）。
        矿/宝石不拦 —— 玩家裁定"矿不影响放置"。
        """
        try:
            o = self.doc.cell_ovl_blocked(cell[0], cell[1],
                                          ini_path=self._mv_rules_path(),
                                          map_path=(getattr(self, 'mv_state', None) or {}).get('path'))
        except Exception:
            o = None
        if not o:
            return True
        near = ''
        try:
            r = self.doc.overlay_free_cell_near(cell[0], cell[1], radius=8,
                                                ini_path=self._mv_rules_path(),
                                                map_path=(getattr(self, 'mv_state', None) or {}).get('path'))
            if r and r[2] > 0:
                near = '\n\n最近的空置格是 (%d,%d)（%d 格远），想放就点它。' % (r[0], r[1], r[2])
        except Exception:
            near = ''
        messagebox.showwarning(
            '这一格不能放',
            '格 (%d,%d) 上有 **%s**。\n\n'
            '墙/沙袋/铁丝网/石头这类覆盖物占格 —— 地编里也不允许往上放，\n'
            '强放进去游戏读档会报错。%s没有替换这一格。%s'
            % (cell[0], cell[1], o['label'], action, near), icon='error')
        self.status.set('(%d,%d) 有挡路覆盖物 %s，未%s' % (cell[0], cell[1], o['name'], action))
        return False

    def mv_copy_units(self):
        """把选中格的单位记进剪贴板（只记 类型/所属方/等级，不改存档）。"""
        got = self._mv_sel_cell(need_units=True)
        if not got:
            return
        sel, recs = got
        self.mv_clip = {'cell': tuple(sel),
                        'units': [{'type': r['type'],
                                   'house': r.get('house') or '',
                                   'veterancy': r.get('veterancy')} for r in recs]}
        self.mv_clip_lbl.config(text=self._mv_clip_text(), foreground=ui_theme.T['info'])
        self.status.set('已复制格 (%d,%d) 的 %d 台单位' % (sel[0], sel[1], len(recs)))
        self.mv_info.config(text='📋 已复制格 (%d,%d) 的 %d 台单位：%s\n'
                                 '下一步：点目标格 → 「📌 粘贴到此格」（复制一份）'
                                 '或「➡ 把源格单位移到此格」（搬过去）'
                                 % (sel[0], sel[1], len(recs), self._mv_clip_text()))

    def mv_paste_units(self):
        """在选中格**克隆**剪贴板里的单位（走 add_unit_by_clone，U2 已验收那条路）。"""
        clip = getattr(self, 'mv_clip', None)
        if not clip:
            messagebox.showinfo('提示', '剪贴板是空的 —— 先选中源格，点「📋 复制该格」')
            return
        got = self._mv_sel_cell()
        if not got:
            return
        sel = got[0]
        if not self._mv_check_visible(sel, '粘贴'):
            return
        if not self._mv_check_ovl(sel, '粘贴'):
            return
        self.status.set('正在粘贴 %d 台单位到 (%d,%d)…' % (len(clip['units']), sel[0], sel[1]))
        self.update_idletasks()
        done, fail, warns = 0, [], []
        for u in clip['units']:
            try:
                info = self.doc.add_unit_by_clone(u['type'], sel[0], sel[1],
                                                  house=(u['house'] or None),
                                                  veterancy=u['veterancy'],
                                                  exact_cell=True, auto_nudge=False)
                done += 1
                warns += [w for w in (info.get('warnings') or [])]
            except Exception as e:
                fail.append('%s：%s' % (u['type'], e))
        self.mv_state['gdata'] = None
        self.mv_state['udata'] = None
        self.mv_render()
        msg = '✅ 已粘贴 %d/%d 台单位到格 (%d,%d)' % (done, len(clip['units']), sel[0], sel[1])
        if warns:
            msg += '\n⚠ ' + '；'.join(warns[:3])
        if fail:
            msg += '\n❌ ' + '；'.join(fail[:3])
        self.mv_info.config(text=msg)
        self.status.set('粘贴完成：%d 台（失败 %d）' % (done, len(fail)))
        if fail:
            messagebox.showwarning('部分没粘上', '\n'.join(fail[:8]))

    def mv_move_units(self):
        """把剪贴板记住的**源格**上的单位整体挪到选中格。

        ★只改位置那 6 处副本（`set_unit_cell`），记录长度不变 ⇒ 不触发全档偏移重算。
        """
        clip = getattr(self, 'mv_clip', None)
        if not clip:
            messagebox.showinfo('提示', '先选中源格，点「📋 复制该格」记住源；再点目标格按这个')
            return
        got = self._mv_sel_cell()
        if not got:
            return
        sel = got[0]
        if tuple(sel) == tuple(clip['cell']):
            messagebox.showinfo('提示', '源格和目标格是同一格')
            return
        if not self._mv_check_visible(sel, '移动'):
            return
        if not self._mv_check_ovl(sel, '移动'):
            return
        self.status.set('正在把 (%d,%d) 的单位移到 (%d,%d)…'
                        % (clip['cell'][0], clip['cell'][1], sel[0], sel[1]))
        self.update_idletasks()
        try:
            res = self.doc.move_units_at(clip['cell'], sel)
        except Exception as e:
            self.status.set('移动失败')
            messagebox.showerror('移动失败', str(e))
            return
        self.mv_state['gdata'] = None
        self.mv_state['udata'] = None
        self.mv_render()
        old = clip['cell']
        clip['cell'] = tuple(sel)          # 源格跟过去，方便连续微调位置
        self.mv_clip_lbl.config(text=self._mv_clip_text(), foreground=ui_theme.T['info'])
        self.mv_info.config(text='✅ 已把 %d 台单位从格 (%d,%d) 移到 (%d,%d)'
                                 '（位置 6 处副本全部重写，格占用已跟着换）'
                                 % (res['count'], old[0], old[1], sel[0], sel[1]))
        self.status.set('移动完成：%d 台 → (%d,%d)' % (res['count'], sel[0], sel[1]))

    def mv_delete_units(self):
        """删掉选中格上的**所有单位**（真删记录：主体 + 伴随 + 两张指针表条目 + 格占用）。

        ★必须先体检引用：别的对象（建筑的目标指针、别的单位）指向它时，
          `remove_unit` 会把那些引用**清 0** —— 这里把清单摆给用户看，让他决定。
        ★删多台时按 `off` **从大到小**删：删字节会让后面的偏移前移，倒序才不会算错。
        """
        got = self._mv_sel_cell(need_units=True)
        if not got:
            return
        sel, recs = got
        # ---- 引用体检（只读）----
        lines, foreign, unknown = [], [], []
        self.status.set('正在检查引用…')
        self.update_idletasks()
        for r in recs:
            try:
                refs = self.doc.unit_key_refs(r)
            except Exception as e:
                refs = []
                lines.append('· %s：体检失败（%s）' % (r['type'], e))
                continue
            for x in refs:
                if x['kind'] == 'foreign_bld':
                    foreign.append((r, x))
                elif x['kind'] == 'foreign_unit':
                    foreign.append((r, x))
                elif x['kind'] == 'other':
                    unknown.append((r, x))
        kinds = {}
        for r in recs:
            kinds[r['type']] = kinds.get(r['type'], 0) + 1
        msg = ['要删除格 (%d,%d) 上的 %d 台单位：' % (sel[0], sel[1], len(recs)),
               '　' + '、'.join(('%s×%d' % (t, n)) if n > 1 else t for t, n in kinds.items()),
               '',
               '删除方式：把记录（主体 + 伴随）从存档里摘掉，']
        msg.append('同时删掉两张全局指针表里的条目、清掉该格的占用。')
        if foreign:
            msg.append('')
            msg.append('⚠ 还有 %d 处外部对象引用它，会**清 0**：' % len(foreign))
            for _r, x in foreign[:6]:
                msg.append('　· %s 记录的 +%#x（%s 在盯着它）'
                           % (x.get('owner'), x.get('rel') or 0,
                              '建筑' if x['kind'] == 'foreign_bld' else '别的单位'))
        if unknown:
            msg.append('')
            msg.append('❌ 有 %d 处认不出的引用 ⇒ 工具会**拒绝删除**这些台' % len(unknown))
        msg.append('')
        msg.append('删除会改变明文长度（每台约 -1.9KB），全档偏移会重算。\n保存前请先「备份存档」。')
        if not messagebox.askyesno('删除单位', '\n'.join(msg), icon='warning'):
            return
        # ---- 逐台删：★每删一台都要**重新定位**（删指针表条目会让后面所有记录前移 8 字节，
        #      拿旧 off 直接删第二台就会撞到「定位组计数失败」）
        ok, fail = 0, []
        for _ in range(len(recs)):
            cur = [r for r in (self.doc.unit_records() or [])
                   if isinstance(r.get('x'), int) and (r['x'], r['y']) == tuple(sel)]
            if not cur:
                break
            r = max(cur, key=lambda x: x['off'])
            try:
                info = self.doc.remove_unit(r)
                ok += 1
                self.status.set('已删 %s @(%d,%d)（-%d 字节）'
                                % (r['type'], info['cell'][0], info['cell'][1], -info['delta']))
                self.update_idletasks()
            except Exception as e:
                fail.append('%s：%s' % (r['type'], e))
                break
        self.mv_state['gdata'] = None
        self.mv_state['udata'] = None
        self.mv_render()
        out = '🗑 已删除 %d/%d 台单位（格 (%d,%d)）' % (ok, len(recs), sel[0], sel[1])
        if foreign:
            out += '\n已清 0 的外部引用：' + '、'.join('%s+%#x' % (x.get('owner'), x.get('rel') or 0)
                                                 for _r, x in foreign[:6])
        if fail:
            out += '\n❌ ' + '；'.join(fail[:3])
        self.mv_info.config(text=out)
        self.status.set('删除完成：%d 台（失败 %d）' % (ok, len(fail)))
        if fail:
            messagebox.showwarning('部分没删掉', '\n'.join(fail[:8]))

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
        if not self._mv_check_visible(sel, '放置'):
            return
        if not self._mv_check_ovl(sel, '放置'):
            return
        cur = self.mv_unit.get() if hasattr(self, 'mv_unit') else ''
        if not cur:
            messagebox.showwarning('提示', '本档没有可克隆的单位类型（先刷新类型）')
            return
        tname = cur.split('（')[0].strip()
        house = self._picked_house('mv_u_house')
        _vl = self.mv_u_vet.get() if hasattr(self, 'mv_u_vet') else UNIT_VET_INHERIT
        vet = _VET_BY_LABEL.get(_vl)
        _nudge = bool(getattr(self, 'mv_u_nudge', None) and self.mv_u_nudge.get())
        # ★2026-10-18 新增：目标格被建筑/单位占着 ⇒ 主动**挪到最近的空地**（玩家要求）
        #   引擎的占用口径 = 建筑记录 + 地基（`cell_taken`），跟格表 +0xF8 不是一回事。
        _tree = None
        try:
            taken = self.doc.cell_taken(sel[0], sel[1])
            _tree = self.doc.terrain_at(sel[0], sel[1])
        except Exception:
            taken = False
        _why = ('格 (%d,%d) 上长着一棵 %s（地形对象，飞机/步兵都过不去）'
                % (sel[0], sel[1], _tree['type'])) if _tree else \
               ('格 (%d,%d) 已被建筑/单位占用' % (sel[0], sel[1]))
        if taken:
            near = None
            try:
                near = self.doc.free_cell_near(sel[0], sel[1], radius=8, clearance=1)
            except Exception:
                near = None
            if not near:
                messagebox.showwarning('放置单位', '%s，附近 8 格内也没找到空地。' % _why)
                return
            if not messagebox.askyesno(
                    '放置单位',
                    '%s（引擎会把它挤掉或直接销毁）。\n\n'
                    '改放到最近的空地 (%d,%d) 吗？' % (_why, near[0], near[1])):
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
            try:
                _has = any(r['type'].upper() == tname.upper() for r in self.doc.unit_records())
            except Exception:
                _has = True
            info = None
            if not _has:
                # ★§216（玩家提议："是不是应该内置一份单位模板，直接可以写入"）：
                #   优先级改成 **模板库 → 参考存档 → 硬克隆**。
                #   模板库只要每型号 ~40 字节（移动方式 + 8 个机体参数），
                #   不用你每次去找一份含该型号的存档。
                _sig = None
                try:
                    _sig = self.doc.unit_signature(tname)
                except Exception:
                    _sig = None
                if _sig:
                    info = self.doc.add_unit_by_signature(tname, sel[0], sel[1],
                                                         house=house, veterancy=vet,
                                                         auto_nudge=_nudge)
                    self.status.set('已用「模板库」写入 %s @(%d,%d)（样本 %s 票，参数 %d 个）'
                                    % (tname, sel[0], sel[1], _sig.get('votes'),
                                       info.get('params_applied') or 0))
                elif messagebox.askyesno(
                        '本档没有这个型号，模板库里也没有',
                        '这份存档里没有 %s 的真机，模板库里也没有它的签名。\n\n'
                        '要不要从**含该型号的存档**里导入一台真机记录（只换身份/坐标/所属方）？\n'
                        '导入时会把该型号的签名一起学进模板库，以后就不必再选了。\n\n'
                        '选「是」→ 挑一份参考存档；选「否」→ 仍然用克隆法硬放（载具可能"卡在地上"）。'
                        % tname):
                    _ref = filedialog.askopenfilename(
                        title='选择含 %s 的参考存档' % tname,
                        filetypes=[('存档', '*.SAV *.sav'), ('全部', '*.*')],
                        initialdir=os.path.dirname(self.doc.path))
                    if _ref:
                        info = self.doc.import_unit_from(_ref, tname, sel[0], sel[1],
                                                         house=house, veterancy=vet,
                                                         auto_nudge=_nudge)
                        self.status.set('已用「直接写入法」从 %s 导入 %s @(%d,%d)'
                                        % (os.path.basename(_ref), tname, sel[0], sel[1]))
            if info is None:
                info = self.doc.add_unit_by_clone(tname, sel[0], sel[1],
                                                  house=house, veterancy=vet,
                                                  auto_nudge=_nudge)
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
        # ★§210：把目标格的地形读数也报出来（水/悬崖是放置失败的头号盲区）
        try:
            _t = self.doc.cell_terrain(sel[0], sel[1]) if self.doc else None
        except Exception:
            _t = None
        if _t:
            _terr = 'tile %d%s' % (_t[0], '' if _t[0] in (self.doc.walkable_tiles() or set()) else '（没被踩过·可疑）')
        else:
            _terr = '地图未加载'
        self.mv_info.config(
            text='✅ 已克隆放置单位 %s @(%d,%d) %s（自键 %#x，等级 %s，移动方式 %s，骨架 %s%s，地形 %s，'
                 '指针表 %d 张，明文 +%d B）%s'
                 % (tname, sel[0], sel[1], _hc, info['key'],
                    info.get('veterancy_name') or '未定义', info.get('loco') or '未定',
                    info.get('scaffold_type') or info.get('template_type') or '?',
                    '（空闲）' if info.get('scaffold_idle') else '（⚠状态非空闲）',
                    _terr, info['tables'], info['delta'],
                    ('｜' + '；'.join(_w)) if _w else ''))
        self._gift_log('[放单位] %s @(%d,%d)：%s' % (tname, sel[0], sel[1], info))
        # ★★§251：落点不是平地时**必须明确告警** —— 实测在斜坡/悬崖/引桥格上放单位，
        #   打掉它会弹框崩溃，而且它还会"有目标"到处飞（引擎认为站位不合法、一直想挪走）。
        #   这个坑藏了 6 轮测试，因为斜坡格是"可通行"的，老的地形校验一路绿灯。
        _nudge = info.get('cell_nudge')
        if _nudge:
            messagebox.showwarning(
                '落点已自动挪到平地',
                '你要放的是 (%d,%d)，但那格**不是平地**（%s）。\n\n'
                '实测后果：在斜坡/悬崖/引桥格上放单位，**打掉它会弹框崩溃**，'
                '而且它会"有目标"、自己往外飞。\n\n'
                '已自动改放到最近的平地 (%d,%d)。'
                % (_nudge['from'][0], _nudge['from'][1], _nudge.get('why') or '',
                   _nudge['to'][0], _nudge['to'][1]))
        # ★★§252：默认**不再自动挪位**（玩家原话「我指定是平地，你选的地方反而更危险」）。
        #   所以"不是平地"这件事必须在这里明确弹出来，否则用户以为一切正常。
        elif any('不是平地' in _x for _x in _w):
            messagebox.showwarning(
                '落点不是平地（已按你的指定原样落位）',
                '你指定的 (%d,%d) **不是平地**：\n%s\n\n'
                '已按你的要求**原样放上去，没有挪动**。\n\n'
                '提醒：实测在这种格子上，单位会"有目标、自己往外跑"，被打掉时还容易弹框。'
                '要让它自动改放到最近的平地，请勾上「自动挪到平地」再放一次。'
                % (sel[0], sel[1],
                   next((x for x in _w if '不是平地' in x), '')))
        # ★§215：骨架不是"空闲"状态时明确告警 —— 实测这种骨架会让新单位继承
        #   "已部署/任务中"（趴在地上，或空中却不能移动、空血不死）。打分已优先挑空闲的，
        #   这里只是兜底（万一整份档都没有空闲的同类）。
        if info.get('scaffold_idle') is False:
            messagebox.showwarning(
                '骨架状态不理想',
                '这次用的骨架（%s）**不是空闲状态**（+0x10C/+0x11C/+0x120 有非 0 值）。\n\n'
                '实测后果：新单位可能一出现就是"已部署/趴在地上"，或者"在空中却不能移动、'
                '空血只爆一次不死"。\n\n'
                '这份存档里没有空闲状态的同类单位（正常档一般都有）。建议换一份存档，'
                '或先在游戏里让那个单位解除部署/结束任务，再存一次档。'
                % (info.get('scaffold_type') or info.get('template_type') or '?'))
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
        # ★★★★★ 2026-10-23 §197：**跨类型放置**（本档没有该类型）—— 玩家已在 SAVE0029
        #   大档实测：GAAIRC 在 (20,179) 放下去**显示完整、能选中、能被打**，
        #   与旁边同类型克隆的 GAROBO / GATECH 表现一致 ⇒ 可用。
        if info.get('cross'):
            _tips.append('ℹ 跨类型：本档原本没有「%s」，用别的建筑当骨架 + 套它的类型签名'
                         '造出来的（已实测显示/选中/受击都正常）。仍建议先进游戏确认一眼'
                         '—— 若出现异常，换一栋更大的同类骨架或改用「⚡ 触发法」' % tname)
        # ★★2026-10-23 §197：**地形**。工具只看"格子有没有被占"，**判断不了自制地形 /
        #   悬崖 / 水面**；玩家实测 (20,184) 就是自制地形 ⇒ 建筑放上去在游戏里不可交互
        #   （看起来像"能显示但不能被攻击"）。所以要提醒一句。
        _tips.append('若进游戏发现新楼"能看见但不能选中/打不掉"，先怀疑**地形**'
                     '（自制地形、悬崖、水面都不行），换个平地重试')
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
        # ★★§287：残留键 = 进游戏必弹框（加载器的键归并断言）⇒ 必须让玩家看见
        _left = r.get('leftover_refs') or {}
        if _left:
            _txt = '、'.join('%08X@%#x' % (k, v[0]) for k, v in list(_left.items())[:4])
            if messagebox.askyesno(
                    '⚠ 还有残留引用 —— 要不要现在清掉？',
                    '这栋建筑的键在存档里还能找到：\n\n    %s\n\n'
                    '正常应该是 0 处。**带着残留进游戏必弹 Internal error**'
                    '（加载器做键归并时对不上）。\n\n'
                    '点「是」我立刻把这 %d 处剔干净；点「否」这次改动就不许存盘。\n'
                    '（参考：stray_swept=%s）'
                    % (_txt, len(_left), r.get('stray_swept'))):
                try:
                    _n = self.doc.sweep_key_refs(set(_left))
                except Exception as e:
                    _n = 0
                    messagebox.showerror('清理失败', str(e))
                _left2 = self.doc.leftover_refs(set(_left))
                if _n and not _left2:
                    self.doc._unsafe_leftover = None
                    messagebox.showinfo(
                        '已清理',
                        '剔掉 %d 处残留引用，现在这份档可以存了（进游戏前建议先备份）。' % _n)
                    self.status.set('✅ 已清理残留引用 %d 处，可以另存为新文件' % _n)
                else:
                    messagebox.showwarning(
                        '仍有残留',
                        '去掉 %d 处，但仍剩 %d 处清不掉 —— 这次改动我不会让你存盘，\n'
                        '请把下面这段话发给我：\n\n  leftover=%s'
                        % (_n, len(_left2), _left2))
                    self.status.set('⚠ 仍有残留引用 %d 处（禁止存盘）' % len(_left2))
            else:
                self.status.set('⚠ 删除后仍有残留引用 %d 处（已禁止存盘）' % len(_left))
        else:
            self.status.set('✅ ' + r['note'] + '（已核对：无残留引用）')
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

    # ---------------- 武器 · 弹头（★2026-10-28 新增）----------------
    # 背景：玩家确认「改 rulesmd.ini 不影响已存盘存档里的武器效果」⇒ 读档时引擎用的是
    # 存档里那张武器类型表。所以这一页的编辑是**真正生效**的，且只影响这一份存档。
    # 写入全是**定长**（Damage/ROF/Range = u32、Verses = double）⇒ 明文长度不变、偏移不动。
