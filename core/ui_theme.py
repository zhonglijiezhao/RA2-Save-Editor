# -*- coding: utf-8 -*-
"""RA2 存档编辑器 · 界面主题层（v1.3 视觉改版）

设计口径（Operate 模式：先能干活，再谈个性）
-----------------------------------------
- 结构色 = 深板岩（rail / 主按钮 / 选中行），数值色 = 战备金（当前值 / 焦点环 / 激活标记），
  正文色 = 中性墨；三色各有职责，不做装饰。
- 一屏之内只有一种"卡片"：白面 + 1px 边框，不做阴影、不做嵌套卡片。
- 字体：UI 用 Microsoft YaHei UI；数字（金钱/偏移/大小）用 Consolas 等宽，便于逐位核对。
- 所有控件走 ttk 样式表，页面代码里不要再写死颜色。

使用方法：
    from ui_theme import apply, NavRail, page_header, card
    apply(app)              # 必须在建控件之前
"""

from tkinter import ttk
import tkinter as tk

FONT_UI = 'Microsoft YaHei UI'
FONT_MONO = 'Consolas'

# ★2026-10-28（审查 P4-20）：DPI 缩放系数。
#   开了 DPI 感知后（见 `app_main.py`），高 DPI 屏上字体按原生分辨率渲染，
#   写死的像素宽度（列表卡 340、导航条 172）就会显得过窄。
#   `App._apply_geometry()` 会按实际 DPI 调 `set_scale()`；不调用时保持 1.0
#   ⇒ 从源码直接跑（未开 DPI 感知）时行为与以前完全一致。
SCALE = 1.0


def set_scale(s):
    """设置全局像素缩放（1.0 = 96 DPI 原尺寸）。"""
    global SCALE
    try:
        SCALE = max(0.75, min(float(s), 3.0))
    except Exception:
        SCALE = 1.0
    return SCALE


def px(n):
    """把 96 DPI 下的像素值换算成当前缩放下的像素。"""
    return max(1, int(round(n * SCALE)))


T = {
    # 地面 / 表面
    'canvas':       '#e9edf3',
    'surface':      '#ffffff',
    'surface_2':    '#f6f8fb',
    'surface_3':    '#eef2f8',
    'line':         '#d7dfea',
    'line_soft':    '#e6ebf3',
    # 文字
    'ink':          '#16202e',
    'ink_2':        '#3d4a5c',
    'muted':        '#68758a',
    'muted_soft':   '#8b96a8',
    # 板岩（结构）
    'rail':         '#1b2432',
    'rail_2':       '#2b3949',
    'rail_3':       '#2d3a4c',
    'rail_text':    '#9aabc0',
    'rail_line':    '#2a3546',
    # 战备金（数值/焦点/激活）
    'accent':       '#e0a12b',
    'accent_deep':  '#b87c12',
    'accent_soft':  '#fdf4e2',
    # 状态
    'danger':       '#b3261e',
    'ok':           '#1c7a54',
    'warn':         '#9a6206',
}

# ttk 样式里要反复用到的组合
_FLAT = dict(borderwidth=1, relief='solid', lightcolor=T['surface'],
             darkcolor=T['surface'], bordercolor=T['line'],
             background=T['surface'], foreground=T['ink'],
             focusthickness=1, focuscolor=T['accent'])


