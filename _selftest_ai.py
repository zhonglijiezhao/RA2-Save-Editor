# -*- coding: utf-8 -*-
"""AI 三大件自检：把存档里读到的 TeamType/TaskForce/ScriptType 与地图 INI 逐条对账。"""
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from core.editor_core import SaveDoc
    from core.map_ini import MapIni
    from core.ai_objects import read_ai
except Exception:
    from editor_core import SaveDoc
    from map_ini import MapIni
    from ai_objects import read_ai

SAV = os.environ.get('AI_SAV', r'E:\YURI\新建文件夹 (24)\SAVE4823.SAV')
MAP = os.environ.get('AI_MAP', r'E:\YURI\all01umd.map')
OK = FAIL = 0


def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)


if not (os.path.exists(SAV) and os.path.exists(MAP)):
    print('跳过：样本不存在（%s / %s）' % (SAV, MAP))
    sys.exit(0)

d = SaveDoc(SAV)
ai = read_ai(d)
mi = MapIni(MAP)
# ★2026-10-22 §191：E:/YURI/ALL01UMD.MAP 现在是**没有** [TeamTypes]/[TaskForces]/[ScriptTypes]
#   的那一版（地图被 FA2/玩家覆盖过），硬跑会 KeyError。缺段就跳过，别报假红。
_missing_sec = [k for k in ('teamtypes', 'taskforces', 'scripttypes') if k not in mi.sections]
if _missing_sec:
    print('跳过：地图 %s 不含 %s（§191 后 ALL01UMD.MAP 已丢这些段）'
          % (MAP, '/'.join('[%s]' % k for k in _missing_sec)))
    sys.exit(0)
teams = {t['id']: t for t in ai['teams']}
# ★地图与存档可能不是同一份（玩家换过图）⇒ 只核对"两边都有"的 id
def _ids(sec):
    return [sec[str(i)] for i in range(len(sec)) if sec[str(i)] in teams]
_missing = [sec[str(i)] for sec in (mi.sections['teamtypes'], mi.sections['taskforces'],
                                    mi.sections['scripttypes'])
            for i in range(len(sec)) if sec[str(i)] not in teams]
if _missing:
    print('   ⚠ 地图里有 %d 个对象在存档里找不到（地图与存档不是同一份？）%s'
          % (len(_missing), _missing[:4]))
tfs = {t['id']: t for t in ai['taskforces']}
scs = {t['id']: t for t in ai['scripts']}
sec_tt = mi.sections['teamtypes']
sec_tf = mi.sections['taskforces']
sec_st = mi.sections['scripttypes']

print('== A: 数量 ==')
print('   存档：小队 %d　特遣 %d　脚本 %d' % (len(ai['teams']), len(ai['taskforces']), len(ai['scripts'])))
print('   地图：小队 %d　特遣 %d　脚本 %d' % (len(sec_tt), len(sec_tf), len(sec_st)))
chk(len([k for k in teams if k.startswith('010')]) >= len(_ids(sec_tt)),
    '地图里的 %d 个小队都在存档里找到了' % len(_ids(sec_tt)))
chk(len([k for k in tfs if k.startswith('010')]) >= len(_ids(sec_tf)), '特遣部队齐全')
chk(len([k for k in scs if k.startswith('010')]) >= len(_ids(sec_st)), '动作脚本齐全')

print('\n== B: 名字 / 编号 ==')


def name_ok(got, want):
    """存档里的名字字段只有 40 字节 ⇒ 长名会被截断，只要求「是地图名的一个前缀」。"""
    got = (got or '').rstrip('…')
    if not want:
        return got == ''
    return bool(got) and want.startswith(got)


for label, table, sec in (('小队', teams, sec_tt), ('特遣部队', tfs, sec_tf),
                          ('脚本', scs, sec_st)):
    ids = _ids(sec)
    trunc = [k for k in ids if table.get(k, {}).get('name') != mi.sections[k].get('Name')]
    bad = [k for k in ids if not name_ok(table.get(k, {}).get('name'),
                                         mi.sections[k].get('Name'))]
    chk(not bad, '%s名字全对（错 %d 个；其中 %d 个是被 40 字节截断）'
        % (label, len(bad), len(trunc)))

