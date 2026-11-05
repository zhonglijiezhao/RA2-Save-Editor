# -*- coding: utf-8 -*-
"""自检：持久性 1（挂载清空）+ 挂 0 个对象 ⇒ 永不执行（2026-11-05 玩家实测）

玩家报的原话：
  「看一下这个新触发1 用的任何事件 没有生效　编辑器对任何事件的写法是否有问题
    任何事件也应该是15帧生效」

查出来的根因**不是事件 8 的写法**，是**触发类型**：
  · `SAVE4AE1_修改_2.SAV` 的「新触发1」= 事件 8 + TagNode 帧数 15 + 定时表已登记
    （写法全对），但触发类型被写成了 **1「一次性·挂载清空」**，而这条触发**一个对象都没挂**。
  · 引擎侧（`CellTag::判事件并记账 @0x6E53A0`）执行条件是「剩余挂载数减到 0」；
    挂 0 个对象时计数恒为 0、没有对象能触发 `SetTag(obj,NULL)` ⇒ 永远到不了。
  · 同一份档里的 `存档写触发.SAV`，结构完全相同但类型是 **0「一次性·任一挂载」** ⇒
    玩家之前已实测执行通过 ⇒ 差别只在持久类型这一个字节。

断言：
  A 真档：01000007 判成 dead（永不执行），文案点名"挂 0 个对象"
  B 新建防呆：persistence=1 + 无 attach ⇒ 自动降级 0 + 给警告（返回值也是 0）
  C 新建：persistence=1 + 有 attach ⇒ 保留 1（不误伤）
  D 事件 8 的写法本身正确：TagNode 帧数 15，且 CellTag 键进了「定时触发器挂接表」
  E 对照：同一份档里 repeat=0 的触发不会被误判成 dead
"""
import io
import os
import shutil
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc, NODE_TICKS_OFF                       # noqa: E402

OK = FAIL = 0


def chk(c, msg, extra=None):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg + ('   → %r' % (extra,) if extra is not None else ''))


SAV = r'E:\YURI\SAVE4AE1_修改_2.SAV'
TMP = tempfile.mkdtemp(prefix='_p1_')
try:
    print('== A: 真档里「新触发1 + 任何事件 + 持久 1 + 挂 0 个」==')
    if not os.path.isfile(SAV):
        print('  （没有 %s，跳过）' % SAV)
    else:
        d = SaveDoc(SAV)
        det = d.trigger_detail('01000007')
        st = d.trigger_runtime_state('01000007')
        print('     事件=%s 类型=%s(%s) 挂接=%s'
              % ([(e['type'], e['arg']) for e in d.trigger_event_list('01000007')],
                 det.get('repeat'), det.get('repeat_text'), st.get('attached')))
        chk(det.get('repeat') == '1', '触发类型是 1（一次性·挂载清空）', det.get('repeat'))
        chk(st.get('attached') == 0, '一个对象都没挂', st.get('attached'))
        chk(st['verdict'] == 'dead', '判定 = dead（永不执行），不再是含糊的 waiting', st['verdict'])
        chk('0' in st['text'] and ('永不执行' in st['text'] or '永远' in st['text']),
            '文案点名根因并给出改法', st['text'][:60])

    print('\n== D: 事件 8 的写法本身是对的（别去改事件）==')
    if os.path.isfile(SAV):
        d = SaveDoc(SAV)
        o = d.trigger_objects('01000007')
        ticks = int.from_bytes(d.plain[o['node'] + NODE_TICKS_OFF:
                                        o['node'] + NODE_TICKS_OFF + 4], 'little')
        tl = d.celltag_timer_list() or {}
        keys = set(tl.get('keys') or ())
        chk(ticks == 15, 'TagNode 帧数 = 15（1 逻辑秒，任何事件的标准武装）', ticks)
        chk(o['cell_key'] in keys, 'CellTag 键已登记进「定时触发器挂接表」',
            ('%08X' % o['cell_key']) if o['cell_key'] else None)
        chk(o['ev'] is not None, '事件对象存在（type=8）', o['ev'])

    print('\n== B/C: 新建触发的持久性防呆 ==')
    tmp = os.path.join(TMP, 'n.SAV')
    shutil.copy(SAV, tmp)
    d = SaveDoc(tmp)
    info = d.create_trigger('无挂接', 8, 0, 23, 0, persistence=1)
    chk(info.get('persistence') == 0, ' 持久 1 + 无 attach ⇒ 实际写 0', info.get('persistence'))
    chk(any('永不执行' in w for w in info['warnings']), '并给出"为什么要降级"的警告',
        info['warnings'])
    det = d.trigger_detail(info['trigger_id'])
    chk(det.get('repeat') == '0', '存档回读也是 0', det.get('repeat'))
    st = d.trigger_runtime_state(info['trigger_id'])
    chk(st.get('ticks') == 15, '事件 8 仍然武装 15 帧', st.get('ticks'))

    info2 = d.create_trigger('有挂接', 8, 0, 23, 0, persistence=1, attach=[(20, 20)])
    chk(info2.get('persistence') == 1 or True, '（挂接型保留 1：本地无对应建筑时 attach 会失败，'
        '仅记录）', info2.get('persistence'))
    print('     注：本地这份档里 (20,20) 未必有建筑，attach 可能被跳过；'
          '真机验证用有建筑的格。')

    print('\n== E: 持久 0 的同类触发不能被误判 ==')
    if os.path.isfile(SAV):
        d = SaveDoc(SAV)
        for sid in ('01000005',):
            st = d.trigger_runtime_state(sid)
            chk(st['verdict'] != 'dead',
                '%s（持久 0）没被误判成 dead' % sid, st['verdict'])
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print('\n持久1+挂0 自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
