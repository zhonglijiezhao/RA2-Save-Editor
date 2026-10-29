# -*- coding: utf-8 -*-
"""§303 自检：主界面头部的「版本号 + GitHub 链接」。

★2026-10-29（玩家要求「在主页面加版本号和 GITHUB 链接」）：
  动机是"报 bug 时说不出自己用哪一版" —— exe 属性里有，但玩家不会去翻，
  而且没签名时还被 SmartScreen 挡一层。所以：

  ① `core/app_version.py` 给程序一个**自己能读**的版本来源
     （前两行由 `build_work/build.py` 每次打包前重写：`--ver` → SEMVER、`--p` → BUILD）
  ② 头部队标题后面显示 `v1.4.2 · p283`，点一下把「版本 + Python + 系统」复制到剪贴板
  ③ 旁边一个蓝色下划线的「GitHub ↗」，点了开浏览器；打不开就把地址复制出来并弹出来

这个自检钉住三件事：**文本对得上 / 点得动（不发真网络请求）/ 打包脚本真会重写版本**。

跑法：py _selftest_ev303_version_link.py
"""
import io
import os
import re
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

import importlib.util  # noqa: E402

PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


spec = importlib.util.spec_from_file_location('gui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
app_version = m.app_version

# ---------------- ① 版本来源本身 ----------------
ck(re.match(r'^\d+\.\d+\.\d+$', str(app_version.SEMVER)) is not None,
   'SEMVER 是 x.y.z 形式（实得 %r）' % app_version.SEMVER)
ck(isinstance(app_version.BUILD, int) and app_version.BUILD > 0,
   'BUILD 是正整数构建号（实得 %r）' % app_version.BUILD)

vt = app_version.version_text()
ck(vt.startswith('v' + str(app_version.SEMVER)),
   'version_text() 以 v<SEMVER> 开头（实得 %r）' % vt)
ck(('p%d' % app_version.BUILD) in vt,
   'version_text() 带构建号 p%d（实得 %r）' % (app_version.BUILD, vt))

_old_build = app_version.BUILD
app_version.BUILD = 0
ck(app_version.version_text() == 'v' + str(app_version.SEMVER),
   '没有构建号时只给 v<SEMVER>（实得 %r）' % app_version.version_text())
app_version.BUILD = _old_build

ck(app_version.REPO.startswith('https://github.com/'),
   '仓库地址是 https 的 GitHub 链接（%s）' % app_version.REPO)
ck(app_version.REPO.rstrip('/').endswith('RA2-Save-Editor'),
   '仓库地址指向 RA2-Save-Editor（%s）' % app_version.REPO)
ck(app_version.ISSUES == app_version.REPO + '/issues',
   'ISSUES 由 REPO 拼出来（%s）' % app_version.ISSUES)

# ---------------- ② 界面元素 ----------------
app = m.App()
for _ in range(40):
    app.update()

ck(hasattr(app, 'lbl_ver') and hasattr(app, 'lbl_repo'),
   '主界面有 lbl_ver / lbl_repo 两个标签')
ck(app.lbl_ver.cget('text') == vt,
   '版本标签文字 = version_text()（实得 %r）' % app.lbl_ver.cget('text'))
ck('GitHub' in str(app.lbl_repo.cget('text')),
   '右边那个标签写着 GitHub（实得 %r）' % app.lbl_repo.cget('text'))
ck(str(app.lbl_ver.cget('cursor')) == 'hand2'
   and str(app.lbl_repo.cget('cursor')) == 'hand2',
   '两个标签都是手型光标（看着就能点）')
ck(str(app.lbl_repo.cget('underline')) in ('1', 'True', 'true')
   or 'underline' in str(app.lbl_repo.cget('font')),
   'GitHub 标签带下划线（%r / %r）'
   % (app.lbl_repo.cget('underline'), app.lbl_repo.cget('font')))

# 两个标签跟标题在同一个头部容器里（而不是被 pack 到别处去了）
ck(app.lbl_ver.master is app.lbl_repo.master,
   '版本号与 GitHub 链接在同一个容器里')

# 不被右侧那排控件挤掉：链接右边界必须在游戏目录输入框左边
_entries = [c for c in app.lbl_ver.master.winfo_children()
            if c.winfo_class() == 'TEntry']
if _entries:
    ex = _entries[0].winfo_x()
    ck(app.lbl_repo.winfo_x() + app.lbl_repo.winfo_width() < ex,
       '版本/链接没和右侧「游戏目录」输入框重叠（链接右缘 %d < 输入框 %d）'
       % (app.lbl_repo.winfo_x() + app.lbl_repo.winfo_width(), ex))
else:
    ck(False, '头部找不到游戏目录输入框（布局变了？）')

# ---------------- ③ 点版本号 → 复制 ----------------
t = app.copy_version()
ck('v' + str(app_version.SEMVER) in t, '复制内容带版本号（%r）' % t)
ck('Python' in t and re.search(r'Python \d+\.\d+', t) is not None,
   '复制内容带 Python 版本（%r）' % t)
try:
    clip = app.clipboard_get()
except Exception:
    clip = ''
ck(clip == t, '剪贴板里就是这行（实得 %r）' % clip)
ck('已复制' in app.status.get(), '状态栏提示已复制（%r）' % app.status.get())

# ---------------- ④ 点 GitHub → 打开浏览器（打补丁，不联网） ----------------
_opened = []
_real_open = m.webbrowser.open
m.webbrowser.open = lambda url, *a, **k: (_opened.append(url), True)[1]
try:
    ok = app.open_repo()
finally:
    m.webbrowser.open = _real_open
ck(ok is True, 'open_repo() 在浏览器可用时返回 True')
ck(_opened and _opened[0] == app_version.REPO,
   '打开的是 REPO 那个地址（实得 %r）' % (_opened[:1],))
ck(app_version.REPO in app.status.get(),
   '状态栏写出了打开的地址（%r）' % app.status.get())

# 打不开时的降级：不能静默失败，地址要落到剪贴板 + 弹窗
_said = []
_real_info = m.messagebox.showinfo
m.messagebox.showinfo = lambda *a, **k: _said.append(a)


def _boom(url, *a, **k):
    raise OSError('no default browser')


m.webbrowser.open = _boom
try:
    ok2 = app.open_repo()
finally:
    m.webbrowser.open = _real_open
    m.messagebox.showinfo = _real_info
ck(ok2 is False, '浏览器打不开时 open_repo() 返回 False（不假装成功）')
ck(bool(_said), '打不开时弹了提示（而不是静默）')
try:
    clip2 = app.clipboard_get()
except Exception:
    clip2 = ''
ck(clip2 == app_version.REPO, '打不开时把地址复制到了剪贴板（实得 %r）' % clip2)
ck(app_version.REPO in app.status.get(), '打不开时状态栏也有地址')

app.destroy()

# ---------------- ⑤ 打包脚本真的会重写版本 ----------------
bspec = importlib.util.spec_from_file_location(
    'bld', os.path.join(HERE, 'build_work', 'build.py'))
bld = importlib.util.module_from_spec(bspec)
bspec.loader.exec_module(bld)

ck(hasattr(bld, 'write_app_version'), 'build.py 提供 write_app_version()')

_tmp = tempfile.mkdtemp()
_fake = os.path.join(_tmp, 'app_version.py')
with io.open(_fake, 'w', encoding='utf-8', newline='') as f:
    f.write("# -*- coding: utf-8 -*-\n"
            "SEMVER = '0.0.1'\n"
            "BUILD = 7\n"
            "REPO = 'https://github.com/x/y'\n"
            "ISSUES = REPO + '/issues'\n"
            "\n"
            "\n"
            "def version_text():\n"
            "    b = int(BUILD)\n"
            "    return ('v%s · p%d' % (SEMVER, b)) if b > 0 else ('v%s' % SEMVER)\n")
_old_appver = bld.APPVER
bld.APPVER = _fake
try:
    bld.write_app_version(4321, '9.9.9')
    after = io.open(_fake, encoding='utf-8').read()
finally:
    bld.APPVER = _old_appver
ck("SEMVER = '9.9.9'" in after, 'write_app_version 改了 SEMVER（%s）'
   % [l for l in after.splitlines() if l.startswith('SEMVER')])
ck('BUILD = 4321' in after, 'write_app_version 改了 BUILD（%s）'
   % [l for l in after.splitlines() if l.startswith('BUILD')])
ck("REPO = 'https://github.com/x/y'" in after,
   'write_app_version 不碰 REPO（手维护的那部分原样保留）')
# 生成出来的文件必须自己能跑（语法/缩进没错）
_spec2 = importlib.util.spec_from_file_location('fakever', _fake)
_fv = importlib.util.module_from_spec(_spec2)
try:
    _spec2.loader.exec_module(_fv)
    ok3 = (_fv.version_text() == 'v9.9.9 · p4321')
except Exception as e:
    ok3 = False
    print('    生成的文件导入失败：%s' % e)
ck(ok3, '重写后的 app_version.py 能被导入且 version_text() 正确')
try:
    os.remove(_fake)
    os.rmdir(_tmp)
except Exception:
    pass

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    for x in FAIL:
        print('  ✗ %s' % x)
sys.exit(1 if FAIL else 0)
