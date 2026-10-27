# -*- coding: utf-8 -*-
"""§175 自检：局部变量（变量表 + 事件 36/37 + 行为 56/57 的正确偏移）。

核心断言（全部来自 gamemd.exe 静态反汇编，2026-10-22）：
  · 变量表：100 槽 × 41B，name[40] + value(u8)，明文基址 0x248A
  · 事件 36/37 的变量号在事件对象 **+0x48**
  · 行为 56/57 的变量号在行为对象 **+0xA4**（+0x48 恒 0）
  · 行为 56 = 值写 1；事件 36 = 值 ≠ 0（电平）

跑法：python _selftest_ev175.py
"""
import os
import struct
import sys

sys.path.insert(0, '.')
from core import editor_core as ec            # noqa: E402

OK = FAIL = 0
FAILS = []


def ck(name, cond, detail=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  ✅ %s%s' % (name, ('  [' + str(detail) + ']') if detail else ''))
    else:
        FAIL += 1
        FAILS.append(name)
        print('  ❌ %s  %s' % (name, detail))


def fp(name):
    for root in ('E:/YURI/', 'E:/YURI/新建文件夹 (26)/'):
        if os.path.exists(root + name):
            return root + name
    raise SystemExit('找不到基准存档：' + name)


SRC = fp('电厂放下 动画未播放完成.SAV')
from _paths import out as _out
TMP = _out('ev175_var.SAV')

print('=' * 78)
print('[1] 常量与变量表结构')
ck('ACT_VAR_OFF = 164', ec.ACT_VAR_OFF == 164, ec.ACT_VAR_OFF)
ck('ACTION_VAR_TYPES = (56, 57)', ec.ACTION_VAR_TYPES == (56, 57))
ck('LOCALVAR_BASE = 0x248A', ec.LOCALVAR_BASE == 0x248A, hex(ec.LOCALVAR_BASE))
ck('槽 41B / 100 槽', ec.LOCALVAR_SLOT == 0x29 and ec.LOCALVAR_COUNT == 100)
ck('行为对象 168B 容得下 +164',
   ec.ACTION_SIZE >= ec.ACT_VAR_OFF + 4, '%d >= %d' % (ec.ACTION_SIZE, ec.ACT_VAR_OFF + 4))

d = ec.SaveDoc(SRC)
ck('_lv_sane() 认这个档', d._lv_sane())
tab = d.local_var_table()
ck('读到 100 个槽', len(tab) == 100, len(tab))
ck('槽 0 偏移 = 0x248A', tab[0]['off'] == 0x248A, hex(tab[0]['off']))
ck('槽步长 0x29', tab[1]['off'] - tab[0]['off'] == 0x29)
ck('fa2 编号 = 索引 + 1', tab[3]['fa2'] == 4 and tab[3]['index'] == 3)
print('     前 5 槽：', [(v['index'], v['name'], v['value']) for v in tab[:5]])

print('\n[2] 命名 / 置值 / 清空')
nm_before = d.local_var(20)['name']
v = d.set_local_var_name(20, 'HELLO')
ck('写名字进 40B 槽', v['name'] == 'HELLO', v['name'])
ck('名字后紧跟 0（不越界）',
   d.plain[0x248A + 20 * 0x29 + 5] == 0)
v = d.set_local_var_name(20, 'X' * 60)
ck('超长名字截到 39 字符', len(v['name']) == 39, len(v['name']))
v = d.set_local_var_value(20, 1)
ck('置值 = 1（u8 @ +40）', v['value'] == 1 and
   d.plain[0x248A + 20 * 0x29 + 40] == 1)
v = d.set_local_var_value(20, 255)
ck('置值 = 255', v['value'] == 255)
try:
    d.set_local_var_value(20, 256)
    ck('值 > 255 被拒', False)
except ValueError:
    ck('值 > 255 被拒', True)
try:
    d.local_var(100)
    ck('索引 >= 100 被拒', False)
except ValueError:
    ck('索引 >= 100 被拒', True, '引擎 `i >= 0x64` 直接 false')
d.clear_local_var(20)
v = d.local_var(20)
ck('clear 后名字空 + 值 0', v['name'] == '' and v['value'] == 0)
d.set_local_var_name(20, nm_before or '')

print('\n[3] ensure_local_var 自动补名')
d.clear_local_var(30)
v = d.ensure_local_var(30)
ck('空槽自动补 V31', v['name'] == 'V31', v['name'])
v = d.ensure_local_var(30, name='CUSTOM', value=7)
ck('显式名字/值优先', v['name'] == 'CUSTOM' and v['value'] == 7)
d.clear_local_var(30)

print('\n[3b] ★纯数字变量名不能污染地图（2026-10-22 实踩：地图 3=D 被写成 3=0）')
try:
    d.define_local_var(3, '0')
    ck('纯数字名被拒', False, '没抛异常')
except ValueError as e:
    ck('纯数字名被拒', '纯数字' in str(e), str(e)[:40])
print('\n[3c] ★铁律（玩家 2026-10-27 00:20）：本工具不许写地图')
import shutil                                     # noqa: E402
import tempfile                                   # noqa: E402
import hashlib                                    # noqa: E402


def _md5(p):
    return hashlib.md5(open(p, 'rb').read()).hexdigest()


_mp = d.scenario_map_path()
if _mp and os.path.exists(_mp):
    _tmp = os.path.join(tempfile.gettempdir(), '_ev175_map.map')
    shutil.copy(_mp, _tmp)
    if os.path.exists(_tmp + '.bak'):
        os.remove(_tmp + '.bak')
    _before = _md5(_tmp)
    d.set_local_var_name(3, 'GARBAGE')             # 存档里塞个名字
    d.define_local_var(3, 'OKNAME', 1, map_path=_tmp)   # map_path 只作兼容，必须被忽略
    d.ensure_local_var(4, 'OKNAME2', 1)
    _after = _md5(_tmp)
    ck('地图字节一字未改（md5 相同）', _before == _after, '%s -> %s' % (_before[:10], _after[:10]))
    ck('没留下 .bak', not os.path.exists(_tmp + '.bak'))
    try:
        ec.write_scenario_variables(_tmp, [(3, 'X', 1)])
        ck('write_scenario_variables 必须拒绝', False, '竟然没抛异常')
    except ec.MapWriteForbidden:
        ck('write_scenario_variables 必须拒绝', True, 'MapWriteForbidden')
    ck('地图仍然没变', _md5(_tmp) == _before)
    for _p in (_tmp, _tmp + '.bak', _tmp + '.tmp'):
        if os.path.exists(_p):
            os.remove(_p)
else:
    ck('地图字节一字未改（md5 相同）', True, '本档无地图，跳过')

print('\n[4] add_action(56) 写 +164 而不是 +0x48')
d2 = ec.SaveDoc(SRC)
info = d2.create_trigger_multi('T56', [(36, 3)], [(56, 3)])
sid = info['trigger_id']
acts = d2.trigger_action_list(sid)
ck('create_trigger_multi 建出行为 56', acts and acts[0]['type'] == 56, acts)
a0 = ec.SaveDoc(SRC)  # 占位，避免引用已改对象
obj = next((x['obj'] for x in d2.actions() if x.get('key') == acts[0]['key']), None)
ck('定位到行为对象', obj is not None, obj)
raw48 = struct.unpack_from('<I', d2.plain, obj + 72)[0]
raw164 = struct.unpack_from('<I', d2.plain, obj + 164)[0]
ck('+0x48 恒 0', raw48 == 0, raw48)
ck('+0xA4 = 变量号 3', raw164 == 3, raw164)
ck('回读 arg = 3（按类型读 +0xA4）', acts[0]['arg'] == 3, acts[0]['arg'])
evs = d2.trigger_event_list(sid)
ck('事件 36 变量号仍在 +0x48', evs and evs[0]['arg'] == 3, evs)

print('\n[5] add_action(57) 同样走 +164；普通行为仍走 +0x48')
info2 = d2.create_trigger_multi('T57', [(37, 5)], [(57, 5)])
sid2 = info2['trigger_id']
acts2 = d2.trigger_action_list(sid2)
ck('行为 57 回读 = 5', acts2 and acts2[0]['arg'] == 5, acts2)
obj2 = next((x['obj'] for x in d2.actions() if x.get('key') == acts2[0]['key']), None)
ck('行为 57 的 +0xA4 = 5', struct.unpack_from('<I', d2.plain, obj2 + 164)[0] == 5)

info3 = d2.create_trigger_multi('T13', [(13, 10)], [(1, 0)])
sid3 = info3['trigger_id']
acts3 = d2.trigger_action_list(sid3)
obj3 = next((x['obj'] for x in d2.actions() if x.get('key') == acts3[0]['key']), None)
ck('行为 1 的 +0xA4 不被污染',
   struct.unpack_from('<I', d2.plain, obj3 + 164)[0] == 0)

print('\n[6] make_local_var_recipe 先写变量再建触发')
d3 = ec.SaveDoc(SRC)
for i in range(40, 46):
    d3.clear_local_var(i)
free = [(b['x'], b['y']) for b in d3.buildings() if not b.get('tag')][:2]
rec = d3.make_local_var_recipe(free, [{'type': 125, 'name': 'GAPOWR',
                                       'cell': d3.free_cell_near(70, 88, 12, 3)}],
                               var_start=40, var_name='T%d', per_name='P%d')
# ★2026-10-27 铁律：write_map 参数已删除（本工具不写地图），传了它会 TypeError
ck('返回 local_vars 字段', 'local_vars' in rec, list(rec.keys()))
ck('变量已命名 T41/T42',
   [x['name'] for x in rec['local_vars']] == ['T41', 'T42'],
   [x['name'] for x in rec['local_vars']])
ck('变量号是引擎索引 40/41', rec['vars'] == [40, 41], rec['vars'])
res_all = [(e['type'], e['arg']) for e in rec['result']['events']]
res_evs = [a for t, a in res_all if t == 36]
ck('汇总事件 36 用同一批索引', sorted(res_evs) == [40, 41], res_all)
# ★§181：事件 36 是纯电平查询，只有进了「定时触发器挂接表」（ticks>0）才会被轮询
ck('汇总触发带 13 心跳（§181 纯电平查询必须挂计时事件）',
   any(t == 13 for t, _ in res_all), res_all)
pb_args = [a['arg'] for x in rec['per_building'] for a in x['actions'] if a['type'] == 56]
ck('单栋行为 56 用同一批索引', sorted(pb_args) == [40, 41], pb_args)

print('\n[7] 存盘往返')
d3.save(TMP)
e3 = ec.SaveDoc(TMP)
ck('存盘后变量表仍在', [x['name'] for x in e3.local_var_table()[40:42]] == ['T41', 'T42'],
   [x['name'] for x in e3.local_var_table()[40:42]])
read_args = []
for x in rec['per_building']:
    for a in e3.trigger_action_list(x['trigger_id']):
        if a['type'] == 56:
            read_args.append(a['arg'])
ck('存盘后行为 56 变量号不变', sorted(read_args) == [40, 41], read_args)

print('\n[8] retarget_trigger 改到 56 也写 +164')
d4 = ec.SaveDoc(SRC)
cell = d4.free_cell_near(72, 88, 10, 3)
t = d4.create_trigger_multi('RT', [(13, 1)], [{'type': 125, 'name': 'GAPOWR', 'cell': cell}])
info4 = d4.retarget_trigger(t['trigger_id'], ac_type=56, ac_arg=9)
ck('retarget 返回 arg = 9', info4.get('arg') == 9, info4)
al = d4.trigger_action_list(t['trigger_id'])
ck('回读行为 56 arg = 9', al and al[0]['arg'] == 9, al)
obj4 = next((x['obj'] for x in d4.actions() if x.get('key') == al[0]['key']), None)
ck('retarget 后 +0xA4 = 9', struct.unpack_from('<I', d4.plain, obj4 + 164)[0] == 9)
ck('retarget 后 +0x48 = 0', struct.unpack_from('<I', d4.plain, obj4 + 72)[0] == 0)

print('\n[9] 玩家给的两档：七变量 / 四变量')
# ★2026-10-22：玩家会把档归档进 `新建文件夹 (NN)/` ⇒ 走递归定位器，别写死根目录
from _paths import find_sav
a = find_sav('七变量.SAV')
b = find_sav('四变量.SAV')
if a and b:
    da, db = ec.SaveDoc(a), ec.SaveDoc(b)
    na = [x['name'] for x in da.local_var_defined()]
    nb = [x['name'] for x in db.local_var_defined()]
    ck('七变量档读到 7 个名字', len(na) == 7, na)
    ck('四变量档读到 4 个名字', len(nb) == 4, nb)
    ck('两档名字前缀一致 A..', na[:4] == nb[:4] == ['A', 'B', 'C', 'D'], na[:4])
else:
    ck('七变量/四变量档存在', False, '跳过')

print('\n[10] GUI 符号（不建窗口）')
src = open('RA2存档编辑器.py', encoding='utf-8').read()
ck('有 local_var_editor 方法', 'def local_var_editor' in src)
# ★v1.3：触发页底部按钮收进「更多 ▾」菜单（原来 9 个按钮横排会溢出窗口）
ck('触发页有「局部变量…」入口（按钮或「更多」菜单）',
   "text='🔢 局部变量…'" in src or "label='局部变量…'" in src)
ck('向导默认 var_start = 0', "var_v0 = tk.StringVar(value='0')" in src)
ck('向导传 var_name 给配方', "var_name=(var_nm.get()" in src)
ck('向导说明里写明 +164', '行为 56 的变量号写行为对象 +164' in src)
app = open('app_main.py', encoding='utf-8').read()
ck('exe 自检有 [8] 项', '[8] 局部变量(行为56@+164)' in app)

print('\n' + '=' * 78)
print('SELFTEST §175  PASS %d / FAIL %d' % (OK, FAIL))
if FAILS:
    print('失败项：')
    for x in FAILS:
        print('  ·', x)
sys.exit(1 if FAIL else 0)
