# -*- coding: utf-8 -*-
"""静默异常的**最小可观测性**（★2026-11-06 审核 §3.3）。

为什么要有
----------
审核统计：应用代码里 **1277 处 `except`，其中 315 处 handler 里只有一个 `pass`**。
这些 `pass` 让"非写盘路径的失败"完全静默：

- rulesmd / artmd 加载失败 → 可建造判定静默失效，界面看起来一切正常；
- 类型指纹缓存损坏 → 悄悄降级成"低置信"，玩家不知道；
- UI 刷新异常 → 某几行不更新，玩家以为数据本来就长这样。

但**不能**把它们改成弹窗或写盘的日志：315 处里绝大多数是"真的无所谓"
（取不到就跳过），一弹窗就没法用了，每条都写盘又会把玩家磁盘刷满。

做法
----
`swallow()` 替换那些 `pass`，做三件事：
1. **绝不抛异常、绝不改行为** —— 整个函数体包在 `except BaseException` 里，
   一旦自身出错就置 `_BROKEN=True` 永久闭嘴（绝不因为"记日志"把主流程搞挂）；
2. **只记内存**：按 `(文件:行号, 异常类型)` 去重计数，最多 400 类，
   记录首末时间、次数、异常原文、真正的抛出点；
3. **不写 stdout** —— headless 报告（`自检结果.txt`）不能被日志污染。

什么时候落盘
------------
- `errormsg.write_log()` 会把 `summary()` 附在错误日志末尾 ⇒ 玩家报任何一次错，
  我拿到的日志里就带着"这一路上被静默吞掉的东西"；
- 程序正常退出时 GUI 会调一次 `dump()`（见 `App.destroy` 的钩子），
  文件在 `%LOCALAPPDATA%\\RA2SED_logs\\diag-<时间戳>.log`；
- 命令行：`--diag` 直接打印/落盘缓冲区（排查 headless 批处理用）。

开发期想看实时输出：设环境变量 `RA2SED_DIAG=1`，每次吞异常会往 stderr 打一行。

刻意不做的
----------
- 不自动"上报"（没有联网代码，这是本项目的铁律）；
- 不改变异常类型、不 re-raise、不动 `sys.excepthook` 的既有链路（只挂一层转交）。
"""
import os
import sys
import time
import threading
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAX_KEYS = 400
_MAX_MSG = 240
_BROKEN = False
_LOCK = threading.RLock()
_ITEMS = {}          # key -> dict
_TOTAL = 0           # 吞掉的异常总数（含被 MAX_KEYS 挤掉的）
_OVERFLOW = 0        # 因超出 MAX_KEYS 而无法单独记的条数
_START = time.time()
_VERBOSE = bool(os.environ.get('RA2SED_DIAG'))


def _rel(path):
    """路径尽量相对项目根 —— 报告里 `core/game_ini.py:412` 比绝对路径好读。"""
    try:
        p = os.path.abspath(path)
        if p.lower().startswith(_ROOT.lower()):
            return os.path.relpath(p, _ROOT).replace('\\', '/')
        return p.replace('\\', '/')
    except Exception:
        return str(path)


def _where(tb):
    """异常真正的**抛出点**（tb 的最后一层 = 最内层）。"""
    try:
        frames = traceback.extract_tb(tb)
        if not frames:
            return ''
        f = frames[-1]
        return '%s:%d' % (_rel(f.filename), f.lineno or 0)
    except Exception:
        return ''


def _record(site, tname, msg, tb=None, tag=''):
    """真正的记账（`swallow` / `note` / 线程钩子共用）。绝不抛。"""
    global _TOTAL, _OVERFLOW
    try:
        msg = ' '.join(str(msg).split()) if msg is not None else ''
        if len(msg) > _MAX_MSG:
            msg = msg[:_MAX_MSG - 1] + '…'
        key = (site, tname)
        now = time.time()
        with _LOCK:
            _TOTAL += 1
            it = _ITEMS.get(key)
            if it is None:
                if len(_ITEMS) >= _MAX_KEYS:
                    _OVERFLOW += 1
                    return
                _ITEMS[key] = {
                    'site': site, 'type': tname, 'n': 1,
                    'first': now, 'last': now, 'msg': msg,
                    'at': _where(tb) if tb is not None else '', 'tag': tag or '',
                }
                if _VERBOSE:
                    sys.stderr.write('[diag] %s %s: %s (throw at %s)\n'
                                     % (site, tname, msg,
                                        (tb is not None and _where(tb)) or '?'))
            else:
                it['n'] += 1
                it['last'] = now
                if not it['msg'] and msg:
                    it['msg'] = msg
    except BaseException:
        global _BROKEN
        _BROKEN = True