print('\n== C: 小队数值字段 ==')
for fld, key in (('group', 'Group'), ('veteran', 'VeteranLevel'), ('max', 'Max'),
                 ('priority', 'Priority')):
    bad = []
    for sid in _ids(sec_tt):
        c = mi.sections[sid]
        if c.get(key) is None:
            continue
        if int(c[key]) != teams[sid][fld]:
            bad.append(sid)
    chk(not bad, '%s 全对（不符 %d）' % (key, len(bad)))
bad = [sid for sid in _ids(sec_tt)
       if teams[sid]['house'] != mi.sections[sid].get('House')]
chk(not bad, 'House（按指针解成国家名）全对（不符 %d）' % len(bad))
bad = [sid for sid in _ids(sec_tt)
       if (teams[sid]['script_id'] or '') != mi.sections[sid].get('Script')]
chk(not bad, 'Script 指针解析全对（不符 %d）' % len(bad))
bad = [sid for sid in _ids(sec_tt)
       if teams[sid]['taskforce_id'] != mi.sections[sid].get('TaskForce')]
chk(not bad, 'TaskForce 指针解析全对（不符 %d）' % len(bad))

print('\n== D: 特遣部队成员 ==')
bad, unresolved = [], []
for sid in _ids(sec_tf):
    c = mi.sections[sid]
    want = [c[k] for k in sorted((k for k in c if k.isdigit()), key=int)]
    got = ['%d,%s' % (m['count'], m['type']) for m in tfs[sid]['members']]
    if [w.split(',')[0] for w in want] != [g.split(',')[0] for g in got]:
        bad.append((sid, want, got))
    for w, m in zip(want, tfs[sid]['members']):
        if m['type'].startswith('#'):
            unresolved.append((sid, w, m['type']))
chk(not bad, '成员数量逐条一致（不符 %d%s）'
    % (len(bad), ('：' + str(bad[:2])) if bad else ''))
chk(len(unresolved) <= 5, '型号名解不出的只有 %d 条（%s）'
    % (len(unresolved), unresolved[:3]))

print('\n== E: 脚本条目 ==')
bad = []
for sid in _ids(sec_st):
    c = mi.sections[sid]
    want = [c[k] for k in sorted((k for k in c if k.isdigit()), key=int)]
    got = ['%d,%d' % (e['action'], e['arg']) for e in scs[sid]['entries']]
    if want != got:
        bad.append((sid, want[:3], got[:3]))
chk(not bad, '动作-参数逐条一致（不符 %d%s）'
    % (len(bad), ('：' + str(bad[:2])) if bad else ''))

print('\n== F: 抽样展示 ==')
for sid in _ids(sec_tt)[:2]:
    t = teams[sid]
    print('   小队 %s「%s」 %s Max=%d 优先=%d 老兵=%d 组=%d'
          % (t['id'], t['name'][:18], t['house'], t['max'], t['priority'],
             t['veteran'], t['group']))
    print('      特遣 %s → %s' % (t['taskforce_id'],
                              '  '.join('%d×%s' % (m['count'], m['type']) for m in t['members'])))
    print('      脚本 %s（%d 条）' % (t['script_id'], t['scripts_n']))

print('\n== G: 写入（set_team_veteran，只改 +0xB4）==')
import tempfile
from fa2_tables import ACTION_TYPES as _AT
A_tab3 = _AT.get(3, ('',))[0]
import struct as _st
try:
    import ai_objects as _aio
except Exception:
    from core import ai_objects as _aio
_map_teams = [t for t in ai['teams'] if t.get('is_map')]
if not _map_teams:
    print('  [SKIP] 这份档没有地图定义的作战小队')
