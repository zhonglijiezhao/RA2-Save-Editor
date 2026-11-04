# -*- coding: utf-8 -*-
"""§SOV07 回归：**关联触发的第二种写法**（每条触发都保留自己的标签）。

玩家 2026-11-04 给的样本：`SAVE6443.SAV` + `all01umd.map`
  · 地图 `[Triggers] 01000000=Neutral,01000002,AAAAAA,…` ⇒ 第 2 字段 = 关联触发（下级）
  · 地图 `[Tags] 01000001=2,AAAAAA 1,01000000` ⇒ **每条触发都有自己的标签**
  · 地图 `[Actions] …54,2,01000000,…` ⇒ 行为里有「54 禁止触发·参数=自己」
  · 玩家原话：「每条触发都是重复触发，行为中禁止自身，不用删除下级触发中的标签就可正常执行，
    从下级触发尾部删除也不影响上一级的触发。这种写法不要求删除下级触发标签。」
  对照的旧写法（SAVE5739）：链头挂标签、从触发删掉自己的标签、整链共用那一个。

要验的：
  A. 链路与地图 [Triggers] 一致（3/3）
  B. 三条**各自都有标签**，重复类型 = 地图 [Tags]（2/2/0）
  C. 修好的判定：`trigger_tag_owner()` 不再把"自己有标签的"判成共用链头
     （旧代码会返回链头 AAAAA ⇒ CCCCC 的挂载数/状态全读错）
  D. 行为 54 的参数在 **+100**，指向**自己**（3/3）；`set_action(trigger=…)` 能改写它
  E. 删链尾不影响上级；删中间节点仍摘链
"""
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'core'))
from editor_core import SaveDoc, ACTION_TRIG_TYPES, OBJ_TYPE_OFF, ACT_TRIG_OFF  # noqa
from fa2_tables import ACTION_TYPES                                            # noqa

SAV = r'E:\YURI\SAVE6443.SAV'
MAP = r'E:\YURI\all01umd.map'
CHAIN = ['01000000', '01000002', '01000004']       # AAAAAA → BBBBB → CCCCC
PERSIST = {'01000000': '2', '01000002': '2', '01000004': '0'}
OK = FAIL = 0


def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s' % msg)


def map_links():
    sec, out = None, {}
    for ln in open(MAP, 'r', encoding='gbk', errors='replace'):
        s = ln.strip()
        if s.startswith('['):
            sec = s
            continue
        if sec == '[Triggers]' and '=' in s:
            k, v = s.split('=', 1)
            f = v.split(',')
            out[k.strip()] = None if f[1].strip() in ('<none>', '') else f[1].strip()
    return out


def raw_trigger_ref(d, a):
    pl = d.plain
    p = a['action_off'] + OBJ_TYPE_OFF + ACT_TRIG_OFF
    return int.from_bytes(pl[p:p + 4], 'little')


