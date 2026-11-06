# -*- coding: utf-8 -*-
"""CI 用的**纯逻辑**自检：不需要存档样本、不需要游戏目录、不需要 tkinter。

为什么单独开一个（审核 §4.2 / §4.3）
------------------------------------
现有 142 个 `_selftest_*.py` 基本都是"构造整档 → 改 → 断言"的集成脚本，
样本存档（`_exp/*.SAV`、`_tmp_ev252.SAV`）因为版权/隐私**不能进仓库**，
所以 CI 上跑出来的全是 `skip` / `error`，"145 例只有 76 绿"有一半是这个原因。

这个文件只测**纯函数与表**：CFB 解析边界、LZO 往返、INI 的 `+=` 追加语法、
事件/行为表、错误提示分类、静默异常观测器、版本号一致性。
在 GitHub Actions 的 windows-latest + 裸 Python 上就能跑，一条命令、几秒钟。

跑法：
    python _selftest_ci_pure.py        # 退出码 0 = 全绿
"""
import os
import sys
import struct
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

OK = FAIL = 0


def chk(c, m):
    global OK, FAIL
    if c:
        OK += 1
        print('  [PASS] ' + m)
    else:
        FAIL += 1
        print('  [FAIL] ' + m)


print('=' * 72)
print('1. CFB 解析边界（损坏档必须"拒绝读"而不是"读出垃圾"）')
print('=' * 72)
from cfb import CFB

CFB_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'

try:
    CFB(b'')
    chk(False, '空数据应当抛异常')
except Exception as e:
    chk(isinstance(e, (ValueError, struct.error)),
        '空数据 → 抛异常（%s：%s）' % (type(e).__name__, e))

try:
    CFB(CFB_MAGIC + b'\x00' * 504)      # 只有魔数、长度够 512
    chk(False, '只有魔数的 512 字节应当抛异常')
except Exception as e:
    chk(isinstance(e, (struct.error, ValueError, IndexError)),
        '只有魔数 → 抛异常（%s，不是静默返回空对象）' % type(e).__name__)

try:
    CFB(CFB_MAGIC + b'\x00' * 8)        # 魔数对但长度不足 512
    chk(False, '太短的文件应当抛异常')
except ValueError as e:
    chk('CFB' in str(e).upper() and 'truncat' in str(e).lower(),
        '长度不足 512 → 明确说"截断"（%s）' % e)

try:
    CFB(b'\x00' * 4096)
    chk(False, '非 CFB 数据应当抛异常')
except ValueError as e:
    chk('CFB' in str(e).upper(), '错误魔数 → ValueError("not a CFB file")')

print()
print('=' * 72)
print('2. LZO 往返（写盘的压缩链路；错了就是存档打不开）')
print('=' * 72)
import lzo_compress
from lzo1x import lzo1x_decompress, LZOError

CASES = [b'hello world' * 100,
         bytes(range(256)) * 4,
         b'\x00' * 200000,                 # 大块全零（走长匹配分支）
         b'A' * 17 + b'B' * 300]           # 跨"首字节 >17"的分支
for d in CASES:
    c = lzo_compress.compress(d)
    o, used = lzo1x_decompress(c, expect_out=len(d))
    chk(o == d, '往返 %6d B → %4d B 无损' % (len(d), len(c)))
    chk(used == len(c), '   consumed_ip == 压缩长度（%d）' % used)

try:
    lzo1x_decompress(b'\x11\x00\x99\xff\xff', expect_out=64)
    chk(False, '坏数据应当抛 LZOError')
except LZOError:
    chk(True, '坏压缩数据 → LZOError（不会读出半截明文）')
except Exception as e:
    chk(True, '坏压缩数据 → 抛异常（%s）' % type(e).__name__)

print()
print('=' * 72)
print('3. INI 的 `+=` 追加注册（★铁律：dict 存会互相覆盖、int("+") 会炸）')
print('=' * 72)
import game_ini

ini = tempfile.NamedTemporaryFile('w', suffix='.ini', delete=False, encoding='utf-8')
ini.write('; 注释行\n'
          '[VehicleTypes]\n'
          '0=AMCV\n'
          '+=SCHP1\n'
          '+=SCHP2\n'
          '1=HTNK ; 行尾注释\n'
          '[BuildingTypes]\n'
          '+=NABLD1\n')
ini.close()
try:
    d = game_ini.parse_ini(ini.name)
    vt = d.get('vehicletypes') or {}
    chk(vt.get('add') == ['SCHP1', 'SCHP2'], '+= 逐条收进 add（%s）' % (vt.get('add'),))
    chk(vt.get('order') == ['0', '+', '+', '1'],
        'order 保留文件里的先后顺序（%s）' % (vt.get('order'),))
    chk(vt['kv'].get('0') == 'AMCV', '普通键值照常解析')
    chk(vt['kv'].get('1') == 'HTNK', '行尾 ; 注释被剥掉（%s）' % vt['kv'].get('1'))
    chk((d.get('buildingtypes') or {}).get('add') == ['NABLD1'], '多段互不串味')
    chk('; 注释行' not in str(d), '整行注释被忽略')