def apply(app):
    """装好整套 ttk 样式（clam 打底 + 全面扁平化）。"""
    st = ttk.Style(app)
    try:
        st.theme_use('clam')
    except Exception:
        pass

    # ---------- 基础 ----------
    st.configure('.', background=T['canvas'], foreground=T['ink'],
                 font=(FONT_UI, 9), bordercolor=T['line'],
                 lightcolor=T['surface'], darkcolor=T['surface'],
                 troughcolor=T['surface_3'], focuscolor=T['accent'])

    st.configure('TFrame', background=T['canvas'])
    st.configure('Surface.TFrame', background=T['surface'])
    st.configure('Bar.TFrame', background=T['surface'])
    st.configure('Hair.TFrame', background=T['line'])

    st.configure('TLabel', background=T['canvas'], foreground=T['ink'])
    st.configure('Surface.TLabel', background=T['surface'], foreground=T['ink'])
    st.configure('Muted.TLabel', background=T['surface'], foreground=T['muted'])
    st.configure('MutedOn.TLabel', background=T['canvas'], foreground=T['muted'])
    st.configure('Title.TLabel', background=T['surface'], foreground=T['ink'],
                 font=(FONT_UI, 13, 'bold'))
    st.configure('Section.TLabel', background=T['surface'], foreground=T['ink'],
                 font=(FONT_UI, 10, 'bold'))
    st.configure('Field.TLabel', background=T['surface'], foreground=T['muted'],
                 font=(FONT_UI, 9))
    st.configure('Value.TLabel', background=T['surface'], foreground=T['ink'],
                 font=(FONT_UI, 10))
    st.configure('Num.TLabel', background=T['surface'], foreground=T['accent_deep'],
                 font=(FONT_MONO, 17, 'bold'))
    st.configure('Danger.TLabel', background=T['surface'], foreground=T['danger'])

    # ---------- 按钮 ----------
    # ★clam 默认 `-width 11`（字符）⇒ 每个按钮最小 105px，一行塞不下几个。
    #   width=0 = 按文字自适应：2 字按钮 ~46px，工具条立刻宽松一倍。
    st.configure('TButton', width=0, padding=(11, 5), **_FLAT)
    st.map('TButton',
           background=[('disabled', T['surface_2']), ('pressed', T['surface_3']),
                       ('active', T['surface_3'])],
           foreground=[('disabled', T['muted_soft'])],
           bordercolor=[('disabled', T['line_soft']), ('active', T['line'])],
           lightcolor=[('active', T['surface_3']), ('pressed', T['surface_3'])],
           darkcolor=[('active', T['surface_3']), ('pressed', T['surface_3'])])

    # 主操作：深板岩底 + 白字
    st.configure('Primary.TButton', width=0, padding=(13, 5), borderwidth=1, relief='solid',
                 background=T['rail'], foreground='#ffffff',
                 lightcolor=T['rail'], darkcolor=T['rail'], bordercolor=T['rail'])
    st.map('Primary.TButton',
           background=[('disabled', '#c9d2df'), ('pressed', '#0f1620'),
                       ('active', T['rail_3'])],
           foreground=[('disabled', '#ffffff')],
           bordercolor=[('disabled', '#c9d2df'), ('active', T['rail_3'])],
           lightcolor=[('pressed', '#0f1620'), ('active', T['rail_3'])],
           darkcolor=[('pressed', '#0f1620'), ('active', T['rail_3'])])

    # 次操作：白底细边（等同 TButton，但字更重）
    st.configure('Secondary.TButton', width=0, padding=(11, 5),
                 font=(FONT_UI, 9, 'bold'), **_FLAT)

    # 文字/幽灵按钮
    st.configure('Ghost.TButton', width=0, padding=(8, 4), borderwidth=0, relief='flat',
                 background=T['surface'], foreground=T['ink_2'],
                 lightcolor=T['surface'], darkcolor=T['surface'])
    st.map('Ghost.TButton',
           background=[('active', T['surface_3']), ('pressed', T['surface_3'])],
           lightcolor=[('active', T['surface_3']), ('pressed', T['surface_3'])],
           darkcolor=[('active', T['surface_3']), ('pressed', T['surface_3'])],
           foreground=[('disabled', T['muted_soft'])])

    st.configure('Danger.TButton', width=0, padding=(11, 5), borderwidth=1, relief='solid',
                 background=T['surface'], foreground=T['danger'],
                 lightcolor=T['surface'], darkcolor=T['surface'],
                 bordercolor='#e2b4b0')
    st.map('Danger.TButton',
           background=[('active', '#fdf0ef'), ('pressed', '#fbe4e2')],
           lightcolor=[('active', '#fdf0ef'), ('pressed', '#fbe4e2')],
           darkcolor=[('active', '#fdf0ef'), ('pressed', '#fbe4e2')])

    st.configure('TCheckbutton', background=T['canvas'], foreground=T['ink'],
                 indicatorcolor=T['surface'], focuscolor=T['accent'])
    st.map('TCheckbutton', background=[('active', T['canvas'])])
    st.configure('Surface.TCheckbutton', background=T['surface'])
    st.map('Surface.TCheckbutton', background=[('active', T['surface'])])
    st.configure('TRadiobutton', background=T['surface'], foreground=T['ink'],
                 indicatorcolor=T['surface'], focuscolor=T['accent'])

    # ---------- 输入 ----------
    for name in ('TEntry', 'TSpinbox', 'TCombobox'):
        st.configure(name, padding=5, fieldbackground=T['surface'],
                     background=T['surface'], foreground=T['ink'],
                     bordercolor=T['line'], lightcolor=T['surface'],
                     darkcolor=T['surface'], insertcolor=T['ink'],
                     arrowcolor=T['muted'], arrowsize=13)
        st.map(name,
               bordercolor=[('focus', T['accent']), ('active', T['line_soft'])],
               lightcolor=[('focus', T['accent'])],
               darkcolor=[('focus', T['accent'])],
               fieldbackground=[('disabled', T['surface_2']), ('readonly', T['surface'])],
               foreground=[('disabled', T['muted_soft'])])

    # ---------- 表格 ----------
    st.configure('Treeview', background=T['surface'], fieldbackground=T['surface'],
                 foreground=T['ink'], borderwidth=0, relief='flat',
                 rowheight=26, font=(FONT_UI, 9))
    st.map('Treeview',
           background=[('selected', T['rail'])],
           foreground=[('selected', '#ffffff')])
    st.configure('Treeview.Heading', background=T['surface_2'], foreground=T['muted'],
                 relief='flat', borderwidth=0, padding=(8, 7),
                 font=(FONT_UI, 9, 'bold'))
    st.map('Treeview.Heading', background=[('active', T['surface_3'])],
           foreground=[('active', T['ink'])])
    # 去掉 clam 表头的立体边
    try:
        st.layout('Treeview.Heading', [('Treeheading.cell', {'sticky': 'nswe'})])
    except Exception:
        pass

    # ---------- 滚动条 ----------
    # ★§286：thumb 原来跟槽只差 8 级灰度 ⇒ 几乎看不见。现在 thumb 明显深一档，
    #   悬停再深一档（鼠标放上去能看出"这个能拖"）。
    for _orient in ('Vertical', 'Horizontal'):
        # thumb #8b9ab0 对槽 2.6:1、对白底 3.0:1 —— 看得见，又不喧宾夺主
        st.configure('%s.TScrollbar' % _orient,
                     background='#8b9ab0', troughcolor='#eaeff6',
                     bordercolor='#dde4ee', lightcolor='#8b9ab0',
                     darkcolor='#8b9ab0', arrowcolor='#5b6675',
                     arrowsize=14, relief='flat', width=14)
        st.map('%s.TScrollbar' % _orient,
               background=[('pressed', '#5d6d85'), ('active', '#6f8098')],
               lightcolor=[('pressed', '#5d6d85'), ('active', '#6f8098')],
               darkcolor=[('pressed', '#5d6d85'), ('active', '#6f8098')])

    # ★§286：地图页画布是深色（#12141a）⇒ 专用的深色滚动条，仍然比画布亮一档
    for _orient in ('Vertical', 'Horizontal'):
        st.configure('Map.%s.TScrollbar' % _orient,
                     background='#6b7d97', troughcolor='#232a35',
                     bordercolor='#232a35', lightcolor='#6b7d97',
                     darkcolor='#6b7d97', arrowcolor='#a9b8cc',
                     arrowsize=14, relief='flat', width=14)
        st.map('Map.%s.TScrollbar' % _orient,
               background=[('pressed', '#a9b8cc'), ('active', '#8ba0bd')],
               lightcolor=[('pressed', '#a9b8cc'), ('active', '#8ba0bd')],
               darkcolor=[('pressed', '#a9b8cc'), ('active', '#8ba0bd')])

    # ---------- 容器 ----------
    st.configure('TLabelframe', background=T['surface'], bordercolor=T['line'],
                 borderwidth=1, relief='solid', padding=10)
    st.configure('TLabelframe.Label', background=T['surface'], foreground=T['muted'],
                 font=(FONT_UI, 9, 'bold'))
    st.configure('Card.TLabelframe', background=T['surface'], bordercolor=T['line'],
                 borderwidth=1, relief='solid', padding=12)
    st.configure('Card.TLabelframe.Label', background=T['surface'],
                 foreground=T['ink'], font=(FONT_UI, 10, 'bold'))

    st.configure('TSeparator', background=T['line'])

    st.configure('TNotebook', background=T['surface'], borderwidth=0,
                 bordercolor=T['line'])
    st.configure('TNotebook.Tab', padding=(12, 6), background=T['surface_2'],
                 foreground=T['muted'], lightcolor=T['surface_2'],
                 darkcolor=T['surface_2'], bordercolor=T['line'])
    st.map('TNotebook.Tab',
           background=[('selected', T['surface'])],
           lightcolor=[('selected', T['surface'])],
           darkcolor=[('selected', T['surface'])],
           foreground=[('selected', T['ink'])],
           expand=[('selected', (0, 0, 0, 0))])

    # ---------- 进度条 ----------
    st.configure('TProgressbar', thickness=6, background=T['accent'],
                 troughcolor=T['surface_3'], bordercolor=T['surface_3'],
                 lightcolor=T['accent'], darkcolor=T['accent'])
    st.configure('Sunken.TProgressbar', thickness=4)

    st.configure('TPanedwindow', background=T['canvas'], sashwidth=8)
    return st


