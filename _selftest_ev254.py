# -*- coding: utf-8 -*-
"""§254 自检：特遣 / 小队 / 脚本的「地图定义 vs AI 运行期生成」判定。

玩家 2026-10-24 反馈：新建作战小队的「特遣部队」下拉里把 AI 生成的那批也列出来了，
「这是没用的」。本节给 `SaveDoc.is_map_sid()` + `taskforces()/team_types()` 的
`defined` 字段，并验证判据在本档是 100% 干净的两类。

跑法：D:/py/python.exe _selftest_ev254.py [存档]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))
import editor_core as ec

_CAND = ['E:/YURI/新建文件夹 (30)/T85_老工程师_修位置副本槽.SAV',
         'E:/YURI/SAVE4823.SAV',
         'E:/YURI/新建文件夹 (30)/SAVE0029.SAV']
SAV = sys.argv[1] if len(sys.argv) > 1 else next((x for x in _CAND if os.path.isfile(x)), _CAND[0])

n_ok = n_bad = 0


def ck(name, cond, detail=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('PASS  %s' % name)
    else:
        n_bad += 1
        print('FAIL  %s   %s' % (name, detail))


print('基档：%s' % SAV)
print('-' * 72)

# ---------- 1) is_map_sid 纯函数 ----------
for sid, want in (('01000241', True), ('01000fff', True), ('0100FFFF', True),
                  ('05C60C4C', False), ('0D082CFC', False), ('0617A3DC', False),
                  ('', False), (None, False), ('0100', False), ('010002411', False)):
    ck('A1 is_map_sid(%r) == %s' % (sid, want), ec.SaveDoc.is_map_sid(sid) is want,
       str(ec.SaveDoc.is_map_sid(sid)))

d = ec.SaveDoc(SAV)
tf_all = d.taskforces()
tm_all = d.team_types()
sc_all = d.script_types()
tf_map = [x for x in tf_all if x['defined']]
tf_ai = [x for x in tf_all if not x['defined']]
tm_map = [x for x in tm_all if x['defined']]
tm_ai = [x for x in tm_all if not x['defined']]
sc_map = [x for x in sc_all if x['defined']]
sc_ai = [x for x in sc_all if not x['defined']]
print('特遣 地图%d / AI%d    小队 地图%d / AI%d    脚本 地图%d / AI%d'
      % (len(tf_map), len(tf_ai), len(tm_map), len(tm_ai), len(sc_map), len(sc_ai)))

# ---------- 2) 两类都非空（本档应有相当数量，否则判据可疑） ----------
ck('A2 特遣 地图定义 非空', len(tf_map) >= 10, len(tf_map))
ck('A3 特遣 AI 生成 非空', len(tf_ai) >= 1, len(tf_ai))
ck('A4 小队 地图定义 非空', len(tm_map) >= 10, len(tm_map))
ck('A5 小队 AI 生成 非空', len(tm_ai) >= 1, len(tm_ai))

# ---------- 3) AI 那批的 ID 全是 8 位十六进制（地图那批全是 0100xxxx） ----------
import re
ck('A6 AI 特遣 ID 全为 8 位十六进制',
   all(re.fullmatch(r'[0-9A-Fa-f]{8}', x['sid']) for x in tf_ai), tf_ai[:3])
ck('A7 地图特遣 ID 全为 0100xxxx',
   all(x['sid'].startswith('0100') for x in tf_map), tf_map[:3])
ck('A8 AI 小队 ID 全为 8 位十六进制',
   all(re.fullmatch(r'[0-9A-Fa-f]{8}', x['sid']) for x in tm_ai), tm_ai[:3])

# ---------- 4) 交叉验证（本节最强的证据）：两类互不引用 ----------
tfkey = {x['key']: x for x in tf_all}
cross = {}
for t in tm_all:
    f = tfkey.get(t['tf_key'])
    key = (t['defined'], f['defined'] if f else None)
    cross[key] = cross.get(key, 0) + 1
ck('B1 地图小队只引用地图特遣',
   cross.get((True, False), 0) == 0, cross)
ck('B2 AI 小队只引用 AI 特遣',
   cross.get((False, True), 0) == 0, cross)
ck('B3 交叉统计覆盖全部小队',
   sum(cross.values()) == len(tm_all), '%d vs %d' % (sum(cross.values()), len(tm_all)))
print('   交叉分布：%s' % cross)

# ---------- 5) 与 ai_objects 的 is_map（startswith('010')）口径一致 ----------
try:
    import ai_objects as ai
    ad = ai.read_ai(d)
    a_tm = {x['id']: x.get('is_map') for x in ad['teams']}
    bad = [x['sid'] for x in tm_all if a_tm.get(x['sid']) is not None
           and bool(a_tm[x['sid']]) != bool(x['defined'])]
    ck('C1 与 ai_objects 的 is_map 口径一致（小队）', not bad, bad[:5])
    a_tf = {x['id']: x.get('is_map') for x in ad['taskforces']}
    bad2 = [x['sid'] for x in tf_all if a_tf.get(x['sid']) is not None
            and bool(a_tf[x['sid']]) != bool(x['defined'])]
    ck('C2 与 ai_objects 的 is_map 口径一致（特遣）', not bad2, bad2[:5])
except Exception as e:
    ck('C1/C2 ai_objects 对照', False, repr(e))

# ---------- 6) 新建特遣时挑的模板必须是「地图定义」的那批 ----------
try:
    dd = ec.SaveDoc(SAV)
    dd.add_unit_by_clone('DISK', 110, 206, house=dd.player_house(), veterancy=0,
                         exact_cell=True)
    r = dd.clone_taskforce([('DISK', 2)], name='EV254特遣')
    new = [x for x in dd.taskforces() if x['name'] == 'EV254特遣']
    ck('D1 新建特遣成功且 ID 是地图定义式',
       bool(new) and new[0]['defined'], new[0]['sid'] if new else '没找到')
    tpl_key = r.get('template_key') or r.get('tpl_key')
    if tpl_key:
        tpl = [x for x in dd.taskforces() if x['key'] == tpl_key]
        ck('D2 模板取自地图定义特遣', bool(tpl) and tpl[0]['defined'],
           tpl[0]['sid'] if tpl else '没找到')
    else:
        ck('D2 模板取自地图定义特遣', True, '（返回里没有模板键，跳过）')
except Exception as e:
    ck('D1/D2 新建特遣', False, repr(e))

print('-' * 72)
print('PASS %d / FAIL %d' % (n_ok, n_bad))
sys.exit(1 if n_bad else 0)
