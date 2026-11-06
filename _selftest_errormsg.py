# -*- coding: utf-8 -*-
"""自检：**坏档提示是不是人话**（2026-11-06 审核 §1.3 的回归钉）。

背景：以前 `ui/page_search.py` 把整段 traceback 直接丢给 messagebox，
玩家打开一个截断档会看到 `struct.error: unpack_from requires a buffer of
at least 512 bytes` —— 技术上没错，但等于"这软件坏了"。

现在 `core/errormsg.py` 负责翻译，本自检钉住：
  A. 五种真实坏档（空文件 / 随机字节 / 截断 / 不存在 / 权限）
     都给出**中文结论 + 该做什么**，且提示里**不出现** `Traceback` / `unpack_from`；
  B. 正文里保留一行技术摘要 + 日志路径 —— 玩家截图报 issue 时我仍有线索；
  C. 日志真的落盘，且含完整 traceback 与版本号；
  D. 保存失败的提示明确说"**原文件没有被改动**"（§2.1/§2.2 修完之后这句才是真的）；
  E. `ui/page_search.py` 的两处错误上报都走 `_explain_error`，没有回退成 traceback。

全部在临时目录里跑，不碰样本（跑完自删）。
"""
import os
import sys

# ★2026-11-07：Windows 控制台默认 cp1252，一 print 中文就 UnicodeEncodeError。
#   CI 里用 PYTHONUTF8=1 兜着，这里再兜一层 —— 脚本在哪都能跑。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
import ast
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

import errormsg
from errormsg import explain, write_log
from editor_core import SaveDoc
from lzo1x import LZOError

OK = FAIL = 0
_TMP = tempfile.mkdtemp(prefix='_errmsg_')


def chk(c, m):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + m)
    else:
        FAIL += 1
        print('  [FAIL] ' + m)


def has_no_traceback(body):
    """玩家视野里不该出现的东西：traceback 头和 Python 内部函数名。"""
    bad = ('Traceback (most recent call last)', 'unpack_from', 'File "')
    return not any(b in body for b in bad)


def ask(exc, path, action='打开'):
    t, body = explain(exc, path, action)
    return t, body


print('=' * 72)
print('A. 真实坏档 → 人话')
print('=' * 72)

# A1 空文件（真实触发：SaveDoc 走 CFB 解析）
p_empty = os.path.join(_TMP, 'empty.SAV')
open(p_empty, 'wb').close()
try:
    SaveDoc(p_empty)
    chk(False, '空文件应当抛异常')
except Exception as e:
    t, body = ask(e, p_empty)
    chk('空' in body, '空文件：结论提到"空"（%s）' % t)
    chk('_备份存档' in body or '备份存档' in body, '空文件：告诉玩家去哪找备份')
    chk(has_no_traceback(body), '空文件：提示里没有 traceback/unpack_from')

# A2 随机字节（不是 CFB）
p_rand = os.path.join(_TMP, 'rand.SAV')
with open(p_rand, 'wb') as f:
    f.write(os.urandom(4096))
try:
    SaveDoc(p_rand)
    chk(False, '随机字节应当抛异常')
except Exception as e:
    t, body = ask(e, p_rand)
    chk('红警' in body or '存档' in body, '随机字节：结论说"不是存档"')
    chk('.SAV' in body, '随机字节：告诉玩家该选什么文件')
    chk(has_no_traceback(body), '随机字节：提示里没有 traceback')

# A3 截断（拿一份能解的档切 60%；样本不在就跳过，不判 FAIL）
sample = None
for cand in ('_tmp_ev252.SAV', os.path.join('_exp', 'M0_基准_SAVE18BE.SAV')):
    p = os.path.join(HERE, cand)
    if os.path.exists(p):
        sample = p
        break
if sample is None:
    print('  [SKIP] 截断用例（找不到样本档）')
