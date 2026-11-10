# -*- coding: utf-8 -*-
"""自检：「可见区域」读写（存档镜像 ↔ 地图 [Map] LocalSize）

玩家 2026-11-10 要从概览页改这个（地编「地图参数 ▸ 可见区域」）。
定位判据与证据见 `SaveDoc._scan_visible_area` 的注释。

跑法：`python _selftest_va_edit.py [存档] [地图]`
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc                                     # noqa: E402

SAV = sys.argv[1] if len(sys.argv) > 1 else None
MAP = sys.argv[2] if len(sys.argv) > 2 else r'E:\YURI\all01umd.map'
TMPD = os.path.join(HERE, '_tmp_va')
TMP = os.path.join(TMPD, '_va_selftest.sav')

# ★2026-11-10：**绝不直接在游戏目录的原档上跑**（那一档随时会被游戏同名默认档顶替，
#   而且自检不该碰玩家的文件）。没给参数就复制一份到项目内再测。
if not SAV:
    src = r'E:\YURI\ALL01UMD.sav'
    if not os.path.exists(src):
        print('跳过：找不到样本存档 %s' % src)
        sys.exit(0)
    os.makedirs(TMPD, exist_ok=True)
    SAV = os.path.join(TMPD, 'va_selftest_src.SAV')
    import shutil
    shutil.copy2(src, SAV)
OK = FAIL = 0


def chk(c, msg):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


print('样本：%s' % SAV)
d = SaveDoc(SAV)
d.load()

va = d.visible_area()
chk(va is not None, '能定位到可见区域字段')
if not va:
    print('\n%d/%d' % (OK, OK + FAIL))
    sys.exit(1)
print('        off=%#x  Size=%d×%d  LocalSize=%s' % (va['off'], va['size'][0],
                                                     va['size'][1], va['local']))

chk(va['size'] == (80, 80), 'Size 读到 80×80（与地图 [Map] Size 同口径）')
chk(va['local'] == (3, 5, 74, 69), 'LocalSize 读到 3,5,74,69')

# 与地图对拍（地图只读）
try:
    ml = d.map_local_size(MAP)
except Exception:
    ml = None
chk(ml == va['local'], '与地图 [Map] LocalSize 一致（地图读到 %s）' % (ml,))

# 明文里只有一份副本
import struct                                                       # noqa: E402
pat = struct.pack('<4i', *va['local'])
dup, i = 0, 0
while True:
    j = bytes(d.plain).find(pat, i)
    if j < 0:
        break
    dup += 1
    i = j + 1
chk(dup == 1, '该四元组在明文里只有 1 份副本（实测 %d）' % dup)

# 越界必须拒绝
before = bytes(d.plain)
for args, label in (((0, 0, 81, 80), '宽超 Size'), ((0, 0, 80, 81), '高超 Size'),
                    ((-1, 0, 10, 10), '左为负'), ((0, 0, 0, 10), '宽为 0')):
    try:
        d.set_visible_area(*args)
        chk(False, '越界参数 %s 应被拒绝（%s）' % (args, label))
    except Exception:
        chk(bytes(d.plain) == before, '越界参数 %s 被拒绝且明文未动（%s）' % (args, label))

# 正常写入
r = d.set_visible_area(0, 0, 80, 80)
chk(r['new'] == (0, 0, 80, 80) and r['old'] == (3, 5, 74, 69), '写入返回 old/new 正确')
chk(d.visible_area()['local'] == (0, 0, 80, 80), '写后立刻读回新值')
after = bytes(d.plain)
diff = [i for i in range(min(len(before), len(after))) if before[i] != after[i]]
chk(len(before) == len(after), '明文长度不变（原地改）')
chk(diff and min(diff) >= r['off'] and max(diff) < r['off'] + 16,
    '只有那 16 字节变化（实际 %d 字节，区间 %#x..%#x）'
    % (len(diff), min(diff), max(diff)))

# 落盘 + 重开
os.makedirs(os.path.dirname(TMP), exist_ok=True)
d.save(TMP, backup=False)
d2 = SaveDoc(TMP)
d2.load()
va2 = d2.visible_area()
chk(va2 is not None and va2['local'] == (0, 0, 80, 80), '保存后重开仍读到 0,0,80,80')
chk(d2.visible_area()['size'] == (80, 80), '重开后 Size 仍是 80×80')
try:
    os.remove(TMP)
except Exception:
    pass

# 反向改回去
d.set_visible_area(3, 5, 74, 69)
chk(d.visible_area()['local'] == (3, 5, 74, 69), '能改回原值')

print('\n%d / %d' % (OK, OK + FAIL))
sys.exit(0 if FAIL == 0 else 1)
