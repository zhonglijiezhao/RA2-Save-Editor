# -*- coding: utf-8 -*-
"""§261 自检：地编「用于基地防御」= `IsBaseDefense` = u8 `+0x10A`。

玩家给了**三档**对照（都是同一张三支测试小队的地图）：
| 档 | AAAAA 勾了什么 |
|---|---|
| `SAVE4AE1.SAV` | 都没勾 |
| `SAVE5F90.SAV` | 只勾「小队成员可被重组」 |
| `SAVE1649.SAV` | 只勾「用于基地防御」（并取消了可重组） |

本脚本锁住四条证据：
1. **三档正交表**：`+0x109` = (0,1,0)、`+0x10A` = (0,0,1) —— 各自只响应自己的勾选；
2. 字节级值向量 `[1,0,0]` 扫 268 偏移，**唯一命中 `+0x10A`**；
3. ★**语义自证**：全档 13 支为 1 里 **12 支是 AI 的基地防御类小队**（名字含 Base/Defense/Guard）；
4. 写入只改这 1 个字节，`+0x109` 不受影响，落盘重读一致。
"""
import io
import os
import re
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


FLD = 'IsBaseDefense'
REC = 'AreTeamMembersRecruitable'
SIDS = [('01000002', 'AAAAA'), ('01000003', 'BBBBB'), ('01000004', 'CCCCC')]

# ---------- A. 常量登记 ----------
print('\n[A] 常量登记')
ck(aio.TEAM_FLAGS.get(0x10A) == FLD, 'TEAM_FLAGS[0x10A] = %s' % FLD,
   aio.TEAM_FLAGS.get(0x10A))
ck(aio.TEAM_FLAGS.get(0x109) == REC, 'TEAM_FLAGS[0x109] 仍是可重组（没被顶掉）',
   aio.TEAM_FLAGS.get(0x109))
ck('is_base_defense' not in ec.SaveDoc.TEAM_UNKNOWN_FIELDS, '已从未定案表移出')
ck('are_team_members_recruitable' not in ec.SaveDoc.TEAM_UNKNOWN_FIELDS,
   '可重组也仍不在未定案表')

# ---------- B. 三档正交表 ----------
print('\n[B] 三档正交表（同一支 AAAAA，三次不同勾选）')
docs = {}
for tag, fn in (('4AE1', 'SAVE4AE1.SAV'), ('5F90', 'SAVE5F90.SAV'), ('1649', 'SAVE1649.SAV')):
    d = ec.SaveDoc(find_sav(fn))
    off = next(t['off'] for t in d.team_types() if t['sid'] == '01000002')
    docs[tag] = (d, off)

table = {}
for tag, (d, off) in docs.items():
    table[tag] = (d.plain[off + 0x109], d.plain[off + 0x10A])
    print('    %s  AAAAA: +0x109=%%d  +0x10A=%%d' % tag % table[tag])

ck([table[t][0] for t in ('4AE1', '5F90', '1649')] == [0, 1, 0],
   '★+0x109 三档 = (0,1,0)：只跟「可重组」走',
   [table[t][0] for t in ('4AE1', '5F90', '1649')])
ck([table[t][1] for t in ('4AE1', '5F90', '1649')] == [0, 0, 1],
   '★+0x10A 三档 = (0,0,1)：只跟「基地防御」走',
   [table[t][1] for t in ('4AE1', '5F90', '1649')])
ck(table['4AE1'] == (0, 0), '基础档（都没勾）两位全 0')

# 另两支在任何一档都不该被带偏
for tag, (d, off) in docs.items():
    for sid, nm in SIDS[1:]:
        o = next(t['off'] for t in d.team_types() if t['sid'] == sid)
        ck(d.plain[o + 0x10A] == 0, '%s 档的 %s「%s」+0x10A 仍为 0' % (tag, sid, nm))

# ---------- C. 值向量扫描：唯一命中 ----------
print('\n[C] 字节级值向量扫描（SAVE1649，目标 [1,0,0]）')
d = docs['1649'][0]
pl = d.plain
offs = [next(t['off'] for t in d.team_types() if t['sid'] == s) for s, _n in SIDS]
hits = [off for off in range(268)
        if pl[offs[0] + off] == 1 and pl[offs[1] + off] == 0 and pl[offs[2] + off] == 0]
ck(hits == [0x10A], '★268 个偏移里唯一命中 +0x10A', ['%#x' % x for x in hits[:8]])

dist = collections.Counter(pl[t['off'] + 0x10A] for t in d.team_types())
ck(set(dist) == {0, 1}, '取值只有 0/1（真布尔）', dict(dist))

