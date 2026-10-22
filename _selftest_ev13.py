"""§163 自检：持久 1 + 多条事件时"离场记账"会被卡住（漏账指纹）

四档（同一张图、同一条触发：持久 1、挂 3 栋电厂、事件「流逝时间 30 秒」and「被任何事物摧毁」）：
  开局未操作档 / 等待30秒后 / 等待30秒后 摧毁电厂结果执行 / 先摧毁三栋电厂 等待30秒后结果不执行
"""
import sys
sys.path.insert(0, 'core')
import editor_core as ec

OK = FAIL = 0


def chk(cond, msg, extra=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  PASS  %s' % msg)
    else:
        FAIL += 1
        print('  FAIL  %s   %s' % (msg, extra))


A = ec.SaveDoc(r'E:\YURI\开局未操作档.SAV')
B = ec.SaveDoc(r'E:\YURI\等待30秒后.SAV')
C = ec.SaveDoc(r'E:\YURI\等待30秒后 摧毁电厂结果执行.SAV')
D = ec.SaveDoc(r'E:\YURI\先摧毁三栋电厂 等待30秒后结果不执行.SAV')
S1 = ec.SaveDoc(r'E:\YURI\摧毁一栋.SAV')
S2 = ec.SaveDoc(r'E:\YURI\卖掉一栋.SAV')
SID = '01000000'

print('[1] 两事件档的事件链顺序（链头 = GUI 列表最后一条）')
for nm, d in (('A', A), ('B', B), ('C', C), ('D', D)):
    ev = [e['type'] for e in d.trigger_event_list(SID)]
    chk(ev == [13, 48], '%s：链序 = 13 → 48（GUI 列表是 48 在上、13 在下）' % nm, ev)

print('\n[2] 基线 / 只等待：计数与存活一致，未执行')
for nm, d in (('A 基线', A), ('B 等 30 秒', B)):
    st = d.trigger_runtime_state(SID)
    chk(st['remain'] == 3 and st['live'] == 3, '%s：计数 3 / 实际存活 3' % nm,
        (st['remain'], st['live']))
    chk(st['leak'] == 0 and not st['executed'], '%s：无漏账、未执行' % nm, st)

print('\n[3] 先等满 30 秒再炸 3 栋 ⇒ 正常执行')
st = C.trigger_runtime_state(SID)
chk(st['executed'], 'C：已执行（CellTag 节点消失）', st)
chk(st['remain'] == 0, 'C：计数归零', st['remain'])
chk(len(C.celltag_nodes()) == 0, 'C：0 个 CellTag 节点', len(C.celltag_nodes()))

print('\n[4] 先炸完 3 栋再等 30 秒 ⇒ 计数漏账、永不执行（§163 关键档）')
st = D.trigger_runtime_state(SID)
chk(len([b for b in D.buildings() if 'POWR' in (b.get('type') or '')]) == 0,
    'D：电量厂实际存活 0 栋')
chk(st['remain'] == 3, 'D：CellTag `+0x40` 计数**仍是 3**（3 次离场一次都没记）', st['remain'])
chk(st['live'] == 0, 'D：几何扫描实际存活挂载建筑 = 0', st['live'])
chk(st['leak'] == 3, 'D：漏账量 = 3', st['leak'])
chk(not st['executed'] and st['has_node'], 'D：节点还在 ⇒ 未执行', st)
chk('漏账' in st['text'], 'D：状态文字报"漏账"', st['text'][:40])

print('\n[5] 单事件对照：摧毁/变卖一栋 ⇒ 计数跟着掉（证明记账本身是好的）')
for nm, d in (('炸 1 栋', S1), ('卖 1 栋', S2)):
    st = d.trigger_runtime_state(SID)
    chk(st['remain'] == 2 and st['live'] == 2, '%s：计数 2 / 存活 2' % nm,
        (st['remain'], st['live']))
    chk(st['leak'] == 0, '%s：无漏账' % nm, st['leak'])

print('\n[5b] §164 事件 7 不认变卖 ⇒ 卖也不记账（与事件 48 对照，只改事件类型）')
S7A = ec.SaveDoc(r'E:\YURI\卖一栋.SAV')
S7B = ec.SaveDoc(r'E:\YURI\卖三栋.SAV')
chk([e['type'] for e in S7A.trigger_event_list(SID)] == [7],
    '卖一栋：事件链只有 7', [e['type'] for e in S7A.trigger_event_list(SID)])
st = S7A.trigger_runtime_state(SID)
chk(st['remain'] == 3 and st['live'] == 2 and st['leak'] == 1,
    '事件 7 + 卖 1 栋：计数**仍 3**、实际存活 2 ⇒ 漏 1 次（事件 48 时会变 2）',
    (st['remain'], st['live'], st['leak']))
st = S7B.trigger_runtime_state(SID)
chk(st['remain'] == 3 and st['live'] == 0 and st['leak'] == 3,
    '事件 7 + 卖光 3 栋：计数**仍 3**、存活 0 ⇒ 漏 3 次', (st['remain'], st['live']))
chk(not st['executed'] and st['has_node'], '事件 7 + 卖光 3 栋：**不执行**（节点还在）', st)
chk('没被记账' in st['text'], '状态文字报"没被记账"', st['text'][:30])

print('\n[5c] §165 换链序照样丢账：13(60 秒) + 48，链头 = 48，60 秒前炸')
S5 = ec.SaveDoc(r'E:\YURI\开局五秒 炸一栋.SAV')
S40 = ec.SaveDoc(r'E:\YURI\40秒炸两栋.SAV')
chk([e['type'] for e in S5.trigger_event_list(SID)] == [48, 13],
    '5s 档：链序 [48, 13]（48 在链头，与 §163 那批相反）',
    [e['type'] for e in S5.trigger_event_list(SID)])
st = S5.trigger_runtime_state(SID)
chk(st['remain'] == 3 and st['live'] == 2 and st['leak'] == 1,
    '5 秒炸 1 栋：计数**仍 3**、存活 2 ⇒ 漏 1（48 在链头也没用）',
    (st['remain'], st['live'], st['leak']))
st = S40.trigger_runtime_state(SID)
chk(st['remain'] == 3 and st['live'] == 1 and st['leak'] == 2,
    '40 秒炸 2 栋：计数**仍 3**、存活 1 ⇒ 漏 2', (st['remain'], st['live'], st['leak']))
chk(not st['executed'], '40 秒档：未执行', st['executed'])

print('\n[5d] §166 窗口打开后只补记"当下"那一次：等过 60 秒再炸最后 1 栋')
S60 = ec.SaveDoc(r'E:\YURI\60秒后炸三栋 不执行.SAV')
st = S60.trigger_runtime_state(SID)
chk(st['remain'] == 2, '60 秒档：计数 **3 → 2**（只有最后那一次被记账）', st['remain'])
chk(st['live'] == 0 and st['leak'] == 2, '60 秒档：存活 0、仍漏 2 次', (st['live'], st['leak']))
chk(not st['executed'] and st['has_node'], '60 秒档：**不执行**（前面 2 次永久丢了）', st)

print('\n[5e] §167 第二条事件也是摧毁类（10 该阵营建筑全毁），一样挡账')
K1 = ec.SaveDoc(r'E:\YURI\先摧毁韩国建筑 后摧毁三栋电厂 结果执行.SAV')
K2 = ec.SaveDoc(r'E:\YURI\先摧毁三个电厂 后摧毁韩国建筑不执行.SAV')
chk([e['type'] for e in K1.trigger_event_list(SID)] == [10, 48],
    '链序 [10, 48]（10 在链头）', [e['type'] for e in K1.trigger_event_list(SID)])
st = K1.trigger_runtime_state(SID)
chk(st['executed'] and st['remain'] == 0, '先满足 10 再毁 3 栋 ⇒ 已执行、计数归零', st['remain'])
st = K2.trigger_runtime_state(SID)
chk(st['remain'] == 3 and st['live'] == 0 and st['leak'] == 3,
    '先毁 3 栋再满足 10 ⇒ 计数**仍 3**、存活 0（3 次全丢）', (st['remain'], st['live']))
chk(not st['executed'] and st['has_node'], '先毁 3 栋 ⇒ **不执行**', st['executed'])

print('\n[6] 持久性提示：多事件 + 挂载要警告，单事件不误伤')
t = D.repeat_pitfall(SID, 1)
chk(bool(t) and '多条事件' in t, 'D（2 事件 + 挂 3）⇒ 报"多条事件"风险',
    (t or '(空)').splitlines()[0][:34])
chk('永久丢账' in (t or '') and '换' in (t or ''),
    'D 的提示里写明"永久丢账"+换顺序没用', (t or '')[:40])
chk(S1.repeat_pitfall(SID, 1) == '', '单事件 + 挂 3（正当用法）⇒ 不报警',
    S1.repeat_pitfall(SID, 1)[:40])
chk(S2.repeat_pitfall(SID, 1) == '', '单事件 + 变卖（正当用法）⇒ 不报警', '')

print('\n[7] 容错')
chk(D.attach_live_count('99999999') is None, '不存在的 sid → None')
chk(D.trigger_runtime_state('99999999')['text'] != '', '不存在的 sid 不抛异常')

print('\nPASS %d / FAIL %d' % (OK, FAIL))
sys.exit(0 if FAIL == 0 else 1)
