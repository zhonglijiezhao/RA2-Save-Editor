# -*- coding: utf-8 -*-
"""自检 ev390：行为 106「设置科技等级」的两个参数

2026-11-17 定案：
    科技类型 = 对象 `+104` 的 **8 字节 ASCII**（`\\0` 补齐），与行为 125 的「建筑类型名」同一个槽
    等级     = 对象 `+164`（就是工具里那个「参数」），`+72` 恒 0

素材：`E:\\YURI\\SAVE0BB3.SAV` / `ALL01UMD.sav` 里的触发 01000000（地编：AEGIS、等级 5）。
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, '_pylibs'))
from core.editor_core import SaveDoc    # noqa: E402

CANDS = [r'E:\YURI\SAVE0BB3.SAV', r'E:\YURI\ALL01UMD.sav']
SRC = next((p for p in CANDS if os.path.exists(p)), None)
if SRC is None:
    print('SKIP：缺素材档 %s' % CANDS)
    sys.exit(0)

ok = fail = 0


def chk(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print('  x %s' % msg)


d = SaveDoc(SRC)
print('素材：%s' % SRC)

print('\n== 1) 读出地编里那两个值 ==')
rows = d.trigger_actions('01000000')
a106 = [x for x in rows if x['type'] == 106]
chk(len(a106) == 1, '触发 01000000 有 1 条 106（实际 %d 条行为、%d 条 106）'
    % (len(rows), len(a106)))
if not a106:
    print('\n结果：绿 %d / 红 %d' % (ok, fail))
    sys.exit(1)
x = a106[0]
off = x['action_off']
print('   type=%d  tech_name=%r  arg(等级)=%d' % (x['type'], x.get('tech_name'), x['arg']))
chk(x.get('tech_name') == 'AEGIS', '科技类型 = AEGIS')
chk(x['arg'] == 5, '等级 = 5')

print('\n== 2) 字节落点核对 ==')
raw = bytes(d.plain[off + 104:off + 112])
chk(raw == b'AEGIS\x00\x00\x00', '+104..+111 = "AEGIS\\0\\0\\0"（实际 %r）' % raw)
chk(struct.unpack_from('<I', d.plain, off + 72)[0] == 0, '+72 = 0（参数不在那儿）')
chk(struct.unpack_from('<I', d.plain, off + 164)[0] == 5, '+164 = 5（等级）')

print('\n== 3) 写 + 回读 ==')
d.set_action(x, atype=106, arg=8, tech_name='GAPILE')
y = d.trigger_actions('01000000')[0]
print('   写 GAPILE/8 → tech_name=%r arg=%d' % (y.get('tech_name'), y['arg']))
chk(y.get('tech_name') == 'GAPILE', '科技类型写成 GAPILE')
chk(y['arg'] == 8, '等级写成 8')
chk(bytes(d.plain[off + 104:off + 112]) == b'GAPILE\x00\x00',
    '+104 字节 = "GAPILE\\0\\0"')
chk(struct.unpack_from('<I', d.plain, off + 72)[0] == 0, '写完后 +72 仍然是 0（没写错位置）')

print('\n== 4) 等级的边界：0–10 可建造，-1 / >10 = 不可建造 ==')
# ★玩家 2026-11-17 口径：科技等级一般 0–10；**10 以上或 -1 表示不可建造**
for lv, want_txt in ((0, 'AEGIS 等级 0'), (10, 'AEGIS 等级 10'),
                     (-1, 'AEGIS 等级 -1（不可建造）'),
                     (12, 'AEGIS 等级 12（不可建造）')):
    d.set_action(x, atype=106, arg=lv, tech_name='AEGIS')
    r = d.trigger_actions('01000000')[0]
    det = d.trigger_detail('01000000')['actions'][0]
    chk(r['arg'] == lv, '等级 %d 写入后读回 %d（无符号翻转或截断）' % (lv, r['arg']))
    chk(det.get('arg_text') == want_txt,
        '等级 %d 的说明是 %r（实际 %r）' % (lv, want_txt, det.get('arg_text')))
    # -1 在存档里是 0xFFFFFFFF
    raw164 = struct.unpack_from('<I', d.plain, off + 164)[0]
    chk(raw164 == (lv & 0xFFFFFFFF), '等级 %d 的 +164 原始值 = %#x' % (lv, raw164))
d.set_action(x, atype=106, arg=5, tech_name='AEGIS')

print('\n== 5) 还原 + 大小写 / 截断 ==')
d.set_action(x, atype=106, arg=5, tech_name='aegis')      # 小写应自动转大写
z = d.trigger_actions('01000000')[0]
chk(z.get('tech_name') == 'AEGIS', '小写 aegis 写入后读回 AEGIS（实际 %r）' % z.get('tech_name'))
d.set_action(x, atype=106, arg=5, tech_name='ABCDEFGHIJ')
w = d.trigger_actions('01000000')[0]
chk(w.get('tech_name') == 'ABCDEFGH', '超过 8 字符按 8 字节截断（实际 %r）'
    % w.get('tech_name'))
d.set_action(x, atype=106, arg=5, tech_name='AEGIS')      # 还原
print('\n== 6) 只有 106 才带 tech_name ==')
chk(all((r.get('tech_name') == '') for r in d.trigger_actions('01000000')
        if r['type'] != 106), '其它行为类型 tech_name 为空')

print('\n结果：绿 %d / 红 %d' % (ok, fail))
sys.exit(0 if fail == 0 else 1)
