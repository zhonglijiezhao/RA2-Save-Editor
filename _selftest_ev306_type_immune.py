# -*- coding: utf-8 -*-
"""§306 自检：型号级「免疫心灵控制」（TypeClass `+0xD49`，1 字节）。

★2026-11-13（玩家游戏内验收通过）：`rulesmd.ini` 的 `ImmuneToPsionics` 对**旧档**无效
  （读档取类型对象里那一份），改存档 `+0xD49` 立刻生效。
  这里钉住：读、写、存盘往返、以及 UI 勾选框的联动。

跑法：D:/py/python.exe _selftest_ev306_type_immune.py [存档]
"""
import importlib.util
import io
import os
import sys
import tempfile

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
from editor_core import SaveDoc                                   # noqa: E402

print('\n== 1. 核心读（已知三种：HARV 免疫 / MTNK 不免疫 / AMBU 靠地图 ini 免疫）==')
doc = SaveDoc(SAV)
ck(doc.type_immune_psionics('HARV') is True, 'HARV（原版 rules 就写着 yes）读到 True')
ck(doc.type_immune_psionics('MTNK') is False, 'MTNK 读到 False')
ck(doc.type_immune_psionics('AMBU') is True,
   'AMBU 读到 True（rules 没写=默认 no，但**地图 ini** 写了 ⇒ 优先级最高，玩家确认）')
ck(SaveDoc.TYPE_IMMUNE_PSI_OFF == 0xD49, '偏移常量 = 0xD49')

print('\n== 2. 偏移自证：rules 显式写 yes 的型号里，这一字节应该基本都是 1 ==')
d2 = SaveDoc(SAV)
d2.set_game_dir(r'E:/YURI')
rules = d2._rules_all() or {}
ut = d2.unit_type_objects()
yes = [t for t in ut if str((rules.get(t) or {}).get('ImmuneToPsionics', '')).lower() == 'yes']
hit = [t for t in yes if d2.plain[ut[t]['off'] + 0xD49] == 1]
ck(len(yes) >= 30, 'rules 显式 yes 的型号 %d 个（样本够用）' % len(yes))
ck(len(hit) >= len(yes) - 3,
   '%d/%d 个显式 yes 的型号在存档 `+0xD49` 上是 1（APOC 刚加的 rules 不算，故允许少量不符）'
   % (len(hit), len(yes)))

print('\n== 3. 写 / 读回 / 存盘往返 ==')
before = d2.type_immune_psionics('MTNK')
info = d2.set_type_immune_psionics('MTNK', True)
ck(info['old'] == (1 if before else 0) and info['new'] == 1, '写入 info：old=%d new=%d'
   % (info['old'], info['new']))
ck(d2.type_immune_psionics('MTNK') is True, '写后读回 True')
ck(len(d2.plain) == len(d2.plain_orig), '明文长度不变（原地 1 字节）')
tmp = os.path.join(tempfile.gettempdir(), '_ev306_immune.SAV')
try:
    d2.save(tmp, backup=False)
    d3 = SaveDoc(tmp)
    ck(d3.type_immune_psionics('MTNK') is True, '存盘后重新打开，MTNK 仍是 True')
    ck(d3.type_immune_psionics('HARV') is True, '同档 HARV 没被带坏')
    ck(d3.type_immune_psionics('E1') is False, '同档 E1 没被带坏')
finally:
    try:
        os.remove(tmp)
    except Exception:
        pass
# 改回去，避免影响后续用例
d2.set_type_immune_psionics('MTNK', before)

print('\n== 4. UI：武器·弹头 ▸ 单位视图 的勾选框 ==')
app = m.App()
app.geometry('1600x1000+10+10')
for _ in range(20):
    app.update()
app._set_doc(SaveDoc(SAV))
for _ in range(20):
    app.update()
app.nb.select(10)
app.update()
app._wpn_mode.set('unit')
app._wpn_switch()
for _ in range(10):
    app.update()
rows = [r for r in app.utw.get_children()
        if str(app.utw.item(r, 'values')[0]).upper() == 'APOC']
ck(bool(rows), '单位视图里找得到 APOC 行')
if rows:
    app.utw.selection_set(rows[0])
    app.utw.focus(rows[0])
    app.update()
    app._unit_pick()
    app.update()
    cur = app.doc.type_immune_psionics('APOC')
    ck(app._unit_immune.get() == bool(cur),
       '勾选框状态跟着型号走（APOC 当前=%s，勾选框=%s）' % (cur, app._unit_immune.get()))
    # 模拟点勾：askyesno 直接回「是」
    orig = m.messagebox.askyesno
    m.messagebox.askyesno = lambda *a, **k: True
    try:
        app._unit_immune.set(not bool(cur))
        app._unit_immune_toggle()
        app.update()
    finally:
        m.messagebox.askyesno = orig
    ck(app.doc.type_immune_psionics('APOC') == (not bool(cur)),
       '点勾后存档里真的变了（现在 = %s）' % app.doc.type_immune_psionics('APOC'))
    ck('已写入' in (app.lbl_unit_msg.cget('text') or ''), '面板给了「已写入」提示')
    # 再点回去
    m.messagebox.askyesno = lambda *a, **k: True
    try:
        app._unit_immune.set(cur)
        app._unit_immune_toggle()
        app.update()
    finally:
        m.messagebox.askyesno = orig
    ck(app.doc.type_immune_psionics('APOC') == bool(cur), '再点一次能改回原值')

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
raise SystemExit(1 if FAIL else 0)
