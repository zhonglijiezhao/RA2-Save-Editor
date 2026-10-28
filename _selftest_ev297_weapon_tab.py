# -*- coding: utf-8 -*-
"""§297 自检：GUI「武器 · 弹头」页（列表 / 编辑 / 克制表 / 与 rules 比对）。

背景（2026-10-28）：玩家确认「改 rulesmd.ini 不影响已存盘存档里的武器效果」，
且实测 `--setweapon` 把光棱塔伤害改 999 后**游戏里真的生效**。
于是把这条能力做成界面：左侧武器表（213 把，标出与 rules 不同的），
右侧改 伤害/射速/射程 + 11 项弹头克制表。

本自检同时守住一条容易被忽略的规矩：
**新页必须追加在末尾** —— 现有自检按固定索引取页（6=地图 7=赠送），插中间会全打歪。

跑法：python _selftest_ev297_weapon_tab.py
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

app = m.App()
app.geometry('1280x820')
for _ in range(6):
    app.update()

# ── ① 页数 / 索引稳定性 ────────────────────────────────────
tabs = app.nb.tabs()
ck(len(tabs) == 11, '共 11 页（实得 %d）' % len(tabs))
# 老索引不能被挤歪（6=地图 7=赠送 8=搜索 9=作战小队）
WANT = {0: '概览', 1: '所属方金钱', 2: '触发', 3: '事件', 4: '行为',
        5: '挂接', 6: '地图 · 选位', 7: '赠送建筑', 8: '明文搜索', 9: '作战小队'}
try:
    rail = app.nb
    texts = [getattr(p, '_nav_text', None) for p in rail._pages]
except Exception:
    texts = []
if all(texts):
    for i, want in WANT.items():
        ck(texts[i] == want, '第 %d 页仍是「%s」（实得「%s」）' % (i, want, texts[i]))
    ck(texts[10] == '武器 · 弹头', '第 10 页（末尾）是「武器 · 弹头」')
else:
    print('  （取不到页名，跳过索引断言）')

# ── ② 载入存档 + 懒加载 ───────────────────────────────────
app.load_doc(SRC)
t0 = time.time()
while app.doc is None and time.time() - t0 < 90:
    app.update()
    time.sleep(0.02)
for _ in range(800):
    app.update()
    if not getattr(app, '_st_steps', None):
        break
    time.sleep(0.01)
ck(app.doc is not None, '存档已载入')

ck(getattr(app, '_wpn_loaded', True) is False,
   '开档时**没有**立刻扫武器表（懒加载，省 230ms）')

app.nb.select(10)
for _ in range(10):
    app.update()
ck(getattr(app, '_wpn_loaded', False) is True, '切到武器页后自动填充')

rows = app.tw.get_children()
ck(len(rows) > 150, '武器列表 %d 行（>150）' % len(rows))
ck('把' in app.lbl_wpn_count.cget('text'), '底部计数标签有内容：%r'
   % app.lbl_wpn_count.cget('text'))

# ── ③ 选中一把武器 → 右侧填好 ───────────────────────────────
ck(app.tw.selection() != (), '默认已选中一把')
app.tw.selection_set('PrismShot')
app.tw.focus('PrismShot')
for _ in range(6):
    app.update()
ck('PrismShot' in app.lbl_wpn_name.cget('text'), '标题显示 PrismShot')
ck(app._wpn_var['damage'].get() == '120', '伤害框 = 120')
ck(app._wpn_var['rof'].get() == '45', '射速框 = 45')
ck(app._wpn_var['range'].get() == '8', '射程框 = 8')
ck('PrismWarhead' in app.lbl_wh_name.cget('text'), '弹头名显示 PrismWarhead')
vs = [v.get() for v in app._verse_var]
ck(vs[0] == '200' and vs[5] == '100' and vs[6] == '50',
   '克制表填对（none=200%%, heavy=100%%, wood=50%%）实得 %s' % vs[:7])
ck('ATESLA' in app.lbl_wpn_users.cget('text'), '「哪些型号用」列出 ATESLA')

# ── ④ 改数值：写进明文、列表刷新、标记未保存 ─────────────────
before = bytes(app.doc.plain)
app._wpn_var['damage'].set('777')
app._wpn_apply()
for _ in range(6):
    app.update()
ck(bytes(app.doc.plain) != before, '点「应用数值」后明文变了')
w = next(x for x in app.doc.weapon_objects() if x['name'] == 'PrismShot')
ck(w['damage'] == 777, '存档里 PrismShot 伤害 = 777（实得 %d）' % w['damage'])
ck(len(app.doc.plain) == len(before), '★ 明文长度不变（定长写入）')
vals = app.tw.item('PrismShot', 'values')
ck(str(vals[1]) == '777', '列表行同步刷新为 777（实得 %s）' % (vals[1],))
ck(app._is_modified(), '改完标题栏会出 * 未保存标记')
ck('777' in app.lbl_wpn_msg.cget('text') or '已改' in app.lbl_wpn_msg.cget('text'),
   '卡片内有「已改」提示：%r' % app.lbl_wpn_msg.cget('text'))

# ── ⑤ 改克制表 ────────────────────────────────────────────
app._verse_var[5].set('400')          # heavy
app._wpn_verse_apply()
for _ in range(4):
    app.update()
wh = next(x for x in app.doc.warhead_objects() if x['name'] == 'PrismWarhead')
ck(abs(wh['verses'][5] - 4.0) < 1e-9,
   'PrismWarhead 对 heavy = 400%%（实得 %g%%）' % (wh['verses'][5] * 100))
ck(len(app.doc.plain) == len(before), '改克制表后明文长度仍不变')

# ── ⑥ 非法输入要拦（弹框会被 monkeypatch 掉）───────────────
_errs = []
_orig_err = m.messagebox.showerror
m.messagebox.showerror = lambda *a, **k: _errs.append(a)
try:
    app._wpn_var['damage'].set('999999')
    app._wpn_apply()
    ck(len(_errs) == 1, '伤害 999999 越界被拦下（弹了 1 次错）')
    ck(next(x for x in app.doc.weapon_objects()
            if x['name'] == 'PrismShot')['damage'] == 777, '越界时没有写入')
finally:
    m.messagebox.showerror = _orig_err

# ── ⑦ 按 rules 还原（只填框，不写）──────────────────────────
app._wpn_revert()
for _ in range(4):
    app.update()
ck(app._wpn_var['damage'].get() in ('120', '120.0'),
   '「按 rules 还原」把伤害框填回 120（实得 %s）' % app._wpn_var['damage'].get())
ck(next(x for x in app.doc.weapon_objects()
        if x['name'] == 'PrismShot')['damage'] == 777,
   '还原只填框、没写盘（存档里仍是 777）')

# ── ⑧ 「只看 ≠rules」过滤 ─────────────────────────────────
n_all = len(app.tw.get_children())
app._wpn_only_diff.set(True)
app.refresh_weapons()
for _ in range(4):
    app.update()
n_diff = len(app.tw.get_children())
ck(0 < n_diff < n_all, '只看 ≠rules：%d → %d 行' % (n_all, n_diff))
app._wpn_only_diff.set(False)
app.refresh_weapons()
for _ in range(4):
    app.update()
ck(len(app.tw.get_children()) == n_all, '取消过滤后恢复 %d 行' % n_all)

# ── ⑨ 搜索 ────────────────────────────────────────────────
app._q_wpn.set('PRISM')
app.refresh_weapons()
for _ in range(4):
    app.update()
names = [app.tw.item(i, 'values')[0].replace('★ ', '') for i in app.tw.get_children()]
ck(names and all('PRISM' in n.upper() for n in names),
   '搜索 PRISM 只剩 %d 行且全部匹配' % len(names))
app._q_wpn.set('')
app.refresh_weapons()
for _ in range(4):
    app.update()

# ── ⑩ 换档后要重扫（不能沿用上一份的缓存）──────────────────
# (a) 不在武器页时换档 ⇒ 不该白扫（230ms 别花在开档路径上）
app.nb.select(0)
for _ in range(4):
    app.update()
app._set_doc(m.SaveDoc(SRC))
ck(getattr(app, '_wpn_loaded', False) is False,
   '不在武器页时换档 ⇒ 不扫类型表（懒加载）')
app.nb.select(10)
for _ in range(8):
    app.update()
ck(getattr(app, '_wpn_loaded', False) is True, '切回武器页会填充')
ck(next(x for x in app.doc.weapon_objects()
        if x['name'] == 'PrismShot')['damage'] == 120,
   '换的是干净档 ⇒ 伤害回到 120')

# (b) 正停在武器页时换档 ⇒ 必须立刻重填（select 同一页不触发事件）
app.tw.selection_set('PrismShot')
for _ in range(4):
    app.update()
ck(getattr(app, '_wpn_cur', None) == 'PrismShot', '换档前选中 PrismShot')
app._set_doc(m.SaveDoc(SRC))
for _ in range(6):
    app.update()
ck(getattr(app, '_wpn_loaded', False) is True, '停在武器页换档 ⇒ 立刻重填')
ck(app.tw.get_children() != (), '重填后列表非空（%d 行）'
   % len(app.tw.get_children()))

app.destroy()

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败项：')
    for f in FAIL:
        print('  - ' + f)
sys.exit(1 if FAIL else 0)
