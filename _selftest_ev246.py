# -*- coding: utf-8 -*-
"""§246 自检：克隆必须清掉"模板残留的外来运行期指针"（2026-10-24 晚）。

背景（玩家三轮实测）：
  T97/T99（克隆飞碟，玩家验收"能飞、能拉回、打掉不崩"）→ 外来指针 7~8 个；
  T98（克隆飞碟，玩家实测"打掉就弹框"）→ 外来指针 247 个。
  ⇒ 判据 = 克隆体的"外来指针"个数必须落在**本档干净原生**的区间里。

"外来指针"定义：dword 落在键值域 `0x01000000..0x17FFFFFF`、但本档并没有这个键
—— 那是骨架**自己那一局**的运行期指针（链表/队列/传感器…，形如 0x145Cxxxx）。
原生单位由引擎在自己会话里建好、读档时重建，所以没事；克隆体带过来就会指向
一堆属于骨架的对象，死亡/深层更新时顺着走就炸（except.txt 里 Eip 落在堆上）。
"""
import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))

from _paths import find_sav, out          # noqa: E402
import editor_core as ec                  # noqa: E402

KEY_LO, KEY_HI = 0x01000000, 0x17FFFFFF
TYPE = 'DISK'

n_ok = n_bad = 0


def ck(msg, cond, extra=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('  [PASS] %s' % msg)
    else:
        n_bad += 1
        print('  [FAIL] %s   %s' % (msg, extra))


def u32(pl, off):
    return struct.unpack_from('<I', pl, off)[0]


def foreign(doc, rec, own=None):
    """记录里"外来指针"的个数（键值域内、但本档没这个键）。

    `own` = 克隆体自己的新键：刚分配、还没进存档键集，必须排除
    （否则每个克隆都凭空多算 1 个 —— 它自己的自键）。
    """
    pl = doc.plain
    keys = set(doc.object_keys())
    if own is not None:
        keys = keys | {own & 0xFFFFFFFF}
    o = rec['off']
    return sum(1 for x in range(0, rec['size'] + rec['comp_size'] - 3, 4)
               if KEY_LO <= u32(pl, o + x) <= KEY_HI and u32(pl, o + x) not in keys)


print('=== §246 自检：外来运行期指针清理 ===')

SRC = find_sav('T85_老工程师_修位置副本槽.SAV')
d = ec.SaveDoc(SRC)
pl = d.plain
recs = [r for r in d.unit_records() if r['type'] == TYPE]
ck('样本档里 %s ≥ 20 架' % TYPE, len(recs) >= 20, len(recs))

# 本档原生的"外来指针"分布 —— 干净区间 = 最小那几个
nat = sorted(foreign(d, r) for r in recs)
CLEAN_HI = nat[len(nat) // 2]              # 干净上沿 = 原生中位数
print('  原生外来指针分布：min=%d 中位=%d max=%d ⇒ 干净上沿取 %d'
      % (nat[0], nat[len(nat) // 2], nat[-1], CLEAN_HI))

# ---- A. 正常克隆（自动挑骨架）----
info = d.add_unit_by_clone(TYPE, 108, 206, house=d.player_house(), veterancy=0, exact_cell=True)
# ★★★ 插入指针表/记录会让**记录偏移移位**（§206/§212/§243 踩过三次的坑）——
#     必须重新取 records 和 plain，不能沿用克隆前的旧引用！
rec = [r for r in d.unit_records() if r['key'] == info['key']][0]
pl = d.plain
f_new = foreign(d, rec, own=info['key'])
print('  A. 自动克隆：外来指针 %d 个（清理 %d）' % (f_new, info['foreign_ptrs']))
ck('A1. 克隆体落在"干净原生"区间', f_new <= CLEAN_HI,
   '%d > %d' % (f_new, CLEAN_HI))
ck('A2. 骨架确实是空闲的（§215 定案项）', info['scaffold_idle'], info['scaffold_type'])

# ---- B. 合法字段必须原样保留（不能再犯"多清"的错）----
keys = set(d.object_keys())
ck('B1. 类型键 +0x6D8 仍是有效键', u32(pl, rec['off'] + 0x6D8) in keys,
   hex(u32(pl, rec['off'] + 0x6D8)))
# ★House 键**不在 object_keys() 里**（House 不按对象锚点扫），`house_keys()` 返回的
#   还是阵营名而不是键 ⇒ 对 `_house_key_names()` 的键集合验。
_hk = set(d._house_key_names())
ck('B2. 所属方键 +0x160/+0x230 是本档 House 键',
   u32(pl, rec['off'] + 0x160) in _hk and u32(pl, rec['off'] + 0x230) in _hk,
   '%08X / %08X' % (u32(pl, rec['off'] + 0x160), u32(pl, rec['off'] + 0x230)))
ck('B2b. 所属方就是我们指定的玩家阵营',
   d._house_key_names().get(u32(pl, rec['off'] + 0x160)) == d.player_house(),
   '%s vs %s' % (d._house_key_names().get(u32(pl, rec['off'] + 0x160)), d.player_house()))
ck('B3. CLSID 前 8 字节没被清', u32(pl, rec['off']) != 0 or u32(pl, rec['off'] + 4) != 0,
   '%08X %08X' % (u32(pl, rec['off']), u32(pl, rec['off'] + 4)))
# ★§248.8：高度"只抬不压"——骨架飞得高（1998）时保持原值，不再强按成类型高度
ck('B4. 高度不低于类型高度（不卡地上）',
   u32(pl, rec['off'] + 0xB8) >= u32(pl, rec['off'] + 0x270) > 0,
   '%d / %d' % (u32(pl, rec['off'] + 0xB8), u32(pl, rec['off'] + 0x270)))
# ★§248.8：0x198 保留骨架值（= 匹配同档原生；地编新放的单位也是这个值）
_nat198 = [u32(d.plain, x['off'] + 0x198) for x in d.unit_records() if x['type'] == TYPE]
ck('B4b. +0x198 与同档原生一致（不再清 0）',
   u32(pl, rec['off'] + 0x198) == max(set(_nat198), key=_nat198.count),
   '%#x vs 众数 %#x' % (u32(pl, rec['off'] + 0x198), max(set(_nat198), key=_nat198.count)))
# ★§248（推翻 §243），★§252 再修正：**任务/目标点**清 0，但**当前指令**`+0x3A4/+0x3BC`
#   必须**抄骨架**（全档统一的共享指令对象）。清 0 = "有指令槽、里面是空对象"
#   ⇒ 引擎死亡路径不判空 ⇒ 打掉弹框（§243 当年实测到的正是这条）。
#   T106 实测：同档 98 台原生 DISK 的这两槽**全部**是 0x17B2DC，只有我们的克隆是 0。
for _o, _nm in ((0x298, '任务号'), (0x29C, '目标x'), (0x2A0, '目标y'), (0x2A4, '目标z')):
    ck('B5.%s +0x%03X 已清 0（新单位=原地待命）' % (_nm, _o),
       u32(pl, rec['off'] + _o) == 0, u32(pl, rec['off'] + _o))
for _o, _nm in ((0x3A4, '当前指令'), (0x3BC, '指令副本')):
    _vals = [u32(d.plain, y['off'] + _o) for y in d.unit_records() if y['type'] == TYPE]
    _nv = max(set(_vals), key=_vals.count) if _vals else 0
    ck('B5.%s +0x%03X 抄骨架=同档原生众数（§252，不再清 0）' % (_nm, _o),
       u32(pl, rec['off'] + _o) == _nv,
       '%#x vs 众数 %#x' % (u32(pl, rec['off'] + _o), _nv))
# 对照组：本档原生飞碟确实带着任务（证明"清"是真的清掉了东西）
_nat = [x for x in d.unit_records() if x['type'] == TYPE and x['key'] != info['key']]
ck('B6. 原生同型号确实带任务（对照组有效）',
   any(u32(d.plain, x['off'] + 0x298) != 0 for x in _nat),
   '原生 0x298 全 0')
ck('B7. +0x5EC 野链已清（§245）', u32(pl, rec['off'] + 0x5EC) == 0,
   hex(u32(pl, rec['off'] + 0x5EC)))

# ---- C. 强制最脏骨架也要被清干净（鲁棒性）----
d2 = ec.SaveDoc(SRC)
rs2 = [r for r in d2.unit_records() if r['type'] == TYPE]
dirtiest = max(rs2, key=lambda r: foreign(d2, r))
f_dirty = foreign(d2, dirtiest)
print('  C. 最脏骨架外来指针 %d 个' % f_dirty)
ck('C1. 最脏骨架确实脏（≥100）', f_dirty >= 100, f_dirty)
d2._pick_scaffold = lambda recs, tgt_loco=None, _s=dirtiest: _s
info2 = d2.add_unit_by_clone(TYPE, 110, 206, house=d2.player_house(), veterancy=0, exact_cell=True)
rec2 = [r for r in d2.unit_records() if r['key'] == info2['key']][0]
f2 = foreign(d2, rec2, own=info2['key'])
print('     强制度骨架克隆出来 %d 个（清理 %d）' % (f2, info2['foreign_ptrs']))
ck('C2. 脏骨架也被清到干净区间', f2 <= CLEAN_HI, '%d > %d' % (f2, CLEAN_HI))
ck('C3. 确实清掉了大量（≥100）', info2['foreign_ptrs'] >= 100, info2['foreign_ptrs'])

# ---- D. 换一份档同样成立（地平三V3：原生中位就 91，脏样本不把判据带偏）----
SRC3 = 'E:/YURI/新建文件夹 (30)/平地三V3.SAV'
if os.path.exists(SRC3):
    d3 = ec.SaveDoc(SRC3)
    rs3 = [r for r in d3.unit_records() if r['type'] == TYPE]
    if len(rs3) >= 3:
        nat3 = sorted(foreign(d3, r) for r in rs3)
        hi3 = nat3[0] + 4
        info3 = d3.add_unit_by_clone(TYPE, 30, 182, house=d3.player_house(), veterancy=0, exact_cell=True)
        rec3 = [r for r in d3.unit_records() if r['key'] == info3['key']][0]
        f3 = foreign(d3, rec3, own=info3['key'])
        print('  D. 平地三V3：原生 min=%d 中位=%d；克隆 %d 个（清理 %d）'
              % (nat3[0], nat3[len(nat3) // 2], f3, info3['foreign_ptrs']))
        ck('D1. 平地三V3 的克隆也在干净区间', f3 <= hi3, '%d > %d' % (f3, hi3))
    else:
        print('  D. 跳过（该档 %s 只有 %d 架）' % (TYPE, len(rs3)))
else:
    print('  D. 跳过（找不到 %s）' % SRC3)

# ---- F. ★§247：克隆绝不能继承"标签挂接"（+0x48）----
import bisect as _bs
import collections as _cl


def _attach_ms(doc):
    """每个 CellTag 节点的 (存档计数, 实挂数) 多重集 —— 不按节点偏移比（插入会移位）。"""
    pl = doc.plain
    units = sorted(doc.unit_records(), key=lambda r: r['off'])
    uoffs = [r['off'] for r in units]
    out = _cl.Counter()
    for nd in doc.celltag_nodes():
        X = nd['x']
        if not X:
            continue
        pat = struct.pack('<I', X)
        n = 0
        q = 0
        while True:
            pp = pl.find(pat, q)
            if pp < 0:
                break
            q = pp + 1
            if pp == nd['node'] + 16 or pp + 112 > len(pl):
                continue
            px, py = struct.unpack_from('<II', pl, pp + 104)
            if not (0 < px < doc.MAXPIX and 0 < py < doc.MAXPIX):
                continue
            k = _bs.bisect_right(uoffs, pp) - 1
            if k >= 0 and units[k]['off'] <= pp < units[k]['comp'] + units[k]['comp_size']:
                n += 1
        out[(nd['count'], n)] += 1
    return out


print('=== [F] 标签挂接不变量（§247）===')
d7 = ec.SaveDoc(SRC)
before_ms = _attach_ms(d7)
info7 = d7.add_unit_by_clone(TYPE, 112, 206, house=d7.player_house(), veterancy=0, exact_cell=True)
rec7 = [r for r in d7.unit_records() if r['key'] == info7['key']][0]
after_ms = _attach_ms(d7)
ck('F1. 克隆后所有 CellTag 节点的挂接剖面不变', before_ms == after_ms,
   '变多 %s / 变少 %s' % (dict(after_ms - before_ms), dict(before_ms - after_ms)))
ck('F2. 克隆体的 +0x48 标签字段已清 0', u32(d7.plain, rec7['off'] + 0x48) == 0,
   hex(u32(d7.plain, rec7['off'] + 0x48)))
# 原生带标签的单位这一项非零（证明这个字段真的有语义，不是恒 0）
_tagged = [r for r in d7.unit_records()
           if u32(d7.plain, r['off'] + 0x48) != 0]
ck('F3. 本档确实有带标签挂接的单位（样本有效）', len(_tagged) > 0, len(_tagged))

# ---- E. 落盘往返 ----
p = out('ev246_外来指针清理.SAV')
d.save(p, backup=False)
e = ec.SaveDoc(p)
re_ = [r for r in e.unit_records() if r['key'] == info['key']][0]
ck('E1. 落盘重读后单位还在', len(e.unit_records()) == len(d.unit_records()))
ck('E2. 落盘重读后仍是干净的', foreign(e, re_, own=info['key']) <= CLEAN_HI,
   foreign(e, re_, own=info['key']))
ck('E3. 坐标没变', (re_['x'], re_['y']) == (108, 206), (re_.get('x'), re_.get('y')))
ck('E4. 位置副本槽正常', e.unit_pos_slots(re_)['ok'], e.unit_pos_slots(re_))

print('\n§246 自检：PASS %d / FAIL %d' % (n_ok, n_bad))
sys.exit(1 if n_bad else 0)
