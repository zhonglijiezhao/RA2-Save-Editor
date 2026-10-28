# -*- coding: utf-8 -*-
"""§294 自检：另存为默认名不撞车 + 事件/行为页有滚动条。

两个都是 2026-10-28 玩家当场提的（截图 QQ20261028-123404）：
  ① 打开 `65_修改.SAV` 点「另存为」，默认名是 `65_修改_修改.SAV` —— 那正好是上一次
     另存为留下的文件 ⇒ Windows 立刻弹「要替换它吗」，玩家以为要覆盖原档。
     原档其实一点没动，但默认名撞上已存在文件这件事本身就该避免。
  ② 事件 / 行为两页的表格没有滚动条，大任务档上千条只能靠滚轮，列超宽看不到右半边。

跑法：python _selftest_ev294_saveas_scroll.py
"""
import io
import os
import sys
import importlib.util

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

SRC = r'E:/YURI/SAVE0665_修改.SAV'
TMPD = os.path.join(HERE, '_selftest_out', '_ev294')

spec = importlib.util.spec_from_file_location('g', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


app = m.App()
for _ in range(4):
    app.update()

# ── ① 另存为默认名：一定是"磁盘上不存在的新文件" ────────────
os.makedirs(TMPD, exist_ok=True)
for f in os.listdir(TMPD):
    os.remove(os.path.join(TMPD, f))


def touch(name):
    open(os.path.join(TMPD, name), 'wb').close()


src = os.path.join(TMPD, '65_修改.SAV')
touch('65_修改.SAV')
sug = app._suggest_save_name(src)
print('  场景A 原档 65_修改.SAV → 建议 %r' % sug)
ck(sug != os.path.basename(src), 'A: 建议名不等于原档名（不会覆盖原档）')
ck(sug == '65_修改_2.SAV', 'A: 已以 _修改 结尾就不再叠加（期望 65_修改_2.SAV）')

# 场景 B：第一次另存为，_修改 还不存在
src_b = os.path.join(TMPD, 'SAVE0665.SAV')
touch('SAVE0665.SAV')
sug_b = app._suggest_save_name(src_b)
print('  场景B 原档 SAVE0665.SAV → 建议 %r' % sug_b)
ck(sug_b == 'SAVE0665_修改.SAV', 'B: 首次另存为仍用 _修改 后缀（期望 SAVE0665_修改.SAV）')

# 场景 C：_修改 已经存在（上一次的产物）⇒ 必须往后找
touch('SAVE0665_修改.SAV')
sug_c = app._suggest_save_name(src_b)
print('  场景C 已有 SAVE0665_修改.SAV → 建议 %r' % sug_c)
ck(sug_c == 'SAVE0665_修改_2.SAV', 'C: 撞车时自动让到 _2（期望 SAVE0665_修改_2.SAV）')
ck(not os.path.exists(os.path.join(TMPD, sug_c)), 'C: 建议名在磁盘上确实不存在')

# 场景 D：连撞两次
touch('SAVE0665_修改_2.SAV')
sug_d = app._suggest_save_name(src_b)
print('  场景D 再撞一次 → 建议 %r' % sug_d)
ck(sug_d == 'SAVE0665_修改_3.SAV', 'D: 继续让到 _3（期望 SAVE0665_修改_3.SAV）')
ck(not os.path.exists(os.path.join(TMPD, sug_d)), 'D: 建议名在磁盘上确实不存在')

# 场景 E：反复另存为不会滚出 _修改_修改_修改
p = src
for i in range(4):
    nxt = os.path.join(TMPD, app._suggest_save_name(p))
    touch(os.path.basename(nxt))
    p = nxt
    print('  场景E 第 %d 次 → %r' % (i + 1, os.path.basename(p)))
ck(p.count('_修改') <= 1, 'E: 连续另存 4 次，名字里 "_修改" 最多出现 1 次（实得 %r）'
   % os.path.basename(p))

# ── ② 事件 / 行为页要有滚动条 ────────────────────────────────
app.load_doc(SRC)
import time
t0 = time.time()
while app.doc is None and time.time() - t0 < 90:
    app.update()
    time.sleep(0.02)
for _ in range(600):
    app.update()
    if not getattr(app, '_st_steps', None):
        break
    time.sleep(0.01)
ck(app.doc is not None, '存档已载入')

for wrap_name, tv_name, label, idx in (('_te_wrap', 'te', '事件页', 3),
                                       ('_ta_wrap', 'ta', '行为页', 4)):
    app.nb.select(idx)
    for _ in range(6):
        app.update()
    wrap = getattr(app, wrap_name, None)
    tv = getattr(app, tv_name, None)
    ck(wrap is not None and tv is not None, '%s：容器与表格都在' % label)
    if wrap is None:
        continue
    ck(tv.winfo_ismapped(), '%s：表格已显示' % label)
    kids = wrap.winfo_children()
    kinds = [k.winfo_class() for k in kids]
    n_sb = kinds.count('TScrollbar')
    ck(n_sb == 2, '%s：容器里有 2 条滚动条（实得 %d：%s）' % (label, n_sb, kinds))
    ck(bool(tv.cget('yscrollcommand')), '%s：表格的 yscrollcommand 已连上' % label)
    ck(bool(tv.cget('xscrollcommand')), '%s：表格的 xscrollcommand 已连上' % label)
    # 竖滚动条应当 fill=y 贴在右边、横的在底部
    vs = [k for k in kids if k.winfo_class() == 'TScrollbar' and str(k.cget('orient')) == 'vertical']
    hs = [k for k in kids if k.winfo_class() == 'TScrollbar' and str(k.cget('orient')) == 'horizontal']
    ck(len(vs) == 1 and len(hs) == 1, '%s：竖 1 条 / 横 1 条' % label)
    if vs and hs:
        ck(vs[0].winfo_height() > 10, '%s：竖滚动条已布局（高 %d）' % (label, vs[0].winfo_height()))
        ck(hs[0].winfo_width() > 10, '%s：横滚动条已布局（宽 %d）' % (label, hs[0].winfo_width()))
    # 表格要真的占满剩余空间（别被滚动条挤成一条缝）
    ck(tv.winfo_height() > 100, '%s：表格高度 %d > 100' % (label, tv.winfo_height()))

# ── ③ 滚动条确实能滚（内容超出时 yview 不再是 (0,1)）──────────
app.nb.select(3)          # 事件页（0=概览 1=所属方 2=触发 3=事件 4=行为）
for _ in range(6):
    app.update()
if len(app.te.get_children()) > 16:
    ck(app.te.yview()[1] < 1.0,
       '事件页 %d 行 > 可见 16 行 ⇒ 竖滚动条可用（yview=%s）'
       % (len(app.te.get_children()), app.te.yview()))
else:
    print('  事件行数 %d ≤ 16，跳过"可滚"断言' % len(app.te.get_children()))

app.destroy()
try:
    for f in os.listdir(TMPD):
        os.remove(os.path.join(TMPD, f))
    os.rmdir(TMPD)
except Exception:
    pass

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败项：')
    for f in FAIL:
        print('  - ' + f)
sys.exit(1 if FAIL else 0)
