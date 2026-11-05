# -*- coding: utf-8 -*-
"""自检：行为 70「摧毁标签」的参数 = 标签对象的**实例键**，存于行为对象 **+96**

玩家 2026-11-05 给了两份对照档（`E:\\YURI\\摧毁标签前.SAV` / `摧毁标签后.SAV`）并说明：
  「70 摧毁标记 应该为摧毁标签」「地编的做法就是 列表就是标签的列表」

§SOV31 结论（本自检固化）：
  · 地编 ptype=38 只有 `70 摧毁标签`，参数下拉 = 本档标签（Tag）列表；
  · 存档里**不**存在 +72/+164（两档实测都恒 0），而是写在行为对象 **+96**，
    值是**标签对象的实例键**（标签对象 `+16` 的自键）；
  · 证据：前档 01000007 的行为 70 `+96 = 0D70B9B0` = 标签 `01000006`「aaaa 1」的自键；
    同一个值在文件里只出现 3 次（标签自身 +16、CellTag +56、行为 70 +96）；
  · 后档里该标签的 CellTag 节点消失 ⇒ 游戏内确实执行了（玩家实测）。

断言：
  A 标签表：本档 2 个标签，键/ID/名对得上（01000006↔0D70B9B0、01000008↔0D70B900）
  B 读：01000007 的行为 70 → tag_key=0D70B9B0、tag=01000006、文案「01000006 aaaa 1」
  C 位置：+72 / +164 必须仍是 0（参数不在那两处）
  D 写：set_action(tag='01000008') → +96 变成 0D70B900，+72/+164 仍 0
  E 落盘：另存后回读仍是 01000008（不靠内存里的 pending）
  F 写错名字要报错（认不出的标签 ID 不许静默写 0）
"""
import io
import os
import shutil
import struct
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'core'))

from editor_core import SaveDoc            # noqa: E402

OK = FAIL = 0


def chk(cond, msg, got=None):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [OK] %s' % msg)
    else:
        FAIL += 1
        print('  [FAIL] %s   实际=%r' % (msg, got))


SAV = r'E:\YURI\摧毁标签前.SAV'
TMP = tempfile.mkdtemp(prefix='_t70_')
try:
    print('== A. 标签（Tag）表 ==')
    if not os.path.isfile(SAV):
        print('     （没有 %s，跳过真档部分）' % SAV)
    else:
        d = SaveDoc(SAV)
        tags = {t['sid']: t for t in d.tag_objects()}
        chk(len(tags) == 2, '本档有 2 个标签', sorted(tags))
        chk(tags.get('01000006', {}).get('key') == 0x0D70B9B0,
            '01000006 的自键 = 0D70B9B0', tags.get('01000006'))
        chk(tags.get('01000008', {}).get('key') == 0x0D70B900,
            '01000008 的自键 = 0D70B900', tags.get('01000008'))
        chk(d.tag_key_of('01000006') == 0x0D70B9B0, 'tag_key_of(01000006) 能用')
        chk(d.tag_label(0x0D70B900) == '01000008 新触发1 1',
            'tag_label(键) → 「01000008 新触发1 1」', d.tag_label(0x0D70B900))

        print('== B. 读：01000007 的 70 指向 01000006 ==')
        rows = d.trigger_actions('01000007')
        a = rows[0]
        chk(a['type'] == 70, '行为类型 = 70', a['type'])
        chk(a['tag_key'] == 0x0D70B9B0, 'tag_key = 0D70B9B0', hex(a['tag_key']))
        chk(a['tag'] == '01000006', 'tag = 01000006', a['tag'])
        chk(d._arg_text(38, a['tag_key']) == '01000006 aaaa 1',
            '文案 = 「01000006 aaaa 1」', d._arg_text(38, a['tag_key']))

        print('== C. 参数不在 +72 / +164 ==')
        o = a['action_off']
        chk(struct.unpack_from('<I', d.plain, o + 72)[0] == 0, '+72 = 0')
        chk(struct.unpack_from('<I', d.plain, o + 164)[0] == 0, '+164 = 0')
        chk(struct.unpack_from('<I', d.plain, o + 96)[0] == 0x0D70B9B0, '+96 = 标签键')

        print('== D. 写：改成 01000008 ==')
        p = os.path.join(TMP, 'in.SAV')
        shutil.copy(SAV, p)
        d = SaveDoc(p)
        a = d.trigger_actions('01000007')[0]
        d.set_action(a, tag='01000008')
        b = d.trigger_actions('01000007')[0]
        chk(b['tag_key'] == 0x0D70B900, '改后 tag_key = 0D70B900', hex(b['tag_key']))
        chk(b['tag'] == '01000008', '改后 tag = 01000008', b['tag'])
        chk(struct.unpack_from('<I', d.plain, b['action_off'] + 72)[0] == 0,
            '改后 +72 仍是 0')
        chk(struct.unpack_from('<I', d.plain, b['action_off'] + 164)[0] == 0,
            '改后 +164 仍是 0')

        print('== E. 落盘回读 ==')
        out = os.path.join(TMP, 'out.SAV')
        d.save(out)
        d2 = SaveDoc(out)
        c = d2.trigger_actions('01000007')[0]
        chk(c['tag'] == '01000008', '回读 tag = 01000008', c['tag'])
        chk(struct.unpack_from('<I', d2.plain, c['action_off'] + 96)[0] == 0x0D70B900,
            '回读 +96 = 0D70B900')

        print('== F. 认不出的标签要报错 ==')
        try:
            d2.set_action(c, tag='DEADBEEF')
            chk(False, '不存在的标签 ID 必须报错')
        except Exception as e:
            chk('认不出' in str(e), '不存在的标签 ID 报错：%s' % str(e)[:40], e)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print('\n行为 70 标签自检：PASS %d / FAIL %d' % (OK, FAIL))
sys.exit(1 if FAIL else 0)
