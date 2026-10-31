# -*- coding: utf-8 -*-
"""§302 自检：类型对象**自身地址表** + 武器→抛射体指针 + 改写指针。

为什么单独立一套（2026-10-29 玩家：「存档是自足的 这是无数次证明的」）：
  §301 里我断言"子武器指针不可算、只能改数量"，理由是"指针值减去文件偏移不是常数"。
  **方向错了** —— 不该拿指针数值反推文件位置，而该去对象里找它自己记的地址。
  找到了：每个 Type 对象在 **名字偏移 −0x28** 处存着**自身运行时地址**。

本自检把这条钉死（跑 2 份不同存档，防止单档巧合）：
  ① 自地址表：合法地址占比高、**零重复**；
  ② 档内**全部**分裂指针都能被这张表解析成唯一、且语义正确的武器名
     （原版档：Electricbounce→TeslaFragment / LargeCometP→CometFragment /
       SuperCometP→SuperCometFragment / SuperSmallCometP→CometFragment）；
  ③ 武器记录的 `+0x7C` 确实是抛射体指针：按 rules 的 `Projectile=` 分组，
     "同组同值 / 异组异值"全部成立，且值等于抛射体对象的地址；
  ④ `set_weapon_projectile` / `set_projectile_shrapnel_weapon` 只改明文 4 字节、
     长度不变、能存盘往返；
  ⑤ 指向不存在的对象必须报错（指向 NULL 就是 v1~v3 崩在 0x4664C0 的原因）。

跑法：python _selftest_ev302_type_addr_pointers.py
"""
import collections
import io
import os
import struct
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc  # noqa: E402

SAMPLES = [p for p in (r'D:/233/RA2YR/SAVE4823.SAV',
                       r'E:/YURI/SAVE0665.SAV') if os.path.exists(p)]
PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


