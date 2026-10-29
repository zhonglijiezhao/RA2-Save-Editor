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
import json, subprocess, webbrowser
import platform
import tkinter as tk
from tkinter import ttk
# ★P2-8：对话框走共享代理，测试打补丁才能对全部页面生效（见 ui/msg.py）
from ui.msg import filedialog, messagebox, simpledialog   # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'core'))
from editor_core import (SaveDoc, MONEY_SIG, MIRROR_DELTA,   # noqa: E402
                         EVENT_TYPES, ACTION_TYPES, event_name, event_ptype,
                         script_action_name, SCRIPT_PARAM_TYPES,
                         action_name, action_ptype, event_note, action_note,
                         ENGINE_PTYPE_TEXT)
import game_ini                                            # noqa: E402  规则文件（rules/art）
import app_version                                         # noqa: E402  ★版本号/仓库地址
import ai_objects                                          # noqa: E402  AI 三大件（小队/特遣/脚本）
import ui_theme                                            # noqa: E402  ★v1.3 视觉主题层
# ★2026-10-28（P2-8）：11 个页签各一个 Page 类（mixin），App 只留外壳；
#   触发编辑器也搬到了 ui/trigger_editor.py（页面要用它，否则循环导入）。
from ui.page_overview import OverviewPage        # noqa: E402
from ui.page_houses import HousesPage        # noqa: E402
from ui.page_triggers import TriggersPage        # noqa: E402
from ui.page_events import EventsPage        # noqa: E402
from ui.page_actions import ActionsPage        # noqa: E402
from ui.page_attach import AttachPage        # noqa: E402
from ui.page_mapview import MapViewPage        # noqa: E402
from ui.page_gift import GiftPage        # noqa: E402
from ui.page_weapons import WeaponsPage        # noqa: E402
from ui.page_search import SearchPage        # noqa: E402
from ui.page_ai import AiPage        # noqa: E402
from ui.trigger_editor import TriggerEditor      # noqa: E402,F401
from ui.const import (UNIT_HOUSE_INHERIT, UNIT_VET_INHERIT,      # noqa: E402
                      UNIT_VET_CHOICES, _VET_BY_LABEL, REPEAT_LABEL)

GAME_DIR_DEFAULT = r'E:\YURI'
BG = ui_theme.T['canvas']
FG = ui_theme.T['ink']
ACCENT = ui_theme.T['accent_deep']
RAIL = ui_theme.T['rail']
SURFACE = ui_theme.T['surface']


