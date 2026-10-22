# -*- coding: utf-8 -*-
r"""类型名对拍：本地权威 FAData.ini（兰博玩 LBW）vs 本工具表 core/fa2_tables.py

用法: python audit_type_names.py [FAData.ini]
默认 FAData: 红色警戒\FinalAlert2SP 1.6.1.0 LBW 20230525\FAData.ini (GBK)

★2026-10-15：全量对拍结论（兰博玩 1.6.1.0 LBW）
  · [EventsRA2] 130 条 —— 名称 **0 差异**
  · [ActionsRA2] 162 条 —— 仅 2 处，且都是**有意为之**：
      16  我们='显示全地图'           FA='「禁用」显示全地图（有替代品）'
          ⇒ FA 那名把「编辑器界面禁用」写进了名称（说明栏：兰博玩地图工具另有专门的
            显示全地图，地编禁用此触发）。**引擎照常执行**（本工具游戏内实测：事件13→16
            地图准时点亮）⇒ 名称改为引擎语义的「显示全地图」，提示语进 ACTION_NOTES。
      107 我们='超时空传送援军...'     FA='超时空传送援军... '（仅 FA 多一个尾随空格）
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
from fa2_tables import EVENT_TYPES, ACTION_TYPES        # noqa: E402

DEFAULT = (r'C:\Users\zhao5\Desktop\红色警戒'
           r'\FinalAlert2SP 1.6.1.0 LBW 20230525\FAData.ini')
KNOWN = {(16, '行为'): '有意修正：FA 名称含编辑器限制，引擎实际照常执行',
         (107, '行为'): '仅尾随空格差异（我们更干净）'}


def sections(txt):
    out = {}
    for m in re.finditer(r'^\[([^\]]+)\](.*?)(?=^\[|\Z)', txt, re.M | re.S):
        d = {}
        for ln in m.group(2).splitlines():
            s = ln.strip()
            if '=' not in s:
                continue
            k, v = s.split('=', 1)
            try:
                d[int(k)] = v.split(',')[0]
            except ValueError:
                pass
        out[m.group(1)] = d
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    if not os.path.exists(path):
        print('未找到 FAData.ini：%s' % path)
        return 1
    raw = open(path, 'rb').read()
    txt = None
    for enc in ('gbk', 'utf-8'):
        try:
            txt = raw.decode(enc)
            break
        except Exception:
            continue
    if txt is None:
        print('FAData.ini 解码失败（GBK/UTF-8 均不行）')
        return 1
    secs = sections(txt)
    total = 0
    for name, tbl, kind in (('EventsRA2', EVENT_TYPES, '事件'),
                            ('ActionsRA2', ACTION_TYPES, '行为')):
        s = secs.get(name)
        if not s:
            print('[%s] 段不存在，跳过' % name)
            continue
        diff = []
        for k, nm in sorted(s.items()):
            ours = tbl.get(k)
            if ours is None:
                diff.append('  %-4d 我们缺该编号：FA=%r' % (k, nm))
            elif ours[0] != nm:
                tag = KNOWN.get((k, kind))
                diff.append('  %-4d 名称不同：我们=%r FA=%r%s'
                            % (k, ours[0], nm, ('  ← ' + tag) if tag else ''))
        print('== [%s] FA %d 条 / 我们 %d 条，差异 %d =='
              % (name, len(s), len(tbl), len(diff)))
        for d in diff:
            print(d)
        total += len([d for d in diff if '←' not in d])
    print()
    print('未知差异（需人工确认）= %d' % total)
    return 0 if total == 0 else 2


if __name__ == '__main__':
    sys.exit(main())
