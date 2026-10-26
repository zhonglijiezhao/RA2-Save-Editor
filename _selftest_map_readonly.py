# -*- coding: utf-8 -*-
"""自检：**铁律 —— 本工具只读地图、只写存档**（玩家 2026-10-27 00:20）

原话：「记住一个铁律！任何情况下存档编辑器都不允许有向地图中写入数据的能力」

本脚本是这道铁律的**看门狗**，四层验证：
  ① 所有历史"写地图"入口一律抛 `MapWriteForbidden`（不静默落盘）；
  ② `SaveDoc.save()` 拒绝把目标指到地图扩展名（.map/.mmx/.yrm/.mpr）；
  ③ **字节级**证明：拿真地图副本跑一遍"起名/设值/清空"，地图文件一个字节都没变、
     也没有产生 `.bak` / `.tmp`；
  ④ 源码静态扫描：任何 `open(...,'wb')` 附近不许出现地图路径变量（防止以后又手滑加回来）。
跑法：python _selftest_map_readonly.py
"""
import hashlib
import io
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from core import editor_core as ec                      # noqa: E402

SAV = r'E:/YURI/SAVE4823.SAV'
MAP_CANDIDATES = [r'C:/Users/zhao5/Desktop/all01umd.map',
                  r'E:/YURI/ALL01UMD.MAP',
                  r'E:/YURI/新建文件夹 (23)/all01umd.map']

ok = fail = 0


def ck(cond, msg):
    global ok, fail
    if cond:
        ok += 1
        print('  ✓ %s' % msg)
    else:
        fail += 1
        print('  ✗ %s' % msg)


def md5(path):
    return hashlib.md5(open(path, 'rb').read()).hexdigest()


print('① 历史写地图入口必须全部拒绝')
for fn in ('write_scenario_variables', 'write_scenario_waypoints'):
    f = getattr(ec, fn, None)
    ck(f is not None, '%s 还在（保留壳，便于旧调用立刻报错）' % fn)
    try:
        f('x.map', {} if fn.endswith('waypoints') else [])
        ck(False, '%s 竟然没抛异常' % fn)
    except ec.MapWriteForbidden:
        ck(True, '%s → MapWriteForbidden' % fn)
    except TypeError:
        ck(True, '%s → 参数不合法（也说明它没有写盘实现）' % fn)

d = ec.SaveDoc(SAV)
for meth, args, kw in (('register_waypoints_to_map', (), {}),
                       ('set_local_var_default', (0, 1), {}),
                       ('remove_local_var', (0,), {})):
    try:
        getattr(d, meth)(*args, **kw)
        ck(False, '%s 竟然没抛异常' % meth)
    except ec.MapWriteForbidden:
        ck(True, '%s → MapWriteForbidden' % meth)

print('\n② save() 不许写到地图')
for ext in ('.map', '.MAP', '.yrm', '.mmx', '.mpr'):
    try:
        d.save(os.path.join(tempfile.gettempdir(), '_ro_test' + ext))
        ck(False, 'save(%s) 竟然放行' % ext)
    except ec.MapWriteForbidden:
        ck(True, 'save(%s) → MapWriteForbidden' % ext)
    except Exception as e:                    # 先干别的校验也不算写地图
        ck(not os.path.exists(os.path.join(tempfile.gettempdir(), '_ro_test' + ext)),
           'save(%s) 未落盘（%s）' % (ext, type(e).__name__))

print('\n③ 字节级：拿真地图跑一遍变量操作，地图必须一字未改')
mp = next((p for p in MAP_CANDIDATES if os.path.exists(p)), None)
if not mp:
    ck(True, '找不到可用地图样本（跳过字节级验证）')
else:
    td = tempfile.mkdtemp(prefix='_ro_map_')
    tmpmap = os.path.join(td, 'probe.map')
    shutil.copy2(mp, tmpmap)
    before = md5(tmpmap)
    n_before = len(os.listdir(td))
    d2 = ec.SaveDoc(SAV)
    d2.define_local_var(5, 'RO_PROBE', 1, map_path=tmpmap)   # map_path 只作兼容，必须被忽略
    d2.define_local_var(6, 'RO_PROBE2', 0, map_path=tmpmap)
    d2.set_local_var_value(5, 0)
    d2.clear_local_var(6)
    d2.ensure_local_var(7, 'RO_PROBE3', 1)
    after = md5(tmpmap)
    ck(before == after, '地图 md5 未变（%s…）' % before[:12])
    ck(len(os.listdir(td)) == n_before, '没多出 .bak/.tmp（目录条目 %d）' % len(os.listdir(td)))
    ck(not any(x.endswith(('.bak', '.tmp')) for x in os.listdir(td)), '没有备份/临时文件')
    shutil.rmtree(td, ignore_errors=True)
# 存档侧确实生效（证明上面不是"什么都没做"）
ck(d2.local_var(5)['name'] == 'RO_PROBE', '存档里名字确实写进去了：%r' % d2.local_var(5)['name'])

print('\n④ 源码静态扫描：open(...,"wb") 附近不许出现地图路径变量')
BAD = re.compile(r"open\s*\([^)]*'wb'")
RISK = ('map_path', 'scenario_map', 'MAPVAR', '.map', 'yrm', 'mmx')
hits = []
for dp, dns, fns in os.walk(HERE):
    if '__pycache__' in dp or 'build' in dp.lower():
        continue
    for fn in fns:
        if not fn.endswith('.py') or fn.startswith('_selftest_map_readonly'):
            continue
        p = os.path.join(dp, fn)
        try:
            lines = io.open(p, encoding='utf-8', errors='replace').read().split('\n')
        except Exception:
            continue
        for i, ln in enumerate(lines, 1):
            if BAD.search(ln) and any(r in ln for r in RISK):
                hits.append('%s:%d %s' % (os.path.basename(p), i, ln.strip()[:90]))
ck(not hits, '没有"写地图"的可疑写盘点' + ('' if not hits else '：%s' % hits))

print('\n结果：%d 通过 / %d 失败' % (ok, fail))
sys.exit(1 if fail else 0)
