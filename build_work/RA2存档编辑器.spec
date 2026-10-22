# -*- mode: python ; coding: utf-8 -*-
import os

# ★ 2026-10-22 修：原来写 '..\core' / '..\app_main.py'，是相对 **cwd** 解析的。
#   在 build_work/ 里跑就对，在项目根跑就变成 1RA2存档分析工具\core（不存在）
#   ⇒ 打包"成功"但 PYZ 里没有 editor_core，exe 一启动就 ModuleNotFoundError。
#   现在一律按 spec 自身位置算绝对路径，cwd 无关。
_ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))
_CORE = os.path.join(_ROOT, 'core')


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
    pathex=[_CORE],
    binaries=[],
    datas=_datas,
    hiddenimports=['editor_core', 'cfb', 'cfb_write', 'map_render', 'map_ini', 'game_ini',
                   'unit_comp_lib', 'mix', 'lzo_fast', 'ai_objects'],
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
