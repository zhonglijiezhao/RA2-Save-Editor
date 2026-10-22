# -*- coding: utf-8 -*-
"""自检：新建触发「不再是废触发」（2026-10-20 玩家质疑）

玩家原话：*"新建触发以第一个触发为模板吗？第一个触发是已执行过的废触发，就会建一个废触发"*

要证的四件事：
  A 新建（事件 8「任何事件」）**必须带 TagNode/CellTag** —— 以前不建，导致之后改成
    「13 流逝时间」也没有节点 ⇒ 引擎不执行，列表直接显示"已执行"= 废触发
  B 新建（事件 13, 10 秒）→ 帧数 150 + 计时表登记 ⇒ 状态是"等待中"而不是"已执行"
  C 编辑器里把事件从 8 改成 13 → **自动补组件**（等价于修复缺组件）
  D 克隆骨架**不继承**模板的运行期指针数组（+0x48..+0x70）
"""
import io, os, sys, struct, tempfile
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc

SAV = r'C:\Users\zhao5\Desktop\工程师\SAVE48DB.SAV'
ALT = os.path.join(HERE, '_exp', 'sample_AB_已执行与等待.SAV')
OK = FAIL = 0


def chk(c, msg):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


def status_of(d, sid):
    """复刻 GUI 状态列的判据 → (文字, has_node, ticks)"""
    st = {s['sid']: s for s in d.runtime_status()}.get(sid)
    if st is None:
        return '无运行期组件', False, None
    if not st['has_node']:
        return ('已执行', False, None)
    if st['rt_enabled']:
        return ('等待中', True, st['ticks'])
    return ('已禁用', True, st['ticks'])


d = SaveDoc(SAV)
print('样本 %s　触发 %d' % (os.path.basename(SAV),
                          sum(1 for r in d.records if r.kind == '触发')))
base_len = len(d.plain)

print('\n== A: 新建（事件 8 任何事件）必须带六件套 ==')
i8 = d.create_trigger('自检_任何事件', 8, 0, 21, 0)
txt8, node8, ticks8 = status_of(d, i8['trigger_id'])
chk(node8 is True, '有 TagNode（%s）' % txt8)
chk(status_of(d, i8['trigger_id'])[1] and txt8 != '已执行', '状态不是「已执行」：%s' % txt8)
det8 = d.trigger_detail(i8['trigger_id'])
chk(det8['has_cell'] is True, '有 CellTag')
chk(det8['ticks'] == 0, '事件 8 帧数 = 0（不进计时表）')
chk(det8['disabled'] is False and det8['easy'] is True, '启用 + 三难度默认开')

print('\n== B: 新建（事件 13, 10 秒）→ 计时正确 ==')
i13 = d.create_trigger('自检_计时', 13, 10, 21, 0)
det13 = d.trigger_detail(i13['trigger_id'])
txt13, node13, ticks13 = status_of(d, i13['trigger_id'])
chk(node13 and ticks13 == 150, '帧数 = 150（10 秒 × 15）')
chk(txt13 == '等待中', '状态是「等待中」而不是「已执行」：%s' % txt13)
tl = d.celltag_timer_list()
ck = d.trigger_objects(i13['trigger_id'])['cell_key']
chk(tl is not None and ck in tl['keys'], '已登记进「定时触发器挂接表」')

print('\n== C: 把「事件 8」改成「13 流逝时间」→ 自动补组件 ==')
det = d.trigger_detail(i8['trigger_id'])
chk(det['has_node'], '改之前就有节点（因为 A 段已经改成六件套）')
d.set_event(det['event'], etype=13, arg=12)
txt2, node2, ticks2 = status_of(d, i8['trigger_id'])
chk(node2 and ticks2 == 180, '改成 12 秒后帧数 = 180')
chk(txt2 == '等待中', '状态 %s' % txt2)
# 老档里的"真·死触发"（有实例但无节点）改事件时，由 GUI 自动修复；core 层验证修复可用
x = None
for r in d.records:
    if r.kind != '触发':
        continue
    st = {s['sid']: s for s in d.runtime_status()}.get(r.sid)
    if st is not None and not st['has_node'] and r.sid not in (i8['trigger_id'], i13['trigger_id']):
        x = r
        break
if x is None:
    print('   （本档没有"有实例无节点"的触发，跳过自动补组件验证）')
else:
    inst = d.trigger_inst_off(x.sid)
    info = d.repair_trigger_runtime(inst)
    st2 = {s['sid']: s for s in d.runtime_status()}[x.sid]
    chk(st2['has_node'] is True, '「%s」补组件后有了 TagNode（帧 %d）'
        % (x.name, info['ticks']))

print('\n== D: 克隆骨架不继承模板的运行期指针 ==')
d2 = SaveDoc(SAV)
pl = d2.plain
tpl, _tag = d2._pick_trigger_template(d2._newtrig_groups())
tb = bytes(pl[tpl:tpl + 200])
new = d2.create_trigger('自检_骨架', 8, 0, 21, 0)
off = d2.trigger_inst_off(new['trigger_id'])
nb = bytes(pl[off:off + 200])
# +0x48..+0x70 = 72..112 是"运行期指针数组"；只允许全 0 相同
bad = [o for o in range(72, 112, 4)
       if tb[o:o + 4] == nb[o:o + 4] and struct.unpack_from('<I', tb, o)[0]]
chk(not bad, '指针区 +0x48..+0x70 没有从模板抄来非零值（%s）' % [hex(o) for o in bad])
# 类 GUID 必须一致（说明还是同一类对象）
chk(tb[0:16] == nb[0:16], '类 GUID 与模板一致（同一类对象）')
chk(d2.trigger_detail(new['trigger_id'])['name'] == '自检_骨架', '名字是新写的')
chk(d2.trigger_detail(new['trigger_id'])['index'] ==
    max((d2.trigger_detail(r.sid)['index'] for r in d2.records
         if r.kind == '触发' and d2.trigger_detail(r.sid)['index'] is not None),
        default=0), '序号 = 末尾')

print('\n== E: 存盘重读 ==')
for sid in (i8['trigger_id'], i13['trigger_id']):
    pass
tmp = os.path.join(tempfile.gettempdir(), '_newtrig_pieces.SAV')
d.save(tmp, backup=False)
d3 = SaveDoc(tmp)
t8 = status_of(d3, i8['trigger_id'])
t13 = status_of(d3, i13['trigger_id'])
chk(t8[1] and t13[1], '重读后两条都还有 TagNode')
chk(t13[2] == 150, '重读后计时触发帧数仍 150')
chk(t8[2] == 180, '重读后事件 8→13 那条帧数仍 180')

print('\n新建触发组件自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
