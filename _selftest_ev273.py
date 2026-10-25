# -*- coding: utf-8 -*-
"""§273 自检：AI 页 / 触发表 提速（路径点表与 CellTag 表缓存）。

跑法：D:/py/python.exe _selftest_ev273.py
测试档：SAVE4823.SAV（3.4MB / 明文 26.2MB / 288 小队 / 232 特遣 / 171 脚本 / 495 触发记录 / 92 CellTag）
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

from editor_core import SaveDoc                                       # noqa: E402
from _paths import find_sav                                           # noqa: E402

OK = 0
FAIL = 0


def chk(cond, msg, detail=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [OK] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s   %r' % (msg, detail))


spec = importlib.util.spec_from_file_location('sedgui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SAV = find_sav('SAVE4823.SAV')
d0 = SaveDoc(SAV)


def t(fn):
    t0 = time.time()
    r = fn()
    return r, time.time() - t0


print('测试档 %s  明文 %.1f MB' % (SAV, len(d0.plain) / 1048576.0))

# ---------------- 1) waypoint_base 缓存 ----------------
print('\n[1] waypoint_base 缓存')
d = SaveDoc(SAV)
(r1, dt1) = t(d.waypoint_base)
(r2, dt2) = t(d.waypoint_base)
r3, dt3 = t(lambda: [d.waypoint_base() for _ in range(50)])
print('    首次 %.4fs / 第二次 %.5fs / 再 50 次 %.4fs  base=%s' % (dt1, dt2, dt3, r1))
chk(r1 is not None, '路径点表定位成功（base=%s）' % r1, r1)
chk(r1 == r2, '两次结果相同', (r1, r2))
chk(dt2 < dt1 / 5 or dt2 < 0.002, '★第二次命中缓存（%.5fs，首次 %.4fs）' % (dt2, dt1), dt2)
chk(dt3 < 0.05, '再调 50 次总耗时 < 0.05s（实际 %.4fs）' % dt3, dt3)

# 增删字节 ⇒ 长度变了 ⇒ 自动作废（模拟：把明文末尾加 4 字节再删）
_n0 = len(d.plain)
d.plain += b'\x00\x00\x00\x00'
(r_after, _) = t(d.waypoint_base)
chk(_n0 != len(d.plain) and r_after == r1, '明文长度变了会自动重算且结果不变（%s）' % r_after, r_after)
del d.plain[_n0:]
chk(d.waypoint_base() == r1, '删回原长度后结果仍一致', d.waypoint_base())

# 原地改 4 字节（set_waypoint）不能让基址失效 —— 长度不变是对的
wps = d.waypoints_save()
_n = sorted(wps)[0] if wps else None
if _n is not None:
    old = wps[_n]
    d.set_waypoint(_n, old[0], old[1])
    chk(d.waypoint_base() == r1 and d.waypoint_cell(_n) == old,
        '原地改路径点后基址/坐标不变（号 %s = %s）' % (_n, old), (d.waypoint_base(), d.waypoint_cell(_n)))

# ---------------- 2) _tag_objs_by_key 缓存 ----------------
print('\n[2] CellTag 对象表缓存')
d2 = SaveDoc(SAV)
(m1, dt1) = t(d2._tag_objs_by_key)
(m2, dt2) = t(d2._tag_objs_by_key)
print('    首次 %.4fs / 第二次 %.5fs  对象 %d 个' % (dt1, dt2, len(m1)))
chk(isinstance(m1, dict) and len(m1) > 0, '扫到 %d 个 CellTag 对象' % len(m1), len(m1))
chk(m1 == m2, '两次结果逐条相同', (len(m1), len(m2)))
chk(dt2 < dt1 / 5 or dt2 < 0.002, '★第二次命中缓存（%.5fs，首次 %.4fs）' % (dt2, dt1), dt2)

# ---------------- 3) 文字没变：_wp_text 与逐个查 doc 完全一致 ----------------
print('\n[3] _wp_text 结果一致（缓存不能改变显示）')
app = m.App()
app.withdraw()
app._set_doc(SaveDoc(SAV))
same = diff = 0
bad = None
for team in (app._ai or {}).get('teams', []):
    n = team.get('waypoint')
    if n is None or n < 0:
        continue
    a = app._wp_text(n)
    c = app.doc.waypoint_cell(n)
    b = ('%d (%d,%d)' % (n, c[0], c[1])) if c else ('%d (未定义)' % n)
    if a == b:
        same += 1
    else:
        diff += 1
        bad = (n, a, b)
print('    逐支小队比对：相同 %d / 不同 %d' % (same, diff))
chk(diff == 0 and same > 50, '★%d 支小队的路径点文字与逐个查表完全一致' % same, bad)

# ---------------- 4) 速度 ----------------
print('\n[4] 速度（优化前：AI 页 2.59s、触发表 5.09s、开档 8.2s）')
(t_ai,) = t(app.refresh_ai)[1:]
(t_rec,) = t(app.refresh_records)[1:]
app.refresh_ai()
app.refresh_records()
(t_ai2,) = t(app.refresh_ai)[1:]
(t_rec2,) = t(app.refresh_records)[1:]
print('    refresh_ai      %.3fs（再跑 %.3fs）' % (t_ai, t_ai2))
print('    refresh_records %.3fs（再跑 %.3fs）' % (t_rec, t_rec2))
chk(t_ai < 0.6, '★AI 页 < 0.6 秒（%.3fs，原 2.59s）' % t_ai, t_ai)
chk(t_rec < 1.5, '★触发表 < 1.5 秒（%.3fs，原 5.09s）' % t_rec, t_rec)
chk(t_ai2 < 0.3 and t_rec2 < 0.5, '二次刷新更快（%.3fs / %.3fs）' % (t_ai2, t_rec2), (t_ai2, t_rec2))

_doc_new = SaveDoc(SAV)
(t_setdoc,) = t(lambda: app._set_doc(_doc_new))[1:]
print('    _set_doc（同步版，含冷缓存）%.3fs' % t_setdoc)
chk(t_setdoc < 5.0, '★开档 _set_doc < 5 秒（%.3fs，上一版 8.2s / 最初 17.4s）' % t_setdoc, t_setdoc)

# ---------------- 5) AI 页默认不显示引擎生成的 ----------------
print('\n[5] AI 页默认只显示「地图定义」（作者写的）')
ai = app._ai or {}
n_map = sum(1 for x in ai.get('teams', []) if x.get('is_map'))
n_all = len(ai.get('teams', []))
rows = len(app.tat.get_children())
print('    小队 全部 %d / 地图定义 %d / 表格实际 %d' % (n_all, n_map, rows))
chk(rows == n_map + len(getattr(app, '_ai_ours', set()) or set()),
    '★表格行数 = 地图定义数（默认隐藏引擎生成的 %d 支）' % (n_all - n_map), (rows, n_map))
chk('隐藏引擎生成的' in app.lbl_ai_count.cget('text'),
    '计数条写明「已隐藏引擎生成的 N 个」', app.lbl_ai_count.cget('text'))
# 勾上「显示全部」应能看全
app._ai_all.set(True)
app._fill_ai_all()
chk(len(app.tat.get_children()) == n_all, '勾选后能显示全部 %d 支（不影响数据，只是视图）' % n_all,
    len(app.tat.get_children()))
app._ai_all.set(False)
app._fill_ai_all()

# ---------------- 6) 单位/触发相关功能没被缓存带坏 ----------------
print('\n[6] 缓存没带坏其它功能')
recs = app.doc.unit_records()
chk(len(recs) > 1000, '单位记录仍能读出 %d 台' % len(recs), len(recs))
objs = None
_sid = None
for _r in app.doc.records:
    _s = getattr(_r, 'sid', None)
    if _s and app.doc.trigger_rec(_s) is not None:
        _sid = _s
        break
chk(_sid is not None, '找到一条能定位的触发记录（%s）' % _sid, _sid)
objs = app.doc.trigger_objects(_sid)
chk(objs.get('inst') is not None and objs.get('tag_obj') is not None,
    'trigger_objects 仍能拿齐实例+标签对象（tag_obj=%s）' % objs.get('tag_obj'),
    {k: objs.get(k) for k in ('inst', 'tag_obj', 'ev')})
chk(len(app.tat.get_children()) > 0 and len(app.tst.get_children()) > 0,
    '三张表都有行（%d / %d / %d）' % (len(app.tat.get_children()), len(app.tft.get_children()),
                                     len(app.tst.get_children())))

print('-' * 72)
print('§273 AI页/触发表提速：PASS %d / FAIL %d' % (OK, FAIL))
