# -*- coding: utf-8 -*-
"""自检：「所属方型」参数 = **地图 [Countries] 的国家序号**

★★2026-11-09 定案，**推翻** 2026-10-20 那条"0 = 玩家自己"的旧结论。
  旧结论的样本是**盟军第一关**（玩家 = Americans = 国家序号 0），0 号碰巧就是玩家；
  换到苏俄关（玩家 = Russians = 8）立刻翻车：编辑器把「苏俄（玩家）」翻成 0 写进去，
  引擎当成"美国单位进楼"，触发永远不执行（01000284 间谍进科技电厂，实测）。

判据（玩家样本）：
  · `sov01umd.map` `[Countries]` = 0=Americans … 7=Confederation **8=Russians** … 12=Neutral 13=Special
  · 地编改好能触发的 `SAVE0029.SAV`：事件对象 `+0x48` = **8**；编辑器改的 = **0**，不触发
  · 旁证：地图给"中立"写 **12**，旧口径的 0..11 列表根本表达不了 → 两套编号不是一回事

断言：
  A 国家序号表：0=美国、7=古巴、8=苏俄、12=中立；玩家 Russians 的 idx=8
  B 胜利者/失败者行为的参数 = 玩家国家序号（本档 8），文案含「苏俄（玩家）」
  C 编辑器参数下拉里能选到「8 苏俄（玩家）」，写回 exactly 8
  D 参数 0 的文案是「美国」，不是「苏俄（玩家）」
"""
import io
import os
import shutil
import sys
import importlib.util
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc

SAV = next((p for p in (r'E:\YURI\SAVE0029.SAV', r'E:\YURI\SAVE4AE1_修改2.SAV',
                        r'E:\YURI\SOV01UMD.sav', r'E:\YURI\SAVE4AE1.SAV')
            if os.path.exists(p)), None)
MAP = r'E:\YURI\sov01umd.map'
OK = FAIL = 0


def chk(c, msg):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


if not SAV:
    print('没有样本存档，跳过本自检（不算失败）')
    sys.exit(0)

d = SaveDoc(SAV)
if os.path.exists(MAP):
    d.attach_map(MAP)
print('样本 %s　玩家 = %s　地图 = %s'
      % (os.path.basename(SAV), d.player_house(),
         os.path.basename(MAP) if os.path.exists(MAP) else '（无）'))

print('\n== A: 国家序号表 ==')
co = d.country_order()
by = {h['idx']: h for h in co}
chk(by.get(0, {}).get('short') == 'Americans', '0 = Americans（%r）' % by.get(0))
chk(by.get(7, {}).get('short') == 'Confederation', '7 = Confederation（%r）' % by.get(7))
chk(by.get(8, {}).get('short') == 'Russians', '8 = Russians（%r）' % by.get(8))
chk(by.get(12, {}).get('short') == 'Neutral', '12 = Neutral（%r）' % by.get(12))
chk(by.get(13, {}).get('short') == 'Special', '13 = Special（%r）' % by.get(13))
me = next((h for h in co if h['is_player']), None)
chk(me is not None and me['idx'] == 8,
    '玩家 Russians 的 idx = 8（实际 %r）' % (me and me['idx']))
chk(d.house_index('苏俄') == 8, 'house_index(苏俄) == 8（实际 %r）' % d.house_index('苏俄'))
chk(d.house_index('中立') == 12, 'house_index(中立) == 12（实际 %r）' % d.house_index('中立'))

print('\n== D: 参数文案 ==')
chk(d._arg_text(2, 8) == '苏俄（玩家）', '_arg_text(2,8) = %r' % d._arg_text(2, 8))
chk(d._arg_text(2, 0) == '美国', '_arg_text(2,0) = %r（旧版错标成「苏俄（玩家）」）'
    % d._arg_text(2, 0))
chk(d._arg_text(2, 12) == '中立', '_arg_text(2,12) = %r' % d._arg_text(2, 12))

print('\n== B: 胜利者/失败者行为的参数含义 ==')
own = d.action_owner_sids()
vic = [(a['arg'], a['arg_text'], own.get(a['key'])) for a in d.actions() if a['type'] in (1, 2)]
print('   %s' % vic[:6])
if vic:
    chk(all(x[0] == 8 for x in vic[:6]),
        '★本档（玩家=苏俄）胜负行为参数都是 8（%s）' % [x[0] for x in vic[:6]])
    chk(all('苏俄（玩家）' in x[1] for x in vic[:6]),
        '★文案都是「苏俄（玩家）」（%s）' % [x[1] for x in vic[:6]][:3])
else:
    print('   （本档没有胜负行为，跳过）')

print('\n== C: 编辑器参数下拉（在副本上改，不动原档）==')
tmp = os.path.join(os.path.dirname(SAV), '_selftest_country_tmp.SAV')
shutil.copyfile(SAV, tmp)
spec = importlib.util.spec_from_file_location('sedgui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
for _n in ('showinfo', 'showwarning', 'showerror'):
    setattr(m.messagebox, _n, lambda *a, **k: None)
m.messagebox.askyesno = lambda *a, **k: True
app = m.App()
app.withdraw()
ed = None
try:
    app.doc = SaveDoc(tmp)
    if os.path.exists(MAP):
        app.doc.attach_map(MAP)
    app._set_doc(app.doc)
    app.refresh_records()
    sid = '01000284'
    det = app.doc.trigger_detail(sid)
    if not det['name']:
        sid = next((k for k, v in vic if v), None) or '01000284'
        det = app.doc.trigger_detail(sid)
    ed = m.TriggerEditor(app, sid)
    app.update()
    ed.tv_ev.selection_set('1')
    ed.tv_ev.focus('1')
    ed._ev_pick()
    app.update()
    vals = list(ed.cb_evarg['values'])
    print('   事件参数候选：%s' % vals)
    want = next((v for v in vals if v.startswith('8 ')), None)
    chk(want is not None and '苏俄' in want, '事件参数下拉里有「8 苏俄…」（%r）' % want)
    if want:
        ed.var_evarg.set(want)
        chk(ed._parse_arg(want) == 8, '下拉文字 "8 苏俄（玩家）" 解析成 8')
        ed.apply_event()
        app.update()
        det2 = app.doc.trigger_detail(sid)
        got = (det2.get('event') or {}).get('arg')
        chk(got == 8, '写回后存档里事件参数 = 8（实际 %r）' % got)
        t0 = next((v for v in list(ed.cb_evarg['values']) if v.startswith('0 ')), None)
        if t0:
            ed.tv_ev.selection_set('1')
            ed.tv_ev.focus('1')
            ed._ev_pick()
            ed.var_evarg.set(t0)
            ed.apply_event()
            app.update()
            det3 = app.doc.trigger_detail(sid)
            chk((det3.get('event') or {}).get('arg') == 0, '改成 "0 美国" 后读回 0')
            chk('美国' in ((det3.get('event') or {}).get('arg_text') or ''),
                '文案变「%s」' % (det3.get('event') or {}).get('arg_text'))
            ed.tv_ev.selection_set('1')
            ed.tv_ev.focus('1')
            ed._ev_pick()
            ed.var_evarg.set(want)
            ed.apply_event()
            app.update()
finally:
    try:
        if ed is not None:
            ed.destroy()
    except Exception:
        pass
    try:
        app.destroy()
    except Exception:
        pass
    try:
        os.remove(tmp)
    except Exception:
        pass

print('\n所属方参数自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
