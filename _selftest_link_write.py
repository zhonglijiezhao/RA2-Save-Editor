# -*- coding: utf-8 -*-
"""§SOV06 回归：**下级触发（关联触发链）的读写**。

背景（2026-11-04 玩家：「下级触发的显示和写入 现在还差什么」）：
  存档记录帧 `+132` = 下级触发的**实例键**（0 = 链尾）。实档不变式（334 条逐条核对）：
     有标签 ∧ 有上级 = 0 ｜ 无标签 ∧ 有上级 = 202 ｜ 无标签 ∧ 无上级 = 0
     标签数 132 = 触发数 334 − 从触发数 202  ⇒ **一个标签 = 一条链**

本自检覆盖：
  A. 读：trigger_seq_info / trigger_link_parents 与实档一致
  B. 写：改 +132 只动 4 字节；改回去**逐字节还原**
  C. 防环：链尾指回链头 / 自指 ⇒ 拒绝且一个字节都没动
  D. dry=True 只校验不落笔
  E. 删链中间节点 ⇒ 上级自动接到下级（摘链，不留野键）
  F. 新建/复制触发 ⇒ +132 = 0（不继承模板的链接）
"""
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'core'))
from editor_core import SaveDoc, TRIG_REC_LINK_OFF   # noqa: E402

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


def raw_link(d, sid):
    r = d.trigger_rec(sid)
    p = r.off + TRIG_REC_LINK_OFF
    return d.plain[p:p + 4]


def main():
    if not os.path.exists(SAV):
        print('SKIP：缺样本 %s' % SAV)
        return 0
    tmp = os.path.join(tempfile.gettempdir(), '_selftest_link.sav')
    shutil.copy2(SAV, tmp)
    d = SaveDoc(tmp)
    snap = bytes(d.plain)

    print('== A. 读：链的位置 ==')
    maps = d.trigger_link_maps()
    head = max(maps['chain'], key=lambda h: len(maps['chain'][h]))
    seq = maps['chain'][head]
    print('     最长链（%d 条）：%s' % (len(seq), ' → '.join(seq)))
    si = d.trigger_seq_info(seq[0])
    chk(si['is_head'] and si['up'] is None and si['down'] == seq[1],
        '%s：链头、无上级、下级 = %s' % (seq[0], si['down']))
    mid = seq[len(seq) // 2]
    sm = d.trigger_seq_info(mid)
    chk(sm['up'] == seq[seq.index(mid) - 1] and sm['down'] == seq[seq.index(mid) + 1],
        '%s：上级 %s / 下级 %s' % (mid, sm['up'], sm['down']))
    st = d.trigger_seq_info(seq[-1])
    chk(st['down'] is None, '链尾 %s 没有下级' % seq[-1])
    chk(d.trigger_link_parents().get(seq[-1]) == [seq[-2]],
        '链尾的上级 = %s' % d.trigger_link_parents().get(seq[-1]))
    chk(all(len(v) == 1 for v in d.trigger_link_parents().values()),
        '没有任何触发被两条链同时指着（实档不变式）')

    print('== B. 改写 + 还原（逐字节）==')
    d.set_trigger_link(seq[0], None)
    chk(raw_link(d, seq[0]) == b'\x00\x00\x00\x00', '清成链尾后 +132 = 0')
    chk(d.trigger_link_sid(seq[0]) is None, 'trigger_link_sid 变 None')
    chk(d.trigger_seq_info(seq[1])['up'] is None, '原下级 %s 现在没有上级' % seq[1])
    d.set_trigger_link(seq[0], seq[1])
    chk(raw_link(d, seq[0]) != b'\x00\x00\x00\x00', '改回来 +132 非 0')
    chk(bytes(d.plain) == snap, '整档明文与初始**逐字节一致**（可逆）')
    chk(len(d.plain) == len(snap), '长度未变（只写 4 字节）')

    print('== C. 防环 / 自指 ==')
    before = bytes(d.plain)
    for args, why in (((seq[0], seq[0]), '自指'),
                      ((seq[-1], seq[0]), '链尾指回链头')):
        try:
            d.set_trigger_link(*args)
            chk(False, '%s 应该被拒绝' % why)
        except RuntimeError as e:
            chk(True, '%s 被拒绝：%s' % (why, str(e)[:40]))
    chk(bytes(d.plain) == before, '两次拒绝后明文一个字节都没动')

    print('== D. dry=True 不落笔 ==')
    r = d.set_trigger_link(seq[0], None, dry=True)
    chk(r['changed'] and r['new_sid'] is None, 'dry 报告"会改"，目标 = 链尾')
    chk(bytes(d.plain) == before, 'dry 之后明文未变')

    print('== E. 删链中间节点 ⇒ 上级接到下级（摘链）==')
    tmp2 = os.path.join(tempfile.gettempdir(), '_selftest_link2.sav')
    shutil.copy2(SAV, tmp2)
    d2 = SaveDoc(tmp2)
    seq2 = d2.trigger_link_maps()['chain'][head]
    a, b, c = seq2[0], seq2[1], seq2[2]
    info = d2.remove_trigger(b)
    chk(d2.trigger_rec(b) is None, '中间节点 %s 已删除' % b)
    chk(d2.trigger_link_sid(a) == c, '%s 的上级 %s 已接到 %s' % (c, a, c))
    chk(not d2.trigger_link_parents().get(c, []) == [], '%s 仍有上级' % c)
    chk([x for x in (d2.trigger_link_parents().get(c) or [])] == [a],
        '摘链后 %s 的上级恰为 %s（不是野键）' % (c, a))
    chk(d2.trigger_link_sid(c) == seq2[3] if len(seq2) > 3 else True,
        '下游链接保持原样')
    os.remove(tmp2)

    print('== F. 新建 / 复制不继承链接 ==')
    info = d.create_trigger('链接自检新触发', 13, 30, 56, 49, persistence=0)
    chk(raw_link(d, info['trigger_id']) == b'\x00\x00\x00\x00',
        '新建 %s 的 +132 = 0（链头）' % info['trigger_id'])
    cp = d.copy_trigger(seq[1])
    chk(raw_link(d, cp['sid']) == b'\x00\x00\x00\x00',
        '复制从触发 %s → %s 的 +132 = 0（没有偷偷进链）' % (seq[1], cp['sid']))

    print('== G. 删链头 ⇒ 报孤儿 ==')
    tmp3 = os.path.join(tempfile.gettempdir(), '_selftest_link3.sav')
    shutil.copy2(SAV, tmp3)
    d3 = SaveDoc(tmp3)
    seq3 = d3.trigger_link_maps()['chain'][head]
    info = d3.remove_trigger(seq3[0])
    chk(seq3[0] not in [r.sid for r in d3.records if r.kind == '触发'],
        '链头 %s 已删除' % seq3[0])
    chk(info.get('orphans') == seq3[1:],
        '返回的孤儿名单 = 原链余下 %d 条' % (len(seq3) - 1))
    chk(d3.trigger_seq_info(seq3[1])['up'] is None,
        '%s 现在没有上级（链头没了）' % seq3[1])
    chk(not d3.trigger_objects(seq3[1]).get('tag_key'),
        '它自己也没有标签 ⇒ 确实没人驱动（工具如实报告）')
    os.remove(tmp3)

    os.remove(tmp)
    print('\n§SOV06 回归：PASS %d / FAIL %d' % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
