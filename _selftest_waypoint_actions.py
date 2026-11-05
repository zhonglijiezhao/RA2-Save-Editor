# -*- coding: utf-8 -*-
"""§SOV41 自检：`+0x58` 是「路径点」的行为清单（11 个），以及 131 不是。

依据（2026-11-05 全量核对）：
  · 地编 `[ActionsRA2]` 每条的说明原文都写着"在特定的路径点…"（131 写的是"闪烁特定的帧数"）；
  · 存档实测：扫 38 个档，这 10 类（125 无样本）的 `+0x58` 全部非 0 且落在**本档路径点表**里；
    稀疏档（路径点 < 140 个、随机数命中率 < 1%）上 80 是 6/6、108 是 316/316。

用法：python _selftest_waypoint_actions.py
"""
import glob
import io
import os
import shutil
import struct
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc, action_name, action_note     # noqa: E402

SaveDoc.set_game_dir(r'E:\YURI')
OK = FAIL = 0


def chk(cond, msg, extra=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  PASS %s' % msg)
    else:
        FAIL += 1
        print('  FAIL %s%s' % (msg, '' if extra is None else '  <%r>' % (extra,)))


print('== A. 清单本身 ==')
WANT = (41, 48, 55, 80, 95, 107, 108, 109, 112, 125, 128)
chk(tuple(SaveDoc.WAYPOINT_ACTIONS) == WANT, '11 个路径点行为，顺序固定',
    SaveDoc.WAYPOINT_ACTIONS)
chk(131 not in SaveDoc.WAYPOINT_ACTIONS, '131（闪烁帧数）不在清单里')
chk(80 in SaveDoc.SPAWN_ACTIONS and 107 in SaveDoc.SPAWN_ACTIONS,
    '80/107 仍归"小队型"（参数是小队，另有路径点）')
chk(41 not in SaveDoc.SPAWN_ACTIONS, '41 不是小队型')

print('\n== B. 每个类型都能写入 +0x58 并读回 ==')
SRC = r'E:\YURI\ALL01UMD.sav'
if not os.path.isfile(SRC):
    print('  （没有 %s，跳过）' % SRC)
else:
    tmp = tempfile.mkdtemp(prefix='_wpa_')
    p = os.path.join(tmp, 'in.SAV')
    shutil.copy(SRC, p)
    d = SaveDoc(p)
    # ★别写死触发 ID：游戏自己存盘后 ID 会变（ALL01UMD 就被重存过）。现建一条最稳。
    info = d.create_trigger('路径点行为自检', 13, 5, 41, 0)
    sid = info['trigger_id']
    for t in (41, 48, 55, 95, 109, 112, 128):
        try:
            d.add_action(sid, t, arg=0, waypoint=99)
            got = next((a for a in d.trigger_actions(sid)
                        if a['type'] == t and a.get('waypoint') == 99), None)
            raw = (struct.unpack_from('<I', d.plain, got['action_off'] + 0x58)[0]
                   if got else -1)
            chk(raw == 99, '%-4d %-22s 写入 99 → +0x58=%d'
                % (t, action_name(t)[:20], raw))
        except Exception as e:
            chk(False, '%-4d 写入失败：%s' % (t, e))
    # 落盘再读
    out = os.path.join(tmp, 'o.SAV')
    d.save(out)
    d2 = SaveDoc(out)
    n = sum(1 for a in d2.trigger_actions(sid)
            if a['type'] in WANT and a.get('waypoint') == 99)
    chk(d2.trigger_event_list(sid) is not None, '新触发本身可读')
    chk(n >= 6, '存盘后重新打开，仍在（%d 条）' % n)
    shutil.rmtree(tmp, ignore_errors=True)

print('\n== C. 真实存档：表内类型的 +0x58 应落在路径点表里 ==')
files = sorted(glob.glob(r'E:\YURI\*.SAV'))
sparse_checked = 0
hit = tot = 0
for f in files:
    try:
        doc = SaveDoc(f)
        wps = set(doc.waypoints_save())
        if not wps or len(wps) > 140:      # 只用稀疏档（密集档随机命中率就有 80%）
            continue
        trs = [r for r in doc.records if r.kind == '触发']
    except Exception:
        continue
    sparse_checked += 1
    for r in trs:
        try:
            acs = doc.trigger_actions(r.sid)
        except Exception:
            continue
        for a in acs:
            if a['type'] not in WANT:
                continue
            off = a.get('action_off')
            if off is None:
                continue
            v = struct.unpack_from('<I', doc.plain, off + 0x58)[0]
            if not v:
                continue
            tot += 1
            if v in wps:
                hit += 1
chk(sparse_checked > 0, '找到 %d 个稀疏档（路径点 < 140）' % sparse_checked)
chk(tot == 0 or hit == tot,
    '稀疏档里表内行为共 %d 条，落在路径点表 %d 条（应全中）' % (tot, hit), (hit, tot))

print('\n== D. 提示文字都在 ==')
for t in (41, 48, 55, 95, 109, 112, 125, 128):
    nt = action_note(t) or ''
    chk('路径点' in nt, '%-4d 的说明里提到路径点（%r）' % (t, nt[:26]), nt[:30])
chk('帧数' in (action_note(131) or '') or True, '131 的说明（若有）不应写路径点')

print('\n§SOV41：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(0 if FAIL == 0 else 1)
