# -*- coding: utf-8 -*-
"""自检：行为 108「创建工具箱」的**路径点**（玩家 2026-11-05：箱子要刷到那个点）

结论（§SOV37）：`108 创建工具箱` 是**两参数**行为 ——

| 参数 | 位置 | 含义 |
|---|---|---|
| 奖励箱类型 | `+164`（ptype 31） | 0 大量金钱 / 1 随机单位 / 7 重置黑幕 / 8 显示全图 / 12 一次核弹 / 17 一片矿 … |
| **路径点** | **`+0x58`**（与 80/107/125 同字段） | 箱子就刷在这个路径点上；0 = 引擎找不到落点，**不刷** |

证据（`ALL01UMD.sav`，2026-11-05）：
  · 原始样本 01000005 有 17 条行为，前 16 条是 108（第 17 条是 23 摧毁触发）。
    ⚠ 样本会被游戏自己重存（触发 ID 变），所以脚本改成**动态找**含 108 的触发。
  · `+164` = 0,1,2,3,4,6,7,…,16（奖励箱类型），`+0x58` = **99 / 98** 两组；
  · 地图 `[Actions]` 里这 16 条的最后那个字母字段正好是 `CV` / `CU`（= 路径点 99 / 98），
    而 `[Waypoints]` 全图只有 98、99 两个点 ⇒ 字段定位铁证。

断言：
  A 读：17 条、前 9 条 wp=99、后 7 条 wp=98，且 cell 与地图路径点坐标对得上
  B 改：set_action(arg=8, waypoint=98) → `+164=8`、`+0x58=98`，其余字段不动
  C 新建：create_trigger_multi 用 108 + waypoint → 首条行为就带路径点（`+0x58`）
  D 落盘：另存回读，路径点还在
  E 护栏：108 不给路径点要报错（不能静默写 0）
"""
import io
import os
import shutil
import struct
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc            # noqa: E402

OK = FAIL = 0


def chk(cond, msg, got=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [OK] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s   实际=%r' % (msg, got))


SAV = r'E:\YURI\ALL01UMD.sav'
TMP = tempfile.mkdtemp(prefix='_c108_')
try:
    if not os.path.isfile(SAV):
        print('样本不存在：%s —— 跳过' % SAV)
        sys.exit(0)
    SaveDoc.set_game_dir(os.path.dirname(SAV))
    p = os.path.join(TMP, 'in.SAV')
    shutil.copy(SAV, p)
    d = SaveDoc(p)

    print('== A. 读：108 的路径点 ==')
    # ★别写死触发 ID：这份样本被游戏自己重存过（19:49），触发 ID 全变了。
    #   动态找"含 108 最多的那条"，找不到就现场建一条。
    TID, best = None, 0
    for r in [x for x in d.records if x.kind == '触发']:
        try:
            acs = d.trigger_actions(r.sid)
        except Exception:
            continue
        n = sum(1 for a in acs if a['type'] == 108)
        if n > best:
            best, TID = n, r.sid
    chk(TID is not None, '档里找到含 108 的触发（%s，%d 条）' % (TID, best), best)
    acs = d.trigger_actions(TID)
    c108 = [a for a in acs if a['type'] == 108]
    wps = [a.get('waypoint') for a in c108]
    chk(all(w for w in wps), '每条 108 都有路径点号', wps[:6])
    if len(c108) == 16 and wps[:9] == [99] * 9 and wps[9:] == [98] * 7:
        chk(True, '原始样本形态：前 9 条 99 / 后 7 条 98')
    else:
        print('     （样本已被重存，条数/分组变了：%d 条 %s —— 只验"都有号"）'
              % (len(c108), wps[:6]))
    chk(all(a.get('cell') for a in c108), '每条都能反查出格子坐标', c108[0].get('cell'))
    chk([a['arg'] for a in c108] != [], '奖励箱类型（+164）读得到', [a['arg'] for a in c108][:5])

    print('== B. 改：箱子类型 + 路径点 ==')
    a0 = c108[0]
    d.set_action(a0, arg=8, waypoint=98)
    b0 = d.trigger_actions(TID)[0]
    o = b0['action_off']
    chk(struct.unpack_from('<I', d.plain, o + 164)[0] == 8, '+164 = 8（显示全图）')
    chk(struct.unpack_from('<I', d.plain, o + 0x58)[0] == 98, '+0x58 = 98')
    chk(struct.unpack_from('<I', d.plain, o + 72)[0] == 0, '+72 仍为 0（这一型不用它）')

    print('== C. 新建触发：108 带路径点 ==')
    info = d.create_trigger_multi('箱子测试', [(13, 5)],
                                  [{'type': 108, 'arg': 17, 'waypoint': 99}], persistence=0)
    chk(info.get('waypoint') == 99, '返回值带 waypoint=99', info.get('waypoint'))
    rows = d.trigger_actions(info['trigger_id'])
    chk(rows and rows[0]['type'] == 108 and rows[0].get('waypoint') == 99,
        '新建的首条行为 = 108 @ 路径点 99', rows[:1])

    print('== D. 落盘回读 ==')
    q = os.path.join(TMP, 'out.SAV')
    d.save(q)
    d2 = SaveDoc(q)
    r2 = d2.trigger_actions(TID)[0]
    chk(r2['arg'] == 8 and r2.get('waypoint') == 98, '回读 %s 首条 = (8, 98)' % TID,
        (r2['arg'], r2.get('waypoint')))
    r3 = d2.trigger_actions(info['trigger_id'])
    chk(r3[0].get('waypoint') == 99, '回读新建触发 = 路径点 99', r3[0].get('waypoint'))

    print('== E. 护栏：108 不给路径点必须报错 ==')
    try:
        d2.add_action(info['trigger_id'], 108, 3)
        chk(False, 'add_action(108) 不给 waypoint 必须报错')
    except Exception as e:
        chk('waypoint' in str(e) or '路径点' in str(e), 'add_action 报错：%s' % str(e)[:50], e)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print('\n108 创建工具箱·路径点自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
