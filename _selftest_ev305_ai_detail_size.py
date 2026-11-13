# -*- coding: utf-8 -*-
"""§305 自检：AI 页「明细区」尺寸 + 分栏不裁控件。

★2026-11-13（玩家截图）：「箭头处的窗口太小了 加大，上面的主窗口可以缩小」。
  截图里箭头指的就是「作战小队」页底部那块灰底明细文本 —— 当时它和上面的笔记本
  都 `pack(fill='both', expand=True)`，笔记本先吃掉富余高度，明细只剩两行半；
  窗口再矮一点就**被底边直接裁掉**（文字画到窗口外）。

  改法：两者放进竖向 `ttk.PanedWindow`（可拖），首屏把 sash 摆到明细占 4 成左右，
  上限受"上格至少留 440px"约束；明细 8 行 → 14 行 + 竖滚动条 + 软换行。
  连带：特遣部队子页的成员表和两行控件改成**先占底部**，窗口变矮时只缩上面那棵列表
  （旧写法最后 pack 的成员框会被压成 1px ⇒ 按钮集体消失）。

本自检钉住四件事（不依赖具体存档里的数据量，靠控件几何）：
  1. 明细文本区高度 ≥ 200px（改之前约 40px）；
  2. 明细区不再越出窗口（控件底边 ≤ 窗口底边，旧写法这里是 False）；
  3. 上格（笔记本）高度 ≥ 440px 的默认配额 ⇒ 列表还能看到好几行；
  4. 特遣部队子页的成员表/数目/种数控件高度正常（没被压成 1px）。

跑法：D:/py/python.exe _selftest_ev305_ai_detail_size.py [存档]
"""
import importlib.util
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

SAV = sys.argv[1] if len(sys.argv) > 1 else r'E:/YURI/ALL01UMD.sav'
PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


spec = importlib.util.spec_from_file_location('gui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

if not os.path.exists(SAV):
    print('（跳过：样本不存在 %s）' % SAV)
    raise SystemExit(0)

app = m.App()
app.geometry('1600x1000+10+10')          # 接近玩家实际窗口（1920 屏最大化）
for _ in range(30):
    app.update()
app._set_doc(m.SaveDoc(SAV))
for _ in range(20):
    app.update()
app.nb.select(9)                          # 作战小队
for _ in range(10):
    app.update()

rows = app.tat.get_children()
if rows:
    app.tat.selection_set(rows[0])
for _ in range(10):
    app.update()
app.update_idletasks()

# 1) 明细文本区够高
h_text = app.ai_text.winfo_height()
ck(h_text >= 200, '明细文本区高度 %dpx ≥ 200（旧版约 40px / 两行半）' % h_text)

# 2) 明细区没越出窗口底边（旧写法在这里会被窗口裁掉）
win_bottom = app.winfo_rooty() + app.winfo_height()
det_bottom = app.ai_text.winfo_rooty() + app.ai_text.winfo_height()
ck(det_bottom <= win_bottom, '明细区底边 %d ≤ 窗口底边 %d（没被裁）'
   % (det_bottom, win_bottom))

# 3) 上格还留着干活的地方
h_top = app.ai_sub.winfo_height()
ck(h_top >= 440, '上格（三大件表格）默认高度 %dpx ≥ 440' % h_top)

# 4) 分栏真的存在、sash 摆过（不是默认 0/1px 的死位置）
ck(hasattr(app, 'ai_split') and app.ai_split.winfo_height() > 0,
   '存在竖向分栏 ai_split（高 %d）' % getattr(app.ai_split, 'winfo_height', lambda: -1)())
try:
    ck(0 < app.ai_split.sashpos(0) < app.ai_split.winfo_height(),
       'sash 位置 %d 落在分栏内部（可继续拖动）' % app.ai_split.sashpos(0))
except Exception as e:
    ck(False, 'sashpos 读取失败：%s' % e)

# 5) 特遣部队子页：成员表 + 两行控件没被压成 1px
app.ai_sub.select(1)
for _ in range(10):
    app.update()
app.update_idletasks()
ck(app.tfm.winfo_height() >= 60, '成员表高度 %dpx ≥ 60（4 行）' % app.tfm.winfo_height())
# 成员框里除成员表外的两行控件（数目/类型/应用/添加/删除 与 种数/改种数/清空）：
# 旧写法它们会被压成 1px ⇒ 直接量这两行 Frame 的高度。
_box = app.tfm.master.master
_rows = [c for c in _box.winfo_children() if c is not app.tfm.master]
_hs = [c.winfo_height() for c in _rows]
ck(len(_hs) >= 2 and min(_hs) >= 25,
   '成员框内 %d 行控件高度 %s（每行 ≥ 25px ⇒ 按钮没被压扁）' % (len(_hs), _hs))
ck(app.tf_type.winfo_height() >= 20, '「类型」下拉高度 %dpx ≥ 20'
   % app.tf_type.winfo_height())
ck(app.tft.winfo_height() >= 80, '特遣部队列表还留着 %dpx（≥80 ⇒ 能看 4 行）'
   % app.tft.winfo_height())

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
raise SystemExit(1 if FAIL else 0)
