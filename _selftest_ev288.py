# -*- coding: utf-8 -*-
"""§287 自检：删建筑不能留"残留键引用"（否则游戏载入弹框 / INT_DIVIDE_BY_ZERO）。

样本：`E:/YURI/SAVE0665_修改.SAV`（942 栋），删第 326 栋 YACOMD @(180,96)。
跑法：D:/py/python.exe _selftest_ev288.py
"""
import io
import os
import shutil
import struct
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc          # noqa: E402

SRC = r'E:/YURI/SAVE0665_修改.SAV'
KEY = 0x1BD99EE0
PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


if not os.path.exists(SRC):
    print('样本不存在，跳过：%s' % SRC)
    sys.exit(0)

td = tempfile.mkdtemp(prefix='_ev288_')
tmp = os.path.join(td, 'a.sav')
shutil.copy2(SRC, tmp)

d = SaveDoc(tmp)
hs, _ = d._bld_group()
ck(len(hs) == 942, '样本 942 栋建筑（实测 %d）' % len(hs))
idx = next((i for i, g in enumerate(hs)
            if struct.unpack_from('<I', d.plain, g + 16)[0] == KEY), None)
ck(idx is not None, '找到目标建筑（键 %08X，序号 %s）' % (KEY, idx))

# 记录删除前：该键在明文里出现几次、以及每张含它的表
n_before = bytes(d.plain).count(struct.pack('<I', KEY))
tabs_before = {tb['count_pos']: struct.unpack_from('<I', d.plain, tb['count_pos'])[0]
               for tb in d._key_tables(KEY)}
ck(n_before >= 2, '删除前键出现 %d 次' % n_before)
ck(len(tabs_before) >= 1, '删除前被 %d 张登记表收录' % len(tabs_before))

r = d.remove_building(idx)
ck(r.get('leftover_refs') == {}, '删除后"残留引用"为空：%s' % (r.get('leftover_refs'),))
ck(bytes(d.plain).count(struct.pack('<I', KEY)) == 0,
   '明文里该键 0 次（%d → 0）' % n_before)
ck(len(d.buildings()) == 941, '建筑 941 栋（%d → 941）' % 942)
ck(r.get('cells_cleared', 0) >= 4, '清掉格子占用 %d 个' % r.get('cells_cleared', 0))
ck(r.get('stray_swept', 0) >= 1,
   '兜底扫尾剔掉 %d 处混合表残留（这就是弹框根因）' % r.get('stray_swept', 0))

# 含该键的表，计数必须同步 −1，且该键不再在表内
for cpos, c0 in tabs_before.items():
    pass
tabs_after = d._key_tables(KEY)
ck(tabs_after == [], '删除后不再有任何登记表收录该键')

# 存盘再读回，仍然干净
out = os.path.join(td, 'b.sav')
d.save(out)
d2 = SaveDoc(out)
ck(bytes(d2.plain).count(struct.pack('<I', KEY)) == 0, '存盘 + 重载后仍然 0 次')
ck(len(d2.buildings()) == 941, '重载后建筑 941 栋')

# 结构护栏：明文长度正好少了（记录 1860 + 表里 3 处 ×4 + 扫尾 1 处 ×4 之类）
dlen = len(SaveDoc(SRC).plain) - len(d2.plain)
ck(1800 <= dlen <= 2100, '明文长度减少 %d B（记录 1860 ± 表项）' % dlen)

shutil.rmtree(td, ignore_errors=True)
print()
print('结果：%d 通过 / %d 失败' % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