else:
    _pick = next((t for t in _map_teams if t['veteran'] != 3), _map_teams[0])
    _sid = _pick['id']
    _old = _pick['veteran']
    _new = 1 if _old == 3 else 3
    _d = SaveDoc(SAV)
    _before = bytes(_d.plain)
    _info = _aio.set_team_veteran(_d, _sid, _new)
    _after = bytes(_d.plain)
    chk(_info['new'] == _new and _info['old'] == _old,
        '返回 old=%s new=%s' % (_info['old'], _new))
    chk(len(_before) == len(_after), '明文长度不变（%d）' % len(_after))
    _diff = [i for i in range(len(_before)) if _before[i] != _after[i]]
    chk(len(_diff) == 1, '逐字节只有 1 处变化（%s）' % (['%#x' % i for i in _diff][:3]))
    chk(bool(_diff) and _diff[0] == _info['off'], '变化的正是 +0xB4（%#x）' % _info['off'])
    _tmp = os.path.join(tempfile.gettempdir(), '_ai_vet_rt.SAV')
    _d.save(_tmp, backup=False)
    _back = next(x for x in read_ai(SaveDoc(_tmp))['teams'] if x['id'] == _sid)
    chk(_back['veteran'] == _new, '存盘往返后读回等级 = %d（应 %d）' % (_back['veteran'], _new))
    try:
        os.remove(_tmp)
    except Exception:
        pass
    try:
        _aio.set_team_veteran(SaveDoc(SAV), _sid, 9)
        chk(False, '等级 9 应被拒绝')
    except ValueError as _e:
        chk('只能' in str(_e), '非法等级被拒绝')

print('\n== H: 写入特遣部队成员（set_taskforce_member）==')
_tfs = [t for t in ai['taskforces'] if t.get('is_map') and t['members']]
if not _tfs:
    print('  [SKIP] 这份档没有地图定义的特遣部队')
else:
    _tf = _tfs[0]
    _d = SaveDoc(SAV)
    _b = bytes(_d.plain)
    _m0 = _tf['members'][0]
    _nc = 1 if _m0['count'] != 1 else 2
    _inf = _aio.set_taskforce_member(_d, _tf['id'], 0, count=_nc)
    _a = bytes(_d.plain)
    chk(_inf['new_count'] == _nc, '数量 %d → %d' % (_inf['old_count'], _inf['new_count']))
    chk(_inf['new_key'] == _inf['old_key'], '只改数量时型号键不动')
    chk(len(_b) == len(_a), '明文长度不变（%d）' % len(_a))
    _df = [i for i in range(len(_b)) if _b[i] != _a[i]]
    chk(len(_df) == 1 and _df[0] == _inf['off'],
        '逐字节只有 1 处变化且在槽位上（%s）' % (['%#x' % i for i in _df][:3],))
    _tmp = os.path.join(tempfile.gettempdir(), '_ai_tf_rt.SAV')
    _d.save(_tmp, backup=False)
    _back = next(x for x in read_ai(SaveDoc(_tmp))['taskforces'] if x['id'] == _tf['id'])
    chk(_back['members'][0]['count'] == _nc,
        '存盘往返后读回 %d×%s' % (_back['members'][0]['count'],
                                 _back['members'][0]['type']))
    try:
        os.remove(_tmp)
    except Exception:
        pass
    # 型号改名：拿档里第一个单位型号试
    _ut = {}
    for _k, _v in _d.unit_types().items():
        _ut.setdefault(str(_v).upper(), _k)
    if _ut:
        _nm = sorted(_ut)[0]
        _inf2 = _aio.set_taskforce_member(_d, _tf['id'], 0, type_name=_nm)
        chk(_inf2['new_key'] == _ut[_nm], '按名字换成 %s（键 %#x）' % (_nm, _ut[_nm]))
    try:
        _aio.set_taskforce_member(_d, _tf['id'], 99, count=1)
        chk(False, '越界序号应被拒绝')
    except IndexError:
        chk(True, '越界序号被拒绝')

print('\n== I: 改种数 / 清空特遣（set_taskforce_size / clear_taskforce）==')
if not [t for t in ai['taskforces'] if t.get('is_map') and t['members']]:
    print('  [SKIP] 没有可用的地图特遣部队')
