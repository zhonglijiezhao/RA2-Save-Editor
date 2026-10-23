# -*- coding: utf-8 -*-
"""§184 自检：跨类型克隆会带整块模板指针 ⇒ 显示不全；用「健康模板 + 跨类型清理」把它掐死。

玩家反馈链：
  §183 让直接落成默认播建造动画 ⇒ 仍「显示不全」。
  §184 查证：那张图**没有 ATESLA** ⇒ 只能跨类型克隆；模板是 GACNST（85 个运行期指针），
        整块被克隆进新楼 ⇒ 渲染残缺。同类型（GAPOWR→GAPOWR）则完全正常
        （玩家早前 `电厂修复S_加建筑带建造动画.SAV` 实测通过，就是同类型）。

本自检锁死：
  [1] 没有同类建筑 / 同类本身残缺 ⇒ `add_placed_readiness(type)` 必须拒绝
  [2] 有健康同类 ⇒ 放行，且新楼指针数很少（<= 15，引擎造的是 0）
  [3] 强行跨类型时 `cross_clean=True` 必须真的把模板指针清掉（远少于 cross_clean=False）
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.editor_core import SaveDoc
from _paths import need_sav

BAD = need_sav('ALL01UMD_放ATESLA.SAV')      # 玩家的图：只有 GAPOWR/GACNST + 一栋放坏的 ATESLA
GOOD = need_sav('T11_工具配方验收.SAV')       # 有引擎造的 ATESLA（触发法产物）

n_ok = n_bad = 0
fails = []


def ck(name, cond, extra=''):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print('  PASS  %s' % name)
    else:
        n_bad += 1
        fails.append(name)
        print('  FAIL  %s   %s' % (name, extra))


def ptr_n(d, g):
    return sum(1 for off in range(16, 1856, 4)
               if 0x0F000000 <= struct.unpack_from('<I', d.plain, g + off)[0] <= 0x14000000)


def find(d, cell):
    for g in d._bld_group()[0]:
        b = next((x for x in d.buildings() if x['base'] == g + 72), None)
        if b and (b['x'], b['y']) == cell:
            return g
    return None


print('=' * 72)
print('§184 自检：模板健康度 + 跨类型指针清理')

# ---------- [1] 拒绝条件 ----------
d = SaveDoc(BAD)
ok, why = d.add_placed_readiness('ATESLA')
print('  [1] ATESLA 判定 = %s  %s' % (ok, (why.splitlines() or [''])[0][:50]))
# ★2026-10-23 §196：**签名判据不再阻断同类型克隆**（T43 实测：健康塔被判 33/313，
#   坏模板反而只有 22/313 ⇒ 与"能否克隆成功"无关）。现在只留一条提示。
ck('本档 ATESLA 签名不达标时**不再阻断**（§196 起只提示）', ok is True, why[:60])
_ = d.add_placed_readiness('ATESLA')
ck('但会给一条提示（写在 _last_readiness_note）',
   '签名' in getattr(d, '_last_readiness_note', ''), '')
ok2, why2 = d.add_placed_readiness('GATECH')
ck('本档没有 GATECH ⇒ 拒绝（不许跨类型克隆）', ok2 is False, why2[:60])
bad, tot = d.template_health(d.same_type_best('ATESLA')[0])
ck('残缺模板健康度确实超标（bad=%d / tot=%d）' % (bad, tot),
   not d._health_ok(bad, tot), '%d/%d' % (bad, tot))

# ---------- [2] 放行条件 + 新楼干净 ----------
ck('GAPOWR 有健康同类 ⇒ 放行', d.add_placed_readiness('GAPOWR')[0] is True)
d2 = SaveDoc(BAD)
c = d2.free_cell_near(70, 92, radius=8, clearance=2)
i2 = d2.add_placed_building('GAPOWR', c[0], c[1], house='Americans',
                            require_free=True, comp_mode='zero', new_state=False)
g2 = find(d2, c)
np2 = ptr_n(d2, g2)
print('  [2] 同类直接落成 @%s 指针数=%d（引擎造 0，GACNST 85）' % (str(c), np2))
ck('同类新楼指针数 <= 15', np2 <= 15, str(np2))
# §188 定案：默认写"建造中"（配方带 6E4=0）⇒ 进游戏播一遍、播完完整
# ★2026-10-23 §196 定案：默认写「刚放下」态（AC=-1 / 534=1 / 6E4=0）
#   —— `done`（6E4=1）会让引擎认为楼已建好 ⇒ 不建炮塔子对象 ⇒ 无头（T42/T43 对照）
_st2 = d2.building_anim_state(g2 + 72)
ck('同类新楼 = 刚放下态（AC=-1 / 534=1 / 6E4=0）',
   _st2['vals']['+0xAC'] in (-1, 0xFFFFFFFF) and _st2['vals']['+0x534'] == 1
   and _st2['vals']['+0x6E4'] == 0,
   '%s %s' % (_st2['state'], {k: _st2['vals'][k] for k in ('+0xAC', '+0x534', '+0x6E4')}))
ck('同类新楼不是 done（done 会无头）', _st2['state'] != 'done', _st2['state'])
ck('同类克隆时模板挑的是健康那栋（不是残缺楼）',
   i2['tpl_key'] != 0x13963000, '%08X' % i2['tpl_key'])

# ---------- [3] 强行跨类型：cross_clean 必须生效 ----------
def cross_run(clean):
    dd = SaveDoc(BAD)
    cc = dd.free_cell_near(96, 86, radius=8, clearance=2)
    ii = dd.add_placed_building('GATECH', cc[0], cc[1], comp_mode='zero',
                                new_state=False, cross_clean=clean)
    gg = find(dd, cc)
    return ii, ptr_n(dd, gg)


i3a, n_clean = cross_run(True)
i3b, n_dirty = cross_run(False)
print('  [3] 跨类型 GATECH：cross_clean=True → %d 个指针；False → %d 个' % (n_clean, n_dirty))
ck('跨类型确实走了 cross 分支', i3a['cross'] is True)
# 注：模板选择也已改成"挑指针最少的那栋"（原来固定取末栋 GACNST=85 个指针），
#     所以这里只要保证 cross_clean 确实又少了一些、且不比不清理更差即可。
ck('cross_clean=True 不比 False 差，且确实清掉了模板指针',
   n_clean <= n_dirty and n_dirty - n_clean >= 1, '%d vs %d' % (n_clean, n_dirty))
ck('note 里报了清理数量', '清掉' in i3a['note'], i3a['note'][-60:])

print('-' * 72)
print('§184 SELFTEST %s   PASS %d / FAIL %d'
      % ('PASS' if not n_bad else 'FAIL', n_ok, n_bad))
if fails:
    print('失败项：')
    for f in fails:
        print('  · %s' % f)
sys.exit(1 if n_bad else 0)
