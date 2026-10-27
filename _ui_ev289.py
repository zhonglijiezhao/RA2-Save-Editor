# -*- coding: utf-8 -*-
"""§289 自检：地图页四张横条式分区 + 全页无横向溢出 + 低频操作收进菜单。

为什么单独立一套：这一轮改的是**布局**（按钮最小宽度、分区容器、行内控件宽度），
翻车方式都是"某个按钮/文字被窗口右缘吃掉"，靠肉眼看图靠不住 ⇒ 用 reqwidth 硬量。
"""
import io
import os
import sys
import importlib.util

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

spec = importlib.util.spec_from_file_location('g', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
import ui_theme  # noqa: E402

PASS = []
FAIL = []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print('  [%s] %s' % ('PASS' if cond else 'FAIL', msg))


app = m.App()
app.geometry('1280x820')
app.update()
app.nb.select(6)                     # 地图 · 选位
for _ in range(6):
    app.update()
page = app.nb._pages[6]

# ── ① 四张分区，标题与顺序 ──
sections = []
for w in page.winfo_children():
    if w.winfo_class() != 'Frame':
        continue
    kids = w.winfo_children()
    if len(kids) != 1:
        continue
    sub = kids[0].winfo_children()
    if len(sub) != 2:
        continue
    head, body = sub
    try:
        title = head.winfo_children()[1].winfo_children()[0].cget('text')
    except Exception:
        continue
    if not title:
        continue
    sections.append((title, body))

titles = [t for t, _ in sections]
ck(titles == ['地图', '视图', '放置', '选中格'], '四张分区且顺序正确：%s' % titles)

# ── ② 每行不溢出（核心断言：reqwidth ≤ body 宽）──
total_rows = 0
for title, body in sections:
    have = body.winfo_width()
    for k, row in enumerate(body.winfo_children()):
        total_rows += 1
        need = row.winfo_reqwidth()
        ck(need <= have, '[%s] 第 %d 行 %d ≤ %d（余 %d）'
           % (title, k + 1, need, have, have - need))
ck(total_rows >= 7, '四张卡共 %d 行控件（原来 8 行散排）' % total_rows)

# ── ③ 三个 ▾ 菜单都在，且项数对 ──
menus = {}
for title, body in sections:
    for row in body.winfo_children():
        for c in row.winfo_children():
            if c.winfo_class() != 'TMenubutton':
                continue
            label = c.cget('text')
            try:
                menu = app.nametowidget(str(c.cget('menu')))
                menus[label] = menu
            except Exception:
                menus[label] = None
ck(set(menus) == {'叠加 ▾', '更多 ▾', '修旧楼 ▾'}, '三个折叠菜单：%s' % sorted(menus))
for label, want in (('叠加 ▾', 3), ('更多 ▾', 4), ('修旧楼 ▾', 3)):
    menu = menus.get(label)
    n = 0
    if menu is not None:
        try:
            # separator 也算一项，只数真命令/勾选项
            for i in range(menu.index('end') + 1):
                if menu.type(i) != 'separator':
                    n += 1
        except Exception:
            n = 0
    ck(n == want, '%s 有 %d 项可点（期望 %d）' % (label, n, want))

# ── ④ 属性没被改坏（回调都靠这些名字）──
for name in ('mv_type', 'mv_house', 'mv_unit', 'mv_u_house', 'mv_u_vet', 'mv_u_nudge',
             'mv_via', 'mv_ev', 'mv_terr_src', 'mv_theater_var', '_cb_theater',
             'mv_clip_lbl', 'mv_urule', 'mv_lbl', 'mv_info', 'mv_grid', 'mv_ovl',
             'mv_wp', 'mv_units', 'mv_terr', 'mv_src_save'):
    ck(hasattr(app, name), '属性 %s 还在' % name)

ck(app.mv_clip is None, '剪贴板初始为空（mv_clip 定义在按钮之后也没事）')
ck(app._cb_theater.cget('values') and len(app._cb_theater.cget('values')) == 6,
   '剧场下拉 6 个选项')

# ── ⑤ 状态文本不再超长（§289：原来 50+ 字拼接被右缘裁）──
app._set_doc(m.SaveDoc(r'E:/YURI/SAVE4823.SAV'))
app.update()
ck(len(app.mv_lbl.cget('text')) <= 40,
   '卡内地图状态 %d 字（≤40）：%r' % (len(app.mv_lbl.cget('text')),
                                app.mv_lbl.cget('text')))
ck(app.mv_urule.winfo_ismapped(), '「规则来源」挂在页头且可见：%r'
   % (app.mv_urule.cget('text')[:28],))
ck(int(app.mv_info.cget('wraplength')) <= 720,
   '画布下提示 wraplength=%s（≤720，不再被裁）' % app.mv_info.cget('wraplength'))

# ── ⑥ 按钮最小宽度不再是 105（clam 的 -width 11）──
st = ui_theme.ttk.Style(app)
ck(str(st.lookup('TButton', 'width')) in ('0', 'None'), 'TButton width=%r（自适应）'
   % st.lookup('TButton', 'width'))
_b = [c for c in page.winfo_children()]      # 拿一个真按钮量一下
_w = None
for title, body in sections:
    for row in body.winfo_children():
        for c in row.winfo_children():
            if c.winfo_class() == 'TButton' and c.cget('text') in ('放置', '放单位', '重绘'):
                _w = c.winfo_reqwidth()
                break
ck(_w is not None and _w < 100, '两字按钮宽 %s px（<100，原来固定 105）' % _w)

# ── ⑦ 赠送建筑页三行也不溢出 ──
app.nb.select(7)
for _ in range(6):
    app.update()
gpage = app.nb._pages[7]
for w in gpage.winfo_children():
    if w.winfo_class() != 'Frame':
        continue
    need, have = w.winfo_reqwidth(), gpage.winfo_width()
    ck(need <= have + 2, '赠送页行 %d ≤ %d' % (need, have))

# ── ⑧ 逐页切换仍然正常 ──
ok = True
for i in range(len(app.nb.tabs())):
    try:
        app.nb.select(i)
        app.update()
    except Exception as e:
        ok = False
        print('   页 %d 切换失败：%r' % (i, e))
ck(ok, '10 页逐页切换无异常')

app.destroy()
print('\n结果：%d 通过 / %d 失败' % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print('  失败：', f)
sys.exit(1 if FAIL else 0)
