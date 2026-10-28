# -*- mode: python ; coding: utf-8 -*-
import os

# ★ 2026-10-22 修：原来写 '..\core' / '..\app_main.py'，是相对 **cwd** 解析的。
#   在 build_work/ 里跑就对，在项目根跑就变成 1RA2存档分析工具\core（不存在）
#   ⇒ 打包"成功"但 PYZ 里没有 editor_core，exe 一启动就 ModuleNotFoundError。
#   现在一律按 spec 自身位置算绝对路径，cwd 无关。
_ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))
_CORE = os.path.join(_ROOT, 'core')
# ★2026-10-24 §227：**打包必用 `build_work/build.py`**（它带 --workpath build_work/_work）。
#   直接 `-m PyInstaller` 会把中间产物落到 `<cwd>/build/<spec名>/`，那里有个**同名 exe**
#   （半成品、缺 _internal\python313.dll）—— 玩家在资源管理器里翻到双击 ⇒
#   "Failed to load Python DLL ... python313.dll"（2026-10-24 01:25 实测中招）。
#   workpath 是**命令行参数**，spec 里设不了 ⇒ 统一走 build.py。


def _c(*parts):
    return os.path.join(_CORE, *parts)


# ★2026-10-22（开源）：`core/ini_builtin/` 里是**游戏原版** rulesmd.ini / artmd.ini（EA 版权），
#   .gitignore 已排除，所以从 GitHub clone 下来是**没有**的。这里改成"有就带上、没有就跳过"，
#   否则新克隆的人一打包就报 FileNotFoundError。
#   没有内置规则也能正常跑：`game_ini.find_sources()` 会依次找 存档目录 → 上一级 → 内置，
#   存档放在游戏目录里时天然就能读到游戏自己的 rulesmd.ini；别处则用 config.json 的 game_dir。
_datas = [(_c('art_sizes.json'), '.'), (_c('type_signature.json'), '.'),
          (_c('unit_comp_lib', '*.bin'), 'unit_comp_lib'),
          (_c('unit_comp_lib', 'index.json'), 'unit_comp_lib'),
          # ★ LZO 加速 DLL（C 后端）：没有它也能跑，只是慢（纯 Python 回退）
          (_c('lzo_fast.dll'), '.')]
for _n in ('rulesmd.ini', 'artmd.ini'):
    _p = _c('ini_builtin', _n)
    if os.path.exists(_p):
        _datas.append((_p, 'ini_builtin'))


a = Analysis(
    [os.path.join(_ROOT, 'app_main.py')],
    # ★2026-10-28（P2-8）：页签拆成 `ui/` 包（在项目根，不在 core/）⇒ 搜索路径
    #   必须同时含项目根，否则 PyInstaller 追不到 `ui.page_*`，exe 一开页就 ImportError。
    pathex=[_ROOT, _CORE],
    binaries=[],
    datas=_datas,
    hiddenimports=['editor_core', 'cfb', 'cfb_write', 'map_render', 'map_ini', 'game_ini',
                   'unit_comp_lib', 'mix', 'lzo_fast', 'ai_objects',
                   'script_actions'],   # ★§253 脚本动作中文名表（动态 import，必须列在这里）
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RA2存档编辑器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # ★2026-10-28：版本资源（属性→详细信息里能看到 v1.4.0）。
    #   文件由 build.py 每次按版本号重写；没有它 exe 的"详细信息"全是空的，
    #   玩家报 bug 时也没法确认用的是哪一版。
    version=(os.path.join(SPECPATH, '_version_info.txt')
             if os.path.exists(os.path.join(SPECPATH, '_version_info.txt')) else None),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RA2存档编辑器',
)