def main():
    if not (os.path.exists(SAV) and os.path.exists(MAP)):
        print('SKIP：缺样本')
        return 0
    tmp = os.path.join(tempfile.gettempdir(), '_selftest_keep_tag.sav')
    shutil.copy2(SAV, tmp)
    d = SaveDoc(tmp)

    print('== A. 链 = 地图 [Triggers] 第 2 字段 ==')
    want = map_links()
    for s, nxt in zip(CHAIN, CHAIN[1:] + [None]):
        chk(d.trigger_link_sid(s) == nxt,
            '%s 的下级 = %s（地图 %s）' % (s, d.trigger_link_sid(s), want.get(s)))
    chk(d.trigger_seq_info(CHAIN[0])['chain'] == CHAIN,
        '整条链 = %s' % d.trigger_seq_info(CHAIN[0])['chain'])

    print('== B. 每条都有自己的标签，重复类型各自独立 ==')
    for s in CHAIN:
        o = d.trigger_objects(s)
        chk(bool(o['tag_key']), '%s 有自己的标签对象（%s）' % (s, o['tag_key']))
        chk(d.own_tag_persist(s) == PERSIST[s],
            '%s 自己的持久性 = %s' % (s, d.own_tag_persist(s)))
        chk(d.trigger_repeat_type(s) == PERSIST[s],
            '%s 重复类型读出 %s' % (s, d.trigger_repeat_type(s)))

    print('== C. 不再误判"共用链头标签"（§SOV07 修的 bug）==')
    chk(d.trigger_tag_owner(CHAIN[0]) is None, '链头自己没有共用对象')
    for s in CHAIN[1:]:
        chk(d.trigger_tag_owner(s) is None,
            '%s 有自己的标签 ⇒ 不借链头的（旧代码会错报 AAAAA）' % s)
        chk(d.trigger_tag_sid(s) == s, '%s 的标签类字段读自己那条' % s)

    print('== D. 行为「54 禁止触发」参数 = 自己（+100 的实例键）==')
    chk(54 in ACTION_TRIG_TYPES and 53 in ACTION_TRIG_TYPES,
        '触发型行为集合含 53/54（共 %d 类）' % len(ACTION_TRIG_TYPES))
    for s in CHAIN:
        acts = [a for a in d.trigger_actions(s) if a['type'] == 54]
        chk(len(acts) == 1, '%s 有一条「54 禁止触发」' % s)
        if not acts:
            continue
        a = acts[0]
        chk(a.get('trigger') == s,
            '  参数指向 = %s（%s）' % (a.get('trigger'), '自己' if a.get('trigger') == s else '别人'))
        chk(raw_trigger_ref(d, a) == d._sid2inst_key(s), '  原始 +100 = 自己的实例键')

    print('== E. 改这条参数：写 +100、读回一致 ==')
    a = [x for x in d.trigger_actions(CHAIN[0]) if x['type'] == 54][0]
    d.set_action(a, atype=54, arg=0, trigger=CHAIN[2])
    a2 = [x for x in d.trigger_actions(CHAIN[0]) if x['type'] == 54][0]
    chk(a2.get('trigger') == CHAIN[2], '改成 %s 后读回 = %s' % (CHAIN[2], a2.get('trigger')))
    chk(raw_trigger_ref(d, a2) == d._sid2inst_key(CHAIN[2]), '写入落在 +100')
    d.set_action(a2, atype=54, arg=0, trigger=CHAIN[0])
    a3 = [x for x in d.trigger_actions(CHAIN[0]) if x['type'] == 54][0]
    chk(a3.get('trigger') == CHAIN[0], '改回自己（可逆）')
    os.remove(tmp)

    print('== F. 删链尾不影响上级 / 删中间仍摘链 ==')
    tmp2 = os.path.join(tempfile.gettempdir(), '_selftest_keep_tag2.sav')
    shutil.copy2(SAV, tmp2)
    d2 = SaveDoc(tmp2)
    d2.remove_trigger(CHAIN[2])
    chk(d2.trigger_link_sid(CHAIN[1]) is None, '删链尾后 %s 变链尾' % CHAIN[1])
    chk(d2.trigger_link_sid(CHAIN[0]) == CHAIN[1], '上级 %s 的下级不受影响' % CHAIN[0])
    chk(d2.trigger_seq_info(CHAIN[1])['up'] == CHAIN[0], '余下链路完好')
    os.remove(tmp2)

    tmp3 = os.path.join(tempfile.gettempdir(), '_selftest_keep_tag3.sav')
    shutil.copy2(SAV, tmp3)
    d3 = SaveDoc(tmp3)
    d3.remove_trigger(CHAIN[1])
    chk(d3.trigger_link_sid(CHAIN[0]) == CHAIN[2], '删中间：%s 直接接到 %s' % (CHAIN[0], CHAIN[2]))
    os.remove(tmp3)

    print('== G. 上级失效的连带范围（玩家两次实验后定案）==')
    d4 = SaveDoc(SAV)
    chk(d4.trigger_downstream(CHAIN[0]) == CHAIN[1:],
        '链头后面 = %s' % d4.trigger_downstream(CHAIN[0]))
    chk(d4.trigger_downstream(CHAIN[2]) == [], '链尾后面没有东西')
    chk(d4.trigger_dependents(CHAIN[0]) == [],
        '本条名下"会被拖死"的下级 = 空（B/C 都有自己的标签）')
    tmp4 = os.path.join(tempfile.gettempdir(), '_selftest_keep_tag4.sav')
    shutil.copy2(SAV, tmp4)
    d5 = SaveDoc(tmp4)
    info = d5.remove_trigger(CHAIN[0])
    # ★玩家 2026-11-04 第二次修正：有自己标签的下级不受影响 ⇒ 连带名单必须为空
    chk(info.get('orphans') == [],
        '删链头 ⇒ 连带名单 = %s（B/C 各有标签，照常执行）' % info.get('orphans'))
    chk(not (d5.trigger_link_parents().get(CHAIN[1]) or []),
        '链被摘断：B 现在没有上级')
    chk(d5.trigger_has_own_tag(CHAIN[1]) and d5.trigger_has_own_tag(CHAIN[2]),
        'B/C 的标签对象都还在 ⇒ 它们不靠链活着')
    os.remove(tmp4)

    print('\n§SOV07 回归：PASS %d / FAIL %d' % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
