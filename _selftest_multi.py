"""_selftest_multi.py —— 多动作触发（一条触发挂多条行为）的验收自检。

覆盖 2026-10-18 16:1x 新增的能力：
  · `trigger_actions()` 能列出某触发的**全部行为**（条数规则见 `_action_runs`）；
  · `retarget_trigger(..., ac_index=N)` 只改第 N 条，其它行为逐字节不变。

规则（两份任务档验证）：条数 = 下一条触发的首行为序号 − 本条首行为序号；
排在最后的 = 动作总数 − 首行为序号。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc          # noqa: E402

ok = True


def check(name, cond, detail=''):
    global ok
    print(('  OK   ' if cond else '  FAIL ') + name + (('  ← ' + detail) if detail and not cond else ''))
    if not cond:
        ok = False


def find(name, root=r'E:\YURI'):
    for dp, _dn, fn in os.walk(root):
        if name in fn:
            return os.path.join(dp, name)
    return os.path.join(root, name)


def main():
    src = find('三建筑未刷出.SAV')
    if not os.path.exists(src):
        print('找不到测试档 三建筑未刷出.SAV')
        return 2
    tmp = tempfile.mkdtemp(prefix='multi_')
    out = os.path.join(tmp, 'm.SAV')

    d = SaveDoc(src)
    rows = d.trigger_actions('01001748')
    check('01001748 列出 3 条行为', len(rows) == 3, str(len(rows)))
    if len(rows) == 3:
        check('第1条 ATESLA / (89,250)', rows[0]['name'] == 'ATESLA' and rows[0]['cell'] == (89, 250),
              repr(rows[0]))
        check('第2条 GAPOWR / (81,245)', rows[1]['name'] == 'GAPOWR' and rows[1]['cell'] == (81, 245),
              repr(rows[1]))
        check('序号连续 1812/1813/1814',
              [r['index'] for r in rows] == [rows[0]['index'], rows[0]['index'] + 1,
                                             rows[0]['index'] + 2])

    # 只改第 2 条：把 GAPOWR@(81,245) 改成 ATESLA@(92,250)
    before = bytes(d.plain)
    info = d.retarget_trigger('01001748', ac_name='ATESLA', ac_cell=(92, 250), ac_index=2)
    d.save(out)
    after_doc = SaveDoc(out)
    pl = bytes(after_doc.plain)
    rows2 = after_doc.trigger_actions('01001748')
    check('改完仍 3 条行为', len(rows2) == 3, str(len(rows2)))
    check('第2条变成 ATESLA', rows2[1]['name'] == 'ATESLA', repr(rows2[1]))
    check('第1、3条未被动（名字）',
          rows2[0]['name'] == 'ATESLA' and rows2[2]['name'] == 'GAPOWR',
          '%s / %s' % (rows2[0]['name'], rows2[2]['name']))
    check('第3条落点未被动 (90,256)', rows2[2]['cell'] == (90, 256), repr(rows2[2]['cell']))

    print('')
    print('SELFTEST MULTI ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
