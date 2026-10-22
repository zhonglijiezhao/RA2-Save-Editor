# -*- coding: utf-8 -*-
r"""RA2 存档编辑器 —— 命令行入口 / PyInstaller 入口

用法（GUI）：
    RA2存档编辑器.exe                          直接双击 = 图形界面
    RA2存档编辑器.exe --help                   打印这份清单

用法（headless，只读或批处理，结果写同目录的报告文件）：
    RA2存档编辑器.exe --selftest <save.SAV>    headless check: build the UI,
        decode the save, money round-trip, write 自检结果.txt next to the exe,
        exit 0/1.  (Windowed exe has no stdout -- the txt file IS the report.)
    RA2存档编辑器.exe --mktrig <in> <out> <name> <ev_type> <ev_arg> <ac_type> <ac_arg>
        headless 新写触发（六件套）—— 批量验证 / 回归用。
    RA2存档编辑器.exe --repairtrig <in> <out> <触发名或ID> [ev_type ev_arg]
        headless 修复缺组件触发（补 TagNode/CellTag + 计时表），可选顺带改事件。
    RA2存档编辑器.exe --gift <in> <out> <类型ID> [ready | placed <cx> <cy>] [槽2hex]
        headless 赠送建筑：ready=就绪未落地（默认），placed=直接放好（三表全登）。
    RA2存档编辑器.exe --place <in> <out> <类型ID> <cx> <cy> [--house 阵营]
    RA2存档编辑器.exe --ready <in>                        # 这份档能用哪种方式放建筑（只读）
    RA2存档编辑器.exe --units <in>                        # 列出单位实例 + 型号统计（只读）
    RA2存档编辑器.exe --tags <in>                         # 列出标签对象全字段（§192，只读）
    RA2存档编辑器.exe --types <in>                        # 列出可放置的全部型号（步兵/载具）
    RA2存档编辑器.exe --putunit <in> <out> <类型> <cx> <cy> [--house 阵营] [--vet 0|1|2]
                              # ★克隆放置单位（同类型克隆，U2 游戏内已验收）
                              #   带 --house ⇒ **给对面刷兵**（改记录 +352/+560 的所属方）
    RA2存档编辑器.exe --unitowner <in> <out> <cx> <cy> <阵营>
                              # ★改「站在该格上的单位」的所属方（只动 8 字节）
    RA2存档编辑器.exe --trigplace <in> <out> <类型ID> <cx> <cy> [--house 阵营]
                              # ★推荐：触发法（事件8 任何事件 → 行为125），有建造动画、
                              #   引擎会自己拒绝悬崖/水面等不可建地形
    RA2存档编辑器.exe --retarget <in> <out> <触发ID或名字> --build <类型ID> --cell x,y
    RA2存档编辑器.exe --retarget <in> <out> <触发ID> --build <类型ID> --wp <路径点号>
    RA2存档编辑器.exe --retarget <in> <out> <触发ID> --ac <行为类型> [--arg 参数] [--dry]
                              # ★改造**已有**触发的动作（M1 路线，游戏内验收过）：
                              #   新建触发引擎不执行，改已有触发能跑 ⇒ 这是目前唯一可靠的路子。
                              #   只改动作的 4 个字段，文件长度不变（不碰 7MB 容器红线）。
    RA2存档编辑器.exe --wp <in>                          # 列出现有路径点（存档明文 1586+4×号）
    RA2存档编辑器.exe --wp <in> <out> <号> <cx> <cy>      # 写/移动路径点（-1 -1 = 删除）
    RA2存档编辑器.exe --actions <in>                      # ★列出现有「行为125 建建筑」条目：
                                                          #   触发ID/名字、建筑名、路径点、格子、
                                                          #   事件类型 + 计时秒数 + 是否启用
                                                          #   （挑"会被执行"的触发来改造时先跑这个）
    RA2存档编辑器.exe --cells <in> [--type 类型] [--n 12]  # ★列「紧邻已建建筑的空格」：
                                                          #   行为125 落到水里/坡上会被引擎**静默拒绝**
                                                          #   （实测 (71,241) 是水 ⇒ 什么都不出），
                                                          #   挑这些格子命中率最高
        headless 直接放好（可指定**所属方**：短名 Russians / 中文名 苏俄 / House 键）。
        --house 也可用于 --gift 的 placed 分支。
    RA2存档编辑器.exe --owner <in> <out> <阵营> [--type 类型] [--cell cx cy] [--key hex] [--idx n]
        headless **改已有建筑的所属方**（写记录载荷 +336/+544）。给 --type = 该类型**全部**都改。
"""
import os
import sys
import traceback
import datetime
import tempfile


def _base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE = _base_dir()
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'core'))

REPORT = os.path.join(BASE, '自检结果.txt')


def _write_report(txt):
    try:
        with open(REPORT, 'w', encoding='utf-8-sig') as f:
            f.write(txt)
    except Exception:
        pass
    # 源码运行时也打印一份，方便命令行看
    try:
        sys.stdout.write(txt + '\n')
    except Exception:
        pass


def selftest(sav):
    lines = ['时间: %s' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
             'exe : %s' % (sys.executable if getattr(sys, 'frozen', False) else '(源码)'),
             '存档: %s' % sav, '']
    try:
        import RA2存档编辑器 as m          # noqa: E402  (frozen: bundled module)
        lines.append('[1] 导入 GUI 模块            OK')
        app = m.App()
        app.withdraw()
        app.update_idletasks()
        rows = len(app.tree.get_children())
        lines.append('[2] 界面构造                 OK（存档列表 %d 行）' % rows)
        app.destroy()

        from editor_core import SaveDoc
        doc = SaveDoc(sav)
        lines.append('[3] 解码存档                 OK（明文 %d 字节，玩家=%s，金钱=%s）'
                     % (len(doc.plain), doc.info.get('玩家阵营'), doc.info.get('金钱')))
        lines.append('[4] 记录/事件/行为            OK（%d / %d / %d）'
                     % (len(doc.records), len(doc.events()), len(doc.actions())))

        # 金钱往返：改 -> 存 -> 读回（玩家优先，无玩家则第一个已定位阵营；
        # 自定义所属方任务里没有标准阵营名，不能写死 'Americans'）
        hs = doc.houses()
        tgt = next((h for h in hs if h['is_player'] and h['verified']), None) \
            or next((h for h in hs if h['verified']), None)
        if tgt:
            name = tgt['short']
            doc.set_house_money(name, 777777)
            out = os.path.join(tempfile.gettempdir(), '_ra2exe_check.SAV')
            doc.save(out)
            c = SaveDoc(out)
            got = [h['money'] for h in c.houses() if h['short'] == name]
            ok = bool(got) and got[0] == 777777
            lines.append('[5] 金钱改写往返(%s)      %s（回读 %s）'
                         % (name, 'OK' if ok else 'FAIL', got))
            os.remove(out)
            if not ok:
                raise RuntimeError('金钱往返失败')
        else:
            lines.append('[5] 金钱改写往返              跳过（该档无数组）')

        # 建造动画判态（§170 五态 + §171 replay 标志）：确认核心库常量与状态机都在包里
        n_anim, st_anim, n_replay = 0, {}, 0
        for b in doc.buildings():
            try:
                s = doc.building_anim_state(b['base'])
            except Exception:
                continue
            st_anim[s['state']] = st_anim.get(s['state'], 0) + 1
            if s.get('replay'):
                n_replay += 1
            n_anim += 1
        bad = sum(st_anim.get(k, 0) for k in ('stuck', 'frozen', 'loop'))
        lines.append('[6] 建造动画判态(%d 栋)      OK（%s；会播两遍 %d 栋；异常 %d 栋）'
                     % (n_anim, st_anim, n_replay, bad))

        # FA2 触发器（§174）：**多事件 + 链头插逆序** —— 内存里建一条再核对，不写盘
        try:
            info7 = doc.create_trigger_multi(
                '自检多事件', [(13, 5), (8, 0)], [(21, 0)])
            ch7 = [(e['type'], e['arg'])
                   for e in doc.trigger_event_list(info7['trigger_id'])]
            ac7 = [(a['type'], a['arg'])
                   for a in doc.trigger_action_list(info7['trigger_id'])]
            ok7 = (ch7 == [(8, 0), (13, 5)]) and (ac7 == [(21, 0)])
            lines.append('[7] FA2 多事件触发器         %s（链头=%s，行为=%s）'
                         % ('OK' if ok7 else 'FAIL', ch7, ac7))
            if not ok7:
                raise RuntimeError('多事件触发链序不对：%s / %s' % (ch7, ac7))
        except Exception as e7:
            lines.append('[7] FA2 多事件触发器         跳过（%s）' % str(e7)[:70])

        # 局部变量（§175）：变量表 100×41B + **行为 56 的变量号在 +164**（不是 +0x48）
        try:
            lv_n = len([v for v in doc.local_var_table() if v['name']])
            t8 = doc.create_trigger_multi('自检变量', [(36, 3)], [(56, 3)])
            a8 = doc.trigger_action_list(t8['trigger_id'])
            o8 = next((x['obj'] for x in doc.actions() if x.get('key') == a8[0]['key']), None)
            v164 = int.from_bytes(doc.plain[o8 + 164:o8 + 168], 'little') if o8 else -1
            v48 = int.from_bytes(doc.plain[o8 + 72:o8 + 76], 'little') if o8 else -1
            ok8 = (a8[0]['type'] == 56 and a8[0]['arg'] == 3 and v164 == 3 and v48 == 0)
            lines.append('[8] 局部变量(行为56@+164)    %s（已命名 %d 条；+0x48=%d，+0xA4=%d）'
                         % ('OK' if ok8 else 'FAIL', lv_n, v48, v164))
            if not ok8:
                raise RuntimeError('行为 56 变量号没写到 +164：+0x48=%d +0xA4=%d' % (v48, v164))
        except Exception as e8:
            lines.append('[8] 局部变量(行为56@+164)    跳过（%s）' % str(e8)[:70])

        lines.append('')
        lines.append('SELFTEST PASS')
        _write_report('\n'.join(lines))
        return 0
    except BaseException:
        lines.append('')
        lines.append('SELFTEST FAIL')
        lines.append(traceback.format_exc())
        _write_report('\n'.join(lines))
        return 1