class App(tk.Tk, OverviewPage, HousesPage, TriggersPage, EventsPage, ActionsPage, AttachPage, MapViewPage, GiftPage, WeaponsPage, SearchPage, AiPage):
    def __init__(self):
        tk.Tk.__init__(self)
        self.title('RA2 / 尤里的复仇 — 存档编辑器')
        self._apply_geometry()           # ★P4-20：按屏幕 DPI 定窗口大小（封顶到屏幕）
        self.configure(bg=BG)
        self.doc = None
        self._warned_zero = False        # 「-无事件-」(0) 告警每次载入只弹一次
        # ★2026-10-28（审查 P4-21）：游戏目录以前写死 E:\YURI 且从不记住，
        #   玩家每次启动都要重新输一遍。现在读配置、并在刷新/开档时回写。
        # ★2026-10-28（玩家要求）：新电脑上别指望它猜对 —— 按
        #   ① 上次记住的  ② config.json 手写的  ③ 自动探测（注册表/盘符，带磁盘缓存）
        #   ④ 开发机老默认（仅当该目录真的存在）  的顺序定初值。
        _gd = self._cfg_get('game_dir', '') or ''
        if not _gd:
            try:
                _gd = game_ini._config_game_dir() or ''
            except Exception:
                _gd = ''
        if not _gd:
            try:
                _gd = game_ini.detect_game_dir() or ''
            except Exception:
                _gd = ''
        if not _gd and os.path.isdir(GAME_DIR_DEFAULT):
            _gd = GAME_DIR_DEFAULT
        self.game_dir = tk.StringVar(value=_gd)
        self.status = tk.StringVar(value='就绪')
        self.q = queue.Queue()
        # 后台任务状态 —— 用于「看门狗」，避免进度条永远转（见 _poll）
        self._pending_load = False
        self._t_load = 0.0
        self._saving = False
        self._t_save = 0.0
        self._scanning = False
        self._sync_game_dir()            # 把游戏目录灌给 core（rules/art 靠它定位）
        self._build_style()
        self._build_ui()
        # ★2026-10-28（审查 P1-5）：以前没有 WM_DELETE_WINDOW 处理 —— 改了一堆触发
        #   直接点 × 就全没了，一句提示都没有。现在有未保存改动会先问。
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self._bind_shortcuts()           # ★P1-7：Ctrl+S / Ctrl+Shift+S / Ctrl+O / F5
        self.after(120, self._poll)
        self.refresh_list()

    def _apply_geometry(self):
        """按屏幕 DPI 定窗口大小（★2026-10-28 审查 P4-20）。

        以前固定 `1280x820` + `minsize(1120, 700)`。开了 DPI 感知后（见 `app_main.py`），
        高 DPI 屏上字体按**原生分辨率**渲染：窗口像素尺寸不变、文字变大 ⇒ 必然横向溢出。
        所以窗口/最小尺寸都跟着 DPI 缩放，并**按屏幕可用区域封顶**
        （150% 时 1280×1.5 = 1920，正好铺满 1920 宽的笔记本，再大就超出屏幕了）。

        从源码直接跑（未开 DPI 感知）时 `winfo_fpixels('1i')` 仍返回 96 ⇒ s=1.0，
        行为与改动前完全一致，不会影响自检基线。
        """
        try:
            s = self.winfo_fpixels('1i') / 96.0
        except Exception:
            s = 1.0
        s = max(1.0, min(float(s), 2.5))
        self._ui_scale = s
        ui_theme.set_scale(s)             # 写死的像素宽度（列表卡/导航条）跟着缩放
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(int(1280 * s), max(960, sw - 40))
        h = min(int(820 * s), max(640, sh - 80))
        self.geometry('%dx%d' % (w, h))
        self.minsize(min(int(1120 * s), max(960, sw - 40)),
                     min(int(700 * s), max(640, sh - 80)))

    def report_callback_exception(self, exc, val, tb):
        """Tk 回调里的异常 —— **写日志 + 弹出来**（不让窗口"静默闪退"）。

        ★§255：玩家报「点新建会闪退」却拿不到任何信息。打包成 exe 后没有控制台，
          默认行为在 windowed 模式下要么静默、要么直接结束进程。
        """
        write_crash_log(exc, val, tb, 'tk-callback')
        try:
            messagebox.showerror('出错了（已写入 crash.log）',
                                 '%s: %s\n\n详情见程序目录下的 crash.log'
                                 % (getattr(exc, '__name__', 'Error'), val))
        except Exception:
            pass

    # ---------------- UI ----------------
    def _build_style(self):
        """★v1.3：样式全部下沉到 `core/ui_theme.py`（token + ttk 样式表）。

        页面代码里不要再写死颜色；要调色/调间距只改 ui_theme.T。
        """
        self.style = ui_theme.apply(self)

    def _build_ui(self):
        T = ui_theme.T

        # ── ① 应用头：品牌 / 标题 / 目录与开档 ──
        head = tk.Frame(self, bg=T['surface'])
        head.pack(fill='x')
        tk.Frame(head, bg=T['line'], height=1).pack(side='bottom', fill='x')
        hi = tk.Frame(head, bg=T['surface'])
        hi.pack(fill='x', padx=14, pady=(10, 9))

        tk.Label(hi, text='RA2', bg=T['rail'], fg=T['accent'],
                 font=(ui_theme.FONT_UI, 10, 'bold'), padx=7, pady=2).pack(side='left')
        tk.Label(hi, text='存档编辑器', bg=T['surface'], fg=T['ink'],
                 font=(ui_theme.FONT_UI, 12, 'bold')).pack(side='left', padx=(9, 6))
        tk.Label(hi, text='尤里的复仇', bg=T['surface'], fg=T['muted'],
                 font=(ui_theme.FONT_UI, 9)).pack(side='left', pady=(4, 0))

        # ★2026-10-29（玩家要求「主页面加版本号和 GITHUB 链接」）：
        #   放头部左侧、标题之后 —— 状态栏右边被进度条占着，而玩家截图报 bug 时
        #   头部一定在画面里，"我用的是哪一版"就再也不用问了。
        #   点版本号 = 复制「版本 + Python + 系统」到剪贴板；点 GitHub = 开浏览器。
        self.lbl_ver = tk.Label(hi, text=app_version.version_text(),
                                bg=T['surface'], fg=T['muted_soft'],
                                font=(ui_theme.FONT_UI, 8), cursor='hand2')
        self.lbl_ver.pack(side='left', padx=(12, 0), pady=(7, 0))
        self.lbl_ver.bind('<Button-1>', lambda e: self.copy_version())
        self.lbl_repo = tk.Label(hi, text='GitHub \u2197', bg=T['surface'], fg=T['info'],
                                 font=(ui_theme.FONT_UI, 8, 'underline'),
                                 cursor='hand2')
        self.lbl_repo.pack(side='left', padx=(9, 0), pady=(7, 0))
        self.lbl_repo.bind('<Button-1>', lambda e: self.open_repo())
        self.lbl_ver.bind('<Enter>', lambda e: self.lbl_ver.configure(fg=T['ink_2']))
        self.lbl_ver.bind('<Leave>', lambda e: self.lbl_ver.configure(fg=T['muted_soft']))
        self.lbl_repo.bind('<Enter>', lambda e: self.lbl_repo.configure(fg=T['accent_deep']))
        self.lbl_repo.bind('<Leave>', lambda e: self.lbl_repo.configure(fg=T['info']))

        # 右侧：目录 + 刷新 + 打开（从右往左 pack）
        ttk.Button(hi, text='打开存档…', style='Secondary.TButton',
                   command=self.open_dialog).pack(side='right')
        ttk.Button(hi, text='刷新', style='Ghost.TButton',
                   command=self.refresh_list).pack(side='right', padx=(2, 10))
        # ★2026-10-28（玩家要求）：主界面直接能选红警目录，不用去地图页菜单里翻
        ttk.Button(hi, text='…', style='Ghost.TButton', width=0,
                   command=self.pick_game_dir).pack(side='right', padx=(0, 4))
        ttk.Entry(hi, textvariable=self.game_dir, width=26).pack(side='right', padx=(6, 8))
        tk.Label(hi, text='游戏目录', bg=T['surface'], fg=T['muted'],
                 font=(ui_theme.FONT_UI, 9)).pack(side='right')

        # ── ② 操作条：文件动作 / 地图动作 / 当前文件 ──
        bar = tk.Frame(self, bg=T['surface_2'])
        bar.pack(fill='x')
        tk.Frame(bar, bg=T['line'], height=1).pack(side='bottom', fill='x')
        bi = tk.Frame(bar, bg=T['surface_2'])
        bi.pack(fill='x', padx=14, pady=7)

        self.btn_save_in = ttk.Button(bi, text='原地保存', style='Primary.TButton',
                                      command=self.save_inplace, state='disabled')
        self.btn_save_in.pack(side='left')
        self.btn_save_new = ttk.Button(bi, text='另存为…', style='Secondary.TButton',
                                       command=self.save_as, state='disabled')
        self.btn_save_new.pack(side='left', padx=(8, 0))
        tk.Frame(bi, bg=T['line'], width=1, height=18).pack(side='left', padx=14)
        self.btn_map_auto = ttk.Button(bi, text='自动匹配地图', style='Ghost.TButton',
                                       command=self.auto_map, state='disabled')
        self.btn_map_auto.pack(side='left')
        self.btn_map_pick = ttk.Button(bi, text='选择地图…', style='Ghost.TButton',
                                       command=self.choose_map, state='disabled')
        self.btn_map_pick.pack(side='left', padx=(2, 0))

        self.lbl_cur = tk.Label(bi, text='未打开存档', bg=T['surface_2'], fg=T['muted'],
                                font=(ui_theme.FONT_UI, 9))
        self.lbl_cur.pack(side='right')

        # ── ③ 主体：存档列表（卡片） + 导航/内容 ──
        body = ttk.PanedWindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=12, pady=(12, 8))

        left = tk.Frame(body, bg=T['line'], width=ui_theme.px(340))
        left.pack_propagate(False)
        inner = tk.Frame(left, bg=T['surface'])
        inner.pack(fill='both', expand=True, padx=1, pady=1)
        lh = tk.Frame(inner, bg=T['surface'])
        lh.pack(fill='x', padx=12, pady=(11, 0))
        tk.Label(lh, text='存档列表', bg=T['surface'], fg=T['ink'],
                 font=(ui_theme.FONT_UI, 10, 'bold')).pack(side='left')
        self.lbl_list_count = tk.Label(lh, text='', bg=T['surface'], fg=T['muted'],
                                       font=(ui_theme.FONT_UI, 9))
        self.lbl_list_count.pack(side='right')
        tk.Frame(inner, bg=T['line_soft'], height=1).pack(fill='x', padx=12, pady=(8, 0))

        cols = ('file', 'time', 'money')
        self.tree = ttk.Treeview(inner, columns=cols, show='headings', height=20)
        for c, t, w in (('file', '文件', 178), ('time', '时间', 86),
                        ('money', '金钱', 48)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor='w' if c != 'money' else 'e')
        self.tree.tag_configure('odd', background=T['surface_2'])
        self.tree.pack(fill='both', expand=True, padx=(8, 14), pady=(6, 8))
        self.tree.bind('<Double-1>', lambda e: self.open_selected())
        self.tree.bind('<Return>', lambda e: self.open_selected())
        body.add(left, weight=0)

        nb = ui_theme.NavRail(body)
        body.add(nb, weight=1)

        nb.add(self._tab_overview(nb), text='概览')
        nb.add(self._tab_houses(nb), text='所属方金钱')
        nb.add(self._tab_triggers(nb), text='触发')
        nb.add(self._tab_events(nb), text='事件')
        nb.add(self._tab_actions(nb), text='行为')
        nb.add(self._tab_attach(nb), text='挂接')
        nb.add(self._tab_mapview(nb), text='地图 · 选位')
        nb.add(self._tab_gift(nb), text='赠送建筑')
        nb.add(self._tab_search(nb), text='明文搜索')
        # ★2026-10-18 新增：AI 三大件（作战小队 / 特遣部队 / 动作脚本）—— 只读
        nb.add(self._tab_ai(nb), text='作战小队')
        # ★2026-10-28 新增：武器 / 弹头（★新页一律**追加在末尾** ——
        #   现有自检按固定索引取页（6=地图 7=赠送…），插在中间会把它们全打歪）
        nb.add(self._tab_weapons(nb), text='武器 · 弹头')
        self.nb = nb
        nb.bind('<<NotebookTabChanged>>', self._on_tab_changed)
        nb.select(0)

        # ── ④ 状态栏 ──
        sb = tk.Frame(self, bg=T['surface'])
        sb.pack(fill='x')
        tk.Frame(sb, bg=T['line'], height=1).pack(side='top', fill='x')
        si = tk.Frame(sb, bg=T['surface'])
        si.pack(fill='x', padx=14, pady=5)
        tk.Label(si, textvariable=self.status, bg=T['surface'], fg=T['muted'],
                 font=(ui_theme.FONT_UI, 9)).pack(side='left')
        self.pbar = ttk.Progressbar(si, mode='indeterminate', length=132)
        self.pbar.pack(side='right')

    # ★2026-10-28（审查 P2-8）：11 个页签的方法体原样搬到了 ui/page_*.py，
    #   由多继承挂回来。这里只剩外壳：窗口装配 + 存档列表 + 状态栏 + 后台轮询。