def swallow(tag=''):
    """吞掉当前异常并记一笔。**替换 `except ...: pass` 用**。

    必须在 `except` 块内直接调用（内部靠 `sys.exc_info()` 取当前异常）。
    永远不抛异常；定位信息取调用者的文件/行号 —— 也就是那个 `except` 的位置。

    `tag='fallback'` 表示**设计内的降级**（utf-8 解不了就试 gbk、DLL 不可用就退回纯
    Python）。这类会高频命中（实测一次解档 2962 次），所以只计数、不进报告明细 ——
    否则真正的异常会被噪声淹掉。详见 `summary()` / `dump()` 的"设计内降级"段。
    """
    global _BROKEN
    if _BROKEN:
        return
    try:
        typ, exc, tb = sys.exc_info()
        f = sys._getframe(1)
        site = '%s:%d' % (_rel(f.f_code.co_filename), f.f_lineno)
        _record(site, typ.__name__ if typ is not None else 'None', exc, tb, tag)
    except BaseException:
        _BROKEN = True            # 观测器自身出错 ⇒ 永久闭嘴，绝不牵连主流程


def note(tag, detail=''):
    """记一条**不是异常**的降级/回退（例如"规则文件没找到，改用内置表"）。

    和 `swallow()` 同一个缓冲区，只是没有异常对象。
    """
    global _BROKEN
    if _BROKEN:
        return
    try:
        f = sys._getframe(1)
        site = '%s:%d' % (_rel(f.f_code.co_filename), f.f_lineno)
        _record(site, 'note:%s' % tag, detail, None, tag)
    except BaseException:
        _BROKEN = True


def snapshot(include_fallback=False):
    """→ 按次数降序的条目列表（给日志/界面用，返回副本，调用方可随意改）。

    默认**不含** `tag='fallback'` 的设计内降级 —— 那些是"本该失败"的，列出来只会
    把真异常挤下去。`include_fallback=True` 时全给（`dump()` 用）。
    """
    try:
        with _LOCK:
            out = [dict(v) for v in _ITEMS.values()
                   if include_fallback or v.get('tag') != 'fallback']
        out.sort(key=lambda x: (-x['n'], x['site']))
        return out
    except BaseException:
        return []


def fallback_counts():
    """→ `(设计内降级类数, 总次数)` —— 给日志里那行"另有 N 处降级"用。"""
    try:
        with _LOCK:
            items = [v for v in _ITEMS.values() if v.get('tag') == 'fallback']
        return len(items), sum(v['n'] for v in items)
    except BaseException:
        return 0, 0


def counts():
    """→ `(去重后的类数, 总次数, 溢出条数)`。"""
    try:
        with _LOCK:
            return len(_ITEMS), _TOTAL, _OVERFLOW
    except BaseException:
        return 0, 0, 0


def summary(max_lines=8):
    """一行标题 + 最多 N 条明细 —— 给错误日志/状态栏用，永远返回字符串。

    设计内降级（`tag='fallback'`）只汇总成一行，不占明细行。
    """
    try:
        n, total, over = counts()
        fn, ftot = fallback_counts()
        real_n = n - fn
        if not n or (real_n == 0 and fn == 0):
            return ''
        items = snapshot()[:max_lines]
        lines = []
        if real_n:
            head = '静默异常：%d 类 / 共 %d 次' % (real_n, total - ftot)
            if over:
                head += '（另有 %d 次因超出上限未单独统计）' % over
            lines.append(head)
            for it in items:
                lines.append('  %-34s %-22s ×%-4d %s'
                             % (it['site'], it['type'], it['n'], it['msg'][:60]))
                if it['at'] and it['at'] != it['site']:
                    lines.append('      抛出点：%s' % it['at'])
            if real_n > len(items):
                lines.append('  …… 其余 %d 类见 diag 日志' % (real_n - len(items)))
        if fn:
            lines.append('设计内降级：%d 处 / 共 %d 次（utf-8→gbk、DLL→纯 Python 之类，正常）'
                         % (fn, ftot))
        return '\n'.join(lines)
    except BaseException:
        return ''