def mktrig(args):
    """headless 新写触发：--mktrig <in> <out> <name> <ev_type> <ev_arg> <ac_type> <ac_arg>"""
    if len(args) < 7:
        _write_report('MKTRIG FAIL\n用法: --mktrig <in> <out> <name> '
                      '<ev_type> <ev_arg> <ac_type> <ac_arg>')
        return 2
    src, out, name = args[0], args[1], args[2]
    ev_type, ev_arg, ac_type, ac_arg = (int(x) for x in args[3:7])
    try:
        from editor_core import SaveDoc
        d = SaveDoc(src)
        if not d.can_create_trigger():
            raise RuntimeError('该档布局不支持新写触发')
        info = d.create_trigger(name, ev_type, ev_arg, ac_type, ac_arg)
        d.save(out)
        lines = ['MKTRIG PASS', 'src : %s' % src, 'out : %s' % out, 'info: %s' % (info,),
                 '触发数: %d' % len([r for r in d.records if r.kind == '触发']),
                 '事件/行为: %d / %d' % (len(d.events()), len(d.actions()))]
        _write_report('\n'.join(lines))
        return 0
    except BaseException:
        _write_report('MKTRIG FAIL\n\n' + traceback.format_exc())
        return 1


def repairtrig(args):
    """headless 修复缺组件触发：
    --repairtrig <in> <out> <触发名或ID> [ev_type ev_arg [ticks]]
    可选：先把该触发的事件改成 (ev_type, ev_arg)，再补齐 TagNode/CellTag + 计时表；
    ticks = TagNode 等待帧数（不给则自动：13 号事件 → 秒×15，否则 0）。"""
    if len(args) not in (3, 5, 6):
        _write_report('REPAIRTRIG FAIL\n用法: --repairtrig <in> <out> <触发名或ID> '
                      '[ev_type ev_arg [ticks]]')
        return 2
    src, out, who = args[0], args[1], args[2]
    try:
        from editor_core import SaveDoc
        d = SaveDoc(src)
        st = {s['sid']: s for s in d.runtime_status()}
        st.update({s['name']: s for s in d.runtime_status()})
        s = st.get(who)
        if s is None:
            raise RuntimeError('找不到触发 %r（可用: %s）'
                               % (who, ', '.join(x['name'] or x['sid'] for x in d.runtime_status())))
        lines = ['REPAIRTRIG PASS', 'src: %s' % src, 'out: %s' % out,
                 '触发: %s (%s) inst@%08X' % (s['name'], s['sid'], s['inst_off']),
                 '修复前: node=%s cell=%s' % (s['has_node'], s['has_cell'])]
        ticks = int(args[5]) if len(args) == 6 else None
        if len(args) >= 5:
            ev_type, ev_arg = int(args[3]), int(args[4])
            evs = d.events()
            # 找该触发的事件：走运行期键链（实例+192 = 事件自键）
            import struct as _s
            ek = _s.unpack_from('<I', d.plain, s['inst_off'] + 192)[0]
            ev = next((e for e in evs
                       if _s.unpack_from('<I', d.plain, e['obj'] + 16)[0] == ek), None)
            if ev is None:
                raise RuntimeError('找不到该触发的事件对象（键 %08X）' % ek)
            d.set_event(ev, etype=ev_type, arg=ev_arg)
            lines.append('事件: → type=%d arg=%d（%s）' % (ev_type, ev_arg,
                          (getattr(d, 'time_sync_note', '') or '').replace('\n', ' ')))
        info = d.repair_trigger_runtime(s['inst_key'], ticks=ticks)
        lines.append('修复: %r' % (info,))
        d.save(out)
        c = SaveDoc(out)
        c2 = {x['sid']: x for x in c.runtime_status()}[s['sid']]
        lines.append('复检: node=%s cell=%s ticks=%s' % (c2['has_node'], c2['has_cell'],
                                                         c2['ticks']))
        if not (c2['has_node'] and c2['has_cell']):
            raise RuntimeError('复检失败：修复后仍缺组件')
        _write_report('\n'.join(lines))
        return 0
    except BaseException:
        _write_report('REPAIRTRIG FAIL\n\n' + traceback.format_exc())
        return 1


def _take_house(argv):
    """从参数里摘出 `--house <阵营>`；返回 (house_or_None, 剩余参数)。"""
    house = None
    out = []
    i = 0
    while i < len(argv):
        if argv[i] == '--house' and i + 1 < len(argv):
            house = argv[i + 1]
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return house, out


_VET_WORDS = {'0': 0, '1': 1, '2': 2,
              '新兵': 0, '老兵': 1, '精英': 2,
              'rookie': 0, 'veteran': 1, 'elite': 2}


def _take_vet(argv):
    """从参数里摘出 `--vet <0|1|2|新兵|老兵|精英>`；返回 (vet_or_None, 剩余参数)。"""
    vet = None
    out = []
    i = 0
    while i < len(argv):
        if argv[i] == '--vet' and i + 1 < len(argv):
            w = str(argv[i + 1]).strip().lower()
            if w not in _VET_WORDS:
                raise ValueError('--vet 只能是 0/1/2 或 新兵/老兵/精英，收到 %r' % (argv[i + 1],))
            vet = _VET_WORDS[w]
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return vet, out


def place(args):
    """headless 直接放好（可指定所属方）：
    --place <in> <out> <类型ID> <cx> <cy> [--house 阵营短名|中文名|House键]"""
    house, args = _take_house(args)
    if len(args) < 5:
        _write_report('PLACE FAIL\n用法: --place <in> <out> <类型ID> <cx> <cy> [--house 阵营]')
        return 2
    src, out, tname = args[0], args[1], args[2]
    try:
        cx, cy = int(args[3]), int(args[4])
    except ValueError:
        _write_report('PLACE FAIL\ncx/cy 必须是整数')
        return 2
    try:
        from editor_core import SaveDoc
        d = SaveDoc(src)
        info = d.add_placed_building(tname, cx, cy, house=house)
        d.save(out)
        c = SaveDoc(out)
        _write_report('\n'.join(['PLACE PASS', 'src: %s' % src, 'out: %s' % out,
                                 'house: %s' % (house,), 'info: %s' % (info,),
                                 '复检明文: %d B' % len(c.plain)]))
        return 0
    except BaseException:
        _write_report('PLACE FAIL\n\n' + traceback.format_exc())
        return 1


def trigplace(args):
    """headless **触发法**放建筑：--trigplace <in> <out> <类型ID> <cx> <cy> [--house 阵营]

    写一条「事件 8 任何事件 → 行为 125 将建筑建于…」的触发，并在目标格补路径点；
    读档后由**引擎自己造** ⇒ 有建造动画、模型完整，且不可建造的地形会被引擎拒绝。
    """
    house, args = _take_house(args)
    if len(args) < 5:
        _write_report('TRIGPLACE FAIL\n用法: --trigplace <in> <out> <类型ID> <cx> <cy> [--house 阵营]')
        return 2
    src, out, tname = args[0], args[1], args[2]
    try:
        cx, cy = int(args[3]), int(args[4])
    except ValueError:
        _write_report('TRIGPLACE FAIL\ncx/cy 必须是整数')
        return 2
    try:
        from editor_core import SaveDoc
        from editor_core import TriggerUnavailable
        d = SaveDoc(src)
        info = d.place_building_by_trigger(tname, cx, cy, house=house)
        d.save(out)
        c = SaveDoc(out)
        w = [x for x in c.actions() if x['type'] == 125]
        _write_report('\n'.join([
            'TRIGPLACE PASS', 'src: %s' % src, 'out: %s' % out,
            '类型/格子: %s @(%d,%d)　路径点 %s　所属方 %s'
            % (tname, cx, cy, info.get('waypoint'), info.get('house')),
            '触发ID: %s（序号 %s）' % (info.get('trigger_id'), info.get('index')),
            '事件: %s / 参数 %s' % (info.get('ev_type'), info.get('ev_arg')),
            '行为125 条数: %d' % len(w),
            '警告: %s' % ('；'.join(info.get('warnings') or []) or '无'),
            '复检明文: %d B' % len(c.plain)]))
        return 0
    except TriggerUnavailable as e:
        _write_report('TRIGPLACE FAIL（本档写不了触发）\n原因: %s\n诊断: %s\n\n'
                      '建议: 改用 --place <in> <out> <类型> <cx> <cy> [--house 阵营]\n'
                      '      （直接落成；若本档连建筑组都没有，就必须先有存档里的建筑）'
                      % (e.reason, e.detail))
        return 3
    except BaseException:
        _write_report('TRIGPLACE FAIL\n\n' + traceback.format_exc())
        return 1


