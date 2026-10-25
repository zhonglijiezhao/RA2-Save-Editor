# -*- coding: utf-8 -*-
"""§235 自检：小队 16 个布尔标志 + 「心控重建」(MindControlDecision)。

证据链（两条独立硬证据，互相印证）：
  ① 引擎反汇编 `0x6F10E0–0x6F13C0`：每个 `mov al,[esi+runtime]` 紧接
     `push "<INI 键>"` ⇒ 键 ↔ 运行期偏移（+0x14 = 存档偏移）。
  ② ALL01UMD.MAP 的 **125 条真小队**逐条对账 `yes/no` 向量。
本自检锁死 ②（离线可复现）+ 读写单字节语义。
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
import editor_core as ec                                    # noqa: E402
import ai_objects                                           # noqa: E402
from _paths import find_sav, out                            # noqa: E402

SAV = find_sav('T85_老工程师_修位置副本槽.SAV')
MAPS = ['C:/Users/zhao5/Desktop/ALL01UMD.MAP', 'E:/YURI/ALL01UMD.MAP',
        'C:/Users/zhao5/Desktop/WorkBuddy/_binghe_extract/all01umd.map']
MAP = next((m for m in MAPS if os.path.exists(m)), None)

n_ok = n_bad = 0


def ck(name, cond, extra=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('  PASS  %s' % name)
    else:
        n_bad += 1
        print('  FAIL  %s   %s' % (name, extra))


print('存档 %s\n地图 %s' % (SAV, MAP))
d = ec.SaveDoc(SAV)
teams = ai_objects.read_ai(d)['teams']
print('小队 %d 个' % len(teams))

print('\n=== [1] 结构：16 个标志 + 心控字段都在 ===')
t0 = teams[0]
# ★§260/§261/§263：+0x109 可重组、+0x10A 基地防御、+0x10B 仅攻击敌对 ⇒ 16 → 19
ck('flags 键数 = 19', len(t0['flags']) == 19, len(t0['flags']))
ck('名字与 TEAM_FLAGS 完全一致',
   set(t0['flags']) == set(ai_objects.TEAM_FLAGS.values()),
   set(t0['flags']) ^ set(ai_objects.TEAM_FLAGS.values()))
ck('每条小队都有 mc_decision', all('mc_decision' in t for t in teams))
ck('AvoidThreats 不在 0xB8 那串里（+0x106）', 0x106 in ai_objects.TEAM_FLAGS)


def ini_of(txt, tid):
    j = txt.find('[%s]' % tid)
    if j < 0:
        return None
    seg = txt[j:j + 800].replace('\r\n', '\n')
    out = {}
    for line in seg.split('\n')[1:]:
        if line.startswith('['):
            break
        if '=' in line:
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip()
    return out


print('\n=== [2] 与地图 INI 逐条对账（硬证据）===')
if not MAP:
    print('  跳过：找不到 ALL01UMD.MAP')
else:
    txt = open(MAP, 'rb').read().decode('latin-1')
    for foff, nm in ai_objects.TEAM_FLAGS.items():
        ok = n = 0
        for t in teams:
            i = ini_of(txt, t['id'])
            if not i or i.get(nm, '').lower() not in ('yes', 'no'):
                continue
            n += 1
            if d.plain[t['off'] + foff] == (1 if i[nm].lower() == 'yes' else 0):
                ok += 1
        ck('%-18s +0x%03X  %d/%d' % (nm, foff, ok, n), n > 0 and ok == n)
    # 心控重建
    ok = n = 0
    for t in teams:
        i = ini_of(txt, t['id'])
        if not i or 'MindControlDecision' not in i:
            continue
        try:
            want = int(i['MindControlDecision'])
        except ValueError:
            continue
        n += 1
        if struct.unpack_from('<i', d.plain, t['off'] + ai_objects.TEAM_MC_OFF)[0] == want:
            ok += 1
    ck('心控重建 +0x%03X  %d/%d' % (ai_objects.TEAM_MC_OFF, ok, n), n > 0 and ok == n)

print('\n=== [3] 写入：只动 1 字节 / 4 字节 ===')
tid = t0['id']
base = bytes(d.plain)
info = ai_objects.set_team_flag(d, tid, '增援', not t0['flags']['Reinforce'])
diff = [i for i in range(len(base)) if base[i] != d.plain[i]]
ck('set_team_flag 只改 1 字节', len(diff) == 1, len(diff))
ck('改的正是 +0xBF（Reinforce）', info['off'] - t0['off'] == 0xBF, hex(info['off'] - t0['off']))
ck('回读生效', bool(d.plain[info['off']]) == (not t0['flags']['Reinforce']))

base = bytes(d.plain)
info2 = ai_objects.set_team_mc_decision(d, tid, 1)
diff = [i for i in range(len(base)) if base[i] != d.plain[i]]
ck('set_team_mc_decision 只改 dword 里必要字节',
   len(diff) <= 4 and info2['off'] - t0['off'] == ai_objects.TEAM_MC_OFF, len(diff))

print('\n=== [4] 落盘重读 ===')
p = out('ev235_小队标志.SAV')
d.save(p, backup=False)
e = ec.SaveDoc(p)
t2 = next(x for x in ai_objects.read_ai(e)['teams'] if x['id'] == tid)
ck('重读后标志保持', t2['flags']['Reinforce'] == (not t0['flags']['Reinforce']),
   t2['flags']['Reinforce'])
ck('重读后心控重建 = 1', t2['mc_decision'] == 1, t2['mc_decision'])
ck('重读后小队数不变', len(ai_objects.read_ai(e)['teams']) == len(teams))
print('  产物：%s' % p)

print('\n§235 自检：PASS %d / FAIL %d' % (n_ok, n_bad))
sys.exit(1 if n_bad else 0)
