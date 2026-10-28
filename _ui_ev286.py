# -*- coding: utf-8 -*-
"""v1.3 视觉改版自检（§284）：主题层 / 左侧导航 / 概览三卡 / 列表列宽 / 折叠说明。

跑法：D:/py/python.exe _ui_ev286.py
"""
import io
import importlib.util
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

spec = importlib.util.spec_from_file_location('g', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from editor_core import SaveDoc          # noqa: E402
import ui_theme                          # noqa: E402

PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


app = m.App()
app.geometry('1280x820+40+30')
app.update()

# ① 主题
st = app.style
ck(st.theme_use() == 'clam', 'clam 打底')
ck(int(st.lookup('Treeview', 'rowheight')) == 26, 'Treeview 行高 26')
ck(st.lookup('Primary.TButton', 'background') == ui_theme.T['rail'],
   '主按钮用板岩底（%s）' % st.lookup('Primary.TButton', 'background'))
ck(st.lookup('Treeview.Heading', 'relief') == 'flat', '表头扁平（无 3D 斜边）')
ck(app.cget('bg') == ui_theme.T['canvas'], '窗口底色 = canvas token')

# ①b 滚动条对比度（玩家：「这两个拖动地图的条 色差太小了 看不清」）


def _lum(c):
    def f(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def _cr(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _rgb(spec):
    spec = spec.lstrip('#')
    return tuple(int(spec[i:i + 2], 16) for i in (0, 2, 4))


for _st_name, _min in (('Vertical.TScrollbar', 2.0), ('Map.Vertical.TScrollbar', 3.0)):
    _bg = st.lookup('%s' % _st_name, 'background')
    _tr = st.lookup(_st_name, 'troughcolor')
    ck(_bg and _tr and _cr(_rgb(_bg), _rgb(_tr)) >= _min,
       '%s thumb/slot 对比 %.2f（≥%.1f）' % (_st_name, _cr(_rgb(_bg), _rgb(_tr)), _min))
# 地图页滚动条必须比画布(#12141a)亮一档，否则贴在深底上还是看不见
_mmap = st.lookup('Map.Vertical.TScrollbar', 'background')
ck(_cr(_rgb(_mmap), (0x12, 0x14, 0x1a)) >= 3.0,
   '地图滚动条 vs 画布 对比 %.2f（≥3）' % _cr(_rgb(_mmap), (0x12, 0x14, 0x1a)))

# ①c 滚动条宽度 = 14px（玩家 4K 屏上 12px 仍偏细）
for _st_name in ('Vertical.TScrollbar', 'Horizontal.TScrollbar',
                 'Map.Vertical.TScrollbar', 'Map.Horizontal.TScrollbar'):
    _w = st.lookup(_st_name, 'width')
    ck(_w is not None and int(_w) >= 14, '%s 宽 %s px（≥14）' % (_st_name, _w))

# ①d 按钮不再被 clam 的 `-width 11`（105px 下限）撑大
_bw = app.winfo_toplevel()
_btn_req = None
try:
    import ui_theme as _ut
    _btn_req = st.lookup('TButton', 'width')
except Exception:
    pass
ck(_btn_req is not None and str(_btn_req) in ('0', 0),
   'TButton width 已归零（按文字自适应，实际 %r）' % (_btn_req,))

# ② 左侧导航（不再是 10 个横向页签）
ck(isinstance(app.nb, ui_theme.NavRail), '主容器是 NavRail（不是 Notebook）')
# ★2026-10-28：新增「武器 · 弹头」页 ⇒ 10 → 11（新页一律追加在末尾，
#   不能插在中间，否则按固定索引取页的老自检全打歪）
ck(len(app.nb.tabs()) == 11, '11 个页签：%d' % len(app.nb.tabs()))
for i in range(10):
    app.nb.select(i)
    app.update()
    if app.nb.index(app.nb.select()) != i:
        ck(False, '切页 %d 失败' % i)
        break
else:
    ck(True, '10 页逐页切换正常')
ck(app.nb.strip.winfo_width() == ui_theme.NavRail.WIDTH,
   '导航条宽 %d' % app.nb.strip.winfo_width())
ck(app.nb.deck.winfo_width() > 600, '内容区宽 %d（>600）' % app.nb.deck.winfo_width())

# ③ 存档列表：3 列，列宽不超出卡片
app.nb.select(0)
app.update()
cols = app.tree['columns']
ck(tuple(cols) == ('file', 'time', 'money'), '列表 3 列 %s' % (tuple(cols),))
widths = [int(app.tree.column(c, 'width')) for c in cols]
ck(sum(widths) <= app.tree.winfo_width(),
   '列宽合计 %d ≤ 树宽 %d' % (sum(widths), app.tree.winfo_width()))

# ④ 开一份真档，概览三卡
sav = r'E:/YURI/SAVE4823.SAV'
if os.path.exists(sav):
    t0 = time.time()
    app._set_doc(SaveDoc(sav))
    app.update()
    ck(time.time() - t0 < 30, '开档 %.1fs' % (time.time() - t0))
    ck(len(app.info_labels) == 12, '概览 12 个字段')
    ck(app.lbl_money.cget('text').strip() != '', '金钱有值：%s' % app.lbl_money.cget('text'))
    ck(app.lbl_map.cget('text') != '-', '地图卡已填：%s' % app.lbl_map.cget('text'))
    ck(app.ent_money.winfo_ismapped() and app.lbl_money.winfo_ismapped(),
       '金钱卡控件可见')
    ck(app.lbl_cur.cget('text').startswith('已打开'), '操作条显示当前存档')
else:
    ck(True, '本机无测试档，跳过开档相关')

# ⑤ 折叠说明：默认收起，点一下展开
app.nb.select(5)          # 挂接页
app.update()
box = getattr(app, '_note_box_attach', None)
btn = [w for w in box.master.winfo_children()
       if w.winfo_class() in ('TFrame', 'Frame') and w.winfo_height() < 60]
ck(box is not None, '挂接页有折叠说明容器')
ck(not box.winfo_ismapped(), '说明默认收起')
try:
    # 直接调按钮命令（避免依赖真实点击坐标）
    hdr = box.master.winfo_children()[0]
    b = [w for w in hdr.winfo_children() if w.winfo_class() == 'TButton']
    b[0].invoke()
    app.update()
    ck(box.winfo_ismapped(), '点「说明 ▸」后展开')
    b[0].invoke()
    app.update()
    ck(not box.winfo_ismapped(), '再点一次收起')
except Exception as e:
    ck(False, '说明开关异常：%r' % (e,))

# ⑥ 触发页：按钮行不再溢出（说明按钮在标题行）
app.nb.select(2)
app.update()
ck(app.btn_trig_note.winfo_ismapped(), '触发页「说明」按钮可见（在标题行）')
ck(app.btn_trig_note.winfo_width() > 30,
   '说明按钮宽度 %d（没被挤成 1px）' % app.btn_trig_note.winfo_width())
ck(not getattr(app, '_trig_note_box').winfo_ismapped(), '触发页长说明默认收起')

app.destroy()
print()
print('结果：%d 通过 / %d 失败' % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
