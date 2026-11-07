# -*- coding: utf-8 -*-
"""自检：`cell_markers()` 改一次扫描后 **结果必须与旧算法完全一致**（2026-11-07）

旧算法（4.4 秒 / 16.6 万次 bytearray.find）：对每条 CellTag 在 43MB 明文里全文搜自键。
新算法（一次扫 CellClass）：走 `_cell_by_key()` 已有的格对象索引，读每个对象 `+80`。

本脚本把旧算法原样复刻成 `cell_markers_ref()`，逐条比对两组结果的**全部字段**。
"""
import io
import os
import struct
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc, CELL_TAGKEY_OFF, TRIG_CELL_SIZE   # noqa: E402

OK = FAIL = 0


def chk(c, msg):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


def cell_markers_ref(d):
    """**旧实现原样复刻**（2026-11-07 之前）：每条 CellTag 全文搜自键。"""
    d._invalidate_if_shifted()
    pl = d.plain
    n = len(pl)
    try:
        meta = {b.get('node'): b for b in (d.attach_overview() or [])}
    except Exception:
        meta = {}
    out = []
    for nd in d.celltag_nodes():
        off, key, cnt = nd['node'], nd['x'], nd['count']
        if not key:
            continue
        m = meta.get(off) or {}
        tag_key = struct.unpack_from('<I', pl, off + CELL_TAGKEY_OFF)[0]
        pat = struct.pack('<I', key)
        q = 0
        while True:
            i = pl.find(pat, q)
            if i < 0:
                break
            q = i + 1
            if off <= i < off + TRIG_CELL_SIZE:
                continue
            if i < d.CELL_TAGLINK_OFF or i + 12 > n:
                continue
            if pl[i + 8:i + 12] != d.CELL_MARK_TAIL:
                continue
            obj = i - d.CELL_TAGLINK_OFF
            if pl[obj:obj + 16] != d.CELL_GUID:
                continue
            x = int.from_bytes(pl[i + d.CELL_MARK_DX:i + d.CELL_MARK_DX + 2], 'little')
            y = int.from_bytes(pl[i + d.CELL_MARK_DX + 2:i + d.CELL_MARK_DX + 4], 'little')
            if not (0 <= x < 2048 and 0 <= y < 2048):
                continue
            out.append({'x': int(x), 'y': int(y),
                        'cell_off': off, 'cell_key': int(key),
                        'tag_key': int(tag_key), 'count': int(cnt),
                        'tagid': m.get('tagid'),
                        'trigger': m.get('trigger_id'),
                        'trigger_name': m.get('trigger_name'),
                        'obj_off': obj, 'link_off': i})
    out.sort(key=lambda r: (r['y'], r['x'], r['cell_key']))
    return out


SAVS = [p for p in (r'E:\YURI\ALL01UMD.sav', r'E:\YURI\改.SAV') if os.path.exists(p)]
if not SAVS:
    print('跳过：找不到样本存档')
    sys.exit(0)

for SAV in SAVS:
    print('\n== %s ==' % os.path.basename(SAV))
    d = SaveDoc(SAV)
    t0 = time.perf_counter()
    ref = cell_markers_ref(d)
    t_old = time.perf_counter() - t0
    t0 = time.perf_counter()
    new = d.cell_markers()
    t_new = time.perf_counter() - t0
    print('  旧 %6.2fs / %d 条   新 %6.2fs / %d 条（%.0f×）'
          % (t_old, len(ref), t_new, len(new), t_old / max(t_new, 1e-6)))
    chk(len(ref) == len(new), '条数一致（%d）' % len(new))
    keys = ('x', 'y', 'cell_off', 'cell_key', 'tag_key', 'count',
            'tagid', 'trigger', 'trigger_name', 'obj_off', 'link_off')
    diffs = []
    for a, b in zip(ref, new):
        for k in keys:
            if a.get(k) != b.get(k):
                diffs.append((k, a.get(k), b.get(k), a.get('x'), a.get('y')))
                break
    chk(not diffs, '全部字段逐条一致（差异 %d 处）' % len(diffs))
    if diffs:
        for x in diffs[:5]:
            print('        %s: 旧 %r → 新 %r @(%s,%s)' % x)
    # 二次调用走缓存
    t0 = time.perf_counter()
    d.cell_markers()
    chk(time.perf_counter() - t0 < 0.01, '第二次调用走缓存（<0.01s）')

print('\n合计 PASS=%d FAIL=%d' % (OK, FAIL))
sys.exit(0 if FAIL == 0 else 1)
