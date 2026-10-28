# -*- coding: utf-8 -*-
"""§298 探测：TechnoType 里的武器槽位（**只读，不写任何文件、不改存档**）。

背景
----
玩家问「能不能给某个兵换把枪」。上一段会话摸清了**武器表**（WeaponType 对象：
Damage/ROF/Range/Verses 都能改，已验收），但没碰"哪个类型用哪把武器"——
那是另一套结构（TechnoType 对象里的 Primary/Secondary 指针），风险等级完全不同。

本脚本把这一轮探测的结论**固化成断言**，跑一遍就知道结论是否还成立。

结论（2026-10-28，`E:/YURI/SAVE0665_修改.SAV` 逐项验过）
--------------------------------------------------------
定位锚点
    Type 对象（步兵/车辆/飞机/建筑…）**没有** "双名 +0x40" 特征，`_type_objects()` 扫不到。
    要先用 `Name:XXX` 串定位，再回退 0x19 得到对象起点 `X`：
        X = 「Name:」出现位置 − 0x19        （实测 456 个对象全部成立）
    校验锚点：`Strength` 在 `X + 0x7C`，333/338 命中（不符的 5 个是地图 INI 覆盖或重复对象）。

四个武器槽位（**全部是 4 字节指针，不是序号、不是名字**）
        X + 0x874   Primary            武器名在对象里根本不以字符串存在
        X + 0x890   Secondary
        X + 0xA70   ElitePrimary
        X + 0xA8C   EliteSecondary
    空槽 = 0（无武器建筑 GAPOWR 四槽全 0）。

指针的含义与限制（**决定这条路能不能走通的关键**）
    · 同一个档里：**同一把武器 = 同一个指针；不同武器 = 不同指针**（实测 69 把 Primary、
      25 把 Secondary、39 把 ElitePrimary、9 把 EliteSecondary，冲突 0）。
    · 不同档之间**不通用**：120mm 在 3 个遭遇战档里都是 0x109E9D40，
      但战役档 `ALL01UMD.sav` 里整片地址都不一样（堆布局不同）。
      ⇒ 换枪只能**从同一个档里的别的类型借用指针**，不能跨档抄、也不能自己算。
    · 因此覆盖率有上限：本档 213 把武器里，有 **125 把（59%）** 能从某个类型对象借到指针；
      剩下 88 把（多为 `AGGattling*`/`AAGatt*` 这类只有 AI 或特定建筑用的）没有现成指针。

跑法：py _probe_primary.py [存档路径]
"""
import io
import os
import re
import struct
import sys
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc  # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else r'E:/YURI/SAVE0665_修改.SAV'

# ---- 实测布局（偏移相对对象起点 X；X = 「Name:」位置 − 0x19）----
NAME_ANCHOR_BACK = 0x19
OFF_STRENGTH = 0x7C
OFF_COST = 0x5EC
WEAPON_SLOTS = (('Primary', 0x874), ('Secondary', 0x890),
                ('ElitePrimary', 0xA70), ('EliteSecondary', 0xA8C))

PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


def type_objects(pl, rules):
    """→ [(名字, X)]。只收 rules 里有的（排掉地图名/触发器名之类的误命中）。"""
    rx = re.compile(rb'Name:([0-9A-Za-z_.\-]{1,40})\x00')
    out, seen = [], {}
    for m in rx.finditer(pl):
        nm = m.group(1).decode('ascii', 'replace')
        if nm not in rules:
            continue
        X = m.start() - NAME_ANCHOR_BACK
        if X < 0 or X + 0xC00 > len(pl):
            continue
        # 同一个名字可能出现两次（存档里有两份对象），两份都要留 —— 用来交叉验证
        out.append((nm, X))
        seen[nm] = seen.get(nm, 0) + 1
    return out


