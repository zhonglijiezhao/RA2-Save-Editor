# -*- coding: utf-8 -*-
"""`_pdb_sym.py` —— 读 MSVC 的 `.pdb`（MSF 7.00），列公开符号（函数 / 全局变量 / 字符串常量）。

**为什么自己写**：本机没有 dumpbin / cdb / llvm-pdbutil / pdbparse，但格式不难，
纯标准库就够。第一次用在 `桌面/FA2SP_HDM_Edition_1.3.5/FA2sp.pdb`（地编 FA2sp.dll）。

用法
    python _pdb_sym.py <xxx.pdb>                        # 总览（流、符号数、抽样）
    python _pdb_sym.py <xxx.pdb> Waypoint               # 名字含 Waypoint 的符号
    python _pdb_sym.py <xxx.pdb> Waypoint --rva --dll <同名.dll>
                                                        # 连地址一起列（换算成真 RVA）
    python _pdb_sym.py <xxx.pdb> --dump syms.txt        # 全部符号导成文本
    python _pdb_sym.py <xxx.pdb> --limit 50 Waypoint

    不给 `--dll` 时地址显示成 `seg1+402F00`（段内偏移）；给了 --dll（需要 pefile）
    就按段的 VirtualAddress 换成真 RVA —— 反汇编必须用后者。

格式要点（都是实测出来的，别按"标准文档"想当然）
  · 超级块 `Microsoft C/C++ MSF 7.00\r\n\x1aDS`：0x20 PageSize / 0x28 PageCount /
    0x2C 目录字节数 / 0x34 目录块号表所在块。
  · **流目录里 `size == 0xFFFFFFFF` 表示"未使用流槽"，块数按 0 算**；
    其它流块数 = `ceil(size / PageSize)`。漏了这条会把后面 10 个流槽读错位。
  · 流 3 = DBI（里面有 Age、符号记录流号等）；流 0 在本档是老式"目录副本"，不看它。
  · 符号记录流里 `S_PUB32`（0x110E）记录的实际布局是：
        u16 reclen | u16 0x110E | u32 off | u32 rva | u16 seg | char name[] …
    （名字从记录起点 +14 开始；微软文档写的是 off+seg+flags，MSVC 实际多一个 u32。）
"""
import os
import re
import struct
import sys

SIG = b'Microsoft C/C++ MSF 7.00'
S_PUB32 = 0x110E


# ---------------------------------------------------------------- MSF 容器
class Pdb(object):
    def __init__(self, path):
        self.path = path
        self.data = open(path, 'rb').read()
        d = self.data
        if not d.startswith(SIG):
            raise ValueError('不是 MSF 7.00 的 PDB（头 %r）' % d[:32])
        (self.page, _freemap, self.npages,
         self.dirbytes, _unk, bmap) = struct.unpack_from('<IIIIII', d, 0x20)
        need = (self.dirbytes + self.page - 1) // self.page
        addrs = list(struct.unpack_from('<%dI' % need, d, bmap * self.page))
        blob = b''.join(d[a * self.page:(a + 1) * self.page] for a in addrs)
        self.dir = blob[:self.dirbytes]
        self.nstream = struct.unpack_from('<I', self.dir, 0)[0]
        self.sizes = list(struct.unpack_from('<%dI' % self.nstream, self.dir, 4))
        p = 4 + 4 * self.nstream
        self.blocks = []
        for i in range(self.nstream):
            sz = self.sizes[i]
            k = 0 if sz == 0xFFFFFFFF else (sz + self.page - 1) // self.page
            self.blocks.append(list(struct.unpack_from('<%dI' % k, self.dir, p)) if k else [])
            p += 4 * k
        self._dir_end = p
        self._cache = {}

    def stream(self, i):
        if i in self._cache:
            return self._cache[i]
        if not (0 <= i < self.nstream) or self.sizes[i] in (0, 0xFFFFFFFF):
            b = b''
        else:
            b = b''.join(self.data[x * self.page:(x + 1) * self.page]
                         for x in self.blocks[i])[:self.sizes[i]]
        self._cache[i] = b
        return b

    # ------------------------------------------------------------ DBI
    def dbi(self):
        d = self.stream(3)
        if len(d) < 64:
            raise ValueError('DBI 流读不到（只有 %d 字节）' % len(d))
        h = struct.unpack_from('<iIIHHHHHHiiiiiIiiHHI', d, 0)
        keys = ('version_sig', 'version_header', 'age', 'global_stream', 'build',
                'public_stream', 'pdb_dll_ver', 'sym_stream', 'pdb_dll_rbld',
                'modinfo_sz', 'seccontr_sz', 'secmap_sz', 'srcinfo_sz',
                'tsmap_sz', 'mfc_ts_idx', 'optdbg_sz', 'ec_sz',
                'flags', 'machine', 'padding')
        return dict(zip(keys, h))

    # ------------------------------------------------------ 公开符号
    def publics(self):
        """[(name, seg_off, seg)] —— S_PUB32。

        ★★seg_off 是**段内偏移，不是 RVA**！RVA = 该段的 VirtualAddress + seg_off。
          实测 `Waypoint_To_String` = seg1+0x402F00，而 .text 的 VA 是 0x1000
          ⇒ 真 RVA 0x403F00 才落在 `55 8B EC`（push ebp; mov ebp,esp）这个函数序言上；
          直接拿 0x402F00 当 RVA 会从函数**中部**开始反汇编，看到的全是错的。
          `--dll` 参数就是干这个换算的（需要 pefile；没有就只显示 seg+偏移）。
        """
        info = self.dbi()
        sym = self.stream(info['sym_stream'])
        out = []
        i, n = 0, len(sym)
        while i + 14 <= n:
            ln, ty = struct.unpack_from('<HH', sym, i)
            if ln < 12 or i + ln + 2 > n:
                # 记录长度坏了就往后挪 2 字节重新对齐（别整条流放弃）
                i += 2
                continue
            if ty == S_PUB32:
                _off, seg_off, seg = struct.unpack_from('<IIH', sym, i + 4)
                nm = sym[i + 14:i + 2 + ln].split(b'\0')[0]
                if nm:
                    out.append((nm.decode('latin-1'), seg_off, seg))
            i += ln + 2
        return out


