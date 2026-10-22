# -*- coding: utf-8 -*-
r"""CFB 写入器（**从零重建**，2026-10-14 新增）

为什么要它：`cfb_surgery` 走的是"在旧文件里原位改扇区/链/FAT"的路子，遇到旧档 FAT 不干净时
会把写出来的流截断（实测：应 837,612 字节 → 实际 677,751，本工具自己都读不回来）。
这里换个思路：**把源文件里的所有流读出来，按标准 CFB 布局重新排一遍写出去** ——
FAT/目录/mini 流全部新建，不再有任何"旧档遗留状态"可以咬人。

保留源档的：目录项顺序、名字、类型、红黑树指针（= 已知可用的目录结构）。
重建的：每个流的 start/size、mini 流内容、mini-FAT、FAT、DIFAT、头部字段。

用法：
    from cfb_write import rebuild
    rebuild(src_path, {'CONTENTS': new_bytes}, out_path)
未在 dict 里给出的流：原样搬运。
"""
import struct

SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
NOSTREAM = 0xFFFFFFFF
MINI_CUTOFF = 4096
SSZ = 512
MSZ = 64


def _read_entry(raw, i):
    e = raw[i:i + 128]
    nlen = struct.unpack_from('<H', e, 64)[0]
    return {
        'index': i // 128,
        'raw': bytes(e),
        'name': e[:max(0, nlen - 2)].decode('utf-16-le', 'replace'),
        'nlen': nlen,
        'type': e[66],
        'start': struct.unpack_from('<I', e, 116)[0],
        'size': struct.unpack_from('<I', e, 120)[0],
    }


def _chain_entries(fat, start, seen=None):
    out, sid = [], start
    seen = seen if seen is not None else set()
    while sid not in (ENDOFCHAIN, FREESECT) and sid < len(fat) and sid not in seen:
        seen.add(sid)
        out.append(sid)
        sid = fat[sid]
    return out


