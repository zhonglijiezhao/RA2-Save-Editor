# -*- coding: utf-8 -*-
"""§288 自检：坏档自愈 + 保存闸门。

① 玩家那份读档弹框的档（`SAVE0665_改_修改.SAV`）里残留 1 处已删建筑的键
   ⇒ `sweep_key_refs()` 能剔掉它，剔完能正常保存；
② 只要还有清不掉的残留引用，`save()` 必须**拒绝写盘**（宁可不给存，也不给出一个进游戏必弹框的档）。

跑法：D:/py/python.exe _selftest_ev289.py
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

BROKEN = r'E:/YURI/SAVE0665_改_修改.SAV'
KEY = 0x1BD99EE0
PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


if not os.path.exists(BROKEN):
    print('样本不存在，跳过：%s' % BROKEN)
    sys.exit(0)

td = tempfile.mkdtemp(prefix='_ev289_')
pat = struct.pack('<I', KEY)

# ── ① 坏档自愈 ──
w = os.path.join(td, 'b.sav')
shutil.copy2(BROKEN, w)
d = SaveDoc(w)
ck(bytes(d.plain).count(pat) == 1, '坏档里残留 1 处键 %08X' % KEY)
lo = d.leftover_refs({KEY})
ck(KEY in lo, 'leftover_refs 认出来了：%s' % ({'%08X' % k: v for k, v in lo.items()},))
tabs = d._key_tables(KEY)
ck(len(tabs) == 1, '它还在 %d 张登记表里（混合表，§287 的老漏网）' % len(tabs))
n = d.sweep_key_refs({KEY})
ck(n == 1, 'sweep_key_refs 剔掉 %d 处' % n)
ck(bytes(d.plain).count(pat) == 0, '剔完 0 处')
ck(d.leftover_refs({KEY}) == {}, '再查残留为空')
out = os.path.join(td, 'b_fixed.sav')
d.save(out)
d2 = SaveDoc(out)
ck(bytes(d2.plain).count(pat) == 0, '修完的档重新打开仍然 0 处')
ck(len(d2.buildings()) == 941, '建筑 941 栋（没误删别的）')
ck(len(d2.records) == 496, '记录 496 条（玩家新建的触发/标签保留）')

# ── ② 保存闸门 ──
try:
    d2._unsafe_leftover = {KEY: [0x100]}
    d2.save(os.path.join(td, 'must_fail.sav'))
    ck(False, '带残留引用居然存成功了（闸门失效）')
except RuntimeError as e:
    ck('拒绝保存' in str(e), '闸门拦住：%s' % str(e).splitlines()[0])
except Exception as e:
    ck(False, '闸门抛了别的异常：%r' % (e,))
else:
    pass

# 闸门不该影响正常档（注意：没有任何修改时 save() 本来就抛「没有任何修改」，那是正常护栏）
try:
    d3 = SaveDoc(out)
    p = os.path.join(td, 'ok.sav')
    d3.save(p)
    ck(os.path.getsize(p) > 0, '干净档照常保存（%d B）' % os.path.getsize(p))
except RuntimeError as e:
    ck('没有任何修改' in str(e), '干净档没被闸门拦（只是「%s」）' % str(e).splitlines()[0])
except Exception as e:
    ck(False, '干净档被闸门误拦：%r' % (e,))

shutil.rmtree(td, ignore_errors=True)
print()
print('结果：%d 通过 / %d 失败' % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
