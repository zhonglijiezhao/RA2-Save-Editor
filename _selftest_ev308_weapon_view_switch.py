# -*- coding: utf-8 -*-
"""§308 自检：武器页「武器（按把）/ 单位」视图来回切换，列表不能消失。

★2026-11-13（玩家截图：「武器 按把 列表出不来」，页面上红字
  `切换视图失败：window "...!frame2" isn't packed`）：
  `_wpn_switch()` 从「单位」切回「按把」时，先给搜索行 pack(before=武器列表)，
  而那会儿武器列表刚被 pack_forget 掉 —— Tk 的 `before=` 要求目标**已 pack**，
  于是抛 TclError，被外层 try 吞掉，列表就再也不显示了。
  修法：先把列表 pack 回去，再把搜索行插到它前面。

这里钉住：进页面就有列表；单位→按把→单位 来回 3 轮，每轮两个列表都非空、
没有「切换视图失败」的红字、列表控件确实是 mapped 的。

跑法：D:/py/python.exe _selftest_ev308_weapon_view_switch.py [存档]
"""
import importlib.util
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

SAV = sys.argv[1] if len(sys.argv) > 1 else r'E:/YURI/SAVE0E29_修改.SAV'
PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


if not os.path.exists(SAV):
    print('（跳过：样本不存在 %s）' % SAV)
    raise SystemExit(0)

spec = importlib.util.spec_from_file_location('gui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

app = m.App()
app.geometry('1600x1000+10+10')
for _ in range(20):
    app.update()
app._set_doc(m.SaveDoc(SAV))
for _ in range(20):
    app.update()
app.nb.select(10)                      # 武器 · 弹头
for _ in range(10):
    app.update()


def mode_err():
    try:
        t = app.lbl_wpn_mode.cget('text') or ''
    except Exception:
        return ''
    return t if '切换' in t else ''


print('\n== 1. 刚进页面（默认「武器（按把）」）==')
n_w = len(app.tw.get_children())
ck(n_w > 0, '武器列表有 %d 把' % n_w)
ck(app._wpn_wrap.winfo_ismapped(), '武器列表容器是 mapped')
ck(not mode_err(), '没有「切换视图失败」提示（%r）' % mode_err())

print('\n== 2. 单位 ⇄ 按把 来回 3 轮 ==')
for i in range(3):
    app._wpn_mode.set('unit')
    app._wpn_switch()
    for _ in range(5):
        app.update()
    n_u = len(app.utw.get_children())
    ck(n_u > 0, '第 %d 轮切到「单位」：%d 个型号' % (i + 1, n_u))
    ck(not mode_err(), '第 %d 轮切到「单位」无红字' % (i + 1))
    ck(app._unit_wrap.winfo_ismapped(), '第 %d 轮单位列表 mapped' % (i + 1))

    app._wpn_mode.set('weapon')
    app._wpn_switch()
    for _ in range(5):
        app.update()
    n_w2 = len(app.tw.get_children())
    ck(n_w2 > 0, '第 %d 轮切回「按把」：%d 把（这就是玩家说"出不来"的那一步）'
       % (i + 1, n_w2))
    ck(not mode_err(), '第 %d 轮切回「按把」无红字（%r）' % (i + 1, mode_err()))
    ck(app._wpn_wrap.winfo_ismapped(), '第 %d 轮武器列表 mapped' % (i + 1))

print('\n== 3. 搜索框 / 只看≠原版 这两件控件也跟着回来 ==')
ck(app._wpn_qrow.winfo_ismapped(), '搜索行 mapped')
app._q_wpn.set('120mm')
app.refresh_weapons()
app.update()
n_f = len(app.tw.get_children())
ck(n_f > 0, '按 "120mm" 过滤后 %d 把' % n_f)
app._q_wpn.set('')
app.refresh_weapons()
app.update()
ck(len(app.tw.get_children()) == n_w, '清空搜索后回到 %d 把' % n_w)

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
raise SystemExit(1 if FAIL else 0)
