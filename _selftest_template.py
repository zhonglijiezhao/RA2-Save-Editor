# -*- coding: utf-8 -*-
"""自检：克隆模板必须"原生"（P1「N27 弹框」修法回归）。

核心断言：
  A) `母本 + GAPOWR + ATESLA`（连加两种）里的 ATESLA 记录
     == `母本 + ATESLA`（只加一栋）里的 ATESLA 记录
     —— 除"必然不同"的字段（键/实例ID/坐标/所属方/电力衍生）外**逐字节相同**。
     这等价于"第二栋没有克隆我们刚注入的第一栋"。
  B) 模板选择命中本档**原生**记录（母本末栋 = GAAIRC），而不是 0x1396xxxx 注入池。
  C) 连加同类型（GAPOWR ×2）仍然正常，且第二栋的模板 = 原生 GAPOWR。
  D) `_is_injected_key` 两套判据（会话表 / 键池区间）都成立。
  E) 保存 → 重读后记录数与键集合自洽（结构没写坏）。
"""
import os, sys, struct, hashlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '_exp'))
from core.editor_core import SaveDoc

SRC = os.path.join(HERE, '_exp', '母本_原存档.SAV')
OK = FAIL = 0
def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)

# 必然不同的载荷偏移（键/实例ID/坐标/所属方/屏幕坐标）
VOLATILE = {0, 20, 208, 264, 232, 1304, 972, 336, 544, 1344,
            160, 164, 600, 604, 1616, 1620, 1600, 1604}

def payload(pl, key, typ=None):
    """按"新键"定位建筑记录的载荷起点。"""
    hs = []
    q = 0
    pat = SaveDoc.BLD_REC_GUID
    while True:
        i = pl.find(pat, q)
        if i < 0:
            break
        hs.append(i)
        q = i + 1
    for g in hs:
        if struct.unpack_from('<I', pl, g + 16)[0] == key:
            return g + 16
    raise RuntimeError('找不到键 %08x 的建筑记录' % key)

print('== A/B: 连加两种 vs 只加一栋 ==')
dA = SaveDoc(SRC)
r1 = dA.add_placed_building('GAPOWR', 69, 64, house='Russians')
r2 = dA.add_placed_building('ATESLA', 68, 65, house='Russians')
dB = SaveDoc(SRC)
rB = dB.add_placed_building('ATESLA', 68, 65, house='Russians')

plA, plB = dA.plain, dB.plain
oA, oB = payload(plA, r2['key']), payload(plB, rB['key'])
diffs = []
for k in range(0, 1844 - 3, 4):
    if k in VOLATILE:
        continue
    va = struct.unpack_from('<I', plA, oA + k)[0]
    vb = struct.unpack_from('<I', plB, oB + k)[0]
    if va != vb:
        diffs.append((k, va, vb))
chk(not diffs, '连加两栋时的第二栋(ATESLA) 与单独加一栋逐字节一致（差异 %d 处）' % len(diffs))
for k, va, vb in diffs[:12]:
    print('        +%-5d 连加=%08x 单加=%08x' % (k, va, vb))

print('== B: 模板必须原生 ==')
hs0 = SaveDoc(SRC)
g0 = hs0._bld_group()[0]
last_native_key = struct.unpack_from('<I', hs0.plain, g0[-1] + 16)[0]
chk(r2['tpl_key'] == last_native_key and r2['cross'],
    '跨类型模板 = 本档原生末栋（%08x），不是注入池（cross=%s）' % (r2['tpl_key'], r2['cross']))
chk(r1['tpl_key'] not in (r1['key'],) and r1['tpl_key'] < 0x13960000,
    'GAPOWR 同类型模板 = 原生 GAPOWR（%08x）' % r1['tpl_key'])

print('== C: 连加同类型 ==')
dC = SaveDoc(SRC)
c1 = dC.add_placed_building('GAPOWR', 69, 64, house='Russians')
c2 = dC.add_placed_building('GAPOWR', 71, 64, house='Russians')
chk(c2['tpl_key'] == c1['tpl_key'] and c2['tpl_key'] < 0x13960000,
    '第二栋 GAPOWR 也克隆原生记录（%08x），不克隆刚注入的第一栋' % c2['tpl_key'])
chk(c1['key'] != c2['key'], '两栋键不同（%08x / %08x）' % (c1['key'], c2['key']))

print('== D: _is_injected_key ==')
chk(dA._is_injected_key(c1['key']), '会话注入表判据命中 %08x' % c1['key'])
dD = SaveDoc(SRC)                       # 新文档（注入表为空）⇒ 走键池区间判据
chk(dD._is_injected_key(c1['key']), '键池区间判据命中 %08x（关档重开场景）' % c1['key'])
chk(not dD._is_injected_key(last_native_key), '原生键 %08x 不判注入' % last_native_key)

print('== E: 保存 → 重读 ==')
tmp = os.path.join(HERE, '_exp', '_selftest_tpl_out.SAV')
if os.path.exists(tmp):
    os.remove(tmp)
dA.save(tmp, backup=False)
dR = SaveDoc(tmp)
hsR = dR._bld_group()[0]
chk(len(hsR) == 13, '重读后建筑数 = 母本 11 + 2 = 13（实测 %d）' % len(hsR))
keysR = {struct.unpack_from('<I', dR.plain, g + 16)[0] for g in hsR}
chk(r1['key'] in keysR and r2['key'] in keysR, '两栋新键都在重读档里')
chk(len(keysR) == 13, '键无重复（%d 个）' % len(keysR))
os.remove(tmp)

print('== F: 模板不能是"未落位"空壳（坐标 -1,-1） ==')
KB = r'E:/YURI/新建文件夹 (20)/韩国无任何建筑.SAV'
if os.path.exists(KB):
    dK = SaveDoc(KB)
    plK = dK.plain
    hsK = dK._bld_group()[0]
    tK, crossK = dK._pick_bld_template(hsK, plK, dK.building_types()['ATESLA'])
    cx = (struct.unpack_from('<I', plK, tK + 16 + 600)[0] - 128) // 256
    cy = (struct.unpack_from('<I', plK, tK + 16 + 604)[0] - 128) // 256
    # 本档末栋 ATESLA 正是 (-1,-1) 的空壳；修好后必须选到**已落位**的
    last_key = struct.unpack_from('<I', plK, hsK[-1] + 16)[0]
    chk(cx >= 0 and cy >= 0, 'ATESLA 模板已落位 @(%d,%d)（键 %08x）' % (cx, cy, struct.unpack_from('<I', plK, tK + 16)[0]))
    ghosts = []
    for g in hsK:
        if struct.unpack_from('<I', plK, g + 16 + 1316)[0] != dK.building_types()['ATESLA']:
            continue
        gx = (struct.unpack_from('<I', plK, g + 16 + 600)[0] - 128) // 256
        if gx < 0:
            ghosts.append(struct.unpack_from('<I', plK, g + 16)[0])
    chk(bool(ghosts), '该档确实存在未落位的 ATESLA 空壳（%d 个），说明这条判据有实际作用' % len(ghosts))
else:
    print('  [SKIP] 缺样本 %s' % KB)

print('\n自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
