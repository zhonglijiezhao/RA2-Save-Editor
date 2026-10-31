# -*- coding: utf-8 -*-
"""版本信息 —— 界面右下角/主界面头部显示的版本号来自这里。

★2026-10-29（玩家要求「主页面加版本号和 GITHUB 链接」）：
  玩家报 bug 的第一句永远是"我用的是哪一版"，而 exe 属性里的版本号没人会去翻。
  所以程序自己得有个可读来源，界面上直接显示，截个图就知道版本。

**前两行由 `build_work/build.py` 每次打包前自动重写**（`--ver` → SEMVER，`--p` → BUILD），
别手改；`REPO` 以下是人维护的部分，build.py 不碰。

源码直接跑时，用的就是上次打包写下的值 —— 这也是"代码现在对应哪个构建"的最实际记录。
"""

SEMVER = '1.4.2'
BUILD = 305

# 仓库地址（界面上「GitHub ↗」点开的就是它）
REPO = 'https://github.com/zhonglijiezhao/RA2-Save-Editor'
REPO_SHORT = 'zhonglijiezhao/RA2-Save-Editor'
# 反馈问题时先去这儿看有没有同款（玩家最常问的"武器改了没用""打开卡死"都在里面）
ISSUES = REPO + '/issues'


def version_text():
    """→ `'v1.4.2 · p283'`；没有构建号时只给 `'v1.4.2'`。

    带构建号是有意的：同一个产品版本号下可能重打包好几次（改了日志文案之类），
    光看 1.4.2 分不出是哪一次。
    """
    try:
        b = int(BUILD)
    except Exception:
        b = 0
    if b > 0:
        return 'v%s · p%d' % (SEMVER, b)
    return 'v%s' % SEMVER
