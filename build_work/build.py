# -*- coding: utf-8 -*-
"""一键打包 RA2 存档编辑器（★这是唯一的打包入口，别的写法都容易踩坑）。

用法：
    D:/py/python.exe build_work/build.py            # 版本号自动 +1（读 _LAST_BUILD.txt）
    D:/py/python.exe build_work/build.py --p 180    # 指定版本号
    D:/py/python.exe build_work/build.py --check    # 只校验现有 build_dist 是否完整

为什么要这个脚本（2026-10-24 §227，玩家实测踩过的坑）：
  · **workpath 必须在命令行给**（`--workpath build_work/_work`）。
    不给就会落在 `<项目>/build/<spec名>/`，那里有个**同名 exe**（半成品，缺
    `_internal\\python313.dll`）——玩家在资源管理器里翻到、双击 ⇒
    "Failed to load Python DLL ... python313.dll"。已有玩家中招。
  · **distpath 每次必须是全新目录**（旧目录上千文件会触发批量删除拦截）。
  · spec 里必须用绝对路径（否则打出"启动即 ModuleNotFoundError"的坏 exe）。
  · 打完后校验 `_internal/python313.dll` 存在、exe 大小合理，再拷进 build_dist。
"""
import os
import re
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # <项目>/build_work
ROOT = os.path.dirname(HERE)                                # <项目>
SPEC = os.path.join(HERE, 'RA2存档编辑器.spec')
WORK = os.path.join(HERE, '_work')
DIST_INNER = os.path.join(ROOT, 'build_dist', 'RA2存档编辑器')
PY = sys.executable
KEEP_DISTS = 3          # 只保留最近 N 个 build_dist_p*（见 prune_dists）


def _last_version():
    p = os.path.join(HERE, '_LAST_BUILD.txt')
    try:
        return int(open(p, encoding='utf-8').read().strip().split('=')[1])
    except Exception:
        return 179


def _save_version(n):
    try:
        with open(os.path.join(HERE, '_LAST_BUILD.txt'), 'w', encoding='utf-8') as f:
            f.write('p=%d\n' % n)
    except Exception:
        pass


def prune_dists(keep=KEEP_DISTS, dry=False):
    """删掉旧的 `build_dist_p*`，只留最近 keep 个（按版本号排，不看 mtime）。

    ★2026-10-28（审查 P4-17）：每个 onedir 产物约 45MB，历史累积到 111 个 = 3.5GB，
      而它们唯一的价值是"回滚到某一版"——源码在 git 里，随时能重打。
      留最近 3 个足够应付"刚发的这版和上两版对比"。

    只认 `build_dist_p<数字>` 这种名字：`build_dist`（部署目录）、
    `build_dist_old_*`、`build_dist_new` 一律不碰（它们不在自动清理范围里）。
    """
    items = []
    for n in os.listdir(ROOT):
        m = re.match(r'^build_dist_p(\d+)$', n)
        p = os.path.join(ROOT, n)
        if m and os.path.isdir(p):
            items.append((int(m.group(1)), n, p))
    items.sort(reverse=True)
    old = items[keep:]
    if not old:
        print('  构建目录已经是干净的（共 %d 个，保留 %d）' % (len(items), keep))
        return 0
    total = 0
    for ver, n, p in old:
        try:
            sz = sum(os.path.getsize(os.path.join(dp, f))
                     for dp, _dn, fs in os.walk(p) for f in fs)
        except Exception:
            sz = 0
        total += sz
        if dry:
            print('  [dry] 将删除 %-22s %6.0f MB' % (n, sz / 1048576.0))
            continue
        shutil.rmtree(p, ignore_errors=True)
        left = os.path.isdir(p)
        print('  [clean] %-22s %6.0f MB  %s' % (n, sz / 1048576.0,
                                                '（删不掉，可能被占用）' if left else ''))
    print('  %s %d 个旧目录，约 %.1f GB'
          % ('待删' if dry else '已删', len(old), total / 1073741824.0))
    return 0


VERINFO = os.path.join(HERE, '_version_info.txt')

