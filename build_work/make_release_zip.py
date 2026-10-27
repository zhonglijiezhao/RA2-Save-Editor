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
VER = sys.argv[1] if len(sys.argv) > 1 else '1.3.0'
OUT = os.path.join(os.path.dirname(ROOT), 'RA2SaveEditor-v%s-win64.zip' % VER)

# 玩家自己的东西：不进包
SKIP_TOP = {'unit_signatures.json', '自检结果.txt', '读我-先看这个.txt'}
SKIP_EXT_TOP = ('.rar', '.zip', '.7z', '.lnk')

exe = os.path.join(SRC, 'RA2存档编辑器.exe')
assert os.path.exists(exe), 'exe 不存在：%s' % exe

n = 0
total = 0
with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    # ① 顶层 exe
    z.write(exe, 'RA2存档编辑器.exe')
    n += 1
    total += os.path.getsize(exe)
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
