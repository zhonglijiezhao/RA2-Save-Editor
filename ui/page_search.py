# -*- coding: utf-8 -*-
"""搜索页（原 `App.search`）

★2026-10-28（审查 P2-8）：`App` 原来 8400 行、222 个方法，是典型的 God Class。
  这里按"页"切开，**方法体一行没改** —— 用 mixin 而不是把 `self` 换成 `self.app`，
  所以 `self.xxx` 照旧指向同一个 App 实例，行为零变化。
  边界 = 原来 `_tab_xxx()` 那整段（含它引用的私有辅助方法）。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, 'core')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from editor_core import SaveDoc
from tkinter import filedialog
from tkinter import messagebox
from tkinter import simpledialog
from tkinter import ttk
import json
import os
import queue
import re
import struct
import threading
import time
import tkinter as tk
import traceback
import ui_theme


class SearchPage(object):
    def _tab_search(self, nb):
        f = ttk.Frame(nb, padding=8)
        _ph = ui_theme.page_header(f, '明文搜索', '在明文里搜字符串 / 偏移')
        _ph.pack(fill='x', padx=2, pady=(2, 8))
        row = ttk.Frame(f)
        row.pack(fill='x')
        ttk.Label(row, text='查找').pack(side='left')
        self.ent_search = ttk.Entry(row, width=36)
        self.ent_search.pack(side='left', padx=6)
        ttk.Button(row, text='搜索', command=self.do_search).pack(side='left')
        ttk.Label(row, text='（支持中文/英文，或十六进制如 5C197E00）',
                  foreground=ui_theme.T['muted']).pack(side='left', padx=8)
        self.lst_hits = tk.Listbox(f, height=8, font=('Consolas', 9))
        self.lst_hits.pack(fill='x', pady=6)
        self.lst_hits.bind('<<ListboxSelect>>', self.on_hit_select)
        self.txt_hex = tk.Text(f, height=18, font=('Consolas', 9), wrap='none')
        self.txt_hex.pack(fill='both', expand=True)
        return f

    # ---------------- 列表 ----------------
    def refresh_list(self):
        d = self.game_dir.get().strip()
        self.tree.delete(*self.tree.get_children())
        self._sync_game_dir(persist=True)      # 目录变了就告诉 core，并记住
        if not os.path.isdir(d):
            self.status.set('目录不存在: %s' % d)
            return
        files = []
        # ★2026-10-28（审查 P4-21）：以前只看顶层目录，而玩家**常把存档挪进子文件夹**
        #   （交接文档里开发脚本用 `_paths.find_sav()` 递归找，工具自己反而看不见）。
        #   现在扫顶层 + 一级子目录；`_备份存档` 跳过（那是本工具自己的备份，别混进列表）。
        def _collect(dirp, prefix=''):
            try:
                names = os.listdir(dirp)
            except Exception as e:
                self.status.set('读取目录失败（%s）：%s' % (dirp, e))
                return
            for n in names:
                fp = os.path.join(dirp, n)
                if n.lower().endswith(('.sav', '.sav.bak')):
                    files.append((fp, prefix + n))
                elif not prefix and os.path.isdir(fp) and n != '_备份存档':
                    _collect(fp, n + '/')

        _collect(d)
        files.sort(key=lambda x: -os.path.getmtime(x[0]))
        for i, (p, disp) in enumerate(files[:200]):
            self.tree.insert('', 'end', iid=p, values=(disp, '', ''),
                             tags=('odd',) if i % 2 else ())
        self.status.set('共 %d 个存档（按时间倒序）' % len(files))
        try:
            self.lbl_list_count.config(text='%d 个' % len(files))
        except Exception:
            pass
        self._scan_meta(list(self.tree.get_children())[:12])

    # ---- 列表元信息缓存（★2026-10-28 审查 P3-13）----
    # 为什么要有：列表只显示「时间 / 任务名 / 金钱」，但**任务名和金钱只能全量解码**才拿得到。
    # 前 12 个档逐个 `SaveDoc()` ⇒ 启动后台烧 ~15 秒 CPU（实测 1.28s/档，大档更久），
    # 还每份顺手写 26MB 明文缓存 ⇒ 光启动≈300MB 磁盘写，而扫完就 `del doc`，那份缓存根本没人用。
    # 现在：键 =(路径, mtime, size) → 命中直接填表，一次解码都不做；未命中才解码，
    # 且 `write_cache=False`（不产生那 26MB）。
    _META_CACHE_NAME = 'meta.json'

    def _meta_cache_path(self):
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        d = os.path.join(base, 'RA2SED_cache')
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            return None
        return os.path.join(d, self._META_CACHE_NAME)

    def _meta_cache_load(self):
        p = self._meta_cache_path()
        if not p or not os.path.exists(p):
            return {}
        try:
            with open(p, 'r', encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}                       # 损坏就当没有，别让列表打不开

    def _meta_cache_save(self, cache):
        """原子写（tmp + replace）；顺手丢掉已不存在的存档条目，别让它无限长。"""
        p = self._meta_cache_path()
        if not p:
            return
        try:
            live = {k: v for k, v in cache.items() if os.path.exists(k)}
            tmp = p + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(live, f, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception:
            pass

    def _scan_meta(self, items):
        """后台读取前 N 个存档的元信息（任务名/时间/金钱需要完整解码，较慢）。
        ★ 同一时刻只跑一个扫描线程（重复点刷新不再堆积）。

        ★★2026-10-27（玩家报「添加行为会卡、然后触发编辑器闪退」）：
          这里对**前 12 个存档**逐个 `SaveDoc(p)` 全量解码，每个 3~9 秒、39MB 大档 460+ 块 LZO，
          而且明文全留在内存里。玩家开程序时它在后台跑，一进触发编辑器点「添加」就假死。
          ⇒ ① 带代号，用户切页签即作废（离开存档列表页就不该再扫）；
             ② 每档之间 sleep 让出 GIL；③ 读完 `del doc` 释放明文。

        ★★2026-10-28（审查 P3-13）：先查 `meta.json` 缓存。命中就不解码 ——
          第二次开程序这个后台线程基本是"零成本"（只 stat 12 个文件）。
        """
        if getattr(self, '_scanning', False):
            return
        self._scanning = True
        self._scan_gen = getattr(self, '_scan_gen', 0) + 1
        _gen = self._scan_gen

        def work():
            cache = self._meta_cache_load()
            dirty = 0
            try:
                for p in items[:12]:
                    if _gen != getattr(self, '_scan_gen', _gen):
                        return          # 用户已经切走/换了目录 ⇒ 别再抢 CPU
                    try:
                        _stt = os.stat(p)
                        _mt, _sz = int(_stt.st_mtime), int(_stt.st_size)
                    except Exception:
                        _mt = _sz = None
                    hit = cache.get(p)
                    if (hit and _mt is not None and len(hit) >= 5
                            and hit[0] == _mt and hit[1] == _sz):
                        self.q.put(('row', p, hit[2], hit[3], hit[4]))
                        continue                    # ★命中：不解码、不写盘
                    try:
                        raw = open(p, 'rb').read()
                        from cfb import CFB
                        c = CFB(raw)
                        import datetime

                        def ft(u64):
                            if not u64:
                                return '-'
                            return (datetime.datetime(1601, 1, 1) +
                                    datetime.timedelta(microseconds=u64 // 10)
                                    ).strftime('%m-%d %H:%M')
                        st = c.open('Last Save Time') or b''
                        t = ft(struct.unpack_from('<Q', st + b'\0' * 8, 0)[0])
                        # ★扫元信息不写 26MB 明文缓存（见上面 P3-13 说明）
                        doc = SaveDoc(p, write_cache=False)
                        _title = doc.info.get('任务名', '-')
                        _money = doc.info.get('金钱')
                        self.q.put(('row', p, _title, t, _money))
                        if _mt is not None:
                            cache[p] = [_mt, _sz, _title, t, _money]
                            dirty += 1
                        del doc                 # ★明文别攒着（12 份大档几百 MB）
                    except Exception:
                        self.q.put(('row', p, '?', '?', None))
                    time.sleep(0.05)            # ★让主线程有喘息机会
            finally:
                if dirty:
                    self._meta_cache_save(cache)
                self._scanning = False
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        """常驻后台消息轮询 —— **永远不停**（之前的 bug：载入时另开了一条临时等待链，
        一旦队列头不是 'loaded' 就 return 且不再安排下一次，导致进度条永远转、档打不开）。"""
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == 'row':
                    p, title, t, money = msg[1:]
                    if self.tree.exists(p):
                        self.tree.item(p, values=(os.path.basename(p),
                                                  t or title,
                                                  '' if money is None else money))
                elif kind == 'status':
                    self.status.set(msg[1])
                elif kind == 'loaded':
                    self.pbar.stop()
                    self._pending_load = False
                    if msg[1] is not None:
                        self._set_doc_async(msg[1])
                elif kind == 'done':
                    self.pbar.stop()
                    self._saving = False
                    self.status.set(msg[1])
                    messagebox.showinfo('完成', msg[1])
                elif kind == 'err':
                    self.pbar.stop()
                    self._pending_load = False
                    self._saving = False
                    messagebox.showerror('出错', msg[1])
        except queue.Empty:
            pass
        except Exception as e:                    # 单条坏消息不能让轮询停摆
            self.status.set('后台消息处理异常：%s' % e)
        # ---- 看门狗：长时间没结果就把进度条停掉，绝不"一直转" ----
        now = time.time()
        if self._pending_load and now - self._t_load > 120:
            self._pending_load = False
            self.pbar.stop()
            self.status.set('解码超时（>120 秒）—— 请重试，或换一个存档')
        if self._saving and now - self._t_save > 180:
            self._saving = False
            self.pbar.stop()
            self.status.set('保存超时（>180 秒）—— 请确认文件未被占用后重试')
        # ---- 标题栏的「*未保存」标记（约每 0.6 秒核一次，26MB 明文比较约 2ms）----
        self._title_tick = getattr(self, '_title_tick', 0) + 1
        if self._title_tick % 5 == 0:
            self._refresh_title()
        self.after(120, self._poll)

    # ---------------- 未保存改动保护（★2026-10-28 审查 P1-5）----------------
    # 以前：全项目没有 protocol('WM_DELETE_WINDOW')，改了一堆触发后直接点 × 或者
    # 在列表里点另一个存档 ⇒ `self.doc` 被换掉，改动**无声消失**，一句提示都没有。
    def _is_modified(self):
        """当前存档有没有未保存的改动（用 `modified()`，不用 `dirty()` —— 后者漏报）。"""
        try:
            return bool(self.doc and self.doc.modified())
        except Exception:
            return False

    def _refresh_title(self):
        """标题栏显示当前文件 + `*`（有未保存改动时）。"""
        base = 'RA2 / 尤里的复仇 — 存档编辑器'
        try:
            if self.doc and getattr(self.doc, 'path', None):
                mark = ' *' if self._is_modified() else ''
                self.title('%s — %s%s' % (base, os.path.basename(self.doc.path), mark))
            else:
                self.title(base)
        except Exception:
            pass

    def _confirm_discard(self, what='继续'):
        """有未保存改动时问一句。→ True 可以继续 / False 中止本次操作。

        三个选项（`askyesnocancel`）：
          **是**   = 先去保存 → 弹出「另存为」，本次操作**中止**（存完再自己点一次，最不容易搞错）
          **否**   = 丢弃改动，继续
          **取消** = 什么都不做
        """
        if not self._is_modified():
            return True
        r = messagebox.askyesnocancel(
            '有未保存的改动',
            '当前存档还有**未保存**的改动。\n\n'
            '是（Y）  = 先去保存（弹出「另存为」），本次操作先取消\n'
            '否（N）  = 丢弃这些改动，直接%s\n'
            '取消     = 什么都不做' % what,
            icon='warning', default='cancel')
        if r is None:                     # 取消
            return False
        if r:                             # 是 ⇒ 去保存，本次操作中止
            self.save_as()
            self.status.set('已打开「另存为」，保存完成后请再%s一次' % what)
            return False
        return True                       # 否 ⇒ 丢弃改动，继续

    def _on_close(self):
        """点窗口 × —— 有未保存改动先问，别让玩家白改一小时。"""
        try:
            if self._is_modified():
                if not messagebox.askyesno(
                        '确认退出',
                        '当前存档还有**未保存**的改动，退出后会全部丢失。\n\n确定退出吗？',
                        icon='warning', default='no'):
                    return
            self.destroy()
        except Exception:
            try:
                self.destroy()
            except Exception:
                pass

    def _bind_shortcuts(self):
        """★2026-10-28（审查 P1-7）：桌面软件的肌肉记忆。

        `Ctrl+S` = 另存为新文件（本工具的**推荐**路径，绝不覆盖原档）
        `Ctrl+O` = 打开存档　`F5` = 刷新列表

        用 `self.bind` 而不是 `bind_all` —— 后者会把快捷键漏进「触发编辑器」那个
        Toplevel，在那边按 Ctrl+S 会去保存主窗口的档，很容易误操作。
        """
        def _k(seq, fn):
            try:
                self.bind(seq, fn, add='+')
            except Exception:
                pass

        def _wrap(fn):
            def h(_e=None):
                try:
                    fn()
                except Exception as e:
                    self.status.set('快捷键执行失败：%s' % e)
                return 'break'
            return h

        _k('<Control-s>', _wrap(self.save_as))
        _k('<Control-o>', _wrap(self.open_dialog))
        _k('<F5>', _wrap(self.refresh_list))

    # ---------------- 打开 ----------------
    def open_selected(self):
        sel = self.tree.selection()
        if sel:
            self.load_doc(sel[0])

    def open_dialog(self):
        p = filedialog.askopenfilename(title='选择存档',
                                       filetypes=[('红警存档', '*.sav *.SAV'), ('所有文件', '*.*')])
        if p:
            self.load_doc(p)

    def load_doc(self, path):
        if getattr(self, '_pending_load', False):
            self.status.set('正在打开上一个存档，请稍候…')
            return
        # ★2026-10-28（审查 P1-5）：换档前先问一句 —— 以前改完不保存就点另一个存档，
        #   `self.doc` 直接被换掉，改动无声消失。
        if not self._confirm_discard('打开另一个存档'):
            return
        # ★2026-10-28（审查 P0-4）：玩家可能在目录框里直接改了路径却没点刷新 ⇒
        #   开档前再同步一次，保证 core 用的是最新的游戏目录。
        self._sync_game_dir()
        self._pending_load = True
        self._t_load = time.time()
        self.pbar.start(12)
        self.status.set('正在解码 %s …' % os.path.basename(path))

        def work():
            try:
                doc = SaveDoc(path)
                self.q.put(('loaded', doc))
            except Exception as e:
                self.q.put(('err', '解码 %s 失败：\n%s'
                            % (os.path.basename(path), traceback.format_exc())))
        threading.Thread(target=work, daemon=True).start()
        # 结果统一由常驻的 _poll() 处理（不要在这里另开"临时等待链"）

    # ---------------- 打开：分阶段刷新（§272 大档读条） ----------------
    def _doc_steps(self):
        """载入后要做的事，按"能看见进度"的粒度切成 6 步（同步版与读条版共用）。"""
        return [('挂载地图', self._st_attach_map),
                ('解析触发器列表', self._st_records),
                ('刷新所属方', self._st_houses),
                ('刷新 AI 页（小队/特遣/脚本）', self._st_ai),
                ('准备地图页', self._st_map),
                ('收尾', self._st_finish)]

    def _st_attach_map(self):
        """① 挂地图 + 概览上的基本信息。"""
        try:
            self._map_mode = self.doc.auto_attach_map([self.game_dir.get()])
        except Exception:
            self._map_mode = 'no_map'
        for k, lbl in self.info_labels.items():
            lbl.config(text=str(self.doc.info.get(k, '-')))
        self.lbl_money.config(text=str(self.doc.info.get('金钱')))
        self.lbl_money_field.config(text='字段: %s' % self.doc.info.get('金钱字段', ''))
        try:
            mp = ''
            try:
                mp = self.doc.scenario_map_path() or ''
            except Exception:
                mp = ''
            self.lbl_map.config(text=os.path.basename(mp) if mp else '（未挂接）')
            self.lbl_map_mode.config(text={'auto': '自动匹配', 'manual': '手动指定',
                                           'no_map': '未找到地图'}.get(
                getattr(self, '_map_mode', ''), getattr(self, '_map_mode', '-') or '-'))
        except Exception:
            pass
        # ★v1.3：操作条右侧显示当前打开的存档（原来只能看状态栏）
        try:
            self.lbl_cur.config(text='已打开　%s' % os.path.basename(self.doc.path)
                                if getattr(self.doc, 'path', '') else '已打开',
                                fg=ui_theme.T['ink'])
        except Exception:
            pass

    def _st_records(self):
        """② 触发表（大档最慢的一步之一）。"""
        self.refresh_records()
        for b in ('btn_save_new', 'btn_save_in', 'btn_map_auto', 'btn_map_pick'):
            try:
                getattr(self, b).config(state='normal')
            except Exception:
                pass
        self._refresh_unit_rule_label()          # 规则跟着"存档所在目录"走 ⇒ 换档要刷新
        if hasattr(self, 'mv_unit'):
            try:
                self.mv_unit['values'] = self._unit_type_names()
            except Exception:
                pass

    def _st_houses(self):
        """③ 所属方金钱表 + 各下拉的所属方列表。"""
        self.refresh_houses()
        try:
            self._fill_house_lists(log=False)      # ★所属方下拉随开档就绪（2026-10-17）
        except Exception:
            pass

    def _st_ai(self):
        """④ 事件 / 行为 / AI（小队·特遣·脚本）/ 挂接。"""
        self.refresh_events()
        self.refresh_actions()
        self.refresh_ai()
        self._attach_scanned = False
        self.refresh_attach()

    def _st_map(self):
        """⑤ 地图页：只做"准备好"，**渲染推迟到切到那一页时**（§272 提速）。"""
        self._mv_reset(lazy=True)

    def _st_finish(self):
        """⑥ 收尾：状态栏总结 + 延迟体检建造动画。"""
        ms = getattr(self.doc, 'map_status', None)
        self.status.set('已载入 %s：%d 条记录 · %s' % (
            os.path.basename(self.doc.path), len(self.doc.records),
            '未挂地图' if getattr(self, '_map_mode', '') == 'no_map'
            else (ms or {}).get('text', '')))
        self._refresh_title()                 # ★P1-5：标题栏显示文件名（刚载入时无 *）
        try:
            self.after(400, self._auto_scan_anim)
        except Exception:
            pass

    def _set_doc(self, doc):
        """**同步**刷新（自检脚本 / 命令行用；界面上走 `_set_doc_async` 才有读条）。"""
        self.doc = doc
        self._warned_zero = False
        self._wpn_loaded = False          # ★换档 ⇒ 武器页要重填
        for _n, fn in self._doc_steps():
            fn()
        self._wpn_refresh_if_visible()

    def _set_doc_async(self, doc):
        """★★§272：分步刷新 + 读条。

        为什么要有它：大任务档（文件 3.4MB / 明文 26MB / 1448 单位 / 1113 建筑）一次刷完
        **要 17 秒**（实测拆分：地图页复位 9.3s、触发表 5.1s、AI 页 2.5s），
        这 17 秒里 Tk 主线程被占死、进度条也不动 —— 玩家以为程序卡了。
        现在每步之间 `update()` 让出一次事件循环：进度条按真实步数走、状态栏显示
        "第几步 / 已用几秒"，窗口不会假死。
        """
        self.doc = doc
        self._warned_zero = False
        self._wpn_loaded = False          # ★换档 ⇒ 武器页要重填
        self._st_steps = list(self._doc_steps())
        self._st_total = len(self._st_steps)
        self._st_t0 = time.time()
        self._wpn_refresh_if_visible()
        try:
            self.pbar.stop()
            self.pbar.config(mode='determinate', maximum=100, value=0)
        except Exception:
            pass
        self.after(1, self._run_doc_stage)

    def _run_doc_stage(self):
        """走一步 + 刷一次界面；走完收进度条。"""
        steps = getattr(self, '_st_steps', None)
        if not steps:
            return
        done = self._st_total - len(steps)
        name, fn = steps.pop(0)
        try:
            try:
                self.pbar['value'] = int(done * 100.0 / self._st_total)
            except Exception:
                pass
            self.status.set('正在载入 %s：%s…（%d/%d，已用 %.1f 秒）'
                            % (os.path.basename(self.doc.path), name,
                               done + 1, self._st_total, time.time() - self._st_t0))
            self.update_idletasks()
            self.update()
            fn()
        except Exception as e:
            self.status.set('载入步骤「%s」出错：%s（已跳过，继续）' % (name, e))
        if steps:
            self.after(1, self._run_doc_stage)
        else:
            try:
                self.pbar.stop()
                self.pbar.config(mode='indeterminate')
            except Exception:
                pass
            self.status.set('已载入 %s：%d 条记录 · %s（总耗时 %.1f 秒）' % (
                os.path.basename(self.doc.path), len(self.doc.records),
                '未挂地图' if getattr(self, '_map_mode', '') == 'no_map'
                else (getattr(self.doc, 'map_status', None) or {}).get('text', ''),
                time.time() - self._st_t0))

    def _auto_scan_anim(self):
        """开档静默体检：数一遍**真正异常**的建筑（§170 十四档定稿），只更新状态栏。

        ★只报 `stuck / frozen / loop` 三种（地图上都表现为残缺或抽搐）：
            卡住 = +0x534=1 但 +0x6E4=0；不播 = +0x534=0 但 +0x10C=0；
            一直播 = +0x534=0、+0x10C≠0 但 +0xAC=5。
        `buildup`（动画没播完）是**合法中间态**，进游戏会自己播完整建造动画，
        报它等于告假警 —— 玩家已经实测确认这类档"观感最好"。
        """
        if not self.doc:
            return
        try:
            bl = self.doc.buildings()
        except Exception:
            return
        bad = []
        n_buildup = 0
        for b in bl:
            try:
                st = self.doc.building_anim_state(b['base'])
            except Exception:
                continue
            if st['state'] in ('stuck', 'frozen', 'loop'):
                bad.append((b['type'], b['x'], b['y']))
            elif st['state'] == 'buildup':
                n_buildup += 1
        try:
            self.mv_state['anim_bad'] = bad
            self.mv_state['anim_buildup'] = n_buildup
        except Exception:
            pass
        if not bad:
            return
        names = '、'.join('%s(%d,%d)' % t for t in bad[:3])
        if len(bad) > 3:
            names += '…'
        try:
            cur = self.status.get() if hasattr(self.status, 'get') else ''
            self.status.set('%s　⚠ %d 栋建造动画异常（%s）→ 地图页「⏩ 修成完成态」'
                            % (cur, len(bad), names))
        except Exception:
            pass

    # ---------------- 操作 ----------------
    def apply_money(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        s = self.ent_money.get().strip().replace(',', '')
        if not s.isdigit():
            messagebox.showwarning('提示', '请输入 0 ~ 16777215 的整数')
            return
        try:
            self.doc.set_money(int(s))
        except Exception as e:
            messagebox.showerror('出错', str(e))
            return
        self.lbl_money.config(text='%s → %s' % (self.doc.info.get('金钱'), s))
        self.status.set('金钱已改为 %s（尚未保存，点“另存为新文件”或“原地保存”）' % s)

    # ---------------- 所属方金钱 ----------------
    def refresh_houses(self):
        if not hasattr(self, 'th'):
            return
        self.th.delete(*self.th.get_children())
        if not self.doc:
            return
        try:
            hs = self.doc.houses()
        except Exception as e:
            self.status.set('所属方扫描失败: %s' % e)
            return
        for h in hs:
            cell = '—' if h['money'] is None else h['money']
            if h['is_player']:
                note = '← 当前玩家'
            elif h['verified']:
                note = ''
            else:
                note = '未定位（只读）'
            self.th.insert('', 'end', iid=h['short'],
                           values=(h['cn'] or '-', h['short'], cell, note))
        if not hs:
            self.th.insert('', 'end', iid='__none__',
                           values=('—', '未定位', '',
                                   '该存档没有 464 步长的所属方记录数组'))
            self.status.set('该存档未找到所属方记录数组（只能改当前玩家的金钱）')

    def _apply_house(self, short, v):
        try:
            self.doc.set_house_money(short, v)
        except Exception as e:
            messagebox.showerror('出错', str(e))
            return False
        if self.th.exists(short):
            vals = list(self.th.item(short, 'values'))
            vals[2] = v
            self.th.item(short, values=vals)
        if short == self.doc.player_house():
            self.lbl_money.config(text=str(v))
            self.lbl_money_field.config(text='字段: %s' % self.doc.info.get('金钱字段', ''))
        return True

    def edit_house(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        sel = self.th.selection()
        if not sel:
            messagebox.showwarning('提示', '请先选中一个所属方')
            return
        short = sel[0]
        if short == '__none__':
            return
        try:
            cur = int(self.th.item(short, 'values')[2])
        except Exception:
            cur = 0
        v = simpledialog.askinteger('修改所属方金钱', '%s 的金钱：' % short,
                                    initialvalue=cur, minvalue=0, maxvalue=4294967295,
                                    parent=self)
        if v is None:
            return
        if self._apply_house(short, v):
            self.status.set('%s 金钱已改为 %d（尚未保存）' % (short, v))

    def set_all_houses(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        hs = [h for h in self.doc.houses() if h.get('verified')]
        if not hs:
            messagebox.showwarning('提示', '该存档里没有定位到可写入的所属方')
            return
        v = simpledialog.askinteger('全部设为',
                                    '把全部 %d 个所属方的金钱设为：' % len(hs),
                                    initialvalue=10000, minvalue=0, maxvalue=4294967295,
                                    parent=self)
        if v is None:
            return
        n = 0
        for h in hs:
            if self._apply_house(h['short'], v):
                n += 1
        self.status.set('已把 %d 个所属方设为 %d（尚未保存）' % (n, v))

    def bump_house(self, sign):
        if not self.doc:
            return
        sel = self.th.selection()
        if not sel:
            messagebox.showwarning('提示', '请先选中所属方（可 Ctrl 多选）')
            return
        me = self.doc.player_house()
        sel = [s for s in sel if s != '__none__']
        if not sel:
            messagebox.showwarning('提示', '选中的行不是有效的所属方')
            return
        s = self.ent_delta.get().strip().replace(',', '')
        if not s.lstrip('-').isdigit():
            messagebox.showwarning('提示', '请输入整数增减量')
            return
        d = int(s) * (1 if sign > 0 else -1)
        n = 0
        for short in sel:
            try:
                cur = int(self.th.item(short, 'values')[2])
            except Exception:
                continue
            nv = max(0, min(4294967295, cur + d))
            if self._apply_house(short, nv):
                n += 1
        self.status.set('已调整 %d 个所属方（尚未保存）' % n)

    def toggle_selected(self, on=None):
        """启用 / 禁用选中触发（写「触发类 GUID 前 8 字节」那 1 个字节，见 core.toggle_trigger）。"""
        if not self.doc:
            return
        sel = self.tt.selection()
        if not sel:
            messagebox.showwarning('提示', '请先选中一条触发')
            return
        done = skipped = never = blocked = 0
        for iid in sel:
            r = self.doc.records[int(iid)]
            if self._is_executed(r):        # ★已执行 ⇒ 引擎不再评估，改了没用
                blocked += 1
                continue
            if not r.can_toggle:
                # 大任务档：运行期启用位未定位 ⇒ 「禁用」走大数法等效禁用（改计时不碰启用位）
                if on is False:
                    if not messagebox.askyesno(
                            '大数法等效禁用',
                            '「%s」的运行期启用位未定位（大任务档），无法直接写字节。\n\n'
                            '改用「等效禁用」：把它的计时改成 9999999 秒（≈115 天），\n'
                            '引擎两种启用模型下都不会再触发（实测验证）。\n'
                            '★只改计时不碰启用位。' % r.name, parent=self):
                        skipped += 1
                        continue
                    try:
                        info = self.doc.set_trigger_never_fire(r)
                    except Exception as e:
                        messagebox.showerror('出错', str(e))
                        continue
                    if info.get('changed'):
                        never += 1
                    else:
                        messagebox.showinfo('无需禁用', info.get('note', ''), parent=self)
                    continue
                skipped += 1
                continue
            target = (not bool(r.enabled)) if on is None else on
            try:
                self.doc.toggle_trigger(r, target)
            except Exception as e:
                messagebox.showerror('出错', str(e))
                continue
            vals = list(self.tt.item(iid, 'values'))
            vals[3] = '启用' if target else '禁用'
            vals[7] = r.note          # ★2026-10-20：列变多了（类型/标签数各占一列）⇒ 说明是第 8 列
            self.tt.item(iid, values=vals)
            done += 1
        if blocked:
            messagebox.showwarning(
                '有 %d 条触发可能已经触发过' % blocked,
                '已跳过 %d 条「事件驱动型·已挂钩」的触发 —— 它们可能已经触发过，\n'
                '改启用状态未必生效（列表里显示橙字）。\n\n'
                '若你确认它只是挂钩等待，可以在「触发编辑器」里改（会先弹确认）。' % blocked,
                parent=self)
        if done or never:
            self.status.set('已修改 %d 条触发状态（大数法 %d 条，跳过已执行 %d 条）（尚未保存）'
                            % (done, never, blocked))
            self.refresh_records()
        elif skipped:
            self.status.set('选中的记录不支持切换（启用位未定位 · 只读；「禁用」可选大数法等效禁用）')

    def do_search(self):
        if not self.doc:
            messagebox.showwarning('提示', '请先打开一个存档')
            return
        text = self.ent_search.get().strip()
        if not text:
            return
        hits = self.doc.search(text, limit=300)
        self.lst_hits.delete(0, 'end')
        for h in hits:
            self.lst_hits.insert('end', '%d  (0x%X)' % (h, h))
        self.status.set('命中 %d 处' % len(hits))
        if hits:
            self.lst_hits.selection_set(0)
            self.on_hit_select()

    def on_hit_select(self, evt=None):
        sel = self.lst_hits.curselection()
        if not sel or not self.doc:
            return
        off = int(self.lst_hits.get(sel[0]).split()[0])
        self.txt_hex.delete('1.0', 'end')
        self.txt_hex.insert('end', self.doc.hexdump(off, 384))

    # ---------------- 保存 ----------------
    def _finish_save(self, path):
        if getattr(self, '_saving', False):
            self.status.set('正在保存上一个文件，请稍候…')
            return
        # ★★文件名过长会让游戏「載入遊戲發生錯誤」（2026-10-15 玩家双盲实测：同一 md5 的档，
        #   长名(约77 GBK字节)必崩、短名(约36字节)正常；疑引擎 64 字节缓冲）⇒ 保存前拦截提醒
        try:
            stem_gbk = len(os.path.splitext(os.path.basename(path))[0].encode('gbk'))
        except Exception:
            stem_gbk = 0
        if stem_gbk > 50 and not getattr(self, '_allow_long_name', False):
            if not messagebox.askyesno(
                    '存档文件名过长',
                    '文件名主干约 %d 字节（GBK）。\n\n'
                    '★实测：同一存档，长文件名（约 77 字节）读档必报'
                    '「載入遊戲發生錯誤」，改短后正常（疑引擎 64 字节缓冲上限）。\n'
                    '建议把文件名改短到 25 个汉字以内。\n\n'
                    '仍要用这个名字保存吗？' % stem_gbk):
                return
            self._allow_long_name = True
        self._saving = True
        self._t_save = time.time()
        self.pbar.start(12)
        self.status.set('正在重建压缩块并写盘…')

        def work():
            try:
                res = self.doc.save(path)
                # ★2026-10-28（审查 P0-3）：把块布局核对结果**显式报给玩家**。
                #   以前这两项算了但没人看，块数不对也照样报"已保存"。
                self.q.put(('done', '已保存：%s\n重编码 %d 块 · 块布局核对 %d/%d 通过'
                            ' · 文件 %d B · md5 %s'
                            % (res['out'], res['recompressed'],
                               res.get('blocks', -1), res.get('blocks_expect', -1),
                               res['file'], res['md5'][:12])))
            except Exception as e:
                self.q.put(('err', '%s\n%s' % (e, traceback.format_exc())))
        threading.Thread(target=work, daemon=True).start()

    def _suggest_save_name(self, src_path):
        """给「另存为」拟一个**磁盘上还不存在**的默认文件名。

        ★2026-10-28（玩家反馈，截图 QQ20261028-123404）：
          以前一律 `base + '_修改'`。打开 `65_修改.SAV` 时建议名是 `65_修改_修改.SAV`，
          而那个文件是上一次另存为留下的 ⇒ Windows 立刻弹「要替换它吗」，
          玩家以为要**覆盖原档**（其实原档 `65_修改.SAV` 一点没动）。
        现在三条规矩：
          ① 名字已经以 `_修改` 结尾就**不再叠加**（避免 `_修改_修改_修改` 越滚越长）；
          ② 磁盘上已有同名文件就往后找 `_2` `_3` …，保证默认名一定是"新文件"；
          ③ 主干仍然截短到 40 GBK 字节以内（引擎读档 64 字节缓冲的老坑，见 _finish_save）。
        """
        base = os.path.splitext(os.path.basename(src_path))[0]
        d = os.path.dirname(os.path.abspath(src_path))

        def _free(stem):
            cand = stem + '.SAV'
            n = 2
            while os.path.exists(os.path.join(d, cand)):
                cand = '%s_%d.SAV' % (stem, n)
                n += 1
                if n > 999:            # 兜底，别死循环
                    cand = '%s_%s.SAV' % (stem, time.strftime('%H%M%S'))
                    break
            return cand

        # ★先把名字尾部的「_修改 / _2 / _3 …」这一串剥掉，否则会滚成
        #   `65_修改_2_修改_2_修改`（自检 §294 场景 E 逮到的）。
        #   只在名字里**本来就含 `_修改`** 时才剥 —— 免得把正常的 `SAVE_2.SAV` 剥成 `SAVE`。
        if '_修改' in base:
            while True:
                nb = re.sub(r'(_修改|_\d+)$', '', base)
                if nb == base or not nb:
                    break
                base = nb
        stem = base + '_修改'
        try:
            if len(stem.encode('gbk', 'ignore')) > 40:
                stem = base[:8] + '_修改'
        except Exception:
            pass
        return _free(stem)

    def save_as(self):
        if not self.doc:
            return
        p = filedialog.asksaveasfilename(
            title='另存为新文件（不会覆盖当前打开的存档）',
            defaultextension='.SAV',
            initialfile=self._suggest_save_name(self.doc.path),
            initialdir=os.path.dirname(self.doc.path))
        if p:
            self._finish_save(p)
