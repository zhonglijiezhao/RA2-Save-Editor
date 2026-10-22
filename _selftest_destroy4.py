"""自检 §160：持久 1 的「挂载数递减到 0 才执行」—— 摧毁法四档实证

样本：E:\\YURI\\ 下 摧毁法 未执行 / 摧毁一栋 / 摧毁两栋 / 三栋摧毁结果执行
（玩家 2026-10-22 提供：进入事件挂 3 栋 + 持久 1 = 永不执行；摧毁法 = 炸完才执行）
"""
import sys, os
sys.path.insert(0, 'core')
import editor_core as ec

ROOT = r'E:\YURI'
FILES = [('摧毁法 未执行.SAV', 3, False), ('摧毁一栋.SAV', 2, False),
         ('摧毁两栋.SAV', 1, False), ('三栋摧毁结果执行.SAV', 0, True)]
SID = '01000000'

np = nf = 0
def chk(cond, name, info=''):
    global np, nf
    if cond:
        np += 1; print('  PASS %s  %s' % (name, info))
    else:
        nf += 1; print('  FAIL %s  %s' % (name, info))

docs = {}
for fn, exp_remain, exp_exec in FILES:
    p = os.path.join(ROOT, fn)
    if not os.path.exists(p):
        print('  缺样本：%s' % p); sys.exit(2)
    docs[fn] = ec.SaveDoc(p)

print('[1] 四档：剩余挂载数 3/2/1/0 + 只有最后一档执行')
for fn, exp_remain, exp_exec in FILES:
    d = docs[fn]
    st = d.trigger_runtime_state(SID)
    chk(st['remain'] == exp_remain and st['executed'] == exp_exec,
        '%-20s 剩余=%s 已执行=%s' % (fn, exp_remain, exp_exec),
        '→ 实测 remain=%s executed=%s' % (st['remain'], st['executed']))

print('\n[2] 剩余数 == 建筑列表里存活的 GAPOWR 数（两个独立来源）')
for fn, exp_remain, exp_exec in FILES:
    d = docs[fn]
    alive = 0
    try:
        alive = sum(1 for b in d.buildings()
                    if str(b.get('type', '')).upper().startswith('GAPOWR'))
    except Exception:
        pass
    st = d.trigger_runtime_state(SID)
    chk(alive == exp_remain and (st['remain'] or 0) == exp_remain,
        '%-20s 建筑剩 %d / 计数 %s' % (fn, alive, st['remain']))

print('\n[3] 关键证据：2 栋(count=1, 未执行) vs 3 栋(已执行) 的触发三件套逐 u32 一致')
A, B = docs['摧毁两栋.SAV'], docs['三栋摧毁结果执行.SAV']
oa, ob = A.trigger_objects(SID), B.trigger_objects(SID)
pa, pb = A.plain, B.plain
def same(offa, offb, size):
    return all(pa[offa + i:offa + i + 4] == pb[offb + i:offb + i + 4]
               for i in range(0, size - 3, 4))
chk(same(oa['rec_off'], ob['rec_off'], 132), 'Trigger 定义记录 132B 完全一致')
chk(same(oa['inst'], ob['inst'], 200), 'Trigger 实例 200B 完全一致')
chk(same(oa['ev'], ob['ev'], 108), '事件对象 108B 完全一致')
chk(oa.get('cell') is not None and ob.get('cell') is None,
    '唯一差别：CellTag 记录从「在」变成「消失」')

print('\n[4] 已执行档的标签记录整条消失（不做执行判据，只作现象记录）')
for fn, exp_remain, exp_exec in FILES:
    d = docs[fn]
    n_tag = len(d.tag_records())
    n_node = len(d.celltag_nodes())
    chk(n_node == (0 if exp_exec else 1),
        '%-20s CellTag 节点 %d 个 / 标签记录 %d 条' % (fn, n_node, n_tag))

print('\n[5] 持久性取值与提示')
d = docs['摧毁法 未执行.SAV']
chk(d.tag_persist('01000001') == '1', '标签 01000001 持久性 = 1',
    d.tag_persist('01000001'))
chk(d.repeat_pitfall(SID, 1) == '', '持久 1 + 摧毁事件 + 挂 3 个 ⇒ **不报警**（正当用法）')
chk('减到 0' in (d.repeat_pitfall(SID, 1, attached=0) or ''),
    '持久 1 + 挂 0 个 ⇒ 警告"减不到 0/永不执行"')
chk(d.REPEAT_TEXT['1'] == '一次性·挂载清空', 'REPEAT_TEXT[1] = 一次性·挂载清空',
    d.REPEAT_TEXT['1'])

print('\n[6] 容错')
chk(d.semi_persist_remaining('FFFFFFF') is None, '不存在的 sid → None')
chk(d.trigger_runtime_state('FFFFFFF')['text'] != '', '不存在的 sid 不抛异常，返回说明文字')

print('\nPASS %d / FAIL %d' % (np, nf))
sys.exit(0 if nf == 0 else 1)
