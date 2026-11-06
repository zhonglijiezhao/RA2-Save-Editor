# -*- coding: utf-8 -*-
"""把底层异常翻译成玩家看得懂的话（★2026-11-06 审核 §1.3）。

问题
----
审核时实测三种坏档：

| 输入        | 以前弹给玩家的东西                                              |
|-------------|------------------------------------------------------------------|
| 截断到 60%  | `struct.error: unpack_from requires a buffer of at least 512 bytes` |
| 随机字节    | `ValueError: not a CFB file`                                     |
| 空文件      | `ValueError: not a CFB file`                                     |

（`ui/page_search.py` 直接把 `traceback.format_exc()` 塞进 `messagebox.showerror`）

技术上是"没崩"，但普通玩家看到 `unpack_from` 只会得出一个结论：**这软件坏了**。
而这三条里至少两条是"存档本身坏了 / 选错文件"，本来一句话就能说清。

做法
----
`explain(exc, path, action)` → `(标题, 正文)`：
  · 正文第一行是**人话结论**（存档坏了 / 选错文件 / 没权限 / 磁盘满 / 内存不够）；
  · 第二段给**这个场景该做什么**（换档、从 `_备份存档` 恢复、别在游戏里开着……）；
  · 最后留一行**技术摘要**（异常类型 + 原文），玩家截图报 issue 时我仍然有线索；
  · 完整 traceback 落盘到 `%LOCALAPPDATA%\\RA2SED_logs\\`，正文里给路径。

刻意不做的事：**不吞异常、不改异常类型**。这里只负责"怎么解释"，判定链仍在各自的
解析器里（`cfb.py` 抛 `ValueError('not a CFB file')` 等），所以 `--selftest` 之类
依赖异常类型的代码不受影响。
"""
# ★2026-11-06（审核 §3.3）：静默异常统一记账（绝不抛、默认只记内存）。
#   本文件里原来的 `except ...: pass` 已换成 `_swallow()` —— 行为完全不变，
#   但被吞掉的东西会进 diag 缓冲区，随错误日志/退出时落盘，排障不再靠猜。
try:
    from diag import swallow as _swallow
except Exception:                    # diag 不可达（极端情况）⇒ 退化成原来的 pass
    try:
        from core.diag import swallow as _swallow
    except Exception:
        def _swallow(*a, **k):
            pass
import os
import re
import sys
import errno
import datetime
import traceback

_MAX_SIZE_SHOW = 1024 * 1024 * 1024        # 超过 1GB 用 GB 显示


def _fmt_size(n):
    try:
        n = int(n)
    except Exception:
        return '未知大小'
    if n >= _MAX_SIZE_SHOW:
        return '%.2f GB' % (n / 1024.0 / 1024.0 / 1024.0)
    if n >= 1024 * 1024:
        return '%.2f MB' % (n / 1024.0 / 1024.0)
    if n >= 1024:
        return '%.1f KB' % (n / 1024.0)
    return '%d B' % n


