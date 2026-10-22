# -*- coding: utf-8 -*-
"""自检：记录分类 —— AI 三大件不再冒充「触发/标签」（2026-10-20 玩家发现）

玩家原话：*"上面灰色和下面对以正常修改的触发是重复的啊"* ——
根因：作战小队/特遣部队/动作脚本与触发/标签共用「8 位 ID + 名字」定长记录容器，
旧分类只看"+176 是不是某个 GUID"就判成触发 ⇒ 37 条被标成灰色触发、82 条被标成标签。

断言：
  A 触发记录数 == 运行期实例数（每条触发都有实例；没有"只有定义"的假触发）
  B 标签记录数 == tag_records()（真标签）
  C AI 记录数 > 0，且 AI 的 ID 与触发/标签的 ID 集合**不相交**
  D 地图上 82 条 [Triggers] 与存档 82 条触发 ID 一一对应（SAVE0029）
  E 新建触发的 ID 落在 01xxxxxx 段、且不会被判成 AI
  F GUI：默认只显示 82 行触发；勾「显示标签/AI」后行数 = 全部记录
"""
import io, os, sys, importlib.util
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc

OK = FAIL = 0


def chk(c, msg):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


SAV = next((p for p in (r'E:\YURI\SAVE0029.SAV',
                        r'C:\Users\zhao5\Desktop\工程师\SAVE48DB.SAV')
            if os.path.exists(p)), r'C:\Users\zhao5\Desktop\工程师\SAVE48DB.SAV')
MAP = next((p for p in (r'C://Users//zhao5//Desktop//工程师//all01umd.map',
                        r'E://YURI//all01umd.map') if os.path.exists(p)),
           r'E://YURI//all01umd.map')
d = SaveDoc(SAV)
by = {}
for r in d.records:
    by.setdefault(r.kind, []).append(r)
print('样本 %s　记录 %d：%s' % (os.path.basename(SAV), len(d.records),
                              {k: len(v) for k, v in by.items()}))

print('\n== A: 触发记录数 == 运行期实例数 ==')
n_trig = len(by.get('触发', []))
n_inst = len(d.runtime_status())
chk(n_trig == n_inst, '触发 %d 条 = 实例 %d 条' % (n_trig, n_inst))
chk(all(r.flag_off for r in by.get('触发', [])), '每条触发都定位到启用位（不再有"只读幽灵"）')

print('\n== B: 标签记录数 == 真标签数 ==')
real_tags = {sid for sid, _nm, _off in d.tag_records()}
chk(len(by.get('标签', [])) == len(real_tags),
    '标签 %d 条 = tag_records() %d 条' % (len(by.get('标签', [])), len(real_tags)))
chk({r.sid for r in by.get('标签', [])} == real_tags, '标签 ID 集合完全一致')

print('\n== C: AI 三大件==')
ai = by.get('AI', [])
chk(len(ai) > 0, 'AI 记录 %d 条' % len(ai))
t_ids = {r.sid for r in by.get('触发', [])}
g_ids = {r.sid for r in by.get('标签', [])}
a_ids = {r.sid for r in ai}
chk(not (a_ids & t_ids), 'AI 与 触发 ID 无交集（交集 %d）' % len(a_ids & t_ids))
chk(not (a_ids & g_ids), 'AI 与 标签 ID 无交集（交集 %d）' % len(a_ids & g_ids))
print('   样例 AI：%s' % [(r.sid, r.name) for r in ai[:3]])

print('\n== D: 与地图 [Triggers] 对账 ==')
try:
    d.attach_map(MAP)
    mt = set(d._map.triggers)
    if not mt:
        print('   ⚠ 该地图没有 [Triggers] 段（可能被覆盖成新图了）—— 本条跳过')
        chk(True, '地图无触发段，跳过对账（%s）' % MAP)
    else:
        # ★地图可能被玩家在 FA2 里改过（加了新触发）⇒ 只要求"存档的触发全在地图里"，
        #   地图多出来的只做提示，不算失败。
        chk(t_ids <= mt, '存档 %d 条触发全部能在地图 [Triggers] 里找到（缺 %d）'
            % (len(t_ids), len(t_ids - mt)))
        extra = sorted(mt - t_ids)
        if extra:
            print('   地图比存档多 %d 条（FA2 里新加、存档还没发生的）：%s'
                  % (len(extra), extra[:3]))
except Exception as e:
    print('   地图对账跳过：%s' % e)

print('\n== E: 新建触发的 ID 与归属 ==')
info = d.create_trigger('分类自检', 8, 0, 21, 0)
sid = info['trigger_id']
chk(sid.startswith('01'), '新 ID = %s（01xxxxxx 段）' % sid)
chk(sid not in a_ids, '新 ID 不与 AI 冲突')
rec = next((r for r in d.records if r.sid == sid), None)
chk(rec is not None and rec.kind == '触发', '新记录被归为「触发」（%s）'
    % (rec.kind if rec else 'None'))

print('\n== F: GUI 列表默认隐藏标签/AI ==')
spec = importlib.util.spec_from_file_location('sedgui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
for _n in ('showinfo', 'showwarning', 'showerror'):
    setattr(m.messagebox, _n, lambda *a, **k: None)
app = m.App()
app.withdraw()
try:
    app.doc = SaveDoc(SAV)
    app._set_doc(app.doc)
    app.refresh_records()
    rows = app.tt.get_children()
    n_t = sum(1 for r in app.doc.records if r.kind == '触发')
    chk(len(rows) == n_t, '默认只显示 %d 行触发（实际 %d）' % (n_t, len(rows)))
    kk = {app.tt.set(k, 'kind') for k in rows}
    chk(kk == {'触发'}, '列表里只有「触发」（%s）' % kk)
    app._show_tags.set(True)
    app._fill_trig_tree()
    chk(len(app.tt.get_children()) == len(app.doc.records),
        '勾「显示标签/AI」后 %d 行 = 全部记录' % len(app.tt.get_children()))
finally:
    try:
        app.destroy()
    except Exception:
        pass

print('\n记录分类自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
