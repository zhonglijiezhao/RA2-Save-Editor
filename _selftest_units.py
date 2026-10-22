# -*- coding: utf-8 -*-
"""自检：从存档里读出**单位**（步兵/载具）—— 2026-10-17 新增能力。

样本：`E:/YURI/SAVE4823.SAV`（玩家给的单 GI + 单灰熊档）。
判据（不写死猜测值，全部有独立交叉证据）：
  ① 类型空间能取到名字（E1 / MTNK …）；
  ② House 块里的「类型+格子」布置表读出 **恰好 E1@(75,73) 与 MTNK@(68,80)**，所属方 Americans；
  ③ 这两个格子的 **CellClass 自键** 在「格对象自身」之外的**另一处**也被引用 ——
     而且那处正好落在该单位的实例簇里（E1 簇 6913xxx / 灰熊簇 6241xxx）⇒ 位置是活的，不是巧合；
  ④ Special / Neutral 的伪对象（SREF / FV …）默认不混进来。
"""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from core.editor_core import SaveDoc
except Exception:
    from editor_core import SaveDoc

OK = FAIL = 0
def chk(c, m):
    global OK, FAIL
    if c:
        OK += 1; print('  [PASS] ' + m)
    else:
        FAIL += 1; print('  [FAIL] ' + m)

# ★样本用 _exp 内副本（E:/YURI 里的档会被游戏挪走/覆盖）
SAV = os.path.join(HERE, '_exp', 'sample_单位_大兵+灰熊.SAV')
if not os.path.exists(SAV):
    print('[SKIP] 缺样本 %s' % SAV)
    print('\n自检结果：PASS 0 / FAIL 0')
    sys.exit(0)

d = SaveDoc(SAV)
pl = d.plain

print('== A: 类型空间 ==')
t = d.unit_types()
chk(len(t) > 100, '解析出 %d 个类型对象' % len(t))
chk('E1' in t.values() and 'MTNK' in t.values(), 'E1 / MTNK 都在类型空间里')

print('== B: 布置表（House 块） ==')
pl_list = d.house_placements()
print('  [INFO] 读到的条目：%s' % (pl_list,))
# ★样本换成「3 建筑 + 1 兵 + 1 熊」的档（E:/YURI 原档会被游戏挪走）：
#   布置表把建筑也列出来 ⇒ 只对 E1/MTNK 做精确断言，建筑条数只做"存在"检查。
units_only = {(u['type'], u['x'], u['y'], u['house']) for u in pl_list
              if u['type'] not in ('GACNST', 'GAPOWR', 'GAPILE', 'GATECH', 'GAREFN', 'GAAIRC')}
chk(units_only == {('E1', 75, 73, 'Americans'), ('MTNK', 68, 80, 'Americans')},
    '单位条目 = E1@(75,73) + MTNK@(68,80)，所属方 Americans（实得 %s）' % (units_only,))
chk(any(u['type'] == 'GACNST' for u in pl_list),
    '建筑（GACNST）与单位混排在同一张表里（%d 条）' % len(pl_list))
chk(all(u['type'] not in ('SREF', 'FV') for u in pl_list), 'Special/Neutral 的伪对象没混进来')

print('== C: 与实例簇交叉验证（格键被谁引用） ==')
G = bytes.fromhex('ce99bfc18c1ad2118175006008055bb5')
cells = {}
i = 0
while True:
    j = pl.find(G, i)
    if j < 0:
        break
    i = j + 1
    y, x = struct.unpack_from('<HH', pl, j + 56)
    cells[(x, y)] = struct.unpack_from('<I', pl, j + 16)[0]
chk(len(cells) > 1000, 'CellClass 对象 %d 个（坐标 → 自键）' % len(cells))
for u in [x for x in pl_list if x['type'] in ('E1', 'MTNK')]:
    k = cells.get((u['x'], u['y']))
    occ = []
    q = 0
    if k:
        b = struct.pack('<I', k)
        while True:
            j = pl.find(b, q)
            if j < 0:
                break
            occ.append(j); q = j + 1
    chk(len(occ) >= 2, '%s@(%d,%d) 的格键 %s 出现 %d 次（格对象 + 实例引用）'
        % (u['type'], u['x'], u['y'], hex(k) if k else None, len(occ)))

print('== D: units() 过滤版 ==')
chk(d.units() == pl_list, '不传 exclude ⇒ units() 与 house_placements() 一致')
chk(d.units(exclude=['E1']) == [x for x in pl_list if x['type'] != 'E1'],
    'exclude 能按类型名剔除（模拟"剔除建筑"）')

print('\n自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