def _file_size(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return None


def _type_name(exc):
    """`struct.error` 的 `__name__` 只是 `error` —— 只用它看不出是谁抛的。"""
    cls = type(exc)
    name = cls.__name__
    mod = getattr(cls, '__module__', '') or ''
    if name in ('error', 'Error') and mod and mod not in ('builtins', '__main__'):
        return '%s.%s' % (mod, name)
    return name


def _short(exc):
    """一行技术摘要：`类型: 原文`（不带路径/换行，够贴 issue 了）。

    `struct.error` 的原文是 `unpack_from requires a buffer of at least 512 bytes
    ... actual buffer size is 0` —— 函数名对玩家毫无意义，但**数字有用**
    （512 = CFB 头长度，0 = 实际读到多少），所以只把措辞翻掉、数字保留。
    """
    if type(exc).__module__ == 'struct':
        m = re.search(r'buffer of at least (\d+) bytes.*?actual buffer size is (\d+)',
                      str(exc))
        if m:
            return ('struct.error: 缓冲区不足（需要 %s 字节，实际 %s 字节）'
                    % (m.group(1), m.group(2)))
    txt = '%s: %s' % (_type_name(exc), exc)
    txt = ' '.join(txt.split())
    return txt if len(txt) <= 220 else txt[:217] + '…'


def _log_dir():
    """日志目录 —— 与明文缓存同级的独立目录，失败就退到临时目录。"""
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    for d in (os.path.join(base, 'RA2SED_logs'),
              os.path.join(os.environ.get('TEMP') or '.', 'RA2SED_logs')):
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except Exception:
            continue
    return None


def write_log(exc, path=None, action=''):
    """把完整 traceback 写盘，返回日志路径（写不了就返回 None）。

    ★不抛异常：日志写失败绝不能再盖掉真正的错误提示。
    """
    d = _log_dir()
    if not d:
        return None
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    fn = os.path.join(d, 'err-%s-%s.log'
                      % (stamp, datetime.datetime.now().strftime('%f')[:3]))
    try:
        with open(fn, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write('时间   : %s\n' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            f.write('动作   : %s\n' % (action or '-'))
            f.write('文件   : %s\n' % (path or '-'))
            try:
                import app_version
                _v = getattr(app_version, 'version_text', None)
                f.write('版本   : %s\n' % (_v() if callable(_v)
                                           else getattr(app_version, 'SEMVER', '?')))
            except Exception:
                _swallow()
            f.write('Python : %s\n' % sys.version.replace('\n', ' '))
            f.write('冻结   : %s\n' % bool(getattr(sys, 'frozen', False)))
            f.write('\n--- traceback ---\n')
            if isinstance(exc, BaseException):
                f.write(''.join(traceback.format_exception(
                    type(exc), exc, exc.__traceback__)))
            else:
                f.write(str(exc))
            # ★2026-11-06（审核 §3.3）：把"这一路上被静默吞掉的东西"附在后面。
            #   玩家报的错往往只是表象（例：保存失败），真正的原因是某个 except pass 盖住的。
            try:
                from diag import summary as _diag_summary
                s = _diag_summary()
                if s:
                    f.write('\n--- 静默异常（曾被 except pass 吞掉）---\n')
                    f.write(s + '\n')
            except Exception:
                _swallow()
        return fn
    except Exception:
        return None


def _truncated_like(exc):
    """`struct.error` 基本只有一种成因：缓冲区比 `unpack_from` 要的短 ⇒ 档被截断。"""
    return (type(exc).__module__ == 'struct'
            and 'unpack' in str(exc))


def explain(exc, path=None, action='打开'):
    """→ `(标题, 正文)`。`action` 只影响"建议"那一段（打开 / 保存 / 别处）。"""
    name = os.path.basename(path) if path else ''
    size = _file_size(path) if path else None
    log = write_log(exc, path, action)

    head = {                      # 第一行结论
        '打开': '这个文件打不开。',
        '保存': '保存没有完成。',
    }.get(action, '操作没有完成。')

    why = ''                      # 第二段：为什么
    todo = ''                     # 第三段：怎么办

    # ---- 1. 文件层面（先看盘上到底有什么）----
    if isinstance(exc, FileNotFoundError):
        head = '找不到文件。'
        why = '路径不存在：\n%s' % (path or (exc.filename or '?'))
        todo = ('可能已被移动/改名/删除，或被同步盘（OneDrive、网盘）挪走了。\n'
                '请回到游戏目录重新找这个存档。')

    elif isinstance(exc, IsADirectoryError):
        head = '这是一个文件夹，不是存档文件。'
        why = '路径：%s' % (path or '?')
        todo = '请选择以 .SAV 结尾的存档文件。'

    elif isinstance(exc, PermissionError):
        head = '没有权限读写这个文件。'
        why = '路径：%s' % (path or '?')
        todo = ('常见原因：① 文件属性是「只读」；② 游戏正开着、存档被它占用；'
                '③ 安全软件（360/火绒/Defender）拦了写入。\n'
                '请关掉游戏后重试；必要时把「%s」加入杀软白名单。'
                % (os.path.dirname(os.path.abspath(path)) if path else '程序目录'))

    elif size == 0:
        head = '这个文件是空的（0 字节）。'
        why = '路径：%s' % (path or '?')
        todo = ('空的存档没有任何可恢复内容。多半是上次写入被中断（断电/强制关机/'
                '杀软拦截）或同步盘留下了占位文件。\n'
                '如果这是一份被清空的存档，可以到程序目录下的「_备份存档」里找带时间戳的 '
                '`.bak-*` 副本恢复。')

    # ---- 2. 解密/解析层面 ----
    elif isinstance(exc, ValueError) and 'CFB' in str(exc).upper():
        head = '这不是一份红警2 / 尤里的复仇存档。'
        if size is not None and size < 512:
            why = ('文件只有 %s，太小了 —— 完整的存档至少几百 KB。\n路径：%s'
                   % (_fmt_size(size), path or '?'))
            todo = '可能下载/复制没完成，或者选错了文件（比如 .map、.ini、快捷方式）。'
        else:
            why = ('文件大小 %s，但开头不是存档容器（OLE2/CFB）的标识。\n路径：%s'
                   % (_fmt_size(size) if size is not None else '未知', path or '?'))
            todo = ('请确认选的是游戏存档（一般在游戏目录或「我的文档」下的存档文件夹里，'
                    '扩展名 .SAV）。\n如果是别的工具压缩/加密过的包，需要先解压。')

    elif _truncated_like(exc):
        head = '存档文件不完整（被截断了）。'
        why = ('读到一半数据就结束了，文件大小 %s。\n路径：%s'
               % (_fmt_size(size) if size is not None else '未知', path or '?'))
        todo = ('常见于：复制/下载中断、U 盘拔得早、云盘只同步了一半、'
                '硬盘坏道。\n'
                '可以试试从程序目录下的「_备份存档」找 `.bak-*` 副本；'
                '如果是从别处拷来的，重新拷一次往往就好了。')

    elif type(exc).__name__ == 'LZOError' or 'LZO' in type(exc).__name__.upper():
        head = '存档里的压缩数据解不开（内容已损坏）。'
        why = '解压第 N 块时报：%s\n路径：%s' % (str(exc), path or '?')
        todo = ('说明文件内容被改坏了（不是本工具写的存档，或者写盘时被打断）。\n'
                '请优先用「_备份存档」里的副本，或换成游戏自己刚存的档。')

    elif isinstance(exc, MemoryError):
        head = '内存不够，读不进来。'
        why = '这份存档解密后需要的内存量超过了当前可用内存。'
        todo = ('关掉一些程序（浏览器标签最占内存）再试；大任务档解密后可达几十 MB，'
                '但 Python 处理时会膨胀数倍。\n32 位系统上尤其容易碰到。')

    elif isinstance(exc, OSError) and getattr(exc, 'errno', None) == errno.ENOSPC:
        head = '磁盘空间不足，写不进去。'
        why = '目标磁盘已满：%s' % (os.path.dirname(os.path.abspath(path)) if path else '?')
        todo = ('清理一些空间（明文缓存在 %%LOCALAPPDATA%%\\RA2SED_cache，可整个删掉），'
                '或另存到别的盘。')

    # ---- 3. 兜底：说人话 + 保留技术摘要 ----
    if not why:
        if action == '保存':
            head = '保存时出错了，**原文件没有被改动**。'
            why = ('保存是"先写临时文件 → 校验 → 再替换"，任何一步失败都不会破坏原档。\n'
                   '出错的环节：%s' % _short(exc))
            todo = ('可以先重试一次；仍失败的话，把下面那份日志发给我。\n'
                    '改动还留在界面上（标题栏有 *），别急着关窗口。')
        else:
            head = '打开时出错了。'
            why = '出错的环节：%s' % _short(exc)
            todo = ('如果这个档在游戏里能正常读，请把下面那份日志发给我，我来查。')

    body = [head, '', why]
    if todo:
        body += ['', todo]
    body += ['', '─' * 34, '技术摘要：%s' % _short(exc)]
    if log:
        body += ['详细日志：%s' % log]
    else:
        body += ['（日志写盘失败，只有上面这行摘要）']
    return ({'打开': '打开失败', '保存': '保存失败'}.get(action, '出错'), '\n'.join(body))


def explain_error(exc, path=None, action='打开'):
    """`explain()` 的别名 —— GUI 里 import 这个名字更顺口。"""
    return explain(exc, path, action)
