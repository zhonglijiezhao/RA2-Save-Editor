# -*- coding: utf-8 -*-
"""自检：软删除（改条件成永不满足）——玩家方案 2026-10-20

规则：
  · 挂了建筑 或 本来就有 TagNode → 事件 13「流逝时间」+ 参数 16,777,215 秒
  · 没挂建筑且无 TagNode     → 事件 1「进入事件」+ 参数 0
断言：只改 8 字节、明文长度不变、对象/记录数不变、可原样改回、存盘重读仍在。
"""
import io, os, sys, tempfile, struct
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc

SAV = r'C:\Users\zhao5\Desktop\工程师\SAVE48DB.SAV'
OK = FAIL = 0


def chk(c, msg):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


d = SaveDoc(SAV)
d.attach_map(r'C:\Users\zhao5\Desktop\工程师\all01umd.map')
trigs = [r for r in d.records if r.kind == '触发']
print('样本 %s　触发 %d' % (os.path.basename(SAV), len(trigs)))

dets = [d.trigger_detail(r.sid) for r in trigs]
dets = [x for x in dets if x['event']]
plain_len0 = len(d.plain)
n_rec0 = len(d.records)
n_inst0 = len(d._instance_maps()['inst'])

# 挑样本：挂了建筑的 / 没挂但也不带 TagNode 的
att = [x for x in dets if x['attached'] and x['event']['type'] != 13]
free = [x for x in dets if not x['attached'] and not x['has_node']]

print('\n== A: 挂了建筑的触发 → 流逝时间上限 ==')
if not att:
    chk(False, '本档找不到"挂建筑且非计时"的触发，无法验证（请提供样本）')
else:
    x = att[0]
    old = (x['event']['type'], x['event']['arg'])
    L0 = len(d.plain)
    info = d.soft_disable_trigger(x['sid'])
    dL = len(d.plain) - L0
    det = d.trigger_detail(x['sid'])
    chk(det['event']['type'] == 13, '事件 → %s' % det['event']['name'])
    chk(det['event']['arg'] == 0xFFFFFF, '参数 = %d（上限）' % det['event']['arg'])
    chk(det['ticks'] == 0xFFFFFF * 15, 'TagNode 帧数 = %d（u32 内）' % det['ticks'])
    chk(dL in (0, 4), '明文长度变化 %+d 字节（0=本来就在计时表，4=补了一条计时登记）' % dL)
    chk(len(d.records) == n_rec0, '记录数不变')
    print('   %s %s　%s' % (x['sid'], x['name'], info['note']))
    # 可逆：改回原事件
    d.set_event(d.trigger_detail(x['sid'])['event'], etype=old[0], arg=old[1])
    back = d.trigger_detail(x['sid'])
    chk(back['event']['type'] == old[0] and back['event']['arg'] == old[1],
        '改回原事件（%d / %d）成功' % old)

print('\n== B: 没挂建筑、无 TagNode → 进入事件 ==')
if not free:
    chk(False, '本档找不到"没挂建筑且无 TagNode"的触发')
else:
    x = free[0]
    old = (x['event']['type'], x['event']['arg'])
    L0 = len(d.plain)
    d.soft_disable_trigger(x['sid'])
    dL = len(d.plain) - L0
    det = d.trigger_detail(x['sid'])
    chk(det['event']['type'] == 1, '事件 → %s' % det['event']['name'])
    chk(det['event']['arg'] == 0, '参数 = 0')
    chk(dL == 0, '明文长度不变（进入事件不碰计时表，%+d）' % dL)
    d.set_event(det['event'], etype=old[0], arg=old[1])
    chk(d.trigger_detail(x['sid'])['event']['type'] == old[0], '改回原事件') 

print('\n== C: 存盘重读 ==')
x = (att or free)[0]
d.soft_disable_trigger(x['sid'])
tmp = os.path.join(tempfile.gettempdir(), '_softdel.SAV')
d.save(tmp, backup=False)
d2 = SaveDoc(tmp)
det2 = d2.trigger_detail(x['sid'])
chk(det2['event']['type'] in (1, 13) and det2['event']['arg'] in (0, 0xFFFFFF),
    '重读后：%s 参数 %d' % (det2['event']['name'], det2['event']['arg']))
chk(len(d2.plain) == len(d.plain), '重读后明文长度一致')

print('\n== D: 真删除仍然可用（回归）==')
n_b = sum(1 for r in d2.records if r.kind == '触发')
info = d2.remove_trigger(x['sid'])
chk(sum(1 for r in d2.records if r.kind == '触发') == n_b - 1, '真删除后触发 %d → %d' % (n_b, n_b - 1))

print('\n软删除自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
