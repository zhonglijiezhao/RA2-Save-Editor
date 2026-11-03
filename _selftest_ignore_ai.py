# -*- coding: utf-8 -*-
"""§SOV05 回归：地图 `[Basic] IgnoreGlobalAITriggers`（FA2「忽略全局AI」）的存档镜像。

定位（2026-11-03，玩家给的 A/B 对照档）：
  明文 **0x34B4** 的 u32 —— 0 = AI 触发生效，1 = 忽略（AI 触发不跑）。
证据链（A/B 两档前后各 64 字节完全一致、只差这一格）：
  · `AI触发打开.SAV`（改图**之前**存的）           = 0
  · `AI触发关闭.SAV`（改图后存，地图 =yes）        = 1
  · `sov01umd.map` =no 对应的 `SOV01UMD.sav`       = 0

自检内容：
  A. 三档读数 + 与地图 `[Basic]` 的交叉验证；
  B. 写入 → 落盘 → 重新打开，值必须还在（重编码不能丢）；
  C. 值不合法时**拒绝写入**（不能瞎写）。
"""
import os
import struct
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'core'))
from editor_core import SaveDoc      # noqa: E402
from map_ini import MapIni           # noqa: E402

ON = r'E:\YURI\AI触发打开.SAV'
OFF = r'E:\YURI\AI触发关闭.SAV'
SOV = r'E:\YURI\SOV01UMD.sav'
SOV_MAP = r'E:\YURI\sov01umd.map'
ALL_MAP = r'E:\YURI\all01umd.map'

OK = FAIL = 0


def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s' % msg)


def main():
    print('== A. 读数 + 地图交叉验证 ==')
    if not (os.path.exists(ON) and os.path.exists(OFF)):
        print('SKIP: 缺 A/B 样本')
        return 0
    a = SaveDoc(ON, write_cache=False)
    b = SaveDoc(OFF, write_cache=False)
    chk(a.ignore_global_ai() == 0, 'AI触发打开.SAV = %r（应 0）' % a.ignore_global_ai())
    chk(b.ignore_global_ai() == 1, 'AI触发关闭.SAV = %r（应 1）' % b.ignore_global_ai())
    if os.path.exists(SOV):
        s = SaveDoc(SOV, write_cache=False)
        m = MapIni(SOV_MAP).sections.get('basic') or {}
        want = 1 if str(m.get('IgnoreGlobalAITriggers', 'no')).lower() == 'yes' else 0
        chk(s.ignore_global_ai() == want,
            'SOV01UMD.sav=%r 与 sov01umd.map IgnoreGlobalAITriggers=%r 对应'
            % (s.ignore_global_ai(), m.get('IgnoreGlobalAITriggers')))
    if os.path.exists(ALL_MAP):
        m2 = MapIni(ALL_MAP).sections.get('basic') or {}
        chk(str(m2.get('IgnoreGlobalAITriggers', '')).lower() == 'yes',
            'all01umd.map 现为 yes ⇒ 与「AI触发关闭.SAV = 1」一致')

    print('== B. 写入 → 落盘 → 重开 ==')
    d = SaveDoc(ON)
    r = d.set_ignore_global_ai(True)
    chk(r['old'] == 0 and r['new'] == 1 and d.ignore_global_ai() == 1,
        '内存里切换 0 → 1（off=0x%X）' % r['off'])
    tmp = os.path.join(tempfile.gettempdir(), '_selftest_ignore_ai.SAV')
    d.save(tmp)
    d2 = SaveDoc(tmp, write_cache=False)
    chk(d2.ignore_global_ai() == 1, '重编码落盘后仍是 1')
    chk(d2.info.get('忽略全局AI触发') == '是（AI 触发不跑）',
        '概览字段 = %r' % d2.info.get('忽略全局AI触发'))
    d3 = SaveDoc(tmp, write_cache=False)
    d3.set_ignore_global_ai(False)
    chk(d3.ignore_global_ai() == 0, '再切回 0 也正常')
    try:
        os.remove(tmp)
    except Exception:
        pass

    print('== C. 定位存疑时拒绝写入 ==')
    d4 = SaveDoc(ON, write_cache=False)
    d4.plain[d4.IGNORE_AI_OFF:d4.IGNORE_AI_OFF + 4] = struct.pack('<I', 0x12345678)
    chk(d4.ignore_global_ai() is None, '非 0/1 值读作 None')
    try:
        d4.set_ignore_global_ai(True)
        chk(False, '应当拒绝写入，但写进去了')
    except RuntimeError as e:
        chk(True, '拒绝写入（%s）' % str(e)[:28])

    print('\n§SOV05 回归：PASS %d / FAIL %d' % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
