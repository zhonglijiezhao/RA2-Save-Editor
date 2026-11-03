# -*- coding: utf-8 -*-
"""§SOV01 回归：以**地图 INI 为真值**对拍存档里每条触发的事件/行为类型序列。

背景（2026-11-03 玩家报「这个存档读出的不对」）：
  `SOV01UMD.sav` 的 `sov01umd.map` 里有一条**未知类型的行为 609**
  （`[Actions] 01000105=2,14,0,8,…,A,609,0,0,1,…,A`），旧的
  「步长切分 + 段内类型必须全在表里」判定因此整表退化 ⇒ 166 条触发全是 0 条行为。
  另外有 2 条事件参数是引擎写的 `-1`（0xFFFFFFFF），被旧滤网 `arg > 0xFFFFFF` 丢掉
  ⇒ 01000211 / 01000213 读出来"没有事件"。

现在（`_split_tables` 按 obj+0 类字 392/393 分流、`_objects` 类字命中即收）：
  166/166 触发的事件/行为类型序列与地图完全一致。

跑法：python _selftest_sov01_maptruth.py
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, 'core'))
from map_ini import MapIni          # noqa: E402
from editor_core import SaveDoc     # noqa: E402

SAV = r'E:\YURI\SOV01UMD.sav'
MAP = r'E:\YURI\sov01umd.map'

def main():
    for p in (SAV, MAP):
        if not os.path.exists(p):
            print('SKIP: 缺样本 %s' % p)
            return 2
    doc = SaveDoc(SAV)
    m = MapIni(MAP)
    mev, mac = dict(m.events_by_trigger()), dict(m.actions_by_trigger())
    n_act = len(doc.actions())
    n_ev = len(doc.events())
    bad = []
    for sid in sorted(m.triggers):
        se = [e['type'] for e in doc.trigger_event_list(sid)]
        sa = [a['type'] for a in doc.trigger_action_list(sid)]
        ee = [t for t, _v in mev.get(sid, [])]
        ea = [t for t, _v in mac.get(sid, [])]
        if (se, sa) != (ee, ea):
            bad.append((sid, se, sa, ee, ea))
    print('对象表：行为 %d 条 / 事件 %d 条；未知类型 %s' % (
        n_act, n_ev, sorted({t for k, t in (getattr(doc, '_unknown_types', None) or [])})))
    print('触发 %d 条，与地图类型序列不一致 %d 条' % (len(m.triggers), len(bad)))
    for sid, se, sa, ee, ea in bad[:10]:
        print('  %s 存档 ev=%s ac=%s  地图 ev=%s ac=%s' % (sid, se, sa, ee, ea))
    # 行为 01000588 的三条（玩家报的那条）
    d = doc.trigger_detail('01000588')
    got = [(a['type'], a['name']) for a in d['actions']]
    print('01000588 行为 =', got)
    ok = (not bad) and [t for t, _ in got] == [46, 107, 11]
    # §SOV01 之二：609 = Phobos「设置雷达模式」，不是未知类型
    from fa2_tables import action_name
    print('行为 609 =', action_name(609))
    ok = ok and action_name(609) == '设置雷达模式（Phobos）'
    # §SOV01 之三：01000588 是「已执行」（CellTag 仍在定时表 + TagNode 帧数归零）
    st = doc.trigger_runtime_state('01000588')
    print('01000588 状态 = %s（ticks=%s in_timer=%s always_true=%s）'
          % (st['verdict'], st['ticks'], st['in_timer'], st['always_true']))
    ok = ok and st['verdict'] == 'fired'
    print('§SOV01 回归：%s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1

if __name__ == '__main__':
    sys.exit(main())