# ---------- D. ★语义自证 ----------
print('\n[D] 语义自证：+0x10A=1 的都是什么小队')
on = [(t['sid'], t['name']) for t in d.team_types() if pl[t['off'] + 0x10A]]
print('    共 %d 支' % len(on))
for sid, nm in on:
    print('      %s  %s' % (sid, nm))
pat = re.compile(r'base|defen[cs]e|guard', re.I)
sem = [nm for _s, nm in on if pat.search(nm)]
ck(len(on) >= 5, '数量足够做判断（%d 支）' % len(on))
ck(len(sem) >= 10,
   '★至少 10 支名字含 Base/Defense/Guard（实际 %d/%d）' % (len(sem), len(on)), sem)
ck(any(nm == 'AAAAA' for _s, nm in on), '玩家勾的那支 AAAAA 在里面')

# ---------- D2. 第二份独立存档复现（不同局、键全不同，结论必须一致） ----------
print('\n[D2] 第二份独立存档复现（SAVE6DF1：同样只有 AAAAA 勾「基地防御」）')
p2 = find_sav('SAVE6DF1.SAV')
if os.path.isfile(p2):
    n2 = ec.SaveDoc(p2)
    npl = n2.plain
    nrec = {t['sid']: t for t in n2.team_types()}
    got2 = {sid: n2.team_detail(sid)['flags'].get(FLD) for sid, _n in SIDS}
    ck(got2 == {'01000002': True, '01000003': False, '01000004': False},
       '★读出与玩家描述一致（只有 AAAAA 勾）', got2)

    o2n, o3n, o4n = (nrec[s]['off'] for s, _n in SIDS)
    hits2 = [off for off in range(268)
             if npl[o2n + off] == 1 and npl[o3n + off] == 0 and npl[o4n + off] == 0]
    ck(hits2 == [0x10A], '★第二份档里值向量仍**唯一命中** +0x10A',
       ['%#x' % x for x in hits2[:8]])

    same = all(
        [npl[nrec[s]['off'] + 0x109], npl[nrec[s]['offset'] if False else nrec[s]['off'] + 0x10A]]
        == [docs['1649'][0].plain[
                next(t['off'] for t in docs['1649'][0].team_types() if t['sid'] == s) + 0x109],
            docs['1649'][0].plain[
                next(t['off'] for t in docs['1649'][0].team_types() if t['sid'] == s) + 0x10A]]
        for s, _n in SIDS)
    ck(same, '★两份档的 (+0x109, +0x10A) 三支全一致（换局换键，结论不变）')
else:
    ck(True, '（跳过：SAVE6DF1.SAV 不在）')

# ---------- E. 读：与玩家描述一致 ----------
print('\n[E] 读（玩家：AAAAA 勾了用于基地防御，另两支未改）')
for sid, nm in SIDS:
    want = (sid == '01000002')
    got = d.team_detail(sid)['flags'].get(FLD)
    ck(got is want, '%s「%s」基地防御 = %s' % (sid, nm, want), got)
    ck(d.team_detail(sid)['flags'].get(REC) is False,
       '%s「%s」可重组 = False（玩家已取消）' % (sid, nm))

# ---------- F. 写：只动 +0x10A ----------
print('\n[F] 写入只影响 +0x10A')
w = ec.SaveDoc(find_sav('SAVE1649.SAV'))
off_a = next(t['off'] for t in w.team_types() if t['sid'] == '01000002')
before = bytes(w.plain[off_a:off_a + 268])
w.set_team_field('01000002', FLD, False)
after = bytes(w.plain[off_a:off_a + 268])
diff = [i for i in range(268) if before[i] != after[i]]
ck(diff == [0x10A], '取消勾选只改 +0x10A 这一个字节', ['%#x' % x for x in diff[:6]])
ck(w.team_detail('01000002')['flags'][FLD] is False, 'AAAAA 已取消')
ck(w.team_detail('01000002')['flags'][REC] is False, '可重组没被带偏')

w.set_team_field('01000004', FLD, True)
t4 = w.team_detail('01000004')['flags']
ck(t4[FLD] is True and t4[REC] is False, 'CCCCC 勾上基地防御，可重组仍 False',
   (t4[FLD], t4[REC]))
ck(t4['Droppod'] is True and t4['Loadable'] is False, '相邻 Droppod/Loadable 未受影响')

# ---------- G. 落盘重读 ----------
print('\n[G] 落盘重读')
out = os.path.join(HERE, '_tmp_ev261.SAV')
w.save(out)
e = ec.SaveDoc(out)
got = {sid: (e.team_detail(sid)['flags'][FLD], e.team_detail(sid)['flags'][REC])
       for sid, _n in SIDS}
ck(got == {'01000002': (False, False), '01000003': (False, False),
           '01000004': (True, False)}, '三支落盘后取值正确', got)
try:
    os.remove(out)
except OSError:
    pass

print('\n' + '-' * 66)
print('§261 核心自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