def load_streams(src_path):
    """返回 (原始文件, 目录项列表, {名字: 数据})"""
    raw = open(src_path, 'rb').read()
    if raw[:8] != SIGNATURE:
        raise ValueError('不是 CFB')
    ssz = 1 << struct.unpack_from('<H', raw, 30)[0]
    num_fat = struct.unpack_from('<I', raw, 44)[0]
    first_dir = struct.unpack_from('<I', raw, 48)[0]
    mini_cutoff = struct.unpack_from('<I', raw, 56)[0]
    first_minifat = struct.unpack_from('<I', raw, 60)[0]
    difat = list(struct.unpack_from('<109I', raw, 76))
    fat_ids = [s for s in difat[:num_fat] if s not in (FREESECT, ENDOFCHAIN)]
    fat = []
    for s in fat_ids:
        off = (s + 1) * ssz
        fat.extend(struct.unpack_from('<%dI' % (ssz // 4), raw, off))
    dirraw = b''.join(raw[(s + 1) * ssz:(s + 2) * ssz]
                      for s in _chain_entries(fat, first_dir))
    entries = [_read_entry(dirraw, i) for i in range(0, len(dirraw) - 127, 128)]
    # mini 流（挂在 Root Entry 上）
    minifat = []
    if first_minifat not in (FREESECT, ENDOFCHAIN):
        mraw = b''.join(raw[(s + 1) * ssz:(s + 2) * ssz]
                        for s in _chain_entries(fat, first_minifat))
        minifat = list(struct.unpack_from('<%dI' % (len(mraw) // 4), mraw, 0))
    mini = b''
    if entries:
        r = entries[0]
        if r['size'] > 0 and r['start'] not in (ENDOFCHAIN, FREESECT):
            # mini 流本身是一条**常规扇区链**（挂在 Root Entry 的 start/size 上）
            mini = b''.join(raw[(s + 1) * ssz:(s + 2) * ssz]
                            for s in _chain_entries(fat, r['start']))[:r['size']]
    streams = {}
    for e in entries:
        if e['type'] != 2 or e['size'] == 0:
            continue
        if e['size'] < mini_cutoff:
            o = e['start'] * MSZ
            streams[e['name']] = mini[o:o + e['size']]
        else:
            streams[e['name']] = b''.join(
                raw[(s + 1) * ssz:(s + 2) * ssz]
                for s in _chain_entries(fat, e['start']))[:e['size']]
    return raw, entries, streams


def rebuild(src_path, new_streams, out_path):
    """把 src 的所有流（其中 new_streams 里的换成新数据）按标准布局重写成 out"""
    raw, entries, streams = load_streams(src_path)
    streams = dict(streams)
    streams.update(new_streams)

    # ---- 1. 分流：小流进 mini 流，大流用常规扇区 ----
    small, big = [], []
    for e in entries:
        if e['type'] != 2 or e['size'] == 0 or e['name'] not in streams:
            continue
        (small if len(streams[e['name']]) < MINI_CUTOFF else big).append(e['name'])
    mini_data = bytearray()
    mini_start = {}
    for nm in small:
        d = streams[nm]
        mini_start[nm] = len(mini_data) // MSZ
        mini_data += d
        while len(mini_data) % MSZ:
            mini_data.append(0)
    mini_nsec = len(mini_data) // MSZ
    minifat = [FREESECT] * mini_nsec
    for nm in small:
        n = (len(streams[nm]) + MSZ - 1) // MSZ
        s0 = mini_start[nm]
        for k in range(n - 1):
            minifat[s0 + k] = s0 + k + 1
        if n:
            minifat[s0 + n - 1] = ENDOFCHAIN

    # ---- 2. 排扇区 ----
    layout = []                     # (name|kind, n_sectors)
    for nm in big:
        layout.append((nm, (len(streams[nm]) + SSZ - 1) // SSZ))
    n_mini_sec = (len(mini_data) + SSZ - 1) // SSZ
    layout.append(('__mini__', n_mini_sec))
    n_minifat_sec = (len(minifat) * 4 + SSZ - 1) // SSZ if minifat else 0
    layout.append(('__minifat__', n_minifat_sec))
    dirsz = ((len(entries) * 128) + SSZ - 1) // SSZ
    layout.append(('__dir__', dirsz))
    # FAT 扇区数需自洽：先估，再定
    # ★2026-10-17 23:4x：**FAT 超过 109 个时必须写 DIFAT 扇区**。
    #   头部只有 109 个槽放 FAT 扇区号；超过就得用 DIFAT 链（每扇区 127 个 + 下一跳）。
    #   旧写法只往头 109 槽写、`first_difat=ENDOFCHAIN` ⇒ 多出来的 FAT 扇区谁都找不到
    #   ⇒ 目录/大流全读不出（实测 39MB 大任务档「放建筑」后产物 0 个流，工具自己都打不开）。
    data_sec = sum(n for _, n in layout)
    n_fat, n_difat = 1, 0
    while True:
        total = data_sec + n_fat + n_difat + 1   # +1 = 头部占文件首 512B
        need = (total + 127) // 128
        need_d = 0 if need <= 109 else (need - 109 + 126) // 127
        if need <= n_fat and need_d == n_difat:
            break
        n_fat = max(need, n_fat)
        n_difat = need_d
    if n_difat:
        layout.append(('__difat__', n_difat))
    layout.append(('__fat__', n_fat))

    # 分配扇区号
    sid_of, sec = 0, {}
    for nm, n in layout:
        sec[nm] = (sid_of, n)
        sid_of += n
    n_total = sid_of

    # ---- 3. 建 FAT ----
    fat = [FREESECT] * (n_fat * 128)
    for nm, n in layout:
        s0, _ = sec[nm]
        for k in range(n):
            fat[s0 + k] = s0 + k + 1 if k < n - 1 else ENDOFCHAIN
    for k in range(n_fat):
        fat[sec['__fat__'][0] + k] = FATSECT
    if n_difat:                                   # DIFAT 扇区标成 DIFSECT(0xFFFFFFFC)
        for k in range(n_difat):
            fat[sec['__difat__'][0] + k] = 0xFFFFFFFC

    # ---- 4. 目录项（保留源档的顺序/名字/树指针，只改 start/size）----
    dirent = bytearray()
    for e in entries:
        b = bytearray(e['raw'])
        nm = e['name']
        if e['type'] == 5:                       # Root Entry：mini 流的 start/size
            struct.pack_into('<I', b, 116, sec['__mini__'][0] if mini_nsec else ENDOFCHAIN)
            struct.pack_into('<I', b, 120, len(mini_data))
        elif e['type'] == 2 and nm in streams:
            d = streams[nm]
            if nm in small:
                struct.pack_into('<I', b, 116, mini_start[nm])
            else:
                struct.pack_into('<I', b, 116, sec[nm][0] if d else ENDOFCHAIN)
            struct.pack_into('<I', b, 120, len(d))
        dirent += b

    # ---- 5. 头部 ----
    head = bytearray(SSZ)
    head[0:8] = SIGNATURE
    struct.pack_into('<H', head, 24, 0x003E)
    struct.pack_into('<H', head, 26, 0x0003)
    struct.pack_into('<H', head, 28, 0xFFFE)
    struct.pack_into('<H', head, 30, 9)
    struct.pack_into('<H', head, 32, 6)
    struct.pack_into('<I', head, 40, 0)
    struct.pack_into('<I', head, 44, n_fat)
    struct.pack_into('<I', head, 48, sec['__dir__'][0])
    struct.pack_into('<I', head, 52, 0)
    struct.pack_into('<I', head, 56, MINI_CUTOFF)
    struct.pack_into('<I', head, 60, sec['__minifat__'][0] if n_minifat_sec else ENDOFCHAIN)
    struct.pack_into('<I', head, 64, n_minifat_sec)
    struct.pack_into('<I', head, 68, sec['__difat__'][0] if n_difat else ENDOFCHAIN)
    struct.pack_into('<I', head, 72, n_difat)
    for k in range(109):
        struct.pack_into('<I', head, 76 + 4 * k,
                         sec['__fat__'][0] + k if k < n_fat else FREESECT)

    # ---- 6. 扇区数据 ----
    body = bytearray(n_total * SSZ)

    def put(s0, data, total=None):
        data = bytes(data)
        if total is not None:
            data = data + b'\x00' * (total * SSZ - len(data))
        body[s0 * SSZ:s0 * SSZ + len(data)] = data

    for nm in big:
        put(sec[nm][0], streams[nm], sec[nm][1])
    if n_mini_sec:
        put(sec['__mini__'][0], bytes(mini_data), n_mini_sec)
    if n_minifat_sec:
        mb = struct.pack('<%dI' % len(minifat), *minifat)
        put(sec['__minifat__'][0], mb, n_minifat_sec)
    put(sec['__dir__'][0], bytes(dirent), dirsz)
    if n_difat:                                   # 第 109 个之后的 FAT 扇区号写进 DIFAT 链
        rest = [sec['__fat__'][0] + k for k in range(n_fat)][109:]
        for i in range(n_difat):
            chunk = rest[i * 127:(i + 1) * 127]
            vals = chunk + [FREESECT] * (127 - len(chunk))
            nxt = sec['__difat__'][0] + i + 1 if i < n_difat - 1 else ENDOFCHAIN
            put(sec['__difat__'][0] + i, struct.pack('<128I', *(vals + [nxt])))
    fatb = struct.pack('<%dI' % len(fat), *fat)
    put(sec['__fat__'][0], fatb, n_fat)

    with open(out_path, 'wb') as f:
        f.write(bytes(head))
        f.write(bytes(body))
    return {'file': SSZ + len(body), 'sectors': n_total, 'n_fat': n_fat}


if __name__ == '__main__':
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else r'E:\YURI\_备份存档\ALL01UMD.sav'
    dst = sys.argv[2] if len(sys.argv) > 2 else r'C:\Users\zhao5\Desktop\_cfbwrite_rt.SAV'
    raw, entries, streams = load_streams(src)
    r = rebuild(src, {}, dst)
    print('原档 %d 字节 -> 重写 %d 字节  扇区 %d  FAT 扇区 %d'
          % (len(raw), r['file'], r['sectors'], r['n_fat']))
    # 逐流比对
    raw2, entries2, streams2 = load_streams(dst)
    bad = [nm for nm in streams if streams[nm] != streams2.get(nm)]
    print('逐流比对：%d 个流，不一致 %d 个 %s' % (len(streams), len(bad), bad[:5]))
    print('目录项数：%d -> %d' % (len(entries), len(entries2)))