def _log_dir():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    for d in (os.path.join(base, 'RA2SED_logs'),
              os.path.join(os.environ.get('TEMP') or '.', 'RA2SED_logs')):
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except Exception:
            continue
    return None


def dump(path=None, prefix='diag'):
    """把缓冲区写成一份文本日志，返回路径（写不了返回 None）。

    没有内容时**不写空文件**（免得玩家目录里一堆无意义的 diag-*.log）。
    """
    try:
        if not counts()[0]:
            return None
        if path is None:
            d = _log_dir()
            if not d:
                return None
            path = os.path.join(d, '%s-%s.log'
                                % (prefix, time.strftime('%Y%m%d-%H%M%S')))
        lines = ['时间   : %s' % time.strftime('%Y-%m-%d %H:%M:%S'),
                 '运行时长: %.1f 秒' % (time.time() - _START),
                 '动作   : %s' % prefix]
        try:
            import app_version
            _v = getattr(app_version, 'version_text', None)
            lines.append('版本   : %s' % (_v() if callable(_v)
                                          else getattr(app_version, 'SEMVER', '?')))
        except Exception:
            _swallow()
        lines.append('Python : %s' % sys.version.replace('\n', ' '))
        lines.append('冻结   : %s' % bool(getattr(sys, 'frozen', False)))
        n, total, over = counts()
        fn, ftot = fallback_counts()
        lines.append('统计   : %d 类 / %d 次%s'
                     % (n, total, ('（溢出 %d）' % over) if over else ''))
        if fn:
            lines.append('其中   : 设计内降级 %d 类 / %d 次（utf-8→gbk、DLL→纯 Python 等，正常）'
                         % (fn, ftot))
        lines.append('')
        lines.append('%-38s %-24s %6s  %s' % ('吞异常的位置', '异常类型', '次数', '信息'))
        lines.append('-' * 100)
        for it in snapshot(include_fallback=True):
            tag = '  〔设计内降级〕' if it['tag'] == 'fallback' else ''
            lines.append('%-38s %-24s %6d  %s%s'
                         % (it['site'], it['type'], it['n'], it['msg'], tag))
            if it['at'] and it['at'] != it['site']:
                lines.append('%-38s %-24s %6s  ↳ 抛出点 %s'
                             % ('', '', '', it['at']))
            if it['tag'] and it['tag'] != 'fallback':
                lines.append('%-38s %-24s %6s  ↳ 标记 %s'
                             % ('', '', '', it['tag']))
        with open(path, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write('\n'.join(lines) + '\n')
        return path
    except BaseException:
        return None


def install():
    """挂上 unhandled 钩子：**只记录、不改变原有行为**。

    - `threading.excepthook`：后台线程挂掉时（原来无声无息）写一份 diag 日志，
      再转交给原钩子 —— 行为与之前一致，只是多了份线索；
    - 不动 `sys.excepthook`（tkinter / PyInstaller 各有自己的处理，抢过来会改变现状）。
    """
    try:
        prev_t = getattr(threading, 'excepthook', None)

        def _t_hook(args):
            try:
                th = getattr(args, 'thread', None)
                _record('thread:%s' % (getattr(th, 'name', '?') or '?'),
                        getattr(getattr(args, 'exc_type', None), '__name__', 'ThreadError'),
                        getattr(args, 'exc_value', None),
                        getattr(args, 'exc_traceback', None),
                        'uncaught')
                dump(prefix='thread')
            except BaseException:
                pass
            if prev_t is not None and prev_t is not _t_hook:
                try:
                    prev_t(args)
                except BaseException:
                    pass

        threading.excepthook = _t_hook
    except BaseException:
        pass
    return True


def reset():
    """清空缓冲区（自检用）。"""
    global _TOTAL, _OVERFLOW, _BROKEN
    try:
        with _LOCK:
            _ITEMS.clear()
            _TOTAL = 0
            _OVERFLOW = 0
            _BROKEN = False
    except BaseException:
        pass


# ---- 两种导入方式指向同一个实例 ----
# core/ 在 sys.path 上时叫 `diag`；顶层脚本用 `import core.diag` 时叫 `core.diag`。
# 不对齐会出现**两份缓冲区**（一边记一边空），排障时最容易被这个骗。
try:
    sys.modules.setdefault('diag', sys.modules[__name__])
    sys.modules.setdefault('core.diag', sys.modules[__name__])
except Exception:
    pass
