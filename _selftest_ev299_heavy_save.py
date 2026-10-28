# -*- coding: utf-8 -*-
"""§299 自检：大存档（5250 条记录）打开不再卡死 + 标签配对/持久性缓存。

玩家 2026-10-28 报「这个存档打开就卡死」（`SAVE5DC8.SAV`，5MB / 明文 25.9MB /
5250 条记录 / 1930 条触发）。用 faulthandler 一抓就看见卡在：

    refresh_records → trigger_repeat_type → trigger_tag_pairs → _tag_pairs_by_name

三处都是"每条触发重算一遍全量"：

  ① `_tag_pairs_by_name`：对每个标签，遍历**全部触发名**做 startswith
     ⇒ O(标签 × 触发名)。改成从尾部找切点（等价，但只跟名字长度有关）
  ② `trigger_tag_pairs` / `tag_persist`：没有 memo，被 2000+ 次调用反复重算
  ③ `action_chain` / `event_chain` / `trigger_objects`：每次调用都重建
     `{key: 行}` 字典（400+ 条行为 × 2000+ 次 = 4800 万次 get）

跑法：py _selftest_ev299_heavy_save.py
"""
import io
import os
import sys
import time
import importlib.util

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc, TAG_PERSIST_OFF  # noqa: E402

# 玩家报的这个档；不在也不影响其余断言（只在有它的时候跑"开档耗时"）
HEAVY = r'C:/Users/zhao5/Desktop/SAVE5DC8.SAV'
NORMAL = r'E:/YURI/SAVE0665_修改.SAV'
BUDGET = 25.0          # 开档预算（秒）—— 以前是"永远打不开"，这里给足余量

PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


print('=' * 68)
print('§299 大存档开档 / 标签缓存')

# ── ① 配对 memo：第二次必须是同一个对象（证明没重算）────────────────────
print('\n[1] trigger_tag_pairs 有 memo')
src = NORMAL if os.path.exists(NORMAL) else HEAVY
if not os.path.exists(src):
    print('  两个样本都不在，跳过')
    sys.exit(0)

doc = SaveDoc(src)
t = time.time()
p1 = doc.trigger_tag_pairs()
t1 = time.time() - t
t = time.time()
p2 = doc.trigger_tag_pairs()
t2 = time.time() - t
print('    首次 %.0f ms / 第二次 %.2f ms（%d 对）' % (t1 * 1000, t2 * 1000, len(p1)))
ck(p1 is p2, '第二次调用直接返回缓存对象（不是重算）')
ck(t2 < 0.01, '第二次调用 < 10ms（实际 %.2f ms）' % (t2 * 1000))
doc.invalidate_caches()
p3 = doc.trigger_tag_pairs()
ck(p3 is not p1, 'invalidate_caches() 之后会重算（不是永久缓存）')
ck([tuple(x) for x in p3] == [tuple(x) for x in p1], '重算结果与缓存一致')

# ── ② tag_persist memo 与逐条扫描结果一致 ────────────────────────────────
print('\n[2] tag_persist 的 memo 结果正确')
tags = doc.tag_records()[:40]
bad = []
for sid, _nm, off in tags:
    v = doc.tag_persist(sid)
    raw = int.from_bytes(doc.plain[off + TAG_PERSIST_OFF:
                                   off + TAG_PERSIST_OFF + 4], 'little')
    want = str(raw) if 0 <= raw <= 3 else None
    if v != want:
        bad.append((sid, v, want))
ck(not bad, '抽查 %d 个标签，memo 值与直接读明文一致%s'
   % (len(tags), ('（不一致：%s）' % bad[:3]) if bad else ''))
ck(doc.tag_persist('不存在的标签') is None, '不存在的标签返回 None')

# ── ③ 行为/事件键表是缓存的，但内容与重建一致 ────────────────────────────
print('\n[3] _actions_by_key / _events_by_key 缓存')
a1 = doc._actions_by_key()
a2 = doc._actions_by_key()
ck(a1 is a2, '行为键表第二次拿到同一个 dict')
ck(len(a1) == len({a.get('key') for a in doc.actions() if a.get('key')}),
   '行为键表条目数与 actions() 一致（%d）' % len(a1))
e1 = doc._events_by_key()
evs = doc.events()
ck(all(e1.get(e['key']) is e for e in evs if e.get('key')),
   '事件键表里的对象就是 events() 里那些')

# ── ④ 大档真的能打开（GUI 全流程），且列表填满 ───────────────────────────
print('\n[4] 玩家报的那份大存档：GUI 能打开')
if not os.path.exists(HEAVY):
    print('    样本不在，跳过（%s）' % HEAVY)
else:
    spec = importlib.util.spec_from_file_location('g', os.path.join(HERE, 'RA2存档编辑器.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    app = m.App()
    for _ in range(20):
        app.update()
    t0 = time.time()
    app.load_doc(HEAVY)
    while time.time() - t0 < 120 and getattr(app, '_pending_load', False):
        app.update()
    for _ in range(40):
        app.update()
        time.sleep(0.02)
    dt = time.time() - t0
    n = len(app.doc.records) if app.doc else 0
    rows = len(app.te.get_children())
    print('    %.1fs 打开；记录 %d 条；触发行 %d' % (dt, n, rows))
    ck(n > 4000, '记录数 > 4000（实际 %d）' % n)
    ck(rows > 1000, '触发列表不是空的（%d 行）' % rows)
    ck(dt < BUDGET, '开档在 %.0fs 预算内（实际 %.1fs）' % (BUDGET, dt))
    try:
        app.destroy()
    except Exception:
        pass

print('\n' + '=' * 68)
print('结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
for f in FAIL:
    print('  FAIL: %s' % f)
sys.exit(0 if not FAIL else 1)
