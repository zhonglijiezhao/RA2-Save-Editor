# -*- coding: utf-8 -*-
"""§265 核心自检：玩家专门做的「六支小队 = 六种心控结果」验证档 SAVE26E9.SAV。

玩家 2026-10-25 13:53：
> 「这个存档六个小队 对应六种心控结果」

档里 6 支测试小队（名字 AAAAA~FFFFF）逐一对到 0~5。
**这是"读法 + 名字 + 语义"三合一的验收档**：如果 `+0xD4` 认错、或档名对不上号，
下面第 [1] 段就会当场炸。

覆盖：
1. 六支小队读出来的 `+0xD4` 恰好是 0~5 各一个，且与队名一一对应（写死的期望表）；
2. 全档分布：非 0 的只有这 5 支测试队 + 官方尤里小队（语义自证仍然成立）；
3. 六支逐一改成"下一个值"（轮转）→ 落盘重读全对 ⇒ 6 个档位都能写；
4. 越界值 6 被拒。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc                                        # noqa: E402
import ai_objects as aio                                               # noqa: E402
from collections import Counter                                        # noqa: E402

SAV = r'E:\YURI\SAVE26E9.SAV'
OK = FAIL = 0

# 玩家做的六支：队名 → 期望值
EXPECT = {'AAAAA': 1, 'BBBBB': 2, 'CCCCC': 3, 'DDDDD': 4, 'EEEEE': 5, 'FFFFF': 0}


def chk(cond, msg, extra=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s   %s' % (msg, extra))


d = SaveDoc(SAV)
teams = aio.read_ai(d)['teams']
by_name = {t['name'].strip(): t for t in teams}

# ---------- 1) 六支逐一对照 ----------
print('[1] 六支测试小队 ↔ 六种心控结果')
hit = 0
for nm, want in EXPECT.items():
    t = by_name.get(nm)
    if t is None:
        chk(False, '档里找不到小队 %r' % nm, sorted(by_name)[:8])
        continue
    got = t.get('mc_decision', 0)
    chk(got == want, '%-6s +0xD4=%-2d → 「%s」' % (nm, got, aio.TEAM_MC_LABELS.get(got, '?')),
        '期望 %d，实际 %d' % (want, got))
    hit += 1
chk(hit == 6, '六支全部找到（%d/6）' % hit)

vals = sorted(by_name[n].get('mc_decision', 0) for n in EXPECT if n in by_name)
chk(vals == [0, 1, 2, 3, 4, 5], '★六支的取值 == {0,1,2,3,4,5} 各一个', vals)

# ---------- 1b) 标签逐字等于地编原文 ----------
print('\n[1b] 6 个标签逐字等于地编原文（放大图 QQ20261025-125905.png）')
WANT_LAB = ['随机', '加入小队', '放入部队回收厂', '放入生化反应炉', '寻敌', '什么也不做']
got_lab = [aio.TEAM_MC_LABELS[v] for v in range(6)]
chk(got_lab == WANT_LAB, '★6 项逐字一致', got_lab)
chk('解放' not in ''.join(got_lab), '★不再有任何"解放"字样（那是看错图）', got_lab)
chk('收入' not in ''.join(got_lab) and '进入' not in ''.join(got_lab),
    '2/3 的动词是「放入」，不是「收入/进入」', got_lab)
chk('到处找敌人打' in aio.TEAM_MC_NOTE, '提示语写明 4 = 到处找敌人打', aio.TEAM_MC_NOTE)

# ---------- 2) 全档分布 ----------
print('\n[2] 全档分布（语义自证）')
c = Counter(t.get('mc_decision', 0) for t in teams)
print('    小队 %d 支：%s' % (len(teams), dict(sorted(c.items()))))
chk(all(v in (0, 1, 2, 3, 4, 5) for v in c), '所有值都落在 0~5 之内', dict(c))
non0 = [(t['name'], t['mc_decision']) for t in teams if t.get('mc_decision')]
yuri = [x for x in non0 if 'yuri' in x[0].lower()]
print('    非 0 共 %d 支：%s' % (len(non0), non0))
chk(len(yuri) >= 7, '非 0 里绝大多数是尤里阵营的心控小队（%d/%d）'
    % (len(yuri), len(non0)), non0)

# ---------- 3) 六档轮转写入 ----------
print('\n[3] 六支轮转写一遍（每支改成下一个值）→ 落盘重读')
for nm, want in EXPECT.items():
    t = by_name[nm]
    nv = (want + 1) % 6
    r = aio.set_team_mc_decision(d, t['id'], nv)
    chk(r['ok'] and r['new'] == nv, '%-6s %d → %d' % (nm, want, nv), r)
out = os.path.join(HERE, '_tmp_ev265b.SAV')
d.save(out)
e = SaveDoc(out)
teams2 = {t['name'].strip(): t for t in aio.read_ai(e)['teams']}
bad = [(nm, teams2[nm].get('mc_decision'), (EXPECT[nm] + 1) % 6)
       for nm in EXPECT if teams2[nm].get('mc_decision') != (EXPECT[nm] + 1) % 6]
chk(not bad, '★落盘重读：六支全部变成"下一个值"', bad)
try:
    os.remove(out)
except OSError:
    pass

# ---------- 4) 越界被拒 ----------
print('\n[4] 越界值')
ok = 0
for v in (6, -1):
    try:
        aio.set_team_mc_decision(d, by_name['AAAAA']['id'], v)
    except ValueError:
        ok += 1
chk(ok == 2, '6 / -1 都被拒（%d/2）' % ok)

print('-' * 72)
print('§265 六档验收档：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
