# -*- coding: utf-8 -*-
"""自检：单位武器表的**覆盖率**（玩家 2026-11-05：「好多没读出来」）

玩家给的 `E:\\YURI\\SAVE6952.SAV` 里，武器表一半型号都是「—」。两个根因，本自检各钉一条：

① **型号扫描漏人**（`unit_types()` 要求 `Name:` 的值 == 类型 ID）
   存档 `+80` 存的是 **UIName 键**，只有一部分类型刚好同名：
     GGI → `Name:GuardianGI`、TANY → `Name:TANYA`、LTNK → `Name:Lasher`、
     BFRT → `Name:Battlefortress`、YURI → `Name:YuriClone`
   SAVE6952 实测 525 个对象里 **181 个**被丢掉 ⇒ 武器表里 43 个型号整行缺席。
   ⇒ 改用 `unit_type_objects()`：**结构判据** `Name:` 恒在对象 `+81`（532/532）。

② **武器指针被地址范围过滤掉**（`type_addr_map()` 写死 `0x04000000 <= v`）
   同一份存档里对象地址分属几段堆：`0x02B8xxxx`（M60/Para/ATGUN/TankBolt…）、
   `0x0EDxxxxx`、`0x1012xxxx`。下界把 0x02B 那 224 个整批丢掉
   ⇒ 槽位明明有指针，却翻不出名字。
   ⇒ 改成「像指针即可」：≥64KB、<2GB、**8 字节对齐**。

断言：
  A 型号空间：≥500 个对象、GGI/TANY/LTNK/BFRT/YURI 全在，且它们的 `Name:` ≠ 类型 ID
  B 武器表：型号数 ≥ 100、上面这几个都在，E1 仍是 M60/Para/M60E/ParaE（回归基准）
  C 地址表：M60（0x02B 段）能查到地址；`resolve_ptr` 能翻出名字
  D 未解析槽：HORNET 主武器有指针但无名字（GUI 会显示 `?地址`，不能显示成 `—`）
  E 写入仍正常：把 E1 主武器改成 Para，落盘回读生效
"""
import io
import os
import shutil
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc            # noqa: E402

OK = FAIL = 0


def chk(cond, msg, got=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [OK] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s   实际=%r' % (msg, got))


SAV = r'E:\YURI\SAVE6952.SAV'
TMP = tempfile.mkdtemp(prefix='_uwc_')
try:
    if not os.path.isfile(SAV):
        print('样本不存在：%s —— 跳过' % SAV)
        sys.exit(0)
    SaveDoc.set_game_dir(os.path.dirname(SAV))
    d = SaveDoc(SAV)

    print('== A. 型号空间（结构判据）==')
    uo = d.unit_type_objects()
    chk(len(uo) >= 500, '扫出 ≥500 个 Type 对象（实 %d）' % len(uo), len(uo))
    for n, ui in (('GGI', 'GuardianGI'), ('TANY', 'TANYA'), ('LTNK', 'Lasher'),
                  ('BFRT', 'Battlefortress'), ('YURI', 'YuriClone')):
        chk(n in uo, '%s 在型号空间里' % n)
        chk((uo.get(n) or {}).get('uiname') == ui,
            '%s 的 Name: = %s（≠ 类型 ID，老判据就是在这里丢的）' % (n, ui),
            (uo.get(n) or {}).get('uiname'))
    chk('E1' in uo and uo['E1']['uiname'] == 'E1', 'E1 的 Name: 恰好等于 ID（所以它一直没丢）')

    print('== B. 武器表覆盖率 ==')
    rows = d.unit_weapon_list(include_empty=True)
    byt = {r['type']: r for r in rows}
    chk(len(rows) >= 100, '型号数 ≥100（实 %d）' % len(rows), len(rows))
    for n in ('E1', 'GGI', 'TANY', 'LTNK', 'BFRT', 'YURI'):
        chk(n in byt, '武器表里有 %s' % n)
    e1 = byt.get('E1') or {'slots': {}}
    got = {k: v['name'] for k, v in (e1.get('slots') or {}).items()}
    chk(got == {'primary': 'M60', 'secondary': 'Para',
                'elite_primary': 'M60E', 'elite_secondary': 'ParaE'},
        'E1 四槽 = M60/Para/M60E/ParaE（回归基准没变）', got)
    ggi = byt.get('GGI') or {'slots': {}}
    chk((ggi['slots'].get('secondary') or {}).get('name') == 'MissileLauncher',
        'GGI 副武器 = MissileLauncher',
        (ggi['slots'].get('secondary') or {}).get('name'))

    print('== C. 地址表（0x02B 段不能再被过滤）==')
    amap = d.type_addr_map()
    chk(len(amap) >= 1000, '地址表 ≥1000 条（实 %d）' % len(amap), len(amap))
    low = [v for v in amap if v < 0x04000000]
    chk(len(low) >= 100, '0x04000000 以下的对象收进来了（实 %d）' % len(low), len(low))
    addr, who = d.obj_addr('M60')
    chk(bool(addr) and addr < 0x04000000, 'M60 的地址查得到且在 0x02B 段', hex(addr or 0))
    chk(d.resolve_ptr(addr or 0) == 'M60', 'resolve_ptr(M60 地址) 翻得出名字',
        d.resolve_ptr(addr or 0))
    bad = [(v, n) for v, names in amap.items() for n in names if v % 8]
    chk(not bad, '地址表里没有非 8 对齐的假对象（名字 `30` 那种）', bad[:3])

    print('== D. 未解析的槽要能区分"空"和"认不出" ==')
    hz = byt.get('HORNET') or {'slots': {}}
    hz_p = hz['slots'].get('primary') or {}
    chk(bool(hz_p.get('addr')) and not hz_p.get('name'),
        'HORNET 主武器：有指针、暂时翻不出名字（GUI 显示 ?地址）', hz_p)
    cm = byt.get('CMIN') or {'slots': {}}
    chk(not any(v['addr'] for v in cm['slots'].values()),
        'CMIN（超时空矿车）四槽真的都是 0', {k: v['addr'] for k, v in cm['slots'].items()})

    print('== E. 改武器仍能落盘 ==')
    p = os.path.join(TMP, 'in.SAV')
    shutil.copy(SAV, p)
    d2 = SaveDoc(p)
    out = d2.set_unit_weapon('E1', 'primary', 'Para', also_elite=False)
    chk(any(c['new_name'] == 'Para' for c in out.get('changes') or []),
        'E1 主武器改成 Para（changes 里有记录）', out.get('changes'))
    q = os.path.join(TMP, 'out.SAV')
    d2.save(q)
    d3 = SaveDoc(q)
    r3 = next(r for r in d3.unit_weapon_list() if r['type'] == 'E1')
    chk(r3['slots']['primary']['name'] == 'Para',
        '落盘回读 E1 主武器 = Para', r3['slots']['primary']['name'])
    chk(r3['slots']['secondary']['name'] == 'Para',
        '副武器没被误改（还是 Para —— 它本来就是）', r3['slots']['secondary']['name'])
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print('\n单位武器覆盖率自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
