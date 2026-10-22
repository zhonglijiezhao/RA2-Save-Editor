# -*- coding: utf-8 -*-
"""自检：「所属方型」参数 = 玩家优先的阵营序号（2026-10-20 玩家实测纠正）

玩家把「胜利者是」参数填成 8（以为 8=苏俄，因为旧版按地编 [Countries] 枚举标注），
进游戏变成"古巴获胜"⇒ 玩家失败。真实语义：**0 = 玩家自己**，其余按存档阵营顺序。

证据（改.SAV）：玩家 = Russians；任务自带的「1a最后胜利a」行为 1 参数 = **0**。
断言：
  A house_order() 里 0 号就是玩家自己
  B 同档两条「胜利者」行为的参数含义：0 → 苏俄（玩家）；8 → 古巴
  C 编辑器里选中该行后，参数下拉显示 "8 古巴"，候选 = 全部所属方
  D 用编辑器把参数改成 0 → 读回 0，含义变「苏俄（玩家）」；再改回 8
"""
import io
import os
import sys
import importlib.util
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc

SAV = next((p for p in (r'E:\YURI\改.SAV', r'E:\YURI\SAVE0029.SAV',
                        r'C:\Users\zhao5\Desktop\工程师\SAVE48DB.SAV')
            if os.path.exists(p)), r'C:\Users\zhao5\Desktop\工程师\SAVE48DB.SAV')
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
print('样本 %s　玩家 = %s' % (os.path.basename(SAV), d.player_house()))

print('\n== A: house_order() 0 号 = 玩家 ==')
ho = d.house_order()
chk(bool(ho), '读得到所属方顺序（%d 个）' % len(ho))
chk(ho[0]['is_player'], '0 号是玩家（%s / %s）' % (ho[0]['short'], ho[0]['cn']))
print('   顺序：%s' % ', '.join('%d%s' % (i, h['cn']) for i, h in enumerate(ho)))
pn = next((h['cn'] for h in ho if h['is_player']), '')
chk(d.house_index(pn) == 0, 'house_index(玩家中文名) == 0')

print('\n== B: 胜利者行为的参数含义 ==')
own = d.action_owner_sids()
vic = [(a['arg'], a['arg_text'], own.get(a['key'])) for a in d.actions() if a['type'] == 1]
print('   %s' % vic)
zero = [x for x in vic if x[0] == 0]
if zero:
    chk('玩家' in zero[0][1], '参数 0 显示「%s」含"玩家"' % zero[0][1])
eight = [x for x in vic if x[0] == 8]
if eight:
    chk('古巴' in eight[0][1], '参数 8 显示「%s」（古巴，不是苏俄）' % eight[0][1])

print('\n== B2: 换一个玩家阵营（美国）再验一次「0 = 玩家」==')
# ★2026-10-20 玩家追问"玩家不管是哪个国家编号都恒为 0 吗" —— 跨档实证：
#   玩家=美国 的存档里，胜利者行为参数也全是 0。
alt = None
for cand in (os.path.join(HERE, '_exp', '母本_原存档.SAV'),
             os.path.join(HERE, '_exp', '_regr_T3.SAV'),
             r'_exp\sample_新开局_SAVE0029.SAV'):
    if os.path.exists(cand):
        alt = cand
        break
if not alt:
    print('   （没有"玩家=美国"的样本，跳过）')
else:
    d2 = SaveDoc(alt)
    ho2 = d2.house_order()
    vics2 = [a for a in d2.actions() if a['type'] in (1, 2)]
    print('   %s 玩家=%s  胜利行为=%s' % (os.path.basename(alt), d2.player_house(),
                                       [(a['arg'], a['arg_text']) for a in vics2[:3]]))
    chk(d2.player_house() != d.player_house(), '这一档的玩家阵营与主样本不同（%s vs %s）'
        % (d2.player_house(), d.player_house()))
    chk(ho2[0]['is_player'], '这一档 0 号也是玩家（%s）' % ho2[0]['short'])
    if vics2:
        chk(all(a['arg'] == 0 for a in vics2),
            '★玩家=%s 时，胜利者行为参数仍全是 0（%s）'
            % (d2.player_house(), [a['arg'] for a in vics2]))
    else:
        print('   （该档没有胜负行为，只验证了 0 号=玩家）')

print('\n== C: 编辑器里参数下拉 ==')
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
    app.doc = SaveDoc(SAV)
    app._set_doc(app.doc)
    app.refresh_records()
    sid = (eight[0][2] if eight else (zero[0][2] if zero else None))
    if not sid:
        chk(False, '本档没有「胜利者」行为，跳过编辑器检查')
    else:
        ed = m.TriggerEditor(app, sid)
        app.update()
        ed.tv_ac.selection_set('1')
        ed.tv_ac.focus('1')
        ed._ac_pick()
        app.update()
        chk(len(ed.cb_acarg['values']) == len(app.doc.house_order()),
            '参数下拉候选 = 所属方个数（%d）' % len(ed.cb_acarg['values']))
        chk('玩家' in ed.cb_acarg['values'][0], '候选第 0 项标了"玩家"（%s）'
            % ed.cb_acarg['values'][0])
        print('   当前参数框：%r' % ed.var_acarg.get())
        # D: 改成 0
        ed.var_acarg.set(ed.cb_acarg['values'][0])
        ed.apply_action()
        det = app.doc.trigger_detail(sid)
        chk(det['actions'][0]['arg'] == 0, '改成 0 后读回 arg=0（%s）'
            % det['actions'][0]['arg_text'])
        chk('玩家' in det['actions'][0]['arg_text'], '含义显示「%s」'
            % det['actions'][0]['arg_text'])
        # 改回 8（若原来就是 8）
        if eight:
            idx = [i for i, v in enumerate(app.doc.house_order()) if v['short'] == 'Confederation']
            if idx:
                ed.tv_ac.selection_set('1')      # 改完会 reload，选中会丢 ⇒ 重新选
                ed.tv_ac.focus('1')
                ed._ac_pick()
                ed.var_acarg.set(ed.cb_acarg['values'][idx[0]])
                ed.apply_action()
                d2 = app.doc.trigger_detail(sid)
                chk(d2['actions'][0]['arg'] == idx[0], '改回 %d（%s）'
                    % (idx[0], d2['actions'][0]['arg_text']))
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

print('\n所属方参数自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