VERINFO_TMPL = """# -*- coding: utf-8 -*-
# 自动生成（build.py），别手改 —— 版本号跟着 --p 走。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=%(v4s)s, prodvers=%(v4s)s,
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('FileDescription', 'RA2 / 尤里的复仇 存档编辑器'),
        StringStruct('FileVersion', '%(fv)s'),
        StringStruct('InternalName', 'RA2SaveEditor'),
        StringStruct('OriginalFilename', 'RA2存档编辑器.exe'),
        StringStruct('ProductName', 'RA2 存档编辑器'),
        StringStruct('ProductVersion', '%(ver)s')])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def write_version_info(ver, semver='1.4.0'):
    """写 `_version_info.txt`（PyInstaller 版本资源）→ 路径。

    ★2026-10-28：以前 exe 的"属性→详细信息"全是空的 —— 玩家报 bug 时说不出
      用的是哪一版，只能靠 zip 文件名猜。

    两套号，都有用：
      · 产品版本 `semver`（1.4.0）—— 玩家认这个
      · 文件版本 1.4.0.<p>（1.4.0.257）—— 同一版本号下重打包也能区分
    """
    try:
        v = int(ver)
    except Exception:
        v = 0
    try:
        a, b, c = (list(map(int, str(semver).split('.'))) + [0, 0, 0])[:3]
    except Exception:
        a, b, c = 1, 4, 0
    v4 = (a, b, c, v % 65536)
    txt = VERINFO_TMPL % {'v4s': repr(v4), 'ver': semver,
                          'fv': '%d.%d.%d.%d' % v4}
    with open(VERINFO, 'w', encoding='utf-8') as f:
        f.write(txt)
    return VERINFO


def check(dist_inner=DIST_INNER):
    """校验一个 onedir 产物是否完整（有 exe + _internal/python31x.dll）。"""
    exe = os.path.join(dist_inner, 'RA2存档编辑器.exe')
    if not os.path.isfile(exe):
        return False, '缺 exe：%s' % exe
    internal = os.path.join(dist_inner, '_internal')
    if not os.path.isdir(internal):
        return False, '缺 _internal 目录（这是构建中间产物，不是正式产物）'
    dlls = [f for f in os.listdir(internal) if f.lower().startswith('python3') and f.endswith('.dll')]
    if not dlls:
        return False, '_internal 里没有 python3xx.dll ⇒ exe 一启动就会 Failed to load Python DLL'
    sz = os.path.getsize(exe)
    if sz < 1_500_000:
        return False, 'exe 只有 %d 字节，太小（正常约 3MB）' % sz
    return True, 'OK：%s（%d 字节），_internal 里 %s' % (os.path.basename(exe), sz, dlls[0])


def main():
    args = sys.argv[1:]
    semver = '1.4.0'
    if '--ver' in args:
        try:
            semver = args[args.index('--ver') + 1]
        except Exception:
            print('--ver 后面要跟版本号（如 1.4.0）'); return 2
    semver = '1.4.0'
    if '--ver' in args:
        try:
            semver = args[args.index('--ver') + 1]
        except Exception:
            print('--ver 后面要跟版本号（如 1.4.0）'); return 2
    if '--clean' in args:
        print('清理旧构建目录（保留最近 %d 个 build_dist_p*）' % KEEP_DISTS)
        return prune_dists(dry=('--dry' in args))
    if '--check' in args:
        ok, msg = check()
        print(('[OK]   ' if ok else '[FAIL] ') + msg)
        return 0 if ok else 1
    ver = None
    if '--p' in args:
        try:
            ver = int(args[args.index('--p') + 1])
        except Exception:
            print('--p 后面要跟数字'); return 2
    if ver is None:
        ver = _last_version() + 1
    dist = os.path.join(ROOT, 'build_dist_p%d' % ver)
    print('=== 打包 p%d ===' % ver)
    print('  spec   : %s' % SPEC)
    print('  workpath: %s' % WORK)
    print('  distpath: %s' % dist)
    if os.path.isdir(dist):
        print('  ⚠ distpath 已存在（旧目录上千文件会触发批量删除拦截）⇒ 换版本号或先手动删')
        return 3
    try:
        print('  版本资源: 产品 %s / 文件 %s.p%d' % (semver, semver, ver))
        write_version_info(ver, semver)
    except Exception as e:
        print('  ⚠ 版本资源生成失败（不影响打包）：%s' % e)
    cmd = [PY, '-m', 'PyInstaller', SPEC, '--noconfirm',
           '--workpath', WORK, '--distpath', dist]
    log = os.path.join(ROOT, '_build_p%d.log' % ver)
    with open(log, 'wb') as f:
        rc = subprocess.call(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
    if rc != 0:
        print('  ✗ PyInstaller 失败（rc=%d），看日志：%s' % (rc, log))
        return rc
    src = os.path.join(dist, 'RA2存档编辑器')
    ok, msg = check(src)
    print(('  [OK]   ' if ok else '  [FAIL] ') + msg)
    if not ok:
        return 4
    if not os.path.isdir(DIST_INNER):
        print('  ✗ 找不到 build_dist/RA2存档编辑器（部署目录）'); return 5
    try:
        shutil.copytree(src, DIST_INNER, dirs_exist_ok=True)
    except Exception as e:
        print('  ✗ 拷贝到 build_dist 失败：%s' % str(e)[:120])
        print('    ★最常见原因：**编辑器正开着**（exe/dll 被占用）⇒ 关掉窗口再重跑。')
        print('    产物在：%s（完整，可直接双击）' % src)
        _save_version(ver)
        return 7
    ok2, msg2 = check(DIST_INNER)
    print(('  [OK]   已部署到 build_dist：' if ok2 else '  [FAIL] 部署后校验失败：') + msg2)
    _save_version(ver)
    print('=== 完成 p%d ===' % ver)
    prune_dists()          # ★P4-17：只留最近 3 个，别让磁盘长到几个 GB
    return 0 if ok2 else 6


if __name__ == '__main__':
    sys.exit(main())
