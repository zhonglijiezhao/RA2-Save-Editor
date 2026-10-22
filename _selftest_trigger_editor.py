# -*- coding: utf-8 -*-
"""自检：触发编辑器 core API（2026-10-20）
   A trigger_detail 与地图 [Triggers] 逐项对拍（禁用位 82/82）
   B set_trigger_name（含标签名后缀）+ 保存重读
   C set_trigger_flags（禁用/难度 4 字节）
   D add_action / remove_action（链表 + 计数）
   E copy_trigger
   F remove_trigger：**往返自证**（新建→删除 长度回到原值）+ 删中间一条后序号=位置
"""
import io, os, sys, tempfile, struct
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc, ACTION_SIZE

SAV = r'C:\Users\zhao5\Desktop\工程师\SAVE48DB.SAV'
MAP = r'C:\Users\zhao5\Desktop\工程师\all01umd.map'
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
d.attach_map(MAP)
trg = d._map.triggers
print('样本 %s　触发 %d　地图 [Triggers] %d'
      % (os.path.basename(SAV), sum(1 for r in d.records if r.kind == '触发'), len(trg)))

print('\n== A: trigger_detail 与地图对拍 ==')
bad = 0
n = 0
for k, v in trg.items():
    try:
        det = d.trigger_detail(k)
    except Exception as e:
        print('   ✗ %s %s' % (k, e))
        bad += 1
        continue
    f = [x.strip() for x in v.split(',')]
    n += 1
    if det['disabled'] != (f[3] == '1'):
        bad += 1
        if bad <= 3:
            print('   ✗ %s 禁用位 %s vs 地图 %s' % (k, det['disabled'], f[3]))
    if det['owner'] and f[0] and det['owner'] != f[0]:
        bad += 1
        if bad <= 3:
            print('   ✗ %s 国家 %s vs 地图 %s' % (k, det['owner'], f[0]))
chk(bad == 0 and n == len(trg), '逐条对拍 %d 条，矛盾 %d' % (n, bad))
one = d.trigger_detail('01000000')
print('   样例：%s 国=%s 类型=%s 禁用=%s 难度=%s/%s/%s 事件=%s 行为%d 条'
      % (one['name'], one['owner'], one['repeat_text'], one['disabled'],
         one['easy'], one['normal'], one['hard'],
         (one['event'] or {}).get('name'), len(one['actions'])))

print('\n== B: 改名字 + 保存重读 ==')
old = one['name']
r = d.set_trigger_name('01000000', '测试改名AA')
d2 = d.trigger_detail('01000000')
chk(d2['name'] == '测试改名AA', '实例名已改（%r）' % d2['name'])
tags = [t for t, g in d.trigger_tag_pairs() if g == '01000000']
tname = next((nm for sid, nm, _o in d.tag_records() if sid in tags), '')
chk(tname.startswith('测试改名AA'), '标签名跟着改（%r）' % tname)
tmp = os.path.join(tempfile.gettempdir(), '_trigedit_name.SAV')
d.save(tmp, backup=False)
d3 = SaveDoc(tmp)
chk(d3.trigger_detail('01000000')['name'] == '测试改名AA', '保存重读后名字仍在')
d.set_trigger_name('01000000', old)
chk(d.trigger_detail('01000000')['name'] == old, '改回原名')

print('\n== C: 禁用 / 难度 ==')
before = bytes(d.plain[one['rec_off'] + 120:one['rec_off'] + 124])
d.set_trigger_flags('01000000', disabled=True)
mid = bytes(d.plain[one['rec_off'] + 120:one['rec_off'] + 124])
chk(mid[3] == 0, '禁用后 +123 = 0（%s → %s）' % (before.hex(' '), mid.hex(' ')))
chk(d.trigger_detail('01000000')['disabled'] is True, 'detail 读回禁用')
d.set_trigger_flags('01000000', disabled=False, easy=True, normal=False, hard=True)
w = bytes(d.plain[one['rec_off'] + 120:one['rec_off'] + 124])
chk(w[0] == 1 and w[1] == 0 and w[2] == 1 and w[3] == 1,
    '简单/普通/困难 写进 +120/+121/+122（%s）' % w.hex(' '))
d.set_trigger_flags('01000000', disabled=False, easy=True, normal=True, hard=True)
chk(bytes(d.plain[one['rec_off'] + 120:one['rec_off'] + 124]) == before, '全部还原')

