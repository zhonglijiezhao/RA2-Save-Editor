# -*- coding: utf-8 -*-
"""§SOV40 自检：地图 INI 的路径点字母编码（双射 26 进制）。

依据：FA2sp 的 PDB 符号 `STDHelpers::WaypointToString`（RVA 0x3CEA50）/
`Waypoint_To_String`（RVA 0x403F00）反汇编 —— 见知识库 c15。
真值检验用 `E:\\YURI` 下 4 张真实地图：`[Actions]` 的字母解出来必须落在 `[Waypoints]` 的键里。

用法：python _selftest_map_wp_code.py
"""
import glob
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from map_ini import wp_encode, wp_decode          # noqa: E402

OK = FAIL = 0


def chk(cond, msg, extra=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  PASS %s' % msg)
    else:
        FAIL += 1
        print('  FAIL %s%s' % (msg, '' if extra is None else '  <%r>' % (extra,)))


print('== A. 定点样例（来自反汇编 + 真实地图）==')
CASES = [(0, 'A'), (1, 'B'), (25, 'Z'), (26, 'AA'), (27, 'AB'),
         (51, 'AZ'), (52, 'BA'), (98, 'CU'), (99, 'CV'), (100, 'CW'),
         (675, 'YZ'), (676, 'ZA'), (701, 'ZZ')]      # 702 个槽（0..701）最大就是 ZZ
for n, s in CASES:
    chk(wp_encode(n) == s, '%d → %s' % (n, s), wp_encode(n))
    chk(wp_decode(s) == n, '%s → %d' % (s, n), wp_decode(s))

print('\n== B. 往返（0..701 全覆盖，不许有例外）==')
bad = [n for n in range(0, 702) if wp_decode(wp_encode(n)) != n]
chk(not bad, '702 个数编解码往返一致', bad[:8])
dup = {}
for n in range(0, 702):
    dup.setdefault(wp_encode(n), []).append(n)
chk(all(len(v) == 1 for v in dup.values()), '没有两个号编出同一个串',
    [k for k, v in dup.items() if len(v) > 1][:5])
chk(len(set(wp_encode(n) for n in range(0, 702))) == 702, '702 个不同串')

print('\n== C. 容错 ==')
chk(wp_decode('cv') == 99, '小写也认（cv → 99）', wp_decode('cv'))
chk(wp_decode(' cv ') == 99, '前后空格无所谓', wp_decode(' cv '))
for junk in ('', 'A1', '1', 'ABCD', '中', None, '500'):
    chk(wp_decode(junk) is None, '认不出就返回 None：%r' % (junk,), wp_decode(junk))
try:
    wp_encode(-1)
    chk(False, '负数应当报错')
except ValueError:
    chk(True, '负数报错（不静默返回垃圾）')

print('\n== D. 真实地图：Actions 的字母必须落在 [Waypoints] 里 ==')
maps = sorted(glob.glob(r'E:\YURI\*.map'))
if not maps:
    print('  （E:\\YURI 下没有 .map，跳过）')
else:
    for p in maps:
        t = open(p, 'rb').read().decode('latin-1')

        def sec(name):
            m = re.search(r'(?mi)^\[' + name + r'\]\s*$(.*?)(?=^\[|\Z)', t, re.S)
            return m.group(1) if m else ''

        wps = set()
        for line in sec('Waypoints').splitlines():
            s = line.strip()
            if '=' in s:
                k = s.split('=', 1)[0].strip()
                if k.isdigit():
                    wps.add(int(k))
        letters = []
        for line in sec('Actions').splitlines():
            s = line.strip()
            if '=' not in s or s.startswith(';'):
                continue
            parts = s.split('=', 1)[1].split(',')
            try:
                n = int(parts[0])
            except Exception:
                continue
            for i in range(n):
                seg = parts[1 + i * 8: 1 + (i + 1) * 8]
                if len(seg) >= 8:
                    letters.append(seg[7].strip())
        # [TaskForces] 的 Waypoint= 也走同一套编码
        tf_letters = re.findall(r'(?mi)^Waypoint\s*=\s*([A-Z]{1,3})\s*$', t)
        letters += tf_letters
        name = os.path.basename(p)
        alpha = [x for x in letters if x and x.isalpha()]
        digits = [x for x in letters if x and x.isdigit()]
        got = [d for d in (wp_decode(x) for x in alpha) if d is not None]
        hit = [d for d in got if d in wps]
        miss = sorted(set(d for d in got if d not in wps))
        # 0 号（字母 A）常是"没指定"，地图未必定义 —— 单独放宽
        real_miss = [d for d in miss if d != 0]
        chk(not real_miss,
            '%-24s 路径点 %-3d 字母引用 %-4d 条 → 落在 [Waypoints] 的 %d/%d%s'
            % (name, len(wps), len(alpha), len(hit), len(got),
               ('（仅 0 号缺定义，正常）' if miss == [0] else '')),
            real_miss[:10])
        chk(len(alpha) + len(digits) == len(letters),
            '%-24s 第 8 字段只有"字母 / 数字"两种形态' % name,
            [x for x in letters if x and not (x.isalpha() or x.isdigit())][:5])
        # ★数字形态不是路径点：实测全是行为 131「闪烁特定类型的建筑」的**帧数**
        chk(not [x for x in digits if wp_decode(x) is not None],
            '%-24s 数字形态不会被误当成路径点（%s）'
            % (name, ','.join(sorted(set(digits))[:4]) or '无'), digits[:5])

print('\n§SOV40：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(0 if FAIL == 0 else 1)
