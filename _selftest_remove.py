# -*- coding: utf-8 -*-
"""自检：删除建筑
  ① 往返对称：add_placed_building() → remove_building() 必须**逐字节回到原档**
  ② 删原生建筑：计数/表/格/长度都要自洽，且能 save+reload
"""
import sys, os, hashlib, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc                                     # noqa

SAV = r'E:\YURI\新建文件夹 (7)\OK1_光棱塔_远处空地.SAV'
if not os.path.exists(SAV):
    SAV = r'E:\YURI\新建文件夹 (11)\C1_对照_引擎自己放的.SAV'
print("样品:", SAV)


def md5(b):
    return hashlib.md5(b).hexdigest()


ok_all = True

# ---------------- ① 往返对称 ----------------
d = SaveDoc(SAV)
orig = bytes(d.plain)
n0 = len(d.buildings())
print("\n== ① 往返对称 ==")
print("  原档：明文 %d B，建筑 %d 栋，md5 %s" % (len(orig), n0, md5(orig)[:16]))

d._blds = None
r1 = d.add_placed_building('ATESLA', 100, 60, comp_mode='none')
after_add = bytes(d.plain)
n1 = len(d.buildings())
print("  插入后：明文 %d B（+%d），建筑 %d 栋，新键 0x%08X" % (
    len(after_add), len(after_add) - len(orig), n1, r1['key']))

# 新加的应该是最后一栋
hs, _ = d._bld_group()
idx_new = len(hs) - 1
d._blds = None
r2 = d.remove_building(idx_new)
after_rm = bytes(d.plain)
n2 = len(d.buildings())
print("  删除后：明文 %d B，建筑 %d 栋" % (len(after_rm), n2))
print("  删除详情:", r2['note'])
print("  未落到表里的键:", r2['untabled_keys'])

same = (after_rm == orig)
print("  ★ 逐字节回到原档：%s" % ('PASS ✅' if same else 'FAIL ❌'))
if not same:
    ok_all = False
    a, b = orig, after_rm
    print("     长度 orig=%d now=%d" % (len(a), len(b)))
    n = min(len(a), len(b))
    diffs = [i for i in range(n) if a[i] != b[i]]
    print("     首个差异偏移:", hex(diffs[0]) if diffs else None, " 差异字节数:", len(diffs))
    if diffs:
        i = diffs[0]
        print("     orig:", a[max(0, i - 24):i + 24].hex(' '))
        print("     now :", b[max(0, i - 24):i + 24].hex(' '))

# ---------------- ② 删原生建筑 ----------------
print("\n== ② 删原生建筑（第 0 栋）==")
d2 = SaveDoc(SAV)
before = d2.buildings()
print("  原 %d 栋，第 0 栋 = %s @(%d,%d)" % (
    len(before), before[0]['type'], before[0]['x'], before[0]['y']))
d2._blds = None
r3 = d2.remove_building(0)
after = d2.buildings()
print("  " + r3['note'])
print("  建筑 %d → %d 栋" % (len(before), len(after)))
gone = [b for b in after if (b['type'], b['x'], b['y']) ==
        (before[0]['type'], before[0]['x'], before[0]['y'])]
print("  被删的那栋是否还在列表里：%s" % ('❌ 还在' if gone else '✅ 已消失'))
cnt_ok = False
try:
    hs2, coff2 = d2._bld_group()
    cnt_ok = True
    print("  组计数自洽（_bld_group 未抛异常）：✅  %d 条记录" % len(hs2))
except Exception as e:
    print("  组计数自洽：❌ %s" % e)
    ok_all = False

# save + reload
out = os.path.join(tempfile.gettempdir(), '_rm_test.SAV')
try:
    d2.save(out, backup=False)
    d3 = SaveDoc(out)
    back = d3.buildings()
    print("  save→reload：✅ 读回 %d 栋（预期 %d）" % (len(back), len(after)))
    if len(back) != len(after):
        ok_all = False
        print("     ❌ 数量不符")
    # 逐栋核对
    sa = sorted((b['type'], b['x'], b['y']) for b in after)
    sb = sorted((b['type'], b['x'], b['y']) for b in back)
    print("  保存后建筑集合一致：%s" % ('✅' if sa == sb else '❌'))
    if sa != sb:
        ok_all = False
    os.remove(out)
except Exception as e:
    ok_all = False
    print("  save→reload：❌ %s" % e)

print("\n==== 总判定：%s ====" % ('PASS ✅' if ok_all else 'FAIL ❌'))
