# -*- coding: utf-8 -*-
"""自检：静默异常观测器（2026-11-06 审核 §3.3 的回归钉）。

背景：应用代码里 313 处 `except ...: pass` 会让"非写盘路径的失败"完全静默
（规则表加载失败 → 可建造判定悄悄失效；指纹缓存损坏 → 悄悄降级）。
这批 `pass` 全部换成了 `_swallow()`，本自检钉住：

  A. 记账正确：位置/类型/次数/原文/抛出点都对，同类自动合并计数；
  B. **绝不抛**：观测器自己坏掉时（内部异常）必须永久闭嘴，不能把主流程带崩；
  C. 不污染 stdout：headless 报告（自检结果.txt）不能被日志混进去；
  D. 缓冲有上限：超过 400 类只计数不爆内存；
  E. 落盘：dump 写出可读日志（含版本/统计），**空缓冲不写文件**；
  F. 三条取回路径都接上了：错误日志、崩溃日志、headless 报告、GUI 退出；
  G. 全仓 313 处都改到了，且每个模块拿到的是**真** diag（不是降级 no-op 桩）。
"""
import ast
import os
import sys

# ★2026-11-07：Windows 控制台默认 cp1252，一 print 中文就 UnicodeEncodeError。
#   CI 里用 PYTHONUTF8=1 兜着，这里再兜一层 —— 脚本在哪都能跑。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
import glob
import io
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

import diag

OK = FAIL = 0


def chk(c, m):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + m)
    else:
        FAIL += 1
        print('  [FAIL] ' + m)


def boom(kind='ValueError'):
    try:
        if kind == 'ValueError':
            int('不是数字')
        else:
            open(os.path.join(HERE, '_不存在的目录_', 'x'), 'rb').close()
    except Exception:
        diag.swallow()


print('=' * 72)
print('A. 记账')
print('=' * 72)
diag.reset()
for _ in range(3):
    boom()
boom('File')
n, total, over = diag.counts()
chk(n == 2, '两类不同异常 → 2 类（实际 %d）' % n)
chk(total == 4, '总次数 4（实际 %d）' % total)
chk(over == 0, '未溢出')
items = diag.snapshot()
chk(items and items[0]['n'] == 3, '按次数降序，首条 3 次（实际 %s）'
    % (items[0]['n'] if items else None))
chk('ValueError' in [i['type'] for i in items], '异常类型记录正确')
chk(any('int()' in i['msg'] for i in items), '异常原文记录正确')
chk(all(':' in i['site'] for i in items), '位置形如 文件:行号')
chk(any(i['at'] and i['at'] != i['site'] for i in items),
    '抛出点与吞异常的位置分开记（排障时才知道是谁惹的祸）')

diag.note('rules-missing', 'rulesmd.ini 没找到，改用内置表')
n2, _, _ = diag.counts()
chk(n2 == n + 1, 'note() 也能记（降级状态不经过异常）')
chk('rules-missing' in diag.summary(), 'summary 里能看到 note 标记')

s = diag.summary()
chk(s.startswith('静默异常：') and '×' in s, 'summary 有一行标题 + 明细')

print()
print('=' * 72)
print('B. 绝不抛 / C. 不污染 stdout')
print('=' * 72)
diag.reset()
_orig = diag._record


def _bad_record(*a, **k):
    raise RuntimeError('观测器自己坏了')


diag._record = _bad_record
buf = io.StringIO()
_stdout = sys.stdout
sys.stdout = buf
_broken_flag = _summary_after = None
try:
    try:
        int('x')
    except Exception:
        diag.swallow()          # 不能抛出去
    _broken_flag = diag._BROKEN
    try:
        int('y')
    except Exception:
        diag.swallow()          # 闭嘴之后更不能抛
    _summary_after = diag.summary()
finally:
    sys.stdout = _stdout
    diag._record = _orig
chk(_broken_flag is True, '内部出错后置 _BROKEN（永久闭嘴）')
chk(_summary_after == '', '闭嘴后 summary 返回空串（不再尝试记录）')
chk(buf.getvalue() == '', '整个过程 stdout 一个字节都没写（实际 %r）'
    % buf.getvalue()[:60])

diag.reset()
try:
    int('x')
except Exception:
    diag.swallow()
chk(diag._BROKEN is False, 'reset() 之后恢复正常')

print()
print('=' * 72)
print('D. 缓冲上限')
print('=' * 72)
diag.reset()
diag._MAX_KEYS = 5
try:
    # 直接喂 _record：要造"不同位置"的 12 类，用同一个 raise 站点只会合并成 1 类
    for i in range(12):
        diag._record('fake_file_%d.py:1' % i, 'KeyError', 'k%d' % i)
    n, total, over = diag.counts()
    chk(n == 5, '只保留 5 类（实际 %d）' % n)
    chk(total == 12, '总次数仍准确（实际 %d）' % total)
    chk(over == 7, '溢出 7 次被单独计数（实际 %d）' % over)
finally:
    diag._MAX_KEYS = 400
    diag.reset()

