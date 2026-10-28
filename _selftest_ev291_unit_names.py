# -*- coding: utf-8 -*-
"""§291 自检：单位下拉必须是「ID（显示名）」而不是裸 ID。

为什么单独立一套（2026-10-28 代码审查发现）：
  `RA2存档编辑器.py` 里曾经有**两个** `_unit_type_names` —— 4874 行的正式版
  （返回 `ADOG（警犬）`、按 rules 建造栏排序）被 7473 行一个 5 行的裸 ID 版覆盖，
  于是地图页全部单位下拉退化成 `ADOG`，中文名和排序都丢了。
  这是提交 62a4d23a 为迁就 `_dlg_tf.add()` 没做 `split('（')` 而加的补丁。

本自检卡三件事：
  ① 类里只有一个 `_unit_type_names`（防止再被覆盖）；
  ② 返回值里**含括号显示名**，且括号前那截必须是合法型号（能被解析回类型键）；
  ③ `_dlg_tf.add()` 那种"直接 .upper() 当型号"的写法不会再吃掉显示名。

跑法：python _selftest_ev291_unit_names.py
"""
import io
import os
import sys
import importlib.util

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

SRC = r'E:/YURI/SAVE0665_修改.SAV'

spec = importlib.util.spec_from_file_location('g', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PASS, FAIL = [], []

def ui_source():
    """主程序 + ui/ 下所有页面的源码（★P2-8 拆页后，文本断言必须扫全部）。

    以前只读 `RA2存档编辑器.py`；页签方法搬到 `ui/page_*.py` 之后，
    只读主文件就什么都找不到了（会静默地"断言失败"，而不是报错）。
    """
    root = os.path.dirname(os.path.abspath(__file__))
    parts = []
    p = os.path.join(root, 'RA2存档编辑器.py')
    if os.path.exists(p):
        parts.append(io.open(p, encoding='utf-8').read())
    udir = os.path.join(root, 'ui')
    if os.path.isdir(udir):
        for n in sorted(os.listdir(udir)):
            if n.endswith('.py'):
                parts.append(io.open(os.path.join(udir, n), encoding='utf-8').read())
    return '\n'.join(parts)



def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


if not os.path.exists(SRC):
    print('样本不存在：%s —— 跳过' % SRC)
    sys.exit(0)

# ── ① 只应有一个 _unit_type_names ──────────────────────────────
# 用源码文本判断（importlib 动态加载的模块 inspect.getsource 拿不到）
_src = ui_source()
n_def = _src.count('def _unit_type_names')
ck(n_def == 1, 'App 里 `_unit_type_names` 只定义 1 次（实际 %d 次）' % n_def)

# 取 _dlg_tf 的方法体（到下一个同级 def 为止）
_i = _src.index('    def _dlg_tf(')
_j = _src.index('\n    def ', _i + 10)
_tf_src = _src[_i:_j]
ck("split('（')" in _tf_src, "_dlg_tf 里对下拉值做了 split('（') 再当型号")
ck("v_ty.get() or '').strip().upper()" not in _tf_src,
   '_dlg_tf 里没有残留"直接 .upper() 当型号"的写法')

app = m.App()
app.geometry('1280x820')
app.update()
app.load_doc(SRC)

# 等后台解码完成（_poll 是 120ms 一跳）
import time
t0 = time.time()
while app.doc is None and time.time() - t0 < 90:
    app.update()
    time.sleep(0.02)
ck(app.doc is not None, '存档已载入')
if app.doc is None:
    print('载入失败，后续跳过')
    app.destroy()
    sys.exit(1)

# 等分步刷新走完（地图页/类型下拉依赖 records 与地图）
for _ in range(400):
    app.update()
    if not getattr(app, '_st_steps', None):
        break
    time.sleep(0.01)

# ── ② 下拉值格式 ───────────────────────────────────────────────
vals = app._unit_type_names()
print('  下拉项数：%d，前 6 项：%s' % (len(vals), vals[:6]))
ck(len(vals) > 0, '单位下拉非空（%d 项）' % len(vals))

with_name = [v for v in vals if '（' in v]
ck(len(with_name) > 0,
   '至少一部分项带中文显示名（%d/%d 项带括号）' % (len(with_name), len(vals)))
ck(len(with_name) >= len(vals) * 0.5,
   '带显示名的比例 >= 50%%（实际 %.0f%%）' % (100.0 * len(with_name) / max(1, len(vals))))

keys = set((app.doc.unit_type_index() or {}).keys())
bad = [v for v in vals if v.split('（')[0].strip().upper() not in keys]
ck(not bad, '每项括号前都是合法型号键（异常 %d 个：%s）' % (len(bad), bad[:5]))

# 裸 ID 版的特征：全部项都不含 '（'
ck(not all('（' not in v for v in vals),
   '不是"全裸 ID"的退化版本')

# ── ③ 反例：确认 add() 那种解析方式在富格式下确实需要 split ─────────
sample = next((v for v in vals if '（' in v), None)
if sample:
    naive = sample.strip().upper()
    fixed = sample.split('（')[0].strip().upper()
    ck(naive not in keys, '反例成立：直接 .upper() 得到 %r 不是合法型号' % naive)
    ck(fixed in keys, 'split 之后得到合法型号 %r' % fixed)

app.destroy()

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败项：')
    for f in FAIL:
        print('  - ' + f)
sys.exit(1 if FAIL else 0)