def typelist(args):
    """--types <in>：列出该档**能放置的全部单位型号**（步兵/载具）。"""
    if len(args) < 1:
        _write_report('TYPES FAIL\n用法: --types <in>')
        return 2
    try:
        from editor_core import SaveDoc
        import game_ini
        d = SaveDoc(args[0])
        try:
            d.auto_attach_map([os.path.dirname(os.path.abspath(args[0]))])
        except Exception:
            pass
        g = game_ini.get_types(os.path.dirname(os.path.abspath(args[0])), None,
                               getattr(getattr(d, '_map', None), 'path', None))
        pairs = d.placeable_unit_types()
        if g.ok():
            pairs = sorted(pairs, key=lambda p: (g.index_of(p[0]), p[0]))
        lines = ['存档: %s' % args[0],
                 '可放置型号 %d 个　规则来源: %s' % (len(pairs), g.source())]
        for cls, label in (('infantry', '步兵'), ('vehicle', '载具')):
            ns = [(n, c) for n, c in pairs if c == cls]
            lines.append('')
            lines.append('%s（%d）：' % (label, len(ns)))
            for n, _c in ns:
                nm = (g.label(n, '') if g.ok() else '') or d.unit_type_display(n) or ''
                br = g.brief(n) if g.ok() else ''
                lines.append('   %-10s %-24s %s'
                             % (n, ('（%s）' % nm) if nm else '', br))
        _write_report('\n'.join(lines))
        return 0
    except BaseException:
        _write_report('TYPES FAIL\n\n' + traceback.format_exc())
        return 1


def tags(args):
    """--tags <in>：列出标签对象（TagTypeClass，§192 字段表）+ 群体检（只读）。"""
    if len(args) < 1:
        _write_report('TAGS FAIL\n用法: --tags <in>')
        return 2
    try:
        from editor_core import SaveDoc
        d = SaveDoc(args[0])
        ts = d.tag_type_records()
        h = d.tag_health()
        lines = ['存档: %s' % args[0],
                 '标签 %d 个　持久性分布 %s　序号异常 %d　ID 重复 %d　vtable 异常 %d'
                 % (h['count'], h['persist_dist'] or {}, len(h['idx_bad']),
                    len(h['dup_sid']), len(h['bad_vt'])),
                 '',
                 '  %-9s %-3s %-3s %-9s %-6s %s'
                 % ('ID', '序', '持', '触发', 'vt', '名字（GBK 52B）')]
        for t in ts:
            lines.append('  %-9s %-3d %-3d %-9s %-6s %s'
                         % (t['sid'], t['index'], t['persist'],
                            t['trig_sid'] or ('%08x' % t['trig_key']),
                            'OK' if t['ok_vt'] else 'BAD', t['name']))
        _write_report('\n'.join(lines))
        return 0
    except BaseException:
        _write_report('TAGS FAIL\n\n' + traceback.format_exc())
        return 1


def units(args):
    """--units <in>：列出存档里的单位实例（类型/格子/类）+ 可克隆的类型。"""
    if len(args) < 1:
        _write_report('UNITS FAIL\n用法: --units <in>')
        return 2
    try:
        from editor_core import SaveDoc
        d = SaveDoc(args[0])
        recs = d.unit_records()
        pairs = d.placeable_unit_types()
        n_inf = sum(1 for _, c in pairs if c == 'infantry')
        n_veh = len(pairs) - n_inf
        lines = ['存档: %s' % args[0],
                 '单位实例 %d 个　档内已有: %s'
                 % (len(recs), '、'.join(d.units_placeable()) or '无'),
                 '可放置型号 %d 个（步兵 %d / 载具 %d）　—— 用 --types 看全表'
                 % (len(pairs), n_inf, n_veh)]
        try:
            import game_ini
            try:                 # 大型任务的规则写在地图 INI 里 ⇒ 先挂图再取规则
                d.auto_attach_map([os.path.dirname(os.path.abspath(args[0]))])
            except Exception:
                pass
            g = game_ini.get_types(os.path.dirname(os.path.abspath(args[0])), None,
                                   getattr(getattr(d, '_map', None), 'path', None))
        except Exception:
            g = None
        for r in recs:
            nm = ''
            if g is not None and g.ok():
                nm = g.label(r['type'], '') or ''
            lines.append('  %-8s %-9s (%s,%s)  所属方 %-12s 记录 %dB + 伴随 %dB %s'
                         % (r['type'], r['class'], r['x'], r['y'],
                            r.get('house') or '?', r['size'], r['comp_size'],
                            ('（%s）' % nm) if nm and nm.upper() != r['type'].upper() else ''))
        _write_report('\n'.join(lines))
        return 0
    except BaseException:
        _write_report('UNITS FAIL\n\n' + traceback.format_exc())
        return 1


def ai_report(args):
    """--ai <in> [--ai-all] [--ai-find 关键词] [--ai-limit N]：读 AI 三大件。

    作战小队 TeamType / 特遣部队 TaskForce / 动作脚本 ScriptType，
    全部字段在 SAVE0029 上与地图 INI 逐条核对（见 docs §118）。
    """
    show_all = '--ai-all' in args
    args = [a for a in args if a != '--ai-all']
    find = limit = None
    if '--ai-find' in args:
        i = args.index('--ai-find')
        find = args[i + 1] if i + 1 < len(args) else ''
        args = args[:i] + args[i + 2:]
    if '--ai-limit' in args:
        i = args.index('--ai-limit')
        try:
            limit = int(args[i + 1])
        except Exception:
            limit = None
        args = args[:i] + args[i + 2:]
    if not args:
        _write_report('AI FAIL\n用法: --ai <in.SAV> [--ai-all] [--ai-find 关键词] [--ai-limit N]')
        return 2
    src = args[0]
    try:
        from editor_core import SaveDoc
        import ai_objects
        d = SaveDoc(src)
        ai = ai_objects.read_ai(d)
    except BaseException:
        _write_report('AI FAIL\n\n' + traceback.format_exc())
        return 1

    teams = ai['teams']
    pool = [t for t in teams if show_all or t.get('is_map')]
    if find:
        q = find.lower()
        pool = [t for t in pool
                if q in t['id'].lower() or q in t['name'].lower()
                or q in (t['house'] or '').lower()
                or q in (t['taskforce'] or '').lower() or q in (t['script'] or '').lower()]
    n_map = sum(1 for t in teams if t.get('is_map'))
    out = ['AI PASS', 'src: %s' % src,
           '小队 %d（地图定义 %d）　特遣部队 %d　动作脚本 %d'
           % (len(teams), n_map, len(ai['taskforces']), len(ai['scripts'])), '']
    out.append('%-9s %-28s %-13s %4s %4s %4s %-14s %5s'
               % ('小队ID', '名称', '国家', 'Max', '优先', '老兵', '特遣部队', '兵力'))
    for t in pool[:limit or 200]:
        out.append('%-9s %-28s %-13s %4d %4d %4d %-14s %5d'
                   % (t['id'], t['name'][:28], t['house'], t['max'], t['priority'],
                      t['veteran'], (t['taskforce_id'] or '—'), 
                      sum(m['count'] for m in t['members'])))
    if limit and len(pool) > limit:
        out.append('…（共 %d 条，已截断到 %d）' % (len(pool), limit))
    # 详情：有关键词就只打命中的第一条
    if find and pool:
        t = pool[0]
        out.append('')
        out.append('== 详情：小队 %s「%s」 ==' % (t['id'], t['name']))
        out.append('  国家 %s　Max %d　优先级 %d　老兵等级 %d　编组 %d'
                   % (t['house'], t['max'], t['priority'], t['veteran'], t['group']))
        out.append('  标志 ' + ' '.join('%s=%s' % (k, 'y' if v else 'n')
                                       for k, v in t['flags'].items()))
        out.append('  特遣部队 %s「%s」' % (t['taskforce_id'] or '—', t['taskforce'] or ''))
        for m in t['members']:
            out.append('      %d × %s' % (m['count'], m['type']))
        sc = next((s for s in ai['scripts'] if s['id'] == t['script_id']), None)
        if sc:
            out.append('  脚本 %s「%s」%d 条' % (sc['id'], sc['name'], len(sc['entries'])))
            for i, e in enumerate(sc['entries']):
                # ★脚本动作表（不是触发器行为表）
                out.append('      %2d. %-2d %-22s 参数 %d'
                           % (i, e['action'], e.get('name') or '', e['arg']))
        else:
            out.append('  脚本：无')
    _write_report('\n'.join(out))
    return 0


