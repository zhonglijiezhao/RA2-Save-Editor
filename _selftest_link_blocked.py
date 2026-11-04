# -*- coding: utf-8 -*-
"""§SOV07 回归：**上级被禁用时，到底谁会被拖死**（玩家 2026-11-04 两次实验后定案）。

结论（两个样本合起来才看得全）：
  · 写 A（SAVE5739）：从触发**没有**自己的标签、共用链头那一个
    ⇒ 禁用/删除上级，它**永远不触发**（命全靠链）。
  · 写 B（SAVE6443）：每条触发**保留**自己的标签
    ⇒ 禁用/删除上级**不影响**它，它按自己的标签执行。
玩家第一次用 SAVE4823 得出「有标签也必失效」，后来自己确认那次实验不严谨、已撤回。

本自检：
  A. SAVE6443：B/C 都有自己标签 ⇒ 不受上级影响（blocked=None、dependents 为空）
  B. SAVE5739：把链头禁用后，无标签的下级被拖死（blocked=链头、verdict=blocked）
  C. 再启用链头 ⇒ 解除
  D. dependents 只列没标签的（不会把有标签的算进去）
  E. SAVE4823（那次不严谨的实验档）不再被当成"有标签也被拖死"的证据
"""
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'core'))
from editor_core import SaveDoc   # noqa: E402

KEEP = r'E:\YURI\SAVE6443.SAV'       # 写 B：每条各留标签
SHARE = r'E:\YURI\SAVE5739.SAV'      # 写 A：从触发无标签
EXPR = r'E:\YURI\SAVE4823.SAV'       # 那次不严谨的实验档
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
    if not (os.path.exists(KEEP) and os.path.exists(SHARE)):
        print('SKIP：缺样本')
        return 0

    print('== A. 写 B（各留标签 + 计时型事件）：上级禁用不影响下级（§SOV08 权威口径）==')
    d = SaveDoc(KEEP)
    a, b, c = '01000000', '01000002', '01000004'
    chk(d.trigger_has_own_tag(b) and d.trigger_has_own_tag(c), 'B/C 都有自己的标签')
    chk(d.trigger_events_self_contained(b) is True,
        'B 的事件是 13 流逝时间 ⇒ 自足型（不需要挂接对象）')
    chk(d.trigger_events_self_contained(a) is False,
        'A 的事件是 1 进入事件 ⇒ 需要挂接对象')
    chk(d.trigger_blocked_by(b) is None, 'B 不被上级拖累')
    chk(d.trigger_blocked_by(c) is None, 'C 不被上级拖累')
    det = d.trigger_detail(a)
    d.set_trigger_flags(a, disabled=True, easy=det['easy'],
                        normal=det['normal'], hard=det['hard'])
    d.invalidate_caches(rescan=True)
    chk(d.trigger_is_disabled(a) is True, '把 A 禁用（模拟）')
    chk(d.trigger_blocked_by(b) is None, 'A 禁用后 B 仍不受影响（有自己标签）')
    chk(d.trigger_blocked_by(c) is None, 'A 禁用后 C 仍不受影响')
    chk(d.trigger_dependents(a) == [], 'A 名下"会被拖死"的下级 = 空')

    print('== B/C/D. 写 A（共用标签）：链头禁用 ⇒ 无标签的下级被拖死 ==')
    tmp = os.path.join(tempfile.gettempdir(), '_selftest_blocked2.sav')
    shutil.copy2(SHARE, tmp)
    d2 = SaveDoc(tmp)
    # 挑一条"链头**启用**、下级**无标签**"的链（本档最长那条 28 条整串都是禁用的，
    # 直接拿来用会把"禁用前就没被拖住"这条断言判错 —— 2026-11-04 自己踩过）
    head, seq = None, None
    for _h, _s in d2.trigger_link_maps()['chain'].items():
        if len(_s) < 2 or d2.trigger_is_disabled(_h):
            continue
        if any(d2.trigger_has_own_tag(x) for x in _s[1:]):
            continue
        head, seq = _h, _s
        break
    if head is None:
        print('  [SKIP] 本档找不到"链头启用 + 下级全无标签"的链')
        return 0
    print('     用链：%s（%d 条，链头启用、下级无标签）' % (head, len(seq)))
    chk(d2.trigger_has_own_tag(seq[0]) is True, '链头自己有标签')
    chk(not d2.trigger_has_own_tag(seq[1]), '第 2 条**没有**自己的标签（写 A 的从触发）')
    chk(d2.trigger_blocked_by(seq[1]) is None, '链头没禁用时，它没被拖住')
    det2 = d2.trigger_detail(head)
    d2.set_trigger_flags(head, disabled=True, easy=det2['easy'],
                         normal=det2['normal'], hard=det2['hard'])
    d2.invalidate_caches(rescan=True)
    chk(d2.trigger_blocked_by(seq[1]) == head, '禁用链头 ⇒ 第 2 条被它拖住')
    st = d2.trigger_runtime_state(seq[1])
    chk(st.get('verdict') == 'blocked', 'verdict = %s' % st.get('verdict'))
    chk('永远不会触发' in (st.get('text') or ''), '文案点明永不触发')
    chk(d2.trigger_dependents(head) == seq[1:],
        '被拖死的名单 = 整串（它们都没标签）')
    det3 = d2.trigger_detail(head)
    d2.set_trigger_flags(head, disabled=False, easy=det3['easy'],
                         normal=det3['normal'], hard=det3['hard'])
    d2.invalidate_caches(rescan=True)
    chk(d2.trigger_blocked_by(seq[1]) is None, '再启用链头 ⇒ 解除')
    chk(d2.trigger_runtime_state(seq[1]).get('verdict') != 'blocked',
        '状态回落到 %s' % d2.trigger_runtime_state(seq[1]).get('verdict'))
    os.remove(tmp)

    print('== E. SAVE4823（实验不严谨那档）：不再当作"有标签也被拖死" ==')
    if os.path.exists(EXPR):
        d3 = SaveDoc(EXPR)
        chk(d3.trigger_has_own_tag('01000002'), 'BBBB 自己是有标签的')
        chk(d3.trigger_blocked_by('01000002') is None,
            '⇒ 上级 A 禁用，也不判它"被拖死"（旧逻辑会误判）')
        chk(d3.trigger_dependents('01000000') == [], 'A 名下没有被拖死的下级')
    else:
        print('  [SKIP] 缺 SAVE4823')

    print('\n§SOV07-B 回归：PASS %d / FAIL %d' % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
