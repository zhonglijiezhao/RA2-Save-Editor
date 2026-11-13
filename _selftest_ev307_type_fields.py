# -*- coding: utf-8 -*-
"""§307 自检：TypeClass 全字段表（19 项）+ 「型号属性」对话框。

★2026-11-13：字段偏移全是**相关性扫描**定出来的（rules 显式值为样本、逐偏移比对、
  再换第二份存档复验），不是猜的。这里钉住四件事：
    ① 19 个字段都能读、值和 rules/常识对得上；
    ② 换一份存档（ALL01UMD.sav）仍然成立（偏移是布局常量）；
    ③ 写入有边界校验（越界/布尔填 2/非整数都要被拒），写完长度不变、能存盘往返；
    ④ 「型号属性」对话框：填格子 → 应用 → 存档真的变了 → 再改回去。

★`MaxDebris`（大写 D）与 `Maxdebris`（小写 d）是两个键，引擎只认前者 —— 自检里专门钉了。

跑法：D:/py/python.exe _selftest_ev307_type_fields.py
"""
import importlib.util
import io
import os
import struct
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

SAV = r'E:/YURI/SAVE0E29_修改.SAV'
SAV2 = r'E:/YURI/ALL01UMD.sav'
PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


for _p in (SAV, SAV2):
    if not os.path.exists(_p):
        print('（跳过：样本不存在 %s）' % _p)
        raise SystemExit(0)

