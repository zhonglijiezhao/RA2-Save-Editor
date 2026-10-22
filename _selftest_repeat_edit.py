# -*- coding: utf-8 -*-
"""_selftest_repeat_edit.py —— 验证"改已有触发的触发类型"（原地写标签对象 +0xB0）。

要点：
  ① 触发类型 = 标签对象 `+0xB0`（= 记录帧 +120），改它是**原地改写 4 字节**（不动长度）；
  ② 帧差校验 `tag_obj_off()` 必须能挡住"帧差假设失效"的情况；
  ③ 非法值 / 找不到标签必须报错而不是乱写。
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc, TAG_GUID_TAIL      # noqa: E402

BIG = 'C:/Users/zhao5/Desktop/工程师/SAVE48DB.SAV'
BIGMAP = 'C:/Users/zhao5/Desktop/工程师/all01umd.map'
AB = os.path.join(HERE, '_exp', 'sample_AB_已执行与等待.SAV')

fails = []


def check(msg, cond, extra=''):
    print('  %s %s%s' % ('PASS' if cond else 'FAIL', msg, ('  (%s)' % extra) if extra else ''))
    if not cond:
        fails.append(msg)


def obj_persist(doc, tag_sid):
    for r in doc.records:
        if r.sid == tag_sid:
            o = doc.tag_obj_off(r.off)
            if o is None:
                return None
            return struct.unpack_from('<I', doc.plain, o + 0xB0)[0]
    return None


def main():
    print('=== 1) 大档：把一条"单次(0)"改成"重复(2)"再改回，全部 82 条真值不能被动过 ===')
    d = SaveDoc(BIG)
    d.load()
    tags = d.tag_records()
    check('先认出 82 个标签', len(tags) == 82, len(tags))
    before = {sid: int.from_bytes(d.plain[off + 120:off + 124], 'little')
              for sid, _n, off in tags}
    # 找一条 persistence=0 的触发
    t0 = None
    for trig, w in _truth().items():
        if str(w) == '0':
            t0 = trig
            break
    check('找到一条单次型触发', t0 is not None, t0)
    info = d.set_trigger_repeat_type(t0, 2)
    check('写成功（changed>=1）', info['changed'] >= 1, info)
    check("回读 trigger_repeat_type = '2'", d.trigger_repeat_type(t0) == '2',
          d.trigger_repeat_type(t0))
    # 只有目标触发的标签被改
    after = {sid: int.from_bytes(d.plain[off + 120:off + 124], 'little')
             for sid, _n, off in tags}
    diff = [s for s in before if before[s] != after[s]]
    check('只改了目标触发的标签（其余 81 条原样）',
          set(diff) <= set(info['tags']) and len(diff) == len(info['tags']),
          'diff=%s tags=%s' % (diff, info['tags']))
    d.set_trigger_repeat_type(t0, 0)
    check('改回 0 后与初值完全一致', before == {
        sid: int.from_bytes(d.plain[off + 120:off + 124], 'little')
        for sid, _n, off in tags})

    print('\n=== 2) 本地 AB 样本：三种类型逐个改 + 互不影响 ===')
    d2 = SaveDoc(AB)
    d2.load()
    # 先记下所有触发的当前类型，确保只动目标那条
    allt = {r.sid: d2.trigger_repeat_type(r.sid) for r in d2.records if r.kind == '触发'}
    trig2 = next(r.sid for r in d2.records if r.kind == '触发')
    check('AB 样本能读出触发类型', allt.get(trig2) in ('0', '1', '2'), allt.get(trig2))
    # 新建一条触发（持久性 0）当"另一条"，验证互不影响
    info_new = d2.create_trigger('OtherTrig', 13, 10, 16, 0, persistence=0)
    other = info_new['trigger_id']
    check('新建的 OtherTrig 类型 = 0', d2.trigger_repeat_type(other) == '0',
          d2.trigger_repeat_type(other))
    for v in (2, 1, 0, 2):
        d2.set_trigger_repeat_type(trig2, v)
        check('%s 改成 %d 后回读 = %d' % (trig2, v, v),
              d2.trigger_repeat_type(trig2) == str(v), d2.trigger_repeat_type(trig2))
        check('同时 OtherTrig 未被影响', d2.trigger_repeat_type(other) == '0',
              d2.trigger_repeat_type(other))
    # 改 OtherTrig 也不影响第一条
    d2.set_trigger_repeat_type(other, 2)
    check('改 OtherTrig 后它 = 2', d2.trigger_repeat_type(other) == '2')
    check('且第一条仍 = 2', d2.trigger_repeat_type(trig2) == '2')

    print('\n=== 2b) SAVE18BE 真值（玩家给过 0/1/2；文件不在则 SKIP） ===')
    if os.path.exists('E:/YURI/SAVE18BE.SAV'):
        d2b = SaveDoc('E:/YURI/SAVE18BE.SAV')
        d2b.load()
        for trig, want in (('01000000', '0'), ('01000005', '1'), ('01000007', '2')):
            check('初始 %s = %s' % (trig, want), d2b.trigger_repeat_type(trig) == want,
                  d2b.trigger_repeat_type(trig))
        for v in (2, 1, 0):
            d2b.set_trigger_repeat_type('01000000', v)
            check('A 改成 %d 后回读 = %d' % (v, v),
                  d2b.trigger_repeat_type('01000000') == str(v))
        check('改 A 不动 B/C',
              d2b.trigger_repeat_type('01000005') == '1'
              and d2b.trigger_repeat_type('01000007') == '2')
    else:
        print('  [SKIP] E:\\YURI\\SAVE18BE.SAV 不存在（玩家已移走）；'
              '真值校验请在有该档时跑 _verify_repeat.py')

    print('\n=== 3) 帧差校验必须能挡住错位 ===')
    d3 = SaveDoc(AB)
    d3.load()
    tr = next(r for r in d3.records if r.kind == '触发')
    toffs = [r.off for r in d3.records
             if TAG_GUID_TAIL in d3.plain[r.off + 124:r.off + 168]]
    r0 = toffs[0]
    check('正常标签 tag_obj_off 有值', d3.tag_obj_off(r0) is not None)
    check('正常标签的对象帧 = 记录帧 - 56', d3.tag_obj_off(r0) == r0 - 56)
    # 同一数组里**别的**标签偏移是合法的（会返回它自己的对象帧），不能当成"错位"
    for o in toffs[1:]:
        check('另一条标签的偏移返回它自己的帧（差 %+d）' % (r0 - o),
              d3.tag_obj_off(o) == o - 56)
    # 真正要挡的：**不是标签记录帧**的偏移
    for delta in (56, -56, 112, -112):
        check('大幅错位 %+d 被拒绝（None）' % delta, d3.tag_obj_off(r0 + delta) is None)
    check('触发记录的偏移被拒绝（None）', d3.tag_obj_off(tr.off) is None)
    check('负偏移被拒绝（None）', d3.tag_obj_off(-10) is None)
    check('越界偏移被拒绝（None）', d3.tag_obj_off(len(d3.plain)) is None)
    # 最后一道闸：把持久性字节改成非法值后，写入必须被拒绝
    obj = d3.tag_obj_off(r0)
    keep = bytes(d3.plain[obj + 0xB0:obj + 0xB4])
    d3.plain[obj + 0xB0:obj + 0xB4] = b'\x63\x00\x00\x00'      # 99
    sid0 = next(r.sid for r in d3.records if r.off == r0)
    trig0 = dict(d3.trigger_tag_pairs()).get(sid0)
    if trig0:
        try:
            d3.set_trigger_repeat_type(trig0, 2)
            check('持久性字节异常时拒绝写入', False, '没报错')
        except RuntimeError:
            check('持久性字节异常时拒绝写入', True)
    d3.plain[obj + 0xB0:obj + 0xB4] = keep
    check('还原后值不变', keep == bytes(d3.plain[obj + 0xB0:obj + 0xB4]))

    print('\n=== 4) 错误路径 ===')
    for bad in (3, -1, 99, 'x'):
        try:
            d3.set_trigger_repeat_type(tr.sid, bad)
            check('persistence=%r 应报错' % (bad,), False, '没报错')
        except (ValueError, TypeError):
            check('persistence=%r 被拒绝' % (bad,), True)
    try:
        d3.set_trigger_repeat_type('FFFFFFFF', 2)
        check('不存在的触发应报错', False, '没报错')
    except Exception:
        check('不存在的触发被拒绝', True)

    print('\n=== 5) 往返保存后再读（确保真写进了 plain，不是缓存） ===')
    d4 = SaveDoc(AB)
    d4.load()
    trig4 = next(r for r in d4.records if r.kind == '触发')
    d4.set_trigger_repeat_type(trig4.sid, 2)
    out = os.path.join(HERE, '_exp', '_tmp_repeat_rt.sav')
    try:
        d4.save(out)
        d5 = SaveDoc(out)
        d5.load()
        check('保存重开后仍是 2', d5.trigger_repeat_type(trig4.sid) == '2',
              d5.trigger_repeat_type(trig4.sid))
    except Exception as e:
        check('保存往返失败: %s' % e, False)
    finally:
        if os.path.exists(out):
            os.remove(out)

    print('\n%s' % ('自检结果：全部通过 ✔' if not fails else '自检结果：FAIL %d' % len(fails)))
    for f in fails:
        print('   FAIL:', f)
    return 1 if fails else 0


def _truth():
    from map_ini import MapIni
    m = MapIni(BIGMAP)
    out = {}
    for k, v in m.tags.items():
        f = [x.strip() for x in v.split(',')]
        out[f[-1]] = f[0]
    return out


if __name__ == '__main__':
    sys.exit(main())
