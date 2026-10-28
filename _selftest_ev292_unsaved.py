# -*- coding: utf-8 -*-
"""§292 自检：未保存改动保护（关窗提醒 / 换档提醒 / 标题栏 * 标记）。

为什么单独立一套（2026-10-28 代码审查 P1-5）：
  全项目以前没有 `protocol('WM_DELETE_WINDOW')` —— 玩家改了一堆触发后直接点 ×，
  或者手滑在列表里点了另一个存档，改动**无声消失**，一句提示都没有。
  而现成的 `doc.dirty()` 又靠不住：它只统计 `pending_*` 那几类延迟写操作，
  像 `set_waypoint` / `add_action` 这种直接改明文的操作它看不到（本自检会把这个
  "漏报"当作反例断言，确保以后不会有人把判据换回 `dirty()`）。

跑法：python _selftest_ev292_unsaved.py
"""
import io
import os
import sys
import time
import importlib.util

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

SRC = r'E:/YURI/SAVE0665_修改.SAV'
TMP = os.path.join(HERE, '_selftest_out', '_ev292_unsaved.SAV')

spec = importlib.util.spec_from_file_location('g', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


if not os.path.exists(SRC):
    print('样本不存在：%s —— 跳过' % SRC)
    sys.exit(0)
os.makedirs(os.path.dirname(TMP), exist_ok=True)
if os.path.exists(TMP):
    os.remove(TMP)

app = m.App()
app.geometry('1280x820')
app.update()
app.load_doc(SRC)

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
if app.doc is None:
    app.destroy()
    sys.exit(1)

BASE_TITLE = 'RA2 / 尤里的复仇 — 存档编辑器'

# ── ① 刚打开：干净，标题无 * ──────────────────────────────
ck(not app._is_modified(), '刚打开时 _is_modified() = False')
app._refresh_title()
app.update()
ck(app.title() == BASE_TITLE + ' — ' + os.path.basename(SRC),
   '标题显示文件名、无 * 标记（实得 %r）' % app.title())
ck('*' not in app.title(), '标题里没有 * 标记')

# ── ② 改一处「dirty() 看不见」的东西：set_waypoint ─────────
app.doc.set_waypoint(1, 21, 21)
ck(app.doc.plain != app.doc.plain_orig, 'set_waypoint 确实改了明文')
ck(app.doc.dirty() is False,
   '反例成立：`dirty()` 对 set_waypoint 返回 False（所以不能用它做判据）')
ck(app._is_modified(), '★ `modified()` 正确识别出 set_waypoint 的改动')

# ── ③ 标题栏出现 * ─────────────────────────────────────
app._refresh_title()
app.update()
ck(app.title().endswith(' *'), '标题栏出现 " *" 未保存标记（实得 %r）' % app.title())

# ── ④ 三个选项的分支 ────────────────────────────────────
_orig_ask = m.messagebox.askyesnocancel
_orig_save_as = app.save_as
_save_as_called = []
app.save_as = lambda: _save_as_called.append(1)

try:
    m.messagebox.askyesnocancel = lambda *a, **k: None          # 取消
    ck(app._confirm_discard('测试') is False, '选「取消」⇒ 中止本次操作')

    m.messagebox.askyesnocancel = lambda *a, **k: False         # 否 = 丢弃
    ck(app._confirm_discard('测试') is True, '选「否」⇒ 丢弃改动、继续')
    ck(app._is_modified(), '（丢弃只是放行，明文此时还没回滚 —— 由换档覆盖）')

    m.messagebox.askyesnocancel = lambda *a, **k: True          # 是 = 去保存
    ck(app._confirm_discard('测试') is False, '选「是」⇒ 本次操作中止（先去保存）')
    ck(len(_save_as_called) == 1, '选「是」时确实调了 save_as()')

    # ── ⑤ 换档会被拦住 ──────────────────────────────────
    m.messagebox.askyesnocancel = lambda *a, **k: None
    before = app.doc.path
    app.load_doc(r'E:/YURI/QS_铁峰243_修改.SAV')
    ck(app.doc.path == before, '有未保存改动时，选「取消」不会换档')
    ck(getattr(app, '_pending_load', False) is False, '被拦住时不会留下 pending_load 脏状态')
finally:
    m.messagebox.askyesnocancel = _orig_ask
    app.save_as = _orig_save_as

# ── ⑥ 存完：标记消失、基准推进 ────────────────────────────
res = app.doc.save(TMP)
ck(app._is_modified() is False, '保存后 _is_modified() = False')
ck(app.doc.dirty() is False, '保存后 dirty() = False')
app._refresh_title()
app.update()
ck('*' not in app.title(), '保存后标题栏的 * 消失（实得 %r）' % app.title())
ck(os.path.basename(app.doc.path) == os.path.basename(TMP),
   '保存后 doc.path 推进到新文件（另存为后「原地保存」才会指向对的文件）')
ck(res.get('blocks') == res.get('blocks_expect'),
   '块布局核对通过（%s/%s）' % (res.get('blocks'), res.get('blocks_expect')))

# ── ⑦ 快捷键已绑定 ──────────────────────────────────────
for seq, label in (('<Control-s>', 'Ctrl+S 另存为'),
                   ('<Control-o>', 'Ctrl+O 打开'),
                   ('<F5>', 'F5 刷新')):
    ck(bool(app.bind(seq)), '%s 已绑定' % label)

app.destroy()
try:
    os.remove(TMP)
except Exception:
    pass

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败项：')
    for f in FAIL:
        print('  - ' + f)
sys.exit(1 if FAIL else 0)
