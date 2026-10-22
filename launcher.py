# -*- coding: utf-8 -*-
"""RA2 存档编辑器 —— ASCII 启动入口

为什么要这个文件：
  .bat 批处理必须保持「纯 ASCII + CRLF」。脚本文件名含中文时，
  如果把中文直接写进 bat，会因为代码页/换行问题导致 cmd 解析失败
  （典型症状：双击 bat 什么都不发生）。
  这里让 bat 只调用纯 ASCII 的 launcher.py，由 Python 自己去处理
  中文文件名与路径（Python 3 的 UTF-8 支持是可靠的）。

另外负责把启动期异常写进「启动错误.txt」并弹窗，
否则 pythonw 无控制台会把错误全部吞掉。

用法:
    pythonw launcher.py            正常启动
    python  launcher.py --selftest 自检（构造界面后立刻退出，不进入消息循环）
"""
import os
import sys
import runpy
import traceback
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, 'RA2存档编辑器.py')
LOG = os.path.join(HERE, '启动错误.txt')


def _report(tb_text):
    """写日志 + 弹窗（弹窗失败也不能再抛异常）"""
    try:
        with open(LOG, 'w', encoding='utf-8-sig') as f:
            f.write('时间: %s\n入口: %s\n\n%s\n'
                    % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), MAIN, tb_text))
    except Exception:
        pass
    try:
        import tkinter.messagebox as mb
        mb.showerror('RA2 存档编辑器 启动失败',
                     tb_text[-1600:] + '\n\n完整信息已写入:\n' + LOG)
    except Exception:
        pass


def _selftest():
    """不进入 mainloop 的自检：验证导入链 + 界面能否构造"""
    import importlib.util
    spec = importlib.util.spec_from_file_location('_ra2editor_main', MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # 模块级受 __main__ 保护，不会 mainloop
    app = mod.App()
    app.withdraw()
    app.update_idletasks()
    rows = len(app.tree.get_children())
    app.destroy()
    print('SELFTEST OK  界面构造成功，存档列表 %d 行' % rows)
    return 0


if __name__ == '__main__':
    os.chdir(HERE)
    sys.path.insert(0, HERE)
    try:
        if '--selftest' in sys.argv:
            sys.exit(_selftest())
        runpy.run_path(MAIN, run_name='__main__')
    except SystemExit:
        raise
    except BaseException:
        _report(traceback.format_exc())
        raise
