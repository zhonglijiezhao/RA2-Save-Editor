# -*- coding: utf-8 -*-
"""自检：俯视展开后「点单位圆点 ⇒ 格上没有单位、不能删除」（玩家 2026-11-07 14:44）

根因
----
俯视 = 等距逆变换 i=(x−y−s0)//2、j=(x+y−d0)//2，是 **2:1 合并**：
一个显示格压着**两个**存档 iso 半格。`ortho_map()` 的 `cells` 反查表只留了
其中一格（地形里先到的那个），于是点在另一半格上的单位，点击会被算成隔壁半格。

本档实测：显示格 (183,20) 的候选 = iso (204,37) 与 iso (205,37)；
`cells[(183,20)] = (204,37)`，而 HTK 单位在 **(205,37)**（游戏里光标也是 205,37）
⇒ 旧版点那颗方块选中 (204,37)，「删除该格单位」报「格 (204,37) 上没有单位」。

修法
----
`_mv_pick_cell()` 点选时把两个候选都算出来，优先落在**真有东西**（单位/建筑/
路径点/单元标记）的半格；两个半格都有东西时连点同一显示格轮换。
`_mv_sel_cell()` 再加一层兜底：手输坐标落在空半格时，自动改用同格有单位的那半格。

样本缺失（游戏目录换了/档被覆盖）⇒ 跳过，不判 FAIL。
"""
import importlib.util
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

SAV = next((p for p in (r'E:\YURI\SAVE0029.SAV',
                        r'E:\YURI\SAVE0029_修改_2.SAV',
                        r'E:\YURI\ALL01UMD.sav') if os.path.exists(p)), None)
MAP = next((p for p in (r'E:\YURI\all01umd.map', r'E:\YURI\ALL01UMD.MAP')
            if os.path.exists(p)), None)
OK = FAIL = 0


def chk(c, msg, extra=''):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] %s %s' % (msg, extra))


if not (SAV and MAP):
    print('跳过：样本缺失（%s / %s）' % (SAV, MAP))
    sys.exit(0)

