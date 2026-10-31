# -*- coding: utf-8 -*-
"""§301 自检：抛射体对象里的分裂配置（ShrapnelWeapon 指针 / ShrapnelCount）。

为什么单独立一套（2026-10-29 玩家：「原版下还是弹框」「改 rules 没用」）：
  ① `Electricbounce` 的 `ShrapnelCount` 在 rules 里改成 6，读档后仍然只分裂 2 个；
  ② 探查后坐实：分裂配置**也存进存档了** —— 抛射体对象 `+0x290` = 子武器指针、
     `+0x294` = 数量，档内值与 rules 值逐个一致（2/5/5/3）。
  ③ 而 v1~v3 的崩溃（EAX=0 读 +0x2F6）就是"自造抛射体名在快照里没有对象 ⇒ 指针 NULL"。

本自检把语义钉死：
  ① 原版档里带分裂的抛射体 = rules 里声明分裂的那几个（数量逐个对上，允许档里多/少个别）；
  ② 不分裂的抛射体这两处必须是 0；
  ③ `set_projectile_shrapnel` 改的是明文里那 4 字节，长度不变、别的字节不动；
  ④ 改完能存盘、重新打开读到新值（往返一致）；
  ⑤ 越界/不存在的名字要报错，不能静默改坏；
  ⑥ 只读方法（projectile_shrapnel）不碰明文。

跑法：python _selftest_ev301_projectile_shrapnel.py
"""
import hashlib
import io
import os
import shutil
import struct
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc  # noqa: E402

SRC = r'E:/YURI/SAVE0665.SAV'
PASS, FAIL = [], []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)


if not os.path.exists(SRC):
    print('样本不存在：%s —— 跳过' % SRC)
    sys.exit(0)


def h(b):
    return hashlib.md5(bytes(b)).hexdigest()


doc = SaveDoc(SRC)
pl = doc.plain
print('样本 %s' % os.path.basename(SRC))

# ---------- ① 档内分裂配置 vs rules ----------
sh = {x['name']: x for x in doc.projectile_shrapnel()}
rules = doc._rules_all() or {}
want = {}
for s, kv in rules.items():
    if kv.get('ShrapnelWeapon'):
        try:
            c = int(str(kv.get('ShrapnelCount', '')).split(';')[0])
        except Exception:
            continue
        want[s] = c
print('  档内分裂抛射体 %d 个：%s' % (len(sh), {k: v['count'] for k, v in sh.items()}))
print('  rules 声明分裂的 %d 个：%s' % (len(want), want))
ck(len(sh) >= 3, '档内至少 3 个抛射体带分裂配置（实际 %d）' % len(sh))
same = [k for k in sh if k in want and want[k] == sh[k]['count']]
ck(len(same) == len(sh), '档内数量与 rules 逐个一致（一致 %d / 档内 %d）' % (len(same), len(sh)))
ck(all(v['ptr'] for v in sh.values()), '带分裂的都同时有子武器指针')

# ---------- ② 不分裂的必须是 0 ----------
allpj = {x['name']: x for x in doc.projectile_objects()}
zero = [n for n, x in allpj.items()
        if not (x.get('shrapnel_ptr') or x.get('shrapnel_count')) and n not in sh]
ck(len(zero) + len(sh) == len(allpj),
   '抛射体被分成「分裂 %d / 不分裂 %d」两类，无遗漏' % (len(sh), len(zero)))

# ---------- ⑥ 只读不碰明文 ----------
before = h(pl)
doc.projectile_shrapnel()
doc.projectile_objects()
ck(h(doc.plain) == before, '只读方法不碰明文')

# ---------- ③④ 写入 ----------
target = 'Electricbounce' if 'Electricbounce' in sh else next(iter(sh))
old_cnt = sh[target]['count']
tmpdir = tempfile.mkdtemp()
dst = os.path.join(tmpdir, 'ev301.SAV')
orig = bytes(doc.plain)
r = doc.set_projectile_shrapnel(target, old_cnt + 3)
ck(r['changed'] is True, 'set_projectile_shrapnel 报告已改（%s: %d -> %d）'
   % (target, r['old'], r['new']))
ck(len(doc.plain) == len(orig), '明文长度不变（%d 字节）' % len(doc.plain))

# 只有目标那 4 字节变了
diff = [i for i in range(len(orig)) if doc.plain[i] != orig[i]]
ck(all(r['off'] <= i < r['off'] + 4 for i in diff),
   '只有目标那 4 字节被改（实际变化 %d 字节）' % len(diff))
ck(len(diff) >= 1, '确实写进去了（变化 %d 字节）' % len(diff))

doc.save(dst)
ck(os.path.exists(dst), '存盘成功：%s' % os.path.basename(dst))

d2 = SaveDoc(dst)
sh2 = {x['name']: x for x in d2.projectile_shrapnel()}
ck(sh2[target]['count'] == old_cnt + 3,
   '重新打开读到新值（%s = %d）' % (target, sh2[target]['count']))
ck(len(sh2) == len(sh), '其他抛射体的分裂状态没变（%d 个）' % len(sh2))

# 改回 0 = 关闭分裂
r2 = d2.set_projectile_shrapnel(target, 0)
ck(r2['new'] == 0 and r2['changed'] is True, '允许写 0（关闭分裂）')
ck({x['name'] for x in d2.projectile_shrapnel()} == {k for k in sh2 if k != target},
   '写成 0 之后它从「分裂列表」里消失')

# ---------- ⑤ 越界要报错 ----------
for bad, desc in ((33, '超过 32'), (-1, '负数')):
    try:
        doc.set_projectile_shrapnel(target, bad)
        ck(False, '%s 应该报错' % desc)
    except ValueError:
        ck(True, '%s 报 ValueError' % desc)
    except Exception as e:
        ck(False, '%s 报的是 %r（应该是 ValueError）' % (desc, e))
try:
    doc.set_projectile_shrapnel('不存在的抛射体XYZ', 2)
    ck(False, '不存在的名字应该报错')
except KeyError:
    ck(True, '不存在的名字报 KeyError')

shutil.rmtree(tmpdir, ignore_errors=True)

print('\n结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败项：')
    for f in FAIL:
        print('  -', f)
    sys.exit(1)