# ---------------------------------------------------------------- 组件
class NavRail(tk.Frame):
    """左侧导航条 + 右侧内容区（替代 10 个横向页签，横着放不下会被裁字）。

    对外装作 ttk.Notebook 的一个子集：`add(frame, text=)` / `select()` / `index()` /
    `tabs()`，并在切换时 `event_generate('<<NotebookTabChanged>>')`
    ⇒ 主程序里 `self.nb.index(self.nb.select())` 那套逻辑一行都不用改。
    """

    WIDTH = 172

    def __init__(self, master, **kw):
        kw.setdefault('bg', T['canvas'])
        kw.setdefault('highlightthickness', 0)
        kw.setdefault('bd', 0)
        tk.Frame.__init__(self, master, **kw)
        self._pages = []
        self._rows = []
        self._cur = -1
        self._group = None

        self.strip = tk.Frame(self, bg=T['rail'], width=px(self.WIDTH))
        self.strip.pack(side='left', fill='y')
        self.strip.pack_propagate(False)

        self.deck = tk.Frame(self, bg=T['canvas'])
        self.deck.pack(side='left', fill='both', expand=True)

    # ---- 导航项 ----
    def _caption(self, text):
        tk.Label(self.strip, text=text, bg=T['rail'], fg=T['rail_text'],
                 font=(FONT_UI, 8, 'bold'), anchor='w').pack(
            fill='x', padx=(18, 10), pady=(14, 4))

    def add(self, frame, text=''):
        idx = len(self._pages)
        self._pages.append(frame)
        label = (text or '').strip()
        if idx == 0:
            self._caption('存档')
        elif idx == 2:
            self._caption('触发器')
        elif idx == 6:
            self._caption('工具')

        row = tk.Frame(self.strip, bg=T['rail'], cursor='hand2')
        row.pack(fill='x')
        bar = tk.Frame(row, bg=T['rail'], width=3)
        bar.pack(side='left', fill='y')
        lab = tk.Label(row, text=label, bg=T['rail'], fg=T['rail_text'],
                       font=(FONT_UI, 10), anchor='w', padx=13, pady=7)
        lab.pack(side='left', fill='x', expand=True)

        for w in (row, lab):
            w.bind('<Button-1>', lambda e, i=idx: self.select(i))
            w.bind('<Enter>', lambda e, i=idx: self._hover(i, True))
            w.bind('<Leave>', lambda e, i=idx: self._hover(i, False))
        self._rows.append({'row': row, 'bar': bar, 'lab': lab})
        if idx == 0:
            self._cur = 0
            self._paint()
        return frame

    def _hover(self, idx, on):
        if idx == self._cur:
            return
        bg = T['rail_2'] if on else T['rail']
        self._rows[idx]['row'].config(bg=bg)
        self._rows[idx]['lab'].config(bg=bg)

    def _paint(self):
        for i, r in enumerate(self._rows):
            on = (i == self._cur)
            bg = T['rail_2'] if on else T['rail']
            r['row'].config(bg=bg)
            r['lab'].config(bg=bg,
                            fg='#ffffff' if on else T['rail_text'],
                            font=(FONT_UI, 10, 'bold' if on else 'normal'))
            r['bar'].config(bg=T['accent'] if on else bg)

    # ---- Notebook 兼容层 ----
    def select(self, which=None):
        if which is None:
            return str(self._pages[self._cur]) if self._pages else ''
        idx = self._resolve(which)
        if idx is None:
            return ''
        changed = (idx != self._cur)
        self._cur = idx
        for i, p in enumerate(self._pages):
            if i == idx:
                p.pack(in_=self.deck, fill='both', expand=True)
            else:
                p.pack_forget()
        self._paint()
        if changed:
            self.event_generate('<<NotebookTabChanged>>', when='tail')
        return str(self._pages[idx])

    def index(self, which):
        idx = self._resolve(which)
        if idx is None:
            raise ValueError('not a page: %r' % (which,))
        return idx

    def tabs(self):
        return tuple(str(p) for p in self._pages)

    def _resolve(self, which):
        if isinstance(which, int):
            return which if 0 <= which < len(self._pages) else None
        if isinstance(which, str):
            for i, p in enumerate(self._pages):
                if str(p) == which:
                    return i
            return None
        try:
            return self._pages.index(which)
        except ValueError:
            return None

    # ---- 便捷 ----
    def current_index(self):
        return self._cur


