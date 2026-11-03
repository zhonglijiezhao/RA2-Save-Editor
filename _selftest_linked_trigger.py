# -*- coding: utf-8 -*-
"""§SOV03 回归：**关联触发**（FA2「关联触发」/ ra2map 教程）—— 一个标签共享给多条触发。

背景（2026-11-03 玩家指出）：
  存档 SAVE5739.SAV 里 `Player_Has_Plant1..8` 是**关联触发**：
  Plant8 挂着标签，Plant8 的下级是 Plant7、7 的下级是 6 …1 是链尾；
  **只有链头（Plant8）有标签对象**，其余 7 条把标签删了、共用那一个。
  旧版工具只会「按名字/地图配对标签」，所以这 7 条整列显示"未读出（无标签）"，
  状态也判成"没有标签"。

存档里的字段：记录帧 `+132` = **下级触发的实例键**（0 = 链尾）。

本自检验证：
  A. 链接字段解析（8 条成一条链、方向 8→7→…→1、无野值）
  B. 从触发能借到链头的标签：持久性/挂载/运行期状态
  C. 直接读「标签对象 +0xB0」与旧的配对读法结果一致（SAVE48DB / SOV01UMD 交叉验证）
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'core'))
from editor_core import SaveDoc            # noqa: E402

SAV = r'E:\YURI\SAVE5739.SAV'              # 关联触发样本（8 条共用 1 个标签）
SAV2 = r'E:\YURI\SOV01UMD.sav'             # 大任务档：新旧读法交叉验证
SAV3 = r'C:\Users\zhao5\Desktop\工程师\SAVE48DB.SAV'

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
        print('SKIP: 缺样本 %s' % SAV)
        return 0
    d = SaveDoc(SAV)
    head = '096E2DDC'          # Player_Has_Plant8（挂标签的那条）
    order = ['096E2DDC', '096E020C', '096E04EC', '096E07CC',
             '096E0AAC', '096E0D8C', '096E125C', '096E1F3C']

    print('== A. 链接字段（记录帧 +132 = 下级触发的实例键）==')
    chk(d.trigger_link_sid(head) == order[1],
        '%s 的下级 = %s' % (head, d.trigger_link_sid(head)))
    chk(d.trigger_link_sid(order[-1]) is None, '%s（链尾）没有下级' % order[-1])
    chk(d.trigger_link_chain(head) == order, '整条链 = %s' % d.trigger_link_chain(head))
    chk(d.trigger_tag_owner(head) is None, '链头自己没有"共用"对象')
    chk(all(d.trigger_tag_owner(s) == head for s in order[1:]),
        '其余 7 条都指向链头 %s' % head)

    print('== B. 从触发借链头的标签 ==')
    chk(d.own_tag_persist(head) == '0', '链头自己的标签持久性 = %s（应 0）'
        % d.own_tag_persist(head))
    chk(d.own_tag_persist(order[1]) is None, '从触发自己没有标签对象')
    for s in order:
        rep = d.trigger_repeat_type(s)
        st = d.trigger_runtime_state(s)
        chk(rep == '0' and st['has_tag'],
            '%s：重复类型=%r、has_tag=%s、verdict=%s'
            % (s, rep, st['has_tag'], st['verdict']))
    st = d.trigger_runtime_state(order[1])
    chk(st.get('tag_owner') == head and '关联触发' in (st.get('text') or ''),
        '运行期状态里说明了"共用链头标签"')

    print('== C. 新读法（标签对象 +0xB0）vs 旧配对读法 ==')
    for p in (SAV2, SAV3):
        if not os.path.exists(p):
            print('  [SKIP] 缺样本 %s' % os.path.basename(p))
            continue
        dd = SaveDoc(p)
        pairs = {}
        for t, g in dd.trigger_tag_pairs():
            pairs.setdefault(g, []).append(t)
        same = diff = 0
        for r in dd.records:
            if r.kind != '触发' or r.sid not in pairs:
                continue
            vs = [dd.tag_persist(t) for t in pairs[r.sid]]
            vs = [v for v in vs if v is not None]
            if not vs:
                continue
            old = '2' if '2' in vs else vs[0]
            own = dd.own_tag_persist(r.sid)
            if own is None:
                continue
            if own == old:
                same += 1
            else:
                diff += 1
                print('      差异 %s：新=%s 旧=%s' % (r.sid, own, old))
        chk(diff == 0, '%s：两者都有值的 %d 条全部一致（差异 %d）'
            % (os.path.basename(p), same, diff))

    print('\n§SOV03 回归：PASS %d / FAIL %d' % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