for SRC in SAMPLES:
    print('\n=== %s ===' % SRC)
    d = SaveDoc(SRC)
    pl = d.plain

    # ① 自地址表
    objs = d._type_objects()
    amap = d.type_addr_map()
    total_addr = sum(len(v) for v in amap.values())
    ck(len(objs) > 200, '类型对象扫到 %d 个（>200）' % len(objs))
    ck(total_addr / float(len(objs)) >= 0.90,
       'name-0x28 是合法地址的占 %.1f%% (≥90%%)' % (100.0 * total_addr / len(objs)))
    dups = {v: o for v, o in amap.items() if len(o) > 1}
    ck(not dups, '地址表零重复（重复 %d 个）' % len(dups))

    # ② 全部分裂指针都能解析
    pj_sh = d.projectile_shrapnel()
    ck(len(pj_sh) >= 3, '档内带分裂的抛射体 %d 个（≥3）' % len(pj_sh))
    bad = [(x['name'], x['ptr']) for x in pj_sh if not d.resolve_ptr(x['ptr'])]
    ck(not bad, '分裂指针全部解析成唯一对象（解析不出：%s）' % (bad or '无'))
    known = {x['name']: d.resolve_ptr(x['ptr']) for x in pj_sh}
    print('     解析结果：%s' % known)
    if 'Electricbounce' in known:
        ck(known['Electricbounce'] == 'TeslaFragment',
           'Electricbounce 的子武器 = TeslaFragment（实际 %s）' % known['Electricbounce'])
    if 'SuperCometP' in known:
        ck(known['SuperCometP'] == 'SuperCometFragment',
           'SuperCometP 的子武器 = SuperCometFragment（实际 %s）' % known['SuperCometP'])

    # ③ 武器 +0x7C = 抛射体指针（按 rules 分组验证“同组同值/异组异值”）
    r = d._rules_all()
    if r:
        groups = collections.defaultdict(list)
        for w in d.weapon_projectiles():
            kv = r.get(w['name']) or {}
            pj = (kv.get('Projectile') or '').strip().upper()
            if pj:
                groups[pj].append(w)
        multi = {g: v for g, v in groups.items() if len(v) >= 2}
        okg = 0
        for g, v in multi.items():
            ptrs = {x['ptr'] for x in v}
            names = {x['projectile'] for x in v}
            if len(ptrs) == 1 and names == {v[0]['projectile']}:
                okg += 1
        ck(okg == len(multi),
           '+0x7C 同抛射体同值：%d/%d 组通过' % (okg, len(multi)))
        ck(len({x['ptr'] for x in sum(multi.values(), [])}) == len(multi),
           '不同抛射体取值互不相同（%d 组 / %d 个不同值）'
           % (len(multi), len({x['ptr'] for x in sum(multi.values(), [])})))
        unp = [x['name'] for x in d.weapon_projectiles()
               if (r.get(x['name']) or {}).get('Projectile') and not x['projectile']]
        ck(not unp, '有 Projectile= 的武器全部解析出抛射体（解析不出：%s）' % (unp[:5] or '无'))

    # ④ 改写：只动 4 字节 + 往返
    wp = {x['name'].upper(): x for x in d.weapon_projectiles()}
    target = 'NZ2BOLT' if 'NZ2BOLT' in wp else next(iter(wp))
    cur = wp[target]['projectile']
    alt = next((n for n in ('InvisibleMedium', 'InvisibleHigh', 'Invisible')
                if d.obj_addr(n)[0] and n != cur), None)
    ck(alt is not None, '找得到一个可换的抛射体（%s）' % alt)
    if alt:
        before = bytes(d.plain)
        res = d.set_weapon_projectile(target, alt)
        after = bytes(d.plain)
        diff = [i for i in range(len(before)) if before[i] != after[i]]
        ck(res['new_name'] == alt, '%s：%s -> %s' % (target, res['old_name'], alt))
        ck(len(after) == len(before), '明文长度不变（%d）' % len(after))
        ck(len(diff) <= 4 and all(res['off'] <= i < res['off'] + 4 for i in diff),
           '只改了目标那 4 字节（实际 %d 字节）' % len(diff))

        tmpdir = tempfile.mkdtemp()
        dst = os.path.join(tmpdir, 'ev302a.SAV')
        d.save(dst)
        e = SaveDoc(dst)
        back = {x['name'].upper(): x for x in e.weapon_projectiles()}
        ck(back[target]['projectile'] == alt,
           '存盘往返后仍是 %s（读到 %s）' % (alt, back[target]['projectile']))

    # ⑤ 指向不存在的对象必须报错
    for fn, args in ((d.set_weapon_projectile, (target, 'NO_SUCH_PROJ_XYZ')),
                     (d.set_projectile_shrapnel_weapon, (pj_sh[0]['name'], 'NO_SUCH_WPN_XYZ'))):
        try:
            fn(*args)
            ck(False, '%s 对不存在名字应当报错，却成功了' % fn.__name__)
        except KeyError:
            ck(True, '%s 对不存在名字报 KeyError（不会写成空指针）' % fn.__name__)

    # ⑥ 只读方法不动明文
    h0 = bytes(d.plain)
    d.type_addr_map(); d.weapon_projectiles(); d.projectile_shrapnel(); d.resolve_ptr(0)
    ck(bytes(d.plain) == h0, '只读方法不修改明文')

    # ⑦ 两级链可建、且能走到终点（不构成环）
    if 'Electricbounce' in known and d.obj_addr('SmallTeslaP')[0]:
        f = SaveDoc(SRC)
        f.set_projectile_shrapnel('Electricbounce', 3)
        f.set_projectile_shrapnel_weapon('SmallTeslaP', 'ElectricBolt')
        f.set_projectile_shrapnel('SmallTeslaP', 1)
        tmpdir = tempfile.mkdtemp()
        dst2 = os.path.join(tmpdir, 'ev302b.SAV')
        f.save(dst2)
        g = SaveDoc(dst2)
        chain, node, seen = [], 'Electricbounce', set()
        while True:
            ps = {x['name']: x for x in g.projectile_shrapnel()}
            ws = {x['name']: x for x in g.weapon_projectiles()}
            p = ps.get(node)
            if not p:
                chain.append('%s(止)' % node)
                break
            w = g.resolve_ptr(p['ptr'])
            chain.append('%s -%d-> %s' % (node, p['count'], w))
            if w in seen or len(chain) > 8:
                chain.append('环形/CONFIRM!')
                break
            seen.add(w)
            node = (ws.get(w) or {}).get('projectile')
            if not node:
                chain.append('(%s 无抛射体)' % w)
                break
        ck(len(chain) >= 3 and '环形' not in chain[-1],
           '两级链：%s' % ' | '.join(chain))

print('\n结果：%d 通过 / %d 失败' % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print('  FAIL: %s' % f)
    sys.exit(1)