spec = importlib.util.spec_from_file_location(
    'sedgui_half', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from editor_core import SaveDoc                                     # noqa: E402
import ui.msg as _uimsg                                              # noqa: E402

said = {'info': [], 'warn': [], 'err': []}
setattr(_uimsg.messagebox, 'showinfo',
        lambda *a, **k: said['info'].append((a, k)))
setattr(_uimsg.messagebox, 'showwarning',
        lambda *a, **k: said['warn'].append((a, k)))
setattr(m.messagebox, 'showinfo', lambda *a, **k: None)

app = m.App()
app.geometry('1200x820+16+16')
app.deiconify()
app._set_doc(SaveDoc(SAV))
app.nb.select(6)
app.update_idletasks()
app.update()
app.mv_load(MAP)
app.mv_state['scale'] = 8
app.mv_rot_kind.set(4)
app._mv_sync_rot_cb()
app._mv_on_rot()
app.update()
st = app.mv_state
print('样本 %s ｜ 地图 %s ｜ %s' % (os.path.basename(SAV), os.path.basename(MAP),
                               app.MV_ROT_NAMES[app._mv_rot_kind_now()]))

cells = app._mv_ortho().get('cells') or {}
uc = {}
for x, y, _t, _h in app._mv_units():
    uc[(x, y)] = uc.get((x, y), 0) + 1
occ = st.get('occ') or {}
wps = set()
_mp, _only = app._mv_wp_points()
for c in list(_mp.values()) + list(_only.values()):
    wps.add((int(c[0]), int(c[1])))


def has_content(c):
    return (uc.get(c, 0) > 0 or c in occ or c in wps)


# 找一台「落在非反查表半格、且另一半没东西」的单位 —— 这是玩家报的那一类
unit = None
for x, y, t, h in app._mv_units():
    i, j = app._mv_cell(x, y)
    canon = tuple(cells.get((i, j)) or ())
    if canon and canon != (x, y) and not has_content(canon):
        unit = (x, y, t, h, i, j, canon)
        break
if unit is None:
    print('跳过：本档没有可用的「半格单位」样本')
    app.destroy()
    sys.exit(0)
ux, uy, ut, uh, i, j, canon = unit
print('样本单位：iso(%d,%d) %s %s → 显示格 (%d,%d)；反查表给 %s'
      % (ux, uy, ut, uh, i, j, canon))

print('\n== A. 候选半格 ==')
cands = app._mv_ortho_candidates(i, j)
chk(len(cands) == 2, '一个显示格压着两个 iso 半格', cands)
chk((ux, uy) in cands and canon in cands, '两个候选正是单位格与反查表格', cands)
chk(cands[0] == canon, '反查表格是默认候选（都空时按旧行为）', cands)
chk(app._mv_cell(ux, uy) == (i, j) and app._mv_cell(*canon) == (i, j),
    '两半格正算回同一显示格')

print('\n== B. 点选挑有东西的半格 ==')
chk(app._mv_pick_cell(i, j) == (ux, uy),
    '点 (%d,%d) ⇒ 选到有单位的 (%d,%d)（修好前是 %s）' % (i, j, ux, uy, canon),
    app._mv_pick_cell(i, j))
s = app._mv_scale_now()
sx = s * app._mv_xscale()
ev = type('E', (), {'x': int(i * sx + sx / 2), 'y': int(j * s + s / 2)})
app.mv_click(ev)
app.update()
chk(tuple(st.get('sel') or ()) == (ux, uy), 'mv_click ⇒ sel 是单位格', st.get('sel'))
chk((app.mv_gx.get(), app.mv_gy.get()) == (str(ux), str(uy)),
    '「定位」框回填单位坐标', (app.mv_gx.get(), app.mv_gy.get()))
chk(ut in app._mv_info_text(ux, uy), '信息栏列出该单位 %s' % ut)

print('\n== C. 删除该格单位：能取到单位记录 ==')
st['sel'] = (ux, uy)
said['info'].clear()
got = app._mv_sel_cell(need_units=True)
chk(got is not None, '不再弹「没有单位」', said['info'])
chk(got and tuple(got[0]) == (ux, uy) and len(got[1]) >= 1,
    '取到该格单位记录', None if not got else (got[0], len(got[1])))
chk(got and any(r['type'] == ut for r in got[1]), '记录里有 %s' % ut)

print('\n== D. 手输坐标落在空半格 ⇒ 兜底自动换过去 ==')
st['sel'] = canon
st['last_disp'] = None
said['info'].clear()
got2 = app._mv_sel_cell(need_units=True)
chk(got2 is not None and tuple(got2[0]) == (ux, uy),
    '空半格 (%d,%d) ⇒ 自动改用 (%d,%d)' % (canon[0], canon[1], ux, uy),
    None if not got2 else got2[0])
chk('另一半' in app.status.get(), '状态栏说明换的是「同一俯视格另一半」', app.status.get())
chk(not said['info'], '没有弹「没有单位」提示', said['info'])

print('\n== E. 空格仍按反查表（不瞎跳）==')
empty = None
for (ii, jj), cc in cells.items():
    cc = tuple(cc)
    sib = [tuple(q) for q in app._mv_ortho_candidates(ii, jj) if tuple(q) != cc]
    if sib and not has_content(cc) and not any(has_content(q) for q in sib):
        empty = (ii, jj, cc)
        break
if empty:
    ii, jj, cc = empty
    st['last_disp'] = None
    chk(app._mv_pick_cell(ii, jj) == cc, '两半格都空 ⇒ 仍选反查表格 %s' % (cc,),
        app._mv_pick_cell(ii, jj))
else:
    print('  （没找到两半格全空的显示格，跳过）')

print('\n== F. 两个半格都有单位 ⇒ 连点轮换 ==')
real_udata = st.get('udata')
st['udata'] = list(real_udata or []) + [(canon[0], canon[1], 'FAKE', '')]
st['sel'] = None
st['last_disp'] = None
p1 = app._mv_pick_cell(i, j)
st['sel'] = p1
st['last_disp'] = (i, j)
p2 = app._mv_pick_cell(i, j)
chk({p1, p2} == {(ux, uy), canon}, '连点两下分别落到两个半格', (p1, p2))
chk(p1 != p2, '第二下切到另一半格', (p1, p2))
st['udata'] = real_udata

print('\n== G. 玩家截图里那一格（游戏光标 205,37）==')
if uc.get((205, 37)) and tuple(cells.get((183, 20)) or ()) == (204, 37):
    st['sel'] = None
    st['last_disp'] = None
    chk(app._mv_pick_cell(183, 20) == (205, 37),
        '显示格 (183,20) ⇒ 选中 (205,37)（旧版给 (204,37) ⇒ 报无单位）',
        app._mv_pick_cell(183, 20))
    st['sel'] = (204, 37)
    said['info'].clear()
    g = app._mv_sel_cell(need_units=True)
    chk(g is not None and tuple(g[0]) == (205, 37),
        '手输 (204,37) 删除 ⇒ 自动改删 (205,37)', None if not g else g[0])
else:
    print('  （本档不含玩家那一格，跳过）')

print('\n== H. 其它显示方式不受影响（1:1）==')
app.mv_rot_kind.set(0)
app._mv_sync_rot_cb()
app._mv_on_rot()
app.update()
chk(app._mv_pick_cell(150, 190) == (150, 190), '原始菱形仍是显示格 = 存档格',
    app._mv_pick_cell(150, 190))

app.destroy()
print('\n合计 PASS=%d FAIL=%d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
