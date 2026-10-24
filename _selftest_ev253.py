"""§253 自检：单独新建「特遣部队 / 动作脚本 / 作战小队」+ 把小队挂到已有触发上。

对应玩家需求（2026-10-24 22:1x）：
> 现在就做 新建特遣部队 动作脚本 作战小队 触发可以挂载作战小队的功能

昨天已验证的是"刷兵三件套一把做完"（§217/§218，T93/T94 验收）；
本节补的是**拆开单独用** + **触发挂小队**，顺带修掉 `clone_team` 写错星级偏移的老 bug
（`+0xF0` → `+0xB4`，§252.17 用地编对话框对账确认）。

用法：D:/py/python.exe _selftest_ev253.py
"""
import sys, os, struct

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'core'))
import editor_core as ec
import ai_objects as aio

BASE = 'E:/YURI/新建文件夹 (30)/T85_老工程师_修位置副本槽.SAV'
OUT = os.path.join(HERE, '_tmp_ev253.SAV')
P = F = 0


def ck(name, cond, info=''):
    global P, F
    if cond:
        P += 1
        print('  [PASS] %s  %s' % (name, info))
    else:
        F += 1
        print('  [FAIL] %s  %s' % (name, info))


def u32(buf, off):
    return struct.unpack_from('<I', buf, off)[0]


def main():
    d = ec.SaveDoc(BASE)
    n_tf0 = len(d.taskforces())
    n_tm0 = len(d.team_types())
    n_sc0 = len(d.script_types())

    print('\n=== [A] 新建三件套（特遣 + 脚本 + 小队），星级=3、脚本=警戒 10 ===')
    r = d.create_ai_team([('DISK', 2)], name='Selftest队', veteran=3,
                         script_entries=[(5, 10)])
    tf, sc, tm = r['tf'], r['script'], r['team']
    print('   tf=%s(%s)  script=%s  team=%s(%s)'
          % (tf['sid'], tf['name'], sc['sid'], tm['sid'], tm['name']))
    ck('A1. 特遣部队 +1', len(d.taskforces()) == n_tf0 + 1,
       '%d → %d' % (n_tf0, len(d.taskforces())))
    ck('A2. 动作脚本 +1', len(d.script_types()) == n_sc0 + 1,
       '%d → %d' % (n_sc0, len(d.script_types())))
    ck('A3. 作战小队 +1', len(d.team_types()) == n_tm0 + 1,
       '%d → %d' % (n_tm0, len(d.team_types())))

    tfs = {x['sid']: x for x in d.taskforces()}
    ck('A4. 特遣里就是 DISK × 2',
       any(u['type'] == 'DISK' and u['count'] == 2 for u in tfs[tf['sid']]['units']),
       str(tfs[tf['sid']]['units']))

    scs = {x['sid']: x for x in d.script_types()}
    ck('A5. 脚本内容 = [(5,10)]（警戒 10）',
       [(e['action'], e['arg']) for e in scs[sc['sid']]['entries']] == [(5, 10)],
       str([(e['action'], e['arg']) for e in scs[sc['sid']]['entries']]))

    teams = {x['sid']: x for x in d.team_types()}
    t = teams[tm['sid']]
    ck('A6. ★小队星级写进 `+0xB4` = 3（旧代码错写 +0xF0，永远读不到）',
       struct.unpack_from('<I', d.plain, t['off'] + 0xB4)[0] == 3,
       '实际 %d' % struct.unpack_from('<I', d.plain, t['off'] + 0xB4)[0])
    ck('A7. read_ai 也能读到 veteran=3', t['veteran'] == 3, str(t['veteran']))
    ck('A8. 小队引用的特遣 = 新建那个', t['tf_key'] == tf['key'],
       '%#x vs %#x' % (t['tf_key'] or 0, tf['key']))
    ck('A9. 小队引用的脚本 = 新建那个', t['script_key'] == sc['key'],
       '%#x vs %#x' % (t['script_key'] or 0, sc['key']))
    ck('A10. 小队国家 = 玩家方',
       t['country_key'] == d.house_key_of(d.player_house()),
       '%#x' % (t['country_key'] or 0))
    ck('A11. 「出现于」`+0xE8` 置 0（与地编口径一致）',
       u32(d.plain, t['off'] + 0xE8) == 0, str(u32(d.plain, t['off'] + 0xE8)))

    print('\n=== [B] 触发挂载作战小队（行为 80）===')
    info = d.create_trigger('Selftest挂小队', 13, 1, 5, 0)
    tid = info['trigger_id']
    a = d.attach_team_to_trigger(tid, tm['sid'], cell=(110, 206))
    print('   attach → %s' % a)
    acts = d.trigger_actions(tid)
    sp = [x for x in acts if int(x.get('type') or 0) in d.SPAWN_ACTIONS]
    ck('B1. 触发上出现了 80 号行为', bool(sp), str([x.get('type') for x in acts]))
    if sp:
        off = sp[0]['action_off']
        ck('B2. 行为里的小队键 `+0x44` = 新小队', u32(d.plain, off + 0x44) == tm['key'],
           '%#x vs %#x' % (u32(d.plain, off + 0x44), tm['key']))
        ck('B3. 路径点 `+0x58` 非 0', u32(d.plain, off + 0x58) != 0,
           str(u32(d.plain, off + 0x58)))
        ck('B4. `+0x48` 恒 0（这一槽不是小队键）', u32(d.plain, off + 0x48) == 0,
           str(u32(d.plain, off + 0x48)))

    print('\n=== [C] 再挂一次：应该"改写同一条"而不是多一条 ===')
    n_act0 = len([x for x in d.trigger_actions(tid)])
    r2 = d.create_ai_team([('DISK', 1)], name='Selftest队2', veteran=1)
    a2 = d.attach_team_to_trigger(tid, r2['team']['sid'], waypoint=a['waypoint'])
    ck('C1. 复用了原行为（mode=reuse）', a2.get('mode') == 'reuse', str(a2.get('mode')))
    ck('C2. 触发行为条数没变', len(d.trigger_actions(tid)) == n_act0, '')
    off = a2['action_off']
    ck('C3. 小队键已换成第二支', u32(d.plain, off + 0x44) == r2['team']['key'],
       '%#x' % u32(d.plain, off + 0x44))

    print('\n=== [D] 落盘重读：计数与引用不变 ===')
    d.save(OUT)
    e = ec.SaveDoc(OUT)
    ck('D1. 两支新小队都在', len(e.team_types()) == n_tm0 + 2,
       '%d' % len(e.team_types()))
    te = {x['sid']: x for x in e.team_types()}
    ck('D2. 重读后星级仍是 3', te[tm['sid']]['veteran'] == 3, str(te[tm['sid']]['veteran']))
    ck('D3. 重读后仍引用新脚本', te[tm['sid']]['script_key'] == sc['key'], '')
    acts2 = e.trigger_actions(tid)
    sp2 = [x for x in acts2 if int(x.get('type') or 0) in e.SPAWN_ACTIONS]
    ck('D4. 触发的小队引用重读后正确',
       bool(sp2) and u32(e.plain, sp2[0]['action_off'] + 0x44) == r2['team']['key'],
       str([x.get('type') for x in acts2]))

    try:
        os.remove(OUT)
    except OSError:
        pass
    print('-' * 72)
    print('§253 自检：PASS %d / FAIL %d' % (P, F))
    return 0 if F == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
