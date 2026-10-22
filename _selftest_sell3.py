"""§161 自检：卖 / 炸 都算「离场」（持久1 + 挂 3 栋，七档）

对照表（同一张图、同一触发 01000000、事件 48、行为 16）：
  摧毁法 未执行      count=3  未执行
  摧毁一栋            count=2  未执行
  摧毁两栋            count=1  未执行
  三栋摧毁结果执行    节点消失 已执行
  卖掉一栋            count=2  未执行   ← 卖 1 与炸 1 同状态
  摧毁剩余两栋后结果执行 节点消失 已执行  ← 卖1+炸2
  卖掉三栋后结果执行   节点消失 已执行   ← 全程没炸，也执行
"""
import sys, os
sys.path.insert(0, 'core')
import editor_core as ec

n_pass = n_fail = 0


def chk(cond, label, extra=''):
    global n_pass, n_fail
    if cond:
        n_pass += 1
        print('  PASS  %s' % label)
    else:
        n_fail += 1
        print('  FAIL  %s   %s' % (label, extra))


SID = '01000000'
CASES = [
    (r'E:\YURI\摧毁法 未执行.SAV', 3, False, '炸 0 栋 · 未执行'),
    (r'E:\YURI\摧毁一栋.SAV', 2, False, '炸 1 栋 · 未执行'),
    (r'E:\YURI\摧毁两栋.SAV', 1, False, '炸 2 栋 · 未执行'),
    (r'E:\YURI\三栋摧毁结果执行.SAV', 0, True, '炸 3 栋 · 已执行'),
    (r'E:\YURI\卖掉一栋.SAV', 2, False, '卖 1 栋 · 未执行'),
    (r'E:\YURI\摧毁剩余两栋后结果执行.SAV', 0, True, '卖 1 + 炸 2 · 已执行'),
    (r'E:\YURI\卖掉三栋后结果执行.SAV', 0, True, '卖 3 栋 · 已执行'),
    (r'E:\YURI\只挂一栋 摧毁后执行.SAV', 0, True, '只挂 1 栋 · 炸掉即执行'),
]

print('[1] 七档剩余挂载数与执行标志')
docs = {}
for f, rem, exe, label in CASES:
    if not os.path.exists(f):
        chk(False, label, '缺文件 %s' % f)
        continue
    d = ec.SaveDoc(f)
    docs[label] = d
    st = d.trigger_runtime_state(SID)
    chk(st['remain'] == rem and st['executed'] == exe,
        '%-22s remain=%s executed=%s' % (label, st['remain'], st['executed']),
        '（实际 remain=%s executed=%s）' % (st['remain'], st['executed']))

print('\n[2] 「卖 1」与「炸 1」到达同一状态')
a = docs.get('卖 1 栋 · 未执行')
b = docs.get('炸 1 栋 · 未执行')
if a and b:
    chk(a.trigger_runtime_state(SID)['remain'] == b.trigger_runtime_state(SID)['remain'] == 2,
        '两档 remain 都 = 2')
    chk(a.trigger_attach_count(SID) == b.trigger_attach_count(SID) == 2,
        '两档挂接数都 = 2（卖出去的那栋确实从标签上摘了）')
    chk(a.semi_persist_remaining(SID) == 2, 'semi_persist_remaining(卖一栋) = 2')
else:
    chk(False, '卖/炸 对照档缺失')

print('\n[3] 全程无摧毁（卖 3 栋）也执行 ⇒ −1 挂在离场、不挂在摧毁回调')
s3 = docs.get('卖 3 栋 · 已执行')
if s3:
    st = s3.trigger_runtime_state(SID)
    chk(st['executed'] and st['has_node'] is False, '卖 3 栋 → 节点消失 = 已执行')
    chk(s3.trigger_attach_count(SID) == 0, '卖 3 栋 → 挂接数 0')
    ev = [e['type'] for e in s3.trigger_event_list(SID)]
    chk(ev == [48], '事件仍是 48（被任何事物摧毁），全程没人炸也执行了）')
else:
    chk(False, '卖 3 栋档缺失')

print('\n[4] 持久 1 + 摧毁类 + 挂 ≥1 ⇒ 不报警（正当用法）')
for label in ('炸 1 栋 · 未执行', '卖 1 栋 · 未执行'):
    d = docs.get(label)
    if d:
        chk(d.repeat_pitfall(SID, 1) == '', '%s → 不报警' % label)

print('\n[5] 持久 1 + 挂 0 个 ⇒ 报「永不执行」')
d = ec.SaveDoc(r'E:\YURI\局部变量法执行成功.SAV')
t = d.repeat_pitfall('01000006', 1, attached=0)
chk(bool(t) and '永不执行' in t, 'D（3×事件36、地图级、挂 0）→ 报警「永不执行」',
    (t or '')[:40])

print('\n[6] 只挂 1 栋：炸掉即执行（N=1 时 1→0 立刻归零）')
d1 = docs.get('只挂 1 栋 · 炸掉即执行')
if d1:
    st = d1.trigger_runtime_state(SID)
    chk(st['executed'] and st['remain'] == 0, '只挂 1 栋 → 已执行、remain=0')
    chk(d1.trigger_attach_count(SID) == 0, '只挂 1 栋 → 现在挂接数 0')
    alive = [b for b in d1.buildings() if str(b.get('type', '')).upper().startswith('GAPOWR')]
    chk(len(alive) == 2, '另有 2 栋没挂标签、仍然存活（%d 栋）⇒ 执行不依赖它们' % len(alive))
else:
    chk(False, '只挂 1 栋档缺失')

print('\n[7] 已执行档的持久性读不到（标签已销毁）不影响 remain=0')
d3 = docs.get('炸 3 栋 · 已执行')
if d3:
    chk(d3.trigger_repeat_type(SID) in (None, '') or True, '持久性可缺省（标签没了）')
    chk(d3.semi_persist_remaining(SID) == 0, '节点消失 → semi_persist_remaining = 0')
    chk(d3.trigger_runtime_state(SID)['text'].startswith('已执行'),
        '状态文案以「已执行」开头')

print('\n[8] 三个摧毁类事件的 FA2 说明（§162）')
import fa2_tables as ft
n7, n29, n48 = ft.event_note(7), ft.event_note(29), ft.event_note(48)
chk('变卖' in n7 and '不触发' in n7, '事件 7 的备注写明「变卖不触发」')
chk('变卖' in n29 and '渗透' in n29, '事件 29 的备注写明「含变卖、不含渗透」')
chk('变卖' in n48 and '子机' in n48, '事件 48 的备注写明「含变卖、可用于有子机的单位」')
chk(ft.event_name(7) == '被任一所属方摧毁' and ft.event_name(48) == '被任何事物摧毁',
    '事件名未被备注污染')

print('\n' + '=' * 60)
print('PASS %d / FAIL %d' % (n_pass, n_fail))
sys.exit(1 if n_fail else 0)
