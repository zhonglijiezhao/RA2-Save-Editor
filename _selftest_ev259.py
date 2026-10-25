# -*- coding: utf-8 -*-
"""§259 自检：作战小队「运输机起始」（TransportWaypoint）= i32 `+0xEC`。

定案证据（本脚本逐条锁住）：
1. 玩家给的 `SAVE4AE1.SAV` 里三支小队，地编设成 `98 / 0 / 99` ⇒ 存档 `+0xEC` 精确对应；
2. `+0xE8`（出现于 / Waypoint）与 `+0xEC` 是**两个独立字段** —— 老档里 125 支地图小队
   是 `+0xE8=0` 而 `+0xEC=-1`；
3. `-1` = 未设置；**`0` 是合法路径点**（`01000002` 就设成 0 号），所以判空只能用 -1；
4. 写 `+0xEC` 只动这 4 字节，不碰 `+0xE8`，也不动别的字段；落盘重读不变。
"""
import io
import os
import struct
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
import editor_core as ec                                     # noqa: E402
from _paths import find_sav                                  # noqa: E402

OK = FAIL = 0


def ck(cond, name, detail=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  PASS  %s' % name)
    else:
        FAIL += 1
        print('  FAIL  %s   %s' % (name, detail))


SAV = find_sav('SAVE4AE1.SAV')
OLD = find_sav('重存一次.SAV')
print('存档：%s' % SAV)

# ---------- A. 读：与地编截图逐条对齐 ----------
print('\n[A] 读（对照玩家截图：98 / 0 / 99）')
d = ec.SaveDoc(SAV)
pl = d.plain
CASES = [('01000002', 'AAAAA', 0), ('01000003', 'BBBBB', 98), ('01000004', 'CCCCC', 99)]
for sid, nm, exp in CASES:
    t = d.team_detail(sid)
    ck(t['transport_waypoint'] == exp,
       '%s「%s」运输机起始 = %d' % (sid, nm, exp), t['transport_waypoint'])
    ck(t['waypoint'] == exp,
       '%s「%s」出现于 = %d（两个键玩家设成同值）' % (sid, nm, exp), t['waypoint'])
    ck(t['flags'].get('Droppod') is True,
       '%s「%s」运输机空投已勾（这是前提）' % (sid, nm))

# ---------- B. 独立性：老档里两列不等 ----------
print('\n[B] 独立性证据（老档里 +0xE8 与 +0xEC 不相等）')
if os.path.isfile(OLD):
    o = ec.SaveDoc(OLD)
    op = o.plain
    neq = []
    for t in o.team_types():
        if not t.get('defined'):
            continue
        a = struct.unpack_from('<i', op, t['off'] + 0xE8)[0]
        b = struct.unpack_from('<i', op, t['off'] + 0xEC)[0]
        if a != b:
            neq.append((t['sid'], a, b))
    ck(len(neq) > 0, '老档里存在两列不等的车队（%d 支）' % len(neq), neq[:3])
    if neq:
        sid, a, b = neq[0]
        ck(a == 0 and b == -1, '典型形态：出现于=0 而 运输机起始=-1（未设置）',
           '%s → %s / %s' % (sid, a, b))
else:
    ck(True, '（跳过：老档 %s 不在）' % os.path.basename(OLD))

# ---------- C. 判空必须用 -1（0 是合法值）----------
print('\n[C] 0 与 -1 的语义')
t0 = d.team_detail('01000002')
ck(t0['transport_waypoint'] == 0, '0 号路径点是**合法值**（01000002 就是 0）',
   t0['transport_waypoint'])
ck('transport_waypoint' not in ec.SaveDoc.TEAM_UNKNOWN_FIELDS,
   '运输机起始已从"未定案"里移出')
ck(ec.SaveDoc.TEAM_FIELD_OFF['transport_waypoint'] == 0xEC,
   '偏移常量 = 0xEC', hex(ec.SaveDoc.TEAM_FIELD_OFF['transport_waypoint']))
for f in ('tech_level', 'para_drop_aircraft', 'ion_immune',
          'transports_return_on_unload'):
    ck(f in ec.SaveDoc.TEAM_UNKNOWN_FIELDS, '玩家裁定"不做"的 %s 仍挂起（不猜）' % f)
# ★§260：AreTeamMembersRecruitable 已定案（+0x109），不再挂起
ck('are_team_members_recruitable' not in ec.SaveDoc.TEAM_UNKNOWN_FIELDS,
   '「小队成员可被重组」已定案（§260 +0x109），不再挂起')

# ---------- D. 写：只动 +0xEC ----------
print('\n[D] 写入只影响 +0xEC')
off3 = next(t['off'] for t in d.team_types() if t['sid'] == '01000003')
before = bytes(d.plain[off3:off3 + 268])
d.set_team_field('01000003', 'transport_waypoint', 25)
after = bytes(d.plain[off3:off3 + 268])
diff = [i for i in range(268) if before[i] != after[i]]
ck(diff == [0xEC], '只改动了 +0xEC 这一个 dword 的起始字节', ['%#x' % x for x in diff[:6]])
t3 = d.team_detail('01000003')
ck(t3['transport_waypoint'] == 25, '写 25 生效', t3['transport_waypoint'])
ck(t3['waypoint'] == 98, '「出现于」没被带偏（仍 98）', t3['waypoint'])
ck(t3['veteran'] == 1 and t3['priority'] == 5 and t3['max'] == 5,
   '星级/优先级/最大 未受影响')

t4 = d.set_team_field('01000004', 'transport_waypoint', -1)
ck(t4['transport_waypoint'] == -1, '写 -1 = 未设置', t4['transport_waypoint'])
ck(t4['waypoint'] == 99, '「出现于」仍 99', t4['waypoint'])

d.set_team_field('01000002', 'transport_waypoint', 0)
ck(d.team_detail('01000002')['transport_waypoint'] == 0, '写 0 仍然合法（不被当成清空）')

# ---------- E. 落盘重读 ----------
print('\n[E] 落盘重读')
out = os.path.join(HERE, '_tmp_ev259.SAV')
d.save(out)
e = ec.SaveDoc(out)
got = {sid: e.team_detail(sid)['transport_waypoint'] for sid, _n, _x in CASES}
ck(got == {'01000002': 0, '01000003': 25, '01000004': -1},
   '三支的值落盘后不变', got)
wp = {sid: e.team_detail(sid)['waypoint'] for sid, _n, _x in CASES}
ck(wp == {'01000002': 0, '01000003': 98, '01000004': 99},
   '「出现于」落盘后也没变', wp)
try:
    os.remove(out)
except OSError:
    pass

print('\n' + '-' * 66)
print('§259 核心自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
