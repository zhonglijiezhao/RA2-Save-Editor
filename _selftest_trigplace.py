# -*- coding: utf-8 -*-
"""自检：「触发法放建筑」（`place_building_by_trigger`）—— 2026-10-17 玩家定案的主用法。

判据：
  ① 触发法的**唯一产物是数据**：路径点 + 一条「事件8 任何事件 → 行为125」的触发，
     **不往建筑组里塞记录**（建筑数不变、明文只多 660 B = 标签184+实例200+事件108+行为168）；
  ② 目标格**没有路径点就新建**（号 = 最大号+1，≤701），**已有就复用**（不重复占号）；
  ③ 默认配方（事件 13 流逝 **1 秒** —— 玩家实测唯一有效的那条）在**新开局档**
     （原本没有 TagNode/CellTag/登记表）上必须把六件套补齐并建表登记，且不报"可能不生效"；
  ④ 存盘重开后字段全对：+88=路径点号、+104=类型名、触发记录 +128=所属方；
  ⑤ 有完整触发集群的档（五路径点档）上同样成立，且不动已有的其它触发。
"""
import os, sys, struct, shutil, tempfile, hashlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from core.editor_core import SaveDoc
except Exception:                      # 技能目录下是平铺的 scripts/
    from editor_core import SaveDoc

OK = FAIL = 0
def chk(c, m):
    global OK, FAIL
    if c:
        OK += 1; print('  [PASS] ' + m)
    else:
        FAIL += 1; print('  [FAIL] ' + m)

# ★样本已复制进 _exp/（E:/YURI 里的原档随时会被游戏挪走/改写）
S_FRESH = os.path.join(HERE, '_exp', 'sample_新开局_触发给韩国一个光棱塔.SAV')   # 无 node/cell/登记表
S_CLUSTER = os.path.join(HERE, '_exp', 'sample_完整集群_T1.SAV')                  # 完整集群 + 已有登记表

def check_one(src, tag):
    print('== %s ==' % tag)
    tmp = tempfile.mkdtemp(prefix='trigplace_')
    try:
        d = SaveDoc(src)
        n_bld0 = len(d.buildings())
        n_acts0 = len([a for a in d.actions() if a['type'] == 125])
        try:
            _g = d._newtrig_groups()
        except Exception:                    # 只有 1 个事件的档量不出步长
            _g = d._newtrig_groups(ev_size=d.auto_ev_size())
        g0 = {k: (len(v) if v else 0) for k, v in _g.items() if k != 'ev_size'}
        wp0 = d.waypoints_save()
        out = os.path.join(tmp, 'out.SAV')
        r = d.place_building_by_trigger('ATESLA', 50, 50, house='YuriCountry')
        chk(len(d.buildings()) == n_bld0, '没有往建筑组里塞记录（建筑数仍 %d）' % n_bld0)
        chk(len(d.plain) - len(d.plain_orig) == 832,
            '明文只多 832 B（184+200+108+168+node92+cell76+登记表4，实测 %+d）'
            % (len(d.plain) - len(d.plain_orig)))
        chk(r['waypoint'] == (max(wp0) + 1) and d.waypoint_cell(r['waypoint']) == (50, 50),
            '新路径点 %d = (50,50)（原最大号 %d）' % (r['waypoint'], max(wp0)))
        chk(r['waypoint'] <= 701, '路径点号在 0..701 内')
        chk(r['house'] == 'YuriCountry', '所属方 = YuriCountry')
        bad = [w for w in (r['warnings'] or []) if '可能不生效' in w or '未登记' in w]
        chk(not bad, '没有"可能不生效/未登记"警告（%s）' % (r['warnings'] or '无警告'))
        g = d._newtrig_groups()
        c1 = {k: (len(v) if v else 0) for k, v in g.items() if k != 'ev_size'}
        chk(all(c1[k] == g0[k] + 1 for k in ('tag', 'trig', 'node', 'cell')),
            '六件套齐：每组各 +1（%s → %s）' % (g0, c1))
        d.save(out, backup=False)
        c = SaveDoc(out)
        acts = [a for a in c.actions() if a['type'] == 125]
        chk(len(acts) == n_acts0 + 1, '行为 125 只多了一条（原 %d → %d）' % (n_acts0, len(acts)))
        o = acts[-1]['obj']
        chk(struct.unpack_from('<I', c.plain, o + 88)[0] == r['waypoint'], '行为 +88 = 路径点号')
        chk(c.plain[o + 104:o + 112].split(b'\x00')[0] == b'ATESLA', '行为 +104 = "ATESLA"')
        chk(any((e['type'], e['arg']) == (13, 1) for e in c.events()),
            '事件 = 13 流逝 1 秒（默认这条是已验证可用的；事件 8 实测不烧，见文档 §52）')
        chk(c.waypoint_cell(r['waypoint']) == (50, 50), '重开后路径点仍在 (50,50)')
        own = [c.trigger_owner_name(x) for x in c.records if c.trigger_owner_key(x)]
        chk('YuriCountry' in own, '触发所属方 = YuriCountry（清单 %s）' % (own,))
        # 同格再放一次：必须复用同一个路径点号，不占新号
        n_wp0 = len(wp0)
        d2 = SaveDoc(src)
        r2 = d2.place_building_by_trigger('GAPOWR', 50, 50, house='Americans')
        chk(r2['waypoint'] == r['waypoint'], '同一格第二次放置复用路径点 %d' % r2['waypoint'])
        chk(len(d2.waypoints_save()) == n_wp0 + 1, '路径点表只多了 1 条（%d → %d）'
            % (n_wp0, len(d2.waypoints_save())))
        # 已有路径点的格子：复用原号
        d3 = SaveDoc(src)
        r3 = d3.place_building_by_trigger('GAPOWR', 80, 80, house='Americans')
        chk(r3['waypoint'] == 98, '(80,80) 已有路径点 ⇒ 复用 98 号（实得 %r）' % (r3['waypoint'],))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if os.path.exists(S_FRESH):
    check_one(S_FRESH, '新开局档 SAVE0029（无 node/cell/登记表）')
else:
    print('  [SKIP] 缺 %s' % S_FRESH)

if os.path.exists(S_CLUSTER):
    check_one(S_CLUSTER, '完整集群档 五路径点档')
else:
    print('  [SKIP] 缺 %s' % S_CLUSTER)


print('== 事件 8「任何事件」：也必须自动登记进激活表 ==')
if os.path.exists(S_FRESH):
    tmp = tempfile.mkdtemp(prefix='trigplace8_')
    try:
        d = SaveDoc(S_FRESH)
        n0 = (d.celltag_timer_list() or {}).get('n', 0)
        r = d.place_building_by_trigger('ATESLA', 60, 60, house='YuriCountry',
                                        ev_type=8, ev_arg=0)          # register 未传 ⇒ 应自动 True
        out = os.path.join(tmp, 'e8.SAV')
        d.save(out, backup=False)
        c = SaveDoc(out)
        tl = c.celltag_timer_list()
        chk(tl is not None and tl.get('n') == n0 + 1,
            '无计时事件也登记了（表条数 %d → %s）' % (n0, tl and tl.get('n')))
        chk(any(e['type'] == 8 for e in c.events()), '事件 = 8「任何事件」已写入')
        chk(not [w for w in (r['warnings'] or []) if '未登记' in w or '可能不生效' in w],
            '没有"未登记/可能不生效"警告')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
else:
    print('  [SKIP] 缺 %s' % S_FRESH)

print('\n自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
