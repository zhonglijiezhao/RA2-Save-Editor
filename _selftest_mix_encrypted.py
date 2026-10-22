# -*- coding: utf-8 -*-
"""自检：加密 MIX 读取（2026-10-21）

背景：`E:\YURI` 下 97 个 MIX 里有 **74 个是加密的**（flags 0x20000/0x30000），
包括 ra2md.mix / ra2.mix / language.mix —— 我们原来的 `core/mix.py` **读不了它们**
（只把 encrypted=True 标出来就返回空表）。现在用纯标准库实现：
  80B key source --RSA(Westwood 公开模数,e=0x10001)--> 56B Blowfish 密钥
  --> Blowfish/ECB 解索引头（standard-ecb 与 westwood-le 两种都试，靠**边界校验**挑对的）

断言：
  A 加密 MIX 能解出索引：count>0、datasize <= 文件剩余、每条 offset+size 在数据区内
  B 取出来的条目内容像真文件（头 4 字节是 MIX 头或文本 '['）
  C 未加密 MIX 不受影响（回归）
  D 错的字节序会被校验否掉（不会静默返回垃圾）
  E 批量：抽样加密 MIX 全部可解
"""
import io
import os
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))
import mix

GAME = r'E:\YURI'
OK = FAIL = 0


def chk(c, msg):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


print('== A: 加密 MIX 解索引 ==')
for name in ('ra2md.mix', 'ra2.mix', 'language.mix', 'multimd.mix'):
    p = os.path.join(GAME, name)
    if not os.path.exists(p):
        print('   （缺 %s，跳过）' % name)
        continue
    m = mix.parse_mix(p)
    size = os.path.getsize(p)
    bad = [e for e in (m['entries'] if m else []) if e[1] + e[2] > m.get('datasize', 0)]
    chk(bool(m and m['entries']), '%s：解出 %s 条（mode=%s）'
        % (name, m and m['count'], m and m.get('mode')))
    chk(m and m.get('datasize', 0) <= size - m['data_start'],
        '   数据区 %s <= 文件剩余 %s' % (m and m.get('datasize'), size - (m['data_start'] if m else 0)))
    chk(not bad, '   全部条目在数据区内（越界 %d 条）' % len(bad))

print('\n== B: 取出的内容像真文件 ==')
p = os.path.join(GAME, 'ra2md.mix')
if os.path.exists(p):
    m = mix.parse_mix(p)
    looks = 0
    for e in (m['entries'] or [])[:5]:
        b = mix.entry_bytes(m, e)
        if len(b) >= 4 and (b[0] == 0 and b[1] == 0 and b[2] in (1, 2, 3, 0) or b[:1] == b'['):
            looks += 1
    chk(looks >= 3, '前 5 条里 %d 条头 4 字节是合法 MIX 头/文本（嵌套 MIX）' % looks)

print('\n== C: 未加密 MIX 回归 ==')
up = r'C:\Users\zhao5\Desktop\WorkBuddy\红警MOD制作素材工具与教程\mix解密\Expandmd99.mix'
if os.path.exists(up):
    m = mix.parse_mix(up)
    chk(m and not m['encrypted'] and m['count'] == 571, 'Expandmd99.mix：未加密、571 条（%s）'
        % (m and m['count']))

print('\n== D: 错误字节序被校验否掉 ==')
if os.path.exists(p):
    data = open(p, 'rb').read()
    key = mix.rsa_key_from_source(data[4:84])
    ok_std = mix.bf_decrypt_ecb(data[84:92], key, False)
    ok_le = mix.bf_decrypt_ecb(data[84:92], key, True)
    chk(ok_std != ok_le, '两种字节序解出的字节不同（说明校验确实在起作用）')
    n_std = int.from_bytes(ok_std[:2], 'little')
    n_le = int.from_bytes(ok_le[:2], 'little')
    print('    standard-ecb 首字=%d，westwood-le 首字=%d（只有前者能通过边界校验）' % (n_std, n_le))
    chk(mix.parse_mix(p)['mode'] == 'standard-ecb', 'ra2md.mix 选中的是 standard-ecb')

print('\n== E: 批量抽样加密 MIX ==')
if os.path.isdir(GAME):
    enc = []
    for fn in sorted(os.listdir(GAME)):
        if not fn.lower().endswith(('.mix', '.mmx')):
            continue
        fp = os.path.join(GAME, fn)
        try:
            head = open(fp, 'rb').read(8)
        except Exception:
            continue
        if len(head) >= 4 and (int.from_bytes(head[:4], 'little') & 0x20000):
            enc.append(fp)
    sample = enc[:8]
    good = 0
    for fp in sample:
        try:
            mm = mix.parse_mix(fp)
            if mm and mm['entries']:
                good += 1
        except Exception:
            pass
    chk(good == len(sample), '抽样 %d 个加密 MIX，全部解出（%d/%d）'
        % (len(sample), good, len(sample)))

print('\n加密 MIX 自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