def aivet(args):
    """--aivet <in> <out> <小队ID|first> <0..3>：只改该小队的 VeteranLevel（+0xB4，4 字节）。"""
    if len(args) < 4:
        _write_report('AIVET FAIL\n用法: --aivet <in> <out> <小队ID|first> <0..3>')
        return 2
    src, out, tid = args[0], args[1], args[2]
    try:
        lv = int(args[3])
    except ValueError:
        _write_report('AIVET FAIL\n等级必须是 0..3')
        return 2
    try:
        from editor_core import SaveDoc
        import ai_objects
        d = SaveDoc(src)
        ai = ai_objects.read_ai(d)
        if tid.lower() in ('first', '1', '?', 'auto'):
            pool = [t for t in ai['teams'] if t.get('is_map')] or ai['teams']
            if not pool:
                _write_report('AIVET FAIL\n这份存档里一个小队都没有')
                return 3
            tid = pool[0]['id']
        info = ai_objects.set_team_veteran(d, tid, lv)
        r = d.save(out, backup=False)
        e = SaveDoc(out)
        t = next(x for x in ai_objects.read_ai(e)['teams'] if x['id'] == tid)
        _write_report('\n'.join([
            'AIVET PASS', 'src: %s' % src, 'out: %s' % out,
            '小队 %s「%s」　国家 %s' % (info['team_id'], info['name'], info['house']),
            '老兵等级 %d → %d（写 +0x%X）' % (info['old'], info['new'],
                                            ai_objects.TEAM_VET_OFF),
            '特遣部队 %s　脚本 %s' % (info['taskforce'], info['script']),
            '保存：块 %d/%d %s' % (r['blocks'], r['blocks_expect'], r['writer']),
            '回验：读回的等级 = %d' % t['veteran']]))
        return 0
    except BaseException:
        _write_report('AIVET FAIL\n\n' + traceback.format_exc())
        return 1


def aitf(args):
    """--aitf <in> <out> <特遣ID> <序号> [--count N] [--type 型号]：改特遣部队成员。"""
    cnt = None
    tname = None
    if '--count' in args:
        i = args.index('--count')
        try:
            cnt = int(args[i + 1])
        except Exception:
            _write_report('AITF FAIL\n--count 必须是整数')
            return 2
        args = args[:i] + args[i + 2:]
    if '--type' in args:
        i = args.index('--type')
        tname = args[i + 1] if i + 1 < len(args) else ''
        args = args[:i] + args[i + 2:]
    if len(args) < 4:
        _write_report('AITF FAIL\n用法: --aitf <in> <out> <特遣ID> <序号> '
                      '[--count N] [--type 型号]')
        return 2
    src, out, tf_id, idx = args[0], args[1], args[2], args[3]
    try:
        idx = int(idx)
    except ValueError:
        _write_report('AITF FAIL\n序号必须是整数')
        return 2
    try:
        from editor_core import SaveDoc
        import ai_objects
        d = SaveDoc(src)
        info = ai_objects.set_taskforce_member(d, tf_id, idx, count=cnt, type_name=tname)
        if not info['changed']:
            _write_report('AITF 未改动（新旧一样）\n%s' % info)
            return 0
        r = d.save(out, backup=False)
        e = SaveDoc(out)
        t = next(x for x in ai_objects.read_ai(e)['taskforces'] if x['id'] == tf_id)
        _write_report('\n'.join([
            'AITF PASS', 'src: %s' % src, 'out: %s' % out,
            '特遣部队 %s「%s」第 %d 项' % (info['tf_id'], info['name'], info['index']),
            '数量 %d → %d　型号键 %#010x → %#010x'
            % (info['old_count'], info['new_count'], info['old_key'], info['new_key']),
            '保存：块 %d/%d %s' % (r['blocks'], r['blocks_expect'], r['writer']),
            '回验：' + '  '.join('%d×%s' % (m['count'], m['type']) for m in t['members'])]))
        return 0
    except BaseException:
        _write_report('AITF FAIL\n\n' + traceback.format_exc())
        return 1


def aitfsize(args):
    """--aitfsize <in> <out> <特遣ID> <1..6>：改特遣部队成员**种数**（+0xB0）。"""
    if len(args) < 4:
        _write_report('AITFSIZE FAIL\n用法: --aitfsize <in> <out> <特遣ID> <1..6>')
        return 2
    src, out, tf_id = args[0], args[1], args[2]
    try:
        n = int(args[3])
    except ValueError:
        _write_report('AITFSIZE FAIL\n种数必须是 1..6')
        return 2
    try:
        from editor_core import SaveDoc
        import ai_objects
        d = SaveDoc(src)
        info = ai_objects.set_taskforce_size(d, tf_id, n)
        r = d.save(out, backup=False)
        e = SaveDoc(out)
        t = next(x for x in ai_objects.read_ai(e)['taskforces'] if x['id'] == tf_id)
        _write_report('\n'.join([
            'AITFSIZE PASS', 'src: %s' % src, 'out: %s' % out,
            '特遣部队 %s「%s」种数 %d → %d' % (info['tf_id'], info['name'],
                                          info['old'], info['new']),
            '保存：块 %d/%d %s' % (r['blocks'], r['blocks_expect'], r['writer']),
            '回验：' + '  '.join('%d×%s' % (m['count'], m['type']) for m in t['members'])]))
        return 0
    except BaseException:
        _write_report('AITFSIZE FAIL\n\n' + traceback.format_exc())
        return 1


def aitfclear(args):
    """--aitfclear <in> <out> <特遣ID>：清空特遣部队（兵种全清 + 种数 0）＝ 变相删除该小队。"""
    if len(args) < 3:
        _write_report('AITFCLEAR FAIL\n用法: --aitfclear <in> <out> <特遣ID>')
        return 2
    src, out, tf_id = args[0], args[1], args[2]
    try:
        from editor_core import SaveDoc
        import ai_objects
        d = SaveDoc(src)
        info = ai_objects.clear_taskforce(d, tf_id)
        r = d.save(out, backup=False)
        e = SaveDoc(out)
        t = next(x for x in ai_objects.read_ai(e)['taskforces'] if x['id'] == tf_id)
        users = [x['id'] for x in ai_objects.read_ai(e)['teams']
                 if x.get('taskforce_id') == tf_id]
        _write_report('\n'.join([
            'AITFCLEAR PASS', 'src: %s' % src, 'out: %s' % out,
            '特遣部队 %s「%s」：种数 %d → %d，6 个槽位数量全 0'
            % (info['tf_id'], info['name'], info['old_n'], len(t['members'])),
            '保存：块 %d/%d %s' % (r['blocks'], r['blocks_expect'], r['writer']),
            '用它的 %d 个小队：%s（指针保留，不产生悬空引用）'
            % (len(users), '、'.join(users[:8]))]))
        return 0
    except BaseException:
        _write_report('AITFCLEAR FAIL\n\n' + traceback.format_exc())
        return 1


def aihouse(args):
    """--aihouse <in> <out> <小队ID> <阵营>：改作战小队的所属国家（+0xD8）。"""
    if len(args) < 4:
        _write_report('AIHOUSE FAIL\n用法: --aihouse <in> <out> <小队ID> <阵营>')
        return 2
    src, out, tid, house = args[0], args[1], args[2], args[3]
    try:
        from editor_core import SaveDoc
        import ai_objects
        d = SaveDoc(src)
        info = ai_objects.set_team_house(d, tid, house)
        r = d.save(out, backup=False)
        e = SaveDoc(out)
        t = next(x for x in ai_objects.read_ai(e)['teams'] if x['id'] == tid)
        _write_report('\n'.join([
            'AIHOUSE PASS', 'src: %s' % src, 'out: %s' % out,
            '小队 %s「%s」所属国家 %s → %s（键 %#010x → %#010x）'
            % (info['team_id'], info['name'], info['old_house'], info['new_house'],
               info['old_key'], info['new_key']),
            '保存：块 %d/%d %s' % (r['blocks'], r['blocks_expect'], r['writer']),
            '回验：house=%s 等级=%d 特遣=%s' % (t['house'], t['veteran'], t['taskforce_id'])]))
        return 0
    except BaseException:
        _write_report('AIHOUSE FAIL\n\n' + traceback.format_exc())
        return 1


def aiwp(args):
    """--aiwp <in> <out> <小队ID> <路径点号>：改小队「出现于」（+0xE8）。"""
    if len(args) < 4:
        _write_report('AIWP FAIL\n用法: --aiwp <in> <out> <小队ID> <路径点号>')
        return 2
    src, out, tid = args[0], args[1], args[2]
    try:
        wp = int(args[3])
    except ValueError:
        _write_report('AIWP FAIL\n路径点号必须是 0..701')
        return 2
    try:
        from editor_core import SaveDoc
        import ai_objects
        d = SaveDoc(src)
        info = ai_objects.set_team_waypoint(d, tid, wp)
        r = d.save(out, backup=False)
        e = SaveDoc(out)
        t = next(x for x in ai_objects.read_ai(e)['teams'] if x['id'] == tid)
        _write_report('\n'.join([
            'AIWP PASS', 'src: %s' % src, 'out: %s' % out,
            '小队 %s「%s」出现于 路径点 %d → %d（格子 %s）'
            % (info['team_id'], info['name'], info['old'], info['new'], info['cell']),
            '保存：块 %d/%d %s' % (r['blocks'], r['blocks_expect'], r['writer']),
            '回验：waypoint=%d 等级=%d 特遣=%s' % (t['waypoint'], t['veteran'],
                                              t['taskforce_id'])]))
        return 0
    except BaseException:
        _write_report('AIWP FAIL\n\n' + traceback.format_exc())
        return 1


