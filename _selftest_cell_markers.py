# -*- coding: utf-8 -*-
"""§SOV10 回归：**CellTag 挂接的单元标记能不能一个个扫出来**。

玩家 2026-11-04：「能扫到那18个单元标记以及上面挂接的触发吗」

机制：CellTag 对象里的 +64 只是**挂接数**（计数）；每个挂接点的宿主记录里存着
"我挂了哪个 CellTag"（CellTag 自键），所以扫全档里 == 自键 的 u32 就能一个个找出来：
  · 命中 −24 起 = (x u16, y u16)  ← 单元标记的坐标
  · 命中 +8 起 = ff ff ff ff      ← 实测 18/18 都是

本自检：
  A. SAVE6784：18 个全部扫到，正好是 x 74..79 × y 76..78 的 6×3 方阵
  B. 挂接数 == 扫到个数（该档）
  C. SAVE5739：**扫到的个数永远不会多于挂接数**（多的那些是"对象已离场但计数没减"）
  D. SAVE6443：挂接数 0 ⇒ 一个都扫不到（不能凭空造）
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'core'))
from editor_core import SaveDoc   # noqa: E402

SAV = r'E:\YURI\SAVE6784.SAV'          # 18 个单元标记
BIG = r'E:\YURI\SAVE5739.SAV'          # 大任务档：306 条带 CellTag
NONE = r'E:\YURI\SAVE6443.SAV'         # 挂接数 0
OK = FAIL = 0


def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s' % msg)


def main():
    if not os.path.exists(SAV):
        print('SKIP：缺样本 SAVE6784.SAV')
        return 0
    d = SaveDoc(SAV)
    print('== A. SAVE6784：18 个单元标记 ==')
    mk = d.trigger_markers('01000002')
    cells = mk['cells']
    chk(mk['count'] == 18, 'CellTag 挂接数 = %s' % mk['count'])
    chk(len(cells) == 18, '扫到 %d 个' % len(cells))
    chk(mk['tag_key'] and mk['cell_key'], '标签键 %08X / CellTag 键 %08X'
        % (mk['tag_key'] or 0, mk['cell_key'] or 0))
    xs = sorted({c[0] for c in cells})
    ys = sorted({c[1] for c in cells})
    chk(xs == list(range(74, 80)), 'x 正好是 74..79（%s）' % xs)
    chk(ys == [76, 77, 78], 'y 正好是 76/77/78（%s）' % ys)
    chk(len(cells) == len(xs) * len(ys), '6×3 = 18，一个不缺')
    print('      坐标：%s' % cells)

    print('== B/C. SAVE5739：扫到的个数不会多于挂接数 ==')
    if os.path.exists(BIG):
        d2 = SaveDoc(BIG)
        tot = eq = fewer = more = 0
        for r in d2.records:
            if r.kind != '触发':
                continue
            m = d2.trigger_markers(r.sid)
            if m['count'] is None:
                continue
            tot += 1
            n = len(m['cells'])
            if n == m['count']:
                eq += 1
            elif n < m['count']:
                fewer += 1
            else:
                more += 1
        print('      带 CellTag 的触发 %d 条：相等 %d · 扫到更少 %d · **扫到更多 %d**'
              % (tot, eq, fewer, more))
        chk(more == 0, '没有任何一条"扫到多于挂接数"（否则就是误扫）')
        chk(eq > 0, '有 %d 条完全对得上' % eq)
        chk(fewer > 0, '有 %d 条"扫到的比计数少"= 挂接对象已离场但计数没减（§163 漏账）'
            % fewer)
    else:
        print('  [SKIP] 缺 SAVE5739')

    print('== D. SAVE6443：挂接数 0 ⇒ 扫到 0 ==')
    if os.path.exists(NONE):
        d3 = SaveDoc(NONE)
        m = d3.trigger_markers('01000002')
        chk(m['count'] == 0, '挂接数 = %s' % m['count'])
        chk(m['cells'] == [], '扫到 %d 个（应为 0）' % len(m['cells']))
    else:
        print('  [SKIP] 缺 SAVE6443')

    print('\n§SOV10 回归：PASS %d / FAIL %d' % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
