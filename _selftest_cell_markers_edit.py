# -*- coding: utf-8 -*-
"""§SOV12 自检：单元标记（CellTag 挂接点）的**索引 / 定位 / 删除**。

覆盖：
  1. SAVE6784 的 18 个标记：坐标 = 6×3 方阵、同属一个 CellTag、count = 18
  2. `cell_markers_at(x,y)` 按格取
  3. `dry_run` 一个字节都不动
  4. 真删一个 ⇒ CellClass +80 清 0、CellTag +64 −1、长度不变、其它标记不受影响
  5. 拿旧 marker 再删一次 ⇒ 拒绝（防误删别处的同一个键）
  6. 删满 18 个 ⇒ 挂接数归 0、索引空
  7. 手工把 8 字节改回去 ⇒ 完全复原（证明"只改 8 字节、可回改"）
  8. SAVE5739 交叉：只删 1 个 ⇒ 只少 1 个，同 CellTag 的其它标记都在
跑：python _selftest_cell_markers_edit.py
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))
from editor_core import SaveDoc, CELL_COUNT_OFF                        # noqa: E402

SAV1 = r'E:\YURI\SAVE6784.SAV'
SAV2 = r'E:\YURI\SAVE5739.SAV'
OK = FAIL = 0


def chk(cond, msg, extra=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s%s' % (msg, '' if extra is None else ('  → %r' % (extra,))))


print('== A. SAVE6784：18 个标记 ==')
d = SaveDoc(SAV1)
ms = d.cell_markers()
chk(len(ms) == 18, '标记数 = 18', len(ms))
xs = sorted({m['x'] for m in ms})
ys = sorted({m['y'] for m in ms})
chk(xs == [74, 75, 76, 77, 78, 79] and ys == [76, 77, 78],
    '★坐标是 x 74..79 × y 76..78 的 6×3 方阵', (xs, ys))
chk(len({m['cell_key'] for m in ms}) == 1, '都挂在同一个 CellTag 上',
    {m['cell_key'] for m in ms})
chk(all(m['count'] == 18 for m in ms), '挂接数 = 18', {m['count'] for m in ms})
chk(all(m['trigger_name'] == 'BBBBB' for m in ms), '归属解成触发名 BBBBB',
    {m['trigger_name'] for m in ms})
chk(all(m['link_off'] - m['obj_off'] == SaveDoc.CELL_TAGLINK_OFF for m in ms),
    '★每个命中都正好在 CellClass +80', {(m['link_off'] - m['obj_off']) for m in ms})
chk(len(d.cell_markers_at(74, 77)) == 1, 'cell_markers_at(74,77) = 1 个')
chk(d.cell_markers_at(10, 10) == [], 'cell_markers_at(10,10) = 空')

print('== B. dry_run 不动字节 ==')
m0 = d.cell_markers_at(74, 76)[0]
before = bytes(d.plain[m0['link_off']:m0['link_off'] + 4])
cnt_before = struct.unpack_from('<I', d.plain, m0['cell_off'] + CELL_COUNT_OFF)[0]
info = d.clear_cell_marker(m0, dry_run=True)
chk(info.get('dry_run') is True, 'dry_run 标记存在')
chk(info['count_before'] == 18 and info['count_after'] == 17,
    'dry_run 报 18 → 17', (info['count_before'], info['count_after']))
chk(bytes(d.plain[m0['link_off']:m0['link_off'] + 4]) == before, '挂接键没被改')
chk(struct.unpack_from('<I', d.plain, m0['cell_off'] + CELL_COUNT_OFF)[0] == cnt_before,
    '挂接数没被改')
chk(len(d.cell_markers()) == 18, '索引仍是 18')

print('== C. 真删一个 ==')
n_bytes = len(d.plain)
info = d.clear_cell_marker(m0)
# 原值要在**删除之前**取（B 段已经存进 `before`；这里别在删完再读，会读到 0）
chk(info['count_before'] == 18 and info['count_after'] == 17,
    '返回 18 → 17', (info['count_before'], info['count_after']))
chk(struct.unpack_from('<I', d.plain, m0['link_off'])[0] == 0,
    '★CellClass +80 已清 0')
chk(struct.unpack_from('<I', d.plain, m0['cell_off'] + CELL_COUNT_OFF)[0] == 17,
    '★CellTag +64 变成 17')
chk(len(d.plain) == n_bytes, '存档长度不变（原地改）', (n_bytes, len(d.plain)))
ms2 = d.cell_markers()
chk(len(ms2) == 17, '索引变 17', len(ms2))
chk(d.cell_markers_at(74, 76) == [], '★(74,76) 的标记没了')
chk(len(d.cell_markers_at(75, 76)) == 1, '邻格 (75,76) 还在')
chk(all(m['count'] == 17 for m in ms2), '剩下的都读到新挂接数 17',
    {m['count'] for m in ms2})

print('== D. 拿旧 marker 再删一次 ⇒ 拒绝 ==')
try:
    d.clear_cell_marker(m0)
    chk(False, '应该抛错但没抛')
except ValueError as e:
    chk('现在挂的不是该 CellTag' in str(e), '拒绝并说明原因（%s）' % str(e)[:40])
except Exception as e:
    chk(False, '抛了别的异常：%r' % (e,))

print('== E. 手工改回去 ⇒ 复原 ==')
d.plain[m0['link_off']:m0['link_off'] + 4] = before      # B 段存的原挂接键
d.plain[m0['cell_off'] + CELL_COUNT_OFF:m0['cell_off'] + CELL_COUNT_OFF + 4] = \
    struct.pack('<I', 18)
d._cellmarks = None
d._celltags = None      # 挂接数也在这张缓存里，改回去要一起清
d._attov = None
chk(len(d.cell_markers()) == 18, '★改回 8 字节 ⇒ 又变 18 个')
chk(d.cell_markers_at(74, 76)[0]['count'] == 18, '挂接数也回到 18')

print('== F. 全删 18 个 ⇒ 挂接数归 0 ==')
for i, m in enumerate(list(d.cell_markers())):
    d.clear_cell_marker(m, rescan=(i == 17))
chk(d.cell_markers() == [], '索引空了', len(d.cell_markers()))
ct = [c for c in d.celltag_nodes() if c['x'] == m0['cell_key']]
chk(ct and ct[0]['count'] == 0, 'CellTag 挂接数 = 0', ct[0]['count'] if ct else None)

print('== G. SAVE5739 交叉（274 个 / 6 条 CellTag）==')
d2 = SaveDoc(SAV2)
ms3 = d2.cell_markers()
chk(len(ms3) > 100, '标记数 = %d' % len(ms3))
target = ms3[len(ms3) // 2]
same = [m for m in ms3 if m['cell_key'] == target['cell_key']]
cnt0 = target['count']
d2.clear_cell_marker(target)
ms4 = d2.cell_markers()
chk(len(ms4) == len(ms3) - 1, '★只少了 1 个（%d → %d）' % (len(ms3), len(ms4)),
    (len(ms3), len(ms4)))
chk(len([m for m in ms4 if m['cell_key'] == target['cell_key']]) == len(same) - 1,
    '同一 CellTag 的其它 %d 个都还在' % (len(same) - 1))
chk(d2.cell_markers_at(target['x'], target['y']) == [],
    '被删的那格没了 (%d,%d)' % (target['x'], target['y']))
left = [m for m in ms4 if m['cell_key'] == target['cell_key']][0]
chk(left['count'] == cnt0 - 1, '挂接数 %d → %d' % (cnt0, left['count']))

print('\n§SOV12：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
