# -*- coding: utf-8 -*-
r"""自检：改造已有触发（交接任务 B，M1 路线的产品化）。

跑法（项目根目录）：
    python _selftest_retarget.py [干净基线存档]

验收点：
  1. `trigger_action` 能读出触发 01000000 的动作（行为 3 / 参数 0）；
  2. `retarget_trigger` 改成「行为 125 建 GAPOWR @ (80,245)」后，
     **明文总长度不变**，且**只有动作对象那几个字段 + 路径点表那一格**变了；
  3. 复检（重新读盘）字段全部对上；
  4. GUI 的「🔧 改造选中触发的动作…」对话框能构造出来（不点写回）。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc                                    # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else \
    r'E:\YURI\新建文件夹 (22)\加觖发刷电厂.SAV'
TID = '01000000'
TYPE = 'GAPOWR'
CELL = (80, 245)


def diff_ranges(a, b):
    """逐字节比较，返回 [ (起点, 终点) ) 的连续不同区间。"""
    n = min(len(a), len(b))
    out, start = [], None
    for i in range(n):
        if a[i] != b[i]:
            if start is None:
                start = i
        elif start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, n))
    return out


def main():
    lines, fails = [], []

    def chk(ok, msg):
        lines.append(('  OK   ' if ok else '  FAIL ') + msg)
        if not ok:
            fails.append(msg)

    lines.append('存档: %s' % BASE)
    d = SaveDoc(BASE)
    before = d.trigger_action(TID)
    lines.append('[1] 读现状: 行为 %d 参数 %d 建筑名 %r 路径点 %d'
                 % (before['type'], before['arg'], before['name'], before['waypoint']))
    chk(before['type'] == 3, '触发 %s 改造前行为应为 3（实际 %d）' % (TID, before['type']))

    n0 = len(d.plain)
    info = d.retarget_trigger(TID, ac_type=125, ac_name=TYPE, ac_cell=CELL)
    tmp = os.path.join(tempfile.gettempdir(), '_ra2_retarget_check.SAV')
    d.save(tmp)
    lines.append('[2] 改造+写盘: %s' % tmp)
    lines.append('    行为 %d→%d　建筑名 %r→%r　路径点 %d→%d %s'
                 % (before['type'], info['type'], before['name'], info['name'],
                    before['waypoint'], info['waypoint'], info['cell']))
    chk(len(d.plain) == n0, '明文长度必须不变（%d → %d）' % (n0, len(d.plain)))
    chk(info['cell'] == CELL, '落点应为 %r（实际 %r）' % (CELL, info['cell']))

    c = SaveDoc(tmp)
    got = c.trigger_action(TID)
    lines.append('[3] 复检(重新读盘): 行为 %d 参数 %d 建筑名 %r 路径点 %d %s'
                 % (got['type'], got['arg'], got['name'], got['waypoint'], got['cell']))
    chk(got['type'] == 125, '复检行为应为 125')
    chk(got['name'] == TYPE, '复检建筑名应为 %s' % TYPE)
    chk(got['cell'] == CELL, '复检落点应为 %r' % (CELL,))

    # —— 铁证：改动只落在动作对象 + 路径点表那一格 ——
    src = SaveDoc(BASE)
    a = before['action_off']
    wp_off = src.waypoint_offset(got['waypoint'])
    rngs = diff_ranges(src.plain, c.plain)
    lines.append('[4] 逐字节差异 %d 段: %s' % (len(rngs), rngs[:12]))
    allowed = [(a, a + 112), (wp_off, wp_off + 4)]
    bad = [r for r in rngs
           if not any(r[0] >= s and r[1] <= e for s, e in allowed)]
    chk(not bad, '差异必须全部落在「动作对象 + 路径点 %d」内，越界段: %s'
        % (got['waypoint'], bad))

    # —— GUI 构造 ——
    try:
        import RA2存档编辑器 as m
        app = m.App()
        app.withdraw()
        app.update_idletasks()
        app._set_doc(SaveDoc(BASE))
        app.update_idletasks()
        row = next((i for i, r in enumerate(app.doc.records) if r.sid == TID), None)
        app.tt.selection_set(str(row))
        app.retarget_dialog()
        app.update_idletasks()
        tops = [w for w in app.winfo_children() if w.winfo_class() == 'Toplevel']
        lines.append('[5] GUI 对话框: %d 个 Toplevel（标题 %r）'
                     % (len(tops), tops[0].title() if tops else ''))
        chk(bool(tops), '找不到 retarget 对话框')
        for w in tops:
            w.destroy()
        app.destroy()
    except Exception as e:
        import traceback
        lines.append('[5] GUI 构造异常:\n' + traceback.format_exc())
        fails.append('GUI 构造异常 %s' % e)

    try:
        os.remove(tmp)
    except Exception:
        pass

    lines.append('')
    lines.append('SELFTEST RETARGET ' + ('PASS' if not fails else 'FAIL'))
    txt = '\n'.join(lines)
    print(txt)
    try:
        with open(os.path.join(HERE, '自检_改造触发.txt'), 'w', encoding='utf-8-sig') as f:
            f.write(txt + '\n')
    except Exception:
        pass
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