def page_header(parent, title, hint=''):
    """页面标题行：左标题 + 右灰字提示（不占据工作区高度）。"""
    wrap = tk.Frame(parent, bg=T['canvas'])
    tk.Label(wrap, text=title, bg=T['canvas'], fg=T['ink'],
             font=(FONT_UI, 13, 'bold')).pack(side='left')
    if hint:
        tk.Label(wrap, text=hint, bg=T['canvas'], fg=T['muted'],
                 font=(FONT_UI, 9)).pack(side='left', padx=(10, 0), pady=(3, 0))
    return wrap


def note_box(parent, header=None, label='说明'):
    """可折叠的「说明」区：默认收起，避免几堵字墙把工作区挤没。

    返回 (box, button)：把原来那段长说明 Label 建在 `box` 里即可；
    `button` 会被放进 `header`（没有 header 就放 parent）的右侧。
    """
    box = tk.Frame(parent, bg=T['canvas'])
    on = tk.BooleanVar(value=False)

    def _toggle():
        on.set(not on.get())
        if on.get():
            kw = {'fill': 'x', 'pady': (0, 8)}
            if header is not None:
                kw['after'] = header
            box.pack(**kw)
        else:
            box.pack_forget()
        btn.config(text=('%s ▾' if on.get() else '%s ▸') % label)

    btn = ttk.Button(header if header is not None else parent,
                     text='%s ▸' % label, style='Ghost.TButton',
                     command=_toggle)
    btn.pack(side='right')
    return box, btn


