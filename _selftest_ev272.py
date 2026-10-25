# -*- coding: utf-8 -*-
"""§272 自检：大档打开时的**读条 / 不假死**，以及地图页延迟解析。

玩家 2026-10-25 17:26：
> 「打开大的存档时 打开慢 可以加读条 不然还以为卡死了」

实测（`SAVE4823.SAV`：文件 3.4MB / 明文 26MB / 1448 单位 / 1113 建筑 / 495 条记录）：
  · 原来一次刷完 **17.4 秒**，其中 地图页复位 9.3s（`map_match` 0.7 + **`parse_map` 10.3**）、
    触发表 5.1s、AI 页 2.5s。这 17 秒 Tk 主线程被占死，进度条也不动 ⇒ 看着像卡死。
  · 现在：① `_set_doc` 拆成 6 步，每步之间 `update()` 并刷「第 x/6 步、已用 n 秒」；
          ② 地图页**延迟解析**（切到那一页才 `parse_map`）；③ `parse_map` 结果按
            (路径,大小,mtime) 缓存，第二次开同一张图秒出。

覆盖：
1. `_doc_steps()` 是 6 步；
2. 同步 `_set_doc` 在懒渲染下 < 12 秒（原来 17.4）；
3. 异步版真的分 6 步走完，状态栏依次出现 "1/6" … "6/6"，进度条最终复位；
4. 开档后 `mv_deferred=True`、地图页**还没解析**；切到第 7 页后 `mv_deferred=False` 且画布有图元；
5. `parse_map` 缓存命中（第二次 < 0.6s）且**返回浅拷贝**（改 size 不污染缓存）。
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
spec = importlib.util.spec_from_file_location('sedgui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from editor_core import SaveDoc                                        # noqa: E402
from _paths import find_sav                                            # noqa: E402
import map_render as mr                                                # noqa: E402

SAV = find_sav('SAVE4823.SAV')
MAP = r'E:\YURI\ALL01UMD.MAP'
OK = FAIL = 0


def chk(cond, msg, extra=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s   %s' % (msg, extra))


print('存档 %s（%.1f MB）' % (SAV, os.path.getsize(SAV) / 1048576.0))
app = m.App()
app.withdraw()

# ---------- 1) 步骤表 ----------
print('\n[1] 步骤表')
steps = app._doc_steps()
print('    %d 步：%s' % (len(steps), ' → '.join(n for n, _f in steps)))
chk(len(steps) == 6, '切成 6 步', len(steps))
chk(all(callable(f) for _n, f in steps), '每步都是可调用对象')

# ---------- 2) 同步版耗时 ----------
print('\n[2] 同步 _set_doc（懒渲染）')
doc = SaveDoc(SAV)
t = time.time()
app._set_doc(doc)
dt = time.time() - t
print('    耗时 %.2fs（原来 17.4s）  mv_deferred=%s' % (dt, getattr(app, 'mv_deferred', None)))
chk(dt < 12.0, '★开档耗时 < 12 秒（%.2fs）' % dt, dt)
chk(getattr(app, 'mv_deferred', False) is True, '★地图页被标记为「延迟解析」', getattr(app, 'mv_deferred', None))
chk(app.mv_state.get('mp') is None, '此时地图还没解析（mv_state["mp"] 为空）', app.mv_state.get('mp'))

# ---------- 3) 异步版 6 步 ----------
print('\n[3] 异步版读条（真实走 after 队列）')
app2 = m.App()
app2.withdraw()
seen = []
_orig_status = app2.status.set


def _cap(txt, *a, **k):
    seen.append(txt)
    return _orig_status(txt, *a, **k)


app2.status.set = _cap
doc2 = SaveDoc(SAV)
t = time.time()
app2._set_doc_async(doc2)
_guard = 0
while getattr(app2, '_st_steps', None) and _guard < 4000:
    app2.update()
    _guard += 1
    time.sleep(0.001)
dt2 = time.time() - t
print('    异步走完用时 %.2fs，状态栏消息 %d 条' % (dt2, len(seen)))
hits = [s for s in seen if '正在载入' in s]
print('    进度消息样例：%s' % (hits[:2] + hits[-1:] if hits else seen[:3]))
chk(len(hits) >= 6, '★6 步都报了进度（%d 条）' % len(hits), len(hits))
chk(any('1/6' in s for s in hits), '出现「1/6」', hits[:1])
chk(any('6/6' in s for s in hits), '出现「6/6」', hits[-1:])
chk(any('已用' in s for s in hits), '★进度里带「已用 n 秒」（让人知道在动）', hits[:1])
chk(any('总耗时' in s for s in seen), '结束时给出总耗时', seen[-1:])

# ---------- 4) 切页补解析 ----------
print('\n[4] 切到地图页才解析')
chk(getattr(app, 'mv_deferred', False) is True, '切页前仍是「待解析」')
t = time.time()
app.nb.select(6)
app.update()
dt3 = time.time() - t
n_items = len(app.mv_canvas.find_all())
print('    切页耗时 %.2fs，画布图元 %d' % (dt3, n_items))
chk(dt3 < 3.0, '★切页（含地图解析+渲染）< 3 秒（%.2fs）' % dt3, dt3)
chk(getattr(app, 'mv_deferred', False) is False, '★切页后标记清掉')
chk(n_items > 0, '★画布真的画出来了（%d 个图元）' % n_items, n_items)
chk(app.mv_state.get('mp') is not None, '地图对象已就绪')

# ---------- 5) parse_map 缓存 ----------
print('\n[5] parse_map 缓存')
mr._PARSE_CACHE.clear()
t = time.time()
a = mr.parse_map(MAP)
dt_a = time.time() - t
t = time.time()
b = mr.parse_map(MAP)
dt_b = time.time() - t
print('    第一次 %.2fs / 第二次 %.3fs' % (dt_a, dt_b))
chk(dt_a < 1.0, '★首次解析已提速（%.3fs，原来是 8.8 秒）' % dt_a, dt_a)
chk(dt_b < 0.6, '★第二次命中缓存（%.3fs）' % dt_b, dt_b)
chk(a is not b, '两次返回的不是同一个对象（浅拷贝）', (id(a), id(b)))
b['size'] = (1, 1)
c = mr.parse_map(MAP)
chk(c['size'] != (1, 1), '★改返回值的 size 不会污染缓存（仍是 %s）' % (c['size'],), c['size'])

# ---------- 6) 快速解码 == 技能脚本（逐条） ----------
print('\n[6] C 后端解码与技能脚本逐条对比')
raw = open(MAP, 'rb').read()
t = time.time()
tiles_fast, info_fast = mr._decode_isomap_fast(raw)
dt_fast = time.time() - t
print('    快速版 %.4fs / %s' % (dt_fast, info_fast))
slow = None
try:
    from ra2_pack import decode_isomap
    t = time.time()
    slow, info_slow = decode_isomap(raw)
    print('    技能脚本 %.2fs / %s' % (time.time() - t, info_slow))
except Exception as e:
    print('    技能脚本不可用：%s' % e)
chk(dt_fast < 0.5, '★快速解码 < 0.5 秒（%.4fs）' % dt_fast, dt_fast)
if slow is not None:
    chk(tiles_fast == slow, '★与技能脚本解出的 %d 格**逐条完全一致**' % len(slow),
        'len %d vs %d' % (len(tiles_fast or []), len(slow)))

print('-' * 72)
print('§272 读条 / 懒解析：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
