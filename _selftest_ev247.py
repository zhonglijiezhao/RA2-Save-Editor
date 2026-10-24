# -*- coding: utf-8 -*-
"""§247 自检：小队「关联标签」+ 克隆绝不继承单位标签挂接（2026-10-24）。

两件事：
  A. 小队记录 `+0x0E4` = 关联标签（FA2「作战小队」里的「关联标签」），存**标签对象键**。
     实证：T85 小队 01000241「a飞碟转圈A」→ 标签 01000251，与玩家 FA2 截图逐字一致。
  B. 单位记录 `+0x48` = 标签挂接字段（存 CellTag 运行期指针 X）。
     克隆/导入**必须清 0** —— 否则新单位会挂到骨架所属的触发上，
     玩家实测 T100：克隆飞碟一被打 → 触发「飞碟被攻击寻敌」放全家 → 打掉就弹框。
"""
import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))

from _paths import find_sav, out          # noqa: E402
import editor_core as ec                  # noqa: E402
import ai_objects                         # noqa: E402

n_ok = n_bad = 0


def ck(msg, cond, extra=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('  [PASS] %s' % msg)
    else:
        n_bad += 1
        print('  [FAIL] %s   %s' % (msg, extra))


SRC = find_sav('T85_老工程师_修位置副本槽.SAV')
print('=== §247 自检：小队关联标签 + 克隆不继承触发 ===')

# ---------- A. 读 ----------
d = ec.SaveDoc(SRC)
ai = ai_objects.read_ai(d)
tagged = [t for t in ai['teams'] if t['tag_key']]
ck('A1. 本档有挂标签的小队（样本有效）', len(tagged) > 0, len(tagged))
if tagged:
    t0 = tagged[0]
    print('  样本：小队 %s「%s」→ 标签 %s' % (t0['id'], t0['name'], t0['tag_id']))
    ck('A2. tag_id 解析成 8 位 ID', t0['tag_id'] and len(str(t0['tag_id'])) == 8,
       t0['tag_id'])
    tags = ai_objects.list_tags(d)
    ck('A3. list_tags 能列出本档标签', len(tags) > 0, len(tags))
    hit = [x for x in tags if x['id'] == t0['tag_id']]
    ck('A4. 小队挂的标签确实在标签表里', bool(hit), t0['tag_id'])
    if hit:
        ck('A5. 标签能解出它链到的触发', hit[0]['trigger_id'] is not None,
           hit[0]['trigger_id'])

# ---------- B. 写 ----------
base = bytes(d.plain)
t = tagged[0] if tagged else ai['teams'][0]
old_key = struct.unpack_from('<I', d.plain, t['off'] + ai_objects.TEAM_TAG_OFF)[0]
# 挑一个**不同**的标签
other = next((x for x in ai_objects.list_tags(d) if x['key'] != old_key), None)
if other:
    info = ai_objects.set_team_tag(d, t['id'], other['id'])
    ck('B1. set_team_tag 报 changed', info.get('changed'), info)
    ck('B2. 新键就是目标标签的键', info['new'] == other['key'],
       '%#x ≠ %#x' % (info['new'], other['key']))
    diff = [i for i in range(len(base)) if base[i] != d.plain[i]]
    # 两个标签键常常只差低 1~2 字节 ⇒ 差异字节数 ≤4，位置必须全落在 +0xE4..+0xE8
    ck('B3. 全档改动 ≤4 字节', 0 < len(diff) <= 4,
       '%d 处：%s' % (len(diff), [hex(x - t['off']) for x in diff]))
    ck('B4. 差的字节全在 +0xE4..+0xE8 内',
       all(t['off'] + 0xE4 <= x < t['off'] + 0xE8 for x in diff),
       [hex(x - t['off']) for x in diff])
    back = ai_objects.read_ai(ec.SaveDoc(SRC))['teams']       # 另开一份对照
    ck('B5. 解除（传 0）能写回 0',
       ai_objects.set_team_tag(d, t['id'], 0)['new'] == 0)
    ck('B6. 再挂回去也能成',
       ai_objects.set_team_tag(d, t['id'], other['id'])['new'] == other['key'])

# 无效标签必须报错且不写
before = bytes(d.plain)
try:
    ai_objects.set_team_tag(d, t['id'], '00999999')
    ck('B7. 不存在的标签被拒绝', False, '没有抛错')
except KeyError:
    ck('B7. 不存在的标签被拒绝', bytes(d.plain) == before, '但文件被改了')
except Exception as e:                      # noqa: BLE001
    ck('B7. 不存在的标签被拒绝', False, repr(e))

# 不存在的小队
try:
    ai_objects.set_team_tag(d, '01999999', 0)
    ck('B8. 不存在的小队被拒绝', False, '没有抛错')
except KeyError:
    ck('B8. 不存在的小队被拒绝', True)
except Exception as e:                      # noqa: BLE001
    ck('B8. 不存在的小队被拒绝', False, repr(e))

# ---------- C. 落盘往返 ----------
p = out('ev247_小队关联标签.SAV')
d.save(p, backup=False)
e = ec.SaveDoc(p)
te = next(x for x in ai_objects.read_ai(e)['teams'] if x['id'] == t['id'])
ck('C1. 落盘重读后关联标签保持', te['tag_key'] == other['key'],
   '%#x ≠ %#x' % (te['tag_key'], other['key']))
ck('C2. tag_id 也解析得出来', te['tag_id'] == other['id'], te['tag_id'])

# ---------- D. 克隆绝不继承单位标签挂接 ----------
import bisect as _bs
import collections as _cl


def _attach_ms(doc):
    pl = doc.plain
    units = sorted(doc.unit_records(), key=lambda r: r['off'])
    uoffs = [r['off'] for r in units]
    out_ = _cl.Counter()
    for nd in doc.celltag_nodes():
        X = nd['x']
        if not X:
            continue
        pat = struct.pack('<I', X)
        n = 0
        q = 0
        while True:
            pp = pl.find(pat, q)
            if pp < 0:
                break
            q = pp + 1
            if pp == nd['node'] + 16 or pp + 112 > len(pl):
                continue
            px, py = struct.unpack_from('<II', pl, pp + 104)
            if not (0 < px < doc.MAXPIX and 0 < py < doc.MAXPIX):
                continue
            k = _bs.bisect_right(uoffs, pp) - 1
            if k >= 0 and units[k]['off'] <= pp < units[k]['comp'] + units[k]['comp_size']:
                n += 1
        out_[(nd['count'], n)] += 1
    return out_


print('=== [D] 克隆不继承单位标签挂接（+0x48）===')
d2 = ec.SaveDoc(SRC)
ms0 = _attach_ms(d2)
info = d2.add_unit_by_clone('DISK', 112, 206, house=d2.player_house(), veterancy=0)
r = [x for x in d2.unit_records() if x['key'] == info['key']][0]
ms1 = _attach_ms(d2)
ck('D1. 克隆后所有 CellTag 节点挂接剖面不变', ms0 == ms1,
   '变多 %s' % {k: v for k, v in (ms1 - ms0).items()})
ck('D2. 克隆体 +0x48 = 0', struct.unpack_from('<I', d2.plain, r['off'] + 0x48)[0] == 0,
   hex(struct.unpack_from('<I', d2.plain, r['off'] + 0x48)[0]))
_tagged_u = [x for x in d2.unit_records()
             if struct.unpack_from('<I', d2.plain, x['off'] + 0x48)[0] != 0]
ck('D3. 本档确实有带标签的单位（证明这一项有语义）', len(_tagged_u) > 0, len(_tagged_u))

print('\n§247 自检：PASS %d / FAIL %d' % (n_ok, n_bad))
sys.exit(1 if n_bad else 0)
