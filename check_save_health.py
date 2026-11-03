#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""存档健康检查 —— 专查「读档必弹框 (0x6CF3DD 除零)」的那几类坏对象。

来历（2026-11-02 实测定案，详见 RA2知识库 topics/07）：
    `木乃伊之咒` sov01umd 里，一辆 YTNK 被 `107 超时空传送` 刷到「可玩区之外」的坦克碉堡
    再执行 `61,0 进驻`，引擎算不出落点 ⇒ 坐标四份拷贝全被写成 0x80（格 (0,0)）、
    格引用留在上一局的 0x0D84xxxx 池 ⇒ 存档带着它，读档键归并直接除零。
    删掉这一辆，读档恢复正常（已实测）。

用法：
    python check_save_health.py <存档.sav>                    # 只体检
    python check_save_health.py <存档.sav> --fix <输出.sav>    # 体检 + 生成修复档
"""
import sys
import os
import io
import struct
import argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.editor_core import SaveDoc

G_CELL = bytes.fromhex('ce99bfc18c1ad2118175006008055bb5')
G_BLD = bytes.fromhex('c62d270e0f9cd111b70900a024ddafd1')

# 「主坐标」两套（相对主体起点）：坏对象这两套会一起被写成 0x80（格 (0,0)）
#   ★实测（2026-11-02）：坏车 +176/+180 与 +0xB0/+0xB4 全是 0x80，而 +0x268/+0x26C 是 0
#   ⇒ 判据只能取这两套，**不能要求 +0x268 也是 0x80**（否则一个都判不出来）
POS_MAIN = (176, 180)          # = +0xB0 / +0xB4，这两处就是主坐标（别再当两组算）
POS_OFFS = POS_MAIN + (374, 384, 0x268, 0x26C)   # = +0x176/+0x180，坏对象这两处是 0
CELL_PTR_OFFS = (224, 248, 1320, 1672)


def cell_pool(doc):
    """本档所有格对象（348B 那种）的自键集合。"""
    pl = doc.plain
    pool, p = set(), 0
    while True:
        p = pl.find(G_CELL, p)
        if p < 0:
            break
        pool.add(struct.unpack_from('<I', pl, p + 16)[0])
        p += 1
    return pool


def scan_units(doc, pool):
    """返回两类问题单位：dead_pos（坐标全 0x80）/ bad_cell（格键不在池里）。"""
    pl = doc.plain
    dead, bad = [], []
    for rec in doc.unit_records():
        off, size = rec['off'], rec['size']
        if off + size > len(pl):
            continue
        pos = [struct.unpack_from('<I', pl, off + o)[0] for o in POS_MAIN if off + o + 4 <= len(pl)]
        all_80 = bool(pos) and all(v == 0x80 for v in pos)
        cell = rec.get('cell')
        cell_ok = cell in pool
        stale = []
        for o in CELL_PTR_OFFS:
            if off + o + 4 > len(pl):
                continue
            v = struct.unpack_from('<I', pl, off + o)[0]
            if v and v not in pool:
                stale.append((o, v))
        item = {'rec': rec, 'type': rec['type'], 'key': rec['key'],
                'x': rec.get('x'), 'y': rec.get('y'),
                'pos_all_80': all_80, 'cell': cell, 'cell_ok': cell_ok, 'stale': stale}
        if all_80:
            dead.append(item)
        elif not cell_ok:
            bad.append(item)
    return dead, bad


def scan_garrison(doc):
    """脏驻军槽：建筑 +1836(flag)==0 但 +1848(驻军键)!=0。"""
    pl = doc.plain
    key2name = {}
    for nm, k in doc.building_types().items():
        key2name.setdefault(k, nm)
    out, p = [], 0
    while True:
        p = pl.find(G_BLD, p)
        if p < 0:
            break
        base = p - 1788
        if key2name.get(struct.unpack_from('<I', pl, base + 0x4EC)[0]):
            s = base - 72
            flag = struct.unpack_from('<I', pl, s + 1836)[0]
            key = struct.unpack_from('<I', pl, s + 1848)[0]
            if flag == 0 and key != 0:
                px, py = struct.unpack_from('<II', pl, base + 104)
                out.append({'off': s + 1848, 'key': key,
                            'x': (px - 128) // 256 if px >= 128 else None,
                            'y': (py - 128) // 256 if py >= 128 else None})
        p += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sav')
    ap.add_argument('--fix', metavar='OUT', help='生成修复档到 OUT')
    a = ap.parse_args()

    doc = SaveDoc(a.sav)
    pool = cell_pool(doc)
    print('存档: %s' % a.sav)
    print('  明文 %d B   单位 %d   格对象 %d' % (len(doc.plain), len(doc.unit_records()), len(pool)))
    print()

    dead, bad = scan_units(doc, pool)
    dirty = scan_garrison(doc)

    print('① 高危：「主坐标 +0xB0/+0xB4 全 0x80（格 0,0）」的单位 %d 个' % len(dead))
    for it in dead:
        print('     %-8s key=0x%08X  off=0x%08X  cell=0x%08X(无效)  旧池引用 %s'
              % (it['type'], it['key'], it['rec']['off'], it['cell'],
                 ['+%d:0x%08X' % (o, v) for o, v in it['stale']] or '无'))
    print()
    rate = 1.0 - (len(bad) + len(dead)) / max(1, len(doc.unit_records()))
    if rate < 0.5:
        print('② 可疑：跳过（本档格对象布局与判据不匹配，命中率仅 %.0f%%，不具判别力）' % (rate * 100))
        bad = []
    else:
        print('② 可疑：格键无效但坐标正常的单位 %d 个（多半无害，仅提示）' % len(bad))
    for it in bad[:10]:
        print('     %-8s key=0x%08X 格(%s,%s) cell=0x%08X'
              % (it['type'], it['key'], it['x'], it['y'], it['cell']))
    if len(bad) > 10:
        print('     ... 还有 %d 个' % (len(bad) - 10))
    print()
    print('③ 脏驻军槽（建筑 flag=0 但驻军键非0）%d 条' % len(dirty))
    for it in dirty:
        print('     格(%s,%s) 驻军键=0x%08X @0x%08X' % (it['x'], it['y'], it['key'], it['off']))

    if not a.fix:
        if dead:
            print('\n★发现高危坏单位，读档会弹 Internal error。加 --fix <输出.sav> 生成修复档。')
        return

    # ---- 修复：清脏槽 → 删高危单位 ----
    d2 = SaveDoc(a.sav)
    pl = d2.plain
    dirty2 = scan_garrison(d2)
    for it in dirty2:
        if struct.unpack_from('<I', pl, it['off'])[0] == it['key']:
            struct.pack_into('<I', pl, it['off'], 0)
    d2.plain = pl

    removed = []
    for it in dead:
        # 删之前重新取记录（前面删过会让偏移前移）
        cur = [r for r in d2.unit_records() if r['key'] == it['key']]
        if not cur:
            continue
        try:
            res = d2.remove_unit(cur[0], clear_cell=True, clear_foreign=True)
            removed.append((it['type'], it['key'], res.get('delta')))
        except Exception as e:
            print('  ★删除 0x%08X 被拒: %s' % (it['key'], e))

    info = d2.save(a.fix, backup=False)
    print()
    print('修复档 -> %s' % a.fix)
    print('  清脏槽 %d 条，删坏单位 %d 个 %s' % (len(dirty2), len(removed),
                                          ['%s@0x%08X' % (t, k) for t, k, _ in removed] or ''))
    print('  块 %s/%s  writer=%s  md5=%s  单位剩 %d'
          % (info.get('blocks'), info.get('blocks_expect'), info.get('writer'),
             info.get('md5'), len(d2.unit_records())))


if __name__ == '__main__':
    main()