# 触发编辑器已拆到 ui/trigger_editor.py（P2-8）


    def pick_game_dir(self):
        """主界面：手动指定红警安装目录。

        ★2026-10-28（玩家要求）：新电脑上存档常在桌面、游戏在别的盘，
          以前只能去地图页「更多 ▾ → 规则目录…」里找，藏得深。
        """
        cur = (self.game_dir.get() or '').strip()
        d = filedialog.askdirectory(
            title='选择红警安装目录（含 gamemd.exe / ra2md.mix 或 rulesmd.ini 的那一层）',
            initialdir=cur if cur and os.path.isdir(cur) else None)
        if not d:
            return
        self.apply_game_dir(d)

    def apply_game_dir(self, d, persist=True):
        """填输入框 → 交给 core → 记住（两处配置）→ 刷新所有依赖规则的地方。

        两处配置都写，是因为它们各管一头：
          · `%APPDATA%\\RA2SED\\editor_config.json` —— 界面下次启动带出来
          · exe 旁边的 `config.json` —— `game_ini` 找规则时读（也可以手写）
        """
        d = (d or '').strip().replace('/', '\\').rstrip('\\')
        self.game_dir.set(d)
        try:
            self._sync_game_dir(persist=persist)        # → SaveDoc.set_game_dir + 界面配置
        except Exception:
            pass
        try:
            game_ini.clear_cache()
        except Exception:
            pass
        if persist and d:
            try:
                cfg = game_ini.config_path()
                data = {}
                if os.path.exists(cfg):
                    with open(cfg, encoding='utf-8') as f:
                        data = json.load(f) or {}
                data['game_dir'] = d
                with open(cfg, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        src = SaveDoc.rules_source() or {}
        rp = src.get('rules')
        kind = src.get('source')
        if not rp:
            self.status.set('⚠ 这个目录里没找到 rules —— 请选游戏安装根目录'
                            '（含 gamemd.exe / ra2md.mix 的那一层）')
        elif kind == 'builtin':
            self.status.set('⚠ 只在程序内置副本里找到规则（%s）—— 你选的那个目录没用上' % rp)
        elif kind == 'auto':
            # 最常见的一种困惑：选了目录但那里没 rules，结果自动探测到别的游戏目录顶上
            self.status.set('⚠ %s 里没找到 rules；自动探测到 %s 并已使用'
                            '（要指定就选含 gamemd.exe 的那一层）' % (d, rp))
        else:
            self.status.set('规则已生效：%s' % rp)
        # 地图页右上角的「规则：…」标签、赠礼页的单位下拉都跟着刷
        for fn in ('_refresh_unit_rule_label',):
            try:
                getattr(self, fn)()
            except Exception:
                pass
        try:
            if getattr(self, 'doc', None):
                self.mv_unit['values'] = self._unit_type_names()
        except Exception:
            pass
        return bool(rp)


    # ---------- 版本号 / 仓库链接（★2026-10-29 玩家要求） ----------
    def version_info_text(self):
        """一行能直接贴进 issue 的版本信息（版本 / Python / 系统）。"""
        try:
            osname = platform.platform(terse=True)
        except Exception:
            osname = 'Windows'
        return ('RA2 存档编辑器 %s  |  Python %s  |  %s'
                % (app_version.version_text(), platform.python_version(), osname))

    def copy_version(self):
        """点版本号 → 复制「版本 + Python + 系统」到剪贴板。

        报 bug 时最缺的就是这三样，让人手打不如一点。
        """
        t = self.version_info_text()
        try:
            self.clipboard_clear()
            self.clipboard_append(t)
            self.update_idletasks()
        except Exception:
            pass
        self.status.set('已复制版本信息：%s' % t)
        return t

    def open_repo(self):
        """点「GitHub ↗」→ 用默认浏览器打开仓库。

        打不开（没默认浏览器 / 被安全策略拦）时不静默失败：
        把地址复制到剪贴板并弹出来，玩家照样能自己粘。
        """
        url = app_version.REPO
        ok = False
        try:
            ok = bool(webbrowser.open(url))
        except Exception:
            ok = False
        if ok:
            self.status.set('已在浏览器打开：%s' % url)
            return True
        try:
            self.clipboard_clear()
            self.clipboard_append(url)
            self.update_idletasks()
            tail = '\n\n（地址已复制到剪贴板，粘到浏览器地址栏就能打开）'
        except Exception:
            tail = ''
        try:
            messagebox.showinfo('项目主页',
                                'RA2 存档编辑器 %s\n\n%s%s'
                                % (app_version.version_text(), url, tail))
        except Exception:
            pass
        self.status.set('无法自动打开浏览器：%s' % url)
        return False


def crash_log_path():
    """崩溃日志路径（exe 同目录；源码跑时在脚本目录）。"""
    import sys as _s
    base = os.path.dirname(os.path.abspath(_s.executable if getattr(_s, 'frozen', False)
                                           else __file__))
    return os.path.join(base, 'crash.log')


def write_crash_log(exc, val, tb, tag=''):
    """把未捕获异常追加写到 crash.log。

    ★§255：玩家 2026-10-24 报「触发编辑器点新建会闪退」，但打包成 exe 后没有控制台，
      异常要么被吞、要么整个进程消失 ⇒ 什么信息都拿不到。写文件是最低成本的自证手段。
    """
    import traceback as _tb
    import datetime as _dt
    try:
        with open(crash_log_path(), 'a', encoding='utf-8') as f:
            f.write('\n===== %s  %s =====\n'
                    % (_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), tag))
            f.write(''.join(_tb.format_exception(exc, val, tb)))
    except Exception:
        pass


def _install_crash_hooks():
    _old = sys.excepthook

    def _hook(exc, val, tb):
        write_crash_log(exc, val, tb, 'uncaught')
        try:
            _old(exc, val, tb)
        except Exception:
            pass

    sys.excepthook = _hook


if __name__ == '__main__':
    _install_crash_hooks()
    App().mainloop()
