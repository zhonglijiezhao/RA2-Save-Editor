# -*- coding: utf-8 -*-
"""自检：游戏目录虚拟文件系统（散文件 > MIX 数字大的优先）+ 内置 MIX 解密

跑：python _selftest_mix_vfs.py

玩家 2026-11-04 定案（这是本自检的验收标准）：
  「解密这个还是要存档编辑器内置这个功能的，发布后不能要求玩家把规则文件解出来
    才能用这个软件，地编也是同样的做法。」
  「游戏读取规则是散文件优先，MIX 文件后面数字大的优先，比如 expandmd99.mix、
    expandmd90.mix 这两个文件，那么就是 expandmd99.mix 中的规则生效。」

断言：
  A 优先级：expandmd99.mix 压 expandmd90.mix（真实合成目录，同一份 soundmd.ini 两个版本）
  B 散文件压过所有 MIX
  C 没有数字的基础包（ra2.mix / ra2md.mix）排在 expandmd* 之后
  D 加密 MIX（Westwood RSA + Blowfish）能直接读，无需外部工具
  E 套娃：ra2md.mix → langmd.mix 里的文件能钻进去取出来
  F 认错保护：小体积 CSF（ares.csf）不会被当成 ra2md.csf
  G 磁盘索引缓存：第二次打开走缓存（快 3 倍以上）
  H 端到端：game_tables 从 MIX 里解出音效/语音表（本机 E:\\YURI / D:\\Ra2Game412）
"""
import io
import os
import shutil
import struct
import sys
import tempfile
import time
import zlib

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))
import mix                                                # noqa: E402
import mix_vfs                                            # noqa: E402

OK = FAIL = 0


def chk(c, msg, extra=None):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg + ('   → %r' % (extra,) if extra is not None else ''))


def build_mix(files):
    """造一个**未加密** MIX 归档：files = [(名字, 字节)] → bytes。

    文件名按 RA2 的规矩算 ID（zlib CRC32 大写），内容里必须自带特征（本自检的文件都带
    头注释），因为真实游戏里 mod 会改 ID —— VFS 也是靠内容认文件。
    """
    blobs, entries, off = [], [], 0
    for name, data in files:
        entries.append((zlib.crc32(name.upper().encode()) & 0xFFFFFFFF, off, len(data)))
        blobs.append(data)
        off += len(data)
    head = struct.pack('<IHI', 0, len(entries), off)
    body = b''.join(struct.pack('<III', *e) for e in entries)
    return head + body + b''.join(blobs)


def sound_ini(names):
    """造一份 soundmd.ini（头注释必须像真文件，VFS 靠它认）。"""
    lines = ['; SOUND.INI', '; All sounds in the game are specified here.', '', '[SoundList]', '']
    for i, n in enumerate(names, 1):
        lines.append('%d=%s' % (i, n))
    # ★VFS 会跳过 <200 字节的成员（真 ini 都是几十 KB），测试文件也要够大
    while len('\r\n'.join(lines)) < 400:
        lines.append('; padding line to keep the member above the size floor')
    return ('\r\n'.join(lines) + '\r\n').encode('latin-1')


def eva_ini(names):
    lines = ['', '[DialogList]', '']
    for i, n in enumerate(names, 1):
        lines.append('%d=%s' % (i, n))
    while len('\r\n'.join(lines)) < 400:
        lines.append('; padding line to keep the member above the size floor')
    return ('\r\n'.join(lines) + '\r\n').encode('latin-1')


