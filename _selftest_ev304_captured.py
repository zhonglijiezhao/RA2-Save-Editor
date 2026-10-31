# -*- coding: utf-8 -*-
"""自检 ev304：占领标记（+0x6AF）与「当前归属 / 原属方」两格的语义

用玩家给的真实档做断言（无需游戏）；找不到实验档时**现场从 A 造**，不依赖外部文件。
  A 一玩家方 占领敌方一个.SAV   —— 74,93 那栋被占领
  B 两基地 一玩家方 一敌方.SAV   —— 未占领
"""
import sys, os, struct, tempfile
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, '_pylibs'))
from core.editor_core import SaveDoc

YURI = r'E:\YURI'


def find_sav(name_part):
    best = None
    for dp, dn, fn in os.walk(YURI):
        for f in fn:
            if not f.upper().endswith('.SAV') or name_part not in f:
                continue
            if f.upper().startswith('TEST'):      # 实验档不算素材
                continue
            p = os.path.join(dp, f)
            if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                best = p
    return best


A_P = find_sav('一玩家方 占领敌方一个')
B_P = find_sav('两基地 一玩家方 一敌方')
if not A_P or not B_P:
    print('SKIP：缺素材档（A=%r B=%r）' % (A_P, B_P)); sys.exit(0)
print('素材：\n  A=%s\n  B=%s' % (A_P, B_P))

ok = fail = 0


def chk(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print('  x %s' % msg)


A = SaveDoc(A_P, write_cache=False)
B = SaveDoc(B_P, write_cache=False)
CAP, REF = (74, 93), (64, 84)
ba = {(r['x'], r['y']): r for r in A.buildings()}
bb = {(r['x'], r['y']): r for r in B.buildings()}

chk(len(ba) == 2 and len(bb) == 2, '两份档都应认到 2 栋建筑（A=%d B=%d）' % (len(ba), len(bb)))
chk(ba[CAP]['captured'] is True, 'A 的 74,93 应 captured=True')
chk(ba[REF]['captured'] is False, 'A 的 64,84 应 captured=False')
chk(bb[CAP]['captured'] is False, 'B 的 74,93 应 captured=False')
chk(bb[REF]['captured'] is False, 'B 的 64,84 应 captured=False')

me = A.player_house()
key_me = A.resolve_house(me)
chk(ba[CAP]['house'] == key_me, 'A 的 74,93 house 应是本家键 0x%X（实际 0x%X）' % (key_me, ba[CAP]['house']))
chk(ba[CAP]['origin'] != ba[CAP]['house'], 'A 的 74,93 原属方应与现归属不同（占领特征）')
chk(ba[REF]['origin'] == ba[REF]['house'], 'A 的 64,84 原属方应等于现归属')
chk(bb[CAP]['house'] == bb[CAP]['origin'], 'B 的 74,93 原属方应等于现归属（未占领）')

na = len(A.buildings_of_house(me))
nb = len(B.buildings_of_house(me))
chk(na == 2, 'A 本家建筑数应为 2（占领来的那栋也算本家），实际 %d' % na)
chk(nb == 1, 'B 本家建筑数应为 1，实际 %d' % nb)

# --- 往返写读：A 清成自建 → 再读回来，只掉 1 个字节 ---
d = SaveDoc(A_P, write_cache=False)
d.set_building_captured(False, cx=CAP[0], cy=CAP[1])
tmp = os.path.join(tempfile.gettempdir(), 'ev304_uncap.SAV')
d.save(tmp, backup=False)
e = SaveDoc(tmp, write_cache=False)
be = {(x['x'], x['y']): x for x in e.buildings()}
chk(be[CAP]['captured'] is False, '清成自建后应 captured=False')
chk(be[CAP]['house'] == ba[CAP]['house'], '清成自建不应改当前归属')
chk(be[CAP]['origin'] == ba[CAP]['origin'], '清成自建（also_origin=False）不应改原属方')
chk(len(bytes(e.plain)) == len(bytes(A.plain)), '明文长度应不变')
pa, pe = bytes(A.plain), bytes(e.plain)
diff = [i for i in range(len(pa)) if pa[i] != pe[i]]
chk(len(diff) == 1, '应只有 1 字节变化，实际 %d 处 %s' % (len(diff), ['0x%X' % i for i in diff[:5]]))
if diff:
    chk(diff[0] == ba[CAP]['base'] + SaveDoc.BLD_CAPTURED_OFF,
        '变化的应是 +0x6AF（期望 0x%X，实际 0x%X）' % (ba[CAP]['base'] + SaveDoc.BLD_CAPTURED_OFF, diff[0]))

# --- 再标回去（captured=True）+ also_origin，验证两个开关各管各的 ---
d2 = SaveDoc(tmp, write_cache=False)
d2.set_building_captured(True, cx=CAP[0], cy=CAP[1], also_origin=True)
tmp2 = os.path.join(tempfile.gettempdir(), 'ev304_recap.SAV')
d2.save(tmp2, backup=False)
e2 = SaveDoc(tmp2, write_cache=False)
b2 = {(x['x'], x['y']): x for x in e2.buildings()}
chk(b2[CAP]['captured'] is True, '标回占领态后应 captured=True')
chk(b2[CAP]['origin'] == b2[CAP]['house'], 'also_origin=True 时原属方应被同步成当前归属')

for p in (tmp, tmp2):
    try:
        os.remove(p)
    except Exception:
        pass

print('ev304 自检：%d 通过 / %d 失败' % (ok, fail))
sys.exit(1 if fail else 0)
