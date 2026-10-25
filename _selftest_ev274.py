# -*- coding: utf-8 -*-
"""§274 自检：删单位 A/B 两法（整条删 vs 只删主体留孤儿伴随）。

跑法：D:/py/python.exe _selftest_ev274.py
测试档：E:\\YURI\\SAVE4823.SAV（1448 台单位；目标 = (16,179) 的盟军基地车 AMCV）
只读测试档，全程在内存里操作，不写任何文件。
"""
import io
import os
import struct
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc                                       # noqa: E402
from _paths import find_sav                                           # noqa: E402

OK = 0
FAIL = 0


def chk(cond, msg, detail=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [OK] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s   %r' % (msg, detail))


def _pick_src():
    """找一份「(16,179) 有 AMCV」的 SAVE4823*.SAV。

    ★玩家会把测试档挪进子文件夹，`find_sav()` 会按目录顺序挑到旧档
      （实测挑到 `新建文件夹 (26)` 那份，里面没有这台车）⇒ 这里按时间倒序逐个验内容。
    """
    import glob
    cands = []
    for p in glob.glob(r'E:\YURI\**\SAVE4823*.SAV', recursive=True):
        try:
            cands.append((os.path.getmtime(p), p))
        except Exception:
            pass
    cands.sort(reverse=True)
    for _, p in cands:
        try:
            d = SaveDoc(p)
            if any(r['type'] == 'AMCV' and (r.get('x'), r.get('y')) == (16, 179)
                   for r in d.unit_records()):
                return p
        except Exception:
            continue
    raise RuntimeError('找不到含 (16,179) 盟军基地车的 SAVE4823*.SAV')


try:
    SRC = _pick_src()
except Exception as ex:
    print('%s' % ex)
    sys.exit(1)

base = SaveDoc(SRC)
TGT = (16, 179)
am0 = [r for r in base.unit_records() if r['type'] == 'AMCV' and (r['x'], r['y']) == TGT][0]
CG = bytes(base.plain[am0['comp']:am0['comp'] + 16])          # 载具 locomotor 伴随类 GUID
N_COMP0 = bytes(base.plain).count(CG)
N_UNIT0 = len(base.unit_records())
U0 = {(r['type'], r['x'], r['y'], r['key']) for r in base.unit_records()}
print('测试档 %s' % SRC)
print('  单位 %d 台；AMCV off=%#x size=%d comp=%#x comp_size=%d key=%#x'
      % (N_UNIT0, am0['off'], am0['size'], am0['comp'], am0['comp_size'], am0['key']))
print('  伴随类 GUID %s 全档 %d 处' % (CG.hex(), N_COMP0))

for label, soft, want_delta in (('A 组：整条删（含伴随）', False, -(am0['size'] + am0['comp_size']) - 8),
                                ('B 组：只删主体，伴随留孤儿', True, -am0['size'] - 8)):
    print('\n=== %s ===' % label)
    d = SaveDoc(SRC)
    am = [r for r in d.unit_records() if r['type'] == 'AMCV' and (r['x'], r['y']) == TGT][0]
    L0 = len(d.plain)
    info = d.soft_remove_unit(am) if soft else d.remove_unit(am)
    pl = d.plain

    chk(info['kept_companion'] is soft, '回报 kept_companion=%s' % soft, info.get('kept_companion'))
    chk(len(pl) - L0 == want_delta,
        '★明文长度变化 = %+d（预期 %+d = 主体%s + 指针表 2×4）'
        % (len(pl) - L0, want_delta, '只' if soft else '+伴随'),
        (len(pl) - L0, want_delta))
    chk(info['group_before'] - 1 == info['group_after'], '组计数 %d → %d'
        % (info['group_before'], info['group_after']), info)
    chk(info['ptr_tables'] == 2, '两张指针表各摘掉 1 条条目', info['ptr_tables'])
    chk(info['cell_cleared'] == 1, '格 (16,179) 的占用者已清 0', info['cell_cleared'])
    chk(any('CALA10' in x for x in info['cleared_refs']),
        '光棱塔 CALA10 的目标指针已清 0', info['cleared_refs'])

    # ---- ★§275 占用种类掩码 ----
    kd = d.cell_kind(*TGT)
    chk(kd == 0, '★格 (16,179) 的占用掩码 +0x138 已清 0（原 0x20，玩家实测不清就挡路）', hex(kd))
    chk(any(x['cell'] == TGT and x['before'] == 0x20 and x['after'] == 0
            for x in info['kind_cleared']),
        'kind_cleared 记账正确', info['kind_cleared'])

    # ---- 该键在明文里彻底消失 ----
    n_key = bytes(pl).count(struct.pack('<I', am['key']))
    chk(n_key == 0, '★单位自键 %#x 在明文里 0 处残留' % am['key'], n_key)

    # ---- 伴随对象：A 少一个，B 保持 ----
    n_comp = bytes(pl).count(CG)
    if soft:
        chk(n_comp == N_COMP0, '★B 组：伴随对象仍在（%d → %d），成了孤儿' % (N_COMP0, n_comp), n_comp)
        chk(len(info['comp_zeroed']) == 2,
            '伴随里回指该键的 %d 处已清 0（%s）' % (len(info['comp_zeroed']), info['comp_zeroed']),
            info['comp_zeroed'])
    else:
        chk(n_comp == N_COMP0 - 1, '★A 组：伴随对象已随之消失（%d → %d）' % (N_COMP0, n_comp), n_comp)
        chk(not info['comp_zeroed'], 'A 组不需要清伴随内部引用', info['comp_zeroed'])

    # ---- 其余单位一个不动 ----
    rs = d.unit_records()
    u = {(r['type'], r['x'], r['y'], r['key']) for r in rs}
    chk(len(rs) == N_UNIT0 - 1, '单位数 %d → %d' % (N_UNIT0, len(rs)), len(rs))
    chk(not [r for r in rs if r['type'] == 'AMCV'], 'AMCV 已不存在', None)
    chk(len(U0 - u) == 1 and not (u - U0),
        '★其余 %d 台单位的 (类型,坐标,自键) 全部原样' % (N_UNIT0 - 1), len(U0 ^ u))
    chk(len({r['key'] for r in rs}) == len(rs), '自键仍全局唯一', None)

    # ---- 其它对象表不受影响 ----
    chk(len(d.buildings() or []) == len(base.buildings() or []),
        '建筑数不变（%d）' % len(d.buildings() or []), len(d.buildings() or []))
    chk(len(d.team_types() or []) == len(base.team_types() or []),
        '小队数不变（%d）' % len(d.team_types() or []), len(d.team_types() or []))
    chk(len(d.trigger_tag_pairs() or []) == len(base.trigger_tag_pairs() or []),
        '触发标签数不变（%d）' % len(d.trigger_tag_pairs() or []), len(d.trigger_tag_pairs() or []))

    # ---- §275 移动：旧格清掩码、新格置掩码 ----
    d3 = SaveDoc(SRC)
    am3 = [r for r in d3.unit_records() if r['type'] == 'AMCV' and (r['x'], r['y']) == TGT][0]
    res3 = d3.set_unit_cell(am3, 23, 179)
    chk(d3.cell_kind(23, 179) == 0x20, '★移动后新格 (23,179) 掩码 = 0x20', hex(d3.cell_kind(23, 179)))
    chk(d3.cell_kind(16, 179) == 0, '★移动后旧格 (16,179) 掩码 = 0（原来会留下看不见的墙）',
        hex(d3.cell_kind(16, 179)))
    chk(len(res3.get('kind_ops') or []) == 2, 'kind_ops 记账 2 条', res3.get('kind_ops'))

    # ---- 拒绝路径仍然有效 ----
    d2 = SaveDoc(SRC)
    am2 = [r for r in d2.unit_records() if r['type'] == 'AMCV' and (r['x'], r['y']) == TGT][0]
    L2 = len(d2.plain)
    try:
        if soft:
            d2.soft_remove_unit(am2, clear_foreign=False)
        else:
            d2.remove_unit(am2, clear_foreign=False)
        chk(False, 'clear_foreign=False 应拒绝删除')
    except RuntimeError as ex:
        chk(len(d2.plain) == L2, 'clear_foreign=False 抛错「%s」且明文一字节未动'
            % str(ex)[:26], len(d2.plain) - L2)

print('-' * 72)
print('§274 删单位 A/B：PASS %d / FAIL %d' % (OK, FAIL))