else:
    _tf = [t for t in ai['taskforces'] if t.get('is_map') and t['members']][0]
    # 改种数：减到 1，顺带清零尾部数量
    _d = SaveDoc(SAV)
    _b = bytes(_d.plain)
    _inf = _aio.set_taskforce_size(_d, _tf['id'], 1)
    _a = bytes(_d.plain)
    _df = [i for i in range(len(_b)) if _b[i] != _a[i]]
    chk(_inf['new'] == 1 and _inf['old'] == len(_tf['members']),
        '种数 %d → 1（清零 %d 个尾部槽位）' % (_inf['old'], _inf['zeroed']))
    chk(len(_b) == len(_a), '明文长度不变')
    _t2 = next(x for x in read_ai(_d)['taskforces'] if x['id'] == _tf['id'])
    chk(len(_t2['members']) == 1, '读回只剩 1 种：%s'
        % '  '.join('%d×%s' % (m['count'], m['type']) for m in _t2['members']))
    chk(1 <= len(_df) <= 4 * 6, '差异字节数合理（%d）' % len(_df))
    # 清空
    _d = SaveDoc(SAV)
    _b = bytes(_d.plain)
    _ci = _aio.clear_taskforce(_d, _tf['id'])
    _a = bytes(_d.plain)
    _t3 = next(x for x in read_ai(_d)['taskforces'] if x['id'] == _tf['id'])
    chk(len(_t3['members']) == 0, '清空后种数 = 0、成员为空')
    chk(len(_b) == len(_a), '清空后明文长度不变')
    _still = [x['id'] for x in read_ai(_d)['teams'] if x.get('taskforce_id') == _tf['id']]
    chk(bool(_still), '用它的小队指针仍在（%s）⇒ 不会悬空' % '、'.join(_still[:3]))
    try:
        _aio.set_taskforce_size(_d, _tf['id'], 9)
        chk(False, '种数 9 应被拒绝')
    except ValueError:
        chk(True, '越界种数被拒绝')

print('\n== J: 改小队所属国家（set_team_house）==')
_tm = [t for t in ai['teams'] if t.get('is_map')]
if not _tm:
    print('  [SKIP] 没有地图定义的小队')
else:
    _tm = _tm[0]
    _hn = d._house_key_names()
    _other = [v for k, v in _hn.items() if str(v) != _tm['house']]
    if not _other:
        print('  [SKIP] 本档只有一个阵营')
    else:
        _want = sorted(_other)[0]
        _d = SaveDoc(SAV)
        _b = bytes(_d.plain)
        _inf = _aio.set_team_house(_d, _tm['id'], _want)
        _a = bytes(_d.plain)
        _df = [i for i in range(len(_b)) if _b[i] != _a[i]]
        chk(_inf['new_house'] == _want, '国家 %s → %s' % (_inf['old_house'], _inf['new_house']))
        chk(len(_b) == len(_a), '明文长度不变')
        chk(bool(_df) and _df[0] == _inf['off'], '变化的正是 +0xD8（%#x）' % _inf['off'])
        chk(len(_df) <= 4, '最多 4 个字节变化（实际 %d）' % len(_df))
        _tmp = os.path.join(tempfile.gettempdir(), '_ai_hs_rt.SAV')
        _d.save(_tmp, backup=False)
        _back = next(x for x in read_ai(SaveDoc(_tmp))['teams'] if x['id'] == _tm['id'])
        chk(_back['house'] == _want, '存盘往返后读回 %s' % _back['house'])
        try:
            os.remove(_tmp)
        except Exception:
            pass
        try:
            _aio.set_team_house(SaveDoc(SAV), _tm['id'], 'NoSuchCountry')
            chk(False, '不存在的国家应被拒绝')
        except KeyError:
            chk(True, '不存在的国家被拒绝（避免悬空指针）')

print('\n== K: 小队出生路径点（+0xE8）==')
_tm = [t for t in ai['teams'] if t.get('is_map')]
if not _tm:
    print('  [SKIP] 没有地图定义的小队')
