# -*- coding: utf-8 -*-
"""§SOV02 回归：触发名**不能被截断**（玩家报「用触发编辑器打开一次，尾号就没了」）。

复现条件（2026-11-03 SAVE5739.SAV）：
  · 存档里 8 条触发叫 `Player_Has_Plant1` … `Player_Has_Plant8`（17 字符）；
  · `ui/trigger_editor.py` 的 `_on_name_change()` 以前写死 `nm = nm[:16]`
    ⇒ 打开触发编辑器（`reload()` 回填名字会触发 trace）时，17 字符被砍成
    `Player_Has_Plant`，且**立刻写进存档**（实时改名）⇒ 8 条尾号全丢。

本自检做两件事：
  1. 源码层：`trigger_editor.py` 里不许再出现按**字符**截断名字的写法；
  2. 数据层：`set_trigger_name()` 对 17 字符名做一次「写入 → 再读」往返，
     名字必须**逐字节不变**；超过 51 GBK 字节必须直接报错（而不是偷偷截断）。
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'core'))
from editor_core import SaveDoc          # noqa: E402

SAV = r'E:\YURI\SAVE5739.SAV'
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
    print('== 1. 源码层：不许按字符截断触发名 ==')
    src = open(os.path.join(_HERE, 'ui', 'trigger_editor.py'), encoding='utf-8').read()
    bad = re.findall(r'nm\s*=\s*nm\[:\s*\d+\s*\]', src)
    chk(not bad, 'trigger_editor.py 里没有 `nm = nm[:N]` 之类的硬截断（找到 %d 处）' % len(bad))
    chk('_name_bytes_ok' in src, '改名走 `_name_bytes_ok()`（按 GBK 字节数判上限）')

    print('== 2. 数据层：17 字符名往返 ==')
    if not os.path.exists(SAV):
        print('  [SKIP] 缺样本 %s' % SAV)
        return 1 if FAIL else 0
    doc = SaveDoc(SAV)
    names = [r for r in doc.records if (r.name or '').startswith('Player_Has_Plant')
             and (r.name or '')[-1].isdigit()]
    chk(len(names) >= 8, '存档里读到 %d 条带尾号的 Player_Has_Plant*' % len(names))
    for r in names[:8]:
        old = r.name
        doc.set_trigger_name(r.sid, old)                 # 同名单写一遍（= 打开编辑器的效果）
        again = doc.trigger_rec(r.sid).name
        chk(again == old, '%s：%r → %r' % (r.sid, old, again))
    # 超长必须报错，不能静默截断
    long_name = 'X' * 52
    try:
        doc.set_trigger_name(names[0].sid, long_name)
        chk(False, '52 字节的名字应当被拒绝，但写进去了')
    except ValueError as e:
        chk(True, '52 字节名字被拒绝（%s）' % e)
    print('\n§SOV02 回归：PASS %d / FAIL %d' % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