print()
print('=' * 72)
print('E. 落盘')
print('=' * 72)
chk(diag.dump() is None, '空缓冲不写文件（不制造无意义的日志）')
boom()
p = diag.dump()
chk(bool(p) and os.path.exists(p), '有内容时写出日志：%s' % (p and os.path.basename(p)))
if p and os.path.exists(p):
    txt = open(p, encoding='utf-8', errors='replace').read()
    chk('吞异常的位置' in txt, '日志有表头')
    chk('版本' in txt and 'Python' in txt, '日志带版本与 Python（报 issue 能对版）')
    chk('统计' in txt, '日志有统计行')

print()
print('=' * 72)
print('F. 三条取回路径')
print('=' * 72)
diag.reset()
boom()
import errormsg
lp = errormsg.write_log(ValueError('占位错误'), os.path.join(HERE, 'x.SAV'), '打开')
if lp and os.path.exists(lp):
    t = open(lp, encoding='utf-8', errors='replace').read()
    chk('静默异常' in t, '错误日志末尾带着静默异常摘要')
else:
    chk(False, '错误日志没写出来')

# headless 报告
import app_main
_tmp_report = os.path.join(tempfile.mkdtemp(), 'report.txt')
_old_report = app_main.REPORT
app_main.REPORT = _tmp_report
try:
    _buf = io.StringIO()
    _so = sys.stdout
    sys.stdout = _buf                # _write_report 会往 stdout 打一份，这里不需要看
    try:
        app_main._write_report('MKTRIG PASS')
    finally:
        sys.stdout = _so
    t = open(_tmp_report, encoding='utf-8-sig', errors='replace').read()
    chk('MKTRIG PASS' in t and '静默异常' in t, 'headless 报告末尾附了摘要')
    diag.reset()
    try:
        app_main._write_report('MKTRIG PASS')
    finally:
        pass
    t = open(_tmp_report, encoding='utf-8-sig', errors='replace').read()
    chk('静默异常' not in t, '没有静默异常时报告保持干净（一个字都不加）')
finally:
    app_main.REPORT = _old_report

# GUI 退出与崩溃日志：静态检查钩子是否接上（不需要真的开窗口）
ps = open(os.path.join(HERE, 'ui', 'page_search.py'), encoding='utf-8').read()
chk("_diag_dump(prefix='session')" in ps, 'GUI 退出时会落一份 session 日志')
main_src = open(os.path.join(HERE, 'RA2存档编辑器.py'), encoding='utf-8').read()
chk('静默异常' in main_src, '崩溃日志（crash.log）会附静默异常摘要')
chk('_diag_install()' in main_src, '启动时挂了后台线程的 unhandled 钩子')

print()
print('=' * 72)
print('G. 全仓接入情况')
print('=' * 72)
files = (sorted(glob.glob(os.path.join(HERE, 'core', '*.py')))
         + sorted(glob.glob(os.path.join(HERE, 'ui', '*.py')))
         + [os.path.join(HERE, 'app_main.py'), os.path.join(HERE, 'RA2存档编辑器.py')])
left = 0
for f in files:
    if os.path.basename(f) == 'diag.py':
        continue
    tree = ast.parse(open(f, encoding='utf-8').read())
    left += sum(1 for x in ast.walk(tree) if isinstance(x, ast.ExceptHandler)
                and len(x.body) == 1 and isinstance(x.body[0], ast.Pass))
chk(left == 0, '应用代码里已无 `except ...: pass`（残留 %d 处）' % left)

# 每个接入的模块必须拿到**真** diag —— 降级成 no-op 桩等于白改
#   ★CI 上的 Python（nuget 包）通常**没有 tkinter** ⇒ ui/ 那批导入不了。
#     这种情况下整段跳过（打印 SKIP 而不是 FAIL），核心模块仍照常检查。
try:
    import tkinter  # noqa: F401
    _HAS_TK = True
except Exception:
    _HAS_TK = False

fallback, real = [], 0
try:
    import importlib
    # `ui_theme` 住在 core/ 但其实是 UI 模块（要 tkinter）：无 tk 的环境排除，有 tk 照查
    mods = [os.path.basename(p)[:-3] for p in glob.glob(os.path.join(HERE, 'core', '*.py'))
            if not p.endswith('diag.py')
            and not (os.path.basename(p) == 'ui_theme.py' and not _HAS_TK)]
    if _HAS_TK:
        mods += ['ui.' + os.path.basename(p)[:-3]
                 for p in glob.glob(os.path.join(HERE, 'ui', '*.py'))]
    mods += ['app_main']
    for m in mods:
        try:
            mod = importlib.import_module(m)
        except Exception as e:
            fallback.append('%s(导入失败:%s)' % (m, str(e)[:40]))
            continue
        fn = getattr(mod, '_swallow', None)
        if fn is None:
            continue
        if getattr(fn, '__module__', '').endswith('diag'):
            real += 1
        else:
            fallback.append(m)
except ImportError as e:
    print('  [SKIP] 模块导入检查（%s）' % e)
chk(not fallback, '全部模块拿到真 diag（%d 个真 / 降级 %s）' % (real, fallback or '无'))
chk(real >= (20 if _HAS_TK else 10),
    '接入模块数合理（%d 个%s）' % (real, '' if _HAS_TK else '，无 tkinter：只查了 core'))

print()
print('-' * 72)
print('自检结果：PASS %d / FAIL %d' % (OK, FAIL))
print('-' * 72)
sys.exit(0 if FAIL == 0 else 1)
