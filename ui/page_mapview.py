# -*- coding: utf-8 -*-
"""地图页（原 `App.mapview`）

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
from ui.const import UNIT_VET_CHOICES
import json
import os
import re
import subprocess
import sys
import time
import tkinter as tk
import ui_theme
_HERE = _ROOT

class MapViewPage(object):
    def _tab_mapview(self, nb):
        f = ttk.Frame(nb, padding=8)
        _ph = ui_theme.page_header(f, '地图 · 选位', '地图只读；拖放 / 移动单位与建筑')
        _ph.pack(fill='x', padx=2, pady=(2, 8))
        # 「规则来源」是全局信息（不是某张卡的操作）⇒ 固定挂在页头右侧常显，
        #   免得挤占本来就只有 ~620px 的卡内宽度
        self.mv_urule = tk.Label(_ph, text='', bg=ui_theme.T['canvas'],
                                 fg=ui_theme.T['muted'], font=(ui_theme.FONT_UI, 8))
        self.mv_urule.pack(side='right', padx=(0, 10))
        _note, self._note_btn_mapview = ui_theme.note_box(f, _ph)
        self._note_box_mapview = _note          # 折叠说明的容器（默认不 pack）
        ttk.Label(_note, text='地图俯视图 + 落位判定　★安全闸：以不变量判定「存档 ↔ 地图」是否同一张图'
                          '——主判据是「地图 [Map] Size == 存档自带尺寸」（存档固定头部 0x348E 的 (W,H)），'
                          '与建筑是否被摧毁无关（后期档建筑大量被毁也照常匹配）；'
                          '建筑重合率只作参考提示，不参与放行。尺寸读不出时才退化为「建筑是否都在图上」。',
                  foreground=ui_theme.T['ink_2'], wraplength=700, justify='left').pack(anchor='w')
        self.mv_warn = ttk.Label(
            _note, text='⚠ 只要地图尺寸与存档尺寸一致，本工具就认定是同一张图 —— 动手改之前请先备份存档'
                        '（点右边「备份存档」一键搞定；改完也建议先另存为新文件再进游戏验证）。',
            foreground=ui_theme.T['danger'], wraplength=700, justify='left')
        self.mv_warn.pack(anchor='w', pady=(4, 0))
        # ★§SOV11：定位行的用法（放在说明里，页面上只留控件，不再多占一行）
        ttk.Label(_note,
                  text='定位：「X / Y」填格子坐标 → 「跳到格」；「路径点」填号（或从下拉选）→ 「跳到点」。'
                       '两者都会选中那一格并把视图居中（贴边的格子只能贴边，滚不到正中）。'
                       '反向也通：在地图上点任意一格，坐标会回填到 X / Y，改完再点「跳到格」；'
                       '拖远了点「居中选中」回到当前格。缩放以视口中心为锚点，不会一缩放就漂回左上。\n'
                       '✏ 写到 X/Y：把「路径点」框里那号路径点**改到** X / Y 的坐标 —— 只写存档'
                       '（原地 4 字节、长度不变、可再改回来），.map 一律不动。'
                       '改完若地图 [Waypoints] 里同号在别处，图上会在**存档实际位置**多画一个橙圈，'
                       '标签写成「号*」，表示"地图与存档不一致，以存档为准"。',
                  foreground=ui_theme.T['ink_2'], wraplength=700,
                  justify='left').pack(anchor='w', pady=(4, 0))
        # ★§SOV12：单元标记（CellTag 挂接点）的用法
        ttk.Label(_note,
                  text='显示（视图区第二行）：路径点 / 单元标记 / 单位 / 树·石 四个勾选直接横排。'
                       '路径点**以存档为准**（引擎读档走存档）：绿圆白字 = 存档里的号、画在存档坐标；'
                       '橙圈 +「号*」= 地图 [Waypoints] 里同号坐标不同（两边不一致，看绿圆）；'
                       '灰空心圈 = 只有地图有、存档里没有的号。单位要 ≥20 倍才画。\n'
                       '单元标记（CellTag 挂接点）：默认开，同一个 CellTag 的格子画成同色。点中一格，'
                       '信息栏会写「★单元标记：N 个（CellTag → 触发名）」；'
                       '「🧹 删该格标记」把这格从 CellTag 上解挂（清 CellClass +80、挂接数 −1，'
                       '对象本身不删、存档长度不变）；「📋 列出标记…」可双击跳格、多选批量删。'
                       '只改存档、不写 .map —— 从地图重开任务，标记还会回来。',
                  foreground=ui_theme.T['ink_2'], wraplength=700,
                  justify='left').pack(anchor='w', pady=(4, 0))
        ttk.Label(_note,
                  text='放置建筑：勾「触发法」= 引擎自己造（有建造动画、模型完整，悬崖 / 水面等不可建'
                       '地形会被引擎拒绝）；不勾 = 直接落成（立即写档、可指定所属方，引擎仍会播一遍'
                       '建造动画，读档后 1~2 秒长出来）。\n'
                       '修旧楼 = 存档在 Buildup 播完前保存 ⇒ 重载后模型显示不全；'
                       '用「修成完成态 / 扫描卡住 / 补齐动画」修。\n'
                       '图例：红 = 该格已被占 ｜ 深红细线 = 与邻格有高度差（悬崖/斜坡）'
                       '｜ 暗色 = 地图外（边界填充格）。',
                  foreground=ui_theme.T['ink_2'], wraplength=700, justify='left').pack(anchor='w', pady=(4, 0))
        # ★§289：原来 7 行散控件（30+ 个控件挤一页，多个按钮被窗口右缘切掉）
        #   ⇒ 四张「横条式分区」：地图 / 视图 / 放置 / 选中格。
        #   section() 的标题在左侧竖列 ⇒ 分组只多吃 4 条 3px 色条，不像四张普通卡片
        #   那样各多一行标题。低频操作（修旧楼 3 个 / 刷单位 / 学习模板 / 模板库 / 规则目录）
        #   收进两个 ▾ 菜单 —— 和触发页「更多 ▾」同一个口径。
        SUF = 'Surface.TFrame'          # 卡片内：白底
        LB = 'Surface.TLabel'
        MU = 'Muted.TLabel'
        SM = (ui_theme.FONT_UI, 8)
        PAD = 4                          # 同行控件间距；工作区只有 ~740px，别调大

        # ── ① 地图：选图 / 匹配 / 备份 ＋ 地形数据源与剧场 ──
        _s, b1 = ui_theme.section(f, '地图', '只读')

        r = ttk.Frame(b1, style=SUF)
        r.pack(fill='x')
        ttk.Button(r, text='选择地图…', command=self.mv_choose).pack(side='left')
        ttk.Button(r, text='自动匹配', command=self.mv_auto).pack(side='left', padx=(PAD, 0))
        ttk.Button(r, text='🗺 用地编打开', command=self.fa2_open).pack(side='left', padx=(PAD, 0))
        ttk.Button(r, text='备份存档', command=self.mv_backup).pack(side='left', padx=(PAD, 0))
        self.mv_lbl = ttk.Label(r, text='未加载地图', style=MU)
        self.mv_lbl.pack(side='left', padx=(8, 0))

        r = ttk.Frame(b1, style=SUF)
        r.pack(fill='x', pady=(6, 0))
        ttk.Label(r, text='地形数据源', style=LB).pack(side='left')
        # ★★§280：地图（默认）／地图+存档补格／存档
        self.mv_terr_src = tk.StringVar(value='地图')
        ttk.Combobox(r, textvariable=self.mv_terr_src, width=12, state='readonly',
                     values=('地图', '地图+存档补格', '存档')).pack(side='left', padx=(6, 0))
        self.mv_terr_src.trace_add('write', self._mv_on_terr_src)
        # ★§285：tile 索引按剧场取色，而存档里没有剧场字段（搜 TEMPERATE/URBAN/SNOW/
        #   Theater/[Basic] 全无）⇒ 有 .MAP 用地图 [Basic] Theater，没有就靠这个下拉。
        #   只影响**配色**：格子归属、层高、选位、放建筑都不看剧场。
        ttk.Label(r, text='剧场', style=LB).pack(side='left', padx=(12, 0))
        self.mv_theater_var = tk.StringVar(value='TEMPERATE')
        self._cb_theater = ttk.Combobox(
            r, textvariable=self.mv_theater_var, width=10, state='readonly',
            values=('TEMPERATE', 'SNOW', 'URBAN', 'NEWURBAN', 'DESERT', 'LUNAR'))
        self._cb_theater.pack(side='left', padx=(6, 0))
        self._cb_theater.bind('<<ComboboxSelected>>', lambda e: self._mv_on_theater())
        self.lbl_terr_hint = ttk.Label(
            r, text='没 .MAP 时地形取自存档；剧场只影响配色',
            style=MU, font=SM)
        self.lbl_terr_hint.pack(side='left', padx=(10, 0))
        # 兼容旧代码/自检：`mv_src_save` 由下拉派生（True ⇔ 档位 = "存档"），不再单独占界面
        self.mv_src_save = tk.BooleanVar(value=False)
        self.mv_src_save.trace_add('write', self._mv_on_src_save)

        # ── ② 视图：缩放 ＋ 叠加开关（低频三个收进「叠加 ▾」，勾选状态照样看得见）──
        _s, b2 = ui_theme.section(f, '视图')

        r = ttk.Frame(b2, style=SUF)
        r.pack(fill='x')
        ttk.Label(r, text='缩放', style=LB).pack(side='left')
        ttk.Button(r, text='－', width=3,
                   command=lambda: self.mv_zoom(-1)).pack(side='left', padx=(6, 0))
        ttk.Button(r, text='＋', width=3,
                   command=lambda: self.mv_zoom(1)).pack(side='left', padx=(3, 0))
        ttk.Button(r, text='适应窗口', command=self.mv_fit).pack(side='left', padx=(6, 0))
        self.mv_grid = tk.BooleanVar(value=True)
        ttk.Checkbutton(r, text='网格', variable=self.mv_grid, command=self.mv_render,
                        style='Surface.TCheckbutton').pack(side='left', padx=(14, 0))
        # ★§283：覆盖物 —— 默认开（金黄=矿/宝石、灰块=挡路、蓝线=桥/铁轨）
        self.mv_ovl = tk.BooleanVar(value=True)
        ttk.Checkbutton(r, text='覆盖物', variable=self.mv_ovl, command=self.mv_render,
                        style='Surface.TCheckbutton').pack(side='left', padx=(8, 0))
        ttk.Button(r, text='重绘', command=self.mv_render).pack(side='left', padx=(10, 0))

        # ★★§SOV13（2026-11-04 玩家：「路径点、单元标记、单位、树石这些选项，不用叠加，
        #   直接向右边排列显示」）⇒ 原来收在「叠加 ▾」里的 4 个勾选**摊平成一行**，
        #   CellTag 相关的两个命令也摆在这一行右边（勾选与操作放一起更好找）。
        r = ttk.Frame(b2, style=SUF)
        r.pack(fill='x', pady=(4, 0))
        ttk.Label(r, text='显示', style=LB).pack(side='left')
        self.mv_wp = tk.BooleanVar(value=True)
        ttk.Checkbutton(r, text='路径点', variable=self.mv_wp, command=self.mv_render,
                        style='Surface.TCheckbutton').pack(side='left', padx=(6, 0))
        # CellTag 挂接点 = 地图上的「单元标记」，默认显示（同一条 CellTag 同色）
        self.mv_marks = tk.BooleanVar(value=True)
        ttk.Checkbutton(r, text='单元标记', variable=self.mv_marks,
                        command=self._mv_on_marks,
                        style='Surface.TCheckbutton').pack(side='left', padx=(10, 0))
        self.mv_units = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text='单位', variable=self.mv_units,
                        command=self._mv_toggle_units,
                        style='Surface.TCheckbutton').pack(side='left', padx=(10, 0))
        self.mv_terr = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text='树/石', variable=self.mv_terr, command=self.mv_render,
                        style='Surface.TCheckbutton').pack(side='left', padx=(10, 0))
        ttk.Button(r, text='📋 列出标记…',
                   command=self.mv_list_markers).pack(side='left', padx=(14, 0))
        ttk.Button(r, text='🧹 删该格标记',
                   command=self.mv_delete_markers).pack(side='left', padx=(4, 0))

        # ★§SOV11（2026-11-04 玩家要求）：地图动辄 1024×1024，靠拖滚动条找一格太费劲
        #   ⇒ 加一行「定位」：填 (x,y) 或路径点号，选中那一格并**把视图居中**到它；
        #   反方向也给：点地图任意格 ⇒ 坐标回填到这两个框里（可微调后再跳）。
        r = ttk.Frame(b2, style=SUF)
        r.pack(fill='x', pady=(4, 0))
        ttk.Label(r, text='定位', style=LB).pack(side='left')
        ttk.Label(r, text='X', style=MU, font=SM).pack(side='left', padx=(6, 0))
        self.mv_gx = tk.StringVar()
        self._e_mv_gx = ttk.Entry(r, textvariable=self.mv_gx, width=4)
        self._e_mv_gx.pack(side='left', padx=(2, 0))
        ttk.Label(r, text='Y', style=MU, font=SM).pack(side='left', padx=(6, 0))
        self.mv_gy = tk.StringVar()
        self._e_mv_gy = ttk.Entry(r, textvariable=self.mv_gy, width=4)
        self._e_mv_gy.pack(side='left', padx=(2, 0))
        for _e in (self._e_mv_gx, self._e_mv_gy):
            _e.bind('<Return>', lambda e: self.mv_goto_xy())
        ttk.Button(r, text='跳到格', width=7,
                   command=self.mv_goto_xy).pack(side='left', padx=(4, 0))
        ttk.Label(r, text='路径点', style=LB).pack(side='left', padx=(10, 0))
        self.mv_gwp = tk.StringVar()
        # 可键入号，也能从下拉挑（下拉项 = 本图 [Waypoints] ∪ 存档里有的号）
        self._cb_mv_gwp = ttk.Combobox(r, textvariable=self.mv_gwp, width=6)
        self._cb_mv_gwp.pack(side='left', padx=(4, 0))
        self._cb_mv_gwp.bind('<Return>', lambda e: self.mv_goto_wp())
        self._cb_mv_gwp.bind('<<ComboboxSelected>>', lambda e: self.mv_goto_wp())
        ttk.Button(r, text='跳到点', width=7,
                   command=self.mv_goto_wp).pack(side='left', padx=(4, 0))
        # ★★§SOV13（2026-11-04 玩家：「改路径点坐标这个功能可以做」）：
        #   把「路径点」框里那号改到 X / Y 框的坐标（只写存档的路径点表）。
        ttk.Button(r, text='✏ 写到X/Y', width=10,
                   command=self.mv_set_wp_xy).pack(side='left', padx=(4, 0))
        # ★★§SOV38（2026-11-05 玩家：「在地图选位页面增加新建路径点功能」）：
        #   「写到X/Y」要求先有号；想**凭空造一个新号**（比如给行为 108 的箱子找个落点）
        #   就得自己猜哪个号没被占。这个按钮自动挑空号，或者在「路径点」框里指定号。
        ttk.Button(r, text='＋ 新建点', width=9,
                   command=self.mv_new_wp).pack(side='left', padx=(4, 0))
        ttk.Button(r, text='居中', width=6,
                   command=self.mv_center_sel).pack(side='left', padx=(6, 0))

        # ── ③ 放置：建筑 / 单位 / 触发法（都作用于选中格）──
        _s, b3 = ui_theme.section(f, '放置', '作用于选中格')

        r = ttk.Frame(b3, style=SUF)
        r.pack(fill='x')
        ttk.Label(r, text='建筑', style=LB).pack(side='left')
        # ★§290：类型列表改成"切到本页就填好"（见 _mv_ensure_types），
        #   原来那个 ↻ 刷新按钮去掉了；combo 相应加宽补回空间
        self.mv_type = ttk.Combobox(r, width=19, state='readonly')
        self.mv_type.pack(side='left', padx=(5, 0))
        ttk.Label(r, text='所属', style=LB).pack(side='left', padx=(6, 0))
        # ★2026-10-17：写建筑记录载荷 +336/+544 的 House 键
        self.mv_house = ttk.Combobox(r, width=13, state='readonly')
        self.mv_house.pack(side='left', padx=(5, 0))
        ttk.Button(r, text='放置', command=self.mv_place).pack(side='left', padx=(8, 0))
        ttk.Button(r, text='删除建筑',
                   command=self.mv_delete).pack(side='left', padx=(4, 0))
        ttk.Button(r, text='改所属方',
                   command=self.mv_set_owner).pack(side='left', padx=(4, 0))

        r = ttk.Frame(b3, style=SUF)
        r.pack(fill='x', pady=(4, 0))
        ttk.Label(r, text='单位', style=LB).pack(side='left')
        self.mv_unit = ttk.Combobox(r, width=13, state='readonly')
        self.mv_unit.pack(side='left', padx=(5, 0))
        self.mv_unit.bind('<<ComboboxSelected>>', lambda e: self._unit_sel_info())
        # ★2026-10-17：单位所属方（写记录 +352/+560 的 House 键）—— 默认沿用模板
        ttk.Label(r, text='所属', style=LB).pack(side='left', padx=(5, 0))
        self.mv_u_house = ttk.Combobox(r, width=13, state='readonly',
                                       values=(UNIT_HOUSE_INHERIT,))
        self.mv_u_house.current(0)
        self.mv_u_house.pack(side='left', padx=(5, 0))
        # ★2026-10-18：等级**只由 +0x150 决定**（+0x164 绝对不能碰）；默认「新兵」
        ttk.Label(r, text='等级', style=LB).pack(side='left', padx=(5, 0))
        self.mv_u_vet = ttk.Combobox(r, width=6, state='readonly',
                                     values=UNIT_VET_CHOICES)
        self.mv_u_vet.current(1)
        self.mv_u_vet.pack(side='left', padx=(5, 0))
        ttk.Button(r, text='放单位',
                   command=self.mv_putunit).pack(side='left', padx=(6, 0))
        # ★★§252：默认**不挪位** —— 你选中哪格就放哪格
        self.mv_u_nudge = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text='挪平地', variable=self.mv_u_nudge,
                        style='Surface.TCheckbutton').pack(side='left', padx=(5, 0))
        # ★§216：单位模板库（学习/查看）、刷单位 —— 低频，收进菜单
        _mb = ttk.Menubutton(r, text='更多 ▾', style='Ghost.TButton')
        _m = tk.Menu(_mb, tearoff=0)
        _m.add_command(label='🔫 刷单位（小队法）…', command=self.mv_spawn_team)
        _m.add_separator()
        _m.add_command(label='📚 学习模板（从本档学）', command=self.mv_learn_sig)
        _m.add_command(label='模板库…', command=self.mv_show_sig)
        _m.add_command(label='规则目录…', command=self.mv_pick_rules)
        _mb['menu'] = _m
        _mb.pack(side='left', padx=(5, 0))

        r = ttk.Frame(b3, style=SUF)
        r.pack(fill='x', pady=(4, 0))
        # ★2026-10-17 玩家定案：放置建筑默认走**触发法**（引擎自己造 ⇒ 有动画、模型完整、
        #   不可建地形会被拒）；★2026-10-21 不勾=直接落成也已游戏内验收。
        self.mv_via = tk.BooleanVar(value=True)
        ttk.Checkbutton(r, text='⚡ 触发法（引擎自己造）', variable=self.mv_via,
                        style='Surface.TCheckbutton').pack(side='left')
        ttk.Label(r, text='事件', style=LB).pack(side='left', padx=(8, 0))
        self.mv_ev = ttk.Combobox(r, width=13, state='readonly',
                                  values=('流逝时间 1 秒', '任何事件(立即)', '流逝时间 5 秒'))
        self.mv_ev.current(0)                       # ★默认 1 秒：实测唯一稳的那条
        self.mv_ev.pack(side='left', padx=(5, 0))
        # ★2026-10-22 §169/§183：修旧楼（放置时已自动播建造动画，这里只修"旧楼"）
        _mb2 = ttk.Menubutton(r, text='修旧楼 ▾', style='Ghost.TButton')
        _m2 = tk.Menu(_mb2, tearoff=0)
        _m2.add_command(label='⏩ 修成完成态（选中格）', command=self.mv_fix_anim)
        _m2.add_command(label='🔍 扫描全档卡住的建筑', command=self.mv_scan_anim)
        _m2.add_command(label='✨ 补齐建筑动画(anim)', command=self.mv_add_anim)
        _mb2['menu'] = _m2
        _mb2.pack(side='left', padx=(10, 0))
        ttk.Label(r, text='勾 = 引擎造 / 不勾 = 直接落成',
                  style=MU, font=SM).pack(side='left', padx=(10, 0))

        # ── ④ 选中格：单位级操作（按格，不做框选）★§268 ──
        _s, b4 = ui_theme.section(f, '选中格', '单位操作')

        r = ttk.Frame(b4, style=SUF)
        r.pack(fill='x')
        ttk.Button(r, text='📋 复制该格',
                   command=self.mv_copy_units).pack(side='left')
        ttk.Button(r, text='📌 粘贴到此格',
                   command=self.mv_paste_units).pack(side='left', padx=(PAD, 0))
        ttk.Button(r, text='➡ 把源格单位移到此格',
                   command=self.mv_move_units).pack(side='left', padx=(PAD, 0))
        ttk.Button(r, text='🗑 删除该格单位',
                   command=self.mv_delete_units).pack(side='left', padx=(PAD, 0))
        self.mv_clip = None
        self.mv_clip_lbl = ttk.Label(r, text='剪贴板：空', style=MU)
        self.mv_clip_lbl.pack(side='left', padx=(10, 0))

        cw = ttk.Frame(f)
        cw.pack(fill='both', expand=True, pady=(6, 4))
        self.mv_canvas = tk.Canvas(cw, bg=ui_theme.MAP['canvas'], highlightthickness=0)
        # ★§286：画布是深色，用专用深色滚动条（原来浅色 thumb 贴在深底上分不清）
        vs = ttk.Scrollbar(cw, orient='vertical', command=self.mv_canvas.yview,
                           style='Map.Vertical.TScrollbar')
        hs = ttk.Scrollbar(cw, orient='horizontal', command=self.mv_canvas.xview,
                           style='Map.Horizontal.TScrollbar')
        self.mv_canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.mv_canvas.grid(row=0, column=0, sticky='nsew')
        vs.grid(row=0, column=1, sticky='ns')
        hs.grid(row=1, column=0, sticky='ew')
        cw.rowconfigure(0, weight=1)
        cw.columnconfigure(0, weight=1)
        self.mv_canvas.bind('<Button-1>', self.mv_click)

        self.mv_info = ttk.Label(f, text='', foreground=ui_theme.T['ink'], justify='left',
                                 wraplength=700)   # ★§289：原来 980 > 工作区 722 ⇒ 被裁
        self.mv_info.pack(anchor='w')
        self.mv_state = {'mp': None, 'ok': False, 'scale': 8, 'img': None,
                         'blds': [], 'occ': {}, 'sel': None, 'path': None,
                         'gdata': None, 'wpdata': None, 'udata': None,
                         'marks': None, 'canv': None}
        return f

    def _mv_reset(self, lazy=False):
        """重置地图页。`lazy=True` ⇒ **只准备好，不渲染**（开档时用，切页再画，§272）。"""
        st = getattr(self, 'mv_state', None)
        if st is None:
            return
        # ★§290：换档 ⇒ 建筑类型列表要重填；如果玩家当前就停在本页（不会有切页事件），
        #   用 after_idle 立刻补上，免得下拉里还留着上一份档的类型
        self._mv_types_loaded = False
        try:
            if self.nb.index(self.nb.select()) == 6:
                self.after_idle(self._mv_ensure_types)
        except Exception:
            pass
        p = getattr(getattr(self.doc, '_map', None), 'path', st.get('path')) if self.doc else None
        st.update({'mp': None, 'ok': False, 'img': None, 'blds': [],
                   'occ': {}, 'sel': None, 'path': p, 'gdata': None, 'dims': None,
                   'wpdata': None, 'udata': None, 'canv': None, 'marks': None})
        self.mv_canvas.delete('all')
        self.mv_info.config(text='')
        if self.doc and not (p and os.path.exists(p)):
            # ★§285：没有 .MAP ⇒ 地形来自存档；同样推迟到切页时再渲染
            st['path'] = None
            if lazy:
                self.mv_deferred = True
                self._mv_defer_path = None
                try:
                    self.mv_lbl.config(text='没有 .MAP ｜ 切到本页时用**存档**画地形',
                                       foreground=ui_theme.T['warn'])
                except Exception:
                    pass
            else:
                try:
                    self.mv_load(None)
                except Exception:
                    pass
            return
        if self.doc and p and os.path.exists(p):
            if lazy:
                # ★§272：**连地图解析也推迟** —— `parse_map()` 对任务图要 10.3 秒，
                #   开档时解析等于白等（玩家多半先看触发表/单位）。
                st['path'] = p
                st['ok'] = False
                self.mv_deferred = True
                self._mv_defer_path = p
                self.mv_lbl.config(text='%s（切到本页再解析渲染，开档更快）'
                                        % os.path.basename(p), foreground=ui_theme.T['muted'])
            else:
                self.mv_load(p)
        else:
            self.mv_lbl.config(text='未加载地图 · 网格模式可点选'
                                   + self._mv_ready_text(),
                               foreground=ui_theme.T['muted'])
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

    def mv_load(self, path, lazy=False):
        """加载地图 —— ★2026-10-17 20:3x 玩家定案：**不强制匹配，也绝不拦人**。

        「地图不是必须的，不能因为匹配不上连打开都不行」⇒ 这里只做**提示**：
          · 匹配（尺寸一致 / 建筑高度重合 / 建筑全在图内）⇒ 绿字；
          · 疑似不匹配 ⇒ **橙字**说明原因，但地图照样画、选位照样点、放建筑照样放；
          · 地图本身解析失败 ⇒ 退回网格模式，红字说明（仍然不影响放建筑）。
        旧行为（`messagebox.showwarning` 弹框 + 不加载）已移除。
        """
        st = self.mv_state
        # ★§285：没有 .MAP（path 为空 / 文件不在了）也照样能画 —— 地形改用存档格对象。
        if path and not os.path.exists(str(path)):
            st['map_err'] = '地图文件不在了：%s' % os.path.basename(str(path))
            path = None
        if not path:
            st['map_err'] = st.get('map_err') or '没有 .MAP 文件'
        st['path'] = path
        st['map_err'] = None
        mode = self._mv_terr_mode()          # 'map' / 'merge' / 'save'
        theater = None
        if mode != 'save':
            try:
                import map_render as _mr0
                theater = _mr0.parse_map(path).get('theater')
            except Exception:
                theater = None
        if not theater:
            # 没有地图 ⇒ 用下拉里选的（默认 TEMPERATE：游戏自己的默认剧场）
            theater = (getattr(self, 'mv_theater', None)
                       or (self.mv_theater_var.get() if hasattr(self, 'mv_theater_var')
                           else None)
                       or 'TEMPERATE')
        self.mv_theater = theater
        try:
            self.mv_theater_var.set(theater)
        except Exception:
            pass
        st['gdata'] = None          # 换地图/换档 ⇒ 缓存的建筑/单位/路径点全部作废
        st['wpdata'] = None
        st['udata'] = None
        self.status.set('正在校验「地图 ↔ 存档」…')
        self.update_idletasks()
        try:
            mt = (self.doc.map_match(path) if path
                  else {'ok': False, 'msg': '无 .MAP（地形取自存档）'})
        except Exception as e:
            mt = {'ok': False, 'msg': '匹配校验没跑通（不影响使用）：%s' % e}
        import map_render as mr
        mp = None
        src = mode
        filled = 0
        if mode != 'save' and path:
            try:
                mp = mr.parse_map(path)
            except Exception as e:
                mp = None
                st['map_err'] = str(e)
        if mp is not None and mode == 'merge':
            # ★§280「地图+存档补格」：地图是底图（永远不变），存档只补地图里没有的格。
            #   本档实测：地图 42901 格 ⊆ 存档 43080 格，补 179 格（178 个 tile=0 + 1 个 138）。
            try:
                _sv = self.doc.terrain_from_save(theater)
            except Exception:
                _sv = None
            if _sv:
                _tr = dict(mp['terrain'])
                for _xy, _v in _sv['terrain'].items():
                    if _xy not in _tr:
                        _tr[_xy] = _v
                        filled += 1
                mp = dict(mp)
                mp['terrain'] = _tr
                mp['size'] = (max(mp['size'][0], _sv['size'][0]),
                              max(mp['size'][1], _sv['size'][1]))
                mp['has_terrain'] = True
        if mp is None:
            # ★★§278：地图读不到（或用户强制）⇒ 用**存档自己**的格对象重建地形。
            #   实测与 .MAP 渲染只差 1.10% 像素（差的那些是引擎挑的 tile 变体，见 docs §279）。
            try:
                mp = self.doc.terrain_from_save(theater)
                src = 'save'
            except Exception as e:
                st['ok'] = False
                st['mp'] = None
                self.mv_fit()              # 落回网格模式
                self.mv_lbl.config(text='地图读取失败，已退回网格模式（放建筑不受影响）：%s' % e,
                                   foreground=ui_theme.T['danger'])
                self.status.set('地图读取失败：%s' % e)
                return
        st['src'] = src
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
        _srctxt = ''
        if src == 'save':
            _srctxt = (' ｜ 地形取自**存档**（%s；剧场 %s —— 存档里没这个字段，'
                       '只影响配色，选位/放建筑不受影响）'
                       % ('没读到 .MAP: ' + st['map_err'] if st.get('map_err') else '你选的', theater))
            ok = False
        elif src == 'merge':
            _srctxt = ' ｜ 地形 = .MAP 底图 + 存档补 %d 格' % filled
        else:
            _srctxt = ' ｜ 地形取自 .MAP（与存档无关）'
        st['filled'] = filled
        _nm = os.path.basename(path) if path else '（无 .MAP）'
        # ★§289：卡内只留短状态（原来 50+ 字的拼接把这一行顶出窗口右缘）；
        #   完整口径（匹配消息 / 地形来源 / 就绪）进状态栏 +「说明 ▸」
        self.mv_lbl.config(text='%s%s' % (_nm, ' ✓' if ok else ' ⚠'),
                           foreground=ui_theme.T['ok'] if ok else ui_theme.T['warn'])
        # 状态栏只说"哪张图 / 对不对"；地形来源与就绪状态在卡内（下拉 + 说明）看得到
        self._map_full_text = '%s ｜ %s%s' % (_nm, mt.get('msg', ''), _srctxt)
        _srcshort = {'save': '地形：存档', 'merge': '地形：地图+存档补格',
                     'map': '地形：地图'}.get(src, '')
        self.status.set(' ｜ '.join(x for x in (_nm, mt.get('msg', ''), _srcshort) if x))
        if lazy:
            # ★§272：开档时不渲染（生成地形图 + PNG 要 9 秒），切到本页再画
            self.mv_deferred = True
            st['img'] = None
            self.mv_lbl.config(text='%s ✓' % _nm if ok else '%s ⚠' % _nm)
            self._map_full_text = '%s ｜ %s（切到本页时再渲染）' % (_nm, mt.get('msg', ''))
            self.status.set(self._map_full_text)
        else:
            self.mv_deferred = False
            self.mv_fit()
        self.mv_info.config(text=('点格子选位 → 上方「放置」选类型 → 放置；下方给出落位判定'
                                 + ('' if ok else '　⚠ 地图与存档的匹配只是提示，不影响任何操作')))
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

    def _mv_wp_points(self):
        """路径点两组数据 → `(地图定义的, 只有存档才有的)`，键是号、值是 (x, y)。

        ★为什么要分两组（§266）：
          · 地图 `[Waypoints]` 里声明的 = **地编里看得见**的那批。填「小队出现于」「运输机起始」
            「行为 80 的路径点」时要对的就是这些号；
          · 存档里的路径点数组（`waypoints_save()`，702 槽）是**引擎运行期**的，可能多出地图
            没定义的点（也可能是地图写了但存档没同步）。两组一起画，对不上一眼就看见。
        坐标空间相同（就是编辑器的格子坐标），不需要换算。
        """
        st = self.mv_state
        if st.get('wpdata') is not None:
            return st['wpdata']
        mp, sv = {}, {}
        path = st.get('path')
        if path:
            try:
                import editor_core as _ec
                mp = dict(_ec.read_scenario_waypoints(path).get('items') or {})
            except Exception:
                mp = {}
        if self.doc:
            try:
                sv = dict(self.doc.waypoints_save() or {})
            except Exception:
                sv = {}
        only = dict((n, c) for n, c in sv.items() if n not in mp)
        st['wpdata'] = (mp, only)
        return st['wpdata']

    def _mv_draw_waypoints(self, c):
        """画路径点。**以存档为准**（引擎读档走存档），地图 [Waypoints] 只作对照。

        ★★§SOV14（2026-11-04 玩家："把路径点 35 移动到 16,104 好像无效"）：
          以前绿圆画的是**地图 [Waypoints]** 的坐标 ⇒ 只改存档时圆点留在老地方，
          看着就像"没生效"。现在：
            · 绿底白字圆 = **存档里有的号**，画在**存档坐标**（引擎实际用的位置）；
            · 橙圈套在外面 + `号*` = 地图里同号但坐标不同（两边不一致，以绿圆为准）；
            · 灰空心圈 + 灰号 = **只有地图有、存档里没有**的号（填参数时看着有，引擎未必认）。
        """
        st = self.mv_state
        s = max(1, int(st.get('scale') or 8))
        try:
            W, H = (st['mp']['size'] if st.get('mp') else self._mv_dims())
        except Exception:
            W, H = 160, 160
        mp, _only = self._mv_wp_points()
        try:
            sv = dict(self.doc.waypoints_save() or {}) if self.doc else {}
        except Exception:
            sv = {}
        r = max(5.0, min(13.0, s * 0.62))
        fs = max(6, min(11, int(s * 0.52)))
        # ① 存档里的点（权威）
        for n, (x, y) in sorted(sv.items()):
            if not (0 <= x < W and 0 <= y < H):
                continue
            cx, cy = x * s + s / 2.0, y * s + s / 2.0
            c.create_oval(cx - r, cy - r, cx + r, cy + r,
                          fill=ui_theme.MAP['wp'], outline=ui_theme.T['on_dark'], width=1,
                          tags=('wp',))
            c.create_text(cx, cy, text=str(n), fill=ui_theme.T['on_dark'],
                          font=('Consolas', fs, 'bold'), tags=('wp',))
            p = mp.get(n)
            if p and (int(p[0]), int(p[1])) != (int(x), int(y)):
                c.create_oval(cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2,
                              outline=ui_theme.T['warn'], width=1, tags=('wp',))
                if s >= 8:
                    c.create_text(cx, cy - r - 8, text='%d*' % n,
                                  fill=ui_theme.T['warn'],
                                  font=('Consolas', 7), tags=('wp',))
        # ② 只有地图有、存档里没有的号（灰空心圈）
        _grey = ui_theme.T.get('muted_soft') or ui_theme.T['muted']
        for n, (x, y) in sorted(mp.items()):
            if n in sv:
                continue
            if not (0 <= x < W and 0 <= y < H):
                continue
            cx, cy = x * s + s / 2.0, y * s + s / 2.0
            c.create_oval(cx - r, cy - r, cx + r, cy + r,
                          outline=_grey, width=1, tags=('wp',))
            if s >= 10:
                c.create_text(cx, cy + r + 7, text='%d' % n, fill=ui_theme.T['muted'],
                              font=('Consolas', 7), tags=('wp',))


    # ---------------- ★§SOV12 单元标记（CellTag 挂接点）：显示 / 列表 / 删除 ----------------
    MV_MARK_PALETTE = ('#FF7A00', '#41D3FF', '#FFD400', '#8CFF41',
                       '#FF4FD8', '#B98CFF', '#FF5A5A', '#5CFFC8')

    def _mv_marks_data(self):
        """这条存档的全部单元标记（缓存在 `st['marks']`，换档时 st 会重建）。"""
        st = self.mv_state
        if st.get('marks') is not None:
            return st['marks']
        try:
            st['marks'] = self.doc.cell_markers() if self.doc else []
        except Exception:
            st['marks'] = []
        return st['marks']

    def _mv_mark_color_map(self, marks):
        """CellTag 键 → 颜色（同一个 CellTag 的标记同色，一眼看出属于哪条触发）。"""
        keys = sorted({m['cell_key'] for m in marks})
        pal = self.MV_MARK_PALETTE
        return {k: pal[i % len(pal)] for i, k in enumerate(keys)}

    def _mv_draw_markers(self, c):
        """画 CellTag 的挂接点（单元标记）。返回画了几个。"""
        try:
            if not (hasattr(self, 'mv_marks') and bool(self.mv_marks.get())):
                return 0
        except Exception:
            return 0
        st = getattr(self, 'mv_state', None) or {}
        s = max(1, int(st.get('scale') or 8))
        if s < 2:                     # 1 倍时一格只有 1 像素，画了也只是噪点
            return 0
        marks = self._mv_marks_data()
        if not marks or len(marks) > 5000:
            return 0
        col = self._mv_mark_color_map(marks)
        # 倍率小 ⇒ 只画 1~2px 的点；放大后补十字，密集区也能数清
        r = 1 if s <= 3 else (2 if s < 9 else 3)
        for m in marks:
            cx, cy = m['x'] * s + s / 2.0, m['y'] * s + s / 2.0
            cc = col.get(m['cell_key'], '#FF7A00')
            c.create_rectangle(cx - r, cy - r, cx + r, cy + r,
                               outline=cc, width=1, tags=('cellmark',))
            if s >= 9:                # 放大后补个十字，密集区也能数清
                c.create_line(cx - r - 2, cy, cx + r + 2, cy, fill=cc, tags=('cellmark',))
                c.create_line(cx, cy - r - 2, cx, cy + r + 2, fill=cc, tags=('cellmark',))
        return len(marks)

    def _mv_on_marks(self):
        """勾选变化 ⇒ **只重画标记层**（不重绘整图：大图全量要 2 秒以上）。"""
        try:
            c = self.mv_canvas
            c.delete('cellmark')
            self._mv_draw_markers(c)
            marks = self._mv_marks_data()
            tot = len(marks)
            n_tag = len({m['cell_key'] for m in marks})
            if not bool(self.mv_marks.get()):
                self.status.set('单元标记：已隐藏（叠加 ▾ 里可再打开）')
            elif tot and int(self.mv_state.get('scale') or 1) < 2:
                self.status.set('单元标记：共 %d 个（%d 条 CellTag）—— 当前倍率太小画不出，'
                                '放大到 2 倍以上就能看到' % (tot, n_tag))
            else:
                self.status.set('单元标记：显示 %d 个（%d 条 CellTag，同一 CellTag 同色）'
                                % (tot, n_tag))
        except Exception as e:
            self.status.set('切换单元标记失败：%s' % e)

    def mv_delete_markers(self):
        """删掉「选中格」上的单元标记（解挂 + 挂接数 −1，§SOV12）。"""
        st = getattr(self, 'mv_state', None)
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档', parent=self)
            return
        if not st or not st.get('sel'):
            messagebox.showinfo(
                '先选一格',
                '在地图上点一下目标格（或用「定位」跳到坐标），再删它的单元标记。',
                parent=self)
            return
        x, y = st['sel']
        try:
            ms = self.doc.cell_markers_at(x, y)
        except Exception as e:
            messagebox.showerror('读不到单元标记', str(e), parent=self)
            return
        if not ms:
            messagebox.showinfo(
                '这格没有单元标记',
                '(%d,%d) 上没有 CellTag 挂接点。\n'
                '（叠加 ▾ → 「单元标记（CellTag 挂接点）」可以在图上看到全部标记）' % (x, y),
                parent=self)
            return
        lines = ['(%d, %d) 上有 %d 个单元标记：' % (x, y, len(ms))]
        for m in ms:
            nm = m.get('trigger_name') or (m.get('trigger') or '?')
            lines.append('　· CellTag %08X ｜ 标签 %08X ｜ 触发「%s」｜ 挂接 %d → %d'
                         % (m['cell_key'], m['tag_key'], nm, m['count'],
                            max(0, m['count'] - 1)))
        lines += ['',
                  '做法：把这格的「CellClass +80」清 0（不再挂在这个 CellTag 上），'
                  '同时把 CellTag 的挂接数 −1；对象本身不删，存档长度不变。',
                  '只改存档、不写 .map —— 下次从地图重开任务，标记还会回来。',
                  '不可撤销 —— 建议先在主窗口「备份存档」。',
                  '',
                  '确定删除吗？']
        if not messagebox.askyesno('删除单元标记', '\n'.join(lines), parent=self):
            return
        ok, errs = [], []
        for i, m in enumerate(ms):
            try:
                ok.append(self.doc.clear_cell_marker(m, rescan=(i == len(ms) - 1)))
            except Exception as e:
                errs.append('CellTag %08X：%s' % (m.get('cell_key') or 0, e))
        st['marks'] = None
        try:
            self.mv_canvas.delete('cellmark')
            self._mv_draw_markers(self.mv_canvas)
        except Exception:
            pass
        try:
            self.mv_info.config(text=self._mv_info_text(x, y))
        except Exception:
            pass
        if errs:
            messagebox.showwarning('部分没删成', '\n'.join(errs), parent=self)
        if ok:
            self.status.set('已删除 (%d,%d) 的 %d 个单元标记（挂接数 %s → %s）'
                            % (x, y, len(ok),
                               '、'.join(str(i['count_before']) for i in ok),
                               '、'.join(str(i['count_after']) for i in ok)))

    def mv_list_markers(self):
        """列出全部单元标记：双击跳格、可多选删除（§SOV12）。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档', parent=self)
            return
        try:
            marks = self._mv_marks_data()
        except Exception as e:
            messagebox.showerror('读不到单元标记', str(e), parent=self)
            return
        if not marks:
            messagebox.showinfo('没有单元标记',
                                '这份存档里没有任何 CellTag 挂接点。', parent=self)
            return
        col = self._mv_mark_color_map(marks)
        dlg = tk.Toplevel(self)
        dlg.title('单元标记（CellTag 挂接点）· 共 %d 个' % len(marks))
        dlg.transient(self)
        dlg.minsize(560, 320)
        frm = ttk.Frame(dlg, padding=8)
        frm.pack(fill='both', expand=True)
        ttk.Label(frm, text='双击一行 = 跳到该格；选中多行可一次删除。'
                            '只改存档，不写 .map。',
                  foreground=ui_theme.T['muted']).pack(anchor='w', pady=(0, 6))
        wrap = ttk.Frame(frm)
        wrap.pack(fill='both', expand=True)
        tv = ttk.Treeview(wrap, columns=('x', 'y', 'cell', 'tag', 'trig', 'cnt'),
                          show='headings', height=16, selectmode='extended')
        for cid, txt, w in (('x', 'X', 50), ('y', 'Y', 50), ('cell', 'CellTag', 90),
                            ('tag', '标签', 90), ('trig', '触发', 200), ('cnt', '挂接', 55)):
            tv.heading(cid, text=txt)
            tv.column(cid, width=w, anchor='center' if cid != 'trig' else 'w')
        ys = ttk.Scrollbar(wrap, orient='vertical', command=tv.yview)
        tv.configure(yscrollcommand=ys.set)
        tv.pack(side='left', fill='both', expand=True)
        ys.pack(side='right', fill='y')
        for i, m in enumerate(marks):
            tv.insert('', 'end', iid=str(i), values=(
                m['x'], m['y'], '%08X' % m['cell_key'], '%08X' % m['tag_key'],
                m.get('trigger_name') or (m.get('trigger') or '?'), m['count']))
        row = ttk.Frame(frm)
        row.pack(side='bottom', fill='x', pady=(8, 0))

        def _goto(_ev=None):
            sel = tv.selection()
            if not sel:
                return
            m = marks[int(sel[0])]
            self.mv_goto(m['x'], m['y'])
            self.status.set('已跳到单元标记 (%d,%d) ｜ CellTag %08X ｜ %s'
                            % (m['x'], m['y'], m['cell_key'],
                               m.get('trigger_name') or '?'))
        tv.bind('<Double-1>', _goto)

        def _del():
            sel = tv.selection()
            if not sel:
                messagebox.showinfo('提示', '先选中要删的行', parent=dlg)
                return
            picked = [marks[int(i)] for i in sel]
            head = '\n'.join('　· (%d,%d) CellTag %08X ｜ %s'
                             % (m['x'], m['y'], m['cell_key'],
                                m.get('trigger_name') or '?')
                             for m in picked[:12])
            if len(picked) > 12:
                head += '\n　… 还有 %d 个' % (len(picked) - 12)
            if not messagebox.askyesno(
                    '删除 %d 个单元标记' % len(picked),
                    '将从对应 CellTag 上解挂（挂接数各 −1），只改存档：\n\n%s\n\n'
                    '不可撤销，建议先「备份存档」。确定删除吗？' % head,
                    parent=dlg):
                return
            okn = 0
            errs = []
            for i, m in enumerate(picked):
                try:
                    self.doc.clear_cell_marker(m, rescan=(i == len(picked) - 1))
                    okn += 1
                except Exception as e:
                    errs.append('(%d,%d) %s' % (m['x'], m['y'], e))
            self.mv_state['marks'] = None
            try:
                self.mv_canvas.delete('cellmark')
                self._mv_draw_markers(self.mv_canvas)
                self.mv_info.config(text=self._mv_info_text(*self.mv_state['sel'])
                                    if self.mv_state.get('sel') else '')
            except Exception:
                pass
            dlg.destroy()
            if errs:
                messagebox.showwarning('部分没删成', '\n'.join(errs[:8]), parent=self)
            self.status.set('已删除 %d 个单元标记%s' % (
                okn, '，%d 个失败' % len(errs) if errs else ''))

        ttk.Button(row, text='跳到该格（或双击行）', command=_goto).pack(side='left')
        ttk.Button(row, text='🗑 删除选中', command=_del).pack(side='left', padx=(8, 0))
        ttk.Button(row, text='关闭', command=dlg.destroy).pack(side='right')

    # ---------------- 地形来源（§280） ----------------
    TERR_SRC_MODES = ('地图', '地图+存档补格', '存档')

    def _mv_terr_mode(self):
        """当前地形来源档位 → `'map'` / `'merge'` / `'save'`（控件不在时按"地图"）。"""
        try:
            v = self.mv_terr_src.get()
        except Exception:
            v = '地图'
        if v.startswith('存档') and '补' not in v:
            return 'save'
        if '补' in v:
            return 'merge'
        return 'map'

    def _mv_on_theater(self):
        """★§285：换剧场只变配色（tile→颜色/贴图映射），格子数据不动 ⇒ 直接重载地形。"""
        if getattr(self, '_mv_terr_sync', False):
            return
        self.mv_theater = self.mv_theater_var.get()
        p = (getattr(self, 'mv_state', None) or {}).get('path')
        try:
            if p:
                self.mv_load(p)        # 有地图：地图的 [Basic] Theater 优先，这里只是提醒
            else:
                self.mv_load(None)
            self.status.set('剧场已切到 %s（只改配色，格子/选位/放置不受影响）' % self.mv_theater)
        except Exception as e:
            self.status.set('切剧场失败：%s' % e)

    def _mv_on_terr_src(self, *_):
        """下拉变了 ⇒ 同步旧 BooleanVar，然后重载地形。"""
        if getattr(self, '_mv_terr_sync', False):
            return
        self._mv_terr_sync = True
        try:
            self.mv_src_save.set(self._mv_terr_mode() == 'save')
        except Exception:
            pass
        finally:
            self._mv_terr_sync = False
        self._mv_reload_terrain()

    def _mv_on_src_save(self, *_):
        """旧 BooleanVar 被外部改动（自检/老脚本）⇒ 反写下拉。"""
        if getattr(self, '_mv_terr_sync', False):
            return
        self._mv_terr_sync = True
        try:
            if self.mv_src_save.get():
                self.mv_terr_src.set('存档')
            elif self._mv_terr_mode() == 'save':
                self.mv_terr_src.set('地图')
        except Exception:
            pass
        finally:
            self._mv_terr_sync = False

    def _mv_reload_terrain(self):
        """切换地形来源 ⇒ 重新装载地形（同一个地图文件不动）。"""
        p = (getattr(self, 'mv_state', None) or {}).get('path')
        if not self.doc:
            return
        try:
            self.mv_load(p)          # ★§285：p 可以为空（= 只用存档地形）
        except Exception as e:
            self.status.set('重载地形失败：%s' % e)

    def _mv_toggle_units(self):
        """勾上「单位」时，若缩放还没到 20 倍就自动放大到 20 倍（并说一句为什么）。"""
        st = self.mv_state
        if self.mv_units.get() and int(st.get('scale') or 8) < 20:
            st['scale'] = 20
            self.mv_render()
            try:
                self.status.set('单位标记只在 ≥20 倍显示（太小会糊成一片），已自动放大到 20 倍')
            except Exception:
                pass
            return
        self.mv_render()

    def _mv_units(self):
        """存档里的**单位**（步兵/载具实例记录）→ `[(x, y, 类型, 所属方)]`。

        ★数据源用 `doc.unit_records()`（单位实例记录，权威）而不是 `house_placements()`：
          后者是"建筑与单位混排"的布置表，在**测试/重开局的档里经常是空的**
          （实测 `SAVE5AF1` / `SAVE26E9` 都是 0 条），只有正儿八经的任务档才有内容。
          `unit_records()` 在 `光棱 飞碟干净.SAV` 上读出 1420 条（1365 条带坐标）、耗时 0.4 秒，
          其中 55 条没有坐标 = 模板/未部署的记录，直接丢掉。
        缓存到 `mv_state['udata']`，换图/换档时清。
        """
        st = self.mv_state
        if st.get('udata') is not None:
            return st['udata']
        out = []
        if self.doc:
            try:
                for r in (self.doc.unit_records() or []):
                    x, y = r.get('x'), r.get('y')
                    if x is None or y is None:
                        continue
                    out.append((int(x), int(y), str(r.get('type') or '?'),
                                str(r.get('house') or '')))
            except Exception:
                out = []
        st['udata'] = out
        return out

    def _mv_terr_text(self, x, y):
        """信息栏补充文字：该格有没有 Terrain 对象（树/石/路牌）。"""
        try:
            t = self.doc.terrain_at(x, y) if self.doc else None
        except Exception:
            t = None
        if not t:
            return ''
        return '　地形对象：%s（HP %s）' % (t.get('type'), t.get('hp'))

    def _mv_draw_terrain(self, c):
        """Terrain 对象（树 / 石头 / 路牌 / 花…）标记 —— 深绿圆点，勾选框「树/石」控制（默认关）。

        ★§281：树/石头是**独立对象类**（CLSID `ce2d270e…`），不是覆盖物、也不是地形贴图，
          但它**占格**（`+0x138` 的 `0x10` 位）⇒ 那格不能放单位/建筑。
          默认关是因为量大（大档 3334 棵），全画会盖住地形。
        """
        if not (self.mv_terr.get() if hasattr(self, 'mv_terr') else False):
            return 0
        st = self.mv_state
        s = int(st.get('scale') or 8)
        if s < 2:                      # 1 倍时一格 1 像素，画不出东西
            return 0
        try:
            ts = self.doc.terrain_objects()
        except Exception:
            return 0
        if not ts:
            return 0
        d = max(3.0, min(s - 1, s * 0.66))
        n = 0
        for t in ts:
            x, y = t.get('x'), t.get('y')
            if not isinstance(x, int) or not isinstance(y, int):
                continue
            cx, cy = x * s + s / 2.0, y * s + s / 2.0
            c.create_oval(cx - d / 2.0, cy - d / 2.0, cx + d / 2.0, cy + d / 2.0,
                          fill=ui_theme.MAP['terrain'], outline=ui_theme.MAP['terrain_line'], width=1, tags=('terr',))
            n += 1
        return n

    def _mv_draw_units(self, c):
        """单位标记（只在 **≥20 倍** 且勾了「单位」时画）：琥珀色小方块 + 白描边，同格多台标 ×N。

        ★为什么不按敌我上色：`houses()` 的 `is_player` 在部分档上不可靠 ——
          实测 `光棱 飞碟干净.SAV` 判成 `Russians`，可档里 1365 台单位**一台 Russians 都没有**
          （Alliance/Americans/YuriCountry），照它上色会全画成"敌方"。
          按玩家"编号别猜"的一贯要求，这里只标位置，不猜阵营。
        """
        st = self.mv_state
        s = int(st.get('scale') or 8)
        if s < 20:
            return 0
        if not (self.mv_units.get() if hasattr(self, 'mv_units') else False):
            return 0
        try:
            W, H = (st['mp']['size'] if st.get('mp') else self._mv_dims())
        except Exception:
            W, H = 160, 160
        us = self._mv_units()
        if not us:
            return 0
        per = {}
        for x, y, _t, _h in us:
            per[(x, y)] = per.get((x, y), 0) + 1
        r = max(6, min(11, int(s * 0.3)))
        n = 0
        for (x, y), cnt in per.items():
            if not (0 <= x < W and 0 <= y < H):
                continue
            cx, cy = x * s + s / 2.0, y * s + s / 2.0
            c.create_rectangle(cx - r, cy - r, cx + r, cy + r, fill=ui_theme.MAP['unit'],
                               outline=ui_theme.T['on_dark'], width=1, tags=('unit',))
            if cnt > 1:
                c.create_text(cx, cy - r - 6, text='×%d' % cnt, fill=ui_theme.T['ink'],
                              font=('Consolas', 8, 'bold'), tags=('unit',))
            n += 1
        return n

    def _mv_grid_safe(self):
        """网格模式的入口：任何异常都在画布上写清楚，绝不留黑屏。"""
        try:
            self._mv_render_grid()
        except Exception:
            import traceback
            try:
                self.mv_canvas.delete('all')
                self.mv_canvas.create_text(
                    16, 16, anchor='nw', fill=ui_theme.T['danger'], width=880, justify='left',
                    text='网格绘制出错（请把这段发给开发者）：\n\n' + traceback.format_exc()[-900:])
            except Exception:
                pass

    def _mv_render_grid(self):
        """**无地图模式**：按坐标范围画格子 —— 依然可以点选格子（触发法放置不需要地图）。"""
        st = self.mv_state
        c = self.mv_canvas
        W, H = self._mv_dims()
        st['canv'] = (int(W), int(H))       # ★§SOV11：网格模式的画布格子数
        s = max(2, int(st.get('scale') or 8))
        st['scale'] = s
        bld, unit, wp = self._mv_grid_data()
        c.delete('all')
        c.configure(scrollregion=(0, 0, W * s, H * s))
        c.create_rectangle(0, 0, W * s, H * s, fill=ui_theme.MAP['ground'], outline='')
        for x in range(0, W + 1):
            c.create_line(x * s, 0, x * s, H * s, fill=ui_theme.MAP['grid'] if x % 10 else ui_theme.MAP['grid_major'])
        for y in range(0, H + 1):
            c.create_line(0, y * s, W * s, y * s, fill=ui_theme.MAP['grid'] if y % 10 else ui_theme.MAP['grid_major'])
        for x in range(0, W, 10):
            c.create_text(x * s + 2, 2, text=str(x), anchor='nw',
                          fill=ui_theme.MAP['grid_text'], font=('Consolas', 7))
        for y in range(0, H, 10):
            c.create_text(2, y * s + 2, text=str(y), anchor='nw',
                          fill=ui_theme.MAP['grid_text'], font=('Consolas', 7))
        for (x, y), t in bld.items():
            if x < W and y < H:
                c.create_rectangle(x * s, y * s, (x + 1) * s, (y + 1) * s,
                                   fill=ui_theme.MAP['building'], outline='', stipple='gray50')
        # ★§267：单位标记改成与地图模式同一套画法（默认关 + ≥20 倍才画），
        #   原来"一直画蓝椭圆"的行为撤掉 —— 小比例下几百个单位糊成一片，看不出东西。
        # ★§266：路径点（与地图模式同一套画法：绿圆=地图定义，蓝框=只有存档有）
        if bool(self.mv_wp.get() if hasattr(self, 'mv_wp') else True):
            try:
                self._mv_draw_waypoints(c)
            except Exception:
                for n, (x, y) in wp.items():
                    if x < W and y < H:
                        c.create_text(x * s + s / 2, y * s + s / 2, text=str(n),
                                      fill=ui_theme.MAP['wp_text'], font=('Consolas', 7))
        try:
            self._mv_draw_markers(c)      # ★§SOV12：单元标记
        except Exception:
            pass
        try:
            self._mv_draw_terrain(c)
        except Exception:
            pass
        # §283：覆盖物标记（默认开；画在树/石之下、单位之上）
        try:
            self._mv_draw_overlays(c)
        except Exception:
            pass
        try:
            self._mv_draw_units(c)
        except Exception:
            pass
        try:
            self._mv_draw_sel()
        except Exception:
            pass
        try:
            self._mv_fill_wp_combo()
        except Exception:
            pass

    def _ovl_colors(self):
        """覆盖物索引 → 颜色（按类别，一次算好给整张图用）。

        ★颜色只按"**挡不挡路**"这一条分档（`overlay_blocks_placement` 是唯一判据）：
          金 = 矿/宝石（不挡）／深灰 = 一切挡路的（墙·沙袋·铁丝网·石头·`Land=Rock`）
          蓝 = 桥面·铁轨·低桥（改地块但能过）／浅灰 = 其它
        """
        out = {}
        try:
            info = self.doc.overlay_type_info(self._mv_rules_path()) or {}
        except Exception:
            info = {}
        for idx in info:
            try:
                blocks = self.doc.overlay_blocks_placement(idx, self._mv_rules_path())
            except Exception:
                blocks = False
            d = info.get(idx) or {}
            if d.get('tiberium'):
                col = ui_theme.MAP['ore']          # 矿 / 宝石
            elif blocks:
                col = ui_theme.MAP['block']          # 挡路的一律深灰
            elif d.get('overrides') or d.get('land'):
                col = ui_theme.MAP['bridge']          # 桥面 / 铁轨 / 低桥（改地块但能走）
            else:
                col = ui_theme.T['muted_soft']          # 其它
            out[idx] = col
        return out

    def _mv_draw_overlays(self, c):
        """覆盖物标记（§283）—— 勾了「覆盖物」就画，画在树/石之下、单位之上。

        数据源 = 格对象 `+0x58`（存档自己记的），绝不从 .MAP 猜。
        颜色见 `_ovl_colors()`：金=矿/宝石、深灰=墙/沙袋/铁丝网、褐=石头、蓝=桥面/铁轨。
        1 倍时一格 1 像素画不出东西，`scale < 3` 直接不画。
        """
        if not (self.mv_ovl.get() if hasattr(self, 'mv_ovl') else True):
            return 0
        st = self.mv_state
        s = int(st.get('scale') or 8)
        if s < 3:
            return 0
        try:
            cells = self.doc.overlay_cells()
        except Exception:
            return 0
        if not cells:
            return 0
        try:
            W, H = (st['mp']['size'] if st.get('mp') else self._mv_dims())
        except Exception:
            W, H = 160, 160
        cols = self._ovl_colors()
        d = max(2.0, min(s - 1, s * 0.5))
        n = 0
        for (x, y), idx in cells.items():
            if not (0 <= x < W and 0 <= y < H):
                continue
            col = cols.get(idx, ui_theme.T['muted_soft'])
            cx, cy = x * s + s / 2.0, y * s + s / 2.0
            if col == ui_theme.MAP['block']:            # 墙类：实心方块（一眼看出"这格不能放"）
                c.create_rectangle(cx - d / 2.0, cy - d / 2.0, cx + d / 2.0, cy + d / 2.0,
                                   fill=col, outline=ui_theme.MAP['outline_soft'], width=1, tags=('ovl',))
            else:                            # 其它：圆点
                c.create_oval(cx - d / 2.0, cy - d / 2.0, cx + d / 2.0, cy + d / 2.0,
                              fill=col, outline=ui_theme.T['on_dark'] if col == ui_theme.MAP['ore'] else '',
                              width=1, tags=('ovl',))
            n += 1
        return n

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
        # ★★§SOV11：选中格**不再烘焙进位图**（原来每次点格子都要整图重渲染：
        #   509×493 @10 倍 = 2.5 秒）。现在选中框是画布图元，点选/跳转只更新这一层。
        W, H, buf, meta = mr.render_rgb(st['mp'], st['blds'], st['scale'],
                                        sel=None, grid=bool(self.mv_grid.get()))
        st['canv'] = (int(W), int(H))       # ★§SOV11：居中/越界要用**这块画布**的格子数
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
        # ★§266：路径点叠加（勾选框控制；画在地形图之上，缩放/重绘自动跟随）
        if bool(self.mv_wp.get() if hasattr(self, 'mv_wp') else True):
            try:
                self._mv_draw_waypoints(c)
            except Exception:
                pass
        # ★§SOV12：单元标记（CellTag 挂接点，默认开；同一 CellTag 同色）
        try:
            self._mv_draw_markers(c)
        except Exception:
            pass
        # ★§281：树/石标记（默认关；画在路径点之上、单位之下）
        try:
            self._mv_draw_terrain(c)
        except Exception:
            pass
        # ★§283：覆盖物标记（默认开；画在树/石之下、单位之上）
        try:
            self._mv_draw_overlays(c)
        except Exception:
            pass
        # ★§267：单位标记（默认关；≥20 倍才画，画在路径点之上）
        try:
            self._mv_draw_units(c)
        except Exception:
            pass
        c.configure(scrollregion=(0, 0, W * st['scale'], H * st['scale']))
        try:
            self._mv_draw_sel()
        except Exception:
            pass
        # ★§SOV11：路径点下拉的候选 = 本图 [Waypoints] ∪ 存档里的号（渲染后必已就绪）
        try:
            self._mv_fill_wp_combo()
        except Exception:
            pass

    def _mv_view_center_cell(self):
        """视口正中那一格（缩放的锚点用）；算不出来返回 None。"""
        st = getattr(self, 'mv_state', None) or {}
        c = getattr(self, 'mv_canvas', None)
        if c is None:
            return None
        s = self._mv_scale_now()
        W, H = self._mv_cell_space()
        try:
            cx = c.canvasx(max(1, c.winfo_width()) / 2.0)
            cy = c.canvasy(max(1, c.winfo_height()) / 2.0)
        except Exception:
            return None
        x, y = int(cx // s), int(cy // s)
        if 0 <= x < W and 0 <= y < H:
            return (x, y)
        return None

    def mv_zoom(self, d):
        st = getattr(self, 'mv_state', None)
        if not st or not st['ok']:
            return
        # ★§SOV11：缩放前记住视口正中那格，缩放后把它放回正中 ——
        #   否则每点一次 ± 视图就漂回左上，找远处的格子等于白找。
        anchor = self._mv_view_center_cell()
        st['scale'] = max(1, min(24, st['scale'] + d))
        # 全量重绘在大图上要 2~4 秒（纯 Python 逐像素）⇒ 先给一句"正在画"，
        # 免得玩家以为点空了又去点（§SOV11）
        try:
            self.status.set('正在按 %d 倍重绘地图…' % st['scale'])
            self.update_idletasks()
        except Exception:
            pass
        self.mv_render()
        if anchor:
            self._mv_center_on(*anchor)
        try:
            self.status.set('缩放 = 每格 %d 像素（滚轮/± 再调；「适应窗口」一键铺满）'
                            % st['scale'])
        except Exception:
            pass

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

    def _mv_draw_sel(self):
        """只画「选中格」这一层（画布图元，tag='selbox'）。

        ★§SOV11：以前选中格是烘焙进位图的（`render_rgb(sel=…)`），于是每点一格都要
        整图重渲染 —— 509×493 @10 倍要 2.5 秒。改成图元后点选/跳转是毫秒级。
        """
        c = getattr(self, 'mv_canvas', None)
        st = getattr(self, 'mv_state', None) or {}
        if c is None:
            return
        try:
            c.delete('selbox')
        except Exception:
            pass
        sel = st.get('sel')
        if not sel:
            return
        s = self._mv_scale_now()
        x, y = sel
        try:
            c.create_rectangle(x * s, y * s, (x + 1) * s, (y + 1) * s,
                               outline='#FFD400', width=2, tags=('selbox',))
            c.create_rectangle(x * s + 1, y * s + 1, (x + 1) * s - 1, (y + 1) * s - 1,
                               outline='#1E78C8', width=1, tags=('selbox',))
        except Exception:
            pass

    def _mv_refresh_sel(self):
        """更新选中格但不重绘整图（点选 / 跳转走这条）；还没渲染过才回落全量。"""
        st = getattr(self, 'mv_state', None) or {}
        if st.get('ok') and st.get('mp'):
            if st.get('img') is None:
                self.mv_render()
                return
        elif not st.get('canv'):
            self._mv_grid_safe()
            return
        self._mv_draw_sel()

    # ---------------- ★§SOV11（2026-11-04）按坐标 / 路径点定位并居中 ----------------
    def _mv_cell_space(self):
        """当前视图的格子范围 `(W, H)` —— 必须与 `scrollregion` / 渲染用的是同一套。

        优先取上一次渲染实际用的画布格子数（`canvas_size()` 会被建筑/地形撑大，
        不一定等于地图声明的 `size`，用错了居中就会偏）。
        """
        st = getattr(self, 'mv_state', None) or {}
        if st.get('canv'):
            try:
                W, H = st['canv']
                return int(W), int(H)
            except Exception:
                pass
        if st.get('ok') and st.get('mp'):
            try:
                W, H = st['mp']['size']
                return int(W), int(H)
            except Exception:
                pass
        try:
            W, H = self._mv_dims()
        except Exception:
            W, H = 160, 160
        return int(W), int(H)

    def _mv_scale_now(self):
        """当前每格像素数（渲染用同一套口径，别各算各的）。"""
        st = getattr(self, 'mv_state', None) or {}
        s = int(st.get('scale') or 8)
        return max(1, s) if (st.get('ok') and st.get('mp')) else max(2, s)

    def _mv_fill_wp_combo(self):
        """把路径点号填进「路径点」下拉（本图 [Waypoints] ∪ 存档里的号）。"""
        cb = getattr(self, '_cb_mv_gwp', None)
        if cb is None:
            return
        try:
            mp, only = self._mv_wp_points()
        except Exception:
            return
        vals = [str(n) for n in sorted(set(mp) | set(only))]
        try:
            cur = list(cb.cget('values') or [])
            if cur != vals:
                cb['values'] = vals
        except Exception:
            try:
                cb['values'] = vals
            except Exception:
                pass

    def _mv_center_on(self, x, y):
        """把画布滚到让 (x,y) 落在视口正中（整图比视口小 ⇒ 贴左上，滚也无意义）。"""
        st = getattr(self, 'mv_state', None) or {}
        c = getattr(self, 'mv_canvas', None)
        if c is None:
            return
        try:
            c.update_idletasks()
        except Exception:
            pass
        s = self._mv_scale_now()
        W, H = self._mv_cell_space()
        tw, th = W * s, H * s
        vw = max(1, c.winfo_width())
        vh = max(1, c.winfo_height())
        cx, cy = (x + 0.5) * s, (y + 0.5) * s
        left = 0 if tw <= vw else max(0, min(tw - vw, cx - vw / 2.0))
        top = 0 if th <= vh else max(0, min(th - vh, cy - vh / 2.0))
        try:
            # scrollregion = (0,0,tw,th) ⇒ moveto 的参数是「左边界 / 总宽」
            c.xview_moveto(0.0 if tw <= vw else left / float(tw))
            c.yview_moveto(0.0 if th <= vh else top / float(th))
        except Exception:
            pass

    def mv_center_sel(self):
        """把视图重新居中到当前选中格（拖远了想回来用这个）。"""
        st = getattr(self, 'mv_state', None) or {}
        if not (st.get('sel')):
            self.status.set('先在地图上点一格，或在 X / Y 里填坐标后点「跳到格」')
            return
        x, y = st['sel']
        self._mv_center_on(x, y)
        self.status.set('视图已居中到 (%d,%d)' % (x, y))

    def mv_goto(self, x, y, why=''):
        """选中 (x,y) 并居中；越界/没开档返回 False（由调用方给提示）。"""
        st = getattr(self, 'mv_state', None)
        if not st:
            return False
        if not self.doc:
            self.status.set('先打开一个存档')
            return False
        W, H = self._mv_cell_space()
        if not (0 <= int(x) < W and 0 <= int(y) < H):
            self.status.set('%s 超出范围：当前这张图是 %d×%d（x 0..%d，y 0..%d）'
                            % (why or '目标坐标', W, H, W - 1, H - 1))
            return False
        x, y = int(x), int(y)
        st['sel'] = (x, y)
        self._mv_refresh_sel()          # ★只换选中框：跳转不再等 2 秒重绘
        self._mv_center_on(x, y)
        # 跳转后画个醒目的十字准星（下次重绘就清掉 —— 正好只标"这一次跳转"）
        try:
            s = self._mv_scale_now()
            c = self.mv_canvas
            cx, cy = (x + 0.5) * s, (y + 0.5) * s
            r = max(9.0, s * 1.8)
            c.create_line(cx - r, cy, cx + r, cy, fill='#FFD400', width=2, tags=('goto',))
            c.create_line(cx, cy - r, cx, cy + r, fill='#FFD400', width=2, tags=('goto',))
            c.create_oval(cx - r * 0.55, cy - r * 0.55, cx + r * 0.55, cy + r * 0.55,
                          outline='#FFD400', width=2, tags=('goto',))
        except Exception:
            pass
        try:
            self.mv_gx.set(str(x))
            self.mv_gy.set(str(y))
        except Exception:
            pass
        try:
            self.mv_info.config(text=self._mv_info_text(x, y))
        except Exception:
            pass
        return True

    def mv_goto_xy(self):
        """「跳到格」：按 X / Y 框里的坐标定位。"""
        if not getattr(self, 'mv_state', None):
            return
        if not self.doc:
            self.status.set('先打开一个存档')
            return
        tx = (self.mv_gx.get() or '').strip()
        ty = (self.mv_gy.get() or '').strip()
        try:
            x, y = int(tx), int(ty)
        except Exception:
            self.status.set('X / Y 要填整数（现在是 “%s” / “%s”）' % (tx, ty))
            return
        W, H = self._mv_cell_space()
        if self.mv_goto(x, y, '坐标 (%d,%d)' % (x, y)):
            self.status.set('已跳到 (%d,%d)　当前图 %d×%d' % (x, y, W, H))

    def mv_goto_wp(self):
        """「跳到点」：按路径点号定位（先查地图定义的，再查只有存档里的）。"""
        if not getattr(self, 'mv_state', None):
            return
        if not self.doc:
            self.status.set('先打开一个存档')
            return
        raw = (self.mv_gwp.get() or '').strip()
        if not raw:
            self.status.set('填一个路径点号（也可以从下拉里选）')
            return
        try:
            n = int(raw.split()[0])
        except Exception:
            self.status.set('路径点号要填数字（现在是 “%s”）' % raw)
            return
        try:
            mp, only = self._mv_wp_points()
        except Exception:
            mp, only = {}, {}
        where, pos = '地图定义', mp.get(n)
        if pos is None:
            pos, where = only.get(n), '只有存档里'
        if pos is None:
            have = sorted(set(mp) | set(only))
            self.status.set(
                '没有 %d 号路径点（本图/本档共 %d 个：%s%s）'
                % (n, len(have), ','.join(str(v) for v in have[:14]),
                   ' …' if len(have) > 14 else ''))
            return
        x, y = pos
        if self.mv_goto(x, y, '路径点 %d' % n):
            self.status.set('路径点 %d（%s）在 (%d,%d)，视图已居中' % (n, where, x, y))

    def _mv_mark_text(self, x, y):
        """该格的单元标记说明（没算过且显示关着就返回空，别为了一句话全档扫一遍）。"""
        st = getattr(self, 'mv_state', None) or {}
        marks = st.get('marks')
        if marks is None:
            try:
                if not (hasattr(self, 'mv_marks') and bool(self.mv_marks.get())):
                    return ''
            except Exception:
                return ''
            marks = self._mv_marks_data()
        mk = [m for m in marks if (m['x'], m['y']) == (x, y)]
        if not mk:
            return ''
        bits = []
        for m in mk[:4]:
            bits.append('%08X → %s（挂 %d）'
                        % (m['cell_key'], m.get('trigger_name') or m.get('trigger') or '?',
                           m['count']))
        more = '　等 %d 个' % len(mk) if len(mk) > 4 else ''
        return '　★单元标记：%d 个（%s%s）' % (len(mk), '、'.join(bits), more)

    def mv_new_wp(self):
        """「＋ 新建点」：在 X / Y 框（或选中格）上**新建**一个路径点（§SOV38）。

        号怎么定：
          · 「路径点」框里有号、且那个号当前是空的 ⇒ **就用它**（想指定号时先填它）；
          · 没填 / 那号已被占用 ⇒ 自动挑空号：优先「当前最大号 +1」，再退回最小的空号。

        底层还是 `SaveDoc.ensure_waypoint_at()` + `set_waypoint()`：**原地写 4 字节**，
        存档长度不变；`.MAP` 那份一律不动（本项目铁律：路径点只写存档）。
        该格**已经有**路径点时不会重复建 —— 直接跳过去并告知是几号。
        """
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档', parent=self)
            return
        W, H = self._mv_cell_space()
        # 目标格：优先 X / Y 框（点地图会自动回填，可手改成精确坐标），其次选中格
        tx = (self.mv_gx.get() or '').strip()
        ty = (self.mv_gy.get() or '').strip()
        x = y = None
        if tx or ty:
            try:
                x, y = int(tx), int(ty)
            except Exception:
                self.status.set('X 和 Y 要一起填整数（现在是 “%s” / “%s”）' % (tx, ty))
                return
        if x is None:
            sel = (getattr(self, 'mv_state', None) or {}).get('sel')
            if sel:
                x, y = int(sel[0]), int(sel[1])
        if x is None:
            self.status.set('先在地图上点一格，或在 X / Y 里填坐标，再点「＋ 新建点」')
            return
        if not (0 <= x < W and 0 <= y < H):
            self.status.set('坐标 (%d,%d) 超出范围：当前这张图是 %d×%d（x 0..%d，y 0..%d）'
                            % (x, y, W, H, W - 1, H - 1))
            return
        # ① 这格已经有路径点？不重复建（同格多点没意义，引擎也只用最小编号那个）
        try:
            same = sorted(n for n, c in (self.doc.waypoints_save() or {}).items()
                          if tuple(c) == (x, y))
        except Exception:
            same = []
        if same:
            n = same[0]
            try:
                self.mv_gwp.set(str(n))
            except Exception:
                pass
            self.mv_goto(x, y, '路径点 %d' % n)
            self.status.set('(%d,%d) 已经有路径点了（%s 号）⇒ 不重复新建'
                            % (x, y, '、'.join(str(v) for v in same)))
            return
        # ② 想用的号（可以不给）
        want = None
        raw = (self.mv_gwp.get() or '').strip()
        if raw:
            try:
                cand = int(raw.split()[0])
            except Exception:
                cand = None
            if cand is None:
                self.status.set('「路径点」框里要填数字（现在是 “%s”），或者清空让它自动挑号'
                                % raw)
                return
            if not (0 <= cand < self.doc.WP_COUNT):
                self.status.set('路径点号要 0..%d（现在是 %d）'
                                % (self.doc.WP_COUNT - 1, cand))
                return
            want = cand
        lim = self.doc.waypoint_limit()
        try:
            n = self.doc.ensure_waypoint_at(x, y, prefer=want)
        except Exception as e:
            messagebox.showerror('新建不了', str(e), parent=self)
            return
        if n is None:
            messagebox.showerror(
                '新建不了',
                '路径点表已经满了（0..%d 全被占用），没有空号可用。\n\n'
                '先把不用的点清掉（把它的坐标写成 0）再新建。' % lim,
                parent=self)
            return
        # ③ 让所有缓存与视图跟上（路径点表变了）
        st = getattr(self, 'mv_state', None) or {}
        st['wpdata'] = None
        st['gdata'] = None
        try:
            self._mv_fill_wp_combo()
        except Exception:
            pass
        try:
            self.mv_canvas.delete('wp')
            if bool(getattr(self, 'mv_wp', None) and self.mv_wp.get()):
                self._mv_draw_waypoints(self.mv_canvas)
        except Exception:
            pass
        try:
            self.mv_gwp.set(str(n))
        except Exception:
            pass
        self.mv_goto(x, y, '新路径点 %d' % n)
        extra = []
        if want is not None and n != want:
            extra.append('%d 号已经被占了，改用空号 %d' % (want, n))
        try:
            mmax = self.doc.map_waypoint_max()
        except Exception:
            mmax = None
        if mmax is not None and n > mmax:
            extra.append('地图 [Waypoints] 里没有这一号 —— 只写进存档，游戏内照样可用')
        self.status.set('已新建 %d 号路径点 @ (%d,%d)%s　→ 记得「保存」写盘'
                        % (n, x, y, ('　（' + '；'.join(extra) + '）') if extra else ''))

    def mv_set_wp_xy(self):
        """把「路径点」框里那号路径点改到 X / Y 框的坐标（只写存档，§SOV13）。

        底层是 `SaveDoc.set_waypoint()`：**原地改 4 字节**（x、y 各 u16），
        存档长度不变、写完立刻回读校验。`.MAP` 那份一律不动 ——
        本项目铁律：路径点只写存档，地图上的点请玩家自己在地编里改。
        """
        st = getattr(self, 'mv_state', None)
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档', parent=self)
            return
        raw = (self.mv_gwp.get() or '').strip()
        if not raw:
            messagebox.showinfo('先选一个路径点',
                                '在「路径点」框里填号（或从下拉里选），再点「✏ 写到 X/Y」。',
                                parent=self)
            return
        try:
            n = int(raw.split()[0])
        except Exception:
            self.status.set('路径点号要填数字（现在是 “%s”）' % raw)
            return
        if not (0 <= n < self.doc.WP_COUNT):
            self.status.set('路径点号要 0..%d（现在是 %d）' % (self.doc.WP_COUNT - 1, n))
            return
        tx = (self.mv_gx.get() or '').strip()
        ty = (self.mv_gy.get() or '').strip()
        try:
            x, y = int(tx), int(ty)
        except Exception:
            self.status.set('X / Y 要填整数（现在是 “%s” / “%s”）' % (tx, ty))
            return
        W, H = self._mv_cell_space()
        if not (0 <= x < W and 0 <= y < H):
            self.status.set('坐标 (%d,%d) 超出范围：当前这张图是 %d×%d（x 0..%d，y 0..%d）'
                            % (x, y, W, H, W - 1, H - 1))
            return
        try:
            old = self.doc.waypoint_cell(n)
        except Exception:
            old = None
        map_pos = None                       # 地图那份只作对照（绝不写）
        try:
            _mp, _only = self._mv_wp_points()
            map_pos = _mp.get(n)
        except Exception:
            pass
        extra = []
        if map_pos and (int(map_pos[0]), int(map_pos[1])) != (x, y):
            extra.append('地图 [Waypoints] 里 %d 号在 (%d,%d) —— 本工具只写存档，'
                         '改完两边会不一致（地图那份请你自己在地编里改）。'
                         % (n, map_pos[0], map_pos[1]))
        if old is None:
            extra.append('这一号原来没有坐标 ⇒ 等于新建一个路径点。')
        if not messagebox.askyesno(
                '改 %d 号路径点' % n,
                '把 %d 号路径点从 %s 改到 (%d, %d)？\n\n'
                '只写存档的路径点表（原地 4 字节，存档长度不变），不写 .map。\n'
                '不可撤销 —— 建议先「备份存档」。%s'
                % (n, ('(%d, %d)' % tuple(old)) if old else '（空）', x, y,
                   ''.join('\n\n' + e for e in extra)),
                parent=self):
            return
        try:
            res = self.doc.set_waypoint(n, x, y)
        except Exception as e:
            messagebox.showerror('写不进去', str(e), parent=self)
            return
        st['wpdata'] = None                  # 路径点缓存要重算（这号坐标变了）
        try:
            self._mv_fill_wp_combo()
        except Exception:
            pass
        try:
            self.mv_canvas.delete('wp')
            if bool(getattr(self, 'mv_wp', None) and self.mv_wp.get()):
                self._mv_draw_waypoints(self.mv_canvas)
        except Exception:
            pass
        self.mv_goto(res[0], res[1])         # 跳到新位置，让玩家看到
        self.status.set('已把 %d 号路径点写到 (%d,%d)%s%s'
                        % (n, res[0], res[1],
                           '（原来 (%d,%d)）' % tuple(old) if old else '（新建）',
                           '　⚠ 与地图 [Waypoints] 不一致（只改了存档）'
                           if (map_pos and (int(map_pos[0]), int(map_pos[1])) != res)
                           else ''))

    def _mv_info_text(self, x, y):
        """选中格的信息栏文本 —— 鼠标点选与「定位」共用（§SOV11 抽出来，避免两套文案）。"""
        import map_render as mr
        st = getattr(self, 'mv_state', None) or {}
        head = '选中格 (%d,%d)' % (x, y)
        try:
            if self.doc and self.doc.cell_visible(x, y, map_path=st.get('path')) is False:
                head += '　⚠ 在蓝线外（游戏里不显示这块地图）'
        except Exception:
            pass
        try:
            head += self._mv_terr_text(x, y)
        except Exception:
            pass
        try:
            head += self._mv_ovl_text(x, y)
        except Exception:
            pass
        if not (st.get('ok') and st.get('mp')):
            # 无地图模式：建筑 / 单位 / 路径点全来自存档
            try:
                bld, unit, wp = self._mv_grid_data()
            except Exception:
                bld, unit, wp = {}, {}, {}
            head += ('　建筑：%s' % bld[(x, y)]) if (x, y) in bld else '　建筑：无'
            if (x, y) in unit:
                head += '　单位：%s' % unit[(x, y)]
            wps = [n for n, c in wp.items() if c == (x, y)]
            if wps:
                head += '　路径点：%s' % ','.join(str(n) for n in wps)
            try:
                head += self._mv_mark_text(x, y)
            except Exception:
                pass
            W, H = self._mv_cell_space()
            head += ('\n（未挂地图：按存档坐标 %d×%d 画网格；放置建筑可直接用触发法，'
                     '不依赖地图）' % (W, H))
            return head
        # 有地图：路径点（两组）/ 单位 / 该格建筑详情 + 落位判定
        # ★§SOV14：路径点**以存档为准**报（地图那份只作对照），否则改完存档点这一格
        #   会说"没有路径点"，和画布上的绿圆对不上。
        try:
            sv = dict(self.doc.waypoints_save() or {}) if self.doc else {}
            mp_w, _only = self._mv_wp_points()
            here = [n for n, cc in sorted(sv.items())
                    if (int(cc[0]), int(cc[1])) == (x, y)]
            if here:
                head += '　路径点（存档）：%s' % ','.join(str(n) for n in here)
                diff = [n for n in here if n in mp_w
                        and (int(mp_w[n][0]), int(mp_w[n][1])) != (x, y)]
                if diff:
                    head += '　⚠ 地图里这些号在别处：%s' % '、'.join(
                        '%d(%d,%d)' % (n, int(mp_w[n][0]), int(mp_w[n][1]))
                        for n in diff[:3])
            onlymap = [n for n, cc in sorted(mp_w.items())
                       if (int(cc[0]), int(cc[1])) == (x, y) and n not in sv]
            if onlymap:
                head += '　路径点（只有地图有、存档没有）：%s' % ','.join(
                    str(n) for n in onlymap)
        except Exception:
            pass
        try:
            head += self._mv_mark_text(x, y)
        except Exception:
            pass
        try:
            us = [t for (ux, uy, t, _h) in self._mv_units() if (ux, uy) == (x, y)]
            if us:
                show = '、'.join(us[:6])
                if len(us) > 6:
                    show += ' …（共 %d 台）' % len(us)
                head += '　单位：%s' % show
        except Exception:
            pass
        b = (st.get('occ') or {}).get((x, y))
        if b:
            head += ('　该格建筑：%s（耐久 %d，所属方指针 0x%08X，挂接键 0x%08X，对象 @0x%X）'
                     % (b['type'], b['hp'], b['house'], b['tag'], b['base']))
        else:
            head += '　空（无建筑）'
        try:
            msgs = mr.check_cell(st['mp'], st['blds'], x, y)
        except Exception:
            msgs = []
        return head + ('\n' + '\n'.join(msgs) if msgs else '')

    def mv_click(self, event):
        """点画布 → 选中该格（信息栏与「定位」共用同一套文案，§SOV11）。"""
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
            self._mv_refresh_sel()
        else:
            s = st['scale']
            x = int(self.mv_canvas.canvasx(event.x) // s)
            y = int(self.mv_canvas.canvasy(event.y) // s)
            st['sel'] = (x, y)
            self._mv_refresh_sel()
        # ★§SOV11：坐标回填到「定位」的两个输入框 —— 点一格就能看到精确坐标，
        #   想微调/复制也不必再算（拖到别处后点「居中选中」也能回来）。
        try:
            self.mv_gx.set(str(x))
            self.mv_gy.set(str(y))
        except Exception:
            pass
        self.mv_info.config(text=self._mv_info_text(x, y))

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
        # ★2026-10-28：改用 `modified()`（比 `dirty()` 可靠 —— 后者漏报直接改明文的操作）
        dirty = self._is_modified()
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

    @classmethod
    def _cfg_path(cls):
        """配置文件放 `%APPDATA%\\RA2SED\\editor_config.json`。

        ★2026-10-28（审查 P4-21）：原来写在**程序目录**，装在 `Program Files` 下会因
          无写权限而静默失败（`except: pass`）—— 玩家的"记住游戏目录"就永远不生效。
          老版本留在程序目录的同名文件会在第一次读不到时自动迁移过来。
        """
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
        d = os.path.join(base, 'RA2SED')
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            return os.path.join(cls._app_dir(), 'editor_config.json')
        p = os.path.join(d, 'editor_config.json')
        if not os.path.exists(p):
            # 迁移老位置（程序目录）里的配置，别让老用户丢设置
            old = os.path.join(cls._app_dir(), 'editor_config.json')
            if os.path.exists(old):
                try:
                    with open(old, encoding='utf-8') as f:
                        data = json.load(f)
                    with open(p, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
        return p

    def _cfg_get(self, key, default=None):
        try:
            with open(self._cfg_path(), encoding='utf-8') as f:
                return json.load(f).get(key, default)
        except Exception:
            return default

    def _cfg_set(self, key, val):
        p = self._cfg_path()
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

    def _sync_game_dir(self, persist=False):
        """把界面上的「游戏目录」交给 core —— rules/art 的定位全看这个。

        ★2026-10-28（审查 P0-4）：core 以前只认写死的开发者路径，玩家装在别处时
          可建造判定 / Foundation / Power 会**静默失效**。现在由界面统一告知。
        `persist=True` 时顺便写进配置（下次启动自动带出来）。
        """
        d = (self.game_dir.get() or '').strip()
        try:
            SaveDoc.set_game_dir(d)
        except Exception:
            pass
        if persist and d:
            self._cfg_set('game_dir', d)

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
