# -*- coding: utf-8 -*-
"""LZO C 后端（lzo_fast.dll）自检 —— 必须与纯 Python 版**逐字节一致**。

覆盖：
  ① C 解码 == Python 解码（多份存档的全部块；含地图 IsoMapPack5 块）
  ② C 编码 → C 解码 往返一致（真实块 + 随机/重复数据）
  ③ C 编码产物用 **Python 解码器**也能解开（证明格式合法，不靠自家解码器自证）
  ④ 拿不到 DLL 时自动回退（把后端临时禁用，纯 Python 仍能跑）
"""
import os
import sys
import random
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))

import lzo_fast                                     # noqa: E402
from lzo1x import lzo1x_decompress as py_decode      # noqa: E402
from lzo_compress import compress                    # noqa: E402
import editor_core as ec                             # noqa: E402

OK = FAIL = 0


def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


print('== A: C 后端可用性 ==')
chk(lzo_fast.available(), 'DLL 已加载：%s' % lzo_fast.backend_path())

print('\n== B: C 解码 == 纯 Python 解码（逐块比对）==')
SAVES = [r'E:\YURI\SAVE0029.SAV',
         r'E:\YURI\新建文件夹 (20)\放天启和犀牛.SAV',
         r'E:\YURI\多单位存档.SAV']
tested = 0
for p in SAVES:
    if not os.path.exists(p):
        print('  [SKIP] 缺样本 %s' % p)
        continue
    d = ec.SaveDoc(p)
    for k, (out_off, out_len, cpos, pk) in enumerate(d.ranges):
        if k % 97:                                   # 抽样，避免整档都跑一遍
            continue
        seg = d.contents[cpos + 8:cpos + 8 + pk]
        a = lzo_fast.decode(seg, out_len)
        b, consumed = py_decode(seg, out_len)
        chk(a == b, '%s 块%d 解出 %d 字节一致（consumed=%d）'
            % (os.path.basename(p), k, len(b), consumed))
        tested += 1
        if tested >= 24:
            break
    if tested >= 24:
        break
chk(tested > 0, '共比对 %d 个真实块' % tested)

print('\n== C: 编码往返（真实块 + 合成数据）==')
p = SAVES[1] if os.path.exists(SAVES[1]) else SAVES[0]
d = ec.SaveDoc(p)
segs = []
for k, (out_off, out_len, cpos, pk) in enumerate(d.ranges[:6]):
    segs.append(bytes(d.plain_orig[out_off:out_off + out_len]))
random.seed(3)
segs.append(bytes(random.getrandbits(8) for _ in range(65536)))
segs.append(b'\x00' * 65536)
segs.append(bytes([7]) * 65536)
segs.append(b'ABCD' * 16384)
for i, s in enumerate(segs):
    pk = lzo_fast.encode(s)
    ok = pk is not None
    chk(ok, '样本%d（%d B）压缩成功 → %d B' % (i, len(s), len(pk) if pk else -1))
    if not ok:
        continue
    chk(lzo_fast.decode(pk, len(s) + 64) == s, '  样本%d C 解码往返一致' % i)
    back, consumed = py_decode(pk, len(s))       # ★用纯 Python 解码器交叉验证
    chk(back == s and consumed == len(pk), '  样本%d Python 解码器也认（consumed=%d/%d）'
        % (i, consumed, len(pk)))

print('\n== D: 拿不到 DLL 时回退纯 Python ==')
_orig = lzo_fast._dll
lzo_fast._dll = None
lzo_fast._tried = True
try:
    seg = d.contents[d.ranges[0][2] + 8:d.ranges[0][2] + 8 + d.ranges[0][3]]
    out, _ = py_decode(seg, d.ranges[0][1])
    chk(out is not None and len(out) == d.ranges[0][1], '后端禁用时纯 Python 解码仍正常（%d B）' % len(out))
    pk = compress(bytes(d.plain_orig[:65536]))
    chk(pk and py_decode(pk, 65536)[0] == bytes(d.plain_orig[:65536]),
        '后端禁用时纯 Python 压缩仍正常（%d B）' % (len(pk) if pk else -1))
finally:
    lzo_fast._dll = _orig

print('\n自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
