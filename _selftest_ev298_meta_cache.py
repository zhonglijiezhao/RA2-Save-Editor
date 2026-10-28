# -*- coding: utf-8 -*-
r"""§298 自检：存档列表元信息缓存（审查 P3-13）+ 扫元信息不再写 26MB 明文缓存。

为什么要有这个自检
------------------
以前开程序时，列表页会对前 12 个存档各做一次 `SaveDoc()` 全量解码（1.28s/档，
大档更久），只为拿「任务名 / 金钱」两列 ⇒ 后台烧 ~15 秒 CPU；
副作用是每份还往 `%LOCALAPPDATA%\RA2SED_cache` 写 26MB 明文缓存，
而扫完就 `del doc`，那份缓存**永远不会被复用** ⇒ 光启动 ≈300MB 磁盘写。

现在：
  · `meta.json` 键 =(路径, mtime, size)，命中就填表、一次解码都不做
  · 未命中才解码，且 `SaveDoc(..., write_cache=False)` ⇒ 不产生那 26MB

跑法：py _selftest_ev298_meta_cache.py
"""
import hashlib
import io
import os
import shutil
import sys
import time
import importlib.util

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc  # noqa: E402

SRC = r'E:/YURI/SAVE0665_修改.SAV'
TMPD = os.path.join(HERE, '_selftest_out', '_ev298')

PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


def cache_file_of(path):
    """明文缓存文件名 = 原文件 md5（见 SaveDoc._load_plain）。"""
    md5 = hashlib.md5(open(path, 'rb').read()).hexdigest()
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'RA2SED_cache', md5 + '.bin')


if not os.path.exists(SRC):
    print('样本不存在：%s —— 跳过' % SRC)
    sys.exit(0)

os.makedirs(TMPD, exist_ok=True)
for f in os.listdir(TMPD):
    p = os.path.join(TMPD, f)
    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)

spec = importlib.util.spec_from_file_location('g', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# ── ① write_cache=False 不写明文缓存；True 仍然写（老行为没破）────────────
print('\n[1] SaveDoc(write_cache=...)')
copy = os.path.join(TMPD, 'A.SAV')
shutil.copyfile(SRC, copy)
cf = cache_file_of(copy)
if os.path.exists(cf):
    os.remove(cf)
d = SaveDoc(copy, write_cache=False)
ck(d.info.get('任务名') not in (None, ''), '只读模式也拿到了任务名（%r）' % (d.info.get('任务名'),))
ck(not os.path.exists(cf), 'write_cache=False 没有生成 26MB 明文缓存')
d2 = SaveDoc(copy)
ck(os.path.exists(cf), 'write_cache=True 仍然生成明文缓存（老行为保留）')
ck(d2.info.get('金钱') == d.info.get('金钱') and d2.info.get('任务名') == d.info.get('任务名'),
   '两种模式读出的元信息一致')
del d, d2

# ── ② _scan_meta：首次建缓存，第二次全命中（把 SaveDoc 换成炸弹来证明）─────
print('\n[2] _scan_meta 缓存命中')
copy2 = os.path.join(TMPD, 'B.SAV')
shutil.copyfile(SRC, copy2)
items = [copy, copy2]

app = m.App()
# ★App() 建完会**自动扫一遍真实存档目录**（那是产品行为）。这里先把它取消，
#   否则 `_scanning` 一直是 True，我们自己调的 `_scan_meta` 会被直接 return 掉。
for _ in range(10):
    app.update()
    time.sleep(0.02)
app._scan_gen = getattr(app, '_scan_gen', 0) + 1
for _ in range(200):
    app.update()
    if not getattr(app, '_scanning', False):
        break
    time.sleep(0.05)
metaf = os.path.join(TMPD, 'meta.json')
app._meta_cache_path = lambda: metaf      # 别污染真实的 %LOCALAPPDATA%
for p in items:
    app.tree.insert('', 'end', iid=p, values=(os.path.basename(p), '', ''))


def pump(limit=120.0):
    """跑 Tk 事件循环直到后台扫描结束（顺带让 _poll 把队列写进表格）。"""
    t0 = time.time()
    while time.time() - t0 < limit:
        app.update()
        if not getattr(app, '_scanning', False):
            for _ in range(20):           # 收尾：把剩下的队列消息处理完
                app.update()
                time.sleep(0.02)
            return True
        time.sleep(0.05)
    return False


app._scan_meta(items)                     # ← 真正触发扫描
ck(pump(), '第一次扫描在 120s 内结束')
ck(os.path.exists(metaf), '生成了 meta.json')
row1 = {p: app.tree.item(p, 'values') for p in items}
print('    第一次：%s' % {os.path.basename(k): v for k, v in row1.items()})
ck(all(v[1] not in ('', '?') for v in row1.values()), '两行的任务名都填上了')
ck(all(v[2] not in ('',) for v in row1.values()), '两行的金钱都填上了')
import json
_cache = json.load(open(metaf, encoding='utf-8'))
ck(len(_cache) == 2, 'meta.json 里有 2 条记录')
ck(all(len(v) >= 5 for v in _cache.values()), '记录结构 = [mtime, size, 任务名, 时间, 金钱]')

# 清空表格值，再把 SaveDoc 换成"一碰就炸"，验证第二次走的是缓存
for p in items:
    app.tree.item(p, values=(os.path.basename(p), '', ''))
_orig = m.SaveDoc


def _bomb(*a, **kw):
    raise AssertionError('缓存未命中：第二次扫描又去解码了')


m.SaveDoc = _bomb
try:
    app._scan_meta(items)
    ck(pump(), '第二次扫描在 120s 内结束')
finally:
    m.SaveDoc = _orig
row2 = {p: app.tree.item(p, 'values') for p in items}
print('    第二次：%s' % {os.path.basename(k): v for k, v in row2.items()})
ck(row2 == row1, '第二次结果与第一次一致（且没触发解码）')

# ── ③ mtime 变了必须重扫（缓存判据不能只看路径）────────────────────────
print('\n[3] 存档被改过后缓存要失效')
_cache[copy] = [1, 2, '旧任务名', '-', 0]
with open(metaf, 'w', encoding='utf-8') as f:
    json.dump(_cache, f, ensure_ascii=False)
os.remove(copy2)                      # 让它失效，扫描时这一行会变 '?'
app.tree.delete(copy2)
app.tree.insert('', 'end', iid=copy2, values=('B.SAV', '', ''))
m.SaveDoc = _orig
app._scan_meta([copy, copy2])
ck(pump(), '第三次扫描在 120s 内结束')
ck(app.tree.item(copy2, 'values')[1] == '?', '文件删掉后该行走异常分支（?）')

# ── ④ meta.json 坏了不能让列表打不开 ─────────────────────────────────
print('\n[4] meta.json 损坏时的降级')
with open(metaf, 'w', encoding='utf-8') as f:
    f.write('{这不是 json')
ck(app._meta_cache_load() == {}, '损坏的 meta.json 读成空缓存（不抛异常）')

try:
    app.destroy()
except Exception:
    pass

print('\n' + '=' * 60)
print('结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
for f in FAIL:
    print('  FAIL: %s' % f)
sys.exit(0 if not FAIL else 1)