else:
    raw = open(sample, 'rb').read()
    p_tr = os.path.join(_TMP, 'trunc.SAV')
    with open(p_tr, 'wb') as f:
        f.write(raw[:int(len(raw) * 0.6)])
    try:
        SaveDoc(p_tr)
        chk(False, '截断档应当抛异常')
    except Exception as e:
        t, body = ask(e, p_tr)
        chk('不完整' in body or '截断' in body, '截断：结论说"不完整"（%s）' % t)
        chk(has_no_traceback(body), '截断：提示里没有 traceback/unpack_from')
        chk('struct.error' in body, '截断：技术摘要仍带 struct.error（可追溯）')

# A4 文件不存在
p_none = os.path.join(_TMP, 'nope.SAV')
try:
    SaveDoc(p_none)
    chk(False, '不存在的文件应当抛异常')
except Exception as e:
    t, body = ask(e, p_none)
    chk('找不到' in body, '不存在：结论说"找不到"')
    chk(has_no_traceback(body), '不存在：提示里没有 traceback')

# A5 权限（构造 PermissionError —— Windows 上 chmod 不可靠，直接造异常）
t, body = ask(PermissionError(13, 'Permission denied'),
              os.path.join(_TMP, 'ro.SAV'))
chk('权限' in body, '权限：结论说"没有权限"')
chk('游戏' in body, '权限：提示常见原因（游戏占着/只读）')

print()
print('=' * 72)
print('B/C. 保留技术摘要 + 日志落盘')
print('=' * 72)
t, body = ask(ValueError('not a CFB file'), p_rand)
chk('技术摘要' in body, '正文含"技术摘要"行')
chk('not a CFB file' in body, '技术摘要保留了原始异常文本')
chk('详细日志' in body, '正文给了日志路径')

logp = write_log(ValueError('自检占位'), p_rand, '测试')
chk(bool(logp) and os.path.exists(logp), 'write_log 真的写出了文件')
if logp and os.path.exists(logp):
    txt = open(logp, encoding='utf-8', errors='replace').read()
    chk('traceback' in txt, '日志含 traceback 段')
    chk('版本' in txt, '日志含版本号（报 issue 时能对版）')
    chk(p_rand in txt, '日志含出问题的文件路径')

print()
print('=' * 72)
print('D. 保存失败的措辞')
print('=' * 72)
t, body = ask(OSError(5, 'I/O error'), os.path.join(_TMP, 'x.SAV'), '保存')
chk('原文件没有被改动' in body, '保存失败：明确说"原文件没有被改动"')
chk(has_no_traceback(body), '保存失败：提示里没有 traceback')

t, body = ask(OSError(28, 'No space left on device'),
              os.path.join(_TMP, 'x.SAV'), '保存')
chk('磁盘' in body or '空间' in body, '磁盘满：认出来了（ENOSPC）')

print()
print('=' * 72)
print('E. GUI 接得上（静态检查，不需要 tkinter）')
print('=' * 72)
ps = os.path.join(HERE, 'ui', 'page_search.py')
src = open(ps, encoding='utf-8').read()
tree = ast.parse(src)
calls = [n.lineno for n in ast.walk(tree)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
         and n.func.id == '_explain_error']
chk(len(calls) >= 2, '两处错误上报都走 _explain_error（行号 %s）' % calls)
chk('from errormsg import explain as _explain_error' in src,
    'page_search 导入了 errormsg')
chk('traceback.format_exc()' not in src,
    '出错分支里不再有 traceback.format_exc()（一个都不剩）')
chk('import traceback' not in src,
    '连 traceback 的 import 也清掉了（不再需要）')
chk('isinstance(payload, tuple)' in src,
    '_poll 兼容新旧两种 err 负载（元组/字符串）')

try:
    shutil.rmtree(_TMP, ignore_errors=True)
except Exception:
    pass

print()
print('-' * 72)
print('自检结果：PASS %d / FAIL %d' % (OK, FAIL))
print('-' * 72)
sys.exit(0 if FAIL == 0 else 1)
