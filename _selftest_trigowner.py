# -*- coding: utf-8 -*-
"""自检：**触发的「所属方（国家）」** = 触发记录 `+128` = 目标阵营 House 块 `+72`。

依据（玩家给的成对档 `刷光棱塔韩国.SAV` / `刷光棱塔德国.SAV`：两份明文长度完全一致、
触发记录区段只差 3 字节，其中 `+128` 正是国家）：
  · 韩国档 `01000002` 的 `+128 = 0x1023ac30` = Alliance 块 +72
  · 德国档 `01000002` 的 `+128 = 0x1023a8b0` = Germans 块 +72

断言：
  A) 两档 12 个阵营的 House 块 `+72` 两两不同（= 每阵营一个"触发所属方对象"键）
  B) 两档 `01000002` 的国家读出来分别是 Alliance / Germans
  C) **写进去能复现**：把韩国档的 B 改成 Germans 后保存重读 ⇒ 国家 = Germans，
     且该记录与德国档同一条记录**只差 1 字节**（+166，运行期指针低位）⇒ 国家就是这一个字段
"""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from core.editor_core import SaveDoc

OK = FAIL = 0
def chk(c, m):
    global OK, FAIL
    if c:
        OK += 1; print('  [PASS] ' + m)
    else:
        FAIL += 1; print('  [FAIL] ' + m)

KR = r'E:/YURI/刷光棱塔韩国.SAV'
DE = r'E:/YURI/刷光棱塔德国.SAV'

if not (os.path.exists(KR) and os.path.exists(DE)):
    print('SKIP: 缺成对样本（刷光棱塔韩国.SAV / 刷光棱塔德国.SAV）')
    sys.exit(0)

a, b = SaveDoc(KR), SaveDoc(DE)

print('== A: House 块 +72 = 每阵营的"触发所属方对象"键 ==')
ka = {h: a.house_trigger_owner_key(h) for h in a.house_keys()}
kb = {h: b.house_trigger_owner_key(h) for h in b.house_keys()}
chk(len(ka) == 12 and all(v for v in ka.values()), '12 个阵营都读到键（%d 个非空）' % sum(1 for v in ka.values() if v))
chk(len(set(ka.values())) == len(ka), '12 个键两两不同（无重复）')
chk(ka == kb, '两档同一阵营的键一致（跨档稳定）')

print('== B: 触发 +128 = 国家 ==')
ta = [r for r in a.records if r.kind == '触发' and r.name == 'B'][0]
tb = [r for r in b.records if r.kind == '触发' and r.name == 'B'][0]
chk(a.trigger_owner_name(ta) == 'Alliance', '韩国档 B 的国家 = Alliance（实测 %s）' % a.trigger_owner_name(ta))
chk(b.trigger_owner_name(tb) == 'Germans', '德国档 B 的国家 = Germans（实测 %s）' % b.trigger_owner_name(tb))
chk(a.trigger_owner_name([r for r in a.records if r.kind == '触发' and r.name == 'A'][0]) == 'Neutral',
    'A 触发（未改）国家 = Neutral')

print('== C: 写入能复现（改国家 = 只改 4 字节） ==')
old, new = a.set_trigger_owner(ta, 'Germans')
chk((old, new) == (ka['Alliance'], ka['Germans']), '改写的旧/新键正确（%08x → %08x）' % (old, new))
tmp = os.path.join(HERE, '_exp', '_selftest_trigowner.SAV')
if os.path.exists(tmp):
    os.remove(tmp)
a.save(tmp, backup=False)
c = SaveDoc(tmp)
tc = [r for r in c.records if r.kind == '触发' and r.name == 'B'][0]
chk(c.trigger_owner_name(tc) == 'Germans', '保存重读后国家 = Germans（实测 %s）' % c.trigger_owner_name(tc))
s1 = bytes(c.plain[tc.off:tc.off + 200])
s2 = bytes(b.plain[tb.off:tb.off + 200])
diff = [(i, s1[i], s2[i]) for i in range(200) if s1[i] != s2[i]]
chk(len(diff) <= 1, '与德国档同一条记录只差 %d 字节 %s（+166 是运行期指针低位）' % (len(diff), diff))
os.remove(tmp)

print('\n自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
