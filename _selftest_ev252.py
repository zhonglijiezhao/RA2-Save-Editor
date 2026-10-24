"""§252 自检：克隆单位的「任务/目的地/指令」整组必须写成**同档原生空闲单位的样子**。

背景（玩家第 7 轮原话）：「飞碟和光棱都有目标 不知要去那里 打掉飞碟弹框」
数据（T106 同档 98 台原生 DISK 做基准，逐字段 0/98 扫描）：
  · `+0x960/+0x964` = 伴随段目的地格，98/98 原生都是**合法格**；我们清 0 = 格 (0,0) = 地图角
  · `+0x658/+0x680` = 目的地 Y（实测恒等于 +0x964）
  · `+0x3A4/+0x3BC` = 全档统一的共享指令对象 `0x17B2DC`；清 0 = 空指令对象 ⇒ 打掉弹框
  · 而地面载具（Drive）157/157 原生这几项**本来就是 0** ⇒ 不能无脑写坐标

★注意：**每次插入后记录偏移会整体移位**，所以一律用"当前文档"重新取记录，
  不能拿插入前的快照按旧偏移读（本项目已踩过 4 次这个坑）。

用法：D:/py/python.exe _selftest_ev252.py
"""
import sys, os, struct

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'core'))
import editor_core as ec

BASE = 'E:/YURI/新建文件夹 (30)/T85_老工程师_修位置副本槽.SAV'
OUT = os.path.join(HERE, '_tmp_ev252.SAV')

P = F = 0


def ck(name, cond, info=''):
    global P, F
    if cond:
        P += 1
        print('  [PASS] %s  %s' % (name, info))
    else:
        F += 1
        print('  [FAIL] %s  %s' % (name, info))


def main():
    d = ec.SaveDoc(BASE)

    def recs(ty):
        return [r for r in d.unit_records() if r['type'] == ty]

    def u(off):
        return struct.unpack_from('<I', d.plain, off)[0]

    def mode(ty, f):
        vals = [u(r['off'] + f) for r in recs(ty)]
        return max(set(vals), key=vals.count) if vals else 0

    n_disk_dest0 = sum(1 for r in recs('DISK') if u(r['off'] + 0x960) == 0)
    m_3a4 = mode('DISK', 0x3A4)
    m_3bc = mode('DISK', 0x3BC)
    m_s3a4 = mode('SREF', 0x3A4)

    print('\n=== [A] 空中单位：目的地必须是自己脚下那格（不是 0）===')
    info = d.add_unit_by_clone('DISK', 110, 206, veterancy=2)
    rec = [r for r in recs('DISK') if r['key'] == info['key']][0]
    o = rec['off']
    ck('A1. 记录落在 (110,206)', (rec['x'], rec['y']) == (110, 206), str((rec['x'], rec['y'])))
    ck('A2. `+0x960` = 自己那格的 x 中心', u(o + 0x960) == (110 << 8) | 0x80, hex(u(o + 0x960)))
    ck('A3. `+0x964` = 自己那格的 y 中心', u(o + 0x964) == (206 << 8) | 0x80, hex(u(o + 0x964)))
    ck('A4. `+0x658`/`+0x680` = 目的地 y（与 0x964 同值）',
       u(o + 0x658) == u(o + 0x964) == u(o + 0x680) != 0,
       '%s/%s/%s' % (hex(u(o + 0x658)), hex(u(o + 0x964)), hex(u(o + 0x680))))
    ck('A5. 同档原生飞碟里目的地为 0 的有几台（应为 0 = 证明 0 才是异常）',
       n_disk_dest0 == 0, '%d 台' % n_disk_dest0)

    print('\n=== [B] 指令组：抄骨架，不再清 0 ===')
    ck('B1. `+0x3A4` 当前指令 = 同档原生众数',
       u(o + 0x3A4) == m_3a4 and m_3a4 != 0,
       '%s vs 众数 %s' % (hex(u(o + 0x3A4)), hex(m_3a4)))
    ck('B2. `+0x3BC` 指令副本 = 同档原生众数',
       u(o + 0x3BC) == m_3bc and m_3bc != 0,
       '%s vs 众数 %s' % (hex(u(o + 0x3BC)), hex(m_3bc)))
    ck('B3. 任务组 `+0x298/+0x29C/+0x2A0/+0x2A4` 仍为 0（空闲态）',
       all(u(o + f) == 0 for f in (0x298, 0x29C, 0x2A0, 0x2A4)), '')

    print('\n=== [C] 地面单位：骨架是 0 就保持 0（不能写成坐标）===')
    i2 = d.add_unit_by_clone('SREF', 109, 204, veterancy=2)
    r2 = [r for r in recs('SREF') if r['key'] == i2['key']][0]
    o2 = r2['off']
    ck('C1. 光棱的目的地仍是 0（与同档原生一致）',
       all(u(o2 + f) == 0 for f in (0x960, 0x964, 0x658, 0x680)),
       str([hex(u(o2 + f)) for f in (0x960, 0x964)]))
    ck('C2. 光棱指令组也照骨架（本档 Drive 众数 = %s）' % hex(m_s3a4),
       u(o2 + 0x3A4) == m_s3a4, '%s vs %s' % (hex(u(o2 + 0x3A4)), hex(m_s3a4)))
    ck('C3. `order_state.dest` 对地面单位是 None（没写目的地）',
       (i2.get('order_state') or {}).get('dest') is None, str(i2.get('order_state')))

    print('\n=== [D] 落点：不再自动挪位（玩家明确要求）===')
    ck('D1. 返回里 cell_nudge 为空', info.get('cell_nudge') is None, str(info.get('cell_nudge')))
    ci = d.cell_flat_info(104, 212)
    ck('D2. (104,212) 确实不是平地（悬崖，对照组有效）', not ci['is_flat'], ci.get('why', ''))
    i3 = d.add_unit_by_clone('DISK', 104, 212)
    r3 = [r for r in recs('DISK') if r['key'] == i3['key']][0]
    ck('D3. 默认**原样落在** (104,212)（不再挪走）', (r3['x'], r3['y']) == (104, 212),
       str((r3['x'], r3['y'])))
    ck('D4. 但不是平地时**必须告警**',
       any('不是平地' in w for w in (i3.get('warnings') or [])),
       ' / '.join(i3.get('warnings') or [])[:80])
    i4 = d.add_unit_by_clone('DISK', 104, 212, auto_nudge=True)
    ck('D5. 显式 auto_nudge=True 时才挪，且挪到的是平地',
       (i4.get('cell_nudge') or {}).get('to') is not None
       and d.cell_flat_info(*i4['cell_nudge']['to'])['is_flat'],
       str(i4.get('cell_nudge')))

    print('\n=== [E] 落盘重读后不变 ===')
    d.save(OUT)
    e = ec.SaveDoc(OUT)
    for k, f, want in ((info['key'], 0x960, (110 << 8) | 0x80),
                       (info['key'], 0x3A4, m_3a4),
                       (i2['key'], 0x960, 0)):
        rs = [r for r in e.unit_records() if r['key'] == k]
        got = struct.unpack_from('<I', e.plain, rs[0]['off'] + f)[0] if rs else None
        ck('E. %08X 重读后 +0x%03X = %s' % (k, f, hex(want)), got == want,
           hex(got) if got is not None else '记录不见了')

    try:
        os.remove(OUT)
    except OSError:
        pass
    print('-' * 72)
    print('§252 自检：PASS %d / FAIL %d' % (P, F))
    return 0 if F == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
