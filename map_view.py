# -*- coding: utf-8 -*-
"""地图俯视图（独立 CLI 版）—— 与编辑器「地图 / 选位」页共用 core.map_render 内核

用法:
    python map_view.py <图.map> <存档.SAV> [out.png] [--cell X,Y] [--scale N] [--types]

渲染：地形配色（minimap.ini）+ 高度差描边（悬崖/斜坡）+ 存档建筑足迹（artmd Foundation）
      + 网格/刻度；--cell 打印落位判定；--types 列出存档里的建筑类型。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'core'))

import map_render as mr                                  # noqa: E402
from editor_core import SaveDoc                           # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('map')
    ap.add_argument('save')
    ap.add_argument('out', nargs='?', default=None)
    ap.add_argument('--cell', default=None, help='校验格子 如 85,77')
    ap.add_argument('--scale', type=int, default=6)
    ap.add_argument('--types', action='store_true', help='列出存档建筑类型')
    ap.add_argument('--no-grid', action='store_true')
    a = ap.parse_args()

    d = SaveDoc(a.save)
    blds = d.buildings()
    mt = d.map_match(a.map)
    mp = mr.parse_map(a.map)
    mp['size'] = mr.canvas_size(mp, blds)
    print('地图 %s：声明 %s，剧院 %s，地形 %d 格，[Structures] %d 栋'
          % (os.path.basename(a.map), mp['size'], mp['theater'],
             len(mp['terrain']), len(mp['structures'])))
    print('存档 %s：建筑 %d 栋' % (os.path.basename(a.save), len(blds)))
    print('匹配判定：%s' % mt['msg'])
    cnt = {}
    for b in blds:
        cnt[b['type']] = cnt.get(b['type'], 0) + 1
    print('建筑构成：%s' % ', '.join('%s×%d' % kv for kv in sorted(cnt.items())))
    if a.types:
        print('可用类型（可克隆的模板）: %s' % ', '.join(sorted(cnt)))

    sel = None
    if a.cell:
        sel = tuple(int(v) for v in a.cell.split(','))
    W, H, buf, meta = mr.render_rgb(mp, blds, a.scale, sel=sel,
                                    grid=not a.no_grid)
    png = mr.to_png(W * a.scale, H * a.scale, buf)
    out = a.out or os.path.splitext(os.path.basename(a.map))[0] + '_view.png'
    open(out, 'wb').write(png)
    print('已渲染 %s（%d×%d px，%d px/格）' % (out, W * a.scale, H * a.scale, a.scale))
    if sel:
        print('\n== 落位判定 %s ==' % (sel,))
        for m in mr.check_cell(mp, blds, sel[0], sel[1]):
            print('  ', m)


if __name__ == '__main__':
    main()