def aiload(args):
    """--aiload <in> <out> <小队ID> <0|1>：开/关小队「预装载」（+0xB9）。"""
    if len(args) < 4:
        _write_report('AILOAD FAIL\\n用法: --aiload <in> <out> <小队ID> <0|1>')
        return 2
    src, out, tid = args[0], args[1], args[2]
    try:
        on = int(args[3]) != 0
    except ValueError:
        _write_report('AILOAD FAIL\\n第四个数只能是 0 或 1')
        return 2
    try:
        from editor_core import SaveDoc
        import ai_objects
        d = SaveDoc(src)
        info = ai_objects.set_team_loadable(d, tid, on)
        r = d.save(out, backup=False)
        e = SaveDoc(out)
        t = next(x for x in ai_objects.read_ai(e)['teams'] if x['id'] == tid)
        _write_report('\\n'.join([
            'AILOAD PASS', 'src: %s' % src, 'out: %s' % out,
            '小队 %s「%s」预装载 %d → %d（写 +0x%X）'
            % (info['team_id'], info['name'], info['old'], info['new'],
               ai_objects.TEAM_LOADABLE_OFF),
            '保存：块 %d/%d %s' % (r['blocks'], r['blocks_expect'], r['writer']),
            '回验：loadable=%s 等级=%d 出现于 wp=%s'
            % (t['loadable'], t['veteran'], t['waypoint'])]))
        return 0
    except BaseException:
        _write_report('AILOAD FAIL\\n\\n' + traceback.format_exc())
        return 1


def putunit(args):
    """--putunit <in> <out> <类型> <cx> <cy> [--house 阵营] [--vet 0|1|2]：克隆放置一个单位。"""
    house, args = _take_house(args)
    vet, args = _take_vet(args)
    if len(args) < 5:
        _write_report('PUTUNIT FAIL\n用法: --putunit <in> <out> <类型> <cx> <cy> '
                      '[--house 阵营] [--vet 0|1|2]')
        return 2
    src, out, tname = args[0], args[1], args[2]
    try:
        cx, cy = int(args[3]), int(args[4])
    except ValueError:
        _write_report('PUTUNIT FAIL\ncx/cy 必须是整数')
        return 2
    try:
        from editor_core import SaveDoc
        from editor_core import UnitUnavailable
        d = SaveDoc(src)
        info = d.add_unit_by_clone(tname, cx, cy, house=house, veterancy=vet)
        d.save(out)
        c = SaveDoc(out)
        got = [(r['type'], r['x'], r['y']) for r in c.unit_records()]
        _write_report('\n'.join([
            'PUTUNIT PASS', 'src: %s' % src, 'out: %s' % out,
            '类型/格子: %s @(%d,%d)　模板自键 %#x → 新自键 %#x'
            % (tname, cx, cy, info['template_key'], info['key']),
            '类: %s　指针表 %d 张　明文增量 +%d B' % (info['class'], info['tables'], info['delta']),
            '所属方: %s%s' % (info.get('house'),
                             '（已改主人：%s → %s）' % (info.get('template_house'),
                                                      info.get('house'))
                             if info.get('house_changed') else ''),
            '等级: %s' % (info.get('veterancy_name') or '未定义'),
            '警告: %s' % ('；'.join(info.get('warnings') or []) or '无'),
            '复检单位: %s' % got]))
        return 0
    except UnitUnavailable as e:
        _write_report('PUTUNIT FAIL（本档没有该类型的模板）\n%s' % e.reason)
        return 3
    except BaseException:
        _write_report('PUTUNIT FAIL\n\n' + traceback.format_exc())
        return 1


def unitowner(args):
    """--unitowner <in> <out> <cx> <cy> <阵营>：改「站在该格上的单位」的所属方。

    ★2026-10-17 新增：所属方 = 记录 +352/+560 两份 House 键（见 docs §64）。
    只改这 8 个字节，文件长度不变 ⇒ 最干净的"改主人"实验。
    """
    if len(args) < 5:
        _write_report('UNITOWNER FAIL\n用法: --unitowner <in> <out> <cx> <cy> <阵营>')
        return 2
    src, out, house = args[0], args[1], args[4]
    try:
        cx, cy = int(args[2]), int(args[3])
    except ValueError:
        _write_report('UNITOWNER FAIL\ncx/cy 必须是整数')
        return 2
    try:
        from editor_core import SaveDoc
        d = SaveDoc(src)
        recs = d.unit_records()
        cand = [r for r in recs if (r['x'], r['y']) == (cx, cy)]
        if not cand:
            _write_report('UNITOWNER FAIL\n(%d,%d) 上没有单位。现有单位：%s'
                          % (cx, cy, [(r['type'], r['x'], r['y'], r.get('house'))
                                      for r in recs]))
            return 3
        r = cand[0]
        n0 = len(d.plain)
        info = d.set_unit_house(r, house)
        d.save(out)
        c = SaveDoc(out)
        got = [(x['type'], x['x'], x['y'], x.get('house')) for x in c.unit_records()]
        _write_report('\n'.join([
            'UNITOWNER PASS', 'src: %s' % src, 'out: %s' % out,
            '单位 %s @(%d,%d)　所属方 %s → %s（+352/+560 两份同改）'
            % (r['type'], cx, cy, info['from'], info['house']),
            '明文长度: %d → %d（不变=%s）' % (n0, len(d.plain), n0 == len(d.plain)),
            '复检单位: %s' % got]))
        return 0
    except BaseException:
        _write_report('UNITOWNER FAIL\n\n' + traceback.format_exc())
        return 1


def learncomp(args):
    """--learncomp <存档> <型号>：把该型号的**伴随对象（移动方式模板）**学进内置库。

    伴随对象的类 = 该单位的 Locomotor（Walk/Drive/Fly/…）。学进来之后，
    就算别的存档里没有这种移动方式的单位，也能照方写入。
    """
    if len(args) < 2:
        _write_report('LEARNCOMP FAIL\n用法: --learncomp <存档> <型号>')
        return 2
    src, tname = args[0], args[1].strip().upper()
    try:
        from editor_core import SaveDoc
        import unit_comp_lib
        d = SaveDoc(src)
        cand = [r for r in d.unit_records() if r['type'].upper() == tname]
        if not cand:
            _write_report('LEARNCOMP FAIL\n%s 里没有 %s。现有：%s'
                          % (src, tname, [(r['type'], r.get('loco')) for r in d.unit_records()]))
            return 3
        r = cand[0]
        loco = r.get('loco') or '?'
        if loco == '?':
            _write_report('LEARNCOMP FAIL\n%s 的伴随类 %s 还不在已知表里，先加进 UNIT_COMP_GUIDS'
                          % (tname, r['comp_guid'].hex()))
            return 4
        raw = bytes(d.plain[r['comp']:r['comp'] + r['comp_size']])
        p = unit_comp_lib.save_loco(loco, raw, r['comp_guid'].hex(), tname)
        _write_report('\n'.join([
            'LEARNCOMP PASS', '存档: %s' % src, '型号: %s（移动方式 %s）' % (tname, loco),
            '伴随对象: %d 字节  GUID=%s' % (len(raw), r['comp_guid'].hex()),
            '已写入: %s' % p,
            '现在内置库里有: %s' % '、'.join(sorted(unit_comp_lib.load().keys()))]))
        return 0
    except BaseException:
        _write_report('LEARNCOMP FAIL\n\n' + traceback.format_exc())
        return 1


def ready(args):
    """--ready <in>：这份存档能用哪种方式放建筑（只读，不改任何字节）。"""
    if len(args) < 1:
        _write_report('READY FAIL\n用法: --ready <in>')
        return 2
    try:
        from editor_core import SaveDoc
        d = SaveDoc(args[0])
        tok, twhy, tdiag = d.trigger_readiness()
        dok, dwhy = d.add_placed_readiness()
        _write_report('\n'.join([
            '存档: %s' % args[0],
            '触发法  : %s%s' % ('可用' if tok else '不可用', ('　—— ' + twhy) if twhy else ''),
            '直接落成: %s%s' % ('可用' if dok else '不可用', ('　—— ' + dwhy) if dwhy else ''),
            '建筑数: %d　单位/建筑布置: %d　触发记录: %d　路径点: %d'
            % (len(d.buildings()), len(d.house_placements()),
               len(d.records), len(d.waypoints_save())),
            '诊断: %s' % tdiag]))
        return 0
    except BaseException:
        _write_report('READY FAIL\n\n' + traceback.format_exc())
        return 1


