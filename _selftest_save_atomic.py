# -*- coding: utf-8 -*-
"""自检：**保存链路的数据安全**（2026-11-06 审核 P0/P1 的回归钉）。

背景（审核报告 §2.1/§2.2/§2.3/§2.4）：
  · 旧实现在"原地保存 + surgery 失败"时会 `os.remove(out_path)` 删掉原档，
    紧接着 `rebuild(self.path, ...)` 找不到源文件 ⇒ FileNotFoundError + 存档消失；
  · 旧实现直接写目标文件（非原子）⇒ 写一半断电就是半截档；
  · 旧实现另存为覆盖已存在文件时不备份；
  · 旧实现保存结束时无条件 `self.plain = bytearray(new_plain)` ⇒
    保存那几秒里的编辑被静默吞掉，状态栏还显示"已保存"。

本自检钉四件事（全部在临时目录里跑，不碰样本）：
  A. 两个 writer 都失败 ⇒ 原档 **md5/大小不变**、无 `.rsed-tmp` 残留、备份已生成；
  B. 原地保存正常 ⇒ 块布局 429/429（按明文算）、金额回读正确、无 tmp 残留；
  C. 另存为覆盖已存在文件 ⇒ **被覆盖的文件有备份**；
  D. 保存期间并发编辑 ⇒ `edited_during_save=True`、内存保留新值、磁盘仍是旧值、
     `modified()` 仍 True；再存一次新值落盘。
"""
import os
import sys

# ★2026-11-07：Windows 控制台默认 cp1252，一 print 中文就 UnicodeEncodeError。
#   CI 里用 PYTHONUTF8=1 兜着，这里再兜一层 —— 脚本在哪都能跑。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
import time
import shutil
import hashlib
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core.editor_core as ec
from core.editor_core import SaveDoc

OK = FAIL = 0


def chk(c, m):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + m)
    else:
        FAIL += 1
        print('  [FAIL] ' + m)


def md5(p):
    with open(p, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()


def baks(p):
    d = os.path.join(os.path.dirname(p), '_备份存档')
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def tmps(p):
    d = os.path.dirname(p)
    return [x for x in os.listdir(d) if x.endswith('.rsed-tmp')]


SAMPLE = os.path.join(HERE, '_exp', 'M0_基准_SAVE18BE.SAV')
if not os.path.exists(SAMPLE):
    print('样本不存在：%s（跳过）' % SAMPLE)
    print('结果：0 通过 / 0 失败（SKIP）')
    sys.exit(0)

TD = tempfile.mkdtemp(prefix='ra2sed_atomic_')


def fresh(name):
    p = os.path.join(TD, name)
    shutil.copy2(SAMPLE, p)
    return p


def player_of(doc):
    hs = [h for h in doc.houses() if h['verified']]
    return hs[0]['short'] if hs else None


try:
    # ---------- A: 两条写入路线都失败 ----------
    print('== A: 原地保存 + surgery/rebuild 都失败 ⇒ 原档必须完好 ==')
    pa = fresh('a.SAV')
    before, size0 = md5(pa), os.path.getsize(pa)
    da = SaveDoc(pa)
    ha = player_of(da)
    da.set_house_money(ha, 100001)
    _surg, _reb = ec.cfb_surgery.surgery, ec.cfb_write.rebuild
    ec.cfb_surgery.surgery = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('注入: surgery 失败'))
    ec.cfb_write.rebuild = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('注入: rebuild 失败'))
    try:
        da.save(pa)
        chk(False, '两个 writer 都失败时 save() 应当抛错，但它返回了成功')
    except Exception as e:
        chk('原存档未改动' in str(e) or '保存失败' in str(e), '抛出可读错误：%s' % str(e).split('\n')[0][:60])
    chk(os.path.exists(pa), '原档仍在（旧实现会删掉它）')
    chk(md5(pa) == before and os.path.getsize(pa) == size0, '原档 md5 与大小逐字节未变')
    chk(not tmps(pa), '没有 .rsed-tmp 残留')
    chk(any(x.startswith('a.SAV.bak') for x in baks(pa)), '失败前已生成备份')
    ec.cfb_surgery.surgery, ec.cfb_write.rebuild = _surg, _reb

    # ---------- B: 原地保存正常 ----------
    print('== B: 原地保存正常 ==')
    pb = fresh('b.SAV')
    before_b = md5(pb)
    db = SaveDoc(pb)
    hb = player_of(db)
    db.set_house_money(hb, 888888)
    rb = db.save(pb)
    chk(rb['blocks'] == rb['blocks_expect'], '块布局 %d/%d 通过' % (rb['blocks'], rb['blocks_expect']))
    chk(md5(pb) != before_b, '文件确实被改写')
    got = [h['money'] for h in SaveDoc(pb).houses() if h['short'] == hb]
    chk(got == [888888], '回读金额 = %r' % (got,))
    chk(not tmps(pb), '没有 .rsed-tmp 残留')
    chk(db.modified() is False, '保存后 modified() = False（基准已推进）')
    chk(any(x.startswith('b.SAV.bak') for x in baks(pb)), '原地保存有备份')

    # ---------- C: 另存为覆盖已存在文件 ----------
    print('== C: 另存为覆盖已存在文件 ==')
    tgt = fresh('target.SAV')
    t_before = md5(tgt)
    dc = SaveDoc(pb)
    dc.set_house_money(hb, 222222)
    dc.save(tgt)
    chk(md5(tgt) != t_before, '目标文件被覆盖')
    chk(any(x.startswith('target.SAV.bak') for x in baks(tgt)),
        '被覆盖的文件有备份（旧实现没有）')

    # ---------- D: 保存期间并发编辑 ----------
    print('== D: 保存期间并发编辑不被吞掉 ==')
    pd = fresh('d.SAV')
    dd = SaveDoc(pd)
    hd = player_of(dd)
    orig_rechunk = dd._rechunk

    def slow(np):
        time.sleep(1.2)                      # 人为拉长保存窗口
        return orig_rechunk(np)

    dd._rechunk = slow
    dd.set_house_money(hd, 100001)           # 保存前 A
    res = {}
    th = threading.Thread(target=lambda: res.update(dd.save(pd)))
    th.start()
    time.sleep(0.35)
    dd.set_house_money(hd, 200002)           # 保存期间 B
    th.join()
    dd._rechunk = orig_rechunk
    chk(res.get('edited_during_save') is True, 'save() 报出 edited_during_save=True')
    mem = [h['money'] for h in dd.houses() if h['short'] == hd]
    chk(mem == [200002], '内存里保留的是新值 %r（旧实现会被 A 覆盖）' % (mem,))
    disk = [h['money'] for h in SaveDoc(pd).houses() if h['short'] == hd]
    chk(disk == [100001], '磁盘上是保存时的值 %r（B 确实还没写进去）' % (disk,))
    chk(dd.modified() is True, 'modified() 仍为 True（标题栏继续挂 *）')
    r2 = dd.save(pd)
    disk2 = [h['money'] for h in SaveDoc(pd).houses() if h['short'] == hd]
    chk(r2.get('edited_during_save') is False, '第二次保存无并发编辑')
    chk(disk2 == [200002], '第二次保存把新值落盘 %r' % (disk2,))
    chk(dd.modified() is False, '第二次保存后 modified() = False')
finally:
    shutil.rmtree(TD, ignore_errors=True)

print('-' * 72)
print('自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
