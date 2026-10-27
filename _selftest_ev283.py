# -*- coding: utf-8 -*-
"""自检 ev283 —— 覆盖物（格对象 +0x58 / 地图 OverlayPack LCW 解码 / 挡路判定）

判定口径（全部"本机实测"）：
  ① LCW 空块解出 8192×0xFF；有矿块 36 字节正好吃完、解够 8192；
  ② `2.map` → 14 个 TIB08 矿石格；
  ③ `新建文件夹 (23)/ALL01UMD.sav` × `all01umd.map`：地图 1525 个覆盖物格，
     格对象 `+0x58` 逐格相符 ≥ 1513（实测定案 99.2%）；
  ④ 玩家档 `SAVE4823.SAV` 有 136 个覆盖物格，其中 14 个 TIB08；
  ⑤ 分类：矿石不挡、墙/石头/`Land=Rock`/硬编码墙挡、桥面/铁轨不挡；
  ⑥ 刷兵口径（玩家 2026-10-26 定）：引擎自己挪格 ⇒ 编辑器**不挪**，
     所以这里只验"能不能判定挡路"，不验自动挪位。
跑法：python _selftest_ev283.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from core import overlay_pack as op
from core.editor_core import SaveDoc

MAP_OK = r'E:/YURI/新建文件夹 (23)/all01umd.map'
SAV_OK = r'E:/YURI/新建文件夹 (23)/ALL01UMD.sav'
MAP2 = r'C:/Users/zhao5/Desktop/2.map'
SAV2 = r'E:/YURI/SAVE4823.SAV'
RULES = r'E:/YURI/rulesmd.ini'

ok = fail = 0


def ck(cond, msg):
    global ok, fail
    if cond:
        ok += 1
        print('  ✓ %s' % msg)
    else:
        fail += 1
        print('  ✗ %s' % msg)


print('① LCW 基本块')
e = op.lcw_decode(bytes.fromhex('81fffeff1fff80'), 8192)
ck(len(e) == 8192 and set(e) == {0xFF}, '空块 7 字节 → 8192×0xFF')
big = bytes.fromhex('81fffe4c0eff83707070fefd01ff0200fffd014f0eff00024d10ff01024c0efeb209ff80')
b = op.lcw_decode(big, 8192)
ck(len(b) == 8192, '有矿块 36 字节 → 8192（实际 %d）' % len(b))
ck(b[:4] == b'\xff\xff\xff\xff' and b'ppp' in b, '有矿块的 0x70 字面量解出')

print('② 2.map 的矿格')
if not os.path.exists(MAP2):
    ck(True, '玩家样本 2.map 已不在桌面（跳过；LCW 基本块已在上一步验过）')
else:
    t2, f2 = op.read_map_packs(MAP2)
    cells2 = [(i % 512, i // 512, t2[i]) for i in range(262144) if t2[i] != 0xFF]
    ck(len(cells2) == 14 and all(v == 112 for _, _, v in cells2), '14 个覆盖物格且全是 112=TIB08')
    ck((77, 71) in [(x, y) for x, y, _ in cells2], '(77,71) 在矿格列表里')

print('③ 地图真值 × 存档 +0x58（定案证据）')
mt, _mf = op.read_map_packs(MAP_OK)
d = SaveDoc(SAV_OK)
truth = [(i % 512, i // 512, mt[i]) for i in range(262144) if mt[i] != 0xFF]
hit = same = 0
for x, y, v in truth:
    if d.cell_offsets().get((x, y)) is None:
        continue
    hit += 1
    if d.overlay_at(x, y) == v:
        same += 1
ck(len(truth) == 1525, '地图覆盖物格 1525（实际 %d）' % len(truth))
ck(hit == 1525, '这些格在存档里都存在（%d）' % hit)
ck(same >= 1513, '+0x58 逐格相符 %d/%d（≥1513 才算定案）' % (same, hit))
emp = sum(1 for xy in d.cell_offsets() if d.overlay_at(*xy) is None)
ck(emp > 50000, '其余 %d 格 +0x58 = 0xFFFFFFFF' % emp)

print('④ 玩家档 SAVE4823')
d2 = SaveDoc(SAV2)
oc = d2.overlay_cells()
ck(len(oc) == 136, '覆盖物格 136（实际 %d）' % len(oc))
n112 = sum(1 for v in oc.values() if v == 112)
ck(n112 == 14, '其中 TIB08 矿石 14 格（实际 %d）' % n112)
info = d2.cell_overlay_info(70, 75, ini_path=RULES)
ck(info and info['index'] == 112, '(70,75) 是矿石格')
ck(info and info['blocks'] is False, '矿石不算挡路')
ck('矿石' in (info or {}).get('category', ''), '矿石类别文字：%s' % (info or {}).get('category'))
ck(d2.overlay_label(241, RULES).startswith('BRIDGEB1'), '索引 241 → BRIDGEB1')
ck(d2.overlay_label(9999, RULES) == '#9999', '未知索引不编名字')

print('⑤ 挡路判定（INI 字段驱动，§283 玩家裁定）')
cases = [(112, False, 'TIB08 矿石'),
         (30, False, 'GEM03 宝石'),
         (241, False, 'BRIDGEB1 桥面'),
         (77, False, 'LOBRDG01 低桥(Land=Road)'),
         (173, True, 'SROCK03 石头'),
         (1, True, 'GASAND 沙袋(Wall=yes)'),
         (3, True, 'GAWALL 围墙(Wall=yes)'),
         (203, True, 'FENCE20 (Land=Rock)'),
         (2, True, 'CYCL 铁链网(硬编码)'),
         (23, True, 'FENC 铁丝网(硬编码)')]
for idx, want, nm in cases:
    got = d2.overlay_blocks_placement(idx, RULES)
    ck(got == want, '%s → blocks=%s' % (nm, got))

print('⑥ 相邻空置格（只用于给玩家建议，不自动挪）')
blk = [(xy, v) for xy, v in oc.items() if d2.overlay_blocks_placement(v, RULES)]
ck(len(blk) > 0, '本档挡路覆盖物格 %d 个' % len(blk))
bxy = blk[0][0] if blk else (0, 0)
ck(d2.cell_ovl_blocked(bxy[0], bxy[1], ini_path=RULES) is not None,
   '%s 判为挡路' % (bxy,))
r = d2.overlay_free_cell_near(bxy[0], bxy[1], radius=8, ini_path=RULES)
ck(r is not None and r[2] >= 1, '%s 旁找到空置格 %s' % (bxy, r))
r2 = d2.overlay_free_cell_near(70, 75, radius=3, ini_path=RULES)
ck(r2 == (70, 75, 0), '矿石格本身就算可用（不挪）：%s' % (r2,))

print('\n结果：%d 通过 / %d 失败' % (ok, fail))
sys.exit(1 if fail else 0)
