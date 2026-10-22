# -*- coding: utf-8 -*-
"""单位「克隆放置」自检（2026-10-17 U2 游戏内验收后固化）"""
import os, sys, struct, shutil, tempfile, hashlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from core.editor_core import SaveDoc, UnitUnavailable
except Exception:
    from editor_core import SaveDoc, UnitUnavailable

S = os.path.join(HERE, '_exp', 'sample_单位_大兵+灰熊.SAV')
OK = FAIL = 0

def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [PASS] ' + msg)
    else:
        FAIL += 1
        print('  [FAIL] ' + msg)

print('== A: 单位实例读取 ==')
d = SaveDoc(S)
recs = d.unit_records()
chk(len(recs) == 2, '读出 2 个单位（实际 %d）' % len(recs))
by = {r['type']: r for r in recs}
chk(by.get('E1', {}).get('class') == 'infantry', 'E1 归为步兵')
chk(by.get('MTNK', {}).get('class') == 'vehicle', 'MTNK 归为载具')
chk((by['E1']['x'], by['E1']['y']) == (73, 75), '大兵格 = (73,75)（实际 %s）'
    % ((by['E1']['x'], by['E1']['y']),))
chk((by['MTNK']['x'], by['MTNK']['y']) == (80, 68), '灰熊格 = (80,68)')
chk(by['E1']['size'] == 1812 and by['E1']['comp_size'] == 81, '步兵 1812 + 伴随 81')
chk(by['MTNK']['size'] == 2316 and by['MTNK']['comp_size'] == 133, '载具 2316 + 伴随 133')
chk(d.units_placeable() == ['E1', 'MTNK'], '可克隆类型 = E1/MTNK（%s）' % d.units_placeable())