def card(parent, title=None, pad=12):
    """白面卡片：1px 边框，可选标题行。返回 (外框, 内容区)。"""
    outer = tk.Frame(parent, bg=T['line'], bd=0, highlightthickness=0)
    inner = tk.Frame(outer, bg=T['surface'], bd=0, highlightthickness=0)
    inner.pack(fill='both', expand=True, padx=1, pady=1)
    if title:
        head = tk.Frame(inner, bg=T['surface'])
        head.pack(fill='x', padx=pad, pady=(pad - 2, 0))
        tk.Label(head, text=title, bg=T['surface'], fg=T['ink'],
                 font=(FONT_UI, 10, 'bold')).pack(side='left')
        tk.Frame(inner, bg=T['line_soft'], height=1).pack(fill='x', padx=pad, pady=(8, 0))
    body = tk.Frame(inner, bg=T['surface'])
    body.pack(fill='both', expand=True, padx=pad, pady=(pad - 4, pad))
    return outer, body


def section(parent, title, hint='', gap=6, pad=5, title_w=84):
    """横条式分区：左侧标题列（3px 板岩条 + 粗体标题），右侧内容区。

    返回 (outer, body)。`body` 里直接 pack 行（或 grid）；多行内容都在同一张卡里。
    ★ 高度 = 内容行数（标题不额外占高），所以「分组」不会把工作区挤小。
    """
    outer = tk.Frame(parent, bg=T['line'], bd=0, highlightthickness=0)
    outer.pack(fill='x', pady=(0, gap))

    inner = tk.Frame(outer, bg=T['surface'], bd=0, highlightthickness=0)
    inner.pack(fill='both', expand=True, padx=1, pady=1)

    head = tk.Frame(inner, bg=T['surface'], width=title_w)
    head.pack(side='left', fill='y')
    head.pack_propagate(False)
    tk.Frame(head, bg=T['rail'], width=3).pack(side='left', fill='y')
    hold = tk.Frame(head, bg=T['surface'])
    hold.pack(side='left', fill='both', expand=True, padx=(8, 4), pady=pad)
    tk.Label(hold, text=title, bg=T['surface'], fg=T['ink'],
             font=(FONT_UI, 9, 'bold'), anchor='w').pack(fill='x')
    if hint:
        tk.Label(hold, text=hint, bg=T['surface'], fg=T['muted_soft'],
                 font=(FONT_UI, 8), anchor='w', justify='left',
                 wraplength=title_w - 12).pack(fill='x')

    body = tk.Frame(inner, bg=T['surface'])
    body.pack(side='left', fill='both', expand=True, padx=(0, 8), pady=pad)
    return outer, body


def field_row(parent, label, value='-', row=0, mono=False, width=None):
    """卡片内的 标签/值 一行（标签灰、值黑，值可选等宽）。"""
    lab = tk.Label(parent, text=label, bg=T['surface'], fg=T['muted'],
                   font=(FONT_UI, 9), anchor='w')
    lab.grid(row=row, column=0, sticky='w', pady=3)
    val = tk.Label(parent, text=value, bg=T['surface'], fg=T['ink'],
                   font=(FONT_MONO, 9) if mono else (FONT_UI, 10), anchor='w')
    val.grid(row=row, column=1, sticky='w', padx=(14, 0), pady=3)
    if width:
        lab.config(width=width)
    return lab, val
