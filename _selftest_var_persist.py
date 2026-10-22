# -*- coding: utf-8 -*-
"""§157 自检：局部变量事件（36/37）× 标签持久性 的坑检测。

用法: python _selftest_var_persist.py [存档]   （默认 E:\\YURI\\SAVE4AE1.SAV）

覆盖：
  1. 事件链解析（TriggerType +0xC0 → Event +0x3C）能读出 3 条 36
  2. local_var_events / repeat_pitfall 的三种持久性结论
  3. 无局部变量的触发不误报
  4. set_trigger_repeat_type 写 1 时把 warn 带回来（不拦，但要提醒）
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))
import editor_core as ec

SAV = sys.argv[1] if len(sys.argv) > 1 else r'E:\YURI\SAVE4AE1.SAV'
TID = '01000470'          # 3×事件36 + 1×行为1(胜利)
PLAIN_TID = '01000029'    # 事件 7（单位进入），非局部变量

ok = fail = 0


def chk(cond, msg, got=''):
    global ok, fail
    if cond:
        ok += 1
        print('  PASS %s%s' % (msg, ('  (%s)' % got) if got != '' else ''))
    else:
        fail += 1
        print('  FAIL %s%s' % (msg, ('  (实得 %s)' % got) if got != '' else ''))


print('存档 %s' % SAV)
d = ec.SaveDoc(SAV)

print('\n[1] 事件链')
ev = d.trigger_event_list(TID)
print('  链: %r' % [(e['type'], e['arg']) for e in ev])
chk(len(ev) == 3, '事件链长度 = 3', len(ev))
chk(all(e['type'] == 36 for e in ev), '三条都是 36（局部变量被设置）',
    [e['type'] for e in ev])
chk(sorted(e['arg'] for e in ev) == [0, 1, 2], '三条参数 = 变量 0/1/2',
    sorted(e['arg'] for e in ev))

print('\n[2] local_var_events / repeat_pitfall')
chk(len(d.local_var_events(TID)) == 3, 'local_var_events 命中 3 条',
    d.local_var_events(TID))
chk(d.trigger_attach_count(TID) == 0, '汇总触发挂接对象数 = 0（地图级）',
    d.trigger_attach_count(TID))
chk(d.repeat_pitfall(TID, 0) == '', '持久性 0 → 无警告')
t1 = d.repeat_pitfall(TID, 1)
chk(bool(t1) and '永不执行' in t1, '持久性 1 + 挂 0 个对象 → "永不执行"',
    t1.splitlines()[0][:34])
t2 = d.repeat_pitfall(TID, 2)
chk(bool(t2) and '反复执行' in t2, '持久性 2 → 警告"反复执行"', t2.splitlines()[0][:40])
chk(d.repeat_pitfall(TID) == d.repeat_pitfall(TID, 0),
    '不传持久性 = 按存档当前值（本档 0）判断')

print('\n[2b] §158 进入事件 BUG（持久 1 + 事件 1 + 挂 ≥2 个对象）')
ENTER_TID = '01000069'      # 事件 1（进入事件），本档挂 1 栋
chk(d.repeat_pitfall(PLAIN_TID, 1, attached=3) == '',
    '事件 7（被摧毁）挂 3 个 + 持久 1 → **不报警**（这是正当用法：全部摧毁后执行）')
bug = d.repeat_pitfall(ENTER_TID, 1, attached=3)
chk(bool(bug) and '已知 BUG' in bug, '事件 1（进入）挂 3 个 + 持久 1 → 报"已知 BUG"',
    (bug or '(空)').splitlines()[0][:34])
chk('局部变量' in (bug or ''), 'BUG 提示里给出局部变量写法', '')

print('\n[3] 无局部变量的触发不误报')
chk(d.local_var_events(PLAIN_TID) == [], '01000029 没有局部变量事件',
    d.local_var_events(PLAIN_TID))
chk(d.repeat_pitfall(PLAIN_TID, 1) == '', '01000029 写 1 不报（该提醒由语义决定，不误伤）')

print('\n[4] set_trigger_repeat_type 带回 warn')
info = d.set_trigger_repeat_type(TID, 1)
chk('warn' in info, '返回 dict 含 warn 字段', sorted(info))
chk(bool(info.get('warn')), 'warn 非空（写 1 的坑）', (info.get('warn') or '').splitlines()[0][:40])
info0 = d.set_trigger_repeat_type(TID, 0)
chk(info0.get('warn') == '', '改回 0 → warn 为空')
chk(d.trigger_repeat_type(TID) == '0', '回读持久性 = 0', d.trigger_repeat_type(TID))

print('\n自检结果：%s（PASS %d / FAIL %d）' % ('全部通过 ✔' if not fail else '有失败 ✘', ok, fail))
sys.exit(1 if fail else 0)
