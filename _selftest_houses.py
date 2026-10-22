# -*- coding: utf-8 -*-
"""「所属方金钱」功能回归自检

用法:
    python _selftest_houses.py                # 用内置的几个存档
    python _selftest_houses.py <存档> [存档…]  # 指定存档

只读原档，写盘一律落在系统临时目录。
"""
import sys, os, tempfile, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))
sys.path.insert(0, HERE)

DEFAULT = [r'D:\233\RA2YR\SAVE1111.SAV', r'E:\YURI\修改.SAV',
           r'D:\233\RA2YR\ALL02UMD.sav', r'E:\YURI\ALL01UMD.sav',
           r'E:\RA2存档备份_20261013\实验暂存\ALL01UMD.sav',
           r'D:\233\RA2YR\ALL01UMD.sav']

spec = importlib.util.spec_from_file_location('_m', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

paths = sys.argv[1:] or [p for p in DEFAULT if os.path.exists(p)]
if not paths:
    print('没有可用的测试存档'); sys.exit(1)

print('%-30s %-11s %-11s %-5s %s' % ('文件', '阵营', '金钱', '数组', '非零所属方'))
for p in paths:
    try:
        d = m.SaveDoc(p)
        hs = d.houses()
        nz = ['%s=%s' % (h['short'], h['money']) for h in hs if h['money']]
        print('%-30s %-11s %-11s %-5d %s' % (os.path.basename(p), d.info.get('玩家阵营'),
                                             d.info.get('金钱'), len(hs), ' '.join(nz[:4])))
    except Exception as e:
        print('%-30s ERR %r' % (os.path.basename(p), e))

# GUI 冒烟 + 编辑/保存/回读
src = paths[0]
if os.path.exists(src):
    print()
    print('--- GUI 冒烟（数据源: %s）---' % os.path.basename(src))
    app = m.App(); app.withdraw(); app.update_idletasks()
    doc = m.SaveDoc(src)
    app._set_doc(doc)
    app.update_idletasks()
    print(' 所属方表行数 =', len(app.th.get_children()))
    hs = doc.houses()
    targets = [h['short'] for h in hs if h['verified']][:4]
    if targets:
        exp = {}
        for k, s in enumerate(targets):
            exp[s] = 200000 + k * 1111
            app._apply_house(s, exp[s])
        out = os.path.join(tempfile.gettempdir(), '_ra2_houses_check.SAV')
        print(' 已改 %s' % exp)
        print(' 保存 ->', out)
        print(' ', doc.save(out))
        got = {h['short']: h['money'] for h in m.SaveDoc(out).houses()}
        bad = [(k, got.get(k), v) for k, v in exp.items() if got.get(k) != v]
        print(' 回读:', {k: got.get(k) for k in exp})
        print(' 结果:', 'PASS ✓' if not bad else 'FAIL ✗ %s' % bad)
        os.remove(out)
    else:
        print(' （该档未定位到可写入的所属方，跳过）')

    # ---- 事件 / 行为 回归 ----
    evs, acs = doc.events(), doc.actions()
    print(' 事件表 %d 条 / 行为表 %d 条' % (len(evs), len(acs)))
    for e in evs[:6]:
        print('   E 0x%X  %d %s  arg=%s(%s)' % (e['off'], e['type'], e['name'],
                                               e['arg'], e['arg_text']))
    for a in acs[:6]:
        print('   A 0x%X  %d %s  arg=%s(%s)' % (a['off'], a['type'], a['name'],
                                               a['arg'], a['arg_text']))
    if evs or acs:
        old_e = evs[0]['type'] if evs else None
        old_a = acs[0]['type'] if acs else None
        if evs:
            doc.set_event(evs[0], etype=(6 if old_e != 6 else 13), arg=77)
        if acs:
            doc.set_action(acs[0], atype=(6 if old_a != 6 else 16), arg=0)
        out = os.path.join(tempfile.gettempdir(), '_ra2_evact_check.SAV')
        print(' 保存 ->', out)
        print(' ', doc.save(out))
        d2 = m.SaveDoc(out)
        e2, a2 = d2.events(), d2.actions()
        ok_e = (not evs) or (e2 and e2[0]['type'] == (6 if old_e != 6 else 13)
                             and e2[0]['arg'] == 77)
        ok_a = (not acs) or (a2 and a2[0]['type'] == (6 if old_a != 6 else 16))
        print(' 回读事件[0]=%s 行为[0]=%s' % (e2[0]['type'] if e2 else None,
                                             a2[0]['type'] if a2 else None))
        print(' 结果:', 'PASS ✓' if (ok_e and ok_a) else 'FAIL ✗')
        os.remove(out)
    # ---- 危险类型 0 必须被拒绝（写 0 会让读档崩溃）----
    def _must_reject(fn, label):
        try:
            fn()
        except ValueError:
            print(' %s: PASS ✓（被拒绝）' % label)
        else:
            print(' %s: FAIL ✗（竟然允许写入 0）' % label)
    if len(doc.events()) > 1:
        _must_reject(lambda: doc.set_event(doc.events()[1], etype=0, arg=0),
                     '类型0防护 set_event')
    if doc.actions():
        _must_reject(lambda: doc.set_action(doc.actions()[0], atype=0, arg=0),
                     '类型0防护 set_action')
    # ---- 「流逝时间」必须同步运行期帧数（秒只是地图副本，引擎按帧计时）----
    # 用**重新载入的干净档**测（上面的用例已把事件类型改掉了）
    doc_t = m.SaveDoc(src)
    ev13 = [e for e in doc_t.events() if e['type'] == 13]
    if ev13 and doc_t.tick_blocks():
        e13 = ev13[0]
        old = e13['arg']
        new = 42 if old != 42 else 43
        doc_t.set_event(e13, arg=new)
        tb = doc_t.tick_blocks()
        got = list(tb.values())[0] if len(tb) == 1 else None
        ok = (got == new * 15) if got is not None else None
        print(' 时间同步 %d→%d 秒: 帧数=%s (期望 %d)  %s'
              % (old, new, got, new * 15,
                 'PASS ✓' if ok else ('— 已提示未同步（多触发档属正常）' if ok is None
                                      else 'FAIL ✗')))
        print('   ', doc_t.time_sync_note)
        out = os.path.join(tempfile.gettempdir(), '_ra2_time_check.SAV')
        print(' ', doc_t.save(out))
        c = m.SaveDoc(out)
        tb2 = c.tick_blocks()
        print(' 回读: 事件=%s  帧数=%s' % ([(x['type'], x['arg']) for x in c.events()],
                                          list(tb2.values())))
        os.remove(out)
    else:
        print(' （该档没有「13 流逝时间」事件或没有帧数块，跳过时间同步测试）')

    # ---- 触发启用/禁用：写「触发类 GUID 前 8 字节」那 1 字节，往返一致 ----
    tgs = [r for r in doc.records if r.kind == '触发' and r.can_toggle]
    if tgs:
        t0 = tgs[0]
        old = t0.enabled
        doc.toggle_trigger(t0, not old)
        out = os.path.join(tempfile.gettempdir(), '_ra2_trg_check.SAV')
        doc.save(out)
        c = m.SaveDoc(out)
        ct = [r for r in c.records if r.kind == '触发' and r.name == t0.name][0]
        ok = (ct.enabled == (not old))
        print(' 触发 %s: enabled %s -> %s  结果: %s' % (t0.name, old, ct.enabled,
                                                       'PASS ✓' if ok else 'FAIL ✗'))
        os.remove(out)
    else:
        print(' （该档没有可写的触发，跳过启用/禁用测试）')
    app.destroy()