spec = importlib.util.spec_from_file_location('gui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from editor_core import SaveDoc                                   # noqa: E402

print('\n== 1. 字段表本身 ==')
ck(len(SaveDoc.TYPE_FIELDS) >= 18, '共 %d 个字段' % len(SaveDoc.TYPE_FIELDS))
keys = [f[0] for f in SaveDoc.TYPE_FIELDS]
ck(len(set(keys)) == len(keys), '字段键不重复')
offs = [f[1] for f in SaveDoc.TYPE_FIELDS]
ck(len(set(offs)) == len(offs), '偏移不重叠（各占一处）')
kinds = {f[2] for f in SaveDoc.TYPE_FIELDS}
ck(kinds <= {'i32', 'u8'}, '类型只有 i32 / u8（定长、原地写）')

print('\n== 2. APOC 读出来的值（对玩家贴的 [APOC] 段）==')
doc = SaveDoc(SAV)
doc.set_game_dir(r'E:/YURI')
f = {x['key']: x for x in doc.type_fields('APOC')}
want = {'strength': 800, 'sight': 6, 'cost': 1750, 'soylent': 1750, 'threatposed': 40,
        'points': 60, 'rot': 5, 'zfudgecol': 9, 'zfudgetun': 15,
        'selfheal': 1, 'explodes': 1, 'crusher': 1, 'turret': 1, 'crewed': 0,
        'selectable': 1, 'crate': 1}
for k, v in want.items():
    ck(f[k]['value'] == v, '%-11s = %-6s（rules %s）' % (k, f[k]['value'], f[k]['rules']))
ck(f['imm_psi']['rules'] in ('no', None, 'No'),
   '免疫心灵控制：存档 0，rules %r（这份是改之前存的）' % f['imm_psi']['rules'])

# ★大小写敏感那条：APOC 只写了小写 `Maxdebris`，正确大小写的 `MaxDebris` 不存在
ck(f['maxdebris']['rules'] is None, 'APOC 没有 `MaxDebris`（大写 D）这个键')
alt = f['maxdebris'].get('rules_alt') or {}
ck(str(alt.get('key')) == 'Maxdebris', '但捞到了大小写变体：%r' % (alt.get('key'),))
ck(f['maxdebris']['value'] == 0, '碎片数在存档里是缺省 0（引擎不认小写键）')

print('\n== 3. 换一份存档（ALL01UMD.sav）仍然成立 ==')
d2 = SaveDoc(SAV2)
d2.set_game_dir(r'E:/YURI')
rules2 = {s: {str(k).upper(): v for k, v in kv.items()}
          for s, kv in (d2._rules_all() or {}).items()}
ut2 = d2.unit_type_objects()
tot = ok = 0
for k, rel, kind, rkey, label, lo, hi, samples in SaveDoc.TYPE_FIELDS:
    for tid, info in ut2.items():
        kv = rules2.get(tid) or {}
        # ★碎片数得按原样大小写取（MaxDebris）；统一转大写会捞到另一个键
        rv = kv.get('MAXDEBRIS') if rkey == 'MaxDebris' else kv.get(rkey.upper())
        if rv is None:
            continue
        s = str(rv).strip().lower()
        off = info['off'] + rel
        if kind == 'u8':
            if s not in ('yes', 'no', 'true', 'false', '0', '1'):
                continue
            expect = 1 if s in ('yes', 'true', '1') else 0
            got = d2.plain[off]
        else:
            try:
                expect = int(float(s.rstrip('%')))
            except Exception:
                continue
            got = struct.unpack_from('<i', d2.plain, off)[0]
        tot += 1
        ok += 1 if got == expect else 0
ck(tot > 500, '第二份存档上可比的样本 %d 条' % tot)
ck(ok >= tot * 0.95, '一致 %d/%d（≥95%% ⇒ 偏移不是拟合出来的巧合）' % (ok, tot))

print('\n== 4. 写入与边界校验 ==')
old = f['sight']['value']
info = doc.set_type_field('APOC', 'sight', 8)
ck(info['old'] == old and info['new'] == 8, '写 sight：%d → %d' % (info['old'], info['new']))
ck(len(doc.plain) == len(doc.plain_orig), '明文长度不变')
ck(doc.type_fields('APOC')[1]['value'] == 8, '读回 8')
for bad, why in ((999, '超出上限'), (-1, '低于下限')):
    try:
        doc.set_type_field('APOC', 'sight', bad)
        ck(False, '越界值 %r 居然写进去了' % bad)
    except Exception:
        ck(True, '%s 被拒（sight=%r）' % (why, bad))
try:
    doc.set_type_field('APOC', 'turret', 2)
    ck(False, '布尔填 2 居然写进去了')
except Exception:
    ck(True, '布尔字段填 2 被拒')
try:
    doc.set_type_field('APOC', 'cost', 'abc')
    ck(False, '非整数居然写进去了')
except Exception:
    ck(True, '非整数被拒')
ck(doc.set_type_field('APOC', 'cost', '0x6D6', dry_run=True)['new'] == 1750,
   '支持 0x 进制写法（0x6D6 → 1750）')
doc.set_type_field('APOC', 'sight', old)                    # 改回去

print('\n== 5. 存盘往返（同时验两个字段）==')
d3 = SaveDoc(SAV)
d3.set_game_dir(r'E:/YURI')
d3.set_type_field('MTNK', 'strength', 333)
d3.set_type_field('MTNK', 'turret', 1)
tmp = os.path.join(tempfile.gettempdir(), '_ev307_fields.SAV')
try:
    d3.save(tmp, backup=False)
    d4 = SaveDoc(tmp)
    g = {x['key']: x for x in d4.type_fields('MTNK')}
    ck(g['strength']['value'] == 333, '存盘后 MTNK 血量上限 = 333')
    ck(g['turret']['value'] == 1, '存盘后 MTNK 炮塔 = 1')
    ck({x['key']: x for x in d4.type_fields('APOC')}['strength']['value'] == 800,
       '别的型号没被带坏（APOC 仍 800）')
finally:
    try:
        os.remove(tmp)
    except Exception:
        pass

print('\n== 6. 「型号属性」对话框 ==')
app = m.App()
app.geometry('1600x1000+10+10')
for _ in range(20):
    app.update()
app._set_doc(SaveDoc(SAV))
for _ in range(20):
    app.update()
app.nb.select(10)
app.update()
app._wpn_mode.set('unit')
app._wpn_switch()
for _ in range(10):
    app.update()
rows = [r for r in app.utw.get_children()
        if str(app.utw.item(r, 'values')[0]).upper() == 'APOC']
ck(bool(rows), '单位视图里有 APOC 行')
if rows:
    app.utw.selection_set(rows[0])
    app.utw.focus(rows[0])
    app.update()
    app._unit_pick()
    app._unit_fields_dialog()
    for _ in range(10):
        app.update()
    ck(hasattr(app, '_uf_vars') and len(app._uf_vars) == len(SaveDoc.TYPE_FIELDS),
       '对话框里 %d 个输入框' % len(getattr(app, '_uf_vars', {})))
    cur = {x['key']: x['value'] for x in app.doc.type_fields('APOC')}
    app._uf_vars['points'].set(str(cur['points'] + 7))
    orig = m.messagebox.askyesno
    m.messagebox.askyesno = lambda *a, **k: True
    try:
        app._uf_apply.invoke()          # 对话框把按钮挂到 self 上了，直接按
        app.update()
    finally:
        m.messagebox.askyesno = orig
    now = {x['key']: x['value'] for x in app.doc.type_fields('APOC')}
    ck(now['points'] == cur['points'] + 7,
       '点「应用改动」后 points %d → %d' % (cur['points'], now['points']))
    ck('已写入' in (app.lbl_unit_msg.cget('text') or ''), '主页面给了「已写入」提示')

    # 「按 rules 填一遍」：先把血量上限填错，点一下应该被 rules 值填回来。
    # ★别拿"碎片数"验：APOC 只写了小写 Maxdebris，正确大小写的 MaxDebris 不存在
    #   ⇒ rules 侧是 None，本来就该跳过（这正是"键名大小写敏感"那条结论的体现）。
    app._uf_vars['strength'].set('999')
    app._uf_fill.invoke()
    app.update()
    ck((app._uf_vars['strength'].get() or '').strip() == '800',
       '「按 rules 填一遍」把血量上限填回 800（覆盖手填的 999）')
    ck((app._uf_vars['maxdebris'].get() or '').strip() == str(cur['maxdebris']),
       '碎片数没有 rules 值（只有小写 Maxdebris）⇒ 填一遍保持原样')

    app.doc.set_type_field('APOC', 'points', cur['points'])     # 改回去
    ck({x['key']: x['value'] for x in app.doc.type_fields('APOC')}['points'] == cur['points'],
       '已还原 points')

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
raise SystemExit(1 if FAIL else 0)