def main():
    print('=' * 72)
    print('§298 探测：TechnoType 武器槽位  存档 = %s' % SRC)
    if not os.path.exists(SRC):
        print('样本不存在，跳过')
        return
    doc = SaveDoc(SRC)
    pl = doc.plain
    rules = doc._rules_all() or {}
    objs = type_objects(pl, rules)
    weapons = doc.weapon_objects()

    print('\n[1] 对象与锚点')
    print('    Type 对象（Name: 命中且在 rules 里）%d 个' % len(objs))
    ck(len(objs) > 400, '对象数量 > 400（实际 %d）' % len(objs))
    ck(bool(doc._type_objects()), '武器表的"双名"扫描仍然可用（%d 个）' % len(doc._type_objects()))
    # 双名扫描扫不到 TechnoType —— 这是本轮的起点
    names = {nm for nm, _ in objs}
    dbl = {nm for _off, nm in doc._type_objects()}
    ck(not (names & dbl) or len(names & dbl) < 20,
       'TechnoType 名字基本不在"双名"对象集合里（交集 %d）' % len(names & dbl))

    print('\n[2] Strength 在 X+0x7C（验证锚点 X 取对了）')
    ok = tot = 0
    for nm, X in objs:
        s = (rules.get(nm, {}).get('Strength') or '').strip()
        if not s.isdigit():
            continue
        tot += 1
        if struct.unpack_from('<I', pl, X + OFF_STRENGTH)[0] == int(s):
            ok += 1
    print('    命中 %d/%d' % (ok, tot))
    ck(tot > 200 and ok / float(tot) > 0.9, 'Strength 命中率 > 90%%（%.0f%%）' % (100.0 * ok / max(1, tot)))

    print('\n[3] 四个武器槽位：值 ↔ 武器名 双向唯一')
    ptr_of = {}
    for key, d in WEAPON_SLOTS:
        name2ptr, ptr2name = defaultdict(set), defaultdict(set)
        for nm, X in objs:
            w = (rules.get(nm, {}).get(key) or '').strip()
            if not w:
                continue
            v = struct.unpack_from('<I', pl, X + d)[0]
            if not v:
                continue
            name2ptr[w].add(v)
            ptr2name[v].add(w)
        merge = [v for v, ws in ptr2name.items() if len(ws) > 1]
        split = [v for v, ps in name2ptr.items() if len(ps) > 1]
        print('    %-14s +0x%03X  覆盖 %-3d 把武器   一指针多武器 %d   一武器多指针 %d'
              % (key, d, len(name2ptr), len(merge), len(split)))
        ck(not merge, '%s：没有"一个指针被两种武器共用"（冲突 %d）' % (key, len(merge)))
        if key == 'Primary':
            ck(len(name2ptr) > 60, 'Primary 覆盖武器 > 60 把（实际 %d）' % len(name2ptr))
        for nm, ps in name2ptr.items():
            if len(ps) == 1:
                ptr_of.setdefault(nm, set()).add(list(ps)[0])

    print('\n[4] 空槽必须是 0（拿无武器建筑当反例）')
    for nm in ('GAPOWR', 'GAREFN', 'GACNST'):
        rows = [(X) for n, X in objs if n == nm]
        if not rows:
            continue
        vals = [struct.unpack_from('<I', pl, rows[0] + d)[0] for _k, d in WEAPON_SLOTS]
        print('    %-8s %s' % (nm, [hex(v) for v in vals]))
        ck(all(v == 0 for v in vals), '%s 四个槽位全为 0（无武器）' % nm)

    print('\n[5] 抽样：rules 名字 → 存档指针')
    for nm in ('E1', 'E2', 'HTNK', 'DESO', 'ATESLA'):
        hit = [(X) for n, X in objs if n == nm]
        if not hit:
            continue
        X = hit[0]
        sec = rules.get(nm, {})
        got = [struct.unpack_from('<I', pl, X + d)[0] for _k, d in WEAPON_SLOTS]
        print('    %-8s rules=%-38s 存档=%s'
              % (nm, '/'.join((sec.get(k) or '-') for k, _ in WEAPON_SLOTS),
                 ' '.join('%08X' % v for v in got)))
        ck((sec.get('Primary') or '') and got[0] != 0,
           '%s 有 Primary ⇒ 第一个槽位非 0' % nm)
        ck((sec.get('Primary') or '') in ptr_of,
           '%s 的 Primary「%s」在指针表里' % (nm, sec.get('Primary')))

    print('\n[6] 覆盖率（决定"换枪"能换到哪些武器）')
    known = [w for w in weapons if w['name'] in ptr_of]
    print('    存档武器 %d 把，有现成指针的 %d 把（%.0f%%）'
          % (len(weapons), len(known), 100.0 * len(known) / max(1, len(weapons))))
    miss = [w['name'] for w in weapons if w['name'] not in ptr_of]
    print('    借不到的 %d 把，例如 %s' % (len(miss), miss[:8]))
    ck(len(known) > 100, '可换目标 > 100 把（实际 %d）' % len(known))

    print('\n' + '=' * 72)
    print('结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print('  FAIL: %s' % f)


if __name__ == '__main__':
    main()