# ---------------------------------------------------------------- 名字美化
def demangle(nm):
    """把 MSVC 的 mangled 名压成人能读的样子（够用即可，不追求完整）。"""
    if nm.startswith('??_C@'):                      # 字符串常量
        body = nm[5:].split('@')
        return 'const_string %r' % (body[-2].replace('?$', '\\').replace('?5', ' ')
                                    if len(body) >= 2 else '')
    if nm.startswith('?'):
        s = nm[1:]
        # ?Func@Class@Ns@@...  →  Ns::Class::Func
        parts = []
        i = 0
        while i < len(s):
            j = s.find('@', i)
            if j < 0:
                break
            parts.append(s[i:j])
            i = j + 1
            if s[i:i + 1] == '@':
                break
        if len(parts) >= 2:
            parts.reverse()
            return '::'.join(p for p in parts if p)
        return nm
    if nm.startswith('_'):
        return nm[1:]
    return nm


def short(nm):
    d = demangle(nm)
    return d


# ---------------------------------------------------------------- 命令行
def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    rest = argv[2:]
    show_rva = '--rva' in rest
    limit = 200
    dump = None
    dll = None
    if '--limit' in rest:
        i = rest.index('--limit')
        limit = int(rest[i + 1])
        del rest[i:i + 2]
    if '--dump' in rest:
        i = rest.index('--dump')
        dump = rest[i + 1]
        del rest[i:i + 2]
    if '--dll' in rest:
        i = rest.index('--dll')
        dll = rest[i + 1]
        del rest[i:i + 2]
    kws = [a.lower() for a in rest if not a.startswith('--') and a != '--rva']

    p = Pdb(path)
    info = p.dbi()
    print('文件      : %s（%.1f MB）' % (os.path.basename(path), len(p.data) / 1048576.0))
    print('MSF       : 页 %d 字节 / 共 %d 块 → %d 个流（目录 %d 字节，解析消耗 %d）'
          % (p.page, p.npages, p.nstream, p.dirbytes, p._dir_end))
    print('DBI       : Age=%d  Build=%d  符号流=%d  TPI=%d  字节 / 符号记录流=%.1f MB'
          % (info['age'], info['build'], info['sym_stream'], p.sizes[2],
             p.sizes[info['sym_stream']] / 1048576.0))

    # 可选：拿 PE 的段表把「段内偏移」换算成真 RVA
    segva = {}
    if dll:
        try:
            import pefile
            for i, s in enumerate(pefile.PE(dll, fast_load=True).sections, 1):
                segva[i] = s.VirtualAddress
            print('段表      : 来自 %s → %s' % (os.path.basename(dll),
                  ', '.join('seg%d=+%#x' % (k, v) for k, v in sorted(segva.items()))))
        except Exception as e:
            print('段表      : 读 %s 失败（%s）⇒ 只显示 seg+偏移' % (dll, e))

    pubs = p.publics()
    print('S_PUB32   : %d 条公开符号（去重 %d）'
          % (len(pubs), len(set(x[0] for x in pubs))))

    def addr(seg_off, seg):
        if segva.get(seg) is not None:
            return '%08X' % (segva[seg] + seg_off)
        return 'seg%d+%06X' % (seg, seg_off)

    if dump:
        with open(dump, 'w', encoding='utf-8') as f:
            for nm, off, seg in pubs:
                f.write('%s %s\n' % (addr(off, seg), short(nm)))
        print('已导出 → %s' % dump)

    hits = pubs
    if kws:
        hits = [x for x in pubs if all(k in x[0].lower() for k in kws)]
        print('\n含 %s 的符号: %d 条' % (' + '.join(kws), len(hits)))
    for nm, off, seg in hits[:limit]:
        if show_rva:
            print('  %-13s %s' % (addr(off, seg), short(nm)))
        else:
            print('  ' + short(nm))
    if len(hits) > limit:
        print('  … 共 %d 条（--limit N 可多看）' % len(hits))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