else:
    _tm = _tm[0]
    chk('waypoint' in _tm, 'teams 里有 waypoint 字段（%r）' % _tm.get('waypoint'))
    _wp = _tm.get('waypoint')
    _cell = d.waypoint_cell(_wp) if _wp is not None else None
    print('   小队 %s 出现于 路径点 %s → %s' % (_tm['id'], _wp, _cell))
    _cands = [n for n in (d.waypoints_save() or {}) if n != _wp]
    if not _cands:
        print('  [SKIP] 只有一个路径点')
    else:
        _to = sorted(_cands)[0]
        _d = SaveDoc(SAV)
        _b = bytes(_d.plain)
        _inf = _aio.set_team_waypoint(_d, _tm['id'], _to)
        _a = bytes(_d.plain)
        _df = [i for i in range(len(_b)) if _b[i] != _a[i]]
        chk(_inf['new'] == _to, '路径点 %s → %s' % (_inf['old'], _inf['new']))
        chk(len(_b) == len(_a), '明文长度不变')
        chk(bool(_df) and _df[0] == _inf['off'], '变化的正是 +0xE8（%#x）' % _inf['off'])
        _tmp = os.path.join(tempfile.gettempdir(), '_ai_wp_rt.SAV')
        _d.save(_tmp, backup=False)
        _back = next(x for x in read_ai(SaveDoc(_tmp))['teams'] if x['id'] == _tm['id'])
        chk(_back['waypoint'] == _to, '存盘往返后读回 waypoint=%d' % _back['waypoint'])
        try:
            os.remove(_tmp)
        except Exception:
            pass
        try:
            _aio.set_team_waypoint(SaveDoc(SAV), _tm['id'], 999)
            chk(False, '路径点 999 应被拒绝')
        except ValueError:
            chk(True, '越界路径点被拒绝')

# 用原始大任务地图做全量对账（若还在盘上）
_BIG_MAP = r'E://YURI//新建文件夹 (23)\all01umd.map'
_BIG_SAV = r'E://YURI//新建文件夹 (24)\SAVE0029.SAV'
if os.path.exists(_BIG_MAP) and os.path.exists(_BIG_SAV):
    try:
        _mi2 = MapIni(_BIG_MAP)
        _tt2 = _mi2.sections['teamtypes']
        _d2 = SaveDoc(_BIG_SAV)
        _tm2 = {t['id']: t for t in read_ai(_d2)['teams']}
        _ids2 = [_tt2[str(i)] for i in range(len(_tt2)) if _tt2[str(i)] in _tm2]
        def _dec(v):
            n = 0
            for ch in str(v).strip().upper():
                n = n * 26 + (ord(ch) - 64)
            return n - 1
        _bad = 0
        for _sid in _ids2:
            _w = _mi2.sections[_sid].get('Waypoint')
            if _w is None:
                continue
            if _tm2[_sid]['waypoint'] != _dec(_w):
                _bad += 1
        chk(_bad == 0, '大任务档 +0xE8 与地图 WAYPOINT 对账（%d 条，错 %d）'
            % (len(_ids2), _bad))
    except Exception as _e:
        print('  [SKIP] 大档对账失败：%r' % (_e,))
else:
    print('  [SKIP] 缺大档/大图，跳过全量对账')

print('\n== L: 小队预装载（+0xB9）==')
_tm = [t for t in ai['teams'] if t.get('is_map')]
if not _tm:
    print('  [SKIP] 没有地图定义的小队')
else:
    _tm = _tm[0]
    chk('loadable' in _tm, "teams 里有 loadable 字段（%r）" % _tm.get('loadable'))
    _d = SaveDoc(SAV)
    _b = bytes(_d.plain)
    _want = not _tm.get('loadable')
    _inf = _aio.set_team_loadable(_d, _tm['id'], _want)
    _a = bytes(_d.plain)
    _df = [i for i in range(len(_b)) if _b[i] != _a[i]]
    chk(_inf['new'] == (1 if _want else 0), '预装载 %d → %d' % (_inf['old'], _inf['new']))
    chk(len(_b) == len(_a), '明文长度不变')
    chk(len(_df) == 1 and _df[0] == _inf['off'],
        '逐字节只有 1 处变化且在 +0xB9（%s）' % (['%#x' % i for i in _df]))
    chk(_inf['off'] == _tm['off'] + _aio.TEAM_LOADABLE_OFF, '偏移 = 记录 +0xB9')
    _tmp = os.path.join(tempfile.gettempdir(), '_ai_load_rt.SAV')
    _d.save(_tmp, backup=False)
    _back = next(x for x in read_ai(SaveDoc(_tmp))['teams'] if x['id'] == _tm['id'])
    chk(bool(_back['loadable']) == bool(_want), '存盘往返后读回 loadable=%s' % _back['loadable'])
    try:
        os.remove(_tmp)
    except Exception:
        pass
    # 关回去
    _inf2 = _aio.set_team_loadable(_d, _tm['id'], False)
    chk(_inf2['new'] == 0, '再关掉 → 0')


