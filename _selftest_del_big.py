"""_selftest_del_big.py —— 用产品代码（SaveDoc.remove_building）在大图上删建筑，并对照引擎档验证。

用法: python _selftest_del_big.py [基档] [x] [y]
默认：C:\\Users\\zhao5\\Desktop\\未卖电厂.SAV 删 (215,291)
"""
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
import _bare  # noqa: E402

G = bytes.fromhex('ce99bfc18c1ad2118175006008055bb5')
S = 348


def refs(pl, k):
    pat, out, i = struct.pack('<I', k), [], pl.find(struct.pack('<I', k))
    while i >= 0:
        out.append(i)
        i = pl.find(pat, i + 1)
    return out


def cells_of(pl, k):
    first = pl.find(G)
    n = (pl.rfind(G) - first) // S + 1
    out = []
    for i in range(n):
        s = first + i * S
        if struct.unpack_from('<I', pl, s + 0xF8)[0] == k:
            out.append((struct.unpack_from('<HH', pl, s + 0x38), s))
    return out


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\zhao5\Desktop\未卖电厂.SAV'
    x = int(sys.argv[2]) if len(sys.argv) > 2 else 215
    y = int(sys.argv[3]) if len(sys.argv) > 3 else 291

    d = _bare.bare(src)
    pl = bytes(d.plain)
    hs, _ = d._bld_group()
    n0 = len(hs)
    b = next(q for q in d.buildings() if (q['x'], q['y']) == (x, y))
    g = b['base'] - 72
    idx = hs.index(g)
    key = struct.unpack_from('<I', pl, g + 16)[0]
    cks = [struct.unpack_from('<I', pl, g + 16 + o)[0] for o in (1388, 1392, 1396, 1400)]
    cks = [c for c in cks if c]
    print('基档 %s' % src)
    print('目标 第%d栋 %s @(%d,%d) key=%#010x 伴随=%s' % (idx, b['type'], x, y, key, [hex(c) for c in cks]))
    print('删前: 建筑记录 %d  建筑键引用 %d  占格 %d' % (n0, len(refs(pl, key)), len(cells_of(pl, key))))

    tmp = os.path.join(tempfile.gettempdir(), 'del_big_in.SAV')
    shutil.copyfile(src, tmp)
    t = _bare.bare(tmp)
    out = t.remove_building(idx)
    print('remove_building →', {k: v for k, v in out.items() if k != 'note'})
    dst = os.path.join(tempfile.gettempdir(), 'del_big_out.SAV')
    info = t.save(dst)
    print('已写出 %s  块 %d/%d %s' % (dst, info['blocks'], info['blocks_expect'], info['writer']))

    e = _bare.bare(dst)
    pe = bytes(e.plain)
    hs2, _ = e._bld_group()
    print('\n=== 回验 ===')
    print('建筑记录 %d → %d' % (n0, len(hs2)))
    print('建筑键引用 %d → %d %s' % (len(refs(pl, key)), len(refs(pe, key)), [hex(p) for p in refs(pe, key)]))
    print('占格 %d → %d' % (len(cells_of(pl, key)), len(cells_of(pe, key))))
    for c in cks:
        print('伴随键 %#x 引用 %d → %d' % (c, len(refs(pl, c)), len(refs(pe, c))))
    # 引擎档对照
    eng = r'C:\Users\zhao5\Desktop\卖掉电厂.SAV'
    if os.path.exists(eng):
        pb = _bare.bare(eng).plain
        pb = bytes(pb)
        print('引擎档对照: 建筑键引用 %d  伴随键引用 %s'
              % (len(refs(pb, key)), [len(refs(pb, c)) for c in cks]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
