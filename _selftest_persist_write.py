# -*- coding: utf-8 -*-
"""_selftest_persist_write.py —— 验证「新写触发可选触发类型 0/1/2」真的写进了标签 +0xB0。

背景（2026-10-20）：标签对象 `+0xB0`(=+176) = 记录帧 +120 = **标签持久性 = 触发类型**
     （0 一次性·任一挂载 / 1 一次性·挂载清空 / 2 可重复）。
     以前 create_trigger 把它硬写成 0；现在加 persistence 参数。
     ⚠ 只有 0 是多年游戏内实测稳定的；1/2 让玩家自行验证，故默认仍是 0。

只做**字节级**验证（不跑游戏）。
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc, TAG_GUID_TAIL      # noqa: E402

BASE = os.path.join(HERE, '_exp', 'sample_AB_已执行与等待.SAV')
fails = []


def check(msg, cond, extra=''):
    print('  %s %s%s' % ('PASS' if cond else 'FAIL', msg, ('  (%s)' % extra) if extra else ''))
    if not cond:
        fails.append(msg)


def tag_persist(doc, tag_id):
    """按标签 ID 找标签对象，读 +0xB0。"""
    for r in doc.records:
        if r.sid == tag_id:
            return struct.unpack_from('<I', doc.plain, r.off - 56 + 0xB0)[0]
    return None


def main():
    print('基档：%s' % BASE)
    for per, want in ((0, 0), (1, 1), (2, 2)):
        d = SaveDoc(BASE)
        d.load()
        pl = d.plain
        info = d.create_trigger('Persist%d' % per, 13, 10, 16, 0, persistence=per)
        print('\n[persistence=%d] 触发 %s / 标签 %s' % (per, info['trigger_id'], info['tag_id']))
        check("返回字段 persistence = %d" % per, info.get('persistence') == per,
              repr(info.get('persistence')))
        v = tag_persist(d, info['tag_id'])
        check('标签对象 +0xB0 = %d' % want, v == want, '实得 %r' % v)
        # 用正向 API 回读（这正是不挂地图时的读取路径）
        back = d.trigger_repeat_type(info['trigger_id'])
        check("trigger_repeat_type 回读 = '%d'" % want, back == str(want), '实得 %r' % back)
        # 记录帧 +120 也应同值
        rec = next((r for r in d.records if r.sid == info['trigger_id']), None)
        if rec:
            rv = int.from_bytes(pl[rec.off + 120:rec.off + 124], 'little') \
                if False else struct.unpack_from('<I', pl, rec.off + 120 - 56 - 120 + 120)[0]
        # 标签记录帧 = 对象帧+56 ⇒ 记录 +120 = 对象 +176
        trec = next((r for r in d.records if r.sid == info['tag_id']), None)
        if trec:
            rv = struct.unpack_from('<I', pl, trec.off + 120)[0]
            check('标签记录帧 +120 = %d' % want, rv == want, '实得 %r' % rv)
        # 新写触发的对象组仍多了一条 ⇒ GUID 尾能认得它
        got = [r.sid for r in d.records if TAG_GUID_TAIL in pl[r.off + 124:r.off + 168]]
        check('新标签被 GUID 尾识别（在 tag_records 里）', info['tag_id'] in got)
        check('新标签名 = 触发名 + " 1"',
              any(r.sid == info['tag_id'] and r.name == 'Persist%d 1' % per
                  for r in d.records))
        check("触发类型中文 = %s" % {'0': '一次性·任一挂载', '1': '一次性·挂载清空',
                                   '2': '可重复'}[str(want)],
              d.REPEAT_TEXT.get(str(want)) == {'0': '一次性·任一挂载', '1': '一次性·挂载清空',
                                               '2': '可重复'}[str(want)])

    print('\n[非法值]')
    d = SaveDoc(BASE)
    d.load()
    for bad in (3, -1, 99):
        try:
            d.create_trigger('Bad%d' % bad, 13, 10, 16, 0, persistence=bad)
            check('persistence=%r 应被拒绝' % bad, False, '竟然没报错')
        except ValueError:
            check('persistence=%r 被拒绝' % bad, True)

    print('\n[默认值]')
    d = SaveDoc(BASE)
    d.load()
    info = d.create_trigger('DefaultPersist', 13, 10, 16, 0)
    check('不传 persistence 默认 0', info.get('persistence') == 0,
          repr(info.get('persistence')))

    print('\n%s' % ('自检结果：全部通过 ✔' if not fails
                    else '自检结果：FAIL %d' % len(fails)))
    for f in fails:
        print('   FAIL:', f)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