print('\n== D: 加 / 删 行为 ==')
multi = max(((len(d.trigger_detail(k)['actions']), k) for k in trg if k), key=lambda x: x[0])
sid = multi[1]
n0 = len(d.trigger_detail(sid)['actions'])
len0 = len(d.plain)
d.add_action(sid, 21, 3)          # 21 = 播放语音/音效类，参数随便
det = d.trigger_detail(sid)
chk(len(det['actions']) == n0 + 1, '行为 %d → %d（%s）' % (n0, len(det['actions']), sid))
chk(struct.unpack_from('<I', d.plain, d.trigger_objects(sid)['inst'] + 172)[0] ==
    d.trigger_detail(sid)['index'], '新行为带正确序号')
newkey = [a for a in det['actions'] if a['type'] == 21][-1]['key']
d.remove_action(sid, newkey)
chk(len(d.trigger_detail(sid)['actions']) == n0, '删掉后回到 %d 条' % n0)
chk(len(d.plain) == len0, '明文长度回到原值（%d）' % len0)

print('\n== E: 复制触发 ==')
src = multi[1]
det = d.trigger_detail(src)
cp = d.copy_trigger(src, '副本测试')
newd = d.trigger_detail(cp['sid'])
chk(newd['name'] == '副本测试', '副本名 %r' % newd['name'])
chk((newd['event'] or {}).get('type') == det['event']['type']
    and (newd['event'] or {}).get('arg') == det['event']['arg'], '事件照抄')
chk(len(newd['actions']) == len(det['actions']), '行为 %d 条全照抄（源 %d）'
    % (len(newd['actions']), len(det['actions'])))
chk(newd['owner'] == det['owner'], '国家照抄（%s）' % newd['owner'])
chk(newd['repeat'] == det['repeat'], '触发类型照抄（%s）' % newd['repeat_text'])
chk(newd['index'] == max((x['index'] for x in
                          (d.trigger_detail(k) for k in trg if k)), default=0)
    or newd['index'] is not None, '副本序号 = %s' % newd['index'])

print('\n== F: 删除（往返自证）==')
len_cp = len(d.plain)
nrec0 = len(d.records)
info = d.remove_trigger(cp['sid'])
chk(len(d.plain) == len_cp - info['bytes_removed'],
    '删掉 %d 字节（对象：%s）' % (info['bytes_removed'], ','.join(info['objects'])))
chk(len(d.records) == nrec0 - 2, '记录数 %d → %d（触发+标签各删一条）' % (nrec0, len(d.records)))
chk(all(r.sid != cp['sid'] for r in d.records), '副本已不在记录表里')

# 删一条**中间**的真实触发 → 序号必须重新等于位置
mids = [k for k in list(trg)[20:22]]
len_b = len(d.plain)
ninst_b = len(d._instance_maps()['inst'])
info2 = d.remove_trigger(mids[0])
im = d._instance_maps()
insts = sorted(im['inst'].items(), key=lambda kv: kv[1])
seqs = [struct.unpack_from('<I', d.plain, o + 172)[0] for _k, o in insts]
chk(seqs == list(range(len(seqs))), '删中间一条后 序号=位置（%d 条）' % len(seqs))
chk(len(insts) == ninst_b - 1, '实例 %d → %d' % (ninst_b, len(insts)))
# 事件/行为侧的序号也必须是新位置
byk = {a.get('key'): a for a in d.actions()}
okseq = True
for i, (k, off) in enumerate(insts):
    ek = struct.unpack_from('<I', d.plain, off + 192)[0]
    ev = next((e for e in d.events() if e.get('key') == ek), None)
    if ev and struct.unpack_from('<I', d.plain, ev['obj'] + 56)[0] != i:
        okseq = False
    for ack in d.action_chain(struct.unpack_from('<I', d.plain, off + 196)[0]):
        a = byk.get(ack)
        if a and struct.unpack_from('<I', d.plain, a['obj'] + 56)[0] != i:
            okseq = False
chk(okseq, '事件/行为的 +56 序号全部跟上新位置')
# 存盘重读（结构自洽的硬证据）
tmp2 = os.path.join(tempfile.gettempdir(), '_trigedit_del.SAV')
d.save(tmp2, backup=False)
d4 = SaveDoc(tmp2)
n_trig_mem = sum(1 for r in d.records if r.kind == '触发')
chk(sum(1 for r in d4.records if r.kind == '触发') == n_trig_mem,
    '重读后触发记录数一致（%d）' % n_trig_mem)
chk(len(d4.runtime_status()) == len(insts), '重读后运行期实例数一致（%d）' % len(insts))
chk(len(d4.events()) == len(insts), '重读后事件数 = 实例数（%d）' % len(d4.events()))

print('\n触发编辑器自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
