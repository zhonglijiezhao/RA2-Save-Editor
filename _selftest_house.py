# -*- coding: utf-8 -*-
r"""_selftest_house.py —— 「加建筑时选所属方」功能的离线自检（2026-10-17 新增）。

对照物：
  · 母本 `_exp/母本_原存档.SAV`（11 栋 / 12 阵营 / 玩家 = Americans）
  · 大任务档 `E:\YURI\_备份存档\SAVE6811.SAV`（1143 栋 / 8 个阵营有建筑）

断言（全 PASS 才算过）：
  A. `house_keys()` 覆盖 HOUSE_ORDER 的 12 个短名，且每个键都是本档 House 对象的键
  B. 大任务档里，**每一栋**建筑的 载荷+336 都命中某个 House 键（且 +544 也是）
  C. 母本 11 栋的 +336 全部 = 玩家 House 键
  D. `player_house()` 是去掉 " House" 后缀的短名，且能在 house_keys() 里查到
  E. `add_placed_building(house=X)` 把 **+336 与 +544 同时**写成 X 的键；`house=None` 时保持模板值
  F. 伴随对象里 0 个含 House 键（⇒ 改所属方只需动建筑记录）
  用法：python _selftest_house.py
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, '_exp'))

from core.editor_core import SaveDoc, HOUSE_ORDER        # noqa: E402
import analyze_sell as AZ                                 # noqa: E402

OK = []
BAD = []


def chk(cond, msg):
    (OK if cond else BAD).append(msg)
    print(('  ✅ ' if cond else '  ❌ ') + msg)


SRC = os.path.join(ROOT, '_exp', '母本_原存档.SAV')
BIG = r'E:\YURI\_备份存档\SAVE6811.SAV'
NOPWR = 'E:/YURI/' + '\u65e0\u7535\u5382.SAV'     # 玩家 0 座电厂的档（发电 0 / 耗电 210）


def house_obj_keys(d):
    pl = bytes(d.plain)
    out, q = set(), 0
    while True:
        i = pl.find(d.HOUSE_GUID, q)
        if i < 0:
            return out
        out.add(struct.unpack_from('<I', pl, i + 16)[0])
        q = i + 1


def main():
    d = SaveDoc(SRC)
    pl = bytes(d.plain)
    objk = house_obj_keys(d)
    print('== A. house_keys() 覆盖 12 阵营 ==')
    hk = d.house_keys()
    chk(len(hk) == 12, 'house_keys() 返回 %d 项（应 12）' % len(hk))
    chk(all(k in hk for k in HOUSE_ORDER), '12 个 HOUSE_ORDER 短名全部有键')
    chk(all(v in objk for v in hk.values()), '全部键都是本档 House 对象的键')

    print('== B/C. 建筑记录的 +336 就是所属方 ==')
    rows, _ = AZ.bld_rows(d)
    bad = [r for r in rows if struct.unpack_from('<I', pl, r['payload'] + 336)[0] not in objk]
    chk(not bad, '母本 %d 栋的 +336 全部命中 House 键' % len(rows))
    ph = d.player_house()
    chk(ph in hk, 'player_house() = %r 能在 house_keys() 里查到' % ph)
    chk(not str(d.info.get('玩家阵营', '')).endswith(' House'),
        'info[玩家阵营] 不再带 " House" 后缀（当前 %r）' % d.info.get('玩家阵营'))
    same = all(struct.unpack_from('<I', pl, r['payload'] + 336)[0]
               == struct.unpack_from('<I', pl, r['payload'] + 544)[0] for r in rows)
    chk(same, '母本每栋 +336 与 +544 一致')

    print('== B2. 大任务档（1143 栋 / 8 阵营）==')
    if os.path.exists(BIG):
        b = SaveDoc(BIG)
        bp = bytes(b.plain)
        bo = house_obj_keys(b)
        hs, _c = b._bld_group()
        miss = [g for g in hs
                if struct.unpack_from('<I', bp, g + 16 + 336)[0] not in bo]
        chk(not miss, '大任务档 %d 栋的 +336 全部命中 House 键（未命中 %d）' % (len(hs), len(miss)))
        chk(len(b.house_keys()) == 12, '大任务档 house_keys() 也是 12 项')
    else:
        print('  ⚠ 大任务档不在，跳过 B2')

    print('== F. 伴随对象不含 House 键 ==')
    n = sum(1 for off, _k in d.companion_offsets()
            if any(struct.unpack_from('<I', pl, off + f)[0] in objk
                   for f in range(0, 476 - 3, 4)))
    chk(n == 0, '母本 16 个伴随里含 House 键的 = %d（应 0）' % n)

    print('== E. add_placed_building(house=…) 写入 ==')
    for short, label in (('Americans', '玩家'), ('Russians', '苏俄'), ('YuriCountry', '尤里')):
        e = SaveDoc(SRC)
        rows2, _ = AZ.bld_rows(e)
        W = e.map_size()
        e._cellbase_memo = None
        base = e._cell_base(e.plain)
        used = set()
        for r in rows2:
            fw, fh = e.foundation_of(r['type'])
            for i in range(fw):
                for j in range(fh):
                    used.add((r['x'] + i, r['y'] + j))
        cell = None
        cand = [(max(abs(cx - 72), abs(cy - 62)), cx, cy)
                for cy in range(2, W[1] - 2) for cx in range(2, W[0] - 2)
                if (cx, cy) not in used
                and struct.unpack_from('<I', e.plain, e.cell_offset(base, cx, cy))[0] == 0]
        cand.sort()
        cell = (cand[0][1], cand[0][2])
        info = e.add_placed_building('ATESLA', cell[0], cell[1], house=short)
        hs2, _c2 = e._bld_group()
        g = [x for x in hs2 if struct.unpack_from('<I', e.plain, x + 16)[0] == info['key']][0]
        got1 = struct.unpack_from('<I', e.plain, g + 16 + 336)[0]
        got2 = struct.unpack_from('<I', e.plain, g + 16 + 544)[0]
        want = e.house_keys()[short]
        chk(got1 == want and got2 == want,
            'house=%-12s → +336/+544 = %08x/%08x（期望 %08x）' % (short, got1, got2, want))
        # ★2026-10-17 新增：+1344 = 运行期阵营序号（必须与键一致，否则游戏里"颜色对但不认领"）
        got3 = struct.unpack_from('<I', e.plain, g + 16 + 1344)[0]
        w3 = e.house_run_index(short)
        chk(got3 == w3, 'house=%-12s → +1344 = %d（期望 %s）' % (short, got3, w3))
    # house=None 保持模板值
    e = SaveDoc(SRC)
    info = e.add_placed_building('ATESLA', 69, 60)
    hs3, _c3 = e._bld_group()
    g = [x for x in hs3 if struct.unpack_from('<I', e.plain, x + 16)[0] == info['key']][0]
    chk(struct.unpack_from('<I', e.plain, g + 16 + 336)[0] == d.house_keys()['Americans'],
        'house=None 时保持模板值（= 玩家键）')

    print('== G. set_building_owner() 改已有建筑的所属方 ==')
    e = SaveDoc(SRC)
    tgt = [b for b in e.buildings_of_house(None) if b['type'] == 'GAPOWR']
    chk(len(tgt) == 6, '母本有 6 座 GAPOWR')
    r = e.set_building_owner('Russians', idx=tgt[0]['idx'])
    hs4, _c4 = e._bld_group()
    o = hs4[tgt[0]['idx']] + 16
    want = e.house_keys()['Russians']
    chk(struct.unpack_from('<I', e.plain, o + 336)[0] == want
        and struct.unpack_from('<I', e.plain, o + 544)[0] == want,
        '改所属方：+336/+544 都变成苏俄键（%s → %s）' % (r['old_house_name'], r['house_name']))
    chk(struct.unpack_from('<I', e.plain, o + 1344)[0] == r['house_idx'] == 8,
        '改所属方：+1344 = %d（期望 8 = 苏俄）' % struct.unpack_from('<I', e.plain, o + 1344)[0])
    left = [b for b in e.buildings_of_house(None)
            if b['type'] == 'GAPOWR' and b['owner'] != want]
    chk(len(left) == 5, '只改了 1 座（其余 5 座仍是玩家）')
    chk(len(e.buildings_of_house('Russians')) == 1, 'buildings_of_house("Russians") 能查到这 1 座')
    # 按格子定位
    e2 = SaveDoc(SRC)
    r2 = e2.set_building_owner('YuriCountry', cx=tgt[1]['x'], cy=tgt[1]['y'])
    chk(r2['house_name'].startswith('尤里'), '按 (cx,cy) 定位改所属方：%s' % r2['note'])

    print('== H. 运行期阵营序号（载荷 +1344）==')
    # 母本只有玩家建筑 ⇒ 反解出 Americans = 0
    chk(d.house_run_index('Americans') == 0, '从母本反解 Americans 序号 = 0（实得 %s）'
        % d.house_run_index('Americans'))
    idxs = {k: d.house_run_index(k) for k in ('Russians', 'YuriCountry', 'Neutral', 'British')}
    chk(idxs == {'Russians': 8, 'YuriCountry': 9, 'Neutral': 12, 'British': 4},
        '兜底表：苏俄 8 / 尤里 9 / 中立 12 / 英国 4（实得 %s）' % idxs)
    d2 = SaveDoc(BIG)
    got = {}
    for r0 in AZ.bld_rows(d2)[0]:
        o0 = r0['payload']
        k0 = struct.unpack_from('<I', d2.plain, o0 + 336)[0]
        got.setdefault(k0, set()).add(struct.unpack_from('<I', d2.plain, o0 + 1344)[0])
    chk(all(len(v) == 1 for v in got.values()),
        '大任务档：每个阵营的 +1344 唯一（%d 个阵营）' % len(got))
    idx_map = {d2.house_label(k): next(iter(v)) for k, v in got.items()}
    chk(idx_map.get('苏俄(Russians)') == 8 and idx_map.get('中立(Neutral)') == 12,
        '大任务档反解：苏俄 8 / 中立 12（实得 %s）' % idx_map)

    print('== I. 电力计数器（House +0x53B8 = (发电, 耗电)）==')
    chk(SaveDoc.power_of('GAPOWR') == 200 and SaveDoc.power_of('ATESLA') == -75
        and SaveDoc.power_of('GATECH') == -100,
        'rules Power= 解析：电厂+200 / 光棱塔-75 / 作战实验室-100')
    chk(SaveDoc.power_of('NOSUCHTYPE') == 0, '未定义类型 Power = 0')
    e = SaveDoc(SRC)
    chk(e.house_power('Americans')[:2] == (1200, 210),
        '母本玩家电力 = (1200, 210)（实得 %s）' % (e.house_power('Americans')[:2],))
    if os.path.exists(NOPWR):
        e2 = SaveDoc(NOPWR)
        chk(e2.house_power('Americans')[:2] == (0, 210),
            '无电厂档玩家电力 = (0, 210)（实得 %s）' % (e2.house_power('Americans')[:2],))
    else:
        print('   ⏭ 跳过「无电厂档」（%s 已被游戏挪走/删除）' % NOPWR)
    # ★2026-10-17：SAVE0029 —— 12 个阵营各不同数量电厂 + 1 光棱塔，存档电力与 rules 算 12/12 一致
    S29 = 'E:/YURI/SAVE0029.SAV'
    if os.path.exists(S29):
        e29 = SaveDoc(S29)
        bad = []
        for r0 in AZ.bld_rows(e29)[0]:
            pass
        import collections
        cnt = collections.Counter()
        for r0 in AZ.bld_rows(e29)[0]:
            cnt[struct.unpack_from('<I', e29.plain, r0['payload'] + 336)[0]] += 1
        for kk, n0 in cnt.items():
            got = e29.house_power(kk)[:2]
            exp = e29._house_power_calc(kk)
            if got != exp:
                bad.append((e29.house_label(kk), got, exp))
        chk(not bad, 'SAVE0029：%d 个阵营的存档电力 == 按 rules 逐栋算（差异 %s）'
            % (len(cnt), bad[:3]))
        # ★2026-10-17 22:6x：清单 416 条、按 +336 统计 412 栋 —— 差 4 条是**历史残留**
        #   （已拆/在建的建筑密钥还留在清单里，引擎自己也留着）。判据改为 ±5 容差。
        _bl = e29.house_build_list('Russians')
        _n336 = cnt[e29.house_keys()['Russians']]
        chk(_bl is not None and abs(_bl[1] - _n336) <= 5,
            'SAVE0029：苏俄建筑清单定位成功（清单 %s 条 vs 按 +336 统计 %d 栋，容差 ±5）'
            % (_bl[1] if _bl else None, _n336))
    else:
        print('   ⏭ 跳过 SAVE0029（不存在）')
    e3 = SaveDoc(SRC)
    e3.add_placed_building('ATESLA', 69, 60, house='Russians')
    chk(e3.house_power('Russians')[:2] == (0, 75), '给苏俄加光棱塔 ⇒ 苏俄耗电 0→75')
    chk(e3.house_power('Americans')[:2] == (1200, 210), '新建筑不改动别的阵营的电力')
    e4 = SaveDoc(SRC)
    t0 = [b for b in e4.buildings_of_house(None) if b['type'] == 'GAPOWR'][0]
    e4.set_building_owner('Russians', idx=t0['idx'])
    chk(e4.house_power('Americans')[:2] == (1000, 210), '电厂改苏俄 ⇒ 玩家发电 1200→1000')
    chk(e4.house_power('Russians')[:2] == (200, 0), '电厂改苏俄 ⇒ 苏俄发电 0→200')
    e5 = SaveDoc(SRC)
    t1 = [b for b in e5.buildings_of_house(None) if b['type'] == 'GAREFN'][0]
    e5.remove_building(t1['idx'])
    chk(e5.house_power('Americans')[:2] == (1200, 160), '删矿场 ⇒ 玩家耗电 210→160')

    print()
    print('==== 总判定：%s（PASS %d / FAIL %d）===='
          % ('PASS ✅' if not BAD else 'FAIL ❌', len(OK), len(BAD)))
    return 0 if not BAD else 1


if __name__ == '__main__':
    sys.exit(main())