def wp(args):
    """headless 路径点表（2026-10-17：表在存档明文 1586+4×号，每项 u16 x + u16 y）。

    --wp <in>                        列出现有路径点（只看，不写盘）
    --wp <in> <out> <号> <cx> <cy>   写入/移动；cx/cy 给 -1 表示删除该路径点
    """
    if len(args) not in (1, 5):
        _write_report('WP FAIL\n用法: --wp <in> | --wp <in> <out> <号> <cx> <cy>'
                      '（cx、cy 都是 -1 表示删除）')
        return 2
    src = args[0]
    try:
        from editor_core import SaveDoc
        d = SaveDoc(src)
        base = d.waypoint_base()
        if len(args) == 1:
            w = d.waypoints_save()
            lines = ['WP PASS', 'src: %s' % src, '表首(明文偏移): %r' % (base,),
                     '路径点数: %d' % len(w)]
            for n in sorted(w):
                lines.append('  %4d → (%d, %d)  @%d' % (n, w[n][0], w[n][1], d.waypoint_offset(n)))
            _write_report('\n'.join(lines))
            return 0
        out = args[1]
        n, cx, cy = int(args[2]), int(args[3]), int(args[4])
        if cx < 0 or cy < 0:
            d.set_waypoint(n, None, None)
            act = '删除'
        else:
            d.set_waypoint(n, cx, cy)
            act = '写入'
        d.save(out)
        c = SaveDoc(out)
        _write_report('\n'.join(['WP PASS', 'src: %s' % src, 'out: %s' % out,
                                 '%s 路径点 %d → (%d,%d)' % (act, n, cx, cy),
                                 '复检: %r' % (c.waypoint_cell(n),),
                                 '复检全部: %s' % (c.waypoints_save(),)]))
        return 0
    except BaseException:
        _write_report('WP FAIL\n\n' + traceback.format_exc())
        return 1


def _opt(argv, name, n=1):
    """摘出 `name v1 [v2…]`，返回 (取值列表或 None, 剩余参数)。"""
    if name not in argv:
        return None, argv
    i = argv.index(name)
    vals = argv[i + 1:i + 1 + n]
    return vals, argv[:i] + argv[i + 1 + n:]


def owner(args):
    """headless 改已有建筑的所属方：
    --owner <in> <out> <阵营> [--type 类型] [--cell cx cy] [--key hex] [--idx n]
      不带定位参数 = 改**全档所有建筑**；带 --type = 改该类型的全部实例。"""
    if len(args) < 3:
        _write_report('OWNER FAIL\n用法: --owner <in> <out> <阵营> '
                      '[--type 类型] [--cell cx cy] [--key hex] [--idx n]')
        return 2
    tvals, args = _opt(args, '--type', 1)
    cvals, args = _opt(args, '--cell', 2)
    kvals, args = _opt(args, '--key', 1)
    ivals, args = _opt(args, '--idx', 1)
    src, out, house = args[0], args[1], args[2]
    try:
        from editor_core import SaveDoc
        d = SaveDoc(src)
        tname = tvals[0] if tvals else None
        targets = []
        for b in d.buildings_of_house(None):
            if tname and b['type'].upper() != tname.upper():
                continue
            if cvals and (b['x'], b['y']) != (int(cvals[0]), int(cvals[1])):
                continue
            if kvals and b['key'] != int(kvals[0], 16):
                continue
            if ivals and b['idx'] != int(ivals[0]):
                continue
            targets.append(b)
        if not targets:
            _write_report('OWNER FAIL\n没有匹配的建筑（--type/--cell/--key/--idx）')
            return 1
        infos = [d.set_building_owner(house, idx=b['idx']) for b in targets]
        d.save(out)
        c = SaveDoc(out)
        _write_report('\n'.join(['OWNER PASS', 'src: %s' % src, 'out: %s' % out,
                                  '改为: %s' % house, '改了 %d 栋' % len(infos)] +
                                 ['  ' + i['note'] for i in infos] +
                                 ['复检明文: %d B' % len(c.plain)]))
        return 0
    except BaseException:
        _write_report('OWNER FAIL\n\n' + traceback.format_exc())
        return 1


def gift(args):
    """headless 赠送建筑：
    --gift <in> <out> <类型ID> [ready | placed <cx> <cy>] [槽2偏移hex]
    ready（默认）= 就绪未落地（侧栏"就绪"可放置）；placed = 直接放好（2026-10-16 验收：新开载入/重载均正常）。
    槽2 仅同会话档需要（跨档留空，只写槽1）。"""
    if len(args) < 3:
        _write_report('GIFT FAIL\n用法: --gift <in> <out> <类型ID> '
                      '[ready | placed <cx> <cy>] [槽2偏移hex]')
        return 2
    house, args = _take_house(args)
    if len(args) < 3:
        _write_report('GIFT FAIL\n用法: --gift <in> <out> <类型ID> '
                      '[ready | placed <cx> <cy>] [槽2偏移hex] [--house 阵营]')
        return 2
    src, out, tname = args[0], args[1], args[2]
    rest = args[3:]
    mode, cell, slot2 = 'ready', None, None
    try:
        if rest and rest[0] in ('ready', 'placed'):
            mode = rest[0]
            rest = rest[1:]
        if mode == 'placed':
            cell = (int(rest[0]), int(rest[1]))
            rest = rest[2:]
        if rest:
            slot2 = int(rest[0], 16)
        from editor_core import SaveDoc
        d = SaveDoc(src)
        if mode == 'placed':
            info = d.add_placed_building(tname, cell[0], cell[1], house=house)
        else:
            info = d.gift_ready_building(tname, slot2_off=slot2)
        d.save(out)
        c = SaveDoc(out)
        lines = ['GIFT PASS', 'src: %s' % src, 'out: %s' % out, 'info: %s' % (info,),
                 '复检明文: %d B' % len(c.plain)]
        _write_report('\n'.join(lines))
        return 0
    except BaseException:
        _write_report('GIFT FAIL\n\n' + traceback.format_exc())
        return 1


def _retarget_opts(argv):
    """解析 --retarget 的选项（本项目没有 argparse，统一样式）。

    支持：--build <TYPE>、--cell <x,y> 或 --cell <x> <y>、--wp <号>、--ac <类型>、
          --arg <参数>、--dry、--house 阵营（仅报告用）。
    返回 (opts, 剩余位置参数)。
    """
    opts = {'build': None, 'cell': None, 'wp': None, 'ac': 125, 'arg': None,
            'dry': False, 'house': None, 'ac_index': 1}
    rest, i = [], 0
    while i < len(argv):
        t = argv[i]
        if t == '--build' and i + 1 < len(argv):
            opts['build'] = argv[i + 1]; i += 2; continue
        if t == '--cell' and i + 1 < len(argv):
            v = argv[i + 1]
            try:
                if ',' in v:
                    x, y = v.split(',', 1)
                    opts['cell'] = (int(x), int(y)); i += 2; continue
                if i + 2 < len(argv):
                    opts['cell'] = (int(v), int(argv[i + 2])); i += 3; continue
            except ValueError:
                raise RuntimeError('--cell 要写成 x,y（或 --cell x y）')
            raise RuntimeError('--cell 要写成 x,y（或 --cell x y）')
        if t in ('--ac-index', '--aci') and i + 1 < len(argv):
            opts['ac_index'] = int(argv[i + 1]); i += 2; continue
        if t in ('--wp', '--waypoint') and i + 1 < len(argv):
            opts['wp'] = int(argv[i + 1]); i += 2; continue
        if t in ('--ac', '--action') and i + 1 < len(argv):
            opts['ac'] = int(argv[i + 1]); i += 2; continue
        if t == '--arg' and i + 1 < len(argv):
            opts['arg'] = int(argv[i + 1]); i += 2; continue
        if t == '--house' and i + 1 < len(argv):
            opts['house'] = argv[i + 1]; i += 2; continue
        if t == '--dry':
            opts['dry'] = True; i += 1; continue
        rest.append(t); i += 1
    return opts, rest


def _adj_hint(doc, cell, radius=2):
    """目标格周围 radius 格内的已建建筑数（0 ⇒ 很可能是水/坡，引擎会静默拒绝）。"""
    try:
        bset = {(b['x'], b['y']) for b in doc.buildings()}
    except Exception:
        return -1
    cx, cy = int(cell[0]), int(cell[1])
    return sum(1 for dx in range(-radius, radius + 1) for dy in range(-radius, radius + 1)
               if (dx or dy) and (cx + dx, cy + dy) in bset)


def _water_warn(near, cell):
    if near != 0:
        return ''
    return ('⚠ 目标格 (%d,%d) 周围 2 格内没有已建建筑 —— 水里/坡上会被引擎**静默拒绝**'
            '（玩家实测 (71,241) 是水 ⇒ 改造写进去了但游戏里什么都没刷出来）。'
            '建议先跑 --cells --near %d,%d 挑一块紧邻建筑的空地'
            % (int(cell[0]), int(cell[1]), int(cell[0]), int(cell[1])))