finally:
    os.unlink(ini.name)

print()
print('=' * 72)
print('4. 事件/行为表（改错一个数字，玩家看到的就是"无效动作"）')
print('=' * 72)
import fa2_tables as T

chk(T.action_ptype(54) == 14, '行为 54「禁止触发」是触发型（ptype 14）')
chk(T.action_ptype(53) == 14, '行为 53「允许触发」是触发型')
chk(T.action_ptype(70) == 38, '行为 70「摧毁标签」ptype 38（目标在 +96）')
chk(T.action_ptype(108) == 31, '行为 108「创建工具箱」ptype 31（要落点）')
chk('禁止' in T.action_name(54), '行为 54 的中文名带"禁止"')
chk(T.event_name(0) == '-无事件-', '事件 0 = 无事件')
chk(T.event_name(999).startswith('未知'), '未知事件不抛异常、给"未知(n)"')
chk(T.action_name(998).startswith('未知'), '未知行为同上')
chk(len(T.SCRIPT_ACTIONS) > 150, '脚本动作表非空（%d 条）' % len(T.SCRIPT_ACTIONS))
chk(len(T.SCRIPT_PARAM_TYPES) > 30, '脚本参数类型表非空（%d 类）' % len(T.SCRIPT_PARAM_TYPES))

print()
print('=' * 72)
print('5. 错误提示分类（审核 §1.3：玩家看得懂，且不泄露 traceback）')
print('=' * 72)
import errormsg

CHECKS = [
    (FileNotFoundError(2, 'No such file'), '找不到'),
    (PermissionError(13, 'denied'), '权限'),
    (MemoryError(), '内存'),
    (OSError(28, 'No space left'), '空间'),
]
for exc, kw in CHECKS:
    t, body = errormsg.explain(exc, os.path.join(HERE, 'x.SAV'), '打开')
    chk(kw in body, '%-22s → 结论含"%s"' % (type(exc).__name__, kw))
    chk('Traceback' not in body, '  提示里没有 traceback 原文')

t, body = errormsg.explain(ValueError('not a CFB file'), os.path.join(HERE, 'x.SAV'))
chk('红警' in body or '存档' in body, '非 CFB → 说"不是存档"')
chk('技术摘要' in body, '仍保留一行技术摘要（报 issue 用）')

print()
print('=' * 72)
print('6. 静默异常观测器（审核 §3.3：故意坏掉也不能拖垮主流程）')
print('=' * 72)
import diag

diag.reset()
for _ in range(3):
    try:
        int('x')
    except Exception:
        diag.swallow()
n, total, over = diag.counts()
chk((n, total, over) == (1, 3, 0), '同一位置合并计数（%d 类 / %d 次）' % (n, total))
chk('×3' in diag.summary(), 'summary 显示次数')

diag.reset()
_orig = diag._record


def _broken(*a, **k):
    raise RuntimeError('观测器自己坏了')


diag._record = _broken
raised = False
try:
    try:
        int('y')
    except Exception:
        diag.swallow()
except BaseException:
    raised = True
diag._record = _orig
chk(not raised, '观测器内部异常不会外泄')
chk(diag._BROKEN is True, '内部出错后自动闭嘴')
diag.reset()
chk(diag.counts()[0] == 0, 'reset() 可恢复')

print()
print('=' * 72)
print('7. 版本号一致性与导入链')
print('=' * 72)
import app_version

vtxt = app_version.version_text()
chk(vtxt.startswith('v'), '版本号形如 v1.7.3（%s）' % vtxt)
chk(('p' in vtxt) or not app_version.BUILD, '带构建号（同一版可能重打包多次）')
chk(app_version.REPO.startswith('https://github.com/'), '仓库地址是 https')

# 核心模块全量可导入（CI 上最容易挡住的"改 A 崩 B"）
#   `ui_theme` 虽然住在 core/ 但是 UI 模块（要 tkinter），CI 的裸 Python 没有 tk ⇒ 排除。
CORE = [os.path.basename(p)[:-3] for p in sorted(os.listdir(os.path.join(HERE, 'core')))
        if p.endswith('.py') and p != 'ui_theme.py']
bad = []
for m in CORE:
    try:
        __import__(m)
    except Exception as e:
        bad.append('%s(%s)' % (m, str(e)[:40]))
chk(not bad, 'core/ 下 %d 个模块全部可导入%s' % (len(CORE), '' if not bad else '：%s' % bad))

print()
print('-' * 72)
print('CI-PURE 自检结果：PASS %d / FAIL %d' % (OK, FAIL))
print('-' * 72)
sys.exit(0 if FAIL == 0 else 1)