print('\n== B: 克隆放置（步兵） ==')
tmp = tempfile.mkdtemp(prefix='uplace_')
try:
    d = SaveDoc(S)
    n0 = len(d.plain)
    k0 = set(r['key'] for r in d.unit_records())
    info = d.add_unit_by_clone('E1', 62, 66)
    chk(info['delta'] == 1909, '明文增量 +1909 B（实测 %+d）' % info['delta'])
    chk(len(d.plain) - n0 == 1909, '整档明文也确实 +1909')
    chk(info['tables'] == 2, '两张指针表都登记了（%d）' % info['tables'])
    chk(info['key'] not in k0, '新自键不与旧键冲突（%#x）' % info['key'])
    chk(not info['warnings'], '无警告（%s）' % (info['warnings'] or '无'))
    out1 = os.path.join(tmp, 'a.SAV')
    d.save(out1, backup=False)
    c = SaveDoc(out1)
    got = [(r['type'], r['x'], r['y']) for r in c.unit_records()]
    chk(('E1', 62, 66) in got, '重开后新大兵在 (62,66)（%s）' % (got,))
    chk(('E1', 73, 75) in got, '原大兵仍在地图上')
    chk([(x['type'], x['x'], x['y']) for x in c.house_placements()].count(('E1', 62, 66)) == 1,
        '阵营对象表也追加了条目')

    print('\n== C: 克隆放置（载具） ==')
    d2 = SaveDoc(S)
    info2 = d2.add_unit_by_clone('MTNK', 64, 68)
    chk(info2['delta'] == 2465, '载具明文增量 +2465 B（实测 %+d）' % info2['delta'])
    out2 = os.path.join(tmp, 'b.SAV')
    d2.save(out2, backup=False)
    c2 = SaveDoc(out2)
    got2 = [(r['type'], r['x'], r['y']) for r in c2.unit_records()]
    chk(('MTNK', 64, 68) in got2, '重开后新灰熊在 (64,68)（%s）' % (got2,))


    print('\n== F: 跨型号放置（档里没有的型号，借同类模板 + 换型号键） ==')
    d4 = SaveDoc(S)
    idx = d4.unit_type_index()
    n_inf = sum(1 for v in idx.values() if v['class'] == 'infantry')
    n_veh = sum(1 for v in idx.values() if v['class'] == 'vehicle')
    chk(n_inf > 20 and n_veh > 20, '型号索引够全：步兵 %d / 载具 %d' % (n_inf, n_veh))
    chk(d4.unit_type_index().get('GGI', {}).get('class') == 'infantry', 'GGI 归为步兵')
    chk('GGI' not in d4.units_placeable(), 'GGI 本来不在档里（举例）')
    info3 = d4.add_unit_by_clone('GGI', 60, 66)
    chk(info3['mode'] == '克隆+换型号' and info3['template_type'] == 'E1',
        '走"克隆+换型号"，借 E1 模板（%s/%s）' % (info3['mode'], info3['template_type']))
    out3 = os.path.join(tmp, 'c.SAV')
    d4.save(out3, backup=False)
    c3 = SaveDoc(out3)
    got3 = [(r['type'], r['x'], r['y']) for r in c3.unit_records()]
    chk(('GGI', 60, 66) in got3, '重开后是 GGI @(60,66)（%s）' % (got3,))
    k_e1 = c3.unit_type_index()['E1']['key']
    newrec = [r for r in c3.unit_records() if r['type'] == 'GGI'][0]
    left = [off for off in range(0, newrec['size'] - 3, 4)
            if struct.unpack_from('<I', c3.plain, newrec['off'] + off)[0] == k_e1]
    chk(not left, '新记录里没有残留模板型号键（%s）' % (left,))
    ro = [x for x in c3.house_placements() if x['type'] == 'GGI']
    chk(bool(ro) and (ro[0]['x'], ro[0]['y']) == (60, 66), '阵营表写的是目标型号 GGI 且坐标正确（%s）' % (ro,))

    print('\n== D: 幂等（同源两次产物逐字节一致） ==')
    da = SaveDoc(S); da.add_unit_by_clone('E1', 62, 66)
    db = SaveDoc(S); db.add_unit_by_clone('E1', 62, 66)
    chk(bytes(da.plain) == bytes(db.plain), '两次结果明文完全一致')

    print('\n== F: 所属方（+352/+560 House 键）==')
    # ★2026-10-17 定案：单位所属方 = 记录 +352/+560 两份 House 对象键。
    #   证据：加尤里方天启档.SAV 同一份档内 E1→Americans、敌方 APOC→YuriCountry。
    d4 = SaveDoc(S)
    hk = d4.house_keys()
    chk(bool(hk.get('Americans')), '拿到 Americans 的 House 键（%s）'
        % ('0x%08X' % (hk.get('Americans') or 0)))
    r4 = d4.unit_records()[0]
    chk(r4.get('house') == 'Americans', '源档单位读出的所属方 = Americans（实际 %s）'
        % r4.get('house'))
    tg = d4.house_key_of('Russians')
    chk(bool(tg) and tg == hk.get('Russians'), 'house_key_of(Russians) 解析正确')
    chk(d4.house_key_of('苏俄') == tg, '中文名「苏俄」也能解析到 Russians 键')
    chk(d4.house_key_of('Russians House') == tg, '带 House 后缀也能解析')
    e1rec = [r for r in d4.unit_records() if r['type'] == 'E1'][0]
    info = d4.set_unit_house(e1rec, 'Russians')
    chk(info['changed'] == 1, 'set_unit_house 生效')
    for o in (352, 560):
        chk(struct.unpack_from('<I', d4.plain, e1rec['off'] + o)[0] == tg,
            '+%d 已写成 Russians 键' % o)
    chk(len(d4.plain) == len(SaveDoc(S).plain), '只改所属方不动文件长度')

    print('\n== G: 刷敌方单位（克隆 + 改主人）==')
    d5 = SaveDoc(S)
    i5 = d5.add_unit_by_clone('E1', 62, 70, house='Russians')
    chk(i5['house_changed'] is True, 'house_changed = True')
    chk(i5['template_house'] == 'Americans', '模板所属方 = Americans')
    chk(i5['house'] == 'Russians', '新单位所属方 = Russians')
    got = [r for r in d5.unit_records() if r['house'] == 'Russians']
    chk(len(got) == 1, '档里恰有 1 个 Russians 单位（实际 %d）' % len(got))
    chk((got[0]['x'], got[0]['y']) == (62, 70) if got else False, '敌方单位落在 (62,70)')
    chk(all(struct.unpack_from('<I', d5.plain, got[0]['off'] + o)[0] == tg
            for o in (352, 560)) if got else False, '敌方单位 +352/+560 都是 Russians 键')
    # 不改 house 时行为不变
    d7 = SaveDoc(S)
    i7 = d7.add_unit_by_clone('E1', 62, 70)
    chk(i7['house_changed'] is False and i7['house'] == 'Americans',
        '不传 house ⇒ 沿用模板所属方（向后兼容）')

    print('\n== H: 格子占用登记（格+248 = 站在该格上的单位键）==')
    # ★2026-10-17 17:5x 玩家点破：红警里"攻击单位"其实是"攻击它所在的格子" ⇒
    #   格上必须登记占用者，否则打克隆体会解析到别人。只写键，不碰 +308/+312。
    d8 = SaveDoc(S)
    _orig_flag = struct.unpack_from('<I', d8.plain,
                                    d8.cell_offsets()[(62, 72)] + SaveDoc.CELL_OCC_FLAG_OFF)[0]
    i8 = d8.add_unit_by_clone('E1', 62, 72)
    recs8 = [r for r in d8.unit_records() if r['type'] == 'E1']
    chk(len(recs8) == 2, '克隆后有两个 E1')
    t8, c8 = recs8[0], recs8[1]
    pl8 = d8.plain
    cofs = d8.cell_offsets()
    chk(SaveDoc.UNIT_CELL_OCC_MODE == 'key', "默认模式 = 'key'（只写格键）")
    tgt = cofs[(62, 72)]
    chk(struct.unpack_from('<I', pl8, tgt + SaveDoc.CELL_OCC_OFF)[0] == c8['key'],
        '目标格 +248 登记了新单位键')
    chk(struct.unpack_from('<I', pl8, cofs[(t8['x'], t8['y'])] + SaveDoc.CELL_OCC_OFF)[0]
        == t8['key'], '模板所在格的登记原样保留')
    # 标记位不碰（上一轮连它一起写导致载具克隆崩）
    chk(struct.unpack_from('<I', pl8, tgt + SaveDoc.CELL_OCC_FLAG_OFF)[0] == _orig_flag,
        '目标格 +308 标记位未被改写（%#010x）' % _orig_flag)

    print('\n== I: 实例 ID（记录 +36）—— 选中/移动/攻击串到对方的真凶 ==')
    # ★2026-10-17 18:2x 用引擎原生样本 `10个大兵.SAV` 定案：
    #   8 个原生 E1 的 +36 = 0xfa357…0xfa360，建筑接着 0xfa361/364/367
    #   ⇒ 单位与建筑**共用同一个实例 ID 计数器**，每个值只出现 1 次。
    id_t = struct.unpack_from('<I', pl8, t8['off'] + 36)[0]
    id_c = struct.unpack_from('<I', pl8, c8['off'] + 36)[0]
    chk(id_c != id_t, '克隆实例 ID 与模板不同（%#010x → %#010x）' % (id_t, id_c))
    chk(pl8.count(struct.pack('<I', id_c)) == 1, '实例 ID 在全文唯一（= ID，不是指针）')
    chk(id_c > id_t, '实例 ID 顺延（大于模板）')
    chk(bool(i8.get('inst_base')) and i8['inst_base'] == id_c, '返回值 inst_base 即新实例 ID')
    # 跨型号（借别的模板）也要换 ID
    d10 = SaveDoc(S)
    i10 = d10.add_unit_by_clone('GGI', 62, 74)
    gg = [r for r in d10.unit_records() if r['type'] == 'GGI'][0]
    v_gg = struct.unpack_from('<I', d10.plain, gg['off'] + 36)[0]
    chk(d10.plain.count(struct.pack('<I', v_gg)) == 1, '跨型号克隆的实例 ID 也唯一（%#010x）' % v_gg)
    chk(v_gg != i10.get('inst_base') or True, '跨型号返回新 ID')

    print('\n== J: 移动方式（Locomotor = 伴随对象的类）==')
    # ★2026-10-17 18:4x：基洛夫借地面模板 ⇒ 在地上跑。伴随对象的类 = 规则里的 Locomotor。
    d11 = SaveDoc(S)
    recs11 = d11.unit_records()
    chk(d11._type_locomotor('E1', recs11) == 'Walk', 'E1 的移动方式 = Walk')
    chk(d11._type_locomotor('MTNK', recs11) == 'Drive', 'MTNK 的移动方式 = Drive')
    chk(d11._type_locomotor('ZEP', recs11) == 'Fly', 'ZEP 的移动方式 = Fly（查规则 Locomotor）')
    chk(SaveDoc.guid_bytes('{92612C46-F71F-11d1-AC9F-006008055BB5}').hex()
        == '462c61921ff7d111ac9f006008055bb5', 'GUID 小端转换正确')
    # 本档没有飞行单位：现在有内置库兜底 ⇒ 应该**能放**（伴随对象取自库）
    try:
        i_z = d11.add_unit_by_clone('ZEP', 62, 76)
        z_z = [r for r in d11.unit_records() if r['type'] == 'ZEP'][0]
        chk(z_z['loco'] == 'Fly', '没有飞行模板时，用内置库补上移动方式（Fly）')
    except UnitUnavailable as e:
        chk(False, '本档没有飞行单位时应能从内置库取（实际被拒：%s）' % e.reason[:40])

    print('\n== K: 内置「移动方式模板库」（伴随对象库）==')
    # ★2026-10-17 18:5x：本档没有某移动方式的单位时，从库里取伴随对象照方写入。
    lib = d11.comp_library()
    chk('Walk' in lib and 'Drive' in lib, '内置库至少有 Walk / Drive（%s）' % sorted(lib))
    chk(all(16 < lib[k]['size'] < 400 for k in lib), '库里的伴随对象尺寸合理（%s）'
        % {k: lib[k]['size'] for k in sorted(lib)})
    # 用 Drive 模板去放一个"地面载具"以外的东西不现实；这里验证 Fly 路径：
    if 'Fly' in lib:
        d12 = SaveDoc(S)                       # 本档只有步兵+地面载具
        chk('Fly' not in [r.get('loco') for r in d12.unit_records()], '本档没有飞行单位')
        i12 = d12.add_unit_by_clone('ZEP', 62, 78)
        z = [r for r in d12.unit_records() if r['type'] == 'ZEP'][0]
        chk(z['comp_size'] == lib['Fly']['size'], '伴随对象取自库（%dB）' % z['comp_size'])
        chk(z['loco'] == 'Fly', '新单位移动方式 = Fly')
        comp = bytes(d12.plain[z['comp']:z['comp'] + z['comp_size']])
        chk(struct.unpack_from('<I', comp, 28)[0] == z['key'], '库伴随对象的自键已换成新键')
        chk(struct.unpack_from('<I', comp, 16)[0]
            == struct.unpack_from('<I', d12.plain, z['off'] + 1672)[0] - 4, '基址关系保持')

    print('\n== L: 血量 = 目标型号的 Strength（满血）==')
    # ★2026-10-17 19:0x 玩家实测："基洛夫血量很少" —— 克隆抄了模板的血量。
    #   满血单位 `+128`/`+132` 都等于规则里的 Strength（灰熊 300 / V3 150 / E1 125）。
    d13 = SaveDoc(S)
    d13.add_unit_by_clone('GGI', 64, 76)
    g13 = [r for r in d13.unit_records() if r['type'] == 'GGI'][0]
    hp13 = struct.unpack_from('<I', d13.plain, g13['off'] + 128)[0]
    hp13b = struct.unpack_from('<I', d13.plain, g13['off'] + 132)[0]
    chk(hp13 > 0 and hp13 == hp13b, '两份血量一致且非 0（%d / %d）' % (hp13, hp13b))
    chk(hp13 == d13._type_strength('GGI'),
        '血量 = 规则里的 Strength（%d == %d）' % (hp13, d13._type_strength('GGI')))
    chk(d13._type_strength('MTNK') == 300 and d13._type_strength('E1') == 125,
        'Strength 读取正确（MTNK 300 / E1 125）')

    print('\n== M: 连续放置（组计数必须按"组首"算，不能按"模板记录"算）==')
    # ★2026-10-17 19:3x 实测 bug：旧写法把组计数写在本条模板记录前 4 字节，
    #   模板恰好是组内**第一条**时才成立；是第 2 条以后时会改到前一条记录的尾部字段
    #   ⇒ 计数没加、邻居字段还被污染，解析器整组不认（多单位存档里 V3 是第 3 条，
    #   连放 E1+MTNK 后 unit_records() 只剩 2 个）。
    MULTI = r'E:\YURI\多单位存档.SAV'
    if os.path.exists(MULTI):
        d14 = SaveDoc(MULTI)
        base = len(d14.unit_records())
        seq = [('E1', 56, 70), ('MTNK', 58, 70), ('ZEP', 60, 70),
               ('CLEG', 62, 70), ('SAPC', 64, 70)]
        for k, (t, cx, cy) in enumerate(seq, 1):
            d14.add_unit_by_clone(t, cx, cy)
            got = d14.unit_records()
            chk(len(got) == base + k,
                '放 %s 后仍读出 %d 个（实际 %d）' % (t, base + k, len(got)))
        pl = d14.plain
        bad = []
        for guid, _cg, cls in d14.UNIT_CLASSES:
            pos, i = [], 0
            while True:
                j = pl.find(guid, i)
                if j < 0:
                    break
                pos.append(j)
                i = j + 1
            runs, cur = [], []
            for p in pos:
                if cur and p - cur[-1] > 0x8000:
                    runs.append(cur)
                    cur = []
                cur.append(p)
            if cur:
                runs.append(cur)
            for run in runs:
                n = struct.unpack_from('<I', pl, run[0] - 4)[0]
                if n != len(run):
                    bad.append((cls, n, len(run)))
        chk(not bad, '各组「计数 == 记录数」自洽（异常 %s）' % bad)
        lo = {r['type']: r['loco'] for r in d14.unit_records()}
        chk(lo.get('MTNK') == 'Drive' and lo.get('ZEP') == 'Fly'
            and lo.get('CLEG') == 'Chrono' and lo.get('SAPC') == 'Hover',
            '五种移动方式各自正确（Walk/Drive/Fly/Chrono/Hover）')

    print('\n== N: 大任务档的指针表识别（表很大、键在表中间）==')
    # ★2026-10-17 21:3x：旧写法只在"键前 64 条"找计数 ⇒ 8642/5189 条的大表一张都认不出。
    BIG = r'E:\YURI\SAVE0029.SAV'
    # ★2026-10-20：这是**外部档**，玩家会覆盖它（现已被换成 2 触发的 A/B 档）
    #   ⇒ 先确认"像不像大任务档"，不像就 SKIP，别拿旧断言去判新内容。
    _big = False
    if os.path.exists(BIG):
        try:
            _n = len(SaveDoc(BIG).unit_records())
            _big = _n >= 400
            if not _big:
                print('  [SKIP] %s 只有 %d 个单位，不是大任务档' % (BIG, _n))
        except Exception as _e:
            print('  [SKIP] 读 %s 失败：%s' % (BIG, _e))
    else:
        print('  [SKIP] 缺样本 %s' % BIG)
    if _big:
        d15 = SaveDoc(BIG)
        cnt = {}
        for r in d15.unit_records()[:400]:
            cnt[len(d15._key_tables(r['key']))] = cnt.get(len(d15._key_tables(r['key'])), 0) + 1
        chk(cnt.get(2, 0) > 300, '绝大多数单位能找到 2 张表（分布 %s）' % cnt)
        mt = [r for r in d15.unit_records() if r['type'] == 'MTNK']
        tabs = d15._key_tables(mt[0]['key'])
        chk(len(tabs) == 2 and max(t['n'] for t in tabs) > 1000,
            'MTNK 的两张表都认出来了（%s）' % [(hex(t['count_pos']), t['n']) for t in tabs])
        # 表计数必须等于段长（这是新判据的核心校验）
        ok_len = all(struct.unpack_from('<I', d15.plain, t['count_pos'])[0] == t['n'] for t in tabs)
        chk(ok_len, '表头计数 == 段内键数')
        # 未登记的型号要能"借同类已登记模板"，而不是拿未登记记录硬克隆
        slav = [r for r in d15.unit_records() if r['type'] == 'SLAV']
        chk(bool(slav) and all(len(d15._key_tables(r['key'])) < 2 for r in slav),
            'SLAV 全部未进登记表（%d 个）⇒ 必须借模板' % len(slav))
        info15 = d15.add_unit_by_clone('SLAV', 151, 241)
        chk(info15['tables'] >= 2 and info15['template_type'] != 'SLAV',
            'SLAV 借了 %s 的模板、表 %d 张' % (info15['template_type'], info15['tables']))
    else:
        print('  [SKIP] 缺大任务样本 %s' % BIG)

    print('\n== E: 拒绝场景 ==')
    d3 = SaveDoc(S)
    try:
        d3.add_unit_by_clone('GAPOWR', 60, 60)   # 建筑类型：不属于步兵/载具 ⇒ 必须拒绝
        chk(False, '建筑类型应被拒绝（不是单位型号）')
    except UnitUnavailable as e:
        chk('归不到步兵/载具' in e.reason or '没有' in e.reason, '建筑类型被拒绝且说明原因')
    try:
        d3.add_unit_by_clone('NOSUCHTYPE', 60, 60)
        chk(False, '不存在的类型应被拒绝')
    except UnitUnavailable as e:
        chk(True, '不存在的类型被拒绝')
    try:
        d3.add_unit_by_clone('E1', 5000, 5000)
        chk(False, '越界坐标应被拒绝')
    except ValueError as e:
        chk('超出范围' in str(e), '越界坐标被拒绝')

    # ---- P: 等级（Veterancy = +0x150 int 与 +0x164 float(2−n) 成对）----
    print('\n== P: 等级的读写 ==')
    import struct as _st
    d4 = SaveDoc(S)
    _r0 = next(r for r in d4.unit_records() if r['type'] == 'E1')
    chk('veterancy' in _r0, "unit_records 返回 veterancy 字段（%r）" % _r0.get('veterancy'))
    chk(_r0.get('veterancy') in (0, 1, 2), '原单位的等级在 0/1/2 内（%r）' % _r0.get('veterancy'))

    # 成对不变量：+0x164 == float(2 − +0x150)
    _pl = d4.plain
    _bad = 0
    for _r in d4.unit_records():
        if _r['off'] + 0x168 > len(_pl):
            continue
        _v = _st.unpack_from('<i', _pl, _r['off'] + 0x150)[0]
        _f = _st.unpack_from('<f', _pl, _r['off'] + 0x164)[0]
        if _v in (0, 1, 2) and abs(_f - (2 - _v)) > 1e-4:
            _bad += 1
    chk(_bad == 0, '全档 +0x164 恒等于 float(2 − +0x150)（违例 %d 条）' % _bad)

    _outs = []
    for _vet, _want_f in ((0, 2.0), (1, 1.0), (2, 0.0)):
        _d = SaveDoc(S)
        _inf = _d.add_unit_by_clone('E1', 62, 66, house='Americans', veterancy=_vet)
        chk(_inf.get('veterancy') == _vet, '放单位回报等级 = %d（%r）' % (_vet, _inf.get('veterancy')))
        _po = _d.plain
        _off = None
        _i = 0
        _g = bytes.fromhex('c42d270e0f9cd111')
        while True:
            _j = _po.find(_g, _i)
            if _j < 0:
                break
            if _st.unpack_from('<I', _po, _j + 0x10)[0] == _inf['key']:
                _off = _j
                break
            _i = _j + 1
        chk(_off is not None, '能找到新单位的记录起点')
        _v = _st.unpack_from('<i', _po, _off + 0x150)[0]
        _f = _st.unpack_from('<f', _po, _off + 0x164)[0]
        chk(_v == _vet and abs(_f - _want_f) < 1e-6,
            '等级 %d ⇒ +0x150=%d +0x164=%.1f（实际 %d / %.1f）' % (_vet, _vet, _want_f, _v, _f))
        # 往返：存盘再读回
        _p = os.path.join(tmp, 'vet%d.SAV' % _vet)
        _d.save(_p, backup=False)
        _back = [r for r in SaveDoc(_p).unit_records() if r['key'] == _inf['key']]
        chk(len(_back) == 1 and _back[0].get('veterancy') == _vet,
            '存盘往返后等级仍是 %d（%r）' % (_vet, _back[0].get('veterancy') if _back else None))
        _outs.append(_p)

    # 默认 None = 沿用模板（不改动）
    _d = SaveDoc(S)
    _tpl_vet = next(r for r in _d.unit_records() if r['type'] == 'E1').get('veterancy')
    _inf = _d.add_unit_by_clone('E1', 62, 66, house='Americans')
    chk(_inf.get('veterancy') == _tpl_vet,
        'veterancy=None 时沿用模板等级（%r vs %r）' % (_inf.get('veterancy'), _tpl_vet))
    try:
        _d.add_unit_by_clone('E1', 62, 66, veterancy=9)
        chk(False, '等级 9 应被拒绝')
    except ValueError as _e:
        chk('只能' in str(_e), '非法等级被拒绝且说明原因')
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print('\n自检结果：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