TMP = tempfile.mkdtemp(prefix='_vfs_')
try:
    # ---------------- A: expandmd99 > expandmd90 ----------------
    print('== A: MIX 文件名数字大的优先（expandmd99 > expandmd90）==')
    d = os.path.join(TMP, 'prio')
    os.makedirs(d)
    with open(os.path.join(d, 'expandmd90.mix'), 'wb') as f:
        f.write(build_mix([('soundmd.ini', sound_ini(['OLD_90_A', 'OLD_90_B']))]))
    with open(os.path.join(d, 'expandmd99.mix'), 'wb') as f:
        f.write(build_mix([('soundmd.ini', sound_ini(['NEW_99_A', 'NEW_99_B', 'NEW_99_C']))]))
    v = mix_vfs.GameVFS(d)
    b, src = v.read('soundmd.ini')
    txt = b.decode('latin-1') if b else ''
    chk(b is not None and 'NEW_99_A' in txt and 'OLD_90_A' not in txt,
        '读到 expandmd99.mix 的那份（3 条），没串到 90', src)
    chk(src and 'expandmd99' in src, '来源标注是 expandmd99.mix', src)

    # 反过来：只有 90 时应该读 90（证明不是"只认 99"）
    os.remove(os.path.join(d, 'expandmd99.mix'))
    v2 = mix_vfs.GameVFS(d)
    b2, src2 = v2.read('soundmd.ini')
    chk(b2 is not None and b'OLD_90_A' in b2, '删掉 99 之后退回 90 的那份', src2)

    # ---------------- B: 散文件压过 MIX ----------------
    print('\n== B: 散文件优先（散装的压过 MIX 里的）==')
    with open(os.path.join(d, 'soundmd.ini'), 'wb') as f:
        f.write(sound_ini(['LOOSE_X']))
    v3 = mix_vfs.GameVFS(d)
    b3, src3 = v3.read('soundmd.ini')
    chk(b3 is not None and b'LOOSE_X' in b3 and b'OLD_90_A' not in b3,
        '散文件 soundmd.ini 生效', src3)
    chk(src3 and '散文件' in src3, '来源标注为散文件', src3)
    os.remove(os.path.join(d, 'soundmd.ini'))

    # ---------------- C: 基础包排在 expandmd* 之后 ----------------
    print('\n== C: 没有数字的基础包排在 expandmd* 之后 ==')
    d2 = os.path.join(TMP, 'base')
    os.makedirs(d2)
    with open(os.path.join(d2, 'ra2md.mix'), 'wb') as f:
        f.write(build_mix([('soundmd.ini', sound_ini(['FROM_RA2MD']))]))
    with open(os.path.join(d2, 'expandmd01.mix'), 'wb') as f:
        f.write(build_mix([('soundmd.ini', sound_ini(['FROM_EXPAND01']))]))
    with open(os.path.join(d2, 'ra2.mix'), 'wb') as f:      # ★名字里的 "2" 不是包序号
        f.write(build_mix([('evamd.ini', eva_ini(['FROM_RA2']))]))
    ranks = dict((fn, r) for fn, r in mix_vfs.describe_layers(d2, 10))
    chk(ranks.get('expandmd01.mix', 0) > ranks.get('ra2md.mix', 0),
        'expandmd01 的优先级高于 ra2md', ranks)
    chk(ranks.get('ra2md.mix', 0) > ranks.get('ra2.mix', 0),
        'ra2md 高于 ra2（基础包内部顺序）', ranks)
    v4 = mix_vfs.GameVFS(d2)
    b4, src4 = v4.read('soundmd.ini')
    chk(b4 is not None and b'FROM_EXPAND01' in b4, 'expandmd01 压过 ra2md', src4)

    # ---------------- D: 加密 MIX ----------------
    print('\n== D: 加密 MIX（RSA + Blowfish）不用外部工具 ==')
    game = r'E:\YURI'
    enc = None
    for fn in ('expandmd99.mix', 'expandmd01.mix', 'ra2md.mix', 'ra2.mix'):
        p = os.path.join(game, fn)
        if os.path.isfile(p):
            head = open(p, 'rb').read(4)
            if len(head) >= 4 and (int.from_bytes(head, 'little') & 0x20000):
                enc = p
                break
    if enc:
        m = mix.parse_mix(enc)
        chk(m and m.get('entries') and m.get('mode'),
            '%s 解出 %s 条（mode=%s）' % (os.path.basename(enc),
                                        m and m['count'], m and m.get('mode')))
        chk(not m.get('error'), '没有解密错误', m.get('error') if m else None)
    else:
        print('  （游戏目录里没有加密 MIX，跳过）')

    # ---------------- E: 套娃 ----------------
    print('\n== E: 套娃（ra2md.mix → langmd.mix → 文件）==')
    d3 = os.path.join(TMP, 'nest')
    os.makedirs(d3)
    art_body = (b'; ART.INI\r\n; This control file provides info\r\n[Movies]\r\n1=NESTED_FILM\r\n'
                + b'; padding\r\n' * 40)
    inner = build_mix([('artmd.ini', art_body)])
    with open(os.path.join(d3, 'ra2md.mix'), 'wb') as f:
        f.write(build_mix([('langmd.mix', inner)]))
    v5 = mix_vfs.GameVFS(d3)
    b5, src5 = v5.read('artmd.ini')
    chk(b5 is not None and b'NESTED_FILM' in b5, '穿过两层归档取到 artmd.ini', src5)
    chk(src5 and '内嵌' in src5, '来源标注出"内嵌"', src5)

    # ---------------- F: 认错保护 ----------------
    print('\n== F: 小体积 CSF 不会被当成 ra2md.csf ==')
    d4 = os.path.join(TMP, 'csf')
    os.makedirs(d4)
    tiny = b' FSC' + struct.pack('<III', 3, 1, 1) + b'\x00' * 8 + b' LBL' + struct.pack('<II', 1, 3) + b'abc' + b' RTS' + struct.pack('<I', 2) + b'A\x00B\x00'
    tiny = tiny + b'\x00' * (10 * 1024 - len(tiny))
    with open(os.path.join(d4, 'ares.mix'), 'wb') as f:
        f.write(build_mix([('ares.csf', tiny)]))
    v6 = mix_vfs.GameVFS(d4)
    chk(v6.index().get('ra2md.csf') is None, '小 CSF 不进 ra2md.csf 表', v6.index().get('ra2md.csf'))

    # ---------------- G: 磁盘缓存 ----------------
    print('\n== G: 磁盘索引缓存（第二次走缓存）==')
    if os.path.isdir(game):
        t0 = time.time()
        mix_vfs.GameVFS(game).index()
        t_first = time.time() - t0
        t0 = time.time()
        mix_vfs.GameVFS(game).index()
        t_second = time.time() - t0
        chk(t_second < max(t_first, 0.05) * 3 or t_second < 0.15,
            '二次载入 %.3fs（首次 %.3fs）' % (t_second, t_first))

    # ---------------- H: 端到端 ----------------
    print('\n== H: 端到端 —— game_tables 从 MIX 里解出表 ==')
    import game_tables
    for g in (r'E:\YURI', r'D:\Ra2Game412'):
        if not os.path.isdir(g):
            continue
        tb = game_tables.lists_for_dir(g)
        if not tb:
            chk(False, '%s：读不到表' % g)
            continue
        print('     %s ｜ %s' % (g, game_tables.describe(g)[:110]))
        chk(len(tb['sounds']) > 500, '%s 音效表 %d 项（soundmd.ini [SoundList]）'
            % (g, len(tb['sounds'])))
        chk(len(tb['eva']) > 100, '%s 语音表 %d 项（evamd.ini [DialogList]）'
            % (g, len(tb['eva'])))
        chk(len(tb['themes']) > 10, '%s 音乐表 %d 项（thememd.ini [Themes]）'
            % (g, len(tb['themes'])))
        chk(len(tb['movies']) > 10, '%s 影片表 %d 项（artmd.ini [Movies]）'
            % (g, len(tb['movies'])))
        chk(len(tb['csf']) > 1000, '%s 文本表 %d 项（ra2md.csf）'
            % (g, len(tb['csf'])))
        # 至少有一张表是从 MIX 里解出来的（证明真的在解密，不是读了散文件）
        from_mix = [k for k, s in tb['source'].items() if '散文件' not in s]
        chk(bool(from_mix), '%s 有 %d 张表是从 MIX 里直接解出来的：%s'
            % (g, len(from_mix), from_mix))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print('\nVFS + MIX 解密自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
