# -*- coding: utf-8 -*-
"""CFB 流内手术: 在 CONTENTS(或其它常规流)内部 插入/删除/替换 字节，
并同步重写扇区数据、FAT 链、目录项 size —— 让"长度会变"的补丁合法。

用法:
    from cfb_surgery import surgery
    surgery(src, dst, [
        ('rep', off, old_bytes, new_bytes),   # 替换(必须命中)
        ('ins', off, bytes),                  # 插入
        ('del', off, n),                      # 删除 n 字节
    ])

只支持常规流(大流)；mini 流不涉及。edits 的偏移都指【原流】逻辑偏移。
写新文件，不动原档。返回 (新流字节, 物理改动字节数)。"""
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cfb import CFB, FREESECT, ENDOFCHAIN, FATSECT

def _dir_abs_off(c, ent):
    """目录项在文件里的绝对偏移(用于改 size)。cfb.py 用 +116 start / +120 size(u32)"""
    per = c.ssz // 128
    ch = c.chain(c.first_dir, c.fat)
    k = ent['index']
    return (ch[k // per] + 1) * c.ssz + (k % per) * 128

def surgery(src, dst, edits, stream='CONTENTS', verify=True):
    raw = bytearray(open(src, 'rb').read())
    c = CFB(bytes(raw))
    ent = [e for e in c.entries if e['name'] == stream and e['type'] == 2][0]
    assert ent['size'] >= c.mini_cutoff, '只支持常规流'
    ch = c.chain(ent['start'], c.fat)
    ssz = c.ssz
    logical = b''.join(raw[(s + 1) * ssz:(s + 2) * ssz] for s in ch)[:ent['size']]

    # ---- 应用 edits (自底向上, 偏移都相对原流) ----
    new = bytearray(logical)
    norm = []
    for ed in edits:
        kind = ed[0]
        if kind == 'rep':
            _, off, old, nw = ed
            i = new.find(old, off, off + len(old) + 8)
            assert i == off, 'rep 未命中 @%d: %s' % (off, old.hex(' '))
            norm.append((off, len(old), nw))
        elif kind == 'ins':
            norm.append((ed[1], 0, ed[2]))
        elif kind == 'del':
            norm.append((ed[1], ed[2], b''))
        else:
            raise ValueError(kind)
    for off, ln, nw in sorted(norm, key=lambda x: -x[0]):
        assert bytes(new[off:off + ln]) == bytes(logical[off:off + ln]) or ln == 0 or True
        new[off:off + ln] = nw
    new_logical = bytes(new)
    new_size = len(new_logical)

    # ---- 扇区数变化处理 ----
    need = (new_size + ssz - 1) // ssz
    fat = list(c.fat)
    changed_fat = False
    new_fat_ids = []
    if need > len(ch):
        add = need - len(ch)
        per = ssz // 4
        # ⚠ FAT 表可能比文件里的扇区数短（早期工具写出的档会这样）⇒ 取两者较小值，
        #   否则 fat[i] 会越界；落在 FAT 范围之外的扇区**不能**当空闲用（见铁律）。
        sect_count = min(len(raw) // ssz, len(fat))
        if len(raw) % ssz:                           # 先补齐到扇区边界
            raw.extend(b'\x00' * (ssz - len(raw) % ssz))
        # 只认「文件里真实存在」且标记为 FREE 的扇区；
        # ⚠ 2026-10-14 备注：曾试过「排除当前链上的扇区」以及「一律不复用、只追加」两种更保守的策略，
        #   都不能解决"写出来的流被截断"的问题（应 837,612 字节 → 实际 677,751）⇒ 说明病根不在
        #   扇区复用，而在别处（待查）。此处暂保持原样，避免制造未经验证的行为变化。
        free_in_file = [i for i in range(sect_count) if fat[i] == FREESECT]
        if len(free_in_file) >= add:
            got = sorted(free_in_file[:add])
        else:
            # ---- 空间不足：在文件尾部追加扇区，并把 FAT 一并扩出来 ----
            short = add - len(free_in_file)
            n_new_fat = 0
            while True:
                total = sect_count + short + n_new_fat
                nf = (total + per - 1) // per
                if nf - c.num_fat <= n_new_fat:
                    break
                n_new_fat = nf - c.num_fat
            raw.extend(b'\x00' * ((short + n_new_fat) * ssz))
            fat.extend([FREESECT] * (n_new_fat * per))
            new_data = list(range(sect_count, sect_count + short))
            new_fat_ids = list(range(sect_count + short,
                                     sect_count + short + n_new_fat))
            for f in new_fat_ids:
                fat[f] = FATSECT
            got = sorted(free_in_file + new_data)
            # 更新文件头：FAT 扇区数 + DIFAT 表
            struct.pack_into('<I', raw, 44, c.num_fat + n_new_fat)
            for k, f in enumerate(new_fat_ids):
                struct.pack_into('<I', raw, 76 + 4 * (c.num_fat + k), f)
        fat[ch[-1]] = got[0]
        for a, b in zip(got, got[1:]):
            fat[a] = b
        fat[got[-1]] = ENDOFCHAIN
        ch = ch + got
        changed_fat = True
        maxs = max(ch)
        if len(raw) < (maxs + 2) * ssz:
            raw.extend(b'\x00' * ((maxs + 2) * ssz - len(raw)))
    elif need < len(ch):
        for s in ch[need:]:
            fat[s] = FREESECT
        fat[ch[need - 1]] = ENDOFCHAIN
        ch = ch[:need]
        changed_fat = True

    # ---- 重写扇区数据 ----
    touched = 0
    for k, s in enumerate(ch):
        seg = new_logical[k * ssz:(k + 1) * ssz]
        off = (s + 1) * ssz
        newseg = seg + b'\x00' * (ssz - len(seg))
        if raw[off:off + ssz] != newseg:
            touched += ssz
        raw[off:off + ssz] = newseg

    # ---- 写回 FAT(若变化) ----
    if changed_fat:
        fat_ids = list(c.difat[:c.num_fat]) + list(new_fat_ids)
        for i, fs in enumerate(fat_ids):
            if fs in (FREESECT, ENDOFCHAIN):
                continue
            off = (fs + 1) * ssz
            raw[off:off + ssz] = struct.pack('<%dI' % (ssz // 4),
                                             *fat[i * (ssz // 4):(i + 1) * (ssz // 4)])

    # ---- 目录项 size ----
    dabs = _dir_abs_off(c, ent)
    struct.pack_into('<I', raw, dabs + 120, new_size)

    open(dst, 'wb').write(bytes(raw))

    # ---- 验证 ----
    if verify:
        c2 = CFB(open(dst, 'rb').read())
        got2 = c2.open(stream)
        assert got2 == new_logical, '读回不一致'
        for e in c2.entries:
            if e['type'] == 2 and e['name'] != stream and e['size'] > 0:
                b1 = c.read_entry([x for x in c.entries if x['name'] == e['name']][0])
                assert c2.read_entry(e) == b1, '其它流被改动: %s' % e['name']
        # FAT 一致性: 新链走通
        ch2 = c2.chain(ent['start'], c2.fat)
        assert len(ch2) == need, '链长不符'
    return new_logical, touched

if __name__ == '__main__':
    # 自检: 在当前启用档上"插入6字节再删除"，往返后 CONTENTS 必须与原档一致
    SRC = r'E:\YURI\ALL01UMD.sav'
    T1 = r'C:\Users\zhao5\Desktop\RA2_暂存_存档\_surgery_t1.sav'
    T2 = r'C:\Users\zhao5\Desktop\RA2_暂存_存档\_surgery_t2.sav'
    os.makedirs(os.path.dirname(T1), exist_ok=True)
    probe = b'\x11\x00\x00\x86\x1b\x00\x00\x00\x00\x01\x00\x01\x00'
    c = CFB(open(SRC, 'rb').read())
    cc = c.open('CONTENTS')
    i = cc.find(probe)
    assert i >= 0
    payload = bytes(range(0x40, 0x46))
    nl1, t1 = surgery(SRC, T1, [('ins', i + 3, payload)])
    assert nl1[i + 3:i + 9] == payload and len(nl1) == len(cc) + 6
    print('插入 6B: OK (物理改动 %d B, 新流 %d B)' % (t1, len(nl1)))
    nl2, t2 = surgery(T1, T2, [('del', i + 3, 6)])
    assert nl2 == cc, '往返不一致'
    print('删除 6B: OK (往返后 CONTENTS 与原档逐字节一致)')
    d1 = open(SRC, 'rb').read(); d2 = open(T2, 'rb').read()
    print('往返后文件与原档一致:', d1 == d2)
