# -*- coding: utf-8 -*-
"""打发布包：`RA2SaveEditor-v<VER>-win64.zip`。

口径（踩过的坑记在这里）：
- **顶层就是 exe + _internal/**，解压后直接双击 exe 就能跑（玩家不用再进一层目录）；
- `_internal/base_library.zip` **必须留**（一刀切跳 .zip ⇒ exe 起不来）；
- 只跳**顶层**的压缩包（build_dist 里可能被玩家塞了 .rar/.7z），`_internal` 里的一个不能跳；
- 玩家自己放的 `unit_signatures.json` / `自检结果.txt` / `读我-先看这个.txt` **不进包**。
"""
import hashlib
import io
import os
import sys
import zipfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = r'C:/Users/zhao5/Desktop/1RA2存档分析工具/RA2存档编辑器'
SRC = os.path.join(ROOT, 'build_dist', 'RA2存档编辑器')
VER = sys.argv[1] if len(sys.argv) > 1 else '1.4.0'
OUT = os.path.join(os.path.dirname(ROOT), 'RA2SaveEditor-v%s-win64.zip' % VER)

# 玩家自己的东西：不进包
SKIP_TOP = {'unit_signatures.json', '自检结果.txt', '读我-先看这个.txt'}
SKIP_EXT_TOP = ('.rar', '.zip', '.7z', '.lnk')
README_NAME = '读我-先看这个.txt'

README_TMPL = """RA2 / 尤里的复仇 —— 存档编辑器  v%(ver)s
=====================================================

解压后直接双击「RA2存档编辑器.exe」即可，不需要安装 Python。


【三件事，请先看】
-----------------------------------------------------
1) 别只拷 exe！
   这个程序是「exe + _internal 文件夹」两部分，缺一不可。
   拷贝或移动时要整个文件夹一起带走；只拷 exe 会报
   "Failed to load Python DLL"。

2) 第一次打开可能弹「Windows 已保护你的电脑」
   因为本程序没有买代码签名证书（个人项目，一年上千块）。
   处理办法：点「更多信息」→「仍要运行」；
   或者右键 exe → 属性 → 勾选「解除锁定」→ 确定。
   （zip 是从网上下载的话，先对 zip 做一次"解除锁定"再解压更省事。）

3) 建议解压到桌面或 D 盘，别放 C://Program Files
   程序要在自己目录里写 crash.log（出错日志）。放在 Program Files 下
   没有写权限，真出问题时反而什么都留不下来。


【系统要求】
-----------------------------------------------------
  Windows 10 / 11（64 位）   正常
  Windows 8.1（64 位）       理论可以，未实测
  Windows 7                  不支持 —— 打包用的 Python 3.13 要求 Win8.1+
                             （Win7 上会报"无法定位程序输入点 PathCchCombineEx"）
  32 位系统                  不支持 —— 这是 64 位程序


【改档前请备份】
-----------------------------------------------------
  工具里有「备份存档」一键按钮，也可以手动把 .SAV 复制一份。
  本工具只读地图、只写存档：任何情况下都不改 .map / .mmx / .yrm / .mpr。


【出错了怎么办】
-----------------------------------------------------
  同目录下会生成 crash.log，把它连同存档来源一起发到：
  https://github.com/zhonglijiezhao/RA2-Save-Editor/issues

  说明里写清：哪个任务/地图、点了什么、期望是什么。能复现就最好。


版本 v%(ver)s    构建 p%(build)s
"""


exe = os.path.join(SRC, 'RA2存档编辑器.exe')
assert os.path.exists(exe), 'exe 不存在：%s' % exe

n = 0
total = 0
with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    # ① 顶层 exe
    z.write(exe, 'RA2存档编辑器.exe')
    n += 1
    total += os.path.getsize(exe)
    # ①' 读我（★2026-10-28：玩家最常见的三个坑都写在这儿，省一轮问答）
    _build = ''
    try:
        _b = open(os.path.join(os.path.dirname(ROOT), 'RA2存档编辑器', 'build_work',
                               '_LAST_BUILD.txt'), encoding='utf-8').read()
        _build = _b.strip().split('=')[-1]
    except Exception:
        pass
    z.writestr(README_NAME, (README_TMPL % {'ver': VER, 'build': _build})
               .replace('\n', '\r\n'))
    n += 1

    # ② 顶层其它文件（跳过玩家自己的 + 压缩包）
    for name in sorted(os.listdir(SRC)):
        p = os.path.join(SRC, name)
        if not os.path.isfile(p) or name == 'RA2存档编辑器.exe':
            continue
        if name in SKIP_TOP or name.lower().endswith(SKIP_EXT_TOP):
            continue
        z.write(p, name)
        n += 1
        total += os.path.getsize(p)
    # ③ _internal/**（一个不落，含 base_library.zip）
    base = os.path.join(SRC, '_internal')
    for dirpath, _dirs, files in os.walk(base):
        for f in sorted(files):
            p = os.path.join(dirpath, f)
            rel = os.path.relpath(p, SRC).replace('\\', '/')
            z.write(p, rel)
            n += 1
            total += os.path.getsize(p)

size = os.path.getsize(OUT)


def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


with zipfile.ZipFile(OUT) as z:
    names = z.namelist()
    has_base = any(x.endswith('_internal/base_library.zip') for x in names)
    top_exe = 'RA2存档编辑器.exe' in names
    bad = [x for x in names if x.lower().endswith(('.rar', '.7z'))]

print('产物   ：%s' % OUT)
print('大小   ：%.1f MB（%d 字节，原始 %.1f MB）' % (size / 1e6, size, total / 1e6))
print('条目   ：%d' % n)
print('顶层 exe          ：%s' % ('✓' if top_exe else '✗'))
print('base_library.zip  ：%s' % ('✓' if has_base else '✗ 致命'))
print('混进压缩包        ：%s' % (bad if bad else '无'))
print('md5    ：%s' % md5(OUT))
assert top_exe and has_base and not bad
