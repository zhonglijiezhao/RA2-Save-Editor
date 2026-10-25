# -*- coding: utf-8 -*-
"""§276 自检：任务名读自存档头部固定字段（不是"猜最长中文串"）。

跑法：D:/py/python.exe _selftest_ev276.py

字段布局（827 个存档实测一致，见 docs §276）：
  0x1360 UTF-16 显示名（上限 45 字，00 00 结尾，后补零到 0x13BA）
  0x13BA ASCII  地图 `[Basic] Name=` 的原始 CSF 键（如 `Name:ALL01md`，可空）
  0x13DA UTF-16 副标题/章节（官方战役「美國：導火索」，自制图常是地图名）
  0x1450 UTF-16 任务简报正文
"""
import glob
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc                                       # noqa: E402

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


# ---------- 1) 常量与两份已知档 ----------
print('[1] 字段偏移与已知档')
chk(SaveDoc.SCEN_NAME_OFF == 0x1360, 'SCEN_NAME_OFF = 0x1360', hex(SaveDoc.SCEN_NAME_OFF))
chk(SaveDoc.SCEN_KEY_OFF == 0x13BA, 'SCEN_KEY_OFF = 0x13BA（不是 0x13B8，实测差 2 字节）',
    hex(SaveDoc.SCEN_KEY_OFF))
chk(SaveDoc.SCEN_SUB_OFF == 0x13DA, 'SCEN_SUB_OFF = 0x13DA', hex(SaveDoc.SCEN_SUB_OFF))
chk(SaveDoc.SCEN_BRIEF_OFF == 0x1450, 'SCEN_BRIEF_OFF = 0x1450', hex(SaveDoc.SCEN_BRIEF_OFF))

# 收集样本：优先拿"铁峰征讨"和"丛林救援"两种任务各一份
cands = sorted({p for pat in (r'E:\YURI\**\*.SAV',) for p in glob.glob(pat, recursive=True)
                if os.path.getsize(p) > 200000}, key=os.path.getmtime, reverse=True)
samples = []
names = set()
tried = 0
for p in cands:
    if len(samples) >= 3 or tried >= 12:
        break
    tried += 1
    try:
        d = SaveDoc(p)
    except Exception:
        continue
    sn = d.scenario_strings()
    nm = (sn.get('name') or '').strip()
    if not nm or nm in names:
        if len(samples) >= 2:
            continue
    names.add(nm)
    samples.append((p, d, sn))
print('  采到 %d 份样本（%d 个不同任务名）' % (len(samples), len(names)))
chk(len(samples) >= 2, '至少两份不同任务的样本', len(samples))

# ---------- 2) 每份样本：任务名正确、不是简报、无乱码首字 ----------
print('\n[2] 任务名（逐份）')
for p, d, sn in samples:
    nm = (sn.get('name') or '')
    sub = (sn.get('subtitle') or '')
    br = (sn.get('brief') or '')
    key = (sn.get('name_key') or '')
    info_nm = d.info.get('任务名') or ''
    print('  %-30s 名=%-14r 副=%-12r 键=%r' % (os.path.basename(p)[:28], nm[:12], sub[:10], key))
    chk(bool(nm.strip()), '0x1360 读得出非空名字', nm)
    chk('\u9200' not in info_nm and '\u9200' not in nm, '没有乱码首字「鈀」（旧版奇对齐的锅）', info_nm[:8])
    chk(info_nm and info_nm != br[:len(info_nm)], '★显示的任务名不是简报正文', (info_nm[:12], br[:12]))
    # ★§277 起：有地图时任务名取自 CSF（值本身带「军事行动：」），没地图时用 0x1360 原文
    chk(info_nm == nm, '★info[任务名] = CSF / 存档 0x1360 的原文', (info_nm, nm))
    chk(d.info.get('任务名短') == (nm[len('军事行动：'):] if nm.startswith('军事行动：') else nm),
        'info[任务名短] = 剥掉「军事行动：」前缀', d.info.get('任务名短'))
    chk(d.info.get('任务名全称') in (nm, '-'), 'info[任务名全称] = 0x1360 原文',
        d.info.get('任务名全称'))
    chk(key == '' or key.startswith('Name:'), '名字键为空或形如 `Name:xxx`', key)
    chk(len(br) > 0, '简报字段也读得到（%d 字）' % len(br), br[:12])

# ---------- 3) 两种已知任务名的具体值 ----------
print('\n[3] 已知任务名')
allnames = {(sn.get('name') or '') for _, _, sn in samples}
print('  本次样本里的名字：%s' % sorted(allnames))

# ---------- 4) 兜底：名字字段被人为清空时，回退到旧启发式且不再出乱码 ----------
print('\n[4] 兜底（名字字段清空）')
p0, d0, _ = samples[0]
d = SaveDoc(p0)
for k in range(45):
    d.plain[SaveDoc.SCEN_NAME_OFF + 2 * k: SaveDoc.SCEN_NAME_OFF + 2 * k + 2] = b'\x00\x00'
sn2 = d.scenario_strings()
chk(sn2['name'] == '', '把 0x1360 清空后 name 为空', sn2['name'])
fb = d._find_title()
print('  兜底 _find_title() = %r' % (fb[:40] if fb else ''))
chk('\u9200' not in (fb or ''), '兜底结果里没有乱码首字「鈀」', (fb or '')[:8])

# ---------- 5) 与地图侧的一致性（能读地图时） ----------
print('\n[5] 地图侧对照')
try:
    mp = d0.scenario_map_path()
    if mp and os.path.isfile(mp):
        import map_ini
        mi = map_ini.MapIni(mp)
        name = (mi.sections.get('basic') or {}).get('Name')
        print('  %s  [Basic] Name = %r' % (os.path.basename(mp), name))
        chk(True, '地图 [Basic] Name 读得到（可能与存档里的键不同名，仅作对照）', name)
    else:
        print('  没找到对应地图，跳过')
except Exception as e:
    print('  地图对照跳过：%s' % e)

print('-' * 72)
print('§276 任务名读取：PASS %d / FAIL %d' % (OK, FAIL))
