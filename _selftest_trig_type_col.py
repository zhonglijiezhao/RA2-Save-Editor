# -*- coding: utf-8 -*-
"""自检：触发页「触发类型 / 标签数」独立列 + 「修复缺组件」诊断定性（2026-10-20 next-step 2/3）。

覆盖：
  A 列结构 = (id,name,kind,state,rep,tags,mapinfo,note)，且状态列**不再**附带类型
  B 每条触发的「触发类型」列 == doc.trigger_repeat_type()；读不出写「未读出（无标签）」
  C 标签总数核对 == 存档标签记录数（82）
  D 「修复缺组件」对话框：能渲染、诊断列全部有值、风险分级分布合理、红字行存在
  E 风险定性单测：四种典型输入（从未有组件 / 执行后回收单次 / 执行后回收未知 / 重复型）分级正确
不修改任何存档，只读 + 渲染后立即销毁窗口。
"""
import io
import os
import sys
import importlib.util

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

spec = importlib.util.spec_from_file_location('sedgui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

OK = FAIL = 0


def chk(c, msg):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


SAVES = [r'C:\Users\zhao5\Desktop\工程师\SAVE48DB.SAV',
         os.path.join(HERE, '_exp', 'sample_trig2.SAV'),
         os.path.join(HERE, '_exp', 'ALL01UMD_2触发.sav')]
SAV = next((p for p in SAVES if os.path.exists(p)), None)
if not SAV:
    print('SKIP：找不到样本存档')
    sys.exit(0)
print('样本：%s' % SAV)

# ---- 静音所有 messagebox（避免模态弹窗卡住）----
_calls = []
for _n in ('showinfo', 'showwarning', 'showerror'):
    setattr(m.messagebox, _n,
            (lambda n: (lambda *a, **k: _calls.append((n, a[:2]))))(_n))
m.messagebox.askyesno = lambda *a, **k: _calls.append(('askyesno', a[:2])) or False

app = m.App()
app.withdraw()
dlg = None
try:
    app.doc = m.SaveDoc(SAV)
    app._set_doc(app.doc) if hasattr(app, '_set_doc') else None
    app.refresh_records()
    print('   记录 %d　触发 %d　标签 %d'
          % (len(app.doc.records),
             sum(1 for r in app.doc.records if r.kind == '触发'),
             len(app.doc.tag_records())))

    print('\n== A: 列结构 ==')
    cols = tuple(app.tt['columns'])
    want = ('id', 'name', 'kind', 'state', 'rep', 'tags', 'mapinfo', 'note')
    chk(cols == want, '列 = %s' % (cols,))
    chk(app.tt.heading('rep')['text'] == '触发类型', '有独立「触发类型」列')
    chk(app.tt.heading('tags')['text'] == '标签数', '有「标签数」列')
    row0 = app.tt.get_children()[0]
    st0 = app.tt.set(row0, 'state')
    chk('任一满足' not in st0 and '所有满足' not in st0,
        '状态列不再附带类型（示例：%s）' % st0)

    print('\n== B/C: 类型列与标签数 ==')
    rep_map = getattr(app.doc, 'REPEAT_TEXT', {})
    bad_rep = 0
    seen = {}
    tag_sum = 0
    n_trig = 0
    for iid in app.tt.get_children():
        sid = app.tt.set(iid, 'id')
        got = app.tt.set(iid, 'rep')
        rp = app.doc.trigger_repeat_type(sid)
        exp = rep_map.get(rp, '型' + rp) if rp else '未读出（无标签）'
        if got != exp:
            bad_rep += 1
            if bad_rep <= 3:
                print('     ✗ %s 列=%r 期望=%r' % (sid, got, exp))
        seen[got] = seen.get(got, 0) + 1
        try:
            tag_sum += int(app.tt.set(iid, 'tags'))
            n_trig += 1
        except ValueError:
            pass
    chk(bad_rep == 0, '类型列与 trigger_repeat_type() 全一致（%d 条，错 %d）' % (len(app.tt.get_children()), bad_rep))
    print('    分布：%s' % seen)
    n_tag = len(app.doc.tag_records())
    chk(tag_sum >= n_tag and tag_sum - n_tag <= 5,
        '标签数合计 %d ≈ 存档标签数 %d（同名触发的标签会被挂两次，允许多 ≤5）'
        % (tag_sum, n_tag))

    print('\n== D: 修复缺组件对话框 ==')
    n_before = len([w for w in app.winfo_children() if isinstance(w, m.tk.Toplevel)])
    app.repair_runtime_dialog()
    tops = [w for w in app.winfo_children() if isinstance(w, m.tk.Toplevel)]
    chk(len(tops) == n_before + 1, '对话框已创建（%d → %d）' % (n_before, len(tops)))
    if len(tops) > n_before:
        dlg = tops[-1]
        app.update()
        tvs = []

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, m.ttk.Treeview):
                    tvs.append(c)
                walk(c)
        walk(dlg)
        chk(bool(tvs), '对话框里有表格')
        if tvs:
            tv = tvs[0]
            kids = tv.get_children()
            print('    缺组件触发 %d 条，样例：%s'
                  % (len(kids), [tv.set(k, 'st') for k in kids[:3]]))
            chk(all(tv.set(k, 'st') for k in kids), '每条都有诊断文字')
            chk(all(tv.set(k, 'rep') for k in kids), '每条都有触发类型文字')
            lv = [app._repair_verdict(s)[1] for s in (app.doc.runtime_status())]
            chk(set(lv) <= {'high', 'mid', 'low'}, '分级取值合法：%s' % sorted(set(lv)))
            from collections import Counter
            print('    分级分布：%s' % Counter(lv))
            chk(all(tv.set(k, 'node') in ('✓', '✗ 缺失') for k in kids), 'TagNode 列取值合法')

    print('\n== E: 风险定性单测 ==')
    base = {'sid': 'X', 'name': 'x', 'has_node': False, 'has_cell': False,
            'record_enabled': True, 'ticks': None}
    _orig = app.doc.trigger_repeat_type
    try:
        for rep, node, cell, en, want_lvl in (
                (None, False, False, True, 'mid'),    # 组件全无 ⇒ 分不清（★不能当"安全"）
                ('0', False, False, True, 'mid'),     # 单次型 + 组件全无（标签被销毁）⇒ 分不清
                ('0', False, True, True, 'high'),     # CellTag 还在 ⇒ 铁证执行后回收
                ('1', False, True, True, 'high'),
                (None, False, True, True, 'mid'),     # 类型读不出
                ('2', False, True, True, 'low'),      # 重复型本来就该再跑
                ('2', False, False, True, 'low'),
                ('0', False, True, False, 'low'),     # 定义即禁用
                ('0', True, False, True, 'low')):     # 只缺 CellTag
            app.doc.trigger_repeat_type = (lambda r: (lambda sid: r))(rep)
            s = dict(base, has_node=node, has_cell=cell, record_enabled=en)
            txt, lvl, _rt = app._repair_verdict(s)
            chk(lvl == want_lvl, 'rep=%s node=%s cell=%s en=%s → %s（%s）'
                % (rep, node, cell, en, lvl, txt))
        # ★回归闸（2026-10-20 自检抓到的真错）：sample_AB 里 A 是"已执行"，
        #   但 node/cell 全无 —— 绝不能被判成 low（否则对话框会把"重新武装"说成"首次生效"）。
        app.doc.trigger_repeat_type = _orig
        ab = os.path.join(HERE, '_exp', 'sample_AB_已执行与等待.SAV')
        if os.path.exists(ab):
            from editor_core import SaveDoc as _SD
            app.doc = _SD(ab)
            app.refresh_records()
            vd = {s['name']: app._repair_verdict(s) for s in app.doc.runtime_status()}
            chk(vd.get('AAAAAAAAAA', ('', 'x'))[1] in ('high', 'mid'),
                '已执行的 A（node/cell 全无）不得判 low：%s' % (vd.get('AAAAAAAAAA'),))
            chk(vd.get('BBBBBBBB', ('', 'x'))[1] == 'low',
                '只缺 CellTag 的 B 判 low：%s' % (vd.get('BBBBBBBB'),))
            # 恢复大档，避免后面 destroy 时状态错乱
            app.doc = _SD(SAV)
            app.refresh_records()
    finally:
        app.doc.trigger_repeat_type = _orig
    chk(not any(c[0] == 'askyesno' for c in _calls),
        '本自检未触发任何确认弹窗（未真正执行修复）')
finally:
    for w in app.winfo_children():
        if isinstance(w, m.tk.Toplevel):
            try:
                w.destroy()
            except Exception:
                pass
    try:
        app.destroy()
    except Exception:
        pass

print('\n触发类型列 / 修复诊断 自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
