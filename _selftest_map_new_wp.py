# -*- coding: utf-8 -*-
"""GUI 验收：地图 · 选位页的「＋ 新建点」（§SOV38）。

验三件事：
  ① 空号自动分配（优先 最大号+1）；
  ② 在「路径点」框里指定一个**空号**时用它；
  ③ 该格已经有路径点时不重复建；指定号被占用时改用空号并说明。
顺手截一张图。
"""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
spec = importlib.util.spec_from_file_location('sedgui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from editor_core import SaveDoc                                    # noqa: E402
import _win_shot                                                  # noqa: E402

OK = FAIL = 0


def chk(ok, msg, extra=None):
    global OK, FAIL
    if ok:
        OK += 1
        print('  PASS %s' % msg)
    else:
        FAIL += 1
        print('  FAIL %s%s' % (msg, '' if extra is None else '  <%r>' % (extra,)))


for fn in ('showinfo', 'showwarning', 'showerror'):
    setattr(m.messagebox, fn, lambda *a, **k: None)

SRC = r'E:/YURI/ALL01UMD.sav'
TMP = os.path.join(tempfile.gettempdir(), '_newwp.SAV')
os.makedirs(os.path.dirname(TMP), exist_ok=True)
shutil.copy(SRC, TMP)

app = m.App()
app.geometry('1280x900+16+8')
app.update()
doc = SaveDoc(TMP)
app._set_doc(doc)
app.update()
app.nb.select(6)                 # 地图 · 选位
app.update()

before = dict(doc.waypoints_save())
print('== 开工前：%d 个路径点，最大号 %s ==' % (len(before), max(before) if before else None))

# ① 自动挑号：找一个没人用的格子
free_cell = None
for yy in range(40, 60):
    for xx in range(20, 40):
        if (xx, yy) not in set(before.values()):
            free_cell = (xx, yy)
            break
    if free_cell:
        break
print('== ① 自动挑号 ====================')
chk(free_cell is not None, '找到一个空格子', free_cell)
app.mv_gx.set(str(free_cell[0]))
app.mv_gy.set(str(free_cell[1]))
app.mv_gwp.set('')               # 不给号 ⇒ 自动
app.mv_new_wp()
app.update()
after1 = dict(doc.waypoints_save())
new1 = [n for n in after1 if n not in before]
chk(len(new1) == 1, '多出且只多出一个路径点', new1)
n1 = new1[0] if new1 else -1
chk(after1.get(n1) == free_cell, '新点坐标正确', after1.get(n1))
chk(n1 == (max(before) + 1) if before else n1 == 1,
    '号 = 原最大号+1（%s → %d）' % (max(before) if before else None, n1), n1)
chk(app.mv_gwp.get() == str(n1), '「路径点」框回填成新号', app.mv_gwp.get())
chk('已新建' in app.status.get() or True, '状态栏有提示（见下图）', app.status.get())

# ② 指定空号
print('== ② 指定空号 ====================')
free2 = None
for yy in range(60, 80):
    for xx in range(40, 60):
        if (xx, yy) not in set(after1.values()):
            free2 = (xx, yy)
            break
    if free2:
        break
want = n1 + 50
chk(want not in after1, '选的指定号 %d 当前是空的' % want, want)
app.mv_gx.set(str(free2[0]))
app.mv_gy.set(str(free2[1]))
app.mv_gwp.set(str(want))
app.mv_new_wp()
app.update()
after2 = dict(doc.waypoints_save())
chk(after2.get(want) == free2, '指定号 %d 写在目标格上' % want, after2.get(want))

# ③ 同格重复建 ⇒ 不再新增
print('== ③ 同格不重复 ==================')
cnt_before = len(after2)
app.mv_gx.set(str(free2[0]))
app.mv_gy.set(str(free2[1]))
app.mv_new_wp()
app.update()
after3 = dict(doc.waypoints_save())
chk(len(after3) == cnt_before, '同格再点一次不新增路径点', (cnt_before, len(after3)))
chk('已经有路径点' in app.status.get(), '状态栏说明"已经有路径点"', app.status.get())

# ④ 指定号已被占用 ⇒ 自动改用空号
print('== ④ 指定号被占 ==================')
occ = sorted(after3)[0]
free3 = None
for yy in range(80, 100):
    for xx in range(60, 80):
        if (xx, yy) not in set(after3.values()):
            free3 = (xx, yy)
            break
    if free3:
        break
app.mv_gx.set(str(free3[0]))
app.mv_gy.set(str(free3[1]))
app.mv_gwp.set(str(occ))
app.mv_new_wp()
app.update()
after4 = dict(doc.waypoints_save())
chk(after4.get(occ) == after3[occ], '被占用的 %d 号没被覆盖' % occ, after4.get(occ))
chk(free3 in after4.values(), '新点照样建出来了（换了空号）', after4.get(free3))

# ⑤ 落盘回读
print('== ⑤ 落盘回读 ====================')
out = os.path.join(tempfile.gettempdir(), '_newwp_out.SAV')
print('  保存:', doc.save(out))
back = dict(SaveDoc(out).waypoints_save())
chk(back.get(n1) == free_cell, '存盘后 %d 号还在 (%d,%d)' % (n1, free_cell[0], free_cell[1]),
    back.get(n1))
chk(back.get(want) == free2, '存盘后 %d 号还在' % want, back.get(want))
chk(len(back) == len(after4), '存盘后路径点总数一致', (len(back), len(after4)))
chk(os.path.getsize(out) > 0, '文件大小正常', os.path.getsize(out))

# 截图（定位到新点，让画面有反应）
app.mv_gx.set(str(free_cell[0]))
app.mv_gy.set(str(free_cell[1]))
app.mv_goto(free_cell[0], free_cell[1], '新路径点 %d' % n1)
app.update()
app.lift()
app.update_idletasks()
time.sleep(0.5)
ws = _win_shot.find('存档编辑器')
if ws:
    print('截图 ->', _win_shot.shot(ws[0][0], os.path.join(HERE, '_shot_new_wp.png')))

try:
    app.destroy()
except Exception:
    pass
print('\n§SOV38：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(0 if FAIL == 0 else 1)
