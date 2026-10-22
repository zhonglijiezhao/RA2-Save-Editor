"""§159 自检：trigger_runtime_state() 在四档上的判定（进入事件 BUG 取证）"""
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


BUG_A = ec.SaveDoc(r'E:\YURI\触发未开始.SAV')
BUG_B = ec.SaveDoc(r'E:\YURI\触发开始未执行.SAV')
VAR_A = ec.SaveDoc(r'E:\YURI\局部变量法未开始.SAV')
VAR_B = ec.SaveDoc(r'E:\YURI\局部变量法执行成功.SAV')

print('[1] BUG 档：持久 1 + 进入事件挂 3 栋')
s = BUG_A.trigger_runtime_state('01000000')
chk(s['attached'] == 3 and not s['executed'] and not s['ev_fired'],
    '未开始：挂 3、未执行、事件未满足', s)
s = BUG_B.trigger_runtime_state('01000000')
chk(s['attached'] == 3 and not s['executed'] and s['ev_fired'],
    '3 栋全进入后：挂 3、仍未执行、事件已满足（只记了 1 个触发者）', s)
chk(not s['executed'] and s['has_node'], '节点还在 ⇒ 判"未执行"', s)

print('\n[2] BUG 档的持久性提示带机制说明（§160：计数减不到 0）')
t = BUG_A.repeat_pitfall('01000000', 1)
chk('已知 BUG' in t and '减到 0' in t, '持久 1 + 进入 + 挂 3 ⇒ 已知 BUG + 计数机制',
    t.splitlines()[0][:40])

print('\n[3] 局部变量法：4 条触发')
chk(len(VAR_A.celltag_nodes()) == 4, '未开始：4 个 CellTag 节点',
    len(VAR_A.celltag_nodes()))
chk(all(not VAR_A.trigger_runtime_state(x)['executed'] for x in
        ('01000000', '01000002', '01000004', '01000006')),
    '未开始：4 条全部"未执行"')
chk(len(VAR_B.celltag_nodes()) == 0, '执行成功：0 个 CellTag 节点（标签全被消费）',
    len(VAR_B.celltag_nodes()))
chk(all(VAR_B.trigger_runtime_state(x)['executed'] for x in
        ('01000000', '01000002', '01000004', '01000006')),
    '执行成功：4 条全部"已执行"')
chk(all(VAR_B.trigger_runtime_state(x)['ev_fired'] for x in
        ('01000000', '01000002', '01000004')),
    '执行成功：A/B/C 的事件 +0x68 都有触发者指针')

print('\n[4] 常量与容错')
chk(ec.SaveDoc.EVENT_FIRED_PTR_OFF == 0x68, 'EVENT_FIRED_PTR_OFF = 0x68')
chk(VAR_A.trigger_runtime_state('99999999')['text'] != '',
    '不存在的 sid 不抛异常，返回说明文字')

print('\nPASS %d / FAIL %d' % (OK, FAIL))
sys.exit(0 if FAIL == 0 else 1)
