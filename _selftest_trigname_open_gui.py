# -*- coding: utf-8 -*-
"""§SOV02 端到端自检（GUI 层）：**用触发编辑器打开/切换触发，名字必须一个字节都不变**。

玩家原话（2026-11-03）：「这 8 个触发，在存档编辑器用触发编辑器打开一次，
触发后面的数字就会被丢弃」。

复现链（SAVE5739.SAV，8 条 `Player_Has_Plant1..8`）：
  1. `reload()` 回填名字 → `var_name.set(det['name'])` 触发 trace → `_on_name_change()`；
  2. `_on_name_change()` 里当时有 `nm = nm[:16]`（**按字符**砍 16 个）
     ⇒ 'Player_Has_Plant1'（17 字符）→ 'Player_Has_Plant'；
  3. 该函数是**实时改名**（§257），于是**只是打开一下**就把截断结果写进了存档。
  顺带：`_on_pick_trigger()` 当时只调 `self.reload()`，而下拉切换必须显式传 sid，
  否则界面会弹回上一条 —— 这里一并回归。

跑法（需要带 tkinter 的 python，本项目是 D:/py/python.exe）：
    D:/py/python.exe _selftest_trigname_open_gui.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

SAV = r'E:\YURI\SAVE5739.SAV'
PREFIX = 'Player_Has_Plant'
NAME_OFF = 64          # 记录帧 +64 = 52 B 的 GBK 名字字段
NAME_SPAN = 52

OK = FAIL = 0


def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s' % msg)


def main():
    try:
        import tkinter  # noqa: F401
    except Exception:
        print('SKIP: 当前 python 没有 tkinter（GUI 用例要用 D:/py/python.exe 跑）')
        return 0
    if not os.path.exists(SAV):
        print('SKIP: 缺样本 %s' % SAV)
        return 0
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'sedgui', os.path.join(HERE, 'RA2存档编辑器.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    msgs = []
    for n in ('showinfo', 'showwarning', 'showerror'):
        setattr(m.messagebox, n, (lambda n: (lambda *a, **k: msgs.append((n, a[:1]))))(n))
    m.messagebox.askyesno = lambda *a, **k: (msgs.append(('askyesno', a[:1])) or True)

    app = m.App()
    app.withdraw()
    try:
        app.doc = m.SaveDoc(SAV)
        app._set_doc(app.doc)
        app.refresh_records()
        sids = [r.sid for r in app.doc.records
                if (r.name or '').startswith(PREFIX) and (r.name or '')[-1].isdigit()]
        print('样本 %s：%d 条带尾号的 %s*' % (os.path.basename(SAV), len(sids), PREFIX))
        chk(len(sids) >= 8, '读到 %d 条（应 ≥8）' % len(sids))
        if not sids:
            return 1

        def raw(sid):
            off = app.doc.trigger_rec(sid).off + NAME_OFF
            return bytes(app.doc.plain[off:off + NAME_SPAN])

        before = {s: (app.doc.trigger_rec(s).name, raw(s)) for s in sids}

        app.open_trigger_editor(sids[0])
        tops = [w for w in app.winfo_children() if isinstance(w, m.TriggerEditor)]
        chk(len(tops) == 1, '触发编辑器已打开')
        ed = tops[-1]
        app.update()
        ed.withdraw()

        print('== 逐条在下拉里选一遍（= 玩家在编辑器里切换触发）==')
        vals = list(ed.cb_t['values'])
        for s in sids:
            idx = [k for k, v in enumerate(vals) if v.startswith(s)]
            if not idx:
                chk(False, '下拉里找不到 %s' % s)
                continue
            ed.cb_t.current(idx[0])
            ed._on_pick_trigger()
            app.update()
            nm, rb = app.doc.trigger_rec(s).name, raw(s)
            chk(app.doc.trigger_rec(s).name == before[s][0] and rb == before[s][1],
                '%s：名字 %r 未变（输入框=%r）' % (s, nm, ed.var_name.get()))
            chk(ed._cur == s, '%s：下拉切换真的换人了（_cur=%r）' % (s, ed._cur))
        ed.destroy()
    finally:
        try:
            app.destroy()
        except Exception:
            pass
    print('\n§SOV02 GUI 回归：PASS %d / FAIL %d' % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
