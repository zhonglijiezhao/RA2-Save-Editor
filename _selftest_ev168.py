# -*- coding: utf-8 -*-
"""§168 自检：CellTag 计数 / 抑制位 / 丢账修复（反汇编落地的三条写操作）。

跑法：python _selftest_ev168.py
存档：E:\\YURI 下的三档（未操作 / 先炸后等 / 等30秒再炸）。缺档则跳过对应断言。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.editor_core import SaveDoc      # noqa: E402

BASE = 'E:/YURI'
F_IDLE = os.path.join(BASE, '开局未操作档.SAV')
F_BOOM_FIRST = os.path.join(BASE, '先摧毁三栋电厂 等待30秒后结果不执行.SAV')
F_WAIT_FIRST = os.path.join(BASE, '等待30秒后 摧毁电厂结果执行.SAV')

CASES = []
PASS = [0]
FAIL = []


def ck(name, cond, detail=''):
    if cond:
        PASS[0] += 1
        print('  PASS  %s' % name)
    else:
        FAIL.append(name)
        print('  FAIL  %s   %s' % (name, detail))


def trig_sid(d):
    sids = [r.sid for r in d.records if r.kind == '触发']
    return sids[0] if sids else None


def load(path):
    if not os.path.exists(path):
        print('  SKIP  存档不存在：%s' % os.path.basename(path))
        return None
    return SaveDoc(path)


# ---------------------------------------------------------------- 1 状态收敛
def t_verdict():
    print('\n[1] trigger_runtime_state 的五档 verdict（§168.2/§168.4）')
    d = load(F_IDLE)
    if d:
        st = d.trigger_runtime_state(trig_sid(d))
        ck('未操作档：remain == 3', st['remain'] == 3, st['remain'])
        ck('未操作档：live == 3', st['live'] == 3, st['live'])
        ck('未操作档：leak == 0', st['leak'] == 0, st['leak'])
        ck('未操作档：verdict == waiting', st['verdict'] == 'waiting', st['verdict'])
        ck('未操作档：suppress == 0', st['suppress'] == 0, st['suppress'])
        ck('未操作档：busy == 0', st['busy'] == 0, st['busy'])

    d = load(F_BOOM_FIRST)
    if d:
        st = d.trigger_runtime_state(trig_sid(d))
        ck('先炸后等：remain == 3（纹丝不动）', st['remain'] == 3, st['remain'])
        ck('先炸后等：live == 0', st['live'] == 0, st['live'])
        ck('先炸后等：leak == 3', st['leak'] == 3, st['leak'])
        ck('先炸后等：verdict == dead', st['verdict'] == 'dead', st['verdict'])
        ck('先炸后等：文案含「不回补」', '不回补' in st['text'], st['text'][:40])

    d = load(F_WAIT_FIRST)
    if d:
        st = d.trigger_runtime_state(trig_sid(d))
        ck('等30s再炸：verdict == executed', st['verdict'] == 'executed', st['verdict'])
        ck('等30s再炸：remain == 0', st['remain'] == 0, st['remain'])
        ck('等30s再炸：has_node False', st['has_node'] is False, st['has_node'])


# ---------------------------------------------------------------- 2 抑制位
def t_flags():
    print('\n[2] celltag_flags / clear_celltag_flags（§168.2 +0x34/+0x35）')
    d = load(F_IDLE)
    if not d:
        return
    sid = trig_sid(d)
    fl = d.celltag_flags(sid)
    ck('读到两个标志字节', fl is not None, fl)
    if fl:
        ck('正常档 suppress/busy 都为 0', (fl['suppress'], fl['busy']) == (0, 0), fl)

    o = d.trigger_objects(sid)
    c = o['cell']
    d.plain[c + d.CELLTAG_SUPPRESS_OFF] = 1
    st = d.trigger_runtime_state(sid)
    ck('置抑制位后 verdict == suppressed', st['verdict'] == 'suppressed', st['verdict'])
    ck('置抑制位后文案含「抑制」', '抑制' in st['text'], st['text'][:30])
    r = d.clear_celltag_flags(sid)
    ck('清零返回 old == (1, 0)', r['old'] == (1, 0), r)
    ck('清零后 suppress == 0', d.celltag_flags(sid)['suppress'] == 0)


# ---------------------------------------------------------------- 3 改计数
def t_set_count():
    print('\n[3] set_attach_count（只动 4 字节）')
    d = load(F_IDLE)
    if not d:
        return
    sid = trig_sid(d)
    before = len(d.plain)
    r = d.set_attach_count(sid, 2)
    ck('old == 3', r['old'] == 3, r)
    ck('new == 2', r['new'] == 2, r)
    ck('明文长度不变', len(d.plain) == before, (before, len(d.plain)))
    ck('回读 remain == 2', d.trigger_runtime_state(sid)['remain'] == 2)
    ck('写成 2 后 live=3 ⇒ leak < 0（负数不算漏账）',
       d.trigger_runtime_state(sid)['leak'] == -1)
    ck('写成 2 后 verdict 仍是 waiting',
       d.trigger_runtime_state(sid)['verdict'] == 'waiting')
    # 还原
    d.set_attach_count(sid, 3)
    ck('还原后 remain == 3', d.trigger_runtime_state(sid)['remain'] == 3)
    # 防御：越界值
    try:
        d.set_attach_count(sid, 999)
        ck('n=999 应被拒', False)
    except Exception:
        ck('n=999 被拒', True)


# ---------------------------------------------------------------- 4 丢账修复
def t_repair():
    print('\n[4] repair_attach_leak（把计数改回实际存活数）')
    d = load(F_BOOM_FIRST)
    if not d:
        return
    sid = trig_sid(d)
    st = d.trigger_runtime_state(sid)
    ck('修复前 dead', st['verdict'] == 'dead', st['verdict'])
    r = d.repair_attach_leak(sid)
    ck('live == 0 ⇒ 判定不可救', r['ok'] is False, r)
    ck('不可救文案含「救不回来」', '救不回来' in r['text'], r['text'][:30])
    ck('不可救时不写盘（remain 仍 3）',
       d.trigger_runtime_state(sid)['remain'] == 3)

    # 造一个"部分丢账"的档：live 改成 3 再修
    d2 = load(F_IDLE)
    if d2:
        sid2 = trig_sid(d2)
        d2.set_attach_count(sid2, 5)          # 人为制造 leak = 5 - 3 = 2
        st2 = d2.trigger_runtime_state(sid2)
        ck('人造 leak == 2', st2['leak'] == 2, st2['leak'])
        ck('人造 leak 后 verdict == dead', st2['verdict'] == 'dead', st2['verdict'])
        r2 = d2.repair_attach_leak(sid2)
        ck('可救：ok True', r2['ok'] is True, r2)
        ck('可救：old 5 → new 3', (r2['old'], r2['new']) == (5, 3), r2)
        ck('修完 remain == 3', d2.trigger_runtime_state(sid2)['remain'] == 3)
        ck('修完 leak == 0', d2.trigger_runtime_state(sid2)['leak'] == 0)
        ck('修完 verdict == waiting',
           d2.trigger_runtime_state(sid2)['verdict'] == 'waiting')


# ---------------------------------------------------------------- 5 存盘往返
def t_save_roundtrip():
    print('\n[5] 改计数 → 存盘 → 重读（确认写进存档且不破坏其它字段）')
    if not os.path.exists(F_IDLE):
        print('  SKIP  无存档')
        return
    tmpdir = tempfile.mkdtemp(prefix='ev168_')
    try:
        d = SaveDoc(F_IDLE)
        sid = trig_sid(d)
        d.set_attach_count(sid, 2)
        out = os.path.join(tmpdir, 'out.SAV')
        d.save(out, backup=False)
        ck('输出档存在', os.path.exists(out))
        d2 = SaveDoc(out)
        sid2 = trig_sid(d2)
        ck('重读 remain == 2', d2.trigger_runtime_state(sid2)['remain'] == 2,
           d2.trigger_runtime_state(sid2)['remain'])
        ck('重读触发数量不变',
           len([r for r in d2.records if r.kind == '触发']) ==
           len([r for r in d.records if r.kind == '触发']))
        ck('重读建筑数量不变', len(d2.buildings()) == len(d.buildings()),
           (len(d.buildings()), len(d2.buildings())))
        # 抑制位也走一趟
        d2.plain[d2.trigger_objects(sid2)['cell'] + 0x34] = 1
        out2 = os.path.join(tmpdir, 'out2.SAV')
        d2.save(out2, backup=False)
        d3 = SaveDoc(out2)
        ck('重读 suppress == 1',
           d3.trigger_runtime_state(trig_sid(d3))['suppress'] == 1)
        ck('重读 verdict == suppressed',
           d3.trigger_runtime_state(trig_sid(d3))['verdict'] == 'suppressed')
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    for fn in (t_verdict, t_flags, t_set_count, t_repair, t_save_roundtrip):
        try:
            fn()
        except Exception as e:
            FAIL.append('%s 抛异常' % fn.__name__)
            print('  FAIL  %s 抛异常: %s: %s' % (fn.__name__, type(e).__name__, e))
    print('\n' + '=' * 60)
    print('PASS %d   FAIL %d' % (PASS[0], len(FAIL)))
    for f in FAIL:
        print('   ! %s' % f)
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
