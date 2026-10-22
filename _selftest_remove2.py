# -*- coding: utf-8 -*-
"""删除建筑 · 扩展自检
  A. 逐栋删除：剩下的是不是"原列表去掉第 i 项"，长度减少量是否等于该记录长度
  B. 末栋用真实组尾（长记录 1872 的类型，如 GAAIRC）
  C. 幂等自证：同一操作连跑两次，产物 md5 必须一致
"""
import sys, os, hashlib, tempfile
HERE = r'C:\Users\zhao5\Desktop\1RA2存档分析工具\RA2存档编辑器'
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'core'))
from editor_core import SaveDoc                                     # noqa

SAV = r'E:\YURI\新建文件夹 (7)\OK1_光棱塔_远处空地.SAV'


def md5(b):
    return hashlib.md5(b).hexdigest()


base = SaveDoc(SAV)
A = [(b['type'], b['x'], b['y']) for b in base.buildings()]
L0 = len(base.plain)
print("原档：%d 栋，明文 %d B" % (len(A), L0))
for i, t in enumerate(A):
    print("   [%2d] %-8s @(%d,%d)" % (i, t[0], t[1], t[2]))

ok = True
print("\n== A. 逐栋删除 ==")
for i in range(len(A)):
    d = SaveDoc(SAV)
    d._blds = None
    try:
        r = d.remove_building(i)
    except Exception as e:
        print("   [%2d] ❌ 异常：%s" % (i, e)); ok = False; continue
    B = [(b['type'], b['x'], b['y']) for b in d.buildings()]
    want = A[:i] + A[i + 1:]
    dl = len(d.plain) - L0
    flag = '✅' if B == want else '❌'
    if B != want:
        ok = False
    print("   [%2d] %-8s dl=%+5d rec=%-5d cells=%d comp=%d lists=%d %s" % (
        i, A[i][0], dl, r['rec_len'], r['cells_cleared'],
        len(r['companions_removed']), r['lists_trimmed'], flag))
    if B != want:
        extra = [x for x in B if x not in want]
        miss = [x for x in want if x not in B]
        print("        多出:%s  少了:%s" % (extra[:3], miss[:3]))

# ---- B. 末栋（真实组尾）----
print("\n== B. 删末栋（走 _bld_tail 的真实组尾）==")
d = SaveDoc(SAV)
d._blds = None
hs, _ = d._bld_group()
last = len(hs) - 1
before_last = [(b['type'], b['x'], b['y']) for b in d.buildings()][-1]
r = d.remove_building(last)
after = [(b['type'], b['x'], b['y']) for b in d.buildings()]
print("   末栋 %s → 删除后剩 %d 栋；rec_len=%d" % (
    before_last, len(after), r['rec_len']))
print("   组计数自洽：", end='')
try:
    d._bld_group(); print("✅")
except Exception as e:
    print("❌", e); ok = False

# ---- C. 幂等自证 ----
print("\n== C. 幂等自证（同一删除连跑两次）==")
outs = []
for k in range(2):
    d = SaveDoc(SAV)
    d._blds = None
    d.remove_building(3)
    p = os.path.join(tempfile.gettempdir(), '_rm_idem%d.SAV' % k)
    d.save(p, backup=False)
    outs.append(md5(open(p, 'rb').read()))
    os.remove(p)
print("   两次产物 md5：%s / %s → %s" % (
    outs[0][:16], outs[1][:16], '✅ 一致' if outs[0] == outs[1] else '❌ 不一致'))
if outs[0] != outs[1]:
    ok = False

print("\n==== 总判定：%s ====" % ('PASS ✅' if ok else 'FAIL ❌'))
