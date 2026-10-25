# -*- coding: utf-8 -*-
"""§260 自检：地编「小队成员可被重组」= `AreTeamMembersRecruitable` = u8 `+0x109`。

玩家 2026-10-25 10:50 给的对照：
- `E:/YURI/SAVE4AE1.SAV` —— 三支小队**都没勾**
- `E:/YURI/SAVE5F90.SAV` —— **只有 AAAAA（01000002）勾了**

本脚本锁住三条证据：
1. 两档对同一小队做 268B 逐字节 diff ⇒ **只有 `+0x109` 是"仅在 AAAAA 上变化"**；
2. 字节级值向量扫描（目标 `[1,0,0]`）⇒ 268 个偏移里**唯一命中 `+0x109`**；
3. 写入只改这 1 个字节，落盘重读一致。
"""
import io
import os
import sys
import collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
import editor_core as ec                                     # noqa: E402
import ai_objects as aio                                     # noqa: E402
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


FLD = 'AreTeamMembersRecruitable'
A = ec.SaveDoc(find_sav('SAVE4AE1.SAV'))      # 三支都没勾
B = ec.SaveDoc(find_sav('SAVE5F90.SAV'))      # 只有 AAAAA 勾了
SIDS = [('01000002', 'AAAAA'), ('01000003', 'BBBBB'), ('01000004', 'CCCCC')]


def off_of(doc, sid):
    return next(t['off'] for t in doc.team_types() if t['sid'] == sid)


# ---------- A. 常量登记 ----------
print('\n[A] 常量登记')
ck(aio.TEAM_FLAGS.get(0x109) == FLD, 'TEAM_FLAGS[0x109] = %s' % FLD,
   aio.TEAM_FLAGS.get(0x109))
ck('are_team_members_recruitable' not in ec.SaveDoc.TEAM_UNKNOWN_FIELDS,
   '已从未定案表移出')
ck(0x106 in aio.TEAM_FLAGS and aio.TEAM_FLAGS[0x106] == 'AvoidThreats',
   '相邻的 +0x106 仍是 AvoidThreats（没撞位）')

# ---------- B. 两档 diff：只有 +0x109 是"仅 AAAAA 变" ----------
print('\n[B] 两档 A/B 逐字节 diff（三支小队）')
only_a = []
for sid, nm in SIDS:
    oa, ob = off_of(A, sid), off_of(B, sid)
    da = bytes(A.plain[oa:oa + 268])
    db = bytes(B.plain[ob:ob + 268])
    diffs = [i for i in range(268) if da[i] != db[i]]
    print('    %s「%s」差异字节 %d 个' % (sid, nm, len(diffs)))
    if sid == '01000002':
        only_a = diffs
    else:
        # 另两支**不该**在 +0x109 上变
        ck(0x109 not in diffs, '%s「%s」的 +0x109 没有变化（0→0）' % (sid, nm),
           ['%#x' % i for i in diffs if i == 0x109])

# 所有差异里，排除"每支都会变的键值/运行期字段"，剩下的必须正好是 +0x109
# 键值：+0x10 自键、+0xD8 国家键、+0xF4 脚本键、+0xF8 特遣键
KEYISH = set(range(0x10, 0x14)) | set(range(0xD8, 0xDC)) | \
    set(range(0xF4, 0xFC))
rest = [i for i in only_a if i not in KEYISH]
ck(0x109 in rest, '★AAAAA 的差异里包含 +0x109', ['%#x' % x for x in rest])
ck(rest == [0x109], '★排除键值后，AAAAA 的差异**只剩 +0x109**',
   ['%#x' % x for x in rest])
oa, ob = off_of(A, '01000002'), off_of(B, '01000002')
ck(A.plain[oa + 0x109] == 0 and B.plain[ob + 0x109] == 1,
   '★+0x109 从 0（未勾）变成 1（勾了）',
   (A.plain[oa + 0x109], B.plain[ob + 0x109]))

# ---------- C. 值向量扫描：唯一命中 ----------
print('\n[C] 字节级值向量扫描（目标 [1,0,0]）')
pl = B.plain
o2, o3, o4 = (off_of(B, s) for s, _n in SIDS)
hits = [off for off in range(268)
        if pl[o2 + off] == 1 and pl[o3 + off] == 0 and pl[o4 + off] == 0]
ck(hits == [0x109], '★268 个偏移里唯一命中 +0x109', ['%#x' % x for x in hits[:8]])

dist = collections.Counter(pl[t['off'] + 0x109] for t in B.team_types())
ck(set(dist) == {0, 1}, '取值只有 0/1 两值（真布尔）', dict(dist))
ck(dist[1] > 0 and dist[0] > 0, '两值都存在（%d 支勾 / %d 支没勾）' % (dist[1], dist[0]))

# ---------- D. 读：与玩家描述一致 ----------
print('\n[D] 读（玩家说：只有 AAAAA 勾了）')
for sid, nm in SIDS:
    want = (sid == '01000002')
    got = B.team_detail(sid)['flags'].get(FLD)
    ck(got is want, '%s「%s」= %s' % (sid, nm, want), got)

# ---------- E. 写：只动 +0x109 一个字节 ----------
print('\n[E] 写入只影响 +0x109')
d = ec.SaveDoc(find_sav('SAVE5F90.SAV'))
off_a = off_of(d, '01000002')
before = bytes(d.plain[off_a:off_a + 268])
d.set_team_field('01000002', FLD, False)
after = bytes(d.plain[off_a:off_a + 268])
diff = [i for i in range(268) if before[i] != after[i]]
ck(diff == [0x109], '取消勾选只改 +0x109 这一个字节', ['%#x' % x for x in diff[:6]])
ck(d.team_detail('01000002')['flags'][FLD] is False, 'AAAAA 已取消', )

d.set_team_field('01000003', FLD, True)
ck(d.team_detail('01000003')['flags'][FLD] is True, 'BBBBB 已勾上')
ck(d.team_detail('01000004')['flags'][FLD] is False, 'CCCCC 保持未勾')

# 顺带确认没把相邻标志写坏
t3 = d.team_detail('01000003')
ck(t3['flags']['Droppod'] is True and t3['flags']['Loadable'] is False,
   '相邻标志 Droppod/Loadable 未受影响',
   (t3['flags']['Droppod'], t3['flags']['Loadable']))

# ---------- F. 落盘重读 ----------
print('\n[F] 落盘重读')
out = os.path.join(HERE, '_tmp_ev260.SAV')
d.save(out)
e = ec.SaveDoc(out)
got = {sid: e.team_detail(sid)['flags'][FLD] for sid, _n in SIDS}
ck(got == {'01000002': False, '01000003': True, '01000004': False},
   '三支落盘后取值正确', got)
try:
    os.remove(out)
except OSError:
    pass

print('\n' + '-' * 66)
print('§260 核心自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
