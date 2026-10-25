# -*- coding: utf-8 -*-
"""§263 自检：地编「仅攻击敌方或敌对」= `OnlyTargetHouseEnemy` = u8 `+0x10B`。

玩家给了**四档**对照（同一张三支测试小队的地图，每档只让一支小队翻一个勾）：

| 档 | AAAAA 勾了什么 | `+0x109` | `+0x10A` | `+0x10B` |
|---|---|---|---|---|
| `SAVE4AE1` | 都没勾 | 0 | 0 | 0 |
| `SAVE5F90` | 小队成员可被重组 | 1 | 0 | 0 |
| `SAVE1649` | 用于基地防御 | 0 | 1 | 0 |
| `SAVE5AF1` | 仅攻击敌方或敌对 | 0 | 0 | 1 |

本脚本锁住：
1. **四档正交表**（三列各自只跟自己的勾选走）；
2. 字节级值向量 `[1,0,0]` 扫 268 偏移，**唯一命中 `+0x10B`**；
3. 该位全档 167 支里**只有 1 支为 1**（符合"AI 小队默认不设"）；
4. 写入只改这 1 个字节，另两列不受影响，落盘重读一致。
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


FLD = 'OnlyTargetHouseEnemy'
REC = 'AreTeamMembersRecruitable'
DEF = 'IsBaseDefense'
SID = '01000002'          # 玩家每次都在这一支上翻勾

# ---------- A. 常量登记 ----------
print('\n[A] 常量登记')
ck(aio.TEAM_FLAGS.get(0x10B) == FLD, 'TEAM_FLAGS[0x10B] = %s' % FLD,
   aio.TEAM_FLAGS.get(0x10B))
ck(aio.TEAM_FLAGS.get(0x109) == REC and aio.TEAM_FLAGS.get(0x10A) == DEF,
   '相邻的 +0x109/+0x10A 没被顶掉')
ck('only_target_house_enemy' not in ec.SaveDoc.TEAM_UNKNOWN_FIELDS,
   '已从未定案表移出')
ck(aio.TEAM_FLAG_LABELS.get(FLD) == '仅攻击敌方或敌对', '中文显示名已登记',
   aio.TEAM_FLAG_LABELS.get(FLD))
ck(len(aio.TEAM_FLAG_LABELS) == len(aio.TEAM_FLAGS),
   '19 个标志都有中文名（%d/%d）' % (len(aio.TEAM_FLAG_LABELS), len(aio.TEAM_FLAGS)))

# ---------- B. 四档正交表 ----------
print('\n[B] 四档正交表（同一支 AAAAA，四次不同勾选）')
CASES = [('4AE1', 'SAVE4AE1.SAV'), ('5F90', 'SAVE5F90.SAV'),
         ('1649', 'SAVE1649.SAV'), ('5AF1', 'SAVE5AF1.SAV')]
docs = {}
table = {}
for tag, fn in CASES:
    d = ec.SaveDoc(find_sav(fn))
    off = next(t['off'] for t in d.team_types() if t['sid'] == SID)
    docs[tag] = (d, off)
    table[tag] = (d.plain[off + 0x109], d.plain[off + 0x10A], d.plain[off + 0x10B])
    print('    %s  (+0x109, +0x10A, +0x10B) = %s' % (tag, table[tag]))

cols = {0x109: [table[t][0] for t, _f in CASES],
        0x10A: [table[t][1] for t, _f in CASES],
        0x10B: [table[t][2] for t, _f in CASES]}
ck(cols[0x109] == [0, 1, 0, 0], '★+0x109 四档 = (0,1,0,0)：只跟「可重组」走', cols[0x109])
ck(cols[0x10A] == [0, 0, 1, 0], '★+0x10A 四档 = (0,0,1,0)：只跟「基地防御」走', cols[0x10A])
ck(cols[0x10B] == [0, 0, 0, 1], '★+0x10B 四档 = (0,0,0,1)：只跟「仅攻击敌对」走', cols[0x10B])
ck(table['4AE1'] == (0, 0, 0), '基础档（都没勾）三位全 0')

# ---------- C. 值向量唯一命中 ----------
print('\n[C] 值向量扫描（SAVE5AF1，目标 [1,0,0]）')
d = docs['5AF1'][0]
pl = d.plain
offs = [next(t['off'] for t in d.team_types() if t['sid'] == s)
        for s in ('01000002', '01000003', '01000004')]
hits = [off for off in range(268)
        if pl[offs[0] + off] == 1 and pl[offs[1] + off] == 0 and pl[offs[2] + off] == 0]
ck(hits == [0x10B], '★268 个偏移里唯一命中 +0x10B', ['%#x' % x for x in hits[:8]])

dist = collections.Counter(pl[t['off'] + 0x10B] for t in d.team_types())
ck(set(dist) == {0, 1}, '取值只有 0/1（真布尔）', dict(dist))
ck(dist[1] == 1, '★全档 167 支里只有这一支为 1（AI 默认不设）', dict(dist))
on = [(t['sid'], t['name']) for t in d.team_types() if pl[t['off'] + 0x10B]]
ck(len(on) == 1 and on[0][0] == SID, '唯一为 1 的就是玩家勾的 AAAAA', on)

# ---------- D. 读：与玩家描述一致 ----------
print('\n[D] 读（玩家：AAAAA 勾选 仅攻击敌方或敌对）')
for sid, nm, want in (('01000002', 'AAAAA', True), ('01000003', 'BBBBB', False),
                      ('01000004', 'CCCCC', False)):
    got = d.team_detail(sid)['flags'].get(FLD)
    ck(got is want, '%s「%s」= %s' % (sid, nm, want), got)
    ck(d.team_detail(sid)['flags'].get(DEF) is False,
       '%s「%s」基地防御 = False（玩家已取消）' % (sid, nm))

# ---------- E. 写：只动 +0x10B ----------
print('\n[E] 写入只影响 +0x10B')
w = ec.SaveDoc(find_sav('SAVE5AF1.SAV'))
o_a = next(t['off'] for t in w.team_types() if t['sid'] == SID)
before = bytes(w.plain[o_a:o_a + 268])
w.set_team_field(SID, FLD, False)
after = bytes(w.plain[o_a:o_a + 268])
diff = [i for i in range(268) if before[i] != after[i]]
ck(diff == [0x10B], '取消勾选只改 +0x10B 这一个字节', ['%#x' % x for x in diff[:6]])
ck(w.team_detail(SID)['flags'][FLD] is False, 'AAAAA 已取消')
ck(w.team_detail(SID)['flags'][DEF] is False
   and w.team_detail(SID)['flags'][REC] is False, '另两列没被带偏')

w.set_team_field('01000004', FLD, True)
t4 = w.team_detail('01000004')['flags']
ck(t4[FLD] is True and t4[DEF] is False and t4[REC] is False,
   'CCCCC 勾上，另两列仍 False', (t4[FLD], t4[DEF], t4[REC]))

# ---------- F. 落盘重读 ----------
print('\n[F] 落盘重读')
out = os.path.join(HERE, '_tmp_ev263.SAV')
w.save(out)
e = ec.SaveDoc(out)
got = {sid: (e.team_detail(sid)['flags'][FLD], e.team_detail(sid)['flags'][DEF],
             e.team_detail(sid)['flags'][REC])
       for sid, _n in (('01000002', ''), ('01000003', ''), ('01000004', ''))}
ck(got == {'01000002': (False, False, False), '01000003': (False, False, False),
           '01000004': (True, False, False)}, '三支落盘后取值正确', got)
try:
    os.remove(out)
except OSError:
    pass

print('\n' + '-' * 66)
print('§263 核心自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