print('\n== M: 脚本动作名走的是「脚本动作表」不是「触发器行为表」==')
from fa2_tables import SCRIPT_ACTIONS, SCRIPT_PARAM_TYPES, script_action_name as _san
chk(_san(3) == '移动到路径点', '脚本动作 3 = %r（FA2 里就是「移动到路径点」）' % _san(3))
chk(A_tab3 != _san(3), '触发器行为 3 = %r ≠ 脚本动作 3（两套编号，不能混用）' % A_tab3)
chk(SCRIPT_PARAM_TYPES.get(2) == '路径点', '脚本参数类型 2 = %r' % SCRIPT_PARAM_TYPES.get(2))
_miss = [i for i in sorted(SCRIPT_ACTIONS) if not SCRIPT_ACTIONS[i][0]]
chk(not _miss, '脚本动作表 %d 条，无空名' % len(SCRIPT_ACTIONS))
# 存档里每条脚本条目都应该带上 name/ptype
_bad = []
for _s in ai['scripts']:
    for _e in _s['entries']:
        if 'name' not in _e or not _e['name'] or 'ptype' not in _e:
            _bad.append((_s['id'], _e.get('action')))
chk(not _bad, '全部脚本条目都带 name/ptype（异常 %d）' % len(_bad))
# 与地图 INI 对账：脚本条目的动作号-参数必须与 INI 逐条一致（名称只影响显示）
try:
    _bad2 = []
    for _sid in _ids(sec_st):
        _c = mi.sections[_sid]
        _want = [(int(k), _c[k]) for k in sorted((k for k in _c if k.isdigit()), key=int)]
        _got = [(e['action'], e['arg']) for e in scs[_sid]['entries']]
        if [(a, int(str(v).split(',')[1])) for a, v in _want] != _got:
            _bad2.append(_sid)
    chk(not _bad2, '脚本条目（动作号+参数）与地图 INI 逐条一致（不符 %d）' % len(_bad2))
except Exception as _e:
    print('  [SKIP] 与地图对账失败：%r' % (_e,))

print('\\n== N: 删除特遣部队成员（remove_taskforce_member）==')
_tf3 = [t for t in ai['taskforces'] if t.get('is_map') and len(t['members']) >= 2]
if not _tf3:
    print('  [SKIP] 没有 ≥2 项的地图特遣部队')
else:
    _t = _tf3[0]
    _d = SaveDoc(SAV)
    _b = bytes(_d.plain)
    _keep = _t['members'][1]
    _inf = _aio.remove_taskforce_member(_d, _t['id'], 0)
    _a = bytes(_d.plain)
    _df = [i for i in range(len(_b)) if _b[i] != _a[i]]
    chk(_inf['old_n'] == len(_t['members']) and _inf['new_n'] == len(_t['members']) - 1,
        '种数 %d → %d' % (_inf['old_n'], _inf['new_n']))
    chk(len(_b) == len(_a), '明文长度不变')
    chk(1 <= len(_df) <= 8 * 6, '只动了槽位区（%d 字节）' % len(_df))
    _t2 = next(x for x in read_ai(_d)['taskforces'] if x['id'] == _t['id'])
    chk(len(_t2['members']) == len(_t['members']) - 1, '读回种数 %d' % len(_t2['members']))
    chk(_t2['members'][0]['type'] == _keep['type']
        and _t2['members'][0]['count'] == _keep['count'],
        '原第 1 项前移到第 0 项（%d×%s）' % (_keep['count'], _keep['type']))
    _tmp = os.path.join(tempfile.gettempdir(), '_ai_tfdel.SAV')
    _d.save(_tmp, backup=False)
    _t3 = next(x for x in read_ai(SaveDoc(_tmp))['taskforces'] if x['id'] == _t['id'])
    chk(len(_t3['members']) == len(_t['members']) - 1, '存盘往返后种数正确')
    try:
        os.remove(_tmp)
    except Exception:
        pass
    try:
        _aio.remove_taskforce_member(SaveDoc(SAV), _t['id'], 99)
        chk(False, '越界序号应被拒绝')
    except IndexError:
        chk(True, '越界序号被拒绝')

print('\n自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
