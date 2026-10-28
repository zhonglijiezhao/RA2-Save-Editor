# -*- coding: utf-8 -*-
"""自检总入口（审查 P4-18）。

为什么要有：项目里有 140+ 个 `_selftest_*.py` / `_ui_ev*.py`，以前全靠人肉记
"跑哪个、基线是几比几"——每次交接文档都要抄一遍
「_ui_ev289 49/0、_selftest_ev289 11/0…」，这本身就是该自动化的信号。

用法
----
    py _selftest_all.py                 跑全部，和基线对比（发布前跑这个）
    py _selftest_all.py --fast          跳过耗时最长的几个
    py _selftest_all.py --only ev29     只跑名字里含 ev29 的
    py _selftest_all.py --list          只列清单，不跑
    py _selftest_all.py --update        跑完把当前结果写成新基线
    py _selftest_all.py --timeout 120   单个用例超时（秒，默认 300）

判定口径
--------
- **只有"相对基线的退化"算失败**：老用例本来就是红的、或者环境原因跳过，
  不让它把整个门禁拖红（否则没人会去看结果）。
- 退化 = 从 ok 变 red / 变 error / 变超时；或者通过数变少了（断言被删掉）
- 基线文件：`_selftest_baseline.json`（跟着仓库走）

退出码：0 = 没有退化；1 = 有退化；2 = 用法错误
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, '_selftest_baseline.json')

# 这几个要全量解码大存档 / 反复构造窗口，单独跑就要 20~50 秒
SLOW = {'_selftest_unit_place', '_selftest_ai', '_selftest_ev280'}

# 已知会卡死的用例（默认跳过，`--only` / `--all` 时才跑）：
#   ev217 / ev218 会走进「小队生成」的模态对话框然后 `wait_window` —— 没人点「确定」，
#   就一直挂着。这是用例本身早就坏了（不是代码回归），修好之前别让它白占 2 分钟。
KNOWN_HANG = {'_ui_ev217', '_ui_ev218'}

# 结果行的写法（历史原因，什么花样都有）：
#   结果：PASS 35 / FAIL 0     自检结果：PASS 38 / FAIL 0
#   结果：49 通过 / 0 失败      ==== 总判定：FAIL ❌（PASS 36 / FAIL 1）====
# 所以不按"前缀"匹配，而是**取全文里最后一个 PASS n / FAIL m**（中间小计会被后面对上）。
RX = [
    re.compile(r'PASS\s*(\d+)\s*/\s*FAIL\s*(\d+)'),
    re.compile(r'PASS\s*(\d+)\s+FAIL\s*(\d+)'),
    re.compile(r'(\d+)\s*通过\s*/\s*(\d+)\s*失败'),
]
# 只打"总判定：PASS ✅"却不给数字的老用例
RX_OK_NOCOUNT = re.compile(r'总判定[：:]\s*PASS')


def discover():
    out = []
    for n in sorted(os.listdir(HERE)):
        if not n.endswith('.py'):
            continue
        if n.startswith('_selftest') or n.startswith('_ui_ev'):
            if n == '_selftest_all.py':      # 别把自己也跑一遍（无限递归）
                continue
            out.append(n[:-3])
    return out


def parse_result(text):
    """→ (pass, fail) 或 None。取**全文位置最后**的那个匹配。

    不能按"模式顺序"取（以前就是这么写错的）：一个用例先打中间小计、
    再打最终结论时，按模式顺序取会拿到小计；按位置取才拿到结论。
    """
    best = None          # (end_pos, pass, fail)
    for rx in RX:
        for m in rx.finditer(text):
            pos = m.end()
            if best is None or pos > best[0]:
                best = (pos, int(m.group(1)), int(m.group(2)))
    return None if best is None else (best[1], best[2])


def run_one(name, timeout):
    """跑一个用例 → dict(name, status, passed, failed, secs, note)。"""
    py = sys.executable or 'python'
    t0 = time.time()
    try:
        p = subprocess.run([py, os.path.join(HERE, name + '.py')],
                           cwd=HERE, capture_output=True, timeout=timeout,
                           env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    except subprocess.TimeoutExpired:
        return {'name': name, 'status': 'timeout', 'passed': 0, 'failed': 0,
                'secs': round(time.time() - t0, 1), 'note': '超过 %ds' % timeout}
    secs = round(time.time() - t0, 1)
    out = (p.stdout or b'').decode('utf-8', 'replace')
    err = (p.stderr or b'').decode('utf-8', 'replace')
    res = parse_result(out)
    if res is None:
        if RX_OK_NOCOUNT.search(out):
            return {'name': name, 'status': 'ok', 'passed': 0, 'failed': 0,
                    'secs': secs, 'note': '只报了总判定 PASS（无计数）'}
        tail = (err.strip() or out.strip()).splitlines()
        note = tail[-1][:120] if tail else '无输出'
        st = 'skip' if _env_limited(note) else 'error'
        return {'name': name, 'status': st, 'passed': 0, 'failed': 0,
                'secs': secs, 'note': note}
    ok, bad = res
    return {'name': name, 'status': 'ok' if bad == 0 else 'red',
            'passed': ok, 'failed': bad, 'secs': secs, 'note': ''}


def load_baseline():
    if not os.path.exists(BASELINE):
        return {}
    try:
        with open(BASELINE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_baseline(rows, merge=True):
    """默认**合并**：只覆盖这次跑到的用例，其余原样保留。

    这样加了一个新用例可以只跑它 + `--update`，不用把 140 个全跑一遍。
    """
    data = load_baseline() if merge else {}
    for r in rows:
        data[r['name']] = {'status': r['status'], 'passed': r['passed'],
                           'failed': r['failed']}
    tmp = BASELINE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write('\n')
    os.replace(tmp, BASELINE)
    return len(data)


ENV_MARKS = ('SAFE_DELETE', 'No such file', 'not exist', 'SKIP', '缺样本',
             '缺少', 'FileNotFoundError')


def _env_limited(note):
    """这条失败是"环境/样本不足"，还是"代码真错了"？

    ★沙箱里跑自检会被安全策略拦批量删除（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`），
      很多用例都要删 50+ 个临时文件 —— 那是**跑的地方不对**，不是代码坏了。
      真实终端里不会出现。
    """
    return any(m in note for m in ENV_MARKS)


def judge(rows, base, full=True):
    """→ (退化列表, 备注列表)。只有退化会让门禁失败。

    `full=False`（只跑子集，例如 --only / --redo-bad / --fast）时**不比对"磁盘上少了谁"**，
    否则会把"这次没跑"误报成"用例被删了"。
    """
    regress, notes = [], []
    for r in rows:
        b = base.get(r['name'])
        if b is None:
            notes.append('%s：新用例（基线里没有）' % r['name'])
            continue
        if b['status'] == 'ok' and r['status'] != 'ok':
            regress.append('%s：%s → %s%s'
                           % (r['name'], b['status'], r['status'],
                              ('（%s）' % r['note']) if r['note'] else ''))
        elif b['status'] == r['status'] == 'ok' and r['passed'] < b['passed']:
            regress.append('%s：通过数 %d → %d（断言被删了？）'
                           % (r['name'], b['passed'], r['passed']))
    if full:
        seen = {r['name'] for r in rows}
        for n in sorted(set(base) - seen):
            notes.append('%s：基线里有，磁盘上没有（改名/删了？）' % n)
    return regress, notes


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('--fast', action='store_true', help='跳过慢用例')
    ap.add_argument('--all', action='store_true', help='连已知会卡死的也跑')
    ap.add_argument('--redo-bad', action='store_true',
                    help='只跑基线里不是绿的那些（改过判定口径后用它刷基线，省时间）')
    ap.add_argument('--only', default='', help='只跑名字含该子串的')
    ap.add_argument('--list', action='store_true', help='只列清单')
    ap.add_argument('--update', action='store_true', help='把当前结果写成新基线')
    ap.add_argument('--timeout', type=int, default=300)
    a = ap.parse_args()

    names = discover()
    if a.only:
        names = [n for n in names if a.only in n]
    if a.fast:
        names = [n for n in names if n not in SLOW]
    if not (a.all or a.only):
        names = [n for n in names if n not in KNOWN_HANG]
    if a.redo_bad:
        base0 = load_baseline()
        names = [n for n in names if base0.get(n, {}).get('status') != 'ok']
    if a.redo_bad:
        base0 = load_baseline()
        names = [n for n in names if base0.get(n, {}).get('status') != 'ok']
    if not names:
        print('没有匹配的用例'); return 2

    if a.list:
        for n in names:
            print('  %s%s' % (n, '   [慢]' if n in SLOW else ''))
        print('\n共 %d 个' % len(names))
        return 0

    print('=' * 78)
    print('自检总入口：%d 个用例（单个超时 %ds）' % (len(names), a.timeout))
    print('=' * 78)

    rows = []
    t0 = time.time()
    for i, n in enumerate(names, 1):
        r = run_one(n, a.timeout)
        rows.append(r)
        mark = {'ok': '  ok  ', 'red': ' FAIL ', 'error': 'ERROR ',
                'skip': ' skip ', 'timeout': 'TIMEOUT'}[r['status']]
        print('  [%3d/%3d] %-6s %-42s %4d/%d  %5.1fs %s'
              % (i, len(names), mark, n, r['passed'], r['failed'], r['secs'],
                 r['note']))
        sys.stdout.flush()

    total = time.time() - t0
    bad = [r for r in rows if r['status'] in ('red', 'error', 'timeout')]
    for r in bad:
        r['nosample'] = (r['status'] == 'error'
                         and ('No such file' in r['note'] or 'not exist' in r['note']))
    ok = [r for r in rows if r['status'] == 'ok']
    nos = [r for r in bad if r['nosample']]

    print('\n' + '=' * 78)
    skips = [r for r in rows if r['status'] == 'skip']
    print('合计 %d 个：绿 %d / 红 %d / 环境跳过 %d / 其它异常 %d / 超时 %d，总耗时 %.1fs'
          % (len(rows), len(ok), len([r for r in bad if r['status'] == 'red']),
             len(skips),
             len([r for r in bad if r['status'] == 'error' and not r['nosample']]),
             len([r for r in bad if r['status'] == 'timeout']), total))
    rest = [r for r in bad if not r['nosample']]
    if rest:
        print('\n不是绿的（基线里也是红的就不算退化）：')
        for r in rest:
            print('  %-8s %-42s %s' % (r['status'], r['name'], r['note']))
    if nos:
        print('\n缺样本（环境原因，不算退化）：%d 个，例如 %s'
              % (len(nos), ', '.join(r['name'] for r in nos[:6])))

    base = load_baseline()
    if not base:
        print('\n（还没有基线：这次结果可以直接 `--update` 存成基线）')
    full_run = not (a.only or a.redo_bad or a.fast)
    regress, notes = judge(rows, base, full=full_run)
    if notes:
        print('\n备注：')
        for n in notes:
            print('  · ' + n)
    if regress:
        print('\n★ 相对基线退化 %d 项：' % len(regress))
        for r in regress:
            print('  ✗ ' + r)
    elif base:
        print('\n✓ 与基线一致，没有退化')

    if a.update:
        n = save_baseline(rows)
        print('\n已写入基线：%s（%d 条）' % (os.path.basename(BASELINE), n))

    return 1 if regress else 0


if __name__ == '__main__':
    sys.exit(main())