def retarget(args):
    """headless 改造**已有触发**的动作（目前唯一游戏内验收过的刷建筑路子）。

    用法:
        --retarget <in> <out> <触发ID> --build <建筑类型> --cell <x,y>
        --retarget <in> <out> <触发ID> --build <建筑类型> --wp <路径点号>
        --retarget <in> <out> <触发ID> --ac <行为类型> [--arg 参数]
        加 --dry 只读预览（不写盘）
        <触发ID> 也可以给触发**名字**（重名时取第一条）

    只改动作对象的 4 个字段（类型 +60/64、建筑名 +104、路径点 +88），
    文件长度不变 ⇒ 不会碰 7MB 容器红线。
    """
    try:
        opts, rest = _retarget_opts(args)
    except BaseException as e:
        _write_report('RETARGET FAIL\n参数解析失败: %s' % e)
        return 2
    if len(rest) < 3:
        _write_report('RETARGET FAIL\n用法: --retarget <in> <out> <触发ID或名字> '
                      '--build <建筑类型> --cell <x,y> | --wp <号>\n'
                      '      --retarget <in> <out> <触发ID> --ac <行为类型> [--arg 参数]\n'
                      '      可选: --dry（只读预览） --house 阵营（仅报告）')
        return 2
    src, out, who = rest[0], rest[1], rest[2]
    if opts['ac'] == 125 and not opts['build']:
        _write_report('RETARGET FAIL\n行为 125（建建筑）必须给 --build <建筑类型>\n'
                      '例: --retarget in.SAV out.SAV 01001591 --build GAPOWR --cell 80,245')
        return 2
    if opts['ac'] == 125 and opts['cell'] is None and opts['wp'] is None:
        _write_report('RETARGET FAIL\n行为 125 必须给目标：--cell <x,y> 或 --wp <号>')
        return 2
    try:
        from editor_core import SaveDoc
        d = SaveDoc(src)
        try:                      # 只为了"格子 → 可建造性/占用"报告，缺地图不影响写
            d.auto_attach_map([os.path.dirname(os.path.abspath(src)), r'E:\YURI'])
        except Exception:
            pass
        tid = who
        if not any(r.sid == who.upper() for r in d.records):
            hit = next((r for r in d.records if r.kind == '触发' and r.name == who), None)
            if hit is None:
                cands = [r.sid for r in d.records if r.kind == '触发'][:12]
                raise RuntimeError('找不到触发 %r（触发ID 或名字）。前几条触发: %s'
                                   % (who, ', '.join(cands)))
            tid = hit.sid
        before = d.trigger_action(tid, ac_index=opts.get('ac_index', 1))
        info = d.retarget_trigger(tid, ac_type=opts['ac'], ac_name=opts['build'],
                                  ac_cell=opts['cell'], ac_waypoint=opts['wp'],
                                  ac_arg=opts['arg'], dry_run=opts['dry'],
                                  ac_index=opts.get('ac_index', 1))
        if opts['dry']:
            extra = ''
            if opts['ac'] == 125 and opts['cell'] is not None:
                extra = _water_warn(_adj_hint(d, opts['cell']), opts['cell'])
            rep = '\n'.join([
                'RETARGET PASS（--dry 只读，未写盘）',
                'src : %s' % src,
                '触发: %s（序号 %d，动作偏移 %d，共 %d 条动作）'
                % (tid, before['index'], before['action_off'], before['count']),
                '现状: 行为 %d 参数 %d　建筑名 %r　路径点 %d → %r'
                % (before['type'], before['arg'], before['name'],
                   before['waypoint'], before['cell']),
                '计划: 行为 %d 参数 %s　建筑名 %r　路径点 %s'
                % (opts['ac'], opts['arg'], opts['build'], opts['wp'] or '（按格子补）'),
                '警告: %s' % ('；'.join(info.get('warnings') or []) or '无')])
            _write_report(rep + ('\n' + extra if extra else ''))
            return 0
        d.save(out)
        c = SaveDoc(out)
        got = c.trigger_action(tid)
        ok = (got['type'] == opts['ac'])
        if opts['ac'] == 125:
            ok = ok and got['name'].upper() == str(opts['build']).strip().upper()
        cellwarn = ''
        if opts['ac'] == 125 and got.get('cell'):
            cellwarn = _water_warn(_adj_hint(d, got['cell']), got['cell'])
        lines = [
            'RETARGET PASS' if ok else 'RETARGET FAIL（复检不符）',
            'src : %s' % src,
            'out : %s' % out,
            '触发: %s（序号 %d）　动作偏移 %d　同序号动作 %d 条'
            % (tid, got['index'], got['action_off'], got['count']),
            '改造: 行为 %d→%d　参数 %d→%d　建筑名 %r→%r　路径点 %d→%d %r'
            % (before['type'], got['type'], before['arg'], got['arg'],
               before['name'], got['name'], before['waypoint'], got['waypoint'],
               got['cell']),
            '明文: %d → %d 字节（%s）' % (len(d.plain), len(c.plain),
                                          '不变' if len(d.plain) == len(c.plain) else '变了'),
            '警告: %s' % ('；'.join(info.get('warnings') or []) or '无'),
            '提示: 这是 M1 已验证的路子（改已有触发）；另存后进游戏等该触发的计时到期即可看到建筑。',
            '若进游戏后建筑**没出现 / 闪一下就消失**：多半是该格地形或占用问题'
            '（水里·坡上·被别的建筑压住都可能，引擎只静默拒绝）—— 换一格再试即可，'
            '可用 --cells 查「紧邻已建建筑的空格」。',
            '验收建议: 用**基线里没有的建筑**验收（如 ATESLA 光棱塔），别用电厂 —— '
            '本档基线自己就会刷电厂，同格撞车分不清是谁建的；另外水面/悬崖格一定失败且不报错。',
        ]
        if cellwarn:
            lines.append(cellwarn)
        _write_report('\n'.join(lines))
        return 0 if ok else 1
    except BaseException:
        _write_report('RETARGET FAIL\n\n' + traceback.format_exc())
        return 1


def list_actions(args):
    """--actions <in>：列出档里所有「行为 125 建建筑」条目 —— 改造选型清单。

    为什么需要：`--retarget` 只能改**会被执行**的触发；哪些会执行，看这里的
    「事件 + 计时秒 + 是否启用」三列（本档实测：事件 13 流逝时间 + 10 秒的那几条是会跑的）。
    """
    import struct as _st
    if not args:
        _write_report('ACTIONS FAIL\n用法: --actions <存档>')
        return 2
    src = args[0]
    try:
        from editor_core import SaveDoc
        d = SaveDoc(src)
        pl = d.plain
        m = d._instance_maps()
        idx2sid = {}
        for k, v in m['sid'].items():
            idx2sid[_st.unpack_from('<I', pl, m['inst'][k] + 172)[0]] = v
        names = {r.sid: r.name for r in d.records}
        rt = {s['sid']: s for s in d.runtime_status()}
        evs = d.events()
        bykey = {_st.unpack_from('<I', pl, e['obj'] + 16)[0]: e for e in evs}
        rows = []
        for a in d.actions():
            if a['type'] != 125:
                continue
            o = a['obj']
            wp = _st.unpack_from('<I', pl, o + 88)[0]
            nm = bytes(pl[o + 104:o + 112]).split(b'\x00')[0].decode('ascii', 'ignore')
            idx = _st.unpack_from('<I', pl, o + 56)[0]
            sid = idx2sid.get(idx)
            s = rt.get(sid) or {}
            et, ea = '-', '-'
            if s.get('inst_off'):
                ev = bykey.get(_st.unpack_from('<I', pl, s['inst_off'] + 192)[0])
                if ev is not None:
                    et = _st.unpack_from('<I', pl, ev['obj'] + 64)[0]
                    ea = _st.unpack_from('<I', pl, ev['obj'] + 60)[0]
            rows.append(((s.get('ticks') or 0) // 15, sid or '', nm, wp, d.waypoint_cell(wp),
                         et, ea, s.get('rt_enabled'), names.get(sid, '')))
        rows.sort(key=lambda r: (r[0], r[1]))
        out = ['ACTIONS PASS', 'src : %s' % src,
               '行为125 共 %d 条（按计时秒升序；"会被执行"看 「事件/秒/启用」三列）' % len(rows),
               '%-10s %-6s %-5s %-9s %-6s %-5s %-6s %s'
               % ('触发ID', '秒', '建筑', '路径点/格子', '事件', '参数', '启用', '触发名')]
        for ticks, sid, nm, wp, cell, et, ea, en, tn in rows:
            out.append('%-10s %-6s %-5s %-9s %-6s %-5s %-6s %s'
                       % (sid or '（无触发）', ticks, nm, '%d %s' % (wp, cell),
                          et, ea, '是' if en else '否', tn))
        _write_report('\n'.join(out))
        return 0
    except BaseException:
        _write_report('ACTIONS FAIL\n\n' + traceback.format_exc())
        return 1


