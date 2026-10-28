# -*- coding: utf-8 -*-
"""§302 自检：自动探测游戏目录 + 主界面「…」指定目录。

★2026-10-28（玩家要求）：「加自动探测 主界面加按钮 手动指定红警目录」。
  背景：新电脑上玩家把存档放桌面、游戏装 D 盘 ⇒ 原来的三层查找
  （显式 / 存档目录 / 存档上一级）全落空，退化到内置快照，
  "非原版"判定和可建造判定静默失效。

本自检钉住：
  ① `game_ini.auto_detect_game_dirs()` 返回 list（判据是 gamemd.exe 等，不是 rulesmd.ini）
  ② `detect_game_dir()` 若给出目录，那个目录必须真能解出 rules
  ③ `SaveDoc.rules_source()` 带 `source` 字段（user/save/mix/auto/builtin）——
     界面靠它说清"你选的目录到底用上没"
  ④ 主界面有 `pick_game_dir` / `apply_game_dir`，且 `apply_game_dir(有效目录)`
     之后 `rules_source()['ok']` 为真、source='user'

⚠ 全程 `persist=False`，不改玩家的配置文件。

跑法：python _selftest_ev302_game_dir.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

import importlib.util  # noqa: E402

import game_ini  # noqa: E402
from editor_core import SaveDoc  # noqa: E402

PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


# ---------------- ① 自动探测 ----------------
dirs = game_ini.auto_detect_game_dirs()
ck(isinstance(dirs, list), 'auto_detect_game_dirs() 返回 list（%d 个候选）' % len(dirs))
ck(all(os.path.isdir(x) for x in dirs), '候选目录都真实存在')
print('     候选：%s' % (dirs[:4] if dirs else '(本机没扫到)'))

# ---------------- ② detect 出来的目录必须真能用 ----------------
g = game_ini.detect_game_dir()
if g:
    src, d, rp, ap = game_ini.find_sources(None, g)
    ck(os.path.isdir(g) and bool(rp) and os.path.exists(rp),
       'detect_game_dir() = %s，且能解出 rules（%s）' % (g, os.path.basename(rp)))
else:
    ck(True, 'detect_game_dir() 没找到（本机无游戏目录，跳过验证）')

# ---------------- ③ rules_source 带 source ----------------
s = SaveDoc.rules_source() or {}
ck('source' in s, 'rules_source() 带 source 字段（当前 = %r）' % s.get('source'))

# ---------------- ④ 主界面入口 ----------------
spec = importlib.util.spec_from_file_location('gui', os.path.join(HERE, 'RA2存档编辑器.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
app = m.App()
for _ in range(20):
    app.update()

ck(hasattr(app, 'pick_game_dir'), '主界面有 pick_game_dir（「…」按钮的回调）')
ck(hasattr(app, 'apply_game_dir'), '主界面有 apply_game_dir（统一生效入口）')

_old = SaveDoc.GAME_DIR
try:
    if g:
        ok = app.apply_game_dir(g, persist=False)
        s2 = SaveDoc.rules_source() or {}
        ck(bool(ok) and bool(s2.get('rules')),
           'apply_game_dir(%s) 后 rules 生效' % g)
        ck(s2.get('source') == 'user',
           '来源标成 user（说明用的是**指定的**目录，不是自动探测顶替）—— 实际 %r'
           % s2.get('source'))
        ck(app.status.get().startswith('规则已生效'),
           '状态栏提示「规则已生效」：%s' % app.status.get()[:40])
    else:
        ck(True, '本机没游戏目录，跳过 apply_game_dir 验证')
finally:
    SaveDoc.set_game_dir(_old)
    app.destroy()

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    for x in FAIL:
        print('  ✗ %s' % x)
sys.exit(1 if FAIL else 0)
