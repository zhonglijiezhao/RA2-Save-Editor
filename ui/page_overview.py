# -*- coding: utf-8 -*-
"""总览页（原 `App.overview`）

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
for _p in (_ROOT, os.path.join(_ROOT, 'core')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tkinter import ttk
import tkinter as tk
import ui_theme
from ui.msg import messagebox


class OverviewPage(object):
    def _tab_overview(self, nb):
        T = ui_theme.T
        f = tk.Frame(nb, bg=T['canvas'])
        ui_theme.page_header(f, '概览', '存档头信息与金钱').pack(fill='x', padx=2, pady=(2, 10))

        # ── ① 存档信息（整宽，双栏 6 行；高度随内容）──
        card, body = ui_theme.card(f, '存档信息')
        card.pack(fill='x')
        for c in (1, 3):
            body.columnconfigure(c, weight=1, uniform='v')

        self.info_labels = {}
        # ★§276：「任务名」读存档头部固定字段（0x1360 → 剥掉「军事行动：」前缀），
        #   「任务副标题」= 0x13DA（官方战役是「國家：章節」，自制图常是地图名）。
        keys = ['文件', '文件大小', '明文大小', '块数', '地图尺寸', '名字键',
                '任务名', '任务副标题', '玩家阵营', '所属方数组', '开始时间', '存档时间']
        for n, k in enumerate(keys):
            r, col = divmod(n, 2)                    # 双栏：5 行 × 2 组
            lab = ttk.Label(body, text=k, style='Field.TLabel', anchor='w', width=9)
            lab.grid(row=r, column=col * 2, sticky='w', pady=6,
                     padx=(0 if col == 0 else 40, 0))
            v = ttk.Label(body, text='-', style='Value.TLabel', anchor='w')
            v.grid(row=r, column=col * 2 + 1, sticky='w', padx=(14, 0), pady=6)
            self.info_labels[k] = v

        # ── ② 金钱条（横向：当前值 / 改为 / 应用 / 说明）──
        row2 = tk.Frame(f, bg=T['canvas'])
        row2.pack(fill='x', pady=(12, 0))

        mcard, mbody = ui_theme.card(row2, '金钱')
        mcard.pack(side='left', fill='both', expand=True)

        ttk.Label(mbody, text='当前', style='Field.TLabel', width=6).grid(
            row=0, column=0, sticky='w')
        self.lbl_money = ttk.Label(mbody, text='-', style='Num.TLabel')
        self.lbl_money.grid(row=0, column=1, sticky='w', padx=(8, 0))
        self.lbl_money_field = ttk.Label(mbody, text='', style='Field.TLabel',
                                        font=(ui_theme.FONT_MONO, 8))
        self.lbl_money_field.grid(row=0, column=2, sticky='e', padx=(16, 0))

        ttk.Label(mbody, text='改为', style='Field.TLabel', width=6).grid(
            row=1, column=0, sticky='w', pady=(12, 0))
        self.ent_money = ttk.Entry(mbody, width=14)
        self.ent_money.grid(row=1, column=1, sticky='w', padx=(8, 0), pady=(12, 0))
        ttk.Button(mbody, text='应用', style='Primary.TButton',
                   command=self.apply_money).grid(row=1, column=2, sticky='e',
                                                  pady=(12, 0))

        # ── ③ 地图与规则（贴铁律：本工具只读地图）──
        gcard, gbody = ui_theme.card(row2, '地图与规则')
        gcard.pack(side='left', fill='both', expand=True, padx=(12, 0))
        # ★只让最后那列吃剩余空间 —— 给 col1 也加 weight/uniform 的话，
        #   这张卡只有 ~280px 宽，Tk 会等比压缩各列 ⇒「可见区域 0,0,80,80」被截成
        #   「0,0,80,8」（2026-11-10 截图发现）。
        gbody.columnconfigure(3, weight=1)
        self.lbl_map = ttk.Label(gbody, text='-', style='Value.TLabel', anchor='w')
        self.lbl_map_mode = ttk.Label(gbody, text='-', style='Value.TLabel', anchor='w')
        # ★§SOV05：地图 [Basic] IgnoreGlobalAITriggers（FA2「忽略全局AI」）的存档镜像
        self.lbl_ignore_ai = ttk.Label(gbody, text='-', style='Value.TLabel', anchor='w')
        for r, (lab, wid) in enumerate((('地图文件', self.lbl_map),
                                        ('挂接方式', self.lbl_map_mode),
                                        ('忽略全局AI', self.lbl_ignore_ai))):
            col = 0
            ttk.Label(gbody, text=lab, style='Field.TLabel', width=9).grid(
                row=r, column=col, sticky='w', pady=6)
            wid.grid(row=r, column=col + 1, sticky='w', padx=(14, 0), pady=6)
        ttk.Button(gbody, text='切换', command=self.toggle_ignore_ai).grid(
            row=2, column=2, sticky='w', padx=(8, 0))

        # ── ★2026-11-10：可见区域（地编「地图参数 ▸ 可见区域」）──
        #   显示**存档里**的值（= 读档后游戏真正用的那个矩形）；
        #   与地图 `[Map] LocalSize` 不一致时变橙 + 打 ⚠（图改过、档还是旧的）。
        #   ★卡片只有 280px 宽，四段数字 + 输入框 + 按钮塞不下 ⇒ 编辑走弹窗
        #     （地编也是点「更改」弹窗填，交互一致）。
        ttk.Label(gbody, text='可见区域', style='Field.TLabel', width=9).grid(
            row=3, column=0, sticky='w', pady=(6, 0))
        self.lbl_visible = tk.Label(gbody, text='-', bg=T['surface'], fg=T['ink'],
                                    font=(ui_theme.FONT_MONO, 10), anchor='w')
        self.lbl_visible.grid(row=3, column=1, sticky='w', padx=(14, 0), pady=(6, 0))
        ttk.Button(gbody, text='改…', width=5,
                   command=self.edit_visible_area).grid(
            row=3, column=2, sticky='w', padx=(8, 0), pady=(6, 0))

        ttk.Label(gbody, text='本工具只读地图：不写 .map，一个字节都不改 '
                             '（「忽略全局AI」「可见区域」改的是存档里的镜像）',
                  style='Field.TLabel').grid(row=4, column=0, columnspan=4,
                                             sticky='w', pady=(6, 0))

        # ── ④ 存档修复：清除空气墙（★2026-11-01 新增）──
        #   判据见 SaveDoc.air_wall_cells()：+0x138 占用标记非零、+0xF8 占用键为 0。
        #   载具读这个标记、步兵不读 ⇒ 症状永远是"只挡载具"。
        #   ★压成一行：首页在 700 高的最小窗口下不能溢出（原底部 606px）。
        row3 = tk.Frame(f, bg=T['canvas'])
        row3.pack(fill='x', pady=(12, 0))
        acard, abody = ui_theme.card(row3, '存档修复 · 空气墙')
        acard.pack(fill='both', expand=True)

        arow = tk.Frame(abody, bg=T['surface'])
        arow.pack(fill='x')
        self.lbl_airwall = ttk.Label(
            arow,
            text='清理「格子上空无一物、却仍登记着被占用」的残留标记（只挡载具）',
            style='Value.TLabel', anchor='w')
        self.lbl_airwall.pack(side='left', fill='x', expand=True)
        ttk.Button(arow, text='一键清除', style='Primary.TButton',
                   command=self.fix_air_walls).pack(side='right')
        ttk.Button(arow, text='检查', command=self.scan_air_walls).pack(
            side='right', padx=(0, 8))
        return f

    # ------------------------------------------------------------------
    #  清除空气墙（★2026-11-01）
    #  真实战绩：SAVE2CD6（超时空+铁幕留下的 9 格 0x20）、SAVE72AE（5 格）
    #  存档改完读档即消失 —— 玩家在游戏里实测确认过。
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    #  忽略全局AI触发（★★§SOV05，2026-11-03）
    #  地图 `[Basic] IgnoreGlobalAITriggers`（FA2 面板「忽略全局AI」）在**存档里有一份镜像**：
    #  明文 0x34B4 的 u32 —— 0 = AI 触发生效，1 = 忽略（AI 触发不跑）。
    #  证据（两档对照，前后 64 字节完全一致、只差这一格）：
    #    · AI触发打开.SAV（改图前存）= 0；AI触发关闭.SAV（改图后存，地图 =yes）= 1；
    #    · 另一张图 sov01umd.map =no 的档同样是 0。
    #  ⚠ 显示的是**存档里的值**；和地图不一致说明图改过而档还是旧的。
    # ------------------------------------------------------------------
    def toggle_ignore_ai(self):
        doc = getattr(self, 'doc', None)
        if not doc:
            messagebox.showinfo('忽略全局AI', '请先打开一个存档。', parent=self)
            return
        cur = doc.ignore_global_ai()
        if cur is None:
            messagebox.showwarning(
                '读不到', '这条存档里没能在 0x%X 定位到「忽略全局AI触发」字段'
                          '（该处不是 0/1）。' % doc.IGNORE_AI_OFF, parent=self)
            return
        new = 0 if cur else 1
        if not messagebox.askyesno(
                '切换「忽略全局AI触发」',
                '当前：%s\n改成：%s\n\n'
                '写的是**存档里**那一格（明文 0x%X，4 字节，原地改）。\n'
                '效果等价于地图 [Basic] IgnoreGlobalAITriggers：\n'
                '  · 忽略 ⇒ 全局 AI 触发（AITriggerTypes）不再被评估；\n'
                '  · 不忽略 ⇒ 恢复评估。\n\n'
                '改完要回主窗口点「保存」才写进文件。确定切换吗？'
                % (doc.ignore_ai_text(cur), doc.ignore_ai_text(new), doc.IGNORE_AI_OFF),
                parent=self):
            return
        try:
            r = doc.set_ignore_global_ai(new)
        except Exception as e:
            messagebox.showerror('写入失败', str(e), parent=self)
            return
        try:
            self.lbl_ignore_ai.config(text=doc.ignore_ai_text(r['new']))
        except Exception:
            _swallow()
        try:
            self.app._lists_dirty = True
        except Exception:
            _swallow()
        self.status.set('忽略全局AI触发：%s → %s（内存已改，点「保存」才落盘）'
                        % (doc.ignore_ai_text(r['old']), doc.ignore_ai_text(r['new'])))

    # ------------------------------------------------------------------
    #  可见区域（★★2026-11-10）
    #  地编「地图参数 ▸ 可见区域」= 地图 `[Map] LocalSize`，存档里有一份镜像：
    #  明文里一对紧邻的 4×int32（Size 在前、LocalSize 在后），是**唯一副本**，
    #  读档后游戏用的就是它 ⇒ 改存档即可，地图文件一个字节都不碰。
    #  证据与定位判据见 `SaveDoc._scan_visible_area` 的注释（两档数值逐字对上地图）。
    # ------------------------------------------------------------------
    def refresh_visible_area(self):
        """把存档里的可见区域刷到概览页（只读，不写）。

        和地图 `[Map] LocalSize` 不一致（图改过、档还是旧的）⇒ 变橙 + 打 ⚠。
        """
        doc = getattr(self, 'doc', None)
        lbl = getattr(self, 'lbl_visible', None)
        if lbl is None:
            return
        va = None
        if doc:
            try:
                va = doc.visible_area()
            except Exception:
                va = None
        if not doc or not va:
            lbl.config(text='-' if not doc else '读不到', fg=ui_theme.T['muted'])
            return
        l, t, w, h = va['local']
        try:
            ml = doc.map_local_size()
        except Exception:
            ml = None
        same = bool(ml) and tuple(ml) == (l, t, w, h)
        lbl.config(text='%d,%d,%d,%d%s' % (l, t, w, h, '' if (not ml or same) else '  ⚠'),
                   fg=ui_theme.T['ink'] if (not ml or same) else ui_theme.T['warn'])

    def edit_visible_area(self):
        """弹窗改「可见区域」（原地改存档那 16 字节；地图文件一个字节都不碰）。

        ★为什么是弹窗：这张卡片只有 280px 宽，四段数字 + 输入框 + 按钮挤不下，
          而且地编本身就是「地图参数 ▸ 可见区域 + 更改」弹窗填 —— 交互也一致。
        """
        doc = getattr(self, 'doc', None)
        if not doc:
            messagebox.showinfo('可见区域', '请先打开一个存档。', parent=self)
            return
        try:
            va = doc.visible_area()
        except Exception:
            va = None
        if not va:
            messagebox.showwarning(
                '读不到', '这条存档里没定位到「可见区域」字段（结构判据不成立），'
                          '为安全起见不改。', parent=self)
            return
        try:
            ml = doc.map_local_size()
        except Exception:
            ml = None
        T = ui_theme.T
        sw, sh = va['size']
        top = tk.Toplevel(self)
        top.title('可见区域')
        top.transient(self.winfo_toplevel())
        top.configure(bg=T['surface'])
        top.resizable(False, False)
        body = tk.Frame(top, bg=T['surface'])
        body.pack(fill='both', expand=True, padx=18, pady=14)

        tk.Label(body, text='格式：左, 上, 宽, 高（单位 = 地编格子，与「地图参数」同口径）',
                 bg=T['surface'], fg=T['ink_2'],
                 font=(ui_theme.FONT_UI, 9)).grid(row=0, column=0, columnspan=4,
                                                  sticky='w', pady=(0, 10))
        cur = '存档当前：%d,%d,%d,%d' % va['local']
        cur += ('　　地图 [Map] LocalSize：%d,%d,%d,%d' % tuple(ml)) if ml else \
            '　　地图：未挂接（无从对照）'
        tk.Label(body, text=cur, bg=T['surface'],
                 fg=T['ink'] if (ml and tuple(ml) == va['local']) else T['warn'],
                 font=(ui_theme.FONT_MONO, 9)).grid(row=1, column=0, columnspan=4,
                                                    sticky='w')
        tk.Label(body, text='地图尺寸 %d×%d —— 可见区域必须落在里面（左/上 ≥ 0，'
                            '左+宽 ≤ %d，上+高 ≤ %d）' % (sw, sh, sw, sh),
                 bg=T['surface'], fg=T['muted'],
                 font=(ui_theme.FONT_UI, 9)).grid(row=2, column=0, columnspan=4,
                                                  sticky='w', pady=(4, 12))

        names = (('左', 'l'), ('上', 't'), ('宽', 'w'), ('高', 'h'))
        vars_ = {}
        erow = tk.Frame(body, bg=T['surface'])
        erow.grid(row=3, column=0, columnspan=8, sticky='w')
        first_ent = None
        for i, (lab, key) in enumerate(names):
            tk.Label(erow, text=lab, bg=T['surface'], fg=T['ink_2'],
                     font=(ui_theme.FONT_UI, 9)).pack(side='left',
                                                      padx=(0 if i == 0 else 18, 5))
            v = tk.StringVar(value=str(va['local'][i]))
            ent = ttk.Entry(erow, textvariable=v, width=6, justify='center')
            ent.pack(side='left')
            vars_[key] = v
            if first_ent is None:
                first_ent = ent
        if first_ent is not None:
            first_ent.focus_set()
            first_ent.select_range(0, 'end')

        def _set(vals):
            for k, n in zip(('l', 't', 'w', 'h'), vals):
                vars_[k].set(str(n))

        brow = tk.Frame(body, bg=T['surface'])
        brow.grid(row=4, column=0, columnspan=8, sticky='ew', pady=(14, 0))
        ttk.Button(brow, text='整张图',
                   command=lambda: _set((0, 0, sw, sh))).pack(side='left')
        if ml:
            ttk.Button(brow, text='用地图的值',
                       command=lambda: _set(tuple(ml))).pack(side='left', padx=(8, 0))
        ttk.Button(brow, text='取消', command=top.destroy).pack(side='right')
        ttk.Button(brow, text='确定', style='Primary.TButton',
                   command=lambda: _apply(top)).pack(side='right', padx=(0, 8))

        def _apply(win):
            try:
                vals = tuple(int(vars_[k].get().strip()) for k in ('l', 't', 'w', 'h'))
            except Exception:
                messagebox.showwarning('格式不对', '四个框都要填整数（左、上、宽、高）。',
                                       parent=win)
                return
            try:
                r = doc.set_visible_area(*vals)
            except Exception as e:
                messagebox.showerror('改不了', str(e), parent=win)
                return
            win.destroy()
            self.refresh_visible_area()
            try:
                self.app._lists_dirty = True
            except Exception:
                _swallow()
            self.status.set(
                '可见区域：%d,%d,%d,%d → %d,%d,%d,%d（内存已改，点「保存」才落盘）'
                % (r['old'] + r['new']))

        top.update_idletasks()
        try:
            px, py = self.winfo_rootx(), self.winfo_rooty()
            w, h = top.winfo_width(), top.winfo_height()
            top.geometry('+%d+%d' % (px + max(0, (self.winfo_width() - w) // 2),
                                     py + max(0, (self.winfo_height() - h) // 3)))
        except Exception:
            _swallow()
        top.grab_set()
        top.bind('<Escape>', lambda e: top.destroy())


    def _airwall_doc(self):
        doc = getattr(self, 'doc', None)
        if not doc:
            messagebox.showinfo('清除空气墙', '请先打开一个存档。', parent=self)
            return None
        return doc
    def scan_air_walls(self):
        """只扫描、不写 → 刷新首页那一行结果。"""
        doc = self._airwall_doc()
        if doc is None:
            return
        try:
            n, cells = doc.air_wall_cells()
        except Exception as exc:
            messagebox.showerror('清除空气墙', '扫描失败：%s' % exc, parent=self)
            return
        if n == 0:
            self.lbl_airwall.config(text='这个档里找不到格子表，无法检查')
            return
        if not cells:
            self.lbl_airwall.config(text='干净：%d 个格槽全部正常' % n)
            return
        self.lbl_airwall.config(text='发现 %d 处空气墙（共 %d 个格槽）'
                                     % (len(cells), n))
        preview = '、'.join('(%d,%d)' % (c['x'], c['y'])
                            for c in sorted(cells, key=lambda c: (c['y'], c['x']))[:16])
        more = ' …' if len(cells) > 16 else ''
        messagebox.showinfo(
            '清除空气墙',
            '发现 %d 处空气墙：\n\n%s%s\n\n点「一键清除」可以全部修掉。'
            % (len(cells), preview, more),
            parent=self)

    def fix_air_walls(self):
        """真正清除（只动 +0x138 这 1 个字节）+ 提示保存。"""
        doc = self._airwall_doc()
        if doc is None:
            return
        try:
            n, cells = doc.clear_air_walls()
        except Exception as exc:
            messagebox.showerror('清除空气墙', '清除失败：%s' % exc, parent=self)
            return
        if n == 0:
            self.lbl_airwall.config(text='这个档里找不到格子表，没有改动')
            return
        if not cells:
            self.lbl_airwall.config(text='干净：%d 个格槽全部正常，无需修复' % n)
            messagebox.showinfo('清除空气墙', '这个档没有空气墙，什么都没改。',
                                parent=self)
            return
        self.lbl_airwall.config(
            text='已清除 %d 处空气墙（共 %d 个格槽）—— 记得保存' % (len(cells), n))
        try:
            self._refresh_title()
        except Exception:
            _swallow()
        messagebox.showinfo(
            '清除空气墙',
            '已清除 %d 处空气墙。\n\n'
            '接下来：保存存档 → 进游戏读这个档，空气墙就没了。\n'
            '（通行状态是读档那一刻按标记重建的，游戏运行中改无效）' % len(cells),
            parent=self)
