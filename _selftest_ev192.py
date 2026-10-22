# -*- coding: utf-8 -*-
"""§192 自检：TagTypeClass（184B 标签记录 / 164B 对象体）字段表。

硬证据来源（写死在下面的期望值）：
  · RTTI → vtable 0x7F45C4 / Size() 0xA4 / ClassID 0x2D
  · `TagTypeClass::WriteINI` @0x6E6160：sprintf("%d,%s,%s", this+0x9C, this+0x64, trig->0x24)
  · 语料 SAVE4CA2（82 个标签）与「触发C为地编写入.sav」（FA2 三件套）

本自检锁死：
  [1] 记录定位：184B 记录、4 个 vtable 与引擎一致
  [2] +0x98 = 序号（0..N-1 严格递增）
  [3] +0x9C = 持久性，且与 `tag_persist()` 逐条一致（旧代码的读法是对的）
  [4] +0xA0 = 触发实例的键，且能解析回触发 ID
  [5] +0x24 = 8 字符 ID 串；+0x64 = GBK 名字
  [6] 垃圾槽 +0x14/+0x20/+0x2C..+0x5F 在 FA2 产物里必须全 0（克隆时要清的目标）
  [7] 写名字：只改名字区、长度不变、回读一致；超长必须拒绝
  [8] scrub_tag_stale 能把脏槽清零；tag_health 在健康档上无告警
"""
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))
import editor_core as ec
from _paths import need_sav

BIG = need_sav('SAVE4CA2.SAV')            # 82 个标签的真实任务档
FA2 = need_sav('触发C为地编写入.sav')       # FA2 亲手写的三件套（0/1/2 序号的极小样本）

n_ok = n_bad = 0
fails = []


def ck(name, cond, extra=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('  PASS  %s' % name)
    else:
        n_bad += 1
        fails.append(name)
        print('  FAIL  %s   %s' % (name, extra))


print('=== [1] 记录定位 / vtable ===')
d = ec.SaveDoc(BIG)
ts = d.tag_type_records()
ck('SAVE4CA2 找到 82 个标签', len(ts) == 82, '实际 %d' % len(ts))
ck('4 个 vtable 全部与引擎一致',
   all(t['ok_vt'] for t in ts),
   '异常 %d 条' % sum(1 for t in ts if not t['ok_vt']))
ck('每 184B 一条、间隔恒定',
   all(ts[i + 1]['rec'] - ts[i]['rec'] == 184 for i in range(len(ts) - 1)))
ck('对象体起点 = 记录 + 20', all(t['obj'] == t['rec'] + 20 for t in ts))

print('=== [2] +0x98 序号 ===')
ck('序号 0..81 严格递增', [t['index'] for t in ts] == list(range(82)),
   '%s…' % [t['index'] for t in ts[:8]])

print('=== [3] +0x9C 持久性 ===')
dist = {}
for t in ts:
    dist[t['persist']] = dist.get(t['persist'], 0) + 1
ck('持久性分布 = {0:73, 2:9}', dist == {0: 73, 2: 9}, str(dist))
ck('与 tag_persist() 逐条一致',
   all(str(t['persist']) == d.tag_persist(t['sid']) for t in ts))

print('=== [4] +0xA0 = 触发键 ===')
ck('每个标签都能解析回触发 ID',
   all(t['trig_sid'] for t in ts),
   '解析失败 %d 条' % sum(1 for t in ts if not t['trig_sid']))
ck('解析出的触发 ID 形状正确（0100000X）',
   all(re.match(r'^01[0-9A-F]{6}$', t['trig_sid'] or '') for t in ts))

print('=== [5] ID 串 / 名字 ===')
ck('ID 串 8 字符且 01 开头',
   all(re.match(r'^01[0-9A-F]{6}$', t['sid']) for t in ts),
   ts[0]['sid'])
ck('名字全部非空', all(t['name'].strip() for t in ts))
ck('中文名字能正确解码（GBK）', any('\u4e00' <= c <= '\u9fff' for c in ts[0]['name']),
   ts[0]['name'])

print('=== [6] 残留区（未使用槽）===')
dirty = 0
for t in ts:
    for lo, hi in ec.TAG_TYPE_STALE:
        for o in range(lo, hi, 4):
            if struct.unpack_from('<I', d.plain, t['obj'] + o)[0]:
                dirty += 1
print('  INFO  长期档 SAVE4CA2 的 82 个标签里有 %d 个残留槽非 0（旧键/裸指针，游戏照跑 ⇒ 无害）'
      % dirty)

print('=== 极小样本（FA2 三件套 / 刚建的干净标签）===')
d2 = ec.SaveDoc(FA2)
t2 = d2.tag_type_records()
ck('三个标签 + 名字 A/B/C', [t['name'] for t in t2] == ['A 1', 'B 1', 'C 1'],
   str([t['name'] for t in t2]))
ck('持久性全 0（与地图 [Tags] 对拍一致）', [t['persist'] for t in t2] == [0, 0, 0])
ck('触发 ID = 01000000/02/04',
   [t['trig_sid'] for t in t2] == ['01000000', '01000002', '01000004'],
   str([t['trig_sid'] for t in t2]))
ck('刚建的标签残留区全 0（新触发该长成这样）',
   all(not struct.unpack_from('<I', d2.plain, t['obj'] + o)[0]
       for t in t2 for lo, hi in ec.TAG_TYPE_STALE for o in range(lo, hi, 4)))

print('=== [7] 写名字 ===')
d3 = ec.SaveDoc(BIG)
before = bytes(d3.plain)
sid0 = d3.tag_type_records()[0]['sid']
r = d3.set_tag_name(sid0, '教材-改名牌测试')
after = bytes(d3.plain)
diff = [i for i in range(len(before)) if before[i] != after[i]]
ck('明文长度不变', len(before) == len(after))
ck('只改名字区（%d 字节以内）' % ec.TAG_TYPE_NAME_LEN,
   len(diff) <= ec.TAG_TYPE_NAME_LEN, '改动 %d 字节' % len(diff))
ck('回读一致', d3.tag_fields(sid0)['name'] == '教材-改名牌测试',
   d3.tag_fields(sid0)['name'])
try:
    d3.set_tag_name(sid0, 'X' * 60)
    ck('超长名字被拒绝', False, '没有抛错')
except RuntimeError:
    ck('超长名字被拒绝', True)

print('=== [8] scrub / health ===')
d4 = ec.SaveDoc(BIG)
t4 = d4.tag_type_records()[0]
d4.plain[t4['obj'] + 0x2C:t4['obj'] + 0x30] = b'\x11\x22\x33\x44'
n = d4.scrub_tag_stale(t4['rec'])
ck('scrub_tag_stale 清零脏槽并计数', n >= 1 and
   d4.plain[t4['obj'] + 0x2C:t4['obj'] + 0x30] == b'\x00' * 4, 'n=%d' % n)
h = d4.tag_health()
ck('tag_health：82 条、序号无异常、ID 不重复',
   h['count'] == 82 and not h['idx_bad'] and not h['dup_sid'], str(h)[:120])
ck('tag_health：vtable 全健康', not h['bad_vt'])

print('\n§192 自检：PASS %d / FAIL %d' % (n_ok, n_bad))
if n_bad:
    print('失败项：' + ', '.join(fails))
sys.exit(1 if n_bad else 0)