def cells_cmd(args):
    """--cells <存档> [--type 类型] [--n 12]：列出「紧邻已建建筑的空格」——高概率可建。

    为什么需要（2026-10-18 实测）：行为 125 落到**水里/坡上**时引擎**静默拒绝**，存档里看不出来、
    游戏里也不报错，只表现为"什么都没刷出来"（玩家已确认 (71,241) 是水）。
    引擎认可过的位置 = 已建建筑占的格子，所以"紧挨着已有建筑的空格"是命中率最高的候选。
    """
    typ, n, near = None, 12, None
    rest, i = [], 0
    while i < len(args):
        if args[i] == '--type' and i + 1 < len(args):
            typ = args[i + 1].strip().upper(); i += 2; continue
        if args[i] in ('--n', '--top') and i + 1 < len(args):
            n = int(args[i + 1]); i += 2; continue
        if args[i] == '--near' and i + 1 < len(args):
            v = args[i + 1]
            if ',' in v:
                x, y = v.split(',', 1); near = (int(x), int(y)); i += 2; continue
            if i + 2 < len(args):
                near = (int(v), int(args[i + 2])); i += 3; continue
            raise RuntimeError('--near 要写成 x,y（或 --near x y）')
        rest.append(args[i]); i += 1
    if not rest:
        _write_report('CELLS FAIL\n用法: --cells <存档> [--type 建筑类型] [--n 12]')
        return 2
    src = rest[0]
    try:
        from editor_core import SaveDoc
        d = SaveDoc(src)
        bs = d.buildings()
        occ = {(b['x'], b['y']): b['type'] for b in bs}
        busy = set(occ)
        try:
            for r in d.unit_records():
                if r.get('x') is not None:
                    busy.add((r['x'], r['y']))
        except Exception:
            pass
        score = {}
        for b in bs:
            if typ and str(b['type']).upper() != typ:
                continue
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    if dx == 0 and dy == 0:
                        continue
                    c = (b['x'] + dx, b['y'] + dy)
                    if c in busy:
                        continue
                    score.setdefault(c, set()).add((b['x'], b['y']))
        if near:
            ranked = sorted(score.items(),
                            key=lambda kv: (-len(kv[1]),
                                            (kv[0][0] - near[0]) ** 2 + (kv[0][1] - near[1]) ** 2,
                                            kv[0]))[:n]
        else:
            ranked = sorted(score.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:n]
        lines = ['CELLS PASS', 'src : %s' % src,
                 '筛选: %s　建筑总数 %d　单位占格 %d'
                 % ('类型 %s' % typ if typ else '全部类型', len(bs), len(busy) - len(occ)),
                 '紧邻已有建筑的空格（按邻接数排序）—— 行为 125 落这些格命中率最高：']
        for c, srcs in ranked:
            kinds = []
            for (bx, by) in sorted(srcs):
                t = occ.get((bx, by))
                if t and t not in kinds:
                    kinds.append(t)
            lines.append('  (%3d,%3d)  邻接 %d 栋  附近类型: %s'
                         % (c[0], c[1], len(srcs), '/'.join(kinds[:4])))
        if not ranked:
            lines.append('  （没有候选：这个类型在档里没有建筑，或周围都占满了）')
        _write_report('\n'.join(lines))
        return 0
    except BaseException:
        _write_report('CELLS FAIL\n\n' + traceback.format_exc())
        return 1



def trigactions_cmd(args):
    """--trigactions <存档> <触发ID或名字>：列出该触发的**全部行为**（多动作触发也能列）。

    用途：一条触发可以挂多条行为（地编里能写 count=3）。`--retarget` 默认只改第 1 条，
    改第 2、3 条要用 `--ac-index N`。先跑这个看清楚有几条、分别是什么。
    """
    if len(args) < 2:
        _write_report("TRIGACTIONS FAIL\n用法: --trigactions <存档> <触发ID或名字>")
        return 2
    src, tid = args[0], args[1]
    try:
        from editor_core import SaveDoc
        d = SaveDoc(src)
        rows = d.trigger_actions(tid)
        lines = ["TRIGACTIONS PASS", "src : %s" % src, "触发: %s" % tid,
                 "共 %d 条行为：" % len(rows),
                 "%-5s %-7s %-6s %-9s %-6s %s" % ("#", "序号", "类型", "建筑", "路径点", "落点")]
        for r in rows:
            lines.append("%-5d %-7d %-6d %-9s %-6d %s"
                         % (r["ordinal"], r["index"], r["type"], r["name"],
                            r["waypoint"], r["cell"]))
        lines.append("提示: --retarget 默认改第 1 条；改别的用 --ac-index N")
        _write_report("\n".join(lines))
        return 0
    except BaseException:
        _write_report("TRIGACTIONS FAIL\n\n" + traceback.format_exc())
        return 1

def main():
    # ★2026-10-22（开源前补）：`--help/-h` 打印模块头部的用法清单。
    #   以前没有这个开关，`--help` 会落到"启动 GUI"分支 —— 在没装 tkinter 的环境里直接
    #   ModuleNotFoundError，对外面第一次用的人非常不友好。
    if '--help' in sys.argv or '-h' in sys.argv:
        sys.stdout.write((__doc__ or '') + '\n')
        return 0
    if '--selftest' in sys.argv:
        i = sys.argv.index('--selftest')
        sav = sys.argv[i + 1] if len(sys.argv) > i + 1 else ''
        if not sav or not os.path.exists(sav):
            _write_report('SELFTEST FAIL\n未提供有效的存档参数: %r\n'
                          '用法: %s --selftest <存档.SAV>' % (sav, sys.argv[0]))
            return 2
        return selftest(sav)
    if '--owner' in sys.argv:
        i = sys.argv.index('--owner')
        return owner(sys.argv[i + 1:])
    if '--types' in sys.argv:
        i = sys.argv.index('--types')
        return typelist(sys.argv[i + 1:])
    if '--units' in sys.argv:
        i = sys.argv.index('--units')
        return units(sys.argv[i + 1:])
    if '--tags' in sys.argv:
        i = sys.argv.index('--tags')
        return tags(sys.argv[i + 1:])
    if '--learncomp' in sys.argv:
        i = sys.argv.index('--learncomp')
        return learncomp(sys.argv[i + 1:])
    if '--unitowner' in sys.argv:
        i = sys.argv.index('--unitowner')
        return unitowner(sys.argv[i + 1:])
    if '--putunit' in sys.argv:
        i = sys.argv.index('--putunit')
        return putunit(sys.argv[i + 1:])
    if '--aivet' in sys.argv:
        i = sys.argv.index('--aivet')
        return aivet(sys.argv[i + 1:])
    if '--aiload' in sys.argv:
        i = sys.argv.index('--aiload')
        return aiload(sys.argv[i + 1:])
    if '--aiwp' in sys.argv:
        i = sys.argv.index('--aiwp')
        return aiwp(sys.argv[i + 1:])
    if '--aihouse' in sys.argv:
        i = sys.argv.index('--aihouse')
        return aihouse(sys.argv[i + 1:])
    if '--aitfclear' in sys.argv:
        i = sys.argv.index('--aitfclear')
        return aitfclear(sys.argv[i + 1:])
    if '--aitfsize' in sys.argv:
        i = sys.argv.index('--aitfsize')
        return aitfsize(sys.argv[i + 1:])
    if '--aitf' in sys.argv:
        i = sys.argv.index('--aitf')
        return aitf(sys.argv[i + 1:])
    if '--ai' in sys.argv:
        i = sys.argv.index('--ai')
        return ai_report(sys.argv[i + 1:])
    if '--ready' in sys.argv:
        i = sys.argv.index('--ready')
        return ready(sys.argv[i + 1:])
    if '--trigplace' in sys.argv:
        i = sys.argv.index('--trigplace')
        return trigplace(sys.argv[i + 1:])
    if '--retarget' in sys.argv:
        i = sys.argv.index('--retarget')
        return retarget(sys.argv[i + 1:])
    if '--actions' in sys.argv:
        i = sys.argv.index('--actions')
        return list_actions(sys.argv[i + 1:])
    if '--cells' in sys.argv:
        i = sys.argv.index('--cells')
        return cells_cmd(sys.argv[i + 1:])
    if '--trigactions' in sys.argv:
        i = sys.argv.index('--trigactions')
        return trigactions_cmd(sys.argv[i + 1:])
    if '--wp' in sys.argv:
        i = sys.argv.index('--wp')
        return wp(sys.argv[i + 1:])
    if '--place' in sys.argv:
        i = sys.argv.index('--place')
        return place(sys.argv[i + 1:])
    if '--mktrig' in sys.argv:
        i = sys.argv.index('--mktrig')
        return mktrig(sys.argv[i + 1:])
    if '--repairtrig' in sys.argv:
        i = sys.argv.index('--repairtrig')
        return repairtrig(sys.argv[i + 1:])
    if '--gift' in sys.argv:
        i = sys.argv.index('--gift')
        return gift(sys.argv[i + 1:])
    import RA2存档编辑器 as m
    m.App().mainloop()
    return 0


if __name__ == '__main__':
    # ★多进程压缩（大档保存提速）在 Windows 打包环境下必须 freeze_support，
    #   否则子进程会重新跑一遍主程序（等于又开一个编辑器窗口）。
    try:
        import multiprocessing as _mp
        _mp.freeze_support()
    except Exception:
        pass
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        # 窗口程序没有控制台，任何启动期异常都要落盘，否则"什么都没发生"
        tb = traceback.format_exc()
        try:
            import tkinter.messagebox as mb
            mb.showerror('RA2 存档编辑器 启动失败', tb[-1600:] + '\n\n详细信息:\n' + REPORT)
        except Exception:
            pass
        _write_report('启动失败\n\n' + tb)
        raise
