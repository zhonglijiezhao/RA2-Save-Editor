# -*- coding: utf-8 -*-
"""所属方页（原 `App.houses`）

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

from tkinter import ttk
import tkinter as tk
import ui_theme
from ui.msg import messagebox


class HousesPage(object):
    def _tab_houses(self, nb):
        f = ttk.Frame(nb, padding=8)
        _ph = ui_theme.page_header(f, '所属方金钱', '每个所属方的金钱；双击行可改')
        _ph.pack(fill='x', padx=2, pady=(2, 8))
        cols = ('cn', 'short', 'money', 'note')
        self.th = ttk.Treeview(f, columns=cols, show='headings', height=14)
        for c, t, w in (('cn', '阵营', 90), ('short', '标识', 130),
                        ('money', '金钱', 120), ('note', '说明', 260)):
            self.th.heading(c, text=t)
            self.th.column(c, width=w, anchor='w')
        self.th.pack(fill='both', expand=True)
        self.th.bind('<Double-1>', lambda e: self.edit_house())
        self.th.bind('<Return>', lambda e: self.edit_house())
        # ★2026-11-17：选中即把该所属方的四项属性读出来（不用点按钮）
        self.th.bind('<<TreeviewSelect>>', lambda e: self.load_house_attr())

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
                  foreground=ui_theme.T['muted']).pack(side='left', padx=8)

        # ── ★2026-11-17：地编「所属方」窗口的那几项（IQ / 科技等级 / 起始资金 / 玩家控制）──
        #   落点：House 对象的 GUID 位置 +0x1E4 / +0x1E8 / +0x1F0 / +0x201（见 core 常量注释）。
        attr = ttk.Labelframe(f, text='所属方属性（地编「所属方」窗口；选中上面一行再改）', padding=8)
        attr.pack(fill='x', pady=(8, 0))

        r1 = ttk.Frame(attr)
        r1.pack(fill='x')
        ttk.Label(r1, text='IQ').pack(side='left')
        self.hs_iq = ttk.Combobox(r1, width=4, state='readonly',
                                  values=tuple(str(i) for i in range(6)))
        self.hs_iq.pack(side='left', padx=(4, 12))
        ttk.Label(r1, text='科技等级').pack(side='left')
        self.hs_tech = ttk.Combobox(r1, width=4, state='readonly',
                                    values=tuple(str(i) for i in range(11)))
        self.hs_tech.pack(side='left', padx=(4, 12))
        ttk.Label(r1, text='起始资金').pack(side='left')
        self.hs_credits = ttk.Entry(r1, width=14)
        self.hs_credits.pack(side='left', padx=(4, 12))
        self.hs_ctrl = tk.BooleanVar(value=False)
        ttk.Checkbutton(r1, text='玩家控制', variable=self.hs_ctrl,
                        style='Surface.TCheckbutton').pack(side='left', padx=(0, 12))
        ttk.Button(r1, text='写入这四项',
                   command=self.apply_house_attr).pack(side='left')
        ttk.Button(r1, text='重新读取',
                   command=self.load_house_attr).pack(side='left', padx=6)

        self.hs_attr_lbl = ttk.Label(attr, text='（先在列表里选中一个所属方）',
                                     foreground=ui_theme.T['muted'])
        self.hs_attr_lbl.pack(anchor='w', pady=(6, 0))
        ttk.Label(attr, text='起始资金 = 地编的 Credits（存档里存的是它 ×100）；'
                             '改它不会变游戏里现在的钱 —— 改钱请用上面的「修改选中」。',
                  foreground=ui_theme.T['muted'], wraplength=880,
                  justify='left').pack(anchor='w')

        ttk.Label(f, text='说明：每个所属方的金钱存在「经济结构」里 —— 60 字节固定模板 → 阵营位掩码(=1<<注册序) → 3 字节金额，'
                          '其后 +304 字节还有一份副本，写入时两份一起改。当前玩家另写它自己的镜像字段与主字段。'
                          '定位方法已用「独特金额 + 重开新档」实测验证（值与该阵营在地图里的 Credits×100 逐一吻合）。',
                  foreground=ui_theme.T['muted'], wraplength=880, justify='left').pack(anchor='w', pady=(8, 0))
        return f

    # ---------------- ★2026-11-17：地编「所属方」窗口的四项 ----------------
    def hs_selected(self):
        """所属方列表里选中的**短名**；没选中/占位行返回 None。"""
        try:
            sel = self.th.selection()
        except Exception:
            return None
        if not sel or sel[0] == '__none__':
            return None
        return sel[0]

    def load_house_attr(self):
        """把选中所属方的 IQ / 科技等级 / 起始资金 / 玩家控制读到控件上。"""
        short = self.hs_selected()
        if not self.doc or not short:
            return
        try:
            a = self.doc.house_attr(short)
        except Exception as e:
            try:
                self.hs_attr_lbl.config(text='读不到这一项：%s' % e)
            except Exception:
                pass
            return
        try:
            self.hs_iq.set(str(a['iq']))
            self.hs_tech.set(str(a['tech']))
            self.hs_credits.delete(0, 'end')
            self.hs_credits.insert(0, str(a['credits']))
            self.hs_ctrl.set(bool(a['player_ctrl']))
            self.hs_attr_lbl.config(
                text='%s：IQ=%d　科技等级=%d　起始资金=%d　%s'
                     % (a['name'], a['iq'], a['tech'], a['credits'],
                        '玩家控制' if a['player_ctrl'] else 'AI 控制'))
        except Exception:
            pass

    def apply_house_attr(self):
        """把控件上的四项写进选中所属方的 House 对象（范围校验在 core 里）。"""
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        short = self.hs_selected()
        if not short:
            messagebox.showwarning('提示', '请先在列表里选中一个所属方')
            return
        try:
            iq = int(str(self.hs_iq.get()).strip())
            tech = int(str(self.hs_tech.get()).strip())
            raw = str(self.hs_credits.get()).strip().replace(',', '').replace('，', '')
            credits = int(raw or '0')
        except Exception:
            messagebox.showwarning('提示', 'IQ / 科技等级 / 起始资金 都要填整数')
            return
        ctrl = bool(self.hs_ctrl.get())
        try:
            old = self.doc.house_attr(short)
        except Exception as e:
            messagebox.showerror('读不到这个所属方', str(e))
            return
        if not messagebox.askyesno(
                '确认改所属方属性',
                '所属方：%s\n\n'
                'IQ：%d → %d　　科技等级：%d → %d\n'
                '起始资金：%d → %d　　玩家控制：%s → %s\n\n'
                '⚠ 「起始资金」是地编的 Credits（存档里存的是它 ×100），'
                '改它**不会**改变游戏里现在的钱。\n'
                '原档不会被覆盖，改完请「另存为新文件」。'
                % (old['name'], old['iq'], iq, old['tech'], tech,
                   old['credits'], credits,
                   '是' if old['player_ctrl'] else '否', '是' if ctrl else '否')):
            return
        try:
            r = self.doc.set_house_attr(short, iq=iq, tech=tech,
                                        credits=credits, player_ctrl=ctrl)
        except Exception as e:
            messagebox.showerror('写入失败（存档未改动）', str(e))
            return
        self.status.set('✅ ' + r['note'])
        self.load_house_attr()
