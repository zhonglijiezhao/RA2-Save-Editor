# -*- coding: utf-8 -*-
"""§277 自检：任务名按玩家规则取 `ra2md.csf`（地图文件名 = CSF 键）。

跑法：D:/py/python.exe _selftest_ev277.py

玩家原话：
  > 地图名字是 ALL01UMD，那么 ra2md.csf 中的 all01md 就是任务名字，
  > 如果地图名字是 sov01umd，那么 ra2md.csf 中的 sov01umd 就是任务名字
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

import csf as csf_mod                                                # noqa: E402
from editor_core import SaveDoc                                      # noqa: E402

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


CSF = r'E:\YURI\ra2md.csf'
if not os.path.isfile(CSF):
    print('缺 %s' % CSF)
    sys.exit(1)

# ---------------- 1) CSF 解析 ----------------
print('[1] CSF 解析')
c = csf_mod.Csf(CSF)
print('    %s：%d 条，错误=%s' % (os.path.basename(CSF), len(c.keys), c.error))
chk(c.error is None, '无解析错误', c.error)
chk(len(c.keys) > 5000, '键数 > 5000（%d）' % len(c.keys), len(c.keys))

print('\n[2] 已知键的值')
EXPECT = {'name:all01md': '军事行动：铁峰征讨三绿野孤城',
          'name:all01mdsav': '铁峰征讨三绿野孤城',
          'name:all01': '军事行动：孤独守卫',
          'theme:intro': 'INTRO'}
for k, want in EXPECT.items():
    got = c.get(k)
    chk(got == want, '%-16s = %r' % (k, want), got)
print('    （★值是**逐字节取反的 UTF-16LE**，直接按 UTF-16LE 解会得到半角假名乱码）')

# ---------------- 3) 地图名 → 键 ----------------
print('\n[3] 地图文件名 → CSF 键（玩家规则）')
for mp, want_key in ((r'E:\YURI\ALL01UMD.MAP', 'name:all01md'),
                     ('SOV01UMD.MAP', 'name:sov01md'),
                     ('ALL01MD.MAP', 'name:all01md'),
                     ('ALL01UMD.map', 'name:all01md')):
    keys = c.map_name_keys(mp)
    full, hit = c.name_for_map(mp)
    short, skip = c.short_name_for_map(mp)
    print('    %-22s 候选 %-46s → %r（%s）/ 短名 %r' % (mp.split(os.sep)[-1], keys, full, hit, short))
    chk(hit == want_key, '%s 命中 %s' % (os.path.basename(mp), want_key), hit)
    chk(bool(full), '取到任务名', full)
    chk(bool(short) and skip == want_key + 'sav',
        '短名走 `...mdsav` 键（%r）' % skip, skip)
    chk('\u9200' not in full and '\ufffd' not in full, '值里没有乱码', full)

# ---------------- 4) 存档侧：CSF 优先，且与存档自带的名字一致 ----------------
print('\n[4] 存档侧：CSF 优先 + 与「存档 0x1360」交叉验证')
SAV = r'E:\YURI\新建文件夹 (31)\SAVE4823.SAV'
if not os.path.isfile(SAV):
    print('    缺 %s（跳过这组）' % SAV)
else:
    d = SaveDoc(SAV)
    mp = d.scenario_map_path()
    sn = d.scenario_strings()
    info = d.info
    print('    地图      = %s' % mp)
    print('    任务名    = %r' % info.get('任务名'))
    print('    来源      = %r' % info.get('任务名来源'))
    print('    存档 0x1360 = %r' % sn.get('name'))
    chk(('CSF' in (info.get('任务名来源') or '')), '★有地图时以 CSF 为准（来源 %r）'
        % info.get('任务名来源'), info.get('任务名来源'))
    chk(info.get('任务名') == sn.get('name'),
        '★CSF 的名字与存档自带的 0x1360 **完全一致**（两条独立来源互证）',
        (info.get('任务名'), sn.get('name')))
    chk(info.get('任务名短') == c.get('name:all01mdsav'),
        '短名取的是 `...mdsav` 那条', info.get('任务名短'))
    chk(info.get('任务名短') and not info['任务名短'].startswith('军事行动：'),
        '★短名不带「军事行动：」前缀', info.get('任务名短'))

# ---------------- 5) 没地图时回退存档字段 ----------------
print('\n[5] 没地图 ⇒ 回退存档 0x1360（不许瞎查）')
d2 = SaveDoc(SAV)
d2.scenario_map_path = lambda: None
info2 = d2.__dict__.get('info') or {}
d3 = SaveDoc(SAV)
d3.scenario_map_path = lambda: None
d3.info = d3._read_info()
print('    来源 = %r   任务名 = %r' % (d3.info.get('任务名来源'), d3.info.get('任务名')))
chk(d3.info.get('任务名来源') == '存档 0x1360',
    '★没有地图时来源标注为「存档 0x1360」', d3.info.get('任务名来源'))
chk(d3.info.get('任务名') == d3.scenario_strings()['name'],
    '任务名等于存档 0x1360 原文', (d3.info.get('任务名'), d3.scenario_strings()['name']))

print('-' * 72)
print('§277 任务名（CSF 规则）：PASS %d / FAIL %d' % (OK, FAIL))
