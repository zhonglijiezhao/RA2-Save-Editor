# -*- coding: utf-8 -*-
"""CI 第一步：把**仓库里的** Python 文件全编译一遍（语法门禁）。

为什么不用 `python -m compileall .`：
  开发机上根目录躺着 300+ 个 `_xxx.py` 一次性探针（`.gitignore` 挡着），
  里面有的是半截脚本、有的故意写坏（`_new_ai_tab.py` 就一个 IndentationError）。
  在本地跑 compileall 会被它们拖红，而在 CI 上又根本不存在 —— 两边的结论对不上。

所以编译清单以 `git ls-files` 为准：**仓库里有谁就编译谁**，两边完全一致。
没有 git 时（比如解压 zip 跑）退回"扫 core/ ui/ + 根目录非下划线开头"。

用法：python ci_compile.py     退出码 0 = 全部通过
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def tracked_py():
    """→ 仓库里被跟踪的 .py 列表（相对路径）；拿不到就返回 None。"""
    try:
        p = subprocess.run(['git', 'ls-files', '-z', '*.py'], cwd=HERE,
                           capture_output=True, timeout=60)
        if p.returncode != 0:
            return None
        names = [x for x in p.stdout.decode('utf-8', 'replace').split('\0') if x]
        return names or None
    except Exception:
        return None


def fallback_py():
    out = []
    for f in sorted(os.listdir(HERE)):
        if f.endswith('.py') and not f.startswith('_'):
            out.append(f)
    for d in ('core', 'ui', 'build_work'):
        dd = os.path.join(HERE, d)
        if not os.path.isdir(dd):
            continue
        for f in sorted(os.listdir(dd)):
            if f.endswith('.py'):
                out.append(os.path.join(d, f))
    return out


def main():
    files = tracked_py()
    src = 'git ls-files'
    if not files:
        files = fallback_py()
        src = '目录扫描（没有 git）'
    print('编译清单来源：%s，共 %d 个文件' % (src, len(files)))

    bad = []
    for f in files:
        path = os.path.join(HERE, f)
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'rb') as fh:
                compile(fh.read(), f, 'exec')
        except SyntaxError as e:
            bad.append('%s:%s %s' % (f, e.lineno, e.msg))
        except ValueError as e:          # 源码里有 NUL 字节之类
            bad.append('%s: %s' % (f, e))
        except Exception as e:
            bad.append('%s: %s' % (f, e))

    if bad:
        print('\n语法不过 %d 个：' % len(bad))
        for b in bad:
            print('  ✗ ' + b)
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
